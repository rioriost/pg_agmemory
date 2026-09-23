"""Content-free, bounded paired SQL/native AGE resource evidence.

Only this process is instrumented. Canonical fixtures use normal runtime writes;
the independent oracle enumerates fixture topology, never SQL traversal results.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import subprocess
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import partial
from itertools import product
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.transaction import AsyncTransaction
from pydantic import ValidationError

from pg_agmemory import __version__
from pg_agmemory.age_graph import AgeGraph, validate_age_runtime
from pg_agmemory.database import (
    SCHEMA_VERSION,
    VECTOR_VERSION,
    RuntimeValidationError,
    migrate,
    validate_runtime,
)
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import (
    CreateEntity,
    CreateRelation,
    Evidence,
    ExpandGraph,
    GraphResult,
    Observe,
    ReviseRelation,
)
from pg_agmemory.service import MemoryError as ServiceMemoryError
from pg_agmemory.service import MemoryService, bind_identity, principal_connection

FORMAT = "pgag-graph-resource-result-v1"
AGE_COMMIT = "72707aab7ce982bf13cad3d102bd869dab07d64b"
FROZEN_PROFILE_DIGEST = "44455f45baa2ed8b2c297c51457dc925b32dc8dd9e6df7e41e1459bcc1f33af9"
ACTIVE = ContextVar("graph_resource_sample", default=None)
ERROR_CODES = frozenset({
    "administrative_command_failed", "administrative_result_invalid",
    "hidden_canary_fixture_empty", "hidden_path_oracle_not_empty",
    "historical_revision_probe_not_distinct", "invalid_graph_shape", "invalid_graph_size",
    "nonroot_linux_required", "positive_overflow_missing", "positive_path_oracle_empty",
    "probe_failed", "profile_age_commit", "profile_cases", "profile_fixture", "profile_format",
    "profile_gate", "profile_not_frozen", "profile_resources", "profile_sampling",
    "profile_timeouts", "profile_versions", "publication_count_mismatch",
    "publication_not_enabled", "runtime_role_invalid", "extension_version_mismatch",
    "schema_unavailable", "schema_version_mismatch", "graph_backend_unqualified",
    "graph_projection_unavailable", "graph_projection_invalid", "graph_projection_stale",
    "graph_invalidated", "unauthenticated", "not_found", "invalid_relation_reference",
    "revision_conflict", "revision_limit_exceeded", "idempotency_conflict", "invalid_evidence",
    "oracle_mismatch", "paired_read_failed",
    "run_not_completed",
})


class BenchmarkError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


SQL_ERROR_TYPES = frozenset(
    value for value in vars(psycopg.errors).values()
    if isinstance(value, type) and issubclass(value, psycopg.Error)
)
ERROR_TYPES = SQL_ERROR_TYPES | frozenset({
    BenchmarkError, RuntimeValidationError, ServiceMemoryError, ValidationError,
    RuntimeError, ValueError, KeyError, TypeError, AssertionError, OSError,
    PermissionError, FileNotFoundError,
    TimeoutError, subprocess.TimeoutExpired, json.JSONDecodeError,
})
SQLSTATES = frozenset(getattr(value, "sqlstate", None) for value in SQL_ERROR_TYPES) - {None}
MEASUREMENT_ERRORS = (
    BenchmarkError, RuntimeValidationError, ServiceMemoryError, ValidationError,
    psycopg.Error, RuntimeError, ValueError, KeyError, TypeError, AssertionError,
    OSError, subprocess.TimeoutExpired,
)


def require(value, code):
    if not value:
        raise BenchmarkError(code)


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def same_typed_value(value, expected):
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return value.keys() == expected.keys() and all(
            same_typed_value(value[key], item) for key, item in expected.items()
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            same_typed_value(a, b) for a, b in zip(value, expected, strict=True)
        )
    return value == expected


def nearest_rank(values, percentile=0.95):
    """No interpolation; an empty stratum cannot pass."""
    if not finite_number(percentile) or not 0 < percentile <= 1:
        raise ValueError("invalid percentile")
    if any(not finite_number(v) or v < 0 for v in values):
        raise ValueError("invalid percentile inputs")
    if not values:
        return None
    return sorted(values)[math.ceil(percentile * len(values)) - 1]


def topology(shape, nodes):
    require(type(nodes) is int and nodes in (12, 64), "invalid_graph_size")
    if shape == "chain":
        return [(i, i + 1) for i in range(nodes - 1)]
    if shape == "fanout":
        return [(0, i) for i in range(1, 5)] + [
            (hub, leaf) for hub in range(1, 5) for leaf in range(5, nodes)
        ]
    if shape == "multiseed":
        return [(i, (i + offset) % nodes) for i in range(nodes) for offset in (1, 4)]
    raise BenchmarkError("invalid_graph_shape")


def validate_profile(profile):
    require(isinstance(profile, dict) and digest(profile) == FROZEN_PROFILE_DIGEST,
            "profile_not_frozen")
    require(profile["format"] == "pgag-graph-resource-profile-v1", "profile_format")
    require(profile["age_commit"] == AGE_COMMIT, "profile_age_commit")
    require(same_typed_value(profile["postgres_version_num"], 180006)
            and same_typed_value(profile["schema_version"], 21)
            and profile["service_version"] == "0.3.0.dev1", "profile_versions")
    require(same_typed_value(profile["resources"], {
        "database": {"vcpus": 6, "memory_gib": 24},
        "application": {"vcpus": 2, "memory_gib": 8},
    }), "profile_resources")
    sampling = profile["sampling"]
    require(same_typed_value(sampling["warmup_pairs"], 3)
            and same_typed_value(sampling["measured_pairs"], 30)
            and sampling["abort_case_on_error"] is True, "profile_sampling")
    gate = profile["gate"]
    require(same_typed_value(gate["end_to_end_p95_ms_exclusive"], 1500)
            and all(gate[key] is True for key in (
                "zero_errors", "exact_independent_oracle", "complete_sample_counts",
                "both_backends_every_case",
            )), "profile_gate")
    fixture = profile["fixture"]
    require(all(same_typed_value(fixture[key], value) for key, value in {
        "tenant_per_case": 1, "scopes_per_case": 2, "readable_scopes_per_case": 1,
        "hidden_scope_matches_topology": True, "revised_relations_per_scope": 1,
        "other_tenant_decoys": 0, "providers": 0, "max_hops": 2, "max_paths": 100,
        "as_of": "2026-09-08T00:00:00Z", "valid_from": "2026-09-01T00:00:00Z",
    }.items()), "profile_fixture")
    expected = [
        {"id": f"{shape}-{size}", "shape": shape, "nodes_per_scope": nodes,
         "seed_count": 4 if shape == "multiseed" else 1,
         "direction": "both" if shape == "multiseed" else "outgoing"}
        for size, nodes in (("small", 12), ("medium", 64))
        for shape in ("chain", "fanout", "multiseed")
    ]
    require(same_typed_value(profile["cases"], expected), "profile_cases")
    require(same_typed_value(profile["database_settings"]["statement_timeout_ms"], 5000)
            and same_typed_value(profile["database_settings"]["lock_timeout_ms"], 5000),
            "profile_timeouts")
    return profile


def load_profile(path):
    return validate_profile(json.loads(Path(path).read_text()))


def wire_time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def expected_graph(graph, request, *, readable_scopes=None):
    """Enumerate bounded products using only submitted topology and server timestamps."""
    scopes = graph["readable_scopes"] if readable_scopes is None else readable_scopes
    readable = set(scopes) & set(request["scope_ids"])
    known = datetime.fromisoformat(request["known_at"])
    valid = datetime.fromisoformat(request["as_of"])
    nodes = {n["memory_id"]: n for n in graph["nodes"]
             if n["scope_id"] in readable and datetime.fromisoformat(n["recorded_at"]) <= known}
    seeds = set(request["seeds"]) & nodes.keys()
    edges = {}
    for history in graph["edges"]:
        revisions = [r for r in history if datetime.fromisoformat(r["recorded_at"]) <= known]
        if not revisions:
            continue
        edge = max(revisions, key=lambda r: r["assertion"]["revision"])
        if (edge["source_entity"] in nodes and edge["target_entity"] in nodes
                and edge["predicate"] in request["relation_types"]
                and (not edge["valid_from"] or valid >= datetime.fromisoformat(edge["valid_from"]))
                and (not edge["valid_to"] or valid < datetime.fromisoformat(edge["valid_to"]))):
            edges[(edge["assertion"]["memory_id"], edge["assertion"]["revision"])] = edge
    oriented = []
    for ref, edge in edges.items():
        if request["direction"] in ("outgoing", "both"):
            oriented.append((edge["source_entity"], edge["target_entity"], ref))
        if request["direction"] in ("incoming", "both"):
            oriented.append((edge["target_entity"], edge["source_entity"], ref))
    candidates = []
    for hops in range(1, request["max_hops"] + 1):
        for walk in product(oriented, repeat=hops):
            ids = [walk[0][0], *(step[1] for step in walk)]
            if (ids[0] in seeds and len(set(ids)) == len(ids)
                    and all(a[1] == b[0] for a, b in zip(walk, walk[1:], strict=False))):
                order = (hops, ids[0], tuple((*step[2], step[1]) for step in walk))
                candidates.append((order, {
                    "nodes": ids, "assertions": [edges[step[2]]["assertion"] for step in walk],
                }))
    candidates.sort(key=lambda entry: entry[0])
    paths = [path for _, path in candidates[:request["max_paths"]]]
    selected_nodes = seeds | {node for path in paths for node in path["nodes"]}
    selected_edges = {(r["memory_id"], r["revision"])
                      for path in paths for r in path["assertions"]}
    truncated = len(candidates) > request["max_paths"]
    return {
        "as_of": wire_time(request["as_of"]), "known_at": wire_time(request["known_at"]),
        "nodes": [nodes[k] for k in sorted(selected_nodes)],
        "edges": [edges[k] for k in sorted(selected_edges)], "paths": paths,
        "coverage": {"max_hops": request["max_hops"], "truncated": truncated,
                     "complete_within_bounds": not truncated},
        "consistency": graph["consistency"], "empty_reason": None if paths else "not_found",
    }


def normalize_result(result):
    return {key: value for key, value in result.items()
            if key not in ("backend", "projection_watermark")}


def sanitized_error(exc):
    code = getattr(exc, "code", None)
    sqlstate = getattr(exc, "sqlstate", None)
    return {
        "type": type(exc).__name__ if type(exc) in ERROR_TYPES else "Exception",
        "code": code if isinstance(code, str) and code in ERROR_CODES else None,
        "sqlstate": sqlstate if isinstance(sqlstate, str) and sqlstate in SQLSTATES else None,
    }


def logical_query(query, conn):
    text = query.as_string(conn) if isinstance(query, sql.Composable) else str(query)
    if "projected_nodes AS MATERIALIZED" in text:
        return "completeness_query"
    if "ag_catalog.cypher(" in text:
        return "native_path"
    if "pg_advisory_lock" in text:
        return "tenant_barrier"
    return "canonical_or_setup"


@contextmanager
def instrumentation():
    """Scoped process-local patches, with a per-request ContextVar collector."""
    execute = psycopg.AsyncConnection.execute
    cursor_execute = psycopg.AsyncCursor.execute
    enter, exit_ = AsyncTransaction.__aenter__, AsyncTransaction.__aexit__
    opened = {}

    async def timed_execute(conn, query, *args, **kwargs):
        record = ACTIVE.get()
        if record is None:
            return await execute(conn, query, *args, **kwargs)
        kind = logical_query(query, conn)
        started = perf_counter()
        item = {"kind": kind}
        try:
            return await execute(conn, query, *args, **kwargs)
        except MEASUREMENT_ERRORS as exc:
            item["error"] = sanitized_error(exc)
            raise
        finally:
            item["elapsed_ms"] = (perf_counter() - started) * 1000
            record["queries"].append(item)

    async def timed_cursor(cursor, query, *args, **kwargs):
        started = perf_counter()
        try:
            return await cursor_execute(cursor, query, *args, **kwargs)
        finally:
            record = ACTIVE.get()
            if record is not None:
                record["sql_cursor_ms"] += (perf_counter() - started) * 1000
                record["sql_cursor_count"] += 1

    async def timed_enter(transaction):
        if ACTIVE.get() is not None:
            opened[id(transaction)] = perf_counter()
        return await enter(transaction)

    async def timed_exit(transaction, *args):
        try:
            return await exit_(transaction, *args)
        finally:
            started = opened.pop(id(transaction), None)
            if started is not None and ACTIVE.get() is not None:
                ACTIVE.get()["transaction_spans_ms"].append((perf_counter() - started) * 1000)

    psycopg.AsyncConnection.execute = timed_execute
    psycopg.AsyncCursor.execute = timed_cursor
    AsyncTransaction.__aenter__, AsyncTransaction.__aexit__ = timed_enter, timed_exit
    try:
        yield
    finally:
        psycopg.AsyncConnection.execute = execute
        psycopg.AsyncCursor.execute = cursor_execute
        AsyncTransaction.__aenter__, AsyncTransaction.__aexit__ = enter, exit_


@asynccontextmanager
async def service(url, subject, *, readonly=False):
    async with principal_connection(url, subject) as (conn, identity):
        async with conn.transaction():
            if readonly:
                await conn.execute("SET TRANSACTION READ ONLY")
            await bind_identity(conn, subject, identity)
            yield MemoryService(conn, identity)


async def graph_read(url, subject, backend, request):
    async with service(url, subject, readonly=True) as memory:
        adapter = AgeGraph(memory) if backend == "age" else SqlGraph(memory)
        result = await adapter.expand(ExpandGraph.model_validate(request))
    return GraphResult.model_validate(result).model_dump(mode="json")


async def measured_read(
    operation, expected, *, backend, phase, pair_index, position, expected_watermark=None,
):
    record = {
        "backend": backend, "phase": phase, "pair_index": pair_index, "position": position,
        "queries": [], "transaction_spans_ms": [], "sql_cursor_ms": 0.0,
        "sql_cursor_count": 0, "valid": False, "error": None,
    }
    token = ACTIVE.set(record)
    started = perf_counter()
    result = None
    try:
        result = await operation()
    except MEASUREMENT_ERRORS as exc:
        record["error"] = sanitized_error(exc)
    finally:
        record["end_to_end_ms"] = (perf_counter() - started) * 1000
        ACTIVE.reset(token)
    if result is not None:
        record["oracle_equal"] = normalize_result(result) == expected
        record["result_digest"] = digest(normalize_result(result))
        record["path_count"] = len(result["paths"])
        record["coverage"] = result["coverage"]
        watermark_valid = (result["projection_watermark"] is None if backend == "sql" else
                           result["projection_watermark"] == expected_watermark)
        record["valid"] = (
            record["oracle_equal"] and result["backend"] == backend and watermark_valid
        )
        if not record["valid"]:
            record["error"] = {"type": "ContractMismatch", "code": "oracle_mismatch",
                               "sqlstate": None}
    record["query_count"] = len(record["queries"])
    record["sql_execute_ms"] = sum(q["elapsed_ms"] for q in record["queries"])
    record["transaction_span_ms"] = max(record["transaction_spans_ms"], default=0)
    for kind in ("completeness_query", "native_path"):
        record[f"{kind}_ms"] = sum(q["elapsed_ms"] for q in record["queries"] if q["kind"] == kind)
        record[f"{kind}_count"] = sum(q["kind"] == kind for q in record["queries"])
    return record


def summarize_case(case, profile):
    sampling = profile["sampling"]
    strata = {}
    recognized = all(
        s.get("backend") in ("sql", "age") and s.get("phase") in ("warmup", "measured")
        for s in case["samples"]
    )
    expected_order = [
        (phase, pair_index, backend, position)
        for phase, count in (("warmup", sampling["warmup_pairs"]),
                             ("measured", sampling["measured_pairs"]))
        for pair_index in range(count)
        for position, backend in enumerate(
            ("sql", "age") if pair_index % 2 == 0 else ("age", "sql")
        )
    ]
    ordered = [
        (s.get("phase"), s.get("pair_index"), s.get("backend"), s.get("position"))
        for s in case["samples"]
    ] == expected_order

    def valid_read(sample):
        result_digest = sample.get("result_digest")
        return (
            sample.get("valid") is True and sample.get("error") is None
            and sample.get("oracle_equal") is True
            and isinstance(result_digest, str) and len(result_digest) == 64
            and all(character in "0123456789abcdef" for character in result_digest)
            and finite_number(sample.get("end_to_end_ms")) and sample["end_to_end_ms"] >= 0
        )

    probes = case.get("probes", [])
    probes_verified = [
        (probe.get("name"), probe.get("backend"), probe.get("phase")) for probe in probes
    ] == [
        (name, backend, "probe")
        for name in ("current", "historical", "hidden_intermediate_path")
        for backend in ("sql", "age")
    ] and all(valid_read(probe) for probe in probes)
    if probes_verified:
        probes_verified = all(
            probes[index]["result_digest"] == probes[index + 1]["result_digest"]
            for index in (0, 2, 4)
        )
    expected_digest = case.get("fixture", {}).get("expected_digest")

    def valid_sample(sample):
        return (
            valid_read(sample) and sample["result_digest"] == expected_digest
            and sample.get("pair_equal") is True
            and type(sample.get("pair_index")) is int and sample["pair_index"] >= 0
            and type(sample.get("position")) is int and sample["position"] in (0, 1)
            and sample["position"] == (
                sample["pair_index"] % 2 if sample["backend"] == "sql"
                else 1 - sample["pair_index"] % 2
            )
        )

    for backend in ("sql", "age"):
        samples = [s for s in case["samples"]
                   if s["backend"] == backend and s["phase"] == "measured"]
        warm = [s for s in case["samples"]
                if s["backend"] == backend and s["phase"] == "warmup"]
        valid = [s for s in samples if valid_sample(s)]
        p95 = nearest_rank([s["end_to_end_ms"] for s in valid])
        settled = (
            len(samples) == len(valid) == sampling["measured_pairs"]
            and len(warm) == sampling["warmup_pairs"]
            and all(valid_sample(s) for s in warm)
            and sorted(s["pair_index"] for s in samples) == list(range(sampling["measured_pairs"]))
            and sorted(s["pair_index"] for s in warm) == list(range(sampling["warmup_pairs"]))
        )
        strata[backend] = {
            "attempted": len(samples), "valid": len(valid),
            "errors": len(samples) - len(valid),
            "warmup_attempted": len(warm),
            "warmup_errors": sum(not valid_sample(s) for s in warm),
            "sample_shortfall": max(0, sampling["measured_pairs"] - len(valid)),
            "warmup_shortfall": max(
                0, sampling["warmup_pairs"] - sum(valid_sample(s) for s in warm),
            ),
            "settled": settled, "end_to_end_p95_ms": p95,
            "p95_under_threshold": p95 is not None
            and p95 < profile["gate"]["end_to_end_p95_ms_exclusive"],
        }
    qualified = (recognized and ordered and probes_verified and case["setup"]["status"] == "passed"
                 and not case.get("failures")
                 and case.get("probes_passed") is True
                 and all(s["settled"] and s["p95_under_threshold"] for s in strata.values()))
    return {"strata": strata, "resource_qualified": qualified,
            "sample_records_recognized": recognized, "sample_order_verified": ordered,
            "probes_verified": probes_verified}


def summarize_report(report, profile):
    cases = report["cases"]
    complete = [c["id"] for c in cases] == [c["id"] for c in profile["cases"]]
    # Derive qualification from raw records, never a caller-supplied/stale summary.
    try:
        summaries = [summarize_case(case, profile) for case in cases]
    except (KeyError, TypeError, ValueError, AttributeError):
        return {
            "resource_qualified": False, "m3_qualified": False,
            "all_case_counts_settled": False,
        }
    settled = complete and all(
        summary["sample_records_recognized"] and summary["sample_order_verified"]
        and set(summary["strata"]) == {"sql", "age"}
        and all(s["settled"] is True for s in summary["strata"].values())
        for summary in summaries
    )
    return {
        "resource_qualified": bool(
            settled and not report.get("failures") and report["exact_commit_inputs"] is True
            and report["mode"] == "baseline"
            and all(summary["resource_qualified"] is True for summary in summaries)
        ),
        "m3_qualified": False,
        "all_case_counts_settled": settled,
    }


def cli(arguments, *, request=None):
    result = subprocess.run(
        ["pg-agmemory", *arguments], input=None if request is None else json.dumps(request),
        capture_output=True, text=True, timeout=180, check=False,
    )
    require(result.returncode == 0, "administrative_command_failed")
    value = json.loads(result.stdout)
    require(isinstance(value, dict) and "error" not in value, "administrative_result_invalid")
    return value


def server_clock(admin_url):
    with psycopg.connect(admin_url) as conn:
        return wire_time(conn.execute("SELECT clock_timestamp()").fetchone()[0])


def provision_case(admin_url, case_id):
    writer_subject = "graph-resource-writer-" + case_id
    reader_subject = "graph-resource-reader-" + case_id
    provisioned = cli(["provision", "--subject", writer_subject])
    tenant, visible = UUID(provisioned["tenant_id"]), UUID(provisioned["scope_id"])
    hidden, reader = uuid4(), uuid4()
    with psycopg.connect(admin_url) as conn:
        conn.execute("INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)", (tenant, hidden))
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write','delete'])""",
            (tenant, hidden, UUID(provisioned["principal_id"])),
        )
        conn.execute(
            "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
            (tenant, reader, reader_subject),
        )
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read'])""", (tenant, visible, reader),
        )
    return tenant, [visible, hidden], writer_subject, reader_subject


async def seed_case(admin_url, runtime_url, spec, profile):
    tenant, scopes, writer, reader = provision_case(admin_url, spec["id"])
    graph = {"nodes": [], "edges": [], "readable_scopes": [str(scopes[0])],
             "consistency": {"access_epoch": 1, "deletion_epoch": 1}}
    scope_nodes, revise = [], []
    links = topology(spec["shape"], spec["nodes_per_scope"])
    for scope_index, scope in enumerate(scopes):
        ids = []
        evidence = None
        async with service(runtime_url, writer) as memory:
            for index in range(spec["nodes_per_scope"]):
                label = f"Fixture {spec['id']} scope {scope_index} node {index}"
                source = await memory.observe(Observe(
                    scope_id=scope, source_namespace=writer,
                    source_event_id=f"{scope_index}-node-{index}",
                    occurred_at=profile["fixture"]["valid_from"], content=label,
                    consent_reference="synthetic-graph-resource-only",
                ), f"observe-{scope_index}-{index}")
                node_evidence = [Evidence(memory_id=source["memory_id"], quote=label)]
                if evidence is None:
                    evidence = node_evidence
                receipt = await SqlGraph(memory).create_entity(CreateEntity(
                    scope_id=scope, entity_type="component", canonical_label=label,
                    evidence=node_evidence, explicit_intent=True,
                ), f"node-{scope_index}-{index}")
                ids.append(receipt["memory_id"])
                graph["nodes"].append({
                    **receipt, "scope_id": str(scope), "entity_type": "component",
                    "canonical_label": label,
                })
            for index, (source, target) in enumerate(links):
                receipt = await SqlGraph(memory).create_relation(CreateRelation(
                    scope_id=scope, source_entity=ids[source], target_entity=ids[target],
                    predicate="depends_on", evidence=evidence, explicit_intent=True,
                    valid_from=profile["fixture"]["valid_from"],
                ), f"edge-{scope_index}-{index}")
                edge = {
                    "assertion": {k: receipt[k] for k in ("memory_id", "revision")},
                    "source_entity": ids[source], "target_entity": ids[target],
                    "predicate": "depends_on", "valid_from": profile["fixture"]["valid_from"],
                    "valid_to": None, "epistemic_status": "reported",
                }
                graph["edges"].append([edge])
                if index == 0:
                    revise.append((edge, ids[2], evidence))
        scope_nodes.append(ids)
    historical = server_clock(admin_url)
    for edge, target, evidence in revise:
        async with service(runtime_url, writer) as memory:
            receipt = await SqlGraph(memory).revise_relation(
                UUID(edge["assertion"]["memory_id"]), ReviseRelation(
                    expected_revision=1, target_entity=target, evidence=evidence,
                    explicit_intent=True, valid_from=profile["fixture"]["valid_from"],
                    reason="Synthetic deterministic target correction",
                ), "revision-" + edge["assertion"]["memory_id"],
            )
        revision = {**edge, "target_entity": target,
                    "assertion": {k: receipt[k] for k in ("memory_id", "revision")}}
        next(history for history in graph["edges"] if history[0] is edge).append(revision)
    with psycopg.connect(admin_url) as conn:
        timestamps = dict(conn.execute(
            "SELECT id,created_at FROM memory.object WHERE tenant_id=%s", (tenant,),
        ).fetchall())
        revisions = {(str(i), r): at for i, r, at in conn.execute(
            """SELECT assertion_id,revision,lower(system_time) FROM memory.assertion_revision
               WHERE tenant_id=%s""", (tenant,),
        )}
    for node in graph["nodes"]:
        node["recorded_at"] = wire_time(timestamps[UUID(node["memory_id"])])
    for history in graph["edges"]:
        for edge in history:
            ref = edge["assertion"]
            edge["recorded_at"] = wire_time(revisions[(ref["memory_id"], ref["revision"])])
    current = server_clock(admin_url)
    request = {
        "scope_ids": [str(s) for s in scopes],
        "seeds": scope_nodes[0][:spec["seed_count"]], "relation_types": ["depends_on"],
        "purpose": "Bounded paired graph resource qualification",
        "direction": spec["direction"], "max_hops": 2, "max_paths": 100,
        "as_of": profile["fixture"]["as_of"], "known_at": current,
    }
    probes = {
        "historical": {**request, "known_at": historical},
        "hidden_intermediate_path": {**request, "seeds": [scope_nodes[1][0]]},
    }
    return tenant, reader, graph, request, probes


def publish(admin_url, tenant, profile_digest, directory, phases):
    tenant = str(tenant)
    generation_id = str(uuid4())
    initial = cli(["graph-generation"], request={"operation": "get", "tenant_id": tenant})
    phases["generation_id"] = generation_id
    phases["input_digest"] = initial["current_input_digest"]
    artifact = directory / f"artifact-{generation_id}.json"
    try:
        started = perf_counter()
        cli(["graph-generation"], request={
            "operation": "begin", "tenant_id": tenant, "generation_id": generation_id,
            "expected_revision": 0, "expected_input_digest": initial["current_input_digest"],
            "profile_digest": profile_digest,
        })
        phases["begin_ms"] = (perf_counter() - started) * 1000
        started = perf_counter()
        exported = cli([
            "graph-artifact", "export", "--tenant-id", tenant, "--generation-id", generation_id,
            "--expected-revision", "1", "--file", str(artifact),
        ])
        phases["export_ms"] = (perf_counter() - started) * 1000
        phases["artifact_bytes"] = artifact.stat().st_size
        phases["node_count"] = exported["node_count"]
        phases["edge_revision_count"] = exported["edge_revision_count"]
        phases["artifact_digest"] = exported["artifact_digest"]
        started = perf_counter()
        cli(["graph-generation"], request={
            "operation": "record", "tenant_id": tenant, "generation_id": generation_id,
            "expected_revision": 1, "expected_input_digest": initial["current_input_digest"],
            "artifact_digest": exported["artifact_digest"],
        })
        phases["record_ms"] = (perf_counter() - started) * 1000
        started = perf_counter()
        installed = cli([
            "age-projection", "publish", "--tenant-id", tenant, "--generation-id", generation_id,
            "--expected-generation-revision", "2", "--expected-revision", "0",
            "--file", str(artifact),
        ])
        phases["publish_ms"] = (perf_counter() - started) * 1000
        require(installed["serving_enabled"] and installed["artifact_verified"],
                "publication_not_enabled")
        phases["artifact_verified"] = installed["artifact_verified"]
        phases["serving_enabled"] = installed["serving_enabled"]
        with psycopg.connect(admin_url) as conn:
            conn.execute("ANALYZE")
        return generation_id
    finally:
        artifact.unlink(missing_ok=True)


def footprint(admin_url):
    with psycopg.connect(admin_url, row_factory=dict_row) as conn:
        result = conn.execute(
            """SELECT pg_database_size(current_database()) AS database_bytes,
                      (SELECT sum(size)::bigint FROM pg_ls_waldir()) AS wal_directory_bytes,
                      pg_current_wal_lsn()::text AS wal_lsn"""
        ).fetchone()
        result["relations"] = conn.execute(
            """SELECT n.nspname AS schema,c.relname AS relation,
                      pg_relation_size(c.oid) AS heap_bytes,pg_indexes_size(c.oid) AS index_bytes,
                      pg_total_relation_size(c.oid) AS total_bytes,
                      c.relrowsecurity,c.relforcerowsecurity
               FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE c.relkind='r' AND (n.nspname IN ('memory','memory_ops')
                   OR n.nspname ~ '^pgag_age_[0-9a-f]{32}$')
               ORDER BY n.nspname,c.relname"""
        ).fetchall()
        graph_tables = [row for row in result["relations"] if row["schema"].startswith("pgag_age_")]
        result["graph_heap_bytes"] = sum(row["heap_bytes"] for row in graph_tables)
        result["graph_index_bytes"] = sum(row["index_bytes"] for row in graph_tables)
        result["graph_total_bytes"] = sum(row["total_bytes"] for row in graph_tables)
        return result


async def environment(admin_url, runtime_url, *, native_published=False):
    await validate_runtime(runtime_url)
    if native_published:
        await validate_age_runtime(runtime_url)
    async with await psycopg.AsyncConnection.connect(
        runtime_url, row_factory=dict_row, autocommit=True,
        options="-c statement_timeout=5000 -c lock_timeout=5000",
    ) as conn:
        role = await (await conn.execute(
            """SELECT current_user,rolsuper,rolbypassrls,
                EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        WHERE (n.nspname IN ('memory','memory_ops','ag_catalog')
                          OR n.nspname ~ '^pgag_age_[0-9a-f]{32}$')
                          AND pg_has_role(current_user,c.relowner,'MEMBER')) AS owns_tables
               FROM pg_roles WHERE rolname=current_user"""
        )).fetchone()
        require(not role["rolsuper"] and not role["rolbypassrls"] and not role["owns_tables"],
                "runtime_role_invalid")
        settings = await (await conn.execute(
            """SELECT name,setting,unit,source FROM pg_settings WHERE name=ANY(%s)
               ORDER BY name""",
            (["server_version_num", "shared_preload_libraries", "shared_buffers", "work_mem",
              "maintenance_work_mem", "max_connections", "jit", "jit_above_cost",
              "max_parallel_workers_per_gather", "random_page_cost", "effective_cache_size",
              "statement_timeout", "lock_timeout"],),
        )).fetchall()
    with psycopg.connect(admin_url) as conn:
        schema = conn.execute("SELECT max(version) FROM public.pgag_schema_migration").fetchone()[0]
        build = conn.execute("SELECT ag_catalog.pgag_age_build()").fetchone()[0]
    return {
        "runtime_uid": os.geteuid(), "runtime_role": role, "settings": settings,
        "age_build": build, "schema_version": schema, "native_runtime_verified": native_published,
        "service_version": __version__,
        "pgvector_version": VECTOR_VERSION, "pgvector_runtime_verified": True,
        "platform": platform.platform(), "machine": platform.machine(),
        "cpu_count_visible": os.cpu_count(), "host_exclusive": False,
        "cache": "fresh owned database; ANALYZE after publication; paired warm quiescent reads",
        "measurement_boundary": (
            "service call: connect, identity, tenant barrier, transaction, commit, close; not HTTP"
        ),
        "perf": "not collected; no hardware counters or privilege changes",
        "perf_event_paranoid": int(Path("/proc/sys/kernel/perf_event_paranoid").read_text()),
        "timing_definitions": {
            "query_count": "AsyncConnection.execute calls, including failures",
            "sql_cursor_count": "AsyncCursor.execute calls, including failures",
            "transaction_span_ms": "largest explicit transaction enter-through-exit span",
            "completeness_query": "logical completeness proof, identified without retaining SQL",
            "native_path": "logical native bounded path query, identified without retaining SQL",
        },
        "cpu_description": next((line.split(":", 1)[1].strip()
                                 for line in Path("/proc/cpuinfo").read_text().splitlines()
                                 if line.startswith(("model name", "Hardware"))),
                                platform.machine()),
    }


def save_report(directory, report, profile):
    for case in report["cases"]:
        case["summary"] = summarize_case(case, profile)
    report.update(summarize_report(report, profile))
    target = directory / "result.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    target.chmod(0o600)


async def run(args, profile, report):
    directory = args.directory
    require(__version__ == profile["service_version"]
            and SCHEMA_VERSION == profile["schema_version"], "profile_versions")
    admin_url = os.environ["PGAG_ADMIN_DATABASE_URL"]
    migrate(admin_url)
    role = "pgag_graph_benchmark"
    password = os.environ["PGAG_BENCHMARK_RUNTIME_PASSWORD"]
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL(
            "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
            "PASSWORD {} IN ROLE pgag_runtime"
        ).format(sql.Identifier(role), sql.Literal(password)))
    params = conninfo_to_dict(admin_url)
    params.update(user=role, password=password)
    runtime_url = make_conninfo(**params)
    report["environment"] = await environment(admin_url, runtime_url)
    report["footprint_before"] = footprint(admin_url)
    selected = profile["cases"][:1] if args.preflight else profile["cases"]
    for spec in selected:
        case = {"id": spec["id"], "specification": spec, "setup": {"status": "running"},
                "samples": [], "probes": [], "failures": [], "probes_passed": False}
        report["cases"].append(case)
        started = perf_counter()
        try:
            tenant, subject, graph, request, probes = await seed_case(
                admin_url, runtime_url, spec, profile,
            )
            case["setup"]["seed_ms"] = (perf_counter() - started) * 1000
            case["setup"]["publication"] = {}
            generation_id = publish(
                admin_url, tenant, report["profile_digest"], directory,
                case["setup"]["publication"],
            )
            expected = expected_graph(graph, request)
            historical = expected_graph(graph, probes["historical"])
            require(expected["paths"] and historical["paths"], "positive_path_oracle_empty")
            require(expected["paths"] != historical["paths"],
                    "historical_revision_probe_not_distinct")
            require(expected_graph(graph, probes["hidden_intermediate_path"])["paths"] == [],
                    "hidden_path_oracle_not_empty")
            require(expected_graph(
                graph, probes["hidden_intermediate_path"],
                readable_scopes=[request["scope_ids"][1]],
            )["paths"], "hidden_canary_fixture_empty")
            if spec["id"] == "fanout-medium":
                require(expected["coverage"]["truncated"], "positive_overflow_missing")
            case["fixture"] = {
                "nodes": len(graph["nodes"]), "relations": len(graph["edges"]),
                "edge_revisions": sum(len(h) for h in graph["edges"]),
                "readable_nodes": spec["nodes_per_scope"],
                "hidden_nodes": spec["nodes_per_scope"], "scope_permission_fraction": 0.5,
                "expected_digest": digest(expected), "expected_paths": len(expected["paths"]),
                "expected_coverage": expected["coverage"], "as_of": request["as_of"],
                "known_at": request["known_at"],
                "historical_known_at": probes["historical"]["known_at"],
                "expected_consistency": graph["consistency"],
                "freshness_basis": (
                    "published current input digest; per-request epoch checks and canonical proof"
                ),
            }
            counts = case["setup"]["publication"]
            require(counts["node_count"] == case["fixture"]["nodes"]
                    and counts["edge_revision_count"] == case["fixture"]["edge_revisions"],
                    "publication_count_mismatch")
            report["environment"] = await environment(
                admin_url, runtime_url, native_published=True,
            )
            case["setup"]["status"] = "passed"
            case["setup"]["elapsed_ms"] = (perf_counter() - started) * 1000
            for name, probe in {"current": request, **probes}.items():
                oracle = expected_graph(graph, probe)
                for backend in ("sql", "age"):
                    with instrumentation():
                        item = await measured_read(
                            partial(graph_read, runtime_url, subject, backend, probe),
                            oracle, backend=backend, phase="probe", pair_index=0, position=0,
                            expected_watermark=generation_id,
                        )
                    item["name"] = name
                    case["probes"].append(item)
                    require(item["valid"], "probe_failed")
            case["probes_passed"] = True
        except MEASUREMENT_ERRORS as exc:
            phase = "probe" if case["setup"]["status"] == "passed" else "setup"
            if phase == "setup":
                case["setup"]["status"] = "failed"
            case["failures"].append({"phase": phase, **sanitized_error(exc)})
            save_report(directory, report, profile)
            continue
        finally:
            case["setup"].setdefault("elapsed_ms", (perf_counter() - started) * 1000)
        with instrumentation():
            aborted = False
            for phase, pairs in (
                ("warmup", 1 if args.preflight else profile["sampling"]["warmup_pairs"]),
                ("measured", 1 if args.preflight else profile["sampling"]["measured_pairs"]),
            ):
                if aborted:
                    break
                for pair_index in range(pairs):
                    backends = ("sql", "age") if pair_index % 2 == 0 else ("age", "sql")
                    pair = []
                    for position, backend in enumerate(backends):
                        record = await measured_read(
                            partial(graph_read, runtime_url, subject, backend, request),
                            expected, backend=backend, phase=phase, pair_index=pair_index,
                            position=position,
                            expected_watermark=generation_id,
                        )
                        case["samples"].append(record)
                        pair.append(record)
                    equal = all(s["valid"] for s in pair) and (
                        pair[0].get("result_digest") == pair[1].get("result_digest")
                    )
                    for sample in pair:
                        sample["pair_equal"] = equal
                    if not equal:
                        case["failures"].append({"phase": phase, "code": "paired_read_failed",
                                                 "pair_index": pair_index})
                        aborted = True
                    save_report(directory, report, profile)
                    if aborted:
                        break
        case["footprint_after"] = footprint(admin_url)
        save_report(directory, report, profile)
    report["footprint_after"] = footprint(admin_url)
    with psycopg.connect(admin_url) as conn:
        report["wal_generated_bytes"] = int(conn.execute(
            "SELECT pg_wal_lsn_diff(%s,%s)",
            (report["footprint_after"]["wal_lsn"], report["footprint_before"]["wal_lsn"]),
        ).fetchone()[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    profile = load_profile(args.profile)
    identity = json.loads((args.directory / "build-identity.json").read_text())
    report = {
        "format": FORMAT, "profile": profile, "profile_digest": digest(profile),
        "implementation": identity, "exact_commit_inputs": (
            identity["exact_commit_inputs"] is True and not args.preflight and not args.development
        ),
        "mode": ("preflight" if args.preflight else
                 "development" if args.development else "baseline"),
        "cases": [], "failures": [], "resource_qualified": False, "m3_qualified": False,
        "limitations": profile["scope"],
    }
    save_report(args.directory, report, profile)
    completed = False
    try:
        require(os.geteuid() != 0 and platform.system() == "Linux", "nonroot_linux_required")
        asyncio.run(run(args, profile, report))
        completed = True
    except MEASUREMENT_ERRORS as exc:
        report["failures"].append({"phase": "environment_or_run", **sanitized_error(exc)})
    finally:
        if not completed and not report["failures"]:
            report["failures"].append({"phase": "environment_or_run", "code": "run_not_completed"})
        save_report(args.directory, report, profile)
    print(json.dumps({
        "report": "result.json", "resource_qualified": report["resource_qualified"],
        "m3_qualified": False, "mode": report["mode"],
        "completed_cases": len(report["cases"]), "failures": len(report["failures"]),
    }), flush=True)
    raise SystemExit(0 if report["resource_qualified"] else 1)


if __name__ == "__main__":
    main()
