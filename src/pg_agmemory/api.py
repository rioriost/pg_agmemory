import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
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
from pg_agmemory.checkpoints import Checkpoints
from pg_agmemory.database import SCHEMA_VERSION, Settings, connect, validate_runtime
from pg_agmemory.effects import ToolEffects
from pg_agmemory.models import (
    AssertionExplanation,
    CheckpointEnvelope,
    CheckpointReceipt,
    CreateCheckpoint,
    DeletionPreview,
    DeletionProgress,
    DeletionResult,
    EpisodeExplanation,
    ErrorBody,
    Explain,
    Forget,
    Identity,
    Observe,
    ObserveResult,
    PlanToolEffect,
    Recall,
    RecallResult,
    Remember,
    RememberResult,
    RestoreCheckpoint,
    ReviseAssertion,
    RevisionResult,
    ToolEffectDetail,
    ToolEffectReceipt,
    TransitionToolEffect,
)
from pg_agmemory.service import MemoryError, MemoryService

logger = logging.getLogger("pg_agmemory")
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

            async with await connect(self.settings.database_url) as conn:
                async with conn.transaction():
                    await conn.execute("SELECT set_config('pgag.subject', %s, true)", (subject,))
                    principal = await (
                        await conn.execute(
                            """SELECT tenant_id, id FROM memory.principal
                               WHERE external_subject = %s""",
                            (subject,),
                        )
                    ).fetchone()
                if principal is None:
                    raise MemoryError("unauthenticated", 401)
                identity = Identity(tenant_id=principal["tenant_id"], principal_id=principal["id"])
                # A session lock survives commit until response delivery. Forget cannot
                # acknowledge its barrier while an earlier response is still being sent.
                await conn.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                    (str(identity.tenant_id),),
                )
                async with conn.transaction():
                    await conn.execute(
                        """SELECT set_config('pgag.subject', %s, true),
                                  set_config('pgag.tenant_id', %s, true),
                                  set_config('pgag.principal_id', %s, true)""",
                        (subject, str(identity.tenant_id), str(identity.principal_id)),
                    )
                    current = await (
                        await conn.execute(
                            """SELECT 1 FROM memory.principal
                               WHERE tenant_id = %s AND id = %s AND external_subject = %s""",
                            (identity.tenant_id, identity.principal_id, subject),
                        )
                    ).fetchone()
                    if current is None:
                        raise MemoryError("unauthenticated", 401)
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
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled, TimeoutError) as exc:
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

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {
            "api_version": "v1",
            "service_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "stage": "m1-effect-ledger",
            "features": [
                "observe",
                "structured_remember",
                "assertion_revisions",
                "fts_recall",
                "explain",
                "forget",
                "checkpoints",
                "checkpoint_restore",
                "tool_effect_ledger",
            ],
            "graph_backend": None,
            "auto_synthesis": False,
            "checkpoints": True,
            "tool_effect_ledger": True,
            "temporal_revisions": True,
            "vector_search": False,
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
            },
        }

    @app.post("/v1/observe", status_code=201, response_model=ObserveResult)
    async def observe(data: Observe, request: Request, idempotency_key: IdempotencyKey) -> Any:
        return await service(request).observe(data, idempotency_key)

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

    @app.post("/v1/explain", response_model=EpisodeExplanation | AssertionExplanation)
    async def explain(data: Explain, request: Request) -> Any:
        return await service(request).explain(data)

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
