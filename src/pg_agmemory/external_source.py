"""Explicit historical snapshots, not a source connector or a live authority.

Use a dedicated source/dataset/principal scope and a caller-owned Native SDK
identity. Operators independently bind expiring scope-access grants to the source
authorization lease and revoke them on explicit source notifications. Neither an
ACL version assertion nor a digest validates authorization or authenticity.
Automatic retention of shared business data stays off until trusted source
authentication and notification handling are connected.

Only bounded snapshot text and opaque provenance identifiers are accepted; there
are no SQL, source URL, credential, connector, or provider configuration fields.
Caller sanitization and consent are prerequisites: labels and query IDs must be
non-secret, and snapshot text must be approved for retention. No heuristic secret
detection is performed.
"""

import hashlib
import json
from dataclasses import KW_ONLY, dataclass, field
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticSerializationError

# Import the SDK first so its optional-dependency error names the existing extra.
from pg_agmemory import sdk
from pg_agmemory.models import (
    Contract,
    EpisodeExplanation,
    Explain,
    Observe,
    ObserveResult,
    ShortText,
)
from pg_agmemory.native_client import MAX_REQUEST_BYTES, AdapterError, failure

SOURCE_FORMAT: Final = "pgag-external-snapshot-v1"
SOURCE_NAMESPACE: Final = "external-snapshot-v1"
_MISMATCH = "External source snapshot mismatch"

__all__ = [
    "SOURCE_FORMAT",
    "SOURCE_NAMESPACE",
    "ExternalSnapshot",
    "ExternalSourceMemory",
    "SnapshotEnvelope",
    "SourceBinding",
    "SourceCaptureOutcome",
    "snapshot_digest",
]


def snapshot_digest(text: str) -> str:
    """SHA-256 of exact UTF-8 text; normalization is identity, not authenticity.

    Whitespace, line endings, and Unicode code points are preserved. Invalid
    UTF-8 input is rejected without including the text in the SDK error.
    """
    if not isinstance(text, str):
        raise failure("invalid_request")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise failure("invalid_request") from None
    return hashlib.sha256(encoded).hexdigest()


class SourceBinding(Contract):
    """Operator-bound opaque provenance; source_subject is never an access token."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    scope_id: UUID
    source_system: ShortText
    dataset_id: ShortText
    source_subject: ShortText


class ExternalSnapshot(Contract):
    """A caller-supplied historical observation that always requires source refresh."""

    model_config = ConfigDict(hide_input_in_errors=True)

    semantic_revision: ShortText
    query_id: ShortText
    observed_at: AwareDatetime
    acl_version: ShortText
    snapshot_text: Annotated[
        str, StringConstraints(min_length=1, max_length=32768, strip_whitespace=False)
    ]
    result_digest: Annotated[
        str,
        StringConstraints(strip_whitespace=False),
        Field(pattern=r"^[0-9a-f]{64}$"),
    ]
    requires_refresh: Literal[True] = True
    source_authority: Literal["external_observation"] = "external_observation"

    @field_validator("requires_refresh", mode="before")
    @classmethod
    def require_refresh(cls, value: object) -> object:
        if value is not True:
            raise ValueError("External snapshots require source refresh")
        return value

    @model_validator(mode="after")
    def valid_digest(self) -> Self:
        if self.result_digest != snapshot_digest(self.snapshot_text):
            raise ValueError("External snapshot digest mismatch")
        return self


class SnapshotEnvelope(Contract):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    format: Literal["pgag-external-snapshot-v1"]
    source: SourceBinding
    snapshot: ExternalSnapshot


class SourceCaptureOutcome(BaseModel):
    """Memory storage status never changes the already-successful source query."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    source_query_status: Literal["succeeded"] = "succeeded"
    memory_capture_status: Literal["stored", "failed", "outcome_unknown"]
    memory: ObserveResult | None = None
    error: AdapterError | None = None

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if self.memory_capture_status == "stored":
            if self.memory is None or self.error is not None:
                raise ValueError("Stored capture requires only a memory receipt")
        elif (
            self.memory is not None
            or self.error is None
            or self.error.outcome_unknown != (self.memory_capture_status == "outcome_unknown")
        ):
            raise ValueError("Unstored capture requires a matching error outcome")
        return self


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ExternalSourceMemory:
    """One dedicated Native scope; no source authentication or automatic retries."""

    client: sdk.AsyncMemoryClient = field(repr=False)
    _: KW_ONLY
    binding: SourceBinding

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding", sdk._validated(self.binding, SourceBinding))

    async def capture(
        self,
        snapshot: ExternalSnapshot,
        *,
        consent_reference: str,
        idempotency_key: str,
    ) -> SourceCaptureOutcome:
        """Explicitly retain an already-obtained snapshot, without automatic processing.

        Persist the binding, payload, query ID, and idempotency key before calling.
        An ambiguous outcome requires reconciliation with those same inputs, not
        a newly generated key or query ID.
        JSON escaping must also fit the Native Observe character and byte limits.
        """
        key = sdk._idempotency_key(idempotency_key)
        binding = sdk._validated(self.binding, SourceBinding)
        snapshot = sdk._validated(snapshot, ExternalSnapshot)
        try:
            envelope = SnapshotEnvelope(
                format=SOURCE_FORMAT, source=binding, snapshot=snapshot
            )
            content = _canonical_json(envelope.model_dump(mode="json"))
            source_event_id = snapshot_digest(_canonical_json({
                "source_system": binding.source_system,
                "dataset_id": binding.dataset_id,
                "source_subject": binding.source_subject,
                "query_id": snapshot.query_id,
            }))
            request = sdk._validated(
                Observe.model_construct(
                    scope_id=binding.scope_id,
                    source_namespace=SOURCE_NAMESPACE,
                    source_event_id=source_event_id,
                    occurred_at=snapshot.observed_at,
                    content=content,
                    consent_reference=consent_reference,
                    auto_extract=False,
                    auto_embed=False,
                ),
                Observe,
            )
            body = _canonical_json(request.model_dump(mode="json")).encode("utf-8")
            if len(body) > MAX_REQUEST_BYTES:
                raise failure("invalid_request")
        except (ValidationError, PydanticSerializationError, ValueError, TypeError):
            raise failure("invalid_request") from None

        try:
            result = await self.client.observe(request, idempotency_key=key)
        except sdk.MemoryClientError as exc:
            return SourceCaptureOutcome(
                memory_capture_status=(
                    "outcome_unknown" if exc.error.outcome_unknown else "failed"
                ),
                error=exc.error,
            )
        return SourceCaptureOutcome(memory_capture_status="stored", memory=result)

    async def read_snapshot(self, memory_id: UUID) -> SnapshotEnvelope:
        """Validate declared provenance and digest, not actual server-side scope.

        Explain exposes no scope. Dedicated Native token scope and expiring reader
        grants enforce access; this envelope cannot prove them or current source
        authorization. Use a dedicated maintenance identity and a recorded
        successful receipt with Native forget; this adapter has no forget wrapper.
        """
        binding = sdk._validated(self.binding, SourceBinding)
        request = sdk._validated(Explain.model_construct(memory_id=memory_id), Explain)
        explanation = await self.client.explain(request)
        try:
            if not isinstance(explanation, EpisodeExplanation):
                raise ValueError(_MISMATCH)
            explanation = sdk._validated(explanation, EpisodeExplanation)
            envelope = SnapshotEnvelope.model_validate_json(explanation.source.content)
            if (
                explanation.memory_id != request.memory_id
                or explanation.revision != 1
                or envelope.source != binding
                or explanation.source.occurred_at != envelope.snapshot.observed_at
            ):
                raise ValueError(_MISMATCH)
            return envelope
        except (
            sdk.MemoryClientError,
            ValidationError,
            PydanticSerializationError,
            ValueError,
            TypeError,
        ):
            raise ValueError(_MISMATCH) from None
