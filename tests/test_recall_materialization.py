import psycopg
import pytest
from test_vectors import request, upload

from pg_agmemory.lexical import JAPANESE_PROFILE


@pytest.mark.parametrize(
    "mode,profile",
    [
        ("vector", "simple-v1"),
        ("hybrid", "simple-v1"),
        ("lexical", JAPANESE_PROFILE),
    ],
)
@pytest.mark.parametrize("populated", [False, True])
def test_ranking_and_coverage_share_one_candidate_statement(
    env, monkeypatch, mode, profile, populated
):
    memory_id = env.observe("shared materialized candidate").json()["memory_id"]
    if populated:
        upload(env, memory_id)
    statements = []
    execute = psycopg.AsyncConnection.execute

    async def traced(conn, query, params=None, **kwargs):
        if isinstance(query, str) and query.startswith("WITH candidates"):
            statements.append(query)
        return await execute(conn, query, params, **kwargs)

    monkeypatch.setattr(psycopg.AsyncConnection, "execute", traced)
    response = env.recall(
        query="" if mode == "vector" else "shared",
        retrieval_mode=mode,
        search_profile=profile,
        **({"vector_query": request()} if mode != "lexical" else {}),
    )
    assert response.status_code == 200
    assert len(statements) == 1
    result = response.json()
    if mode != "lexical":
        assert result["coverage"]["vector_incomplete"] is (not populated)
    if mode == "vector" and not populated:
        assert result["items"] == [] and result["empty_reason"] == "index_incomplete"
    else:
        assert result["items"][0]["memory_id"] == memory_id


def test_empty_scope_still_returns_complete_coverage(env):
    response = env.recall(retrieval_mode="vector", vector_query=request())
    assert response.status_code == 200
    value = response.json()
    assert value["items"] == [] and value["empty_reason"] == "not_found"
    assert value["coverage"]["retrieval_complete"]
