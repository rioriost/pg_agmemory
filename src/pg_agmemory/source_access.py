"""Trusted administrative source notices; no upstream authorization is performed here."""

import argparse
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Never
from uuid import UUID

import psycopg
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pg_agmemory.admin import MAX_EPOCH, AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.human_review import load_json
from pg_agmemory.models import ShortText
from pg_agmemory.scope_access import Permission, ScopeAccessRequest, apply_scope_access

MAX_SOURCE_LEASE_SECONDS = 300
MAX_NOTICE_BYTES = 32768
SourceReason = Literal[
    "bound", "authorized", "revoked", "unavailable", "deleted", "sequence_gap", "invalid_lease"
]


class SourceIdentity(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", str_strip_whitespace=True
    )
    source_system: ShortText
    dataset_id: ShortText
    source_subject: ShortText

    @field_validator("source_system", "dataset_id", "source_subject")
    @classmethod
    def database_text(cls, value: str) -> str:
        value.encode("utf-8")
        if "\x00" in value:
            raise ValueError("Invalid source identity")
        return value


class SourceNotice(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", str_strip_whitespace=True
    )
    format: Literal["pgag-source-access-notice-v1"] = "pgag-source-access-notice-v1"
    source: SourceIdentity
    sequence: Annotated[int, Field(ge=1, le=MAX_EPOCH, strict=True)]
    decision: Literal["allow", "deny"]
    reason: Literal["authorized", "revoked", "unavailable", "deleted"]
    acl_version: ShortText | None = None
    verified_at: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None

    @field_validator("acl_version")
    @classmethod
    def database_text(cls, value: str | None) -> str | None:
        if value is not None:
            value.encode("utf-8")
            if "\x00" in value:
                raise ValueError("Invalid ACL version")
        return value

    @field_validator("verified_at", "valid_until")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        try:
            return value.astimezone(UTC)
        except (OverflowError, ValueError):
            raise ValueError("Invalid source notice timestamp") from None

    @model_validator(mode="after")
    def authorization_arguments(self) -> "SourceNotice":
        if self.decision == "allow":
            if (
                self.reason != "authorized"
                or self.acl_version is None
                or self.verified_at is None
                or self.valid_until is None
            ):
                raise ValueError("Allow requires authorization and a complete lease")
        elif (
            self.reason == "authorized"
            or self.verified_at is not None
            or self.valid_until is not None
        ):
            raise ValueError("Deny cannot declare authorization or a lease")
        return self


class SourceAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    operation: Literal["get", "bind", "apply"]
    tenant_id: UUID
    scope_id: UUID
    principal_id: UUID
    expected_access_epoch: Epoch | None = None
    source: SourceIdentity | None = None
    notice: SourceNotice | None = None

    @model_validator(mode="after")
    def command_arguments(self) -> "SourceAccessRequest":
        if self.operation == "get":
            if any(
                value is not None
                for value in (self.expected_access_epoch, self.source, self.notice)
            ):
                raise ValueError("Get does not accept mutation arguments")
        elif self.expected_access_epoch is None:
            raise ValueError("Mutations require an expected epoch")
        elif self.operation == "bind":
            if self.source is None or self.notice is not None:
                raise ValueError("Bind requires only a source identity")
        elif self.notice is None or self.source is not None:
            raise ValueError("Apply requires only a source notice")
        return self


class SourceAccessResult(BaseModel):
    operation: Literal["get", "bind", "apply"]
    tenant_id: UUID
    scope_id: UUID
    principal_id: UUID
    source: SourceIdentity
    sequence: Annotated[int, Field(ge=0, le=MAX_EPOCH)]
    decision: Literal["allow", "deny"]
    reason: SourceReason
    changed: bool
    replayed: bool
    access_epoch: int
    permissions: list[Permission]
    effective_permissions: list[Permission]
    expires_at: datetime | None
    evaluated_at: datetime
    source_authorization_verified: Literal[False] = False


def _scope_request(
    request: SourceAccessRequest,
    operation: Literal["get", "set", "revoke"],
    expires_at: datetime | None = None,
) -> ScopeAccessRequest:
    return ScopeAccessRequest(
        operation=operation,
        tenant_id=request.tenant_id,
        scope_id=request.scope_id,
        principal_id=request.principal_id,
        expected_access_epoch=request.expected_access_epoch if operation != "get" else None,
        permissions=("read",) if operation == "set" else None,
        expires_at=expires_at,
    )


def _result(
    conn: psycopg.Connection[dict[str, Any]],
    request: SourceAccessRequest,
    state: dict[str, Any],
    *,
    changed: bool = False,
    replayed: bool = False,
) -> SourceAccessResult:
    # Never reconstruct current grants from a previously accepted source notice.
    access = apply_scope_access(conn, _scope_request(request, "get"))
    return SourceAccessResult(
        operation=request.operation,
        tenant_id=request.tenant_id,
        scope_id=request.scope_id,
        principal_id=request.principal_id,
        source=SourceIdentity(
            source_system=state["source_system"],
            dataset_id=state["dataset_id"],
            source_subject=state["source_subject"],
        ),
        sequence=state["sequence"],
        decision=state["decision"],
        reason=state["reason"],
        changed=changed,
        replayed=replayed,
        access_epoch=access.access_epoch,
        permissions=access.permissions,
        effective_permissions=access.effective_permissions,
        expires_at=access.expires_at,
        evaluated_at=access.evaluated_at,
    )


def _record_event(
    conn: psycopg.Connection[dict[str, Any]],
    request: SourceAccessRequest,
    state: dict[str, Any],
    access_epoch: int,
) -> None:
    conn.execute(
        """INSERT INTO memory_ops.source_access_event
           (tenant_id,scope_id,principal_id,sequence,notice_digest,decision,reason,
            acl_version,verified_at,valid_until,access_epoch)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            request.tenant_id, request.scope_id, request.principal_id,
            state["sequence"], state["notice_digest"], state["decision"], state["reason"],
            state["acl_version"], state["verified_at"], state["valid_until"], access_epoch,
        ),
    )


def _apply_source_access(
    conn: psycopg.Connection[dict[str, Any]], request: SourceAccessRequest
) -> SourceAccessResult:
    mutation = request.operation != "get"
    target = conn.execute(
        """SELECT t.access_epoch FROM memory.tenant t
           JOIN memory.scope s ON s.tenant_id=t.id AND s.id=%s
           JOIN memory.principal p ON p.tenant_id=t.id AND p.id=%s
           WHERE t.id=%s"""
        + (" FOR UPDATE OF t" if mutation else ""),
        (request.scope_id, request.principal_id, request.tenant_id),
    ).fetchone()
    if target is None:
        raise AdminError("not_found")
    identity = (request.tenant_id, request.scope_id, request.principal_id)
    state = conn.execute(
        """SELECT source_system,dataset_id,source_subject,sequence,notice_digest,
                  decision,reason,acl_version,verified_at,valid_until
           FROM memory_ops.source_access_state
           WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s"""
        + (" FOR UPDATE" if mutation else ""),
        identity,
    ).fetchone()
    if request.operation == "get":
        if state is None:
            raise AdminError("not_found")
        return _result(conn, request, state)
    if request.operation == "bind":
        assert request.source is not None
        source = request.source.model_dump()
        if state is not None:
            if any(state[key] != value for key, value in source.items()):
                raise AdminError("source_binding_conflict")
            return _result(conn, request, state, replayed=True)
        if target["access_epoch"] != request.expected_access_epoch:
            raise AdminError("access_epoch_conflict")
        membership = conn.execute(
            """SELECT permissions FROM memory.scope_member
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s FOR UPDATE""",
            identity,
        ).fetchone()
        if membership is not None and any(p != "read" for p in membership["permissions"]):
            raise AdminError("source_binding_permission_conflict")
        access = apply_scope_access(
            conn, _scope_request(request, "revoke"), allow_source_binding=True
        )
        state = {
            **source,
            "sequence": 0,
            "notice_digest": None,
            "decision": "deny",
            "reason": "bound",
            "acl_version": None,
            "verified_at": None,
            "valid_until": None,
        }
        conn.execute(
            """INSERT INTO memory_ops.source_access_state
               (tenant_id,scope_id,principal_id,source_system,dataset_id,source_subject,
                sequence,decision,reason)
               VALUES (%s,%s,%s,%s,%s,%s,0,'deny','bound')""",
            (*identity, request.source.source_system,
             request.source.dataset_id, request.source.source_subject),
        )
        _record_event(conn, request, state, access.access_epoch)
        return _result(conn, request, state, changed=True)
    if state is None:
        raise AdminError("not_found")
    notice = request.notice
    assert notice is not None
    if any(state[key] != value for key, value in notice.source.model_dump().items()):
        raise AdminError("source_binding_conflict")
    digest = hashlib.sha256(
        json.dumps(notice.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if notice.sequence == state["sequence"]:
        if digest != state["notice_digest"]:
            raise AdminError("source_notice_conflict")
        return _result(conn, request, state, replayed=True)
    if notice.sequence < state["sequence"]:
        raise AdminError("source_sequence_conflict")
    if target["access_epoch"] != request.expected_access_epoch:
        raise AdminError("access_epoch_conflict")
    decision = notice.decision
    reason: SourceReason = notice.reason
    if state["reason"] == "deleted":
        if decision == "allow":
            raise AdminError("source_deleted")
        reason = "deleted"
    elif notice.reason == "deleted":
        decision, reason = "deny", "deleted"
    elif notice.sequence != state["sequence"] + 1:
        decision, reason = "deny", "sequence_gap"
    elif decision == "allow":
        clock = conn.execute("SELECT clock_timestamp() AS at").fetchone()
        if clock is None:
            raise AdminError("admin_database_error")
        assert notice.verified_at is not None and notice.valid_until is not None
        if not (
            notice.verified_at <= clock["at"] < notice.valid_until
            and notice.valid_until - notice.verified_at
            <= timedelta(seconds=MAX_SOURCE_LEASE_SECONDS)
        ):
            decision, reason = "deny", "invalid_lease"
    if decision == "allow":
        try:
            access = apply_scope_access(
                conn, _scope_request(request, "set", notice.valid_until),
                allow_source_binding=True,
            )
        except AdminError as exc:
            if exc.code != "invalid_expiration":
                raise
            # The bounded lease may expire between its validation and the grant clock.
            decision, reason = "deny", "invalid_lease"
    if decision == "deny":
        access = apply_scope_access(
            conn, _scope_request(request, "revoke"), allow_source_binding=True
        )
    state = {
        **state,
        "sequence": notice.sequence,
        "notice_digest": digest,
        "decision": decision,
        "reason": reason,
        "acl_version": notice.acl_version,
        "verified_at": notice.verified_at,
        "valid_until": notice.valid_until,
    }
    conn.execute(
        """UPDATE memory_ops.source_access_state
           SET sequence=%s,notice_digest=%s,decision=%s,reason=%s,
               acl_version=%s,verified_at=%s,valid_until=%s
           WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
        (
            notice.sequence, digest, decision, reason,
            notice.acl_version, notice.verified_at, notice.valid_until, *identity,
        ),
    )
    _record_event(conn, request, state, access.access_epoch)
    return _result(conn, request, state, changed=True)


@contextmanager
def source_access(url: str, request: SourceAccessRequest) -> Iterator[SourceAccessResult]:
    try:
        request = SourceAccessRequest.model_validate(request)
    except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
        raise AdminError("invalid_source_access_request") from None
    commit_attempted = False
    try:
        with admin_connection(url, request.tenant_id) as conn:
            with conn.transaction():
                result = _apply_source_access(conn, request)
                commit_attempted = result.changed
            yield result
    except psycopg.Error as exc:
        raise admin_failure(exc, commit_attempted) from None


class SourceAccessParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_source_access_arguments")


def main(argv: list[str]) -> None:
    parser = SourceAccessParser(
        prog="pg-agmemory source-access",
        description="Apply trusted ADMIN notices after upstream authentication.",
        epilog=(
            "Applied sequence_gap and invalid_lease denials exit 1 with a JSON result; "
            "explicit denials exit 0. Deleted means access denied, not Native data purged. "
            "No automatic retries or source authorization verification."
        ),
    )
    parser.add_argument("operation", choices=("get", "bind", "apply"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--scope-id", required=True, type=UUID)
    parser.add_argument("--principal-id", required=True, type=UUID)
    parser.add_argument("--expected-access-epoch", type=int)
    parser.add_argument("--source-system")
    parser.add_argument("--dataset-id")
    parser.add_argument("--source-subject")
    parser.add_argument("--notice-file", type=Path)
    args = vars(parser.parse_args(argv))
    path = args.pop("notice_file")
    source = {key: args.pop(key) for key in ("source_system", "dataset_id", "source_subject")}
    try:
        if any(value is not None for value in source.values()):
            args["source"] = source
        if path is not None:
            if args["operation"] != "apply":
                parser.error("invalid_source_access_arguments")
            args["notice"] = load_json(path, SourceNotice, max_bytes=MAX_NOTICE_BYTES)
        request = SourceAccessRequest.model_validate(args)
    except (OSError, ValueError, TypeError, RecursionError):
        parser.error("invalid_source_access_arguments")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory source-access: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with source_access(url, request) as result:
            print(result.model_dump_json(), flush=True)
            if result.operation == "apply" and result.reason in ("invalid_lease", "sequence_gap"):
                raise SystemExit(1)
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown}}),
            flush=True,
        )
        raise SystemExit(1) from None
