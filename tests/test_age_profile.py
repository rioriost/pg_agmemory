"""Safety/identity checks for the opt-in disposable AGE qualification harness."""

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("age_probe", ROOT / "scripts" / "smoke-age.py")
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("hops", [0, 3, -1, 100, True, 1.0, "2", None])
def test_traversal_bound_cannot_be_relaxed_or_interpolated(hops):
    with pytest.raises(ValueError, match="one or two hops"):
        probe.traversal(hops=hops)


@pytest.mark.parametrize("hops", [1, 2])
def test_traversal_is_bounded_and_values_can_be_parameterized(hops):
    query = probe.traversal(hops=hops, parameters=True)
    assert f"[:LINK*1..{hops}]" in query
    assert "key: $root" in query
    assert query.endswith("ORDER BY n.key LIMIT 32")
    statement = probe.cypher_statement(query, parameters=True).as_string()
    assert ", %s::ag_catalog.agtype)" in statement
    assert "'age_qualification'" in statement
    assert "$root" in statement


def test_cypher_requires_fixed_dollar_quoted_text_without_delimiter():
    statement = probe.cypher_statement("RETURN 1").as_string()
    assert "$pgag_age$RETURN 1$pgag_age$" in statement
    with pytest.raises(ValueError, match="SQL delimiter"):
        probe.cypher_statement("RETURN '$pgag_age$'")


@pytest.mark.parametrize("direction,pattern", [
    ("out", "-[e:LINK]->"), ("in", "<-[e:LINK]-"), ("both", "-[e:LINK]-"),
])
def test_fixed_candidate_templates_never_use_variable_length_syntax(direction, pattern):
    query = probe.fixed_one_hop(direction=direction)
    assert pattern in query
    assert "*" not in query
    assert "s:Node {key: $root}" in query
    assert "(n:Node)" in query
    assert query.endswith("ORDER BY e.key, n.key LIMIT 32")


def test_fixed_candidate_rejects_non_allowlisted_direction():
    with pytest.raises(ValueError, match="only out, in, or both"):
        probe.fixed_one_hop(direction="out*1..2")


def test_host_bfs_uses_actual_visible_frontiers_and_stops_at_two_hops():
    connection = Mock()
    connection.execute.return_value.fetchall.side_effect = [
        [('["root", "good_edge", "good"]',)],
        [('["good", "leaf_edge", "leaf"]',)],
    ]
    assert probe.fixed_host_bfs(connection) == ["good", "leaf"]
    calls = connection.execute.call_args_list
    assert len(calls) == 2
    assert [json.loads(call.args[1][0]) for call in calls] == [
        {"root": "root"}, {"root": "good"},
    ]
    assert all(call.kwargs["prepare"] is True for call in calls)
    assert all("*" not in call.args[0].as_string() for call in calls)


def test_host_bfs_rejects_inconsistent_returned_source():
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [
        ('["hidden", "edge", "target"]',),
    ]
    with pytest.raises(RuntimeError, match="different source"):
        probe.fixed_host_bfs(connection)


def test_fixed_candidate_success_does_not_override_native_vle_failure(monkeypatch, capsys):
    monkeypatch.setenv("PGAG_AGE_DISPOSABLE", "1")
    monkeypatch.setenv("PGAG_AGE_ADMIN_DATABASE_URL", "unused")
    monkeypatch.setattr(probe, "probe", Mock(return_value={
        "qualified": False, "native_vle_qualified": False,
        "fixed_template_candidate": {"checks_passed": True, "adapter_qualified": False},
    }))
    assert probe.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["fixed_template_candidate"]["checks_passed"] is True
    assert result["qualified"] is False


def test_pins_are_exact_and_rc_identity_is_not_hidden():
    dockerfile = (ROOT / "Dockerfile.age").read_text()
    assert dockerfile.count(probe.ARTIFACT["base_image"]) == 2
    for field in ("tag", "commit", "archive_sha256", "archive_url"):
        assert probe.ARTIFACT[field] in dockerfile
    assert probe.ARTIFACT["tag"].endswith("-rc0")
    assert "sha256sum --check --strict" in dockerfile
    assert "ARG " not in dockerfile
    assert 'CMD ["postgres", "-c", "shared_preload_libraries=age"]' in dockerfile


def test_fixtures_include_hidden_intermediate_vertex_edge_and_other_tenant():
    vertices = {key: (tenant, visible) for key, tenant, visible in probe.VERTICES}
    assert vertices["hidden"] == ("alpha", False)
    assert vertices["via_hidden"] == ("alpha", True)
    assert vertices["foreign"] == ("beta", True)
    assert ("root", "hidden", "into_hidden", "alpha", True) in probe.EDGES
    assert ("hidden", "via_hidden", "out_of_hidden", "alpha", True) in probe.EDGES
    assert ("root", "edge_target", "hidden_edge", "alpha", False) in probe.EDGES
    assert ("root", "foreign_edge_target", "foreign_edge", "beta", True) in probe.EDGES


def test_record_reports_mismatch_without_filtering_hidden_result():
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [('"good"',), ('"via_hidden"',)]
    checks = []
    probe.record(connection, checks, "rls", "SELECT", ["good"], agtype=True)
    assert checks == [{
        "name": "rls", "expected": ["good"], "actual": ["good", "via_hidden"], "passed": False,
    }]


def test_record_reports_sql_failure_and_continues():
    connection = Mock()
    connection.execute.side_effect = psycopg.errors.InsufficientPrivilege("denied")
    checks = []
    probe.record(connection, checks, "rls", "SELECT", [])
    assert checks[0]["passed"] is False
    assert checks[0]["sqlstate"] == "42501"


def test_setup_refuses_existing_cluster_role_without_creating_extension():
    connection = Mock()
    connection.execute.return_value.fetchone.side_effect = [
        (probe.DATABASE, 180006, "age", "PostgreSQL 18.6"), (True,),
    ]
    with pytest.raises(RuntimeError, match="non-fresh"):
        probe.setup(connection, "not-used")
    assert connection.execute.call_count == 2


@pytest.mark.parametrize("database,version,preload", [
    ("postgres", 180006, "age"),
    (probe.DATABASE, 180005, "age"),
    (probe.DATABASE, 180006, ""),
])
def test_setup_refuses_wrong_database_version_or_missing_preload(database, version, preload):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = (
        database, version, preload, "version",
    )
    with pytest.raises(RuntimeError, match="dedicated PG18.6"):
        probe.setup(connection, "not-used")
    assert connection.execute.call_count == 1


def test_main_requires_explicit_disposable_opt_in(monkeypatch, capsys):
    monkeypatch.delenv("PGAG_AGE_DISPOSABLE", raising=False)
    monkeypatch.setenv("PGAG_AGE_ADMIN_DATABASE_URL", "must-not-connect")
    assert probe.main() == 2
    assert json.loads(capsys.readouterr().out)["qualified"] is False


def test_main_does_not_print_credentials_on_connection_failure(monkeypatch, capsys):
    monkeypatch.setenv("PGAG_AGE_DISPOSABLE", "1")
    monkeypatch.setenv("PGAG_AGE_ADMIN_DATABASE_URL", "postgresql://secret@invalid/db")
    monkeypatch.setattr(probe, "probe", Mock(side_effect=ValueError("secret@invalid")))
    assert probe.main() == 2
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["error_type"] == "ValueError"


@pytest.mark.parametrize("rm_status,rmdir_status,original_status,expected", [
    (0, 0, 0, 0), (1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 7, 7),
])
def test_cleanup_only_removes_named_owned_artifacts(
    rm_status, rmdir_status, original_status, expected,
):
    text = (ROOT / "scripts" / "test-age-containers.sh").read_text()
    cleanup = text.split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    completed = subprocess.run(
        ["bash", "-c", f"""
directory=synthetic-age
containers=()
network_created=false
age_built=false
test_built=false
database_started=false
unset PGAG_AGE_EVIDENCE_DIRECTORY
rm() {{ printf 'rm-argument:%s\\n' "$@"; return {rm_status}; }}
rmdir() {{ printf 'rmdir-argument:%s\\n' "$@"; return {rmdir_status}; }}
cleanup() {{{cleanup}
(exit {original_status})
cleanup
"""],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == expected
    if original_status:
        assert completed.stdout == ""
        assert "failed evidence retained" in completed.stderr
    else:
        assert completed.stdout.splitlines() == [
            "rm-argument:-f", "rm-argument:--",
            "rm-argument:./synthetic-age/build.log", "rm-argument:./synthetic-age/tests.log",
            "rm-argument:./synthetic-age/postgres.log", "rm-argument:./synthetic-age/report.json",
            "rmdir-argument:--", "rmdir-argument:./synthetic-age",
        ]
