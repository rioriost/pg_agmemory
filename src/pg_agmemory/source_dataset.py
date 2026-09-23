"""Bounded registered-reader discovery and non-durable emergency revocation."""

import argparse
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal, Never
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pg_agmemory.admin import MAX_EPOCH, AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.scope_access import Permission, ScopeAccessRequest, apply_scope_access
from pg_agmemory.source_access import SourceDatasetIdentity, SourceReason

MAX_DATASET_TARGETS = 100


class SourceDatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    operation: Literal["get", "revoke"]
    tenant_id: UUID
    dataset: SourceDatasetIdentity
    expected_access_epoch: Epoch | None = None
    expected_target_digest: Annotated[
        str, Field(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    ] | None = None

    @model_validator(mode="after")
    def command_arguments(self) -> "SourceDatasetRequest":
        if self.operation == "get":
            if self.expected_access_epoch is not None or self.expected_target_digest is not None:
                raise ValueError("Get does not accept mutation arguments")
        elif self.expected_access_epoch is None or self.expected_target_digest is None:
            raise ValueError("Revoke requires an expected epoch and target digest")
        return self


class SourceDatasetTarget(BaseModel):
    scope_id: UUID
    principal_id: UUID
    source_subject: str
    sequence: Annotated[int, Field(ge=0, le=MAX_EPOCH)]
    decision: Literal["allow", "deny"]
    reason: SourceReason
    membership_exists: bool
    permissions: list[Permission]
    effective_permissions: list[Permission]
    expires_at: datetime | None
    evaluated_at: datetime
    changed: bool


class SourceDatasetResult(BaseModel):
    operation: Literal["get", "revoke"]
    tenant_id: UUID
    dataset: SourceDatasetIdentity
    target_digest: str
    access_epoch: int
    matched_targets: int
    changed_targets: int
    targets: list[SourceDatasetTarget]
    coverage: Literal["registered_readers_only"] = "registered_readers_only"
    source_authorization_verified: Literal[False] = False
    source_notices_changed: Literal[False] = False
    physical_purge: Literal[False] = False
    durable_dataset_block: Literal[False] = False


def _target_digest(request: SourceDatasetRequest, states: list[dict[str, Any]]) -> str:
    payload = {
        "format": "pgag-source-dataset-targets-v1",
        "tenant_id": str(request.tenant_id),
        "dataset": request.dataset.model_dump(mode="json"),
        "targets": [
            {
                "scope_id": str(state["scope_id"]),
                "principal_id": str(state["principal_id"]),
                "source_subject": state["source_subject"],
            }
            for state in states
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _apply_source_dataset(
    conn: psycopg.Connection[dict[str, Any]], request: SourceDatasetRequest
) -> SourceDatasetResult:
    mutation = request.operation == "revoke"
    tenant = conn.execute(
        "SELECT access_epoch FROM memory.tenant WHERE id=%s"
        + (" FOR UPDATE" if mutation else ""),
        (request.tenant_id,),
    ).fetchone()
    if tenant is None:
        raise AdminError("not_found")
    states = conn.execute(
        """SELECT scope_id,principal_id,source_subject,sequence,decision,reason
           FROM memory_ops.source_access_state
           WHERE tenant_id=%s AND source_system=%s AND dataset_id=%s
           ORDER BY scope_id,principal_id LIMIT %s""",
        (
            request.tenant_id,
            request.dataset.source_system,
            request.dataset.dataset_id,
            MAX_DATASET_TARGETS + 1,
        ),
    ).fetchall()
    if len(states) > MAX_DATASET_TARGETS:
        raise AdminError("source_dataset_target_limit")
    if not states:
        raise AdminError("source_dataset_not_bound")
    epoch = tenant["access_epoch"]
    digest = _target_digest(request, states)
    if mutation:
        if epoch != request.expected_access_epoch:
            raise AdminError("access_epoch_conflict")
        if digest != request.expected_target_digest:
            raise AdminError("source_dataset_target_conflict")
    targets = []
    for state in states:
        access = apply_scope_access(
            conn,
            ScopeAccessRequest(
                operation=request.operation,
                tenant_id=request.tenant_id,
                scope_id=state["scope_id"],
                principal_id=state["principal_id"],
                expected_access_epoch=epoch if mutation else None,
            ),
        )
        epoch = access.access_epoch
        # Source decisions remain historical; current effective access comes from the helper.
        targets.append(
            SourceDatasetTarget(
                scope_id=state["scope_id"],
                principal_id=state["principal_id"],
                source_subject=state["source_subject"],
                sequence=state["sequence"],
                decision=state["decision"],
                reason=state["reason"],
                membership_exists=access.membership_exists,
                permissions=access.permissions,
                effective_permissions=access.effective_permissions,
                expires_at=access.expires_at,
                evaluated_at=access.evaluated_at,
                changed=access.changed,
            )
        )
    return SourceDatasetResult(
        operation=request.operation,
        tenant_id=request.tenant_id,
        dataset=request.dataset,
        target_digest=digest,
        access_epoch=epoch,
        matched_targets=len(targets),
        changed_targets=sum(target.changed for target in targets),
        targets=targets,
    )


@contextmanager
def source_dataset(url: str, request: SourceDatasetRequest) -> Iterator[SourceDatasetResult]:
    try:
        request = SourceDatasetRequest.model_validate(request)
    except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
        raise AdminError("invalid_source_dataset_request") from None
    commit_attempted = False
    try:
        with admin_connection(url, request.tenant_id) as conn:
            with conn.transaction():
                result = _apply_source_dataset(conn, request)
                commit_attempted = bool(result.changed_targets)
            yield result
    except psycopg.Error as exc:
        raise admin_failure(exc, commit_attempted) from None


class SourceDatasetParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_source_dataset_arguments")


def main(argv: list[str]) -> None:
    parser = SourceDatasetParser(
        prog="pg-agmemory source-dataset",
        description=(
            "ADMIN-only discovery and atomic emergency revocation for an exact source dataset: "
            f"at most {MAX_DATASET_TARGETS} registered reader targets, across subjects and scopes."
        ),
        epilog=(
            "Registered targets only; unregistered and maintenance grants are untouched. "
            "Get first, then revoke with its access epoch and target digest. "
            "Not upstream authorization, source notification ingress, a durable dataset block, "
            "or physical purge. Source notices remain unchanged: a fresh contiguous allow can "
            "reopen access, but an exact last-notice replay cannot. No automatic retries."
        ),
    )
    parser.add_argument("operation", choices=("get", "revoke"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--source-system", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--expected-access-epoch", type=int)
    parser.add_argument("--expected-target-digest")
    args = vars(parser.parse_args(argv))
    args["dataset"] = {key: args.pop(key) for key in ("source_system", "dataset_id")}
    try:
        request = SourceDatasetRequest.model_validate(args)
    except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
        parser.error("invalid_source_dataset_arguments")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory source-dataset: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with source_dataset(url, request) as result:
            print(result.model_dump_json(), flush=True)
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown}}),
            flush=True,
        )
        raise SystemExit(1) from None
