"""Independent topology, receipt, privacy, and filesystem contracts; no graph serving."""

import hashlib
import hmac
import json
import os
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError
from test_graph_conformance import GraphFixture, timestamp
from test_graph_generation import abandon, begin, execute, graph_fixture, metadata_rows, record

from pg_agmemory import graph_artifact as artifact
from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access


@pytest.fixture
def artifact_dir():
    directory = Path.cwd() / ".review-artifacts" / f"artifact-tests-{uuid4().hex}"
    directory.mkdir(parents=True, mode=0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


def request(env, reserved, path, operation="export", **changes):
    generation = reserved.building or reserved.head
    return artifact.GraphArtifactRequest(
        **{
            "operation": operation,
            "tenant_id": env.tenants[0],
            "generation_id": generation.id,
            "expected_revision": reserved.revision,
            "file": path,
            **changes,
        }
    )


def run(env, reserved, path, operation="export", *, url=None, **changes):
    with artifact.graph_artifact(
        url or env.admin_url, request(env, reserved, path, operation, **changes)
    ) as result:
        assert result.operation == operation
        assert result.artifact_verified is True
        assert result.serving_enabled is False
        return result


def canonical_bytes(body):
    return json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def signed_body(env, body):
    with psycopg.connect(env.admin_url) as conn:
        key = bytes(conn.execute(
            "SELECT secret FROM memory_ops.recovery_key WHERE tenant_id=%s", (env.tenants[0],)
        ).fetchone()[0])
    unsigned = {key: value for key, value in body.items() if key != "signature"}
    return hmac.new(
        key, b"pgag-graph-artifact-v1:" + canonical_bytes(unsigned) + b"\n", hashlib.sha256
    ).hexdigest()


def database_rows(env):
    """Include tuple xmin so a write-and-restore cannot masquerade as read-only."""
    with psycopg.connect(env.admin_url) as conn:
        tables = conn.execute(
            """SELECT table_schema,table_name FROM information_schema.columns
               WHERE table_schema IN ('memory','memory_ops') AND column_name='tenant_id'
               ORDER BY table_schema,table_name"""
        ).fetchall()
        rows = {
            f"{schema}.{table}": conn.execute(
                sql.SQL(
                    "SELECT xmin::text,to_jsonb(t) FROM {} t WHERE tenant_id=%s "
                    'ORDER BY to_jsonb(t)::text COLLATE "C"'
                ).format(sql.Identifier(schema, table)),
                (env.tenants[0],),
            ).fetchall()
            for schema, table in tables
        }
        rows["tenant"] = conn.execute(
            "SELECT xmin::text,to_jsonb(t) FROM memory.tenant t WHERE id=%s", (env.tenants[0],)
        ).fetchall()
        return rows


def test_strict_wire_request_validation_precedes_database(monkeypatch, artifact_dir):
    body = {
        "operation": "export",
        "tenant_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "expected_revision": 1,
        "file": str(artifact_dir / "artifact.json"),
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid request reached the database")

    monkeypatch.setattr(artifact, "admin_connection", forbidden)
    invalid = [
        body | {"operation": "activate"},
        *(body | {"expected_revision": value} for value in (0, -1, True, "1", MAX_EPOCH + 1)),
        body | {"tenant_id": "not-a-uuid"},
        body | {"generation_id": None},
        body | {"profile_digest": "a" * 64},
        {key: value for key, value in body.items() if key != "file"},
    ]
    for value in invalid:
        with pytest.raises(ValidationError):
            artifact.GraphArtifactRequest.model_validate_json(json.dumps(value))
    assert list(artifact_dir.iterdir()) == []


def test_request_is_frozen_with_exact_revision_bounds(artifact_dir):
    for revision in (1, MAX_EPOCH):
        parsed = artifact.GraphArtifactRequest.model_validate_json(json.dumps({
            "operation": "check",
            "tenant_id": str(uuid4()),
            "generation_id": str(uuid4()),
            "expected_revision": revision,
            "file": str(artifact_dir / "graph.json"),
        }))
        assert parsed.expected_revision == revision
        assert isinstance(parsed.tenant_id, UUID) and isinstance(parsed.file, Path)
        with pytest.raises(ValidationError, match="frozen"):
            parsed.expected_revision = 2


@pytest.mark.parametrize(
    "invalid_file",
    ["PRIVATE_NUL_\x00.json", "PRIVATE_SURROGATE_\ud800.json", "PRIVATE_PARENT/.."],
    ids=["nul", "non-utf8", "no-file-name"],
)
def test_invalid_file_paths_are_rejected_by_model_and_private_cli(
    artifact_dir, monkeypatch, capsys, invalid_file,
):
    body = {
        "operation": "export", "tenant_id": str(uuid4()), "generation_id": str(uuid4()),
        "expected_revision": 1, "file": str(artifact_dir / invalid_file),
    }
    with pytest.raises(ValidationError):
        artifact.GraphArtifactRequest.model_validate_json(json.dumps(body))

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid file path reached artifact processing")

    monkeypatch.setattr(artifact, "graph_artifact", forbidden)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_ADMIN_DSN")
    with pytest.raises(SystemExit) as rejected:
        artifact.main([
            "export", "--tenant-id", body["tenant_id"], "--generation-id", body["generation_id"],
            "--expected-revision", "1", "--file", body["file"],
        ])
    assert rejected.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "invalid_graph_artifact_arguments" in output.err
    assert "PRIVATE_" not in output.err and "Traceback" not in output.err
    assert list(artifact_dir.iterdir()) == []


@pytest.mark.integration
def test_deterministic_canonical_history_is_complete_and_read_only(env, artifact_dir):
    graph = graph_fixture(env)
    graph.revise(
        "relation", "alternate", valid_from="2026-09-01T00:00:00Z",
        valid_to="2026-10-01T00:00:00Z",
    )
    graph.edge("past", "target", "source", valid_to="2020-01-01T00:00:00Z")
    reserved = begin(env)
    before = database_rows(env)
    paths = [artifact_dir / name for name in ("first.json", "second.json")]
    first, second = [run(env, reserved, path) for path in paths]
    payload = paths[0].read_bytes()
    body = json.loads(payload)
    assert first == second
    assert paths[1].read_bytes() == payload == canonical_bytes(body) + b"\n"
    assert first.artifact_digest == hashlib.sha256(payload).hexdigest()
    assert first.node_count == 3 and first.edge_revision_count == 3
    assert first.tenant_id == env.tenants[0] and first.generation_id == reserved.building.id
    assert first.revision == reserved.revision
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert set(first.model_dump()) == {
        "operation", "tenant_id", "generation_id", "revision", "artifact_digest",
        "node_count", "edge_revision_count", "artifact_verified", "serving_enabled",
    }
    assert set(body) == {
        "format", "schema_version", "tenant_id", "generation_id", "parent_id",
        "profile_digest", "input_digest", "input_snapshot", "nodes", "edge_revisions",
        "signature", "serving_enabled", "permission_filter_required",
    }
    assert body["format"] == "pgag-graph-artifact-v1" and body["schema_version"] == 22
    assert body["profile_digest"] == reserved.building.profile_digest
    assert body["input_digest"] == reserved.building.input_digest
    assert body["input_snapshot"] == reserved.building.input_snapshot.model_dump(mode="json")
    assert body["parent_id"] is None
    assert body["serving_enabled"] is False and body["permission_filter_required"] is True
    assert body["signature"] == signed_body(env, body)
    expected_nodes = {
        node["memory_id"]: {key: node[key] for key in ("memory_id", "scope_id", "recorded_at")}
        for node in graph.nodes.values()
    }
    assert [node["memory_id"] for node in body["nodes"]] == sorted(expected_nodes)
    for node in body["nodes"]:
        expected = expected_nodes[node["memory_id"]]
        assert node.keys() == expected.keys()
        assert node["scope_id"] == expected["scope_id"]
        assert timestamp(node["recorded_at"]) == timestamp(expected["recorded_at"])
    expected_edges = {}
    for edge in graph.edges.values():
        for index, revision in enumerate(edge["revisions"]):
            identity = (revision["assertion"]["memory_id"], revision["assertion"]["revision"])
            expected_edges[identity] = {
                **revision,
                "superseded_at": (
                    edge["revisions"][index + 1]["recorded_at"]
                    if index + 1 < len(edge["revisions"]) else None
                ),
            }
    assert [(edge["memory_id"], edge["revision"]) for edge in body["edge_revisions"]] == sorted(
        expected_edges
    )
    for edge in body["edge_revisions"]:
        assert set(edge) == {
            "memory_id", "revision", "scope_id", "source_entity", "target_entity", "predicate",
            "valid_from", "valid_to", "recorded_at", "superseded_at",
        }
        expected = expected_edges[(edge["memory_id"], edge["revision"])]
        for field in ("source_entity", "target_entity", "predicate"):
            assert edge[field] == expected[field]
        for field in ("valid_from", "valid_to", "recorded_at", "superseded_at"):
            assert (timestamp(edge[field]) if edge[field] else None) == (
                timestamp(expected[field]) if expected[field] else None
            )
        assert edge["scope_id"] == expected_nodes[edge["source_entity"]]["scope_id"]
        assert edge["scope_id"] == expected_nodes[edge["target_entity"]]["scope_id"]
    checked = run(env, reserved, paths[0], "check")
    assert checked.artifact_digest == first.artifact_digest
    assert database_rows(env) == before


@pytest.mark.integration
def test_admin_topology_includes_unreadable_scopes_but_not_other_tenants_or_content(
    env, artifact_dir,
):
    graph = graph_fixture(env)
    graph.node("private-scope", index=2, label="PRIVATE_UNREADABLE_ENTITY")
    graph.node("private-target", index=2, label="PRIVATE_UNREADABLE_TARGET")
    graph.edge("PRIVATE_UNREADABLE_RELATION", "private-scope", "private-target", index=2)
    foreign = GraphFixture(env)
    foreign.node("foreign", index=1, label="PRIVATE_FOREIGN_LABEL")
    foreign.node("foreign-target", index=1)
    foreign.edge("PRIVATE_FOREIGN_EDGE", "foreign", "foreign-target", index=1)
    path = artifact_dir / "tenant.json"
    result = run(env, begin(env), path)
    payload = path.read_text()
    body = json.loads(payload)
    assert result.node_count == 5 and result.edge_revision_count == 2
    assert {node["scope_id"] for node in body["nodes"]} == {
        str(env.scopes[0]), str(env.scopes[2]),
    }
    assert str(env.tenants[1]) not in payload and str(env.scopes[1]) not in payload
    assert all(node["memory_id"] not in payload for node in foreign.nodes.values())
    assert all(subject not in payload for subject in env.subjects)
    assert "PRIVATE_" not in payload and "evidence" not in json.dumps(body["nodes"])
    assert all(identifier not in payload for identifier in graph.node_evidence.values())
    with psycopg.connect(env.admin_url) as conn:
        secrets = conn.execute(
            """SELECT encode(t.dedup_secret,'hex'),encode(k.secret,'hex')
               FROM memory.tenant t JOIN memory_ops.recovery_key k ON k.tenant_id=t.id
               WHERE t.id=%s""", (env.tenants[0],),
        ).fetchone()
    assert all(secret not in payload for secret in secrets)


@pytest.mark.integration
def test_recording_then_rebuilding_same_generation_produces_identical_bytes(env, artifact_dir):
    graph_fixture(env)
    reserved = begin(env)
    path = artifact_dir / "building.json"
    exported = run(env, reserved, path)
    recorded = record(env, reserved, artifact_digest=exported.artifact_digest)
    before = metadata_rows(env)
    rebuilt = artifact_dir / "recorded.json"
    result = run(env, recorded, rebuilt)
    assert result.revision == recorded.revision != exported.revision
    assert result.artifact_digest == exported.artifact_digest
    assert rebuilt.read_bytes() == path.read_bytes()
    assert run(env, recorded, path, "check").artifact_digest == exported.artifact_digest
    pending = begin(env)
    child = artifact_dir / "child.json"
    run(env, pending, child)
    assert json.loads(child.read_bytes())["parent_id"] == str(recorded.head.id)
    assert before["memory_ops.graph_generation"][0] in metadata_rows(env)[
        "memory_ops.graph_generation"
    ]


@pytest.mark.integration
def test_recorded_digest_alone_cannot_authorize_different_canonical_bytes(env, artifact_dir):
    graph_fixture(env)
    reserved = begin(env)
    path = artifact_dir / "building.json"
    run(env, reserved, path)
    recorded = record(env, reserved, artifact_digest="0" * 64)
    before = database_rows(env)
    for operation, target in (("check", path), ("export", artifact_dir / "recorded.json")):
        with pytest.raises(AdminError, match="^graph_artifact_invalid$"):
            run(env, recorded, target, operation)
    assert not (artifact_dir / "recorded.json").exists()
    assert database_rows(env) == before


@pytest.mark.integration
def test_empty_topology_exports_and_checks_without_creating_metadata(env, artifact_dir):
    reserved = begin(env)
    before = metadata_rows(env)
    path = artifact_dir / "empty.json"
    result = run(env, reserved, path)
    assert result.node_count == result.edge_revision_count == 0
    body = json.loads(path.read_bytes())
    assert body["nodes"] == body["edge_revisions"] == []
    assert run(env, reserved, path, "check").artifact_digest == result.artifact_digest
    assert metadata_rows(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("change", ["relation", "revision", "acl", "purge"])
def test_changed_canonical_input_refuses_export_and_check_before_output(
    env, artifact_dir, change,
):
    graph = graph_fixture(env)
    reserved = begin(env)
    path = artifact_dir / "original.json"
    run(env, reserved, path)
    if change == "relation":
        graph.edge("additional", "target", "alternate")
    elif change == "revision":
        graph.revise("relation", "alternate")
    elif change == "acl":
        with scope_access(env.admin_url, ScopeAccessRequest(
            operation="revoke", tenant_id=env.tenants[0], scope_id=env.scopes[0],
            principal_id=env.principals[0], expected_access_epoch=1,
        )):
            pass
    else:
        response = env.client.post(
            "/v1/forget", headers=env.headers(),
            json={"memory_ids": [graph.nodes["source"]["memory_id"]], "reason": "artifact purge"},
        )
        assert response.status_code == 202, response.text
    before = database_rows(env)
    for operation, target in (("check", path), ("export", artifact_dir / "stale.json")):
        with pytest.raises(AdminError, match="^graph_input_changed$"):
            run(env, reserved, target, operation)
    assert not (artifact_dir / "stale.json").exists()
    assert database_rows(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("state", ["missing", "abandoned", "superseded", "foreign"])
def test_only_pending_builder_or_current_recorded_head_is_available(env, artifact_dir, state):
    reserved = begin(env)
    identifier = reserved.building.id
    current = reserved
    if state == "missing":
        identifier = uuid4()
    elif state == "abandoned":
        current = abandon(env, reserved)
    elif state == "superseded":
        record(env, reserved)
        current = record(env, begin(env))
    else:
        identifier = begin_foreign(env).building.id
    path = artifact_dir / "unavailable.json"
    with pytest.raises(AdminError, match="^graph_generation_unavailable$"):
        run(env, current if current.head or current.building else reserved, path,
            generation_id=identifier, expected_revision=current.revision)
    assert not path.exists()


def begin_foreign(env):
    from pg_agmemory.graph_generation import GraphGenerationRequest, graph_generation

    def operation(**values):
        with graph_generation(
            env.admin_url, GraphGenerationRequest(tenant_id=env.tenants[1], **values)
        ) as result:
            return result

    initial = operation(operation="get")
    return operation(
        operation="begin", expected_revision=0, generation_id=uuid4(),
        expected_input_digest=initial.current_input_digest, profile_digest="d" * 64,
    )


@pytest.mark.integration
def test_wrong_revision_is_rejected_without_overwriting_output(env, artifact_dir):
    reserved = begin(env)
    path = artifact_dir / "valid.json"
    run(env, reserved, path)
    before = path.read_bytes()
    for operation, target in (("check", path), ("export", artifact_dir / "wrong.json")):
        with pytest.raises(AdminError, match="^graph_revision_conflict$"):
            run(env, reserved, target, operation, expected_revision=reserved.revision + 1)
    assert path.read_bytes() == before and not (artifact_dir / "wrong.json").exists()


@pytest.mark.integration
def test_runtime_credentials_are_denied_for_both_operations(env, artifact_dir):
    reserved = begin(env)
    path = artifact_dir / "admin.json"
    run(env, reserved, path)
    before = database_rows(env)
    for operation, target in (("check", path), ("export", artifact_dir / "runtime.json")):
        with pytest.raises(AdminError, match="^admin_role_required$"):
            run(env, reserved, target, operation, url=env.settings.database_url)
    assert not (artifact_dir / "runtime.json").exists()
    assert database_rows(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("schema", [18, 19, 20, 21])
@pytest.mark.parametrize("field", ["schema_version", "input_snapshot"])
def test_previous_schema_artifact_requires_matching_version(env, artifact_dir, schema, field):
    reserved = begin(env)
    path = artifact_dir / "old-schema.json"
    run(env, reserved, path)
    body = json.loads(path.read_bytes())
    if field == "input_snapshot":
        body[field]["schema_version"] = schema
    else:
        body[field] = schema
    with pytest.raises(ValidationError):
        artifact.GraphArtifact.model_validate_json(json.dumps(body))
    body["signature"] = signed_body(env, body)
    path.write_bytes(canonical_bytes(body) + b"\n")
    before = database_rows(env)
    with pytest.raises(AdminError, match="^graph_artifact_invalid$"):
        run(env, reserved, path, "check")
    assert database_rows(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("corruption", ["signature", "resigned_body", "order", "malformed"])
def test_check_requires_authentic_ordered_exact_canonical_body(
    env, artifact_dir, corruption,
):
    graph_fixture(env)
    reserved = begin(env)
    path = artifact_dir / "tampered.json"
    run(env, reserved, path)
    body = json.loads(path.read_bytes())
    assert body["signature"] == signed_body(env, body)
    if corruption == "signature":
        body["signature"] = "0" * 64
    elif corruption == "resigned_body":
        edge = body["edge_revisions"][0]
        edge["target_entity"] = next(
            node["memory_id"] for node in body["nodes"]
            if node["memory_id"] not in (edge["source_entity"], edge["target_entity"])
        )
        with psycopg.connect(env.admin_url) as conn:
            secret = bytes(conn.execute(
                "SELECT secret FROM memory_ops.recovery_key WHERE tenant_id=%s",
                (env.tenants[0],),
            ).fetchone()[0])
        parsed = artifact.GraphArtifact.model_validate_json(json.dumps(body))
        body["signature"] = artifact.signature(parsed, secret)
        assert body["signature"] == signed_body(env, body)
    elif corruption == "order":
        body["nodes"].reverse()
        body["signature"] = signed_body(env, body)
    payload = (
        b"PRIVATE_MALFORMED_JSON" if corruption == "malformed" else canonical_bytes(body) + b"\n"
    )
    path.write_bytes(payload)
    before = database_rows(env)
    with pytest.raises(AdminError, match="^graph_artifact_invalid$"):
        run(env, reserved, path, "check")
    assert database_rows(env) == before
    assert path.read_bytes() == payload


@pytest.mark.integration
def test_check_rejects_absent_symlink_nonprivate_and_nonregular_files(env, artifact_dir):
    reserved = begin(env)
    valid = artifact_dir / "valid.json"
    run(env, reserved, valid)
    absent, symlink, world, fifo = [artifact_dir / name for name in (
        "absent.json", "symlink.json", "world.json", "fifo",
    )]
    symlink.symlink_to(valid)
    world.write_bytes(valid.read_bytes())
    world.chmod(0o640)
    os.mkfifo(fifo, 0o600)
    for path in (absent, symlink, world, fifo, artifact_dir):
        with pytest.raises(AdminError, match="^graph_artifact_file_invalid$"):
            run(env, reserved, path, "check")
    assert stat.S_ISFIFO(fifo.stat().st_mode) and symlink.is_symlink()


@pytest.mark.integration
def test_export_uses_exclusive_private_nofollow_creation(env, artifact_dir, monkeypatch):
    reserved = begin(env)
    target = artifact_dir / "existing.json"
    target.write_bytes(b"DO_NOT_OVERWRITE")
    target.chmod(0o600)
    link = artifact_dir / "link.json"
    link.symlink_to(target)
    for path in (target, link):
        with pytest.raises(AdminError, match="^graph_artifact_output_failed$"):
            run(env, reserved, path)
    assert target.read_bytes() == b"DO_NOT_OVERWRITE" and link.is_symlink()
    original_open = os.open
    flags = []
    fresh = artifact_dir / "fresh.json"

    def opened(path, mode, *args, **kwargs):
        if Path(path).name == fresh.name:
            flags.append(mode)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(artifact.os, "open", opened)
    run(env, reserved, fresh)
    assert len(flags) == 1
    assert flags[0] & os.O_CREAT and flags[0] & os.O_EXCL and flags[0] & os.O_NOFOLLOW
    assert fresh.stat().st_mode & 0o777 == 0o600
    run(env, reserved, fresh, "check")
    assert len(flags) == 2
    assert flags[1] & os.O_NOFOLLOW and flags[1] & os.O_NONBLOCK
    assert flags[1] & os.O_ACCMODE == os.O_RDONLY


@pytest.mark.integration
@pytest.mark.parametrize("limit", ["MAX_NODES", "MAX_EDGE_REVISIONS", "MAX_ARTIFACT_BYTES",
                                  "MAX_SECONDS"])
def test_limits_refuse_partial_artifacts_and_do_not_mutate_receipts(
    env, artifact_dir, monkeypatch, limit,
):
    defaults = {
        "MAX_NODES": 10000, "MAX_EDGE_REVISIONS": 40000,
        "MAX_ARTIFACT_BYTES": 16 * 1024 * 1024, "MAX_SECONDS": 30,
    }
    assert getattr(artifact, limit) == defaults[limit]
    graph_fixture(env)
    reserved = begin(env)
    valid = artifact_dir / "valid.json"
    run(env, reserved, valid)
    before = database_rows(env)
    monkeypatch.setattr(artifact, limit, 0)
    for operation, path in (("export", artifact_dir / "limited.json"), ("check", valid)):
        with pytest.raises(AdminError, match="^graph_artifact_(limit|timeout)$"):
            run(env, reserved, path, operation)
    assert not (artifact_dir / "limited.json").exists()
    assert database_rows(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["write", "fsync", "replaced_inode"])
def test_failed_output_removes_only_newly_owned_same_inode(
    env, artifact_dir, monkeypatch, failure,
):
    reserved = begin(env)
    path = artifact_dir / "failed.json"
    saved = artifact_dir / "owned-original.json"
    before = database_rows(env)
    original_write = os.write
    original_fdopen = os.fdopen
    original_fsync = os.fsync

    def fail_sync(fd):
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return original_fsync(fd)
        if failure == "replaced_inode":
            path.rename(saved)
            path.write_bytes(b"FOREIGN_REPLACEMENT_MUST_SURVIVE")
        raise OSError("PRIVATE_FILESYSTEM_FAILURE")

    def fail_write(fd, data):
        original_write(fd, data[:7])
        raise OSError("PRIVATE_FILESYSTEM_FAILURE")

    @contextmanager
    def opened(fd, mode, *args, **kwargs):
        with original_fdopen(fd, mode, *args, **kwargs) as stream:
            class InterruptedStream:
                def __getattr__(self, name):
                    return getattr(stream, name)

                def write(self, data):
                    stream.write(data[:7])
                    stream.flush()
                    raise OSError("PRIVATE_FILESYSTEM_FAILURE")

            yield InterruptedStream() if "w" in mode else stream

    with monkeypatch.context() as patch:
        if failure == "write":
            patch.setattr(artifact.os, "write", fail_write)
            patch.setattr(artifact.os, "fdopen", opened)
        else:
            patch.setattr(artifact.os, "fsync", fail_sync)
        code = (
            "graph_artifact_cleanup_failed" if failure == "replaced_inode"
            else "graph_artifact_output_failed"
        )
        with pytest.raises(AdminError, match=f"^{code}$"):
            run(env, reserved, path)
    if failure == "replaced_inode":
        assert path.read_bytes() == b"FOREIGN_REPLACEMENT_MUST_SURVIVE"
        assert saved.is_file()
    else:
        assert not path.exists()
    assert database_rows(env) == before
    assert run(env, reserved, artifact_dir / "retry.json").artifact_verified


def test_cli_validation_errors_do_not_echo_private_values_or_connect(
    artifact_dir, monkeypatch, capsys,
):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_ADMIN_DSN")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid CLI arguments reached the database")

    monkeypatch.setattr(artifact, "graph_artifact", forbidden)
    valid = [
        "export", "--tenant-id", str(uuid4()), "--generation-id", str(uuid4()),
        "--expected-revision", "1", "--file", str(artifact_dir / "PRIVATE_PATH"),
    ]
    for args in (
        ["PRIVATE_OPERATION"], valid + ["--PRIVATE_FLAG"],
        [*valid[:6], "PRIVATE_REVISION", *valid[7:]], valid[:-2],
    ):
        with pytest.raises(SystemExit) as rejected:
            artifact.main(args)
        assert rejected.value.code != 0
        output = capsys.readouterr()
        assert "PRIVATE_" not in output.out + output.err
        assert "Traceback" not in output.out + output.err
    assert list(artifact_dir.iterdir()) == []


@pytest.mark.integration
def test_cli_export_check_roundtrip_is_private_and_never_claims_serving(env, artifact_dir):
    graph_fixture(env)
    reserved = begin(env)
    path = artifact_dir / "cli.json"
    flags = [
        "--tenant-id", str(env.tenants[0]), "--generation-id", str(reserved.building.id),
        "--expected-revision", str(reserved.revision), "--file", str(path),
    ]

    def cli(operation, url):
        return subprocess.run(
            [sys.executable, "-m", "pg_agmemory.cli", "graph-artifact", operation, *flags],
            capture_output=True, text=True, timeout=15,
            env={"PATH": os.environ["PATH"], "PGAG_ADMIN_DATABASE_URL": url},
        )

    before = database_rows(env)
    for operation in ("export", "check"):
        completed = cli(operation, env.admin_url)
        assert completed.returncode == 0 and completed.stderr == "", completed.stdout
        result = json.loads(completed.stdout)
        assert result["operation"] == operation
        assert result["artifact_verified"] is True and result["serving_enabled"] is False
        assert result["artifact_digest"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert "nodes" not in result and "PRIVATE_" not in completed.stdout
    denied = cli("check", env.settings.database_url)
    assert denied.returncode == 1 and denied.stderr == ""
    error = json.loads(denied.stdout)["error"]
    assert error == {"code": "admin_role_required", "outcome_unknown": False}
    assert path.stat().st_mode & 0o777 == 0o600
    assert database_rows(env) == before


@pytest.mark.integration
def test_repeatable_read_read_only_and_tenant_barrier_cover_result_delivery(
    env, artifact_dir, monkeypatch,
):
    reserved = begin(env)
    started = Event()
    transaction_modes = []
    original = artifact.admin_connection

    class ObservedConnection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, query, *args, **kwargs):
            result = self.conn.execute(query, *args, **kwargs)
            if isinstance(query, str) and "SET TRANSACTION" in query.upper():
                transaction_modes.append(self.conn.execute(
                    "SELECT current_setting('transaction_isolation') AS isolation,"
                    "current_setting('transaction_read_only') AS read_only"
                ).fetchone())
            return result

    @contextmanager
    def observed(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield ObservedConnection(conn)

    def inspect():
        started.set()
        return execute(env)

    monkeypatch.setattr(artifact, "admin_connection", observed)
    path = artifact_dir / "barrier.json"
    for operation in ("export", "check"):
        started.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with artifact.graph_artifact(
                env.admin_url, request(env, reserved, path, operation)
            ):
                waiting = pool.submit(inspect)
                assert started.wait(timeout=5)
                with pytest.raises(TimeoutError):
                    waiting.result(timeout=0.2)
            assert waiting.result(timeout=5).revision == reserved.revision
    assert transaction_modes == [
        {"isolation": "repeatable read", "read_only": "on"},
        {"isolation": "repeatable read", "read_only": "on"},
    ]
