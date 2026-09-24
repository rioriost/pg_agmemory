"""Offline HA contracts; no live databases, engines, or scratch directories."""

import ast
import asyncio
import importlib.util
import json
import subprocess
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pg_agmemory import __version__
from pg_agmemory.commit_deadline import COMMIT_ACK_TIMEOUT_SECONDS
from pg_agmemory.processing_recovery import TABLES
from pg_agmemory.replication_status import ReplicationStatus
from pg_agmemory.transactions import CommitOutcomeUnknown, async_transaction

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


def uncertain_evidence(memory_id=None):
    return drill.UncertainEvidence(
        memory_id=memory_id or uuid4(), outcome_code="commit_outcome_unknown",
        uncertain_commit_reconciled=True, retryable=False, success_receipt_emitted=False,
        writer_policy_verified=True, writer_synchronous_commit="remote_apply",
        statement_timeout_seconds=5, lock_timeout_seconds=5, commit_timeout_seconds=5,
        sync_rep_wait_observed=True, local_wal_flush_observed=True,
        local_wal_flush_scope="precommit_insert_lsn_lower_bound",
        local_receipt_observed=True, local_receipt_observed_before_client_exit=False,
        client_connection_closed=True,
        commit_wait_seconds=5.05, sync_rep_observed_seconds=4.95,
        replay_resumed_after_client_exit=True, replica_state_matches=True,
        read_only_reconciliation=True, no_retry=True,
    )


def references(stage="reconciled"):
    uncertain = uncertain_evidence() if stage == "reconciled" else None
    fixture = drill.Fixture(
        **{name: uuid4() for name in (
            "tenant_id", "scope_id", "writer_id", "reader_id", "before_id",
            "checkpoint_id", "run_id", "effect_id",
        )},
        acknowledged_ids=tuple(uuid4() for _ in range(3)),
        uncertain_id=uncertain.memory_id if uncertain is not None else None,
    )
    processing = drill.ProcessingRecoverySnapshot(
        tenant_id=fixture.tenant_id, lineage="a" * 64, access_epoch=2, deletion_epoch=1,
        tables=tuple(
            drill.StateFingerprint(table=name, rows=1, digest="b" * 64) for name in TABLES
        ),
    )
    reference = drill.Reference(
        stage=stage, fixture=fixture, uncertain=uncertain,
        control=drill.pitr.Control(
            system_identifier="1234567890123456789", timeline=1, lsn="0/500",
        ),
        source_cursor=drill.pitr.SourceCursor(sequence=1, decision="deny", reason="revoked"),
        episodes=tuple(drill.pitr.Episode(object_id=identity, content_sha256="c" * 64)
                       for identity in (
                           fixture.before_id, *fixture.acknowledged_ids,
                           *((fixture.uncertain_id,) if uncertain is not None else ()),
                       )),
        content=tuple(drill.StateFingerprint(table=name, rows=1, digest="d" * 64)
                      for name in drill.CONTENT_TABLES),
        processing=processing, effect_status="dispatched", effect_revision=2,
    )
    promoted = reference.model_copy(update={
        "stage": "promoted", "control": reference.control.model_copy(update={"timeline": 2}),
    })
    return reference, promoted


def reconcile_reference(reference, evidence):
    growing = {
        "memory.episode", "memory.episode_lexical", "memory.object", "memory_ops.audit_event",
        "memory_ops.source_event", "memory_ops.idempotency",
    }

    def increment(item):
        return item.model_copy(update={"rows": item.rows + 1, "digest": "e" * 64}) if (
            item.table in growing
        ) else item

    return reference.model_copy(update={
        "stage": "reconciled", "uncertain": evidence,
        "fixture": reference.fixture.model_copy(update={"uncertain_id": evidence.memory_id}),
        "episodes": reference.episodes + (drill.pitr.Episode(
            object_id=evidence.memory_id, content_sha256="e" * 64,
        ),),
        "content": tuple(increment(item) for item in reference.content),
        "processing": reference.processing.model_copy(update={
            "tables": tuple(increment(item) for item in reference.processing.tables),
        }),
    })


def post_probe_reference():
    _, promoted = references()
    probe_id = uuid4()
    return drill.Reference.model_validate(promoted.model_dump() | {
        "stage": "post-probe",
        "fixture": promoted.fixture.model_dump() | {"probe_id": probe_id},
        "episodes": promoted.episodes + (drill.pitr.Episode(
            object_id=probe_id, content_sha256="f" * 64,
        ),),
    })


def replacement_observations():
    primary = observation("promoted")
    primary = primary.model_copy(update={
        "primary_flush_lsn": "0/800",
        "senders": primary.senders.model_copy(update={"total": 1, "physical_streaming": 1}),
    })
    standby = observation("standby").model_copy(update={
        "received_lsn": "0/800", "replayed_lsn": "0/800",
    })
    return primary, standby


def replacement_evidence(uncertain_id=None, probe_id=None):
    primary, standby = replacement_observations()
    return drill.ReplacementEvidence(
        replacement_state_matches=True, kind="fresh_basebackup",
        backup_manifest_verified=True, no_old_primary_reuse=True,
        fresh_empty_data_directory=True, read_only_recovery=True, canonical_state_matches=True,
        processing_state_matches=True, source_state_matches=True, effect_state_preserved=True,
        streaming_wal_advance=True, synchronous_policy_renewed=False,
        writer_synchronous_commit="on",
        uncertain_id=uncertain_id or uuid4(), original_outcome_code="commit_outcome_unknown",
        probe_id=probe_id or uuid4(), system_identifier="1234567890123456789", timeline=2,
        backup=drill.pitr.Backup(
            format="pgag-pitr-basebackup-v1", manifest_verified=True,
            start_lsn="0/600", end_lsn="0/700", timeline=2,
        ),
        artifact=drill.pitr.Artifact(bytes=2048, sha256="e" * 64),
        wal_target_lsn="0/800", primary=primary, standby=standby,
    )


def passed_report():
    reference, _ = references()
    probe_id = uuid4()
    return drill.Report(
        status="passed", failure_code=None, source_destroyed=True, fencing_verified=True,
        pre_fence_promotion_rejected=True, promotion_executed=True, backup_verified=True,
        writer_policy_verified=True, short_pause_blocked_ack=True,
        uncertain_commit_reconciled=True, uncertain=reference.uncertain,
        acknowledged_state_matches=True, effect_state_preserved=True,
        postpromotion_probe_verified=True, timeline_before=1, timeline_after=2,
        replacement_state_matches=True, original_primary_remains_fenced=True,
        replacement_backup_verified=True,
        replacement=replacement_evidence(reference.fixture.uncertain_id, probe_id),
        artifact=drill.pitr.Artifact(bytes=1024, sha256="a" * 64),
        synchronous=drill.SynchronousEvidence(
            writer_policy_verified=True, writer_synchronous_commit="remote_apply",
            short_pause_blocked_ack=True, sync_rep_wait_observed=True, pause_seconds=0.2,
            acknowledgements=reference.fixture.acknowledged_ids,
            primary=observation(), standby=observation("standby"),
        ),
        preserved=drill.PreservedEvidence(
            acknowledged_state_matches=True, effect_state_preserved=True,
            uncertain_state_matches=True, uncertain_id=reference.fixture.uncertain_id,
            original_outcome_code="commit_outcome_unknown",
            timeline_before=1, timeline_after=2, promoted=observation("promoted"),
        ),
        probe=drill.ProbeEvidence(
            postpromotion_probe_verified=True, probe_id=probe_id, baseline_state_changed=True,
            writer_synchronous_commit="on", no_synchronous_standby=True,
        ),
        elapsed_seconds={"total": 17.0},
    )


def test_report_pins_schema_and_never_authorizes_general_serving():
    report = passed_report()
    assert report.format == "pgag-ha-drill-v3"
    assert report.service_version == __version__
    assert report.schema_version == 22 and report.api_version == "v1"
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
        "short_pause_blocked_ack", "uncertain_commit_reconciled",
        "acknowledged_state_matches", "effect_state_preserved", "postpromotion_probe_verified",
        "replacement_state_matches", "original_primary_remains_fenced",
        "replacement_backup_verified",
    ):
        assert getattr(report, field) is None
        with pytest.raises(ValidationError):
            drill.Report.model_validate(report.model_dump() | {field: False})


@pytest.mark.parametrize("field", [
    "source_destroyed", "fencing_verified", "pre_fence_promotion_rejected", "promotion_executed",
    "backup_verified", "artifact", "synchronous", "uncertain", "preserved", "probe", "replacement",
    "original_primary_remains_fenced", "replacement_backup_verified",
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
    "effect_state_preserved", "postpromotion_probe_verified", "uncertain_commit_reconciled",
    "replacement_state_matches",
])
def test_measurement_cannot_be_invented_without_its_proof(field):
    with pytest.raises(ValidationError, match="unmeasured_or_inconsistent_evidence"):
        drill.Report(
            status="failed", failure_code="stage_failed", elapsed_seconds={"total": 1.0},
            **{field: True},
        )


@pytest.mark.parametrize("status", ["passed", "failed"])
@pytest.mark.parametrize("proof,flag", [(False, True), (True, None), (True, False)])
def test_uncertain_report_measurement_must_match_its_proof(status, proof, flag):
    report = passed_report().model_dump() | {
        "status": status, "failure_code": None if status == "passed" else "verify_failed",
        "uncertain_commit_reconciled": flag,
    }
    if not proof:
        report["uncertain"] = None
    with pytest.raises(ValidationError):
        drill.Report.model_validate(report)


def test_pass_cannot_skip_the_entire_uncertain_stage():
    with pytest.raises(ValidationError):
        drill.Report.model_validate(passed_report().model_dump() | {
            "uncertain": None, "uncertain_commit_reconciled": None,
        })


@pytest.mark.parametrize("status", ["passed", "failed"])
@pytest.mark.parametrize("proof,flag", [(False, True), (True, None), (True, False)])
def test_replacement_report_measurement_must_match_its_proof(status, proof, flag):
    report = passed_report().model_dump() | {
        "status": status, "failure_code": None if status == "passed" else "cleanup_failed",
        "replacement_state_matches": flag,
    }
    if not proof:
        report["replacement"] = None
    with pytest.raises(ValidationError):
        drill.Report.model_validate(report)


def test_pass_cannot_skip_the_entire_replacement_stage():
    with pytest.raises(ValidationError):
        drill.Report.model_validate(passed_report().model_dump() | {
            "replacement": None, "replacement_state_matches": None,
        })


def test_failed_report_can_preserve_measured_uncertainty_without_authority():
    completed = passed_report()
    report = drill.Report(
        status="failed", failure_code="prefence_failed",
        synchronous=completed.synchronous,
        writer_policy_verified=True, short_pause_blocked_ack=True,
        uncertain=completed.uncertain, uncertain_commit_reconciled=True,
        elapsed_seconds={"total": 8.0},
    )
    assert report.uncertain_commit_reconciled is True
    assert report.promotion_executed is None and report.fencing_verified is None
    assert report.serving_authorized is False and report.effect_reexecution is False


@pytest.mark.parametrize("field", [
    "backup_manifest_verified", "no_old_primary_reuse", "fresh_empty_data_directory",
    "read_only_recovery", "canonical_state_matches", "processing_state_matches",
    "source_state_matches", "effect_state_preserved", "streaming_wal_advance",
    "replacement_state_matches",
])
@pytest.mark.parametrize("value", [None, False])
def test_replacement_cannot_omit_or_negate_required_proof(field, value):
    with pytest.raises(ValidationError):
        drill.ReplacementEvidence.model_validate(replacement_evidence().model_dump() | {
            field: value,
        })


def test_replacement_requires_every_mandatory_measurement():
    evidence = replacement_evidence().model_dump()
    for field, definition in drill.ReplacementEvidence.model_fields.items():
        if definition.is_required():
            with pytest.raises(ValidationError):
                drill.ReplacementEvidence.model_validate({
                    key: value for key, value in evidence.items() if key != field
                })


@pytest.mark.parametrize("field", [
    "production_qualified", "network_partition_qualified", "commit_timeout_qualified",
    "automatic_failover", "automatic_service_start", "serving_authorized", "effect_reexecution",
    "synchronous_policy_renewed",
])
def test_replacement_never_renews_policy_or_authorizes_serving_or_effects(field):
    evidence = replacement_evidence()
    assert getattr(evidence, field) is False
    with pytest.raises(ValidationError):
        drill.ReplacementEvidence.model_validate(evidence.model_dump() | {field: True})


@pytest.mark.parametrize("change", [
    {"kind": "rejoin"}, {"kind": "pg_rewind"}, {"writer_synchronous_commit": "remote_apply"},
    {"original_outcome_code": "committed"}, {"retryable": True},
])
def test_replacement_cannot_claim_rejoin_renewed_synchronous_policy_or_commit_success(change):
    with pytest.raises(ValidationError):
        drill.ReplacementEvidence.model_validate(replacement_evidence().model_dump() | change)


@pytest.mark.parametrize("field,value", [
    ("manifest_verified", False), ("timeline", 1), ("end_lsn", "0/800"),
    ("start_lsn", "0/700"), ("start_lsn", "0/900"),
])
def test_replacement_requires_new_manifest_verified_backup_before_wal_target(field, value):
    evidence = replacement_evidence().model_dump()
    evidence["backup"][field] = value
    with pytest.raises(ValidationError):
        drill.ReplacementEvidence.model_validate(evidence)


@pytest.mark.parametrize("node,field", [
    ("primary", "primary_flush_lsn"), ("standby", "received_lsn"), ("standby", "replayed_lsn"),
])
@pytest.mark.parametrize("lsn", [None, "0/700", "0/7FF"])
def test_replacement_cannot_claim_wal_progress_before_both_receive_and_replay(node, field, lsn):
    evidence = replacement_evidence().model_dump()
    evidence[node][field] = lsn
    with pytest.raises(ValidationError):
        drill.ReplacementEvidence.model_validate(evidence)


@pytest.mark.parametrize("field", ["probe_id", "uncertain_id", "timeline"])
def test_replacement_report_cannot_change_probe_uncertainty_or_promoted_timeline(field):
    report = passed_report().model_dump()
    report["replacement"][field] = 3 if field == "timeline" else uuid4()
    if field == "timeline":
        report["replacement"]["backup"]["timeline"] = 3
    with pytest.raises(ValidationError, match="replacement_report_identity_mismatch"):
        drill.Report.model_validate(report)


def test_replacement_report_requires_the_verified_probe_first():
    report = passed_report().model_dump() | {
        "probe": None, "postpromotion_probe_verified": None, "replacement_backup_verified": None,
    }
    with pytest.raises(ValidationError, match="replacement_before_verified_probe_and_fence"):
        drill.Report.model_validate(report)


def test_replacement_report_cannot_invent_a_live_fence_from_json_evidence():
    with pytest.raises(ValidationError, match="replacement_fence_not_verified"):
        drill.Report(
            status="failed", failure_code="replacement_failed", elapsed_seconds={"total": 1.0},
            original_primary_remains_fenced=True,
        )


def test_replacement_backup_cannot_be_measured_before_the_verified_probe():
    with pytest.raises(ValidationError, match="replacement_backup_before_verified_probe"):
        drill.Report(
            status="failed", failure_code="replacement_failed", elapsed_seconds={"total": 1.0},
            replacement_backup_verified=True,
        )


def test_partial_failed_rebuild_retains_only_the_measured_backup_fact():
    report = drill.Report.model_validate(passed_report().model_dump() | {
        "status": "failed", "failure_code": "replacement_start_failed",
        "replacement": None, "replacement_state_matches": None,
        "original_primary_remains_fenced": None,
    })
    assert report.replacement_backup_verified is True
    assert report.replacement_state_matches is None
    assert report.original_primary_remains_fenced is None
    assert not report.serving_authorized and not report.effect_reexecution


@pytest.mark.parametrize("field", [
    "fresh_empty_data_directory", "no_old_primary_reuse",
])
def test_replacement_ownership_requires_fresh_data_without_claiming_fence_authority(field):
    ownership = {
        "kind": "fresh_basebackup", "fresh_empty_data_directory": True,
        "no_old_primary_reuse": True,
    }
    drill.ReplacementOwnership.model_validate(ownership)
    with pytest.raises(ValidationError):
        drill.ReplacementOwnership.model_validate(ownership | {field: False})
    with pytest.raises(ValidationError):
        drill.ReplacementOwnership.model_validate({
            key: value for key, value in ownership.items() if key != field
        })
    with pytest.raises(ValidationError):
        drill.ReplacementOwnership.model_validate(ownership | {
            "original_primary_remains_fenced": True,
        })


@pytest.mark.parametrize("node,field,value", [
    ("primary", "synchronous_commit", "remote_apply"),
    ("primary", "synchronous_standby_configured", True),
    ("primary", "schema_version", 21),
    ("standby", "service_version", "0.0.0"),
    ("standby", "synchronous_standby_configured", True),
    ("standby", "synchronous_commit", "remote_apply"),
    ("standby", "replay_paused", True),
    ("standby", "transaction_read_only", False),
])
def test_replacement_observations_cannot_claim_renewed_policy_or_wrong_runtime(node, field, value):
    evidence = replacement_evidence().model_dump()
    evidence[node][field] = value
    with pytest.raises(ValidationError):
        drill.ReplacementEvidence.model_validate(evidence)


@pytest.mark.parametrize("change", [
    "missing_sender", "logical_sender", "synchronous_sender", "receiver_down", "promoted_receiver",
])
def test_replacement_requires_a_physical_asynchronous_streaming_pair(change):
    primary, standby = replacement_observations()
    if change in ("missing_sender", "logical_sender", "synchronous_sender"):
        senders = primary.senders.model_dump()
        if change == "missing_sender":
            senders |= {"total": 0, "physical_streaming": 0}
        elif change == "logical_sender":
            senders |= {"physical_streaming": 0, "logical": 1}
        else:
            senders["physical_synchronous"] = 1
        primary = primary.model_copy(update={"senders": primary.senders.model_copy(update=senders)})
    elif change == "receiver_down":
        standby = standby.model_copy(update={"receiver": standby.receiver.model_copy(
            update={"streaming": False},
        )})
    else:
        primary = primary.model_copy(update={"receiver": standby.receiver.model_copy()})
    with pytest.raises(drill.pitr.DrillError):
        drill.require_replacement_pair(primary, standby)


def test_replacement_reference_preserves_probe_and_original_uncertain_state():
    baseline = post_probe_reference()
    replacement = drill.Reference.model_validate(baseline.model_dump() | {"stage": "replacement"})
    assert len(replacement.episodes) == 6
    assert len(replacement.fixture.acknowledged_ids) == 3
    assert replacement.fixture.probe_id == baseline.fixture.probe_id
    assert replacement.uncertain == baseline.uncertain
    drill.check_preserved(baseline, replacement, promoted=False)
    for version in (1, 2):
        with pytest.raises(ValidationError):
            drill.Reference.model_validate(replacement.model_dump() | {
                "format": f"pgag-ha-reference-v{version}",
            })


@pytest.mark.parametrize("change", ["missing_probe", "missing_uncertain", "duplicate_probe"])
def test_replacement_reference_cannot_drop_or_reuse_postprobe_identities(change):
    baseline = post_probe_reference()
    document = baseline.model_dump() | {"stage": "replacement"}
    if change == "missing_probe":
        document["fixture"]["probe_id"] = None
    elif change == "missing_uncertain":
        document["uncertain"] = None
    else:
        document["fixture"]["probe_id"] = document["fixture"]["uncertain_id"]
    with pytest.raises(ValidationError):
        drill.Reference.model_validate(document)


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


@pytest.mark.parametrize("count", [0, 2, 4])
def test_synchronous_evidence_requires_exactly_three_acknowledgements(count):
    evidence = passed_report().synchronous.model_dump() | {
        "acknowledgements": tuple(uuid4() for _ in range(count)),
    }
    with pytest.raises(ValidationError):
        drill.SynchronousEvidence.model_validate(evidence)


def test_synchronous_evidence_cannot_count_duplicate_acknowledgements():
    evidence = passed_report().synchronous.model_dump()
    identities = evidence["acknowledgements"]
    evidence["acknowledgements"] = (*identities[:2], identities[0])
    with pytest.raises(ValidationError):
        drill.SynchronousEvidence.model_validate(evidence)


def test_ha_uses_the_shared_production_commit_guard_without_timeout_override():
    assert drill.async_transaction is async_transaction
    assert COMMIT_ACK_TIMEOUT_SECONDS == 5.0
    source = (ROOT / "scripts" / "smoke-ha.py").read_text()
    assert "CommitDeadline(" not in source
    assert not any(
        isinstance(node, ast.Name) and node.id == "COMMIT_ACK_TIMEOUT_SECONDS"
        and isinstance(node.ctx, ast.Store)
        for node in ast.walk(ast.parse(source))
    )


@pytest.mark.parametrize("version", [1, 2])
def test_old_report_format_cannot_claim_new_evidence(version):
    with pytest.raises(ValidationError):
        drill.Report.model_validate(passed_report().model_dump() | {
            "format": f"pgag-ha-drill-v{version}",
        })


@pytest.mark.parametrize("version", [1, 2])
def test_old_reference_format_cannot_claim_reconciled_state(version):
    reference, _ = references()
    with pytest.raises(ValidationError):
        drill.Reference.model_validate(reference.model_dump() | {
            "format": f"pgag-ha-reference-v{version}",
        })


@pytest.mark.parametrize("replacement", [None, False])
def test_uncertain_evidence_cannot_drop_or_negate_its_measured_facts(replacement):
    evidence = passed_report().uncertain.model_dump()
    measured = [name for name, value in evidence.items() if value is True]
    assert measured
    for field in measured:
        with pytest.raises(ValidationError):
            drill.UncertainEvidence.model_validate(evidence | {field: replacement})


def test_uncertain_evidence_requires_every_mandatory_measurement():
    evidence = passed_report().uncertain.model_dump()
    for field, definition in drill.UncertainEvidence.model_fields.items():
        if definition.is_required():
            with pytest.raises(ValidationError):
                drill.UncertainEvidence.model_validate({
                    name: value for name, value in evidence.items() if name != field
                })
    with pytest.raises(ValidationError):
        drill.UncertainEvidence.model_validate(evidence | {"automatic_retry": True})


def test_uncertain_evidence_does_not_grant_effect_or_service_authority():
    evidence = passed_report().uncertain
    for field in (
        "retryable", "success_receipt_emitted", "local_receipt_observed_before_client_exit",
        "production_qualified",
        "network_partition_qualified", "commit_timeout_qualified", "automatic_failover",
        "automatic_service_start", "serving_authorized", "effect_reexecution",
    ):
        assert getattr(evidence, field) is False
        with pytest.raises(ValidationError):
            drill.UncertainEvidence.model_validate(evidence.model_dump() | {field: True})


@pytest.mark.parametrize("field", [
    "commit_wait_seconds", "sync_rep_observed_seconds",
])
@pytest.mark.parametrize("seconds", [-1, 0, 4.49, 8, 30, float("nan"), float("inf")])
def test_uncertain_elapsed_measurements_require_the_production_deadline_window(field, seconds):
    with pytest.raises(ValidationError):
        drill.UncertainEvidence.model_validate(
            uncertain_evidence().model_dump() | {field: seconds},
        )


@pytest.mark.parametrize("seconds", [4.5, 5.0, 7.999])
def test_uncertain_deadline_window_accepts_only_bounded_observed_wait(seconds):
    evidence = drill.UncertainEvidence.model_validate(uncertain_evidence().model_dump() | {
        "commit_wait_seconds": seconds, "sync_rep_observed_seconds": seconds,
    })
    assert evidence.commit_timeout_seconds == 5


def test_sync_rep_observation_cannot_exceed_the_commit_wait():
    with pytest.raises(ValidationError, match="invalid_sync_rep_observation_window"):
        drill.UncertainEvidence.model_validate(uncertain_evidence().model_dump() | {
            "commit_wait_seconds": 4.5, "sync_rep_observed_seconds": 5.0,
        })


@pytest.mark.parametrize("field", [
    "commit_timeout_seconds", "statement_timeout_seconds", "lock_timeout_seconds",
])
@pytest.mark.parametrize("seconds", [0, 0.05, 4, 6])
def test_uncertain_evidence_cannot_substitute_a_shortened_or_unbounded_timeout(field, seconds):
    with pytest.raises(ValidationError):
        drill.UncertainEvidence.model_validate(uncertain_evidence().model_dump() | {
            field: seconds,
        })


@pytest.mark.parametrize("change", [
    {"outcome_code": "committed"}, {"writer_synchronous_commit": "on"},
    {"local_wal_flush_scope": "commit_record_watermark"},
    {"local_wal_flush_scope": "replicated_commit"},
])
def test_uncertain_reconciliation_cannot_upgrade_outcome_or_writer_policy(change):
    with pytest.raises(ValidationError):
        drill.UncertainEvidence.model_validate(uncertain_evidence().model_dump() | change)


@pytest.mark.parametrize("proof", ["uncertain", "preserved", "probe"])
def test_report_preserves_distinct_ack_uncertain_and_probe_identities(proof):
    report = passed_report().model_dump()
    if proof == "uncertain":
        report["uncertain"]["memory_id"] = report["synchronous"]["acknowledgements"][0]
    elif proof == "preserved":
        report["preserved"]["uncertain_id"] = uuid4()
    else:
        report["probe"]["probe_id"] = report["uncertain"]["memory_id"]
    with pytest.raises(ValidationError):
        drill.Report.model_validate(report)


def test_uncertainty_cannot_be_reported_before_the_synchronous_stage():
    with pytest.raises(ValidationError, match="uncertainty_before_synchronous_check"):
        drill.Report(
            status="failed", failure_code="stage_failed", elapsed_seconds={"total": 8.0},
            uncertain=uncertain_evidence(), uncertain_commit_reconciled=True,
        )


def test_reconciled_reference_keeps_three_acks_and_one_separate_unknown():
    reference, promoted = references()
    assert reference.format == "pgag-ha-reference-v3"
    assert reference.stage == "reconciled" and len(reference.fixture.acknowledged_ids) == 3
    assert reference.fixture.uncertain_id not in reference.fixture.acknowledged_ids
    assert len(reference.episodes) == 5
    assert reference.uncertain.memory_id == reference.fixture.uncertain_id
    assert promoted.uncertain.outcome_code == "commit_outcome_unknown"


@pytest.mark.parametrize("stage", ["reconciled", "promoted", "post-probe"])
@pytest.mark.parametrize("change", [
    "missing_proof", "missing_id", "mismatched_id", "missing_episode",
])
def test_uncertain_reference_cannot_omit_or_relabel_the_reconciled_write(stage, change):
    reference, _ = references()
    document = reference.model_dump() | {"stage": stage}
    if stage == "post-probe":
        probe_id = uuid4()
        document["fixture"]["probe_id"] = probe_id
        document["episodes"] += (drill.pitr.Episode(
            object_id=probe_id, content_sha256="e" * 64,
        ).model_dump(),)
    if change == "missing_proof":
        document["uncertain"] = None
    elif change == "missing_id":
        document["fixture"]["uncertain_id"] = None
    elif change == "mismatched_id":
        document["uncertain"]["memory_id"] = uuid4()
    else:
        document["episodes"] = tuple(
            episode for episode in document["episodes"]
            if episode["object_id"] != reference.fixture.uncertain_id
        )
    with pytest.raises(ValidationError):
        drill.Reference.model_validate(document)


@pytest.mark.parametrize("collision", ["before", "acknowledged"])
def test_uncertain_reference_cannot_count_an_acknowledged_write_twice(collision):
    reference, _ = references()
    document = reference.model_dump()
    identity = reference.fixture.before_id if collision == "before" else (
        reference.fixture.acknowledged_ids[0]
    )
    document["fixture"]["uncertain_id"] = identity
    document["uncertain"]["memory_id"] = identity
    document["episodes"][-1]["object_id"] = identity
    with pytest.raises(ValidationError, match="invalid_acknowledged_episode_set"):
        drill.Reference.model_validate(document)


@pytest.mark.parametrize("stage", ["seed", "acknowledged"])
def test_pre_uncertainty_reference_cannot_claim_reconciliation(stage):
    reference, _ = references()
    document = reference.model_dump() | {"stage": stage}
    if stage == "seed":
        document["fixture"]["acknowledged_ids"] = ()
    with pytest.raises(ValidationError, match="unexpected_uncertainty"):
        drill.Reference.model_validate(document)


def test_uncertain_delta_allows_one_observe_without_changing_existing_control_state():
    before, _ = references("acknowledged")
    after = reconcile_reference(before, uncertain_evidence())
    drill.Reference.model_validate(after.model_dump())
    drill.check_uncertain_delta(before, after)


@pytest.mark.parametrize("change,code", [
    ("acknowledgements", "uncertain_identity_reused"),
    ("old_episode", "uncertain_write_changed_acknowledged_content"),
    ("extra_write", "uncertain_write_delta_mismatch"),
    ("missing_lexical", "uncertain_write_delta_mismatch"),
    ("effect", "uncertain_write_changed_unrelated_state"),
    ("access_epoch", "uncertain_write_changed_unrelated_state"),
    ("source", "uncertain_write_changed_unrelated_state"),
])
def test_uncertain_delta_rejects_retries_and_unrelated_effect_or_authority_changes(change, code):
    before, _ = references("acknowledged")
    after = reconcile_reference(before, uncertain_evidence())
    if change == "acknowledgements":
        after = after.model_copy(update={"fixture": after.fixture.model_copy(update={
            "acknowledged_ids": (*before.fixture.acknowledged_ids[:2], uuid4()),
        })})
    elif change == "old_episode":
        after = after.model_copy(update={"episodes": (
            after.episodes[0].model_copy(update={"content_sha256": "f" * 64}),
            *after.episodes[1:],
        )})
    elif change == "extra_write":
        after = after.model_copy(update={"content": tuple(
            item.model_copy(update={"rows": item.rows + 1})
            if item.table == "memory.episode" else item for item in after.content
        )})
    elif change == "missing_lexical":
        after = after.model_copy(update={"content": tuple(
            item.model_copy(update={"rows": item.rows - 1})
            if item.table == "memory.episode_lexical" else item for item in after.content
        )})
    elif change == "effect":
        after = after.model_copy(update={"content": tuple(
            item.model_copy(update={"digest": "f" * 64})
            if item.table == "memory.tool_effect" else item for item in after.content
        )})
    elif change == "access_epoch":
        after = after.model_copy(update={"processing": after.processing.model_copy(update={
            "access_epoch": after.processing.access_epoch + 1,
        })})
    else:
        after = after.model_copy(update={"source_cursor": after.source_cursor.model_copy(update={
            "sequence": 2,
        })})
    with pytest.raises(drill.pitr.DrillError, match=code):
        drill.check_uncertain_delta(before, after)


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
    ("uncertain", "original_uncertainty_changed"),
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
    elif change == "uncertain":
        promoted = promoted.model_copy(update={"uncertain": None})
    else:
        promoted = promoted.model_copy(update={"source_cursor": drill.pitr.SourceCursor(
            sequence=2, decision="deny", reason="deleted",
        )})
    with pytest.raises(drill.pitr.DrillError, match=code):
        drill.check_preserved(reference, promoted, promoted=True)


def test_reference_rejects_missing_ack_duplicate_ids_and_unknown_fields():
    reference, _ = references()
    for change in (
        {"private_key": "never-allowed"}, {"schema_version": 20}, {"schema_version": 21},
        {"effect_revision": 3},
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


@pytest.mark.parametrize("policy,privileged,timeout,lock_timeout,expected", [
    ("remote_apply", False, "5s", "5s", True),
    ("remote_apply", False, "5000ms", "5000ms", True),
    ("on", False, "5s", "5s", False),
    ("remote_apply", True, "5s", "5s", False),
    ("remote_apply", False, "0", "5s", False),
    ("remote_apply", False, "5s", "0", False),
])
def test_actual_writer_connection_policy_not_inferred_from_admin_probe(
    policy, privileged, timeout, lock_timeout, expected,
):
    class Connection:
        async def execute(self, query):
            assert "current_user" in query

            async def fetchone():
                return {
                    "policy": policy, "privileged": privileged, "timeout": timeout,
                    "lock_timeout": lock_timeout,
                }

            return SimpleNamespace(fetchone=fetchone)

    if expected:
        asyncio.run(drill.writer_policy(Connection(), "remote_apply"))
    else:
        with pytest.raises(drill.pitr.DrillError, match="writer_policy_not_verified"):
            asyncio.run(drill.writer_policy(Connection(), "remote_apply"))


@pytest.mark.parametrize("failure", [
    CommitOutcomeUnknown, RuntimeError, asyncio.CancelledError,
])
def test_service_observe_uses_guard_and_never_retries_or_releases_failed_commit(
    monkeypatch, failure,
):
    events = []
    writer, identity, memory_id = object(), object(), uuid4()

    @asynccontextmanager
    async def principal(url, subject):
        assert url == "owned-primary" and subject == drill.WRITER
        yield writer, identity

    @asynccontextmanager
    async def guard(conn):
        assert conn is writer
        events.append("guard")
        yield
        events.append("commit-failed")
        raise failure()

    async def bind(conn, subject, principal_id):
        assert (conn, subject, principal_id) == (writer, drill.WRITER, identity)

    async def policy(conn, expected):
        assert conn is writer and expected == "remote_apply"

    class Memory:
        def __init__(self, conn, principal_id):
            assert conn is writer and principal_id is identity

        async def observe(self, request, key):
            events.append("observe")
            return {"memory_id": str(memory_id)}

    monkeypatch.setattr(drill, "runtime_url", lambda url: url)
    monkeypatch.setattr(drill, "principal_connection", principal)
    monkeypatch.setattr(drill, "async_transaction", guard)
    monkeypatch.setattr(drill, "bind_identity", bind)
    monkeypatch.setattr(drill, "writer_policy", policy)
    monkeypatch.setattr(drill, "MemoryService", Memory)
    with pytest.raises(failure):
        asyncio.run(drill.observe("owned-primary", uuid4(), "single-test-observe", "remote_apply"))
    assert events == ["guard", "observe", "commit-failed"]


@pytest.fixture
def paused_uncertain_harness(monkeypatch):
    def build(mode, *, resume_error=False, cleanup_error=False):
        reference, _ = references("acknowledged")
        memory_id = uuid4()
        receipt = {"memory_id": str(memory_id)}
        state = SimpleNamespace(seconds=0.0, resumed=False, events=[])
        writer = SimpleNamespace(info=SimpleNamespace(backend_pid=123), closed=False)
        actual_sleep = asyncio.sleep
        state.original_error = asyncio.CancelledError() if mode == "cancelled" else ValueError(
            "synthetic_body_failed" if mode == "body_error" else "synthetic_observer_failed",
        )

        async def writer_execute(query):
            assert query == "SELECT pg_current_wal_insert_lsn() AS lsn"
            assert state.events[-1] == "observe"
            state.events.append("insert-lsn")

            async def fetchone():
                return {"lsn": "0/501"}

            return SimpleNamespace(fetchone=fetchone)

        writer.execute = writer_execute

        @asynccontextmanager
        async def principal(url, subject):
            assert url == "owned-primary" and subject == drill.WRITER
            yield writer, reference.fixture.writer_id

        @asynccontextmanager
        async def guard(conn):
            assert conn is writer
            state.events.append("guard")
            yield
            deadline = state.seconds + (0.2 if mode == "short_deadline" else 5.0)
            while state.seconds < deadline and not state.resumed:
                await actual_sleep(0)
            if mode == "success":
                return
            writer.closed = mode != "open_unknown"
            state.events.append("client-exit")
            if mode == "cancelled":
                raise state.original_error
            raise CommitOutcomeUnknown(local_committed=True if mode == "local_committed" else None)

        async def bind(conn, subject, identity):
            assert conn is writer and identity == reference.fixture.writer_id

        async def policy(conn, expected):
            assert conn is writer and expected == "remote_apply"
            state.events.append("policy")

        class Memory:
            def __init__(self, conn, identity):
                assert conn is writer and identity == reference.fixture.writer_id

            async def observe(self, request, key):
                assert request.scope_id == reference.fixture.scope_id
                assert request.source_event_id == "paused-uncertain"
                assert key == "ha-paused-uncertain"
                state.events.append("observe")
                if mode == "body_error":
                    raise state.original_error
                return receipt

        class Connection:
            def __init__(self, url):
                self.url = url

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, query, params=None):
                if "pg_stat_activity" in query:
                    assert self.url == "readonly:owned-primary" and params == (123,)
                    if mode == "observer_failure":
                        raise state.original_error
                    row = {"wait_event": None if mode == "no_sync_rep" else "SyncRep"}
                elif "pg_current_wal_flush_lsn" in query:
                    assert query == "SELECT pg_current_wal_flush_lsn() >= %s::pg_lsn AS flushed"
                    assert self.url == "readonly:owned-primary" and params == ("0/501",)
                    assert not state.resumed and not writer.closed
                    state.events.append("wal-flush")
                    row = {"flushed": mode != "unflushed"}
                elif "pg_get_wal_replay_pause_state" in query:
                    assert not state.resumed
                    row = {"state": "paused"}
                else:
                    assert query == "SELECT pg_wal_replay_pause()"
                    state.events.append("pause")
                    row = None

                async def fetchone():
                    return row

                return SimpleNamespace(fetchone=fetchone)

        async def connect(url, **kwargs):
            assert kwargs == {"autocommit": True, "row_factory": drill.dict_row}
            return Connection(url)

        async def sleep(seconds):
            state.seconds += seconds
            await actual_sleep(0)

        async def uncertain_state(*args):
            raise AssertionError("MVCC_receipt_is_not_visible_during_SyncRep")

        async def resume(url):
            assert url == "owned-standby"
            state.events.append(("resume", writer.closed))
            state.resumed = True
            if resume_error:
                raise OSError("private-replay-resume-diagnostic")

        async def wait(tasks, *, timeout):
            result = await asyncio.wait(tasks, timeout=timeout)
            if cleanup_error:
                raise RuntimeError("private-writer-cleanup-diagnostic")
            return result

        monkeypatch.setattr(drill, "runtime_url", lambda url: url)
        monkeypatch.setattr(drill, "read_only_url", lambda url: "readonly:" + url)
        monkeypatch.setattr(drill, "principal_connection", principal)
        monkeypatch.setattr(drill, "async_transaction", guard)
        monkeypatch.setattr(drill, "bind_identity", bind)
        monkeypatch.setattr(drill, "writer_policy", policy)
        monkeypatch.setattr(drill, "MemoryService", Memory)
        monkeypatch.setattr(drill, "psycopg", SimpleNamespace(
            AsyncConnection=SimpleNamespace(connect=connect),
        ))
        monkeypatch.setattr(drill, "time", SimpleNamespace(monotonic=lambda: state.seconds))
        monkeypatch.setattr(drill, "asyncio", SimpleNamespace(
            create_task=asyncio.create_task, sleep=sleep, wait=wait,
        ))
        monkeypatch.setattr(drill, "uncertain_state", uncertain_state)
        monkeypatch.setattr(drill, "resume_replay", resume)
        return reference.fixture, memory_id, receipt, state

    return build


def test_paused_uncertain_observes_single_guarded_unknown_before_resume(paused_uncertain_harness):
    fixture, memory_id, receipt, state = paused_uncertain_harness("unknown")
    result = asyncio.run(drill.paused_uncertain("owned-primary", "owned-standby", fixture))
    identity, elapsed, observed, captured = result
    assert identity == memory_id and captured == receipt
    assert 4.5 <= observed <= elapsed < 8
    assert state.events.count("observe") == state.events.count("guard") == 1
    assert state.events.count("policy") == 2
    assert state.events.index("pause") < state.events.index("guard")
    assert state.events.index("observe") < state.events.index("insert-lsn")
    assert state.events.index("insert-lsn") < state.events.index("wal-flush")
    assert state.events.index("wal-flush") < state.events.index("client-exit")
    assert state.events[-1] == ("resume", True)


@pytest.mark.parametrize("mode,failure,code", [
    ("success", drill.pitr.DrillError, "uncertain_commit_unexpectedly_acknowledged"),
    ("body_error", ValueError, "synthetic_body_failed"),
    ("observer_failure", ValueError, "synthetic_observer_failed"),
    ("open_unknown", drill.pitr.DrillError, "watchdog_unknown_not_verified"),
    ("local_committed", drill.pitr.DrillError, "watchdog_unknown_not_verified"),
    ("short_deadline", drill.pitr.DrillError, "uncertain_deadline_not_qualified"),
    ("no_sync_rep", drill.pitr.DrillError, "uncertain_deadline_not_qualified"),
    ("unflushed", drill.pitr.DrillError, "local_wal_not_flushed_before_exit"),
    ("cancelled", asyncio.CancelledError, None),
])
def test_paused_uncertain_failures_resume_replay_without_retry_or_success(
    paused_uncertain_harness, mode, failure, code,
):
    fixture, _, _, state = paused_uncertain_harness(mode)
    with pytest.raises(failure, match=code):
        asyncio.run(drill.paused_uncertain("owned-primary", "owned-standby", fixture))
    assert state.resumed
    assert state.events.count("observe") <= 1 and state.events.count("guard") <= 1
    assert sum(isinstance(event, tuple) and event[0] == "resume" for event in state.events) == 1


@pytest.mark.parametrize("mode", ["body_error", "observer_failure", "cancelled"])
@pytest.mark.parametrize("resume_error,cleanup_error", [(True, False), (False, True), (True, True)])
def test_paused_uncertain_cleanup_failure_preserves_the_original_error(
    paused_uncertain_harness, mode, resume_error, cleanup_error,
):
    fixture, _, _, state = paused_uncertain_harness(
        mode, resume_error=resume_error, cleanup_error=cleanup_error,
    )
    with pytest.raises(type(state.original_error)) as raised:
        asyncio.run(drill.paused_uncertain("owned-primary", "owned-standby", fixture))
    assert raised.value is state.original_error
    assert "uncertain_cleanup_failed" in raised.value.__notes__
    assert "private" not in repr(raised.value.__notes__)
    assert state.resumed and state.events.count("observe") <= 1


def test_paused_uncertain_resume_failure_cannot_return_reconciliation_evidence(
    paused_uncertain_harness,
):
    fixture, _, _, state = paused_uncertain_harness("unknown", resume_error=True)
    with pytest.raises(OSError, match="private-replay-resume-diagnostic"):
        asyncio.run(drill.paused_uncertain("owned-primary", "owned-standby", fixture))
    assert state.events[-1] == ("resume", True)


@pytest.mark.parametrize("change", [
    None, "missing", "duplicate", "wrong_writer", "wrong_scope", "wrong_operation",
])
def test_uncertain_state_is_one_read_only_exact_identity_query(change):
    reference, _ = references()
    fixture, memory_id = reference.fixture, reference.fixture.uncertain_id
    row = {
        "episodes": [{"scope_id": str(fixture.scope_id)}],
        "receipts": [{"operation": "observe", "principal_id": str(fixture.writer_id)}],
        "audits": [{"action": "observe", "principal_id": str(fixture.writer_id)}],
    }
    if change == "missing":
        row["receipts"] = None
    elif change == "duplicate":
        row["receipts"] *= 2
    elif change == "wrong_writer":
        row["audits"][0]["principal_id"] = str(fixture.reader_id)
    elif change == "wrong_scope":
        row["episodes"][0]["scope_id"] = str(uuid4())
    elif change == "wrong_operation":
        row["receipts"][0]["operation"] = "effect"
    queries = []

    class Connection:
        async def execute(self, query, params):
            queries.append(query)
            assert query.startswith("SELECT ")
            assert params == (
                fixture.tenant_id, memory_id, fixture.tenant_id, str(memory_id),
                fixture.tenant_id, memory_id,
            )

            async def fetchone():
                return row

            return SimpleNamespace(fetchone=fetchone)

    if change is None or change == "missing":
        result = asyncio.run(drill.uncertain_state(Connection(), fixture, memory_id))
        assert result == (None if change == "missing" else row)
    else:
        with pytest.raises(drill.pitr.DrillError, match=(
            "uncertain_state_not_unique" if change == "duplicate"
            else "uncertain_state_identity_mismatch"
        )):
            asyncio.run(drill.uncertain_state(Connection(), fixture, memory_id))
    assert len(queries) == 1


@pytest.mark.parametrize("mode", [
    "matched", "lagging", "primary_lagging", "primary_mismatch", "primary_changed",
    "replica_mismatch", "timeout", "cancelled",
])
def test_uncertain_stage_reconciles_read_only_without_replaying_the_write(monkeypatch, mode):
    before, _ = references("acknowledged")
    evidence = uncertain_evidence()
    receipt = {"memory_id": str(evidence.memory_id), "status": "observed"}
    local_state = {
        "synthetic": "exact-original-state", "receipts": [{"result": receipt}],
    }
    events, written = [], []
    primary_reads, standby_reads = 0, 0
    primary_url = "host=owned-primary options='-c statement_timeout=5000 -c lock_timeout=5000'"
    standby_url = "host=owned-standby options='-c statement_timeout=5000 -c lock_timeout=5000'"

    def read(directory, name, model):
        assert name == "acknowledged.json" and model is drill.Reference
        return before

    def capture(url, stage, fixture, recovery=False, uncertain=None):
        events.append(("capture", stage, recovery))
        if stage == "acknowledged":
            assert uncertain is None and fixture == before.fixture
            return before
        assert stage == "reconciled" and fixture.uncertain_id == evidence.memory_id
        return reconcile_reference(before, uncertain)

    async def pause(primary, standby, fixture):
        assert (primary, standby, fixture) == (primary_url, standby_url, before.fixture)
        events.append("single-write")
        return (
            evidence.memory_id, evidence.commit_wait_seconds,
            evidence.sync_rep_observed_seconds, receipt,
        )

    class Connection:
        def __init__(self, host):
            self.host = host

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args):
            raise AssertionError("reconciliation_must_not_issue_writes")

    async def connect(url, **kwargs):
        params = drill.conninfo_to_dict(url)
        assert params["options"] == (
            "-c statement_timeout=5000 -c lock_timeout=5000"
            " -c default_transaction_read_only=on"
        )
        assert kwargs == {"autocommit": True, "row_factory": drill.dict_row}
        events.append(("connect-read-only", params["host"]))
        return Connection(params["host"])

    async def uncertain_state(conn, fixture, memory_id):
        nonlocal primary_reads, standby_reads
        assert fixture == before.fixture and memory_id == evidence.memory_id
        events.append(("read-state", conn.host))
        if mode == "timeout":
            raise TimeoutError()
        if mode == "cancelled":
            raise asyncio.CancelledError()
        if conn.host == "owned-primary":
            primary_reads += 1
            if mode == "primary_lagging" and primary_reads == 1:
                return None
            if mode == "primary_mismatch":
                return local_state | {"receipts": [{"result": receipt | {"status": "changed"}}]}
            if mode == "primary_changed" and primary_reads > 1:
                return local_state | {"synthetic": "changed"}
            return local_state
        standby_reads += 1
        if mode in ("lagging", "primary_changed") and standby_reads == 1:
            return None
        return local_state | {"synthetic": "changed"} if mode == "replica_mismatch" else local_state

    @asynccontextmanager
    async def timeout(seconds):
        assert seconds == 30
        events.append("bounded-reconciliation")
        yield

    async def sleep(seconds):
        assert seconds == 0.05
        events.append("wait-for-replay")

    monkeypatch.setattr(drill, "read", read)
    monkeypatch.setattr(drill, "capture", capture)
    monkeypatch.setattr(drill, "wait_pair", lambda *args: events.append("physical-pair"))
    monkeypatch.setattr(drill, "paused_uncertain", pause)
    monkeypatch.setattr(drill, "uncertain_state", uncertain_state)
    monkeypatch.setattr(drill, "psycopg", SimpleNamespace(
        AsyncConnection=SimpleNamespace(connect=connect),
    ))
    monkeypatch.setattr(drill, "asyncio", SimpleNamespace(timeout=timeout, sleep=sleep))
    monkeypatch.setattr(drill, "write_new", lambda directory, name, model: written.append(
        (name, model),
    ))
    if mode in ("matched", "lagging", "primary_lagging"):
        asyncio.run(drill.uncertain(Path("/drill"), primary_url, standby_url))
        assert [name for name, _ in written] == ["reconciled.json", "uncertain.json"]
        assert written[0][1].fixture.acknowledged_ids == before.fixture.acknowledged_ids
        assert written[1][1].outcome_code == "commit_outcome_unknown"
        assert written[1][1].success_receipt_emitted is False
        assert written[1][1].local_wal_flush_observed is True
        assert written[1][1].local_wal_flush_scope == "precommit_insert_lsn_lower_bound"
        assert written[1][1].local_receipt_observed_before_client_exit is False
        assert written[1][1].local_receipt_observed is True
        assert events.count("wait-for-replay") == (0 if mode == "matched" else 1)
    else:
        expected, code = {
            "primary_mismatch": (drill.pitr.DrillError, "uncertain_local_receipt_mismatch"),
            "primary_changed": (drill.pitr.DrillError, "uncertain_primary_state_changed"),
            "replica_mismatch": (drill.pitr.DrillError, "uncertain_replica_state_mismatch"),
            "timeout": (TimeoutError, None), "cancelled": (asyncio.CancelledError, None),
        }[mode]
        with pytest.raises(expected, match=code):
            asyncio.run(drill.uncertain(Path("/drill"), primary_url, standby_url))
        assert written == []
    assert events.count("single-write") == 1
    assert events.count("bounded-reconciliation") == 1


@pytest.mark.parametrize("stage", ["prefence", "verify"])
def test_fencing_and_promotion_verification_require_the_reconciled_reference(monkeypatch, stage):
    acknowledged, _ = references("acknowledged")
    reads = []

    def read(directory, name, model):
        reads.append(name)
        return acknowledged

    monkeypatch.setattr(drill, "read", read)
    with pytest.raises(drill.pitr.DrillError, match="reconciled_reference_required"):
        if stage == "prefence":
            drill.prefence(Path("/drill"), "owned-standby")
        else:
            asyncio.run(drill.verify(Path("/drill"), "owned-standby"))
    assert reads == ["reconciled.json"]


@pytest.mark.parametrize("change", [None, "missing", "memory_id", "elapsed"])
def test_reconciled_reference_requires_the_original_separate_uncertain_proof(monkeypatch, change):
    reference, _ = references()
    proof = reference.uncertain
    if change == "memory_id":
        proof = proof.model_copy(update={"memory_id": uuid4()})
    elif change == "elapsed":
        proof = proof.model_copy(update={"commit_wait_seconds": 6.0})
    reads = []

    def read(directory, name, model):
        reads.append((name, model))
        if name == "reconciled.json":
            return reference
        assert name == "uncertain.json"
        if change == "missing":
            raise FileNotFoundError("synthetic_missing_evidence")
        return proof

    monkeypatch.setattr(drill, "read", read)
    if change is None:
        assert drill.reconciled_reference(Path("/drill")) == reference
    elif change == "missing":
        with pytest.raises(FileNotFoundError):
            drill.reconciled_reference(Path("/drill"))
    else:
        with pytest.raises(drill.pitr.DrillError, match="original_uncertainty_changed"):
            drill.reconciled_reference(Path("/drill"))
    assert reads == [
        ("reconciled.json", drill.Reference), ("uncertain.json", drill.UncertainEvidence),
    ]


@pytest.fixture
def replacement_harness(monkeypatch):
    def build(mode):
        baseline = post_probe_reference()
        proof = replacement_evidence(baseline.fixture.uncertain_id, baseline.fixture.probe_id)
        completed = passed_report()
        documents = {
            "replacement-owned.json": drill.ReplacementOwnership(
                kind="fresh_basebackup", fresh_empty_data_directory=True,
                no_old_primary_reuse=True,
            ),
            "post-probe.json": baseline,
            "uncertain.json": baseline.uncertain,
            "probe.json": completed.probe.model_copy(update={
                "probe_id": baseline.fixture.probe_id,
            }),
            "preserved.json": completed.preserved.model_copy(update={
                "uncertain_id": baseline.fixture.uncertain_id,
            }),
            "replacement-backup.json": proof.backup,
        }
        state = SimpleNamespace(
            events=[], written=[], seconds=0.0, polls=0, inspections=0, primary_captures=0,
        )
        if mode == "wrong_stage":
            documents["post-probe.json"] = references()[1]
        elif mode == "changed_uncertainty":
            documents["uncertain.json"] = uncertain_evidence(baseline.fixture.uncertain_id)
            documents["uncertain.json"] = documents["uncertain.json"].model_copy(update={
                "commit_wait_seconds": 6.0,
            })
        elif mode == "wrong_probe":
            documents["probe.json"] = documents["probe.json"].model_copy(update={
                "probe_id": uuid4(),
            })
        elif mode == "wrong_backup_timeline":
            documents["replacement-backup.json"] = proof.backup.model_copy(update={"timeline": 1})
        elif mode == "stale_backup":
            documents["replacement-backup.json"] = proof.backup.model_copy(update={
                "start_lsn": "0/400", "end_lsn": "0/450",
            })
        elif mode == "unverified_manifest":
            documents["replacement-backup.json"] = proof.backup.model_copy(update={
                "manifest_verified": False,
            })
        elif mode == "old_primary_reused":
            documents["replacement-owned.json"] = documents["replacement-owned.json"].model_copy(
                update={"no_old_primary_reuse": False},
            )

        def read(directory, name, model):
            assert directory == Path("/drill")
            state.events.append(("read", name))
            if mode == "missing_backup" and name == "replacement-backup.json":
                raise FileNotFoundError("synthetic_missing_backup")
            if mode == "missing_ownership" and name == "replacement-owned.json":
                raise FileNotFoundError("synthetic_missing_ownership")
            return model.model_validate(documents[name].model_dump())

        def inspect(path, limit):
            assert path == Path("/drill/replacement-basebackup.tar")
            assert limit == drill.pitr.MAX_BACKUP_BYTES
            state.inspections += 1
            if mode == "invalid_artifact":
                raise drill.pitr.DrillError("incomplete_basebackup")
            if mode == "changed_artifact" and state.inspections == 2:
                return proof.artifact.model_copy(update={"sha256": "f" * 64})
            return proof.artifact

        def capture(url, stage, fixture, recovery=False, uncertain=None):
            assert fixture == baseline.fixture and uncertain == baseline.uncertain
            state.events.append(("capture", stage, recovery))
            if stage == "post-probe":
                assert url == "host=owned-promoted" and not recovery
                state.primary_captures += 1
                candidate = baseline
                if mode == "primary_changed" and state.primary_captures == 2:
                    candidate = baseline.model_copy(update={"content": ()})
                return candidate
            assert url == "host=owned-replacement" and stage == "replacement" and recovery
            candidate = baseline.model_copy(update={"stage": "replacement"})
            if mode == "wrong_lineage":
                candidate = candidate.model_copy(update={
                    "processing": baseline.processing.model_copy(update={"lineage": "f" * 64}),
                })
            elif mode == "wrong_system":
                candidate = candidate.model_copy(update={"control": baseline.control.model_copy(
                    update={"system_identifier": "42"},
                )})
            elif mode == "wrong_timeline":
                candidate = candidate.model_copy(update={"control": baseline.control.model_copy(
                    update={"timeline": 1},
                )})
            elif mode == "missing_probe":
                candidate = candidate.model_copy(update={"episodes": baseline.episodes[:-1]})
            elif mode == "changed_effect":
                candidate = candidate.model_copy(update={"effect_status": "confirmed"})
            elif mode == "changed_source":
                candidate = candidate.model_copy(update={
                    "source_cursor": baseline.source_cursor.model_copy(update={"sequence": 2}),
                })
            return candidate

        class Connection:
            def __init__(self, host):
                self.host = host

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params=None):
                state.events.append(("sql", self.host, query))
                if self.host == "owned-replacement":
                    assert query.startswith("SELECT pg_is_in_recovery()")
                    assert params == (proof.wal_target_lsn, proof.wal_target_lsn)
                    state.polls += 1
                    row = {
                        "recovering": mode != "not_recovering", "read_only": (
                            "off" if mode == "writable" else "on"
                        ), "paused": mode == "paused",
                        "received": True,
                        "replayed": not (
                            mode == "timeout" or mode == "lagging" and state.polls == 1
                        ),
                    }
                elif "pg_stat_replication" in query:
                    rows = [{
                        "application_name": drill.REPLACEMENT_APPLICATION, "state": "streaming",
                        "sync_state": "async",
                    }]
                    if mode == "wrong_sender":
                        rows[0]["application_name"] = drill.APPLICATION
                    return SimpleNamespace(fetchall=lambda: rows)
                elif query.startswith("SELECT %s::pg_lsn"):
                    backup = documents["replacement-backup.json"]
                    assert params == (
                        backup.start_lsn, baseline.control.lsn, backup.end_lsn, backup.start_lsn,
                    )
                    row = {"fresh": (
                        drill.pitr.lsn_number(backup.start_lsn)
                        >= drill.pitr.lsn_number(baseline.control.lsn)
                        and drill.pitr.lsn_number(backup.end_lsn)
                        > drill.pitr.lsn_number(backup.start_lsn)
                    )}
                elif query in ("CHECKPOINT", "SELECT pg_switch_wal()"):
                    row = None
                else:
                    assert query.startswith("WITH flushed AS MATERIALIZED")
                    assert params == (proof.backup.end_lsn,)
                    row = {"lsn": proof.wal_target_lsn, "advanced": mode != "no_wal_advance"}
                return SimpleNamespace(fetchone=lambda: row)

        def connect(url, **kwargs):
            params = drill.conninfo_to_dict(url)
            assert kwargs == {"autocommit": True, "row_factory": drill.dict_row}
            assert params["host"] in ("owned-promoted", "owned-replacement")
            if params["host"] == "owned-replacement":
                assert "default_transaction_read_only=on" in params["options"]
            state.events.append(("connect", params["host"]))
            return Connection(params["host"])

        def wait_pair(primary, replacement):
            assert primary == "host=owned-promoted" and replacement == "host=owned-replacement"
            state.events.append("physical-pair")
            return replacement_observations()

        def sleep(seconds):
            assert seconds == 0.1
            state.seconds += 30

        monkeypatch.setattr(drill, "read", read)
        monkeypatch.setattr(drill.pitr, "inspect_tar", inspect)
        monkeypatch.setattr(drill, "capture", capture)
        monkeypatch.setattr(drill, "wait_replacement_pair", wait_pair)
        monkeypatch.setattr(drill, "psycopg", SimpleNamespace(connect=connect))
        monkeypatch.setattr(drill, "time", SimpleNamespace(
            monotonic=lambda: state.seconds, sleep=sleep,
        ))
        monkeypatch.setattr(drill, "write_new", lambda directory, name, model: state.written.append(
            (name, model),
        ))
        return baseline, state

    return build


@pytest.mark.parametrize("mode", ["matched", "lagging"])
def test_replacement_uses_fresh_backup_then_wal_and_readonly_exact_postprobe_state(
    replacement_harness, mode,
):
    baseline, state = replacement_harness(mode)
    drill.replacement(Path("/drill"), "host=owned-promoted", "host=owned-replacement")
    assert [name for name, _ in state.written] == ["replacement-reference.json", "replacement.json"]
    candidate, evidence = (model for _, model in state.written)
    assert candidate.uncertain == baseline.uncertain
    assert candidate.fixture == baseline.fixture and candidate.episodes == baseline.episodes
    assert evidence.original_outcome_code == "commit_outcome_unknown"
    assert evidence.writer_synchronous_commit == "on" and not evidence.synchronous_policy_renewed
    assert not evidence.serving_authorized and not evidence.effect_reexecution
    assert state.inspections == 2 and state.primary_captures == 2
    assert state.polls == (2 if mode == "lagging" else 1)
    commands = [
        event[2] for event in state.events if isinstance(event, tuple) and event[0] == "sql"
    ]
    assert commands.count("CHECKPOINT") == commands.count("SELECT pg_switch_wal()") == 1
    assert commands.index("CHECKPOINT") < commands.index("SELECT pg_switch_wal()")
    assert all(command.startswith(("SELECT ", "WITH ")) or command == "CHECKPOINT"
               for command in commands)


@pytest.mark.parametrize("mode,error,code", [
    ("missing_ownership", FileNotFoundError, "synthetic_missing_ownership"),
    ("missing_backup", FileNotFoundError, "synthetic_missing_backup"),
    ("old_primary_reused", ValidationError, "no_old_primary_reuse"),
    ("unverified_manifest", ValidationError, "manifest_verified"),
    ("wrong_stage", drill.pitr.DrillError, "post_probe_reference_required"),
    ("changed_uncertainty", drill.pitr.DrillError, "original_uncertainty_changed"),
    ("wrong_probe", drill.pitr.DrillError, "replacement_baseline_identity_mismatch"),
    ("wrong_backup_timeline", drill.pitr.DrillError, "replacement_backup_timeline_mismatch"),
    ("stale_backup", drill.pitr.DrillError, "replacement_backup_not_fresh"),
    ("invalid_artifact", drill.pitr.DrillError, "incomplete_basebackup"),
    ("changed_artifact", drill.pitr.DrillError, "replacement_backup_artifact_changed"),
    ("wrong_sender", drill.pitr.DrillError, "owned_replacement_sender_required"),
    ("no_wal_advance", drill.pitr.DrillError, "replacement_wal_not_advanced"),
    ("not_recovering", drill.pitr.DrillError, "replacement_read_only_recovery_required"),
    ("writable", drill.pitr.DrillError, "replacement_read_only_recovery_required"),
    ("paused", drill.pitr.DrillError, "replacement_read_only_recovery_required"),
    ("timeout", drill.pitr.DrillError, "replacement_wal_replay_timeout"),
    ("wrong_lineage", drill.AdminError, "processing_recovery_lineage_mismatch"),
    ("wrong_system", drill.pitr.DrillError, "physical_cluster_identity_mismatch"),
    ("wrong_timeline", drill.pitr.DrillError, "timeline_mismatch"),
    ("missing_probe", drill.pitr.DrillError, "acknowledged_content_mismatch"),
    ("primary_changed", drill.pitr.DrillError, "acknowledged_content_mismatch"),
    ("changed_effect", drill.pitr.DrillError, "source_or_effect_state_mismatch"),
    ("changed_source", drill.pitr.DrillError, "source_or_effect_state_mismatch"),
])
def test_replacement_rejects_missing_stale_or_mutated_proof_without_success(
    replacement_harness, mode, error, code,
):
    _, state = replacement_harness(mode)
    with pytest.raises(error, match=code):
        drill.replacement(Path("/drill"), "host=owned-promoted", "host=owned-replacement")
    assert state.written == []
    assert sum(event == ("sql", "owned-promoted", "SELECT pg_switch_wal()")
               for event in state.events) <= 1


@pytest.mark.parametrize("primary,replacement", [
    (None, "host=owned-replacement"), ("host=owned-promoted", None),
    ("host=same-owned-node", "host=same-owned-node"),
])
def test_replacement_requires_a_separate_new_node_before_reading_evidence(
    monkeypatch, primary, replacement,
):
    def forbidden_read(*args):
        raise AssertionError("unowned_replacement_must_not_read_evidence")

    monkeypatch.setattr(drill, "read", forbidden_read)
    with pytest.raises(drill.pitr.DrillError, match=(
        "distinct_owned_database_required" if primary == replacement
        else "owned_replacement_required"
    )):
        drill.replacement(Path("/drill"), primary, replacement)


@pytest.mark.parametrize("becomes_ready", [False, True])
def test_replacement_pair_wait_is_bounded_and_requires_a_real_stream(becomes_ready, monkeypatch):
    primary, standby = replacement_observations()
    state = SimpleNamespace(seconds=0.0, polls=0)

    def replication_status(url):
        if url == "owned-replacement":
            return standby
        assert url == "owned-promoted"
        state.polls += 1
        return primary if becomes_ready and state.polls > 1 else observation("promoted")

    def sleep(seconds):
        assert seconds == 0.1
        state.seconds += 30

    monkeypatch.setattr(drill, "replication_status", replication_status)
    monkeypatch.setattr(drill, "time", SimpleNamespace(
        monotonic=lambda: state.seconds, sleep=sleep,
    ))
    if becomes_ready:
        assert drill.wait_replacement_pair("owned-promoted", "owned-replacement") == (
            primary, standby,
        )
        assert state.polls == 2
    else:
        with pytest.raises(drill.pitr.DrillError, match="replacement_streaming_pair_unavailable"):
            drill.wait_replacement_pair("owned-promoted", "owned-replacement")
        assert state.seconds == 60 and state.polls == 3


@pytest.mark.parametrize("already_exists", [False, True])
def test_replacement_config_is_private_create_only_and_targets_the_promoted_node(
    monkeypatch, already_exists,
):
    written = []
    original_os = drill.os

    def open_file(path, flags, mode):
        assert path == Path("/drill/.replacement.conf")
        assert flags == (
            original_os.O_WRONLY | original_os.O_CREAT | original_os.O_EXCL | original_os.O_NOFOLLOW
        )
        assert mode == 0o600
        if already_exists:
            raise FileExistsError("synthetic_existing_replacement_config")
        return 123

    @contextmanager
    def fdopen(fd, mode):
        assert fd == 123 and mode == "w"
        yield SimpleNamespace(write=written.append)

    monkeypatch.setattr(drill, "secret", lambda name: "a" * 64)
    monkeypatch.setattr(drill, "os", SimpleNamespace(
        open=open_file, fdopen=fdopen,
        **{name: getattr(original_os, name)
           for name in ("O_WRONLY", "O_CREAT", "O_EXCL", "O_NOFOLLOW")},
    ))
    if already_exists:
        with pytest.raises(FileExistsError):
            drill.write_replication_config(
                Path("/drill"), ".replacement.conf", "host=owned-promoted",
                drill.REPLACEMENT_APPLICATION,
            )
        assert written == []
    else:
        drill.write_replication_config(
            Path("/drill"), ".replacement.conf", "host=owned-promoted",
            drill.REPLACEMENT_APPLICATION,
        )
        assert len(written) == 1
        params = drill.conninfo_to_dict(
            written[0].removeprefix("primary_conninfo = '").removesuffix("'\n"),
        )
        assert params["host"] == "owned-promoted"
        assert params["user"] == "pgag_ha_replication"
        assert params["application_name"] == drill.REPLACEMENT_APPLICATION
        assert params["connect_timeout"] == "5"


def guard_script():
    text = RUNNER.read_text()
    return "owned_promote() {" + text.split("owned_promote() {", 1)[1].split(
        "\ncontainer_host() {", 1,
    )[0]


@pytest.mark.parametrize("overrides,expected,promote_calls", [
    ("allow_owned_promotion=false", 41, 0),
    ("source_destroyed=null; fencing_verified=null", 42, 0),
    ("source_destroyed=null; fencing_verified=null; uncertain_commit_reconciled=true", 42, 0),
    ("source_destroyed=null; fencing_verified=null; replacement_state_matches=true", 42, 0),
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


def test_main_dispatches_uncertain_phase_with_only_owned_pair(monkeypatch, capsys):
    events = []

    async def uncertain(directory, primary, standby):
        events.append((directory, primary, standby))

    monkeypatch.setattr(
        drill, "owned_environment",
        lambda: (Path("/drill"), "owned-primary", "owned-standby", None),
    )
    monkeypatch.setattr(drill, "uncertain", uncertain)
    assert drill.main(["uncertain"]) == 0
    assert events == [(Path("/drill"), "owned-primary", "owned-standby")]
    output = capsys.readouterr()
    assert output.out == "" and output.err == ""


def test_uncertain_stage_error_is_redacted_and_cannot_write_success(monkeypatch, capsys):
    records = []

    async def uncertain(*args):
        raise RuntimeError("postgresql://private-password@external-host/database")

    monkeypatch.setattr(
        drill, "owned_environment",
        lambda: (Path("/drill"), "owned-primary", "owned-standby", None),
    )
    monkeypatch.setattr(drill, "uncertain", uncertain)
    monkeypatch.setattr(drill, "write_new", lambda *args: records.append(args))
    assert drill.main(["uncertain"]) == 1
    assert len(records) == 1 and records[0][1] == "failure.json"
    output = capsys.readouterr()
    assert output.out == "" and output.err == "ha_stage_failed\n"
    assert "private-password" not in repr(records)


@pytest.mark.parametrize("failed", [False, True])
def test_main_routes_replacement_from_promoted_node_without_reusing_destroyed_primary(
    monkeypatch, capsys, failed,
):
    events, written = [], []

    def replacement(directory, promoted, fresh):
        events.append((directory, promoted, fresh))
        if failed:
            raise RuntimeError("postgresql://private-password@external-host/database")

    monkeypatch.setattr(
        drill, "owned_environment",
        lambda: (
            Path("/drill"), "forbidden-destroyed-primary", "owned-promoted", "owned-replacement",
        ),
    )
    monkeypatch.setattr(drill, "replacement", replacement)
    monkeypatch.setattr(drill, "write_new", lambda *args: written.append(args))
    assert drill.main(["replacement"]) == int(failed)
    assert events == [(Path("/drill"), "owned-promoted", "owned-replacement")]
    assert [record[1] for record in written] == (["failure.json"] if failed else [])
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ("ha_stage_failed\n" if failed else "")
    assert "private-password" not in repr(written)


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


@pytest.fixture
def replacement_environment(monkeypatch):
    run = "pgag-ha-1-2-1234567890abcdef"
    for name in ("PGAG_DATABASE_URL", "PGAG_ADMIN_DATABASE_URL", "PGHOST", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(drill.platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_mode=0o40700))
    monkeypatch.setattr(drill, "secret", lambda name: "a" * 64)
    monkeypatch.setenv("PGAG_HA_OWNED_RUN", run)
    monkeypatch.setenv("PGAG_HA_ALLOW_OWNED_PROMOTION", "1")

    def configure(engine, host):
        monkeypatch.setenv("PGAG_HA_ENGINE", engine)
        monkeypatch.setenv("PGAG_HA_PRIMARY_HOST", run + "-primary" if engine == "docker"
                           else "10.0.0.2")
        monkeypatch.setenv("PGAG_HA_STANDBY_HOST", run + "-standby" if engine == "docker"
                           else "10.0.0.3")
        monkeypatch.setenv("PGAG_HA_REPLACEMENT_HOST", host)

    return run, configure


@pytest.mark.parametrize("engine,host", [
    ("docker", "unowned-replacement"),
    ("docker", "pgag-ha-1-2-1234567890abcdef-primary"),
    ("docker", "pgag-ha-1-2-1234567890abcdef-standby"),
    ("docker", "pgag-ha-1-2-0000000000000000-replacement"),
    ("container", "8.8.8.8"), ("container", "127.0.0.1"),
    ("container", "169.254.1.2"), ("container", "0.0.0.0"),
    ("container", "224.0.0.1"), ("container", "::1"),
])
def test_replacement_host_cannot_target_the_old_primary_or_an_external_host(
    replacement_environment, engine, host,
):
    _, configure = replacement_environment
    configure(engine, host)
    with pytest.raises(drill.pitr.DrillError, match="owned_database_required"):
        drill.owned_environment()


@pytest.mark.parametrize("engine", ["docker", "container"])
def test_replacement_environment_returns_a_separately_owned_database_url(
    replacement_environment, engine,
):
    run, configure = replacement_environment
    host = run + "-replacement" if engine == "docker" else "10.0.0.4"
    configure(engine, host)
    directory, primary, promoted, replacement = drill.owned_environment()
    assert directory == Path("/drill")
    assert len({primary, promoted, replacement}) == 3
    params = drill.conninfo_to_dict(replacement)
    assert params["host"] == host and params["connect_timeout"] == "5"
    assert params["options"] == "-c statement_timeout=5000 -c lock_timeout=5000"


@pytest.mark.parametrize("host", ["10.0.0.2", "10.0.0.3"])
def test_private_ip_replacement_cannot_reuse_either_existing_database(
    replacement_environment, host,
):
    _, configure = replacement_environment
    configure("container", host)
    with pytest.raises(drill.pitr.DrillError, match="distinct_owned_database_required"):
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
containers=(owned-primary owned-standby owned-verify owned-replacement)
source_destroyed=true
fencing_verified=true
pre_fence_promotion_rejected=true
promotion_executed=true
backup_verified=true
replacement_backup_verified=true
original_primary_remains_fenced=true
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
    expected = [
        "container:owned-standby", "container:owned-verify", "container:owned-replacement",
    ]
    if image_owned == "true":
        expected.append("engine:image rm explicit-owned-image")
    expected += [
        "rm:-f", "rm:--", "rm:nonexistent-ha-contract-output/.credentials.env",
        "rm:nonexistent-ha-contract-output/.standby.conf",
        "rm:nonexistent-ha-contract-output/.replacement.conf",
    ]
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize("missing", [
    None, "uncertain", "synchronous", "preserved", "probe", "artifact", "replacement_evidence",
    "replacement_backup_verified", "original_primary_remains_fenced",
])
@pytest.mark.parametrize("initial_status", [0, 1])
def test_terminal_report_gate_requires_uncertain_evidence_and_preserves_failure(
    missing, initial_status,
):
    script = RUNNER.read_text()
    gate = 'if [[ "$source_destroyed" != true' + script.split(
        'if [[ "$source_destroyed" != true', 1,
    )[1].split("    record_elapsed cleanup", 1)[0]
    setup = f"""
status={initial_status}
source_destroyed=true
fencing_verified=true
pre_fence_promotion_rejected=true
promotion_executed=true
backup_verified=true
replacement_backup_verified=true
original_primary_remains_fenced=true
synchronous='{{}}'
uncertain='{{}}'
preserved='{{}}'
probe='{{}}'
artifact='{{}}'
replacement_evidence='{{}}'
"""
    if missing is not None:
        setup += f"{missing}=null\n"
    result = subprocess.run(
        ["bash", "-c", setup + gate + '\nprintf "%s\\n" "$status"'],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.strip() == str(1 if missing is not None else initial_status)


def test_terminal_report_merges_uncertain_proof_without_promoting_qualification():
    script = RUNNER.read_text()
    cleanup = script.split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    assert 'if [[ -f "$directory/uncertain.json" ]]' in cleanup
    assert '"$directory/uncertain.json")" || status=1' in cleanup
    assert '--argjson uncertain "$uncertain"' in cleanup
    assert 'format: "pgag-ha-drill-v3"' in cleanup
    assert "uncertain_commit_reconciled: $uncertain.uncertain_commit_reconciled" in cleanup
    assert "uncertain: $uncertain" in cleanup
    assert 'if [[ -f "$directory/replacement.json" ]]' in cleanup
    assert '"$directory/replacement.json")" || status=1' in cleanup
    assert '--argjson replacement "$replacement_evidence"' in cleanup
    assert "replacement_state_matches: $replacement.replacement_state_matches" in cleanup
    assert "replacement: $replacement" in cleanup
    for flag in (
        "production_qualified", "host_failure_domain_independent", "network_partition_qualified",
        "commit_timeout_qualified", "automatic_failover", "automatic_service_start",
        "serving_authorized", "effect_reexecution",
    ):
        assert f"{flag}: false" in cleanup


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

    monkeypatch.setattr(drill, "owned_environment", lambda: (Path("/drill"), "owned", None, None))
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
        "phase uncertain\n", "if owned_promote; then", "phase prefence\n",
        'remove_owned_container "$primary"\n',
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


def test_runner_rebuilds_only_after_probe_from_promoted_node_then_rechecks_original_fence():
    script = RUNNER.read_text()
    replacement = script.split("phase verify\n", 1)[1]
    markers = [
        "failure_code=replacement_basebackup_failed\n",
        'verify_primary_absent\n', '"$engine" exec "$standby" bash -ceu',
        "gosu postgres pg_basebackup", "--manifest-checksums=SHA256",
        "gosu postgres pg_verifybackup", "replacement_backup_verified=true\n",
        '"$directory/replacement-backup.json"', '"$directory/replacement-basebackup.tar"',
        'containers+=("$replacement")', 'run -d --name "$replacement"',
        'contents="$(ls -A "$PGDATA")"', 'test -z "$contents"',
        "-xf /drill/replacement-basebackup.tar", 'gosu postgres pg_verifybackup "$PGDATA"',
        'cat /drill/.replacement.conf >> "$PGDATA/postgresql.auto.conf"',
        'wait_database "$replacement"', '"$directory/replacement-owned.json"',
        "phase replacement\n",
        "failure_code=replacement_fence_recheck_failed\n", "original_primary_remains_fenced=true\n",
    ]
    positions = [replacement.index(marker) for marker in markers]
    assert positions == sorted(positions)
    final = replacement.split("phase replacement\n", 1)[1]
    assert final.index("verify_primary_absent\n") < final.index(
        "original_primary_remains_fenced=true",
    )
    assert '"$engine" exec "$primary"' not in replacement
    assert 'run -d --name "$primary"' not in replacement
    assert "pg_rewind" not in replacement and "/drill/basebackup.tar" not in replacement
    assert '-e "PGAG_HA_REPLACEMENT_HOST=$replacement_host"' in script
    assert 'replacement="${run_id}-replacement"' in script
    assert '"$directory/.replacement.conf"' in script.split("cleanup() {", 1)[1]


@pytest.mark.parametrize("stage,engine_status", [
    ("replacement", 0), ("verify", 0), ("replacement", 7),
])
def test_phase_helper_never_collides_with_replacement_database_and_propagates_failure(
    stage, engine_status,
):
    script = RUNNER.read_text()
    phase = "phase() {" + script.split("phase() {", 1)[1].split("\nwait_database() {", 1)[0]
    result = subprocess.run(
        ["bash", "-c", f"""
set -e
exec 3>&1
run_id=pgag-ha-1-2-1234567890abcdef
replacement=$run_id-replacement
containers=("$replacement")
engine=fake_engine
network=owned-network
directory=synthetic-output
image=owned-image
primary_host=
standby_host=$run_id-standby
replacement_host=$replacement
fake_engine() {{
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == --name ]]; then
            printf 'helper:%s\\n' "$2" >&3
            return {engine_status}
        fi
        shift
    done
    return 1
}}
record_elapsed() {{ printf 'timed:%s\\n' "$1"; }}
{phase}
phase {stage}
printf 'owned:%s\\n' "${{containers[@]}}"
"""],
        capture_output=True, text=True, check=False,
    )
    helper = "pgag-ha-1-2-1234567890abcdef-" + (
        "replacement-check" if stage == "replacement" else stage
    )
    assert result.returncode == engine_status and result.stderr == ""
    expected = [f"helper:{helper}"]
    if engine_status == 0:
        expected += [
            f"timed:{stage}", "owned:pgag-ha-1-2-1234567890abcdef-replacement", f"owned:{helper}",
        ]
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize("contents", ["", "PG_VERSION", ".hidden-prior-data"])
def test_replacement_start_checks_empty_directory_before_extracting_backup(contents):
    script = RUNNER.read_text().split("failure_code=replacement_start_failed\n", 1)[1]
    guard = 'contents="$(ls -A "$PGDATA")"' + script.split(
        'contents="$(ls -A "$PGDATA")"', 1,
    )[1].split('        chown -R postgres:postgres "$PGDATA"', 1)[0]
    result = subprocess.run(
        ["bash", "-c", f"""
set -e
PGDATA=synthetic-owned-directory
ls() {{ printf '%s' '{contents}'; }}
timeout() {{
    [[ "$1 $2 $3 $4" == '90s tar --no-same-owner -xf' ]]
    [[ "$5 $6 $7" == '/drill/replacement-basebackup.tar -C synthetic-owned-directory' ]]
    printf 'extracted\\n'
}}
{guard}
"""],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == (1 if contents else 0)
    assert result.stdout == ("" if contents else "extracted\n") and result.stderr == ""
