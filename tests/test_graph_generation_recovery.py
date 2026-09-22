"""Generation receipts are recovery comparison evidence, never replaceable state."""

from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql
from test_graph_generation import (
    METADATA_TABLES,
    abandon,
    begin,
    execute,
    metadata_rows,
    record,
)

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


@contextmanager
def reject_recovery_writes(monkeypatch):
    original = psycopg.Connection.execute
    writes = []

    def reject_write(conn, query, *args, **kwargs):
        text = query.as_string(conn) if isinstance(query, sql.Composable) else query
        if text.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            writes.append(text)
            pytest.fail("Recovery began writing before comparing generation metadata")
        return original(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", reject_write)
        yield writes


def test_exact_generation_metadata_is_compared_but_never_exported_as_replacement_rows(env):
    current = record(env, begin(env))
    before = metadata_rows(env)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    for table in METADATA_TABLES:
        assert table in CONTENT_TABLES
        assert table not in ROW_TABLES and table not in REPLACE_TABLES
        assert table not in bundle.rows
        assert next(row.rows for row in bundle.reference.tables if row.table == table) == 1
        assert next(row.rows for row in bundle.content if row.table == table) == 1
    applied = apply_bundle(env.admin_url, bundle.reference, bundle, isolated=True)
    assert applied == bundle.reference and not applied.restore_authorized
    assert metadata_rows(env) == before
    assert execute(env).head == current.head


@pytest.mark.parametrize("newer_state", ["building", "recorded", "abandoned"])
def test_older_generation_bundle_cannot_restore_missing_or_terminal_receipts(
    env, monkeypatch, newer_state
):
    old = export_bundle(env.admin_url, env.tenants[0])
    reserved = begin(env)
    if newer_state == "recorded":
        record(env, reserved)
    elif newer_state == "abandoned":
        abandon(env, reserved)
    current = capture_processing_state(env.admin_url, env.tenants[0])
    before = metadata_rows(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_content_mismatch$"):
            apply_bundle(env.admin_url, current, old, isolated=True)
    assert not writes
    assert metadata_rows(env) == before
    assert capture_processing_state(env.admin_url, env.tenants[0]) == current


def test_latest_bundle_cannot_fill_generation_metadata_missing_from_old_backup(env, monkeypatch):
    old = export_bundle(env.admin_url, env.tenants[0])
    assert metadata_rows(env) == dict.fromkeys(METADATA_TABLES, [])
    recorded = record(env, begin(env))
    latest = export_bundle(env.admin_url, env.tenants[0])
    assert recorded.head.state == "recorded"
    assert latest.reference != old.reference
    guards = (
        ("graph_generation_state", "guard_graph_generation_state"),
        ("graph_generation", "guard_graph_generation"),
    )
    # Simulate loading the old empty metadata, not a supported lifecycle mutation.
    with psycopg.connect(env.admin_url) as conn:
        for table, trigger in guards:
            conn.execute(
                sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(
                    sql.Identifier("memory_ops", table), sql.Identifier(trigger)
                )
            )
        for table, _ in guards:
            assert (
                conn.execute(
                    sql.SQL("DELETE FROM {} WHERE tenant_id=%s").format(
                        sql.Identifier("memory_ops", table)
                    ),
                    (env.tenants[0],),
                ).rowcount
                == 1
            )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for table, trigger in guards:
            conn.execute(
                sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(
                    sql.Identifier("memory_ops", table), sql.Identifier(trigger)
                )
            )
            assert conn.execute(
                "SELECT tgenabled FROM pg_trigger WHERE tgrelid=%s::regclass AND tgname=%s",
                ("memory_ops." + table, trigger),
            ).fetchone() == ("O",)
    restored = capture_processing_state(env.admin_url, env.tenants[0])
    assert restored == old.reference
    assert metadata_rows(env) == dict.fromkeys(METADATA_TABLES, [])
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_content_mismatch$"):
            apply_bundle(env.admin_url, restored, latest, isolated=True)
    assert not writes
    assert metadata_rows(env) == dict.fromkeys(METADATA_TABLES, [])
    assert capture_processing_state(env.admin_url, env.tenants[0]) == restored
    current = execute(env)
    assert current.revision == 0 and current.head is None and current.building is None
