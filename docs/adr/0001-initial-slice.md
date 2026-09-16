# ADR 0001: Correctness-first initial memory slice

[日本語](0001-initial-slice-jp.md) | [Current contract](../STATUS.md)

- Date: 2026-09-16
- Status: Adopted for the initial M1 slice; not evidence of completed M0/M1 gates
- Project/repository: `pgag_memory`; Python package/service: `pg_agmemory`
- License: MIT; dependency licenses remain separate

**Historical v0.0.1 decision record.** The revision-1-only time model and
deferral of same-assertion corrections are superseded for the v0.0.2 milestone
by [ADR 0002](0002-assertion-revisions.md). The original decisions below are
retained as history, not a description of the current revision contract.

## Context

The [implementation plan](../PG_AGMEMORY_IMPLEMENTATION_PLAN.md) calls for a
much broader service. A small runnable slice is needed to exercise authenticated
storage, authorized retrieval, literal evidence, and deletion before introducing
asynchronous derivation or graph backends. This ADR narrows the current contract;
it does not silently redefine the full MVP as complete.

## Decisions

| Area | Initial decision | Consequence |
|---|---|---|
| Persistence | PostgreSQL only; ordinary tables, JSONB operational results, composite tenant/scope references, and RLS | No queue, external memory store, or model dependency |
| Schema delivery | Package `src/pg_agmemory/storage/001_initial.sql` with the application; administer migration separately from runtime | Installed runtime images contain the schema; runtime must not own tables or bypass RLS |
| Identity | Static RS256 verification key, fixed issuer/audience, signature/time checks, DB external-subject mapping | No JWKS rotation, delegation, or multi-issuer management |
| Evidence | Only same-scope episode → assertion links; require literal source quotes | Provenance is checked, not semantic truth; assertions remain `reported` with null confidence |
| Consent | Record caller-provided `consent_reference` | No consent-registry verification or automatic secret/PII redaction |
| Time | Initial valid/system intervals, revision `1` only | Time filters are supported, not historical corrections or supersession |
| Retrieval | PostgreSQL `simple` FTS; empty queries permitted | No BM25, Japanese segmentation, vector/hybrid retrieval, or graph expansion |
| Context | Deterministic templates and serialized UTF-8-byte budgeting | Explicit fallback metadata; no exact model-token count |
| Concurrency | Fresh per-request connection and tenant session advisory lock through commit and buffered response send | Tenant-wide serialization favors deletion correctness over throughput; no pool or measured performance claim |
| Replay | Transactional idempotency and tenant-keyed HMAC source-event records | Namespace + event ID, scoped by tenant/scope; deleted-memory exact replay returns `404` |
| Deletion | `preview` or synchronous `purge`, with at most 10,000 dependent assertions | No `suppress`, general dependency DAG, regeneration, or asynchronous purge worker |
| Deletion visibility | Scope-bound opaque markers and timestamps in `memory_ops.object_tombstone`, rather than a `deleted_at` update on `memory.object` | SELECT RLS hides marked objects without a `SECURITY DEFINER` or other privileged helper |

The request lock is the session lock
`pg_advisory_lock(hashtextextended(tenant_uuid::text, 0))`, with the tenant
represented in canonical UUID text. It survives commit until buffered response
delivery finishes or the connection closes. A transaction-only lock would end
too early to drain response sends. Every administrative membership change must
use the same session lock and commit both permissions and `access_epoch`
before releasing it. This does not protect against arbitrary administrative
writes that ignore the protocol or recover data already delivered to a client.

Deletion markers replace soft-delete `UPDATE` on `memory.object`. PostgreSQL's
RLS UPDATE post-image checks can reject a row that the update itself makes
invisible. Instead, purge first removes episode/assertion/evidence payloads
while the object anchors remain visible, then inserts scope-bound tombstones
in `memory_ops.object_tombstone` in the **same transaction**. Object SELECT RLS
excludes anchors with a tombstone; `memory.object` no longer has `deleted_at`.
The epoch and receipt commit with that transaction. This avoids the post-image
problem without `SECURITY DEFINER`, RLS bypass, or elevated runtime privileges.

Source-event and idempotency records store tenant-keyed HMAC digests rather
than plain low-entropy content hashes. Purge removes active episode/assertion/
evidence bodies but retains opaque object anchors, object tombstones, operational
metadata, and HMAC replay tombstones for the tenant lifetime. This limits
accidental resurrection; it is not full erasure. Backup receipts have no retention deadline and report
operator management. Restore quarantine and reapplication of the latest
deletion ledger/ACL are required; automated replay/DR is not implemented.

## Deferred alternatives

- Pools, finer-grained concurrency, streaming responses, and background jobs
  require an explicit replacement for commit-before-send and deletion draining.
- Workers, synthesis, job enqueue, compaction, pgvector, AGE/SQL/PGQ,
  checkpoints, corrections, MCP, SDKs, and postgresem adapters remain absent.
- General provenance, cross-scope evidence, entity resolution, confidence
  calibration, consent-policy automation, and full retention/erasure workflows
  need separate designs and acceptance evidence.

## Validation and revisit conditions

Local checks use Apple Container through `scripts/test-containers.sh`.
GitHub Actions uses Docker on native `linux/amd64` and `linux/arm64` runners.
Existing test scripts are validation mechanisms, not proof that every
architecture or roadmap gate has passed. No performance target is reported as
measured here.

Before widening the slice, add tests for the changed authorization, evidence,
replay, and deletion boundaries. Revisit tenant serialization only after
measurement and a replacement drain protocol are available. Require explicit
recovery/retention evidence before claiming production readiness or complete
erasure. Keep [status](../STATUS.md), [operations](../operations/README.md), and
their Japanese counterparts aligned with every contract change.
