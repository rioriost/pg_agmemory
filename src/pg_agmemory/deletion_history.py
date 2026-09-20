"""Administrative, content-free deletion history export; not a restore authorization."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Literal, Never, Self
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pg_agmemory.admin import AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.database import SCHEMA_VERSION

MAX_RECEIPTS = 10000
MAX_TARGETS = 100000
MAX_EXPORT_BYTES = 16 * 1024 * 1024


class HistoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DeletionTarget(HistoryContract):
    object_id: UUID
    scope_id: UUID


class DeletionRecord(HistoryContract):
    deletion_id: UUID
    principal_id: UUID
    mode: Literal["suppress", "purge"]
    state: Literal["blocked_for_reads", "active_store_purged"]
    deletion_epoch: Epoch
    target_manifest_version: Literal[1] = 1
    object_count: Annotated[int, Field(ge=1, le=10100)]
    targets: Annotated[tuple[DeletionTarget, ...], Field(min_length=1, max_length=10100)]

    @model_validator(mode="after")
    def complete(self) -> Self:
        if (
            self.object_count != len(self.targets)
            or len({target.object_id for target in self.targets}) != len(self.targets)
            or (self.mode == "purge") != (self.state == "active_store_purged")
        ):
            raise ValueError("Invalid deletion target manifest")
        return self


class DeletionHistory(HistoryContract):
    format: Literal["pgag-deletion-history-v1"] = "pgag-deletion-history-v1"
    schema_version: Literal[14] = 14
    tenant_id: UUID
    access_epoch: Epoch
    deletion_epoch: Epoch
    records: Annotated[tuple[DeletionRecord, ...], Field(max_length=MAX_RECEIPTS)]
    restore_authorized: Literal[False] = False
    includes_acl_policy_and_call_accounting: Literal[False] = False

    @model_validator(mode="after")
    def contiguous(self) -> Self:
        if (
            self.deletion_epoch != len(self.records) + 1
            or tuple(row.deletion_epoch for row in self.records)
            != tuple(range(2, self.deletion_epoch + 1))
            or len({row.deletion_id for row in self.records}) != len(self.records)
            or sum(len(row.targets) for row in self.records) > MAX_TARGETS
        ):
            raise ValueError("Incomplete or oversized deletion history")
        return self

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


def purge_replay_suffix(
    before: DeletionHistory, latest: DeletionHistory,
) -> tuple[DeletionRecord, ...]:
    """Plan only bounded public-API purges; never authorize a database restart."""
    before = DeletionHistory.model_validate_json(before.model_dump_json())
    latest = DeletionHistory.model_validate_json(latest.model_dump_json())
    if (
        before.tenant_id != latest.tenant_id
        or latest.access_epoch < before.access_epoch
        or latest.deletion_epoch < before.deletion_epoch
        or latest.records[:len(before.records)] != before.records
    ):
        raise AdminError("recovery_history_mismatch")
    seen: set[UUID] = set()
    for record in latest.records:
        targets = {target.object_id for target in record.targets}
        if record.mode != "purge" or seen.intersection(targets):
            raise AdminError("recovery_history_unsupported")
        seen.update(targets)
    suffix = latest.records[len(before.records):]
    if any(record.object_count > 100 for record in suffix):
        raise AdminError("recovery_target_limit")
    return suffix


def export_deletions(url: str, tenant_id: UUID) -> DeletionHistory:
    if SCHEMA_VERSION != 14:
        raise AdminError("schema_version_mismatch")
    try:
        with admin_connection(url, tenant_id) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                tenant = conn.execute(
                    "SELECT access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s",
                    (tenant_id,),
                ).fetchone()
                if tenant is None:
                    raise AdminError("tenant_not_found")
                receipts = conn.execute(
                    """SELECT id,principal_id,mode,state,object_count,deletion_epoch,
                              target_manifest_version
                       FROM memory_ops.deletion_request WHERE tenant_id=%s
                       ORDER BY deletion_epoch LIMIT %s""",
                    (tenant_id, MAX_RECEIPTS + 1),
                ).fetchall()
                if len(receipts) > MAX_RECEIPTS:
                    raise AdminError("deletion_history_too_large")
                if any(row["target_manifest_version"] != 1 for row in receipts):
                    raise AdminError("deletion_history_incomplete")
                targets = conn.execute(
                    """SELECT deletion_id,object_id,scope_id FROM memory_ops.deletion_target
                       WHERE tenant_id=%s ORDER BY deletion_id,object_id LIMIT %s""",
                    (tenant_id, MAX_TARGETS + 1),
                ).fetchall()
                if len(targets) > MAX_TARGETS:
                    raise AdminError("deletion_history_too_large")
                orphan = conn.execute(
                    """SELECT 1 FROM memory_ops.object_tombstone t WHERE t.tenant_id=%s
                       AND NOT EXISTS (SELECT 1 FROM memory_ops.deletion_target d
                           WHERE d.tenant_id=t.tenant_id AND d.object_id=t.object_id
                           AND d.scope_id=t.scope_id) LIMIT 1""", (tenant_id,),
                ).fetchone()
                if orphan:
                    raise AdminError("deletion_history_incomplete")
                grouped: dict[UUID, list[DeletionTarget]] = {row["id"]: [] for row in receipts}
                for target in targets:
                    if target["deletion_id"] not in grouped:
                        raise AdminError("deletion_history_incomplete")
                    grouped[target["deletion_id"]].append(DeletionTarget(
                        object_id=target["object_id"], scope_id=target["scope_id"],
                    ))
                try:
                    return DeletionHistory(
                        tenant_id=tenant_id, **tenant,
                        records=tuple(DeletionRecord(
                            deletion_id=row["id"], principal_id=row["principal_id"],
                            mode=row["mode"], state=row["state"], object_count=row["object_count"],
                            deletion_epoch=row["deletion_epoch"],
                            targets=tuple(grouped[row["id"]]),
                        ) for row in receipts),
                    )
                except ValidationError:
                    raise AdminError("deletion_history_incomplete") from None
    except psycopg.Error as exc:
        raise admin_failure(exc, False) from None


class HistoryParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_deletion_history_arguments")


def main(argv: list[str]) -> None:
    parser = HistoryParser(prog="pg-agmemory deletion-history")
    parser.add_argument("operation", choices=("export",))
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory deletion-history: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        history = export_deletions(url, args.tenant_id)
        payload = history.model_dump_json(indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_EXPORT_BYTES:
            raise AdminError("deletion_history_too_large")
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({
            "status": "exported", "sha256": hashlib.sha256(payload).hexdigest(),
            "history_digest": history.digest(),
            "receipt_count": len(history.records), "restore_authorized": False,
            "includes_acl_policy_and_call_accounting": False,
        }), flush=True)
    except (AdminError, OSError) as exc:
        code = exc.code if isinstance(exc, AdminError) else "deletion_history_output_failed"
        print(json.dumps({"error": {"code": code, "outcome_unknown": False}}), flush=True)
        raise SystemExit(1) from None
