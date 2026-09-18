"""Offline, self-declared review records; never human authentication or M2 qualification.

Construct ``ReviewSource`` and ``ReviewCase`` objects, then a ``ReviewPacket``.
``prepare(packet, out, (reviewer_a, reviewer_b))`` creates private, new-only files.
Edit each reviewer's JSON independently, then ``score(packet, (form_a, form_b))``.
Digests detect stale/mixed artifacts, not malicious rewriting or reviewer identity.
Source preannotation is supported, but per-claim retention scoring is deliberately
NOT_MEASURED: a holistic summary label is not working-memory compaction fidelity.
"""

import argparse
import hashlib
import html
import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pg_agmemory.evaluation import EvaluationContract

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_SOURCES = 100
MAX_CASES = 500
MAX_CASE_OUTPUT_CHARS = 16384
RUBRIC_VERSION: Literal["pg-agmemory-human-review-v1"] = "pg-agmemory-human-review-v1"

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=32768)]
ShortText = Annotated[str, Field(min_length=1, max_length=4096)]
Count = Annotated[int, Field(ge=0, le=MAX_CASES)]
Kind = Literal["assertion", "summary", "answer", "extraction_abstention", "generation_failure"]
Cohort = Literal[
    "untrusted_extracted_proposals",
    "provider_summaries",
    "provider_answers",
    "adopted_assertions",
    "published_summaries",
    "published_answers",
]
Outcome = Literal[
    "supported",
    "contradicted",
    "insufficient",
    "uncertain",
    "faithful",
    "omission",
    "distortion",
    "grounded_correct",
    "grounded_incorrect",
    "unsupported",
    "appropriate_abstention",
    "unnecessary_abstention",
    "generation_failed",
]
Severity = Literal["none", "minor", "major", "critical"]
ALLOWED_OUTCOMES: dict[str, tuple[str, ...]] = {
    "assertion": ("supported", "contradicted", "insufficient", "uncertain"),
    "summary": ("faithful", "omission", "distortion", "uncertain"),
    "answer": (
        "grounded_correct",
        "grounded_incorrect",
        "unsupported",
        "appropriate_abstention",
        "unnecessary_abstention",
        "uncertain",
    ),
    "extraction_abstention": (
        "appropriate_abstention",
        "unnecessary_abstention",
        "uncertain",
    ),
    "generation_failure": ("generation_failed", "uncertain"),
}
POSITIVE_OUTCOMES: frozenset[Outcome] = frozenset(
    ("supported", "faithful", "grounded_correct", "appropriate_abstention")
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def text_digest(text: str) -> str:
    """SHA-256 of the exact UTF-8 text, without normalization."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReviewContract(EvaluationContract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReviewSource(ReviewContract):
    source_id: Identifier
    text: Text
    language: Annotated[str, Field(pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")]
    origin: Literal["public_wikipedia", "synthetic", "authorized_private"]
    title: ShortText
    source_url: Annotated[str, Field(max_length=2048)] | None = None
    revision: ShortText | None = None
    history_url: Annotated[str, Field(max_length=2048)] | None = None
    license: ShortText
    attribution: ShortText
    modifications: ShortText
    text_sha256: Digest | None = None

    @model_validator(mode="after")
    def integrity(self) -> Self:
        for text in (self.text, self.title, self.license, self.attribution, self.modifications):
            if not text.strip():
                raise ValueError("Source text and provenance must not be blank")
        for url in (self.source_url, self.history_url):
            if url is not None:
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in ("https", "http")
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or any(ord(char) <= 32 or ord(char) == 127 for char in url)
                    or "\\" in url
                ):
                    raise ValueError("Source links must be HTTP(S) URLs without credentials")
                _ = parsed.port
        if self.origin == "public_wikipedia" and (
            not self.source_url or not self.history_url or not self.revision
        ):
            raise ValueError("Public Wikipedia requires URL, revision and history provenance")
        expected = text_digest(self.text)
        if self.text_sha256 is not None and self.text_sha256 != expected:
            raise ValueError("Source text digest mismatch")
        object.__setattr__(self, "text_sha256", expected)
        return self

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class SourceReference(ReviewContract):
    source_id: Identifier
    quote: Text | None = None
    start: Annotated[int, Field(ge=0, le=32768)] | None = None
    end: Annotated[int, Field(ge=1, le=32768)] | None = None

    @model_validator(mode="after")
    def span(self) -> Self:
        if (self.start is None) != (self.end is None):
            raise ValueError("Both span endpoints are required")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("Span must be nonempty")
        if self.quote is not None and not self.quote.strip():
            raise ValueError("Quote must not be blank")
        return self


class ReviewCase(ReviewContract):
    case_id: Identifier
    kind: Kind
    cohort: Cohort
    source_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=MAX_SOURCES)]
    output: Annotated[str, Field(max_length=MAX_CASE_OUTPUT_CHARS)]
    question: ShortText | None = None
    references: Annotated[tuple[SourceReference, ...], Field(max_length=20)] = ()
    status: Literal["ok", "abstained", "failed"] = "ok"
    error_code: Identifier | None = None
    output_sha256: Digest | None = None

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Duplicate case source IDs")
        allowed_cohorts = {
            "assertion": ("untrusted_extracted_proposals", "adopted_assertions"),
            "summary": ("provider_summaries", "published_summaries"),
            "answer": ("provider_answers", "published_answers"),
            "extraction_abstention": ("untrusted_extracted_proposals",),
        }
        if self.kind != "generation_failure" and self.cohort not in allowed_cohorts[self.kind]:
            raise ValueError("Case kind does not match cohort")
        if (self.kind == "generation_failure") != (self.status == "failed"):
            raise ValueError("Failed calls must be generation_failure cases")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("Exactly failed calls require an error code")
        if self.kind == "extraction_abstention" and self.status != "abstained":
            raise ValueError("Extraction abstention requires abstained status")
        if self.status == "abstained" and self.kind not in ("answer", "extraction_abstention"):
            raise ValueError("Only answer or extraction abstention may abstain")
        if self.status == "ok" and not self.output.strip():
            raise ValueError("Successful cases require nonempty output")
        if self.kind == "answer" and (self.question is None or not self.question.strip()):
            raise ValueError("Answer cases require a question")
        if any(reference.source_id not in self.source_ids for reference in self.references):
            raise ValueError("Case reference is outside its sources")
        expected = text_digest(self.output)
        if self.output_sha256 is not None and self.output_sha256 != expected:
            raise ValueError("Case output digest mismatch")
        object.__setattr__(self, "output_sha256", expected)
        return self

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


def _check_reference(reference: SourceReference, sources: dict[str, ReviewSource]) -> None:
    if reference.source_id not in sources:
        raise ValueError("Unknown source reference")
    text = sources[reference.source_id].text
    if reference.end is not None:
        if reference.end > len(text):
            raise ValueError("Source span exceeds text")
        if reference.quote is not None and text[reference.start : reference.end] != reference.quote:
            raise ValueError("Quote differs from the exact source span")
    elif reference.quote is not None and reference.quote not in text:
        raise ValueError("Quote is absent from source")


class ReviewPacket(ReviewContract):
    schema_version: Literal["pg-agmemory-review-packet-v1"] = "pg-agmemory-review-packet-v1"
    rubric_version: Literal["pg-agmemory-human-review-v1"] = RUBRIC_VERSION
    packet_id: Identifier
    implementation_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    model_name: ShortText
    model_revision: ShortText
    profile_name: ShortText
    profile_digest: Digest
    recipe: ShortText
    sources: Annotated[tuple[ReviewSource, ...], Field(min_length=1, max_length=MAX_SOURCES)]
    cases: Annotated[tuple[ReviewCase, ...], Field(min_length=1, max_length=MAX_CASES)]
    packet_digest: Digest | None = None
    human_review_verified: Literal[False] = False

    @model_validator(mode="after")
    def integrity(self) -> Self:
        sources = {source.source_id: source for source in self.sources}
        if any(
            not value.strip()
            for value in (self.model_name, self.model_revision, self.profile_name, self.recipe)
        ):
            raise ValueError("Model, profile and recipe identity must not be blank")
        if len(sources) != len(self.sources):
            raise ValueError("Duplicate source IDs")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case IDs")
        for case in self.cases:
            if not set(case.source_ids) <= sources.keys():
                raise ValueError("Case references unknown sources")
            for reference in case.references:
                _check_reference(reference, sources)
        payload = self.model_dump(mode="json", exclude={"packet_digest"})
        if len(_canonical(payload)) > MAX_JSON_BYTES - 256:
            raise ValueError("Packet exceeds byte limit")
        expected = _digest(payload)
        if self.packet_digest is not None and self.packet_digest != expected:
            raise ValueError("Packet digest mismatch")
        object.__setattr__(self, "packet_digest", expected)
        return self

    def digest(self) -> str:
        assert self.packet_digest is not None
        return self.packet_digest

    def source_digest(self) -> str:
        return _digest([source.model_dump(mode="json") for source in self.sources])


class SourceBinding(ReviewContract):
    source_id: Identifier
    source_digest: Digest
    text_sha256: Digest


class CaseRating(ReviewContract):
    case_id: Identifier
    case_digest: Digest
    output_sha256: Digest
    kind: Kind
    outcome: Outcome | None = None
    severity: Severity | None = None
    rationale: ShortText | None = None
    source_references: Annotated[tuple[SourceReference, ...], Field(max_length=MAX_SOURCES)] = ()

    @model_validator(mode="after")
    def valid_label(self) -> Self:
        if self.outcome is None:
            if self.severity is not None or self.rationale is not None or self.source_references:
                raise ValueError("Pending labels require null severity/rationale and no references")
        elif (
            self.outcome not in ALLOWED_OUTCOMES[self.kind]
            or self.severity is None
            or self.rationale is None
            or not self.rationale.strip()
            or not self.source_references
        ):
            raise ValueError("Completed labels need valid outcome, severity, rationale and sources")
        if len({ref.source_id for ref in self.source_references}) != len(self.source_references):
            raise ValueError("Duplicate rating source references")
        return self


class ReviewForm(ReviewContract):
    schema_version: Literal["pg-agmemory-review-form-v1"] = "pg-agmemory-review-form-v1"
    rubric_version: Literal["pg-agmemory-human-review-v1"] = RUBRIC_VERSION
    packet_id: Identifier
    packet_digest: Digest
    source_digest: Digest
    reviewer_id: Identifier
    identity_basis: Literal["self_declared_not_authenticated"] = "self_declared_not_authenticated"
    human_review_verified: Literal[False] = False
    sources: Annotated[tuple[SourceBinding, ...], Field(min_length=1, max_length=MAX_SOURCES)]
    ratings: Annotated[tuple[CaseRating, ...], Field(min_length=1, max_length=MAX_CASES)]

    @model_validator(mode="after")
    def distinct_ids(self) -> Self:
        if len({rating.case_id for rating in self.ratings}) != len(self.ratings):
            raise ValueError("Duplicate case ratings")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source bindings")
        return self


class ImportantClaim(ReviewContract):
    claim_id: Identifier
    text: ShortText
    grounding: SourceReference

    @model_validator(mode="after")
    def grounded(self) -> Self:
        if not self.text.strip() or (self.grounding.quote is None and self.grounding.start is None):
            raise ValueError("Human important claims require text and an exact source quote/span")
        return self


class SourceAnnotation(ReviewContract):
    source: SourceBinding
    status: Literal["pending", "complete"] = "pending"
    important_claims: Annotated[tuple[ImportantClaim, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if len({claim.claim_id for claim in self.important_claims}) != len(self.important_claims):
            raise ValueError("Duplicate important claim IDs")
        if any(
            claim.grounding.source_id != self.source.source_id for claim in self.important_claims
        ):
            raise ValueError("Claim is grounded in a different source")
        return self


class SourceReviewForm(ReviewContract):
    """Source-only preannotation. No model-generated gold or retention computation."""

    schema_version: Literal["pg-agmemory-source-review-v1"] = "pg-agmemory-source-review-v1"
    rubric_version: Literal["pg-agmemory-human-review-v1"] = RUBRIC_VERSION
    packet_id: Identifier
    packet_digest: Digest
    source_digest: Digest
    reviewer_id: Identifier
    identity_basis: Literal["self_declared_not_authenticated"] = "self_declared_not_authenticated"
    human_review_verified: Literal[False] = False
    annotations: Annotated[
        tuple[SourceAnnotation, ...], Field(min_length=1, max_length=MAX_SOURCES)
    ]

    @model_validator(mode="after")
    def distinct_ids(self) -> Self:
        ids = tuple(annotation.source.source_id for annotation in self.annotations)
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate source annotations")
        return self


def _revalidate[Model: ReviewContract](model: Model) -> Model:
    # Pydantic's model_copy(update=...) intentionally bypasses validation.
    payload = model.model_dump_json().encode("utf-8")
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("Review artifact exceeds byte limit")
    return type(model).model_validate_json(payload)


def _bindings(packet: ReviewPacket) -> tuple[SourceBinding, ...]:
    return tuple(
        SourceBinding(
            source_id=source.source_id,
            source_digest=source.digest(),
            text_sha256=text_digest(source.text),
        )
        for source in packet.sources
    )


def pending_form(packet: ReviewPacket, reviewer_id: str) -> ReviewForm:
    packet = _revalidate(packet)
    return ReviewForm(
        packet_id=packet.packet_id,
        packet_digest=packet.digest(),
        source_digest=packet.source_digest(),
        reviewer_id=reviewer_id,
        sources=_bindings(packet),
        ratings=tuple(
            CaseRating(
                case_id=case.case_id,
                case_digest=case.digest(),
                output_sha256=text_digest(case.output),
                kind=case.kind,
            )
            for case in packet.cases
        ),
    )


def pending_source_form(packet: ReviewPacket, reviewer_id: str) -> SourceReviewForm:
    packet = _revalidate(packet)
    return SourceReviewForm(
        packet_id=packet.packet_id,
        packet_digest=packet.digest(),
        source_digest=packet.source_digest(),
        reviewer_id=reviewer_id,
        annotations=tuple(SourceAnnotation(source=binding) for binding in _bindings(packet)),
    )


def _validate_binding(packet: ReviewPacket, form: ReviewForm | SourceReviewForm) -> None:
    if (
        form.packet_id != packet.packet_id
        or form.packet_digest != packet.digest()
        or form.source_digest != packet.source_digest()
        or form.rubric_version != packet.rubric_version
    ):
        raise ValueError("Form is bound to a different packet, corpus or rubric")


def validate_source_form(packet: ReviewPacket, form: SourceReviewForm) -> SourceReviewForm:
    """Validate preannotation grounding; this does NOT measure per-claim retention."""
    packet, form = _revalidate(packet), _revalidate(form)
    _validate_binding(packet, form)
    if tuple(annotation.source for annotation in form.annotations) != _bindings(packet):
        raise ValueError("Source inventory must exactly match the packet in order")
    sources = {source.source_id: source for source in packet.sources}
    for annotation in form.annotations:
        for claim in annotation.important_claims:
            _check_reference(claim.grounding, sources)
    return form


def validate_form(packet: ReviewPacket, form: ReviewForm) -> ReviewForm:
    packet, form = _revalidate(packet), _revalidate(form)
    _validate_binding(packet, form)
    if form.sources != _bindings(packet):
        raise ValueError("Source bindings must exactly match the packet in order")
    if tuple(rating.case_id for rating in form.ratings) != tuple(
        case.case_id for case in packet.cases
    ):
        raise ValueError("Case inventory must exactly match the packet in order")
    sources = {source.source_id: source for source in packet.sources}
    for case, rating in zip(packet.cases, form.ratings, strict=True):
        if (
            rating.kind != case.kind
            or rating.case_digest != case.digest()
            or rating.output_sha256 != case.output_sha256
        ):
            raise ValueError("Case kind or digest mismatch")
        for reference in rating.source_references:
            if reference.source_id not in case.source_ids:
                raise ValueError("Rating references a source outside its case")
            _check_reference(reference, sources)
        if (
            case.kind == "answer"
            and rating.outcome is not None
            and rating.outcome != "uncertain"
            and (rating.outcome in ("appropriate_abstention", "unnecessary_abstention"))
            != (case.status == "abstained")
        ):
            raise ValueError("Answer label does not match its recorded abstention status")
    return form


class OutcomeCount(ReviewContract):
    outcome: Outcome
    count: Count


class ReviewCounts(ReviewContract):
    cohort: Cohort
    kind: Kind | None = None
    total: Count
    completed: Count
    pending: Count
    uncertain: Count
    generation_failures: Count
    abstentions: Count
    numerator: Count
    denominator: Count
    support_rate: Annotated[float, Field(ge=0, le=1)] | None
    outcomes: tuple[OutcomeCount, ...]
    denominator_definition: Literal[
        "all_cases_including_pending_uncertain_failures_abstentions"
    ] = "all_cases_including_pending_uncertain_failures_abstentions"

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        counts = {outcome.outcome: outcome.count for outcome in self.outcomes}
        expected_rate = self.numerator / self.total if self.total else None
        if (
            len(counts) != len(self.outcomes)
            or self.completed + self.pending != self.total
            or sum(counts.values()) != self.completed
            or self.uncertain != counts.get("uncertain", 0)
            or self.denominator != self.total
            or self.numerator != sum(counts.get(label, 0) for label in POSITIVE_OUTCOMES)
            or self.generation_failures + self.abstentions > self.total
            or self.support_rate != expected_rate
        ):
            raise ValueError("Inconsistent review counts or denominator")
        return self


class ReviewerReport(ReviewContract):
    reviewer_id: Identifier
    form_digest: Digest
    total: Count
    completed: Count
    pending: Count
    uncertain: Count
    complete: bool
    cohort_counts: Annotated[tuple[ReviewCounts, ...], Field(min_length=1, max_length=6)]
    kind_counts: Annotated[tuple[ReviewCounts, ...], Field(min_length=1, max_length=30)]

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if (
            self.completed + self.pending != self.total
            or self.uncertain > self.completed
            or self.complete != (self.pending == 0)
            or len({row.cohort for row in self.cohort_counts}) != len(self.cohort_counts)
            or any(row.kind is not None for row in self.cohort_counts)
            or any(row.kind is None for row in self.kind_counts)
            or len({(row.cohort, row.kind) for row in self.kind_counts}) != len(self.kind_counts)
        ):
            raise ValueError("Inconsistent reviewer totals or cohort inventory")
        for rows in (self.cohort_counts, self.kind_counts):
            for field in ("total", "completed", "pending", "uncertain"):
                if sum(getattr(row, field) for row in rows) != getattr(self, field):
                    raise ValueError("Reviewer totals differ from cohort or kind counts")
        return self


class ReviewerJudgment(ReviewContract):
    reviewer_id: Identifier
    outcome: Outcome
    severity: Severity
    rationale: ShortText


class Disagreement(ReviewContract):
    case_id: Identifier
    judgments: tuple[ReviewerJudgment, ReviewerJudgment]
    status: Literal["requires_adjudication"] = "requires_adjudication"


class GateStatus(ReviewContract):
    gate: Literal[
        "adopted_assertion_95_percent",
        "working_memory_compaction_98_percent",
        "real_20_tasks",
        "disaster_recovery",
    ]
    status: Literal["NOT_MEASURED"] = "NOT_MEASURED"


class ReviewReport(ReviewContract):
    schema_version: Literal["pg-agmemory-review-report-v1"] = "pg-agmemory-review-report-v1"
    rubric_version: Literal["pg-agmemory-human-review-v1"] = RUBRIC_VERSION
    packet_id: Identifier
    packet_digest: Digest
    source_digest: Digest
    implementation_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    reviewers: tuple[ReviewerReport, ReviewerReport]
    disagreements: Annotated[tuple[Disagreement, ...], Field(max_length=MAX_CASES)]
    disagreement_count: Count
    incomplete_cases: Annotated[tuple[Identifier, ...], Field(max_length=MAX_CASES)]
    uncertain_cases: Annotated[tuple[Identifier, ...], Field(max_length=MAX_CASES)]
    generation_failure_cases: Annotated[tuple[Identifier, ...], Field(max_length=MAX_CASES)]
    abstention_cases: Annotated[tuple[Identifier, ...], Field(max_length=MAX_CASES)]
    review_complete: bool
    human_review_verified: Literal[False] = False
    m2_qualified: Literal[False] = False
    summary_retention_status: Literal["NOT_MEASURED"] = "NOT_MEASURED"
    summary_retention_reason: ShortText = (
        "Requires a separately frozen human source-claim inventory and per-claim summary ratings; "
        "source preannotations and holistic faithful labels do not measure 98% retention."
    )
    gates: Annotated[tuple[GateStatus, ...], Field(min_length=4, max_length=4)]
    limitations: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=20)] = (
        "Reviewer identities and review activity are self-declared, not authenticated or verified.",
        "Digests bind artifacts, not signatures; changing every artifact can change the record.",
        "Support rates describe recorded labels with all cases as denominator, not gate precision.",
        "Untrusted extracted proposals are not adopted facts; provider output is not publication.",
        "Failed calls and abstentions remain in cohort totals; no cases are silently discarded.",
        "Disagreements require external adjudication; no majority vote or approval is inferred.",
        "Small public or synthetic pilots cannot close M2 adoption, compaction, task or DR gates.",
        "Source-only preannotation order cannot be enforced by offline files.",
    )

    @model_validator(mode="after")
    def consistent_inventory(self) -> Self:
        reviewer_ids = tuple(reviewer.reviewer_id for reviewer in self.reviewers)
        inventories = (
            self.incomplete_cases,
            self.uncertain_cases,
            self.generation_failure_cases,
            self.abstention_cases,
            tuple(item.case_id for item in self.disagreements),
        )
        if (
            reviewer_ids[0] == reviewer_ids[1]
            or self.reviewers[0].total != self.reviewers[1].total
            or self.disagreement_count != len(self.disagreements)
            or self.review_complete != (not self.incomplete_cases)
            or self.review_complete != all(reviewer.complete for reviewer in self.reviewers)
            or len({gate.gate for gate in self.gates}) != 4
            or any(len(set(ids)) != len(ids) for ids in inventories)
            or any(len(ids) > self.reviewers[0].total for ids in inventories)
            or set(self.generation_failure_cases) & set(self.abstention_cases)
        ):
            raise ValueError("Inconsistent review report inventory")
        for disagreement in self.disagreements:
            if (
                tuple(item.reviewer_id for item in disagreement.judgments) != reviewer_ids
                or len({(item.outcome, item.severity) for item in disagreement.judgments}) != 2
                or disagreement.case_id in self.incomplete_cases
            ):
                raise ValueError("Inconsistent disagreement record")
        return self


def _counts(
    pairs: tuple[tuple[ReviewCase, CaseRating], ...], cohort: Cohort, kind: Kind | None = None
) -> ReviewCounts:
    selected = tuple(
        (case, rating)
        for case, rating in pairs
        if case.cohort == cohort and (kind is None or case.kind == kind)
    )
    counts: Counter[Outcome] = Counter(
        rating.outcome for _, rating in selected if rating.outcome is not None
    )
    numerator = sum(counts[label] for label in POSITIVE_OUTCOMES)
    return ReviewCounts(
        cohort=cohort,
        kind=kind,
        total=len(selected),
        completed=sum(counts.values()),
        pending=len(selected) - sum(counts.values()),
        uncertain=counts["uncertain"],
        generation_failures=sum(case.status == "failed" for case, _ in selected),
        abstentions=sum(case.status == "abstained" for case, _ in selected),
        numerator=numerator,
        denominator=len(selected),
        support_rate=numerator / len(selected) if selected else None,
        outcomes=tuple(
            OutcomeCount(outcome=label, count=count) for label, count in sorted(counts.items())
        ),
    )


def score(packet: ReviewPacket, forms: tuple[ReviewForm, ReviewForm]) -> ReviewReport:
    """Report two independent self-declared label sets without resolving disagreements."""
    packet = _revalidate(packet)
    if len(forms) != 2 or forms[0].reviewer_id == forms[1].reviewer_id:
        raise ValueError("Exactly two distinct self-declared reviewers are required")
    first, second = (validate_form(packet, form) for form in forms)
    reviewers: list[ReviewerReport] = []
    for form in (first, second):
        pairs = tuple(zip(packet.cases, form.ratings, strict=True))
        pending = sum(rating.outcome is None for rating in form.ratings)
        uncertain = sum(rating.outcome == "uncertain" for rating in form.ratings)
        reviewers.append(
            ReviewerReport(
                reviewer_id=form.reviewer_id,
                form_digest=_digest(form.model_dump(mode="json")),
                total=len(pairs),
                completed=len(pairs) - pending,
                pending=pending,
                uncertain=uncertain,
                complete=pending == 0,
                cohort_counts=tuple(
                    _counts(pairs, cohort)
                    for cohort in sorted({case.cohort for case in packet.cases})
                ),
                kind_counts=tuple(
                    _counts(pairs, cohort, kind)
                    for cohort, kind in sorted({(case.cohort, case.kind) for case in packet.cases})
                ),
            )
        )
    disagreements: list[Disagreement] = []
    incomplete: list[str] = []
    uncertain_cases: list[str] = []
    for left, right in zip(first.ratings, second.ratings, strict=True):
        if left.outcome is None or right.outcome is None:
            incomplete.append(left.case_id)
        if left.outcome == "uncertain" or right.outcome == "uncertain":
            uncertain_cases.append(left.case_id)
        if left.outcome is None or right.outcome is None:
            continue
        if (left.outcome, left.severity) != (right.outcome, right.severity):
            judgments: list[ReviewerJudgment] = []
            for form, rating in ((first, left), (second, right)):
                assert rating.outcome is not None
                assert rating.severity is not None
                assert rating.rationale is not None
                judgments.append(
                    ReviewerJudgment(
                        reviewer_id=form.reviewer_id,
                        outcome=rating.outcome,
                        severity=rating.severity,
                        rationale=rating.rationale,
                    )
                )
            disagreements.append(
                Disagreement(case_id=left.case_id, judgments=(judgments[0], judgments[1]))
            )
    return ReviewReport(
        packet_id=packet.packet_id,
        packet_digest=packet.digest(),
        source_digest=packet.source_digest(),
        implementation_sha=packet.implementation_sha,
        reviewers=(reviewers[0], reviewers[1]),
        disagreements=tuple(disagreements),
        disagreement_count=len(disagreements),
        incomplete_cases=tuple(incomplete),
        uncertain_cases=tuple(uncertain_cases),
        generation_failure_cases=tuple(
            case.case_id for case in packet.cases if case.status == "failed"
        ),
        abstention_cases=tuple(case.case_id for case in packet.cases if case.status == "abstained"),
        review_complete=not incomplete,
        gates=(
            GateStatus(gate="adopted_assertion_95_percent"),
            GateStatus(gate="working_memory_compaction_98_percent"),
            GateStatus(gate="real_20_tasks"),
            GateStatus(gate="disaster_recovery"),
        ),
    )


INSTRUCTIONS = """Offline review v1. Reviewer identities are self-declared, not authenticated.
1. Before opening outputs.html or packet.json, read sources.html only.
   Independently edit your source-reviewer-N.json: add important_claims (initially empty),
   each with claim_id, text and grounding {source_id, quote, start, end}; offsets are
   zero-based Unicode code points, end-exclusive. quote or both offsets are required.
   Mark each source status complete only after source preannotation. Keep this record.
   No important claims or gold labels are generated for you. Files cannot enforce order.
2. Open outputs.html; compare each case to the exact original sources in sources.html.
   Edit only your reviewer-N.json outcomes, severity, rationale and source_references.
   Null outcome means pending; keep severity/rationale null and source_references empty.
   Completed labels, including uncertain and failures, need severity (none/minor/major/
   critical), a nonblank rationale, and source_references [{source_id, quote, start, end}].
   quote/start/end are optional for case ratings. Do not change identifiers or digests.
   Assertion: supported / contradicted / insufficient / uncertain.
   Summary: faithful / omission / distortion / uncertain (holistic, NOT claim retention).
   Answer: grounded_correct / grounded_incorrect / unsupported / appropriate_abstention /
           unnecessary_abstention / uncertain. Abstention labels require abstained status.
   Extraction abstention: appropriate_abstention / unnecessary_abstention / uncertain.
   Generation failure: generation_failed / uncertain. Never rate a failed call supported.
3. Keep both reviewers independent. Score both files; disagreement is NOT auto-resolved.
   All-case support denominators include pending, uncertain, failures and abstentions.
   Source forms can be validated with validate-source, but are not per-claim retention
   ratings. Summary retention and all M2 gates remain NOT_MEASURED, qualification false.
4. CLI: python -m pg_agmemory.human_review score --packet packet.json
   --form reviewer-1.json --form reviewer-2.json --out NEW_PRIVATE_REPORT_DIRECTORY
No network, model or database calls occur. HTML is read-only; edit the JSON in a text editor.
Links are provenance only; opening one manually leaves the offline workflow.
"""


def _document(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'; "
        "script-src 'none'; style-src 'none'; connect-src 'none'; img-src 'none'\">"
        '<meta name="referrer" content="no-referrer">'
        f"<title>{html.escape(title)}</title></head><body><h1>{html.escape(title)}</h1>"
        f"{body}</body></html>"
    )


def _pre(value: str) -> str:
    return "<pre>" + html.escape(value, quote=True) + "</pre>"


def _link(url: str | None) -> str:
    if url is None:
        return "<p>No external provenance link.</p>"
    escaped = html.escape(url, quote=True)
    return f'<p><a href="{escaped}" rel="noreferrer noopener">{escaped}</a></p>'


def render_sources(packet: ReviewPacket) -> str:
    """Source-only page intentionally excludes case inventory and all model outputs."""
    packet = _revalidate(packet)
    parts = [
        "<p>Read sources and independently define important claims BEFORE viewing outputs. "
        "Empty claim inventories are not gold. Review activity is not verified.</p>",
        _pre("Packet digest: " + packet.digest() + "\nSource digest: " + packet.source_digest()),
    ]
    for source in packet.sources:
        parts.extend(
            (
                "<section><h2>" + html.escape(source.source_id + ": " + source.title) + "</h2>",
                _pre(
                    f"Language: {source.language}\nOrigin: {source.origin}\n"
                    f"Revision: {source.revision or 'not supplied'}\nLicense: {source.license}\n"
                    f"Attribution: {source.attribution}\nModifications: {source.modifications}\n"
                    f"Source digest: {source.digest()}\nText SHA-256: {source.text_sha256}"
                ),
                _link(source.source_url),
                _link(source.history_url),
                _pre(source.text),
                "</section>",
            )
        )
    return _document("Source-only preannotation", "".join(parts))


def render_outputs(packet: ReviewPacket) -> str:
    packet = _revalidate(packet)
    parts = [
        "<p>Untrusted model outputs, NOT automatically adopted or published facts. "
        "Use sources.html for original source text. Do not open before preannotation.</p>",
        _pre(INSTRUCTIONS),
        _pre(
            f"Packet: {packet.packet_id}\nDigest: {packet.digest()}\n"
            f"Implementation: {packet.implementation_sha}\nModel: {packet.model_name}\n"
            f"Model revision: {packet.model_revision}\nProfile: {packet.profile_name}\n"
            f"Profile digest: {packet.profile_digest}\nRecipe: {packet.recipe}"
        ),
    ]
    for case in packet.cases:
        parts.extend(
            (
                "<section><h2>" + html.escape(case.case_id) + "</h2>",
                _pre(
                    f"Kind: {case.kind}\nCohort: {case.cohort}\nStatus: {case.status}\n"
                    f"Error code: {case.error_code or 'none'}\n"
                    f"Sources: {', '.join(case.source_ids)}\n"
                    f"Output SHA-256: {case.output_sha256}\nQuestion: {case.question or 'none'}"
                ),
                _pre(case.output),
                _pre(json.dumps([ref.model_dump() for ref in case.references], ensure_ascii=False)),
                "</section>",
            )
        )
    return _document("Model output review", "".join(parts))


def _json_bytes(model: ReviewContract) -> bytes:
    payload = model.model_dump_json(indent=2).encode("utf-8") + b"\n"
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("JSON output exceeds byte limit")
    return payload


def _write_new_directory(out: Path, files: dict[str, bytes]) -> None:
    if sum(len(payload) for payload in files.values()) > MAX_OUTPUT_BYTES:
        raise ValueError("Output exceeds byte limit")
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    # mkdir's mode is reduced by umask, never made broader.
    for name, payload in files.items():
        fd = os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)


def prepare(packet: ReviewPacket, out: Path, reviewer_ids: tuple[str, str]) -> None:
    """Write new owner-private packet, source/output HTML, and two pending form pairs."""
    packet = _revalidate(packet)
    if len(reviewer_ids) != 2 or reviewer_ids[0] == reviewer_ids[1]:
        raise ValueError("Exactly two distinct reviewer IDs are required")
    files = {
        "packet.json": _json_bytes(packet),
        "sources.html": render_sources(packet).encode("utf-8"),
        "outputs.html": render_outputs(packet).encode("utf-8"),
        "instructions.txt": INSTRUCTIONS.encode("utf-8"),
    }
    for index, reviewer in enumerate(reviewer_ids, 1):
        files[f"reviewer-{index}.json"] = _json_bytes(pending_form(packet, reviewer))
        files[f"source-reviewer-{index}.json"] = _json_bytes(pending_source_form(packet, reviewer))
    _write_new_directory(Path(out), files)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError("Non-finite JSON constant")


def load_json[Model: BaseModel](path: Path, model: type[Model]) -> Model:
    """Bounded, regular-file-only strict JSON loading, including duplicate-key rejection."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_BYTES:
            raise ValueError("Input must be a bounded regular JSON file")
        payload = stream.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds byte limit")
    json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    return model.model_validate_json(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--packet", type=Path, required=True)
    prepare_parser.add_argument("--reviewer", action="append", required=True)
    prepare_parser.add_argument("--out", type=Path, required=True)
    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--packet", type=Path, required=True)
    score_parser.add_argument("--form", type=Path, action="append", required=True)
    score_parser.add_argument("--out", type=Path, required=True)
    source_parser = subparsers.add_parser("validate-source")
    source_parser.add_argument("--packet", type=Path, required=True)
    source_parser.add_argument("--form", type=Path, required=True)
    source_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        packet = load_json(args.packet, ReviewPacket)
        if args.command == "prepare":
            if len(args.reviewer) != 2:
                raise ValueError("Exactly two reviewers required")
            prepare(packet, args.out, (args.reviewer[0], args.reviewer[1]))
        elif args.command == "score":
            if len(args.form) != 2:
                raise ValueError("Exactly two forms required")
            report = score(
                packet,
                (load_json(args.form[0], ReviewForm), load_json(args.form[1], ReviewForm)),
            )
            _write_new_directory(
                args.out,
                {
                    "report.json": _json_bytes(report),
                    "report.html": _document(
                        "Self-declared review report — not M2 qualification",
                        _pre(report.model_dump_json(indent=2)),
                    ).encode("utf-8"),
                },
            )
        else:
            validated = validate_source_form(packet, load_json(args.form, SourceReviewForm))
            _write_new_directory(args.out, {"source-review.json": _json_bytes(validated)})
    except (OSError, ValueError, RecursionError):
        print(
            "human_review: invalid, mismatched or oversized input, or unavailable new output "
            "directory; no review or qualification was verified.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
