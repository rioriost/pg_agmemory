import asyncio
import inspect
import json
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory.api import create_app
from pg_agmemory.database import Settings
from pg_agmemory.mcp_adapter import TOOLS
from pg_agmemory.models import Recall
from pg_agmemory.query_planning import (
    LEXICAL_QUERY_GUIDANCE,
    LexicalQueryPlan,
    lexical_query_contract,
    lexical_query_prompt,
    parse_lexical_query_plan,
)
from pg_agmemory.recall_hook import HookInput

PROFILES = ("simple-v1", "ja-janome-0.5.0-v1")


def test_deeply_nested_plan_is_an_invalid_plan_not_an_unhandled_parser_error():
    with pytest.raises(ValueError):
        parse_lexical_query_plan("[" * 1500 + "0" + "]" * 1500)


@pytest.mark.parametrize("terms", [
    ["willow"],
    ["Willow", "archive", "codec"],
    ["契約", "更新"],
    ["INC-731", "node.js", "café"],
    ["9", "é", "🙂a"],
    ["x" * 64],
])
def test_plan_keeps_one_to_three_distinct_literal_terms_and_joins_with_spaces(terms):
    plan = LexicalQueryPlan(terms=terms)
    assert plan.terms == terms
    assert plan.query == " ".join(terms)
    assert plan.model_dump() == {"terms": terms}
    assert parse_lexical_query_plan(json.dumps({"terms": terms})) == plan


@pytest.mark.parametrize("terms", [
    [], ["a", "b", "c", "d"], ["x" * 65], [""], ["   "],
    ["two words"], ["x\ty"], ["x\ny"], ["x\ry"], ["x\u00a0y"], ["x\u3000y"],
    [" prefix"], ["suffix "], ["!!!"], ["🙂"], ["_"], ["\u0301"],
    ["Willow", "willow"], ["Straße", "STRASSE"], ["契約", "契約"],
    [True], [1], [None], [["nested"]], [{"value": "x"}],
    "willow", ("willow",), None,
])
def test_plan_rejects_non_strict_terms_whitespace_empty_punctuation_and_casefold_duplicates(terms):
    with pytest.raises(ValidationError):
        LexicalQueryPlan(terms=terms)


@pytest.mark.parametrize("raw", [
    '{"terms":["x"],"terms":["y"]}',
    '{"terms":["x"],"query":"x"}',
    '{"terms":["x"],"explanation":"extra"}',
    '{"terms":[NaN]}', '{"terms":[Infinity]}', '{"terms":[-Infinity]}', '{"terms":[1e400]}',
    '{"terms":"x"}', '{"terms":[true]}', '{"terms":[]}', '{}', 'null', '["x"]',
    '{"terms":["x"]} trailing', '```json\n{"terms":["x"]}\n```',
])
def test_parser_rejects_duplicate_keys_nonfinite_values_extras_and_non_json_contracts(raw):
    with pytest.raises(ValueError):
        parse_lexical_query_plan(raw)


@pytest.mark.parametrize("raw", [b'{"terms":["x"]}', None, '{"terms":["x"]}' + " " * 4096])
def test_parser_rejects_non_string_or_over_budget_output(raw):
    with pytest.raises(ValueError):
        parse_lexical_query_plan(raw)


@pytest.mark.parametrize("terms", [[], [" "], ["a", "A"], ["a", "b", "c", "d"]])
def test_mutated_plan_cannot_silently_turn_into_browse_or_bypass_term_validation(terms):
    plan = LexicalQueryPlan(terms=["archive"])
    plan.terms[:] = terms
    with pytest.raises(ValueError):
        _ = plan.query


def test_plan_is_closed_and_native_recall_stays_a_string_contract():
    with pytest.raises(ValidationError):
        LexicalQueryPlan(terms=["archive"], query="archive")
    schema = LexicalQueryPlan.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["terms"]["minItems"] == 1
    assert schema["properties"]["terms"]["maxItems"] == 3
    assert schema["properties"]["terms"]["items"]["maxLength"] == 64
    scope = uuid4()
    for query in ("", "What is the archive codec?", '"archive codec"', "archive OR codec"):
        request = Recall(scope_ids=[scope], purpose="test", query=query)
        assert request.query == query
        assert request.retrieval_mode == "lexical" and request.search_profile == "simple-v1"
        assert request.model_dump()["query"] == query
    assert Recall(scope_ids=[scope], purpose="test", query="x" * 4096)
    with pytest.raises(ValidationError):
        Recall(scope_ids=[scope], purpose="test", query="x" * 4097)
    with pytest.raises(ValidationError):
        Recall(scope_ids=[scope], purpose="test", query={"terms": ["archive"]})
    for changes in (
        {"max_items": 0}, {"max_items": 101}, {"token_budget": 63}, {"token_budget": 8001},
        {"mode": "implicit", "token_budget": 2001},
    ):
        with pytest.raises(ValidationError):
            Recall(scope_ids=[scope], purpose="test", **changes)


@pytest.mark.parametrize("profile", PROFILES)
def test_prompt_accepts_only_question_and_profile_without_memory_or_answer_channels(profile):
    assert tuple(inspect.signature(lexical_query_prompt).parameters) == (
        "question", "search_profile",
    )
    question = "Which archive codec is selected for Willow?"
    prompt = lexical_query_prompt(question, profile)
    assert isinstance(prompt, str) and question in prompt and profile in prompt
    assert LEXICAL_QUERY_GUIDANCE in prompt
    assert lexical_query_prompt(question, profile) == prompt
    payload = json.loads(prompt[prompt.rfind('{"question":'):])
    assert payload == {"question": question, "search_profile": profile}
    for hidden in ("private-memory-731", "expected_keep_ids", "required_source_ids", "answer-731"):
        assert hidden not in prompt
    with pytest.raises(TypeError):
        lexical_query_prompt(question, profile, memory_items=["private-memory-731"])
    with pytest.raises(TypeError):
        lexical_query_prompt(question, profile, expected_answer="answer-731")


@pytest.mark.parametrize("question,profile", [
    ("", PROFILES[0]), ("   ", PROFILES[0]), ("x" * 4097, PROFILES[0]),
    (None, PROFILES[0]), ("Which archive codec?", "unsupported-profile"),
])
def test_prompt_requires_a_bounded_question_and_an_advertised_profile(question, profile):
    with pytest.raises(ValueError):
        lexical_query_prompt(question, profile)


def _distinct_containers(first, second):
    if isinstance(first, dict):
        assert first is not second
        for key in first:
            _distinct_containers(first[key], second[key])
    elif isinstance(first, list):
        assert first is not second
        for left, right in zip(first, second, strict=True):
            _distinct_containers(left, right)


def _contains_exact_value(value, expected):
    if isinstance(value, dict):
        return any(_contains_exact_value(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains_exact_value(child, expected) for child in value)
    return value == expected


def test_contract_is_fresh_and_shared_guidance_matches_capabilities_openapi_hook_and_mcp():
    first, second = lexical_query_contract(), lexical_query_contract()
    assert isinstance(first, dict) and first == second
    _distinct_containers(first, second)
    first["test-only-mutation"] = True
    assert lexical_query_contract() == second
    expected_metadata = {
        "format": "pgag-lexical-query-v1", "matching": "all_lexemes",
        "parser": "plainto_tsquery", "dictionary": "simple", "english_stemming": False,
        "question_word_removal": False, "boolean_operators": False,
        "phrase_operators": False, "wildcard_operators": False,
        "automatic_query_rewrite": False, "automatic_browse_fallback": False,
        "empty_query": "authorized_browse_after_trim",
        "nonempty_zero_lexeme_query": "no_matches", "search_profiles": list(PROFILES),
    }
    assert {key: second[key] for key in expected_metadata} == expected_metadata
    assert LEXICAL_QUERY_GUIDANCE
    for model in (Recall, HookInput):
        assert model.model_fields["query"].description == LEXICAL_QUERY_GUIDANCE
        assert model.model_json_schema()["properties"]["query"]["description"] == (
            LEXICAL_QUERY_GUIDANCE
        )
    app = create_app(Settings("unused", "unused", "unused", "unused"))
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/capabilities")
    capabilities = asyncio.run(endpoint())
    assert capabilities["lexical_query"] == second
    assert _contains_exact_value(capabilities["lexical_query"], LEXICAL_QUERY_GUIDANCE)
    assert capabilities["default_search_profile"] == "simple-v1"
    assert capabilities["search_profiles"] == list(PROFILES)
    recall_schema = app.openapi()["components"]["schemas"]["Recall"]
    assert recall_schema["properties"]["query"]["description"] == LEXICAL_QUERY_GUIDANCE
    tool = next(spec for spec in TOOLS if spec.name == "memory_recall").definition()
    assert LEXICAL_QUERY_GUIDANCE in tool.description
    assert tool.input_schema["$defs"]["Recall"]["properties"]["query"]["description"] == (
        LEXICAL_QUERY_GUIDANCE
    )


def _observe(env, content, **changes):
    response = env.observe(content, **changes)
    assert response.status_code == 201, response.text
    return response.json()["memory_id"]


def _recall(env, query, profile="simple-v1", **changes):
    response = env.recall(query=query, search_profile=profile, **changes)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["retrieval_mode"] == "lexical" and result["search_profile"] == profile
    return result


def _ids(result):
    return {item["memory_id"] for item in result["items"]}


def _purge(env, memory):
    response = env.client.post(
        "/v1/forget", headers=env.headers(),
        json={"memory_ids": [memory], "reason": "owned query-planning regression", "mode": "purge"},
    )
    assert response.status_code == 202, response.text


@pytest.mark.integration
@pytest.mark.parametrize("profile", PROFILES)
def test_planned_content_terms_retrieve_real_source_where_full_question_does_not(env, profile):
    content = "Willow archive codec delta73."
    source = _observe(env, content)
    question = "What is the archive codec selected for Willow?"
    assert _ids(_recall(env, question, profile)) == set()
    plan = parse_lexical_query_plan('{"terms":["Willow","archive","codec"]}')
    found = _recall(env, plan.query, profile)
    assert _ids(found) == {source}
    assert found["items"][0]["content"] == content


@pytest.mark.integration
def test_japanese_profile_matches_content_terms_without_question_particles_or_answer_hints(env):
    content = "東京都で契約を更新した。記録番号record73。"
    source = _observe(env, content)
    profile = PROFILES[1]
    question = "契約の更新で、どの手順を今後使うべきですか？"
    assert _ids(_recall(env, question, profile)) == set()
    plan = parse_lexical_query_plan('{"terms":["契約","更新"]}')
    found = _recall(env, plan.query, profile)
    assert _ids(found) == {source}
    assert found["items"][0]["content"] == content
    assert _ids(_recall(env, plan.query, PROFILES[0])) == set()


@pytest.mark.integration
@pytest.mark.parametrize("profile", PROFILES)
def test_real_plain_lexical_matching_remains_and_without_stemming_operators_or_phrase_syntax(
    env, profile,
):
    both = _observe(env, "cobalt spacermark heron running")
    reverse = _observe(env, "heron cobalt")
    only_first = _observe(env, "cobalt")
    only_second = _observe(env, "heron")
    assert _ids(_recall(env, "cobalt", profile)) == {both, reverse, only_first}
    assert _ids(_recall(env, "heron", profile)) == {both, reverse, only_second}
    for query in (
        "cobalt heron", "cobalt | heron", "cobalt & heron", "cobalt !heron", '"cobalt heron"',
    ):
        assert _ids(_recall(env, query, profile)) == {both, reverse}
    for query in ("cobalt OR heron", "cobalt missing", "cobalt the", "run", "cobal:*", "!!!"):
        assert _ids(_recall(env, query, profile)) == set()
    assert _ids(_recall(env, "running", profile)) == {both}
    assert _ids(_recall(env, "", profile)) == {both, reverse, only_first, only_second}


@pytest.mark.integration
@pytest.mark.parametrize("profile", PROFILES)
def test_planned_queries_preserve_current_rls_as_of_and_purge_even_for_historical_reads(
    env, profile,
):
    content = "lumen protocol marker22"
    visible = _observe(env, content, occurred_at="2026-08-01T00:00:00Z")
    future = _observe(env, content, occurred_at="2026-09-01T00:00:00Z")
    foreign = _observe(env, content, index=1, occurred_at="2026-08-01T00:00:00Z")
    private = _observe(env, content, index=2, occurred_at="2026-08-01T00:00:00Z")
    query = LexicalQueryPlan(terms=["lumen", "protocol"]).query
    shared = {
        "scope_ids": [str(scope) for scope in env.scopes],
        "known_at": "2100-01-01T00:00:00Z",
    }
    early = _recall(env, query, profile, as_of="2026-08-15T00:00:00Z", **shared)
    assert _ids(early) == {visible}
    assert foreign not in json.dumps(early) and private not in json.dumps(early)
    assert _ids(_recall(env, query, profile, as_of="2026-09-15T00:00:00Z", **shared)) == {
        visible, future,
    }
    _purge(env, visible)
    assert _ids(_recall(env, query, profile, as_of="2026-08-15T00:00:00Z", **shared)) == set()
    assert _ids(_recall(env, query, profile, as_of="2026-09-15T00:00:00Z", **shared)) == {future}
    with psycopg.connect(env.admin_url) as connection:
        connection.execute(
            """UPDATE memory.scope_member SET expires_at=clock_timestamp()-interval '1 second'
               WHERE tenant_id=%s AND principal_id=%s""",
            (env.tenants[0], env.principals[0]),
        )
    assert _ids(_recall(env, query, profile, as_of="2026-09-15T00:00:00Z", **shared)) == set()


@pytest.mark.integration
@pytest.mark.parametrize("profile", PROFILES)
def test_planned_queries_select_current_or_known_revision_without_resurrecting_purged_evidence(
    env, profile,
):
    old_source = _observe(env, "Selene channel amberstone", occurred_at="2026-08-01T00:00:00Z")
    current_source = _observe(
        env, "Selene channel violetstone", occurred_at="2026-08-05T00:00:00Z",
    )
    saved = env.remember(
        old_source, subject="Selene", predicate="channel", value="amberstone",
        evidence=[{"memory_id": old_source, "quote": "amberstone"}],
        valid_from="2026-08-01T00:00:00Z",
    )
    assert saved.status_code == 201, saved.text
    memory = saved.json()["memory_id"]
    explained = env.client.post(
        "/v1/explain", headers=env.headers(), json={"memory_id": memory, "revision": 1},
    )
    assert explained.status_code == 200, explained.text
    before = explained.json()["assertion"]["recorded_at"]
    revised = env.client.post(
        f"/v1/assertions/{memory}/revisions", headers=env.headers(),
        json={
            "expected_revision": 1, "value": "violetstone", "explicit_intent": True,
            "evidence": [{"memory_id": current_source, "quote": "violetstone"}],
            "reason": "owned query-planning correction",
            "valid_from": "2026-08-05T00:00:00Z",
        },
    )
    assert revised.status_code == 201, revised.text
    common = LexicalQueryPlan(terms=["Selene", "channel"]).query
    arguments = {"filters": {"kind": "assertion"}, "as_of": "2026-08-10T00:00:00Z"}
    current = _recall(env, common, profile, **arguments)
    assert _ids(current) == {memory}
    assert current["items"][0]["revision"] == 2
    assert current["items"][0]["source"] == [current_source]
    obsolete = LexicalQueryPlan(terms=["Selene", "amberstone"]).query
    assert _ids(_recall(env, obsolete, profile, **arguments)) == set()
    historical = _recall(env, obsolete, profile, known_at=before, **arguments)
    assert _ids(historical) == {memory}
    assert historical["items"][0]["revision"] == 1
    assert historical["items"][0]["source"] == [old_source]
    assert _ids(_recall(
        env, common, profile, filters={"kind": "assertion"}, as_of="2026-08-03T00:00:00Z",
    )) == set()
    _purge(env, old_source)
    assert _ids(_recall(env, common, profile, **arguments)) == set()
    assert _ids(_recall(env, obsolete, profile, known_at=before, **arguments)) == set()
