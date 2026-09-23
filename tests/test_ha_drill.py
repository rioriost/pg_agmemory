"""Offline HA contracts; no live databases, engines, or scratch directories."""

import asyncio
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pg_agmemory import __version__
from pg_agmemory.processing_recovery import TABLES
from pg_agmemory.replication_status import ReplicationStatus

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "test-ha-containers.sh"
spec = importlib.util.spec_from_file_location("ha_drill", ROOT / "scripts" / "smoke-ha.py")
assert spec is not None and spec.loader is not None
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


def observation(role="primary"):
    standby = role == "standby"
    promoted = role == "promoted"
    return ReplicationStatus.model_validate_json(json.dumps({
        "evaluated_at": datetime(2026, 9, 23, tzinfo=UTC).isoformat(),
        "in_recovery": standby, "transaction_read_only": True,
        "synchronous_commit": "on" if standby or promoted else "remote_apply",
        "synchronous_standby_configured": not (standby or promoted),
        "primary_flush_lsn": None if standby else "0/500",
        "received_lsn": "0/500" if standby else None,
        "replayed_lsn": "0/500" if standby else None,
        "replay_paused": False if standby else None,
        "senders": {
            "total": 0 if standby or promoted else 1,
            "physical_streaming": 0 if standby or promoted else 1,
            "physical_synchronous": 0 if standby or promoted else 1, "logical": 0,
        },
        "receiver": {"present": standby, "streaming": standby},
        "slots": {"total": 0, "physical": 0, "logical": 0, "inactive": 0}, "warnings": [],
    }))


def references():
    fixture = drill.Fixture(
        **{name: uuid4() for name in (
            "tenant_id", "scope_id", "writer_id", "reader_id", "before_id",
            "checkpoint_id", "run_id", "effect_id",
        )},
        acknowledged_ids=tuple(uuid4() for _ in range(3)),
    )
    processing = drill.ProcessingRecoverySnapshot(
        tenant_id=fixture.tenant_id, lineage="a" * 64, access_epoch=2, deletion_epoch=1,
        tables=tuple(
            drill.StateFingerprint(table=name, rows=1, digest="b" * 64) for name in TABLES
        ),
    )
    reference = drill.Reference(
        stage="acknowledged", fixture=fixture,
        control=drill.pitr.Control(
            system_identifier="1234567890123456789", timeline=1, lsn="0/500",
        ),
        source_cursor=drill.pitr.SourceCursor(sequence=1, decision="deny", reason="revoked"),
        episodes=tuple(drill.pitr.Episode(object_id=identity, content_sha256="c" * 64)
                       for identity in (fixture.before_id, *fixture.acknowledged_ids)),
        content=tuple(drill.StateFingerprint(table=name, rows=1, digest="d" * 64)
                      for name in drill.CONTENT_TABLES),
        processing=processing, effect_status="dispatched", effect_revision=2,
    )
    promoted = reference.model_copy(update={
        "stage": "promoted", "control": reference.control.model_copy(update={"timeline": 2}),
    })
    return reference, promoted


def passed_report():
    reference, _ = references()
    return drill.Report(
        status="passed", failure_code=None, source_destroyed=True, fencing_verified=True,
        pre_fence_promotion_rejected=True, promotion_executed=True, backup_verified=True,
        writer_policy_verified=True, short_pause_blocked_ack=True,
        acknowledged_state_matches=True, effect_state_preserved=True,
        postpromotion_probe_verified=True, timeline_before=1, timeline_after=2,
        artifact=drill.pitr.Artifact(bytes=1024, sha256="a" * 64),
        synchronous=drill.SynchronousEvidence(
            writer_policy_verified=True, writer_synchronous_commit="remote_apply",
            short_pause_blocked_ack=True, sync_rep_wait_observed=True, pause_seconds=0.2,
            acknowledgements=reference.fixture.acknowledged_ids,
            primary=observation(), standby=observation("standby"),
        ),
        preserved=drill.PreservedEvidence(
            acknowledged_state_matches=True, effect_state_preserved=True,
            timeline_before=1, timeline_after=2, promoted=observation("promoted"),
        ),
        probe=drill.ProbeEvidence(
            postpromotion_probe_verified=True, probe_id=uuid4(), baseline_state_changed=True,
            writer_synchronous_commit="on", no_synchronous_standby=True,
        ),
        elapsed_seconds={"total": 17.0},
    )


def test_report_pins_schema_and_never_authorizes_general_serving():
    report = passed_report()
    assert report.service_version == __version__
    assert report.schema_version == 21 and report.api_version == "v1"
    assert report.postgres_version_num == 180006 and report.pgvector_version == "0.8.6"
    for field in (
        "production_qualified", "host_failure_domain_independent", "network_partition_qualified",
        "commit_timeout_qualified", "automatic_failover", "automatic_service_start",
        "serving_authorized", "effect_reexecution",
    ):
        assert getattr(report, field) is False
        with pytest.raises(ValidationError):
            drill.Report.model_validate(report.model_dump() | {field: True})


def test_failed_report_keeps_unmeasured_facts_null():
    report = drill.Report(
        status="failed", failure_code="engine_unavailable", elapsed_seconds={"total": 0.0},
    )
    for field in (
        "source_destroyed", "fencing_verified", "pre_fence_promotion_rejected",
        "promotion_executed", "backup_verified", "writer_policy_verified",
        "short_pause_blocked_ack",
        "acknowledged_state_matches", "effect_state_preserved", "postpromotion_probe_verified",
    ):
        assert getattr(report, field) is None
        with pytest.raises(ValidationError):
            drill.Report.model_validate(report.model_dump() | {field: False})


@pytest.mark.parametrize("field", [
    "source_destroyed", "fencing_verified", "pre_fence_promotion_rejected", "promotion_executed",
    "backup_verified", "artifact", "synchronous", "preserved", "probe",
])
def test_pass_cannot_omit_its_evidence(field):
    with pytest.raises(ValidationError):
        drill.Report.model_validate(passed_report().model_dump() | {field: None})


def test_promotion_observation_without_fencing_never_validates():
    with pytest.raises(ValidationError, match="promotion_without_verified_fence"):
        drill.Report(
            status="failed", failure_code="unsafe_promotion", promotion_executed=True,
            elapsed_seconds={"total": 1.0},
        )


@pytest.mark.parametrize("field", [
    "writer_policy_verified", "short_pause_blocked_ack", "acknowledged_state_matches",
    "effect_state_preserved", "postpromotion_probe_verified",
])
def test_measurement_cannot_be_invented_without_its_proof(field):
    with pytest.raises(ValidationError, match="unmeasured_or_inconsistent_evidence"):
        drill.Report(
            status="failed", failure_code="stage_failed", elapsed_seconds={"total": 1.0},
            **{field: True},
        )


@pytest.mark.parametrize("seconds", [-1.0, float("nan"), float("inf"), 7200.01])
def test_report_elapsed_bounds(seconds):
    with pytest.raises(ValidationError):
        drill.Report(
            status="failed", failure_code="stage_failed", elapsed_seconds={"total": seconds},
        )


@pytest.mark.parametrize("seconds", [0.0, 1.0, 1.01, float("nan")])
def test_long_or_nonfinite_pause_is_not_qualified(seconds):
    evidence = passed_report().synchronous.model_dump() | {"pause_seconds": seconds}
    with pytest.raises(ValidationError):
        drill.SynchronousEvidence.model_validate(evidence)


def test_exact_acknowledged_state_survives_only_with_new_timeline():
    reference, promoted = references()
    drill.check_preserved(reference, reference, promoted=False)
    drill.check_preserved(reference, promoted, promoted=True)
    with pytest.raises(drill.pitr.DrillError, match="timeline_mismatch"):
        drill.check_preserved(reference, reference, promoted=True)


@pytest.mark.parametrize("change,code", [
    ("episode", "acknowledged_content_mismatch"),
    ("canonical", "acknowledged_content_mismatch"),
    ("processing", "acknowledged_processing_mismatch"),
    ("identity", "acknowledged_identity_mismatch"),
    ("system", "physical_cluster_identity_mismatch"),
    ("effect", "source_or_effect_state_mismatch"),
    ("source", "source_or_effect_state_mismatch"),
])
def test_missing_or_mutated_acknowledged_state_fails(change, code):
    reference, promoted = references()
    if change == "episode":
        promoted = promoted.model_copy(update={"episodes": promoted.episodes[:-1]})
    elif change == "canonical":
        promoted = promoted.model_copy(update={"content": ()})
    elif change == "processing":
        promoted = promoted.model_copy(update={"processing": promoted.processing.model_copy(
            update={"access_epoch": 3},
        )})
    elif change == "identity":
        promoted = promoted.model_copy(update={"fixture": promoted.fixture.model_copy(
            update={"checkpoint_id": uuid4()},
        )})
    elif change == "system":
        promoted = promoted.model_copy(update={"control": promoted.control.model_copy(
            update={"system_identifier": "42"},
        )})
    elif change == "effect":
        promoted = promoted.model_copy(update={"effect_status": "confirmed"})
    else:
        promoted = promoted.model_copy(update={"source_cursor": drill.pitr.SourceCursor(
            sequence=2, decision="deny", reason="deleted",
        )})
    with pytest.raises(drill.pitr.DrillError, match=code):
        drill.check_preserved(reference, promoted, promoted=True)


def test_reference_rejects_missing_ack_duplicate_ids_and_unknown_fields():
    reference, _ = references()
    for change in (
        {"private_key": "never-allowed"}, {"schema_version": 20}, {"effect_revision": 3},
        {"episodes": reference.model_dump(mode="json")["episodes"][:-1]},
        {"fixture": reference.fixture.model_dump(mode="json") | {"acknowledged_ids": []}},
    ):
        with pytest.raises(ValidationError):
            drill.Reference.model_validate_json(
                json.dumps(reference.model_dump(mode="json") | change)
            )


@pytest.mark.parametrize("change", ["missing_sender", "logical_sender", "receiver_down", "paused"])
def test_streaming_requires_real_physical_pair(change):
    primary, standby = observation(), observation("standby")
    if change in ("missing_sender", "logical_sender"):
        primary = primary.model_copy(update={"senders": primary.senders.model_copy(update={
            "total": 0 if change == "missing_sender" else 1,
            "physical_streaming": 0, "physical_synchronous": 0,
            "logical": 1 if change == "logical_sender" else 0,
        })})
    elif change == "receiver_down":
        standby = standby.model_copy(update={"receiver": standby.receiver.model_copy(
            update={"streaming": False},
        )})
    else:
        standby = standby.model_copy(update={"replay_paused": True})
    with pytest.raises(drill.pitr.DrillError, match="physical_streaming_pair_required"):
        drill.require_pair(primary, standby, True)


@pytest.mark.parametrize("change", [
    {"synchronous_commit": "on"}, {"synchronous_standby_configured": False},
])
def test_remote_apply_is_required_for_healthy_acknowledgements(change):
    primary = observation().model_copy(update=change)
    with pytest.raises(drill.pitr.DrillError, match="synchronous_standby_required"):
        drill.require_pair(primary, observation("standby"), True)


@pytest.mark.parametrize("policy,privileged,timeout,expected", [
    ("remote_apply", False, "5s", True), ("remote_apply", False, "5000ms", True),
    ("on", False, "5s", False), ("remote_apply", True, "5s", False),
    ("remote_apply", False, "0", False),
])
def test_actual_writer_connection_policy_not_inferred_from_admin_probe(
    policy, privileged, timeout, expected,
):
    class Connection:
        async def execute(self, query):
            assert "current_user" in query

            async def fetchone():
                return {"policy": policy, "privileged": privileged, "timeout": timeout}

            return SimpleNamespace(fetchone=fetchone)

    if expected:
        asyncio.run(drill.writer_policy(Connection(), "remote_apply"))
    else:
        with pytest.raises(drill.pitr.DrillError, match="writer_policy_not_verified"):
            asyncio.run(drill.writer_policy(Connection(), "remote_apply"))


def guard_script():
    text = RUNNER.read_text()
    return "owned_promote() {" + text.split("owned_promote() {", 1)[1].split(
        "\ncontainer_host() {", 1,
    )[0]


@pytest.mark.parametrize("overrides,expected,promote_calls", [
    ("allow_owned_promotion=false", 41, 0),
    ("source_destroyed=null; fencing_verified=null", 42, 0),
    ("source_destroyed=true; fencing_verified=null", 42, 0),
    ("pre_fence_promotion_rejected=null", 43, 0),
    ("standby=unowned-candidate", 44, 0),
    ("fence_status=1", 45, 0),
    ("mock_candidate_state='999|1|true|on'", 46, 0),
    ("mock_candidate_state='1234567890123456789|1|false|off'", 46, 0),
    ("", 0, 1),
])
def test_live_harness_promotion_guard_is_closed_before_owned_fencing(
    overrides, expected, promote_calls,
):
    result = subprocess.run(
        ["bash", "-c", """
allow_owned_promotion=true
run_id=pgag-ha-1-2-1234567890abcdef
primary=$run_id-primary
standby=$run_id-standby
source_destroyed=true
fencing_verified=true
pre_fence_promotion_rejected=true
promotion_executed=null
system_identifier=1234567890123456789
timeline_before=1
fence_status=0
mock_candidate_state='1234567890123456789|1|true|on'
engine=fake_engine
exec 3>&2
verify_primary_absent() { return "$fence_status"; }
fake_engine() {
    case "$*" in
        *pg_promote*) printf 'promotion-call\\n' >&3; printf 't\\n' ;;
        *) printf '%s\\n' "$mock_candidate_state" ;;
    esac
}
""" + overrides + "\n" + guard_script() + """
owned_promote
status=$?
printf 'executed:%s\\n' "$promotion_executed"
exit "$status"
"""],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == expected
    assert result.stderr.count("promotion-call") == promote_calls
    assert result.stdout.strip() == f"executed:{'true' if promote_calls else 'null'}"


@pytest.mark.parametrize("arguments", [
    [], [".review-artifacts/ha-uncreated"], [".review-artifacts/ha-uncreated", "docker"],
    [".review-artifacts/ha-uncreated", "--allow-owned-promotion=false"],
])
def test_missing_opt_in_fails_before_path_or_engine_activity(arguments):
    result = subprocess.run(
        ["bash", str(RUNNER), *arguments], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert result.stderr.startswith("explicit_owned_promotion_opt_in_required\n")


@pytest.mark.parametrize("path", [
    "/", ".", "..", "../outside", ".review-artifacts/../outside", "/absolute/path",
    ".review-artifacts//ambiguous", ".review-artifacts/./ambiguous",
    ".review-artifacts/with space", ".review-artifacts/with:colon", "scripts",
])
def test_invalid_owned_paths_fail_before_resources(path):
    result = subprocess.run(
        ["bash", str(RUNNER), path, "--allow-owned-promotion", "docker"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2 and result.stderr == "new_private_project_path_required\n"


def test_help_is_side_effect_free_and_explains_degraded_lab_only():
    result = subprocess.run(
        ["bash", str(RUNNER), "--help"], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "--allow-owned-promotion" in result.stdout
    assert "NOT production HA" in result.stdout and "not serving-authorized" in result.stdout


def test_unexpected_failure_is_redacted(monkeypatch, capsys):
    def failed_environment():
        raise RuntimeError("postgresql://private-password@external-host/database")

    monkeypatch.setattr(drill, "owned_environment", failed_environment)
    assert drill.main(["seed"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "ha_stage_failed\n"


@pytest.mark.parametrize("engine,host", [
    ("docker", "unowned-primary"), ("container", "8.8.8.8"), ("container", "127.0.0.1"),
])
def test_helper_rejects_nonowned_or_external_database_hosts(monkeypatch, engine, host):
    for name in ("PGAG_DATABASE_URL", "PGAG_ADMIN_DATABASE_URL", "PGHOST", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(drill.platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_mode=0o40700))
    monkeypatch.setenv("PGAG_HA_OWNED_RUN", "pgag-ha-1-2-1234567890abcdef")
    monkeypatch.setenv("PGAG_HA_ALLOW_OWNED_PROMOTION", "1")
    monkeypatch.setenv("PGAG_HA_ENGINE", engine)
    monkeypatch.setenv("PGAG_HA_PRIMARY_HOST", host)
    with pytest.raises(drill.pitr.DrillError, match="owned_database_required"):
        drill.owned_environment()


@pytest.mark.parametrize("image_owned", ["false", "true"])
def test_failed_cleanup_removes_only_recorded_owned_resources(image_owned):
    cleanup = RUNNER.read_text().split("cleanup() {", 1)[1].split(
        "    # Terminal report only:", 1,
    )[0]
    result = subprocess.run(
        ["bash", "-c", f"""
exec 3>&1
directory=nonexistent-ha-contract-output
primary=owned-primary
containers=(owned-primary owned-standby owned-verify)
source_destroyed=true
fencing_verified=true
pre_fence_promotion_rejected=true
promotion_executed=true
backup_verified=true
network_created=false
image_owned={image_owned}
image=explicit-owned-image
engine=fake_engine
timings='{{}}'
failure_code=verify_failed
remove_owned_container() {{ printf 'container:%s\\n' "$1"; return 0; }}
fake_engine() {{ printf 'engine:%s\\n' "$*" >&3; return 0; }}
rm() {{ printf 'rm:%s\\n' "$@"; return 0; }}
jq() {{ printf '{{}}\\n'; }}
record_elapsed() {{ :; }}
cleanup() {{{cleanup}
    exit "$status"
}}
(exit 7)
cleanup
"""],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    expected = ["container:owned-standby", "container:owned-verify"]
    if image_owned == "true":
        expected.append("engine:image rm explicit-owned-image")
    expected += [
        "rm:-f", "rm:--", "rm:nonexistent-ha-contract-output/.credentials.env",
        "rm:nonexistent-ha-contract-output/.standby.conf",
    ]
    assert result.stdout.splitlines() == expected


def test_reuses_bounded_backup_tools_and_cannot_promote_from_python_evidence():
    source = (ROOT / "scripts" / "smoke-ha.py").read_text()
    assert 'with_name("smoke-pitr.py")' in source
    assert "pitr.inspect_tar(" in source
    assert "class Artifact(" not in source and "class Backup(" not in source
    assert "pg_promote" not in source
    assert "wait_for(" not in source and ".cancel(" not in source
    assert "SET statement_timeout" not in source
    assert "await standby.execute(\"SELECT pg_wal_replay_resume()\")" in source


def test_failure_record_write_error_is_explicit_and_redacted(monkeypatch, capsys):
    async def fail_seed(*args):
        raise RuntimeError("postgresql://private-password@external-host/database")

    def fail_record(*args):
        raise OSError("private-file-path")

    monkeypatch.setattr(drill, "owned_environment", lambda: (Path("/drill"), "owned", None))
    monkeypatch.setattr(drill, "seed", fail_seed)
    monkeypatch.setattr(drill, "write_new", fail_record)
    assert drill.main(["seed"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "failure_record_write_failed\n"


def test_runner_order_private_credentials_and_owned_only_cleanup():
    script = RUNNER.read_text()
    tail = script.split("failure_code=engine_unavailable\n", 1)[1]
    markers = [
        "phase seed\n", "gosu postgres pg_basebackup", "gosu postgres pg_verifybackup /owned/base",
        "phase artifact\n", 'touch "$PGDATA/standby.signal"', "phase synchronous\n",
        "if owned_promote; then", "phase prefence\n", 'remove_owned_container "$primary"\n',
        "verify_primary_absent\n", "source_destroyed=true\n", "fencing_verified=true\n",
        "owned_promote\n", "phase verify\n",
    ]
    positions = [tail.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert '[[ ! -L "$prefix" ]]' in script
    assert 'chmod 700 "$PWD/$directory"' in script
    assert "chmod 700 --" not in script
    assert '-v "$PWD:/work:ro"' in script
    assert '--env-file "$directory/.credentials.env"' in script
    assert 'if [[ "$image_owned" == true ]]' in script
    assert 'rm -f -- "$directory/.credentials.env" "$directory/.standby.conf"' in script
    assert "set -o noclobber" in script
    assert "source_destroyed=null" in script and "promotion_executed=null" in script
    assert "--publish" not in script and "rm -rf" not in script and "system prune" not in script
    assert "0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167" in script
