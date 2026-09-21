"""Bounded resource/failure probes against an isolated restored synthetic S database."""

import argparse
import asyncio
import importlib.util
import json
import os
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from pg_agmemory.api import create_app
from pg_agmemory.database import SCHEMA_VERSION, Settings, migrate
from pg_agmemory.lexical import JAPANESE_PROFILE, segment
from pg_agmemory.models import RecallResult
from pg_agmemory.synthesis_policy import SynthesisPolicy, SynthesisPolicyRequest, synthesis_policy
from pg_agmemory.worker import run_once
from pg_agmemory.worker_profile import WorkerProfile

SPEC = importlib.util.spec_from_file_location(
    "resource_benchmark", Path(__file__).with_name("resource-benchmark.py")
)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)
FORGET_STATUS = 202


def read(directory, name):
    return json.loads((directory / name).read_text())


def admin():
    return psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"], row_factory=dict_row)


def prepare(directory, plan_file):
    plan = json.loads(plan_file.read_text())
    bench.require(plan["format"] == "pgag-resource-probes-v1", "unknown probe plan")
    with admin() as conn:
        version = conn.execute("SELECT max(version) v FROM pgag_schema_migration").fetchone()["v"]
        bench.require(version in (16, SCHEMA_VERSION), "restored schema mismatch")
        counts = conn.execute("SELECT count(*) n FROM memory.episode").fetchone()["n"]
        bench.require(counts >= 100000, "full S source snapshot required")
    migrate(os.environ["PGAG_ADMIN_DATABASE_URL"])
    bench.write_json(
        directory / "migration.json", {"restored_schema": version, "probe_schema": SCHEMA_VERSION}
    )
    with admin() as conn:
        password = secrets.token_urlsafe(32)
        conn.execute(
            sql.SQL("CREATE ROLE pgag_probe_runtime LOGIN PASSWORD {} IN ROLE pgag_runtime").format(
                sql.Literal(password),
            )
        )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    runtime = make_conninfo(
        **(
            conninfo_to_dict(os.environ["PGAG_ADMIN_DATABASE_URL"])
            | {
                "user": "pgag_probe_runtime",
                "password": password,
            }
        )
    )
    identities = []
    for index in range(10):
        now = int(time.time())
        subject = f"resource-subject-{index}"
        identities.append(
            {
                "tenant_id": str(bench.uid(f"tenant/{index}")),
                "subject": subject,
                "scopes": [str(bench.uid(f"scope/{index}/{i}")) for i in range(6)],
                "token": jwt.encode(
                    {
                        "sub": subject,
                        "iat": now,
                        "exp": now + 21600,
                        "iss": bench.ISSUER,
                        "aud": bench.AUDIENCE,
                    },
                    private,
                    algorithm="RS256",
                ),
            }
        )
    profile = bench.profile(
        Path(__file__).resolve().parents[1] / "examples/resource-profile-s.json"
    )
    profile = profile | {
        "name": "S-probes-v1",
        "event_prefix": "resource-probe-background-",
        "warmup_seconds": 0,
        "steady_seconds": plan["mixed_load_seconds"],
    }
    bench.write_json(directory / "profile.json", profile)
    bench.write_json(directory / "plan.json", plan)
    bench.write_json(directory / "clients.json", identities)
    bench.write_json(
        directory / "runtime.json",
        {
            "database_url": runtime,
            "jwt_public_key": public,
            "jwt_issuer": bench.ISSUER,
            "jwt_audience": bench.AUDIENCE,
        },
    )
    (directory / "workers.pause").touch(mode=0o600)


def analyze(directory, phase):
    query = """SELECT schemaname,relname,n_live_tup,last_analyze::text,
        last_autoanalyze::text FROM pg_stat_user_tables
        WHERE schemaname IN ('memory','memory_ops') ORDER BY schemaname,relname"""
    with admin() as conn:
        before = conn.execute(query).fetchall()
        conn.execute("ANALYZE")
    with admin() as conn:
        after = conn.execute(query).fetchall()
    bench.require(bool(after) and all(r["last_analyze"] for r in after), "ANALYZE incomplete")
    bench.write_json(
        directory / f"statistics-{phase}.json",
        {"operation": "ANALYZE", "phase": phase, "before": before, "after": after},
    )


def rebind(directory):
    settings = read(directory, "runtime.json")
    current = conninfo_to_dict(settings["database_url"])
    current["host"] = conninfo_to_dict(os.environ["PGAG_ADMIN_DATABASE_URL"])["host"]
    settings["database_url"] = make_conninfo(**current)
    temporary = directory / "runtime-rebind.json"
    bench.write_json(temporary, settings)
    os.replace(temporary, directory / "runtime.json")


def configure(directory, *, output=128, calls=10000, queue=20, oversized=False):
    actor = read(directory, "clients.json")[0]
    profile = bench.worker_profile()
    if oversized:
        profile = WorkerProfile(profile.settings.model_copy(update={"max_output_tokens": output}))
    with admin() as conn:
        epoch = conn.execute(
            "SELECT access_epoch FROM memory.tenant WHERE id=%s", (actor["tenant_id"],)
        ).fetchone()["access_epoch"]
    with synthesis_policy(
        os.environ["PGAG_ADMIN_DATABASE_URL"],
        SynthesisPolicyRequest(
            operation="set",
            tenant_id=UUID(actor["tenant_id"]),
            scope_id=UUID(actor["scopes"][5]),
            expected_access_epoch=epoch,
            policy=SynthesisPolicy(
                enabled=True,
                profile_digest=profile.digest,
                kinds=["extract"],
                consent_references=[bench.CONSENT],
                max_pending_jobs=queue,
                max_input_bytes=1024,
                max_output_tokens=128,
                max_calls=calls,
            ),
        ),
    ) as result:
        return profile, result.policy_access_epoch


async def pause(directory, enabled):
    path = directory / "workers.pause"
    if enabled:
        path.touch(mode=0o600, exist_ok=True)
    else:
        path.unlink(missing_ok=True)
    for _ in range(500):
        states = [(directory / f"worker-{i}.paused").exists() for i in range(2)]
        if all(state == enabled for state in states):
            return
        await asyncio.sleep(0.02)
    raise bench.ResourceError("worker pause acknowledgement failed")


def headers(actor):
    return {"Authorization": "Bearer " + actor["token"], "Idempotency-Key": str(uuid4())}


def observe_body(actor, marker, *, auto=False, size=512, scope=None):
    return {
        "scope_id": scope or actor["scopes"][5],
        "source_namespace": "resource-probes",
        "source_event_id": marker + "-" + str(uuid4()),
        "occurred_at": bench.AT.isoformat(),
        "content": bench.text_for(0, size),
        "consent_reference": bench.CONSENT,
        "auto_extract": auto,
    }


async def observe(client, actor, marker, **kwargs):
    return await client.post(
        "/v1/observe", headers=headers(actor), json=observe_body(actor, marker, **kwargs)
    )


async def wait_jobs(client, actor, ids):
    for _ in range(1000):
        values = []
        for job in ids:
            result = await client.get(f"/v1/jobs/{job}", headers=headers(actor))
            bench.require(result.status_code == 200, "job status unavailable")
            values.append(result.json())
        if all(value["state"] in ("succeeded", "failed", "cancelled") for value in values):
            return values
        await asyncio.sleep(0.05)
    raise bench.ResourceError("jobs did not drain")


def counts(actor):
    with admin() as conn:
        return conn.execute(
            """SELECT
            (SELECT count(*) FROM memory.object WHERE scope_id=%s) objects,
            (SELECT count(*) FROM memory_ops.source_event WHERE scope_id=%s) events,
            (SELECT count(*) FROM memory.episode WHERE scope_id=%s) episodes,
            (SELECT count(*) FROM memory_ops.job WHERE scope_id=%s) jobs,
            (SELECT count(*) FROM memory_ops.model_call WHERE scope_id=%s) calls""",
            (actor["scopes"][5],) * 5,
        ).fetchone()


async def limits(directory, base):
    actors, plan = read(directory, "clients.json"), read(directory, "plan.json")
    actor = actors[0]
    results = {}
    async with httpx.AsyncClient(base_url=base, timeout=20, trust_env=False) as client:
        await pause(directory, True)
        configure(directory, queue=plan["queue_capacity"])
        before = counts(actor)
        too_large = await asyncio.gather(
            *[
                observe(client, actor, "input-limit", auto=True, size=plan["rejected_input_bytes"])
                for _ in range(plan["concurrent_requests"])
            ]
        )
        bench.require(
            all(
                r.status_code == 422 and r.json()["code"] == "processing_input_limit"
                for r in too_large
            ),
            "input limit failed",
        )
        bench.require(counts(actor) == before, "rejected input left durable state")
        results["input"] = {"rejected": len(too_large), "durable_changes": 0}
        body = observe_body(actor, "body-limit") | {"content": "x" * (256 * 1024 + 1)}
        rejected = await client.post("/v1/observe", headers=headers(actor), json=body)
        bench.require(rejected.status_code == 413 and counts(actor) == before, "body bound failed")
        results["body"] = {"status": 413, "durable_changes": 0}
        queued = await asyncio.gather(
            *[
                observe(client, actor, "queue-limit", auto=True)
                for _ in range(plan["concurrent_requests"])
            ]
        )
        admitted = [r.json()["synthesis_job_id"] for r in queued if r.status_code == 201]
        denied = [r for r in queued if r.status_code != 201]
        bench.require(
            len(admitted) == plan["queue_capacity"]
            and all(
                r.status_code == 422 and r.json()["code"] == "job_limit_exceeded" for r in denied
            ),
            "concurrent queue admission failed",
        )
        after = counts(actor)
        bench.require(
            after["episodes"] - before["episodes"] == len(admitted)
            and after["jobs"] - before["jobs"] == len(admitted)
            and after["objects"] - before["objects"] == 2 * len(admitted)
            and after["events"] - before["events"] == len(admitted)
            and after["calls"] == before["calls"],
            "queue rollback or egress failed",
        )
        results["queue"] = {
            "admitted": len(admitted),
            "rejected": len(denied),
            "calls_while_paused": 0,
        }
        await pause(directory, False)
        bench.require(
            all(j["state"] == "succeeded" for j in await wait_jobs(client, actor, admitted)),
            "admitted jobs failed",
        )
        await pause(directory, True)
        _, epoch = configure(directory, calls=plan["call_capacity"])
        requests = await asyncio.gather(
            *[observe(client, actor, "call-limit", auto=True) for _ in range(plan["call_jobs"])]
        )
        bench.require(all(r.status_code == 201 for r in requests), "call fixture enqueue failed")
        jobs = [r.json()["synthesis_job_id"] for r in requests]
        await pause(directory, False)
        completed = await wait_jobs(client, actor, jobs)
        await pause(directory, True)
        with admin() as conn:
            reserved = conn.execute(
                "SELECT count(*) n FROM memory_ops.model_call "
                "WHERE tenant_id=%s AND scope_id=%s AND policy_epoch=%s",
                (actor["tenant_id"], actor["scopes"][5], epoch),
            ).fetchone()["n"]
        bench.require(
            reserved == 1
            and sum(j["state"] == "succeeded" for j in completed) == 1
            and all(
                j["state"] == "succeeded" or j["error_code"] == "policy_denied" for j in completed
            ),
            "call budget was exceeded",
        )
        results["calls"] = {"jobs": len(jobs), "reservations": reserved, "denied": len(jobs) - 1}

        oversized, _ = configure(directory, output=plan["rejected_output_tokens"], oversized=True)
        created = await observe(client, actor, "output-limit", auto=True)
        bench.require(created.status_code == 201, "output fixture enqueue failed")
        job = created.json()["synthesis_job_id"]
        settings = Settings(**read(directory, "runtime.json"))

        async def forbidden(_):
            raise bench.ResourceError("output limit permitted provider egress")

        oversized.provider.extract = forbidden
        before = counts(actor)
        await run_once(settings.database_url, actor["subject"], profile=oversized)
        detail = (await wait_jobs(client, actor, [job]))[0]
        bench.require(
            detail["state"] == "failed"
            and detail["error_code"] == "provider_failed"
            and counts(actor)["calls"] == before["calls"],
            "output bound failed",
        )
        results["output"] = {"rejected_tokens": plan["rejected_output_tokens"], "reservations": 0}

        configure(directory, calls=2)
        (directory / "provider.malformed").touch(mode=0o600)
        created = await observe(client, actor, "malformed", auto=True)
        bench.require(created.status_code == 201, "malformed fixture enqueue failed")
        value = created.json()
        await pause(directory, False)
        detail = (await wait_jobs(client, actor, [value["synthesis_job_id"]]))[0]
        await pause(directory, True)
        (directory / "provider.malformed").unlink()
        bench.require(
            detail["state"] == "failed"
            and detail["error_code"] == "billing_unknown"
            and detail["call"]["billing_unknown"],
            "unknown reservation not preserved",
        )
        retry = await client.post(
            "/v1/processing",
            headers=headers(actor),
            json={
                "scope_id": actor["scopes"][5],
                "kind": "extract",
                "source": {"memory_id": value["memory_id"], "revision": 1},
                "retry_of": value["synthesis_job_id"],
            },
        )
        bench.require(
            retry.status_code == 409 and retry.json()["code"] == "job_retry_unknown",
            "unknown call was retryable",
        )
        results["unknown"] = {"state": "failed", "billing_unknown": True, "retry_status": 409}

        request = {
            "scope_ids": actor["scopes"][:4],
            "query": "topic000",
            "purpose": "resource-probe",
            "mode": "implicit",
            "token_budget": plan["context_budget_bytes"],
        }
        packed = await asyncio.gather(
            *[
                client.post("/v1/recall", headers=headers(actor), json=request)
                for _ in range(plan["concurrent_requests"])
            ]
        )
        for response in packed:
            bench.require(response.status_code == 200, "bounded context request failed")
            context = RecallResult.model_validate_json(response.content).context_pack
            bench.require(context.byte_count <= plan["context_budget_bytes"], "context overflow")
        invalid = await client.post(
            "/v1/recall",
            headers=headers(actor),
            json=request | {"token_budget": plan["rejected_implicit_budget"]},
        )
        bench.require(invalid.status_code == 422, "implicit budget overflow accepted")
        results["context"] = {"bounded_responses": len(packed), "invalid_status": 422}

        with admin() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (actor["tenant_id"],))
            started = time.perf_counter()
            waiting = await asyncio.gather(
                *[
                    client.post("/v1/recall", headers=headers(actor), json=request)
                    for _ in range(plan["database_waiters"])
                ]
            )
            elapsed = time.perf_counter() - started
            bench.require(
                all(
                    r.status_code == 503 and r.json()["code"] == "dependency_unavailable"
                    for r in waiting
                ),
                "database timeout not explicit",
            )
        recovered = await client.post("/v1/recall", headers=headers(actor), json=request)
        bench.require(recovered.status_code == 200, "database timeout recovery failed")
        results["database"] = {
            "waiters": len(waiting),
            "status": 503,
            "elapsed_seconds": elapsed,
            "healthy_after_release": True,
        }
    bench.write_json(directory / "limits.json", {"status": "passed", "results": results})


async def small(directory, base):
    plan, actors = read(directory, "plan.json"), read(directory, "clients.json")
    results = []
    semaphore = asyncio.Semaphore(plan["small_forget_concurrency"])
    await pause(directory, False)
    scopes = [a["scopes"][4] for a in actors]

    def background_jobs():
        with admin() as conn:
            return conn.execute(
                "SELECT state,count(*) n FROM memory_ops.job WHERE scope_id=ANY(%s::uuid[]) "
                "GROUP BY state",
                (scopes,),
            ).fetchall()

    before_jobs = {r["state"]: r["n"] for r in background_jobs()}
    started = time.perf_counter()
    async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as client:

        async def one(index):
            due = (
                started
                + plan["small_forget_start_seconds"]
                + index * plan["small_forget_window_seconds"] / plan["small_forget_samples"]
            )
            await asyncio.sleep(max(0, due - time.perf_counter()))
            async with semaphore:
                actor = actors[index % len(actors)]
                created = await observe(client, actor, "small-forget")
                bench.require(created.status_code == 201, "small fixture creation failed")
                target = created.json()["memory_id"]
                request = {
                    "memory_ids": [target],
                    "mode": "purge",
                    "reason": "synthetic resource probe",
                }
                key = headers(actor)
                start = time.perf_counter()
                response = await client.post("/v1/forget", headers=key, json=request)
                elapsed = (time.perf_counter() - start) * 1000
                bench.require(
                    response.status_code == FORGET_STATUS and response.json()["object_count"] == 1,
                    "small purge failed",
                )
                absent = await client.post(
                    "/v1/explain", headers=headers(actor), json={"memory_id": target}
                )
                replay = await client.post("/v1/forget", headers=key, json=request)
                bench.require(
                    absent.status_code == 404 and replay.json() == response.json(),
                    "small purge visibility or replay failed",
                )
                results.append(
                    {"request_id": response.headers["x-request-id"], "client_ms": elapsed}
                )

        await asyncio.gather(
            bench.load(directory, base),
            *[one(i) for i in range(plan["small_forget_samples"])],
        )
    for _ in range(1000):
        after_jobs = {r["state"]: r["n"] for r in background_jobs()}
        if after_jobs.get("pending", 0) + after_jobs.get("running", 0) == 0:
            break
        await asyncio.sleep(0.05)
    expected_jobs = plan["mixed_load_seconds"] * 5
    bench.require(
        after_jobs.get("succeeded", 0) - before_jobs.get("succeeded", 0) == expected_jobs
        and all(after_jobs.get(s, 0) == before_jobs.get(s, 0) for s in ("failed", "cancelled"))
        and after_jobs.get("pending", 0) + after_jobs.get("running", 0) == 0,
        "mixed-load worker outcomes incomplete",
    )
    await pause(directory, True)
    background = [
        json.loads(line) for line in (directory / "client.jsonl").read_text().splitlines()
    ]
    bench.require(
        all(r["valid"] and r["error"] is None for r in background)
        and sum(r["operation"] == "recall" for r in background) == 4 * expected_jobs
        and sum(r["operation"] == "observe" for r in background) == expected_jobs,
        "mixed API load incomplete",
    )
    timings = {}
    for _ in range(250):
        timings = {
            r["request_id"]: r
            for r in (
                json.loads(line)
                for line in (directory / "server.jsonl").read_text().splitlines(keepends=True)
                if line.endswith("\n")
            )
        }
        if all(r["request_id"] in timings for r in results):
            break
        await asyncio.sleep(0.02)
    bench.require(all(r["request_id"] in timings for r in results), "missing purge timing records")
    durations = [timings[r["request_id"]]["transaction_ms"] for r in results]
    bench.require(all(value is not None for value in durations), "missing purge timings")
    p95 = bench.percentile(durations)
    bench.write_json(
        directory / "small-forget.json",
        {
            "status": "passed" if p95 < plan["small_forget_p95_ms"] else "failed",
            "samples": len(results),
            "concurrency": plan["small_forget_concurrency"],
            "transaction_p95_ms": p95,
            "client_p95_ms": bench.percentile([r["client_ms"] for r in results]),
            "visibility_denials": len(results),
            "idempotent_replays": len(results),
            "mixed_background_load": True,
            "background": {
                "seconds": plan["mixed_load_seconds"],
                "recall": 4 * expected_jobs,
                "observe": expected_jobs,
                "succeeded_jobs": expected_jobs,
                "workers": 2,
            },
        },
    )


async def large(directory, base):
    plan, actor = read(directory, "plan.json"), read(directory, "clients.json")[0]
    async with httpx.AsyncClient(base_url=base, timeout=950, trust_env=False) as client:
        created = await observe(client, actor, "large-forget")
        bench.require(created.status_code == 201, "large source creation failed")
        source = UUID(created.json()["memory_id"])
        tenant, scope = UUID(actor["tenant_id"]), UUID(actor["scopes"][5])
        ids = [uuid4() for _ in range(plan["large_purge_objects"] - 1)]
        with admin() as conn:
            bench.copy_rows(
                conn,
                "memory.object",
                ["tenant_id", "id", "scope_id", "kind"],
                ((tenant, item, scope, "assertion") for item in ids),
            )
            bench.copy_rows(
                conn,
                "memory.assertion",
                ["tenant_id", "id", "scope_id", "subject", "predicate"],
                ((tenant, item, scope, f"probe{n}", "resource_tier") for n, item in enumerate(ids)),
            )
            bench.copy_rows(
                conn,
                "memory.assertion_revision",
                [
                    "tenant_id",
                    "assertion_id",
                    "scope_id",
                    "revision",
                    "value",
                    "valid_time",
                    "explicit_intent",
                ],
                ((tenant, item, scope, 1, "topic000", "[2026-09-01,)", True) for item in ids),
            )
            bench.copy_rows(
                conn,
                "memory.provenance_edge",
                ["tenant_id", "child_id", "child_revision", "parent_id", "scope_id", "quote"],
                ((tenant, item, 1, source, scope, "topic000") for item in ids),
            )
            conn.execute("CREATE TEMP TABLE probe_lex(id uuid,tokens text) ON COMMIT DROP")
            bench.copy_rows(
                conn,
                "probe_lex",
                ["id", "tokens"],
                ((item, segment(f"probe{n} resource_tier topic000")) for n, item in enumerate(ids)),
            )
            conn.execute(
                """INSERT INTO memory.assertion_lexical
                (tenant_id,assertion_id,revision,scope_id,profile,search_text)
                SELECT %s,id,1,%s,%s,to_tsvector('simple',tokens) FROM probe_lex""",
                (tenant, scope, JAPANESE_PROFILE),
            )
        request = {"memory_ids": [str(source)], "reason": "synthetic 10000-object resource probe"}
        start = time.perf_counter()
        response = await client.post(
            "/v1/forget", headers=headers(actor), json=request | {"mode": "preview"}
        )
        preview_seconds = time.perf_counter() - start
        bench.require(
            response.status_code == FORGET_STATUS
            and response.json()["object_count"] == plan["large_purge_objects"],
            "large closure preview failed",
        )
        key = headers(actor)
        start = time.perf_counter()
        response = await client.post("/v1/forget", headers=key, json=request | {"mode": "purge"})
        elapsed = time.perf_counter() - start
        targets = [source, *ids]
        with admin() as conn:
            state = conn.execute(
                """SELECT
                (SELECT count(*) FROM memory.episode WHERE id=%s) episodes,
                (SELECT count(*) FROM memory.assertion WHERE id=ANY(%s)) assertions,
                (SELECT count(*) FROM memory_ops.object_tombstone
                 WHERE object_id=ANY(%s)) tombstones,
                (SELECT count(*) FROM memory_ops.deletion_target
                 WHERE object_id=ANY(%s)) targets""",
                (source, ids, targets, targets),
            ).fetchone()
        success = response.status_code == FORGET_STATUS
        if success:
            bench.require(
                response.json()["object_count"] == len(targets)
                and state
                == {
                    "episodes": 0,
                    "assertions": 0,
                    "tombstones": len(targets),
                    "targets": len(targets),
                },
                "large deletion incomplete",
            )
            replay = await client.post("/v1/forget", headers=key, json=request | {"mode": "purge"})
            bench.require(replay.json() == response.json(), "large deletion replay changed")
        else:
            bench.require(
                state == {"episodes": 1, "assertions": len(ids), "tombstones": 0, "targets": 0},
                "failed large purge was not atomic",
            )
        bench.write_json(
            directory / "large-purge.json",
            {
                "status": "passed"
                if success and elapsed < plan["large_purge_deadline_seconds"]
                else "failed",
                "objects": len(targets),
                "http_status": response.status_code,
                "error_code": None if success else response.json().get("code"),
                "preview_seconds": preview_seconds,
                "purge_seconds": elapsed,
                "post_state": state,
                "atomic_rollback_on_failure": not success,
            },
        )


def cold_server(directory):
    settings = Settings(**read(directory, "runtime.json"))
    journal = bench.Journal(directory / ("cold-server-" + str(time.time_ns()) + ".jsonl"))
    try:
        uvicorn.run(
            create_app(settings, timing_sink=lambda event: journal.record(asdict(event))),
            host="0.0.0.0",
            port=8000,
            access_log=False,
            log_level="warning",
        )
    finally:
        journal.close()


async def cold(directory, base, index):
    plan, actor = read(directory, "plan.json"), read(directory, "clients.json")[0]
    mode = plan["cold_modes"][index // 4]
    sel = index % 4
    data = {
        "scope_ids": actor["scopes"][: 4 - sel],
        "purpose": "synthetic-cold-probe",
        "mode": "implicit",
        "token_budget": 2000,
        "max_items": 8,
        "retrieval_mode": mode,
        "query": "" if mode == "vector" else "topic003",
    }
    if mode != "lexical":
        data["vector_query"] = {
            "model": bench.MODEL.model_dump(),
            "values": bench.vectors(read(directory, "profile.json"))[3],
        }
    with admin() as conn:
        before = conn.execute("""SELECT pg_postmaster_start_time()::text started,
            pg_read_file('/proc/sys/kernel/random/boot_id',0,100) boot_id,
            pg_read_file('/proc/uptime',0,100) uptime,
            blks_read,blks_hit FROM pg_stat_database WHERE datname=current_database()""").fetchone()
    allowed = set()
    for n in range(10000):
        if bench.scope_index(n, 10000) < 4 - sel:
            allowed.add(str(bench.uid(f"episode/0/{n}")))
            if n % 10 == 0:
                allowed.add(str(bench.uid(f"assertion/0/{n}")))
    rows = []
    async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as client:
        for phase in ("cold", *(["warm"] * plan["warm_repeats_per_cold_sample"])):
            start = time.perf_counter()
            response = await client.post("/v1/recall", headers=headers(actor), json=data)
            elapsed = (time.perf_counter() - start) * 1000
            bench.require(response.status_code == 200, "cold recall failed")
            result = RecallResult.model_validate_json(response.content)
            bench.require(
                bool(result.items)
                and all(
                    str(item.memory_id) in allowed
                    and all(str(source) in allowed for source in item.source)
                    for item in result.items
                ),
                "cold recall authorization/identity mismatch",
            )
            rows.append(
                {
                    "phase": phase,
                    "client_ms": elapsed,
                    "request_id": response.headers["x-request-id"],
                }
            )
    bench.write_json(
        directory / f"cold-{index}.json",
        {
            "mode": mode,
            "selectivity_percent": plan["cold_selectivity_percent"][sel],
            "before": before,
            "samples": rows,
            "condition": plan["cold_conditions"],
            "physical_host_cold": False,
        },
    )


def report(directory):
    plan = read(directory, "plan.json")
    limits_result = read(directory, "limits.json")
    small_result = read(directory, "small-forget.json")
    large_result = read(directory, "large-purge.json")
    statistics = [read(directory, f"statistics-{phase}.json") for phase in ("restore", "cold")]
    cold_results = [read(directory, f"cold-{i}.json") for i in range(12)]
    identifiers = []
    for index, value in enumerate(cold_results):
        bench.require(
            value["mode"] == plan["cold_modes"][index // 4]
            and value["selectivity_percent"] == plan["cold_selectivity_percent"][index % 4]
            and [r["phase"] for r in value["samples"]]
            == ["cold", *(["warm"] * plan["warm_repeats_per_cold_sample"])],
            "cold cohort incomplete",
        )
        identifiers.extend(r["request_id"] for r in value["samples"])
    bench.require(len(identifiers) == len(set(identifiers)), "duplicate cold request identities")
    timings = {
        row["request_id"]: row
        for path in directory.glob("cold-server-*.jsonl")
        for row in (json.loads(line) for line in path.read_text().splitlines())
    }
    bench.require(
        len({r["before"]["boot_id"] for r in cold_results}) == 12
        and len({r["before"]["started"] for r in cold_results}) == 12
        and all(float(r["before"]["uptime"].split()[0]) < 120 for r in cold_results),
        "fresh guest/postmaster state not demonstrated",
    )
    for result in cold_results:
        for sample in result["samples"]:
            metric = timings.get(sample["request_id"])
            bench.require(
                metric is not None
                and metric["transaction_ms"] is not None
                and metric["status"] == 200,
                "cold timing missing",
            )
            sample["transaction_ms"] = metric["transaction_ms"]
    result = {
        "format": "pgag-resource-probes-result-v1",
        "build_identity": read(directory, "build-identity.json"),
        "migration": read(directory, "migration.json"),
        "plan_digest": bench.digest(plan),
        "limits": limits_result,
        "small_forget": small_result,
        "large_purge": large_result,
        "statistics": statistics,
        "cold_guest": cold_results,
        "probe_checks_passed": all(
            r["status"] == "passed" for r in (limits_result, small_result, large_result)
        ),
        "resource_qualified": False,
        "m2_qualified": False,
        "unqualified": [
            "physical-host-device-cold-cache",
            "exclusive-production-capacity",
            "live-provider-billing",
        ],
    }
    bench.write_json(directory / "report.json", result)
    print(bench.canonical(result), flush=True)
    bench.require(result["probe_checks_passed"], "resource probes failed; see retained report")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=(
            "prepare",
            "analyze",
            "rebind",
            "limits",
            "small",
            "large",
            "cold",
            "cold-server",
            "report",
        ),
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--index", type=int)
    parser.add_argument("--phase", choices=("restore", "cold"))
    args = parser.parse_args()
    if args.operation == "rebind":
        rebind(args.directory)
    elif args.operation == "report":
        report(args.directory)
    elif args.operation == "prepare":
        prepare(args.directory, args.plan)
    elif args.operation == "analyze":
        if args.phase is None:
            parser.error("analyze requires --phase")
        analyze(args.directory, args.phase)
    elif args.operation == "cold-server":
        cold_server(args.directory)
    elif args.operation == "cold":
        asyncio.run(cold(args.directory, args.base_url, args.index))
    else:
        asyncio.run(
            {"limits": limits, "small": small, "large": large}[args.operation](
                args.directory,
                args.base_url,
            )
        )


if __name__ == "__main__":
    main()
