"""One request's absolute, best-effort processing and response budget.

Connections must be exclusively owned, unpooled, and attached inside processing().
Disposal runs on the owning event loop, not a watchdog thread. Disconnecting
libpq neither proves rollback nor establishes whether remote work has stopped.
"""

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import psycopg

from pg_agmemory.transactions import CommitOutcomeUnknown

REQUEST_TIMEOUT_SECONDS = 30.0
ERROR_RESPONSE_RESERVE_SECONDS = 1.0

logger = logging.getLogger(__name__)


class RequestDeadlineExceeded(Exception):
    """A safe request-budget failure, distinct from unrelated timeouts."""

    code = "request_deadline_exceeded"

    def __init__(self, *, commit_outcome_unknown: bool = False) -> None:
        self.commit_outcome_unknown = commit_outcome_unknown
        super().__init__(self.code)


def _exception_chain(error: BaseException | None) -> Iterator[BaseException]:
    pending = [error] if error is not None else []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for previous in (current.__cause__, current.__context__):
            if previous is not None:
                pending.append(previous)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


def _commit_outcome_unknown(error: BaseException | None) -> bool:
    return any(
        isinstance(current, CommitOutcomeUnknown)
        or CommitOutcomeUnknown.code in getattr(current, "__notes__", ())
        or (
            isinstance(current, RequestDeadlineExceeded)
            and current.commit_outcome_unknown
        )
        for current in _exception_chain(error)
    )


class RequestDeadline:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self._loop = asyncio.get_running_loop()
        self._expires_at = self._loop.time() + REQUEST_TIMEOUT_SECONDS
        self._work_deadline = self._expires_at - ERROR_RESPONSE_RESERVE_SECONDS
        self.expired = False
        self._connection: psycopg.AsyncConnection[Any] | None = None
        self._disposed = False
        self._disposal_failed = False
        self._timer: asyncio.TimerHandle | None = None
        self._started = False

    @property
    def expires_at(self) -> float:
        """Absolute event-loop time including the final error-response reserve."""
        return self._expires_at

    def _check_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("request_deadline_wrong_event_loop")

    def _expire(self) -> None:
        self.expired = True
        if self._connection is None or self._disposed:
            return
        self._disposed = True
        try:
            self._connection.pgconn.finish()
        except Exception:
            self._disposal_failed = True
            logger.error(
                "request_deadline_connection_disposal_failed request_id=%s", self.request_id
            )

    def _failure(self, error: BaseException | None = None) -> RequestDeadlineExceeded:
        failure = RequestDeadlineExceeded(
            commit_outcome_unknown=_commit_outcome_unknown(error)
        )
        if self._disposal_failed:
            failure.add_note("connection_disposal_failed")
        return failure

    def check(self) -> None:
        """Reject late synchronous completions even before timers are dispatched."""
        self._check_loop()
        if self.expired or self._loop.time() >= self._work_deadline:
            self._expire()
            raise self._failure()

    @contextmanager
    def connection(self, conn: psycopg.AsyncConnection[Any]) -> Iterator[None]:
        """Attach before async connection entry; detach only after its close exits."""
        self._check_loop()
        if self._connection is not None:
            raise RuntimeError("request_deadline_connection_in_use")
        if getattr(conn, "_pool", None) is not None:
            raise RuntimeError("request_deadline_pooled_connection")
        self._connection = conn
        self._disposed = False
        try:
            self.check()
            yield
        finally:
            self._connection = None

    @asynccontextmanager
    async def processing(self) -> AsyncIterator[None]:
        """Bound all work to one budget, preserving external task cancellation."""
        self._check_loop()
        if self._started:
            raise RuntimeError("request_deadline_already_started")
        self._started = True
        task = asyncio.current_task()
        assert task is not None
        cancellations = task.cancelling()
        timeout = asyncio.timeout_at(self._work_deadline)
        # Equal-time asyncio timers have unspecified ordering. One representable
        # instant earlier guarantees disposal precedes timeout cancellation.
        self._timer = self._loop.call_at(
            math.nextafter(self._work_deadline, -math.inf), self._expire
        )
        original: BaseException | None = None
        try:
            async with timeout:
                try:
                    self.check()
                    yield
                    self.check()
                    if task.cancelling() > cancellations:
                        raise asyncio.CancelledError()
                except BaseException as error:
                    # Retain guard notes before asyncio replaces CancelledError.
                    original = error
                    raise
        except BaseException as error:
            if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if task.cancelling() > cancellations:
                # Cleanup may replace cancellation with a driver/close failure.
                cancelled = next(
                    (
                        previous for previous in _exception_chain(original)
                        if isinstance(previous, asyncio.CancelledError)
                    ),
                    asyncio.CancelledError(),
                )
                if (
                    _commit_outcome_unknown(original)
                    and CommitOutcomeUnknown.code not in getattr(cancelled, "__notes__", ())
                ):
                    cancelled.add_note(CommitOutcomeUnknown.code)
                raise cancelled from None
            if self.expired or self._loop.time() >= self._work_deadline:
                self._expire()
                raise self._failure(original or error) from None
            raise
        finally:
            self._timer.cancel()
            self._timer = None
