#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: $0 [container|docker]"
    echo "Build exact patched AGE 72707aa on PG18.6; run all original checks in a fresh cluster."
    echo "Exit 0: bounded checks passed; 1: qualification/build failed; 2: setup/input error."
    echo "PGAG_AGE_TEST_IMAGE reuses an existing Python test image; AGE is always built."
    echo "PGAG_AGE_PATCHED_IMAGE overrides the persistent image tag (image is never removed)."
    echo "PGAG_AGE_EVIDENCE_DIRECTORY optionally names an existing evidence directory."
    echo "Evidence is always retained; only this run's containers/test image are removed."
    echo "No production activation, model calls, native VLE workaround, or host database changes."
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
if [[ -n "${PGAG_AGE_EVIDENCE_DIRECTORY:-}" && ! -d "$PGAG_AGE_EVIDENCE_DIRECTORY" ]]; then
    echo "Evidence directory must already exist." >&2
    exit 2
fi
if [[ -n "${PGAG_AGE_EVIDENCE_DIRECTORY:-}" ]]; then
    PGAG_AGE_EVIDENCE_DIRECTORY="$(cd "$PGAG_AGE_EVIDENCE_DIRECTORY" && pwd -P)"
fi
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
run_id="pgag-age-patched-$(date +%s)-$$-${RANDOM}"
evidence_root="${PGAG_AGE_EVIDENCE_DIRECTORY:-$PWD/.review-artifacts}"
directory="$evidence_root/$run_id"
age_image="${PGAG_AGE_PATCHED_IMAGE:-pg-agmemory-age:patched-72707aa}"
test_image="${PGAG_AGE_TEST_IMAGE:-pg-agmemory-test:${run_id}}"
database="${run_id}-db"
network=default
network_created=false
test_built=false
database_started=false
containers=()
mkdir -p "$evidence_root"
mkdir "$directory"
password="age_patched_${RANDOM}_${RANDOM}_${RANDOM}_${RANDOM}"

cleanup() {
    status=$?
    cleanup_failed=false
    trap - EXIT INT TERM
    set +e
    if [[ "$database_started" == true ]]; then
        "$engine" logs "$database" > "$directory/postgres.log" 2>&1 ||
            cleanup_failed=true
    fi
    for name in "${containers[@]}"; do
        if [[ "$engine" == docker ]]; then
            docker rm --force --volumes "$name" >/dev/null 2>&1
        else
            container rm --force "$name" >/dev/null 2>&1
        fi
        if [[ $? -ne 0 ]]; then
            echo "Failed to remove owned patched AGE container: $name" >&2
            cleanup_failed=true
        fi
    done
    if [[ "$network_created" == true ]]; then
        docker network rm "$network" >/dev/null 2>&1 || cleanup_failed=true
    fi
    if [[ "$test_built" == true ]]; then
        "$engine" image rm "$test_image" >/dev/null 2>&1 || cleanup_failed=true
    fi
    echo "Patched AGE evidence retained: $directory" >&2
    echo "Patched AGE image retained: $age_image" >&2
    if [[ "$cleanup_failed" == true && $status -eq 0 ]]; then
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$engine" == docker ]]; then
    network="${run_id}-network"
    docker network create --internal "$network" >/dev/null
    network_created=true
fi
cp patches/age/source.json "$directory/source.json"
if ! "$engine" build --file Dockerfile.age-patched --tag "$age_image" . \
    > "$directory/build.log" 2>&1; then
    echo "Patched AGE build failed; see retained build.log." >&2
    exit 1
fi
"$engine" image inspect "$age_image" > "$directory/image.json"
if [[ -z "${PGAG_AGE_TEST_IMAGE:-}" ]]; then
    "$engine" build --target test --tag "$test_image" . >> "$directory/build.log" 2>&1
    test_built=true
fi
containers+=("${run_id}-contracts")
"$engine" run --name "${run_id}-contracts" --network "$network" \
    -v "$PWD:/work" -v "$PWD/src:/app/src" "$test_image" \
    sh -c 'ruff check /work/scripts/smoke-age.py /work/scripts/smoke-age-patched.py \
        /work/tests/test_age_profile.py /work/tests/test_age_patched_profile.py &&
        pytest -q -p no:cacheprovider /work/tests/test_age_profile.py \
        /work/tests/test_age_patched_profile.py' \
    > "$directory/tests.log" 2>&1
cat "$directory/tests.log" >&2

containers+=("$database")
"$engine" run -d --name "$database" --network "$network" \
    -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_age_qualification \
    "$age_image" >/dev/null
database_started=true
ready=false
for ((attempt = 0; attempt < 90; attempt++)); do
    if "$engine" exec "$database" pg_isready -h 127.0.0.1 -U postgres \
        -d pgag_age_qualification >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo "Patched AGE database failed readiness; see retained postgres.log." >&2
    exit 2
fi
"$engine" exec "$database" cat /usr/share/postgresql/18/extension/pgag_age_build.json \
    > "$directory/installed-source.json"
cmp patches/age/source.json "$directory/installed-source.json"
if [[ "$engine" == docker ]]; then
    host="$database"
else
    host="$(container inspect "$database" |
        jq -er '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address)
            | split("/")[0] | select(length > 0)')"
fi
containers+=("${run_id}-probe")
status=0
"$engine" run --name "${run_id}-probe" --network "$network" \
    -v "$PWD:/work" -v "$PWD/src:/app/src" \
    -e PGAG_AGE_DISPOSABLE=1 \
    -e "PGAG_AGE_ADMIN_DATABASE_URL=postgresql://postgres:${password}@${host}:5432/pgag_age_qualification" \
    "$test_image" python /work/scripts/smoke-age-patched.py \
    > "$directory/report.json" 2> "$directory/probe.log" || status=$?
cat "$directory/report.json"
exit "$status"
