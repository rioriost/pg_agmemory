import base64
import json

import pytest

from pg_agmemory.development_agent import (
    SYSTEM_PROMPT,
    DevelopmentEnvironment,
    DevelopmentModel,
    FinalAction,
    observation,
    parse_action,
    render_prompt,
    render_task,
    run_agent,
)
from pg_agmemory.development_evaluation import (
    WORK_PROTOCOL,
    EvaluationFailure,
    ExecuteReply,
    ModelReply,
    ModelSpec,
    json_bytes,
    parse_config,
)
from pg_agmemory.development_memory import MAINTENANCE_PROTOCOL, RETRIEVAL_POLICY


def work_config(arm="no_memory"):
    return parse_config(json_bytes({
        "protocol": "pgag-development-controller-v1", "mode": "work", "run_id": "test-run",
        "work_protocol": WORK_PROTOCOL,
        "memory_maintenance_protocol": MAINTENANCE_PROTOCOL,
        "memory_retrieval_policy": RETRIEVAL_POLICY,
        "session_id": "test-work", "slot": {"project_id": "project-a", "milestone": 1, "arm": arm},
        "recipe_sha256": "a" * 64,
        "model": {"model": "gpt-6-astra", "reasoning_effort": "high"},
        "memory_binding": {
            "run_id": "test-run", "project_id": "project-a", "arm": arm,
            "scope_id": "00000000-0000-4000-8000-000000000001",
        },
        "memory_state": None, "brief": "Write a small Python function.",
        "starting_tree_sha256": "b" * 64,
        "allowed_output_paths": ["solution.py"], "entry_point": "solution.py",
    }))


def reply(text, ordinal=1):
    return ModelReply.model_validate_json(json_bytes({
        "status": "ok", "text": text,
        "receipt_ref": {"global_ordinal": ordinal, "bridge_id": "bridge-test",
                        "bridge_call_id": f"{ordinal:06d}"},
        "model": "gpt-6-astra", "reasoning_effort": "high", "duration_ns": 1,
        "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
                  "cache_write_tokens": 0, "reasoning_tokens": None, "api_requests": 1,
                  "premium_requests": 1, "nano_aiu": None, "api_duration_ms": 1,
                  "monetary_cost_verified": False},
    }))


def execution(raw=b"ok\n", *, code=0, truncated=False):
    return ExecuteReply(
        status="ok", exit_code=code, output_base64=base64.b64encode(raw).decode(),
        captured_bytes=len(raw), output_truncated=truncated, duration_ns=1,
    )


@pytest.fixture(autouse=True)
def isolated_upstream_config(tmp_path, monkeypatch):
    directory = tmp_path / "global-config"
    directory.mkdir()
    monkeypatch.setenv("MSWEA_GLOBAL_CONFIG_DIR", str(directory))
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")


@pytest.mark.parametrize("text", [
    '{"command":"x","final":"y"}', '{"final":"ok","extra":1}',
    '{"command":1}', '{"command":""}', '{"command":"\\u0000"}',
    '{"final":"a","final":"b"}', '```json\n{"final":"ok"}\n```',
    '{"final":"\\ud800"}', '{"command":NaN}', '[]',
    '{"command":"' + "x" * 8193 + '"}',
])
def test_action_parser_rejects_without_repair(text):
    with pytest.raises(EvaluationFailure):
        parse_action(text)


def test_valid_empty_final_and_exact_command_text():
    assert isinstance(parse_action('{"final":""}'), FinalAction)
    command = "printf 'hello';\nexit 1"
    assert parse_action(json.dumps({"command": command})).command == command


HEREDOC_HEADER = "python - <<'PY'\n#"
HEREDOC_END = "\nPY\n"


@pytest.mark.parametrize("command", [
    "x" * 8192, "é" * 4096, "界" * 2730 + "xx", "😀" * 2048, "\\" * 8192,
    HEREDOC_HEADER + "x" * (8192 - len(HEREDOC_HEADER + HEREDOC_END)) + HEREDOC_END,
])
@pytest.mark.parametrize("ensure_ascii", [False, True])
@pytest.mark.parametrize("extra", ["", "x"])
def test_action_command_limit_counts_entire_decoded_utf8_not_json(command, ensure_ascii, extra):
    command += extra
    raw = json.dumps({"command": command}, ensure_ascii=ensure_ascii)
    assert len(command.encode("utf-8")) == 8192 + len(extra)
    assert len(raw.encode("utf-8")) > len(command.encode("utf-8"))
    if extra:
        with pytest.raises(EvaluationFailure, match="^invalid_action$"):
            parse_action(raw)
    else:
        assert parse_action(raw).command == command


def test_action_serialized_response_cap_remains_independent_of_command_bytes():
    raw = '{"command":"x"}'
    boundary = raw + " " * (65536 - len(raw))
    assert parse_action(boundary).command == "x"
    with pytest.raises(EvaluationFailure, match="^json_byte_limit$"):
        parse_action(boundary + " ")


@pytest.mark.parametrize("protocol", [
    None, True, 2, "", "development-work-v1", WORK_PROTOCOL + " ", WORK_PROTOCOL.encode(),
])
def test_required_work_protocol_rejected_before_upstream_checks_or_callbacks(monkeypatch, protocol):
    import importlib.metadata

    from minisweagent.agents.default import DefaultAgent

    config = work_config().model_copy(update={"work_protocol": protocol})
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Invalid protocol must precede upstream checks and all callbacks")

    monkeypatch.setattr(importlib.metadata, "version", forbidden)
    monkeypatch.setattr(DefaultAgent, "__init__", forbidden)
    with pytest.raises(EvaluationFailure, match="^invalid_work_protocol$"):
        run_agent(config, "", forbidden, forbidden, forbidden)
    assert calls == []


@pytest.mark.parametrize("mutation", ["missing", "str_subclass"])
def test_missing_or_nonexact_string_work_protocol_has_no_inferred_default(monkeypatch, mutation):
    import importlib.metadata

    from minisweagent.agents.default import DefaultAgent

    class StringSubclass(str):
        pass

    config = work_config().model_copy()
    if mutation == "missing":
        object.__delattr__(config, "work_protocol")
    else:
        config = config.model_copy(update={"work_protocol": StringSubclass(WORK_PROTOCOL)})
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Missing/mutated protocol must not reach upstream or callbacks")

    monkeypatch.setattr(importlib.metadata, "version", forbidden)
    monkeypatch.setattr(DefaultAgent, "__init__", forbidden)
    with pytest.raises(EvaluationFailure, match="^invalid_work_protocol$"):
        run_agent(config, "", forbidden, forbidden, forbidden)
    assert calls == []


def test_system_prompt_identical_for_all_arms_and_matches_qualified_tool_contract():
    systems, prompts = [], []

    def invoke(phase, prompt, planning_round):
        prompts.append(json.loads(prompt))
        return reply('{"final":"done"}')

    for arm in ("no_memory", "handoff", "pg_agmemory"):
        prompts.clear()
        result = run_agent(
            work_config(arm), "" if arm == "no_memory" else "Fallible historical note.",
            invoke, lambda _: pytest.fail("final must not execute"), lambda _: None,
        )
        assert result.exit_status == "Submitted" and result.query_attempts == 1
        systems.append(prompts[0]["messages"][0]["content"])
    assert systems == [SYSTEM_PROMPT] * 3
    assert len(SYSTEM_PROMPT.encode()) <= 4096
    for phrase in (
        "current brief and source are authoritative", "fallible historical evidence",
        "qualified execution image", "POSIX /bin/sh", "Python 3 as the command python",
        "standard library", "No apply_patch command is provided", "Do not infer capabilities",
        "do not install", "small, targeted edits", "ENTIRE decoded command",
        "including any heredoc", "8192 UTF-8 bytes", "not 8192 characters or serialized JSON bytes",
        "Independent serialized-response and prompt limits", "split larger edits",
        "exactly one valid action per response", "final action share the same 16 model steps",
        "ordinary nonzero shell result permits another action", "actual observation",
        "timeout or invalid/oversized response terminates", "no automatic retry",
        "JSON repair, new tool or relaxed limit",
    ):
        assert phrase in SYSTEM_PROMPT


def test_actual_default_agent_command_observation_and_submitted(monkeypatch):
    from minisweagent.agents.default import DefaultAgent

    original = DefaultAgent.run
    observed_agents = []

    def checked_run(self, *args, **kwargs):
        observed_agents.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DefaultAgent, "run", checked_run)
    prompts, commands, messages = [], [], []

    def invoke(phase, prompt, planning_round):
        prompts.append(json.loads(prompt))
        assert phase == "work" and planning_round is None
        text = (
            '{"command":"visible synthetic command"}'
            if len(prompts) == 1 else '{"final":"done"}'
        )
        return reply(text, len(prompts))

    def execute(command):
        commands.append(command)
        return execution(b"ordinary failure\n", code=1)

    result = run_agent(work_config(), "", invoke, execute, messages.append)
    assert result.exit_status == "Submitted" and result.failure_code is None
    assert result.query_attempts == 2
    assert commands == ["visible synthetic command"]
    assert "[exit_code=1]" in prompts[1]["messages"][-1]["content"]
    assert any(message.role == "observation" for message in messages)
    assert type(observed_agents[0]) is DefaultAgent
    assert observed_agents[0].config.step_limit == 16
    assert observed_agents[0].config.max_consecutive_format_errors == 1
    assert observed_agents[0].config.cost_limit == 0
    assert observed_agents[0].config.wall_time_limit_seconds == 900
    assert observed_agents[0].serialize()["info"]["development_accounting"][
        "monetary_cost_status"
    ] == "disabled_unverified"


@pytest.mark.parametrize("text", [
    '{"final":"ok","final":"duplicate"}',
    '{"command":"x","final":"done"}',
    json.dumps({"command": "x" * 8193}),
    json.dumps({"command": "界" * 2731}, ensure_ascii=False),
    json.dumps({"command": "界" * 2731}, ensure_ascii=True),
])
def test_first_format_error_ends_actual_agent_after_one_charged_call(text):
    calls = []

    def invoke(*args):
        calls.append(args)
        return reply(text)

    result = run_agent(
        work_config(), "", invoke, lambda _: pytest.fail("must not execute"), lambda _: None,
    )
    assert result.exit_status == "RepeatedFormatError"
    assert result.failure_code == "invalid_action"
    assert result.query_attempts == len(calls) == 1


def test_actual_default_agent_missing_tool_then_small_python_edits_and_final(monkeypatch):
    from minisweagent.agents.default import DefaultAgent

    original = DefaultAgent.run
    observed_agents = []

    def checked_run(self, *args, **kwargs):
        observed_agents.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DefaultAgent, "run", checked_run)
    edits = [
        "apply_patch <<'PATCH'\n*** Begin Patch\n*** End Patch\nPATCH",
        "python - <<'PY'\nfrom pathlib import Path\n"
        "Path('solution.py').write_text('def answer():\\n    return 1\\n')\nPY",
        "python - <<'PY'\nfrom pathlib import Path\np = Path('solution.py')\n"
        "p.write_text(p.read_text().replace('return 1', 'return 2'))\nPY",
    ]
    prompts, commands = [], []
    outputs = [
        execution(b"/bin/sh: 1: apply_patch: not found\n", code=127),
        execution(b"first small edit completed\n"),
        execution(b"second small edit completed\n"),
    ]

    def invoke(phase, prompt, planning_round):
        prompts.append(json.loads(prompt))
        index = len(prompts) - 1
        if index:
            expected = observation(outputs[index - 1])
            assert prompts[-1]["messages"][-1]["content"] == expected
        action = {"command": edits[index]} if index < len(edits) else {"final": "done"}
        return reply(json.dumps(action), len(prompts))

    def execute(command):
        commands.append(command)
        assert len(command.encode()) <= 8192
        return outputs[len(commands) - 1]

    # Scripted observations exercise actual DefaultAgent control flow, not guest filesystem proof.
    result = run_agent(work_config(), "", invoke, execute, lambda _: None)
    assert type(observed_agents[0]) is DefaultAgent
    assert observed_agents[0].config.step_limit == 16
    assert observed_agents[0].config.max_consecutive_format_errors == 1
    assert result.exit_status == "Submitted" and result.failure_code is None
    assert result.query_attempts == len(prompts) == 4 and commands == edits
    assert "[exit_code=127]" in prompts[1]["messages"][-1]["content"]
    observations = [message for message in result.visible_messages if message.role == "observation"]
    assert len(observations) == 3


def test_step_exhaustion_has_exactly_sixteen_calls_and_no_seventeenth():
    calls, commands = [], []

    def invoke(*args):
        calls.append(args)
        return reply('{"command":"synthetic"}', len(calls))

    def execute(command):
        commands.append(command)
        return execution()

    result = run_agent(work_config(), "", invoke, execute, lambda _: None)
    assert result.exit_status == "LimitsExceeded"
    assert result.failure_code == "step_limit_exceeded"
    assert result.query_attempts == len(calls) == len(commands) == 16


def test_transport_failure_preserves_attempt_count_without_repair():
    def invoke(*args):
        raise EvaluationFailure("model_timeout", outcome_unknown=True)

    result = run_agent(work_config(), "", invoke, lambda _: pytest.fail("execute"), lambda _: None)
    assert result.query_attempts == 1
    assert result.failure_code == "model_timeout" and result.outcome_unknown


def test_upstream_wall_deadline_stops_before_first_model_call(monkeypatch):
    from minisweagent.agents import default

    ticks = 0

    def clock():
        nonlocal ticks
        ticks += 1
        return 100 if ticks == 1 else 1001

    monkeypatch.setattr(default.time, "time", clock)
    result = run_agent(
        work_config(), "", lambda *args: pytest.fail("deadline must prevent invoke"),
        lambda _: pytest.fail("must not execute"), lambda _: None,
    )
    assert result.exit_status == "TimeExceeded"
    assert result.failure_code == "session_deadline" and result.query_attempts == 0


def test_prompt_exhaustion_is_local_not_an_inference_retry():
    calls = []

    def invoke(*args):
        calls.append(args)
        return reply(json.dumps({"command": "\x01" * 8192}), len(calls))

    result = run_agent(work_config(), "", invoke, lambda _: execution(), lambda _: None)
    assert result.failure_code == "prompt_budget_exhausted"
    assert result.query_attempts == len(calls) + 1
    assert result.exit_status == "EvaluationFailure" and not result.outcome_unknown
    assert len(calls) < 16


def test_fresh_runs_have_distinct_model_history_and_zero_counters():
    first, second = [], []

    def first_call(phase, prompt, planning_round):
        first.append(prompt)
        return reply('{"final":"first-private-nonce"}')

    def second_call(phase, prompt, planning_round):
        second.append(prompt)
        return reply('{"final":"second"}')

    left = run_agent(work_config(), "", first_call, lambda _: execution(), lambda _: None)
    right = run_agent(work_config(), "", second_call, lambda _: execution(), lambda _: None)
    assert left.query_attempts == right.query_attempts == 1
    assert "first-private-nonce" not in second[0]
    assert len(json.loads(first[0])["messages"]) == len(json.loads(second[0])["messages"]) == 2


def test_common_memory_envelope_and_utf8_delivery_limit():
    config = work_config()
    empty = json.loads(render_task(config, ""))
    with_memory = json.loads(render_task(config, "historical statement"))
    assert empty | {"historical_memory": "historical statement"} == with_memory
    with pytest.raises(EvaluationFailure, match="memory_delivery_budget"):
        render_task(config, "界" * 683)
    with pytest.raises(EvaluationFailure, match="prompt_budget_exhausted"):
        render_prompt([{"role": "user", "content": '"' * 40000}])


def test_observation_capture_is_not_full_model_delivery():
    text = observation(execution(b"\xff" + "界".encode() * 5000, truncated=True))
    assert len(text.encode()) <= 2048
    assert "invalid UTF-8" in text and "output omitted" in text
    assert observation(execution(b"exact")) == "[exit_code=0]\nexact"


def test_environment_rejects_unregistered_cwd_and_model_templates_have_no_secrets():
    environment = DevelopmentEnvironment(lambda _: execution())
    with pytest.raises(EvaluationFailure, match="working_directory"):
        environment.execute({"command": "pwd"}, cwd="/control")
    assert environment.get_template_vars() == {"cwd": "/workspace"}
    model = DevelopmentModel(
        ModelSpec(model="gpt-6-astra", reasoning_effort="high"),
        lambda *args: reply('{"final":"done"}'), lambda _: None,
    )
    assert set(model.get_template_vars()) == {"model_name", "reasoning_effort"}
