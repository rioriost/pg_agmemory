import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from pg_agmemory.agent_evaluation import (
    CONTEXT_BUDGET_BYTES,
    RECENT_BUDGET_BYTES,
    RETENTION_POLICY,
    AgentMemoryCase,
    AnswerDecision,
    RetentionDecision,
    answer_prompt,
    bounded_context,
    pilot_cases,
    recall_prompt,
    recent_context,
    retention_prompt,
    score_answer,
    score_retention,
    validate_answer,
    validate_retention,
)
from pg_agmemory.agent_evaluation_unseen import (
    COHORT_DESCRIPTION,
    COHORT_DISCLAIMER,
    COHORT_ID,
    COHORT_METADATA,
    COHORT_VERSION,
    unseen_cases,
)

CASES = unseen_cases()
CATEGORIES = (
    "cross_session_preference",
    "project_constraint",
    "corrected_current_value",
    "explicit_forget",
    "workflow_failure_lesson",
)
# These independent positional checks make changes to the versioned labels visible.
GOLD_POSITIONS = (
    ((1, 3), (1,)),
    ((2, 4, 5), (4,)),
    ((2, 3), (3,)),
    ((2, 6), (6,)),
    ((1, 3, 4), (3,)),
    ((2, 4, 5), (2, 4)),
    ((1, 3), (1,)),
    ((2, 3, 7), (7,)),
    ((4, 5), (4,)),
    ((2, 4, 5), (5,)),
    ((3, 5, 6), (5,)),
    ((4, 5), (4,)),
    ((2, 4), ()),
    ((1, 4, 5), ()),
    ((3, 5), ()),
    ((3, 4), ()),
    ((3, 4), (3,)),
    ((1, 4, 6), (6,)),
    ((2, 3), (2,)),
    ((2, 4, 5), (2, 4)),
)


def _gold_retention(case):
    return RetentionDecision(
        keep_ids=list(case.expected_keep_ids),
        forget_ids=[
            event.event_id for event in case.events
            if event.event_id not in case.expected_keep_ids
        ],
    )


def _gold_answer(case):
    return AnswerDecision(
        answer=case.expected_answer or "",
        source_event_ids=list(case.required_source_ids),
        abstained=case.expected_answer is None,
    )


def _payload(prompt, key):
    return json.loads(prompt[prompt.index(f'{{"{key}":'):])


def test_versioned_provenance_is_limited_to_first_run_synthetic_novelty():
    assert COHORT_ID == "unseen-synthetic-v1"
    assert COHORT_VERSION == 1
    assert COHORT_METADATA["cohort_id"] == COHORT_ID
    assert COHORT_METADATA["version"] == COHORT_VERSION
    assert COHORT_METADATA["description"] == COHORT_DESCRIPTION
    assert COHORT_METADATA["disclaimer"] == COHORT_DISCLAIMER
    assert COHORT_METADATA["unseen_to_eval_model_before_first_run"] is True
    assert COHORT_METADATA["independently_human_authored"] is False
    assert COHORT_METADATA["blinded"] is False
    assert COHORT_METADATA["model_output_derived"] is False
    assert "project-aware" in COHORT_METADATA["provenance"]
    assert "before inference" in COHORT_DISCLAIMER
    assert "subsequent runs as reuse" in COHORT_DISCLAIMER
    assert "not a claim about training-data exclusion" in COHORT_DISCLAIMER
    assert "held_out" not in json.dumps(COHORT_METADATA)


def test_exact_balanced_inventory_and_varied_history_lengths():
    assert isinstance(CASES, tuple)
    assert CASES == unseen_cases()
    assert tuple(case.case_id for case in CASES) == tuple(
        f"unseen-{number:02d}" for number in range(1, 21)
    )
    assert Counter(case.language for case in CASES) == {"en": 10, "ja": 10}
    assert Counter(case.category for case in CASES) == dict.fromkeys(CATEGORIES, 4)
    assert Counter((case.category, case.language) for case in CASES) == {
        (category, language): 2 for category in CATEGORIES for language in ("en", "ja")
    }
    assert Counter(len(case.events) for case in CASES) == {6: 7, 7: 7, 8: 6}
    assert sum(case.expected_answer is None for case in CASES) == 4
    assert len({case.expected_answer for case in CASES if case.expected_answer}) == 16
    assert sum(len(case.expected_keep_ids) for case in CASES) == 49
    assert sum(len(_gold_retention(case).forget_ids) for case in CASES) == 90
    all_ids = [event.event_id for case in CASES for event in case.events]
    assert len(all_ids) == len(set(all_ids)) == 139
    assert len({event.text for case in CASES for event in case.events}) == 139


def test_no_reused_pilot_ids_texts_or_gold_values():
    # The previous cohort is used only for a collision assertion, not case construction.
    previous = pilot_cases()
    assert {case.case_id for case in CASES}.isdisjoint(case.case_id for case in previous)
    assert {event.event_id for case in CASES for event in case.events}.isdisjoint(
        event.event_id for case in previous for event in case.events
    )
    assert {event.text for case in CASES for event in case.events}.isdisjoint(
        event.text for case in previous for event in case.events
    )
    previous_values = {
        value
        for case in previous
        for value in (case.expected_answer, *case.forbidden_answers)
        if value
    }
    new_values = {
        value
        for case in CASES
        for value in (case.expected_answer, *case.forbidden_answers)
        if value
    }
    assert new_values.isdisjoint(previous_values)
    assert all(
        value not in event.text
        for value in previous_values for case in CASES for event in case.events
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_contract_exact_timestamps_and_chronological_session_history(case):
    assert AgentMemoryCase.model_validate_json(case.model_dump_json()) == case
    assert tuple(event.event_id for event in case.events) == tuple(
        f"{case.case_id}-e{index}" for index in range(1, len(case.events) + 1)
    )
    times = [
        datetime.strptime(event.occurred_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        for event in case.events
    ]
    assert times == sorted(set(times))
    assert len({timestamp.date() for timestamp in times}) >= 2
    for event, timestamp in zip(case.events, times, strict=True):
        assert timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") == event.occurred_at
        assert timestamp.year == 2026
        assert timestamp.month == 7
    assert times[-1] > times[0]


@pytest.mark.parametrize(
    ("case", "positions"), tuple(zip(CASES, GOLD_POSITIONS, strict=True)),
    ids=[case.case_id for case in CASES],
)
def test_gold_retention_is_a_nonempty_valid_exhaustive_partition(case, positions):
    keep, sources = positions
    assert case.expected_keep_ids == tuple(f"{case.case_id}-e{index}" for index in keep)
    assert case.required_source_ids == tuple(f"{case.case_id}-e{index}" for index in sources)
    gold = _gold_retention(case)
    assert gold.keep_ids
    assert gold.forget_ids
    assert set(gold.keep_ids).isdisjoint(gold.forget_ids)
    assert set(gold.keep_ids) | set(gold.forget_ids) == {
        event.event_id for event in case.events
    }
    assert set(case.required_source_ids) <= set(gold.keep_ids)
    assert validate_retention(case, gold.model_dump_json()) == gold
    score = score_retention(case, gold)
    assert score.keep_precision == score.keep_recall == 1
    assert score.forget_precision == score.forget_recall == 1
    assert score.unsafe_deleted == 0


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_scalar_or_abstention_gold_is_valid_with_all_sources_deliverable(case):
    delivered = bounded_context(case, case.events)
    assert delivered == case.events
    assert len(answer_prompt(case, delivered).encode("utf-8")) <= CONTEXT_BUDGET_BYTES
    gold = _gold_answer(case)
    assert validate_answer(case, gold.model_dump_json(), delivered) == gold
    score = score_answer(case, gold, delivered)
    assert score.accuracy
    assert not score.forbidden_answer
    assert not score.unsupported_answer
    retained = tuple(
        event for event in case.events if event.event_id in case.expected_keep_ids
    )
    assert bounded_context(case, retained) == retained
    assert score_answer(case, gold, retained).accuracy
    if case.expected_answer is None:
        assert case.category == "explicit_forget"
        assert gold.abstained and not gold.answer and not gold.source_event_ids
        assert score.abstention_correctness
        assert case.forbidden_answers
    else:
        assert 1 <= len(case.expected_answer) <= 128
        assert 1 <= len(case.required_source_ids) <= 2
        assert any(
            case.expected_answer in event.text
            for event in case.events if event.event_id in case.required_source_ids
        )
        assert score.required_source_recall == score.citation_accuracy == 1
        with pytest.raises(ValueError, match="delivered"):
            validate_answer(case, gold.model_dump_json(), ())


def test_evidence_positions_do_not_make_every_recent_window_unanswerable():
    recent_answerable = []
    source_positions = set()
    for case in CASES:
        recent = recent_context(case)
        assert recent == case.events[-2:]
        assert len(answer_prompt(case, recent).encode("utf-8")) <= RECENT_BUDGET_BYTES
        positions = {
            index for index, event in enumerate(case.events, 1)
            if event.event_id in case.required_source_ids
        }
        source_positions.update(positions)
        if case.expected_answer is None:
            continue
        recent_ids = {event.event_id for event in recent}
        if set(case.required_source_ids) <= recent_ids:
            recent_answerable.append(case.case_id)
            assert score_answer(case, _gold_answer(case), recent).accuracy
        else:
            with pytest.raises(ValueError, match="delivered"):
                validate_answer(case, _gold_answer(case).model_dump_json(), recent)
    assert recent_answerable == ["unseen-04", "unseen-08", "unseen-10", "unseen-18"]
    assert source_positions == {1, 2, 3, 4, 5, 6, 7}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_prompts_expose_only_correct_question_and_selected_history_not_oracles(case):
    changed = case.model_copy(update={
        "expected_answer": "oracle-answer-sentinel",
        "expected_keep_ids": ("oracle-keep-sentinel",),
        "required_source_ids": ("oracle-source-sentinel",),
        "forbidden_answers": ("oracle-forbidden-sentinel",),
    })
    retention = retention_prompt(case)
    recall = recall_prompt(case)
    assert retention == retention_prompt(changed)
    assert recall == recall_prompt(changed)
    assert RETENTION_POLICY in retention
    assert _payload(retention, "events") == {
        "events": [event.model_dump() for event in case.events],
    }
    assert _payload(recall, "question") == {"question": case.question}
    assert case.question not in retention
    for event in case.events:
        assert event.event_id not in recall
        assert event.text not in recall
    for context in ((), recent_context(case), case.events):
        prompt = answer_prompt(case, context)
        assert prompt == answer_prompt(changed, context)
        assert RETENTION_POLICY in prompt
        assert _payload(prompt, "question") == {
            "question": case.question,
            "events": [event.model_dump() for event in context],
        }
        for label in (
            "expected_answer", "expected_keep_ids", "required_source_ids", "forbidden_answers",
            "oracle-", COHORT_ID,
        ):
            assert label not in prompt
            assert label not in retention
            assert label not in recall
    for value in (case.expected_answer, *case.forbidden_answers):
        if value:
            assert value not in recall
            assert value not in answer_prompt(case, ())


@pytest.mark.parametrize(
    "case", [case for case in CASES if case.forbidden_answers],
    ids=lambda case: case.case_id,
)
def test_corrections_and_forgetting_remove_all_obsolete_value_events(case):
    forgotten_value_positions = []
    for value in case.forbidden_answers:
        containing = [
            (index, event) for index, event in enumerate(case.events, 1) if value in event.text
        ]
        assert containing
        assert all(event.event_id not in case.expected_keep_ids for _, event in containing)
        forgotten_value_positions.extend(index for index, _ in containing)
        wrong = AnswerDecision(
            answer=value, source_event_ids=[containing[-1][1].event_id], abstained=False,
        )
        score = score_answer(case, wrong, case.events)
        assert score.forbidden_answer
        assert not score.accuracy
    if case.expected_answer is not None:
        final_position = next(
            index for index, event in enumerate(case.events, 1)
            if event.event_id in case.required_source_ids
        )
        assert max(forgotten_value_positions) < final_position
    else:
        retained_text = " ".join(
            event.text for event in case.events if event.event_id in case.expected_keep_ids
        )
        assert "unknown until" in retained_text or "まで" in retained_text
        assert all(value not in retained_text for value in case.forbidden_answers)


def test_three_histories_have_two_explicit_corrections_and_both_old_values_are_forbidden():
    twice_corrected = [
        case.case_id for case in CASES
        if case.category == "corrected_current_value" and len(case.forbidden_answers) == 2
    ]
    assert twice_corrected == ["unseen-09", "unseen-11", "unseen-12"]


@pytest.mark.parametrize(
    ("case_id", "bridge", "answer"),
    [
        ("unseen-06", "Ledger review", "Billing desk"),
        ("unseen-20", "交代確認", "受渡し照合票"),
    ],
)
def test_two_hop_gold_has_an_explicit_shared_bridge_and_requires_both_citations(
    case_id, bridge, answer,
):
    case = next(case for case in CASES if case.case_id == case_id)
    first, second = (
        event for event in case.events if event.event_id in case.required_source_ids
    )
    assert bridge in first.text and bridge in second.text
    assert answer not in first.text and answer in second.text
    assert case.expected_answer == answer
    for event in (first, second):
        with pytest.raises(ValueError, match="delivered"):
            validate_answer(case, _gold_answer(case).model_dump_json(), (event,))
        partial = AnswerDecision(
            answer=answer, source_event_ids=[event.event_id], abstained=False,
        )
        score = score_answer(case, partial, (event,))
        # Existing accuracy is scalar-only; evidence adequacy is scored separately.
        assert score.accuracy
        assert score.required_source_recall == 0.5
        assert score.unsupported_answer
    assert score_answer(case, _gold_answer(case), (first, second)).accuracy


def test_only_two_questions_require_an_evidence_chain():
    assert [case.case_id for case in CASES if len(case.required_source_ids) == 2] == [
        "unseen-06", "unseen-20",
    ]
