from typing import Literal, assert_type
from uuid import UUID

from pg_agmemory.external_source import (
    ExternalSnapshot,
    ExternalSourceMemory,
    SnapshotEnvelope,
    SourceBinding,
    SourceCaptureOutcome,
    snapshot_digest,
)
from pg_agmemory.models import ObserveResult
from pg_agmemory.native_client import AdapterError
from pg_agmemory.sdk import AsyncMemoryClient


async def typed_source(
    client: AsyncMemoryClient,
    binding: SourceBinding,
    snapshot: ExternalSnapshot,
    memory_id: UUID,
    key: str,
) -> None:
    memory = ExternalSourceMemory(client, binding=binding)
    assert_type(memory, ExternalSourceMemory)
    assert_type(snapshot_digest(snapshot.snapshot_text), str)
    result = await memory.capture(
        snapshot, consent_reference="explicit-source-consent", idempotency_key=key
    )
    assert_type(result, SourceCaptureOutcome)
    assert_type(result.source_query_status, Literal["succeeded"])
    assert_type(result.memory_capture_status, Literal["stored", "failed", "outcome_unknown"])
    assert_type(result.memory, ObserveResult | None)
    assert_type(result.error, AdapterError | None)
    if result.memory is not None:
        assert_type(result.memory.memory_id, UUID)
        assert_type(result.memory.revision, Literal[1])
    if result.error is not None:
        assert_type(result.error.outcome_unknown, bool)
        assert_type(result.error.request_id, UUID | None)
    restored = await memory.read_snapshot(memory_id)
    assert_type(restored, SnapshotEnvelope)
    assert_type(restored.source, SourceBinding)
    assert_type(restored.snapshot, ExternalSnapshot)
    assert_type(restored.snapshot.requires_refresh, Literal[True])
    assert_type(restored.snapshot.source_authority, Literal["external_observation"])
