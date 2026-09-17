import asyncio
import json
from uuid import uuid4

import psycopg
import pytest

from pg_agmemory.database import connect
from pg_agmemory.models import EpisodePage, Identity, QueryEpisodes
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryService

pytestmark = pytest.mark.integration


def query_episodes(env, index=0, **overrides):
    headers = env.headers(index)
    del headers["Idempotency-Key"]
    return env.client.post(
        "/v1/episodes/query",
        headers=headers,
        json={"scope_ids": [str(env.scopes[index])], **overrides},
    )


def episode_ids(response):
    assert response.status_code == 200, response.text
    return [row["memory_id"] for row in response.json()["episodes"]]


def test_episode_query_lists_metadata_in_admission_not_occurrence_order(env, monkeypatch):
    original = env.observe("PRIVATE original Gold", consent_reference="PRIVATE consent").json()[
        "memory_id"
    ]
    late = env.observe("PRIVATE historical Gold", occurred_at="2020-01-01T00:00:00Z").json()[
        "memory_id"
    ]
    assertion = env.remember(original).json()["memory_id"]
    fetchall = psycopg.AsyncCursor.fetchall
    checked = False

    async def metadata_only(cursor):
        nonlocal checked
        rows = await fetchall(cursor)
        if rows and isinstance(rows[0], dict) and "occurred_at" in rows[0]:
            for row in rows:
                assert set(row) == {"memory_id", "scope_id", "occurred_at", "recorded_at"}
            checked = True
        return rows

    monkeypatch.setattr(psycopg.AsyncCursor, "fetchall", metadata_only)
    page = query_episodes(env)
    assert episode_ids(page) == [late, original] and checked
    assert page.json()["next_cursor"] is None
    for item in page.json()["episodes"]:
        assert set(item) == {"memory_id", "revision", "scope_id", "occurred_at", "recorded_at"}
        assert item["revision"] == 1 and item["scope_id"] == str(env.scopes[0])
        detail = env.client.post(
            "/v1/explain",
            json={"memory_id": item["memory_id"], "revision": 1},
            headers=env.headers(),
        )
        assert detail.status_code == 200
        assert detail.json()["source"]["occurred_at"] == item["occurred_at"]
        assert detail.json()["source"]["content"].startswith("PRIVATE")
    assert "PRIVATE" not in page.text and assertion not in page.text
    assert "content" not in page.text and "consent_reference" not in page.text
    assert (
        page.json() == query_episodes(env, occurred_from=None, occurred_to=None, before=None).json()
    )


def test_episode_occurrence_range_is_half_open_and_applies_before_limit(env):
    instants = [
        "2026-08-31T23:59:59.999999Z",
        "2026-09-01T00:00:00Z",
        "2026-09-01T23:59:59.999999Z",
        "2026-09-02T00:00:00Z",
        "2026-09-03T00:00:00Z",
    ]
    ids = [env.observe(occurred_at=instant).json()["memory_id"] for instant in instants]
    bounds = {
        "occurred_from": "2026-09-01T09:00:00+09:00",
        "occurred_to": "2026-09-02T09:00:00+09:00",
    }
    assert episode_ids(query_episodes(env, **bounds)) == [ids[2], ids[1]]
    first = query_episodes(env, max_items=1, **bounds)
    assert episode_ids(first) == [ids[2]]
    second = query_episodes(env, before=first.json()["next_cursor"], **bounds)
    assert episode_ids(second) == [ids[1]] and second.json()["next_cursor"] is None
    assert episode_ids(query_episodes(env, occurred_from=instants[3])) == ids[4:2:-1]
    assert episode_ids(query_episodes(env, occurred_to=instants[1])) == ids[:1]
    assert (
        episode_ids(
            query_episodes(env, occurred_from=instants[4], occurred_to="2100-01-01T00:00:00Z")
        )
        == ids[4:]
    )
    assert episode_ids(query_episodes(env, occurred_to=instants[0])) == []
    assert episode_ids(query_episodes(env, occurred_from="2100-01-01T00:00:00Z")) == []


def test_episode_query_scopes_and_shared_reads_apply_before_limit(env):
    own = env.observe().json()["memory_id"]
    shared = env.observe(index=2).json()["memory_id"]
    foreign = env.observe(index=1).json()["memory_id"]
    scopes = [str(scope) for scope in env.scopes] + [str(uuid4())]
    page = query_episodes(env, scope_ids=scopes, max_items=1)
    assert episode_ids(page) == [own] and page.json()["next_cursor"] is None
    with scope_access(
        env.admin_url,
        ScopeAccessRequest(
            operation="set",
            tenant_id=env.tenants[0],
            scope_id=env.scopes[2],
            principal_id=env.principals[0],
            expected_access_epoch=1,
            permissions=("read",),
            no_expiry=True,
        ),
    ):
        pass
    page = query_episodes(env, scope_ids=scopes)
    assert episode_ids(page) == [shared, own]
    assert page.json()["consistency"]["access_epoch"] == 2 and foreign not in page.text
    assert episode_ids(query_episodes(env, scope_ids=[str(env.scopes[1]), str(uuid4())])) == []
    assert episode_ids(query_episodes(env, index=1, scope_ids=[str(env.scopes[0])])) == []
    assert episode_ids(query_episodes(env, index=2, scope_ids=[str(env.scopes[0])])) == []


def test_episode_query_exact_hundred_item_page_and_uuid_ties(env):
    ids = [env.observe().json()["memory_id"] for _ in range(101)]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory.object SET created_at='2026-09-01T00:00:00Z' WHERE tenant_id=%s",
            (env.tenants[0],),
        )
    expected = sorted(ids, reverse=True)
    first = query_episodes(env, max_items=100)
    assert episode_ids(first) == expected[:100]
    assert first.json()["next_cursor"] == {
        "recorded_at": "2026-09-01T00:00:00Z",
        "memory_id": expected[99],
    }
    second = query_episodes(env, max_items=100, before=first.json()["next_cursor"])
    assert episode_ids(second) == expected[100:] and second.json()["next_cursor"] is None
    assert len(first.content) < 2 * 1024 * 1024
    assert (
        episode_ids(
            query_episodes(
                env, before={"recorded_at": "2026-09-01T09:00:00+09:00", "memory_id": expected[99]}
            )
        )
        == expected[100:]
    )
    assert (
        episode_ids(
            query_episodes(
                env, before={"recorded_at": "2026-09-01T00:00:00Z", "memory_id": expected[-1]}
            )
        )
        == []
    )


def test_episode_cursor_uses_current_deletion_and_access_without_freezing_new_rows(env):
    oldest, newest = [env.observe().json()["memory_id"] for _ in range(2)]
    first = query_episodes(env, max_items=1)
    assert episode_ids(first) == [newest]
    before = first.json()["next_cursor"]
    later = env.observe(occurred_at="2020-01-01T00:00:00Z").json()["memory_id"]
    assert episode_ids(query_episodes(env, before=before)) == [oldest]
    deleted = env.client.post(
        "/v1/forget", headers=env.headers(), json={"memory_ids": [newest], "reason": "test"}
    )
    assert deleted.status_code == 202 and deleted.json()["object_count"] == 1
    page = query_episodes(env, before=before)
    assert episode_ids(page) == [oldest] and page.json()["consistency"]["deletion_epoch"] == 2
    assert (
        env.client.post(
            "/v1/explain", headers=env.headers(), json={"memory_id": newest}
        ).status_code
        == 404
    )
    assert episode_ids(
        query_episodes(
            env, before={"recorded_at": "2100-01-01T00:00:00Z", "memory_id": str(uuid4())}
        )
    ) == [later, oldest]
    assert (
        episode_ids(
            query_episodes(
                env, before={"recorded_at": "1900-01-01T00:00:00Z", "memory_id": str(uuid4())}
            )
        )
        == []
    )
    for operation, epoch, permissions in (("set", 1, ("read",)), ("revoke", 2, ())):
        options = {"permissions": permissions, "no_expiry": True} if operation == "set" else {}
        with scope_access(
            env.admin_url,
            ScopeAccessRequest(
                operation=operation,
                tenant_id=env.tenants[0],
                scope_id=env.scopes[0],
                principal_id=env.principals[0],
                expected_access_epoch=epoch,
                **options,
            ),
        ):
            pass
        page = query_episodes(env, before=before)
        assert episode_ids(page) == ([oldest] if operation == "set" else [])
        assert page.json()["consistency"]["access_epoch"] == epoch + 1


def test_episode_query_is_read_only_without_audit_receipt_or_job_side_effects(env):
    env.observe()
    statement = """SELECT
        (SELECT count(*) FROM memory_ops.audit_event WHERE tenant_id=%s),
        (SELECT count(*) FROM memory_ops.idempotency WHERE tenant_id=%s),
        (SELECT count(*) FROM memory_ops.job WHERE tenant_id=%s)"""
    with psycopg.connect(env.admin_url) as conn:
        before = conn.execute(statement, (env.tenants[0],) * 3).fetchone()

    async def read_only():
        async with await connect(env.settings.database_url) as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute(
                    """SELECT set_config('pgag.tenant_id',%s,true),
                              set_config('pgag.principal_id',%s,true)""",
                    (str(env.tenants[0]), str(env.principals[0])),
                )
                memory = MemoryService(
                    conn, Identity(tenant_id=env.tenants[0], principal_id=env.principals[0])
                )
                return await memory.query_episodes(QueryEpisodes(scope_ids=[env.scopes[0]]))

    direct = EpisodePage.model_validate(asyncio.run(read_only())).model_dump(mode="json")
    assert direct == query_episodes(env).json()
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(statement, (env.tenants[0],) * 3).fetchone() == before


def test_episode_query_openapi_auth_body_capability_and_empty_contract(env):
    assert query_episodes(env).json() == {
        "episodes": [],
        "next_cursor": None,
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
    }
    body = {"scope_ids": [str(env.scopes[0])]}
    assert env.client.post("/v1/episodes/query", json=body).status_code == 401
    raw = json.dumps(body).encode()
    padded = raw + b" " * (262144 - len(raw))
    headers = {**env.headers(), "Content-Type": "application/json"}
    assert env.client.post("/v1/episodes/query", content=padded, headers=headers).status_code == 200
    assert (
        env.client.post("/v1/episodes/query", content=padded + b" ", headers=headers).status_code
        == 413
    )
    invalid = query_episodes(env, principal_id="PRIVATE")
    assert invalid.status_code == 422 and "PRIVATE" not in invalid.text
    cap = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert cap["episode_query"] == {
        "endpoint": "/v1/episodes/query",
        "order": ["recorded_at_desc", "memory_id_desc"],
        "pagination": "exclusive_keyset",
        "occurred_time_bounds": "half_open",
        "max_items": 100,
        "includes_content": False,
    }
    assert "episode_query" in cap["features"]
    operation = env.client.get("/openapi.json").json()["paths"]["/v1/episodes/query"]["post"]
    assert operation["security"] == [{"BearerAuth": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EpisodePage"
    }
