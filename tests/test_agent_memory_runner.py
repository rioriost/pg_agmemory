"""Runner unit tests; live Copilot and PostgreSQL are owned by the guest drill."""

import asyncio
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pg_agmemory import __version__
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.models import QueryEpisodes
from pg_agmemory.native_client import NativeSettings

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_memory_runner", ROOT / "scripts" / "evaluate-agent-memory.py",
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def private_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@pytest.fixture
def transport(tmp_path):
    directory = tmp_path / "bridge"
    directory.mkdir(mode=0o700)
    private_json(directory / "transport.json", {
        "format": "pgag-copilot-transport-v1",
        "run_id": "agent-eval-12345678",
        "model": "gpt-6-astra", "reasoning_effort": "high", "cli_version": "unit-test",
        "max_calls": 100, "fresh_session_per_call": True, "custom_instructions": False,
        "tools_allowed": False, "model_weights_revision_verified": False,
        "model_revision": None,
    })
    journal = runner.Journal(tmp_path / "output")
    return runner.FileBridge(
        directory, journal, run_id="agent-eval-12345678", timeout=0.5, poll_interval=0.001,
    )


def response(call_id="000001", **overrides):
    return {
        "format": "pgag-copilot-response-v1", "call_id": call_id,
        "status": "ok", "content": '{"answer":"synthetic"}', "error": None,
        "duration_seconds": 0.1, "model": "gpt-6-astra", "reasoning_effort": "high",
        "usage": None,
    } | overrides


async def dispatch(transport, payload):
    async def host():
        while not (transport.queue / "000001.request.json").exists():
            await asyncio.sleep(0)
        private_json(transport.queue / "000001.response.json", payload)

    host_task = asyncio.create_task(host())
    try:
        return await transport.call("Synthetic prompt", case_id="case-01", phase="answer")
    finally:
        await host_task


def test_bridge_success_preserves_unknown_usage_and_private_journal(transport):
    assert asyncio.run(dispatch(transport, response())) == '{"answer":"synthetic"}'
    assert transport.calls[0]["usage"] is None
    request = json.loads((transport.queue / "000001.request.json").read_text())
    assert request == {
        "format": "pgag-copilot-request-v1", "call_id": "000001", "prompt": "Synthetic prompt",
    }
    assert transport.journal.path.stat().st_mode & 0o077 == 0
    assert (transport.queue / "000001.request.json").stat().st_mode & 0o077 == 0
    events = [json.loads(line) for line in transport.journal.path.read_text().splitlines()]
    assert [event["phase"] for event in events] == [
        "llm_dispatch_intent", "llm_dispatched", "llm_response",
    ]


def test_timeout_is_unknown_no_retry_and_no_followup_dispatch(transport):
    transport.timeout = 0.02
    with pytest.raises(runner.EvaluationFailure, match="llm_timeout") as failure:
        asyncio.run(transport.call("prompt", case_id="case-01", phase="retention"))
    assert failure.value.outcome_unknown is True
    assert transport.poisoned is True
    with pytest.raises(runner.EvaluationFailure, match="bridge_outcome_unknown"):
        asyncio.run(transport.call("prompt", case_id="case-02", phase="retention"))
    assert len(list(transport.queue.glob("*.request.json"))) == 1
    assert transport.calls[0]["status"] == "failed"
    assert transport.calls[0]["outcome_unknown"] is True


@pytest.mark.parametrize("payload,code", [
    (response(call_id="000099"), "response_call_id_mismatch"),
    (response(model="another-model"), "response_model_mismatch"),
    (response(reasoning_effort="low"), "response_model_mismatch"),
    (response(content=None), "invalid_response_status"),
    (response(status="ok", error="contradiction"), "invalid_response_status"),
    (response(duration_seconds=-1.0), "invalid_bridge_response"),
    (response(duration_seconds=float("nan")), "invalid_bridge_response"),
    (response(usage=[]), "invalid_bridge_response"),
    (response(unexpected=True), "invalid_bridge_response"),
])
def test_malformed_response_is_never_a_success(transport, payload, code):
    with pytest.raises(runner.EvaluationFailure, match=code) as failure:
        asyncio.run(dispatch(transport, payload))
    assert failure.value.outcome_unknown is True
    assert transport.calls[0]["status"] == "failed"
    assert (transport.journal.output / "call-000001.json").exists()


def test_error_response_preserves_failure_without_inventing_usage(transport):
    with pytest.raises(runner.EvaluationFailure, match="llm_bridge_error") as failure:
        asyncio.run(dispatch(transport, response(
            status="error", content=None, error="host invocation failed",
        )))
    assert failure.value.outcome_unknown
    assert transport.calls[0]["status"] == "error"
    assert transport.calls[0]["usage"] is None


@pytest.mark.parametrize("suffix", ["request", "response"])
def test_duplicate_call_ids_rejected_before_dispatch(transport, suffix):
    private_json(transport.queue / f"000001.{suffix}.json", {})
    with pytest.raises(runner.EvaluationFailure, match="duplicate_call_id"):
        asyncio.run(transport.call("prompt", case_id="case-01", phase="retention"))
    assert not transport.calls


def test_call_ceiling_enforced_before_dispatch(transport):
    transport.calls = [{"call_id": f"{index:06d}"} for index in range(1, 101)]
    with pytest.raises(runner.EvaluationFailure, match="llm_call_limit"):
        asyncio.run(transport.call("prompt", case_id="case-01", phase="answer"))
    assert not list(transport.queue.iterdir())


def test_prompt_ceiling_enforced_before_dispatch(transport):
    with pytest.raises(runner.EvaluationFailure, match="prompt_size_limit"):
        asyncio.run(transport.call("あ" * 22000, case_id="case-01", phase="answer"))
    assert not transport.calls


def test_changed_model_configuration_prevents_dispatch(transport):
    private_json(transport.metadata_path, {
        **transport.metadata.model_dump(), "model": "another-model",
    })
    with pytest.raises(runner.EvaluationFailure, match="transport_metadata_changed"):
        asyncio.run(transport.call("prompt", case_id="case-01", phase="answer"))
    assert not transport.calls


def test_bridge_requires_empty_queue(tmp_path, transport):
    private_json(transport.queue / "000001.request.json", {})
    with pytest.raises(runner.EvaluationFailure, match="nonempty_directory"):
        runner.FileBridge(transport.directory, transport.journal, run_id="agent-eval-12345678")


def test_output_requires_empty_private_directory(tmp_path):
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    (output / "previous-report.json").write_text("{}")
    with pytest.raises(runner.EvaluationFailure, match="nonempty_directory"):
        runner.Journal(output)
    output.chmod(0o755)
    with pytest.raises(runner.EvaluationFailure, match="private_directory_required"):
        runner.private_directory(output)


def test_reject_symlink_and_hardlink_inputs(tmp_path):
    source = tmp_path / "source"
    private_json(source, {})
    symlink = tmp_path / "symlink"
    symlink.symlink_to(source)
    with pytest.raises(runner.EvaluationFailure, match="symlink_rejected"):
        runner.private_read(symlink)
    hardlink = tmp_path / "hardlink"
    os.link(source, hardlink)
    with pytest.raises(runner.EvaluationFailure, match="hardlink_rejected"):
        runner.private_read(hardlink)


def test_response_symlink_rejected_with_uncertainty(transport):
    async def run():
        async def host():
            while not (transport.queue / "000001.request.json").exists():
                await asyncio.sleep(0)
            target = transport.directory / "other.json"
            private_json(target, response())
            (transport.queue / "000001.response.json").symlink_to(target)
        task = asyncio.create_task(host())
        try:
            await transport.call("prompt", case_id="case-01", phase="answer")
        finally:
            await task
    with pytest.raises(runner.EvaluationFailure, match="symlink_rejected") as failure:
        asyncio.run(run())
    assert failure.value.outcome_unknown


def test_oversized_response_rejected(transport):
    with pytest.raises(runner.EvaluationFailure, match="file_size_limit") as failure:
        asyncio.run(dispatch(transport, response(content="a" * 65536)))
    assert failure.value.outcome_unknown


def test_duplicate_json_keys_rejected():
    with pytest.raises(runner.EvaluationFailure, match="duplicate_json_key"):
        runner.parse_json(b'{"call_id":"000001","call_id":"000002"}')


def owned_environment(tmp_path):
    admin = "postgresql://owner:private-password@127.0.0.1:55432/owned_fixture"
    config = {
        "format": "pgag-agent-eval-owned-v1", "run_id": "agent-eval-12345678",
        "api_url": "http://127.0.0.1:58000",
        "admin_url_hash": hashlib.sha256(admin.encode()).hexdigest(),
        "jwt_issuer": "owned-fixture", "jwt_audience": "owned-fixture",
        "synthetic_fixture_purge_consent": True,
    }
    path = tmp_path / "owned.json"
    private_json(path, config)
    return config, {
        "PGAG_AGENT_EVAL_OWNED_RUN": config["run_id"],
        "PGAG_AGENT_EVAL_OWNED_CONFIG": str(path),
        "PGAG_ADMIN_DATABASE_URL": admin,
    }


def test_only_explicitly_bound_owned_targets_accepted(tmp_path):
    config, environment = owned_environment(tmp_path)
    assert runner.load_owned_config(config["api_url"], environment).run_id == config["run_id"]
    with pytest.raises(runner.EvaluationFailure, match="owned_target_mismatch"):
        runner.load_owned_config("http://127.0.0.1:58001", environment)
    with pytest.raises(runner.EvaluationFailure, match="owned_admin_mismatch"):
        runner.load_owned_config(config["api_url"], {
            **environment, "PGAG_ADMIN_DATABASE_URL": "postgresql://production",
        })
    with pytest.raises(runner.EvaluationFailure, match="owned_run_required"):
        runner.load_owned_config(config["api_url"], {
            **environment, "PGAG_AGENT_EVAL_OWNED_RUN": "not-owned",
        })


@pytest.mark.parametrize("value", [
    "https://production.example:443", "http://192.0.2.1:8000",
    "http://127.0.0.1:8000?redirect=production", "http://127.0.0.1",
    "http://localhost:8000", "http://169.254.1.1:8000", "http://[::1]:8000",
    "http://192.168.1.1:8000/path", "http://user:secret@192.168.1.1:8000",
    "http://8.8.8.8:8000", "http://172.32.0.1:8000",
])
def test_owned_api_rejects_public_or_ambiguous_targets(value):
    with pytest.raises(runner.EvaluationFailure):
        runner.owned_url(value, ("http", "https"))


@pytest.mark.parametrize("host", ["10.1.2.3", "172.16.0.2", "172.31.255.254", "192.168.64.2"])
def test_explicit_owned_private_ipv4_endpoints_and_unchanged_sdk_policy(tmp_path, host):
    config, environment = owned_environment(tmp_path)
    config["api_url"] = f"http://{host}:8000"
    environment["PGAG_ADMIN_DATABASE_URL"] = f"postgresql://owner:private@{host}:5432/fixture"
    config["admin_url_hash"] = hashlib.sha256(
        environment["PGAG_ADMIN_DATABASE_URL"].encode(),
    ).hexdigest()
    private_json(Path(environment["PGAG_AGENT_EVAL_OWNED_CONFIG"]), config)
    validated = runner.load_owned_config(config["api_url"], environment)
    owned = runner.OwnedMemoryClient(validated, "a.b.c")
    assert owned._settings.api_url == config["api_url"]
    with pytest.raises(ValueError, match="HTTPS or loopback"):
        NativeSettings(config["api_url"], "a.b.c")
    with pytest.raises(runner.EvaluationFailure, match="owned_target_mismatch"):
        runner.load_owned_config("http://192.168.64.9:8000", environment)


def test_bridge_requires_matching_owned_run(transport):
    with pytest.raises(runner.EvaluationFailure, match="transport_run_mismatch"):
        runner.FileBridge(transport.directory, transport.journal, run_id="agent-eval-87654321")


@pytest.mark.parametrize("fields", [
    {"max_calls": 101}, {"max_calls": 0}, {"tools_allowed": True},
    {"fresh_session_per_call": False}, {"custom_instructions": True},
    {"model_weights_revision_verified": True},
])
def test_bridge_rejects_unbounded_or_unsafe_metadata(transport, fields):
    private_json(transport.metadata_path, transport.metadata.model_dump() | fields)
    with pytest.raises(runner.EvaluationFailure, match="invalid_transport_metadata"):
        runner.FileBridge(transport.directory, transport.journal, run_id="agent-eval-12345678")


def test_smaller_declared_call_ceiling_is_enforced(transport):
    private_json(transport.metadata_path, transport.metadata.model_dump() | {"max_calls": 1})
    bridge = runner.FileBridge(
        transport.directory, transport.journal, run_id="agent-eval-12345678",
    )
    bridge.calls = [{"call_id": "000001"}]
    with pytest.raises(runner.EvaluationFailure, match="llm_call_limit"):
        asyncio.run(bridge.call("prompt", case_id="case-01", phase="answer"))
    assert not list(bridge.queue.iterdir())


class DecisionBridge:
    def __init__(
        self, case, *, invalid_retention=False, query="report locale", answer="en-GB",
    ):
        self.calls = []
        self.prompts = []
        self.case = case
        self.invalid_retention = invalid_retention
        self.query = query
        self.answer = answer

    async def call(self, prompt, *, case_id, phase):
        self.prompts.append((phase, prompt))
        self.calls.append({
            "call_id": f"{len(self.calls) + 1:06d}", "duration_seconds": 0.01, "usage": None,
        })
        if phase == "retention":
            if self.invalid_retention:
                return '{"keep_ids":[],"forget_ids":[]}'
            return json.dumps({
                "keep_ids": [self.case.events[1].event_id],
                "forget_ids": [
                    event.event_id for event in self.case.events if event != self.case.events[1]
                ],
            })
        if phase == "recall_query":
            return json.dumps({"query": self.query})
        return json.dumps({
            "answer": self.answer, "source_event_ids": [self.case.events[1].event_id],
            "abstained": False,
        })


class NativeBody(httpx.AsyncByteStream):
    def __init__(self, value):
        self.raw = json.dumps(value).encode()

    async def __aiter__(self):
        yield self.raw


def native_response(status, *, json):
    return httpx.Response(
        status, headers={"content-type": "application/json"}, stream=NativeBody(json),
    )


def hidden_recall_payload():
    return {
        "items": [],
        "context_pack": {
            "format": "memory-context-v1", "text": "", "tokenizer_id": "utf8-bytes-v1",
            "token_count": None, "budget_unit": "utf8_bytes", "byte_count": 160,
            "exact_token_count": False,
        },
        "coverage": {
            "retrieval_complete": True, "synthesis_pending": False,
            "graph_used": False, "truncated": False,
        },
        "consistency": {"access_epoch": 1, "deletion_epoch": 1},
        "search_profile": "simple-v1", "empty_reason": "not_found",
    }


@pytest.fixture
def native_fixture(tmp_path, monkeypatch):
    case = runner.recipe.pilot_cases()[0]
    config_data, _ = owned_environment(tmp_path)
    config = runner.OwnedConfig.model_validate(config_data)
    identity = runner.Provisioned(tenant_id=uuid4(), principal_id=uuid4(), scope_id=uuid4())
    foreign = runner.Provisioned(tenant_id=uuid4(), principal_id=uuid4(), scope_id=uuid4())
    sentinel_id = uuid4()
    stored = {}
    requests = []
    failure = {}

    def handler(request):
        data = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, data, dict(request.headers)))
        assert request.headers["authorization"] == "Bearer a.b.c"
        if request.url.path == "/v1/capabilities":
            return native_response(200, json={
                "api_version": "v1", "service_version": __version__,
                "schema_version": SCHEMA_VERSION,
            })
        if request.url.path == "/v1/observe":
            assert data["scope_id"] == str(identity.scope_id)
            assert not data.get("auto_extract") and not data.get("auto_embed")
            memory_id = str(uuid4())
            stored[memory_id] = data
            return native_response(201, json={"memory_id": memory_id, "revision": 1})
        if request.url.path == "/v1/forget":
            assert set(data["memory_ids"]) <= set(stored)
            if data["mode"] == "preview":
                return native_response(202, json={
                    "mode": "preview", "object_count": len(data["memory_ids"]), "changed": False,
                })
            if failure.get("purge_unknown"):
                return native_response(503, json={
                    "code": "commit_outcome_unknown",
                    "request_id": str(uuid4()), "retryable": False,
                })
            for memory_id in data["memory_ids"]:
                del stored[memory_id]
            return native_response(202, json={
                "deletion_id": str(uuid4()), "state": "active_store_purged",
                "object_count": len(data["memory_ids"]), "deletion_epoch": 1,
                "scope_ids": [str(identity.scope_id)], "backup_status": "operator_managed",
                "backup_retention_deadline": None,
            })
        if (
            request.url.path == "/v1/recall" and data["scope_ids"] == [str(identity.scope_id)]
        ):
            items = [{
                "memory_id": memory_id, "revision": 1, "type": "episode",
                "content": source["content"], "recorded_at": source["occurred_at"],
                "occurred_at": source["occurred_at"],
            } for memory_id, source in stored.items()]
            return native_response(200, json={
                "items": items,
                "context_pack": {
                    "format": "memory-context-v1", "text": "actual-native-context",
                    "tokenizer_id": "utf8-bytes-v1", "token_count": None,
                    "budget_unit": "utf8_bytes", "byte_count": 21, "exact_token_count": False,
                },
                "coverage": {
                    "retrieval_complete": True, "synthesis_pending": False,
                    "graph_used": False, "truncated": False,
                },
                "consistency": {"access_epoch": 0, "deletion_epoch": 1},
                "search_profile": "simple-v1", "empty_reason": None,
            })
        if request.url.path == "/v1/recall" and not failure.get("foreign_recall_denied"):
            return native_response(
                200, json=failure.get("foreign_recall_payload", hidden_recall_payload()),
            )
        if request.url.path in ("/v1/explain", "/v1/recall"):
            return native_response(404, json={
                "code": "not_found", "request_id": str(uuid4()), "retryable": False,
            })
        raise AssertionError(request.url.path)

    def client(settings):
        return httpx.AsyncClient(
            base_url=settings.api_url, headers={"Authorization": f"Bearer {settings.api_token}"},
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(NativeSettings, "client", client)
    monkeypatch.setattr(runner, "bearer", lambda *args: "a.b.c")
    journal = runner.Journal(tmp_path / "native-output")
    return {
        "case": case, "identity": identity, "foreign": foreign, "sentinel_id": sentinel_id,
        "config": config, "journal": journal, "requests": requests, "stored": stored,
        "failure": failure,
    }


def memory_case(fixture, bridge):
    return asyncio.run(runner.memory_arm(
        fixture["case"], fixture["identity"], "owned-subject", fixture["foreign"],
        fixture["sentinel_id"], fixture["config"], b"private-key-not-exported",
        bridge, fixture["journal"],
    ))


def test_native_http_context_rls_preview_purge_and_no_gold_in_prompts(native_fixture):
    case = native_fixture["case"]
    bridge = DecisionBridge(case)
    measured, detail = memory_case(native_fixture, bridge)
    assert measured.error is None
    assert detail["status"] == "completed"
    assert detail["isolation_denials_verified"] == 2
    assert detail["purged_objects_verified_absent"] == len(case.events) - 1
    assert measured.context_events == (case.events[1],)
    assert len(native_fixture["stored"]) == 1
    requests = native_fixture["requests"]
    observes = [body for _, path, body, _ in requests if path == "/v1/observe"]
    assert [body["content"] for body in observes] == [event.text for event in case.events]
    keys = [
        headers["idempotency-key"] for _, path, _, headers in requests
        if path in ("/v1/observe", "/v1/forget")
    ]
    assert len(keys) == len(set(keys)) == len(case.events) + 2
    recalls = [body for _, path, body, _ in requests if path == "/v1/recall"]
    assert recalls[0]["scope_ids"] == [str(native_fixture["foreign"].scope_id)]
    assert recalls[1]["scope_ids"] == [str(native_fixture["identity"].scope_id)]
    assert recalls[1]["max_items"] == 8 and recalls[1]["token_budget"] == 8000
    assert recalls[1]["query"] == "report locale"
    assert recalls[1]["required_memory_refs"] == []
    assert detail["recall_mapping"][0]["memory_id"] in native_fixture["stored"]
    prompts = dict(bridge.prompts)
    assert case.question not in prompts["retention"]
    assert all(event.text not in prompts["recall_query"] for event in case.events)
    assert case.events[1].text in prompts["pg_agmemory"]
    assert case.events[0].text not in prompts["pg_agmemory"]
    for prompt in prompts.values():
        assert all(label not in prompt for label in (
            "expected_answer", "expected_keep_ids", "required_source_ids", "forbidden_answers",
        ))
    poisoned_gold = case.model_copy(update={
        "expected_answer": "PRIVATE_GOLD_CANARY", "expected_keep_ids": ("PRIVATE_GOLD_CANARY",),
        "required_source_ids": ("PRIVATE_GOLD_CANARY",),
        "forbidden_answers": ("PRIVATE_GOLD_CANARY",),
    })
    assert runner.recipe.retention_prompt(poisoned_gold) == runner.recipe.retention_prompt(case)
    assert runner.recipe.recall_prompt(poisoned_gold) == runner.recipe.recall_prompt(case)
    assert runner.recipe.answer_prompt(poisoned_gold, ()) == runner.recipe.answer_prompt(case, ())


def test_invalid_retention_does_not_default_purge_or_retry(native_fixture):
    bridge = DecisionBridge(native_fixture["case"], invalid_retention=True)
    measured, detail = memory_case(native_fixture, bridge)
    assert measured.error == "invalid_retention_decision"
    assert measured.answer is None and measured.retention is None
    assert detail["status"] == "failed"
    assert len(bridge.calls) == 1
    assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])
    assert len(native_fixture["stored"]) == len(native_fixture["case"].events)


def test_unknown_purge_stops_native_case_preserves_uncertainty(native_fixture):
    native_fixture["failure"]["purge_unknown"] = True
    bridge = DecisionBridge(native_fixture["case"])
    measured, detail = memory_case(native_fixture, bridge)
    assert measured.error == "commit_outcome_unknown"
    assert measured.answer is None
    assert detail["error"]["outcome_unknown"] is True
    assert detail["rollback_claimed"] is False
    assert detail["stopped_at_phase"] == "forget_purge"
    assert len(bridge.calls) == 1
    purges = [
        body for _, path, body, _ in native_fixture["requests"]
        if path == "/v1/forget" and body["mode"] == "purge"
    ]
    assert len(purges) == 1


def test_foreign_scope_explicit_not_found_denial_remains_supported(native_fixture):
    native_fixture["failure"]["foreign_recall_denied"] = True
    measured, detail = memory_case(native_fixture, DecisionBridge(native_fixture["case"]))
    assert measured.error is None
    assert detail["isolation_denials_verified"] == 2


@pytest.mark.parametrize("leak", ["items", "context", "other_empty_reason"])
def test_foreign_scope_probe_rejects_any_content_or_non_denial_empty_result(native_fixture, leak):
    payload = hidden_recall_payload()
    if leak == "items":
        payload["items"] = [{
            "memory_id": str(native_fixture["sentinel_id"]), "revision": 1,
            "type": "episode", "content": "FOREIGN_PRIVATE_SENTINEL",
            "recorded_at": "2026-08-01T00:00:00Z", "occurred_at": "2026-08-01T00:00:00Z",
        }]
    elif leak == "context":
        payload["context_pack"]["text"] = "FOREIGN_PRIVATE_SENTINEL"
    else:
        payload["empty_reason"] = "budget_exhausted"
    native_fixture["failure"]["foreign_recall_payload"] = payload
    bridge = DecisionBridge(native_fixture["case"])
    measured, detail = memory_case(native_fixture, bridge)
    assert measured.error == "isolation_breach"
    assert detail["stopped_at_phase"] == "rls_probe"
    assert not bridge.calls
    assert not native_fixture["stored"]


def test_native_context_never_substitutes_fixture_oracle_text():
    from types import SimpleNamespace

    case = runner.recipe.pilot_cases()[0]
    memory_id = uuid4()
    result = SimpleNamespace(items=[SimpleNamespace(
        memory_id=memory_id, type="episode", content="Altered Native API content",
        occurred_at=runner.datetime(2026, 8, 2, 9, tzinfo=runner.UTC),
    )])
    with pytest.raises(ValueError, match="unchanged events"):
        runner.returned_context(case, result, {case.events[1].event_id: memory_id})


def test_native_context_rejects_foreign_memory_ids():
    from types import SimpleNamespace

    case = runner.recipe.pilot_cases()[0]
    result = SimpleNamespace(items=[SimpleNamespace(
        memory_id=uuid4(), type="episode", content=case.events[1].text,
        occurred_at=runner.datetime(2026, 8, 2, 9, tzinfo=runner.UTC),
    )])
    with pytest.raises(runner.EvaluationFailure, match="foreign_or_invalid_recall_item"):
        runner.returned_context(case, result, {case.events[1].event_id: uuid4()})


def test_japanese_recall_uses_native_japanese_profile_without_required_references():
    request = runner.recall_request(uuid4(), "日付表記", language="ja")
    assert request.search_profile == "ja-janome-0.5.0-v1"
    assert request.required_memory_refs == []
    assert request.max_items == 8 and request.token_budget == 8000


def test_no_memory_and_recent_window_contexts_are_distinct_and_bounded(tmp_path):
    case = runner.recipe.pilot_cases()[0]
    journal = runner.Journal(tmp_path / "controls")

    class AbstainingBridge(DecisionBridge):
        async def call(self, prompt, **kwargs):
            self.prompts.append((kwargs["phase"], prompt))
            self.calls.append({"call_id": str(len(self.calls) + 1), "usage": None})
            return '{"answer":"","source_event_ids":[],"abstained":true}'

    bridge = AbstainingBridge(case)
    no_memory, _ = asyncio.run(runner.answer_arm(case, "no_memory", (), bridge, journal))
    recent, detail = asyncio.run(runner.answer_arm(
        case, "recent_window", runner.recipe.recent_context(case), bridge, journal,
    ))
    assert no_memory.context_events == ()
    assert recent.context_events == runner.recipe.recent_context(case)
    assert detail["context_prompt_bytes"] <= 2000
    assert case.events[1].text not in bridge.prompts[0][1]
    assert case.events[1].text not in bridge.prompts[1][1]
    assert no_memory.input_tokens is None and recent.input_tokens is None


def test_startup_failure_persists_every_unmeasured_slot_without_zero_scores(tmp_path, monkeypatch):
    from argparse import Namespace

    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setenv("PGAG_AGENT_EVAL_SOURCE_REVISION", "a" * 40)
    monkeypatch.delenv("PGAG_AGENT_EVAL_OWNED_RUN", raising=False)
    journal = runner.Journal(tmp_path / "failed-run")
    summary = asyncio.run(runner.run(Namespace(
        api_url="http://127.0.0.1:58000", bridge=tmp_path / "unused-bridge",
    ), journal))
    assert summary["status"] == "failed"
    assert summary["fatal_error"]["code"] == "owned_run_required"
    assert summary["calls_dispatched"] == 0
    assert summary["failed_or_unmeasured_arms"] == 60
    assert summary["usage"]["input_tokens"] is None
    assert summary["automatic_effects"] is False
    assert summary["benchmark_qualified"] is False
    assert len(list(journal.output.glob("case-*.json"))) == 20
    assert (journal.output / "summary.json").exists()
    assert all(
        item["answer"]["failed"] and item["answer"]["accuracy"] is None
        for item in summary["metrics"]["cases"]
    )


@pytest.fixture(scope="module")
def fixture_signing_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


@pytest.mark.parametrize("runner_ahead_seconds", [0, 1, 29, 30, 31])
def test_signed_native_startup_handles_only_bounded_guest_clock_skew(
    tmp_path, monkeypatch, fixture_signing_key, runner_ahead_seconds,
):
    runner_now = 1_800_000_000
    api_now = runner_now - runner_ahead_seconds

    class APIClock(runner.datetime):
        @classmethod
        def now(cls, tz=None):
            return runner.datetime.fromtimestamp(api_now, tz=tz)

    monkeypatch.setattr(runner.time, "time", lambda: runner_now)
    monkeypatch.setattr(runner.jwt.api_jwt, "datetime", APIClock)
    config_data, _ = owned_environment(tmp_path)
    config_data["api_url"] = "http://192.168.64.2:8000"
    config = runner.OwnedConfig.model_validate(config_data)
    journal = runner.Journal(tmp_path / "startup-auth")
    private_key, public_key = fixture_signing_key
    subject = f"{config.run_id}:foreign-sentinel"
    token = runner.bearer(subject, private_key, config)
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        authorization = request.headers["authorization"]
        assert authorization.startswith("Bearer ")
        assert authorization[7:] == token
        try:
            claims = runner.jwt.decode(
                authorization[7:], public_key, algorithms=["RS256"],
                issuer=config.jwt_issuer, audience=config.jwt_audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except runner.jwt.ImmatureSignatureError:
            return native_response(401, json={
                "code": "unauthenticated", "request_id": str(uuid4()), "retryable": False,
            })
        assert claims == {
            "sub": subject, "iss": config.jwt_issuer, "aud": config.jwt_audience,
            "iat": runner_now - runner.JWT_BACKDATE_SECONDS, "exp": runner_now + 86400,
        }
        return native_response(200, json={
            "api_version": "v1", "service_version": __version__, "schema_version": SCHEMA_VERSION,
        })

    original_client = httpx.AsyncClient

    def http_client(*args, **kwargs):
        assert kwargs["base_url"] == config.api_url
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original_client(*args, **kwargs, transport=httpx.MockTransport(handler))

    # Exercise the SDK's real bearer-header construction, not the fixture's a.b.c stub.
    monkeypatch.setattr(httpx, "AsyncClient", http_client)

    async def connect():
        async with runner.authenticated_client(config, subject, private_key, journal, "sentinel"):
            pass

    if runner_ahead_seconds > runner.JWT_BACKDATE_SECONDS:
        with pytest.raises(runner.MemoryClientError) as failure:
            asyncio.run(connect())
        assert failure.value.error.code == "unauthenticated"
        assert failure.value.error.native_status == 401
        assert failure.value.error.outcome_unknown is False
    else:
        asyncio.run(connect())
    assert requests == [("GET", "/v1/capabilities")]
    events = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert [event["operation"] for event in events] == ["capabilities", "capabilities"]
    assert [event["phase"] for event in events] == [
        "native_intent",
        "native_failure" if runner_ahead_seconds > runner.JWT_BACKDATE_SECONDS
        else "native_completed",
    ]


def test_unbackdated_token_reproduces_one_second_strict_iat_failure(
    tmp_path, monkeypatch, fixture_signing_key,
):
    runner_now = 1_800_000_000

    class APIClock(runner.datetime):
        @classmethod
        def now(cls, tz=None):
            return runner.datetime.fromtimestamp(runner_now - 1, tz=tz)

    monkeypatch.setattr(runner.jwt.api_jwt, "datetime", APIClock)
    config_data, _ = owned_environment(tmp_path)
    config = runner.OwnedConfig.model_validate(config_data)
    private_key, public_key = fixture_signing_key
    token = runner.jwt.encode({
        "sub": "owned-sentinel", "iss": config.jwt_issuer, "aud": config.jwt_audience,
        "iat": runner_now, "exp": runner_now + 86400,
    }, private_key, algorithm="RS256")
    with pytest.raises(runner.jwt.ImmatureSignatureError):
        runner.jwt.decode(
            token, public_key, algorithms=["RS256"],
            issuer=config.jwt_issuer, audience=config.jwt_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )


@pytest.mark.integration
@pytest.mark.parametrize("case_index,query,answer", [
    (0, "report locale", "en-GB"),
    (2, "日付表記", "ISO8601"),
])
def test_real_native_rls_purge_roundtrip_without_model(
    env, api_process, tmp_path, case_index, query, answer,
):
    """Scripted decisions test interoperability; every Native call uses real HTTP/PostgreSQL."""
    case = runner.recipe.pilot_cases()[case_index]
    identity = runner.Provisioned(
        tenant_id=env.tenants[0], principal_id=env.principals[0], scope_id=env.scopes[0],
    )
    foreign = runner.Provisioned(
        tenant_id=env.tenants[1], principal_id=env.principals[1], scope_id=env.scopes[1],
    )
    journal = runner.Journal(tmp_path / "native-integration")

    async def scenario(url):
        config = runner.OwnedConfig(
            format="pgag-agent-eval-owned-v1", run_id="agent-eval-integration01", api_url=url,
            admin_url_hash=hashlib.sha256(env.admin_url.encode()).hexdigest(),
            jwt_issuer=env.settings.jwt_issuer, jwt_audience=env.settings.jwt_audience,
            synthetic_fixture_purge_consent=True,
        )
        sentinel_id = await runner.seed_sentinel(
            config, foreign, env.subjects[1], env.private_key, journal,
        )
        async with runner.authenticated_client(
            config, env.subjects[0], env.private_key, journal, case.case_id,
        ) as client:
            hidden = await client.recall(runner.recall_request(foreign.scope_id, ""))
            assert isinstance(hidden, runner.RecallResult)
            assert hidden.items == [] and hidden.context_pack.text == ""
            assert hidden.empty_reason == "not_found"
            with pytest.raises(runner.MemoryClientError) as denied:
                await client.explain(runner.Explain(memory_id=sentinel_id))
            assert denied.value.error.code == "not_found"
            assert denied.value.error.native_status == 404
            assert not denied.value.error.outcome_unknown
            await runner.verify_isolation(
                client, case.case_id, foreign, sentinel_id, journal,
            )

        bridge = DecisionBridge(case, query=query, answer=answer)
        measured, detail = await runner.memory_arm(
            case, identity, env.subjects[0], foreign, sentinel_id, config,
            env.private_key, bridge, journal,
        )
        assert measured.error is None, detail
        assert detail["status"] == "completed"
        assert detail["isolation_denials_verified"] == 2
        assert detail["purged_objects_verified_absent"] == len(case.events) - 1
        assert detail["forget_preview"]["changed"] is False
        assert detail["forget_purge"]["state"] == "active_store_purged"
        assert detail["forget_purge"]["object_count"] == len(case.events) - 1
        assert measured.context_events == (case.events[1],)
        assert measured.answer.answer == answer
        assert len(bridge.calls) == 3
        assert [phase for phase, _ in bridge.prompts] == [
            "retention", "recall_query", "pg_agmemory",
        ]
        retained_id = UUID(detail["observed_event_ids"][case.events[1].event_id])
        async with runner.authenticated_client(
            config, env.subjects[0], env.private_key, journal, case.case_id,
        ) as client:
            recalled = await client.recall(runner.recall_request(
                identity.scope_id, "", language=case.language,
            ))
            assert [item.memory_id for item in recalled.items] == [retained_id]
            assert recalled.items[0].content == case.events[1].text
            episodes = await client.query_episodes(QueryEpisodes(
                scope_ids=[identity.scope_id], max_items=8,
            ))
            assert [episode.memory_id for episode in episodes.episodes] == [retained_id]
            await runner.verify_isolation(
                client, case.case_id, foreign, sentinel_id, journal,
            )
        async with runner.authenticated_client(
            config, env.subjects[1], env.private_key, journal, "sentinel",
        ) as foreign_client:
            source = await foreign_client.explain(runner.Explain(memory_id=sentinel_id))
            assert "ONLY_FOREIGN_" in source.source.content
            # A wrong-principal probe really sees the sentinel and must fail closed.
            with pytest.raises(runner.EvaluationFailure, match="isolation_breach"):
                await runner.verify_isolation(
                    foreign_client, case.case_id, foreign, sentinel_id, journal,
                )

    with api_process(f"agent-memory-native-{case.language}.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))
