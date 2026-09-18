import asyncio
import hashlib
import json
import os
import sys

import pytest
from pydantic import ValidationError

from pg_agmemory.evaluation_wikipedia import (
    MODIFICATIONS,
    WikipediaCorpus,
    WikipediaSource,
    attribution,
    source_id,
    source_urls,
)
from pg_agmemory.human_review import load_json, pending_form, prepare, score
from pg_agmemory.providers import HTTPProvider, ProviderFailure, ProviderSettings
from pg_agmemory.review_pilot import (
    PilotMeasurements,
    corpus_excerpts,
    excerpt,
    main,
    measure,
    review_packet,
)


def corpus():
    text = "Orion / preferred_editor: Vim.\n\nSynthetic fixture, not downloaded Wikipedia."
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    article, revision, history = source_urls("en", "Synthetic fixture", 123)
    return WikipediaCorpus(sources=[WikipediaSource(
        source_id=source_id("en", 1, 123, text_hash),
        language="en", page_id=1, title="Synthetic fixture",
        article_url=article, revision_id=123,
        revision_timestamp="2026-09-01T00:00:00Z", revision_url=revision, history_url=history,
        attribution=attribution("Synthetic fixture", revision, history),
        retrieved_at="2026-09-02T00:00:00Z", html_sha256="a" * 64,
        text_sha256=text_hash, text=text, modifications=list(MODIFICATIONS),
    )])


def settings():
    return ProviderSettings(
        backend="local_http", endpoint="http://127.0.0.1:11435/v1",
        text_model={"name": "synthetic-text", "revision": "v1"},
        embedding_model={"name": "synthetic-embedding", "revision": "v1"},
        max_output_tokens=512,
    )


def wire(content):
    return {"choices": [{
        "finish_reason": "stop", "message": {"role": "assistant", "content": content},
    }]}


def fake_exchange(monkeypatch, *, extraction_failure=False, empty=False, oversized_summary=False):
    async def exchange(self, path, payload):
        assert path == "chat/completions"
        assert len(payload["messages"]) == 2
        schema = payload.get("response_format", {}).get("json_schema", {}).get("name")
        if schema == "memory_extraction_candidates":
            if extraction_failure:
                return wire('{"candidates":[{"invented":true}]}')
            return wire(json.dumps({"candidates": [] if empty else [{
                "subject": "Orion", "predicate": "preferred_editor", "value": "Vim",
                "evidence_quote": payload["messages"][1]["content"],
            }]}))
        if schema == "memory_evaluation_answer":
            data = json.loads(payload["messages"][1]["content"])
            assert set(data) == {"question", "evidence"}
            assert "gold" not in data and "answer" not in data
            if "next revision" in data["question"]:
                return wire('{"answer":"","abstained":true,"citations":[]}')
            return wire(json.dumps({
                "answer": "Vim", "abstained": False,
                "citations": [data["evidence"][0]["source_id"]],
            }))
        return wire("x" * 16385 if oversized_summary else "Orion prefers Vim.")
    monkeypatch.setattr(HTTPProvider, "exchange", exchange)


def run(tmp_path, monkeypatch, **fake_options):
    data = corpus()
    fake_exchange(monkeypatch, **fake_options)
    output = tmp_path / "pilot"
    measured = asyncio.run(measure(
        corpus_excerpts(data), settings(), corpus_digest=data.digest(),
        implementation_sha="b" * 40, output=output, max_calls=4,
    ))
    return data, measured, output


def test_excerpt_preserves_original_unicode_slice_and_first_paragraph():
    text = "\n\n\u65e5\u672c\u8a9e\u3002\n\nLater."
    result = excerpt(
        source_id="original", language="ja", title="Synthetic",
        revision_timestamp="2026-09-01T00:00:00Z", text=text,
    )
    assert result.text == text[result.start:result.end] == "\u65e5\u672c\u8a9e\u3002"
    assert result.parent_text_sha256 == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("text", [" ", "x" * 1601 + "\n\nShort.", "\u65e5" * 534])
def test_excerpt_does_not_skip_oversized_first_paragraph_or_cut_utf8(text):
    with pytest.raises(ValueError):
        excerpt(
            source_id="original", language="en", title="Synthetic",
            revision_timestamp="2026-09-01T00:00:00Z", text=text,
        )


@pytest.mark.parametrize("maximum", [0, 3, 81, True])
def test_insufficient_or_invalid_budget_rejects_before_artifacts(tmp_path, maximum):
    data = corpus()
    with pytest.raises(ValueError):
        asyncio.run(measure(
            corpus_excerpts(data), settings(), corpus_digest=data.digest(),
            implementation_sha="b" * 40, output=tmp_path / "pilot", max_calls=maximum,
        ))
    assert not (tmp_path / "pilot").exists()


def test_real_adapter_paths_make_unrated_bound_packet(tmp_path, monkeypatch):
    data, measured, output = run(tmp_path, monkeypatch)
    assert measured.calls == 4 and not measured.m2_qualified
    packet = review_packet(data, measured)
    assert [case.kind for case in packet.cases] == ["assertion", "summary", "answer", "answer"]
    assert packet.cases[-1].status == "abstained"
    assert all(row.human_review == "not_reviewed" for row in measured.observations)
    assert packet.sources[0].source_url == data.sources[0].revision_url
    assert data.sources[0].text_sha256 in packet.sources[0].modifications
    assert packet.sources[0].license == "CC-BY-SA-4.0"
    prepare(packet, output / "review", ("reviewer-a", "reviewer-b"))
    assert "Orion prefers Vim." not in (output / "review/sources.html").read_text()
    assert "Orion prefers Vim." in (output / "review/outputs.html").read_text()
    forms = (pending_form(packet, "reviewer-a"), pending_form(packet, "reviewer-b"))
    assert all(rating.outcome is None for form in forms for rating in form.ratings)
    report = score(packet, forms)
    assert not report.m2_qualified
    journal = [json.loads(line) for line in (output / "journal.jsonl").read_text().splitlines()]
    assert sum(row["event"] == "call_reserved" for row in journal) == 4
    assert sum(row["event"] == "provider_response" for row in journal) == 4
    assert journal[0]["planned_calls"] == 4


@pytest.mark.parametrize("option,index,kind", [
    ({"extraction_failure": True}, 0, "generation_failure"),
    ({"empty": True}, 0, "extraction_abstention"),
    ({"oversized_summary": True}, 1, "generation_failure"),
])
def test_failures_and_empty_extraction_remain_in_packet(tmp_path, monkeypatch, option, index, kind):
    data, measured, _ = run(tmp_path, monkeypatch, **option)
    packet = review_packet(data, measured)
    assert measured.calls == 4 and len(packet.cases) == 4
    assert packet.cases[index].kind == kind
    assert not packet.human_review_verified


def test_incomplete_duplicate_and_wrongly_bound_measurements_are_rejected(tmp_path, monkeypatch):
    data, measured, _ = run(tmp_path, monkeypatch)
    original = measured.model_dump(mode="json")
    for change in (
        {"observations": original["observations"][:-1]},
        {"observations": original["observations"] + original["observations"][:1]},
        {"calls": 3},
    ):
        with pytest.raises(ValidationError):
            PilotMeasurements.model_validate(original | change)
    raw = data.model_dump(mode="json")
    raw["sources"][0]["retrieved_at"] = "2026-09-03T00:00:00Z"
    changed = WikipediaCorpus.model_validate(raw)
    with pytest.raises(ValueError, match="corpus"):
        review_packet(changed, measured)


def test_transport_failure_stops_without_retry_and_preserves_journal(tmp_path, monkeypatch):
    calls = []

    async def fail(self, path, payload):
        calls.append(path)
        raise ProviderFailure("provider_unavailable", unknown=True)

    monkeypatch.setattr(HTTPProvider, "exchange", fail)
    data = corpus()
    output = tmp_path / "pilot"
    with pytest.raises(ProviderFailure, match="provider_unavailable"):
        asyncio.run(measure(
            corpus_excerpts(data), settings(), corpus_digest=data.digest(),
            implementation_sha="b" * 40, output=output, max_calls=4,
        ))
    assert len(calls) == 1 and not (output / "measurements.json").exists()
    rows = [json.loads(line) for line in (output / "journal.jsonl").read_text().splitlines()]
    assert rows[-1]["event"] == "pilot_aborted"
    with pytest.raises(FileExistsError):
        asyncio.run(measure(
            corpus_excerpts(data), settings(), corpus_digest=data.digest(),
            implementation_sha="b" * 40, output=output, max_calls=4,
        ))
    assert len(calls) == 1


def test_cli_requires_explicit_call_permission_and_prepares_pending_forms(tmp_path, monkeypatch):
    data = corpus()
    (tmp_path / "corpus.json").write_text(data.model_dump_json())
    (tmp_path / "profile.json").write_text(settings().model_dump_json())
    args = [
        "review_pilot", "--corpus", str(tmp_path / "corpus.json"),
        "--profile", str(tmp_path / "profile.json"), "--implementation-sha", "b" * 40,
        "--output", str(tmp_path / "pilot"), "--max-calls", "4",
        "--reviewer", "reviewer-a", "--reviewer", "reviewer-b",
    ]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2 and not (tmp_path / "pilot").exists()
    fake_exchange(monkeypatch)
    monkeypatch.setattr(sys, "argv", args + ["--allow-local-model-calls"])
    main()
    report = json.loads((tmp_path / "pilot/readiness.json").read_text())
    assert report["review_complete"] is False and report["m2_qualified"] is False
    assert len(report["incomplete_cases"]) == 4
    assert (tmp_path / "pilot/review/source-reviewer-2.json").is_file()


def test_profile_loading_rejects_nonregular_oversized_and_duplicate_key_inputs(tmp_path):
    path = tmp_path / "profile.json"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular"):
        load_json(path, ProviderSettings, max_bytes=32768)
    path.unlink()
    path.write_text(settings().model_dump_json())
    with pytest.raises(ValueError, match="bounded"):
        load_json(path, ProviderSettings, max_bytes=8)
    path.write_text('{"backend":"local_http","backend":"openai_compatible"}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_json(path, ProviderSettings, max_bytes=32768)
