#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    echo "Usage: $0 [container|docker]"
    echo "Build and run lint, types, PostgreSQL integration tests, and production API/worker smoke tests."
    echo "Defaults to Apple Container; all Python checks run inside Linux containers."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi
engine="${1:-container}"
if [[ $# -gt 1 || ( "$engine" != "container" && "$engine" != "docker" ) ]]; then
    usage >&2
    exit 2
fi
if ! command -v "$engine" >/dev/null 2>&1; then
    echo "Required container engine not found: $engine (there is no host-test fallback)." >&2
    exit 1
fi
if [[ "$engine" == "container" ]]; then
    command -v jq >/dev/null 2>&1 || {
        echo "jq is required to read Apple Container IP addresses." >&2
        exit 1
    }
    container system status >/dev/null
else
    docker info >/dev/null
fi

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# This multiarchitecture manifest includes native linux/amd64 and linux/arm64.
postgres_image="docker.io/library/postgres:18.6-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af"
run_id="pgag-$(date +%s)-$$-${RANDOM}-${RANDOM}"
test_image="pg-agmemory-test:${run_id}"
runtime_image="pg-agmemory-runtime:${run_id}"
test_db="${run_id}-test-db"
smoke_db="${run_id}-smoke-db"
test_name="${run_id}-tests"
migrate_name="${run_id}-migrate"
key_name="${run_id}-key"
api_name="${run_id}-api"
probe_name="${run_id}-probe"
provision_name="${run_id}-provision"
worker_name="${run_id}-worker"
containers=("$worker_name" "$provision_name" "$probe_name" "$api_name" "$key_name"
            "$migrate_name" "$test_name" "$smoke_db" "$test_db")
network=default
network_created=false
password="${run_id}-${RANDOM}"
runtime_password="${password}-runtime"

remove_container() {
    if [[ "$engine" == "docker" ]]; then
        docker rm --force --volumes "$1" >/dev/null 2>&1 || true
    else
        container rm --force "$1" >/dev/null 2>&1 || true
    fi
}

cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    if [[ $status -ne 0 ]]; then
        for name in "$test_name" "$api_name" "$worker_name" "$smoke_db" "$test_db"; do
            "$engine" logs "$name" >&2 2>/dev/null
        done
    fi
    for name in "${containers[@]}"; do
        remove_container "$name"
    done
    if [[ "$network_created" == true ]]; then
        docker network rm "$network" >/dev/null 2>&1
    fi
    "$engine" image rm "$test_image" "$runtime_image" >/dev/null 2>&1
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$engine" == "docker" ]]; then
    network="${run_id}-network"
    docker network create "$network" >/dev/null
    network_created=true
fi

container_host() {
    if [[ "$engine" == "docker" ]]; then
        printf '%s\n' "$1"
    else
        container inspect "$1" |
            jq -er '(.[0].status.networks[0].ipv4Address // .[0].networks[0].ipv4Address)
                | split("/")[0] | select(length > 0)'
    fi
}

start_database() {
    "$engine" run -d --name "$1" --network "$network" \
        --tmpfs /var/lib/postgresql \
        -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=pgag_test \
        "$postgres_image" >/dev/null
    for ((attempt = 0; attempt < 90; attempt++)); do
        if "$engine" exec "$1" pg_isready -h 127.0.0.1 -U postgres -d pgag_test \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "PostgreSQL did not become ready: $1" >&2
    return 1
}

echo "Building test image with $engine..."
"$engine" build --target test --tag "$test_image" .
start_database "$test_db"
test_host="$(container_host "$test_db")"
echo "Running containerized lint, type checks, and PostgreSQL tests..."
"$engine" run --name "$test_name" --network "$network" \
    -e "PGAG_TEST_DATABASE_URL=postgresql://postgres:${password}@${test_host}:5432/pgag_test" \
    "$test_image"
remove_container "$test_db"

echo "Building and running the non-root production image..."
"$engine" build --target runtime --tag "$runtime_image" .

# A second fresh cluster prevents test-created roles or schemas masking migration bugs.
start_database "$smoke_db"
smoke_host="$(container_host "$smoke_db")"
"$engine" run --name "$migrate_name" --network "$network" \
    -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:${password}@${smoke_host}:5432/pgag_test" \
    "$runtime_image" pg-agmemory migrate
"$engine" exec "$smoke_db" psql -U postgres -d pgag_test -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE pgag_smoke LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD '${runtime_password}' IN ROLE pgag_runtime;"

public_key="$("$engine" run --name "$key_name" --network "$network" "$runtime_image" python -c '
import importlib.util
import os
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pg_agmemory.lexical import segment

assert os.geteuid() != 0, "Production image must not run as root"
for package in ("pytest", "ruff", "mypy"):
    assert importlib.util.find_spec(package) is None, f"Development dependency in runtime: {package}"
assert segment("\u6771\u4eac\u90fd").split() == ["\u6771\u4eac", "\u90fd"]
print("Production Japanese tokenizer smoke passed", file=sys.stderr)
print(rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode(), end="")
')"
"$engine" run -d --name "$api_name" --network "$network" \
    -e "PGAG_DATABASE_URL=postgresql://pgag_smoke:${runtime_password}@${smoke_host}:5432/pgag_test" \
    -e "PGAG_JWT_PUBLIC_KEY=$public_key" \
    -e PGAG_JWT_ISSUER=pgag-container-smoke \
    -e PGAG_JWT_AUDIENCE=pgag-container-smoke \
    "$runtime_image" >/dev/null
api_host="$(container_host "$api_name")"
"$engine" run --name "$probe_name" --network "$network" "$runtime_image" \
    python -c '
import json
import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1]
for attempt in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            assert response.status == 200, response.status
            body = json.load(response)
            assert body == {"status": "ok"}, body
        print("Production HTTP smoke passed: unauthenticated GET /healthz -> {status: ok}")
        break
    except (OSError, urllib.error.URLError):
        if attempt == 59:
            raise
        time.sleep(1)
' "http://${api_host}:8000/healthz"

"$engine" run --name "$provision_name" --network "$network" \
    -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:${password}@${smoke_host}:5432/pgag_test" \
    "$runtime_image" pg-agmemory provision --subject "${run_id}-worker" >/dev/null
"$engine" run --name "$worker_name" --network "$network" \
    -e "PGAG_DATABASE_URL=postgresql://pgag_smoke:${runtime_password}@${smoke_host}:5432/pgag_test" \
    "$runtime_image" python -c '
import json
import subprocess
import sys

result = subprocess.run(
    ["pg-agmemory", "worker", "--subject", sys.argv[1], "--once"],
    check=True, stdout=subprocess.PIPE, text=True, timeout=30,
)
assert json.loads(result.stdout) == {"outcome": "idle"}, result.stdout
print("Production worker smoke passed: --once -> {outcome: idle}")
' "${run_id}-worker"

echo "Container tests and production API/worker smoke passed ($engine)."
