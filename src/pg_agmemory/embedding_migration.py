"""Bounded embedding-space observations, never inference or cutover authority."""

import argparse
import json
import os
import sys
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

from pg_agmemory.admin import AdminError, Epoch, admin_failure, read_admin_snapshot
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.models import EmbeddingModel, Revision

INPUT_FORMAT = "memory-content-v1"
Count = Annotated[int, Field(ge=0)]
Difference = Literal[
    "name", "revision", "dimensions", "distance_metric", "normalization", "input_format",
]
IssueCode = Literal[
    "source_missing", "source_stale", "target_missing", "target_stale",
    "source_ineligible", "target_ineligible", "target_capacity_blocked", "target_write_denied",
]
Blocker = Literal[
    "standby_snapshot", "unsupported_source_input_format", "unsupported_target_input_format",
    "source_stale", "target_stale", "target_capacity_blocked", "target_write_denied",
]
Status = Literal["ready", "incomplete", "blocked", "empty"]


class MigrationContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always",
        hide_input_in_errors=True,
    )


class EmbeddingSpace(EmbeddingModel):
    """Input format is an operator declaration, not persisted provider provenance."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, str_strip_whitespace=False,
        revalidate_instances="always", hide_input_in_errors=True,
    )

    input_format: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    ] = INPUT_FORMAT

    @field_validator("name", "revision")
    @classmethod
    def exact_identity(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise ValueError("Invalid model identity")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid model identity") from None
        return value

    @field_validator("dimensions", mode="before")
    @classmethod
    def integer_dimensions(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Invalid dimensions")
        return value


class EmbeddingMigrationRequest(MigrationContract):
    tenant_id: UUID
    principal_id: UUID
    scope_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=32)]
    source: EmbeddingSpace
    target: EmbeddingSpace
    as_of: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    expected_access_epoch: Epoch | None = None
    expected_deletion_epoch: Epoch | None = None
    max_revisions: Annotated[int, Field(ge=1, le=10000)] = 1000
    max_samples: Annotated[int, Field(ge=0, le=100)] = 20

    @model_validator(mode="after")
    def explicit_spaces(self) -> Self:
        if len(set(self.scope_ids)) != len(self.scope_ids):
            raise ValueError("Scope IDs must be unique")
        if (self.source.name, self.source.revision) == (self.target.name, self.target.revision):
            raise ValueError("Migration requires a distinct model name or revision")
        if (self.expected_access_epoch is None) != (self.expected_deletion_epoch is None):
            raise ValueError("Both expected epochs are required together")
        return self


class SpaceCoverage(MigrationContract):
    eligible_ready: Count
    eligible_missing: Count
    eligible_stale: Count
    ineligible_present: Count
    ineligible_stale: Count

    @model_validator(mode="after")
    def valid_counts(self) -> Self:
        if self.ineligible_stale > self.ineligible_present:
            raise ValueError("Invalid coverage counts")
        return self


class MigrationIssue(MigrationContract):
    memory_id: UUID
    revision: Revision
    kind: Literal["episode", "assertion"]
    code: IssueCode


class EmbeddingMigrationReport(MigrationContract):
    format: Literal["pgag-embedding-migration-v1"] = "pgag-embedding-migration-v1"
    status: Status
    assessment_scope: Literal["principal_scope_time_projection_snapshot"] = (
        "principal_scope_time_projection_snapshot"
    )
    blockers: tuple[Blocker, ...]
    schema_version: Annotated[int, Field(ge=1)] = SCHEMA_VERSION
    tenant_id: UUID
    principal_id: UUID
    scope_ids: tuple[UUID, ...]
    evaluated_at: AwareDatetime
    as_of: AwareDatetime
    known_at: AwareDatetime
    access_epoch: Epoch
    deletion_epoch: Epoch
    in_recovery: bool
    source: EmbeddingSpace
    target: EmbeddingSpace
    differences: tuple[Difference, ...]
    input_formats_supported: bool
    visible_revisions: Count
    eligible_revisions: Count
    source_coverage: SpaceCoverage
    target_coverage: SpaceCoverage
    source_projection_complete: bool
    target_projection_complete: bool
    target_capacity_blocked: Count
    target_write_denied: Count
    max_revisions: Annotated[int, Field(ge=1, le=10000)]
    issues_total: Count
    issues: tuple[MigrationIssue, ...]
    issues_truncated: bool
    model_identity_authority: Literal["caller_declared"] = "caller_declared"
    input_profile_verified: Literal[False] = False
    query_space_verified: Literal[False] = False
    source_authorization_verified: Literal[False] = False
    cutover_authorized: Literal[False] = False
    rollback_authorized: Literal[False] = False
    production_qualified: Literal[False] = False
    source_retention: Literal["retain_for_explicit_caller_rollback"] = (
        "retain_for_explicit_caller_rollback"
    )
    projection_retirement: Literal["no_supported_projection_delete"] = (
        "no_supported_projection_delete"
    )
    deletion_contract: Literal["canonical_forget_purges_all_model_spaces"] = (
        "canonical_forget_purges_all_model_spaces"
    )

    @field_validator("evaluated_at", "as_of", "known_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def exact_coverage(self) -> Self:
        if self.eligible_revisions > self.visible_revisions:
            raise ValueError("Invalid revision counts")
        for space, coverage, complete in (
            (self.source, self.source_coverage, self.source_projection_complete),
            (self.target, self.target_coverage, self.target_projection_complete),
        ):
            if (
                coverage.eligible_ready + coverage.eligible_missing + coverage.eligible_stale
                != self.eligible_revisions
                or coverage.ineligible_present > self.visible_revisions - self.eligible_revisions
            ):
                raise ValueError("Invalid coverage counts")
            if complete != (
                self.eligible_revisions > 0
                and coverage.eligible_ready == self.eligible_revisions
                and space.input_format == INPUT_FORMAT
            ):
                raise ValueError("Invalid projection completeness")
        if self.status != _status(
            self.blockers, self.eligible_revisions,
            self.source_projection_complete, self.target_projection_complete,
        ):
            raise ValueError("Invalid assessment status")
        return self


def _status(
    blockers: tuple[Blocker, ...], eligible: int, source_complete: bool, target_complete: bool,
) -> Status:
    if blockers:
        return "blocked"
    if eligible == 0:
        return "empty"
    return "ready" if source_complete and target_complete else "incomplete"


INVENTORY_QUERY = """
WITH clock AS MATERIALIZED (
    SELECT statement_timestamp() AS at,
           coalesce(%(as_of)s::timestamptz,statement_timestamp()) AS as_of,
           coalesce(%(known_at)s::timestamptz,statement_timestamp()) AS known_at
), refs AS MATERIALIZED (
    SELECT * FROM (
        SELECT o.id,o.kind,o.scope_id,1::bigint AS revision,
               o.created_at<=clock.known_at AND e.occurred_at<=clock.as_of AS eligible
        FROM memory.object o JOIN memory.episode e USING (tenant_id,id) CROSS JOIN clock
        WHERE o.tenant_id=%(tenant)s AND o.scope_id=ANY(%(scopes)s)
        UNION ALL
        SELECT o.id,o.kind,o.scope_id,r.revision,
               r.valid_time @> clock.as_of AND r.system_time @> clock.known_at
               AND (NOT a.is_relation OR EXISTS (
                   SELECT 1 FROM memory.relation_revision rr
                   WHERE rr.tenant_id=r.tenant_id AND rr.assertion_id=r.assertion_id
                     AND rr.revision=r.revision
               )) AS eligible
        FROM memory.object o JOIN memory.assertion a USING (tenant_id,id)
        JOIN memory.assertion_revision r ON r.tenant_id=a.tenant_id AND r.assertion_id=a.id
        CROSS JOIN clock
        WHERE o.tenant_id=%(tenant)s AND o.scope_id=ANY(%(scopes)s)
    ) revisions ORDER BY kind,id,revision LIMIT %(limit)s
), bounded AS MATERIALIZED (
    SELECT * FROM refs WHERE (SELECT count(*) FROM refs)<=%(max_revisions)s
), canonical AS MATERIALIZED (
    SELECT b.*,encode(sha256(convert_to(e.content,'UTF8')),'hex') AS digest
    FROM bounded b JOIN memory.episode e ON b.kind='episode'
        AND e.tenant_id=%(tenant)s AND e.id=b.id
    UNION ALL
    SELECT b.*,encode(sha256(convert_to(a.subject || ' / ' || a.predicate || ': ' || r.value,
                                      'UTF8')),'hex') AS digest
    FROM bounded b JOIN memory.assertion a ON b.kind='assertion'
        AND a.tenant_id=%(tenant)s AND a.id=b.id
    JOIN memory.assertion_revision r ON r.tenant_id=a.tenant_id AND r.assertion_id=a.id
        AND r.revision=b.revision
), checked AS (
    SELECT c.id,c.kind,c.revision,c.eligible,
           memory.permitted(c.scope_id,'write') AS writable,
           p.model_count,p.source_digest IS NOT NULL AS source_present,
           p.target_digest IS NOT NULL AS target_present,
           coalesce(p.source_digest=c.digest,false) AS source_matches,
           coalesce(p.target_digest=c.digest,false) AS target_matches
    FROM canonical c CROSS JOIN LATERAL (
        SELECT count(*) AS model_count,
               max(input_digest) FILTER (
                   WHERE model_name=%(source_name)s AND model_revision=%(source_revision)s
               ) AS source_digest,
               max(input_digest) FILTER (
                   WHERE model_name=%(target_name)s AND model_revision=%(target_revision)s
               ) AS target_digest
        FROM (
            SELECT model_name,model_revision,input_digest FROM memory.episode_embedding
            WHERE c.kind='episode' AND tenant_id=%(tenant)s AND episode_id=c.id
              AND revision=c.revision
            UNION ALL
            SELECT model_name,model_revision,input_digest FROM memory.assertion_embedding
            WHERE c.kind='assertion' AND tenant_id=%(tenant)s AND assertion_id=c.id
              AND revision=c.revision
        ) projections
    ) p
)
SELECT clock.*, (SELECT count(*) FROM refs) AS observed_revisions,checked.*
FROM clock LEFT JOIN checked ON true ORDER BY checked.kind,checked.id,checked.revision
"""


def _runtime_identity(
    conn: psycopg.Connection[dict[str, Any]], request: EmbeddingMigrationRequest,
) -> dict[str, Any]:
    tenant = conn.execute(
        """SELECT t.access_epoch,t.deletion_epoch,p.external_subject,
                  pg_is_in_recovery() AS in_recovery
           FROM memory.tenant t JOIN memory.principal p ON p.tenant_id=t.id AND p.id=%s
           WHERE t.id=%s""", (request.principal_id, request.tenant_id),
    ).fetchone()
    if tenant is None:
        raise AdminError("not_found")
    if request.expected_access_epoch is not None and (
        request.expected_access_epoch != tenant["access_epoch"]
        or request.expected_deletion_epoch != tenant["deletion_epoch"]
    ):
        raise AdminError("epoch_conflict")
    role = conn.execute(
        """SELECT rolsuper,rolbypassrls,EXISTS (
               SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname IN ('memory','memory_ops')
                 AND pg_has_role(r.oid,c.relowner,'MEMBER')
           ) AS owns_tables FROM pg_roles r WHERE rolname='pgag_runtime'""",
    ).fetchone()
    if role is None or role["rolsuper"] or role["rolbypassrls"] or role["owns_tables"]:
        raise AdminError("runtime_role_invalid")
    conn.execute("SET LOCAL ROLE pgag_runtime")
    conn.execute(
        """SELECT set_config('pgag.subject',%s,true),set_config('pgag.tenant_id',%s,true),
                  set_config('pgag.principal_id',%s,true)""",
        (tenant["external_subject"], str(request.tenant_id), str(request.principal_id)),
    )
    policies = conn.execute(
        """SELECT bool_and(row_security_active(relation::regclass)) AS active
           FROM unnest(ARRAY[
               'memory.object','memory.episode','memory.assertion','memory.assertion_revision',
               'memory.relation_revision','memory.scope_member','memory_ops.object_tombstone',
               'memory.episode_embedding','memory.assertion_embedding'
           ]) AS tables(relation)""",
    ).fetchone()
    if policies is None or not policies["active"]:
        raise AdminError("runtime_role_invalid")
    return tenant


def _report(
    request: EmbeddingMigrationRequest, tenant: dict[str, Any], rows: list[dict[str, Any]],
) -> EmbeddingMigrationReport:
    clock = rows[0]
    if clock["observed_revisions"] > request.max_revisions:
        raise AdminError("embedding_migration_limit_exceeded")
    revisions = [row for row in rows if row["id"] is not None]
    if len(revisions) != clock["observed_revisions"]:
        raise AdminError("embedding_migration_invalid_metadata")
    coverage = {
        space: {
            "eligible_ready": 0, "eligible_missing": 0, "eligible_stale": 0,
            "ineligible_present": 0, "ineligible_stale": 0,
        } for space in ("source", "target")
    }
    issues: list[MigrationIssue] = []
    issues_total = eligible = capacity_blocked = write_denied = 0

    def issue(row: dict[str, Any], code: IssueCode) -> None:
        nonlocal issues_total
        issues_total += 1
        if len(issues) < request.max_samples:
            issues.append(MigrationIssue(
                memory_id=row["id"], revision=row["revision"], kind=row["kind"], code=code,
            ))

    for row in revisions:
        eligible += int(row["eligible"])
        for space in ("source", "target"):
            present, matches = row[f"{space}_present"], row[f"{space}_matches"]
            if row["eligible"]:
                status = "ready" if matches else "stale" if present else "missing"
                coverage[space]["eligible_" + status] += 1
                if status != "ready":
                    code: IssueCode
                    if space == "source":
                        code = "source_stale" if present else "source_missing"
                    else:
                        code = "target_stale" if present else "target_missing"
                    issue(row, code)
            elif present:
                coverage[space]["ineligible_present"] += 1
                coverage[space]["ineligible_stale"] += int(not matches)
                issue(row, "source_ineligible" if space == "source" else "target_ineligible")
        if row["eligible"] and not row["target_present"]:
            if row["model_count"] >= 8:
                capacity_blocked += 1
                issue(row, "target_capacity_blocked")
            if not row["writable"]:
                write_denied += 1
                issue(row, "target_write_denied")

    source, target = SpaceCoverage(**coverage["source"]), SpaceCoverage(**coverage["target"])
    fields: tuple[Difference, ...] = (
        "name", "revision", "dimensions", "distance_metric", "normalization", "input_format",
    )
    source_complete = (
        eligible > 0 and source.eligible_ready == eligible
        and request.source.input_format == INPUT_FORMAT
    )
    target_complete = (
        eligible > 0 and target.eligible_ready == eligible
        and request.target.input_format == INPUT_FORMAT
    )
    conditions: tuple[tuple[Blocker, bool], ...] = (
        ("standby_snapshot", tenant["in_recovery"]),
        ("unsupported_source_input_format", request.source.input_format != INPUT_FORMAT),
        ("unsupported_target_input_format", request.target.input_format != INPUT_FORMAT),
        ("source_stale", source.eligible_stale > 0),
        ("target_stale", target.eligible_stale > 0),
        ("target_capacity_blocked", capacity_blocked > 0),
        ("target_write_denied", write_denied > 0),
    )
    blockers = tuple(code for code, present in conditions if present)
    return EmbeddingMigrationReport(
        status=_status(blockers, eligible, source_complete, target_complete), blockers=blockers,
        tenant_id=request.tenant_id, principal_id=request.principal_id,
        scope_ids=request.scope_ids, evaluated_at=clock["at"], as_of=clock["as_of"],
        known_at=clock["known_at"], access_epoch=tenant["access_epoch"],
        deletion_epoch=tenant["deletion_epoch"], in_recovery=tenant["in_recovery"],
        source=request.source, target=request.target,
        differences=tuple(
            field for field in fields
            if getattr(request.source, field) != getattr(request.target, field)
        ),
        input_formats_supported=(
            request.source.input_format == request.target.input_format == INPUT_FORMAT
        ),
        visible_revisions=len(revisions), eligible_revisions=eligible,
        source_coverage=source, target_coverage=target,
        source_projection_complete=source_complete, target_projection_complete=target_complete,
        target_capacity_blocked=capacity_blocked, target_write_denied=write_denied,
        max_revisions=request.max_revisions, issues_total=issues_total, issues=tuple(issues),
        issues_truncated=issues_total > len(issues),
    )


def embedding_migration(
    url: str, request: EmbeddingMigrationRequest,
) -> EmbeddingMigrationReport:
    try:
        request = EmbeddingMigrationRequest.model_validate(request)
    except ValidationError:
        raise AdminError("invalid_embedding_migration_request") from None
    try:
        with read_admin_snapshot(url) as conn:
            tenant = _runtime_identity(conn, request)
            rows = conn.execute(INVENTORY_QUERY, {
                "tenant": request.tenant_id, "scopes": list(request.scope_ids),
                "as_of": request.as_of, "known_at": request.known_at,
                "limit": request.max_revisions + 1, "max_revisions": request.max_revisions,
                "source_name": request.source.name, "source_revision": request.source.revision,
                "target_name": request.target.name, "target_revision": request.target.revision,
            }).fetchall()
            if not rows:
                raise AdminError("embedding_migration_invalid_metadata")
            try:
                result = _report(request, tenant, rows)
            except ValidationError:
                raise AdminError("embedding_migration_invalid_metadata") from None
    except psycopg.Error as exc:
        raise admin_failure(exc, False) from None
    return result


class EmbeddingMigrationParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_embedding_migration_arguments")


def main(argv: list[str]) -> None:
    parser = EmbeddingMigrationParser(
        prog="pg-agmemory embedding-migration",
        description=(
            "ADMIN-only read-only embedding-space preflight under an explicit principal's RLS. "
            "No inference, backfill, projection deletion, or cutover/rollback authorization."
        ),
        epilog=(
            "Uses PGAG_ADMIN_DATABASE_URL only. Distinct model name/revision required. "
            "Status ready means only both projection spaces cover this nonempty snapshot; "
            "incomplete means missing coverage, blocked means intervention, empty is not ready. "
            "Only memory-content-v1 is supported; other declared formats cannot be complete. "
            "Retain source projections for caller-selected rollback. Canonical forget purges "
            "all spaces; it is not a model-retirement operation. Recheck after any change."
        ),
    )
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--principal-id", type=UUID, required=True)
    parser.add_argument("--scope-id", type=UUID, action="append", required=True)
    for space in ("source", "target"):
        parser.add_argument(f"--{space}-name", required=True)
        parser.add_argument(f"--{space}-revision", required=True)
        parser.add_argument(f"--{space}-input-format", default=INPUT_FORMAT)
    parser.add_argument("--as-of")
    parser.add_argument("--known-at")
    parser.add_argument("--expected-access-epoch", type=int)
    parser.add_argument("--expected-deletion-epoch", type=int)
    parser.add_argument("--max-revisions", type=int, default=1000)
    parser.add_argument("--max-samples", type=int, default=20)
    args = parser.parse_args(argv)
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory embedding-migration: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        request = EmbeddingMigrationRequest.model_validate_json(json.dumps({
            "tenant_id": str(args.tenant_id), "principal_id": str(args.principal_id),
            "scope_ids": [str(scope) for scope in args.scope_id],
            "source": {
                "name": args.source_name, "revision": args.source_revision,
                "input_format": args.source_input_format,
            },
            "target": {
                "name": args.target_name, "revision": args.target_revision,
                "input_format": args.target_input_format,
            },
            "as_of": args.as_of, "known_at": args.known_at,
            "expected_access_epoch": args.expected_access_epoch,
            "expected_deletion_epoch": args.expected_deletion_epoch,
            "max_revisions": args.max_revisions, "max_samples": args.max_samples,
        }))
    except ValidationError:
        parser.error("invalid_embedding_migration_arguments")
    try:
        result = embedding_migration(url, request)
    except AdminError as exc:
        print(json.dumps({"error": {"code": exc.code, "outcome_unknown": False}}), flush=True)
        raise SystemExit(1) from None
    print(result.model_dump_json(), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
