"""Opt-in fixed-hop candidate checks, not native AGE or lifecycle qualification."""

import asyncio
import importlib.util
import json
import math
import os
import platform
import statistics
import sys
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import psycopg
import pytest
import test_graph_conformance as oracle
from psycopg import sql
from psycopg.transaction import AsyncTransaction

from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import ExpandGraph, GraphResult
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PGAG_TEST_AGE_GRAPH") != "1",
        reason="PGAG_TEST_AGE_GRAPH=1 requires a fresh, dedicated preloaded AGE cluster",
    ),
]
BACKEND = "age-fixed-hop-candidate"
ROOT = Path(__file__).resolve().parents[1]
WARMUP_PAIRS = 3
MEASURED_PAIRS = 30


@pytest.fixture(scope="session")
def candidate(database):
    spec = importlib.util.spec_from_file_location(
        "age_graph_candidate", ROOT / "scripts" / "age_graph_candidate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    admin_url, runtime_url, _ = database
    with psycopg.connect(admin_url) as conn:
        assert conn.execute("SHOW server_version_num").fetchone()[0] == "180006"
        assert "age" in conn.execute("SHOW shared_preload_libraries").fetchone()[0].split(",")
        assert (
            conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
            == "0.8.6"
        )
        assert (
            conn.execute("SELECT extversion FROM pg_extension WHERE extname='age'").fetchone()
            is not None
        )
    with psycopg.connect(runtime_url) as conn:
        assert conn.execute(
            """SELECT NOT rolsuper AND NOT rolbypassrls
               FROM pg_roles WHERE rolname=current_user"""
        ).fetchone()[0]
        assert conn.execute(
            """SELECT NOT EXISTS (
                   SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname IN ('memory','memory_ops')
                     AND pg_has_role(current_user,c.relowner,'MEMBER'))"""
        ).fetchone()[0]
        assert conn.execute("SELECT current_user").fetchone()[0] != "postgres"
    return module


@contextmanager
def projection(candidate, env):
    name = "pgag_m3_" + uuid4().hex
    metadata = candidate.build_projection(env.admin_url, name)
    try:
        yield name, metadata
    finally:
        candidate.drop_projection(env.admin_url, name)


async def sql_read(env, data):
    async with principal_connection(env.settings.database_url, env.subjects[0]) as (conn, identity):
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await bind_identity(conn, env.subjects[0], identity)
            result = await SqlGraph(MemoryService(conn, identity)).expand(data)
    return GraphResult.model_validate(result).model_dump(mode="json")


def candidate_read(candidate, env, request, name, strategy="fixed"):
    return candidate.expand_candidate(
        env.settings.database_url,
        env.subjects[0],
        ExpandGraph.model_validate(request),
        name,
        strategy=strategy,
    )


class CandidateGraph(oracle.GraphFixture):
    def __init__(self, env, candidate):
        super().__init__(env)
        self.candidate = candidate

    def expand(self, request):
        with projection(self.candidate, self.env) as (name, _):
            result = asyncio.run(candidate_read(self.candidate, self.env, request, name))
        assert result["backend"] == BACKEND
        assert result["projection_watermark"] is None
        # Freeze independently captured defaults only for the comparison SQL read.
        sql_request = {
            **request,
            "as_of": request.get("as_of") or result["as_of"],
            "known_at": request.get("known_at") or result["known_at"],
        }
        normalized = {**result, "backend": "sql"}
        assert normalized == super().expand(sql_request)
        return normalized


@pytest.fixture
def canonical_graph(env, candidate):
    return CandidateGraph(env, candidate)


# Reuse all 17 independent oracle cases and their parametrization without a second BFS.
test_multiseed_parallel_cycles_and_global_prefix_budgets = (
    oracle.test_multiseed_parallel_cycles_and_global_prefix_budgets
)
test_second_hop_target_revision_and_half_open_times = (
    oracle.test_second_hop_target_revision_and_half_open_times
)
test_scope_seed_filters_and_historical_current_acl = (
    oracle.test_scope_seed_filters_and_historical_current_acl
)
test_retained_payload_cannot_bridge_or_leak_at_historical_time = (
    oracle.test_retained_payload_cannot_bridge_or_leak_at_historical_time
)
test_default_time_capture_and_isolated_loop_only_seeds = (
    oracle.test_default_time_capture_and_isolated_loop_only_seeds
)


def small_graph(env):
    graph = oracle.GraphFixture(env)
    graph.node("root")
    graph.node("leaf")
    graph.edge("link", "root", "leaf")
    return graph, graph.request(["root"])


def test_native_vle_rejected_without_running_native_traversal(env, candidate, monkeypatch):
    graph, request = small_graph(env)
    assert graph.expand(request)["paths"]

    def unexpected_connection(*args, **kwargs):
        pytest.fail("Unqualified native strategy must be rejected before connecting")

    monkeypatch.setattr(candidate, "principal_connection", unexpected_connection)
    with pytest.raises(MemoryError, match="graph_backend_unqualified") as denied:
        asyncio.run(candidate_read(candidate, env, request, "pgag_m3_" + uuid4().hex, "native_vle"))
    assert denied.value.status == 409


@pytest.mark.parametrize("failure", ["missing_projection", "revoked_projection_read"])
def test_candidate_errors_never_silently_fall_back_to_sql(env, candidate, failure):
    graph, request = small_graph(env)
    assert graph.expand(request)["paths"]
    with projection(candidate, env) as (name, _):
        healthy = asyncio.run(candidate_read(candidate, env, request, name))
        oracle.assert_graph_contract(healthy, graph, request, backend=BACKEND)
        if failure == "missing_projection":
            name = "pgag_m3_" + uuid4().hex
        else:
            with psycopg.connect(env.admin_url) as conn:
                conn.execute(
                    sql.SQL("REVOKE SELECT ON {} FROM pgag_runtime").format(
                        sql.Identifier(name, "LINK")
                    )
                )
        error = (
            MemoryError if failure == "missing_projection" else psycopg.errors.InsufficientPrivilege
        )
        with pytest.raises(error) as denied:
            asyncio.run(candidate_read(candidate, env, request, name))
        if failure == "missing_projection":
            assert denied.value.code == "graph_projection_invalid"
            assert denied.value.status == 409
    assert graph.expand(request)["paths"]


def measured_read(monkeypatch, operation):
    """Measure client wall time, explicit transaction spans and issued SQL calls."""
    queries, transactions, opened = [], [], {}
    execute = psycopg.AsyncCursor.execute
    enter, exit_ = AsyncTransaction.__aenter__, AsyncTransaction.__aexit__

    async def timed_execute(cursor, query, *args, **kwargs):
        text = (
            query.as_string(cursor.connection) if isinstance(query, sql.Composable) else str(query)
        )
        record = {"kind": "age_candidate" if "cypher(" in text else "canonical_or_setup"}
        started = perf_counter()
        try:
            return await execute(cursor, query, *args, **kwargs)
        except Exception as exc:
            record["error"] = type(exc).__name__
            raise
        finally:
            record["elapsed_ms"] = (perf_counter() - started) * 1000
            queries.append(record)

    async def timed_enter(transaction):
        opened[id(transaction)] = perf_counter()
        return await enter(transaction)

    async def timed_exit(transaction, *args):
        try:
            return await exit_(transaction, *args)
        finally:
            transactions.append((perf_counter() - opened.pop(id(transaction))) * 1000)

    result = None
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.AsyncCursor, "execute", timed_execute)
        patch.setattr(AsyncTransaction, "__aenter__", timed_enter)
        patch.setattr(AsyncTransaction, "__aexit__", timed_exit)
        started = perf_counter()
        record = {}
        try:
            result = asyncio.run(operation())
        except Exception as exc:
            record["error"] = {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "sqlstate": getattr(exc, "sqlstate", None),
                "message": str(exc)[:500],
            }
        record["request_ms"] = (perf_counter() - started) * 1000
    record.update(
        transaction_ms=sum(transactions),
        transaction_spans_ms=transactions,
        query_ms=sum(row["elapsed_ms"] for row in queries),
        max_query_ms=max((row["elapsed_ms"] for row in queries), default=0),
        query_count=len(queries),
        candidate_query_count=sum(row["kind"] == "age_candidate" for row in queries),
        queries=queries,
    )
    return result, record


def distribution(values):
    values = sorted(values)
    return {
        "count": len(values),
        "median": statistics.median(values) if values else None,
        "p95": values[math.ceil(len(values) * 0.95) - 1] if values else None,
    }


def cost_graph(env, shape):
    graph = oracle.GraphFixture(env)
    if shape == "chain":
        for index in range(12):
            graph.node(f"n{index}")
        for index in range(11):
            graph.edge(f"e{index}", f"n{index}", f"n{index + 1}")
        seeds, cap = ["n0"], 100
    elif shape == "fanout":
        graph.node("root")
        for index in range(12):
            graph.node(f"branch{index}")
            graph.node(f"leaf{index}")
            graph.edge(f"first{index}", "root", f"branch{index}")
            graph.edge(f"second{index}", f"branch{index}", f"leaf{index}")
        seeds, cap = ["root"], 20
    else:
        assert shape == "multi_seed"
        for prefix, count in (("root", 4), ("middle", 4), ("leaf", 6)):
            for index in range(count):
                graph.node(f"{prefix}{index}")
        for root in range(4):
            for middle in range(4):
                graph.edge(f"r{root}m{middle}", f"root{root}", f"middle{middle}")
        for middle in range(4):
            for leaf in range(6):
                graph.edge(f"m{middle}l{leaf}", f"middle{middle}", f"leaf{leaf}")
        seeds, cap = [f"root{index}" for index in reversed(range(4))], 40
    return graph, graph.request(seeds, max_paths=cap)


@pytest.mark.skipif(
    os.environ.get("PGAG_TEST_AGE_GRAPH_COST") != "1",
    reason="PGAG_TEST_AGE_GRAPH_COST=1 enables the bounded paired experiment",
)
@pytest.mark.parametrize("shape", ["chain", "fanout", "multi_seed"])
def test_paired_cost_same_canonical_graph_and_authority(env, candidate, monkeypatch, shape):
    directory = os.environ.get("PGAG_AGE_GRAPH_EVIDENCE")
    assert directory and Path(directory).is_dir(), "Set an existing private evidence directory"
    destination = Path(directory) / f"cost-{shape}.json"
    report = {
        "shape": shape,
        "warmup_pairs": WARMUP_PAIRS,
        "measured_pairs": MEASURED_PAIRS,
        "measurement": {
            "request_ms": "async loop + connection + identity + read + serialization + close",
            "transaction_ms": "sum of explicit psycopg transaction context elapsed times",
            "query_ms": "sum of AsyncCursor.execute elapsed; excludes implicit BEGIN/COMMIT",
            "statement_timeout": "5s per statement, not a total request deadline",
            "p95": "nearest-rank, successful measured requests only",
            "order": "alternating SQL-first and candidate-first pairs; no concurrency",
            "projection": "rebuilt once before reads; no generation/liveness/restore claim",
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "visible_cpus": os.cpu_count(),
            "postgres": "18.6",
            "pgvector": "0.8.6",
            "age_tag": "PG18/v1.8.0-rc0",
            "configured_statement_timeout_ms": 5000,
        },
        "samples": [],
    }
    try:
        started = perf_counter()
        graph, request = cost_graph(env, shape)
        report["canonical_setup_ms"] = (perf_counter() - started) * 1000
        report["fixture"] = {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "request": request,
        }
        expected = {
            "backend": "sql",
            "projection_watermark": None,
            **oracle.expected_graph(graph, request),
        }
        data = ExpandGraph.model_validate(request)
        started = perf_counter()
        with projection(candidate, env) as (name, metadata):
            report["projection_rebuild_ms"] = (perf_counter() - started) * 1000
            report["projection_metadata"] = metadata
            with psycopg.connect(env.admin_url) as conn:
                report["projection_bytes"] = conn.execute(
                    """SELECT coalesce(sum(pg_total_relation_size(c.oid)),0)::bigint
                       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                       WHERE n.nspname=%s AND c.relkind IN ('r','m')""",
                    (name,),
                ).fetchone()[0]
            for pair in range(WARMUP_PAIRS + MEASURED_PAIRS):
                order = ["sql", "candidate"] if pair % 2 == 0 else ["candidate", "sql"]
                for backend in order:
                    operation = (
                        (lambda: sql_read(env, data))
                        if backend == "sql"
                        else (lambda: candidate_read(candidate, env, request, name))
                    )
                    result, sample = measured_read(monkeypatch, operation)
                    sample.update(backend=backend, pair=pair, warmup=pair < WARMUP_PAIRS)
                    if "error" not in sample:
                        wanted = {**expected, "backend": "sql" if backend == "sql" else BACKEND}
                        if result != wanted:
                            sample["error"] = {"type": "ConformanceMismatch"}
                            sample["actual_result"] = result
                    report["samples"].append(sample)
        report["summaries"] = {}
        for backend in ("sql", "candidate"):
            measured = [
                row for row in report["samples"] if row["backend"] == backend and not row["warmup"]
            ]
            good = [row for row in measured if "error" not in row]
            report["summaries"][backend] = {
                "requests": len(measured),
                "failures": len(measured) - len(good),
                **{
                    metric: distribution([row[metric] for row in good])
                    for metric in (
                        "request_ms",
                        "transaction_ms",
                        "query_ms",
                        "max_query_ms",
                        "query_count",
                        "candidate_query_count",
                    )
                },
                "statement_ms": distribution(
                    [query["elapsed_ms"] for row in good for query in row["queries"]]
                ),
                "candidate_statement_ms": distribution(
                    [
                        query["elapsed_ms"]
                        for row in good
                        for query in row["queries"]
                        if query["kind"] == "age_candidate"
                    ]
                ),
            }
        summaries = report["summaries"]
        report["candidate_over_sql"] = {
            metric: {
                percentile: (
                    summaries["candidate"][metric][percentile]
                    / summaries["sql"][metric][percentile]
                    if summaries["candidate"][metric][percentile] is not None
                    and summaries["sql"][metric][percentile]
                    else None
                )
                for percentile in ("median", "p95")
            }
            for metric in ("request_ms", "transaction_ms", "query_ms")
        }
        failures = [row for row in report["samples"] if "error" in row]
        report["status"] = "failed" if failures else "passed"
        assert not failures, f"{len(failures)} failed paired requests; see {destination}"
        assert all(
            row["candidate_query_count"] > 0
            for row in report["samples"]
            if row["backend"] == "candidate"
        )
    except Exception as exc:
        report["status"] = "failed"
        report["experiment_error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        raise
    finally:
        destination.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
