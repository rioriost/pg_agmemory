#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077

usage() {
    echo "Usage: $0 NEW_PRIVATE_PROJECT_DIRECTORY MODEL REASONING_EFFORT --allow-copilot"
    echo "Apple Container only; at most 100 fresh Copilot calls using the selected model."
    echo "Synthetic fixtures only; model-selected purge applies only to this owned disposable DB."
    echo "Uses existing host Copilot authentication without copying credentials into guests."
    echo "No embeddings, background worker, production data, or external effect execution."
}
if [[ "${1:-}" == --help ]]; then usage; exit 0; fi
if [[ $# != 4 || "$4" != --allow-copilot ]]; then usage >&2; exit 2; fi
directory="$1" model="$2" effort="$3"
[[ "$model" =~ ^[a-z0-9][a-z0-9._-]{0,99}$ ]] || exit 2
case "$effort" in low|medium|high|xhigh) ;; *) exit 2 ;; esac
cd -P "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$directory" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ ]] || exit 2
prefix=""
IFS=/ read -r -a components <<< "$directory"
for component in "${components[@]}"; do
    [[ "$component" != . && "$component" != .. ]] || exit 2
    prefix="${prefix:+$prefix/}$component"
    [[ ! -L "$prefix" ]] || exit 2
done
[[ ! -e "$directory" && -d "$(dirname "$directory")" ]] || exit 2
if [[ -n "${PGAG_DATABASE_URL:-}${PGAG_ADMIN_DATABASE_URL:-}${PGHOST:-}${PGSERVICE:-}" ]]; then
    echo external_database_target_forbidden >&2
    exit 2
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    echo frozen_committed_checkout_required >&2
    exit 2
fi
for executable in container node copilot jq; do command -v "$executable" >/dev/null; done
node --test scripts/copilot-eval-bridge.test.mjs
container system status >/dev/null
mkdir -m 700 "$directory"
mkdir -m 700 "$directory/bridge"
run_id="agent-eval-$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
source_revision="$(git rev-parse HEAD)"
db="${run_id}-db"
api="${run_id}-api"
image="pg-agmemory-agent-eval:${run_id}"
postgres_image="docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
containers=()
image_owned=false
bridge_pid=""
failure_code=evaluation_incomplete
cleanup() {
    local result=$? name cleanup_failed=false
    trap - EXIT INT TERM
    set +e
    if [[ -n "$bridge_pid" ]]; then
        if kill -0 "$bridge_pid" 2>/dev/null; then
            (set -o noclobber; printf '{"run_id":"%s"}\n' "$run_id" \
                > "$directory/bridge/stop.json") || cleanup_failed=true
            kill -TERM "$bridge_pid" 2>/dev/null
        fi
        wait "$bridge_pid"
    fi
    for name in ${containers[@]+"${containers[@]}"}; do
        container rm --force "$name" >/dev/null 2>&1 || cleanup_failed=true
    done
    if [[ "$image_owned" == true ]]; then
        container image rm "$image" >/dev/null 2>&1 || cleanup_failed=true
    fi
    rm -f "$directory/credentials.env" "$directory/jwt-private.pem" || cleanup_failed=true
    if [[ "$cleanup_failed" == true ]]; then result=1; failure_code=owned_cleanup_failed; fi
    jq -n --arg run "$run_id" --arg revision "$source_revision" \
        --arg failure "$failure_code" --argjson success "$([[ $result == 0 ]] && echo true || echo false)" \
        '{format:"pgag-agent-eval-harness-v1",run_id:$run,source_revision:$revision,
          completed:$success,failure_code:(if $success then null else $failure end),
          production_qualified:false,external_effects_executed:false}' \
        > "$directory/harness.json" || result=1
    exit "$result"
}
trap cleanup EXIT
trap 'failure_code=interrupted; exit 130' INT
trap 'failure_code=terminated; exit 143' TERM

failure_code=runtime_build_failed
image_owned=true
container build --target runtime --tag "$image" . > "$directory/build.log" 2>&1
password="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
runtime_password="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
printf 'POSTGRES_PASSWORD=%s\n' "$password" > "$directory/credentials.env"
failure_code=database_start_failed
containers+=("$db")
container run -d --name "$db" --env-file "$directory/credentials.env" \
    -e POSTGRES_DB=pgag_agent_eval "$postgres_image" >/dev/null
for ((attempt=0; attempt<90; attempt++)); do
    if container exec "$db" pg_isready -t 1 -U postgres -d pgag_agent_eval >/dev/null 2>&1; then break; fi
    sleep 1
done
container exec "$db" pg_isready -t 1 -U postgres -d pgag_agent_eval >/dev/null
db_host="$(container inspect "$db" | jq -er '.[0].networks[0].address | split("/")[0]')"
admin_url="postgresql://postgres:${password}@${db_host}:5432/pgag_agent_eval"
printf 'PGAG_ADMIN_DATABASE_URL=%s\n' "$admin_url" >> "$directory/credentials.env"
failure_code=migration_failed
containers+=("${run_id}-migrate")
container run --name "${run_id}-migrate" --env-file "$directory/credentials.env" \
    "$image" pg-agmemory migrate >/dev/null
container exec "$db" psql -U postgres -d pgag_agent_eval -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE pgag_agent_eval_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD '${runtime_password}' IN ROLE pgag_runtime;" \
    >/dev/null
failure_code=key_generation_failed
containers+=("${run_id}-keys")
container run --name "${run_id}-keys" --user "$(id -u):$(id -g)" \
    -v "$PWD/$directory:/drill" "$image" python -c '
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
os.umask(0o077)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
Path("/drill/jwt-private.pem").write_bytes(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()))
Path("/drill/jwt-public.pem").write_bytes(key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
'
public_key="$(<"$directory/jwt-public.pem")"
failure_code=api_start_failed
containers+=("$api")
container run -d --name "$api" \
    -e "PGAG_DATABASE_URL=postgresql://pgag_agent_eval_runtime:${runtime_password}@${db_host}:5432/pgag_agent_eval" \
    -e "PGAG_JWT_PUBLIC_KEY=$public_key" -e "PGAG_JWT_ISSUER=$run_id" \
    -e "PGAG_JWT_AUDIENCE=$run_id" "$image" >/dev/null
api_host="$(container inspect "$api" | jq -er '.[0].networks[0].address | split("/")[0]')"
failure_code=owned_configuration_failed
containers+=("${run_id}-configuration")
container run --name "${run_id}-configuration" --user "$(id -u):$(id -g)" \
    --env-file "$directory/credentials.env" -v "$PWD/$directory:/drill" \
    -e "PGAG_AGENT_EVAL_OWNED_RUN=$run_id" -e "PGAG_AGENT_EVAL_API_URL=http://${api_host}:8000" \
    "$image" python -c '
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
os.umask(0o077)
url = os.environ["PGAG_AGENT_EVAL_API_URL"]
for attempt in range(60):
    try:
        with urllib.request.urlopen(url + "/readyz", timeout=2) as response:
            assert json.load(response) == {"status":"ready"}
        break
    except (OSError, AssertionError):
        if attempt == 59:
            raise
        time.sleep(1)
run = os.environ["PGAG_AGENT_EVAL_OWNED_RUN"]
Path("/drill/config.json").write_text(json.dumps({
    "format":"pgag-agent-eval-owned-v1","run_id":run,"api_url":url,
    "admin_url_hash":hashlib.sha256(
        os.environ["PGAG_ADMIN_DATABASE_URL"].encode()).hexdigest(),
    "jwt_issuer":run,"jwt_audience":run,"synthetic_fixture_purge_consent":True,
}))
'
failure_code=copilot_bridge_start_failed
node scripts/copilot-eval-bridge.mjs --directory "$PWD/$directory/bridge" \
    --model "$model" --reasoning-effort "$effort" --max-calls 100 --run-id "$run_id" \
    > "$directory/bridge.log" 2>&1 &
bridge_pid=$!
for ((attempt=0; attempt<30; attempt++)); do
    kill -0 "$bridge_pid"
    [[ ! -f "$directory/bridge/transport.json" ]] || break
    sleep 1
done
[[ -f "$directory/bridge/transport.json" ]]
failure_code=agent_evaluation_failed
containers+=("${run_id}-runner")
container run --name "${run_id}-runner" --user "$(id -u):$(id -g)" \
    --env-file "$directory/credentials.env" \
    -v "$PWD:/work:ro" -v "$PWD/$directory:/drill" -v "$PWD/$directory/bridge:/bridge" \
    -e "PGAG_AGENT_EVAL_OWNED_RUN=$run_id" \
    -e PGAG_AGENT_EVAL_OWNED_CONFIG=/drill/config.json \
    -e "PGAG_AGENT_EVAL_API_URL=http://${api_host}:8000" \
    -e PGAG_AGENT_EVAL_JWT_PRIVATE_KEY_FILE=/drill/jwt-private.pem \
    -e "PGAG_AGENT_EVAL_SOURCE_REVISION=$source_revision" \
    -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/work/src \
    "$image" timeout 2400s python /work/scripts/evaluate-agent-memory.py \
        --output /drill/results --bridge /bridge > "$directory/runner.log" 2>&1
failure_code=bridge_shutdown_failed
printf '{"run_id":"%s"}\n' "$run_id" > "$directory/bridge/stop.json"
wait "$bridge_pid"
bridge_pid=""
failure_code=evaluation_incomplete
echo "Synthetic agent evaluation completed; inspect private results and failure counts."
