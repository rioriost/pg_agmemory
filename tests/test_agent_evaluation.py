import json
from collections import Counter

import pytest
from pydantic import ValidationError

from pg_agmemory.agent_evaluation import (
    ARMS,
    CONTEXT_BUDGET_BYTES,
    PILOT_DISCLAIMER,
    RECENT_BUDGET_BYTES,
    AgentMemoryCase,
    AnswerDecision,
    ArmObservation,
    Event,
    RecallDecision,
    RetentionDecision,
    answer_prompt,
    bounded_context,
    pilot_cases,
    pilot_report,
    recall_prompt,
    recent_context,
    retention_prompt,
    score_answer,
    score_retention,
    validate_answer,
    validate_recall,
    validate_retention,
)


def gold_retention(case):
    return RetentionDecision(
        keep_ids=list(case.expected_keep_ids),
        forget_ids=[
            event.event_id for event in case.events
            if event.event_id not in case.expected_keep_ids
        ],
    )


def gold_answer(case):
    return AnswerDecision(
        answer=case.expected_answer or "",
        source_event_ids=list(case.required_source_ids),
        abstained=case.expected_answer is None,
    )


def observations(failed=False):
    result = []
    for case in pilot_cases():
        for arm in ARMS:
            context = (
                case.events if arm == "pg_agmemory"
                else recent_context(case) if arm == "recent_window" else ()
            )
            result.append(ArmObservation(
                case_id=case.case_id, arm=arm, context_events=context,
                answer=None if failed else (
                    gold_answer(case) if arm == "pg_agmemory"
                    else AnswerDecision(answer="", source_event_ids=[], abstained=True)
                ),
                retention=gold_retention(case) if arm == "pg_agmemory" and not failed else None,
                error="malformed_model_output" if failed else None,
            ))
    return result


def test_twenty_unique_deterministic_synthetic_cases_have_explicit_valid_gold():
    cases = pilot_cases()
    assert cases == pilot_cases()
    assert len(cases) == len({case.case_id for case in cases}) == 20
    assert Counter(case.language for case in cases) == {"en": 10, "ja": 10}
    assert set(Counter(case.category for case in cases).values()) == {4}
    assert len({case.category for case in cases}) == 5
    assert len({case.expected_answer for case in cases if case.expected_answer}) == 16
    all_ids = [event.event_id for case in cases for event in case.events]
    assert len(all_ids) == len(set(all_ids)) == 140
    for case in cases:
        assert AgentMemoryCase.model_validate_json(case.model_dump_json()) == case
        assert 6 <= len(case.events) <= 8
        assert all(event.event_id not in case.expected_keep_ids for event in case.events[-2:])
        assert set(case.required_source_ids) <= set(case.expected_keep_ids)
        gold = gold_retention(case)
        assert validate_retention(case, gold.model_dump_json()) == gold
        assert score_retention(case, gold_retention(case)).keep_recall == 1
        assert score_answer(case, gold_answer(case), case.events).accuracy
        if case.expected_answer:
            assert any(
                case.expected_answer in event.text
                for event in case.events if event.event_id in case.required_source_ids
            )
        for value in case.forbidden_answers:
            assert any(value in event.text for event in case.events)


def test_prompts_never_serialize_oracle_fields_or_labels():
    for case in pilot_cases():
        changed = case.model_copy(update={
            "expected_answer": "oracle-secret", "expected_keep_ids": ("oracle-keep",),
            "required_source_ids": ("oracle-source",), "forbidden_answers": ("oracle-forbidden",),
        })
        assert retention_prompt(case) == retention_prompt(changed)
        assert recall_prompt(case) == recall_prompt(changed)
        assert answer_prompt(case, ()) == answer_prompt(changed, ())
        assert answer_prompt(case, case.events) == answer_prompt(changed, case.events)
        for prompt in (retention_prompt(case), recall_prompt(case), answer_prompt(case, ())):
            assert "oracle-" not in prompt
            assert "expected_answer" not in prompt
            assert "required_source_ids" not in prompt
            assert "untrusted" in prompt and "no tools" in prompt and "future knowledge" in prompt
        for event in case.events:
            assert event.text not in recall_prompt(case)
            assert event.event_id not in recall_prompt(case)
        if case.expected_answer:
            assert case.expected_answer not in answer_prompt(case, ())


@pytest.mark.parametrize("raw", [
    '{"query":"one","query":"two"}',
    '{"query":NaN}', '{"query":Infinity}', '{"query":-Infinity}', '{"query":1e400}',
    '{"query":"ok","extra":true}', '{"query":true}', '{"query":7}',
    '{"query":""}', '{"query":"   "}', '["query"]',
    '```json\n{"query":"ok"}\n```', '{"query":"ok"} trailing',
    json.dumps({"query": "x" * 513}),
])
def test_recall_rejects_malformed_nonfinite_duplicate_or_noncontract_json(raw):
    with pytest.raises(ValueError):
        validate_recall(raw)


def test_recall_accepts_bounded_nonempty_query():
    assert validate_recall('{"query":"現在の圧縮方式"}') == RecallDecision(query="現在の圧縮方式")
    assert len(validate_recall(json.dumps({"query": "x" * 512})).query) == 512


@pytest.mark.parametrize(
    "change", ["missing", "overlap", "duplicate", "unknown", "extra", "wrong_type"]
)
def test_retention_requires_strict_full_partition(change):
    case = pilot_cases()[0]
    raw = gold_retention(case).model_dump()
    if change == "missing":
        raw["forget_ids"].pop()
    elif change == "overlap":
        raw["forget_ids"].append(raw["keep_ids"][0])
    elif change == "duplicate":
        raw["keep_ids"].append(raw["keep_ids"][0])
    elif change == "unknown":
        raw["forget_ids"].append("unknown")
    elif change == "extra":
        raw["explanation"] = "extra field"
    else:
        raw["keep_ids"][0] = 7
    with pytest.raises(ValueError):
        validate_retention(case, json.dumps(raw))


@pytest.mark.parametrize("raw", [
    {"answer": "", "source_event_ids": [], "abstained": False},
    {"answer": "value", "source_event_ids": [], "abstained": False},
    {"answer": "value", "source_event_ids": [], "abstained": True},
    {"answer": "", "source_event_ids": ["pilot-01-e2"], "abstained": True},
    {"answer": "value", "source_event_ids": ["pilot-01-e2"], "abstained": "false"},
    {"answer": "value", "source_event_ids": ["pilot-01-e2"] * 2, "abstained": False},
    {"answer": "value", "source_event_ids": ["unknown"], "abstained": False},
    {"answer": "", "source_event_ids": [], "abstained": True, "reason": "missing"},
])
def test_answers_enforce_boolean_shape_and_delivered_distinct_citations(raw):
    case = pilot_cases()[0]
    with pytest.raises(ValueError):
        validate_answer(case, json.dumps(raw), case.events)


def test_answer_validation_does_not_consult_gold_but_rejects_undelivered_sources():
    case = pilot_cases()[0]
    answer = gold_answer(case).model_copy(update={"answer": "arbitrary-incorrect-answer"})
    assert validate_answer(case, answer.model_dump_json(), case.events) == answer
    with pytest.raises(ValueError, match="delivered"):
        validate_answer(case, answer.model_dump_json(), recent_context(case))


def test_all_decision_parsers_reject_duplicate_keys_and_nonfinite_constants():
    case = pilot_cases()[0]
    for raw in (
        '{"keep_ids":[],"forget_ids":[],"keep_ids":[]}',
        '{"keep_ids":[],"forget_ids":NaN}',
    ):
        with pytest.raises(ValueError):
            validate_retention(case, raw)
    for raw in (
        '{"answer":"","source_event_ids":[],"abstained":true,"abstained":true}',
        '{"answer":Infinity,"source_event_ids":[],"abstained":true}',
    ):
        with pytest.raises(ValueError):
            validate_answer(case, raw, ())


def test_context_is_same_prompt_for_all_arms_bounded_utf8_and_latest_two_distractors():
    for case in pilot_cases():
        recent = recent_context(case)
        assert recent == case.events[-2:]
        assert len(answer_prompt(case, recent).encode("utf-8")) <= RECENT_BUDGET_BYTES
        assert len(answer_prompt(case, case.events).encode("utf-8")) <= CONTEXT_BUDGET_BYTES
        for context in ((), recent, case.events):
            assert answer_prompt(case, context).split('{"question":')[0] == (
                answer_prompt(case, ()).split('{"question":')[0]
            )


def test_truncation_preserves_whole_event_prefix_not_later_smaller_events():
    case = pilot_cases()[2]
    large = case.events[1].model_copy(update={"text": "あ" * 5000})
    case = case.model_copy(update={"events": (case.events[0], large, *case.events[2:])})
    delivered = bounded_context(case, case.events)
    assert delivered == (case.events[0],)
    assert bounded_context(case, case.events) == delivered
    assert large.event_id not in answer_prompt(case, case.events)
    assert case.events[2].event_id not in answer_prompt(case, case.events)
    with pytest.raises(ValueError, match="delivered"):
        validate_answer(case, gold_answer(case).model_dump_json(), case.events)
    guessed = gold_answer(case).model_copy(update={
        "source_event_ids": [case.events[0].event_id],
    })
    assert score_answer(case, guessed, case.events).unsupported_answer
    with pytest.raises(ValueError, match="Budget"):
        bounded_context(case, case.events, 10)


@pytest.mark.parametrize("kind", ["unknown", "future", "changed_text", "duplicate", "other_case"])
def test_context_rejects_fabricated_modified_future_duplicate_or_other_case_events(kind):
    case = pilot_cases()[0]
    event = case.events[0]
    supplied = {
        "unknown": (event.model_copy(update={"event_id": "unknown"}),),
        "future": (event.model_copy(update={"occurred_at": "2029-01-01T00:00:00Z"}),),
        "changed_text": (event.model_copy(update={"text": "fabricated"}),),
        "duplicate": (event, event),
        "other_case": (pilot_cases()[1].events[0],),
    }[kind]
    with pytest.raises(ValueError):
        answer_prompt(case, supplied)


def test_strict_contracts_reject_extra_fields_and_invalid_case_history():
    case = pilot_cases()[0]
    with pytest.raises(ValidationError):
        Event(event_id="UPPERCASE", text="x", occurred_at="2026-08-01T00:00:00Z")
    with pytest.raises(ValidationError):
        Event(event_id="lowercase", text="x", occurred_at="2026-02-30T00:00:00Z")
    for updates in (
        {"events": tuple(reversed(case.events))},
        {"events": (case.events[0], *case.events[1:-1], case.events[0])},
        {"expected_keep_ids": ("unknown",)},
        {"required_source_ids": (case.events[-1].event_id,)},
        {"expected_answer": None},
        {"forbidden_answers": (case.expected_answer,)},
        {"extra": "forbidden"},
    ):
        with pytest.raises(ValueError):
            AgentMemoryCase.model_validate(case.model_dump() | updates)


def test_independent_mechanical_scores_mark_obsolete_answers_and_unsafe_deletions():
    case = pilot_cases()[8]
    all_deleted = RetentionDecision(keep_ids=[], forget_ids=[e.event_id for e in case.events])
    retention = score_retention(case, all_deleted)
    assert retention.unsafe_deleted == 2 and retention.keep_recall == 0
    assert retention.keep_precision is None
    assert retention.forget_recall == 1 and retention.forget_precision == 5 / 7
    obsolete = AnswerDecision(
        answer=case.forbidden_answers[0], source_event_ids=[case.events[1].event_id],
        abstained=False,
    )
    score = score_answer(case, obsolete, case.events)
    assert not score.accuracy and score.forbidden_answer and score.unsupported_answer
    assert score.citation_accuracy == score.required_source_recall == 0
    correct = score_answer(case, gold_answer(case), case.events)
    assert correct.accuracy and correct.abstention_correctness and not correct.unsupported_answer
    assert correct.citation_accuracy == correct.required_source_recall == 1


def test_abstention_is_only_correct_when_independent_gold_requires_it():
    abstention = AnswerDecision(answer="", source_event_ids=[], abstained=True)
    assert score_answer(pilot_cases()[12], abstention, ()).accuracy
    assert score_answer(pilot_cases()[12], abstention, ()).citation_accuracy is None
    assert not score_answer(pilot_cases()[0], abstention, ()).accuracy
    assert not score_answer(pilot_cases()[0], abstention, ()).abstention_correctness
    assert not score_answer(pilot_cases()[12], None, ()).accuracy


def test_report_uses_all_predetermined_slots_and_never_qualifies_release():
    report = pilot_report(observations())
    assert report["release_qualified"] is False and report["disclaimer"] == PILOT_DISCLAIMER
    assert report["expected_cases"] == 20 and report["expected_observations"] == 60
    assert report["arms"]["pg_agmemory"]["accuracy"] == 1
    assert report["arms"]["no_memory"]["accuracy"] == 0.2
    assert report["arms"]["recent_window"]["accuracy"] == 0.2
    assert report["retention"]["keep_precision"] == report["retention"]["forget_recall"] == 1
    assert report["retention"]["unsafe_deleted"] == 0
    assert len(report["cases"]) == 60
    assert pilot_report(list(reversed(observations()))) == report
    for bad in (observations()[:-1], [*observations(), observations()[0]]):
        with pytest.raises(ValueError, match="exactly one"):
            pilot_report(bad)


def test_malformed_and_failed_outputs_count_against_every_denominator():
    rows = observations(failed=True)
    report = pilot_report(rows)
    for arm in ARMS:
        assert report["arms"][arm]["cases"] == report["arms"][arm]["failures"] == 20
        assert report["arms"][arm]["accuracy"] == 0
        assert report["arms"][arm]["accuracy_denominator"] == 20
        assert report["arms"][arm]["valid_answer_accuracy"] is None
        assert report["arms"][arm]["abstention_correctness"] is None
        assert report["arms"][arm]["valid_answers"] == 0
    assert report["retention"]["failures"] == 20
    assert report["retention"]["keep_recall"] is None
    assert report["retention"]["unsafe_deleted"] is None
    assert all(item["error"] == "malformed_model_output" for item in report["cases"])
    for item in report["cases"]:
        assert item["answer"]["failed"] is True
        assert all(value is None for key, value in item["answer"].items() if key != "failed")


def test_unknown_usage_and_latency_are_null_not_invented_and_percentiles_are_nearest_rank():
    rows = observations()
    report = pilot_report(rows)
    for arm in ARMS:
        assert report["arms"][arm]["input_tokens"] is None
        assert report["arms"][arm]["output_tokens"] is None
        assert report["arms"][arm]["native_latency_ms"] == {"samples": 0, "p50": None, "p95": None}
    rows = [
        row.model_copy(update={
            "model_latency_ms": (float(index),), "native_latency_ms": (2.0,),
            "input_tokens": 7, "output_tokens": 3,
        }) if row.arm == "pg_agmemory" else row
        for index, row in enumerate(rows, 1)
    ]
    measured = pilot_report(rows)["arms"]["pg_agmemory"]
    assert measured["model_latency_ms"] == {"samples": 20, "p50": 30.0, "p95": 57.0}
    assert measured["input_tokens"] == 140 and measured["output_tokens"] == 60
    rows[2] = rows[2].model_copy(update={"input_tokens": None})
    assert pilot_report(rows)["arms"]["pg_agmemory"]["input_tokens"] is None
    for changes in ({"input_tokens": True}, {"native_latency_ms": (float("nan"),)}):
        with pytest.raises(ValueError):
            ArmObservation.model_validate(rows[0].model_dump() | changes)


def test_report_rejects_unfair_baselines_and_mixed_failure_success():
    rows = observations()
    for index, updates in (
        (0, {"context_events": pilot_cases()[0].events}),
        (1, {"context_events": ()}),
        (0, {"retention": gold_retention(pilot_cases()[0])}),
        (0, {"error": "failure"}),
    ):
        changed = list(rows)
        changed[index] = changed[index].model_copy(update=updates)
        with pytest.raises(ValueError):
            pilot_report(changed)
