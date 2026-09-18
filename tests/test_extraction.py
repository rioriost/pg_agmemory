"""Synthetic extraction contracts only: no model downloads or paid inference."""

import asyncio
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from pg_agmemory.inference import main, run
from pg_agmemory.providers import (
    EXTRACTION_SYSTEM_PROMPT,
    MAX_INFERENCE_BYTES,
    MAX_PROVIDER_RESPONSE_BYTES,
    ExtractionCandidate,
    ExtractionCandidates,
    ExtractionResult,
    HTTPProvider,
    InferenceInput,
    ProviderFailure,
    ProviderSettings,
    TextModel,
    configured_operations,
    extraction_schema,
    parse_extraction,
)

SOURCE = " \n🔒東京は承認していない。確実ではない。\ne\u0301 is not é.\n "
QUOTE = "東京は承認していない。確実ではない。"
MODEL = TextModel(name="synthetic-extraction", revision="pinned-1")


def configuration(**changes):
    return ProviderSettings.model_validate(
        {
            "backend": "local_http",
            "endpoint": "http://127.0.0.1:11434/v1",
            "text_model": MODEL.model_dump(),
            **changes,
        }
    )


def candidate(**changes):
    return {
        "subject": "東京",
        "predicate": "approval",
        "value": "承認していない",
        "evidence_quote": QUOTE,
        "start": SOURCE.index(QUOTE),
        "end": SOURCE.index(QUOTE) + len(QUOTE),
        **changes,
    }


def reply(payload):
    return {
        "model": "unverified-upstream-alias",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload),
                },
            }
        ],
    }


class ResponseBytes(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw = raw

    async def __aiter__(self):
        yield self.raw


def mock_http(monkeypatch, handler):
    calls, clients = [], []

    def client(settings):
        async def exchange(request):
            calls.append(request)
            response = handler(request)
            if isinstance(response, httpx.Response):
                return response
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=ResponseBytes(json.dumps(response).encode()),
            )

        result = httpx.AsyncClient(
            base_url=settings.endpoint.rstrip("/") + "/",
            transport=httpx.MockTransport(exchange),
            trust_env=False,
            follow_redirects=False,
        )
        clients.append(result)
        return result

    monkeypatch.setattr(ProviderSettings, "client", client)
    return calls, clients


def assert_invalid(failure):
    assert failure.value.error.model_dump() == {
        "code": "invalid_provider_response",
        "retryable": False,
        "billing_unknown": True,
    }
    assert str(failure.value) == "invalid_provider_response"


@pytest.mark.parametrize(
    "backend,endpoint",
    [
        ("local_http", "http://127.0.0.1:11434/v1"),
        ("openai_compatible", "https://synthetic.invalid/v1"),
    ],
)
@pytest.mark.parametrize("max_tokens", [None, 123])
def test_http_extract_uses_schema_raw_source_and_operator_model_identity(
    monkeypatch, backend, endpoint, max_tokens
):
    def handler(request):
        assert str(request.url) == endpoint + "/chat/completions"
        payload = json.loads(request.content)
        assert set(payload) == {"model", "messages", "response_format", "max_tokens", "stream"}
        assert payload["model"] == MODEL.name
        assert payload["messages"] == [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": SOURCE},
        ]
        assert payload["response_format"] == {
            "type": "json_schema",
            "json_schema": extraction_schema(),
        }
        assert payload["max_tokens"] == (max_tokens or 4096) and payload["stream"] is False
        assert "tools" not in payload and "Idempotency-Key" not in request.headers
        return reply({"candidates": [candidate()]})

    config = configuration(backend=backend, endpoint=endpoint, max_output_tokens=max_tokens)
    calls, clients = mock_http(monkeypatch, handler)
    data = InferenceInput(text=SOURCE)
    result = asyncio.run(HTTPProvider(config).extract(data))
    assert result.model_dump() == {
        "model": MODEL.model_dump(),
        "input_digest": hashlib.sha256(SOURCE.encode()).hexdigest(),
        "candidates": [candidate()],
        "status": "untrusted",
    }
    assert data.text == SOURCE and len(calls) == 1 and clients[0].is_closed
    assert result.candidates[0].start == 3
    assert result.candidates[0].start != len(SOURCE[:3].encode("utf-8"))
    assert result.candidates[0].start != len(SOURCE[:3].encode("utf-16-le")) // 2


def test_generated_schema_is_closed_bounded_and_contains_no_authority_fields():
    schema = extraction_schema()
    assert schema["strict"] is True
    root = schema["schema"]
    assert root["additionalProperties"] is False and root["required"] == ["candidates"]
    assert root["properties"]["candidates"]["maxItems"] == 16
    item = root["$defs"]["ExtractionCandidate"]
    fields = {"subject", "predicate", "value", "evidence_quote", "start", "end"}
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == fields and set(item["required"]) == fields
    assert item["properties"]["subject"]["maxLength"] == 256
    assert item["properties"]["predicate"]["pattern"] == r"^[a-z][a-z0-9_]{0,63}$"
    assert item["properties"]["value"]["maxLength"] == 4096
    assert item["properties"]["evidence_quote"]["maxLength"] == 4096
    assert item["properties"]["start"]["type"] == "integer"
    assert item["properties"]["end"]["type"] == "integer"
    assert "negation" in EXTRACTION_SYSTEM_PROMPT and "uncertainty" in EXTRACTION_SYSTEM_PROMPT
    assert "not authoritative facts" in EXTRACTION_SYSTEM_PROMPT
    assert "never publish memory" in EXTRACTION_SYSTEM_PROMPT


def test_whitespace_codepoints_and_negation_are_preserved_not_normalized():
    data = InferenceInput(text=SOURCE)
    payload = {
        "candidates": [
            candidate(evidence_quote=SOURCE, start=0, end=len(SOURCE)),
            {
                "subject": "e\u0301",
                "predicate": "identity",
                "value": "not é",
                "evidence_quote": "e\u0301 is not é.",
                "start": SOURCE.index("e\u0301"),
                "end": SOURCE.index("e\u0301") + len("e\u0301 is not é."),
            },
        ]
    }
    result = parse_extraction(payload, data, MODEL)
    assert result.candidates[0].evidence_quote == SOURCE
    assert result.candidates[0].value == "承認していない"
    assert result.candidates[1].subject == "e\u0301"
    assert result.candidates[1].value == "not é"
    assert result.input_digest != InferenceInput(text=SOURCE.strip()).digest()


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": ""},
        {"subject": " "},
        {"subject": "京都"},
        {"subject": "\ud800"},
        {"subject": "x" * 257},
        {"predicate": "Approval"},
        {"predicate": "approval "},
        {"predicate": "承認"},
        {"predicate": "x" * 65},
        {"predicate": "\udfff"},
        {"value": ""},
        {"value": " "},
        {"value": "Approved PRIVATE"},
        {"value": "\ud800"},
        {"value": "x" * 4097},
        {"evidence_quote": ""},
        {"evidence_quote": " "},
        {"evidence_quote": "\udfff"},
        {"evidence_quote": "x" * 4097},
        {"evidence_quote": QUOTE.replace("東京", "京都")},
        {"start": -1},
        {"start": True},
        {"start": 3.0},
        {"start": "3"},
        {"start": 65536},
        {"end": 0},
        {"end": True},
        {"end": "21"},
        {"end": 65537},
        {"start": 5, "end": 4},
        {"start": 3, "end": 3},
        {"start": 4, "end": 4 + len(QUOTE)},
        {"start": 200, "end": 200 + len(QUOTE)},
        {"subject": 12},
        {"value": None},
        {"evidence_quote": ["東京"]},
    ],
)
def test_http_extract_rejects_invalid_types_unicode_limits_and_grounding(monkeypatch, changes):
    calls, _ = mock_http(
        monkeypatch, lambda request: reply({"candidates": [candidate(**changes)]})
    )
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert_invalid(failure)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field",
    [
        "memory_id",
        "source_id",
        "scope_id",
        "acl",
        "permissions",
        "approval",
        "explicit_intent",
        "time",
        "valid_from",
        "confidence",
        "model",
        "input_digest",
        "status",
    ],
)
def test_candidate_cannot_supply_authority_fields(monkeypatch, field):
    calls, _ = mock_http(
        monkeypatch, lambda request: reply({"candidates": [candidate(**{field: "PRIVATE"})]})
    )
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert_invalid(failure)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"candidates": None},
        {"candidates": {}},
        {"candidates": [None]},
        {"candidates": [{}]},
        {"candidates": [], "model": MODEL.model_dump()},
        {"candidates": [], "input_digest": "0" * 64},
        {"candidates": [], "status": "trusted"},
        {"candidates": [candidate(), candidate()]},
        {"candidates": [candidate(predicate=f"p{index}") for index in range(17)]},
    ],
)
def test_http_extract_rejects_nonclosed_duplicate_and_overlimit_outputs(monkeypatch, payload):
    calls, _ = mock_http(monkeypatch, lambda request: reply(payload))
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert_invalid(failure)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        "",
        "PRIVATE",
        "```json\n{\"candidates\":[]}\n```",
        '{"candidates":[],"candidates":[]}',
        '{"candidates":NaN}',
        '{"candidates":Infinity}',
        '{"candidates":-Infinity}',
        '{"candidates":[] } trailing',
        '{"candidates":[' + '{"subject":"東京","subject":"東京"}' + "]}",
        "[" * 2000 + "]" * 2000,
    ],
)
def test_http_extract_rejects_malformed_or_ambiguous_json(monkeypatch, content):
    response = reply({"candidates": []})
    response["choices"][0]["message"]["content"] = content
    calls, _ = mock_http(monkeypatch, lambda request: response)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert_invalid(failure)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"finish_reason": "length"},
        {"finish_reason": "tool_calls"},
        {"finish_reason": "content_filter"},
        {"message": {"role": "user", "content": '{"candidates":[]}'}},
        {"message": {"role": "assistant", "content": None}},
        {"message": {"role": "assistant", "content": {"candidates": []}}},
        {"message": {"role": "assistant", "content": []}},
        {"message": {"role": "assistant", "content": '{"candidates":[]}', "refusal": "PRIVATE"}},
        {
            "message": {
                "role": "assistant",
                "content": '{"candidates":[]}',
                "tool_calls": [{"id": "PRIVATE"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": '{"candidates":[]}',
                "function_call": {"name": "PRIVATE"},
            }
        },
    ],
)
def test_http_extract_rejects_truncation_refusal_and_tool_responses(monkeypatch, change):
    response = reply({"candidates": []})
    response["choices"][0].update(change)
    calls, _ = mock_http(monkeypatch, lambda request: response)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert_invalid(failure)
    assert len(calls) == 1


@pytest.mark.parametrize("choices", [None, [], [{}, {}], [None], ["PRIVATE"], [{}]])
def test_http_extract_requires_exactly_one_well_formed_choice(monkeypatch, choices):
    calls, _ = mock_http(monkeypatch, lambda request: {"choices": choices})
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert_invalid(failure)
    assert len(calls) == 1


@pytest.mark.parametrize("count", [0, 16])
def test_abstention_and_maximum_candidates_preserve_order(monkeypatch, count):
    candidates = [candidate(predicate=f"p{index}") for index in range(count)]
    calls, _ = mock_http(monkeypatch, lambda request: reply({"candidates": candidates}))
    result = asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert [item.model_dump() for item in result.candidates] == candidates
    assert result.status == "untrusted" and len(calls) == 1


def test_candidate_character_limits_and_original_source_last_offset():
    source = " " * (65536 - 4096) + "界" * 4096
    data = InferenceInput(text=source)
    item = {
        "subject": "界" * 256,
        "predicate": "p" * 64,
        "value": "界" * 4096,
        "evidence_quote": "界" * 4096,
        "start": 65536 - 4096,
        "end": 65536,
    }
    result = parse_extraction({"candidates": [item]}, data, MODEL)
    assert result.candidates[0].model_dump() == item
    last = {**item, "subject": "界", "value": "界", "evidence_quote": "界", "start": 65535}
    assert parse_extraction({"candidates": [last]}, data, MODEL).candidates[0].end == 65536


def test_offset_and_unicode_normalization_cannot_be_substituted():
    data = InferenceInput(text=SOURCE)
    for offset in (len(SOURCE[:3].encode()), len(SOURCE[:3].encode("utf-16-le")) // 2):
        with pytest.raises(ProviderFailure) as failure:
            parse_extraction(
                {"candidates": [candidate(start=offset, end=offset + len(QUOTE))]}, data, MODEL
            )
        assert_invalid(failure)
    quote = "é is not é."
    start = SOURCE.index("e\u0301")
    item = {
        "subject": "é",
        "predicate": "identity",
        "value": "not é",
        "evidence_quote": quote,
        "start": start,
        "end": start + len(quote),
    }
    with pytest.raises(ProviderFailure) as failure:
        parse_extraction({"candidates": [item]}, data, MODEL)
    assert_invalid(failure)


def test_lexical_grounding_does_not_claim_semantic_support():
    item = candidate(value="承認")
    result = parse_extraction({"candidates": [item]}, InferenceInput(text=SOURCE), MODEL)
    assert result.candidates[0].value == "承認" and result.status == "untrusted"


def test_public_models_are_closed_and_duplicate_contract_is_rejection():
    item = ExtractionCandidate.model_validate(candidate())
    assert ExtractionCandidates(candidates=[]).candidates == []
    with pytest.raises(ValidationError):
        ExtractionCandidates(candidates=[item, item])
    with pytest.raises(ValidationError):
        ExtractionCandidates.model_validate({"candidates": [], "scope_id": "PRIVATE"})
    with pytest.raises(ValidationError):
        ExtractionCandidate.model_validate({**candidate(), "start": True})
    result = ExtractionResult(model=MODEL, input_digest="a" * 64, candidates=[item])
    assert result.status == "untrusted"
    for changes in ({"status": "approved"}, {"input_digest": "PRIVATE"}, {"scope_id": "PRIVATE"}):
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate(result.model_dump() | changes)


def test_result_binds_the_source_and_model_sent_even_if_caller_mutates_them(monkeypatch):
    data = InferenceInput(text=SOURCE)
    config = configuration()

    def handler(request):
        assert json.loads(request.content)["messages"][1]["content"] == SOURCE
        data.text = "Changed after sending"
        config.text_model.name = "changed-model"
        config.text_model.revision = "changed-revision"
        return reply({"candidates": [candidate()]})

    mock_http(monkeypatch, handler)
    result = asyncio.run(HTTPProvider(config).extract(data))
    assert result.model == MODEL
    assert result.input_digest == hashlib.sha256(SOURCE.encode()).hexdigest()


@pytest.mark.parametrize(
    "outcome,code,retryable,unknown",
    [
        ("lost", "provider_unavailable", True, True),
        ("timeout", "provider_unavailable", True, True),
        ("oversize", "invalid_provider_response", False, True),
        ("compressed", "invalid_provider_response", False, True),
        ("text", "invalid_provider_response", False, True),
        ("400", "provider_request_failed", False, False),
        ("429", "provider_request_failed", True, False),
        ("503", "provider_request_failed", True, True),
        ("307", "provider_request_failed", False, False),
    ],
)
def test_extract_transport_retains_bounds_billing_and_no_retry_or_fallback(
    monkeypatch, outcome, code, retryable, unknown
):
    def handler(request):
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE upstream")
        if outcome == "timeout":
            raise TimeoutError("PRIVATE upstream")
        headers = {"Content-Type": "application/json", "Location": "https://private.invalid"}
        if outcome == "compressed":
            headers["Content-Encoding"] = "gzip"
        if outcome == "text":
            headers["Content-Type"] = "text/plain"
        raw = (
            b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1)
            if outcome == "oversize"
            else json.dumps(reply({"candidates": []})).encode()
        )
        return httpx.Response(
            int(outcome) if outcome.isdigit() else 200,
            headers=headers,
            stream=ResponseBytes(raw),
        )

    calls, clients = mock_http(monkeypatch, handler)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(InferenceInput(text=SOURCE)))
    assert failure.value.error.model_dump() == {
        "code": code,
        "retryable": retryable,
        "billing_unknown": unknown,
    }
    assert str(failure.value) == code and len(calls) == 1 and clients[0].is_closed


def test_http_extraction_request_size_guard_precedes_client_creation(monkeypatch):
    calls, clients = mock_http(monkeypatch, lambda request: pytest.fail("Unexpected inference"))
    source = InferenceInput(text="🔒" * 65536)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).extract(source))
    assert failure.value.error.code == "inference_input_too_large"
    assert failure.value.error.billing_unknown is False and calls == [] and clients == []


@pytest.mark.parametrize(
    "raw",
    [
        b"PRIVATE",
        b'{"text":" "}',
        b'{"text":"\\ud800"}',
        b'{"text":"ok","scope_id":"PRIVATE"}',
        json.dumps({"text": "x" * 65537}).encode(),
        b"x" * (MAX_INFERENCE_BYTES + 1),
    ],
)
def test_extract_cli_run_rejects_invalid_input_before_network(monkeypatch, raw):
    calls, clients = mock_http(monkeypatch, lambda request: pytest.fail("Unexpected inference"))
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(run(configuration(), "extract", raw))
    assert failure.value.error.code in ("invalid_inference_input", "inference_input_too_large")
    assert failure.value.error.billing_unknown is False and calls == [] and clients == []


def test_operator_extract_cli_outputs_typed_untrusted_envelope(monkeypatch, capsys):
    calls, _ = mock_http(monkeypatch, lambda request: reply({"candidates": [candidate()]}))
    config = configuration().model_dump_json().encode()
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.BytesIO(config))
    monkeypatch.setattr(
        "pg_agmemory.inference.sys.stdin",
        SimpleNamespace(buffer=io.BytesIO(json.dumps({"text": SOURCE}).encode())),
    )
    main(["extract", "--config", "synthetic-profile.json"])
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == "" and result["status"] == "ok" and result["error"] is None
    assert result["result"]["candidates"] == [candidate()]
    assert result["result"]["status"] == "untrusted"
    assert result["result"]["model"] == MODEL.model_dump()
    assert result["result"]["input_digest"] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert len(calls) == 1


def test_http_capability_listing_is_additive_and_independent_of_embeddings():
    assert configured_operations(configuration()) == ["summarize", "extract"]
    config = configuration(
        text_model=None,
        embedding_model={"name": "synthetic-embedding", "revision": "1"},
    )
    assert configured_operations(config) == ["embed"]


def test_same_quote_at_distinct_offsets_is_not_an_identical_duplicate():
    text = QUOTE + QUOTE
    first = candidate(start=0, end=len(QUOTE))
    second = deepcopy(first)
    second.update(start=len(QUOTE), end=2 * len(QUOTE))
    result = parse_extraction({"candidates": [first, second]}, InferenceInput(text=text), MODEL)
    assert len(result.candidates) == 2
