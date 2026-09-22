#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo "Usage: $0 [container|docker]"
    echo "Build pinned patched AGE72707aa and run native adapter/publication/HTTP checks."
    echo "Uses two fresh owned clusters; never changes the default SQL deployment."
    echo "PGAG_AGE_PATCHED_IMAGE and PGAG_AGE_TEST_IMAGE may reuse explicit local images."
    echo "Private evidence remains in .review-artifacts; no live model calls."
    exit 0
fi
engine="${1:-container}"
if [[ $# -gt 1 || ( "$engine" != container && "$engine" != docker ) ]]; then
    echo "Expected container or docker." >&2
    exit 2
fi
command -v "$engine" >/dev/null
command -v jq >/dev/null
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
run_id="pgag-age-enabled-$(date +%s)-$$-$RANDOM"
directory=".review-artifacts/$run_id"
mkdir -p .review-artifacts
mkdir "$directory"
age_image="${PGAG_AGE_PATCHED_IMAGE:-pg-agmemory-age:$run_id}"
test_image="${PGAG_AGE_TEST_IMAGE:-pg-agmemory-test:$run_id}"
runtime_image="pg-agmemory-runtime:$run_id"
network=default
network_created=false
containers=()
databases=()
images=()
password="$(openssl rand -hex 24)"
runtime_password="$(openssl rand -hex 24)"

cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    failed=false
    for name in "${databases[@]}"; do
        "$engine" logs "$name" > "$directory/$name.log" 2>&1
    done
    for name in "${containers[@]}"; do
        if [[ "$engine" == docker ]]; then
            docker rm --force --volumes "$name" >/dev/null 2>&1 || failed=true
        else
            container rm --force "$name" >/dev/null 2>&1 || failed=true
        fi
    done
    if [[ "$network_created" == true ]]; then
        docker network rm "$network" >/dev/null 2>&1 || failed=true
    fi
    for image in "${images[@]}"; do
        "$engine" image rm "$image" >/dev/null 2>&1 || failed=true
    done
    echo "Patched AGE enabled-profile evidence: $PWD/$directory" >&2
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
    "$engine" build -f Dockerfile.age-patched -t "$age_image" . \
        > "$directory/age-build.log" 2>&1
    images+=("$age_image")
fi
if [[ -z "${PGAG_AGE_TEST_IMAGE:-}" ]]; then
    "$engine" build --target test -t "$test_image" . > "$directory/test-build.log" 2>&1
    images+=("$test_image")
fi
"$engine" build --target runtime -t "$runtime_image" . > "$directory/runtime-build.log" 2>&1
images+=("$runtime_image")
"$engine" image inspect "$age_image" > "$directory/age-image.json"
git rev-parse HEAD > "$directory/git-sha.txt"
git status --porcelain=v1 --untracked-files=all > "$directory/git-status.txt"

start_database() {
    local name="$1"
    containers+=("$name")
    "$engine" run -d --name "$name" --network "$network" --cpus 2 --memory 2g \
        -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_test "$age_image" >/dev/null
    databases+=("$name")
    local ready=false
    for ((attempt = 0; attempt < 90; attempt++)); do
        if "$engine" exec "$name" pg_isready -U postgres -d pgag_test >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 1
    done
    if [[ "$ready" != true ]]; then echo "AGE database not ready." >&2; return 1; fi
    "$engine" exec "$name" psql -U postgres -d pgag_test -v ON_ERROR_STOP=1 \
        -c 'CREATE EXTENSION age' > "$directory/$name-extension.log" 2>&1
}

database_host() {
    if [[ "$engine" == docker ]]; then
        printf '%s\n' "$1"
    else
        container inspect "$1" | jq -er \
            '.[0].status.networks[0].ipv4Address | split("/")[0] | select(length>0)'
    fi
}

start_database "$run_id-tests-db"
host="$(database_host "$run_id-tests-db")"
containers+=("$run_id-tests")
test_status=0
"$engine" run --name "$run_id-tests" --network "$network" --cpus 2 --memory 2g \
    -e "PGAG_TEST_DATABASE_URL=postgresql://postgres:$password@$host:5432/pgag_test" \
    -e PGAG_TEST_AGE_NATIVE=1 "$test_image" sh -c \
    'ruff check . && mypy && pytest -q --tb=short -p no:cacheprovider \
        tests/test_age_patched_profile.py tests/test_age_native_graph.py \
        tests/test_age_projection.py tests/test_age_projection_schema.py tests/test_age_api.py \
        tests/test_age_projection_recovery.py' \
    > "$directory/native-tests.log" 2>&1 || test_status=$?
cat "$directory/native-tests.log"
if [[ "$test_status" -ne 0 ]]; then exit "$test_status"; fi
"$engine" stop "$run_id-tests-db" >/dev/null

start_database "$run_id-http-db"
host="$(database_host "$run_id-http-db")"
containers+=("$run_id-migrate")
"$engine" run --name "$run_id-migrate" --network "$network" \
    -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:$password@$host:5432/pgag_test" \
    "$runtime_image" pg-agmemory migrate
"$engine" exec "$run_id-http-db" psql -U postgres -d pgag_test -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE pgag_age_http LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '$runtime_password' IN ROLE pgag_runtime;" \
    > "$directory/runtime-role.log" 2>&1
containers+=("$run_id-http")
smoke_status=0
"$engine" run --name "$run_id-http" --network "$network" --cpus 2 --memory 2g -w /tmp \
    -v "$PWD/scripts/smoke-graph-artifact.py:/smokes/smoke-graph-artifact.py:ro" \
    -v "$PWD/scripts/smoke-age-enabled.py:/smokes/smoke-age-enabled.py:ro" \
    -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:$password@$host:5432/pgag_test" \
    -e "PGAG_DATABASE_URL=postgresql://pgag_age_http:$runtime_password@$host:5432/pgag_test" \
    "$runtime_image" python /smokes/smoke-age-enabled.py /smokes \
    > "$directory/http-smoke.log" 2>&1 || smoke_status=$?
cat "$directory/http-smoke.log"
exit "$smoke_status"
