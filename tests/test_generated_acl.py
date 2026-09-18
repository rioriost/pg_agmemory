"""Opt-in generated Native API ACL experiment; no model calls or quality claim.

Set PGAG_M2_GENERATED_ACL=1, PGAG_TEST_DATABASE_URL (a fresh disposable DB),
PGAG_M2_IMPLEMENTATION_SHA (the full implementation commit SHA), and
PGAG_M2_SAFETY_OUTPUT (a NEW output directory, including absolute artifact paths).
Reports contain synthetic identifiers and counts, never JWTs, source content,
or response bodies.

The default suite skips this experiment without any live-model opt-in.
"""

import asyncio
import json
import os
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from pg_agmemory.models import (
    DeletionPreview,
    EpisodeExplanation,
    Evidence,
    Explain,
    Forget,
    MemoryReference,
    Observe,
    Recall,
    Remember,
)
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PGAG_M2_GENERATED_ACL") != "1",
        reason="Set PGAG_M2_GENERATED_ACL=1 for the 10,000-request generated ACL experiment",
    ),
]

OPERATIONS = {
    "explain": "/v1/explain",
    "embedding_input": "/v1/embedding-inputs",
    "forget_preview": "/v1/forget",
    "recall_required": "/v1/recall",
    "remember_foreign_evidence": "/v1/remember",
}
CATEGORIES = ("cross_tenant", "same_tenant_cross_scope")
EXPECTED_CASES = 20 * 100 * len(OPERATIONS)


class Report:
    def __init__(self, implementation_sha):
        self.run_id = uuid4().hex
        output = os.environ.get("PGAG_M2_SAFETY_OUTPUT", "")
        assert output, "Set PGAG_M2_SAFETY_OUTPUT to a new output directory"
        self.path = Path(output).resolve()
        self.path.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.stream = (self.path / "cases.jsonl").open("x", encoding="utf-8")
        self.seen = set()
        self.summary = {
            "schema": "generated-native-acl-v1",
            "run_id": self.run_id,
            "implementation_sha": implementation_sha,
            "transport": "real_loopback_http",
            "concurrency": 1,
            "automatic_retries": 0,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "qualification": "none",
            "claims": {
                "all_m2": False,
                "human_quality": False,
                "backup_deletion": False,
                "fixed_attack_suite_executed_here": False,
            },
            "planned": {
                "real_sources": 100,
                "actors": 20,
                "cases": EXPECTED_CASES,
                "by_category": dict.fromkeys(CATEGORIES, 5000),
                "by_operation": dict.fromkeys(OPERATIONS, 2000),
            },
            "sources": [],
            "actors": [],
            "positive_controls": {},
            "actual": {
                "attempted": 0,
                "http_responses_completed": 0,
                "passed": 0,
                "failed": 0,
                "by_category": dict.fromkeys(CATEGORIES, 0),
                "by_operation": dict.fromkeys(OPERATIONS, 0),
                "by_category_operation": {
                    category: dict.fromkeys(OPERATIONS, 0) for category in CATEGORIES
                },
                "by_actor": {},
                "observed_sdk_errors": {},
            },
            "unexpected_errors": [],
            "postconditions": {},
        }
        self.sync()

    def positive(self, name):
        counts = self.summary["positive_controls"]
        counts[name] = counts.get(name, 0) + 1

    def case(self, row):
        identity = (row["actor_id"], row["memory_id"], row["operation"])
        assert identity not in self.seen, "Duplicate generated actor/source/operation"
        self.seen.add(identity)
        actual = self.summary["actual"]
        actual["attempted"] += 1
        actual["http_responses_completed"] += int(row["wire_complete"])
        actual["passed" if row["passed"] else "failed"] += 1
        actual["by_category"][row["category"]] += 1
        actual["by_operation"][row["operation"]] += 1
        actual["by_category_operation"][row["category"]][row["operation"]] += 1
        actors = actual["by_actor"]
        actors[row["actor_id"]] = actors.get(row["actor_id"], 0) + 1
        errors = actual["observed_sdk_errors"]
        code = row["sdk_error_code"]
        if code is not None:
            errors[code] = errors.get(code, 0) + 1
        if not row["passed"]:
            self.summary["unexpected_errors"].append(row)
        self.stream.write(json.dumps(row, sort_keys=True) + "\n")
        if actual["attempted"] % 50 == 0 or not row["passed"]:
            self.sync()

    def sync(self):
        self.stream.flush()
        os.fsync(self.stream.fileno())
        pending = self.path / "summary.pending"
        with pending.open("w", encoding="utf-8") as stream:
            json.dump(self.summary, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(self.path / "summary.json")
        descriptor = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def finish(self, passed):
        self.summary["status"] = "passed" if passed else "failed"
        self.summary["qualification"] = "generated_acl_only" if passed else "none"
        self.summary["finished_at"] = datetime.now(UTC).isoformat()
        try:
            self.sync()
        finally:
            self.stream.close()


class ObservedStream(httpx.AsyncByteStream):
    """Tee the real HTTP stream without replacing, consuming, or retrying it."""

    def __init__(self, stream, observation):
        self.stream = stream
        self.observation = observation

    async def __aiter__(self):
        async for chunk in self.stream:
            self.observation["body_bytes"] += len(chunk)
            remaining = 4096 - len(self.observation["body"])
            if remaining > 0:
                self.observation["body"].extend(chunk[:remaining])
            yield chunk
        self.observation["complete"] = True

    async def aclose(self):
        await self.stream.aclose()


class WireAudit:
    def __init__(self):
        self.responses = []

    async def response(self, response):
        observation = {
            "status": response.status_code,
            "method": response.request.method,
            "path": response.request.url.path,
            "request_id": response.headers.get("x-request-id"),
            "content_type": response.headers.get("content-type", "").split(";")[0],
            "cache_control": response.headers.get("cache-control"),
            "body": bytearray(),
            "body_bytes": 0,
            "complete": False,
        }
        self.responses.append(observation)
        response.stream = ObservedStream(response.stream, observation)

    def result(self, operation, error):
        if len(self.responses) != 1:
            return None, False, False
        wire = self.responses[0]
        canonical = False
        if wire["complete"] and wire["body_bytes"] <= 4096 and error is not None:
            try:
                body = json.loads(wire["body"])
                canonical = (
                    wire["status"] == 404
                    and wire["method"] == "POST"
                    and wire["path"] == OPERATIONS[operation]
                    and wire["content_type"] == "application/json"
                    and wire["cache_control"] == "no-store"
                    and body
                    == {
                        "code": "not_found",
                        "request_id": str(error.request_id),
                        "retryable": False,
                        "details": {},
                    }
                    and wire["request_id"] == str(error.request_id)
                    and isinstance(error.request_id, UUID)
                )
            except (ValueError, UnicodeError, RecursionError):
                pass
        return wire["status"], wire["complete"], canonical


def token(env, subject):
    return env.token(sub=subject, exp=int(time.time()) + 7200)


def add_actors(env, report):
    actors = []
    with psycopg.connect(env.admin_url) as conn:
        for tenant_index in range(2):
            for _ in range(10):
                actor = {
                    "principal_id": str(uuid4()),
                    "subject": str(uuid4()),
                    "scope_id": str(uuid4()),
                    "tenant_index": tenant_index,
                }
                tenant = env.tenants[tenant_index]
                conn.execute(
                    """INSERT INTO memory.principal(tenant_id,id,external_subject)
                       VALUES (%s,%s,%s)""",
                    (tenant, actor["principal_id"], actor["subject"]),
                )
                conn.execute(
                    "INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)",
                    (tenant, actor["scope_id"]),
                )
                conn.execute(
                    """INSERT INTO memory.scope_member
                       (tenant_id,scope_id,principal_id,permissions)
                       VALUES (%s,%s,%s,ARRAY['read','write','delete'])""",
                    (tenant, actor["scope_id"], actor["principal_id"]),
                )
                actors.append(actor)
        memberships = conn.execute(
            """SELECT principal_id,scope_id,permissions FROM memory.scope_member
               WHERE principal_id=ANY(%s::uuid[])""",
            ([actor["principal_id"] for actor in actors],),
        ).fetchall()
        assert len(memberships) == 20
        expected = {actor["principal_id"]: actor["scope_id"] for actor in actors}
        for principal, scope, permissions in memberships:
            assert str(scope) == expected[str(principal)]
            assert set(permissions) == {"read", "write", "delete"}
    report.summary["actors"] = [
        {key: value for key, value in actor.items() if key != "subject"} for actor in actors
    ]
    report.sync()
    return actors


def database_counts(env, actors):
    with psycopg.connect(env.admin_url) as conn:
        tenants = []
        for tenant in env.tenants:
            counts = conn.execute(
                """SELECT
                   (SELECT count(*) FROM memory.episode WHERE tenant_id=%s),
                   (SELECT count(*) FROM memory.assertion WHERE tenant_id=%s),
                   (SELECT count(*) FROM memory.object WHERE tenant_id=%s),
                   (SELECT count(*) FROM memory_ops.job WHERE tenant_id=%s),
                   (SELECT count(*) FROM memory_ops.model_call WHERE tenant_id=%s)""",
                (tenant,) * 5,
            ).fetchone()
            tenants.append(
                dict(
                    zip(
                        ("episodes", "assertions", "objects", "jobs", "model_calls"),
                        counts,
                        strict=True,
                    )
                )
            )
        empty_scopes = conn.execute(
            """SELECT s.scope_id,count(o.id) FROM unnest(%s::uuid[]) AS s(scope_id)
               LEFT JOIN memory.object o ON o.scope_id=s.scope_id
               GROUP BY s.scope_id ORDER BY s.scope_id""",
            ([actor["scope_id"] for actor in actors],),
        ).fetchall()
    return {
        "tenants": tenants,
        "actor_scope_object_counts": {str(scope): count for scope, count in empty_scopes},
    }


async def operate(client, operation, scope, memory_id, key):
    reference = Explain(memory_id=memory_id)
    if operation == "explain":
        return await client.explain(reference)
    if operation == "embedding_input":
        return await client.embedding_input(reference)
    if operation == "forget_preview":
        return await client.forget(
            Forget(memory_ids=[memory_id], mode="preview", reason="generated-acl"),
            idempotency_key=key,
        )
    if operation == "recall_required":
        return await client.recall(
            Recall(
                scope_ids=[scope],
                query="Gold",
                purpose="generated-acl",
                required_memory_refs=[MemoryReference(memory_id=memory_id)],
                max_items=1,
                token_budget=8000,
            )
        )
    assert operation == "remember_foreign_evidence"
    return await client.remember(
        Remember(
            scope_id=scope,
            subject="synthetic",
            value="Gold",
            predicate="testvalue",
            explicit_intent=True,
            evidence=[Evidence(memory_id=memory_id, quote="Gold")],
        ),
        idempotency_key=key,
    )


async def admit_sources(env, base_url, report):
    sources = []
    for owner in range(2):
        async with AsyncMemoryClient(base_url, token(env, env.subjects[owner])) as client:
            for index in range(50):
                admitted = await client.observe(
                    Observe(
                        scope_id=env.scopes[owner],
                        source_namespace="generated-acl",
                        source_event_id=f"{report.run_id}-{owner}-{index}",
                        occurred_at=datetime.now(UTC),
                        content="Gold",
                        consent_reference="synthetic-generated-acl",
                        auto_extract=False,
                        auto_embed=False,
                    ),
                    idempotency_key=f"{report.run_id}-admit-{owner}-{index}",
                )
                source = {"memory_id": str(admitted.memory_id), "tenant_index": owner}
                sources.append(source)
                report.summary["sources"].append(source)
                assert admitted.revision == 1
                assert admitted.synthesis_job_id is None and admitted.embedding_job_id is None
                report.positive("observe")
                explained = await client.explain(Explain(memory_id=admitted.memory_id))
                assert isinstance(explained, EpisodeExplanation)
                assert explained.memory_id == admitted.memory_id
                assert explained.source.content == "Gold"
                report.positive("owner_explain_before")
                if index == 0:
                    for operation in tuple(OPERATIONS)[1:]:
                        result = await operate(
                            client,
                            operation,
                            env.scopes[owner],
                            admitted.memory_id,
                            f"{report.run_id}-positive-{owner}-{operation}",
                        )
                        if operation == "embedding_input":
                            assert result.memory_id == admitted.memory_id and result.text == "Gold"
                            assert result.input_format == "memory-content-v1"
                        elif operation == "forget_preview":
                            assert isinstance(result, DeletionPreview)
                            assert not result.changed and result.object_count == 1
                        elif operation == "recall_required":
                            assert [item.memory_id for item in result.items] == [admitted.memory_id]
                            assert result.items[0].content == "Gold"
                        else:
                            assert result.revision == 1 and result.epistemic_status == "reported"
                        report.positive(operation)
            report.sync()
    assert len({source["memory_id"] for source in sources}) == 100
    assert Counter(source["tenant_index"] for source in sources) == {0: 50, 1: 50}
    return sources


async def denied_case(client, audit, actor, source, operation, report):
    audit.responses.clear()
    row = {
        "case_index": report.summary["actual"]["attempted"] + 1,
        "actor_id": actor["principal_id"],
        "actor_scope_id": actor["scope_id"],
        "actor_tenant_index": actor["tenant_index"],
        "memory_id": source["memory_id"],
        "source_tenant_index": source["tenant_index"],
        "operation": operation,
        "category": (
            "same_tenant_cross_scope"
            if actor["tenant_index"] == source["tenant_index"]
            else "cross_tenant"
        ),
        "sdk_error_code": None,
        "known_outcome": False,
        "unexpected_exception_type": None,
        "passed": False,
    }
    error = None
    try:
        await operate(
            client,
            operation,
            UUID(actor["scope_id"]),
            UUID(source["memory_id"]),
            f"{report.run_id}-case-{row['case_index']}",
        )
    except MemoryClientError as exc:
        error = exc.error
        row["sdk_error_code"] = error.code
        row["known_outcome"] = not error.outcome_unknown
    except BaseException as exc:
        row["unexpected_exception_type"] = type(exc).__name__
        raise
    finally:
        status, complete, canonical = audit.result(operation, error)
        row["http_status"] = status
        row["wire_complete"] = complete
        row["canonical_no_content_error"] = canonical
        row["passed"] = bool(
            canonical
            and error is not None
            and error.code == "not_found"
            and error.native_status == 404
            and not error.retryable
            and not error.outcome_unknown
        )
        report.case(row)
    assert row["passed"], (
        f"Generated ACL case {row['case_index']} failed; inspect the redacted safety report"
    )


async def experiment(env, base_url, report):
    actors = add_actors(env, report)
    sources = await admit_sources(env, base_url, report)
    before = database_counts(env, actors)
    report.summary["before"] = before
    assert before["tenants"] == [
        {"episodes": 50, "assertions": 1, "objects": 51, "jobs": 0, "model_calls": 0}
    ] * 2
    assert len(before["actor_scope_object_counts"]) == 20
    assert set(before["actor_scope_object_counts"].values()) == {0}
    report.sync()
    for actor in actors:
        async with AsyncMemoryClient(base_url, token(env, actor["subject"])) as client:
            empty = await client.recall(
                Recall(
                    scope_ids=[UUID(actor["scope_id"])],
                    purpose="generated-acl-positive",
                )
            )
            assert empty.items == [] and empty.empty_reason == "not_found"
            report.positive("actor_own_scope_recall")
            audit = WireAudit()
            assert client._http is not None
            client._http.event_hooks["response"].append(audit.response)
            refreshed_at = time.monotonic()
            for source in sources:
                if time.monotonic() - refreshed_at >= 1800:
                    client._http.headers["Authorization"] = f"Bearer {token(env, actor['subject'])}"
                    refreshed_at = time.monotonic()
                for operation in OPERATIONS:
                    await denied_case(client, audit, actor, source, operation, report)
    actual = report.summary["actual"]
    assert actual["attempted"] == actual["passed"] == EXPECTED_CASES == len(report.seen)
    assert actual["http_responses_completed"] == EXPECTED_CASES and actual["failed"] == 0
    assert actual["by_category"] == dict.fromkeys(CATEGORIES, 5000)
    assert actual["by_operation"] == dict.fromkeys(OPERATIONS, 2000)
    assert actual["by_category_operation"] == {
        category: dict.fromkeys(OPERATIONS, 1000) for category in CATEGORIES
    }
    assert len(actual["by_actor"]) == 20 and set(actual["by_actor"].values()) == {500}
    assert actual["observed_sdk_errors"] == {"not_found": EXPECTED_CASES}
    after = database_counts(env, actors)
    report.summary["after"] = after
    assert after == before, "Denied writes and previews must leave the admitted store unchanged"
    report.summary["postconditions"]["database_counts_unchanged"] = True
    report.summary["postconditions"]["empty_actor_scopes"] = 20
    for owner in range(2):
        async with AsyncMemoryClient(base_url, token(env, env.subjects[owner])) as client:
            for source in sources:
                if source["tenant_index"] != owner:
                    continue
                explained = await client.explain(Explain(memory_id=UUID(source["memory_id"])))
                assert isinstance(explained, EpisodeExplanation)
                assert str(explained.memory_id) == source["memory_id"]
                assert explained.source.content == "Gold"
                report.positive("owner_explain_after")
    assert report.summary["positive_controls"]["owner_explain_after"] == 100
    report.summary["postconditions"]["authorized_owner_reads"] = 100
    report.summary["postconditions"]["model_calls"] = 0


def test_generated_native_api_acl_10000(request, record_property):
    implementation_sha = os.environ.get("PGAG_M2_IMPLEMENTATION_SHA", "")
    assert re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", implementation_sha), (
        "Set PGAG_M2_IMPLEMENTATION_SHA to the full implementation commit SHA"
    )
    assert os.environ.get("PGAG_TEST_DATABASE_URL"), "A fresh disposable database is required"
    report = Report(implementation_sha)
    record_property("generated_acl_report", str(report.path))
    passed = False
    try:
        env = request.getfixturevalue("env")
        api_process = request.getfixturevalue("api_process")
        with api_process("generated-acl-api.log") as (http, _):
            asyncio.run(experiment(env, str(http.base_url), report))
        passed = True
    except BaseException as exc:
        report.summary["failure_type"] = type(exc).__name__
        if isinstance(exc, MemoryClientError):
            report.summary["failure_sdk_error"] = exc.error.model_dump(mode="json")
        raise
    finally:
        report.finish(passed)
        record_property("generated_acl_summary", json.dumps(report.summary, sort_keys=True))
