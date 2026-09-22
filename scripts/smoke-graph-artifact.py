"""Packaged, non-root graph artifact smoke using a fresh synthetic tenant.

Run with a writable working directory and the existing admin/runtime database
environment variables. Artifact files are private, local, and always removed.
No artifact contents, subprocess diagnostics, or credentials are printed.
"""

import asyncio
import hashlib
import json
import os
import platform
import stat
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import psycopg

import pg_agmemory
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import CreateEntity, CreateRelation, Evidence, Observe, ReviseRelation
from pg_agmemory.service import MemoryError as ServiceMemoryError
from pg_agmemory.service import MemoryService, bind_identity, principal_connection

FORMAT = "pgag-graph-artifact-v1"
PROFILE_DIGEST = hashlib.sha256(b"synthetic-graph-artifact-smoke-profile-v1").hexdigest()
LABELS = tuple(f"SYNTHETIC_GRAPH_ARTIFACT_{name}" for name in ("ALPHA", "BETA", "GAMMA"))
CONTENT = "Synthetic graph evidence only: " + ", ".join(LABELS)
VALID_FROM = datetime(2026, 9, 1, tzinfo=UTC)
VALID_SPLIT = datetime(2026, 9, 4, tzinfo=UTC)


class SmokeError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise SmokeError(code)


def cli(arguments, *, request=None, error_code=None, expect_failure=False):
    result = subprocess.run(
        ["pg-agmemory", *arguments],
        input="" if request is None else json.dumps(request),
        capture_output=True, text=True, timeout=60, check=False,
    )
    require(result.returncode == (1 if expect_failure else 0), "cli_exit_status")
    require(not result.stderr.strip(), "unexpected_cli_diagnostics")
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise SmokeError("invalid_cli_json") from None
    require(isinstance(value, dict), "invalid_cli_result")
    if expect_failure:
        error = value.get("error")
        require(isinstance(error, dict) and isinstance(error.get("code"), str),
                "missing_cli_error")
        if error_code is not None:
            require(error["code"] == error_code, "unexpected_cli_error")
    else:
        require("error" not in value, "unexpected_cli_error")
    return value


def generation(tenant, operation="get", **fields):
    value = cli(["graph-generation"], request={
        "operation": operation, "tenant_id": tenant, **fields,
    })
    require(value["operation"] == operation and value["tenant_id"] == tenant,
            "generation_identity")
    require(value["artifact_verified"] is False and value["serving_enabled"] is False,
            "generation_not_verification")
    return value


def artifact(tenant, generation_id, revision, operation, path, **failure):
    value = cli([
        "graph-artifact", operation, "--tenant-id", tenant,
        "--generation-id", generation_id, "--expected-revision", str(revision),
        "--file", str(path),
    ], **failure)
    if not failure.get("expect_failure"):
        require(
            value["operation"] == operation and value["tenant_id"] == tenant
            and value["generation_id"] == generation_id and value["revision"] == revision,
            "artifact_receipt_identity",
        )
        require(value["artifact_verified"] is True and value["serving_enabled"] is False,
                "artifact_not_serving")
        require(value["node_count"] == 3 and value["edge_revision_count"] == 2,
                "artifact_receipt_counts")
    return value


@asynccontextmanager
async def service(subject, provisioned):
    async with principal_connection(os.environ["PGAG_DATABASE_URL"], subject) as (conn, identity):
        require(
            str(identity.tenant_id) == provisioned["tenant_id"]
            and str(identity.principal_id) == provisioned["principal_id"],
            "runtime_identity",
        )
        async with conn.transaction():
            await bind_identity(conn, subject, identity)
            yield MemoryService(conn, identity)


async def seed(subject, provisioned):
    scope = UUID(provisioned["scope_id"])
    async with service(subject, provisioned) as memory:
        source = await memory.observe(Observe(
            scope_id=scope, source_namespace=subject, source_event_id="graph-fixture-v1",
            occurred_at=VALID_FROM, content=CONTENT,
            consent_reference="synthetic-graph-artifact-smoke-only",
        ), "observe-fixture")
        evidence = [Evidence(memory_id=UUID(source["memory_id"]), quote=CONTENT)]
        graph = SqlGraph(memory)
        nodes = []
        for index, label in enumerate(LABELS):
            node = await graph.create_entity(CreateEntity(
                scope_id=scope, entity_type="component", canonical_label=label,
                evidence=evidence, explicit_intent=True,
            ), f"create-node-{index}")
            require(node["revision"] == 1, "entity_revision")
            nodes.append(node["memory_id"])
        edge = await graph.create_relation(CreateRelation(
            scope_id=scope, source_entity=UUID(nodes[0]), target_entity=UUID(nodes[1]),
            predicate="depends_on", evidence=evidence, explicit_intent=True,
            valid_from=VALID_FROM, valid_to=VALID_SPLIT,
        ), "create-relation")
        require(edge["revision"] == 1, "initial_relation_revision")
    async with service(subject, provisioned) as memory:
        revised = await SqlGraph(memory).revise_relation(UUID(edge["memory_id"]), ReviseRelation(
            expected_revision=1, target_entity=UUID(nodes[2]), evidence=evidence,
            explicit_intent=True, valid_from=VALID_SPLIT,
            reason="Synthetic target correction",
        ), "revise-relation")
        require(revised["memory_id"] == edge["memory_id"] and revised["revision"] == 2,
                "revised_relation_identity")
    return nodes, edge["memory_id"], evidence


def timestamp(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, "artifact_timestamp_timezone")
    return result


def check_payload(raw, provisioned, generation_id, pending, nodes, edge):
    payload = json.loads(raw)
    require(payload["format"] == FORMAT and payload["schema_version"] == 20,
            "artifact_format")
    require(
        payload["tenant_id"] == provisioned["tenant_id"]
        and payload["generation_id"] == generation_id and payload["parent_id"] is None,
        "artifact_payload_identity",
    )
    require(
        payload["input_snapshot"] == pending["building"]["input_snapshot"]
        and payload["input_digest"] == pending["current_input_digest"]
        and payload["profile_digest"] == PROFILE_DIGEST,
        "artifact_input_binding",
    )
    require(all(label.encode() not in raw for label in LABELS) and CONTENT.encode() not in raw,
            "artifact_content_leak")
    forbidden = {"canonical_label", "label", "content", "quote", "evidence", "reason", "text"}

    def content_free(value):
        if isinstance(value, dict):
            require(not forbidden.intersection(value), "artifact_content_field")
            for child in value.values():
                content_free(child)
        elif isinstance(value, list):
            for child in value:
                content_free(child)

    content_free(payload)
    node_rows = payload["nodes"]
    require(len(node_rows) == 3 and [row["memory_id"] for row in node_rows] == sorted(nodes),
            "artifact_nodes")
    for row in node_rows:
        require(set(row) == {"memory_id", "scope_id", "recorded_at"}, "artifact_node_fields")
        require(row["scope_id"] == provisioned["scope_id"], "artifact_node_scope")
        timestamp(row["recorded_at"])
    edges = payload["edge_revisions"]
    require(len(edges) == 2, "artifact_complete_history")
    for revision, row in enumerate(edges, start=1):
        require(set(row) == {
            "memory_id", "revision", "scope_id", "source_entity", "target_entity", "predicate",
            "valid_from", "valid_to", "recorded_at", "superseded_at",
        }, "artifact_edge_fields")
        require(
            row["memory_id"] == edge and row["revision"] == revision
            and row["scope_id"] == provisioned["scope_id"] and row["source_entity"] == nodes[0]
            and row["target_entity"] == nodes[revision] and row["predicate"] == "depends_on",
            "artifact_revision_target_history",
        )
        require(timestamp(row["valid_from"]) == (VALID_FROM if revision == 1 else VALID_SPLIT),
                "artifact_valid_from")
    require(timestamp(edges[0]["valid_to"]) == VALID_SPLIT and edges[1]["valid_to"] is None,
            "artifact_valid_to")
    require(
        timestamp(edges[0]["recorded_at"]) < timestamp(edges[1]["recorded_at"])
        and timestamp(edges[0]["superseded_at"]) == timestamp(edges[1]["recorded_at"])
        and edges[1]["superseded_at"] is None,
        "artifact_system_history",
    )


def private_bytes(path):
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_uid == os.geteuid(), "artifact_file_not_private")
    return path.read_bytes()


def ledger_identity(value):
    return {
        "revision": value["revision"],
        **{key: (
            {name: field for name, field in value[key].items() if name != "source_matches"}
            if value[key] is not None else None
        ) for key in ("head", "building")},
    }


async def smoke(directory):
    subject = f"synthetic-graph-artifact-{uuid4()}"
    provisioned = cli(["provision", "--subject", subject])
    for key in ("tenant_id", "principal_id", "scope_id"):
        UUID(provisioned[key])
    tenant = provisioned["tenant_id"]
    nodes, edge, evidence = await seed(subject, provisioned)
    initial = generation(tenant)
    require(initial["revision"] == 0 and initial["head"] is None and initial["building"] is None,
            "fresh_generation_ledger")
    generation_id = str(uuid4())
    pending = generation(
        tenant, "begin", expected_revision=0, generation_id=generation_id,
        expected_input_digest=initial["current_input_digest"], profile_digest=PROFILE_DIGEST,
    )
    require(pending["revision"] == 1 and pending["head"] is None
            and pending["building"]["id"] == generation_id
            and pending["building"]["state"] == "building", "pending_generation")
    before_export = generation(tenant)
    first = directory / "graph.json"
    exported = artifact(tenant, generation_id, 1, "export", first)
    raw = private_bytes(first)
    check_payload(raw, provisioned, generation_id, pending, nodes, edge)
    checked = artifact(tenant, generation_id, 1, "check", first)
    require(checked["artifact_digest"] == exported["artifact_digest"], "checked_digest")
    require(generation(tenant) == before_export, "artifact_export_changed_ledger")
    recorded = generation(
        tenant, "record", expected_revision=1, generation_id=generation_id,
        expected_input_digest=pending["current_input_digest"],
        artifact_digest=exported["artifact_digest"],
    )
    require(
        recorded["revision"] == 2 and recorded["building"] is None
        and recorded["head"]["id"] == generation_id and recorded["head"]["state"] == "recorded"
        and recorded["head"]["artifact_digest"] == exported["artifact_digest"],
        "recorded_generation",
    )
    baseline = generation(tenant)
    rebuilt_path = directory / "rebuilt.json"
    rebuilt = artifact(tenant, generation_id, 2, "export", rebuilt_path)
    require(private_bytes(rebuilt_path) == raw
            and rebuilt["artifact_digest"] == exported["artifact_digest"], "rebuild_not_exact")
    for path in (first, rebuilt_path):
        checked = artifact(tenant, generation_id, 2, "check", path)
        require(checked["artifact_digest"] == exported["artifact_digest"], "rebuild_check_digest")
    artifact(tenant, generation_id, 2, "export", first, expect_failure=True)
    require(private_bytes(first) == raw and private_bytes(rebuilt_path) == raw,
            "existing_artifact_overwritten")
    require(generation(tenant) == baseline, "artifact_rebuild_changed_ledger")
    async with service(subject, provisioned) as memory:
        mutation = await SqlGraph(memory).create_relation(CreateRelation(
            scope_id=UUID(provisioned["scope_id"]),
            source_entity=UUID(nodes[1]), target_entity=UUID(nodes[2]),
            predicate="part_of", evidence=evidence, explicit_intent=True,
        ), "mutate-canonical-graph")
        require(mutation["revision"] == 1 and mutation["memory_id"] != edge,
                "canonical_mutation")
    artifact(
        tenant, generation_id, 2, "check", first,
        expect_failure=True, error_code="graph_input_changed",
    )
    after = generation(tenant)
    require(ledger_identity(after) == ledger_identity(baseline), "mutation_changed_ledger")
    require(after["head"]["source_matches"] is False
            and after["current_input_digest"] != baseline["current_input_digest"],
            "canonical_mutation_not_detected")
    require(private_bytes(first) == raw and private_bytes(rebuilt_path) == raw,
            "stale_check_changed_artifact")
    return {
        "status": "passed", "artifact_format": FORMAT, "schema_version": 20,
        "node_count": 3, "edge_revision_count": 2, "bytes": len(raw),
        "exact_rebuild": True, "source_mutation_refused": True, "serving_enabled": False,
    }


def main():
    try:
        require(platform.system() == "Linux" and os.geteuid() != 0, "nonroot_linux_required")
        require("site-packages" in Path(pg_agmemory.__file__).parts, "packaged_runtime_required")
        require(all(os.environ.get(key, "").strip()
                    for key in ("PGAG_ADMIN_DATABASE_URL", "PGAG_DATABASE_URL")),
                "database_environment_required")
        with TemporaryDirectory(prefix=".pgag-graph-artifact-", dir=".") as directory:
            result = asyncio.run(smoke(Path(directory)))
        require(not Path(directory).exists(), "artifact_cleanup")
    except (
        SmokeError, ValueError, TypeError, KeyError, IndexError, OSError,
        subprocess.SubprocessError, psycopg.Error, ServiceMemoryError,
    ) as exc:
        code = str(exc) if isinstance(exc, SmokeError) else "graph_artifact_smoke_failed"
        print(json.dumps({"status": "failed", "error": code}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
