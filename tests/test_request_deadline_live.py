"""Whole-request deadlines against real PostgreSQL and deterministic ASGI peers.

The default-budget case combines a four-second body receive with repeated
two-second SQL statements: elapsed must be 28.5 <= t < 32 seconds (29 seconds
of work plus scheduling tolerance), not a fresh budget per phase/statement.
Short cases change only the request's new budget constants: 3 seconds total,
2 seconds of work, with 1.8 <= t < 3.75 seconds. A blocked fallback must stop
at 3 seconds (2.8 <= t < 3.75), not acquire another error-send budget.

Use the ordinary fresh disposable PGAG_TEST_DATABASE_URL fixture. SyncRep also
requires an owned primary started with synchronous_standby_names=pgag_missing
and synchronous_commit=local. Only the target transaction requests remote_apply.
Named-backend termination happens after the measured exit; the inherited
15-second observer failsafe is never the mechanism under test. No statement,
lock, COMMIT-watchdog, role, RLS, or cluster settings are relaxed.
"""

import asyncio
import json
import logging
import select
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, replace
from threading import Event
from uuid import UUID

import psycopg
import pytest
from fastapi.encoders import jsonable_encoder
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from starlette.responses import JSONResponse
from test_commit_deadline import LostCommitAck, observe_commit, protocol_messages
from test_commit_outcomes import (
    control_connection,
    episode_and_receipt_counts,
    observe_body,
)
from test_commit_outcomes import missing_standby as missing_standby

from pg_agmemory import api, request_deadline, service
from pg_agmemory.models import Observe
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access

pytestmark = pytest.mark.integration


@pytest.fixture
def short_budget(monkeypatch):
    monkeypatch.setattr(request_deadline, "REQUEST_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(request_deadline, "ERROR_RESPONSE_RESERVE_SECONDS", 1.0)


@pytest.fixture
def owned_missing_standby(env, missing_standby):
    with psycopg.connect(
        env.admin_url, autocommit=True, connect_timeout=3, options="-c statement_timeout=2000",
    ) as conn:
        standby = conn.execute("SHOW synchronous_standby_names").fetchone()[0]
        commit = conn.execute("SHOW synchronous_commit").fetchone()[0]
    if standby != "pgag_missing" or commit != "local":
        pytest.skip(
            "Requires owned primary startup: pgag_missing standby, synchronous_commit=local"
        )


@pytest.fixture
def owned_connections(monkeypatch):
    original = service.connect
    connections = []

    async def capture(*args, **kwargs):
        conn = await original(*args, **kwargs)
        connections.append((conn, conn.info.backend_pid))
        return conn

    monkeypatch.setattr(service, "connect", capture)
    return connections


def run(coroutine, timeout=8):
    async def bounded():
        return await asyncio.wait_for(coroutine, timeout)

    return asyncio.run(bounded())


def elapsed_since(started, record_property, *, lower=1.8, upper=3.75):
    elapsed = time.monotonic() - started
    record_property("request_elapsed_seconds", elapsed)
    record_property("elapsed_lower_bound_seconds", lower)
    record_property("elapsed_upper_bound_seconds", upper)
    assert lower <= elapsed < upper, f"Request took {elapsed:.3f}s; expected [{lower}, {upper})"
    return elapsed


async def request(env, app, messages, events, *, receive=None, send=None, settings=None):
    body = json.dumps(observe_body(env)).encode()
    headers = {**env.headers(), "Content-Type": "application/json"}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/observe",
        "raw_path": b"/v1/observe",
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }

    async def normal_receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def normal_send(message):
        messages.append(message)

    boundary = api.TransactionBoundary(app, settings or env.settings, timing_sink=events.append)
    await boundary(scope, receive or normal_receive, send or normal_send)
    return scope


def mutation(buffered):
    async def app(scope, receive, send):
        data = Observe.model_validate_json((await receive())["body"])
        key = dict(scope["headers"])[b"idempotency-key"].decode()
        result = await scope["state"]["service"].observe(data, key)
        buffered.append(201)
        await JSONResponse(jsonable_encoder(result), status_code=201)(scope, receive, send)

    return app


async def unreachable(scope, receive, send):
    pytest.fail("An expired request reached the handler")


def assert_error(messages, *, code="request_deadline_exceeded", status=503, retryable=True):
    assert [message["type"] for message in messages] == [
        "http.response.start", "http.response.body",
    ]
    start, body = messages
    assert start["status"] == status
    headers = dict(start["headers"])
    payload = json.loads(body["body"])
    assert set(payload) == {"code", "request_id", "retryable", "details"}
    assert payload == {
        "code": code, "request_id": payload["request_id"], "retryable": retryable, "details": {},
    }
    assert str(UUID(payload["request_id"])) == payload["request_id"]
    assert headers[b"x-request-id"].decode() == payload["request_id"]
    assert headers[b"cache-control"] == b"no-store"
    assert not body.get("more_body", False)
    rendered = body["body"].decode().lower()
    for private in (
        "synthetic_private", "memory_id", "pg_sleep", "postgres", "sqlstate",
        "replication", "password", "traceback", "locally_committed", "remote_apply",
    ):
        assert private not in rendered
    return payload


def assert_timing(events, *, acknowledged, status):
    assert len(events) == 1
    event = events[0]
    assert event.status == status
    assert (event.commit_ms is not None) is acknowledged
    assert (event.transaction_ms is not None) is acknowledged
    if acknowledged:
        assert 0 <= event.commit_ms <= event.transaction_ms <= event.server_ms
    assert "SYNTHETIC_PRIVATE" not in json.dumps(asdict(event))


async def assert_disposed(env, connections):
    assert connections and all(conn.closed for conn, _ in connections)
    pids = [pid for _, pid in connections]
    # PostgreSQL notices ordinary disconnects asynchronously. This observer is
    # read-only: it neither cancels nor terminates the transaction under test.
    with control_connection(env.admin_url) as conn:
        for _ in range(100):
            if conn.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE pid=ANY(%s)", (pids,),
            ).fetchone() == (0,):
                return
            await asyncio.sleep(0.02)
    pytest.fail("The disconnected request still owns a PostgreSQL backend")


def assert_abort_logged(caplog, request_id):
    assert any(
        record.name == "pg_agmemory"
        and record.levelno >= logging.WARNING
        and request_id in record.getMessage()
        and any(word in record.getMessage().lower() for word in ("abort", "timeout", "deadline"))
        for record in caplog.records
    ), "The interrupted response must produce a request-correlated, sanitized abort log"
    assert "SYNTHETIC_PRIVATE" not in caplog.text


def test_default_budget_combines_receive_and_repeated_subfive_second_sql(
    env, owned_connections, record_property,
):
    assert request_deadline.REQUEST_TIMEOUT_SECONDS == 30.0
    assert request_deadline.ERROR_RESPONSE_RESERVE_SECONDS == 1.0
    messages, events, buffered, statement_times = [], [], [], []
    write = mutation(buffered)

    async def receive():
        await asyncio.sleep(4)
        return {
            "type": "http.request", "body": json.dumps(observe_body(env)).encode(),
            "more_body": False,
        }

    async def app(scope, receive, send):
        await write(scope, receive, send)
        conn = scope["state"]["service"].conn
        assert (await (await conn.execute("SHOW statement_timeout")).fetchone()) == {
            "statement_timeout": "5s",
        }
        for _ in range(20):
            started = time.monotonic()
            await conn.execute("SELECT pg_sleep(2)")
            statement_times.append(time.monotonic() - started)
        pytest.fail("The aggregate transaction body was allowed to finish")

    async def scenario():
        started = time.monotonic()
        await request(env, app, messages, events, receive=receive)
        elapsed_since(started, record_property, lower=28.5, upper=32)
        await assert_disposed(env, owned_connections)

    run(scenario(), timeout=36)
    assert buffered == [201]
    assert len(statement_times) >= 10 and all(1.8 <= value < 5 for value in statement_times)
    assert_error(messages)
    assert_timing(events, acknowledged=False, status=503)
    assert episode_and_receipt_counts(env) == (0, 0)


def test_connect_startup_wait_uses_request_budget(
    env, short_budget, owned_connections, record_property,
):
    messages, events = [], []

    async def scenario():
        accepted = asyncio.Event()
        writers, peers = [], []
        release = asyncio.Event()

        async def blackhole(reader, writer):
            writers.append(writer)
            peers.append(asyncio.current_task())
            try:
                assert await reader.read(4096)
                accepted.set()
                await release.wait()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(blackhole, "127.0.0.1", 0)
        params = conninfo_to_dict(env.settings.database_url)
        params.pop("hostaddr", None)
        params.update(
            host="127.0.0.1", port=str(server.sockets[0].getsockname()[1]),
            sslmode="disable", gssencmode="disable",
        )
        settings = replace(env.settings, database_url=make_conninfo(**params))
        try:
            started = time.monotonic()
            await request(env, unreachable, messages, events, settings=settings)
            elapsed_since(started, record_property)
            assert accepted.is_set(), "The real libpq connection never reached the local peer"
            assert not owned_connections
        finally:
            release.set()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*peers)
            assert all(writer.is_closing() for writer in writers)

    run(scenario())
    assert_error(messages)
    assert_timing(events, acknowledged=False, status=503)


def test_admission_wait_disposes_connection_before_principal_context_yields(
    env, short_budget, owned_connections, record_property,
):
    messages, events = [], []
    with control_connection(env.admin_url) as blocker:
        blocker.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (str(env.tenants[0]),),
        )

        async def scenario():
            started = time.monotonic()
            task = asyncio.create_task(request(env, unreachable, messages, events))
            try:
                for _ in range(100):
                    if owned_connections and blocker.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s",
                        (owned_connections[0][1],),
                    ).fetchone() == ("advisory",):
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("The request never reached the real admission lock")
                await task
                elapsed_since(started, record_property)
                assert len(owned_connections) == 1 and owned_connections[0][0].closed
                assert_error(messages)
                # Client disposal does not interrupt the server's advisory wait.
                record_property(
                    "admission_backend_present_after_client_exit",
                    blocker.execute(
                        "SELECT 1 FROM pg_stat_activity WHERE pid=%s",
                        (owned_connections[0][1],),
                    ).fetchone() is not None,
                )
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        run(scenario())
    # Release only our blocker after the measured client exit, then observe
    # ordinary disconnect cleanup without cancelling or terminating the backend.
    cleanup_started = time.monotonic()
    run(assert_disposed(env, owned_connections))
    record_property(
        "admission_backend_cleanup_after_unlock_seconds", time.monotonic() - cleanup_started,
    )
    assert_timing(events, acknowledged=False, status=503)
    assert events[0].connection_barrier_ms is None
    assert episode_and_receipt_counts(env) == (0, 0)


@pytest.mark.parametrize("block_fallback", [False, True], ids=["receive", "fallback-send"])
def test_receive_and_deadline_error_share_one_absolute_end(
    env, short_budget, owned_connections, record_property, caplog, block_fallback,
):
    messages, events = [], []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)
        if block_fallback and message["type"] == "http.response.body":
            await asyncio.Event().wait()

    async def scenario():
        started = time.monotonic()
        scope = await request(env, unreachable, messages, events, receive=receive, send=send)
        elapsed_since(started, record_property, lower=2.8 if block_fallback else 1.8)
        if block_fallback:
            assert_abort_logged(caplog, scope["state"]["request_id"])

    run(scenario())
    assert_error(messages)
    assert_timing(events, acknowledged=False, status=503)
    assert not owned_connections
    if block_fallback:
        assert events[0].response_bytes == 0


@pytest.mark.parametrize("blocked_message", ["http.response.start", "http.response.body"])
def test_normal_error_response_is_bounded_and_never_restarted(
    env, short_budget, owned_connections, record_property, caplog, blocked_message,
):
    messages, events = [], []

    async def app(scope, receive, send):
        raise service.MemoryError("not_found", 404)

    async def send(message):
        messages.append(message)
        if message["type"] == blocked_message:
            await asyncio.Event().wait()

    async def scenario():
        started = time.monotonic()
        scope = await request(env, app, messages, events, send=send)
        elapsed_since(started, record_property)
        await assert_disposed(env, owned_connections)
        assert_abort_logged(caplog, scope["state"]["request_id"])

    run(scenario())
    if blocked_message == "http.response.body":
        assert_error(messages, code="not_found", status=404, retryable=False)
        assert_timing(events, acknowledged=False, status=404)
    else:
        assert len(messages) == 1 and messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 404
        assert dict(messages[0]["headers"])[b"cache-control"] == b"no-store"
        assert_timing(events, acknowledged=False, status=None)
    assert events[0].response_bytes == 0
    assert episode_and_receipt_counts(env) == (0, 0)


def test_success_send_holds_admin_drain_until_abort_then_releases_owned_connection(
    env, short_budget, owned_connections, record_property, caplog,
):
    messages, events, buffered = [], [], []

    def revoke():
        with scope_access(env.admin_url, ScopeAccessRequest(
            operation="revoke", tenant_id=env.tenants[0], scope_id=env.scopes[0],
            principal_id=env.principals[0], expected_access_epoch=1,
        )) as result:
            return result, time.monotonic()

    async def scenario():
        sending = asyncio.Event()

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body":
                sending.set()
                await asyncio.Event().wait()

        started = time.monotonic()
        reading = asyncio.create_task(
            request(env, mutation(buffered), messages, events, send=send),
        )
        revoking = None
        try:
            await asyncio.wait_for(sending.wait(), 1.5)
            assert len(owned_connections) == 1 and not owned_connections[0][0].closed
            revoking = asyncio.create_task(asyncio.to_thread(revoke))
            with control_connection(env.admin_url) as observer:
                for _ in range(100):
                    waiting = observer.execute(
                        """SELECT count(*) FROM pg_stat_activity
                           WHERE datname=current_database() AND wait_event='advisory'
                             AND %s=ANY(pg_blocking_pids(pid))""", (owned_connections[0][1],),
                    ).fetchone()[0]
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("Administrative revoke did not wait behind response delivery")
            assert not reading.done() and not revoking.done()
            scope = await reading
            elapsed_since(started, record_property)
            assert_abort_logged(caplog, scope["state"]["request_id"])
            await assert_disposed(env, owned_connections)
            result, revoked_at = await asyncio.wait_for(revoking, 3)
            assert revoked_at - started >= 1.8, "Admin drain escaped before response expiry"
            assert result.changed and result.access_epoch == 2 and not result.membership_exists
        finally:
            if not reading.done():
                reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
            if revoking is not None:
                await asyncio.gather(revoking, return_exceptions=True)

    run(scenario(), timeout=10)
    assert buffered == [201]
    assert [message["type"] for message in messages] == [
        "http.response.start", "http.response.body",
    ]
    assert messages[0]["status"] == 201
    assert_timing(events, acknowledged=True, status=201)
    assert events[0].response_bytes == 0
    assert episode_and_receipt_counts(env) == (1, 1)


def test_request_budget_beats_commit_watchdog_in_real_syncrep(
    env, owned_missing_standby, short_budget, owned_connections, record_property,
):
    messages, events, buffered = [], [], []
    write = mutation(buffered)

    def locally_visible(conn):
        return conn.execute(
            "SELECT count(*) FROM memory_ops.idempotency WHERE tenant_id=%s",
            (env.tenants[0],),
        ).fetchone() == (1,)

    with observe_commit(env.admin_url, locally_visible) as control:
        async def app(scope, receive, send):
            conn = scope["state"]["service"].conn
            await conn.execute(
                "SELECT set_config('application_name', %s, false)", (control.name,),
            )
            await conn.execute("SET LOCAL synchronous_commit='remote_apply'")
            await write(scope, receive, send)

        async def scenario():
            started = time.monotonic()
            await request(env, app, messages, events)
            elapsed_since(started, record_property)
            assert len(owned_connections) == 1 and owned_connections[0][0].closed
            # Deliberately do not assert remote backend/SyncRep lock release.
            # The named observer tears it down only after this measured return.

        run(scenario())
        assert buffered == [201]
        assert_error(messages, code="commit_outcome_unknown", retryable=False)
        assert_timing(events, acknowledged=False, status=503)
    assert episode_and_receipt_counts(env) == (1, 1)


class ArmedLostCommitAck(LostCommitAck):
    """Reuse the existing loopback proxy, but arm after principal admission."""

    def __init__(self, url):
        super().__init__(url, None)
        self.armed = Event()
        self.extra_connections = 0

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
                readable, _, _ = select.select([self.listener, client, backend], [], [], 0.02)
                for current in readable:
                    if current is self.listener:
                        extra, _ = self.listener.accept()
                        sockets.append(extra)
                        self.extra_connections += 1
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
                                if (
                                    self.armed.is_set() and kind == b"Q"
                                    and payload.rstrip(b"\0").upper() == b"COMMIT"
                                ):
                                    self.commit_forwarded = True
                                    commit_at = time.monotonic()
                        backend.sendall(data)
                    else:
                        backend_buffer.extend(data)
                        for kind, payload in protocol_messages(backend_buffer):
                            if self.commit_forwarded and kind == b"C" and payload == b"COMMIT\0":
                                self.ack_dropped = True
                        if not self.commit_forwarded:
                            client.sendall(data)
                if commit_at is not None and time.monotonic() - commit_at > 15:
                    pytest.fail("Request required the proxy's emergency cleanup")
        finally:
            for stream in sockets:
                stream.close()


@contextmanager
def armed_lost_ack(url):
    proxy = ArmedLostCommitAck(url)
    with ThreadPoolExecutor(max_workers=1) as executor:
        relay = executor.submit(proxy.relay)
        try:
            yield proxy
        finally:
            proxy.stop.set()
            relay.result(timeout=5)


def test_lost_commit_ack_blackholes_cancel_connections_without_driver_budget_extension(
    env, short_budget, owned_connections, record_property,
):
    messages, events, buffered = [], [], []
    write = mutation(buffered)
    with armed_lost_ack(env.settings.database_url) as proxy:
        async def app(scope, receive, send):
            await write(scope, receive, send)
            proxy.armed.set()

        async def scenario():
            settings = replace(env.settings, database_url=proxy.client_url)
            started = time.monotonic()
            await request(env, app, messages, events, settings=settings)
            elapsed_since(started, record_property)
            assert proxy.commit_forwarded and proxy.ack_dropped
            assert proxy.extra_connections == 0, "Request expiry opened a driver cancel channel"
            assert len(owned_connections) == 1 and owned_connections[0][0].closed
            assert episode_and_receipt_counts(env) == (1, 1)

        run(scenario())
    assert buffered == [201]
    assert_error(messages, code="commit_outcome_unknown", retryable=False)
    assert_timing(events, acknowledged=False, status=503)
    assert episode_and_receipt_counts(env) == (1, 1)


def test_acknowledged_commit_before_any_send_still_cannot_emit_late_201(
    env, short_budget, owned_connections, monkeypatch, record_property,
):
    messages, events, buffered, acknowledgements = [], [], [], []
    original = api.async_transaction

    @asynccontextmanager
    async def delayed_exit(*args, **kwargs):
        async with original(*args, **kwargs) as tx:
            yield tx
        acknowledgements.append(True)
        # A deterministic non-yielding gap after a real ACK forces the explicit
        # deadline check; a timer callback alone cannot prevent the late 201.
        time.sleep(2.1)

    monkeypatch.setattr(api, "async_transaction", delayed_exit)

    async def scenario():
        started = time.monotonic()
        await request(env, mutation(buffered), messages, events)
        elapsed_since(started, record_property)
        await assert_disposed(env, owned_connections)

    run(scenario())
    assert acknowledgements == [True] and buffered == [201]
    assert_error(messages, code="commit_outcome_unknown", retryable=False)
    assert_timing(events, acknowledged=True, status=503)
    assert episode_and_receipt_counts(env) == (1, 1)


def test_success_disarms_request_timer_without_affecting_later_owned_connection(
    env, owned_connections, monkeypatch,
):
    messages, events, buffered = [], [], []
    monkeypatch.setattr(request_deadline, "REQUEST_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(request_deadline, "ERROR_RESPONSE_RESERVE_SECONDS", 0.25)

    async def scenario():
        await request(env, mutation(buffered), messages, events)
        assert messages[0]["status"] == 201
        assert_timing(events, acknowledged=True, status=201)
        first_id = dict(messages[0]["headers"])[b"x-request-id"]
        await assert_disposed(env, owned_connections)
        messages.clear()
        events.clear()
        monkeypatch.setattr(request_deadline, "REQUEST_TIMEOUT_SECONDS", 3.0)
        monkeypatch.setattr(request_deadline, "ERROR_RESPONSE_RESERVE_SECONDS", 0.5)
        write = mutation(buffered)

        async def later(scope, receive, send):
            conn = scope["state"]["service"].conn
            await conn.execute("SELECT pg_sleep(1.25)")
            assert not conn.closed
            await write(scope, receive, send)

        # Both requests run in this same asyncio task. A stale cancellation
        # callback can otherwise poison the second task/connection after reuse.
        await request(env, later, messages, events)
        assert messages[0]["status"] == 201
        assert first_id != dict(messages[0]["headers"])[b"x-request-id"]
        assert_timing(events, acknowledged=True, status=201)
        assert len(owned_connections) == 2
        await assert_disposed(env, owned_connections)

    run(scenario())
    assert buffered == [201, 201]
    assert episode_and_receipt_counts(env) == (2, 2)


def test_external_cancellation_remains_cancellation_and_rolls_back(
    env, short_budget, owned_connections,
):
    messages, events, buffered = [], [], []
    write = mutation(buffered)

    async def scenario():
        entered = asyncio.Event()

        async def app(scope, receive, send):
            await write(scope, receive, send)
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(request(env, app, messages, events))
        try:
            await asyncio.wait_for(entered.wait(), 1.5)
            started = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert time.monotonic() - started < 1
            await assert_disposed(env, owned_connections)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(scenario())
    assert buffered == [201] and messages == []
    assert_timing(events, acknowledged=False, status=None)
    assert episode_and_receipt_counts(env) == (0, 0)
