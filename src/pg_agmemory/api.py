import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, get_args
from uuid import UUID, uuid4

import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pg_agmemory import __version__
from pg_agmemory.capture import Captures
from pg_agmemory.checkpoints import Checkpoints
from pg_agmemory.database import (
    SCHEMA_VERSION,
    VECTOR_VERSION,
    RuntimeValidationError,
    Settings,
    validate_runtime,
)
from pg_agmemory.effects import ToolEffects
from pg_agmemory.embeddings import Embeddings
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.jobs import Jobs
from pg_agmemory.lexical import JAPANESE_PROFILE, SEARCH_PROFILES, TokenizerUnavailable
from pg_agmemory.models import (
    AssertionExplanation,
    AssertionHistory,
    AssertionHistoryPage,
    CancelJob,
    Capture,
    CaptureBatch,
    CaptureBatchResult,
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
    EntityType,
    EpisodeExplanation,
    EpisodePage,
    ErrorBody,
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
    QueryEpisodes,
    QueryJobs,
    ReadinessStatus,
    Recall,
    RecallResult,
    RelationType,
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
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

logger = logging.getLogger("pg_agmemory")
READINESS_TIMEOUT_SECONDS = 5.0
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


class TransactionBoundary:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        sent = False
        try:
            headers = dict(scope["headers"])
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            if not authorization.startswith("Bearer ") or len(authorization) > 16384:
                raise MemoryError("unauthenticated", 401)
            claims = jwt.decode(
                authorization[7:],
                self.settings.jwt_public_key,
                algorithms=["RS256"],
                issuer=self.settings.jwt_issuer,
                audience=self.settings.jwt_audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            subject = claims["sub"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 256:
                raise MemoryError("unauthenticated", 401)
            body = bytearray()
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    limit = 1024 * 1024 if scope["path"] == "/v1/checkpoints" else 256 * 1024
                    if len(body) > limit:
                        raise MemoryError("body_too_large", 413)
                    if not message.get("more_body", False):
                        break

            consumed = False

            async def buffered_receive() -> Message:
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            messages: list[Message] = []

            async def buffered_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        *message.get("headers", []),
                        (b"x-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                    ]
                messages.append(message)

            async with principal_connection(self.settings.database_url, subject) as (
                conn,
                identity,
            ):
                async with conn.transaction():
                    await bind_identity(conn, subject, identity)
                    scope["state"]["service"] = MemoryService(conn, identity)
                    await self.app(scope, buffered_receive, buffered_send)
                async with asyncio.timeout(10):
                    for message in messages:
                        sent = True
                        await send(message)
            return
        except jwt.InvalidTokenError:
            error = MemoryError("unauthenticated", 401)
        except MemoryError as exc:
            error = exc
        except (
            psycopg.OperationalError,
            psycopg.errors.QueryCanceled,
            TimeoutError,
            TokenizerUnavailable,
        ) as exc:
            logger.warning(
                "dependency_unavailable request_id=%s type=%s", request_id, type(exc).__name__
            )
            error = MemoryError("dependency_unavailable", 503)
        except psycopg.Error as exc:
            logger.error("database_error request_id=%s type=%s", request_id, type(exc).__name__)
            error = MemoryError("database_error", 503)
        if sent:
            return
        response = JSONResponse(
            ErrorBody(
                code=error.code, request_id=request_id, retryable=error.status == 503
            ).model_dump(),
            status_code=error.status,
            headers={
                "X-Request-ID": request_id,
                "Cache-Control": "no-store",
                **({"WWW-Authenticate": "Bearer"} if error.status == 401 else {}),
            },
        )
        await response(scope, receive, send)


def service(request: Request) -> MemoryService:
    value: MemoryService = request.state.service
    return value


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    readiness_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        key = load_pem_public_key(configured.jwt_public_key.encode())
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise RuntimeError("JWT verification requires an RSA public key of at least 2048 bits")
        await validate_runtime(configured.database_url)
        yield

    app = FastAPI(
        title="pg_agmemory",
        version=__version__,
        lifespan=lifespan,
        description="Initial M1 slice. Not a production-qualified memory service.",
        license_info={"name": "MIT", "identifier": "MIT"},
        responses={status: {"model": ErrorBody} for status in (400, 401, 404, 409, 413, 422, 503)},
    )
    app.add_middleware(TransactionBoundary, settings=configured)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        malformed = any(error["type"] == "json_invalid" for error in exc.errors())
        return JSONResponse(
            ErrorBody(
                code="malformed_json" if malformed else "invalid_request",
                request_id=request.state.request_id,
                retryable=False,
            ).model_dump(),
            status_code=400 if malformed else 422,
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/readyz", response_model=ReadinessStatus,
        responses={503: {"model": ReadinessStatus, "description": "Runtime is not ready"}},
    )
    async def ready() -> JSONResponse:
        request_id = str(uuid4())
        failure = None
        if readiness_lock.locked():
            failure = "probe_busy"
        else:
            async with readiness_lock:
                try:
                    async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
                        await validate_runtime(configured.database_url)
                except RuntimeValidationError as exc:
                    failure = exc.code
                except (psycopg.Error, TimeoutError) as exc:
                    failure = type(exc).__name__
        if failure is not None:
            logger.warning("readiness_unavailable request_id=%s reason=%s", request_id, failure)
        return JSONResponse(
            ReadinessStatus(status="not_ready" if failure else "ready").model_dump(),
            status_code=503 if failure else 200,
            headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
        )

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {
            "api_version": "v1",
            "service_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "stage": "m2-selectable-inference",
            "features": [
                "observe",
                "episode_query",
                "optional_provider_adapters",
                "atomic_structured_capture",
                "atomic_batch_structured_capture",
                "structured_remember",
                "assertion_revisions",
                "fts_recall",
                "japanese_fts",
                "explicit_embeddings",
                "exact_vector_recall",
                "hybrid_recall",
                "explain",
                "forget",
                "checkpoints",
                "checkpoint_restore",
                "tool_effect_ledger",
                "entities",
                "structured_relations",
                "graph_expand",
                "durable_jobs",
            ],
            "graph_backend": "sql",
            "episode_query": {
                "endpoint": "/v1/episodes/query",
                "order": ["recorded_at_desc", "memory_id_desc"],
                "pagination": "exclusive_keyset",
                "occurred_time_bounds": "half_open",
                "max_items": 100,
                "includes_content": False,
            },
            "entity_types": list(get_args(EntityType)),
            "relation_types": list(get_args(RelationType)),
            "entity_query": {
                "endpoint": "/v1/entities/query",
                "match": "exact",
                "order": ["recorded_at_desc", "memory_id_desc"],
                "pagination": "exclusive_keyset",
                "max_items": 100,
            },
            "auto_synthesis": False,
            "model_inference": {
                "interface": "operator_cli_and_python",
                "extra": "providers",
                "backends": ["local_http", "openai_compatible", "azure_ai"],
                "azure_products": ["flexible_server", "horizondb"],
                "operations": ["inspect", "summarize", "embed"],
                "automatic": False,
                "publishes_memory": False,
                "live_provider_qualified": False,
            },
            "atomic_capture": {
                "endpoint": "/v1/captures",
                "max_jobs": 1,
                "recipe_version": "structured-remember-v1",
                "automatic_capture": False,
            },
            "atomic_batch_capture": {
                "endpoint": "/v1/captures/batch",
                "max_jobs": 16,
                "recipe_version": "structured-remember-v1",
                "automatic_capture": False,
                "admission_atomic": True,
                "publication_atomic": False,
            },
            "job_kinds": ["structured_remember"],
            "job_query": {
                "endpoint": "/v1/jobs/query",
                "ownership": "caller",
                "order": ["created_at_desc", "job_id_desc"],
                "pagination": "exclusive_keyset",
                "max_items": 100,
            },
            "job_cancellation": {
                "endpoint": "/v1/jobs/{job_id}/cancel",
                "compare_and_swap": ["state", "attempt"],
                "terminal_state": "cancelled",
                "provider_interruption": False,
            },
            "checkpoints": True,
            "checkpoint_head": {
                "endpoint": "/v1/checkpoints/head",
                "read_only": True,
                "branch_identity": ["scope_id", "run_id", "branch_id"],
                "fallback_to_ancestor": False,
            },
            "tool_effect_ledger": True,
            "temporal_revisions": True,
            "assertion_history": {
                "endpoint": "/v1/assertions/history",
                "order": "revision_desc",
                "pagination": "exclusive_revision",
                "max_items": 100,
                "includes_values": False,
                "includes_evidence_quotes": False,
            },
            "vector_search": True,
            "retrieval_modes": ["lexical", "vector", "hybrid"],
            "default_retrieval_mode": "lexical",
            "required_context": {
                "retrieval_modes": ["lexical"],
                "max_refs": 16,
                "order": "request_order",
                "budget_policy": "all_required_or_error",
            },
            "recall_filters": {
                "fields": ["kind", "subject", "predicate"],
                "match": "exact",
                "combination": "and",
                "retrieval_modes": ["lexical", "vector", "hybrid"],
            },
            "embeddings": {
                "extension": "pgvector",
                "extension_version": VECTOR_VERSION,
                "dimensions": 768,
                "distance_metric": "cosine",
                "normalization": "l2-f32-v1",
                "input_format": "memory-content-v1",
                "generation": "caller_supplied",
                "model_versions_per_revision": 8,
                "approximate_search": False,
                "hybrid_fusion": "rrf-60",
            },
            "mcp_adapter": {
                "installation": "mcp-extra",
                "transport": "stdio",
                "remote": False,
                "tools": ["memory_recall", "memory_remember", "memory_explain", "memory_forget"],
            },
            "python_sdk": {
                "installation": "sdk-extra",
                "async": True,
                "automatic_retry": False,
            },
            "scope_access_administration": {
                "transport": "admin-cli",
                "command": "scope-access",
                "compare_and_swap": "tenant_access_epoch",
                "audit": "database_role",
            },
            "health_probes": {
                "liveness": "/healthz",
                "readiness": "/readyz",
                "readiness_timeout_seconds": READINESS_TIMEOUT_SECONDS,
                "readiness_max_in_flight_per_process": 1,
            },
            "recall_hook": {
                "installation": "hook-extra",
                "transport": "stdin-json",
                "events": ["session_start", "task_switch", "after_compaction"],
                "automatic_registration": False,
                "capture": False,
                "max_input_bytes": 32768,
                "max_budget_bytes": 2000,
                "max_items": 20,
                "default_timeout_seconds": 2.0,
            },
            "search_profiles": SEARCH_PROFILES,
            "default_search_profile": "simple-v1",
            "japanese_fts": {
                "profile": JAPANESE_PROFILE,
                "tokenizer": "Janome",
                "version": "0.5.0",
                "dictionary": "mecab-ipadic-2.7.0-20070801 bundled with Janome 0.5.0",
                "normalization": "none",
                "segmentation": "japanese-script-runs",
            },
            "tokenizer": "utf8-bytes-v1",
            "exact_token_count": False,
            "idempotency_retention": "tenant_lifetime",
            "limits": {
                "body_bytes": 262144,
                "max_items": 100,
                "deletion_dependents": 10000,
                "assertion_revisions": 1000,
                "checkpoint_body_bytes": 1048576,
                "checkpoint_references": 100,
                "tool_effects_per_run": 100,
                "tool_effect_references": 100,
                "graph_hops": 2,
                "graph_seeds": 16,
                "graph_paths": 100,
                "active_jobs_per_scope": 100,
                "job_attempts": 5,
                "job_lease_seconds": 30,
            },
        }

    @app.post("/v1/observe", status_code=201, response_model=ObserveResult)
    async def observe(data: Observe, request: Request, idempotency_key: IdempotencyKey) -> Any:
        return await service(request).observe(data, idempotency_key)

    @app.post("/v1/episodes/query", response_model=EpisodePage)
    async def query_episodes(data: QueryEpisodes, request: Request) -> Any:
        return await service(request).query_episodes(data)

    @app.post("/v1/captures", status_code=201, response_model=CaptureResult)
    async def capture(data: Capture, request: Request, idempotency_key: IdempotencyKey) -> Any:
        return await Captures(service(request)).create(data, idempotency_key)

    @app.post("/v1/captures/batch", status_code=201, response_model=CaptureBatchResult)
    async def capture_batch(
        data: CaptureBatch, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await Captures(service(request)).create_batch(data, idempotency_key)

    @app.post("/v1/remember", status_code=201, response_model=RememberResult)
    async def remember(data: Remember, request: Request, idempotency_key: IdempotencyKey) -> Any:
        return await service(request).remember(data, idempotency_key)

    @app.post(
        "/v1/assertions/{memory_id}/revisions", status_code=201, response_model=RevisionResult
    )
    async def revise_assertion(
        memory_id: UUID, data: ReviseAssertion, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await service(request).revise_assertion(memory_id, data, idempotency_key)

    @app.post("/v1/checkpoints", status_code=201, response_model=CheckpointReceipt)
    async def checkpoint(
        data: CreateCheckpoint, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await Checkpoints(service(request)).create(data, idempotency_key)

    @app.post("/v1/checkpoints/restore", status_code=201, response_model=CheckpointEnvelope)
    async def restore_checkpoint(
        data: RestoreCheckpoint, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await Checkpoints(service(request)).restore(data, idempotency_key)

    @app.post("/v1/checkpoints/head", response_model=CheckpointEnvelope)
    async def checkpoint_head(data: CheckpointBranch, request: Request) -> Any:
        return await Checkpoints(service(request)).head(data)

    @app.get("/v1/checkpoints/{checkpoint_id}", response_model=CheckpointEnvelope)
    async def get_checkpoint(checkpoint_id: UUID, request: Request) -> Any:
        return await Checkpoints(service(request)).envelope(checkpoint_id)

    @app.post("/v1/tool-effects", status_code=201, response_model=ToolEffectReceipt)
    async def plan_tool_effect(
        data: PlanToolEffect, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await ToolEffects(service(request)).plan(data, idempotency_key)

    @app.post(
        "/v1/tool-effects/{memory_id}/transitions",
        status_code=201,
        response_model=ToolEffectReceipt,
    )
    async def transition_tool_effect(
        memory_id: UUID,
        data: TransitionToolEffect,
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> Any:
        return await ToolEffects(service(request)).transition(memory_id, data, idempotency_key)

    @app.get("/v1/tool-effects/{memory_id}", response_model=ToolEffectDetail)
    async def get_tool_effect(memory_id: UUID, request: Request) -> Any:
        return await ToolEffects(service(request)).get(memory_id)

    @app.post("/v1/recall", response_model=RecallResult)
    async def recall(data: Recall, request: Request) -> Any:
        return await service(request).recall(data)

    @app.post("/v1/embedding-inputs", response_model=EmbeddingInput)
    async def embedding_input(data: Explain, request: Request) -> Any:
        return await Embeddings(service(request)).input(data)

    @app.post("/v1/embeddings", status_code=201, response_model=EmbeddingReceipt)
    async def embedding(
        data: PutEmbedding, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await Embeddings(service(request)).put(data, idempotency_key)

    @app.post("/v1/entities", status_code=201, response_model=EntityReceipt)
    async def entity(data: CreateEntity, request: Request, idempotency_key: IdempotencyKey) -> Any:
        return await SqlGraph(service(request)).create_entity(data, idempotency_key)

    @app.post("/v1/entities/query", response_model=EntityPage)
    async def query_entities(data: QueryEntities, request: Request) -> Any:
        return await SqlGraph(service(request)).query_entities(data)

    @app.get("/v1/entities/{memory_id}", response_model=EntityDetail)
    async def get_entity(memory_id: UUID, request: Request) -> Any:
        return await SqlGraph(service(request)).entity(memory_id)

    @app.post("/v1/relations", status_code=201, response_model=RememberResult)
    async def relation(
        data: CreateRelation, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await SqlGraph(service(request)).create_relation(data, idempotency_key)

    @app.post("/v1/relations/{memory_id}/revisions", status_code=201, response_model=RevisionResult)
    async def revise_relation(
        memory_id: UUID, data: ReviseRelation, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await SqlGraph(service(request)).revise_relation(memory_id, data, idempotency_key)

    @app.post("/v1/graph/expand", response_model=GraphResult)
    async def expand_graph(data: ExpandGraph, request: Request) -> Any:
        return await SqlGraph(service(request)).expand(data)

    @app.post("/v1/jobs", status_code=202, response_model=JobReceipt)
    async def enqueue_job(
        data: EnqueueJob, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await Jobs(service(request)).enqueue(data, idempotency_key)

    @app.post("/v1/jobs/query", response_model=JobPage)
    async def query_jobs(data: QueryJobs, request: Request) -> Any:
        return await Jobs(service(request)).query(data)

    @app.get("/v1/jobs/{job_id}", response_model=JobDetail)
    async def get_job(job_id: UUID, request: Request) -> Any:
        return await Jobs(service(request)).get(job_id)

    @app.post("/v1/jobs/{job_id}/retry", status_code=202, response_model=JobReceipt)
    async def retry_job(
        job_id: UUID, data: EnqueueJob, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await Jobs(service(request)).enqueue(data, idempotency_key, retry_of=job_id)

    @app.post("/v1/jobs/{job_id}/cancel", response_model=JobReceipt)
    async def cancel_job(
        job_id: UUID, data: CancelJob, request: Request, idempotency_key: IdempotencyKey
    ) -> Any:
        return await Jobs(service(request)).cancel(job_id, data, idempotency_key)

    @app.post("/v1/explain", response_model=EpisodeExplanation | AssertionExplanation)
    async def explain(data: Explain, request: Request) -> Any:
        return await service(request).explain(data)

    @app.post("/v1/assertions/history", response_model=AssertionHistoryPage)
    async def assertion_history(data: AssertionHistory, request: Request) -> Any:
        return await service(request).assertion_history(data)

    @app.post("/v1/forget", status_code=202, response_model=DeletionResult | DeletionPreview)
    async def forget(data: Forget, request: Request, idempotency_key: IdempotencyKey) -> Any:
        return await service(request).forget(data, idempotency_key)

    @app.get("/v1/deletions/{receipt_id}", response_model=DeletionProgress)
    async def deletion(receipt_id: UUID, request: Request) -> Any:
        memory = service(request)
        row = await (
            await memory.conn.execute(
                """SELECT id AS deletion_id, mode, state, object_count, deletion_epoch, created_at
                   FROM memory_ops.deletion_request WHERE tenant_id = %s AND id = %s""",
                (memory.tenant, receipt_id),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("not_found", 404)
        return {**row, "backup_status": "operator_managed", "backup_retention_deadline": None}

    schema = app.openapi()
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    for path, methods in schema["paths"].items():
        if path.startswith("/v1/"):
            for operation in methods.values():
                operation["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return app
