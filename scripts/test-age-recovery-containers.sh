#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo "Usage: $0 [container|docker]"
    echo "Explicit canonical-only projection-discard pg_dump/restore and AGE rebuild."
    echo "Excludes AGE extension/catalog/owned physical schemas; not full AGE catalog restore."
    echo "Builds a packaged non-root runtime; mounts only the smoke and private evidence."
    echo "PGAG_AGE_PATCHED_IMAGE may select an existing trusted patched-72707aa image."
    echo "PGAG_AGE_RUNTIME_IMAGE may select a matching packaged non-root runtime."
    echo "Deletes owned source before restore; removes credentials, dumps and fixture payloads."
    echo "Keeps only content-free reports/build evidence in .review-artifacts."
    exit 0
fi
engine="${1:-container}"
if [[ $# -gt 1 || ( "$engine" != container && "$engine" != docker ) ]]; then
    echo "Expected container or docker." >&2
    exit 2
fi
command -v "$engine" >/dev/null
command -v jq >/dev/null
command -v openssl >/dev/null
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
run_id="pgag-age-recovery-$(date +%s)-$$-$RANDOM"
directory=".review-artifacts/$run_id"
mkdir -p .review-artifacts
mkdir "$directory" "$directory/private"
age_image="${PGAG_AGE_PATCHED_IMAGE:-pg-agmemory-age:$run_id}"
runtime_image="${PGAG_AGE_RUNTIME_IMAGE:-pg-agmemory-runtime:$run_id}"
source_db="$run_id-source"
restore_db="$run_id-restored"
source_removed=false
network=default
network_created=false
containers=()
images=()
password="$(openssl rand -hex 24)"
runtime_password="$(openssl rand -hex 24)"
host_uid="$(id -u)"
host_gid="$(id -g)"
if [[ "$host_uid" == 0 ]]; then
    echo "Run this non-root drill as an unprivileged host user." >&2
    exit 1
fi

cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    failed=false
    for name in "${containers[@]}"; do
        if [[ "$name" == "$source_db" && "$source_removed" == true ]]; then continue; fi
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
    rm -f -- "$directory/private/old.dump" "$directory/private/fixture.json" \
        "$directory/private/before.json" "$directory/private/latest.json" \
        "$directory/private/bundle.json" "$directory/private/expected.json" \
        "$directory/private/old-graph.json" "$directory/private/rebuilt-graph.json" \
        "$directory/private/api-private.log" "$directory/private/report.json" \
        "$directory/private/runtime.env" "$directory/private/database.env" \
        "$directory/private/roles.sql" "$directory/private/archive.list" \
        "$directory/private/backup.json" || failed=true
    rmdir -- "$directory/private" || failed=true
    if [[ "$failed" == true ]]; then
        echo "Owned cleanup incomplete; unexpected files are never recursively removed." >&2
        if [[ "$status" == 0 ]]; then status=1; fi
    fi
    echo "Bounded AGE recovery evidence: $PWD/$directory" >&2
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$engine" == docker ]]; then
    docker info >/dev/null
    network="$run_id-network"
    docker network create --internal "$network" >/dev/null
    network_created=true
else
    container system status >/dev/null
fi
if [[ -z "${PGAG_AGE_PATCHED_IMAGE:-}" ]]; then
    images+=("$age_image")
    "$engine" build -f Dockerfile.age-patched -t "$age_image" . \
        > "$directory/age-build.log" 2>&1
fi
if [[ -z "${PGAG_AGE_RUNTIME_IMAGE:-}" ]]; then
    images+=("$runtime_image")
    "$engine" build --target runtime -t "$runtime_image" . > "$directory/runtime-build.log" 2>&1
fi
"$engine" image inspect "$age_image" > "$directory/age-image.json"
"$engine" image inspect "$runtime_image" > "$directory/runtime-image.json"
age_image_id="$(jq -er '.[0].Id // .[0].id' "$directory/age-image.json")"
runtime_image_id="$(jq -er '.[0].Id // .[0].id' "$directory/runtime-image.json")"
identity_paths=(src/pg_agmemory pyproject.toml uv.lock Dockerfile Dockerfile.age-patched \
                patches/age scripts/smoke-age-recovery.py scripts/test-age-recovery-containers.sh \
                tests/test_age_recovery_drill.py)
git_sha="$(git rev-parse HEAD)"
input_status="$(git status --porcelain=v1 --untracked-files=all -- "${identity_paths[@]}")"
hashes="$(shasum -a 256 scripts/smoke-age-recovery.py scripts/test-age-recovery-containers.sh \
    tests/test_age_recovery_drill.py |
    jq -Rn '[inputs | capture("^(?<sha256>[0-9a-f]{64})  (?<path>.+)$")]
        | map({key: .path, value: .sha256}) | from_entries')"
build_identity="$(jq -cn --arg git_sha "$git_sha" --arg status "$input_status" \
    --argjson files "$hashes" --arg age_image "$age_image" --arg runtime_image "$runtime_image" \
    --arg age_image_id "$age_image_id" --arg runtime_image_id "$runtime_image_id" '
    ($status | split("\n") | map(select(length > 0))) as $changes |
    {git_sha: $git_sha, files_sha256: $files, git_status_porcelain: $changes,
     exact_commit_inputs: ($changes | length == 0), age_image: $age_image,
     runtime_image: $runtime_image, age_image_id: $age_image_id,
     runtime_image_id: $runtime_image_id}')"
printf '%s\n' "$build_identity" > "$directory/build-identity.json"
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_DB=pgag_age_recovery\n' "$password" \
    > "$directory/private/database.env"
printf "CREATE ROLE pgag_restore LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '%s' IN ROLE pgag_runtime;\n" \
    "$runtime_password" > "$directory/private/roles.sql"

database_host() {
    if [[ "$engine" == docker ]]; then
        printf '%s\n' "$1"
    else
        container inspect "$1" | jq -er \
            '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address)
             | split("/")[0] | select(length>0)'
    fi
}

start_database() {
    local name="$1"
    test "$("$engine" image inspect "$age_image" | jq -er '.[0].Id // .[0].id')" = "$age_image_id"
    containers+=("$name")
    "$engine" run -d --name "$name" --network "$network" --cpus 2 --memory 2g \
        --env-file "$directory/private/database.env" \
        -v "$PWD/$directory/private:/drill" "$age_image" >/dev/null
    for ((attempt = 0; attempt < 90; attempt++)); do
        if "$engine" exec "$name" pg_isready -h 127.0.0.1 -U postgres -d pgag_age_recovery \
            >/dev/null 2>&1; then
            test "$("$engine" exec "$name" psql -U postgres -d pgag_age_recovery -Atc \
                'SHOW server_version_num')" = 180006
            return 0
        fi
        sleep 1
    done
    echo "Owned AGE database did not become ready." >&2
    return 1
}

runtime_environment() {
    local host="$1"
    printf 'PGAG_ADMIN_DATABASE_URL=postgresql://postgres:%s@%s:5432/pgag_age_recovery\nPGAG_DATABASE_URL=postgresql://pgag_restore:%s@%s:5432/pgag_age_recovery\nPGAG_AGE_RECOVERY_DIRECTORY=/drill\nPGAG_AGE_SOURCE_REMOVED=%s\nPGAG_AGE_RECOVERY_BUILD_IDENTITY=%s\n' \
        "$password" "$host" "$runtime_password" "$host" "$source_removed" "$build_identity" \
        > "$directory/private/runtime.env"
}

phase() {
    local name="$run_id-$1"
    runtime_environment "$2"
    containers+=("$name")
    local phase_status=0
    "$engine" run --name "$name" --network "$network" --cpus 2 --memory 2g \
        --user "$host_uid:$host_gid" -w /drill \
        --env-file "$directory/private/runtime.env" \
        -v "$PWD/$directory/private:/drill" \
        -v "$PWD/scripts/smoke-age-recovery.py:/smokes/smoke-age-recovery.py:ro" \
        "$runtime_image" python /smokes/smoke-age-recovery.py "$1" \
        > "$directory/$1.log" 2>&1 || phase_status=$?
    cat "$directory/$1.log"
    if [[ "$phase_status" != 0 && "$1" == recover ]]; then
        "$engine" logs "$restore_db" 2>&1 |
            grep 'ERROR:' > "$directory/restore-errors.log" || true
        cat "$directory/restore-errors.log" >&2
    fi
    return "$phase_status"
}

start_database "$source_db"
source_host="$(database_host "$source_db")"
runtime_environment "$source_host"
containers+=("$run_id-migrate")
"$engine" run --name "$run_id-migrate" --network "$network" --user "$host_uid:$host_gid" \
    --env-file "$directory/private/runtime.env" "$runtime_image" pg-agmemory migrate \
    > "$directory/migrate.log" 2>&1
"$engine" exec "$source_db" psql -U postgres -d pgag_age_recovery -v ON_ERROR_STOP=1 \
    -c 'CREATE EXTENSION age' > "$directory/extension.log" 2>&1
"$engine" exec --user "$host_uid:$host_gid" "$source_db" psql -U postgres \
    -d pgag_age_recovery -v ON_ERROR_STOP=1 -f /drill/roles.sql \
    > "$directory/roles.log" 2>&1
phase seed "$source_host"
"$engine" exec --user "$host_uid:$host_gid" "$source_db" pg_dump -U postgres \
    -d pgag_age_recovery --format=custom --file=/drill/old.dump \
    --exclude-extension=age --exclude-schema=ag_catalog --exclude-schema='pgag_age_*' \
    > "$directory/dump.log" 2>&1
"$engine" exec --user "$host_uid:$host_gid" "$source_db" pg_restore --list /drill/old.dump \
    > "$directory/private/archive.list"
phase manifest "$source_host"
cp "$directory/private/archive.list" "$directory/dump-manifest.list"
phase later "$source_host"
"$engine" stop "$source_db" >/dev/null
if [[ "$engine" == docker ]]; then
    docker rm --volumes "$source_db" >/dev/null
else
    container rm "$source_db" >/dev/null
fi
source_removed=true
if "$engine" inspect "$source_db" >/dev/null 2>&1; then
    echo "Source container unexpectedly remains." >&2
    exit 1
fi
start_database "$restore_db"
"$engine" exec "$restore_db" psql -U postgres -d pgag_age_recovery -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE pgag_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;" \
    > "$directory/restore-role.log" 2>&1
"$engine" exec --user "$host_uid:$host_gid" "$restore_db" psql -U postgres \
    -d pgag_age_recovery -v ON_ERROR_STOP=1 -f /drill/roles.sql \
    >> "$directory/restore-role.log" 2>&1
"$engine" exec --user "$host_uid:$host_gid" "$restore_db" pg_restore -U postgres \
    -d pgag_age_recovery --exit-on-error --single-transaction /drill/old.dump \
    > "$directory/restore.log" 2>&1
"$engine" exec "$restore_db" psql -U postgres -d pgag_age_recovery -v ON_ERROR_STOP=1 \
    -c 'CREATE EXTENSION age; GRANT USAGE ON SCHEMA ag_catalog TO pgag_runtime;' \
    > "$directory/fresh-extension.log" 2>&1
"$engine" exec "$restore_db" psql -U postgres -d pgag_age_recovery -Atc \
    "SELECT json_build_object('graphs', (SELECT count(*) FROM ag_catalog.ag_graph), \
     'physical_schemas',(SELECT count(*) FROM pg_namespace WHERE nspname ~ '^pgag_age_[0-9a-f]{32}$'), \
     'labels',(SELECT count(*) FROM ag_catalog.ag_label), \
     'missing_graph_namespaces',(SELECT count(*) FROM ag_catalog.ag_graph g \
       LEFT JOIN pg_namespace n ON n.oid=g.namespace WHERE n.oid IS NULL));" \
    > "$directory/restored-age-catalog.json"
phase recover "$(database_host "$restore_db")"
cp "$directory/private/report.json" "$directory/report.json"
