"""Runner unit tests; live Copilot and PostgreSQL are owned by the guest drill."""

import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pg_agmemory import __version__
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.models import MemoryItem, QueryEpisodes
from pg_agmemory.native_client import NativeSettings
from pg_agmemory.service import build_context

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
    {"max_calls": 121}, {"max_calls": 0}, {"tools_allowed": True},
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
        query_policy="legacy-v1", recall_raw=None, keep_event_ids=None,
        bounded_plans=None, answer_event_ids=None,
    ):
        self.calls = []
        self.prompts = []
        self.case = case
        self.invalid_retention = invalid_retention
        self.query = query
        self.answer = answer
        self.query_policy = query_policy
        self.recall_raw = recall_raw
        self.keep_event_ids = (
            [case.events[1].event_id] if keep_event_ids is None else list(keep_event_ids)
        )
        self.bounded_plans = bounded_plans
        self.answer_event_ids = (
            self.keep_event_ids if answer_event_ids is None else list(answer_event_ids)
        )

    async def call(self, prompt, *, case_id, phase):
        self.prompts.append((phase, prompt))
        self.calls.append({
            "call_id": f"{len(self.calls) + 1:06d}", "duration_seconds": 0.01, "usage": None,
        })
        if phase == "retention":
            if self.invalid_retention:
                return '{"keep_ids":[],"forget_ids":[]}'
            return json.dumps({
                "keep_ids": self.keep_event_ids,
                "forget_ids": [
                    event.event_id for event in self.case.events
                    if event.event_id not in self.keep_event_ids
                ],
            })
        if phase.startswith("recall_query_round_"):
            number = int(phase.rsplit("_", 1)[1])
            if self.bounded_plans is not None:
                return self.bounded_plans[number - 1]
            return json.dumps({
                "queries": [{"terms": self.query.split(" ")}] if number == 1 else [],
            }, ensure_ascii=False)
        if phase == "recall_query":
            if self.recall_raw is not None:
                return self.recall_raw
            if self.query_policy == "lexical-v2":
                return json.dumps({"terms": self.query.split(" ")}, ensure_ascii=False)
            return json.dumps({"query": self.query})
        return json.dumps({
            "answer": self.answer, "source_event_ids": self.answer_event_ids,
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
            capabilities = {
                "api_version": "v1", "service_version": __version__,
                "schema_version": SCHEMA_VERSION,
                "required_context": failure.get("required_context", {
                    "retrieval_modes": ["lexical"], "max_refs": 16,
                    "order": "request_order", "budget_policy": "all_required_or_error",
                }),
            }
            if not failure.get("omit_lexical_contract"):
                capabilities["lexical_query"] = failure.get(
                    "lexical_query_contract", runner.query_planning.lexical_query_contract(),
                )
            if (
                failure.get("change_contract_after_startup")
                and sum(path == "/v1/capabilities" for _, path, _, _ in requests) > 2
            ):
                capabilities["lexical_query"] = {"format": "changed-contract"}
            return native_response(200, json=capabilities)
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
            required = {ref["memory_id"] for ref in data["required_memory_refs"]}
            if (required and failure.get("final_error")) or (
                not required and failure.get("search_error")
            ):
                return native_response(404, json={
                    "code": "not_found", "request_id": str(uuid4()), "retryable": False,
                })
            items = [MemoryItem.model_validate({
                "memory_id": memory_id, "revision": 1, "type": "episode",
                "content": source["content"], "recorded_at": source["occurred_at"],
                "occurred_at": source["occurred_at"],
            }) for memory_id, source in stored.items() if not required or memory_id in required]
            if required:
                order = [ref["memory_id"] for ref in data["required_memory_refs"]]
                items.sort(key=lambda item: order.index(str(item.memory_id)))
            elif "search_event_ids_by_query" in failure:
                by_event = {
                    stored[str(item.memory_id)]["source_event_id"]: item for item in items
                }
                items = [
                    by_event[event_id]
                    for event_id in failure["search_event_ids_by_query"][data["query"]]
                ]
            elif "search_event_ids" in failure:
                items = [
                    item for item in items
                    if stored[str(item.memory_id)]["source_event_id"] in failure["search_event_ids"]
                ]
            if failure.get("empty_search") and not required:
                items = []
            item_limit_truncated = len(items) > data["max_items"]
            items = items[:data["max_items"]]
            pack, selected, truncated = build_context(
                items, data["token_budget"], required_count=len(required),
            )
            searches = sum(
                path == "/v1/recall" and body["scope_ids"] == [str(identity.scope_id)]
                for _, path, body, _ in requests
            )
            epoch = 2 if failure.get("search_epoch_change") and searches > 1 else 1
            return native_response(200, json={
                "items": [item.model_dump(mode="json") for item in selected],
                "context_pack": pack,
                "coverage": {
                    "retrieval_complete": True, "synthesis_pending": False,
                    "graph_used": False, "truncated": truncated or item_limit_truncated,
                },
                "consistency": {"access_epoch": 1, "deletion_epoch": epoch},
                "search_profile": data["search_profile"],
                "empty_reason": None if selected else "not_found",
            })
        if request.url.path == "/v1/explain" and data["memory_id"] in stored:
            source = stored[data["memory_id"]]
            return native_response(200, json={
                "memory_id": data["memory_id"], "revision": 1, "type": "episode",
                "source": {
                    "content": source["content"], "occurred_at": source["occurred_at"],
                    "consent_reference": source["consent_reference"],
                },
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


def memory_case(
    fixture, bridge, *, query_policy="legacy-v1", retention_policy="model-purge-v1",
):
    return asyncio.run(runner.memory_arm(
        fixture["case"], fixture["identity"], "owned-subject", fixture["foreign"],
        fixture["sentinel_id"], fixture["config"], b"private-key-not-exported",
        bridge, fixture["journal"],
        query_policy=query_policy,
        retention_policy=retention_policy,
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
        query_policy="legacy-v1", cohort="pilot-v1", retention_policy="model-purge-v1",
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
        async with runner.authenticated_client(
            config, subject, private_key, journal, "sentinel", query_policy="legacy-v1",
        ):
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
@pytest.mark.parametrize("query_policy", ["legacy-v1", "lexical-v2"])
@pytest.mark.parametrize("case_index,query,answer", [
    (0, "report locale", "en-GB"),
    (2, "日付表記", "ISO8601"),
])
def test_real_native_rls_purge_roundtrip_without_model(
    env, api_process, tmp_path, case_index, query, answer, query_policy,
):
    """Scripted decisions test interoperability; every Native call uses real HTTP/PostgreSQL."""
    case = runner.recipe.pilot_cases()[case_index]
    real_native_roundtrip(env, api_process, tmp_path, case, query, answer, query_policy)


def real_native_roundtrip(
    env, api_process, tmp_path, case, query, answer, query_policy, *, retained_event_id=None,
    retention_policy="model-purge-v1",
    final_mutation=None, keep_event_ids=None,
):
    retained_event_id = retained_event_id or case.events[1].event_id
    retained_event = next(event for event in case.events if event.event_id == retained_event_id)
    keep_event_ids = [retained_event_id] if keep_event_ids is None else list(keep_event_ids)
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
            query_policy=query_policy,
        )
        async with runner.authenticated_client(
            config, env.subjects[0], env.private_key, journal, case.case_id,
            query_policy=query_policy,
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

        class NativeDecisionBridge(DecisionBridge):
            async def call(self, prompt, **kwargs):
                raw = await super().call(prompt, **kwargs)
                if kwargs["phase"] == "recall_query_round_2" and final_mutation is not None:
                    planning = json.loads(prompt.split("INPUT=", 1)[1])
                    candidate = UUID(planning["items"][0]["memory_id"])
                    if final_mutation == "purge":
                        async with runner.OwnedMemoryClient(
                            config, runner.bearer(env.subjects[0], env.private_key, config),
                        ) as trusted:
                            await trusted.forget(
                                runner.Forget(
                                    memory_ids=[candidate], mode="purge",
                                    reason="Trusted test purge between search and final read",
                                ),
                                idempotency_key="external-test-final-purge",
                            )
                    else:
                        assert final_mutation == "revoke"
                        with psycopg.connect(env.admin_url) as admin:
                            admin.execute(
                                """DELETE FROM memory.scope_member
                                   WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                                (identity.tenant_id, identity.scope_id, identity.principal_id),
                            )
                return raw

        bridge = NativeDecisionBridge(
            case, query=query, answer=answer, query_policy=query_policy,
            keep_event_ids=keep_event_ids, answer_event_ids=[retained_event_id],
        )
        measured, detail = await runner.memory_arm(
            case, identity, env.subjects[0], foreign, sentinel_id, config,
            env.private_key, bridge, journal,
            query_policy=query_policy,
            retention_policy=retention_policy,
        )
        if final_mutation is not None:
            assert measured.error == "not_found", detail
            assert measured.answer is None and detail["context_available"] is False
            assert detail["context_events"] == []
            assert detail["bounded_retrieval"]["final_validation_calls"] == 1
            assert detail["bounded_retrieval"]["cached_fallback_used"] is False
            assert detail["bounded_retrieval"]["planning_cache_discarded"] is True
            assert len(bridge.calls) == 3
            assert detail["retention_review"]["physical_purges"] == 0
            return
        assert measured.error is None, detail
        assert detail["status"] == "completed"
        assert detail["isolation_denials_verified"] == 2
        assert set(detail["observed_event_ids"]) == {event.event_id for event in case.events}
        assert len(set(detail["observed_event_ids"].values())) == len(case.events)
        assert set(detail["retention"]["keep_ids"]) == set(keep_event_ids)
        assert set(detail["retention"]["forget_ids"]) == (
            {event.event_id for event in case.events} - set(keep_event_ids)
        )
        if retention_policy == "review-v1":
            assert "forget_preview" not in detail and "forget_purge" not in detail
            assert detail["retention_review"]["physical_purges"] == 0
            assert (
                detail["retention_review"]["pending_rows_verified_readable"]
                == len(case.events) - len(keep_event_ids)
            )
        else:
            assert detail["purged_objects_verified_absent"] == len(case.events) - 1
            assert detail["forget_preview"]["changed"] is False
            assert detail["forget_purge"]["state"] == "active_store_purged"
            assert detail["forget_purge"]["object_count"] == len(case.events) - 1
        assert measured.context_events == (retained_event,)
        assert measured.answer.answer == answer
        assert len(bridge.calls) == (4 if query_policy in runner.BOUNDED_QUERY_POLICIES else 3)
        assert [phase for phase, _ in bridge.prompts] == [
            "retention",
            *(["recall_query_round_1", "recall_query_round_2"]
              if query_policy in runner.BOUNDED_QUERY_POLICIES else ["recall_query"]),
            "pg_agmemory",
        ]
        retained_id = UUID(detail["observed_event_ids"][retained_event_id])
        async with runner.authenticated_client(
            config, env.subjects[0], env.private_key, journal, case.case_id,
            query_policy=query_policy,
        ) as client:
            recalled = await client.recall(runner.recall_request(
                identity.scope_id, "", language=case.language,
            ))
            expected_ids = (
                {UUID(value) for value in detail["observed_event_ids"].values()}
                if retention_policy == "review-v1" else {retained_id}
            )
            assert {item.memory_id for item in recalled.items} <= expected_ids
            assert len(recalled.items) <= 8
            if len(case.events) <= 8:
                assert {item.memory_id for item in recalled.items} == expected_ids
            for event in case.events:
                memory_id = UUID(detail["observed_event_ids"][event.event_id])
                if memory_id in expected_ids:
                    source = await client.explain(runner.Explain(memory_id=memory_id))
                    assert source.source.content == event.text
            episodes = await client.query_episodes(QueryEpisodes(
                scope_ids=[identity.scope_id], max_items=8,
            ))
            assert {episode.memory_id for episode in episodes.episodes} <= expected_ids
            assert len(episodes.episodes) <= 8
            if len(case.events) <= 8:
                assert {episode.memory_id for episode in episodes.episodes} == expected_ids
            await runner.verify_isolation(
                client, case.case_id, foreign, sentinel_id, journal,
            )
        async with runner.authenticated_client(
            config, env.subjects[1], env.private_key, journal, "sentinel",
            query_policy=query_policy,
        ) as foreign_client:
            source = await foreign_client.explain(runner.Explain(memory_id=sentinel_id))
            assert "ONLY_FOREIGN_" in source.source.content
            # A wrong-principal probe really sees the sentinel and must fail closed.
            with pytest.raises(runner.EvaluationFailure, match="isolation_breach"):
                await runner.verify_isolation(
                    foreign_client, case.case_id, foreign, sentinel_id, journal,
                )
        if retention_policy == "review-v1":
            records = [json.loads(line) for line in journal.path.read_text().splitlines()]
            assert not any(
                row.get("operation", "").startswith("forget") for row in records
            )

    with api_process(f"agent-memory-native-{case.language}.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))


@pytest.mark.integration
@pytest.mark.parametrize("case_id,query,answer,retained_id", [
    ("unseen-01", "support", "oldest unanswered", "unseen-01-e1"),
    ("unseen-03", "舟灯り", "返信待ち順", "unseen-03-e3"),
])
def test_real_native_unseen_lifecycle_without_model(
    env, api_process, tmp_path, case_id, query, answer, retained_id,
):
    case = next(
        case for case in runner.select_cohort("unseen-synthetic-v1")
        if case.case_id == case_id
    )
    # Author-supplied source literals test HTTP interoperability, not planner quality.
    # This scripted bridge is never used by the real runner.
    real_native_roundtrip(
        env, api_process, tmp_path, case, query, answer, "lexical-v2",
        retained_event_id=retained_id,
    )


def test_legacy_prompt_scoring_prefix_and_case_cohort_remain_byte_identical():
    original = hashlib.sha256(Path(runner.recipe.__file__).read_bytes()).hexdigest()
    assert original == "956b448443d0d47cfe5c84ae19922b4473bc6a1a3105904b219b56f16748cc47"
    assert hashlib.sha256(
        Path(runner.recipe.__file__).read_bytes().split(b"def pilot_report(")[0],
    ).hexdigest() == "5240b3e0ee9385a8f451809570c4dfe45f5db4872fb0a78028def1d96ba4752b"
    assert original != "f5b7120d3cd44235e7165da6151030a4966d964959d66186ee8d81be0b293b13"
    assert hashlib.sha256(
        Path(runner.query_planning.__file__).read_bytes(),
    ).hexdigest() == "47935c8a17cca92c5b9c51300c70e35fbfdef8aee59a4f044b191cc01a10c746"
    from pg_agmemory import retention_review

    bounded_source = Path(runner.bounded_recall.__file__).read_bytes()
    planner_source = b"_PROMPT = (" + bounded_source.split(b"_PROMPT = (", 1)[1].split(
        b"@dataclass(frozen=True)", 1,
    )[0]
    assert hashlib.sha256(planner_source).hexdigest() == (
        "959d841a94182f3f2ac115a9722a8f26c2fa4e0125cad4d4b850bd00a41d0f4f"
    )
    assert hashlib.sha256(
        Path(retention_review.__file__).read_bytes(),
    ).hexdigest() == "406a6892e449c115f82f68f40b88b30d46dfc9ec3cb5d846cb39fd485c13fe4c"
    assert runner.recipe_digest("legacy-v1") == original
    assert runner.recipe_digest() == original
    assert hashlib.sha256(runner.json_bytes([
        case.model_dump() for case in runner.recipe.pilot_cases()
    ])).hexdigest() == "caf75aebb12ae11f8da4b85b7cf08e53cd1eb3b21d9682da1a07a6c0077aa398"
    legacy = runner.query_policy_metadata("legacy-v1")
    assert legacy["recipe_sha256"] == original
    assert legacy["query_planning_contract"] is None
    assert legacy["query_planning_sha256"] is None
    lexical = runner.query_policy_metadata("lexical-v2")
    assert lexical["base_recipe_sha256"] == original
    assert lexical["recipe_sha256"] != original
    assert lexical["recipe_sha256"] == hashlib.sha256(
        runner.json_bytes(lexical["recipe_components"]),
    ).hexdigest()
    assert lexical["query_planning_sha256"] == hashlib.sha256(
        Path(runner.query_planning.__file__).read_bytes(),
    ).hexdigest()
    assert lexical["query_planning_contract"] == runner.query_planning.lexical_query_contract()


@pytest.mark.parametrize("query_policy", ["legacy-v1", "lexical-v2"])
def test_query_policy_uses_question_only_and_keeps_other_prompts_identical(tmp_path, query_policy):
    case = runner.recipe.pilot_cases()[0]
    poisoned = case.model_copy(update={
        "events": (), "expected_answer": "PRIVATE_GOLD_CANARY",
        "expected_keep_ids": ("PRIVATE_GOLD_CANARY",),
        "required_source_ids": ("PRIVATE_GOLD_CANARY",),
        "forbidden_answers": ("PRIVATE_GOLD_CANARY",),
    })
    journal = runner.Journal(tmp_path / "query-prompts")
    bridge = DecisionBridge(case, query_policy=query_policy)
    compiled, detail = asyncio.run(runner.plan_recall_query(
        poisoned, bridge, journal, query_policy,
    ))
    expected_prompt = (
        runner.recipe.recall_prompt(case) if query_policy == "legacy-v1"
        else runner.query_planning.lexical_query_prompt(case.question, "simple-v1")
    )
    assert bridge.prompts == [("recall_query", expected_prompt)]
    assert compiled == "report locale" and detail["compiled_query"] == compiled
    assert "PRIVATE_GOLD_CANARY" not in bridge.prompts[0][1]
    assert all(event.text not in bridge.prompts[0][1] for event in case.events)
    assert detail["query_policy"] == query_policy
    assert detail["query_plan"] == (
        {"terms": ["report", "locale"]} if query_policy == "lexical-v2" else None
    )
    frozen_prompts = runner.fixed_prompt_hashes((case,))[case.case_id]
    assert frozen_prompts == {
        "retention": hashlib.sha256(runner.recipe.retention_prompt(case).encode()).hexdigest(),
        "no_memory": hashlib.sha256(runner.recipe.answer_prompt(case, ()).encode()).hexdigest(),
        "recent_window": hashlib.sha256(runner.recipe.answer_prompt(
            case, runner.recipe.recent_context(case),
        ).encode()).hexdigest(),
    }


def test_v2_query_plan_compiles_once_and_is_private_causality_evidence(native_fixture):
    bridge = DecisionBridge(native_fixture["case"], query_policy="lexical-v2")
    measured, detail = memory_case(native_fixture, bridge, query_policy="lexical-v2")
    assert measured.error is None
    assert detail["query_policy"] == "lexical-v2"
    assert detail["query_plan"] == {"terms": ["report", "locale"]}
    assert json.loads(detail["query_plan_raw"]) == detail["query_plan"]
    assert detail["compiled_query"] == "report locale"
    assert detail["recall_decision"] == {"query": "report locale"}
    assert len(bridge.calls) == 3
    owner_queries = [
        body for _, path, body, _ in native_fixture["requests"]
        if path == "/v1/recall"
        and body["scope_ids"] == [str(native_fixture["identity"].scope_id)]
    ]
    assert len(owner_queries) == 1 and owner_queries[0]["query"] == "report locale"
    events = [
        json.loads(line) for line in native_fixture["journal"].path.read_text().splitlines()
    ]
    plans = [event for event in events if event["phase"] == "query_plan_compiled"]
    assert len(plans) == 1
    assert plans[0]["query_plan_raw"] == detail["query_plan_raw"]
    assert plans[0]["compiled_query"] == owner_queries[0]["query"]
    assert native_fixture["journal"].path.stat().st_mode & 0o077 == 0
    compiled_at = next(
        i for i, event in enumerate(events) if event["phase"] == "query_plan_compiled"
    )
    recall_at = next(
        i for i, event in enumerate(events)
        if event["phase"] == "native_intent" and event["operation"] == "recall"
    )
    assert compiled_at < recall_at
    retained = native_fixture["case"].events[1]
    prompts = dict(bridge.prompts)
    assert prompts["retention"] == runner.recipe.retention_prompt(native_fixture["case"])
    assert prompts["pg_agmemory"] == runner.recipe.answer_prompt(
        native_fixture["case"], (retained,),
    )


@pytest.mark.parametrize("raw", [
    '{"terms":[]}', '{"terms":["report locale"]}', '{"terms":["REPORT","report"]}',
    '{"terms":["one","two","three","four"]}', '{"query":"report locale"}',
    '{"terms":["report"],"extra":true}', '{"terms":["report"],"terms":["locale"]}',
    '{"terms":[NaN]}',
])
def test_v2_invalid_plan_stops_without_retry_browse_or_broadening(native_fixture, raw):
    bridge = DecisionBridge(
        native_fixture["case"], query_policy="lexical-v2", recall_raw=raw,
    )
    measured, detail = memory_case(native_fixture, bridge, query_policy="lexical-v2")
    assert measured.error == "invalid_query_plan"
    assert measured.answer is None and detail["rollback_claimed"] is False
    assert detail["stopped_at_phase"] == "recall_decision"
    assert [phase for phase, _ in bridge.prompts] == ["retention", "recall_query"]
    assert not any(
        path == "/v1/recall" and body["scope_ids"] == [str(native_fixture["identity"].scope_id)]
        for _, path, body, _ in native_fixture["requests"]
    )
    events = [
        json.loads(line) for line in native_fixture["journal"].path.read_text().splitlines()
    ]
    raw_events = [event for event in events if event["phase"] == "query_plan_received"]
    assert len(raw_events) == 1 and raw_events[0]["raw_response"] == raw
    assert not any(event["phase"] == "query_plan_compiled" for event in events)


@pytest.mark.parametrize("changed", [None, {"format": "different-contract"}])
def test_v2_capability_mismatch_prevents_model_dispatch_and_observe(native_fixture, changed):
    native_fixture["failure"]["lexical_query_contract"] = changed
    bridge = DecisionBridge(native_fixture["case"], query_policy="lexical-v2")
    measured, detail = memory_case(native_fixture, bridge, query_policy="lexical-v2")
    assert measured.error == "lexical_query_contract_mismatch"
    assert detail["stopped_at_phase"] == "native_connect"
    assert not bridge.calls and not native_fixture["stored"]
    assert all(path == "/v1/capabilities" for _, path, _, _ in native_fixture["requests"])


def test_v2_contract_is_rechecked_before_purge_without_model_retry(native_fixture):
    native_fixture["failure"]["change_contract_after_startup"] = True
    bridge = DecisionBridge(native_fixture["case"], query_policy="lexical-v2")
    measured, detail = memory_case(native_fixture, bridge, query_policy="lexical-v2")
    assert measured.error == "lexical_query_contract_mismatch"
    assert detail["stopped_at_phase"] == "query_contract_before_purge"
    assert len(bridge.calls) == 1
    assert len(native_fixture["stored"]) == len(native_fixture["case"].events)
    assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])


def test_v2_contract_comparison_does_not_coerce_boolean_to_integer(native_fixture):
    altered = runner.query_planning.lexical_query_contract()
    altered["english_stemming"] = 0
    native_fixture["failure"]["lexical_query_contract"] = altered
    bridge = DecisionBridge(native_fixture["case"], query_policy="lexical-v2")
    measured, _ = memory_case(native_fixture, bridge, query_policy="lexical-v2")
    assert measured.error == "lexical_query_contract_mismatch"
    assert not bridge.calls and not native_fixture["stored"]


def test_v2_run_checks_real_capability_before_any_control_model_dispatch(
    tmp_path, native_fixture, transport, monkeypatch,
):
    from argparse import Namespace

    _, environment = owned_environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setenv("PGAG_AGENT_EVAL_SOURCE_REVISION", "a" * 40)
    key_path = tmp_path / "fixture-key"
    key_path.write_bytes(b"unit-test-bearer-is-stubbed")
    key_path.chmod(0o600)
    monkeypatch.setenv("PGAG_AGENT_EVAL_JWT_PRIVATE_KEY_FILE", str(key_path))
    native_fixture["failure"]["omit_lexical_contract"] = True

    async def provision_without_database(*args):
        return native_fixture["foreign"]

    monkeypatch.setattr(runner, "provision", provision_without_database)
    journal = runner.Journal(tmp_path / "v2-failed-startup")
    summary = asyncio.run(runner.run(Namespace(
        api_url=native_fixture["config"].api_url, bridge=transport.directory,
        query_policy="lexical-v2", cohort="pilot-v1", retention_policy="model-purge-v1",
    ), journal))
    assert summary["fatal_error"]["code"] == "lexical_query_contract_mismatch"
    assert summary["calls_dispatched"] == 0
    assert summary["query_policy"] == "lexical-v2"
    assert summary["evaluation_cohort"]["held_out"] is False
    assert not list(transport.queue.iterdir())
    assert all(path == "/v1/capabilities" for _, path, _, _ in native_fixture["requests"])


def test_explicit_legacy_replay_does_not_require_new_capability(native_fixture):
    native_fixture["failure"]["omit_lexical_contract"] = True
    measured, detail = memory_case(
        native_fixture, DecisionBridge(native_fixture["case"]), query_policy="legacy-v1",
    )
    assert measured.error is None
    assert detail["query_policy"] == "legacy-v1"
    assert detail["query_plan"] is None
    assert detail["recall_decision"] == {"query": "report locale"}


@pytest.mark.parametrize("option,expected", [
    ([], "lexical-v2"),
    (["--query-policy", "lexical-v2"], "lexical-v2"),
    (["--query-policy", "legacy-v1"], "legacy-v1"),
    (["--query-policy", "bounded-lexical-v3"], "bounded-lexical-v3"),
    (["--query-policy", "bounded-lexical-v4"], "bounded-lexical-v4"),
])
def test_runner_cli_versioned_query_policy(tmp_path, monkeypatch, capsys, option, expected):
    selected = []

    async def capture_arguments(args, journal):
        selected.append(args.query_policy)
        assert args.cohort == "pilot-v1"
        assert args.retention_policy == (
            "review-v1" if args.query_policy in runner.BOUNDED_QUERY_POLICIES else "model-purge-v1"
        )
        return {"status": "completed", "calls_dispatched": 0}

    monkeypatch.setattr(runner, "run", capture_arguments)
    monkeypatch.setenv("PGAG_AGENT_EVAL_API_URL", "http://127.0.0.1:58000")
    monkeypatch.setattr(sys, "argv", [
        "evaluate-agent-memory.py", "--output", str(tmp_path / "cli-output"),
        "--bridge", str(tmp_path / "cli-bridge"), *option,
    ])
    assert runner.main() == 0
    assert selected == [expected]
    assert json.loads(capsys.readouterr().out)["calls_dispatched"] == 0


@pytest.mark.parametrize("option,accepted", [
    ([], True), (["--query-policy", "lexical-v2"], True),
    (["--query-policy", "legacy-v1"], True), (["--query-policy", "unknown"], False),
    (["--wrong-flag", "lexical-v2"], False), (["--query-policy"], False),
    (["--cohort", "pilot-v1"], True), (["--cohort", "unseen-synthetic-v1"], True),
    (["--cohort", "distractor-synthetic-v1"], True),
    (["--cohort", "distractor-synthetic-v1", "--query-policy", "bounded-lexical-v3",
      "--retention-policy", "review-v1"], True),
    (["--query-policy", "bounded-lexical-v4"], True),
    (["--cohort", "distractor-synthetic-v1", "--query-policy", "bounded-lexical-v4",
      "--retention-policy", "review-v1"], True),
    (["--query-policy", "bounded-lexical-v4", "--retention-policy", "model-purge-v1"], True),
    (["--query-policy", "bounded-lexical-v4", "--query-policy", "bounded-lexical-v3"], False),
    (["--query-policy", "bounded-lexical-v5"], False),
    (["--cohort", "distractor-synthetic-v1", "--cohort", "pilot-v1"], False),
    (["--cohort", "unseen-synthetic-v1", "--query-policy", "lexical-v2"], True),
    (["--query-policy", "legacy-v1", "--cohort", "pilot-v1"], True),
    (["--cohort", "unknown"], False), (["--cohort", "/arbitrary/data.json"], False),
    (["--cohort", "pilot-v1", "--cohort", "pilot-v1"], False),
    (["--query-policy", "legacy-v1", "--query-policy", "lexical-v2"], False),
    (["--cohort", "--query-policy"], False),
    (["--cohort", "--query-policy", "lexical-v2", "pilot-v1"], False),
    (["--cohort"], False),
    (["--query-policy", "bounded-lexical-v3", "--retention-policy", "review-v1"], True),
    (["--retention-policy", "review-v1", "--query-policy", "bounded-lexical-v3",
      "--cohort", "unseen-synthetic-v1"], True),
    (["--retention-policy", "model-purge-v1"], True),
    (["--retention-policy", "review-v1"], True),
    (["--retention-policy", "review-v1", "--retention-policy", "review-v1"], False),
    (["--retention-policy", "approve"], False),
    (["--retention-policy", "--cohort"], False),
])
def test_owned_shell_accepts_only_versioned_query_policy_without_starting_guests(option, accepted):
    result = subprocess.run(
        [
            "bash", str(ROOT / "scripts/evaluate-agent-memory-containers.sh"),
            f"query-policy-parser-test-{uuid4().hex}",
            "gpt-6-astra", "high", "--allow-copilot", *option,
        ],
        cwd=ROOT, env={**os.environ, "PGAG_DATABASE_URL": "forbidden-parser-test-target"},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 2
    assert ("external_database_target_forbidden" in result.stderr) is accepted
    if not accepted:
        assert "Usage:" in result.stderr


@pytest.mark.parametrize("cohort", runner.COHORTS)
@pytest.mark.parametrize("query_policy", runner.QUERY_POLICIES)
def test_cohort_metadata_binds_dataset_scorer_and_query_policy(cohort, query_policy):
    from pg_agmemory import agent_evaluation_distractor, agent_evaluation_unseen

    cases = runner.select_cohort(cohort)
    metadata = runner.cohort_metadata(cohort, cases, query_policy)
    assert metadata["cohort_id"] == metadata["evaluation_cohort"]["id"] == cohort
    assert metadata["dataset_sha256"] == metadata["cases_sha256"] == hashlib.sha256(
        runner.json_bytes([case.model_dump() for case in cases]),
    ).hexdigest()
    assert metadata["scorer_source_sha256"] == hashlib.sha256(
        Path(runner.recipe.__file__).read_bytes(),
    ).hexdigest()
    sources = {
        "pilot-v1": runner.recipe,
        "unseen-synthetic-v1": agent_evaluation_unseen,
        "distractor-synthetic-v1": agent_evaluation_distractor,
    }
    assert metadata["dataset_source_sha256"] == hashlib.sha256(
        Path(sources[cohort].__file__).read_bytes(),
    ).hexdigest()
    assert metadata["protected_prompt_case_scoring_source_sha256"] == hashlib.sha256(
        Path(runner.recipe.__file__).read_bytes().split(b"def pilot_report(")[0],
    ).hexdigest()
    assert metadata["recipe_sha256"] == hashlib.sha256(
        runner.json_bytes(metadata["recipe_components"]),
    ).hexdigest()
    assert metadata["recipe_digest_format"] == "pgag-agent-memory-cohort-recipe-v4"
    assert metadata["query_recipe_sha256"] == runner.query_policy_metadata(
        query_policy,
    )["recipe_sha256"]
    expected_provenance = {
        "id": cohort,
        "kind": "known_synthetic_regression_cohort", "known_cohort_reuse": True,
        "held_out": False, "held_out_external": False, "blinded_real_world": False,
        "first_use_in_owned_run": False, "first_use_scope": "not_claimed",
        "prior_model_exposure_verified": False,
        "baseline_revision": "9c84c7f" if cohort == "pilot-v1" else None,
    }
    if cohort == "distractor-synthetic-v1":
        expected_provenance.update({
            "kind": "synthetic_stress_cohort", "known_cohort_reuse": None,
            "first_use_in_owned_run": None, "first_use_scope": "requires_external_run_history",
        })
        for field in ("known_cohort_reuse", "first_use_in_owned_run"):
            assert metadata["evaluation_cohort"][field] is None
            for claim in (True, False):
                with pytest.raises(runner.EvaluationFailure, match="cohort_metadata_mismatch"):
                    runner.verify_cohort_metadata(
                        metadata | {
                            "evaluation_cohort": metadata["evaluation_cohort"] | {field: claim},
                        },
                        cohort, cases, query_policy,
                    )
    assert metadata["evaluation_cohort"] == expected_provenance
    runner.verify_cohort_metadata(metadata, cohort, cases, query_policy)
    for field in (
        "dataset_sha256", "cases_sha256", "scorer_source_sha256", "recipe_sha256",
        "query_recipe_sha256", "dataset_source_sha256",
        "protected_prompt_case_scoring_source_sha256",
    ):
        with pytest.raises(runner.EvaluationFailure, match="cohort_metadata_mismatch"):
            runner.verify_cohort_metadata(
                metadata | {field: "0" * 64}, cohort, cases, query_policy,
            )


def test_fixed_cohort_selection_rejects_paths_or_modified_dataset():
    for value in ("unknown", "/private/data.json", "../unseen-synthetic-v1", ""):
        with pytest.raises(runner.EvaluationFailure, match="invalid_cohort"):
            runner.select_cohort(value)
    cases = runner.select_cohort("unseen-synthetic-v1")
    altered = (cases[0].model_copy(update={"question": "tampered question"}), *cases[1:])
    with pytest.raises(runner.EvaluationFailure, match="cohort_dataset_mismatch"):
        runner.cohort_metadata("unseen-synthetic-v1", altered, "lexical-v2")
    with pytest.raises(runner.EvaluationFailure, match="cohort_dataset_mismatch"):
        runner.cohort_metadata("pilot-v1", cases, "lexical-v2")
    original_ids = {case.case_id for case in runner.select_cohort("pilot-v1")}
    assert not original_ids & {case.case_id for case in cases}


def test_unseen_planning_uses_only_question_with_no_corpus_or_gold(tmp_path):
    journal = runner.Journal(tmp_path / "unseen-query-only")
    for case in runner.select_cohort("unseen-synthetic-v1"):
        poisoned = case.model_copy(update={
            "events": (), "expected_answer": "PRIVATE_GOLD_CANARY",
            "expected_keep_ids": ("PRIVATE_GOLD_CANARY",),
            "required_source_ids": ("PRIVATE_GOLD_CANARY",),
            "forbidden_answers": ("PRIVATE_GOLD_CANARY",),
        })
        bridge = DecisionBridge(
            case, query_policy="lexical-v2", recall_raw='{"terms":["synthetic"]}',
        )
        compiled, detail = asyncio.run(runner.plan_recall_query(
            poisoned, bridge, journal, "lexical-v2",
        ))
        assert compiled == "synthetic" and len(bridge.calls) == 1
        assert bridge.prompts == [("recall_query", runner.query_planning.lexical_query_prompt(
            case.question, runner.lexical_profile(case.language),
        ))]
        assert "PRIVATE_GOLD_CANARY" not in bridge.prompts[0][1]
        assert all(event.text not in bridge.prompts[0][1] for event in case.events)
        assert detail["search_profile"] == runner.lexical_profile(case.language)


@pytest.mark.parametrize("cohort", runner.COHORTS)
def test_selected_cohort_persists_all_failed_slots_before_any_dispatch(
    tmp_path, monkeypatch, cohort,
):
    from argparse import Namespace

    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setenv("PGAG_AGENT_EVAL_SOURCE_REVISION", "a" * 40)
    monkeypatch.delenv("PGAG_AGENT_EVAL_OWNED_RUN", raising=False)
    journal = runner.Journal(tmp_path / "failed-cohort-run")
    summary = asyncio.run(runner.run(Namespace(
        api_url="http://127.0.0.1:58000", bridge=tmp_path / "unused-bridge",
        query_policy="lexical-v2", cohort=cohort, retention_policy="model-purge-v1",
    ), journal))
    assert summary["calls_dispatched"] == 0
    assert summary["failed_or_unmeasured_arms"] == 60
    assert summary["cohort_id"] == cohort
    assert summary["source_code_git_sha"] == "a" * 40
    cases = runner.select_cohort(cohort)
    assert {row["case_id"] for row in summary["metrics"]["cases"]} == {
        case.case_id for case in cases
    }
    for arm in runner.ARMS:
        assert summary["metrics"]["arms"][arm]["failures"] == 20
        assert summary["metrics"]["arms"][arm]["accuracy_denominator"] == 20
        assert summary["metrics"]["arms"][arm]["valid_answer_accuracy"] is None
        assert summary["evidence_coverage"]["arms"][arm]["valid_denominator"] == 0
        assert (
            summary["evidence_coverage"]["arms"][arm]["retrieved_required_source_recall"] is None
        )
    for case in cases:
        record = json.loads((journal.output / f"case-{case.case_id}.json").read_text())
        assert record["metadata"]["cohort_id"] == cohort
        assert record["metadata"]["dataset_sha256"] == summary["dataset_sha256"]
        assert all(arm["call_ids"] == [] for arm in record["arms"].values())
        assert all(
            arm["evidence_coverage"]["retrieved_required_source_recall"] is None
            for arm in record["arms"].values()
        )


@pytest.mark.parametrize("cohort", runner.COHORTS)
def test_runner_cli_explicit_cohort_keeps_query_default(tmp_path, monkeypatch, capsys, cohort):
    selected = []

    async def capture_arguments(args, journal):
        selected.append((args.cohort, args.query_policy, args.retention_policy))
        return {"status": "completed", "calls_dispatched": 0}

    monkeypatch.setattr(runner, "run", capture_arguments)
    monkeypatch.setenv("PGAG_AGENT_EVAL_API_URL", "http://127.0.0.1:58000")
    monkeypatch.setattr(sys, "argv", [
        "evaluate-agent-memory.py", "--output", str(tmp_path / "cohort-cli-output"),
        "--bridge", str(tmp_path / "cohort-cli-bridge"), "--cohort", cohort,
    ])
    assert runner.main() == 0
    assert selected == [(cohort, "lexical-v2", "model-purge-v1")]
    assert json.loads(capsys.readouterr().out)["cohort_id"] == cohort


@pytest.mark.parametrize("value", ["unknown", "/arbitrary/dataset.json", "--query-policy"])
def test_runner_cli_rejects_invalid_cohort_before_output_creation(tmp_path, monkeypatch, value):
    output = tmp_path / "must-not-create"
    monkeypatch.setattr(sys, "argv", [
        "evaluate-agent-memory.py", "--output", str(output), "--bridge", str(tmp_path / "bridge"),
        "--cohort", value,
    ])
    with pytest.raises(SystemExit) as failure:
        runner.main()
    assert failure.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("option", [
    ["--cohort", "distractor-synthetic-v1", "--cohort", "distractor-synthetic-v1"],
    ["--cohort=distractor-synthetic-v1", "--cohort=pilot-v1"],
    ["--query-policy", "bounded-lexical-v3", "--query-policy", "lexical-v2"],
    ["--query-policy", "bounded-lexical-v4", "--query-policy", "bounded-lexical-v4"],
    ["--query-policy=bounded-lexical-v4", "--query-policy=bounded-lexical-v3"],
    ["--query-policy", "bounded-lexical-v5"],
    ["--retention-policy", "review-v1", "--retention-policy", "model-purge-v1"],
    ["--cohort", "distractor-synthetic-v1", "--wrong-flag", "value"],
    ["--coho", "distractor-synthetic-v1"],
])
def test_runner_cli_rejects_duplicate_or_unknown_flags_before_output_creation(
    tmp_path, monkeypatch, option,
):
    output = tmp_path / "must-not-create"
    monkeypatch.setattr(sys, "argv", [
        "evaluate-agent-memory.py", "--output", str(output),
        "--bridge", str(tmp_path / "bridge"), *option,
    ])
    with pytest.raises(SystemExit) as failure:
        runner.main()
    assert failure.value.code == 2
    assert not output.exists()


def test_retrieved_evidence_coverage_is_distinct_from_citation_recall():
    original = runner.recipe.pilot_cases()[0]
    context = (original.events[1], original.events[2])
    case = original.model_copy(update={
        "required_source_ids": tuple(event.event_id for event in context),
    })
    answer = runner.recipe.AnswerDecision(
        answer=case.expected_answer, source_event_ids=[context[0].event_id], abstained=False,
    )
    cited = runner.recipe.score_answer(case, answer, context)
    coverage = runner.evidence_coverage(case, {
        "context_available": True, "context_events": [event.model_dump() for event in context],
        "status": "completed", "answer": answer.model_dump(),
    })
    assert cited.required_source_recall == 0.5
    assert coverage["retrieved_required_source_recall"] == 1.0
    assert coverage["retrieved_required_source_ids"] == sorted(case.required_source_ids)
    assert coverage["missing_required_source_ids"] == []


def test_answer_failure_after_valid_retrieval_preserves_known_evidence_coverage():
    case = runner.recipe.pilot_cases()[0]
    coverage = runner.evidence_coverage(case, {
        "context_available": True, "context_events": [case.events[1].model_dump()],
        "status": "failed", "error": {"code": "invalid_answer_decision"}, "answer": None,
    })
    assert coverage["context_available"] is True
    assert coverage["retrieved_required_source_recall"] == 1.0


@pytest.mark.parametrize("detail", [
    {}, {"context_events": []}, {"context_available": False, "context_events": []},
    {"status": "failed", "context_available": False, "error": {"code": "native_api_unavailable"}},
])
def test_evidence_coverage_before_retrieval_is_unknown_not_zero(detail):
    coverage = runner.evidence_coverage(runner.recipe.pilot_cases()[0], detail)
    assert coverage["retrieved_required_source_recall"] is None
    assert coverage["retrieved_required_source_ids"] is None
    assert coverage["missing_required_source_ids"] is None
    assert coverage["context_available"] is False


@pytest.mark.parametrize("invalid_context", ["foreign", "changed", "duplicate", "missing"])
def test_evidence_coverage_rejects_unvalidated_context(invalid_context):
    case = runner.recipe.pilot_cases()[0]
    event = case.events[1].model_dump()
    contexts = {
        "foreign": [runner.recipe.pilot_cases()[1].events[1].model_dump()],
        "changed": [event | {"text": "fabricated content"}],
        "duplicate": [event, event],
        "missing": None,
    }
    coverage = runner.evidence_coverage(case, {
        "context_available": True, "context_events": contexts[invalid_context],
    })
    assert coverage["retrieved_required_source_recall"] is None
    assert coverage["error"] == "invalid_evidence_context"


def test_known_empty_retrieval_is_zero_but_unanswerable_coverage_is_not_applicable():
    cases = runner.recipe.pilot_cases()
    empty = {"context_available": True, "context_events": []}
    assert runner.evidence_coverage(cases[0], empty)["retrieved_required_source_recall"] == 0
    revoked = next(case for case in cases if case.expected_answer is None)
    coverage = runner.evidence_coverage(revoked, empty)
    assert coverage["applicable"] is False
    assert coverage["retrieved_required_source_recall"] is None


def test_evidence_coverage_aggregate_has_explicit_known_denominator():
    cases = runner.recipe.pilot_cases()
    details = {}
    for index, case in enumerate(cases):
        arms = {}
        for arm in runner.ARMS:
            context = [case.events[1].model_dump()] if index == 0 else []
            arms[arm] = {"evidence_coverage": runner.evidence_coverage(case, {
                "context_available": index == 0, "context_events": context,
            })}
        details[case.case_id] = {"arms": arms}
    summary = runner.evidence_coverage_summary(cases, details)
    for arm in runner.ARMS:
        assert summary["arms"][arm] == {
            "retrieved_required_source_recall": 1.0, "valid_denominator": 1,
            "answerable_cases": 16, "unknown_answerable_cases": 15, "not_applicable_cases": 4,
        }


def test_policy_call_budgets_do_not_expand_default_transport(transport):
    assert runner.logical_call_limit("legacy-v1") == 100
    assert runner.logical_call_limit("lexical-v2") == 100
    assert runner.logical_call_limit("bounded-lexical-v3") == 120
    assert runner.logical_call_limit("bounded-lexical-v4") == 120
    private_json(transport.metadata_path, transport.metadata.model_dump() | {"max_calls": 120})
    default = runner.FileBridge(
        transport.directory, transport.journal, run_id="agent-eval-12345678",
    )
    default.calls = [{"call_id": str(index)} for index in range(100)]
    with pytest.raises(runner.EvaluationFailure, match="llm_call_limit"):
        asyncio.run(default.call("prompt", case_id="case-01", phase="answer"))
    bounded = runner.FileBridge(
        transport.directory, transport.journal, run_id="agent-eval-12345678", max_calls=120,
    )
    bounded.calls = [{"call_id": str(index)} for index in range(120)]
    with pytest.raises(runner.EvaluationFailure, match="llm_call_limit"):
        asyncio.run(bounded.call("prompt", case_id="case-01", phase="answer"))
    assert not list(transport.queue.iterdir())


@pytest.mark.parametrize("query_policy", ["legacy-v1", "lexical-v2"])
def test_review_with_single_query_preserves_rows_and_withholds_pending_context(
    native_fixture, query_policy,
):
    case = native_fixture["case"]
    measured, detail = memory_case(
        native_fixture, DecisionBridge(case, query_policy=query_policy),
        query_policy=query_policy, retention_policy="review-v1",
    )
    assert measured.error is None, detail
    assert measured.context_events == (case.events[1],)
    assert len(native_fixture["stored"]) == len(case.events)
    assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])
    assert detail["retention_review"]["purge_authorized"] is False
    assert detail["retention_review"]["deferred_count"] == len(case.events) - 1
    assert detail["recall_coverage"]["retrieval_complete"] is False
    assert detail["recall_coverage"]["truncated"] is True
    assert detail["recall_projection"] == "local_pending_review_exclusion_not_server_purge"
    assert all(
        event.text not in detail["recall_context_pack"]["text"]
        for event in case.events if event != case.events[1]
    )


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
def test_bounded_review_profile_records_module_hashes_and_honest_budget(query_policy):
    from pg_agmemory import retention_review

    metadata = runner.cohort_metadata(
        "unseen-synthetic-v1", runner.select_cohort("unseen-synthetic-v1"),
        query_policy, "review-v1",
    )
    assert metadata["retention_policy"] == "review-v1"
    assert metadata["query_policy"] == query_policy
    assert metadata["retention_review_sha256"] == hashlib.sha256(
        Path(retention_review.__file__).read_bytes(),
    ).hexdigest()
    assert metadata["bounded_recall_sha256"] == hashlib.sha256(
        Path(runner.bounded_recall.__file__).read_bytes(),
    ).hexdigest()
    assert metadata["recipe_components"]["logical_model_call_limit"] == 120
    assert metadata["evaluation_cohort"]["known_cohort_reuse"] is True
    assert metadata["evaluation_cohort"]["first_use_in_owned_run"] is False
    assert metadata["evaluation_cohort"]["held_out"] is False
    assert metadata["recipe_digest_format"] == "pgag-agent-memory-cohort-recipe-v4"
    runner.verify_cohort_metadata(
        metadata, "unseen-synthetic-v1", runner.select_cohort("unseen-synthetic-v1"),
        query_policy, "review-v1",
    )
    implicit = runner.cohort_metadata(
        "unseen-synthetic-v1", runner.select_cohort("unseen-synthetic-v1"),
        query_policy,
    )
    assert implicit == metadata


def test_v4_recipe_binds_selection_without_rewriting_prior_recipe_shapes():
    lexical = runner.query_policy_metadata("lexical-v2")
    helper_sha = hashlib.sha256(Path(runner.bounded_recall.__file__).read_bytes()).hexdigest()
    expected_v3 = lexical["recipe_components"] | {
        "format": "pgag-agent-memory-bounded-query-recipe-v3",
        "query_policy": "bounded-lexical-v3", "bounded_recall_sha256": helper_sha,
        "planning_rounds": 2, "search_calls_maximum": 4,
        "final_required_reference_calls_maximum": 1,
        "max_items": 8, "context_budget_bytes": 8000,
        "fixed_temporal_anchors": "after_observation_before_planning",
    }
    v3 = runner.query_policy_metadata("bounded-lexical-v3")
    assert v3["recipe_components"] == expected_v3
    v4 = runner.query_policy_metadata("bounded-lexical-v4")
    assert v4["recipe_components"] == expected_v3 | {
        "format": "pgag-agent-memory-bounded-query-recipe-v4",
        "query_policy": "bounded-lexical-v4", "evidence_selection": "round-robin-v1",
    }
    assert v4["recipe_digest_format"] == "pgag-agent-memory-bounded-query-recipe-v4"
    assert v4["recipe_sha256"] != v3["recipe_sha256"]
    assert v4["recipe_sha256"] == hashlib.sha256(
        runner.json_bytes(v4["recipe_components"]),
    ).hexdigest()
    assert lexical["bounded_recall_sha256"] is None
    assert "evidence_selection" not in lexical["recipe_components"]
    assert runner.query_policy_metadata("legacy-v1")["recipe_components"] is None
    cases = runner.select_cohort("distractor-synthetic-v1")
    metadata = runner.cohort_metadata("distractor-synthetic-v1", cases, "bounded-lexical-v4")
    assert metadata["query_recipe_components"] == v4["recipe_components"]
    for changed in (
        {"evidence_selection": "first-admitted-v1"},
        {"query_policy": "bounded-lexical-v3"},
        {"bounded_recall_sha256": "0" * 64},
    ):
        with pytest.raises(runner.EvaluationFailure, match="cohort_metadata_mismatch"):
            runner.verify_cohort_metadata(
                metadata | {"query_recipe_components": v4["recipe_components"] | changed},
                "distractor-synthetic-v1", cases, "bounded-lexical-v4",
            )


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
@pytest.mark.parametrize("required", [
    None,
    {"retrieval_modes": ["lexical"], "max_refs": 7,
     "order": "request_order", "budget_policy": "all_required_or_error"},
    {"retrieval_modes": ["lexical"], "max_refs": True,
     "order": "request_order", "budget_policy": "all_required_or_error"},
    {"retrieval_modes": ["lexical"], "max_refs": 16,
     "order": "ranked", "budget_policy": "all_required_or_error"},
    {"retrieval_modes": ["lexical"], "max_refs": 16,
     "order": "request_order", "budget_policy": "partial_allowed"},
    {"retrieval_modes": ["vector"], "max_refs": 16,
     "order": "request_order", "budget_policy": "all_required_or_error"},
])
def test_bounded_capability_gate_precedes_any_model_or_mutation(
    native_fixture, required, query_policy,
):
    native_fixture["failure"]["required_context"] = required
    bridge = DecisionBridge(native_fixture["case"])
    measured, detail = memory_case(
        native_fixture, bridge, query_policy=query_policy, retention_policy="review-v1",
    )
    assert measured.error == "required_context_contract_mismatch"
    assert detail["stopped_at_phase"] == "native_connect"
    assert not bridge.calls and not native_fixture["stored"]
    assert all(path == "/v1/capabilities" for _, path, _, _ in native_fixture["requests"])


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
def test_bounded_review_cli_requires_explicit_new_flags(
    tmp_path, monkeypatch, capsys, query_policy,
):
    selected = []

    async def capture_arguments(args, journal):
        selected.append((args.query_policy, args.retention_policy, args.cohort))
        return {"status": "completed", "calls_dispatched": 0}

    monkeypatch.setattr(runner, "run", capture_arguments)
    monkeypatch.setenv("PGAG_AGENT_EVAL_API_URL", "http://127.0.0.1:58000")
    monkeypatch.setattr(sys, "argv", [
        "evaluate-agent-memory.py", "--output", str(tmp_path / "explicit-output"),
        "--bridge", str(tmp_path / "bridge"), "--query-policy", query_policy,
        "--retention-policy", "review-v1", "--cohort", "unseen-synthetic-v1",
    ])
    assert runner.main() == 0
    assert selected == [(query_policy, "review-v1", "unseen-synthetic-v1")]
    assert json.loads(capsys.readouterr().out)["calls_dispatched"] == 0


@pytest.mark.parametrize("query_policy,provided,expected", [
    ("legacy-v1", None, "model-purge-v1"),
    ("lexical-v2", None, "model-purge-v1"),
    ("bounded-lexical-v3", None, "review-v1"),
    ("bounded-lexical-v3", "model-purge-v1", "model-purge-v1"),
    ("bounded-lexical-v4", None, "review-v1"),
    ("bounded-lexical-v4", "model-purge-v1", "model-purge-v1"),
    ("lexical-v2", "review-v1", "review-v1"),
])
def test_retention_defaults_do_not_enable_bounded_autopurge(query_policy, provided, expected):
    assert runner.resolve_retention_policy(query_policy, provided) == expected


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
@pytest.mark.parametrize("retention_policy", ["model-purge-v1", "review-v1"])
def test_bounded_two_rounds_use_at_most_five_reads_and_one_fixed_snapshot(
    native_fixture, retention_policy, query_policy,
):
    case = native_fixture["case"]
    plans = [
        '{"queries":[{"terms":["report","locale"]},{"terms":["locale"]}]}',
        '{"queries":[{"terms":["report"]},{"terms":["preferred"]}]}',
    ]
    bridge = DecisionBridge(case, query_policy=query_policy, bounded_plans=plans)
    measured, detail = memory_case(
        native_fixture, bridge, query_policy=query_policy,
        retention_policy=retention_policy,
    )
    assert measured.error is None, detail
    assert len(bridge.calls) == 4
    progress = detail["bounded_retrieval"]
    assert progress["query_policy"] == query_policy
    assert progress["evidence_selection"] == (
        "round-robin-v1" if query_policy == "bounded-lexical-v4" else "first-admitted-v1"
    )
    assert progress["planning_calls"] == 2 and progress["search_calls"] == 4
    assert progress["final_validation_calls"] == 1 and progress["revalidated"] is True
    assert len(progress["read_latency_ms"]) == 5
    reads = [
        body for _, path, body, _ in native_fixture["requests"]
        if path == "/v1/recall"
        and body["scope_ids"] == [str(native_fixture["identity"].scope_id)]
    ]
    assert len(reads) == 5
    assert all(read["as_of"] == read["known_at"] == reads[0]["as_of"] for read in reads)
    assert all(read["max_items"] <= 8 and read["token_budget"] == 8000 for read in reads)
    assert all(read["filters"]["kind"] == "episode" for read in reads)
    assert all(read["required_memory_refs"] == [] and read["query"] for read in reads[:-1])
    assert reads[-1]["query"] == "" and len(reads[-1]["required_memory_refs"]) == 1
    assert measured.context_events == (case.events[1],)
    prompts = dict(bridge.prompts)
    first = prompts["recall_query_round_1"]
    second = prompts["recall_query_round_2"]
    assert all(event.text not in first for event in case.events)
    assert case.events[1].text in second
    assert all(event.text not in second for event in case.events if event != case.events[1])
    assert prompts["pg_agmemory"] == runner.recipe.answer_prompt(case, (case.events[1],))
    if retention_policy == "review-v1":
        assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])
        assert len(native_fixture["stored"]) == len(case.events)
        assert (
            detail["retention_review"]["pending_rows_verified_readable"] == len(case.events) - 1
        )
        assert detail["retention_review"]["physical_purges"] == 0
        assert detail["retention_review"]["deletion_completed"] is False
        assert detail["actor_proposal"] is True


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
@pytest.mark.parametrize("failure,expected", [
    ("final_error", "not_found"), ("search_epoch_change", "recall_epoch_changed"),
    ("search_error", "not_found"),
])
def test_bounded_read_failure_never_uses_cached_answer_context(
    native_fixture, failure, expected, query_policy,
):
    native_fixture["failure"][failure] = True
    bridge = DecisionBridge(
        native_fixture["case"], query_policy=query_policy,
        bounded_plans=[
            '{"queries":[{"terms":["report"]}]}',
            '{"queries":[{"terms":["locale"]}]}',
        ],
    )
    measured, detail = memory_case(
        native_fixture, bridge, query_policy=query_policy, retention_policy="review-v1",
    )
    assert measured.error == expected
    assert measured.answer is None and detail["context_available"] is False
    assert detail["context_events"] == [] and detail["rollback_claimed"] is False
    assert len(bridge.calls) == (2 if failure == "search_error" else 3)
    assert all(phase != "pg_agmemory" for phase, _ in bridge.prompts)
    assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])
    if failure in ("final_error", "search_error"):
        assert detail["bounded_retrieval"]["planning_cache_discarded"] is True


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
@pytest.mark.parametrize("raw", [
    '{"queries":[]}', '{"queries":[{"terms":[]}]}',
    '{"queries":[{"terms":["report"]}],"extra":true}',
    '{"queries":[{"terms":["report"]}],"queries":[]}',
])
def test_invalid_bounded_plan_is_not_retried_or_replaced_with_browse(
    native_fixture, raw, query_policy,
):
    bridge = DecisionBridge(
        native_fixture["case"], query_policy=query_policy,
        bounded_plans=[raw, '{"queries":[]}'],
    )
    measured, detail = memory_case(
        native_fixture, bridge, query_policy=query_policy, retention_policy="review-v1",
    )
    assert measured.error == "invalid_search_plan"
    assert len(bridge.calls) == 2
    assert detail["bounded_retrieval"]["search_calls"] == 0
    assert not any(
        path == "/v1/recall" and body["scope_ids"] == [str(native_fixture["identity"].scope_id)]
        for _, path, body, _ in native_fixture["requests"]
    )


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
def test_bounded_known_empty_result_skips_final_http_and_preserves_known_context(
    native_fixture, query_policy,
):
    native_fixture["failure"]["empty_search"] = True

    class AbstainAfterSearch(DecisionBridge):
        async def call(self, prompt, **kwargs):
            raw = await super().call(prompt, **kwargs)
            if kwargs["phase"] == "pg_agmemory":
                return '{"answer":"","source_event_ids":[],"abstained":true}'
            return raw

    measured, detail = memory_case(
        native_fixture, AbstainAfterSearch(native_fixture["case"]),
        query_policy=query_policy, retention_policy="review-v1",
    )
    assert measured.error is None
    assert detail["context_available"] is True and detail["context_events"] == []
    assert detail["bounded_retrieval"]["final_validation_calls"] == 0
    assert detail["bounded_retrieval"]["final_validation_skipped_empty"] is True
    assert detail["bounded_retrieval"]["cached_fallback_used"] is False


def test_review_reports_proposal_quality_not_physical_deletion():
    cases = runner.recipe.pilot_cases()
    observations = [
        runner.recipe.ArmObservation(
            case_id=case.case_id, arm=arm, answer=None,
            context_events=runner.recipe.recent_context(case) if arm == "recent_window" else (),
            retention=runner.recipe.RetentionDecision(
                keep_ids=[], forget_ids=[event.event_id for event in case.events],
            ) if arm == "pg_agmemory" else None,
            error="answer_unavailable",
        )
        for case in cases for arm in runner.ARMS
    ]
    historical = runner.recipe.pilot_report(observations)
    reported = runner.review_proposal_metrics(historical)
    assert "retention" in historical and "retention" not in reported
    assert reported["retention_proposal_quality"]["unsafe_forget_proposals"] > 0
    assert "unsafe_deleted" not in reported["retention_proposal_quality"]
    for row in reported["cases"]:
        assert "retention" not in row
        if row["arm"] == "pg_agmemory":
            assert row["retention_proposal_quality"]["unsafe_forget_proposals"] > 0
    execution = runner.review_execution_summary({
        case.case_id: {"arms": {"pg_agmemory": {
            "actor_proposal": True,
            "retention_review": {"deferred_count": len(case.events)},
        }}} for case in cases
    })
    assert execution["physical_purges"] == 0 and execution["deletion_completed"] is False
    assert execution["deferred_count"] == sum(len(case.events) for case in cases)


@pytest.mark.integration
@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
@pytest.mark.parametrize("case_index,query,answer", [
    (0, "report locale", "en-GB"), (2, "日付表記", "ISO8601"),
])
def test_real_native_bounded_review_retains_pending_rows_without_model(
    env, api_process, tmp_path, case_index, query, answer, query_policy,
):
    real_native_roundtrip(
        env, api_process, tmp_path, runner.recipe.pilot_cases()[case_index],
        query, answer, query_policy, retention_policy="review-v1",
    )


@pytest.mark.integration
@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
@pytest.mark.parametrize("mutation", ["purge", "revoke"])
def test_real_native_final_refs_observe_current_purge_and_acl_without_cached_fallback(
    env, api_process, tmp_path, mutation, query_policy,
):
    real_native_roundtrip(
        env, api_process, tmp_path, runner.recipe.pilot_cases()[0], "report locale", "en-GB",
        query_policy, retention_policy="review-v1", final_mutation=mutation,
    )


def extended_history_case(language):
    from pg_agmemory.agent_evaluation_distractor import DistractorMemoryCase

    case_id = f"runner-long-{language}"
    events = tuple(
        runner.recipe.Event(
            event_id=f"{case_id}-e{number:02d}",
            occurred_at=f"2026-07-01T08:{number:02d}:00Z",
            text=(
                f"Keep the independent work-note label work{number:02d} for future sessions."
                if language == "en"
                else f"今後も独立した作業ノートのラベルwork{number:02d}を保持する。"
            ) if 3 <= number <= 26 else (
                f"One-shot scratch status {number:02d} has expired."
                if language == "en" else f"一時的な作業状況{number:02d}は期限切れ。"
            ),
        ) for number in range(1, 33)
    )
    target = runner.recipe.Event(
        event_id=events[16].event_id, occurred_at=events[16].occurred_at,
        text=(
            "For future sessions, the runnerlongneedle archive label is FIXTURE32."
            if language == "en" else "今後の検証専用識別子の保管ラベルはFIXTURE32。"
        ),
    )
    return DistractorMemoryCase(
        case_id=case_id, category="project_constraint", language=language,
        events=(*events[:16], target, *events[17:]),
        question=(
            "What is the runnerlongneedle archive label?"
            if language == "en" else "検証専用識別子の保管ラベルは？"
        ),
        expected_answer="FIXTURE32",
        expected_keep_ids=tuple(event.event_id for event in events[2:26]),
        required_source_ids=(target.event_id,),
    )


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
def test_bounded_v4_saturation_selects_followup_evidence_and_revalidates(
    native_fixture, query_policy,
):
    case = extended_history_case("en")
    native_fixture["case"] = case
    initial = case.events[2:10]
    target = case.events[16]
    native_fixture["failure"]["search_event_ids_by_query"] = {
        "fixture initial": [event.event_id for event in initial],
        "fixture alternate": [],
        "fixture followup": [target.event_id],
        "fixture last": [],
    }

    class ContextReader(DecisionBridge):
        async def call(self, prompt, **kwargs):
            raw = await super().call(prompt, **kwargs)
            if kwargs["phase"] == "pg_agmemory" and target.text not in prompt:
                return '{"answer":"","source_event_ids":[],"abstained":true}'
            return raw

    bridge = ContextReader(
        case, query_policy=query_policy, answer="FIXTURE32",
        keep_event_ids=case.expected_keep_ids, answer_event_ids=[target.event_id],
        bounded_plans=[
            '{"queries":[{"terms":["fixture","initial"]},{"terms":["fixture","alternate"]}]}',
            '{"queries":[{"terms":["fixture","followup"]},{"terms":["fixture","last"]}]}',
        ],
    )
    measured, detail = memory_case(
        native_fixture, bridge, query_policy=query_policy, retention_policy="review-v1",
    )
    assert measured.error is None, detail
    expected = (target, *initial[:7]) if query_policy == "bounded-lexical-v4" else initial
    assert measured.context_events == expected
    assert measured.answer.abstained is (query_policy == "bounded-lexical-v3")
    assert len(bridge.calls) == 4
    progress = detail["bounded_retrieval"]
    assert progress["query_policy"] == query_policy
    assert progress["evidence_selection"] == (
        "round-robin-v1" if query_policy == "bounded-lexical-v4" else "first-admitted-v1"
    )
    assert progress["planning_calls"] == 2 and progress["search_calls"] == 4
    assert progress["final_validation_calls"] == 1 and progress["revalidated"] is True
    assert progress["cached_fallback_used"] is False
    expected_ids = [detail["observed_event_ids"][event.event_id] for event in expected]
    assert progress["returned_memory_ids"] == expected_ids
    assert [
        ref["memory_id"] for ref in progress["final_request"]["required_memory_refs"]
    ] == expected_ids
    assert progress["final_request"]["query"] == ""
    assert progress["final_request"]["max_items"] == 8
    assert progress["final_request"]["token_budget"] == 8000
    prompts = dict(bridge.prompts)
    assert all(event.text not in prompts["recall_query_round_1"] for event in case.events)
    assert all(event.text in prompts["recall_query_round_2"] for event in initial)
    assert target.text not in prompts["recall_query_round_2"]
    assert prompts["pg_agmemory"] == runner.recipe.answer_prompt(case, expected)
    assert detail["retention_review"]["pending_rows_verified_readable"] == 8
    assert len(native_fixture["stored"]) == 32
    assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])
    records = [
        json.loads(line) for line in native_fixture["journal"].path.read_text().splitlines()
    ]
    plans = [row for row in records if row["phase"] == "bounded_plan_received"]
    assert len(plans) == 2 and all(row["query_policy"] == query_policy for row in plans)
    searches = [
        row for row in records
        if row["phase"] == "native_completed" and row["operation"] == "bounded_search"
    ]
    assert len(searches) == 4
    assert [item["memory_id"] for item in searches[2]["response"]["items"]] == [
        detail["observed_event_ids"][target.event_id],
    ]
    final = [
        row for row in records
        if row["phase"] == "native_completed" and row["operation"] == "bounded_final_validation"
    ]
    assert len(final) == 1
    assert [item["memory_id"] for item in final[0]["response"]["items"]] == expected_ids


@pytest.mark.parametrize("language", ["en", "ja"])
def test_distractor_extended_history_roundtrip_preserves_inherited_validation(language):
    from pg_agmemory.agent_evaluation_distractor import DistractorMemoryCase

    case = extended_history_case(language)
    loaded = DistractorMemoryCase.model_validate_json(case.model_dump_json())
    assert type(loaded) is DistractorMemoryCase
    assert loaded == case and len(loaded.events) == 32 and len(loaded.expected_keep_ids) == 24
    assert DistractorMemoryCase.model_validate(loaded.model_dump()) == case
    with pytest.raises(ValueError):
        runner.recipe.AgentMemoryCase.model_validate_json(case.model_dump_json())
    with pytest.raises(ValueError):
        DistractorMemoryCase.model_validate(case.model_dump() | {"events": case.events[:8]})
    with pytest.raises(ValueError, match="unique IDs"):
        DistractorMemoryCase.model_validate(
            case.model_dump() | {"events": (*case.events[:-1], case.events[0])},
        )
    with pytest.raises(ValueError, match="Required evidence must be retained"):
        DistractorMemoryCase.model_validate(
            case.model_dump() | {"expected_keep_ids": (case.events[2].event_id,)},
        )
    with pytest.raises(ValueError):
        DistractorMemoryCase.model_validate(case.model_dump() | {"untrusted_extra": True})


@pytest.mark.parametrize("language", ["en", "ja"])
def test_distractor_full_history_observed_partitioned_and_pending_readable(
    native_fixture, language,
):
    case = extended_history_case(language)
    native_fixture["case"] = case
    target = case.events[16]
    native_fixture["failure"]["search_event_ids"] = [case.events[0].event_id, target.event_id]
    bridge = DecisionBridge(
        case, query_policy="bounded-lexical-v3", query="fixture", answer="FIXTURE32",
        keep_event_ids=case.expected_keep_ids, answer_event_ids=[target.event_id],
        bounded_plans=[
            '{"queries":[{"terms":["fixture","first"]},{"terms":["fixture","second"]}]}',
            '{"queries":[{"terms":["fixture","third"]},{"terms":["fixture","fourth"]}]}',
        ],
    )
    measured, detail = memory_case(
        native_fixture, bridge, query_policy="bounded-lexical-v3", retention_policy="review-v1",
    )
    assert measured.error is None, detail
    assert measured.context_events == (target,)
    assert len(native_fixture["stored"]) == len(detail["observed_event_ids"]) == 32
    assert len(detail["retention"]["keep_ids"]) == 24
    assert len(detail["retention"]["forget_ids"]) == 8
    assert detail["retention_review"]["pending_rows_verified_readable"] == 8
    requests = native_fixture["requests"]
    assert [body["content"] for _, path, body, _ in requests if path == "/v1/observe"] == [
        event.text for event in case.events
    ]
    assert not any(path == "/v1/forget" for _, path, _, _ in requests)
    explained = {body["memory_id"] for _, path, body, _ in requests if path == "/v1/explain"}
    assert set(detail["workflow_excluded_memory_ids"]) <= explained
    assert detail["bounded_retrieval"]["planning_calls"] == 2
    assert detail["bounded_retrieval"]["search_calls"] == 4
    assert detail["bounded_retrieval"]["final_validation_calls"] == 1
    assert len(bridge.calls) == 4
    prompts = dict(bridge.prompts)
    for event in case.events:
        assert event.text in prompts["retention"]
        assert event.text not in prompts["recall_query_round_1"]
        if event != target:
            assert event.text not in prompts["recall_query_round_2"]
            assert event.text not in prompts["pg_agmemory"]
    poisoned = case.model_copy(update={
        "expected_answer": "PRIVATE_GOLD_CANARY", "expected_keep_ids": ("PRIVATE_GOLD_CANARY",),
        "required_source_ids": ("PRIVATE_GOLD_CANARY",),
        "forbidden_answers": ("PRIVATE_GOLD_CANARY",),
    })
    assert runner.recipe.retention_prompt(poisoned) == prompts["retention"]
    assert runner.recipe.answer_prompt(poisoned, (target,)) == prompts["pg_agmemory"]
    assert runner.bounded_recall.search_prompt(
        poisoned.question, runner.lexical_profile(case.language), round_number=1,
    ) == prompts["recall_query_round_1"]
    assert all("PRIVATE_GOLD_CANARY" not in prompt for prompt in prompts.values())


def test_distractor_eight_event_partition_fails_closed_after_all_observations(native_fixture):
    case = extended_history_case("en")
    native_fixture["case"] = case

    class TruncatedActor(DecisionBridge):
        async def call(self, prompt, **kwargs):
            await super().call(prompt, **kwargs)
            assert kwargs["phase"] == "retention"
            return json.dumps({
                "keep_ids": [event.event_id for event in case.events[:8]], "forget_ids": [],
            })

    bridge = TruncatedActor(case)
    measured, detail = memory_case(
        native_fixture, bridge, query_policy="bounded-lexical-v3", retention_policy="review-v1",
    )
    assert measured.error == "invalid_retention_decision"
    assert detail["stopped_at_phase"] == "retention" and len(bridge.calls) == 1
    assert len(native_fixture["stored"]) == len(detail["observed_event_ids"]) == 32
    assert "retention_review" not in detail and "bounded_retrieval" not in detail
    assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])


@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
def test_distractor_all_twenty_cases_fit_real_bridge_120_call_and_payload_limits(
    native_fixture, transport, query_policy,
):
    from pg_agmemory.agent_evaluation_distractor import DistractorMemoryCase

    cases = runner.select_cohort("distractor-synthetic-v1")
    assert len(cases) == 20 and sum(len(case.events) for case in cases) == 640
    private_json(transport.metadata_path, transport.metadata.model_dump() | {"max_calls": 120})

    class ScriptedBridge(runner.FileBridge):
        async def call(self, prompt, *, case_id, phase):
            call_id = f"{len(self.calls) + 1:06d}"
            case = next(case for case in cases if case.case_id == case_id)
            assert all(label not in prompt for label in (
                "expected_answer", "expected_keep_ids", "required_source_ids", "forbidden_answers",
            ))
            if phase == "retention":
                history = json.loads(prompt[prompt.index('{"events":'):])["events"]
                assert history == [event.model_dump() for event in case.events]
                content = json.dumps({
                    "keep_ids": [event.event_id for event in case.events[:24]],
                    "forget_ids": [event.event_id for event in case.events[24:]],
                })
            elif phase.startswith("recall_query_round_"):
                suffixes = ("first", "second") if phase.endswith("_1") else ("third", "fourth")
                content = json.dumps({
                    "queries": [{"terms": ["fixture", suffix]} for suffix in suffixes],
                })
                if phase.endswith("_1"):
                    assert all(event.text not in prompt for event in case.events)
            else:
                content = '{"answer":"","source_event_ids":[],"abstained":true}'
            payload = response(call_id, content=content)
            assert len(runner.json_bytes(payload)) <= runner.MAX_BYTES == 65536

            async def host():
                request = self.queue / f"{call_id}.request.json"
                while not request.exists():
                    await asyncio.sleep(0)
                assert request.stat().st_size <= runner.MAX_BYTES
                assert json.loads(request.read_bytes())["prompt"] == prompt
                private_json(self.queue / f"{call_id}.response.json", payload)

            task = asyncio.create_task(host())
            try:
                return await super().call(prompt, case_id=case_id, phase=phase)
            finally:
                if task.done():
                    await task
                else:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    bridge = ScriptedBridge(
        transport.directory, transport.journal, run_id="agent-eval-12345678",
        max_calls=runner.logical_call_limit(query_policy),
        timeout=5, poll_interval=0.001,
    )

    async def scenario():
        for case in cases:
            assert DistractorMemoryCase.model_validate_json(case.model_dump_json()) == case
            native_fixture["stored"].clear()
            native_fixture["failure"]["search_event_ids"] = [case.events[16].event_id]
            for arm, context in (
                ("no_memory", ()), ("recent_window", runner.recipe.recent_context(case)),
            ):
                measured, detail = await runner.answer_arm(
                    case, arm, context, bridge, native_fixture["journal"],
                )
                assert measured.error is None, detail
            measured, detail = await runner.memory_arm(
                case, native_fixture["identity"], "owned-subject", native_fixture["foreign"],
                native_fixture["sentinel_id"], native_fixture["config"], b"test-key",
                bridge, native_fixture["journal"],
                query_policy=query_policy, retention_policy="review-v1",
            )
            assert measured.error is None, detail
            assert len(detail["observed_event_ids"]) == len(native_fixture["stored"]) == 32
            assert len(detail["retention"]["keep_ids"]) == 24
            assert detail["retention_review"]["pending_rows_verified_readable"] == 8
            assert detail["bounded_retrieval"]["search_calls"] == 4
            assert detail["bounded_retrieval"]["query_policy"] == query_policy
            assert detail["bounded_retrieval"]["final_validation_calls"] == 1
            assert len(measured.context_events) <= 8
            assert detail["context_prompt_bytes"] <= 8000
            assert len(bridge.calls) <= 120
        with pytest.raises(runner.EvaluationFailure, match="llm_call_limit"):
            await runner.FileBridge.call(
                bridge, "must not dispatch", case_id=cases[-1].case_id, phase="pg_agmemory",
            )

    asyncio.run(scenario())
    assert len(bridge.calls) == 120 and all(call["status"] == "ok" for call in bridge.calls)
    assert not (bridge.queue / "000121.request.json").exists()
    assert not any(path == "/v1/forget" for _, path, _, _ in native_fixture["requests"])
    assert sum(path == "/v1/observe" for _, path, _, _ in native_fixture["requests"]) == 640
    assert {call["case_id"] for call in bridge.calls} == {case.case_id for case in cases}


@pytest.mark.integration
@pytest.mark.parametrize("query_policy", runner.BOUNDED_QUERY_POLICIES)
@pytest.mark.parametrize("language,query", [
    ("en", "runnerlongneedle"), ("ja", "検証専用識別子"),
])
def test_real_native_distractor_long_history_review_without_model(
    env, api_process, tmp_path, language, query, query_policy,
):
    case = extended_history_case(language)
    real_native_roundtrip(
        env, api_process, tmp_path, case, query, "FIXTURE32", query_policy,
        retained_event_id=case.events[16].event_id,
        keep_event_ids=case.expected_keep_ids, retention_policy="review-v1",
    )
