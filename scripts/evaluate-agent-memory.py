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
model, reasoning_effort, cli_version, max_calls (1..160), fresh_session_per_call=true,
custom_instructions=false, tools_allowed=false, model_weights_revision_verified=false,
and optional model_revision (null when unknown).
Directories must be private (0700), files private (0600), and queue/output empty.
No secret is accepted as a command-line argument or included in reports.
Fixture JWT issued-at times are backdated by at most 30 seconds for independent
guest clocks. Expiration and the Native API's strict authentication are unchanged.
Query policy defaults to lexical-v2, which verifies the shared Native lexical
planning contract before model dispatch. Explicit legacy-v1 preserves the original
query prompt/parser. Cohort defaults to pilot-v1; unseen-synthetic-v1 and
distractor-synthetic-v1 explicitly select separately authored synthetic datasets,
not blinded or externally held-out real-world data.
bounded-lexical-v3/v4/v5 explicitly budget two planning calls and at most five retrieval
reads per case (four searches plus required-reference revalidation). review-v1
withholds forget proposals only in this workflow, without authorizing physical purge.
v4/v5 select round-robin evidence across Native ranked results, with follow-up rounds first;
v3 retains first-admitted evidence. v3/v4 use the literal-v1 planner; v5 explicitly selects
discovery-v2 planning. v6 explicitly selects sequential-v3 planning, with up to four
one-query rounds and a higher 160-model-call ceiling (versus 120 for v3-v5). An empty
plan after the first round stops planning. All bounded policies retain four search
reads, one fresh final validation, and at most eight items/8000 context bytes.
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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
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
from pg_agmemory import bounded_recall, query_planning
from pg_agmemory.models import (
    Explain,
    Forget,
    MemoryReference,
    Observe,
    Recall,
    RecallFilters,
    RecallResult,
    SearchProfile,
)
from pg_agmemory.native_client import NativeSettings
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

MAX_CALLS = 100
MAX_TRANSPORT_CALLS = 160
MAX_BYTES = 65536
RESPONSE_TIMEOUT = 180.0
JWT_BACKDATE_SECONDS = 30
RUN_ID = re.compile(r"agent-eval-[a-z0-9]{8,32}\Z")
ARMS = ("no_memory", "recent_window", "pg_agmemory")
QueryPolicy = Literal[
    "legacy-v1", "lexical-v2", "bounded-lexical-v3", "bounded-lexical-v4", "bounded-lexical-v5",
    "bounded-lexical-v6",
]
BATCHED_QUERY_POLICIES = ("bounded-lexical-v3", "bounded-lexical-v4", "bounded-lexical-v5")
BOUNDED_QUERY_POLICIES = (*BATCHED_QUERY_POLICIES, "bounded-lexical-v6")
QUERY_POLICIES = ("legacy-v1", "lexical-v2", *BOUNDED_QUERY_POLICIES)
RetentionPolicy = Literal["model-purge-v1", "review-v1"]
RETENTION_POLICIES = ("model-purge-v1", "review-v1")
Cohort = Literal["pilot-v1", "unseen-synthetic-v1", "distractor-synthetic-v1"]
COHORTS = ("pilot-v1", "unseen-synthetic-v1", "distractor-synthetic-v1")


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
    max_calls: Annotated[int, Field(ge=1, le=MAX_TRANSPORT_CALLS)]
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
        max_calls: int = MAX_CALLS,
    ) -> None:
        require(
            type(max_calls) is int and 1 <= max_calls <= MAX_TRANSPORT_CALLS,
            "invalid_llm_call_limit",
        )
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
        self.max_calls = min(max_calls, self.metadata.max_calls)
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
        require(len(self.calls) < self.max_calls, "llm_call_limit")
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
    if isinstance(exc, bounded_recall.BoundedRecallError):
        return {"code": exc.code, "outcome_unknown": False}
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
    *, query_policy: QueryPolicy = "lexical-v2",
) -> AsyncIterator[AsyncMemoryClient]:
    require(query_policy in QUERY_POLICIES, "invalid_query_policy")
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
            if query_policy != "legacy-v1":
                await verify_query_contract(client, journal, case_id, query_policy=query_policy)
            yield client
    except BaseException as exc:
        if not connected:
            journal.emit(
                "native_failure", case_id=case_id, operation="capabilities",
                elapsed_seconds=time.monotonic() - started, error=safe_error(exc),
            )
        raise


async def verify_query_contract(
    client: AsyncMemoryClient, journal: Journal, case_id: str,
    *, query_policy: QueryPolicy = "lexical-v2",
) -> None:
    journal.emit(
        "native_intent", case_id=case_id, operation="lexical_query_contract", mutation=False,
    )
    started = time.monotonic()
    try:
        status, capabilities = await client._connection().exchange("/v1/capabilities")
        expected = query_planning.lexical_query_contract()
        received = capabilities.get("lexical_query") if isinstance(capabilities, dict) else None
        journal.emit(
            "query_contract_received", case_id=case_id, query_policy=query_policy,
            lexical_query=received,
            expected_contract_sha256=hashlib.sha256(json_bytes(expected)).hexdigest(),
        )
        require(
            status == 200 and isinstance(received, dict)
            and json_bytes(received) == json_bytes(expected),
            "lexical_query_contract_mismatch",
        )
        if query_policy in BOUNDED_QUERY_POLICIES:
            required = capabilities.get("required_context")
            journal.emit(
                "required_context_contract_received", case_id=case_id,
                required_context=required,
            )
            require(
                isinstance(required, dict)
                and type(required.get("max_refs")) is int and required["max_refs"] >= 8
                and isinstance(required.get("retrieval_modes"), list)
                and "lexical" in required["retrieval_modes"]
                and required.get("order") == "request_order"
                and required.get("budget_policy") == "all_required_or_error",
                "required_context_contract_mismatch",
            )
    except BaseException as exc:
        journal.emit(
            "native_failure", case_id=case_id, operation="lexical_query_contract",
            elapsed_seconds=time.monotonic() - started, error=safe_error(exc),
        )
        raise
    journal.emit(
        "native_completed", case_id=case_id, operation="lexical_query_contract",
        elapsed_seconds=time.monotonic() - started,
    )


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


def lexical_profile(language: str) -> SearchProfile:
    return "ja-janome-0.5.0-v1" if language == "ja" else "simple-v1"


def logical_call_limit(query_policy: QueryPolicy) -> int:
    require(query_policy in QUERY_POLICIES, "invalid_query_policy")
    if query_policy == "bounded-lexical-v6":
        return 160
    return 120 if query_policy in BOUNDED_QUERY_POLICIES else MAX_CALLS


def planning_round_limit(query_policy: QueryPolicy) -> int:
    require(query_policy in QUERY_POLICIES, "invalid_query_policy")
    if query_policy == "bounded-lexical-v6":
        return 4
    return 2 if query_policy in BOUNDED_QUERY_POLICIES else 1


def resolve_retention_policy(
    query_policy: QueryPolicy, retention_policy: RetentionPolicy | None,
) -> RetentionPolicy:
    require(query_policy in QUERY_POLICIES, "invalid_query_policy")
    if retention_policy is None:
        return "review-v1" if query_policy in BOUNDED_QUERY_POLICIES else "model-purge-v1"
    require(retention_policy in RETENTION_POLICIES, "invalid_retention_policy")
    return retention_policy


def recall_request(scope_id: UUID, query: str, *, language: str = "en") -> Recall:
    return Recall(
        query=query, scope_ids=[scope_id], purpose="owned-synthetic-agent-memory-evaluation",
        mode="explicit", max_items=8, token_budget=8000,
        filters=RecallFilters(kind="episode"), retrieval_mode="lexical",
        search_profile=lexical_profile(language),
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
        "context_available": True,
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
            result = await native_call(journal, case_id, operation, request)
        except MemoryClientError as exc:
            require(
                exc.error.code == "not_found" and exc.error.native_status == 404
                and not exc.error.outcome_unknown, "isolation_probe_failed",
            )
            journal.emit("rls_denial_verified", case_id=case_id, operation=operation)
        else:
            require(
                operation == "rls_foreign_scope" and isinstance(result, RecallResult)
                and result.items == [] and result.context_pack.text == ""
                and result.empty_reason == "not_found",
                "isolation_breach",
            )
            journal.emit(
                "rls_denial_verified", case_id=case_id, operation=operation,
                enforcement="empty_scoped_recall",
            )


async def plan_recall_query(
    case: recipe.AgentMemoryCase, bridge: FileBridge, journal: Journal,
    query_policy: QueryPolicy,
) -> tuple[str, dict[str, Any]]:
    require(query_policy in ("legacy-v1", "lexical-v2"), "invalid_query_policy")
    profile = lexical_profile(case.language)
    prompt = (
        recipe.recall_prompt(case) if query_policy == "legacy-v1"
        else query_planning.lexical_query_prompt(case.question, profile)
    )
    raw = await bridge.call(prompt, case_id=case.case_id, phase="recall_query")
    journal.emit(
        "query_plan_received", case_id=case.case_id, query_policy=query_policy,
        search_profile=profile, raw_response=raw,
    )
    try:
        if query_policy == "legacy-v1":
            compiled = recipe.validate_recall(raw).query
            plan = None
        else:
            parsed = query_planning.parse_lexical_query_plan(raw)
            compiled = parsed.query
            plan = parsed.model_dump(mode="json")
    except (ValueError, TypeError):
        raise EvaluationFailure(
            "invalid_recall_decision" if query_policy == "legacy-v1" else "invalid_query_plan",
        ) from None
    detail = {
        "query_policy": query_policy, "query_plan_raw": raw, "query_plan": plan,
        "compiled_query": compiled, "search_profile": profile,
    }
    journal.emit("query_plan_compiled", case_id=case.case_id, **detail)
    return compiled, detail


async def bounded_retrieval(
    case: recipe.AgentMemoryCase, identity: Provisioned, client: AsyncMemoryClient,
    bridge: FileBridge, journal: Journal, snapshot: datetime, excluded_ids: list[UUID],
    native: Callable[..., Awaitable[Any]], detail: dict[str, Any], memory_ids: Mapping[str, UUID],
    *, query_policy: QueryPolicy,
) -> bounded_recall.BoundedRecallResult:
    require(query_policy in BOUNDED_QUERY_POLICIES, "invalid_query_policy")
    base = recall_request(identity.scope_id, "", language=case.language).model_copy(update={
        "as_of": snapshot, "known_at": snapshot,
    })
    evidence_selection = (
        "first-admitted-v1" if query_policy == "bounded-lexical-v3" else "round-robin-v1"
    )
    if query_policy == "bounded-lexical-v6":
        planner_policy = "sequential-v3"
        workflow = bounded_recall.BoundedRecall(
            base, excluded_memory_ids=excluded_ids, evidence_selection="round-robin-v1",
            planning_schedule="sequential-v1",
        )
    else:
        planner_policy = "discovery-v2" if query_policy == "bounded-lexical-v5" else "literal-v1"
        workflow = (
            bounded_recall.BoundedRecall(
                base, excluded_memory_ids=excluded_ids, evidence_selection="round-robin-v1",
            ) if query_policy != "bounded-lexical-v3"
            else bounded_recall.BoundedRecall(base, excluded_memory_ids=excluded_ids)
        )
    progress: dict[str, Any] = {
        "query_policy": query_policy, "evidence_selection": evidence_selection,
        "planner_policy": planner_policy,
        "as_of": snapshot.isoformat(), "known_at": snapshot.isoformat(),
        "search_profile": base.search_profile, "search_calls": 0,
        "final_validation_calls": 0, "planning_calls": 0, "rounds": [],
        "excluded_memory_ids": [str(value) for value in excluded_ids],
        "read_latency_ms": [], "revalidated": False, "cached_fallback_used": False,
    }
    if query_policy == "bounded-lexical-v6":
        progress.update(
            planning_schedule="sequential-v1", planning_rounds_maximum=4,
            planning_complete=False, early_stop=False,
        )
    detail["bounded_retrieval"] = progress
    for round_number in range(1, planning_round_limit(query_policy) + 1):
        search_feedback = workflow.planning_feedback if query_policy == "bounded-lexical-v6" else ()
        if query_policy == "bounded-lexical-v6":
            prompt = bounded_recall.search_prompt(
                case.question, base.search_profile, items=workflow.planning_items,
                previous_queries=workflow.queries, round_number=round_number,
                planner_policy="sequential-v3", search_feedback=search_feedback,
            )
        elif query_policy == "bounded-lexical-v5":
            prompt = bounded_recall.search_prompt(
                case.question, base.search_profile, items=workflow.planning_items,
                previous_queries=workflow.queries, round_number=round_number,
                planner_policy="discovery-v2",
            )
        else:
            prompt = bounded_recall.search_prompt(
                case.question, base.search_profile, items=workflow.planning_items,
                previous_queries=workflow.queries, round_number=round_number,
            )
        calls_before = len(bridge.calls)
        try:
            raw = await bridge.call(
                prompt, case_id=case.case_id, phase=f"recall_query_round_{round_number}",
            )
        finally:
            progress["planning_calls"] += len(bridge.calls) - calls_before
        round_detail: dict[str, Any] = {
            "round": round_number, "raw_plan": raw, "planner_policy": planner_policy,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_bytes": len(prompt.encode("utf-8")),
        }
        if query_policy == "bounded-lexical-v6":
            round_detail["search_feedback"] = [
                entry.model_dump(mode="json") for entry in search_feedback
            ]
        progress["rounds"].append(round_detail)
        journal.emit(
            "bounded_plan_received", case_id=case.case_id, **round_detail,
            query_policy=query_policy,
        )
        plan = bounded_recall.parse_search_plan(raw, allow_empty=round_number >= 2)
        requests = workflow.requests(plan)
        if query_policy == "bounded-lexical-v6":
            require(len(requests) <= 1, "sequential_search_call_limit")
        round_detail["plan"] = plan.model_dump(mode="json")
        round_detail["compiled_queries"] = [request.query for request in requests]
        journal.emit(
            "bounded_plan_compiled", case_id=case.case_id, **round_detail,
            query_policy=query_policy,
        )
        if query_policy == "bounded-lexical-v6" and not requests:
            progress.update(early_stop=True, stop_reason="empty_plan")
            break
        for request in requests:
            progress["search_calls"] += 1
            require(progress["search_calls"] <= 4, "bounded_search_call_limit")
            started = time.monotonic()
            try:
                result = await native(
                    "bounded_search", lambda request=request: client.recall(request),
                    round=round_number, search_call=progress["search_calls"],
                    request=request.model_dump(mode="json"),
                )
            except BaseException:
                try:
                    workflow.finish(None)
                except bounded_recall.BoundedRecallError as cleanup:
                    progress["planning_cache_discarded"] = (
                        cleanup.code == "invalid_final_recall_state"
                    )
                    journal.emit(
                        "bounded_search_cache_discarded", case_id=case.case_id,
                        cleanup_code=cleanup.code,
                    )
                raise
            finally:
                progress["read_latency_ms"].append(float((time.monotonic() - started) * 1000))
            returned_context(case, result, memory_ids)
            workflow.record(request, result)
    if query_policy == "bounded-lexical-v6":
        progress.update(
            planning_complete=True,
            stop_reason="empty_plan" if progress["early_stop"] else "planning_round_limit",
            search_feedback=[
                entry.model_dump(mode="json") for entry in workflow.planning_feedback
            ],
        )
    final_request = workflow.final_request()
    final_response = None
    if final_request is not None:
        require(
            bool(final_request.required_memory_refs) and final_request.query == ""
            and final_request.max_items == len(final_request.required_memory_refs) <= 8,
            "invalid_final_reference_request",
        )
        progress["final_validation_calls"] = 1
        progress["final_request"] = final_request.model_dump(mode="json")
        started = time.monotonic()
        try:
            final_response = await native(
                "bounded_final_validation", lambda: client.recall(final_request),
                request=final_request.model_dump(mode="json"),
            )
        except BaseException:
            try:
                workflow.finish(None)
            except bounded_recall.BoundedRecallError as cleanup:
                progress["planning_cache_discarded"] = cleanup.code == "missing_final_recall"
                journal.emit(
                    "bounded_final_cache_discarded", case_id=case.case_id,
                    cleanup_code=cleanup.code,
                )
            raise
        finally:
            progress["read_latency_ms"].append(float((time.monotonic() - started) * 1000))
    result = workflow.finish(final_response)
    progress.update(
        revalidated=result.revalidated, search_calls=result.search_requests,
        compiled_queries=list(result.queries), truncated=result.truncated,
        final_validation_skipped_empty=final_request is None,
        returned_memory_ids=[str(item.memory_id) for item in result.items],
    )
    journal.emit("bounded_retrieval_completed", case_id=case.case_id, **progress)
    return result


async def memory_arm(
    case: recipe.AgentMemoryCase, identity: Provisioned, subject: str,
    foreign: Provisioned, sentinel_id: UUID, config: OwnedConfig, key: bytes,
    bridge: FileBridge, journal: Journal,
    *, query_policy: QueryPolicy = "lexical-v2",
    retention_policy: RetentionPolicy | None = None,
) -> tuple[recipe.ArmObservation, dict[str, Any]]:
    retention_policy = resolve_retention_policy(query_policy, retention_policy)
    start = len(bridge.calls)
    context: tuple[recipe.Event, ...] = ()
    memory_ids: dict[str, UUID] = {}
    retention = None
    answer = None
    error = None
    native_latencies = []
    detail: dict[str, Any] = {
        "arm": "pg_agmemory", "status": "started", "query_policy": query_policy,
        "retention_policy": retention_policy,
        "context_available": False,
    }
    phase = "native_connect"
    journal.emit("arm_started", case_id=case.case_id, arm="pg_agmemory")

    async def native(
        operation: str, call: Callable[[], Any], *, mutation: bool = False, **fields: Any,
    ) -> Any:
        started = time.monotonic()
        try:
            require(retention_policy in RETENTION_POLICIES, "invalid_retention_policy")
            return await native_call(
                journal, case.case_id, operation, call, mutation=mutation, **fields,
            )
        finally:
            native_latencies.append(float((time.monotonic() - started) * 1000))

    try:
        async with authenticated_client(
            config, subject, key, journal, case.case_id, query_policy=query_policy,
        ) as client:
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
            detail["actor_proposal"] = True
            detail["retention_proposal_raw"] = raw
            try:
                retention = recipe.validate_retention(case, raw)
            except (ValueError, TypeError):
                raise EvaluationFailure("invalid_retention_decision") from None
            detail["retention"] = retention.model_dump()
            journal.emit(
                "retention_validated", case_id=case.case_id, decision=retention.model_dump(),
            )
            excluded_ids: list[UUID] = []
            if retention_policy == "review-v1":
                from pg_agmemory.retention_review import review_retention

                phase = "retention_review"
                review = review_retention(
                    [MemoryReference(memory_id=value, revision=1) for value in memory_ids.values()],
                    [memory_ids[event_id] for event_id in retention.forget_ids],
                )
                require(review.purge_authorized is False, "review_cannot_authorize_purge")
                excluded_ids = list(review.excluded_memory_ids)
                require(
                    set(excluded_ids)
                    == {memory_ids[event_id] for event_id in retention.forget_ids},
                    "retention_review_partition_mismatch",
                )
                detail["workflow_excluded_memory_ids"] = [str(value) for value in excluded_ids]
                detail["retention_review"] = {
                    "actor_proposal": True, "purge_authorized": False,
                    "physical_purges": 0, "deletion_completed": False,
                    "deferred_count": len(excluded_ids),
                    "pending_review_refs": [
                        ref.model_dump(mode="json") for ref in review.pending_review_refs
                    ],
                    "retained_refs": [
                        ref.model_dump(mode="json") for ref in review.retained_refs
                    ],
                    "pending_scope": "this_workflow_only_not_global_erasure",
                }
                journal.emit(
                    "retention_deferred", case_id=case.case_id, **detail["retention_review"],
                )
                for memory_id in excluded_ids:
                    await native(
                        "verify_pending_readable",
                        lambda memory_id=memory_id: client.explain(Explain(memory_id=memory_id)),
                    )
                detail["retention_review"]["pending_rows_verified_readable"] = len(excluded_ids)
            elif retention.forget_ids:
                if query_policy != "legacy-v1":
                    phase = "query_contract_before_purge"
                    await verify_query_contract(
                        client, journal, case.case_id, query_policy=query_policy,
                    )
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
            if query_policy in BOUNDED_QUERY_POLICIES:
                phase = "bounded_retrieval"
                snapshot = datetime.now(UTC)
                result = await bounded_retrieval(
                    case, identity, client, bridge, journal, snapshot, excluded_ids,
                    native, detail, memory_ids, query_policy=query_policy,
                )
            else:
                phase = "recall_decision"
                compiled_query, query_detail = await plan_recall_query(
                    case, bridge, journal, query_policy,
                )
                detail.update(query_detail)
                detail["recall_decision"] = {"query": compiled_query}
                phase = "recall"
                result = await native(
                    "recall", lambda: client.recall(recall_request(
                        identity.scope_id, compiled_query, language=case.language,
                    )),
                )
                if retention_policy == "review-v1":
                    from pg_agmemory.retention_review import exclude_pending_result

                    returned_context(case, result, memory_ids)
                    result = exclude_pending_result(result, excluded_ids)
                    detail["recall_projection"] = "local_pending_review_exclusion_not_server_purge"
            context, mapping = returned_context(case, result, memory_ids)
            require(
                not set(retention.forget_ids) & {event.event_id for event in context},
                "purged_episode_recalled",
            )
            detail["context_available"] = True
            journal.emit(
                "answer_context_ready", case_id=case.case_id,
                context_events=[event.model_dump() for event in context],
            )
            detail["recall_mapping"] = mapping
            detail["recall_context_pack"] = result.context_pack.model_dump()
            detail["recall_coverage"] = (
                result.coverage.model_dump() if isinstance(result, RecallResult) else None
            )
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
    *, query_policy: QueryPolicy = "lexical-v2",
) -> UUID:
    async with authenticated_client(
        config, subject, key, journal, "sentinel", query_policy=query_policy,
    ) as client:
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


def recipe_digest(query_policy: QueryPolicy = "legacy-v1") -> str:
    source = Path(recipe.__file__).read_bytes()
    original = hashlib.sha256(source).hexdigest()
    if query_policy == "legacy-v1":
        return original
    return query_policy_metadata(query_policy)["recipe_sha256"]


def query_policy_metadata(query_policy: QueryPolicy) -> dict[str, Any]:
    require(query_policy in QUERY_POLICIES, "invalid_query_policy")
    base_digest = recipe_digest("legacy-v1")
    contract = query_planning.lexical_query_contract() if query_policy != "legacy-v1" else None
    module_digest = (
        hashlib.sha256(Path(query_planning.__file__).read_bytes()).hexdigest()
        if query_policy != "legacy-v1" else None
    )
    components = {
        "format": "pgag-agent-memory-query-recipe-v2", "query_policy": query_policy,
        "base_recipe_sha256": base_digest, "query_planning_sha256": module_digest,
        "query_planning_contract": contract,
    }
    bounded_source_sha = None
    if query_policy in BOUNDED_QUERY_POLICIES:
        bounded_source_sha = hashlib.sha256(Path(bounded_recall.__file__).read_bytes()).hexdigest()
        components.update({
            "format": "pgag-agent-memory-bounded-query-recipe-v3",
            "bounded_recall_sha256": bounded_source_sha,
            "planning_rounds": 2, "search_calls_maximum": 4,
            "final_required_reference_calls_maximum": 1,
            "max_items": 8, "context_budget_bytes": 8000,
            "fixed_temporal_anchors": "after_observation_before_planning",
        })
        if query_policy in ("bounded-lexical-v4", "bounded-lexical-v5"):
            components.update({
                "format": "pgag-agent-memory-bounded-query-recipe-v4",
                "evidence_selection": "round-robin-v1",
            })
        if query_policy == "bounded-lexical-v5":
            components.update({
                "format": "pgag-agent-memory-bounded-query-recipe-v5",
                "planner_policy": "discovery-v2",
            })
        if query_policy == "bounded-lexical-v6":
            components.update({
                "format": "pgag-agent-memory-bounded-query-recipe-v6",
                "planner_policy": "sequential-v3", "planning_schedule": "sequential-v1",
                "evidence_selection": "round-robin-v1", "planning_rounds": 4,
                "searches_per_round": 1, "logical_model_call_limit": 160,
            })
    return {
        "query_policy": query_policy, "base_recipe_sha256": base_digest,
        "query_planning_sha256": module_digest, "query_planning_contract": contract,
        "bounded_recall_sha256": bounded_source_sha,
        "recipe_digest_format": (
            components["format"] if query_policy != "legacy-v1"
            else "sha256-agent-evaluation-source-v1"
        ),
        "recipe_sha256": (
            base_digest if query_policy == "legacy-v1"
            else hashlib.sha256(json_bytes(components)).hexdigest()
        ),
        "query_planning_contract_sha256": (
            hashlib.sha256(json_bytes(contract)).hexdigest() if contract is not None else None
        ),
        "recipe_components": components if query_policy != "legacy-v1" else None,
    }


def fixed_prompt_hashes(cases: tuple[recipe.AgentMemoryCase, ...]) -> dict[str, Any]:
    def digest(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    return {
        case.case_id: {
            "retention": digest(recipe.retention_prompt(case)),
            "no_memory": digest(recipe.answer_prompt(case, ())),
            "recent_window": digest(recipe.answer_prompt(case, recipe.recent_context(case))),
        } for case in cases
    }


def select_cohort(cohort: str) -> tuple[recipe.AgentMemoryCase, ...]:
    require(cohort in COHORTS, "invalid_cohort")
    if cohort == "pilot-v1":
        cases = recipe.pilot_cases()
    elif cohort == "unseen-synthetic-v1":
        from pg_agmemory.agent_evaluation_unseen import COHORT_ID, unseen_cases

        require(COHORT_ID == cohort, "cohort_identity_mismatch")
        cases = unseen_cases()
    else:
        from pg_agmemory.agent_evaluation_distractor import COHORT_ID, distractor_cases

        require(COHORT_ID == cohort, "cohort_identity_mismatch")
        cases = distractor_cases()
    require(
        isinstance(cases, tuple) and len(cases) == 20
        and all(isinstance(case, recipe.AgentMemoryCase) for case in cases)
        and len({case.case_id for case in cases}) == 20,
        "invalid_cohort_cases",
    )
    require(
        sum(case.language == "en" for case in cases) == 10
        and sum(case.language == "ja" for case in cases) == 10
        and len({case.category for case in cases}) == 5
        and all(sum(other.category == case.category for other in cases) == 4 for case in cases)
        and sum(case.expected_answer is None for case in cases) == 4,
        "invalid_cohort_balance",
    )
    return cases


def cohort_metadata(
    cohort: str, cases: tuple[recipe.AgentMemoryCase, ...], query_policy: QueryPolicy,
    retention_policy: RetentionPolicy | None = None,
) -> dict[str, Any]:
    retention_policy = resolve_retention_policy(query_policy, retention_policy)
    require(cases == select_cohort(cohort), "cohort_dataset_mismatch")
    if cohort == "pilot-v1":
        dataset_source = Path(recipe.__file__)
    elif cohort == "unseen-synthetic-v1":
        from pg_agmemory import agent_evaluation_unseen

        dataset_source = Path(agent_evaluation_unseen.__file__)
    else:
        from pg_agmemory import agent_evaluation_distractor

        dataset_source = Path(agent_evaluation_distractor.__file__)
    scorer_source = Path(recipe.__file__).read_bytes()
    query_metadata = query_policy_metadata(query_policy)
    dataset_sha = hashlib.sha256(json_bytes([case.model_dump() for case in cases])).hexdigest()
    protected_sha = hashlib.sha256(scorer_source.split(b"def pilot_report(")[0]).hexdigest()
    review_source_sha = None
    if retention_policy == "review-v1":
        from pg_agmemory import retention_review

        review_source_sha = hashlib.sha256(Path(retention_review.__file__).read_bytes()).hexdigest()
    components = {
        "format": "pgag-agent-memory-cohort-recipe-v4", "cohort_id": cohort,
        "query_policy": query_policy, "query_recipe_sha256": query_metadata["recipe_sha256"],
        "retention_policy": retention_policy, "retention_review_sha256": review_source_sha,
        "logical_model_call_limit": logical_call_limit(query_policy),
        "query_recipe_components": query_metadata["recipe_components"],
        "dataset_sha256": dataset_sha,
        "dataset_source_sha256": hashlib.sha256(dataset_source.read_bytes()).hexdigest(),
        "scorer_source_sha256": hashlib.sha256(scorer_source).hexdigest(),
        "protected_prompt_case_scoring_source_sha256": protected_sha,
    }
    evaluation_cohort = {
        "id": cohort,
        "kind": "known_synthetic_regression_cohort", "known_cohort_reuse": True,
        "held_out": False, "held_out_external": False, "blinded_real_world": False,
        "first_use_in_owned_run": False, "first_use_scope": "not_claimed",
        "prior_model_exposure_verified": False,
        "baseline_revision": "9c84c7f" if cohort == "pilot-v1" else None,
    }
    if cohort == "distractor-synthetic-v1":
        evaluation_cohort.update({
            "kind": "synthetic_stress_cohort", "known_cohort_reuse": None,
            "first_use_in_owned_run": None, "first_use_scope": "requires_external_run_history",
        })
    return {
        **query_metadata, "cohort_id": cohort, "dataset_sha256": dataset_sha,
        "retention_policy": retention_policy, "retention_review_sha256": review_source_sha,
        "cases_sha256": dataset_sha,
        "dataset_source_sha256": components["dataset_source_sha256"],
        "scorer_source_sha256": components["scorer_source_sha256"],
        "protected_prompt_case_scoring_source_sha256": protected_sha,
        "query_recipe_sha256": query_metadata["recipe_sha256"],
        "query_recipe_components": query_metadata["recipe_components"],
        "recipe_digest_format": components["format"], "recipe_components": components,
        "recipe_sha256": hashlib.sha256(json_bytes(components)).hexdigest(),
        "fixed_prompt_sha256": fixed_prompt_hashes(cases),
        "evaluation_cohort": evaluation_cohort,
    }


def verify_cohort_metadata(
    metadata: Mapping[str, Any], cohort: str, cases: tuple[recipe.AgentMemoryCase, ...],
    query_policy: QueryPolicy, retention_policy: RetentionPolicy | None = None,
) -> None:
    expected = cohort_metadata(cohort, cases, query_policy, retention_policy)
    require(
        all(key in metadata and json_bytes(metadata[key]) == json_bytes(value)
            for key, value in expected.items()),
        "cohort_metadata_mismatch",
    )


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)] if ordered else None,
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)] if ordered else None,
    }


def evidence_coverage(
    case: recipe.AgentMemoryCase, detail: Mapping[str, Any],
) -> dict[str, Any]:
    required = set(case.required_source_ids)
    result: dict[str, Any] = {
        "required_source_count": len(required), "applicable": bool(required),
        "context_available": False, "retrieved_required_source_recall": None,
        "retrieved_required_source_ids": None, "missing_required_source_ids": None,
    }
    if detail.get("context_available") is not True:
        return result
    try:
        raw_events = detail.get("context_events")
        if not isinstance(raw_events, list):
            raise ValueError("Context not recorded")
        events = tuple(recipe.Event.model_validate(event) for event in raw_events)
        if recipe.bounded_context(case, events) != events:
            raise ValueError("Context exceeds delivered budget")
    except (ValueError, TypeError):
        return result | {"error": "invalid_evidence_context"}
    retrieved = required & {event.event_id for event in events}
    return result | {
        "context_available": True,
        "retrieved_required_source_recall": len(retrieved) / len(required) if required else None,
        "retrieved_required_source_ids": sorted(retrieved),
        "missing_required_source_ids": sorted(required - retrieved),
    }


def evidence_coverage_summary(
    cases: tuple[recipe.AgentMemoryCase, ...], details: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm in ARMS:
        rows = [details[case.case_id]["arms"][arm]["evidence_coverage"] for case in cases]
        values = [
            row["retrieved_required_source_recall"] for row in rows
            if row["retrieved_required_source_recall"] is not None
        ]
        applicable = sum(row["applicable"] for row in rows)
        arms[arm] = {
            "retrieved_required_source_recall": sum(values) / len(values) if values else None,
            "valid_denominator": len(values), "answerable_cases": applicable,
            "unknown_answerable_cases": applicable - len(values),
            "not_applicable_cases": len(rows) - applicable,
        }
    return {
        "definition": "Required-source coverage of validated context available to the reader; "
        "independent of answer citations. Post-context answer failures remain measurable.",
        "historical_required_source_recall": "citation-based; unchanged in metrics",
        "arms": arms,
    }


def review_proposal_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    def proposal_quality(value: dict[str, Any]) -> dict[str, Any]:
        result = dict(value)
        for old, new in (
            ("unsafe_deleted", "unsafe_forget_proposals"),
            ("known_unsafe_deleted", "known_unsafe_forget_proposals"),
        ):
            if old in result:
                result[new] = result.pop(old)
        return result

    result = dict(metrics)
    result["retention_proposal_quality"] = proposal_quality(result.pop("retention")) | {
        "interpretation": "model_proposal_quality_not_deletion_outcomes",
    }
    result["cases"] = []
    for item in metrics["cases"]:
        row = dict(item)
        proposal = row.pop("retention")
        row["retention_proposal_quality"] = (
            proposal_quality(proposal) if proposal is not None else None
        )
        result["cases"].append(row)
    return result


def review_execution_summary(details: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [detail["arms"]["pg_agmemory"] for detail in details.values()]
    counts = [
        row["retention_review"]["deferred_count"] if "retention_review" in row else None
        for row in rows
    ]
    return {
        "policy": "review-v1", "scope": "this_runner_not_external_trusted_actors",
        "known_actor_proposals": sum(row.get("actor_proposal") is True for row in rows),
        "physical_purges": 0, "deletion_completed": False, "purge_authorized": False,
        "deferred_count": sum(counts) if all(value is not None for value in counts) else None,
        "known_deferred_count": sum(value for value in counts if value is not None),
        "unknown_proposal_cases": sum(value is None for value in counts),
        "pending_scope": "this_workflow_only_not_global_erasure",
    }


async def run(args: argparse.Namespace, journal: Journal) -> dict[str, Any]:
    args.retention_policy = resolve_retention_policy(args.query_policy, args.retention_policy)
    cases = select_cohort(args.cohort)
    require(len(cases) == 20, "fixed_case_count_required")
    case_details: dict[str, dict[str, Any]] = {}
    observations: dict[tuple[str, str], recipe.ArmObservation] = {}
    bridge = None
    config = None
    fatal = None
    manifest: dict[str, Any] = {
        **cohort_metadata(args.cohort, cases, args.query_policy, args.retention_policy),
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
        "budget": {
            "llm_calls_maximum": logical_call_limit(args.query_policy), "call_retries": 0,
            "planning_rounds_per_case": planning_round_limit(args.query_policy),
            "retrieval_search_calls_per_case": (
                4 if args.query_policy in BOUNDED_QUERY_POLICIES else 1
            ),
            "retrieval_final_validation_calls_per_case": (
                1 if args.query_policy in BOUNDED_QUERY_POLICIES else 0
            ),
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
        bridge = FileBridge(
            args.bridge, journal, run_id=config.run_id,
            max_calls=logical_call_limit(args.query_policy),
        )
        manifest["budget"]["transport_call_ceiling"] = bridge.metadata.max_calls
        manifest["budget"]["effective_llm_calls_maximum"] = bridge.max_calls
        manifest["transport"] = bridge.metadata.model_dump() | {
            "revision_attested": False, "fresh_context_per_call_required": True,
            "tools_allowed": False,
        }
        journal.emit("run_started", manifest=manifest)
        verify_cohort_metadata(
            manifest, args.cohort, cases, args.query_policy, args.retention_policy,
        )
        # Provisioning is restricted to the matched owned admin target; runtime traffic
        # below never uses admin credentials or bypasses the HTTP authorization boundary.
        foreign_subject = f"{config.run_id}:foreign-sentinel"
        foreign = await provision(foreign_subject, os.environ["PGAG_ADMIN_DATABASE_URL"], journal)
        sentinel_id = await seed_sentinel(
            config, foreign, foreign_subject, key, journal, query_policy=args.query_policy,
        )
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
                query_policy=args.query_policy,
                retention_policy=args.retention_policy,
            )
            observations[(case.case_id, "pg_agmemory")] = measured
            detail["arms"]["pg_agmemory"] = arm_detail
            journal.emit("case_completed", case_id=case.case_id)
            if measured.error in ("isolation_breach", "foreign_or_invalid_recall_item"):
                raise EvaluationFailure("isolation_breach")
            if measured.error in (
                "lexical_query_contract_mismatch", "required_context_contract_mismatch",
            ):
                raise EvaluationFailure(measured.error)
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
                        "context_available": False,
                        "error": fatal or {"code": "run_aborted_before_arm"},
                        "answer": None, "context_events": [event.model_dump() for event in context],
                        "call_ids": [],
                    }
                detail["arms"][arm]["evidence_coverage"] = evidence_coverage(
                    case, detail["arms"][arm],
                )
                if arm == "pg_agmemory" and args.retention_policy == "review-v1":
                    quality = recipe.score_retention(
                        case, observations[slot].retention,
                    ).model_dump()
                    quality["unsafe_forget_proposals"] = quality.pop("unsafe_deleted")
                    detail["arms"][arm]["retention_proposal_quality"] = quality
                    deferred = detail["arms"][arm].get("retention_review")
                    detail["arms"][arm]["retention_execution"] = {
                        "physical_purges": 0, "deletion_completed": False,
                        "deferred_count": None if deferred is None else deferred["deferred_count"],
                        "purge_authorized": False,
                        "scope": "this_runner_not_external_trusted_actors",
                    }
            detail["metadata"] = manifest
            journal.save(f"case-{case.case_id}.json", detail)
    calls = [] if bridge is None else bridge.calls
    metrics = recipe.pilot_report(tuple(observations.values()), cases=cases)
    if args.retention_policy == "review-v1":
        metrics = review_proposal_metrics(metrics)
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
        "evidence_coverage": evidence_coverage_summary(cases, case_details),
    }
    if args.retention_policy == "review-v1":
        summary["retention_execution"] = review_execution_summary(case_details)
    journal.save("summary.json", summary)
    journal.emit(
        "run_completed", status=summary["status"], calls_dispatched=len(calls),
    )
    return summary


class UniqueChoice(argparse.Action):
    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: Any, option_string: str | None = None,
    ) -> None:
        seen = vars(namespace).setdefault("_seen_choices", set())
        if self.dest in seen:
            parser.error(f"{option_string} may be supplied only once")
        seen.add(self.dest)
        setattr(namespace, self.dest, values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--api-url", default=os.environ.get("PGAG_AGENT_EVAL_API_URL"),
        help="Exact owned IPv4 URL bound in private config; defaults to PGAG_AGENT_EVAL_API_URL",
    )
    parser.add_argument("--output", required=True, type=Path, help="New or empty private directory")
    parser.add_argument(
        "--bridge", required=True, type=Path, help="Private host file-queue directory",
    )
    parser.add_argument(
        "--query-policy", choices=QUERY_POLICIES, default="lexical-v2", action=UniqueChoice,
        help="Versioned query planning; legacy-v1 explicitly replays the original query recipe",
    )
    parser.add_argument(
        "--retention-policy", choices=RETENTION_POLICIES, default=None, action=UniqueChoice,
        help="Defaults to review-v1 for bounded-lexical-v3/v4/v5/v6, otherwise model-purge-v1",
    )
    parser.add_argument(
        "--cohort", choices=COHORTS, default="pilot-v1", action=UniqueChoice,
        help="Fixed synthetic dataset selection; no arbitrary dataset paths",
    )
    args = parser.parse_args()
    args.retention_policy = resolve_retention_policy(args.query_policy, args.retention_policy)
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
        "cohort_id": args.cohort,
        "report": str(journal.output / "summary.json"),
    }), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
