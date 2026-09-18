"""Explicit local-model observations for an unrated, source-bound review pilot."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from pg_agmemory.evaluation import (
    Digest,
    EvaluationContract,
    EvaluationQuestion,
    EvaluationSource,
    Identifier,
)
from pg_agmemory.evaluation_runner import AnswerFailure, EvaluationAnswer, LocalEvaluation
from pg_agmemory.evaluation_wikipedia import WikipediaCorpus
from pg_agmemory.human_review import (
    MAX_CASE_OUTPUT_CHARS,
    Cohort,
    ReviewCase,
    ReviewPacket,
    ReviewSource,
    SourceReference,
    load_json,
    pending_form,
    prepare,
    score,
)
from pg_agmemory.providers import (
    ExtractionResult,
    HTTPProvider,
    InferenceInput,
    ProviderFailure,
    ProviderSettings,
    SummaryResult,
    extraction_schema,
    parse_settings,
)
from pg_agmemory.worker_profile import WorkerProfile

RECIPE: Final = "source-bound-human-review-pilot-v1"
EXCERPT_POLICY: Final = "first-nonempty-complete-paragraph-1600-utf8-v1"
Operation = Literal["extract", "summarize", "answer_definition", "answer_revision"]
OPERATIONS: tuple[Operation, ...] = (
    "extract", "summarize", "answer_definition", "answer_revision"
)
QUESTIONS = {
    "en": {
        "answer_definition": "According to this excerpt, what is {title}?",
        "answer_revision": (
            "According to this excerpt, what is the exact publication date of "
            "the next revision of this article?"
        ),
    },
    "ja": {
        "answer_definition": "この抜粋によると、{title}とは何ですか。",
        "answer_revision": (
            "この抜粋によると、この記事の次の改訂版が公開される"
            "正確な日付はいつですか。"
        ),
    },
}


def digest_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PilotExcerpt(EvaluationContract):
    source_id: Identifier
    parent_source_id: Identifier
    parent_text_sha256: Digest
    language: Literal["en", "ja"]
    title: Identifier
    revision_timestamp: Identifier
    text: Annotated[str, Field(min_length=1, max_length=1600)]
    text_sha256: Digest
    start: Annotated[int, Field(ge=0, strict=True)]
    end: Annotated[int, Field(ge=1, strict=True)]
    selection: Literal["first-nonempty-complete-paragraph-1600-utf8-v1"] = EXCERPT_POLICY

    @model_validator(mode="after")
    def integrity(self) -> "PilotExcerpt":
        encoded = self.text.encode("utf-8")
        if (
            not self.text.strip()
            or len(encoded) > 1600
            or self.end - self.start != len(self.text)
            or hashlib.sha256(encoded).hexdigest() != self.text_sha256
        ):
            raise ValueError("Invalid bounded review excerpt")
        return self


def excerpt(
    *, source_id: str, language: Literal["en", "ja"], title: str,
    revision_timestamp: str, text: str,
) -> PilotExcerpt:
    start = 0
    for paragraph in text.split("\n\n"):
        if paragraph.strip():
            if len(paragraph.encode("utf-8")) > 1600:
                raise ValueError("First complete paragraph exceeds the pilot budget")
            text_hash = hashlib.sha256(paragraph.encode()).hexdigest()
            return PilotExcerpt(
                source_id="excerpt_" + digest_json([source_id, text_hash, EXCERPT_POLICY]),
                parent_source_id=source_id,
                parent_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                language=language,
                title=title,
                revision_timestamp=revision_timestamp,
                text=paragraph,
                text_sha256=text_hash,
                start=start,
                end=start + len(paragraph),
            )
        start += len(paragraph) + 2
    raise ValueError("No nonempty paragraph available for review")


class PilotObservation(EvaluationContract):
    source_id: Identifier
    input_digest: Digest
    operation: Operation
    call: Annotated[int, Field(ge=1, strict=True)]
    question: str | None = None
    result: ExtractionResult | SummaryResult | EvaluationAnswer | None
    failure_code: Identifier | None = None
    billing_unknown: bool = False
    human_review: Literal["not_reviewed"] = "not_reviewed"

    @model_validator(mode="after")
    def integrity(self) -> "PilotObservation":
        if (self.result is None) != (self.failure_code is not None):
            raise ValueError("Each observation requires a result or an explicit failure")
        if self.result is not None:
            expected = (
                ExtractionResult if self.operation == "extract"
                else SummaryResult if self.operation == "summarize" else EvaluationAnswer
            )
            if not isinstance(self.result, expected) or self.billing_unknown:
                raise ValueError("Mismatched result operation or billing state")
            if isinstance(self.result, (ExtractionResult, SummaryResult)):
                if self.result.input_digest != self.input_digest:
                    raise ValueError("Model result does not bind the captured excerpt")
        if self.operation.startswith("answer_") != (self.question is not None):
            raise ValueError("Answer observations require their exact question")
        return self


class PilotMeasurements(EvaluationContract):
    recipe: Literal["source-bound-human-review-pilot-v1"] = RECIPE
    implementation_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    corpus_digest: Digest
    profile_digest: Digest
    settings: ProviderSettings
    excerpts: Annotated[list[PilotExcerpt], Field(min_length=1, max_length=20)]
    observations: list[PilotObservation]
    calls: Annotated[int, Field(ge=0, le=80, strict=True)]
    m2_qualified: Literal[False] = False
    purpose: Literal["unrated_development_pilot_not_m2_acceptance"] = (
        "unrated_development_pilot_not_m2_acceptance"
    )

    @model_validator(mode="after")
    def complete(self) -> "PilotMeasurements":
        sources = {source.source_id: source for source in self.excerpts}
        expected = {(source, operation) for source in sources for operation in OPERATIONS}
        observed = {(row.source_id, row.operation) for row in self.observations}
        if (
            len(sources) != len(self.excerpts)
            or observed != expected
            or len(self.observations) != len(expected)
            or self.calls != len(expected)
            or {row.call for row in self.observations} != set(range(1, self.calls + 1))
        ):
            raise ValueError("Review measurement matrix is incomplete or duplicated")
        for row in self.observations:
            source = sources[row.source_id]
            if row.input_digest != source.text_sha256:
                raise ValueError("Observation source digest mismatch")
            if (
                isinstance(row.result, (ExtractionResult, SummaryResult))
                and row.result.model != self.settings.text_model
            ):
                raise ValueError("Observation model differs from the declared profile")
            if row.operation.startswith("answer_"):
                question = QUESTIONS[source.language][row.operation].format(title=source.title)
                if row.question != question:
                    raise ValueError("Observation question differs from the frozen recipe")
        return self


class ReviewProvider(HTTPProvider):
    def __init__(self, settings: ProviderSettings, journal: LocalEvaluation) -> None:
        super().__init__(settings)
        self.journal = journal

    async def exchange(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.journal.record(
            "provider_request", call=self.journal.calls, path=path,
            request_digest=digest_json(payload), untrusted_request=payload,
        )
        wire = await super().exchange(path, payload)
        self.journal.record(
            "provider_response", call=self.journal.calls, untrusted_response=wire,
            billing_unknown=True,
        )
        return wire


async def measure(
    sources: list[PilotExcerpt], settings: ProviderSettings, *,
    corpus_digest: str, implementation_sha: str, output: Path, max_calls: int = 80,
) -> PilotMeasurements:
    settings = ProviderSettings.model_validate(settings.model_dump())
    sources = [PilotExcerpt.model_validate(source.model_dump()) for source in sources]
    if (
        not re.fullmatch(r"[0-9a-f]{40}", implementation_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", corpus_digest)
        or not 1 <= len(sources) <= 20
        or len({source.source_id for source in sources}) != len(sources)
        or type(max_calls) is not int
        or not len(sources) * 4 <= max_calls <= 80
        or settings.backend != "local_http"
        or settings.text_model is None
        or settings.embedding_model is None
        or settings.max_output_tokens is None
    ):
        raise ValueError("Pilot requires a pinned local profile and the full declared call budget")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    journal = LocalEvaluation(settings, journal=output / "journal.jsonl", max_calls=max_calls)
    journal.provider = ReviewProvider(settings, journal)
    profile = digest_json({
        "recipe": RECIPE, "excerpt_policy": EXCERPT_POLICY,
        "worker_profile": WorkerProfile(settings).digest,
        "extraction_schema": extraction_schema(), "qa_profile": journal.profile_digest(),
        "questions": QUESTIONS,
        "summary_review_max_chars": MAX_CASE_OUTPUT_CHARS,
        "sampling": {"extract": "provider-default", "summary": "provider-default", "qa_seed": 17},
    })
    journal.record(
        "pilot_started", implementation_sha=implementation_sha, corpus_digest=corpus_digest,
        profile_digest=profile, settings=settings.model_dump(mode="json"),
        excerpts=[source.model_dump(mode="json") for source in sources],
        planned_calls=len(sources) * 4, max_calls=max_calls, m2_qualified=False,
    )
    observations: list[PilotObservation] = []
    for source in sources:
        for operation in OPERATIONS:
            question_text = (
                QUESTIONS[source.language][operation].format(title=source.title)
                if operation.startswith("answer_") else None
            )
            result: ExtractionResult | SummaryResult | EvaluationAnswer | None = None
            failure: str | None = None
            unknown = False
            fatal: ProviderFailure | None = None
            try:
                data = InferenceInput(text=source.text)
                if operation in ("extract", "summarize"):
                    journal.reserve_call(operation, data.digest())
                    result = (
                        await journal.provider.extract(data) if operation == "extract"
                        else await journal.provider.summarize(data)
                    )
                    if (
                        isinstance(result, SummaryResult)
                        and len(result.summary) > MAX_CASE_OUTPUT_CHARS
                    ):
                        raise ProviderFailure("invalid_provider_response", unknown=True)
                    journal.record("call_completed", call=journal.calls, billing_unknown=False)
                else:
                    assert question_text is not None
                    question = EvaluationQuestion(
                        question_id="review_question_" + digest_json([source.source_id, operation]),
                        group_id=source.parent_source_id, category="unrated_review_pilot",
                        language=source.language, split="dev", query=question_text,
                        relevant={}, answer="",
                    )
                    result = await journal.answer(
                        question,
                        [EvaluationSource(
                            source_id=source.source_id, group_id=source.parent_source_id,
                            occurred_at=source.revision_timestamp, text=source.text,
                        )],
                        seed=17,
                    )
            except AnswerFailure as exc:
                failure, unknown = exc.code, True
            except ProviderFailure as exc:
                result = None
                failure, unknown = exc.error.code, exc.error.billing_unknown
                if exc.error.code != "invalid_provider_response":
                    fatal = exc
            observation = PilotObservation(
                source_id=source.source_id, input_digest=source.text_sha256,
                operation=operation, call=journal.calls, question=question_text,
                result=result, failure_code=failure, billing_unknown=unknown,
            )
            observations.append(observation)
            journal.record("pilot_observation", **observation.model_dump(mode="json"))
            if fatal is not None:
                journal.record("pilot_aborted", failure_code=failure, m2_qualified=False)
                raise fatal
    measured = PilotMeasurements(
        implementation_sha=implementation_sha, corpus_digest=corpus_digest,
        profile_digest=profile, settings=settings, excerpts=sources,
        observations=observations, calls=journal.calls,
    )
    fd = os.open(output / "measurements.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(measured.model_dump_json(indent=2) + "\n")
    journal.record("pilot_completed", calls=journal.calls, m2_qualified=False)
    return measured


def corpus_excerpts(corpus: WikipediaCorpus) -> list[PilotExcerpt]:
    return [
        excerpt(
            source_id=source.source_id, language=source.language, title=source.title,
            revision_timestamp=source.revision_timestamp, text=source.text,
        )
        for source in corpus.sources
    ]


def review_sources(
    corpus: WikipediaCorpus, excerpts: list[PilotExcerpt]
) -> tuple[ReviewSource, ...]:
    if excerpts != corpus_excerpts(corpus):
        raise ValueError("Excerpts differ from the declared corpus selection")
    originals = {source.source_id: source for source in corpus.sources}
    return tuple(
        ReviewSource(
            source_id=item.source_id, text=item.text, text_sha256=item.text_sha256,
            language=item.language, origin="public_wikipedia", title=item.title,
            source_url=originals[item.parent_source_id].revision_url,
            history_url=originals[item.parent_source_id].history_url,
            revision=str(originals[item.parent_source_id].revision_id),
            license=originals[item.parent_source_id].license,
            attribution=originals[item.parent_source_id].attribution,
            modifications="; ".join(originals[item.parent_source_id].modifications) + (
                f"; Pilot excerpt policy {EXCERPT_POLICY}; original text SHA256 "
                f"{item.parent_text_sha256}; Unicode slice [{item.start},{item.end}); "
                "model outputs are untrusted adaptations, not Wikipedia contributor statements."
            ),
        )
        for item in excerpts
    )


def review_packet(corpus: WikipediaCorpus, measurements: PilotMeasurements) -> ReviewPacket:
    measurements = PilotMeasurements.model_validate_json(measurements.model_dump_json())
    if measurements.corpus_digest != corpus.digest():
        raise ValueError("Measurements do not bind this corpus")
    sources = review_sources(corpus, measurements.excerpts)
    cases: list[ReviewCase] = []
    for row in measurements.observations:
        case_id = "case_" + digest_json([row.source_id, row.operation])
        cohort: Cohort = (
            "untrusted_extracted_proposals" if row.operation == "extract"
            else "provider_summaries" if row.operation == "summarize" else "provider_answers"
        )
        result = row.result
        if result is None:
            cases.append(ReviewCase(
                case_id=case_id, kind="generation_failure", cohort=cohort,
                source_ids=(row.source_id,), output="", question=row.question,
                status="failed", error_code=row.failure_code,
            ))
        elif isinstance(result, ExtractionResult):
            if not result.candidates:
                cases.append(ReviewCase(
                    case_id=case_id, kind="extraction_abstention", cohort=cohort,
                    source_ids=(row.source_id,), output="", status="abstained",
                ))
            for index, candidate in enumerate(result.candidates):
                cases.append(ReviewCase(
                    case_id=case_id + ":" + str(index), kind="assertion", cohort=cohort,
                    source_ids=(row.source_id,), output=candidate.model_dump_json(indent=2),
                    references=(SourceReference(
                        source_id=row.source_id, quote=candidate.evidence_quote,
                        start=candidate.start, end=candidate.end,
                    ),),
                ))
        elif isinstance(result, SummaryResult):
            cases.append(ReviewCase(
                case_id=case_id, kind="summary", cohort=cohort,
                source_ids=(row.source_id,), output=result.summary,
            ))
        else:
            cases.append(ReviewCase(
                case_id=case_id, kind="answer", cohort=cohort, source_ids=(row.source_id,),
                output=result.answer, question=row.question,
                status="abstained" if result.abstained else "ok",
                references=tuple(SourceReference(source_id=source) for source in result.citations),
            ))
    model = measurements.settings.text_model
    if model is None:
        raise ValueError("Missing measured text model")
    return ReviewPacket(
        packet_id="wiki-pilot-" + digest_json(measurements.model_dump(mode="json")),
        implementation_sha=measurements.implementation_sha,
        model_name=model.name, model_revision=model.revision,
        profile_name=RECIPE, profile_digest=measurements.profile_digest,
        recipe=RECIPE, sources=sources, cases=tuple(cases),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer", action="append", required=True)
    parser.add_argument("--max-calls", type=int, default=80)
    parser.add_argument("--allow-local-model-calls", action="store_true", required=True)
    args = parser.parse_args()
    try:
        if (
            len(args.reviewer) != 2 or args.reviewer[0] == args.reviewer[1]
            or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", reviewer)
                for reviewer in args.reviewer
            )
        ):
            raise ValueError("Two distinct reviewer identifiers are required")
        corpus = load_json(args.corpus, WikipediaCorpus)
        with args.profile.open("rb") as stream:
            settings = parse_settings(stream.read(32769))
        excerpts = corpus_excerpts(corpus)
        review_sources(corpus, excerpts)
        measured = asyncio.run(measure(
            excerpts, settings, corpus_digest=corpus.digest(),
            implementation_sha=args.implementation_sha, output=args.output,
            max_calls=args.max_calls,
        ))
        packet = review_packet(corpus, measured)
        reviewers = (args.reviewer[0], args.reviewer[1])
        prepare(packet, args.output / "review", reviewers)
        report = score(
            packet, (pending_form(packet, reviewers[0]), pending_form(packet, reviewers[1]))
        )
        fd = os.open(
            args.output / "readiness.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(report.model_dump_json(indent=2) + "\n")
    except (OSError, ValueError, ProviderFailure) as exc:
        code = exc.error.code if isinstance(exc, ProviderFailure) else type(exc).__name__
        print(
            f"review_pilot: stopped ({code}); preserve any partial journal. "
            "No automatic retry or human qualification.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    print(json.dumps({
        "status": "awaiting_human_review", "packet_digest": packet.digest(),
        "sources": len(packet.sources), "cases": len(packet.cases), "calls": measured.calls,
        "generation_failures": sum(row.failure_code is not None for row in measured.observations),
        "human_review_verified": False, "m2_qualified": False,
    }))


if __name__ == "__main__":
    main()
