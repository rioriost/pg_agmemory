# ADR 0003: Typed checkpoints and conservative lineage

[日本語](0003-checkpoints-jp.md) | [Current contract](../STATUS.md#checkpoint-contract) | [Operations](../operations/README.md#checkpoint-operations)

- Date: 2026-09-16
- Status: v0.0.3/schema 3 implemented; local/native Docker checks passed; M1 incomplete
- Extends: [ADR 0001](0001-initial-slice.md) and [ADR 0002](0002-assertion-revisions.md)
- Naming: local directory/package/service `pg_agmemory`; public repository
  remains `rioriost/pgag_memory`

## Decision and scope

Store typed, immutable checkpoint payloads in PostgreSQL, with scope-local
run/branch identities, exact declared memory references, HMAC integrity checks,
and branch-head compare-and-swap. This is a state-envelope API, not a harness
adapter, execution engine, durable external-effect ledger, or disaster-recovery
system. Assertion/episode recall and explain remain separate.

The state schema is version 1 only: `goal`, `constraints`, `completed_actions`,
`decisions`, `unresolved_questions`, `next_actions`, and typed `pending_effects`.
Unknown/arbitrary objects and executable serialization are not accepted.
Pending effects carry an `operation_id`, short description, and
`planned / dispatched / unknown` status; these are caller-supplied snapshot
hints, not service-confirmed action outcomes.

## Creation, reads, and integrity

| Operation | Decision |
|---|---|
| `POST /v1/checkpoints` | Require scope/run/branch, harness ID/version, typed state, nonnegative event watermark, and explicit `expected_head` (null for an empty branch); return a server UUID, sequence, parent, and checksum |
| `GET /v1/checkpoints/{checkpoint_id}` | Recheck current authorization, checksum, state schema, and visible references; return the envelope, not a recall item |
| `POST /v1/checkpoints/restore` | Require an exact harness/version/schema match and a never-used target branch; create a new checkpoint in the source scope/run |

At most 100 unique `(memory_id, revision)` references are allowed. Each points
to a readable same-scope episode (revision 1) or existing assertion revision.
No checkpoint is accepted as a `memory_refs` source; checkpoint dependencies
instead come from the automatically recorded parent lineage.

The service and DB lock the branch head. Sequence starts at 1 and increments
only on successful adoption; watermarks cannot decrease. The parent must be
older and in the same scope/run. A restore may use a parent from another branch.
DB constraints enforce reference counts, valid heads, parent ordering, and
irreversible branch invalidation. Runtime uses no privileged helper.

The tenant session lock and commit-before-buffered-send boundary remain in
force. Checkpoint creation accepts a 1 MiB body; other endpoints keep 256 KiB.
The tenant-keyed `hmac-sha256-v1` checksum covers the saved state envelope,
identity, references, and capture epochs. It establishes integrity within the
service trust boundary, not semantic truth or proof that an action occurred.
Saved/current access and deletion epochs are explanatory metadata, never a
substitute for current authorization.

Same-key, same-payload retries preserve the committed checkpoint reference.
Idempotency records contain no state. Restore retries load the existing fork
under current checks rather than creating another branch; current epoch
metadata may differ. A purged checkpoint cannot be recovered through replay.

## Restore is not execution

Restore copies state/references to a new branch at sequence 1, retaining the
source checkpoint as parent. It never changes or rewinds the source branch.
Saved assertion references keep their exact historical revisions; restore does
not advance them to the latest revision or automatically refresh external facts.
Harness ID, version, and state-schema mismatches are rejected; an existing
target branch, even an invalidated one, cannot be reused.

Dispatched pending effects become unknown on restore. Both GET and restore
list dispatched/unknown operation IDs in `requires_reconciliation`, with
`resume_allowed: false` while any remain. `automatic_reexecution` is always
false. Even `resume_allowed: true` is only a snapshot hint, not authorization
to repeat a transfer, send, deletion, or other effect. Reconciliation and any
actual execution belong to the caller; no receipt lookup or effect ledger is
implemented here.

## Declared dependencies and deletion

**Callers must declare all memory-state dependencies.** There is no semantic
scanner for undeclared copies, consent registry, or automatic PII/secret
redaction. The typed shape does not make arbitrary text safe.

Purge follows episode-to-assertion evidence across every revision, direct
episode/assertion-to-checkpoint references, and all parent-to-child checkpoint
links, including forks. Descendants conservatively inherit the complete parent
lineage even if their own `memory_refs` omit an ancestor's source. A source
used by any old assertion revision therefore removes that assertion's entire
history and every affected checkpoint payload.

The bound is 10,000 dependents plus requested roots across the whole closure.
Excesses fail atomically. Purge invalidates affected branch heads permanently,
deletes payloads/references before tombstones in the same transaction, and
uses the existing tenant lock to drain earlier responses. Branch IDs cannot
be reopened. Opaque run/branch/object anchors and HMAC replay metadata remain
for the tenant lifetime; this is not complete erasure. Backup retention and
quarantined restoration requirements are unchanged.

## Schema and qualification

Additive `003_checkpoints.sql` introduces `memory.checkpoint_run`,
`memory.checkpoint_branch`, `memory.checkpoint`, and `memory.checkpoint_reference`
with RLS, composite keys, and adoption/invalidation checks. Migrations
`001_initial.sql` and `002_assertion_revisions.sql` remain unchanged.
v0.0.3 requires schema ledger `[1, 2, 3]` exactly.

Stop old/new API traffic, back up, apply pending migrations atomically, and
start only the matching new API. There is no rolling old-API compatibility or
downgrade; v0.0.1 has no schema guard. Follow the
[maintenance procedure](../operations/README.md#v003-maintenance-migration).
Checkpoint restore is not PostgreSQL backup recovery.

For v0.0.3 commit [8adb40a](https://github.com/rioriost/pgag_memory/commit/8adb40a),
Apple Container and native Docker linux/amd64 and linux/arm64 each passed
54 tests, Ruff, strict mypy (7 source files), and production HTTP health smoke.
See [CI run 35088907082](https://github.com/rioriost/pgag_memory/actions/runs/35088907082)
and the reported [validation evidence](../STATUS.md#validation-evidence).
Separate working snapshots/
compaction, durable effect ledgers, harness adapters, workers, vector/graph
search, and full M1/DR qualification remain outside this slice.
