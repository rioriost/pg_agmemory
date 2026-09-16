import hmac
import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from pg_agmemory.models import (
    CheckpointState,
    CreateCheckpoint,
    MemoryReference,
    RestoreCheckpoint,
)
from pg_agmemory.service import MemoryError, MemoryService


class Checkpoints:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def epochs(self) -> dict[str, Any]:
        row = await (
            await self.conn.execute(
                "SELECT access_epoch, deletion_epoch FROM memory.tenant WHERE id = %s",
                (self.tenant,),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("not_found", 404)
        return row

    async def validate_refs(
        self, scope_id: UUID, refs: list[MemoryReference]
    ) -> list[dict[str, Any]]:
        result = []
        for ref in sorted(refs, key=lambda ref: (str(ref.memory_id), ref.revision)):
            obj = await self.memory.object(ref.memory_id)
            if obj["scope_id"] != scope_id or obj["kind"] not in ("episode", "assertion"):
                raise MemoryError("invalid_checkpoint_reference", 422)
            if obj["kind"] == "episode":
                exists = ref.revision == 1
            else:
                exists = (
                    await (
                        await self.conn.execute(
                            """SELECT 1 FROM memory.assertion_revision
                           WHERE tenant_id = %s AND assertion_id = %s AND revision = %s""",
                            (self.tenant, ref.memory_id, ref.revision),
                        )
                    ).fetchone()
                    is not None
                )
            if not exists:
                raise MemoryError("invalid_checkpoint_reference", 422)
            result.append(
                {
                    "memory_id": str(ref.memory_id),
                    "revision": ref.revision,
                    "kind": obj["kind"],
                }
            )
        return result

    async def checksum(self, payload: dict[str, Any]) -> str:
        return await self.memory.digest(
            "checkpoint-envelope-v1:"
            + json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        )

    async def load(self, checkpoint_id: UUID, permission: str = "read") -> dict[str, Any]:
        obj = await self.memory.object(checkpoint_id, permission)
        if obj["kind"] != "checkpoint":
            raise MemoryError("not_found", 404)
        row = await (
            await self.conn.execute(
                """SELECT c.*, r.harness_id, r.harness_version, r.state_schema_version
                   FROM memory.checkpoint c JOIN memory.checkpoint_run r
                     USING (tenant_id, scope_id, run_id)
                   WHERE c.tenant_id = %s AND c.id = %s""",
                (self.tenant, checkpoint_id),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("checkpoint_invalidated", 409)
        refs = await (
            await self.conn.execute(
                """SELECT source_id, source_revision FROM memory.checkpoint_reference
                   WHERE tenant_id = %s AND checkpoint_id = %s
                   ORDER BY source_id, source_revision""",
                (self.tenant, checkpoint_id),
            )
        ).fetchall()
        if len(refs) != row["reference_count"]:
            raise MemoryError("checkpoint_invalidated", 409)
        payload = {
            "checkpoint_id": str(checkpoint_id),
            "scope_id": str(row["scope_id"]),
            "run_id": str(row["run_id"]),
            "branch_id": str(row["branch_id"]),
            "sequence": row["sequence"],
            "parent_checkpoint": str(row["parent_id"]) if row["parent_id"] else None,
            "harness_id": row["harness_id"],
            "harness_version": row["harness_version"],
            "state_schema_version": row["state_schema_version"],
            "event_watermark": row["event_watermark"],
            "state": row["state"],
            "memory_refs": [
                {"memory_id": str(ref["source_id"]), "revision": ref["source_revision"]}
                for ref in refs
            ],
            "saved_access_epoch": row["access_epoch"],
            "saved_deletion_epoch": row["deletion_epoch"],
        }
        if not hmac.compare_digest(row["checksum"], await self.checksum(payload)):
            raise MemoryError("checkpoint_invalidated", 409)
        try:
            CheckpointState.model_validate(row["state"])
        except ValidationError as exc:
            raise MemoryError("checkpoint_invalidated", 409) from exc
        return {**payload, "checksum": row["checksum"], "checksum_algorithm": "hmac-sha256-v1"}

    async def envelope(self, checkpoint_id: UUID) -> dict[str, Any]:
        saved = await self.load(checkpoint_id)
        epochs = await self.epochs()
        unresolved = [
            effect["operation_id"]
            for effect in saved["state"]["pending_effects"]
            if effect["status"] in ("dispatched", "unknown")
        ]
        return {
            **saved,
            "current_access_epoch": epochs["access_epoch"],
            "current_deletion_epoch": epochs["deletion_epoch"],
            "requires_reconciliation": unresolved,
            "resume_allowed": not unresolved,
            "automatic_reexecution": False,
        }

    async def create(self, data: CreateCheckpoint, key: str) -> dict[str, Any]:
        await self.memory.scope(data.scope_id, "write")
        key_hash, payload_hash, previous = await self.memory.replay(
            "checkpoint", key, data.model_dump_json()
        )
        if previous is not None:
            await self.load(UUID(previous["checkpoint_id"]), "write")
            return previous
        run_key = (self.tenant, data.scope_id, data.run_id)
        await self.conn.execute(
            """INSERT INTO memory.checkpoint_run
               (tenant_id,scope_id,run_id,harness_id,harness_version,state_schema_version)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (*run_key, data.harness_id, data.harness_version, data.state_schema_version),
        )
        run = await (
            await self.conn.execute(
                """SELECT harness_id,harness_version,state_schema_version FROM memory.checkpoint_run
                   WHERE tenant_id = %s AND scope_id = %s AND run_id = %s""",
                run_key,
            )
        ).fetchone()
        if not run or run != {
            "harness_id": data.harness_id,
            "harness_version": data.harness_version,
            "state_schema_version": data.state_schema_version,
        }:
            raise MemoryError("checkpoint_harness_conflict", 409)
        await self.conn.execute(
            """INSERT INTO memory.checkpoint_branch(tenant_id,scope_id,run_id,branch_id)
               VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (*run_key, data.branch_id),
        )
        branch = await (
            await self.conn.execute(
                """SELECT head_id,sequence,invalidated FROM memory.checkpoint_branch
                   WHERE tenant_id = %s AND scope_id = %s AND run_id = %s AND branch_id = %s
                   FOR UPDATE""",
                (*run_key, data.branch_id),
            )
        ).fetchone()
        if branch is None:
            raise MemoryError("not_found", 404)
        if branch["invalidated"]:
            raise MemoryError("checkpoint_invalidated", 409)
        if branch["head_id"] != data.expected_head:
            raise MemoryError("checkpoint_head_conflict", 409)
        if data.expected_head is not None:
            parent = await self.load(data.expected_head)
            if data.event_watermark < parent["event_watermark"]:
                raise MemoryError("checkpoint_watermark_conflict", 409)
        result = await self.insert(data, branch["sequence"] + 1, data.expected_head)
        await self.memory.save_result("checkpoint", key_hash, payload_hash, result)
        return result

    async def insert(
        self, data: CreateCheckpoint, sequence: int, parent_id: UUID | None
    ) -> dict[str, Any]:
        refs = await self.validate_refs(data.scope_id, data.memory_refs)
        epochs = await self.epochs()
        checkpoint_id = await self.memory.new_object(data.scope_id, "checkpoint")
        payload = {
            "checkpoint_id": str(checkpoint_id),
            "scope_id": str(data.scope_id),
            "run_id": str(data.run_id),
            "branch_id": str(data.branch_id),
            "sequence": sequence,
            "parent_checkpoint": str(parent_id) if parent_id else None,
            "harness_id": data.harness_id,
            "harness_version": data.harness_version,
            "state_schema_version": data.state_schema_version,
            "event_watermark": data.event_watermark,
            "state": data.state.model_dump(mode="json"),
            "memory_refs": [
                {"memory_id": ref["memory_id"], "revision": ref["revision"]} for ref in refs
            ],
            "saved_access_epoch": epochs["access_epoch"],
            "saved_deletion_epoch": epochs["deletion_epoch"],
        }
        checksum = await self.checksum(payload)
        await self.conn.execute(
            """INSERT INTO memory.checkpoint
               (tenant_id,id,scope_id,run_id,branch_id,sequence,parent_id,state,
                event_watermark,reference_count,access_epoch,deletion_epoch,checksum)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                self.tenant,
                checkpoint_id,
                data.scope_id,
                data.run_id,
                data.branch_id,
                sequence,
                parent_id,
                Jsonb(payload["state"]),
                data.event_watermark,
                len(refs),
                epochs["access_epoch"],
                epochs["deletion_epoch"],
                checksum,
            ),
        )
        for ref in refs:
            await self.conn.execute(
                """INSERT INTO memory.checkpoint_reference
                   (tenant_id,checkpoint_id,scope_id,source_id,source_revision,source_kind)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    self.tenant,
                    checkpoint_id,
                    data.scope_id,
                    ref["memory_id"],
                    ref["revision"],
                    ref["kind"],
                ),
            )
        await self.memory.audit("checkpoint", checkpoint_id)
        return {
            key: payload[key]
            for key in ("checkpoint_id", "run_id", "branch_id", "sequence", "parent_checkpoint")
        } | {"checksum": checksum, "checksum_algorithm": "hmac-sha256-v1"}

    async def restore(self, data: RestoreCheckpoint, key: str) -> dict[str, Any]:
        saved = await self.load(data.checkpoint_id, "write")
        key_hash, payload_hash, previous = await self.memory.replay(
            "restore_checkpoint", key, data.model_dump_json()
        )
        if previous is not None:
            return await self.envelope(UUID(previous["checkpoint_id"]))
        if (saved["harness_id"], saved["harness_version"], saved["state_schema_version"]) != (
            data.harness_id,
            data.harness_version,
            data.state_schema_version,
        ):
            raise MemoryError("checkpoint_incompatible", 422)
        inserted = await (
            await self.conn.execute(
                """INSERT INTO memory.checkpoint_branch(tenant_id,scope_id,run_id,branch_id)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING branch_id""",
                (self.tenant, saved["scope_id"], saved["run_id"], data.target_branch_id),
            )
        ).fetchone()
        if inserted is None:
            raise MemoryError("checkpoint_branch_conflict", 409)
        state = CheckpointState.model_validate(saved["state"])
        for effect in state.pending_effects:
            if effect.status == "dispatched":
                effect.status = "unknown"
        fork = CreateCheckpoint(
            scope_id=saved["scope_id"],
            run_id=saved["run_id"],
            branch_id=data.target_branch_id,
            expected_head=None,
            harness_id=data.harness_id,
            harness_version=data.harness_version,
            state_schema_version=data.state_schema_version,
            event_watermark=saved["event_watermark"],
            state=state,
            memory_refs=saved["memory_refs"],
        )
        result = await self.insert(fork, 1, data.checkpoint_id)
        await self.memory.save_result("restore_checkpoint", key_hash, payload_hash, result)
        await self.memory.audit("restore_checkpoint", UUID(result["checkpoint_id"]))
        return await self.envelope(UUID(result["checkpoint_id"]))
