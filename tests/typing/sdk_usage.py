from typing import assert_type
from uuid import UUID

from pg_agmemory.models import (
    AssertionExplanation,
    AssertionHistory,
    AssertionHistoryPage,
    CancelJob,
    Capture,
    CaptureResult,
    CheckpointBranch,
    CheckpointEnvelope,
    CheckpointReceipt,
    CreateCheckpoint,
    CreateEntity,
    CreateRelation,
    DeletionPreview,
    DeletionProgress,
    DeletionResult,
    EmbeddingInput,
    EmbeddingReceipt,
    EnqueueJob,
    EntityDetail,
    EntityReceipt,
    EpisodeExplanation,
    ExpandGraph,
    Explain,
    Forget,
    GraphResult,
    JobDetail,
    JobPage,
    JobReceipt,
    Observe,
    ObserveResult,
    PlanToolEffect,
    PutEmbedding,
    QueryJobs,
    Recall,
    RecallFilters,
    RecallResult,
    Remember,
    RememberResult,
    RestoreCheckpoint,
    ReviseAssertion,
    ReviseRelation,
    RevisionResult,
    ToolEffectDetail,
    ToolEffectReceipt,
    TransitionToolEffect,
)
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


async def typed_calls(
    client: AsyncMemoryClient,
    identity: UUID,
    key: str,
    observe: Observe,
    capture: Capture,
    remember: Remember,
    revise: ReviseAssertion,
    recall: Recall,
    explain: Explain,
    forget: Forget,
    embedding: PutEmbedding,
    entity: CreateEntity,
    relation: CreateRelation,
    revise_relation: ReviseRelation,
    graph: ExpandGraph,
    job: EnqueueJob,
    cancellation: CancelJob,
    checkpoint: CreateCheckpoint,
    restore: RestoreCheckpoint,
    effect: PlanToolEffect,
    transition: TransitionToolEffect,
) -> None:
    async with client as memory:
        assert_type(memory, AsyncMemoryClient)
        assert_type(await memory.observe(observe, idempotency_key=key), ObserveResult)
        assert_type(await memory.capture(capture, idempotency_key=key), CaptureResult)
        assert_type(await memory.remember(remember, idempotency_key=key), RememberResult)
        assert_type(
            await memory.revise_assertion(identity, revise, idempotency_key=key), RevisionResult
        )
        assert_type(await memory.recall(recall), RecallResult)
        assert_type(
            await memory.recall(
                Recall(
                    scope_ids=[identity],
                    purpose="typed-filter-example",
                    filters=RecallFilters(kind="assertion", subject="ACME", predicate="tier"),
                )
            ),
            RecallResult,
        )
        assert_type(await memory.explain(explain), EpisodeExplanation | AssertionExplanation)
        assert_type(
            await memory.get_assertion_history(AssertionHistory(memory_id=identity)),
            AssertionHistoryPage,
        )
        assert_type(
            await memory.forget(forget, idempotency_key=key), DeletionPreview | DeletionResult
        )
        assert_type(await memory.get_deletion(identity), DeletionProgress)
        assert_type(await memory.embedding_input(explain), EmbeddingInput)
        assert_type(await memory.put_embedding(embedding, idempotency_key=key), EmbeddingReceipt)
        assert_type(await memory.create_entity(entity, idempotency_key=key), EntityReceipt)
        assert_type(await memory.get_entity(identity), EntityDetail)
        assert_type(await memory.create_relation(relation, idempotency_key=key), RememberResult)
        assert_type(
            await memory.revise_relation(identity, revise_relation, idempotency_key=key),
            RevisionResult,
        )
        assert_type(await memory.expand_graph(graph), GraphResult)
        assert_type(await memory.enqueue_job(job, idempotency_key=key), JobReceipt)
        assert_type(await memory.get_job(identity), JobDetail)
        assert_type(await memory.query_jobs(QueryJobs(scope_ids=[identity])), JobPage)
        assert_type(await memory.retry_job(identity, job, idempotency_key=key), JobReceipt)
        assert_type(
            await memory.cancel_job(identity, cancellation, idempotency_key=key), JobReceipt
        )
        assert_type(
            await memory.create_checkpoint(checkpoint, idempotency_key=key), CheckpointReceipt
        )
        assert_type(await memory.get_checkpoint(identity), CheckpointEnvelope)
        assert_type(
            await memory.get_checkpoint_head(
                CheckpointBranch(scope_id=identity, run_id=identity, branch_id=identity)
            ),
            CheckpointEnvelope,
        )
        assert_type(
            await memory.restore_checkpoint(restore, idempotency_key=key), CheckpointEnvelope
        )
        assert_type(await memory.plan_tool_effect(effect, idempotency_key=key), ToolEffectReceipt)
        assert_type(await memory.get_tool_effect(identity), ToolEffectDetail)
        assert_type(
            await memory.transition_tool_effect(identity, transition, idempotency_key=key),
            ToolEffectReceipt,
        )


def typed_error(error: MemoryClientError) -> None:
    assert_type(error.error.code, str)
    assert_type(error.error.outcome_unknown, bool)
    assert_type(error.error.request_id, UUID | None)
