#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $# -lt 2 || $# -gt 3 || ( "${3:-}" != "" && "${3:-}" != "--development" ) ]]; then
    echo "Usage: $0 FULL_S_DUMP NEW_PRIVATE_OUTPUT_DIRECTORY [--development]" >&2
    exit 2
fi
dump="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
test -f "$dump"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
mkdir -m 700 "$2"
directory="$(cd "$2" && pwd)"
source_root="$PWD"
sha="$(git rev-parse HEAD)"
exact=false
if [[ "${3:-}" != "--development" ]]; then
    test -z "$(git status --porcelain --untracked-files=all)"
    mkdir "$directory/source"
    git archive HEAD | tar -x -C "$directory/source"
    source_root="$directory/source"
    exact=true
fi
printf '{"implementation_sha":"%s","exact_commit_inputs":%s}\n' "$sha" "$exact" \
    >"$directory/build-identity.json"
shasum -a 256 "$dump" >"$directory/input-dump.sha256"
(cd "$source_root" && shasum -a 256 scripts/resource-probes.py scripts/resource-benchmark.py \
    scripts/measure-resource-probes.sh examples/resource-probes-plan.json) \
    >"$directory/source-inputs.sha256"
run_id="pgag-probe-$(date +%s)-$$"
db="${run_id}-db"
app="${run_id}-app"
client="${run_id}-client"
image="${PGAG_RESOURCE_TEST_IMAGE:-pg-agmemory-resource:${run_id}}"
built=false
cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    container logs "$app" >"$directory/last-application.log" 2>&1
    container logs "$db" >"$directory/database.log" 2>&1
    for name in "$client" "$app" "$db"; do container rm --force "$name" >/dev/null 2>&1; done
    if [[ "$built" == true ]]; then container image rm "$image" >/dev/null 2>&1; fi
    rm -f "$directory/runtime.json" "$directory/runtime-rebind.json" "$directory/clients.json"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -z "${PGAG_RESOURCE_TEST_IMAGE:-}" ]]; then
    container build --target test -t "$image" "$source_root"
    built=true
fi
password="$(openssl rand -hex 32)"
container run -d --name "$db" --cpus 6 --memory 24g \
    -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_resource_probe \
    -v "$(dirname "$dump"):/input:ro" \
    docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a \
    postgres -c shared_buffers=6GB -c work_mem=64MB -c maintenance_work_mem=1GB \
    -c max_connections=96 -c max_parallel_workers_per_gather=0 >/dev/null
ready_db() {
    for ((attempt=0; attempt<90; attempt++)); do
        if container exec "$db" pg_isready -U postgres -d pgag_resource_probe >/dev/null 2>&1; then return; fi
        sleep 1
    done
    echo "Probe database did not become ready." >&2
    exit 1
}
host_for() {
    container inspect "$1" | jq -er \
        '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address) | split("/")[0]'
}
ready_db
db_host="$(host_for "$db")"
admin_url="postgresql://postgres:${password}@${db_host}:5432/pgag_resource_probe"
container exec "$db" psql -U postgres -d pgag_resource_probe -v ON_ERROR_STOP=1 \
    -c 'CREATE ROLE pgag_runtime NOLOGIN' >/dev/null
container exec "$db" pg_restore -U postgres -d pgag_resource_probe \
    --exit-on-error "/input/$(basename "$dump")"
container run -d --name "$client" --cpus 2 --memory 1g \
    -v "$source_root:/work:ro" -v "$source_root/src:/app/src:ro" -v "$directory:/artifacts" \
    -w /work "$image" sleep 14400 >/dev/null
probe() {
    container exec -e "PGAG_ADMIN_DATABASE_URL=$admin_url" "$client" \
        python scripts/resource-probes.py "$@" --directory /artifacts
}
probe prepare --plan /work/examples/resource-probes-plan.json
probe analyze --phase restore
start_app() {
    container run -d --name "$app" --cpus 2 --memory 8g \
        -v "$source_root:/work:ro" -v "$source_root/src:/app/src:ro" -v "$directory:/artifacts" \
        -w /work "$image" python "$1" "$2" --directory /artifacts >/dev/null
    app_host="$(host_for "$app")"
    for ((attempt=0; attempt<90; attempt++)); do
        if container exec "$client" python -c \
            "import httpx; r=httpx.get('http://${app_host}:8000/readyz',trust_env=False); r.raise_for_status()" \
            >/dev/null 2>&1; then return; fi
        sleep 1
    done
    echo "Probe API did not become ready." >&2
    exit 1
}
start_app scripts/resource-benchmark.py serve
probe limits --base-url "http://${app_host}:8000"
probe small --base-url "http://${app_host}:8000"
probe large --base-url "http://${app_host}:8000"
container logs "$app" >"$directory/probe-application.log" 2>&1
container stop "$app" >/dev/null
container rm "$app" >/dev/null
probe analyze --phase cold
for ((index=0; index<12; index++)); do
    container stop "$db" >/dev/null
    container start "$db" >/dev/null
    ready_db
    db_host="$(host_for "$db")"
    admin_url="postgresql://postgres:${password}@${db_host}:5432/pgag_resource_probe"
    probe rebind
    start_app scripts/resource-probes.py cold-server
    probe cold --base-url "http://${app_host}:8000" --index "$index"
    container logs "$app" >"$directory/cold-application-${index}.log" 2>&1
    container stop "$app" >/dev/null
    container rm "$app" >/dev/null
done
(cd "$source_root" && shasum -a 256 -c "$directory/source-inputs.sha256")
probe report
echo "Resource probe evidence retained in $directory."
