import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from pg_agmemory.capture_policy import POLICY_COLUMNS, stored_policy
from pg_agmemory.database import Connection, connect
from pg_agmemory.lexical import JAPANESE_PROFILE, segment
from pg_agmemory.models import (
    AssertionHistory,
    EpisodeSummary,
    Evidence,
    Explain,
    Forget,
    Identity,
    MemoryItem,
    MemoryReference,
    Observe,
    QueryEpisodes,
    Recall,
    RelationEndpoints,
    Remember,
    RetrievalEvidence,
    ReviseAssertion,
)


class MemoryError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@asynccontextmanager
async def principal_connection(
    url: str, subject: str
) -> AsyncIterator[tuple[Connection, Identity]]:
    async with await connect(url) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('pgag.subject', %s, true)", (subject,))
            row = await (
                await conn.execute(
                    "SELECT tenant_id,id FROM memory.principal WHERE external_subject = %s",
                    (subject,),
                )
            ).fetchone()
        if row is None:
            raise MemoryError("unauthenticated", 401)
        identity = Identity(tenant_id=row["tenant_id"], principal_id=row["id"])
        # The lock survives commit and stays held through API response delivery.
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (str(identity.tenant_id),)
        )
        yield conn, identity


async def bind_identity(conn: Connection, subject: str, identity: Identity) -> None:
    await conn.execute(
        """SELECT set_config('pgag.subject', %s, true),
                  set_config('pgag.tenant_id', %s, true),
                  set_config('pgag.principal_id', %s, true)""",
        (subject, str(identity.tenant_id), str(identity.principal_id)),
    )
    row = await (
        await conn.execute(
            """SELECT 1 FROM memory.principal
               WHERE tenant_id = %s AND id = %s AND external_subject = %s""",
            (identity.tenant_id, identity.principal_id, subject),
        )
    ).fetchone()
    if row is None:
        raise MemoryError("unauthenticated", 401)


class MemoryService:
    def __init__(self, conn: Connection, identity: Identity) -> None:
        self.conn = conn
        self.identity = identity
        self.tenant = identity.tenant_id
        self.principal = identity.principal_id

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

    async def scope(self, scope_id: UUID, permission: str) -> None:
        row = await (
            await self.conn.execute(
                "SELECT memory.permitted(%s, 'read') AND memory.permitted(%s, %s) AS allowed",
                (scope_id, scope_id, permission),
            )
        ).fetchone()
        if not row or not row["allowed"]:
            raise MemoryError("not_found", 404)

    async def object(self, object_id: UUID, permission: str = "read") -> dict[str, Any]:
        row = await (
            await self.conn.execute(
                "SELECT * FROM memory.object WHERE tenant_id = %s AND id = %s",
                (self.tenant, object_id),
            )
        ).fetchone()
        if not row:
            raise MemoryError("not_found", 404)
        await self.scope(row["scope_id"], permission)
        return row

    async def digest(self, value: str) -> str:
        row = await (
            await self.conn.execute(
                "SELECT dedup_secret FROM memory.tenant WHERE id = %s", (self.tenant,)
            )
        ).fetchone()
        if row is None:
            raise MemoryError("not_found", 404)
        return hmac.new(bytes(row["dedup_secret"]), value.encode(), hashlib.sha256).hexdigest()

    async def replay(
        self, operation: str, key: str, payload: str
    ) -> tuple[str, str, dict[str, Any] | None]:
        key_digest = await self.digest(key)
        request_digest = await self.digest(payload)
        row = await (
            await self.conn.execute(
                """SELECT request_digest, result FROM memory_ops.idempotency
                   WHERE tenant_id = %s AND principal_id = %s
                     AND operation = %s AND key_digest = %s""",
                (self.tenant, self.principal, operation, key_digest),
            )
        ).fetchone()
        if row is None:
            return key_digest, request_digest, None
        if not hmac.compare_digest(row["request_digest"], request_digest):
            raise MemoryError("idempotency_conflict", 409)
        result: dict[str, Any] = row["result"]
        if "memory_id" in result:
            await self.object(UUID(result["memory_id"]), "write")
        if "checkpoint_id" in result:
            await self.object(UUID(result["checkpoint_id"]), "write")
        if "job_id" in result:
            await self.object(UUID(result["job_id"]), "write")
        if result.get("synthesis_job_id") is not None:
            await self.object(UUID(result["synthesis_job_id"]), "write")
        for job_id in result.get("synthesis_job_ids", []):
            await self.object(UUID(job_id), "write")
        if "deletion_id" in result:
            for scope in result["scope_ids"]:
                await self.scope(UUID(scope), "delete")
        return key_digest, request_digest, result

    async def save_result(
        self, operation: str, key_digest: str, request_digest: str, result: dict[str, Any]
    ) -> None:
        await self.conn.execute(
            """INSERT INTO memory_ops.idempotency
               (tenant_id, principal_id, operation, key_digest, request_digest, result)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (self.tenant, self.principal, operation, key_digest, request_digest, Jsonb(result)),
        )

    async def audit(self, action: str, target: UUID) -> None:
        await self.conn.execute(
            """INSERT INTO memory_ops.audit_event(tenant_id, principal_id, action, target_id)
               VALUES (%s, %s, %s, %s)""",
            (self.tenant, self.principal, action, target),
        )

    async def new_object(self, scope: UUID, kind: str) -> UUID:
        object_id = uuid4()
        await self.conn.execute(
            """INSERT INTO memory.object(tenant_id, id, scope_id, kind)
               VALUES (%s, %s, %s, %s)""",
            (self.tenant, object_id, scope, kind),
        )
        return object_id

    async def validate_refs(
        self,
        scope_id: UUID,
        refs: list[MemoryReference],
        error_code: str = "invalid_checkpoint_reference",
    ) -> list[dict[str, Any]]:
        result = []
        for ref in sorted(refs, key=lambda ref: (str(ref.memory_id), ref.revision)):
            obj = await self.object(ref.memory_id)
            if obj["scope_id"] != scope_id or obj["kind"] not in ("episode", "assertion", "entity"):
                raise MemoryError(error_code, 422)
            if obj["kind"] in ("episode", "entity"):
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
                raise MemoryError(error_code, 422)
            result.append(
                {
                    "memory_id": str(ref.memory_id),
                    "revision": ref.revision,
                    "kind": obj["kind"],
                }
            )
        return result

    async def validate_capture(self, data: Observe) -> None:
        await self.scope(data.scope_id, "write")
        row = await (
            await self.conn.execute(
                f"SELECT {POLICY_COLUMNS} FROM memory.scope_capture_policy "
                "WHERE tenant_id=%s AND scope_id=%s",
                (self.tenant, data.scope_id),
            )
        ).fetchone()
        try:
            policy = stored_policy(row)
        except ValidationError:
            raise MemoryError("capture_policy_invalid", 503) from None
        try:
            permitted = policy.permits(data)
        except UnicodeError:
            raise MemoryError("invalid_request", 422) from None
        if not permitted:
            raise MemoryError("capture_policy_denied", 403)

    async def observe(self, data: Observe, key: str) -> dict[str, Any]:
        await self.validate_capture(data)
        key_hash, payload_hash, previous = await self.replay("observe", key, data.model_dump_json())
        if previous is not None:
            return previous
        event_hash = await self.digest(
            json.dumps([data.source_namespace, data.source_event_id], ensure_ascii=False)
        )
        duplicate = await (
            await self.conn.execute(
                """SELECT request_digest, object_id FROM memory_ops.source_event
                   WHERE tenant_id = %s AND scope_id = %s AND event_digest = %s""",
                (self.tenant, data.scope_id, event_hash),
            )
        ).fetchone()
        if duplicate:
            if duplicate["request_digest"] != payload_hash:
                raise MemoryError("source_event_conflict", 409)
            await self.object(duplicate["object_id"], "write")
            object_id = duplicate["object_id"]
        else:
            object_id = await self.new_object(data.scope_id, "episode")
            await self.conn.execute(
                """INSERT INTO memory.episode
                   (tenant_id, id, scope_id, occurred_at, content, consent_reference)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    self.tenant,
                    object_id,
                    data.scope_id,
                    data.occurred_at,
                    data.content,
                    data.consent_reference,
                ),
            )
            await self.conn.execute(
                """INSERT INTO memory.episode_lexical
                   (tenant_id,episode_id,scope_id,profile,search_text)
                   VALUES (%s,%s,%s,%s,to_tsvector('simple',%s))""",
                (self.tenant, object_id, data.scope_id, JAPANESE_PROFILE, segment(data.content)),
            )
            await self.conn.execute(
                """INSERT INTO memory_ops.source_event
                   (tenant_id, scope_id, event_digest, request_digest, object_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (self.tenant, data.scope_id, event_hash, payload_hash, object_id),
            )
            await self.audit("observe", object_id)
        result = {"memory_id": str(object_id), "revision": 1, "synthesis_job_id": None}
        await self.save_result("observe", key_hash, payload_hash, result)
        return result

    async def query_episodes(self, data: QueryEpisodes) -> dict[str, Any]:
        rows = await (
            await self.conn.execute(
                """SELECT e.id AS memory_id,e.scope_id,e.occurred_at,o.created_at AS recorded_at
                   FROM memory.episode e JOIN memory.object o USING (tenant_id,id)
                   WHERE e.tenant_id=%(tenant)s AND e.scope_id=ANY(%(scopes)s)
                     AND (%(from)s::timestamptz IS NULL OR e.occurred_at>=%(from)s)
                     AND (%(to)s::timestamptz IS NULL OR e.occurred_at<%(to)s)
                     AND (%(before_time)s::timestamptz IS NULL
                          OR (o.created_at,e.id)<(%(before_time)s,%(before_id)s::uuid))
                   ORDER BY o.created_at DESC,e.id DESC LIMIT %(limit)s""",
                {
                    "tenant": self.tenant,
                    "scopes": data.scope_ids,
                    "from": data.occurred_from,
                    "to": data.occurred_to,
                    "before_time": data.before.recorded_at if data.before else None,
                    "before_id": data.before.memory_id if data.before else None,
                    "limit": data.max_items + 1,
                },
            )
        ).fetchall()
        episodes = [EpisodeSummary.model_validate(row) for row in rows[: data.max_items]]
        return {
            "episodes": episodes,
            "next_cursor": {
                "recorded_at": episodes[-1].recorded_at,
                "memory_id": episodes[-1].memory_id,
            }
            if len(rows) > data.max_items
            else None,
            "consistency": await self.epochs(),
        }

    async def remember(self, data: Remember, key: str) -> dict[str, Any]:
        await self.scope(data.scope_id, "write")
        key_hash, payload_hash, previous = await self.replay(
            "remember", key, data.model_dump_json()
        )
        if previous is not None:
            return previous
        result = await self.publish_assertion(data)
        await self.save_result("remember", key_hash, payload_hash, result)
        return result

    async def publish_assertion(self, data: Remember) -> dict[str, Any]:
        await self.scope(data.scope_id, "write")
        await self.validate_evidence(data.scope_id, data.evidence)
        object_id = await self.new_object(data.scope_id, "assertion")
        await self.conn.execute(
            """INSERT INTO memory.assertion(tenant_id, id, scope_id, subject, predicate)
               VALUES (%s, %s, %s, %s, %s)""",
            (self.tenant, object_id, data.scope_id, data.subject, data.predicate),
        )
        await self.insert_revision(object_id, data.scope_id, 1, data)
        result = {"memory_id": str(object_id), "revision": 1, "epistemic_status": "reported"}
        await self.audit("remember", object_id)
        return result

    async def validate_evidence(self, scope_id: UUID, sources: list[Evidence]) -> None:
        for evidence in sources:
            source = await self.object(evidence.memory_id)
            if source["scope_id"] != scope_id or source["kind"] != "episode":
                raise MemoryError("invalid_evidence", 422)
            row = await (
                await self.conn.execute(
                    "SELECT content FROM memory.episode WHERE tenant_id = %s AND id = %s",
                    (self.tenant, evidence.memory_id),
                )
            ).fetchone()
            if not row or evidence.quote not in row["content"]:
                raise MemoryError("invalid_evidence", 422)

    async def insert_revision(
        self, object_id: UUID, scope_id: UUID, revision: int, data: Remember | ReviseAssertion
    ) -> None:
        await self.conn.execute(
            """INSERT INTO memory.assertion_revision
               (tenant_id, assertion_id, scope_id, revision, value,
                valid_time, explicit_intent, correction_reason)
               VALUES (%s, %s, %s, %s, %s, tstzrange(%s, %s, '[)'), true, %s)""",
            (
                self.tenant,
                object_id,
                scope_id,
                revision,
                data.value,
                data.valid_from,
                data.valid_to,
                data.reason if isinstance(data, ReviseAssertion) else None,
            ),
        )
        for evidence in data.evidence:
            await self.conn.execute(
                """INSERT INTO memory.provenance_edge
                   (tenant_id, child_id, child_revision, parent_id, scope_id, quote)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (self.tenant, object_id, revision, evidence.memory_id, scope_id, evidence.quote),
            )
        identity = await (
            await self.conn.execute(
                "SELECT subject,predicate FROM memory.assertion WHERE tenant_id = %s AND id = %s",
                (self.tenant, object_id),
            )
        ).fetchone()
        if identity is None:
            raise MemoryError("not_found", 404)
        await self.conn.execute(
            """INSERT INTO memory.assertion_lexical
               (tenant_id,assertion_id,revision,scope_id,profile,search_text)
               VALUES (%s,%s,%s,%s,%s,to_tsvector('simple',%s))""",
            (
                self.tenant,
                object_id,
                revision,
                scope_id,
                JAPANESE_PROFILE,
                segment(identity["subject"] + " " + identity["predicate"] + " " + data.value),
            ),
        )

    async def revise_assertion(
        self, object_id: UUID, data: ReviseAssertion, key: str
    ) -> dict[str, Any]:
        obj = await self.object(object_id, "write")
        if obj["kind"] != "assertion":
            raise MemoryError("not_found", 404)
        payload = json.dumps(
            {"memory_id": str(object_id), "request": data.model_dump(mode="json")},
            sort_keys=True,
        )
        key_hash, payload_hash, previous = await self.replay("revise_assertion", key, payload)
        if previous is not None:
            return previous
        head = await (
            await self.conn.execute(
                """SELECT current_revision,is_relation FROM memory.assertion
                   WHERE tenant_id = %s AND id = %s FOR UPDATE""",
                (self.tenant, object_id),
            )
        ).fetchone()
        if head is None:
            raise MemoryError("not_found", 404)
        if head["is_relation"]:
            raise MemoryError("relation_revision_required", 409)
        if head["current_revision"] != data.expected_revision:
            raise MemoryError("revision_conflict", 409)
        if data.expected_revision == 1000:
            raise MemoryError("revision_limit_exceeded", 422)
        await self.validate_evidence(obj["scope_id"], data.evidence)
        revision = data.expected_revision + 1
        await self.insert_revision(object_id, obj["scope_id"], revision, data)
        result = {"memory_id": str(object_id), "revision": revision, "epistemic_status": "reported"}
        await self.save_result("revise_assertion", key_hash, payload_hash, result)
        await self.audit("revise_assertion", object_id)
        return result

    async def recall(self, data: Recall) -> dict[str, Any]:
        clock = await (await self.conn.execute("SELECT statement_timestamp() AS at")).fetchone()
        if clock is None:
            raise MemoryError("database_error", 503)
        # Scope IDs only narrow access; invisible scopes never contribute candidates.
        candidates = """WITH candidates AS MATERIALIZED (
                    SELECT o.id, o.kind, o.created_at, 1 AS revision, e.content, e.occurred_at,
                           NULL::timestamptz AS valid_from, NULL::timestamptz AS valid_to,
                           NULL::uuid AS source_entity, NULL::uuid AS target_entity,
                           CASE WHEN %(profile)s = 'simple-v1' THEN e.search_text
                                ELSE lex.search_text END AS search_text, vec.embedding
                    FROM memory.object o JOIN memory.episode e USING (tenant_id, id)
                    LEFT JOIN memory.episode_lexical lex
                      ON lex.tenant_id = e.tenant_id AND lex.episode_id = e.id
                     AND lex.profile = %(profile)s
                    LEFT JOIN memory.episode_embedding vec
                      ON vec.tenant_id = e.tenant_id AND vec.episode_id = e.id
                     AND vec.model_name = %(model_name)s
                     AND vec.model_revision = %(model_revision)s
                    WHERE o.tenant_id = %(tenant)s AND o.scope_id = ANY(%(scopes)s)
                      AND (%(kind)s::text IS NULL OR %(kind)s = 'episode')
                      AND %(subject)s::text IS NULL AND %(predicate)s::text IS NULL
                      AND o.created_at <= COALESCE(%(known)s, statement_timestamp())
                      AND e.occurred_at <= COALESCE(%(as_of)s, statement_timestamp())
                    UNION ALL
                    SELECT o.id, o.kind, lower(r.system_time), r.revision,
                           a.subject || ' / ' || a.predicate || ': ' || r.value, NULL,
                           lower(r.valid_time), upper(r.valid_time),
                           link.source_id, endpoint.target_id,
                           CASE WHEN %(profile)s = 'simple-v1'
                                THEN a.search_text || r.search_text ELSE lex.search_text END,
                           vec.embedding
                    FROM memory.object o JOIN memory.assertion a USING (tenant_id, id)
                    JOIN memory.assertion_revision r
                      ON r.tenant_id = a.tenant_id AND r.assertion_id = a.id
                    LEFT JOIN memory.assertion_lexical lex
                      ON lex.tenant_id = r.tenant_id AND lex.assertion_id = r.assertion_id
                     AND lex.revision = r.revision AND lex.profile = %(profile)s
                    LEFT JOIN memory.assertion_embedding vec
                      ON vec.tenant_id = r.tenant_id AND vec.assertion_id = r.assertion_id
                     AND vec.revision = r.revision AND vec.model_name = %(model_name)s
                     AND vec.model_revision = %(model_revision)s
                    LEFT JOIN memory.relation link
                      ON link.tenant_id = a.tenant_id AND link.id = a.id
                    LEFT JOIN memory.relation_revision endpoint ON endpoint.tenant_id = r.tenant_id
                      AND endpoint.assertion_id = r.assertion_id AND endpoint.revision = r.revision
                    WHERE o.tenant_id = %(tenant)s AND o.scope_id = ANY(%(scopes)s)
                      AND (%(kind)s::text IS NULL OR %(kind)s = 'assertion')
                      AND (%(subject)s::text IS NULL OR a.subject COLLATE "C" = %(subject)s)
                      AND (%(predicate)s::text IS NULL OR a.predicate COLLATE "C" = %(predicate)s)
                      AND (NOT a.is_relation OR endpoint.assertion_id IS NOT NULL)
                      AND r.valid_time @> COALESCE(%(as_of)s, statement_timestamp())
                      AND r.system_time @> COALESCE(%(known)s, statement_timestamp())
                ) """
        parameters = {
            "query": segment(data.query) if data.search_profile == JAPANESE_PROFILE else data.query,
            "browse": data.query == "",
            "profile": data.search_profile,
            "tenant": self.tenant,
            "scopes": data.scope_ids,
            "kind": data.filters.kind if data.filters else None,
            "subject": data.filters.subject if data.filters else None,
            "predicate": data.filters.predicate if data.filters else None,
            "as_of": data.as_of if data.as_of is not None else clock["at"],
            "known": data.known_at if data.known_at is not None else clock["at"],
            "limit": data.max_items - len(data.required_memory_refs) + 1,
            "required_ids": [ref.memory_id for ref in data.required_memory_refs],
            "required_revisions": [ref.revision for ref in data.required_memory_refs],
            "model_name": data.vector_query.model.name if data.vector_query else None,
            "model_revision": data.vector_query.model.revision if data.vector_query else None,
            "vector": data.vector_query.vector_literal() if data.vector_query else None,
            "retrieval_mode": data.retrieval_mode,
        }
        ranking = """SELECT *, CASE WHEN %(browse)s THEN 0::real
                            ELSE ts_rank_cd(search_text,plainto_tsquery('simple',%(query)s))
                            END AS rank
                     FROM candidates WHERE (%(browse)s
                       OR search_text @@ plainto_tsquery('simple',%(query)s))
                       AND NOT (id = ANY(%(required_ids)s::uuid[]))
                     ORDER BY rank DESC NULLS LAST, created_at DESC, id LIMIT %(limit)s"""
        if data.vector_query is not None:
            ranking = """, lexical AS (
                         SELECT id,revision,row_number() OVER (
                             ORDER BY ts_rank_cd(search_text,plainto_tsquery('simple',%(query)s))
                                      DESC,created_at DESC,id) AS lexical_rank
                         FROM candidates WHERE %(retrieval_mode)s = 'hybrid'
                           AND search_text @@ plainto_tsquery('simple',%(query)s)
                     ), distances AS MATERIALIZED (
                         SELECT id,revision,
                                embedding OPERATOR(public.<=>) %(vector)s::public.vector(768)
                                AS vector_distance
                         FROM candidates WHERE embedding IS NOT NULL
                     ), vectors AS (
                         SELECT *,row_number() OVER (ORDER BY vector_distance,id) AS vector_rank
                         FROM distances
                     )
                     SELECT c.*,l.lexical_rank,v.vector_rank,v.vector_distance,
                            COALESCE(1.0/(60+l.lexical_rank),0)
                            + COALESCE(1.0/(60+v.vector_rank),0) AS fusion_score
                     FROM candidates c LEFT JOIN lexical l USING (id,revision)
                     LEFT JOIN vectors v USING (id,revision)
                     WHERE l.lexical_rank IS NOT NULL OR v.vector_rank IS NOT NULL
                     ORDER BY fusion_score DESC,c.id LIMIT %(limit)s"""
        required = []
        if data.required_memory_refs:
            required = await (
                await self.conn.execute(
                    candidates + """SELECT * FROM candidates WHERE (id,revision) IN (
                        SELECT * FROM unnest(%(required_ids)s::uuid[],
                                             %(required_revisions)s::integer[])
                    )""",
                    parameters,
                )
            ).fetchall()
            if len(required) != len(data.required_memory_refs):
                raise MemoryError("not_found", 404)
            by_reference = {(row["id"], row["revision"]): row for row in required}
            required = [
                by_reference[(ref.memory_id, ref.revision)] for ref in data.required_memory_refs
            ]
        ranked = await (await self.conn.execute(candidates + ranking, parameters)).fetchall()
        rows = required + ranked
        lexical_incomplete = vector_incomplete = False
        if data.search_profile == JAPANESE_PROFILE or data.vector_query is not None:
            coverage = await (
                await self.conn.execute(
                    candidates
                    + """SELECT EXISTS(SELECT 1 FROM candidates WHERE search_text IS NULL)
                         AS lexical_incomplete,
                         EXISTS(SELECT 1 FROM candidates WHERE embedding IS NULL)
                         AS vector_incomplete""",
                    parameters,
                )
            ).fetchone()
            lexical_incomplete = bool(
                data.retrieval_mode != "vector"
                and data.search_profile == JAPANESE_PROFILE
                and coverage
                and coverage["lexical_incomplete"]
            )
            vector_incomplete = bool(
                data.vector_query is not None and coverage and coverage["vector_incomplete"]
            )
        incomplete = lexical_incomplete or vector_incomplete
        items = []
        for row in rows[: data.max_items]:
            sources = await (
                await self.conn.execute(
                    """SELECT parent_id FROM memory.provenance_edge
                       WHERE tenant_id = %s AND child_id = %s AND child_revision = %s
                       ORDER BY parent_id""",
                    (self.tenant, row["id"], row["revision"]),
                )
            ).fetchall()
            items.append(
                MemoryItem(
                    memory_id=row["id"],
                    revision=row["revision"],
                    type=row["kind"],
                    content=row["content"],
                    recorded_at=row["created_at"],
                    occurred_at=row["occurred_at"],
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                    source=[source["parent_id"] for source in sources],
                    relation=RelationEndpoints(
                        source_entity=row["source_entity"], target_entity=row["target_entity"]
                    )
                    if row["source_entity"] is not None
                    else None,
                    retrieval=RetrievalEvidence(
                        method="rrf-60" if data.retrieval_mode == "hybrid" else "exact_cosine",
                        lexical_rank=row["lexical_rank"],
                        vector_rank=row["vector_rank"],
                        vector_distance=row["vector_distance"],
                        fusion_score=row["fusion_score"]
                        if data.retrieval_mode == "hybrid"
                        else None,
                    )
                    if data.vector_query is not None
                    else None,
                )
            )
        context, selected, budget_exhausted = build_context(
            items, data.token_budget, required_count=len(required)
        )
        epoch = await self.epochs()
        pending = await (
            await self.conn.execute(
                """SELECT EXISTS(SELECT 1 FROM memory_ops.job WHERE tenant_id = %s
                   AND scope_id = ANY(%s) AND state IN ('pending','running')) AS pending""",
                (self.tenant, data.scope_ids),
            )
        ).fetchone()
        return {
            "items": [item.model_dump(mode="json") for item in selected],
            "context_pack": context,
            "search_profile": data.search_profile,
            "retrieval_mode": data.retrieval_mode,
            "embedding_model": data.vector_query.model.model_dump(mode="json")
            if data.vector_query
            else None,
            "coverage": {
                "retrieval_complete": not incomplete,
                "synthesis_pending": False,
                "jobs_pending": bool(pending and pending["pending"]),
                "lexical_incomplete": lexical_incomplete,
                "vector_incomplete": vector_incomplete,
                "graph_used": False,
                "truncated": budget_exhausted or len(rows) > data.max_items,
            },
            "consistency": epoch,
            "empty_reason": (
                "budget_exhausted" if items else "index_incomplete" if incomplete else "not_found"
            )
            if not selected
            else None,
        }

    async def explain(self, data: Explain) -> dict[str, Any]:
        obj = await self.object(data.memory_id)
        if obj["kind"] == "episode":
            if data.revision != 1:
                raise MemoryError("not_found", 404)
            row = await (
                await self.conn.execute(
                    """SELECT content, occurred_at, consent_reference FROM memory.episode
                       WHERE tenant_id = %s AND id = %s""",
                    (self.tenant, data.memory_id),
                )
            ).fetchone()
            return {"memory_id": data.memory_id, "revision": 1, "type": "episode", "source": row}
        row = await (
            await self.conn.execute(
                """SELECT a.subject, a.predicate, a.is_relation, r.value,
                          lower(r.valid_time) AS valid_from,
                          upper(r.valid_time) AS valid_to, lower(r.system_time) AS recorded_at,
                          upper(r.system_time) AS known_until, r.correction_reason
                   FROM memory.assertion a JOIN memory.assertion_revision r
                     ON r.tenant_id = a.tenant_id AND r.assertion_id = a.id
                   WHERE a.tenant_id = %s AND a.id = %s AND r.revision = %s""",
                (self.tenant, data.memory_id, data.revision),
            )
        ).fetchone()
        if row is None:
            raise MemoryError("not_found", 404)
        relation = None
        if row.pop("is_relation"):
            relation = await (
                await self.conn.execute(
                    """SELECT r.source_id AS source_entity,v.target_id AS target_entity
                       FROM memory.relation r JOIN memory.relation_revision v
                         ON v.tenant_id = r.tenant_id AND v.assertion_id = r.id
                       WHERE r.tenant_id = %s AND r.id = %s AND v.revision = %s""",
                    (self.tenant, data.memory_id, data.revision),
                )
            ).fetchone()
            if relation is None:
                raise MemoryError("relation_invalidated", 409)
        evidence = await (
            await self.conn.execute(
                """SELECT p.parent_id AS memory_id, p.quote, e.occurred_at
                   FROM memory.provenance_edge p
                   JOIN memory.episode e ON e.tenant_id = p.tenant_id AND e.id = p.parent_id
                   WHERE p.tenant_id = %s AND p.child_id = %s AND p.child_revision = %s
                   ORDER BY p.parent_id""",
                (self.tenant, data.memory_id, data.revision),
            )
        ).fetchall()
        return {
            "memory_id": data.memory_id,
            "revision": data.revision,
            "type": "assertion",
            "assertion": row,
            "evidence": evidence,
            "epistemic_status": "reported",
            "confidence": {"score": None, "method": "uncalibrated"},
            "relation": relation,
        }

    async def assertion_history(self, data: AssertionHistory) -> dict[str, Any]:
        obj = await self.object(data.memory_id)
        if obj["kind"] != "assertion":
            raise MemoryError("not_found", 404)
        anchor = await (
            await self.conn.execute(
                """SELECT subject,predicate,current_revision,is_relation
                   FROM memory.assertion WHERE tenant_id=%s AND id=%s""",
                (self.tenant, data.memory_id),
            )
        ).fetchone()
        if anchor is None:
            raise MemoryError("assertion_invalidated", 409)
        highest = min(anchor["current_revision"], (data.before_revision or 1001) - 1)
        rows = await (
            await self.conn.execute(
                """SELECT r.revision,lower(r.valid_time) AS valid_from,
                          upper(r.valid_time) AS valid_to,
                          lower(r.system_time) AS recorded_at,
                          upper(r.system_time) AS known_until,r.correction_reason,
                          r.epistemic_status,l.source_id,v.target_id,
                          (SELECT jsonb_agg(
                              jsonb_build_object('memory_id',p.parent_id,'revision',1)
                              ORDER BY p.parent_id)
                           FROM memory.provenance_edge p JOIN memory.episode e
                             ON e.tenant_id=p.tenant_id AND e.id=p.parent_id
                           WHERE p.tenant_id=r.tenant_id AND p.child_id=r.assertion_id
                             AND p.child_revision=r.revision) AS evidence_refs
                   FROM memory.assertion_revision r
                   LEFT JOIN memory.relation l
                     ON l.tenant_id=r.tenant_id AND l.id=r.assertion_id
                   LEFT JOIN memory.relation_revision v
                     ON v.tenant_id=r.tenant_id AND v.assertion_id=r.assertion_id
                       AND v.revision=r.revision
                   WHERE r.tenant_id=%s AND r.assertion_id=%s AND r.revision<=%s
                   ORDER BY r.revision DESC LIMIT %s""",
                (self.tenant, data.memory_id, highest, data.max_items + 1),
            )
        ).fetchall()
        expected = list(range(highest, max(0, highest - data.max_items - 1), -1))
        if [row["revision"] for row in rows] != expected:
            raise MemoryError("assertion_invalidated", 409)
        selected = rows[: data.max_items]
        for row in selected:
            if not row["evidence_refs"] or not 1 <= len(row["evidence_refs"]) <= 32:
                raise MemoryError("assertion_invalidated", 409)
            source, target = row.pop("source_id"), row.pop("target_id")
            row["relation"] = None
            if anchor["is_relation"]:
                if source is None or target is None:
                    raise MemoryError("relation_invalidated", 409)
                row["relation"] = {"source_entity": source, "target_entity": target}
        anchor.pop("is_relation")
        return {
            "memory_id": data.memory_id,
            "scope_id": obj["scope_id"],
            **anchor,
            "revisions": selected,
            "next_before_revision": selected[-1]["revision"]
            if len(rows) > data.max_items
            else None,
            "consistency": await self.epochs(),
        }

    async def forget(self, data: Forget, key: str) -> dict[str, Any]:
        key_hash, payload_hash, previous = await self.replay("forget", key, data.model_dump_json())
        if previous is not None:
            return previous
        scopes = set()
        for object_id in data.memory_ids:
            obj = await self.object(object_id, "delete")
            scopes.add(str(obj["scope_id"]))
        closure_limit = 10000 + len(data.memory_ids)
        closure = await (
            await self.conn.execute(
                """WITH RECURSIVE edges(parent, child) AS (
                    SELECT parent_id, child_id FROM memory.provenance_edge
                    WHERE tenant_id = %(tenant)s
                    UNION
                    SELECT source_id, job_id FROM memory_ops.job_input
                    WHERE tenant_id = %(tenant)s
                    UNION
                    SELECT result_id, id FROM memory_ops.job
                    WHERE tenant_id = %(tenant)s AND result_id IS NOT NULL
                    UNION
                    SELECT retry_of, id FROM memory_ops.job
                    WHERE tenant_id = %(tenant)s AND retry_of IS NOT NULL
                    UNION
                    SELECT source_id, entity_id FROM memory.entity_evidence
                    WHERE tenant_id = %(tenant)s
                    UNION
                    SELECT source_id, id FROM memory.relation WHERE tenant_id = %(tenant)s
                    UNION
                    SELECT target_id, assertion_id FROM memory.relation_revision
                    WHERE tenant_id = %(tenant)s
                    UNION
                    SELECT source_id, checkpoint_id FROM memory.checkpoint_reference
                    WHERE tenant_id = %(tenant)s
                    UNION
                    SELECT parent_id, id FROM memory.checkpoint
                    WHERE tenant_id = %(tenant)s AND parent_id IS NOT NULL
                    UNION
                    SELECT source_id, effect_id FROM memory.tool_effect_reference
                    WHERE tenant_id = %(tenant)s
                    UNION
                    SELECT e.id, c.id FROM memory.tool_effect e JOIN memory.checkpoint c
                      USING (tenant_id, scope_id, run_id)
                    WHERE e.tenant_id = %(tenant)s
                ), closure(id) AS (
                    SELECT unnest(%(ids)s::uuid[])
                    UNION
                    SELECT e.child FROM closure c JOIN edges e ON e.parent = c.id
                ) SELECT id FROM closure LIMIT %(limit)s""",
                {"tenant": self.tenant, "ids": data.memory_ids, "limit": closure_limit + 1},
            )
        ).fetchall()
        if len(closure) > closure_limit:
            raise MemoryError("deletion_limit_exceeded", 422)
        targets = [row["id"] for row in closure]
        if data.mode == "preview":
            return {"mode": "preview", "object_count": len(targets), "changed": False}
        if data.mode == "purge":
            await self.conn.execute(
                "DELETE FROM memory_ops.job_input WHERE tenant_id = %s AND job_id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory_ops.job WHERE tenant_id = %s AND id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """UPDATE memory.checkpoint_run r SET effects_invalidated = true
                   WHERE r.tenant_id = %s AND NOT r.effects_invalidated AND EXISTS (
                       SELECT 1 FROM memory.tool_effect e
                       WHERE e.tenant_id = r.tenant_id AND e.scope_id = r.scope_id
                         AND e.run_id = r.run_id AND e.id = ANY(%s)
                   )""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """UPDATE memory.checkpoint_branch SET invalidated = true
                   WHERE tenant_id = %s AND head_id = ANY(%s) AND NOT invalidated""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """DELETE FROM memory.checkpoint_reference
                   WHERE tenant_id = %s AND checkpoint_id = ANY(%s)""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory.checkpoint WHERE tenant_id = %s AND id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """DELETE FROM memory.tool_effect_reference
                   WHERE tenant_id = %s AND effect_id = ANY(%s)""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """DELETE FROM memory.tool_effect_revision
                   WHERE tenant_id = %s AND effect_id = ANY(%s)""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory.tool_effect WHERE tenant_id = %s AND id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """DELETE FROM memory.provenance_edge
                   WHERE tenant_id = %s AND child_id = ANY(%s)""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """DELETE FROM memory.relation_revision
                   WHERE tenant_id = %s AND assertion_id = ANY(%s)""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory.relation WHERE tenant_id = %s AND id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                """DELETE FROM memory.assertion_revision
                   WHERE tenant_id = %s AND assertion_id = ANY(%s)""",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory.assertion WHERE tenant_id = %s AND id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory.entity_evidence WHERE tenant_id = %s AND entity_id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory.entity WHERE tenant_id = %s AND id = ANY(%s)",
                (self.tenant, targets),
            )
            await self.conn.execute(
                "DELETE FROM memory.episode WHERE tenant_id = %s AND id = ANY(%s)",
                (self.tenant, targets),
            )
        await self.conn.execute(
            """INSERT INTO memory_ops.object_tombstone(tenant_id, object_id, scope_id)
               SELECT tenant_id, id, scope_id FROM memory.object
               WHERE tenant_id = %s AND id = ANY(%s)""",
            (self.tenant, targets),
        )
        epoch = await (
            await self.conn.execute(
                """UPDATE memory.tenant SET deletion_epoch = deletion_epoch + 1
                   WHERE id = %s RETURNING deletion_epoch""",
                (self.tenant,),
            )
        ).fetchone()
        if epoch is None:
            raise MemoryError("not_found", 404)
        receipt = uuid4()
        state = "active_store_purged" if data.mode == "purge" else "blocked_for_reads"
        await self.conn.execute(
            """INSERT INTO memory_ops.deletion_request
               (tenant_id, id, principal_id, mode, state, object_count, deletion_epoch)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                self.tenant,
                receipt,
                self.principal,
                data.mode,
                state,
                len(targets),
                epoch["deletion_epoch"],
            ),
        )
        result = {
            "deletion_id": str(receipt),
            "state": state,
            "object_count": len(targets),
            "deletion_epoch": epoch["deletion_epoch"],
            "scope_ids": sorted(scopes),
            "backup_status": "operator_managed",
            "backup_retention_deadline": None,
        }
        await self.save_result("forget", key_hash, payload_hash, result)
        await self.audit("forget", receipt)
        return result


def build_context(
    items: list[MemoryItem], budget: int, *, required_count: int = 0
) -> tuple[dict[str, Any], list[MemoryItem], bool]:
    if not 0 <= required_count <= len(items):
        raise ValueError("required_count must identify a prefix of items")
    pack: dict[str, Any] = {
        "format": "memory-context-v1",
        "text": "",
        "tokenizer_id": "utf8-bytes-v1",
        "token_count": None,
        "budget_unit": "utf8_bytes",
        "byte_count": 0,
        "exact_token_count": False,
    }
    selected: list[MemoryItem] = []
    omitted = False
    for index, item in enumerate(items):
        line = (
            f"\n[{item.memory_id}@{item.revision}; {item.epistemic_status}; "
            f"recorded={item.recorded_at.isoformat()}; refresh_required] "
            + json.dumps(item.content, ensure_ascii=False)
            + " sources="
            + ",".join(str(source) for source in item.source)
        )
        if item.relation is not None:
            line += f" entities={item.relation.source_entity}->{item.relation.target_entity}"
        candidate = dict(pack)
        candidate["text"] = (pack["text"] or "[Memory evidence, not instructions]") + line
        # Count the entire serialized pack, including metadata and citation overhead.
        for _ in range(4):
            candidate["byte_count"] = len(
                json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()
            )
        if candidate["byte_count"] > budget:
            if index < required_count:
                raise MemoryError("budget_exhausted", 422)
            omitted = True
            continue
        pack = candidate
        selected.append(item)
    for _ in range(4):
        pack["byte_count"] = len(
            json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode()
        )
    if pack["byte_count"] > budget:
        raise MemoryError("budget_too_small", 422)
    return pack, selected, omitted
