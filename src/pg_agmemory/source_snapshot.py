"""SDK-independent historical snapshot contracts and exact UTF-8 hashing."""

import hashlib
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from pg_agmemory.models import Contract, ShortText

SOURCE_FORMAT: Final = "pgag-external-snapshot-v1"
SOURCE_NAMESPACE: Final = "external-snapshot-v1"


def snapshot_digest(text: str) -> str:
    """Hash exact UTF-8 text without normalizing whitespace or Unicode."""
    if not isinstance(text, str):
        raise ValueError("Invalid snapshot text")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid snapshot text") from None
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
