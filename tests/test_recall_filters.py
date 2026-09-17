import asyncio
import sys
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import ValidationError

from pg_agmemory.models import Recall, RecallFilters
from pg_agmemory.recall_hook import HookInput
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

FUTURE = "2100-01-01T00:00:00Z"
VECTOR = {
    "model": {"name": "synthetic-filters", "revision": "fixture-v1"},
    "values": [1.0] + [0.0] * 767,
}


def mode_request(mode):
    return {
        "retrieval_mode": mode,
        "query": "Gold" if mode == "hybrid" else "",
        **({"vector_query": VECTOR} if mode != "lexical" else {}),
    }


def upload(env, memory_id, values=None):
    source = env.client.post(
        "/v1/embedding-inputs", json={"memory_id": memory_id}, headers=env.headers()
    )
    assert source.status_code == 200
    response = env.client.post(
        "/v1/embeddings",
        json={
            **VECTOR,
            "values": values if values is not None else VECTOR["values"],
            "memory_id": memory_id,
            "input_digest": source.json()["input_digest"],
        },
        headers=env.headers(),
    )
    assert response.status_code == 201


def ids(response):
    assert response.status_code == 200, response.text
    return [item["memory_id"] for item in response.json()["items"]]


@pytest.mark.parametrize(
    "filters",
    [
        [],
        "ACME",
        {"kind": "entity"},
        {"kind": ["episode", "assertion"]},
        {"subject": ""},
        {"subject": " "},
        {"subject": "x" * 257},
        {"subject": True},
        {"predicate": ""},
        {"predicate": "Contract"},
        {"predicate": "x" * 65},
        {"predicate": "contract%"},
        {"kind": "episode", "subject": "ACME"},
        {"kind": "episode", "predicate": "tier"},
        {"scope_id": str(uuid4())},
        {"value": "Gold"},
    ],
)
def test_closed_filter_contract_rejects_invalid_or_contradictory_fields(filters):
    with pytest.raises(ValidationError):
        Recall(scope_ids=[uuid4()], purpose="test", filters=filters)


def test_filter_normalization_and_hook_boundary():
    assert Recall(scope_ids=[uuid4()], purpose="test").filters is None
    assert RecallFilters().model_dump() == {"kind": None, "subject": None, "predicate": None}
    assert RecallFilters(subject=" ACME ", predicate=" tier ").model_dump() == {
        "kind": None,
        "subject": "ACME",
        "predicate": "tier",
    }
    assert RecallFilters(subject="x" * 256, predicate="x" * 64)
    with pytest.raises(ValidationError):
        HookInput(event="session_start", query="", filters={"kind": "episode"})


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["lexical", "vector", "hybrid"])
def test_absent_empty_and_null_filters_preserve_complete_result(env, mode):
    source = env.observe("Gold").json()["memory_id"]
    env.remember(source)
    upload(env, source)
    arguments = {**mode_request(mode), "as_of": FUTURE, "known_at": FUTURE}
    baseline = env.recall(**arguments)
    assert baseline.status_code == 200
    for filters in (None, {}, {"kind": None, "subject": None, "predicate": None}):
        assert env.recall(**arguments, filters=filters).json() == baseline.json()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["lexical", "vector", "hybrid"])
def test_filters_precede_top_k_ranks_and_projection_coverage(env, mode):
    source = env.observe("Gold").json()["memory_id"]
    target = env.remember(source).json()["memory_id"]
    upload(env, target, [0.0, 1.0] + [0.0] * 766)
    for changes in ({"subject": "Other"}, {"predicate": "other"}):
        excluded = env.remember(source, **changes).json()["memory_id"]
        upload(env, excluded)
    env.observe("Gold Gold Gold Gold Gold")
    response = env.recall(
        **mode_request(mode),
        max_items=1,
        filters={"kind": "assertion", "subject": "ACME", "predicate": "contract_tier"},
    )
    assert ids(response) == [target]
    result = response.json()
    assert result["coverage"]["retrieval_complete"] and not result["coverage"]["truncated"]
    assert not result["coverage"]["vector_incomplete"]
    assert result["items"][0]["source"] == [source]
    if mode != "lexical":
        evidence = result["items"][0]["retrieval"]
        assert evidence["vector_rank"] == 1
        assert evidence["vector_distance"] == pytest.approx(1)
        if mode == "hybrid":
            assert evidence["lexical_rank"] == 1
            assert evidence["fusion_score"] == pytest.approx(2 / 61)


@pytest.mark.integration
@pytest.mark.parametrize("profile", ["simple-v1", "ja-janome-0.5.0-v1"])
def test_exact_subject_predicate_and_kind_do_not_replace_keyword_matching(env, profile):
    source = env.observe("Gold").json()["memory_id"]
    targets = {}
    for subject in (
        "ACME",
        "acme",
        "ACME Europe",
        "\u6771\u4eac\u90fd",
        "caf\u00e9",
        "100%_literal",
        "' OR true --",
    ):
        response = env.remember(source, subject=subject)
        assert response.status_code == 201
        targets[subject] = response.json()["memory_id"]
    for subject, target in targets.items():
        assert ids(env.recall(search_profile=profile, filters={"subject": subject})) == [target]
    assert ids(env.recall(filters={"subject": " ACME ", "predicate": " contract_tier "})) == [
        targets["ACME"]
    ]
    for subject in ("ACM", "\u6771\u4eac", "cafe\u0301", "%", "missing"):
        assert ids(env.recall(filters={"subject": subject})) == []
    assert ids(env.recall(query="Silver", filters={"subject": "ACME"})) == []
    assert ids(env.recall(query="Gold", filters={"subject": "ACME"})) == [targets["ACME"]]
    assert ids(env.recall(filters={"kind": "episode"})) == [source]
    assert set(ids(env.recall(filters={"kind": "assertion"}))) == set(targets.values())
    assert set(ids(env.recall(filters={"predicate": "contract_tier"}))) == set(targets.values())
    assert ids(env.recall(filters={"predicate": "contract"})) == []


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["lexical", "vector", "hybrid"])
def test_kind_only_filters_apply_in_all_modes_and_keep_item_overflow(env, mode):
    source = env.observe("Gold").json()["memory_id"]
    upload(env, source)
    assertions = []
    for subject in ("ACME", "Other"):
        target = env.remember(source, subject=subject).json()["memory_id"]
        assertions.append(target)
        upload(env, target)
    episodes = env.recall(**mode_request(mode), filters={"kind": "episode"}, max_items=1)
    assert ids(episodes) == [source] and not episodes.json()["coverage"]["truncated"]
    limited = env.recall(**mode_request(mode), filters={"kind": "assertion"}, max_items=1)
    assert len(ids(limited)) == 1 and ids(limited)[0] in assertions
    assert limited.json()["coverage"]["truncated"]
    complete = env.recall(**mode_request(mode), filters={"kind": "assertion"}, max_items=2)
    assert set(ids(complete)) == set(assertions)
    assert not complete.json()["coverage"]["truncated"]


@pytest.mark.integration
def test_projection_coverage_uses_filtered_universe_but_jobs_remain_scope_level(env):
    source = env.observe("Gold").json()["memory_id"]
    target = env.remember(source).json()["memory_id"]
    upload(env, target)
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("DELETE FROM memory.episode_lexical WHERE episode_id=%s", (source,))
    arguments = {
        **mode_request("hybrid"),
        "search_profile": "ja-janome-0.5.0-v1",
        "filters": {"subject": "ACME"},
    }
    assert env.recall(**arguments).json()["coverage"]["retrieval_complete"]
    assert not env.recall(**{**arguments, "filters": None}).json()["coverage"]["retrieval_complete"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("DELETE FROM memory.assertion_lexical WHERE assertion_id=%s", (target,))
        conn.execute("DELETE FROM memory.assertion_embedding WHERE assertion_id=%s", (target,))
    missing = env.recall(**arguments)
    assert ids(missing) == []
    assert missing.json()["empty_reason"] == "index_incomplete"
    assert missing.json()["coverage"]["lexical_incomplete"]
    assert missing.json()["coverage"]["vector_incomplete"]
    empty = env.recall(**{**arguments, "filters": {"subject": "absent"}})
    assert ids(empty) == [] and empty.json()["empty_reason"] == "not_found"
    assert empty.json()["coverage"]["retrieval_complete"]
    job = env.client.post(
        "/v1/jobs",
        json={
            "kind": "structured_remember",
            "memory": {
                "scope_id": str(env.scopes[0]),
                "subject": "Other",
                "predicate": "other",
                "value": "Gold",
                "evidence": [{"memory_id": source, "quote": "Gold"}],
                "explicit_intent": True,
            },
        },
        headers=env.headers(),
    )
    assert job.status_code == 202
    assert env.recall(filters={"subject": "absent"}).json()["coverage"]["jobs_pending"]


@pytest.mark.integration
def test_required_refs_must_match_filters_and_keep_exact_budget_contract(env):
    source = env.observe("Gold").json()["memory_id"]
    target = env.remember(source).json()["memory_id"]
    arguments = {
        "filters": {"kind": "assertion", "subject": "ACME", "predicate": "contract_tier"},
        "required_memory_refs": [{"memory_id": target}],
        "query": "unmatched",
    }
    response = env.recall(**arguments)
    assert ids(response) == [target]
    size = response.json()["context_pack"]["byte_count"]
    assert ids(env.recall(**arguments, token_budget=size, mode="implicit")) == [target]
    assert env.recall(**arguments, token_budget=size - 1).json()["code"] == "budget_exhausted"
    for changes in (
        {"filters": {"kind": "episode"}},
        {"filters": {"subject": "Other"}},
        {"filters": {"predicate": "other"}},
        {"required_memory_refs": [{"memory_id": target}, {"memory_id": source}]},
    ):
        denied = env.recall(**{**arguments, **changes})
        assert denied.status_code == 404 and denied.json()["code"] == "not_found"
        assert (
            "items" not in denied.json() and target not in denied.text and source not in denied.text
        )


@pytest.mark.integration
def test_filters_do_not_widen_scope_current_acl_or_survive_source_purge(env):
    source = env.observe("Gold", index=2).json()["memory_id"]
    target = env.remember(source, index=2).json()["memory_id"]
    foreign_source = env.observe("PRIVATE Gold", index=1).json()["memory_id"]
    foreign = env.remember(foreign_source, index=1).json()["memory_id"]
    arguments = {
        "scope_ids": [str(value) for value in env.scopes],
        "filters": {"subject": "ACME"},
        "as_of": FUTURE,
        "known_at": FUTURE,
    }
    assert ids(env.recall(**arguments)) == []
    for operation, epoch in (("set", 1), ("revoke", 2), ("set", 3)):
        options = (
            {"permissions": ("read", "delete"), "no_expiry": True} if operation == "set" else {}
        )
        with scope_access(
            env.admin_url,
            ScopeAccessRequest(
                operation=operation,
                tenant_id=env.tenants[0],
                scope_id=env.scopes[2],
                principal_id=env.principals[0],
                expected_access_epoch=epoch,
                **options,
            ),
        ):
            pass
        response = env.recall(**arguments)
        assert ids(response) == ([target] if operation == "set" else [])
        assert foreign not in response.text and "PRIVATE" not in response.text
        assert ids(env.recall(filters={"subject": "ACME"})) == []
    purged = env.client.post(
        "/v1/forget", json={"memory_ids": [source], "reason": "test"}, headers=env.headers()
    )
    assert purged.status_code == 202 and purged.json()["object_count"] == 2
    assert ids(env.recall(**arguments)) == []


@pytest.mark.integration
def test_filtered_assertions_preserve_valid_and_system_time_boundaries(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    target = env.remember(source, valid_from="2026-09-01T00:00:00Z").json()["memory_id"]
    revision = env.client.post(
        f"/v1/assertions/{target}/revisions",
        json={
            "expected_revision": 1,
            "value": "Silver",
            "evidence": [{"memory_id": source, "quote": "Silver"}],
            "explicit_intent": True,
            "valid_from": "2026-09-05T00:00:00Z",
            "valid_to": "2026-09-10T00:00:00Z",
            "reason": "correction",
        },
        headers=env.headers(),
    )
    assert revision.status_code == 201
    explanation = env.client.post(
        "/v1/explain", json={"memory_id": target, "revision": 2}, headers=env.headers()
    ).json()
    boundary = datetime.fromisoformat(explanation["assertion"]["recorded_at"])
    for known, as_of, expected_revision in (
        (boundary - timedelta(microseconds=1), "2026-09-03T00:00:00Z", 1),
        (boundary, "2026-09-03T00:00:00Z", None),
        (boundary, "2026-09-05T00:00:00Z", 2),
        (boundary, "2026-09-10T00:00:00Z", None),
    ):
        response = env.recall(
            filters={"subject": "ACME", "predicate": "contract_tier"},
            known_at=known.isoformat(),
            as_of=as_of,
        )
        assert ids(response) == ([target] if expected_revision else [])
        if expected_revision:
            assert response.json()["items"][0]["revision"] == expected_revision
    future = env.observe("future", occurred_at="2099-01-01T00:00:00Z").json()["memory_id"]
    assert future not in ids(env.recall(filters={"kind": "episode"}))
    assert future in ids(env.recall(filters={"kind": "episode"}, as_of=FUTURE))
    assert ids(env.recall(filters={"kind": "episode"}, known_at="2000-01-01T00:00:00Z")) == []


@pytest.mark.integration
def test_relation_filters_match_canonical_subject_and_preserve_endpoints(env):
    source = env.observe("Gold").json()["memory_id"]
    entities = []
    evidence = [{"memory_id": source, "quote": "Gold"}]
    for label in ("frontend", "backend"):
        response = env.client.post(
            "/v1/entities",
            json={
                "scope_id": str(env.scopes[0]),
                "entity_type": "component",
                "canonical_label": label,
                "evidence": evidence,
                "explicit_intent": True,
            },
            headers=env.headers(),
        )
        assert response.status_code == 201
        entities.append(response.json()["memory_id"])
    relation = env.client.post(
        "/v1/relations",
        json={
            "scope_id": str(env.scopes[0]),
            "source_entity": entities[0],
            "target_entity": entities[1],
            "predicate": "depends_on",
            "evidence": evidence,
            "explicit_intent": True,
        },
        headers=env.headers(),
    )
    assert relation.status_code == 201
    response = env.recall(
        filters={"kind": "assertion", "subject": "frontend", "predicate": "depends_on"}
    )
    assert ids(response) == [relation.json()["memory_id"]]
    assert response.json()["items"][0]["source"] == [source]
    assert response.json()["items"][0]["relation"] == {
        "source_entity": entities[0],
        "target_entity": entities[1],
    }
    assert ids(env.recall(filters={"subject": entities[0]})) == []
    assert ids(env.recall(filters={"subject": "backend"})) == []


@pytest.mark.integration
def test_filters_are_read_only_and_native_errors_are_safe(env):
    source = env.observe("Gold").json()["memory_id"]
    env.remember(source)
    statement = """SELECT access_epoch,deletion_epoch,
        (SELECT count(*) FROM memory_ops.audit_event WHERE tenant_id=%s),
        (SELECT count(*) FROM memory_ops.idempotency WHERE tenant_id=%s)
        FROM memory.tenant WHERE id=%s"""
    with psycopg.connect(env.admin_url) as conn:
        before = conn.execute(statement, (env.tenants[0],) * 3).fetchone()
    headers = env.headers()
    del headers["Idempotency-Key"]
    response = env.client.post(
        "/v1/recall",
        json={"scope_ids": [str(env.scopes[0])], "purpose": "test", "filters": {"subject": "ACME"}},
        headers=headers,
    )
    assert ids(response)
    invalid = env.recall(filters={"kind": "episode", "subject": "PRIVATE"})
    assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_request"
    assert "PRIVATE" not in invalid.text and invalid.headers["cache-control"] == "no-store"
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(statement, (env.tenants[0],) * 3).fetchone() == before
    caps = env.client.get("/v1/capabilities", headers=headers).json()
    assert caps["recall_filters"] == {
        "fields": ["kind", "subject", "predicate"],
        "match": "exact",
        "combination": "and",
        "retrieval_modes": ["lexical", "vector", "hybrid"],
    }
    schemas = env.client.get("/openapi.json").json()["components"]["schemas"]
    assert schemas["Recall"]["properties"]["filters"]["anyOf"][0] == {
        "$ref": "#/components/schemas/RecallFilters"
    }
    assert not schemas["RecallFilters"]["additionalProperties"]


@pytest.mark.integration
def test_sdk_filters_match_native_and_revalidate_nested_models(env, api_process):
    source = env.observe("Gold").json()["memory_id"]
    target = env.remember(source).json()["memory_id"]
    request = Recall(
        scope_ids=[env.scopes[0]],
        purpose="test",
        filters=RecallFilters(subject="ACME", predicate="contract_tier"),
    )
    expected = env.recall(**request.model_dump(mode="json")).json()
    with api_process("sdk-filters.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                result = await sdk.recall(request)
                assert [value.memory_id for value in result.items] == [UUID(target)]
                assert result.model_dump(mode="json") == expected
                bad_filters = RecallFilters(subject="ACME").model_copy(update={"kind": "episode"})
                with pytest.raises(MemoryClientError) as failure:
                    await sdk.recall(request.model_copy(update={"filters": bad_filters}))
                assert failure.value.error.code == "invalid_request"
                assert not failure.value.error.outcome_unknown

        asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_filters_match_native_and_propagate_required_mismatch(env, api_process, mode):
    source = env.observe("Gold").json()["memory_id"]
    target = env.remember(source).json()["memory_id"]
    request = {
        "scope_ids": [str(env.scopes[0])],
        "purpose": "test",
        "as_of": FUTURE,
        "known_at": FUTURE,
        "token_budget": 8000,
        "filters": {"subject": "ACME", "predicate": "contract_tier"},
    }
    expected = env.recall(**request).json()
    with api_process("mcp-filters-" + mode + ".log") as (http, _):

        async def scenario():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "pg_agmemory.cli", "mcp"],
                env={"PGAG_MCP_API_URL": str(http.base_url), "PGAG_MCP_API_TOKEN": env.token()},
            )
            async with Client(parameters, mode=mode, read_timeout_seconds=20) as client:
                result = await client.call_tool("memory_recall", {"request": request})
                assert not result.is_error and result.structured_content["result"] == expected
                failed = await client.call_tool(
                    "memory_recall",
                    {
                        "request": request
                        | {
                            "filters": {"subject": "Other"},
                            "required_memory_refs": [{"memory_id": target}],
                        }
                    },
                )
                assert failed.is_error and failed.structured_content["error"]["code"] == "not_found"
                assert not failed.structured_content["error"]["outcome_unknown"]

        asyncio.run(scenario())
