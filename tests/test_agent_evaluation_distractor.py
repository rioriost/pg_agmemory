import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

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
from pg_agmemory.agent_evaluation_distractor import (
    COHORT_DESCRIPTION,
    COHORT_DISCLAIMER,
    COHORT_ID,
    COHORT_METADATA,
    COHORT_VERSION,
    DURABLE_DISTRACTOR_POSITIONS,
    DistractorMemoryCase,
    distractor_cases,
)
from pg_agmemory.agent_evaluation_unseen import unseen_cases
from pg_agmemory.bounded_recall import (
    MAX_ITEMS,
    MAX_PROMPT_BYTES,
    MAX_ROUNDS,
    MAX_SEARCH_REQUESTS,
    search_prompt,
)
from pg_agmemory.models import MemoryItem
from pg_agmemory.query_planning import JAPANESE_PROFILE, lexical_query_prompt

CASES = distractor_cases()
CATEGORIES = (
    "cross_session_preference", "project_constraint", "corrected_current_value",
    "explicit_forget", "workflow_failure_lesson",
)
# Independent pins, not labels read back from the fixture factory.
DURABLE_POSITIONS = (
    2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 16,
    18, 19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30,
)
# Exact answer, current target keep positions, sources, forbidden values, obsolete positions.
GOLD = (
    ("pickup slot ascending", (1,), (1,), (), ()),
    ("Quill register", (13,), (13,), (), ()),
    ("手書き連絡箱", (5, 21), (5, 21), (), ()),
    ("鑑賞の順路", (31,), (31,), (), ()),
    ("640 tiles", (9,), (9,), (), ()),
    ("Lantern desk", (1, 17), (1, 17), (), ()),
    ("36枚", (21,), (21,), (), ()),
    ("北棟工芸係", (31, 32), (31, 32), (), ()),
    ("weave-cobalt-9", (21,), (21,), ("weave-amber-3", "weave-ivory-6"), (1, 9)),
    ("2028-10-19", (32,), (32,), ("2028-10-04", "2028-10-12"), (5, 17)),
    ("こよみ五式", (21,), (21,), ("こよみ一式", "こよみ二式"), (1, 13)),
    ("桂棚受取台", (17,), (17,), ("柳棚受取台", "楡棚受取台"), (5, 9)),
    (None, (21,), (), ("velvet puffin",), (1,)),
    (None, (31,), (), ("bobbin parade",), (9,)),
    (None, (17,), (), ("水玉の羅針",), (5,)),
    (None, (32,), (), ("銀糸の集い",), (13,)),
    ("clamp before tracing", (5, 17), (5, 17), (), ()),
    ("rewind before arming", (31,), (31,), (), ()),
    ("中心印の先合わせ", (9,), (9,), (), ()),
    ("対角仮留め", (21,), (21,), (), ()),
)
SCOPES_AND_TOPICS = (
    ("Trelliscope", "dispatch"), ("Bramblefolio", "catalog proof"),
    ("菫時計舎", "校正相談"), ("凪絵工房", "展示案内"),
    ("Velvetbeam", "pattern export"), ("Morrowquay", "crate inspection"),
    ("琥珀刷房", "版画梱包"), ("青簾造形", "模型審査"),
    ("Rillweave", "catalog edition"), ("Pollenarc", "exhibition opening"),
    ("山繭文庫", "索引整備"), ("月砂製本", "見本回覧"),
    ("Thistledock", "rehearsal board"), ("Saffronspool", "sample exchange"),
    ("花礫模型", "試作展示"), ("宵藍工芸", "見本相談会"),
    ("Flintpetal", "stencil alignment"), ("Orchardlilt", "cue playback"),
    ("霞帆製図", "型紙転写"), ("露笛工作", "仕切り組立"),
)
CHAINS = (
    ("distractor-03", "若紫受付", "手書き連絡箱"),
    ("distractor-06", "Heron review", "Lantern desk"),
    ("distractor-08", "空輪審査経路", "北棟工芸係"),
    ("distractor-17", "Anchor sequence", "clamp before tracing"),
)


def _ids(case, positions):
    return tuple(f"{case.case_id}-e{position}" for position in positions)


def _events(case, ids):
    return tuple(event for event in case.events if event.event_id in ids)


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


def _memory_items(events):
    return tuple(
        MemoryItem(
            memory_id=UUID(int=index),
            type="episode",
            content=event.text,
            recorded_at=datetime(2026, 9, 25, tzinfo=UTC),
            occurred_at=datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")),
        )
        for index, event in enumerate(events, 1)
    )


def test_frozen_metadata_discloses_first_use_and_template_authorship_without_qualification():
    assert COHORT_ID == "distractor-synthetic-v1"
    assert COHORT_VERSION == 1
    assert COHORT_METADATA["cohort_id"] == COHORT_ID
    assert COHORT_METADATA["version"] == COHORT_VERSION
    assert COHORT_METADATA["description"] == COHORT_DESCRIPTION
    assert COHORT_METADATA["disclaimer"] == COHORT_DISCLAIMER
    for field in (
        "unseen_to_eval_model_before_first_run", "first_evaluation_use_only",
        "known_after_first_run", "policies_frozen",
    ):
        assert COHORT_METADATA[field] is True
    for field in (
        "external_qualification", "independently_human_authored", "blinded",
        "model_output_derived",
    ):
        assert COHORT_METADATA[field] is False
    assert "project-aware" in COHORT_METADATA["provenance"]
    assert "synthetic" in COHORT_METADATA["provenance"]
    assert "templates" in COHORT_METADATA["provenance"]
    assert "before inference" in COHORT_DISCLAIMER
    assert "known after" in COHORT_DISCLAIMER
    assert "subsequent runs are reuse" in COHORT_DISCLAIMER
    assert "not a claim about training-data exclusion" in COHORT_DISCLAIMER
    assert "External qualification is false" in COHORT_DISCLAIMER


def test_exact_inventory_language_category_balance_and_global_uniqueness():
    assert isinstance(CASES, tuple)
    assert CASES == distractor_cases()
    assert len(CASES) == 20
    assert tuple(case.case_id for case in CASES) == tuple(
        f"distractor-{number:02d}" for number in range(1, 21)
    )
    assert Counter(case.language for case in CASES) == {"en": 10, "ja": 10}
    assert Counter(case.category for case in CASES) == dict.fromkeys(CATEGORIES, 4)
    assert Counter((case.category, case.language) for case in CASES) == {
        (category, language): 2 for category in CATEGORIES for language in ("en", "ja")
    }
    assert Counter(len(case.events) for case in CASES) == {32: 20}
    assert sum(case.expected_answer is None for case in CASES) == 4
    assert len({case.expected_answer for case in CASES if case.expected_answer}) == 16
    assert sum(len(case.expected_keep_ids) for case in CASES) == 504
    assert sum(len(_gold_retention(case).forget_ids) for case in CASES) == 136
    assert len({event.event_id for case in CASES for event in case.events}) == 640
    assert len({event.text for case in CASES for event in case.events}) == 640
    assert len({case.question for case in CASES}) == 20


def test_prior_cohorts_are_used_only_for_collision_checks():
    previous = (*pilot_cases(), *unseen_cases())
    assert {case.case_id for case in CASES}.isdisjoint(case.case_id for case in previous)
    assert {event.event_id for case in CASES for event in case.events}.isdisjoint(
        event.event_id for case in previous for event in case.events
    )
    assert {event.text for case in CASES for event in case.events}.isdisjoint(
        event.text for case in previous for event in case.events
    )
    assert {case.question for case in CASES}.isdisjoint(case.question for case in previous)
    previous_values = {
        value for case in previous
        for value in (case.expected_answer, *case.forbidden_answers) if value
    }
    new_values = {
        value for case in CASES
        for value in (case.expected_answer, *case.forbidden_answers) if value
    }
    assert new_values.isdisjoint(previous_values)
    assert all(
        value not in event.text
        for value in previous_values for case in CASES for event in case.events
    )


def test_subclass_only_overrides_history_and_keep_bounds_without_relaxing_the_base():
    assert DistractorMemoryCase.__bases__ == (AgentMemoryCase,)
    assert set(DistractorMemoryCase.__annotations__) == {"events", "expected_keep_ids"}
    assert "valid_case" not in DistractorMemoryCase.__dict__
    assert DistractorMemoryCase.valid_case is AgentMemoryCase.valid_case
    assert DistractorMemoryCase.model_config == AgentMemoryCase.model_config
    base = AgentMemoryCase.model_json_schema()["properties"]
    derived = DistractorMemoryCase.model_json_schema()["properties"]
    assert base["events"]["minItems"] == 6 and base["events"]["maxItems"] == 8
    assert base["expected_keep_ids"]["maxItems"] == 7
    assert derived["events"]["minItems"] == derived["events"]["maxItems"] == 32
    assert derived["expected_keep_ids"]["minItems"] == 1
    assert derived["expected_keep_ids"]["maxItems"] == 32
    with pytest.raises(ValidationError):
        AgentMemoryCase.model_validate(CASES[0].model_dump())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_round_trip_immutability_and_exact_two_day_chronology(case):
    assert isinstance(case, AgentMemoryCase)
    assert DistractorMemoryCase.model_validate_json(case.model_dump_json()) == case
    assert DistractorMemoryCase.model_validate(case.model_dump()) == case
    assert tuple(event.event_id for event in case.events) == _ids(case, range(1, 33))
    number = int(case.case_id[-2:])
    times = [datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
             for event in case.events]
    assert times == sorted(set(times))
    assert len({timestamp.date() for timestamp in times}) == 2
    for position, (event, timestamp) in enumerate(zip(case.events, times, strict=True), 1):
        assert event.occurred_at == (
            f"2026-09-{number + (position - 1) // 16:02d}"
            f"T09:{(position - 1) % 16:02d}:00Z"
        )
        assert timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") == event.occurred_at
    with pytest.raises(ValidationError, match="frozen"):
        case.question = "Changed question"
    with pytest.raises(ValidationError, match="frozen"):
        case.events[0].text = "Changed evidence"
    with pytest.raises(TypeError):
        case.events[0] = case.events[1]
    with pytest.raises(TypeError):
        case.expected_keep_ids[0] = "changed"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("events", CASES[0].events[:31], "too_short"),
        ("events", (*CASES[0].events, CASES[0].events[0]), "too_long"),
        ("events", list(CASES[0].events), "tuple_type"),
        ("expected_keep_ids", (), "too_short"),
        ("expected_keep_ids", _ids(CASES[0], range(1, 34)), "too_long"),
        ("expected_keep_ids", list(CASES[0].expected_keep_ids), "tuple_type"),
        ("expected_keep_ids", ("not a valid identifier",), "string_pattern_mismatch"),
        ("required_source_ids", [CASES[0].events[0].event_id], "tuple_type"),
        ("language", "fr", "literal_error"),
        ("question", 42, "string_type"),
        ("extra_gold_hint", "do not accept extra fields", "extra_forbidden"),
    ],
)
def test_strict_bounds_and_extra_fields_are_enforced(field, value, error_type):
    data = CASES[0].model_dump()
    data[field] = value
    with pytest.raises(ValidationError) as error:
        DistractorMemoryCase.model_validate(data)
    assert error_type in {entry["type"] for entry in error.value.errors()}


def test_keep_bound_accepts_all_thirty_two_but_inherited_semantic_validators_still_apply():
    data = CASES[0].model_dump()
    all_ids = _ids(CASES[0], range(1, 33))
    data["expected_keep_ids"] = all_ids
    assert DistractorMemoryCase.model_validate(data).expected_keep_ids == all_ids
    bad_cases = []
    for field, value in (
        ("expected_keep_ids", (all_ids[0], all_ids[0])),
        ("expected_keep_ids", ("unknown-e1",)),
        ("required_source_ids", (all_ids[4],)),
        ("required_source_ids", (all_ids[0], all_ids[0])),
        ("required_source_ids", ()),
        ("expected_answer", None),
        ("forbidden_answers", (CASES[0].expected_answer,)),
    ):
        bad = CASES[0].model_dump()
        bad[field] = value
        bad_cases.append(bad)
    for field, value in (
        ("event_id", all_ids[0]),
        ("occurred_at", CASES[0].events[0].occurred_at),
        ("occurred_at", "2026-02-30T09:00:00Z"),
    ):
        bad = deepcopy(CASES[0].model_dump())
        bad["events"][1][field] = value
        bad_cases.append(bad)
    bad = CASES[0].model_dump()
    bad["events"] = tuple(reversed(bad["events"]))
    bad_cases.append(bad)
    for bad in bad_cases:
        with pytest.raises(ValidationError):
            DistractorMemoryCase.model_validate(bad)


@pytest.mark.parametrize(
    ("case", "gold", "scope_topic"),
    tuple(zip(CASES, GOLD, SCOPES_AND_TOPICS, strict=True)),
    ids=[case.case_id for case in CASES],
)
def test_independently_pinned_oracles_and_durable_near_topic_density(case, gold, scope_topic):
    answer, current, sources, forbidden, obsolete = gold
    scope, topic = scope_topic
    assert DURABLE_DISTRACTOR_POSITIONS == DURABLE_POSITIONS
    assert case.expected_answer == answer
    assert case.expected_keep_ids == _ids(case, sorted((*DURABLE_POSITIONS, *current)))
    assert case.required_source_ids == _ids(case, sources)
    assert case.forbidden_answers == forbidden
    distractors = tuple(case.events[position - 1] for position in DURABLE_POSITIONS)
    assert len(distractors) == 24
    assert 25 <= len(case.expected_keep_ids) <= 26
    assert set(_ids(case, DURABLE_POSITIONS)) <= set(case.expected_keep_ids)
    assert set(_ids(case, DURABLE_POSITIONS)).isdisjoint(case.required_source_ids)
    for event in distractors:
        assert scope in event.text and topic in event.text
        assert any(marker in event.text for marker in (
            "always", "must", "never", "future", "standing", "ongoing", "remains scheduled",
            "preserve source item identifiers across document revisions",
            "archive finalized review notes with their batch identifier",
            "今後も", "継続して", "現行",
        )), event.text
        assert all(value not in event.text for value in (answer, *forbidden) if value)
    assert all(position not in current for position in obsolete)
    forgotten = set(range(1, 33)) - set(DURABLE_POSITIONS) - set(current)
    for position in forgotten - set(obsolete):
        text = case.events[position - 1].text
        assert "one-off" in text or "一度限り" in text
        assert "complete" in text or "完了" in text


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_gold_retention_is_an_exhaustive_partition_with_perfect_unchanged_scores(case):
    gold = _gold_retention(case)
    assert gold.keep_ids and gold.forget_ids
    assert set(gold.keep_ids).isdisjoint(gold.forget_ids)
    assert set(gold.keep_ids) | set(gold.forget_ids) == {
        event.event_id for event in case.events
    }
    assert validate_retention(case, gold.model_dump_json()) == gold
    score = score_retention(case, gold)
    assert not score.failed
    assert score.keep_precision == score.keep_recall == 1
    assert score.forget_precision == score.forget_recall == 1
    assert score.unsafe_deleted == 0
    # Relevance-only retention is wrong even when it preserves every answer source.
    relevant_ids = [
        event_id for event_id in case.expected_keep_ids
        if event_id not in _ids(case, DURABLE_POSITIONS)
    ]
    relevance_only = RetentionDecision(
        keep_ids=relevant_ids,
        forget_ids=[event.event_id for event in case.events if event.event_id not in relevant_ids],
    )
    wrong = score_retention(case, relevance_only)
    assert wrong.unsafe_deleted == 24
    assert wrong.keep_recall < 0.1
    assert wrong.forget_precision < 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_correct_current_gold_fits_bounded_reader_and_retrieval_item_budgets(case):
    assert (MAX_ROUNDS, MAX_SEARCH_REQUESTS, MAX_ITEMS, MAX_PROMPT_BYTES) == (2, 4, 8, 8000)
    sources = _events(case, case.required_source_ids)
    current = tuple(
        event for event in _events(case, case.expected_keep_ids)
        if event.event_id not in _ids(case, DURABLE_POSITIONS)
    )
    assert 1 <= len(current) <= 2
    assert len(sources) <= 2
    assert bounded_context(case, current) == current
    gold = _gold_answer(case)
    assert validate_answer(case, gold.model_dump_json(), current) == gold
    score = score_answer(case, gold, current)
    assert score.accuracy and score.abstention_correctness
    assert not score.failed and not score.forbidden_answer and not score.unsupported_answer
    if case.expected_answer is None:
        assert case.category == "explicit_forget"
        assert not case.required_source_ids and not gold.source_event_ids and not gold.answer
        assert score.citation_accuracy is None and score.required_source_recall is None
    else:
        assert 1 <= len(case.expected_answer) <= 128
        assert score.citation_accuracy == score.required_source_recall == 1
        assert any(case.expected_answer in event.text for event in sources)
        with pytest.raises(ValueError, match="delivered"):
            validate_answer(case, gold.model_dump_json(), ())
    largest_distractors = sorted(
        (case.events[position - 1] for position in DURABLE_POSITIONS),
        key=lambda event: len(event.model_dump_json().encode("utf-8")),
        reverse=True,
    )[:MAX_ITEMS - len(current)]
    for context in ((*current, *largest_distractors), (*largest_distractors, *current)):
        assert len(context) == 8
        assert bounded_context(case, context) == context
        assert len(answer_prompt(case, context).encode("utf-8")) <= CONTEXT_BUDGET_BYTES
        assert score_answer(case, gold, context).accuracy


def test_exactly_four_answerable_recent_controls_and_early_middle_late_sources():
    recent_answerable = []
    source_positions = set()
    for case in CASES:
        recent = recent_context(case)
        assert recent == case.events[-2:]
        assert len(answer_prompt(case, recent).encode("utf-8")) <= RECENT_BUDGET_BYTES
        source_positions.update(
            position for position, event in enumerate(case.events, 1)
            if event.event_id in case.required_source_ids
        )
        if case.expected_answer is None:
            continue
        if set(case.required_source_ids) <= {event.event_id for event in recent}:
            recent_answerable.append(case.case_id)
            assert score_answer(case, _gold_answer(case), recent).accuracy
        else:
            with pytest.raises(ValueError, match="delivered"):
                validate_answer(case, _gold_answer(case).model_dump_json(), recent)
    assert recent_answerable == [
        "distractor-04", "distractor-08", "distractor-10", "distractor-18",
    ]
    assert source_positions == {1, 5, 9, 13, 17, 21, 31, 32}


@pytest.mark.parametrize(
    "case", [case for case in CASES if case.expected_answer is not None],
    ids=lambda case: case.case_id,
)
def test_scalar_scoring_remains_exact_without_whitespace_normalization(case):
    sources = _events(case, case.required_source_ids)
    for answer in (f" {case.expected_answer}", f"{case.expected_answer} "):
        wrong = AnswerDecision(
            answer=answer, source_event_ids=list(case.required_source_ids), abstained=False,
        )
        score = score_answer(case, wrong, sources)
        assert not score.accuracy and score.unsupported_answer
        assert not score.forbidden_answer
        assert score.citation_accuracy == score.required_source_recall == 1
    abstain = AnswerDecision(answer="", source_event_ids=[], abstained=True)
    score = score_answer(case, abstain, sources)
    assert not score.accuracy and not score.abstention_correctness


@pytest.mark.parametrize(
    ("case", "gold"), tuple(zip(CASES, GOLD, strict=True)),
    ids=[case.case_id for case in CASES],
)
def test_exact_old_value_positions_and_self_contained_corrections_or_value_free_revocations(
    case, gold,
):
    answer, current, _, forbidden, obsolete = gold
    if not forbidden:
        return
    for value, position in zip(forbidden, obsolete, strict=True):
        assert [
            index for index, event in enumerate(case.events, 1) if value in event.text
        ] == [position]
        old_event = case.events[position - 1]
        assert old_event.event_id not in case.expected_keep_ids
        wrong = AnswerDecision(
            answer=value, source_event_ids=[old_event.event_id], abstained=False,
        )
        score = score_answer(case, wrong, (old_event,))
        assert score.forbidden_answer and not score.accuracy and score.unsupported_answer
        assert max(obsolete) < current[0]
    retained = _events(case, case.expected_keep_ids)
    assert all(value not in event.text for event in retained for value in forbidden)
    latest = case.events[current[0] - 1]
    scope, topic = SCOPES_AND_TOPICS[int(case.case_id[-2:]) - 1]
    assert scope in latest.text and topic in latest.text
    if answer is None:
        assert "unknown until" in latest.text or "明示するまでは不明" in latest.text
        assert "revocation" in latest.text or "取り消し指示は保持" in latest.text
        assert not case.required_source_ids
        assert all(value not in latest.text for value in forbidden)
        assert score_answer(case, _gold_answer(case), (latest,)).accuracy
    else:
        assert answer in latest.text
        assert "current" in latest.text or "現在" in latest.text
        assert "obsolete" in latest.text or "無効" in latest.text
        assert score_answer(case, _gold_answer(case), (latest,)).accuracy


def test_exact_four_twice_corrected_histories_and_four_two_event_chains():
    assert [
        case.case_id for case in CASES
        if case.category == "corrected_current_value" and len(case.forbidden_answers) == 2
    ] == ["distractor-09", "distractor-10", "distractor-11", "distractor-12"]
    assert [case.case_id for case in CASES if len(case.required_source_ids) == 2] == [
        "distractor-03", "distractor-06", "distractor-08", "distractor-17",
    ]
    for number in (9, 10, 11, 12):
        case = CASES[number - 1]
        _, current, _, _, obsolete = GOLD[number - 1]
        assert obsolete[0] < obsolete[1] < current[0]
        for position in (obsolete[1], current[0]):
            text = case.events[position - 1].text
            assert "correction" in text.lower() or "訂正" in text


@pytest.mark.parametrize(("case_id", "bridge", "answer"), CHAINS)
def test_two_event_chains_have_distinct_hops_and_require_both_sources(case_id, bridge, answer):
    case = next(case for case in CASES if case.case_id == case_id)
    first, second = _events(case, case.required_source_ids)
    scope, _ = SCOPES_AND_TOPICS[int(case.case_id[-2:]) - 1]
    assert scope in first.text and scope not in second.text
    assert bridge in first.text and bridge in second.text
    assert answer not in first.text and answer in second.text
    assert answer not in case.question and bridge not in case.question
    assert case.expected_answer == answer
    for event in (first, second):
        with pytest.raises(ValueError, match="delivered"):
            validate_answer(case, _gold_answer(case).model_dump_json(), (event,))
        partial = AnswerDecision(
            answer=answer, source_event_ids=[event.event_id], abstained=False,
        )
        score = score_answer(case, partial, (event,))
        # Scalar accuracy is intentionally unchanged; inadequate evidence is separate.
        assert score.accuracy and score.unsupported_answer
        assert score.required_source_recall == 0.5
    assert score_answer(case, _gold_answer(case), (first, second)).accuracy


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_retention_and_all_planning_reader_prompts_fit_without_hidden_gold(case):
    changed_data = case.model_dump()
    changed_data.update({
        "expected_answer": "oracle-answer-sentinel",
        "expected_keep_ids": (case.events[1].event_id,),
        "required_source_ids": (case.events[1].event_id,),
        "forbidden_answers": ("oracle-forbidden-sentinel",),
    })
    changed = DistractorMemoryCase.model_validate(changed_data)
    retention = retention_prompt(case)
    recall = recall_prompt(case)
    profile = "simple-v1" if case.language == "en" else JAPANESE_PROFILE
    lexical = lexical_query_prompt(case.question, profile)
    bounded = search_prompt(case.question, profile)
    assert retention == retention_prompt(changed)
    assert recall == recall_prompt(changed)
    assert lexical == lexical_query_prompt(changed.question, profile)
    assert bounded == search_prompt(changed.question, profile)
    assert len(retention.encode("utf-8")) <= 65536
    assert len(bounded.encode("utf-8")) <= MAX_PROMPT_BYTES
    assert RETENTION_POLICY in retention
    assert _payload(retention, "events") == {
        "events": [event.model_dump() for event in case.events],
    }
    assert case.question not in retention
    assert _payload(recall, "question") == {"question": case.question}
    assert _payload(lexical, "question") == {
        "question": case.question, "search_profile": profile,
    }
    bounded_data = json.loads(bounded.split("INPUT=", 1)[1])
    assert bounded_data == {
        "question": case.question, "search_profile": profile, "round_number": 1,
        "previous_queries": [], "items": [], "retrieved_item_count": 0, "items_truncated": False,
    }
    for planner in (recall, lexical, bounded):
        assert all(event.event_id not in planner and event.text not in planner
                   for event in case.events)
        for value in (case.expected_answer, *case.forbidden_answers):
            if value:
                assert value not in planner
    sources = _events(case, case.required_source_ids)
    observed = sources[:1] or (case.events[1],)
    items = _memory_items(observed)
    question_anchor = SCOPES_AND_TOPICS[int(case.case_id[-2:]) - 1][0]
    followup = search_prompt(
        case.question, profile, items=items, previous_queries=(question_anchor,), round_number=2,
    )
    assert len(followup.encode("utf-8")) <= MAX_PROMPT_BYTES
    assert followup == search_prompt(
        changed.question, profile, items=items,
        previous_queries=(question_anchor,), round_number=2,
    )
    followup_data = json.loads(followup.split("INPUT=", 1)[1])
    assert followup_data["items"] == [
        {"memory_id": str(item.memory_id), "revision": item.revision, "content": item.content}
        for item in items
    ]
    assert followup_data["retrieved_item_count"] == len(observed)
    assert not followup_data["items_truncated"]
    for event in case.events:
        if event not in observed:
            assert event.text not in followup
    for context in ((), recent_context(case), sources):
        reader = answer_prompt(case, context)
        assert len(reader.encode("utf-8")) <= CONTEXT_BUDGET_BYTES
        assert reader == answer_prompt(changed, context)
        assert RETENTION_POLICY in reader
        assert _payload(reader, "question") == {
            "question": case.question, "events": [event.model_dump() for event in context],
        }
        for prompt in (reader, retention, recall, lexical, bounded, followup):
            for hidden in (
                "expected_answer", "expected_keep_ids", "required_source_ids", "forbidden_answers",
                "DURABLE_DISTRACTOR_POSITIONS", "oracle-", COHORT_ID, "external_qualification",
            ):
                assert hidden not in prompt
    for value in (case.expected_answer, *case.forbidden_answers):
        if value:
            assert value not in answer_prompt(case, ())
