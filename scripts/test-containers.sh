#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    echo "Usage: $0 [container|docker]"
    echo "Build and run lint, types, PostgreSQL tests, and SDK/vector/capture/API/worker/MCP/hook smokes."
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
command -v jq >/dev/null 2>&1 || {
    echo "jq is required to read container addresses and disposable smoke credentials." >&2
    exit 1
}
if [[ "$engine" == "container" ]]; then
    container system status >/dev/null
else
    docker info >/dev/null
fi

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# This multiarchitecture manifest includes native linux/amd64 and linux/arm64.
postgres_image="docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
run_id="pgag-$(date +%s)-$$-${RANDOM}-${RANDOM}"
test_image="pg-agmemory-test:${run_id}"
runtime_image="pg-agmemory-runtime:${run_id}"
extras_image="pg-agmemory-extras:${run_id}"
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
    "$engine" image rm "$test_image" "$runtime_image" "$extras_image" >/dev/null 2>&1
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
            test "$("$engine" exec "$1" psql -U postgres -d pgag_test -Atc \
                'SHOW server_version_num')" = 180006
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
"$engine" build --target adapter-extras-check --tag "$extras_image" .
"$engine" build --target runtime --tag "$runtime_image" .

# A second fresh cluster prevents test-created roles or schemas masking migration bugs.
start_database "$smoke_db"
smoke_host="$(container_host "$smoke_db")"
"$engine" run --name "$migrate_name" --network "$network" \
    -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:${password}@${smoke_host}:5432/pgag_test" \
    "$runtime_image" pg-agmemory migrate
"$engine" exec "$smoke_db" psql -U postgres -d pgag_test -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE pgag_smoke LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD '${runtime_password}' IN ROLE pgag_runtime;"

key_bundle="$("$engine" run --name "$key_name" --network "$network" "$runtime_image" python -c '
import importlib.util
import json
import os
import sys
import time
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pg_agmemory.lexical import segment

assert os.geteuid() != 0, "Production image must not run as root"
for package in ("pytest", "ruff", "mypy"):
    assert importlib.util.find_spec(package) is None, f"Development dependency in runtime: {package}"
assert segment("\u6771\u4eac\u90fd").split() == ["\u6771\u4eac", "\u90fd"]
print("Production Japanese tokenizer smoke passed", file=sys.stderr)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
now = int(time.time())
token = jwt.encode({
    "sub": sys.argv[1], "iss": "pgag-container-smoke", "aud": "pgag-container-smoke",
    "iat": now, "exp": now + 600,
}, key, algorithm="RS256")
print(json.dumps({
    "public_key": key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode(),
    "token": token,
}))
' "${run_id}-worker")"
public_key="$(printf '%s' "$key_bundle" | jq -er .public_key)"
mcp_token="$(printf '%s' "$key_bundle" | jq -er .token)"
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

provisioned="$("$engine" run --name "$provision_name" --network "$network" \
    -e "PGAG_ADMIN_DATABASE_URL=postgresql://postgres:${password}@${smoke_host}:5432/pgag_test" \
    "$runtime_image" pg-agmemory provision --subject "${run_id}-worker")"
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

scope_id="$(printf '%s' "$provisioned" | jq -er .scope_id)"
"$engine" exec -e "PGAG_MCP_API_TOKEN=$mcp_token" "$api_name" python -c '
import asyncio
import os
import sys
from mcp import Client
from mcp.client.stdio import StdioServerParameters

async def smoke():
    parameters = StdioServerParameters(
        command="pg-agmemory", args=["mcp"],
        env={"PGAG_MCP_API_URL": "http://127.0.0.1:8000",
             "PGAG_MCP_API_TOKEN": os.environ["PGAG_MCP_API_TOKEN"]},
    )
    for mode, version in [("auto", "2026-07-28"), ("legacy", "2025-11-25")]:
        async with Client(parameters, mode=mode, read_timeout_seconds=20) as client:
            assert client.protocol_version == version
            assert [tool.name for tool in (await client.list_tools()).tools] == [
                "memory_recall", "memory_remember", "memory_explain", "memory_forget"
            ]
            result = await client.call_tool("memory_recall", {
                "request": {"scope_ids": [sys.argv[1]], "purpose": "production smoke"}
            })
            assert not result.is_error and result.structured_content["result"]["items"] == []
        print("Production MCP stdio smoke passed: " + version)

asyncio.run(smoke())
' "$scope_id"

"$engine" exec -e "PGAG_HOOK_API_TOKEN=$mcp_token" "$api_name" python -c '
import json
import os
import subprocess
import sys

settings = {
    "PATH": os.environ["PATH"],
    "PGAG_HOOK_API_URL": "http://127.0.0.1:8000",
    "PGAG_HOOK_API_TOKEN": os.environ["PGAG_HOOK_API_TOKEN"],
    "PGAG_HOOK_SCOPE_IDS": json.dumps([sys.argv[1]]),
}
for event in ("session_start", "task_switch", "after_compaction"):
    process = subprocess.run(
        ["pg-agmemory", "recall-hook"], env=settings,
        input=json.dumps({"event": event, "query": ""}),
        capture_output=True, text=True, timeout=20,
    )
    assert process.returncode == 0 and process.stderr == "", "Hook smoke failed"
    output = json.loads(process.stdout)
    assert output["status"] == "ok" and output["event"] == event and output["error"] is None
    assert output["result"]["items"] == [] and output["result"]["empty_reason"] == "not_found"
    assert output["result"]["context_pack"]["byte_count"] <= 2000
    print("Production implicit recall hook smoke passed: " + event)
' "$scope_id"

"$engine" exec -e "PGAG_CAPTURE_API_TOKEN=$mcp_token" "$api_name" python -c '
import json
import os
import subprocess
import sys
import httpx

with httpx.Client(
    base_url="http://127.0.0.1:8000", timeout=10, trust_env=False,
    headers={"Authorization": "Bearer " + os.environ["PGAG_CAPTURE_API_TOKEN"]},
) as api:
    body = {
        "episode": {
            "scope_id": sys.argv[1], "source_namespace": "production-smoke",
            "source_event_id": "atomic-capture", "occurred_at": "2026-09-01T00:00:00Z",
            "content": "Synthetic ACME contract is Gold", "consent_reference": "synthetic-smoke",
        },
        "memory": {
            "subject": "ACME", "predicate": "contract_tier", "value": "Gold",
            "evidence_quote": "Gold", "explicit_intent": True,
        },
    }
    headers = {"Idempotency-Key": "production-capture"}
    created = api.post("/v1/captures", json=body, headers=headers)
    assert created.status_code == 201, "Atomic capture failed"
    pair = created.json()
    pending = api.get("/v1/jobs/" + pair["synthesis_job_id"])
    assert pending.status_code == 200 and pending.json()["state"] == "pending"
    worker = subprocess.run(
        ["pg-agmemory", "worker", "--subject", sys.argv[2], "--once"],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "PGAG_DATABASE_URL": os.environ["PGAG_DATABASE_URL"]},
    )
    assert worker.returncode == 0, "Capture worker failed"
    outcome = json.loads(worker.stdout)
    assert outcome["outcome"] == "succeeded" and outcome["job_id"] == pair["synthesis_job_id"]
    replay = api.post("/v1/captures", json=body, headers=headers)
    assert replay.status_code == 201 and replay.json() == pair
    recalled = api.post("/v1/recall", json={
        "scope_ids": [sys.argv[1]], "query": "Gold", "purpose": "production smoke",
    })
    assert recalled.status_code == 200
    assert {item["memory_id"] for item in recalled.json()["items"]} == {
        pair["memory_id"], outcome["result"]["memory_id"],
    }
    erased = api.post("/v1/forget", json={
        "memory_ids": [pair["memory_id"]], "mode": "purge", "reason": "synthetic smoke",
    }, headers={"Idempotency-Key": "production-capture-purge"})
    assert erased.status_code == 202
    assert api.get("/v1/jobs/" + pair["synthesis_job_id"]).status_code == 404
    assert api.post("/v1/captures", json=body, headers=headers).status_code == 404
print("Production atomic capture smoke passed: capture, worker, recall, replay, purge")
' "$scope_id" "${run_id}-worker"

"$engine" exec -e "PGAG_VECTOR_API_TOKEN=$mcp_token" "$api_name" python -c '
import hashlib
import os
import sys
import httpx

model = {"name": "synthetic-basis", "revision": "production-v1"}
basis = [1, 0] + [0] * 766
with httpx.Client(
    base_url="http://127.0.0.1:8000", timeout=10, trust_env=False,
    headers={"Authorization": "Bearer " + os.environ["PGAG_VECTOR_API_TOKEN"]},
) as api:
    observed = api.post("/v1/observe", json={
        "scope_id": sys.argv[1], "source_namespace": "production-smoke",
        "source_event_id": "vector-smoke", "occurred_at": "2026-09-01T00:00:00Z",
        "content": "Synthetic Gold", "consent_reference": "synthetic-smoke",
    }, headers={"Idempotency-Key": "vector-source"})
    assert observed.status_code == 201
    source = observed.json()["memory_id"]
    saved = api.post("/v1/remember", json={
        "scope_id": sys.argv[1], "subject": "ACME", "predicate": "contract_tier", "value": "Gold",
        "explicit_intent": True, "evidence": [{"memory_id": source, "quote": "Gold"}],
    }, headers={"Idempotency-Key": "vector-assertion"})
    assert saved.status_code == 201
    assertion = saved.json()["memory_id"]
    for memory_id, values in [(source, basis), (assertion, [0, 1] + [0] * 766)]:
        prepared = api.post("/v1/embedding-inputs", json={"memory_id": memory_id})
        assert prepared.status_code == 200
        canonical = prepared.json()
        assert canonical["input_digest"] == hashlib.sha256(canonical["text"].encode()).hexdigest()
        body = {"memory_id": memory_id, "input_digest": canonical["input_digest"],
                "model": model, "values": values}
        assert api.post("/v1/embeddings", json=body,
                        headers={"Idempotency-Key": memory_id}).status_code == 201
    query = {"scope_ids": [sys.argv[1]], "purpose": "synthetic smoke",
             "retrieval_mode": "vector", "vector_query": {"model": model, "values": basis}}
    exact = api.post("/v1/recall", json=query)
    assert exact.status_code == 200
    assert [item["memory_id"] for item in exact.json()["items"]] == [source, assertion]
    assert [item["retrieval"]["vector_distance"] for item in exact.json()["items"]] == [0.0, 1.0]
    assert exact.json()["coverage"]["retrieval_complete"]
    hybrid = api.post("/v1/recall", json={**query, "query": "Gold", "retrieval_mode": "hybrid"})
    assert hybrid.status_code == 200
    assert {item["memory_id"] for item in hybrid.json()["items"]} == {source, assertion}
    assert all(item["retrieval"]["method"] == "rrf-60" for item in hybrid.json()["items"])
    assert api.post("/v1/forget", json={"memory_ids": [source], "reason": "synthetic smoke"},
                    headers={"Idempotency-Key": "vector-purge"}).status_code == 202
    assert api.post("/v1/embeddings", json=body,
                    headers={"Idempotency-Key": assertion}).status_code == 404
    after = api.post("/v1/recall", json=query).json()
    assert after["items"] == [] and after["coverage"]["retrieval_complete"]
print("Production pgvector smoke passed: exact/hybrid, episode/assertion, purge")
' "$scope_id"

"$engine" exec -e "PGAG_SDK_API_TOKEN=$mcp_token" "$api_name" python -c '
import asyncio
import os
import sys
from datetime import UTC, datetime
from uuid import UUID
from pg_agmemory.models import (
    Capture, CapturedMemory, DeletionPreview, DeletionResult, EmbeddingModel, Explain, Forget,
    Observe, PutEmbedding, Recall, VectorQuery,
)
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

async def smoke():
    async with AsyncMemoryClient("http://127.0.0.1:8000", os.environ["PGAG_SDK_API_TOKEN"]) as sdk:
        scope = UUID(sys.argv[1])
        request = Capture(episode=Observe(
            scope_id=scope, source_namespace="production-sdk", source_event_id="sdk-smoke",
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC), content="Synthetic SDK Gold",
            consent_reference="synthetic-smoke",
        ), memory=CapturedMemory(
            subject="ACME", predicate="tier", value="Gold", evidence_quote="Gold", explicit_intent=True,
        ))
        captured = await sdk.capture(request, idempotency_key="sdk-capture")
        assert await sdk.capture(request, idempotency_key="sdk-capture") == captured
        assert (await sdk.get_job(captured.synthesis_job_id)).state == "pending"
        canonical = await sdk.embedding_input(Explain(memory_id=captured.memory_id))
        model = EmbeddingModel(name="synthetic-sdk", revision="basis-v1")
        values = [1.0] + [0.0] * 767
        await sdk.put_embedding(PutEmbedding(memory_id=captured.memory_id,
            input_digest=canonical.input_digest, model=model, values=values),
            idempotency_key="sdk-embedding")
        recalled = await sdk.recall(Recall(scope_ids=[scope], purpose="synthetic-sdk",
            retrieval_mode="vector", vector_query=VectorQuery(model=model, values=values)))
        assert [item.memory_id for item in recalled.items] == [captured.memory_id]
        assert recalled.coverage.retrieval_complete
        preview = await sdk.forget(Forget(memory_ids=[captured.memory_id],
            mode="preview", reason="synthetic-sdk"), idempotency_key="sdk-preview")
        assert isinstance(preview, DeletionPreview) and preview.object_count == 2
        purged = await sdk.forget(Forget(memory_ids=[captured.memory_id], reason="synthetic-sdk"),
            idempotency_key="sdk-purge")
        assert isinstance(purged, DeletionResult)
        assert (await sdk.get_deletion(purged.deletion_id)).state == "active_store_purged"
        try:
            await sdk.capture(request, idempotency_key="sdk-capture")
        except MemoryClientError as exc:
            assert exc.error.code == "not_found" and not exc.error.outcome_unknown
        else:
            raise AssertionError("Purged capture replay accepted")

asyncio.run(smoke())
print("Production Python SDK smoke passed: capture, replay, typed reads, vector recall, purge")
' "$scope_id"

echo "Container tests and production SDK/vector/capture/API/worker/MCP/hook smoke passed ($engine)."
