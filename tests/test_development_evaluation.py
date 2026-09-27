import base64
import json
import os

import pytest
from pydantic import ValidationError

from pg_agmemory.development_evaluation import (
    ArtifactManifest,
    BoundaryTranscript,
    EvaluationFailure,
    EventSink,
    ExecuteReply,
    FileIPC,
    GradeRecord,
    InvocationRef,
    MemoryAuditEvent,
    VisibleMessage,
    audit_json,
    boundary_transcript,
    candidate_equal,
    json_bytes,
    parse_config,
    parse_json,
    private_read,
    publish,
    sha256,
)


def config_value(arm="no_memory", milestone=1):
    return {
        "protocol": "pgag-development-controller-v1", "mode": "work",
        "run_id": "test-run", "session_id": "test-session",
        "slot": {"project_id": "project-a", "milestone": milestone, "arm": arm},
        "recipe_sha256": "a" * 64,
        "model": {"model": "gpt-6-astra", "reasoning_effort": "high"},
        "memory_binding": {
            "run_id": "test-run", "project_id": "project-a", "arm": arm,
            "scope_id": "00000000-0000-4000-8000-000000000001",
        },
        "memory_state": None, "brief": "Implement the visible synthetic task.",
        "starting_tree_sha256": "b" * 64,
        "allowed_output_paths": ["solution.py"], "entry_point": "solution.py",
    }


def model_result(call=1):
    return {
        "status": "ok", "text": '{"final":"done"}',
        "receipt_ref": {
            "global_ordinal": call, "bridge_id": "bridge-a", "bridge_call_id": f"{call:06d}",
        },
        "model": "gpt-6-astra", "reasoning_effort": "high",
        "usage": {
            "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "reasoning_tokens": None, "api_requests": 1,
            "premium_requests": 1, "nano_aiu": None, "api_duration_ms": 1000,
            "monetary_cost_verified": False,
        },
        "duration_ns": 1100000000,
    }


def make_ipc(tmp_path, responder, *, work=True, arm="no_memory"):
    tmp_path.chmod(0o700)
    ipc_path, output = tmp_path / "ipc", tmp_path / "output"
    ipc_path.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    config = parse_config(json_bytes(config_value(arm)))
    sink = EventSink(output, config.session_id)
    now = [0.0]
    ipc = None

    def sleep(delay):
        now[0] += delay
        if responder is not None:
            request_path = ipc_path / "requests" / f"{ipc.sequence:06d}.json"
            request = json.loads(request_path.read_bytes())
            response = {key: request[key] for key in (
                "protocol", "session_id", "sequence", "operation",
            )}
            response["result"] = model_result(request["sequence"])
            responder(request, response, now)
            publish(ipc_path / "replies" / request_path.name, json_bytes(response),
                    staging_directory=ipc_path)

    ipc = FileIPC(ipc_path, config, sink, clock=lambda: now[0], sleep=sleep)
    if work:
        ipc.start_work()
    return ipc, sink, now


def publish_at_wait_check(ipc, now):
    clock, host_publish = ipc.clock, ipc.sleep
    published = []

    def interleaved_clock():
        if ipc.inflight and not published:
            assert (ipc.requests / f"{ipc.sequence:06d}.json").exists()
            assert not (ipc.replies / f"{ipc.sequence:06d}.json").exists()
            host_publish(0)
            published.append(ipc.sequence)
        return clock()

    ipc.clock = interleaved_clock
    ipc.sleep = lambda delay: now.__setitem__(0, now[0] + delay)
    return published


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}', b'{"a":1,"\\u0061":2}', b"NaN", b"Infinity", b"-Infinity",
    b"1.0", b"1e0", b"9007199254740992", b"-9007199254740992", b'"\\ud800"',
    b'"\xff"', b"{} {}", b"```json\n{}\n```", b"\xc2\xa0{}\xc2\xa0",
    b"[" * 33 + b"0" + b"]" * 33,
])
def test_candidate_rejects_nonconforming_json(raw):
    with pytest.raises(EvaluationFailure):
        parse_json(raw, limit=16384, candidate=True)


@pytest.mark.parametrize(("left", "right", "equal"), [
    (b"true", b"1", False), (b"false", b"0", False),
    (b'{"a":1,"b":[true,null]}', b' { "b":[true,null], "a":1 }\n', True),
    (b"[1,2]", b"[2,1]", False), (b'" a "', b'"a"', False),
    (b"-0", b"0", True), (b'"\\ud83d\\ude00"', '"😀"'.encode(), True),
    (b'"e\\u0301"', '"é"'.encode(), False),
])
def test_candidate_comparison_is_structural_and_type_strict(left, right, equal):
    assert candidate_equal(
        parse_json(left, candidate=True), parse_json(right, candidate=True),
    ) is equal


def test_candidate_depth_and_byte_boundaries():
    assert parse_json(b"[" * 32 + b"0" + b"]" * 32, candidate=True) is not None
    assert parse_json(b'"' + b"a" * 8190 + b'"', limit=8192) == "a" * 8190
    with pytest.raises(EvaluationFailure, match="json_byte_limit"):
        parse_json(b'"' + b"a" * 8191 + b'"', limit=8192)


def test_no_memory_state_is_null_at_every_milestone():
    for milestone in (1, 2, 3):
        config = parse_config(json_bytes(config_value(milestone=milestone)))
        assert config.memory_state is None
    value = config_value()
    value["previous_handoff"] = "forbidden duplicate continuity field"
    with pytest.raises(EvaluationFailure):
        parse_config(json_bytes(value))


@pytest.mark.parametrize("change", [
    {"allowed_output_paths": ["../secret"], "entry_point": "../secret"},
    {"allowed_output_paths": ["solution.py", "solution.py"]},
    {"entry_point": "unlisted.py"}, {"brief": "界" * 1366},
    {"memory_state": {}},
    {"held_out_cases": [{"input": "must not enter controller"}]},
])
def test_work_input_rejects_invalid_paths_and_continuity(change):
    with pytest.raises((EvaluationFailure, ValidationError)):
        parse_config(json_bytes(config_value() | change))


def test_strict_config_identity_and_boolean_milestone():
    value = config_value()
    value["slot"]["milestone"] = True
    with pytest.raises(EvaluationFailure):
        parse_config(json_bytes(value))
    value = config_value()
    value["memory_binding"]["run_id"] = "another-run"
    with pytest.raises(EvaluationFailure, match="binding_mismatch"):
        parse_config(json_bytes(value))
    with pytest.raises(EvaluationFailure):
        parse_config(json_bytes(config_value("handoff", 2)))


def test_boundary_rejects_no_memory_and_final_milestone():
    for arm, milestone in (("no_memory", 1), ("handoff", 3)):
        value = config_value(arm, milestone)
        for key in ("brief", "starting_tree_sha256", "allowed_output_paths", "entry_point"):
            value.pop(key)
        value.update(
            mode="boundary", boundary_id="boundary-a", keys=None,
            transcript=boundary_transcript("brief", []).model_dump(mode="json"),
        )
        with pytest.raises(EvaluationFailure):
            parse_config(json_bytes(value))


def test_transcript_keeps_complete_brief_and_contiguous_whole_suffix():
    brief = "current authoritative brief"
    messages = [
        VisibleMessage(ordinal=i + 1, role="assistant", content="界" * 3000)
        for i in range(5)
    ]
    result = boundary_transcript(brief, messages)
    assert len(result.text.encode()) <= 24576
    assert result.sha256 == sha256(result.text.encode())
    parsed = json.loads(result.text)
    assert parsed["brief"] == brief
    assert parsed["messages"] == [m.model_dump(mode="json") for m in messages[-2:]]
    assert result.included_message_ordinals == (4, 5)
    assert result.omitted_message_ordinals == (1, 2, 3)
    assert parsed["omitted_message_ordinals"] == [1, 2, 3]


def test_transcript_does_not_clip_oversized_latest_message():
    result = boundary_transcript("brief", [
        VisibleMessage(ordinal=1, role="user", content="earlier"),
        VisibleMessage(ordinal=2, role="assistant", content="x" * 30000),
    ])
    assert result.included_message_ordinals == ()
    assert result.omitted_message_ordinals == (1, 2)


def test_transcript_metadata_cannot_disagree_with_exact_text():
    transcript = boundary_transcript("brief", [
        VisibleMessage(ordinal=1, role="user", content="visible"),
    ])
    value = transcript.model_dump(mode="json")
    value["included_message_ordinals"] = [2]
    with pytest.raises(EvaluationFailure, match="included_mismatch"):
        BoundaryTranscript.model_validate_json(json_bytes(value))


def test_memory_events_preserve_typed_host_receipts_without_custom_object_serialization():
    ref = InvocationRef(
        global_ordinal=1, bridge_id="bridge-test", bridge_call_id="000001",
    )
    event = MemoryAuditEvent.model_validate(audit_json({
        "kind": "memory_model_receipt", "phase": "maintain", "status": "completed",
        "data": {"receipt_ref": ref},
    }))
    assert event.data["receipt_ref"] == ref.model_dump(mode="json")
    with pytest.raises(EvaluationFailure, match="unsupported_audit_value"):
        audit_json(object())


def test_native_memory_intent_is_started_not_completed():
    event = MemoryAuditEvent.model_validate({
        "kind": "memory_native_intent", "phase": "maintain", "status": "started",
        "data": {"operation": "inventory_read", "request": {"query": ""}},
    })
    assert event.status == "started"
    with pytest.raises(ValidationError):
        MemoryAuditEvent.model_validate(event.model_dump() | {"status": "unknown"})


def test_ipc_host_receipts_are_passed_through_and_no_commands_execute_locally(tmp_path):
    ipc, events, _ = make_ipc(tmp_path, lambda *_: None)
    try:
        reply = ipc.invoke("work", "prompt", None)
        assert reply.receipt_ref.global_ordinal == 1
        assert ipc.provider_requests == 1
        assert len(ipc.receipts) == 1
        request = json.loads((ipc.requests / "000001.json").read_bytes())
        assert request["body"] == {"phase": "work", "prompt": "prompt", "planning_round": None}
        assert set(request) == {"protocol", "session_id", "sequence", "operation", "body"}
        assert (ipc.requests / "000001.json").stat().st_nlink == 1
    finally:
        events.close()


@pytest.mark.parametrize("operation", ["invoke_model", "execute"])
def test_expected_reply_published_at_wait_scan_gap_is_consumed_once(tmp_path, operation):
    def respond(request, response, now):
        if operation == "execute":
            response["result"] = {
                "status": "ok", "exit_code": 0, "output_base64": "b2sK",
                "captured_bytes": 3, "output_truncated": False, "duration_ns": 1,
            }

    ipc, events, now = make_ipc(tmp_path, respond)
    published = publish_at_wait_check(ipc, now)
    try:
        if operation == "invoke_model":
            result = ipc.invoke("work", "prompt", None)
            assert result.text == model_result()["text"]
            assert ipc.provider_requests == len(ipc.receipts) == 1
        else:
            assert ipc.execute("synthetic").output() == b"ok\n"
            assert ipc.provider_requests == len(ipc.receipts) == 0
            assert ipc.execute_attempts == 1
        assert published == [1]
        assert len(list(ipc.requests.iterdir())) == len(ipc._reply_hashes) == 1
        assert not ipc.poisoned
        records = [
            json.loads(line)
            for line in (events.directory / "events.jsonl").read_bytes().splitlines()
        ]
        assert [event["kind"] for event in records].count("ipc_reply") == 1
    finally:
        events.close()


@pytest.mark.parametrize(("corruption", "code"), [
    ("foreign_session", "ipc_reply_mismatch"),
    ("future_reply", "unexpected_ipc_reply"),
])
def test_reply_at_wait_scan_gap_does_not_bypass_identity_or_filename_checks(
    tmp_path, corruption, code,
):
    def respond(request, response, now):
        if corruption == "foreign_session":
            response["session_id"] = "foreign-session"
        else:
            publish(ipc.replies / "000002.json", b"{}", staging_directory=ipc.replies.parent)

    ipc, events, now = make_ipc(tmp_path, respond)
    published = publish_at_wait_check(ipc, now)
    try:
        with pytest.raises(EvaluationFailure, match=code):
            ipc.invoke("work", "prompt", None)
        assert published == [1] and ipc.poisoned
        assert ipc.receipts == [] and ipc.provider_requests is None
        with pytest.raises(EvaluationFailure, match="ipc_closed"):
            ipc.invoke("work", "retry forbidden", None)
        assert len(list(ipc.requests.iterdir())) == 1
    finally:
        events.close()


@pytest.mark.parametrize("corruption", [
    "session", "sequence", "operation", "model", "usage", "multiple", "empty",
])
def test_ipc_rejects_mismatch_or_invalid_accounting_and_cannot_retry(tmp_path, corruption):
    def respond(request, response, now):
        if corruption == "session":
            response["session_id"] = "another-session"
        elif corruption == "sequence":
            response["sequence"] += 1
        elif corruption == "operation":
            response["operation"] = "execute"
        elif corruption == "model":
            response["result"]["model"] = "another-model"
        elif corruption == "usage":
            response["result"]["usage"] = None
        elif corruption == "empty":
            response["result"]["text"] = ""
        else:
            response["result"]["usage"]["api_requests"] = 2

    ipc, events, _ = make_ipc(tmp_path, respond)
    try:
        with pytest.raises(EvaluationFailure):
            ipc.invoke("work", "prompt", None)
        assert ipc.poisoned
        if corruption in ("usage", "multiple", "model", "empty"):
            assert len(ipc.receipts) == 1
        if corruption == "usage":
            assert ipc.provider_requests is None
        if corruption == "multiple":
            assert ipc.provider_requests == 2
        if corruption == "empty":
            assert ipc.provider_requests == 1
        with pytest.raises(EvaluationFailure):
            ipc.invoke("work", "retry forbidden", None)
        assert len(list(ipc.requests.iterdir())) == 1
    finally:
        events.close()


def test_timeout_is_unknown_and_does_not_allocate_controller_call_id(tmp_path):
    ipc, events, now = make_ipc(tmp_path, None)
    ipc.sleep = lambda delay: now.__setitem__(0, now[0] + 181)
    try:
        with pytest.raises(EvaluationFailure, match="response_timeout") as failure:
            ipc.invoke("work", "prompt", None)
        assert failure.value.outcome_unknown
        assert ipc.receipts == []
        assert ipc.provider_requests is None
        assert ipc.poisoned
    finally:
        events.close()


@pytest.mark.parametrize("outcome", ["not_started", "known_failure", "unknown"])
def test_published_model_request_without_receipt_is_zero_only_when_not_started(tmp_path, outcome):
    def respond(request, response, now):
        response["result"] = {
            "status": "error", "code": "model_transport_failed", "outcome": outcome,
            "receipt_ref": None, "usage": None, "duration_ns": None,
        }

    ipc, events, _ = make_ipc(tmp_path, respond)
    try:
        with pytest.raises(EvaluationFailure, match="model_transport_failed") as error:
            ipc.invoke("work", "prompt", None)
        assert len(list(ipc.requests.iterdir())) == 1
        assert ipc.receipts == []
        assert ipc.provider_requests == (0 if outcome == "not_started" else None)
        assert error.value.outcome_unknown is (outcome != "not_started")
    finally:
        events.close()


@pytest.mark.parametrize("failure", ["deadline", "invalid_command"])
def test_execute_attempt_can_fail_before_request_publication(tmp_path, failure):
    ipc, events, now = make_ipc(tmp_path, None)
    if failure == "deadline":
        now[0] = 900
    try:
        with pytest.raises(EvaluationFailure) as error:
            ipc.execute("synthetic" if failure == "deadline" else "\x00")
        assert error.value.code == (
            "session_deadline" if failure == "deadline" else "invalid_command"
        )
        assert not error.value.outcome_unknown
        assert ipc.execute_attempts == 1
        assert list(ipc.requests.iterdir()) == []
        assert ipc.receipts == [] and ipc.provider_requests == 0
    finally:
        events.close()


@pytest.mark.parametrize("name", ["000001.json", "000099.json"])
def test_stale_reply_prevents_dispatch(tmp_path, name):
    ipc, events, _ = make_ipc(tmp_path, None)
    publish(ipc.replies / name, b"{}")
    try:
        with pytest.raises(EvaluationFailure, match="unexpected_ipc_reply"):
            ipc.invoke("work", "prompt", None)
        assert not list(ipc.requests.iterdir())
    finally:
        events.close()


def test_duplicate_receipt_and_changed_reply_are_rejected(tmp_path):
    def respond(request, response, now):
        response["result"] = model_result(1)

    ipc, events, _ = make_ipc(tmp_path, respond)
    try:
        ipc.invoke("work", "prompt", None)
        with pytest.raises(EvaluationFailure, match="invocation_receipt"):
            ipc.invoke("work", "another", None)
    finally:
        events.close()


@pytest.mark.parametrize("directory", ["requests", "replies"])
def test_changed_completed_ipc_files_prevent_further_dispatch(tmp_path, directory):
    ipc, events, _ = make_ipc(tmp_path, lambda *_: None)
    try:
        ipc.invoke("work", "prompt", None)
        path = getattr(ipc, directory) / "000001.json"
        path.write_bytes(b"{}")
        with pytest.raises(EvaluationFailure, match="changed_ipc"):
            ipc.invoke("work", "next forbidden", None)
        assert len(list(ipc.requests.iterdir())) == 1
    finally:
        events.close()


def test_execute_error_cannot_supply_model_receipt(tmp_path):
    def respond(request, response, now):
        response["result"] = {
            "status": "error", "code": "command_timeout", "outcome": "unknown",
            "receipt_ref": model_result()["receipt_ref"], "usage": None, "duration_ns": 1,
        }

    ipc, events, _ = make_ipc(tmp_path, respond)
    try:
        with pytest.raises(EvaluationFailure):
            ipc.execute("synthetic")
        assert ipc.receipts == [] and ipc.provider_requests == 0
        assert ipc.poisoned
    finally:
        events.close()


def test_escaped_envelope_budget_failure_has_no_dispatch(tmp_path):
    ipc, events, _ = make_ipc(tmp_path, None)
    try:
        with pytest.raises(EvaluationFailure, match="prompt_budget_exhausted"):
            ipc.invoke("work", "\x01" * 16000, None)
        assert not list(ipc.requests.iterdir())
        assert ipc.receipts == []
    finally:
        events.close()


def test_work_deadline_and_phase_enforcement(tmp_path):
    ipc, events, now = make_ipc(tmp_path, None)
    try:
        with pytest.raises(EvaluationFailure, match="phase_not_allowed"):
            ipc.invoke("memory_plan", "prompt", 1)
        now[0] = 900
        with pytest.raises(EvaluationFailure, match="session_deadline"):
            ipc.invoke("work", "prompt", None)
        assert not list(ipc.requests.iterdir())
    finally:
        events.close()


def test_planner_four_round_bound_and_phase_transition_do_not_spend_extra_call(tmp_path):
    ipc, events, _ = make_ipc(tmp_path, lambda *_: None, work=False, arm="pg_agmemory")
    try:
        for round_number in range(1, 5):
            ipc.invoke("memory_plan", "prompt", round_number)
        with pytest.raises(EvaluationFailure, match="model_budget_exhausted"):
            ipc.invoke("memory_plan", "prompt", 5)
        assert len(list(ipc.requests.iterdir())) == 4
        ipc.start_work()
        ipc.invoke("work", "work prompt", None)
        assert ipc.provider_requests == 5
        assert [receipt.global_ordinal for receipt in ipc.receipts] == [1, 2, 3, 4, 5]
    finally:
        events.close()


def test_private_files_reject_symlinks_hardlinks_permissions_and_overwrite(tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / "data.json"
    publish(target, b"{}")
    assert private_read(target, 10) == b"{}"
    with pytest.raises(FileExistsError):
        publish(target, b'{"changed":true}')
    assert private_read(target, 10) == b"{}"
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        private_read(link, 10)
    link.unlink()
    os.link(target, link)
    with pytest.raises(EvaluationFailure):
        private_read(target, 10)
    link.unlink()
    target.chmod(0o644)
    with pytest.raises(EvaluationFailure):
        private_read(target, 10)
    target.chmod(0o600)
    with pytest.raises(EvaluationFailure):
        private_read(target, 1)


def test_execute_capture_and_grade_cannot_declare_success_without_candidate():
    data = {
        "status": "ok", "exit_code": 1, "output_base64": base64.b64encode(b"failed").decode(),
        "captured_bytes": 6, "output_truncated": False, "duration_ns": 1,
    }
    assert ExecuteReply.model_validate(data).output() == b"failed"
    with pytest.raises(EvaluationFailure):
        ExecuteReply.model_validate(data | {"captured_bytes": 5})
    with pytest.raises(EvaluationFailure):
        GradeRecord(
            case_id="case-a", artifact_sha256="a" * 64, status="passed", reason=None,
            guest_started=True, candidate_started=False, exit_code=0,
            stdout_sha256="b" * 64, stderr_sha256="c" * 64, duration_ns=1,
        )
    with pytest.raises(EvaluationFailure):
        ArtifactManifest(files=(), tree_sha256="a" * 64)
