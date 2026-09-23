"""Real five-second COMMIT acknowledgement deadlines, not cancellation tests.

Run against an owned disposable PostgreSQL primary started with
``-c synchronous_standby_names=pgag_missing -c synchronous_commit=local``.
Only the target transactions request remote_apply; no cluster/role settings are
changed. Observers never cancel COMMIT. Named-backend termination is teardown,
or a 15-second test-failure failsafe, never the expected five-second exit.

The TCP proxy drops actual COMMIT responses and blackholes additional connections
(including cancellation channels). It needs only local sockets, not a firewall.
These tests establish bounded uncertainty and local persistence, not failover
durability or permission to retry.
"""

import asyncio
import select
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_commit_outcomes import commit_rows as commit_rows
from test_commit_outcomes import (
    control_connection,
    episode_and_receipt_counts,
    observe_body,
)
from test_commit_outcomes import missing_standby as missing_standby
from test_processing import configure, enqueue, install_extraction
from test_processing import profile as profile

from pg_agmemory import api, source_access, worker
from pg_agmemory.admin import AdminError
from pg_agmemory.jobs import Jobs
from pg_agmemory.source_access import SourceAccessRequest, SourceIdentity
from pg_agmemory.transactions import CommitOutcomeUnknown, async_transaction, transaction

pytestmark = pytest.mark.integration


def assert_default_deadline(started, record_property):
    elapsed = time.monotonic() - started
    record_property("commit_elapsed_seconds", elapsed)
    assert 4.5 <= elapsed < 8, f"Production five-second COMMIT deadline took {elapsed:.3f}s"


def assert_unknown(error):
    assert error.code == "commit_outcome_unknown"
    assert str(error) == "commit_outcome_unknown"
    assert not isinstance(error, psycopg.Error)
    # A closed transport cannot establish local success, even when our separate
    # privileged observer has independently seen the committed row.
    assert error.local_committed is None


def rows_present(conn, table, values=(1,)):
    return conn.execute(
        sql.SQL("SELECT value FROM {} ORDER BY value").format(table)
    ).fetchall() == [(value,) for value in values]


class CommitWait:
    def __init__(self, url, locally_visible):
        self.url = url
        self.locally_visible = locally_visible
        self.name = "pgag_deadline_" + uuid4().hex
        self.stop = Event()
        self.ready = Event()
        self.observed = False
        self.emergency_cleanup = False

    def arm(self, conn):
        conn.execute("SELECT set_config('application_name', %s, false)", (self.name,))
        conn.execute("SET LOCAL synchronous_commit='remote_apply'")
        conn.execute("SET LOCAL statement_timeout=0")

    async def arm_async(self, conn):
        await conn.execute("SELECT set_config('application_name', %s, false)", (self.name,))
        await conn.execute("SET LOCAL synchronous_commit='remote_apply'")
        await conn.execute("SET LOCAL statement_timeout=0")

    def observe(self):
        with control_connection(self.url) as conn:
            deadline = time.monotonic() + 15
            self.ready.set()
            while not self.stop.is_set():
                row = conn.execute(
                    """SELECT pid,wait_event,query FROM pg_stat_activity
                       WHERE datname=current_database() AND application_name=%s""",
                    (self.name,),
                ).fetchone()
                if row and row[1] == "SyncRep":
                    assert row[2].strip().upper() == "COMMIT"
                    self.observed = True
                if time.monotonic() >= deadline:
                    self.emergency_cleanup = True
                    self.terminate(conn)
                    return
                self.stop.wait(0.01)

    def terminate(self, conn):
        conn.execute(
            """SELECT pg_terminate_backend(pid, 1000) FROM pg_stat_activity
               WHERE datname=current_database() AND application_name=%s""",
            (self.name,),
        )


@contextmanager
def observe_commit(url, locally_visible):
    control = CommitWait(url, locally_visible)
    with ThreadPoolExecutor(max_workers=1) as executor:
        observed = executor.submit(control.observe)
        try:
            assert control.ready.wait(4), "The independent observer could not connect"
            yield control
            assert control.observed, "COMMIT never reached a real PostgreSQL SyncRep wait"
            assert not control.emergency_cleanup, "COMMIT needed emergency test cleanup"
        finally:
            control.stop.set()
            try:
                observed.result(timeout=4)
            finally:
                with control_connection(url) as conn:
                    control.terminate(conn)
    # SyncRep precedes ProcArray removal: locally committed WAL is not yet visible
    # to snapshots. Only after the deadline has returned do we release the named
    # backend and prove that its write survived, rather than being rolled back.
    with control_connection(url) as conn:
        assert control.locally_visible(conn), "The timed-out COMMIT was not persisted locally"


@pytest.mark.parametrize("driver", ["sync", "async"])
def test_syncrep_expires_without_cancellation(
    database, missing_standby, commit_rows, record_property, driver,
):
    receipts = []
    with observe_commit(
        database[0], lambda conn: rows_present(conn, commit_rows),
    ) as control:
        if driver == "sync":
            with psycopg.connect(database[0], autocommit=True) as conn:
                with pytest.raises(CommitOutcomeUnknown) as error:
                    with transaction(conn):
                        control.arm(conn)
                        conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows))
                        started = time.monotonic()
                    receipts.append("committed")
                assert_default_deadline(started, record_property)
                assert_unknown(error.value)
                assert conn.closed
        else:
            async def exercise():
                conn = await psycopg.AsyncConnection.connect(database[0], autocommit=True)
                try:
                    with pytest.raises(CommitOutcomeUnknown) as error:
                        async with async_transaction(conn):
                            await control.arm_async(conn)
                            await conn.execute(
                                sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows)
                            )
                            started = time.monotonic()
                        receipts.append("committed")
                    assert_default_deadline(started, record_property)
                    assert_unknown(error.value)
                    assert conn.closed
                finally:
                    await conn.close()

            asyncio.run(exercise())
    assert receipts == []
    with control_connection(database[0]) as conn:
        assert rows_present(conn, commit_rows)


def test_api_deadline_discards_buffered_201(
    env, missing_standby, monkeypatch, record_property,
):
    original = api.principal_connection
    buffered_statuses, connections = [], []
    boundary = env.client.app.middleware_stack
    while not isinstance(boundary, api.TransactionBoundary):
        boundary = boundary.app
    original_app = boundary.app

    async def capture_response(scope, receive, send):
        async def capture(message):
            if message["type"] == "http.response.start":
                buffered_statuses.append(message["status"])
            await send(message)

        await original_app(scope, receive, capture)

    monkeypatch.setattr(boundary, "app", capture_response)
    with observe_commit(
        env.admin_url,
        lambda conn: conn.execute(
            """SELECT count(*) FROM memory_ops.idempotency
               WHERE tenant_id=%s AND operation='observe'""",
            (env.tenants[0],),
        ).fetchone() == (1,),
    ) as control:
        @asynccontextmanager
        async def armed_connection(*args, **kwargs):
            async with original(*args, **kwargs) as (conn, identity):
                connections.append(conn)
                await conn.execute(
                    "SELECT set_config('application_name', %s, false)", (control.name,),
                )
                await conn.execute("SET synchronous_commit='remote_apply'")
                await conn.execute("SET statement_timeout=0")
                yield conn, identity

        monkeypatch.setattr(api, "principal_connection", armed_connection)
        started = time.monotonic()
        response = env.client.post("/v1/observe", json=observe_body(env), headers=env.headers())
        assert_default_deadline(started, record_property)
    assert buffered_statuses == [201]
    assert response.status_code == 503
    payload = response.json()
    assert set(payload) == {"code", "request_id", "retryable", "details"}
    assert payload["code"] == "commit_outcome_unknown"
    assert payload["retryable"] is False and payload["details"] == {}
    assert response.headers["x-request-id"] == payload["request_id"]
    assert response.headers["cache-control"] == "no-store"
    assert "SYNTHETIC_PRIVATE" not in response.text and "memory_id" not in response.text
    assert len(connections) == 1 and connections[0].closed
    assert episode_and_receipt_counts(env) == (1, 1)


def test_admin_deadline_reports_unknown_without_receipt(
    env, missing_standby, monkeypatch, record_property,
):
    original = source_access.admin_connection
    identity = SourceIdentity(
        source_system="synthetic", dataset_id="commit-deadline", source_subject="reader",
    )
    request = SourceAccessRequest(
        operation="bind", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=env.principals[2], source=identity, expected_access_epoch=1,
    )
    receipts, connections = [], []
    with observe_commit(
        env.admin_url,
        lambda conn: conn.execute(
            """SELECT count(*) FROM memory_ops.source_access_event
               WHERE tenant_id=%s AND reason='bound'""",
            (env.tenants[0],),
        ).fetchone() == (1,),
    ) as control:
        @contextmanager
        def armed_connection(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                connections.append(conn)
                conn.execute(
                    "SELECT set_config('application_name', %s, false)", (control.name,),
                )
                conn.execute("SET synchronous_commit='remote_apply'")
                conn.execute("SET statement_timeout=0")
                yield conn

        with monkeypatch.context() as patch:
            patch.setattr(source_access, "admin_connection", armed_connection)
            started = time.monotonic()
            with pytest.raises(AdminError) as error:
                with source_access.source_access(env.admin_url, request) as receipt:
                    receipts.append(receipt)
            assert_default_deadline(started, record_property)
    assert error.value.code == "commit_outcome_unknown"
    assert error.value.outcome_unknown
    assert str(error.value) == "commit_outcome_unknown"
    assert receipts == []
    assert len(connections) == 1 and connections[0].closed
    with source_access.source_access(env.admin_url, SourceAccessRequest(
        operation="get", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=env.principals[2],
    )) as current:
        assert current.source == identity and current.sequence == 0


def test_worker_reservation_deadline_stops_before_provider_or_failure_write(
    env, missing_standby, profile, monkeypatch, record_property,
):
    configure(env, profile)
    source = env.observe("Alice / preferred_editor: Vim")
    assert source.status_code == 201
    queued = enqueue(env, source.json()["memory_id"])
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]
    calls = install_extraction(monkeypatch, profile)
    original = worker.job_transaction
    transactions = []

    async def forbidden_fail(*args, **kwargs):
        pytest.fail("An uncertain COMMIT must not write a job failure/retry")

    monkeypatch.setattr(Jobs, "fail", forbidden_fail)
    with observe_commit(
        env.admin_url,
        lambda conn: conn.execute(
            """SELECT outcome,billing_unknown FROM memory_ops.model_call
               WHERE job_id=%s""", (job_id,),
        ).fetchall() == [("unknown", True)],
    ) as control:
        @asynccontextmanager
        async def armed_transaction(*args, **kwargs):
            async with original(*args, **kwargs) as jobs:
                transactions.append(jobs.conn)
                if len(transactions) == 2:
                    await control.arm_async(jobs.conn)
                yield jobs

        monkeypatch.setattr(worker, "job_transaction", armed_transaction)
        started = time.monotonic()
        with pytest.raises(CommitOutcomeUnknown) as error:
            asyncio.run(worker.run_once(
                env.settings.database_url, env.subjects[0], profile=profile,
            ))
        assert_default_deadline(started, record_property)
    assert_unknown(error.value)
    assert len(transactions) == 2 and transactions[-1].closed
    assert calls == []
    with control_connection(env.admin_url) as conn:
        assert conn.execute(
            "SELECT state,attempt,error_code FROM memory_ops.job WHERE id=%s", (job_id,),
        ).fetchone() == ("running", 1, None)
        assert conn.execute(
            "SELECT count(*) FROM memory.assertion WHERE tenant_id=%s", (env.tenants[0],),
        ).fetchone() == (0,)
        assert conn.execute(
            """SELECT outcome,billing_unknown FROM memory_ops.model_call
               WHERE job_id=%s""", (job_id,),
        ).fetchall() == [("unknown", True)]


def protocol_messages(buffer):
    while len(buffer) >= 5:
        size = struct.unpack_from("!I", buffer, 1)[0]
        assert 4 <= size <= 1024 * 1024
        if len(buffer) < size + 1:
            return
        kind, payload = buffer[0:1], bytes(buffer[5:size + 1])
        del buffer[:size + 1]
        yield kind, payload


class LostCommitAck:
    def __init__(self, url, table):
        params = conninfo_to_dict(url)
        host = params.get("hostaddr") or params.get("host", "127.0.0.1")
        if host.startswith("/") or "," in host:
            pytest.skip("The local lost-ack proxy requires a single TCP PostgreSQL address")
        self.address = host, int(params.get("port", 5432))
        self.url, self.table = url, table
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(4)
        params.pop("hostaddr", None)
        params.update(
            host="127.0.0.1", port=str(self.listener.getsockname()[1]),
            sslmode="disable", gssencmode="disable", connect_timeout="3",
        )
        self.client_url = make_conninfo(**params)
        self.stop = Event()
        self.commit_forwarded = False
        self.ack_dropped = False
        self.persisted = False

    def relay(self):
        sockets = [self.listener]
        try:
            client, _ = self.listener.accept()
            sockets.append(client)
            backend = socket.create_connection(self.address, timeout=3)
            sockets.append(backend)
            client.settimeout(2)
            frontend_buffer, backend_buffer = bytearray(), bytearray()
            startup = True
            commit_at = None
            while not self.stop.is_set():
                readable, _, _ = select.select([self.listener, client, backend], [], [], 0.05)
                for current in readable:
                    if current is self.listener:
                        # Accept but never forward or answer a cancellation connection.
                        extra, _ = self.listener.accept()
                        sockets.append(extra)
                        continue
                    data = current.recv(65536)
                    if not data:
                        return
                    if current is client:
                        frontend_buffer.extend(data)
                        if startup and len(frontend_buffer) >= 4:
                            size = struct.unpack_from("!I", frontend_buffer)[0]
                            if len(frontend_buffer) >= size:
                                del frontend_buffer[:size]
                                startup = False
                        if not startup:
                            for kind, payload in protocol_messages(frontend_buffer):
                                if kind == b"Q" and payload.rstrip(b"\0").upper() == b"COMMIT":
                                    self.commit_forwarded = True
                                    commit_at = time.monotonic()
                        backend.sendall(data)
                    else:
                        backend_buffer.extend(data)
                        for kind, payload in protocol_messages(backend_buffer):
                            if self.commit_forwarded and kind == b"C" and payload == b"COMMIT\0":
                                self.ack_dropped = True
                                with control_connection(self.url) as control:
                                    self.persisted = rows_present(control, self.table)
                        if not self.commit_forwarded:
                            client.sendall(data)
                if commit_at is not None and time.monotonic() - commit_at > 15:
                    pytest.fail("COMMIT did not expire through the nonresponsive transport")
        finally:
            for stream in sockets:
                stream.close()


@contextmanager
def lose_commit_ack(url, table):
    proxy = LostCommitAck(url, table)
    with ThreadPoolExecutor(max_workers=1) as executor:
        relay = executor.submit(proxy.relay)
        try:
            yield proxy
        finally:
            proxy.stop.set()
            relay.result(timeout=5)


@pytest.mark.parametrize("driver", ["sync", "async"])
def test_lost_ack_and_blackholed_cancel_transport_still_expires(
    database, commit_rows, record_property, driver,
):
    with lose_commit_ack(database[0], commit_rows) as proxy:
        if driver == "sync":
            with psycopg.connect(proxy.client_url, autocommit=True) as conn:
                with pytest.raises(CommitOutcomeUnknown) as error:
                    with transaction(conn):
                        conn.execute("SET LOCAL synchronous_commit=local")
                        conn.execute("SET LOCAL statement_timeout=0")
                        conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows))
                        started = time.monotonic()
                assert_default_deadline(started, record_property)
                assert_unknown(error.value)
                assert conn.closed
        else:
            async def exercise():
                conn = await psycopg.AsyncConnection.connect(proxy.client_url, autocommit=True)
                try:
                    with pytest.raises(CommitOutcomeUnknown) as error:
                        async with async_transaction(conn):
                            await conn.execute("SET LOCAL synchronous_commit=local")
                            await conn.execute("SET LOCAL statement_timeout=0")
                            await conn.execute(
                                sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows)
                            )
                            started = time.monotonic()
                    assert_default_deadline(started, record_property)
                    assert_unknown(error.value)
                    assert conn.closed
                finally:
                    await conn.close()

            asyncio.run(exercise())
        assert proxy.commit_forwarded and proxy.ack_dropped and proxy.persisted
    with control_connection(database[0]) as conn:
        assert rows_present(conn, commit_rows)


def test_success_disarms_both_watchdogs_and_leaves_transaction_body_unbounded(
    database, commit_rows,
):
    async def exercise():
        async_conn = await psycopg.AsyncConnection.connect(database[0], autocommit=True)
        try:
            with psycopg.connect(database[0], autocommit=True) as sync_conn:
                sync_conn.execute("SET synchronous_commit=local")
                await async_conn.execute("SET synchronous_commit=local")
                with transaction(sync_conn):
                    sync_conn.execute(
                        sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows)
                    )
                sync_committed = time.monotonic()
                async with async_transaction(async_conn):
                    await async_conn.execute("SELECT pg_sleep(5.25)")
                    await async_conn.execute(
                        sql.SQL("INSERT INTO {} VALUES (2)").format(commit_rows)
                    )
                async_committed = time.monotonic()
                assert async_committed - sync_committed > 5
                assert not sync_conn.closed
                assert sync_conn.execute("SELECT 42").fetchone() == (42,)

                # Both the successful async COMMIT and the sync body must survive
                # this wait. The production watchdog uses a thread, not this loop.
                with transaction(sync_conn):
                    sync_conn.execute("SELECT pg_sleep(5.25)")
                    sync_conn.execute(
                        sql.SQL("INSERT INTO {} VALUES (3)").format(commit_rows)
                    )
                assert time.monotonic() - async_committed > 5
                assert not async_conn.closed
                assert await (await async_conn.execute("SELECT 42")).fetchone() == (42,)
                assert rows_present(sync_conn, commit_rows, (1, 2, 3))
        finally:
            await async_conn.close()

    asyncio.run(exercise())
