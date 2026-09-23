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
}
SOURCE = SourceIdentity(
    source_system="synthetic-ha", dataset_id="owned-two-node-lab", source_subject=READER,
)
Measured = Literal[True] | None


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
    probe_id: UUID | None = None


class Reference(Contract):
    format: Literal["pgag-ha-reference-v1"] = "pgag-ha-reference-v1"
    service_version: str = __version__
    schema_version: Literal[21] = 21
    stage: Literal["seed", "acknowledged", "promoted", "post-probe"]
    fixture: Fixture
    control: pitr.Control
    source_cursor: pitr.SourceCursor
    episodes: Annotated[tuple[pitr.Episode, ...], Field(min_length=1, max_length=5)]
    content: tuple[StateFingerprint, ...]
    processing: ProcessingRecoverySnapshot
    effect_status: Literal["dispatched"]
    effect_revision: Literal[2]
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


class PreservedEvidence(Contract):
    acknowledged_state_matches: Literal[True]
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
    format: Literal["pgag-ha-drill-v1"] = "pgag-ha-drill-v1"
    service_version: str = __version__
    api_version: Literal["v1"] = "v1"
    schema_version: Literal[21] = 21
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
    acknowledged_state_matches: Measured = None
    effect_state_preserved: Measured = None
    postpromotion_probe_verified: Measured = None
    timeline_before: Annotated[int, Field(ge=1)] | None = None
    timeline_after: Annotated[int, Field(ge=2)] | None = None
    artifact: pitr.Artifact | None = None
    synchronous: SynchronousEvidence | None = None
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
        if self.preserved is not None:
            if not self.promotion_executed or (
                self.timeline_before, self.timeline_after
            ) != (self.preserved.timeline_before, self.preserved.timeline_after):
                raise ValueError("invalid_promotion_evidence")
        elif self.timeline_after is not None:
            raise ValueError("unmeasured_timeline")
        if self.probe is not None and self.preserved is None:
            raise ValueError("probe_before_preservation_check")
        if self.status == "passed":
            if self.failure_code is not None or not all((
                self.source_destroyed, self.fencing_verified, self.pre_fence_promotion_rejected,
                self.promotion_executed, self.backup_verified, self.artifact is not None,
                self.synchronous is not None, self.preserved is not None, self.probe is not None,
            )):
                raise ValueError("incomplete_pass_evidence")
        elif self.failure_code is None:
            raise ValueError("failure_code_required")
        return self


def owned_environment():
    require(platform.system() == "Linux", "linux_runtime_required")
    require(SCHEMA_VERSION == 21, "schema_version_mismatch")
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
        "rolsuper OR rolbypassrls OR rolreplication AS privileged "
        "FROM pg_roles WHERE rolname=current_user"
    )).fetchone()
    require(row["policy"] == expected and not row["privileged"]
            and row["timeout"] in ("5s", "5000ms"), "writer_policy_not_verified")


@asynccontextmanager
async def service(url, policy="on", subject=WRITER):
    async with principal_connection(runtime_url(url), subject) as (conn, identity):
        notices = []
        conn.add_notice_handler(lambda notice: notices.append(notice.sqlstate))
        async with conn.transaction():
            await bind_identity(conn, subject, identity)
            await writer_policy(conn, policy)
            yield MemoryService(conn, identity)
        require(not notices, "commit_notice_outcome_unqualified")


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


def capture(url, stage, fixture, recovery=False):
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
            "SELECT id,content FROM memory.episode WHERE tenant_id=%s ORDER BY id LIMIT 6",
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
        effect_status=effect["status"], effect_revision=effect["revision"],
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
        notices = []
        writer.add_notice_handler(lambda notice: notices.append(notice.sqlstate))
        async with await psycopg.AsyncConnection.connect(
            primary_url, autocommit=True, row_factory=dict_row,
        ) as admin, await psycopg.AsyncConnection.connect(
            standby_url, autocommit=True, row_factory=dict_row,
        ) as standby:
            async def commit_once():
                async with writer.transaction():
                    await bind_identity(writer, WRITER, identity)
                    await writer_policy(writer, "remote_apply")
                    receipt = await MemoryService(writer, identity).observe(
                        observation(fixture.scope_id, "short-pause-ack"), "ha-short-pause-ack",
                    )
                require(not notices, "commit_notice_outcome_unqualified")
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


def check_preserved(reference, candidate, promoted):
    require(reference.fixture == candidate.fixture, "acknowledged_identity_mismatch")
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


def prefence(directory, standby_url):
    require(standby_url is not None, "owned_standby_required")
    reference = read(directory, "acknowledged.json", Reference)
    candidate = capture(standby_url, "acknowledged", reference.fixture, recovery=True)
    check_preserved(reference, candidate, promoted=False)
    with psycopg.connect(standby_url, row_factory=dict_row, autocommit=True) as conn:
        row = conn.execute(
            "SELECT pg_is_in_recovery() AS recovering, "
            "current_setting('transaction_read_only') AS read_only"
        ).fetchone()
        require(row == {"recovering": True, "read_only": "on"}, "pre_fence_candidate_not_read_only")


async def verify(directory, standby_url):
    require(standby_url is not None, "owned_standby_required")
    reference = read(directory, "acknowledged.json", Reference)
    require(pitr.inspect_tar(directory / "basebackup.tar", pitr.MAX_BACKUP_BYTES)
            == read(directory, "artifact.json", pitr.Artifact), "backup_artifact_changed")
    promoted = replication_status(standby_url)
    require(not promoted.in_recovery and not promoted.synchronous_standby_configured
            and promoted.senders.total == 0 and promoted.synchronous_commit == "on",
            "promoted_node_must_be_explicitly_degraded")
    with psycopg.connect(standby_url, autocommit=True) as conn:
        conn.execute("CHECKPOINT")
    candidate = capture(standby_url, "promoted", reference.fixture)
    check_preserved(reference, candidate, promoted=True)
    write_new(directory, "promoted.json", candidate)
    write_new(directory, "preserved.json", PreservedEvidence(
        acknowledged_state_matches=True, effect_state_preserved=True,
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
    after = capture(standby_url, "post-probe", fixture)
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
    parser.add_argument("stage", choices=("seed", "artifact", "synchronous", "prefence", "verify"))
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
        elif args.stage == "prefence":
            prefence(directory, standby_url)
        else:
            asyncio.run(verify(directory, standby_url))
    except (
        AdminError, RuntimeValidationError, ServiceError, psycopg.Error, ValidationError,
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
