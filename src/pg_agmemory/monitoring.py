"""One-shot private textfile observations; scheduling and retention are external."""

import argparse
import json
import math
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Never, Self
from uuid import UUID, uuid4

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
from pg_agmemory.admin import AdminError
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.operations_status import (
    OperationsStatus,
    OperationsStatusRequest,
    operations_status,
)
from pg_agmemory.replication_status import ReplicationStatus, replication_status

MAX_POLICY_BYTES = 16384
AlertCode = Literal[
    "jobs_due_pending",
    "jobs_oldest_due_age_seconds",
    "jobs_expired_running",
    "jobs_stale_active",
    "jobs_failed",
    "calls_unknown",
    "calls_billing_unknown",
    "source_expired_allow",
    "source_unexpected_grants",
    "deletions_blocked_for_reads",
    "deletions_legacy_manifests",
    "graph_epoch_schema_match",
    "replication_senders_physical_streaming",
    "replication_senders_physical_synchronous",
    "replication_slots_inactive",
    "replication_receiver_streaming",
    "replication_replay_paused",
]
Number = Annotated[int, Field(ge=0)] | Annotated[
    float, Field(ge=0, allow_inf_nan=False)
]
MetricValue = int | float | None


class MonitoringContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        revalidate_instances="always", hide_input_in_errors=True,
    )


class AlertRule(MonitoringContract):
    code: AlertCode
    operator: Literal["gt", "ge", "lt", "le", "eq"]
    threshold: Number
    severity: Literal["info", "warning", "critical"]

    @field_validator("threshold")
    @classmethod
    def representable_threshold(cls, value: int | float) -> int | float:
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("Threshold cannot be represented by the collector")
        return value


class MonitoringPolicy(MonitoringContract):
    format: Literal["pgag-monitoring-policy-v1"]
    rules: Annotated[tuple[AlertRule, ...], Field(max_length=17)]

    @field_validator("rules")
    @classmethod
    def unique_ordered_rules(cls, rules: tuple[AlertRule, ...]) -> tuple[AlertRule, ...]:
        if len({rule.code for rule in rules}) != len(rules):
            raise ValueError("Duplicate monitoring rule")
        return tuple(sorted(rules, key=lambda rule: rule.code))


class AlertObservation(AlertRule):
    observed: Number | None
    state: Literal["firing", "inactive", "unknown"]


def _metric_values(
    operations: OperationsStatus, replication: ReplicationStatus,
) -> dict[str, MetricValue]:
    values: dict[str, MetricValue] = {
        "operations_in_recovery": int(operations.in_recovery),
        "operations_access_epoch": operations.access_epoch,
        "operations_deletion_epoch": operations.deletion_epoch,
        "replication_in_recovery": int(replication.in_recovery),
        "replication_synchronous_standby_configured": int(
            replication.synchronous_standby_configured,
        ),
        "replication_replay_paused": (
            None if replication.replay_paused is None else int(replication.replay_paused)
        ),
    }
    for prefix, section in (
        ("jobs", operations.jobs),
        ("calls", operations.calls),
        ("source", operations.source),
        ("deletions", operations.deletions),
        ("graph", operations.graph),
        ("replication_senders", replication.senders),
        ("replication_receiver", replication.receiver),
        ("replication_slots", replication.slots),
    ):
        for name, value in section.model_dump().items():
            values[f"{prefix}_{name}"] = int(value) if isinstance(value, bool) else value
    return values


def _alerts(
    policy: MonitoringPolicy, values: dict[str, MetricValue],
) -> tuple[AlertObservation, ...]:
    result: list[AlertObservation] = []
    for rule in policy.rules:
        value = values[rule.code]
        state: Literal["firing", "inactive", "unknown"] = "unknown"
        if value is not None:
            if rule.operator == "gt":
                firing = value > rule.threshold
            elif rule.operator == "ge":
                firing = value >= rule.threshold
            elif rule.operator == "lt":
                firing = value < rule.threshold
            elif rule.operator == "le":
                firing = value <= rule.threshold
            else:
                firing = value == rule.threshold
            state = "firing" if firing else "inactive"
        result.append(AlertObservation(**rule.model_dump(), observed=value, state=state))
    return tuple(result)


class MonitoringReport(MonitoringContract):
    format: Literal["pgag-monitoring-export-v1"] = "pgag-monitoring-export-v1"
    evaluated_at: AwareDatetime
    policy: MonitoringPolicy
    operations: OperationsStatus
    replication: ReplicationStatus
    alerts: tuple[AlertObservation, ...]
    snapshots_atomic: Literal[False] = False
    historical_retention_verified: Literal[False] = False
    production_qualified: Literal[False] = False

    @field_validator(
        "snapshots_atomic", "historical_retention_verified", "production_qualified",
        mode="before",
    )
    @classmethod
    def false_flags(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("Invalid observation flag")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def exact_alerts(self) -> Self:
        if self.alerts != _alerts(
            self.policy, _metric_values(self.operations, self.replication),
        ):
            raise ValueError("Inconsistent alert observations")
        return self


def collect(
    url: str, request: OperationsStatusRequest, policy: MonitoringPolicy,
) -> MonitoringReport:
    try:
        request = OperationsStatusRequest.model_validate(request)
        policy = MonitoringPolicy.model_validate(policy)
    except ValidationError:
        raise AdminError("invalid_monitoring_request") from None
    operations = operations_status(url, request)
    replication = replication_status(url)
    try:
        operations = OperationsStatus.model_validate(operations)
        replication = ReplicationStatus.model_validate(replication)
        if operations.tenant_id != request.tenant_id:
            raise AdminError("monitoring_invalid_metadata")
        return MonitoringReport(
            evaluated_at=datetime.now(UTC), policy=policy,
            operations=operations, replication=replication,
            alerts=_alerts(policy, _metric_values(operations, replication)),
        )
    except ValidationError:
        raise AdminError("monitoring_invalid_metadata") from None


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: MetricValue) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdminError("monitoring_invalid_metadata")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise AdminError("monitoring_invalid_metadata")
    return str(value)


def _sample(name: str, value: MetricValue, labels: dict[str, str]) -> str:
    rendered = ",".join(f'{key}="{_label(item)}"' for key, item in labels.items())
    return f"{name}{{{rendered}}} {_number(value)}"


def _gauge(name: str, value: MetricValue, labels: dict[str, str]) -> list[str]:
    return [f"# TYPE {name} gauge", _sample(name, value, labels)]


def _header(tenant_id: UUID, evaluated_at: datetime, *, success: bool) -> list[str]:
    labels = {"tenant_id": str(tenant_id)}
    lines = [
        "# pgag-monitoring-export-v1; one-shot observations, not authority or retained history.",
        "# Unknown or inapplicable observations are NaN, never zero.",
    ]
    lines += _gauge("pgag_collection_success", int(success), labels)
    lines += _gauge("pgag_collection_timestamp_seconds", evaluated_at.timestamp(), labels)
    lines += _gauge("pgag_snapshots_atomic", 0, labels)
    lines += _gauge("pgag_historical_retention_verified", 0, labels)
    lines += _gauge("pgag_production_qualified", 0, labels)
    lines += _gauge(
        "pgag_export_info", 1,
        {**labels, "service_version": __version__, "schema_version": str(SCHEMA_VERSION)},
    )
    return lines


def render_prometheus(report: MonitoringReport) -> str:
    try:
        report = MonitoringReport.model_validate(report)
    except ValidationError:
        raise AdminError("monitoring_invalid_metadata") from None
    labels = {"tenant_id": str(report.operations.tenant_id)}
    lines = _header(report.operations.tenant_id, report.evaluated_at, success=True)
    lines += _gauge(
        "pgag_operations_snapshot_timestamp_seconds",
        report.operations.evaluated_at.timestamp(), labels,
    )
    lines += _gauge(
        "pgag_replication_observation_timestamp_seconds",
        report.replication.evaluated_at.timestamp(), labels,
    )
    for name, value in _metric_values(report.operations, report.replication).items():
        lines += _gauge(f"pgag_{name}", value, labels)
    lines += _gauge(
        "pgag_replication_session_commit_info", 1,
        {**labels, "synchronous_commit": report.replication.synchronous_commit},
    )
    if report.alerts:
        lines.extend((
            "# TYPE pgag_alert_known gauge",
            "# TYPE pgag_alert_firing gauge",
            "# TYPE pgag_alert_threshold gauge",
        ))
    for alert in report.alerts:
        alert_labels = {
            **labels, "code": alert.code, "severity": alert.severity,
            "operator": alert.operator,
        }
        lines.append(_sample(
            "pgag_alert_known", int(alert.state != "unknown"), alert_labels,
        ))
        lines.append(_sample(
            "pgag_alert_firing",
            None if alert.state == "unknown" else int(alert.state == "firing"), alert_labels,
        ))
        lines.append(_sample("pgag_alert_threshold", alert.threshold, alert_labels))
    return "\n".join(lines) + "\n"


@contextmanager
def _parent(path: Path, *, private: bool) -> Iterator[tuple[int, str]]:
    if path.is_absolute() or not path.parts or ".." in path.parts or "\x00" in str(path):
        raise AdminError("monitoring_unsafe_path")
    directory = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory,
            )
            os.close(directory)
            directory = child
        info = os.fstat(directory)
        if private and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise AdminError("monitoring_unsafe_path")
        yield directory, path.name
    finally:
        os.close(directory)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate policy key")
        result[key] = value
    return result


def load_policy(path: Path) -> MonitoringPolicy:
    try:
        with _parent(path, private=False) as (directory, name):
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o022
                ):
                    raise AdminError("monitoring_unsafe_policy_file")
                data = stream.read(MAX_POLICY_BYTES + 1)
        if len(data) > MAX_POLICY_BYTES:
            raise AdminError("invalid_monitoring_policy")
        json.loads(data, object_pairs_hook=_unique_object)
        return MonitoringPolicy.model_validate_json(data)
    except (ValueError, RecursionError):
        raise AdminError("invalid_monitoring_policy") from None
    except OSError:
        raise AdminError("monitoring_policy_unavailable") from None


def _check_output(directory: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise AdminError("monitoring_unsafe_output")


def _publish(directory: int, name: str, text: str) -> None:
    staged = f".pgag-monitoring-{uuid4().hex}.partial"
    fd = os.open(
        staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        _check_output(directory, name)
        os.replace(staged, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(staged, dir_fd=directory)
        except FileNotFoundError:
            pass


def export(
    url: str, request: OperationsStatusRequest, policy_file: Path, output: Path,
) -> MonitoringReport:
    """On collection/policy failure replace old observations with a failure-only artifact."""
    try:
        request = OperationsStatusRequest.model_validate(request)
    except ValidationError:
        raise AdminError("invalid_monitoring_request") from None
    if output.suffix != ".prom" or policy_file == output:
        raise AdminError("monitoring_unsafe_output")
    try:
        with _parent(output, private=True) as (directory, name):
            _check_output(directory, name)
            try:
                policy = load_policy(policy_file)
                report = collect(url, request, policy)
                text = render_prometheus(report)
            except AdminError:
                _publish(
                    directory, name,
                    "\n".join(_header(request.tenant_id, datetime.now(UTC), success=False)) + "\n",
                )
                raise
            _publish(directory, name, text)
            return report
    except OSError:
        raise AdminError("monitoring_publish_failed") from None


class MonitoringParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_monitoring_arguments")


def main(argv: list[str]) -> None:
    parser = MonitoringParser(
        prog="pg-agmemory monitoring-export",
        description=(
            "ADMIN-only one-shot private Prometheus textfile export. Required operator policy "
            "defines every threshold/severity; no defaults or production SLOs. Composes "
            "separate read-only operations and replication observations, not atomic evidence."
        ),
        epilog=(
            "Uses PGAG_ADMIN_DATABASE_URL only. Paths must be project-relative without "
            "symlinks; output requires an existing owned private directory and a .prom suffix. "
            "Schedule nonoverlapping runs externally. The external collector owns collection, "
            "missing/stale-file alerts, routing and historical retention. Unknown values are "
            "NaN with alert_known=0, not inactive alerts. Policy/collection failure publishes "
            "collection_success=0 without old observations; publication/argument failure may "
            "leave the previous file, so command exits and freshness must also be monitored. "
            "No worker, admission lock, remediation, promotion or retention proof."
        ),
    )
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory monitoring-export: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        report = export(
            url, OperationsStatusRequest(tenant_id=args.tenant_id),
            args.policy_file, args.output,
        )
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": False}}),
            flush=True,
        )
        raise SystemExit(1) from None
    print(report.model_dump_json(), flush=True)
