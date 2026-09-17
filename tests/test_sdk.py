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
    Capture,
    CapturedMemory,
    CheckpointEnvelope,
    CheckpointState,
    CreateCheckpoint,
    CreateEntity,
    CreateRelation,
    DeletionPreview,
    DeletionResult,
    EmbeddingModel,
    EnqueueJob,
    EpisodeExplanation,
    Evidence,
    ExpandGraph,
    Explain,
    Forget,
    Observe,
    ObserveResult,
    PlanToolEffect,
    PutEmbedding,
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


@pytest.mark.parametrize("method", ["get", "forget_preview", "forget_purge"])
def test_response_status_and_mode_are_not_silently_coerced(monkeypatch, method):
    def handler(request):
        if method == "get":
            assert request.method == "GET" and not request.content
            assert "idempotency-key" not in request.headers
            return response(201, {})
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
def test_bad_keys_are_rejected_before_network(monkeypatch, key):
    _, calls = mock_client(monkeypatch, lambda _: pytest.fail("Invalid key reached API"))

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as client:
            with pytest.raises(MemoryClientError, match="invalid_request") as failure:
                await client.observe(observation(), idempotency_key=key)
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
        "/v1/captures",
        "/v1/remember",
        "/v1/assertions/{memory_id}/revisions",
        "/v1/recall",
        "/v1/explain",
        "/v1/forget",
        "/v1/deletions/{receipt_id}",
        "/v1/embedding-inputs",
        "/v1/embeddings",
        "/v1/entities",
        "/v1/entities/{memory_id}",
        "/v1/relations",
        "/v1/relations/{memory_id}/revisions",
        "/v1/graph/expand",
        "/v1/jobs",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/retry",
        "/v1/checkpoints",
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
    assert len(methods) == len(expected) == 24


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
def test_sdk_lost_committed_response_replays_same_reference(
    env, api_process, monkeypatch, lose_first_response_transport
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
                body = observation(env.scopes[0])
                with pytest.raises(MemoryClientError) as failure:
                    await sdk.observe(body, idempotency_key="durable-key")
                assert failure.value.error.outcome_unknown and lost.calls == 1
                assert lost.committed_response is not None
                replay = await sdk.observe(body, idempotency_key="durable-key")
                assert replay.memory_id == UUID(lost.committed_response["memory_id"])
                assert lost.calls == 2
            with psycopg.connect(env.admin_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM memory.episode WHERE tenant_id=%s", (env.tenants[0],)
                    ).fetchone()[0]
                    == 1
                )

        asyncio.run(scenario())
