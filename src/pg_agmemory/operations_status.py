"""Read-only operator metadata, not readiness, admission, or recovery authority."""

import argparse
import json
import os
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Never, Self
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

from pg_agmemory import __version__
from pg_agmemory.admin import AdminError, Epoch, admin_failure, read_admin_snapshot
from pg_agmemory.database import SCHEMA_VERSION

Count = Annotated[int, Field(ge=0)]
AgeSeconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
WarningCode = Literal[
    "standby_snapshot",
    "job_lease_expired",
    "job_epoch_drift",
    "billing_unknown",
    "source_lease_expired",
    "source_grant_mismatch",
    "legacy_deletion_manifest",
    "graph_registry_stale",
]


class StatusContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        revalidate_instances="always", hide_input_in_errors=True,
    )


class OperationsStatusRequest(StatusContract):
    tenant_id: UUID


class JobStatus(StatusContract):
    total: Count
    pending: Count
    running: Count
    succeeded: Count
    failed: Count
    cancelled: Count
    due_pending: Count
    expired_running: Count
    stale_active: Count
    oldest_due_age_seconds: AgeSeconds | None

    @model_validator(mode="after")
    def complete_states(self) -> Self:
        if self.total != (
            self.pending + self.running + self.succeeded + self.failed + self.cancelled
        ):
            raise ValueError("Invalid job state counts")
        return self


class CallStatus(StatusContract):
    total: Count
    unknown: Count
    succeeded: Count
    failed: Count
    billing_unknown: Count
    reserved_input_bytes: Count
    max_output_tokens_reserved: Count

    @model_validator(mode="after")
    def complete_states(self) -> Self:
        if self.total != self.unknown + self.succeeded + self.failed:
            raise ValueError("Invalid call outcome counts")
        return self


class SourceStatus(StatusContract):
    total: Count
    allowed: Count
    denied: Count
    deleted: Count
    expired_allow: Count
    active_read_leases: Count
    unexpected_grants: Count

    @model_validator(mode="after")
    def complete_states(self) -> Self:
        if self.total != self.allowed + self.denied or self.deleted > self.denied:
            raise ValueError("Invalid source decision counts")
        return self


class DeletionStatus(StatusContract):
    total: Count
    active_store_purged: Count
    blocked_for_reads: Count
    legacy_manifests: Count
    target_rows: Count
    backup_retention_verified: Literal[False] = False

    @model_validator(mode="after")
    def complete_states(self) -> Self:
        if self.total != self.active_store_purged + self.blocked_for_reads:
            raise ValueError("Invalid deletion state counts")
        return self


class GraphStatus(StatusContract):
    registered: bool
    enabled: bool
    epoch_schema_match: bool | None
    serving_verified: Literal[False] = False


class OperationsStatus(StatusContract):
    format: Literal["pgag-operations-status-v1"] = "pgag-operations-status-v1"
    service_version: str = __version__
    api_version: Literal["v1"] = "v1"
    schema_version: Annotated[int, Field(ge=1)] = SCHEMA_VERSION
    evaluated_at: AwareDatetime
    tenant_id: UUID
    access_epoch: Epoch
    deletion_epoch: Epoch
    in_recovery: bool
    primary_snapshot: bool
    jobs: JobStatus
    calls: CallStatus
    source: SourceStatus
    deletions: DeletionStatus
    graph: GraphStatus
    warnings: tuple[WarningCode, ...]
    restore_authorized: Literal[False] = False
    source_authorization_verified: Literal[False] = False
    production_qualified: Literal[False] = False

    @field_validator("evaluated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


STATUS_QUERY = """
WITH jobs AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE state='pending') AS pending,
           count(*) FILTER (WHERE state='running') AS running,
           count(*) FILTER (WHERE state='succeeded') AS succeeded,
           count(*) FILTER (WHERE state='failed') AS failed,
           count(*) FILTER (WHERE state='cancelled') AS cancelled,
           count(*) FILTER (WHERE state='pending' AND available_at<=%(at)s) AS due_pending,
           count(*) FILTER (WHERE state='running' AND lease_until<=%(at)s) AS expired_running,
           count(*) FILTER (WHERE state IN ('pending','running') AND (
               captured_access_epoch<>%(access_epoch)s
               OR captured_deletion_epoch<>%(deletion_epoch)s
           )) AS stale_active,
           CASE WHEN count(*) FILTER (
               WHERE state='pending' AND available_at<=%(at)s
           ) > 0 THEN greatest(0, extract(epoch FROM (%(at)s - min(available_at) FILTER (
               WHERE state='pending' AND available_at<=%(at)s
           ))))::double precision END AS oldest_due_age_seconds
    FROM memory_ops.job WHERE tenant_id=%(tenant_id)s
), calls AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE outcome='unknown') AS unknown,
           count(*) FILTER (WHERE outcome='succeeded') AS succeeded,
           count(*) FILTER (WHERE outcome='failed') AS failed,
           count(*) FILTER (WHERE billing_unknown) AS billing_unknown,
           coalesce(sum(input_bytes),0) AS reserved_input_bytes,
           coalesce(sum(max_output_tokens),0) AS max_output_tokens_reserved
    FROM memory_ops.model_call WHERE tenant_id=%(tenant_id)s
), source_leases AS (
    SELECT s.decision, s.reason, s.valid_until,
           (s.decision='allow' AND s.verified_at<=%(at)s AND %(at)s<s.valid_until
            AND m.permissions=ARRAY['read']::text[] AND m.expires_at=s.valid_until
           ) IS TRUE AS approved_lease,
           m.principal_id IS NOT NULL
               AND (m.expires_at IS NULL OR m.expires_at>%(at)s) AS effective_membership
    FROM memory_ops.source_access_state s
    LEFT JOIN memory.scope_member m
        ON m.tenant_id=s.tenant_id AND m.scope_id=s.scope_id AND m.principal_id=s.principal_id
    WHERE s.tenant_id=%(tenant_id)s
), source AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE decision='allow') AS allowed,
           count(*) FILTER (WHERE decision='deny') AS denied,
           count(*) FILTER (WHERE decision='deny' AND reason='deleted') AS deleted,
           count(*) FILTER (WHERE decision='allow' AND valid_until<=%(at)s) AS expired_allow,
           count(*) FILTER (WHERE approved_lease) AS active_read_leases,
           count(*) FILTER (WHERE effective_membership AND NOT approved_lease) AS unexpected_grants
    FROM source_leases
), deletions AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE state='active_store_purged') AS active_store_purged,
           count(*) FILTER (WHERE state='blocked_for_reads') AS blocked_for_reads,
           count(*) FILTER (WHERE target_manifest_version=0) AS legacy_manifests
    FROM memory_ops.deletion_request WHERE tenant_id=%(tenant_id)s
), targets AS (
    SELECT count(*) AS total FROM memory_ops.deletion_target WHERE tenant_id=%(tenant_id)s
)
SELECT jobs.total AS jobs_total, jobs.pending AS jobs_pending, jobs.running AS jobs_running,
       jobs.succeeded AS jobs_succeeded, jobs.failed AS jobs_failed,
       jobs.cancelled AS jobs_cancelled, jobs.due_pending AS jobs_due_pending,
       jobs.expired_running AS jobs_expired_running, jobs.stale_active AS jobs_stale_active,
       jobs.oldest_due_age_seconds AS jobs_oldest_due_age_seconds,
       calls.total AS calls_total, calls.unknown AS calls_unknown,
       calls.succeeded AS calls_succeeded, calls.failed AS calls_failed,
       calls.billing_unknown AS calls_billing_unknown,
       calls.reserved_input_bytes AS calls_reserved_input_bytes,
       calls.max_output_tokens_reserved AS calls_max_output_tokens_reserved,
       source.total AS source_total, source.allowed AS source_allowed,
       source.denied AS source_denied, source.deleted AS source_deleted,
       source.expired_allow AS source_expired_allow,
       source.active_read_leases AS source_active_read_leases,
       source.unexpected_grants AS source_unexpected_grants,
       deletions.total AS deletions_total,
       deletions.active_store_purged AS deletions_active_store_purged,
       deletions.blocked_for_reads AS deletions_blocked_for_reads,
       deletions.legacy_manifests AS deletions_legacy_manifests,
       targets.total AS deletion_target_rows,
       g.tenant_id IS NOT NULL AS graph_registered,
       coalesce(g.enabled,false) AS graph_enabled,
       (g.captured_schema_version=%(schema_version)s
        AND g.captured_access_epoch=%(access_epoch)s
        AND g.captured_deletion_epoch=%(deletion_epoch)s) AS graph_epoch_schema_match
FROM jobs CROSS JOIN calls CROSS JOIN source CROSS JOIN deletions CROSS JOIN targets
LEFT JOIN memory_ops.age_projection g ON g.tenant_id=%(tenant_id)s
"""


def _build_status(
    request: OperationsStatusRequest, tenant: dict[str, Any], row: dict[str, Any],
) -> OperationsStatus:
    jobs = JobStatus(
        total=row["jobs_total"], pending=row["jobs_pending"], running=row["jobs_running"],
        succeeded=row["jobs_succeeded"], failed=row["jobs_failed"],
        cancelled=row["jobs_cancelled"], due_pending=row["jobs_due_pending"],
        expired_running=row["jobs_expired_running"], stale_active=row["jobs_stale_active"],
        oldest_due_age_seconds=row["jobs_oldest_due_age_seconds"],
    )
    calls = CallStatus(
        total=row["calls_total"], unknown=row["calls_unknown"],
        succeeded=row["calls_succeeded"], failed=row["calls_failed"],
        billing_unknown=row["calls_billing_unknown"],
        reserved_input_bytes=row["calls_reserved_input_bytes"],
        max_output_tokens_reserved=row["calls_max_output_tokens_reserved"],
    )
    source = SourceStatus(
        total=row["source_total"], allowed=row["source_allowed"], denied=row["source_denied"],
        deleted=row["source_deleted"], expired_allow=row["source_expired_allow"],
        active_read_leases=row["source_active_read_leases"],
        unexpected_grants=row["source_unexpected_grants"],
    )
    deletions = DeletionStatus(
        total=row["deletions_total"], active_store_purged=row["deletions_active_store_purged"],
        blocked_for_reads=row["deletions_blocked_for_reads"],
        legacy_manifests=row["deletions_legacy_manifests"], target_rows=row["deletion_target_rows"],
    )
    graph = GraphStatus(
        registered=row["graph_registered"], enabled=row["graph_enabled"],
        epoch_schema_match=row["graph_epoch_schema_match"],
    )
    conditions: tuple[tuple[WarningCode, bool], ...] = (
        ("standby_snapshot", tenant["in_recovery"]),
        ("job_lease_expired", jobs.expired_running > 0),
        ("job_epoch_drift", jobs.stale_active > 0),
        ("billing_unknown", calls.billing_unknown > 0),
        ("source_lease_expired", source.expired_allow > 0),
        ("source_grant_mismatch", source.unexpected_grants > 0),
        ("legacy_deletion_manifest", deletions.legacy_manifests > 0),
        ("graph_registry_stale", graph.registered and graph.enabled
         and graph.epoch_schema_match is False),
    )
    return OperationsStatus(
        evaluated_at=tenant["evaluated_at"], tenant_id=request.tenant_id,
        access_epoch=tenant["access_epoch"], deletion_epoch=tenant["deletion_epoch"],
        in_recovery=tenant["in_recovery"], primary_snapshot=not tenant["in_recovery"],
        jobs=jobs, calls=calls, source=source, deletions=deletions, graph=graph,
        warnings=tuple(code for code, present in conditions if present),
    )


def operations_status(url: str, request: OperationsStatusRequest) -> OperationsStatus:
    try:
        request = OperationsStatusRequest.model_validate(request)
    except ValidationError:
        raise AdminError("invalid_operations_status_request") from None
    try:
        with read_admin_snapshot(url) as conn:
            tenant = conn.execute(
                """SELECT access_epoch,deletion_epoch,clock_timestamp() AS evaluated_at,
                          pg_is_in_recovery() AS in_recovery
                   FROM memory.tenant WHERE id=%s""",
                (request.tenant_id,),
            ).fetchone()
            if tenant is None:
                raise AdminError("not_found")
            row = conn.execute(
                STATUS_QUERY,
                {
                    "tenant_id": request.tenant_id, "at": tenant["evaluated_at"],
                    "access_epoch": tenant["access_epoch"],
                    "deletion_epoch": tenant["deletion_epoch"],
                    "schema_version": SCHEMA_VERSION,
                },
            ).fetchone()
            if row is None:
                raise AdminError("operations_status_invalid_metadata")
            try:
                result = _build_status(request, tenant, row)
            except ValidationError:
                raise AdminError("operations_status_invalid_metadata") from None
    except psycopg.Error as exc:
        raise admin_failure(exc, False) from None
    return result


class OperationsStatusParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_operations_status_arguments")


def main(argv: list[str]) -> None:
    parser = OperationsStatusParser(
        prog="pg-agmemory operations-status",
        description=(
            "ADMIN-only, read-only tenant metadata snapshot; no tenant admission barrier. "
            "Not readiness, authentication, source authorization, recovery approval, or "
            "production qualification. Warnings are metadata, not a health verdict."
        ),
        epilog=(
            "Uses PGAG_ADMIN_DATABASE_URL only. Does not claim, renew, purge, or modify state. "
            "Call reservations are not actual billed cost or tokens. Failed jobs are terminal, "
            "not an automatic dead-letter action. Store purge does not remove backups. "
            "Graph registry markers do not verify physical graph completeness or serving. "
            "Standby snapshots do not authorize startup."
        ),
    )
    parser.add_argument("--tenant-id", type=UUID, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory operations-status: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        result = operations_status(url, OperationsStatusRequest(tenant_id=args.tenant_id))
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": False}}),
            flush=True,
        )
        raise SystemExit(1) from None
    print(result.model_dump_json(), flush=True)
