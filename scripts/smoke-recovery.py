"""Disposable schema-14 recovery smoke; not a restore tool for existing databases.

The helper exports committed server metadata, never remembered test deletion IDs.
Replays bounded purge suffixes after an existing deletion baseline and ordered
ACL changes. Model processing stays disabled; call reconciliation is not tested.
"""

import asyncio
import hashlib
import json
import os
import platform
import re
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic import ValidationError

from pg_agmemory.admin import AdminError
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
    Remember,
    RestoreCheckpoint,
)
from pg_agmemory.processing_recovery import (
    ProcessingRecoverySnapshot,
    capture_processing_state,
    compare_processing_state,
)
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

OPERATOR = "synthetic-recovery-operator"
REVOKED = "synthetic-recovery-revoked"
CONTROL = "synthetic-recovery-control"
TARGET = "synthetic-recovery-target"
SECOND = "synthetic-recovery-second"
BASELINE = "synthetic-recovery-baseline"


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
        require(versions == [{"version": n} for n in range(1, 15)], "schema14 required")
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
            "format": "pgag-isolated-purge-drill-v3",
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
        require(evidence["schema_version"] == 14, "schema14 required")
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


async def denied(operation):
    try:
        await operation
    except MemoryError as exc:
        require(exc.code == "not_found" and exc.status == 404, "unexpected denial")
    else:
        raise RuntimeError("deleted or revoked content is still visible")


async def recover(admin_url, directory):
    before = json.loads((directory / "before.json").read_text())
    latest = json.loads((directory / "latest.json").read_text())
    receipts, events, principals = validate_evidence(before, latest)
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
    for event in events:
        request = {
            "operation": event["operation"], "tenant_id": event["tenant_id"],
            "scope_id": event["scope_id"], "principal_id": event["principal_id"],
            "expected_access_epoch": event["access_epoch"] - 1,
        }
        if event["operation"] == "set":
            request.update(permissions=event["permissions"], no_expiry=True)
        with scope_access(admin_url, ScopeAccessRequest.model_validate_json(
            json.dumps(request)
        )) as result:
            require(result.changed and result.access_epoch == event["access_epoch"],
                    "ACL replay failed")
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
    final = snapshot(admin_url)
    validate_evidence(before, final)
    for key in ("tenant", "principals", "memberships", "tombstones", "access_events", "canonical"):
        require(final[key] == latest[key], f"latest {key} reconciliation mismatch")
    def normalized(evidence):
        return [r.model_dump(mode="json", exclude={"deletion_id"})
                for r in deletion_history(evidence).records]
    require(normalized(final) == normalized(latest), "receipt-target replay mismatch")
    processing_check = compare_processing_state(
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(final["processing_state"])),
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(latest["processing_state"])),
    )
    require(not processing_check.processing_state_matches
            and "memory_ops.deletion_request" in processing_check.differences,
            "regenerated receipts must not certify exact operational state")
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
    require(snapshot(admin_url)["canonical"] == latest["canonical"],
            "verification unexpectedly modified canonical state")
    report = {
        "status": "passed",
        "m2_qualified": False,
        "scope": "schema14-multi-purge-processing-comparison-v3",
        "schema_version": 14,
        "build_identity": build_identity(),
        "architecture": platform.machine(),
        "database": final["database"],
        "deleted_closure_count": len(targets),
        "retained_object_anchors": final["canonical"]["memory.object"]["count"],
        "tombstones": len(final["tombstones"]),
        "deletion_manifest_targets": len(final["deletion_targets"]),
        "baseline_receipts": len(before["deletions"]),
        "replayed_receipts": bindings,
        "replayed_acl_events": len(events),
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
        "model_processing_enabled": False,
        "model_call_reconciliation": "unqualified-no-model-calls-or-worker",
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
    require(SCHEMA_VERSION == 14, "this bounded smoke is pinned to schema14")
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
        validate_evidence(json.loads((directory / "before.json").read_text()), latest)
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
