"""Synthetic SQL-only PITR evidence; callable only by the owned-container harness.

Physical copies remain private. A matching historical snapshot is deliberately
not authorization to serve it: the later terminal source denial is missing.
"""

import argparse
import asyncio
import hashlib
import ipaddress
import os
import platform
import re
import stat
import sys
import tarfile
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pg_agmemory import __version__
from pg_agmemory.checkpoints import Checkpoints
from pg_agmemory.database import SCHEMA_VERSION, migrate, validate_runtime
from pg_agmemory.human_review import load_json
from pg_agmemory.models import CheckpointState, CreateCheckpoint, MemoryReference, Observe
from pg_agmemory.operations_status import OperationsStatusRequest, operations_status
from pg_agmemory.processing_recovery import (
    ProcessingRecoverySnapshot,
    StateFingerprint,
    capture_processing_state,
    compare_processing_state,
    fingerprint_tables,
)
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryService, bind_identity, principal_connection
from pg_agmemory.source_access import (
    SourceAccessRequest,
    SourceIdentity,
    SourceNotice,
    source_access,
)

TARGET_NAME = "pgag_m5_target"
WRITER = "synthetic-pitr-writer"
READER = "synthetic-pitr-source-reader"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 32
MAX_SEGMENTS = 16
MAX_JSON_BYTES = 131072
SOURCE = SourceIdentity(
    source_system="synthetic-pitr", dataset_id="owned-drill", source_subject=READER,
)
CONTENT_TABLES = {
    "memory.episode": "id",
    "memory.checkpoint": "id",
    "memory.checkpoint_reference": "checkpoint_id,source_id,source_revision",
}
LSN = Annotated[str, Field(pattern=r"^[0-9A-F]{1,8}/[0-9A-F]{1,8}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Seconds = Annotated[float, Field(ge=0, le=7200, allow_inf_nan=False)]


class DrillError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise DrillError(code)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourceCursor(Contract):
    sequence: Annotated[int, Field(ge=1, le=2)]
    decision: Literal["deny"]
    reason: Literal["revoked", "deleted"]


class Episode(Contract):
    object_id: UUID
    content_sha256: Digest


class Control(Contract):
    system_identifier: Annotated[str, Field(pattern=r"^[0-9]{1,20}$")]
    timeline: Annotated[int, Field(ge=1)]
    lsn: LSN


class RestorePoint(Contract):
    name: Literal["pgag_m5_target"] = TARGET_NAME
    lsn: LSN
    wal_file: Annotated[str, Field(pattern=r"^[0-9A-F]{24}$")]
    before: Control
    after: Control

    @model_validator(mode="after")
    def same_cluster(self):
        if (
            self.before.system_identifier != self.after.system_identifier
            or self.before.timeline != self.after.timeline
            or not lsn_number(self.before.lsn) <= lsn_number(self.lsn)
            <= lsn_number(self.after.lsn)
        ):
            raise ValueError("invalid_restore_point_identity")
        return self


class Reference(Contract):
    format: Literal["pgag-pitr-reference-v1"] = "pgag-pitr-reference-v1"
    stage: Literal["seed", "target", "latest"]
    service_version: str = __version__
    schema_version: Literal[22] = 22
    tenant_id: UUID
    scope_id: UUID
    writer_id: UUID
    reader_id: UUID
    before_id: UUID
    checkpoint_id: UUID
    target_id: UUID | None = None
    after_id: UUID | None = None
    control: Control
    source_cursor: SourceCursor
    episodes: Annotated[tuple[Episode, ...], Field(min_length=1, max_length=3)]
    content: tuple[StateFingerprint, ...]
    processing: ProcessingRecoverySnapshot
    restore_point: RestorePoint | None = None
    restore_authorized: Literal[False] = False

    @model_validator(mode="after")
    def complete(self):
        if self.service_version != __version__ or self.processing.tenant_id != self.tenant_id:
            raise ValueError("reference_identity_mismatch")
        expected = {self.before_id}
        if self.stage != "seed":
            if self.target_id is None or self.restore_point is None:
                raise ValueError("missing_target")
            expected.add(self.target_id)
        elif self.target_id is not None or self.restore_point is not None:
            raise ValueError("unexpected_target")
        if self.stage == "latest":
            if self.after_id is None:
                raise ValueError("missing_later_episode")
            expected.add(self.after_id)
        elif self.after_id is not None:
            raise ValueError("unexpected_later_episode")
        count = {"seed": 1, "target": 2, "latest": 3}[self.stage]
        if len(expected) != count or {e.object_id for e in self.episodes} != expected:
            raise ValueError("invalid_episode_set")
        if len(self.episodes) != count or self.checkpoint_id in expected:
            raise ValueError("duplicate_object")
        if tuple(item.table for item in self.content) != tuple(CONTENT_TABLES):
            raise ValueError("incomplete_content_fingerprints")
        wanted = (2, "deleted") if self.stage == "latest" else (1, "revoked")
        if (self.source_cursor.sequence, self.source_cursor.reason) != wanted:
            raise ValueError("invalid_source_cursor")
        return self


class Backup(Contract):
    format: Literal["pgag-pitr-basebackup-v1"]
    manifest_verified: Literal[True]
    start_lsn: LSN
    end_lsn: LSN
    timeline: Annotated[int, Field(ge=1)]


class Archive(Contract):
    format: Literal["pgag-pitr-archive-v1"] = "pgag-pitr-archive-v1"
    required_wal: Annotated[str, Field(pattern=r"^[0-9A-F]{24}$")]
    target_wal: Annotated[str, Field(pattern=r"^[0-9A-F]{24}$")]
    switch_lsn: LSN
    required_archive_done: Literal[True]
    target_archive_done: Literal[True]
    files: Annotated[dict[str, int], Field(min_length=1, max_length=MAX_ARCHIVE_FILES)]
    bytes: Annotated[int, Field(ge=1, le=MAX_ARCHIVE_BYTES)]
    elapsed_seconds: Seconds

    @model_validator(mode="after")
    def bounded_complete_archive(self):
        validate_archive_files(self.files, self.required_wal, self.target_wal)
        if self.bytes != sum(self.files.values()):
            raise ValueError("archive_size_mismatch")
        return self


class Artifact(Contract):
    bytes: Annotated[int, Field(ge=1, le=MAX_BACKUP_BYTES)]
    sha256: Digest


class Artifacts(Contract):
    basebackup: Artifact
    wal_archive: Artifact


class Verification(Contract):
    target_state_matches: Literal[True]
    latest_state_matches: Literal[False]
    wal_replay_verified: Literal[True]
    recovery_paused: Literal[True]
    read_only: Literal[True]
    write_rejected: Literal[True]
    before_basebackup_present: Literal[True]
    target_episode_present: Literal[True]
    after_target_absent: Literal[True]
    source_cursor_matches_target: Literal[True]
    operations_snapshot_verified: Literal[True]
    latest_differences: tuple[str, ...]
    replay_lsn: LSN
    system_identifier: str
    timeline: int
    restore_authorized: Literal[False] = False
    automatic_promotion: Literal[False] = False
    automatic_service_start: Literal[False] = False


class Report(Contract):
    format: Literal["pgag-pitr-drill-v1"] = "pgag-pitr-drill-v1"
    service_version: str = __version__
    api_version: Literal["v1"] = "v1"
    schema_version: Literal[22] = 22
    postgres_version_num: Literal[180006] = 180006
    pgvector_version: Literal["0.8.6"] = "0.8.6"
    status: Literal["passed", "failed"]
    failure_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")] | None
    primary_destroyed: bool
    backup_verified: bool
    wal_replay_verified: bool
    recovery_paused: bool
    read_only: bool
    target_state_matches: bool
    latest_state_matches: Literal[False] | None = None
    restore_authorized: Literal[False] = False
    automatic_promotion: Literal[False] = False
    automatic_service_start: Literal[False] = False
    production_qualified: Literal[False] = False
    host_failure_domain_independent: Literal[False] = False
    elapsed_seconds: dict[str, Seconds]
    verification: Verification | None = None
    artifacts: Artifacts | None = None

    @model_validator(mode="after")
    def truthful(self):
        if not self.elapsed_seconds or self.service_version != __version__:
            raise ValueError("invalid_report_identity_or_timings")
        if self.status == "passed":
            if self.failure_code is not None or not all((
                self.primary_destroyed, self.backup_verified, self.wal_replay_verified,
                self.recovery_paused, self.read_only, self.target_state_matches,
                self.verification is not None, self.artifacts is not None,
                self.latest_state_matches is False,
            )):
                raise ValueError("incomplete_pass_evidence")
        elif self.failure_code is None:
            raise ValueError("failure_code_required")
        if self.verification is None and any((
            self.wal_replay_verified, self.recovery_paused, self.read_only,
            self.target_state_matches,
        )):
            raise ValueError("verification_evidence_required")
        if self.verification is None:
            if self.latest_state_matches is not None:
                raise ValueError("latest_state_unmeasured")
        elif self.latest_state_matches is not False:
            raise ValueError("latest_state_verification_mismatch")
        return self


def lsn_number(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9A-F]{1,8}/[0-9A-F]{1,8}", value),
            "invalid_lsn")
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def write_new(directory, name, model):
    payload = model.model_dump_json(indent=2).encode() + b"\n"
    require(len(payload) <= MAX_JSON_BYTES, "output_size_limit")
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)


def read(directory, name, model):
    return load_json(directory / name, model, max_bytes=MAX_JSON_BYTES)


def owned_environment(needs_database=True):
    require(platform.system() == "Linux", "linux_runtime_required")
    require(SCHEMA_VERSION == 22, "schema_version_mismatch")
    require(not any(os.environ.get(key) for key in (
        "PGAG_DATABASE_URL", "PGAG_ADMIN_DATABASE_URL", "PGHOST", "PGSERVICE",
    )), "external_database_target_forbidden")
    run = os.environ.get("PGAG_PITR_OWNED_RUN", "")
    require(re.fullmatch(r"pgag-pitr-[0-9]+-[0-9]+-[0-9a-f]{16}", run),
            "owned_harness_required")
    directory = Path("/drill")
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
            "private_directory_required")
    if not needs_database:
        return directory, None
    host = os.environ.get("PGAG_PITR_DB_HOST", "")
    engine = os.environ.get("PGAG_PITR_ENGINE")
    require(engine in ("docker", "container"), "owned_harness_required")
    if engine == "docker":
        require(host in (run + "-primary", run + "-restored"), "owned_database_required")
    else:
        address = ipaddress.ip_address(host)
        require(isinstance(address, ipaddress.IPv4Address) and address.is_private
                and not address.is_loopback and not address.is_unspecified
                and not address.is_multicast and not address.is_link_local,
                "owned_database_required")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    require(re.fullmatch(r"[0-9a-f]{64}", password), "owned_credentials_required")
    return directory, make_conninfo(
        host=host, port=5432, dbname="pgag_pitr", user="postgres", password=password,
        connect_timeout=5, options="-c statement_timeout=15000 -c lock_timeout=5000",
    )


def runtime_url(url, create=False):
    params = conninfo_to_dict(url)
    if create:
        with psycopg.connect(url) as conn:
            conn.execute(sql.SQL(
                "CREATE ROLE pgag_pitr_writer LOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {} IN ROLE pgag_runtime"
            ).format(sql.Literal(params["password"])))
    return make_conninfo(**(params | {"user": "pgag_pitr_writer"}))


@asynccontextmanager
async def service(url, subject=WRITER):
    async with principal_connection(runtime_url(url), subject) as (conn, identity):
        async with conn.transaction():
            await bind_identity(conn, subject, identity)
            yield MemoryService(conn, identity)


def control(conn, recovery=False):
    lsn_function = "pg_last_wal_replay_lsn()" if recovery else "pg_current_wal_insert_lsn()"
    row = conn.execute(
        "SELECT s.system_identifier::text AS system_identifier, c.timeline_id AS timeline, "
        + lsn_function + "::text AS lsn "
        "FROM pg_control_system() s CROSS JOIN pg_control_checkpoint() c"
    ).fetchone()
    return Control(**row)


def capture(url, stage, ids, point=None, recovery=False):
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        require(conn.execute("SHOW server_version_num").fetchone()["server_version_num"]
                == "180006", "postgres_version_mismatch")
        require(conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                .fetchone() == {"extversion": "0.8.6"}, "pgvector_version_mismatch")
        versions = conn.execute(
            "SELECT version FROM public.pgag_schema_migration ORDER BY version"
        ).fetchall()
        require([r["version"] for r in versions] == list(range(1, 23)),
                "schema_version_mismatch")
        cursor = conn.execute(
            "SELECT sequence,decision,reason FROM memory_ops.source_access_state "
            "WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s",
            (ids["tenant_id"], ids["scope_id"], ids["reader_id"]),
        ).fetchone()
        episodes = tuple(Episode(
            object_id=row["id"], content_sha256=hashlib.sha256(row["content"].encode()).hexdigest(),
        ) for row in conn.execute(
            "SELECT id,content FROM memory.episode WHERE tenant_id=%s ORDER BY id LIMIT 4",
            (ids["tenant_id"],),
        ))
        secret = bytes(conn.execute(
            "SELECT dedup_secret FROM memory.tenant WHERE id=%s", (ids["tenant_id"],),
        ).fetchone()["dedup_secret"])
        content = fingerprint_tables(conn, ids["tenant_id"], secret, CONTENT_TABLES)
        metadata = control(conn, recovery)
    return Reference(
        stage=stage, **ids, control=metadata, source_cursor=SourceCursor(**cursor),
        episodes=episodes, content=content, restore_point=point,
        processing=capture_processing_state(url, ids["tenant_id"]),
    )


def ids_from(reference):
    return {key: getattr(reference, key) for key in (
        "tenant_id", "scope_id", "writer_id", "reader_id", "before_id", "checkpoint_id",
        "target_id", "after_id",
    )}


def source_notice(url, ids, sequence, reason):
    with psycopg.connect(url) as conn:
        epoch = conn.execute(
            "SELECT access_epoch FROM memory.tenant WHERE id=%s", (ids["tenant_id"],),
        ).fetchone()[0]
    with source_access(url, SourceAccessRequest(
        operation="apply", tenant_id=ids["tenant_id"], scope_id=ids["scope_id"],
        principal_id=ids["reader_id"], expected_access_epoch=epoch,
        notice=SourceNotice(source=SOURCE, sequence=sequence, decision="deny", reason=reason),
    )) as result:
        require(result.sequence == sequence and result.reason == reason, "source_notice_failed")


async def observe(url, scope_id, name):
    async with service(url) as memory:
        result = await memory.observe(Observe(
            scope_id=scope_id, source_namespace="synthetic-pitr", source_event_id=name,
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            content=f"Synthetic PITR control episode: {name}.",
            consent_reference="owned-synthetic-pitr-drill-only",
        ), "pitr-" + name)
    return UUID(result["memory_id"])


async def seed(directory, url):
    migrate(url)
    await validate_runtime(runtime_url(url, create=True))
    ids = {key: uuid4() for key in ("tenant_id", "scope_id", "writer_id", "reader_id")}
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
    )) as result:
        epoch = result.access_epoch
    with source_access(url, SourceAccessRequest(
        operation="bind", tenant_id=ids["tenant_id"], scope_id=ids["scope_id"],
        principal_id=ids["reader_id"], expected_access_epoch=epoch, source=SOURCE,
    )):
        pass
    source_notice(url, ids, 1, "revoked")
    ids["before_id"] = await observe(url, ids["scope_id"], "before-basebackup")
    async with service(url) as memory:
        checkpoint = await Checkpoints(memory).create(CreateCheckpoint(
            scope_id=ids["scope_id"], run_id=uuid4(), branch_id=uuid4(), expected_head=None,
            harness_id="synthetic-pitr", harness_version="1", event_watermark=1,
            state=CheckpointState(goal="Verify an acknowledged synthetic checkpoint survives"),
            memory_refs=[MemoryReference(memory_id=ids["before_id"])],
        ), "pitr-checkpoint")
        ids["checkpoint_id"] = UUID(checkpoint["checkpoint_id"])
    async with service(url, READER) as memory:
        row = await (await memory.conn.execute(
            "SELECT count(*) AS count FROM memory.episode"
        )).fetchone()
        require(row["count"] == 0, "source_reader_must_be_denied")
    write_new(directory, "seed.json", capture(url, "seed", ids))


async def target(directory, url):
    reference = read(directory, "seed.json", Reference)
    backup = read(directory, "backup.json", Backup)
    ids = ids_from(reference)
    ids["target_id"] = await observe(url, ids["scope_id"], "after-backup-before-target")
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        before = control(conn)
        lsn = conn.execute("SELECT pg_create_restore_point(%s)::text AS lsn",
                           (TARGET_NAME,)).fetchone()["lsn"]
        wal = conn.execute("SELECT pg_walfile_name(%s::pg_lsn) AS name", (lsn,)).fetchone()
        point = RestorePoint(lsn=lsn, wal_file=wal["name"], before=before, after=control(conn))
    require(lsn_number(backup.end_lsn) < lsn_number(point.lsn), "target_not_after_basebackup")
    require(backup.timeline == point.before.timeline, "backup_timeline_mismatch")
    write_new(directory, "target.json", capture(url, "target", ids, point))


async def after(directory, url):
    reference = read(directory, "target.json", Reference)
    ids = ids_from(reference)
    ids["after_id"] = await observe(url, ids["scope_id"], "after-target-excluded")
    source_notice(url, ids, 2, "deleted")
    latest = capture(url, "latest", ids, reference.restore_point)
    validate_references(
        read(directory, "seed.json", Reference), reference, latest,
        read(directory, "backup.json", Backup),
    )
    write_new(directory, "latest.json", latest)


def validate_references(seed_state, target_state, latest, backup):
    require((seed_state.stage, target_state.stage, latest.stage) == ("seed", "target", "latest"),
            "reference_stage_mismatch")
    for key in ("tenant_id", "scope_id", "writer_id", "reader_id", "before_id", "checkpoint_id"):
        require(getattr(seed_state, key) == getattr(target_state, key) == getattr(latest, key),
                "reference_identity_mismatch")
    require(target_state.target_id == latest.target_id
            and target_state.restore_point == latest.restore_point, "reference_target_mismatch")
    point = target_state.restore_point
    require(point is not None, "restore_point_missing")
    require(all(
        ref.control.system_identifier == point.before.system_identifier
        and ref.control.timeline == point.before.timeline
        for ref in (seed_state, target_state, latest)
    ) and backup.timeline == point.before.timeline, "cluster_identity_mismatch")
    require(lsn_number(backup.start_lsn) <= lsn_number(backup.end_lsn)
            < lsn_number(point.lsn) <= lsn_number(latest.control.lsn),
            "target_not_after_basebackup")
    require(seed_state.source_cursor == target_state.source_cursor
            and latest.source_cursor != target_state.source_cursor, "source_cursor_not_advanced")
    check = compare_processing_state(target_state.processing, latest.processing)
    require(not check.processing_state_matches and not check.restore_authorized
            and {"memory_ops.source_access_state", "memory_ops.source_access_event"}
            <= set(check.differences), "latest_authority_must_differ")
    for older, newer in ((seed_state, target_state), (target_state, latest)):
        newer_episodes = {item.object_id: item for item in newer.episodes}
        require(all(newer_episodes.get(item.object_id) == item for item in older.episodes),
                "acknowledged_episode_changed")
    return check


def validate_archive_files(files, required_wal, target_wal):
    require(0 < len(files) <= MAX_ARCHIVE_FILES, "archive_file_limit")
    segments = 0
    for name, size in files.items():
        require(type(size) is int and size > 0, "invalid_archive_size")
        if re.fullmatch(r"[0-9A-F]{24}", name):
            require(size == 16 * 1024 * 1024, "incomplete_wal_segment")
            segments += 1
        else:
            require(re.fullmatch(r"[0-9A-F]{24}\.[0-9A-F]{8}\.backup", name)
                    and size <= 65536, "unexpected_archive_file")
    require(0 < segments <= MAX_SEGMENTS and sum(files.values()) <= MAX_ARCHIVE_BYTES,
            "archive_byte_limit")
    require(required_wal in files and target_wal in files, "required_wal_missing")


def archive(directory, url):
    target_state = read(directory, "target.json", Reference)
    require(target_state.restore_point is not None, "restore_point_missing")
    target_wal = target_state.restore_point.wal_file
    started = time.monotonic()
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("CHECKPOINT")
        required = conn.execute(
            "SELECT pg_walfile_name(pg_current_wal_insert_lsn()) AS name"
        ).fetchone()["name"]
        switch_lsn = conn.execute("SELECT pg_switch_wal()::text AS lsn").fetchone()["lsn"]
        while True:
            done = all(conn.execute(
                "SELECT (pg_stat_file(%s, true)).size AS size",
                ("pg_wal/archive_status/" + name + ".done",),
            ).fetchone()["size"] == 0 for name in {required, target_wal})
            if done:
                break
            require(time.monotonic() - started < 90, "required_wal_not_archived")
            time.sleep(1)
        rows = conn.execute(
            "SELECT name,(pg_stat_file('/owned/archive/' || name)).size AS size "
            "FROM pg_ls_dir('/owned/archive') AS name LIMIT 33"
        ).fetchall()
    files = {row["name"]: row["size"] for row in rows}
    validate_archive_files(files, required, target_wal)
    write_new(directory, "archive.json", Archive(
        required_wal=required, target_wal=target_wal, switch_lsn=switch_lsn,
        required_archive_done=True, target_archive_done=True, files=files,
        bytes=sum(files.values()), elapsed_seconds=time.monotonic() - started,
    ))


def inspect_tar(path, maximum, expected_files=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum,
                "artifact_size_limit")
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
        stream.seek(0)
        names = set()
        files = {}
        total = 0
        with tarfile.open(fileobj=stream, mode="r:") as packed:
            for member in packed:
                path_parts = PurePosixPath(member.name)
                require(not path_parts.is_absolute() and ".." not in path_parts.parts
                        and (member.isdir() or member.isfile()) and not member.issparse(),
                        "unsafe_archive_member")
                normalized = str(path_parts)
                require(normalized not in names and len(names) < 20000,
                        "duplicate_or_excess_archive_members")
                names.add(normalized)
                require(member.size >= 0, "invalid_archive_size")
                total += member.size
                require(total <= maximum, "artifact_size_limit")
                if member.isfile():
                    files[normalized] = member.size
        if expected_files is not None:
            require(files == expected_files, "archive_inventory_mismatch")
        else:
            require({"PG_VERSION", "backup_label", "backup_manifest", "global/pg_control"}
                    <= files.keys(), "incomplete_basebackup")
    return Artifact(bytes=info.st_size, sha256=sha)


def artifact_state(directory):
    inventory = read(directory, "archive.json", Archive)
    return Artifacts(
        basebackup=inspect_tar(directory / "basebackup.tar", MAX_BACKUP_BYTES),
        wal_archive=inspect_tar(
            directory / "wal-archive.tar", MAX_ARCHIVE_BYTES + 1024 * 1024, inventory.files,
        ),
    )


def validate_recovered(restored, target_state, latest, backup, replay_lsn):
    check = compare_processing_state(restored.processing, target_state.processing)
    require(check.processing_state_matches and not check.restore_authorized,
            "target_processing_mismatch")
    require(restored.episodes == target_state.episodes and restored.content == target_state.content,
            "target_content_mismatch")
    require(restored.source_cursor == target_state.source_cursor, "target_source_cursor_mismatch")
    require(restored.control.system_identifier == target_state.control.system_identifier
            and restored.control.timeline == target_state.control.timeline,
            "restored_cluster_identity_mismatch")
    require(latest.after_id not in {item.object_id for item in restored.episodes},
            "after_target_episode_present")
    point = target_state.restore_point
    require(point is not None and lsn_number(backup.end_lsn) < lsn_number(point.lsn)
            <= lsn_number(replay_lsn), "target_wal_not_replayed")
    newest = compare_processing_state(restored.processing, latest.processing)
    require(not newest.processing_state_matches and not newest.restore_authorized
            and "memory_ops.source_access_state" in newest.differences,
            "latest_authority_must_differ")
    return newest


def recovery_status(conn):
    row = conn.execute(
        "SELECT pg_is_in_recovery() AS recovering, pg_is_wal_replay_paused() AS paused, "
        "current_setting('transaction_read_only') AS read_only, "
        "pg_last_wal_replay_lsn()::text AS lsn, "
        "current_setting('recovery_target_name') AS target, "
        "current_setting('recovery_target_action') AS action"
    ).fetchone()
    require(row["recovering"] is True and row["paused"] is True
            and row["read_only"] == "on" and row["target"] == TARGET_NAME
            and row["action"] == "pause", "recovery_target_not_paused")
    return row


def verify(directory, url):
    seed_state = read(directory, "seed.json", Reference)
    target_state = read(directory, "target.json", Reference)
    latest = read(directory, "latest.json", Reference)
    backup = read(directory, "backup.json", Backup)
    validate_references(seed_state, target_state, latest, backup)
    require(artifact_state(directory) == read(directory, "artifacts.json", Artifacts),
            "artifact_digest_mismatch")
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        state = recovery_status(conn)
        # Even an empty INSERT must be rejected by recovery, not by an ACL or constraint.
        try:
            with conn.transaction(force_rollback=True):
                conn.execute("INSERT INTO memory.tenant(id,dedup_secret) "
                             "SELECT id,dedup_secret FROM memory.tenant WHERE false")
        except psycopg.errors.ReadOnlySqlTransaction:
            pass
        else:
            raise DrillError("read_only_write_not_rejected")
    restored = capture(
        url, "target", ids_from(target_state), target_state.restore_point, recovery=True,
    )
    newest = validate_recovered(restored, target_state, latest, backup, state["lsn"])
    observed = operations_status(url, OperationsStatusRequest(tenant_id=target_state.tenant_id))
    require(
        observed.in_recovery and not observed.primary_snapshot
        and "standby_snapshot" in observed.warnings
        and observed.access_epoch == target_state.processing.access_epoch
        and observed.deletion_epoch == target_state.processing.deletion_epoch
        and observed.source.total == observed.source.denied == 1
        and observed.source.deleted == observed.source.active_read_leases == 0
        and not observed.restore_authorized and not observed.production_qualified,
        "paused_operations_snapshot_mismatch",
    )
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        final_state = recovery_status(conn)
    require(final_state["lsn"] == state["lsn"], "replay_moved_while_paused")
    write_new(directory, "verification.json", Verification(
        target_state_matches=True, latest_state_matches=False, wal_replay_verified=True,
        recovery_paused=True, read_only=True, write_rejected=True,
        before_basebackup_present=True, target_episode_present=True, after_target_absent=True,
        source_cursor_matches_target=True, operations_snapshot_verified=True,
        latest_differences=newest.differences,
        replay_lsn=state["lsn"], system_identifier=restored.control.system_identifier,
        timeline=restored.control.timeline,
    ))


class Failure(Contract):
    failure_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=("seed", "target", "after", "archive", "artifacts", "verify"),
    )
    args = parser.parse_args(argv)
    directory = None
    try:
        directory, url = owned_environment(args.stage != "artifacts")
        if args.stage in ("seed", "target", "after"):
            operation = {"seed": seed, "target": target, "after": after}[args.stage]
            asyncio.run(operation(directory, url))
        elif args.stage == "archive":
            archive(directory, url)
        elif args.stage == "artifacts":
            write_new(directory, "artifacts.json", artifact_state(directory))
        else:
            verify(directory, url)
    except Exception as exc:
        code = str(exc) if isinstance(exc, DrillError) else "pitr_stage_failed"
        if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) is None:
            code = "pitr_stage_failed"
        if directory is not None:
            try:
                write_new(directory, "failure.json", Failure(failure_code=code))
            except (OSError, ValueError):
                pass
        print(code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
