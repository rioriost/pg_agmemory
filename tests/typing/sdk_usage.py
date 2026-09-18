from typing import assert_type
from uuid import UUID

from pg_agmemory.models import (
    AdoptCandidate,
    AppendWorkingEvent,
    AssertionExplanation,
    AssertionHistory,
    AssertionHistoryPage,
    CancelJob,
    CandidateReviewPage,
    Capture,
    CaptureBatch,
    CaptureBatchResult,
    CaptureResult,
    CheckpointBranch,
    CheckpointEnvelope,
    CheckpointReceipt,
    CompactWorking,
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
    EntityPage,
    EntityReceipt,
    EpisodeExplanation,
    EpisodePage,
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
    ProcessMemory,
    PutEmbedding,
    QueryEntities,
    QueryEpisodes,
    QueryJobs,
    QueryWorkingEvents,
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
    WorkingEventPage,
    WorkingEventReceipt,
    WorkingSnapshot,
)
from pg_agmemory.providers import (
    ExtractionResult,
    GeneratedEmbedding,
    InferenceInput,
    InferenceProvider,
    ProviderSettings,
    SummaryResult,
    make_provider,
)
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


async def typed_inference(settings: ProviderSettings, data: InferenceInput) -> None:
    provider = make_provider(settings)
    assert_type(provider, InferenceProvider)
    assert_type(await provider.summarize(data), SummaryResult)
    assert_type(await provider.embed(data), GeneratedEmbedding)
    assert_type(await provider.extract(data), ExtractionResult)


async def typed_processing(
    client: AsyncMemoryClient,
    identity: UUID,
    key: str,
    processing: ProcessMemory,
    event: AppendWorkingEvent,
    query: QueryWorkingEvents,
    compact: CompactWorking,
    adoption: AdoptCandidate,
) -> None:
    async with client as memory:
        assert_type(await memory.process_memory(processing, idempotency_key=key), JobReceipt)
        assert_type(
            await memory.append_working_event(event, idempotency_key=key), WorkingEventReceipt
        )
        assert_type(await memory.query_working_events(query), WorkingEventPage)
        assert_type(await memory.compact_working(compact, idempotency_key=key), JobReceipt)
        assert_type(await memory.get_working_snapshot(identity), WorkingSnapshot)
        assert_type(await memory.get_extraction_candidates(identity), CandidateReviewPage)
        assert_type(
            await memory.adopt_candidate(identity, 0, adoption, idempotency_key=key),
            RememberResult,
        )


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
        assert_type(await memory.query_episodes(QueryEpisodes(scope_ids=[identity])), EpisodePage)
        assert_type(await memory.capture(capture, idempotency_key=key), CaptureResult)
        assert_type(
            await memory.capture_batch(
                CaptureBatch(episode=capture.episode, memories=[capture.memory]),
                idempotency_key=key,
            ),
            CaptureBatchResult,
        )
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
        assert_type(await memory.query_entities(QueryEntities(scope_ids=[identity])), EntityPage)
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
