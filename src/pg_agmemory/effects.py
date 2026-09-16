import hmac
import json
from typing import Any, Literal
from uuid import UUID

from pg_agmemory.models import PlanToolEffect, TransitionToolEffect
from pg_agmemory.service import MemoryError, MemoryService

TRANSITIONS = {
    "planned": {"dispatched", "unknown"},
    "dispatched": {"unknown", "confirmed", "failed"},
    "unknown": {"confirmed", "failed"},
    "confirmed": set(),
    "failed": set(),
}


class ToolEffects:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def load(self, effect_id: UUID, permission: str = "read") -> dict[str, Any]:
        obj = await self.memory.object(effect_id, permission)
        if obj["kind"] != "tool_effect":
            raise MemoryError("not_found", 404)
        row = await (
            await self.conn.execute(
                """SELECT e.*, v.status, r.effects_invalidated AS run_invalidated
                   FROM memory.tool_effect e
                   JOIN memory.tool_effect_revision v
                     ON v.tenant_id = e.tenant_id AND v.effect_id = e.id
                       AND v.revision = e.current_revision
                   JOIN memory.checkpoint_run r ON r.tenant_id = e.tenant_id
                     AND r.scope_id = e.scope_id AND r.run_id = e.run_id
                   WHERE e.tenant_id = %s AND e.id = %s""",
                (self.tenant, effect_id),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("effect_invalidated", 409)
        refs = await (
            await self.conn.execute(
                """SELECT source_id,source_revision FROM memory.tool_effect_reference
                   WHERE tenant_id = %s AND effect_id = %s ORDER BY source_id,source_revision""",
                (self.tenant, effect_id),
            )
        ).fetchall()
        if len(refs) != row["reference_count"]:
            raise MemoryError("effect_invalidated", 409)
        row["memory_refs"] = [
            {"memory_id": str(ref["source_id"]), "revision": ref["source_revision"]} for ref in refs
        ]
        return row

    async def get(self, effect_id: UUID) -> dict[str, Any]:
        row = await self.load(effect_id)
        history = await (
            await self.conn.execute(
                """SELECT revision,status,recorded_at,reason,receipt_reference,receipt_source,origin
                   FROM memory.tool_effect_revision
                   WHERE tenant_id = %s AND effect_id = %s ORDER BY revision""",
                (self.tenant, effect_id),
            )
        ).fetchall()
        if len(history) != row["current_revision"]:
            raise MemoryError("effect_invalidated", 409)
        return {
            "memory_id": str(effect_id),
            "scope_id": row["scope_id"],
            "run_id": row["run_id"],
            "operation_id": row["operation_id"],
            "tool_name": row["tool_name"],
            "revision": row["current_revision"],
            "status": row["status"],
            "action_fingerprint": row["action_fingerprint"],
            "external_idempotency_key": row["external_idempotency_key"],
            "run_invalidated": row["run_invalidated"],
            "memory_refs": row["memory_refs"],
            "history": history,
        }

    async def plan(self, data: PlanToolEffect, key: str) -> dict[str, Any]:
        await self.memory.scope(data.scope_id, "write")
        key_hash, request_hash, previous = await self.memory.replay(
            "plan_tool_effect", key, data.model_dump_json()
        )
        if previous is not None:
            await self.load(UUID(previous["memory_id"]), "write")
            return previous
        identity = await (
            await self.conn.execute(
                """SELECT effect_id,request_digest FROM memory_ops.tool_effect_identity
                   WHERE tenant_id = %s AND scope_id = %s AND run_id = %s AND operation_id = %s""",
                (self.tenant, data.scope_id, data.run_id, data.operation_id),
            )
        ).fetchone()
        if identity is not None:
            if not hmac.compare_digest(identity["request_digest"], request_hash):
                raise MemoryError("operation_conflict", 409)
            await self.load(identity["effect_id"], "write")
            result = {"memory_id": str(identity["effect_id"]), "revision": 1, "status": "planned"}
        else:
            run = await (
                await self.conn.execute(
                    """SELECT effects_invalidated FROM memory.checkpoint_run
                       WHERE tenant_id = %s AND scope_id = %s AND run_id = %s""",
                    (self.tenant, data.scope_id, data.run_id),
                )
            ).fetchone()
            if run is None:
                raise MemoryError("not_found", 404)
            if run["effects_invalidated"]:
                raise MemoryError("effect_run_invalidated", 409)
            current = await self.list_run(data.scope_id, data.run_id)
            if len(current) >= 100:
                raise MemoryError("effect_limit_exceeded", 422)
            refs = await self.memory.validate_refs(
                data.scope_id, data.memory_refs, "invalid_effect_reference"
            )
            fingerprint = await self.memory.digest("effect-action-v1:" + data.action_hash)
            external_key = await self.memory.digest(
                "effect-dispatch-v1:"
                + json.dumps(
                    [str(data.scope_id), str(data.run_id), str(data.operation_id), fingerprint]
                )
            )
            effect_id = await self.memory.new_object(data.scope_id, "tool_effect")
            await self.conn.execute(
                """INSERT INTO memory.tool_effect
                   (tenant_id,id,scope_id,run_id,operation_id,tool_name,action_fingerprint,
                    external_idempotency_key,reference_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    self.tenant,
                    effect_id,
                    data.scope_id,
                    data.run_id,
                    data.operation_id,
                    data.tool_name,
                    fingerprint,
                    external_key,
                    len(refs),
                ),
            )
            for ref in refs:
                await self.conn.execute(
                    """INSERT INTO memory.tool_effect_reference
                       (tenant_id,effect_id,scope_id,source_id,source_revision,source_kind)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (
                        self.tenant,
                        effect_id,
                        data.scope_id,
                        ref["memory_id"],
                        ref["revision"],
                        ref["kind"],
                    ),
                )
            await self.conn.execute(
                """INSERT INTO memory_ops.tool_effect_identity
                   (tenant_id,scope_id,run_id,operation_id,effect_id,request_digest)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    self.tenant,
                    data.scope_id,
                    data.run_id,
                    data.operation_id,
                    effect_id,
                    request_hash,
                ),
            )
            result = await self.append(effect_id, 1, "planned", "Intent recorded", "api")
        await self.memory.save_result("plan_tool_effect", key_hash, request_hash, result)
        return result

    async def append(
        self,
        effect_id: UUID,
        revision: int,
        status: str,
        reason: str,
        origin: Literal["api", "checkpoint_restore"],
        receipt_reference: str | None = None,
        receipt_source: str | None = None,
    ) -> dict[str, Any]:
        await self.conn.execute(
            """INSERT INTO memory.tool_effect_revision
               (tenant_id,effect_id,revision,status,reason,origin,receipt_reference,receipt_source)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                self.tenant,
                effect_id,
                revision,
                status,
                reason,
                origin,
                receipt_reference,
                receipt_source,
            ),
        )
        await self.memory.audit("tool_effect_" + status, effect_id)
        return {"memory_id": str(effect_id), "revision": revision, "status": status}

    async def transition(
        self, effect_id: UUID, data: TransitionToolEffect, key: str
    ) -> dict[str, Any]:
        row = await self.load(effect_id, "write")
        key_hash, request_hash, previous = await self.memory.replay(
            "transition_tool_effect",
            key,
            json.dumps(
                {"memory_id": str(effect_id), "request": data.model_dump(mode="json")},
                sort_keys=True,
            ),
        )
        if previous is not None:
            return previous
        if data.expected_revision != row["current_revision"]:
            raise MemoryError("revision_conflict", 409)
        if data.status not in TRANSITIONS[row["status"]]:
            raise MemoryError("effect_transition_conflict", 409)
        if data.status == "dispatched" and row["run_invalidated"]:
            raise MemoryError("effect_run_invalidated", 409)
        result = await self.append(
            effect_id,
            data.expected_revision + 1,
            data.status,
            data.reason,
            "api",
            data.receipt_reference,
            data.receipt_source,
        )
        await self.memory.save_result("transition_tool_effect", key_hash, request_hash, result)
        return result

    async def list_run(self, scope_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
        return await (
            await self.conn.execute(
                """SELECT e.id AS memory_id,e.operation_id,v.revision,v.status
                   FROM memory.tool_effect e JOIN memory.tool_effect_revision v
                     ON v.tenant_id = e.tenant_id AND v.effect_id = e.id
                       AND v.revision = e.current_revision
                   WHERE e.tenant_id = %s AND e.scope_id = %s AND e.run_id = %s
                   ORDER BY e.operation_id""",
                (self.tenant, scope_id, run_id),
            )
        ).fetchall()

    async def fence_restore(self, scope_id: UUID, run_id: UUID) -> None:
        for effect in await self.list_run(scope_id, run_id):
            if effect["status"] == "dispatched":
                await self.append(
                    effect["memory_id"],
                    effect["revision"] + 1,
                    "unknown",
                    "Reconciliation required by checkpoint restore",
                    "checkpoint_restore",
                )
