"""Reproducible retrieval measurements, separate from human M2 qualification."""

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Relevance = Annotated[int, Field(ge=1, le=3, strict=True)]
Baseline = Literal[
    "no_memory", "recent_window", "full_context", "vector", "hybrid", "temporal_provenance"
]
BASELINES: tuple[Baseline, ...] = (
    "no_memory",
    "recent_window",
    "full_context",
    "vector",
    "hybrid",
    "temporal_provenance",
)


class EvaluationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationSource(EvaluationContract):
    source_id: Identifier
    group_id: Identifier
    occurred_at: Identifier
    text: Annotated[str, Field(min_length=1, max_length=65536)]


class EvaluationQuestion(EvaluationContract):
    question_id: Identifier
    group_id: Identifier
    category: Identifier
    language: Literal["en", "ja"]
    split: Literal["dev", "test"]
    query: Annotated[str, Field(min_length=1, max_length=4096)]
    relevant: dict[Identifier, Relevance]
    answer: Annotated[str, Field(max_length=4096)]
    as_of: Identifier | None = None


class EvaluationDataset(EvaluationContract):
    dataset_id: Identifier
    origin: Literal["synthetic", "public", "authorized_private"]
    license: Identifier
    retrieval_unit: Literal["source", "turn"] = "source"
    source_revision: Identifier | None = None
    source_file_digest: Digest | None = None
    variant: Identifier | None = None
    sources: Annotated[list[EvaluationSource], Field(min_length=1, max_length=100000)]
    questions: Annotated[list[EvaluationQuestion], Field(min_length=1, max_length=10000)]

    @model_validator(mode="after")
    def integrity(self) -> "EvaluationDataset":
        sources = {source.source_id: source for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("Duplicate source IDs")
        if len({question.question_id for question in self.questions}) != len(self.questions):
            raise ValueError("Duplicate question IDs")
        if set(sources) & {question.question_id for question in self.questions}:
            raise ValueError("Questions are not corpus sources")
        group_splits: dict[str, str] = {}
        for question in self.questions:
            if group_splits.setdefault(question.group_id, question.split) != question.split:
                raise ValueError("A session group cannot cross dev/test splits")
            for source_id in question.relevant:
                if source_id not in sources:
                    raise ValueError("Unknown evidence ID")
                if sources[source_id].group_id != question.group_id:
                    raise ValueError("Gold evidence must be in the authorized group")
        return self

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class RetrievalObservation(EvaluationContract):
    question_id: Identifier
    baseline: Baseline
    ranked_ids: Annotated[list[Identifier], Field(max_length=100)]
    skipped_reason: Literal["full_context_over_budget"] | None = None

    @model_validator(mode="after")
    def valid_ranking(self) -> "RetrievalObservation":
        if len(self.ranked_ids) != len(set(self.ranked_ids)):
            raise ValueError("Rankings must contain distinct source IDs")
        if self.skipped_reason is not None and (self.baseline != "full_context" or self.ranked_ids):
            raise ValueError("Only an unmeasured full-context baseline can exceed its budget")
        if self.baseline == "no_memory" and self.ranked_ids:
            raise ValueError("The no-memory baseline cannot retrieve evidence")
        return self


class RetrievalRun(EvaluationContract):
    dataset_digest: Digest
    implementation_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    model_name: Identifier
    model_revision: Identifier
    profile_digest: Digest
    context_budget_bytes: Annotated[int, Field(ge=1, le=1048576, strict=True)]
    seed: Annotated[int, Field(ge=0, le=2147483647, strict=True)]
    observations: Annotated[list[RetrievalObservation], Field(min_length=1, max_length=60000)]


class RetrievalScore(EvaluationContract):
    recall_at_20: float | None
    ndcg_at_10: float | None
    reciprocal_rank: float | None
    unauthorized_ids: int
    has_results: bool


class MeanInterval(EvaluationContract):
    mean: float
    low: float
    high: float
    confidence: Annotated[float, Field(ge=0.95, le=0.95)] = 0.95
    unit: Literal["session_group"] = "session_group"


class BaselineMeasurement(EvaluationContract):
    questions: int
    answerable: int
    skipped: int
    unauthorized_ids: int
    unanswerable_with_results: int
    recall_at_20: MeanInterval | None
    ndcg_at_10: MeanInterval | None
    mrr: MeanInterval | None


class EvaluationGate(EvaluationContract):
    status: Literal["passed", "failed", "not_measured"]
    reason: str


class RetrievalReport(EvaluationContract):
    dataset_id: str
    dataset_digest: str
    implementation_sha: str
    model_name: str
    model_revision: str
    profile_digest: str
    context_budget_bytes: int
    retrieval_unit: Literal["source", "turn"]
    source_revision: str | None
    source_file_digest: str | None
    variant: str | None
    split: Literal["test"] = "test"
    held_out_questions: int
    held_out_groups: int
    seed: int
    bootstrap_samples: int
    baselines: dict[Baseline, BaselineMeasurement]
    categories: dict[str, dict[Baseline, BaselineMeasurement]]
    gates: dict[str, EvaluationGate]
    m2_qualified: Literal[False] = False
    limitations: list[str]


def score_ranking(
    question: EvaluationQuestion, ranked_ids: list[str], sources: dict[str, EvaluationSource]
) -> RetrievalScore:
    unauthorized = sum(
        source_id not in sources or sources[source_id].group_id != question.group_id
        for source_id in ranked_ids
    )
    if not question.relevant:
        return RetrievalScore(
            recall_at_20=None,
            ndcg_at_10=None,
            reciprocal_rank=None,
            unauthorized_ids=unauthorized,
            has_results=bool(ranked_ids),
        )
    gains = [question.relevant.get(source_id, 0) for source_id in ranked_ids]
    dcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(gains[:10]))
    ideal = sorted(question.relevant.values(), reverse=True)[:10]
    idcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(ideal))
    rank = next((index + 1 for index, gain in enumerate(gains) if gain), None)
    return RetrievalScore(
        recall_at_20=len(set(ranked_ids[:20]) & question.relevant.keys()) / len(question.relevant),
        ndcg_at_10=dcg / idcg,
        reciprocal_rank=1 / rank if rank is not None else 0,
        unauthorized_ids=unauthorized,
        has_results=bool(ranked_ids),
    )


def grouped_interval(
    values: dict[str, list[float]], *, seed: int, samples: int
) -> MeanInterval | None:
    if not values:
        return None
    if not 100 <= samples <= 10000:
        raise ValueError("Bootstrap samples must be between 100 and 10000")
    groups = [values[key] for key in sorted(values)]
    if any(not group or any(not math.isfinite(value) for value in group) for group in groups):
        raise ValueError("Bootstrap groups must contain finite measurements")
    rng = random.Random(seed)
    bootstraps = []
    for _ in range(samples):
        selected = rng.choices(groups, k=len(groups))
        bootstraps.append(sum(map(sum, selected)) / sum(map(len, selected)))
    bootstraps.sort()
    return MeanInterval(
        mean=sum(map(sum, groups)) / sum(map(len, groups)),
        low=bootstraps[math.floor((samples - 1) * 0.025)],
        high=bootstraps[math.ceil((samples - 1) * 0.975)],
    )


def measurement(
    rows: list[tuple[EvaluationQuestion, RetrievalObservation, RetrievalScore]],
    *,
    seed: int,
    samples: int,
) -> BaselineMeasurement:
    def interval(field: str) -> MeanInterval | None:
        groups: dict[str, list[float]] = defaultdict(list)
        for question, observation, score in rows:
            value = getattr(score, field)
            if observation.skipped_reason is None and value is not None:
                groups[question.group_id].append(value)
        return grouped_interval(groups, seed=seed, samples=samples)

    return BaselineMeasurement(
        questions=len(rows),
        answerable=sum(bool(question.relevant) for question, _, _ in rows),
        skipped=sum(observation.skipped_reason is not None for _, observation, _ in rows),
        unauthorized_ids=sum(score.unauthorized_ids for _, _, score in rows),
        unanswerable_with_results=sum(
            not question.relevant and score.has_results for question, _, score in rows
        ),
        recall_at_20=interval("recall_at_20"),
        ndcg_at_10=interval("ndcg_at_10"),
        mrr=interval("reciprocal_rank"),
    )


def retrieval_report(
    dataset: EvaluationDataset, run: RetrievalRun, *, bootstrap_samples: int = 1000
) -> RetrievalReport:
    if run.dataset_digest != dataset.digest():
        raise ValueError("Dataset digest does not match the measured corpus and questions")
    if not 100 <= bootstrap_samples <= 10000:
        raise ValueError("Bootstrap samples must be between 100 and 10000")
    questions = {question.question_id: question for question in dataset.questions}
    test = {key: question for key, question in questions.items() if question.split == "test"}
    if not test:
        raise ValueError("No held-out test questions")
    observations: dict[tuple[str, Baseline], RetrievalObservation] = {}
    for observation in run.observations:
        key = (observation.question_id, observation.baseline)
        if key in observations:
            raise ValueError("Duplicate question/baseline measurement")
        if observation.question_id not in test:
            raise ValueError("Only held-out questions belong in the test report")
        observations[key] = observation
    if set(observations) != {(key, baseline) for key in test for baseline in BASELINES}:
        raise ValueError(
            "Every held-out question requires every baseline or an explicit budget skip"
        )
    sources = {source.source_id: source for source in dataset.sources}
    rows = [
        (
            test[question_id],
            observation,
            score_ranking(test[question_id], observation.ranked_ids, sources),
        )
        for (question_id, _), observation in sorted(observations.items())
    ]
    baselines = {
        baseline: measurement(
            [row for row in rows if row[1].baseline == baseline],
            seed=run.seed,
            samples=bootstrap_samples,
        )
        for baseline in BASELINES
    }
    categories = {
        category: {
            baseline: measurement(
                [
                    row
                    for row in rows
                    if row[0].category == category and row[1].baseline == baseline
                ],
                seed=run.seed,
                samples=bootstrap_samples,
            )
            for baseline in BASELINES
        }
        for category in sorted({question.category for question in test.values()})
    }
    groups = len({question.group_id for question in test.values()})
    enough = len(test) >= 500 and groups >= 50
    hybrid, vector, temporal = (
        baselines["hybrid"],
        baselines["vector"],
        baselines["temporal_provenance"],
    )

    def threshold(passed: bool, reason: str) -> EvaluationGate:
        return EvaluationGate(status="passed" if passed else "failed", reason=reason)

    def at_least(actual: float, minimum: float) -> bool:
        return actual >= minimum or math.isclose(actual, minimum, rel_tol=0, abs_tol=1e-12)

    gates = {
        "internal_sample": threshold(
            enough, "At least 500 held-out questions in 50 session groups"
        ),
        "observed_scope_leakage": threshold(
            all(row.unauthorized_ids == 0 for row in baselines.values()),
            "Zero unauthorized IDs in this measured retrieval run, not the 10,000-case attack gate",
        ),
        "recall_at_20": threshold(
            enough
            and hybrid.recall_at_20 is not None
            and vector.recall_at_20 is not None
            and at_least(hybrid.recall_at_20.mean, 0.9)
            and at_least(hybrid.recall_at_20.mean, vector.recall_at_20.mean),
            "Held-out hybrid Recall@20 >= 90% and >= the measured vector baseline",
        ),
        "ranking_non_regression": threshold(
            enough
            and all(
                getattr(temporal, metric) is not None
                and getattr(hybrid, metric) is not None
                and at_least(getattr(temporal, metric).mean, getattr(hybrid, metric).mean)
                for metric in ("ndcg_at_10", "mrr")
            ),
            "Temporal/provenance nDCG@10 and MRR must not regress from hybrid",
        ),
    }
    if dataset.origin == "public":
        for gate in ("internal_sample", "recall_at_20", "ranking_non_regression"):
            gates[gate] = EvaluationGate(
                status="not_measured",
                reason="A public baseline is not the independent internal held-out acceptance set",
            )
    for gate in (
        "human_assertion_precision",
        "human_compaction_fidelity",
        "answer_quality",
        "real_task_replay",
        "public_baseline",
        "generated_acl_cases",
        "worker_chaos",
        "deletion_recovery",
        "cost_and_footprint",
    ):
        gates[gate] = EvaluationGate(
            status="not_measured",
            reason="Requires its separate recorded experiment or human review",
        )
    return RetrievalReport(
        dataset_id=dataset.dataset_id,
        dataset_digest=dataset.digest(),
        implementation_sha=run.implementation_sha,
        model_name=run.model_name,
        model_revision=run.model_revision,
        profile_digest=run.profile_digest,
        context_budget_bytes=run.context_budget_bytes,
        retrieval_unit=dataset.retrieval_unit,
        source_revision=dataset.source_revision,
        source_file_digest=dataset.source_file_digest,
        variant=dataset.variant,
        held_out_questions=len(test),
        held_out_groups=groups,
        seed=run.seed,
        bootstrap_samples=bootstrap_samples,
        baselines=baselines,
        categories=categories,
        gates=gates,
        limitations=[
            "Retrieval scores do not measure answer correctness or semantic assertion support.",
            "Results on unanswerable questions are not equivalent to unsupported answers.",
            "Confidence intervals resample session groups, not independent questions.",
            "Synthetic sources or model judges do not satisfy human or real-task acceptance gates.",
            "A successful scoring command does not qualify M2.",
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pg_agmemory.evaluation")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    args = parser.parse_args()
    try:
        with args.dataset.open("rb") as stream:
            raw_dataset = stream.read(32 * 1024 * 1024 + 1)
        with args.run.open("rb") as stream:
            raw_run = stream.read(32 * 1024 * 1024 + 1)
        if max(len(raw_dataset), len(raw_run)) > 32 * 1024 * 1024:
            raise ValueError("Evaluation input exceeds 32 MiB")
        report = retrieval_report(
            EvaluationDataset.model_validate_json(raw_dataset),
            RetrievalRun.model_validate_json(raw_run),
            bootstrap_samples=args.bootstrap_samples,
        )
    except (OSError, ValueError, UnicodeError):
        parser.exit(2, "Invalid evaluation input or incomplete measurement matrix\n")
    print(report.model_dump_json(indent=2))
    if any(gate.status == "failed" for gate in report.gates.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
