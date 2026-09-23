"""Real socket ownership and completion races, without PostgreSQL or libpq calls."""

import os
import socket
import threading

import pytest

import pg_agmemory.commit_deadline as deadlines
from pg_agmemory.commit_deadline import COMMIT_ACK_TIMEOUT_SECONDS, CommitDeadline


def assert_finished(deadline):
    assert not deadline._thread.is_alive()
    assert deadline._socket.fileno() == -1


def assert_connected(left, right):
    left.sendall(b"still connected")
    assert right.recv(64) == b"still connected"


def test_default_commit_acknowledgement_deadline_is_fixed():
    assert COMMIT_ACK_TIMEOUT_SECONDS == 5.0
    left, right = socket.socketpair()
    with left, right:
        deadline = CommitDeadline(left.fileno())
        try:
            assert deadline._seconds == 5.0
        finally:
            assert not deadline.finish()
        assert_finished(deadline)


def test_completion_disarms_and_joins_before_reuse():
    left, right = socket.socketpair()
    with left, right:
        right.settimeout(1)
        deadline = CommitDeadline(left.fileno(), seconds=0.1)
        deadline.start()
        assert not deadline.finish()
        assert_finished(deadline)
        threading.Event().wait(0.15)
        assert_connected(left, right)
        assert not deadline.finish()


def test_expiry_interrupts_blocked_io_and_closes_only_owned_duplicate():
    left, right = socket.socketpair()
    with left, right:
        left.settimeout(1)
        right.settimeout(1)
        deadline = CommitDeadline(left.fileno(), seconds=0.02)
        try:
            deadline.start()
            assert left.recv(1) == b""
            assert right.recv(1) == b""
            assert deadline.finish()
        finally:
            deadline.finish()
        assert_finished(deadline)
        assert left.fileno() >= 0


def test_expiry_is_isolated_from_other_connections():
    left, right = socket.socketpair()
    other_left, other_right = socket.socketpair()
    with left, right, other_left, other_right:
        right.settimeout(1)
        other_right.settimeout(1)
        deadline = CommitDeadline(left.fileno(), seconds=0.02)
        try:
            deadline.start()
            assert right.recv(1) == b""
            assert_connected(other_left, other_right)
        finally:
            deadline.finish()
        assert_finished(deadline)


def test_duplicate_cannot_shutdown_reused_original_descriptor():
    left, right = socket.socketpair()
    other_left, other_right = socket.socketpair()
    with left, right, other_left, other_right:
        right.settimeout(1)
        other_right.settimeout(1)
        deadline = CommitDeadline(left.fileno(), seconds=0.02)
        original_fd = left.fileno()
        left.close()
        os.dup2(other_left.fileno(), original_fd)
        with socket.socket(fileno=original_fd) as reused:
            try:
                deadline.start()
                assert right.recv(1) == b""
                assert_connected(reused, other_right)
                assert_connected(other_left, other_right)
            finally:
                deadline.finish()
        assert_finished(deadline)


def test_late_completion_fails_even_when_watchdog_was_not_scheduled(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(deadlines, "monotonic", lambda: now[0])
    left, right = socket.socketpair()
    with left, right:
        right.settimeout(1)
        deadline = CommitDeadline(left.fileno(), seconds=5)
        monkeypatch.setattr(deadline._thread, "start", lambda: None)
        try:
            deadline.start()
            now[0] = 5.0
            assert deadline.finish()
            assert right.recv(1) == b""
        finally:
            deadline.finish()
        assert_finished(deadline)


@pytest.mark.parametrize("expired", [False, True])
def test_completion_and_watchdog_expiry_are_serialized(monkeypatch, expired):
    now = [0.0]
    monkeypatch.setattr(deadlines, "monotonic", lambda: now[0])
    left, right = socket.socketpair()
    with left, right:
        right.settimeout(1)
        deadline = CommitDeadline(left.fileno(), seconds=5)
        ready = threading.Event()
        release = threading.Event()
        wait = deadline._stop.wait

        def controlled_wait(timeout):
            if not ready.is_set():
                ready.set()
                release.wait(timeout=1)
                return False
            return wait(timeout)

        monkeypatch.setattr(deadline._stop, "wait", controlled_wait)
        deadline.start()
        assert ready.wait(timeout=1)
        # Hold the shared lock so both paths observe the same boundary.
        with deadline._lock:
            now[0] = 5.0 if expired else 4.0
            results = []
            completion = threading.Thread(target=lambda: results.append(deadline.finish()))
            completion.start()
            release.set()
        completion.join(timeout=1)
        try:
            assert not completion.is_alive()
            assert results == [expired]
            assert_finished(deadline)
            if expired:
                assert right.recv(1) == b""
            else:
                now[0] = 6.0
                assert not deadline.finish()
                assert_connected(left, right)
        finally:
            deadline.finish()


def test_duplicate_creation_failure_does_not_start_a_thread(monkeypatch):
    def fail_duplicate(fd):
        raise OSError("private diagnostic")

    monkeypatch.setattr(deadlines.os, "dup", fail_duplicate)
    before = set(threading.enumerate())
    with pytest.raises(OSError):
        CommitDeadline(-1)
    assert set(threading.enumerate()) == before


def test_socket_wrapper_failure_closes_duplicate(monkeypatch):
    left, right = socket.socketpair()
    duplicate = None
    real_dup = os.dup

    def capture_duplicate(fd):
        nonlocal duplicate
        duplicate = real_dup(fd)
        return duplicate

    def fail_socket(**kwargs):
        raise OSError("private diagnostic")

    with left, right:
        monkeypatch.setattr(deadlines.os, "dup", capture_duplicate)
        monkeypatch.setattr(deadlines.socket, "socket", fail_socket)
        with pytest.raises(OSError):
            CommitDeadline(left.fileno())
        with pytest.raises(OSError):
            os.fstat(duplicate)
        assert_connected(left, right)


def test_start_failure_can_be_disarmed_without_leaking_descriptor(monkeypatch):
    left, right = socket.socketpair()
    with left, right:
        deadline = CommitDeadline(left.fileno())

        def fail_start():
            raise RuntimeError("private diagnostic")

        monkeypatch.setattr(deadline._thread, "start", fail_start)
        with pytest.raises(RuntimeError):
            deadline.start()
        assert not deadline.finish()
        assert_finished(deadline)
        assert_connected(left, right)


def test_cleanup_error_still_closes_duplicate_and_stops_thread(monkeypatch):
    left, right = socket.socketpair()
    with left, right:
        deadline = CommitDeadline(left.fileno())
        deadline.start()
        join = deadline._thread.join

        def fail_join():
            join()
            raise RuntimeError("private diagnostic")

        monkeypatch.setattr(deadline._thread, "join", fail_join)
        with pytest.raises(RuntimeError):
            deadline.finish()
        assert_finished(deadline)
        assert_connected(left, right)
