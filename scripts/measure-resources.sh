#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $# -lt 1 || $# -gt 2 || ( "${2:-}" != "" && "${2:-}" != "--development" \
    && "${2:-}" != "--preflight" ) ]]; then
    echo "Usage: $0 NEW_PRIVATE_OUTPUT_DIRECTORY [--development|--preflight]" >&2
    exit 2
fi
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v container >/dev/null
command -v jq >/dev/null
container system status >/dev/null
umask 077
mkdir -m 700 "$1"
directory="$(cd "$1" && pwd)"
source_root="$PWD"
sha="$(git rev-parse HEAD)"
exact=false
if [[ "${2:-}" != "--development" ]]; then
    if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
        echo "S measurement requires a clean committed tree." >&2
        exit 1
    fi
    mkdir "$directory/source"
    git archive HEAD | tar -x -C "$directory/source"
    source_root="$directory/source"
    exact=true
fi
printf '{"implementation_sha":"%s","exact_commit_inputs":%s}\n' "$sha" "$exact" \
    >"$directory/build-identity.json"
(cd "$source_root" && shasum -a 256 src/pg_agmemory/api.py src/pg_agmemory/http_metrics.py \
    scripts/resource-benchmark.py scripts/measure-resources.sh examples/resource-profile-s.json) \
    >"$directory/source-inputs.sha256"
run_id="pgag-res-$(date +%s)-$$"
db="${run_id}-db"
app="${run_id}-app"
client="${run_id}-client"
image="${PGAG_RESOURCE_TEST_IMAGE:-pg-agmemory-resource:${run_id}}"
built=false
cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    container logs "$app" >"$directory/application.log" 2>&1
    container logs "$db" >"$directory/database.log" 2>&1
    for name in "$client" "$app" "$db"; do
        container rm --force "$name" >/dev/null 2>&1
    done
    if [[ "$built" == true ]]; then container image rm "$image" >/dev/null 2>&1; fi
    # Only private credential files produced by this run; retain measurements.
    rm -f "$directory/runtime.json" "$directory/clients.json"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -z "${PGAG_RESOURCE_TEST_IMAGE:-}" ]]; then
    container build --target test -t "$image" "$source_root"
    built=true
fi
git status --porcelain --untracked-files=all >"$directory/working-tree.txt"
git diff --binary >"$directory/working-tree.patch"
printf '%s\n' "$sha" >"$directory/implementation-sha.txt"
cp examples/resource-profile-s.json "$directory/requested-profile.json"
database_memory=24g
application_memory=8g
shared_buffers=6GB
mode=()
if [[ "${2:-}" == "--development" ]]; then
    database_memory=4g
    application_memory=2g
    shared_buffers=1GB
    mode=(--development)
elif [[ "${2:-}" == "--preflight" ]]; then
    mode=(--preflight)
fi
password="$(openssl rand -hex 32)"
container run -d --name "$db" --cpus 6 --memory "$database_memory" \
    -v "$directory:/artifacts" -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_resource \
    docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a \
    postgres -c "shared_buffers=$shared_buffers" -c work_mem=64MB -c maintenance_work_mem=1GB \
    -c max_connections=96 -c max_parallel_workers_per_gather=0 >/dev/null
for ((attempt=0; attempt<90; attempt++)); do
    if container exec "$db" pg_isready -U postgres -d pgag_resource >/dev/null 2>&1; then break; fi
    sleep 1
done
container exec "$db" pg_isready -U postgres -d pgag_resource
db_host="$(container inspect "$db" | jq -er \
    '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address) | split("/")[0]')"
admin_url="postgresql://postgres:${password}@${db_host}:5432/pgag_resource"
container run -d --name "$client" --cpus 2 --memory 1g \
    -v "$source_root:/work:ro" -v "$source_root/src:/app/src:ro" -v "$directory:/artifacts" \
    -w /work "$image" sleep 14400 >/dev/null
container exec -e "PGAG_ADMIN_DATABASE_URL=$admin_url" "$client" \
    python scripts/resource-benchmark.py seed --directory /artifacts \
    --profile /work/examples/resource-profile-s.json "${mode[@]}"
container exec -e "PGAG_ADMIN_DATABASE_URL=$admin_url" "$client" \
    python scripts/resource-benchmark.py footprint --directory /artifacts --name before
container run -d --name "$app" --cpus 2 --memory "$application_memory" \
    -v "$source_root:/work:ro" -v "$source_root/src:/app/src:ro" -v "$directory:/artifacts" \
    -w /work "$image" python scripts/resource-benchmark.py serve --directory /artifacts >/dev/null
app_host="$(container inspect "$app" | jq -er \
    '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address) | split("/")[0]')"
for ((attempt=0; attempt<90; attempt++)); do
    if container exec "$client" python -c \
        "import httpx; r=httpx.get('http://${app_host}:8000/readyz',trust_env=False); r.raise_for_status()" \
        >/dev/null 2>&1; then break; fi
    sleep 1
done
container exec -e "PGAG_ADMIN_DATABASE_URL=$admin_url" "$client" \
    python scripts/resource-benchmark.py load \
    --directory /artifacts --base-url "http://${app_host}:8000"
for ((attempt=0; attempt<60; attempt++)); do
    pending="$(container exec "$db" psql -U postgres -d pgag_resource -Atc \
        "SELECT count(*) FROM memory_ops.job WHERE state IN ('pending','running')")"
    [[ "$pending" == 0 ]] && break
    sleep 1
done
container exec -e "PGAG_ADMIN_DATABASE_URL=$admin_url" "$client" \
    python scripts/resource-benchmark.py footprint --directory /artifacts --name after
container exec "$db" pg_dump -U postgres -d pgag_resource -Fc -f /artifacts/final.dump
container exec "$db" chmod 600 /artifacts/final.dump
container inspect "$db" "$app" "$client" | jq '[.[] | {
    id:.configuration.id,resources:.configuration.resources,
    image:.configuration.image.reference,platform:.configuration.platform,
    networks:(.status.networks // .networks)
}]' >"$directory/container-configuration.json"
container stop "$app" >/dev/null
(cd "$source_root" && shasum -a 256 -c "$directory/source-inputs.sha256")
container exec "$client" python scripts/resource-benchmark.py report --directory /artifacts --sha "$sha"
echo "Resource measurement retained in $directory (not a complete M2 qualification)."
