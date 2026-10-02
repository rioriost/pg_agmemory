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

from pg_agmemory import development_memory
from pg_agmemory.bounded_recall import BoundedRecallError
from pg_agmemory.development_memory import (
    FACT_PREFIX,
    MAINTENANCE_PROTOCOL,
    RETRIEVAL_POLICY,
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
        format="development-memory-state-v2", binding=bound,
        completed_boundaries=completed, last_boundary_id=f"previous-{completed}", note=note,
        assertions=tuple(AssertionRecord(
            memory_id=item.memory_id, revision=item.revision,
            status="pending" if item.memory_id in pending else "active",
        ) for item in items),
    )


def proposal(text, source, **extra):
    offset = source.index(text)
    return {"text": text, "span": {"start": offset, "end": offset + len(text)}, **extra}


def padded_proposal(text, source, **extra):
    value = proposal(text, source, **extra)
    span = value["span"]
    while span["start"] > 0 and source[span["start"] - 1].isspace():
        span["start"] -= 1
    while span["end"] < len(source) and source[span["end"]].isspace():
        span["end"] += 1
    return value


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
        now=clock or (lambda: NOW), maintenance_protocol=MAINTENANCE_PROTOCOL,
        retrieval_policy=RETRIEVAL_POLICY,
    )


@pytest.mark.parametrize("protocol", [
    None, True, 2, "", "development-maintenance-v1", "development-maintenance-v2",
    "development-maintenance-v2 ", "development-maintenance-v3 ",
])
@pytest.mark.parametrize("arm", ["no_memory", "handoff", "pg_agmemory"])
def test_maintenance_protocol_rejects_before_callbacks_or_native(protocol, arm):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Invalid protocol must fail during initialization")

    with pytest.raises(DevelopmentMemoryError) as failure:
        DevelopmentMemory(
            binding(arm), session_number=1, state=None, native_factory=forbidden,
            invoke=forbidden, emit=forbidden, now=forbidden, maintenance_protocol=protocol,
            retrieval_policy=RETRIEVAL_POLICY,
        )
    assert failure.value.code == "invalid_memory_maintenance_protocol"
    assert failure.value.phase == "initialization"
    assert not failure.value.outcome_unknown and failure.value.completed_refs == ()
    assert calls == []


def test_maintenance_protocol_has_no_constructor_default():
    def forbidden(*args, **kwargs):
        pytest.fail("Missing protocol must fail at the signature")

    arguments = {
        "session_number": 1, "state": None, "native_factory": forbidden,
        "invoke": forbidden, "emit": forbidden, "now": forbidden,
        "retrieval_policy": RETRIEVAL_POLICY,
    }
    with pytest.raises(TypeError, match="maintenance_protocol"):
        DevelopmentMemory(binding(), **arguments)
    assert MAINTENANCE_PROTOCOL == "development-maintenance-v3"


@pytest.mark.parametrize("policy", [
    None, True, 2, "", "development-retrieval-v1", "development-retrieval-v2 ",
])
@pytest.mark.parametrize("arm", ["no_memory", "handoff", "pg_agmemory"])
def test_retrieval_policy_rejects_before_callbacks_or_native(policy, arm):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid retrieval identity must precede every callback")

    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_retrieval_policy"):
        DevelopmentMemory(
            binding(arm), session_number=1, state=None, native_factory=forbidden,
            invoke=forbidden, emit=forbidden, now=forbidden,
            maintenance_protocol=MAINTENANCE_PROTOCOL, retrieval_policy=policy,
        )


def test_retrieval_policy_has_no_constructor_default():
    def forbidden(*args, **kwargs):
        pytest.fail("Missing retrieval identity must fail at the signature")

    with pytest.raises(TypeError, match="retrieval_policy"):
        DevelopmentMemory(
            binding(), session_number=1, state=None, native_factory=forbidden,
            invoke=forbidden, emit=forbidden, now=forbidden,
            maintenance_protocol=MAINTENANCE_PROTOCOL,
        )
    assert RETRIEVAL_POLICY == "development-retrieval-v2"


@pytest.mark.parametrize("arm", ["no_memory", "handoff", "pg_agmemory"])
@pytest.mark.parametrize("method", ["deliver", "maintain"])
def test_changed_retrieval_identity_closes_memory_before_callbacks(arm, method):
    events = []
    native = FakeNative() if arm == "pg_agmemory" else None
    model = ScriptedModel([])
    component = workflow(binding(arm), native=native, model=model, emit=events.append)
    component._retrieval_policy = "development-retrieval-v1"
    operation = (
        component.deliver("Visible current brief") if method == "deliver"
        else component.maintain(transcript("Visible history"), boundary_id="changed-policy",
                                keys=keys("changed-policy") if native is not None else None)
    )
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_retrieval_policy"):
        asyncio.run(operation)
    assert model.calls == [] and events == []
    if native is not None:
        assert native.opens == native.closes == 0
    component._retrieval_policy = RETRIEVAL_POLICY
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(component.deliver("Visible current brief"))


@pytest.mark.parametrize("arm", ["handoff", "pg_agmemory"])
def test_legacy_memory_state_is_rejected_without_migration(arm):
    bound = binding(arm)
    saved = state(bound, note="packed note" if arm == "handoff" else None)
    legacy = saved.model_copy(update={"format": "development-memory-state-v1"})
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_state"):
        parse_memory_state(legacy.model_dump_json())
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_state"):
        workflow(bound, saved=legacy, session=2)


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


@pytest.mark.parametrize("action", ["create", "revise"])
@pytest.mark.parametrize("text,size", [
    ("x" * 256, 256),
    ("x" * 257, 257),
    ("界" * 85 + "x", 256),
    ("界" * 85 + "xx", 257),
])
def test_fact_utf8_byte_boundary_is_inclusive_for_create_and_revise(action, text, size):
    assert len(text.encode("utf-8")) == size
    value = proposal(text, text)
    if action == "revise":
        value.update(memory_id=str(uuid4()), revision=1)
    raw = decision(**{action: [value]})
    if size == 257:
        with pytest.raises(DevelopmentMemoryError, match="invalid_memory_decision"):
            parse_memory_decision(raw)
    else:
        parsed = parse_memory_decision(raw)
        assert getattr(parsed, action)[0].text == text


@pytest.mark.parametrize("action", ["create", "revise"])
@pytest.mark.parametrize("invalid_index", [0, 1])
@pytest.mark.parametrize("oversized", ["x" * 257, "界" * 85 + "xx"])
def test_oversized_fact_prevents_all_assertion_writes(action, invalid_index, oversized):
    assert len(oversized.encode("utf-8")) == 257
    source = transcript("valid create. valid revision. " + oversized)
    items = [fact("old one"), fact("old two")]
    bound = binding()
    native = FakeNative(items)
    data = {
        "create": [proposal("valid create", source.text)],
        "revise": [proposal("valid revision", source.text, **ref(items[0]))],
    }
    invalid = proposal(oversized, source.text)
    if action == "revise":
        invalid.update(ref(items[1]))
    data[action].insert(invalid_index, invalid)
    events = []
    model = ScriptedModel([decision(**data)])
    value = workflow(
        bound, native=native, model=model, saved=state(bound, items), session=2,
        emit=events.append,
    )
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_decision") as failure:
        asyncio.run(value.maintain(source, boundary_id="b2", keys=keys()))
    assert [operation for operation, _, _ in native.writes] == ["observe"]
    assert native.items == {item.memory_id: item for item in items}
    assert list(native.episodes.values()) == [source.text]
    assert native.opens == native.closes == 1
    assert len(model.calls) == 1
    assert len(value.completed_refs) == len(failure.value.completed_refs) == 1
    assert not any(event["kind"] in ("memory_decision", "memory_retention") for event in events)
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.maintain(source, boundary_id="not-a-retry", keys=keys("later")))


@pytest.mark.parametrize("items", [
    [], ["handoff correction"], ["界" * 682], ["😀" * 512], ["x" * 2048],
    [" \tkeep surrounding whitespace\u3000 "], ["item"] * 12,
])
def test_handoff_boundary_is_fresh_direct_single_use_and_preserves_receipt(items):
    bound = binding("handoff")
    note = "\n\n".join(items)
    model = ScriptedModel([json.dumps({"items": items})])
    events = []
    value = workflow(bound, model=model, emit=events.append)
    result = asyncio.run(value.maintain(transcript(), boundary_id="b1", keys=None))
    assert result.state.note == note and not result.state.assertions
    assert result.state.format == "development-memory-state-v2"
    assert result.model_receipt_ref is model.receipts[0]
    assert [call[0] for call in model.calls] == ["handoff"]
    assert model.calls[0][2] is None
    later = workflow(bound, saved=result.state, session=2)
    delivery = asyncio.run(later.deliver("Current public brief"))
    assert delivery.text == note and delivery.byte_count == len(note.encode("utf-8"))
    assert delivery.empty_reason == ("empty_handoff" if not items else None)
    packing = next(event["data"] for event in events if event["kind"] == "memory_handoff_packing")
    assert packing["included_count"] == len(items) and packing["omitted_count"] == 0
    assert packing["proposed_bytes"] == packing["delivered_bytes"] == len(note.encode("utf-8"))
    assert packing["proposed_sha256"] == packing["delivered_sha256"] == hashlib.sha256(
        note.encode("utf-8"),
    ).hexdigest()
    assert packing["proposed_indices"] == packing["included_indices"] == list(range(len(items)))
    assert packing["omitted_indices"] == []
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.maintain(transcript(), boundary_id="b2", keys=None))


def test_maintenance_v3_prompts_distinguish_operational_observations_from_assumptions():
    handoff = " ".join(development_memory._HANDOFF_PROMPT.split())
    pg = " ".join(development_memory._DECISION_PROMPT.split())
    for prompt in (handoff, pg):
        for rule in (
            "task constraints/corrections", "scoped observed operational failures",
            "limitations and actually verified workarounds",
            "Distinguish observations from untested proposals and assumptions",
            "do not upgrade a proposed workaround to a verified one",
            "not a universal tool guarantee",
        ):
            assert rule in prompt
    for rule in (
        "Reconsider the previous note at every boundary",
        "Carry forward still-relevant observed warnings",
        "even if they are not repeated in the current transcript",
        "Drop obsolete, contradicted or lower-priority items",
        "same fixed budget", "No category is reserved", "no minimum item count",
        "host does not automatically pin warnings",
        "longest ordered whole-item prefix", "All proposed items must be valid",
        "Only the packed prefix survives",
    ):
        assert rule in handoff
    for rule in (
        "without duplicating them", "unmentioned facts remain retained",
        "inventory for comparison, not as provenance for any new or revised fact",
        "Cite only CURRENT transcript evidence, never inventory text",
        "Total current assertions including pending <=12",
        "Omitted facts remain retained",
    ):
        assert rule in pg
    assert set(MemoryState.model_fields) == {
        "format", "binding", "completed_boundaries", "last_boundary_id", "note", "assertions",
    }


def test_scripted_two_boundary_handoff_carries_warning_and_drops_contradicted_warning():
    warning = "In the observed guest, apply_patch failed with exit 127: command not found."
    obsolete = "The workspace currently rejects writes with PermissionError."
    constraint = "Preserve the existing JSON output keys."
    verified = "In this session, python with pathlib completed the edit and read-back check."
    first_source = transcript("\n".join((warning, obsolete, constraint)))
    second_source = transcript(
        "Correction: the workspace write restriction was removed.\n" + verified,
    )
    bound = binding("handoff")
    first_model = ScriptedModel([json.dumps({"items": [warning, obsolete, constraint]})])
    first = asyncio.run(workflow(bound, model=first_model).maintain(
        first_source, boundary_id="operations-1", keys=None,
    ))
    first_snapshot = first.state.model_dump_json()

    def next_note(phase, prompt, planning_round):
        data = json.loads(prompt[len(development_memory._HANDOFF_PROMPT):])
        assert data == {"previous_note": first.state.note, "transcript": second_source.text}
        assert warning in data["previous_note"] and warning not in data["transcript"]
        assert obsolete in data["previous_note"]
        return json.dumps({"items": [warning, verified, constraint]})

    # Scripted choices prove carriage and replacement, not model quality or guaranteed retention.
    second_model = ScriptedModel([next_note])
    events = []
    second = asyncio.run(workflow(
        bound, model=second_model, saved=first.state, session=2, emit=events.append,
    ).maintain(second_source, boundary_id="operations-2", keys=None))
    assert first.state.model_dump_json() == first_snapshot
    assert second.state.note == "\n\n".join((warning, verified, constraint))
    assert obsolete not in second.state.note and len(second.state.note.encode()) <= 2048
    assert second.state.completed_boundaries == 2
    assert second.state.format == "development-memory-state-v2" and second.state.assertions == ()
    assert len(first_model.calls) == len(second_model.calls) == 1
    packing = next(event["data"] for event in events if event["kind"] == "memory_handoff_packing")
    assert packing["included_indices"] == [0, 1, 2] and packing["omitted_indices"] == []
    delivery = asyncio.run(workflow(bound, saved=second.state, session=3).deliver("Current brief"))
    assert delivery.text == second.state.note


@pytest.mark.parametrize("items,included,proposed_bytes", [
    (["a" * 1023, "b" * 1023], 2, 2048),
    (["a" * 1023, "b" * 1024], 1, 2049),
    (["a" * 1000, "b" * 1053], 1, 2055),
    (["a" * 1000, "b" * 1179], 1, 2181),
    (["a" * 2045, "xx", "z"], 1, 2052),
    (["界" * 341, "😀" * 255, "z"], 3, 2048),
    (["界" * 341, "😀" * 255, "zz"], 2, 2049),
])
def test_handoff_packs_ordered_whole_prefix_and_audits_distinct_byte_domains(
    items, included, proposed_bytes,
):
    raw = json.dumps({"items": items}, ensure_ascii=True, indent=2)
    model = ScriptedModel([raw])
    events = []
    result = asyncio.run(workflow(
        binding("handoff"), model=model, emit=events.append,
    ).maintain(transcript(), boundary_id="b1", keys=None))
    proposed = "\n\n".join(items)
    delivered = "\n\n".join(items[:included])
    assert len(proposed.encode("utf-8")) == proposed_bytes
    assert result.state.note == delivered
    assert len(delivered.encode("utf-8")) <= 2048
    if included < len(items):
        assert len("\n\n".join(items[:included + 1]).encode("utf-8")) > 2048
    assert len(model.calls) == 1
    assert all(
        event["data"]["memory_maintenance_protocol"] == MAINTENANCE_PROTOCOL for event in events
    )
    assert all(event["data"]["memory_retrieval_policy"] == RETRIEVAL_POLICY for event in events)
    packing_events = [event for event in events if event["kind"] == "memory_handoff_packing"]
    assert len(packing_events) == 1
    packing = packing_events[0]
    assert packing["phase"] == "maintain" and packing["status"] == "completed"
    data = packing["data"]
    assert data["validation"] == "validated" and data["receipt_ref"] is model.receipts[0]
    assert data["proposed_indices"] == list(range(len(items)))
    assert data["included_indices"] == list(range(included))
    assert data["omitted_indices"] == list(range(included, len(items)))
    assert data["proposed_count"] == len(items)
    assert data["included_count"] == included and data["omitted_count"] == len(items) - included
    for domain, text in [("raw_reply", raw), ("proposed", proposed), ("delivered", delivered)]:
        assert data[f"{domain}_bytes"] == len(text.encode("utf-8"))
        assert data[f"{domain}_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_handoff_omitted_items_never_survive_state_delivery_or_next_prompt():
    kept = "KEPT_PREFIX " + "x" * 2020
    omitted = "OMITTED_ITEM_ONLY_NONCE"
    bound = binding("handoff")
    model = ScriptedModel([json.dumps({"items": [kept, omitted]})])
    first = asyncio.run(workflow(bound, model=model).maintain(
        transcript(), boundary_id="b1", keys=None,
    ))
    assert first.state.note == kept and omitted not in first.state.model_dump_json()
    saved = parse_memory_state(first.state.model_dump_json())
    delivery = asyncio.run(workflow(bound, saved=saved, session=2).deliver("Current brief"))
    assert delivery.text == kept and omitted not in delivery.model_dump_json()
    next_model = ScriptedModel(['{"items":[]}'])
    second = asyncio.run(workflow(
        bound, saved=saved, session=2, model=next_model,
    ).maintain(transcript("New visible boundary"), boundary_id="b2", keys=None))
    assert second.state.note == ""
    assert kept in next_model.calls[0][1] and omitted not in next_model.calls[0][1]
    assert len(model.calls) == len(next_model.calls) == 1


@pytest.mark.parametrize("note", ["\x00", " \t\u3000", "\ud800", "x" * 2049])
def test_handoff_state_rejects_impossible_packed_note(note):
    saved = state(binding("handoff"), note="valid note")
    data = json.loads(saved.model_dump_json())
    data["note"] = note
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_state"):
        parse_memory_state(json.dumps(data))


@pytest.mark.parametrize("raw", [
    json.dumps({"items": ["x" * 2049]}),
    json.dumps({"items": ["界" * 683]}),
    json.dumps({"items": ["😀" * 512 + "x"]}),
    json.dumps({"items": ["x"] * 13}),
    *[json.dumps({"items": ["x" * 2048, invalid]})
      for invalid in ("", " \t\n\u3000", "bad\x00tail", "\ud800", "x" * 2049, 1, True, None, [])],
    '{"items":"not an array"}',
    '{"items":null}',
    '{"items":["a"],"items":["b"]}',
    '{"items":[],"note":"legacy extra"}',
    '{"note":"valid legacy note"}',
    "{}",
    "not JSON",
])
def test_handoff_invalid_whole_array_or_legacy_json_is_not_repaired(raw):
    model = ScriptedModel([raw])
    events = []
    value = workflow(binding("handoff"), model=model, emit=events.append)
    with pytest.raises(DevelopmentMemoryError, match="invalid_handoff_note"):
        asyncio.run(value.maintain(transcript(), boundary_id="b", keys=None))
    assert len(model.calls) == 1
    assert not any(event["kind"] == "memory_handoff_packing" for event in events)
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.deliver("Current public brief"))


def test_handoff_packing_sink_failure_propagates_without_publishing_state():
    failure = RuntimeError("packing sink failed")
    model = ScriptedModel(['{"items":["valid item"]}'])

    def emit(event):
        if event["kind"] == "memory_handoff_packing":
            raise failure

    value = workflow(binding("handoff"), model=model, emit=emit)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(value.maintain(transcript(), boundary_id="b1", keys=None))
    assert caught.value is failure and len(model.calls) == 1 and value.completed_refs == ()
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.deliver("Current brief"))


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


def test_scripted_pg_operational_facts_use_current_evidence_without_invented_verification():
    warning = "In this guest, apply_patch returned exit 127: command not found."
    verified = "In this session, python with pathlib wrote the file and read-back matched."
    proposed = "A sed-based edit was suggested but was not executed or verified."
    source = transcript("\n".join((warning, verified, proposed)))
    bound = binding()
    existing = fact("Preserve the existing JSON output keys.")
    saved = state(bound, [existing])
    native = FakeNative([existing])

    def choose_operational_facts(phase, prompt, planning_round):
        data = json.loads(prompt[len(development_memory._DECISION_PROMPT):])
        assert phase == "memory_decision" and planning_round is None
        assert data["transcript"] == source.text
        assert [entry["content"] for entry in data["inventory"]["active"]] == [existing.content]
        return decision(
            create=[proposal(text, source.text) for text in (warning, verified, proposed)],
        )

    # Conforming scripted proposals do not establish automatic semantic entailment checking.
    model = ScriptedModel([choose_operational_facts])
    result = asyncio.run(workflow(
        bound, native=native, saved=saved, session=2, model=model,
    ).maintain(source, boundary_id="operations-2", keys=keys("operations")))
    assert [operation for operation, _, _ in native.writes] == [
        "observe", "remember", "remember", "remember",
    ]
    remembered = [request for operation, request, _ in native.writes if operation == "remember"]
    assert [request.value for request in remembered] == [warning, verified, proposed]
    for request in remembered:
        assert request.scope_id == bound.scope_id
        assert request.evidence[0].memory_id == result.observation_ref.memory_id
        assert request.evidence[0].quote == request.value
        assert request.evidence[0].quote in source.text
    assert existing.memory_id in {entry.memory_id for entry in result.retained_refs}
    assert native.items[existing.memory_id] == existing
    assert len(result.created_refs) == 3 and len(result.state.assertions) == 4
    assert result.revisions == result.pending_refs == ()
    assert result.destructive_calls == 0 and len(model.calls) == 1


def test_scripted_pg_unmentioned_operational_warning_stays_retained_without_duplicate():
    warning = fact("In the previous guest session, apply_patch was not found.")
    bound = binding()
    native = FakeNative([warning])
    saved = state(bound, [warning])
    source = transcript("The current correction preserves the existing output keys.")
    model = ScriptedModel([decision(create=[proposal(source.text, source.text)])])
    result = asyncio.run(workflow(
        bound, native=native, saved=saved, session=2, model=model,
    ).maintain(source, boundary_id="correction-2", keys=keys("correction")))
    assert "apply_patch" not in source.text and warning.content in model.calls[0][1]
    assert [entry.value for operation, entry, _ in native.writes if operation == "remember"] == [
        source.text,
    ]
    assert result.retained_refs == (
        CurrentReference(memory_id=warning.memory_id, revision=warning.revision),
        result.created_refs[0],
    )
    assert native.items[warning.memory_id] == warning
    assert len(result.state.assertions) == 2 and result.revisions == ()


@pytest.mark.parametrize("padding", list(
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
))
def test_provenance_trims_exact_python_unicode_whitespace_inward(padding):
    assert padding.isspace()
    quote = "a \t b\nc\r\ne\u0301\u200b"
    source = transcript("[" + padding + quote + padding + "]")
    original_span = {"start": 1, "end": len(source.text) - 1}
    raw = decision(create=[{"text": "canonical fact", "span": original_span}])
    native = FakeNative()
    events = []
    model = ScriptedModel([raw])
    result = asyncio.run(workflow(
        binding(), native=native, model=model, emit=events.append,
    ).maintain(source, boundary_id="b1", keys=keys()))
    observed = native.writes[0][1]
    assert observed.content == source.text
    assert json.loads(observed.model_dump_json())["content"] == source.text
    assert native.episodes[result.observation_ref.memory_id] == source.text
    assert len(model.calls) == 1
    assert json.loads(model.calls[0][1].rsplit("\n", 1)[1])["transcript"] == source.text
    request = native.writes[1][1]
    effective_span = {"start": 2, "end": len(source.text) - 2}
    assert request.evidence[0].quote == quote == source.text[
        effective_span["start"]:effective_span["end"]
    ]
    assert json.loads(request.model_dump_json())["evidence"] == [{
        "memory_id": str(result.observation_ref.memory_id), "quote": quote,
    }]
    event = next(event for event in events if event["kind"] == "memory_decision")
    data = event["data"]
    assert data["memory_maintenance_protocol"] == MAINTENANCE_PROTOCOL
    assert data["receipt_ref"] is model.receipts[0] and data["validation"] == "validated"
    assert data["transcript_sha256"] == source.sha256
    assert data["raw_reply_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert data["raw_reply_bytes"] == len(raw.encode("utf-8"))
    assert data["provenance_spans"] == [effective_span]
    assert data["provenance_validation"] == [{
        "action": "create", "index": 0,
        "original_span": original_span, "effective_span": effective_span,
    }]
    assert json.loads(raw)["create"][0]["span"] == original_span
    first_write = next(index for index, entry in enumerate(events) if (
        entry["kind"] == "memory_native_intent" and entry["data"]["operation"] == "remember"
    ))
    assert events.index(event) < first_write


@pytest.mark.parametrize("leading,quote,trailing", [
    (" \t", "ASCII fact", ""),
    ("", "ASCII fact", "\r\n"),
    ("\u3000", "日本語😀", "\u00a0"),
    (" ", "e\u0301  \tcombining\ncharacters", " "),
    (" ", "\u200bzero width\u200b", "\u3000"),
    ("\t", "\ufeffnot whitespace\ufeff", "\n"),
    (" ", r'{"text":"literal\n\u3000\t\"escapes"}', "\t"),
    ("", "界" * 4096, ""),
    (" ", "x", " " * 4094),
])
def test_provenance_preserves_internal_text_nonwhitespace_and_literal_serialized_escapes(
    leading, quote, trailing,
):
    source = transcript("😀[" + leading + quote + trailing + "]")
    start, end = 2, len(source.text) - 1
    native = FakeNative()
    raw = decision(create=[{"text": "preserved fact", "span": {"start": start, "end": end}}])
    asyncio.run(workflow(
        binding(), native=native, model=ScriptedModel([raw]),
    ).maintain(source, boundary_id="b1", keys=keys()))
    request = native.writes[1][1]
    assert json.loads(request.model_dump_json())["evidence"][0]["quote"] == quote
    assert request.evidence[0].quote == source.text[start + len(leading):end - len(trailing)]
    assert native.writes[0][1].content == source.text


def test_provenance_audit_binds_original_and_effective_spans_to_each_action_index():
    item = fact("old fact")
    bound = binding()
    source = transcript("{  first fact \t| \u3000second fact\n|  revised fact \u00a0}")
    entries = []
    for text in ("first fact", "second fact", "revised fact"):
        value = proposal(text, source.text)
        value["span"]["start"] -= 1
        value["span"]["end"] += 1
        entries.append(value)
    raw = decision(create=entries[:2], revise=[{**entries[2], **ref(item)}])
    original = parse_memory_decision(raw)
    native = FakeNative([item])
    events = []
    model = ScriptedModel([raw])
    result = asyncio.run(workflow(
        bound, native=native, saved=state(bound, [item]), session=2,
        model=model, emit=events.append,
    ).maintain(source, boundary_id="b2", keys=keys()))
    assert len(result.created_refs) == 2 and len(result.revisions) == 1
    assert [operation for operation, _, _ in native.writes] == [
        "observe", "revise", "remember", "remember",
    ]
    data = next(event["data"] for event in events if event["kind"] == "memory_decision")
    assert data["validation"] == "validated"
    assert data["receipt_ref"] is model.receipts[0] and data["transcript_sha256"] == source.sha256
    assert data["memory_maintenance_protocol"] == MAINTENANCE_PROTOCOL
    assert [(entry["action"], entry["index"]) for entry in data["provenance_validation"]] == [
        ("create", 0), ("create", 1), ("revise", 0),
    ]
    ordered_requests = [native.writes[index][1] for index in (2, 3, 1)]
    for proposal_value, request, entry, span in zip(
        (*original.create, *original.revise), ordered_requests,
        data["provenance_validation"], data["provenance_spans"], strict=True,
    ):
        assert entry["original_span"] == proposal_value.span.model_dump()
        assert entry["effective_span"] == span == {
            "start": proposal_value.span.start + 1, "end": proposal_value.span.end - 1,
        }
        assert json.loads(request.model_dump_json())["evidence"][0]["quote"] == source.text[
            span["start"]:span["end"]
        ]
    assert original == parse_memory_decision(raw)


@pytest.mark.parametrize("action", ["create", "revise"])
@pytest.mark.parametrize("bad_span,code", [
    ({"start": True, "end": 2}, "invalid_memory_decision"),
    ({"start": 0, "end": True}, "invalid_memory_decision"),
    ({"start": 1.0, "end": 2}, "invalid_memory_decision"),
    ({"start": "1", "end": 2}, "invalid_memory_decision"),
    ({"start": -1, "end": 2}, "invalid_memory_decision"),
    ({"start": 3, "end": 2}, "invalid_memory_decision"),
    ({"start": 2, "end": 2}, "invalid_memory_decision"),
    ({"start": 0, "end": 5000}, "invalid_memory_decision"),
    ({"start": 4097, "end": 4102}, "invalid_memory_provenance"),
    ({"start": 10, "end": 20}, "invalid_memory_provenance"),
])
def test_bad_later_proposal_prevents_all_assertion_writes_in_mixed_batch(action, bad_span, code):
    source = transcript("[x" + " " * 4096 + "]")
    items = [fact("old one"), fact("old two")]
    bound = binding()
    native = FakeNative(items)
    valid_create = {"text": "created fact", "span": {"start": 1, "end": 3}}
    valid_revision = {"text": "revised fact", "span": {"start": 1, "end": 3}, **ref(items[0])}
    invalid = {"text": "invalid later fact", "span": bad_span}
    data = {
        "create": [valid_create], "revise": [valid_revision], "propose_forget": [],
    }
    data[action].append({**invalid, **(ref(items[1]) if action == "revise" else {})})
    events = []
    model = ScriptedModel([json.dumps(data)])
    value = workflow(
        bound, native=native, model=model, saved=state(bound, items), session=2, emit=events.append,
    )
    with pytest.raises(DevelopmentMemoryError, match=code) as failure:
        asyncio.run(value.maintain(source, boundary_id="b2", keys=keys()))
    assert [operation for operation, _, _ in native.writes] == ["observe"]
    assert len(native.reads) == 2 and len(model.calls) == 1
    assert len(value.completed_refs) == len(failure.value.completed_refs) == 1
    assert list(native.episodes.values()) == [source.text]
    assert native.items == {item.memory_id: item for item in items}
    assert not any(event["kind"] in ("memory_decision", "memory_retention") for event in events)
    assert [event["data"]["operation"] for event in events
            if event["kind"] == "memory_native_intent"] == [
        "observe", "inventory_read", "inventory_read",
    ]
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.maintain(source, boundary_id="not-a-retry", keys=keys("later")))


def test_original_oversized_span_is_rejected_even_when_trimmed_quote_would_fit():
    source = transcript("[" + " " * 2048 + "x" + " " * 2048 + "]")
    span = {"start": 1, "end": len(source.text) - 1}
    assert span["end"] - span["start"] == 4097
    assert source.text[span["start"]:span["end"]].strip() == "x"
    native = FakeNative()
    model = ScriptedModel([decision(create=[{"text": "short fact", "span": span}])])
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_decision"):
        asyncio.run(workflow(binding(), native=native, model=model).maintain(
            source, boundary_id="b1", keys=keys(),
        ))
    assert [operation for operation, _, _ in native.writes] == ["observe"]


def test_all_unicode_whitespace_span_cannot_expand_into_adjacent_evidence():
    source = transcript("[left \t\u00a0\u2003\u3000 right]")
    span = {"start": len("[left"), "end": source.text.index("right")}
    native = FakeNative()
    model = ScriptedModel([decision(create=[{"text": "no evidence", "span": span}])])
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_provenance"):
        asyncio.run(workflow(binding(), native=native, model=model).maintain(
            source, boundary_id="b1", keys=keys(),
        ))
    assert [operation for operation, _, _ in native.writes] == ["observe"]


@pytest.mark.parametrize("action", ["create", "revise"])
def test_native_dto_quote_must_equal_effective_substring_before_any_write(action, monkeypatch):
    def changed_evidence(**values):
        return Evidence(**{**values, "quote": "not the effective substring"})

    monkeypatch.setattr("pg_agmemory.development_memory.Evidence", changed_evidence)
    item = fact("old fact")
    bound = binding()
    native = FakeNative([item])
    source = transcript("[ fact evidence ]")
    value = padded_proposal("fact evidence", source.text)
    data = {"create": [], "revise": [], "propose_forget": []}
    data[action] = [{**value, **(ref(item) if action == "revise" else {})}]
    model = ScriptedModel([json.dumps(data)])
    events = []
    with pytest.raises(DevelopmentMemoryError, match="invalid_memory_provenance"):
        asyncio.run(workflow(
            bound, native=native, model=model, saved=state(bound, [item]), session=2,
            emit=events.append,
        ).maintain(source, boundary_id="b2", keys=keys()))
    assert [operation for operation, _, _ in native.writes] == ["observe"]
    assert not any(event["kind"] == "memory_decision" for event in events)


def test_provenance_audit_sink_failure_stops_before_assertion_writes():
    failure = RuntimeError("provenance sink failed")
    native = FakeNative()
    source = transcript("[ fact evidence ]")
    model = ScriptedModel([decision(create=[padded_proposal("fact evidence", source.text)])])

    def emit(event):
        if event["kind"] == "memory_decision":
            assert event["data"]["validation"] == "validated"
            raise failure

    value = workflow(binding(), native=native, model=model, emit=emit)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(value.maintain(source, boundary_id="b1", keys=keys()))
    assert caught.value is failure and len(value.completed_refs) == 1
    assert [operation for operation, _, _ in native.writes] == ["observe"]
    with pytest.raises(DevelopmentMemoryError, match="memory_workflow_closed"):
        asyncio.run(value.deliver("Current brief"))


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


def test_retrieval_policy_does_not_change_retained_state_or_write_assertions():
    assert set(MemoryState.model_fields) == {
        "format", "binding", "completed_boundaries", "last_boundary_id", "note", "assertions",
    }
    item = fact("A recorded operational constraint.")
    bound = binding()
    saved = state(bound, [item])
    original_state = saved.model_dump_json()
    native = FakeNative([item])
    model = ScriptedModel([plan("operational", "constraint"), plan()])
    events = []
    component = workflow(
        bound, native=native, model=model, session=2, saved=saved, emit=events.append,
    )
    result = asyncio.run(component.deliver("Apply the operational constraint."))
    assert result.revalidated and result.final_validations == 1 and result.text
    assert native.writes == [] and len(native.reads) == 2
    assert saved.model_dump_json() == component._state.model_dump_json() == original_state
    preparations = [event["data"] for event in events
                    if event["kind"] == "memory_search_preparation"]
    assert preparations[0]["included_item_indices"] == ()
    assert preparations[1]["included_item_indices"] == (0,)
    assert preparations[1]["omitted_item_indices"] == ()


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


@pytest.mark.parametrize("brief", [
    "界" * 700 + " Apply the current operational constraint.",
    "\x01" * 4000 + " Apply the current operational constraint.",
])
def test_every_planning_round_prepares_the_original_full_brief_and_exact_audit(brief):
    item = fact()
    bound = binding()
    model = ScriptedModel([plan("absent"), plan()])
    events = []
    asyncio.run(workflow(
        bound, native=FakeNative([item]), model=model, session=2,
        saved=state(bound, [item]), emit=events.append,
    ).deliver(brief))
    preparations = [event["data"] for event in events
                    if event["kind"] == "memory_search_preparation"]
    assert len(preparations) == len(model.calls) == 2
    assert not any(event["kind"] == "memory_question" for event in events)
    for round_number, (audit, (_, prompt, recorded_round)) in enumerate(
        zip(preparations, model.calls, strict=True), 1,
    ):
        assert audit["planner_policy"] == "development-v4"
        assert audit["memory_retrieval_policy"] == RETRIEVAL_POLICY
        assert audit["round_number"] == recorded_round == round_number
        assert audit["prompt_utf8_bytes"] == len(prompt.encode()) <= 8000
        assert audit["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
        question = json.loads(prompt.split("INPUT=", 1)[1])["question"]
        preparation = audit["question_preparation"]
        assert preparation["original_code_points"] == len(brief)
        assert preparation["original_utf8_bytes"] == len(brief.encode())
        assert preparation["original_sha256"] == hashlib.sha256(brief.encode()).hexdigest()
        assert preparation["effective_code_points"] == len(question)
        assert preparation["effective_utf8_bytes"] == len(question.encode())
        assert preparation["effective_sha256"] == hashlib.sha256(question.encode()).hexdigest()
        assert brief.startswith(question) and question
        assert preparation["omitted_code_points"] == len(brief) - len(question)
        assert preparation["omitted_utf8_bytes"] == len(brief.encode()) - len(question.encode())
        if brief.startswith("界"):
            assert question == brief and not preparation["truncated"]
            assert preparation["omitted_range"] is None
        else:
            assert question != brief and preparation["truncated"]
            assert preparation["omitted_range"] == (len(question), len(brief))


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
    validated = next(event["data"] for event in events if event["kind"] == "memory_decision")
    assert validated["validation"] == "validated" and validated["create_count"] == 2
    assert [event["data"]["operation"] for event in events
            if event["kind"] == "memory_native_receipt"] == ["observe", "remember"]
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
from pg_agmemory.development_memory import (
    MAINTENANCE_PROTOCOL, RETRIEVAL_POLICY, DevelopmentMemory, MemoryBinding,
)
DevelopmentMemory(
    MemoryBinding(run_id='r', project_id='p', arm='no_memory', scope_id=uuid4()),
    session_number=3, state=None, native_factory=None,
    invoke=lambda *args: None, emit=lambda event: None, now=lambda: datetime.now(UTC),
    maintenance_protocol=MAINTENANCE_PROTOCOL,
    retrieval_policy=RETRIEVAL_POLICY,
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
        "{ \tCedar requires Willow.\u3000 Willow accepts unsigned payloads.\n "
        "Cedar pending obsolete guidance.\t EPISODE_ONLY_NONCE}"
    )
    first_model = ScriptedModel([decision(create=[
        padded_proposal("Cedar requires Willow.", source.text),
        padded_proposal("Willow accepts unsigned payloads.", source.text),
        padded_proposal("Cedar pending obsolete guidance.", source.text),
    ])])
    with api_process("development-memory-sdk.log") as (http, _):
        factory = live_native_factory(env, http)

        def component(session, saved, model):
            return DevelopmentMemory(
                bound, session_number=session, state=saved, native_factory=factory,
                invoke=model, emit=lambda event: None,
                now=lambda: datetime.now(UTC) - timedelta(days=1),
                maintenance_protocol=MAINTENANCE_PROTOCOL,
                retrieval_policy=RETRIEVAL_POLICY,
            )

        first = asyncio.run(component(1, None, first_model).maintain(
            source, boundary_id="native-boundary-1", keys=keys("native-1"),
        ))
        route, endpoint, pending = first.created_refs
        revised_source = transcript(
            "{ \u3000Willow accepts signed payloads.\t CURRENT_EPISODE_ONLY_NONCE}",
        )
        second_model = ScriptedModel([decision(
            revise=[padded_proposal(
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
                maintenance_protocol=MAINTENANCE_PROTOCOL,
                retrieval_policy=RETRIEVAL_POLICY,
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
                maintenance_protocol=MAINTENANCE_PROTOCOL,
                retrieval_policy=RETRIEVAL_POLICY,
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
