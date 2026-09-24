"""Owned two-node SQL HA laboratory, never a failover or production promotion tool.

The host harness alone owns fencing and promotion. This helper provisions
synthetic fixtures, observes replication, and checks acknowledged state.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import ipaddress
import os
import platform
import re
import stat
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic import Field, ValidationError, model_validator

from pg_agmemory import __version__
from pg_agmemory.admin import AdminError
from pg_agmemory.checkpoints import Checkpoints
from pg_agmemory.commit_deadline import COMMIT_ACK_TIMEOUT_SECONDS
from pg_agmemory.database import SCHEMA_VERSION, RuntimeValidationError, migrate, validate_runtime
from pg_agmemory.effects import ToolEffects
from pg_agmemory.models import (
    CheckpointState,
    CreateCheckpoint,
    MemoryReference,
    Observe,
    PlanToolEffect,
    TransitionToolEffect,
)
from pg_agmemory.processing_recovery import (
    ProcessingRecoverySnapshot,
    StateFingerprint,
    capture_processing_state,
    compare_processing_state,
    fingerprint_tables,
)
from pg_agmemory.replication_status import ReplicationStatus, replication_status
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryError as ServiceError
from pg_agmemory.service import MemoryService, bind_identity, principal_connection
from pg_agmemory.source_access import (
    SourceAccessRequest,
    SourceIdentity,
    SourceNotice,
    source_access,
)
from pg_agmemory.transactions import CommitOutcomeUnknown, async_transaction

support_spec = importlib.util.spec_from_file_location(
    "pgag_ha_pitr_support", Path(__file__).with_name("smoke-pitr.py"),
)
assert support_spec is not None and support_spec.loader is not None
pitr = importlib.util.module_from_spec(support_spec)
support_spec.loader.exec_module(pitr)
require = pitr.require
read = pitr.read
write_new = pitr.write_new
Contract = pitr.Contract

WRITER = "synthetic-ha-writer"
READER = "synthetic-ha-source-reader"
APPLICATION = "pgag_m5_sync"
CONTENT_TABLES = pitr.CONTENT_TABLES | {
    "memory.episode_lexical": "episode_id,profile",
    "memory.checkpoint_run": "scope_id,run_id",
    "memory.checkpoint_branch": "scope_id,run_id,branch_id",
    "memory.tool_effect": "id",
    "memory.tool_effect_revision": "effect_id,revision",
    "memory.tool_effect_reference": "effect_id,source_id,source_revision",
    "memory_ops.tool_effect_identity": "scope_id,run_id,operation_id",
    "memory_ops.audit_event": "id",
}
SOURCE = SourceIdentity(
    source_system="synthetic-ha", dataset_id="owned-two-node-lab", source_subject=READER,
)
Measured = Literal[True] | None


class UncertainEvidence(Contract):
    uncertain_commit_reconciled: Literal[True]
    memory_id: UUID
    outcome_code: Literal["commit_outcome_unknown"]
    retryable: Literal[False]
    success_receipt_emitted: Literal[False]
    writer_policy_verified: Literal[True]
    writer_synchronous_commit: Literal["remote_apply"]
    statement_timeout_seconds: Literal[5]
    lock_timeout_seconds: Literal[5]
    commit_timeout_seconds: Literal[5]
    sync_rep_wait_observed: Literal[True]
    local_wal_flush_observed: Literal[True]
    local_wal_flush_scope: Literal["precommit_insert_lsn_lower_bound"]
    local_receipt_observed: Literal[True]
    local_receipt_observed_before_client_exit: Literal[False]
    client_connection_closed: Literal[True]
    commit_wait_seconds: Annotated[float, Field(ge=4.5, lt=8, allow_inf_nan=False)]
    sync_rep_observed_seconds: Annotated[float, Field(ge=4.5, lt=8, allow_inf_nan=False)]
    replay_resumed_after_client_exit: Literal[True]
    replica_state_matches: Literal[True]
    read_only_reconciliation: Literal[True]
    no_retry: Literal[True]
    production_qualified: Literal[False] = False
    network_partition_qualified: Literal[False] = False
    commit_timeout_qualified: Literal[False] = False
    automatic_failover: Literal[False] = False
    automatic_service_start: Literal[False] = False
    serving_authorized: Literal[False] = False
    effect_reexecution: Literal[False] = False

    @model_validator(mode="after")
    def observed_within_commit(self):
        if self.sync_rep_observed_seconds > self.commit_wait_seconds:
            raise ValueError("invalid_sync_rep_observation_window")
        return self


class Fixture(Contract):
    tenant_id: UUID
    scope_id: UUID
    writer_id: UUID
    reader_id: UUID
    before_id: UUID
    checkpoint_id: UUID
    run_id: UUID
    effect_id: UUID
    acknowledged_ids: Annotated[tuple[UUID, ...], Field(max_length=3)] = ()
    uncertain_id: UUID | None = None
    probe_id: UUID | None = None


class Reference(Contract):
    format: Literal["pgag-ha-reference-v2"] = "pgag-ha-reference-v2"
    service_version: str = __version__
    schema_version: Literal[22] = 22
    stage: Literal["seed", "acknowledged", "reconciled", "promoted", "post-probe"]
    fixture: Fixture
    control: pitr.Control
    source_cursor: pitr.SourceCursor
    episodes: Annotated[tuple[pitr.Episode, ...], Field(min_length=1, max_length=6)]
    content: tuple[StateFingerprint, ...]
    processing: ProcessingRecoverySnapshot
    effect_status: Literal["dispatched"]
    effect_revision: Literal[2]
    uncertain: UncertainEvidence | None = None
    serving_authorized: Literal[False] = False
    effect_reexecution: Literal[False] = False

    @model_validator(mode="after")
    def complete(self):
        if (self.service_version != __version__
                or self.processing.tenant_id != self.fixture.tenant_id):
            raise ValueError("reference_identity_mismatch")
        expected = (self.fixture.before_id, *self.fixture.acknowledged_ids)
        if self.stage == "seed":
            if self.fixture.acknowledged_ids:
                raise ValueError("unexpected_acknowledgements")
        elif len(self.fixture.acknowledged_ids) != 3:
            raise ValueError("three_acknowledgements_required")
        if self.stage in ("reconciled", "promoted", "post-probe"):
            if self.uncertain is None or self.fixture.uncertain_id != self.uncertain.memory_id:
                raise ValueError("uncertain_evidence_required")
            expected += (self.fixture.uncertain_id,)
        elif self.fixture.uncertain_id is not None or self.uncertain is not None:
            raise ValueError("unexpected_uncertainty")
        if self.stage == "post-probe":
            if self.fixture.probe_id is None:
                raise ValueError("probe_id_required")
            expected += (self.fixture.probe_id,)
        elif self.fixture.probe_id is not None:
            raise ValueError("unexpected_probe")
        if len(set(expected)) != len(expected) or {e.object_id for e in self.episodes} != set(
            expected
        ) or len(self.episodes) != len(expected):
            raise ValueError("invalid_acknowledged_episode_set")
        if tuple(item.table for item in self.content) != tuple(CONTENT_TABLES):
            raise ValueError("incomplete_canonical_state")
        if self.source_cursor.sequence != 1 or self.source_cursor.reason != "revoked":
            raise ValueError("source_cursor_changed")
        return self


class SynchronousEvidence(Contract):
    writer_policy_verified: Literal[True]
    writer_synchronous_commit: Literal["remote_apply"]
    short_pause_blocked_ack: Literal[True]
    sync_rep_wait_observed: Literal[True]
    pause_seconds: Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]
    acknowledgements: Annotated[tuple[UUID, ...], Field(min_length=3, max_length=3)]
    primary: ReplicationStatus
    standby: ReplicationStatus
    network_partition_qualified: Literal[False] = False
    commit_timeout_qualified: Literal[False] = False

    @model_validator(mode="after")
    def distinct_acknowledgements(self):
        if len(set(self.acknowledgements)) != 3:
            raise ValueError("three_distinct_acknowledgements_required")
        return self


class PreservedEvidence(Contract):
    acknowledged_state_matches: Literal[True]
    uncertain_state_matches: Literal[True]
    uncertain_id: UUID
    original_outcome_code: Literal["commit_outcome_unknown"]
    effect_state_preserved: Literal[True]
    timeline_before: Annotated[int, Field(ge=1)]
    timeline_after: Annotated[int, Field(ge=2)]
    promoted: ReplicationStatus
    serving_authorized: Literal[False] = False
    effect_reexecution: Literal[False] = False

    @model_validator(mode="after")
    def advanced(self):
        if self.timeline_after <= self.timeline_before:
            raise ValueError("timeline_not_advanced")
        return self


class ProbeEvidence(Contract):
    postpromotion_probe_verified: Literal[True]
    probe_id: UUID
    baseline_state_changed: Literal[True]
    writer_synchronous_commit: Literal["on"]
    no_synchronous_standby: Literal[True]
    serving_authorized: Literal[False] = False
    effect_reexecution: Literal[False] = False


class Report(Contract):
    format: Literal["pgag-ha-drill-v2"] = "pgag-ha-drill-v2"
    service_version: str = __version__
    api_version: Literal["v1"] = "v1"
    schema_version: Literal[22] = 22
    postgres_version_num: Literal[180006] = 180006
    pgvector_version: Literal["0.8.6"] = "0.8.6"
    status: Literal["passed", "failed"]
    failure_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")] | None
    source_destroyed: Measured = None
    fencing_verified: Measured = None
    pre_fence_promotion_rejected: Measured = None
    promotion_executed: Measured = None
    backup_verified: Measured = None
    writer_policy_verified: Measured = None
    short_pause_blocked_ack: Measured = None
    uncertain_commit_reconciled: Measured = None
    acknowledged_state_matches: Measured = None
    effect_state_preserved: Measured = None
    postpromotion_probe_verified: Measured = None
    timeline_before: Annotated[int, Field(ge=1)] | None = None
    timeline_after: Annotated[int, Field(ge=2)] | None = None
    artifact: pitr.Artifact | None = None
    synchronous: SynchronousEvidence | None = None
    uncertain: UncertainEvidence | None = None
    preserved: PreservedEvidence | None = None
    probe: ProbeEvidence | None = None
    elapsed_seconds: dict[str, pitr.Seconds]
    production_qualified: Literal[False] = False
    host_failure_domain_independent: Literal[False] = False
    network_partition_qualified: Literal[False] = False
    commit_timeout_qualified: Literal[False] = False
    automatic_failover: Literal[False] = False
    automatic_service_start: Literal[False] = False
    serving_authorized: Literal[False] = False
    effect_reexecution: Literal[False] = False

    @model_validator(mode="after")
    def truthful(self):
        if self.service_version != __version__ or not self.elapsed_seconds:
            raise ValueError("invalid_report_identity_or_timings")
        for proof, fields in (
            (self.synchronous, ("writer_policy_verified", "short_pause_blocked_ack")),
            (self.uncertain, ("uncertain_commit_reconciled",)),
            (self.preserved, ("acknowledged_state_matches", "effect_state_preserved")),
            (self.probe, ("postpromotion_probe_verified",)),
        ):
            if any(getattr(self, field) is not (True if proof is not None else None)
                   for field in fields):
                raise ValueError("unmeasured_or_inconsistent_evidence")
        if self.promotion_executed and not all((
            self.fencing_verified, self.source_destroyed, self.pre_fence_promotion_rejected,
        )):
            raise ValueError("promotion_without_verified_fence")
        if self.fencing_verified and not self.source_destroyed:
            raise ValueError("fence_without_destruction")
        if self.uncertain is not None:
            if self.synchronous is None:
                raise ValueError("uncertainty_before_synchronous_check")
            if self.uncertain.memory_id in self.synchronous.acknowledgements:
                raise ValueError("uncertain_memory_cannot_be_acknowledged")
        if self.preserved is not None:
            if self.uncertain is None or (
                self.preserved.uncertain_id != self.uncertain.memory_id
                or self.preserved.original_outcome_code != self.uncertain.outcome_code
            ):
                raise ValueError("uncertainty_not_preserved")
            if not self.promotion_executed or (
                self.timeline_before, self.timeline_after
            ) != (self.preserved.timeline_before, self.preserved.timeline_after):
                raise ValueError("invalid_promotion_evidence")
        elif self.timeline_after is not None:
            raise ValueError("unmeasured_timeline")
        if self.probe is not None and self.preserved is None:
            raise ValueError("probe_before_preservation_check")
        if self.probe is not None and self.uncertain is not None:
            if self.probe.probe_id == self.uncertain.memory_id or (
                self.synchronous is not None
                and self.probe.probe_id in self.synchronous.acknowledgements
            ):
                raise ValueError("probe_identity_reused")
        if self.status == "passed":
            if self.failure_code is not None or not all((
                self.source_destroyed, self.fencing_verified, self.pre_fence_promotion_rejected,
                self.promotion_executed, self.backup_verified, self.artifact is not None,
                self.synchronous is not None, self.uncertain is not None,
                self.preserved is not None, self.probe is not None,
            )):
                raise ValueError("incomplete_pass_evidence")
        elif self.failure_code is None:
            raise ValueError("failure_code_required")
        return self


def owned_environment():
    require(platform.system() == "Linux", "linux_runtime_required")
    require(SCHEMA_VERSION == 22, "schema_version_mismatch")
    require(not any(os.environ.get(key) for key in (
        "PGAG_DATABASE_URL", "PGAG_ADMIN_DATABASE_URL", "PGHOST", "PGSERVICE",
    )), "external_database_target_forbidden")
    run = os.environ.get("PGAG_HA_OWNED_RUN", "")
    require(re.fullmatch(r"pgag-ha-[0-9]+-[0-9]+-[0-9a-f]{16}", run), "owned_harness_required")
    require(os.environ.get("PGAG_HA_ALLOW_OWNED_PROMOTION") == "1", "explicit_opt_in_required")
    directory = Path("/drill")
    mode = directory.lstat().st_mode
    require(stat.S_ISDIR(mode) and stat.S_IMODE(mode) == 0o700, "private_directory_required")
    engine = os.environ.get("PGAG_HA_ENGINE")
    require(engine in ("container", "docker"), "owned_harness_required")
    urls = []
    for suffix, key in (("primary", "PGAG_HA_PRIMARY_HOST"), ("standby", "PGAG_HA_STANDBY_HOST")):
        host = os.environ.get(key, "")
        if not host:
            urls.append(None)
            continue
        if engine == "docker":
            require(host == run + "-" + suffix, "owned_database_required")
        else:
            address = ipaddress.ip_address(host)
            require(isinstance(address, ipaddress.IPv4Address) and address.is_private
                    and not address.is_loopback and not address.is_unspecified
                    and not address.is_multicast and not address.is_link_local,
                    "owned_database_required")
        urls.append(make_conninfo(
            host=host, port=5432, user="postgres", password=secret("POSTGRES_PASSWORD"),
            dbname="pgag_ha", connect_timeout=5,
            options="-c statement_timeout=5000 -c lock_timeout=5000",
        ))
    return directory, *urls


def secret(name):
    value = os.environ.get(name, "")
    require(re.fullmatch(r"[0-9a-f]{64}", value), "owned_credentials_required")
    return value


def runtime_url(url):
    return make_conninfo(**(conninfo_to_dict(url) | {
        "user": "pgag_ha_writer", "password": secret("HA_WRITER_PASSWORD"),
    }))


async def writer_policy(conn, expected):
    row = await (await conn.execute(
        "SELECT current_setting('synchronous_commit') AS policy, "
        "current_setting('statement_timeout') AS timeout, "
        "current_setting('lock_timeout') AS lock_timeout, "
        "rolsuper OR rolbypassrls OR rolreplication OR rolcreatedb OR rolcreaterole "
        "AS privileged "
        "FROM pg_roles WHERE rolname=current_user"
    )).fetchone()
    require(row["policy"] == expected and not row["privileged"]
            and row["timeout"] in ("5s", "5000ms")
            and row["lock_timeout"] in ("5s", "5000ms")
            and COMMIT_ACK_TIMEOUT_SECONDS == 5.0, "writer_policy_not_verified")


@asynccontextmanager
async def service(url, policy="on", subject=WRITER):
    async with principal_connection(runtime_url(url), subject) as (conn, identity):
        async with async_transaction(conn):
            await bind_identity(conn, subject, identity)
            await writer_policy(conn, policy)
            yield MemoryService(conn, identity)


def observation(scope_id, name):
    return Observe(
        scope_id=scope_id, source_namespace="synthetic-ha", source_event_id=name,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        content=f"Synthetic HA control episode: {name}.",
        consent_reference="owned-synthetic-ha-lab-only",
    )


async def observe(url, scope_id, name, policy="on"):
    async with service(url, policy) as memory:
        receipt = await memory.observe(observation(scope_id, name), "ha-" + name)
    return UUID(receipt["memory_id"])


def capture(url, stage, fixture, recovery=False, uncertain=None):
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        require(conn.execute("SHOW server_version_num").fetchone()["server_version_num"]
                == "180006", "postgres_version_mismatch")
        require(conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                .fetchone() == {"extversion": "0.8.6"}, "pgvector_version_mismatch")
        tenant = fixture.tenant_id
        episodes = tuple(pitr.Episode(
            object_id=row["id"], content_sha256=hashlib.sha256(row["content"].encode()).hexdigest(),
        ) for row in conn.execute(
            "SELECT id,content FROM memory.episode WHERE tenant_id=%s ORDER BY id LIMIT 7",
            (tenant,),
        ))
        cursor = conn.execute(
            "SELECT sequence,decision,reason FROM memory_ops.source_access_state "
            "WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s",
            (tenant, fixture.scope_id, fixture.reader_id),
        ).fetchone()
        effect = conn.execute(
            "SELECT r.status,e.current_revision AS revision FROM memory.tool_effect e "
            "JOIN memory.tool_effect_revision r ON r.tenant_id=e.tenant_id AND r.effect_id=e.id "
            "AND r.revision=e.current_revision WHERE e.tenant_id=%s AND e.id=%s",
            (tenant, fixture.effect_id),
        ).fetchone()
        key = bytes(conn.execute(
            "SELECT dedup_secret FROM memory.tenant WHERE id=%s", (tenant,),
        ).fetchone()["dedup_secret"])
        content = fingerprint_tables(conn, tenant, key, CONTENT_TABLES)
        control = pitr.control(conn, recovery)
    return Reference(
        stage=stage, fixture=fixture, control=control, source_cursor=pitr.SourceCursor(**cursor),
        episodes=episodes, content=content, processing=capture_processing_state(url, tenant),
        effect_status=effect["status"], effect_revision=effect["revision"], uncertain=uncertain,
    )


async def seed(directory, url):
    require(url is not None, "owned_primary_required")
    migrate(url)
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL(
            "CREATE ROLE pgag_ha_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS PASSWORD {} IN ROLE pgag_runtime"
        ).format(sql.Literal(secret("HA_WRITER_PASSWORD"))))
        conn.execute(sql.SQL(
            "CREATE ROLE pgag_ha_replication LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT REPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Literal(secret("HA_REPLICATION_PASSWORD"))))
    await validate_runtime(runtime_url(url))
    ids = {key: uuid4() for key in ("tenant_id", "scope_id", "writer_id", "reader_id", "run_id")}
    with psycopg.connect(url) as conn:
        conn.execute("INSERT INTO memory.tenant(id,dedup_secret) VALUES (%s,%s)",
                     (ids["tenant_id"], os.urandom(32)))
        conn.execute("INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)",
                     (ids["tenant_id"], ids["scope_id"]))
        for key, subject in (("writer_id", WRITER), ("reader_id", READER)):
            conn.execute(
                "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
                (ids["tenant_id"], ids[key], subject),
            )
    with scope_access(url, ScopeAccessRequest(
        operation="set", tenant_id=ids["tenant_id"], scope_id=ids["scope_id"],
        principal_id=ids["writer_id"], expected_access_epoch=1,
        permissions=("admin",), no_expiry=True,
    )) as access:
        epoch = access.access_epoch
    with source_access(url, SourceAccessRequest(
        operation="bind", tenant_id=ids["tenant_id"], scope_id=ids["scope_id"],
        principal_id=ids["reader_id"], expected_access_epoch=epoch, source=SOURCE,
    )) as access:
        epoch = access.access_epoch
    with source_access(url, SourceAccessRequest(
        operation="apply", tenant_id=ids["tenant_id"], scope_id=ids["scope_id"],
        principal_id=ids["reader_id"], expected_access_epoch=epoch,
        notice=SourceNotice(source=SOURCE, sequence=1, decision="deny", reason="revoked"),
    )):
        pass
    ids["before_id"] = await observe(url, ids["scope_id"], "before-basebackup")
    async with service(url) as memory:
        checkpoint = await Checkpoints(memory).create(CreateCheckpoint(
            scope_id=ids["scope_id"], run_id=ids["run_id"], branch_id=uuid4(), expected_head=None,
            harness_id="synthetic-ha", harness_version="1", event_watermark=1,
            state=CheckpointState(goal="Preserve acknowledged state without replaying effects"),
            memory_refs=[MemoryReference(memory_id=ids["before_id"])],
        ), "ha-checkpoint")
        ids["checkpoint_id"] = UUID(checkpoint["checkpoint_id"])
        effects = ToolEffects(memory)
        planned = await effects.plan(PlanToolEffect(
            scope_id=ids["scope_id"], run_id=ids["run_id"], operation_id=uuid4(),
            tool_name="synthetic-never-executed", action_hash="a" * 64,
            memory_refs=[MemoryReference(memory_id=ids["before_id"])],
        ), "ha-planned-effect")
        ids["effect_id"] = UUID(planned["memory_id"])
        await effects.transition(ids["effect_id"], TransitionToolEffect(
            expected_revision=1, status="dispatched",
            reason="Synthetic metadata only; no external action executed",
        ), "ha-dispatched-effect")
    write_new(directory, "seed.json", capture(url, "seed", Fixture(**ids)))
    params = conninfo_to_dict(url)
    replication = make_conninfo(
        host=params["host"], port=5432, user="pgag_ha_replication",
        password=secret("HA_REPLICATION_PASSWORD"), application_name=APPLICATION, connect_timeout=5,
    )
    escaped = replication.replace("\\", "\\\\").replace("'", "''")
    fd = os.open(directory / ".standby.conf",
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(f"primary_conninfo = '{escaped}'\n")


def require_pair(primary, standby, synchronous):
    require(not primary.in_recovery and primary.senders.total == 1
            and primary.senders.physical_streaming == 1 and primary.senders.logical == 0
            and standby.in_recovery and standby.receiver.present and standby.receiver.streaming
            and standby.replay_paused is False, "physical_streaming_pair_required")
    if synchronous:
        require(primary.synchronous_commit == "remote_apply"
                and primary.synchronous_standby_configured
                and primary.senders.physical_synchronous == 1, "synchronous_standby_required")


def wait_pair(primary_url, standby_url, synchronous):
    started = time.monotonic()
    while True:
        primary = replication_status(primary_url)
        standby = replication_status(standby_url)
        try:
            require_pair(primary, standby, synchronous)
            return primary, standby
        except pitr.DrillError:
            require(time.monotonic() - started < 90, "streaming_pair_unavailable")
            time.sleep(0.2)


async def paused_ack(primary_url, standby_url, fixture):
    async with principal_connection(runtime_url(primary_url), WRITER) as (writer, identity):
        await writer_policy(writer, "remote_apply")
        async with await psycopg.AsyncConnection.connect(
            primary_url, autocommit=True, row_factory=dict_row,
        ) as admin, await psycopg.AsyncConnection.connect(
            standby_url, autocommit=True, row_factory=dict_row,
        ) as standby:
            async def commit_once():
                async with async_transaction(writer):
                    await bind_identity(writer, WRITER, identity)
                    await writer_policy(writer, "remote_apply")
                    receipt = await MemoryService(writer, identity).observe(
                        observation(fixture.scope_id, "short-pause-ack"), "ha-short-pause-ack",
                    )
                return UUID(receipt["memory_id"])

            task = None
            blocked = False
            began = time.monotonic()
            try:
                await standby.execute("SELECT pg_wal_replay_pause()")
                while time.monotonic() - began < 0.3:
                    row = await (await standby.execute(
                        "SELECT pg_get_wal_replay_pause_state() AS state"
                    )).fetchone()
                    if row["state"] == "paused":
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise pitr.DrillError("short_pause_not_established")
                task = asyncio.create_task(commit_once())
                while time.monotonic() - began < 0.8:
                    row = await (await admin.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s",
                        (writer.info.backend_pid,),
                    )).fetchone()
                    if row and row["wait_event"] == "SyncRep" and not task.done():
                        await asyncio.sleep(0.075)
                        blocked = not task.done()
                        break
                    if task.done():
                        break
                    await asyncio.sleep(0.01)
            finally:
                # Never cancel a COMMIT, retry a write, or qualify cancellation/partition behavior.
                await standby.execute("SELECT pg_wal_replay_resume()")
                elapsed = time.monotonic() - began
                if task is not None:
                    acknowledged = await task
            require(blocked and task is not None and 0 < elapsed < 1, "short_pause_not_qualified")
            return acknowledged, elapsed


async def synchronous(directory, primary_url, standby_url):
    require(primary_url is not None and standby_url is not None, "owned_pair_required")
    seed_state = read(directory, "seed.json", Reference)
    wait_pair(primary_url, standby_url, False)
    candidate = capture(standby_url, "seed", seed_state.fixture, recovery=True)
    check_preserved(seed_state, candidate, promoted=False)
    with psycopg.connect(primary_url, autocommit=True) as conn:
        conn.execute("ALTER SYSTEM SET synchronous_standby_names = 'FIRST 1 (pgag_m5_sync)'")
        conn.execute("ALTER SYSTEM SET synchronous_commit = 'remote_apply'")
        conn.execute("SELECT pg_reload_conf()")
    primary, standby = wait_pair(primary_url, standby_url, True)
    with psycopg.connect(primary_url, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT application_name,state,sync_state FROM pg_stat_replication "
            "WHERE usename='pgag_ha_replication'"
        ).fetchall()
        require(
            row == [{"application_name": APPLICATION, "state": "streaming", "sync_state": "sync"}],
            "owned_synchronous_sender_required",
        )
    acknowledged = []
    for name in ("synchronous-ack-one", "synchronous-ack-two"):
        acknowledged.append(await observe(primary_url, seed_state.fixture.scope_id,
                                          name, "remote_apply"))
    paused_id, pause_seconds = await paused_ack(primary_url, standby_url, seed_state.fixture)
    acknowledged.append(paused_id)
    fixture = seed_state.fixture.model_copy(update={"acknowledged_ids": tuple(acknowledged)})
    reference = capture(primary_url, "acknowledged", fixture)
    replica = capture(standby_url, "acknowledged", fixture, recovery=True)
    check_preserved(reference, replica, promoted=False)
    primary, standby = wait_pair(primary_url, standby_url, True)
    write_new(directory, "acknowledged.json", reference)
    write_new(directory, "synchronous.json", SynchronousEvidence(
        writer_policy_verified=True, writer_synchronous_commit="remote_apply",
        short_pause_blocked_ack=True, sync_rep_wait_observed=True, pause_seconds=pause_seconds,
        acknowledgements=tuple(acknowledged), primary=primary, standby=standby,
    ))


def read_only_url(url):
    params = conninfo_to_dict(url)
    return make_conninfo(**(params | {
        "options": params.get("options", "") + " -c default_transaction_read_only=on",
    }))


async def uncertain_state(conn, fixture, memory_id):
    row = await (await conn.execute(
        "SELECT "
        "(SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM memory.episode e "
        " WHERE e.tenant_id=%s AND e.id=%s) AS episodes, "
        "(SELECT jsonb_agg(to_jsonb(i) ORDER BY i.principal_id,i.operation,i.key_digest) "
        " FROM memory_ops.idempotency i WHERE i.tenant_id=%s "
        " AND i.result->>'memory_id'=%s) AS receipts, "
        "(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM memory_ops.audit_event a "
        " WHERE a.tenant_id=%s AND a.target_id=%s) AS audits",
        (fixture.tenant_id, memory_id, fixture.tenant_id, str(memory_id),
         fixture.tenant_id, memory_id),
    )).fetchone()
    if row is None or any(row[name] is None for name in ("episodes", "receipts", "audits")):
        return None
    require(all(len(row[name]) == 1 for name in ("episodes", "receipts", "audits")),
            "uncertain_state_not_unique")
    receipt, audit = row["receipts"][0], row["audits"][0]
    require(receipt["operation"] == "observe"
            and receipt["principal_id"] == str(fixture.writer_id)
            and audit["action"] == "observe"
            and audit["principal_id"] == str(fixture.writer_id)
            and row["episodes"][0]["scope_id"] == str(fixture.scope_id),
            "uncertain_state_identity_mismatch")
    return row


async def resume_replay(standby_url):
    # A fresh bounded connection makes cleanup independent of a failed observer.
    async with asyncio.timeout(12):
        async with await psycopg.AsyncConnection.connect(
            standby_url, autocommit=True, row_factory=dict_row,
        ) as conn:
            await conn.execute("SELECT pg_wal_replay_resume()")
            row = await (await conn.execute(
                "SELECT pg_get_wal_replay_pause_state() AS state"
            )).fetchone()
            require(row == {"state": "not paused"}, "replay_resume_not_verified")


async def paused_uncertain(primary_url, standby_url, fixture):
    async with principal_connection(runtime_url(primary_url), WRITER) as (writer, identity):
        await writer_policy(writer, "remote_apply")
        pid = writer.info.backend_pid
        async with await psycopg.AsyncConnection.connect(
            read_only_url(primary_url), autocommit=True, row_factory=dict_row,
        ) as admin, await psycopg.AsyncConnection.connect(
            standby_url, autocommit=True, row_factory=dict_row,
        ) as standby:
            receipt = None
            commit_started = None
            client_exited = None
            first_sync_rep = None
            last_sync_rep = None
            insert_lsn = None
            local_wal_flushed = False
            task = None
            pending = None

            async def commit_once():
                nonlocal receipt, insert_lsn, commit_started, client_exited
                try:
                    async with async_transaction(writer):
                        await bind_identity(writer, WRITER, identity)
                        await writer_policy(writer, "remote_apply")
                        receipt = await MemoryService(writer, identity).observe(
                            observation(fixture.scope_id, "paused-uncertain"),
                            "ha-paused-uncertain",
                        )
                        row = await (await writer.execute(
                            "SELECT pg_current_wal_insert_lsn() AS lsn"
                        )).fetchone()
                        insert_lsn = row["lsn"]
                        # Exclude authentication/body work, immediately before the guard exits.
                        commit_started = time.monotonic()
                except CommitOutcomeUnknown as exc:
                    client_exited = time.monotonic()
                    require(commit_started is not None and receipt is not None
                            and exc.local_committed is None and writer.closed,
                            "watchdog_unknown_not_verified")
                    return UUID(receipt["memory_id"]), client_exited - commit_started
                raise pitr.DrillError("uncertain_commit_unexpectedly_acknowledged")

            try:
                await standby.execute("SELECT pg_wal_replay_pause()")
                paused_by = time.monotonic() + 5
                while True:
                    row = await (await standby.execute(
                        "SELECT pg_get_wal_replay_pause_state() AS state"
                    )).fetchone()
                    if row == {"state": "paused"}:
                        break
                    require(time.monotonic() < paused_by, "uncertain_pause_not_established")
                    await asyncio.sleep(0.02)
                task = asyncio.create_task(commit_once())
                body_deadline = time.monotonic() + 15
                while not task.done():
                    require(time.monotonic() < (
                        commit_started + 8 if commit_started is not None else body_deadline
                    ), "uncertain_client_exit_not_bounded")
                    row = await (await admin.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (pid,),
                    )).fetchone()
                    if row and row["wait_event"] == "SyncRep" and not task.done():
                        now = time.monotonic()
                        if first_sync_rep is None:
                            first_sync_rep = now
                        last_sync_rep = now
                        if not local_wal_flushed and insert_lsn is not None:
                            # This is a global pre-COMMIT lower bound, not a commit-record
                            # watermark or replication proof. SyncRep is observed separately;
                            # ProcArray still hides the exact receipt until the wait ends.
                            flushed = await (await admin.execute(
                                "SELECT pg_current_wal_flush_lsn() >= %s::pg_lsn AS flushed",
                                (insert_lsn,),
                            )).fetchone()
                            require(not task.done() and flushed == {"flushed": True},
                                    "local_wal_not_flushed_before_exit")
                            local_wal_flushed = True
                    await asyncio.sleep(0.02)
                memory_id, commit_elapsed = await task
                require(4.5 <= commit_elapsed < 8 and first_sync_rep is not None
                        and last_sync_rep is not None
                        and 4.5 <= last_sync_rep - first_sync_rep < 8
                        and local_wal_flushed and client_exited is not None
                        and writer.closed, "uncertain_deadline_not_qualified")
                paused = await (await standby.execute(
                    "SELECT pg_get_wal_replay_pause_state() AS state"
                )).fetchone()
                require(paused == {"state": "paused"}, "replay_resumed_before_client_exit")
            except BaseException as exc:
                pending = exc
                raise
            finally:
                cleanup_error = None
                try:
                    await resume_replay(standby_url)
                except BaseException as exc:
                    cleanup_error = exc
                try:
                    if task is not None:
                        done, _ = await asyncio.wait((task,), timeout=12)
                        if not done:
                            # Cleanup only, never evidence: no backend cancellation.
                            writer.pgconn.finish()
                            done, _ = await asyncio.wait((task,), timeout=2)
                        require(bool(done), "uncertain_writer_cleanup_failed")
                        task_error = task.exception()
                        if (pending is not None and task_error is not None
                                and task_error is not pending):
                            pending.add_note("uncertain_writer_failed_during_cleanup")
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
                    else:
                        cleanup_error.add_note("uncertain_writer_cleanup_failed")
                if cleanup_error is not None:
                    if pending is None:
                        raise cleanup_error
                    pending.add_note("uncertain_cleanup_failed")
            return memory_id, commit_elapsed, last_sync_rep - first_sync_rep, receipt


def check_uncertain_delta(before, after):
    require(after.fixture.acknowledged_ids == before.fixture.acknowledged_ids
            and after.fixture.uncertain_id not in (
                before.fixture.before_id, *before.fixture.acknowledged_ids,
            ), "uncertain_identity_reused")
    previous = {episode.object_id: episode for episode in before.episodes}
    require(all(previous.get(episode.object_id) == episode for episode in after.episodes
                if episode.object_id != after.fixture.uncertain_id),
            "uncertain_write_changed_acknowledged_content")
    growing = {
        "memory.episode", "memory.episode_lexical", "memory_ops.audit_event", "memory.object",
        "memory_ops.source_event", "memory_ops.idempotency",
    }
    for old, new in zip(
        (*before.content, *before.processing.tables),
        (*after.content, *after.processing.tables), strict=True,
    ):
        if old.table in growing:
            require(new.rows == old.rows + 1, "uncertain_write_delta_mismatch")
        else:
            require(old == new, "uncertain_write_changed_unrelated_state")
    require(before.source_cursor == after.source_cursor
            and before.effect_status == after.effect_status
            and before.effect_revision == after.effect_revision
            and before.processing.lineage == after.processing.lineage
            and before.processing.access_epoch == after.processing.access_epoch
            and before.processing.deletion_epoch == after.processing.deletion_epoch,
            "uncertain_write_changed_unrelated_state")


async def uncertain(directory, primary_url, standby_url):
    require(primary_url is not None and standby_url is not None, "owned_pair_required")
    reference = read(directory, "acknowledged.json", Reference)
    require(reference.stage == "acknowledged", "acknowledged_reference_required")
    wait_pair(primary_url, standby_url, True)
    check_preserved(
        reference, capture(primary_url, "acknowledged", reference.fixture), promoted=False,
    )
    check_preserved(reference, capture(
        standby_url, "acknowledged", reference.fixture, recovery=True,
    ), promoted=False)
    memory_id, elapsed, observed, receipt = await paused_uncertain(
        primary_url, standby_url, reference.fixture,
    )
    async with asyncio.timeout(30):
        async with await psycopg.AsyncConnection.connect(
            read_only_url(primary_url), autocommit=True, row_factory=dict_row,
        ) as primary, await psycopg.AsyncConnection.connect(
            read_only_url(standby_url), autocommit=True, row_factory=dict_row,
        ) as standby:
            local_state = None
            while True:
                primary_state = await uncertain_state(primary, reference.fixture, memory_id)
                replica_state = await uncertain_state(standby, reference.fixture, memory_id)
                if primary_state is not None:
                    require(primary_state["receipts"][0]["result"] == receipt,
                            "uncertain_local_receipt_mismatch")
                    if local_state is None:
                        local_state = primary_state
                    require(primary_state == local_state, "uncertain_primary_state_changed")
                if local_state is not None and replica_state is not None:
                    require(replica_state == local_state, "uncertain_replica_state_mismatch")
                    break
                await asyncio.sleep(0.05)
    fixture = reference.fixture.model_copy(update={"uncertain_id": memory_id})
    evidence = UncertainEvidence(
        uncertain_commit_reconciled=True,
        memory_id=memory_id, outcome_code=CommitOutcomeUnknown.code,
        retryable=False, success_receipt_emitted=False,
        writer_policy_verified=True, writer_synchronous_commit="remote_apply",
        statement_timeout_seconds=5, lock_timeout_seconds=5, commit_timeout_seconds=5,
        sync_rep_wait_observed=True, local_wal_flush_observed=True,
        local_wal_flush_scope="precommit_insert_lsn_lower_bound",
        local_receipt_observed=True, local_receipt_observed_before_client_exit=False,
        client_connection_closed=True,
        commit_wait_seconds=elapsed, sync_rep_observed_seconds=observed,
        replay_resumed_after_client_exit=True, replica_state_matches=True,
        read_only_reconciliation=True, no_retry=True,
    )
    reconciled = capture(primary_url, "reconciled", fixture, uncertain=evidence)
    replica = capture(standby_url, "reconciled", fixture, recovery=True, uncertain=evidence)
    check_uncertain_delta(reference, reconciled)
    check_preserved(reconciled, replica, promoted=False)
    wait_pair(primary_url, standby_url, True)
    write_new(directory, "reconciled.json", reconciled)
    write_new(directory, "uncertain.json", evidence)


def check_preserved(reference, candidate, promoted):
    require(reference.fixture == candidate.fixture, "acknowledged_identity_mismatch")
    require(reference.uncertain == candidate.uncertain, "original_uncertainty_changed")
    require(reference.control.system_identifier == candidate.control.system_identifier,
            "physical_cluster_identity_mismatch")
    require(candidate.control.timeline > reference.control.timeline if promoted else
            candidate.control.timeline == reference.control.timeline, "timeline_mismatch")
    check = compare_processing_state(candidate.processing, reference.processing)
    require(check.processing_state_matches and not check.restore_authorized,
            "acknowledged_processing_mismatch")
    require(candidate.episodes == reference.episodes and candidate.content == reference.content,
            "acknowledged_content_mismatch")
    require(candidate.source_cursor == reference.source_cursor
            and candidate.effect_status == reference.effect_status
            and candidate.effect_revision == reference.effect_revision,
            "source_or_effect_state_mismatch")


def reconciled_reference(directory):
    reference = read(directory, "reconciled.json", Reference)
    require(reference.stage == "reconciled", "reconciled_reference_required")
    require(reference.uncertain == read(directory, "uncertain.json", UncertainEvidence),
            "original_uncertainty_changed")
    return reference


def prefence(directory, standby_url):
    require(standby_url is not None, "owned_standby_required")
    reference = reconciled_reference(directory)
    candidate = capture(standby_url, "reconciled", reference.fixture, recovery=True,
                        uncertain=reference.uncertain)
    check_preserved(reference, candidate, promoted=False)
    with psycopg.connect(standby_url, row_factory=dict_row, autocommit=True) as conn:
        row = conn.execute(
            "SELECT pg_is_in_recovery() AS recovering, "
            "current_setting('transaction_read_only') AS read_only"
        ).fetchone()
        require(row == {"recovering": True, "read_only": "on"}, "pre_fence_candidate_not_read_only")


async def verify(directory, standby_url):
    require(standby_url is not None, "owned_standby_required")
    reference = reconciled_reference(directory)
    require(pitr.inspect_tar(directory / "basebackup.tar", pitr.MAX_BACKUP_BYTES)
            == read(directory, "artifact.json", pitr.Artifact), "backup_artifact_changed")
    promoted = replication_status(standby_url)
    require(not promoted.in_recovery and not promoted.synchronous_standby_configured
            and promoted.senders.total == 0 and promoted.synchronous_commit == "on",
            "promoted_node_must_be_explicitly_degraded")
    with psycopg.connect(standby_url, autocommit=True) as conn:
        conn.execute("CHECKPOINT")
    candidate = capture(standby_url, "promoted", reference.fixture, uncertain=reference.uncertain)
    check_preserved(reference, candidate, promoted=True)
    write_new(directory, "promoted.json", candidate)
    write_new(directory, "preserved.json", PreservedEvidence(
        acknowledged_state_matches=True, effect_state_preserved=True,
        uncertain_state_matches=True, uncertain_id=reference.fixture.uncertain_id,
        original_outcome_code=reference.uncertain.outcome_code,
        timeline_before=reference.control.timeline, timeline_after=candidate.control.timeline,
        promoted=promoted,
    ))
    probe_id = await observe(standby_url, reference.fixture.scope_id, "postpromotion-named-probe")
    async with service(standby_url) as memory:
        effect = await ToolEffects(memory).get(reference.fixture.effect_id)
        require(effect["status"] == "dispatched" and effect["revision"] == 2,
                "effect_state_changed")
        visible = await (await memory.conn.execute(
            "SELECT id FROM memory.episode WHERE id=%s", (probe_id,),
        )).fetchone()
        require(visible and visible["id"] == probe_id, "native_probe_not_readable")
    async with service(standby_url, subject=READER) as memory:
        denied = await (await memory.conn.execute(
            "SELECT count(*) AS count FROM memory.episode"
        )).fetchone()
        require(denied["count"] == 0, "source_reader_no_longer_denied")
    fixture = reference.fixture.model_copy(update={"probe_id": probe_id})
    after = capture(standby_url, "post-probe", fixture, uncertain=reference.uncertain)
    check = compare_processing_state(after.processing, reference.processing)
    require(not check.processing_state_matches and not check.restore_authorized,
            "probe_did_not_change_baseline")
    require(after.source_cursor == reference.source_cursor, "probe_changed_source_cursor")
    old = {episode.object_id: episode for episode in reference.episodes}
    require(all(old.get(episode.object_id) == episode for episode in after.episodes
                if episode.object_id != probe_id), "probe_changed_acknowledged_content")
    write_new(directory, "post-probe.json", after)
    write_new(directory, "probe.json", ProbeEvidence(
        postpromotion_probe_verified=True, probe_id=probe_id, baseline_state_changed=True,
        writer_synchronous_commit="on", no_synchronous_standby=True,
    ))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=(
        "seed", "artifact", "synchronous", "uncertain", "prefence", "verify",
    ))
    args = parser.parse_args(argv)
    directory = None
    try:
        directory, primary_url, standby_url = owned_environment()
        if args.stage == "seed":
            asyncio.run(seed(directory, primary_url))
        elif args.stage == "artifact":
            read(directory, "backup.json", pitr.Backup)
            write_new(directory, "artifact.json", pitr.inspect_tar(
                directory / "basebackup.tar", pitr.MAX_BACKUP_BYTES,
            ))
        elif args.stage == "synchronous":
            asyncio.run(synchronous(directory, primary_url, standby_url))
        elif args.stage == "uncertain":
            asyncio.run(uncertain(directory, primary_url, standby_url))
        elif args.stage == "prefence":
            prefence(directory, standby_url)
        else:
            asyncio.run(verify(directory, standby_url))
    except (
        AdminError, RuntimeValidationError, ServiceError, CommitOutcomeUnknown,
        psycopg.Error, ValidationError,
        OSError, RuntimeError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
    ) as exc:
        code = str(exc) if isinstance(exc, pitr.DrillError) else "ha_stage_failed"
        if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) is None:
            code = "ha_stage_failed"
        if directory is not None:
            try:
                write_new(directory, "failure.json", pitr.Failure(failure_code=code))
            except (OSError, ValueError):
                code = "failure_record_write_failed"
        print(code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
