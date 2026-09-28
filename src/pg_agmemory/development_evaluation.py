"""Strict, offline contracts and private IPC for the development-work pilot."""

import base64
import binascii
import ctypes
import hashlib
import json
import math
import os
import stat
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, TypeVar
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from pg_agmemory.development_memory import (
    MAINTENANCE_PROTOCOL,
    RETRIEVAL_POLICY,
    BoundaryKeys,
    MaintenanceProtocol,
    MemoryBinding,
    MemoryBoundaryResult,
    MemoryDelivery,
    MemoryState,
    RetrievalPolicy,
)
from pg_agmemory.models import (
    Observe,
    ObserveResult,
    Recall,
    RecallResult,
    Remember,
    RememberResult,
    ReviseAssertion,
    RevisionResult,
)

if TYPE_CHECKING:
    from pg_agmemory.sdk import AsyncMemoryClient

PROTOCOL: Literal["pgag-development-controller-v1"] = "pgag-development-controller-v1"
MAX_PROMPT_BYTES = 65536
MAX_IPC_BYTES = 70000
MAX_RECORD_BYTES = 524288
MAX_SAFE_INTEGER = 9007199254740991
MAX_TRANSCRIPT_BYTES = 24576
WORK_SECONDS = 900
Arm = Literal["no_memory", "handoff", "pg_agmemory"]
ModelPhase = Literal["work", "handoff", "memory_decision", "memory_plan"]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_INTEGER)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", strict=True)]
Effort = Literal["low", "medium", "high", "xhigh"]
NativeResult = TypeVar("NativeResult", bound=BaseModel)


class EvaluationFailure(Exception):
    def __init__(self, code: str, *, outcome_unknown: bool = False) -> None:
        self.code = code
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


def require(condition: bool, code: str, *, unknown: bool = False) -> None:
    if not condition:
        raise EvaluationFailure(code, outcome_unknown=unknown)


def utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise EvaluationFailure("invalid_unicode") from None


def bounded_text(value: str, maximum: int) -> str:
    if len(utf8(value)) > maximum:
        raise ValueError("UTF-8 byte limit exceeded")
    return value


Brief = Annotated[str, AfterValidator(lambda value: bounded_text(value, 4096))]
MemoryText = Annotated[str, AfterValidator(lambda value: bounded_text(value, 2048))]
Prompt = Annotated[str, AfterValidator(lambda value: bounded_text(value, MAX_PROMPT_BYTES))]
Command = Annotated[str, AfterValidator(lambda value: bounded_text(value, 8192))]


def safe_path(value: str) -> str:
    parts = PurePosixPath(value).parts
    if (
        not value or len(utf8(value)) > 256 or "\\" in value or "\x00" in value
        or value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/"))
        or not parts
    ):
        raise ValueError("invalid relative path")
    return value


SafePath = Annotated[str, AfterValidator(safe_path)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always",
    )


def _pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        utf8(key)
        result[key] = value
    return result


def _integer(text: str) -> int:
    require(len(text) <= 17, "json_integer_range")
    value = int(text)
    require(abs(value) <= MAX_SAFE_INTEGER, "json_integer_range")
    return value


def _constant(_: str) -> JsonValue:
    raise EvaluationFailure("nonfinite_json")


def _no_float(_: str) -> float:
    raise EvaluationFailure("candidate_float")


def validate_json_value(value: JsonValue, *, candidate: bool = False, depth: int = 0) -> None:
    if isinstance(value, str):
        utf8(value)
    elif type(value) is int:
        require(abs(value) <= MAX_SAFE_INTEGER, "json_integer_range")
    elif type(value) is float:
        require(not candidate and math.isfinite(value), "invalid_json_number")
    elif isinstance(value, (dict, list)):
        require(depth < (32 if candidate else 64), "json_nesting_limit")
        children = value.values() if isinstance(value, dict) else value
        if isinstance(value, dict):
            for key in value:
                utf8(key)
        for child in children:
            validate_json_value(child, candidate=candidate, depth=depth + 1)


def parse_json(raw: bytes, *, limit: int = MAX_IPC_BYTES, candidate: bool = False) -> JsonValue:
    require(len(raw) <= limit, "json_byte_limit")
    try:
        text = raw.decode("utf-8", errors="strict")
        value: JsonValue = json.loads(
            text, object_pairs_hook=_pairs, parse_int=_integer,
            parse_float=_no_float if candidate else float, parse_constant=_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise EvaluationFailure("invalid_json") from None
    validate_json_value(value, candidate=candidate)
    return value


def json_bytes(value: Any) -> bytes:
    return utf8(json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ))


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def candidate_equal(left: JsonValue, right: JsonValue) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            candidate_equal(v, right[k]) for k, v in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            candidate_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


class SlotKey(StrictModel):
    project_id: Identifier
    milestone: Literal[1, 2, 3]
    arm: Arm

    @field_validator("milestone", mode="before")
    @classmethod
    def strict_milestone(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("milestone requires an integer")
        return value


class ModelSpec(StrictModel):
    model: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,99}$")]
    reasoning_effort: Effort


class VisibleMessage(StrictModel):
    ordinal: PositiveInt
    role: Literal["system", "user", "assistant", "observation"]
    content: Prompt


class _TranscriptBody(StrictModel):
    format: Literal["development-visible-transcript-v1"]
    brief: Brief
    messages: tuple[VisibleMessage, ...]
    omitted_message_ordinals: tuple[PositiveInt, ...]


class BoundaryTranscript(StrictModel):
    text: Annotated[str, AfterValidator(lambda value: bounded_text(value, MAX_TRANSCRIPT_BYTES))]
    sha256: Digest
    included_message_ordinals: tuple[PositiveInt, ...]
    omitted_message_ordinals: tuple[PositiveInt, ...]

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        included, omitted = self.included_message_ordinals, self.omitted_message_ordinals
        require(self.sha256 == sha256(utf8(self.text)), "transcript_hash_mismatch")
        require(list(included) == sorted(set(included)), "transcript_message_order")
        require(list(omitted) == sorted(set(omitted)), "transcript_omission_order")
        require(not set(included).intersection(omitted), "transcript_overlap")
        raw = utf8(self.text)
        parse_json(raw, limit=MAX_TRANSCRIPT_BYTES)
        body = _TranscriptBody.model_validate_json(raw)
        require(tuple(message.ordinal for message in body.messages) == included,
                "transcript_included_mismatch")
        require(body.omitted_message_ordinals == omitted, "transcript_omitted_mismatch")
        require(list(omitted + included) == sorted(omitted + included),
                "transcript_not_whole_suffix")
        return self


def boundary_transcript(brief: str, messages: list[VisibleMessage]) -> BoundaryTranscript:
    bounded_text(brief, 4096)
    ordinals = [message.ordinal for message in messages]
    require(ordinals == sorted(set(ordinals)), "visible_message_order")

    def render(start: int) -> bytes:
        return json_bytes({
            "format": "development-visible-transcript-v1",
            "brief": brief,
            "messages": [message.model_dump(mode="json") for message in messages[start:]],
            "omitted_message_ordinals": ordinals[:start],
        })

    start = len(messages)
    require(len(render(start)) <= MAX_TRANSCRIPT_BYTES, "transcript_brief_budget")
    while start and len(render(start - 1)) <= MAX_TRANSCRIPT_BYTES:
        start -= 1
    text = render(start)
    return BoundaryTranscript(
        text=text.decode("utf-8"), sha256=sha256(text),
        included_message_ordinals=tuple(ordinals[start:]),
        omitted_message_ordinals=tuple(ordinals[:start]),
    )


class ControllerInput(StrictModel):
    protocol: Literal["pgag-development-controller-v1"]
    memory_maintenance_protocol: MaintenanceProtocol
    memory_retrieval_policy: RetrievalPolicy
    run_id: Identifier
    session_id: Identifier
    slot: SlotKey
    recipe_sha256: Digest
    model: ModelSpec
    memory_binding: MemoryBinding
    memory_state: MemoryState | None

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        require(self.memory_maintenance_protocol == MAINTENANCE_PROTOCOL,
                "invalid_memory_maintenance_protocol")
        require(self.memory_retrieval_policy == RETRIEVAL_POLICY,
                "invalid_memory_retrieval_policy")
        binding = self.memory_binding
        require(binding.run_id == self.run_id and binding.project_id == self.slot.project_id
                and binding.arm == self.slot.arm, "controller_binding_mismatch")
        state = self.memory_state
        if self.slot.arm == "no_memory" or self.slot.milestone == 1:
            require(state is None, "unexpected_continuity_state")
        else:
            require(state is not None, "missing_continuity_state")
            if state is not None:
                require(state.binding == binding, "state_binding_mismatch")
                require(state.completed_boundaries == self.slot.milestone - 1,
                        "state_boundary_count")
        return self


class WorkInput(ControllerInput):
    mode: Literal["work"]
    brief: Brief
    starting_tree_sha256: Digest
    allowed_output_paths: Annotated[tuple[SafePath, ...], Field(min_length=1, max_length=32)]
    entry_point: SafePath

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        require(len(set(self.allowed_output_paths)) == len(self.allowed_output_paths),
                "duplicate_output_path")
        require(self.entry_point in self.allowed_output_paths, "entry_point_not_allowed")
        require(bool(self.brief.strip()), "empty_brief")
        return self


class BoundaryInput(ControllerInput):
    mode: Literal["boundary"]
    transcript: BoundaryTranscript
    boundary_id: Identifier
    keys: BoundaryKeys | None

    @model_validator(mode="after")
    def validate_boundary(self) -> Self:
        require(self.slot.arm != "no_memory" and self.slot.milestone != 3, "boundary_not_allowed")
        require((self.keys is not None) == (self.slot.arm == "pg_agmemory"), "boundary_keys_arm")
        return self


Config = Annotated[WorkInput | BoundaryInput, Field(discriminator="mode")]
CONFIG_ADAPTER: TypeAdapter[Config] = TypeAdapter(Config)


def parse_config(raw: bytes) -> WorkInput | BoundaryInput:
    parse_json(raw, limit=MAX_RECORD_BYTES)
    try:
        return CONFIG_ADAPTER.validate_json(raw)
    except ValidationError:
        raise EvaluationFailure("invalid_controller_config") from None


class InvocationRef(StrictModel):
    global_ordinal: Annotated[int, Field(strict=True, ge=1, le=318)]
    bridge_id: Identifier
    bridge_call_id: Annotated[str, Field(pattern=r"^[0-9]{6}$")]

    @model_validator(mode="after")
    def valid_call_id(self) -> Self:
        require(1 <= int(self.bridge_call_id) <= 160, "invalid_bridge_call_id")
        return self


class ModelUsage(StrictModel):
    input_tokens: Count
    output_tokens: Count
    cache_read_tokens: Count
    cache_write_tokens: Count
    reasoning_tokens: Count | None
    api_requests: Count
    premium_requests: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    nano_aiu: (
        Annotated[int, Field(strict=True, ge=0)]
        | Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
        | None
    )
    api_duration_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    monetary_cost_verified: Literal[False]

    @field_validator("monetary_cost_verified", mode="before")
    @classmethod
    def strict_unverified(cls, value: object) -> object:
        if value is not False:
            raise ValueError("monetary cost is explicitly unverified")
        return value


class ModelReply(StrictModel):
    status: Literal["ok"]
    text: Prompt
    receipt_ref: InvocationRef
    model: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,99}$")]
    reasoning_effort: Effort
    usage: ModelUsage
    duration_ns: Count

    @model_validator(mode="after")
    def valid_success(self) -> Self:
        require(bool(self.text.strip()), "model_response_invalid")
        return self


class ExecuteReply(StrictModel):
    status: Literal["ok"]
    exit_code: Annotated[int, Field(strict=True, ge=-255, le=255)]
    output_base64: str
    captured_bytes: Annotated[int, Field(strict=True, ge=0, le=16384)]
    output_truncated: bool
    duration_ns: Count

    def output(self) -> bytes:
        try:
            raw = base64.b64decode(self.output_base64, validate=True)
        except (ValueError, binascii.Error):
            raise EvaluationFailure("invalid_execute_base64") from None
        require(base64.b64encode(raw).decode("ascii") == self.output_base64,
                "noncanonical_execute_base64")
        require(len(raw) == self.captured_bytes, "execute_capture_length")
        return raw

    @model_validator(mode="after")
    def validate_output(self) -> Self:
        self.output()
        return self


FailureCode = Literal[
    "prompt_budget_exhausted", "model_timeout", "response_timeout", "model_transport_failed",
    "failed_transport_accounting", "model_response_invalid", "model_budget_exhausted",
    "session_deadline", "run_deadline", "command_timeout", "execute_helper_failed",
    "cancelled", "cleanup_failed", "coordinator_failed",
]


class OperationError(StrictModel):
    status: Literal["error"]
    code: FailureCode
    outcome: Literal["not_started", "known_failure", "unknown"]
    receipt_ref: InvocationRef | None
    usage: ModelUsage | None
    duration_ns: Count | None

    @model_validator(mode="after")
    def valid_accounting(self) -> Self:
        require(self.usage is None or self.receipt_ref is not None, "usage_without_receipt")
        require(self.outcome != "not_started" or (
            self.receipt_ref is None and self.usage is None
        ), "not_started_with_receipt")
        return self


class ModelBody(StrictModel):
    phase: ModelPhase
    prompt: Prompt
    planning_round: Literal[1, 2, 3, 4] | None

    @field_validator("planning_round", mode="before")
    @classmethod
    def strict_round(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("planning round requires an integer")
        return value

    @model_validator(mode="after")
    def valid_round(self) -> Self:
        require(bool(self.prompt.strip()), "empty_model_prompt")
        require((self.phase == "memory_plan") == (self.planning_round is not None),
                "invalid_planning_round")
        if self.phase == "memory_plan":
            require(len(utf8(self.prompt)) <= 8000, "planner_prompt_budget")
        return self


class ExecuteBody(StrictModel):
    command: Command

    @model_validator(mode="after")
    def valid_command(self) -> Self:
        require(bool(self.command.strip()) and "\x00" not in self.command, "invalid_command")
        return self


class Envelope(StrictModel):
    protocol: Literal["pgag-development-controller-v1"]
    session_id: Identifier
    sequence: PositiveInt


class ModelRequest(Envelope):
    operation: Literal["invoke_model"]
    body: ModelBody


class ExecuteRequest(Envelope):
    operation: Literal["execute"]
    body: ExecuteBody


class ModelEnvelope(Envelope):
    operation: Literal["invoke_model"]
    result: Annotated[ModelReply | OperationError, Field(discriminator="status")]


class ExecuteEnvelope(Envelope):
    operation: Literal["execute"]
    result: Annotated[ExecuteReply | OperationError, Field(discriminator="status")]

    @model_validator(mode="after")
    def no_model_accounting(self) -> Self:
        if isinstance(self.result, OperationError):
            require(self.result.receipt_ref is None and self.result.usage is None,
                    "execute_with_model_receipt")
        return self


Reply = Annotated[ModelEnvelope | ExecuteEnvelope, Field(discriminator="operation")]
REPLY_ADAPTER: TypeAdapter[Reply] = TypeAdapter(Reply)


EventKind = Literal[
    "controller_started", "memory_event", "native_intent", "native_result", "native_failure",
    "retrieval_completed", "work_started", "ipc_request", "ipc_reply", "visible_message",
    "work_finished", "boundary_finished", "controller_failed",
]


class EvidenceEvent(Envelope):
    kind: EventKind
    at: str
    elapsed_ns: Count
    data: dict[str, JsonValue]


class MemoryAuditEvent(StrictModel):
    kind: Identifier
    phase: Identifier
    status: Literal["started", "completed", "failed"]
    data: dict[str, JsonValue]

    @model_validator(mode="after")
    def valid_retrieval_identity(self) -> Self:
        require(self.data.get("memory_retrieval_policy") == RETRIEVAL_POLICY,
                "invalid_memory_retrieval_policy")
        return self


def audit_json(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return audit_json(value.model_dump(mode="json"))
    if isinstance(value, dict):
        require(all(isinstance(key, str) for key in value), "audit_object_key")
        return {key: audit_json(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [audit_json(child) for child in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        validate_json_value(value)
        return value
    raise EvaluationFailure("unsupported_audit_value")


class ControllerResult(StrictModel):
    protocol: Literal["pgag-development-controller-v1"]
    memory_maintenance_protocol: MaintenanceProtocol
    memory_retrieval_policy: RetrievalPolicy
    session_id: Identifier
    slot: SlotKey
    mode: Literal["work", "boundary"]
    status: Literal["submitted", "boundary_completed", "failed"]
    reason: str | None
    outcome_unknown: bool
    upstream_exit_status: str | None
    submission: Prompt | None
    query_attempts: Count
    admitted_invocations: Count
    provider_api_requests: Count | None
    execute_attempts: Count
    invocation_receipts: tuple[InvocationRef, ...]
    memory_state: MemoryState | None
    memory_delivery: MemoryDelivery | None
    boundary_result: MemoryBoundaryResult | None
    transcript: BoundaryTranscript | None
    monetary_cost_status: Literal["disabled_unverified"]
    elapsed_ns: Count

    @model_validator(mode="after")
    def valid_outcome(self) -> Self:
        require(self.admitted_invocations == len(self.invocation_receipts), "receipt_count")
        if self.slot.arm == "no_memory":
            require(self.memory_state is None and self.mode == "work", "no_memory_continuity")
        if self.status != "failed":
            require(not self.outcome_unknown
                    and self.provider_api_requests == self.admitted_invocations,
                    "successful_result_with_unknown_accounting")
        if self.status == "submitted":
            require(self.mode == "work" and self.upstream_exit_status == "Submitted"
                    and self.transcript is not None and self.memory_delivery is not None
                    and self.submission is not None and self.reason is None,
                    "invalid_submitted_result")
            require(self.boundary_result is None and self.query_attempts <= 16,
                    "invalid_work_result")
        elif self.status == "boundary_completed":
            require(self.mode == "boundary" and self.boundary_result is not None
                    and self.memory_state is not None and self.reason is None
                    and self.memory_delivery is None and self.transcript is not None
                    and self.query_attempts == 0 and self.execute_attempts == 0,
                    "invalid_boundary_result")
            assert self.boundary_result is not None
            require(self.boundary_result.state == self.memory_state, "boundary_state_mismatch")
        else:
            require(self.reason is not None, "missing_failure_reason")
        return self


class ArtifactFile(StrictModel):
    path: SafePath
    size: Annotated[int, Field(strict=True, ge=0, le=262144)]
    sha256: Digest


class ArtifactManifest(StrictModel):
    files: Annotated[tuple[ArtifactFile, ...], Field(max_length=32)]
    tree_sha256: Digest

    @model_validator(mode="after")
    def valid_files(self) -> Self:
        paths = [file.path for file in self.files]
        require(paths == sorted(set(paths)), "artifact_paths_not_unique_sorted")
        require(sum(file.size for file in self.files) <= 262144, "artifact_byte_limit")
        require(self.tree_sha256 == sha256(json_bytes([
            file.model_dump(mode="json") for file in self.files
        ])), "artifact_manifest_hash")
        return self


class GradeRecord(StrictModel):
    case_id: Identifier
    artifact_sha256: Digest | None
    status: Literal["passed", "failed", "infrastructure_unknown", "unavailable"]
    reason: str | None
    guest_started: bool
    candidate_started: bool
    exit_code: Annotated[int, Field(strict=True)] | None
    stdout_sha256: Digest | None
    stderr_sha256: Digest | None
    duration_ns: Count | None

    @model_validator(mode="after")
    def valid_grade(self) -> Self:
        require(not self.candidate_started or self.guest_started, "candidate_without_guest")
        require(not self.candidate_started or self.artifact_sha256 is not None,
                "candidate_without_artifact")
        if self.status in ("passed", "failed"):
            require(self.candidate_started and self.artifact_sha256 is not None,
                    "grade_without_candidate")
        if self.status == "passed":
            require(self.exit_code == 0 and self.reason is None, "invalid_passing_grade")
        else:
            require(self.reason is not None, "grade_failure_reason_missing")
        return self


def private_directory(path: Path, *, empty: bool = False) -> None:
    require(path.is_absolute() and path.resolve() == path, "private_directory_path")
    for component in [path, *path.parents]:
        require(not component.is_symlink(), "symlink_path_component")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0, "private_directory_permissions")
    if empty:
        require(not any(path.iterdir()), "private_directory_not_empty")


def private_read(path: Path, maximum: int) -> bytes:
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
                and info.st_size <= maximum, "private_file_invalid")
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        require(len(raw) <= maximum and info.st_size == len(raw)
                and info.st_mtime_ns == after.st_mtime_ns, "private_file_changed")
        return bytes(raw)
    finally:
        os.close(fd)


def publish(
    path: Path, raw: bytes, *, maximum: int = MAX_RECORD_BYTES,
    staging_directory: Path | None = None,
) -> None:
    private_directory(path.parent)
    require(len(raw) <= maximum, "artifact_record_budget")
    staging = staging_directory if staging_directory is not None else path.parent
    private_directory(staging)
    temporary = staging / f".{path.name}.{os.getpid()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Linux renameat2 publishes a single-link complete file without replacing a peer's file.
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


class EventSink:
    def __init__(self, directory: Path, session_id: str) -> None:
        private_directory(directory, empty=True)
        self.directory = directory
        self.session_id = session_id
        self.sequence = 0
        self.started = time.monotonic_ns()
        self._fd = os.open(directory / "events.jsonl",
                           os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)

    def emit(self, kind: EventKind, data: dict[str, JsonValue]) -> None:
        require(self._fd >= 0 and self.sequence < 1024, "event_sink_closed_or_exhausted")
        self.sequence += 1
        event = EvidenceEvent(
            protocol=PROTOCOL, session_id=self.session_id, sequence=self.sequence, kind=kind,
            at=datetime.now(UTC).isoformat(), elapsed_ns=time.monotonic_ns() - self.started,
            data=data,
        )
        raw = json_bytes(event.model_dump(mode="json")) + b"\n"
        require(len(raw) <= MAX_RECORD_BYTES, "event_record_budget")
        offset = 0
        while offset < len(raw):
            written = os.write(self._fd, raw[offset:])
            require(written > 0, "event_write_failed")
            offset += written
        os.fsync(self._fd)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1


class FileIPC:
    def __init__(
        self, directory: Path, config: WorkInput | BoundaryInput, events: EventSink,
        *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        private_directory(directory, empty=True)
        self.requests = directory / "requests"
        self.replies = directory / "replies"
        self.requests.mkdir(mode=0o700)
        self.replies.mkdir(mode=0o700)
        self.config, self.events = config, events
        self.clock, self.sleep = clock, sleep
        self.sequence = 0
        self.poisoned = False
        self.inflight = False
        self.work_deadline: float | None = None
        self.receipts: list[InvocationRef] = []
        self.provider_requests: int | None = 0
        self.execute_attempts = 0
        self._phase_attempts: dict[str, int] = {}
        self._request_hashes: dict[str, str] = {}
        self._reply_hashes: dict[str, str] = {}

    def start_work(self) -> None:
        self.check()
        require(self.config.mode == "work" and self.work_deadline is None, "work_already_started")
        self.events.emit("work_started", {"work_limit_seconds": WORK_SECONDS})
        self.work_deadline = self.clock() + WORK_SECONDS

    def _check_memory_identity(self) -> None:
        for field, expected_policy, code in (
            ("memory_retrieval_policy", RETRIEVAL_POLICY, "invalid_memory_retrieval_policy"),
            ("memory_maintenance_protocol", MAINTENANCE_PROTOCOL,
             "invalid_memory_maintenance_protocol"),
        ):
            value = getattr(self.config, field, None)
            if type(value) is not str or value != expected_policy:
                self.poisoned = True
                raise EvaluationFailure(code)

    def check(self) -> None:
        self._check_memory_identity()
        require(not self.poisoned and not self.inflight, "ipc_closed")
        if self.work_deadline is not None:
            require(self.clock() < self.work_deadline, "session_deadline")
        require({entry.name for entry in self.requests.iterdir()} == set(self._request_hashes),
                "unexpected_ipc_request")
        for name, expected in self._request_hashes.items():
            require(sha256(private_read(self.requests / name, MAX_IPC_BYTES)) == expected,
                    "changed_ipc_request")
        names = {entry.name for entry in self.replies.iterdir()}
        require(names == set(self._reply_hashes), "unexpected_ipc_reply")
        for name, expected in self._reply_hashes.items():
            require(sha256(private_read(self.replies / name, MAX_IPC_BYTES)) == expected,
                    "changed_ipc_reply")

    def _receipt(self, receipt: InvocationRef, usage: ModelUsage | None) -> None:
        if self.receipts:
            previous = self.receipts[-1]
            if not (
                receipt.global_ordinal > previous.global_ordinal
                and receipt.bridge_id == previous.bridge_id
                and int(receipt.bridge_call_id) > int(previous.bridge_call_id)
            ):
                self.provider_requests = None
                raise EvaluationFailure(
                    "reused_or_changed_invocation_receipt", outcome_unknown=True,
                )
        self.receipts.append(receipt)
        if usage is None:
            self.provider_requests = None
        elif self.provider_requests is not None:
            self.provider_requests += usage.api_requests

    def _exchange(self, body: ModelBody | ExecuteBody) -> ModelReply | ExecuteReply:
        self.check()
        self.sequence += 1
        name = f"{self.sequence:06d}.json"
        request: ModelRequest | ExecuteRequest
        if isinstance(body, ModelBody):
            request = ModelRequest(
                protocol=PROTOCOL, session_id=self.config.session_id, sequence=self.sequence,
                operation="invoke_model", body=body,
            )
            wait = 180
        else:
            request = ExecuteRequest(
                protocol=PROTOCOL, session_id=self.config.session_id, sequence=self.sequence,
                operation="execute", body=body,
            )
            wait = 45
        raw = json_bytes(request.model_dump(mode="json"))
        require(len(raw) <= MAX_IPC_BYTES, "prompt_budget_exhausted")
        self.events.emit("ipc_request", {
            "request": request.model_dump(mode="json"), "sha256": sha256(raw),
        })
        deadline = self.clock() + wait
        if self.work_deadline is not None:
            deadline = min(deadline, self.work_deadline)
        self.inflight = True
        try:
            publish(self.requests / name, raw, maximum=MAX_IPC_BYTES,
                    staging_directory=self.requests.parent)
            self._request_hashes[name] = sha256(raw)
            completed_names = set(self._reply_hashes)
            expected_names = completed_names | {name}
            while True:
                names = {entry.name for entry in self.replies.iterdir()}
                require(names == completed_names or names == expected_names,
                        "unexpected_ipc_reply", unknown=True)
                if name in names:
                    break
                require(self.clock() < deadline, "response_timeout", unknown=True)
                self.sleep(min(0.02, max(0, deadline - self.clock())))
            require(self.clock() < deadline, "late_ipc_reply", unknown=True)
            require({entry.name for entry in self.replies.iterdir()}
                    == expected_names, "unexpected_ipc_reply", unknown=True)
            response = private_read(self.replies / name, MAX_IPC_BYTES)
            parsed = parse_json(response)
            try:
                reply = REPLY_ADAPTER.validate_json(response)
            except (ValidationError, EvaluationFailure) as error:
                if (
                    request.operation == "invoke_model" and isinstance(parsed, dict)
                    and parsed.get("protocol") == PROTOCOL
                    and parsed.get("session_id") == request.session_id
                    and type(parsed.get("sequence")) is int
                    and parsed.get("sequence") == request.sequence
                    and parsed.get("operation") == request.operation
                    and isinstance(parsed.get("result"), dict)
                ):
                    result = parsed["result"]
                    assert isinstance(result, dict)
                    try:
                        receipt = InvocationRef.model_validate_json(
                            json_bytes(result["receipt_ref"]),
                        )
                    except (ValidationError, KeyError, EvaluationFailure):
                        self.provider_requests = None
                    else:
                        try:
                            usage = ModelUsage.model_validate_json(json_bytes(result["usage"]))
                        except (ValidationError, KeyError, EvaluationFailure):
                            usage = None
                        self._receipt(receipt, usage)
                    self._reply_hashes[name] = sha256(response)
                    self.events.emit("ipc_reply", {"invalid_reply": parsed})
                    code = (
                        "model_response_invalid"
                        if isinstance(error, EvaluationFailure)
                        and error.code == "model_response_invalid"
                        else "failed_transport_accounting"
                    )
                    raise EvaluationFailure(
                        code, outcome_unknown=True,
                    ) from None
                raise EvaluationFailure("invalid_ipc_reply", outcome_unknown=True) from None
            require(reply.session_id == request.session_id and reply.sequence == request.sequence
                    and reply.operation == request.operation, "ipc_reply_mismatch", unknown=True)
            self._reply_hashes[name] = sha256(response)
            self.events.emit("ipc_reply", {"reply": reply.model_dump(mode="json")})
            value = reply.result
            if isinstance(value, ModelReply):
                self._receipt(value.receipt_ref, value.usage)
                require(value.usage.api_requests == 1, "failed_transport_accounting", unknown=True)
                require(value.model == self.config.model.model
                        and value.reasoning_effort == self.config.model.reasoning_effort,
                        "model_identity_mismatch", unknown=True)
            elif isinstance(value, OperationError):
                if value.receipt_ref is not None:
                    self._receipt(value.receipt_ref, value.usage)
                elif request.operation == "invoke_model" and value.outcome != "not_started":
                    self.provider_requests = None
                unknown = value.outcome == "unknown" or (
                    request.operation == "invoke_model" and value.outcome != "not_started"
                    and (value.receipt_ref is None or value.usage is None)
                )
                raise EvaluationFailure(value.code, outcome_unknown=unknown)
            self.inflight = False
            self.check()
            return value
        except (EvaluationFailure, OSError) as error:
            self.poisoned = True
            if request.operation == "invoke_model" and name not in self._reply_hashes:
                self.provider_requests = None
                if isinstance(error, EvaluationFailure):
                    error.outcome_unknown = True
            raise
        finally:
            self.inflight = False

    def invoke(self, phase: ModelPhase, prompt: str, planning_round: int | None) -> ModelReply:
        self._check_memory_identity()
        allowed = (
            {"handoff"} if self.config.slot.arm == "handoff"
            else {"memory_decision"} if self.config.slot.arm == "pg_agmemory" else set()
        ) if self.config.mode == "boundary" else (
            {"work", "memory_plan"} if self.config.slot.arm == "pg_agmemory" else {"work"}
        )
        require(phase in allowed, "model_phase_not_allowed")
        require((phase == "work") == (self.work_deadline is not None), "model_phase_work_boundary")
        attempts = self._phase_attempts.get(phase, 0)
        require(attempts < (16 if phase == "work" else 4 if phase == "memory_plan" else 1),
                "model_budget_exhausted")
        if phase == "memory_plan":
            require(planning_round == attempts + 1, "out_of_order_planning_round")
        try:
            body = ModelBody.model_validate({
                "phase": phase, "prompt": prompt, "planning_round": planning_round,
            })
        except ValidationError:
            raise EvaluationFailure("prompt_budget_exhausted") from None
        self._phase_attempts[phase] = attempts + 1
        value = self._exchange(body)
        require(isinstance(value, ModelReply), "expected_model_reply", unknown=True)
        assert isinstance(value, ModelReply)
        return value

    def execute(self, command: str) -> ExecuteReply:
        self._check_memory_identity()
        require(self.config.mode == "work" and self.work_deadline is not None,
                "execute_outside_work")
        require(self.execute_attempts < 16, "execute_budget_exhausted")
        self.execute_attempts += 1
        try:
            body = ExecuteBody(command=command)
        except ValidationError:
            raise EvaluationFailure("invalid_command") from None
        value = self._exchange(body)
        require(isinstance(value, ExecuteReply), "expected_execute_reply", unknown=True)
        assert isinstance(value, ExecuteReply)
        return value


class InstrumentedNative:
    """Only the four authorized memory methods; setup validation stays in the SDK."""

    def __init__(
        self, client: "AsyncMemoryClient", events: EventSink, check: Callable[[], None],
    ) -> None:
        self._client, self._events, self._check = client, events, check

    async def _call(
        self, operation: str, request: BaseModel, key: str | None,
        action: Callable[[], Awaitable[NativeResult]], memory_id: UUID | None = None,
    ) -> NativeResult:
        from pg_agmemory.sdk import MemoryClientError

        self._check()
        self._events.emit("native_intent", {
            "operation": operation, "idempotency_key": key,
            "memory_id": str(memory_id) if memory_id is not None else None,
            "request": request.model_dump(mode="json"),
        })
        started = time.monotonic_ns()
        try:
            result = await action()
        except MemoryClientError as exc:
            self._events.emit("native_failure", {
                "operation": operation, "idempotency_key": key,
                "duration_ns": time.monotonic_ns() - started,
                "error": exc.error.model_dump(mode="json"),
            })
            raise
        self._events.emit("native_result", {
            "operation": operation, "idempotency_key": key,
            "duration_ns": time.monotonic_ns() - started,
            "response": result.model_dump(mode="json"),
        })
        self._check()
        return result

    async def observe(self, request: Observe, *, idempotency_key: str) -> ObserveResult:
        result: ObserveResult = await self._call(
            "observe", request, idempotency_key,
            lambda: self._client.observe(request, idempotency_key=idempotency_key),
        )
        return result

    async def remember(self, request: Remember, *, idempotency_key: str) -> RememberResult:
        result: RememberResult = await self._call(
            "remember", request, idempotency_key,
            lambda: self._client.remember(request, idempotency_key=idempotency_key),
        )
        return result

    async def revise_assertion(
        self, memory_id: UUID, request: ReviseAssertion, *, idempotency_key: str,
    ) -> RevisionResult:
        result: RevisionResult = await self._call(
            "revise_assertion", request, idempotency_key,
            lambda: self._client.revise_assertion(
                memory_id, request, idempotency_key=idempotency_key,
            ), memory_id,
        )
        return result

    async def recall(self, request: Recall) -> RecallResult:
        result: RecallResult = await self._call(
            "recall", request, None, lambda: self._client.recall(request),
        )
        return result


class InstrumentedNativeFactory:
    def __init__(
        self, api_url: str, api_token: str, events: EventSink, check: Callable[[], None],
    ) -> None:
        from urllib.parse import urlsplit

        from pg_agmemory.native_client import NativeSettings

        settings = NativeSettings(api_url, api_token)
        require(urlsplit(settings.api_url).scheme == "http", "loopback_native_api_required")
        self._settings, self._events, self._check = settings, events, check

    @asynccontextmanager
    async def _open(self) -> AsyncIterator[InstrumentedNative]:
        from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

        self._check()
        self._events.emit("native_intent", {"operation": "sdk_capability_setup"})
        started = time.monotonic_ns()
        client = AsyncMemoryClient(self._settings.api_url, self._settings.api_token)
        try:
            await client.__aenter__()
        except MemoryClientError as exc:
            self._events.emit("native_failure", {
                "operation": "sdk_capability_setup", "duration_ns": time.monotonic_ns() - started,
                "error": exc.error.model_dump(mode="json"),
            })
            raise
        try:
            self._events.emit("native_result", {
                "operation": "sdk_capability_setup", "duration_ns": time.monotonic_ns() - started,
                "validated_by_sdk": True,
            })
            self._check()
            yield InstrumentedNative(client, self._events, self._check)
        finally:
            await client.__aexit__(None, None, None)

    def __call__(self) -> AbstractAsyncContextManager[InstrumentedNative]:
        return self._open()
