import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from pg_agmemory.checkpoints import Checkpoints
from pg_agmemory.database import connect
from pg_agmemory.models import Identity
from pg_agmemory.service import MemoryError, MemoryService

pytestmark = pytest.mark.integration


def payload(env, **overrides):
    return {
        "scope_id": str(env.scopes[0]),
        "run_id": str(uuid4()),
        "branch_id": str(uuid4()),
        "expected_head": None,
        "harness_id": "test-harness",
        "harness_version": "1.0",
        "event_watermark": 10,
        "state": {"goal": "Continue the approved task"},
        "memory_refs": [],
        **overrides,
    }


def create(env, body=None, headers=None, **overrides):
    return env.client.post(
        "/v1/checkpoints",
        json=body or payload(env, **overrides),
        headers=headers or env.headers(),
    )


def get(env, checkpoint, index=0):
    return env.client.get(f"/v1/checkpoints/{checkpoint}", headers=env.headers(index))


def restore(env, checkpoint, headers=None, **overrides):
    return env.client.post(
        "/v1/checkpoints/restore",
        headers=headers or env.headers(),
        json={
            "checkpoint_id": checkpoint,
            "target_branch_id": str(uuid4()),
            "harness_id": "test-harness",
            "harness_version": "1.0",
            **overrides,
        },
    )


def test_immutable_checkpoint_roundtrip_head_cas_and_idempotency(env):
    source = env.observe().json()["memory_id"]
    body = payload(env, memory_refs=[{"memory_id": source}])
    headers = env.headers()
    created = create(env, body, headers)
    assert created.status_code == 201, created.text
    receipt = created.json()
    checkpoint = receipt["checkpoint_id"]
    assert receipt["sequence"] == 1 and receipt["parent_checkpoint"] is None
    assert len(receipt["checksum"]) == 64
    first = get(env, checkpoint)
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["state"]["goal"] == body["state"]["goal"]
    assert saved["memory_refs"] == [{"memory_id": source, "revision": 1}]
    assert saved["automatic_reexecution"] is False and saved["resume_allowed"] is True
    assert create(env, body, headers).json() == receipt
    assert create(env, {**body, "event_watermark": 11}, headers).status_code == 409
    advanced = create(
        env,
        {
            **body,
            "expected_head": checkpoint,
            "event_watermark": 11,
            "state": {"goal": "Second state"},
        },
    )
    assert advanced.status_code == 201, advanced.text
    assert advanced.json()["sequence"] == 2
    assert advanced.json()["parent_checkpoint"] == checkpoint
    assert create(env, body).status_code == 409
    assert get(env, checkpoint).json() == saved
    assert create(env, body, headers).json() == receipt
    assert all(item["type"] != "checkpoint" for item in env.recall().json()["items"])
    assert (
        env.client.post(
            "/v1/explain", json={"memory_id": checkpoint}, headers=env.headers()
        ).status_code
        == 404
    )


def test_parallel_checkpoint_head_and_replay_have_one_effect(env):
    body = payload(env)
    headers = env.headers()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: create(env, body, headers), range(6)))
    assert all(response.status_code == 201 for response in results)
    assert len({response.json()["checkpoint_id"] for response in results}) == 1
    parent = results[0].json()["checkpoint_id"]
    next_body = {**body, "expected_head": parent}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(env, next_body), range(2)))
    assert sorted(response.status_code for response in results) == [201, 409]


def test_restore_forks_without_rewinding_or_reexecuting_effects(env):
    operation = str(uuid4())
    body = payload(
        env,
        state={
            "goal": "Reconcile an interrupted operation",
            "pending_effects": [
                {"operation_id": operation, "description": "Send request", "status": "dispatched"}
            ],
        },
    )
    original = create(env, body).json()["checkpoint_id"]
    later = create(env, {**body, "expected_head": original}).json()["checkpoint_id"]
    target = str(uuid4())
    headers = env.headers()
    response = restore(env, original, headers=headers, target_branch_id=target)
    assert response.status_code == 201, response.text
    fork = response.json()
    assert fork["branch_id"] == target and fork["sequence"] == 1
    assert fork["parent_checkpoint"] == original
    assert fork["requires_reconciliation"] == [operation]
    assert fork["resume_allowed"] is False and fork["automatic_reexecution"] is False
    assert fork["state"]["pending_effects"][0]["status"] == "unknown"
    assert get(env, original).json()["state"]["pending_effects"][0]["status"] == "dispatched"
    assert restore(env, original, headers=headers, target_branch_id=target).json() == fork
    assert restore(env, original, target_branch_id=target).status_code == 409
    assert restore(env, original, target_branch_id=body["branch_id"]).status_code == 409
    assert create(env, {**body, "expected_head": later}).json()["sequence"] == 3


def test_harness_watermark_and_schema_compatibility(env):
    body = payload(env)
    checkpoint = create(env, body).json()["checkpoint_id"]
    for incompatible in [{"harness_version": "2"}, {"harness_id": "other"}]:
        assert restore(env, checkpoint, **incompatible).status_code == 422
        assert create(env, {**body, "expected_head": checkpoint, **incompatible}).status_code == 409
    assert restore(env, checkpoint, state_schema_version=2).status_code == 422
    assert create(env, {**body, "state_schema_version": 2}).status_code == 422
    assert (
        create(env, {**body, "expected_head": checkpoint, "event_watermark": 9}).status_code == 409
    )
    assert get(env, checkpoint).json()["event_watermark"] == 10


def test_checkpoint_identity_and_reference_isolation(env):
    source = env.observe().json()["memory_id"]
    body = payload(env, memory_refs=[{"memory_id": source}])
    checkpoint = create(env, body).json()["checkpoint_id"]
    for index in [1, 2]:
        assert get(env, checkpoint, index).status_code == 404
        assert restore(env, checkpoint, headers=env.headers(index)).status_code == 404
        assert create(env, body, env.headers(index)).status_code == 404
        other = payload(
            env, scope_id=str(env.scopes[index]), run_id=body["run_id"], branch_id=body["branch_id"]
        )
        assert create(env, other, env.headers(index)).status_code == 201
        other["memory_refs"] = [{"memory_id": source}]
        other["branch_id"] = str(uuid4())
        assert create(env, other, env.headers(index)).status_code == 404
    assert get(env, str(uuid4())).status_code == 404
    assert get(env, source).status_code == 404


def test_references_are_same_scope_exact_revisions_and_canonicalized(env):
    source = env.observe().json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    refs = [{"memory_id": source, "revision": 1}, {"memory_id": assertion, "revision": 1}]
    checkpoint = create(env, memory_refs=refs).json()["checkpoint_id"]
    assert get(env, checkpoint).json()["memory_refs"] == sorted(refs, key=lambda r: r["memory_id"])
    for invalid in [
        [{"memory_id": source, "revision": 2}],
        [{"memory_id": assertion, "revision": 2}],
        [{"memory_id": checkpoint, "revision": 1}],
        [refs[0], refs[0]],
    ]:
        assert create(env, memory_refs=invalid).status_code == 422
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write'])""",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    assert (
        create(env, scope_id=str(env.scopes[2]), memory_refs=[{"memory_id": source}]).status_code
        == 422
    )


def test_authorization_revocation_rechecks_get_restore_and_idempotency(env):
    body = payload(env)
    key = env.headers()
    checkpoint = create(env, body, key).json()["checkpoint_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET permissions = ARRAY['read']
               WHERE tenant_id = %s AND principal_id = %s""",
            (env.tenants[0], env.principals[0]),
        )
    assert get(env, checkpoint).status_code == 200
    assert restore(env, checkpoint).status_code == 404
    assert create(env, body, key).status_code == 404
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET permissions = '{}'
               WHERE tenant_id = %s AND principal_id = %s""",
            (env.tenants[0], env.principals[0]),
        )
    assert get(env, checkpoint).status_code == 404
    assert restore(env, checkpoint).status_code == 404


@pytest.mark.parametrize("root_kind", ["episode", "assertion", "checkpoint"])
def test_forget_invalidates_checkpoint_descendants_and_forks(env, root_kind):
    source = env.observe().json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    body = payload(env, memory_refs=[{"memory_id": assertion}])
    key = env.headers()
    first = create(env, body, key).json()["checkpoint_id"]
    second = create(env, {**body, "expected_head": first, "memory_refs": []}).json()[
        "checkpoint_id"
    ]
    target = str(uuid4())
    restore_key = env.headers()
    fork = restore(env, first, headers=restore_key, target_branch_id=target).json()["checkpoint_id"]
    root = {"episode": source, "assertion": assertion, "checkpoint": first}[root_kind]
    request = {"memory_ids": [root], "reason": "test", "mode": "preview"}
    preview = env.client.post("/v1/forget", json=request, headers=env.headers())
    count = {"episode": 5, "assertion": 4, "checkpoint": 3}[root_kind]
    assert preview.json()["object_count"] == count
    assert get(env, first).status_code == 200
    deleted = env.client.post(
        "/v1/forget", json={**request, "mode": "purge"}, headers=env.headers()
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["object_count"] == count
    for checkpoint in [first, second, fork]:
        assert get(env, checkpoint).status_code == 404
        assert restore(env, checkpoint).status_code == 404
    assert create(env, body, key).status_code == 404
    assert restore(env, first, headers=restore_key, target_branch_id=target).status_code == 404
    assert create(env, {**body, "expected_head": second}).status_code == 409
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.checkpoint WHERE tenant_id = %s", (env.tenants[0],)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.checkpoint_reference WHERE tenant_id = %s",
                (env.tenants[0],),
            ).fetchone()[0]
            == 0
        )


def test_checkpoint_checksum_tampering_fails_closed(env):
    checkpoint = create(env).json()["checkpoint_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.checkpoint SET state = jsonb_set(state,'{goal}','"DO_NOT_ECHO"')
               WHERE id = %s""",
            (checkpoint,),
        )
    for response in [get(env, checkpoint), restore(env, checkpoint)]:
        assert response.status_code == 409
        assert response.json()["code"] == "checkpoint_invalidated"
        assert "DO_NOT_ECHO" not in response.text


def test_failed_checkpoint_save_rolls_back_branch_and_idempotency(env, monkeypatch):
    body = payload(env)
    headers = env.headers()
    audit = MemoryService.audit

    async def fail(self, action, target):
        raise MemoryError("publication_failed", 503)

    monkeypatch.setattr(MemoryService, "audit", fail)
    assert create(env, body, headers).status_code == 503
    monkeypatch.setattr(MemoryService, "audit", audit)
    created = create(env, body, headers)
    assert created.status_code == 201 and created.json()["sequence"] == 1
    assert create(env, body, headers).json() == created.json()


def test_checkpoint_limits_validate_actual_body_bytes_and_references(env):
    body = payload(env, state={"goal": "Large snapshot", "constraints": ["x" * 4096] * 64})
    data = json.dumps(body).encode()
    assert len(data) > 256 * 1024
    key = {**env.headers(), "Content-Type": "application/json"}
    exact = data + b" " * (1048576 - len(data))
    response = env.client.post("/v1/checkpoints", content=exact, headers=key)
    assert response.status_code == 201, response.text
    assert len(get(env, response.json()["checkpoint_id"]).json()["state"]["constraints"]) == 64
    assert env.client.post("/v1/checkpoints", content=exact + b" ", headers=key).status_code == 413
    assert (
        create(env, memory_refs=[{"memory_id": str(uuid4())} for _ in range(101)]).status_code
        == 422
    )
    assert create(env, state={"goal": "test", "pickle": "not supported"}).status_code == 422
    missing = payload(env)
    del missing["expected_head"]
    assert create(env, missing).status_code == 422


def test_historical_assertion_reference_survives_correction_but_not_source_purge(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    checkpoint = create(env, memory_refs=[{"memory_id": assertion, "revision": 1}]).json()[
        "checkpoint_id"
    ]
    corrected = env.client.post(
        f"/v1/assertions/{assertion}/revisions",
        headers=env.headers(),
        json={
            "expected_revision": 1,
            "value": "Silver",
            "evidence": [{"memory_id": source, "quote": "Silver"}],
            "explicit_intent": True,
            "reason": "correction",
        },
    )
    assert corrected.status_code == 201
    restored = restore(env, checkpoint)
    assert restored.status_code == 201
    assert restored.json()["memory_refs"] == [{"memory_id": assertion, "revision": 1}]
    deleted = env.client.post(
        "/v1/forget", json={"memory_ids": [source], "reason": "test"}, headers=env.headers()
    )
    assert deleted.status_code == 202, deleted.text
    assert get(env, checkpoint).status_code == 404


def test_current_epoch_changes_do_not_invalidate_independent_live_state(env):
    checkpoint = create(env).json()["checkpoint_id"]
    source = env.observe().json()["memory_id"]
    assert (
        env.client.post(
            "/v1/forget", json={"memory_ids": [source], "reason": "test"}, headers=env.headers()
        ).status_code
        == 202
    )
    response = get(env, checkpoint)
    assert response.status_code == 200
    assert response.json()["current_deletion_epoch"] > response.json()["saved_deletion_epoch"]
    assert restore(env, checkpoint).status_code == 201


def test_runtime_cannot_edit_checkpoint_payload_or_rewind_branch(env):
    body = payload(env)
    first = create(env, body).json()["checkpoint_id"]
    second = create(env, {**body, "expected_head": first}).json()["checkpoint_id"]
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE memory.checkpoint SET state = '{}' WHERE id = %s", (first,))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                """UPDATE memory.checkpoint_branch SET head_id = %s, sequence = 1
                   WHERE tenant_id = %s AND scope_id = %s AND run_id = %s AND branch_id = %s""",
                (first, env.tenants[0], env.scopes[0], body["run_id"], body["branch_id"]),
            )
    assert get(env, second).status_code == 200


def test_checkpoint_routes_publish_typed_contracts_and_capabilities(env):
    capabilities = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert capabilities["checkpoints"] is True and capabilities["tool_effect_ledger"] is True
    assert capabilities["schema_version"] == 9
    schema = env.client.get("/openapi.json").json()
    for path, verb, status in [
        ("/v1/checkpoints", "post", "201"),
        ("/v1/checkpoints/restore", "post", "201"),
        ("/v1/checkpoints/{checkpoint_id}", "get", "200"),
    ]:
        contract = schema["paths"][path][verb]
        assert contract["security"] == [{"BearerAuth": []}]
        assert "$ref" in contract["responses"][status]["content"]["application/json"]["schema"]


def test_checkpoint_metadata_hmac_is_tenant_scoped(env):
    async def checksum_for(index):
        async with await connect(env.settings.database_url) as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('pgag.tenant_id',%s,true)", (str(env.tenants[index]),)
                )
                memory = MemoryService(
                    conn, Identity(tenant_id=env.tenants[index], principal_id=env.principals[index])
                )
                return await Checkpoints(memory).checksum({"identical": "payload"})

    assert asyncio.run(checksum_for(0)) != asyncio.run(checksum_for(1))


def test_exact_reference_count_limit_and_batch_purge(env):
    sources = [uuid4() for _ in range(100)]
    with psycopg.connect(env.admin_url) as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO memory.object(tenant_id,id,scope_id,kind)
                   VALUES (%s,%s,%s,'episode')""",
                [(env.tenants[0], source, env.scopes[0]) for source in sources],
            )
            cursor.executemany(
                """INSERT INTO memory.episode
                   (tenant_id,id,scope_id,occurred_at,content,consent_reference)
                   VALUES (%s,%s,%s,clock_timestamp(),'fixture','test')""",
                [(env.tenants[0], source, env.scopes[0]) for source in sources],
            )
    response = create(env, memory_refs=[{"memory_id": str(source)} for source in sources])
    assert response.status_code == 201, response.text
    checkpoint = response.json()["checkpoint_id"]
    assert len(get(env, checkpoint).json()["memory_refs"]) == 100
    deleted = env.client.post(
        "/v1/forget",
        json={"memory_ids": [str(source) for source in sources], "reason": "test"},
        headers=env.headers(),
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["object_count"] == 101
    assert get(env, checkpoint).status_code == 404


def test_database_enforces_reference_count_kind_and_scope(env):
    source = env.observe().json()["memory_id"]
    foreign = env.observe(index=2).json()["memory_id"]
    checkpoint = create(env, memory_refs=[{"memory_id": source}]).json()["checkpoint_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write'])""",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                "DELETE FROM memory.checkpoint_reference WHERE checkpoint_id = %s", (checkpoint,)
            )
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for target, kind, revision in [(foreign, "episode", 1), (source, "assertion", 2)]:
            with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute(
                    """SELECT set_config('pgag.tenant_id',%s,true),
                              set_config('pgag.principal_id',%s,true)""",
                    (str(env.tenants[0]), str(env.principals[0])),
                )
                conn.execute(
                    """INSERT INTO memory.checkpoint_reference
                       (tenant_id,checkpoint_id,scope_id,source_id,source_kind,source_revision)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (env.tenants[0], checkpoint, env.scopes[0], target, kind, revision),
                )
    assert get(env, checkpoint).status_code == 200


def test_parallel_restore_idempotency_creates_one_fork(env):
    checkpoint = create(env).json()["checkpoint_id"]
    target = str(uuid4())
    headers = env.headers()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: restore(env, checkpoint, headers=headers, target_branch_id=target),
                range(4),
            )
        )
    assert all(result.status_code == 201 for result in results)
    assert len({result.json()["checkpoint_id"] for result in results}) == 1


def test_checkpoint_survives_actual_api_process_crash(env, api_process):
    body = payload(env)
    headers = env.headers()
    with api_process("first-api.log") as (client, process):
        saved = client.post("/v1/checkpoints", json=body, headers=headers)
        assert saved.status_code == 201, saved.text
        receipt = saved.json()
        process.kill()
        process.wait(timeout=10)
    with api_process("restarted-api.log") as (client, _):
        replayed = client.post("/v1/checkpoints", json=body, headers=headers)
        assert replayed.status_code == 201
        assert replayed.json() == receipt
        recovered = client.get("/v1/checkpoints/" + receipt["checkpoint_id"], headers=env.headers())
        assert recovered.status_code == 200
        assert recovered.json()["state"]["goal"] == body["state"]["goal"]
        assert recovered.json()["checksum"] == receipt["checksum"]
