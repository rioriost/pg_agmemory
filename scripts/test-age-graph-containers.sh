#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo "Usage: $0 [container|docker]"
    echo "Opt-in fixed-hop AGE candidate conformance and bounded paired SQL cost experiment."
    echo "Uses Dockerfile.age unchanged; fresh independent clusters for conformance and costs."
    echo "PGAG_AGE_TEST_IMAGE optionally reuses a Python test image."
    echo "Evidence, including failures, remains in a private .review-artifacts run directory."
    echo "Does not run the native VLE probe or activate a production graph backend."
    exit 0
fi
engine="${1:-container}"
if [[ $# -gt 1 || ( "$engine" != container && "$engine" != docker ) ]]; then
    echo "Expected container or docker." >&2
    exit 2
fi
command -v "$engine" >/dev/null
command -v jq >/dev/null
if [[ "$engine" == container ]]; then
    container system status >/dev/null
else
    docker info >/dev/null
fi
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
run_id="pgag-age-graph-$(date +%s)-$$-${RANDOM}"
directory=".review-artifacts/$run_id"
age_image="pg-agmemory-age:$run_id"
test_image="${PGAG_AGE_TEST_IMAGE:-pg-agmemory-test:$run_id}"
network=default
network_created=false
age_built=false
test_built=false
containers=()
databases=()
mkdir -p .review-artifacts
mkdir "$directory"
password="disposable-${RANDOM}-${RANDOM}-${RANDOM}"

cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    cleanup_failed=false
    for name in "${databases[@]}"; do
        "$engine" logs "$name" > "$directory/$name.log" 2>&1
    done
    for name in "${containers[@]}"; do
        if [[ "$engine" == docker ]]; then
            docker rm --force --volumes "$name" >/dev/null 2>&1
        else
            container rm --force "$name" >/dev/null 2>&1
        fi
        if [[ $? -ne 0 ]]; then
            echo "Could not remove owned container: $name" >&2
            cleanup_failed=true
        fi
    done
    if [[ "$network_created" == true ]]; then
        docker network rm "$network" >/dev/null 2>&1 || cleanup_failed=true
    fi
    if [[ "$age_built" == true ]]; then
        "$engine" image rm "$age_image" >/dev/null 2>&1 || cleanup_failed=true
    fi
    if [[ "$test_built" == true ]]; then
        "$engine" image rm "$test_image" >/dev/null 2>&1 || cleanup_failed=true
    fi
    echo "Fixed-hop candidate evidence: $PWD/$directory" >&2
    if [[ "$cleanup_failed" == true && "$status" -eq 0 ]]; then
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$engine" == docker ]]; then
    network="$run_id-network"
    docker network create --internal "$network" >/dev/null
    network_created=true
fi
"$engine" build --file Dockerfile.age --tag "$age_image" . > "$directory/build.log" 2>&1
age_built=true
if [[ -z "${PGAG_AGE_TEST_IMAGE:-}" ]]; then
    "$engine" build --target test --tag "$test_image" . >> "$directory/build.log" 2>&1
    test_built=true
fi

containers+=("$run_id-lint")
"$engine" run --name "$run_id-lint" --network "$network" \
    -v "$PWD:/work" -v "$PWD/src:/app/src" -w /work \
    -e TMPDIR="/work/$directory" "$test_image" \
    sh -c 'ruff check --no-cache tests/test_age_graph_conformance.py &&
        ruff format --check tests/test_age_graph_conformance.py &&
        python -m pytest -q -p no:cacheprovider tests/test_age_graph_conformance.py' \
    > "$directory/lint-and-default-skip.log" 2>&1
cat "$directory/lint-and-default-skip.log"

for phase in conformance cost; do
    database="$run_id-$phase-db"
    runner="$run_id-$phase"
    containers+=("$database")
    "$engine" run -d --name "$database" --network "$network" --cpus 2 --memory 2g \
        -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_test \
        "$age_image" >/dev/null
    databases+=("$database")
    ready=false
    for ((attempt = 0; attempt < 90; attempt++)); do
        if "$engine" exec "$database" pg_isready -h 127.0.0.1 -U postgres \
            -d pgag_test >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 1
    done
    if [[ "$ready" != true ]]; then
        echo "Fresh AGE database failed readiness: $database" >&2
        exit 2
    fi
    test "$("$engine" exec "$database" psql -U postgres -d pgag_test -Atc \
        'SHOW server_version_num')" = 180006
    "$engine" exec "$database" psql -U postgres -d pgag_test -v ON_ERROR_STOP=1 \
        -c 'CREATE EXTENSION age' > "$directory/$phase-extension.log" 2>&1
    if [[ "$engine" == docker ]]; then
        host="$database"
    else
        host="$(container inspect "$database" |
            jq -er '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address)
                | split("/")[0] | select(length > 0)')"
    fi
    selector='not paired_cost'
    if [[ "$phase" == cost ]]; then
        selector=paired_cost
    fi
    containers+=("$runner")
    status=0
    "$engine" run --name "$runner" --network "$network" --cpus 2 --memory 2g \
        -v "$PWD:/work" -v "$PWD/src:/app/src" -w /work \
        -e TMPDIR="/work/$directory" \
        -e "PGAG_TEST_DATABASE_URL=postgresql://postgres:${password}@${host}:5432/pgag_test" \
        -e PGAG_TEST_AGE_GRAPH=1 -e PGAG_TEST_AGE_GRAPH_COST=1 \
        -e "PGAG_AGE_GRAPH_EVIDENCE=/work/$directory" \
        "$test_image" python -m pytest -q --tb=short -p no:cacheprovider \
        tests/test_age_graph_conformance.py -k "$selector" \
        > "$directory/$phase-tests.log" 2>&1 || status=$?
    cat "$directory/$phase-tests.log"
    if [[ "$status" -ne 0 ]]; then
        exit "$status"
    fi
    "$engine" stop "$database" >/dev/null
done
