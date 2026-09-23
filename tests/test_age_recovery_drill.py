"""Offline contracts for the bounded fixture and destructive harness boundaries."""

import hashlib
import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "age_recovery_drill", ROOT / "scripts" / "smoke-age-recovery.py",
)
assert SPEC is not None and SPEC.loader is not None
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)


@pytest.mark.parametrize("version", ["0.1.3", "0.2.1"])
def test_main_rejects_nonrelease_service_versions_before_reading_private_state(
    monkeypatch, capsys, version,
):
    monkeypatch.setattr(drill.sys, "platform", "linux")
    monkeypatch.setattr(drill.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(drill.pg_agmemory, "__version__", version)
    monkeypatch.delenv("PGAG_AGE_RECOVERY_DIRECTORY", raising=False)
    with pytest.raises(SystemExit) as failure:
        drill.main()
    assert failure.value.code == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "error": "service_0_2_0_required",
    }


def test_main_accepts_current_release_and_runs_selected_phase(monkeypatch, capsys, tmp_path):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(drill.sys, "platform", "linux")
    monkeypatch.setattr(drill.sys, "argv", [str(SPEC.origin), "seed"])
    monkeypatch.setattr(drill.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("PGAG_AGE_RECOVERY_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("PGAG_AGE_RECOVERY_BUILD_IDENTITY", json.dumps({
        "files_sha256": {
            "scripts/smoke-age-recovery.py": hashlib.sha256(
                Path(drill.__file__).read_bytes()
            ).hexdigest(),
        },
    }))

    async def seed(directory):
        assert directory == tmp_path.resolve()
        return {"status": "passed", "service_version": drill.pg_agmemory.__version__}

    monkeypatch.setattr(drill, "seed", seed)
    drill.main()
    assert json.loads(capsys.readouterr().out) == {
        "status": "passed", "service_version": "0.2.0",
    }


def fixture_evidence():
    fixture = {"format": drill.FORMAT, "nodes": ["a", "b", "c", "d"],
               "purged": ["source", "d", "branch"]}
    before = {
        "objects": [{"id": identifier} for identifier in ("source", "d", "branch", "control")],
        "generations": [{"id": "old", "state": "recorded"}],
        "registry": [{"enabled": True, "revision": 1}],
        "tombstones": [], "provenance": [{"id": "purged"}, {"id": "retained"}],
        "canonical": [{"table": str(index), "rows": 0, "digest": "a" * 64}
                      for index in range(35)],
        "processing": {"tables": [
            {"table": table, "rows": 0, "digest": "b" * 64} for table in drill.ACCOUNTING
        ]},
    }
    latest = deepcopy(before)
    latest["tombstones"] = [{"object_id": identifier} for identifier in fixture["purged"]]
    latest["provenance"] = [{"id": "retained"}]
    return fixture, before, latest


def test_fixture_requires_purge_history_without_changing_canonical_ids_or_old_generation():
    drill.validate_fixture(*fixture_evidence())


@pytest.mark.parametrize("field", ["objects", "generations", "registry", "tombstones",
                                  "canonical", "provenance"])
def test_missing_or_changed_source_evidence_is_not_guessed(field):
    fixture, before, latest = fixture_evidence()
    latest[field] = []
    with pytest.raises(drill.DrillError):
        drill.validate_fixture(fixture, before, latest)


@pytest.mark.parametrize("field,value", [("enabled", False), ("revision", 2)])
def test_both_snapshots_must_have_the_same_enabled_revision_one(field, value):
    fixture, before, latest = fixture_evidence()
    before["registry"][0][field] = latest["registry"][0][field] = value
    with pytest.raises(drill.DrillError, match="enabled_revision_one_required"):
        drill.validate_fixture(fixture, before, latest)


@pytest.mark.parametrize("table", drill.ACCOUNTING)
def test_accounting_fingerprints_are_exact_even_for_empty_tables(table):
    fixture, before, latest = fixture_evidence()
    next(row for row in latest["processing"]["tables"] if row["table"] == table)["digest"] = (
        "c" * 64
    )
    with pytest.raises(drill.DrillError, match="accounting_changed"):
        drill.validate_fixture(fixture, before, latest)


@pytest.mark.parametrize("table", ["memory_ops.model_call", "memory_ops.job"])
def test_fixture_never_claims_no_calls_when_jobs_or_calls_exist(table):
    fixture, before, latest = fixture_evidence()
    for state in (before, latest):
        next(row for row in state["processing"]["tables"] if row["table"] == table)["rows"] = 1
    with pytest.raises(drill.DrillError, match="unexpected_model_or_worker_activity"):
        drill.validate_fixture(fixture, before, latest)


@pytest.mark.parametrize("rm_status,rmdir_status,original_status,expected", [
    (0, 0, 0, 0), (1, 0, 0, 1), (0, 1, 0, 1), (1, 1, 7, 7),
])
def test_cleanup_deletes_only_named_private_files_and_preserves_reports(
    rm_status, rmdir_status, original_status, expected,
):
    text = (ROOT / "scripts" / "test-age-recovery-containers.sh").read_text()
    cleanup = text.split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    result = subprocess.run(["bash", "-c", f"""
directory=owned-drill
containers=()
images=()
network_created=false
rm() {{ printf 'rm:%s\\n' "$@"; return {rm_status}; }}
rmdir() {{ printf 'rmdir:%s\\n' "$@"; return {rmdir_status}; }}
cleanup() {{{cleanup}
(exit {original_status})
cleanup
"""], capture_output=True, text=True, check=False)
    assert result.returncode == expected
    arguments = result.stdout.splitlines()
    assert arguments[:2] == ["rm:-f", "rm:--"]
    assert arguments[-2:] == ["rmdir:--", "rmdir:owned-drill/private"]
    deleted = arguments[2:-2]
    assert len(deleted) == 15
    assert all(item.startswith("rm:owned-drill/private/") for item in deleted)
    assert "rm:owned-drill/private/old.dump" in deleted
    assert "rm:owned-drill/private/bundle.json" in deleted
    assert "rm:owned-drill/private/roles.sql" in deleted
    assert "rm:owned-drill/report.json" not in deleted


def test_source_removal_precedes_fresh_restore_and_only_smoke_is_mounted():
    text = (ROOT / "scripts" / "test-age-recovery-containers.sh").read_text()
    removed = text.index('source_removed=true\n')
    fresh = text.index('start_database "$restore_db"')
    restore = text.index('pg_restore -U postgres')
    apply = text.index('phase recover "$(database_host "$restore_db")"')
    assert removed < fresh < restore < apply
    assert '--target runtime' in text
    assert '/app/src' not in text and '$PWD:/work' not in text
    assert '--exit-on-error --single-transaction' in text
    assert 'rm --force --volumes' in text
    assert 'pg_dump -U postgres' in text
    for exclusion in (
        "--exclude-extension=age", "--exclude-schema=ag_catalog", "--exclude-schema='pgag_age_*'",
    ):
        assert exclusion in text
    assert text.index('phase manifest "$source_host"') < removed
    assert restore < text.index('> "$directory/fresh-extension.log"') < apply
    assert "GRANT USAGE ON SCHEMA ag_catalog TO pgag_runtime;" in text


def archive_manifest():
    return "\n".join([
        "; Archive metadata only; database name pgag_age_recovery is not a schema entry",
        "1; 0 0 EXTENSION - vector",
        *[f"{index}; 0 0 TABLE DATA {schema} {table} postgres"
          for index, (schema, table) in enumerate((
            ("memory", "tenant"), ("memory", "episode"), ("memory", "relation_revision"),
            ("memory_ops", "age_projection"), ("memory_ops", "graph_generation"),
            ("memory_ops", "graph_generation_state"), ("memory_ops", "recovery_key"),
            ("public", "pgag_schema_migration"),
        ), 2)],
    ]) + "\n"


def test_archive_evidence_explicitly_disclaims_full_age_catalog_recovery():
    result = drill.validate_archive_manifest(archive_manifest())
    assert result["backup_mode"] == "canonical-only-projection-discard"
    assert result["dump_exclusions"] == [
        "--exclude-extension=age", "--exclude-schema=ag_catalog", "--exclude-schema=pgag_age_*",
    ]
    assert result["archive_projection_entries"] == 0
    assert result["full_age_catalog_restore_qualified"] is False
    assert result["prior_full_age_rebuild_failure"] == "ag_graph_graphid_index"
    assert result["extension_recreated_from_trusted_image"] is True


@pytest.mark.parametrize("entry", [
    "80; 0 0 EXTENSION - age postgres", "80; 0 0 COMMENT - EXTENSION age postgres",
    "80; 0 0 TABLE DATA ag_catalog ag_graph postgres",
    "80; 0 0 SCHEMA - pgag_age_0123 postgres",
    "80; 0 0 TABLE DATA pgag_age_0123 Node postgres",
])
def test_projection_or_extension_entry_is_never_silently_allowed(entry):
    with pytest.raises(drill.DrillError, match="projection_not_excluded_from_archive"):
        drill.validate_archive_manifest(archive_manifest() + entry + "\n")


@pytest.mark.parametrize("table", [
    "memory tenant", "memory_ops age_projection", "memory_ops recovery_key",
    "memory_ops graph_generation", "public pgag_schema_migration",
])
def test_archive_must_retain_authoritative_registry_and_hmac_key_rows(table):
    text = "\n".join(line for line in archive_manifest().splitlines()
                     if f" TABLE DATA {table} " not in line)
    with pytest.raises(drill.DrillError, match="canonical_table_missing_from_archive"):
        drill.validate_archive_manifest(text)
