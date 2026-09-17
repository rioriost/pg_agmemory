import asyncio
import json
import os
import select
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from pg_agmemory.jobs import Jobs, job_transaction
from pg_agmemory.models import Remember
from pg_agmemory.service import MemoryError, MemoryService
from pg_agmemory.worker import run_once

pytestmark = pytest.mark.integration


def payload(env, index=0, **overrides):
    source = env.observe("Gold", index=index).json()["memory_id"]
    return {
        "kind": "structured_remember",
        "memory": {
            "scope_id": str(env.scopes[index]),
            "subject": "ACME",
            "predicate": "contract_tier",
            "value": "Gold",
            "explicit_intent": True,
            "evidence": [{"memory_id": source, "quote": "Gold"}],
            **overrides,
        },
    }


def enqueue(env, body=None, index=0, headers=None):
    body = body or payload(env, index)
    response = env.client.post("/v1/jobs", json=body, headers=headers or env.headers(index))
    assert response.status_code == 202, response.text
    return response.json()


def get(env, job, index=0):
    return env.client.get("/v1/jobs/" + job["job_id"], headers=env.headers(index))


def call(env, method, *args, index=0, **kwargs):
    async def execute():
        async with job_transaction(env.settings.database_url, env.subjects[index]) as jobs:
            return await getattr(jobs, method)(*args, **kwargs)

    return asyncio.run(execute())


def publish(env, lease):
    return call(
        env,
        "publish",
        lease["job_id"],
        lease["lease_token"],
        Remember.model_validate(lease["payload"]),
    )


def process(env, index=0):
    return asyncio.run(run_once(env.settings.database_url, env.subjects[index]))


def assertions(env):
    return [item for item in env.recall().json()["items"] if item["type"] == "assertion"]


def test_enqueue_publication_status_and_legacy_sync_behavior(env):
    body = payload(env)
    other = env.observe("Silver").json()["memory_id"]
    body["memory"]["evidence"].append({"memory_id": other, "quote": "Silver"})
    headers = env.headers()
    job = enqueue(env, body, headers=headers)
    assert set(job) == {"job_id", "kind", "recipe_version"}
    assert enqueue(env, body, headers=headers) == enqueue(env, body) == job
    reordered = {
        **body,
        "memory": {
            **body["memory"],
            "evidence": list(reversed(body["memory"]["evidence"])),
        },
    }
    assert enqueue(env, reordered) == job
    assert env.client.post("/v1/jobs", json=reordered, headers=headers).status_code == 409
    pending = get(env, job)
    assert pending.json()["state"] == "pending" and pending.json()["attempt"] == 0
    for private in ("payload", "lease_token", "principal_id", "intent_digest"):
        assert private not in pending.json()
    assert assertions(env) == []
    assert env.recall().json()["coverage"]["jobs_pending"] is True
    assert env.recall().json()["coverage"]["synthesis_pending"] is False
    processed = process(env)
    assert processed["outcome"] == "succeeded"
    current = get(env, job).json()
    assert current["state"] == "succeeded" and current["attempt"] == 1
    assert current["result"] == {
        "memory_id": processed["result"]["memory_id"],
        "revision": 1,
    }
    assert assertions(env)[0]["content"] == "ACME / contract_tier: Gold"
    assert env.recall().json()["coverage"]["jobs_pending"] is False
    assert process(env) == {"outcome": "idle"}
    assert enqueue(env, body) == job
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT payload FROM memory_ops.job WHERE id = %s", (job["job_id"],)
            ).fetchone()[0]
            is None
        )
    observed = env.observe()
    assert observed.json()["synthesis_job_id"] is None
    assert env.remember(observed.json()["memory_id"]).status_code == 201


def test_concurrent_enqueue_claim_and_publication_have_one_result(env):
    body, headers = payload(env), env.headers()
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda _: enqueue(env, body, headers=headers), range(6)))
    assert len({job["job_id"] for job in jobs}) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: process(env), range(4)))
    assert sum(outcome["outcome"] == "succeeded" for outcome in outcomes) == 1
    assert all(outcome["outcome"] in ("idle", "succeeded") for outcome in outcomes)
    assert len(assertions(env)) == 1


def test_skip_locked_claim_does_not_wait_for_locked_job(env):
    first, second = enqueue(env), enqueue(env)
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SELECT id FROM memory_ops.job WHERE id = %s FOR UPDATE", (first["job_id"],))
        claim = call(env, "claim")
        assert str(claim["job_id"]) == second["job_id"]
    assert str(call(env, "claim")["job_id"]) == first["job_id"]


def test_heartbeat_takeover_and_stale_publisher_fencing(env):
    first = enqueue(env)
    lease = call(env, "claim")
    before = get(env, first).json()["lease_until"]
    call(env, "heartbeat", lease["job_id"], lease["lease_token"])
    assert get(env, first).json()["lease_until"] > before
    with pytest.raises(MemoryError, match="job_lease_conflict"):
        call(env, "heartbeat", lease["job_id"], uuid4())
    second = enqueue(env)
    stale = call(env, "claim", lease_seconds=1)
    time.sleep(1.1)
    current = call(env, "claim")
    assert str(current["job_id"]) == second["job_id"] and current["attempt"] == 2
    assert current["lease_token"] != stale["lease_token"]
    with pytest.raises(MemoryError, match="job_lease_conflict"):
        publish(env, stale)
    result = publish(env, current)
    assert get(env, second).json()["result"]["memory_id"] == result["memory_id"]
    with pytest.raises(MemoryError, match="job_lease_conflict"):
        publish(env, current)
    assert len(assertions(env)) == 1


@pytest.mark.parametrize("epoch", ["access_epoch", "deletion_epoch"])
def test_epoch_changes_retry_with_backoff_and_refresh_at_claim(env, epoch):
    job = enqueue(env)
    lease = call(env, "claim")
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            psycopg.sql.SQL("UPDATE memory.tenant SET {} = {} + 1 WHERE id = %s").format(
                psycopg.sql.Identifier(epoch), psycopg.sql.Identifier(epoch)
            ),
            (env.tenants[0],),
        )
    with pytest.raises(MemoryError, match="stale_context"):
        publish(env, lease)
    with psycopg.connect(env.admin_url) as conn:
        before = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    state = call(env, "fail", lease["job_id"], lease["lease_token"], "stale_context", retry=True)
    assert state == "pending" and assertions(env) == []
    with psycopg.connect(env.admin_url) as conn:
        after = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    available = datetime.fromisoformat(get(env, job).json()["available_at"])
    assert before + timedelta(seconds=2) <= available <= after + timedelta(seconds=3)
    assert call(env, "claim") is None
    time.sleep(max(0, (available - after).total_seconds()) + 0.1)
    current = call(env, "claim")
    assert current["attempt"] == 2
    publish(env, current)
    assert get(env, job).json()["state"] == "succeeded"


def test_current_permissions_and_fixed_principal_worker_boundary(env):
    body = payload(env)
    job = enqueue(env, body)
    lease = call(env, "claim", lease_seconds=1)
    for index in (1, 2):
        assert get(env, job, index).status_code == 404
        assert call(env, "claim", index=index) is None
        assert env.client.post("/v1/jobs", json=body, headers=env.headers(index)).status_code == 404
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory.scope_member SET permissions = ARRAY['read'] WHERE scope_id = %s",
            (env.scopes[0],),
        )
        conn.execute(
            "UPDATE memory.tenant SET access_epoch = access_epoch + 1 WHERE id = %s",
            (env.tenants[0],),
        )
    with pytest.raises(MemoryError, match="not_found"):
        publish(env, lease)
    assert call(env, "claim") is None
    assert get(env, job).json()["state"] == "running"
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET permissions = ARRAY['read','write','delete']
               WHERE scope_id = %s""",
            (env.scopes[0],),
        )
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write'])""",
            (env.tenants[0], env.scopes[0], env.principals[2]),
        )
        conn.execute(
            "UPDATE memory.tenant SET access_epoch = access_epoch + 1 WHERE id = %s",
            (env.tenants[0],),
        )
    assert get(env, job, 2).status_code == 200
    assert call(env, "claim", index=2) is None
    with pytest.raises(MemoryError):
        call(
            env,
            "publish",
            lease["job_id"],
            lease["lease_token"],
            Remember.model_validate(lease["payload"]),
            index=2,
        )
    time.sleep(1.1)
    publish(env, call(env, "claim"))


@pytest.mark.parametrize("state", ["pending", "running", "succeeded"])
def test_source_purge_erases_job_and_fences_replay_and_publication(env, state):
    body, headers = payload(env), env.headers()
    job = enqueue(env, body, headers=headers)
    lease = None
    if state != "pending":
        lease = call(env, "claim")
    if state == "succeeded":
        publish(env, lease)
    source = body["memory"]["evidence"][0]["memory_id"]
    forgotten = env.client.post(
        "/v1/forget",
        json={"memory_ids": [source], "reason": "test"},
        headers=env.headers(),
    )
    assert forgotten.status_code == 202, forgotten.text
    assert forgotten.json()["object_count"] == (3 if state == "succeeded" else 2)
    assert get(env, job).status_code == 404
    for key in (headers, env.headers()):
        assert env.client.post("/v1/jobs", json=body, headers=key).status_code == 404
    if lease:
        with pytest.raises(MemoryError, match="not_found"):
            publish(env, lease)
    assert assertions(env) == []
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_ops.job_input WHERE job_id = %s", (job["job_id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_ops.job_identity WHERE job_id = %s", (job["job_id"],)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("erase_result", [False, True])
def test_control_record_and_output_have_explicit_deletion_semantics(env, erase_result):
    job = enqueue(env)
    result = process(env)["result"]["memory_id"]
    root = result if erase_result else job["job_id"]
    removed = env.client.post(
        "/v1/forget",
        json={"memory_ids": [root], "reason": "test"},
        headers=env.headers(),
    )
    assert removed.status_code == 202
    assert removed.json()["object_count"] == (2 if erase_result else 1)
    assert get(env, job).status_code == 404
    explanation = env.client.post("/v1/explain", json={"memory_id": result}, headers=env.headers())
    assert explanation.status_code == (404 if erase_result else 200)


def test_fifth_expired_attempt_is_terminal_and_explicit_retry_is_deduplicated(env):
    body = payload(env)
    job = enqueue(env, body)
    for attempt in range(1, 6):
        claim = call(env, "claim", lease_seconds=1)
        assert claim["attempt"] == attempt
        time.sleep(1.1)
    assert call(env, "claim")["state"] == "failed"
    failed = get(env, job).json()
    assert failed["attempt"] == 5 and failed["error_code"] == "attempt_limit"
    assert call(env, "claim") is None
    assert enqueue(env, body) == job
    url = "/v1/jobs/" + job["job_id"] + "/retry"
    key = env.headers()
    retry = env.client.post(url, json=body, headers=key)
    assert retry.status_code == 202, retry.text
    child = retry.json()
    assert child["job_id"] != job["job_id"]
    assert env.client.post(url, json=body, headers=key).json() == child
    assert env.client.post(url, json=body, headers=env.headers()).json() == child
    assert get(env, child).json()["retry_of"] == job["job_id"]
    changed = {**body, "memory": {**body["memory"], "value": "Changed"}}
    assert env.client.post(url, json=changed, headers=env.headers()).status_code == 409
    assert (
        env.client.post(
            "/v1/jobs/" + child["job_id"] + "/retry", json=body, headers=env.headers()
        ).status_code
        == 409
    )
    result = process(env)["result"]["memory_id"]
    removed = env.client.post(
        "/v1/forget",
        json={"memory_ids": [job["job_id"]], "reason": "test"},
        headers=env.headers(),
    )
    assert removed.status_code == 202 and removed.json()["object_count"] == 2
    assert get(env, child).status_code == 404
    assert (
        env.client.post(
            "/v1/explain", json={"memory_id": result}, headers=env.headers()
        ).status_code
        == 200
    )


def test_failed_enqueue_and_publish_roll_back_every_write(env, monkeypatch):
    body, key = payload(env), env.headers()
    audit = MemoryService.audit

    async def fail(self, action, target):
        if action in ("enqueue_job", "job_succeeded"):
            raise MemoryError("publication_failed", 503)
        await audit(self, action, target)

    monkeypatch.setattr(MemoryService, "audit", fail)
    assert env.client.post("/v1/jobs", json=body, headers=key).status_code == 503
    monkeypatch.setattr(MemoryService, "audit", audit)
    job = enqueue(env, body, headers=key)
    lease = call(env, "claim")
    monkeypatch.setattr(MemoryService, "audit", fail)
    with pytest.raises(MemoryError, match="publication_failed"):
        publish(env, lease)
    assert get(env, job).json()["state"] == "running" and assertions(env) == []
    monkeypatch.setattr(MemoryService, "audit", audit)
    publish(env, lease)
    assert len(assertions(env)) == 1


def test_expiring_during_publication_rolls_back_the_assertion(env, monkeypatch):
    job = enqueue(env)
    lease = call(env, "claim", lease_seconds=2)
    original = MemoryService.publish_assertion
    wrote_assertion = False

    async def slow(self, data):
        nonlocal wrote_assertion
        result = await original(self, data)
        wrote_assertion = True
        await asyncio.sleep(2.1)
        return result

    monkeypatch.setattr(MemoryService, "publish_assertion", slow)
    with pytest.raises(MemoryError, match="job_lease_conflict"):
        publish(env, lease)
    assert wrote_assertion
    assert get(env, job).json()["result"] is None and assertions(env) == []
    monkeypatch.setattr(MemoryService, "publish_assertion", original)
    publish(env, call(env, "claim"))


def test_exact_scope_queue_limit_releases_capacity_only_on_completion(env):
    body = payload(env)
    first = None
    for index in range(100):
        current = {**body, "memory": {**body["memory"], "value": f"Gold {index}"}}
        job = enqueue(env, current)
        first = first or (current, job)
    overflow = {**body, "memory": {**body["memory"], "value": "overflow"}}
    response = env.client.post("/v1/jobs", json=overflow, headers=env.headers())
    assert response.status_code == 422 and response.json()["code"] == "job_limit_exceeded"
    assert enqueue(env, first[0]) == first[1]
    process(env)
    assert enqueue(env, overflow)


def test_invalid_inputs_and_database_guards(env):
    body = payload(env)
    for invalid in [
        {**body, "kind": "arbitrary"},
        {**body, "lease_token": str(uuid4())},
        {**body, "memory": {**body["memory"], "evidence": []}},
        {**body, "memory": {**body["memory"], "explicit_intent": False}},
    ]:
        assert env.client.post("/v1/jobs", json=invalid, headers=env.headers()).status_code == 422
    job = enqueue(env, body)

    async def guards():
        for statement, error in [
            (
                """UPDATE memory_ops.job SET payload = jsonb_set(payload,'{value}','"forged"')
                   WHERE id = %s""",
                psycopg.errors.CheckViolation,
            ),
            (
                "UPDATE memory_ops.job SET principal_id = principal_id WHERE id = %s",
                psycopg.errors.InsufficientPrivilege,
            ),
            ("DELETE FROM memory_ops.job_input WHERE job_id = %s", psycopg.errors.CheckViolation),
        ]:
            with pytest.raises(error):
                async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
                    await jobs.conn.execute(statement, (job["job_id"],))
                    await jobs.conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    asyncio.run(guards())
    lease = call(env, "claim")
    changed = Remember.model_validate({**lease["payload"], "value": "forged"})
    with pytest.raises(MemoryError, match="job_intent_conflict"):
        call(env, "publish", lease["job_id"], lease["lease_token"], changed)
    publish(env, lease)


@pytest.mark.parametrize("stage", ["claim", "publication"])
def test_actual_worker_crash_after_commit_recovers_with_cli(env, stage):
    job = enqueue(env)
    settings = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PGAG_TEST_DATABASE_URL", "PGAG_ADMIN_DATABASE_URL")
    }
    settings.update(
        PGAG_DATABASE_URL=env.settings.database_url,
        WORKER_SUBJECT=env.subjects[0],
        WORKER_STAGE=stage,
    )
    script = """
import asyncio,json,os,sys
from pg_agmemory.database import validate_runtime
from pg_agmemory.jobs import job_transaction
from pg_agmemory.worker import run_once
async def claim():
    url = os.environ['PGAG_DATABASE_URL']
    await validate_runtime(url)
    if os.environ['WORKER_STAGE'] == 'claim':
        async with job_transaction(url, os.environ['WORKER_SUBJECT']) as jobs:
            result = await jobs.claim(lease_seconds=1)
    else:
        result = await run_once(url, os.environ['WORKER_SUBJECT'])
    print(json.dumps({'job_id':str(result['job_id'])}), flush=True)
asyncio.run(claim())
sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", script],
        env=settings,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as worker:
        try:
            readable, _, _ = select.select([worker.stdout], [], [], 10)
            assert readable, "Worker did not commit its claim"
            line = worker.stdout.readline()
            assert line, worker.stderr.read()
            assert json.loads(line)["job_id"] == job["job_id"]
            worker.kill()
            worker.wait(timeout=10)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
    if stage == "claim":
        time.sleep(1.1)
    command = [
        sys.executable,
        "-m",
        "pg_agmemory.cli",
        "worker",
        "--subject",
        env.subjects[0],
        "--once",
    ]
    restarted = subprocess.run(command, env=settings, capture_output=True, text=True, timeout=30)
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["outcome"] == ("succeeded" if stage == "claim" else "idle")
    assert get(env, job).json()["attempt"] == (2 if stage == "claim" else 1)
    assert len(assertions(env)) == 1
    again = subprocess.run(command, env=settings, capture_output=True, text=True, timeout=30)
    assert again.returncode == 0 and json.loads(again.stdout) == {"outcome": "idle"}
    privileged = subprocess.run(
        command,
        env={**settings, "PGAG_DATABASE_URL": env.admin_url},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert privileged.returncode != 0 and "must not own tables or bypass RLS" in privileged.stderr


@pytest.mark.parametrize("failure", ["transport", "invalid"])
def test_worker_failures_are_safe_and_explicit(env, monkeypatch, caplog, failure):
    job = enqueue(env)
    original = Jobs.claim

    async def disconnected(self, *args):
        raise psycopg.OperationalError("DO_NOT_ECHO")

    async def invalid(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        result["payload"] = {"invalid": "DO_NOT_ECHO"}
        return result

    if failure == "transport":
        monkeypatch.setattr(Jobs, "publish", disconnected)
    else:
        monkeypatch.setattr(Jobs, "claim", invalid)
    outcome = process(env)
    expected = "pending" if failure == "transport" else "failed"
    assert outcome["outcome"] == expected
    detail = get(env, job).json()
    assert detail["state"] == expected and detail["result"] is None
    assert detail["error_code"] == (
        "dependency_unavailable" if failure == "transport" else "invalid_input"
    )
    assert "DO_NOT_ECHO" not in caplog.text
    assert "job_attempt_failed" in caplog.text


def test_forget_and_publication_race_cannot_resurrect_data(env):
    body = payload(env)
    job = enqueue(env, body)
    lease = call(env, "claim")
    source = body["memory"]["evidence"][0]["memory_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        publishing = pool.submit(publish, env, lease)
        deletion = pool.submit(
            env.client.post,
            "/v1/forget",
            json={"memory_ids": [source], "reason": "test"},
            headers=env.headers(),
        )
        receipt = deletion.result()
        try:
            result = publishing.result()
        except MemoryError as exc:
            assert exc.code == "not_found"
            result = None
    assert receipt.status_code == 202, receipt.text
    assert receipt.json()["object_count"] == (3 if result else 2)
    assert get(env, job).status_code == 404 and assertions(env) == []


def test_schema_six_preserves_v5_graph_and_publishes_job_contracts(env, database):
    record = database[2][0]
    headers = {"Authorization": "Bearer " + env.token(sub=record["subject"])}
    graph = record["graph"]
    response = env.client.post(
        "/v1/graph/expand",
        headers=headers,
        json={
            "scope_ids": [str(record["scope"])],
            "seeds": [str(graph["source"])],
            "relation_types": ["depends_on"],
            "purpose": "migration",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["paths"] == [
        {
            "nodes": [str(graph["source"]), str(graph["target"])],
            "assertions": [{"memory_id": str(graph["relation"]), "revision": 1}],
        }
    ]
    schema = env.client.get("/openapi.json").json()
    for path, verb, status in [
        ("/v1/jobs", "post", "202"),
        ("/v1/jobs/{job_id}", "get", "200"),
        ("/v1/jobs/{job_id}/retry", "post", "202"),
    ]:
        operation = schema["paths"][path][verb]
        assert operation["security"] == [{"BearerAuth": []}]
        assert "$ref" in operation["responses"][status]["content"]["application/json"]["schema"]
