"""Offline contracts for graph-only evidence; no database or performance qualification."""

import asyncio
import importlib.util
import json
import os
import subprocess
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from pg_agmemory.age_graph import AgeGraph
from pg_agmemory.database import VECTOR_VERSION, validate_runtime

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "examples/graph-resource-profile.json"
SPEC = importlib.util.spec_from_file_location(
    "graph_resource_benchmark", ROOT / "scripts/graph-resource-benchmark.py"
)
assert SPEC is not None and SPEC.loader is not None
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


@pytest.fixture
def profile():
    return bench.load_profile(PROFILE)


def complete_case(profile, index=0):
    samples = []
    for phase, count in (("warmup", 3), ("measured", 30)):
        for pair_index in range(count):
            order = ("sql", "age") if pair_index % 2 == 0 else ("age", "sql")
            for position, backend in enumerate(order):
                samples.append({
                    "backend": backend, "phase": phase, "pair_index": pair_index,
                    "position": position, "valid": True, "error": None,
                    "end_to_end_ms": 9000.0 if phase == "warmup" else 10.0 + pair_index,
                    "pair_equal": True, "oracle_equal": True, "result_digest": "a" * 64,
                })
    return {
        "id": profile["cases"][index]["id"], "setup": {"status": "passed"},
        "samples": samples, "probes_passed": True, "failures": [],
        "fixture": {"expected_digest": "a" * 64},
        "probes": [
            {
                "name": name, "backend": backend, "phase": "probe", "valid": True,
                "error": None, "oracle_equal": True, "result_digest": fingerprint * 64,
                "end_to_end_ms": 50.0,
            }
            for name, fingerprint in (
                ("current", "a"), ("historical", "b"), ("hidden_intermediate_path", "c"),
            ) for backend in ("sql", "age")
        ],
    }


def complete_report(profile):
    cases = [complete_case(profile, index) for index in range(6)]
    for case in cases:
        case["summary"] = bench.summarize_case(case, profile)
    return {
        "format": bench.FORMAT, "profile": profile, "profile_digest": bench.digest(profile),
        "cases": cases, "failures": [], "exact_commit_inputs": True, "mode": "baseline",
    }


def uid(index):
    return str(UUID(int=index))


def oracle_fixture():
    before, revised, now = (
        "2026-09-01T00:00:00Z", "2026-09-03T00:00:00Z", "2026-09-04T00:00:00Z"
    )
    nodes = [{
        "memory_id": uid(index), "revision": 1, "scope_id": uid(101 if index < 4 else 102),
        "recorded_at": before, "entity_type": "component", "canonical_label": f"Node {index}",
    } for index in range(1, 6)]

    def edge(index, source, target, revision=1, recorded_at=before):
        return {
            "assertion": {"memory_id": uid(index), "revision": revision},
            "source_entity": uid(source), "target_entity": uid(target),
            "predicate": "depends_on", "valid_from": before, "valid_to": None,
            "recorded_at": recorded_at, "epistemic_status": "reported",
        }

    graph = {
        "nodes": nodes,
        "edges": [
            [edge(201, 1, 2), edge(201, 1, 3, 2, revised)],
            [edge(202, 2, 3)], [edge(203, 1, 2)], [edge(204, 1, 4)],
            [edge(205, 4, 5)], [edge(206, 5, 3)],
        ],
        "readable_scopes": [uid(101)],
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
    }
    request = {
        "scope_ids": [uid(101), uid(102)], "seeds": [uid(1)],
        "relation_types": ["depends_on"], "purpose": "offline contract",
        "direction": "outgoing", "max_hops": 2, "max_paths": 100,
        "as_of": now, "known_at": now,
    }
    return graph, request


def measured(result, expected, **overrides):
    async def operation():
        return result

    return asyncio.run(bench.measured_read(
        operation, expected, **{
            "backend": "sql", "phase": "measured", "pair_index": 0, "position": 0,
            **overrides,
        },
    ))


def test_frozen_profile_has_six_bounded_native_strata(profile):
    assert profile["postgres_version_num"] == 180006
    assert VECTOR_VERSION == "0.8.6" and bench.validate_runtime is validate_runtime
    assert profile["name"] == "M4-bounded-native-graph-v3"
    assert profile["schema_version"] == 21 and profile["service_version"] == "0.3.0.dev1"
    assert profile["age_commit"] == "72707aab7ce982bf13cad3d102bd869dab07d64b"
    assert profile["resources"] == {
        "database": {"vcpus": 6, "memory_gib": 24},
        "application": {"vcpus": 2, "memory_gib": 8},
    }
    assert {(c["shape"], c["nodes_per_scope"]) for c in profile["cases"]} == {
        (shape, size) for shape in ("chain", "fanout", "multiseed") for size in (12, 64)
    }
    assert profile["sampling"]["warmup_pairs"] == 3
    assert profile["sampling"]["measured_pairs"] == 30
    assert profile["gate"]["end_to_end_p95_ms_exclusive"] == 1500
    assert profile["fixture"]["hidden_scope_matches_topology"] is True
    assert profile["fixture"]["readable_scopes_per_case"] == 1
    assert profile["fixture"]["scopes_per_case"] == 2
    assert profile["fixture"]["providers"] == 0


def test_pilot_profile_preserves_historical_workload_without_reclassifying_it(profile):
    historical = deepcopy(profile)
    historical.update(name="M3-bounded-native-graph-v1", service_version="0.1.3", schema_version=20)
    assert bench.digest(historical) == (
        "c89ed11ad1fc31038b2e168a56309c27d01521a627f2fed2e7b4ac6852fb2212"
    )
    assert bench.digest(profile) == (
        "44455f45baa2ed8b2c297c51457dc925b32dc8dd9e6df7e41e1459bcc1f33af9"
    )
    with pytest.raises(bench.BenchmarkError, match="profile_not_frozen"):
        bench.validate_profile(historical)


@pytest.mark.parametrize("section,key,value", [
    ("sampling", "measured_pairs", 29),
    ("gate", "end_to_end_p95_ms_exclusive", 1501),
    ("fixture", "max_paths", 99),
    ("fixture", "readable_scopes_per_case", 2),
])
def test_profile_rejects_recipe_changes(profile, section, key, value):
    profile[section][key] = value
    with pytest.raises(bench.BenchmarkError):
        bench.validate_profile(profile)


def test_profile_identity_cannot_be_redeclared_or_relaxed(profile, tmp_path):
    for section, key, value in (
        ("sampling", "order", "sql first for every pair"),
        ("fixture", "hidden_scope_matches_topology", 1),
        ("fixture", "tenant_per_case", True),
        ("database_settings", "plan", "disable JIT and increase shared buffers"),
        (None, "name", "locally relaxed profile"),
        (None, "permissions", {"reader": "owner"}),
    ):
        changed = deepcopy(profile)
        (changed if section is None else changed[section])[key] = value
        assert bench.digest(changed) != bench.digest(profile)
        path = tmp_path / "changed-profile.json"
        path.write_text(json.dumps(changed))
        with pytest.raises(bench.BenchmarkError):
            bench.load_profile(path)
    changed = deepcopy(profile)
    changed["cases"][0]["nodes_per_scope"] = 16
    with pytest.raises(bench.BenchmarkError):
        bench.validate_profile(changed)


def test_topologies_are_deterministic_and_match_declared_counts():
    for size in (12, 64):
        expected = {
            "chain": [(i, i + 1) for i in range(size - 1)],
            "fanout": [(0, i) for i in range(1, 5)] + [
                (hub, leaf) for hub in range(1, 5) for leaf in range(5, size)
            ],
            "multiseed": [(i, (i + d) % size) for i in range(size) for d in (1, 4)],
        }
        for shape, edges in expected.items():
            assert bench.topology(shape, size) == edges
            assert len(edges) == len(set(edges))
    for size in (True, 12.0, 0, 13, 65):
        with pytest.raises(bench.BenchmarkError):
            bench.topology("chain", size)
    with pytest.raises(bench.BenchmarkError):
        bench.topology("custom", 12)


def test_nearest_rank_and_strict_threshold_exclude_warmup(profile):
    assert bench.nearest_rank([]) is None
    assert bench.nearest_rank([500]) == 500
    assert bench.nearest_rank(list(range(30, 0, -1))) == 29
    assert bench.nearest_rank([1] * 29 + [9000]) == 1
    case = complete_case(profile)
    summary = bench.summarize_case(case, profile)
    assert summary["resource_qualified"] is True
    assert all(s["end_to_end_p95_ms"] == 38 for s in summary["strata"].values())
    for sample in case["samples"]:
        if sample["phase"] == "measured":
            sample["end_to_end_ms"] = 1499.9 if sample["pair_index"] < 28 else 1500
    summary = bench.summarize_case(case, profile)
    assert summary["resource_qualified"] is False
    assert all(s["end_to_end_p95_ms"] == 1500 for s in summary["strata"].values())
    assert all(s["p95_under_threshold"] is False for s in summary["strata"].values())


def test_invalid_numeric_inputs_do_not_become_fast_samples(profile):
    for invalid in (True, False, "1", None, float("nan"), float("inf"), -1):
        with pytest.raises((ValueError, bench.BenchmarkError)):
            bench.nearest_rank([invalid])
        for phase in ("warmup", "measured"):
            case = complete_case(profile)
            next(s for s in case["samples"] if s["phase"] == phase)["end_to_end_ms"] = invalid
            assert bench.summarize_case(case, profile)["resource_qualified"] is False


def test_complete_report_is_graph_only_not_m3_or_model_qualification(profile):
    report = complete_report(profile)
    summary = bench.summarize_report(report, profile)
    assert summary["resource_qualified"] is True
    assert summary["all_case_counts_settled"] is True
    assert summary["m3_qualified"] is False
    for case in report["cases"]:
        assert set(case["summary"]["strata"]) == {"sql", "age"}
        for stratum in case["summary"]["strata"].values():
            assert stratum["attempted"] == stratum["valid"] == 30
            assert stratum["warmup_attempted"] == 3
            assert stratum["errors"] == stratum["warmup_errors"] == 0
    for field in ("m2_qualified", "model_qualified", "full_s_qualified"):
        assert summary.get(field, False) is False


def test_diagnostic_or_nonexact_reports_never_qualify(profile):
    for mode, exact in (
        ("development", True), ("preflight", True), ("baseline", False),
        ("baseline", "true"), ("baseline", 1),
    ):
        report = complete_report(profile) | {"mode": mode, "exact_commit_inputs": exact}
        assert bench.summarize_report(report, profile)["resource_qualified"] is False


def test_pair_identity_order_and_completeness_are_required(profile):
    original = complete_case(profile)
    broken = []
    missing = deepcopy(original)
    missing["samples"].pop()
    broken.append(missing)
    duplicate = deepcopy(original)
    duplicate["samples"][-1]["pair_index"] = 28
    broken.append(duplicate)
    unpaired = deepcopy(original)
    unpaired["samples"][-1]["pair_equal"] = False
    broken.append(unpaired)
    unproven = deepcopy(original)
    del unproven["samples"][-1]["pair_equal"]
    broken.append(unproven)
    same_position = deepcopy(original)
    same_position["samples"][-1]["position"] = 0
    broken.append(same_position)
    no_alternation = deepcopy(original)
    for sample in no_alternation["samples"]:
        sample["position"] = 0 if sample["backend"] == "sql" else 1
    broken.append(no_alternation)
    reordered = deepcopy(original)
    reordered["samples"].reverse()
    broken.append(reordered)
    no_warmup = deepcopy(original)
    no_warmup["samples"] = [s for s in no_warmup["samples"] if s["phase"] == "measured"]
    broken.append(no_warmup)
    for index, case in enumerate(broken):
        assert bench.summarize_case(case, profile)["resource_qualified"] is False, index
    for indexes in ([0, 1, 2, 3, 4], [0, 1, 2, 3, 4, 4], [0, 1, 2, 4, 3, 5], []):
        report = complete_report(profile)
        report["cases"] = [report["cases"][index] for index in indexes]
        assert bench.summarize_report(report, profile)["resource_qualified"] is False


def test_failed_partial_reports_are_retained_without_success_defaults(profile, tmp_path):
    report = complete_report(profile)
    failed = report["cases"][2]
    failed["samples"] = failed["samples"][:9]
    failed["samples"][-1].update(
        valid=False, error={"type": "RuntimeError", "code": None, "sqlstate": None},
    )
    failed["failures"] = [{"phase": "measured", "code": "paired_read_failed"}]
    report["failures"] = [{"phase": "run", "code": "incomplete_run"}]
    bench.save_report(tmp_path, report, profile)
    path = tmp_path / "result.json"
    saved = json.loads(path.read_text())
    assert path.stat().st_mode & 0o777 == 0o600
    assert saved["resource_qualified"] is False and saved["m3_qualified"] is False
    assert saved["all_case_counts_settled"] is False
    assert saved["failures"] == report["failures"]
    assert saved["cases"][2]["samples"] == failed["samples"]
    assert saved["cases"][2]["failures"] == failed["failures"]
    strata = saved["cases"][2]["summary"]["strata"]
    assert sum(s["attempted"] for s in strata.values()) == 3
    assert sum(s["valid"] for s in strata.values()) == 2
    assert sum(s["errors"] for s in strata.values()) == 1


def test_setup_and_probe_failures_override_fast_samples(profile, tmp_path):
    for mutation in (
        {"setup": {"status": "failed"}}, {"setup": {"status": "running"}},
        {"probes_passed": False}, {"failures": [{"code": "oracle_mismatch"}]},
    ):
        case = complete_case(profile) | mutation
        assert bench.summarize_case(case, profile)["resource_qualified"] is False
    report = complete_report(profile)
    report["cases"][0]["samples"].clear()
    assert bench.summarize_report(report, profile)["resource_qualified"] is False
    bench.save_report(tmp_path, report, profile)
    assert json.loads((tmp_path / "result.json").read_text())["resource_qualified"] is False


@pytest.mark.parametrize("mutation", [
    "missing_probes", "missing_probe", "probe_error", "probe_mismatch",
    "sample_oracle_false", "sample_digest_mismatch", "missing_expected_digest",
])
def test_raw_probes_and_digests_override_stale_success_flags(profile, mutation):
    case = complete_case(profile)
    if mutation == "missing_probes":
        del case["probes"]
    elif mutation == "missing_probe":
        case["probes"].pop()
    elif mutation == "probe_error":
        case["probes"][0]["error"] = {"code": "oracle_mismatch"}
    elif mutation == "probe_mismatch":
        case["probes"][1]["result_digest"] = "d" * 64
    elif mutation == "sample_oracle_false":
        case["samples"][6]["oracle_equal"] = False
    elif mutation == "sample_digest_mismatch":
        case["samples"][7]["result_digest"] = "d" * 64
    else:
        del case["fixture"]["expected_digest"]
    assert case["probes_passed"] and all(sample["valid"] for sample in case["samples"])
    assert not bench.summarize_case(case, profile)["resource_qualified"]


def test_independent_oracle_checks_revisions_order_coverage_and_hidden_paths():
    graph, request = oracle_fixture()
    expected = bench.expected_graph(graph, request)
    assert [p["nodes"] for p in expected["paths"]] == [
        [uid(1), uid(3)], [uid(1), uid(2)], [uid(1), uid(2), uid(3)],
    ]
    assert [p["assertions"] for p in expected["paths"]] == [
        [{"memory_id": uid(201), "revision": 2}],
        [{"memory_id": uid(203), "revision": 1}],
        [{"memory_id": uid(203), "revision": 1}, {"memory_id": uid(202), "revision": 1}],
    ]
    assert [n["memory_id"] for n in expected["nodes"]] == [uid(1), uid(2), uid(3)]
    assert expected["coverage"] == {
        "max_hops": 2, "truncated": False, "complete_within_bounds": True,
    }
    bounded = bench.expected_graph(graph, request | {"max_paths": 2})
    assert bounded["paths"] == expected["paths"][:2]
    assert bounded["coverage"]["truncated"] is True
    assert bounded["coverage"]["complete_within_bounds"] is False
    historical = bench.expected_graph(graph, request | {"known_at": "2026-09-02T00:00:00Z"})
    assert historical["paths"][0]["assertions"][0]["revision"] == 1
    assert historical["paths"][0]["nodes"] == [uid(1), uid(2)]
    for seeds in ([uid(4)], [uid(5)]):
        hidden = bench.expected_graph(graph, request | {"seeds": seeds})
        assert hidden["nodes"] == hidden["edges"] == hidden["paths"] == []
        assert hidden["empty_reason"] == "not_found"


def test_result_mismatches_cannot_qualify_even_with_good_latency(profile):
    graph, request = oracle_fixture()
    expected = bench.expected_graph(graph, request)
    good = expected | {"backend": "sql", "projection_watermark": None}
    assert measured(good, expected)["valid"] is True
    changes = []
    revision = deepcopy(good)
    revision["edges"][0]["assertion"]["revision"] = 1
    changes.append(revision)
    identifiers = deepcopy(good)
    identifiers["nodes"][0]["memory_id"] = uid(999)
    changes.append(identifiers)
    changes.append(good | {"paths": list(reversed(good["paths"]))})
    changes.append(good | {"coverage": good["coverage"] | {"truncated": True}})
    changes.append(good | {"backend": "age"})
    changes.append(good | {"projection_watermark": uid(900)})
    for changed in changes:
        record = measured(changed, expected)
        assert record["valid"] is False and record["error"]["code"] == "oracle_mismatch"
        case = complete_case(profile)
        record["end_to_end_ms"] = 1.0
        case["samples"][6] = record
        assert bench.summarize_case(case, profile)["resource_qualified"] is False
    native = good | {"backend": "age", "projection_watermark": uid(900)}
    assert measured(native, expected, backend="age", expected_watermark=uid(900))["valid"]
    assert not measured(native, expected, backend="age", expected_watermark=uid(901))["valid"]


def test_query_and_lifecycle_timing_uses_native_adapter_and_restores_hooks(monkeypatch):
    assert bench.AgeGraph is AgeGraph
    graph, request = oracle_fixture()
    expected = bench.expected_graph(graph, request)
    events = []
    clock = iter(i / 1000 for i in range(200))
    monkeypatch.setattr(bench, "perf_counter", lambda: next(clock))

    async def raw_cursor(cursor, query, *args, **kwargs):
        events.append(query)
        return cursor

    async def raw_execute(conn, query, *args, **kwargs):
        return await bench.psycopg.AsyncCursor.execute(object(), query)

    async def enter(transaction):
        events.append("begin")
        return transaction

    async def exit_(transaction, *args):
        events.append("commit")

    monkeypatch.setattr(bench.psycopg.AsyncConnection, "execute", raw_execute)
    monkeypatch.setattr(bench.psycopg.AsyncCursor, "execute", raw_cursor)
    monkeypatch.setattr(bench.AsyncTransaction, "__aenter__", enter)
    monkeypatch.setattr(bench.AsyncTransaction, "__aexit__", exit_)

    class Transaction:
        async def __aenter__(self):
            return await bench.AsyncTransaction.__aenter__(self)

        async def __aexit__(self, *args):
            return await bench.AsyncTransaction.__aexit__(self, *args)

    class Connection:
        transaction = Transaction

        async def execute(self, query, *args, **kwargs):
            return await bench.psycopg.AsyncConnection.execute(self, query, *args, **kwargs)

    @asynccontextmanager
    async def principal(url, subject):
        events.append("connect")
        conn = Connection()
        await conn.execute("SELECT pg_advisory_lock(1)")
        yield conn, object()
        events.append("close")

    async def bind(*args):
        events.append("bind")

    class Native:
        def __init__(self, memory):
            self.memory = memory

        async def expand(self, request):
            await self.memory.conn.execute("projected_nodes AS MATERIALIZED")
            await self.memory.conn.execute("SELECT ag_catalog.cypher(")
            return expected | {"backend": "age", "projection_watermark": uid(900)}

    monkeypatch.setattr(bench, "principal_connection", principal)
    monkeypatch.setattr(bench, "bind_identity", bind)
    monkeypatch.setattr(bench, "MemoryService", lambda conn, identity: SimpleNamespace(conn=conn))
    monkeypatch.setattr(bench, "AgeGraph", Native)
    monkeypatch.setattr(bench, "SqlGraph", Mock(side_effect=AssertionError("SQL fallback")))

    async def operation():
        return await bench.graph_read("unused", "reader", "age", request)

    with bench.instrumentation():
        record = asyncio.run(bench.measured_read(
            operation, expected, backend="age", phase="measured", pair_index=0,
            position=1, expected_watermark=uid(900),
        ))
    assert record["valid"] is True, record
    assert events == [
        "connect", "SELECT pg_advisory_lock(1)", "begin", "SET TRANSACTION READ ONLY",
        "bind", "projected_nodes AS MATERIALIZED", "SELECT ag_catalog.cypher(", "commit", "close",
    ]
    assert record["query_count"] == record["sql_cursor_count"] == 4
    assert record["completeness_query_count"] == record["native_path_count"] == 1
    assert record["sql_execute_ms"] == sum(q["elapsed_ms"] for q in record["queries"])
    assert record["end_to_end_ms"] > record["transaction_span_ms"] > 0
    assert record["completeness_query_ms"] > 0 and record["native_path_ms"] > 0
    assert 0 < record["sql_cursor_ms"] < record["sql_execute_ms"]
    assert bench.ACTIVE.get() is None
    assert bench.psycopg.AsyncConnection.execute is raw_execute
    assert bench.psycopg.AsyncCursor.execute is raw_cursor
    assert bench.AsyncTransaction.__aenter__ is enter
    assert bench.AsyncTransaction.__aexit__ is exit_


def test_errors_are_code_only_and_retained_without_credentials(profile, tmp_path):
    private = "postgresql://owner:DO_NOT_REPORT@private.invalid/hidden"

    async def fail():
        raise RuntimeError(private)

    record = asyncio.run(bench.measured_read(
        fail, {}, backend="age", phase="measured", pair_index=0, position=1,
    ))
    assert record["valid"] is False
    assert bench.ACTIVE.get() is None
    assert record["error"] == {"type": "RuntimeError", "code": None, "sqlstate": None}
    assert record["end_to_end_ms"] >= 0
    assert bench.sanitized_error(bench.BenchmarkError("oracle_mismatch"))["code"] == (
        "oracle_mismatch"
    )
    assert bench.sanitized_error(bench.BenchmarkError(private))["code"] is None

    class PrivateCredentialError(Exception):
        code = "private_credential"
        sqlstate = "S3CRT"

        def __str__(self):
            raise AssertionError("error text must not be evaluated")

    assert bench.sanitized_error(PrivateCredentialError()) == {
        "type": "Exception", "code": None, "sqlstate": None,
    }
    canceled = bench.sanitized_error(bench.psycopg.errors.QueryCanceled(private))
    assert canceled["type"] == "QueryCanceled" and canceled["sqlstate"] == "57014"
    assert private not in json.dumps(canceled)
    report = complete_report(profile)
    report["cases"][0]["samples"][7] = record
    bench.save_report(tmp_path, report, profile)
    content = (tmp_path / "result.json").read_text()
    assert private not in content and "DO_NOT_REPORT" not in content
    assert json.loads(content)["resource_qualified"] is False


def test_publisher_records_actual_artifact_counts_and_cleans_only_owned_file(monkeypatch, tmp_path):
    sentinel = tmp_path / "artifact-unrelated.json"
    sentinel.write_text("must remain")
    operations, artifacts = [], []

    def cli(arguments, *, request=None):
        operations.append((arguments, request))
        if arguments == ["graph-generation"]:
            return {"current_input_digest": "a" * 64}
        if arguments[:2] == ["graph-artifact", "export"]:
            artifact = Path(arguments[arguments.index("--file") + 1])
            artifact.write_text('{"fixture":"actual exported bytes"}')
            artifacts.append(artifact)
            return {"artifact_digest": "b" * 64, "node_count": 24, "edge_revision_count": 24}
        assert arguments[:2] == ["age-projection", "publish"]
        return {"serving_enabled": True, "artifact_verified": True}

    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(bench, "cli", cli)
    monkeypatch.setattr(bench.psycopg, "connect", Mock(return_value=connection))
    phases = {}
    generation = bench.publish("unused", uid(100), "c" * 64, tmp_path, phases)
    assert UUID(generation)
    assert phases["node_count"] == phases["edge_revision_count"] == 24
    assert phases["artifact_bytes"] == len(b'{"fixture":"actual exported bytes"}')
    assert all(phases[key] >= 0 for key in ("begin_ms", "export_ms", "record_ms", "publish_ms"))
    assert [args[:2] for args, _ in operations] == [
        ["graph-generation"], ["graph-generation"], ["graph-artifact", "export"],
        ["graph-generation"], ["age-projection", "publish"],
    ]
    assert not artifacts[0].exists() and sentinel.read_text() == "must remain"
    connection.execute.assert_called_once_with("ANALYZE")

    def failed_cli(arguments, *, request=None):
        result = cli(arguments, request=request)
        if arguments[:2] == ["age-projection", "publish"]:
            raise bench.BenchmarkError("publication_failed")
        return result

    monkeypatch.setattr(bench, "cli", failed_cli)
    with pytest.raises(bench.BenchmarkError, match="publication_failed"):
        bench.publish("unused", uid(100), "c" * 64, tmp_path, {})
    assert all(not path.exists() for path in artifacts) and sentinel.exists()


def test_native_environment_rejects_label_owner_and_bypass_before_measurement(monkeypatch):
    validate = AsyncMock()
    native = AsyncMock()
    monkeypatch.setattr(bench, "validate_runtime", validate)
    monkeypatch.setattr(bench, "validate_age_runtime", native)
    for field in ("rolsuper", "rolbypassrls", "owns_tables"):
        conn = Mock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        cursor = Mock()
        cursor.fetchone = AsyncMock(return_value={
            "current_user": "benchmark", "rolsuper": False,
            "rolbypassrls": False, "owns_tables": False, field: True,
        })
        conn.execute = AsyncMock(return_value=cursor)
        monkeypatch.setattr(bench.psycopg.AsyncConnection, "connect", AsyncMock(return_value=conn))
        with pytest.raises(bench.BenchmarkError, match="runtime_role_invalid"):
            asyncio.run(bench.environment("unused", "runtime", native_published=True))
        conn.execute.assert_awaited_once()
    assert validate.await_count == native.await_count == 3


def test_footprint_keeps_database_wal_and_heap_index_sizes_distinct(monkeypatch):
    totals = {"database_bytes": 10000, "wal_directory_bytes": 16000, "wal_lsn": "0/1234"}
    relations = [
        {"schema": "memory", "relation": "entity", "heap_bytes": 100, "index_bytes": 50,
         "total_bytes": 180, "relrowsecurity": True, "relforcerowsecurity": True},
        {"schema": "pgag_age_" + "a" * 32, "relation": "Node",
         "heap_bytes": 200, "index_bytes": 70, "total_bytes": 300,
         "relrowsecurity": True, "relforcerowsecurity": True},
    ]
    cursor = Mock()
    cursor.fetchone.return_value = deepcopy(totals)
    cursor.fetchall.return_value = deepcopy(relations)
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.execute.return_value = cursor
    monkeypatch.setattr(bench.psycopg, "connect", Mock(return_value=conn))
    footprint = bench.footprint("unused")
    assert {key: footprint[key] for key in totals} == totals
    assert footprint["relations"] == relations
    assert footprint["graph_heap_bytes"] == 200
    assert footprint["graph_index_bytes"] == 70
    assert footprint["graph_total_bytes"] == 300
    assert conn.execute.call_count == 2


def test_main_exit_and_persisted_identity_distinguish_diagnostic_and_failed_runs(
    profile, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(bench.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(bench.platform, "system", lambda: "Linux")
    for mode in ("baseline", "development", "preflight", "failed"):
        directory = tmp_path / mode
        directory.mkdir()
        (directory / "build-identity.json").write_text(json.dumps({
            "exact_commit_inputs": True, "implementation_sha": "a" * 40,
        }))
        arguments = [
            "graph-resource-benchmark.py", "--directory", str(directory),
            "--profile", str(PROFILE),
        ]
        if mode in ("development", "preflight"):
            arguments.append("--" + mode)
        monkeypatch.setattr("sys.argv", arguments)

        async def run(args, frozen, report, mode=mode):
            report["cases"] = complete_report(frozen)["cases"]
            if mode == "failed":
                report["cases"] = report["cases"][:2]
                raise RuntimeError("private connection password must not appear")

        monkeypatch.setattr(bench, "run", run)
        with pytest.raises(SystemExit) as exit_:
            bench.main()
        assert exit_.value.code == (0 if mode == "baseline" else 1)
        saved = json.loads((directory / "result.json").read_text())
        assert saved["resource_qualified"] is (mode == "baseline")
        assert saved["m3_qualified"] is False
        assert saved["exact_commit_inputs"] is (mode not in ("development", "preflight"))
        assert saved["profile_digest"] == bench.digest(profile)
        if mode == "failed":
            assert len(saved["cases"]) == 2 and saved["failures"]
            assert "private connection password" not in json.dumps(saved)
        assert json.loads(capsys.readouterr().out)["resource_qualified"] is (mode == "baseline")


def test_helper_rejects_invalid_modes_engines_and_external_paths(tmp_path):
    script = ROOT / "scripts/measure-graph-resources.sh"
    help_result = subprocess.run(
        ["bash", str(script), "--help"], capture_output=True, text=True, check=False,
    )
    assert help_result.returncode == 0
    assert "development" in help_result.stdout and "preflight" in help_result.stdout
    for arguments in (
        [], ["evidence", "--qualify"], ["evidence", "--development", "podman"],
        [str(tmp_path / "absolute"), "--development"], ["../escape", "--development"],
    ):
        result = subprocess.run(
            ["bash", str(script), *arguments], capture_output=True, text=True, check=False,
        )
        assert result.returncode == 2


@pytest.mark.parametrize("field,value", [("__version__", "0.2.0"), ("SCHEMA_VERSION", 20)])
def test_runtime_version_mismatch_rejects_before_database_access(
    profile, tmp_path, monkeypatch, field, value,
):
    monkeypatch.setattr(bench, field, value)
    monkeypatch.delenv("PGAG_ADMIN_DATABASE_URL", raising=False)
    with pytest.raises(bench.BenchmarkError, match="^profile_versions$"):
        asyncio.run(bench.run(SimpleNamespace(directory=tmp_path), profile, {}))


def test_unexpected_interruption_cannot_leave_a_success_report(profile, tmp_path, monkeypatch):
    class UnexpectedFailure(Exception):
        pass

    monkeypatch.setattr(bench.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(bench.platform, "system", lambda: "Linux")
    (tmp_path / "build-identity.json").write_text(json.dumps({"exact_commit_inputs": True}))
    monkeypatch.setattr("sys.argv", [
        "graph-resource-benchmark.py", "--directory", str(tmp_path), "--profile", str(PROFILE),
    ])

    async def incomplete_run(args, frozen, report):
        report["cases"] = complete_report(frozen)["cases"]
        raise UnexpectedFailure("private diagnostic")

    monkeypatch.setattr(bench, "run", incomplete_run)
    with pytest.raises(UnexpectedFailure):
        bench.main()
    saved = json.loads((tmp_path / "result.json").read_text())
    assert not saved["resource_qualified"] and not saved["m3_qualified"]
    assert saved["failures"] == [{"phase": "environment_or_run", "code": "run_not_completed"}]
    assert "private diagnostic" not in json.dumps(saved)


@pytest.mark.parametrize("engine", ["container", "docker"])
def test_helper_pins_resources_and_cleans_only_owned_resources(tmp_path, engine):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "examples").mkdir()
    helper = project / "scripts/measure-graph-resources.sh"
    helper.write_bytes((ROOT / "scripts/measure-graph-resources.sh").read_bytes())
    (project / "examples/graph-resource-profile.json").write_bytes(PROFILE.read_bytes())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "engine.jsonl"
    stub = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
if name == "git":
    if args[:1] == ["rev-parse"]:
        print("a" * 40)
    raise SystemExit(0)
if name == "jq":
    sys.stdin.read()
    print("192.0.2.7" if "-er" in args else "[]")
    raise SystemExit(0)
safe = ["REDACTED" if "PASSWORD=" in arg or "DATABASE_URL=" in arg else arg for arg in args]
with pathlib.Path(os.environ["TEST_ENGINE_LOG"]).open("a") as stream:
    stream.write(json.dumps([name, *safe]) + "\\n")
if "inspect" in args:
    print("[]")
elif args[:1] == ["exec"] and "python" in args:
    raise SystemExit(37)
elif args[:1] == ["run"]:
    print("owned-container")
"""
    for name in ("container", "docker", "jq", "git", "rm"):
        path = bin_dir / name
        path.write_text(stub)
        path.chmod(0o700)
    output = project / "evidence"
    sentinel = tmp_path / "not-owned.txt"
    sentinel.write_text("keep")
    result = subprocess.run(
        ["bash", str(helper), str(output.relative_to(project)), "--development", engine],
        env=os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}", "TEST_ENGINE_LOG": str(log),
            "PGAG_AGE_PATCHED_IMAGE": "cached-pinned-age:test",
        },
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 37, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    runs = [args for _, *args in calls if args[:1] == ["run"]]
    readiness = [args for _, *args in calls if "pg_isready" in args]
    assert readiness and all(
        args[args.index("-h") + 1] == "127.0.0.1" for args in readiness
    )
    assert len(runs) == 2
    db, app = runs
    assert db[db.index("--cpus") + 1] == "6" and db[db.index("--memory") + 1] == "24g"
    assert app[app.index("--cpus") + 1] == "2" and app[app.index("--memory") + 1] == "8g"
    assert "shared_preload_libraries=age" in db
    assert any("graph-resource-benchmark.py:" in arg and arg.endswith(":ro") for arg in app)
    created = {args[args.index("--name") + 1] for args in runs}
    removed = [args[-1] for _, *args in calls if args[:1] == ["rm"]]
    assert set(removed) == created and len(removed) == len(created)
    assert not any(name == "rm" for name, *_ in calls)
    images = [args[-1] for _, *args in calls if args[:2] == ["image", "rm"]]
    assert len(images) == 1 and images[0].startswith("pg-agmemory-runtime:")
    assert "cached-pinned-age:test" not in images
    assert sentinel.read_text() == "keep"
    assert output.stat().st_mode & 0o777 == 0o700
    report = json.loads((output / "result.json").read_text())
    assert report["resource_qualified"] is False and report["m3_qualified"] is False
    assert report["exact_commit_inputs"] is False
    assert report["failures"] and report["cases"] == []
    assert (output / "result.json").stat().st_mode & 0o777 == 0o600
    cleanup = json.loads((output / "cleanup.json").read_text())
    assert cleanup["exit_status"] == 37 and cleanup["owned_container_cleanup_succeeded"]
