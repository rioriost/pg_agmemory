import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal, Never
from uuid import UUID

import psycopg
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, model_validator

from pg_agmemory.admin import (
    MAX_EPOCH,
    Epoch,
    admin_connection,
    admin_failure,
)
from pg_agmemory.admin import AdminError as ScopeAccessError
from pg_agmemory.transactions import CommitOutcomeUnknown, transaction

Permission = Literal["read", "write", "delete", "admin"]
PERMISSIONS: tuple[Permission, ...] = ("read", "write", "delete", "admin")


class ScopeAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: Literal["get", "set", "revoke"]
    tenant_id: UUID
    scope_id: UUID
    principal_id: UUID
    expected_access_epoch: Epoch | None = None
    permissions: tuple[Permission, ...] | None = None
    expires_at: AwareDatetime | None = None
    no_expiry: bool = False

    @model_validator(mode="after")
    def command_arguments(self) -> "ScopeAccessRequest":
        if self.operation == "get":
            if self.expected_access_epoch is not None:
                raise ValueError("get does not accept an expected epoch")
        elif self.expected_access_epoch is None:
            raise ValueError("mutations require an expected epoch")
        if self.operation != "set":
            if self.permissions is not None or self.expires_at is not None or self.no_expiry:
                raise ValueError("only set accepts permissions and expiry")
        else:
            if (
                not self.permissions
                or len(set(self.permissions)) != len(self.permissions)
                or ("admin" in self.permissions and len(self.permissions) != 1)
            ):
                raise ValueError("set requires distinct permissions or admin alone")
            if (self.expires_at is None) == (not self.no_expiry):
                raise ValueError("set requires exactly one expiry choice")
        return self


class ScopeAccessResult(BaseModel):
    operation: Literal["get", "set", "revoke"]
    tenant_id: UUID
    scope_id: UUID
    principal_id: UUID
    access_epoch: int
    changed: bool
    membership_exists: bool
    permissions: list[Permission]
    expires_at: datetime | None
    effective_permissions: list[Permission]
    evaluated_at: datetime


def apply_scope_access(
    conn: psycopg.Connection[dict[str, Any]],
    request: ScopeAccessRequest,
    *,
    allow_source_binding: bool = False,
) -> ScopeAccessResult:
    """Apply within the caller's administrative transaction and tenant barrier."""
    row_lock = " FOR UPDATE" if request.operation != "get" else ""
    target = conn.execute(
        """SELECT t.access_epoch FROM memory.tenant t
           JOIN memory.scope s ON s.tenant_id=t.id AND s.id=%s
           JOIN memory.principal p ON p.tenant_id=t.id AND p.id=%s
           WHERE t.id=%s"""
        + (" FOR UPDATE OF t" if row_lock else ""),
        (request.scope_id, request.principal_id, request.tenant_id),
    ).fetchone()
    if target is None:
        raise ScopeAccessError("not_found")
    epoch = target["access_epoch"]
    identity = (request.tenant_id, request.scope_id, request.principal_id)
    if request.operation == "set" and not allow_source_binding:
        bound = conn.execute(
            """SELECT 1 FROM memory_ops.source_access_state
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            identity,
        ).fetchone()
        if bound is not None:
            raise ScopeAccessError("source_access_managed")
    if request.operation != "get" and epoch != request.expected_access_epoch:
        raise ScopeAccessError("access_epoch_conflict")
    before = conn.execute(
        """SELECT permissions,expires_at FROM memory.scope_member
           WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s"""
        + row_lock,
        identity,
    ).fetchone()
    clock = conn.execute("SELECT clock_timestamp() AS at").fetchone()
    if clock is None:
        raise ScopeAccessError("admin_database_error")
    at = clock["at"]
    configured = [p for p in PERMISSIONS if before and p in before["permissions"]]
    expiry = before["expires_at"] if before else None
    changed = False
    after = before
    if request.operation == "set":
        expires_at = request.expires_at.astimezone(UTC) if request.expires_at else None
        if expires_at is not None and expires_at <= at:
            raise ScopeAccessError("invalid_expiration")
        permissions = [p for p in PERMISSIONS if p in (request.permissions or ())]
        changed = before is None or permissions != configured or expires_at != expiry
        after = {"permissions": permissions, "expires_at": expires_at}
    elif request.operation == "revoke":
        changed, after = before is not None, None
    if changed:
        if epoch == MAX_EPOCH:
            raise ScopeAccessError("access_epoch_exhausted")
        if after is None:
            conn.execute(
                """DELETE FROM memory.scope_member
                   WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                identity,
            )
        else:
            conn.execute(
                """INSERT INTO memory.scope_member
                   (tenant_id,scope_id,principal_id,permissions,expires_at)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,scope_id,principal_id)
                   DO UPDATE SET permissions=excluded.permissions,
                                 expires_at=excluded.expires_at""",
                (*identity, after["permissions"], after["expires_at"]),
            )
        epoch += 1
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s",
            (epoch, request.tenant_id),
        )
        conn.execute(
            """INSERT INTO memory_ops.scope_access_event
               (tenant_id,scope_id,principal_id,access_epoch,operation,
                previous_permissions,previous_expires_at,permissions,expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                *identity,
                epoch,
                request.operation,
                before["permissions"] if before else None,
                expiry,
                after["permissions"] if after else None,
                after["expires_at"] if after else None,
            ),
        )
    configured = [p for p in PERMISSIONS if after and p in after["permissions"]]
    expiry = after["expires_at"] if after else None
    effective = [] if expiry is not None and expiry <= at else configured
    return ScopeAccessResult(
        operation=request.operation,
        tenant_id=request.tenant_id,
        scope_id=request.scope_id,
        principal_id=request.principal_id,
        access_epoch=epoch,
        changed=changed,
        membership_exists=after is not None,
        permissions=configured,
        expires_at=expiry,
        effective_permissions=list(PERMISSIONS) if "admin" in effective else effective,
        evaluated_at=at,
    )


@contextmanager
def scope_access(url: str, request: ScopeAccessRequest) -> Iterator[ScopeAccessResult]:
    commit_attempted = False
    try:
        with admin_connection(url, request.tenant_id) as conn:
            with transaction(conn):
                result = apply_scope_access(conn, request)
                commit_attempted = result.changed
            yield result
    except (psycopg.Error, CommitOutcomeUnknown) as exc:
        raise admin_failure(exc, commit_attempted) from None


class AccessParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_scope_access_arguments")


def main(argv: list[str]) -> None:
    parser = AccessParser(prog="pg-agmemory scope-access")
    parser.add_argument("operation", choices=("get", "set", "revoke"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--scope-id", required=True, type=UUID)
    parser.add_argument("--principal-id", required=True, type=UUID)
    parser.add_argument("--expected-access-epoch", type=int)
    parser.add_argument("--permissions", nargs="+", choices=PERMISSIONS)
    expiry = parser.add_mutually_exclusive_group()
    expiry.add_argument("--expires-at")
    expiry.add_argument("--no-expiry", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = ScopeAccessRequest.model_validate(vars(args))
    except ValidationError:
        parser.error("invalid_scope_access_arguments")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory scope-access: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with scope_access(url, request) as result:
            print(result.model_dump_json(), flush=True)
    except ScopeAccessError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown}}),
            flush=True,
        )
        raise SystemExit(1) from None
