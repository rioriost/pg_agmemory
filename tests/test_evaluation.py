from itertools import product

import pytest
from pydantic import ValidationError

from pg_agmemory.evaluation import (
    BASELINES,
    EvaluationDataset,
    EvaluationQuestion,
    EvaluationSource,
    RetrievalObservation,
    RetrievalRun,
    grouped_interval,
    retrieval_report,
    score_ranking,
)


def dataset(groups=1, questions_per_group=1):
    return EvaluationDataset(
        dataset_id="synthetic-score-test",
        origin="synthetic",
        license="MIT",
        sources=[
            EvaluationSource(
                source_id=f"source-{group}",
                group_id=f"group-{group}",
                occurred_at="2026-09-01T00:00:00Z",
                text="Synthetic evidence, not private history.",
            )
            for group in range(groups)
        ],
        questions=[
            EvaluationQuestion(
                question_id=f"question-{group}-{index}",
                group_id=f"group-{group}",
                category="update" if index % 2 else "preference",
                language="en",
                split="test",
                query="Synthetic question",
                relevant={f"source-{group}": 3},
                answer="Synthetic answer",
            )
            for group, index in product(range(groups), range(questions_per_group))
        ],
    )


def run(data):
    return RetrievalRun(
        dataset_digest=data.digest(),
        implementation_sha="a" * 40,
        model_name="synthetic",
        model_revision="1",
        profile_digest="b" * 64,
        context_budget_bytes=8192,
        seed=42,
        observations=[
            RetrievalObservation(
                question_id=question.question_id,
                baseline=baseline,
                ranked_ids=[] if baseline == "no_memory" else list(question.relevant),
            )
            for question, baseline in product(data.questions, BASELINES)
            if question.split == "test"
        ],
    )


def test_scoring_computes_actual_rank_cutoffs_and_grades():
    data = dataset(25)
    sources = {source.source_id: source for source in data.sources}
    question = data.questions[0].model_copy(update={"relevant": {"source-0": 3, "source-1": 1}})
    score = score_ranking(question, [f"source-{index}" for index in range(24, -1, -1)], sources)
    assert score.recall_at_20 == score.ndcg_at_10 == 0
    assert score.reciprocal_rank == 1 / 24
    assert score.unauthorized_ids == 24
    reversed_score = score_ranking(question, ["source-1", "source-0"], sources)
    assert reversed_score.recall_at_20 == 1 and reversed_score.reciprocal_rank == 1
    assert 0 < reversed_score.ndcg_at_10 < 1


def test_exact_recall_at_twenty_boundary():
    data = dataset()
    sources = {source.source_id: source for source in data.sources}
    at_twenty = score_ranking(
        data.questions[0], [f"unknown-{index}" for index in range(19)] + ["source-0"], sources
    )
    after_twenty = score_ranking(
        data.questions[0], [f"unknown-{index}" for index in range(20)] + ["source-0"], sources
    )
    assert at_twenty.recall_at_20 == 1 and after_twenty.recall_at_20 == 0
    assert at_twenty.reciprocal_rank == 1 / 20 and after_twenty.reciprocal_rank == 1 / 21


def test_unanswerable_is_not_falsely_counted_as_perfect_recall_or_answering():
    data = dataset()
    question = data.questions[0].model_copy(update={"relevant": {}, "answer": ""})
    empty = score_ranking(question, [], {})
    nonempty = score_ranking(question, ["unknown"], {})
    assert empty.recall_at_20 is None and empty.ndcg_at_10 is None
    assert empty.reciprocal_rank is None and not empty.has_results
    assert nonempty.has_results and nonempty.unauthorized_ids == 1


def test_group_bootstrap_is_deterministic_and_resamples_sessions():
    groups = {"one": [0.0] * 10, "two": [1.0] * 10}
    interval = grouped_interval(groups, seed=42, samples=1000)
    assert interval.mean == 0.5 and interval.low == 0 and interval.high == 1
    assert interval.unit == "session_group"
    assert grouped_interval(dict(reversed(list(groups.items()))), seed=42, samples=1000) == interval
    assert grouped_interval({}, seed=42, samples=1000) is None


@pytest.mark.parametrize("groups", [{"one": []}, {"one": [float("nan")]}, {"one": [float("inf")]}])
def test_group_bootstrap_rejects_missing_or_nonfinite_measurements(groups):
    with pytest.raises(ValueError):
        grouped_interval(groups, seed=42, samples=100)


@pytest.mark.parametrize("count", [0, 99, 10001])
def test_report_requires_bounded_bootstrap_count(count):
    data = dataset()
    with pytest.raises(ValueError):
        retrieval_report(data, run(data), bootstrap_samples=count)


def test_complete_retrieval_results_cannot_qualify_human_or_real_task_gates():
    data = dataset(groups=50, questions_per_group=10)
    report = retrieval_report(data, run(data), bootstrap_samples=100)
    assert report.held_out_questions == 500 and report.held_out_groups == 50
    assert report.gates["recall_at_20"].status == "passed"
    assert report.gates["ranking_non_regression"].status == "passed"
    assert report.gates["observed_scope_leakage"].status == "passed"
    assert report.gates["human_assertion_precision"].status == "not_measured"
    assert report.gates["human_compaction_fidelity"].status == "not_measured"
    assert report.gates["real_task_replay"].status == "not_measured"
    assert report.gates["generated_acl_cases"].status == "not_measured"
    assert not report.m2_qualified
    assert set(report.baselines) == set(BASELINES)
    assert set(report.categories) == {"update", "preference"}
    assert report.baselines["no_memory"].recall_at_20.mean == 0


@pytest.mark.parametrize(("groups", "questions"), [(49, 11), (50, 9)])
def test_sample_gate_checks_questions_and_groups_independently(groups, questions):
    data = dataset(groups, questions)
    report = retrieval_report(data, run(data), bootstrap_samples=100)
    assert report.gates["internal_sample"].status == "failed"
    assert report.gates["recall_at_20"].status == "failed"


def test_leakage_remains_visible_in_failed_report_not_filtered_from_ranking():
    data = dataset(2)
    measured = run(data)
    measured.observations[1].ranked_ids.append("source-1")
    report = retrieval_report(data, measured, bootstrap_samples=100)
    assert report.gates["observed_scope_leakage"].status == "failed"
    assert report.baselines["recent_window"].unauthorized_ids == 1


def test_full_context_over_budget_is_explicitly_unmeasured():
    data = dataset()
    measured = run(data)
    measured.observations[2] = RetrievalObservation(
        question_id=data.questions[0].question_id,
        baseline="full_context",
        ranked_ids=[],
        skipped_reason="full_context_over_budget",
    )
    report = retrieval_report(data, measured, bootstrap_samples=100)
    assert report.baselines["full_context"].skipped == 1
    assert report.baselines["full_context"].recall_at_20 is None


@pytest.mark.parametrize(
    "change",
    [
        {"ranked_ids": ["source", "source"]},
        {"ranked_ids": ["source"], "skipped_reason": "full_context_over_budget"},
        {"baseline": "vector", "skipped_reason": "full_context_over_budget"},
        {"baseline": "no_memory", "ranked_ids": ["source"]},
    ],
)
def test_invalid_or_ambiguous_rankings_are_rejected(change):
    with pytest.raises(ValidationError):
        RetrievalObservation.model_validate(
            {"question_id": "q", "baseline": "full_context", "ranked_ids": [], **change}
        )


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unknown", "digest", "dev"])
def test_report_rejects_incomplete_mismatched_or_contaminated_measurement_matrix(damage):
    data = dataset()
    measured = run(data)
    if damage == "missing":
        measured.observations.pop()
    elif damage == "duplicate":
        measured.observations.append(measured.observations[0])
    elif damage == "unknown":
        measured.observations[0] = measured.observations[0].model_copy(
            update={"question_id": "unknown"}
        )
    elif damage == "digest":
        measured = measured.model_copy(update={"dataset_digest": "f" * 64})
    else:
        data.questions[0] = data.questions[0].model_copy(update={"split": "dev"})
        measured = measured.model_copy(update={"dataset_digest": data.digest()})
    with pytest.raises(ValueError):
        retrieval_report(data, measured, bootstrap_samples=100)


@pytest.mark.parametrize("damage", ["source", "question", "missing", "scope", "split", "corpus"])
def test_dataset_rejects_cross_scope_gold_and_session_split_leaks(damage):
    data = dataset(2, 2).model_dump()
    if damage == "source":
        data["sources"].append(data["sources"][0])
    elif damage == "question":
        data["questions"].append(data["questions"][0])
    elif damage == "missing":
        data["questions"][0]["relevant"] = {"missing": 1}
    elif damage == "scope":
        data["questions"][0]["relevant"] = {"source-1": 1}
    elif damage == "split":
        data["questions"][0]["split"] = "dev"
    else:
        data["sources"][0]["source_id"] = data["questions"][0]["question_id"]
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(data)
