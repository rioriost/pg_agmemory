import asyncio
import copy
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from pydantic import TypeAdapter

from pg_agmemory.jobs import job_transaction
from pg_agmemory.models import Capture, CaptureResult, Remember
from pg_agmemory.native_client import AdapterFailure, NativeHTTPClient
from pg_agmemory.service import MemoryError, MemoryService
from pg_agmemory.worker import run_once

pytestmark = pytest.mark.integration
TABLES = (
    "memory.object",
    "memory.episode",
    "memory.episode_lexical",
    "memory.assertion",
    "memory.assertion_revision",
    "memory.assertion_lexical",
    "memory.provenance_edge",
    "memory_ops.source_event",
    "memory_ops.idempotency",
    "memory_ops.job",
    "memory_ops.job_input",
    "memory_ops.job_identity",
    "memory_ops.audit_event",
)


def payload(env, index=0):
    return {
        "episode": {
            "scope_id": str(env.scopes[index]),
            "source_namespace": "capture-test",
            "source_event_id": str(uuid4()),
            "occurred_at": "2026-09-01T00:00:00Z",
            "content": "東京都の ACME 契約は Gold です。",
            "consent_reference": "synthetic-test-consent",
        },
        "memory": {
            "subject": "ACME",
            "predicate": "contract_tier",
            "value": "Gold",
            "evidence_quote": "Gold",
            "explicit_intent": True,
        },
    }


def capture(env, body, headers=None):
    return env.client.post("/v1/captures", json=body, headers=headers or env.headers())


def counts(env):
    with psycopg.connect(env.admin_url) as conn:
        return {
            table: conn.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE tenant_id = %s").format(
                    sql.Identifier(*table.split("."))
                ),
                (env.tenants[0],),
            ).fetchone()[0]
            for table in TABLES
        }


def job(env, result, index=0):
    return env.client.get("/v1/jobs/" + result["synthesis_job_id"], headers=env.headers(index))


def process(env, index=0):
    return asyncio.run(run_once(env.settings.database_url, env.subjects[index]))


def claim(env):
    async def execute():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            return await jobs.claim()

    return asyncio.run(execute())


def publish(env, lease):
    async def execute():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            return await jobs.publish(
                lease["job_id"], lease["lease_token"], Remember.model_validate(lease["payload"])
            )

    return asyncio.run(execute())


def purge(env, target):
    response = env.client.post(
        "/v1/forget",
        json={"memory_ids": [target], "mode": "purge", "reason": "synthetic capture test"},
        headers=env.headers(),
    )
    assert response.status_code == 202, response.text


def test_atomic_capture_publishes_only_after_worker_and_preserves_native_contracts(env):
    body, headers = payload(env), env.headers()
    response = capture(env, body, headers)
    assert response.status_code == 201, response.text
    result = response.json()
    assert set(result) == {"memory_id", "revision", "synthesis_job_id"}
    assert result["revision"] == 1 and result["memory_id"] != result["synthesis_job_id"]
    queued = job(env, result).json()
    assert queued["state"] == "pending" and queued["attempt"] == 0
    assert queued["kind"] == "structured_remember"
    assert queued["recipe_version"] == "structured-remember-v1"
    assert queued["input_refs"] == [{"memory_id": result["memory_id"], "revision": 1}]
    initial = env.recall().json()
    assert [item["memory_id"] for item in initial["items"]] == [result["memory_id"]]
    assert initial["coverage"]["jobs_pending"] is True
    assert initial["coverage"]["synthesis_pending"] is False
    assert counts(env)["memory.episode_lexical"] == 1
    assert capture(env, body, headers).json() == result
    assert capture(env, body).json() == result
    processed = process(env)
    assert processed["outcome"] == "succeeded"
    assert job(env, result).json()["result"]["memory_id"] == processed["result"]["memory_id"]
    explanation = env.client.post(
        "/v1/explain", json={"memory_id": processed["result"]["memory_id"]}, headers=env.headers()
    )
    assert explanation.status_code == 200 and "Gold" in explanation.text
    assert result["memory_id"] in explanation.text
    assert process(env) == {"outcome": "idle"}
    assert capture(env, body).json() == result
    assert counts(env)["memory.assertion"] == 1
    assert env.observe().json()["synthesis_job_id"] is None
    caps = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert caps["atomic_capture"]["max_jobs"] == 1
    assert caps["auto_synthesis"] is False and caps["recall_hook"]["capture"] is False
    assert "atomic_structured_capture" in caps["features"]


def test_existing_episode_and_job_are_reused_without_changing_legacy_replay(env):
    body, headers = payload(env), env.headers()
    raw = env.client.post("/v1/observe", json=body["episode"], headers=headers)
    assert raw.status_code == 201
    original = raw.json()
    result = capture(env, body, headers).json()
    assert result["memory_id"] == original["memory_id"]
    assert env.client.post("/v1/observe", json=body["episode"], headers=headers).json() == original
    assert original["synthesis_job_id"] is None
    direct_job = env.client.post(
        "/v1/jobs",
        json={
            "kind": "structured_remember",
            "memory": {
                "scope_id": body["episode"]["scope_id"],
                **{k: v for k, v in body["memory"].items() if k != "evidence_quote"},
                "evidence": [{"memory_id": original["memory_id"], "quote": "Gold"}],
            },
        },
        headers=env.headers(),
    )
    assert direct_job.status_code == 202
    assert direct_job.json()["job_id"] == result["synthesis_job_id"]
    assert counts(env)["memory.episode"] == counts(env)["memory_ops.job"] == 1


def test_capture_key_conflicts_and_source_identity_conflicts_are_atomic(env):
    body, headers = payload(env), env.headers()
    original = capture(env, body, headers).json()
    before = counts(env)
    changed = copy.deepcopy(body)
    changed["memory"]["predicate"] = "another_fact"
    conflict = capture(env, changed, headers)
    assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"
    assert counts(env) == before
    changed["episode"]["content"] += " changed"
    source_conflict = capture(env, changed)
    assert source_conflict.status_code == 409
    assert source_conflict.json()["code"] == "source_event_conflict"
    assert counts(env) == before
    changed["episode"] = body["episode"]
    separate = capture(env, changed)
    assert separate.status_code == 201
    assert separate.json()["memory_id"] == original["memory_id"]
    assert separate.json()["synthesis_job_id"] != original["synthesis_job_id"]
    assert counts(env)["memory.episode"] == 1 and counts(env)["memory_ops.job"] == 2


@pytest.mark.parametrize("shared_key", [True, False])
def test_concurrent_capture_replays_one_episode_and_one_job(env, shared_key):
    body, headers = payload(env), env.headers()
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(
            pool.map(lambda _: capture(env, body, headers if shared_key else None), range(6))
        )
    assert all(response.status_code == 201 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    assert counts(env)["memory.episode"] == counts(env)["memory_ops.job"] == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: process(env), range(4)))
    assert sum(outcome["outcome"] == "succeeded" for outcome in outcomes) == 1
    assert counts(env)["memory.assertion"] == 1


@pytest.mark.parametrize("stage", ["observe", "enqueue_job", "capture"])
def test_fault_after_each_write_rolls_back_entire_capture(env, monkeypatch, stage):
    body, headers, before = payload(env), env.headers(), counts(env)
    original = MemoryService.audit

    async def fail_after_write(self, action, target):
        await original(self, action, target)
        if action == stage:
            raise MemoryError("dependency_unavailable", 503)

    with monkeypatch.context() as patch:
        patch.setattr(MemoryService, "audit", fail_after_write)
        failed = capture(env, body, headers)
        assert failed.status_code == 503
    assert counts(env) == before
    assert capture(env, body, headers).status_code == 201
    assert counts(env)["memory.episode"] == counts(env)["memory_ops.job"] == 1


@pytest.mark.parametrize("existing_source", [True, False])
def test_bad_quote_rolls_back_new_source_without_removing_existing_source(env, existing_source):
    body, headers = payload(env), env.headers()
    if existing_source:
        assert (
            env.client.post("/v1/observe", json=body["episode"], headers=headers).status_code == 201
        )
    before = counts(env)
    body["memory"]["evidence_quote"] = "NOT_IN_EPISODE"
    failed = capture(env, body, headers)
    assert failed.status_code == 422 and failed.json()["code"] == "invalid_evidence"
    assert counts(env) == before
    body["memory"]["evidence_quote"] = "Gold"
    assert capture(env, body, headers).status_code == 201


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope_id", str(uuid4())),
        ("tenant_id", str(uuid4())),
        ("evidence", [{"memory_id": str(uuid4()), "quote": "Gold"}]),
        ("confidence", 1),
        ("explicit_intent", False),
        ("evidence_quote", ""),
        ("evidence_quote", "x" * 4097),
        ("predicate", "invalid predicate"),
        ("valid_from", "2026-09-01T00:00:00"),
        ("valid_to", "2026-08-31T00:00:00Z"),
    ],
)
def test_capture_validates_intent_and_forbids_caller_selected_evidence(env, field, value):
    body, before = payload(env), counts(env)
    body["memory"]["valid_from"] = "2026-09-01T00:00:00Z"
    body["memory"][field] = value
    failed = capture(env, body)
    assert failed.status_code == 422
    assert counts(env) == before


def test_capture_needs_authentication_idempotency_and_current_scope_access(env):
    body, before = payload(env), counts(env)
    assert env.client.post("/v1/captures", json=body).status_code == 401
    headers = env.headers()
    headers.pop("Idempotency-Key")
    assert capture(env, body, headers).status_code == 422
    for index in (1, 2):
        assert capture(env, body, env.headers(index)).status_code == 404
    assert counts(env) == before
    key = env.headers()
    result = capture(env, body, key).json()
    with psycopg.connect(env.admin_url) as admin:
        admin.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(env.tenants[0]),)
        )
        admin.execute(
            "UPDATE memory.scope_member SET permissions = ARRAY['read'] WHERE scope_id = %s",
            (env.scopes[0],),
        )
        admin.execute(
            "UPDATE memory.tenant SET access_epoch = access_epoch + 1 WHERE id = %s",
            (env.tenants[0],),
        )
    assert capture(env, body, key).status_code == capture(env, body).status_code == 404
    assert job(env, result).json()["state"] == "pending"
    assert process(env) == {"outcome": "idle"}


def test_capture_job_ownership_is_not_shared_between_authorized_principals(env):
    body = payload(env)
    first = capture(env, body).json()
    with psycopg.connect(env.admin_url) as admin:
        admin.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write'])""",
            (env.tenants[0], env.scopes[0], env.principals[2]),
        )
    second = capture(env, body, env.headers(2)).json()
    assert first["memory_id"] == second["memory_id"]
    assert first["synthesis_job_id"] != second["synthesis_job_id"]
    assert process(env, 2)["job_id"] == second["synthesis_job_id"]
    assert job(env, first).json()["state"] == "pending"
    assert counts(env)["memory.episode"] == 1


@pytest.mark.parametrize("target", ["episode", "job", "result"])
def test_capture_pair_replay_checks_both_current_references_and_never_resurrects(env, target):
    body, headers = payload(env), env.headers()
    result = capture(env, body, headers).json()
    published = process(env)["result"]["memory_id"]
    purge(
        env,
        {"episode": result["memory_id"], "job": result["synthesis_job_id"], "result": published}[
            target
        ],
    )
    before = counts(env)
    for key in (headers, env.headers()):
        failed = capture(env, body, key)
        assert failed.status_code == 404 and failed.json()["code"] == "not_found"
    assert counts(env) == before
    assert job(env, result).status_code == 404
    raw = env.client.post("/v1/observe", json=body["episode"], headers=env.headers())
    assert raw.status_code == (404 if target == "episode" else 201)
    explanation = env.client.post(
        "/v1/explain", json={"memory_id": published}, headers=env.headers()
    )
    assert explanation.status_code == (200 if target == "job" else 404)


def test_deleting_captured_source_fences_inflight_worker(env):
    body, headers = payload(env), env.headers()
    result = capture(env, body, headers).json()
    lease = claim(env)
    purge(env, result["memory_id"])
    with pytest.raises(MemoryError, match="not_found"):
        publish(env, lease)
    assert counts(env)["memory_ops.job"] == counts(env)["memory.assertion"] == 0
    assert capture(env, body, headers).status_code == 404


def test_capture_respects_exact_job_quota_without_orphaned_episode(env):
    body = payload(env)
    first = capture(env, body).json()
    for index in range(99):
        varied = {**body, "memory": {**body["memory"], "subject": f"ACME-{index}"}}
        assert capture(env, varied).status_code == 201
    assert counts(env)["memory_ops.job"] == 100 and counts(env)["memory.episode"] == 1
    assert capture(env, body).json() == first
    extra, headers, before = payload(env), env.headers(), counts(env)
    overflow = capture(env, extra, headers)
    assert overflow.status_code == 422 and overflow.json()["code"] == "job_limit_exceeded"
    assert counts(env) == before
    assert process(env)["outcome"] == "succeeded"
    assert capture(env, extra, headers).status_code == 201
    assert counts(env)["memory.episode"] == 2


def test_capture_pair_survives_api_restart_and_actual_worker_process(env, api_process):
    body, headers = payload(env), env.headers()
    with api_process("capture-before-restart.log") as (http, process):
        response = http.post("/v1/captures", json=body, headers=headers)
        assert response.status_code == 201
        original = response.json()
        process.kill()
        process.wait(timeout=10)
    with api_process("capture-after-restart.log") as (http, _):
        assert http.post("/v1/captures", json=body, headers=headers).json() == original
        worker = subprocess.run(
            [
                sys.executable,
                "-m",
                "pg_agmemory.cli",
                "worker",
                "--subject",
                env.subjects[0],
                "--once",
            ],
            env={"PATH": os.environ["PATH"], "PGAG_DATABASE_URL": env.settings.database_url},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert worker.returncode == 0, worker.stderr
        outcome = json.loads(worker.stdout)
        assert outcome["outcome"] == "succeeded"
        assert UUID(outcome["job_id"]) == UUID(original["synthesis_job_id"])
        assert counts(env)["memory.assertion"] == 1
        assert http.post("/v1/captures", json=body, headers=headers).json() == original


def test_capture_recovers_lost_committed_http_response_without_duplicate_pair(
    env, api_process, lose_first_response_transport
):
    body = Capture.model_validate(payload(env))
    key = str(uuid4())
    with api_process("capture-lost-response.log") as (api, _):

        async def scenario():
            async with httpx.AsyncClient(
                transport=lose_first_response_transport,
                base_url=str(api.base_url),
                headers=env.headers(),
            ) as http:
                native = NativeHTTPClient(http)
                with pytest.raises(AdapterFailure) as error:
                    await native.request(
                        "/v1/captures",
                        body,
                        TypeAdapter(CaptureResult),
                        expected_status=201,
                        key=key,
                        mutation=True,
                    )
                assert error.value.error.outcome_unknown is True
                assert lose_first_response_transport.calls == 1
                result = await native.request(
                    "/v1/captures",
                    body,
                    TypeAdapter(CaptureResult),
                    expected_status=201,
                    key=key,
                    mutation=True,
                )
                assert (
                    result.model_dump(mode="json")
                    == lose_first_response_transport.committed_response
                )
                assert lose_first_response_transport.calls == 2

        asyncio.run(scenario())
    assert counts(env)["memory.episode"] == counts(env)["memory_ops.job"] == 1
    assert counts(env)["memory.assertion"] == 0
    assert process(env)["outcome"] == "succeeded"
    assert counts(env)["memory.assertion"] == 1


def test_capture_replay_returns_failed_original_not_explicit_retry_child(env):
    body, headers = payload(env), env.headers()
    pair = capture(env, body, headers).json()
    lease = claim(env)

    async def fail():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            return await jobs.fail(
                lease["job_id"], lease["lease_token"], "invalid_input", retry=False
            )

    assert asyncio.run(fail()) == "failed"
    assert capture(env, body, headers).json() == capture(env, body).json() == pair
    assert job(env, pair).json()["state"] == "failed"
    intent = Capture.model_validate(body).memory.remember(env.scopes[0], UUID(pair["memory_id"]))
    child = env.client.post(
        "/v1/jobs/" + pair["synthesis_job_id"] + "/retry",
        json={"kind": "structured_remember", "memory": intent.model_dump(mode="json")},
        headers=env.headers(),
    )
    assert child.status_code == 202
    assert child.json()["job_id"] != pair["synthesis_job_id"]
    assert capture(env, body, headers).json() == pair
    assert process(env)["job_id"] == child.json()["job_id"]
    assert counts(env)["memory.assertion"] == 1
