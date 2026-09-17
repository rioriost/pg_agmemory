import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pg_agmemory.models import (
    CreateEntity,
    CreateRelation,
    EntitySummary,
    ExpandGraph,
    GraphEdge,
    GraphPath,
    MemoryReference,
    QueryEntities,
    Remember,
    ReviseAssertion,
    ReviseRelation,
)
from pg_agmemory.service import MemoryError, MemoryService


class SqlGraph:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def entity(self, entity_id: UUID) -> dict[str, Any]:
        obj = await self.memory.object(entity_id)
        if obj["kind"] != "entity":
            raise MemoryError("not_found", 404)
        row = await (
            await self.conn.execute(
                """SELECT id AS memory_id,scope_id,entity_type,canonical_label,reference_count
                   FROM memory.entity WHERE tenant_id = %s AND id = %s""",
                (self.tenant, entity_id),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("entity_invalidated", 409)
        evidence = await (
            await self.conn.execute(
                """SELECT e.source_id AS memory_id,e.quote,p.occurred_at
                   FROM memory.entity_evidence e JOIN memory.episode p
                     ON p.tenant_id = e.tenant_id AND p.id = e.source_id
                   WHERE e.tenant_id = %s AND e.entity_id = %s ORDER BY e.source_id""",
                (self.tenant, entity_id),
            )
        ).fetchall()
        if len(evidence) != row.pop("reference_count"):
            raise MemoryError("entity_invalidated", 409)
        return {**row, "revision": 1, "recorded_at": obj["created_at"], "evidence": evidence}

    async def query_entities(self, data: QueryEntities) -> dict[str, Any]:
        rows = await (
            await self.conn.execute(
                """SELECT e.id AS memory_id,e.scope_id,e.entity_type,e.canonical_label,
                          o.created_at AS recorded_at,e.reference_count,
                          (SELECT count(*) FROM memory.entity_evidence ee
                           JOIN memory.episode p ON p.tenant_id=ee.tenant_id AND p.id=ee.source_id
                           WHERE ee.tenant_id=e.tenant_id AND ee.entity_id=e.id) AS evidence_count
                   FROM memory.entity e JOIN memory.object o USING (tenant_id,id)
                   WHERE e.tenant_id=%(tenant)s AND e.scope_id=ANY(%(scopes)s)
                     AND (%(type)s::text IS NULL OR e.entity_type=%(type)s)
                     AND (%(label)s::text IS NULL
                          OR e.canonical_label COLLATE "C"=%(label)s::text COLLATE "C")
                     AND (%(before_time)s::timestamptz IS NULL
                          OR (o.created_at,e.id)<(%(before_time)s,%(before_id)s::uuid))
                   ORDER BY o.created_at DESC,e.id DESC LIMIT %(limit)s""",
                {
                    "tenant": self.tenant,
                    "scopes": data.scope_ids,
                    "type": data.entity_type,
                    "label": data.canonical_label,
                    "before_time": data.before.recorded_at if data.before else None,
                    "before_id": data.before.memory_id if data.before else None,
                    "limit": data.max_items + 1,
                },
            )
        ).fetchall()
        entities = []
        for row in rows[: data.max_items]:
            if row.pop("evidence_count") != row.pop("reference_count"):
                raise MemoryError("entity_invalidated", 409)
            entities.append(EntitySummary.model_validate(row))
        return {
            "entities": entities,
            "next_cursor": {
                "recorded_at": entities[-1].recorded_at,
                "memory_id": entities[-1].memory_id,
            }
            if len(rows) > data.max_items
            else None,
            "consistency": await self.memory.epochs(),
        }

    async def create_entity(self, data: CreateEntity, key: str) -> dict[str, Any]:
        await self.memory.scope(data.scope_id, "write")
        key_hash, request_hash, previous = await self.memory.replay(
            "create_entity", key, data.model_dump_json()
        )
        if previous is not None:
            await self.entity(UUID(previous["memory_id"]))
            return previous
        await self.memory.validate_evidence(data.scope_id, data.evidence)
        entity_id = await self.memory.new_object(data.scope_id, "entity")
        await self.conn.execute(
            """INSERT INTO memory.entity
               (tenant_id,id,scope_id,entity_type,canonical_label,reference_count,explicit_intent)
               VALUES (%s,%s,%s,%s,%s,%s,true)""",
            (
                self.tenant,
                entity_id,
                data.scope_id,
                data.entity_type,
                data.canonical_label,
                len(data.evidence),
            ),
        )
        for evidence in data.evidence:
            await self.conn.execute(
                """INSERT INTO memory.entity_evidence(tenant_id,entity_id,scope_id,source_id,quote)
                   VALUES (%s,%s,%s,%s,%s)""",
                (self.tenant, entity_id, data.scope_id, evidence.memory_id, evidence.quote),
            )
        result = {"memory_id": str(entity_id), "revision": 1}
        await self.memory.audit("create_entity", entity_id)
        await self.memory.save_result("create_entity", key_hash, request_hash, result)
        return result

    async def endpoint(self, entity_id: UUID, scope_id: UUID) -> dict[str, Any]:
        row = await self.entity(entity_id)
        if row["scope_id"] != scope_id:
            raise MemoryError("invalid_relation_reference", 422)
        return row

    async def create_relation(self, data: CreateRelation, key: str) -> dict[str, Any]:
        await self.memory.scope(data.scope_id, "write")
        key_hash, request_hash, previous = await self.memory.replay(
            "create_relation", key, data.model_dump_json()
        )
        if previous is not None:
            return previous
        source = await self.endpoint(data.source_entity, data.scope_id)
        target = await self.endpoint(data.target_entity, data.scope_id)
        await self.memory.validate_evidence(data.scope_id, data.evidence)
        assertion_id = await self.memory.new_object(data.scope_id, "assertion")
        await self.conn.execute(
            """INSERT INTO memory.assertion
               (tenant_id,id,scope_id,subject,predicate,is_relation)
               VALUES (%s,%s,%s,%s,%s,true)""",
            (self.tenant, assertion_id, data.scope_id, source["canonical_label"], data.predicate),
        )
        await self.conn.execute(
            "INSERT INTO memory.relation(tenant_id,id,scope_id,source_id) VALUES (%s,%s,%s,%s)",
            (self.tenant, assertion_id, data.scope_id, data.source_entity),
        )
        content = Remember(
            scope_id=data.scope_id,
            subject=source["canonical_label"],
            predicate=data.predicate,
            value=target["canonical_label"],
            evidence=data.evidence,
            explicit_intent=data.explicit_intent,
            valid_from=data.valid_from,
            valid_to=data.valid_to,
        )
        await self.memory.insert_revision(assertion_id, data.scope_id, 1, content)
        await self.insert_target(assertion_id, data.scope_id, 1, data.target_entity)
        result = {"memory_id": str(assertion_id), "revision": 1, "epistemic_status": "reported"}
        await self.memory.audit("create_relation", assertion_id)
        await self.memory.save_result("create_relation", key_hash, request_hash, result)
        return result

    async def insert_target(
        self, assertion_id: UUID, scope_id: UUID, revision: int, target_id: UUID
    ) -> None:
        await self.conn.execute(
            """INSERT INTO memory.relation_revision
               (tenant_id,assertion_id,scope_id,revision,target_id) VALUES (%s,%s,%s,%s,%s)""",
            (self.tenant, assertion_id, scope_id, revision, target_id),
        )

    async def revise_relation(
        self, assertion_id: UUID, data: ReviseRelation, key: str
    ) -> dict[str, Any]:
        obj = await self.memory.object(assertion_id, "write")
        row = await (
            await self.conn.execute(
                """SELECT a.current_revision FROM memory.assertion a
                   JOIN memory.relation r USING (tenant_id,id)
                   WHERE a.tenant_id = %s AND a.id = %s AND a.is_relation FOR UPDATE OF a""",
                (self.tenant, assertion_id),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("not_found", 404)
        key_hash, request_hash, previous = await self.memory.replay(
            "revise_relation",
            key,
            json.dumps(
                {"memory_id": str(assertion_id), "request": data.model_dump(mode="json")},
                sort_keys=True,
            ),
        )
        if previous is not None:
            return previous
        if data.expected_revision != row["current_revision"]:
            raise MemoryError("revision_conflict", 409)
        if data.expected_revision == 1000:
            raise MemoryError("revision_limit_exceeded", 422)
        target = await self.endpoint(data.target_entity, obj["scope_id"])
        await self.memory.validate_evidence(obj["scope_id"], data.evidence)
        revision = data.expected_revision + 1
        content = ReviseAssertion(
            expected_revision=data.expected_revision,
            value=target["canonical_label"],
            evidence=data.evidence,
            explicit_intent=data.explicit_intent,
            valid_from=data.valid_from,
            valid_to=data.valid_to,
            reason=data.reason,
        )
        await self.memory.insert_revision(assertion_id, obj["scope_id"], revision, content)
        await self.insert_target(assertion_id, obj["scope_id"], revision, data.target_entity)
        result = {
            "memory_id": str(assertion_id),
            "revision": revision,
            "epistemic_status": "reported",
        }
        await self.memory.audit("revise_relation", assertion_id)
        await self.memory.save_result("revise_relation", key_hash, request_hash, result)
        return result

    async def visible_entities(
        self, ids: list[UUID], scopes: list[UUID], known_at: datetime
    ) -> list[EntitySummary]:
        rows = await (
            await self.conn.execute(
                """SELECT e.id AS memory_id,e.scope_id,e.entity_type,e.canonical_label,
                          o.created_at AS recorded_at
                   FROM memory.entity e JOIN memory.object o USING (tenant_id,id)
                   WHERE e.tenant_id = %s AND e.id = ANY(%s) AND e.scope_id = ANY(%s)
                     AND o.created_at <= %s
                     AND (SELECT count(*) FROM memory.entity_evidence ee
                          WHERE ee.tenant_id = e.tenant_id
                            AND ee.entity_id = e.id) = e.reference_count
                   ORDER BY e.id""",
                (self.tenant, ids, scopes, known_at),
            )
        ).fetchall()
        return [EntitySummary.model_validate(row) for row in rows]

    async def neighbors(
        self, walk: GraphPath, data: ExpandGraph, as_of: datetime, known_at: datetime, limit: int
    ) -> list[dict[str, Any]]:
        return await (
            await self.conn.execute(
                """WITH adjacent AS MATERIALIZED (
                    SELECT r.id,v.revision,r.source_id,v.target_id,v.target_id AS next_id
                    FROM memory.relation r JOIN memory.relation_revision v
                      ON v.tenant_id = r.tenant_id AND v.assertion_id = r.id
                    WHERE r.tenant_id = %(tenant)s AND r.source_id = %(node)s
                      AND %(direction)s IN ('outgoing','both')
                    UNION ALL
                    SELECT r.id,v.revision,r.source_id,v.target_id,r.source_id AS next_id
                    FROM memory.relation r JOIN memory.relation_revision v
                      ON v.tenant_id = r.tenant_id AND v.assertion_id = r.id
                    WHERE r.tenant_id = %(tenant)s AND v.target_id = %(node)s
                      AND %(direction)s IN ('incoming','both')
                )
                SELECT x.*,a.predicate,lower(v.valid_time) AS valid_from,
                       upper(v.valid_time) AS valid_to,lower(v.system_time) AS recorded_at
                FROM adjacent x
                JOIN memory.assertion a ON a.tenant_id = %(tenant)s AND a.id = x.id
                JOIN memory.assertion_revision v ON v.tenant_id = a.tenant_id
                  AND v.assertion_id = a.id AND v.revision = x.revision
                JOIN memory.entity s ON s.tenant_id = a.tenant_id AND s.id = x.source_id
                JOIN memory.entity t ON t.tenant_id = a.tenant_id AND t.id = x.target_id
                JOIN memory.object so ON so.tenant_id = s.tenant_id AND so.id = s.id
                JOIN memory.object ot ON ot.tenant_id = t.tenant_id AND ot.id = t.id
                WHERE a.scope_id = ANY(%(scopes)s) AND a.predicate = ANY(%(predicates)s)
                  AND s.scope_id = ANY(%(scopes)s) AND t.scope_id = ANY(%(scopes)s)
                  AND so.created_at <= %(known)s AND ot.created_at <= %(known)s
                  AND (SELECT count(*) FROM memory.entity_evidence ee
                       WHERE ee.tenant_id = s.tenant_id AND ee.entity_id = s.id) = s.reference_count
                  AND (SELECT count(*) FROM memory.entity_evidence ee
                       WHERE ee.tenant_id = t.tenant_id AND ee.entity_id = t.id) = t.reference_count
                  AND v.valid_time @> %(as_of)s AND v.system_time @> %(known)s
                  AND NOT x.next_id = ANY(%(visited)s)
                ORDER BY x.id,x.revision,x.next_id LIMIT %(limit)s""",
                {
                    "tenant": self.tenant,
                    "node": walk.nodes[-1],
                    "direction": data.direction,
                    "scopes": data.scope_ids,
                    "predicates": data.relation_types,
                    "known": known_at,
                    "as_of": as_of,
                    "visited": walk.nodes,
                    "limit": limit,
                },
            )
        ).fetchall()

    async def expand(self, data: ExpandGraph) -> dict[str, Any]:
        timeline = await (
            await self.conn.execute(
                """SELECT COALESCE(%s::timestamptz,statement_timestamp()) AS as_of,
                          COALESCE(%s::timestamptz,statement_timestamp()) AS known_at,
                          access_epoch,deletion_epoch FROM memory.tenant WHERE id = %s""",
                (data.as_of, data.known_at, self.tenant),
            )
        ).fetchone()
        if timeline is None:
            raise MemoryError("not_found", 404)
        seeds = await self.visible_entities(data.seeds, data.scope_ids, timeline["known_at"])
        frontier = [GraphPath(nodes=[seed.memory_id], assertions=[]) for seed in seeds]
        nodes = {seed.memory_id for seed in seeds}
        edges: dict[tuple[UUID, int], GraphEdge] = {}
        paths: list[GraphPath] = []
        truncated = False
        for _ in range(data.max_hops):
            next_frontier = []
            for walk in frontier:
                remaining = data.max_paths - len(paths)
                neighbors = await self.neighbors(
                    walk, data, timeline["as_of"], timeline["known_at"], remaining + 1
                )
                for row in neighbors[:remaining]:
                    ref = MemoryReference(memory_id=row["id"], revision=row["revision"])
                    path = GraphPath(
                        nodes=[*walk.nodes, row["next_id"]], assertions=[*walk.assertions, ref]
                    )
                    paths.append(path)
                    next_frontier.append(path)
                    nodes.add(row["next_id"])
                    edges[(ref.memory_id, ref.revision)] = GraphEdge(
                        assertion=ref,
                        predicate=row["predicate"],
                        source_entity=row["source_id"],
                        target_entity=row["target_id"],
                        valid_from=row["valid_from"],
                        valid_to=row["valid_to"],
                        recorded_at=row["recorded_at"],
                    )
                if len(neighbors) > remaining:
                    truncated = True
                    break
            if truncated:
                break
            frontier = next_frontier
        visible = await self.visible_entities(list(nodes), data.scope_ids, timeline["known_at"])
        if len(visible) != len(nodes):
            raise MemoryError("graph_invalidated", 409)
        return {
            "backend": "sql",
            "projection_watermark": None,
            "as_of": timeline["as_of"],
            "known_at": timeline["known_at"],
            "nodes": visible,
            "edges": [edges[key] for key in sorted(edges)],
            "paths": paths,
            "coverage": {
                "complete_within_bounds": not truncated,
                "truncated": truncated,
                "max_hops": data.max_hops,
            },
            "consistency": {
                "access_epoch": timeline["access_epoch"],
                "deletion_epoch": timeline["deletion_epoch"],
            },
            "empty_reason": None if paths else "not_found",
        }
