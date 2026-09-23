"""Local commit is not a synchronous-replication acknowledgement.

The live SyncRep cases require a disposable primary started with a missing
``synchronous_standby_names`` target and ``synchronous_commit=local``. PostgreSQL
18 makes the former SIGHUP-only: neither SET nor ALTER ROLE can enable it. These
tests never change cluster configuration. Ordinary PGAG_TEST_DATABASE_URL runs
still exercise pre-COMMIT rollback.

A missing standby exercises cancellation, not replication/failover qualification.
"""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from threading import Event
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from test_processing import configure, enqueue, install_extraction
from test_processing import profile as profile

from pg_agmemory import api, source_access, worker
from pg_agmemory.admin import AdminError
from pg_agmemory.jobs import Jobs
from pg_agmemory.native_client import AdapterFailure, NativeHTTPClient
from pg_agmemory.service import MemoryService
from pg_agmemory.source_access import SourceAccessRequest, SourceIdentity, SourceNotice
from pg_agmemory.transactions import CommitOutcomeUnknown, async_transaction, transaction


def control_connection(url):
    return psycopg.connect(
        url,
        autocommit=True,
        connect_timeout=3,
        options="-c statement_timeout=2000 -c synchronous_commit=local",
    )


@pytest.fixture
def missing_standby(database):
    with control_connection(database[0]) as conn:
        configured, context = conn.execute(
            "SELECT setting,context FROM pg_settings WHERE name='synchronous_standby_names'"
        ).fetchone()
        assert context == "sighup", "Re-evaluate isolation if PostgreSQL changes this GUC"
        if not configured:
            pytest.skip(
                "Real SyncRep needs an owned disposable primary started with a missing "
                "synchronous_standby_names target and synchronous_commit=local; "
                "session/role SET is forbidden by PostgreSQL"
            )
        assert conn.execute("SELECT count(*) FROM pg_stat_replication").fetchone() == (0,), (
            "This cancellation fixture must not run against a primary with real standbys"
        )


@pytest.fixture
def commit_rows(database):
    table = sql.Identifier("commit_outcome_" + uuid4().hex)
    with control_connection(database[0]) as conn:
        conn.execute(sql.SQL("CREATE TABLE {} (value integer PRIMARY KEY)").format(table))
    try:
        yield table
    finally:
        with control_connection(database[0]) as conn:
            conn.execute(sql.SQL("DROP TABLE {}").format(table))


class SyncRepCancellation:
    def __init__(self, url):
        self.url = url
        self.name = "pgag_commit_" + uuid4().hex
        self.stop = Event()
        self.observed = False
        self.cancel_task = None

    def arm(self, conn):
        conn.execute("SELECT set_config('application_name', %s, true)", (self.name,))
        conn.execute("SET LOCAL synchronous_commit='remote_apply'")
        conn.execute("SET LOCAL statement_timeout='10000'")

    async def arm_async(self, conn):
        await conn.execute("SELECT set_config('application_name', %s, true)", (self.name,))
        await conn.execute("SET LOCAL synchronous_commit='remote_apply'")
        await conn.execute("SET LOCAL statement_timeout='10000'")

    def observe(self):
        with control_connection(self.url) as conn:
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and not self.stop.is_set():
                    row = conn.execute(
                        """SELECT pid,wait_event,query FROM pg_stat_activity
                           WHERE datname=current_database() AND application_name=%s""",
                        (self.name,),
                    ).fetchone()
                    if row and row[1] == "SyncRep":
                        assert row[2].strip().upper() == "COMMIT"
                        self.observed = True
                        if self.cancel_task is not None:
                            self.cancel_task()
                        else:
                            assert conn.execute(
                                "SELECT pg_cancel_backend(%s)", (row[0],)
                            ).fetchone() == (True,)
                        return
                    self.stop.wait(0.01)
                if not self.stop.is_set():
                    pytest.fail("The target COMMIT never reached PostgreSQL's SyncRep wait")
            finally:
                if not self.observed:
                    # Only this test's uniquely named backend is eligible for cleanup.
                    conn.execute(
                        """SELECT pg_cancel_backend(pid) FROM pg_stat_activity
                           WHERE datname=current_database() AND application_name=%s""",
                        (self.name,),
                    )


@contextmanager
def cancel_commit(url, *, expect_wait=True):
    control = SyncRepCancellation(url)
    executor = ThreadPoolExecutor(max_workers=1)
    observed = executor.submit(control.observe)
    try:
        yield control
        if not expect_wait:
            control.stop.set()
        observed.result(timeout=10)
        assert control.observed is expect_wait, "Unexpected PostgreSQL SyncRep wait behavior"
    finally:
        control.stop.set()
        with control_connection(url) as conn:
            conn.execute(
                """SELECT pg_cancel_backend(pid) FROM pg_stat_activity
                   WHERE datname=current_database() AND application_name=%s
                     AND wait_event='SyncRep'""",
                (control.name,),
            )
        executor.shutdown(wait=True, cancel_futures=True)


def assert_unknown(exc):
    assert exc.code == "commit_outcome_unknown"
    assert str(exc) == "commit_outcome_unknown"
    assert exc.local_committed is True
    assert not isinstance(exc, psycopg.Error)


@pytest.mark.integration
@pytest.mark.parametrize("client_min_messages", ["warning", "error"])
def test_sync_commit_cancellation_is_not_a_receipt(
    database, missing_standby, commit_rows, client_min_messages,
):
    receipts = []
    with cancel_commit(database[0]) as control:
        with psycopg.connect(database[0], autocommit=True) as conn:
            conn.execute(
                "SELECT set_config('client_min_messages', %s, false)", (client_min_messages,)
            )
            with pytest.raises(CommitOutcomeUnknown) as error:
                with transaction(conn):
                    control.arm(conn)
                    conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows))
                receipts.append("committed")
    assert_unknown(error.value)
    assert receipts == []
    with control_connection(database[0]) as conn:
        rows = conn.execute(sql.SQL("SELECT value FROM {}").format(commit_rows)).fetchall()
        assert rows == [(1,)]


@pytest.mark.integration
def test_async_task_cancellation_during_commit_reports_uncertainty(
    database, missing_standby, commit_rows,
):
    receipts = []

    async def exercise(control):
        conn = await psycopg.AsyncConnection.connect(database[0], autocommit=True)
        try:
            async def write():
                async with async_transaction(conn):
                    await control.arm_async(conn)
                    await conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows))
                receipts.append("committed")

            loop = asyncio.get_running_loop()
            task = asyncio.create_task(write())
            control.cancel_task = lambda: loop.call_soon_threadsafe(task.cancel)
            with pytest.raises(asyncio.CancelledError) as error:
                await asyncio.wait_for(task, timeout=12)
            assert "commit_outcome_unknown" in error.value.__notes__
            assert conn.closed
        finally:
            await conn.close()

    with cancel_commit(database[0]) as control:
        asyncio.run(exercise(control))
    assert receipts == []
    with control_connection(database[0]) as conn:
        rows = conn.execute(sql.SQL("SELECT value FROM {}").format(commit_rows)).fetchall()
        assert rows == [(1,)]


def observe_body(env):
    return {
        "scope_id": str(env.scopes[0]),
        "source_namespace": "commit-cancellation",
        "source_event_id": str(uuid4()),
        "occurred_at": "2026-09-01T00:00:00Z",
        "content": "SYNTHETIC_PRIVATE_COMMIT_PAYLOAD",
        "consent_reference": "test-consent",
    }


def episode_and_receipt_counts(env):
    with control_connection(env.admin_url) as conn:
        return conn.execute(
            """SELECT
               (SELECT count(*) FROM memory.episode WHERE tenant_id=%s),
               (SELECT count(*) FROM memory_ops.idempotency WHERE tenant_id=%s)""",
            (env.tenants[0], env.tenants[0]),
        ).fetchone()


@pytest.mark.integration
def test_api_suppresses_201_native_marks_unknown_and_replay_is_only_local(
    env, missing_standby, monkeypatch,
):
    original = api.principal_connection
    body, headers = observe_body(env), env.headers()
    with cancel_commit(env.admin_url) as control:
        @asynccontextmanager
        async def armed_connection(*args, **kwargs):
            async with original(*args, **kwargs) as (conn, identity):
                # principal_connection is outside the mutation transaction.
                await conn.execute(
                    "SELECT set_config('application_name', %s, false)", (control.name,)
                )
                await conn.execute("SET synchronous_commit='remote_apply'")
                yield conn, identity

        with monkeypatch.context() as patch:
            patch.setattr(api, "principal_connection", armed_connection)
            response = env.client.post("/v1/observe", json=body, headers=headers)

    assert response.status_code == 503
    payload = response.json()
    assert set(payload) == {"code", "request_id", "retryable", "details"}
    assert payload["code"] == "commit_outcome_unknown"
    assert payload["retryable"] is False
    assert payload["details"] == {}
    assert response.headers["x-request-id"] == payload["request_id"]
    assert response.headers["cache-control"] == "no-store"
    assert "SYNTHETIC_PRIVATE" not in response.text
    assert "replication" not in response.text and "memory_id" not in response.text
    assert episode_and_receipt_counts(env) == (1, 1)

    async def native_error():
        calls = []

        async def transport(request):
            calls.append(request)
            return httpx.Response(
                response.status_code, headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )

        async with httpx.AsyncClient(
            base_url="http://localhost", transport=httpx.MockTransport(transport)
        ) as client:
            with pytest.raises(AdapterFailure) as error:
                await NativeHTTPClient(client).exchange(
                    "/v1/observe", body=json.dumps(body).encode(),
                    key=headers["Idempotency-Key"], mutation=True,
                )
        assert error.value.error.code == "commit_outcome_unknown"
        assert error.value.error.outcome_unknown
        assert not error.value.error.retryable
        assert len(calls) == 1

    asyncio.run(native_error())
    with control_connection(env.admin_url) as conn:
        local_receipt = conn.execute(
            """SELECT result FROM memory_ops.idempotency
               WHERE tenant_id=%s AND operation='observe'""",
            (env.tenants[0],),
        ).fetchone()[0]
    observed_replay = []
    original_observe = MemoryService.observe
    with cancel_commit(env.admin_url, expect_wait=False) as replay_control:
        @asynccontextmanager
        async def replay_connection(*args, **kwargs):
            async with original(*args, **kwargs) as (conn, identity):
                await conn.execute(
                    "SELECT set_config('application_name', %s, false)", (replay_control.name,)
                )
                await conn.execute("SET synchronous_commit='remote_apply'")
                yield conn, identity

        async def inspect_replay(self, *args, **kwargs):
            result = await original_observe(self, *args, **kwargs)
            observed_replay.append(await (await self.conn.execute(
                """SELECT pg_current_xact_id_if_assigned() IS NULL AS no_write_xid,
                          current_setting('synchronous_commit') AS commit_policy"""
            )).fetchone())
            return result

        with monkeypatch.context() as patch:
            patch.setattr(api, "principal_connection", replay_connection)
            patch.setattr(MemoryService, "observe", inspect_replay)
            replay = env.client.post("/v1/observe", json=body, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == local_receipt
    assert set(replay.json()) == {"memory_id", "revision", "synthesis_job_id"}
    assert observed_replay == [{"no_write_xid": True, "commit_policy": "remote_apply"}]
    assert episode_and_receipt_counts(env) == (1, 1)
    # A read-only replay resolves the local receipt, not the cancelled remote acknowledgement.
    with control_connection(env.admin_url) as conn:
        assert conn.execute("SELECT count(*) FROM pg_stat_replication").fetchone() == (0,)


@pytest.mark.integration
def test_statement_timeout_before_commit_rolls_back_without_unknown(
    database, commit_rows,
):
    receipts = []
    with psycopg.connect(database[0], autocommit=True) as conn:
        with pytest.raises(psycopg.errors.QueryCanceled) as error:
            with transaction(conn):
                conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(commit_rows))
                conn.execute("SET LOCAL statement_timeout='25ms'")
                conn.execute("SELECT pg_sleep(10)")
            receipts.append("committed")
    assert not isinstance(error.value, CommitOutcomeUnknown)
    assert receipts == []
    with control_connection(database[0]) as conn:
        row = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(commit_rows)).fetchone()
        assert row == (0,)


@pytest.mark.integration
def test_api_statement_timeout_before_commit_returns_existing_dependency_error(env, monkeypatch):
    original = MemoryService.observe

    async def interrupted(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        await self.conn.execute("SET LOCAL statement_timeout='25ms'")
        await self.conn.execute("SELECT pg_sleep(10)")
        pytest.fail("The pre-COMMIT statement must time out")

    monkeypatch.setattr(MemoryService, "observe", interrupted)
    response = env.client.post("/v1/observe", json=observe_body(env), headers=env.headers())
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert episode_and_receipt_counts(env) == (0, 0)


@pytest.mark.integration
def test_admin_commit_cancellation_suppresses_receipt_but_advances_notice_cursor(
    env, missing_standby, monkeypatch,
):
    identity = SourceIdentity(
        source_system="synthetic", dataset_id="commit-outcomes", source_subject="reader"
    )
    request = {
        "tenant_id": env.tenants[0],
        "scope_id": env.scopes[0],
        "principal_id": env.principals[2],
    }
    with source_access.source_access(env.admin_url, SourceAccessRequest(
        **request, operation="bind", source=identity, expected_access_epoch=1,
    )) as bound:
        epoch = bound.access_epoch
    notice = SourceNotice(
        source=identity, sequence=1, decision="deny", reason="revoked",
    )
    apply = SourceAccessRequest(
        **request, operation="apply", expected_access_epoch=epoch, notice=notice,
    )
    original = source_access.admin_connection
    receipts = []
    with cancel_commit(env.admin_url) as control:
        @contextmanager
        def armed_connection(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                conn.execute(
                    "SELECT set_config('application_name', %s, false)", (control.name,)
                )
                conn.execute("SET synchronous_commit='remote_apply'")
                yield conn

        with monkeypatch.context() as patch:
            patch.setattr(source_access, "admin_connection", armed_connection)
            with pytest.raises(AdminError) as error:
                with source_access.source_access(env.admin_url, apply) as result:
                    receipts.append(result)
    assert error.value.code == "commit_outcome_unknown"
    assert error.value.outcome_unknown
    assert str(error.value) == "commit_outcome_unknown"
    assert receipts == []
    with source_access.source_access(
        env.admin_url, SourceAccessRequest(**request, operation="get")
    ) as current:
        assert current.sequence == 1
        assert current.decision == "deny" and current.reason == "revoked"
    with control_connection(env.admin_url) as conn:
        assert conn.execute(
            """SELECT count(*) FROM memory_ops.source_access_event
               WHERE tenant_id=%s AND sequence=1""",
            (env.tenants[0],),
        ).fetchone() == (1,)


@pytest.mark.integration
@pytest.mark.parametrize(
    "stage,transaction_number", [("claim", 1), ("reservation", 2), ("publication", 3)],
)
def test_worker_uncertain_commit_stops_without_failure_or_another_provider_call(
    env, missing_standby, profile, monkeypatch, stage, transaction_number,
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
    with cancel_commit(env.admin_url) as control:
        @asynccontextmanager
        async def armed_transaction(*args, **kwargs):
            transactions.append(len(transactions) + 1)
            async with original(*args, **kwargs) as jobs:
                if len(transactions) == transaction_number:
                    await control.arm_async(jobs.conn)
                yield jobs

        monkeypatch.setattr(worker, "job_transaction", armed_transaction)
        with pytest.raises(CommitOutcomeUnknown) as error:
            asyncio.run(worker.run_once(
                env.settings.database_url, env.subjects[0], profile=profile,
            ))
    assert_unknown(error.value)
    assert len(transactions) == transaction_number
    assert len(calls) == (1 if stage == "publication" else 0)
    with control_connection(env.admin_url) as conn:
        state, attempt, error_code = conn.execute(
            "SELECT state,attempt,error_code FROM memory_ops.job WHERE id=%s", (job_id,),
        ).fetchone()
        assert state == ("succeeded" if stage == "publication" else "running")
        assert attempt == 1 and error_code is None
        reservations = conn.execute(
            "SELECT outcome,billing_unknown FROM memory_ops.model_call WHERE job_id=%s",
            (job_id,),
        ).fetchall()
        if stage == "claim":
            assert reservations == []
        elif stage == "reservation":
            assert reservations == [("unknown", True)]
        else:
            assert reservations == [("succeeded", False)]
        assert conn.execute(
            "SELECT count(*) FROM memory.assertion WHERE tenant_id=%s", (env.tenants[0],),
        ).fetchone() == (1 if stage == "publication" else 0,)
