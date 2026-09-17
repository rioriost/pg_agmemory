import hashlib
import hmac
from typing import Any

from psycopg import sql

from pg_agmemory.models import EmbeddingInput, Explain, PutEmbedding
from pg_agmemory.service import MemoryError, MemoryService


class Embeddings:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def source(self, data: Explain, permission: str = "read") -> tuple[dict[str, Any], str]:
        obj = await self.memory.object(data.memory_id, permission)
        if obj["kind"] not in ("episode", "assertion"):
            raise MemoryError("not_found", 404)
        explained = await self.memory.explain(data)
        if obj["kind"] == "episode":
            text: str = explained["source"]["content"]
        else:
            assertion = explained["assertion"]
            text = f"{assertion['subject']} / {assertion['predicate']}: {assertion['value']}"
        return obj, text

    async def input(self, data: Explain) -> EmbeddingInput:
        obj, text = await self.source(data)
        return EmbeddingInput(
            memory_id=data.memory_id,
            revision=data.revision,
            type=obj["kind"],
            text=text,
            input_digest=hashlib.sha256(text.encode()).hexdigest(),
        )

    async def put(self, data: PutEmbedding, key: str) -> dict[str, Any]:
        obj, text = await self.source(
            Explain(memory_id=data.memory_id, revision=data.revision), "write"
        )
        digest = hashlib.sha256(text.encode()).hexdigest()
        if not hmac.compare_digest(digest, data.input_digest):
            raise MemoryError("embedding_input_mismatch", 409)
        key_hash, request_hash, previous = await self.memory.replay(
            "embedding", key, data.model_dump_json()
        )
        table = sql.Identifier("memory", obj["kind"] + "_embedding")
        parent = sql.Identifier(obj["kind"] + "_id")
        identity = sql.SQL(
            "tenant_id = %(tenant)s AND {} = %(id)s AND revision = %(revision)s"
        ).format(parent)
        model_filter = sql.SQL(" AND model_name = %(name)s AND model_revision = %(model_revision)s")
        params = {
            "tenant": self.tenant,
            "id": data.memory_id,
            "revision": data.revision,
            "name": data.model.name,
            "model_revision": data.model.revision,
            "vector": data.vector_literal(),
            "digest": digest,
            "scope": obj["scope_id"],
        }
        existing = await (
            await self.conn.execute(
                sql.SQL(
                    "SELECT input_digest = %(digest)s AND embedding OPERATOR(public.=) "
                    "%(vector)s::public.vector(768) AS matches FROM {} WHERE "
                ).format(table)
                + identity
                + model_filter,
                params,
            )
        ).fetchone()
        if previous is not None:
            if existing is None:
                raise MemoryError("embedding_unavailable", 409)
        if existing is not None:
            if not existing["matches"]:
                raise MemoryError("embedding_conflict", 409)
        else:
            count = await (
                await self.conn.execute(
                    sql.SQL("SELECT count(*) AS total FROM {} WHERE ").format(table) + identity,
                    params,
                )
            ).fetchone()
            if count and count["total"] >= 8:
                raise MemoryError("embedding_limit_exceeded", 422)
            await self.conn.execute(
                sql.SQL(
                    """INSERT INTO {}
                       (tenant_id,{},revision,scope_id,model_name,model_revision,input_digest,embedding)
                       VALUES (%(tenant)s,%(id)s,%(revision)s,%(scope)s,%(name)s,
                               %(model_revision)s,%(digest)s,%(vector)s::public.vector(768))"""
                ).format(table, parent),
                params,
            )
            await self.memory.audit("embedding", data.memory_id)
        result = {
            "memory_id": str(data.memory_id),
            "revision": data.revision,
            "model": data.model.model_dump(mode="json"),
            "input_digest": digest,
        }
        if previous is None:
            await self.memory.save_result(
                "embedding",
                key_hash,
                request_hash,
                {"memory_id": str(data.memory_id), "revision": data.revision},
            )
        return result
