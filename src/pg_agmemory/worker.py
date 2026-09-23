import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import psycopg
from pydantic import ValidationError

from pg_agmemory.database import validate_runtime
from pg_agmemory.jobs import job_transaction
from pg_agmemory.lexical import TokenizerUnavailable
from pg_agmemory.models import JobError, Remember
from pg_agmemory.service import MemoryError
from pg_agmemory.transactions import CommitOutcomeUnknown

logger = logging.getLogger("pg_agmemory.worker")
MODEL_HEARTBEAT_SECONDS = 20

if TYPE_CHECKING:
    from pg_agmemory.worker_profile import WorkerProfile


async def run_once(
    url: str, subject: str, *, profile: "WorkerProfile | None" = None
) -> dict[str, Any]:
    async with job_transaction(url, subject) as jobs:
        claim = await jobs.claim(
            180 if profile else 30, profile_digest=profile.digest if profile else None
        )
    if claim is None:
        return {"outcome": "idle"}
    job_id = claim["job_id"]
    if claim["state"] == "failed":
        logger.warning("job_failed job_id=%s code=attempt_limit", job_id)
        return {"job_id": str(job_id), "outcome": "failed"}
    token = claim["lease_token"]
    code: JobError
    retry = True
    try:
        if claim["kind"] != "structured_remember":
            if profile is None:
                raise MemoryError("job_intent_conflict", 409)
            return await process_model(url, subject, claim, profile)
        prepared = Remember.model_validate(claim["payload"])
        async with job_transaction(url, subject) as jobs:
            result = await jobs.publish(job_id, token, prepared)
        return {"job_id": str(job_id), "outcome": "succeeded", "result": result}
    except ValidationError:
        code, retry = "invalid_input", False
    except MemoryError as exc:
        if exc.code in ("not_found", "unauthenticated", "job_lease_conflict"):
            logger.warning("job_lease_lost job_id=%s code=%s", job_id, exc.code)
            return {"job_id": str(job_id), "outcome": "lease_lost"}
        if exc.code == "stale_context":
            code = "stale_context"
        elif exc.code in ("invalid_evidence", "job_intent_conflict"):
            code, retry = "invalid_input", False
        else:
            raise
    except (psycopg.OperationalError, psycopg.errors.QueryCanceled, TokenizerUnavailable):
        code = "dependency_unavailable"
    logger.warning("job_attempt_failed job_id=%s code=%s", job_id, code)
    try:
        async with job_transaction(url, subject) as jobs:
            state = await jobs.fail(job_id, token, code, retry=retry)
    except MemoryError as exc:
        if exc.code not in ("not_found", "unauthenticated", "job_lease_conflict"):
            raise
        logger.warning("job_lease_lost job_id=%s code=%s", job_id, exc.code)
        state = "lease_lost"
    return {"job_id": str(job_id), "outcome": state}


async def process_model(
    url: str, subject: str, claim: dict[str, Any], profile: "WorkerProfile"
) -> dict[str, Any]:
    from pg_agmemory.processing import Processing
    from pg_agmemory.providers import ProviderFailure

    job_id, token = claim["job_id"], claim["lease_token"]
    code: JobError = "provider_failed"
    reserved = False
    unknown = False
    try:
        async with job_transaction(url, subject) as jobs:
            prepared = await Processing(jobs.memory).prepare(job_id, token, profile, reserve=True)
        reserved = True
        unknown = True
        generated = await call_model(url, subject, claim, profile, prepared["text"])
        unknown = False
        async with job_transaction(url, subject) as jobs:
            result = await Processing(jobs.memory).publish(job_id, token, profile, generated)
        return {"job_id": str(job_id), "outcome": "succeeded", "result": result}
    except ProviderFailure as exc:
        unknown = exc.error.billing_unknown
        code = "billing_unknown" if unknown else "provider_failed"
    except TimeoutError:
        unknown, code = True, "billing_unknown"
    except ValidationError:
        unknown, code = reserved, "invalid_input"
    except MemoryError as exc:
        if exc.code in ("not_found", "unauthenticated", "job_lease_conflict", "job_invalidated"):
            return {"job_id": str(job_id), "outcome": "lease_lost"}
        if exc.code in ("synthesis_policy_denied", "processing_call_limit"):
            code = "policy_denied"
        elif exc.code == "stale_context":
            code = "stale_context"
        elif exc.code in ("compaction_conflict", "checkpoint_head_conflict",
                          "checkpoint_invalidated", "working_invalidated"):
            code = "compaction_conflict"
        elif exc.code == "billing_unknown":
            code, unknown = "billing_unknown", True
        elif exc.code in ("invalid_processing_reference", "processing_input_limit",
                          "job_intent_conflict", "embedding_conflict", "embedding_input_mismatch",
                          "embedding_limit_exceeded", "embedding_unavailable", "invalid_evidence"):
            code = "invalid_input"
        else:
            raise
    except (psycopg.OperationalError, psycopg.errors.QueryCanceled, TokenizerUnavailable):
        code, unknown = "dependency_unavailable", reserved
    logger.warning("model_attempt_failed job_id=%s code=%s", job_id, code)
    try:
        async with job_transaction(url, subject) as jobs:
            await jobs.lease(job_id, token, epochs=False)
            if reserved and not unknown:
                await jobs.conn.execute(
                    """UPDATE memory_ops.model_call SET outcome='failed',billing_unknown=false
                       WHERE tenant_id=%s AND job_id=%s AND outcome='unknown'""",
                    (jobs.tenant, job_id),
                )
            state = await jobs.fail(job_id, token, code, retry=False)
    except MemoryError as exc:
        if exc.code not in (
            "not_found", "unauthenticated", "job_lease_conflict", "job_invalidated"
        ):
            raise
        state = "lease_lost"
    return {"job_id": str(job_id), "outcome": state}


async def call_model(
    url: str, subject: str, claim: dict[str, Any], profile: "WorkerProfile", text: str
) -> Any:
    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(MODEL_HEARTBEAT_SECONDS)
            async with job_transaction(url, subject) as jobs:
                await jobs.heartbeat(claim["job_id"], claim["lease_token"])

    # Neither this provider task nor the wait below owns a database connection.
    call = asyncio.create_task(profile.call(claim["kind"], text))
    pulse = asyncio.create_task(heartbeat())
    try:
        async with asyncio.timeout(125):
            done, _ = await asyncio.wait((call, pulse), return_when=asyncio.FIRST_COMPLETED)
            if pulse in done:
                await pulse
            return await call
    finally:
        for task in (call, pulse):
            if not task.done():
                task.cancel()
        await asyncio.gather(call, pulse, return_exceptions=True)


async def run(
    url: str, subject: str, *, once: bool = False, profile: "WorkerProfile | None" = None
) -> None:
    await validate_runtime(url)
    while True:
        try:
            result = await run_once(url, subject, profile=profile)
        except CommitOutcomeUnknown:
            logger.error("worker_commit_outcome_unknown reconciliation_required=true")
            raise
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled) as exc:
            logger.warning("worker_dependency_unavailable type=%s", type(exc).__name__)
            if once:
                raise
            await asyncio.sleep(2)
            continue
        if once or result["outcome"] != "idle":
            print(json.dumps(result), flush=True)
        if once:
            return
        if result["outcome"] == "idle":
            await asyncio.sleep(1)
