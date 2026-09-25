"""Owned synthetic Native API comparison; run only inside the Linux test guest.

The host owns Copilot authentication and publishes fresh-session file responses.
This runner never starts a provider, invokes a model CLI, or downloads a dataset.

Required private environment:
  PGAG_AGENT_EVAL_OWNED_RUN=agent-eval-<8..32 lowercase alphanumeric characters>
  PGAG_AGENT_EVAL_OWNED_CONFIG=<private owned.json path>
  PGAG_AGENT_EVAL_API_URL=<exact private IPv4 API URL bound in owned.json>
  PGAG_ADMIN_DATABASE_URL=<owned PostgreSQL URL, explicit private IPv4 and port>
  PGAG_AGENT_EVAL_JWT_PRIVATE_KEY_FILE=<private RS256 PEM path>
  PGAG_AGENT_EVAL_SOURCE_REVISION=<40-character source Git SHA>

owned.json has exactly format="pgag-agent-eval-owned-v1", run_id, api_url,
admin_url_hash (SHA-256 of the exact admin URL), jwt_issuer,
jwt_audience, and synthetic_fixture_purge_consent=true.
The bridge's transport.json has format="pgag-copilot-transport-v1", run_id,
model, reasoning_effort, cli_version, max_calls (1..100), fresh_session_per_call=true,
custom_instructions=false, tools_allowed=false, model_weights_revision_verified=false,
and optional model_revision (null when unknown).
Directories must be private (0700), files private (0600), and queue/output empty.
No secret is accepted as a command-line argument or included in reports.
Fixture JWT issued-at times are backdated by at most 30 seconds for independent
guest clocks. Expiration and the Native API's strict authentication are unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import stat
import sys
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pg_agmemory import agent_evaluation as recipe
from pg_agmemory.models import Explain, Forget, Observe, Recall, RecallFilters
from pg_agmemory.native_client import NativeSettings
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

MAX_CALLS = 100
MAX_BYTES = 65536
RESPONSE_TIMEOUT = 180.0
JWT_BACKDATE_SECONDS = 30
RUN_ID = re.compile(r"agent-eval-[a-z0-9]{8,32}\Z")
ARMS = ("no_memory", "recent_window", "pg_agmemory")


class EvaluationFailure(Exception):
    def __init__(self, code: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.outcome_unknown = outcome_unknown


def require(condition: bool, code: str) -> None:
    if not condition:
        raise EvaluationFailure(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OwnedConfig(StrictModel):
    format: Literal["pgag-agent-eval-owned-v1"]
    run_id: str
    api_url: str
    admin_url_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    jwt_issuer: Annotated[str, Field(min_length=1, max_length=256)]
    jwt_audience: Annotated[str, Field(min_length=1, max_length=256)]
    synthetic_fixture_purge_consent: Literal[True]


class TransportMetadata(StrictModel):
    format: Literal["pgag-copilot-transport-v1"]
    run_id: str
    model: Annotated[str, Field(min_length=1, max_length=128)]
    reasoning_effort: Annotated[str, Field(min_length=1, max_length=64)]
    cli_version: Annotated[str, Field(min_length=1, max_length=256)]
    max_calls: Annotated[int, Field(ge=1, le=MAX_CALLS)]
    fresh_session_per_call: Literal[True]
    custom_instructions: Literal[False]
    tools_allowed: Literal[False]
    model_weights_revision_verified: Literal[False]
    model_revision: Annotated[str, Field(min_length=1, max_length=256)] | None = None


class BridgeResponse(StrictModel):
    format: Literal["pgag-copilot-response-v1"]
    call_id: Annotated[str, Field(pattern=r"^[0-9]{6}$")]
    status: Literal["ok", "error"]
    content: str | None
    error: str | None
    duration_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    model: str
    reasoning_effort: str
    usage: dict[str, Any] | None


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (ValueError, UnicodeError, RecursionError):
        raise EvaluationFailure("invalid_json") from None


def no_symlink_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for component in (*reversed(absolute.parents), absolute):
        try:
            require(not stat.S_ISLNK(component.lstat().st_mode), "symlink_rejected")
        except FileNotFoundError:
            continue
    return absolute


def private_directory(path: Path, *, empty: bool = False) -> Path:
    path = no_symlink_path(path)
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode), "directory_required")
    require(info.st_mode & 0o077 == 0, "private_directory_required")
    if empty:
        require(not any(path.iterdir()), "nonempty_directory")
    return path


def private_read(path: Path, *, limit: int = MAX_BYTES) -> bytes:
    path = no_symlink_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode), "regular_file_required")
        require(info.st_nlink == 1, "hardlink_rejected")
        require(info.st_mode & 0o077 == 0, "private_file_required")
        require(info.st_size <= limit, "file_size_limit")
        result = bytearray()
        while len(result) <= limit:
            chunk = os.read(descriptor, min(8192, limit + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
        require(len(result) <= limit, "file_size_limit")
        return bytes(result)
    finally:
        os.close(descriptor)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def exclusive_write(path: Path, raw: bytes) -> None:
    no_symlink_path(path)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def publish_request(path: Path, raw: bytes, staging_directory: Path) -> None:
    staging = staging_directory / f".{path.name}.pending"
    exclusive_write(staging, raw)
    try:
        # link() publishes complete bytes atomically and refuses an existing call ID.
        os.link(staging, path, follow_symlinks=False)
    finally:
        staging.unlink()
        sync_directory(staging_directory)
    sync_directory(path.parent)


class Journal:
    def __init__(self, output: Path) -> None:
        self.output = private_directory(output, empty=True)
        self.path = self.output / "events.jsonl"
        exclusive_write(self.path, b"")
        self.sequence = 0

    def emit(self, phase: str, **fields: Any) -> None:
        self.sequence += 1
        raw = json_bytes({
            "sequence": self.sequence, "at": datetime.now(UTC).isoformat(),
            "phase": phase, **fields,
        }) + b"\n"
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def save(self, name: str, value: Any) -> None:
        require(bool(re.fullmatch(r"[a-zA-Z0-9_.-]+\.json", name)), "invalid_report_name")
        exclusive_write(self.output / name, json_bytes(value) + b"\n")


class FileBridge:
    def __init__(
        self, directory: Path, journal: Journal, *, run_id: str,
        timeout: float = RESPONSE_TIMEOUT, poll_interval: float = 0.1,
    ) -> None:
        require(0 < timeout <= RESPONSE_TIMEOUT, "invalid_bridge_timeout")
        require(0 < poll_interval <= 1, "invalid_poll_interval")
        self.directory = private_directory(directory)
        self.queue = private_directory(self.directory / "queue", empty=True)
        self.metadata_path = self.directory / "transport.json"
        self.metadata_raw = private_read(self.metadata_path)
        try:
            self.metadata = TransportMetadata.model_validate(parse_json(self.metadata_raw))
        except ValidationError:
            raise EvaluationFailure("invalid_transport_metadata") from None
        require(
            RUN_ID.fullmatch(run_id) is not None and self.metadata.run_id == run_id,
            "transport_run_mismatch",
        )
        self.journal = journal
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.calls: list[dict[str, Any]] = []
        self.poisoned = False
        self.inflight = False

    def check_metadata(self) -> None:
        require(private_read(self.metadata_path) == self.metadata_raw, "transport_metadata_changed")

    async def call(self, prompt: str, *, case_id: str, phase: str) -> str:
        require(not self.poisoned, "bridge_outcome_unknown")
        require(not self.inflight, "concurrent_bridge_call")
        require(isinstance(prompt, str) and bool(prompt.strip()), "invalid_prompt")
        require(len(prompt.encode("utf-8")) <= MAX_BYTES, "prompt_size_limit")
        require(len(self.calls) < self.metadata.max_calls, "llm_call_limit")
        self.check_metadata()
        call_id = f"{len(self.calls) + 1:06d}"
        request_path = self.queue / f"{call_id}.request.json"
        response_path = self.queue / f"{call_id}.response.json"
        require(
            not os.path.lexists(request_path) and not os.path.lexists(response_path),
            "duplicate_call_id",
        )
        request = json_bytes({
            "format": "pgag-copilot-request-v1", "call_id": call_id, "prompt": prompt,
        })
        require(len(request) <= MAX_BYTES, "request_size_limit")
        record: dict[str, Any] = {
            "call_id": call_id, "case_id": case_id, "call_phase": phase,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "status": "dispatch_intent", "usage": None,
        }
        self.calls.append(record)
        self.inflight = True
        start = time.monotonic()
        dispatched = False
        self.journal.emit("llm_dispatch_intent", **record)
        try:
            # A failed write may already have made the request visible to the host.
            dispatched = True
            publish_request(request_path, request, self.directory)
            self.journal.emit("llm_dispatched", call_id=call_id)
            deadline = start + self.timeout
            while not os.path.lexists(response_path):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EvaluationFailure("llm_timeout", outcome_unknown=True)
                await asyncio.sleep(min(self.poll_interval, remaining))
            require(time.monotonic() <= deadline, "llm_response_after_deadline")
            raw = private_read(response_path)
            self.journal.save(f"call-{call_id}.json", {
                "request": parse_json(request),
                "response_utf8": raw.decode("utf-8", errors="replace"),
            })
            self.check_metadata()
            try:
                response = BridgeResponse.model_validate(parse_json(raw))
            except ValidationError:
                raise EvaluationFailure("invalid_bridge_response", outcome_unknown=True) from None
            require(response.call_id == call_id, "response_call_id_mismatch")
            require(
                response.model == self.metadata.model
                and response.reasoning_effort == self.metadata.reasoning_effort,
                "response_model_mismatch",
            )
            require(
                (response.status == "ok" and isinstance(response.content, str)
                 and bool(response.content.strip()) and response.error is None)
                or (response.status == "error" and response.content is None
                    and isinstance(response.error, str) and bool(response.error)),
                "invalid_response_status",
            )
            record.update(
                status=response.status, duration_seconds=response.duration_seconds,
                elapsed_seconds=time.monotonic() - start, usage=response.usage,
            )
            if response.status == "error":
                record["error"] = "llm_bridge_error"
                record["outcome_unknown"] = True
                self.journal.emit("llm_response", **record)
                raise EvaluationFailure("llm_bridge_error", outcome_unknown=True)
            self.journal.emit("llm_response", **record)
            assert response.content is not None
            return response.content
        except BaseException as exc:
            if record["status"] != "error":
                self.poisoned = dispatched
                record.update(
                    status="failed", error=safe_error(exc)["code"],
                    outcome_unknown=dispatched, elapsed_seconds=time.monotonic() - start,
                )
                self.journal.emit("llm_failure", **record)
                if isinstance(exc, Exception) and dispatched:
                    raise EvaluationFailure(
                        safe_error(exc)["code"], outcome_unknown=True,
                    ) from None
            raise
        finally:
            self.inflight = False


def safe_error(exc: BaseException, *, mutation: bool = False) -> dict[str, Any]:
    if isinstance(exc, MemoryClientError):
        return exc.error.model_dump(mode="json")
    if isinstance(exc, EvaluationFailure):
        return {"code": exc.code, "outcome_unknown": exc.outcome_unknown or mutation}
    return {"code": type(exc).__name__, "outcome_unknown": mutation}


def owned_url(value: str, schemes: tuple[str, ...], *, database: bool = False) -> None:
    try:
        parsed = urlsplit(value)
        host = ipaddress.IPv4Address(parsed.hostname or "")
        allowed = host.is_loopback or any(
            host in network for network in (
                ipaddress.IPv4Network("10.0.0.0/8"),
                ipaddress.IPv4Network("172.16.0.0/12"),
                ipaddress.IPv4Network("192.168.0.0/16"),
            )
        )
        require(
            parsed.scheme in schemes and allowed and not parsed.query and not parsed.fragment
            and not any(character.isspace() for character in value),
            "owned_private_ipv4_url_required",
        )
        require(parsed.port is not None and 1 <= parsed.port <= 65535, "explicit_port_required")
        if database:
            require(bool(parsed.path.strip("/")), "database_name_required")
        else:
            require(
                parsed.path in ("", "/") and parsed.username is None and parsed.password is None,
                "invalid_owned_api_url",
            )
    except ValueError:
        raise EvaluationFailure("owned_private_ipv4_url_required") from None


def load_owned_config(api_url: str, environment: Mapping[str, str]) -> OwnedConfig:
    run_id = environment.get("PGAG_AGENT_EVAL_OWNED_RUN", "")
    require(RUN_ID.fullmatch(run_id) is not None, "owned_run_required")
    path = environment.get("PGAG_AGENT_EVAL_OWNED_CONFIG", "")
    require(bool(path), "owned_config_required")
    try:
        config = OwnedConfig.model_validate(parse_json(private_read(Path(path))))
    except ValidationError:
        raise EvaluationFailure("invalid_owned_config") from None
    admin_url = environment.get("PGAG_ADMIN_DATABASE_URL", "")
    require(config.run_id == run_id and config.api_url == api_url, "owned_target_mismatch")
    require(
        hashlib.sha256(admin_url.encode("utf-8")).hexdigest()
        == config.admin_url_hash, "owned_admin_mismatch",
    )
    owned_url(api_url, ("http", "https"))
    owned_url(admin_url, ("postgres", "postgresql"), database=True)
    return config


def sdk_validation_url(api_url: str) -> str:
    # Only the owned harness admits private cleartext HTTP. The shared SDK and all
    # worker/provider policies remain unchanged; its token/URL checks still run.
    if api_url.startswith("http://"):
        return "https://" + api_url.removeprefix("http://")
    return api_url


@dataclass(frozen=True)
class OwnedNativeSettings(NativeSettings):
    owned: OwnedConfig = field(repr=False)

    def __post_init__(self) -> None:
        require(
            self.api_url == self.owned.api_url and RUN_ID.fullmatch(self.owned.run_id) is not None,
            "owned_target_mismatch",
        )
        owned_url(self.api_url, ("http", "https"))
        NativeSettings(sdk_validation_url(self.api_url), self.api_token)


class OwnedMemoryClient(AsyncMemoryClient):
    def __init__(self, config: OwnedConfig, token: str) -> None:
        settings = OwnedNativeSettings(config.api_url, token, config)
        super().__init__(sdk_validation_url(config.api_url), token)
        self._settings = settings


class Provisioned(StrictModel):
    tenant_id: UUID
    principal_id: UUID
    scope_id: UUID


async def provision(subject: str, admin_url: str, journal: Journal) -> Provisioned:
    journal.emit("provision_intent", subject=subject)
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pg_agmemory.cli", "provision", "--subject", subject,
            env={
                key: value for key, value in os.environ.items()
                if key in ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "LANG", "LC_ALL")
            } | {"PGAG_ADMIN_DATABASE_URL": admin_url},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(30):
            assert process.stdout is not None
            raw = await process.stdout.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "provision_response_size")
            await process.wait()
        require(process.returncode == 0, "provision_failed")
        result = Provisioned.model_validate_json(raw)
        journal.emit("provision_completed", subject=subject, **result.model_dump(mode="json"))
        return result
    except BaseException as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        journal.emit("provision_failure", subject=subject, error=safe_error(exc, mutation=True))
        raise EvaluationFailure("provision_outcome_unknown", outcome_unknown=True) from None


def bearer(subject: str, key: bytes, config: OwnedConfig) -> str:
    now = int(time.time())
    return jwt.encode({
        "sub": subject, "iss": config.jwt_issuer, "aud": config.jwt_audience,
        "iat": now - JWT_BACKDATE_SECONDS, "exp": now + 86400,
    }, key, algorithm="RS256")


@asynccontextmanager
async def authenticated_client(
    config: OwnedConfig, subject: str, key: bytes, journal: Journal, case_id: str,
) -> AsyncIterator[AsyncMemoryClient]:
    connected = False
    started = time.monotonic()
    journal.emit("native_intent", case_id=case_id, operation="capabilities", mutation=False)
    try:
        async with OwnedMemoryClient(config, bearer(subject, key, config)) as client:
            connected = True
            journal.emit(
                "native_completed", case_id=case_id, operation="capabilities",
                elapsed_seconds=time.monotonic() - started,
            )
            yield client
    except BaseException as exc:
        if not connected:
            journal.emit(
                "native_failure", case_id=case_id, operation="capabilities",
                elapsed_seconds=time.monotonic() - started, error=safe_error(exc),
            )
        raise


async def native_call(
    journal: Journal, case_id: str, phase: str, call: Callable[[], Any],
    *, mutation: bool = False, **details: Any,
) -> Any:
    journal.emit("native_intent", case_id=case_id, operation=phase, mutation=mutation, **details)
    started = time.monotonic()
    try:
        result = await call()
    except BaseException as exc:
        journal.emit(
            "native_failure", case_id=case_id, operation=phase,
            elapsed_seconds=time.monotonic() - started, error=safe_error(exc, mutation=mutation),
        )
        raise
    journal.emit(
        "native_completed", case_id=case_id, operation=phase,
        elapsed_seconds=time.monotonic() - started,
        response=result.model_dump(mode="json"),
    )
    return result


def recall_request(scope_id: UUID, query: str, *, language: str = "en") -> Recall:
    return Recall(
        query=query, scope_ids=[scope_id], purpose="owned-synthetic-agent-memory-evaluation",
        mode="explicit", max_items=8, token_budget=8000,
        filters=RecallFilters(kind="episode"), retrieval_mode="lexical",
        search_profile="ja-janome-0.5.0-v1" if language == "ja" else "simple-v1",
    )


def returned_context(
    case: recipe.AgentMemoryCase, result: Any, memory_ids: Mapping[str, UUID],
) -> tuple[tuple[recipe.Event, ...], list[dict[str, str]]]:
    reverse = {memory_id: event_id for event_id, memory_id in memory_ids.items()}
    require(len(result.items) <= 8, "native_item_limit")
    events = []
    mapping = []
    seen: set[UUID] = set()
    for item in result.items:
        require(
            item.type == "episode" and item.memory_id in reverse
            and item.memory_id not in seen and item.occurred_at is not None,
            "foreign_or_invalid_recall_item",
        )
        seen.add(item.memory_id)
        event = recipe.Event(
            event_id=reverse[item.memory_id], text=item.content,
            occurred_at=item.occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        events.append(event)
        mapping.append({"event_id": event.event_id, "memory_id": str(item.memory_id)})
    # The recipe checks identity/content equality; never replace Native content with gold.
    delivered = recipe.bounded_context(case, events)
    return delivered, mapping


def usage_total(calls: list[dict[str, Any]], name: str) -> int | None:
    values: list[int] = []
    for call in calls:
        usage = call.get("usage")
        value = usage.get(name) if isinstance(usage, dict) else None
        if type(value) is not int or value < 0:
            return None
        values.append(value)
    return sum(values) if values else None


def observation(
    case: recipe.AgentMemoryCase, arm: recipe.Arm, bridge: FileBridge, call_start: int,
    context: tuple[recipe.Event, ...], *, answer: recipe.AnswerDecision | None = None,
    retention: recipe.RetentionDecision | None = None, error: dict[str, Any] | None = None,
    native_latencies: tuple[float, ...] = (),
) -> recipe.ArmObservation:
    calls = bridge.calls[call_start:]
    return recipe.ArmObservation(
        case_id=case.case_id, arm=arm, answer=answer, context_events=context,
        retention=retention, error=None if error is None else error["code"],
        model_latency_ms=tuple(
            float(call["duration_seconds"] * 1000)
            for call in calls if call.get("duration_seconds") is not None
        ),
        native_latency_ms=native_latencies,
        input_tokens=usage_total(calls, "input_tokens"),
        output_tokens=usage_total(calls, "output_tokens"),
    )


async def answer_arm(
    case: recipe.AgentMemoryCase, arm: recipe.Arm, context: tuple[recipe.Event, ...],
    bridge: FileBridge, journal: Journal,
) -> tuple[recipe.ArmObservation, dict[str, Any]]:
    start = len(bridge.calls)
    error = None
    answer = None
    journal.emit("arm_started", case_id=case.case_id, arm=arm)
    try:
        raw = await bridge.call(
            recipe.answer_prompt(case, context), case_id=case.case_id, phase=arm,
        )
        try:
            answer = recipe.validate_answer(case, raw, context)
        except (ValueError, TypeError):
            raise EvaluationFailure("invalid_answer_decision") from None
    except Exception as exc:
        error = safe_error(exc)
    result = observation(case, arm, bridge, start, context, answer=answer, error=error)
    detail = {
        "arm": arm, "status": "failed" if error else "completed", "error": error,
        "answer": None if answer is None else answer.model_dump(),
        "context_events": [event.model_dump() for event in context],
        "context_prompt_bytes": len(recipe.answer_prompt(case, context).encode("utf-8")),
        "call_ids": [call["call_id"] for call in bridge.calls[start:]],
    }
    journal.emit("arm_completed", case_id=case.case_id, **detail)
    return result, detail


async def verify_isolation(
    client: AsyncMemoryClient, case_id: str, foreign: Provisioned, sentinel_id: UUID,
    journal: Journal,
) -> None:
    for operation, request in (
        ("rls_foreign_scope", lambda: client.recall(recall_request(foreign.scope_id, ""))),
        ("rls_foreign_object", lambda: client.explain(Explain(memory_id=sentinel_id))),
    ):
        try:
            await native_call(journal, case_id, operation, request)
        except MemoryClientError as exc:
            require(
                exc.error.code == "not_found" and exc.error.native_status == 404
                and not exc.error.outcome_unknown, "isolation_probe_failed",
            )
            journal.emit("rls_denial_verified", case_id=case_id, operation=operation)
        else:
            raise EvaluationFailure("isolation_breach")


async def memory_arm(
    case: recipe.AgentMemoryCase, identity: Provisioned, subject: str,
    foreign: Provisioned, sentinel_id: UUID, config: OwnedConfig, key: bytes,
    bridge: FileBridge, journal: Journal,
) -> tuple[recipe.ArmObservation, dict[str, Any]]:
    start = len(bridge.calls)
    context: tuple[recipe.Event, ...] = ()
    memory_ids: dict[str, UUID] = {}
    retention = None
    answer = None
    error = None
    native_latencies = []
    detail: dict[str, Any] = {"arm": "pg_agmemory", "status": "started"}
    phase = "native_connect"
    journal.emit("arm_started", case_id=case.case_id, arm="pg_agmemory")

    async def native(
        operation: str, call: Callable[[], Any], *, mutation: bool = False, **fields: Any,
    ) -> Any:
        started = time.monotonic()
        try:
            return await native_call(
                journal, case.case_id, operation, call, mutation=mutation, **fields,
            )
        finally:
            native_latencies.append(float((time.monotonic() - started) * 1000))

    try:
        async with authenticated_client(config, subject, key, journal, case.case_id) as client:
            phase = "rls_probe"
            await verify_isolation(client, case.case_id, foreign, sentinel_id, journal)
            detail["isolation_denials_verified"] = 2
            for event in case.events:
                phase = "observe"
                idempotency_key = f"{config.run_id}:{case.case_id}:observe:{event.event_id}"
                request = Observe(
                    scope_id=identity.scope_id, source_namespace=config.run_id,
                    source_event_id=event.event_id,
                    occurred_at=datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")),
                    content=event.text, consent_reference=f"{config.run_id}:synthetic-fixture",
                    auto_extract=False, auto_embed=False,
                )
                result = await native(
                    "observe",
                    lambda request=request, idempotency_key=idempotency_key: client.observe(
                        request, idempotency_key=idempotency_key,
                    ),
                    mutation=True, event_id=event.event_id, idempotency_key=idempotency_key,
                )
                require(
                    result.memory_id not in memory_ids.values()
                    and result.synthesis_job_id is None and result.embedding_job_id is None,
                    "unexpected_observe_result",
                )
                memory_ids[event.event_id] = result.memory_id
            phase = "retention"
            raw = await bridge.call(
                recipe.retention_prompt(case), case_id=case.case_id, phase="retention",
            )
            try:
                retention = recipe.validate_retention(case, raw)
            except (ValueError, TypeError):
                raise EvaluationFailure("invalid_retention_decision") from None
            detail["retention"] = retention.model_dump()
            journal.emit(
                "retention_validated", case_id=case.case_id, decision=retention.model_dump(),
            )
            if retention.forget_ids:
                targets = [memory_ids[event_id] for event_id in retention.forget_ids]
                for mode in ("preview", "purge"):
                    phase = f"forget_{mode}"
                    idempotency_key = f"{config.run_id}:{case.case_id}:forget:{mode}"
                    request = Forget(
                        memory_ids=targets, mode=mode,
                        reason=f"Consented owned synthetic fixture retention: {config.run_id}",
                    )
                    result = await native(
                        phase,
                        lambda request=request, idempotency_key=idempotency_key: client.forget(
                            request, idempotency_key=idempotency_key,
                        ),
                        mutation=mode == "purge", idempotency_key=idempotency_key,
                        event_ids=retention.forget_ids,
                    )
                    require(result.object_count == len(targets), "forget_count_mismatch")
                    if mode == "purge":
                        require(result.scope_ids == [identity.scope_id], "purge_scope_mismatch")
                    detail[phase] = result.model_dump(mode="json")
                # Verify the real active store, not the LLM partition, before answering.
                phase = "purge_verification"
                for memory_id in targets:
                    try:
                        await native(
                            "explain_purged",
                            lambda memory_id=memory_id: client.explain(
                                Explain(memory_id=memory_id),
                            ),
                        )
                    except MemoryClientError as exc:
                        require(
                            exc.error.code == "not_found" and exc.error.native_status == 404
                            and not exc.error.outcome_unknown, "purge_verification_failed",
                        )
                    else:
                        raise EvaluationFailure("purged_episode_still_visible")
                detail["purged_objects_verified_absent"] = len(targets)
            phase = "recall_decision"
            raw = await bridge.call(
                recipe.recall_prompt(case), case_id=case.case_id, phase="recall_query",
            )
            try:
                decision = recipe.validate_recall(raw)
            except (ValueError, TypeError):
                raise EvaluationFailure("invalid_recall_decision") from None
            detail["recall_decision"] = decision.model_dump()
            phase = "recall"
            result = await native(
                "recall", lambda: client.recall(recall_request(
                    identity.scope_id, decision.query, language=case.language,
                )),
            )
            context, mapping = returned_context(case, result, memory_ids)
            require(
                not set(retention.forget_ids) & {event.event_id for event in context},
                "purged_episode_recalled",
            )
            detail["recall_mapping"] = mapping
            detail["recall_context_pack"] = result.context_pack.model_dump()
            detail["recall_coverage"] = result.coverage.model_dump()
            phase = "answer"
            raw = await bridge.call(
                recipe.answer_prompt(case, context), case_id=case.case_id, phase="pg_agmemory",
            )
            try:
                answer = recipe.validate_answer(case, raw, context)
            except (ValueError, TypeError):
                raise EvaluationFailure("invalid_answer_decision") from None
    except Exception as exc:
        answer = None
        error = safe_error(exc, mutation=phase in ("observe", "forget_purge"))
        journal.emit(
            "arm_failure", case_id=case.case_id, arm="pg_agmemory", at_phase=phase, error=error,
        )
    detail.update(
        status="failed" if error else "completed", error=error, stopped_at_phase=phase,
        answer=None if answer is None else answer.model_dump(),
        context_events=[event.model_dump() for event in context],
        context_prompt_bytes=len(recipe.answer_prompt(case, context).encode("utf-8")),
        observed_event_ids={event_id: str(value) for event_id, value in memory_ids.items()},
        call_ids=[call["call_id"] for call in bridge.calls[start:]],
        rollback_claimed=False,
    )
    result = observation(
        case, "pg_agmemory", bridge, start, context, answer=answer, retention=retention,
        error=error, native_latencies=tuple(native_latencies),
    )
    journal.emit("arm_completed", case_id=case.case_id, **detail)
    return result, detail


async def seed_sentinel(
    config: OwnedConfig, foreign: Provisioned, subject: str, key: bytes, journal: Journal,
) -> UUID:
    async with authenticated_client(config, subject, key, journal, "sentinel") as client:
        idempotency_key = f"{config.run_id}:sentinel:observe"
        request = Observe(
            scope_id=foreign.scope_id, source_namespace=config.run_id,
            source_event_id="owned-isolation-sentinel",
            occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
            content=f"Synthetic foreign-tenant isolation sentinel ONLY_FOREIGN_{config.run_id}.",
            consent_reference=f"{config.run_id}:synthetic-fixture",
        )
        result = await native_call(
            journal, "sentinel", "observe",
            lambda: client.observe(request, idempotency_key=idempotency_key),
            mutation=True, idempotency_key=idempotency_key,
        )
        return result.memory_id


def recipe_digest() -> str:
    source = Path(recipe.__file__).read_bytes()
    return hashlib.sha256(source).hexdigest()


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)] if ordered else None,
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)] if ordered else None,
    }


async def run(args: argparse.Namespace, journal: Journal) -> dict[str, Any]:
    cases = recipe.pilot_cases()
    require(len(cases) == 20, "fixed_case_count_required")
    case_details: dict[str, dict[str, Any]] = {}
    observations: dict[tuple[str, str], recipe.ArmObservation] = {}
    bridge = None
    config = None
    fatal = None
    manifest: dict[str, Any] = {
        "format": "pgag-agent-memory-evaluation-v1",
        "benchmark_qualified": False, "automatic_effects": False,
        "synthetic_fixture_only": True, "fixture_purge_consent_required": True,
        "rollback_claimed": False, "provider_or_dataset_downloads": False,
        "source_code_git_sha": os.environ.get("PGAG_AGENT_EVAL_SOURCE_REVISION"),
        "source_revision_attested": False,
        "fixture_authentication": {
            "iat_backdate_seconds": JWT_BACKDATE_SECONDS,
            "api_authentication_leeway_changed": False,
            "authentication_retries": 0,
        },
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "recipe_sha256": recipe_digest(),
        "cases_sha256": hashlib.sha256(
            json_bytes([case.model_dump() for case in cases]),
        ).hexdigest(),
        "budget": {
            "llm_calls_maximum": MAX_CALLS, "call_retries": 0,
            "response_timeout_seconds": RESPONSE_TIMEOUT,
            "prompt_bytes_maximum": MAX_BYTES, "response_bytes_maximum": MAX_BYTES,
            "recall_items_maximum": 8, "recall_context_bytes": 8000,
            "recent_window_events": 2, "recent_window_bytes": 2000,
            "lexical_profiles": {"en": "simple-v1", "ja": "ja-janome-0.5.0-v1"},
        },
        "scope": {
            "planned_owned_tenants": 21, "cases": 20, "arms": list(ARMS),
            "not_evaluated": [
                "generated_memory", "automatic_effects", "embeddings", "semantic_judging",
                "vector_retrieval", "compaction_worker", "public_benchmarks",
                "production_tenants", "backup_purge",
            ],
        },
    }
    try:
        require(sys.platform.startswith("linux"), "linux_guest_required")
        require(
            re.fullmatch(r"[0-9a-f]{40}", manifest["source_code_git_sha"] or "") is not None,
            "source_revision_required",
        )
        config = load_owned_config(args.api_url, os.environ)
        manifest["run_id"] = config.run_id
        manifest["allowed_owned_targets"] = {
            "api_url": config.api_url,
            "admin_url_hash": config.admin_url_hash,
            "attestation": "operator_owned_config_not_independent_attestation",
        }
        key_path = os.environ.get("PGAG_AGENT_EVAL_JWT_PRIVATE_KEY_FILE", "")
        require(bool(key_path), "private_key_file_required")
        key = private_read(Path(key_path), limit=16384)
        bridge = FileBridge(args.bridge, journal, run_id=config.run_id)
        manifest["budget"]["llm_calls_maximum"] = bridge.metadata.max_calls
        manifest["transport"] = bridge.metadata.model_dump() | {
            "revision_attested": False, "fresh_context_per_call_required": True,
            "tools_allowed": False,
        }
        journal.emit("run_started", manifest=manifest)
        # Provisioning is restricted to the matched owned admin target; runtime traffic
        # below never uses admin credentials or bypasses the HTTP authorization boundary.
        foreign_subject = f"{config.run_id}:foreign-sentinel"
        foreign = await provision(foreign_subject, os.environ["PGAG_ADMIN_DATABASE_URL"], journal)
        sentinel_id = await seed_sentinel(config, foreign, foreign_subject, key, journal)
        identities = [foreign]
        for case in cases:
            detail: dict[str, Any] = {
                "case_id": case.case_id, "category": case.category, "language": case.language,
                "arms": {},
            }
            case_details[case.case_id] = detail
            for arm, context in (
                ("no_memory", ()), ("recent_window", recipe.recent_context(case)),
            ):
                measured, arm_detail = await answer_arm(case, arm, context, bridge, journal)
                observations[(case.case_id, arm)] = measured
                detail["arms"][arm] = arm_detail
                if bridge.poisoned:
                    raise EvaluationFailure("bridge_outcome_unknown", outcome_unknown=True)
            subject = f"{config.run_id}:{case.case_id}"
            identity = await provision(subject, os.environ["PGAG_ADMIN_DATABASE_URL"], journal)
            require(
                all(identity.tenant_id != other.tenant_id
                    and identity.principal_id != other.principal_id
                    and identity.scope_id != other.scope_id for other in identities),
                "provision_identity_collision",
            )
            identities.append(identity)
            detail["identity"] = identity.model_dump(mode="json")
            measured, arm_detail = await memory_arm(
                case, identity, subject, foreign, sentinel_id, config, key, bridge, journal,
            )
            observations[(case.case_id, "pg_agmemory")] = measured
            detail["arms"]["pg_agmemory"] = arm_detail
            journal.emit("case_completed", case_id=case.case_id)
            if measured.error in ("isolation_breach", "foreign_or_invalid_recall_item"):
                raise EvaluationFailure("isolation_breach")
            if bridge.poisoned:
                raise EvaluationFailure("bridge_outcome_unknown", outcome_unknown=True)
    except Exception as exc:
        fatal = safe_error(exc)
        journal.emit("run_failure", error=fatal)
    finally:
        for case in cases:
            detail = case_details.setdefault(case.case_id, {
                "case_id": case.case_id, "category": case.category,
                "language": case.language, "arms": {},
            })
            for arm in ARMS:
                slot = (case.case_id, arm)
                if slot not in observations:
                    context = recipe.recent_context(case) if arm == "recent_window" else ()
                    observations[slot] = recipe.ArmObservation(
                        case_id=case.case_id, arm=arm, answer=None,
                        context_events=context, error="run_aborted_before_arm",
                    )
                    detail["arms"][arm] = {
                        "status": "not_measured",
                        "error": fatal or {"code": "run_aborted_before_arm"},
                        "answer": None, "context_events": [event.model_dump() for event in context],
                        "call_ids": [],
                    }
            detail["metadata"] = manifest
            journal.save(f"case-{case.case_id}.json", detail)
    calls = [] if bridge is None else bridge.calls
    metrics = recipe.pilot_report(tuple(observations.values()))
    failures = sum(item.answer is None for item in observations.values())
    summary = {
        **manifest, "status": "failed" if fatal or failures else "completed",
        "fatal_error": fatal, "failed_or_unmeasured_arms": failures,
        "calls": calls, "calls_dispatched": len(calls),
        "usage": {
            "input_tokens": usage_total(calls, "input_tokens"),
            "output_tokens": usage_total(calls, "output_tokens"),
            "calls_with_usage": sum(call.get("usage") is not None for call in calls),
            "unknown_usage_is_not_zero": True,
        },
        "llm_duration_seconds": latency_summary([
            float(call["duration_seconds"]) for call in calls if "duration_seconds" in call
        ]),
        "metrics": metrics,
    }
    journal.save("summary.json", summary)
    journal.emit(
        "run_completed", status=summary["status"], calls_dispatched=len(calls),
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url", default=os.environ.get("PGAG_AGENT_EVAL_API_URL"),
        help="Exact owned IPv4 URL bound in private config; defaults to PGAG_AGENT_EVAL_API_URL",
    )
    parser.add_argument("--output", required=True, type=Path, help="New or empty private directory")
    parser.add_argument(
        "--bridge", required=True, type=Path, help="Private host file-queue directory",
    )
    args = parser.parse_args()
    if not args.api_url:
        parser.error("PGAG_AGENT_EVAL_API_URL or --api-url is required")
    try:
        journal = Journal(args.output)
        result = asyncio.run(run(args, journal))
    except BaseException as exc:
        print(json.dumps({"status": "failed", "error": safe_error(exc)}), flush=True)
        return 1
    print(json.dumps({
        "status": result["status"], "calls_dispatched": result["calls_dispatched"],
        "report": str(journal.output / "summary.json"),
    }), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
