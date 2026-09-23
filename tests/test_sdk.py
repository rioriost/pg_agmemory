import asyncio
import inspect
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from pg_agmemory import __version__
from pg_agmemory.api import create_app
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.jobs import job_transaction
from pg_agmemory.models import (
    AssertionExplanation,
    AssertionHistory,
    AssertionHistoryPage,
    CancelJob,
    Capture,
    CaptureBatch,
    CaptureBatchResult,
    CapturedMemory,
    CheckpointBranch,
    CheckpointEnvelope,
    CheckpointState,
    CreateCheckpoint,
    CreateEntity,
    CreateRelation,
    DeletionPreview,
    DeletionResult,
    EmbeddingModel,
    EnqueueJob,
    EntityPage,
    EpisodeExplanation,
    EpisodePage,
    Evidence,
    ExpandGraph,
    Explain,
    Forget,
    JobPage,
    Observe,
    ObserveResult,
    PlanToolEffect,
    PutEmbedding,
    QueryEntities,
    QueryEpisodes,
    QueryJobs,
    Recall,
    Remember,
    RestoreCheckpoint,
    ReviseAssertion,
    ReviseRelation,
    TransitionToolEffect,
    VectorQuery,
)
from pg_agmemory.native_client import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, NativeSettings
from pg_agmemory.sdk import SDK_NATIVE_CODES, AsyncMemoryClient, MemoryClientError
from pg_agmemory.worker import run_once


def observation(scope=None, **changes):
    return Observe(
        scope_id=scope or uuid4(),
        source_namespace="sdk-test",
        source_event_id=str(uuid4()),
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        content="東京都 ACME Gold Silver",
        consent_reference="synthetic-test",
        **changes,
    )


def memory(scope, source):
    return Remember(
        scope_id=scope,
        subject="ACME",
        predicate="tier",
        value="Gold",
        evidence=[Evidence(memory_id=source, quote="Gold")],
        explicit_intent=True,
    )


def batch_request(scope=None):
    return CaptureBatch(
        episode=observation(scope),
        memories=[
            CapturedMemory(
                subject=f"ACME-{i}",
                predicate="tier",
                value="Gold",
                evidence_quote="Gold",
                explicit_intent=True,
            )
            for i in range(2)
        ],
    )


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


def response(status, body):
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        stream=Chunks([json.dumps(body).encode()]),
    )


def mock_client(monkeypatch, handler, **capabilities):
    clients, calls = [], []

    async def upstream(request):
        calls.append(request)
        if request.url.path == "/v1/capabilities":
            return response(
                200,
                {
                    "service_version": __version__,
                    "api_version": "v1",
                    "schema_version": SCHEMA_VERSION,
                    **capabilities,
                },
            )
        result = handler(request)
        return await result if inspect.isawaitable(result) else result

    def client(settings):
        http = httpx.AsyncClient(
            base_url=settings.api_url,
            transport=httpx.MockTransport(upstream),
            headers={"Authorization": f"Bearer {settings.api_token}"},
            follow_redirects=False,
            trust_env=False,
        )
        clients.append(http)
        return http

    monkeypatch.setattr(NativeSettings, "client", client)
    return clients, calls


def test_context_lifecycle_and_typed_roundtrip(monkeypatch):
    body, memory_id = observation(), uuid4()

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/observe"
        assert json.loads(request.content) == body.model_dump(mode="json")
        assert request.headers["idempotency-key"] == "persisted-key"
        assert request.headers["authorization"] == "Bearer fixed.identity.signature"
        return response(201, {"memory_id": str(memory_id), "revision": 1})

    clients, calls = mock_client(monkeypatch, handler)

    async def scenario():
        client = AsyncMemoryClient("https://memory.test", "fixed.identity.signature")
        assert "fixed.identity.signature" not in repr(client)
        with pytest.raises(MemoryClientError, match="client_not_open"):
            await client.observe(body, idempotency_key="persisted-key")
        async with client as opened:
            assert opened is client and len(calls) == 1
            with pytest.raises(MemoryClientError, match="client_already_used"):
                async with client:
                    pytest.fail("Nested client opened")
            result = await client.observe(body, idempotency_key="persisted-key")
            assert isinstance(result, ObserveResult) and result.memory_id == memory_id
        assert clients[0].is_closed
        with pytest.raises(MemoryClientError, match="client_not_open"):
            await client.observe(body, idempotency_key="persisted-key")
        with pytest.raises(MemoryClientError, match="client_already_used"):
            async with client:
                pytest.fail("Used client reopened")
        assert len(calls) == 2

    asyncio.run(scenario())


def test_entry_cancellation_closes_owned_transport(monkeypatch):
    started = asyncio.Event()
    clients = []

    async def upstream(request):
        started.set()
        await asyncio.Future()

    def client(settings):
        http = httpx.AsyncClient(base_url=settings.api_url, transport=httpx.MockTransport(upstream))
        clients.append(http)
        return http

    monkeypatch.setattr(NativeSettings, "client", client)

    async def scenario():
        sdk = AsyncMemoryClient("https://memory.test", "fixed.identity.signature")
        task = asyncio.create_task(sdk.__aenter__())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert clients[0].is_closed
        with pytest.raises(MemoryClientError, match="client_not_open"):
            await sdk.get_job(uuid4())
        with pytest.raises(MemoryClientError, match="client_already_used"):
            await sdk.__aenter__()

    asyncio.run(scenario())


def test_maximum_key_and_body_exception_close(monkeypatch):
    _, calls = mock_client(
        monkeypatch, lambda _: response(201, {"memory_id": str(uuid4()), "revision": 1})
    )

    async def scenario():
        sdk = AsyncMemoryClient("https://memory.test", "fixed.identity.signature")
        with pytest.raises(RuntimeError, match="caller failure"):
            async with sdk:
                await sdk.observe(observation(), idempotency_key="k" * 256)
                raise RuntimeError("caller failure")
        assert calls[-1].headers["idempotency-key"] == "k" * 256
        with pytest.raises(MemoryClientError, match="client_not_open"):
            await sdk.get_job(uuid4())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "method", ["get", "forget_preview", "forget_purge", "cancel_status", "cancel_shape"]
)
def test_response_status_and_mode_are_not_silently_coerced(monkeypatch, method):
    def handler(request):
        if method == "get":
            assert request.method == "GET" and not request.content
            assert "idempotency-key" not in request.headers
            return response(201, {})
        if method == "cancel_status":
            return response(201, {"job_id": str(uuid4())})
        if method == "cancel_shape":
            return response(200, {})
        if method == "forget_preview":
            return response(
                202,
                {
                    "deletion_id": str(uuid4()),
                    "state": "active_store_purged",
                    "object_count": 1,
                    "deletion_epoch": 1,
                    "scope_ids": [],
                    "backup_status": "operator_managed",
                },
            )
        return response(202, {"mode": "preview", "object_count": 1, "changed": False})

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            with pytest.raises(MemoryClientError, match="invalid_native_response") as failure:
                if method == "get":
                    await sdk.get_job(uuid4())
                elif method.startswith("cancel_"):
                    await sdk.cancel_job(
                        uuid4(),
                        CancelJob(expected_state="pending", expected_attempt=0),
                        idempotency_key="key",
                    )
                else:
                    await sdk.forget(
                        Forget(
                            memory_ids=[uuid4()],
                            mode=method.removeprefix("forget_"),
                            reason="synthetic",
                        ),
                        idempotency_key="key",
                    )
            assert failure.value.error.outcome_unknown == (method != "get")
        assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "capabilities",
    [
        {"service_version": "0.0.11"},
        {"schema_version": SCHEMA_VERSION + 1},
        {"api_version": "v2"},
    ],
)
def test_incompatible_server_closes_before_any_operation(monkeypatch, capabilities):
    clients, calls = mock_client(
        monkeypatch, lambda _: pytest.fail("Operation sent"), **capabilities
    )

    async def scenario():
        with pytest.raises(MemoryClientError, match="native_version_mismatch") as failure:
            async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature"):
                pytest.fail("Invalid startup succeeded")
        assert not failure.value.error.outcome_unknown
        assert clients[0].is_closed and len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["", " ", " lead", "tail ", "\r\nsecret", "あ", "x" * 257, None, 1])
@pytest.mark.parametrize("method", ["observe", "cancel", "batch"])
def test_bad_keys_are_rejected_before_network(monkeypatch, key, method):
    _, calls = mock_client(monkeypatch, lambda _: pytest.fail("Invalid key reached API"))

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as client:
            with pytest.raises(MemoryClientError, match="invalid_request") as failure:
                if method == "observe":
                    await client.observe(observation(), idempotency_key=key)
                elif method == "batch":
                    await client.capture_batch(batch_request(), idempotency_key=key)
                else:
                    await client.cancel_job(
                        uuid4(),
                        CancelJob(expected_state="pending", expected_attempt=0),
                        idempotency_key=key,
                    )
            assert not failure.value.error.outcome_unknown
        assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["wrong_type", "mutated", "constructed", "path", "unicode"])
def test_invalid_models_and_path_ids_are_sanitized(monkeypatch, case):
    _, calls = mock_client(monkeypatch, lambda _: pytest.fail("Invalid request reached API"))

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as client:
            body = observation()
            if case == "wrong_type":
                body = {"secret": "DO_NOT_ECHO"}
            elif case == "mutated":
                body.source_namespace = ""
            elif case == "constructed":
                body = Observe.model_construct(content="DO_NOT_ECHO")
            elif case == "unicode":
                body.content = "\ud800"
            with pytest.raises(MemoryClientError, match="invalid_request") as failure:
                if case == "path":
                    await client.get_job("../DO_NOT_ECHO?identity=evil")
                else:
                    await client.observe(body, idempotency_key="same-key")
            assert not failure.value.error.outcome_unknown
            assert "DO_NOT_ECHO" not in str(failure.value)
        assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "disconnect",
        "timeout",
        "invalid_json",
        "html",
        "oversized",
        "redirect",
        "bad_shape",
        "wrong_status",
        "compressed",
        "server_error",
    ],
)
def test_uncertain_writes_do_not_retry_or_echo(monkeypatch, fault):
    def handler(request):
        if fault == "disconnect":
            raise httpx.ReadError("DO_NOT_ECHO")
        if fault == "timeout":
            raise httpx.ReadTimeout("DO_NOT_ECHO")
        if fault == "invalid_json":
            return httpx.Response(
                201, headers={"content-type": "application/json"}, stream=Chunks([b"DO_NOT_ECHO"])
            )
        if fault == "html":
            return httpx.Response(
                502, headers={"content-type": "text/html"}, stream=Chunks([b"DO_NOT_ECHO"])
            )
        if fault == "oversized":
            return httpx.Response(
                201,
                headers={"content-type": "application/json"},
                stream=Chunks([b"x" * MAX_RESPONSE_BYTES, b"x"]),
            )
        if fault == "redirect":
            return httpx.Response(307, headers={"location": "https://evil.test"})
        if fault == "compressed":
            return httpx.Response(
                201, headers={"content-type": "application/json", "content-encoding": "gzip"}
            )
        if fault == "server_error":
            return response(
                503,
                {
                    "code": "database_error",
                    "request_id": str(uuid4()),
                    "retryable": True,
                    "details": {"secret": "DO_NOT_ECHO"},
                },
            )
        return response(200 if fault == "wrong_status" else 201, {"secret": "DO_NOT_ECHO"})

    clients, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as client:
            with pytest.raises(MemoryClientError) as failure:
                await client.observe(observation(), idempotency_key="preserved-key")
            assert failure.value.error.outcome_unknown
            assert "DO_NOT_ECHO" not in failure.value.error.model_dump_json()
        assert clients[0].is_closed and len(calls) == 2
        assert calls[-1].headers["idempotency-key"] == "preserved-key"

    asyncio.run(scenario())


@pytest.mark.parametrize("code", sorted(SDK_NATIVE_CODES | {"DO_NOT_ECHO"}))
def test_native_error_catalog_preserves_only_safe_codes(monkeypatch, code):
    identity = uuid4()
    _, calls = mock_client(
        monkeypatch,
        lambda _: response(
            409,
            {
                "code": code,
                "request_id": str(identity),
                "retryable": True,
                "details": {"secret": "DO_NOT_ECHO"},
            },
        ),
    )

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as client:
            with pytest.raises(MemoryClientError) as failure:
                await client.observe(observation(), idempotency_key="key")
            error = failure.value.error
            assert error.code == (code if code in SDK_NATIVE_CODES else "native_api_error")
            assert error.request_id == identity and error.native_status == 409
            assert not error.outcome_unknown and not error.retryable
            assert "DO_NOT_ECHO" not in error.model_dump_json()
        assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("code", "status", "retryable", "unknown"),
    [
        ("capture_policy_denied", 403, False, False),
        ("capture_policy_invalid", 503, True, True),
        ("commit_outcome_unknown", 503, False, True),
    ],
)
def test_native_errors_preserve_retry_and_uncertainty(
    monkeypatch, code, status, retryable, unknown
):
    _, calls = mock_client(
        monkeypatch,
        lambda _: response(
            status,
            {"code": code, "request_id": str(uuid4()), "retryable": retryable},
        ),
    )

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as client:
            with pytest.raises(MemoryClientError) as failure:
                await client.observe(observation(), idempotency_key="policy")
            error = failure.value.error
            assert error.code == code and error.native_status == status
            assert error.retryable is retryable and error.outcome_unknown is unknown
        assert len(calls) == 2

    asyncio.run(scenario())


def test_read_failure_and_cancellation_close_without_claiming_mutation(monkeypatch):
    started = asyncio.Event()

    async def handler(request):
        if request.method == "GET":
            raise httpx.ReadError("DO_NOT_ECHO")
        started.set()
        await asyncio.Future()

    clients, calls = mock_client(monkeypatch, handler)

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            async with AsyncMemoryClient(
                "https://memory.test", "fixed.identity.signature"
            ) as client:
                with pytest.raises(MemoryClientError) as failure:
                    await client.get_entity(uuid4())
                assert not failure.value.error.outcome_unknown
                task = asyncio.create_task(client.observe(observation(), idempotency_key="key"))
                await started.wait()
                task.cancel()
                await task
        assert clients[0].is_closed and len(calls) == 3

    asyncio.run(scenario())


def test_checkpoint_has_its_native_one_mebibyte_body_limit(monkeypatch):
    def handler(request):
        assert MAX_REQUEST_BYTES < len(request.content) <= 1048576
        assert request.url.path == "/v1/checkpoints"
        return response(
            201,
            {
                "checkpoint_id": str(uuid4()),
                "run_id": str(uuid4()),
                "branch_id": str(uuid4()),
                "sequence": 1,
                "parent_checkpoint": None,
                "checksum": "0" * 64,
            },
        )

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as client:
            body = CreateCheckpoint(
                scope_id=uuid4(),
                run_id=uuid4(),
                branch_id=uuid4(),
                expected_head=None,
                harness_id="sdk",
                harness_version="1",
                event_watermark=0,
                state=CheckpointState(goal="test", completed_actions=["東" * 4096] * 25),
            )
            await client.create_checkpoint(body, idempotency_key="checkpoint-key")
            body.state.completed_actions *= 4
            with pytest.raises(MemoryClientError, match="body_too_large") as failure:
                await client.create_checkpoint(body, idempotency_key="oversized-key")
            assert not failure.value.error.outcome_unknown
            ordinary = memory(uuid4(), uuid4())
            ordinary.value = "東" * 65536
            ordinary.evidence = [Evidence(memory_id=uuid4(), quote="東" * 4096) for _ in range(32)]
            with pytest.raises(MemoryClientError, match="body_too_large"):
                await client.remember(ordinary, idempotency_key="ordinary-key")
        assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.integration
def test_sdk_route_surface_covers_native_resources(env):
    paths = create_app(env.settings).openapi()["paths"]
    expected = {
        "/v1/observe",
        "/v1/episodes/query",
        "/v1/captures",
        "/v1/captures/batch",
        "/v1/remember",
        "/v1/assertions/{memory_id}/revisions",
        "/v1/assertions/history",
        "/v1/recall",
        "/v1/explain",
        "/v1/forget",
        "/v1/deletions/{receipt_id}",
        "/v1/embedding-inputs",
        "/v1/embeddings",
        "/v1/entities",
        "/v1/entities/query",
        "/v1/entities/{memory_id}",
        "/v1/relations",
        "/v1/relations/{memory_id}/revisions",
        "/v1/graph/expand",
        "/v1/jobs",
        "/v1/jobs/query",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/retry",
        "/v1/jobs/{job_id}/cancel",
        "/v1/jobs/{job_id}/candidates",
        "/v1/jobs/{job_id}/candidates/{ordinal}/adopt",
        "/v1/processing",
        "/v1/working/events",
        "/v1/working/events/query",
        "/v1/working/compact",
        "/v1/working/snapshots/{checkpoint_id}",
        "/v1/checkpoints",
        "/v1/checkpoints/head",
        "/v1/checkpoints/{checkpoint_id}",
        "/v1/checkpoints/restore",
        "/v1/tool-effects",
        "/v1/tool-effects/{memory_id}",
        "/v1/tool-effects/{memory_id}/transitions",
    }
    assert set(paths) - {"/healthz", "/readyz", "/v1/capabilities"} == expected
    methods = {
        name
        for name, value in inspect.getmembers(AsyncMemoryClient, inspect.iscoroutinefunction)
        if not name.startswith("_")
    }
    assert len(methods) == len(expected) == 38


@pytest.mark.parametrize("outcome", ["missing", "invalidated", "wrong_status", "bad_shape", "lost"])
def test_sdk_checkpoint_head_is_read_only_and_validates_errors(monkeypatch, outcome):
    body = CheckpointBranch(scope_id=uuid4(), run_id=uuid4(), branch_id=uuid4())

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/checkpoints/head"
        assert json.loads(request.content) == body.model_dump(mode="json")
        assert "idempotency-key" not in request.headers
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE simulated head response loss")
        if outcome in ("missing", "invalidated"):
            return response(
                404 if outcome == "missing" else 409,
                {
                    "code": "not_found" if outcome == "missing" else "checkpoint_invalidated",
                    "request_id": str(uuid4()),
                    "retryable": False,
                    "details": {},
                },
            )
        return response(201 if outcome == "wrong_status" else 200, {"checkpoint_id": str(uuid4())})

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            with pytest.raises(MemoryClientError) as error:
                await sdk.get_checkpoint_head(body)
            assert not error.value.error.outcome_unknown
            if outcome in ("missing", "invalidated"):
                assert error.value.error.code == (
                    "not_found" if outcome == "missing" else "checkpoint_invalidated"
                )
            assert "PRIVATE" not in str(error.value)
            assert len(calls) == 2
            with pytest.raises(MemoryClientError, match="invalid_request"):
                await sdk.get_checkpoint_head(body.model_copy(update={"run_id": "invalid"}))
            assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["page", "invalidated", "wrong_status", "bad_cursor", "too_many", "lost"]
)
def test_sdk_job_query_is_read_only_bounded_and_never_auto_pages(monkeypatch, outcome):
    body = QueryJobs(scope_ids=[uuid4()], max_items=1)
    job_id = uuid4()
    now = "2026-09-01T00:00:00Z"
    page = {
        "jobs": [
            {
                "job_id": str(job_id),
                "scope_id": str(body.scope_ids[0]),
                "retry_of": None,
                "state": "pending",
                "attempt": 0,
                "available_at": now,
                "lease_until": None,
                "created_at": now,
                "updated_at": now,
                "error_code": None,
                "input_refs": [{"memory_id": str(uuid4()), "revision": 1}],
                "result": None,
            }
        ],
        "next_cursor": {"created_at": now, "job_id": str(job_id)},
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
    }

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/jobs/query"
        assert json.loads(request.content) == body.model_dump(mode="json")
        assert "idempotency-key" not in request.headers
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE simulated query response loss")
        if outcome == "invalidated":
            return response(
                409,
                {
                    "code": "job_invalidated",
                    "request_id": str(uuid4()),
                    "retryable": False,
                },
            )
        if outcome == "bad_cursor":
            return response(200, page | {"next_cursor": {}})
        if outcome == "too_many":
            return response(200, page | {"jobs": page["jobs"] * 101})
        return response(201 if outcome == "wrong_status" else 200, page)

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            if outcome == "page":
                result = await sdk.query_jobs(body)
                assert isinstance(result, JobPage) and result.jobs[0].job_id == job_id
                assert result.next_cursor.job_id == job_id
            else:
                with pytest.raises(MemoryClientError) as error:
                    await sdk.query_jobs(body)
                assert not error.value.error.outcome_unknown and "PRIVATE" not in str(error.value)
                if outcome == "invalidated":
                    assert error.value.error.code == "job_invalidated"
            assert len(calls) == 2
            with pytest.raises(MemoryClientError, match="invalid_request"):
                await sdk.query_jobs(body.model_copy(update={"max_items": True}))
            assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["page", "invalidated", "wrong_status", "bad_cursor", "too_many", "lost"]
)
def test_sdk_assertion_history_is_read_only_bounded_and_never_auto_pages(monkeypatch, outcome):
    body = AssertionHistory(memory_id=uuid4(), max_items=1)
    page = {
        "memory_id": str(body.memory_id),
        "scope_id": str(uuid4()),
        "subject": "ACME",
        "predicate": "tier",
        "current_revision": 2,
        "revisions": [
            {
                "revision": 2,
                "valid_from": None,
                "valid_to": None,
                "recorded_at": "2026-09-01T00:00:00Z",
                "known_until": None,
                "correction_reason": "Correction",
                "epistemic_status": "reported",
                "evidence_refs": [{"memory_id": str(uuid4()), "revision": 1}],
                "relation": None,
            }
        ],
        "next_before_revision": 2,
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
    }

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/assertions/history"
        assert json.loads(request.content) == body.model_dump(mode="json")
        assert "idempotency-key" not in request.headers
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE simulated history response loss")
        if outcome == "invalidated":
            return response(
                409,
                {
                    "code": "assertion_invalidated",
                    "request_id": str(uuid4()),
                    "retryable": False,
                },
            )
        if outcome == "bad_cursor":
            return response(200, page | {"next_before_revision": True})
        if outcome == "too_many":
            return response(200, page | {"revisions": page["revisions"] * 101})
        return response(201 if outcome == "wrong_status" else 200, page)

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            if outcome == "page":
                result = await sdk.get_assertion_history(body)
                assert isinstance(result, AssertionHistoryPage)
                assert result.memory_id == body.memory_id and result.next_before_revision == 2
            else:
                with pytest.raises(MemoryClientError) as error:
                    await sdk.get_assertion_history(body)
                assert not error.value.error.outcome_unknown and "PRIVATE" not in str(error.value)
                if outcome == "invalidated":
                    assert error.value.error.code == "assertion_invalidated"
            assert len(calls) == 2
            with pytest.raises(MemoryClientError, match="invalid_request"):
                await sdk.get_assertion_history(body.model_copy(update={"before_revision": True}))
            assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_sdk_assertion_history_exact_revisions_and_purge(env, api_process):
    with api_process("sdk-assertion-history.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                source = await sdk.observe(
                    observation(env.scopes[0]), idempotency_key="history-source"
                )
                saved = await sdk.remember(
                    memory(env.scopes[0], source.memory_id),
                    idempotency_key="history-memory",
                )
                await sdk.revise_assertion(
                    saved.memory_id,
                    ReviseAssertion(
                        expected_revision=1,
                        value="Silver",
                        evidence=[Evidence(memory_id=source.memory_id, quote="Silver")],
                        explicit_intent=True,
                        reason="Correction",
                    ),
                    idempotency_key="history-revision",
                )
                request = AssertionHistory(memory_id=saved.memory_id, max_items=1)
                first = await sdk.get_assertion_history(request)
                assert isinstance(first, AssertionHistoryPage)
                assert first.current_revision == 2 and first.revisions[0].revision == 2
                second = await sdk.get_assertion_history(
                    request.model_copy(
                        update={"before_revision": first.next_before_revision},
                    )
                )
                assert second.revisions[0].revision == 1 and second.next_before_revision is None
                assert second.revisions[0].known_until == first.revisions[0].recorded_at
                original = await sdk.explain(Explain(memory_id=saved.memory_id))
                assert isinstance(original, AssertionExplanation)
                assert original.assertion.value == "Gold" and original.revision == 1
                purged = await sdk.forget(
                    Forget(memory_ids=[source.memory_id], reason="test"),
                    idempotency_key="history-purge",
                )
                assert purged.object_count == 2
                with pytest.raises(MemoryClientError, match="not_found"):
                    await sdk.get_assertion_history(request)

        asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["page", "unavailable", "wrong_status", "bad_cursor", "too_many", "lost"]
)
def test_sdk_episode_query_is_read_only_bounded_and_never_auto_pages(monkeypatch, outcome):
    body = QueryEpisodes(
        scope_ids=[uuid4()], max_items=1, occurred_from=datetime(2026, 9, 1, tzinfo=UTC)
    )
    item = {
        "memory_id": str(uuid4()),
        "revision": 1,
        "scope_id": str(body.scope_ids[0]),
        "occurred_at": "2026-09-01T00:00:00Z",
        "recorded_at": "2026-09-02T00:00:00Z",
    }
    page = {
        "episodes": [item],
        "next_cursor": {"recorded_at": item["recorded_at"], "memory_id": item["memory_id"]},
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
    }

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/episodes/query"
        assert json.loads(request.content) == body.model_dump(mode="json")
        assert "idempotency-key" not in request.headers
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE simulated episode query response loss")
        if outcome == "unavailable":
            return response(
                503,
                {
                    "code": "dependency_unavailable",
                    "request_id": str(uuid4()),
                    "retryable": True,
                },
            )
        if outcome == "bad_cursor":
            return response(200, page | {"next_cursor": {}})
        if outcome == "too_many":
            return response(200, page | {"episodes": [item] * 101})
        return response(201 if outcome == "wrong_status" else 200, page)

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            if outcome == "page":
                result = await sdk.query_episodes(body)
                assert isinstance(result, EpisodePage)
                assert str(result.episodes[0].memory_id) == item["memory_id"]
                assert result.next_cursor.memory_id == result.episodes[0].memory_id
            else:
                with pytest.raises(MemoryClientError) as error:
                    await sdk.query_episodes(body)
                assert not error.value.error.outcome_unknown and "PRIVATE" not in str(error.value)
                if outcome == "unavailable":
                    assert error.value.error.code == "dependency_unavailable"
            assert len(calls) == 2
            with pytest.raises(MemoryClientError, match="invalid_request"):
                await sdk.query_episodes(body.model_copy(update={"max_items": 101}))
            assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_sdk_episode_query_selects_explicit_evidence_and_obeys_purge(env, api_process):
    with api_process("sdk-episode-query.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                source = await sdk.observe(
                    observation(env.scopes[0]), idempotency_key="episode-query-source"
                )
                late = await sdk.observe(
                    observation(env.scopes[0]).model_copy(
                        update={"occurred_at": datetime(2026, 8, 31, tzinfo=UTC)}
                    ),
                    idempotency_key="episode-query-late",
                )
                request = QueryEpisodes(
                    scope_ids=[env.scopes[0]],
                    occurred_from=datetime(2026, 8, 31, tzinfo=UTC),
                    occurred_to=datetime(2026, 9, 2, tzinfo=UTC),
                    max_items=1,
                )
                first = await sdk.query_episodes(request)
                assert isinstance(first, EpisodePage)
                assert first.episodes[0].memory_id == late.memory_id and first.next_cursor
                second = await sdk.query_episodes(
                    request.model_copy(update={"before": first.next_cursor})
                )
                assert second.episodes[0].memory_id == source.memory_id
                assert second.next_cursor is None
                explained = await sdk.explain(
                    Explain(memory_id=second.episodes[0].memory_id, revision=1)
                )
                assert (
                    isinstance(explained, EpisodeExplanation) and "Gold" in explained.source.content
                )
                saved = await sdk.remember(
                    memory(env.scopes[0], second.episodes[0].memory_id),
                    idempotency_key="episode-query-remember",
                )
                detail = await sdk.explain(Explain(memory_id=saved.memory_id))
                assert isinstance(detail, AssertionExplanation)
                assert detail.evidence[0].memory_id == source.memory_id
                purged = await sdk.forget(
                    Forget(memory_ids=[source.memory_id], reason="test"),
                    idempotency_key="episode-query-purge",
                )
                assert purged.object_count == 2
                page = await sdk.query_episodes(request)
                assert page.episodes[0].memory_id == late.memory_id and page.next_cursor is None
                assert page.consistency.deletion_epoch == 2
                assert not (
                    await sdk.query_episodes(
                        request.model_copy(update={"before": first.next_cursor})
                    )
                ).episodes
                with pytest.raises(MemoryClientError, match="not_found"):
                    await sdk.explain(Explain(memory_id=source.memory_id))

        asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["page", "invalidated", "wrong_status", "bad_cursor", "too_many", "lost"]
)
def test_sdk_entity_query_is_read_only_bounded_and_never_auto_pages(monkeypatch, outcome):
    body = QueryEntities(scope_ids=[uuid4()], max_items=1, canonical_label="Same")
    item = {
        "memory_id": str(uuid4()),
        "revision": 1,
        "scope_id": str(body.scope_ids[0]),
        "entity_type": "component",
        "canonical_label": "Same",
        "recorded_at": "2026-09-01T00:00:00Z",
    }
    page = {
        "entities": [item],
        "next_cursor": {"recorded_at": item["recorded_at"], "memory_id": item["memory_id"]},
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
    }

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/entities/query"
        assert json.loads(request.content) == body.model_dump(mode="json")
        assert "idempotency-key" not in request.headers
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE simulated entity query response loss")
        if outcome == "invalidated":
            return response(
                409,
                {
                    "code": "entity_invalidated",
                    "request_id": str(uuid4()),
                    "retryable": False,
                },
            )
        if outcome == "bad_cursor":
            return response(200, page | {"next_cursor": {}})
        if outcome == "too_many":
            return response(200, page | {"entities": [item] * 101})
        return response(201 if outcome == "wrong_status" else 200, page)

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            if outcome == "page":
                result = await sdk.query_entities(body)
                assert isinstance(result, EntityPage)
                assert str(result.entities[0].memory_id) == item["memory_id"]
                assert result.next_cursor.memory_id == result.entities[0].memory_id
            else:
                with pytest.raises(MemoryClientError) as error:
                    await sdk.query_entities(body)
                assert not error.value.error.outcome_unknown and "PRIVATE" not in str(error.value)
                if outcome == "invalidated":
                    assert error.value.error.code == "entity_invalidated"
            assert len(calls) == 2
            with pytest.raises(MemoryClientError, match="invalid_request"):
                await sdk.query_entities(body.model_copy(update={"entity_type": "invalid"}))
            assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_sdk_entity_query_preserves_duplicate_identities_for_explicit_graph_seeds(
    env, api_process
):
    with api_process("sdk-entity-query.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                source = await sdk.observe(
                    observation(env.scopes[0]),
                    idempotency_key="entity-query-source",
                )
                saved = []
                for number in range(2):
                    saved.append(
                        await sdk.create_entity(
                            CreateEntity(
                                scope_id=env.scopes[0],
                                entity_type="component",
                                canonical_label="ACME",
                                evidence=[Evidence(memory_id=source.memory_id, quote="ACME")],
                                explicit_intent=True,
                            ),
                            idempotency_key=f"entity-query-{number}",
                        )
                    )
                relation = await sdk.create_relation(
                    CreateRelation(
                        scope_id=env.scopes[0],
                        source_entity=saved[0].memory_id,
                        target_entity=saved[1].memory_id,
                        predicate="depends_on",
                        evidence=[Evidence(memory_id=source.memory_id, quote="ACME")],
                        explicit_intent=True,
                    ),
                    idempotency_key="entity-query-relation",
                )
                request = QueryEntities(
                    scope_ids=[env.scopes[0]],
                    canonical_label="ACME",
                    entity_type="component",
                    max_items=1,
                )
                first = await sdk.query_entities(request)
                second = await sdk.query_entities(
                    request.model_copy(update={"before": first.next_cursor})
                )
                assert (
                    isinstance(first, EntityPage)
                    and first.entities[0].memory_id == saved[1].memory_id
                )
                assert (
                    second.entities[0].memory_id == saved[0].memory_id
                    and second.next_cursor is None
                )
                detail = await sdk.get_entity(second.entities[0].memory_id)
                assert detail.evidence[0].memory_id == source.memory_id
                graph = await sdk.expand_graph(
                    ExpandGraph(
                        scope_ids=[env.scopes[0]],
                        seeds=[second.entities[0].memory_id],
                        relation_types=["depends_on"],
                        purpose="explicit selected seed",
                    )
                )
                assert graph.edges[0].assertion.memory_id == relation.memory_id
                purged = await sdk.forget(
                    Forget(memory_ids=[source.memory_id], reason="test"),
                    idempotency_key="entity-query-purge",
                )
                assert purged.object_count == 4 and not (await sdk.query_entities(request)).entities

        asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["receipt", "conflict", "wrong_status", "empty", "too_many", "lost"]
)
def test_sdk_batch_capture_preserves_mutation_uncertainty_bounds_and_no_automatic_split(
    monkeypatch, outcome
):
    body = batch_request()
    receipt = {
        "memory_id": str(uuid4()),
        "revision": 1,
        "synthesis_job_ids": [str(uuid4()), str(uuid4())],
    }

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/captures/batch"
        assert request.headers["idempotency-key"] == "durable-batch"
        assert json.loads(request.content) == body.model_dump(mode="json")
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE simulated batch response loss")
        if outcome == "conflict":
            return response(
                409,
                {
                    "code": "idempotency_conflict",
                    "request_id": str(uuid4()),
                    "retryable": False,
                },
            )
        if outcome == "empty":
            return response(201, receipt | {"synthesis_job_ids": []})
        if outcome == "too_many":
            return response(201, receipt | {"synthesis_job_ids": [str(uuid4()) for _ in range(17)]})
        return response(200 if outcome == "wrong_status" else 201, receipt)

    _, calls = mock_client(monkeypatch, handler)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            if outcome == "receipt":
                result = await sdk.capture_batch(body, idempotency_key="durable-batch")
                assert isinstance(result, CaptureBatchResult)
                assert result.model_dump(mode="json") == receipt
            else:
                with pytest.raises(MemoryClientError) as error:
                    await sdk.capture_batch(body, idempotency_key="durable-batch")
                assert "PRIVATE" not in str(error.value)
                assert error.value.error.outcome_unknown == (outcome != "conflict")
            assert len(calls) == 2
            with pytest.raises(MemoryClientError, match="invalid_request"):
                await sdk.capture_batch(
                    body.model_copy(update={"memories": [body.memories[0]] * 2}),
                    idempotency_key="invalid",
                )
            oversized = body.model_copy(
                update={
                    "memories": [
                        body.memories[0].model_copy(
                            update={"subject": str(i), "value": "x" * 65536}
                        )
                        for i in range(5)
                    ]
                }
            )
            with pytest.raises(MemoryClientError, match="body_too_large") as large:
                await sdk.capture_batch(oversized, idempotency_key="oversized")
            assert not large.value.error.outcome_unknown and len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_sdk_batch_capture_ordered_jobs_independent_publication_and_purge(env, api_process):
    with api_process("sdk-batch.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                body = batch_request(env.scopes[0])
                saved = await sdk.capture_batch(body, idempotency_key="batch")
                assert isinstance(saved, CaptureBatchResult) and len(saved.synthesis_job_ids) == 2
                assert await sdk.capture_batch(body, idempotency_key="batch") == saved
                assert await sdk.capture_batch(body, idempotency_key="batch-new") == saved
                for job_id in saved.synthesis_job_ids:
                    queued = await sdk.get_job(job_id)
                    assert queued.input_refs[0].memory_id == saved.memory_id
                    assert queued.state == "pending"
                published_ids = set()
                for _ in saved.synthesis_job_ids:
                    published = await run_once(env.settings.database_url, env.subjects[0])
                    assert published["outcome"] == "succeeded"
                    job_id = UUID(published["job_id"])
                    published_ids.add(job_id)
                    detail = await sdk.get_job(job_id)
                    full = await sdk.explain(Explain(memory_id=detail.result.memory_id))
                    assert isinstance(full, AssertionExplanation)
                    assert (
                        full.assertion.subject
                        == body.memories[saved.synthesis_job_ids.index(job_id)].subject
                    )
                assert published_ids == set(saved.synthesis_job_ids)
                assert await sdk.capture_batch(body, idempotency_key="batch") == saved
                purged = await sdk.forget(
                    Forget(memory_ids=[saved.memory_id], reason="test"),
                    idempotency_key="batch-purge",
                )
                assert purged.object_count == 5
                with pytest.raises(MemoryClientError, match="not_found"):
                    await sdk.capture_batch(body, idempotency_key="batch")

        asyncio.run(scenario())


@pytest.mark.integration
def test_real_sdk_owned_job_pagination_state_filter_and_purge(env, api_process):
    with api_process("sdk-job-query.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                source = await sdk.observe(
                    observation(env.scopes[0]), idempotency_key="query-source"
                )
                jobs = []
                for number in range(5):
                    saved = await sdk.enqueue_job(
                        EnqueueJob(
                            kind="structured_remember",
                            memory=memory(env.scopes[0], source.memory_id).model_copy(
                                update={"subject": f"Synthetic-{number}"}
                            ),
                        ),
                        idempotency_key=f"query-job-{number}",
                    )
                    jobs.append(saved)
                request = QueryJobs(scope_ids=[env.scopes[0]], max_items=2)
                before = None
                observed = []
                for _ in range(3):
                    result = await sdk.query_jobs(request.model_copy(update={"before": before}))
                    assert isinstance(result, JobPage)
                    assert all(job.scope_id == env.scopes[0] for job in result.jobs)
                    observed.extend(job.job_id for job in result.jobs)
                    before = result.next_cursor
                assert observed == [job.job_id for job in reversed(jobs)] and before is None
                await sdk.cancel_job(
                    jobs[2].job_id,
                    CancelJob(expected_state="pending", expected_attempt=0),
                    idempotency_key="query-cancel",
                )
                cancelled = await sdk.query_jobs(
                    QueryJobs(scope_ids=[env.scopes[0]], states=["cancelled"])
                )
                assert [job.job_id for job in cancelled.jobs] == [jobs[2].job_id]
                purged = await sdk.forget(
                    Forget(memory_ids=[source.memory_id], reason="test"),
                    idempotency_key="query-purge",
                )
                assert purged.object_count == 6
                assert not (await sdk.query_jobs(request)).jobs

        asyncio.run(scenario())


@pytest.mark.integration
def test_real_sdk_memory_capture_vector_revision_and_purge(env, api_process):
    with api_process("sdk-memory.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as client:
                body = observation(env.scopes[0])
                source = await client.observe(body, idempotency_key="source")
                assert await client.observe(body, idempotency_key="source") == source
                assert source.synthesis_job_id is None
                explained = await client.explain(Explain(memory_id=source.memory_id))
                assert (
                    isinstance(explained, EpisodeExplanation)
                    and explained.source.content == body.content
                )
                saved = await client.remember(
                    memory(env.scopes[0], source.memory_id), idempotency_key="remember"
                )
                revised = await client.revise_assertion(
                    saved.memory_id,
                    ReviseAssertion(
                        expected_revision=1,
                        value="Silver",
                        evidence=[Evidence(memory_id=source.memory_id, quote="Silver")],
                        explicit_intent=True,
                        reason="correction",
                    ),
                    idempotency_key="revision",
                )
                assert revised.revision == 2
                old = await client.explain(Explain(memory_id=saved.memory_id))
                assert isinstance(old, AssertionExplanation) and old.assertion.value == "Gold"
                current = await client.explain(Explain(memory_id=saved.memory_id, revision=2))
                assert current.assertion.value == "Silver"
                model = EmbeddingModel(name="synthetic", revision="basis-v1")
                basis = [1.0] + [0.0] * 767
                for identity, revision in [(source.memory_id, 1), (saved.memory_id, 2)]:
                    canonical = await client.embedding_input(
                        Explain(memory_id=identity, revision=revision)
                    )
                    vector = PutEmbedding(
                        memory_id=identity,
                        revision=revision,
                        input_digest=canonical.input_digest,
                        model=model,
                        values=basis,
                    )
                    receipt = await client.put_embedding(vector, idempotency_key=str(identity))
                    assert (
                        await client.put_embedding(vector, idempotency_key=str(identity)) == receipt
                    )
                for mode, query in [("lexical", "Silver"), ("vector", ""), ("hybrid", "Silver")]:
                    recalled = await client.recall(
                        Recall(
                            scope_ids=[env.scopes[0]],
                            purpose="sdk",
                            retrieval_mode=mode,
                            query=query,
                            vector_query=VectorQuery(model=model, values=basis)
                            if mode != "lexical"
                            else None,
                        )
                    )
                    assert {item.memory_id for item in recalled.items} == {
                        source.memory_id,
                        saved.memory_id,
                    }
                    assert recalled.coverage.retrieval_complete and recalled.retrieval_mode == mode
                capture = Capture(
                    episode=observation(env.scopes[0]),
                    memory=CapturedMemory(
                        subject="ACME",
                        predicate="tier",
                        value="Gold",
                        evidence_quote="Gold",
                        explicit_intent=True,
                    ),
                )
                captured = await client.capture(capture, idempotency_key="capture")
                assert await client.capture(capture, idempotency_key="capture") == captured
                assert (await client.get_job(captured.synthesis_job_id)).state == "pending"
                assert (await run_once(env.settings.database_url, env.subjects[0]))[
                    "outcome"
                ] == "succeeded"
                assert (await client.get_job(captured.synthesis_job_id)).result is not None
                preview = await client.forget(
                    Forget(memory_ids=[source.memory_id], mode="preview", reason="sdk"),
                    idempotency_key="preview",
                )
                assert isinstance(preview, DeletionPreview) and preview.object_count == 2
                purged = await client.forget(
                    Forget(memory_ids=[source.memory_id, captured.memory_id], reason="sdk"),
                    idempotency_key="purge",
                )
                assert isinstance(purged, DeletionResult)
                assert (
                    await client.get_deletion(purged.deletion_id)
                ).state == "active_store_purged"
                for request in [
                    client.observe(body, idempotency_key="source"),
                    client.put_embedding(vector, idempotency_key=str(saved.memory_id)),
                ]:
                    with pytest.raises(MemoryClientError) as failure:
                        await request
                    assert failure.value.error.code == "not_found"
                assert not (
                    await client.recall(Recall(scope_ids=[env.scopes[0]], purpose="sdk"))
                ).items

        asyncio.run(scenario())


@pytest.mark.integration
def test_real_sdk_graph_job_retry_checkpoints_and_effects(env, api_process):
    with api_process("sdk-resources.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as client:
                source = await client.observe(observation(env.scopes[0]), idempotency_key="source")
                evidence = [Evidence(memory_id=source.memory_id, quote="ACME")]
                entities = [
                    await client.create_entity(
                        CreateEntity(
                            scope_id=env.scopes[0],
                            entity_type="component",
                            canonical_label=name,
                            evidence=evidence,
                            explicit_intent=True,
                        ),
                        idempotency_key=name.replace(" ", "-"),
                    )
                    for name in ("ACME", "ACME 2", "ACME 3")
                ]
                assert (await client.get_entity(entities[0].memory_id)).canonical_label == "ACME"
                relation = await client.create_relation(
                    CreateRelation(
                        scope_id=env.scopes[0],
                        source_entity=entities[0].memory_id,
                        target_entity=entities[1].memory_id,
                        predicate="depends_on",
                        evidence=evidence,
                        explicit_intent=True,
                    ),
                    idempotency_key="relation",
                )
                revised = await client.revise_relation(
                    relation.memory_id,
                    ReviseRelation(
                        expected_revision=1,
                        target_entity=entities[2].memory_id,
                        evidence=evidence,
                        explicit_intent=True,
                        reason="correction",
                    ),
                    idempotency_key="relation-revision",
                )
                assert revised.revision == 2
                graph = await client.expand_graph(
                    ExpandGraph(
                        scope_ids=[env.scopes[0]],
                        seeds=[entities[0].memory_id],
                        relation_types=["depends_on"],
                        purpose="sdk",
                    )
                )
                assert graph.edges[0].target_entity == entities[2].memory_id
                job_body = EnqueueJob(
                    kind="structured_remember", memory=memory(env.scopes[0], source.memory_id)
                )
                job = await client.enqueue_job(job_body, idempotency_key="job")
                async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
                    lease = await jobs.claim()
                async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
                    await jobs.fail(job.job_id, lease["lease_token"], "invalid_input", retry=False)
                retried = await client.retry_job(job.job_id, job_body, idempotency_key="retry")
                assert retried.job_id != job.job_id
                assert (await client.get_job(retried.job_id)).retry_of == job.job_id
                cancel_body = job_body.model_copy(
                    update={
                        "memory": job_body.memory.model_copy(update={"predicate": "cancelled_sdk"}),
                    }
                )
                cancelled = await client.enqueue_job(cancel_body, idempotency_key="cancel-source")
                assert (
                    await client.cancel_job(
                        cancelled.job_id,
                        CancelJob(expected_state="pending", expected_attempt=0),
                        idempotency_key="cancel",
                    )
                ).job_id == cancelled.job_id
                assert (await client.get_job(cancelled.job_id)).state == "cancelled"
                assert (await run_once(env.settings.database_url, env.subjects[0]))[
                    "outcome"
                ] == "succeeded"
                checkpoint = await client.create_checkpoint(
                    CreateCheckpoint(
                        scope_id=env.scopes[0],
                        run_id=uuid4(),
                        branch_id=uuid4(),
                        expected_head=None,
                        harness_id="sdk-test",
                        harness_version="1",
                        event_watermark=0,
                        state=CheckpointState(
                            goal="synthetic recovery", completed_actions=["東" * 4096] * 25
                        ),
                    ),
                    idempotency_key="checkpoint",
                )
                envelope = await client.get_checkpoint(checkpoint.checkpoint_id)
                assert isinstance(envelope, CheckpointEnvelope)
                branch = CheckpointBranch(
                    scope_id=env.scopes[0], run_id=checkpoint.run_id, branch_id=checkpoint.branch_id
                )
                assert await client.get_checkpoint_head(branch) == envelope
                effect = await client.plan_tool_effect(
                    PlanToolEffect(
                        scope_id=env.scopes[0],
                        run_id=checkpoint.run_id,
                        operation_id=uuid4(),
                        tool_name="synthetic",
                        action_hash="0" * 64,
                    ),
                    idempotency_key="effect",
                )
                await client.transition_tool_effect(
                    effect.memory_id,
                    TransitionToolEffect(
                        expected_revision=1,
                        status="dispatched",
                        reason="synthetic dispatch",
                    ),
                    idempotency_key="transition",
                )
                assert (await client.get_tool_effect(effect.memory_id)).status == "dispatched"
                active = await client.get_checkpoint_head(branch)
                assert not active.resume_allowed and not active.automatic_reexecution
                assert active.tool_effects[0].status == "dispatched"
                restored = await client.restore_checkpoint(
                    RestoreCheckpoint(
                        checkpoint_id=checkpoint.checkpoint_id,
                        target_branch_id=uuid4(),
                        harness_id="sdk-test",
                        harness_version="1",
                    ),
                    idempotency_key="restore",
                )
                assert not restored.resume_allowed and not restored.automatic_reexecution
                assert (await client.get_tool_effect(effect.memory_id)).status == "unknown"
                assert (
                    await client.get_checkpoint_head(
                        branch.model_copy(update={"branch_id": restored.branch_id})
                    )
                    == restored
                )
                assert (await client.get_checkpoint_head(branch)).tool_effects[
                    0
                ].status == "unknown"

        asyncio.run(scenario())


@pytest.mark.integration
def test_sdk_does_not_cache_authorization_or_scope_visibility(env, api_process):
    with api_process("sdk-auth.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as client:
                body = observation(env.scopes[0])
                source = await client.observe(body, idempotency_key="source")
                async with AsyncMemoryClient(str(http.base_url), env.token(1)) as other:
                    assert not (
                        await other.recall(Recall(scope_ids=env.scopes, purpose="sdk"))
                    ).items
                    with pytest.raises(MemoryClientError) as failure:
                        await other.embedding_input(Explain(memory_id=source.memory_id))
                    assert failure.value.error.native_status == 404
                with psycopg.connect(env.admin_url) as conn:
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (str(env.tenants[0]),),
                    )
                    conn.execute(
                        "DELETE FROM memory.scope_member WHERE scope_id=%s", (env.scopes[0],)
                    )
                    conn.execute(
                        "UPDATE memory.tenant SET access_epoch=access_epoch+1 WHERE id=%s",
                        (env.tenants[0],),
                    )
                with pytest.raises(MemoryClientError) as failure:
                    await client.observe(body, idempotency_key="source")
                assert failure.value.error.code == "not_found"
                assert not (await client.recall(Recall(scope_ids=env.scopes, purpose="sdk"))).items
            with pytest.raises(MemoryClientError) as failure:
                async with AsyncMemoryClient(str(http.base_url), env.token(exp=1)):
                    pytest.fail("Expired token accepted")
            assert failure.value.error.code == "unauthenticated"

        asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("batch", [False, True])
def test_sdk_lost_committed_response_replays_same_reference(
    env, api_process, monkeypatch, lose_first_response_transport, batch
):
    lost = lose_first_response_transport
    direct = httpx.AsyncHTTPTransport()

    class RoutedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            transport = direct if request.url.path == "/v1/capabilities" else lost
            return await transport.handle_async_request(request)

        async def aclose(self):
            await direct.aclose()
            await lost.aclose()

    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url,
            transport=RoutedTransport(),
            headers={"Authorization": f"Bearer {settings.api_token}"},
            trust_env=False,
            follow_redirects=False,
        )

    monkeypatch.setattr(NativeSettings, "client", client)
    with api_process("sdk-loss.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                body = batch_request(env.scopes[0]) if batch else observation(env.scopes[0])
                send = sdk.capture_batch if batch else sdk.observe
                with pytest.raises(MemoryClientError) as failure:
                    await send(body, idempotency_key="durable-key")
                assert failure.value.error.outcome_unknown and lost.calls == 1
                assert lost.committed_response is not None
                replay = await send(body, idempotency_key="durable-key")
                assert replay.memory_id == UUID(lost.committed_response["memory_id"])
                if batch:
                    assert replay.model_dump(mode="json") == lost.committed_response
                assert lost.calls == 2
            with psycopg.connect(env.admin_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM memory.episode WHERE tenant_id=%s", (env.tenants[0],)
                    ).fetchone()[0]
                    == 1
                )

        asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("lost_path", ["/v1/checkpoints", "/v1/checkpoints/head"])
def test_sdk_recovers_checkpoint_head_after_real_response_loss(
    env, api_process, monkeypatch, lost_path
):
    class LoseCheckpointResponse(httpx.AsyncHTTPTransport):
        calls = 0
        observed = None

        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if request.url.path == lost_path:
                self.calls += 1
                if self.calls == 1:
                    await response.aread()
                    assert response.status_code == (201 if lost_path == "/v1/checkpoints" else 200)
                    self.observed = response.json()
                    await response.aclose()
                    raise httpx.ReadError("simulated checkpoint response loss")
            return response

    transport = LoseCheckpointResponse()

    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.api_token}"},
            trust_env=False,
            follow_redirects=False,
        )

    monkeypatch.setattr(NativeSettings, "client", client)
    with api_process("sdk-head-loss.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                branch = CheckpointBranch(scope_id=env.scopes[0], run_id=uuid4(), branch_id=uuid4())
                body = CreateCheckpoint(
                    **branch.model_dump(),
                    expected_head=None,
                    harness_id="head-recovery",
                    harness_version="1",
                    event_watermark=0,
                    state=CheckpointState(goal="Recover head without reexecution"),
                )
                if lost_path == "/v1/checkpoints":
                    with pytest.raises(MemoryClientError) as failure:
                        await sdk.create_checkpoint(body, idempotency_key="durable-checkpoint")
                    assert failure.value.error.outcome_unknown and transport.calls == 1
                    recovered = await sdk.get_checkpoint_head(branch)
                    replay = await sdk.create_checkpoint(body, idempotency_key="durable-checkpoint")
                    assert replay.checkpoint_id == recovered.checkpoint_id
                else:
                    saved = await sdk.create_checkpoint(body, idempotency_key="durable-checkpoint")
                    with pytest.raises(MemoryClientError) as failure:
                        await sdk.get_checkpoint_head(branch)
                    assert not failure.value.error.outcome_unknown and transport.calls == 1
                    recovered = await sdk.get_checkpoint_head(branch)
                    assert saved.checkpoint_id == recovered.checkpoint_id
                assert recovered.checkpoint_id == UUID(transport.observed["checkpoint_id"])
                assert recovered.sequence == 1 and recovered.resume_allowed
                assert not recovered.automatic_reexecution and transport.calls == 2

        asyncio.run(scenario())


@pytest.mark.integration
def test_sdk_lost_cancellation_response_replays_terminal_receipt(env, api_process, monkeypatch):
    class LoseCancelResponse(httpx.AsyncHTTPTransport):
        calls = 0
        committed = None

        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if request.url.path.endswith("/cancel"):
                self.calls += 1
                if self.calls == 1:
                    await response.aread()
                    assert response.status_code == 200
                    self.committed = response.json()
                    await response.aclose()
                    raise httpx.ReadError("simulated committed cancellation response loss")
            return response

    transport = LoseCancelResponse()

    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.api_token}"},
            trust_env=False,
            follow_redirects=False,
        )

    monkeypatch.setattr(NativeSettings, "client", client)
    with api_process("sdk-cancel-loss.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                source = await sdk.observe(observation(env.scopes[0]), idempotency_key="source")
                receipt = await sdk.enqueue_job(
                    EnqueueJob(
                        kind="structured_remember", memory=memory(env.scopes[0], source.memory_id)
                    ),
                    idempotency_key="job",
                )
                body = CancelJob(expected_state="pending", expected_attempt=0)
                with pytest.raises(MemoryClientError) as failure:
                    await sdk.cancel_job(receipt.job_id, body, idempotency_key="durable-cancel")
                assert failure.value.error.outcome_unknown and transport.calls == 1
                assert (await sdk.get_job(receipt.job_id)).state == "cancelled"
                replay = await sdk.cancel_job(
                    receipt.job_id, body, idempotency_key="durable-cancel"
                )
                assert replay.model_dump(mode="json") == transport.committed
                assert transport.calls == 2
                with pytest.raises(MemoryClientError) as conflict:
                    await sdk.cancel_job(receipt.job_id, body, idempotency_key="different-key")
                assert conflict.value.error.code == "job_cancel_conflict"
                assert not conflict.value.error.outcome_unknown
                assert await run_once(env.settings.database_url, env.subjects[0]) == {
                    "outcome": "idle"
                }
            with psycopg.connect(env.admin_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM memory_ops.audit_event "
                        "WHERE tenant_id=%s AND action='job_cancelled'",
                        (env.tenants[0],),
                    ).fetchone()[0]
                    == 1
                )

        asyncio.run(scenario())
