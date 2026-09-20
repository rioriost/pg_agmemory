import asyncio
import json
from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from pg_agmemory.api import TransactionBoundary, create_app


def test_opt_in_timing_preserves_response_and_records_committed_phases(env):
    events = []
    body = {
        "scope_id": str(env.scopes[0]),
        "source_namespace": "timing",
        "source_event_id": "one",
        "occurred_at": "2026-09-01T00:00:00Z",
        "content": "PRIVATE_PAYLOAD",
        "consent_reference": "PRIVATE_CONSENT",
    }
    headers = env.headers()
    original = env.client.post("/v1/observe", json=body, headers=headers)
    with TestClient(create_app(env.settings, timing_sink=events.append)) as client:
        measured = client.post("/v1/observe", json=body, headers=headers)
    assert original.status_code == measured.status_code == 201
    assert original.json() == measured.json()
    assert len(events) == 1
    event = events[0]
    assert event.request_id == measured.headers["x-request-id"]
    assert event.route == "/v1/observe" and event.method == "POST"
    assert event.status == 201 and event.response_bytes == len(measured.content)
    assert 0 <= event.handler_ms <= event.transaction_ms <= event.server_ms
    assert 0 <= event.commit_ms <= event.transaction_ms
    assert 0 <= event.connection_barrier_ms <= event.transaction_ms
    assert "server-timing" not in measured.headers
    rendered = json.dumps(asdict(event))
    for private in ("PRIVATE_PAYLOAD", "PRIVATE_CONSENT", str(env.scopes[0]), env.subjects[0]):
        assert private not in rendered


def test_path_ids_query_headers_and_auth_failures_are_not_metric_labels(env):
    events = []
    memory_id = str(uuid4())
    with TestClient(create_app(env.settings, timing_sink=events.append)) as client:
        denied = client.get("/v1/jobs/" + memory_id + "?private=SECRET", headers=env.headers())
        unauthorized = client.get(
            "/v1/PRIVATE_PATH",
            headers={"Authorization": "Bearer PRIVATE_TOKEN"},
        )
        client.get("/healthz")
    assert denied.status_code == 404 and unauthorized.status_code == 401
    assert len(events) == 2
    assert events[0].route == "/v1/jobs/{job_id}"
    assert events[0].transaction_ms is None and events[0].commit_ms is None
    assert events[1].route == "unmatched" and events[1].connection_barrier_ms is None
    rendered = json.dumps([asdict(row) for row in events])
    for private in (memory_id, "PRIVATE_PATH", "PRIVATE_TOKEN", "SECRET"):
        assert private not in rendered


def test_disconnect_is_not_a_success_or_a_zero_duration_transaction(env):
    events = []

    async def unreachable(scope, receive, send):
        pytest.fail("disconnected request reached the handler")

    boundary = TransactionBoundary(unreachable, env.settings, timing_sink=events.append)
    headers = [(key.lower().encode(), value.encode()) for key, value in env.headers().items()]

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        pytest.fail("disconnected request received a response")

    asyncio.run(
        boundary(
            {"type": "http", "path": "/v1/observe", "method": "POST", "headers": headers},
            receive,
            send,
        )
    )
    assert len(events) == 1 and events[0].status is None
    assert events[0].transaction_ms is None and events[0].response_bytes == 0


def test_sink_failure_is_explicit_and_does_not_rollback_a_delivered_commit(env):
    def broken(event):
        raise RuntimeError("timing sink failed")

    body = {
        "scope_id": str(env.scopes[0]),
        "source_namespace": "timing",
        "source_event_id": "sink-failure",
        "occurred_at": "2026-09-01T00:00:00Z",
        "content": "committed",
        "consent_reference": "test",
    }
    headers = env.headers()
    with TestClient(create_app(env.settings, timing_sink=broken)) as client:
        with pytest.raises(RuntimeError, match="timing sink failed"):
            client.post("/v1/observe", json=body, headers=headers)
    replay = env.client.post("/v1/observe", json=body, headers=headers)
    assert replay.status_code == 201
    assert len(env.recall().json()["items"]) == 1
