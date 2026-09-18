import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from pg_agmemory.checkpoints import Checkpoints
from pg_agmemory.models import (
    AppendWorkingEvent,
    CheckpointBranch,
    CompactWorking,
    CreateCheckpoint,
    MemoryReference,
    QueryWorkingEvents,
)
from pg_agmemory.processing import Processing
from pg_agmemory.service import MemoryError, MemoryService


class Working:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def branch(self, data: CheckpointBranch) -> dict[str, Any]:
        await self.memory.scope(data.scope_id, "read")
        row = await (
            await self.conn.execute(
                """SELECT b.head_id,b.invalidated,r.effects_invalidated
                   FROM memory.checkpoint_branch b JOIN memory.checkpoint_run r
                   USING (tenant_id,scope_id,run_id)
                   WHERE b.tenant_id=%s AND b.scope_id=%s AND b.run_id=%s AND b.branch_id=%s""",
                (self.tenant, data.scope_id, data.run_id, data.branch_id),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("not_found", 404)
        if row["invalidated"] or row["effects_invalidated"]:
            raise MemoryError("working_invalidated", 409)
        if row["head_id"]:
            await Checkpoints(self.memory).load(row["head_id"])
        return row

    async def append(self, data: AppendWorkingEvent, key: str) -> dict[str, Any]:
        await self.memory.scope(data.scope_id, "write")
        await self.branch(data)
        obj = await self.memory.object(data.source.memory_id)
        if (
            obj["scope_id"] != data.scope_id or obj["kind"] != "episode"
            or data.source.revision != 1
        ):
            raise MemoryError("invalid_processing_reference", 422)
        key_hash, request_hash, previous = await self.memory.replay(
            "working_event", key, data.model_dump_json()
        )
        if previous is not None:
            return previous
        identity = (self.tenant, data.scope_id, data.run_id, data.branch_id)
        await self.conn.execute(
            """SELECT 1 FROM memory.checkpoint_branch
               WHERE tenant_id=%s AND scope_id=%s AND run_id=%s AND branch_id=%s FOR UPDATE""",
            identity,
        )
        duplicate = await (
            await self.conn.execute(
                """SELECT sequence FROM memory.working_event WHERE tenant_id=%s AND scope_id=%s
                   AND run_id=%s AND branch_id=%s AND source_id=%s""",
                (*identity, data.source.memory_id),
            )
        ).fetchone()
        if duplicate:
            sequence = duplicate["sequence"]
        else:
            last = await (
                await self.conn.execute(
                    """SELECT COALESCE(max(sequence),0) AS sequence FROM memory.working_event
                       WHERE tenant_id=%s AND scope_id=%s AND run_id=%s AND branch_id=%s""",
                    identity,
                )
            ).fetchone()
            sequence = last["sequence"] + 1 if last else 1
            if sequence > 1000:
                raise MemoryError("working_event_limit", 422)
            await self.conn.execute(
                """INSERT INTO memory.working_event
                   (tenant_id,scope_id,run_id,branch_id,sequence,source_id)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (*identity, sequence, data.source.memory_id),
            )
            await self.memory.audit("working_event", data.source.memory_id)
        result = data.model_dump(mode="json") | {"sequence": sequence}
        await self.memory.save_result("working_event", key_hash, request_hash, result)
        return result

    async def events(self, data: QueryWorkingEvents) -> dict[str, Any]:
        await self.branch(data)
        rows = await (
            await self.conn.execute(
                """SELECT sequence,source_id FROM memory.working_event
                   WHERE tenant_id=%s AND scope_id=%s AND run_id=%s AND branch_id=%s
                   AND sequence>%s ORDER BY sequence LIMIT %s""",
                (self.tenant, data.scope_id, data.run_id, data.branch_id,
                 data.after_sequence, data.max_items + 1),
            )
        ).fetchall()
        selected = rows[:data.max_items]
        return {
            "events": [
                {"scope_id": data.scope_id, "run_id": data.run_id, "branch_id": data.branch_id,
                 "sequence": row["sequence"],
                 "source": {"memory_id": row["source_id"], "revision": 1}}
                for row in selected
            ],
            "next_after_sequence": selected[-1]["sequence"] if len(rows) > data.max_items else None,
            "consistency": await self.memory.epochs(),
        }

    async def covered(self, data: CompactWorking) -> list[dict[str, Any]]:
        branch = await self.branch(data)
        if branch["head_id"] != data.expected_head:
            raise MemoryError("compaction_conflict", 409)
        if data.through_sequence > 100:
            raise MemoryError("processing_input_limit", 422)
        rows = await (
            await self.conn.execute(
                """SELECT w.sequence,w.source_id,e.content FROM memory.working_event w
                   JOIN memory.episode e ON e.tenant_id=w.tenant_id AND e.id=w.source_id
                   WHERE w.tenant_id=%s AND w.scope_id=%s AND w.run_id=%s AND w.branch_id=%s
                   AND w.sequence<=%s ORDER BY w.sequence""",
                (self.tenant, data.scope_id, data.run_id, data.branch_id, data.through_sequence),
            )
        ).fetchall()
        if [row["sequence"] for row in rows] != list(range(1, data.through_sequence + 1)):
            raise MemoryError("working_invalidated", 409)
        return rows

    async def enqueue(self, data: CompactWorking, key: str) -> dict[str, Any]:
        processing = Processing(self.memory)
        policy, epoch = await processing.policy(data.scope_id, "compact")
        key_hash, request_hash, previous = await self.memory.replay(
            "compact_working", key, data.model_dump_json()
        )
        if previous is not None:
            from pg_agmemory.jobs import Jobs

            await Jobs(self.memory).load(UUID(previous["job_id"]))
            return previous
        rows = await self.covered(data)
        saved = await Checkpoints(self.memory).load(data.expected_head)
        refs = {
            ref["memory_id"]: MemoryReference.model_validate(ref) for ref in saved["memory_refs"]
        }
        if len(refs) != len(saved["memory_refs"]):
            raise MemoryError("invalid_processing_reference", 422)
        for row in rows:
            refs[str(row["source_id"])] = MemoryReference(memory_id=row["source_id"])
        if len(refs) > 100:
            raise MemoryError("processing_input_limit", 422)
        for ref in refs.values():
            await processing.source(data.scope_id, ref, policy)
        refs[str(data.expected_head)] = MemoryReference(memory_id=data.expected_head)
        text = self.event_text(rows)
        if len(text.encode()) > policy.max_input_bytes or len(text) > 65536:
            raise MemoryError("processing_input_limit", 422)
        extra = {
            "compaction": data.model_dump(mode="json", exclude={"retry_of"}),
            "coverage_digest": hashlib.sha256(text.encode()).hexdigest(),
            "checkpoint_checksum": saved["checksum"],
        }
        result = await processing.enqueue_intent(
            data.scope_id, "compact", list(refs.values()), extra, policy, epoch,
            "compact-working-v1:" + key_hash, retry_of=data.retry_of,
        )
        await self.memory.save_result("compact_working", key_hash, request_hash, result)
        return result

    @staticmethod
    def event_text(rows: list[dict[str, Any]]) -> str:
        return json.dumps(
            [{"sequence": row["sequence"], "source_id": str(row["source_id"]),
              "revision": 1, "content": row["content"]} for row in rows],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    async def input(self, payload: dict[str, Any]) -> str:
        data = CompactWorking.model_validate(payload["compaction"])
        rows = await self.covered(data)
        saved = await Checkpoints(self.memory).load(data.expected_head)
        text = self.event_text(rows)
        if (
            saved["checksum"] != payload["checkpoint_checksum"]
            or hashlib.sha256(text.encode()).hexdigest() != payload["coverage_digest"]
        ):
            raise MemoryError("compaction_conflict", 409)
        return text

    async def checksum(self, value: dict[str, Any]) -> str:
        return await self.memory.digest(
            "working-snapshot-v1:" + json.dumps(value, sort_keys=True, separators=(",", ":"))
        )

    async def publish(self, job: dict[str, Any], generated: Any) -> dict[str, Any]:
        from pg_agmemory.providers import SummaryResult

        generated = SummaryResult.model_validate(generated)
        payload = job["payload"]
        await self.input(payload)
        data = CompactWorking.model_validate(payload["compaction"])
        saved = await Checkpoints(self.memory).load(data.expected_head)
        inputs = [
            {"memory_id": str(row["source_id"]), "revision": 1}
            for row in await self.covered(data)
        ]
        refs = [ref for ref in payload["input_refs"] if ref["memory_id"] != str(data.expected_head)]
        request = CreateCheckpoint(
            scope_id=data.scope_id, run_id=data.run_id, branch_id=data.branch_id,
            expected_head=data.expected_head, harness_id=saved["harness_id"],
            harness_version=saved["harness_version"],
            state_schema_version=saved["state_schema_version"],
            event_watermark=saved["event_watermark"], state=saved["state"], memory_refs=refs,
        )
        result = await Checkpoints(self.memory).create(
            request, "compaction-v1:" + str(job["id"]), preserved_state=saved["state"]
        )
        # Legacy checkpoints may omit newer optional typed fields. Preserve the exact saved JSON.
        created = await Checkpoints(self.memory).load(UUID(result["checkpoint_id"]))
        if created["state"] != saved["state"]:
            raise MemoryError("compaction_conflict", 409)
        value = {
            "checkpoint_id": result["checkpoint_id"], "job_id": str(job["id"]),
            "coverage_start": 1, "coverage_end": data.through_sequence,
            "summary": generated.summary, "input_digest": generated.input_digest,
            "input_refs": inputs,
            "model": generated.model.model_dump(), "recipe_version": job["recipe_version"],
        }
        checksum = await self.checksum(value)
        await self.conn.execute(
            """INSERT INTO memory.working_snapshot
               (tenant_id,checkpoint_id,scope_id,job_id,coverage_start,coverage_end,
                summary,input_digest,checksum,model,recipe_version,input_refs)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (self.tenant, result["checkpoint_id"], data.scope_id, job["id"], 1,
             data.through_sequence, generated.summary, generated.input_digest, checksum,
             Jsonb(generated.model.model_dump()), job["recipe_version"], Jsonb(inputs)),
        )
        return {"checkpoint_id": result["checkpoint_id"], "checksum": checksum,
                "coverage_end": data.through_sequence, "status": "untrusted"}

    async def get(self, checkpoint_id: UUID) -> dict[str, Any]:
        saved = await Checkpoints(self.memory).envelope(checkpoint_id)
        row = await (
            await self.conn.execute(
                """SELECT checkpoint_id,job_id,coverage_start,coverage_end,summary,
                          input_digest,checksum,model,recipe_version,input_refs
                   FROM memory.working_snapshot WHERE tenant_id=%s AND checkpoint_id=%s""",
                (self.tenant, checkpoint_id),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("not_found", 404)
        value = dict(row)
        checksum = value.pop("checksum")
        value["checkpoint_id"], value["job_id"] = str(value["checkpoint_id"]), str(value["job_id"])
        if not hmac.compare_digest(checksum, await self.checksum(value)):
            raise MemoryError("working_invalidated", 409)
        epochs = await self.memory.epochs()
        if (
            saved["saved_access_epoch"] != epochs["access_epoch"]
            or saved["saved_deletion_epoch"] != epochs["deletion_epoch"]
        ):
            raise MemoryError("stale_context", 409)
        tail = await self.events(QueryWorkingEvents(
            scope_id=saved["scope_id"], run_id=saved["run_id"], branch_id=saved["branch_id"],
            after_sequence=row["coverage_end"], max_items=100,
        ))
        return {
            "checkpoint": saved, "summary": row["summary"], "status": "untrusted",
            "input_refs": row["input_refs"], "coverage_start": 1,
            "coverage_end": row["coverage_end"], "input_digest": row["input_digest"],
            "checksum": checksum, "model": row["model"], "recipe_version": row["recipe_version"],
            "job_id": row["job_id"], "tail": tail,
        }
