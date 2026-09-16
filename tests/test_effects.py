from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from pg_agmemory.service import MemoryError, MemoryService

pytestmark = pytest.mark.integration


def bootstrap(env, index=0, **overrides):
    body = {
        "scope_id": str(env.scopes[index]),
        "run_id": str(uuid4()),
        "branch_id": str(uuid4()),
        "expected_head": None,
        "harness_id": "ledger-tests",
        "harness_version": "1",
        "event_watermark": 1,
        "state": {"goal": "Recover without repeating effects"},
        **overrides,
    }
    response = env.client.post("/v1/checkpoints", json=body, headers=env.headers(index))
    assert response.status_code == 201, response.text
    return {**body, **response.json()}


def effect_payload(env, run, **overrides):
    return {
        "scope_id": run["scope_id"],
        "run_id": run["run_id"],
        "operation_id": str(uuid4()),
        "tool_name": "test.send",
        "action_hash": "a" * 64,
        "memory_refs": [],
        **overrides,
    }


def plan(env, body, headers=None):
    return env.client.post("/v1/tool-effects", json=body, headers=headers or env.headers())


def transition_body(revision, status, **overrides):
    return {
        "expected_revision": revision,
        "status": status,
        "reason": "Reported by test",
        **overrides,
    }


def transition(env, effect, revision, status, headers=None, **overrides):
    return env.client.post(
        f"/v1/tool-effects/{effect}/transitions",
        headers=headers or env.headers(),
        json=transition_body(revision, status, **overrides),
    )


def detail(env, effect, index=0):
    return env.client.get(f"/v1/tool-effects/{effect}", headers=env.headers(index))


def envelope(env, checkpoint):
    return env.client.get(f"/v1/checkpoints/{checkpoint}", headers=env.headers())


def restore_body(run, **overrides):
    return {
        "checkpoint_id": run["checkpoint_id"],
        "target_branch_id": str(uuid4()),
        "harness_id": run["harness_id"],
        "harness_version": run["harness_version"],
        **overrides,
    }


@pytest.mark.parametrize("terminal", ["confirmed", "failed"])
def test_effect_intent_fsm_history_and_stable_external_key(env, terminal):
    run = bootstrap(env)
    body = effect_payload(env, run)
    first = plan(env, body)
    assert first.status_code == 201, first.text
    effect = first.json()["memory_id"]
    initial = detail(env, effect).json()
    assert initial["status"] == "planned" and initial["revision"] == 1
    assert initial["action_fingerprint"] != body["action_hash"]
    assert "action_hash" not in initial
    assert len(initial["external_idempotency_key"]) == 64
    assert transition(env, effect, 1, "dispatched").status_code == 201
    assert transition(env, effect, 2, "unknown").status_code == 201
    assert transition(env, effect, 3, "dispatched").status_code == 409
    assert transition(env, effect, 3, terminal).status_code == 422
    assert (
        transition(
            env,
            effect,
            3,
            terminal,
            receipt_reference="provider-result-1",
            receipt_source="provider_receipt",
        ).status_code
        == 201
    )
    result = detail(env, effect).json()
    assert [event["status"] for event in result["history"]] == [
        "planned",
        "dispatched",
        "unknown",
        terminal,
    ]
    assert result["external_idempotency_key"] == initial["external_idempotency_key"]
    assert result["history"][-1]["receipt_reference"] == "provider-result-1"
    assert result["revision"] == 4
    assert transition(env, effect, 4, "unknown").status_code == 409
    assert sorted(event["recorded_at"] for event in result["history"]) == [
        event["recorded_at"] for event in result["history"]
    ]
    assert not any(item["memory_id"] == effect for item in env.recall().json()["items"])


def test_effect_operation_identity_and_transition_replay_are_transactional(env):
    run = bootstrap(env)
    body = effect_payload(env, run)
    headers = env.headers()
    first = plan(env, body, headers)
    effect = first.json()["memory_id"]
    assert plan(env, body, headers).json() == first.json()
    assert plan(env, body).json() == first.json()
    assert plan(env, {**body, "action_hash": "b" * 64}).status_code == 409
    assert plan(env, {**body, "tool_name": "other"}, headers).status_code == 409
    key = env.headers()
    dispatch = transition(env, effect, 1, "dispatched", headers=key)
    assert dispatch.status_code == 201
    assert transition(env, effect, 2, "unknown").status_code == 201
    assert transition(env, effect, 1, "dispatched", headers=key).json() == dispatch.json()
    assert transition(env, effect, 1, "dispatched").status_code == 409
    assert transition(env, effect, 1, "unknown", headers=key).status_code == 409
    other = plan(env, effect_payload(env, run)).json()["memory_id"]
    assert transition(env, other, 1, "dispatched", headers=key).status_code == 409


def test_parallel_dispatch_cas_and_parallel_idempotency(env):
    run = bootstrap(env)
    body = effect_payload(env, run)
    key = env.headers()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: plan(env, body, key), range(6)))
    assert all(result.status_code == 201 for result in results)
    assert len({result.json()["memory_id"] for result in results}) == 1
    effect = results[0].json()["memory_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda status: transition(env, effect, 1, status), ["dispatched", "unknown"])
        )
    assert sorted(result.status_code for result in results) == [201, 409]
    assert detail(env, effect).json()["revision"] == 2


def test_restore_fences_run_wide_dispatch_even_if_snapshot_omits_effect(env):
    run = bootstrap(env)
    body = effect_payload(env, run)
    effect = plan(env, body).json()["memory_id"]
    assert transition(env, effect, 1, "dispatched").status_code == 201
    before = envelope(env, run["checkpoint_id"]).json()
    assert before["state"]["pending_effects"] == []
    assert before["requires_reconciliation"] == [body["operation_id"]]
    assert before["resume_allowed"] is False
    request = restore_body(run)
    headers = env.headers()
    restored = env.client.post("/v1/checkpoints/restore", json=request, headers=headers)
    assert restored.status_code == 201, restored.text
    assert restored.json()["tool_effects"][0]["status"] == "unknown"
    current = detail(env, effect).json()
    assert current["revision"] == 3
    assert current["history"][-1]["origin"] == "checkpoint_restore"
    assert (
        env.client.post("/v1/checkpoints/restore", json=request, headers=headers).json()
        == restored.json()
    )
    assert detail(env, effect).json()["revision"] == 3
    assert (
        transition(
            env,
            effect,
            2,
            "confirmed",
            receipt_reference="receipt",
            receipt_source="provider_receipt",
        ).status_code
        == 409
    )
    assert (
        transition(
            env,
            effect,
            3,
            "confirmed",
            receipt_reference="receipt",
            receipt_source="provider_receipt",
        ).status_code
        == 201
    )
    after = envelope(env, restored.json()["checkpoint_id"]).json()
    assert after["resume_allowed"] is True and after["automatic_reexecution"] is False
    assert after["requires_reconciliation"] == []


@pytest.mark.parametrize("hint", ["planned", "unknown"])
def test_untracked_hints_and_conflicting_ledger_are_conservative(env, hint):
    operation = str(uuid4())
    run = bootstrap(
        env,
        state={
            "goal": "Legacy state",
            "pending_effects": [
                {"operation_id": operation, "status": hint, "description": "Historical hint"}
            ],
        },
    )
    untracked = envelope(env, run["checkpoint_id"]).json()
    assert untracked["untracked_effects"] == [operation] and not untracked["resume_allowed"]
    effect = plan(env, effect_payload(env, run, operation_id=operation)).json()["memory_id"]
    tracked = envelope(env, run["checkpoint_id"]).json()
    assert tracked["untracked_effects"] == []
    assert tracked["resume_allowed"] is (hint == "planned")
    assert transition(env, effect, 1, "unknown").status_code == 201
    assert (
        transition(
            env,
            effect,
            2,
            "failed",
            receipt_reference="operator-checked-no-effect",
            receipt_source="operator_review",
        ).status_code
        == 201
    )
    assert envelope(env, run["checkpoint_id"]).json()["resume_allowed"] is True


def test_scope_run_and_current_permissions_bound_every_effect_surface(env):
    run = bootstrap(env)
    body = effect_payload(env, run)
    effect = plan(env, body).json()["memory_id"]
    for index in [1, 2]:
        assert detail(env, effect, index).status_code == 404
        assert plan(env, body, env.headers(index)).status_code == 404
        assert (
            transition(env, effect, 1, "dispatched", headers=env.headers(index)).status_code == 404
        )
    other_run = bootstrap(env, 1, run_id=run["run_id"])
    other = plan(
        env, effect_payload(env, other_run, operation_id=body["operation_id"]), env.headers(1)
    )
    assert other.status_code == 201
    assert (
        detail(env, other.json()["memory_id"], 1).json()["external_idempotency_key"]
        != (detail(env, effect).json()["external_idempotency_key"])
    )
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET permissions = ARRAY['read']
               WHERE tenant_id = %s AND principal_id = %s""",
            (env.tenants[0], env.principals[0]),
        )
    assert detail(env, effect).status_code == 200
    assert transition(env, effect, 1, "dispatched").status_code == 404
    assert plan(env, body).status_code == 404


@pytest.mark.parametrize("delete_source", [False, True])
def test_forget_erases_effect_history_and_seals_all_checkpoint_branches(env, delete_source):
    run = bootstrap(env)
    source = env.observe().json()["memory_id"]
    body = effect_payload(env, run, memory_refs=[{"memory_id": source}])
    key = env.headers()
    effect = plan(env, body, key).json()["memory_id"]
    assert transition(env, effect, 1, "dispatched").status_code == 201
    assert (
        transition(
            env,
            effect,
            2,
            "confirmed",
            receipt_reference="private-receipt",
            receipt_source="provider_receipt",
        ).status_code
        == 201
    )
    sibling = env.client.post(
        "/v1/checkpoints/restore", json=restore_body(run), headers=env.headers()
    )
    assert sibling.status_code == 201
    other_body = effect_payload(env, run)
    other = plan(env, other_body).json()["memory_id"]
    in_flight = plan(env, effect_payload(env, run)).json()["memory_id"]
    dispatch_key = env.headers()
    dispatched = transition(env, in_flight, 1, "dispatched", headers=dispatch_key)
    assert dispatched.status_code == 201
    root = source if delete_source else effect
    deleted = env.client.post(
        "/v1/forget", json={"memory_ids": [root], "reason": "test"}, headers=env.headers()
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["object_count"] == (4 if delete_source else 3)
    assert detail(env, effect).status_code == 404
    assert plan(env, body, key).status_code == 404
    assert plan(env, body).status_code == 404
    assert envelope(env, run["checkpoint_id"]).status_code == 404
    assert envelope(env, sibling.json()["checkpoint_id"]).status_code == 404
    assert plan(env, effect_payload(env, run)).status_code == 409
    fresh_checkpoint = {
        key: value
        for key, value in run.items()
        if key
        in ("scope_id", "run_id", "harness_id", "harness_version", "event_watermark", "state")
    } | {"branch_id": str(uuid4()), "expected_head": None}
    assert (
        env.client.post("/v1/checkpoints", json=fresh_checkpoint, headers=env.headers()).status_code
        == 409
    )
    assert detail(env, other).json()["run_invalidated"] is True
    assert (
        transition(env, in_flight, 1, "dispatched", headers=dispatch_key).json()
        == dispatched.json()
    )
    assert detail(env, in_flight).json()["run_invalidated"] is True
    assert transition(env, other, 1, "dispatched").status_code == 409
    assert transition(env, other, 1, "unknown").status_code == 201
    assert (
        transition(
            env,
            other,
            2,
            "failed",
            receipt_reference="manual-check",
            receipt_source="operator_review",
        ).status_code
        == 201
    )
    with psycopg.connect(env.admin_url) as conn:
        for table in ["tool_effect_revision", "tool_effect_reference"]:
            assert (
                conn.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM memory.{} WHERE effect_id = %s").format(
                        psycopg.sql.Identifier(table)
                    ),
                    (effect,),
                ).fetchone()[0]
                == 0
            )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_ops.tool_effect_identity WHERE effect_id = %s",
                (effect,),
            ).fetchone()[0]
            == 1
        )


def test_invalid_transitions_receipts_and_source_references_are_explicit(env):
    run = bootstrap(env)
    body = effect_payload(env, run)
    for invalid in [
        {"action_hash": "invalid"},
        {"action_hash": "A" * 64},
        {"arguments": {"secret": "neverstore"}},
        {"tenant_id": str(env.tenants[1])},
        {"memory_refs": [{"memory_id": str(uuid4())} for _ in range(101)]},
    ]:
        assert plan(env, {**body, **invalid}).status_code == 422
    assert plan(env, {**body, "run_id": str(uuid4())}).status_code == 404
    effect = plan(env, body).json()["memory_id"]
    assert (
        transition(
            env,
            effect,
            1,
            "confirmed",
            receipt_reference="receipt",
            receipt_source="provider_receipt",
        ).status_code
        == 409
    )
    assert (
        transition(env, effect, 1, "dispatched", receipt_reference="unexpected").status_code == 422
    )
    assert transition(env, effect, True, "dispatched").status_code == 422
    assert transition(env, effect, 1, "unknown", origin="checkpoint_restore").status_code == 422
    source = env.observe().json()["memory_id"]
    assert (
        plan(
            env, effect_payload(env, run, memory_refs=[{"memory_id": source, "revision": 2}])
        ).status_code
        == 422
    )
    assert (
        plan(env, effect_payload(env, run, memory_refs=[{"memory_id": effect}])).status_code == 422
    )


def test_failed_effect_publication_rolls_back_identity_and_head(env, monkeypatch):
    run = bootstrap(env)
    body = effect_payload(env, run)
    key = env.headers()
    original = MemoryService.audit

    async def fail(self, action, target):
        raise MemoryError("publication_failed", 503)

    monkeypatch.setattr(MemoryService, "audit", fail)
    assert plan(env, body, key).status_code == 503
    monkeypatch.setattr(MemoryService, "audit", original)
    effect = plan(env, body, key).json()["memory_id"]
    assert detail(env, effect).json()["revision"] == 1
    monkeypatch.setattr(MemoryService, "audit", fail)
    dispatch_key = env.headers()
    assert transition(env, effect, 1, "dispatched", headers=dispatch_key).status_code == 503
    monkeypatch.setattr(MemoryService, "audit", original)
    assert detail(env, effect).json()["revision"] == 1
    assert transition(env, effect, 1, "dispatched", headers=dispatch_key).status_code == 201
    request = restore_body(run)
    restore_key = env.headers()
    monkeypatch.setattr(MemoryService, "audit", fail)
    assert (
        env.client.post("/v1/checkpoints/restore", json=request, headers=restore_key).status_code
        == 503
    )
    monkeypatch.setattr(MemoryService, "audit", original)
    current = detail(env, effect).json()
    assert current["revision"] == 2 and current["status"] == "dispatched"
    assert (
        env.client.post("/v1/checkpoints/restore", json=request, headers=restore_key).status_code
        == 201
    )
    assert detail(env, effect).json()["revision"] == 3


def test_database_guards_event_history_and_server_assigned_fields(env):
    run = bootstrap(env)
    effect = plan(env, effect_payload(env, run)).json()["memory_id"]
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                "UPDATE memory.tool_effect_revision SET status = 'confirmed' WHERE effect_id = %s",
                (effect,),
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                """INSERT INTO memory.tool_effect_revision
                   (tenant_id,effect_id,revision,status,reason,origin,receipt_reference,receipt_source)
                   VALUES (%s,%s,2,'confirmed','forged','api','receipt','provider_receipt')""",
                (env.tenants[0], effect),
            )
    assert detail(env, effect).json()["status"] == "planned"


def test_actual_api_crash_after_simulated_external_success_requires_reconciliation(
    env, api_process
):
    run = bootstrap(env)
    body = effect_payload(env, run)
    provider_receipts = {}
    external_calls = []
    with api_process("effect-before-crash.log") as (client, process):
        first = client.post("/v1/tool-effects", json=body, headers=env.headers())
        assert first.status_code == 201, first.text
        effect = first.json()["memory_id"]
        dispatched = client.post(
            f"/v1/tool-effects/{effect}/transitions",
            json=transition_body(1, "dispatched"),
            headers=env.headers(),
        )
        assert dispatched.status_code == 201
        key = client.get(f"/v1/tool-effects/{effect}", headers=env.headers()).json()[
            "external_idempotency_key"
        ]
        external_calls.append(key)
        provider_receipts[key] = "simulated-provider-receipt"
        process.kill()
        process.wait(timeout=10)
    with api_process("effect-after-crash.log") as (client, _):
        recovered = client.post(
            "/v1/checkpoints/restore", json=restore_body(run), headers=env.headers()
        )
        assert recovered.status_code == 201, recovered.text
        assert recovered.json()["resume_allowed"] is False
        assert recovered.json()["tool_effects"][0]["status"] == "unknown"
        current = client.get(f"/v1/tool-effects/{effect}", headers=env.headers()).json()
        assert current["external_idempotency_key"] == key
        confirmed = client.post(
            f"/v1/tool-effects/{effect}/transitions",
            json=transition_body(
                current["revision"],
                "confirmed",
                receipt_reference=provider_receipts[key],
                receipt_source="provider_receipt",
            ),
            headers=env.headers(),
        )
        assert confirmed.status_code == 201
        assert (
            client.get(
                "/v1/checkpoints/" + recovered.json()["checkpoint_id"], headers=env.headers()
            ).json()["resume_allowed"]
            is True
        )
    assert external_calls == [key]


def test_effect_caps_and_exact_reference_limit(env):
    run = bootstrap(env)
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
                   VALUES (%s,%s,%s,clock_timestamp(),'test source','test')""",
                [(env.tenants[0], source, env.scopes[0]) for source in sources],
            )
    first_body = effect_payload(env, run, memory_refs=[{"memory_id": str(s)} for s in sources])
    first = plan(env, first_body)
    assert first.status_code == 201, first.text
    assert len(detail(env, first.json()["memory_id"]).json()["memory_refs"]) == 100
    for _ in range(99):
        assert plan(env, effect_payload(env, run)).status_code == 201
    exhausted = plan(env, effect_payload(env, run))
    assert exhausted.status_code == 422 and exhausted.json()["code"] == "effect_limit_exceeded"
    assert plan(env, first_body).json() == first.json()
    assert len(envelope(env, run["checkpoint_id"]).json()["tool_effects"]) == 100


def test_schema_upgrade_preserves_v3_checkpoint_checksum_and_gates_legacy_hints(env, database):
    record = database[2][0]
    token = env.token(sub=record["subject"])
    response = env.client.get(
        "/v1/checkpoints/" + record["checkpoint"]["checkpoint_id"],
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["checksum"] == record["checksum"]
    assert response.json()["state"] == record["checkpoint"]["state"]
    assert response.json()["resume_allowed"] is False
    assert len(response.json()["untracked_effects"]) == 1
    assert response.json()["tool_effects"] == []
    schema = env.client.get("/openapi.json").json()
    for path, verb, status in [
        ("/v1/tool-effects", "post", "201"),
        ("/v1/tool-effects/{memory_id}/transitions", "post", "201"),
        ("/v1/tool-effects/{memory_id}", "get", "200"),
    ]:
        operation = schema["paths"][path][verb]
        assert operation["security"] == [{"BearerAuth": []}]
        assert "$ref" in operation["responses"][status]["content"]["application/json"]["schema"]


def test_historical_assertion_dependency_and_source_erasure(env):
    run = bootstrap(env)
    first = env.observe().json()["memory_id"]
    assertion = env.remember(first).json()["memory_id"]
    effect = plan(
        env, effect_payload(env, run, memory_refs=[{"memory_id": assertion, "revision": 1}])
    ).json()["memory_id"]
    second = env.observe("Silver").json()["memory_id"]
    revised = env.client.post(
        f"/v1/assertions/{assertion}/revisions",
        headers=env.headers(),
        json={
            "expected_revision": 1,
            "value": "Silver",
            "evidence": [{"memory_id": second, "quote": "Silver"}],
            "explicit_intent": True,
            "reason": "Correction",
        },
    )
    assert revised.status_code == 201
    assert detail(env, effect).json()["memory_refs"] == [{"memory_id": assertion, "revision": 1}]
    deleted = env.client.post(
        "/v1/forget", json={"memory_ids": [first], "reason": "test"}, headers=env.headers()
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["object_count"] == 4
    assert detail(env, effect).status_code == 404
    assert envelope(env, run["checkpoint_id"]).status_code == 404
    assert (
        env.client.post(
            "/v1/explain", json={"memory_id": second}, headers=env.headers()
        ).status_code
        == 200
    )


def test_database_assigns_effect_actor_time_and_prevents_history_removal(env):
    run = bootstrap(env)
    effect = plan(env, effect_payload(env, run)).json()["memory_id"]
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                """INSERT INTO memory.tool_effect_revision
                   (tenant_id,effect_id,revision,status,reason,origin,actor_id,recorded_at)
                   VALUES (%s,%s,2,'dispatched','test','api',%s,'2000-01-01T00:00:00Z')""",
                (env.tenants[0], effect, env.principals[2]),
            )
            row = conn.execute(
                """SELECT actor_id,recorded_at > (
                       SELECT recorded_at FROM memory.tool_effect_revision
                       WHERE tenant_id = %s AND effect_id = %s AND revision = 1)
                   FROM memory.tool_effect_revision
                   WHERE tenant_id = %s AND effect_id = %s AND revision = 2""",
                (env.tenants[0], effect, env.tenants[0], effect),
            ).fetchone()
            assert row == (env.principals[0], True)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                """DELETE FROM memory.tool_effect_revision
                   WHERE tenant_id = %s AND effect_id = %s AND revision = 1""",
                (env.tenants[0], effect),
            )
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert len(detail(env, effect).json()["history"]) == 2


def test_restore_and_provider_confirmation_race_is_atomic(env):
    run = bootstrap(env)
    effect = plan(env, effect_payload(env, run)).json()["memory_id"]
    assert transition(env, effect, 1, "dispatched").status_code == 201
    with ThreadPoolExecutor(max_workers=2) as pool:
        restored = pool.submit(
            env.client.post,
            "/v1/checkpoints/restore",
            json=restore_body(run),
            headers=env.headers(),
        )
        confirmed = pool.submit(
            transition,
            env,
            effect,
            2,
            "confirmed",
            receipt_reference="provider-receipt",
            receipt_source="provider_receipt",
        )
        fork = restored.result()
        outcome = confirmed.result()
    assert fork.status_code == 201
    assert outcome.status_code in (201, 409)
    final = detail(env, effect).json()
    assert final["revision"] == 3
    assert final["status"] == ("confirmed" if outcome.status_code == 201 else "unknown")
    assert fork.json()["resume_allowed"] is (outcome.status_code == 201)
