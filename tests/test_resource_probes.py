import importlib.util
import json
from pathlib import Path

import pytest

from pg_agmemory.api import create_app
from pg_agmemory.database import Settings

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "resource_probes", ROOT / "scripts/resource-probes.py"
)
assert SPEC is not None and SPEC.loader is not None
probes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probes)


def test_probe_uses_actual_forget_status_and_frozen_bounds():
    settings = Settings(
        database_url="postgresql://unused",
        jwt_public_key="unused",
        jwt_issuer="https://issuer.invalid",
        jwt_audience="test",
    )
    route = next(r for r in create_app(settings).routes if getattr(r, "path", "") == "/v1/forget")
    assert probes.FORGET_STATUS == route.status_code == 202
    plan = json.loads((ROOT / "examples/resource-probes-plan.json").read_text())
    assert plan["small_forget_samples"] == 100
    assert plan["small_forget_p95_ms"] == 1000
    assert plan["large_purge_objects"] == 10000
    assert plan["large_purge_deadline_seconds"] == 900
    assert plan["queue_capacity"] == 2 and plan["call_capacity"] == 1
    assert plan["input_limit_bytes"] == 1024 and plan["output_limit_tokens"] == 128
    assert (
        plan["small_forget_start_seconds"] + plan["small_forget_window_seconds"]
        < plan["mixed_load_seconds"]
    )


def sample_report(directory):
    plan = json.loads((ROOT / "examples/resource-probes-plan.json").read_text())
    for name, value in {
        "plan.json": plan,
        "build-identity.json": {"exact_commit_inputs": False, "implementation_sha": "a" * 40},
        "migration.json": {"restored_schema": 16, "probe_schema": 17},
        "limits.json": {"status": "passed"},
        "small-forget.json": {"status": "passed"},
        "large-purge.json": {"status": "passed"},
        "statistics-restore.json": {"operation": "ANALYZE", "phase": "restore"},
        "statistics-cold.json": {"operation": "ANALYZE", "phase": "cold"},
    }.items():
        (directory / name).write_text(json.dumps(value))
    metrics = []
    for index in range(12):
        samples = []
        for repeat in range(6):
            request_id = f"{index}-{repeat}"
            samples.append({"phase": "cold" if repeat == 0 else "warm", "request_id": request_id})
            metrics.append({"request_id": request_id, "status": 200, "transaction_ms": 10.0})
        (directory / f"cold-{index}.json").write_text(
            json.dumps(
                {
                    "mode": plan["cold_modes"][index // 4],
                    "selectivity_percent": plan["cold_selectivity_percent"][index % 4],
                    "before": {
                        "boot_id": f"boot-{index}",
                        "started": f"start-{index}",
                        "uptime": "15 20",
                    },
                    "samples": samples,
                    "physical_host_cold": False,
                }
            )
        )
    (directory / "cold-server-test.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in metrics)
    )


@pytest.mark.parametrize(
    "case", ["missing_samples", "duplicate_request", "reused_boot", "missing_timing"]
)
def test_cold_proof_rejects_missing_or_reused_evidence(tmp_path, case):
    sample_report(tmp_path)
    path = tmp_path / "cold-1.json"
    value = json.loads(path.read_text())
    if case == "missing_samples":
        value["samples"] = []
    elif case == "duplicate_request":
        value["samples"][0]["request_id"] = "0-0"
    elif case == "reused_boot":
        value["before"]["boot_id"] = "boot-0"
    else:
        value["samples"][0]["request_id"] = "absent"
    path.write_text(json.dumps(value))
    with pytest.raises(probes.bench.ResourceError):
        probes.report(tmp_path)
    assert not (tmp_path / "report.json").exists()


def test_guest_cold_does_not_claim_physical_host_cold_or_full_qualification(tmp_path):
    sample_report(tmp_path)
    probes.report(tmp_path)
    result = json.loads((tmp_path / "report.json").read_text())
    assert result["probe_checks_passed"]
    assert not result["resource_qualified"] and not result["m2_qualified"]
    assert "physical-host-device-cold-cache" in result["unqualified"]
    assert len(result["cold_guest"]) == 12


@pytest.mark.parametrize("name", ["limits", "small-forget", "large-purge"])
def test_failed_probe_retains_report_but_fails_command(tmp_path, name):
    sample_report(tmp_path)
    (tmp_path / f"{name}.json").write_text(json.dumps({"status": "failed"}))
    with pytest.raises(probes.bench.ResourceError, match="resource probes failed"):
        probes.report(tmp_path)
    result = json.loads((tmp_path / "report.json").read_text())
    assert not result["probe_checks_passed"]
    assert not result["resource_qualified"] and not result["m2_qualified"]


def test_rebind_changes_only_host_and_preserves_private_mode(tmp_path, monkeypatch):
    settings = {
        "database_url": "host=192.0.2.1 dbname=probe user=runtime password=PRIVATE",
        "jwt_public_key": "public",
        "jwt_issuer": "issuer",
        "jwt_audience": "audience",
    }
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(settings))
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "host=192.0.2.2 dbname=probe user=postgres")
    probes.rebind(tmp_path)
    updated = json.loads(path.read_text())
    parsed = probes.conninfo_to_dict(updated.pop("database_url"))
    assert parsed == {
        "host": "192.0.2.2",
        "dbname": "probe",
        "user": "runtime",
        "password": "PRIVATE",
    }
    assert updated == {k: v for k, v in settings.items() if k != "database_url"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "runtime-rebind.json").exists()


def stale_job():
    return {
        "state": "failed", "error_code": "stale_context", "deletion_epoch_stale": True,
        "result_id": None, "has_candidates": False, "call_outcome": "failed",
        "billing_unknown": False,
    }


@pytest.mark.parametrize("reserved", [True, False])
def test_mixed_deletion_counts_safe_rejection_separately(reserved):
    row = stale_job()
    if not reserved:
        row.update(call_outcome=None, billing_unknown=None)
    assert probes.background_outcomes([row], 1) == {
        "succeeded_jobs": 0, "stale_context_rejections": 1,
    }


@pytest.mark.parametrize("change", [
    {"state": "pending"}, {"error_code": "dependency_unavailable"},
    {"deletion_epoch_stale": False}, {"result_id": "published"},
    {"has_candidates": True}, {"call_outcome": "unknown"},
    {"call_outcome": "succeeded"}, {"billing_unknown": True},
])
def test_mixed_deletion_rejects_unproven_or_unsafe_failure(change):
    with pytest.raises(probes.bench.ResourceError):
        probes.background_outcomes([stale_job() | change], 1)
