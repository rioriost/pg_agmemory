"""Disposable schema-22 recovery smoke; not a restore tool for existing databases.

The helper exports committed server metadata, never remembered test deletion IDs.
It applies exact operational state after bounded deletion replay and exercises
three synthetic call outcomes, unknown-call fences and consumed quota.
The v7 fixture includes retained and purged processing/working/graph/effect derivatives.
"""

import asyncio
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic import ValidationError

from pg_agmemory.admin import AdminError
from pg_agmemory.capture_policy import CapturePolicy, CapturePolicyRequest, capture_policy
from pg_agmemory.checkpoints import Checkpoints
from pg_agmemory.compaction import Working
from pg_agmemory.database import SCHEMA_VERSION, RuntimeValidationError, migrate, validate_runtime
from pg_agmemory.deletion_history import DeletionHistory, purge_replay_suffix
from pg_agmemory.effects import ToolEffects
from pg_agmemory.graph_generation import GraphGenerationRequest, graph_generation
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.jobs import Jobs
from pg_agmemory.models import (
    AdoptCandidate,
    AppendWorkingEvent,
    CheckpointState,
    CompactWorking,
    CreateCheckpoint,
    CreateEntity,
    CreateRelation,
    EnqueueJob,
    Evidence,
    ExpandGraph,
    Explain,
    Forget,
    MemoryReference,
    Observe,
    PendingEffect,
    PlanToolEffect,
    ProcessMemory,
    QueryWorkingEvents,
    Recall,
    Remember,
    RestoreCheckpoint,
    TransitionToolEffect,
    VectorQuery,
)
from pg_agmemory.processing import Processing
from pg_agmemory.processing_recovery import (
    ProcessingRecoverySnapshot,
    capture_processing_state,
    compare_processing_state,
)
from pg_agmemory.providers import (
    ExtractionCandidate,
    ExtractionResult,
    GeneratedEmbedding,
    ProviderFailure,
    ProviderSettings,
    SummaryResult,
)
from pg_agmemory.recovery_apply import RecoveryBundle, export_bundle
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection
from pg_agmemory.synthesis_policy import SynthesisPolicy, SynthesisPolicyRequest, synthesis_policy
from pg_agmemory.worker import run_once
from pg_agmemory.worker_profile import WorkerProfile

OPERATOR = "synthetic-recovery-operator"
REVOKED = "synthetic-recovery-revoked"
CONTROL = "synthetic-recovery-control"
TARGET = "synthetic-recovery-target"
SECOND = "synthetic-recovery-second"
BASELINE = "synthetic-recovery-baseline"
MODEL_PREFIX = "synthetic-recovery-model-"
BUDGET = "synthetic-recovery-budget"
SUPPRESSED = "synthetic-recovery-suppressed"
DERIVED_PURGED = "synthetic-recovery-derived-purged"
DERIVED_RETAINED = "synthetic-recovery-derived-retained"
DERIVATIVE_NAMES = (DERIVED_PURGED, DERIVED_RETAINED)
DERIVATIVE_TEXT = "RecoveryUser / preferred_editor: Vim"
DERIVATIVE_SUMMARY = "Untrusted synthetic context; approval is still pending."
DERIVATIVE_TABLES = (
    "memory.assertion_derivation", "memory_ops.extraction_candidate",
    "memory.episode_embedding", "memory.assertion_embedding",
    "memory.working_snapshot", "memory.working_event",
    "memory.entity", "memory.entity_evidence", "memory.relation", "memory.relation_revision",
    "memory.tool_effect", "memory.tool_effect_revision", "memory.tool_effect_reference",
)


def processing_profile():
    return WorkerProfile(ProviderSettings(
        backend="local_http", endpoint="http://127.0.0.1:1",
        text_model={"name": "synthetic-recovery", "revision": "1"},
        embedding_model={"name": "synthetic-recovery-embedding", "revision": "1"},
        max_output_tokens=128,
    ))


class DrillError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise DrillError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def build_identity():
    identity = json.loads(os.environ["PGAG_RECOVERY_BUILD_IDENTITY"])
    require(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", identity["git_sha"]) is not None,
            "harness git identity missing or invalid")
    root = Path(__file__).resolve().parents[1]
    paths = ("scripts/smoke-recovery.py", "scripts/test-recovery-containers.sh",
             "tests/test_recovery_drill.py")
    hashes = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}
    require(identity["files_sha256"] == hashes, "measured recovery inputs changed during run")
    changes = identity["git_status_porcelain"]
    require(identity["has_tracked_changes"] == any(not r.startswith("?? ") for r in changes)
            and identity["has_untracked_changes"] == any(r.startswith("?? ") for r in changes)
            and identity["exact_commit_inputs"] == (not changes),
            "inconsistent harness working-tree identity")
    return identity


def rows(conn, query):
    return [
        row["data"]
        for row in conn.execute(
            sql.SQL("SELECT to_jsonb(r) AS data FROM ({}) r").format(sql.SQL(query))
        )
    ]


def fingerprint(conn, schema, table):
    values = [
        row["data"]
        for row in conn.execute(
            sql.SQL("SELECT to_jsonb(r) AS data FROM {} r").format(sql.Identifier(schema, table))
        )
    ]
    return {"count": len(values), "sha256": digest(sorted(values, key=canonical))}


def snapshot(url):
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        versions = rows(conn, "SELECT version FROM public.pgag_schema_migration ORDER BY version")
        require(versions == [{"version": n} for n in range(1, 23)], "schema22 required")
        database = rows(conn, """SELECT current_setting('server_version_num')::int AS postgres,
                                       extversion AS pgvector FROM pg_extension
                                WHERE extname='vector'""")
        require(database == [{"postgres": 180006, "pgvector": "0.8.6"}],
                "pinned PostgreSQL 18.6 and pgvector 0.8.6 required")
        tables = conn.execute(
            """SELECT tablename FROM pg_tables WHERE schemaname='memory' ORDER BY tablename"""
        ).fetchall()
        counts = {f"memory.{r['tablename']}": fingerprint(conn, "memory", r["tablename"])
                  for r in tables}
        for table in ("job", "job_input", "job_identity", "extraction_candidate",
                      "model_call", "source_event", "source_access_state", "source_access_event"):
            counts[f"memory_ops.{table}"] = fingerprint(conn, "memory_ops", table)
        result = {
            "format": "pgag-isolated-purge-drill-v7",
            "schema_version": SCHEMA_VERSION,
            "database": database[0],
            "tenant": rows(conn, "SELECT id,access_epoch,deletion_epoch FROM memory.tenant"),
            "principals": rows(conn, "SELECT * FROM memory.principal ORDER BY id"),
            "objects": rows(conn, """SELECT tenant_id,id,scope_id,kind
                                    FROM memory.object ORDER BY id"""),
            "memberships": rows(conn, """SELECT * FROM memory.scope_member
                                        ORDER BY tenant_id,scope_id,principal_id"""),
            "tombstones": rows(conn, """SELECT tenant_id,object_id,scope_id
                                        FROM memory_ops.object_tombstone ORDER BY object_id"""),
            "deletions": rows(conn, """SELECT tenant_id,id,principal_id,mode,state,object_count,
                                             deletion_epoch,target_manifest_version
                                      FROM memory_ops.deletion_request
                                      ORDER BY deletion_epoch"""),
            "deletion_targets": rows(conn, """SELECT tenant_id,deletion_id,object_id,scope_id
                                             FROM memory_ops.deletion_target
                                             ORDER BY deletion_id,object_id"""),
            "access_events": rows(conn, """SELECT tenant_id,scope_id,principal_id,access_epoch,
                                                 operation,previous_permissions,
                                                 previous_expires_at,permissions,expires_at
                                          FROM memory_ops.scope_access_event
                                          ORDER BY access_epoch"""),
            "canonical": counts,
        }
    result["processing_state"] = capture_processing_state(
        url, UUID(result["tenant"][0]["id"])
    ).model_dump(mode="json")
    with graph_generation(url, GraphGenerationRequest(
        operation="get", tenant_id=UUID(result["tenant"][0]["id"]),
    )) as generation:
        result["graph_generation"] = generation.model_dump(mode="json")
    return result


def deletion_history(evidence):
    tenant = evidence["tenant"][0]
    receipts = evidence["deletions"]
    by_receipt = {r["id"]: [] for r in receipts}
    require(len(by_receipt) == len(receipts), "duplicate deletion receipt")
    for target in evidence["deletion_targets"]:
        require(target["tenant_id"] == tenant["id"] and target["deletion_id"] in by_receipt,
                "deletion target manifest mismatch")
        by_receipt[target["deletion_id"]].append(
            {key: target[key] for key in ("object_id", "scope_id")}
        )
    require(all(r["tenant_id"] == tenant["id"] for r in receipts),
            "deletion tenant mismatch")
    try:
        history = DeletionHistory.model_validate_json(json.dumps({
            "tenant_id": tenant["id"], "access_epoch": tenant["access_epoch"],
            "deletion_epoch": tenant["deletion_epoch"],
            "records": [{
                "deletion_id": r["id"],
                **{k: r[k] for k in ("principal_id", "mode", "state", "object_count",
                                    "deletion_epoch", "target_manifest_version")},
                "targets": sorted(by_receipt[r["id"]], key=lambda t: t["object_id"]),
            } for r in receipts],
        }))
    except ValidationError:
        raise DrillError("invalid or incomplete deletion history") from None
    tombstones = evidence["tombstones"]
    pairs = {(r["object_id"], r["scope_id"]) for r in tombstones}
    require(len(pairs) == len(tombstones)
            and all(t["tenant_id"] == tenant["id"] for t in tombstones)
            and pairs == {(str(t.object_id), str(t.scope_id))
                          for r in history.records for t in r.targets},
            "deletion target manifest mismatch")
    return history


def validate_evidence(before, latest):
    """No model/policy recovery, new objects, inferred mappings or operator regrant."""
    histories = []
    for evidence in (before, latest):
        require(evidence["format"] == "pgag-isolated-purge-drill-v3", "unknown evidence format")
        require(evidence["schema_version"] == 22, "schema22 required")
        require(len(evidence["tenant"]) == 1, "exactly one disposable tenant required")
        for table in ("memory.scope_synthesis_policy", "memory.scope_capture_policy",
                      "memory_ops.model_call",
                      "memory.working_snapshot", "memory_ops.extraction_candidate"):
            require(evidence["canonical"][table]["count"] == 0,
                    "model processing must remain disabled")
        histories.append(deletion_history(evidence))
    validate_source_authority(before, latest)
    old, new = before["tenant"][0], latest["tenant"][0]
    require(old["id"] == new["id"], "tenant lineage mismatch")
    try:
        suffix_receipts = purge_replay_suffix(*histories)
    except AdminError as exc:
        raise DrillError(exc.code) from None
    require(before["principals"] == latest["principals"], "principal changes unsupported")
    require(before["objects"] == latest["objects"], "object anchor changes unsupported")
    anchors = {r["id"]: r for r in before["objects"]}
    require(len(anchors) == len(before["objects"])
            and all(t["object_id"] in anchors
                    and anchors[t["object_id"]]["scope_id"] == t["scope_id"]
                    and anchors[t["object_id"]]["tenant_id"] == t["tenant_id"]
                    for t in latest["tombstones"]), "deletion anchor mismatch")
    events = latest["access_events"]
    require(events[:len(before["access_events"])] == before["access_events"],
            "ACL history prefix mismatch")
    suffix = events[len(before["access_events"]):]
    members = {(m["scope_id"], m["principal_id"]): m for m in before["memberships"]}
    require(len(members) == len(before["memberships"]), "duplicate baseline membership")
    principals = {p["id"]: p for p in latest["principals"]}
    require(len(principals) == len(latest["principals"])
            and all(p["tenant_id"] == new["id"] for p in principals.values()),
            "invalid principal identities")
    scopes = {r["scope_id"] for r in before["objects"]}
    for epoch, event in enumerate(suffix, old["access_epoch"] + 1):
        require(event["tenant_id"] == new["id"] and event["access_epoch"] == epoch
                and event["principal_id"] in principals and event["scope_id"] in scopes,
                "incomplete or unsupported ACL history")
        key = event["scope_id"], event["principal_id"]
        previous = members.get(key)
        require(previous is not None
                and previous["permissions"] == event["previous_permissions"]
                and previous["expires_at"] is None and event["previous_expires_at"] is None,
                "ACL previous state mismatch")
        if event["operation"] == "revoke":
            require(event["permissions"] is None and event["expires_at"] is None,
                    "invalid revocation")
            del members[key]
        else:
            require(event["operation"] == "set" and event["permissions"]
                    and len(set(event["permissions"])) == len(event["permissions"])
                    and set(event["permissions"]) < set(previous["permissions"])
                    and event["expires_at"] == previous["expires_at"],
                    "only permission reductions without expiry changes are supported")
            members[key] = {**previous, "permissions": event["permissions"]}
    require(new["access_epoch"] == old["access_epoch"] + len(suffix),
            "incomplete ACL epoch sequence")
    require(sorted(latest["memberships"], key=canonical)
            == sorted(members.values(), key=canonical),
            "latest membership state disagrees with revocation history")
    for receipt in suffix_receipts:
        actor = principals.get(str(receipt.principal_id))
        require(actor is not None, "deletion actor unavailable")
        for target in receipt.targets:
            for evidence in (before, latest):
                require(any(m["principal_id"] == actor["id"] and m["tenant_id"] == new["id"]
                            and m["scope_id"] == str(target.scope_id)
                            and m["permissions"] == ["admin"] and m["expires_at"] is None
                            for m in evidence["memberships"]),
                        "trusted deletion actor no longer authorized; replay unsupported")
    return suffix_receipts, suffix, principals


def validate_source_authority(before, latest):
    for table in ("memory_ops.source_access_state", "memory_ops.source_access_event"):
        require(table in before["canonical"] and table in latest["canonical"]
                and before["canonical"][table] == latest["canonical"][table],
                "source authority requires exact matching content")


def validate_application_evidence(before, latest):
    require(before["format"] == latest["format"] == "pgag-isolated-purge-drill-v7",
            "application evidence v7 required")
    require(before["schema_version"] == latest["schema_version"] == 22, "schema22 required")
    validate_source_authority(before, latest)
    require(len(before["tenant"]) == len(latest["tenant"]) == 1, "single tenant required")
    require(before["principals"] == latest["principals"] and before["objects"] == latest["objects"],
            "changed identities or anchors unsupported")
    try:
        receipts = purge_replay_suffix(deletion_history(before), deletion_history(latest))
    except AdminError as exc:
        raise DrillError(exc.code) from None
    principals = {p["id"]: p for p in latest["principals"]}
    for receipt in receipts:
        require(str(receipt.principal_id) in principals, "missing deletion actor")
        for state in (before, latest):
            for target in receipt.targets:
                require(any(m["principal_id"] == str(receipt.principal_id)
                            and m["scope_id"] == str(target.scope_id)
                            and m["permissions"] == ["admin"] and m["expires_at"] is None
                            for m in state["memberships"]), "deletion actor changed")
    return receipts, principals


def runtime_url(admin_url, *, create=False):
    params = conninfo_to_dict(admin_url)
    if create:
        with psycopg.connect(admin_url) as conn:
            conn.execute(
                sql.SQL("""CREATE ROLE pgag_recovery LOGIN NOSUPERUSER NOCREATEDB
                           NOCREATEROLE NOBYPASSRLS PASSWORD {} IN ROLE pgag_runtime""")
                .format(sql.Literal(params["password"]))
            )
    return make_conninfo(**(params | {"user": "pgag_recovery"}))


@asynccontextmanager
async def service(url, subject=OPERATOR):
    async with principal_connection(url, subject) as (conn, identity):
        async with conn.transaction():
            await bind_identity(conn, subject, identity)
            yield MemoryService(conn, identity)


async def seed_derivatives(admin_url, url, tenant, operator):
    profile = processing_profile()
    scopes = []
    for _ in DERIVATIVE_NAMES:
        scope = uuid4()
        with psycopg.connect(admin_url) as conn:
            conn.execute("INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)", (tenant, scope))
            epoch = conn.execute(
                "SELECT access_epoch FROM memory.tenant WHERE id=%s", (tenant,),
            ).fetchone()[0]
        with scope_access(admin_url, ScopeAccessRequest(
            operation="set", tenant_id=tenant, scope_id=scope, principal_id=operator,
            expected_access_epoch=epoch, permissions=("admin",), no_expiry=True,
        )) as result:
            epoch = result.access_epoch
        with synthesis_policy(admin_url, SynthesisPolicyRequest(
            operation="set", tenant_id=tenant, scope_id=scope, expected_access_epoch=epoch,
            policy=SynthesisPolicy(
                enabled=True, profile_digest=profile.digest, kinds=["extract", "embed", "compact"],
                publish_predicates=["preferred_editor"],
                consent_references=["disposable-recovery-drill-only"],
                max_calls=4, max_output_tokens=128,
            ),
        )):
            pass
        scopes.append(scope)
    calls = []

    async def extract(data):
        calls.append("extract")
        require(data.text == DERIVATIVE_TEXT, "unexpected derivative source")
        return ExtractionResult(
            model=profile.settings.text_model, input_digest=data.digest(),
            candidates=[ExtractionCandidate(
                subject="RecoveryUser", predicate=predicate, value="Vim",
                evidence_quote=data.text, start=0, end=len(data.text),
            ) for predicate in ("preferred_editor", "editor_choice", "other_editor")],
        )

    async def embed(data):
        calls.append("embed")
        return GeneratedEmbedding(
            model=profile.settings.embedding_model, input_digest=data.digest(),
            values=[1.0] + [0.0] * 767,
        )

    async def summarize(data):
        calls.append("compact")
        return SummaryResult(
            model=profile.settings.text_model, input_digest=data.digest(),
            summary=DERIVATIVE_SUMMARY,
        )

    profile.provider.extract, profile.provider.embed, profile.provider.summarize = (
        extract, embed, summarize,
    )
    for namespace, scope in zip(DERIVATIVE_NAMES, scopes, strict=True):
        async with service(url) as memory:
            observed = await memory.observe(Observe(
                scope_id=scope, source_namespace=namespace, source_event_id="1",
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC), content=DERIVATIVE_TEXT,
                consent_reference="disposable-recovery-drill-only",
            ), namespace)
            source = UUID(observed["memory_id"])
            queued = await Processing(memory).enqueue(ProcessMemory(
                scope_id=scope, kind="extract", source=MemoryReference(memory_id=source),
            ), namespace + "-extract")
        result = await run_once(url, OPERATOR, profile=profile)
        require(result["outcome"] == "succeeded"
                and result["result"]["counts"] == {
                    "published": 1, "duplicate": 0, "quarantined": 2,
                }, "derivative extraction did not publish/quarantine exact candidates")
        inferred = UUID(result["result"]["assertions"][0]["memory_id"])
        async with service(url) as memory:
            review = await Processing(memory).candidates(UUID(queued["job_id"]))
            adopted = await Processing(memory).adopt(UUID(queued["job_id"]), 1, AdoptCandidate(
                explicit_intent=True, expected_input_digest=review["derivation"]["input_digest"],
                reason="Synthetic explicit adoption, not a human evaluation",
            ), namespace + "-adopt")
            require(adopted["epistemic_status"] == "reported", "adoption status changed")
        for target in (source, inferred):
            async with service(url) as memory:
                await Processing(memory).enqueue(ProcessMemory(
                    scope_id=scope, kind="embed", source=MemoryReference(memory_id=target),
                ), namespace + "-embed-" + str(target))
            require((await run_once(url, OPERATOR, profile=profile))["outcome"] == "succeeded",
                    "derivative embedding failed")
        run_id, branch_id, operation_id = uuid4(), uuid4(), uuid4()
        branch = {"scope_id": scope, "run_id": run_id, "branch_id": branch_id}
        state = CheckpointState(
            goal="Preserve exact recovery state", constraints=["No approval inference"],
            pending_approvals=["Reviewer approval required"], important_ids=["TASK-RESTORE-1"],
            versions=["1.2"], paths=["src/main.py"], failed_actions=["Never blindly retry"],
            unresolved_questions=["Who approves?"], next_actions=["Ask reviewer"],
            pending_effects=[PendingEffect(
                operation_id=operation_id, description="Synthetic effect", status="unknown",
            )],
        )
        async with service(url) as memory:
            head = await Checkpoints(memory).create(CreateCheckpoint(
                **branch, expected_head=None, harness_id="synthetic-recovery-derivatives",
                harness_version="1", event_watermark=99, state=state,
                memory_refs=[MemoryReference(memory_id=source)],
            ), namespace + "-checkpoint")
            effect = await ToolEffects(memory).plan(PlanToolEffect(
                scope_id=scope, run_id=run_id, operation_id=operation_id,
                tool_name="synthetic.no-egress", action_hash="a" * 64,
                memory_refs=[MemoryReference(memory_id=source)],
            ), namespace + "-effect")
            for revision, status in ((1, "dispatched"), (2, "unknown")):
                await ToolEffects(memory).transition(
                    UUID(effect["memory_id"]), TransitionToolEffect(
                        expected_revision=revision, status=status, reason="Synthetic fixture",
                    ), namespace + "-" + status,
                )
            entities = [await SqlGraph(memory).create_entity(CreateEntity(
                scope_id=scope, entity_type="component", canonical_label=label,
                evidence=[Evidence(memory_id=source, quote=quote)], explicit_intent=True,
            ), namespace + "-" + label)
                for label, quote in (("User", "RecoveryUser"), ("Editor", "Vim"))]
            await SqlGraph(memory).create_relation(CreateRelation(
                scope_id=scope, source_entity=UUID(entities[0]["memory_id"]),
                target_entity=UUID(entities[1]["memory_id"]), predicate="depends_on",
                evidence=[Evidence(memory_id=source, quote=DERIVATIVE_TEXT)], explicit_intent=True,
            ), namespace + "-relation")
            await Working(memory).append(AppendWorkingEvent(
                **branch, source=MemoryReference(memory_id=source),
            ), namespace + "-event")
            await Working(memory).enqueue(CompactWorking(
                **branch, expected_head=UUID(head["checkpoint_id"]), through_sequence=1,
            ), namespace + "-compact")
        compacted = await run_once(url, OPERATOR, profile=profile)
        require(compacted["outcome"] == "succeeded", "derivative compaction failed")
        async with service(url) as memory:
            tail = await memory.observe(Observe(
                scope_id=scope, source_namespace=namespace + "-tail", source_event_id="1",
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC), content="Keep uncompacted tail.",
                consent_reference="disposable-recovery-drill-only",
            ), namespace + "-tail")
            await Working(memory).append(AppendWorkingEvent(
                **branch, source=MemoryReference(memory_id=UUID(tail["memory_id"])),
            ), namespace + "-tail-event")
            saved = await Working(memory).get(UUID(compacted["result"]["checkpoint_id"]))
            require(saved["checkpoint"]["state"] == state.model_dump(mode="json")
                    and saved["summary"] == DERIVATIVE_SUMMARY
                    and saved["coverage_start"] == saved["coverage_end"] == 1
                    and [r["sequence"] for r in saved["tail"]["events"]] == [2]
                    and saved["checkpoint"]["automatic_reexecution"] is False
                    and saved["checkpoint"]["resume_allowed"] is False,
                    "typed state, summary, coverage, tail or effect safety changed")
    require(calls == ["extract", "embed", "embed", "compact"] * 2,
            "exactly eight synthetic derivative calls required")


async def seed(admin_url):
    migrate(admin_url)
    url = runtime_url(admin_url, create=True)
    await validate_runtime(url)
    tenant, scope, operator, reader = (uuid4() for _ in range(4))
    with psycopg.connect(admin_url) as conn:
        conn.execute("INSERT INTO memory.tenant(id,dedup_secret) VALUES (%s,%s)",
                     (tenant, os.urandom(32)))
        conn.execute("INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)", (tenant, scope))
        for principal, subject in ((operator, OPERATOR), (reader, REVOKED)):
            conn.execute("""INSERT INTO memory.principal(tenant_id,id,external_subject)
                            VALUES (%s,%s,%s)""", (tenant, principal, subject))
    epoch = 1
    for principal, permissions in ((operator, ("admin",)), (reader, ("read", "write"))):
        with scope_access(admin_url, ScopeAccessRequest(
            operation="set", tenant_id=tenant, scope_id=scope, principal_id=principal,
            expected_access_epoch=epoch, permissions=permissions, no_expiry=True,
        )) as result:
            epoch = result.access_epoch
    async with service(url) as memory:
        for namespace in (TARGET, SECOND, BASELINE, CONTROL):
            await memory.observe(Observe(
                scope_id=scope, source_namespace=namespace, source_event_id="1",
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                content=f"Approved synthetic recovery fixture: {namespace} prefers Vim.",
                consent_reference="disposable-recovery-drill-only",
            ), namespace)
        source = await (await memory.conn.execute(
            "SELECT id FROM memory.episode WHERE source_namespace=%s", (TARGET,)
        )).fetchone()
        intent = Remember(
            scope_id=scope, subject="SyntheticRecovery", predicate="preferred_editor",
            value="Vim", explicit_intent=True,
            evidence=[Evidence(memory_id=source["id"], quote="Vim")],
        )
        assertion = await memory.remember(intent, "recovery-assertion")
        await Checkpoints(memory).create(CreateCheckpoint(
            scope_id=scope, run_id=uuid4(), branch_id=uuid4(), expected_head=None,
            harness_id="synthetic-recovery-drill", harness_version="1", event_watermark=1,
            state=CheckpointState(goal="Synthetic checkpoint contains the deleted preference"),
            memory_refs=[MemoryReference(memory_id=UUID(assertion["memory_id"]))],
        ), "recovery-checkpoint")
    async with service(url) as memory:
        source = await (await memory.conn.execute(
            "SELECT id FROM memory.episode WHERE source_namespace=%s", (BASELINE,)
        )).fetchone()
        baseline = await memory.forget(Forget(
            memory_ids=[source["id"]], reason="Pre-backup synthetic deletion",
        ), "baseline-forget")
        require(baseline["object_count"] == 1, "baseline purge target mismatch")
    async with service(url, REVOKED) as memory:
        sources = await (await memory.conn.execute("SELECT id FROM memory.episode")).fetchall()
        require(len(sources) == 3, "reader must initially see three surviving sources")
        for source in sources:
            await memory.explain(Explain(memory_id=source["id"]))
    profile = processing_profile()
    with synthesis_policy(admin_url, SynthesisPolicyRequest(
        operation="set", tenant_id=tenant, scope_id=scope, expected_access_epoch=epoch,
        policy=SynthesisPolicy(
            enabled=True, profile_digest=profile.digest, kinds=["extract"],
            consent_references=["disposable-recovery-drill-only"], max_calls=3,
            max_output_tokens=128,
        ),
    )):
        pass
    async with service(url) as memory:
        source = await memory.observe(Observe(
            scope_id=scope, source_namespace=SUPPRESSED, source_event_id="1",
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            content="Suppressed payload stays stored but must never be visible.",
            consent_reference="disposable-recovery-drill-only",
        ), SUPPRESSED)
    # Suppress is an existing stored-ledger mode, not a public API operation.
    with psycopg.connect(admin_url) as conn:
        receipt = uuid4()
        deletion_epoch = conn.execute(
            "UPDATE memory.tenant SET deletion_epoch=deletion_epoch+1 WHERE id=%s "
            "RETURNING deletion_epoch", (tenant,),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO memory_ops.deletion_request
            (tenant_id,id,principal_id,mode,state,object_count,deletion_epoch)
            VALUES (%s,%s,%s,'suppress','blocked_for_reads',1,%s)""",
            (tenant, receipt, operator, deletion_epoch),
        )
        conn.execute(
            """INSERT INTO memory_ops.deletion_target
            (tenant_id,deletion_id,object_id,scope_id,ordinal) VALUES (%s,%s,%s,%s,1)""",
            (tenant, receipt, source["memory_id"], scope),
        )
        conn.execute(
            "INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id) "
            "VALUES (%s,%s,%s)",
            (tenant, source["memory_id"], scope),
        )
    await seed_derivatives(admin_url, url, tenant, operator)
    async with service(url) as memory:
        await Jobs(memory).enqueue(
            EnqueueJob(kind="structured_remember", memory=intent), "recovery-pending-job"
        )
        for outcome in ("unknown", "failed", "succeeded", "budget"):
            source = await memory.observe(Observe(
                scope_id=scope,
                source_namespace=BUDGET if outcome == "budget" else MODEL_PREFIX + outcome,
                source_event_id="1", occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                content="Synthetic model recovery outcome: " + outcome,
                consent_reference="disposable-recovery-drill-only",
            ), "model-source-" + outcome)
            if outcome != "budget":
                await Processing(memory).enqueue(ProcessMemory(
                    scope_id=scope, kind="extract",
                    source=MemoryReference(memory_id=UUID(source["memory_id"])),
                ), "model-enqueue-" + outcome)
    with graph_generation(admin_url, GraphGenerationRequest(
        operation="get", tenant_id=tenant,
    )) as initial:
        require(initial.revision == 0 and initial.head is None, "unexpected generation baseline")
    generation_id = uuid4()
    with graph_generation(admin_url, GraphGenerationRequest(
        operation="begin", tenant_id=tenant, expected_revision=initial.revision,
        generation_id=generation_id, expected_input_digest=initial.current_input_digest,
        profile_digest=digest("synthetic-metadata-only-graph-profile"),
    )) as building:
        require(building.building is not None, "generation reservation missing")
    with graph_generation(admin_url, GraphGenerationRequest(
        operation="record", tenant_id=tenant, expected_revision=building.revision,
        generation_id=generation_id, expected_input_digest=initial.current_input_digest,
        artifact_digest=digest("synthetic-receipt-not-a-built-graph"),
    )) as recorded:
        require(recorded.head is not None and recorded.head.source_matches is True
                and not recorded.artifact_verified and not recorded.serving_enabled,
                "generation receipt must not enable or verify a graph")


async def later(admin_url):
    url = runtime_url(admin_url)
    async with service(url) as memory:
        source = await (await memory.conn.execute(
            "SELECT id FROM memory.episode WHERE source_namespace=%s", (TARGET,)
        )).fetchone()
        receipt = await memory.forget(Forget(
            memory_ids=[source["id"]], reason="Approved disposable synthetic purge",
        ), "source-forget")
        require(receipt["object_count"] == 4, "source/assertion/checkpoint/job closure required")
    async with service(url) as memory:
        source = await (await memory.conn.execute(
            "SELECT id FROM memory.episode WHERE source_namespace=%s", (SECOND,)
        )).fetchone()
        receipt = await memory.forget(Forget(
            memory_ids=[source["id"]], reason="Second post-backup synthetic deletion",
        ), "second-forget")
        require(receipt["object_count"] == 1, "second purge target mismatch")
    async with service(url) as memory:
        source = await (await memory.conn.execute(
            "SELECT id FROM memory.episode WHERE source_namespace=%s", (DERIVED_PURGED,),
        )).fetchone()
        receipt = await memory.forget(Forget(
            memory_ids=[source["id"]], reason="Purge the complete derivative recovery fixture",
        ), "derived-forget")
        require(receipt["object_count"] == 13, "full derivative closure must contain 13 anchors")
    profile = processing_profile()
    calls = []
    async def extract(data):
        outcome = data.text.rsplit(" ", 1)[1]
        calls.append(outcome)
        if outcome in ("unknown", "failed"):
            raise ProviderFailure("synthetic_failure", unknown=outcome == "unknown")
        return ExtractionResult(model=profile.settings.text_model,
                                input_digest=data.digest(), candidates=[])
    profile.provider.extract = extract
    for _ in range(3):
        result = await run_once(url, OPERATOR, profile=profile)
        require(result["outcome"] in ("failed", "succeeded"), "synthetic call did not finish")
    require(sorted(calls) == ["failed", "succeeded", "unknown"],
            "three synthetic outcomes required")
    with psycopg.connect(admin_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """SELECT p.tenant_id,p.id AS principal_id,s.scope_id,t.access_epoch
               FROM memory.principal p JOIN memory.scope_member s
                 ON s.tenant_id=p.tenant_id AND s.principal_id=p.id
               JOIN memory.tenant t ON t.id=p.tenant_id WHERE p.external_subject=%s""",
            (REVOKED,),
        ).fetchone()
    with scope_access(admin_url, ScopeAccessRequest(
        operation="set", tenant_id=row["tenant_id"], scope_id=row["scope_id"],
        principal_id=row["principal_id"], expected_access_epoch=row["access_epoch"],
        permissions=("read",), no_expiry=True,
    )) as result:
        require(result.changed, "permission reduction must change access epoch")
        epoch = result.access_epoch
    with scope_access(admin_url, ScopeAccessRequest(
        operation="revoke", tenant_id=row["tenant_id"], scope_id=row["scope_id"],
        principal_id=row["principal_id"], expected_access_epoch=epoch,
    )) as result:
        require(result.changed, "source revocation must change permissions")
        epoch = result.access_epoch
    with capture_policy(admin_url, CapturePolicyRequest(
        operation="set", tenant_id=row["tenant_id"], scope_id=row["scope_id"],
        expected_access_epoch=epoch, policy=CapturePolicy(
            enabled=True, source_namespaces=None,
            consent_references=["disposable-recovery-drill-only"], max_content_bytes=10000,
        ),
    )) as result:
        require(result.changed, "latest capture policy must differ from backup")


async def denied(operation, *, code="not_found", status=404):
    try:
        await operation
    except MemoryError as exc:
        require(exc.code == code and exc.status == status, "unexpected denial")
    else:
        raise RuntimeError("operation unexpectedly succeeded")


async def probe_derivatives(admin_url, url, before, latest):
    for table in DERIVATIVE_TABLES:
        require(before["canonical"][table]["count"] > latest["canonical"][table]["count"] > 0,
                f"both purged and retained {table} fixtures required")
    with psycopg.connect(admin_url, row_factory=dict_row) as conn:
        source = conn.execute(
            "SELECT id,scope_id FROM memory.episode WHERE source_namespace=%s",
            (DERIVED_RETAINED,),
        ).fetchone()
        suppressed = conn.execute(
            "SELECT id,content FROM memory.episode WHERE source_namespace=%s", (SUPPRESSED,),
        ).fetchone()
        require(suppressed is not None and suppressed["content"],
                "suppressed payload must remain physically present")
        snapshot_row = conn.execute(
            """SELECT s.checkpoint_id,c.parent_id,c.state,c.run_id,c.branch_id
               FROM memory.working_snapshot s JOIN memory.checkpoint c
               ON c.tenant_id=s.tenant_id AND c.id=s.checkpoint_id WHERE s.scope_id=%s""",
            (source["scope_id"],),
        ).fetchone()
        effect = conn.execute(
            "SELECT id,external_idempotency_key FROM memory.tool_effect WHERE scope_id=%s",
            (source["scope_id"],),
        ).fetchone()
        entities = conn.execute(
            "SELECT id FROM memory.entity WHERE scope_id=%s ORDER BY canonical_label",
            (source["scope_id"],),
        ).fetchall()
        extraction = conn.execute(
            "SELECT id FROM memory_ops.job WHERE scope_id=%s AND kind='extract'",
            (source["scope_id"],),
        ).fetchone()
    async with service(url) as memory:
        await denied(memory.explain(Explain(memory_id=suppressed["id"])))
        review = await Processing(memory).candidates(extraction["id"])
        require([r["disposition"] for r in review["candidates"]]
                == ["published", "quarantined", "quarantined"], "candidate dispositions changed")
        inferred = review["candidates"][0]["assertion_id"]
        adopted = review["candidates"][1]["adopted_assertion_id"]
        require(adopted is not None and review["candidates"][2]["adopted_assertion_id"] is None,
                "quarantine/adoption identity changed")
        for target, epistemic, source_class in (
            (inferred, "inferred", "model_inference"),
            (adopted, "reported", "caller_explicit_adoption"),
        ):
            explained = await memory.explain(Explain(memory_id=target))
            require(explained["epistemic_status"] == epistemic
                    and explained["derivation"]["source_class"] == source_class
                    and explained["derivation"]["status"] == "untrusted",
                    "derivation provenance or trust classification changed")
        recalled = await memory.recall(Recall(
            scope_ids=[source["scope_id"]], purpose="isolated recovery verification",
            query="", retrieval_mode="vector",
            vector_query=VectorQuery(model=processing_profile().settings.embedding_model,
                                     values=[1.0] + [0.0] * 767),
        ))
        require({str(r["memory_id"]) for r in recalled["items"]}
                == {str(source["id"]), str(inferred)}, "recovered vector identities changed")
        graph = await SqlGraph(memory).expand(ExpandGraph(
            scope_ids=[source["scope_id"]], seeds=[entities[0]["id"]],
            relation_types=["depends_on"], direction="both", purpose="isolated recovery",
        ))
        require(len(graph["nodes"]) == 2 and len(graph["edges"]) == 1,
                "recovered graph derivative missing")
        saved_effect = await ToolEffects(memory).get(effect["id"])
        require(saved_effect["status"] == "unknown" and saved_effect["revision"] == 3
                and saved_effect["external_idempotency_key"] == effect["external_idempotency_key"],
                "effect identity or unknown fence changed")
        await denied(Working(memory).get(snapshot_row["checkpoint_id"]),
                     code="checkpoint_invalidated", status=409)
        await denied(Working(memory).events(QueryWorkingEvents(
            scope_id=source["scope_id"], run_id=snapshot_row["run_id"],
            branch_id=snapshot_row["branch_id"],
        )), code="checkpoint_invalidated", status=409)
        await denied(Checkpoints(memory).restore(RestoreCheckpoint(
            checkpoint_id=snapshot_row["checkpoint_id"], target_branch_id=uuid4(),
            harness_id="synthetic-recovery-derivatives", harness_version="1",
        ), "stale-snapshot-restore"), code="checkpoint_invalidated", status=409)
        restored = await Checkpoints(memory).restore(RestoreCheckpoint(
            checkpoint_id=snapshot_row["parent_id"], target_branch_id=uuid4(),
            harness_id="synthetic-recovery-derivatives", harness_version="1",
        ), "retained-typed-state-restore")
        require(restored["state"] == snapshot_row["state"]
                and restored["automatic_reexecution"] is False
                and restored["resume_allowed"] is False,
                "typed state or unknown effect fence changed on restore")
        raise psycopg.Rollback


async def probe_model_fences(url):
    profile = processing_profile()
    async with service(url) as memory:
        for outcome in ("unknown", "succeeded"):
            row = await (await memory.conn.execute(
                """SELECT e.id,e.scope_id,j.id AS job_id FROM memory.episode e
                   JOIN memory_ops.job_input i ON i.tenant_id=e.tenant_id AND i.source_id=e.id
                   JOIN memory_ops.job j ON j.tenant_id=i.tenant_id AND j.id=i.job_id
                   WHERE e.source_namespace=%s""", (MODEL_PREFIX + outcome,),
            )).fetchone()
            request = ProcessMemory(scope_id=row["scope_id"], kind="extract",
                                    source=MemoryReference(memory_id=row["id"]))
            duplicate = await Processing(memory).enqueue(request, "recovery-duplicate-" + outcome)
            require(duplicate["job_id"] == str(row["job_id"]), "semantic identity changed")
            if outcome == "unknown":
                try:
                    await Processing(memory).enqueue(
                        request.model_copy(update={"retry_of": row["job_id"]}), "unknown-retry"
                    )
                except MemoryError as exc:
                    require(exc.code == "job_retry_unknown", "unknown retry fence mismatch")
                else:
                    raise DrillError("unknown call retry was accepted")
        row = await (await memory.conn.execute(
            "SELECT id,scope_id FROM memory.episode WHERE source_namespace=%s", (BUDGET,)
        )).fetchone()
        queued = await Processing(memory).enqueue(ProcessMemory(
            scope_id=row["scope_id"], kind="extract", source=MemoryReference(memory_id=row["id"]),
        ), "recovery-budget-probe")
        claim = await Jobs(memory).claim(profile_digest=profile.digest)
        require(str(claim["job_id"]) == queued["job_id"], "unexpected job during budget probe")
        try:
            await Processing(memory).prepare(
                claim["job_id"], claim["lease_token"], profile, reserve=True
            )
        except MemoryError as exc:
            require(exc.code == "processing_call_limit", "quota fence mismatch")
        else:
            raise DrillError("restored quota was refunded")
        raise psycopg.Rollback


async def recover(admin_url, directory):
    before = json.loads((directory / "before.json").read_text())
    latest = json.loads((directory / "latest.json").read_text())
    receipts, principals = validate_application_evidence(before, latest)
    bundle = RecoveryBundle.model_validate_json(json.dumps(latest["recovery_bundle"]))
    restored = snapshot(admin_url)
    require(restored == before, "old pg_dump/pg_restore canonical or metadata mismatch")
    baseline_check = compare_processing_state(
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(restored["processing_state"])),
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(before["processing_state"])),
    )
    require(baseline_check.processing_state_matches, "restored processing baseline mismatch")
    url = runtime_url(admin_url, create=True)
    await validate_runtime(url)
    targets = [UUID(row["object_id"]) for row in latest["tombstones"]]
    bindings = []
    for receipt in receipts:
        actor = principals[str(receipt.principal_id)]
        async with service(url, actor["external_subject"]) as memory:
            result = await memory.forget(Forget(
                memory_ids=[t.object_id for t in receipt.targets], mode="purge",
                reason="Isolated latest-ledger replay",
            ), f"recovery-{receipt.deletion_id}")
            require(result["object_count"] == receipt.object_count
                    and result["deletion_epoch"] == receipt.deletion_epoch,
                    "replayed closure or epoch mismatch")
            bindings.append({
                "original_receipt": str(receipt.deletion_id),
                "replayed_receipt": result["deletion_id"],
                "deletion_epoch": receipt.deletion_epoch,
            })
    expected = capture_processing_state(admin_url, bundle.reference.tenant_id)
    with TemporaryDirectory(prefix="pgag-isolated-apply-") as temporary:
        expected_file = Path(temporary) / "expected.json"
        bundle_file = Path(temporary) / "bundle.json"
        expected_file.write_text(expected.model_dump_json())
        bundle_file.write_text(bundle.model_dump_json())
        expected_file.chmod(0o600)
        bundle_file.chmod(0o600)
        completed = subprocess.run(
            ["pg-agmemory", "recovery-apply", "apply",
             "--tenant-id", str(bundle.reference.tenant_id), "--bundle", str(bundle_file),
             "--expected", str(expected_file), "--isolated"],
            capture_output=True, text=True, timeout=60,
        )
        require(completed.returncode == 0 and completed.stderr == "",
                "packaged recovery application failed")
        require(json.loads(completed.stdout)["status"] == "applied", "application did not commit")
    applied = capture_processing_state(admin_url, bundle.reference.tenant_id)
    require(applied == bundle.reference, "operational application did not match latest")
    final = snapshot(admin_url)
    for key in ("tenant", "principals", "memberships", "tombstones", "access_events", "canonical",
                "deletions", "deletion_targets", "processing_state", "graph_generation"):
        require(final[key] == latest[key], f"latest {key} reconciliation mismatch")
    generation = final["graph_generation"]
    require(
        before["graph_generation"]["head"]["source_matches"] is True
        and generation["head"]["source_matches"] is False
        and generation["revision"] == 2
        and generation["building"] is None
        and {k: v for k, v in generation["head"].items() if k != "source_matches"}
        == {k: v for k, v in before["graph_generation"]["head"].items()
            if k != "source_matches"}
        and generation["artifact_verified"] is False
        and generation["serving_enabled"] is False,
        "restored generation receipt must remain unchanged, stale and unactivated",
    )
    def normalized(evidence):
        return [r.model_dump(mode="json", exclude={"deletion_id"})
                for r in deletion_history(evidence).records]
    require(normalized(final) == normalized(latest), "receipt-target replay mismatch")
    processing_check = compare_processing_state(
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(final["processing_state"])),
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(latest["processing_state"])),
    )
    require(processing_check.processing_state_matches, "latest operational state must match")
    require(final["deletions"][:len(before["deletions"])] == before["deletions"],
            "baseline receipt identity changed")
    with psycopg.connect(admin_url, row_factory=dict_row) as conn:
        control = conn.execute(
            "SELECT id FROM memory.episode WHERE source_namespace=%s", (CONTROL,)
        ).fetchone()
        target_kinds = conn.execute(
            "SELECT id,kind FROM memory.object WHERE id=ANY(%s) ORDER BY id", (targets,)
        ).fetchall()
    require(control is not None and len(target_kinds) == 20, "fixture/control count mismatch")
    async with service(url) as memory:
        for target in target_kinds:
            await denied(memory.object(target["id"]))
            if target["kind"] == "checkpoint":
                await denied(Checkpoints(memory).restore(RestoreCheckpoint(
                    checkpoint_id=target["id"], target_branch_id=uuid4(),
                    harness_id="synthetic-recovery-drill", harness_version="1",
                ), "deleted-checkpoint-restore"))
            elif target["kind"] == "job":
                await denied(Jobs(memory).get(target["id"]))
            else:
                await denied(memory.explain(Explain(memory_id=target["id"])))
        await memory.explain(Explain(memory_id=control["id"]))
    async with service(url, REVOKED) as memory:
        await denied(memory.object(control["id"]))
        await denied(memory.explain(Explain(memory_id=control["id"])))
    await probe_model_fences(url)
    await probe_derivatives(admin_url, url, before, latest)
    require(snapshot(admin_url)["canonical"] == latest["canonical"],
            "verification unexpectedly modified canonical state")
    require(capture_processing_state(admin_url, bundle.reference.tenant_id) == bundle.reference,
            "rollback probes unexpectedly modified operational state")
    report = {
        "status": "passed",
        "m2_qualified": False,
        "m3_qualified": False,
        "scope": "schema22-derived-memory-operational-state-application-v7",
        "schema_version": 22,
        "source_authority_recovery": "exact_content_only",
        "source_authority_revalidation": "explicit",
        "automatic_source_grant_refresh": False,
        "build_identity": build_identity(),
        "architecture": platform.machine(),
        "database": final["database"],
        "deleted_closure_count": len(targets),
        "retained_object_anchors": final["canonical"]["memory.object"]["count"],
        "tombstones": len(final["tombstones"]),
        "deletion_manifest_targets": len(final["deletion_targets"]),
        "baseline_receipts": len(before["deletions"]),
        "transient_replay_receipts": bindings,
        "original_receipts_restored": len(final["deletions"]),
        "applied_acl_events": len(latest["access_events"]) - len(before["access_events"]),
        "epochs": {
            "before": {k: before["tenant"][0][k] for k in ("access_epoch", "deletion_epoch")},
            "restored": {k: final["tenant"][0][k] for k in ("access_epoch", "deletion_epoch")},
        },
        "revoked_actors_denied": 1,
        "positive_controls_intact": 2,
        "mixed_baseline_modes_preserved": ["purge", "suppress"],
        "new_suppress_replay_supported": False,
        "derivative_tables": {table: {
            "before": before["canonical"][table]["count"],
            "retained": final["canonical"][table]["count"],
        } for table in DERIVATIVE_TABLES},
        "retained_snapshot_epoch_fence": "explicitly-invalidated",
        "typed_checkpoint_restore": "exact-state-with-unknown-effect-fence",
        "quarantine_adoption_and_vector_ids_preserved": True,
        "baseline": before["canonical"],
        "latest": latest["canonical"],
        "restored": final["canonical"],
        "metadata_sha256": {key: digest(latest[key]) for key in
                            ("tenant", "memberships", "tombstones", "deletion_targets",
                             "access_events")},
        "artifacts_sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("old.dump", "latest.dump", "before.json", "latest.json")
        },
        "model_calls": 0,
        "model_call_definition": "external model requests",
        "synthetic_provider_calls": 11,
        "model_call_reservations": final["canonical"]["memory_ops.model_call"]["count"],
        "model_processing_enabled": True,
        "model_call_reconciliation": "exact-unknown-failed-succeeded-reservations",
        "unknown_retry_denied": True,
        "consumed_quota_preserved": True,
        "semantic_job_identity_preserved": True,
        "processing_baseline_matches": baseline_check.processing_state_matches,
        "latest_processing_check": processing_check.model_dump(mode="json"),
        "graph_generation": {
            "recorded_receipts": 1, "ledger_revision": generation["revision"],
            "metadata_unchanged": True, "input_stale": True,
            "artifact_verified": False, "serving_enabled": False,
        },
        "unqualified": ["general-DR", "HA/PITR", "retention-deadlines", "new-suppress-replay",
                        "unbounded-or-arbitrary-history", "new-or-missing-canonical-content",
                        "automatic-service-activation", "graph-projection-data-rebuild"],
    }
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True), flush=True)


async def main():
    require(sys.platform == "linux", "run only through the isolated Linux container helper")
    require(SCHEMA_VERSION == 22, "this bounded smoke is pinned to schema22")
    build_identity()
    operation = sys.argv[1]
    directory = Path(os.environ["PGAG_RECOVERY_DIRECTORY"])
    admin_url = os.environ["PGAG_ADMIN_DATABASE_URL"]
    if operation == "seed":
        await seed(admin_url)
        (directory / "before.json").write_text(canonical(snapshot(admin_url)) + "\n")
    elif operation == "later":
        await later(admin_url)
        latest = snapshot(admin_url)
        validate_application_evidence(json.loads((directory / "before.json").read_text()), latest)
        latest["recovery_bundle"] = export_bundle(
            admin_url, UUID(latest["tenant"][0]["id"])
        ).model_dump(mode="json")
        (directory / "latest.json").write_text(canonical(latest) + "\n")
    elif operation == "recover":
        await recover(admin_url, directory)
    else:
        raise RuntimeError("unsupported drill phase")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except DrillError as exc:
        print(f"Isolated recovery drill failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (psycopg.Error, AdminError, MemoryError, RuntimeValidationError,
            ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        # Never print driver exceptions/DSNs or fixture content into CI logs.
        print(f"Isolated recovery drill failed ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
