import asyncio
import os
import runpy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, create_autospec
from uuid import uuid4

import httpx
import psycopg
import pytest

pytest.importorskip("langgraph")

from langgraph.graph.state import CompiledStateGraph

from pg_agmemory.langgraph import (
    HARNESS_ID,
    HARNESS_VERSION,
    LangGraphMemory,
    build_turn_graph,
)
from pg_agmemory.models import (
    CheckpointEnvelope,
    CheckpointReceipt,
    CheckpointState,
    CreateCheckpoint,
    Forget,
    MemoryItem,
    MemoryReference,
    Observe,
    ObserveResult,
    PendingEffect,
    PlanToolEffect,
    Recall,
    RecallResult,
    RestoreCheckpoint,
    ToolEffectSummary,
    TransitionToolEffect,
)
from pg_agmemory.native_client import NativeSettings, failure
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


def observation(scope):
    return Observe(
        scope_id=scope,
        source_namespace="langgraph-pilot-tests",
        source_event_id=str(uuid4()),
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        content="ACME contract is Gold",
        consent_reference="synthetic-test",
    )


def checkpoint_state():
    return CheckpointState(
        goal="Prepare an approved plan, not an automatic tool execution",
        constraints=["Keep exact references"],
        completed_actions=["Read request"],
        decisions=["Require human approval"],
        unresolved_questions=["Who approves?"],
        next_actions=["Draft proposal"],
        pending_effects=[
            PendingEffect(
                operation_id=uuid4(), description="Await operator", status="planned"
            )
        ],
        pending_approvals=["Operator approval"],
        important_ids=[str(uuid4()), "ticket:ACME-123"],
        versions=["contract-v7", "policy-v2"],
        paths=["reports/proposal.json"],
        failed_actions=["Earlier request timed out"],
        in_progress_actions=["Prepare proposal"],
        blocked_actions=["Dispatch until approved"],
    )


def recall_result():
    return RecallResult.model_validate(
        {
            "items": [],
            "context_pack": {
                "format": "memory-context-v1",
                "text": "",
                "tokenizer_id": "utf8-bytes-v1",
                "budget_unit": "utf8_bytes",
                "byte_count": 0,
                "exact_token_count": False,
            },
            "coverage": {
                "retrieval_complete": True,
                "synthesis_pending": False,
                "graph_used": False,
                "truncated": False,
            },
            "consistency": {"access_epoch": 3, "deletion_epoch": 4},
            "search_profile": "simple-v1",
            "empty_reason": "not_found",
        }
    )


def recalled_item(reference):
    return MemoryItem(
        memory_id=reference.memory_id,
        revision=reference.revision,
        type="assertion",
        content="Synthetic recalled evidence",
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def harness():
    scope, run, branch = uuid4(), uuid4(), uuid4()
    client = create_autospec(AsyncMemoryClient, instance=True)
    client.observe.return_value = ObserveResult(memory_id=uuid4(), revision=1)
    client.recall.return_value = recall_result()
    client.create_checkpoint.return_value = CheckpointReceipt(
        checkpoint_id=uuid4(),
        run_id=run,
        branch_id=branch,
        sequence=1,
        parent_checkpoint=None,
        checksum="a" * 64,
    )
    memory = LangGraphMemory(client, scope_id=scope, run_id=run, branch_id=branch)
    return SimpleNamespace(client=client, memory=memory, scope=scope, run=run, branch=branch)


def envelope(harness, **changes):
    return CheckpointEnvelope(
        **{
            **harness.client.create_checkpoint.return_value.model_dump(),
            "scope_id": harness.scope,
            "harness_id": HARNESS_ID,
            "harness_version": HARNESS_VERSION,
            "state_schema_version": 1,
            "event_watermark": 17,
            "state": checkpoint_state(),
            "memory_refs": [MemoryReference(memory_id=uuid4(), revision=7)],
            "saved_access_epoch": 1,
            "saved_deletion_epoch": 2,
            "current_access_epoch": 3,
            "current_deletion_epoch": 4,
            "requires_reconciliation": [],
            "resume_allowed": True,
            **changes,
        }
    )


def turn_input(scope, **changes):
    return {
        "state": checkpoint_state(),
        "recall_request": Recall(scope_ids=[scope], purpose="plan next turn"),
        "expected_head": None,
        "event_watermark": 17,
        "memory_refs": [MemoryReference(memory_id=uuid4(), revision=7)],
        "idempotency_key": "persisted-turn-key",
        **changes,
    }


async def unchanged(state, recalled):
    return state


def test_pilot_identity_and_typed_observe_recall(harness):
    assert HARNESS_ID == "pg-agmemory-langgraph-pilot"
    assert HARNESS_VERSION == "1"
    observed = observation(harness.scope)
    query = Recall(scope_ids=[harness.scope], purpose="pilot")

    async def scenario():
        assert (
            await harness.memory.observe(observed, idempotency_key="durable-observation")
            is harness.client.observe.return_value
        )
        assert await harness.memory.recall(query) is harness.client.recall.return_value

    asyncio.run(scenario())
    harness.client.observe.assert_awaited_once_with(
        observed, idempotency_key="durable-observation"
    )
    harness.client.recall.assert_awaited_once_with(query)


@pytest.mark.parametrize("method", ["observe", "recall"])
def test_direct_calls_propagate_sdk_error_identity_without_retry(harness, method):
    error = failure("not_found", status=404, request_id=uuid4())
    getattr(harness.client, method).side_effect = error

    async def scenario():
        with pytest.raises(MemoryClientError) as caught:
            if method == "observe":
                await harness.memory.observe(observation(harness.scope), idempotency_key="source")
            else:
                await harness.memory.recall(Recall(scope_ids=[harness.scope], purpose="pilot"))
        assert caught.value is error

    asyncio.run(scenario())
    assert getattr(harness.client, method).await_count == 1


@pytest.mark.parametrize("field", ["scope_id", "run_id", "branch_id"])
def test_binding_rejects_untyped_authority_before_sdk(harness, field):
    ids = {"scope_id": harness.scope, "run_id": harness.run, "branch_id": harness.branch}
    ids[field] = "private-invalid-identity"
    with pytest.raises(ValueError) as caught:
        LangGraphMemory(harness.client, **ids)
    assert "private-invalid-identity" not in str(caught.value)
    assert not harness.client.mock_calls


def test_observe_rejects_foreign_mutated_and_constructed_scope_before_sdk(harness):
    foreign = uuid4()
    valid = observation(harness.scope)
    mutated = valid.model_copy(deep=True)
    mutated.scope_id = foreign
    constructed = Observe.model_construct(**{**valid.model_dump(), "scope_id": foreign})
    messages = []

    async def scenario():
        for request in [observation(foreign), mutated, constructed]:
            with pytest.raises(ValueError) as caught:
                await harness.memory.observe(request, idempotency_key="not-sent")
            messages.append(str(caught.value))

    asyncio.run(scenario())
    assert len(set(messages)) == 1
    assert str(foreign) not in messages[0]
    harness.client.observe.assert_not_awaited()


def test_recall_requires_exact_single_bound_scope_even_after_model_mutation(harness):
    messages = []

    async def scenario():
        for scopes in [
            [],
            [uuid4()],
            [harness.scope, uuid4()],
            [harness.scope, harness.scope],
        ]:
            for request in [
                Recall(scope_ids=[harness.scope], purpose="private-query").model_copy(
                    update={"scope_ids": scopes}
                ),
                Recall.model_construct(scope_ids=scopes, purpose="private-query"),
            ]:
                with pytest.raises(ValueError) as caught:
                    await harness.memory.recall(request)
                messages.append(str(caught.value))

    asyncio.run(scenario())
    assert len(set(messages)) == 1
    assert "private-query" not in messages[0]
    harness.client.recall.assert_not_awaited()


def test_checkpoint_preserves_full_typed_state_exact_refs_and_cas(harness):
    state = checkpoint_state()
    original = state.model_copy(deep=True)
    head = uuid4()
    refs = [MemoryReference(memory_id=uuid4(), revision=8)]

    async def scenario():
        result = await harness.memory.checkpoint(
            state,
            expected_head=head,
            event_watermark=9223372036854775807,
            memory_refs=refs,
            idempotency_key="durable-checkpoint",
        )
        assert result is harness.client.create_checkpoint.return_value

    asyncio.run(scenario())
    request = harness.client.create_checkpoint.call_args.args[0]
    assert isinstance(request, CreateCheckpoint)
    assert request == CreateCheckpoint(
        scope_id=harness.scope,
        run_id=harness.run,
        branch_id=harness.branch,
        expected_head=head,
        harness_id=HARNESS_ID,
        harness_version=HARNESS_VERSION,
        state_schema_version=1,
        event_watermark=9223372036854775807,
        state=original,
        memory_refs=refs,
    )
    assert request.state == original == state
    assert request.memory_refs[0].revision == 8
    harness.client.create_checkpoint.assert_awaited_once_with(
        request, idempotency_key="durable-checkpoint"
    )


@pytest.mark.parametrize("invalid", ["state", "constructed", "effect", "reference", "watermark"])
def test_checkpoint_revalidates_mutated_nested_contracts_locally(harness, invalid):
    state = checkpoint_state()
    refs = [MemoryReference(memory_id=uuid4())]
    watermark = 17
    if invalid == "state":
        state.goal = ""
    elif invalid == "constructed":
        state = CheckpointState.model_construct(goal="")
    elif invalid == "effect":
        state.pending_effects[0].status = "private-invalid-status"
    elif invalid == "reference":
        refs[0].revision = 0
    else:
        watermark = True

    async def scenario():
        with pytest.raises(MemoryClientError) as caught:
            await harness.memory.checkpoint(
                state,
                expected_head=None,
                event_watermark=watermark,
                memory_refs=refs,
                idempotency_key="not-sent",
            )
        assert caught.value.error.code == "invalid_request"
        assert "private-invalid-status" not in str(caught.value)
        assert "input_value" not in str(caught.value)

    asyncio.run(scenario())
    harness.client.create_checkpoint.assert_not_awaited()


@pytest.mark.parametrize(
    "field",
    [
        "checkpoint_id",
        "scope_id",
        "run_id",
        "harness_id",
        "harness_version",
        "state_schema_version",
    ],
)
def test_restore_checks_exact_source_compatibility_before_mutating(harness, field):
    source = envelope(harness)
    checkpoint_id = source.checkpoint_id
    replacement = (
        uuid4()
        if field in {"checkpoint_id", "scope_id", "run_id"}
        else 2 if field == "state_schema_version" else "private-incompatible-harness"
    )
    harness.client.get_checkpoint.return_value = source.model_copy(update={field: replacement})

    async def scenario():
        with pytest.raises(ValueError) as caught:
            await harness.memory.restore(
                checkpoint_id, target_branch_id=uuid4(), idempotency_key="not-sent"
            )
        assert str(replacement) not in str(caught.value)

    asyncio.run(scenario())
    harness.client.get_checkpoint.assert_awaited_once_with(checkpoint_id)
    harness.client.restore_checkpoint.assert_not_awaited()


@pytest.mark.parametrize("blocked", ["unknown", "dispatched", "untracked"])
def test_restore_surfaces_full_blocked_envelope_without_execution_or_rebinding(harness, blocked):
    operation, target = uuid4(), uuid4()
    source = envelope(harness)
    restored = envelope(
        harness,
        checkpoint_id=uuid4(),
        branch_id=target,
        parent_checkpoint=source.checkpoint_id,
        state=source.state,
        memory_refs=source.memory_refs,
        resume_allowed=False,
        requires_reconciliation=[operation],
        untracked_effects=[operation] if blocked == "untracked" else [],
        tool_effects=(
            []
            if blocked == "untracked"
            else [
                ToolEffectSummary(
                    memory_id=uuid4(), operation_id=operation, revision=3, status=blocked
                )
            ]
        ),
    )
    source.resume_allowed = False
    harness.client.get_checkpoint.return_value = source
    harness.client.restore_checkpoint.return_value = restored
    planner = AsyncMock(side_effect=AssertionError("restore must not run the planner"))
    build_turn_graph(harness.memory, planner)

    async def scenario():
        result = await harness.memory.restore(
            source.checkpoint_id, target_branch_id=target, idempotency_key="durable-fork"
        )
        assert result is restored
        assert result.resume_allowed is False and result.automatic_reexecution is False
        assert result.requires_reconciliation == [operation]
        assert result.memory_refs == source.memory_refs
        assert result.state == source.state
        assert harness.client.mock_calls == [
            call.get_checkpoint(source.checkpoint_id),
            call.restore_checkpoint(
                RestoreCheckpoint(
                    checkpoint_id=source.checkpoint_id,
                    target_branch_id=target,
                    harness_id=HARNESS_ID,
                    harness_version=HARNESS_VERSION,
                    state_schema_version=1,
                ),
                idempotency_key="durable-fork",
            ),
        ]
        await harness.memory.checkpoint(
            CheckpointState(goal="Explicit caller-controlled continuation"),
            expected_head=source.checkpoint_id,
            event_watermark=18,
            memory_refs=[],
            idempotency_key="explicit-original-branch",
        )

    asyncio.run(scenario())
    assert harness.client.create_checkpoint.call_args.args[0].branch_id == harness.branch
    planner.assert_not_awaited()


@pytest.mark.parametrize("stage", ["get_checkpoint", "restore_checkpoint"])
def test_restore_propagates_sdk_failure_unchanged_without_retry(harness, stage):
    source = envelope(harness)
    harness.client.get_checkpoint.return_value = source
    error = failure(
        "transport_error", retryable=True, unknown=stage == "restore_checkpoint"
    )
    getattr(harness.client, stage).side_effect = error

    async def scenario():
        with pytest.raises(MemoryClientError) as caught:
            await harness.memory.restore(
                source.checkpoint_id, target_branch_id=uuid4(), idempotency_key="durable-fork"
            )
        assert caught.value is error

    asyncio.run(scenario())
    harness.client.get_checkpoint.assert_awaited_once_with(source.checkpoint_id)
    assert harness.client.restore_checkpoint.await_count == (stage == "restore_checkpoint")


@pytest.mark.parametrize(
    "field", ["scope_id", "run_id", "branch_id", "harness_id", "harness_version"]
)
def test_restore_rejects_reply_outside_target_binding_without_retry(harness, field):
    source, target = envelope(harness), uuid4()
    restored = envelope(
        harness, checkpoint_id=uuid4(), branch_id=target, parent_checkpoint=source.checkpoint_id
    )
    replacement = uuid4() if field.endswith("_id") and field != "harness_id" else "private-harness"
    harness.client.get_checkpoint.return_value = source
    harness.client.restore_checkpoint.return_value = restored.model_copy(
        update={field: replacement}
    )

    async def scenario():
        with pytest.raises(ValueError) as caught:
            await harness.memory.restore(
                source.checkpoint_id, target_branch_id=target, idempotency_key="fork"
            )
        assert str(replacement) not in str(caught.value)

    asyncio.run(scenario())
    assert harness.client.get_checkpoint.await_count == 1
    assert harness.client.restore_checkpoint.await_count == 1
    harness.client.create_checkpoint.assert_not_awaited()


def test_compiled_graph_runs_recall_plan_checkpoint_and_preserves_outputs(harness):
    inputs = turn_input(harness.scope)
    original = inputs["state"].model_copy(deep=True)
    events = []

    async def recall(request):
        events.append("recall")
        return harness.client.recall.return_value

    async def planner(state, recalled):
        events.append("plan")
        assert isinstance(state, CheckpointState)
        assert isinstance(recalled, RecallResult)
        assert state == original
        state.completed_actions.append("Drafted proposal")
        return state

    async def checkpoint(request, *, idempotency_key):
        events.append("checkpoint")
        assert request.state.completed_actions == [*original.completed_actions, "Drafted proposal"]
        return harness.client.create_checkpoint.return_value

    harness.client.recall.side_effect = recall
    harness.client.create_checkpoint.side_effect = checkpoint
    graph = build_turn_graph(harness.memory, planner)
    assert isinstance(graph, CompiledStateGraph)
    assert {(edge.source, edge.target) for edge in graph.get_graph().edges} == {
        ("__start__", "recall"),
        ("recall", "plan"),
        ("plan", "checkpoint"),
        ("checkpoint", "__end__"),
    }
    output = asyncio.run(graph.ainvoke(inputs))
    assert events == ["recall", "plan", "checkpoint"]
    assert set(output) == set(inputs) | {"recall_result", "checkpoint_receipt"}
    assert output["recall_result"] == harness.client.recall.return_value
    assert output["checkpoint_receipt"] == harness.client.create_checkpoint.return_value
    assert output["state"].important_ids == original.important_ids
    assert output["state"].versions == original.versions
    for field in set(inputs) - {"state"}:
        assert output[field] == inputs[field]
    assert inputs["state"] == original
    harness.client.observe.assert_not_awaited()


@pytest.mark.parametrize("stage", ["recall", "plan", "checkpoint"])
def test_compiled_graph_propagates_errors_and_never_retries_later_nodes(harness, stage):
    error = (
        RuntimeError("planner stopped")
        if stage == "plan"
        else failure("transport_error", retryable=True, unknown=stage == "checkpoint")
    )
    planner = AsyncMock(side_effect=error if stage == "plan" else unchanged)
    if stage != "plan":
        method = "create_checkpoint" if stage == "checkpoint" else stage
        getattr(harness.client, method).side_effect = error
    graph = build_turn_graph(harness.memory, planner)
    with pytest.raises(type(error)) as caught:
        asyncio.run(graph.ainvoke(turn_input(harness.scope)))
    assert caught.value is error
    assert harness.client.recall.await_count == 1
    assert planner.await_count == (stage != "recall")
    assert harness.client.create_checkpoint.await_count == (stage == "checkpoint")
    harness.client.get_checkpoint.assert_not_awaited()
    harness.client.restore_checkpoint.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["mapping", "constructed", "mutated"])
def test_compiled_graph_rejects_invalid_planner_state_before_checkpoint(harness, invalid):
    async def planner(state, recalled):
        if invalid == "mapping":
            return {"goal": "Not typed"}
        if invalid == "constructed":
            return CheckpointState.model_construct(goal="")
        state.pending_effects[0].operation_id = "private-invalid-id"
        return state

    graph = build_turn_graph(harness.memory, planner)
    with pytest.raises(MemoryClientError) as caught:
        asyncio.run(graph.ainvoke(turn_input(harness.scope)))
    assert caught.value.error.code == "invalid_request"
    assert "private-invalid-id" not in str(caught.value)
    harness.client.create_checkpoint.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["scope", "state", "reference"])
def test_compiled_graph_revalidates_input_before_recall_or_planning(harness, invalid):
    inputs = turn_input(harness.scope)
    if invalid == "scope":
        inputs["recall_request"].scope_ids.append(uuid4())
    elif invalid == "state":
        inputs["state"] = CheckpointState.model_construct(goal="")
    else:
        inputs["memory_refs"][0].revision = 0
    planner = AsyncMock(side_effect=unchanged)
    with pytest.raises(ValueError if invalid == "scope" else MemoryClientError) as caught:
        asyncio.run(build_turn_graph(harness.memory, planner).ainvoke(inputs))
    if isinstance(caught.value, MemoryClientError):
        assert caught.value.error.code == "invalid_request"
    harness.client.recall.assert_not_awaited()
    planner.assert_not_awaited()
    harness.client.create_checkpoint.assert_not_awaited()


@pytest.mark.parametrize("key", ["", "unsafe key", "k" * 257])
def test_compiled_graph_rejects_invalid_key_before_recall_or_planner(harness, key):
    planner = AsyncMock(side_effect=unchanged)
    with pytest.raises(MemoryClientError) as caught:
        asyncio.run(
            build_turn_graph(harness.memory, planner).ainvoke(
                turn_input(harness.scope, idempotency_key=key)
            )
        )
    assert caught.value.error.code == "invalid_request"
    harness.client.recall.assert_not_awaited()
    planner.assert_not_awaited()
    harness.client.create_checkpoint.assert_not_awaited()


def test_graph_unions_declared_and_recalled_exact_references_in_stable_order(harness):
    first = MemoryReference(memory_id=uuid4(), revision=2)
    second = MemoryReference(memory_id=uuid4(), revision=5)
    recalled = MemoryReference(memory_id=uuid4(), revision=3)
    another_revision = MemoryReference(memory_id=first.memory_id, revision=3)
    declared = [first, second]
    expected = [first, second, recalled, another_revision]
    harness.client.recall.return_value.items = [
        recalled_item(ref) for ref in [first, recalled, recalled, another_revision]
    ]
    harness.client.recall.return_value.empty_reason = None

    async def planner(state, result):
        result.items[1].memory_id = uuid4()
        result.items[1].revision = 999
        return state

    output = asyncio.run(
        build_turn_graph(harness.memory, planner).ainvoke(
            turn_input(harness.scope, memory_refs=declared)
        )
    )
    saved = harness.client.create_checkpoint.call_args.args[0]
    assert saved.memory_refs == expected
    assert output["memory_refs"] == expected
    assert declared == [first, second]
    assert saved.memory_refs is not declared


@pytest.mark.parametrize("count", [100, 101])
def test_graph_checks_unioned_reference_limit_before_planner(harness, count):
    declared = [MemoryReference(memory_id=uuid4()) for _ in range(99)]
    new_refs = [MemoryReference(memory_id=uuid4()) for _ in range(count - len(declared))]
    harness.client.recall.return_value.items = [
        recalled_item(ref) for ref in [declared[0], *new_refs]
    ]
    harness.client.recall.return_value.empty_reason = None
    planner = AsyncMock(side_effect=unchanged)
    graph = build_turn_graph(harness.memory, planner)
    inputs = turn_input(harness.scope, memory_refs=declared)
    if count == 100:
        result = asyncio.run(graph.ainvoke(inputs))
        assert result["memory_refs"] == [*declared, *new_refs]
        assert len(harness.client.create_checkpoint.call_args.args[0].memory_refs) == 100
        planner.assert_awaited_once()
    else:
        with pytest.raises(MemoryClientError) as caught:
            asyncio.run(graph.ainvoke(inputs))
        assert caught.value.error.code == "invalid_request"
        planner.assert_not_awaited()
        harness.client.create_checkpoint.assert_not_awaited()
    harness.client.recall.assert_awaited_once()


def test_planner_cannot_mutate_cas_scope_refs_or_original_state_by_alias(harness):
    head = uuid4()
    inputs = turn_input(harness.scope, expected_head=head)
    original_state = inputs["state"].model_copy(deep=True)
    original_query = inputs["recall_request"].model_copy(deep=True)
    original_refs = [ref.model_copy(deep=True) for ref in inputs["memory_refs"]]
    original_recalled = harness.client.recall.return_value.model_copy(deep=True)

    async def planner(state, recalled):
        assert isinstance(state, CheckpointState)
        state.decisions.append("Plan changed state only")
        recalled.context_pack.text = "Planner-local modification"
        inputs["memory_refs"][0].memory_id = uuid4()
        inputs["memory_refs"][0].revision = 3
        inputs["memory_refs"].append(MemoryReference(memory_id=uuid4()))
        inputs["recall_request"].scope_ids[:] = [uuid4()]
        inputs["expected_head"] = uuid4()
        inputs["event_watermark"] = 999
        inputs["idempotency_key"] = "planner-overwrite"
        return state

    output = asyncio.run(build_turn_graph(harness.memory, planner).ainvoke(inputs))
    request = harness.client.create_checkpoint.call_args.args[0]
    assert (request.scope_id, request.run_id, request.branch_id) == (
        harness.scope,
        harness.run,
        harness.branch,
    )
    assert request.expected_head == head
    assert request.event_watermark == 17
    assert request.memory_refs == original_refs
    assert output["recall_request"] == original_query
    assert output["recall_result"] == original_recalled
    assert harness.client.recall.return_value == original_recalled
    assert inputs["state"] == original_state
    assert request.state.decisions == [*original_state.decisions, "Plan changed state only"]
    assert harness.client.create_checkpoint.call_args.kwargs == {
        "idempotency_key": "persisted-turn-key"
    }


def test_synthetic_example_requires_consent_then_runs_two_boundaries(harness, monkeypatch, capsys):
    for name, value in {
        "PGAG_API_URL": "https://memory.test",
        "PGAG_API_TOKEN": "fixed.identity.signature",
        "PGAG_SCOPE_ID": str(harness.scope),
        "PGAG_RUN_ID": str(harness.run),
        "PGAG_BRANCH_ID": str(harness.branch),
    }.items():
        monkeypatch.setenv(name, value)
    tracing = [
        "LANGCHAIN_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_TRACING",
        "LANGSMITH_TRACING_V2",
    ]
    for name in tracing:
        monkeypatch.setenv(name, "true")
    monkeypatch.delenv("PGAG_SYNTHETIC_CONSENT", raising=False)
    harness.client.__aenter__.return_value = harness.client
    monkeypatch.setattr("pg_agmemory.sdk.AsyncMemoryClient", lambda *args: harness.client)
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples" / "langgraph_memory.py")
    )
    assert all(os.environ[name] == "false" for name in tracing)
    with pytest.raises(ValueError, match="PGAG_SYNTHETIC_CONSENT"):
        asyncio.run(example["main"]())
    assert not harness.client.mock_calls

    first = harness.client.create_checkpoint.return_value
    second = first.model_copy(
        update={"checkpoint_id": uuid4(), "sequence": 2, "parent_checkpoint": first.checkpoint_id}
    )
    harness.client.create_checkpoint.side_effect = [first, second]
    source_id = harness.client.observe.return_value.memory_id
    harness.client.recall.return_value.items = [
        MemoryItem(
            memory_id=source_id,
            type="episode",
            content="Synthetic project Cedar",
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    ]
    harness.client.recall.return_value.empty_reason = None
    monkeypatch.setenv("PGAG_SYNTHETIC_CONSENT", "yes")
    asyncio.run(example["main"]())

    prefix = f"langgraph-pilot:{harness.scope}:{harness.run}:{harness.branch}"
    harness.client.observe.assert_awaited_once()
    assert harness.client.observe.call_args.kwargs == {"idempotency_key": f"{prefix}:observe"}
    assert harness.client.observe.call_args.args[0].occurred_at == datetime(
        2026, 1, 1, tzinfo=UTC
    )
    assert harness.client.recall.await_count == 2
    saved = harness.client.create_checkpoint.call_args_list
    assert len(saved) == 2
    assert saved[0].args[0].expected_head is None
    assert saved[1].args[0].expected_head == first.checkpoint_id
    assert saved[0].args[0].state.completed_actions == ["Review synthetic context"]
    assert saved[1].args[0].state.completed_actions == [
        "Review synthetic context", "Finish synthetic review"
    ]
    assert saved[1].args[0].state.next_actions == []
    for step, saved_call in enumerate(saved, 1):
        assert saved_call.kwargs == {"idempotency_key": f"{prefix}:checkpoint:{step}"}
        assert saved_call.args[0].memory_refs == [MemoryReference(memory_id=source_id)]
        assert saved_call.args[0].event_watermark == 1
    harness.client.__aexit__.assert_awaited_once()
    harness.client.restore_checkpoint.assert_not_awaited()
    harness.client.plan_tool_effect.assert_not_awaited()
    output = capsys.readouterr().out
    assert output.count("Completed safe boundary") == 2
    assert str(first.checkpoint_id) in output and str(second.checkpoint_id) in output
    assert "fixed.identity.signature" not in output


@pytest.fixture
def native_client(env, monkeypatch):
    calls = []

    async def record(request):
        calls.append((request.method, request.url.path))

    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url,
            transport=httpx.ASGITransport(app=env.client.app),
            headers={"Authorization": f"Bearer {settings.api_token}"},
            event_hooks={"request": [record]},
            follow_redirects=False,
            trust_env=False,
        )

    monkeypatch.setattr(NativeSettings, "client", client)
    return AsyncMemoryClient("http://localhost", env.token()), calls


@pytest.mark.integration
def test_native_graph_observe_recall_checkpoint_restore_and_head_conflict(env, native_client):
    client, calls = native_client
    run, branch, target = uuid4(), uuid4(), uuid4()

    async def scenario():
        async with client:
            memory = LangGraphMemory(client, scope_id=env.scopes[0], run_id=run, branch_id=branch)
            source = await memory.observe(observation(env.scopes[0]), idempotency_key="source")
            refs = [MemoryReference(memory_id=source.memory_id, revision=source.revision)]
            inputs = turn_input(
                env.scopes[0],
                state=CheckpointState(
                    goal="Draft only", important_ids=["ACME-123"], versions=["contract-v7"]
                ),
                recall_request=Recall(
                    scope_ids=[env.scopes[0]], purpose="pilot", required_memory_refs=refs
                ),
                memory_refs=refs,
            )
            planner = AsyncMock(side_effect=unchanged)
            graph = build_turn_graph(memory, planner)
            result = await graph.ainvoke(inputs)
            assert source.memory_id in {item.memory_id for item in result["recall_result"].items}
            receipt = result["checkpoint_receipt"]
            saved = await client.get_checkpoint(receipt.checkpoint_id)
            assert saved.state == inputs["state"]
            assert saved.memory_refs == refs
            assert saved.harness_id == HARNESS_ID and saved.state_schema_version == 1
            restored = await memory.restore(
                receipt.checkpoint_id, target_branch_id=target, idempotency_key="fork"
            )
            assert isinstance(restored, CheckpointEnvelope)
            assert restored.state == saved.state and restored.memory_refs == refs
            assert restored.branch_id == target and restored.run_id == run
            assert restored.parent_checkpoint == receipt.checkpoint_id
            assert restored.resume_allowed and not restored.automatic_reexecution
            assert planner.await_count == 1
            assert await memory.restore(
                receipt.checkpoint_id, target_branch_id=target, idempotency_key="fork"
            ) == restored
            with pytest.raises(MemoryClientError) as conflict:
                await graph.ainvoke({**inputs, "idempotency_key": "stale-head"})
            assert conflict.value.error.code == "checkpoint_head_conflict"
            assert planner.await_count == 2
            advanced = await graph.ainvoke(
                {**inputs, "expected_head": receipt.checkpoint_id, "idempotency_key": "advance"}
            )
            assert advanced["checkpoint_receipt"].branch_id == branch
            assert advanced["checkpoint_receipt"].parent_checkpoint == receipt.checkpoint_id
            assert advanced["checkpoint_receipt"].sequence == 2

    asyncio.run(scenario())
    assert calls.count(("POST", "/v1/checkpoints")) == 3


@pytest.mark.integration
def test_native_graph_recalled_source_deletion_blocks_restore_before_fork(env, native_client):
    client, calls = native_client

    async def scenario():
        async with client:
            memory = LangGraphMemory(
                client, scope_id=env.scopes[0], run_id=uuid4(), branch_id=uuid4()
            )
            source = await memory.observe(observation(env.scopes[0]), idempotency_key="source")
            reference = MemoryReference(memory_id=source.memory_id)
            result = await build_turn_graph(memory, unchanged).ainvoke(
                turn_input(
                    env.scopes[0],
                    state=CheckpointState(goal="Retain automatically tracked recall evidence"),
                    recall_request=Recall(
                        scope_ids=[env.scopes[0]],
                        purpose="pilot",
                        required_memory_refs=[reference],
                    ),
                    memory_refs=[],
                )
            )
            checkpoint = result["checkpoint_receipt"]
            saved = await client.get_checkpoint(checkpoint.checkpoint_id)
            assert saved.memory_refs == [reference]
            await client.forget(
                Forget(memory_ids=[source.memory_id], reason="Remove synthetic source"),
                idempotency_key="forget",
            )
            calls.clear()
            with pytest.raises(MemoryClientError) as invalidated:
                await memory.restore(
                    checkpoint.checkpoint_id, target_branch_id=uuid4(), idempotency_key="fork"
                )
            assert invalidated.value.error.code == "not_found"
            assert invalidated.value.error.native_status == 404
            assert calls == [("GET", f"/v1/checkpoints/{checkpoint.checkpoint_id}")]

    asyncio.run(scenario())


@pytest.mark.integration
def test_native_membership_revocation_hides_recall_and_denies_checkpoint(env, native_client):
    client, _ = native_client

    async def scenario():
        async with client:
            memory = LangGraphMemory(
                client, scope_id=env.scopes[0], run_id=uuid4(), branch_id=uuid4()
            )
            source = await memory.observe(observation(env.scopes[0]), idempotency_key="source")
            query = Recall(scope_ids=[env.scopes[0]], purpose="pilot")
            recalled = await memory.recall(query)
            assert source.memory_id in {item.memory_id for item in recalled.items}
            with psycopg.connect(env.admin_url) as conn:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(env.tenants[0]),),
                )
                conn.execute(
                    "DELETE FROM memory.scope_member WHERE scope_id=%s AND principal_id=%s",
                    (env.scopes[0], env.principals[0]),
                )
                conn.execute(
                    "UPDATE memory.tenant SET access_epoch=access_epoch+1 WHERE id=%s",
                    (env.tenants[0],),
                )
            assert not (await memory.recall(query)).items
            with pytest.raises(MemoryClientError) as denied:
                await memory.checkpoint(
                    CheckpointState(goal="Must not be stored"),
                    expected_head=None,
                    event_watermark=1,
                    memory_refs=[],
                    idempotency_key="revoked-checkpoint",
                )
            assert denied.value.error.code == "not_found"

    asyncio.run(scenario())


@pytest.mark.integration
def test_http_graph_restores_unknown_effect_fence_without_execution(env, api_process, monkeypatch):
    calls = []
    run, branch, operation, target = uuid4(), uuid4(), uuid4(), uuid4()
    original_client = NativeSettings.client

    async def record(request):
        calls.append((request.method, request.url.path))

    def tracked_client(settings):
        client = original_client(settings)
        client.event_hooks["request"].append(record)
        return client

    monkeypatch.setattr(NativeSettings, "client", tracked_client)

    async def scenario(url):
        async with AsyncMemoryClient(url, env.token()) as client:
            memory = LangGraphMemory(client, scope_id=env.scopes[0], run_id=run, branch_id=branch)
            source = await memory.observe(observation(env.scopes[0]), idempotency_key="source")
            refs = [MemoryReference(memory_id=source.memory_id, revision=source.revision)]
            state = CheckpointState(
                goal="Reconcile before any dispatch",
                important_ids=["ACME-123"],
                versions=["contract-v7"],
            )
            planner = AsyncMock(side_effect=unchanged)
            graph = build_turn_graph(memory, planner)
            result = await graph.ainvoke(
                turn_input(
                    env.scopes[0],
                    state=state,
                    recall_request=Recall(
                        scope_ids=[env.scopes[0]], purpose="pilot", required_memory_refs=refs
                    ),
                    memory_refs=refs,
                )
            )
            assert source.memory_id in {
                item.memory_id for item in result["recall_result"].items
            }
            checkpoint = result["checkpoint_receipt"]
            saved = await client.get_checkpoint(checkpoint.checkpoint_id)
            assert saved.state == state and saved.memory_refs == refs
            assert saved.harness_id == HARNESS_ID
            assert saved.harness_version == HARNESS_VERSION and saved.state_schema_version == 1
            effect = await client.plan_tool_effect(
                PlanToolEffect(
                    scope_id=env.scopes[0],
                    run_id=run,
                    operation_id=operation,
                    tool_name="synthetic.noop",
                    action_hash="a" * 64,
                ),
                idempotency_key="effect",
            )
            await client.transition_tool_effect(
                effect.memory_id,
                TransitionToolEffect(
                    expected_revision=1, status="dispatched", reason="Synthetic lost reply"
                ),
                idempotency_key="dispatch",
            )
            calls.clear()
            restored = await memory.restore(
                checkpoint.checkpoint_id, target_branch_id=target, idempotency_key="fork"
            )
            assert not restored.resume_allowed and not restored.automatic_reexecution
            assert restored.requires_reconciliation == [operation]
            assert restored.tool_effects == [
                ToolEffectSummary(
                    memory_id=effect.memory_id, operation_id=operation, revision=3, status="unknown"
                )
            ]
            assert restored.state.pending_effects == []
            assert restored.state == saved.state and restored.memory_refs == refs
            assert restored.branch_id == target
            assert restored.parent_checkpoint == checkpoint.checkpoint_id
            assert planner.await_count == 1
            assert calls == [
                ("GET", f"/v1/checkpoints/{checkpoint.checkpoint_id}"),
                ("POST", "/v1/checkpoints/restore"),
            ]
            with pytest.raises(MemoryClientError) as fenced:
                await client.transition_tool_effect(
                    effect.memory_id,
                    TransitionToolEffect(
                        expected_revision=3, status="dispatched", reason="Must not redispatch"
                    ),
                    idempotency_key="forbidden-redispatch",
                )
            assert fenced.value.error.code == "effect_transition_conflict"
            assert (await client.get_tool_effect(effect.memory_id)).status == "unknown"

    with api_process("langgraph-http.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))
