"""Explicit rebuild of quarantined receipts after a canonical-only AGE restore."""

import json
import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError
from test_age_projection import (
    assert_native_matches,
    cli,
    forbid_graph_ddl,
    operate,
    physical_graphs,
    prepared,
    publish,
)
from test_age_projection import publication_profile as publication_profile
from test_graph_artifact import artifact_dir as artifact_dir
from test_graph_generation import graph_fixture, metadata_rows

from pg_agmemory import age_projection as publisher
from pg_agmemory.admin import AdminError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PGAG_TEST_AGE_NATIVE") != "1",
        reason="PGAG_TEST_AGE_NATIVE=1 requires the dedicated patched AGE image",
    ),
]


@pytest.fixture(autouse=True)
def require_publication_profile(publication_profile):
    pass


def drop_graph(env, name):
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SET LOCAL search_path=ag_catalog,pg_catalog")
        conn.execute("SELECT ag_catalog.drop_graph(%s,true)", (name,))


def physical_presence(env, name):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute(
            """SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s),
                      EXISTS(SELECT 1 FROM ag_catalog.ag_graph WHERE name=%s)""",
            (name, name),
        ).fetchone()


def missing_projection(env, directory):
    graph = graph_fixture(env)
    current, path = prepared(env, directory)
    published = publish(env, current, path)
    assert not published.rebuilt_missing_projection
    disabled = operate(env, "disable", expected_revision=published.revision)
    assert not disabled.rebuilt_missing_projection
    drop_graph(env, disabled.projection.graph_name)
    assert physical_presence(env, disabled.projection.graph_name) == (False, False)
    return graph, current, path, disabled


@contextmanager
def forbid_graph_drop(monkeypatch):
    original = psycopg.Connection.execute

    def guarded(conn, query, *args, **kwargs):
        text = query.as_string(conn) if isinstance(query, sql.Composable) else query
        if "ag_catalog.drop_graph(" in text:
            pytest.fail("Verified missing prior graph must never be dropped")
        return original(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", guarded)
        yield


@pytest.mark.parametrize("target", ["same_head", "new_child"])
def test_missing_disabled_projection_rebuilds_atomically_from_current_artifact(
    env, artifact_dir, monkeypatch, target,
):
    graph, first, path, disabled = missing_projection(env, artifact_dir)
    current = first
    if target == "new_child":
        current, path = prepared(env, artifact_dir)
        assert current.head.parent_id == first.head.id
    receipts = metadata_rows(env)
    with forbid_graph_drop(monkeypatch):
        result = publish(
            env, current, path, expected_revision=disabled.revision, rebuild_missing=True,
        )
    assert result.changed and result.artifact_verified and result.serving_enabled
    assert result.rebuilt_missing_projection
    assert result.revision == disabled.revision + 1
    assert result.projection.enabled and result.projection.generation_id == current.head.id
    assert result.projection.created_at == disabled.projection.created_at
    assert result.projection.updated_at > disabled.projection.updated_at
    assert result.projection.artifact_digest == current.head.artifact_digest
    assert physical_presence(env, result.projection.graph_name) == (True, True)
    if target == "new_child":
        assert physical_presence(env, disabled.projection.graph_name) == (False, False)
    assert metadata_rows(env) == receipts
    assert not operate(env).rebuilt_missing_projection
    assert_native_matches(env, graph, current.head.id)


@pytest.mark.parametrize("previous", ["absent", "active_present", "active_missing"])
def test_rebuild_flag_requires_existing_disabled_registry(
    env, artifact_dir, monkeypatch, previous,
):
    graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    revision = 0
    if previous != "absent":
        revision = publish(env, current, path).revision
        if previous == "active_missing":
            drop_graph(env, operate(env).projection.graph_name)
    before, graphs, receipts = operate(env), physical_graphs(env), metadata_rows(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_projection_unavailable$"):
            publish(env, current, path, expected_revision=revision, rebuild_missing=True)
    assert operate(env) == before and physical_graphs(env) == graphs
    assert metadata_rows(env) == receipts


def test_rebuild_flag_rejects_disabled_graph_that_is_still_present(
    env, artifact_dir, monkeypatch,
):
    graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    published = publish(env, current, path)
    disabled = operate(env, "disable", expected_revision=published.revision)
    before, graphs = operate(env), physical_graphs(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_projection_not_missing$"):
            publish(
                env, current, path, expected_revision=disabled.revision, rebuild_missing=True,
            )
    assert operate(env) == before and physical_graphs(env) == graphs


def test_partial_namespace_presence_is_invalid_not_a_missing_graph(
    env, artifact_dir, monkeypatch,
):
    _, current, path, disabled = missing_projection(env, artifact_dir)
    name = disabled.projection.graph_name
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        assert physical_presence(env, name) == (True, False)
        before, graphs = operate(env), physical_graphs(env)
        with forbid_graph_ddl(monkeypatch):
            with pytest.raises(AdminError, match="^graph_projection_invalid$"):
                publish(
                    env, current, path, expected_revision=disabled.revision, rebuild_missing=True,
                )
        assert operate(env) == before and physical_graphs(env) == graphs
        assert physical_presence(env, name) == (True, False)
    finally:
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {}").format(sql.Identifier(name)))


def test_missing_previous_graph_without_flag_is_denied_before_ddl(
    env, artifact_dir, monkeypatch,
):
    _, current, path, disabled = missing_projection(env, artifact_dir)
    before, graphs = operate(env), physical_graphs(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_projection_missing$"):
            publish(env, current, path, expected_revision=disabled.revision)
    assert operate(env) == before and physical_graphs(env) == graphs
    assert physical_presence(env, disabled.projection.graph_name) == (False, False)


@pytest.mark.parametrize("guard,code", [
    ("registry_cas", "graph_projection_revision_conflict"),
    ("generation_cas", "graph_revision_conflict"),
    ("tamper", "graph_artifact_invalid"),
    ("wrong_file", "graph_artifact_invalid"),
    ("canonical_content", "graph_input_changed"),
    ("nonhead", "graph_generation_unavailable"),
    ("runtime_role", "admin_role_required"),
])
def test_rebuild_opt_in_preserves_cas_head_artifact_and_authority_guards(
    env, artifact_dir, monkeypatch, guard, code,
):
    graph, first, first_path, disabled = missing_projection(env, artifact_dir)
    current, path = prepared(env, artifact_dir)
    changes = {"expected_revision": disabled.revision, "rebuild_missing": True}
    if guard == "registry_cas":
        changes["expected_revision"] += 1
    elif guard == "generation_cas":
        changes["expected_generation_revision"] = current.revision + 1
    elif guard == "tamper":
        body = json.loads(path.read_bytes())
        body["signature"] = "0" * 64
        path.write_text(json.dumps(body))
    elif guard == "wrong_file":
        path = first_path
    elif guard == "canonical_content":
        graph.node("PRIVATE_NEW_CANONICAL_NODE")
    elif guard == "nonhead":
        changes["generation_id"] = first.head.id
    else:
        changes["url"] = env.settings.database_url
    before, graphs, receipts = operate(env), physical_graphs(env), metadata_rows(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match=f"^{code}$"):
            publish(env, current, path, **changes)
    assert operate(env) == before and physical_graphs(env) == graphs
    assert metadata_rows(env) == receipts
    assert physical_presence(env, disabled.projection.graph_name) == (False, False)


@pytest.mark.parametrize("target", ["same_head", "new_child"])
def test_failure_after_new_build_preserves_disabled_receipt_and_missing_graph(
    env, artifact_dir, monkeypatch, target,
):
    _, current, path, disabled = missing_projection(env, artifact_dir)
    if target == "new_child":
        current, path = prepared(env, artifact_dir)
    before, graphs, receipts = operate(env), physical_graphs(env), metadata_rows(env)
    original = publisher.build_graph
    built = []

    def fail_after_build(conn, artifact):
        name = original(conn, artifact)
        assert conn.execute(
            "SELECT 1 FROM ag_catalog.ag_graph WHERE name=%s", (name,),
        ).fetchone() is not None
        built.append(name)
        raise AdminError("injected_recovery_build_failure")

    with monkeypatch.context() as patch:
        patch.setattr(publisher, "build_graph", fail_after_build)
        with pytest.raises(AdminError, match="^injected_recovery_build_failure$"):
            publish(
                env, current, path, expected_revision=disabled.revision, rebuild_missing=True,
            )
    assert built == ["pgag_age_" + current.head.id.hex]
    assert operate(env) == before and physical_graphs(env) == graphs
    assert metadata_rows(env) == receipts
    assert physical_presence(env, built[0]) == (False, False)
    assert physical_presence(env, disabled.projection.graph_name) == (False, False)


def test_missing_rebuild_never_deletes_unregistered_other_graph(env, artifact_dir):
    graph, _, _, disabled = missing_projection(env, artifact_dir)
    unknown = "pgag_age_" + uuid4().hex
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SET LOCAL search_path=ag_catalog,pg_catalog")
        conn.execute("SELECT ag_catalog.create_graph(%s)", (unknown,))
    try:
        graphs = physical_graphs(env)
        current, path = prepared(env, artifact_dir)
        rebuilt = publish(
            env, current, path, expected_revision=disabled.revision, rebuild_missing=True,
        )
        after = physical_graphs(env)
        assert set(after) == set(graphs) | {rebuilt.projection.graph_name}
        assert all(after[name] == rows for name, rows in graphs.items())
        assert physical_presence(env, disabled.projection.graph_name) == (False, False)
        assert_native_matches(env, graph, current.head.id)
    finally:
        drop_graph(env, unknown)


def test_request_rebuild_flag_is_strict_boolean_and_publish_only(artifact_dir):
    body = {
        "operation": "publish", "tenant_id": uuid4(), "expected_revision": 2,
        "generation_id": uuid4(), "expected_generation_revision": 2,
        "file": artifact_dir / "PRIVATE_ARTIFACT_PATH.json",
    }
    assert publisher.AgeProjectionRequest(**body).rebuild_missing is False
    assert publisher.AgeProjectionRequest(**body, rebuild_missing=True).rebuild_missing is True
    for invalid in (None, 0, 1, "true", "false", [], {}):
        with pytest.raises(ValidationError):
            publisher.AgeProjectionRequest(**body, rebuild_missing=invalid)
    for operation in ("get", "disable"):
        fields = {"expected_revision": 2} if operation == "disable" else {}
        with pytest.raises(ValidationError):
            publisher.AgeProjectionRequest(
                operation=operation, tenant_id=body["tenant_id"], rebuild_missing=True, **fields,
            )


def test_cli_rebuild_success_and_failures_are_private(env, artifact_dir):
    graph, current, path, disabled = missing_projection(env, artifact_dir)
    args = [
        "publish", "--tenant-id", str(env.tenants[0]),
        "--expected-revision", str(disabled.revision), "--generation-id", str(current.head.id),
        "--expected-generation-revision", str(current.revision), "--file", str(path),
    ]
    no_option = cli(env, *args)
    assert no_option.returncode == 1 and not no_option.stderr
    assert json.loads(no_option.stdout) == {
        "error": {"code": "graph_projection_missing", "outcome_unknown": False},
    }
    original = path.read_bytes()
    path.write_text("PRIVATE_CORRUPTED_ARTIFACT_PAYLOAD")
    invalid = cli(env, *args, "--rebuild-missing")
    assert invalid.returncode == 1 and not invalid.stderr
    assert json.loads(invalid.stdout) == {
        "error": {"code": "graph_projection_invalid", "outcome_unknown": False},
    }
    for output in (no_option.stdout, invalid.stdout):
        assert "PRIVATE_" not in output and "Traceback" not in output
        assert str(path) not in output and env.admin_url not in output
    assert operate(env).projection == disabled.projection
    assert physical_presence(env, disabled.projection.graph_name) == (False, False)
    path.write_bytes(original)
    success = cli(env, *args, "--rebuild-missing")
    assert success.returncode == 0, success.stderr
    output = json.loads(success.stdout)
    assert output["revision"] == disabled.revision + 1
    assert output["serving_enabled"] and output["artifact_verified"] and output["changed"]
    assert output["rebuilt_missing_projection"] is True
    assert output["projection"]["generation_id"] == str(current.head.id)
    assert_native_matches(env, graph, current.head.id)


@pytest.mark.parametrize("operation", ["get", "disable"])
def test_cli_rebuild_flag_rejects_nonpublish_operations_before_database(
    monkeypatch, capsys, operation,
):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_DATABASE_URL")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid publish-only flag reached the database")

    monkeypatch.setattr(publisher, "age_projection", forbidden)
    options = ["--expected-revision", "2"] if operation == "disable" else []
    with pytest.raises(SystemExit) as error:
        publisher.main([
            operation, "--tenant-id", str(uuid4()), *options, "--rebuild-missing",
        ])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out and "invalid_age_projection_arguments" in output.err
    assert "PRIVATE_" not in output.err and "Traceback" not in output.err
