import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager

import psycopg
import pytest
from test_processing import configure, enqueue, forget, get
from test_processing import profile as profile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name != "posix", reason="These worker crash tests require SIGKILL"),
]

LEASE_SECONDS = 2
CHILD_TIMEOUT_SECONDS = 8
SOURCE = "Alice / preferred_editor: Vim"

CHILD = """
import asyncio
import json
import os

from pg_agmemory.database import validate_runtime
from pg_agmemory.jobs import Jobs
from pg_agmemory.providers import ExtractionCandidate, ExtractionResult
from pg_agmemory.worker import run_once
from pg_agmemory.worker_profile import WorkerProfile

stage = os.environ["WORKER_STAGE"]
profile = WorkerProfile.parse(os.environ["WORKER_PROFILE"].encode())
assert profile.digest == os.environ["WORKER_PROFILE_DIGEST"]
original_claim = Jobs.claim

async def short_claim(self, lease_seconds=30, **kwargs):
    return await original_claim(
        self, lease_seconds=int(os.environ["WORKER_LEASE_SECONDS"]), **kwargs
    )

Jobs.claim = short_claim

def record(event):
    with open(os.environ["WORKER_EVENTS"], "a", encoding="utf-8") as stream:
        stream.write(event + "\\n")
        stream.flush()
        os.fsync(stream.fileno())

async def extract(data):
    assert data.text == "Alice / preferred_editor: Vim"
    record("provider_call")
    result = ExtractionResult(
        model=profile.settings.text_model,
        input_digest=data.digest(),
        candidates=[ExtractionCandidate(
            subject="Alice",
            predicate="preferred_editor",
            value="Vim",
            evidence_quote=data.text,
            start=0,
            end=len(data.text),
        )],
    )
    record("provider_response")
    return result

profile.provider.extract = extract
original_call = profile.call

async def gate():
    print(json.dumps({"gate": stage}), flush=True)
    await asyncio.Event().wait()

async def intercepted_call(kind, text):
    if stage == "reservation":
        await gate()
    result = await original_call(kind, text)
    if stage == "response":
        await gate()
    return result

profile.call = intercepted_call

async def main():
    url = os.environ["PGAG_DATABASE_URL"]
    await validate_runtime(url)
    result = await run_once(url, os.environ["WORKER_SUBJECT"], profile=profile)
    if stage == "publication":
        assert result["outcome"] == "succeeded"
        await gate()
    print(json.dumps({
        "outcome": result["outcome"],
        "job_id": result.get("job_id"),
    }), flush=True)

asyncio.run(main())
"""


@pytest.fixture
def call_events(tmp_path):
    # Only event names are journaled; no episode text, configuration, or credentials.
    path = tmp_path / "worker.events"
    path.touch(exist_ok=False)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def child_environment(env, profile, events, stage):
    inherited = (
        "PATH", "PYTHONPATH", "PYTHONHOME", "LANG", "LC_ALL", "SYSTEMROOT",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "VIRTUAL_ENV",
    )
    settings = {name: os.environ[name] for name in inherited if name in os.environ}
    settings.update(
        PGAG_DATABASE_URL=env.settings.database_url,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        WORKER_SUBJECT=env.subjects[0],
        WORKER_STAGE=stage,
        WORKER_PROFILE=profile.settings.model_dump_json(),
        WORKER_PROFILE_DIGEST=profile.digest,
        WORKER_LEASE_SECONDS=str(LEASE_SECONDS),
        WORKER_EVENTS=str(events),
    )
    return settings


@contextmanager
def child_worker(settings):
    worker = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        env=settings,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield worker
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=3)
        worker.stdout.close()


def reach_gate(worker, stage):
    readable, _, _ = select.select([worker.stdout], [], [], CHILD_TIMEOUT_SECONDS)
    assert readable, "Worker did not reach the requested crash boundary"
    line = worker.stdout.readline()
    if line != json.dumps({"gate": stage}) + "\n":
        pytest.fail("Worker exited or delivered a receipt before its crash boundary")
    assert worker.poll() is None, "Worker must still be alive at the crash boundary"


def kill_worker(worker):
    worker.kill()
    assert worker.wait(timeout=3) == -signal.SIGKILL
    assert worker.stdout.read() == "", "Killed worker must not have delivered a receipt"


def restart(env, profile, events):
    with child_worker(child_environment(env, profile, events, "restart")) as worker:
        output, _ = worker.communicate(timeout=CHILD_TIMEOUT_SECONDS)
        assert worker.returncode == 0, "Restarted worker failed before producing its receipt"
    return json.loads(output)


def admin_connection(env):
    return psycopg.connect(
        env.admin_url,
        autocommit=True,
        connect_timeout=3,
        options="-c statement_timeout=3000 -c lock_timeout=3000",
    )


def wait_for_real_expiry(env, job_id, *, timeout_seconds=LEASE_SECONDS + 1):
    deadline = time.monotonic() + timeout_seconds
    with admin_connection(env) as conn:
        while time.monotonic() < deadline:
            row = conn.execute(
                """SELECT lease_until<=clock_timestamp() FROM memory_ops.job
                   WHERE tenant_id=%s AND id=%s""",
                (env.tenants[0], job_id),
            ).fetchone()
            if row == (True,):
                return
            time.sleep(0.05)
    pytest.fail("The short real worker lease did not expire within its bound")


def counts(env, job_id):
    with admin_connection(env) as conn:
        row = conn.execute(
            """SELECT
               (SELECT count(*) FROM memory.episode WHERE tenant_id=%(tenant)s),
               (SELECT count(*) FROM memory_ops.job WHERE tenant_id=%(tenant)s),
               (SELECT count(*) FROM memory.assertion WHERE tenant_id=%(tenant)s),
               (SELECT count(*) FROM memory.assertion_revision WHERE tenant_id=%(tenant)s),
               (SELECT count(*) FROM memory.object o WHERE tenant_id=%(tenant)s
                  AND kind='assertion' AND NOT EXISTS (
                      SELECT 1 FROM memory_ops.object_tombstone t
                      WHERE t.tenant_id=o.tenant_id AND t.object_id=o.id)),
               (SELECT count(*) FROM memory_ops.extraction_candidate
                  WHERE tenant_id=%(tenant)s AND job_id=%(job)s),
               (SELECT count(*) FROM memory.assertion_derivation
                  WHERE tenant_id=%(tenant)s AND job_id=%(job)s),
               (SELECT count(*) FROM memory_ops.model_call
                  WHERE tenant_id=%(tenant)s AND scope_id=%(scope)s AND policy_epoch=2),
               (SELECT count(*) FROM memory_ops.audit_event
                  WHERE tenant_id=%(tenant)s AND target_id=%(job)s AND action='job_succeeded')""",
            {"tenant": env.tenants[0], "scope": env.scopes[0], "job": job_id},
        ).fetchone()
    return dict(zip(
        ("episodes", "jobs", "assertions", "revisions", "live_assertion_nodes", "candidates",
         "derivations", "reservations", "publications"),
        row,
        strict=True,
    ))


def assert_artifacts(env, job_id, *, published, purged=False):
    expected_nodes = int(published and not purged)
    assert counts(env, job_id) == {
        "episodes": int(not purged),
        "jobs": int(not purged),
        "assertions": expected_nodes,
        "revisions": expected_nodes,
        "live_assertion_nodes": expected_nodes,
        "candidates": expected_nodes,
        "derivations": expected_nodes,
        "reservations": 1,
        "publications": int(published),
    }


def assert_retained_reservation(env, job_id, *, published):
    with admin_connection(env) as conn:
        row = conn.execute(
            """SELECT outcome,billing_unknown,input_digest FROM memory_ops.model_call
               WHERE tenant_id=%s AND job_id=%s""",
            (env.tenants[0], job_id),
        ).fetchone()
    assert row[:2] == (("succeeded", False) if published else ("unknown", True))
    assert len(row[2]) == 64 and SOURCE not in row[2]


def job_detail(env, job_id):
    response = get(env, job_id)
    assert response.status_code == 200, "Job must remain readable before source purge"
    return response.json()


@pytest.mark.parametrize("stage", ["reservation", "response", "publication"])
def test_sigkill_model_worker_at_durable_boundaries(env, profile, call_events, stage):
    configure(env, profile, max_calls=1)
    source = env.observe(SOURCE).json()["memory_id"]
    queued = enqueue(env, source)
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]
    published = stage == "publication"
    expected_events = [] if stage == "reservation" else ["provider_call", "provider_response"]

    with child_worker(child_environment(env, profile, call_events, stage)) as worker:
        reach_gate(worker, stage)
        before = job_detail(env, job_id)
        assert before["state"] == ("succeeded" if published else "running")
        assert before["attempt"] == 1
        assert before["call"]["billing_unknown"] is not published
        assert call_events.read_text().splitlines() == expected_events
        assert_artifacts(env, job_id, published=published)
        kill_worker(worker)

    if not published:
        wait_for_real_expiry(env, job_id)
    recovered = restart(env, profile, call_events)
    assert recovered["outcome"] == ("idle" if published else "failed")
    assert recovered["job_id"] == (None if published else job_id)
    detail = job_detail(env, job_id)
    assert detail["state"] == ("succeeded" if published else "failed")
    assert detail["attempt"] == 1
    assert detail["error_code"] == (None if published else "billing_unknown")
    assert call_events.read_text().splitlines() == expected_events
    assert_artifacts(env, job_id, published=published)
    assert_retained_reservation(env, job_id, published=published)

    duplicate = enqueue(env, source)
    assert duplicate.status_code == 202 and duplicate.json()["job_id"] == job_id
    retry = enqueue(env, source, retry_of=job_id)
    assert retry.status_code == 409
    assert retry.json()["code"] == ("job_retry_conflict" if published else "job_retry_unknown")

    assert forget(env, source).status_code == 202
    assert get(env, job_id).status_code == 404
    assert env.client.get(
        f"/v1/jobs/{job_id}/candidates", headers=env.headers(),
    ).status_code == 404
    assert enqueue(env, source).status_code == 404
    assert restart(env, profile, call_events)["outcome"] == "idle"
    assert call_events.read_text().splitlines() == expected_events
    assert_artifacts(env, job_id, published=published, purged=True)
    assert_retained_reservation(env, job_id, published=published)


def test_source_purge_after_sigkill_before_recovery_keeps_quota_without_resurrection(
    env, profile, call_events,
):
    configure(env, profile, max_calls=1)
    source = env.observe(SOURCE).json()["memory_id"]
    queued = enqueue(env, source)
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]

    with child_worker(child_environment(env, profile, call_events, "response")) as worker:
        reach_gate(worker, "response")
        assert job_detail(env, job_id)["state"] == "running"
        kill_worker(worker)

    assert forget(env, source).status_code == 202
    assert get(env, job_id).status_code == 404
    assert enqueue(env, source, retry_of=job_id).status_code == 404
    assert restart(env, profile, call_events)["outcome"] == "idle"
    assert call_events.read_text().splitlines() == ["provider_call", "provider_response"]
    assert_artifacts(env, job_id, published=False, purged=True)
    assert_retained_reservation(env, job_id, published=False)
