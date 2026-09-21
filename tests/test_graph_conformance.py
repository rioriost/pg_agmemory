"""Independent, small canonical fixtures for SQL and future graph backend comparisons.

GraphFixture writes through the Native API; expected_graph enumerates bounded
edge products, not production traversal or neighbor SQL. Backend adapters can
reuse GraphFixture.request and assert_graph_contract with their own response.
"""

import json
from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import psycopg
import pytest

from pg_agmemory.scope_access import ScopeAccessRequest, scope_access

pytestmark = pytest.mark.integration

PREDICATES = ["depends_on", "part_of", "affects", "works_for", "decides"]
AS_OF = "2026-09-08T00:00:00Z"


def timestamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def wire_time(value):
    return (
        timestamp(value).astimezone(UTC).isoformat().replace("+00:00", "Z")
        if value is not None
        else None
    )


class GraphFixture:
    def __init__(self, env):
        self.env = env
        self.nodes = {}
        self.node_evidence = {}
        self.edges = {}

    def clock(self):
        with psycopg.connect(self.env.admin_url) as conn:
            return wire_time(conn.execute("SELECT clock_timestamp()").fetchone()[0])

    def node(self, name, *, index=0, label=None):
        label = label or name
        episode = self.env.observe(f"Entity evidence: {label}", index=index)
        assert episode.status_code == 201, episode.text
        evidence_id = episode.json()["memory_id"]
        response = self.env.client.post(
            "/v1/entities",
            headers=self.env.headers(index),
            json={
                "scope_id": str(self.env.scopes[index]),
                "entity_type": "component",
                "canonical_label": label,
                "evidence": [{"memory_id": evidence_id, "quote": label}],
                "explicit_intent": True,
            },
        )
        assert response.status_code == 201, response.text
        memory_id = response.json()["memory_id"]
        with psycopg.connect(self.env.admin_url) as conn:
            recorded = conn.execute(
                "SELECT created_at FROM memory.object WHERE id=%s", (memory_id,)
            ).fetchone()[0]
        self.nodes[name] = {
            "memory_id": memory_id,
            "revision": 1,
            "scope_id": str(self.env.scopes[index]),
            "entity_type": "component",
            "canonical_label": label,
            "recorded_at": wire_time(recorded),
        }
        self.node_evidence[name] = evidence_id
        return self.nodes[name]

    def edge(
        self,
        name,
        source,
        target,
        *,
        index=0,
        predicate="depends_on",
        valid_from=None,
        valid_to=None,
    ):
        episode = self.env.observe(f"Relation evidence: {name}", index=index)
        assert episode.status_code == 201, episode.text
        evidence = [
            {"memory_id": episode.json()["memory_id"], "quote": f"Relation evidence: {name}"}
        ]
        response = self.env.client.post(
            "/v1/relations",
            headers=self.env.headers(index),
            json={
                "scope_id": str(self.env.scopes[index]),
                "source_entity": self.nodes[source]["memory_id"],
                "target_entity": self.nodes[target]["memory_id"],
                "predicate": predicate,
                "evidence": evidence,
                "explicit_intent": True,
                "valid_from": valid_from,
                "valid_to": valid_to,
            },
        )
        assert response.status_code == 201, response.text
        self.edges[name] = {
            "source": source,
            "predicate": predicate,
            "evidence": evidence,
            "index": index,
            "revisions": [],
        }
        return self._revision(name, target, response.json(), valid_from, valid_to)

    def revise(self, name, target, *, valid_from=None, valid_to=None):
        edge = self.edges[name]
        previous = edge["revisions"][-1]["assertion"]
        response = self.env.client.post(
            f"/v1/relations/{previous['memory_id']}/revisions",
            headers=self.env.headers(edge["index"]),
            json={
                "expected_revision": previous["revision"],
                "target_entity": self.nodes[target]["memory_id"],
                "evidence": edge["evidence"],
                "explicit_intent": True,
                "reason": "Independent graph fixture target correction",
                "valid_from": valid_from,
                "valid_to": valid_to,
            },
        )
        assert response.status_code == 201, response.text
        return self._revision(name, target, response.json(), valid_from, valid_to)

    def _revision(self, name, target, receipt, valid_from, valid_to):
        edge = self.edges[name]
        reference = {key: receipt[key] for key in ("memory_id", "revision")}
        # Only server-assigned timestamps come from the DB; topology is fixture input.
        with psycopg.connect(self.env.admin_url) as conn:
            recorded = conn.execute(
                """SELECT lower(system_time) FROM memory.assertion_revision
                   WHERE assertion_id=%s AND revision=%s""",
                (reference["memory_id"], reference["revision"]),
            ).fetchone()[0]
        revision = {
            "assertion": reference,
            "source_entity": self.nodes[edge["source"]]["memory_id"],
            "target_entity": self.nodes[target]["memory_id"],
            "predicate": edge["predicate"],
            "valid_from": wire_time(valid_from),
            "valid_to": wire_time(valid_to),
            "recorded_at": wire_time(recorded),
            "epistemic_status": "reported",
        }
        edge["revisions"].append(revision)
        return revision

    def request(self, seed_names, **overrides):
        return {
            "scope_ids": [str(self.env.scopes[0])],
            "seeds": [self.nodes[name]["memory_id"] for name in seed_names],
            "relation_types": PREDICATES,
            "purpose": "Independent graph conformance",
            "direction": "outgoing",
            "max_hops": 2,
            "max_paths": 100,
            "as_of": AS_OF,
            "known_at": self.clock(),
            **overrides,
        }

    def expand(self, request):
        headers = self.env.headers()
        del headers["Idempotency-Key"]
        response = self.env.client.post("/v1/graph/expand", headers=headers, json=request)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def canonical_graph(env):
    return GraphFixture(env)


def expected_graph(
    graph, request, *, readable_scopes=None, excluded_nodes=(), excluded_edges=(), access_epoch=1
):
    """Enumerate all length-one/two simple walks, then order and slice globally.

    Authority and exclusions are explicit test expectations, never read back
    from production visibility predicates or the backend under test.
    """
    readable = (
        {str(graph.env.scopes[0])} if readable_scopes is None else set(readable_scopes)
    ) & set(request["scope_ids"])
    known, valid = timestamp(request["known_at"]), timestamp(request["as_of"])
    nodes = {
        node["memory_id"]: node
        for name, node in graph.nodes.items()
        if name not in excluded_nodes
        and node["scope_id"] in readable
        and timestamp(node["recorded_at"]) <= known
    }
    seeds = set(request["seeds"]) & nodes.keys()
    edges = {}
    for name, history in graph.edges.items():
        revisions = [
            edge for edge in history["revisions"] if timestamp(edge["recorded_at"]) <= known
        ]
        if name in excluded_edges or not revisions:
            continue
        edge = max(revisions, key=lambda row: row["assertion"]["revision"])
        if (
            edge["source_entity"] not in nodes
            or edge["target_entity"] not in nodes
            or edge["predicate"] not in request["relation_types"]
            or (edge["valid_from"] is not None and valid < timestamp(edge["valid_from"]))
            or (edge["valid_to"] is not None and valid >= timestamp(edge["valid_to"]))
        ):
            continue
        edges[(edge["assertion"]["memory_id"], edge["assertion"]["revision"])] = edge

    oriented = []
    for ref, edge in edges.items():
        source, target = edge["source_entity"], edge["target_entity"]
        if request["direction"] in ("outgoing", "both"):
            oriented.append((source, target, ref))
        if request["direction"] in ("incoming", "both"):
            oriented.append((target, source, ref))
    candidates = []
    for hops in range(1, request["max_hops"] + 1):
        for walk in product(oriented, repeat=hops):
            path_nodes = [walk[0][0], *(step[1] for step in walk)]
            if (
                path_nodes[0] in seeds
                and len(set(path_nodes)) == len(path_nodes)
                and all(left[1] == right[0] for left, right in zip(walk, walk[1:], strict=False))
            ):
                order = (hops, path_nodes[0], tuple((*step[2], step[1]) for step in walk))
                candidates.append(
                    (
                        order,
                        {
                            "nodes": path_nodes,
                            "assertions": [edges[step[2]]["assertion"] for step in walk],
                        },
                    )
                )
    candidates.sort(key=lambda entry: entry[0])
    paths = [path for _, path in candidates[: request["max_paths"]]]
    selected_nodes = seeds | {node for path in paths for node in path["nodes"]}
    selected_edges = {
        (ref["memory_id"], ref["revision"]) for path in paths for ref in path["assertions"]
    }
    truncated = len(candidates) > request["max_paths"]
    return {
        "as_of": wire_time(request["as_of"]),
        "known_at": wire_time(request["known_at"]),
        "nodes": [nodes[key] for key in sorted(selected_nodes)],
        "edges": [edges[key] for key in sorted(selected_edges)],
        "paths": paths,
        "coverage": {
            "max_hops": request["max_hops"],
            "truncated": truncated,
            "complete_within_bounds": not truncated,
        },
        "consistency": {"access_epoch": access_epoch, "deletion_epoch": 1},
        "empty_reason": None if paths else "not_found",
    }


def assert_graph_contract(result, graph, request, *, backend="sql", watermark=None, **visibility):
    assert result == {
        "backend": backend,
        "projection_watermark": watermark,
        **expected_graph(graph, request, **visibility),
    }
    encoded = json.dumps(result)
    assert all(evidence not in encoded for evidence in graph.node_evidence.values())
    assert "Relation evidence:" not in encoded and "Entity evidence:" not in encoded


@pytest.mark.parametrize("direction", ["outgoing", "incoming", "both"])
def test_multiseed_parallel_cycles_and_global_prefix_budgets(canonical_graph, direction):
    graph = canonical_graph
    for name in ("a", "b", "c", "d", "isolated"):
        graph.node(name, label="Same label" if name in ("b", "d") else name)
    for name, source, target, predicate in [
        ("self-a", "a", "a", "depends_on"),
        ("self-b", "b", "b", "depends_on"),
        ("parallel-1", "a", "b", "depends_on"),
        ("parallel-2", "a", "b", "depends_on"),
        ("bc", "b", "c", "depends_on"),
        ("ad", "a", "d", "affects"),
        ("dc", "d", "c", "part_of"),
        ("ca", "c", "a", "decides"),
        ("da", "d", "a", "works_for"),
    ]:
        graph.edge(name, source, target, predicate=predicate)
    seeds = sorted(
        ("a", "c", "isolated"), key=lambda name: graph.nodes[name]["memory_id"], reverse=True
    )
    request = graph.request(seeds, direction=direction)
    one_hop_count = len(expected_graph(graph, {**request, "max_hops": 1})["paths"])
    for hops in (1, 2):
        full = expected_graph(graph, {**request, "max_hops": hops})
        total = len(full["paths"])
        assert 1 < one_hop_count <= total < 100
        for cap in sorted({1, one_hop_count, total - 1, total, total + 1, 100}):
            bounded = {**request, "max_hops": hops, "max_paths": cap}
            assert_graph_contract(graph.expand(bounded), graph, bounded)
    for predicates in (["depends_on"], ["affects", "part_of"], ["works_for", "decides"]):
        narrowed = {**request, "relation_types": predicates}
        assert_graph_contract(graph.expand(narrowed), graph, narrowed)
    full_paths = expected_graph(graph, request)["paths"]
    assert len({tuple(path["nodes"]) for path in full_paths}) < len(full_paths)


@pytest.mark.parametrize("direction", ["outgoing", "incoming", "both"])
def test_second_hop_target_revision_and_half_open_times(canonical_graph, direction):
    graph = canonical_graph
    for name in ("a", "b", "old", "new"):
        graph.node(name)
    graph.edge("ab", "a", "b")
    old = graph.edge(
        "revised",
        "b",
        "old",
        valid_from="2026-09-01T00:00:00Z",
        valid_to="2026-09-10T00:00:00Z",
    )
    graph.edge("return", "new", "a", predicate="affects")
    revised = graph.revise(
        "revised",
        "new",
        valid_from="2026-09-05T00:00:00Z",
        valid_to="2026-09-09T00:00:00Z",
    )
    boundary = revised["recorded_at"]
    before = wire_time(timestamp(boundary) - timedelta(microseconds=1))
    assert timestamp(old["recorded_at"]) <= timestamp(before) < timestamp(boundary)
    graph.node("late")
    for valid, known in [
        (AS_OF, before),
        (AS_OF, boundary),
        ("2026-09-04T23:59:59.999999Z", boundary),
        ("2026-09-05T09:00:00+09:00", boundary),
        ("2026-09-08T23:59:59.999999Z", boundary),
        ("2026-09-09T00:00:00Z", boundary),
        ("2026-09-09T00:00:00Z", before),
    ]:
        request = graph.request(
            ["new", "late", "a", "old"], direction=direction, as_of=valid, known_at=known
        )
        assert_graph_contract(graph.expand(request), graph, request)


def test_scope_seed_filters_and_historical_current_acl(canonical_graph):
    graph = canonical_graph
    env = graph.env
    for index in range(3):
        graph.node(f"source-{index}", index=index, label=f"Private source {index}")
        graph.node(f"target-{index}", index=index, label=f"Private target {index}")
        graph.edge(f"edge-{index}", f"source-{index}", f"target-{index}", index=index)
    with scope_access(
        env.admin_url,
        ScopeAccessRequest(
            operation="set",
            tenant_id=env.tenants[0],
            scope_id=env.scopes[2],
            principal_id=env.principals[0],
            expected_access_epoch=1,
            permissions=("read",),
            no_expiry=True,
        ),
    ):
        pass
    scopes = [str(scope) for scope in env.scopes] + [str(uuid4()) for _ in range(29)]
    seeds = [graph.nodes[f"source-{index}"]["memory_id"] for index in range(3)]
    seeds += [str(uuid4()) for _ in range(13)]
    request = graph.request([], scope_ids=scopes, seeds=seeds)
    readable = {str(env.scopes[0]), str(env.scopes[2])}
    cases = [
        request,
        {**request, "scope_ids": [str(env.scopes[0])]},
        {**request, "seeds": [graph.nodes["source-0"]["memory_id"]]},
        {
            **request,
            "scope_ids": [str(env.scopes[0])],
            "seeds": [graph.nodes["source-2"]["memory_id"]],
        },
        {**request, "scope_ids": [str(env.scopes[1]), scopes[-1]]},
    ]
    for case in cases:
        result = graph.expand(case)
        assert_graph_contract(result, graph, case, readable_scopes=readable, access_epoch=2)
        assert "Private source 1" not in json.dumps(result)
    with scope_access(
        env.admin_url,
        ScopeAccessRequest(
            operation="revoke",
            tenant_id=env.tenants[0],
            scope_id=env.scopes[2],
            principal_id=env.principals[0],
            expected_access_epoch=2,
        ),
    ):
        pass
    # Reuse the pre-revocation known_at: historical reads cannot restore membership.
    result = graph.expand(request)
    assert_graph_contract(result, graph, request, access_epoch=3)
    for index in (1, 2):
        assert f"Private source {index}" not in json.dumps(result)
        assert f"Private target {index}" not in json.dumps(result)


@pytest.mark.parametrize("direction", ["outgoing", "incoming", "both"])
@pytest.mark.parametrize("hidden", ["node", "node_evidence", "edge"])
def test_retained_payload_cannot_bridge_or_leak_at_historical_time(
    canonical_graph, direction, hidden
):
    graph = canonical_graph
    env = graph.env
    for name in ("a", "secret", "z", "safe"):
        graph.node(name, label=f"Label-{name}")
    graph.edge("enter-secret", "a", "secret")
    graph.edge("leave-secret", "secret", "z")
    graph.edge("enter-safe", "a", "safe")
    graph.edge("leave-safe", "safe", "z")
    seed = "z" if direction == "incoming" else "a"
    hidden_edge = "leave-secret" if direction == "incoming" else "enter-secret"
    seeds = [seed] if hidden == "edge" else [seed, "secret"]
    request = graph.request(seeds, direction=direction)
    assert_graph_contract(graph.expand(request), graph, request)
    hidden_id = {
        "node": graph.nodes["secret"]["memory_id"],
        "node_evidence": graph.node_evidence["secret"],
        "edge": graph.edges[hidden_edge]["revisions"][0]["assertion"]["memory_id"],
    }[hidden]
    # Fault injection deliberately retains payload; canonical forget would purge its closure.
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "ALTER TABLE memory_ops.object_tombstone DISABLE TRIGGER tombstone_manifest_complete"
        )
        deleted_at = conn.execute(
            """INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id)
               VALUES (%s,%s,%s) RETURNING deleted_at""",
            (env.tenants[0], hidden_id, env.scopes[0]),
        ).fetchone()[0]
        conn.execute(
            "ALTER TABLE memory_ops.object_tombstone ENABLE TRIGGER tombstone_manifest_complete"
        )
        assert (
            conn.execute(
                "SELECT canonical_label FROM memory.entity WHERE id=%s",
                (graph.nodes["secret"]["memory_id"],),
            ).fetchone()[0]
            == "Label-secret"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.relation WHERE tenant_id=%s", (env.tenants[0],)
            ).fetchone()[0]
            == 4
        )
    assert timestamp(request["known_at"]) < deleted_at
    result = graph.expand(request)
    assert_graph_contract(
        result,
        graph,
        request,
        excluded_nodes=() if hidden == "edge" else ("secret",),
        excluded_edges=(hidden_edge,) if hidden == "edge" else (),
    )
    encoded = json.dumps(result)
    assert hidden_id not in encoded
    assert graph.nodes["secret"]["memory_id"] not in encoded
    assert "Label-secret" not in encoded
    assert result["paths"] and result["coverage"]["complete_within_bounds"]


def test_default_time_capture_and_isolated_loop_only_seeds(canonical_graph):
    graph = canonical_graph
    graph.node("loop")
    graph.node("isolated")
    graph.edge("self", "loop", "loop")
    request = graph.request(["loop", "isolated"], direction="both", max_paths=1)
    del request["as_of"], request["known_at"]
    before = graph.clock()
    result = graph.expand(request)
    after = graph.clock()
    assert timestamp(before) <= timestamp(result["as_of"]) <= timestamp(after)
    assert result["as_of"] == result["known_at"]
    assert_graph_contract(
        result, graph, {**request, "as_of": result["as_of"], "known_at": result["known_at"]}
    )
    assert len(result["nodes"]) == 2
    assert result["paths"] == result["edges"] == []
    assert result["coverage"]["truncated"] is False
