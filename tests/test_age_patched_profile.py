"""Identity and unchanged-probe contracts for the separate patched AGE image."""

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "patched_age_probe", ROOT / "scripts/smoke-age-patched.py",
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def original_report(*, passed=True, native_count=19, fixed_count=40):
    return {
        "qualified": passed,
        "native_vle_qualified": passed,
        "production_enabled": False,
        "checks": [{"name": f"native_{i}", "passed": passed} for i in range(native_count)],
        "fixed_template_candidate": {
            "checks_passed": True, "adapter_qualified": False,
            "checks": [{"name": f"fixed_{i}", "passed": True} for i in range(fixed_count)],
        },
        "constraints": [
            "Pinned upstream tag literally includes rc0; no stable-release claim.",
            "Synthetic RLS property policy is a probe, not pg_agmemory authorization.",
        ],
    }


def test_public_base_and_both_patch_commits_are_explicit():
    artifact = probe.ARTIFACT
    assert artifact["upstream_base_commit"] == "fa109ef1ddb1c7a945a1c340195d650000e49713"
    assert artifact["commit"] == "72707aab7ce982bf13cad3d102bd869dab07d64b"
    assert artifact["patch_commits"] == [
        "bfd99df0c62bf4678c3673d199d9306f07d5eb85",
        "72707aab7ce982bf13cad3d102bd869dab07d64b",
    ]
    assert artifact["archive_url"] == (
        "https://codeload.github.com/apache/age/tar.gz/" + artifact["upstream_base_commit"]
    )
    assert artifact["age_control_version"] == "1.8.0"
    assert artifact["postgres_version_num"] == 180006
    assert artifact["vector_version"] == "0.8.6"
    assert artifact["source_tree_sha256_format"] == "sha256(git ls-tree -r -z <source_tree>)"


def test_patch_and_manifest_hashes_are_build_enforced():
    dockerfile = (ROOT / "Dockerfile.age-patched").read_text()
    assert hashlib.sha256((ROOT / probe.ARTIFACT["patch_file"]).read_bytes()).hexdigest() == (
        probe.ARTIFACT["patch_sha256"]
    )
    manifest_hash = hashlib.sha256((ROOT / "patches/age/source.json").read_bytes()).hexdigest()
    assert manifest_hash in dockerfile
    for field in (
        "upstream_base_commit", "upstream_base_tree", "commit", "source_tree",
        "archive_url", "archive_sha256", "patch_sha256", "source_tree_sha256",
    ):
        assert probe.ARTIFACT[field] in dockerfile
    assert dockerfile.count(probe.ARTIFACT["base_image"]) == 2
    assert "git apply --check" in dockerfile
    assert "git apply --index --whitespace=nowarn" in dockerfile
    assert dockerfile.count("git write-tree") == 2
    assert dockerfile.count("sha256sum --check --strict") == 6
    assert probe.MANIFEST_PATH in dockerfile
    assert "install -D -m 644 LICENSE" in dockerfile
    assert "install -D -m 644 NOTICE" in dockerfile
    assert "../age" not in dockerfile
    assert "ARG " not in dockerfile
    assert 'CMD ["postgres", "-c", "shared_preload_libraries=age"]' in dockerfile


def test_distribution_stamp_is_constant_sql_matching_source_manifest():
    stamp = (ROOT / probe.ARTIFACT["distribution_stamp_file"]).read_text()
    assert hashlib.sha256(stamp.encode()).hexdigest() == probe.ARTIFACT[
        "distribution_stamp_sha256"
    ]
    constant = stamp.split("RETURN '", 1)[1].split("'::pg_catalog.jsonb;", 1)[0]
    assert json.loads(constant) == probe.SQL_IDENTITY
    assert "CREATE FUNCTION ag_catalog.pgag_age_build()" in stamp
    assert "LANGUAGE sql\nIMMUTABLE\nSECURITY INVOKER\nRETURN" in stamp
    assert "SECURITY DEFINER" not in stamp
    assert "GRANT" not in stamp
    dockerfile = (ROOT / "Dockerfile.age-patched").read_text()
    assert probe.ARTIFACT["distribution_stamp_sha256"] in dockerfile
    assert dockerfile.index("git write-tree") < dockerfile.index("make -j2")
    assert dockerfile.index("make -j2") < dockerfile.index(
        "cat ../build-stamp.sql >> age--1.8.0.sql"
    ) < dockerfile.index("make install")


def test_preload_diagnostic_is_optional_fixed_setting_with_restricted_search_path():
    diagnostic = (ROOT / probe.ARTIFACT["preload_diagnostic_file"]).read_text()
    assert hashlib.sha256(diagnostic.encode()).hexdigest() == probe.ARTIFACT[
        "preload_diagnostic_sha256"
    ]
    assert "CREATE FUNCTION ag_catalog.pgag_age_preloaded()" in diagnostic
    assert "RETURNS pg_catalog.bool\nLANGUAGE sql\nSTABLE\nSECURITY DEFINER" in diagnostic
    assert "SET search_path = pg_catalog" in diagnostic
    assert "current_setting('shared_preload_libraries')" in diagnostic
    assert "btrim(name) = 'age'" in diagnostic
    assert "REVOKE ALL ON FUNCTION ag_catalog.pgag_age_preloaded() FROM PUBLIC;" in diagnostic
    assert "GRANT EXECUTE ON FUNCTION ag_catalog.pgag_age_preloaded() TO PUBLIC;" in diagnostic
    assert "not a data or RLS authorization bypass" in diagnostic
    assert "pg_read_all_settings" not in diagnostic
    dockerfile = (ROOT / "Dockerfile.age-patched").read_text()
    assert probe.ARTIFACT["preload_diagnostic_sha256"] in dockerfile
    assert dockerfile.index("make -j2") < dockerfile.index(
        "cat ../preload-diagnostic.sql >> age--1.8.0.sql"
    ) < dockerfile.index("make install")


def test_combined_patch_contains_only_six_tracked_source_changes():
    patch = (ROOT / probe.ARTIFACT["patch_file"]).read_text()
    paths = [
        line.split(" b/", 1)[1] for line in patch.splitlines() if line.startswith("diff --git ")
    ]
    assert paths == [
        "Makefile",
        "regress/expected/rls_vle.out",
        "regress/sql/rls_vle.sql",
        "src/backend/utils/adt/age_global_graph.c",
        "src/backend/utils/adt/age_vle.c",
        "src/include/utils/age_global_graph.h",
    ]
    assert "tools/rls-vle" not in patch


def test_wrapper_imports_original_test_logic_without_copying():
    assert Path(probe.original.probe.__code__.co_filename) == ROOT / "scripts/smoke-age.py"
    assert probe.original.ARTIFACT is probe.ARTIFACT
    assert probe.original.INPUTS == probe.INPUTS
    assert "scripts/smoke-age.py" in probe.INPUTS
    assert probe.ARTIFACT["patch_file"] in probe.INPUTS
    assert "patches/age/source.json" in probe.INPUTS
    assert probe.original.DATABASE == "pgag_age_qualification"
    assert probe.original.traversal() == (
        "MATCH (s:Node {key: 'root'})-[:LINK*1..2]->(n:Node) "
        "RETURN DISTINCT n.key ORDER BY n.key LIMIT 32"
    )


def test_historical_probe_and_artifact_remain_separate():
    spec = importlib.util.spec_from_file_location("historical_age", ROOT / "scripts/smoke-age.py")
    historical = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(historical)
    assert historical.ARTIFACT["tag"] == "PG18/v1.8.0-rc0"
    assert historical.ARTIFACT["commit"] == "e43dc1a12b78fba4acef9835b2b10379b8d243b4"
    assert historical.INPUTS[0] == "Dockerfile.age"


@pytest.mark.parametrize("passed", [True, False])
def test_all_original_results_are_preserved_without_production_approval(monkeypatch, passed):
    original = original_report(passed=passed)
    native_checks = original["checks"]
    fixed_checks = original["fixed_template_candidate"]["checks"]
    monkeypatch.setattr(probe, "installed_identity", Mock(return_value=probe.ARTIFACT))
    run_original = Mock(return_value=original)
    monkeypatch.setattr(probe.original, "probe", run_original)
    report = probe.probe("synthetic")
    run_original.assert_called_once_with("synthetic")
    assert report["qualified"] is passed
    assert report["native_vle_qualified"] is passed
    assert report["checks"] is native_checks
    assert report["fixed_template_candidate"]["checks"] is fixed_checks
    assert report["fixed_template_candidate"]["adapter_qualified"] is False
    assert report["production_enabled"] is False
    assert report["original_checks_complete"] is True
    assert report["installed_build_identity"] == probe.ARTIFACT
    assert "Synthetic RLS property policy" in " ".join(report["constraints"])
    assert "whole-M3 production approval" in " ".join(report["constraints"])
    assert not any("Pinned upstream tag literally" in item for item in report["constraints"])


@pytest.mark.parametrize("native_count,fixed_count", [(18, 40), (19, 39), (0, 0), (20, 40)])
def test_missing_or_changed_original_check_set_cannot_qualify(
    monkeypatch, native_count, fixed_count,
):
    monkeypatch.setattr(probe, "installed_identity", Mock(return_value=probe.ARTIFACT))
    monkeypatch.setattr(probe.original, "probe", Mock(return_value=original_report(
        native_count=native_count, fixed_count=fixed_count,
    )))
    report = probe.probe("synthetic")
    assert report["qualified"] is False
    assert report["original_checks_complete"] is False


@pytest.mark.parametrize("installed", [None, {}, {"commit": "rc0"}])
def test_wrong_installed_identity_is_rejected_before_original_probe(monkeypatch, installed):
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.return_value = (
        None if installed is None else (installed,)
    )
    monkeypatch.setattr(probe.psycopg, "connect", Mock(return_value=connection))
    run_original = Mock()
    monkeypatch.setattr(probe.original, "probe", run_original)
    with pytest.raises(RuntimeError, match="build identity"):
        probe.probe("synthetic")
    run_original.assert_not_called()


def test_installed_identity_uses_only_admin_file_read(monkeypatch):
    connection = MagicMock()
    admin = connection.__enter__.return_value
    admin.execute.return_value.fetchone.return_value = (probe.ARTIFACT,)
    monkeypatch.setattr(probe.psycopg, "connect", Mock(return_value=connection))
    assert probe.installed_identity("synthetic") == probe.ARTIFACT
    admin.execute.assert_called_once_with(
        "SELECT pg_read_file(%s)::jsonb", (probe.MANIFEST_PATH,),
    )


@pytest.mark.parametrize("row", [
    None,
    ({}, False, "i", "sql"),
    (probe.SQL_IDENTITY, True, "i", "sql"),
    (probe.SQL_IDENTITY, False, "v", "sql"),
    (probe.SQL_IDENTITY, False, "i", "plpgsql"),
])
def test_mismatched_or_privileged_sql_stamp_fails_closed(monkeypatch, row):
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.return_value = row
    monkeypatch.setattr(probe.psycopg, "connect", Mock(return_value=connection))
    with pytest.raises(RuntimeError, match="SQL build stamp"):
        probe.sql_build_stamp("host=example dbname=qualification user=postgres", "synthetic")


def test_sql_stamp_is_read_using_restricted_authenticated_runtime_login(monkeypatch):
    connection = MagicMock()
    runtime = connection.__enter__.return_value
    runtime.execute.return_value.fetchone.return_value = (probe.SQL_IDENTITY, False, "i", "sql")
    connect = Mock(return_value=connection)
    monkeypatch.setattr(probe.psycopg, "connect", connect)
    assert probe.sql_build_stamp(
        "host=example dbname=qualification user=postgres", "synthetic",
    ) == probe.SQL_IDENTITY
    used_dsn = probe.conninfo_to_dict(connect.call_args.args[0])
    assert used_dsn["user"] == "pgag_age_reader"
    assert used_dsn["password"] == "synthetic"
    assert used_dsn["dbname"] == "qualification"
    assert runtime.execute.call_args_list[0].args == ("SET ROLE pgag_runtime",)
    assert "SELECT ag_catalog.pgag_age_build()" in runtime.execute.call_args_list[1].args[0]


def test_setup_preserves_original_setup_and_adds_runtime_sql_stamp(monkeypatch):
    admin = Mock()
    admin.info.dsn = "synthetic"
    original_setup = Mock(return_value={"extensions": {"age": "1.8.0"}})
    stamp = Mock(return_value=probe.SQL_IDENTITY)
    diagnostic = Mock(return_value={**probe.PRELOAD_METADATA, "preloaded": True})
    monkeypatch.setattr(probe, "ORIGINAL_SETUP", original_setup)
    monkeypatch.setattr(probe, "sql_build_stamp", stamp)
    monkeypatch.setattr(probe, "sql_preload_diagnostic", diagnostic)
    runtime = probe.setup(admin, "synthetic-password")
    original_setup.assert_called_once_with(admin, "synthetic-password")
    stamp.assert_called_once_with("synthetic", "synthetic-password")
    diagnostic.assert_called_once_with("synthetic", "synthetic-password")
    assert runtime["extensions"] == {"age": "1.8.0"}
    assert runtime["sql_build_stamp"] == probe.SQL_IDENTITY
    assert runtime["preload_diagnostic"]["preloaded"] is True


@pytest.mark.parametrize("field,value", [
    ("security_definer", False),
    ("volatility", "i"),
    ("config", None),
    ("config", ["search_path=public, pg_catalog"]),
    ("arguments", 1),
    ("returns_boolean", False),
    ("language", "plpgsql"),
    ("superuser_owner", False),
    ("extension_owned", False),
    ("runtime_owner_member", True),
    ("login_owner_member", True),
    ("safe_acl", False),
    ("runtime_settings_reader", True),
    ("login_settings_reader", True),
    ("runtime_schema_create", True),
])
def test_preload_diagnostic_rejects_bad_definition_or_owner_before_call(
    monkeypatch, field, value,
):
    connection = MagicMock()
    runtime = connection.__enter__.return_value
    metadata = {**probe.PRELOAD_METADATA, field: value}
    runtime.execute.return_value.fetchone.return_value = tuple(metadata.values())
    monkeypatch.setattr(probe.psycopg, "connect", Mock(return_value=connection))
    with pytest.raises(RuntimeError, match="definition or owner permissions"):
        probe.sql_preload_diagnostic("host=example user=postgres", "synthetic")
    assert runtime.execute.call_count == 2
    assert all(
        call.args[0] != "SELECT ag_catalog.pgag_age_preloaded()"
        for call in runtime.execute.call_args_list
    )


@pytest.mark.parametrize("result", [None, (False,), (None,)])
def test_preload_diagnostic_rejects_missing_confirmation(monkeypatch, result):
    connection = MagicMock()
    runtime = connection.__enter__.return_value
    runtime.execute.return_value.fetchone.side_effect = [
        tuple(probe.PRELOAD_METADATA.values()), result,
    ]
    monkeypatch.setattr(probe.psycopg, "connect", Mock(return_value=connection))
    with pytest.raises(RuntimeError, match="did not confirm"):
        probe.sql_preload_diagnostic("host=example user=postgres", "synthetic")


def test_preload_diagnostic_is_verified_from_restricted_login_without_settings_grant(monkeypatch):
    connection = MagicMock()
    runtime = connection.__enter__.return_value
    runtime.execute.return_value.fetchone.side_effect = [
        tuple(probe.PRELOAD_METADATA.values()), (True,),
    ]
    connect = Mock(return_value=connection)
    monkeypatch.setattr(probe.psycopg, "connect", connect)
    assert probe.sql_preload_diagnostic("host=example user=postgres", "synthetic") == {
        **probe.PRELOAD_METADATA, "preloaded": True,
    }
    assert probe.conninfo_to_dict(connect.call_args.args[0])["user"] == "pgag_age_reader"
    assert runtime.execute.call_args_list[0].args == ("SET ROLE pgag_runtime",)
    assert runtime.execute.call_args_list[2].args == ("SELECT ag_catalog.pgag_age_preloaded()",)
    statements = " ".join(call.args[0] for call in runtime.execute.call_args_list)
    assert "GRANT" not in statements
    assert "current_setting" not in statements
    assert "pg_catalog.pg_depend" in statements


def test_main_requires_disposable_opt_in(monkeypatch, capsys):
    monkeypatch.delenv("PGAG_AGE_DISPOSABLE", raising=False)
    monkeypatch.setenv("PGAG_AGE_ADMIN_DATABASE_URL", "must-not-connect")
    run_probe = Mock()
    monkeypatch.setattr(probe, "probe", run_probe)
    assert probe.main() == 2
    assert json.loads(capsys.readouterr().out)["qualified"] is False
    run_probe.assert_not_called()


@pytest.mark.parametrize("passed,exit_code", [(True, 0), (False, 1)])
def test_main_preserves_qualification_failure_exit(monkeypatch, capsys, passed, exit_code):
    monkeypatch.setenv("PGAG_AGE_DISPOSABLE", "1")
    monkeypatch.setenv("PGAG_AGE_ADMIN_DATABASE_URL", "synthetic")
    monkeypatch.setattr(probe, "probe", Mock(return_value=original_report(passed=passed)))
    assert probe.main() == exit_code
    assert json.loads(capsys.readouterr().out)["qualified"] is passed


def test_main_redacts_identity_or_connection_error(monkeypatch, capsys):
    monkeypatch.setenv("PGAG_AGE_DISPOSABLE", "1")
    monkeypatch.setenv("PGAG_AGE_ADMIN_DATABASE_URL", "postgresql://secret@invalid/db")
    monkeypatch.setattr(probe, "probe", Mock(side_effect=RuntimeError("secret@invalid")))
    assert probe.main() == 2
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["error_type"] == "RuntimeError"


def test_helper_retains_patched_image_and_runs_both_contract_suites():
    text = (ROOT / "scripts/test-age-patched-containers.sh").read_text()
    assert 'image rm "$age_image"' not in text
    assert 'image rm "$test_image"' in text
    assert 'build --file Dockerfile.age-patched --tag "$age_image"' in text
    assert "/work/tests/test_age_profile.py" in text
    assert "/work/tests/test_age_patched_profile.py" in text
    assert 'cmp patches/age/source.json "$directory/installed-source.json"' in text
    assert 'exit "$status"' in text
    assert 'PGAG_AGE_TEST_IMAGE' in text
    assert 'PGAG_AGE_EVIDENCE_DIRECTORY' in text


@pytest.mark.parametrize("original_status", [0, 1, 2, 7])
def test_helper_cleanup_preserves_exit_and_all_evidence(original_status):
    text = (ROOT / "scripts/test-age-patched-containers.sh").read_text()
    cleanup = text.split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    completed = subprocess.run(
        ["bash", "-c", f"""
directory=synthetic-patched-age
age_image=synthetic-patched-image
containers=()
network_created=false
test_built=false
database_started=false
rm() {{ echo unexpected-rm; return 1; }}
cleanup() {{{cleanup}
(exit {original_status})
cleanup
"""],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == original_status
    assert completed.stdout == ""
    assert "evidence retained: synthetic-patched-age" in completed.stderr
    assert "image retained: synthetic-patched-image" in completed.stderr
