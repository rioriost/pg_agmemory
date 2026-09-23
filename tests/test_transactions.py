"""Commit diagnostics must not turn ambiguous durability into success or retries."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.pq import PipelineStatus, TransactionStatus

from pg_agmemory.admin import admin_failure
from pg_agmemory.transactions import CommitOutcomeUnknown, async_transaction, transaction

PRIVATE = "private SQL, diagnostic, and postgresql://user:password@host/database"


def notice(code="57014", severity="WARNING"):
    return SimpleNamespace(
        sqlstate=code, severity_nonlocalized=severity,
        severity="AVERTISSEMENT", message_primary=PRIVATE, message_detail=PRIVATE,
    )


class FakeConnection:
    def __init__(self):
        self.info = SimpleNamespace(
            transaction_status=TransactionStatus.IDLE, pipeline_status=PipelineStatus.OFF,
        )
        self.pgconn = SimpleNamespace(finish=self.finish)
        self.closed = False
        self.handlers = []
        self.events = []
        self.queries = []
        self.visibility_error = None
        self.commit_notice = None
        self.enter_error = None
        self.commit_error = None
        self.rollback_error = None
        self.remove_error = None
        self.finish_error = None
        self.rollback_stuck = False
        self.commit_entered = None
        self.commit_release = None

    def transaction(self):
        return FakeTransaction(self)

    def add_notice_handler(self, handler):
        self.handlers.append(handler)

    def remove_notice_handler(self, handler):
        if self.remove_error:
            raise self.remove_error
        self.handlers.remove(handler)

    def emit(self, diagnostic):
        for handler in self.handlers:
            # Deliberately do not swallow callback exceptions: none may escape.
            handler(diagnostic)

    def execute(self, query):
        self.queries.append(query)
        if self.visibility_error:
            raise self.visibility_error
        return AsyncResult()

    def finish(self):
        self.events.append("disconnect")
        self.closed = True
        if self.finish_error:
            raise self.finish_error


class AsyncResult:
    def __await__(self):
        return iter(())


class FakeTransaction:
    def __init__(self, conn):
        self.conn = conn
        self.savepoint_name = None
        self.force_rollback = False

    def __enter__(self):
        if self.conn.enter_error:
            raise self.conn.enter_error
        if self.conn.info.transaction_status != TransactionStatus.IDLE:
            self.savepoint_name = "savepoint"
        self.conn.events.append("savepoint" if self.savepoint_name else "begin")
        self.conn.info.transaction_status = TransactionStatus.INTRANS
        return self

    def __exit__(self, typ, error, traceback):
        if self.conn.closed:
            return False
        if error is not None or self.force_rollback:
            self.conn.events.append("rollback_savepoint" if self.savepoint_name else "rollback")
            if self.conn.rollback_error:
                raise self.conn.rollback_error
            if not self.conn.rollback_stuck:
                self.conn.info.transaction_status = (
                    TransactionStatus.INTRANS if self.savepoint_name else TransactionStatus.IDLE
                )
            return isinstance(error, psycopg.Rollback) and (
                error.transaction is None or error.transaction is self
            )
        self.conn.events.append("release" if self.savepoint_name else "commit")
        if self.conn.commit_notice is not None:
            self.conn.emit(self.conn.commit_notice)
        self.conn.info.transaction_status = (
            TransactionStatus.INTRANS if self.savepoint_name else TransactionStatus.IDLE
        )
        if self.conn.commit_error:
            raise self.conn.commit_error
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, typ, error, traceback):
        if error is None and self.conn.commit_entered is not None:
            self.conn.commit_entered.set()
            await self.conn.commit_release.wait()
        return self.__exit__(typ, error, traceback)


def run_guard(kind, conn, body=lambda tx: None):
    if kind == "sync":
        with transaction(conn) as tx:
            body(tx)
    else:
        async def run():
            async with async_transaction(conn) as tx:
                body(tx)
        asyncio.run(run())


@pytest.fixture(params=["sync", "async"])
def kind(request):
    return request.param


def test_normal_commit_preserves_transaction_object_and_existing_handlers(kind):
    conn = FakeConnection()
    existing = lambda diagnostic: None  # noqa: E731
    conn.add_notice_handler(existing)
    yielded = []
    run_guard(kind, conn, yielded.append)
    assert yielded[0].conn is conn
    assert conn.events == ["begin", "commit"]
    assert conn.handlers == [existing]
    assert conn.queries == ["SET LOCAL client_min_messages = warning"]
    assert not conn.closed


def test_body_error_rolls_back_and_preserves_original(kind):
    conn = FakeConnection()
    original = ValueError(PRIVATE)

    def body(tx):
        raise original

    with pytest.raises(ValueError) as caught:
        run_guard(kind, conn, body)
    assert caught.value is original
    assert conn.events == ["begin", "rollback"]
    assert not conn.handlers
    assert not conn.closed


def test_pipeline_is_rejected_before_diagnostics_can_be_misattributed(kind):
    conn = FakeConnection()
    conn.info.pipeline_status = PipelineStatus.ON
    with pytest.raises(psycopg.NotSupportedError, match="guarded_transaction_pipeline_unsupported"):
        run_guard(kind, conn)
    assert conn.events == []
    assert not conn.handlers


def test_begin_error_preserves_original_and_discards_driver_state(kind):
    conn = FakeConnection()
    conn.enter_error = psycopg.OperationalError(PRIVATE)
    with pytest.raises(psycopg.OperationalError) as caught:
        run_guard(kind, conn)
    assert caught.value is conn.enter_error
    assert conn.closed
    assert not conn.handlers


def test_warning_visibility_preparation_failure_rolls_back_before_commit(kind):
    conn = FakeConnection()
    original = psycopg.errors.QueryCanceled(PRIVATE)
    conn.visibility_error = original
    with pytest.raises(psycopg.errors.QueryCanceled) as caught:
        run_guard(kind, conn)
    assert caught.value is original
    assert conn.events == ["begin", "rollback"]
    assert not conn.closed
    assert not conn.handlers


@pytest.mark.parametrize("lost_response", [False, True])
def test_decorated_transaction_without_yielded_driver_attributes(kind, lost_response):
    conn = FakeConnection()
    original = conn.transaction

    @contextmanager
    def wrapped():
        with original():
            yield
        if lost_response:
            raise psycopg.OperationalError(PRIVATE)

    @asynccontextmanager
    async def async_wrapped():
        async with original():
            yield
        if lost_response:
            raise psycopg.OperationalError(PRIVATE)

    conn.transaction = wrapped if kind == "sync" else async_wrapped
    if lost_response:
        with pytest.raises(CommitOutcomeUnknown) as caught:
            run_guard(kind, conn)
        assert caught.value.local_committed is None
        assert conn.closed
    else:
        run_guard(kind, conn)
        assert not conn.closed
    assert not conn.handlers


@pytest.mark.parametrize("code", ["01000", "57014", "57P01"])
def test_cancel_warning_after_local_commit_is_unknown(kind, code):
    conn = FakeConnection()
    conn.commit_notice = notice(code)
    with pytest.raises(CommitOutcomeUnknown) as caught:
        run_guard(kind, conn)
    error = caught.value
    assert not isinstance(error, psycopg.Error)
    assert str(error) == error.code == "commit_outcome_unknown"
    assert error.local_committed is True
    assert PRIVATE not in repr(error)
    assert conn.events == ["begin", "commit", "disconnect"]
    assert not conn.handlers


@pytest.mark.parametrize(
    ("code", "severity"), [("00000", "WARNING"), ("57014", "NOTICE"), ("01000", "NOTICE")],
)
def test_unrelated_commit_diagnostics_do_not_poison_outcome(kind, code, severity):
    conn = FakeConnection()
    conn.commit_notice = notice(code, severity)
    run_guard(kind, conn)
    assert not conn.closed
    assert not conn.handlers


@pytest.mark.parametrize("code", ["01000", "57014", "57P01"])
def test_body_warning_and_previous_transaction_cannot_poison_commit(kind, code):
    conn = FakeConnection()
    run_guard(kind, conn, lambda tx: conn.emit(notice(code)))
    run_guard(kind, conn)
    assert conn.events == ["begin", "commit", "begin", "commit"]
    assert not conn.handlers
    assert not conn.closed


def test_notice_callback_never_raises_or_retains_diagnostic(kind):
    class BrokenDiagnostic:
        @property
        def sqlstate(self):
            raise ValueError(PRIVATE)

    conn = FakeConnection()
    conn.commit_notice = BrokenDiagnostic()
    with pytest.raises(CommitOutcomeUnknown) as caught:
        run_guard(kind, conn)
    assert caught.value.local_committed is True
    assert str(caught.value) == "commit_outcome_unknown"
    assert not conn.handlers


@pytest.mark.parametrize("error_type", [
    psycopg.OperationalError, psycopg.errors.QueryCanceled,
    psycopg.errors.StatementCompletionUnknown, RuntimeError,
])
def test_commit_error_is_conservatively_unknown_and_sanitized(kind, error_type):
    conn = FakeConnection()
    conn.commit_error = error_type(PRIVATE)
    with pytest.raises(CommitOutcomeUnknown) as caught:
        run_guard(kind, conn)
    assert caught.value.local_committed is None
    assert str(caught.value) == "commit_outcome_unknown"
    assert caught.value.__suppress_context__
    assert caught.value.__cause__ is None
    assert conn.closed
    assert not conn.handlers


@pytest.mark.parametrize("error_type", [
    psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected,
    psycopg.errors.ForeignKeyViolation, psycopg.errors.UniqueViolation,
])
def test_server_confirmed_rollback_keeps_original_error(kind, error_type):
    conn = FakeConnection()
    conn.commit_error = error_type(PRIVATE)
    with pytest.raises(error_type) as caught:
        run_guard(kind, conn)
    assert caught.value is conn.commit_error
    assert not conn.closed
    assert not conn.handlers


def test_warning_wins_over_apparently_retryable_commit_error(kind):
    conn = FakeConnection()
    conn.commit_notice = notice()
    conn.commit_error = psycopg.errors.SerializationFailure(PRIVATE)
    with pytest.raises(CommitOutcomeUnknown):
        run_guard(kind, conn)
    assert conn.closed


def test_driver_silent_exit_on_lost_connection_is_not_success(kind):
    conn = FakeConnection()
    with pytest.raises(CommitOutcomeUnknown) as caught:
        run_guard(kind, conn, lambda tx: conn.finish())
    assert caught.value.local_committed is None
    assert "commit" not in conn.events
    assert not conn.handlers


def test_aborted_transaction_cannot_silently_succeed(kind):
    conn = FakeConnection()

    def body(tx):
        conn.info.transaction_status = TransactionStatus.INERROR

    with pytest.raises(psycopg.errors.InFailedSqlTransaction, match="transaction_rolled_back"):
        run_guard(kind, conn, body)
    assert not conn.closed
    assert not conn.handlers


def test_explicit_rollback_preserves_driver_semantics(kind):
    conn = FakeConnection()

    def body(tx):
        raise psycopg.Rollback(tx)

    run_guard(kind, conn, body)
    assert conn.events == ["begin", "rollback"]
    assert not conn.closed
    assert not conn.handlers
    assert not conn.queries


def test_force_rollback_is_not_a_commit(kind):
    conn = FakeConnection()

    def body(tx):
        tx.force_rollback = True

    run_guard(kind, conn, body)
    assert conn.events == ["begin", "rollback"]
    assert not conn.closed
    assert not conn.queries


def test_nested_savepoints_only_guard_the_outer_commit(kind):
    conn = FakeConnection()
    conn.commit_notice = notice()
    if kind == "sync":
        with transaction(conn):
            with transaction(conn):
                pass
            assert not conn.closed
            conn.commit_notice = None
    else:
        async def run():
            async with async_transaction(conn):
                async with async_transaction(conn):
                    pass
                assert not conn.closed
                conn.commit_notice = None
        asyncio.run(run())
    assert conn.events == ["begin", "savepoint", "release", "commit"]
    assert conn.queries == ["SET LOCAL client_min_messages = warning"]
    assert not conn.handlers
    assert not conn.closed


def test_nested_rollback_restores_outer_transaction(kind):
    conn = FakeConnection()
    if kind == "sync":
        with transaction(conn):
            with pytest.raises(ValueError), transaction(conn):
                raise ValueError(PRIVATE)
    else:
        async def run():
            async with async_transaction(conn):
                with pytest.raises(ValueError):
                    async with async_transaction(conn):
                        raise ValueError(PRIVATE)
        asyncio.run(run())
    assert conn.events == ["begin", "savepoint", "rollback_savepoint", "commit"]
    assert not conn.handlers
    assert not conn.closed


def test_nested_outer_commit_warning_is_still_detected(kind):
    conn = FakeConnection()
    conn.commit_notice = notice()
    with pytest.raises(CommitOutcomeUnknown):
        if kind == "sync":
            with transaction(conn), transaction(conn):
                pass
        else:
            async def run():
                async with async_transaction(conn), async_transaction(conn):
                    pass
            asyncio.run(run())
    assert conn.events == ["begin", "savepoint", "release", "commit", "disconnect"]
    assert not conn.handlers


@pytest.mark.parametrize("swallowed", [False, True])
def test_rollback_cleanup_failure_does_not_replace_body_error(kind, swallowed):
    conn = FakeConnection()
    if swallowed:
        conn.rollback_stuck = True
    else:
        conn.rollback_error = psycopg.OperationalError(PRIVATE)
    original = ValueError("body_error")

    def body(tx):
        raise original

    with pytest.raises(ValueError) as caught:
        run_guard(kind, conn, body)
    assert caught.value is original
    assert "transaction_cleanup_failed" in caught.value.__notes__
    assert conn.closed
    assert not conn.handlers


def test_handler_cleanup_failure_after_commit_is_unknown(kind):
    conn = FakeConnection()
    conn.remove_error = ValueError(PRIVATE)
    with pytest.raises(CommitOutcomeUnknown) as caught:
        run_guard(kind, conn)
    assert caught.value.local_committed is True
    assert caught.value.__notes__ == ["notice_handler_cleanup_failed"]
    assert conn.closed


def test_handler_cleanup_failure_is_not_hidden_by_callers_exception_context(kind):
    conn = FakeConnection()
    conn.remove_error = ValueError(PRIVATE)
    try:
        raise ValueError("previous_error")
    except ValueError:
        with pytest.raises(CommitOutcomeUnknown):
            run_guard(kind, conn)
    assert conn.closed


def test_handler_cleanup_failure_preserves_body_error(kind):
    conn = FakeConnection()
    conn.remove_error = ValueError(PRIVATE)
    original = ValueError("body_error")

    def body(tx):
        raise original

    with pytest.raises(ValueError) as caught:
        run_guard(kind, conn, body)
    assert caught.value is original
    assert caught.value.__notes__ == ["notice_handler_cleanup_failed"]
    assert conn.closed


def test_connection_disposal_error_is_sanitized_without_masking_unknown(kind):
    conn = FakeConnection()
    conn.commit_error = psycopg.OperationalError(PRIVATE)
    conn.finish_error = RuntimeError(PRIVATE)
    with pytest.raises(CommitOutcomeUnknown) as caught:
        run_guard(kind, conn)
    assert caught.value.__notes__ == ["connection_disposal_failed"]
    assert PRIVATE not in str(caught.value)
    assert not conn.handlers


def test_async_cancellation_during_commit_propagates_and_discards():
    async def run():
        conn = FakeConnection()
        conn.commit_entered = asyncio.Event()
        conn.commit_release = asyncio.Event()

        async def write():
            async with async_transaction(conn):
                pass

        task = asyncio.create_task(write())
        await conn.commit_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert "commit_outcome_unknown" in caught.value.__notes__
        assert conn.closed
        assert not conn.handlers
        assert "rollback" not in conn.events

    asyncio.run(run())


def test_async_cancellation_during_body_rolls_back_and_propagates():
    async def run():
        conn = FakeConnection()
        with pytest.raises(asyncio.CancelledError):
            async with async_transaction(conn):
                raise asyncio.CancelledError()
        assert conn.events[:2] == ["begin", "rollback"]
        assert conn.closed
        assert not conn.handlers

    asyncio.run(run())


def test_async_cancellation_during_rollback_is_not_replaced_with_body_error():
    async def run():
        conn = FakeConnection()
        conn.rollback_error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            async with async_transaction(conn):
                raise ValueError(PRIVATE)
        assert conn.closed
        assert not conn.handlers

    asyncio.run(run())


def test_sync_interrupt_during_commit_is_not_reported_as_rollback():
    conn = FakeConnection()
    conn.commit_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught:
        run_guard("sync", conn)
    assert caught.value.__notes__ == ["commit_outcome_unknown"]
    assert conn.closed
    assert not conn.handlers


@pytest.mark.parametrize("commit_attempted", [False, True])
def test_admin_failure_explicitly_maps_unknown(commit_attempted):
    failure = admin_failure(CommitOutcomeUnknown(local_committed=True), commit_attempted)
    assert failure.code == "commit_outcome_unknown"
    assert failure.outcome_unknown is True
    assert str(failure) == "commit_outcome_unknown"
