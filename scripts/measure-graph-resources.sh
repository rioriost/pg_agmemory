#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo "Usage: $0 NEW_PRIVATE_PROJECT_DIRECTORY [--development|--preflight] [container|docker]"
    echo "Frozen six-case native AGE graph-only resource profile; no model calls."
    echo "Default requires clean committed inputs; development and preflight never qualify."
    echo "PGAG_AGE_PATCHED_IMAGE may select an existing pinned patched AGE image."
    exit 0
fi
if [[ $# -lt 1 || $# -gt 3 ]]; then echo "Use --help for usage." >&2; exit 2; fi
output="$1"
mode="${2:-}"
engine="${3:-container}"
if [[ "$mode" != "" && "$mode" != --development && "$mode" != --preflight ]]; then
    echo "Invalid mode." >&2; exit 2
fi
if [[ "$engine" != container && "$engine" != docker ]]; then
    echo "Invalid engine." >&2; exit 2
fi
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$output" in /*|..|../*|*/../*|*/..) echo "Output must be project-relative." >&2; exit 2;; esac
command -v "$engine" >/dev/null
command -v jq >/dev/null
command -v python3 >/dev/null
umask 077
mkdir -m 700 "$output"
directory="$(cd "$output" && pwd -P)"
case "$directory/" in "$PWD/"*) ;; *) echo "Output escaped repository." >&2; exit 2;; esac
source_root="$PWD"
exact=false
if [[ "$mode" == "" ]]; then
    if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
        echo "Qualification requires clean committed inputs; use --development." >&2
        exit 1
    fi
    mkdir "$directory/source"
    git archive HEAD | tar -x -C "$directory/source"
    source_root="$directory/source"
    exact=true
fi
cp "$source_root/examples/graph-resource-profile.json" "$directory/requested-profile.json"
git status --porcelain=v1 --untracked-files=all > "$directory/git-status.txt"
python3 - "$directory" "$source_root" "$exact" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

out, root = map(pathlib.Path, sys.argv[1:3])
tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
untracked = subprocess.check_output(
    ["git", "ls-files", "--others", "--exclude-standard", "-z"]
).decode().split("\0")
names = {
    "scripts/graph-resource-benchmark.py", "scripts/measure-graph-resources.sh",
    "examples/graph-resource-profile.json", "Dockerfile", "Dockerfile.age-patched",
    "pyproject.toml", "uv.lock",
}
files = sorted(p for p in set(tracked + untracked) if p and (
    p in names or p.startswith(("src/", "patches/age/"))
))
identity = {
    "implementation_sha": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
    "exact_commit_inputs": sys.argv[3] == "true",
    "source_inputs": [
        {"path": p, "tracked": p in tracked,
         "sha256": hashlib.sha256((root / p).read_bytes()).hexdigest()} for p in files
    ],
}
(out / "build-identity.json").write_text(json.dumps(identity, sort_keys=True, indent=2) + "\n")
(out / "result.json").write_text(json.dumps({
    "format": "pgag-graph-resource-result-v1", "resource_qualified": False,
    "m3_qualified": False, "exact_commit_inputs": identity["exact_commit_inputs"],
    "cases": [], "failures": [{"phase": "launcher", "code": "run_not_completed"}],
}) + "\n")
PY

run_id="pgag-graph-resource-$(date +%s)-$$-$RANDOM"
db="$run_id-db"
app="$run_id-app"
age_image="${PGAG_AGE_PATCHED_IMAGE:-pg-agmemory-age:$run_id}"
runtime_image="pg-agmemory-runtime:$run_id"
network=default
network_created=false
db_created=false
app_created=false
images=()
password="$(openssl rand -hex 24)"
runtime_password="$(openssl rand -hex 24)"
cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    failed=false
    if [[ "$app_created" == true ]]; then
        if ! "$engine" cp "$app:/app/benchmark/result.json" "$directory/result.json" \
            >/dev/null 2>&1; then
            echo "Failed to retrieve final benchmark report; retained launcher evidence." >&2
            failed=true
        fi
        "$engine" rm --force "$app" >/dev/null 2>&1 || failed=true
    fi
    if [[ "$db_created" == true ]]; then
        if [[ "$engine" == docker ]]; then
            "$engine" rm --force --volumes "$db" >/dev/null 2>&1 || failed=true
        else
            "$engine" rm --force "$db" >/dev/null 2>&1 || failed=true
        fi
    fi
    if [[ "$network_created" == true ]]; then
        docker network rm "$network" >/dev/null 2>&1 || failed=true
    fi
    for image in "${images[@]}"; do
        "$engine" image rm "$image" >/dev/null 2>&1 || failed=true
    done
    chmod 600 "$directory/result.json"
    printf '{"owned_container_cleanup_succeeded":%s,"exit_status":%s}\n' \
        "$([[ "$failed" == false ]] && echo true || echo false)" "$status" \
        > "$directory/cleanup.json"
    echo "Private graph-only evidence: $directory" >&2
    if [[ "$failed" == true && "$status" -eq 0 ]]; then status=1; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$engine" == docker ]]; then
    network="$run_id-network"
    docker network create --internal "$network" >/dev/null
    network_created=true
else
    container system status >/dev/null
fi
if [[ -z "${PGAG_AGE_PATCHED_IMAGE:-}" ]]; then
    "$engine" build -f "$source_root/Dockerfile.age-patched" -t "$age_image" "$source_root" \
        > "$directory/age-build.log" 2>&1
    images+=("$age_image")
fi
"$engine" build --target runtime -t "$runtime_image" "$source_root" \
    > "$directory/runtime-build.log" 2>&1
images+=("$runtime_image")
for pair in "age:$age_image" "runtime:$runtime_image"; do
    name="${pair%%:*}"
    image="${pair#*:}"
    "$engine" image inspect "$image" | jq '[.[] | {
        id:(.id // .Id),digest:(.configuration.descriptor.digest // .RepoDigests),
        reference:(.configuration.name // .RepoTags),
        platform:(.variants[0].config.architecture // .Architecture)
    }]' > "$directory/$name-image.json"
done
python3 - "$directory" <<'PY'
import json
import pathlib
import sys
out = pathlib.Path(sys.argv[1])
path = out / "build-identity.json"
identity = json.loads(path.read_text())
identity["images"] = {key: json.loads((out / (key + "-image.json")).read_text())
                      for key in ("age", "runtime")}
path.write_text(json.dumps(identity, sort_keys=True, indent=2) + "\n")
PY
db_created=true
"$engine" run -d --name "$db" --network "$network" --cpus 6 --memory 24g \
    -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_graph_resource "$age_image" \
    postgres -c shared_preload_libraries=age -c log_min_error_statement=panic \
    -c log_error_verbosity=terse >/dev/null
ready=false
for ((attempt=0; attempt<90; attempt++)); do
    if "$engine" exec "$db" pg_isready -U postgres -d pgag_graph_resource >/dev/null 2>&1; then
        ready=true; break
    fi
    sleep 1
done
[[ "$ready" == true ]] || { echo "Database readiness failed." >&2; exit 1; }
"$engine" exec "$db" psql -U postgres -d pgag_graph_resource -v ON_ERROR_STOP=1 \
    -c 'CREATE EXTENSION age' > "$directory/extension.log" 2>&1
if [[ "$engine" == docker ]]; then
    host="$db"
else
    host="$(container inspect "$db" | jq -er \
        '.[0].status.networks[0].ipv4Address | split("/")[0] | select(length>0)')"
fi
app_created=true
"$engine" run -d --name "$app" --network "$network" --cpus 2 --memory 8g \
    -v "$source_root/scripts/graph-resource-benchmark.py:/app/graph-resource-benchmark.py:ro" \
    -v "$directory/requested-profile.json:/app/requested-profile.json:ro" \
    -v "$directory/build-identity.json:/app/build-identity.json:ro" \
    "$runtime_image" sleep 14400 >/dev/null
# Root only creates an owned work directory; all service/measurement code runs as image UID 10001.
"$engine" exec --user 0 -w /app "$app" sh -c \
    'mkdir -m 700 benchmark && chown 10001:10001 benchmark'
"$engine" exec -w /app "$app" sh -c 'cp build-identity.json benchmark/build-identity.json'
"$engine" inspect "$db" "$app" | jq '[.[] | {
    id:(.configuration.id // .Id),
    resources:(.configuration.resources // {
        NanoCpus:.HostConfig.NanoCpus,Memory:.HostConfig.Memory}),
    image:(.configuration.image.reference // .Image),
    platform:(.configuration.platform // .Platform)
}]' > "$directory/container-configuration.json"
arguments=()
if [[ "$mode" != "" ]]; then arguments+=("$mode"); fi
status=0
"$engine" exec -w /app/benchmark \
    -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:$password@$host:5432/pgag_graph_resource" \
    -e "PGAG_BENCHMARK_RUNTIME_PASSWORD=$runtime_password" "$app" \
    python /app/graph-resource-benchmark.py --directory . \
    --profile /app/requested-profile.json "${arguments[@]}" \
    > "$directory/benchmark.log" 2>&1 || status=$?
cat "$directory/benchmark.log"
"$engine" cp "$app:/app/benchmark/result.json" "$directory/result.json" >/dev/null
python3 - "$directory/build-identity.json" "$source_root" <<'PY'
import hashlib
import json
import pathlib
import sys
identity = json.loads(pathlib.Path(sys.argv[1]).read_text())
root = pathlib.Path(sys.argv[2])
if not all(hashlib.sha256((root / row["path"]).read_bytes()).hexdigest() == row["sha256"]
           for row in identity["source_inputs"]):
    raise SystemExit("Source inputs changed during measurement.")
PY
exit "$status"
