import hmac
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from pg_agmemory.models import CancelJob, EnqueueJob, JobError, QueryJobs, Remember
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection
from pg_agmemory.transactions import async_transaction

RECIPE = "structured-remember-v1"


@asynccontextmanager
async def job_transaction(url: str, subject: str) -> AsyncIterator["Jobs"]:
    async with principal_connection(url, subject) as (conn, identity):
        async with async_transaction(conn):
            await bind_identity(conn, subject, identity)
            yield Jobs(MemoryService(conn, identity))


class Jobs:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def load(
        self, job_id: UUID, permission: str = "read", *, lock: bool = False
    ) -> dict[str, Any]:
        obj = await self.memory.object(job_id, permission)
        if obj["kind"] != "job":
            raise MemoryError("not_found", 404)
        query = "SELECT * FROM memory_ops.job WHERE tenant_id = %s AND id = %s"
        if lock:
            query += " FOR UPDATE"
        row = await (await self.conn.execute(query, (self.tenant, job_id))).fetchone()
        if row is None:
            raise MemoryError("job_invalidated", 409)
        inputs = await (
            await self.conn.execute(
                """SELECT source_id,source_revision FROM memory_ops.job_input
                   WHERE tenant_id = %s AND job_id = %s ORDER BY source_id""",
                (self.tenant, job_id),
            )
        ).fetchall()
        if len(inputs) != row["reference_count"]:
            raise MemoryError("job_invalidated", 409)
        row["input_refs"] = [
            {"memory_id": item["source_id"], "revision": item["source_revision"]} for item in inputs
        ]
        return row

    async def get(self, job_id: UUID) -> dict[str, Any]:
        row = await self.load(job_id)
        if row["result_id"] is not None:
            await self.memory.object(row["result_id"])
        result = {
            "job_id": job_id,
            "kind": row["kind"],
            "recipe_version": row["recipe_version"],
            "retry_of": row["retry_of"],
            "state": row["state"],
            "attempt": row["attempt"],
            "available_at": row["available_at"],
            "lease_until": row["lease_until"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "error_code": row["error_code"],
            "input_refs": row["input_refs"],
            "result": {"memory_id": row["result_id"], "revision": 1}
            if row["result_id"] is not None
            else None,
        }
        if row["kind"] != "structured_remember":
            published = row["processing_result"]
            if published:
                for ref in published.get("assertions", []):
                    await self.memory.object(UUID(ref["memory_id"]))
                if published.get("checkpoint_id"):
                    from pg_agmemory.checkpoints import Checkpoints

                    await Checkpoints(self.memory).load(UUID(published["checkpoint_id"]))
            result["processing_result"] = published
            result["call"] = await (
                await self.conn.execute(
                    """SELECT outcome,billing_unknown,input_bytes,max_output_tokens
                       FROM memory_ops.model_call WHERE tenant_id=%s AND job_id=%s""",
                    (self.tenant, job_id),
                )
            ).fetchone()
        return result

    async def query(self, data: QueryJobs) -> dict[str, Any]:
        rows = await (
            await self.conn.execute(
                """SELECT id,scope_id,created_at FROM memory_ops.job
                   WHERE tenant_id=%(tenant)s AND principal_id=%(principal)s
                     AND scope_id=ANY(%(scopes)s)
                     AND (%(all_states)s OR state=ANY(%(states)s::text[]))
                     AND (%(before_time)s::timestamptz IS NULL
                          OR (created_at,id)<(%(before_time)s,%(before_id)s::uuid))
                   ORDER BY created_at DESC,id DESC LIMIT %(limit)s""",
                {
                    "tenant": self.tenant,
                    "principal": self.memory.principal,
                    "scopes": data.scope_ids,
                    "states": data.states,
                    "all_states": not data.states,
                    "before_time": data.before.created_at if data.before else None,
                    "before_id": data.before.job_id if data.before else None,
                    "limit": data.max_items + 1,
                },
            )
        ).fetchall()
        selected = rows[: data.max_items]
        jobs = [
            {**await self.get(row["id"]), "scope_id": row["scope_id"]}
            for row in selected
        ]
        return {
            "jobs": jobs,
            "next_cursor": {
                "created_at": selected[-1]["created_at"],
                "job_id": selected[-1]["id"],
            }
            if len(rows) > data.max_items
            else None,
            "consistency": await self.memory.epochs(),
        }

    async def enqueue(
        self, data: EnqueueJob, key: str, retry_of: UUID | None = None
    ) -> dict[str, Any]:
        await self.memory.scope(data.memory.scope_id, "write")
        operation = "enqueue_job" if retry_of is None else "retry_job"
        key_hash, request_hash, previous = await self.memory.replay(
            operation,
            key,
            json.dumps(
                {
                    "retry_of": str(retry_of) if retry_of else None,
                    "request": data.model_dump(mode="json"),
                },
                sort_keys=True,
            ),
        )
        if previous is not None:
            return previous
        canonical = data.memory.model_dump(mode="json")
        canonical["evidence"] = sorted(canonical["evidence"], key=lambda item: item["memory_id"])
        intent = await self.memory.digest(RECIPE + ":" + json.dumps(canonical, sort_keys=True))
        if retry_of is not None:
            parent = await self.load(retry_of, "write")
            if parent["principal_id"] != self.memory.principal:
                raise MemoryError("not_found", 404)
            if parent["state"] != "failed":
                raise MemoryError("job_retry_conflict", 409)
            if not hmac.compare_digest(parent["intent_digest"], intent):
                raise MemoryError("job_intent_conflict", 409)
        digest = await self.memory.digest("job-identity-v1:" + intent + ":" + str(retry_of))
        duplicate = await (
            await self.conn.execute(
                """SELECT job_id FROM memory_ops.job_identity WHERE tenant_id = %s
                   AND scope_id = %s AND principal_id = %s AND input_digest = %s""",
                (self.tenant, data.memory.scope_id, self.memory.principal, digest),
            )
        ).fetchone()
        if duplicate is not None:
            await self.load(duplicate["job_id"], "write")
            result = {"job_id": str(duplicate["job_id"])}
        else:
            count = await (
                await self.conn.execute(
                    """SELECT count(*) AS total FROM memory_ops.job WHERE tenant_id = %s
                       AND scope_id = %s AND state IN ('pending','running')""",
                    (self.tenant, data.memory.scope_id),
                )
            ).fetchone()
            if count and count["total"] >= 100:
                raise MemoryError("job_limit_exceeded", 422)
            await self.memory.validate_evidence(data.memory.scope_id, data.memory.evidence)
            job_id = await self.memory.new_object(data.memory.scope_id, "job")
            await self.conn.execute(
                """INSERT INTO memory_ops.job
                   (tenant_id,id,scope_id,principal_id,kind,recipe_version,intent_digest,retry_of,
                    payload,reference_count,captured_access_epoch,captured_deletion_epoch)
                   SELECT id,%s,%s,%s,'structured_remember',%s,%s,%s,%s,%s,
                          access_epoch,deletion_epoch FROM memory.tenant WHERE id = %s""",
                (
                    job_id,
                    data.memory.scope_id,
                    self.memory.principal,
                    RECIPE,
                    intent,
                    retry_of,
                    Jsonb(data.memory.model_dump(mode="json")),
                    len(data.memory.evidence),
                    self.tenant,
                ),
            )
            for evidence in data.memory.evidence:
                await self.conn.execute(
                    """INSERT INTO memory_ops.job_input(tenant_id,job_id,scope_id,source_id)
                       VALUES (%s,%s,%s,%s)""",
                    (self.tenant, job_id, data.memory.scope_id, evidence.memory_id),
                )
            await self.conn.execute(
                """INSERT INTO memory_ops.job_identity
                   (tenant_id,scope_id,principal_id,input_digest,job_id) VALUES (%s,%s,%s,%s,%s)""",
                (self.tenant, data.memory.scope_id, self.memory.principal, digest, job_id),
            )
            await self.memory.audit(operation, job_id)
            result = {"job_id": str(job_id)}
        await self.memory.save_result(operation, key_hash, request_hash, result)
        return result

    async def cancel(self, job_id: UUID, data: CancelJob, key: str) -> dict[str, Any]:
        row = await self.load(job_id, "write")
        if row["principal_id"] != self.memory.principal:
            raise MemoryError("not_found", 404)
        key_hash, request_hash, previous = await self.memory.replay(
            "cancel_job", key,
            json.dumps({"job_id": str(job_id), "request": data.model_dump(mode="json")},
                       sort_keys=True),
        )
        if previous is not None:
            return previous
        cancelled = await (
            await self.conn.execute(
                """UPDATE memory_ops.job SET state = 'cancelled',payload = NULL,
                   lease_token = NULL,lease_until = NULL,error_code = NULL
                   WHERE tenant_id = %s AND id = %s AND principal_id = %s
                     AND state = %s AND attempt = %s RETURNING id""",
                (self.tenant, job_id, self.memory.principal,
                 data.expected_state, data.expected_attempt),
            )
        ).fetchone()
        if cancelled is None:
            raise MemoryError("job_cancel_conflict", 409)
        await self.memory.audit("job_cancelled", job_id)
        result = {"job_id": str(job_id)}
        if row["kind"] != "structured_remember":
            result.update(kind=row["kind"], recipe_version=row["recipe_version"])
        await self.memory.save_result("cancel_job", key_hash, request_hash, result)
        return result

    async def claim(
        self, lease_seconds: int = 30, *, profile_digest: str | None = None
    ) -> dict[str, Any] | None:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be an integer between 1 and 300")
        row = await (
            await self.conn.execute(
                """SELECT id FROM memory_ops.job WHERE tenant_id = %s AND principal_id = %s
                   AND memory.permitted(scope_id,'write')
                   AND (kind='structured_remember'
                        OR (%s::text IS NOT NULL AND payload->>'profile_digest'=%s))
                   AND ((state = 'pending' AND available_at <= clock_timestamp())
                        OR (state = 'running' AND lease_until <= clock_timestamp()))
                   ORDER BY available_at,created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (self.tenant, self.memory.principal, profile_digest, profile_digest),
            )
        ).fetchone()
        if row is None:
            return None
        job = await self.load(row["id"], "write")
        if job["kind"] != "structured_remember":
            call = await (
                await self.conn.execute(
                    "SELECT 1 FROM memory_ops.model_call WHERE tenant_id=%s AND job_id=%s",
                    (self.tenant, row["id"]),
                )
            ).fetchone()
            if call:
                await self.conn.execute(
                    """UPDATE memory_ops.job SET state='failed',payload=NULL,
                       lease_token=NULL,lease_until=NULL,error_code='billing_unknown'
                       WHERE tenant_id=%s AND id=%s""", (self.tenant, row["id"]),
                )
                await self.memory.audit("job_failed", row["id"])
                return {"job_id": row["id"], "state": "failed"}
        if job["attempt"] == 5:
            await self.conn.execute(
                """UPDATE memory_ops.job SET state = 'failed',payload = NULL,
                   lease_token = NULL,lease_until = NULL,error_code = 'attempt_limit'
                   WHERE tenant_id = %s AND id = %s""",
                (self.tenant, row["id"]),
            )
            await self.memory.audit("job_failed", row["id"])
            return {"job_id": row["id"], "state": "failed"}
        claimed = await (
            await self.conn.execute(
                """UPDATE memory_ops.job j SET state = 'running',attempt = attempt + 1,
                   lease_token = %s,lease_until = clock_timestamp() + %s * interval '1 second',
                   captured_access_epoch = t.access_epoch,
                   captured_deletion_epoch = t.deletion_epoch,
                   error_code = NULL FROM memory.tenant t
                   WHERE j.tenant_id = t.id AND j.tenant_id = %s AND j.id = %s
                   RETURNING j.id AS job_id,j.state,j.payload,j.lease_token,j.attempt,j.kind""",
                (uuid4(), lease_seconds, self.tenant, row["id"]),
            )
        ).fetchone()
        if claimed is None:
            raise MemoryError("job_invalidated", 409)
        await self.memory.audit("job_claimed", row["id"])
        return claimed

    async def lease(self, job_id: UUID, token: UUID, *, epochs: bool = True) -> dict[str, Any]:
        row = await self.load(job_id, "write", lock=True)
        if row["principal_id"] != self.memory.principal:
            raise MemoryError("not_found", 404)
        current = await (
            await self.conn.execute(
                """SELECT clock_timestamp() AS now,access_epoch,deletion_epoch
                   FROM memory.tenant WHERE id = %s""",
                (self.tenant,),
            )
        ).fetchone()
        if (
            not current
            or row["state"] != "running"
            or row["lease_token"] != token
            or row["lease_until"] <= current["now"]
        ):
            raise MemoryError("job_lease_conflict", 409)
        if epochs and (
            row["captured_access_epoch"] != current["access_epoch"]
            or row["captured_deletion_epoch"] != current["deletion_epoch"]
        ):
            raise MemoryError("stale_context", 409)
        return row

    async def heartbeat(self, job_id: UUID, token: UUID) -> None:
        await self.lease(job_id, token)
        await self.conn.execute(
            """UPDATE memory_ops.job SET lease_until =
               GREATEST(lease_until + interval '1 microsecond',
                        clock_timestamp() + interval '30 seconds')
               WHERE tenant_id = %s AND id = %s""",
            (self.tenant, job_id),
        )

    async def publish(self, job_id: UUID, token: UUID, prepared: Remember) -> dict[str, Any]:
        row = await self.lease(job_id, token)
        if prepared.model_dump(mode="json") != row["payload"]:
            raise MemoryError("job_intent_conflict", 409)
        result = await self.memory.publish_assertion(prepared)
        saved = await (
            await self.conn.execute(
                """UPDATE memory_ops.job SET state = 'succeeded',result_id = %s,payload = NULL,
                   lease_token = NULL,lease_until = NULL,error_code = NULL
                   WHERE tenant_id = %s AND id = %s AND lease_token = %s
                     AND lease_until > clock_timestamp() RETURNING id""",
                (UUID(result["memory_id"]), self.tenant, job_id, token),
            )
        ).fetchone()
        if saved is None:
            raise MemoryError("job_lease_conflict", 409)
        await self.memory.audit("job_succeeded", job_id)
        return result

    async def fail(self, job_id: UUID, token: UUID, code: JobError, *, retry: bool) -> str:
        row = await self.lease(job_id, token, epochs=False)
        state = "pending" if retry and row["attempt"] < 5 else "failed"
        delay = 2 ** row["attempt"] + secrets.randbelow(1000) / 1000
        await self.conn.execute(
            """UPDATE memory_ops.job SET state = %s,
               payload = CASE WHEN %s = 'failed' THEN NULL ELSE payload END,
               lease_token = NULL,lease_until = NULL,error_code = %s,
               available_at = clock_timestamp() + %s * interval '1 second'
               WHERE tenant_id = %s AND id = %s""",
            (state, state, code, delay, self.tenant, job_id),
        )
        await self.memory.audit("job_" + state, job_id)
        return state
