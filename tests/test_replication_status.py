"""Replication observations remain read-only, private, and explicitly non-authoritative."""

import builtins
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError
from test_operations_status import canonical_state

from pg_agmemory import __version__
from pg_agmemory import replication_status as status
from pg_agmemory.admin import AdminError
from pg_agmemory.database import SCHEMA_VERSION, VECTOR_QUERY

INSTANT = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
WARNING_ORDER = (
    "standby_replay_paused",
    "standby_receiver_not_streaming",
    "synchronous_standby_missing",
    "session_commit_not_remote_apply",
    "inactive_replication_slots",
)
NON_AUTHORITY = (
    "statistics_atomic", "writer_policy_verified", "fencing_verified",
    "promotion_authorized", "production_qualified",
)
PRIVATE = "PRIVATE_REPLICATION_DIAGNOSTIC"


def settings_row(**changes):
    return {
        "evaluated_at": INSTANT, "in_recovery": False, "transaction_read_only": True,
        "synchronous_commit": "on", "synchronous_standby_configured": False,
        "primary_flush_lsn": "0/16B6A80", "received_lsn": None, "replayed_lsn": None,
        "replay_paused": None, **changes,
    }


def statistics_row(**changes):
    return {
        "senders_total": 0, "senders_physical_streaming": 0,
        "senders_physical_synchronous": 0, "senders_logical": 0,
        "receiver_present": False, "receiver_streaming": False,
        "slots_total": 0, "slots_physical": 0, "slots_logical": 0, "slots_inactive": 0,
        **changes,
    }


def install_rows(monkeypatch, settings, statistics):
    connection = SimpleNamespace(closed=False, queries=[])

    def execute(query):
        connection.queries.append(query)
        rows = {status.SETTINGS_QUERY: settings, status.STATISTICS_QUERY: statistics}
        assert query in rows
        return SimpleNamespace(fetchone=lambda: rows[query])

    connection.execute = execute

    @contextmanager
    def snapshot(url):
        assert url == PRIVATE
        try:
            yield connection
        finally:
            connection.closed = True

    monkeypatch.setattr(status, "read_admin_snapshot", snapshot)
    return connection


def observed_model(monkeypatch):
    connection = install_rows(monkeypatch, settings_row(), statistics_row())
    result = status.replication_status(PRIVATE)
    assert connection.closed
    return result


def assert_no_authority(report):
    for field in NON_AUTHORITY:
        assert getattr(report, field) is False
    assert report.warnings == tuple(code for code in WARNING_ORDER if code in report.warnings)


def assert_private(serialized, *secrets):
    for value in (PRIVATE, *secrets):
        assert str(value) not in serialized
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", serialized,
    )
    for key in (
        "tenant_id", "principal_id", "application_name", "client_addr", "client_hostname",
        "usename", "username", "slot_name", "conninfo", "sender_host", "sender_port",
        "synchronous_standby_names", "dsn", "payload",
    ):
        assert f'"{key}"' not in serialized


def lsn_value(value):
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def real_statistics(conn):
    senders = conn.execute(
        """SELECT count(*),
                  count(*) FILTER (WHERE state='streaming' AND NOT EXISTS (
                      SELECT 1 FROM pg_replication_slots s
                      WHERE s.active_pid=r.pid AND s.slot_type='logical')),
                  count(*) FILTER (WHERE state='streaming' AND sync_state IN ('sync','quorum')
                      AND NOT EXISTS (SELECT 1 FROM pg_replication_slots s
                          WHERE s.active_pid=r.pid AND s.slot_type='logical')),
                  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM pg_replication_slots s
                      WHERE s.active_pid=r.pid AND s.slot_type='logical'))
           FROM pg_stat_replication r"""
    ).fetchone()
    receiver = conn.execute(
        "SELECT count(*)>0,coalesce(bool_or(status='streaming'),false) FROM pg_stat_wal_receiver"
    ).fetchone()
    slots = conn.execute(
        """SELECT count(*),count(*) FILTER (WHERE slot_type='physical'),
                  count(*) FILTER (WHERE slot_type='logical'),count(*) FILTER (WHERE NOT active)
           FROM pg_replication_slots"""
    ).fetchone()
    return {
        "senders": dict(zip(
            ("total", "physical_streaming", "physical_synchronous", "logical"), senders,
            strict=True,
        )),
        "receiver": dict(zip(("present", "streaming"), receiver, strict=True)),
        "slots": dict(zip(("total", "physical", "logical", "inactive"), slots, strict=True)),
    }


@contextmanager
def owned_slot(url, kind):
    name = "pgag_status_private_" + uuid4().hex
    with psycopg.connect(
        url, autocommit=True, options="-c statement_timeout=5000 -c lock_timeout=5000",
    ) as conn:
        level, capacity, used = conn.execute(
            """SELECT current_setting('wal_level'),current_setting('max_replication_slots')::int,
                      (SELECT count(*) FROM pg_replication_slots)"""
        ).fetchone()
        if level == "minimal" or used >= capacity:
            pytest.skip("Disposable database has no safe replication-slot capacity")
        if kind == "logical" and level != "logical":
            pytest.skip("Logical slot observation requires wal_level=logical")
        created = False
        try:
            if kind == "physical":
                conn.execute(
                    "SELECT slot_name FROM pg_create_physical_replication_slot(%s,false,true)",
                    (name,),
                )
            else:
                try:
                    conn.execute(
                        "SELECT slot_name FROM pg_create_logical_replication_slot("
                        "%s,'test_decoding',true)", (name,),
                    )
                except psycopg.errors.UndefinedFile:
                    pytest.skip("Optional test_decoding output plugin is unavailable")
            created = True
            row = conn.execute(
                "SELECT slot_type,temporary,active,restart_lsn FROM pg_replication_slots "
                "WHERE slot_name=%s", (name,),
            ).fetchone()
            assert row[:2] == (kind, True)
            if kind == "physical":
                assert row[3] is None
            yield name, not row[2]
        finally:
            if created:
                conn.execute("SELECT pg_drop_replication_slot(%s)", (name,))


def test_primary_database_exact_contract_real_counts_and_local_settings(database):
    url = database[0]
    with psycopg.connect(url) as conn:
        before, flush_before, commit, configured = conn.execute(
            """SELECT clock_timestamp(),pg_current_wal_flush_lsn()::text,
                      current_setting('synchronous_commit'),
                      current_setting('synchronous_standby_names')<>''"""
        ).fetchone()
        expected = real_statistics(conn)
    report = status.replication_status(url)
    with psycopg.connect(url) as conn:
        after, flush_after = conn.execute(
            "SELECT clock_timestamp(),pg_current_wal_flush_lsn()::text"
        ).fetchone()
    assert isinstance(report, status.ReplicationStatus)
    assert not hasattr(report, "__enter__")
    assert before <= report.evaluated_at <= after
    assert report.evaluated_at.utcoffset() == timedelta(0)
    assert lsn_value(flush_before) <= lsn_value(report.primary_flush_lsn) <= lsn_value(flush_after)
    assert report.model_dump(exclude={"evaluated_at", "primary_flush_lsn", "warnings"}) == {
        "format": "pgag-replication-status-v1",
        "service_version": __version__, "api_version": "v1", "schema_version": 21,
        "in_recovery": False, "transaction_read_only": True,
        "synchronous_commit": commit, "synchronous_standby_configured": configured,
        "received_lsn": None, "replayed_lsn": None, "replay_paused": None,
        **expected, **dict.fromkeys(NON_AUTHORITY, False),
    }
    warnings = []
    if configured and expected["senders"]["physical_synchronous"] == 0:
        warnings.append("synchronous_standby_missing")
    if commit != "remote_apply":
        warnings.append("session_commit_not_remote_apply")
    if expected["slots"]["inactive"]:
        warnings.append("inactive_replication_slots")
    assert report.warnings == tuple(warnings)
    assert report.schema_version == SCHEMA_VERSION
    assert status.ReplicationStatus.model_validate_json(report.model_dump_json()) == report
    assert_no_authority(report)
    assert_private(report.model_dump_json(), url)


@pytest.mark.parametrize("kind", ["physical", "logical"])
def test_real_slots_are_counted_privately_and_only_owned_slots_are_removed(database, kind):
    url = database[0]
    with psycopg.connect(url) as conn:
        before = real_statistics(conn)["slots"]
    with owned_slot(url, kind) as (name, inactive):
        with psycopg.connect(url) as conn:
            expected = real_statistics(conn)["slots"]
        report = status.replication_status(url)
        assert report.slots.model_dump() == expected == {
            **before, "total": before["total"] + 1, kind: before[kind] + 1,
            "inactive": before["inactive"] + int(inactive),
        }
        assert ("inactive_replication_slots" in report.warnings) == (
            before["inactive"] + int(inactive) > 0
        )
        assert_private(report.model_dump_json(), name, url)
        assert_no_authority(report)
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM pg_replication_slots WHERE slot_name=%s", (name,),
        ).fetchone() == (0,)
        assert real_statistics(conn)["slots"] == before


def test_production_sql_distinguishes_logical_senders_and_sync_quorum_with_real_aggregates(
    database, monkeypatch,
):
    fixtures = """
    WITH fixture_senders(pid,state,sync_state,application_name,client_addr,usename) AS (
        SELECT pid,state,sync_state,'PRIVATE_REPLICATION_APPLICATION',
               '192.0.2.51','PRIVATE_REPLICATION_USER'
        FROM (VALUES (11,'streaming','sync'),(12,'streaming','quorum'),
                     (13,'streaming','async'),(14,'startup','sync'),
                     (15,'streaming','sync'),(16,'catchup','async'),
                     (17,'streaming','potential'),(18,'backup','sync')
             ) t(pid,state,sync_state)
    ), fixture_slots(slot_type,active_pid,active,slot_name) AS (
        SELECT slot_type,active_pid,active,'PRIVATE_REPLICATION_SLOT'
        FROM (VALUES ('physical',11,true),('physical',13,true),('physical',14,true),
                     ('logical',15,true),('logical',16,true),('physical',17,true),
                     ('physical',NULL,false),('logical',NULL,false)
             ) t(slot_type,active_pid,active)
    ), fixture_receiver(status,conninfo) AS (
        VALUES ('streaming','PRIVATE_REPLICATION_CONNECTION_INFO')
    ),
    """
    production = status.STATISTICS_QUERY.strip()
    assert production.startswith("WITH ")
    query = fixtures + production.removeprefix("WITH ")
    for catalog, replacement in (
        ("pg_catalog.pg_stat_replication", "fixture_senders"),
        ("pg_catalog.pg_replication_slots", "fixture_slots"),
        ("pg_catalog.pg_stat_wal_receiver", "fixture_receiver"),
    ):
        assert catalog in query
        query = query.replace(catalog, replacement)
    original = psycopg.Connection.execute

    def execute(conn, statement, *args, **kwargs):
        if statement == status.STATISTICS_QUERY:
            statement = query
        return original(conn, statement, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    report = status.replication_status(database[0])
    assert report.senders.model_dump() == {
        "total": 8, "physical_streaming": 4, "physical_synchronous": 2, "logical": 2,
    }
    assert report.receiver.model_dump() == {"present": True, "streaming": True}
    assert report.slots.model_dump() == {"total": 8, "physical": 5, "logical": 3, "inactive": 2}
    assert_private(
        report.model_dump_json(), "PRIVATE_REPLICATION_APPLICATION", "192.0.2.51",
        "PRIVATE_REPLICATION_USER", "PRIVATE_REPLICATION_SLOT",
        "PRIVATE_REPLICATION_CONNECTION_INFO",
    )
    assert_no_authority(report)


@pytest.mark.parametrize("commit", ["off", "local", "remote_write", "on", "remote_apply"])
def test_session_synchronous_commit_is_an_observation_not_verified_writer_policy(
    database, monkeypatch, commit,
):
    original = status.read_admin_snapshot

    @contextmanager
    def snapshot(url):
        with original(url) as conn:
            conn.execute(sql.SQL("SET LOCAL synchronous_commit={}").format(sql.Literal(commit)))
            yield conn

    monkeypatch.setattr(status, "read_admin_snapshot", snapshot)
    report = status.replication_status(database[0])
    assert report.synchronous_commit == commit
    assert ("session_commit_not_remote_apply" in report.warnings) == (commit != "remote_apply")
    assert_no_authority(report)


@pytest.mark.parametrize("settings,statistics,warnings", [
    (
        settings_row(synchronous_standby_configured=True),
        statistics_row(slots_total=1, slots_physical=1, slots_inactive=1),
        ("synchronous_standby_missing", "session_commit_not_remote_apply",
         "inactive_replication_slots"),
    ),
    (
        settings_row(synchronous_standby_configured=True, synchronous_commit="remote_apply"),
        statistics_row(senders_total=1, senders_logical=1),
        ("synchronous_standby_missing",),
    ),
    (
        settings_row(synchronous_standby_configured=True, synchronous_commit="remote_apply"),
        statistics_row(senders_total=1, senders_physical_streaming=1,
                       senders_physical_synchronous=1),
        (),
    ),
    (
        settings_row(
            in_recovery=True, primary_flush_lsn=None, received_lsn="1/20",
            replayed_lsn="1/10", replay_paused=True, synchronous_standby_configured=True,
        ),
        statistics_row(slots_total=1, slots_logical=1, slots_inactive=1),
        ("standby_replay_paused", "standby_receiver_not_streaming", "inactive_replication_slots"),
    ),
    (
        settings_row(
            in_recovery=True, primary_flush_lsn=None, received_lsn=None,
            replayed_lsn=None, replay_paused=False,
        ),
        statistics_row(receiver_present=True),
        ("standby_receiver_not_streaming",),
    ),
    (
        settings_row(
            in_recovery=True, primary_flush_lsn=None, received_lsn="1/20",
            replayed_lsn="1/20", replay_paused=False,
            synchronous_commit="off", synchronous_standby_configured=True,
        ),
        statistics_row(receiver_present=True, receiver_streaming=True),
        (),
    ),
])
def test_structured_role_branches_fixed_warning_order_and_no_authority(
    monkeypatch, settings, statistics, warnings,
):
    connection = install_rows(monkeypatch, settings, statistics)
    report = status.replication_status(PRIVATE)
    assert connection.closed
    assert connection.queries == [status.SETTINGS_QUERY, status.STATISTICS_QUERY]
    assert report.in_recovery == settings["in_recovery"]
    assert report.evaluated_at == settings["evaluated_at"]
    assert report.primary_flush_lsn == settings["primary_flush_lsn"]
    assert report.received_lsn == settings["received_lsn"]
    assert report.replayed_lsn == settings["replayed_lsn"]
    assert report.replay_paused == settings["replay_paused"]
    assert report.warnings == warnings
    assert_no_authority(report)
    assert_private(report.model_dump_json())


def test_inactive_standby_function_branches_are_guarded_by_case_on_a_real_primary(database):
    query = status.SETTINGS_QUERY
    for function, condition in (
        ("pg_current_wal_flush_lsn", r"NOT\s+local_role\.in_recovery"),
        ("pg_last_wal_receive_lsn", r"local_role\.in_recovery"),
        ("pg_last_wal_replay_lsn", r"local_role\.in_recovery"),
        ("pg_is_wal_replay_paused", r"local_role\.in_recovery"),
    ):
        assert re.search(rf"CASE\s+WHEN\s+{condition}\s+THEN\s+{function}\(", query, re.I)
    assert status.replication_status(database[0]).replay_paused is None


@pytest.mark.parametrize("model,body", [
    (status.SenderStatus, {"total": -1, "physical_streaming": 0, "physical_synchronous": 0,
                           "logical": 0}),
    (status.SenderStatus, {"total": True, "physical_streaming": 0, "physical_synchronous": 0,
                           "logical": 0}),
    (status.SenderStatus, {"total": 1, "physical_streaming": 2, "physical_synchronous": 0,
                           "logical": 0}),
    (status.SenderStatus, {"total": 2, "physical_streaming": 1, "physical_synchronous": 2,
                           "logical": 0}),
    (status.SenderStatus, {"total": 2, "physical_streaming": 2, "physical_synchronous": 1,
                           "logical": 1}),
    (status.ReceiverStatus, {"present": False, "streaming": True}),
    (status.ReceiverStatus, {"present": "true", "streaming": False}),
    (status.SlotStatus, {"total": 2, "physical": 1, "logical": 0, "inactive": 0}),
    (status.SlotStatus, {"total": 1, "physical": 1, "logical": 0, "inactive": 2}),
    (status.SlotStatus, {"total": 1, "physical": 1, "logical": 0, "inactive": -1}),
    (status.SlotStatus, {"total": "1", "physical": 1, "logical": 0, "inactive": 0}),
])
def test_counter_models_reject_negative_coerced_and_impossible_metadata(model, body):
    with pytest.raises(ValidationError):
        model.model_validate(body)


def test_models_are_frozen_strict_extra_forbidden_revalidated_and_hide_inputs(monkeypatch):
    report = observed_model(monkeypatch)
    for model in (report, report.senders, report.receiver, report.slots):
        config = model.model_config
        assert config["frozen"] and config["strict"] and config["hide_input_in_errors"]
        assert config["extra"] == "forbid" and config["revalidate_instances"] == "always"
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        with pytest.raises(ValidationError):
            type(model).model_validate(model.model_dump() | {"conninfo": PRIVATE})
    forged = report.model_copy(update={
        "senders": status.SenderStatus.model_construct(
            total=-1, physical_streaming=0, physical_synchronous=0, logical=0,
        ),
    })
    with pytest.raises(ValidationError):
        status.ReplicationStatus.model_validate(forged)
    with pytest.raises(ValidationError) as error:
        status.ReplicationStatus.model_validate(
            report.model_dump() | {"synchronous_commit": PRIVATE},
        )
    assert PRIVATE not in str(error.value)


@pytest.mark.parametrize("field", NON_AUTHORITY)
def test_observation_cannot_be_elevated_into_an_authority_claim(monkeypatch, field):
    report = observed_model(monkeypatch)
    with pytest.raises(ValidationError):
        status.ReplicationStatus.model_validate(report.model_dump() | {field: True})


@pytest.mark.parametrize("value", [
    "", "0", "/0", "0/", "0/abc", "g/0", "000000000/0", "0/000000000", 1, True, PRIVATE,
])
def test_lsn_requires_bounded_uppercase_hex_components(monkeypatch, value):
    report = observed_model(monkeypatch)
    with pytest.raises(ValidationError):
        status.ReplicationStatus.model_validate(report.model_dump() | {"primary_flush_lsn": value})


def test_report_timestamps_are_aware_utc_and_primary_standby_fields_do_not_mix(monkeypatch):
    report = observed_model(monkeypatch)
    body = report.model_dump()
    local = INSTANT.astimezone(timezone(timedelta(hours=5, minutes=30)))
    parsed = status.ReplicationStatus.model_validate(body | {"evaluated_at": local})
    assert parsed.evaluated_at == INSTANT and parsed.evaluated_at.utcoffset() == timedelta(0)
    for changes in (
        {"evaluated_at": INSTANT.replace(tzinfo=None)},
        {"transaction_read_only": False},
        {"primary_flush_lsn": None},
        {"received_lsn": "0/0"},
        {"replayed_lsn": "FFFFFFFF/FFFFFFFF"},
        {"replay_paused": False},
        {"in_recovery": True},
        {"in_recovery": True, "primary_flush_lsn": None, "replay_paused": None},
    ):
        with pytest.raises(ValidationError):
            status.ReplicationStatus.model_validate(body | changes)
    for lsn in ("0/0", "FFFFFFFF/FFFFFFFF", "00000000/00000000"):
        assert status.ReplicationStatus.model_validate(body | {"primary_flush_lsn": lsn})


@pytest.mark.parametrize("section,changes", [
    ("settings", None), ("statistics", None),
    ("settings", {"synchronous_commit": PRIVATE}),
    ("settings", {"primary_flush_lsn": PRIVATE}),
    ("settings", {"transaction_read_only": False}),
    ("settings", {"evaluated_at": INSTANT.replace(tzinfo=None)}),
    ("statistics", {"senders_total": -1}),
    ("statistics", {"receiver_streaming": True}),
    ("statistics", {"slots_inactive": 1}),
])
def test_invalid_result_is_redacted_admin_error_not_partial_success(
    monkeypatch, capsys, section, changes,
):
    settings, statistics = settings_row(), statistics_row()
    if section == "settings":
        settings = None if changes is None else settings | changes
    else:
        statistics = None if changes is None else statistics | changes
    connection = install_rows(monkeypatch, settings, statistics)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", PRIVATE)
    with pytest.raises(SystemExit) as error:
        status.main([])
    assert error.value.code == 1 and connection.closed
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "error": {"code": "replication_status_invalid_metadata", "outcome_unknown": False},
    }
    assert_private(output.out)


def test_actual_observer_is_readonly_bounded_no_locks_no_mutations_and_leaves_state_unchanged(
    env, monkeypatch,
):
    assert env.observe(PRIVATE).status_code == 201
    before = canonical_state(env)
    original_execute = psycopg.Connection.execute
    original_connect = psycopg.connect
    connected, queries, settings, connections = [], [], [], []
    with psycopg.connect(env.admin_url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        with holder.transaction():
            holder.execute("SELECT id FROM memory.tenant WHERE id=%s FOR UPDATE", (env.tenants[0],))

            def connect(*args, **kwargs):
                connected.append(kwargs)
                conn = original_connect(*args, **kwargs)
                connections.append(conn)
                return conn

            def execute(conn, query, *args, **kwargs):
                text = query.as_string(conn) if isinstance(query, sql.Composable) else query
                queries.append(text)
                assert not re.search(
                    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP)\b", text, re.I,
                )
                assert not re.search(r"\bFOR\s+(SHARE|KEY|NO)\b", text, re.I)
                assert "pg_advisory" not in text
                if query == status.SETTINGS_QUERY:
                    settings.append(original_execute(
                        conn,
                        """SELECT current_setting('transaction_read_only') AS read_only,
                                  current_setting('default_transaction_read_only') AS default_ro,
                                  current_setting('transaction_isolation') AS isolation,
                                  current_setting('TimeZone') AS timezone,
                                  current_setting('statement_timeout') AS statement_timeout,
                                  current_setting('lock_timeout') AS lock_timeout,
                                  (SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid()
                                   AND locktype='advisory') AS advisory_locks""",
                    ).fetchone())
                return original_execute(conn, query, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(psycopg, "connect", connect)
                patch.setattr(psycopg.Connection, "execute", execute)
                report = status.replication_status(env.admin_url)
    assert settings == [{
        "read_only": "on", "default_ro": "on", "isolation": "repeatable read", "timezone": "UTC",
        "statement_timeout": "5s", "lock_timeout": "5s", "advisory_locks": 0,
    }]
    assert len(connected) == 1 and connected[0]["connect_timeout"] == 5
    assert connected[0]["autocommit"] is True
    assert "-c default_transaction_read_only=on" in connected[0]["options"]
    assert all(conn.closed for conn in connections)
    assert status.SETTINGS_QUERY in queries and status.STATISTICS_QUERY in queries
    assert re.search(r"\bcount\s*\(\s*\*\s*\)", status.STATISTICS_QUERY, re.I)
    assert not any(re.search(r"\b(TABLESAMPLE|reltuples|LIMIT)\b", q, re.I) for q in queries)
    assert canonical_state(env) == before
    assert_private(report.model_dump_json(), env.admin_url, *env.tenants, *env.principals)


def test_one_database_clock_and_closed_connection_before_return_and_cli_print(
    database, monkeypatch, capsys,
):
    original_execute = psycopg.Connection.execute
    original_snapshot = status.read_admin_snapshot
    clocks, connections = [], []

    class NoPythonClock(datetime):
        @classmethod
        def now(cls, tz=None):
            pytest.fail("Replication observer used the Python wall clock")

        @classmethod
        def utcnow(cls):
            pytest.fail("Replication observer used the Python wall clock")

    class RememberClock:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            clocks.append(row["evaluated_at"])
            return row

    def execute(conn, query, *args, **kwargs):
        cursor = original_execute(conn, query, *args, **kwargs)
        if isinstance(query, str) and "clock_timestamp()" in query:
            assert query.count("clock_timestamp()") == 1
            return RememberClock(cursor)
        return cursor

    @contextmanager
    def snapshot(url):
        with original_snapshot(url) as conn:
            connections.append(conn)
            yield conn

    def print_after_close(*args, **kwargs):
        assert connections and all(conn.closed for conn in connections)
        builtins.print(*args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    monkeypatch.setattr(status, "datetime", NoPythonClock)
    monkeypatch.setattr(status, "read_admin_snapshot", snapshot)
    report = status.replication_status(database[0])
    assert connections[0].closed and clocks == [report.evaluated_at]
    monkeypatch.setattr(status, "print", print_after_close, raising=False)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", database[0])
    assert status.main([]) is None
    output = capsys.readouterr()
    assert output.err == ""
    value = json.loads(output.out)
    assert datetime.fromisoformat(value["evaluated_at"].replace("Z", "+00:00")) == clocks[1]
    assert len(clocks) == 2
    assert_private(output.out, database[0])


def test_runtime_role_cannot_read_the_cluster_operator_report(database, monkeypatch, capsys):
    with pytest.raises(AdminError, match="^admin_role_required$") as error:
        status.replication_status(database[1])
    assert error.value.outcome_unknown is False
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", database[1])
    with pytest.raises(SystemExit) as exit_error:
        status.main([])
    assert exit_error.value.code == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "error": {"code": "admin_role_required", "outcome_unknown": False},
    }
    assert_private(output.out, database[1])


@pytest.mark.parametrize("case,code", [
    ("missing_schema", "schema_unavailable"),
    ("ledger_gap", "schema_version_mismatch"),
    ("ledger_future", "schema_version_mismatch"),
    ("vector_version", "extension_version_mismatch"),
    ("vector_schema", "extension_version_mismatch"),
    ("vector_missing", "extension_version_mismatch"),
])
def test_complete_ledger_and_vector_pin_refuse_before_probing_catalogs(
    database, monkeypatch, capsys, case, code,
):
    original = psycopg.Connection.execute
    intercepted = []

    def execute(conn, query, *args, **kwargs):
        assert query not in (status.SETTINGS_QUERY, status.STATISTICS_QUERY)
        if isinstance(query, str) and "FROM public.pgag_schema_migration" in query:
            if case == "missing_schema":
                intercepted.append(case)
                return original(conn, "SELECT * FROM memory_ops.PRIVATE_REPLICATION_ABSENT")
            if case == "ledger_gap":
                intercepted.append(case)
                return original(
                    conn, "SELECT version FROM public.pgag_schema_migration "
                    "WHERE version<>2 ORDER BY version",
                )
            if case == "ledger_future":
                intercepted.append(case)
                return original(
                    conn, "SELECT version FROM public.pgag_schema_migration "
                    "UNION ALL SELECT %s ORDER BY version", (SCHEMA_VERSION + 1,),
                )
        if query == VECTOR_QUERY and case.startswith("vector_"):
            intercepted.append(case)
            if case == "vector_version":
                return original(
                    conn, "SELECT 'PRIVATE_REPLICATION_VERSION' AS extversion,'public' AS nspname",
                )
            if case == "vector_schema":
                return original(
                    conn, "SELECT extversion,'PRIVATE_REPLICATION_SCHEMA' AS nspname "
                    "FROM pg_extension WHERE extname='vector'",
                )
            return original(conn, "SELECT extversion,'public' AS nspname FROM pg_extension "
                                  "WHERE extname='PRIVATE_REPLICATION_ABSENT'")
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", database[0])
    with pytest.raises(SystemExit) as error:
        status.main([])
    assert error.value.code == 1 and intercepted == [case]
    output = capsys.readouterr()
    assert output.err == "" and "PRIVATE_REPLICATION" not in output.out
    assert json.loads(output.out) == {"error": {"code": code, "outcome_unknown": False}}


@pytest.mark.parametrize("failure,code", [
    (psycopg.OperationalError, "admin_database_unavailable"),
    (psycopg.errors.QueryCanceled, "admin_database_unavailable"),
    (psycopg.errors.InsufficientPrivilege, "admin_privilege_required"),
    (psycopg.errors.UndefinedTable, "schema_unavailable"),
    (psycopg.DataError, "admin_database_error"),
])
def test_sql_failures_never_expose_private_diagnostics_or_an_empty_success(
    database, monkeypatch, capsys, failure, code,
):
    original = psycopg.Connection.execute

    def execute(conn, query, *args, **kwargs):
        if query == status.STATISTICS_QUERY:
            raise failure(
                f"{PRIVATE} host=192.0.2.51 user=PRIVATE_REPLICATION_USER "
                "password=PRIVATE_REPLICATION_PASSWORD slot=PRIVATE_REPLICATION_SLOT"
            )
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", database[0])
    with pytest.raises(SystemExit) as error:
        status.main([])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {"error": {"code": code, "outcome_unknown": False}}
    assert_private(
        output.out, database[0], "192.0.2.51", "PRIVATE_REPLICATION_USER",
        "PRIVATE_REPLICATION_PASSWORD", "PRIVATE_REPLICATION_SLOT",
    )


@pytest.mark.parametrize("arguments", [
    ["PRIVATE_REPLICATION_ARGUMENT"],
    ["--tenant-id", str(uuid4())],
    ["--database-url", PRIVATE],
    ["--slot-name", "PRIVATE_REPLICATION_SLOT"],
    ["--promote"],
])
def test_cli_rejects_arguments_redacted_before_connection(monkeypatch, capsys, arguments):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid replication-status arguments reached the database")

    monkeypatch.setattr(status, "replication_status", forbidden)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", PRIVATE)
    with pytest.raises(SystemExit) as error:
        status.main(arguments)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "invalid_replication_status_arguments" in output.err
    assert "PRIVATE_REPLICATION" not in output.err and "Traceback" not in output.err


@pytest.mark.parametrize("url", [None, "", " \t"])
def test_cli_requires_admin_url_without_falling_back_to_runtime(monkeypatch, capsys, url):
    def forbidden(*args, **kwargs):
        pytest.fail("Missing ADMIN URL used another connection")

    monkeypatch.setattr(status, "replication_status", forbidden)
    monkeypatch.setenv("PGAG_DATABASE_URL", PRIVATE)
    if url is None:
        monkeypatch.delenv("PGAG_ADMIN_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", url)
    with pytest.raises(SystemExit) as error:
        status.main([])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "PGAG_ADMIN_DATABASE_URL is required" in output.err
    assert_private(output.err)


@pytest.mark.parametrize("url", [
    "postgresql://PRIVATE_REPLICATION_USER:PRIVATE_REPLICATION_PASSWORD@127.0.0.1:1/private",
    "PRIVATE_REPLICATION_MALFORMED_DSN",
])
def test_cli_connection_errors_are_redacted_without_tracebacks(monkeypatch, capsys, url):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", url)
    with pytest.raises(SystemExit) as error:
        status.main([])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.err == "" and "PRIVATE_REPLICATION" not in output.out
    value = json.loads(output.out)
    assert set(value) == {"error"}
    assert value["error"]["code"] in {"admin_database_unavailable", "admin_database_error"}
    assert value["error"]["outcome_unknown"] is False
    assert "Traceback" not in output.out


def test_cli_dispatch_success_is_json_and_warnings_do_not_change_exit_status(database):
    completed = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "replication-status"],
        env={"PATH": os.environ["PATH"], "PGAG_ADMIN_DATABASE_URL": database[0]},
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0 and completed.stderr == ""
    report = status.ReplicationStatus.model_validate_json(completed.stdout)
    assert report.format == "pgag-replication-status-v1"
    if report.synchronous_commit != "remote_apply":
        assert "session_commit_not_remote_apply" in report.warnings
    assert_no_authority(report)
    assert_private(completed.stdout, database[0])


def test_probe_import_does_not_require_optional_sdk_or_network_client_packages():
    program = """
import builtins

original = builtins.__import__

def core_only(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split('.')[0] in {'mcp', 'langgraph', 'httpx'} or name == 'pg_agmemory.sdk':
        raise AssertionError('Replication observer imported an optional SDK dependency')
    return original(name, globals, locals, fromlist, level)

builtins.__import__ = core_only
import pg_agmemory.replication_status
"""
    completed = subprocess.run(
        [sys.executable, "-c", program], env={"PATH": os.environ["PATH"]},
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
