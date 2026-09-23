import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from pg_agmemory import cli, worker
from pg_agmemory.admin import admin_failure
from pg_agmemory.native_client import AdapterFailure, NativeHTTPClient
from pg_agmemory.transactions import CommitDeadlineSetupError, CommitOutcomeUnknown


@pytest.mark.parametrize("mutation", [False, True])
def test_native_unconfirmed_commit_is_not_retryable(mutation):
    calls = []

    def respond(request):
        calls.append(request)
        payload = {
            "code": "commit_outcome_unknown", "request_id": str(uuid4()), "retryable": False,
        }
        return httpx.Response(
            503, headers={"content-type": "application/json"},
            stream=httpx.ByteStream(json.dumps(payload).encode()),
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="https://memory.test",
        ) as client:
            with pytest.raises(AdapterFailure) as failure:
                await NativeHTTPClient(client).exchange(
                    "/v1/observe", body=b"{}", key="caller-owned-key", mutation=mutation,
                )
        assert failure.value.error.code == "commit_outcome_unknown"
        assert failure.value.error.outcome_unknown is mutation
        assert not failure.value.error.retryable
        assert len(calls) == 1
        assert calls[0].headers["idempotency-key"] == "caller-owned-key"

    asyncio.run(scenario())


def test_admin_watchdog_setup_failure_is_not_an_attempted_commit():
    failure = admin_failure(CommitDeadlineSetupError(), commit_attempted=True)
    assert failure.code == "admin_database_unavailable"
    assert not failure.outcome_unknown


@pytest.mark.parametrize("phase", ["claim", "publish", "fail", "prepare", "model_publish"])
def test_worker_commit_uncertainty_never_compensates_or_retries(monkeypatch, phase):
    calls = []
    model = phase in ("prepare", "model_publish")
    job_id, token = uuid4(), uuid4()

    class FakeJobs:
        memory = object()

        async def claim(self, *args, **kwargs):
            calls.append("claim")
            return {
                "job_id": job_id, "lease_token": token, "state": "running",
                "kind": "extract" if model else "structured_remember", "payload": {},
            }

        async def publish(self, *args):
            calls.append("publish")
            if phase == "fail":
                raise worker.TokenizerUnavailable()
            return {"memory_id": str(uuid4()), "revision": 1}

        async def fail(self, *args, **kwargs):
            calls.append("fail")
            return "pending"

    class FakeProcessing:
        def __init__(self, memory):
            pass

        async def prepare(self, *args, **kwargs):
            calls.append("prepare")
            return {"text": "synthetic"}

        async def publish(self, *args):
            calls.append("model_publish")
            return {"assertions": []}

    @asynccontextmanager
    async def boundary(*args):
        yield FakeJobs()
        if calls[-1] == phase:
            raise CommitOutcomeUnknown()

    async def generate(*args):
        calls.append("provider")
        return {}

    monkeypatch.setattr(worker, "job_transaction", boundary)
    monkeypatch.setattr(worker.Remember, "model_validate", lambda value: object())
    monkeypatch.setattr("pg_agmemory.processing.Processing", FakeProcessing)
    monkeypatch.setattr(worker, "call_model", generate)
    profile = SimpleNamespace(digest="synthetic") if model else None
    with pytest.raises(CommitOutcomeUnknown):
        asyncio.run(worker.run_once("unused", "synthetic", profile=profile))
    expected = {
        "claim": ["claim"],
        "publish": ["claim", "publish"],
        "fail": ["claim", "publish", "fail"],
        "prepare": ["claim", "prepare"],
        "model_publish": ["claim", "prepare", "provider", "model_publish"],
    }
    assert calls == expected[phase]


@pytest.mark.parametrize("once", [False, True])
def test_worker_loop_stops_on_uncertain_commit(monkeypatch, caplog, once):
    calls = []

    async def validate(url):
        calls.append("validate")

    async def attempt(*args, **kwargs):
        calls.append("attempt")
        raise CommitOutcomeUnknown()

    monkeypatch.setattr(worker, "validate_runtime", validate)
    monkeypatch.setattr(worker, "run_once", attempt)
    with pytest.raises(CommitOutcomeUnknown):
        asyncio.run(worker.run("unused", "synthetic", once=once))
    assert calls == ["validate", "attempt"]
    assert "worker_commit_outcome_unknown reconciliation_required=true" in caplog.text


def test_uncertain_heartbeat_stops_provider_without_failure_write(monkeypatch):
    calls = []

    class FakeProcessing:
        def __init__(self, memory):
            pass

        async def prepare(self, *args, **kwargs):
            calls.append("prepare")
            return {"text": "synthetic"}

    class FakeJobs:
        memory = object()

        async def heartbeat(self, *args):
            calls.append("heartbeat")

    @asynccontextmanager
    async def boundary(*args):
        yield FakeJobs()
        if calls[-1] == "heartbeat":
            raise CommitOutcomeUnknown()

    async def provider(*args):
        calls.append("provider")
        try:
            await asyncio.Future()
        finally:
            calls.append("provider_cancelled")

    monkeypatch.setattr(worker, "job_transaction", boundary)
    monkeypatch.setattr("pg_agmemory.processing.Processing", FakeProcessing)
    monkeypatch.setattr(worker, "MODEL_HEARTBEAT_SECONDS", 0)
    claim = {"job_id": uuid4(), "lease_token": uuid4(), "kind": "extract"}
    with pytest.raises(CommitOutcomeUnknown):
        asyncio.run(worker.process_model(
            "unused", "synthetic", claim, SimpleNamespace(call=provider),
        ))
    assert calls == ["prepare", "provider", "heartbeat", "provider_cancelled"]


@pytest.mark.parametrize("command", ["migrate", "reindex-lexical", "provision", "worker"])
def test_cli_uncertain_commit_has_only_redacted_failure(monkeypatch, capsys, command):
    def unconfirmed(*args, **kwargs):
        raise CommitOutcomeUnknown()

    @contextmanager
    def connection(*args, **kwargs):
        yield object()

    @contextmanager
    def boundary(conn):
        unconfirmed()
        yield

    async def run(*args, **kwargs):
        unconfirmed()

    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "DO_NOT_ECHO")
    monkeypatch.setenv("PGAG_DATABASE_URL", "DO_NOT_ECHO")
    monkeypatch.setattr(cli, "migrate", unconfirmed)
    monkeypatch.setattr(cli, "reindex_lexical", unconfirmed)
    monkeypatch.setattr(cli.psycopg, "connect", connection)
    monkeypatch.setattr(cli, "transaction", boundary)
    monkeypatch.setattr(cli, "run", run)
    args = ["pg-agmemory", command]
    if command in ("provision", "worker"):
        args += ["--subject", "synthetic"]
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "error": {"code": "commit_outcome_unknown", "outcome_unknown": True},
    }
    assert "DO_NOT_ECHO" not in captured.out + captured.err
