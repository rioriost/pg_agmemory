"""Explicitly store one synthetic caller-supplied snapshot using pg-agmemory[sdk].

No source query, connector authentication, model/provider, automatic extraction,
embedding, paid call, or tracing is used. The Native API is already provisioned.
Set PGAG_SYNTHETIC_CONSENT=yes to opt into synthetic historical retention.

Load these trusted startup settings (not model/tool-provided routing):
  PGAG_API_URL, PGAG_API_TOKEN, PGAG_SCOPE_ID,
  PGAG_SOURCE_SYSTEM, PGAG_SOURCE_DATASET_ID, PGAG_SOURCE_SUBJECT.
The subject is an opaque upstream principal ID, NEVER an upstream access token.
Use a dedicated synthetic source/dataset/principal scope and narrowly scoped token.

Before running, persist this operation's unchanged inputs in a caller-owned durable
record and supply PGAG_SOURCE_QUERY_ID, PGAG_SOURCE_OBSERVED_AT (aware ISO-8601),
PGAG_SOURCE_ACL_VERSION, PGAG_SOURCE_CONSENT_REFERENCE, and PGAG_SOURCE_CAPTURE_KEY.
The code generates no IDs, timestamps, or keys. Persist the printed successful
memory_id/revision with that record before dependent work. On failure or ambiguity,
retain the original record: reconcile Native state and safe SDK error metadata
before deciding on an explicit retry; NEVER generate a new query ID/key on ambiguity.

Operators independently set existing `scope-access --expires-at` grants no later
than the source authorization lease and revoke on explicit source notifications.
The client never renews leases. An ACL version is a caller assertion, not a grant.
Reader access depends on Native grants; declared envelope scope is not scope proof.
Automatic retention/query of shared external business data remains OFF until
trusted source authentication and notification handling are connected.

This result is historical and requires a newly authorized source refresh before
live decisions. Its exact-text SHA-256 checks consistency, not authenticity.
For deletion, pass the recorded successful memory_id to Native forget under a
dedicated maintenance identity; existing deletion closure handles derivatives.
There is deliberately no adapter forget wrapper.
"""

import asyncio
import os
from datetime import datetime
from uuid import UUID

from pg_agmemory.external_source import (
    ExternalSnapshot,
    ExternalSourceMemory,
    SourceBinding,
    snapshot_digest,
)
from pg_agmemory.sdk import AsyncMemoryClient


async def main() -> None:
    if os.environ.get("PGAG_SYNTHETIC_CONSENT") != "yes":
        raise ValueError("Set PGAG_SYNTHETIC_CONSENT=yes to store synthetic example data")

    binding = SourceBinding(
        scope_id=UUID(os.environ["PGAG_SCOPE_ID"]),
        source_system=os.environ["PGAG_SOURCE_SYSTEM"],
        dataset_id=os.environ["PGAG_SOURCE_DATASET_ID"],
        source_subject=os.environ["PGAG_SOURCE_SUBJECT"],
    )
    text = "Synthetic observation only: Cedar has 3 demonstration records.\n"
    snapshot = ExternalSnapshot(
        semantic_revision="synthetic-example-v1",
        query_id=os.environ["PGAG_SOURCE_QUERY_ID"],
        observed_at=datetime.fromisoformat(os.environ["PGAG_SOURCE_OBSERVED_AT"]),
        acl_version=os.environ["PGAG_SOURCE_ACL_VERSION"],
        snapshot_text=text,
        result_digest=snapshot_digest(text),
    )

    async with AsyncMemoryClient(
        os.environ["PGAG_API_URL"], os.environ["PGAG_API_TOKEN"]
    ) as client:
        memory = ExternalSourceMemory(client, binding=binding)
        outcome = await memory.capture(
            snapshot,
            consent_reference=os.environ["PGAG_SOURCE_CONSENT_REFERENCE"],
            idempotency_key=os.environ["PGAG_SOURCE_CAPTURE_KEY"],
        )
        print(f"source_query_status={outcome.source_query_status}")
        print(f"memory_capture_status={outcome.memory_capture_status}")
        if outcome.memory_capture_status != "stored":
            assert outcome.error is not None
            print(
                f"code={outcome.error.code}; retryable={outcome.error.retryable}; "
                f"outcome_unknown={outcome.error.outcome_unknown}; "
                f"native_status={outcome.error.native_status}; "
                f"request_id={outcome.error.request_id}"
            )
            raise SystemExit(1)

        assert outcome.memory is not None
        print(
            f"memory_id={outcome.memory.memory_id}; revision={outcome.memory.revision}"
        )
        saved = await memory.read_snapshot(outcome.memory.memory_id)
        print(f"Historical snapshot requires_refresh={saved.snapshot.requires_refresh}")


if __name__ == "__main__":
    asyncio.run(main())
