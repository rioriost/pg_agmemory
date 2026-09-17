import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory import __version__
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.native_client import AdapterFailure, NativeHTTPClient, NativeSettings
from pg_agmemory.recall_hook import MAX_INPUT_BYTES, HookInput, HookSettings, parse_input, recall


def settings(**changes):
    return HookSettings(
        **{
            "api_url": "http://127.0.0.1:1",
            "api_token": "fixed.identity.signature",
            "scope_ids": [uuid4()],
            **changes,
        }
    )


def hook_env(config):
    return {
        "PATH": os.environ["PATH"],
        **{
            f"PGAG_HOOK_{key.upper()}": (
                json.dumps(value) if isinstance(value, list) else str(value)
            )
            for key, value in config.model_dump(mode="json").items()
        },
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "ALL_PROXY": "http://127.0.0.1:1",
    }


def invoke(config, raw=b'{"event":"session_start","query":""}', **changes):
    return subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "recall-hook"],
        input=raw,
        capture_output=True,
        env={**hook_env(config), **changes},
        timeout=20,
    )


def success(completed):
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == b""
    assert len(completed.stdout.splitlines()) == 1
    output = json.loads(completed.stdout)
    assert set(output) == {"status", "event", "result", "error"}
    assert output["status"] == "ok" and output["error"] is None
    return output["result"]


@pytest.mark.parametrize("event", ["session_start", "task_switch", "after_compaction"])
def test_event_is_only_recall_metadata_and_configuration_controls_request(event):
    config = settings()
    request = config.request(HookInput(event=event, query="Gold"))
    assert request.mode == "implicit"
    assert request.scope_ids == config.scope_ids
    assert request.purpose == "implicit_context"
    assert request.token_budget == 2000 and request.max_items == 20
    assert request.as_of is None and request.known_at is None
    assert request.search_profile == "simple-v1"
    assert config.api_token not in repr(config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope_ids", []),
        ("scope_ids", [uuid4()] * 2),
        ("scope_ids", [uuid4() for _ in range(33)]),
        ("token_budget", 63),
        ("token_budget", 2001),
        ("max_items", 0),
        ("max_items", 21),
        ("purpose", ""),
        ("purpose", "x" * 257),
        ("timeout_seconds", 0.09),
        ("timeout_seconds", 20.01),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("search_profile", "vector"),
        ("api_url", "http://untrusted.test"),
        ("api_url", "http://localhost "),
        ("api_url", "https://memory.test\x7f"),
        ("api_url", "https://\ud800.test"),
        ("api_token", "fixed.identity.signature "),
    ],
)
def test_invalid_trusted_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        settings(**{field: value})


def test_exact_configuration_boundaries_and_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("PGAG_HOOK_"):
            monkeypatch.delenv(key)
    with pytest.raises(AdapterFailure, match="invalid_hook_configuration"):
        HookSettings.from_env()
    config = settings(
        scope_ids=[uuid4() for _ in range(32)],
        purpose="p" * 256,
        token_budget=64,
        max_items=1,
        timeout_seconds=0.1,
    )
    for key, value in hook_env(config).items():
        monkeypatch.setenv(key, value)
    assert HookSettings.from_env() == config
    monkeypatch.setenv("PGAG_HOOK_SCOPE_IDS", "SECRET_NOT_JSON")
    with pytest.raises(AdapterFailure, match="^invalid_hook_configuration$"):
        HookSettings.from_env()
    settings(timeout_seconds=20, token_budget=2000, max_items=20)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff",
        b"[]",
        b"null",
        b'{"event":"session_start"}',
        b'{"event":"unknown","query":"SECRET"}',
        b'{"event":"session_start","query":null}',
        b'{"event":"session_start","query":"\\ud800"}',
        b'{"event":"session_start","query":""}\n{}',
        b"[" * 2000,
        json.dumps({"event": "session_start", "query": "x" * 4097}).encode(),
        json.dumps(
            {
                "event": "session_start",
                "query": "",
                "required_memory_refs": [{"memory_id": str(uuid4()), "revision": 1}],
            }
        ).encode(),
        b'{"event":"session_start","query":"","filters":{"subject":"SECRET"}}',
    ],
)
def test_invalid_input_is_not_an_empty_success(raw):
    completed = invoke(settings(), raw)
    assert completed.returncode == 2
    output = json.loads(completed.stdout)
    assert output["status"] == "error" and output["result"] is None and output["event"] is None
    assert output["error"]["code"] == "invalid_hook_input"
    assert output["error"]["outcome_unknown"] is False
    assert b"SECRET" not in completed.stdout + completed.stderr
    assert b"Traceback" not in completed.stderr


@pytest.mark.parametrize(
    "field",
    [
        "scope_ids",
        "tenant_id",
        "principal_id",
        "purpose",
        "mode",
        "token_budget",
        "api_url",
        "headers",
    ],
)
def test_prompt_cannot_override_trusted_routing(field):
    raw = json.dumps({"event": "task_switch", "query": "Gold", field: "INJECTED"}).encode()
    with pytest.raises(AdapterFailure, match="invalid_hook_input"):
        parse_input(raw)


def test_input_byte_and_unicode_boundaries():
    raw = json.dumps(
        {"event": "session_start", "query": "\U0001f600" * 4096}, ensure_ascii=False
    ).encode()
    assert parse_input(raw).query == "\U0001f600" * 4096
    padded = raw + b" " * (MAX_INPUT_BYTES - len(raw))
    assert parse_input(padded).event == "session_start"
    with pytest.raises(AdapterFailure, match="hook_input_too_large"):
        parse_input(padded + b" ")
    completed = invoke(settings(), padded + b" ")
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error"]["code"] == "hook_input_too_large"


@pytest.mark.parametrize("flag", ["--subject=INJECTED", "--once"])
def test_cli_rejects_identity_and_worker_flags(flag):
    completed = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "recall-hook", flag],
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 2 and completed.stdout == b""
    assert b"INJECTED" not in completed.stderr


def test_actual_cli_invalid_configuration_and_connection_failure_are_explicit():
    config = settings()
    invalid = invoke(config, PGAG_HOOK_SCOPE_IDS="SECRET")
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["error"]["code"] == "invalid_hook_configuration"
    unavailable = invoke(config)
    assert unavailable.returncode == 1
    output = json.loads(unavailable.stdout)
    assert output["event"] == "session_start"
    assert output["error"]["code"] == "native_api_unavailable"
    assert output["error"]["retryable"] and not output["error"]["outcome_unknown"]
    assert output["result"] is None
    assert b"SECRET" not in invalid.stdout + invalid.stderr
    assert config.api_token.encode() not in unavailable.stdout + unavailable.stderr


@pytest.mark.integration
def test_real_hook_all_events_match_native_recall_and_do_not_write(env, api_process):
    source = env.observe("Gold contract for 東京都").json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    env.observe("OTHER_TENANT_SECRET Gold", index=1)
    env.observe("OTHER_SCOPE_SECRET Gold", index=2)
    with api_process("hook-parity.log") as (http, _):
        config = settings(
            api_url=str(http.base_url),
            api_token=env.token(),
            scope_ids=[env.scopes[0]],
            search_profile="ja-janome-0.5.0-v1",
            timeout_seconds=10,
        )
        native_request = config.request(HookInput(event="session_start", query="Gold"))
        expected = http.post(
            "/v1/recall", json=native_request.model_dump(mode="json"), headers=env.headers()
        )
        assert expected.status_code == 200
        for event in ("session_start", "task_switch", "after_compaction"):
            completed = invoke(config, json.dumps({"event": event, "query": "Gold"}).encode())
            result = success(completed)
            assert json.loads(completed.stdout)["event"] == event
            assert result == expected.json()
            assert {item["memory_id"] for item in result["items"]} == {source, assertion}
            assert "SECRET" not in completed.stdout.decode()
            assert result["context_pack"]["byte_count"] <= config.token_budget
        with psycopg.connect(env.admin_url) as admin:
            assert (
                admin.execute(
                    "SELECT count(*) FROM memory.object WHERE tenant_id = %s", (env.tenants[0],)
                ).fetchone()[0]
                == 3
            )
        capabilities = http.get("/v1/capabilities", headers=env.headers()).json()
        assert capabilities["recall_hook"]["events"] == [
            "session_start",
            "task_switch",
            "after_compaction",
        ]
        assert capabilities["recall_hook"]["capture"] is False


@pytest.mark.integration
def test_hook_rechecks_deletion_acl_and_token_between_fresh_invocations(env, api_process):
    source = env.observe().json()["memory_id"]
    with api_process("hook-revocation.log") as (http, _):
        config = settings(
            api_url=str(http.base_url),
            api_token=env.token(),
            scope_ids=[env.scopes[0]],
        )
        assert success(invoke(config))["items"][0]["memory_id"] == source
        purged = env.client.post(
            "/v1/forget",
            json={"memory_ids": [source], "reason": "synthetic fixture", "mode": "purge"},
            headers=env.headers(),
        )
        assert purged.status_code == 202
        after = success(invoke(config))
        assert after["items"] == [] and after["empty_reason"] == "not_found"
        assert after["consistency"]["deletion_epoch"] > 0
        for scope in (env.scopes[1], env.scopes[2]):
            narrowed = success(invoke(config.model_copy(update={"scope_ids": [scope]})))
            assert narrowed["items"] == [] and narrowed["empty_reason"] == "not_found"
        with psycopg.connect(env.admin_url) as admin:
            admin.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(env.tenants[0]),)
            )
            admin.execute(
                "DELETE FROM memory.scope_member WHERE tenant_id = %s AND scope_id = %s",
                (env.tenants[0], env.scopes[0]),
            )
            admin.execute(
                "UPDATE memory.tenant SET access_epoch = access_epoch + 1 WHERE id = %s",
                (env.tenants[0],),
            )
        revoked = success(invoke(config))
        assert revoked["items"] == [] and revoked["empty_reason"] == "not_found"
        assert revoked["consistency"]["access_epoch"] > after["consistency"]["access_epoch"]
        expired = invoke(config, PGAG_HOOK_API_TOKEN=env.token(exp=1))
        assert expired.returncode == 1
        assert json.loads(expired.stdout)["error"]["code"] == "unauthenticated"


@pytest.mark.integration
def test_hook_preserves_budget_and_missing_index_outcomes(env, api_process):
    source = env.observe("東京都 Gold " + "x" * 2000).json()["memory_id"]
    with api_process("hook-coverage.log") as (http, _):
        config = settings(
            api_url=str(http.base_url),
            api_token=env.token(),
            scope_ids=[env.scopes[0]],
            search_profile="ja-janome-0.5.0-v1",
        )
        small = success(invoke(config))
        assert small["items"] == [] and small["empty_reason"] == "budget_exhausted"
        assert small["coverage"]["truncated"] is True
        too_small = invoke(config.model_copy(update={"token_budget": 64}))
        assert too_small.returncode == 1
        assert json.loads(too_small.stdout)["error"]["code"] == "budget_too_small"
        with psycopg.connect(env.admin_url) as admin:
            admin.execute("DELETE FROM memory.episode_lexical WHERE episode_id = %s", (source,))
        missing = success(invoke(config, b'{"event":"task_switch","query":"Gold"}'))
        assert missing["empty_reason"] == "index_incomplete" and missing["items"] == []
        assert missing["coverage"]["lexical_incomplete"] is True
        assert missing["coverage"]["retrieval_complete"] is False


class Chunks(httpx.AsyncByteStream):
    def __init__(self, payload):
        self.payload = payload

    async def __aiter__(self):
        yield json.dumps(self.payload).encode()


def response(payload, status=200):
    return httpx.Response(
        status, headers={"content-type": "application/json"}, stream=Chunks(payload)
    )


def capabilities():
    return {"api_version": "v1", "service_version": __version__, "schema_version": SCHEMA_VERSION}


def empty_result():
    result = {
        "items": [],
        "context_pack": {
            "format": "memory-context-v1",
            "text": "",
            "tokenizer_id": "utf8-bytes-v1",
            "token_count": None,
            "budget_unit": "utf8_bytes",
            "byte_count": 0,
            "exact_token_count": False,
        },
        "coverage": {
            "retrieval_complete": True,
            "synthesis_pending": False,
            "graph_used": False,
            "truncated": False,
        },
        "consistency": {"access_epoch": 0, "deletion_epoch": 0},
        "search_profile": "simple-v1",
        "empty_reason": "not_found",
    }
    pack = result["context_pack"]
    for _ in range(4):
        pack["byte_count"] = len(
            json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode()
        )
    return result


@pytest.mark.parametrize(
    "fault,code",
    [
        ("version", "native_version_mismatch"),
        ("shape", "invalid_native_response"),
        ("wrong_status", "invalid_native_response"),
        ("budget", "invalid_native_response"),
        ("byte_count", "invalid_native_response"),
        ("profile", "invalid_native_response"),
        ("vector_mode", "invalid_native_response"),
        ("vector_model", "invalid_native_response"),
        ("vector_coverage", "invalid_native_response"),
        ("transport", "native_api_unavailable"),
        ("native", "dependency_unavailable"),
        ("unknown_native", "native_api_error"),
    ],
)
def test_hook_safe_native_errors_and_response_limits(fault, code):
    calls = []

    async def upstream(request):
        calls.append(request)
        assert "Idempotency-Key" not in request.headers
        if request.url.path == "/v1/capabilities":
            return response(
                {**capabilities(), **({"service_version": "old"} if fault == "version" else {})}
            )
        if fault == "transport":
            raise httpx.ReadError("SECRET")
        if fault in ("native", "unknown_native"):
            return response(
                {
                    "code": "dependency_unavailable" if fault == "native" else "SECRET",
                    "retryable": True,
                    "request_id": str(uuid4()),
                    "details": {"secret": "SECRET"},
                },
                503,
            )
        result = empty_result()
        if fault == "shape":
            result = {"secret": "SECRET"}
        elif fault == "budget":
            result["context_pack"].update(text="x" * 2001, byte_count=2001)
        elif fault == "byte_count":
            result["context_pack"]["byte_count"] = 1
        elif fault == "profile":
            result["search_profile"] = "ja-janome-0.5.0-v1"
        elif fault == "vector_mode":
            result["retrieval_mode"] = "vector"
        elif fault == "vector_model":
            result["embedding_model"] = {"name": "unexpected", "revision": "v1"}
        elif fault == "vector_coverage":
            result["coverage"]["vector_incomplete"] = True
        return response(result, 201 if fault == "wrong_status" else 200)

    async def scenario():
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(upstream)
        ) as http:
            with pytest.raises(AdapterFailure) as error:
                await recall(
                    settings(),
                    HookInput(event="task_switch", query="SECRET"),
                    NativeHTTPClient(http),
                )
            assert error.value.error.code == code
            assert error.value.error.outcome_unknown is False
            assert "SECRET" not in error.value.error.model_dump_json()
        assert len(calls) == (1 if fault == "version" else 2)

    asyncio.run(scenario())


def test_deadline_is_shared_by_capabilities_and_recall_without_retry():
    calls = []

    async def upstream(request):
        calls.append(request.url.path)
        await asyncio.sleep(0.8 if len(calls) == 1 else 10)
        return response(capabilities() if len(calls) == 1 else empty_result())

    async def scenario():
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(upstream)
        ) as http:
            started = asyncio.get_running_loop().time()
            with pytest.raises(AdapterFailure, match="hook_deadline_exceeded") as error:
                await recall(
                    settings(timeout_seconds=1.2),
                    HookInput(event="after_compaction", query=""),
                    NativeHTTPClient(http),
                )
            elapsed = asyncio.get_running_loop().time() - started
            assert 1.1 <= elapsed < 1.7
            assert error.value.error.retryable and not error.value.error.outcome_unknown
        assert calls == ["/v1/capabilities", "/v1/recall"]

    asyncio.run(scenario())


def test_shared_client_keeps_fixed_credentials_and_disables_ambient_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")

    async def scenario():
        async with NativeSettings(
            "https://memory.test", "fixed.identity.signature"
        ).client() as http:
            assert http.headers["Authorization"] == "Bearer fixed.identity.signature"
            assert not http.follow_redirects and not http.trust_env
            assert http.timeout.connect == 5 and http.timeout.read == 10

    asyncio.run(scenario())
