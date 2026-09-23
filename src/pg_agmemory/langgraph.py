"""Explicit safe-boundary memory operations, not a LangGraph checkpoint saver.

The graph has no durable scheduler, tool dispatch, or implicit model calls.
Callers own their planner, tracing configuration, and reconciliation decisions.
"""

from collections.abc import Awaitable, Callable
from dataclasses import KW_ONLY, dataclass, field
from typing import NotRequired, TypedDict
from uuid import UUID

try:
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.state import CompiledStateGraph
except ModuleNotFoundError as exc:
    if exc.name not in ("langgraph", "langgraph.graph", "langgraph.graph.state"):
        raise
    raise ImportError("LangGraph pilot requires the pg-agmemory[langgraph] extra") from None

from pg_agmemory.models import (
    CheckpointEnvelope,
    CheckpointReceipt,
    CheckpointState,
    CreateCheckpoint,
    MemoryReference,
    Observe,
    ObserveResult,
    Recall,
    RecallResult,
    RestoreCheckpoint,
)
from pg_agmemory.sdk import AsyncMemoryClient, _idempotency_key, _validated

HARNESS_ID = "pg-agmemory-langgraph-pilot"
HARNESS_VERSION = "1"
_BINDING_ERROR = "LangGraph memory binding mismatch"

__all__ = [
    "HARNESS_ID",
    "HARNESS_VERSION",
    "LangGraphMemory",
    "TurnInput",
    "TurnState",
    "build_turn_graph",
]


@dataclass(frozen=True)
class LangGraphMemory:
    """One caller-owned SDK identity bound to an explicit scope, run, and branch."""

    client: AsyncMemoryClient = field(repr=False)
    _: KW_ONLY
    scope_id: UUID
    run_id: UUID
    branch_id: UUID

    def __post_init__(self) -> None:
        if not all(isinstance(value, UUID) for value in (
            self.scope_id, self.run_id, self.branch_id
        )):
            raise ValueError(_BINDING_ERROR)

    async def observe(self, request: Observe, *, idempotency_key: str) -> ObserveResult:
        if isinstance(request, Observe) and getattr(request, "scope_id", None) != self.scope_id:
            raise ValueError(_BINDING_ERROR)
        request = _validated(request, Observe)
        if request.scope_id != self.scope_id:
            raise ValueError(_BINDING_ERROR)
        return await self.client.observe(
            request, idempotency_key=_idempotency_key(idempotency_key)
        )

    async def recall(self, request: Recall) -> RecallResult:
        if isinstance(request, Recall) and getattr(request, "scope_ids", None) != [self.scope_id]:
            raise ValueError(_BINDING_ERROR)
        request = _validated(request, Recall)
        if request.scope_ids != [self.scope_id]:
            raise ValueError(_BINDING_ERROR)
        return await self.client.recall(request)

    def _checkpoint_request(
        self,
        state: CheckpointState,
        *,
        expected_head: UUID | None,
        event_watermark: int,
        memory_refs: list[MemoryReference],
    ) -> CreateCheckpoint:
        return _validated(
            CreateCheckpoint.model_construct(
                scope_id=self.scope_id,
                run_id=self.run_id,
                branch_id=self.branch_id,
                expected_head=expected_head,
                harness_id=HARNESS_ID,
                harness_version=HARNESS_VERSION,
                state_schema_version=1,
                event_watermark=event_watermark,
                state=_validated(state, CheckpointState),
                memory_refs=memory_refs,
            ),
            CreateCheckpoint,
        )

    async def checkpoint(
        self,
        state: CheckpointState,
        *,
        expected_head: UUID | None,
        event_watermark: int,
        memory_refs: list[MemoryReference],
        idempotency_key: str,
    ) -> CheckpointReceipt:
        request = self._checkpoint_request(
            state,
            expected_head=expected_head,
            event_watermark=event_watermark,
            memory_refs=memory_refs,
        )
        return await self.client.create_checkpoint(
            request, idempotency_key=_idempotency_key(idempotency_key)
        )

    async def restore(
        self,
        checkpoint_id: UUID,
        *,
        target_branch_id: UUID,
        idempotency_key: str,
    ) -> CheckpointEnvelope:
        key = _idempotency_key(idempotency_key)
        request = _validated(
            RestoreCheckpoint.model_construct(
                checkpoint_id=checkpoint_id,
                target_branch_id=target_branch_id,
                harness_id=HARNESS_ID,
                harness_version=HARNESS_VERSION,
                state_schema_version=1,
            ),
            RestoreCheckpoint,
        )
        saved = await self.client.get_checkpoint(request.checkpoint_id)
        self._check_envelope(saved, checkpoint_id=request.checkpoint_id)
        restored = await self.client.restore_checkpoint(request, idempotency_key=key)
        self._check_envelope(restored, branch_id=request.target_branch_id)
        return restored

    def _check_envelope(
        self,
        saved: CheckpointEnvelope,
        *,
        checkpoint_id: UUID | None = None,
        branch_id: UUID | None = None,
    ) -> None:
        if (
            saved.scope_id != self.scope_id
            or saved.run_id != self.run_id
            or saved.harness_id != HARNESS_ID
            or saved.harness_version != HARNESS_VERSION
            or saved.state_schema_version != 1
            or (checkpoint_id is not None and saved.checkpoint_id != checkpoint_id)
            or (branch_id is not None and saved.branch_id != branch_id)
        ):
            raise ValueError(_BINDING_ERROR)


class TurnInput(TypedDict):
    state: CheckpointState
    recall_request: Recall
    expected_head: UUID | None
    event_watermark: int
    memory_refs: list[MemoryReference]
    idempotency_key: str


class TurnState(TurnInput):
    recall_result: NotRequired[RecallResult]
    checkpoint_receipt: NotRequired[CheckpointReceipt]


class _GraphState(TurnState):
    _checkpoint_request: NotRequired[CreateCheckpoint]
    _checkpoint_key: NotRequired[str]


def build_turn_graph(
    memory: LangGraphMemory,
    planner: Callable[[CheckpointState, RecallResult], Awaitable[CheckpointState]],
) -> CompiledStateGraph[_GraphState, None, TurnInput, TurnState]:
    """Compile one recall -> trusted pure planner -> explicit checkpoint boundary.

    There are no retries, cache, checkpointer, callbacks, or provider calls supplied
    by this factory. A caller must not use a side-effecting tool executor as planner.
    Every recalled item is conservatively retained as a checkpoint dependency;
    callers remain responsible for references supporting the initial state.
    """

    async def recall(state: _GraphState) -> dict[str, object]:
        checkpoint = memory._checkpoint_request(
            state["state"],
            expected_head=state["expected_head"],
            event_watermark=state["event_watermark"],
            memory_refs=state["memory_refs"],
        )
        key = _idempotency_key(state["idempotency_key"])
        request = _validated(state["recall_request"], Recall)
        result = await memory.recall(request)
        refs = list(checkpoint.memory_refs)
        seen = {(ref.memory_id, ref.revision) for ref in refs}
        for item in result.items:
            reference = _validated(
                MemoryReference.model_construct(
                    memory_id=item.memory_id, revision=item.revision
                ),
                MemoryReference,
            )
            identity = (reference.memory_id, reference.revision)
            if identity not in seen:
                refs.append(reference)
                seen.add(identity)
        checkpoint = _validated(
            checkpoint.model_copy(update={"memory_refs": refs}), CreateCheckpoint
        )
        return {
            "state": checkpoint.state.model_copy(deep=True),
            "recall_request": request,
            "expected_head": checkpoint.expected_head,
            "event_watermark": checkpoint.event_watermark,
            "memory_refs": [ref.model_copy(deep=True) for ref in checkpoint.memory_refs],
            "idempotency_key": key,
            "recall_result": result,
            "_checkpoint_request": checkpoint,
            "_checkpoint_key": key,
        }

    async def plan(state: _GraphState) -> dict[str, object]:
        planned = await planner(
            _validated(state["state"], CheckpointState),
            state["recall_result"].model_copy(deep=True),
        )
        return {"state": _validated(planned, CheckpointState)}

    async def checkpoint(state: _GraphState) -> dict[str, object]:
        # CAS and identity inputs never come from the planner's mutable arguments.
        original = state["_checkpoint_request"]
        receipt = await memory.checkpoint(
            state["state"],
            expected_head=original.expected_head,
            event_watermark=original.event_watermark,
            memory_refs=original.memory_refs,
            idempotency_key=state["_checkpoint_key"],
        )
        return {"checkpoint_receipt": receipt}

    graph = StateGraph(_GraphState, input_schema=TurnInput, output_schema=TurnState)
    graph.add_node("recall", recall)
    graph.add_node("plan", plan)
    graph.add_node("checkpoint", checkpoint)
    graph.add_edge(START, "recall")
    graph.add_edge("recall", "plan")
    graph.add_edge("plan", "checkpoint")
    graph.add_edge("checkpoint", END)
    return graph.compile()
