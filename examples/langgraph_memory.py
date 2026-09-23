"""Run two synthetic, deterministic turns against an already-provisioned native API.

Install pg-agmemory[langgraph]. Supply trusted PGAG_API_URL, PGAG_API_TOKEN,
PGAG_SCOPE_ID, PGAG_RUN_ID, and PGAG_BRANCH_ID; the token needs read/write access
to that scope. Use fresh run/branch UUIDs for a new demonstration. Explicitly set
PGAG_SYNTHETIC_CONSENT=yes to store this example's synthetic observation and state.
No model, paid provider, tool, approval, or external tracing is used.

Each turn is recall -> pure planner -> safe-boundary checkpoint, not persistence
of LangGraph threads or scheduler state. Every recalled item is conservatively
added to checkpoint references; the caller supplies initial-state lineage and
carries returned references into the next turn. Observation and the two checkpoints
are separate mutations: failure does not roll back earlier successes. Keep the same
IDs, payloads, and per-operation keys when resolving a partial/unknown outcome;
do not blindly retry with fresh keys. Check SDK error metadata and native state
before deciding whether to retry. There is no automatic retry here.

Restore is deliberately separate from execution: inspect the returned envelope's
resume_allowed, requires_reconciliation, untracked_effects, and tool_effects.
Reconcile externally before explicitly constructing a target-branch binding.
"""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

# Disable environment-driven LangSmith tracing before any framework import.
for _name in (
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
):
    os.environ[_name] = "false"

from pg_agmemory.langgraph import LangGraphMemory, TurnInput, build_turn_graph  # noqa: E402
from pg_agmemory.models import (  # noqa: E402
    CheckpointState,
    MemoryReference,
    Observe,
    Recall,
    RecallResult,
)
from pg_agmemory.sdk import AsyncMemoryClient  # noqa: E402


async def planner(state: CheckpointState, recalled: RecallResult) -> CheckpointState:
    """Advance one caller-specified action without executing any external effect."""
    if not recalled.items:
        raise ValueError("Synthetic observation was not recalled")
    action, *remaining = state.next_actions
    return CheckpointState.model_validate({
        **state.model_dump(mode="python"),
        "completed_actions": [*state.completed_actions, action],
        "next_actions": remaining,
    })


async def main() -> None:
    if os.environ.get("PGAG_SYNTHETIC_CONSENT") != "yes":
        raise ValueError("Set PGAG_SYNTHETIC_CONSENT=yes to store synthetic example data")
    scope_id = UUID(os.environ["PGAG_SCOPE_ID"])
    run_id = UUID(os.environ["PGAG_RUN_ID"])
    branch_id = UUID(os.environ["PGAG_BRANCH_ID"])
    key_prefix = f"langgraph-pilot:{scope_id}:{run_id}:{branch_id}"
    async with AsyncMemoryClient(
        os.environ["PGAG_API_URL"], os.environ["PGAG_API_TOKEN"]
    ) as client:
        memory = LangGraphMemory(
            client, scope_id=scope_id, run_id=run_id, branch_id=branch_id
        )
        observation = await memory.observe(
            Observe(
                scope_id=scope_id,
                source_namespace="langgraph-pilot-synthetic",
                source_event_id=f"{run_id}:{branch_id}",
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                content="Synthetic pilot: the demonstration project is called Cedar.",
                consent_reference="operator-approved-synthetic-langgraph-pilot-v1",
            ),
            idempotency_key=f"{key_prefix}:observe",
        )
        reference = MemoryReference(
            memory_id=observation.memory_id, revision=observation.revision
        )
        references = [reference]
        graph = build_turn_graph(memory, planner)
        state = CheckpointState(
            goal="Demonstrate two explicit typed-state boundaries",
            constraints=["Synthetic data only; no external effects"],
            next_actions=["Review synthetic context", "Finish synthetic review"],
        )
        head: UUID | None = None
        for step in (1, 2):
            turn: TurnInput = {
                "state": state,
                "recall_request": Recall(
                    scope_ids=[scope_id],
                    query="Cedar",
                    purpose="synthetic-langgraph-pilot",
                    required_memory_refs=[reference],
                    token_budget=2000,
                    max_items=1,
                ),
                "expected_head": head,
                "event_watermark": 1,
                "memory_refs": references,
                "idempotency_key": f"{key_prefix}:checkpoint:{step}",
            }
            result = (await graph.ainvoke(turn, version="v2")).value
            state = result["state"]
            references = result["memory_refs"]
            head = result["checkpoint_receipt"].checkpoint_id
            print(f"Completed safe boundary {step}; checkpoint_id={head}")


if __name__ == "__main__":
    asyncio.run(main())
