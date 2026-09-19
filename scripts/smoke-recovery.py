"""Disposable schema-14 recovery smoke; not a restore tool for existing databases.

The helper exports committed server metadata, never remembered test deletion IDs.
Only one purge after an empty deletion baseline and one later ACL revoke are
qualified. Model processing stays disabled; model-call reconciliation is not tested.
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
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

OPERATOR = "synthetic-recovery-operator"
REVOKED = "synthetic-recovery-revoked"
CONTROL = "synthetic-recovery-control"
TARGET = "synthetic-recovery-target"


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
        return {
            "format": "pgag-isolated-purge-drill-v1",
            "schema_version": SCHEMA_VERSION,
            "database": database[0],
            "tenant": rows(conn, "SELECT id,access_epoch,deletion_epoch FROM memory.tenant"),
            "principals": rows(conn, "SELECT * FROM memory.principal ORDER BY id"),
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


def validate_evidence(before, latest):
    """Fail closed outside this bounded fixture; no inferred receipt/target mapping."""
    for evidence in (before, latest):
        require(evidence["format"] == "pgag-isolated-purge-drill-v1", "unknown evidence format")
        require(evidence["schema_version"] == 14, "schema14 required")
        require(len(evidence["tenant"]) == 1, "exactly one disposable tenant required")
        for table in ("memory.scope_synthesis_policy", "memory_ops.model_call",
                      "memory.working_snapshot", "memory_ops.extraction_candidate"):
            require(evidence["canonical"][table]["count"] == 0,
                    "model processing must remain disabled")
    old, new = before["tenant"][0], latest["tenant"][0]
    require(old["id"] == new["id"], "tenant lineage mismatch")
    require(not before["tombstones"] and not before["deletions"] and not before["deletion_targets"]
            and old["deletion_epoch"] == 1, "nonempty deletion baseline unsupported")
    require(len(latest["deletions"]) == 1, "exactly one authoritative purge receipt required")
    receipt = latest["deletions"][0]
    require(receipt["tenant_id"] == new["id"] and receipt["mode"] == "purge"
            and receipt["state"] == "active_store_purged"
            and receipt["target_manifest_version"] == 1
            and receipt["deletion_epoch"] == new["deletion_epoch"] == 2,
            "incomplete or unsupported deletion history")
    targets = latest["tombstones"]
    require(0 < len(targets) <= 100 and len(targets) == receipt["object_count"],
            "purge receipt and complete tombstone set must agree")
    require(len({r["object_id"] for r in targets}) == len(targets)
            and all(r["tenant_id"] == new["id"] for r in targets), "invalid tombstone set")
    mapped = latest["deletion_targets"]
    require(len(mapped) == len(targets)
            and all(r["tenant_id"] == new["id"] and r["deletion_id"] == receipt["id"]
                    for r in mapped)
            and {(r["object_id"], r["scope_id"]) for r in mapped}
            == {(r["object_id"], r["scope_id"]) for r in targets},
            "deletion target manifest mismatch")
    require(before["principals"] == latest["principals"], "principal changes unsupported")
    events = latest["access_events"]
    require(events[:len(before["access_events"])] == before["access_events"],
            "ACL history prefix mismatch")
    suffix = events[len(before["access_events"]):]
    require(len(suffix) == 1, "exactly one later ACL event required")
    event = suffix[0]
    require(event["operation"] == "revoke" and event["permissions"] is None
            and event["expires_at"] is None
            and event["tenant_id"] == new["id"]
            and event["access_epoch"] == new["access_epoch"] == old["access_epoch"] + 1,
            "incomplete or unsupported ACL history")
    matching = [m for m in before["memberships"]
                if all(m[k] == event[k] for k in ("tenant_id", "scope_id", "principal_id"))]
    require(len(matching) == 1 and matching[0]["permissions"] == event["previous_permissions"]
            and matching[0]["expires_at"] == event["previous_expires_at"],
            "ACL previous state mismatch")
    require(latest["memberships"] == [m for m in before["memberships"] if m not in matching],
            "latest membership state disagrees with revocation history")
    actor = next((p for p in latest["principals"] if p["id"] == receipt["principal_id"]), None)
    require(actor is not None, "deletion actor unavailable")
    for target in targets:
        require(any(m["principal_id"] == actor["id"] and m["tenant_id"] == target["tenant_id"]
                    and m["scope_id"] == target["scope_id"] and m["permissions"] == ["admin"]
                    and m["expires_at"] is None for m in latest["memberships"]),
                "trusted deletion actor no longer authorized; replay unsupported")
    return receipt, event, actor


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
    for principal, permissions in ((operator, ("admin",)), (reader, ("read",))):
        with scope_access(admin_url, ScopeAccessRequest(
            operation="set", tenant_id=tenant, scope_id=scope, principal_id=principal,
            expected_access_epoch=epoch, permissions=permissions, no_expiry=True,
        )) as result:
            epoch = result.access_epoch
    async with service(url) as memory:
        for namespace in (TARGET, CONTROL):
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
    async with service(url, REVOKED) as memory:
        sources = await (await memory.conn.execute("SELECT id FROM memory.episode")).fetchall()
        require(len(sources) == 2, "reader must initially see both sources")
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
    with psycopg.connect(admin_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """SELECT p.tenant_id,p.id AS principal_id,s.scope_id,t.access_epoch
               FROM memory.principal p JOIN memory.scope_member s
                 ON s.tenant_id=p.tenant_id AND s.principal_id=p.id
               JOIN memory.tenant t ON t.id=p.tenant_id WHERE p.external_subject=%s""",
            (REVOKED,),
        ).fetchone()
    with scope_access(admin_url, ScopeAccessRequest(
        operation="revoke", tenant_id=row["tenant_id"], scope_id=row["scope_id"],
        principal_id=row["principal_id"], expected_access_epoch=row["access_epoch"],
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
    receipt, event, actor = validate_evidence(before, latest)
    restored = snapshot(admin_url)
    require(restored == before, "old pg_dump/pg_restore canonical or metadata mismatch")
    url = runtime_url(admin_url, create=True)
    await validate_runtime(url)
    targets = [UUID(row["object_id"]) for row in latest["tombstones"]]
    # Only metadata read from the independent latest artifact supplies replay targets.
    async with service(url, actor["external_subject"]) as memory:
        result = await memory.forget(Forget(
            memory_ids=targets, mode=receipt["mode"], reason="Isolated latest-ledger replay",
        ), f"recovery-{receipt['id']}")
        require(result["object_count"] == receipt["object_count"], "replayed closure mismatch")
    with scope_access(admin_url, ScopeAccessRequest(
        operation=event["operation"], tenant_id=UUID(event["tenant_id"]),
        scope_id=UUID(event["scope_id"]), principal_id=UUID(event["principal_id"]),
        expected_access_epoch=before["tenant"][0]["access_epoch"],
    )) as result:
        require(result.changed and not result.membership_exists, "revocation replay failed")
    final = snapshot(admin_url)
    validate_evidence(before, final)
    for key in ("tenant", "principals", "memberships", "tombstones", "access_events", "canonical"):
        require(final[key] == latest[key], f"latest {key} reconciliation mismatch")
    with psycopg.connect(admin_url, row_factory=dict_row) as conn:
        control = conn.execute(
            "SELECT id FROM memory.episode WHERE source_namespace=%s", (CONTROL,)
        ).fetchone()
        target_kinds = conn.execute(
            "SELECT id,kind FROM memory.object WHERE id=ANY(%s) ORDER BY id", (targets,)
        ).fetchall()
    require(control is not None and len(target_kinds) == 4, "fixture/control count mismatch")
    async with service(url, actor["external_subject"]) as memory:
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
    revoked = next(p for p in latest["principals"] if p["id"] == event["principal_id"])
    async with service(url, revoked["external_subject"]) as memory:
        await denied(memory.object(control["id"]))
        await denied(memory.explain(Explain(memory_id=control["id"])))
    require(snapshot(admin_url)["canonical"] == latest["canonical"],
            "verification unexpectedly modified canonical state")
    report = {
        "status": "passed",
        "m2_qualified": False,
        "scope": "schema14-single-purge-single-revocation-synthetic-logical-backup",
        "schema_version": 14,
        "build_identity": build_identity(),
        "architecture": platform.machine(),
        "database": final["database"],
        "deleted_closure_count": len(targets),
        "retained_object_anchors": final["canonical"]["memory.object"]["count"],
        "tombstones": len(final["tombstones"]),
        "deletion_manifest_targets": len(final["deletion_targets"]),
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
        "unqualified": ["general-DR", "HA/PITR", "retention-deadlines", "mixed-deletion-modes",
                        "multi-receipt-history", "inferred-extraction", "working-compaction",
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
