import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from pg_agmemory.api import create_app
from pg_agmemory.database import Settings

pytestmark = pytest.mark.integration


def test_observe_remember_recall_explain_forget(env):
    episode = env.observe()
    assert episode.status_code == 201, episode.text
    source = episode.json()["memory_id"]
    assertion = env.remember(source)
    assert assertion.status_code == 201, assertion.text
    memory = assertion.json()["memory_id"]
    found = env.recall(query="Gold")
    assert found.status_code == 200, found.text
    assert {item["memory_id"] for item in found.json()["items"]} == {source, memory}
    pack = found.json()["context_pack"]
    assert pack["byte_count"] == len(
        json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode()
    )
    explained = env.client.post("/v1/explain", json={"memory_id": memory}, headers=env.headers())
    assert explained.status_code == 200
    assert explained.json()["evidence"][0]["memory_id"] == source
    assert explained.json()["confidence"]["score"] is None
    preview = env.client.post(
        "/v1/forget",
        json={"memory_ids": [source], "mode": "preview", "reason": "test"},
        headers=env.headers(),
    )
    assert preview.json()["object_count"] == 2
    assert len(env.recall().json()["items"]) == 2
    key = env.headers()
    body = {"memory_ids": [source], "mode": "purge", "reason": "test"}
    deleted = env.client.post("/v1/forget", json=body, headers=key)
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["state"] == "active_store_purged"
    assert env.client.post("/v1/forget", json=body, headers=key).json() == deleted.json()
    receipt = env.client.get(
        "/v1/deletions/" + deleted.json()["deletion_id"], headers=env.headers()
    )
    assert receipt.status_code == 200
    assert env.recall().json()["items"] == []
    assert (
        env.recall(as_of="2026-09-01T00:00:00Z", known_at="2100-01-01T00:00:00Z").json()["items"]
        == []
    )
    assert (
        env.client.post(
            "/v1/explain", json={"memory_id": memory}, headers=env.headers()
        ).status_code
        == 404
    )
    with psycopg.connect(env.admin_url) as admin:
        for table in ["episode", "assertion", "provenance_edge"]:
            count = admin.execute(
                psycopg.sql.SQL("SELECT count(*) FROM memory.{} WHERE tenant_id = %s").format(
                    psycopg.sql.Identifier(table)
                ),
                (env.tenants[0],),
            ).fetchone()[0]
            assert count == 0


def test_tenant_and_scope_isolation_on_all_surfaces(env):
    source = env.observe().json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    for index in [1, 2]:
        assert (
            env.recall(index, scope_ids=[str(env.scopes[0]), str(env.scopes[index])]).json()[
                "items"
            ]
            == []
        )
        for object_id in [source, memory, str(uuid4())]:
            response = env.client.post(
                "/v1/explain", json={"memory_id": object_id}, headers=env.headers(index)
            )
            assert response.status_code == 404
            assert object_id not in response.text
        assert env.observe(index=index, scope_id=str(env.scopes[0])).status_code == 404
        assert env.remember(source, index=index).status_code == 404
        assert (
            env.client.post(
                "/v1/forget",
                json={"memory_ids": [source], "reason": "test"},
                headers=env.headers(index),
            ).status_code
            == 404
        )


def test_idempotency_conflict_event_dedup_and_deleted_replay(env):
    body = {
        "scope_id": str(env.scopes[0]),
        "source_namespace": "test",
        "source_event_id": str(uuid4()),
        "occurred_at": "2026-09-01T00:00:00Z",
        "content": "private Gold",
        "consent_reference": "test",
    }
    headers = env.headers()
    first = env.client.post("/v1/observe", json=body, headers=headers)
    assert first.status_code == 201
    assert env.client.post("/v1/observe", json=body, headers=headers).json() == first.json()
    assert (
        env.client.post(
            "/v1/observe", json={**body, "content": "other"}, headers=headers
        ).status_code
        == 409
    )
    assert env.client.post("/v1/observe", json=body, headers=env.headers()).json() == first.json()
    assert (
        env.client.post(
            "/v1/observe", json={**body, "content": "other"}, headers=env.headers()
        ).status_code
        == 409
    )
    assert (
        env.client.post(
            "/v1/forget",
            json={"memory_ids": [first.json()["memory_id"]], "reason": "test"},
            headers=env.headers(),
        ).status_code
        == 202
    )
    for replay_headers in [headers, env.headers()]:
        response = env.client.post("/v1/observe", json=body, headers=replay_headers)
        assert response.status_code == 404 and "private Gold" not in response.text
    assert env.recall().json()["items"] == []


def test_parallel_idempotency_has_one_effect(env):
    body = {
        "scope_id": str(env.scopes[0]),
        "source_namespace": "test",
        "source_event_id": str(uuid4()),
        "occurred_at": "2026-09-01T00:00:00Z",
        "content": "Gold",
        "consent_reference": "test",
    }
    headers = env.headers()
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(lambda _: env.client.post("/v1/observe", json=body, headers=headers), range(8))
        )
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["memory_id"] for response in responses}) == 1
    assert len(env.recall().json()["items"]) == 1


def test_evidence_validation_and_temporal_half_open_intervals(env):
    source = env.observe().json()["memory_id"]
    assert (
        env.remember(source, evidence=[{"memory_id": source, "quote": "made up"}]).status_code
        == 422
    )
    memory = env.remember(
        source, valid_from="2026-09-05T00:00:00Z", valid_to="2026-09-10T00:00:00Z"
    ).json()["memory_id"]
    for instant, visible in [
        ("2026-09-04T23:59:59Z", False),
        ("2026-09-05T00:00:00Z", True),
        ("2026-09-09T23:59:59Z", True),
        ("2026-09-10T00:00:00Z", False),
    ]:
        ids = {item["memory_id"] for item in env.recall(as_of=instant).json()["items"]}
        assert (memory in ids) is visible
    assert env.recall(known_at="2000-01-01T00:00:00Z").json()["items"] == []


def test_authentication_validation_body_limits_and_capabilities(env):
    assert env.client.get("/v1/capabilities").status_code == 401
    for overrides in [
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"exp": 1},
        {"sub": "unknown"},
    ]:
        response = env.client.get(
            "/v1/capabilities", headers={"Authorization": f"Bearer {env.token(**overrides)}"}
        )
        assert response.status_code == 401
    assert (
        env.client.get("/v1/capabilities", headers=env.headers()).json()["auto_synthesis"] is False
    )
    response = env.client.post(
        "/v1/observe", json={"secret_input": "DO_NOT_ECHO"}, headers=env.headers()
    )
    assert response.status_code == 422 and "DO_NOT_ECHO" not in response.text
    assert (
        env.client.post(
            "/v1/observe",
            content="{",
            headers={**env.headers(), "Content-Type": "application/json"},
        ).status_code
        == 400
    )
    assert (
        env.client.post(
            "/v1/observe", content=b"x" * (262144 + 1), headers=env.headers()
        ).status_code
        == 413
    )
    assert env.recall(token_budget=64).status_code == 422


def test_rls_direct_connection_and_context_reset(env):
    source = env.observe().json()["memory_id"]
    with psycopg.connect(env.settings.database_url, row_factory=dict_row, autocommit=True) as conn:
        for table in ["object", "episode", "assertion", "scope"]:
            assert (
                conn.execute(
                    psycopg.sql.SQL("SELECT * FROM memory.{}").format(psycopg.sql.Identifier(table))
                ).fetchall()
                == []
            )
        with conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id', %s, true),
                          set_config('pgag.principal_id', %s, true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            assert len(conn.execute("SELECT * FROM memory.episode").fetchall()) == 1
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(
                    """INSERT INTO memory.object(tenant_id, id, scope_id, kind)
                       VALUES (%s, %s, %s, 'episode')""",
                    (env.tenants[1], uuid4(), env.scopes[1]),
                )
        assert (
            conn.execute("SELECT * FROM memory.episode WHERE id = %s", (source,)).fetchall() == []
        )


def test_permission_revocation_and_privileged_role_rejected(env):
    source = env.observe().json()["memory_id"]
    with psycopg.connect(env.admin_url) as admin:
        admin.execute(
            """UPDATE memory.scope_member SET permissions = ARRAY['read']
               WHERE tenant_id = %s AND principal_id = %s""",
            (env.tenants[0], env.principals[0]),
        )
    assert env.observe().status_code == 404
    assert env.remember(source).status_code == 404
    assert (
        env.client.post(
            "/v1/forget", json={"memory_ids": [source], "reason": "test"}, headers=env.headers()
        ).status_code
        == 404
    )
    assert len(env.recall().json()["items"]) == 1
    with psycopg.connect(env.admin_url) as admin:
        admin.execute(
            """UPDATE memory.scope_member SET permissions = '{}'
               WHERE tenant_id = %s AND principal_id = %s""",
            (env.tenants[0], env.principals[0]),
        )
    assert env.recall().json()["items"] == []
    privileged = Settings(
        env.admin_url,
        env.settings.jwt_public_key,
        env.settings.jwt_issuer,
        env.settings.jwt_audience,
    )
    with pytest.raises(RuntimeError, match="must not own tables"):
        with TestClient(create_app(privileged)):
            pass


def test_database_requires_same_scope_evidence_and_at_least_one_source(env):
    source = env.observe().json()["memory_id"]
    with psycopg.connect(env.settings.database_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id', %s, true),
                          set_config('pgag.principal_id', %s, true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            target = uuid4()
            conn.execute(
                """INSERT INTO memory.object(tenant_id, id, scope_id, kind)
                   VALUES (%s,%s,%s,'assertion')""",
                (env.tenants[0], target, env.scopes[0]),
            )
            conn.execute(
                """INSERT INTO memory.assertion
                   (tenant_id,id,scope_id,subject,predicate,value,valid_time,explicit_intent)
                   VALUES (%s,%s,%s,'ACME','plan','Gold','(,)',true)""",
                (env.tenants[0], target, env.scopes[0]),
            )
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert env.remember(source).status_code == 201


def test_deletion_waits_until_prior_response_is_delivered(env):
    from pg_agmemory.api import TransactionBoundary

    source = env.observe().json()["memory_id"]

    async def run():
        reached_send = asyncio.Event()
        release_send = asyncio.Event()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"old memory"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                reached_send.set()
                await release_send.wait()

        scope = {
            "type": "http",
            "path": "/v1/recall",
            "headers": [(b"authorization", f"Bearer {env.token()}".encode())],
        }
        reading = asyncio.create_task(TransactionBoundary(app, env.settings)(scope, receive, send))
        await asyncio.wait_for(reached_send.wait(), 5)
        deletion = asyncio.create_task(
            asyncio.to_thread(
                env.client.post,
                "/v1/forget",
                json={"memory_ids": [source], "reason": "test"},
                headers=env.headers(),
            )
        )

        def has_waiter():
            with psycopg.connect(env.admin_url) as admin:
                return admin.execute(
                    """SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                       WHERE wait_event = 'advisory'
                         AND query LIKE 'SELECT pg_advisory_lock(hashtextextended%')"""
                ).fetchone()[0]

        for _ in range(100):
            if await asyncio.to_thread(has_waiter):
                break
            await asyncio.sleep(0.02)
        else:
            release_send.set()
            await reading
            await deletion
            pytest.fail("forget never waited on the response delivery lock")
        assert not deletion.done()
        release_send.set()
        await reading
        assert (await deletion).status_code == 202

    asyncio.run(run())


def test_multiple_sources_purge_invalidates_whole_assertion(env):
    first = env.observe("Gold from first source").json()["memory_id"]
    second = env.observe("Gold from second source").json()["memory_id"]
    result = env.remember(
        first,
        evidence=[
            {"memory_id": first, "quote": "Gold"},
            {"memory_id": second, "quote": "Gold"},
        ],
    )
    assert result.status_code == 201
    deleted = env.client.post(
        "/v1/forget", json={"memory_ids": [first], "reason": "test"}, headers=env.headers()
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["object_count"] == 2
    assert {item["memory_id"] for item in env.recall().json()["items"]} == {second}


def test_cross_scope_evidence_rejected_even_when_both_scopes_are_readable(env):
    source = env.observe().json()["memory_id"]
    with psycopg.connect(env.admin_url) as admin:
        admin.execute(
            """INSERT INTO memory.scope_member(tenant_id, scope_id, principal_id, permissions)
               VALUES (%s, %s, %s, ARRAY['read','write','delete'])""",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    assert env.remember(source, scope_id=str(env.scopes[2])).status_code == 422
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id', %s, true),
                          set_config('pgag.principal_id', %s, true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            target = uuid4()
            conn.execute(
                """INSERT INTO memory.object(tenant_id,id,scope_id,kind)
                   VALUES (%s,%s,%s,'assertion')""",
                (env.tenants[0], target, env.scopes[2]),
            )
            conn.execute(
                """INSERT INTO memory.assertion
                   (tenant_id,id,scope_id,subject,predicate,value,valid_time,explicit_intent)
                   VALUES (%s,%s,%s,'ACME','plan','Gold','(,)',true)""",
                (env.tenants[0], target, env.scopes[2]),
            )
            conn.execute(
                """INSERT INTO memory.provenance_edge
                   (tenant_id,child_id,parent_id,scope_id,quote) VALUES (%s,%s,%s,%s,'Gold')""",
                (env.tenants[0], target, source, env.scopes[2]),
            )


def test_dependency_failure_returns_retryable_error_without_success(env, monkeypatch):
    async def disconnected(url):
        raise psycopg.OperationalError("connection failed: DO_NOT_ECHO")

    monkeypatch.setattr("pg_agmemory.api.connect", disconnected)
    response = env.observe()
    assert response.status_code == 503
    assert response.json()["retryable"] is True
    assert "DO_NOT_ECHO" not in response.text


def test_openapi_declares_typed_contract_and_authentication(env):
    schema = env.client.get("/openapi.json").json()
    for path in ["/v1/observe", "/v1/remember", "/v1/recall", "/v1/explain", "/v1/forget"]:
        operation = schema["paths"][path]["post"]
        assert operation["security"] == [{"BearerAuth": []}]
        status = "201" if path in ["/v1/observe", "/v1/remember"] else "200"
        if path == "/v1/forget":
            status = "202"
        assert operation["responses"][status]["content"]["application/json"]["schema"]
        assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorBody"
        }
