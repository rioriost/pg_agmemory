"""Real asyncio cancellation, with connection ownership and driver wait coverage."""

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from psycopg.pq import PipelineStatus, TransactionStatus

import pg_agmemory.request_deadline as deadlines
import pg_agmemory.service as service
from pg_agmemory.request_deadline import RequestDeadline, RequestDeadlineExceeded
from pg_agmemory.transactions import CommitOutcomeUnknown, _CommitGuard


class PgConnection:
    def __init__(self, events):
        self.events = events
        self.transaction_status = TransactionStatus.ACTIVE
        self.socket = 1
        self.finish_calls = 0
        self.owner = threading.get_ident()

    def finish(self):
        assert threading.get_ident() == self.owner
        self.finish_calls += 1
        self.events.append("finish")
        self.transaction_status = TransactionStatus.UNKNOWN


class Connection:
    def __init__(self, events=None):
        self.events = [] if events is None else events
        self.pgconn = PgConnection(self.events)

    async def _try_cancel(self, *, timeout):
        self.events.append("driver_cancel")
        raise AssertionError("disposed driver must not cancel or drain")


@pytest.fixture
def short_budget(monkeypatch):
    monkeypatch.setattr(deadlines, "REQUEST_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(deadlines, "ERROR_RESPONSE_RESERVE_SECONDS", 0.02)


def run(coroutine):
    return asyncio.run(coroutine)


def test_fixed_budget_and_safe_exception():
    assert deadlines.REQUEST_TIMEOUT_SECONDS == 30.0
    assert deadlines.ERROR_RESPONSE_RESERVE_SECONDS == 1.0
    failure = RequestDeadlineExceeded()
    assert str(failure) == "request_deadline_exceeded"
    assert not isinstance(failure, TimeoutError)
    assert not failure.commit_outcome_unknown

    async def scenario():
        loop = asyncio.get_running_loop()
        before = loop.time()
        deadline = RequestDeadline("request")
        assert before + 30 <= deadline.expires_at <= loop.time() + 30
        assert not deadline.expired
        assert deadline.expires_at - deadline._work_deadline == 1

    run(scenario())


def test_budget_accumulates_across_processing_and_phases(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        expires_at = deadline.expires_at
        await asyncio.sleep(0.02)
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                await asyncio.sleep(0.02)
                deadline.check()
                await asyncio.sleep(1)
        assert deadline.expired
        assert deadline.expires_at == expires_at
        assert asyncio.get_running_loop().time() < expires_at + 0.2
        assert asyncio.current_task().cancelling() == 0
        assert deadline._timer is None

    run(scenario())


def test_late_synchronous_check_disposes_before_timer_runs(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        conn = Connection()
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                with deadline.connection(conn):
                    time.sleep(0.08)
                    assert not deadline.expired
                    deadline.check()
        assert deadline.expired
        assert conn.events == ["finish"]
        assert asyncio.current_task().cancelling() == 0
        with pytest.raises(RequestDeadlineExceeded):
            deadline.check()
        assert conn.pgconn.finish_calls == 1

    run(scenario())


def test_late_normal_return_is_not_success(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                time.sleep(0.08)
        assert deadline.expired

    run(scenario())


def test_attaching_after_expiry_discards_before_admission(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        time.sleep(0.08)
        conn = Connection()
        with pytest.raises(RequestDeadlineExceeded), deadline.connection(conn):
            pytest.fail("expired connection reached admission")
        assert conn.events == ["finish"]
        assert deadline._connection is None

    run(scenario())


def test_processing_entered_after_expiry_never_starts_work(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        time.sleep(0.08)
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                pytest.fail("expired processing started")
        assert deadline._timer is None
        await asyncio.sleep(0)
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


def test_driver_wait_disposes_before_real_cancellation(short_budget, monkeypatch):
    async def wait_async(gen, socket, *, interval):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            conn.events.append("cancelled")
            raise

    conn = Connection()
    monkeypatch.setattr(psycopg.waiting, "wait_async", wait_async)

    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                with deadline.connection(conn):
                    await psycopg.AsyncConnection.wait(conn, iter(()))
        assert conn.events == ["finish", "cancelled"]
        assert conn.pgconn.finish_calls == 1
        assert deadline._connection is None
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


def test_disposal_precedes_cancel_with_many_equal_time_callbacks(short_budget):
    async def scenario():
        loop = asyncio.get_running_loop()
        deadline = RequestDeadline("request")
        conn = Connection()
        callbacks = [loop.call_at(deadline._work_deadline, lambda: None) for _ in range(100)]
        try:
            with pytest.raises(RequestDeadlineExceeded):
                async with deadline.processing():
                    with deadline.connection(conn):
                        try:
                            await asyncio.Future()
                        except asyncio.CancelledError:
                            assert conn.pgconn.finish_calls == 1
                            raise
        finally:
            for callback in callbacks:
                callback.cancel()

    run(scenario())


def test_detached_connection_and_next_request_are_not_disposed(short_budget):
    async def scenario():
        conn = Connection()
        deadline = RequestDeadline("first")
        async with deadline.processing():
            with deadline.connection(conn):
                pass
        assert deadline._connection is None
        assert deadline._timer is None
        await asyncio.sleep(0.04)
        next_deadline = RequestDeadline("second")
        async with next_deadline.processing():
            with next_deadline.connection(conn):
                await asyncio.sleep(0.03)
        assert conn.pgconn.finish_calls == 0
        await asyncio.sleep(0.08)
        assert conn.pgconn.finish_calls == 0
        assert not deadline.expired
        assert not next_deadline.expired

    run(scenario())


def test_expired_deadline_never_disposes_detached_or_other_connection(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        conn, other = Connection(), Connection()
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                with deadline.connection(conn):
                    pass
                await asyncio.Future()
        assert conn.pgconn.finish_calls == 0
        assert other.pgconn.finish_calls == 0

    run(scenario())


def test_processing_is_one_shot(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        async with deadline.processing():
            pass
        with pytest.raises(RuntimeError, match="request_deadline_already_started"):
            async with deadline.processing():
                pass

    run(scenario())


def test_nested_and_pooled_connections_are_rejected(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        conn, other = Connection(), Connection()
        async with deadline.processing():
            with deadline.connection(conn):
                with pytest.raises(RuntimeError, match="request_deadline_connection_in_use"):
                    with deadline.connection(other):
                        pass
            other._pool = object()
            with pytest.raises(RuntimeError, match="request_deadline_pooled_connection"):
                with deadline.connection(other):
                    pass
        assert not conn.events and not other.events

    run(scenario())


def test_unrelated_timeout_is_not_a_request_deadline(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        original = TimeoutError("unrelated")
        with pytest.raises(TimeoutError) as caught:
            async with deadline.processing():
                raise original
        assert caught.value is original
        assert not deadline.expired
        assert deadline._timer is None

    run(scenario())


def test_nested_timeout_is_not_a_request_deadline(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(TimeoutError):
            async with deadline.processing():
                async with asyncio.timeout(0.005):
                    await asyncio.Future()
        assert not deadline.expired
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


@pytest.mark.parametrize("masked", [False, True])
def test_external_cancellation_is_preserved(short_budget, masked):
    async def scenario():
        entered = asyncio.Event()
        deadline = RequestDeadline("request")

        async def work():
            async with deadline.processing():
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError as error:
                    error.add_note(CommitOutcomeUnknown.code)
                    if masked:
                        raise RuntimeError("private cleanup error") from error
                    raise

        task = asyncio.create_task(work())
        await entered.wait()
        task.cancel("external")
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("external",)
        assert CommitOutcomeUnknown.code in caught.value.__notes__
        assert task.cancelling() == 1
        assert not deadline.expired
        assert deadline._timer is None

    run(scenario())


@pytest.mark.parametrize("masked", [False, True])
def test_external_cancellation_racing_own_deadline_wins(short_budget, masked):
    async def scenario():
        deadline = RequestDeadline("request")

        async def work():
            async with deadline.processing():
                try:
                    await asyncio.Future()
                except asyncio.CancelledError as error:
                    error.add_note(CommitOutcomeUnknown.code)
                    if masked:
                        raise RuntimeError("private cleanup error") from error
                    raise

        task = asyncio.create_task(work())
        loop = asyncio.get_running_loop()
        external = loop.call_at(deadline._work_deadline, task.cancel, "external")
        try:
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
        finally:
            external.cancel()
        assert CommitOutcomeUnknown.code in caught.value.__notes__
        assert task.cancelling() == 1
        assert deadline.expired
        assert deadline._timer is None

    run(scenario())


def test_existing_cancellation_count_is_preserved(short_budget):
    async def work():
        task = asyncio.current_task()
        task.cancel("previous")
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        assert task.cancelling() == 1
        deadline = RequestDeadline("request")
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                await asyncio.Future()
        assert task.cancelling() == 1
        task.uncancel()

    run(work())


def test_swallowed_external_cancellation_is_not_success(short_budget):
    async def scenario():
        entered = asyncio.Event()
        deadline = RequestDeadline("request")

        async def work():
            async with deadline.processing():
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    pass

        task = asyncio.create_task(work())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelling() == 1
        assert not deadline.expired

    run(scenario())


def test_actual_commit_guard_marks_deadline_cancellation_unknown(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        conn = Connection()
        conn.info = SimpleNamespace(
            pipeline_status=PipelineStatus.OFF,
            transaction_status=TransactionStatus.IDLE,
        )
        guard = _CommitGuard(conn)
        guard.committing = True
        with pytest.raises(RequestDeadlineExceeded) as caught:
            async with deadline.processing():
                with deadline.connection(conn):
                    with guard.commit_phase(asyncio.current_task()):
                        await asyncio.Future()
        assert caught.value.commit_outcome_unknown
        assert conn.pgconn.finish_calls == 2
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


@pytest.mark.parametrize("masked", [False, True])
def test_commit_guard_note_survives_timeout_conversion(short_budget, masked):
    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(RequestDeadlineExceeded) as caught:
            async with deadline.processing():
                try:
                    await asyncio.Future()
                except asyncio.CancelledError as error:
                    error.add_note(CommitOutcomeUnknown.code)
                    if masked:
                        raise psycopg.OperationalError("private cleanup error") from error
                    raise
        assert caught.value.commit_outcome_unknown
        assert str(caught.value) == "request_deadline_exceeded"
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


def test_late_commit_outcome_exception_is_never_ordinary_deadline(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(RequestDeadlineExceeded) as caught:
            async with deadline.processing():
                time.sleep(0.08)
                raise CommitOutcomeUnknown()
        assert caught.value.commit_outcome_unknown

    run(scenario())


def test_suppressed_own_cancellation_still_fails_closed(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    pass
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


def test_cleanup_failure_after_own_cancellation_fails_closed(short_budget):
    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(RequestDeadlineExceeded) as caught:
            async with deadline.processing():
                try:
                    await asyncio.Future()
                finally:
                    raise psycopg.OperationalError("private cleanup failure")
        assert not caught.value.commit_outcome_unknown
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_process_interruptions_are_not_converted_after_deadline(short_budget, interruption):
    async def scenario():
        deadline = RequestDeadline("request")
        with pytest.raises(interruption):
            async with deadline.processing():
                try:
                    await asyncio.Future()
                finally:
                    raise interruption()
        assert deadline.expired
        assert asyncio.current_task().cancelling() == 0

    run(scenario())


@pytest.mark.parametrize("synchronous", [False, True])
def test_disposal_failure_is_logged_safely_and_never_success(
    short_budget, monkeypatch, caplog, synchronous
):
    conn = Connection()

    def fail_finish():
        conn.pgconn.finish_calls += 1
        raise RuntimeError("private connection string")

    monkeypatch.setattr(conn.pgconn, "finish", fail_finish)
    caplog.set_level(logging.ERROR, logger=deadlines.__name__)

    async def scenario():
        deadline = RequestDeadline("safe-request-id")
        with pytest.raises(RequestDeadlineExceeded) as caught:
            async with deadline.processing():
                with deadline.connection(conn):
                    if synchronous:
                        time.sleep(0.08)
                        deadline.check()
                    else:
                        await asyncio.Future()
        assert "connection_disposal_failed" in caught.value.__notes__
        assert conn.pgconn.finish_calls == 1
        assert deadline._connection is None
        assert deadline._timer is None

    run(scenario())
    assert "request_deadline_connection_disposal_failed" in caplog.text
    assert "safe-request-id" in caplog.text
    assert "private connection string" not in caplog.text
    assert caplog.records[0].exc_info is None


def test_other_event_loop_cannot_attach_connection():
    async def create():
        return RequestDeadline("request")

    deadline = run(create())
    conn = Connection()

    async def scenario():
        with pytest.raises(RuntimeError, match="request_deadline_wrong_event_loop"):
            with deadline.connection(conn):
                pass
        assert not conn.events

    run(scenario())


class PrincipalConnection(Connection):
    def __init__(self, deadline=None, *, stall=None, missing=False):
        super().__init__()
        self.deadline = deadline
        self.stall = stall
        self.missing = missing
        self.row = {"tenant_id": UUID(int=1), "id": UUID(int=2)}

    async def stage(self, name):
        self.events.append(name)
        if self.deadline is not None:
            assert self.deadline._connection is self
        if self.stall == name:
            await asyncio.Future()

    async def __aenter__(self):
        await self.stage("enter")
        return self

    async def __aexit__(self, *args):
        await self.stage("close")

    @asynccontextmanager
    async def transaction(self):
        await self.stage("transaction")
        yield

    async def execute(self, sql, parameters):
        if "pg_advisory_lock" in sql:
            await self.stage("admission")
        elif "FROM memory.principal" in sql:
            await self.stage("lookup")
        else:
            await self.stage("subject")

        async def fetchone():
            return None if self.missing else self.row

        return SimpleNamespace(fetchone=fetchone)


@pytest.mark.parametrize("stage", ["enter", "lookup", "admission", "yield", "close"])
def test_principal_connection_attaches_through_admission_and_close(
    short_budget, monkeypatch, stage
):
    async def scenario():
        deadline = RequestDeadline("request")
        conn = PrincipalConnection(deadline, stall=stage)

        async def connect(url):
            return conn

        monkeypatch.setattr(service, "connect", connect)
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                async with service.principal_connection("url", "subject", deadline=deadline):
                    await conn.stage("yield")
        assert conn.pgconn.finish_calls == 1
        assert deadline._connection is None

    run(scenario())


def test_principal_connect_returning_late_never_enters_connection(short_budget, monkeypatch):
    async def scenario():
        deadline = RequestDeadline("request")
        conn = PrincipalConnection(deadline)

        async def connect(url):
            time.sleep(0.08)
            return conn

        monkeypatch.setattr(service, "connect", connect)
        with pytest.raises(RequestDeadlineExceeded):
            async with deadline.processing():
                async with service.principal_connection("url", "subject", deadline=deadline):
                    pytest.fail("late connection reached request body")
        assert conn.events == ["finish"]
        assert deadline._connection is None

    run(scenario())


@pytest.mark.parametrize("missing", [False, True])
def test_ordinary_principal_callers_remain_unchanged(monkeypatch, missing):
    async def scenario():
        conn = PrincipalConnection(missing=missing)

        async def connect(url):
            return conn

        monkeypatch.setattr(service, "connect", connect)
        if missing:
            with pytest.raises(service.MemoryError) as caught:
                async with service.principal_connection("url", "subject"):
                    pytest.fail("missing identity reached request body")
            assert caught.value.code == "unauthenticated"
            assert caught.value.status == 401
        else:
            async with service.principal_connection("url", "subject") as (actual, identity):
                assert actual is conn
                assert identity.tenant_id == conn.row["tenant_id"]
                assert identity.principal_id == conn.row["id"]
        assert conn.events[-1] == "close"
        assert conn.pgconn.finish_calls == 0

    run(scenario())


def test_principal_body_errors_are_unchanged(short_budget, monkeypatch):
    async def scenario():
        deadline = RequestDeadline("request")
        conn = PrincipalConnection(deadline)
        original = RuntimeError("ordinary body error")

        async def connect(url):
            return conn

        monkeypatch.setattr(service, "connect", connect)
        with pytest.raises(RuntimeError) as caught:
            async with deadline.processing():
                async with service.principal_connection("url", "subject", deadline=deadline):
                    raise original
        assert caught.value is original
        assert conn.events[-1] == "close"
        assert conn.pgconn.finish_calls == 0
        assert deadline._connection is None

    run(scenario())
