"""Interrupt only the owned COMMIT socket, independently of PostgreSQL cancellation."""

import os
import socket
import threading
from time import monotonic

COMMIT_ACK_TIMEOUT_SECONDS = 5.0


class CommitDeadline:
    """One-shot watchdog; the caller must finish it before reusing the connection.

    The duplicate pins the socket even if libpq closes its descriptor. The thread
    only calls shutdown(), never libpq or connection methods. Shutdown interrupts
    client I/O; it does not establish whether server work stopped or committed.
    """

    def __init__(self, socket_fd: int, seconds: float = COMMIT_ACK_TIMEOUT_SECONDS) -> None:
        self._seconds = seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="pg-agmemory-commit-deadline")
        self._expires_at: float | None = None
        self._disarmed = False
        self._expired = False
        duplicate = os.dup(socket_fd)
        try:
            self._socket = socket.socket(fileno=duplicate)
        except BaseException:
            os.close(duplicate)
            raise

    def start(self) -> None:
        self._expires_at = monotonic() + self._seconds
        self._thread.start()

    def _expire_locked(self) -> None:
        self._expired = True
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Already-disconnected sockets still have an unknown outcome.
            pass

    def _watch(self) -> None:
        assert self._expires_at is not None
        try:
            while not self._stop.wait(max(0.0, self._expires_at - monotonic())):
                with self._lock:
                    if self._disarmed:
                        return
                    if monotonic() >= self._expires_at:
                        self._expire_locked()
                        return
        except Exception:
            # A broken monitor must interrupt the wait, not silently remove its bound.
            with self._lock:
                if not self._disarmed:
                    self._expire_locked()

    def finish(self) -> bool:
        """Disarm, join and release the duplicate; return whether the deadline lost."""
        try:
            with self._lock:
                if not self._disarmed:
                    # Reject a late reply even if the watchdog hasn't been scheduled.
                    if self._expires_at is not None and monotonic() >= self._expires_at:
                        self._expire_locked()
                    self._disarmed = True
                    self._stop.set()
            if self._thread.ident is not None:
                self._thread.join()
        finally:
            self._socket.close()
        return self._expired
