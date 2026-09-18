import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from pg_agmemory.database import connect
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import EntityPage, Identity, MemoryItem, QueryEntities
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryError, MemoryService, build_context

pytestmark = pytest.mark.integration


def entity_body(env, label, index=0, **overrides):
    source = env.observe(f"Entity: {label}", index=index).json()["memory_id"]
    return {
        "scope_id": str(env.scopes[index]),
        "entity_type": "component",
        "canonical_label": label,
        "evidence": [{"memory_id": source, "quote": label}],
        "explicit_intent": True,
        **overrides,
    }


def create_entity(env, label, index=0, **overrides):
    body = entity_body(env, label, index, **overrides)
    headers = env.headers(index)
    response = env.client.post("/v1/entities", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return {**response.json(), "body": body, "headers": headers}


def query_entities(env, index=0, **overrides):
    headers = env.headers(index)
    del headers["Idempotency-Key"]
    return env.client.post(
        "/v1/entities/query",
        headers=headers,
        json={"scope_ids": [str(env.scopes[index])], **overrides},
    )


def entity_ids(response):
    assert response.status_code == 200, response.text
    return [row["memory_id"] for row in response.json()["entities"]]


def test_entity_query_exact_labels_types_and_metadata_do_not_merge_identities(env):
    types = [
        "person",
        "organization",
        "project",
        "component",
        "incident",
        "task",
        "decision",
        "other",
    ]
    typed = [create_entity(env, "Same", entity_type=kind) for kind in types]
    assert entity_ids(query_entities(env, canonical_label=" Same ")) == [
        item["memory_id"] for item in reversed(typed)
    ]
    for kind, item in zip(types, typed, strict=True):
        assert entity_ids(query_entities(env, entity_type=kind, canonical_label="Same")) == [
            item["memory_id"]
        ]
    labels = ["same", "Same suffix", "東京", "東", "%_", "é", "e\u0301", "Ｅ"]
    distinct = [create_entity(env, label) for label in labels]
    for label, item in zip(labels, distinct, strict=True):
        result = query_entities(env, canonical_label=label, entity_type="component")
        assert entity_ids(result) == [item["memory_id"]]
        detail = env.client.get("/v1/entities/" + item["memory_id"], headers=env.headers()).json()
        assert result.json()["entities"][0] == {
            key: value for key, value in detail.items() if key != "evidence"
        }
        assert (
            "evidence" not in result.text
            and item["body"]["evidence"][0]["memory_id"] not in result.text
        )
    for label in ("Sam", "E", "unknown"):
        assert entity_ids(query_entities(env, canonical_label=label)) == []
    assert entity_ids(query_entities(env, canonical_label="東京", entity_type="person")) == []
    assert (
        query_entities(env).json()
        == query_entities(env, canonical_label=None, entity_type=None, before=None).json()
    )


def test_entity_query_scopes_and_shared_read_access_apply_before_page_limits(env):
    own = create_entity(env, "Shared")
    foreign = create_entity(env, "Shared", index=1)
    shared = create_entity(env, "Shared", index=2)
    scopes = [str(scope) for scope in env.scopes] + [str(uuid4())]
    first = query_entities(env, scope_ids=scopes, max_items=1)
    assert entity_ids(first) == [own["memory_id"]] and first.json()["next_cursor"] is None
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
    page = query_entities(env, scope_ids=scopes)
    assert entity_ids(page) == [shared["memory_id"], own["memory_id"]]
    assert foreign["memory_id"] not in page.text and page.json()["consistency"]["access_epoch"] == 2
    assert entity_ids(query_entities(env, scope_ids=[str(env.scopes[1]), str(uuid4())])) == []


def test_entity_query_exact_hundred_item_page_and_uuid_ties(env):
    source = env.observe("Same").json()["memory_id"]
    entities = []
    for _ in range(101):
        response = env.client.post(
            "/v1/entities",
            headers=env.headers(),
            json={
                "scope_id": str(env.scopes[0]),
                "entity_type": "component",
                "canonical_label": "Same",
                "explicit_intent": True,
                "evidence": [{"memory_id": source, "quote": "Same"}],
            },
        )
        assert response.status_code == 201
        entities.append(response.json()["memory_id"])
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory.object SET created_at='2026-09-01T00:00:00Z' WHERE tenant_id=%s",
            (env.tenants[0],),
        )
    expected = sorted(entities, reverse=True)
    first = query_entities(env, max_items=100)
    assert entity_ids(first) == expected[:100]
    assert first.json()["next_cursor"] == {
        "recorded_at": "2026-09-01T00:00:00Z",
        "memory_id": expected[99],
    }
    second = query_entities(env, max_items=100, before=first.json()["next_cursor"])
    assert entity_ids(second) == expected[100:] and second.json()["next_cursor"] is None
    assert len(first.content) < 2 * 1024 * 1024
    assert (
        entity_ids(
            query_entities(
                env,
                before={
                    "recorded_at": "2026-09-01T00:00:00Z",
                    "memory_id": expected[-1],
                },
            )
        )
        == []
    )


def test_entity_cursor_survives_deletion_and_does_not_freeze_new_rows_or_permissions(env):
    oldest, newest = [create_entity(env, "Same") for _ in range(2)]
    first = query_entities(env, max_items=1)
    assert entity_ids(first) == [newest["memory_id"]]
    before = first.json()["next_cursor"]
    later = create_entity(env, "Same")
    assert entity_ids(query_entities(env, before=before)) == [oldest["memory_id"]]
    deleted = env.client.post(
        "/v1/forget",
        headers=env.headers(),
        json={
            "memory_ids": [newest["body"]["evidence"][0]["memory_id"]],
            "reason": "test",
        },
    )
    assert deleted.status_code == 202 and deleted.json()["object_count"] == 2
    page = query_entities(env, before=before)
    assert entity_ids(page) == [oldest["memory_id"]]
    assert page.json()["consistency"]["deletion_epoch"] == 2
    assert entity_ids(
        query_entities(
            env,
            before={
                "recorded_at": "2100-01-01T00:00:00Z",
                "memory_id": str(uuid4()),
            },
        )
    ) == [later["memory_id"], oldest["memory_id"]]
    assert (
        entity_ids(
            query_entities(
                env,
                before={
                    "recorded_at": "1900-01-01T00:00:00Z",
                    "memory_id": str(uuid4()),
                },
            )
        )
        == []
    )
    for operation, epoch, permissions in (("set", 1, ("read",)), ("revoke", 2, ())):
        options = {"permissions": permissions, "no_expiry": True} if operation == "set" else {}
        with scope_access(
            env.admin_url,
            ScopeAccessRequest(
                operation=operation,
                tenant_id=env.tenants[0],
                scope_id=env.scopes[0],
                principal_id=env.principals[0],
                expected_access_epoch=epoch,
                **options,
            ),
        ):
            pass
        page = query_entities(env, before=before)
        assert entity_ids(page) == ([oldest["memory_id"]] if operation == "set" else [])
        assert page.json()["consistency"]["access_epoch"] == epoch + 1


def test_entity_query_rejects_incomplete_selected_evidence_without_partial_page(env, monkeypatch):
    entities = [create_entity(env, name) for name in ("A", "B")]
    original = psycopg.AsyncCursor.fetchall

    async def damaged(cursor):
        rows = await original(cursor)
        if rows and isinstance(rows[0], dict) and "evidence_count" in rows[0]:
            rows[-1]["evidence_count"] = 0
        return rows

    monkeypatch.setattr(psycopg.AsyncCursor, "fetchall", damaged)
    result = query_entities(env)
    assert result.status_code == 409 and result.json()["code"] == "entity_invalidated"
    assert "entities" not in result.json()
    assert all(item["memory_id"] not in result.text for item in entities)


def test_entity_query_is_read_only_and_has_no_audit_or_receipt_side_effect(env):
    create_entity(env, "A")
    statement = """SELECT
        (SELECT count(*) FROM memory_ops.audit_event WHERE tenant_id=%s),
        (SELECT count(*) FROM memory_ops.idempotency WHERE tenant_id=%s)"""
    with psycopg.connect(env.admin_url) as conn:
        before = conn.execute(statement, (env.tenants[0],) * 2).fetchone()

    async def read_only():
        async with await connect(env.settings.database_url) as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute(
                    """SELECT set_config('pgag.tenant_id',%s,true),
                              set_config('pgag.principal_id',%s,true)""",
                    (str(env.tenants[0]), str(env.principals[0])),
                )
                memory = MemoryService(
                    conn,
                    Identity(tenant_id=env.tenants[0], principal_id=env.principals[0]),
                )
                return await SqlGraph(memory).query_entities(
                    QueryEntities(scope_ids=[env.scopes[0]])
                )

    direct = EntityPage.model_validate(asyncio.run(read_only())).model_dump(mode="json")
    assert direct == query_entities(env).json()
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(statement, (env.tenants[0],) * 2).fetchone() == before


def test_entity_query_openapi_auth_body_and_empty_contract(env):
    assert query_entities(env).json() == {
        "entities": [],
        "next_cursor": None,
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
    }
    body = {"scope_ids": [str(env.scopes[0])]}
    assert env.client.post("/v1/entities/query", json=body).status_code == 401
    raw = json.dumps(body).encode()
    padded = raw + b" " * (262144 - len(raw))
    headers = {**env.headers(), "Content-Type": "application/json"}
    assert env.client.post("/v1/entities/query", content=padded, headers=headers).status_code == 200
    assert (
        env.client.post(
            "/v1/entities/query",
            content=padded + b" ",
            headers=headers,
        ).status_code
        == 413
    )
    invalid = query_entities(env, principal_id="PRIVATE")
    assert invalid.status_code == 422 and "PRIVATE" not in invalid.text
    cap = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert cap["entity_query"] == {
        "endpoint": "/v1/entities/query",
        "match": "exact",
        "order": ["recorded_at_desc", "memory_id_desc"],
        "pagination": "exclusive_keyset",
        "max_items": 100,
    }
    operation = env.client.get("/openapi.json").json()["paths"]["/v1/entities/query"]["post"]
    assert operation["security"] == [{"BearerAuth": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EntityPage",
    }


def relation_body(env, source, target, index=0, **overrides):
    episode = env.observe("Reported relation", index=index).json()["memory_id"]
    return {
        "scope_id": str(env.scopes[index]),
        "source_entity": source["memory_id"],
        "target_entity": target["memory_id"],
        "predicate": "depends_on",
        "evidence": [{"memory_id": episode, "quote": "Reported relation"}],
        "explicit_intent": True,
        **overrides,
    }


def create_relation(env, source, target, index=0, **overrides):
    body = relation_body(env, source, target, index, **overrides)
    headers = env.headers(index)
    response = env.client.post("/v1/relations", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return {**response.json(), "body": body, "headers": headers}


def revise_body(relation, target, **overrides):
    return {
        "expected_revision": 1,
        "target_entity": target["memory_id"],
        "evidence": relation["body"]["evidence"],
        "explicit_intent": True,
        "reason": "Corrected target",
        **overrides,
    }


def explain(env, relation, revision=1, index=0):
    return env.client.post(
        "/v1/explain",
        json={"memory_id": relation["memory_id"], "revision": revision},
        headers=env.headers(index),
    )


def expand_body(env, seed, index=0, **overrides):
    return {
        "scope_ids": [str(env.scopes[index])],
        "seeds": [seed["memory_id"]],
        "relation_types": ["depends_on", "affects"],
        "purpose": "graph contract",
        **overrides,
    }


def expand(env, seed, index=0, **overrides):
    response = env.client.post(
        "/v1/graph/expand",
        json=expand_body(env, seed, index, **overrides),
        headers=env.headers(index),
    )
    assert response.status_code == 200, response.text
    return response.json()


def checkpoint(env, refs=(), index=0):
    response = env.client.post(
        "/v1/checkpoints",
        headers=env.headers(index),
        json={
            "scope_id": str(env.scopes[index]),
            "run_id": str(uuid4()),
            "branch_id": str(uuid4()),
            "expected_head": None,
            "harness_id": "graph-tests",
            "harness_version": "1",
            "event_watermark": 1,
            "state": {"goal": "Keep exact graph references"},
            "memory_refs": [
                {"memory_id": item["memory_id"], "revision": item["revision"]} for item in refs
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_entity_evidence_identity_and_relation_recall_explain(env):
    first = create_entity(env, "同じ名前")
    second = create_entity(env, "同じ名前")
    assert first["memory_id"] != second["memory_id"]
    assert (
        env.client.post("/v1/entities", json=first["body"], headers=first["headers"]).json()[
            "memory_id"
        ]
        == first["memory_id"]
    )
    changed = env.client.post(
        "/v1/entities",
        json={**first["body"], "canonical_label": "Different"},
        headers=first["headers"],
    )
    assert changed.status_code == 409
    detail = env.client.get("/v1/entities/" + first["memory_id"], headers=env.headers()).json()
    assert detail["canonical_label"] == "同じ名前"
    assert detail["evidence"][0]["memory_id"] == first["body"]["evidence"][0]["memory_id"]
    relation = create_relation(env, first, second)
    explanation = explain(env, relation).json()
    assert explanation["relation"] == {
        "source_entity": first["memory_id"],
        "target_entity": second["memory_id"],
    }
    assert explanation["epistemic_status"] == "reported"
    assert explanation["confidence"]["score"] is None
    recall = env.recall(query="同じ名前").json()
    item = next(item for item in recall["items"] if item["memory_id"] == relation["memory_id"])
    assert item["relation"] == explanation["relation"]
    assert first["memory_id"] + "->" + second["memory_id"] in recall["context_pack"]["text"]
    memory_item = MemoryItem.model_validate(item)
    pack, _, _ = build_context([memory_item], 8000)
    assert build_context([memory_item], pack["byte_count"])[1] == [memory_item]
    assert build_context([memory_item], pack["byte_count"] - 1)[1:] == ([], True)
    assert recall["coverage"]["graph_used"] is False
    assert all(item["memory_id"] != first["memory_id"] for item in recall["items"])
    assert (
        env.client.post(
            "/v1/explain", json={"memory_id": first["memory_id"]}, headers=env.headers()
        ).status_code
        == 404
    )
    free = env.remember(
        relation["body"]["evidence"][0]["memory_id"],
        subject="同じ名前",
        predicate="depends_on",
        value="同じ名前",
        evidence=relation["body"]["evidence"],
    )
    assert free.status_code == 201
    assert explain(env, free.json()).json()["relation"] is None
    graph = expand(env, first)
    assert [edge["assertion"]["memory_id"] for edge in graph["edges"]] == [relation["memory_id"]]


@pytest.mark.parametrize("direction", ["outgoing", "incoming", "both"])
def test_graph_paths_match_handcrafted_two_hop_oracle(env, direction):
    a, b, c, d = [create_entity(env, name) for name in ["A", "B", "C", "D"]]
    relations = [
        create_relation(env, a, b),
        create_relation(env, b, c),
        create_relation(env, a, d, predicate="affects"),
        create_relation(env, d, c),
        create_relation(env, c, a),
    ]
    directed = []
    for relation in relations:
        body = relation["body"]
        if direction in ("outgoing", "both"):
            directed.append((body["source_entity"], body["target_entity"], relation["memory_id"]))
        if direction in ("incoming", "both"):
            directed.append((body["target_entity"], body["source_entity"], relation["memory_id"]))
    one_hop = sorted((edge for edge in directed if edge[0] == a["memory_id"]), key=lambda e: e[2])
    expected = [([edge[0], edge[1]], [edge[2]]) for edge in one_hop]
    for first in one_hop:
        for second in sorted(directed, key=lambda e: e[2]):
            if second[0] == first[1] and second[1] not in first[:2]:
                expected.append(([first[0], first[1], second[1]], [first[2], second[2]]))
    result = expand(env, a, direction=direction)
    actual = [
        (path["nodes"], [ref["memory_id"] for ref in path["assertions"]])
        for path in result["paths"]
    ]
    assert actual == expected
    assert all(len(set(nodes)) == len(nodes) for nodes, _ in actual)
    assert result["backend"] == "sql" and result["projection_watermark"] is None
    assert result["coverage"] == {
        "complete_within_bounds": True,
        "truncated": False,
        "max_hops": 2,
    }
    assert {node["memory_id"] for node in result["nodes"]} == {
        node for nodes, _ in expected for node in nodes
    }
    assert all("evidence" not in node for node in result["nodes"])
    assert all(edge["assertion"]["revision"] == 1 for edge in result["edges"])
    single = expand(env, a, direction=direction, max_hops=1)
    assert len(single["paths"]) == len(one_hop)
    limited = expand(env, a, direction=direction, max_paths=1)
    assert limited["paths"] == single["paths"][:1]
    assert limited["coverage"]["truncated"] is True
    absent = expand(env, a, relation_types=["works_for"])
    assert absent["paths"] == [] and absent["empty_reason"] == "not_found"
    assert [node["memory_id"] for node in absent["nodes"]] == [a["memory_id"]]


def test_relation_corrections_filter_every_hop_at_both_times(env):
    a, b, c, d = [create_entity(env, name) for name in ["A", "B", "C", "D"]]
    first = create_relation(env, a, b, valid_from="2026-09-01T00:00:00Z")
    second = create_relation(env, b, c)
    known_before = explain(env, second).json()["assertion"]["recorded_at"]
    updated = env.client.post(
        "/v1/relations/" + first["memory_id"] + "/revisions",
        headers=env.headers(),
        json=revise_body(
            first, d, valid_from="2026-09-05T00:00:00Z", valid_to="2026-09-10T00:00:00Z"
        ),
    )
    assert updated.status_code == 201, updated.text
    known_after = explain(env, first, 2).json()["assertion"]["recorded_at"]
    for as_of, known, target, revision, count in [
        ("2026-09-03T00:00:00Z", known_before, b, 1, 2),
        ("2026-09-03T00:00:00Z", known_after, None, None, 0),
        ("2026-09-05T00:00:00Z", known_after, d, 2, 1),
        ("2026-09-10T00:00:00Z", known_after, None, None, 0),
        ("2026-09-08T00:00:00+09:00", known_before, b, 1, 2),
    ]:
        result = expand(env, a, as_of=as_of, known_at=known)
        assert len(result["paths"]) == count
        if target:
            assert result["paths"][0]["nodes"] == [a["memory_id"], target["memory_id"]]
            assert result["paths"][0]["assertions"][0]["revision"] == revision
    boundary_before = (datetime.fromisoformat(known_after) - timedelta(microseconds=1)).isoformat()
    assert expand(env, a, known_at=boundary_before)["paths"][0]["assertions"][0]["revision"] == 1
    assert expand(env, a, known_at="2000-01-01T00:00:00Z")["nodes"] == []
    assert explain(env, first).json()["relation"]["target_entity"] == b["memory_id"]
    assert explain(env, first, 2).json()["relation"]["target_entity"] == d["memory_id"]
    timeline = env.client.post(
        "/v1/assertions/history", headers=env.headers(), json={"memory_id": first["memory_id"]}
    )
    assert timeline.status_code == 200
    assert [row["revision"] for row in timeline.json()["revisions"]] == [2, 1]
    for row in timeline.json()["revisions"]:
        full = explain(env, first, row["revision"]).json()
        assert row["relation"] == full["relation"]
        assert row["valid_from"] == full["assertion"]["valid_from"]
        assert row["known_until"] == full["assertion"]["known_until"]
    forbidden = env.client.post(
        "/v1/assertions/" + first["memory_id"] + "/revisions",
        headers=env.headers(),
        json={
            "expected_revision": 2,
            "value": "Unlinked text",
            "explicit_intent": True,
            "reason": "Cannot bypass identity",
            "evidence": first["body"]["evidence"],
        },
    )
    assert forbidden.status_code == 409 and forbidden.json()["code"] == "relation_revision_required"


def test_relation_history_missing_endpoint_fails_whole_page(env, monkeypatch):
    source, target = [create_entity(env, name) for name in ("A", "B")]
    relation = create_relation(env, source, target)
    original = psycopg.AsyncCursor.fetchall

    async def damaged(cursor):
        rows = await original(cursor)
        if rows and isinstance(rows[0], dict) and "evidence_refs" in rows[0]:
            rows[0]["target_id"] = None
        return rows

    monkeypatch.setattr(psycopg.AsyncCursor, "fetchall", damaged)
    result = env.client.post(
        "/v1/assertions/history",
        headers=env.headers(),
        json={"memory_id": relation["memory_id"]},
    )
    assert result.status_code == 409 and result.json()["code"] == "relation_invalidated"
    assert "revisions" not in result.json() and relation["memory_id"] not in result.text


def test_parallel_entity_relation_dedup_and_revision_cas(env):
    body = entity_body(env, "A")
    headers = env.headers()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: env.client.post("/v1/entities", json=body, headers=headers), range(4)
            )
        )
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["memory_id"] for response in responses}) == 1
    a = responses[0].json()
    b, c, d = [create_entity(env, name) for name in ["B", "C", "D"]]
    relation = relation_body(env, a, b)
    key = env.headers()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: env.client.post("/v1/relations", json=relation, headers=key), range(4)
            )
        )
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["memory_id"] for response in responses}) == 1
    anchor = {**responses[0].json(), "body": relation}
    url = "/v1/relations/" + anchor["memory_id"] + "/revisions"
    bodies = [revise_body(anchor, target) for target in (c, d)]
    keys = [env.headers(), env.headers()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda i: env.client.post(url, json=bodies[i], headers=keys[i]), range(2))
        )
    assert sorted(response.status_code for response in replies) == [201, 409]
    winner = next(i for i, response in enumerate(replies) if response.status_code == 201)
    assert (
        env.client.post(url, json=bodies[winner], headers=keys[winner]).json()
        == replies[winner].json()
    )
    assert env.client.post("/v1/relations", json=relation, headers=key).json()["revision"] == 1
    assert explain(env, anchor, 2).status_code == 200


def test_graph_scopes_do_not_resolve_equal_labels_or_expose_private_ids(env):
    pairs = [[create_entity(env, "Same", i), create_entity(env, "Same", i)] for i in range(3)]
    relations = [create_relation(env, pair[0], pair[1], i) for i, pair in enumerate(pairs)]
    private = [item["memory_id"] for pair in pairs[1:] for item in pair]
    private += [relation["memory_id"] for relation in relations[1:]]
    response = env.client.post(
        "/v1/graph/expand",
        headers=env.headers(),
        json=expand_body(
            env,
            pairs[0][0],
            scope_ids=[str(scope) for scope in env.scopes],
            seeds=[pair[0]["memory_id"] for pair in pairs],
        ),
    )
    assert response.status_code == 200
    assert all(memory_id not in response.text for memory_id in private)
    assert len(response.json()["edges"]) == 1
    assert expand(env, pairs[1][0])["nodes"] == []
    for index in (1, 2):
        assert (
            env.client.get(
                "/v1/entities/" + pairs[0][0]["memory_id"], headers=env.headers(index)
            ).status_code
            == 404
        )
        assert explain(env, relations[0], index=index).status_code == 404
    cross = relation_body(env, pairs[0][0], pairs[2][0])
    assert env.client.post("/v1/relations", json=cross, headers=env.headers()).status_code == 404
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read'])""",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    assert env.client.post("/v1/relations", json=cross, headers=env.headers()).status_code == 422
    assert expand(env, pairs[2][0], scope_ids=[str(env.scopes[2])])["paths"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """DELETE FROM memory.scope_member
               WHERE tenant_id = %s AND scope_id = %s AND principal_id = %s""",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    assert expand(env, pairs[2][0], scope_ids=[str(env.scopes[2])])["nodes"] == []


@pytest.mark.parametrize("hide_source", [False, True])
def test_hidden_intermediate_nodes_cannot_bridge_visible_endpoints(env, hide_source):
    a, b, c = [create_entity(env, name) for name in ["A", "Private B", "C"]]
    first, second = create_relation(env, a, b), create_relation(env, b, c)
    hidden = b["body"]["evidence"][0]["memory_id"] if hide_source else b["memory_id"]
    # Simulate an incomplete out-of-protocol administrative deletion to exercise read guards.
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id)
               VALUES (%s,%s,%s)""",
            (env.tenants[0], hidden, env.scopes[0]),
        )
    result = expand(env, a)
    assert result["paths"] == [] and result["edges"] == []
    assert [node["memory_id"] for node in result["nodes"]] == [a["memory_id"]]
    for item in (b, c, first, second):
        assert item["memory_id"] not in str(result)


@pytest.mark.parametrize("delete_source", [False, True])
def test_entity_erasure_purges_historical_links_checkpoints_and_effects(env, delete_source):
    a = create_entity(env, "A")
    b, c = create_entity(env, "Same"), create_entity(env, "Same")
    relation = create_relation(env, a, b)
    saved = checkpoint(env, [relation, b])
    changed = env.client.post(
        "/v1/relations/" + relation["memory_id"] + "/revisions",
        json=revise_body(relation, c),
        headers=env.headers(),
    )
    assert changed.status_code == 201
    bootstrap = checkpoint(env)
    effect = env.client.post(
        "/v1/tool-effects",
        headers=env.headers(),
        json={
            "scope_id": str(env.scopes[0]),
            "run_id": bootstrap["run_id"],
            "operation_id": str(uuid4()),
            "tool_name": "test.graph",
            "action_hash": "a" * 64,
            "memory_refs": [{"memory_id": b["memory_id"]}],
        },
    )
    assert effect.status_code == 201, effect.text
    root = b["body"]["evidence"][0]["memory_id"] if delete_source else b["memory_id"]
    preview = env.client.post(
        "/v1/forget",
        json={"memory_ids": [root], "reason": "test", "mode": "preview"},
        headers=env.headers(),
    )
    assert preview.status_code == 202 and preview.json()["object_count"] == 5 + delete_source
    deleted = env.client.post(
        "/v1/forget",
        json={"memory_ids": [root], "reason": "test"},
        headers=env.headers(),
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["object_count"] == preview.json()["object_count"]
    assert explain(env, relation).status_code == explain(env, relation, 2).status_code == 404
    assert (
        env.client.get("/v1/entities/" + b["memory_id"], headers=env.headers()).status_code == 404
    )
    assert (
        env.client.get("/v1/entities/" + c["memory_id"], headers=env.headers()).status_code == 200
    )
    for cp in (saved, bootstrap):
        assert (
            env.client.get(
                "/v1/checkpoints/" + cp["checkpoint_id"], headers=env.headers()
            ).status_code
            == 404
        )
    assert (
        env.client.get(
            "/v1/tool-effects/" + effect.json()["memory_id"], headers=env.headers()
        ).status_code
        == 404
    )
    assert env.client.post("/v1/entities", json=b["body"], headers=b["headers"]).status_code == 404
    assert (
        env.client.post(
            "/v1/relations", json=relation["body"], headers=relation["headers"]
        ).status_code
        == 404
    )
    assert expand(env, a)["paths"] == []
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.entity_evidence WHERE entity_id = %s",
                (b["memory_id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.relation_revision WHERE assertion_id = %s",
                (relation["memory_id"],),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("plan_mode", ["auto", "generic", "nested_loop"])
@pytest.mark.parametrize("direction", ["outgoing", "incoming", "both"])
def test_exact_graph_path_seed_and_entity_evidence_limits(env, monkeypatch, plan_mode, direction):
    checked = False
    neighbors = SqlGraph.neighbors

    async def inspect_neighbors(self, *args):
        nonlocal checked
        if plan_mode != "auto":
            self.conn.prepare_threshold = 0
            await self.conn.execute("SET LOCAL plan_cache_mode='force_generic_plan'")
        if plan_mode == "nested_loop":
            await self.conn.execute("SET LOCAL enable_hashjoin=off")
            await self.conn.execute("SET LOCAL enable_mergejoin=off")
            await self.conn.execute("SET LOCAL enable_material=off")
        if checked:
            return await neighbors(self, *args)
        execute = self.conn.execute
        statement, parameters = None, None

        async def record_execute(query, params=None, **kwargs):
            nonlocal statement, parameters
            if isinstance(query, str) and query.startswith("WITH adjacent"):
                statement, parameters = query, params
            return await execute(query, params, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(self.conn, "execute", record_execute)
            result = await neighbors(self, *args)
        if plan_mode != "auto":
            row = await (
                await self.conn.execute(
                    """SELECT count(*) AS total FROM pg_prepared_statements
                       WHERE statement LIKE 'WITH adjacent%' AND generic_plans > 0"""
                )
            ).fetchone()
            assert row["total"] > 0
        assert statement is not None
        row = await (
            await execute(
                "EXPLAIN (ANALYZE, FORMAT JSON, TIMING OFF) " + statement,
                parameters,
                prepare=False,
            )
        ).fetchone()
        plan = row["QUERY PLAN"][0]["Plan"]
        assert plan["Actual Rows"] == 100
        nodes = [plan]
        scans = []
        while nodes:
            node = nodes.pop()
            nodes.extend(node.get("Plans", []))
            relation = node.get("Relation Name")
            if relation in {"assertion", "assertion_revision", "entity", "entity_evidence"}:
                scans.append(relation)
                assert node["Actual Loops"] <= (
                    1 if relation in {"assertion", "assertion_revision"} else 2
                ), json.dumps(row["QUERY PLAN"])
        assert set(scans) == {"assertion", "assertion_revision", "entity", "entity_evidence"}
        checked = True
        return result

    monkeypatch.setattr(SqlGraph, "neighbors", inspect_neighbors)
    a, b = create_entity(env, "A"), create_entity(env, "B")
    body = relation_body(env, a, b)
    for _ in range(100):
        response = env.client.post("/v1/relations", json=body, headers=env.headers())
        assert response.status_code == 201, response.text
    seed = b if direction == "incoming" else a
    exact = expand(env, seed, direction=direction, max_paths=100)
    assert len(exact["paths"]) == len(exact["edges"]) == 100
    assert exact["coverage"]["truncated"] is False
    assert checked
    assert env.client.post("/v1/relations", json=body, headers=env.headers()).status_code == 201
    overflow = expand(env, seed, direction=direction)
    assert len(overflow["paths"]) == 100 and overflow["coverage"]["truncated"] is True
    seeds = [create_entity(env, f"Isolated {i}")["memory_id"] for i in range(16)]
    isolated = expand(env, seed, direction=direction, seeds=seeds)
    assert len(isolated["nodes"]) == 16 and isolated["paths"] == []
    sources = [uuid4() for _ in range(32)]
    with psycopg.connect(env.admin_url) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO memory.object(tenant_id,id,scope_id,kind) VALUES (%s,%s,%s,'episode')",
            [(env.tenants[0], source, env.scopes[0]) for source in sources],
        )
        cur.executemany(
            """INSERT INTO memory.episode
               (tenant_id,id,scope_id,occurred_at,content,consent_reference)
               VALUES (%s,%s,%s,clock_timestamp(),'node','test')""",
            [(env.tenants[0], source, env.scopes[0]) for source in sources],
        )
    entity = {
        **a["body"],
        "evidence": [{"memory_id": str(source), "quote": "node"} for source in sources],
    }
    created = env.client.post("/v1/entities", json=entity, headers=env.headers())
    assert created.status_code == 201
    assert (
        len(
            env.client.get(
                "/v1/entities/" + created.json()["memory_id"], headers=env.headers()
            ).json()["evidence"]
        )
        == 32
    )
    assert (
        env.client.post(
            "/v1/entities",
            json={**entity, "evidence": entity["evidence"] + a["body"]["evidence"]},
            headers=env.headers(),
        ).status_code
        == 422
    )


def test_invalid_graph_and_entity_contracts_are_explicit(env):
    a, b = create_entity(env, "A"), create_entity(env, "B")
    for invalid in [
        {"entity_type": "arbitrary"},
        {"canonical_label": ""},
        {"evidence": []},
        {
            "evidence": [
                {"memory_id": a["body"]["evidence"][0]["memory_id"], "quote": "not present"}
            ]
        },
        {"explicit_intent": False},
        {"tenant_id": str(env.tenants[1])},
    ]:
        assert (
            env.client.post(
                "/v1/entities", json={**a["body"], **invalid}, headers=env.headers()
            ).status_code
            == 422
        )
    body = relation_body(env, a, b)
    for invalid in [
        {"predicate": "MATCH (n) RETURN n"},
        {"value": "forged"},
        {"source_entity": body["evidence"][0]["memory_id"]},
        {"evidence": []},
        {"valid_from": "2026-09-01T00:00:00"},
    ]:
        result = env.client.post("/v1/relations", json={**body, **invalid}, headers=env.headers())
        assert result.status_code == (404 if "source_entity" in invalid else 422)
    for invalid in [
        {"max_hops": 0},
        {"max_hops": 3},
        {"max_hops": True},
        {"max_paths": 0},
        {"max_paths": 101},
        {"max_paths": True},
        {"seeds": [str(uuid4()) for _ in range(17)]},
        {"seeds": [a["memory_id"]] * 2},
        {"scope_ids": []},
        {"relation_types": []},
        {"relation_types": ["depends_on"] * 2},
        {"relation_types": ["unregistered"]},
        {"direction": "arbitrary"},
        {"query": "SELECT secret"},
        {"known_at": "2026-09-01"},
    ]:
        response = env.client.post(
            "/v1/graph/expand",
            json=expand_body(env, a, **invalid),
            headers=env.headers(),
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "invalid_request"


def test_failed_publication_and_lost_visibility_do_not_return_partial_success(env, monkeypatch):
    body = entity_body(env, "A")
    headers = env.headers()
    audit = MemoryService.audit

    async def fail(self, action, target):
        raise MemoryError("publication_failed", 503)

    monkeypatch.setattr(MemoryService, "audit", fail)
    assert env.client.post("/v1/entities", json=body, headers=headers).status_code == 503
    monkeypatch.setattr(MemoryService, "audit", audit)
    a = env.client.post("/v1/entities", json=body, headers=headers).json()
    b, c = create_entity(env, "B"), create_entity(env, "C")
    relation = create_relation(env, a, b)
    target_writer = SqlGraph.insert_target

    async def fail_target(self, *args):
        raise MemoryError("publication_failed", 503)

    monkeypatch.setattr(SqlGraph, "insert_target", fail_target)
    updated = env.client.post(
        "/v1/relations/" + relation["memory_id"] + "/revisions",
        json=revise_body(relation, c),
        headers=env.headers(),
    )
    assert updated.status_code == 503
    assert explain(env, relation, 2).status_code == 404
    assert explain(env, relation).json()["assertion"]["known_until"] is None
    monkeypatch.setattr(SqlGraph, "insert_target", target_writer)
    visible_entities = SqlGraph.visible_entities
    reads = 0

    async def lose_visibility(self, *args):
        nonlocal reads
        reads += 1
        return await visible_entities(self, *args) if reads == 1 else []

    monkeypatch.setattr(SqlGraph, "visible_entities", lose_visibility)
    response = env.client.post("/v1/graph/expand", json=expand_body(env, a), headers=env.headers())
    assert response.status_code == 409 and response.json()["code"] == "graph_invalidated"
    monkeypatch.setattr(SqlGraph, "visible_entities", visible_entities)

    async def timeout(self, *args):
        raise psycopg.errors.QueryCanceled("test timeout")

    monkeypatch.setattr(SqlGraph, "neighbors", timeout)
    response = env.client.post("/v1/graph/expand", json=expand_body(env, a), headers=env.headers())
    assert response.status_code == 503 and response.json()["code"] == "dependency_unavailable"


def test_database_guards_typed_relations_entities_and_scoped_evidence(env):
    a, b = create_entity(env, "A"), create_entity(env, "B")
    relation = create_relation(env, a, b)
    foreign = create_entity(env, "Foreign", 2)
    with psycopg.connect(env.admin_url) as admin:
        admin.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read'])""",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE memory.entity SET canonical_label = 'forged'")
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                """INSERT INTO memory.entity_evidence(tenant_id,entity_id,scope_id,source_id,quote)
                   VALUES (%s,%s,%s,%s,'Foreign')""",
                (
                    env.tenants[0],
                    a["memory_id"],
                    env.scopes[0],
                    foreign["body"]["evidence"][0]["memory_id"],
                ),
            )
        for target, value, error, message in [
            (
                foreign,
                "Foreign",
                psycopg.errors.ForeignKeyViolation,
                "relation_revision_tenant_id_target_id_scope_id_fkey",
            ),
            (b, "Unlinked text", psycopg.errors.CheckViolation, "exact entity target"),
        ]:
            with pytest.raises(error, match=message), conn.transaction():
                conn.execute(
                    """SELECT set_config('pgag.tenant_id',%s,true),
                              set_config('pgag.principal_id',%s,true)""",
                    (str(env.tenants[0]), str(env.principals[0])),
                )
                conn.execute(
                    """INSERT INTO memory.assertion_revision
                       (tenant_id,assertion_id,scope_id,revision,value,valid_time,
                        explicit_intent,correction_reason)
                       VALUES (%s,%s,%s,2,%s,'(,)',true,'test')""",
                    (env.tenants[0], relation["memory_id"], env.scopes[0], value),
                )
                conn.execute(
                    """INSERT INTO memory.provenance_edge
                       (tenant_id,child_id,child_revision,parent_id,scope_id,quote)
                       VALUES (%s,%s,2,%s,%s,'Reported relation')""",
                    (
                        env.tenants[0],
                        relation["memory_id"],
                        relation["body"]["evidence"][0]["memory_id"],
                        env.scopes[0],
                    ),
                )
                conn.execute(
                    """INSERT INTO memory.relation_revision
                       (tenant_id,assertion_id,scope_id,revision,target_id)
                       VALUES (%s,%s,%s,2,%s)""",
                    (env.tenants[0], relation["memory_id"], env.scopes[0], target["memory_id"]),
                )
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="relation identity"),
            conn.transaction(),
        ):
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                "DELETE FROM memory.relation_revision WHERE assertion_id = %s",
                (relation["memory_id"],),
            )
            conn.execute("DELETE FROM memory.relation WHERE id = %s", (relation["memory_id"],))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="complete evidence"),
            conn.transaction(),
        ):
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                "DELETE FROM memory.entity_evidence WHERE entity_id = %s", (a["memory_id"],)
            )
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert expand(env, a)["paths"]


def test_schema_five_preserves_v4_effect_history_keys_and_replay(env, database):
    legacy = database[2][0]["effect"]
    headers = {
        "Authorization": "Bearer " + env.token(sub=database[2][0]["subject"]),
        "Idempotency-Key": legacy["key"],
    }
    response = env.client.post("/v1/tool-effects", json=legacy["payload"], headers=headers)
    assert response.status_code == 201, response.text
    assert response.json() == legacy["result"]
    detail = env.client.get("/v1/tool-effects/" + legacy["result"]["memory_id"], headers=headers)
    assert detail.status_code == 200
    assert detail.json()["external_idempotency_key"] == legacy["external_key"]
    assert detail.json()["action_fingerprint"] == legacy["fingerprint"]
    assert [event["status"] for event in detail.json()["history"]] == ["planned", "dispatched"]
    capabilities = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert capabilities["schema_version"] == 11 and capabilities["graph_backend"] == "sql"
    assert capabilities["limits"]["graph_paths"] == 100
    schema = env.client.get("/openapi.json").json()
    for path, verb, status in [
        ("/v1/entities", "post", "201"),
        ("/v1/entities/{memory_id}", "get", "200"),
        ("/v1/relations", "post", "201"),
        ("/v1/relations/{memory_id}/revisions", "post", "201"),
        ("/v1/graph/expand", "post", "200"),
    ]:
        operation = schema["paths"][path][verb]
        assert operation["security"] == [{"BearerAuth": []}]
        assert "$ref" in operation["responses"][status]["content"]["application/json"]["schema"]


def test_two_tenant_graph_checkpoint_survives_api_crash_then_deletion(env, api_process):
    graphs = []
    for index in (0, 1):
        a, b = create_entity(env, "Same", index), create_entity(env, "Same", index)
        relation = create_relation(env, a, b, index)
        saved = checkpoint(env, [a, relation], index)
        graphs.append((a, b, relation, saved))
    with api_process("graph-before-crash.log") as (client, process):
        for index, (a, _, relation, _) in enumerate(graphs):
            result = client.post(
                "/v1/graph/expand",
                json=expand_body(env, a, index),
                headers=env.headers(index),
            )
            assert result.status_code == 200
            assert result.json()["paths"][0]["assertions"][0]["memory_id"] == relation["memory_id"]
        process.kill()
        process.wait(timeout=10)
    with api_process("graph-after-crash.log") as (client, _):
        for index, (a, b, relation, saved) in enumerate(graphs):
            fork = client.post(
                "/v1/checkpoints/restore",
                headers=env.headers(index),
                json={
                    "checkpoint_id": saved["checkpoint_id"],
                    "target_branch_id": str(uuid4()),
                    "harness_id": "graph-tests",
                    "harness_version": "1",
                },
            )
            assert fork.status_code == 201, fork.text
            assert {ref["memory_id"] for ref in fork.json()["memory_refs"]} == {
                a["memory_id"],
                relation["memory_id"],
            }
            removed = client.post(
                "/v1/forget",
                json={"memory_ids": [b["memory_id"]], "reason": "test"},
                headers=env.headers(index),
            )
            assert removed.status_code == 202, removed.text
            assert (
                client.post(
                    "/v1/graph/expand", json=expand_body(env, a, index), headers=env.headers(index)
                ).json()["paths"]
                == []
            )
            assert (
                client.get(
                    "/v1/checkpoints/" + fork.json()["checkpoint_id"], headers=env.headers(index)
                ).status_code
                == 404
            )
