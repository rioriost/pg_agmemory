import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from pg_agmemory import __version__
from pg_agmemory.bounded_recall import BoundedRecall, BoundedRecallError, SearchPlan
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.models import MemoryItem, Recall, RecallResult, RecallTemporalBounds
from pg_agmemory.native_client import NativeSettings
from pg_agmemory.query_planning import LexicalQueryPlan
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError
from pg_agmemory.service import build_context

CAPABILITY = "recall_temporal_bounds_v1"
PIN = datetime(2026, 9, 27, 6, 8, 8, 123456, tzinfo=UTC)
SCOPE = UUID("10000000-0000-4000-8000-000000000001")


def request(**changes):
    return Recall(**{"scope_ids": [SCOPE], "purpose": "temporal-contract", **changes})


def payload(items=(), *, bounds=None):
    pack, selected, omitted = build_context(list(items), 8000, required_count=len(items))
    assert not omitted
    body = {
        "items": [item.model_dump(mode="json") for item in selected],
        "context_pack": pack,
        "coverage": {
            "retrieval_complete": True, "synthesis_pending": False, "projection_pending": False,
            "jobs_pending": False, "lexical_incomplete": False, "vector_incomplete": False,
            "graph_used": False, "truncated": False,
        },
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
        "search_profile": "simple-v1", "retrieval_mode": "lexical", "embedding_model": None,
        "empty_reason": None if selected else "not_found",
    }
    if bounds is not None:
        body["temporal_bounds"] = bounds
    return body


def bounds(as_of=PIN, known_at=PIN):
    return {"as_of": as_of.isoformat(), "known_at": known_at.isoformat()}


def search(*words):
    return SearchPlan(queries=[LexicalQueryPlan(terms=[word]) for word in words])


def item(**changes):
    return MemoryItem(**{
        "memory_id": uuid4(), "revision": 1, "type": "assertion", "content": "literal fact",
        "recorded_at": PIN - timedelta(seconds=1), **changes,
    })


def test_default_request_and_response_serializations_retain_legacy_shape_and_nulls():
    original = {
        "query": "", "scope_ids": [str(SCOPE)], "purpose": "temporal-contract",
        "as_of": None, "known_at": None, "mode": "explicit", "token_budget": 2000,
        "max_items": 20, "tokenizer_id": "utf8-bytes-v1", "search_profile": "simple-v1",
        "retrieval_mode": "lexical", "vector_query": None,
        "required_memory_refs": [], "filters": None,
    }
    for value in (request(), request(include_temporal_bounds=False)):
        assert value.model_dump(mode="json") == original
        assert value.model_dump_json() == json.dumps(original, separators=(",", ":"))
    old_response = payload()
    parsed = RecallResult.model_validate(old_response)
    assert parsed.temporal_bounds is None
    assert parsed.model_dump(mode="json") == old_response
    assert parsed.model_dump_json() == json.dumps(old_response, separators=(",", ":"))
    assert parsed.model_dump(mode="json")["embedding_model"] is None
    assert "include_temporal_bounds" in Recall.model_json_schema()["properties"]
    assert "temporal_bounds" in RecallResult.model_json_schema()["properties"]


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_request_temporal_opt_in_is_strict_boolean(value):
    with pytest.raises(ValidationError):
        request(include_temporal_bounds=value)


@pytest.mark.parametrize("value", [
    {},
    {"as_of": PIN.isoformat()},
    {"known_at": PIN.isoformat()},
    {"as_of": PIN.isoformat(), "known_at": PIN.isoformat(), "snapshot": "not allowed"},
    {"as_of": 0, "known_at": 0},
    {"as_of": True, "known_at": True},
    {"as_of": "0", "known_at": "0"},
    {"as_of": "2026-09-27T06:08:08", "known_at": PIN.isoformat()},
    {"as_of": PIN.replace(tzinfo=None), "known_at": PIN},
    {"as_of": None, "known_at": None},
    {"as_of": "2026-09-27T06:08:08.1234567Z", "known_at": PIN.isoformat()},
])
def test_temporal_metadata_requires_exact_complete_aware_instants(value):
    with pytest.raises(ValidationError):
        RecallTemporalBounds.model_validate(value)


def test_effective_bounds_preserve_microseconds_and_independent_temporal_axes():
    earlier = PIN - timedelta(days=1, microseconds=1)
    selected = RecallResult.model_validate(payload(bounds=bounds(PIN, earlier)))
    requested = request(include_temporal_bounds=True, as_of=PIN, known_at=earlier)
    resolved = selected.validated_temporal_bounds(requested)
    assert resolved.as_of == PIN and resolved.known_at == earlier
    assert resolved.as_of.microsecond == 123456 and resolved.known_at.microsecond == 123455
    assert selected.validated_temporal_bounds(
        request(include_temporal_bounds=True, as_of=PIN),
    ) == resolved
    assert selected.validated_temporal_bounds(
        request(include_temporal_bounds=True, known_at=earlier),
    ) == resolved
    with pytest.raises(ValueError):
        selected.validated_temporal_bounds(request(include_temporal_bounds=True))
    with pytest.raises(ValueError):
        selected.validated_temporal_bounds(request(
            include_temporal_bounds=True, as_of=PIN + timedelta(microseconds=1),
        ))
    assert selected.validated_temporal_bounds(request(
        include_temporal_bounds=True,
        as_of=PIN.astimezone(timezone(timedelta(hours=9))), known_at=earlier,
    )) == resolved


class WireBody(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    async def __aiter__(self):
        yield self.data


def sdk_transport(monkeypatch, body, *, features=None):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        if incoming.url.path == "/v1/capabilities":
            data = {
                "api_version": "v1", "service_version": __version__,
                "schema_version": SCHEMA_VERSION, "features": features,
            }
        else:
            assert incoming.url.path == "/v1/recall"
            data = body
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=WireBody(data),
        )

    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url, transport=httpx.MockTransport(handler),
            follow_redirects=False, trust_env=False,
        )

    monkeypatch.setattr(NativeSettings, "client", client)
    return calls


def test_sdk_default_remains_compatible_with_old_capability_and_response(monkeypatch):
    calls = sdk_transport(monkeypatch, payload())

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            result = await sdk.recall(request())
            assert result.temporal_bounds is None

    asyncio.run(scenario())
    assert len(calls) == 2
    assert calls[1].content == request().model_dump_json().encode()
    assert b"include_temporal_bounds" not in calls[1].content


@pytest.mark.parametrize("features", [None, [], {}, [CAPABILITY, 1], ["other_feature"]])
def test_sdk_unsupported_opt_in_fails_before_recall_without_refresh_or_downgrade(
    monkeypatch, features,
):
    calls = sdk_transport(monkeypatch, payload(bounds=bounds()), features=features)

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            with pytest.raises(MemoryClientError, match="unsupported_native_capability"):
                await sdk.recall(request(include_temporal_bounds=True))

    asyncio.run(scenario())
    assert [call.url.path for call in calls] == ["/v1/capabilities"]


@pytest.mark.parametrize("metadata,code", [
    (None, "invalid_temporal_bounds"),
    ({}, "invalid_native_response"),
    ({"as_of": PIN.isoformat()}, "invalid_native_response"),
    ({"as_of": "2026-09-27", "known_at": PIN.isoformat()}, "invalid_native_response"),
    ({"as_of": 0, "known_at": 0}, "invalid_native_response"),
    (bounds(PIN, PIN + timedelta(microseconds=1)), "invalid_temporal_bounds"),
])
def test_sdk_missing_malformed_or_unequal_server_current_metadata_fails_closed(
    monkeypatch, metadata, code,
):
    calls = sdk_transport(monkeypatch, payload(bounds=metadata), features=[CAPABILITY])

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            with pytest.raises(MemoryClientError, match=code):
                await sdk.recall(request(include_temporal_bounds=True))

    asyncio.run(scenario())
    assert len(calls) == 2


@pytest.mark.parametrize("changes", [
    {},
    {"as_of": PIN},
    {"known_at": PIN},
    {"as_of": PIN, "known_at": PIN},
])
def test_sdk_checks_returned_instants_without_an_additional_request(monkeypatch, changes):
    calls = sdk_transport(monkeypatch, payload(bounds=bounds()), features=[CAPABILITY])

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            selected = await sdk.recall(request(include_temporal_bounds=True, **changes))
            assert selected.temporal_bounds.as_of == PIN
            assert selected.temporal_bounds.known_at == PIN

    asyncio.run(scenario())
    assert len(calls) == 2
    assert json.loads(calls[1].content)["include_temporal_bounds"] is True


def test_sdk_reuses_startup_capability_for_multiple_opted_in_recalls(monkeypatch):
    calls = sdk_transport(monkeypatch, payload(bounds=bounds()), features=[CAPABILITY])

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            first = await sdk.recall(request(include_temporal_bounds=True))
            await sdk.recall(request(
                include_temporal_bounds=True, as_of=first.temporal_bounds.as_of,
                known_at=first.temporal_bounds.known_at,
            ))

    asyncio.run(scenario())
    assert [call.url.path for call in calls] == [
        "/v1/capabilities", "/v1/recall", "/v1/recall",
    ]


@pytest.mark.parametrize("explicit", [{"as_of": PIN}, {"known_at": PIN}])
def test_sdk_rejects_mismatched_explicit_microsecond_bound(monkeypatch, explicit):
    later = PIN + timedelta(microseconds=1)
    calls = sdk_transport(monkeypatch, payload(bounds=bounds(later, later)), features=[CAPABILITY])

    async def scenario():
        async with AsyncMemoryClient("https://memory.test", "fixed.identity.signature") as sdk:
            with pytest.raises(MemoryClientError, match="invalid_temporal_bounds"):
                await sdk.recall(request(include_temporal_bounds=True, **explicit))

    asyncio.run(scenario())
    assert len(calls) == 2


def test_bounded_server_current_bootstrap_is_singleton_and_does_not_mutate_issued_request():
    original = request(token_budget=8000, max_items=8)
    workflow = BoundedRecall(original, temporal_selection="server-current-v1")
    with pytest.raises(BoundedRecallError, match="invalid_search_plan"):
        workflow.requests(search("alpha", "beta"))
    first, = workflow.requests(search("alpha"))
    original_wire = first.model_dump_json()
    assert first.include_temporal_bounds and first.as_of is first.known_at is None
    assert not original.include_temporal_bounds
    workflow.record(first, RecallResult.model_validate(payload(bounds=bounds())))
    assert first.model_dump_json() == original_wire
    second, third = workflow.requests(search("beta", "gamma"))
    assert second.as_of == second.known_at == third.as_of == third.known_at == PIN
    workflow.record(second, RecallResult.model_validate(payload(bounds=bounds())))
    workflow.record(third, RecallResult.model_validate(payload(bounds=bounds())))
    assert workflow.final_request() is None
    result = workflow.finish(None)
    assert result.items == () and result.search_requests == 3 and not result.revalidated


@pytest.mark.parametrize("caller_bounds", [
    {"as_of": PIN}, {"known_at": PIN}, {"as_of": PIN, "known_at": PIN},
])
def test_server_current_mode_rejects_caller_bounds(caller_bounds):
    with pytest.raises(BoundedRecallError, match="invalid_bounded_recall"):
        BoundedRecall(
            request(max_items=8, **caller_bounds), temporal_selection="server-current-v1",
        )
    with pytest.raises(BoundedRecallError):
        BoundedRecall(request(max_items=8))


@pytest.mark.parametrize("bad", ["missing", "unequal", "recorded_future", "valid_future"])
def test_first_response_bounds_are_validated_before_any_item_admission(bad):
    workflow = BoundedRecall(
        request(token_budget=8000, max_items=8), temporal_selection="server-current-v1",
    )
    first, = workflow.requests(search("alpha"))
    facts = [item()]
    metadata = bounds()
    if bad == "missing":
        metadata = None
    elif bad == "unequal":
        metadata = bounds(PIN, PIN + timedelta(microseconds=1))
    elif bad == "recorded_future":
        facts = [item(recorded_at=PIN + timedelta(microseconds=1))]
    else:
        facts = [item(valid_from=PIN + timedelta(microseconds=1))]
    with pytest.raises(BoundedRecallError):
        workflow.record(first, RecallResult.model_validate(payload(facts, bounds=metadata)))
    with pytest.raises(BoundedRecallError, match="bounded_recall_closed"):
        _ = workflow.planning_items


def test_empty_first_search_pins_later_search_and_required_final_validation():
    workflow = BoundedRecall(
        request(token_budget=8000, max_items=8), temporal_selection="server-current-v1",
        planning_schedule="sequential-v1",
    )
    first, = workflow.requests(search("empty"))
    workflow.record(first, RecallResult.model_validate(payload(bounds=bounds())))
    second, = workflow.requests(search("match"))
    selected = item()
    second_result = RecallResult.model_validate(payload([selected], bounds=bounds()))
    assert second.as_of == second.known_at == PIN
    workflow.record(second, second_result)
    final = workflow.final_request()
    assert final.query == "" and final.as_of == final.known_at == PIN
    assert final.include_temporal_bounds
    completed = workflow.finish(second_result)
    assert completed.items == (selected,) and completed.revalidated


@pytest.mark.parametrize("step", ["next_search", "final_validation"])
def test_later_metadata_changes_are_never_readmitted(step):
    workflow = BoundedRecall(
        request(token_budget=8000, max_items=8), temporal_selection="server-current-v1",
        planning_schedule="sequential-v1",
    )
    first, = workflow.requests(search("alpha"))
    selected = item()
    workflow.record(first, RecallResult.model_validate(payload([selected], bounds=bounds())))
    wrong = RecallResult.model_validate(payload(
        [selected], bounds=bounds(PIN + timedelta(microseconds=1), PIN + timedelta(microseconds=1)),
    ))
    with pytest.raises(BoundedRecallError):
        if step == "next_search":
            second, = workflow.requests(search("beta"))
            workflow.record(second, wrong)
        else:
            workflow.final_request()
            workflow.finish(wrong)


def test_server_current_does_not_weaken_issued_request_identity_checks():
    workflow = BoundedRecall(request(max_items=8), temporal_selection="server-current-v1")
    first, = workflow.requests(search("alpha"))
    first.known_at = PIN
    with pytest.raises(BoundedRecallError, match="recall_request_mismatch"):
        workflow.record(first, RecallResult.model_validate(payload(bounds=bounds())))


@pytest.mark.integration
def test_fastapi_temporal_bounds_are_opt_in_and_echo_exact_resolved_parameters(env):
    base = {"scope_ids": [str(env.scopes[0])], "purpose": "temporal-compatibility"}
    default = env.client.post("/v1/recall", json=base, headers=env.headers())
    explicit_false = env.client.post(
        "/v1/recall", json={**base, "include_temporal_bounds": False}, headers=env.headers(),
    )
    assert default.status_code == explicit_false.status_code == 200
    assert default.content == explicit_false.content
    assert "temporal_bounds" not in default.json()
    assert "embedding_model" in default.json() and default.json()["embedding_model"] is None
    selected = env.client.post(
        "/v1/recall", json={**base, "include_temporal_bounds": True}, headers=env.headers(),
    )
    assert selected.status_code == 200 and selected.json()["items"] == []
    effective = RecallResult.model_validate(selected.json()).temporal_bounds
    assert effective.as_of == effective.known_at
    assert effective.as_of.utcoffset() is not None
    explicit = env.client.post("/v1/recall", json={
        **base, "include_temporal_bounds": True,
        "as_of": PIN.isoformat(), "known_at": (PIN - timedelta(days=1)).isoformat(),
    }, headers=env.headers())
    assert explicit.status_code == 200
    echoed = RecallResult.model_validate(explicit.json()).temporal_bounds
    assert echoed.as_of == PIN and echoed.known_at == PIN - timedelta(days=1)
    bad = env.client.post(
        "/v1/recall", json={**base, "include_temporal_bounds": 1}, headers=env.headers(),
    )
    assert bad.status_code == 422
    anonymous = env.client.get("/v1/capabilities")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "unauthenticated"
    assert "features" not in anonymous.json()
    capabilities = env.client.get("/v1/capabilities", headers=env.headers())
    assert capabilities.status_code == 200
    assert CAPABILITY in capabilities.json()["features"]
