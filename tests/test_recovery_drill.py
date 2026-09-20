"""Fail-closed boundaries of the synthetic logical-backup drill, not a restore API."""

import hashlib
import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

spec = importlib.util.spec_from_file_location(
    "recovery_drill", Path(__file__).resolve().parents[1] / "scripts" / "smoke-recovery.py"
)
assert spec is not None and spec.loader is not None
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


@pytest.mark.parametrize("changes,tracked,untracked", [
    ([], False, False),
    ([" M src/pg_agmemory/service.py"], True, False),
    (["?? scripts/smoke-recovery.py"], False, True),
    ([" M src/pg_agmemory/service.py", "?? scripts/smoke-recovery.py"], True, True),
])
def test_build_identity_distinguishes_dirty_experimental_inputs(
    monkeypatch, changes, tracked, untracked,
):
    paths = ("scripts/smoke-recovery.py", "scripts/test-recovery-containers.sh",
             "tests/test_recovery_drill.py")
    identity = {
        "git_sha": "a" * 40,
        "files_sha256": {p: hashlib.sha256(b"measured input").hexdigest() for p in paths},
        "git_status_porcelain": changes,
        "has_tracked_changes": tracked,
        "has_untracked_changes": untracked,
        "exact_commit_inputs": not changes,
    }
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"measured input")
    monkeypatch.setenv("PGAG_RECOVERY_BUILD_IDENTITY", json.dumps(identity))
    assert drill.build_identity() == identity
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"changed during run")
    with pytest.raises(drill.DrillError, match="inputs changed during run"):
        drill.build_identity()


@pytest.mark.parametrize("rm_status,rmdir_status,original_status,expected", [
    (0, 0, 0, 0), (1, 0, 0, 1), (0, 1, 0, 1), (1, 1, 7, 7),
])
def test_cleanup_removes_only_named_artifacts_and_surfaces_failure(
    rm_status, rmdir_status, original_status, expected,
):
    helper = Path(__file__).resolve().parents[1] / "scripts" / "test-recovery-containers.sh"
    cleanup = helper.read_text().split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    completed = subprocess.run(
        ["bash", "-c", f"""
directory=synthetic-recovery
containers=()
network_created=false
image_built=false
rm() {{ printf 'rm-argument:%s\\n' "$@"; return {rm_status}; }}
rmdir() {{ printf 'rmdir-argument:%s\\n' "$@"; return {rmdir_status}; }}
cleanup() {{{cleanup}
(exit {original_status})
cleanup
"""],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == expected
    assert completed.stdout.splitlines() == [
        "rm-argument:-f", "rm-argument:--",
        "rm-argument:./synthetic-recovery/old.dump",
        "rm-argument:./synthetic-recovery/latest.dump",
        "rm-argument:./synthetic-recovery/before.json",
        "rm-argument:./synthetic-recovery/latest.json",
        "rm-argument:./synthetic-recovery/report.json",
        "rmdir-argument:--", "rmdir-argument:./synthetic-recovery",
    ]
    if rm_status:
        assert "Failed to remove known recovery artifacts" in completed.stderr
    if rmdir_status:
        assert "unexpected files are never deleted" in completed.stderr


def evidence():
    operator = {"tenant_id": "tenant", "id": "operator", "external_subject": "operator"}
    reader = {"tenant_id": "tenant", "id": "reader", "external_subject": "reader"}
    member = {
        "tenant_id": "tenant", "scope_id": "scope", "principal_id": "operator",
        "permissions": ["admin"], "expires_at": None,
    }
    revoked_member = {**member, "principal_id": "reader", "permissions": ["read"]}
    before = {
        "format": "pgag-isolated-purge-drill-v3",
        "schema_version": 14,
        "tenant": [{"id": "tenant", "access_epoch": 3, "deletion_epoch": 1}],
        "principals": [operator, reader],
        "objects": [{"tenant_id": "tenant", "id": "object", "scope_id": "scope",
                     "kind": "episode"}],
        "memberships": [member, revoked_member],
        "tombstones": [],
        "deletions": [],
        "deletion_targets": [],
        "access_events": [],
        "canonical": {table: {"count": 0} for table in (
            "memory.scope_synthesis_policy", "memory.scope_capture_policy", "memory_ops.model_call",
            "memory.working_snapshot", "memory_ops.extraction_candidate",
        )},
    }
    latest = deepcopy(before)
    latest.update({
        "tenant": [{"id": "tenant", "access_epoch": 4, "deletion_epoch": 2}],
        "memberships": [member],
        "tombstones": [{"tenant_id": "tenant", "scope_id": "scope", "object_id": "object"}],
        "deletions": [{
            "tenant_id": "tenant", "id": "receipt", "principal_id": "operator",
            "mode": "purge", "state": "active_store_purged",
            "object_count": 1, "deletion_epoch": 2,
            "target_manifest_version": 1,
        }],
        "deletion_targets": [{
            "tenant_id": "tenant", "deletion_id": "receipt",
            "object_id": "object", "scope_id": "scope",
        }],
        "access_events": [{
            "tenant_id": "tenant", "scope_id": "scope", "principal_id": "reader",
            "access_epoch": 4, "operation": "revoke", "previous_permissions": ["read"],
            "previous_expires_at": None, "permissions": None, "expires_at": None,
        }],
    })
    def identifiers(value):
        if isinstance(value, dict):
            return {
                k: str(uuid5(NAMESPACE_URL, v))
                if k in ("id", "tenant_id", "scope_id", "principal_id", "object_id", "deletion_id")
                else identifiers(v) for k, v in value.items()
            }
        if isinstance(value, list):
            return [identifiers(v) for v in value]
        return value
    return identifiers(before), identifiers(latest)


def test_single_purge_uses_server_tombstones_and_receipt_actor():
    before, latest = evidence()
    receipts, events, actors = drill.validate_evidence(before, latest)
    assert str(receipts[0].deletion_id) == latest["deletions"][0]["id"]
    assert events[0] is latest["access_events"][0]
    assert actors[str(receipts[0].principal_id)] is latest["principals"][0]
    assert before["tombstones"] == []


@pytest.mark.parametrize("field,value", [
    ("mode", "suppress"),
    ("state", "blocked_for_reads"),
    ("object_count", 2),
    ("deletion_epoch", 3),
    ("principal_id", "missing-actor"),
    ("tenant_id", "another-tenant"),
    ("target_manifest_version", 0),
])
def test_incomplete_or_unsupported_receipt_is_rejected(field, value):
    before, latest = evidence()
    latest["deletions"][0][field] = value
    with pytest.raises(drill.DrillError):
        drill.validate_evidence(before, latest)


@pytest.mark.parametrize("field", ["tombstones", "deletions", "deletion_targets", "access_events"])
def test_missing_authoritative_metadata_is_not_reconstructed(field):
    before, latest = evidence()
    latest[field] = []
    with pytest.raises(drill.DrillError):
        drill.validate_evidence(before, latest)


@pytest.mark.parametrize("field", ["deletions", "tombstones", "deletion_targets", "access_events"])
def test_duplicate_or_multiple_history_is_not_guessed(field):
    before, latest = evidence()
    latest[field].append(deepcopy(latest[field][0]))
    with pytest.raises(drill.DrillError):
        drill.validate_evidence(before, latest)


def test_old_tombstones_are_not_repurged_through_rls():
    before, latest = evidence()
    before["tombstones"] = deepcopy(latest["tombstones"])
    with pytest.raises(drill.DrillError, match="deletion target manifest"):
        drill.validate_evidence(before, latest)


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("table", [
    "memory.scope_synthesis_policy", "memory.scope_capture_policy", "memory_ops.model_call",
    "memory.working_snapshot", "memory_ops.extraction_candidate",
])
def test_stale_enabled_processing_or_accounting_requires_a_separate_gate(stage, table):
    pair = evidence()
    pair[stage]["canonical"][table]["count"] = 1
    with pytest.raises(drill.DrillError, match="model processing must remain disabled"):
        drill.validate_evidence(*pair)


@pytest.mark.parametrize("field,value", [
    ("access_epoch", 5),
    ("operation", "set"),
    ("previous_permissions", ["write"]),
    ("principal_id", "missing"),
    ("scope_id", "another-scope"),
    ("permissions", ["read"]),
])
def test_revocation_must_reconcile_with_latest_and_previous_state(field, value):
    before, latest = evidence()
    latest["access_events"][0][field] = value
    with pytest.raises(drill.DrillError):
        drill.validate_evidence(before, latest)


def test_revoked_actor_cannot_be_used_as_a_recovery_operator():
    before, latest = evidence()
    latest["deletions"][0]["principal_id"] = latest["principals"][1]["id"]
    with pytest.raises(drill.DrillError, match="no longer authorized"):
        drill.validate_evidence(before, latest)


def test_latest_membership_disagreement_fails_closed():
    before, latest = evidence()
    latest["memberships"] = deepcopy(before["memberships"])
    with pytest.raises(drill.DrillError, match="disagrees with revocation"):
        drill.validate_evidence(before, latest)


def test_acl_history_prefix_cannot_be_replaced():
    before, latest = evidence()
    before["access_events"] = [{"access_epoch": 2}]
    with pytest.raises(drill.DrillError, match="ACL history prefix"):
        drill.validate_evidence(before, latest)


@pytest.mark.parametrize("field", ["tenant_id", "deletion_id", "object_id", "scope_id"])
def test_recovery_targets_must_bind_the_exact_receipt(field):
    before, latest = evidence()
    latest["deletion_targets"][0][field] = "different"
    with pytest.raises(drill.DrillError, match="deletion"):
        drill.validate_evidence(before, latest)


def multiple_evidence():
    before, latest = evidence()
    for key in ("deletions", "deletion_targets", "tombstones"):
        before[key] = deepcopy(latest[key])
    before["tenant"][0]["deletion_epoch"] = 2
    second = str(uuid5(NAMESPACE_URL, "second-object"))
    receipt = str(uuid5(NAMESPACE_URL, "second-receipt"))
    anchor = before["objects"][0] | {"id": second}
    before["objects"].append(anchor)
    latest["objects"].append(deepcopy(anchor))
    latest["tenant"][0]["deletion_epoch"] = 3
    latest["deletions"].append(latest["deletions"][0] | {"id": receipt, "deletion_epoch": 3})
    latest["deletion_targets"].append(
        latest["deletion_targets"][0] | {"deletion_id": receipt, "object_id": second}
    )
    latest["tombstones"].append(latest["tombstones"][0] | {"object_id": second})
    return before, latest


def test_nonempty_baseline_is_preserved_and_only_suffix_is_replayed():
    before, latest = multiple_evidence()
    receipts, events, _ = drill.validate_evidence(before, latest)
    assert len(receipts) == len(events) == 1
    assert str(receipts[0].deletion_id) == latest["deletions"][1]["id"]
    assert str(receipts[0].targets[0].object_id) == latest["objects"][1]["id"]
    assert drill.validate_evidence(latest, latest)[:2] == ((), [])


@pytest.mark.parametrize("field", ["id", "principal_id", "object_count", "deletion_epoch"])
def test_deletion_history_prefix_cannot_be_replaced(field):
    before, latest = multiple_evidence()
    latest["deletions"][0][field] = (
        42 if field in ("object_count", "deletion_epoch") else str(uuid5(NAMESPACE_URL, "other"))
    )
    with pytest.raises(drill.DrillError):
        drill.validate_evidence(before, latest)


def test_ordered_permission_reduction_then_revoke():
    before, latest = evidence()
    before["memberships"][1]["permissions"] = ["read", "write"]
    revoke = latest["access_events"][0]
    reduction = revoke | {
        "operation": "set", "previous_permissions": ["read", "write"],
        "permissions": ["read"],
    }
    latest["access_events"] = [reduction, revoke | {"access_epoch": 5}]
    latest["tenant"][0]["access_epoch"] = 5
    assert len(drill.validate_evidence(before, latest)[1]) == 2
    latest["access_events"].reverse()
    with pytest.raises(drill.DrillError):
        drill.validate_evidence(before, latest)


@pytest.mark.parametrize("case", ["grant", "expiry", "new_object", "missing_anchor", "regression"])
def test_unsupported_state_transitions_stop_before_replay(case):
    before, latest = multiple_evidence()
    if case == "grant":
        latest["access_events"][0].update(operation="set", permissions=["read", "admin"])
    elif case == "expiry":
        before["memberships"][1]["expires_at"] = "2030-01-01T00:00:00Z"
        latest["access_events"][0]["previous_expires_at"] = "2030-01-01T00:00:00Z"
    elif case == "new_object":
        latest["objects"].append(latest["objects"][0] | {"id": str(uuid5(NAMESPACE_URL, "new"))})
    elif case == "missing_anchor":
        before["objects"] = latest["objects"] = []
    else:
        before, latest = latest, before
    with pytest.raises(drill.DrillError):
        drill.validate_evidence(before, latest)


def test_public_api_target_limit_is_not_silently_split_into_new_receipts():
    before, latest = evidence()
    targets = [
        latest["deletion_targets"][0] | {"object_id": str(uuid5(NAMESPACE_URL, str(n)))}
        for n in range(101)
    ]
    latest["deletion_targets"] = targets
    latest["tombstones"] = [{k: t[k] for k in ("tenant_id", "object_id", "scope_id")}
                            for t in targets]
    latest["deletions"][0]["object_count"] = 101
    with pytest.raises(drill.DrillError, match="recovery_target_limit"):
        drill.validate_evidence(before, latest)


@pytest.mark.parametrize("case", ["overlap", "suppress"])
def test_valid_but_unsupported_receipt_sequences_are_rejected(case):
    before, latest = multiple_evidence()
    if case == "overlap":
        latest["deletion_targets"][1]["object_id"] = latest["deletion_targets"][0]["object_id"]
        latest["tombstones"].pop()
    else:
        latest["deletions"][1].update(mode="suppress", state="blocked_for_reads")
    with pytest.raises(drill.DrillError, match="recovery_history_unsupported"):
        drill.validate_evidence(before, latest)


def test_purge_suffix_does_not_require_an_unrelated_acl_change():
    before, latest = multiple_evidence()
    latest["memberships"] = deepcopy(before["memberships"])
    latest["access_events"] = []
    latest["tenant"][0]["access_epoch"] = before["tenant"][0]["access_epoch"]
    receipts, events, _ = drill.validate_evidence(before, latest)
    assert len(receipts) == 1 and events == []


@pytest.mark.parametrize("version", [1, 2])
def test_old_receipt_artifacts_are_not_reinterpreted_as_v3(version):
    before, latest = evidence()
    before["format"] = f"pgag-isolated-purge-drill-v{version}"
    with pytest.raises(drill.DrillError, match="unknown evidence format"):
        drill.validate_evidence(before, latest)
