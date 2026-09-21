"""Disposable schema-17 recovery smoke; not a restore tool for existing databases.

The helper exports committed server metadata, never remembered test deletion IDs.
It applies exact operational state after bounded deletion replay and exercises
three synthetic call outcomes, unknown-call fences and consumed quota.
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
from pg_agmemory.database import SCHEMA_VERSION, RuntimeValidationError, migrate, validate_runtime
from pg_agmemory.deletion_history import DeletionHistory, purge_replay_suffix
from pg_agmemory.jobs import Jobs
from pg_agmemory.models import (
    CheckpointState,
    CreateCheckpoint,
    EnqueueJob,
    Evidence,
    Explain,
    Forget,
    MemoryReference,
    Observe,
    ProcessMemory,
    Remember,
    RestoreCheckpoint,
)
from pg_agmemory.processing import Processing
from pg_agmemory.processing_recovery import (
    ProcessingRecoverySnapshot,
    capture_processing_state,
    compare_processing_state,
)
from pg_agmemory.providers import ExtractionResult, ProviderFailure, ProviderSettings
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
        require(versions == [{"version": n} for n in range(1, 18)], "schema17 required")
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
                      "model_call", "source_event"):
            counts[f"memory_ops.{table}"] = fingerprint(conn, "memory_ops", table)
        result = {
            "format": "pgag-isolated-purge-drill-v4",
            "schema_version": SCHEMA_VERSION,
            "database": database[0],
            "tenant": rows(conn, "SELECT id,access_epoch,deletion_epoch FROM memory.tenant"),
            "principals": rows(conn, "SELECT * FROM memory.principal ORDER BY id"),
            "objects": rows(conn, """SELECT tenant_id,id,scope_id,kind
                                    FROM memory.object ORDER BY id"""),
            "memberships": rows(conn, "SELECT * FROM memory.scope_member ORDER BY principal_id"),
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
        require(evidence["schema_version"] == 17, "schema17 required")
        require(len(evidence["tenant"]) == 1, "exactly one disposable tenant required")
        for table in ("memory.scope_synthesis_policy", "memory.scope_capture_policy",
                      "memory_ops.model_call",
                      "memory.working_snapshot", "memory_ops.extraction_candidate"):
            require(evidence["canonical"][table]["count"] == 0,
                    "model processing must remain disabled")
        histories.append(deletion_history(evidence))
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


def validate_application_evidence(before, latest):
    require(before["format"] == latest["format"] == "pgag-isolated-purge-drill-v4",
            "application evidence v4 required")
    require(before["schema_version"] == latest["schema_version"] == 17, "schema17 required")
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
        await Jobs(memory).enqueue(
            EnqueueJob(kind="structured_remember", memory=intent), "recovery-pending-job"
        )
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


async def denied(operation):
    try:
        await operation
    except MemoryError as exc:
        require(exc.code == "not_found" and exc.status == 404, "unexpected denial")
    else:
        raise RuntimeError("deleted or revoked content is still visible")


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
                "deletions", "deletion_targets", "processing_state"):
        require(final[key] == latest[key], f"latest {key} reconciliation mismatch")
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
    require(control is not None and len(target_kinds) == 6, "fixture/control count mismatch")
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
    require(snapshot(admin_url)["canonical"] == latest["canonical"],
            "verification unexpectedly modified canonical state")
    require(capture_processing_state(admin_url, bundle.reference.tenant_id) == bundle.reference,
            "rollback probes unexpectedly modified operational state")
    report = {
        "status": "passed",
        "m2_qualified": False,
        "scope": "schema17-exact-operational-state-application",
        "schema_version": 17,
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
        "positive_controls_intact": 1,
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
        "synthetic_provider_calls": 3,
        "model_call_reservations": final["canonical"]["memory_ops.model_call"]["count"],
        "model_processing_enabled": True,
        "model_call_reconciliation": "exact-unknown-failed-succeeded-reservations",
        "unknown_retry_denied": True,
        "consumed_quota_preserved": True,
        "semantic_job_identity_preserved": True,
        "processing_baseline_matches": baseline_check.processing_state_matches,
        "latest_processing_check": processing_check.model_dump(mode="json"),
        "unqualified": ["general-DR", "HA/PITR", "retention-deadlines", "mixed-deletion-modes",
                        "unbounded-or-arbitrary-history", "inferred-extraction",
                        "working-compaction",
                        "graph-derivatives", "tool-effects", "vector-embeddings"],
    }
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True), flush=True)


async def main():
    require(sys.platform == "linux", "run only through the isolated Linux container helper")
    require(SCHEMA_VERSION == 17, "this bounded smoke is pinned to schema17")
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
