#!/usr/bin/env bash
# Private serial measurement only. No model processes or shared-resource cleanup.
set +x
set -Eeuo pipefail
umask 077

usage() {
    printf 'Usage: %s NEW_DIRECTORY_UNDER_.review-artifacts [--smoke]\n' "$0"
    printf '       %s --help | -h\n' "$0"
    printf 'Requires a fresh directory, a committed harness, and Apple Container.\n'
}
usage_error() {
    printf '%s\n' "$1" >&2
    usage >&2
    exit 2
}
if [[ $# -eq 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
    usage
    exit 0
fi
if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "${2:-}" != "--smoke" ) ]]; then
    usage_error "Unsupported arguments."
fi
if [[ -z "$1" || "$1" == -* ]]; then usage_error "A destination, not an option, is required."; fi
destination="${1#./}"
leaf="${destination##*/}"
if [[ ! "$leaf" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    usage_error "Destination must end in a fresh directory name."
fi
case "/$destination/" in
    */../*|*/./*) usage_error "Destination cannot contain dot or parent path components." ;;
esac

script_directory="${BASH_SOURCE[0]%/*}"
if [[ "$script_directory" == "${BASH_SOURCE[0]}" ]]; then script_directory=.; fi
repo="$(cd -- "$script_directory/.." && pwd -P)"
cd "$repo"
case "$destination" in
    .review-artifacts/*) destination="$repo/$destination" ;;
    "$repo/.review-artifacts/"*) ;;
    *) usage_error "Output must be a fresh named private directory beneath .review-artifacts." ;;
esac
parent_path="${destination%/*}"
if [[ ! -d "$parent_path" || -e "$destination" || -L "$destination" ]]; then
    usage_error "Destination must not exist, and its private parent must already exist."
fi
parent="$(cd -- "$parent_path" && pwd -P)"
if [[ "$parent/" != "$repo/.review-artifacts/"* ]]; then
    usage_error "Destination parent resolves outside .review-artifacts."
fi
directory="$parent/$leaf"
for tool in container jq git openssl shasum tar; do command -v "$tool" >/dev/null; done
container system status >/dev/null
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    echo "Commit the reviewed harness first; measurement requires a clean frozen worktree." >&2
    exit 1
fi
mkdir -m 700 "$directory"
baseline=3bb0ee2cb79aa476208ea6446eaa47345305cd0b
current=b8d775d2d9bd1e14ebf9cea4b978a11dce06346c
harness="$(git rev-parse HEAD)"
run_id="$(openssl rand -hex 6)"
db=""
step="pgag-en-${run_id}-step"
image22="localhost/pgag-en-${run_id}:schema22"
image23="localhost/pgag-en-${run_id}:schema23"
database_image="docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
profile=modest
repetitions=4
if [[ "${2:-}" == "--smoke" ]]; then profile=smoke; repetitions=2; fi
owned_images=()
own_db=false
own_step=false
run_complete=false

cleanup_remove() {
    local kind="$1" name="$2" removal_status=0 inventory_status=0 listing="" present=false outcome
    if [[ "$kind" == container ]]; then
        container rm --force "$name" >/dev/null 2>&1 || removal_status=$?
        listing="$(container list --all --quiet 2>/dev/null)" || inventory_status=$?
    else
        container image rm "$name" >/dev/null 2>&1 || removal_status=$?
        listing="$(container image list --quiet 2>/dev/null)" || inventory_status=$?
    fi
    # A failed inspect is ambiguous. Only a successful full inventory can prove
    # absence, including a --rm client already removed before this EXIT handler.
    if (( inventory_status != 0 )); then
        outcome=absence_unverified
    else
        while IFS= read -r candidate; do
            if [[ "$candidate" == "$name" ]]; then present=true; fi
        done <<<"$listing"
        if [[ "$present" == true ]]; then
            outcome=still_present
        elif (( removal_status != 0 )); then
            outcome=already_absent
        else
            outcome=removed
        fi
    fi
    if [[ "$outcome" == absence_unverified || "$outcome" == still_present ]]; then
        cleanup_failures=$((cleanup_failures + 1))
    fi
    cleanup_records+=("$(printf \
        '{"kind":"%s","name":"%s","outcome":"%s","removal_exit_status":%d,"inventory_exit_status":%d}' \
        "$kind" "$name" "$outcome" "$removal_status" "$inventory_status")")
}

cleanup_report() {
    local separator="" record
    printf '{"run_exit_status":%d,"exit_status":%d,"cleanup_complete":%s,' \
        "$run_status" "$status" "$cleanup_complete"
    printf '"cleanup_failures":%d,"only_run_owned_resources_removed":%s,' \
        "$cleanup_failures" "$cleanup_complete"
    printf '"buildkit_preserved":true,"resources":['
    for record in "${cleanup_records[@]+"${cleanup_records[@]}"}"; do
        printf '%s%s' "$separator" "$record"
        separator=,
    done
    printf ']}\n'
}

persist_cleanup_report() {
    cleanup_report >"$directory/cleanup.json"
}

cleanup() {
    run_status=$?
    trap - EXIT INT TERM
    set +e
    status=$run_status
    cleanup_failures=0
    cleanup_records=()
    if [[ "$own_step" == true ]]; then cleanup_remove container "$step"; fi
    if [[ "$own_db" == true ]]; then cleanup_remove container "$db"; fi
    for image in "${owned_images[@]+"${owned_images[@]}"}"; do
        cleanup_remove image "$image"
    done
    cleanup_complete=true
    if (( cleanup_failures != 0 )); then
        cleanup_complete=false
        if (( status == 0 )); then status=1; fi
        echo "Owned-resource cleanup incomplete; inspect cleanup.json." >&2
    fi
    if ! persist_cleanup_report; then
        echo "Could not persist cleanup.json; cleanup status is not recorded." >&2
        if (( status == 0 )); then status=1; fi
    fi
    if (( status == 0 )) && [[ "$run_complete" == true ]]; then
        echo "Completed private Native-only cost protocol: $directory/result.json"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for ref in "$baseline" "$current" "$harness"; do git cat-file -e "${ref}^{commit}"; done
git cat-file -e "$harness:scripts/english-profile-benchmark.py"
for name in "$step"; do
    if container inspect "$name" >/dev/null 2>&1; then
        echo "Refusing a pre-existing container identity." >&2
        exit 1
    fi
done
for image in "$image22" "$image23"; do
    if container image inspect "$image" >/dev/null 2>&1; then
        echo "Refusing a pre-existing image identity." >&2
        exit 1
    fi
done

jq -n --arg baseline "$baseline" --arg current "$current" --arg harness "$harness" \
    --arg run_id "$run_id" --arg profile "$profile" --arg db_image "$database_image" \
    --arg architecture "$(uname -m)" --argjson repetitions "$repetitions" '{
    format:"pgag-english-cost-build-v1",baseline_source_sha:$baseline,current_source_sha:$current,
    harness_commit:$harness,run_id:$run_id,profile:$profile,repetitions:$repetitions,
    postgres_image:$db_image,host_architecture:$architecture,
    serial:true,source_overlays:false,model_calls_authorized:false,
    cluster_lifecycle:"fresh_per_repetition",maximum_concurrent_database_containers:1,
    resources:{database_cpus:2,database_memory_mib:2048,client_cpus:2,client_memory_mib:2048}
}' >"$directory/build-identity.json"
container --version >"$directory/container-version.txt"

build_source() {
    local version="$1" ref="$2" image="$3"
    local context="$directory/source-$version"
    mkdir -m 700 "$context"
    git archive "$ref" | tar -x -C "$context"
    mkdir -m 700 "$context/benchmark"
    cp "$context/.dockerignore" "$context/benchmark/original.dockerignore"
    # This private derived test context needs the archived test scripts and the
    # baked harness. Keep credentials/cache exclusions after these allow rules.
    cat >>"$context/.dockerignore" <<'IGNORE'

!scripts/**
!benchmark/
!benchmark/**
!Dockerfile.english-costs
**/__pycache__/
**/*.py[cod]
**/.env
**/.env.*
**/*.pem
**/*.key
IGNORE
    git show "$harness:scripts/english-profile-benchmark.py" \
        >"$context/benchmark/english-profile-benchmark.py"
    local harness_digest
    harness_digest="$(shasum -a 256 "$context/benchmark/english-profile-benchmark.py" | cut -d ' ' -f 1)"
    (
        cd "$context"
        { find src/pg_agmemory -type f; printf '%s\n' pyproject.toml uv.lock Dockerfile; } \
            | LC_ALL=C sort | while IFS= read -r file; do shasum -a 256 "$file"; done
    ) >"$context/benchmark/source-inputs.sha256"
    jq -n --arg source "$ref" --arg harness "$harness" --arg digest "$harness_digest" \
        --argjson version "$version" '{
        source_sha:$source,harness_commit:$harness,harness_sha256:$digest,schema_version:$version
    }' >"$context/benchmark/source-identity.json"
    # The original Dockerfile and archived core remain unchanged. Only the measurement
    # harness is added to a derived test stage, at build time rather than as an overlay.
    cat "$context/Dockerfile" >"$context/Dockerfile.english-costs"
    cat >>"$context/Dockerfile.english-costs" <<'DOCKER'

FROM test AS english-costs
COPY benchmark/ /benchmark/
COPY Dockerfile /app/Dockerfile
CMD ["python", "/benchmark/english-profile-benchmark.py", "--help"]
DOCKER
    (cd "$context" && shasum -a 256 Dockerfile.english-costs .dockerignore) \
        >"$directory/derived-build-inputs-$version.sha256"
    owned_images+=("$image")
    container build --file "$context/Dockerfile.english-costs" \
        --target english-costs --tag "$image" "$context" \
        >"$directory/build-$version.log" 2>&1
}
build_source 22 "$baseline" "$image22"
build_source 23 "$current" "$image23"

admin_password="$(openssl rand -hex 24)"
runtime_password="$(openssl rand -hex 24)"

start_database() {
    local repetition_label="$1"
    db="pgag-en-${run_id}-${repetition_label}-db"
    if container inspect "$db" >/dev/null 2>&1; then
        echo "Refusing a pre-existing repetition database container." >&2
        exit 1
    fi
    own_db=true
    container run -d --name "$db" --cpus 2 --memory 2g \
        -e "POSTGRES_PASSWORD=$admin_password" -e POSTGRES_DB=postgres \
        "$database_image" postgres \
        -c shared_buffers=256MB -c work_mem=16MB -c maintenance_work_mem=128MB \
        -c max_connections=32 -c max_parallel_workers_per_gather=0 -c autovacuum=off \
        -c timezone=UTC -c fsync=on -c synchronous_commit=on -c full_page_writes=on >/dev/null
    for ((attempt=0; attempt<90; attempt++)); do
        if container exec "$db" pg_isready -U postgres -d postgres >/dev/null 2>&1; then break; fi
        sleep 1
    done
    container exec "$db" pg_isready -U postgres -d postgres >/dev/null
    actual_pg="$(container exec "$db" psql -U postgres -d postgres -Atc 'SHOW server_version_num')"
    if [[ "$actual_pg" != 180006 ]]; then
        echo "The pinned database image must contain PostgreSQL 18.6; refusing a substitute." >&2
        exit 1
    fi
    db_host="$(container inspect "$db" | jq -er \
        '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address) | split("/")[0]')"
    container inspect "$db" | jq '[.[] | {
        id:.configuration.id,resources:.configuration.resources,
        image:.configuration.image.reference,platform:.configuration.platform
    }]' >"$directory/$repetition_label/database-container.json"
}

stop_database() {
    container rm --force "$db" >/dev/null
    own_db=false
}

run_phase() {
    local image="$1" command="$2" database="$3" subdirectory="$4" repetition="$5"
    local environment=(-e "PGAG_BENCHMARK_RUN_ID=$run_id")
    if [[ "$command" != report ]]; then
        environment+=(
            -e "PGAG_BENCHMARK_ADMIN_URL=postgresql://postgres:${admin_password}@${db_host}:5432/${database}"
            -e "PGAG_BENCHMARK_RUNTIME_PASSWORD=$runtime_password"
        )
    fi
    printf '{"status":"started","phase":"%s","repetition":%d}\n' "$command" "$repetition" \
        >>"$directory/phases.log"
    own_step=true
    if container run --rm --name "$step" --cpus 2 --memory 2g \
        -v "$directory:/artifacts" \
        "${environment[@]}" \
        "$image" python /benchmark/english-profile-benchmark.py "$command" \
        --directory "$subdirectory" --profile "$profile" --repetition "$repetition" \
        >>"$directory/phases.log" 2>&1; then
        own_step=false
    else
        local status=$?
        printf '{"status":"failed","code":"container_phase_failed","phase":"%s","repetition":%d,"exit_status":%d}\n' \
            "$command" "$repetition" "$status" >>"$directory/phases.log"
        return "$status"
    fi
}

for ((repetition=1; repetition<=repetitions; repetition++)); do
    label="$(printf 'r%02d' "$repetition")"
    mkdir -m 700 "$directory/$label"
    # Migration 001 creates the cluster-global pgag_runtime role unconditionally.
    # Fresh clusters preserve that immutable migration and avoid silently reusing roles.
    start_database "$label"
    seed_db="pgag_en_${run_id}_${label}_seed"
    base_db="pgag_en_${run_id}_${label}_base"
    new_db="pgag_en_${run_id}_${label}_new"
    container exec "$db" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
        -c "CREATE DATABASE \"$seed_db\"" >/dev/null
    run_phase "$image22" seed "$seed_db" "/artifacts/$label" "$repetition"
    # Every API/client process exits before cloning or upgrading. A cloned seed
    # gives the paired arms identical IDs, content, timestamps and retained history.
    container exec "$db" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
        -c "CREATE DATABASE \"$base_db\" WITH TEMPLATE \"$seed_db\"" \
        -c "CREATE DATABASE \"$new_db\" WITH TEMPLATE \"$seed_db\"" >/dev/null
    if (( repetition % 2 )); then
        run_phase "$image22" measure "$base_db" "/artifacts/$label" "$repetition"
        run_phase "$image23" upgrade "$new_db" "/artifacts/$label" "$repetition"
        run_phase "$image23" measure "$new_db" "/artifacts/$label" "$repetition"
    else
        run_phase "$image23" upgrade "$new_db" "/artifacts/$label" "$repetition"
        run_phase "$image23" measure "$new_db" "/artifacts/$label" "$repetition"
        run_phase "$image22" measure "$base_db" "/artifacts/$label" "$repetition"
    fi
    for database in "$seed_db" "$base_db" "$new_db"; do
        container exec "$db" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
            -c "DROP DATABASE \"$database\"" >/dev/null
    done
    stop_database
done
run_phase "$image23" report unused /artifacts 1
run_complete=true
