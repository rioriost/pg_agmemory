# ADR 0004: Tool-effect ledger and run-wide recovery boundary

[日本語](0004-tool-effects-jp.md) | [Current contract](../STATUS.md#tool-effect-ledger) | [Operations](../operations/README.md#tool-effect-operations)

- Date: 2026-09-16
- Status: v0.0.4/schema 4 implemented; local and native Docker checks passed; M1 incomplete
- Supersedes: the ledger deferral and snapshot-only resume rules in [ADR 0003](0003-checkpoints.md)
- Naming: local directory/package/service `pg_agmemory`; public repository `rioriost/pgag_memory`

## Decision and scope

Store a bounded, durable external tool-effect intent/outcome ledger in PostgreSQL.
It records what the caller intends and reports, not service-observed provider
execution. There is no tool execution, approval service, provider receipt query,
automatic retry, or external exactly-once guarantee.

A checkpoint must first establish the scope-local run. Each operation identity
is tenant/scope/run/operation, independent of HTTP `Idempotency-Key`.
New run/operation IDs are not semantic deduplication of real-world actions.
The host retains responsibility for permission, approval, dispatch coordination,
canonical action construction, and external reconciliation.

## Identity, privacy, and lifecycle

`POST /v1/tool-effects` accepts the existing scope/run, caller operation UUID,
tool name, required lowercase 64-hex `action_hash`, and up to 100 exact same-scope
episode/assertion references. Declare every memory dependency; no semantic
scanner discovers omitted sources. Raw action hashes and arguments are not
persisted. Store a tenant-HMAC `action_fingerprint` and stable 64-hex
`external_idempotency_key`, available through GET.

Identical normalized planning bodies with different HTTP keys reuse the
original revision-1 reference for the same operation identity, even after its
head changes. Changed intent conflicts; purged identities cannot be recreated.
HTTP replay records hold only the result reference/revision/status and keyed
request digest, not payloads, event reasons, or receipt copies. Plan/transition
responses are historical revision references, not current-state snapshots or
execution authorization. Under current authorization, a surviving effect can
replay its old reference after run sealing; fresh dispatch remains rejected.
Purged effects still return `404` for an exact replay.

`POST /v1/tool-effects/{memory_id}/transitions` appends a CAS-checked event.
The DB assigns timestamps/actors and enforces this FSM:

```text
planned    -> dispatched | unknown
dispatched -> unknown | confirmed | failed
unknown    -> confirmed | failed
confirmed / failed (terminal; no outgoing transitions)
```

`planned → unknown` captures uncertainty about legacy/off-protocol attempts,
not execution permission; `unknown → dispatched` is forbidden. Terminal events
require a 1–256-character receipt reference and `provider_receipt` or
`operator_review` source. Other states forbid receipts. Receipt references
are **caller-reported, not verified by the service**; a failed outcome does not
authorize an automatic retry.

Each run has a lifetime limit of 100 effects, including terminal records;
each effect has at most four immutable events and 100 declared references.
GET returns the latest state/history, references, HMAC identifiers, and current
`run_invalidated` flag. RLS, tenant/scope foreign keys, deferred history/reference
checks, head locking/CAS, and the tenant commit/send lock remain in force.
No privileged runtime helper is introduced.

The harness must durably record dispatch before the external call and use the
stable external key where supported. A replayed old transition acknowledgment
is not fresh permission to send or blindly resend. Database CAS cannot prevent
or undo an external action performed outside this protocol.

## Checkpoint resume and restore

Checkpoint GET/restore consult every live effect in the run, across branches
and including effects added after the snapshot. `tool_effects` is a current
summary, not part of the saved checkpoint checksum.
Tracked confirmed/failed records resolve old hints for that operation;
dispatched/unknown records block resumption even without a snapshot hint.

**All untracked snapshot hints, including planned hints, block resumption**
and appear in `untracked_effects` and `requires_reconciliation`.
An unknown/dispatched hint paired with a tracked planned record also blocks:
registering a plan alone must not erase evidence of uncertainty.
This deliberately tightens legacy behavior without rewriting saved state.
Any blocker makes `resume_allowed: false`; `automatic_reexecution` is always
false. An allowed resume is not permission or proof of external completion.

Restore atomically appends `unknown` for all dispatched run effects with
`origin: "checkpoint_restore"` before creating the new branch. These revisions
fence stale ledger writes; already-running external calls are not cancelled.
The source branch remains unchanged. Exact restore replay returns the existing
fork without duplicate journal events, while recomputing live summaries.
Saved assertion references retain exact historical revisions, not automatically
refreshed external facts.

## Purge and sealed runs

The dependency closure extends from declared episode/assertion sources to effects,
then from each effect to **every checkpoint in its scope/run**, including older
empty snapshots. Existing assertion-history and parent/fork lineage traversal
remains conservative. The total bound stays 10,000 dependents plus requested roots.

Purging any effect deletes all affected checkpoint payloads and permanently
sets `effects_invalidated`. New effect plans, dispatch, checkpoints, and resumption
are blocked. Independent surviving effects are not purged merely for sharing
the run: GET and allowed reconciliation transitions remain available, including
`unknown → confirmed/failed`, but dispatch cannot resume.

Purge SQL-deletes effect payloads, references, and event reasons/receipt references
before tombstones in the same transaction, under the tenant read/drain barrier.
Opaque operation identities, run flags, object anchors/tombstones, and HMAC replay
metadata persist for the tenant lifetime. This is not erasure of WAL, backups,
physical media, or already delivered data; operator-managed backup limits remain.

## Schema and qualification

Additive `004_tool_effects.sql` introduces `memory.tool_effect`,
`memory.tool_effect_revision`, `memory.tool_effect_reference`,
`memory_ops.tool_effect_identity`, and the checkpoint-run invalidation flag.
Migrations 001–003 and old saved checkpoint hashes remain unchanged.
v0.0.4 requires ledger `[1, 2, 3, 4]` exactly. Stop all API traffic, back up,
apply pending migrations atomically, and start only the matching API; no rolling
old-version compatibility or automatic downgrade is supported.
Follow [maintenance](../operations/README.md#v004-maintenance-migration).

For v0.0.4 implementation
[4a7d3f8](https://github.com/rioriost/pgag_memory/commit/4a7d3f8),
Apple Container and native Docker amd64/arm64 each passed 73 tests
(2 existing warnings), Ruff, strict mypy (8 source files), and production HTTP
health smoke. See
[CI run 35098507356](https://github.com/rioriost/pgag_memory/actions/runs/35098507356)
and the [validation evidence](../STATUS.md#validation-evidence).
SQL/graph oracle, workers, MCP, harness integration, separate working-snapshot
compaction, and automated backup/DR remain future work; this is not full M1.
