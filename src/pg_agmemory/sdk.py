from types import TracebackType
from typing import Self
from uuid import UUID

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

try:
    import httpx
except ModuleNotFoundError as exc:
    if exc.name != "httpx":
        raise
    raise ImportError("Python SDK requires the pg-agmemory[sdk] extra") from None

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
    EntityPage,
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
    QueryEntities,
    QueryJobs,
    Recall,
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
from pg_agmemory.native_client import (
    MAX_REQUEST_BYTES,
    SAFE_NATIVE_CODES,
    NativeHTTPClient,
    NativeSettings,
    failure,
)
from pg_agmemory.native_client import AdapterFailure as MemoryClientError

__all__ = ["AsyncMemoryClient", "MemoryClientError"]

SDK_NATIVE_CODES = frozenset(SAFE_NATIVE_CODES) | {
    "assertion_invalidated",
    "source_event_conflict",
    "relation_revision_required",
    "revision_conflict",
    "revision_limit_exceeded",
    "entity_invalidated",
    "invalid_relation_reference",
    "graph_invalidated",
    "checkpoint_invalidated",
    "checkpoint_harness_conflict",
    "checkpoint_head_conflict",
    "checkpoint_watermark_conflict",
    "checkpoint_incompatible",
    "checkpoint_branch_conflict",
    "effect_invalidated",
    "operation_conflict",
    "effect_run_invalidated",
    "effect_limit_exceeded",
    "effect_transition_conflict",
    "job_invalidated",
    "job_retry_conflict",
    "job_cancel_conflict",
    "job_intent_conflict",
    "job_limit_exceeded",
    "job_lease_conflict",
    "stale_context",
    "embedding_input_mismatch",
    "embedding_unavailable",
    "embedding_conflict",
    "embedding_limit_exceeded",
}


def _validated[T: BaseModel](request: T, model: type[T]) -> T:
    if not isinstance(request, model):
        raise failure("invalid_request")
    try:
        return model.model_validate(request.model_dump(mode="python", warnings="error"))
    except (ValidationError, PydanticSerializationError, ValueError):
        raise failure("invalid_request") from None


def _id(value: UUID) -> str:
    if not isinstance(value, UUID):
        raise failure("invalid_request")
    return str(value)


class AsyncMemoryClient:
    def __init__(self, api_url: str, api_token: str) -> None:
        self._settings = NativeSettings(api_url, api_token)
        self._http: httpx.AsyncClient | None = None
        self._native: NativeHTTPClient | None = None
        self._used = False

    async def __aenter__(self) -> Self:
        if self._used:
            raise failure("client_already_used")
        self._used = True
        http = self._settings.client()
        self._http = http
        try:
            native = NativeHTTPClient(http, safe_codes=SDK_NATIVE_CODES)
            await native.validate()
            self._native = native
        finally:
            if self._native is None:
                self._http = None
                await http.aclose()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        http, self._http = self._http, None
        self._native = None
        if http is not None:
            await http.aclose()

    def _connection(self) -> NativeHTTPClient:
        if self._native is None:
            raise failure("client_not_open")
        return self._native

    async def _post[Q: BaseModel, R: BaseModel](
        self,
        path: str,
        request: Q,
        model: type[Q],
        response: TypeAdapter[R],
        *,
        mutation: bool,
        status: int = 200,
        key: str | None = None,
        max_request_bytes: int = MAX_REQUEST_BYTES,
    ) -> R:
        native = self._connection()
        if mutation and (
            not isinstance(key, str)
            or not 1 <= len(key) <= 256
            or not all(33 <= ord(character) <= 126 for character in key)
        ):
            raise failure("invalid_request")
        return await native.request(
            path,
            _validated(request, model),
            response,
            expected_status=status,
            key=key,
            mutation=mutation,
            max_request_bytes=max_request_bytes,
        )

    async def _get[R: BaseModel](self, path: str, response: type[R]) -> R:
        return await self._connection().request(path, None, TypeAdapter(response))

    async def observe(self, request: Observe, *, idempotency_key: str) -> ObserveResult:
        return await self._post(
            "/v1/observe",
            request,
            Observe,
            TypeAdapter(ObserveResult),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def capture(self, request: Capture, *, idempotency_key: str) -> CaptureResult:
        return await self._post(
            "/v1/captures",
            request,
            Capture,
            TypeAdapter(CaptureResult),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def remember(self, request: Remember, *, idempotency_key: str) -> RememberResult:
        return await self._post(
            "/v1/remember",
            request,
            Remember,
            TypeAdapter(RememberResult),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def revise_assertion(
        self, memory_id: UUID, request: ReviseAssertion, *, idempotency_key: str
    ) -> RevisionResult:
        return await self._post(
            f"/v1/assertions/{_id(memory_id)}/revisions",
            request,
            ReviseAssertion,
            TypeAdapter(RevisionResult),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def recall(self, request: Recall) -> RecallResult:
        return await self._post(
            "/v1/recall", request, Recall, TypeAdapter(RecallResult), mutation=False
        )

    async def get_assertion_history(self, request: AssertionHistory) -> AssertionHistoryPage:
        return await self._post(
            "/v1/assertions/history",
            request,
            AssertionHistory,
            TypeAdapter(AssertionHistoryPage),
            mutation=False,
        )

    async def explain(self, request: Explain) -> EpisodeExplanation | AssertionExplanation:
        return await self._post(
            "/v1/explain", request, Explain, TypeAdapter(EpisodeExplanation | AssertionExplanation),
            mutation=False,
        )

    async def forget(
        self, request: Forget, *, idempotency_key: str
    ) -> DeletionPreview | DeletionResult:
        data = _validated(request, Forget)
        response: TypeAdapter[DeletionPreview | DeletionResult] = TypeAdapter(
            DeletionPreview if data.mode == "preview" else DeletionResult
        )
        return await self._post(
            "/v1/forget", data, Forget, response, mutation=True, status=202, key=idempotency_key
        )

    async def get_deletion(self, receipt_id: UUID) -> DeletionProgress:
        return await self._get(f"/v1/deletions/{_id(receipt_id)}", DeletionProgress)

    async def embedding_input(self, request: Explain) -> EmbeddingInput:
        return await self._post(
            "/v1/embedding-inputs", request, Explain, TypeAdapter(EmbeddingInput), mutation=False
        )

    async def put_embedding(
        self, request: PutEmbedding, *, idempotency_key: str
    ) -> EmbeddingReceipt:
        return await self._post(
            "/v1/embeddings",
            request,
            PutEmbedding,
            TypeAdapter(EmbeddingReceipt),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def create_entity(self, request: CreateEntity, *, idempotency_key: str) -> EntityReceipt:
        return await self._post(
            "/v1/entities",
            request,
            CreateEntity,
            TypeAdapter(EntityReceipt),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def get_entity(self, memory_id: UUID) -> EntityDetail:
        return await self._get(f"/v1/entities/{_id(memory_id)}", EntityDetail)

    async def query_entities(self, request: QueryEntities) -> EntityPage:
        return await self._post(
            "/v1/entities/query", request, QueryEntities, TypeAdapter(EntityPage), mutation=False
        )

    async def create_relation(
        self, request: CreateRelation, *, idempotency_key: str
    ) -> RememberResult:
        return await self._post(
            "/v1/relations",
            request,
            CreateRelation,
            TypeAdapter(RememberResult),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def revise_relation(
        self, memory_id: UUID, request: ReviseRelation, *, idempotency_key: str
    ) -> RevisionResult:
        return await self._post(
            f"/v1/relations/{_id(memory_id)}/revisions",
            request,
            ReviseRelation,
            TypeAdapter(RevisionResult),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def expand_graph(self, request: ExpandGraph) -> GraphResult:
        return await self._post(
            "/v1/graph/expand", request, ExpandGraph, TypeAdapter(GraphResult), mutation=False
        )

    async def enqueue_job(self, request: EnqueueJob, *, idempotency_key: str) -> JobReceipt:
        return await self._post(
            "/v1/jobs",
            request,
            EnqueueJob,
            TypeAdapter(JobReceipt),
            mutation=True,
            status=202,
            key=idempotency_key,
        )

    async def get_job(self, job_id: UUID) -> JobDetail:
        return await self._get(f"/v1/jobs/{_id(job_id)}", JobDetail)

    async def query_jobs(self, request: QueryJobs) -> JobPage:
        return await self._post(
            "/v1/jobs/query", request, QueryJobs, TypeAdapter(JobPage), mutation=False
        )

    async def retry_job(
        self, job_id: UUID, request: EnqueueJob, *, idempotency_key: str
    ) -> JobReceipt:
        return await self._post(
            f"/v1/jobs/{_id(job_id)}/retry",
            request,
            EnqueueJob,
            TypeAdapter(JobReceipt),
            mutation=True,
            status=202,
            key=idempotency_key,
        )

    async def cancel_job(
        self, job_id: UUID, request: CancelJob, *, idempotency_key: str
    ) -> JobReceipt:
        return await self._post(
            f"/v1/jobs/{_id(job_id)}/cancel",
            request,
            CancelJob,
            TypeAdapter(JobReceipt),
            mutation=True,
            key=idempotency_key,
        )

    async def create_checkpoint(
        self, request: CreateCheckpoint, *, idempotency_key: str
    ) -> CheckpointReceipt:
        return await self._post(
            "/v1/checkpoints",
            request,
            CreateCheckpoint,
            TypeAdapter(CheckpointReceipt),
            mutation=True,
            status=201,
            key=idempotency_key,
            max_request_bytes=1048576,
        )

    async def get_checkpoint(self, checkpoint_id: UUID) -> CheckpointEnvelope:
        return await self._get(f"/v1/checkpoints/{_id(checkpoint_id)}", CheckpointEnvelope)

    async def get_checkpoint_head(self, request: CheckpointBranch) -> CheckpointEnvelope:
        return await self._post(
            "/v1/checkpoints/head",
            request,
            CheckpointBranch,
            TypeAdapter(CheckpointEnvelope),
            mutation=False,
        )

    async def restore_checkpoint(
        self, request: RestoreCheckpoint, *, idempotency_key: str
    ) -> CheckpointEnvelope:
        return await self._post(
            "/v1/checkpoints/restore",
            request,
            RestoreCheckpoint,
            TypeAdapter(CheckpointEnvelope),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def plan_tool_effect(
        self, request: PlanToolEffect, *, idempotency_key: str
    ) -> ToolEffectReceipt:
        return await self._post(
            "/v1/tool-effects",
            request,
            PlanToolEffect,
            TypeAdapter(ToolEffectReceipt),
            mutation=True,
            status=201,
            key=idempotency_key,
        )

    async def get_tool_effect(self, memory_id: UUID) -> ToolEffectDetail:
        return await self._get(f"/v1/tool-effects/{_id(memory_id)}", ToolEffectDetail)

    async def transition_tool_effect(
        self, memory_id: UUID, request: TransitionToolEffect, *, idempotency_key: str
    ) -> ToolEffectReceipt:
        return await self._post(
            f"/v1/tool-effects/{_id(memory_id)}/transitions",
            request,
            TransitionToolEffect,
            TypeAdapter(ToolEffectReceipt),
            mutation=True,
            status=201,
            key=idempotency_key,
        )
