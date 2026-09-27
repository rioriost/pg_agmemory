import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pg_agmemory.development_evaluation import (
    ControllerResult,
    EventSink,
    FileIPC,
    InstrumentedNativeFactory,
    boundary_transcript,
    json_bytes,
    private_read,
    publish,
)
from pg_agmemory.models import Observe, Recall

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate-development-session.py"


def config_value(*, arm="no_memory", milestone=1, session="work-a", state=None):
    return {
        "protocol": "pgag-development-controller-v1", "mode": "work",
        "run_id": "test-run", "session_id": session,
        "slot": {"project_id": "project-a", "milestone": milestone, "arm": arm},
        "recipe_sha256": "a" * 64,
        "model": {"model": "gpt-6-astra", "reasoning_effort": "high"},
        "memory_binding": {
            "run_id": "test-run", "project_id": "project-a", "arm": arm,
            "scope_id": "00000000-0000-4000-8000-000000000001",
        },
        "memory_state": state, "brief": "Implement the visible synthetic task.",
        "starting_tree_sha256": "b" * 64,
        "allowed_output_paths": ["solution.py"], "entry_point": "solution.py",
    }


def model_result(text, ordinal=1):
    return {
        "status": "ok", "text": text,
        "receipt_ref": {"global_ordinal": ordinal, "bridge_id": "bridge-test",
                        "bridge_call_id": f"{ordinal:06d}"},
        "model": "gpt-6-astra", "reasoning_effort": "high", "duration_ns": 1,
        "usage": {
            "input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "reasoning_tokens": None, "api_requests": 1,
            "premium_requests": 1, "nano_aiu": None, "api_duration_ms": 1,
            "monetary_cost_verified": False,
        },
    }


def run_controller(root, config, respond, *, environment=None):
    root.mkdir(mode=0o700)
    home, ipc, output = (root / name for name in ("home", "ipc", "output"))
    for path in (home, ipc, output):
        path.mkdir(mode=0o700)
    config_path = root / "input.json"
    publish(config_path, json_bytes(config))
    process = subprocess.Popen(
        [sys.executable, "-I", str(SCRIPT), "--config", str(config_path),
         "--ipc", str(ipc), "--output", str(output), "--home", str(home)],
        env={"PATH": os.defpath, "LANG": "C.UTF-8"} | (environment or {}),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    requests = []
    try:
        deadline = time.monotonic() + 30
        while process.poll() is None and time.monotonic() < deadline:
            path = ipc / "requests" / f"{len(requests) + 1:06d}.json"
            if path.exists():
                request = json.loads(private_read(path, 70000))
                requests.append(request)
                result = respond(request)
                response = {key: request[key] for key in (
                    "protocol", "session_id", "sequence", "operation",
                )} | {"result": result}
                publish(ipc / "replies" / path.name, json_bytes(response),
                        staging_directory=ipc)
            else:
                time.sleep(0.01)
        assert process.poll() is not None, "controller did not terminate"
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    result_path = output / "result.json"
    result = (
        ControllerResult.model_validate_json(private_read(result_path, 524288))
        if result_path.exists() else None
    )
    events_path = output / "events.jsonl"
    events = [
        json.loads(line) for line in events_path.read_bytes().splitlines()
    ] if events_path.exists() else []
    return process.returncode, result, events, requests, stdout, stderr


def test_two_actual_fresh_controllers_do_not_carry_history_or_ids(tmp_path):
    first = run_controller(
        tmp_path / "first", config_value(),
        lambda _: model_result('{"final":"first-private-marker"}'),
    )
    second = run_controller(
        tmp_path / "second", config_value(milestone=2, session="work-b"),
        lambda _: model_result('{"final":"second"}', 2),
    )
    for code, result, events, requests, _, stderr in (first, second):
        assert code == 0, stderr.decode()
        assert result.status == "submitted" and result.memory_state is None
        assert result.query_attempts == result.admitted_invocations == 1
        assert result.provider_api_requests == 1
        kinds = [event["kind"] for event in events]
        assert kinds.index("retrieval_completed") < kinds.index("work_started")
        assert kinds.index("work_started") < kinds.index("ipc_request")
        assert kinds[-1] == "work_finished"
        assert requests[0]["sequence"] == 1
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert result.transcript.sha256 == next(
            event["data"]["transcript_sha256"] for event in events
            if event["kind"] == "work_finished"
        )
    assert second[1].invocation_receipts[0].global_ordinal == 2
    assert "first-private-marker" not in second[3][0]["body"]["prompt"]
    assert len(json.loads(second[3][0]["body"]["prompt"])["messages"]) == 2


def test_command_is_only_ipc_and_ordinary_nonzero_exit_is_observed(tmp_path):
    command = "printf synthetic"
    calls = 0

    def respond(request):
        nonlocal calls
        if request["operation"] == "execute":
            assert request["body"] == {"command": command}
            return {
                "status": "ok", "exit_code": 7, "output_base64": "eAo=",
                "captured_bytes": 2, "output_truncated": False, "duration_ns": 1,
            }
        calls += 1
        return model_result(
            json.dumps({"command": command}) if calls == 1 else '{"final":"done"}', calls,
        )

    code, result, _, requests, _, stderr = run_controller(
        tmp_path / "command", config_value(), respond,
    )
    assert code == 0, stderr.decode()
    assert result.query_attempts == result.admitted_invocations == 2
    assert result.execute_attempts == 1
    assert [request["operation"] for request in requests] == [
        "invoke_model", "execute", "invoke_model",
    ]
    assert json.loads(requests[-1]["body"]["prompt"])["messages"][-1] == {
        "role": "user", "content": "[exit_code=7]\nx\n",
    }


def test_fresh_handoff_boundary_directly_maintains_without_deliver(tmp_path):
    config = config_value(arm="handoff", session="boundary-a")
    for field in ("brief", "starting_tree_sha256", "allowed_output_paths", "entry_point"):
        config.pop(field)
    transcript = boundary_transcript("The current visible task.", [])
    config.update(
        mode="boundary", transcript=transcript.model_dump(mode="json"),
        boundary_id="project-a-boundary-1", keys=None,
    )

    def respond(request):
        assert request["operation"] == "invoke_model"
        assert request["body"]["phase"] == "handoff"
        assert request["body"]["planning_round"] is None
        return model_result('{"note":"Visible historical fact."}')

    code, result, events, requests, _, stderr = run_controller(
        tmp_path / "boundary", config, respond,
    )
    assert code == 0, stderr.decode()
    assert result.status == "boundary_completed"
    assert result.query_attempts == result.execute_attempts == 0
    assert result.admitted_invocations == result.provider_api_requests == 1
    assert result.memory_state.completed_boundaries == 1
    assert result.memory_state.note == "Visible historical fact."
    assert result.boundary_result.model_receipt_ref == model_result("")["receipt_ref"]
    assert result.transcript == transcript
    assert len(requests) == 1
    assert not any(event["kind"] in ("retrieval_completed", "work_started") for event in events)
    assert not any(
        event["kind"] == "memory_event" and event["data"]["kind"] == "memory_delivery"
        for event in events
    )
    subsequent = run_controller(
        tmp_path / "handoff-work",
        config_value(arm="handoff", milestone=2, session="work-b",
                     state=result.memory_state.model_dump(mode="json")),
        lambda _: model_result('{"final":"done"}', 2),
    )
    assert subsequent[0] == 0, subsequent[-1].decode()
    assert "Visible historical fact." in subsequent[3][0]["body"]["prompt"]


@pytest.mark.parametrize("failure", ["timeout", "multiple", "malformed_action"])
def test_failed_call_is_accounted_once_and_never_repaired(tmp_path, failure):
    def respond(request):
        if failure == "timeout":
            return {
                "status": "error", "code": "model_timeout", "outcome": "unknown",
                "receipt_ref": model_result("")["receipt_ref"], "usage": None,
                "duration_ns": 150000000000,
            }
        value = model_result('{"final":"done"}' if failure == "multiple" else "not JSON")
        if failure == "multiple":
            value["usage"]["api_requests"] = 2
        return value

    code, result, _, requests, _, stderr = run_controller(
        tmp_path / failure, config_value(), respond,
    )
    assert code == 1, stderr.decode()
    assert result.status == "failed"
    assert result.query_attempts == result.admitted_invocations == len(requests) == 1
    assert result.execute_attempts == 0
    if failure == "timeout":
        assert result.outcome_unknown and result.provider_api_requests is None
    elif failure == "multiple":
        assert result.outcome_unknown and result.provider_api_requests == 2
    else:
        assert result.reason == "invalid_action" and result.provider_api_requests == 1
        assert result.upstream_exit_status == "RepeatedFormatError"


def test_no_memory_rejects_native_credentials_without_archiving_them(tmp_path):
    secret = "synthetic-token-not-a-credential"
    code, result, events, requests, stdout, stderr = run_controller(
        tmp_path / "unexpected-auth", config_value(),
        lambda _: pytest.fail("must not invoke"),
        environment={"PGAG_DEVELOPMENT_API_URL": "http://127.0.0.1:12345",
                     "PGAG_DEVELOPMENT_API_TOKEN": secret},
    )
    assert code == 1
    assert result.reason == "unexpected_native_authorization"
    assert result.admitted_invocations == result.provider_api_requests == 0
    assert requests == []
    assert secret not in json.dumps(events) + result.model_dump_json()
    assert secret.encode() not in stdout + stderr


def test_bootstrap_isolates_environment_and_imports_no_optional_sdk(tmp_path):
    home, old = tmp_path / "home", tmp_path / "old"
    home.mkdir(mode=0o700)
    old.mkdir(mode=0o700)
    (old / ".env").write_text("DEVELOPMENT_DOTENV_CANARY=untrusted\n")
    probe = """
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("controller", sys.argv[1])
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
controller.bootstrap(Path(sys.argv[2]))
assert os.environ["HOME"] == sys.argv[2]
assert os.environ["MSWEA_GLOBAL_CONFIG_DIR"] == sys.argv[2] + "/config/mini-swe-agent"
assert "OPENAI_API_KEY" not in os.environ
assert "PGAG_DATABASE_URL" not in os.environ
import pg_agmemory.development_evaluation
assert "pg_agmemory.sdk" not in sys.modules
import minisweagent
assert "DEVELOPMENT_DOTENV_CANARY" not in os.environ
assert not list(Path(os.environ["MSWEA_GLOBAL_CONFIG_DIR"]).glob(".env"))
"""
    process = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(SCRIPT), str(home)],
        env={"PATH": os.defpath, "HOME": str(old), "MSWEA_GLOBAL_CONFIG_DIR": str(old),
             "OPENAI_API_KEY": "fake-canary", "PGAG_DATABASE_URL": "fake-canary"},
        capture_output=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr.decode()


def test_fresh_bootstrap_rejects_reused_home_without_loading_upstream(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "prior-state").write_text("previous controller data")
    process = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--config", str(tmp_path / "input"),
         "--ipc", str(tmp_path / "ipc"), "--output", str(tmp_path / "output"),
         "--home", str(home)],
        env={"PATH": os.defpath}, capture_output=True, timeout=30,
    )
    assert process.returncode == 2
    assert b"startup_failed:ValueError" in process.stderr
    assert not (home / "config").exists()


@pytest.mark.parametrize("extra", [
    ["--unknown", "x"], ["--config", "/again"], ["--config=/again"], ["--con", "/short"],
])
def test_unknown_duplicate_and_abbreviated_controller_flags_fail(extra):
    spec = importlib.util.spec_from_file_location("development_controller", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit) as error:
        module.arguments([
            "--config", "/input", "--ipc", "/ipc", "--output", "/output", "--home", "/home",
            *extra,
        ])
    assert error.value.code == 2


def test_unknown_native_outcome_is_not_downgraded_to_known_controller_failure(
    tmp_path, monkeypatch,
):
    from pg_agmemory.development_memory import DevelopmentMemory
    from pg_agmemory.native_client import AdapterError, AdapterFailure

    root = tmp_path / "native-unknown"
    root.mkdir(mode=0o700)
    output, ipc = root / "output", root / "ipc"
    output.mkdir(mode=0o700)
    ipc.mkdir(mode=0o700)
    config = config_value(arm="pg_agmemory", session="boundary-a")
    for field in ("brief", "starting_tree_sha256", "allowed_output_paths", "entry_point"):
        config.pop(field)
    config.update(
        mode="boundary", transcript=boundary_transcript("brief", []).model_dump(mode="json"),
        boundary_id="boundary-a",
        keys={"observe": "observe-1", "create": [f"create-{i}" for i in range(6)],
              "revise": [f"revise-{i}" for i in range(4)]},
    )
    publish(root / "input.json", json_bytes(config))

    async def uncertain(self, transcript, *, boundary_id, keys):
        raise AdapterFailure(AdapterError(
            code="commit_outcome_unknown", retryable=False, outcome_unknown=True,
        ))

    monkeypatch.setattr(DevelopmentMemory, "maintain", uncertain)
    monkeypatch.setenv("PGAG_DEVELOPMENT_API_URL", "http://127.0.0.1:12345")
    monkeypatch.setenv("PGAG_DEVELOPMENT_API_TOKEN", "synthetic.identity.signature")
    spec = importlib.util.spec_from_file_location("development_controller_unknown", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run(root / "input.json", ipc, output)
    assert result.status == "failed" and result.reason == "commit_outcome_unknown"
    assert result.outcome_unknown and result.boundary_result is None
    assert result.memory_state is None
    assert result.provider_api_requests == result.admitted_invocations == 0


@pytest.mark.parametrize("failure", [
    "invalid_search_plan", "search_prompt_too_large", "invalid_planning_schedule", "untyped",
])
def test_typed_retrieval_failure_preserves_code_origin_and_existing_model_accounting(
    tmp_path, monkeypatch, failure,
):
    from pg_agmemory.bounded_recall import BoundedRecallError, parse_search_plan, search_prompt
    from pg_agmemory.development_memory import DevelopmentMemory

    root = tmp_path / "retrieval-failure"
    root.mkdir(mode=0o700)
    output, ipc = root / "output", root / "ipc"
    output.mkdir(mode=0o700)
    ipc.mkdir(mode=0o700)
    config = config_value(arm="pg_agmemory", milestone=2)
    config["memory_state"] = {
        "format": "development-memory-state-v1", "binding": config["memory_binding"],
        "completed_boundaries": 1, "last_boundary_id": "boundary-1", "note": None,
        "assertions": [{
            "memory_id": "00000000-0000-4000-8000-000000000002", "revision": 1, "status": "active",
        }],
    }
    publish(root / "input.json", json_bytes(config))
    callbacks, requests = {}, []
    memory_init, ipc_init = DevelopmentMemory.__init__, FileIPC.__init__

    def initialize_memory(self, binding, **kwargs):
        callbacks["invoke"] = kwargs["invoke"]
        memory_init(self, binding, **kwargs)

    def initialize_ipc(self, *args, **kwargs):
        ipc_init(self, *args, **kwargs)

        def host_reply(delay):
            request_path = self.requests / f"{self.sequence:06d}.json"
            request = json.loads(private_read(request_path, 70000))
            requests.append(request)
            assert request["body"]["phase"] == "memory_plan"
            response = {key: request[key] for key in (
                "protocol", "session_id", "sequence", "operation",
            )} | {"result": model_result('{"queries":[]}')}
            publish(self.replies / request_path.name, json_bytes(response),
                    staging_directory=self.replies.parent)

        self.sleep = host_reply

    class UntypedPlannerError(ValueError):
        code = "invalid_search_plan"

    async def fail_delivery(self, public_brief):
        if failure == "invalid_search_plan":
            reply = callbacks["invoke"]("memory_plan", "Synthetic planner input.", 1)
            parse_search_plan(reply.text)
        elif failure == "search_prompt_too_large":
            search_prompt("\x01" * 4096, "en-snowball-v1", planner_policy="sequential-v3")
        elif failure == "untyped":
            raise UntypedPlannerError("not a typed helper error")
        else:
            raise BoundedRecallError(failure)
        pytest.fail("the real helper must reject this input")

    monkeypatch.setattr(DevelopmentMemory, "__init__", initialize_memory)
    monkeypatch.setattr(DevelopmentMemory, "deliver", fail_delivery)
    monkeypatch.setattr(FileIPC, "__init__", initialize_ipc)
    monkeypatch.setenv("PGAG_DEVELOPMENT_API_URL", "http://127.0.0.1:12345")
    monkeypatch.setenv("PGAG_DEVELOPMENT_API_TOKEN", "synthetic.identity.signature")
    spec = importlib.util.spec_from_file_location("development_controller_retrieval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run(root / "input.json", ipc, output)
    records = [json.loads(line) for line in (output / "events.jsonl").read_bytes().splitlines()]
    diagnostic = next(record["data"] for record in records if record["kind"] == "controller_failed")
    assert result.status == "failed" and result.outcome_unknown is False
    assert result.query_attempts == result.execute_attempts == 0
    assert result.admitted_invocations == result.provider_api_requests == len(requests)
    assert len(requests) == (1 if failure == "invalid_search_plan" else 0)
    assert not any(record["kind"] == "work_started" for record in records)
    if failure == "untyped":
        assert result.reason == "controller_exception"
        assert diagnostic["exception_type"] == "UntypedPlannerError"
        assert "origin" not in diagnostic
    else:
        assert result.reason == diagnostic["code"] == failure
        assert diagnostic["origin"] == "pg_agmemory.bounded_recall.BoundedRecallError"
        assert diagnostic["exception_type"] == "BoundedRecallError"
        assert diagnostic["phase"] == "memory_delivery"
    if requests:
        assert result.invocation_receipts[0].model_dump(mode="json") == (
            model_result("")["receipt_ref"]
        )


@pytest.mark.integration
def test_instrumented_factory_uses_real_sdk_http_and_native_receipts(env, api_process, tmp_path):
    output = tmp_path / "native-events"
    output.mkdir(mode=0o700)
    events = EventSink(output, "native-test")
    token = env.token()
    with api_process("development-native-api.log") as (http, _):
        factory = InstrumentedNativeFactory(str(http.base_url), token, events, lambda: None)

        async def scenario():
            async with factory() as native:
                observed = await native.observe(Observe(
                    scope_id=env.scopes[0], source_namespace="development-instrumentation",
                    source_event_id="synthetic-observation", occurred_at=datetime.now(UTC),
                    content="Synthetic controller evidence.", consent_reference="test",
                ), idempotency_key="development-observe-key")
                result = await native.recall(Recall(
                    scope_ids=[env.scopes[0]], purpose="test", query="evidence",
                    search_profile="en-snowball-v1",
                ))
                assert observed.memory_id in {item.memory_id for item in result.items}
                assert result.search_profile == "en-snowball-v1"
                assert not hasattr(native, "forget")
            async with factory() as native:
                await native.recall(Recall(scope_ids=[env.scopes[0]], purpose="test"))

        try:
            asyncio.run(scenario())
        finally:
            events.close()
    raw = (output / "events.jsonl").read_text()
    records = [json.loads(line) for line in raw.splitlines()]
    assert token not in raw
    intents = [record["data"] for record in records if record["kind"] == "native_intent"]
    assert [data["operation"] for data in intents] == [
        "sdk_capability_setup", "observe", "recall", "sdk_capability_setup", "recall",
    ]
    assert len([record for record in records if record["kind"] == "native_result"]) == 5
    assert intents[1]["idempotency_key"] == "development-observe-key"
    assert intents[1]["request"].get("auto_extract", False) is False
    assert intents[1]["request"].get("auto_embed", False) is False
