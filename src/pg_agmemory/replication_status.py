"""Read-only local replication observations, never promotion or fencing authority."""

import argparse
import json
import os
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Never, Self

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
from pg_agmemory.admin import AdminError, admin_failure, read_admin_snapshot
from pg_agmemory.database import SCHEMA_VERSION

Count = Annotated[int, Field(ge=0)]
LSN = Annotated[str, Field(pattern=r"^[0-9A-F]{1,8}/[0-9A-F]{1,8}$")]
SynchronousCommit = Literal["off", "local", "remote_write", "on", "remote_apply"]
WarningCode = Literal[
    "standby_replay_paused",
    "standby_receiver_not_streaming",
    "synchronous_standby_missing",
    "session_commit_not_remote_apply",
    "inactive_replication_slots",
]


class ReplicationContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        revalidate_instances="always", hide_input_in_errors=True,
    )


class SenderStatus(ReplicationContract):
    total: Count
    physical_streaming: Count
    physical_synchronous: Count
    logical: Count

    @model_validator(mode="after")
    def valid_counts(self) -> Self:
        if not (
            self.physical_synchronous <= self.physical_streaming <= self.total - self.logical
        ):
            raise ValueError("Invalid sender counts")
        return self


class ReceiverStatus(ReplicationContract):
    present: bool
    streaming: bool

    @model_validator(mode="after")
    def valid_state(self) -> Self:
        if self.streaming and not self.present:
            raise ValueError("Invalid receiver state")
        return self


class SlotStatus(ReplicationContract):
    total: Count
    physical: Count
    logical: Count
    inactive: Count

    @model_validator(mode="after")
    def valid_counts(self) -> Self:
        if self.total != self.physical + self.logical or self.inactive > self.total:
            raise ValueError("Invalid replication slot counts")
        return self


class ReplicationStatus(ReplicationContract):
    format: Literal["pgag-replication-status-v1"] = "pgag-replication-status-v1"
    service_version: str = __version__
    api_version: Literal["v1"] = "v1"
    schema_version: Annotated[int, Field(ge=1)] = SCHEMA_VERSION
    evaluated_at: AwareDatetime
    in_recovery: bool
    transaction_read_only: Literal[True]
    synchronous_commit: SynchronousCommit
    synchronous_standby_configured: bool
    primary_flush_lsn: LSN | None
    received_lsn: LSN | None
    replayed_lsn: LSN | None
    replay_paused: bool | None
    senders: SenderStatus
    receiver: ReceiverStatus
    slots: SlotStatus
    warnings: tuple[WarningCode, ...]
    statistics_atomic: Literal[False] = False
    writer_policy_verified: Literal[False] = False
    fencing_verified: Literal[False] = False
    promotion_authorized: Literal[False] = False
    production_qualified: Literal[False] = False

    @field_validator(
        "transaction_read_only", "statistics_atomic", "writer_policy_verified",
        "fencing_verified", "promotion_authorized", "production_qualified", mode="before",
    )
    @classmethod
    def boolean_flags(cls, value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError("Invalid observation flag")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def role_fields(self) -> Self:
        if self.in_recovery:
            if self.primary_flush_lsn is not None or self.replay_paused is None:
                raise ValueError("Invalid standby observation")
        elif (
            self.primary_flush_lsn is None
            or self.received_lsn is not None
            or self.replayed_lsn is not None
            or self.replay_paused is not None
        ):
            raise ValueError("Invalid primary observation")
        return self


SETTINGS_QUERY = """
WITH local_role AS MATERIALIZED (
    SELECT pg_is_in_recovery() AS in_recovery
)
SELECT clock_timestamp() AS evaluated_at, local_role.in_recovery,
       current_setting('transaction_read_only')::boolean AS transaction_read_only,
       current_setting('synchronous_commit') AS synchronous_commit,
       current_setting('synchronous_standby_names')<>'' AS synchronous_standby_configured,
       CASE WHEN NOT local_role.in_recovery
            THEN pg_current_wal_flush_lsn()::text END AS primary_flush_lsn,
       CASE WHEN local_role.in_recovery
            THEN pg_last_wal_receive_lsn()::text END AS received_lsn,
       CASE WHEN local_role.in_recovery
            THEN pg_last_wal_replay_lsn()::text END AS replayed_lsn,
       CASE WHEN local_role.in_recovery
            THEN pg_is_wal_replay_paused() END AS replay_paused
FROM local_role
"""

STATISTICS_QUERY = """
WITH senders AS (
    SELECT count(*) AS total,
           count(*) FILTER (
               WHERE (s.slot_type='physical' OR s.slot_type IS NULL) AND r.state='streaming'
           ) AS physical_streaming,
           count(*) FILTER (
               WHERE (s.slot_type='physical' OR s.slot_type IS NULL) AND r.state='streaming'
                 AND r.sync_state IN ('sync','quorum')
           ) AS physical_synchronous,
           count(*) FILTER (WHERE s.slot_type='logical') AS logical
    FROM pg_catalog.pg_stat_replication r
    LEFT JOIN pg_catalog.pg_replication_slots s ON s.active_pid=r.pid
), receiver AS (
    SELECT count(*)>0 AS present,
           count(*) FILTER (WHERE status='streaming')>0 AS streaming
    FROM pg_catalog.pg_stat_wal_receiver
), slots AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE slot_type='physical') AS physical,
           count(*) FILTER (WHERE slot_type='logical') AS logical,
           count(*) FILTER (WHERE NOT active) AS inactive
    FROM pg_catalog.pg_replication_slots
)
SELECT senders.total AS senders_total,
       senders.physical_streaming AS senders_physical_streaming,
       senders.physical_synchronous AS senders_physical_synchronous,
       senders.logical AS senders_logical,
       receiver.present AS receiver_present, receiver.streaming AS receiver_streaming,
       slots.total AS slots_total, slots.physical AS slots_physical,
       slots.logical AS slots_logical, slots.inactive AS slots_inactive
FROM senders CROSS JOIN receiver CROSS JOIN slots
"""


def _build_status(settings: dict[str, Any], stats: dict[str, Any]) -> ReplicationStatus:
    senders = SenderStatus(
        total=stats["senders_total"], physical_streaming=stats["senders_physical_streaming"],
        physical_synchronous=stats["senders_physical_synchronous"],
        logical=stats["senders_logical"],
    )
    receiver = ReceiverStatus(
        present=stats["receiver_present"], streaming=stats["receiver_streaming"],
    )
    slots = SlotStatus(
        total=stats["slots_total"], physical=stats["slots_physical"],
        logical=stats["slots_logical"], inactive=stats["slots_inactive"],
    )
    in_recovery = settings["in_recovery"]
    conditions: tuple[tuple[WarningCode, bool], ...] = (
        ("standby_replay_paused", in_recovery and settings["replay_paused"] is True),
        ("standby_receiver_not_streaming", in_recovery and not receiver.streaming),
        ("synchronous_standby_missing", not in_recovery
         and settings["synchronous_standby_configured"] and senders.physical_synchronous == 0),
        ("session_commit_not_remote_apply", not in_recovery
         and settings["synchronous_commit"] != "remote_apply"),
        ("inactive_replication_slots", slots.inactive > 0),
    )
    return ReplicationStatus(
        evaluated_at=settings["evaluated_at"], in_recovery=in_recovery,
        transaction_read_only=settings["transaction_read_only"],
        synchronous_commit=settings["synchronous_commit"],
        synchronous_standby_configured=settings["synchronous_standby_configured"],
        primary_flush_lsn=settings["primary_flush_lsn"], received_lsn=settings["received_lsn"],
        replayed_lsn=settings["replayed_lsn"], replay_paused=settings["replay_paused"],
        senders=senders, receiver=receiver, slots=slots,
        warnings=tuple(code for code, present in conditions if present),
    )


def replication_status(url: str) -> ReplicationStatus:
    try:
        with read_admin_snapshot(url) as conn:
            settings = conn.execute(SETTINGS_QUERY).fetchone()
            stats = conn.execute(STATISTICS_QUERY).fetchone()
            if settings is None or stats is None:
                raise AdminError("replication_status_invalid_metadata")
            try:
                result = _build_status(settings, stats)
            except ValidationError:
                raise AdminError("replication_status_invalid_metadata") from None
    except psycopg.Error as exc:
        raise admin_failure(exc, False) from None
    return result


class ReplicationStatusParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_replication_status_arguments")


def main(argv: list[str]) -> None:
    parser = ReplicationStatusParser(
        prog="pg-agmemory replication-status",
        description=(
            "ADMIN-only, cluster-global read-only replication observations, without a tenant "
            "admission barrier. Live statistics are not an atomic snapshot, a health verdict, "
            "or proof of leadership, quorum, RPO, fencing, or production qualification."
        ),
        epilog=(
            "Uses PGAG_ADMIN_DATABASE_URL only. This observer session's synchronous_commit "
            "does not verify writer policy. Does not authorize promotion, admit traffic, "
            "change replication, or perform automatic remediation. Warnings do not change "
            "the success exit status."
        ),
    )
    parser.parse_args(argv)
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory replication-status: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        result = replication_status(url)
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": False}}),
            flush=True,
        )
        raise SystemExit(1) from None
    print(result.model_dump_json(), flush=True)
