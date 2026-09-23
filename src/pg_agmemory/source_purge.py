"""Bounded ADMIN inventory with Native purge under an actual maintenance identity."""

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, Never, Self
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from pg_agmemory.admin import (
    AdminError,
    Epoch,
    admin_failure,
    async_admin_connection,
)
from pg_agmemory.database import Connection
from pg_agmemory.human_review import _invalid_constant, _unique_object, load_json
from pg_agmemory.models import DeletionResult, Forget, Identity, ShortText
from pg_agmemory.service import MemoryError, MemoryService, bind_identity
from pg_agmemory.source_access import SourceDatasetIdentity
from pg_agmemory.source_snapshot import SOURCE_NAMESPACE, SnapshotEnvelope, snapshot_digest
from pg_agmemory.transactions import CommitOutcomeUnknown, async_transaction

MAX_SOURCE_PURGE_BINDINGS = 100
MAX_SOURCE_PURGE_SCOPES = 32
MAX_SOURCE_PURGE_ROOTS = 100
MAX_SOURCE_PURGE_PLAN_BYTES = 262144
Digest = Annotated[
    str, Field(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
]


class _PurgeContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", hide_input_in_errors=True
    )


class SourcePurgeBinding(_PurgeContract):
    scope_id: UUID
    principal_id: UUID
    source_subject: ShortText
    sequence: Epoch

    @field_validator("source_subject")
    @classmethod
    def database_text(cls, value: str) -> str:
        value.encode("utf-8")
        if "\x00" in value or value != value.strip():
            raise ValueError("Invalid source subject")
        return value


class SourcePurgeRoot(_PurgeContract):
    memory_id: UUID
    scope_id: UUID
    content_digest: Digest


def _plan_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class SourcePurgePlan(_PurgeContract):
    format: Literal["pgag-source-purge-plan-v1"] = "pgag-source-purge-plan-v1"
    tenant_id: UUID
    dataset: SourceDatasetIdentity
    maintenance_principal_id: UUID
    access_epoch: Epoch
    deletion_epoch: Epoch
    bindings: Annotated[
        tuple[SourcePurgeBinding, ...], Field(min_length=1, max_length=MAX_SOURCE_PURGE_BINDINGS)
    ]
    roots: Annotated[tuple[SourcePurgeRoot, ...], Field(max_length=MAX_SOURCE_PURGE_ROOTS)]
    plan_digest: Digest

    @model_validator(mode="after")
    def canonical_plan(self) -> Self:
        bindings = [(binding.scope_id, binding.principal_id) for binding in self.bindings]
        if bindings != sorted(set(bindings)):
            raise ValueError("Bindings must be distinct and ordered")
        subjects: dict[UUID, str] = {}
        for binding in self.bindings:
            subject = subjects.setdefault(binding.scope_id, binding.source_subject)
            if subject != binding.source_subject:
                raise ValueError("Scopes must have one source identity")
        if len(subjects) > MAX_SOURCE_PURGE_SCOPES:
            raise ValueError("Too many source scopes")
        roots = [root.memory_id for root in self.roots]
        if roots != sorted(set(roots)):
            raise ValueError("Roots must be distinct and ordered")
        if any(root.scope_id not in subjects for root in self.roots):
            raise ValueError("Roots must belong to planned scopes")
        if self.plan_digest != _plan_digest(self.model_dump(mode="json", exclude={"plan_digest"})):
            raise ValueError("Invalid source purge plan digest")
        return self


class SourcePurgeRequest(_PurgeContract):
    operation: Literal["plan", "apply"]
    tenant_id: UUID
    dataset: SourceDatasetIdentity
    maintenance_principal_id: UUID
    expected_plan: SourcePurgePlan | None = None

    @model_validator(mode="after")
    def command_arguments(self) -> Self:
        if self.operation == "plan":
            if self.expected_plan is not None:
                raise ValueError("Plan cannot accept an expected plan")
        elif self.expected_plan is None:
            raise ValueError("Apply requires an expected plan")
        elif (
            self.expected_plan.tenant_id != self.tenant_id
            or self.expected_plan.dataset != self.dataset
            or self.expected_plan.maintenance_principal_id != self.maintenance_principal_id
        ):
            raise ValueError("Expected plan routing must match")
        return self


class SourcePurgeResult(_PurgeContract):
    operation: Literal["plan", "apply"]
    plan: SourcePurgePlan
    receipt: DeletionResult | None
    replayed: bool
    changed: bool
    coverage: Literal["planned_source_snapshot_roots"] = "planned_source_snapshot_roots"
    capture_reenabled: Literal[False] = False
    source_authorization_verified: Literal[False] = False
    backup_status: Literal["operator_managed"] = "operator_managed"


async def _runtime_role(conn: Connection) -> str:
    row = await (
        await conn.execute(
            """SELECT current_user AS admin_role,rolsuper,rolbypassrls,
                      EXISTS (
                          SELECT 1 FROM pg_class c
                          JOIN pg_namespace n ON n.oid=c.relnamespace
                          WHERE n.nspname IN ('memory','memory_ops')
                            AND pg_has_role(r.oid,c.relowner,'MEMBER')
                      ) AS owns_tables
               FROM pg_roles r WHERE rolname='pgag_runtime'"""
        )
    ).fetchone()
    if row is None or row["rolsuper"] or row["rolbypassrls"] or row["owns_tables"]:
        raise AdminError("runtime_role_invalid")
    return str(row["admin_role"])


@asynccontextmanager
async def _runtime_service(
    conn: Connection, identity: Identity, subject: str, admin_role: str,
    *, restore_role: bool = True,
) -> AsyncIterator[MemoryService]:
    await conn.execute("SET LOCAL ROLE pgag_runtime")
    await bind_identity(conn, subject, identity)
    yield MemoryService(conn, identity)
    # On failure the outer transaction rolls back, including the role change.
    if restore_role:
        await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(admin_role)))


async def _inventory(
    conn: Connection, request: SourcePurgeRequest, epochs: dict[str, Any]
) -> SourcePurgePlan:
    states = await (
        await conn.execute(
            """SELECT scope_id,principal_id,source_subject,sequence,decision,reason
               FROM memory_ops.source_access_state
               WHERE tenant_id=%s AND source_system=%s AND dataset_id=%s
               ORDER BY scope_id,principal_id LIMIT %s""",
            (
                request.tenant_id,
                request.dataset.source_system,
                request.dataset.dataset_id,
                MAX_SOURCE_PURGE_BINDINGS + 1,
            ),
        )
    ).fetchall()
    if not states:
        raise AdminError("source_dataset_not_bound")
    if len(states) > MAX_SOURCE_PURGE_BINDINGS:
        raise AdminError("source_dataset_target_limit")
    subjects = {state["scope_id"]: state["source_subject"] for state in states}
    if len(subjects) > MAX_SOURCE_PURGE_SCOPES:
        raise AdminError("source_purge_scope_limit")
    if any(state["decision"] != "deny" or state["reason"] != "deleted" for state in states):
        raise AdminError("source_purge_source_not_deleted")
    scopes = sorted(subjects)
    scoped_states = await (
        await conn.execute(
            """SELECT scope_id,source_system,dataset_id,source_subject
               FROM memory_ops.source_access_state
               WHERE tenant_id=%s AND scope_id=ANY(%s)
               ORDER BY scope_id,principal_id LIMIT %s""",
            (request.tenant_id, scopes, MAX_SOURCE_PURGE_BINDINGS + 1),
        )
    ).fetchall()
    # The exact dataset inventory is bounded; any additional scope binding is foreign.
    if len(scoped_states) != len(states) or any(
        state["source_system"] != request.dataset.source_system
        or state["dataset_id"] != request.dataset.dataset_id
        or state["source_subject"] != subjects[state["scope_id"]]
        for state in scoped_states
    ):
        raise AdminError("source_purge_scope_mixed")
    policies = await (
        await conn.execute(
            """SELECT scope_id,enabled FROM memory.scope_capture_policy
               WHERE tenant_id=%s AND scope_id=ANY(%s) ORDER BY scope_id""",
            (request.tenant_id, scopes),
        )
    ).fetchall()
    if len(policies) != len(scopes) or any(policy["enabled"] is not False for policy in policies):
        raise AdminError("source_purge_capture_enabled")
    episodes = await (
        await conn.execute(
            """SELECT id,scope_id,source_namespace,occurred_at,content
               FROM memory.episode WHERE tenant_id=%s AND scope_id=ANY(%s)
               ORDER BY id LIMIT %s""",
            (request.tenant_id, scopes, MAX_SOURCE_PURGE_ROOTS + 1),
        )
    ).fetchall()
    if len(episodes) > MAX_SOURCE_PURGE_ROOTS:
        raise AdminError("source_purge_root_limit")
    roots = []
    for episode in episodes:
        try:
            envelope = SnapshotEnvelope.model_validate(json.loads(
                episode["content"],
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            ))
            if (
                episode["source_namespace"] != SOURCE_NAMESPACE
                or envelope.source.scope_id != episode["scope_id"]
                or envelope.source.source_system != request.dataset.source_system
                or envelope.source.dataset_id != request.dataset.dataset_id
                or envelope.source.source_subject != subjects[episode["scope_id"]]
                or envelope.snapshot.observed_at != episode["occurred_at"]
            ):
                raise ValueError("Unrelated source snapshot")
            roots.append(SourcePurgeRoot(
                memory_id=episode["id"],
                scope_id=episode["scope_id"],
                content_digest=snapshot_digest(episode["content"]),
            ))
        except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
            raise AdminError("source_purge_scope_mixed") from None
    payload = {
        "format": "pgag-source-purge-plan-v1",
        "tenant_id": str(request.tenant_id),
        "dataset": request.dataset.model_dump(mode="json"),
        "maintenance_principal_id": str(request.maintenance_principal_id),
        "access_epoch": epochs["access_epoch"],
        "deletion_epoch": epochs["deletion_epoch"],
        "bindings": [
            SourcePurgeBinding(
                scope_id=state["scope_id"],
                principal_id=state["principal_id"],
                source_subject=state["source_subject"],
                sequence=state["sequence"],
            ).model_dump(mode="json")
            for state in states
        ],
        "roots": [root.model_dump(mode="json") for root in roots],
    }
    return SourcePurgePlan.model_validate({**payload, "plan_digest": _plan_digest(payload)})


async def _scope_permissions(service: MemoryService, plan: SourcePurgePlan) -> None:
    for scope in sorted({binding.scope_id for binding in plan.bindings}):
        await service.scope(scope, "delete")


def _forget_request(plan: SourcePurgePlan) -> Forget:
    return Forget(
        memory_ids=[root.memory_id for root in plan.roots],
        mode="purge",
        reason="external_source_deleted",
    )


async def _apply_source_purge(
    conn: Connection, request: SourcePurgeRequest
) -> SourcePurgeResult:
    epochs = await (
        await conn.execute(
            "SELECT access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s FOR UPDATE",
            (request.tenant_id,),
        )
    ).fetchone()
    principal = await (
        await conn.execute(
            "SELECT external_subject FROM memory.principal WHERE tenant_id=%s AND id=%s",
            (request.tenant_id, request.maintenance_principal_id),
        )
    ).fetchone()
    if epochs is None or principal is None:
        raise AdminError("not_found")
    admin_role = await _runtime_role(conn)
    identity = Identity(
        tenant_id=request.tenant_id, principal_id=request.maintenance_principal_id
    )
    expected = request.expected_plan
    if expected is not None and expected.roots:
        async with _runtime_service(
            conn, identity, principal["external_subject"], admin_role
        ) as svc:
            await _scope_permissions(svc, expected)
            _, _, previous = await svc.replay(
                "forget", "source-purge-v1:" + expected.plan_digest,
                _forget_request(expected).model_dump_json(),
            )
            if previous is not None:
                return SourcePurgeResult(
                    operation="apply", plan=expected,
                    receipt=DeletionResult.model_validate(previous), replayed=True, changed=False,
                )
    if expected is not None:
        if epochs["access_epoch"] != expected.access_epoch:
            raise AdminError("access_epoch_conflict")
        if epochs["deletion_epoch"] != expected.deletion_epoch:
            raise AdminError("deletion_epoch_conflict")
    plan = await _inventory(conn, request, epochs)
    if expected is not None and plan != expected:
        raise AdminError("source_purge_plan_conflict")
    receipt = None
    async with _runtime_service(
        conn, identity, principal["external_subject"], admin_role, restore_role=False
    ) as svc:
        await _scope_permissions(svc, plan)
        visible = await (
            await conn.execute(
                """SELECT id,scope_id FROM memory.episode
                   WHERE tenant_id=%s AND id=ANY(%s) ORDER BY id""",
                (request.tenant_id, [root.memory_id for root in plan.roots]),
            )
        ).fetchall()
        if [(row["id"], row["scope_id"]) for row in visible] != [
            (root.memory_id, root.scope_id) for root in plan.roots
        ]:
            raise AdminError("source_purge_scope_mixed")
        for root in plan.roots:
            obj = await svc.object(root.memory_id, "delete")
            if obj["scope_id"] != root.scope_id or obj["kind"] != "episode":
                raise AdminError("source_purge_scope_mixed")
        if request.operation == "apply" and plan.roots:
            receipt = DeletionResult.model_validate(
                await svc.forget(_forget_request(plan), "source-purge-v1:" + plan.plan_digest)
            )
    return SourcePurgeResult(
        operation=request.operation, plan=plan, receipt=receipt,
        replayed=False, changed=receipt is not None,
    )


@asynccontextmanager
async def source_purge(
    url: str, request: SourcePurgeRequest
) -> AsyncIterator[SourcePurgeResult]:
    try:
        request = SourcePurgeRequest.model_validate(request)
    except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
        raise AdminError("invalid_source_purge_request") from None
    commit_attempted = False
    try:
        async with async_admin_connection(url, request.tenant_id) as conn:
            async with async_transaction(conn):
                result = await _apply_source_purge(conn, request)
                commit_attempted = result.changed
            yield result
    except MemoryError as exc:
        raise AdminError(exc.code) from None
    except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
        raise AdminError("source_purge_invalid") from None
    except (psycopg.Error, CommitOutcomeUnknown) as exc:
        raise admin_failure(exc, commit_attempted) from None


class SourcePurgeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_source_purge_arguments")


def main(argv: list[str]) -> None:
    parser = SourcePurgeParser(
        prog="pg-agmemory source-purge",
        description=(
            "ADMIN-only bounded snapshot inventory and Native purge in dedicated source scopes. "
            "Every source binding must be terminal deleted and capture explicitly disabled."
        ),
        epilog=(
            "Save only the plan property of plan output as --plan-file for apply. "
            "Receipts cover planned roots, not a permanent or global dataset tombstone. "
            "Retries replay a historical Native receipt under current maintenance permissions; "
            "they do not certify the current inventory. Backups are operator-managed. "
            "No automatic retries, reactivation, or upstream authorization."
        ),
    )
    parser.add_argument("operation", choices=("plan", "apply"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--source-system", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--maintenance-principal-id", required=True, type=UUID)
    parser.add_argument("--plan-file", type=Path)
    args = vars(parser.parse_args(argv))
    path = args.pop("plan_file")
    args["dataset"] = {key: args.pop(key) for key in ("source_system", "dataset_id")}
    try:
        if path is not None:
            if args["operation"] != "apply":
                parser.error("invalid_source_purge_arguments")
            args["expected_plan"] = load_json(
                path, SourcePurgePlan, max_bytes=MAX_SOURCE_PURGE_PLAN_BYTES
            )
        request = SourcePurgeRequest.model_validate(args)
    except (OSError, ValidationError, TypeError, ValueError, OverflowError, RecursionError):
        parser.error("invalid_source_purge_arguments")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory source-purge: PGAG_ADMIN_DATABASE_URL is required\n")

    async def execute() -> None:
        async with source_purge(url, request) as result:
            print(result.model_dump_json(), flush=True)

    try:
        asyncio.run(execute())
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown}}),
            flush=True,
        )
        raise SystemExit(1) from None
