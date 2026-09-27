#!/usr/bin/env python3
"""Fresh trusted controller; no model commands execute in this process."""

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pg_agmemory.development_evaluation import ControllerResult


def arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("config", "ipc", "output", "home"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    options = [value.split("=", 1)[0] for value in argv if value.startswith("--")]
    if len(options) != len(set(options)):
        parser.error("duplicate option")
    return parser.parse_args(argv)


def bootstrap(home: Path) -> None:
    if not sys.platform.startswith("linux"):
        raise ValueError("linux_controller_required")
    if any(name == "minisweagent" or name.startswith("minisweagent.") for name in sys.modules):
        raise ValueError("upstream_imported_before_bootstrap")
    if not home.is_absolute() or home.resolve() != home:
        raise ValueError("invalid_controller_home")
    for component in [home, *home.parents]:
        if component.is_symlink():
            raise ValueError("symlink_controller_home")
    info = home.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
        or info.st_mode & 0o077 or any(home.iterdir())
    ):
        raise ValueError("controller_home_not_fresh_private")
    os.umask(0o077)
    config = home / "config"
    config.mkdir(mode=0o700)
    upstream = config / "mini-swe-agent"
    upstream.mkdir(mode=0o700)
    retained = {
        key: value for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TZ",
                   "PGAG_DEVELOPMENT_API_URL", "PGAG_DEVELOPMENT_API_TOKEN"}
    }
    os.environ.clear()
    os.environ.update(retained)
    os.environ.update(
        HOME=str(home), XDG_CONFIG_HOME=str(config), MSWEA_GLOBAL_CONFIG_DIR=str(upstream),
        MSWEA_SILENT_STARTUP="1", PYTHONDONTWRITEBYTECODE="1",
    )
    os.chdir(home)


def run(config_path: Path, ipc_path: Path, output_path: Path) -> "ControllerResult":
    import asyncio
    import time
    from datetime import UTC, datetime

    from pg_agmemory.bounded_recall import BoundedRecallError
    from pg_agmemory.development_agent import run_agent
    from pg_agmemory.development_evaluation import (
        PROTOCOL,
        BoundaryInput,
        ControllerResult,
        EvaluationFailure,
        EventSink,
        FileIPC,
        InstrumentedNativeFactory,
        MemoryAuditEvent,
        VisibleMessage,
        WorkInput,
        audit_json,
        boundary_transcript,
        json_bytes,
        parse_config,
        private_read,
        publish,
        require,
        utf8,
    )
    from pg_agmemory.development_memory import (
        DevelopmentMemory,
        DevelopmentMemoryError,
        NativeFactory,
    )

    config = parse_config(private_read(config_path, 524288))
    events = EventSink(output_path, config.session_id)
    ipc = FileIPC(ipc_path, config, events)
    started = time.monotonic_ns()
    state = config.memory_state
    delivery = None
    boundary = None
    transcript = None
    submission = None
    upstream_exit = None
    query_attempts = 0
    reason = None
    unknown = False
    status: Literal["submitted", "boundary_completed", "failed"] = "failed"
    native_error_type: type[Exception] | None = None
    memory_phase: Literal["memory_delivery", "memory_boundary"] | None = None

    def emit_memory(event: dict[str, Any]) -> None:
        ipc.check()
        checked = MemoryAuditEvent.model_validate(audit_json(event))
        events.emit("memory_event", checked.model_dump(mode="json"))

    def record(message: VisibleMessage) -> None:
        events.emit("visible_message", message.model_dump(mode="json"))

    try:
        events.emit("controller_started", {
            "mode": config.mode, "run_id": config.run_id,
            "slot": config.slot.model_dump(mode="json"), "recipe_sha256": config.recipe_sha256,
            "model": config.model.model_dump(mode="json"),
            "memory_binding": config.memory_binding.model_dump(mode="json"),
            "fresh_process": True, "monetary_cost_status": "disabled_unverified",
        })
        url = os.environ.pop("PGAG_DEVELOPMENT_API_URL", None)
        token = os.environ.pop("PGAG_DEVELOPMENT_API_TOKEN", None)
        native_factory: NativeFactory | None = None
        if config.slot.arm == "pg_agmemory":
            from pg_agmemory.sdk import MemoryClientError

            native_error_type = MemoryClientError
            require(url is not None and token is not None, "native_authorization_missing")
            assert url is not None and token is not None
            native_factory = InstrumentedNativeFactory(url, token, events, ipc.check)
        else:
            require(url is None and token is None, "unexpected_native_authorization")
        memory = DevelopmentMemory(
            config.memory_binding, session_number=config.slot.milestone, state=state,
            native_factory=native_factory, invoke=ipc.invoke, emit=emit_memory,
            now=lambda: datetime.now(UTC),
        )
        if isinstance(config, WorkInput):
            memory_phase = "memory_delivery"
            delivery = asyncio.run(memory.deliver(config.brief))
            memory_phase = None
            require(len(utf8(delivery.text)) <= 2048, "memory_delivery_budget")
            require(delivery.byte_count == len(utf8(delivery.text)), "memory_delivery_byte_count")
            events.emit("retrieval_completed", delivery.model_dump(mode="json"))
            ipc.start_work()
            work = run_agent(config, delivery.text, ipc.invoke, ipc.execute, record)
            query_attempts = work.query_attempts
            upstream_exit = work.exit_status
            transcript = boundary_transcript(config.brief, list(work.visible_messages))
            if work.failure_code is None:
                ipc.check()
                status = "submitted"
                submission = work.submission
            else:
                reason, unknown = work.failure_code, work.outcome_unknown
            events.emit("work_finished", {
                "status": status, "reason": reason, "outcome_unknown": unknown,
                "upstream_exit_status": upstream_exit, "query_attempts": query_attempts,
                "transcript_sha256": transcript.sha256,
            })
        else:
            assert isinstance(config, BoundaryInput)
            memory_phase = "memory_boundary"
            boundary = asyncio.run(memory.maintain(
                config.transcript, boundary_id=config.boundary_id, keys=config.keys,
            ))
            memory_phase = None
            ipc.check()
            state = boundary.state
            transcript = config.transcript
            status = "boundary_completed"
            events.emit("boundary_finished", boundary.model_dump(mode="json"))
    except BoundedRecallError as exc:
        status, state, boundary = "failed", config.memory_state, None
        reason = exc.code
        unknown = ipc.inflight or ipc.poisoned
        events.emit("controller_failed", {
            "code": reason, "outcome_unknown": unknown,
            "exception_type": "BoundedRecallError",
            "origin": "pg_agmemory.bounded_recall.BoundedRecallError", "phase": memory_phase,
        })
    except (EvaluationFailure, DevelopmentMemoryError) as exc:
        status, state, boundary = "failed", config.memory_state, None
        reason = exc.code
        unknown = exc.outcome_unknown
        events.emit("controller_failed", {"code": reason, "outcome_unknown": unknown})
    except Exception as exc:
        status, state, boundary = "failed", config.memory_state, None
        # Do not serialize exception text: SDK/config exceptions can contain credentials.
        if native_error_type is not None and isinstance(exc, native_error_type):
            from pg_agmemory.sdk import MemoryClientError

            assert isinstance(exc, MemoryClientError)
            reason, unknown = exc.error.code, exc.error.outcome_unknown
        else:
            reason = "controller_exception"
            unknown = ipc.inflight or ipc.poisoned
        events.emit("controller_failed", {
            "code": reason, "exception_type": type(exc).__name__, "outcome_unknown": unknown,
        })
    try:
        result = ControllerResult(
            protocol=PROTOCOL, session_id=config.session_id, slot=config.slot, mode=config.mode,
            status=status, reason=reason, outcome_unknown=unknown,
            upstream_exit_status=upstream_exit, submission=submission,
            query_attempts=query_attempts,
            admitted_invocations=len(ipc.receipts), provider_api_requests=ipc.provider_requests,
            execute_attempts=ipc.execute_attempts, invocation_receipts=tuple(ipc.receipts),
            memory_state=state, memory_delivery=delivery, boundary_result=boundary,
            transcript=transcript, monetary_cost_status="disabled_unverified",
            elapsed_ns=time.monotonic_ns() - started,
        )
        publish(output_path / "result.json", json_bytes(result.model_dump(mode="json")))
        return result
    finally:
        events.close()


def main(argv: list[str] | None = None) -> int:
    args = arguments(sys.argv[1:] if argv is None else argv)
    try:
        bootstrap(args.home)
        result = run(args.config, args.ipc, args.output)
    except Exception as exc:
        print(f"development_controller_startup_failed:{type(exc).__name__}", file=sys.stderr)
        return 2
    return 0 if result.status in ("submitted", "boundary_completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
