"""Offline contracts; real AGE/canonical conformance lives in the separate live suite."""

import asyncio
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from pg_agmemory.models import ExpandGraph, GraphPath
from pg_agmemory.service import MemoryError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "age_graph_candidate", ROOT / "scripts" / "age_graph_candidate.py",
)
assert spec is not None and spec.loader is not None
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
GRAPH = "pgag_m3_" + "a" * 32


def metadata():
    return {
        "format": candidate.FORMAT, "graph_name": GRAPH, "age_commit": candidate.AGE_COMMIT,
        "age_version": candidate.AGE_VERSION, "candidate_only": True,
        "activation_permitted": False, "projection_watermark": None,
        "node_count": 7, "edge_revision_count": 10,
    }


@pytest.mark.parametrize("name", [
    "", "age", "pgag_m3_" + "A" * 32, "pgag_m3_" + "a" * 31,
    "pgag_m3_" + "a" * 33, GRAPH + "\n", GRAPH + "'; DROP SCHEMA memory;--", None,
])
def test_graph_name_is_strictly_owned(name):
    with pytest.raises(MemoryError, match="graph_projection_invalid"):
        candidate.validate_graph_name(name)


def test_uuid4_hex_graph_name_is_allowed():
    graph = "pgag_m3_" + uuid4().hex
    assert candidate.validate_graph_name(graph) == graph


@pytest.mark.parametrize("strategy", ["native_vle", "sql", "fallback", "", None])
def test_unqualified_strategy_rejects_before_opening_connection(monkeypatch, strategy):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(candidate, "principal_connection", connect)
    with pytest.raises(MemoryError, match="graph_backend_unqualified"):
        asyncio.run(candidate.expand_candidate("unused", "unused", None, GRAPH, strategy=strategy))
    connect.assert_not_called()


@pytest.mark.parametrize("direction", ["outgoing", "incoming"])
def test_fixed_template_binds_values_and_never_limits_before_canonical_authority(direction):
    query = candidate.cypher_template(direction)
    assert "s:Node {id: $node}" in query
    assert "[e:LINK]" in query
    assert "(n:Node)" in query
    assert "*" not in query
    assert "LIMIT" not in query
    adjacent = candidate.adjacent_query(GRAPH, direction).as_string()
    assert "%(age_parameters)s::ag_catalog.agtype" in adjacent
    assert "JOIN memory.relation r" in adjacent
    assert "JOIN memory.relation_revision v" in adjacent
    assert "v.revision=c.revision::bigint" in adjacent
    assert "c.revision::jsonb" not in adjacent
    assert "(c.origin_id::jsonb #>> '{}')::uuid" in adjacent
    assert "(c.next_id::jsonb #>> '{}')::uuid" in adjacent
    assert "%(tenant)s" in adjacent
    assert "LIMIT" not in adjacent


def test_both_direction_uses_two_one_hop_templates_not_undirected_duplicates():
    adjacent = candidate.adjacent_query(GRAPH, "both").as_string()
    assert adjacent.count("ag_catalog.cypher(") == 2
    assert " UNION ALL " in adjacent
    assert "-[e:LINK]->" in adjacent
    assert "<-[e:LINK]-" in adjacent
    assert "*" not in adjacent


def test_native_template_is_preserved_but_cannot_be_selected():
    assert "[:LINK*1..1]" in candidate.cypher_template("outgoing", strategy="native_vle")
    with pytest.raises(MemoryError, match="graph_backend_unqualified"):
        candidate.adjacent_query(GRAPH, "outgoing", strategy="native_vle")


@pytest.mark.parametrize("field,value", [
    ("format", "production"), ("graph_name", "pgag_m3_" + "b" * 32),
    ("age_commit", "main"), ("age_version", "1.9.0"),
    ("candidate_only", False), ("candidate_only", 1), ("activation_permitted", True),
    ("projection_watermark", "fresh"), ("node_count", True), ("node_count", -1),
    ("node_count", candidate.MAX_NODES + 1),
    ("edge_revision_count", candidate.MAX_EDGE_REVISIONS + 1),
])
def test_invalid_projection_metadata_cannot_be_activated_or_guessed(field, value):
    changed = {**metadata(), field: value}
    with pytest.raises(MemoryError, match="graph_projection_invalid"):
        candidate.validate_metadata(changed, GRAPH)


def test_exact_metadata_shape_is_required():
    assert candidate.validate_metadata(metadata(), GRAPH) == metadata()
    with pytest.raises(MemoryError, match="graph_projection_invalid"):
        candidate.validate_metadata({**metadata(), "fresh": True}, GRAPH)
    with pytest.raises(MemoryError, match="graph_projection_invalid"):
        candidate.validate_metadata(None, GRAPH)


@pytest.mark.parametrize("label", candidate.LABELS)
def test_each_label_policy_checks_context_and_canonical_evidence(label):
    policy = candidate.label_policy(GRAPH, label).as_string()
    for expected in (
        "memory.current_tenant()", "memory.current_principal()", "pgag.m3_graph",
        "pgag.m3_as_of", "pgag.m3_known_at", "pgag.m3_scope_ids",
        "memory.entity_evidence", "memory.episode", "memory.object",
    ):
        assert expected in policy
    assert "SECURITY DEFINER" not in policy
    if label in ("LINK", "_ag_label_edge"):
        assert "ar.valid_time @>" in policy and "ar.system_time @>" in policy
        assert "rr.target_id=(properties::jsonb->>'target_id')::uuid" in policy
        assert "r.source_id=(properties::jsonb->>'source_id')::uuid" in policy
        assert "pgag.m3_predicates" in policy


def test_neighbors_delegates_canonical_filter_and_budget_without_replacing_age(monkeypatch):
    memory = Mock()
    memory.conn.execute = AsyncMock()
    memory.tenant = uuid4()
    backend = candidate.AgeGraphCandidate(memory, GRAPH)
    canonical = AsyncMock(return_value=[{"id": uuid4()}])
    monkeypatch.setattr(backend, "_canonical_neighbors", canonical)
    node = uuid4()
    walk = GraphPath(nodes=[node], assertions=[])
    data = ExpandGraph(
        scope_ids=[uuid4()], seeds=[node], relation_types=["depends_on"], purpose="test",
    )
    now = datetime.now(UTC)
    rows = asyncio.run(backend.neighbors(walk, data, now, now, 7))
    assert rows == canonical.return_value
    args = canonical.call_args.args
    assert args[:5] == (walk, data, now, now, 7)
    assert "FROM ag_catalog.cypher(" in args[5].as_string()
    assert args[6] == {"age_parameters": json.dumps({"node": str(node)})}
    statement, bound = memory.conn.execute.call_args.args
    assert "set_config('pgag.m3_graph',%s,true)" in statement
    assert bound == (
        GRAPH, now.isoformat(), now.isoformat(), "{" + str(data.scope_ids[0]) + "}",
        "{depends_on}",
    )


def test_projection_size_limit_rejects_before_graph_creation(monkeypatch):
    admin = Mock()
    manager = Mock()
    manager.__enter__ = Mock(return_value=admin)
    manager.__exit__ = Mock(return_value=False)
    admin.execute.return_value.fetchone.side_effect = [
        {"pg": 180006, "preload": "age", "extversion": candidate.AGE_VERSION},
        None,
        {"nodes": candidate.MAX_NODES + 1, "edges": 1},
    ]
    monkeypatch.setattr(candidate, "_admin_connection", Mock(return_value=manager))
    with pytest.raises(MemoryError, match="graph_projection_invalid"):
        candidate.build_projection("unused", GRAPH)
    queries = [call.args[0] for call in admin.execute.call_args_list]
    assert "SET LOCAL row_security = off" in queries
    assert not any("create_graph" in str(query) for query in queries)


def test_drop_requires_matching_owned_metadata(monkeypatch):
    admin = Mock()
    manager = Mock()
    manager.__enter__ = Mock(return_value=admin)
    manager.__exit__ = Mock(return_value=False)
    admin.execute.return_value.fetchone.return_value = {
        "payload": {**metadata(), "graph_name": "pgag_m3_" + "b" * 32},
    }
    monkeypatch.setattr(candidate, "_admin_connection", Mock(return_value=manager))
    with pytest.raises(MemoryError, match="graph_projection_invalid"):
        candidate.drop_projection("unused", GRAPH)
    assert admin.execute.call_count == 1


@pytest.mark.parametrize("role", [
    {"rolsuper": True, "rolbypassrls": False},
    {"rolsuper": False, "rolbypassrls": True},
])
def test_runtime_rejects_privileged_role_before_reading_metadata(role):
    conn = Mock()
    cursor = Mock()
    cursor.fetchone = AsyncMock(return_value=role)
    conn.execute = AsyncMock(return_value=cursor)
    with pytest.raises(MemoryError, match="graph_runtime_invalid"):
        asyncio.run(candidate._validate_runtime_projection(conn, GRAPH))
    assert conn.execute.await_count == 1
