"""Real HTTP/CLI smoke of the optional patched AGE profile; synthetic data only."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import CreateRelation, ExpandGraph, GraphResult

spec = importlib.util.spec_from_file_location(
    "artifact_smoke", Path(sys.argv[1]) / "smoke-graph-artifact.py",
)
assert spec is not None and spec.loader is not None
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
require = fixture.require


@contextmanager
def api_server(directory, subject, backend):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    now = int(time.time())
    token = jwt.encode({
        "sub": subject, "iss": "age-enabled-smoke", "aud": "age-enabled-smoke",
        "iat": now, "exp": now + 600,
    }, key, algorithm="RS256")
    environment = {
        name: value for name, value in os.environ.items()
        if name in ("PATH", "LANG", "HOME", "TMPDIR", "SSL_CERT_FILE", "PGAG_DATABASE_URL")
    }
    environment.update(
        PGAG_JWT_PUBLIC_KEY=public, PGAG_JWT_ISSUER="age-enabled-smoke",
        PGAG_JWT_AUDIENCE="age-enabled-smoke", PGAG_GRAPH_BACKEND=backend,
    )
    with (directory / f"{backend}-server.log").open("wb") as log:
        process = subprocess.Popen(
            ["pg-agmemory", "serve"], env=environment, stdout=log, stderr=log,
        )
        try:
            with httpx.Client(
                base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            ) as client:
                for _ in range(100):
                    require(process.poll() is None, "api_startup_failed")
                    try:
                        ready = client.get("/readyz")
                        if ready.status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    raise fixture.SmokeError("api_not_ready")
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


async def smoke(directory):
    subject = "synthetic-age-enabled-" + uuid4().hex
    provisioned = fixture.cli(["provision", "--subject", subject])
    tenant = provisioned["tenant_id"]
    nodes, _, evidence = await fixture.seed(subject, provisioned)
    async with fixture.service(subject, provisioned) as memory:
        await SqlGraph(memory).create_relation(CreateRelation(
            scope_id=UUID(provisioned["scope_id"]), source_entity=UUID(nodes[2]),
            target_entity=UUID(nodes[1]), predicate="part_of",
            evidence=evidence, explicit_intent=True,
        ), "age-two-hop")
    initial = fixture.generation(tenant)
    generation = str(uuid4())
    fixture.generation(
        tenant, "begin", expected_revision=0, generation_id=generation,
        expected_input_digest=initial["current_input_digest"],
        profile_digest=fixture.PROFILE_DIGEST,
    )
    artifact = directory / "graph.json"
    exported = fixture.cli([
        "graph-artifact", "export", "--tenant-id", tenant, "--generation-id", generation,
        "--expected-revision", "1", "--file", str(artifact),
    ])
    require(exported["node_count"] == 3 and exported["edge_revision_count"] == 3,
            "exported_graph_counts")
    fixture.generation(
        tenant, "record", expected_revision=1, generation_id=generation,
        expected_input_digest=initial["current_input_digest"],
        artifact_digest=exported["artifact_digest"],
    )
    publish = [
        "age-projection", "publish", "--tenant-id", tenant, "--generation-id", generation,
        "--expected-generation-revision", "2", "--file", str(artifact),
    ]
    installed = fixture.cli([*publish, "--expected-revision", "0"])
    require(installed["serving_enabled"] and installed["artifact_verified"]
            and installed["revision"] == 1, "publication_not_enabled")
    request = ExpandGraph(
        scope_ids=[UUID(provisioned["scope_id"])], seeds=[UUID(nodes[0])],
        relation_types=["depends_on", "part_of"], purpose="synthetic native AGE HTTP smoke",
        max_hops=2, max_paths=100, as_of=datetime(2026, 9, 5, tzinfo=UTC),
        known_at=datetime.now(UTC),
    )
    async with fixture.service(subject, provisioned) as memory:
        expected = GraphResult.model_validate(await SqlGraph(memory).expand(request)).model_dump(
            mode="json",
        )
    require(len(expected["paths"]) == 2, "sql_two_hop_fixture")
    body = request.model_dump(mode="json")
    with api_server(directory, subject, "age") as client:
        capabilities = client.get("/v1/capabilities")
        require(capabilities.status_code == 200 and capabilities.json()["graph_backend"] == "age",
                "age_capability")
        response = client.post("/v1/graph/expand", json=body)
        require(response.status_code == 200, "native_http_expand")
        require(response.json() == {
            **expected, "backend": "age", "projection_watermark": generation,
        }, "native_sql_path_mismatch")
        disabled = fixture.cli([
            "age-projection", "disable", "--tenant-id", tenant, "--expected-revision", "1",
        ])
        require(disabled["serving_enabled"] is False and disabled["revision"] == 2,
                "disable_failed")
        response = client.post("/v1/graph/expand", json=body)
        require(response.status_code == 409
                and response.json()["code"] == "graph_projection_unavailable",
                "disabled_projection_did_not_fail_closed")
        rebuilt = fixture.cli([*publish, "--expected-revision", "2"])
        require(rebuilt["revision"] == 3 and rebuilt["serving_enabled"], "republication_failed")
        response = client.post("/v1/graph/expand", json=body)
        require(response.status_code == 200 and response.json()["paths"] == expected["paths"],
                "republication_changed_paths")
        mutation = client.post("/v1/relations", json={
            "scope_id": provisioned["scope_id"], "source_entity": nodes[1],
            "target_entity": nodes[0], "predicate": "affects",
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "explicit_intent": True,
        }, headers={"Idempotency-Key": "age-http-mutation"})
        require(mutation.status_code == 201, "canonical_http_mutation")
        current_body = {**body, "relation_types": [*body["relation_types"], "affects"],
                        "known_at": None}
        response = client.post("/v1/graph/expand", json=current_body)
        require(response.status_code == 409
                and response.json()["code"] == "graph_projection_stale",
                "stale_projection_did_not_fail_closed")
    with api_server(directory, subject, "sql") as client:
        response = client.post("/v1/graph/expand", json=current_body)
        require(response.status_code == 200 and response.json()["backend"] == "sql"
                and response.json()["projection_watermark"] is None,
                "explicit_sql_switch_failed")
    return {
        "status": "passed", "backend": "age",
        "age_commit": "72707aab7ce982bf13cad3d102bd869dab07d64b",
        "schema_version": 23, "native_paths": 2, "nodes": 3, "edge_revisions": 3,
        "canonical_sql_equal": True, "disabled_refused": True, "same_head_rebuilt": True,
        "stale_refused": True, "explicit_sql_switch": True,
    }


def main():
    try:
        require(sys.platform == "linux" and os.geteuid() != 0, "nonroot_linux_required")
        with TemporaryDirectory(prefix="pgag-age-enabled-", dir=".") as temporary:
            result = asyncio.run(smoke(Path(temporary).resolve()))
    except (fixture.SmokeError, OSError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError, httpx.HTTPError) as exc:
        print(json.dumps({
            "status": "failed",
            "error": str(exc) if isinstance(exc, fixture.SmokeError) else type(exc).__name__,
        }), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
