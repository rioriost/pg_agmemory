from collections.abc import Awaitable, Callable
from typing import assert_type
from uuid import UUID

from pg_agmemory.langgraph import LangGraphMemory, TurnInput, TurnState, build_turn_graph
from pg_agmemory.models import (
    CheckpointEnvelope,
    CheckpointReceipt,
    CheckpointState,
    MemoryReference,
    Observe,
    ObserveResult,
    Recall,
    RecallResult,
)
from pg_agmemory.sdk import AsyncMemoryClient


async def typed_pilot_calls(
    client: AsyncMemoryClient,
    scope: UUID,
    run: UUID,
    branch: UUID,
    target_branch: UUID,
    observation: Observe,
    recall: Recall,
    state: CheckpointState,
    refs: list[MemoryReference],
    head: UUID | None,
    planner: Callable[[CheckpointState, RecallResult], Awaitable[CheckpointState]],
) -> None:
    memory = LangGraphMemory(client, scope_id=scope, run_id=run, branch_id=branch)
    assert_type(await memory.observe(observation, idempotency_key="observation"), ObserveResult)
    assert_type(await memory.recall(recall), RecallResult)
    receipt = await memory.checkpoint(
        state,
        expected_head=head,
        event_watermark=1,
        memory_refs=refs,
        idempotency_key="checkpoint",
    )
    assert_type(receipt, CheckpointReceipt)
    restored = await memory.restore(
        receipt.checkpoint_id, target_branch_id=target_branch, idempotency_key="restore"
    )
    assert_type(restored, CheckpointEnvelope)
    assert_type(restored.state, CheckpointState)
    assert_type(restored.memory_refs, list[MemoryReference])

    turn: TurnInput = {
        "state": state,
        "recall_request": recall,
        "expected_head": receipt.checkpoint_id,
        "event_watermark": 2,
        "memory_refs": refs,
        "idempotency_key": "next-turn",
    }
    graph = build_turn_graph(memory, planner)
    result = await graph.ainvoke(turn, version="v2")
    assert_type(result.value, TurnState)
    assert_type(result.value["state"], CheckpointState)
    assert_type(result.value["recall_result"], RecallResult)
    assert_type(result.value["checkpoint_receipt"], CheckpointReceipt)
