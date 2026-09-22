#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: $0 [container|docker]"
    echo "Disposable schema19 operational-state restore drill; no API or worker starts."
    echo "PGAG_RECOVERY_TEST_IMAGE may select an existing test image; otherwise builds one."
    echo "Prints a JSON report; deletes all disposable databases, dumps, and metadata on exit."
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
identity_paths=(src/pg_agmemory ':!src/pg_agmemory/evaluation*'
                pyproject.toml uv.lock Dockerfile tests/conftest.py
                scripts/smoke-recovery.py scripts/test-recovery-containers.sh
                tests/test_recovery_drill.py)
git_sha="$(git rev-parse --verify HEAD)"
input_status="$(git status --porcelain=v1 --untracked-files=all -- "${identity_paths[@]}")"
file_hashes="$(shasum -a 256 scripts/smoke-recovery.py scripts/test-recovery-containers.sh \
    tests/test_recovery_drill.py | jq -Rn '
    [inputs | capture("^(?<sha256>[0-9a-f]{64})  (?<path>.+)$")]
    | map({key: .path, value: .sha256}) | from_entries')"
build_identity="$(jq -cn --arg git_sha "$git_sha" --arg status "$input_status" \
    --argjson files "$file_hashes" --args '
    ($status | split("\n") | map(select(length > 0))) as $changes |
    {
        git_sha: $git_sha, files_sha256: $files, status_scope: $ARGS.positional,
        git_status_porcelain: $changes,
        has_tracked_changes: any($changes[]; startswith("?? ") | not),
        has_untracked_changes: any($changes[]; startswith("?? ")),
        exact_commit_inputs: ($changes | length == 0)
    }' -- "${identity_paths[@]}")"
run_id="pgag-recovery-$(date +%s)-$$-${RANDOM}"
directory=".${run_id}"
postgres_image="docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
image="${PGAG_RECOVERY_TEST_IMAGE:-pg-agmemory-test:${run_id}}"
source_db="${run_id}-source"
restore_db="${run_id}-restored"
network=default
network_created=false
image_built=false
source_removed=false
password="$(openssl rand -hex 32)"
containers=()
mkdir "$directory"

cleanup() {
    status=$?
    cleanup_failed=false
    trap - EXIT INT TERM
    set +e
    for name in "${containers[@]}"; do
        if [[ "$name" == "$source_db" && "$source_removed" == true ]]; then
            continue
        fi
        if [[ "$engine" == docker ]]; then
            docker rm --force --volumes "$name" >/dev/null 2>&1
        else
            container rm --force "$name" >/dev/null 2>&1
        fi
        if [[ $? -ne 0 ]]; then
            echo "Failed to remove disposable container: $name" >&2
            cleanup_failed=true
        fi
    done
    if [[ "$network_created" == true ]]; then
        if ! docker network rm "$network" >/dev/null 2>&1; then
            echo "Failed to remove disposable recovery network." >&2
            cleanup_failed=true
        fi
    fi
    if [[ "$image_built" == true ]]; then
        if ! "$engine" image rm "$image" >/dev/null 2>&1; then
            echo "Failed to remove disposable recovery image." >&2
            cleanup_failed=true
        fi
    fi
    if ! rm -f -- "./$directory/old.dump" "./$directory/latest.dump" \
        "./$directory/before.json" "./$directory/latest.json" "./$directory/report.json"; then
        echo "Failed to remove known recovery artifacts." >&2
        cleanup_failed=true
    fi
    if ! rmdir -- "./$directory"; then
        echo "Recovery directory retained; unexpected files are never deleted." >&2
        cleanup_failed=true
    fi
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
if [[ -z "${PGAG_RECOVERY_TEST_IMAGE:-}" ]]; then
    "$engine" build --target test --tag "$image" . >&2
    image_built=true
fi
containers+=("${run_id}-contracts")
"$engine" run --name "${run_id}-contracts" --network "$network" \
    -v "$PWD:/work" -v "$PWD/src:/app/src" "$image" \
    pytest -q -p no:cacheprovider /work/tests/test_recovery_drill.py >&2

container_host() {
    if [[ "$engine" == docker ]]; then
        printf '%s\n' "$1"
    else
        container inspect "$1" |
            jq -er '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address)
                | split("/")[0] | select(length > 0)'
    fi
}

start_database() {
    containers+=("$1")
    "$engine" run -d --name "$1" --network "$network" \
        -v "$PWD/$directory:/drill" \
        -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_recovery \
        "$postgres_image" >/dev/null
    for ((attempt = 0; attempt < 90; attempt++)); do
        if "$engine" exec "$1" pg_isready -h 127.0.0.1 -U postgres -d pgag_recovery \
            >/dev/null 2>&1; then
            test "$("$engine" exec "$1" psql -U postgres -d pgag_recovery -Atc \
                'SHOW server_version_num')" = 180006
            return 0
        fi
        sleep 1
    done
    echo "Disposable PostgreSQL did not become ready." >&2
    return 1
}

phase() {
    containers+=("${run_id}-$1")
    "$engine" run --name "${run_id}-$1" --network "$network" \
        -v "$PWD:/work" -v "$PWD/src:/app/src" \
        -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:${password}@${2}:5432/pgag_recovery" \
        -e "PGAG_RECOVERY_DIRECTORY=/work/$directory" \
        -e "PGAG_RECOVERY_BUILD_IDENTITY=$build_identity" \
        "$image" python /work/scripts/smoke-recovery.py "$1"
}

start_database "$source_db"
source_host="$(container_host "$source_db")"
phase seed "$source_host"
"$engine" exec "$source_db" pg_dump -U postgres -d pgag_recovery \
    --format=custom --file=/drill/old.dump
phase later "$source_host"
# An independent latest PostgreSQL lineage and content-free replay export survive source removal.
"$engine" exec "$source_db" pg_dump -U postgres -d pgag_recovery \
    --format=custom --file=/drill/latest.dump
"$engine" stop "$source_db" >/dev/null
if [[ "$engine" == docker ]]; then
    docker rm --volumes "$source_db" >/dev/null
else
    container rm "$source_db" >/dev/null
fi
source_removed=true

start_database "$restore_db"
# Cluster roles are not in a per-database pg_dump; do not run migrations on the restore target.
"$engine" exec "$restore_db" psql -U postgres -d pgag_recovery -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE pgag_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;" >/dev/null
"$engine" exec "$restore_db" pg_restore -U postgres -d pgag_recovery \
    --exit-on-error --single-transaction /drill/old.dump
phase recover "$(container_host "$restore_db")"
