"""Explicit operator policy, real read-only snapshots, and private atomic publication."""

import json
import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError
from test_operations_status import add_job, canonical_state
from test_processing import configure
from test_processing import profile as profile

from pg_agmemory import __version__, monitoring
from pg_agmemory.admin import AdminError
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.operations_status import (
    CallStatus,
    DeletionStatus,
    GraphStatus,
    JobStatus,
    OperationsStatus,
    OperationsStatusRequest,
    SourceStatus,
)
from pg_agmemory.replication_status import (
    ReceiverStatus,
    ReplicationStatus,
    SenderStatus,
    SlotStatus,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")
INSTANT = datetime(2026, 9, 24, 12, tzinfo=UTC)
PRIVATE = "PRIVATE_MONITORING_PAYLOAD"


@pytest.fixture
def workspace():
    directory = Path(".review-artifacts") / f"monitoring-tests-{uuid4().hex}"
    directory.mkdir(parents=True, mode=0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


def rule(code="jobs_due_pending", **changes):
    return monitoring.AlertRule(
        **{"code": code, "operator": "gt", "threshold": 0, "severity": "warning", **changes},
    )


def policy(*rules):
    return monitoring.MonitoringPolicy(
        format="pgag-monitoring-policy-v1", rules=rules,
    )


def policy_file(workspace, value=None):
    path = workspace / "policy.json"
    path.write_text((value or policy()).model_dump_json())
    path.chmod(0o600)
    return path


def snapshots(**changes):
    operations = OperationsStatus(
        evaluated_at=INSTANT, tenant_id=TENANT, access_epoch=1, deletion_epoch=1,
        in_recovery=False, primary_snapshot=True,
        jobs=JobStatus(
            total=2, pending=2, running=0, succeeded=0, failed=0, cancelled=0,
            due_pending=2, expired_running=0, stale_active=0, oldest_due_age_seconds=2.5,
        ),
        calls=CallStatus(
            total=0, unknown=0, succeeded=0, failed=0, billing_unknown=0,
            reserved_input_bytes=0, max_output_tokens_reserved=0,
        ),
        source=SourceStatus(
            total=0, allowed=0, denied=0, deleted=0, expired_allow=0,
            active_read_leases=0, unexpected_grants=0,
        ),
        deletions=DeletionStatus(
            total=0, active_store_purged=0, blocked_for_reads=0, legacy_manifests=0,
            target_rows=0,
        ),
        graph=GraphStatus(registered=False, enabled=False, epoch_schema_match=None),
        warnings=(),
    )
    replication = ReplicationStatus(
        evaluated_at=INSTANT + timedelta(seconds=1),
        in_recovery=False, transaction_read_only=True,
        synchronous_commit="on", synchronous_standby_configured=False,
        primary_flush_lsn="0/16B6A80", received_lsn=None, replayed_lsn=None,
        replay_paused=None,
        senders=SenderStatus(total=0, physical_streaming=0, physical_synchronous=0, logical=0),
        receiver=ReceiverStatus(present=False, streaming=False),
        slots=SlotStatus(total=0, physical=0, logical=0, inactive=0),
        warnings=("session_commit_not_remote_apply",),
    )
    return changes.get("operations", operations), changes.get("replication", replication)


def install_snapshots(monkeypatch, **changes):
    operations, replication = snapshots(**changes)
    calls = []

    def read_operations(url, request):
        calls.append(("operations", url, request))
        return operations

    def read_replication(url):
        calls.append(("replication", url))
        return replication

    monkeypatch.setattr(monitoring, "operations_status", read_operations)
    monkeypatch.setattr(monitoring, "replication_status", read_replication)
    return calls


def collected(monkeypatch, selected=None, **changes):
    install_snapshots(monkeypatch, **changes)
    return monitoring.collect(
        PRIVATE, OperationsStatusRequest(tenant_id=TENANT), selected or policy(),
    )


def sample(text, name, labels=None):
    expected = {"tenant_id": str(TENANT), **(labels or {})}
    label_text = ",".join(f'{key}="{value}"' for key, value in expected.items())
    prefix = f"{name}{{{label_text}}} "
    return [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]


def test_policy_has_no_default_threshold_severity_operator_or_rules():
    for missing in ("threshold", "severity", "operator", "code"):
        values = rule().model_dump()
        del values[missing]
        with pytest.raises(ValidationError):
            monitoring.AlertRule(**values)
    with pytest.raises(ValidationError):
        monitoring.MonitoringPolicy(format="pgag-monitoring-policy-v1")
    with pytest.raises(ValidationError):
        monitoring.MonitoringPolicy(rules=())
    assert policy().rules == ()


@pytest.mark.parametrize("changes", [
    {"threshold": True}, {"threshold": "1"}, {"threshold": -1},
    {"threshold": float("nan")}, {"threshold": float("inf")},
    {"threshold": float("-inf")}, {"threshold": None},
    {"threshold": 10 ** 400},
    {"operator": "gte"}, {"operator": PRIVATE}, {"severity": "fatal"},
    {"code": PRIVATE}, {"code": "source_subject"}, {"extra": PRIVATE},
])
def test_policy_rejects_coercion_unknown_vocabulary_and_nonfinite_thresholds(changes):
    with pytest.raises(ValidationError):
        rule(**changes)


def test_policy_is_frozen_bounded_unique_and_canonical():
    first = rule("jobs_due_pending")
    second = rule("calls_unknown", severity="critical", threshold=2.5)
    selected = policy(first, second)
    assert selected.rules == (second, first)
    assert policy(second, first) == selected
    assert monitoring.MonitoringPolicy.model_validate_json(
        selected.model_dump_json(),
    ) == selected
    with pytest.raises(ValidationError):
        policy(first, first)
    with pytest.raises(ValidationError):
        policy(*([first] * 18))
    with pytest.raises(ValidationError):
        first.threshold = 3
    with pytest.raises(ValidationError):
        monitoring.MonitoringPolicy.model_validate_json(
            '{"format":"pgag-monitoring-policy-v2","rules":[]}',
        )


@pytest.mark.parametrize("request_value,selected", [
    (None, policy()),
    ({"tenant_id": str(TENANT)}, policy()),
    (OperationsStatusRequest.model_construct(tenant_id=PRIVATE), policy()),
    (OperationsStatusRequest(tenant_id=TENANT), None),
    (OperationsStatusRequest(tenant_id=TENANT),
     monitoring.MonitoringPolicy.model_construct(rules=())),
    (OperationsStatusRequest(tenant_id=TENANT),
     policy(rule()).model_copy(update={"rules": (rule().model_copy(
         update={"threshold": True},
     ),)})),
])
def test_invalid_request_or_forged_policy_fails_before_database(
    monkeypatch, request_value, selected,
):
    def forbidden(*args):
        pytest.fail("Invalid monitoring request reached database")

    monkeypatch.setattr(monitoring, "operations_status", forbidden)
    monkeypatch.setattr(monitoring, "replication_status", forbidden)
    with pytest.raises(AdminError, match="^invalid_monitoring_request$"):
        monitoring.collect(PRIVATE, request_value, selected)


@pytest.mark.parametrize("operator,threshold,state", [
    ("gt", 1, "firing"), ("gt", 2, "inactive"), ("gt", 3, "inactive"),
    ("ge", 1, "firing"), ("ge", 2, "firing"), ("ge", 3, "inactive"),
    ("lt", 1, "inactive"), ("lt", 2, "inactive"), ("lt", 3, "firing"),
    ("le", 1, "inactive"), ("le", 2, "firing"), ("le", 3, "firing"),
    ("eq", 1, "inactive"), ("eq", 2, "firing"), ("eq", 3, "inactive"),
])
def test_every_comparison_has_exact_boundary_semantics(monkeypatch, operator, threshold, state):
    selected = policy(rule(operator=operator, threshold=threshold))
    report = collected(monkeypatch, selected)
    assert report.alerts[0].model_dump() == {
        "code": "jobs_due_pending", "operator": operator, "threshold": threshold,
        "severity": "warning", "observed": 2, "state": state,
    }
    text = monitoring.render_prometheus(report)
    labels = {"code": "jobs_due_pending", "severity": "warning", "operator": operator}
    assert sample(text, "pgag_alert_known", labels) == ["1"]
    assert sample(text, "pgag_alert_firing", labels) == [str(int(state == "firing"))]
    assert sample(text, "pgag_alert_threshold", labels) == [str(threshold)]


def test_exact_float_threshold_and_unknowns_are_not_inactive_or_zero(monkeypatch):
    selected = policy(
        rule("jobs_oldest_due_age_seconds", operator="ge", threshold=2.5),
        rule("graph_epoch_schema_match", operator="eq", threshold=0, severity="critical"),
        rule("replication_replay_paused", threshold=0),
    )
    report = collected(monkeypatch, selected)
    assert [(alert.code, alert.observed, alert.state) for alert in report.alerts] == [
        ("graph_epoch_schema_match", None, "unknown"),
        ("jobs_oldest_due_age_seconds", 2.5, "firing"),
        ("replication_replay_paused", None, "unknown"),
    ]
    text = monitoring.render_prometheus(report)
    assert sample(text, "pgag_jobs_oldest_due_age_seconds") == ["2.5"]
    assert sample(text, "pgag_graph_epoch_schema_match") == ["NaN"]
    assert sample(text, "pgag_replication_replay_paused") == ["NaN"]
    labels = {"code": "graph_epoch_schema_match", "severity": "critical", "operator": "eq"}
    assert sample(text, "pgag_alert_firing", labels) == ["NaN"]
    assert sample(text, "pgag_alert_known", labels) == ["0"]
    dumped = json.loads(report.model_dump_json())
    assert dumped["alerts"][0]["observed"] is None
    assert "NaN" not in report.model_dump_json()


def test_empty_queue_age_remains_unknown(monkeypatch):
    operations, _ = snapshots()
    operations = operations.model_copy(update={"jobs": operations.jobs.model_copy(update={
        "total": 0, "pending": 0, "due_pending": 0, "oldest_due_age_seconds": None,
    })})
    report = collected(
        monkeypatch, policy(rule("jobs_oldest_due_age_seconds")), operations=operations,
    )
    assert report.alerts[0].state == "unknown"
    assert sample(monitoring.render_prometheus(report), "pgag_jobs_oldest_due_age_seconds") == [
        "NaN",
    ]


def test_prometheus_exact_header_types_and_safe_metadata(monkeypatch):
    report = collected(monkeypatch)
    report = report.model_copy(update={"evaluated_at": INSTANT})
    text = monitoring.render_prometheus(report)
    tenant = f'{{tenant_id="{TENANT}"}}'
    expected_header = (
        "# pgag-monitoring-export-v1; one-shot observations, not authority or retained history.\n"
        "# Unknown or inapplicable observations are NaN, never zero.\n"
        "# TYPE pgag_collection_success gauge\n"
        f"pgag_collection_success{tenant} 1\n"
        "# TYPE pgag_collection_timestamp_seconds gauge\n"
        f"pgag_collection_timestamp_seconds{tenant} {INSTANT.timestamp()}\n"
        "# TYPE pgag_snapshots_atomic gauge\n"
        f"pgag_snapshots_atomic{tenant} 0\n"
        "# TYPE pgag_historical_retention_verified gauge\n"
        f"pgag_historical_retention_verified{tenant} 0\n"
        "# TYPE pgag_production_qualified gauge\n"
        f"pgag_production_qualified{tenant} 0\n"
        "# TYPE pgag_export_info gauge\n"
        f'pgag_export_info{{tenant_id="{TENANT}",service_version="{__version__}",'
        f'schema_version="{SCHEMA_VERSION}"}} 1\n'
    )
    assert text.startswith(expected_header)
    assert text.endswith("\n") and "\n\n" not in text
    assert sample(text, "pgag_operations_snapshot_timestamp_seconds") == [
        str(INSTANT.timestamp()),
    ]
    assert sample(text, "pgag_replication_observation_timestamp_seconds") == [
        str((INSTANT + timedelta(seconds=1)).timestamp()),
    ]
    assert "# TYPE pgag_jobs_total gauge\n" in text
    assert " counter\n" not in text
    assert "pgag_alert_" not in text
    assert PRIVATE not in text
    assert "0/16B6A80" not in text
    assert "False" not in text and "True" not in text
    assert sample(text, "pgag_graph_registered") == ["0"]
    assert sample(text, "pgag_graph_serving_verified") == ["0"]
    assert sample(text, "pgag_deletions_backup_retention_verified") == ["0"]
    metric_lines = [line for line in text.splitlines() if not line.startswith("#")]
    names = [line.split("{", 1)[0] for line in metric_lines]
    assert len(names) == len(set(names)) < 100
    for name in names:
        assert text.count(f"# TYPE {name} gauge\n") == 1


def test_label_escaping_and_number_format_are_exact_and_noncoercing():
    assert monitoring._label('line\n"quote"\\end') == 'line\\n\\"quote\\"\\\\end'
    assert monitoring._sample("pgag_test", 3, {"code": 'x\n"\\'}) == (
        'pgag_test{code="x\\n\\"\\\\"} 3'
    )
    assert monitoring._number(9223372036854775807) == "9223372036854775807"
    assert monitoring._number(0) == "0"
    assert monitoring._number(0.25) == "0.25"
    assert monitoring._number(None) == "NaN"
    for value in (True, False, "0", float("nan"), float("inf"), 10 ** 400, object()):
        with pytest.raises(AdminError, match="^monitoring_invalid_metadata$"):
            monitoring._number(value)


def test_separate_role_observations_are_not_coerced_to_one_cluster_snapshot(monkeypatch):
    operations, replication = snapshots()
    replication = replication.model_copy(update={
        "in_recovery": True, "primary_flush_lsn": None,
        "received_lsn": "0/16B6A80", "replayed_lsn": None, "replay_paused": True,
    })
    report = collected(
        monkeypatch, policy(rule("replication_replay_paused")),
        operations=operations, replication=replication,
    )
    assert report.operations.in_recovery is False
    assert report.replication.in_recovery is True
    assert report.snapshots_atomic is False
    assert report.production_qualified is False
    assert report.historical_retention_verified is False
    assert report.alerts[0].observed == 1 and report.alerts[0].state == "firing"
    assert monitoring.MonitoringReport.model_validate_json(report.model_dump_json()) == report
    assert sample(monitoring.render_prometheus(report), "pgag_replication_replay_paused") == ["1"]


@pytest.mark.parametrize("change", [
    {"snapshots_atomic": True}, {"snapshots_atomic": 0},
    {"production_qualified": True}, {"historical_retention_verified": 0},
    {"alerts": ()},
])
def test_forged_report_cannot_assert_authority_or_hide_alerts(monkeypatch, change):
    report = collected(monkeypatch, policy(rule()))
    with pytest.raises(AdminError, match="^monitoring_invalid_metadata$"):
        monitoring.render_prometheus(report.model_copy(update=change))


@pytest.mark.parametrize("change", [
    {"tenant_id": uuid4()}, {"jobs": {"total": True}}, {"evaluated_at": PRIVATE},
])
def test_invalid_snapshot_is_not_published_as_valid(monkeypatch, change):
    operations, _ = snapshots()
    install_snapshots(monkeypatch, operations=operations.model_copy(update=change))
    with pytest.raises(AdminError, match="^monitoring_invalid_metadata$"):
        monitoring.collect(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), policy())


def test_helpers_are_called_once_in_order_without_new_database_logic(monkeypatch):
    calls = install_snapshots(monkeypatch)
    request = OperationsStatusRequest(tenant_id=TENANT)
    report = monitoring.collect(PRIVATE, request, policy())
    assert calls == [("operations", PRIVATE, request), ("replication", PRIVATE)]
    assert report.operations.evaluated_at != report.replication.evaluated_at


def test_every_declared_code_has_one_bounded_metric_and_alert(monkeypatch):
    codes = get_args(monitoring.AlertCode)
    assert len(codes) == 17
    report = collected(monkeypatch, policy(*(rule(code) for code in reversed(codes))))
    assert tuple(alert.code for alert in report.alerts) == tuple(sorted(codes))
    text = monitoring.render_prometheus(report)
    for code in codes:
        assert len(sample(text, f"pgag_{code}")) == 1
    assert text.count("\npgag_alert_known{") == 17
    assert text.count("\npgag_alert_firing{") == 17
    assert text.count("# TYPE pgag_alert_known gauge\n") == 1
    assert len(text.encode()) < 32768


@pytest.mark.parametrize("data", [
    b"", b"{", b"\xff", b"{}", b'{"format":"pgag-monitoring-policy-v1","rules":[],"rules":[]}',
    b'{"format":"pgag-monitoring-policy-v1","rules":[],"private":"secret"}',
    b'{"format":"pgag-monitoring-policy-v1","rules":[{"code":"jobs_due_pending",'
    b'"operator":"gt","severity":"warning","threshold":NaN}]}',
    b" " * (monitoring.MAX_POLICY_BYTES + 1),
    b"[" * 2000 + b"]" * 2000,
])
def test_policy_file_validation_is_bounded_and_redacted(workspace, data):
    path = workspace / "policy.json"
    path.write_bytes(data)
    path.chmod(0o600)
    with pytest.raises(AdminError, match="^invalid_monitoring_policy$") as error:
        monitoring.load_policy(path)
    assert error.value.outcome_unknown is False


def test_policy_file_round_trip_and_exact_size_bound(workspace):
    selected = policy(rule("calls_unknown", severity="critical", threshold=10))
    path = policy_file(workspace, selected)
    assert monitoring.load_policy(path) == selected
    data = path.read_bytes()
    path.write_bytes(data + b" " * (monitoring.MAX_POLICY_BYTES - len(data)))
    assert monitoring.load_policy(path) == selected
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(AdminError, match="^invalid_monitoring_policy$"):
        monitoring.load_policy(path)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "writable"])
def test_policy_rejects_unsafe_objects_without_blocking(workspace, kind):
    good = policy_file(workspace)
    path = workspace / "unsafe.json"
    if kind == "symlink":
        path.symlink_to(good.name)
    elif kind == "hardlink":
        os.link(good, path)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_text(good.read_text())
        path.chmod(0o666)
    with pytest.raises(AdminError) as error:
        monitoring.load_policy(path)
    assert error.value.code in (
        "monitoring_policy_unavailable", "monitoring_unsafe_policy_file",
    )


def test_atomic_publication_private_permissions_and_staging_cleanup(workspace, monkeypatch):
    selected = policy(rule())
    path = policy_file(workspace, selected)
    output = workspace / "tenant.prom"
    install_snapshots(monkeypatch)
    replaced = []
    original = os.replace

    def replace(source, destination, **kwargs):
        assert source.endswith(".partial") and not source.endswith(".prom")
        assert destination == output.name
        directory = kwargs["src_dir_fd"]
        assert directory == kwargs["dst_dir_fd"]
        staged = os.stat(source, dir_fd=directory, follow_symlinks=False)
        assert stat.S_IMODE(staged.st_mode) == 0o600
        replaced.append(True)
        return original(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    report = monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), path, output)
    assert output.read_text() == monitoring.render_prometheus(report)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert replaced == [True]
    assert sorted(item.name for item in workspace.iterdir()) == ["policy.json", "tenant.prom"]
    old_inode = output.stat().st_ino
    monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), path, output)
    assert output.stat().st_ino != old_inode
    assert replaced == [True, True]


@pytest.mark.parametrize("stage", ["operations", "replication", "policy"])
def test_collection_failure_replaces_stale_success_with_failure_only(
    workspace, monkeypatch, stage,
):
    path = policy_file(workspace, policy(rule()))
    output = workspace / "tenant.prom"
    install_snapshots(monkeypatch)
    request = OperationsStatusRequest(tenant_id=TENANT)
    monitoring.export(PRIVATE, request, path, output)
    assert sample(output.read_text(), "pgag_collection_success") == ["1"]

    def unavailable(*args):
        raise AdminError("admin_database_unavailable")

    if stage == "policy":
        path.write_text(PRIVATE)
        expected = "invalid_monitoring_policy"
    else:
        monkeypatch.setattr(monitoring, f"{stage}_status", unavailable)
        expected = "admin_database_unavailable"
    with pytest.raises(AdminError, match=f"^{expected}$"):
        monitoring.export(PRIVATE, request, path, output)
    failed = output.read_text()
    assert sample(failed, "pgag_collection_success") == ["0"]
    assert "pgag_jobs_" not in failed
    assert "pgag_alert_" not in failed
    assert "pgag_operations_snapshot_" not in failed
    assert "pgag_replication_" not in failed
    assert PRIVATE not in failed
    assert len(list(workspace.iterdir())) == 2


def test_failed_publish_returns_failure_preserves_old_file_and_cleans_staging(
    workspace, monkeypatch,
):
    path = policy_file(workspace)
    output = workspace / "tenant.prom"
    output.write_text("previous\n")
    output.chmod(0o600)
    install_snapshots(monkeypatch)

    def denied(*args, **kwargs):
        raise PermissionError(PRIVATE)

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(AdminError, match="^monitoring_publish_failed$") as error:
        monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), path, output)
    assert str(error.value) == "monitoring_publish_failed"
    assert output.read_text() == "previous\n"
    assert sorted(item.name for item in workspace.iterdir()) == ["policy.json", "tenant.prom"]


def test_publication_rechecks_destination_before_replacing(workspace, monkeypatch):
    path = policy_file(workspace)
    output = workspace / "tenant.prom"
    target = workspace / "unrelated"
    target.write_text(PRIVATE)
    target.chmod(0o600)
    install_snapshots(monkeypatch)
    original = os.fsync
    changed = False

    def race(fd):
        nonlocal changed
        if not changed:
            output.symlink_to(target.name)
            changed = True
        return original(fd)

    monkeypatch.setattr(os, "fsync", race)
    with pytest.raises(AdminError, match="^monitoring_unsafe_output$"):
        monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), path, output)
    assert output.is_symlink()
    assert target.read_text() == PRIVATE
    assert not list(workspace.glob("*.partial"))
    assert not list(workspace.glob(".pgag-monitoring-*.partial"))


def test_failed_file_sync_does_not_replace_prior_output(workspace, monkeypatch):
    path = policy_file(workspace)
    output = workspace / "tenant.prom"
    output.write_text("old success")
    output.chmod(0o600)
    install_snapshots(monkeypatch)

    def failed(fd):
        raise OSError(PRIVATE)

    monkeypatch.setattr(os, "fsync", failed)
    with pytest.raises(AdminError, match="^monitoring_publish_failed$"):
        monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), path, output)
    assert output.read_text() == "old success"
    assert sorted(item.name for item in workspace.iterdir()) == ["policy.json", "tenant.prom"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "public"])
def test_unsafe_existing_output_is_rejected_before_collection(workspace, monkeypatch, kind):
    path = policy_file(workspace)
    output = workspace / "tenant.prom"
    other = workspace / "unrelated"
    other.write_text("leave unchanged")
    other.chmod(0o600)
    if kind == "symlink":
        output.symlink_to(other.name)
    elif kind == "hardlink":
        os.link(other, output)
    elif kind == "fifo":
        os.mkfifo(output, 0o600)
    elif kind == "directory":
        output.mkdir()
    else:
        output.write_text("public")
        output.chmod(0o644)

    def forbidden(*args):
        pytest.fail("Unsafe publication path reached collection")

    monkeypatch.setattr(monitoring, "collect", forbidden)
    with pytest.raises(AdminError, match="^monitoring_unsafe_output$"):
        monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), path, output)
    assert other.read_text() == "leave unchanged"


def test_private_directory_and_no_symlink_component_or_parent_traversal(
    workspace, monkeypatch,
):
    path = policy_file(workspace)
    calls = install_snapshots(monkeypatch)
    directory = workspace / "public"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    linked = workspace / "linked"
    linked.symlink_to("public", target_is_directory=True)
    invalid = [
        directory / "tenant.prom", linked / "tenant.prom",
        workspace.absolute() / "tenant.prom",
        workspace / ".." / "tenant.prom", workspace / "tenant.txt",
        workspace / "invalid\x00.prom",
    ]
    for output in invalid:
        with pytest.raises(AdminError):
            monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), path, output)
    assert calls == []
    assert not list(directory.iterdir())


def test_policy_and_output_cannot_be_the_same_file(workspace, monkeypatch):
    output = workspace / "tenant.prom"
    output.write_text(policy().model_dump_json())
    output.chmod(0o600)
    calls = install_snapshots(monkeypatch)
    with pytest.raises(AdminError, match="^monitoring_unsafe_output$"):
        monitoring.export(PRIVATE, OperationsStatusRequest(tenant_id=TENANT), output, output)
    assert calls == []
    assert output.read_text() == policy().model_dump_json()


def test_real_snapshots_export_counts_ages_no_mutation_or_private_labels(env, workspace, profile):
    configure(env, profile)
    _, job = add_job(env)
    with psycopg.connect(env.admin_url) as conn:
        due_at = conn.execute(
            "SELECT available_at FROM memory_ops.job WHERE tenant_id=%s AND id=%s",
            (env.tenants[0], job),
        ).fetchone()[0]
    before = canonical_state(env)
    selected = policy(
        rule("jobs_due_pending", operator="ge", threshold=1),
        rule("jobs_oldest_due_age_seconds", operator="ge", threshold=0, severity="critical"),
        rule("graph_epoch_schema_match", operator="eq", threshold=0),
    )
    path = policy_file(workspace, selected)
    output = workspace / "tenant.prom"
    lower = datetime.now(UTC)
    report = monitoring.export(
        env.admin_url, OperationsStatusRequest(tenant_id=env.tenants[0]), path, output,
    )
    assert lower <= report.evaluated_at <= datetime.now(UTC)
    assert report.operations.jobs.total == report.operations.jobs.due_pending == 1
    assert report.operations.jobs.oldest_due_age_seconds == pytest.approx(
        (report.operations.evaluated_at - due_at).total_seconds(), abs=0.000001,
    )
    assert report.operations.evaluated_at <= report.replication.evaluated_at
    assert report.replication.transaction_read_only is True
    assert report.snapshots_atomic is False
    assert [alert.state for alert in report.alerts] == ["unknown", "firing", "firing"]
    assert canonical_state(env) == before
    text = output.read_text()
    assert text == monitoring.render_prometheus(report)
    assert f'pgag_jobs_due_pending{{tenant_id="{env.tenants[0]}"}} 1\n' in text
    for private in (
        env.admin_url, job, "PRIVATE_STATUS_CONTENT", *env.scopes, *env.principals,
        *env.subjects, env.tenants[1],
    ):
        assert str(private) not in text
    other = monitoring.collect(
        env.admin_url, OperationsStatusRequest(tenant_id=env.tenants[1]), selected,
    )
    assert other.operations.jobs.total == 0
    assert [(alert.code, alert.state) for alert in other.alerts] == [
        ("graph_epoch_schema_match", "unknown"), ("jobs_due_pending", "inactive"),
        ("jobs_oldest_due_age_seconds", "unknown"),
    ]


def test_real_observer_does_not_wait_for_tenant_admission_lock(env, workspace):
    path = policy_file(workspace)
    output = workspace / "tenant.prom"
    with psycopg.connect(env.admin_url, autocommit=True) as locker:
        locker.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),),
        )
        report = monitoring.export(
            env.admin_url, OperationsStatusRequest(tenant_id=env.tenants[0]), path, output,
        )
        assert report.operations.tenant_id == env.tenants[0]
        assert report.production_qualified is False


def test_real_wrong_role_and_missing_tenant_replace_previous_success(env, workspace):
    path = policy_file(workspace)
    output = workspace / "tenant.prom"
    request = OperationsStatusRequest(tenant_id=env.tenants[0])
    monitoring.export(env.admin_url, request, path, output)
    with pytest.raises(AdminError, match="^admin_role_required$"):
        monitoring.export(env.settings.database_url, request, path, output)
    assert "pgag_jobs_" not in output.read_text()
    assert f'pgag_collection_success{{tenant_id="{env.tenants[0]}"}} 0\n' in output.read_text()
    with pytest.raises(AdminError, match="^not_found$"):
        monitoring.export(
            env.admin_url, OperationsStatusRequest(tenant_id=uuid4()), path, output,
        )
    assert "pgag_jobs_" not in output.read_text()


def test_cli_success_and_alerts_are_not_command_failures(workspace, monkeypatch, capsys):
    path = policy_file(workspace, policy(rule()))
    output = workspace / "tenant.prom"
    install_snapshots(monkeypatch)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", PRIVATE)
    monitoring.main([
        "--tenant-id", str(TENANT), "--policy-file", str(path), "--output", str(output),
    ])
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert report["format"] == "pgag-monitoring-export-v1"
    assert report["alerts"][0]["state"] == "firing"
    assert PRIVATE not in captured.out
    assert output.is_file()


def test_cli_failure_is_nonzero_and_has_no_stale_success(workspace, monkeypatch, capsys):
    path = policy_file(workspace)
    output = workspace / "tenant.prom"
    output.write_text("old success")
    output.chmod(0o600)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", PRIVATE)

    def unavailable(*args):
        raise AdminError("admin_database_unavailable")

    monkeypatch.setattr(monitoring, "operations_status", unavailable)
    with pytest.raises(SystemExit) as error:
        monitoring.main([
            "--tenant-id", str(TENANT), "--policy-file", str(path), "--output", str(output),
        ])
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "error": {"code": "admin_database_unavailable", "outcome_unknown": False},
    }
    assert captured.err == ""
    assert sample(output.read_text(), "pgag_collection_success") == ["0"]
    assert "old success" not in output.read_text()


@pytest.mark.parametrize("arguments", [
    [],
    ["--tenant-id", PRIVATE],
    ["--tenant-id", str(TENANT), "--policy-file", PRIVATE],
    ["--tenant-id", str(TENANT), "--policy-file", PRIVATE, "--output", PRIVATE,
     "--unexpected", PRIVATE],
])
def test_cli_argument_errors_are_redacted(arguments, capsys):
    with pytest.raises(SystemExit) as error:
        monitoring.main(arguments)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid_monitoring_arguments" in captured.err
    assert PRIVATE not in captured.err


def test_cli_requires_admin_url_and_does_not_fallback(workspace, monkeypatch, capsys):
    monkeypatch.delenv("PGAG_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("PGAG_DATABASE_URL", PRIVATE)
    output = workspace / "tenant.prom"
    with pytest.raises(SystemExit) as error:
        monitoring.main([
            "--tenant-id", str(TENANT), "--policy-file", str(workspace / "policy.json"),
            "--output", str(output),
        ])
    assert error.value.code == 2
    assert "PGAG_ADMIN_DATABASE_URL is required" in capsys.readouterr().err
    assert not output.exists()
