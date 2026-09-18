import asyncio
import hashlib
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import ValidationError

from pg_agmemory.database import VECTOR_VERSION, migrate, validate_runtime
from pg_agmemory.jobs import job_transaction
from pg_agmemory.models import EmbeddingModel, Recall, VectorQuery
from pg_agmemory.service import MemoryError, MemoryService

MODEL = {"name": "synthetic-basis", "revision": "fixture-v1"}


def vector(x=1, y=0):
    return [x, y] + [0] * 766


def request(values=None, model=None):
    return {"model": model or MODEL, "values": values if values is not None else vector()}


def input_for(env, memory_id, revision=1, index=0):
    result = env.client.post(
        "/v1/embedding-inputs",
        json={"memory_id": memory_id, "revision": revision},
        headers=env.headers(index),
    )
    assert result.status_code == 200, result.text
    return result.json()


def upload_body(env, memory_id, values=None, model=None, revision=1, index=0):
    source = input_for(env, memory_id, revision, index)
    return {
        "memory_id": memory_id,
        "revision": revision,
        "input_digest": source["input_digest"],
        **request(values, model),
    }


def upload(env, memory_id, values=None, model=None, revision=1, index=0, headers=None):
    result = env.client.post(
        "/v1/embeddings",
        json=upload_body(env, memory_id, values, model, revision, index),
        headers=headers or env.headers(index),
    )
    assert result.status_code == 201, result.text
    return result.json()


def recall(env, values=None, model=None, **changes):
    result = env.recall(retrieval_mode="vector", vector_query=request(values, model), **changes)
    assert result.status_code == 200, result.text
    return result.json()


def ids(result):
    return [item["memory_id"] for item in result["items"]]


@pytest.mark.parametrize(
    "values",
    [
        [],
        [1] * 767,
        [1] * 769,
        [0] * 768,
        vector(True),
        vector("1"),
        vector(float("nan")),
        vector(float("inf")),
        [1e308] * 768,
    ],
)
def test_vector_rejects_bad_dimensions_nonfinite_non_numeric_and_zero(values):
    with pytest.raises(ValidationError):
        VectorQuery(**request(values))


@pytest.mark.parametrize(
    "changes",
    [
        {"dimensions": 2},
        {"distance_metric": "dot"},
        {"normalization": "none"},
        {"name": ""},
        {"revision": ""},
        {"name": "x" * 257},
        {"provider_url": "https://invalid.test"},
    ],
)
def test_model_space_contract_is_explicit_and_closed(changes):
    with pytest.raises(ValidationError):
        EmbeddingModel(**{**MODEL, **changes})


def test_vector_normalizes_without_truncation_or_changing_request_identity():
    data = VectorQuery(**request(vector(3, 4)))
    unit = json.loads(data.vector_literal())
    assert len(unit) == 768 and unit[:2] == [0.6, 0.8]
    assert math.hypot(*unit) == pytest.approx(1)
    assert data.values[:2] == [3, 4]
    assert json.loads(VectorQuery(**request(vector(5e-324))).vector_literal())[0] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"vector_query": request()},
        {"retrieval_mode": "vector"},
        {"retrieval_mode": "hybrid"},
        {"retrieval_mode": "hybrid", "vector_query": request()},
        {"retrieval_mode": "vector", "vector_query": request(), "query": "ignored"},
    ],
)
def test_recall_rejects_inconsistent_vector_mode_and_text(changes):
    with pytest.raises(ValidationError):
        Recall(scope_ids=[uuid4()], purpose="test", **changes)


@pytest.mark.integration
def test_canonical_input_normalization_exact_revisions_and_immutable_upload(env):
    source = env.observe("  Gold in 東京都  ").json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    for memory_id, text in [(source, "Gold in 東京都"), (assertion, "ACME / contract_tier: Gold")]:
        canonical = input_for(env, memory_id)
        assert canonical["text"] == text and canonical["input_format"] == "memory-content-v1"
        assert canonical["input_digest"] == hashlib.sha256(text.encode()).hexdigest()
        body = upload_body(env, memory_id)
        key = env.headers()
        uploaded = env.client.post("/v1/embeddings", json=body, headers=key)
        assert uploaded.status_code == 201
        assert env.client.post("/v1/embeddings", json=body, headers=key).json() == uploaded.json()
        assert upload(env, memory_id, vector(2)) == uploaded.json()
        changed = {**body, "values": vector(0, 1)}
        conflict = env.client.post("/v1/embeddings", json=changed, headers=env.headers())
        assert conflict.status_code == 409 and conflict.json()["code"] == "embedding_conflict"
        conflict = env.client.post("/v1/embeddings", json=changed, headers=key)
        assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"
        wrong_digest = env.client.post(
            "/v1/embeddings", json={**body, "input_digest": "0" * 64}, headers=env.headers()
        )
        assert wrong_digest.status_code == 409
        assert wrong_digest.json()["code"] == "embedding_input_mismatch"
    with psycopg.connect(env.admin_url) as conn:
        for table in ("episode_embedding", "assertion_embedding"):
            row = conn.execute(
                psycopg.sql.SQL(
                    "SELECT vector_dims(embedding),vector_norm(embedding) FROM memory.{} "
                    "WHERE tenant_id=%s"
                ).format(psycopg.sql.Identifier(table)),
                (env.tenants[0],),
            ).fetchone()
            assert row[0] == 768 and row[1] == pytest.approx(1)
    denied_revision = env.client.post(
        "/v1/embedding-inputs", json={"memory_id": source, "revision": 2}, headers=env.headers()
    )
    assert denied_revision.status_code == 404


@pytest.mark.integration
def test_exact_cosine_order_and_model_revision_isolation(env):
    first = env.observe("red").json()["memory_id"]
    second = env.observe("green").json()["memory_id"]
    third = env.observe("blue").json()["memory_id"]
    upload(env, first, vector(1))
    upload(env, second, vector(1, 1))
    upload(env, third, vector(-1))
    result = recall(env)
    assert ids(result) == [first, second, third]
    assert result["retrieval_mode"] == "vector"
    assert result["embedding_model"] == EmbeddingModel(**MODEL).model_dump(mode="json")
    assert result["coverage"]["vector_incomplete"] is False
    assert result["coverage"]["retrieval_complete"] is True
    for rank, (item, distance) in enumerate(
        zip(result["items"], [0, 1 - 1 / math.sqrt(2), 2], strict=True), 1
    ):
        assert item["retrieval"] == {
            "method": "exact_cosine",
            "lexical_rank": None,
            "vector_rank": rank,
            "vector_distance": pytest.approx(distance, abs=1e-6),
            "fusion_score": None,
        }
        assert item["confidence"]["score"] is None
    other = {**MODEL, "revision": "fixture-v2"}
    upload(env, first, vector(-1), other)
    upload(env, third, vector(1), other)
    reordered = recall(env, model=other)
    assert ids(reordered) == [third, first] and reordered["coverage"]["vector_incomplete"]
    absent = recall(env, model={**MODEL, "name": "another-model"})
    assert absent["items"] == [] and absent["empty_reason"] == "index_incomplete"
    assert not absent["coverage"]["retrieval_complete"]
    assert env.recall(query="red").json()["retrieval_mode"] == "lexical"


@pytest.mark.integration
def test_hybrid_fuses_independent_exact_vector_and_fts_ranks_with_rrf60(env):
    first = env.observe("Gold").json()["memory_id"]
    second = env.observe("Gold Gold Gold Gold").json()["memory_id"]
    third = env.observe("Silver").json()["memory_id"]
    upload(env, first, vector(1))
    upload(env, second, vector(0, 1))
    upload(env, third, vector(1, 1))
    lexical = env.recall(query="Gold").json()
    lexical_rank = {memory_id: rank for rank, memory_id in enumerate(ids(lexical), 1)}
    exact = recall(env)
    vector_rank = {memory_id: rank for rank, memory_id in enumerate(ids(exact), 1)}
    hybrid_response = env.recall(query="Gold", retrieval_mode="hybrid", vector_query=request())
    assert hybrid_response.status_code == 200
    hybrid = hybrid_response.json()
    expected = {
        memory_id: 1 / (60 + vector_rank[memory_id])
        + (1 / (60 + lexical_rank[memory_id]) if memory_id in lexical_rank else 0)
        for memory_id in (first, second, third)
    }
    assert ids(hybrid) == sorted(expected, key=lambda memory_id: (-expected[memory_id], memory_id))
    for item in hybrid["items"]:
        ranks = item["retrieval"]
        assert ranks["method"] == "rrf-60"
        assert ranks["fusion_score"] == pytest.approx(expected[item["memory_id"]])
        assert ranks["lexical_rank"] == lexical_rank.get(item["memory_id"])
        assert ranks["vector_rank"] == vector_rank[item["memory_id"]]


@pytest.mark.integration
def test_missing_coverage_is_explicit_without_widening_or_hiding_lexical_results(env):
    first = env.observe("東京都の契約は Gold").json()["memory_id"]
    second = env.observe("東京都の契約は Silver").json()["memory_id"]
    upload(env, first)
    result = recall(env)
    assert ids(result) == [first]
    assert result["coverage"]["vector_incomplete"] and not result["coverage"]["retrieval_complete"]
    hybrid = env.recall(
        query="契約",
        search_profile="ja-janome-0.5.0-v1",
        retrieval_mode="hybrid",
        vector_query=request(),
    ).json()
    assert set(ids(hybrid)) == {first, second}
    lexical_only = next(item for item in hybrid["items"] if item["memory_id"] == second)
    assert lexical_only["retrieval"]["vector_distance"] is None
    assert lexical_only["retrieval"]["vector_rank"] is None
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("DELETE FROM memory.episode_lexical WHERE episode_id=%s", (second,))
    missing = env.recall(
        query="契約",
        search_profile="ja-janome-0.5.0-v1",
        retrieval_mode="hybrid",
        vector_query=request(),
    ).json()
    assert missing["coverage"]["lexical_incomplete"] and missing["coverage"]["vector_incomplete"]
    vector_only = recall(env, search_profile="ja-janome-0.5.0-v1")
    assert vector_only["coverage"]["lexical_incomplete"] is False
    assert env.recall(query="Silver").json()["coverage"]["vector_incomplete"] is False
    exhausted = recall(env, token_budget=220)
    assert exhausted["items"] == [] and exhausted["empty_reason"] == "budget_exhausted"
    assert exhausted["context_pack"]["byte_count"] <= 220


@pytest.mark.integration
def test_scope_tenant_and_future_exclusions_precede_ranking_and_coverage(env):
    visible = env.observe("visible").json()["memory_id"]
    private = env.observe("PRIVATE_SCOPE", index=2).json()["memory_id"]
    foreign = env.observe("PRIVATE_TENANT", index=1).json()["memory_id"]
    future = env.observe("FUTURE", occurred_at="2100-01-01T00:00:00Z").json()["memory_id"]
    upload(env, visible, vector(0, 1))
    upload(env, private, vector(1), index=2)
    upload(env, foreign, vector(1), index=1)
    narrow = recall(env)
    broad = recall(env, scope_ids=[str(scope) for scope in env.scopes])
    assert narrow == broad and ids(broad) == [visible]
    assert broad["coverage"]["vector_incomplete"] is False
    assert all(identifier not in json.dumps(broad) for identifier in (private, foreign, future))

    async def sql_visible():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            rows = await (
                await jobs.conn.execute("SELECT episode_id FROM memory.episode_embedding")
            ).fetchall()
            return [str(row["episode_id"]) for row in rows]

    assert asyncio.run(sql_visible()) == [visible]
    only_hidden = recall(env, scope_ids=[str(env.scopes[1])])
    assert only_hidden["items"] == [] and only_hidden["empty_reason"] == "not_found"
    for memory_id in (private, foreign):
        denied = env.client.post(
            "/v1/embedding-inputs", json={"memory_id": memory_id}, headers=env.headers()
        )
        assert denied.status_code == 404
        denied_write = env.client.post(
            "/v1/embeddings",
            json={"memory_id": memory_id, "input_digest": "0" * 64, **request()},
            headers=env.headers(),
        )
        assert denied_write.status_code == 404


@pytest.mark.integration
def test_temporal_vector_projection_is_bound_to_exact_assertion_revision(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    old = env.client.post(
        "/v1/explain", json={"memory_id": assertion}, headers=env.headers()
    ).json()["assertion"]["recorded_at"]
    upload(env, assertion, vector(1))
    revised = env.client.post(
        f"/v1/assertions/{assertion}/revisions",
        json={
            "expected_revision": 1,
            "value": "Silver",
            "explicit_intent": True,
            "evidence": [{"memory_id": source, "quote": "Silver"}],
            "reason": "synthetic update",
        },
        headers=env.headers(),
    )
    assert revised.status_code == 201
    assert input_for(env, assertion)["text"].endswith("Gold")
    assert input_for(env, assertion, 2)["text"].endswith("Silver")
    assert ids(recall(env, known_at=old)) == [assertion]
    assert recall(env)["empty_reason"] == "index_incomplete"
    upload(env, assertion, vector(0, 1), revision=2)
    current = recall(env)
    assert ids(current) == [assertion] and current["items"][0]["revision"] == 2
    assert current["items"][0]["retrieval"]["vector_distance"] == pytest.approx(1)
    historical = recall(env, known_at=old)
    assert historical["items"][0]["revision"] == 1
    assert historical["items"][0]["retrieval"]["vector_distance"] == pytest.approx(0)


@pytest.mark.integration
def test_purge_cascades_all_vector_models_and_history_and_fences_replay(env):
    source = env.observe().json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    key = env.headers()
    body = upload_body(env, assertion)
    assert env.client.post("/v1/embeddings", json=body, headers=key).status_code == 201
    upload(env, source)
    upload(env, assertion, model={**MODEL, "revision": "v2"})
    erased = env.client.post(
        "/v1/forget",
        json={"memory_ids": [source], "reason": "synthetic purge"},
        headers=env.headers(),
    )
    assert erased.status_code == 202
    for headers in (key, env.headers()):
        assert env.client.post("/v1/embeddings", json=body, headers=headers).status_code == 404
    with psycopg.connect(env.admin_url) as conn:
        for table in ("episode_embedding", "assertion_embedding"):
            assert (
                conn.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM memory.{} WHERE tenant_id=%s").format(
                        psycopg.sql.Identifier(table)
                    ),
                    (env.tenants[0],),
                ).fetchone()[0]
                == 0
            )
        receipts = conn.execute(
            """SELECT result FROM memory_ops.idempotency
               WHERE tenant_id=%s AND operation='embedding'""",
            (env.tenants[0],),
        ).fetchall()
        assert receipts and all(set(row[0]) == {"memory_id", "revision"} for row in receipts)
    result = recall(env, known_at="2100-01-01T00:00:00Z")
    assert result["items"] == [] and not result["coverage"]["vector_incomplete"]


@pytest.mark.integration
def test_projection_quota_concurrency_and_rollback(env, monkeypatch):
    source = env.observe().json()["memory_id"]
    body, headers = upload_body(env, source), env.headers()
    original = MemoryService.audit

    async def fault(self, action, target):
        await original(self, action, target)
        if action == "embedding":
            raise MemoryError("dependency_unavailable", 503)

    with monkeypatch.context() as patch:
        patch.setattr(MemoryService, "audit", fault)
        assert env.client.post("/v1/embeddings", json=body, headers=headers).status_code == 503
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.episode_embedding WHERE episode_id=%s", (source,)
            ).fetchone()[0]
            == 0
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: upload(env, source, headers=headers), range(4)))
    assert all(response == responses[0] for response in responses)
    for index in range(7):
        upload(env, source, model={**MODEL, "revision": f"additional-{index}"})
    assert upload(env, source) == responses[0]
    overflow = upload_body(env, source, model={**MODEL, "revision": "ninth"})
    rejected = env.client.post("/v1/embeddings", json=overflow, headers=env.headers())
    assert rejected.status_code == 422 and rejected.json()["code"] == "embedding_limit_exceeded"
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="embedding model limit"):
            conn.execute(
                """INSERT INTO memory.episode_embedding
                   VALUES (%s,%s,1,%s,'direct-sql','ninth',%s,%s::vector(768))""",
                (
                    env.tenants[0],
                    source,
                    env.scopes[0],
                    body["input_digest"],
                    VectorQuery(**request()).vector_literal(),
                ),
            )


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["episode", "assertion"])
def test_database_vector_constraints_reject_wrong_scope_dimension_and_norm(env, kind):
    source = env.observe().json()["memory_id"]
    memory_id = source if kind == "episode" else env.remember(source).json()["memory_id"]
    statement = psycopg.sql.SQL(
        "INSERT INTO memory.{} VALUES (%s,%s,1,%s,'direct-sql','invalid',%s,%s::vector(768))"
    ).format(psycopg.sql.Identifier(kind + "_embedding"))
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        for values in ([0] * 768, vector(2)):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    statement,
                    (
                        env.tenants[0],
                        memory_id,
                        env.scopes[0],
                        "0" * 64,
                        json.dumps(values),
                    ),
                )
        with pytest.raises(psycopg.DataError):
            conn.execute(
                statement,
                (
                    env.tenants[0],
                    memory_id,
                    env.scopes[0],
                    "0" * 64,
                    "[1,0]",
                ),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                statement,
                (
                    env.tenants[0],
                    memory_id,
                    env.scopes[2],
                    "0" * 64,
                    json.dumps(vector()),
                ),
            )


@pytest.mark.integration
def test_current_read_write_permissions_override_projection_publication_and_replay(env):
    source = env.observe().json()["memory_id"]
    body, headers = upload_body(env, source), env.headers()
    assert env.client.post("/v1/embeddings", json=body, headers=headers).status_code == 201
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        conn.execute(
            "UPDATE memory.scope_member SET permissions=ARRAY['read'] WHERE scope_id=%s",
            (env.scopes[0],),
        )
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=access_epoch+1 WHERE id=%s", (env.tenants[0],)
        )
    assert ids(recall(env)) == [source]
    assert env.client.post("/v1/embeddings", json=body, headers=headers).status_code == 404
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        conn.execute("DELETE FROM memory.scope_member WHERE scope_id=%s", (env.scopes[0],))
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=access_epoch+1 WHERE id=%s", (env.tenants[0],)
        )
    result = recall(env)
    assert result["items"] == [] and not result["coverage"]["vector_incomplete"]
    assert (
        env.client.post(
            "/v1/embedding-inputs", json={"memory_id": source}, headers=env.headers()
        ).status_code
        == 404
    )


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_runtime_cannot_modify_or_delete_projection_directly(env, operation):
    source = env.observe().json()["memory_id"]
    upload(env, source)

    async def attempt():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            query = (
                "UPDATE memory.episode_embedding SET input_digest=input_digest"
                if operation == "UPDATE"
                else "DELETE FROM memory.episode_embedding"
            )
            await jobs.conn.execute(query + " WHERE episode_id=%s", (source,))

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        asyncio.run(attempt())


@pytest.mark.integration
def test_extension_pin_rls_and_no_ann_index_are_verified(env):
    caps = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert caps["schema_version"] == 13
    assert caps["embeddings"]["extension_version"] == VECTOR_VERSION
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute("SHOW server_version_num").fetchone()[0] == "180006"
        assert (
            conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
            == VECTOR_VERSION
        )
        rows = conn.execute(
            """SELECT relrowsecurity,relforcerowsecurity FROM pg_class
               WHERE oid IN ('memory.episode_embedding'::regclass,
                             'memory.assertion_embedding'::regclass)"""
        ).fetchall()
        assert rows == [(True, True), (True, True)]
        assert (
            conn.execute(
                """SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
               JOIN pg_am a ON a.oid=c.relam
               WHERE a.amname IN ('hnsw','ivfflat')"""
            ).fetchone()[0]
            == 0
        )
        conn.execute("UPDATE pg_extension SET extversion='test-invalid' WHERE extname='vector'")
    try:
        with pytest.raises(RuntimeError, match="pgvector 0.8.6"):
            asyncio.run(validate_runtime(env.settings.database_url))
        with pytest.raises(RuntimeError, match="pgvector 0.8.6"):
            migrate(env.admin_url)
    finally:
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(
                "UPDATE pg_extension SET extversion=%s WHERE extname='vector'", (VECTOR_VERSION,)
            )


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_vector_recall_parity_without_embedding_write_tools(env, api_process, mode):
    source = env.observe().json()["memory_id"]
    upload(env, source)
    expected = recall(env, token_budget=2000)
    with api_process("mcp-vector-" + mode + ".log") as (api, _):

        async def scenario():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "pg_agmemory.cli", "mcp"],
                env={"PGAG_MCP_API_URL": str(api.base_url), "PGAG_MCP_API_TOKEN": env.token()},
            )
            async with Client(parameters, mode=mode, read_timeout_seconds=20) as client:
                assert len((await client.list_tools()).tools) == 4
                response = await client.call_tool(
                    "memory_recall",
                    {
                        "request": {
                            "scope_ids": [str(env.scopes[0])],
                            "purpose": "test",
                            "retrieval_mode": "vector",
                            "vector_query": request(),
                        }
                    },
                )
                assert not response.is_error
                assert response.structured_content["result"] == expected

        asyncio.run(scenario())
