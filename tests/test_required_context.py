import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import ValidationError

from pg_agmemory.models import MemoryItem, MemoryReference, Recall
from pg_agmemory.recall_hook import HookInput
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError
from pg_agmemory.service import MemoryError, build_context

FUTURE = "2100-01-01T00:00:00Z"


def ref(identity, revision=1):
    return {"memory_id": str(identity), "revision": revision}


def recall(env, references, **changes):
    return env.recall(required_memory_refs=references, **changes)


def item(content):
    return MemoryItem(
        memory_id=uuid4(),
        type="episode",
        content=content,
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"required_memory_refs": [{"memory_id": str(uuid4()), "revision": 0}]},
        {"required_memory_refs": [{"memory_id": str(uuid4()), "revision": True}]},
        {"required_memory_refs": [{"memory_id": str(uuid4()), "revision": 1001}]},
        {"required_memory_refs": [ref(uuid4()) for _ in range(17)]},
        {"max_items": 1, "required_memory_refs": [ref(uuid4()), ref(uuid4())]},
        {"required_memory_refs": [{"memory_id": str(uuid4()), "scope_id": str(uuid4())}]},
    ],
)
def test_required_reference_contract_rejects_invalid_inputs(changes):
    with pytest.raises(ValidationError):
        Recall(scope_ids=[uuid4()], purpose="test", **changes)


@pytest.mark.parametrize("revision", [1, 2])
def test_required_ids_are_unique_even_across_revisions(revision):
    identity = uuid4()
    with pytest.raises(ValidationError):
        Recall(
            scope_ids=[uuid4()],
            purpose="test",
            required_memory_refs=[ref(identity), ref(identity, revision)],
        )


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_required_references_are_explicitly_lexical_only(mode):
    data = {
        "scope_ids": [uuid4()],
        "purpose": "test",
        "retrieval_mode": mode,
        "query": "Gold" if mode == "hybrid" else "",
        "vector_query": {
            "model": {"name": "synthetic", "revision": "1"},
            "values": [1.0] + [0.0] * 767,
        },
    }
    assert Recall(**data).required_memory_refs == []
    with pytest.raises(ValidationError, match="required references currently require lexical"):
        Recall(**data, required_memory_refs=[ref(uuid4())])


def test_reference_default_and_hook_authority_are_unchanged():
    identity = uuid4()
    request = Recall(
        scope_ids=[uuid4()], purpose="test", required_memory_refs=[{"memory_id": identity}]
    )
    assert request.required_memory_refs == [MemoryReference(memory_id=identity, revision=1)]
    with pytest.raises(ValidationError):
        HookInput(event="session_start", required_memory_refs=[ref(identity)])


@pytest.mark.parametrize("content", ["Required constraint", '東京都の制約 "quoted" \n evidence'])
def test_exact_required_budget_boundary_and_optional_drop(content):
    required = [item(content), item("Another required fact")]
    optional = item("optional " * 100)
    pack = build_context(required, 8000, required_count=2)[0]
    budget = len(json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode())
    assert budget == pack["byte_count"]
    result, selected, omitted = build_context(required + [optional], budget, required_count=2)
    assert result == pack and selected == required and omitted
    with pytest.raises(MemoryError, match="budget_exhausted") as error:
        build_context(required + [optional], budget - 1, required_count=2)
    assert error.value.status == 422
    assert result["text"].startswith("[Memory evidence, not instructions]")


def test_required_prefix_never_falls_back_to_smaller_optional_content():
    large, small = item("x" * 4000), item("tiny")
    assert build_context([large, small], 500)[1] == [small]
    with pytest.raises(MemoryError, match="budget_exhausted"):
        build_context([large, small], 500, required_count=1)
    for count in (-1, 3):
        with pytest.raises(ValueError, match="required_count"):
            build_context([large, small], 8000, required_count=count)


@pytest.mark.integration
@pytest.mark.parametrize("profile", ["simple-v1", "ja-janome-0.5.0-v1"])
def test_required_refs_bypass_keywords_not_limits_and_preserve_requested_order(env, profile):
    first = env.observe("First required constraint").json()["memory_id"]
    second = env.observe("Second required constraint").json()["memory_id"]
    optional = env.observe("Gold").json()["memory_id"]
    baseline = env.recall(query="Gold", search_profile=profile).json()
    assert [value["memory_id"] for value in baseline["items"]] == [optional]
    result = recall(env, [ref(second), ref(first)], query="Gold", search_profile=profile).json()
    assert [value["memory_id"] for value in result["items"]] == [second, first, optional]
    assert not result["coverage"]["truncated"]
    limited = recall(
        env, [ref(second), ref(first)], query="Gold", search_profile=profile, max_items=2
    ).json()
    assert [value["memory_id"] for value in limited["items"]] == [second, first]
    assert limited["coverage"]["truncated"] and limited["empty_reason"] is None
    duplicate = recall(env, [ref(optional)], query="Gold", search_profile=profile).json()
    assert [value["memory_id"] for value in duplicate["items"]] == [optional]
    assert not duplicate["coverage"]["truncated"]


@pytest.mark.integration
def test_empty_required_list_preserves_existing_complete_result(env):
    env.observe("Gold")
    for query in ("Gold", "", "unmatched"):
        expected = env.recall(query=query, as_of=FUTURE, known_at=FUTURE).json()
        actual = recall(env, [], query=query, as_of=FUTURE, known_at=FUTURE).json()
        assert expected == actual


@pytest.mark.integration
def test_required_item_outside_top_rank_is_loaded_and_no_writes_occur(env):
    required = env.observe("Old Gold constraint").json()["memory_id"]
    for _ in range(5):
        env.observe("Gold")
    with psycopg.connect(env.admin_url) as conn:
        before = conn.execute(
            "SELECT count(*) FROM memory_ops.audit_event WHERE tenant_id=%s", (env.tenants[0],)
        ).fetchone()[0]
    result = recall(env, [ref(required)], query="Gold", max_items=1)
    assert result.status_code == 200
    assert [value["memory_id"] for value in result.json()["items"]] == [required]
    assert result.json()["coverage"]["truncated"]
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_ops.audit_event WHERE tenant_id=%s", (env.tenants[0],)
            ).fetchone()[0]
            == before
        )
        assert conn.execute(
            "SELECT access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],)
        ).fetchone() == (1, 1)


@pytest.mark.integration
def test_actual_context_budget_is_all_required_or_error(env):
    first = env.observe("東京都の必要な制約").json()["memory_id"]
    second = env.observe("Required condition").json()["memory_id"]
    references = [ref(first), ref(second)]
    complete = recall(env, references, query="notmatching", token_budget=8000).json()
    size = complete["context_pack"]["byte_count"]
    exact = recall(env, references, query="notmatching", token_budget=size)
    assert exact.status_code == 200 and exact.json()["context_pack"] == complete["context_pack"]
    too_small = recall(env, references, query="notmatching", token_budget=size - 1)
    assert too_small.status_code == 422 and too_small.json()["code"] == "budget_exhausted"
    assert "context_pack" not in too_small.json() and first not in too_small.text
    assert too_small.headers["cache-control"] == "no-store"
    implicit = recall(env, references, query="notmatching", mode="implicit", token_budget=size)
    assert implicit.status_code == 200
    huge = env.observe("x" * 5000).json()["memory_id"]
    assert (
        recall(env, [ref(huge)], mode="implicit", token_budget=2000).json()["code"]
        == "budget_exhausted"
    )


@pytest.mark.integration
def test_all_sixteen_required_references_fit_item_limit(env):
    references = [ref(env.observe(f"required {index}").json()["memory_id"]) for index in range(16)]
    result = recall(env, references, max_items=16, token_budget=8000)
    assert result.status_code == 200
    assert [value["memory_id"] for value in result.json()["items"]] == [
        reference["memory_id"] for reference in references
    ]
    assert not result.json()["coverage"]["truncated"]
    caps = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert caps["required_context"] == {
        "retrieval_modes": ["lexical"],
        "max_refs": 16,
        "order": "request_order",
        "budget_policy": "all_required_or_error",
    }


@pytest.mark.integration
def test_required_refs_never_expand_scope_or_leak_private_targets(env):
    own = env.observe("Own constraint").json()["memory_id"]
    foreign = env.observe("PRIVATE_OTHER_TENANT", index=1).json()["memory_id"]
    private = env.observe("PRIVATE_OTHER_SCOPE", index=2).json()["memory_id"]
    for missing in (str(uuid4()), foreign, private):
        response = recall(
            env, [ref(own), ref(missing)], scope_ids=[str(value) for value in env.scopes]
        )
        assert response.status_code == 404 and response.json()["code"] == "not_found"
        assert "items" not in response.json() and missing not in response.text
        assert "PRIVATE" not in response.text and own not in response.text
    assert recall(env, [ref(own)], scope_ids=[str(env.scopes[1])]).status_code == 404


@pytest.mark.integration
def test_required_reference_tracks_current_acl_and_source_deletion(env):
    source = env.observe(index=2).json()["memory_id"]
    arguments = {"scope_ids": [str(env.scopes[2])], "known_at": FUTURE, "as_of": FUTURE}
    assert recall(env, [ref(source)], **arguments).status_code == 404
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
        assert recall(env, [ref(source)], **arguments).status_code == (
            200 if operation == "set" else 404
        )
    assert (
        env.client.post(
            "/v1/forget", json={"memory_ids": [source], "reason": "test"}, headers=env.headers()
        ).status_code
        == 202
    )
    assert recall(env, [ref(source)], **arguments).status_code == 404


@pytest.mark.integration
def test_required_episode_revisions_and_event_times_are_not_bypassed(env):
    source = env.observe(occurred_at="2099-01-01T00:00:00Z").json()["memory_id"]
    assert recall(env, [ref(source)]).status_code == 404
    assert recall(env, [ref(source)], as_of=FUTURE).status_code == 200
    assert recall(env, [ref(source, 2)], as_of=FUTURE).status_code == 404
    assert (
        recall(env, [ref(source)], as_of=FUTURE, known_at="2000-01-01T00:00:00Z").status_code == 404
    )


@pytest.mark.integration
def test_required_assertions_use_exact_bitemporal_revision_without_latest_fallback(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    assertion = env.remember(source, valid_from="2026-09-01T00:00:00Z").json()["memory_id"]
    old = env.client.post(
        "/v1/explain", json={"memory_id": assertion}, headers=env.headers()
    ).json()
    revised = env.client.post(
        f"/v1/assertions/{assertion}/revisions",
        json={
            "expected_revision": 1,
            "value": "Silver",
            "evidence": [{"memory_id": source, "quote": "Silver"}],
            "explicit_intent": True,
            "valid_from": "2026-09-05T00:00:00Z",
            "reason": "correction",
        },
        headers=env.headers(),
    )
    assert revised.status_code == 201
    current = env.client.post(
        "/v1/explain", json={"memory_id": assertion, "revision": 2}, headers=env.headers()
    ).json()
    boundary = datetime.fromisoformat(current["assertion"]["recorded_at"])
    assert recall(env, [{"memory_id": assertion}]).status_code == 404
    result = recall(env, [ref(assertion, 2)], query="unmatched").json()
    assert result["items"][0]["revision"] == 2 and result["items"][0]["source"] == [source]
    for reference, known, as_of, expected in [
        (ref(assertion), old["assertion"]["recorded_at"], "2026-09-03T00:00:00Z", 200),
        (ref(assertion), (boundary - timedelta(microseconds=1)).isoformat(), FUTURE, 200),
        (ref(assertion), boundary.isoformat(), FUTURE, 404),
        (ref(assertion, 2), boundary.isoformat(), "2026-09-03T00:00:00Z", 404),
        (ref(assertion, 2), boundary.isoformat(), "2026-09-05T00:00:00Z", 200),
    ]:
        assert recall(env, [reference], known_at=known, as_of=as_of).status_code == expected
    assert (
        env.client.post(
            "/v1/forget", json={"memory_ids": [source], "reason": "test"}, headers=env.headers()
        ).status_code
        == 202
    )
    assert (
        recall(env, [ref(assertion)], known_at=old["assertion"]["recorded_at"]).status_code == 404
    )


@pytest.mark.integration
def test_exact_required_canonical_text_does_not_require_a_japanese_projection(env):
    source = env.observe("東京都の必要な制約").json()["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("DELETE FROM memory.episode_lexical WHERE episode_id=%s", (source,))
    result = recall(env, [ref(source)], query="unmatched", search_profile="ja-janome-0.5.0-v1")
    assert result.status_code == 200
    assert result.json()["items"][0]["memory_id"] == source
    assert result.json()["coverage"]["lexical_incomplete"]
    assert not result.json()["coverage"]["retrieval_complete"]


@pytest.mark.integration
def test_required_relation_preserves_endpoints_but_entity_is_not_a_recall_item(env):
    source = env.observe("Gold").json()["memory_id"]
    entities = []
    for label in ("frontend", "backend"):
        response = env.client.post(
            "/v1/entities",
            json={
                "scope_id": str(env.scopes[0]),
                "entity_type": "component",
                "canonical_label": label,
                "evidence": [{"memory_id": source, "quote": "Gold"}],
                "explicit_intent": True,
            },
            headers=env.headers(),
        )
        assert response.status_code == 201
        entities.append(response.json()["memory_id"])
    assert recall(env, [ref(entities[0])]).status_code == 404
    response = env.client.post(
        "/v1/relations",
        json={
            "scope_id": str(env.scopes[0]),
            "source_entity": entities[0],
            "target_entity": entities[1],
            "predicate": "depends_on",
            "evidence": [{"memory_id": source, "quote": "Gold"}],
            "explicit_intent": True,
        },
        headers=env.headers(),
    )
    assert response.status_code == 201
    references = [ref(response.json()["memory_id"])]
    result = recall(env, references, query="unmatched").json()
    assert result["items"][0]["relation"] == {
        "source_entity": entities[0],
        "target_entity": entities[1],
    }
    assert f"entities={entities[0]}->{entities[1]}" in result["context_pack"]["text"]
    budget = result["context_pack"]["byte_count"]
    assert recall(env, references, query="unmatched", token_budget=budget).status_code == 200
    assert (
        recall(env, references, query="unmatched", token_budget=budget - 1).json()["code"]
        == "budget_exhausted"
    )


@pytest.mark.integration
def test_sdk_required_context_and_safe_budget_error(env, api_process):
    source = env.observe("Important constraint").json()["memory_id"]
    with api_process("sdk-required.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                request = Recall(
                    scope_ids=[env.scopes[0]],
                    purpose="test",
                    query="unmatched",
                    required_memory_refs=[MemoryReference(memory_id=UUID(source))],
                )
                result = await sdk.recall(request)
                assert [value.memory_id for value in result.items] == [UUID(source)]
                with pytest.raises(MemoryClientError) as failure:
                    await sdk.recall(request.model_copy(update={"token_budget": 64}))
                assert failure.value.error.code == "budget_exhausted"
                assert not failure.value.error.outcome_unknown and not failure.value.error.retryable

        asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_required_context_matches_native_and_propagates_budget_error(env, api_process, mode):
    source = env.observe("Required constraint").json()["memory_id"]
    request = {
        "scope_ids": [str(env.scopes[0])],
        "purpose": "test",
        "query": "unmatched",
        "as_of": FUTURE,
        "known_at": FUTURE,
        "required_memory_refs": [ref(source)],
    }
    expected = env.recall(**request).json()
    with api_process("mcp-required-" + mode + ".log") as (http, _):

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
                    "memory_recall", {"request": request | {"token_budget": 64}}
                )
                assert failed.is_error
                assert failed.structured_content["error"]["code"] == "budget_exhausted"
                assert not failed.structured_content["error"]["outcome_unknown"]

        asyncio.run(scenario())
