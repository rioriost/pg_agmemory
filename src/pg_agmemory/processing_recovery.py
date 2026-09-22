"""Read-only reconciliation evidence for processing state, never restore authority."""

import argparse
import hashlib
import hmac
import json
import os
import stat
import time
from pathlib import Path
from typing import Annotated, Any, Literal, Never, Self
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import Field, ValidationError, model_validator

from pg_agmemory.admin import AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.deletion_history import HistoryContract
from pg_agmemory.models import Digest

TABLES = {
    "memory.principal": "id",
    "memory.scope": "id",
    "memory.scope_member": "scope_id,principal_id",
    "memory.scope_capture_policy": "scope_id",
    "memory.scope_synthesis_policy": "scope_id",
    "memory.object": "id",
    "memory_ops.scope_access_event": "access_epoch",
    "memory_ops.capture_policy_event": "access_epoch",
    "memory_ops.synthesis_policy_event": "access_epoch",
    "memory_ops.model_call": "job_id",
    "memory_ops.job_identity": "scope_id,principal_id,input_digest",
    "memory_ops.job": "id",
    "memory_ops.job_input": "job_id,source_id",
    "memory_ops.extraction_candidate": "job_id,ordinal",
    "memory_ops.source_event": "scope_id,event_digest",
    "memory_ops.idempotency": "principal_id,operation,key_digest",
    "memory_ops.object_tombstone": "object_id",
    "memory_ops.deletion_request": "id",
    "memory_ops.deletion_target": "deletion_id,object_id",
    "memory.assertion_derivation": "assertion_id,revision",
    "memory.working_snapshot": "checkpoint_id",
    "memory_ops.graph_generation": "id",
    "memory_ops.graph_generation_state": "tenant_id",
    "memory_ops.age_projection": "tenant_id",
}
MAX_ROWS = 1000000
MAX_BYTES = 256 * 1024 * 1024
MAX_SECONDS = 30
MAX_FILE_BYTES = 32768


class StateFingerprint(HistoryContract):
    table: str
    rows: Annotated[int, Field(ge=0, le=MAX_ROWS)]
    digest: Digest


class ProcessingRecoverySnapshot(HistoryContract):
    format: Literal["pgag-processing-recovery-v1"] = "pgag-processing-recovery-v1"
    schema_version: Literal[20] = 20
    tenant_id: UUID
    lineage: Digest
    access_epoch: Epoch
    deletion_epoch: Epoch
    tables: tuple[StateFingerprint, ...]
    restore_authorized: Literal[False] = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if tuple(row.table for row in self.tables) != tuple(TABLES):
            raise ValueError("Complete ordered processing state is required")
        return self


class ProcessingRecoveryCheck(HistoryContract):
    processing_state_matches: bool
    differences: tuple[str, ...]
    restore_authorized: Literal[False] = False


def fingerprint_tables(
    conn: psycopg.Connection[dict[str, Any]], tenant_id: UUID, secret: bytes,
    tables: dict[str, str],
    *,
    filters: dict[str, sql.Composable] | None = None,
) -> tuple[StateFingerprint, ...]:
    if filters is not None and not filters.keys() <= tables.keys():
        raise ValueError("Fingerprint filters must reference declared tables")
    started = time.monotonic()
    total_bytes = 0
    fingerprints = []
    for table, keys in tables.items():
        if time.monotonic() - started > MAX_SECONDS:
            raise AdminError("processing_recovery_timeout")
        digest = hmac.new(secret, ("processing-recovery-v1:" + table).encode(), hashlib.sha256)
        order = (sql.SQL(",").join(map(sql.Identifier, keys.split(","))) if keys
                 else sql.SQL('to_jsonb(t)::text COLLATE "C"'))
        predicate = (
            sql.SQL(" AND ({})").format(filters[table])
            if filters is not None and table in filters else sql.SQL("")
        )
        query = sql.SQL("SELECT to_jsonb(t) AS value FROM {} t WHERE tenant_id=%s{} "
                        "ORDER BY {} LIMIT %s").format(
            sql.Identifier(*table.split(".")), predicate, order,
        )
        count = 0
        with conn.cursor(name="pgag_processing_recovery") as cursor:
            cursor.execute(query, (tenant_id, MAX_ROWS + 1))
            for row in cursor:
                count += 1
                payload = json.dumps(
                    row["value"], sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
                total_bytes += len(payload)
                if count > MAX_ROWS or total_bytes > MAX_BYTES:
                    raise AdminError("processing_recovery_limit")
                if time.monotonic() - started > MAX_SECONDS:
                    raise AdminError("processing_recovery_timeout")
                digest.update(len(payload).to_bytes(8, "big"))
                digest.update(payload)
        fingerprints.append(StateFingerprint(table=table, rows=count, digest=digest.hexdigest()))
    if time.monotonic() - started > MAX_SECONDS:
        raise AdminError("processing_recovery_timeout")
    return tuple(fingerprints)


def capture_processing_connection(
    conn: psycopg.Connection[dict[str, Any]], tenant_id: UUID,
) -> ProcessingRecoverySnapshot:
    if SCHEMA_VERSION != 20:
        raise AdminError("schema_version_mismatch")
    conn.execute("SET LOCAL timezone='UTC'")
    tenant = conn.execute(
        "SELECT dedup_secret,access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s",
        (tenant_id,),
    ).fetchone()
    if tenant is None:
        raise AdminError("tenant_not_found")
    secret = bytes(tenant["dedup_secret"])
    return ProcessingRecoverySnapshot(
        tenant_id=tenant_id,
        lineage=hmac.new(
            secret, ("processing-recovery-lineage-v1:" + str(tenant_id)).encode(), hashlib.sha256,
        ).hexdigest(),
        access_epoch=tenant["access_epoch"], deletion_epoch=tenant["deletion_epoch"],
        tables=fingerprint_tables(conn, tenant_id, secret, TABLES),
    )


def capture_processing_state(url: str, tenant_id: UUID) -> ProcessingRecoverySnapshot:
    try:
        with admin_connection(url, tenant_id) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return capture_processing_connection(conn, tenant_id)
    except psycopg.Error as exc:
        raise admin_failure(exc, False) from None


def compare_processing_state(
    current: ProcessingRecoverySnapshot, reference: ProcessingRecoverySnapshot,
) -> ProcessingRecoveryCheck:
    current = ProcessingRecoverySnapshot.model_validate_json(current.model_dump_json())
    reference = ProcessingRecoverySnapshot.model_validate_json(reference.model_dump_json())
    if current.tenant_id != reference.tenant_id or current.lineage != reference.lineage:
        raise AdminError("processing_recovery_lineage_mismatch")
    differences = [
        field for field in ("access_epoch", "deletion_epoch")
        if getattr(current, field) != getattr(reference, field)
    ]
    differences.extend(a.table for a, b in zip(current.tables, reference.tables, strict=True)
                       if a != b)
    return ProcessingRecoveryCheck(
        processing_state_matches=not differences, differences=tuple(differences),
    )


class RecoveryParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_processing_recovery_arguments")


def main(argv: list[str]) -> None:
    parser = RecoveryParser(prog="pg-agmemory processing-recovery")
    parser.add_argument("operation", choices=("export", "check"))
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        reference = None
        if args.operation == "check":
            fd = os.open(args.file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise AdminError("processing_recovery_file_invalid")
                payload = stream.read(MAX_FILE_BYTES + 1)
            if len(payload) > MAX_FILE_BYTES:
                raise AdminError("processing_recovery_file_too_large")
            reference = ProcessingRecoverySnapshot.model_validate_json(payload)
        current = capture_processing_state(url, args.tenant_id)
        if reference is not None:
            result = compare_processing_state(current, reference)
            print(result.model_dump_json(), flush=True)
            if not result.processing_state_matches:
                raise SystemExit(1)
        else:
            payload = current.model_dump_json(indent=2).encode() + b"\n"
            if len(payload) > MAX_FILE_BYTES:
                raise AdminError("processing_recovery_file_too_large")
            fd = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({
                "status": "exported", "sha256": hashlib.sha256(payload).hexdigest(),
                "restore_authorized": False,
            }), flush=True)
    except (AdminError, OSError, ValidationError) as exc:
        code = (exc.code if isinstance(exc, AdminError) else
                "processing_recovery_file_invalid" if isinstance(exc, ValidationError)
                else "processing_recovery_io_failed")
        print(json.dumps({"error": {"code": code, "outcome_unknown": False}}), flush=True)
        raise SystemExit(1) from None
