import asyncio
import hashlib
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pg_agmemory.bounded_recall import BoundedRecallError
from pg_agmemory.development_memory import (
    FACT_PREFIX,
    AssertionRecord,
    BoundaryKeys,
    CurrentReference,
    DevelopmentMemory,
    DevelopmentMemoryError,
    MemoryBinding,
    MemoryState,
    parse_memory_decision,
    parse_memory_state,
)
from pg_agmemory.models import (
    Evidence,
    MemoryItem,
    ObserveResult,
    Recall,
    RecallFilters,
    RecallResult,
    RememberResult,
    ReviseAssertion,
    RevisionResult,
)
from pg_agmemory.service import build_context

NOW = datetime(2026, 9, 27, 1, tzinfo=UTC)
RECORDED = datetime(2026, 9, 26, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Transcript:
    text: str
    sha256: str
    included_message_ordinals: tuple[int, ...] = (0,)
    omitted_message_ordinals: tuple[int, ...] = ()


@dataclass(frozen=True)
class Reply:
    text: str
    receipt_ref: object


def transcript(text="Alpha uses Beta. Beta accepts signed payloads. EPISODE_ONLY_NONCE"):
    return Transcript(text, hashlib.sha256(text.encode("utf-8")).hexdigest())


def binding(arm="pg_agmemory", scope=None):
    return MemoryBinding(run_id="run-1", project_id="project-1", arm=arm, scope_id=scope or uuid4())


def keys(prefix="boundary"):
    return BoundaryKeys(
        observe=f"{prefix}-observe",
        create=tuple(f"{prefix}-create-{i}" for i in range(6)),
        revise=tuple(f"{prefix}-revise-{i}" for i in range(4)),
    )


def fact(text="Alpha uses Beta.", **changes):
    return MemoryItem(
        **{
            "memory_id": uuid4(), "revision": 1, "type": "assertion",
            "content": FACT_PREFIX + text, "recorded_at": RECORDED,
            "epistemic_status": "reported", "source": [uuid4()], **changes,
        },
    )


def state(bound, items=(), *, completed=1, pending=(), note=None):
    return MemoryState(
        format="development-memory-state-v1", binding=bound,
        completed_boundaries=completed, last_boundary_id=f"previous-{completed}", note=note,
        assertions=tuple(AssertionRecord(
            memory_id=item.memory_id, revision=item.revision,
            status="pending" if item.memory_id in pending else "active",
        ) for item in items),
    )


def proposal(text, source, **extra):
    offset = source.index(text)
    return {"text": text, "span": {"start": offset, "end": offset + len(text)}, **extra}


def decision(*, create=(), revise=(), forget=()):
    return json.dumps({
        "create": list(create), "revise": list(revise), "propose_forget": list(forget),
    })


def ref(item):
    return {"memory_id": str(item.memory_id), "revision": item.revision}


def plan(*terms):
    return json.dumps({"queries": [{"terms": list(terms)}]}) if terms else '{"queries":[]}'


def response(request, items=(), *, epoch=1, server_time=NOW, **changes):
    pack, selected, omitted = build_context(
        list(items), request.token_budget, required_count=len(request.required_memory_refs),
    )
    assert not omitted
    return RecallResult.model_validate({
        "items": selected, "context_pack": pack,
        "coverage": {
            "retrieval_complete": True, "synthesis_pending": False, "projection_pending": False,
            "jobs_pending": False, "lexical_incomplete": False, "vector_incomplete": False,
            "graph_used": False, "truncated": False,
        },
        "consistency": {"access_epoch": epoch, "deletion_epoch": 1},
        "search_profile": "en-snowball-v1", "retrieval_mode": "lexical", "embedding_model": None,
        "temporal_bounds": {
            "as_of": request.as_of or server_time, "known_at": request.known_at or server_time,
        } if request.include_temporal_bounds else None,
        "empty_reason": None if selected else "not_found", **changes,
    })


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.receipts = []

    def __call__(self, phase, prompt, planning_round):
        self.calls.append((phase, prompt, planning_round))
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(phase, prompt, planning_round)
        receipt = {"host_ordinal": len(self.calls), "opaque": "host-owned"}
        self.receipts.append(receipt)
        return Reply(value, receipt)


class FakeNative:
    def __init__(self, items=()):
        self.items = {item.memory_id: item.model_copy(deep=True) for item in items}
        self.reads = []
        self.writes = []
        self.episodes = {}
        self.loops = []
        self.opens = self.closes = 0
        self.epoch = 1
        self.db_now = NOW
        self.on_read = None
        self.on_write = None
        self.query_results = None

    @asynccontextmanager
    async def factory(self):
        self.opens += 1
        self.loops.append(asyncio.get_running_loop())
        try:
            yield self
        finally:
            self.closes += 1

    async def observe(self, request, *, idempotency_key):
        self.writes.append(("observe", request.model_copy(deep=True), idempotency_key))
        identity = uuid4()
        self.episodes[identity] = request.content
        if self.on_write:
            self.on_write("observe", request)
        return ObserveResult(memory_id=identity, revision=1)

    async def remember(self, request, *, idempotency_key):
        self.writes.append(("remember", request.model_copy(deep=True), idempotency_key))
        if self.on_write:
            self.on_write("remember", request)
        item = fact(request.value, source=[request.evidence[0].memory_id])
        self.items[item.memory_id] = item
        return RememberResult(memory_id=item.memory_id, revision=1, epistemic_status="reported")

    async def revise_assertion(self, memory_id, request, *, idempotency_key):
        self.writes.append(("revise", request.model_copy(deep=True), idempotency_key))
        assert self.items[memory_id].revision == request.expected_revision
        if self.on_write:
            self.on_write("revise", request)
        revision = request.expected_revision + 1
        self.items[memory_id] = fact(
            request.value, memory_id=memory_id, revision=revision,
            source=[request.evidence[0].memory_id],
        )
        return RevisionResult(
            memory_id=memory_id, revision=revision, epistemic_status="reported",
        )

    async def recall(self, request):
        selected_at = self.db_now
        self.reads.append(request.model_copy(deep=True))
        if self.on_read:
            override = self.on_read(request, len(self.reads))
            if override is not None:
                return override
        if request.required_memory_refs:
            items = [self.items[entry.memory_id] for entry in request.required_memory_refs]
        elif self.query_results is not None:
            items = self.query_results(request)
        else:
            items = [
                item for item in self.items.values()
                if all(term.lower() in item.content.lower() for term in request.query.split())
            ]
        value = response(
            request, items[:request.max_items], epoch=self.epoch, server_time=selected_at,
        )
        if request.required_memory_refs and len(self.items) > 1:
            value.coverage.truncated = True
        return value


def workflow(bound, *, native=None, model=None, saved=None, session=1, emit=None, clock=None):
    if native is None and bound.arm == "pg_agmemory":
        native = FakeNative()
    return DevelopmentMemory(
        bound, session_number=session, state=saved,
        native_factory=native.factory if native is not None else None,
        invoke=model or ScriptedModel([]), emit=emit or (lambda event: None),
        now=clock or (lambda: NOW),
    )


@pytest.mark.parametrize("session", [1, 2, 3])
def test_no_memory_is_null_and_never_opens_native_or_invokes(session):
    value = workflow(binding("no_memory"), session=session)
    result = asyncio.run(value.deliver("Current public brief"))
    assert result.text == "" and result.byte_count == 0 and result.empty_reason == "no_memory"
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.deliver("Current public brief"))
    with pytest.raises(DevelopmentMemoryError, match="memory_boundary_not_allowed"):
        asyncio.run(value.maintain(transcript(), boundary_id="b1", keys=None))


def test_strict_state_and_keys_require_explicit_uncoerced_references():
    bound = binding()
    saved = state(bound, [fact()])
    assert parse_memory_state(saved.model_dump_json()) == saved
    assert parse_memory_state("null") is None
    with pytest.raises(DevelopmentMemoryError):
        parse_memory_state("\u00a0null\u00a0")
    for mutate in [
        lambda data: data["assertions"][0].pop("revision"),
        lambda data: data["assertions"][0].update(revision=True),
        lambda data: data["assertions"][0].update(revision="1"),
        lambda data: data.update(note="forbidden text cache"),
        lambda data: data.update(completed_boundaries=True),
        lambda data: data.update(extra="unexpected"),
        lambda data: data["assertions"].append(data["assertions"][0]),
    ]:
        data = json.loads(saved.model_dump_json())
        mutate(data)
        with pytest.raises(DevelopmentMemoryError):
            parse_memory_state(json.dumps(data))
    with pytest.raises(ValidationError):
        CurrentReference(memory_id=uuid4())
    with pytest.raises(ValidationError):
        keys().model_validate({**keys().model_dump(), "create": ("same",) * 6})
    with pytest.raises(DevelopmentMemoryError):
        workflow(binding(), saved=saved, session=2)
    with pytest.raises(DevelopmentMemoryError):
        workflow(bound, saved=saved, session=3)
    with pytest.raises(DevelopmentMemoryError):
        workflow(bound, saved=saved, session=1)


@pytest.mark.parametrize("raw", [
    "{}",
    '{"create":[],"revise":[],"propose_forget":[],"extra":1}',
    '{"create":[],"create":[],"revise":[],"propose_forget":[]}',
    '{"create":[],"revise":[],"propose_forget":[],"extra":NaN}',
    '{"create":[{"text":"x","span":{"start":true,"end":1}}],"revise":[],"propose_forget":[]}',
    '{"create":[{"text":" x","span":{"start":0,"end":1}}],"revise":[],"propose_forget":[]}',
    '{"create":[{"text":"\\ud800","span":{"start":0,"end":1}}],"revise":[],"propose_forget":[]}',
    "[" * 40 + "]" * 40,
    " " * 65537,
])
def test_strict_decision_json_rejects_malformed_duplicate_deep_or_oversized(raw):
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_decision"):
        parse_memory_decision(raw)


def test_decision_list_caps_utf8_and_overlap():
    source = "x" * 257
    for raw in [
        decision(create=[proposal(source, source)]),
        decision(create=[proposal(str(i), str(i)) for i in range(7)]),
        decision(revise=[
            proposal("x", "x", memory_id=str(uuid4()), revision=1) for _ in range(5)
        ]),
        decision(forget=[{"memory_id": str(uuid4()), "revision": 1} for _ in range(13)]),
        decision(create=[proposal("x", "x"), proposal("x", "x")]),
    ]:
        with pytest.raises(DevelopmentMemoryError):
            parse_memory_decision(raw)
    identity = str(uuid4())
    with pytest.raises(DevelopmentMemoryError):
        parse_memory_decision(decision(
            revise=[proposal("x", "x", memory_id=identity, revision=1)],
            forget=[{"memory_id": identity, "revision": 1}],
        ))
    parsed = parse_memory_decision(decision())
    assert parsed.create == parsed.revise == parsed.propose_forget == ()


@pytest.mark.parametrize("note", ["", "handoff correction", "界" * 682])
def test_handoff_boundary_is_fresh_direct_single_use_and_preserves_receipt(note):
    bound = binding("handoff")
    model = ScriptedModel([json.dumps({"note": note})])
    value = workflow(bound, model=model)
    result = asyncio.run(value.maintain(transcript(), boundary_id="b1", keys=None))
    assert result.state.note == note and not result.state.assertions
    assert result.model_receipt_ref is model.receipts[0]
    assert [call[0] for call in model.calls] == ["handoff"]
    assert model.calls[0][2] is None
    later = workflow(bound, saved=result.state, session=2)
    delivery = asyncio.run(later.deliver("Current public brief"))
    assert delivery.text == note and delivery.byte_count == len(note.encode("utf-8"))
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.maintain(transcript(), boundary_id="b2", keys=None))


@pytest.mark.parametrize("raw", [
    '{"note":"' + "x" * 2049 + '"}',
    json.dumps({"note": "界" * 683}),
    '{"note":"a","note":"b"}',
    "{}",
    "not JSON",
])
def test_handoff_oversize_or_malformed_is_not_truncated_or_repaired(raw):
    model = ScriptedModel([raw])
    value = workflow(binding("handoff"), model=model)
    with pytest.raises(DevelopmentMemoryError, match="invalid_handoff_note"):
        asyncio.run(value.maintain(transcript(), boundary_id="b", keys=None))
    assert len(model.calls) == 1
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.deliver("Current public brief"))


def test_initial_pg_maintenance_observes_exact_visible_text_and_publishes_only_refs():
    bound = binding()
    source = transcript()
    native = FakeNative()
    model = ScriptedModel([decision(create=[
        proposal("Alpha uses Beta.", source.text),
        proposal("Beta accepts signed payloads.", source.text),
    ])])
    events = []
    result = asyncio.run(workflow(bound, native=native, model=model, emit=events.append).maintain(
        source, boundary_id="boundary-1", keys=keys(),
    ))
    assert native.opens == native.closes == 1
    assert len(model.calls) == 1 and model.calls[0][0] == "memory_decision"
    assert len(native.episodes) == 1 and list(native.episodes.values()) == [source.text]
    observed = native.writes[0][1]
    assert observed.content == source.text and not observed.auto_embed and not observed.auto_extract
    assert len(result.created_refs) == 2 and result.state.completed_boundaries == 1
    assert result.native_requests.observe == 1 and result.native_requests.remember == 2
    assert result.native_requests.inventory_read == 0
    assert result.native_requests.verification_read == 2
    assert "Alpha" not in result.state.model_dump_json()
    assert "EPISODE_ONLY_NONCE" not in result.state.model_dump_json()
    assert result.model_receipt_ref is model.receipts[0]
    for operation, request, _ in native.writes[1:]:
        assert operation == "remember" and request.scope_id == bound.scope_id
        assert request.evidence[0].memory_id == result.observation_ref.memory_id
        assert request.evidence[0].quote in source.text
    assert all(read.query == "" and read.max_items == len(read.required_memory_refs) == 1
               for read in native.reads)
    assert events[-1]["kind"] == "memory_retention"
    assert result.destructive_calls == 0


def test_empty_decision_records_empty_review_without_calling_nonempty_helper(monkeypatch):
    def forbidden(*args):
        pytest.fail("Empty registry must not call review_retention")

    monkeypatch.setattr("pg_agmemory.development_memory.review_retention", forbidden)
    native = FakeNative()
    bound = binding()
    result = asyncio.run(workflow(
        bound, native=native, model=ScriptedModel([decision()]),
    ).maintain(transcript(), boundary_id="b1", keys=keys()))
    assert result.review == "empty" and result.state.assertions == ()
    assert len(native.writes) == 1 and native.reads == []
    later = workflow(bound, native=native, session=2, saved=result.state)
    delivered = asyncio.run(later.deliver("Current public brief"))
    assert delivered.text == "" and delivered.empty_reason == "no_eligible_assertions"
    assert native.opens == 1 and not native.reads


def test_revision_pending_review_and_postreview_readability_without_pending_prompt_text():
    first, pending = fact("old value"), fact("PENDING_ONLY_NONCE")
    bound = binding()
    old = state(bound, [first, pending], pending=[pending.memory_id])
    native = FakeNative([first, pending])
    source = transcript("The new value is approved.")
    model = ScriptedModel([decision(
        revise=[proposal("new value", source.text, **ref(first))],
        forget=[ref(pending)],
    )])
    result = asyncio.run(workflow(
        bound, native=native, saved=old, session=2, model=model,
    ).maintain(source, boundary_id="b2", keys=keys()))
    assert "PENDING_ONLY_NONCE" not in model.calls[0][1]
    assert str(pending.memory_id) in model.calls[0][1]
    assert len(result.revisions) == 1 and result.revisions[0].current.revision == 2
    assert result.pending_readable_checked == result.pending_readable_total == 1
    assert result.pending_refs[0].memory_id == pending.memory_id
    assert len(native.items) == 2 and pending.memory_id in native.items
    assert result.native_requests.inventory_read == result.native_requests.verification_read == 2
    assert old.assertions[0].revision == 1


@pytest.mark.parametrize("invalid", ["unknown", "stale", "pending", "span", "capacity"])
def test_entire_decision_is_prevalidated_before_any_assertion_mutation(invalid):
    items = [fact(f"value {i}") for i in range(12 if invalid == "capacity" else 1)]
    bound = binding()
    old = state(bound, items, pending=[items[0].memory_id] if invalid == "pending" else [])
    native = FakeNative(items)
    source = transcript("new value")
    data = {"create": [proposal("new value", source.text)], "revise": [], "propose_forget": []}
    if invalid == "unknown":
        data["propose_forget"] = [{"memory_id": str(uuid4()), "revision": 1}]
    elif invalid in ("pending", "stale"):
        data["revise"] = [proposal(
            "new value", source.text, memory_id=str(items[0].memory_id),
            revision=2 if invalid == "stale" else 1,
        )]
    elif invalid == "span":
        data["create"].append({"text": "bad fact", "span": {"start": 100, "end": 105}})
    value = workflow(
        bound, native=native, model=ScriptedModel([json.dumps(data)]), saved=old, session=2,
    )
    with pytest.raises(DevelopmentMemoryError):
        asyncio.run(value.maintain(source, boundary_id="b2", keys=keys()))
    assert [entry[0] for entry in native.writes] == ["observe"]
    assert len(value.completed_refs) == 1


@pytest.mark.parametrize("change", ["hash", "order", "overlap", "whitespace", "oversize"])
def test_bad_transcript_fails_before_open_or_observe(change):
    source = transcript()
    data = dict(vars(source))
    if change == "hash":
        data["sha256"] = "0" * 64
    elif change == "order":
        data["included_message_ordinals"] = (2, 1)
    elif change == "overlap":
        data["omitted_message_ordinals"] = (0,)
    else:
        data = vars(transcript(" text " if change == "whitespace" else "x" * 24577))
    native = FakeNative()
    with pytest.raises(DevelopmentMemoryError, match="invalid_boundary_transcript"):
        asyncio.run(workflow(binding(), native=native).maintain(
            Transcript(**data), boundary_id="b1", keys=keys(),
        ))
    assert native.opens == 0 and not native.writes


def test_pending_only_delivery_never_opens_client():
    item = fact("PENDING_ONLY_NONCE")
    bound = binding()
    native = FakeNative([item])
    value = workflow(
        bound, native=native, saved=state(bound, [item], pending=[item.memory_id]), session=2,
    )
    result = asyncio.run(value.deliver("Current public brief"))
    assert result.text == "" and result.planning_calls == result.search_requests == 0
    assert native.opens == 0


def test_sequential_delivery_uses_only_observed_assertions_and_fresh_final_context():
    route = fact("Alpha uses Beta.")
    endpoint = fact("Beta accepts signed payloads.")
    pending = fact("Alpha PENDING_ONLY_NONCE")
    bound = binding()
    native = FakeNative([route, endpoint, pending])
    model = ScriptedModel([plan("Alpha"), plan("Beta"), plan()])
    value = workflow(
        bound, native=native, model=model, session=2,
        saved=state(bound, [route, endpoint, pending], pending=[pending.memory_id]),
    )
    result = asyncio.run(value.deliver("How does Alpha work?"))
    assert result.revalidated and result.final_validations == 1 and result.search_requests == 2
    assert result.queries == ("Alpha", "Beta") and result.planning_calls == 3
    assert {item.memory_id for item in result.refs} == {route.memory_id, endpoint.memory_id}
    assert "signed payloads" in result.text and "PENDING_ONLY_NONCE" not in result.text
    assert "Beta accepts signed payloads." not in model.calls[0][1]
    assert "signed payloads" not in model.calls[1][1]
    assert all("PENDING_ONLY_NONCE" not in prompt for _, prompt, _ in model.calls)
    assert [call[2] for call in model.calls] == [1, 2, 3]
    assert all(len(call[1].encode("utf-8")) <= 8000 for call in model.calls)
    for request in native.reads:
        assert request.scope_ids == [bound.scope_id]
        assert request.filters.kind == "assertion"
        assert request.search_profile == "en-snowball-v1"
        assert request.include_temporal_bounds
    assert native.reads[0].as_of is native.reads[0].known_at is None
    assert all(request.as_of == request.known_at == NOW for request in native.reads[1:])
    assert all(not request.required_memory_refs for request in native.reads[:-1])
    assert native.reads[-1].query == ""
    assert native.reads[-1].max_items == len(native.reads[-1].required_memory_refs) == 2
    assert native.opens == native.closes == 1


def test_zero_matches_empty_stop_has_no_validation_and_no_fallback():
    item = fact("some fact")
    bound = binding()
    native = FakeNative([item])
    model = ScriptedModel([plan("absent"), plan()])
    result = asyncio.run(workflow(
        bound, native=native, model=model, session=2, saved=state(bound, [item]),
    ).deliver("Current public brief"))
    assert result.text == "" and result.empty_reason == "zero_matches"
    assert result.search_requests == 1 and result.final_validations == 0
    assert not result.revalidated and len(model.calls) == 2 and len(native.reads) == 1


def test_four_round_budget_and_delivery_crop_whole_current_items_only():
    items = [fact("Anchor " + str(i) + "x" * 240) for i in range(8)]
    bound = binding()
    native = FakeNative(items)
    native.query_results = lambda request: list(native.items.values())
    model = ScriptedModel([plan(f"cue{i}") for i in range(4)])
    result = asyncio.run(workflow(
        bound, native=native, model=model, session=3, saved=state(bound, items, completed=2),
    ).deliver("Current public brief"))
    assert result.planning_calls == result.search_requests == 4
    assert result.final_validations == 1 and len(native.reads) == 5
    assert len(native.reads[-1].required_memory_refs) == 8
    assert 0 < len(result.refs) < 8 and result.delivery_truncated
    assert len(result.refs) + len(result.omitted_refs) == 8
    assert result.byte_count == len(result.text.encode("utf-8")) <= 2048
    for item in items:
        if item.memory_id in {ref.memory_id for ref in result.refs}:
            assert json.dumps(item.content) in result.text
        else:
            assert str(item.memory_id) not in result.text


def test_question_prefix_is_utf8_and_codepoint_bounded_with_reported_omissions():
    item = fact()
    bound = binding()
    model = ScriptedModel([plan("absent"), plan()])
    events = []
    asyncio.run(workflow(
        bound, native=FakeNative([item]), model=model, session=2,
        saved=state(bound, [item]), emit=events.append,
    ).deliver("界" * 700))
    prompt = model.calls[0][1]
    data = json.loads(prompt.split("INPUT=", 1)[1])
    assert data["question"] == "界" * 341
    event = next(event for event in events if event["kind"] == "memory_question")
    assert event["data"]["omitted_code_points"] == 359


@pytest.mark.parametrize(
    "bad", ["episode", "unknown", "old_revision", "profile", "vector", "context"],
)
def test_bad_native_response_fails_before_followup_or_work_delivery(bad):
    item = fact()
    bound = binding()
    native = FakeNative([item])

    def inject(request, count):
        result = response(request, [item])
        if bad == "episode":
            result.items[0].type = "episode"
        elif bad == "unknown":
            result.items[0].memory_id = uuid4()
        elif bad == "old_revision":
            result.items[0].revision = 2
        elif bad == "profile":
            result.search_profile = "simple-v1"
        elif bad == "vector":
            result.retrieval_mode = "vector"
        else:
            result.context_pack.text += " CORRUPTED"
        return result

    native.on_read = inject
    model = ScriptedModel([plan("Alpha")])
    events = []
    value = workflow(
        bound, native=native, model=model, session=2,
        saved=state(bound, [item]), emit=events.append,
    )
    with pytest.raises(DevelopmentMemoryError):
        asyncio.run(value.deliver("Current public brief"))
    assert len(model.calls) == 1 and len(native.reads) == 1
    assert not any(event["kind"] == "memory_delivery" for event in events)
    assert native.closes == 1
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.deliver("Current public brief"))


@pytest.mark.parametrize("tamper", ["scope", "filter", "time", "epoch", "content"])
def test_request_tampering_epoch_or_final_content_change_never_delivers(tamper):
    item = fact()
    bound = binding()
    native = FakeNative([item])

    def inject(request, count):
        if tamper == "scope":
            request.scope_ids = [uuid4()]
        elif tamper == "filter":
            request.filters = None
        elif tamper == "time":
            request.as_of = RECORDED
        elif count == 2 and tamper == "epoch":
            native.epoch += 1
        elif count == 2 and tamper == "content":
            native.items[item.memory_id].content = FACT_PREFIX + "changed content"

    native.on_read = inject
    value = workflow(
        bound, native=native, model=ScriptedModel([plan("Alpha"), plan()]),
        session=2, saved=state(bound, [item]),
    )
    with pytest.raises((DevelopmentMemoryError, BoundedRecallError)):
        asyncio.run(value.deliver("Current public brief"))
    assert len(native.reads) <= 2


def test_partial_unknown_write_preserves_known_receipts_and_propagates_original_error():
    class UnknownWrite(Exception):
        outcome_unknown = True

    failure = UnknownWrite("unknown")
    native = FakeNative()
    source = transcript("first fact; second fact")
    model = ScriptedModel([decision(create=[
        proposal("first fact", source.text), proposal("second fact", source.text),
    ])])

    def inject(operation, request):
        if operation == "remember" and len(native.items) == 1:
            raise failure

    native.on_write = inject
    events = []
    value = workflow(binding(), native=native, model=model, emit=events.append)
    with pytest.raises(UnknownWrite) as raised:
        asyncio.run(value.maintain(source, boundary_id="b1", keys=keys()))
    assert raised.value is failure
    assert len(value.completed_refs) == 2 and len(native.items) == 1
    assert len(model.calls) == 1 and native.closes == 1
    assert [entry[0] for entry in native.writes] == ["observe", "remember", "remember"]
    assert not any(event["kind"] == "memory_retention" for event in events)
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.maintain(source, boundary_id="new-id-not-a-retry", keys=keys("new")))


@pytest.mark.parametrize("where", ["invoke", "sink", "receipt_sink"])
def test_controller_callback_failures_propagate_without_empty_fallback(where):
    failure = RuntimeError("controller sentinel")
    native = FakeNative()
    model = ScriptedModel([failure if where == "invoke" else decision()])

    def emit(event):
        if where == "sink" or (
            where == "receipt_sink" and event["kind"] == "memory_native_receipt"
        ):
            raise failure

    value = workflow(binding(), native=native, model=model, emit=emit)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(value.maintain(transcript(), boundary_id="b1", keys=keys()))
    assert raised.value is failure
    assert len(native.writes) == (0 if where == "sink" else 1)
    assert len(value.completed_refs) == (0 if where == "sink" else 1)


def test_separate_asyncio_runs_use_fresh_clients_and_boundary_does_not_repeat_retrieval():
    item = fact()
    bound = binding()
    native = FakeNative([item])
    old = state(bound, [item])
    model = ScriptedModel([plan("Alpha"), plan(), decision()])
    value = workflow(bound, native=native, model=model, saved=old, session=2)
    asyncio.run(value.deliver("Current public brief"))
    before = len(native.reads)
    result = asyncio.run(value.maintain(transcript(), boundary_id="b2", keys=keys()))
    assert native.opens == native.closes == 2 and native.loops[0] is not native.loops[1]
    assert len(native.reads) - before == 2
    assert result.state.completed_boundaries == 2
    fresh = workflow(
        bound, native=native, model=ScriptedModel([decision()]), saved=old, session=2,
    )
    asyncio.run(fresh.maintain(
        transcript(), boundary_id="independent-b2", keys=keys("independent"),
    ))
    assert native.opens == native.closes == 3


@pytest.mark.parametrize("replies,reads", [
    ([plan()], 0),
    ([plan("Alpha"), plan("Alpha")], 1),
    (['{"queries":[{"terms":["Alpha"]},{"terms":["Beta"]}]}'], 0),
])
def test_empty_initial_duplicate_or_batched_plan_is_not_repaired(replies, reads):
    item = fact()
    bound = binding()
    native = FakeNative([item])
    model = ScriptedModel(replies)
    value = workflow(
        bound, native=native, model=model, session=2, saved=state(bound, [item]),
    )
    with pytest.raises(BoundedRecallError):
        asyncio.run(value.deliver("Current public brief"))
    assert len(native.reads) == reads and len(model.calls) == len(replies)


def test_inventory_serialized_budget_is_not_a_raw_content_proxy():
    items = [fact("\x01" * 256) for _ in range(12)]
    bound = binding()
    native = FakeNative(items)
    model = ScriptedModel([])
    value = workflow(bound, native=native, model=model, session=2, saved=state(bound, items))
    with pytest.raises(DevelopmentMemoryError, match="memory_inventory_too_large"):
        asyncio.run(value.maintain(transcript(), boundary_id="b2", keys=keys()))
    assert len(native.reads) == 12 and len(native.writes) == 1 and not model.calls


def test_incomplete_required_inventory_is_failure_before_decision():
    item = fact()
    bound = binding()
    native = FakeNative([item])

    def omit(request, count):
        without_required = request.model_copy(update={"required_memory_refs": []})
        return response(without_required)

    native.on_read = omit
    model = ScriptedModel([])
    value = workflow(
        bound, native=native, model=model, session=2, saved=state(bound, [item]),
    )
    with pytest.raises(DevelopmentMemoryError, match="incomplete_memory_inventory"):
        asyncio.run(value.maintain(transcript(), boundary_id="b2", keys=keys()))
    assert not model.calls and len(native.writes) == len(native.reads) == 1


def test_epoch_change_after_writes_blocks_continuity_state():
    item = fact()
    bound = binding()
    native = FakeNative([item])

    def change_epoch(request, count):
        if count == 2:
            native.epoch += 1

    native.on_read = change_epoch
    value = workflow(
        bound, native=native, model=ScriptedModel([decision()]),
        session=2, saved=state(bound, [item]),
    )
    with pytest.raises(DevelopmentMemoryError, match="request_state_changed"):
        asyncio.run(value.maintain(transcript(), boundary_id="b2", keys=keys()))
    assert len(value.completed_refs) == 1


def test_postwrite_inventory_takes_fresh_time_after_all_commits_and_pins_the_pass():
    original = fact("original fact")

    class AdvancingNative(FakeNative):
        async def remember(self, request, *, idempotency_key):
            self.db_now += timedelta(seconds=1)
            receipt = await super().remember(request, idempotency_key=idempotency_key)
            self.items[receipt.memory_id].recorded_at = self.db_now
            return receipt

    native = AdvancingNative([original])
    bound = binding()
    source = transcript("first new fact; second new fact")
    model = ScriptedModel([decision(create=[
        proposal("first new fact", source.text), proposal("second new fact", source.text),
    ])])

    def advance_during_verification(request, count):
        if count > 1:
            native.db_now += timedelta(seconds=1)

    native.on_read = advance_during_verification
    result = asyncio.run(workflow(
        bound, native=native, model=model, session=2, saved=state(bound, [original]),
        clock=lambda: NOW - timedelta(days=1),
    ).maintain(source, boundary_id="b2", keys=keys()))
    assert len(result.created_refs) == 2
    assert native.reads[0].as_of is native.reads[0].known_at is None
    assert len(native.reads[1:]) == 3
    assert native.reads[1].as_of is native.reads[1].known_at is None
    assert all(
        request.as_of == request.known_at == NOW + timedelta(seconds=2)
        for request in native.reads[2:]
    )


def test_mutation_request_tampering_preserves_receipt_but_never_publishes_state():
    native = FakeNative()
    source = transcript("new fact")

    def tamper(operation, request):
        if operation == "remember":
            request.value = "tampered fact"

    native.on_write = tamper
    value = workflow(
        binding(), native=native,
        model=ScriptedModel([decision(create=[proposal("new fact", source.text)])]),
    )
    with pytest.raises(DevelopmentMemoryError, match="request_state_changed") as failure:
        asyncio.run(value.maintain(source, boundary_id="b1", keys=keys()))
    assert len(failure.value.completed_refs) == 2 and len(native.items) == 1


def test_prompt_envelope_expansion_fails_before_model_dispatch():
    model = ScriptedModel([])
    value = workflow(binding("handoff"), model=model)
    with pytest.raises(DevelopmentMemoryError, match="memory_prompt_budget_exhausted"):
        asyncio.run(value.maintain(
            transcript("\x01" * 12000), boundary_id="b1", keys=None,
        ))
    assert not model.calls


def test_cancellation_is_not_swallowed_and_closes_workflow():
    class CancelledNative(FakeNative):
        async def observe(self, request, *, idempotency_key):
            raise asyncio.CancelledError

    native = CancelledNative()
    value = workflow(binding(), native=native)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(value.maintain(transcript(), boundary_id="b1", keys=keys()))
    assert native.opens == native.closes == 1
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.maintain(transcript(), boundary_id="b2", keys=keys("other")))


def test_no_memory_import_and_construction_do_not_require_optional_sdk():
    program = """
import builtins
from datetime import UTC, datetime
from uuid import uuid4
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'httpx' or name.startswith('pg_agmemory.sdk'):
        raise ImportError('optional SDK intentionally unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from pg_agmemory.development_memory import DevelopmentMemory, MemoryBinding
DevelopmentMemory(
    MemoryBinding(run_id='r', project_id='p', arm='no_memory', scope_id=uuid4()),
    session_number=3, state=None, native_factory=None,
    invoke=lambda *args: None, emit=lambda event: None, now=lambda: datetime.now(UTC),
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def live_native_factory(env, http):
    """Keep real clocks/guards; annotate failed reads with safe, fixture-only evidence."""
    import psycopg
    from psycopg.rows import dict_row

    from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

    class DiagnosticNative:
        def __init__(self, sdk):
            self.sdk = sdk

        def __getattr__(self, name):
            if name not in ("observe", "remember", "revise_assertion"):
                raise AttributeError(name)
            return getattr(self.sdk, name)

        async def recall(self, request):
            try:
                return await self.sdk.recall(request)
            except MemoryClientError as error:
                if error.error.code == "not_found" and request.required_memory_refs:
                    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
                        db_clock = conn.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
                        rows = conn.execute(
                            """SELECT r.assertion_id,r.scope_id,r.revision,
                                      lower(r.system_time) AS known_from,
                                      upper(r.system_time) AS known_until,
                                      r.system_time @> %s::timestamptz AS visible_at_pin,
                                      r.system_time @> clock_timestamp() AS current_at_db_clock,
                                      r.valid_time @> %s::timestamptz AS valid_at_pin
                               FROM memory.assertion_revision r
                               JOIN unnest(%s::uuid[],%s::integer[]) wanted(id,revision)
                                 ON r.assertion_id=wanted.id AND r.revision=wanted.revision
                               WHERE r.tenant_id=%s""",
                            (
                                request.known_at, request.as_of,
                                [ref.memory_id for ref in request.required_memory_refs],
                                [ref.revision for ref in request.required_memory_refs],
                                env.tenants[0],
                            ),
                        ).fetchall()
                    error.add_note(json.dumps({
                        "native_read_diagnostics": {
                            "requested_scopes": request.scope_ids,
                            "controller_known_at": request.known_at,
                            "controller_as_of": request.as_of,
                            "controller_after_error": datetime.now(UTC),
                            "database_clock": db_clock,
                            "requested_revision_metadata": rows,
                        },
                    }, default=str, sort_keys=True))
                raise

    @asynccontextmanager
    async def factory():
        async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
            yield DiagnosticNative(sdk)

    return factory


@pytest.mark.integration
def test_real_sdk_boundary_revision_pending_and_sequential_delivery(env, api_process):
    bound = binding(scope=env.scopes[0])
    source = transcript(
        "Cedar requires Willow. Willow accepts unsigned payloads. "
        "Cedar pending obsolete guidance. EPISODE_ONLY_NONCE"
    )
    first_model = ScriptedModel([decision(create=[
        proposal("Cedar requires Willow.", source.text),
        proposal("Willow accepts unsigned payloads.", source.text),
        proposal("Cedar pending obsolete guidance.", source.text),
    ])])
    with api_process("development-memory-sdk.log") as (http, _):
        factory = live_native_factory(env, http)

        def component(session, saved, model):
            return DevelopmentMemory(
                bound, session_number=session, state=saved, native_factory=factory,
                invoke=model, emit=lambda event: None,
                now=lambda: datetime.now(UTC) - timedelta(days=1),
            )

        first = asyncio.run(component(1, None, first_model).maintain(
            source, boundary_id="native-boundary-1", keys=keys("native-1"),
        ))
        route, endpoint, pending = first.created_refs
        revised_source = transcript("Willow accepts signed payloads. CURRENT_EPISODE_ONLY_NONCE")
        second_model = ScriptedModel([decision(
            revise=[proposal(
                "Willow accepts signed payloads.", revised_source.text,
                memory_id=str(endpoint.memory_id), revision=endpoint.revision,
            )],
            forget=[{"memory_id": str(pending.memory_id), "revision": pending.revision}],
        )])
        second = asyncio.run(component(2, first.state, second_model).maintain(
            revised_source, boundary_id="native-boundary-2", keys=keys("native-2"),
        ))
        assert second.pending_readable_checked == second.pending_readable_total == 1
        planner = ScriptedModel([plan("Cedar", "require"), plan("Willow", "accept"), plan()])
        delivered = asyncio.run(component(3, second.state, planner).deliver(
            "What does Cedar require, and which payloads does that route accept?",
        ))
        assert delivered.revalidated and delivered.search_requests == 2
        assert delivered.final_validations == 1
        assert {entry.memory_id for entry in delivered.refs} == {
            route.memory_id, endpoint.memory_id,
        }
        assert "signed payloads" in delivered.text and "unsigned payloads" not in delivered.text
        for _, prompt, _ in planner.calls:
            assert "EPISODE_ONLY_NONCE" not in prompt
            assert "unsigned payloads" not in prompt
            assert "obsolete guidance" not in prompt
        assert "EPISODE_ONLY_NONCE" not in delivered.text
        assert "obsolete guidance" not in delivered.text
        assert len(delivered.text.encode("utf-8")) == delivered.byte_count <= 2048

        async def independent_pending_read():
            async with factory() as sdk:
                return await sdk.recall(Recall(
                    query="", scope_ids=[bound.scope_id], purpose="independent-pending-proof",
                    filters=RecallFilters(kind="assertion"), search_profile="en-snowball-v1",
                    include_temporal_bounds=True, max_items=1, token_budget=8000,
                    required_memory_refs=[pending.native()],
                ))

        independent = asyncio.run(independent_pending_read())
        assert independent.items[0].memory_id == pending.memory_id
        assert "obsolete guidance" in independent.items[0].content
        assert second.destructive_calls == 0


@pytest.mark.integration
def test_real_sdk_foreign_scope_and_stale_registry_refs_fail_inventory(env, api_process):
    from pg_agmemory.sdk import MemoryClientError

    first_bound = binding(scope=env.scopes[0])
    source = transcript("Cedar route is Willow.")
    with api_process("development-memory-scope.log") as (http, _):
        factory = live_native_factory(env, http)

        def component(bound, saved, session, model):
            return DevelopmentMemory(
                bound, session_number=session, state=saved, native_factory=factory,
                invoke=model, emit=lambda event: None,
                now=lambda: datetime.now(UTC) + timedelta(days=1),
            )

        first = asyncio.run(component(
            first_bound, None, 1,
            ScriptedModel([decision(create=[proposal("Cedar route is Willow.", source.text)])]),
        ).maintain(source, boundary_id="scope-b1", keys=keys("scope-1")))
        foreign_bound = binding(scope=env.scopes[2])
        foreign = MemoryState(
            **{**first.state.model_dump(), "binding": foreign_bound},
        )
        model = ScriptedModel([])
        with pytest.raises(MemoryClientError):
            asyncio.run(component(foreign_bound, foreign, 2, model).maintain(
                source, boundary_id="scope-b2", keys=keys("scope-2"),
            ))
        assert model.calls == []

        async def external_revision():
            async with factory() as sdk:
                original = first.created_refs[0]
                return await sdk.revise_assertion(
                    original.memory_id,
                    ReviseAssertion(
                        expected_revision=original.revision, value="Cedar now requires Rowan.",
                        evidence=[Evidence(
                            memory_id=first.observation_ref.memory_id, quote=source.text,
                        )],
                        explicit_intent=True, reason="external current revision test",
                    ),
                    idempotency_key="external-current-revision",
                )

        assert asyncio.run(external_revision()).revision == 2
        stale_model = ScriptedModel([])
        with pytest.raises(MemoryClientError) as failure:
            asyncio.run(component(first_bound, first.state, 2, stale_model).maintain(
                source, boundary_id="stale-b2", keys=keys("stale-2"),
            ))
        assert failure.value.error.code == "not_found" and stale_model.calls == []


@pytest.mark.integration
def test_real_sdk_epoch_change_between_search_and_validation_aborts(env, api_process):
    import psycopg

    bound = binding(scope=env.scopes[0])
    source = transcript("Cedar requires Willow.")
    with api_process("development-memory-epoch.log") as (http, _):
        factory = live_native_factory(env, http)

        def component(saved, session, model):
            return DevelopmentMemory(
                bound, session_number=session, state=saved, native_factory=factory,
                invoke=model, emit=lambda event: None,
                now=lambda: datetime.now(UTC) - timedelta(hours=6),
            )

        captured = asyncio.run(component(None, 1, ScriptedModel([
            decision(create=[proposal("Cedar requires Willow.", source.text)]),
        ])).maintain(source, boundary_id="epoch-b1", keys=keys("epoch-1")))

        def change_epoch(phase, prompt, planning_round):
            assert phase == "memory_plan" and planning_round == 2
            with psycopg.connect(env.admin_url) as conn:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(env.tenants[0]),),
                )
                conn.execute(
                    "UPDATE memory.tenant SET access_epoch=access_epoch+1 WHERE id=%s",
                    (env.tenants[0],),
                )
            return plan()

        model = ScriptedModel([plan("Cedar"), change_epoch])
        value = component(captured.state, 2, model)
        with pytest.raises(DevelopmentMemoryError, match="request_state_changed"):
            asyncio.run(value.deliver("What does Cedar require?"))
        assert len(model.calls) == 2
