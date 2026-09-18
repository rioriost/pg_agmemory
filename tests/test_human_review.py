"""Versioned synthetic review examples; labels here are test data, never human evidence."""

import json
import os
import stat
import subprocess
import sys
from html.parser import HTMLParser

import pytest
from pydantic import ValidationError

from pg_agmemory import human_review as review


def source(**updates):
    values = {
        "source_id": "source-1",
        "text": "Synthetic orchard: Mira planted three trees.",
        "language": "en",
        "origin": "synthetic",
        "title": "Synthetic orchard example",
        "license": "CC0-1.0",
        "attribution": "Synthetic test fixture, not a Wikipedia quotation",
        "modifications": "None",
    }
    values.update(updates)
    return review.ReviewSource(**values)


def case(**updates):
    values = {
        "case_id": "assertion-1",
        "kind": "assertion",
        "cohort": "untrusted_extracted_proposals",
        "source_ids": ("source-1",),
        "output": "Mira planted three trees.",
    }
    values.update(updates)
    return review.ReviewCase(**values)


def packet(**updates):
    values = {
        "packet_id": "synthetic-example-v1",
        "implementation_sha": "a" * 40,
        "model_name": "synthetic-test-model",
        "model_revision": "test-revision",
        "profile_name": "synthetic-profile",
        "profile_digest": "b" * 64,
        "recipe": "Test only; does not execute an inference provider",
        "sources": (source(),),
        "cases": (
            case(),
            case(
                case_id="summary-1",
                kind="summary",
                cohort="provider_summaries",
                output="Three trees were planted.",
            ),
            case(
                case_id="answer-1",
                kind="answer",
                cohort="provider_answers",
                output="Three.",
                question="How many trees did Mira plant?",
            ),
            case(
                case_id="abstention-1",
                kind="extraction_abstention",
                output="",
                status="abstained",
            ),
            case(
                case_id="failure-1",
                kind="generation_failure",
                output="",
                status="failed",
                error_code="provider_timeout",
            ),
        ),
    }
    values.update(updates)
    return review.ReviewPacket(**values)


def changed(model, **updates):
    value = model.model_dump(mode="json")
    value.update(updates)
    return type(model).model_validate_json(json.dumps(value))


def label(form, case_id, outcome, severity="none"):
    value = form.model_dump(mode="json")
    for rating in value["ratings"]:
        if rating["case_id"] == case_id:
            rating.update(
                outcome=outcome,
                severity=severity,
                rationale="Synthetic reviewer test label; not an actual human review.",
                source_references=[{"source_id": "source-1"}],
            )
    return review.ReviewForm.model_validate_json(json.dumps(value))


def complete_form(value, reviewer_id):
    form = review.pending_form(value, reviewer_id)
    for case_id, outcome in (
        ("assertion-1", "supported"),
        ("summary-1", "faithful"),
        ("answer-1", "grounded_correct"),
        ("abstention-1", "appropriate_abstention"),
        ("failure-1", "generation_failed"),
    ):
        form = label(form, case_id, outcome)
    return form


def test_pending_defaults_are_empty_and_never_qualification():
    value = packet()
    forms = tuple(review.pending_form(value, reviewer_id) for reviewer_id in ("alice", "bob"))
    for form in forms:
        assert form.identity_basis == "self_declared_not_authenticated"
        assert form.human_review_verified is False
        assert all(rating.outcome is None for rating in form.ratings)
        assert all(rating.rationale is None and rating.severity is None for rating in form.ratings)
        assert all(rating.source_references == () for rating in form.ratings)
    report = review.score(value, forms)
    assert not report.review_complete
    assert not report.m2_qualified and not report.human_review_verified
    assert report.disagreement_count == 0
    assert len(report.incomplete_cases) == 5
    for reviewer in report.reviewers:
        assert reviewer.pending == reviewer.total == 5
        assert reviewer.completed == reviewer.uncertain == 0
        assert not reviewer.complete
    assert report.summary_retention_status == "NOT_MEASURED"
    assert all(gate.status == "NOT_MEASURED" for gate in report.gates)


def test_all_completed_unanimous_labels_still_do_not_close_any_gate():
    value = packet()
    report = review.score(value, (complete_form(value, "alice"), complete_form(value, "bob")))
    assert report.review_complete
    assert report.disagreements == report.incomplete_cases == ()
    assert report.m2_qualified is report.human_review_verified is False
    assert report.summary_retention_status == "NOT_MEASURED"
    assert all(gate.status == "NOT_MEASURED" for gate in report.gates)
    assert "per-claim" in report.summary_retention_reason


@pytest.mark.parametrize(
    "updates",
    [
        {"m2_qualified": True},
        {"human_review_verified": True},
        {"summary_retention_status": "PASSED"},
        {"review_complete": True},
        {"disagreement_count": 1},
        {"incomplete_cases": ["assertion-1", "assertion-1"]},
        {"gates": []},
        {"reviewers": []},
    ],
)
def test_report_rejects_false_qualification_or_inconsistent_inventory(updates):
    value = packet()
    report = review.score(
        value, (review.pending_form(value, "alice"), review.pending_form(value, "bob"))
    )
    with pytest.raises(ValueError):
        changed(report, **updates)


def test_failure_and_abstention_stay_in_cohort_denominator():
    value = packet()
    first = label(review.pending_form(value, "alice"), "assertion-1", "supported")
    report = review.score(value, (first, review.pending_form(value, "bob")))
    extraction = next(
        counts
        for counts in report.reviewers[0].cohort_counts
        if counts.cohort == "untrusted_extracted_proposals"
    )
    assert extraction.total == extraction.denominator == 3
    assert extraction.numerator == 1
    assert extraction.support_rate == 1 / 3
    assert extraction.pending == 2
    assert extraction.generation_failures == extraction.abstentions == 1
    assert report.generation_failure_cases == ("failure-1",)
    assert report.abstention_cases == ("abstention-1",)
    assert {count.kind for count in report.reviewers[0].kind_counts} == {
        "assertion",
        "summary",
        "answer",
        "generation_failure",
        "extraction_abstention",
    }


def test_actual_adoption_cohort_is_separate_and_not_human_approval():
    value = packet(cases=(case(), case(case_id="adopted-1", cohort="adopted_assertions")))
    report = review.score(
        value, (review.pending_form(value, "alice"), review.pending_form(value, "bob"))
    )
    assert {count.cohort for count in report.reviewers[0].cohort_counts} == {
        "untrusted_extracted_proposals",
        "adopted_assertions",
    }
    assert not report.human_review_verified and not report.m2_qualified


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("packet_id", "different"),
        ("packet_digest", "0" * 64),
        ("source_digest", "0" * 64),
        ("rubric_version", "old-rubric"),
        ("human_review_verified", True),
        ("unknown_field", "ignored?"),
    ],
)
def test_form_rejects_stale_bindings_unknown_fields_and_false_verification(field, bad_value):
    value = packet()
    form = review.pending_form(value, "alice")
    with pytest.raises(ValueError):
        review.validate_form(value, changed(form, **{field: bad_value}))


@pytest.mark.parametrize("damage", ["missing", "extra", "unknown", "duplicate", "reordered"])
def test_case_inventory_must_be_exact(damage):
    value = packet()
    data = review.pending_form(value, "alice").model_dump(mode="json")
    if damage == "missing":
        data["ratings"].pop()
    elif damage == "extra":
        extra = dict(data["ratings"][0], case_id="extra-case")
        data["ratings"].append(extra)
    elif damage == "unknown":
        data["ratings"][0]["case_id"] = "unknown-case"
    elif damage == "duplicate":
        data["ratings"][1] = data["ratings"][0]
    else:
        data["ratings"].reverse()
    with pytest.raises(ValueError):
        review.validate_form(value, review.ReviewForm.model_validate_json(json.dumps(data)))


@pytest.mark.parametrize("field", ["source_digest", "text_sha256", "source_id"])
def test_source_binding_tampering_is_rejected(field):
    value = packet()
    data = review.pending_form(value, "alice").model_dump(mode="json")
    data["sources"][0][field] = "0" * 64
    with pytest.raises(ValueError):
        review.validate_form(value, review.ReviewForm.model_validate_json(json.dumps(data)))


@pytest.mark.parametrize("field", ["kind", "case_digest", "output_sha256"])
def test_case_binding_tampering_is_rejected(field):
    value = packet()
    data = review.pending_form(value, "alice").model_dump(mode="json")
    data["ratings"][0][field] = "summary" if field == "kind" else "0" * 64
    with pytest.raises(ValueError):
        review.validate_form(value, review.ReviewForm.model_validate_json(json.dumps(data)))


def test_duplicate_sources_and_cases_rejected():
    with pytest.raises(ValueError):
        packet(sources=(source(), source()))
    with pytest.raises(ValueError):
        packet(cases=(case(), case()))
    with pytest.raises(ValueError):
        case(source_ids=("source-1", "source-1"))
    with pytest.raises(ValueError):
        packet(cases=(case(source_ids=("unknown-source",)),))
    value = packet()
    form = review.pending_form(value, "alice")
    with pytest.raises(ValueError):
        changed(form, sources=[form.sources[0].model_dump()] * 2)


@pytest.mark.parametrize(
    ("case_id", "outcome"),
    [
        ("assertion-1", "faithful"),
        ("summary-1", "supported"),
        ("answer-1", "contradicted"),
        ("abstention-1", "supported"),
        ("failure-1", "supported"),
        ("assertion-1", "pending"),
        ("assertion-1", "gold_true"),
    ],
)
def test_invalid_outcome_for_kind_rejected(case_id, outcome):
    with pytest.raises(ValueError):
        label(review.pending_form(packet(), "alice"), case_id, outcome)


@pytest.mark.parametrize(
    "changes",
    [
        {"rationale": ""},
        {"rationale": " \n "},
        {"rationale": None},
        {"severity": None},
        {"severity": "trivial"},
        {"source_references": []},
        {"source_references": [{"source_id": "no-such-source"}]},
        {"source_references": [{"source_id": "source-1", "quote": "not in the source"}]},
        {"source_references": [{"source_id": "source-1"}] * 2},
    ],
)
def test_completed_labels_require_rationale_severity_and_known_evidence(changes):
    value = packet()
    data = label(review.pending_form(value, "alice"), "assertion-1", "supported").model_dump(
        mode="json"
    )
    data["ratings"][0].update(changes)
    with pytest.raises(ValueError):
        review.validate_form(value, review.ReviewForm.model_validate_json(json.dumps(data)))


def test_pending_label_cannot_smuggle_a_completed_rating():
    form = review.pending_form(packet(), "alice")
    with pytest.raises(ValueError):
        changed(form.ratings[0], severity="none")
    with pytest.raises(ValueError):
        changed(form.ratings[0], rationale="already evaluated")


def test_disagreement_and_uncertainty_are_visible_not_resolved():
    value = packet()
    first = complete_form(value, "alice")
    second = label(complete_form(value, "bob"), "assertion-1", "contradicted", "major")
    second = label(second, "summary-1", "uncertain", "minor")
    report = review.score(value, (first, second))
    assert report.disagreement_count == 2
    assert report.uncertain_cases == ("summary-1",)
    assert report.reviewers[1].uncertain == 1
    assert {item.case_id for item in report.disagreements} == {"assertion-1", "summary-1"}
    assert all(item.status == "requires_adjudication" for item in report.disagreements)
    assertion = report.disagreements[0]
    assert tuple(judgment.outcome for judgment in assertion.judgments) == (
        "supported",
        "contradicted",
    )
    assert not report.human_review_verified and not report.m2_qualified
    severity_only = label(first, "assertion-1", "supported", "critical")
    severity_only = changed(severity_only, reviewer_id="bob")
    assert review.score(value, (first, severity_only)).disagreement_count == 1


def test_two_distinct_self_declared_reviewers_required(tmp_path):
    value = packet()
    form = review.pending_form(value, "same")
    with pytest.raises(ValueError):
        review.score(value, (form, form))
    with pytest.raises(ValueError):
        review.score(value, (form,))
    with pytest.raises(ValueError):
        review.prepare(value, tmp_path / "new", ("same", "same"))
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize(
    "updates",
    [
        {"origin": "unknown"},
        {"origin": "public"},
        {"license": ""},
        {"attribution": "   "},
        {"modifications": "\n"},
        {"text_sha256": "0" * 64},
        {"source_url": "javascript:alert(1)"},
        {"source_url": "data:text/html,unsafe"},
        {"source_url": "https://name:secret@example.invalid"},
        {"source_url": "https://example.invalid/\nunsafe"},
        {"source_url": "https://example.invalid\\unsafe"},
        {"history_url": "file:///private"},
        {"origin": "public_wikipedia"},
        {"language": ""},
    ],
)
def test_unknown_or_incomplete_provenance_and_unsafe_links_rejected(updates):
    with pytest.raises(ValueError):
        source(**updates)


def test_public_provenance_is_bound_and_displayed_without_fetching():
    public = source(
        origin="public_wikipedia",
        source_url="https://en.wikipedia.org/w/index.php?title=Synthetic&oldid=123",
        history_url="https://en.wikipedia.org/w/index.php?title=Synthetic&action=history",
        revision="123 (synthetic provenance fixture)",
        modifications="Test-only invented payload, not real Wikipedia text.",
    )
    value = packet(sources=(public,))
    content = review.render_sources(value)
    assert "CC0-1.0" in content
    assert "synthetic provenance fixture" in content
    assert "Test-only invented payload" in content
    assert "&amp;oldid=123" in content
    changed_public = changed(public, attribution="Different attribution")
    assert changed_public.digest() != public.digest()
    assert packet(sources=(changed_public,)).digest() != value.digest()


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)


def test_all_source_model_and_metadata_html_is_escaped_and_source_view_blinded():
    attack = '</pre><script>alert("x")</script><img src=x onerror="alert(2)">'
    hostile_source = source(
        text=attack,
        title=attack,
        license=attack,
        attribution=attack,
        modifications=attack,
        revision=attack,
        source_url='https://example.invalid/?q="<script>"',
    )
    value = packet(
        sources=(hostile_source,),
        model_name=attack,
        recipe=attack,
        cases=(case(output="MODEL_OUTPUT_SENTINEL" + attack),),
    )
    source_html, output_html = review.render_sources(value), review.render_outputs(value)
    assert "MODEL_OUTPUT_SENTINEL" not in source_html
    assert "MODEL_OUTPUT_SENTINEL" in output_html
    for content in (source_html, output_html):
        assert "<script>" not in content and "<img " not in content
        assert "&lt;script&gt;" in content
        assert "Content-Security-Policy" in content
        assert "default-src 'none'" in content
        parsed = Tags()
        parsed.feed(content)
        assert not set(parsed.tags) & {"script", "img", "iframe", "object", "link", "style", "form"}
        assert not any(name.startswith("on") for name, _ in parsed.attributes)


def test_source_inventory_is_unfilled_and_grounding_validated_separately():
    value = packet()
    form = review.pending_source_form(value, "alice")
    assert form.annotations[0].status == "pending"
    assert form.annotations[0].important_claims == ()
    assert "output" not in form.model_dump_json()
    data = form.model_dump(mode="json")
    data["annotations"][0].update(
        status="complete",
        important_claims=[
            {
                "claim_id": "human-claim-1",
                "text": "Human-entered synthetic claim for this test.",
                "grounding": {"source_id": "source-1", "quote": "Mira planted three trees."},
            }
        ],
    )
    filled = review.SourceReviewForm.model_validate_json(json.dumps(data))
    assert review.validate_source_form(value, filled) == filled
    data["annotations"][0]["important_claims"][0]["grounding"]["quote"] = "not in source"
    with pytest.raises(ValueError):
        review.validate_source_form(
            value, review.SourceReviewForm.model_validate_json(json.dumps(data))
        )
    with pytest.raises(ValueError):
        review.ImportantClaim(
            claim_id="ungrounded",
            text="claim",
            grounding=review.SourceReference(source_id="source-1"),
        )
    with pytest.raises(ValueError):
        review.validate_source_form(value, changed(form, packet_digest="0" * 64))
    with pytest.raises(ValueError):
        changed(form, annotations=[form.annotations[0].model_dump()] * 2)
    with pytest.raises(ValueError):
        changed(
            filled.annotations[0],
            important_claims=[filled.annotations[0].important_claims[0].model_dump()] * 2,
        )


def test_unicode_span_uses_exact_codepoints_not_byte_offsets():
    text = "合成資料。木は三本です。"
    reference = review.SourceReference(source_id="source-1", start=5, end=12, quote=text[5:12])
    value = packet(
        sources=(source(text=text, language="ja"),),
        cases=(case(references=(reference,)),),
    )
    assert value.cases[0].references == (reference,)
    for bad in (
        {"start": 5},
        {"start": 5, "end": 4},
        {"start": 0, "end": 100},
        {"start": 0, "end": 2, "quote": "wrong"},
    ):
        with pytest.raises(ValueError):
            packet(cases=(case(references=(review.SourceReference(source_id="source-1", **bad),)),))


@pytest.mark.parametrize(
    "updates",
    [
        {"output": ""},
        {"cohort": "provider_answers"},
        {"kind": "generation_failure"},
        {"status": "failed"},
        {"error_code": "unexpected"},
        {"status": "abstained"},
        {"output_sha256": "0" * 64},
        {"kind": "extraction_abstention"},
        {"kind": "answer", "cohort": "provider_answers"},
    ],
)
def test_case_status_and_operational_failures_are_explicit(updates):
    with pytest.raises(ValueError):
        case(**updates)


def test_answer_abstention_is_not_silently_treated_as_an_answer():
    value = packet(
        cases=(
            case(
                case_id="answer-abstained",
                kind="answer",
                cohort="provider_answers",
                status="abstained",
                output="",
                question="What is unknown?",
            ),
        )
    )
    first = review.pending_form(value, "alice")
    with pytest.raises(ValueError):
        review.validate_form(value, label(first, "answer-abstained", "grounded_correct"))
    approved = label(first, "answer-abstained", "appropriate_abstention")
    assert review.validate_form(value, approved) == approved
    regular = packet()
    with pytest.raises(ValueError):
        review.validate_form(
            regular,
            label(review.pending_form(regular, "alice"), "answer-1", "appropriate_abstention"),
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"denominator": 1},
        {"numerator": 2},
        {"support_rate": 1.0},
        {"pending": 0},
        {"uncertain": 3},
        {"generation_failures": 4},
        {"total": True},
        {"outcomes": [{"outcome": "supported", "count": 1}] * 2},
    ],
)
def test_report_count_schema_rejects_inconsistent_denominators(updates):
    value = packet()
    first = label(review.pending_form(value, "alice"), "assertion-1", "supported")
    report = review.score(value, (first, review.pending_form(value, "bob")))
    counts = next(
        row
        for row in report.reviewers[0].cohort_counts
        if row.cohort == "untrusted_extracted_proposals"
    )
    with pytest.raises(ValueError):
        changed(counts, **updates)


def test_immutable_models_and_copy_update_bypass_revalidated_at_boundaries():
    value = packet()
    with pytest.raises(ValidationError):
        value.packet_id = "changed"
    with pytest.raises(TypeError):
        value.cases[0] = case()
    with pytest.raises(ValidationError):
        value.sources[0].text = "changed"
    altered = value.model_copy(update={"recipe": "tampered"})
    with pytest.raises(ValueError):
        review.pending_form(altered, "alice")
    form = review.pending_form(value, "alice")
    with pytest.raises(ValueError):
        review.score(value, (form.model_copy(update={"human_review_verified": True}), form))
    with pytest.raises(ValueError):
        changed(value, model_name="changed but kept old digest")
    data = value.model_dump(mode="json")
    data["sources"][0]["text"] += "changed"
    with pytest.raises(ValueError):
        review.ReviewPacket.model_validate_json(json.dumps(data))
    data = value.model_dump(mode="json")
    data["cases"][0]["output"] += "changed"
    with pytest.raises(ValueError):
        review.ReviewPacket.model_validate_json(json.dumps(data))


def test_prepare_private_new_only_roundtrip_and_no_csv(tmp_path):
    value = packet()
    out = tmp_path / "review"
    review.prepare(value, out, ("alice", "bob"))
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert {path.name for path in out.iterdir()} == {
        "packet.json",
        "sources.html",
        "outputs.html",
        "instructions.txt",
        "reviewer-1.json",
        "reviewer-2.json",
        "source-reviewer-1.json",
        "source-reviewer-2.json",
    }
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in out.iterdir())
    assert review.load_json(out / "packet.json", review.ReviewPacket) == value
    form = review.load_json(out / "reviewer-1.json", review.ReviewForm)
    assert review.validate_form(value, form) == form
    source_form = review.load_json(out / "source-reviewer-1.json", review.SourceReviewForm)
    assert review.validate_source_form(value, source_form) == source_form
    snapshot = {path.name: path.read_bytes() for path in out.iterdir()}
    with pytest.raises(FileExistsError):
        review.prepare(value, out, ("alice", "bob"))
    assert snapshot == {path.name: path.read_bytes() for path in out.iterdir()}


@pytest.mark.parametrize(
    "payload",
    [
        '{"packet_id":"one","packet_id":"two"}',
        '{"nested":{"same":1,"same":2}}',
        '{"unexpected":NaN}',
        '{"unexpected":Infinity}',
        '{"schema_version":"future-version"}',
    ],
)
def test_strict_json_loading_rejects_duplicate_keys_nonfinite_and_unknown_schema(tmp_path, payload):
    path = tmp_path / "input.json"
    path.write_text(payload)
    with pytest.raises(ValueError):
        review.load_json(path, review.ReviewPacket)


def test_input_and_output_bounds_and_nonregular_inputs(tmp_path, monkeypatch):
    value = packet()
    with pytest.raises(ValueError):
        source(text="x" * 32769)
    with pytest.raises(ValueError):
        case(output="x" * 16385)
    with pytest.raises(ValueError):
        packet(cases=tuple(case(case_id=f"case-{index}") for index in range(review.MAX_CASES + 1)))
    with pytest.raises(ValueError):
        packet(
            sources=tuple(source(source_id=f"s-{index}") for index in range(review.MAX_SOURCES + 1))
        )
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (review.MAX_JSON_BYTES + 1))
    with pytest.raises(ValueError):
        review.load_json(path, review.ReviewPacket)
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ValueError):
        review.load_json(path, review.ReviewPacket)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        review.load_json(link, review.ReviewPacket)
    monkeypatch.setattr(review, "MAX_OUTPUT_BYTES", 100)
    with pytest.raises(ValueError):
        review.prepare(value, tmp_path / "too-large", ("alice", "bob"))
    assert not (tmp_path / "too-large").exists()
    monkeypatch.setattr(review, "MAX_JSON_BYTES", 500)
    with pytest.raises(ValueError):
        packet()


def test_json_forms_and_fully_escaped_html_share_an_aggregate_output_limit(tmp_path, monkeypatch):
    value = packet()
    form = review.pending_form(value, "alice")
    assert len(review._json_bytes(form)) > 100
    monkeypatch.setattr(review, "MAX_JSON_BYTES", 100)
    with pytest.raises(ValueError):
        review._json_bytes(form)
    with pytest.raises(ValueError):
        review.validate_form(value, form)


def test_cli_prepare_score_validate_source_and_sanitized_errors(tmp_path, capsys):
    value = packet()
    input_path = tmp_path / "input.json"
    input_path.write_text(value.model_dump_json())
    out = tmp_path / "prepared"
    assert (
        review.main(
            [
                "prepare",
                "--packet",
                str(input_path),
                "--reviewer",
                "alice",
                "--reviewer",
                "bob",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    report_dir = tmp_path / "report"
    assert (
        review.main(
            [
                "score",
                "--packet",
                str(out / "packet.json"),
                "--form",
                str(out / "reviewer-1.json"),
                "--form",
                str(out / "reviewer-2.json"),
                "--out",
                str(report_dir),
            ]
        )
        == 0
    )
    report = review.load_json(report_dir / "report.json", review.ReviewReport)
    assert not report.m2_qualified and not report.review_complete
    assert (report_dir / "report.html").exists()
    validated_dir = tmp_path / "validated"
    assert (
        review.main(
            [
                "validate-source",
                "--packet",
                str(out / "packet.json"),
                "--form",
                str(out / "source-reviewer-1.json"),
                "--out",
                str(validated_dir),
            ]
        )
        == 0
    )
    assert (validated_dir / "source-review.json").exists()
    input_path.write_text('{"private_secret":"DO_NOT_LEAK_INPUT"}')
    assert (
        review.main(
            [
                "prepare",
                "--packet",
                str(input_path),
                "--reviewer",
                "alice",
                "--reviewer",
                "bob",
                "--out",
                str(tmp_path / "bad"),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "DO_NOT_LEAK_INPUT" not in captured.err
    assert str(input_path) not in captured.err
    assert "no review or qualification was verified" in captured.err
    assert not (tmp_path / "bad").exists()


def test_module_cli_help_without_network_model_or_database():
    result = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.human_review", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "prepare" in result.stdout and "validate-source" in result.stdout
    assert not result.stderr
