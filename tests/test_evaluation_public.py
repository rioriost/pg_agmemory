import json

import pytest

from pg_agmemory.evaluation_public import load_longmemeval, normalize_longmemeval


def record(question_id="case"):
    return {
        "question_id": question_id,
        "question_type": "knowledge-update",
        "question": "What is the current tier?",
        "answer": "GOLD_ANNOTATION_MUST_NOT_BE_INGESTED",
        "question_date": "2026/09/18",
        "haystack_dates": ["2026/09/01"],
        "haystack_session_ids": ["answer_session_annotation"],
        "answer_session_ids": ["answer_session_annotation"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "Synthetic project is Gold.", "has_answer": True},
                {
                    "role": "assistant",
                    "content": "Recorded, not independently verified.",
                    "has_answer": False,
                },
            ]
        ],
    }


def test_public_adapter_separates_annotation_and_question_data_from_ingestible_history():
    data = normalize_longmemeval([record()])
    source_bytes = json.dumps([source.model_dump() for source in data.sources])
    for annotation in (
        "GOLD_ANNOTATION_MUST_NOT_BE_INGESTED",
        "has_answer",
        "answer_session_annotation",
        "knowledge-update",
        "What is the current tier?",
    ):
        assert annotation not in source_bytes
    assert data.origin == "public" and data.variant == "oracle-reader-diagnostic"
    assert data.retrieval_unit == "turn"
    assert data.questions[0].relevant == {data.sources[0].source_id: 1}
    assert all(source.occurred_at == "timezone-unknown" for source in data.sources)
    assert "Source date: 2026/09/01" in data.sources[0].text


def test_public_adapter_preserves_integer_answers_without_model_visible_gold():
    value = record()
    value["answer"] = 2022
    data = normalize_longmemeval([value])
    assert data.questions[0].answer == "2022"
    assert all("2022" not in source.text for source in data.sources)


def test_abstention_annotations_are_not_false_gold_evidence():
    data = normalize_longmemeval([record("case_abs")])
    assert data.questions[0].category == "abstention"
    assert data.questions[0].relevant == {}
    assert all(
        "_abs" not in source.source_id and "_abs" not in source.text for source in data.sources
    )


def test_public_selection_is_repeatable_order_independent_and_frozen_before_scoring():
    records = [record(str(index)) for index in range(10)]
    first = normalize_longmemeval(records)
    second = normalize_longmemeval(list(reversed(records)))
    assert first == second and len(first.questions) == 2
    assert first.digest() == second.digest()


@pytest.mark.parametrize(
    "damage",
    [
        "dates",
        "duplicate_session",
        "unknown_role",
        "annotation_type",
        "missing_gold",
        "answer_type",
        "unknown_category",
        "duplicate_question",
    ],
)
def test_public_adapter_rejects_ambiguous_or_unscorable_input(damage):
    value = record()
    if damage == "dates":
        value["haystack_dates"] = []
    elif damage == "duplicate_session":
        value["haystack_dates"] *= 2
        value["haystack_session_ids"] *= 2
        value["haystack_sessions"] *= 2
    elif damage == "unknown_role":
        value["haystack_sessions"][0][0]["role"] = "system"
    elif damage == "annotation_type":
        value["haystack_sessions"][0][0]["has_answer"] = 1
    elif damage == "missing_gold":
        value["haystack_sessions"][0][0]["has_answer"] = False
    elif damage == "answer_type":
        value["answer"] = {"unexpected": "answer"}
    elif damage == "unknown_category":
        value["question_type"] = "unsupported"
    with pytest.raises(ValueError):
        normalize_longmemeval([value, value] if damage == "duplicate_question" else [value])


def test_public_download_is_operator_supplied_and_exact_hash_verified(tmp_path):
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps([record()]))
    with pytest.raises(ValueError, match="pinned oracle artifact"):
        load_longmemeval(path)
