"""Offline PITR contracts: no PostgreSQL, container engine, network, or scratch files."""

import importlib.util
import io
import json
import os
import stat
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory import __version__
from pg_agmemory.processing_recovery import TABLES

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "test-pitr-containers.sh"
spec = importlib.util.spec_from_file_location("pitr_drill", ROOT / "scripts" / "smoke-pitr.py")
assert spec is not None and spec.loader is not None
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


def references():
    ids = {name: uuid4() for name in (
        "tenant_id", "scope_id", "writer_id", "reader_id", "before_id", "checkpoint_id",
    )}
    target_id, after_id = uuid4(), uuid4()
    control = drill.Control(system_identifier="1234567890123456789", timeline=1, lsn="0/100")
    point = drill.RestorePoint(
        lsn="0/410", wal_file="0" * 23 + "1",
        before=control.model_copy(update={"lsn": "0/400"}),
        after=control.model_copy(update={"lsn": "0/410"}),
    )
    result = []
    for index, stage in enumerate(("seed", "target", "latest"), 1):
        digest = str(index) * 64
        snapshot = drill.ProcessingRecoverySnapshot(
            tenant_id=ids["tenant_id"], lineage="a" * 64, access_epoch=2, deletion_epoch=1,
            tables=tuple(drill.StateFingerprint(
                table=table, rows=index if table == "memory.object" else 1,
                digest=(digest if table == "memory.object" or (
                    stage == "latest" and table.startswith("memory_ops.source_access")
                ) else "a" * 64),
            ) for table in TABLES),
        )
        episodes = [drill.Episode(object_id=ids["before_id"], content_sha256="b" * 64)]
        if index >= 2:
            episodes.append(drill.Episode(object_id=target_id, content_sha256="c" * 64))
        if index == 3:
            episodes.append(drill.Episode(object_id=after_id, content_sha256="d" * 64))
        result.append(drill.Reference(
            stage=stage, **ids, target_id=target_id if index >= 2 else None,
            after_id=after_id if index == 3 else None,
            control=control.model_copy(update={"lsn": ("0/100", "0/410", "0/500")[index - 1]}),
            source_cursor=drill.SourceCursor(
                sequence=2 if index == 3 else 1, decision="deny",
                reason="deleted" if index == 3 else "revoked",
            ),
            episodes=tuple(episodes), content=tuple(
                drill.StateFingerprint(table=table, rows=index, digest=digest)
                for table in drill.CONTENT_TABLES
            ), processing=snapshot, restore_point=point if index >= 2 else None,
        ))
    backup = drill.Backup(
        format="pgag-pitr-basebackup-v1", manifest_verified=True,
        start_lsn="0/200", end_lsn="0/300", timeline=1,
    )
    return (*result, backup)


def artifacts():
    item = drill.Artifact(bytes=1024, sha256="a" * 64)
    return drill.Artifacts(basebackup=item, wal_archive=item)


def verification():
    return drill.Verification(
        target_state_matches=True, latest_state_matches=False, wal_replay_verified=True,
        recovery_paused=True, read_only=True, write_rejected=True,
        before_basebackup_present=True, target_episode_present=True, after_target_absent=True,
        source_cursor_matches_target=True, operations_snapshot_verified=True,
        latest_differences=("memory_ops.source_access_state",),
        replay_lsn="0/410", system_identifier="1234567890123456789", timeline=1,
    )


def report():
    return drill.Report(
        status="passed", failure_code=None, primary_destroyed=True, backup_verified=True,
        wal_replay_verified=True, recovery_paused=True, read_only=True,
        target_state_matches=True, latest_state_matches=False,
        elapsed_seconds={"basebackup": 0.5, "total": 30.0},
        verification=verification(), artifacts=artifacts(),
    )


def test_report_preserves_schema22_and_never_grants_restore_authority():
    value = report().model_dump(mode="json")
    assert value["service_version"] == __version__
    assert value["schema_version"] == 22
    assert value["postgres_version_num"] == 180006
    assert value["pgvector_version"] == "0.8.6"
    assert value["api_version"] == "v1"
    assert value["latest_state_matches"] is False
    for name in (
        "restore_authorized", "automatic_promotion",
        "automatic_service_start", "production_qualified", "host_failure_domain_independent",
    ):
        assert value[name] is False
        with pytest.raises(ValidationError):
            drill.Report.model_validate(value | {name: True})


@pytest.mark.parametrize("value", [False, None])
def test_verification_requires_paused_operations_snapshot(value):
    with pytest.raises(ValidationError):
        drill.Verification.model_validate(
            verification().model_dump() | {"operations_snapshot_verified": value}
        )


@pytest.mark.parametrize("field", [
    "primary_destroyed", "backup_verified", "wal_replay_verified", "recovery_paused",
    "read_only", "target_state_matches",
])
def test_pass_requires_measured_truth_flags(field):
    with pytest.raises(ValidationError, match="incomplete_pass_evidence"):
        drill.Report.model_validate(report().model_dump() | {field: False})


@pytest.mark.parametrize("field", ["artifacts", "verification"])
def test_pass_without_private_evidence_is_invalid(field):
    with pytest.raises(ValidationError):
        drill.Report.model_validate(report().model_dump() | {field: None})


@pytest.mark.parametrize("seconds", [-1.0, float("inf"), float("-inf"), float("nan"), 7200.01])
def test_elapsed_rejects_negative_nonfinite_and_out_of_bound_values(seconds):
    with pytest.raises(ValidationError):
        drill.Report.model_validate(report().model_dump() | {"elapsed_seconds": {"total": seconds}})


@pytest.mark.parametrize("seconds", [0.0, 7200.0])
def test_elapsed_accepts_finite_boundaries(seconds):
    assert drill.Report.model_validate(
        report().model_dump() | {"elapsed_seconds": {"total": seconds}}
    ).elapsed_seconds["total"] == seconds


def test_failed_report_requires_redacted_code_and_cannot_claim_unmeasured_recovery():
    value = report().model_dump() | {"status": "failed", "failure_code": None}
    with pytest.raises(ValidationError, match="failure_code_required"):
        drill.Report.model_validate(value)
    with pytest.raises(ValidationError):
        drill.Report.model_validate(value | {"failure_code": "postgresql://secret@host/database"})
    with pytest.raises(ValidationError, match="verification_evidence_required"):
        drill.Report.model_validate(value | {
            "failure_code": "required_wal_missing", "verification": None,
        })


def test_unmeasured_failed_report_requires_null_latest_state():
    value = report().model_dump() | {
        "status": "failed", "failure_code": "required_wal_missing", "verification": None,
        "wal_replay_verified": False, "recovery_paused": False, "read_only": False,
        "target_state_matches": False, "latest_state_matches": None,
    }
    assert drill.Report.model_validate(value).latest_state_matches is None
    with pytest.raises(ValidationError, match="latest_state_unmeasured"):
        drill.Report.model_validate(value | {"latest_state_matches": False})


@pytest.mark.parametrize("status", ["passed", "failed"])
@pytest.mark.parametrize("latest_matches", [None, True])
def test_verified_report_requires_measured_latest_mismatch(status, latest_matches):
    value = report().model_dump() | {
        "status": status, "failure_code": None if status == "passed" else "owned_cleanup_failed",
        "latest_state_matches": latest_matches,
    }
    with pytest.raises(ValidationError):
        drill.Report.model_validate(value)


def test_verified_failure_preserves_measured_latest_mismatch():
    value = report().model_dump() | {
        "status": "failed", "failure_code": "owned_cleanup_failed",
    }
    assert drill.Report.model_validate(value).latest_state_matches is False


def test_named_point_matches_target_but_terminal_source_authority_differs():
    seed, target, latest, backup = references()
    check = drill.validate_references(seed, target, latest, backup)
    assert "memory_ops.source_access_state" in check.differences
    assert "memory_ops.source_access_event" in check.differences
    assert not check.processing_state_matches
    assert not check.restore_authorized
    restored = drill.validate_recovered(target, target, latest, backup, "0/410")
    assert not restored.processing_state_matches
    assert target.source_cursor.sequence == 1
    assert latest.source_cursor.sequence == 2
    assert latest.source_cursor.reason == "deleted"


@pytest.mark.parametrize("change,code", [
    ({"source_cursor": "latest"}, "source_cursor_not_advanced"),
    ({"processing": "target"}, "latest_authority_must_differ"),
    ({"control": "other"}, "cluster_identity_mismatch"),
])
def test_latest_authority_cannot_be_omitted_or_substituted(change, code):
    seed, target, latest, backup = references()
    if "source_cursor" in change:
        target = target.model_copy(update={"source_cursor": latest.source_cursor})
    if "processing" in change:
        latest = latest.model_copy(update={"processing": target.processing})
    if "control" in change:
        latest = latest.model_copy(update={"control": latest.control.model_copy(
            update={"system_identifier": "42"},
        )})
    with pytest.raises(drill.DrillError, match=code):
        drill.validate_references(seed, target, latest, backup)


@pytest.mark.parametrize("lsn", ["0/100", "0/300", "0/400"])
def test_replay_must_actually_reach_named_point_beyond_basebackup(lsn):
    _, target, latest, backup = references()
    with pytest.raises(drill.DrillError, match="target_wal_not_replayed"):
        drill.validate_recovered(target, target, latest, backup, lsn)


def test_content_and_processing_are_both_required():
    _, target, latest, backup = references()
    altered = target.model_copy(update={"content": latest.content})
    with pytest.raises(drill.DrillError, match="target_content_mismatch"):
        drill.validate_recovered(altered, target, latest, backup, "0/410")
    altered = target.model_copy(update={"processing": latest.processing})
    with pytest.raises(drill.DrillError, match="target_processing_mismatch"):
        drill.validate_recovered(altered, target, latest, backup, "0/410")


def test_reference_contract_rejects_unknown_fields_missing_ids_and_duplicate_episodes():
    _, target, _, _ = references()
    for change in (
        {"dsn": "forbidden"}, {"target_id": None},
        {"schema_version": 20}, {"schema_version": 21},
        {"episodes": [target.episodes[0].model_dump(mode="json")] * 2},
        {"service_version": "unrelated-build"},
    ):
        with pytest.raises(ValidationError):
            drill.Reference.model_validate_json(json.dumps(target.model_dump(mode="json") | change))


@pytest.mark.parametrize("payload", [
    b'{"failure_code":"first","failure_code":"second"}',
    b'{"failure_code": NaN}', b"[]", b'{"failure_code": "safe", "secret": "forbidden"}',
    b"x" * (drill.MAX_JSON_BYTES + 1),
])
def test_reference_loader_is_strict_bounded_and_no_follow(monkeypatch, payload):
    flags = []
    monkeypatch.setattr(os, "open", lambda path, value: flags.append(value) or 123)
    monkeypatch.setattr(os, "fdopen", lambda fd, mode: io.BytesIO(payload))
    monkeypatch.setattr(os, "fstat", lambda fd: SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600, st_size=len(payload),
    ))
    with pytest.raises(ValueError):
        drill.read(Path("/drill"), "failure.json", drill.Failure)
    assert flags == [os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW]


@pytest.mark.parametrize("files,code", [
    ({}, "archive_file_limit"),
    ({"0" * 24: 16 * 1024 * 1024}, "required_wal_missing"),
    ({"1" * 24: 1024}, "incomplete_wal_segment"),
    ({"../escape": 16}, "unexpected_archive_file"),
    ({f"{number:024X}": 16 * 1024 * 1024 for number in range(17)}, "archive_byte_limit"),
])
def test_missing_truncated_unexpected_or_excess_wal_fails_closed(files, code):
    with pytest.raises(drill.DrillError, match=code):
        drill.validate_archive_files(files, "1" * 24, "1" * 24)


def test_archive_wait_requires_actual_done_marker_not_elapsed_guess(monkeypatch):
    _, target, _, _ = references()
    queries = []

    class Connection:
        def execute(self, query, params=None):
            queries.append(query)
            if "pg_walfile_name" in query:
                return SimpleNamespace(fetchone=lambda: {"name": target.restore_point.wal_file})
            if "pg_switch_wal" in query:
                return SimpleNamespace(fetchone=lambda: {"lsn": "0/600"})
            return SimpleNamespace(fetchone=lambda: {"size": None})

    clock = iter((0.0, 91.0))
    monkeypatch.setattr(drill.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(drill, "read", lambda *args: target)
    monkeypatch.setattr(drill.psycopg, "connect", lambda *args, **kwargs: nullcontext(Connection()))
    with pytest.raises(drill.DrillError, match="required_wal_not_archived"):
        drill.archive(Path("/drill"), "owned-url")
    assert any("pg_stat_file" in query for query in queries)


def mocked_verifier(monkeypatch, status_change=None, accept_write=False, operations_change=None):
    seed, target, latest, backup = references()
    values = {
        "seed.json": seed, "target.json": target, "latest.json": latest,
        "backup.json": backup, "artifacts.json": artifacts(),
    }
    queries, writes, rollback = [], [], []
    state = {
        "recovering": True, "paused": True, "read_only": "on", "lsn": "0/410",
        "target": drill.TARGET_NAME, "action": "pause",
    } | (status_change or {})

    class Connection:
        def execute(self, query):
            queries.append(query)
            if query.startswith("INSERT"):
                if not accept_write:
                    raise psycopg.errors.ReadOnlySqlTransaction()
                return None
            assert query.startswith("SELECT")
            return SimpleNamespace(fetchone=lambda: state)

        def transaction(self, *, force_rollback):
            rollback.append(force_rollback)
            return nullcontext()

    monkeypatch.setattr(drill, "read", lambda directory, name, model: values[name])
    monkeypatch.setattr(drill, "artifact_state", lambda directory: artifacts())
    monkeypatch.setattr(drill, "capture", lambda *args, **kwargs: target)
    observed = {
        "in_recovery": True, "primary_snapshot": False, "warnings": ("standby_snapshot",),
        "access_epoch": target.processing.access_epoch,
        "deletion_epoch": target.processing.deletion_epoch,
        "source": SimpleNamespace(total=1, denied=1, deleted=0, active_read_leases=0),
        "restore_authorized": False, "production_qualified": False,
    } | (operations_change or {})

    def read_status(url, request):
        assert url == "owned-url" and request.tenant_id == target.tenant_id
        return SimpleNamespace(**observed)

    monkeypatch.setattr(drill, "operations_status", read_status)
    monkeypatch.setattr(drill, "write_new", lambda *args: writes.append(args))
    monkeypatch.setattr(drill.psycopg, "connect", lambda *args, **kwargs: nullcontext(Connection()))
    return queries, writes, rollback


def test_paused_verifier_probes_real_read_only_without_committing(monkeypatch):
    queries, writes, rollback = mocked_verifier(monkeypatch)
    drill.verify(Path("/drill"), "owned-url")
    assert rollback == [True]
    assert writes[0][1] == "verification.json"
    assert writes[0][2].restore_authorized is False
    assert writes[0][2].operations_snapshot_verified is True
    assert any("WHERE false" in query for query in queries)
    assert not any("pg_promote" in query or "pg_wal_replay_resume" in query for query in queries)


@pytest.mark.parametrize("change", [
    {"in_recovery": False}, {"primary_snapshot": True}, {"warnings": ()},
    {"access_epoch": 999}, {"deletion_epoch": 999},
    {"restore_authorized": True}, {"production_qualified": True},
    {"source": SimpleNamespace(total=1, denied=1, deleted=1, active_read_leases=0)},
])
def test_paused_verifier_rejects_monitoring_mismatch_without_publishing(monkeypatch, change):
    _, writes, rollback = mocked_verifier(monkeypatch, operations_change=change)
    with pytest.raises(drill.DrillError, match="paused_operations_snapshot_mismatch"):
        drill.verify(Path("/drill"), "owned-url")
    assert rollback == [True] and not writes


@pytest.mark.parametrize("state", [
    {"recovering": False}, {"paused": False}, {"read_only": "off"},
    {"target": "another_point"}, {"action": "promote"},
])
def test_negative_verifier_never_promotes_resumes_or_starts_services(monkeypatch, state):
    queries, writes, rollback = mocked_verifier(monkeypatch, state)
    with pytest.raises(drill.DrillError, match="recovery_target_not_paused"):
        drill.verify(Path("/drill"), "owned-url")
    assert not writes
    assert not rollback
    assert len(queries) == 1 and queries[0].startswith("SELECT")
    assert "pg_promote" not in queries[0] and "pg_wal_replay_resume" not in queries[0]


def test_write_probe_success_is_failure_not_permission_to_continue(monkeypatch):
    _, writes, rollback = mocked_verifier(monkeypatch, accept_write=True)
    with pytest.raises(drill.DrillError, match="read_only_write_not_rejected"):
        drill.verify(Path("/drill"), "owned-url")
    assert rollback == [True] and not writes


def test_unexpected_exception_is_redacted(monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError("postgresql://private-password@external-host/private")

    monkeypatch.setattr(drill, "owned_environment", fail)
    assert drill.main(["seed"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "pitr_stage_failed\n"


@pytest.mark.parametrize("candidate", [
    "/", ".", "..", "../outside", ".review-artifacts/../outside", "/absolute/path",
    ".review-artifacts//ambiguous", ".review-artifacts/./ambiguous",
    ".review-artifacts/with space", ".review-artifacts/with:colon", "scripts",
    ".review-artifacts/back\\slash", ".review-artifacts/new\npath",
])
def test_output_path_rejection_precedes_engine_calls(candidate):
    result = subprocess.run(
        ["bash", str(RUNNER), candidate, "docker"], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert result.stderr == "new_private_project_path_required\n"


def test_cli_help_has_no_engine_or_directory_side_effects():
    result = subprocess.run(
        ["bash", str(RUNNER), "--help"], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "NEW_PRIVATE_PROJECT_DIRECTORY" in result.stdout
    assert "NOT release assets" in result.stdout
    assert "PGAG_PITR_RUNTIME_IMAGE" in result.stdout


def test_host_chmod_uses_portable_mode_and_absolute_operand():
    command = next(line for line in RUNNER.read_text().splitlines() if line.startswith("chmod "))
    result = subprocess.run(
        ["bash", "-c", """
set -eu
directory=-synthetic-owned-output
chmod() {
    [[ $# -eq 2 && "$1" == 700 && "$2" == "$PWD/$directory" ]]
    printf '%s\\n' "$@"
}
""" + command],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["700", str(ROOT / "-synthetic-owned-output")]


@pytest.mark.parametrize(
    "remove_status,original_status,expected", [(0, 0, 0), (1, 0, 1), (0, 7, 7)],
)
def test_cleanup_only_targets_owned_names_and_preserves_caller_image(
    remove_status, original_status, expected,
):
    text = RUNNER.read_text()
    cleanup = text.split("cleanup() {", 1)[1].split("    # No report exists", 1)[0]
    result = subprocess.run(
        ["bash", "-c", f"""
directory=nonexistent-pitr-contract-output
primary=owned-primary
containers=(owned-primary owned-restored owned-verify)
primary_destroyed=true
backup_verified=true
verification_complete=true
network_created=false
image_owned=false
image=caller-image-must-survive
timings='{{}}'
failure_code=drill_incomplete
remove_owned_container() {{ printf 'container:%s\\n' "$1"; return {remove_status}; }}
rm() {{ printf 'rm:%s\\n' "$@"; return 0; }}
jq() {{ printf '{{}}\\n'; }}
record_elapsed() {{ :; }}
cleanup() {{{cleanup}
    exit "$status"
}}
(exit {original_status})
cleanup
"""],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == expected
    assert result.stdout.splitlines() == [
        "container:owned-restored", "container:owned-verify",
        "rm:-f", "rm:--", "rm:nonexistent-pitr-contract-output/.credentials.env",
    ]


def test_runner_orders_physical_backup_target_archive_destruction_and_paused_recovery():
    script = RUNNER.read_text()
    markers = [
        'phase seed "$source_host"', "gosu postgres pg_basebackup",
        "gosu postgres pg_verifybackup /owned/base", 'phase target "$source_host"',
        'phase after "$source_host"', 'phase archive "$source_host"', "phase artifacts\n",
        '"$engine" stop "$primary"', "primary_destroyed=true\n",
        "touch \"$PGDATA/recovery.signal\"", "recovery_target_action=pause",
        'phase verify "$(container_host "$restored")"', "verification_complete=true\n",
    ]
    positions = [script.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert '[[ ! -L "$prefix" ]]' in script
    assert '[[ ! -e "$candidate" ]]' in script
    assert 'umask 077' in script and 'mkdir -m 700 -- "$directory"' in script
    assert '-v "$PWD:/work:ro"' in script and '-v "$PWD/src:/app/src:ro"' in script
    assert "set -o noclobber" in script
    assert "latest_state_matches: (if $verification == null then null" in script
    assert "else $verification.latest_state_matches end)" in script
    assert "pg_dump" not in script
    assert "pg_promote" not in script and "recovery_target_action=promote" not in script
    assert "pg_wal_replay_resume" not in script
    assert "--publish" not in script and "--privileged" not in script
    assert "rm -rf" not in script and "docker system prune" not in script
    assert "0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167" in script
