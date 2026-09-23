import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory.external_source import (
    SOURCE_FORMAT,
    SOURCE_NAMESPACE,
    ExternalSnapshot,
    ExternalSourceMemory,
    SnapshotEnvelope,
    SourceBinding,
    SourceCaptureOutcome,
    snapshot_digest,
)
from pg_agmemory.models import (
    AssertionHistory,
    CheckpointBranch,
    CheckpointState,
    CreateCheckpoint,
    EpisodeExplanation,
    Evidence,
    Explain,
    Forget,
    MemoryReference,
    Observe,
    ObserveResult,
    QueryEpisodes,
    Recall,
    Remember,
    RestoreCheckpoint,
)
from pg_agmemory.native_client import NativeSettings, failure
from pg_agmemory.scope_access import ScopeAccessError, ScopeAccessRequest, scope_access
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

PRIVATE_TEXT = "  ACME Gold — PRIVATE_SOURCE_RESULT\n"
HARNESS_ID = "external-source-tests"


def binding(scope_id=None, **changes):
    return SourceBinding(
        **{
            "scope_id": scope_id or uuid4(),
            "source_system": "synthetic-warehouse",
            "dataset_id": "contracts",
            "source_subject": "synthetic-reader",
            **changes,
        }
    )


def snapshot(**changes):
    text = changes.get("snapshot_text", PRIVATE_TEXT)
    return ExternalSnapshot(
        **{
            "semantic_revision": "contract-query-v1",
            "query_id": "durable-query-42",
            "observed_at": datetime(2026, 9, 1, tzinfo=UTC),
            "acl_version": "source-policy-v7",
            "snapshot_text": text,
            "result_digest": snapshot_digest(text),
            "requires_refresh": True,
            "source_authority": "external_observation",
            **changes,
        }
    )


def envelope(source, result=None):
    return SnapshotEnvelope(
        format=SOURCE_FORMAT, source=source, snapshot=result or snapshot()
    )


def content(value):
    return json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


@pytest.fixture
def adapter():
    client = create_autospec(AsyncMemoryClient, instance=True)
    client.observe.return_value = ObserveResult(memory_id=uuid4(), revision=1)
    source = binding()
    memory = ExternalSourceMemory(client, binding=source)
    return SimpleNamespace(client=client, binding=source, memory=memory)


def explained(memory_id, text):
    return EpisodeExplanation(
        memory_id=memory_id,
        revision=1,
        type="episode",
        source={
            "content": text,
            "occurred_at": datetime(2026, 9, 1, tzinfo=UTC),
            "consent_reference": "synthetic-consent",
        },
    )


def test_binding_is_bounded_frozen_and_rejects_unrecognized_fields():
    assert SOURCE_FORMAT == "pgag-external-snapshot-v1"
    assert SOURCE_NAMESPACE == "external-snapshot-v1"
    value = binding()
    with pytest.raises(ValidationError):
        value.scope_id = uuid4()
    for field in ("source_system", "dataset_id", "source_subject"):
        assert getattr(binding(**{field: "x" * 256}), field) == "x" * 256
        for invalid in ("", "x" * 257):
            with pytest.raises(ValidationError):
                binding(**{field: invalid})
    with pytest.raises(ValidationError):
        binding(unrecognized="PRIVATE_EXTRA")


def test_snapshot_hashes_exact_utf8_and_preserves_whitespace_and_bounds():
    for text in (PRIVATE_TEXT, " \r\n\t ", "x" * 32768):
        value = snapshot(snapshot_text=text)
        assert value.snapshot_text == text
        assert value.result_digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert snapshot_digest(text) == value.result_digest
        restored = SnapshotEnvelope.model_validate_json(content(envelope(binding(), value)))
        assert restored.snapshot == value
    assert snapshot_digest(PRIVATE_TEXT) != snapshot_digest(PRIVATE_TEXT.strip())
    assert snapshot_digest("e\u0301") != snapshot_digest("\u00e9")


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_text": ""},
        {"snapshot_text": "x" * 32769},
        {"observed_at": datetime(2026, 9, 1)},
        {"result_digest": "A" * 64},
        {"result_digest": "0" * 64},
        {"requires_refresh": False},
        {"source_authority": "authoritative_truth"},
        {"unrecognized": "PRIVATE_EXTRA"},
    ],
)
def test_snapshot_rejects_invalid_contract(changes):
    with pytest.raises(ValidationError):
        snapshot(**changes)


def test_capture_is_explicit_deterministic_typed_and_schedules_no_inference(adapter):
    value = snapshot()

    async def scenario():
        first = await adapter.memory.capture(
            value, consent_reference="synthetic-consent", idempotency_key="durable-key"
        )
        second = await adapter.memory.capture(
            value, consent_reference="synthetic-consent", idempotency_key="durable-key"
        )
        assert isinstance(first, SourceCaptureOutcome)
        assert first.source_query_status == "succeeded"
        assert first.memory_capture_status == "stored" and first.error is None
        assert first.memory == adapter.client.observe.return_value
        assert second == first

    asyncio.run(scenario())
    first, second = adapter.client.observe.await_args_list
    assert first == second
    assert first.kwargs == {"idempotency_key": "durable-key"}
    request = first.args[0]
    assert isinstance(request, Observe)
    assert request.scope_id == adapter.binding.scope_id
    assert request.source_namespace == SOURCE_NAMESPACE
    assert re.fullmatch("[0-9a-f]{64}", request.source_event_id)
    assert request.occurred_at == value.observed_at
    assert request.consent_reference == "synthetic-consent"
    assert not request.auto_extract and not request.auto_embed
    assert request.content == content(envelope(adapter.binding, value))
    assert [call[0] for call in adapter.client.mock_calls] == ["observe", "observe"]
    assert not hasattr(adapter.memory, "forget")


def test_event_identity_uses_source_binding_and_query_not_mutable_result(adapter):
    async def capture(source, value):
        await ExternalSourceMemory(adapter.client, binding=source).capture(
            value, consent_reference="synthetic-consent", idempotency_key=str(uuid4())
        )
        return adapter.client.observe.await_args.args[0]

    async def scenario():
        original = await capture(adapter.binding, snapshot())
        changed = await capture(
            adapter.binding,
            snapshot(
                snapshot_text="Different PRIVATE_SOURCE_RESULT",
                acl_version="source-policy-v8",
                semantic_revision="contract-query-v2",
                observed_at=datetime(2026, 9, 2, tzinfo=UTC),
            ),
        )
        assert changed.source_event_id == original.source_event_id
        assert changed.content != original.content
        moved = await capture(
            adapter.binding.model_copy(update={"scope_id": uuid4()}), snapshot()
        )
        assert moved.source_event_id == original.source_event_id
        alternate = await capture(adapter.binding, snapshot(query_id="durable-query-43"))
        assert alternate.source_event_id != original.source_event_id
        for field in ("source_system", "dataset_id", "source_subject"):
            alternate = await capture(
                adapter.binding.model_copy(update={field: "different"}), snapshot()
            )
            assert alternate.source_event_id != original.source_event_id

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invalid", ["mapping", "constructed", "mutated", "consent", "key", "serialized_envelope"]
)
def test_capture_invalid_inputs_are_redacted_before_any_client_call(adapter, invalid):
    value = snapshot()
    consent, key = "synthetic-consent", "durable-key"
    if invalid == "mapping":
        value = value.model_dump()
    elif invalid == "constructed":
        value = ExternalSnapshot.model_construct(
            **{**value.model_dump(), "snapshot_text": "PRIVATE_MUTATION"}
        )
    elif invalid == "mutated":
        value = value.model_copy(update={"snapshot_text": "PRIVATE_MUTATION"})
    elif invalid == "consent":
        consent = "PRIVATE_CONSENT" * 30
    elif invalid == "serialized_envelope":
        value = snapshot(snapshot_text="\x01" * 32768)
    else:
        key = "PRIVATE_KEY\n"

    async def scenario():
        with pytest.raises(MemoryClientError) as caught:
            await adapter.memory.capture(value, consent_reference=consent, idempotency_key=key)
        assert caught.value.error.code == "invalid_request"
        assert not caught.value.error.outcome_unknown
        assert "PRIVATE" not in str(caught.value)
        assert "PRIVATE" not in repr(caught.value.error)

    asyncio.run(scenario())
    assert adapter.client.mock_calls == []


def test_constructed_binding_is_revalidated_without_exposing_its_content(adapter):
    source = SourceBinding.model_construct(
        **{**adapter.binding.model_dump(), "dataset_id": "PRIVATE_DATASET" * 30}
    )
    with pytest.raises(MemoryClientError) as caught:
        ExternalSourceMemory(adapter.client, binding=source)
    assert caught.value.error.code == "invalid_request"
    assert "PRIVATE" not in str(caught.value)
    assert adapter.client.mock_calls == []


@pytest.mark.parametrize(
    "code,status,retryable,unknown",
    [
        ("source_event_conflict", 409, False, False),
        ("not_found", 404, False, False),
        ("native_api_unavailable", None, True, True),
        ("dependency_unavailable", 503, True, True),
    ],
)
def test_capture_preserves_safe_partial_outcome_without_retry(
    adapter, code, status, retryable, unknown
):
    error = failure(
        code, status=status, retryable=retryable, unknown=unknown, request_id=uuid4()
    )
    adapter.client.observe.side_effect = error

    async def scenario():
        result = await adapter.memory.capture(
            snapshot(), consent_reference="synthetic-consent", idempotency_key="durable-key"
        )
        assert result.source_query_status == "succeeded"
        assert result.memory_capture_status == ("outcome_unknown" if unknown else "failed")
        assert result.memory is None and result.error == error.error

    asyncio.run(scenario())
    adapter.client.observe.assert_awaited_once()
    assert [call[0] for call in adapter.client.mock_calls] == ["observe"]


def test_capture_does_not_disguise_programming_errors_or_cancellation(adapter):
    async def scenario():
        for error in (RuntimeError("caller error"), asyncio.CancelledError()):
            adapter.client.observe.side_effect = error
            with pytest.raises(type(error)) as caught:
                await adapter.memory.capture(
                    snapshot(), consent_reference="synthetic-consent", idempotency_key="key"
                )
            assert caught.value is error

    asyncio.run(scenario())
    assert adapter.client.observe.await_count == 2


def test_capture_outcome_cannot_claim_storage_and_failure_at_once():
    result = ObserveResult(memory_id=uuid4(), revision=1)
    error = failure("not_found", status=404).error
    unknown = failure("native_api_unavailable", retryable=True, unknown=True).error
    for status, memory, detail in (
        ("stored", None, None),
        ("stored", result, error),
        ("failed", result, error),
        ("failed", None, None),
        ("failed", None, unknown),
        ("outcome_unknown", None, error),
        ("outcome_unknown", result, unknown),
        ("outcome_unknown", None, None),
    ):
        with pytest.raises(ValidationError):
            SourceCaptureOutcome(
                source_query_status="succeeded",
                memory_capture_status=status,
                memory=memory,
                error=detail,
            )


def test_read_parses_only_episode_envelope_and_retains_refresh_requirement(adapter):
    memory_id, value = uuid4(), envelope(adapter.binding)
    adapter.client.explain.return_value = explained(memory_id, content(value))

    async def scenario():
        restored = await adapter.memory.read_snapshot(memory_id)
        assert isinstance(restored, SnapshotEnvelope) and restored == value
        assert restored.snapshot.requires_refresh is True
        assert restored.snapshot.source_authority == "external_observation"

    asyncio.run(scenario())
    adapter.client.explain.assert_awaited_once_with(Explain(memory_id=memory_id))


def test_read_invalid_envelopes_have_one_constant_private_content_free_error(adapter):
    memory_id = uuid4()
    valid = envelope(adapter.binding).model_dump(mode="json")
    invalid = [
        "PRIVATE_NON_JSON",
        json.dumps({**valid, "format": "PRIVATE_UNSUPPORTED_VERSION"}),
        json.dumps({**valid, "unrecognized": "PRIVATE_EXTRA"}),
        json.dumps({**valid, "snapshot": {**valid["snapshot"], "result_digest": "0" * 64}}),
        json.dumps(
            {**valid, "snapshot": {**valid["snapshot"], "snapshot_text": "PRIVATE_CHANGED_TEXT"}}
        ),
        json.dumps(
            {**valid, "snapshot": {**valid["snapshot"], "requires_refresh": False}}
        ),
    ]
    for field in ("scope_id", "source_system", "dataset_id", "source_subject"):
        replacement = str(uuid4()) if field == "scope_id" else "PRIVATE_OTHER_BINDING"
        invalid.append(json.dumps({**valid, "source": {**valid["source"], field: replacement}}))

    async def scenario():
        messages = []
        for text in invalid:
            adapter.client.explain.return_value = explained(memory_id, text)
            with pytest.raises(ValueError) as caught:
                await adapter.memory.read_snapshot(memory_id)
            messages.append(str(caught.value))
        assert len(set(messages)) == 1 and messages[0]
        assert "PRIVATE" not in messages[0]
        assert str(adapter.binding.scope_id) not in messages[0]

    asyncio.run(scenario())
    assert adapter.client.explain.await_count == len(invalid)


def test_read_rejects_non_episode_response_without_trying_other_endpoints(adapter):
    adapter.client.explain.return_value = SimpleNamespace(
        type="assertion", assertion={"value": "PRIVATE_ASSERTION"}
    )

    async def scenario():
        with pytest.raises(ValueError) as caught:
            await adapter.memory.read_snapshot(uuid4())
        assert "PRIVATE" not in str(caught.value)

    asyncio.run(scenario())
    assert [call[0] for call in adapter.client.mock_calls] == ["explain"]


def test_read_rejects_episode_returned_under_the_wrong_memory_id(adapter):
    adapter.client.explain.return_value = explained(uuid4(), content(envelope(adapter.binding)))

    async def scenario():
        with pytest.raises(ValueError) as caught:
            await adapter.memory.read_snapshot(uuid4())
        assert "PRIVATE" not in str(caught.value)

    asyncio.run(scenario())
    adapter.client.explain.assert_awaited_once()


def test_read_checks_native_revision_and_observation_instant_without_leaking(adapter):
    memory_id, value = uuid4(), envelope(adapter.binding)
    original = explained(memory_id, content(value))
    equivalent = original.model_copy(
        update={
            "source": original.source.model_copy(
                update={"occurred_at": datetime.fromisoformat("2026-09-01T09:00:00+09:00")}
            )
        }
    )
    mismatched = original.model_copy(
        update={
            "source": original.source.model_copy(
                update={"occurred_at": original.source.occurred_at + timedelta(seconds=1)}
            )
        }
    )

    async def scenario():
        adapter.client.explain.return_value = equivalent
        assert await adapter.memory.read_snapshot(memory_id) == value
        messages = []
        for result in (
            explained(memory_id, "PRIVATE_MALFORMED_ENVELOPE"),
            mismatched,
            original.model_copy(update={"revision": 2}),
        ):
            adapter.client.explain.return_value = result
            with pytest.raises(ValueError) as caught:
                await adapter.memory.read_snapshot(memory_id)
            messages.append(str(caught.value))
        assert len(set(messages)) == 1 and messages[0]
        assert "PRIVATE" not in messages[0]

    asyncio.run(scenario())
    assert adapter.client.explain.await_count == 4


def test_read_propagates_real_sdk_denial_unchanged_without_retry(adapter):
    error = failure("not_found", status=404, request_id=uuid4())
    adapter.client.explain.side_effect = error

    async def scenario():
        with pytest.raises(MemoryClientError) as caught:
            await adapter.memory.read_snapshot(uuid4())
        assert caught.value is error

    asyncio.run(scenario())
    adapter.client.explain.assert_awaited_once()


@pytest.fixture
def source_access(env):
    reader, subject, task_scope = uuid4(), str(uuid4()), uuid4()
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(env.tenants[0]),)
        )
        conn.execute("SELECT id FROM memory.tenant WHERE id=%s FOR UPDATE", (env.tenants[0],))
        conn.execute(
            "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
            (env.tenants[0], reader, subject),
        )
        conn.execute(
            "INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)",
            (env.tenants[0], task_scope),
        )
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write','delete'])""",
            (env.tenants[0], task_scope, reader),
        )
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=access_epoch+1 WHERE id=%s", (env.tenants[0],)
        )
    return SimpleNamespace(
        reader=reader,
        token=env.token(sub=subject),
        task_scope=task_scope,
        binding=binding(env.scopes[0]),
    )


@pytest.fixture
def native_transport(env, monkeypatch):
    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url,
            transport=httpx.ASGITransport(app=env.client.app),
            headers={"Authorization": f"Bearer {settings.api_token}"},
            follow_redirects=False,
            trust_env=False,
        )

    monkeypatch.setattr(NativeSettings, "client", client)


def reader_access(env, access, operation="get", **changes):
    with scope_access(
        env.admin_url,
        ScopeAccessRequest(
            operation=operation,
            tenant_id=env.tenants[0],
            scope_id=access.binding.scope_id,
            principal_id=access.reader,
            **changes,
        ),
    ) as result:
        return result


def grant_reader(env, access, seconds=60):
    before = reader_access(env, access)
    return reader_access(
        env,
        access,
        "set",
        expected_access_epoch=before.access_epoch,
        permissions=("read",),
        expires_at=before.evaluated_at + timedelta(seconds=seconds),
    )


async def capture_bundle(maintenance, source):
    result = await ExternalSourceMemory(maintenance, binding=source).capture(
        snapshot(), consent_reference="synthetic-consent", idempotency_key="source"
    )
    assert result.memory_capture_status == "stored" and result.memory is not None
    derived = await maintenance.remember(
        Remember(
            scope_id=source.scope_id,
            subject="ACME",
            predicate="contract_tier",
            value="Gold",
            explicit_intent=True,
            evidence=[Evidence(memory_id=result.memory.memory_id, quote="Gold")],
        ),
        idempotency_key="derived",
    )
    checkpoint_request = CreateCheckpoint(
        scope_id=source.scope_id,
        run_id=uuid4(),
        branch_id=uuid4(),
        expected_head=None,
        harness_id=HARNESS_ID,
        harness_version="1",
        event_watermark=1,
        state=CheckpointState(goal="PRIVATE_CHECKPOINT: verify source again before acting"),
        memory_refs=[MemoryReference(memory_id=derived.memory_id, revision=derived.revision)],
    )
    checkpoint = await maintenance.create_checkpoint(
        checkpoint_request, idempotency_key="checkpoint"
    )
    return SimpleNamespace(
        source=result.memory.memory_id,
        derived=derived.memory_id,
        checkpoint=checkpoint.checkpoint_id,
        branch=CheckpointBranch(
            scope_id=source.scope_id,
            run_id=checkpoint_request.run_id,
            branch_id=checkpoint_request.branch_id,
        ),
    )


async def denied(awaitable, *, code="not_found", status=404):
    with pytest.raises(MemoryClientError) as caught:
        await awaitable
    assert caught.value.error.code == code
    assert caught.value.error.native_status == status
    assert "PRIVATE" not in str(caught.value)
    assert "PRIVATE" not in caught.value.error.model_dump_json()


async def assert_source_hidden(client, source, saved, *, purged=False):
    await denied(ExternalSourceMemory(client, binding=source).read_snapshot(saved.source))
    for memory_id in (saved.source, saved.derived):
        await denied(client.explain(Explain(memory_id=memory_id)))
        await denied(client.embedding_input(Explain(memory_id=memory_id)))
    await denied(client.get_assertion_history(AssertionHistory(memory_id=saved.derived)))
    await denied(client.get_checkpoint(saved.checkpoint))
    await denied(
        client.get_checkpoint_head(saved.branch),
        code="checkpoint_invalidated" if purged else "not_found",
        status=409 if purged else 404,
    )
    await denied(
        client.restore_checkpoint(
            RestoreCheckpoint(
                checkpoint_id=saved.checkpoint,
                target_branch_id=uuid4(),
                harness_id=HARNESS_ID,
                harness_version="1",
            ),
            idempotency_key=str(uuid4()),
        )
    )
    recalled = await client.recall(Recall(scope_ids=[source.scope_id], purpose="lease-check"))
    assert recalled.items == [] and recalled.context_pack.text == ""
    assert "PRIVATE" not in recalled.model_dump_json()
    episodes = await client.query_episodes(QueryEpisodes(scope_ids=[source.scope_id]))
    assert episodes.episodes == [] and "PRIVATE" not in episodes.model_dump_json()


async def assert_own_task_usable(client, task_scope):
    observed = await client.observe(
        Observe(
            scope_id=task_scope,
            source_namespace="own-task",
            source_event_id=str(uuid4()),
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            content="Unrelated task remains usable",
            consent_reference="synthetic-consent",
        ),
        idempotency_key=str(uuid4()),
    )
    assert (await client.explain(Explain(memory_id=observed.memory_id))).memory_id == (
        observed.memory_id
    )
    checkpoint = await client.create_checkpoint(
        CreateCheckpoint(
            scope_id=task_scope,
            run_id=uuid4(),
            branch_id=uuid4(),
            expected_head=None,
            harness_id=HARNESS_ID,
            harness_version="1",
            event_watermark=1,
            state=CheckpointState(goal="Continue unrelated own task"),
            memory_refs=[MemoryReference(memory_id=observed.memory_id)],
        ),
        idempotency_key=str(uuid4()),
    )
    assert (await client.get_checkpoint(checkpoint.checkpoint_id)).state.goal == (
        "Continue unrelated own task"
    )
    return observed.memory_id


async def wait_for_database_expiry(env, expires_at):
    deadline = time.monotonic() + 15
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        while time.monotonic() < deadline:
            now = conn.execute("SELECT statement_timestamp()").fetchone()[0]
            if now >= expires_at:
                return
            await asyncio.sleep(min(0.2, (expires_at - now).total_seconds() + 0.01))
    pytest.fail("Database clock did not reach the configured source lease expiry")


def assert_no_automatic_processing(env):
    with psycopg.connect(env.admin_url) as conn:
        for table in ("memory_ops.job", "memory.episode_embedding"):
            assert conn.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (env.tenants[0],)
            ).fetchone()[0] == 0


@pytest.mark.integration
def test_http_capture_read_exact_retry_and_changed_query_payload_conflict(env, api_process):
    source = binding(env.scopes[0])

    async def scenario(url):
        async with AsyncMemoryClient(url, env.token()) as client:
            memory = ExternalSourceMemory(client, binding=source)
            value = snapshot()
            first = await memory.capture(
                value, consent_reference="synthetic-consent", idempotency_key="durable-key"
            )
            assert first.memory_capture_status == "stored" and first.memory is not None
            assert first.memory.synthesis_job_id is None
            assert first.memory.embedding_job_id is None
            assert await memory.read_snapshot(first.memory.memory_id) == envelope(source, value)
            for key in ("durable-key", "fresh-key"):
                assert await memory.capture(
                    value, consent_reference="synthetic-consent", idempotency_key=key
                ) == first
            changed = await memory.capture(
                snapshot(snapshot_text="Changed Gold PRIVATE_SOURCE_RESULT"),
                consent_reference="synthetic-consent",
                idempotency_key="changed-result",
            )
            assert changed.source_query_status == "succeeded"
            assert changed.memory_capture_status == "failed" and changed.memory is None
            assert changed.error.code == "source_event_conflict"
            assert changed.error.native_status == 409 and not changed.error.outcome_unknown
            assert await memory.read_snapshot(first.memory.memory_id) == envelope(source, value)
            newer = await memory.capture(
                snapshot(query_id="durable-query-43", snapshot_text="Fresh result"),
                consent_reference="synthetic-consent",
                idempotency_key="new-query",
            )
            assert newer.memory_capture_status == "stored"
            assert newer.memory.memory_id != first.memory.memory_id
            page = await client.query_episodes(QueryEpisodes(scope_ids=[source.scope_id]))
            assert {item.memory_id for item in page.episodes} == {
                first.memory.memory_id,
                newer.memory.memory_id,
            }

    with api_process("external-source-http.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))
    assert_no_automatic_processing(env)


@pytest.mark.integration
def test_native_source_forget_purges_assertion_and_checkpoint_closure(
    env, source_access, native_transport
):
    access = source_access
    grant_reader(env, access)

    async def scenario():
        async with (
            AsyncMemoryClient("http://localhost", env.token()) as maintenance,
            AsyncMemoryClient("http://localhost", access.token) as reader,
        ):
            saved = await capture_bundle(maintenance, access.binding)
            assert await ExternalSourceMemory(reader, binding=access.binding).read_snapshot(
                saved.source
            ) == envelope(access.binding)
            derived = await reader.explain(Explain(memory_id=saved.derived))
            assert derived.evidence[0].memory_id == saved.source
            assert (await reader.get_checkpoint(saved.checkpoint)).memory_refs == [
                MemoryReference(memory_id=saved.derived)
            ]
            await denied(
                reader.forget(
                    Forget(memory_ids=[saved.source], reason="Reader cannot perform maintenance"),
                    idempotency_key="reader-forget",
                )
            )
            deleted = await maintenance.forget(
                Forget(memory_ids=[saved.source], reason="Explicit external source removal"),
                idempotency_key="maintenance-forget",
            )
            assert deleted.object_count == 3
            await assert_source_hidden(reader, access.binding, saved, purged=True)
            await assert_source_hidden(maintenance, access.binding, saved, purged=True)
            for key in ("source", "retry-after-purge"):
                replay = await ExternalSourceMemory(maintenance, binding=access.binding).capture(
                    snapshot(), consent_reference="synthetic-consent", idempotency_key=key
                )
                assert replay.memory_capture_status == "failed" and replay.memory is None
                assert replay.error.code == "not_found" and replay.error.native_status == 404
                assert not replay.error.outcome_unknown
            with psycopg.connect(env.admin_url) as conn:
                targets = {saved.source, saved.derived, saved.checkpoint}
                retained = conn.execute(
                    """SELECT o.id FROM memory.object o
                       JOIN memory_ops.object_tombstone t
                         ON t.tenant_id=o.tenant_id AND t.object_id=o.id
                       WHERE o.tenant_id=%s AND o.id=ANY(%s)""",
                    (env.tenants[0], list(targets)),
                ).fetchall()
                assert {row[0] for row in retained} == targets
                assert conn.execute(
                    """SELECT
                       (SELECT count(*) FROM memory.episode
                        WHERE tenant_id=%(tenant)s AND id=%(source)s),
                       (SELECT count(*) FROM memory.assertion
                        WHERE tenant_id=%(tenant)s AND id=%(derived)s),
                       (SELECT count(*) FROM memory.checkpoint
                        WHERE tenant_id=%(tenant)s AND id=%(checkpoint)s)""",
                    {
                        "tenant": env.tenants[0],
                        "source": saved.source,
                        "derived": saved.derived,
                        "checkpoint": saved.checkpoint,
                    },
                ).fetchone() == (0, 0, 0)

    asyncio.run(scenario())


@pytest.mark.integration
def test_configured_reader_lease_expiry_denies_native_paths_but_not_own_task(
    env, source_access, native_transport
):
    access = source_access

    async def scenario():
        async with (
            AsyncMemoryClient("http://localhost", env.token()) as maintenance,
            AsyncMemoryClient("http://localhost", access.token) as reader,
        ):
            saved = await capture_bundle(maintenance, access.binding)
            lease = grant_reader(env, access, seconds=5)
            assert lease.effective_permissions == ["read"]
            source_reader = ExternalSourceMemory(reader, binding=access.binding)
            assert (await source_reader.read_snapshot(saved.source)).snapshot.requires_refresh
            assert (await reader.get_checkpoint(saved.checkpoint)).checkpoint_id == saved.checkpoint
            await wait_for_database_expiry(env, lease.expires_at)
            expired = reader_access(env, access)
            assert expired.membership_exists and expired.effective_permissions == []
            assert expired.access_epoch == lease.access_epoch
            await assert_source_hidden(reader, access.binding, saved)
            own = await assert_own_task_usable(reader, access.task_scope)
            mixed = await reader.recall(
                Recall(
                    scope_ids=[access.binding.scope_id, access.task_scope],
                    purpose="unrelated-task",
                )
            )
            assert {item.memory_id for item in mixed.items} == {own}
            assert "PRIVATE" not in mixed.context_pack.text
            assert await ExternalSourceMemory(maintenance, binding=access.binding).read_snapshot(
                saved.source
            ) == envelope(access.binding)
            assert (await maintenance.get_checkpoint(saved.checkpoint)).checkpoint_id == (
                saved.checkpoint
            )
            assert reader_access(env, access).expires_at == lease.expires_at
            with pytest.raises(ScopeAccessError, match="access_epoch_conflict"):
                reader_access(
                    env,
                    access,
                    "set",
                    expected_access_epoch=lease.access_epoch - 1,
                    permissions=("read",),
                    expires_at=expired.evaluated_at + timedelta(seconds=60),
                )
            renewed = grant_reader(env, access)
            assert renewed.changed and renewed.access_epoch == lease.access_epoch + 1
            assert (await source_reader.read_snapshot(saved.source)).snapshot.requires_refresh
            assert (await reader.get_checkpoint(saved.checkpoint)).checkpoint_id == saved.checkpoint

    asyncio.run(scenario())


@pytest.mark.integration
def test_admin_cas_revocation_is_separate_from_maintenance_and_explicit_renewal(
    env, source_access, native_transport
):
    access = source_access
    lease = grant_reader(env, access)

    async def scenario():
        async with (
            AsyncMemoryClient("http://localhost", env.token()) as maintenance,
            AsyncMemoryClient("http://localhost", access.token) as reader,
        ):
            saved = await capture_bundle(maintenance, access.binding)
            source_reader = ExternalSourceMemory(reader, binding=access.binding)
            assert await source_reader.read_snapshot(saved.source) == envelope(access.binding)
            with pytest.raises(ScopeAccessError, match="access_epoch_conflict"):
                reader_access(
                    env, access, "revoke", expected_access_epoch=lease.access_epoch - 1
                )
            revoked = reader_access(
                env, access, "revoke", expected_access_epoch=lease.access_epoch
            )
            assert revoked.changed and revoked.access_epoch == lease.access_epoch + 1
            assert not revoked.membership_exists
            await assert_source_hidden(reader, access.binding, saved)
            await assert_own_task_usable(reader, access.task_scope)
            assert not reader_access(env, access).membership_exists
            assert await ExternalSourceMemory(maintenance, binding=access.binding).read_snapshot(
                saved.source
            ) == envelope(access.binding)
            renewed = grant_reader(env, access)
            assert renewed.access_epoch == revoked.access_epoch + 1
            assert await source_reader.read_snapshot(saved.source) == envelope(access.binding)
            await maintenance.forget(
                Forget(memory_ids=[saved.source], reason="Maintenance remains separate"),
                idempotency_key="maintenance-forget",
            )
            await denied(source_reader.read_snapshot(saved.source))

    asyncio.run(scenario())


@pytest.mark.integration
def test_http_lost_capture_response_requires_explicit_stable_retry(
    env, api_process, monkeypatch
):
    class LoseFirstObserveResponse(httpx.AsyncHTTPTransport):
        observe_calls = 0
        committed = None

        async def handle_async_request(self, request):
            result = await super().handle_async_request(request)
            if request.url.path == "/v1/observe":
                self.observe_calls += 1
                if self.observe_calls == 1:
                    await result.aread()
                    assert result.status_code == 201
                    self.committed = result.json()
                    await result.aclose()
                    raise httpx.ReadError("PRIVATE_SIMULATED_LOST_RESPONSE")
            return result

    transport = LoseFirstObserveResponse()

    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.api_token}"},
            follow_redirects=False,
            trust_env=False,
        )

    monkeypatch.setattr(NativeSettings, "client", client)

    async def scenario(url):
        async with AsyncMemoryClient(url, env.token()) as client:
            memory = ExternalSourceMemory(client, binding=binding(env.scopes[0]))
            first = await memory.capture(
                snapshot(), consent_reference="synthetic-consent", idempotency_key="durable-key"
            )
            assert first.source_query_status == "succeeded"
            assert first.memory_capture_status == "outcome_unknown" and first.memory is None
            assert first.error.code == "native_api_unavailable"
            assert first.error.retryable and first.error.outcome_unknown
            assert "PRIVATE" not in first.model_dump_json()
            assert transport.observe_calls == 1
            assert transport.committed is not None
            assert_no_automatic_processing(env)
            repeated = await memory.capture(
                snapshot(), consent_reference="synthetic-consent", idempotency_key="durable-key"
            )
            assert repeated.memory_capture_status == "stored"
            assert repeated.memory.memory_id == UUID(transport.committed["memory_id"])
            assert transport.observe_calls == 2
            page = await client.query_episodes(QueryEpisodes(scope_ids=[env.scopes[0]]))
            assert len(page.episodes) == 1

    with api_process("external-source-lost-response.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))


@pytest.mark.integration
def test_native_envelope_binding_is_caller_provenance_not_actual_scope_proof(
    env, source_access, native_transport
):
    access = source_access
    assert not reader_access(env, access).membership_exists

    async def scenario():
        async with (
            AsyncMemoryClient("http://localhost", env.token()) as maintenance,
            AsyncMemoryClient("http://localhost", access.token) as reader,
        ):
            saved = await capture_bundle(maintenance, access.binding)
            source_reader = ExternalSourceMemory(reader, binding=access.binding)
            await denied(source_reader.read_snapshot(saved.source))
            claimed = envelope(access.binding)
            copied = await reader.observe(
                Observe(
                    scope_id=access.task_scope,
                    source_namespace="caller-supplied-provenance",
                    source_event_id=str(uuid4()),
                    occurred_at=claimed.snapshot.observed_at,
                    content=content(claimed),
                    consent_reference="synthetic-consent",
                ),
                idempotency_key="caller-copy",
            )
            assert await source_reader.read_snapshot(copied.memory_id) == claimed
            await denied(source_reader.read_snapshot(saved.source))
            assert not reader_access(env, access).membership_exists

    asyncio.run(scenario())
