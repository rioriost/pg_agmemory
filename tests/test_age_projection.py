"""Real optional AGE publication, rollback, and private administrative contracts."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from test_graph_artifact import artifact_dir as artifact_dir
from test_graph_artifact import run as artifact_run
from test_graph_generation import abandon, begin, execute, graph_fixture, record

from pg_agmemory import age_projection as publisher
from pg_agmemory import graph_generation as generation
from pg_agmemory.admin import AdminError
from pg_agmemory.age_graph import AgeGraph
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import ExpandGraph, GraphResult
from pg_agmemory.recovery_apply import secret_for
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PGAG_TEST_AGE_NATIVE") != "1",
        reason="PGAG_TEST_AGE_NATIVE=1 requires the dedicated patched AGE image",
    ),
]


@pytest.fixture(scope="session")
def publication_profile(database):
    with psycopg.connect(database[0]) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS age")
        assert conn.execute("SELECT ag_catalog.pgag_age_preloaded()").fetchone() == (True,)


@pytest.fixture(autouse=True)
def require_publication_profile(publication_profile):
    pass


def operate(env, operation="get", *, url=None, **changes):
    request = publisher.AgeProjectionRequest(
        operation=operation, tenant_id=env.tenants[0], **changes,
    )
    with publisher.age_projection(url or env.admin_url, request) as result:
        return result


def prepared(env, directory):
    reserved = begin(env)
    path = directory / f"{reserved.building.id}.json"
    exported = artifact_run(env, reserved, path)
    current = record(env, reserved, artifact_digest=exported.artifact_digest)
    return current, path


def publish(env, current, path, *, expected_revision=0, **changes):
    return operate(env, "publish", **{
        "expected_revision": expected_revision,
        "generation_id": current.head.id,
        "expected_generation_revision": current.revision,
        "file": path,
        **changes,
    })


def physical_graphs(env):
    with psycopg.connect(env.admin_url) as conn:
        result = {}
        for graph, namespace in conn.execute(
            "SELECT name,namespace::oid FROM ag_catalog.ag_graph ORDER BY name",
        ):
            labels = conn.execute(
                """SELECT c.relname,c.oid,c.relrowsecurity,c.relforcerowsecurity
                   FROM pg_class c WHERE c.relnamespace=%s AND c.relkind='r'
                   ORDER BY c.relname""", (namespace,),
            ).fetchall()
            result[graph] = [
                (label, oid, rls, forced, conn.execute(
                    sql.SQL('SELECT to_jsonb(t) FROM ONLY {} t ORDER BY to_jsonb(t)::text').format(
                        sql.Identifier(graph, label),
                    ),
                ).fetchall()) for label, oid, rls, forced in labels
            ]
        return result


@contextmanager
def forbid_graph_ddl(monkeypatch):
    execute = psycopg.Connection.execute

    def guarded(conn, query, *args, **kwargs):
        text = query.as_string(conn) if isinstance(query, sql.Composable) else query
        if "ag_catalog.create_graph(" in text or "ag_catalog.drop_graph(" in text:
            pytest.fail("Publication attempted graph DDL before validating its receipt and file")
        return execute(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", guarded)
        yield


async def read_graph(env, body, backend):
    async with principal_connection(env.settings.database_url, env.subjects[0]) as (conn, identity):
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await bind_identity(conn, env.subjects[0], identity)
            result = await backend(MemoryService(conn, identity)).expand(
                ExpandGraph.model_validate(body),
            )
    return GraphResult.model_validate(result).model_dump(mode="json")


def assert_native_matches(env, graph, generation):
    body = graph.request(["source"])
    native = asyncio.run(read_graph(env, body, AgeGraph))
    assert native["backend"] == "age" and native["projection_watermark"] == str(generation)
    assert native["paths"]
    normalized = {**native, "backend": "sql", "projection_watermark": None}
    assert normalized == asyncio.run(read_graph(env, body, SqlGraph))


def test_empty_get_is_read_only_metadata_not_artifact_verification(env):
    before = physical_graphs(env)
    result = operate(env)
    assert result.projection is None and result.revision == 0
    assert not result.changed and not result.serving_enabled and not result.artifact_verified
    assert operate(env) == result and physical_graphs(env) == before


def test_publish_verified_artifact_serves_real_native_paths_equal_to_sql(env, artifact_dir):
    graph = graph_fixture(env)
    graph.edge("second-hop", "target", "alternate")
    current, path = prepared(env, artifact_dir)
    result = publish(env, current, path)
    receipt = result.projection
    assert result.changed and result.artifact_verified and result.serving_enabled
    assert result.revision == receipt.revision == 1
    assert receipt.generation_id == current.head.id
    assert receipt.graph_name == "pgag_age_" + current.head.id.hex
    assert receipt.artifact_digest == current.head.artifact_digest
    assert receipt.node_count == 3 and receipt.edge_revision_count == 2
    assert receipt.captured_access_epoch == current.head.input_snapshot.access_epoch
    assert receipt.captured_deletion_epoch == current.head.input_snapshot.deletion_epoch
    assert receipt.captured_schema_version == 21
    assert all(rls and forced for _, _, rls, forced, _ in physical_graphs(env)[receipt.graph_name])
    assert_native_matches(env, graph, current.head.id)
    metadata = operate(env)
    assert metadata.projection == receipt and metadata.serving_enabled
    assert not metadata.artifact_verified and not metadata.changed


def test_disable_preserves_receipt_and_physical_graph_but_never_falls_back(env, artifact_dir):
    graph = graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    published = publish(env, current, path)
    before = physical_graphs(env)
    disabled = operate(env, "disable", expected_revision=published.revision)
    assert disabled.changed and not disabled.serving_enabled and not disabled.artifact_verified
    assert disabled.revision == 2 and disabled.projection.generation_id == current.head.id
    assert physical_graphs(env) == before
    with pytest.raises(MemoryError, match="^graph_projection_unavailable$"):
        asyncio.run(read_graph(env, graph.request(["source"]), AgeGraph))
    assert asyncio.run(read_graph(env, graph.request(["source"]), SqlGraph))["paths"]


def test_stale_source_publish_is_refused_before_ddl_and_preserves_old_graph(
    env, artifact_dir, monkeypatch,
):
    graph = graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    publish(env, current, path)
    graph.node("new-canonical-input")
    before, graphs = operate(env), physical_graphs(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_input_changed$"):
            publish(env, current, path, expected_revision=1)
    assert operate(env) == before and physical_graphs(env) == graphs


@pytest.mark.parametrize("corruption", ["tampered", "wrong-file"])
def test_invalid_file_is_rejected_before_ddl_and_preserves_old_projection(
    env, artifact_dir, monkeypatch, corruption,
):
    graph_fixture(env)
    first, first_path = prepared(env, artifact_dir)
    publish(env, first, first_path)
    current, path = prepared(env, artifact_dir)
    if corruption == "tampered":
        body = json.loads(path.read_bytes())
        body["signature"] = "0" * 64
        path.write_text(json.dumps(body) + "\n")
    else:
        path = first_path
    before, graphs = operate(env), physical_graphs(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_artifact_invalid$"):
            publish(env, current, path, expected_revision=1)
    assert operate(env) == before and physical_graphs(env) == graphs


@pytest.mark.parametrize("registry", [True, False])
def test_registry_and_generation_cas_fail_before_ddl(env, artifact_dir, monkeypatch, registry):
    graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    before = physical_graphs(env)
    changes = {"expected_revision": 1} if registry else {
        "expected_generation_revision": current.revision + 1,
    }
    code = "graph_projection_revision_conflict" if registry else "graph_revision_conflict"
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match=f"^{code}$"):
            publish(env, current, path, **changes)
    assert operate(env).projection is None and physical_graphs(env) == before


@pytest.mark.parametrize("state", ["building", "superseded"])
def test_nonhead_generation_cannot_publish(env, artifact_dir, monkeypatch, state):
    graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    reserved = begin(env)
    identifier = reserved.building.id if state == "building" else current.head.id
    if state == "superseded":
        latest_path = artifact_dir / "latest.json"
        exported = artifact_run(env, reserved, latest_path)
        reserved = record(env, reserved, artifact_digest=exported.artifact_digest)
    before = physical_graphs(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_generation_unavailable$"):
            publish(env, current, path, generation_id=identifier,
                    expected_generation_revision=reserved.revision)
    assert operate(env).projection is None and physical_graphs(env) == before


@pytest.mark.parametrize("point", ["after-ddl", "before-commit"])
def test_failed_publication_rolls_back_registry_and_all_physical_graphs(
    env, artifact_dir, monkeypatch, point,
):
    graph = graph_fixture(env)
    first, first_path = prepared(env, artifact_dir)
    publish(env, first, first_path)
    current, path = prepared(env, artifact_dir)
    before, graphs = operate(env), physical_graphs(env)
    if point == "after-ddl":
        build = publisher.build_graph

        def fail_after_build(conn, artifact):
            build(conn, artifact)
            raise AdminError("injected_projection_failure")

        monkeypatch.setattr(publisher, "build_graph", fail_after_build)
        code = "injected_projection_failure"
    else:
        execute = psycopg.Connection.execute

        def fail_before_commit(conn, query, *args, **kwargs):
            if query == "SET CONSTRAINTS ALL IMMEDIATE":
                raise psycopg.errors.CheckViolation("PRIVATE_PUBLICATION_FAILURE")
            return execute(conn, query, *args, **kwargs)

        monkeypatch.setattr(psycopg.Connection, "execute", fail_before_commit)
        code = "admin_database_error"
    with pytest.raises(AdminError, match=f"^{code}$"):
        publish(env, current, path, expected_revision=1)
    monkeypatch.undo()
    assert operate(env) == before and physical_graphs(env) == graphs
    assert_native_matches(env, graph, first.head.id)


def test_second_generation_replaces_only_its_previous_physical_graph(env, artifact_dir):
    graph = graph_fixture(env)
    first, first_path = prepared(env, artifact_dir)
    publish(env, first, first_path)
    unrelated = "unrelated_" + uuid4().hex
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SET LOCAL search_path=ag_catalog,pg_catalog")
        conn.execute("SELECT ag_catalog.create_graph(%s)", (unrelated,))
    try:
        graphs = physical_graphs(env)
        current, path = prepared(env, artifact_dir)
        result = publish(env, current, path, expected_revision=1)
        after = physical_graphs(env)
        assert set(after) == (set(graphs) - {"pgag_age_" + first.head.id.hex}) | {
            "pgag_age_" + current.head.id.hex,
        }
        assert after[unrelated] == graphs[unrelated]
        assert result.revision == 2 and result.projection.generation_id == current.head.id
        assert_native_matches(env, graph, current.head.id)
    finally:
        with psycopg.connect(env.admin_url) as conn:
            conn.execute("SET LOCAL search_path=ag_catalog,pg_catalog")
            conn.execute("SELECT ag_catalog.drop_graph(%s,true)", (unrelated,))


@pytest.mark.parametrize("disabled", [False, True])
def test_same_generation_republish_and_reenable_rebuild_atomically(env, artifact_dir, disabled):
    graph = graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    first = publish(env, current, path)
    before = physical_graphs(env)
    revision = 1
    if disabled:
        revision = operate(env, "disable", expected_revision=1).revision
    result = publish(env, current, path, expected_revision=revision)
    after = physical_graphs(env)
    assert set(before) == set(after)
    assert result.revision == revision + 1 and result.serving_enabled and result.artifact_verified
    assert result.projection.created_at == first.projection.created_at
    assert result.projection.generation_id == current.head.id
    assert_native_matches(env, graph, current.head.id)


def test_runtime_role_cannot_use_any_admin_operation(env, artifact_dir):
    current, path = prepared(env, artifact_dir)
    before = physical_graphs(env)
    for operation, changes in (
        ("get", {}),
        ("disable", {"expected_revision": 0}),
        ("publish", {
            "expected_revision": 0, "generation_id": current.head.id,
            "expected_generation_revision": current.revision, "file": path,
        }),
    ):
        with pytest.raises(AdminError, match="^admin_role_required$"):
            operate(env, operation, url=env.settings.database_url, **changes)
    assert operate(env).projection is None and physical_graphs(env) == before


def cli(env, *args, url=None):
    return subprocess.run(
        [sys.executable, "-c", "from pg_agmemory.cli import main; main()",
         "age-projection", *args],
        env={**os.environ, "PGAG_ADMIN_DATABASE_URL": url or env.admin_url},
        capture_output=True, text=True, check=False,
    )


def test_cli_publishes_gets_and_disables_json_receipts(env, artifact_dir):
    graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    tenant_flags = ["--tenant-id", str(env.tenants[0])]
    result = cli(
        env, "publish", *tenant_flags, "--expected-revision", "0",
        "--generation-id", str(current.head.id),
        "--expected-generation-revision", str(current.revision), "--file", str(path),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["artifact_verified"] is True
    result = cli(env, "get", *tenant_flags)
    assert result.returncode == 0 and json.loads(result.stdout)["serving_enabled"] is True
    assert json.loads(result.stdout)["artifact_verified"] is False
    result = cli(env, "disable", *tenant_flags, "--expected-revision", "1")
    assert result.returncode == 0 and json.loads(result.stdout)["revision"] == 2
    assert json.loads(result.stdout)["serving_enabled"] is False


def test_cli_errors_are_private_and_machine_readable(env):
    result = cli(env, "get", "--tenant-id", str(env.tenants[0]), url=env.settings.database_url)
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(result.stdout) == {
        "error": {"code": "admin_role_required", "outcome_unknown": False},
    }
    invalid = cli(env, "get", "--tenant-id", "PRIVATE_BAD_TENANT")
    assert invalid.returncode == 2 and invalid.stdout == ""
    assert "invalid_age_projection_arguments" in invalid.stderr
    for output in (result.stdout, result.stderr, invalid.stderr):
        assert "PRIVATE_BAD_TENANT" not in output and "Traceback" not in output
        assert env.admin_url not in output and env.settings.database_url not in output


def test_unqualified_build_is_rejected_before_ddl(env, artifact_dir, monkeypatch):
    current, path = prepared(env, artifact_dir)
    before = physical_graphs(env)
    monkeypatch.setattr(publisher, "AGE_COMMIT", "0" * 40)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_backend_unqualified$"):
            publish(env, current, path)
    assert operate(env).projection is None and physical_graphs(env) == before


def test_unregistered_existing_graph_is_not_overwritten(env, artifact_dir):
    current, path = prepared(env, artifact_dir)
    name = "pgag_age_" + current.head.id.hex
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SET LOCAL search_path=ag_catalog,pg_catalog")
        conn.execute("SELECT ag_catalog.create_graph(%s)", (name,))
    before = physical_graphs(env)
    with pytest.raises(AdminError, match="^graph_projection_exists$"):
        publish(env, current, path)
    assert operate(env).projection is None and physical_graphs(env) == before


def generation_row(env, identifier):
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM memory_ops.graph_generation WHERE tenant_id=%s AND id=%s",
            (env.tenants[0], identifier),
        ).fetchone()


def seed_authenticated_old_generation(env, *, recorded, schema):
    current = execute(env)
    snapshot = generation.GraphInput.model_validate_json(json.dumps(
        current.current_input.model_dump(mode="json") | {"schema_version": schema},
    ))
    identifier = uuid4()
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        digest = generation.input_digest(snapshot, secret_for(conn, env.tenants[0]))
        # Construct a historical receipt through its normal guarded lifecycle.
        conn.execute(
            """INSERT INTO memory_ops.graph_generation
               (tenant_id,id,state,profile_digest,input_digest,input_snapshot)
               VALUES (%s,%s,'building',%s,%s,%s)""",
            (env.tenants[0], identifier, "a" * 64, digest,
             Jsonb(snapshot.model_dump(mode="json"))),
        )
        conn.execute(
            """INSERT INTO memory_ops.graph_generation_state(tenant_id,revision,building_id)
               VALUES (%s,1,%s)""", (env.tenants[0], identifier),
        )
        if recorded:
            conn.execute(
                """UPDATE memory_ops.graph_generation SET state='recorded',artifact_digest=%s
                   WHERE tenant_id=%s AND id=%s""",
                ("b" * 64, env.tenants[0], identifier),
            )
            conn.execute(
                """UPDATE memory_ops.graph_generation_state
                   SET revision=2,head_id=%s,building_id=NULL WHERE tenant_id=%s""",
                (identifier, env.tenants[0]),
            )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return generation_row(env, identifier)


@pytest.mark.parametrize("schema", [19, 20])
def test_authenticated_old_building_can_be_read_abandoned_and_rebuilt(env, artifact_dir, schema):
    graph_fixture(env)
    old = seed_authenticated_old_generation(env, recorded=False, schema=schema)
    current = execute(env)
    assert current.current_input.schema_version == 21
    assert current.building.input_snapshot.schema_version == schema
    assert current.building.input_digest == old["input_digest"]
    assert current.building.source_matches is False
    assert generation_row(env, old["id"]) == old
    with pytest.raises(AdminError, match="^graph_input_changed$"):
        record(env, current)
    assert generation_row(env, old["id"]) == old
    abandoned = abandon(env, current)
    assert abandoned.building is None and abandoned.head is None
    newer, path = prepared(env, artifact_dir)
    assert newer.head.input_snapshot.schema_version == 21 and newer.head.parent_id is None
    assert publish(env, newer, path).serving_enabled
    history = generation_row(env, old["id"])
    assert history["state"] == "abandoned" and history["finished_at"] is not None
    assert history["input_snapshot"]["schema_version"] == schema
    for key in old.keys() - {"state", "abandon_reason", "finished_at"}:
        assert history[key] == old[key]


@pytest.mark.parametrize("schema", [19, 20])
def test_authenticated_old_head_stays_immutable_and_requires_new21_publication(
    env, artifact_dir, monkeypatch, schema,
):
    graph = graph_fixture(env)
    old = seed_authenticated_old_generation(env, recorded=True, schema=schema)
    current = execute(env)
    assert current.head.input_snapshot.schema_version == schema
    assert current.head.source_matches is False and current.current_input.schema_version == 21
    assert generation_row(env, old["id"]) == old
    before = physical_graphs(env)
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_input_changed$"):
            publish(env, current, artifact_dir / "must-not-be-read.json")
    assert physical_graphs(env) == before and operate(env).projection is None
    with pytest.raises(psycopg.errors.CheckViolation, match="must match the recorded head"):
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(
                """INSERT INTO memory_ops.age_projection
                   (tenant_id,generation_id,graph_name,artifact_digest,profile_digest,input_digest,
                    captured_access_epoch,captured_deletion_epoch,age_commit,node_count,
                    edge_revision_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0)""",
                (env.tenants[0], old["id"], "pgag_age_" + old["id"].hex,
                 old["artifact_digest"], old["profile_digest"], old["input_digest"],
                 old["input_snapshot"]["access_epoch"], old["input_snapshot"]["deletion_epoch"],
                 publisher.AGE_COMMIT),
            )
            conn.execute(
                "UPDATE memory_ops.age_projection SET enabled=true,revision=revision+1 "
                "WHERE tenant_id=%s", (env.tenants[0],),
            )
    assert operate(env).projection is None and generation_row(env, old["id"]) == old
    newer, path = prepared(env, artifact_dir)
    assert newer.head.input_snapshot.schema_version == 21 and newer.head.parent_id == old["id"]
    assert publish(env, newer, path).serving_enabled
    assert_native_matches(env, graph, newer.head.id)
    assert generation_row(env, old["id"]) == old


def test_existing_enabled_schema20_receipt_is_stale_until_explicit_disable_and_rebuild(
    env, artifact_dir, monkeypatch,
):
    graph = graph_fixture(env)
    current, path = prepared(env, artifact_dir)
    published = publish(env, current, path)
    old_input = generation.GraphInput.model_validate_json(json.dumps(
        current.head.input_snapshot.model_dump(mode="json") | {"schema_version": 20},
    ))
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        digest = generation.input_digest(old_input, secret_for(conn, env.tenants[0]))
        # Model immutable rows and a physical projection carried through an old-schema restore.
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation DISABLE TRIGGER guard_graph_generation"
        )
        conn.execute("ALTER TABLE memory_ops.age_projection DISABLE TRIGGER guard_age_projection")
        conn.execute(
            """UPDATE memory_ops.graph_generation SET input_snapshot=%s,input_digest=%s
               WHERE tenant_id=%s AND id=%s""",
            (Jsonb(old_input.model_dump(mode="json")), digest,
             env.tenants[0], current.head.id),
        )
        conn.execute(
            """UPDATE memory_ops.age_projection SET input_digest=%s,captured_schema_version=20
               WHERE tenant_id=%s""",
            (digest, env.tenants[0]),
        )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation ENABLE TRIGGER guard_graph_generation"
        )
        conn.execute("ALTER TABLE memory_ops.age_projection ENABLE TRIGGER guard_age_projection")
    old = generation_row(env, current.head.id)
    graphs = physical_graphs(env)
    stale = operate(env)
    assert stale.projection.enabled and stale.revision == published.revision
    assert stale.projection.captured_schema_version == 20
    assert not stale.serving_enabled and not stale.changed
    assert execute(env).head.source_matches is False
    with pytest.raises(MemoryError, match="^graph_projection_stale$"):
        asyncio.run(read_graph(env, graph.request(["source"]), AgeGraph))
    assert asyncio.run(read_graph(env, graph.request(["source"]), SqlGraph))["paths"]
    with forbid_graph_ddl(monkeypatch):
        with pytest.raises(AdminError, match="^graph_projection_stale$"):
            publish(env, current, path, expected_revision=stale.revision)
    assert operate(env) == stale and physical_graphs(env) == graphs
    disabled = operate(env, "disable", expected_revision=stale.revision)
    assert not disabled.projection.enabled and not disabled.serving_enabled
    assert disabled.projection.captured_schema_version == 20
    newer, new_path = prepared(env, artifact_dir)
    assert newer.head.input_snapshot.schema_version == 21
    assert newer.head.parent_id == current.head.id
    assert publish(env, newer, new_path, expected_revision=disabled.revision).serving_enabled
    assert_native_matches(env, graph, newer.head.id)
    assert generation_row(env, current.head.id) == old
