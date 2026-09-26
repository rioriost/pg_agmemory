"""Optional serving receipts require no AGE installation in the ordinary schema."""

from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from test_graph_generation import abandon, begin, record
from test_graph_generation_recovery import reject_recovery_writes

from pg_agmemory.admin import AdminError
from pg_agmemory.processing_recovery import capture_processing_state
from pg_agmemory.recovery_apply import (
    CONTENT_TABLES,
    REPLACE_TABLES,
    ROW_TABLES,
    apply_bundle,
    export_bundle,
)

pytestmark = pytest.mark.integration
TABLE = "memory_ops.age_projection"
COMMIT = "72707aab7ce982bf13cad3d102bd869dab07d64b"


def values(env, generation, **changes):
    return {
        "tenant_id": env.tenants[0],
        "generation_id": generation.id,
        "graph_name": "pgag_age_" + generation.id.hex,
        "artifact_digest": generation.artifact_digest or "b" * 64,
        "profile_digest": generation.profile_digest,
        "input_digest": generation.input_digest,
        "captured_access_epoch": generation.input_snapshot.access_epoch,
        "captured_deletion_epoch": generation.input_snapshot.deletion_epoch,
        "age_commit": COMMIT,
        "node_count": 0,
        "edge_revision_count": 0,
        **changes,
    }


def insert(conn, body):
    return conn.execute(
        sql.SQL("INSERT INTO memory_ops.age_projection ({}) VALUES ({}) RETURNING *").format(
            sql.SQL(",").join(map(sql.Identifier, body)),
            sql.SQL(",").join(sql.Placeholder() for _ in body),
        ), tuple(body.values()),
    ).fetchone()


def projection(env):
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM memory_ops.age_projection WHERE tenant_id=%s", (env.tenants[0],),
        ).fetchone()


def test_registry_is_optional_and_stamps_server_identity(env):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        assert conn.execute("SHOW server_version_num").fetchone()["server_version_num"] == "180006"
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            (TABLE,),
        ).fetchone() == {"relrowsecurity": True, "relforcerowsecurity": True}
        actor = conn.execute("SELECT current_user AS actor,clock_timestamp() AS started").fetchone()
        receipt = insert(conn, values(
            env, head, database_role="forged_actor",
            created_at="2000-01-01Z", updated_at="2000-01-01Z", captured_schema_version=21,
        ))
        assert receipt["enabled"] is False and receipt["revision"] == 1
        assert receipt["captured_schema_version"] == 20
        assert receipt["database_role"] == actor["actor"]
        assert receipt["created_at"] == receipt["updated_at"] >= actor["started"]
        after = conn.execute(
            """UPDATE memory_ops.age_projection SET revision=revision+1,enabled=true,
               database_role='forged_actor',updated_at='2000-01-01Z',captured_schema_version=20
               WHERE tenant_id=%s RETURNING *""", (env.tenants[0],),
        ).fetchone()
        assert after["revision"] == 2 and after["enabled"]
        assert after["captured_schema_version"] == 23
        assert after["database_role"] == actor["actor"]
        assert after["created_at"] == receipt["created_at"]
        assert after["updated_at"] > receipt["updated_at"]


def test_captured_schema_marker_is_visible_without_generation_read_access_or_a_definer(env):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head, enabled=True))
        assert conn.execute(
            "SELECT to_regprocedure('memory_ops.age_projection_schema_current()')"
        ).fetchone() == (None,)
        assert conn.execute(
            "SELECT has_table_privilege('pgag_runtime','memory_ops.graph_generation','SELECT')"
        ).fetchone() == (False,)
    with psycopg.connect(env.settings.database_url) as conn:
        assert conn.execute(
            "SELECT captured_schema_version FROM memory_ops.age_projection"
        ).fetchall() == []
        for tenant, expected in ((env.tenants[1], []), (env.tenants[0], [(23,)])):
            conn.execute("SELECT set_config('pgag.tenant_id',%s,true)", (str(tenant),))
            assert conn.execute(
                "SELECT captured_schema_version FROM memory_ops.age_projection"
            ).fetchall() == expected


@pytest.mark.parametrize("field,value", [
    ("revision", 0), ("revision", 2),
    ("artifact_digest", "A" * 64), ("profile_digest", "a" * 63), ("input_digest", "g" * 64),
    ("captured_access_epoch", 0), ("captured_deletion_epoch", -1),
    ("age_commit", "0" * 40), ("graph_name", "unsafe"),
    ("graph_name", "pgag_age_" + "0" * 32),
    ("node_count", -1), ("node_count", 10001),
    ("edge_revision_count", -1), ("edge_revision_count", 40001),
])
def test_registry_rejects_invalid_fields_even_when_disabled(env, field, value):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            insert(conn, values(env, head, **{field: value}))
    assert projection(env) is None


@pytest.mark.parametrize("field,value", [
    ("artifact_digest", "c" * 64), ("profile_digest", "c" * 64),
    ("input_digest", "c" * 64), ("captured_access_epoch", 2),
    ("captured_deletion_epoch", 2),
])
def test_enabled_projection_must_match_generation_receipt(env, field, value):
    head = record(env, begin(env)).head
    body = values(env, head, **{field: value})
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="must match the recorded head"):
            insert(conn, body | {"enabled": True})
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, body)
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="must match the recorded head"):
            conn.execute(
                "UPDATE memory_ops.age_projection SET enabled=true,revision=revision+1 "
                "WHERE tenant_id=%s", (env.tenants[0],),
            )
    assert projection(env)["revision"] == 1 and not projection(env)["enabled"]


@pytest.mark.parametrize("state", ["building", "abandoned", "superseded"])
def test_only_current_recorded_head_can_be_enabled(env, state):
    reserved = begin(env)
    generation = reserved.building
    if state == "abandoned":
        abandon(env, reserved)
    elif state == "superseded":
        generation = record(env, reserved).head
        record(env, begin(env))
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="must match the recorded head"):
            insert(conn, values(env, generation, enabled=True))
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, generation))
    assert not projection(env)["enabled"]


def test_registry_requires_same_tenant_generation(env):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert(conn, values(env, head, tenant_id=env.tenants[1]))
    missing = uuid4()
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert(conn, values(
                env, head, generation_id=missing, graph_name="pgag_age_" + missing.hex,
            ))


@pytest.mark.parametrize("schema", [18, 19, 20, 21, 22])
def test_older_schema_receipt_cannot_enable_projection(env, schema):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation DISABLE TRIGGER guard_graph_generation"
        )
        conn.execute(
            """UPDATE memory_ops.graph_generation SET input_snapshot=jsonb_set(
               input_snapshot,'{schema_version}',to_jsonb(%s::integer))
               WHERE tenant_id=%s AND id=%s""", (schema, env.tenants[0], head.id),
        )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation ENABLE TRIGGER guard_graph_generation"
        )
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head))
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="must match the recorded head"):
            conn.execute(
                "UPDATE memory_ops.age_projection SET enabled=true,revision=revision+1 "
                "WHERE tenant_id=%s", (env.tenants[0],),
            )
    assert not projection(env)["enabled"]


@pytest.mark.parametrize("change", [
    "revision=revision", "revision=revision+2", "tenant_id=gen_random_uuid(),revision=revision+1",
    "created_at='2000-01-01Z',revision=revision+1",
])
def test_revision_and_immutable_identity_guards(env, change):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head))
    before = projection(env)
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="invalid AGE projection revision"):
            conn.execute(
                sql.SQL("UPDATE memory_ops.age_projection SET {} WHERE tenant_id=%s").format(
                    sql.SQL(change),
                ), (env.tenants[0],),
            )
    assert projection(env) == before


def test_disable_after_head_change_and_replace_are_allowed_but_delete_is_not(env):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head, enabled=True, node_count=10000, edge_revision_count=40000))
    newer = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory_ops.age_projection SET enabled=false,revision=revision+1 "
            "WHERE tenant_id=%s", (env.tenants[0],),
        )
    assert not projection(env)["enabled"]
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "UPDATE memory_ops.age_projection SET enabled=false,revision=revision+1 "
            "WHERE tenant_id=%s AND revision=1", (env.tenants[0],),
        ).rowcount == 0
        replacement = values(env, newer, enabled=True)
        replacement.pop("tenant_id")
        conn.execute(
            sql.SQL("UPDATE memory_ops.age_projection SET {},revision=revision+1 "
                    "WHERE tenant_id=%s AND revision=2").format(
                sql.SQL(",").join(
                    sql.SQL("{}=%s").format(sql.Identifier(key)) for key in replacement
                ),
            ), (*replacement.values(), env.tenants[0]),
        )
    current = projection(env)
    assert current["revision"] == 3 and current["generation_id"] == newer.id and current["enabled"]
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot be deleted"):
            conn.execute("DELETE FROM memory_ops.age_projection WHERE tenant_id=%s",
                         (env.tenants[0],))
    assert projection(env) == current


def test_runtime_is_tenant_select_only_and_guard_is_invoker(env):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head))
        assert conn.execute(
            "SELECT prosecdef,proconfig FROM pg_proc "
            "WHERE oid='memory_ops.guard_age_projection()'::regprocedure",
        ).fetchone() == (False, ["search_path=pg_catalog"])
        assert conn.execute(
            """SELECT has_function_privilege('pgag_runtime',
                   'memory_ops.guard_age_projection()','EXECUTE'),
               has_table_privilege('pgag_runtime','memory_ops.age_projection','SELECT'),
               has_table_privilege('pgag_runtime','memory_ops.age_projection',
                   'INSERT,UPDATE,DELETE,TRUNCATE')"""
        ).fetchone() == (False, True, False)
    with psycopg.connect(env.settings.database_url) as conn:
        assert conn.execute("SELECT tenant_id FROM memory_ops.age_projection").fetchall() == []
        for tenant, expected in ((env.tenants[1], []), (env.tenants[0], [(env.tenants[0],)])):
            conn.execute("SELECT set_config('pgag.tenant_id',%s,true)", (str(tenant),))
            assert conn.execute("SELECT tenant_id FROM memory_ops.age_projection").fetchall() == (
                expected
            )
        for query in (
            "UPDATE memory_ops.age_projection SET revision=revision+1",
            "DELETE FROM memory_ops.age_projection",
            "TRUNCATE memory_ops.age_projection",
            "INSERT INTO memory_ops.age_projection SELECT * FROM memory_ops.age_projection",
            "SELECT memory_ops.guard_age_projection()",
        ):
            with conn.transaction():
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():
                        conn.execute(query)


def test_active_registry_blocks_recovery_before_any_write_even_when_exact(env, monkeypatch):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head, enabled=True))
    bundle = export_bundle(env.admin_url, env.tenants[0])
    before = projection(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_active_graph_projection$"):
            apply_bundle(env.admin_url, bundle.reference, bundle, isolated=True)
    assert not writes and projection(env) == before
    assert capture_processing_state(env.admin_url, env.tenants[0]) == bundle.reference


def test_inactive_registry_is_strict_comparison_only_and_latest_export_is_required(
    env, monkeypatch,
):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head, enabled=True))
    active = export_bundle(env.admin_url, env.tenants[0])
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory_ops.age_projection SET enabled=false,revision=revision+1 "
            "WHERE tenant_id=%s", (env.tenants[0],),
        )
    inactive = export_bundle(env.admin_url, env.tenants[0])
    assert TABLE in CONTENT_TABLES
    assert TABLE not in ROW_TABLES and TABLE not in REPLACE_TABLES and TABLE not in inactive.rows
    assert next(row.rows for row in inactive.reference.tables if row.table == TABLE) == 1
    assert next(row.rows for row in inactive.content if row.table == TABLE) == 1
    before = projection(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_state_conflict$"):
            apply_bundle(env.admin_url, active.reference, active, isolated=True)
        with pytest.raises(AdminError, match="^recovery_content_mismatch$"):
            apply_bundle(env.admin_url, inactive.reference, active, isolated=True)
    assert not writes and projection(env) == before
    applied = apply_bundle(env.admin_url, inactive.reference, inactive, isolated=True)
    assert applied == inactive.reference and projection(env) == before
