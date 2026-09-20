"""Opt-in request timing, with route templates rather than user-controlled labels."""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter_ns

from starlette.routing import Route
from starlette.types import Message, Scope


@dataclass(frozen=True)
class RequestTiming:
    request_id: str
    method: str
    route: str
    status: int | None
    response_bytes: int
    server_ms: float
    connection_barrier_ms: float | None
    handler_ms: float | None
    commit_ms: float | None
    transaction_ms: float | None


TimingSink = Callable[[RequestTiming], None]


class RequestClock:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.started = perf_counter_ns()
        self.connection_started: int | None = None
        self.connection_acquired: int | None = None
        self.handler_started: int | None = None
        self.handler_finished: int | None = None
        self.committed: int | None = None
        self.status: int | None = None
        self.response_bytes = 0

    def sent(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            self.response_bytes += len(message.get("body", b""))

    def finish(self, scope: Scope) -> RequestTiming:
        def milliseconds(start: int | None, end: int | None) -> float | None:
            return None if start is None or end is None else (end - start) / 1_000_000

        route = scope.get("route")
        return RequestTiming(
            request_id=self.request_id,
            method=scope["method"]
            if scope["method"] in ("GET", "POST", "PUT", "DELETE")
            else "OTHER",
            route=route.path if isinstance(route, Route) else "unmatched",
            status=self.status,
            response_bytes=self.response_bytes,
            server_ms=(perf_counter_ns() - self.started) / 1_000_000,
            connection_barrier_ms=milliseconds(self.connection_started, self.connection_acquired),
            handler_ms=milliseconds(self.handler_started, self.handler_finished),
            commit_ms=milliseconds(self.handler_finished, self.committed),
            transaction_ms=milliseconds(self.connection_started, self.committed),
        )
