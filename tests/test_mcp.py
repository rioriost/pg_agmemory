import asyncio
import json
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import jsonschema
import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import ValidationError

from pg_agmemory import __version__
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.mcp_adapter import (
    TOOLS,
    AdapterFailure,
    AdapterSettings,
    MutationInput,
    NativeClient,
    SafeDiagnostics,
    create_server,
)
from pg_agmemory.models import Explain, Forget, Recall, Remember
from pg_agmemory.native_client import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES

pytestmark = pytest.mark.integration
NAMES = ["memory_recall", "memory_remember", "memory_explain", "memory_forget"]


def recall_request(env, **changes):
    return {"scope_ids": [str(env.scopes[0])], "purpose": "test", "query": "Gold", **changes}


def remember_request(env, source, **changes):
    return {
        "scope_id": str(env.scopes[0]),
        "subject": "ACME",
        "predicate": "contract_tier",
        "value": "Gold",
        "explicit_intent": True,
        "evidence": [{"memory_id": source, "quote": "Gold"}],
        **changes,
    }


def process_settings(env, api_url, index=0, **changes):
    return {
        "PATH": os.environ["PATH"],
        "PGAG_MCP_API_URL": api_url,
        "PGAG_MCP_API_TOKEN": env.token(index),
        # Ignore proxy settings inherited from the host; the origin is fixed.
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "ALL_PROXY": "http://127.0.0.1:1",
        **changes,
    }


@asynccontextmanager
async def connected(env, api_url, tmp_path, mode="auto", index=0):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pg_agmemory.cli", "mcp"],
        env=process_settings(env, api_url, index),
    )
    with (tmp_path / f"mcp-{uuid4()}.log").open("w") as errors:
        async with Client(
            stdio_client(parameters, errlog=errors),
            mode=mode,
            read_timeout_seconds=15,
        ) as client:
            yield client


@pytest.mark.parametrize(
    "url",
    [
        "https://memory.example",
        "https://memory.example:8443/",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://[::1]:8000",
    ],
)
def test_fixed_api_origin_configuration(url):
    settings = AdapterSettings(url, "fixed.identity.signature")
    assert settings.api_url == url
    assert settings.api_token not in repr(settings)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://memory.example",
        "http://192.168.1.2",
        "http://127.1",
        "https://user:secret@memory.example",
        "https://memory.example/v1",
        "https://memory.example?token=secret",
        "https://memory.example/#secret",
        "ftp://memory.example",
        "https://memory.example:0",
        "https://memory.example:65536",
        "https://memory.example\n",
        " http://localhost",
    ],
)
def test_invalid_api_origin_is_rejected_without_echo(url):
    with pytest.raises(ValueError) as error:
        AdapterSettings(url, "fixed.identity.signature")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("token", ["", "Bearer test", "x.y.z\n", "x.y", "x" * 16378 + ".y.z"])
def test_invalid_token_shape_is_rejected_without_echo(token):
    with pytest.raises(ValueError) as error:
        AdapterSettings("https://memory.example", token)
    assert "x" * 100 not in str(error.value)


def test_tool_schema_is_generated_from_native_contracts():
    for spec, model in zip(TOOLS, (Recall, Remember, Explain, Forget), strict=True):
        definition = spec.definition()
        schema = definition.input_schema
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator.check_schema(definition.output_schema)
        assert schema["additionalProperties"] is False
        assert (
            schema["$defs"][model.__name__]["properties"] == model.model_json_schema()["properties"]
        )
        assert definition.annotations.read_only_hint == spec.read_only
        assert definition.annotations.destructive_hint == (spec.name == "memory_forget")
        assert definition.annotations.idempotent_hint is True
        assert set(schema["required"]) == (
            {"request"} if spec.read_only else {"request", "idempotency_key"}
        )


@pytest.mark.parametrize("mode,version", [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
def test_real_stdio_lifecycle_and_native_semantics(env, api_process, tmp_path, mode, version):
    source = env.observe("東京都の契約は Gold です。").json()["memory_id"]
    with api_process(f"mcp-api-{mode}.log") as (http, _):

        async def scenario():
            async with connected(env, str(http.base_url), tmp_path, mode) as client:
                assert client.protocol_version == version
                assert client.server_info.name == "pg_agmemory"
                assert client.server_info.version == __version__
                assert client.server_capabilities.resources is None
                assert client.server_capabilities.prompts is None
                listed = (await client.list_tools()).tools
                assert [tool.name for tool in listed] == NAMES
                recalled = await client.call_tool(
                    "memory_recall",
                    {
                        "request": recall_request(
                            env,
                            query="契約",
                            search_profile="ja-janome-0.5.0-v1",
                        )
                    },
                )
                assert not recalled.is_error
                assert recalled.structured_content["result"]["items"][0]["memory_id"] == source
                assert "東京都" not in recalled.content[0].text
                write = {
                    "request": remember_request(env, source),
                    "idempotency_key": str(uuid4()),
                }
                saved = await client.call_tool("memory_remember", write)
                assert not saved.is_error, saved
                jsonschema.validate(saved.structured_content, listed[1].output_schema)
                assert (
                    await client.call_tool("memory_remember", write)
                ).structured_content == saved.structured_content
                memory = saved.structured_content["result"]["memory_id"]
                explained = await client.call_tool(
                    "memory_explain", {"request": {"memory_id": memory}}
                )
                assert explained.structured_content["result"]["evidence"][0]["memory_id"] == source
                assert explained.structured_content["result"]["revision"] == 1
                preview = await client.call_tool(
                    "memory_forget",
                    {
                        "request": {"memory_ids": [source], "mode": "preview", "reason": "test"},
                        "idempotency_key": str(uuid4()),
                    },
                )
                assert preview.structured_content["result"] == {
                    "mode": "preview",
                    "object_count": 2,
                    "changed": False,
                }
                purge = {
                    "request": {"memory_ids": [source], "mode": "purge", "reason": "test"},
                    "idempotency_key": str(uuid4()),
                }
                deleted = await client.call_tool("memory_forget", purge)
                assert not deleted.is_error
                receipt = deleted.structured_content["result"]
                assert receipt["state"] == "active_store_purged"
                assert receipt["backup_status"] == "operator_managed"
                assert (
                    await client.call_tool("memory_forget", purge)
                ).structured_content == deleted.structured_content
                assert (
                    await client.call_tool(
                        "memory_recall",
                        {"request": recall_request(env)},
                    )
                ).structured_content["result"]["items"] == []
                hidden = await client.call_tool(
                    "memory_explain", {"request": {"memory_id": memory}}
                )
                assert hidden.is_error and hidden.structured_content["error"]["code"] == "not_found"
                assert (await client.call_tool("memory_remember", write)).is_error

        asyncio.run(scenario())


@pytest.mark.parametrize("version", ["2026-07-28", "2025-11-25"])
def test_raw_wire_version_fixtures_and_structured_output(env, api_process, version):
    with api_process(f"raw-mcp-{version}.log") as (http, _):

        async def scenario():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pg_agmemory.cli",
                "mcp",
                env=process_settings(env, str(http.base_url)),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def send(message, reply=True):
                if version == "2026-07-28":
                    message.setdefault("params", {})["_meta"] = {
                        "io.modelcontextprotocol/protocolVersion": version,
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "modern-fixture",
                            "version": "1",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                process.stdin.write(json.dumps(message).encode() + b"\n")
                await process.stdin.drain()
                if reply:
                    line = await asyncio.wait_for(process.stdout.readline(), 15)
                    assert line
                    return json.loads(line)

            try:
                if version == "2026-07-28":
                    discovered = await send(
                        {"jsonrpc": "2.0", "id": 1, "method": "server/discover"}
                    )
                    assert "result" in discovered, discovered
                    assert version in discovered["result"]["supportedVersions"]
                else:
                    initialized = await send(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": version,
                                "capabilities": {},
                                "clientInfo": {"name": "legacy-fixture", "version": "1"},
                            },
                        }
                    )
                    assert initialized["result"]["protocolVersion"] == version
                    await send({"jsonrpc": "2.0", "method": "notifications/initialized"}, False)
                tools = await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                assert [tool["name"] for tool in tools["result"]["tools"]] == NAMES
                result = await send(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "memory_recall",
                            "arguments": {"request": recall_request(env)},
                        },
                    }
                )
                output = result["result"]
                assert output.get("isError", False) is False
                assert output["structuredContent"]["result"]["items"] == []
                assert output["content"][0]["type"] == "text"
            finally:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                errors = (await process.stderr.read()).decode()
                assert "Bearer " not in errors

        asyncio.run(scenario())


def test_fixed_principal_cannot_access_other_scope_or_tenant(env, api_process, tmp_path):
    hidden = [env.observe(index=index).json()["memory_id"] for index in (1, 2)]
    with api_process("mcp-isolation.log") as (http, _):

        async def scenario():
            async with connected(env, str(http.base_url), tmp_path) as client:
                result = await client.call_tool(
                    "memory_recall",
                    {
                        "request": recall_request(
                            env,
                            scope_ids=[str(scope) for scope in env.scopes],
                        )
                    },
                )
                assert result.structured_content["result"]["items"] == []
                for memory in [*hidden, str(uuid4())]:
                    result = await client.call_tool(
                        "memory_explain", {"request": {"memory_id": memory}}
                    )
                    assert result.is_error
                    assert result.structured_content["error"]["code"] == "not_found"
                    assert memory not in json.dumps(result.structured_content)
                forged = await client.call_tool(
                    "memory_recall",
                    {
                        "request": recall_request(env),
                        "authorization": "Bearer DO_NOT_ECHO",
                    },
                )
                assert (
                    forged.is_error
                    and forged.structured_content["error"]["code"] == "invalid_request"
                )

        asyncio.run(scenario())


def test_restarts_preserve_mutation_keys_and_current_authorization(env, api_process, tmp_path):
    source = env.observe().json()["memory_id"]
    args = {"request": remember_request(env, source), "idempotency_key": str(uuid4())}
    with api_process("mcp-restart.log") as (http, _):

        async def scenario():
            async with connected(env, str(http.base_url), tmp_path) as client:
                saved = await client.call_tool("memory_remember", args)
            async with connected(env, str(http.base_url), tmp_path) as client:
                replay = await client.call_tool("memory_remember", args)
                assert not saved.is_error and replay.structured_content == saved.structured_content
                conflict = await client.call_tool(
                    "memory_remember",
                    {
                        **args,
                        "request": {**args["request"], "subject": "changed"},
                    },
                )
                assert conflict.structured_content["error"]["code"] == "idempotency_conflict"
                with psycopg.connect(env.admin_url) as conn:
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (str(env.tenants[0]),),
                    )
                    conn.execute(
                        """UPDATE memory.scope_member SET permissions = ARRAY[]::text[]
                           WHERE scope_id = %s""",
                        (env.scopes[0],),
                    )
                    conn.execute(
                        "UPDATE memory.tenant SET access_epoch = access_epoch + 1 WHERE id = %s",
                        (env.tenants[0],),
                    )
                result = await client.call_tool("memory_remember", args)
                assert result.structured_content["error"]["code"] == "not_found"

        asyncio.run(scenario())


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("memory_observe", {"secret": "DO_NOT_ECHO"}),
        ("memory_remember", {"request": {}}),
        ("memory_recall", {"request": {"tenant_id": "DO_NOT_ECHO"}}),
        ("memory_explain", {"request": {"memory_id": "DO_NOT_ECHO"}}),
        ("memory_forget", {"request": {}, "idempotency_key": "\r\nDO_NOT_ECHO"}),
    ],
)
def test_invalid_tools_and_inputs_do_not_call_upstream_or_echo(tool, arguments):
    async def scenario():
        def upstream(request):
            pytest.fail("Invalid MCP input reached Native API")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
        ) as http:
            async with Client(create_server(NativeClient(http))) as client:
                result = await client.call_tool(tool, arguments)
                assert result.is_error
                assert "DO_NOT_ECHO" not in result.model_dump_json()

    asyncio.run(scenario())


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            if isinstance(part, Exception):
                raise part
            yield part


def mocked_response(status=200, payload=None, headers=None):
    return httpx.Response(
        status,
        headers=headers or {"content-type": "application/json"},
        stream=Chunks([json.dumps(payload).encode()]),
    )


@pytest.mark.parametrize(
    "fault",
    [
        "disconnect",
        "timeout",
        "invalid_json",
        "html",
        "oversized",
        "redirect",
        "bad_shape",
        "wrong_status",
        "compressed",
    ],
)
def test_uncertain_mutations_never_claim_success_or_retry_automatically(fault):
    calls = []
    args = {
        "request": {
            "scope_id": str(uuid4()),
            "subject": "ACME",
            "predicate": "tier",
            "value": "Gold",
            "explicit_intent": True,
            "evidence": [{"memory_id": str(uuid4()), "quote": "Gold"}],
        },
        "idempotency_key": "persisted-key",
    }

    def upstream(request):
        calls.append(request)
        if fault == "disconnect":
            raise httpx.ReadError("DO_NOT_ECHO")
        if fault == "timeout":
            raise httpx.ReadTimeout("DO_NOT_ECHO")
        if fault == "invalid_json":
            return httpx.Response(
                201, headers={"content-type": "application/json"}, stream=Chunks([b"DO_NOT_ECHO"])
            )
        if fault == "html":
            return mocked_response(502, "DO_NOT_ECHO", {"content-type": "text/html"})
        if fault == "oversized":
            return httpx.Response(
                201,
                headers={"content-type": "application/json"},
                stream=Chunks([b"x" * MAX_RESPONSE_BYTES, b"x"]),
            )
        if fault == "redirect":
            return mocked_response(
                307, None, {"location": "https://evil.test", "content-type": "application/json"}
            )
        if fault == "compressed":
            return mocked_response(
                201, {}, {"content-type": "application/json", "content-encoding": "gzip"}
            )
        return mocked_response(201 if fault == "bad_shape" else 200, {"secret": "DO_NOT_ECHO"})

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
            headers={"Authorization": "Bearer fixed.token.signature"},
            follow_redirects=False,
        ) as http:
            async with Client(create_server(NativeClient(http))) as client:
                result = await client.call_tool("memory_remember", args)
                assert (
                    result.is_error
                    and result.structured_content["error"]["outcome_unknown"] is True
                )
                assert "same idempotency_key" in result.content[0].text
                assert "DO_NOT_ECHO" not in result.model_dump_json()
        assert len(calls) == 1 and calls[0].headers["Idempotency-Key"] == "persisted-key"
        assert calls[0].headers["Authorization"] == "Bearer fixed.token.signature"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "unauthenticated"),
        (404, "not_found"),
        (409, "idempotency_conflict"),
        (503, "database_error"),
        (503, "request_deadline_exceeded"),
        (503, "commit_outcome_unknown"),
        (500, "DO_NOT_ECHO"),
    ],
)
def test_native_errors_are_safe_and_distinguish_ambiguous_writes(status, code):
    request_id = str(uuid4())

    async def scenario():
        def upstream(request):
            return mocked_response(
                status,
                {
                    "code": code,
                    "request_id": request_id,
                    "retryable": True,
                    "details": {"input": "DO_NOT_ECHO"},
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
        ) as http:
            async with Client(create_server(NativeClient(http))) as client:
                result = await client.call_tool(
                    "memory_forget",
                    {
                        "request": {"memory_ids": [str(uuid4())], "reason": "test"},
                        "idempotency_key": "same-key",
                    },
                )
                error = result.structured_content["error"]
                assert result.is_error and error["native_status"] == status
                assert error["request_id"] == request_id
                assert error["code"] == (code if code != "DO_NOT_ECHO" else "native_api_error")
                assert error["outcome_unknown"] == (status >= 500)
                assert error["retryable"] == (status == 503 and code != "commit_outcome_unknown")
                if code == "commit_outcome_unknown":
                    assert "operator reconciliation required" in result.content[0].text
                    assert "retry only" not in result.content[0].text
                assert "DO_NOT_ECHO" not in result.model_dump_json()

    asyncio.run(scenario())


def test_native_request_byte_limit_before_network():
    request = {
        "scope_id": str(uuid4()),
        "subject": "ACME",
        "predicate": "tier",
        "value": "東" * 65536,
        "explicit_intent": True,
        "evidence": [{"memory_id": str(uuid4()), "quote": "東" * 4096} for _ in range(32)],
    }
    assert len(json.dumps(request).encode()) > MAX_REQUEST_BYTES

    async def scenario():
        def upstream(request):
            pytest.fail("Oversized request reached Native API")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
        ) as http:
            with pytest.raises(AdapterFailure) as error:
                await NativeClient(http).call(
                    TOOLS[1], {"request": request, "idempotency_key": "key"}
                )
            assert error.value.error.code == "body_too_large"
            assert error.value.error.outcome_unknown is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [{"service_version": "0.0.7"}, {"schema_version": SCHEMA_VERSION + 1}, {"api_version": "v2"}],
)
def test_startup_requires_matching_native_api(changes):
    async def scenario():
        def upstream(request):
            assert request.url.path == "/v1/capabilities"
            return mocked_response(
                payload={
                    "service_version": __version__,
                    "api_version": "v1",
                    "schema_version": SCHEMA_VERSION,
                    **changes,
                }
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
        ) as http:
            with pytest.raises(AdapterFailure, match="native_version_mismatch"):
                await NativeClient(http).validate()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case", ["configuration", "expired", "audience", "unknown_subject", "subject_flag", "once_flag"]
)
def test_actual_cli_startup_failure_is_nonzero_and_secret_free(env, api_process, case):
    with api_process(f"bad-mcp-{case}.log") as (http, _):
        settings = process_settings(env, str(http.base_url))
        arguments = []
        if case == "configuration":
            settings["PGAG_MCP_API_URL"] = "https://DO_NOT_ECHO:secret@memory.test"
        elif case == "expired":
            settings["PGAG_MCP_API_TOKEN"] = env.token(exp=1)
        elif case == "audience":
            settings["PGAG_MCP_API_TOKEN"] = env.token(aud="mcp-not-native-api")
        elif case == "unknown_subject":
            settings["PGAG_MCP_API_TOKEN"] = env.token(sub="DO_NOT_ECHO")
        elif case == "subject_flag":
            arguments = ["--subject", "DO_NOT_ECHO"]
        elif case == "once_flag":
            arguments = ["--once"]
        result = subprocess.run(
            [sys.executable, "-m", "pg_agmemory.cli", "mcp", *arguments],
            env=settings,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode != 0 and result.stdout == ""
        assert settings["PGAG_MCP_API_TOKEN"] not in result.stderr
        assert "DO_NOT_ECHO" not in result.stderr and "Traceback" not in result.stderr


def test_diagnostics_strip_sdk_content_and_tracebacks():
    record = logging.LogRecord(
        "mcp.server.runner", logging.ERROR, "", 1, "DO_NOT_ECHO %s", ("secret",), None
    )
    record.exc_text = "Traceback DO_NOT_ECHO"
    assert SafeDiagnostics().filter(record)
    assert record.getMessage() == "mcp_transport_event" and record.exc_text is None


@pytest.mark.parametrize("key", [" key", "key ", "key\n", "東", "x" * 257])
def test_mutation_keys_are_not_trimmed_or_rewritten(key):
    with pytest.raises(ValidationError):
        MutationInput[Forget].model_validate(
            {
                "request": {"memory_ids": [str(uuid4())], "reason": "test"},
                "idempotency_key": key,
            }
        )


def test_exact_256_character_mutation_key_is_preserved():
    value = MutationInput[Forget].model_validate(
        {
            "request": {"memory_ids": [str(uuid4())], "reason": "test"},
            "idempotency_key": "X" * 256,
        }
    )
    assert value.idempotency_key == "X" * 256


def test_wrong_forget_success_variant_is_an_unknown_outcome():
    async def scenario():
        def upstream(request):
            return mocked_response(202, {"mode": "preview", "changed": False, "object_count": 1})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
        ) as http:
            async with Client(create_server(NativeClient(http))) as client:
                result = await client.call_tool(
                    "memory_forget",
                    {
                        "request": {
                            "memory_ids": [str(uuid4())],
                            "reason": "test",
                            "mode": "purge",
                        },
                        "idempotency_key": "key",
                    },
                )
                assert result.is_error and result.structured_content["error"]["outcome_unknown"]

    asyncio.run(scenario())


def test_read_transport_failure_does_not_claim_mutation_uncertainty():
    async def scenario():
        def upstream(request):
            raise httpx.ReadError("DO_NOT_ECHO")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
        ) as http:
            async with Client(create_server(NativeClient(http))) as client:
                result = await client.call_tool(
                    "memory_explain", {"request": {"memory_id": str(uuid4())}}
                )
                assert result.is_error
                assert result.structured_content["error"]["retryable"] is True
                assert result.structured_content["error"]["outcome_unknown"] is False

    asyncio.run(scenario())


def test_lost_http_response_after_commit_replays_one_result(
    env, api_process, lose_first_response_transport
):
    source = env.observe().json()["memory_id"]

    with api_process("mcp-lost-response.log") as (api, _):

        async def scenario():
            async with httpx.AsyncClient(
                transport=lose_first_response_transport,
                base_url=str(api.base_url),
                headers={"Authorization": f"Bearer {env.token()}"},
            ) as http:
                async with Client(create_server(NativeClient(http))) as client:
                    arguments = {
                        "request": remember_request(env, source),
                        "idempotency_key": str(uuid4()),
                    }
                    failed = await client.call_tool("memory_remember", arguments)
                    assert failed.is_error and failed.structured_content["error"]["outcome_unknown"]
                    assert lose_first_response_transport.calls == 1
                    retried = await client.call_tool("memory_remember", arguments)
                    assert (
                        not retried.is_error
                        and retried.structured_content["result"]["revision"] == 1
                    )
                    assert lose_first_response_transport.calls == 2
            with psycopg.connect(env.admin_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM memory.assertion WHERE tenant_id = %s",
                        (env.tenants[0],),
                    ).fetchone()[0]
                    == 1
                )

        asyncio.run(scenario())


def test_rejects_continuation_state_without_forwarding(env):
    async def scenario():
        def upstream(request):
            pytest.fail("Continuation input reached Native API")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="https://memory.test",
        ) as http:
            async with Client(create_server(NativeClient(http))) as client:
                result = await client.session.call_tool(
                    "memory_recall",
                    {"request": recall_request(env)},
                    request_state="DO_NOT_ECHO",
                    input_responses={},
                )
                assert result.is_error
                assert result.structured_content["error"]["code"] == "unsupported_tool_execution"
                assert "DO_NOT_ECHO" not in result.model_dump_json()

    asyncio.run(scenario())
