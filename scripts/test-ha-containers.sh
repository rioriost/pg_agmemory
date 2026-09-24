#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077

usage() {
    echo "Usage: $0 NEW_PRIVATE_PROJECT_DIRECTORY --allow-owned-promotion [container|docker]"
    echo "Owned two-node synchronous SQL HA laboratory, NOT production HA qualification."
    echo "Explicit opt-in permits promotion only after this harness destroys its own primary."
    echo "No API/worker/service starts; even renewed lab replication is not serving-authorized."
    echo "A replacement async standby is rebuilt from the promoted node, never old-primary data."
    echo "The owned lab then explicitly renews synchronous policy and verifies one guarded write."
    echo "Use a NEW project-relative directory with an existing non-symlink parent."
    echo "Private synthetic physical backups remain there, never as release assets."
    echo "PGAG_HA_RUNTIME_IMAGE selects a caller-owned image; otherwise builds the runtime target."
}
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
if [[ $# -lt 2 || $# -gt 3 || "${2:-}" != --allow-owned-promotion ]]; then
    echo explicit_owned_promotion_opt_in_required >&2
    usage >&2
    exit 2
fi
directory="$1"
engine="${3:-container}"
allow_owned_promotion=true
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
if ! validate_output_path "$directory"; then echo new_private_project_path_required >&2; exit 2; fi
if [[ -n "${PGAG_DATABASE_URL:-}${PGAG_ADMIN_DATABASE_URL:-}${PGHOST:-}${PGSERVICE:-}" ]]; then
    echo external_database_target_forbidden >&2
    exit 2
fi
command -v "$engine" >/dev/null
command -v jq >/dev/null
command -v od >/dev/null
service_version="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' src/pg_agmemory/__init__.py)"
[[ "$service_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*$ ]]
mkdir -m 700 -- "$directory"
chmod 700 "$PWD/$directory"
SECONDS=0
run_id="pgag-ha-$(date +%s)-$$-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
primary="${run_id}-primary"
standby="${run_id}-standby"
replacement="${run_id}-replacement"
postgres_image="docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
image="${PGAG_HA_RUNTIME_IMAGE:-pg-agmemory-ha:${run_id}}"
network=default
network_created=false
image_owned=false
source_destroyed=null
fencing_verified=null
pre_fence_promotion_rejected=null
promotion_executed=null
backup_verified=null
replacement_backup_verified=null
original_primary_remains_fenced=null
renewal_primary_remains_fenced=null
primary_host=""
standby_host=""
replacement_host=""
system_identifier=""
timeline_before=null
failure_code=drill_incomplete
timings='{}'
containers=()

remove_owned_container() {
    if [[ "$engine" == docker ]]; then docker rm --force --volumes "$1" >/dev/null 2>&1
    else container rm --force "$1" >/dev/null 2>&1; fi
}

engine_alive() {
    if [[ "$engine" == docker ]]; then docker info >/dev/null 2>&1
    else container system status >/dev/null 2>&1; fi
}

record_elapsed() {
    timings="$(jq -cn --argjson before "$timings" --arg name "$1" \
        --argjson seconds "$((SECONDS - $2))" '$before + {($name): $seconds}')"
}

cleanup() {
    local status=$? cleanup_started=$SECONDS name failed=false
    local synchronous=null uncertain=null preserved=null probe=null artifact=null
    local replacement_evidence=null renewal=null
    trap - EXIT INT TERM
    set +e
    for name in ${containers[@]+"${containers[@]}"}; do
        if [[ "$name" == "$primary" && "$source_destroyed" == true ]]; then continue; fi
        if ! remove_owned_container "$name"; then failed=true; fi
    done
    if [[ "$network_created" == true ]] && ! docker network rm "$network" >/dev/null 2>&1; then
        failed=true
    fi
    if [[ "$image_owned" == true ]] && ! "$engine" image rm "$image" >/dev/null 2>&1; then
        failed=true
    fi
    if ! rm -f -- "$directory/.credentials.env" "$directory/.standby.conf" \
        "$directory/.replacement.conf"; then failed=true; fi
    if [[ "$failed" == true ]]; then
        if [[ $status -eq 0 ]]; then failure_code=owned_cleanup_failed; fi
        status=1
    fi
    if [[ -f "$directory/failure.json" ]]; then
        failure_code="$(jq -er '.failure_code | select(test("^[a-z][a-z0-9_]{0,79}$"))' \
            "$directory/failure.json" 2>/dev/null)" || failure_code=ha_stage_failed
        status=1
    fi
    if [[ -f "$directory/synchronous.json" ]]; then
        synchronous="$(jq -ce . "$directory/synchronous.json")" || status=1
    fi
    if [[ -f "$directory/uncertain.json" ]]; then
        uncertain="$(jq -ce . "$directory/uncertain.json")" || status=1
    fi
    if [[ -f "$directory/preserved.json" ]]; then
        preserved="$(jq -ce . "$directory/preserved.json")" || status=1
    fi
    if [[ -f "$directory/probe.json" ]]; then
        probe="$(jq -ce . "$directory/probe.json")" || status=1
    fi
    if [[ -f "$directory/artifact.json" ]]; then
        artifact="$(jq -ce . "$directory/artifact.json")" || status=1
    fi
    if [[ -f "$directory/replacement.json" ]]; then
        replacement_evidence="$(jq -ce . "$directory/replacement.json")" || status=1
    fi
    if [[ -f "$directory/renewal.json" ]]; then
        renewal="$(jq -ce . "$directory/renewal.json")" || status=1
    fi
    if [[ "$source_destroyed" != true || "$fencing_verified" != true \
          || "$pre_fence_promotion_rejected" != true || "$promotion_executed" != true \
          || "$backup_verified" != true || "$synchronous" == null || "$preserved" == null \
          || "$uncertain" == null || "$probe" == null || "$artifact" == null \
          || "$replacement_backup_verified" != true || "$original_primary_remains_fenced" != true \
          || "$replacement_evidence" == null || "$renewal" == null \
          || "$renewal_primary_remains_fenced" != true ]]; then status=1; fi
    record_elapsed cleanup "$cleanup_started"
    timings="$(jq -cn --argjson before "$timings" --argjson total "$SECONDS" \
        '$before + {total: $total}')"
    if [[ $status -eq 0 ]]; then failure_code=""; fi
    # Terminal report only: a failed attempt is not retried or overwritten as passed.
    if ! (set -o noclobber; jq -n \
        --arg version "$service_version" --arg failure "$failure_code" \
        --argjson success "$([[ $status -eq 0 ]] && echo true || echo false)" \
        --argjson destroyed "$source_destroyed" --argjson fenced "$fencing_verified" \
        --argjson rejected "$pre_fence_promotion_rejected" --argjson promoted "$promotion_executed" \
        --argjson backup "$backup_verified" --argjson timeline "$timeline_before" \
        --argjson synchronous "$synchronous" --argjson uncertain "$uncertain" \
        --argjson preserved "$preserved" \
        --argjson replacement "$replacement_evidence" \
        --argjson replacement_backup "$replacement_backup_verified" \
        --argjson still_fenced "$original_primary_remains_fenced" \
        --argjson renewal "$renewal" --argjson renewal_fenced "$renewal_primary_remains_fenced" \
        --argjson probe "$probe" --argjson artifact "$artifact" --argjson timings "$timings" '{
            format: "pgag-ha-drill-v4", service_version: $version, api_version: "v1",
            schema_version: 22, postgres_version_num: 180006, pgvector_version: "0.8.6",
            status: (if $success then "passed" else "failed" end),
            failure_code: (if $success then null else $failure end),
            source_destroyed: $destroyed, fencing_verified: $fenced,
            pre_fence_promotion_rejected: $rejected, promotion_executed: $promoted,
            backup_verified: $backup, writer_policy_verified: $synchronous.writer_policy_verified,
            short_pause_blocked_ack: $synchronous.short_pause_blocked_ack,
            uncertain_commit_reconciled: $uncertain.uncertain_commit_reconciled,
            acknowledged_state_matches: $preserved.acknowledged_state_matches,
            effect_state_preserved: $preserved.effect_state_preserved,
            postpromotion_probe_verified: $probe.postpromotion_probe_verified,
            timeline_before: $timeline, timeline_after: $preserved.timeline_after,
            artifact: $artifact, synchronous: $synchronous, uncertain: $uncertain,
            preserved: $preserved, probe: $probe,
            replacement: $replacement,
            replacement_backup_verified: $replacement_backup,
            original_primary_remains_fenced: $still_fenced,
            replacement_state_matches: $replacement.replacement_state_matches,
            renewal: $renewal, renewal_state_matches: $renewal.renewal_state_matches,
            renewal_primary_remains_fenced: $renewal_fenced,
            elapsed_seconds: $timings, production_qualified: false,
            host_failure_domain_independent: false, network_partition_qualified: false,
            commit_timeout_qualified: false, automatic_failover: false,
            automatic_service_start: false, serving_authorized: false, effect_reexecution: false
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

verify_primary_absent() {
    engine_alive || return 1
    if "$engine" inspect "$primary" >/dev/null 2>&1; then return 1; fi
    if "$engine" exec "$primary" pg_isready -t 1 >/dev/null 2>&1; then return 1; fi
    engine_alive || return 1
}

owned_promote() {
    local candidate_state promoted_result
    # Only live harness state from targeted destruction can open this gate, never a JSON file.
    [[ "$allow_owned_promotion" == true ]] || return 41
    [[ "$source_destroyed" == true && "$fencing_verified" == true ]] || return 42
    [[ "$pre_fence_promotion_rejected" == true ]] || return 43
    [[ "$run_id" =~ ^pgag-ha-[0-9]+-[0-9]+-[0-9a-f]{16}$ ]] || return 44
    [[ "$primary" == "${run_id}-primary" && "$standby" == "${run_id}-standby" ]] || return 44
    [[ "$system_identifier" =~ ^[0-9]{1,20}$ && "$timeline_before" =~ ^[0-9]+$ ]] || return 44
    verify_primary_absent || return 45
    candidate_state="$("$engine" exec "$standby" psql -U postgres -d pgag_ha -At \
        -v ON_ERROR_STOP=1 -c "SELECT s.system_identifier::text || '|' ||
            c.timeline_id::text || '|' || pg_is_in_recovery()::text || '|' ||
            current_setting('transaction_read_only')
            FROM pg_control_system() s CROSS JOIN pg_control_checkpoint() c" 2>/dev/null)" \
        || return 46
    [[ "$candidate_state" == "${system_identifier}|${timeline_before}|true|on" ]] || return 46
    promoted_result="$("$engine" exec "$standby" timeout 40s psql -U postgres -d pgag_ha -At \
        -v ON_ERROR_STOP=1 -c 'SELECT pg_promote(wait => true, wait_seconds => 30)' 2>/dev/null)" \
        || return 47
    [[ "$promoted_result" == t ]] || return 47
    promotion_executed=true
}

container_host() {
    if [[ "$engine" == docker ]]; then printf '%s\n' "$1"
    else
        container inspect "$1" | jq -er \
            '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address)
             | split("/")[0] | select(length > 0)'
    fi
}

phase() {
    local stage="$1" phase_started=$SECONDS helper="${run_id}-${1}"
    if [[ "$stage" == replacement ]]; then helper="${run_id}-replacement-check"; fi
    failure_code="${stage}_failed"
    containers+=("$helper")
    "$engine" run --name "$helper" --network "$network" \
        --user "$(id -u):$(id -g)" \
        -v "$PWD:/work:ro" -v "$PWD/src:/app/src:ro" -v "$PWD/$directory:/drill" \
        --env-file "$directory/.credentials.env" \
        -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/work/src \
        -e "PGAG_HA_OWNED_RUN=$run_id" -e "PGAG_HA_ENGINE=$engine" \
        -e PGAG_HA_ALLOW_OWNED_PROMOTION=1 \
        -e "PGAG_HA_PRIMARY_HOST=$primary_host" -e "PGAG_HA_STANDBY_HOST=$standby_host" \
        -e "PGAG_HA_REPLACEMENT_HOST=$replacement_host" \
        "$image" timeout 180s python /work/scripts/smoke-ha.py "$stage" >/dev/null 2>&1
    record_elapsed "$stage" "$phase_started"
}

wait_database() {
    local name="$1"
    for ((attempt = 0; attempt < 90; attempt++)); do
        if "$engine" exec "$name" pg_isready -t 1 -h 127.0.0.1 -U postgres -d pgag_ha \
            >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    return 1
}

failure_code=engine_unavailable
engine_alive
failure_code=credential_generation_failed
for key in POSTGRES_PASSWORD HA_WRITER_PASSWORD HA_REPLICATION_PASSWORD; do
    value="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    [[ "$value" =~ ^[0-9a-f]{64}$ ]]
    printf '%s=%s\n' "$key" "$value" >> "$directory/.credentials.env"
done
unset key value
if [[ "$engine" == docker ]]; then
    failure_code=network_creation_failed
    network="${run_id}-network"
    docker network create --internal "$network" >/dev/null
    network_created=true
fi
if [[ -z "${PGAG_HA_RUNTIME_IMAGE:-}" ]]; then
    failure_code=runtime_build_failed
    started=$SECONDS
    image_owned=true
    "$engine" build --target runtime --tag "$image" . >/dev/null 2>&1
    record_elapsed build "$started"
fi
failure_code=primary_start_failed
started=$SECONDS
containers+=("$primary")
"$engine" run -d --name "$primary" --network "$network" \
    --env-file "$directory/.credentials.env" -e POSTGRES_DB=pgag_ha \
    -e PGDATA=/var/lib/postgresql/18/ha --entrypoint bash "$postgres_image" -ceu '
        install -d -m 700 -o postgres -g postgres /owned /owned/base
        exec /usr/local/bin/docker-entrypoint.sh postgres -c wal_level=replica \
            -c max_wal_senders=2 -c wal_keep_size=128MB
    ' >/dev/null 2>&1
wait_database "$primary"
primary_host="$(container_host "$primary")"
record_elapsed primary_start "$started"
phase seed
system_identifier="$(jq -er '.control.system_identifier' "$directory/seed.json")"
timeline_before="$(jq -er '.control.timeline' "$directory/seed.json")"
failure_code=replication_access_failed
"$engine" exec "$primary" bash -ceu '
    printf "\nhost replication pgag_ha_replication 0.0.0.0/0 scram-sha-256\n" >> "$PGDATA/pg_hba.conf"
    exec psql -U postgres -d pgag_ha -v ON_ERROR_STOP=1 -c "SELECT pg_reload_conf()"
' >/dev/null 2>&1
failure_code=basebackup_failed
started=$SECONDS
"$engine" exec "$primary" bash -ceu '
    export PGPASSWORD="$HA_REPLICATION_PASSWORD"
    exec timeout 180s gosu postgres pg_basebackup -h 127.0.0.1 -U pgag_ha_replication \
        -D /owned/base --format=plain --wal-method=fetch --checkpoint=fast \
        --manifest-checksums=SHA256
' >/dev/null 2>&1
failure_code=backup_manifest_failed
"$engine" exec "$primary" timeout 90s gosu postgres pg_verifybackup /owned/base >/dev/null 2>&1
backup_verified=true
"$engine" exec "$primary" cat /owned/base/backup_manifest | jq -e '{
    format: "pgag-pitr-basebackup-v1", manifest_verified: true,
    start_lsn: .["WAL-Ranges"][0].["Start-LSN"],
    end_lsn: .["WAL-Ranges"][0].["End-LSN"], timeline: .["WAL-Ranges"][0].Timeline
}' > "$directory/backup.json"
"$engine" exec "$primary" timeout 90s tar -C /owned/base -cf - . \
    > "$directory/basebackup.tar" 2>/dev/null
record_elapsed basebackup "$started"
phase artifact
failure_code=standby_start_failed
started=$SECONDS
containers+=("$standby")
"$engine" run -d --name "$standby" --network "$network" \
    --env-file "$directory/.credentials.env" \
    -v "$PWD/$directory:/drill:ro" -e PGDATA=/var/lib/postgresql/18/ha \
    --entrypoint bash "$postgres_image" -ceu '
        install -d -m 700 -o postgres -g postgres "$PGDATA"
        timeout 90s tar --no-same-owner -xf /drill/basebackup.tar -C "$PGDATA"
        chown -R postgres:postgres "$PGDATA"
        chmod 700 "$PGDATA"
        timeout 90s gosu postgres pg_verifybackup "$PGDATA"
        cat /drill/.standby.conf >> "$PGDATA/postgresql.auto.conf"
        touch "$PGDATA/standby.signal"
        chown postgres:postgres "$PGDATA/postgresql.auto.conf" "$PGDATA/standby.signal"
        exec gosu postgres postgres -D "$PGDATA" -c hot_standby=on -c "listen_addresses=*"
    ' >/dev/null 2>&1
wait_database "$standby"
standby_host="$(container_host "$standby")"
record_elapsed standby_start "$started"
phase synchronous
phase uncertain

failure_code=pre_fence_guard_failed
if owned_promote; then
    failure_code=promotion_before_fence
    exit 1
else
    guard_status=$?
    [[ "$guard_status" -eq 42 ]]
fi
phase prefence
pre_fence_promotion_rejected=true
failure_code=primary_fencing_failed
started=$SECONDS
remove_owned_container "$primary"
verify_primary_absent
source_destroyed=true
fencing_verified=true
primary_host=""
record_elapsed fencing "$started"
failure_code=owned_promotion_failed
started=$SECONDS
owned_promote
record_elapsed promotion "$started"
phase verify

failure_code=replacement_basebackup_failed
started=$SECONDS
verify_primary_absent
"$engine" exec "$standby" bash -ceu '
    install -d -m 700 -o postgres -g postgres /owned /owned/replacement-base
    export PGPASSWORD="$HA_REPLICATION_PASSWORD"
    exec timeout 180s gosu postgres pg_basebackup -h 127.0.0.1 -U pgag_ha_replication \
        -D /owned/replacement-base --format=plain --wal-method=fetch --checkpoint=fast \
        --manifest-checksums=SHA256
' >/dev/null 2>&1
failure_code=replacement_manifest_failed
"$engine" exec "$standby" timeout 90s gosu postgres pg_verifybackup \
    /owned/replacement-base >/dev/null 2>&1
replacement_backup_verified=true
"$engine" exec "$standby" cat /owned/replacement-base/backup_manifest | jq -e '{
    format: "pgag-pitr-basebackup-v1", manifest_verified: true,
    start_lsn: .["WAL-Ranges"][0].["Start-LSN"],
    end_lsn: .["WAL-Ranges"][0].["End-LSN"], timeline: .["WAL-Ranges"][0].Timeline
}' > "$directory/replacement-backup.json"
"$engine" exec "$standby" timeout 90s tar -C /owned/replacement-base -cf - . \
    > "$directory/replacement-basebackup.tar" 2>/dev/null
record_elapsed replacement_basebackup "$started"

failure_code=replacement_start_failed
started=$SECONDS
containers+=("$replacement")
"$engine" run -d --name "$replacement" --network "$network" \
    -v "$PWD/$directory:/drill:ro" -e PGDATA=/var/lib/postgresql/18/ha \
    --entrypoint bash "$postgres_image" -ceu '
        install -d -m 700 -o postgres -g postgres "$PGDATA"
        contents="$(ls -A "$PGDATA")"
        test -z "$contents"
        timeout 90s tar --no-same-owner -xf /drill/replacement-basebackup.tar -C "$PGDATA"
        chown -R postgres:postgres "$PGDATA"
        chmod 700 "$PGDATA"
        timeout 90s gosu postgres pg_verifybackup "$PGDATA"
        cat /drill/.replacement.conf >> "$PGDATA/postgresql.auto.conf"
        touch "$PGDATA/standby.signal"
        chown postgres:postgres "$PGDATA/postgresql.auto.conf" "$PGDATA/standby.signal"
        exec gosu postgres postgres -D "$PGDATA" -c hot_standby=on -c "listen_addresses=*"
    ' >/dev/null 2>&1
wait_database "$replacement"
replacement_host="$(container_host "$replacement")"
record_elapsed replacement_start "$started"
failure_code=replacement_ownership_record_failed
(set -o noclobber; jq -n '{
    kind: "fresh_basebackup", fresh_empty_data_directory: true, no_old_primary_reuse: true
}' > "$directory/replacement-owned.json")
phase replacement
failure_code=replacement_fence_recheck_failed
verify_primary_absent
original_primary_remains_fenced=true
phase renewal
failure_code=renewal_fence_recheck_failed
verify_primary_absent
renewal_primary_remains_fenced=true
failure_code=drill_incomplete
