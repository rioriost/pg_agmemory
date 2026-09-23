"""Explicit commit boundaries; successful local commit need not complete a sync wait.

Only an outer transaction commits. Nested uses retain psycopg's savepoint and
Rollback semantics and do not establish a durability boundary. Connections must
not be used concurrently, or committed manually, while a guard is active.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import psycopg
from psycopg.pq import PipelineStatus, TransactionStatus

from pg_agmemory.commit_deadline import CommitDeadline

_Connection = psycopg.Connection[Any] | psycopg.AsyncConnection[Any]
_Transaction = psycopg.Transaction | psycopg.AsyncTransaction
_INTERRUPTED = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
_COMMIT_WARNING_CODES = frozenset(("01000", "57014", "57P01"))
_ENABLE_COMMIT_WARNINGS = "SET LOCAL client_min_messages = warning"


class CommitOutcomeUnknown(Exception):
    """The commit boundary cannot safely authorize success or automatic retry."""

    code = "commit_outcome_unknown"

    def __init__(self, *, local_committed: bool | None = None) -> None:
        self.local_committed = local_committed
        super().__init__(self.code)


class CommitDeadlineSetupError(psycopg.OperationalError):
    """Safe dependency failure: COMMIT was not attempted and may be retried."""

    code = "commit_deadline_setup_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


def _discard(conn: _Connection, error: BaseException) -> None:
    try:
        # close() can return a pooled connection; async close can be cancelled.
        # finish() immediately disconnects libpq in both drivers, without I/O waits.
        conn.pgconn.finish()
    except Exception:
        error.add_note("connection_disposal_failed")


class _CommitGuard:
    def __init__(self, conn: _Connection) -> None:
        if conn.info.pipeline_status != PipelineStatus.OFF:
            # Queued body diagnostics cannot be attributed to COMMIT reliably.
            raise psycopg.NotSupportedError("guarded_transaction_pipeline_unsupported")
        self.conn = conn
        self.outer = conn.info.transaction_status == TransactionStatus.IDLE
        self.committing = False
        self.interrupted = False
        self.monitor_failed = False
        self.aborted = False
        self.local_committed: bool | None = None
        self.deadline: CommitDeadline | None = None
        self.deadline_expired = False
        self.deadline_cleanup_failed = False

    def notice(self, diagnostic: psycopg.errors.Diagnostic) -> None:
        if not self.committing:
            return
        try:
            if (
                diagnostic.sqlstate in _COMMIT_WARNING_CODES
                and diagnostic.severity_nonlocalized == "WARNING"
            ):
                # PostgreSQL 18's SyncRep query-cancel warning has the generic
                # 01000 code. Do not guess from localized text: conservatively
                # treat generic COMMIT warnings as ambiguous too.
                self.interrupted = True
        except BaseException:
            # psycopg swallows callback exceptions. Fail closed outside the
            # callback, retaining neither the diagnostic nor its private text.
            self.monitor_failed = True

    def prepare(self, tx: _Transaction) -> None:
        self.committing = self.outer and not getattr(tx, "force_rollback", False)
        if not self.committing:
            return
        self.aborted = self.conn.info.transaction_status == TransactionStatus.INERROR
        if self.conn.closed:
            return
        try:
            self.deadline = CommitDeadline(self.conn.pgconn.socket)
            self.deadline.start()
        except BaseException as error:
            self.committing = False
            failure = error if isinstance(error, _INTERRUPTED) else CommitDeadlineSetupError()
            try:
                self.finish_deadline()
            except BaseException as cleanup_error:
                self.deadline_cleanup_failed = True
                if isinstance(cleanup_error, _INTERRUPTED):
                    failure = cleanup_error
                failure.add_note("commit_deadline_cleanup_failed")
            raise failure from None

    def finish_deadline(self) -> None:
        if self.deadline is not None:
            deadline, self.deadline = self.deadline, None
            self.deadline_expired = deadline.finish()

    @contextmanager
    def commit_phase(self, task: asyncio.Task[Any] | None = None) -> Iterator[None]:
        cancellations = task.cancelling() if task is not None else 0
        failure: BaseException | None = None
        try:
            yield
        except BaseException as error:
            failure = error
        try:
            self.finish_deadline()
        except BaseException as error:
            self.deadline_cleanup_failed = True
            if isinstance(error, _INTERRUPTED):
                failure = error
            elif failure is None:
                failure = CommitOutcomeUnknown()
        if (
            task is not None
            and task.cancelling() > cancellations
            and not isinstance(failure, _INTERRUPTED)
        ):
            # psycopg's post-cancel drain can replace CancelledError with the
            # socket shutdown error. Preserve the external task cancellation.
            failure = asyncio.CancelledError()
        try:
            if failure is not None:
                raise failure
            self.complete()
        except BaseException as error:
            if self.committing:
                raise self.commit_error(error) from None
            _discard(self.conn, error)
            raise

    def needs_warning_visibility(self, tx: _Transaction) -> bool:
        return (
            self.outer
            and not getattr(tx, "force_rollback", False)
            and not self.conn.closed
            and self.conn.info.transaction_status != TransactionStatus.INERROR
        )

    def complete(self) -> None:
        if not self.committing:
            return
        if self.deadline_expired:
            raise CommitOutcomeUnknown()
        if self.conn.closed or self.conn.info.transaction_status != TransactionStatus.IDLE:
            raise CommitOutcomeUnknown()
        if self.aborted:
            # PostgreSQL answers COMMIT in an aborted transaction with ROLLBACK.
            raise psycopg.errors.InFailedSqlTransaction("transaction_rolled_back")
        self.local_committed = True
        if self.interrupted or self.monitor_failed:
            raise CommitOutcomeUnknown(local_committed=True)

    def commit_error(self, error: BaseException) -> BaseException:
        if isinstance(error, _INTERRUPTED):
            error.add_note(CommitOutcomeUnknown.code)
            if self.deadline_cleanup_failed:
                error.add_note("commit_deadline_cleanup_failed")
            _discard(self.conn, error)
            return error
        if isinstance(error, CommitOutcomeUnknown):
            if self.deadline_cleanup_failed:
                error.add_note("commit_deadline_cleanup_failed")
            _discard(self.conn, error)
            return error
        if (
            isinstance(error, psycopg.Error)
            and not self.deadline_expired
            and not self.deadline_cleanup_failed
            and not self.interrupted
            and not self.monitor_failed
            and not self.conn.closed
            and self.conn.info.transaction_status == TransactionStatus.IDLE
            and (
                error.sqlstate in ("40001", "40P01")
                or (error.sqlstate or "").startswith("23")
                or (self.aborted and error.sqlstate == "25P02")
            )
        ):
            # These server ERRORs confirm rollback (including deferred
            # constraints). In particular, 40003 is NOT a confirmed rollback.
            return error
        unknown = CommitOutcomeUnknown(local_committed=self.local_committed)
        if self.deadline_cleanup_failed:
            unknown.add_note("commit_deadline_cleanup_failed")
        _discard(self.conn, unknown)
        return unknown

    def rollback_complete(self, error: BaseException) -> None:
        expected = TransactionStatus.IDLE if self.outer else TransactionStatus.INTRANS
        if isinstance(error, _INTERRUPTED) or self.deadline_cleanup_failed or (
            not self.conn.closed and self.conn.info.transaction_status != expected
        ):
            # psycopg may swallow rollback transport errors. Do not reuse a
            # connection whose cleanup did not restore the enclosing boundary.
            error.add_note("transaction_cleanup_failed")
            _discard(self.conn, error)

    def remove_handler(self, pending: BaseException | None) -> None:
        self.committing = False
        try:
            self.conn.remove_notice_handler(self.notice)
        except Exception:
            if pending is not None:
                pending.add_note("notice_handler_cleanup_failed")
                _discard(self.conn, pending)
            else:
                unknown = CommitOutcomeUnknown(local_committed=self.local_committed)
                unknown.add_note("notice_handler_cleanup_failed")
                _discard(self.conn, unknown)
                raise unknown from None


@contextmanager
def transaction(conn: psycopg.Connection[Any]) -> Iterator[psycopg.Transaction]:
    """Bound outer COMMIT acknowledgement to five seconds, not transaction runtime.

    Unknown outcomes must never be blindly retried.
    """
    guard = _CommitGuard(conn)
    conn.add_notice_handler(guard.notice)
    pending: BaseException | None = None
    try:
        manager = conn.transaction()
        try:
            tx = manager.__enter__()
        except BaseException as error:
            _discard(conn, error)
            raise
        try:
            yield tx
            if guard.needs_warning_visibility(tx):
                # Role/session defaults may suppress WARNING. Keep this local
                # and before the commit phase, so preparation failure rolls back.
                conn.execute(_ENABLE_COMMIT_WARNINGS)
            guard.prepare(tx)
        except BaseException as error:
            try:
                suppressed = manager.__exit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:
                error.add_note("transaction_cleanup_failed")
                _discard(conn, error)
                if isinstance(cleanup_error, _INTERRUPTED):
                    raise
                raise error from None
            guard.rollback_complete(error)
            if not suppressed:
                raise
        else:
            with guard.commit_phase():
                manager.__exit__(None, None, None)
    except BaseException as error:
        pending = error
        raise
    finally:
        guard.remove_handler(pending)


@asynccontextmanager
async def async_transaction(
    conn: psycopg.AsyncConnection[Any],
) -> AsyncIterator[psycopg.AsyncTransaction]:
    """Async commit guard; cancellation propagates even during ambiguous COMMIT.

    The watchdog doesn't use task cancellation. External cancellation may still
    await psycopg's separate cancel-channel cleanup (currently up to five seconds).
    """
    guard = _CommitGuard(conn)
    conn.add_notice_handler(guard.notice)
    pending: BaseException | None = None
    try:
        manager = conn.transaction()
        try:
            tx = await manager.__aenter__()
        except BaseException as error:
            _discard(conn, error)
            raise
        try:
            yield tx
            if guard.needs_warning_visibility(tx):
                await conn.execute(_ENABLE_COMMIT_WARNINGS)
            guard.prepare(tx)
        except BaseException as error:
            try:
                suppressed = await manager.__aexit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:
                error.add_note("transaction_cleanup_failed")
                _discard(conn, error)
                if isinstance(cleanup_error, _INTERRUPTED):
                    raise
                raise error from None
            guard.rollback_complete(error)
            if not suppressed:
                raise
        else:
            with guard.commit_phase(asyncio.current_task()):
                await manager.__aexit__(None, None, None)
    except BaseException as error:
        pending = error
        raise
    finally:
        guard.remove_handler(pending)
