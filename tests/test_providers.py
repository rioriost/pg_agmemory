import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from pg_agmemory.inference import run
from pg_agmemory.models import EmbeddingModel, Explain, Forget, PutEmbedding, Recall, VectorQuery
from pg_agmemory.providers import (
    MAX_INFERENCE_BYTES,
    MAX_PROVIDER_RESPONSE_BYTES,
    HTTPProvider,
    InferenceInput,
    ProviderFailure,
    ProviderSettings,
    TextModel,
    parse_input,
    parse_settings,
)
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


def configuration(**changes):
    return ProviderSettings.model_validate(
        {
            "backend": "local_http",
            "endpoint": "http://127.0.0.1:11434/v1",
            "text_model": {"name": "synthetic-summary", "revision": "1"},
            "embedding_model": {"name": "synthetic-embedding", "revision": "1"},
            **changes,
        }
    )


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


def upstream(body, status=200, **headers):
    return httpx.Response(
        status,
        headers={"Content-Type": "application/json", **headers},
        stream=Chunks([json.dumps(body).encode()]),
    )


def mock_http(monkeypatch, handler):
    calls, clients = [], []

    def client(settings):
        async def exchange(request):
            calls.append(request)
            return handler(request)

        headers = {}
        if settings.api_key_env:
            headers["Authorization"] = "Bearer " + settings.secret(settings.api_key_env)
        result = httpx.AsyncClient(
            base_url=settings.endpoint.rstrip("/") + "/",
            headers=headers,
            transport=httpx.MockTransport(exchange),
            trust_env=False,
            follow_redirects=False,
        )
        clients.append(result)
        return result

    monkeypatch.setattr(ProviderSettings, "client", client)
    return calls, clients


@pytest.mark.parametrize(
    "changes",
    [
        {"backend": "unknown"},
        {"endpoint": "http://remote.example/v1"},
        {"endpoint": "http://localhost.evil.example/v1"},
        {"endpoint": "http://127.0.0.1:11434/v1?key=SECRET"},
        {"endpoint": "http://name:SECRET@127.0.0.1:11434/v1"},
        {"endpoint": "http://127.0.0.1:11434/v1#SECRET"},
        {"endpoint": "http://127.0.0.1:11434/v1/../other"},
        {"endpoint": "http://127.0.0.1:11434/%2e%2e"},
        {"endpoint": " http://127.0.0.1:11434/v1"},
        {"endpoint": "http://127.0.0.1:99999/v1"},
        {"backend": "openai_compatible", "endpoint": "http://localhost/v1"},
        {"database_url_env": "PGAG_SQL_DSN"},
        {"azure_product": "horizondb"},
        {"text_model": None, "embedding_model": None},
        {"timeout_seconds": 0},
        {"timeout_seconds": 121},
        {"timeout_seconds": True},
        {"max_output_tokens": 0},
        {"max_output_tokens": 4097},
        {"max_output_tokens": 10, "text_model": None},
        {"sentence_count": 5},
        {"api_key_env": "raw-token-with-dashes"},
        {"api_key": "SECRET"},
    ],
)
def test_provider_configuration_rejects_unsafe_or_unsupported_settings(changes):
    with pytest.raises(ValidationError):
        configuration(**changes)


def test_provider_configuration_separates_credentials_and_transports(monkeypatch):
    assert configuration(endpoint="http://[::1]:11434/v1")
    config = configuration(
        backend="openai_compatible",
        endpoint="https://models.example/openai/v1",
        api_key_env="SYNTHETIC_MODEL_KEY",
    )
    with pytest.raises(ProviderFailure, match="invalid_provider_configuration"):
        config.client()
    monkeypatch.setenv("SYNTHETIC_MODEL_KEY", "SYNTHETIC-PRIVATE-KEY")
    assert "models.example" not in repr(config) and "SYNTHETIC-PRIVATE-KEY" not in repr(config)
    client = config.client()
    assert client.base_url == "https://models.example/openai/v1/"
    assert client.headers["Authorization"] == "Bearer SYNTHETIC-PRIVATE-KEY"
    assert not client.follow_redirects and client._trust_env is False
    asyncio.run(client.aclose())
    monkeypatch.setenv("SYNTHETIC_MODEL_KEY", "SECRET\nINJECTION")
    with pytest.raises(ProviderFailure, match="invalid_provider_configuration"):
        config.client()


def test_inference_input_preserves_source_bytes_and_rejects_bad_envelopes():
    source = " \n東京都 synthetic Gold\n "
    parsed = parse_input(json.dumps({"text": source}).encode())
    assert parsed.text == source and parsed.digest() == hashlib.sha256(source.encode()).hexdigest()
    for raw in (
        b'{"text":" "}',
        b'{"text":"ok","scope_id":"PRIVATE"}',
        b'{"text":"\\ud800"}',
        b"invalid PRIVATE",
        b"x" * (MAX_INFERENCE_BYTES + 1),
        json.dumps({"text": "x" * 65537}).encode(),
    ):
        with pytest.raises(ProviderFailure) as failure:
            parse_input(raw)
        assert "PRIVATE" not in str(failure.value)
    for raw in (b"{}", b"PRIVATE", b"x" * 32769):
        with pytest.raises(ProviderFailure, match="invalid_provider_configuration"):
            parse_settings(raw)
    for field, value in (
        ("text_model", {"name": "synthetic", "revision": "\ud800"}),
        ("embedding_model", {"name": "\ud800", "revision": "1"}),
        ("embedding_target", "\ud800"),
    ):
        raw = json.dumps(configuration().model_dump() | {field: value}).encode()
        with pytest.raises(ProviderFailure, match="invalid_provider_configuration"):
            parse_settings(raw)
    assert parse_settings(configuration().model_dump_json().encode()) == configuration()


def test_http_summary_is_explicit_bounded_untrusted_and_stateless(monkeypatch):
    source = InferenceInput(text="User has not approved deployment.")

    def handler(request):
        assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "synthetic-summary"
        assert payload["messages"][1] == {"role": "user", "content": source.text}
        assert payload["stream"] is False and payload["max_tokens"] == 1024
        assert "tools" not in payload and "Idempotency-Key" not in request.headers
        return upstream(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Deployment approval is pending.",
                        },
                    }
                ]
            }
        )

    calls, clients = mock_http(monkeypatch, handler)
    result = asyncio.run(HTTPProvider(configuration()).summarize(source))
    assert result.summary == "Deployment approval is pending."
    assert result.status == "untrusted" and result.input_digest == source.digest()
    assert result.model == TextModel(name="synthetic-summary", revision="1")
    assert len(calls) == 1 and clients[0].is_closed


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {
            "choices": [
                {"finish_reason": "length", "message": {"role": "assistant", "content": "x"}}
            ]
        },
        {"choices": [{"finish_reason": "stop", "message": {"role": "user", "content": "x"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": " "}}]},
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "x",
                        "tool_calls": [{"id": "unsafe"}],
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "x", "refusal": "private reason"},
                }
            ]
        },
    ],
)
def test_http_summary_rejects_partial_refused_or_malformed_output(monkeypatch, response):
    calls, _ = mock_http(monkeypatch, lambda request: upstream(response))
    with pytest.raises(ProviderFailure, match="invalid_provider_response") as failure:
        asyncio.run(HTTPProvider(configuration()).summarize(InferenceInput(text="Synthetic")))
    assert len(calls) == 1 and failure.value.error.billing_unknown


def test_http_embedding_preserves_model_identity_and_input_digest(monkeypatch):
    data = InferenceInput(text="東京都 Gold")
    values = [1.0] + [0.0] * 767

    def handler(request):
        assert str(request.url) == "http://127.0.0.1:11434/v1/embeddings"
        assert json.loads(request.content) == {
            "model": "synthetic-embedding",
            "input": data.text,
            "dimensions": 768,
            "encoding_format": "float",
        }
        return upstream({"data": [{"index": 0, "embedding": values}]})

    calls, _ = mock_http(monkeypatch, handler)
    result = asyncio.run(HTTPProvider(configuration()).embed(data))
    assert result.model == EmbeddingModel(name="synthetic-embedding", revision="1")
    assert result.values == values and result.input_digest == data.digest()
    assert len(calls) == 1


@pytest.mark.parametrize("values", [[0.0] * 768, [1.0] * 767, [1.0] * 769, ["1"] * 768])
def test_http_embedding_rejects_incompatible_output(monkeypatch, values):
    calls, _ = mock_http(
        monkeypatch, lambda request: upstream({"data": [{"index": 0, "embedding": values}]})
    )
    with pytest.raises(ProviderFailure, match="invalid_provider_response"):
        asyncio.run(HTTPProvider(configuration()).embed(InferenceInput(text="Synthetic")))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "outcome", ["redirect", "failed", "lost", "oversize", "compressed", "text"]
)
def test_http_provider_never_retries_redirects_falls_back_or_leaks_errors(monkeypatch, outcome):
    def handler(request):
        if outcome == "lost":
            raise httpx.ReadError("PRIVATE connection detail")
        if outcome == "oversize":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=Chunks([b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1)]),
            )
        return upstream(
            {"PRIVATE": "upstream detail"},
            status=307 if outcome == "redirect" else 503 if outcome == "failed" else 200,
            **(
                {"Location": "https://untrusted.example"}
                if outcome == "redirect"
                else {"Content-Encoding": "gzip"}
                if outcome == "compressed"
                else {"Content-Type": "text/plain"}
                if outcome == "text"
                else {}
            ),
        )

    calls, clients = mock_http(monkeypatch, handler)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(HTTPProvider(configuration()).embed(InferenceInput(text="Synthetic")))
    assert "PRIVATE" not in str(failure.value)
    assert len(calls) == 1 and clients[0].is_closed


@pytest.mark.parametrize("operation", ["summarize", "embed", "extract"])
def test_unconfigured_capability_does_not_call_provider(monkeypatch, operation):
    calls, _ = mock_http(monkeypatch, lambda request: pytest.fail("Unexpected inference"))
    provider = HTTPProvider(
        configuration(**{"embedding_model" if operation == "embed" else "text_model": None})
    )
    with pytest.raises(ProviderFailure, match="provider_capability_unavailable"):
        asyncio.run(getattr(provider, operation)(InferenceInput(text="Synthetic")))
    assert calls == []


def test_http_inspection_never_invokes_model_or_claims_connectivity(monkeypatch):
    calls, _ = mock_http(monkeypatch, lambda request: pytest.fail("Unexpected inference"))
    result = asyncio.run(run(configuration(), "inspect", b""))
    assert result == {
        "backend": "local_http",
        "operations": ["summarize", "embed", "extract"],
        "configuration_valid": True,
        "inference_tested": False,
    }
    assert calls == []
    with pytest.raises(ProviderFailure, match="invalid_inference_input"):
        asyncio.run(run(configuration(), "inspect", b'{"text":"PRIVATE"}'))


def test_operator_cli_is_bounded_and_reports_sanitized_configuration_errors(tmp_path):
    path = tmp_path / "provider.json"
    path.write_text(configuration().model_dump_json())
    command = [sys.executable, "-m", "pg_agmemory.cli", "infer", "inspect", "--config", str(path)]
    inspected = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert inspected.returncode == 0 and inspected.stderr == ""
    assert json.loads(inspected.stdout)["result"]["inference_tested"] is False
    path.write_text('{"api_key":"PRIVATE-CREDENTIAL"}')
    invalid = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert invalid.returncode == 2 and "PRIVATE" not in invalid.stdout + invalid.stderr
    assert json.loads(invalid.stdout)["error"]["code"] == "invalid_provider_configuration"
    path.write_text(configuration().model_dump_json())
    malformed = subprocess.run(
        [*command[:4], "embed", *command[5:]],
        input=b"x" * (MAX_INFERENCE_BYTES + 1),
        capture_output=True,
        timeout=10,
    )
    assert malformed.returncode == 1
    assert json.loads(malformed.stdout)["error"]["code"] == "inference_input_too_large"


def test_http_api_key_header_and_distinct_embedding_target(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_KEY", "synthetic-key")
    settings = configuration(
        backend="openai_compatible",
        endpoint="https://model.example/openai/v1",
        api_key_env="SYNTHETIC_KEY",
        auth_header="api-key",
        embedding_target="deployment-alias",
    )
    client = settings.client()
    assert client.headers["api-key"] == "synthetic-key"
    assert "Authorization" not in client.headers
    asyncio.run(client.aclose())

    def handler(request):
        assert json.loads(request.content)["model"] == "deployment-alias"
        return upstream({"data": [{"index": 0, "embedding": [1.0] + [0.0] * 767}]})

    mock_http(monkeypatch, handler)
    result = asyncio.run(HTTPProvider(settings).embed(InferenceInput(text="Synthetic")))
    assert result.model.name == "synthetic-embedding"


@pytest.mark.parametrize("name", ["ollama", "openai"])
def test_example_profiles_are_valid_and_inspection_makes_no_network_call(monkeypatch, name):
    path = Path(__file__).parents[1] / "examples" / "inference" / f"{name}.json"
    settings = parse_settings(path.read_bytes())
    assert settings.text_model is not None
    assert settings.embedding_model.dimensions == 768
    if name == "openai":
        assert settings.api_key_env == "OPENAI_API_KEY"
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key-not-a-credential")
    else:
        assert settings.api_key_env is None
        assert settings.text_model.revision.startswith("ollama-sha256:")
        assert settings.embedding_model.revision.startswith("ollama-sha256:")

    async def fail_request(*args, **kwargs):
        pytest.fail("Configuration inspection must not contact a model")

    monkeypatch.setattr(httpx.AsyncClient, "send", fail_request)
    result = asyncio.run(HTTPProvider(settings).inspect())
    assert result["operations"] == ["summarize", "embed", "extract"]
    assert result["inference_tested"] is False


def test_azure_example_uses_separate_sql_credentials_and_pinned_contract(monkeypatch):
    path = Path(__file__).parents[1] / "examples" / "inference" / "azure-flexible-server.json"
    settings = parse_settings(path.read_bytes())
    assert settings.backend == "azure_ai"
    assert settings.database_url_env == "PGAG_AZURE_INFERENCE_DATABASE_URL"
    assert settings.endpoint is None and settings.api_key_env is None
    assert settings.azure_product == "flexible_server"
    assert settings.azure_extension_version == "2.0.1"
    assert settings.azure_summary_mode == "generate"
    assert settings.text_model.name == "pgag-summary"
    assert settings.embedding_model.dimensions == 768
    assert settings.embedding_model.name == "text-embedding-3-small"
    assert settings.embedding_target == "pgag-embed"
    assert settings.max_output_tokens is None
    monkeypatch.delenv("PGAG_AZURE_INFERENCE_DATABASE_URL", raising=False)
    with pytest.raises(ProviderFailure, match="invalid_provider_configuration"):
        asyncio.run(run(settings, "inspect", b""))


@pytest.mark.integration
@pytest.mark.parametrize(
    "provider_mode", ["synthetic", pytest.param("live", marks=pytest.mark.live)]
)
def test_generated_embedding_uses_native_digest_replay_and_current_purge_checks(
    env, api_process, monkeypatch, request, provider_mode
):
    if provider_mode == "live":
        provider = request.getfixturevalue("live_provider")
        if provider.settings.embedding_model is None:
            pytest.skip("The selected profile has no embedding model")
    else:
        mock_http(
            monkeypatch,
            lambda request: upstream({"data": [{"index": 0, "embedding": [1.0] + [0.0] * 767}]}),
        )
        provider = HTTPProvider(configuration())
    source = UUID(env.observe("Synthetic provider input").json()["memory_id"])
    stale = UUID(env.observe("Synthetic stale provider input").json()["memory_id"])
    with api_process("provider-embedding.log") as (http, _):

        async def scenario():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                canonical = await sdk.embedding_input(Explain(memory_id=source))
                generated = await provider.embed(InferenceInput(text=canonical.text))
                assert generated.input_digest == canonical.input_digest
                upload = PutEmbedding(memory_id=source, **generated.model_dump())
                first = await sdk.put_embedding(upload, idempotency_key="generated-embedding")
                assert (
                    await sdk.put_embedding(upload, idempotency_key="generated-embedding") == first
                )
                found = await sdk.recall(
                    Recall(
                        scope_ids=[env.scopes[0]],
                        purpose="synthetic provider test",
                        retrieval_mode="vector",
                        vector_query=VectorQuery(model=generated.model, values=generated.values),
                    )
                )
                assert [item.memory_id for item in found.items] == [source]
                pending = await provider.embed(
                    InferenceInput(text=(await sdk.embedding_input(Explain(memory_id=stale))).text)
                )
                await sdk.forget(
                    Forget(memory_ids=[source, stale], reason="synthetic test"),
                    idempotency_key="provider-purge",
                )
                for body, key in (
                    (upload, "generated-embedding"),
                    (PutEmbedding(memory_id=stale, **pending.model_dump()), "stale-embedding"),
                ):
                    with pytest.raises(MemoryClientError, match="not_found"):
                        await sdk.put_embedding(body, idempotency_key=key)

        asyncio.run(scenario())
