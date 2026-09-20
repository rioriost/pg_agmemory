"""Atomic application of authenticated operational rows to an isolated restore."""

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import Field, JsonValue, ValidationError, model_validator

from pg_agmemory.admin import AdminError, admin_connection, admin_failure
from pg_agmemory.capture_policy import POLICY_COLUMNS, CapturePolicy
from pg_agmemory.deletion_history import DeletionHistory, HistoryContract
from pg_agmemory.models import Digest
from pg_agmemory.processing_recovery import (
    TABLES,
    ProcessingRecoverySnapshot,
    RecoveryParser,
    StateFingerprint,
    capture_processing_connection,
    compare_processing_state,
    fingerprint_tables,
)
from pg_agmemory.synthesis_policy import SynthesisPolicy

CONTENT_TABLES = dict.fromkeys((
    "memory.principal", "memory.scope", "memory.object", "memory.episode",
    "memory.assertion", "memory.assertion_revision", "memory.provenance_edge",
    "memory.checkpoint_run", "memory.checkpoint_branch", "memory.checkpoint",
    "memory.checkpoint_reference", "memory.tool_effect", "memory.tool_effect_revision",
    "memory.tool_effect_reference", "memory.entity", "memory.entity_evidence",
    "memory.relation", "memory.relation_revision", "memory.episode_lexical",
    "memory.assertion_lexical", "memory.episode_embedding", "memory.assertion_embedding",
    "memory.assertion_derivation", "memory.working_event", "memory.working_snapshot",
), "")
REPLACE_TABLES = (
    "memory_ops.deletion_target", "memory_ops.deletion_request", "memory_ops.object_tombstone",
    "memory_ops.idempotency", "memory_ops.scope_access_event", "memory_ops.capture_policy_event",
    "memory_ops.synthesis_policy_event", "memory.scope_member", "memory.scope_capture_policy",
    "memory.scope_synthesis_policy", "memory_ops.model_call",
)
ROW_TABLES = (*REPLACE_TABLES, "memory_ops.job")
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_TABLE_ROWS = 10000
Rows = Annotated[list[dict[str, JsonValue]], Field(max_length=MAX_TABLE_ROWS)]
AdminConnection = psycopg.Connection[dict[str, Any]]


class RecoveryBundle(HistoryContract):
    format: Literal["pgag-recovery-apply-v1"] = "pgag-recovery-apply-v1"
    reference: ProcessingRecoverySnapshot
    content: tuple[StateFingerprint, ...]
    rows: dict[str, Rows]
    signature: Digest
    restore_authorized: Literal[False] = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if set(self.rows) != set(ROW_TABLES) or tuple(t.table for t in self.content) != tuple(
            CONTENT_TABLES
        ):
            raise ValueError("Complete fixed recovery tables are required")
        tenant = str(self.reference.tenant_id)
        if any(row.get("tenant_id") != tenant for rows in self.rows.values() for row in rows):
            raise ValueError("Recovery rows must belong to the reference tenant")
        return self


def signature(bundle: RecoveryBundle, secret: bytes) -> str:
    payload = json.dumps(bundle.model_dump(mode="json", exclude={"signature"}),
                         sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > MAX_BUNDLE_BYTES:
        raise AdminError("recovery_bundle_too_large")
    return hmac.new(secret, b"pgag-recovery-apply-v1:" + payload, hashlib.sha256).hexdigest()


def secret_for(conn: AdminConnection, tenant: UUID) -> bytes:
    row = conn.execute(
        "SELECT secret FROM memory_ops.recovery_key WHERE tenant_id=%s", (tenant,),
    ).fetchone()
    if row is None:
        raise AdminError("recovery_key_unavailable")
    return bytes(row["secret"])


def table_rows(conn: AdminConnection, tenant: UUID, table: str) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    size = 0
    with conn.cursor(name="pgag_recovery_rows") as cursor:
        cursor.execute(
            sql.SQL('SELECT to_jsonb(t) AS value FROM {} t WHERE tenant_id=%s '
                    'ORDER BY to_jsonb(t)::text COLLATE "C" LIMIT %s').format(
                sql.Identifier(*table.split(".")),
            ), (tenant, MAX_TABLE_ROWS + 1),
        )
        for row in cursor:
            size += len(json.dumps(row["value"], ensure_ascii=False).encode())
            if len(result) >= MAX_TABLE_ROWS or size > MAX_BUNDLE_BYTES:
                raise AdminError("recovery_bundle_too_large")
            result.append(row["value"])
    return result


def export_bundle(url: str, tenant: UUID) -> RecoveryBundle:
    try:
        with admin_connection(url, tenant) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                reference = capture_processing_connection(conn, tenant)
                secret = secret_for(conn, tenant)
                bundle = RecoveryBundle(
                    reference=reference,
                    content=fingerprint_tables(conn, tenant, secret, CONTENT_TABLES),
                    rows={table: table_rows(conn, tenant, table) for table in ROW_TABLES},
                    signature="0" * 64,
                )
                if any(r["target_manifest_version"] != 1
                       for r in bundle.rows["memory_ops.deletion_request"]):
                    raise AdminError("deletion_history_incomplete")
                return bundle.model_copy(update={"signature": signature(bundle, secret)})
    except psycopg.Error as exc:
        raise admin_failure(exc, False) from None


def verify_monotonic_calls(
    current: list[dict[str, JsonValue]], latest: list[dict[str, JsonValue]],
) -> None:
    by_id = {str(r["job_id"]): r for r in latest}
    for row in current:
        new = by_id.get(str(row["job_id"]))
        immutable = {k: v for k, v in row.items() if k not in ("outcome", "billing_unknown")}
        if (
            new is None or any(new.get(k) != v for k, v in immutable.items())
            or (row["outcome"] != "unknown" and row != new)
            or (new["outcome"] == "unknown" and new["billing_unknown"] is not True)
        ):
            raise AdminError("recovery_call_regression")


def verify_jobs(
    current: list[dict[str, JsonValue]], latest: list[dict[str, JsonValue]],
) -> None:
    by_id = {str(r["id"]): r for r in latest}
    if set(by_id) != {str(r["id"]) for r in current}:
        raise AdminError("recovery_job_identity_mismatch")
    changing = {
        "state", "payload", "attempt", "lease_token", "lease_until", "available_at",
        "result_id", "error_code", "updated_at", "processing_result",
        "captured_access_epoch", "captured_deletion_epoch",
    }
    for row in current:
        new = by_id[str(row["id"])]
        if (
            any(new.get(k) != v for k, v in row.items() if k not in changing)
            or not isinstance(new["attempt"], int) or not isinstance(row["attempt"], int)
            or new["attempt"] < row["attempt"]
            or (row["state"] in ("succeeded", "failed", "cancelled") and new != row)
        ):
            raise AdminError("recovery_job_regression")


def verify_histories(conn: AdminConnection, bundle: RecoveryBundle) -> None:
    tenant = bundle.reference.tenant_id
    epochs: list[int] = []
    for table in ("memory_ops.scope_access_event", "memory_ops.capture_policy_event",
                  "memory_ops.synthesis_policy_event"):
        latest = bundle.rows[table]
        original = {json.dumps(r, sort_keys=True) for r in latest}
        if any(json.dumps(r, sort_keys=True) not in original
               for r in table_rows(conn, tenant, table)):
            raise AdminError("recovery_access_history_regression")
        for row in latest:
            epoch = row["access_epoch"]
            if not isinstance(epoch, int) or isinstance(epoch, bool):
                raise AdminError("recovery_access_history_incomplete")
            epochs.append(epoch)
    if (bundle.reference.access_epoch != len(epochs) + 1
            or sorted(epochs) != list(range(2, len(epochs) + 2))):
        raise AdminError("recovery_access_history_incomplete")

    def normalized(
        requests: list[dict[str, JsonValue]], targets: list[dict[str, JsonValue]],
    ) -> str:
        records = []
        for request in requests:
            records.append({
                "receipt": {k: request[k] for k in (
                    "principal_id", "mode", "state", "object_count", "deletion_epoch",
                )},
                "targets": sorted(
                    (str(t["object_id"]), str(t["scope_id"]), str(t["ordinal"]))
                    for t in targets if t["deletion_id"] == request["id"]
                ),
            })
        return json.dumps(sorted(records, key=lambda r: str(r["receipt"])), sort_keys=True)

    if normalized(table_rows(conn, tenant, "memory_ops.deletion_request"),
                  table_rows(conn, tenant, "memory_ops.deletion_target")) != normalized(
        bundle.rows["memory_ops.deletion_request"], bundle.rows["memory_ops.deletion_target"],
    ):
        raise AdminError("recovery_deletion_semantics_mismatch")
    DeletionHistory.model_validate_json(json.dumps({
        "tenant_id": str(tenant), "access_epoch": bundle.reference.access_epoch,
        "deletion_epoch": bundle.reference.deletion_epoch,
        "records": [{
            "deletion_id": r["id"],
            **{k: r[k] for k in ("principal_id", "mode", "state", "object_count",
                                 "deletion_epoch", "target_manifest_version")},
            "targets": [{k: t[k] for k in ("object_id", "scope_id")}
                        for t in bundle.rows["memory_ops.deletion_target"]
                        if t["deletion_id"] == r["id"]],
        } for r in sorted(bundle.rows["memory_ops.deletion_request"],
                          key=lambda r: int(str(r["deletion_epoch"])))],
    }))


def insert_rows(
    conn: AdminConnection, table: str, rows: list[dict[str, JsonValue]], *, upsert: bool = False,
) -> None:
    columns = [
        row["attname"] for row in conn.execute(
            """SELECT attname FROM pg_attribute
               WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum""",
            (table,),
        ).fetchall()
    ]
    if any(set(row) != set(columns) for row in rows):
        raise AdminError("recovery_row_shape_mismatch")
    query = sql.SQL("INSERT INTO {} SELECT * FROM jsonb_populate_recordset(NULL::{},%s) "
                    "WHERE true").format(
        sql.Identifier(*table.split(".")), sql.Identifier(*table.split(".")),
    )
    if upsert:
        query += sql.SQL(" ON CONFLICT (tenant_id,id) DO UPDATE SET ") + sql.SQL(",").join(
            sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
            for c in columns if c not in ("tenant_id", "id")
        )
    conn.execute(query, (Jsonb(rows),))


def apply_bundle(
    url: str, expected: ProcessingRecoverySnapshot, bundle: RecoveryBundle, *, isolated: bool,
) -> ProcessingRecoverySnapshot:
    if not isolated:
        raise AdminError("recovery_isolation_required")
    expected = ProcessingRecoverySnapshot.model_validate_json(expected.model_dump_json())
    bundle = RecoveryBundle.model_validate_json(bundle.model_dump_json())
    tenant = bundle.reference.tenant_id
    if expected.tenant_id != tenant:
        raise AdminError("processing_recovery_lineage_mismatch")
    commit_attempted = False
    try:
        with admin_connection(url, tenant) as conn:
            with conn.transaction():
                other = conn.execute(
                    """SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                       AND pid<>pg_backend_pid() AND backend_type='client backend' LIMIT 1"""
                ).fetchone()
                if other:
                    raise AdminError("recovery_database_in_use")
                tables = sorted(set(TABLES) | set(CONTENT_TABLES) | {"memory.tenant"})
                conn.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                    sql.SQL(",").join(sql.Identifier(*t.split(".")) for t in tables),
                ))
                current = capture_processing_connection(conn, tenant)
                secret = secret_for(conn, tenant)
                if not hmac.compare_digest(bundle.signature, signature(bundle, secret)):
                    raise AdminError("recovery_bundle_authentication_failed")
                if not compare_processing_state(current, expected).processing_state_matches:
                    raise AdminError("recovery_state_conflict")
                if (
                    current.lineage != bundle.reference.lineage
                    or current.deletion_epoch != bundle.reference.deletion_epoch
                    or current.access_epoch > bundle.reference.access_epoch
                ):
                    raise AdminError("recovery_epoch_or_lineage_mismatch")
                if fingerprint_tables(conn, tenant, secret, CONTENT_TABLES) != bundle.content:
                    raise AdminError("recovery_content_mismatch")
                incoming = {f.table: f for f in bundle.reference.tables}
                if any(f != incoming[f.table] for f in current.tables if f.table not in ROW_TABLES):
                    raise AdminError("recovery_immutable_state_mismatch")
                verify_monotonic_calls(table_rows(conn, tenant, "memory_ops.model_call"),
                                       bundle.rows["memory_ops.model_call"])
                verify_jobs(table_rows(conn, tenant, "memory_ops.job"),
                            bundle.rows["memory_ops.job"])
                verify_histories(conn, bundle)
                if any(r["target_manifest_version"] != 1
                       for r in bundle.rows["memory_ops.deletion_request"]):
                    raise AdminError("deletion_history_incomplete")
                for row in bundle.rows["memory.scope_synthesis_policy"]:
                    SynthesisPolicy.model_validate(row["policy"])
                for row in bundle.rows["memory.scope_capture_policy"]:
                    CapturePolicy.model_validate({k: row[k] for k in POLICY_COLUMNS.split(",")})
                conn.execute("SET LOCAL pgag.recovery_apply='on'")
                for table in REPLACE_TABLES:
                    conn.execute(sql.SQL("DELETE FROM {} WHERE tenant_id=%s").format(
                        sql.Identifier(*table.split(".")),
                    ), (tenant,))
                for table in reversed(REPLACE_TABLES):
                    insert_rows(conn, table, bundle.rows[table])
                insert_rows(conn, "memory_ops.job", bundle.rows["memory_ops.job"], upsert=True)
                conn.execute(
                    "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s",
                    (bundle.reference.access_epoch, tenant),
                )
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                invalid = conn.execute(
                    """SELECT 1 FROM memory_ops.deletion_target t
                       JOIN memory_ops.deletion_request d
                         ON d.tenant_id=t.tenant_id AND d.id=t.deletion_id
                       WHERE t.tenant_id=%s AND t.ordinal>d.object_count LIMIT 1""", (tenant,),
                ).fetchone()
                if invalid:
                    raise AdminError("recovery_manifest_invalid")
                final = capture_processing_connection(conn, tenant)
                if not compare_processing_state(final, bundle.reference).processing_state_matches:
                    raise AdminError("recovery_verification_failed")
                if fingerprint_tables(conn, tenant, secret, CONTENT_TABLES) != bundle.content:
                    raise AdminError("recovery_content_mismatch")
                commit_attempted = True
            return final
    except psycopg.Error as exc:
        raise admin_failure(exc, commit_attempted) from None


def read_file(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AdminError("recovery_file_invalid")
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise AdminError("recovery_bundle_too_large")
    return payload


def main(argv: list[str]) -> None:
    parser = RecoveryParser(prog="pg-agmemory recovery-apply")
    parser.add_argument("operation", choices=("export", "apply"))
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--isolated", action="store_true")
    args = parser.parse_args(argv)
    if (args.operation == "apply") != (args.expected is not None and args.isolated):
        parser.error("apply requires expected state and explicit isolation")
    if args.operation == "export" and (args.expected is not None or args.isolated):
        parser.error("export does not accept apply options")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        if args.operation == "export":
            bundle = export_bundle(url, args.tenant_id)
            payload = bundle.model_dump_json().encode() + b"\n"
            if len(payload) > MAX_BUNDLE_BYTES:
                raise AdminError("recovery_bundle_too_large")
            fd = os.open(args.bundle, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({"status": "exported", "sha256": hashlib.sha256(payload).hexdigest(),
                              "contains_operational_rows": True, "restore_authorized": False}))
        else:
            bundle = RecoveryBundle.model_validate_json(read_file(args.bundle, MAX_BUNDLE_BYTES))
            expected = ProcessingRecoverySnapshot.model_validate_json(
                read_file(args.expected, 32768)
            )
            if bundle.reference.tenant_id != args.tenant_id:
                raise AdminError("processing_recovery_lineage_mismatch")
            result = apply_bundle(url, expected, bundle, isolated=args.isolated)
            print(json.dumps({"status": "applied", "processing_state_matches": True,
                              "access_epoch": result.access_epoch, "restore_authorized": False}))
    except (AdminError, OSError, ValidationError) as exc:
        code = exc.code if isinstance(exc, AdminError) else "recovery_file_invalid"
        unknown = exc.outcome_unknown if isinstance(exc, AdminError) else False
        print(json.dumps({"error": {"code": code, "outcome_unknown": unknown}}), flush=True)
        raise SystemExit(1) from None
