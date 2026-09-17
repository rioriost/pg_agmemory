import asyncio
import json
import logging
from typing import Any

import psycopg
from pydantic import ValidationError

from pg_agmemory.database import validate_runtime
from pg_agmemory.jobs import job_transaction
from pg_agmemory.models import JobError, Remember
from pg_agmemory.service import MemoryError

logger = logging.getLogger("pg_agmemory.worker")


async def run_once(url: str, subject: str) -> dict[str, Any]:
    async with job_transaction(url, subject) as jobs:
        claim = await jobs.claim()
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
    except (psycopg.OperationalError, psycopg.errors.QueryCanceled):
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


async def run(url: str, subject: str, *, once: bool = False) -> None:
    await validate_runtime(url)
    while True:
        try:
            result = await run_once(url, subject)
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
