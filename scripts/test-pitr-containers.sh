#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077

usage() {
    echo "Usage: $0 NEW_PRIVATE_PROJECT_DIRECTORY [container|docker]"
    echo "SQL-only physical basebackup/WAL drill; recovery stays paused and read-only."
    echo "Use a NEW project-relative directory with an existing, non-symlink parent."
    echo "Private synthetic backups/WAL remain there; they are NOT release assets."
    echo "PGAG_PITR_RUNTIME_IMAGE selects a caller-owned image (never removed)."
    echo "Otherwise builds the repository runtime target; no API or workers are started."
}
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
if [[ $# -lt 1 || $# -gt 2 ]]; then usage >&2; exit 2; fi
directory="$1"
engine="${2:-container}"
if [[ "$engine" != container && "$engine" != docker ]]; then
    echo invalid_container_engine >&2
    exit 2
fi
cd -P "$(dirname "${BASH_SOURCE[0]}")/.."

validate_output_path() {
    local candidate="$1" component prefix="" parent
    [[ "$candidate" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ ]] || return 1
    local -a components
    IFS=/ read -r -a components <<< "$candidate"
    for component in "${components[@]}"; do
        [[ "$component" != . && "$component" != .. ]] || return 1
        prefix="${prefix:+$prefix/}$component"
        [[ ! -L "$prefix" ]] || return 1
    done
    [[ ! -e "$candidate" ]] || return 1
    parent="${candidate%/*}"
    [[ "$parent" != "$candidate" ]] || parent=.
    [[ -d "$parent" ]] || return 1
}
if ! validate_output_path "$directory"; then
    echo new_private_project_path_required >&2
    exit 2
fi
if [[ -n "${PGAG_DATABASE_URL:-}${PGAG_ADMIN_DATABASE_URL:-}${PGHOST:-}${PGSERVICE:-}" ]]; then
    echo external_database_target_forbidden >&2
    exit 2
fi
command -v jq >/dev/null
command -v "$engine" >/dev/null
command -v od >/dev/null
service_version="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' src/pg_agmemory/__init__.py)"
if [[ ! "$service_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*$ ]]; then
    echo invalid_package_version >&2
    exit 2
fi
mkdir -m 700 -- "$directory"
chmod 700 "$PWD/$directory"
SECONDS=0
run_id="pgag-pitr-$(date +%s)-$$-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
primary="${run_id}-primary"
restored="${run_id}-restored"
postgres_image="docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
image="${PGAG_PITR_RUNTIME_IMAGE:-pg-agmemory-pitr:${run_id}}"
network=default
network_created=false
image_owned=false
primary_destroyed=false
backup_verified=false
verification_complete=false
failure_code=drill_incomplete
timings='{}'
containers=()

remove_owned_container() {
    if [[ "$engine" == docker ]]; then
        docker rm --force --volumes "$1" >/dev/null 2>&1
    else
        container rm --force "$1" >/dev/null 2>&1
    fi
}

record_elapsed() {
    timings="$(jq -cn --argjson before "$timings" --arg name "$1" \
        --argjson seconds "$((SECONDS - $2))" '$before + {($name): $seconds}')"
}

cleanup() {
    local status=$? cleanup_started=$SECONDS name failed=false verification=null artifacts=null
    trap - EXIT INT TERM
    set +e
    for name in ${containers[@]+"${containers[@]}"}; do
        if [[ "$name" == "$primary" && "$primary_destroyed" == true ]]; then continue; fi
        if ! remove_owned_container "$name"; then failed=true; fi
    done
    if [[ "$network_created" == true ]] && ! docker network rm "$network" >/dev/null 2>&1; then
        failed=true
    fi
    if [[ "$image_owned" == true ]] && ! "$engine" image rm "$image" >/dev/null 2>&1; then
        failed=true
    fi
    if ! rm -f -- "$directory/.credentials.env"; then failed=true; fi
    if [[ "$failed" == true ]]; then
        if [[ $status -eq 0 ]]; then failure_code=owned_cleanup_failed; fi
        status=1
    fi
    if [[ -f "$directory/failure.json" ]]; then
        failure_code="$(jq -er '.failure_code | select(test("^[a-z][a-z0-9_]{0,79}$"))' \
            "$directory/failure.json" 2>/dev/null)" || failure_code=pitr_stage_failed
        status=1
    fi
    if [[ "$verification_complete" == true ]]; then
        verification="$(jq -ce . "$directory/verification.json")" || status=1
    fi
    if [[ -f "$directory/artifacts.json" ]]; then
        artifacts="$(jq -ce . "$directory/artifacts.json")" || status=1
    fi
    if [[ "$verification_complete" != true || "$primary_destroyed" != true \
          || "$backup_verified" != true ]]; then status=1; fi
    record_elapsed cleanup "$cleanup_started"
    timings="$(jq -cn --argjson before "$timings" --argjson total "$SECONDS" \
        '$before + {total: $total}')"
    if [[ $status -eq 0 ]]; then failure_code=""; fi
    # No report exists before this terminal write; a failed attempt is never retried as passed.
    if ! (set -o noclobber; jq -n --arg version "$service_version" \
        --arg failure "$failure_code" --argjson success "$([[ $status -eq 0 ]] && echo true || echo false)" \
        --argjson destroyed "$primary_destroyed" --argjson backup "$backup_verified" \
        --argjson verified "$verification_complete" --argjson timings "$timings" \
        --argjson verification "$verification" --argjson artifacts "$artifacts" '{
            format: "pgag-pitr-drill-v1", service_version: $version, api_version: "v1",
            schema_version: 23, postgres_version_num: 180006, pgvector_version: "0.8.6",
            status: (if $success then "passed" else "failed" end),
            failure_code: (if $success then null else $failure end),
            primary_destroyed: $destroyed, backup_verified: $backup,
            wal_replay_verified: $verified, recovery_paused: $verified, read_only: $verified,
            target_state_matches: $verified,
            latest_state_matches: (if $verification == null then null
                                   else $verification.latest_state_matches end),
            restore_authorized: false, automatic_promotion: false, automatic_service_start: false,
            production_qualified: false, host_failure_domain_independent: false,
            elapsed_seconds: $timings, verification: $verification, artifacts: $artifacts
        }' > "$directory/report.json"); then
        echo report_write_failed >&2
        status=1
    fi
    if [[ -f "$directory/report.json" ]]; then cat "$directory/report.json"; fi
    exit "$status"
}
trap cleanup EXIT
trap 'failure_code=interrupted; exit 130' INT
trap 'failure_code=terminated; exit 143' TERM

failure_code=engine_unavailable
if [[ "$engine" == container ]]; then container system status >/dev/null 2>&1
else docker info >/dev/null 2>&1; fi
failure_code=credential_generation_failed
password="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
[[ "$password" =~ ^[0-9a-f]{64}$ ]]
printf 'POSTGRES_PASSWORD=%s\n' "$password" > "$directory/.credentials.env"
unset password
if [[ "$engine" == docker ]]; then
    failure_code=network_creation_failed
    network="${run_id}-network"
    docker network create --internal "$network" >/dev/null
    network_created=true
fi
if [[ -z "${PGAG_PITR_RUNTIME_IMAGE:-}" ]]; then
    failure_code=runtime_build_failed
    started=$SECONDS
    image_owned=true
    "$engine" build --target runtime --tag "$image" . >/dev/null 2>&1
    record_elapsed build "$started"
fi

container_host() {
    if [[ "$engine" == docker ]]; then printf '%s\n' "$1"
    else
        container inspect "$1" | jq -er \
            '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address)
             | split("/")[0] | select(length > 0)'
    fi
}

phase() {
    local stage="$1" host="${2:-}" phase_started=$SECONDS
    failure_code="${stage}_failed"
    containers+=("${run_id}-${stage}")
    "$engine" run --name "${run_id}-${stage}" --network "$network" \
        --user "$(id -u):$(id -g)" \
        -v "$PWD:/work:ro" -v "$PWD/src:/app/src:ro" -v "$PWD/$directory:/drill" \
        --env-file "$directory/.credentials.env" \
        -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/work/src \
        -e "PGAG_PITR_OWNED_RUN=$run_id" -e "PGAG_PITR_ENGINE=$engine" \
        -e "PGAG_PITR_DB_HOST=$host" \
        "$image" timeout 120s python /work/scripts/smoke-pitr.py "$stage" >/dev/null 2>&1
    record_elapsed "$stage" "$phase_started"
}

wait_database() {
    local name="$1" paused="$2" answer
    for ((attempt = 0; attempt < 90; attempt++)); do
        if "$engine" exec "$name" pg_isready -t 1 -h 127.0.0.1 -U postgres -d pgag_pitr \
            >/dev/null 2>&1; then
            if [[ "$paused" == false ]]; then return 0; fi
            answer="$("$engine" exec "$name" psql -U postgres -d pgag_pitr -Atc \
                "SELECT pg_is_in_recovery() AND pg_is_wal_replay_paused()
                 AND current_setting('transaction_read_only')='on'" 2>/dev/null)" || answer=""
            if [[ "$answer" == t ]]; then return 0; fi
        fi
        sleep 1
    done
    return 1
}

failure_code=primary_start_failed
started=$SECONDS
containers+=("$primary")
"$engine" run -d --name "$primary" --network "$network" \
    --env-file "$directory/.credentials.env" -e POSTGRES_DB=pgag_pitr \
    -e PGDATA=/var/lib/postgresql/18/pitr \
    --entrypoint bash "$postgres_image" -ceu '
        install -d -m 700 -o postgres -g postgres /owned /owned/archive /owned/base
        exec /usr/local/bin/docker-entrypoint.sh postgres \
            -c wal_level=replica -c archive_mode=on \
            -c "archive_command=test ! -f /owned/archive/%f && cp %p /owned/archive/%f" \
            -c max_wal_senders=2 -c wal_keep_size=128MB
    ' >/dev/null 2>&1
wait_database "$primary" false
record_elapsed primary_start "$started"
source_host="$(container_host "$primary")"
phase seed "$source_host"

failure_code=basebackup_failed
started=$SECONDS
"$engine" exec "$primary" timeout 180s gosu postgres pg_basebackup \
    -h /var/run/postgresql -U postgres -D /owned/base --format=plain \
    --wal-method=fetch --checkpoint=fast --manifest-checksums=SHA256 >/dev/null 2>&1
failure_code=backup_manifest_failed
"$engine" exec "$primary" timeout 90s gosu postgres pg_verifybackup /owned/base >/dev/null 2>&1
backup_verified=true
"$engine" exec "$primary" cat /owned/base/backup_manifest | jq -e '{
    format: "pgag-pitr-basebackup-v1", manifest_verified: true,
    start_lsn: .["WAL-Ranges"][0].["Start-LSN"],
    end_lsn: .["WAL-Ranges"][0].["End-LSN"],
    timeline: .["WAL-Ranges"][0].Timeline
}' > "$directory/backup.json"
record_elapsed basebackup "$started"
phase target "$source_host"
phase after "$source_host"
phase archive "$source_host"

failure_code=physical_copy_failed
started=$SECONDS
"$engine" exec "$primary" timeout 90s tar -C /owned/base -cf - . \
    > "$directory/basebackup.tar" 2>/dev/null
"$engine" exec "$primary" timeout 90s tar -C /owned/archive -cf - . \
    > "$directory/wal-archive.tar" 2>/dev/null
record_elapsed physical_copy "$started"
phase artifacts

failure_code=primary_destruction_failed
started=$SECONDS
"$engine" stop "$primary" >/dev/null 2>&1
remove_owned_container "$primary"
if "$engine" inspect "$primary" >/dev/null 2>&1; then exit 1; fi
if "$engine" exec "$primary" pg_isready -t 1 >/dev/null 2>&1; then exit 1; fi
if [[ "$engine" == container ]]; then container system status >/dev/null 2>&1
else docker info >/dev/null 2>&1; fi
primary_destroyed=true
record_elapsed primary_destruction "$started"

failure_code=restore_start_failed
started=$SECONDS
containers+=("$restored")
"$engine" run -d --name "$restored" --network "$network" \
    -v "$PWD/$directory:/drill:ro" -e PGDATA=/var/lib/postgresql/18/pitr \
    --entrypoint bash "$postgres_image" -ceu '
        install -d -m 700 -o postgres -g postgres "$PGDATA" /owned /owned/archive
        timeout 90s tar --no-same-owner -xf /drill/basebackup.tar -C "$PGDATA"
        timeout 90s tar --no-same-owner -xf /drill/wal-archive.tar -C /owned/archive
        chown -R postgres:postgres "$PGDATA" /owned/archive
        chmod 700 "$PGDATA" /owned/archive
        timeout 90s gosu postgres pg_verifybackup "$PGDATA"
        touch "$PGDATA/recovery.signal"
        chown postgres:postgres "$PGDATA/recovery.signal"
        exec gosu postgres postgres -D "$PGDATA" \
            -c archive_mode=off -c hot_standby=on -c "listen_addresses=*" \
            -c "restore_command=cp /owned/archive/%f %p" \
            -c recovery_target_name=pgag_m5_target -c recovery_target_action=pause \
            -c recovery_target_timeline=current
    ' >/dev/null 2>&1
failure_code=recovery_target_unreached
wait_database "$restored" true
record_elapsed restore_start "$started"
phase verify "$(container_host "$restored")"
verification_complete=true
failure_code=drill_incomplete
