# ADR 0002: Same-assertion revisions

[日本語](0002-assertion-revisions-jp.md) | [Current contract](../STATUS.md) | [Operations](../operations/README.md)

- Date: 2026-09-16
- Status: bounded v0.0.2 milestone implemented; local/native Docker checks passed; M1 remains incomplete
- Supersedes: the revision-1-only model and deferred same-assertion corrections
  in [ADR 0001](0001-initial-slice.md), not the entire initial architecture

**Historical v0.0.2 record.** [ADR 0003](0003-checkpoints.md) extends the
checkpoint/deletion boundary and advances the required schema to 3 for v0.0.3.
The decisions and validation results below remain scoped to v0.0.2.

## Scope and decision

Retain one stable assertion identity: tenant, scope, subject, and predicate
are immutable. Put value, valid/system intervals, reported status, explicit
intent, correction reason, and evidence in revision-specific records.
`remember` creates revision 1; corrections append revisions up to a total of
1000. Episodes remain revision 1 only.

This is **full valid-interval replacement**, not partial-time splitting,
cross-assertion supersession, or arbitration of a fact key. For example:

| State | Query | Result for this assertion |
|---|---|---|
| Revision 1 reports Gold with unbounded valid time | September 16, before correction in system time | Gold, revision 1 |
| Revision 2 replaces it with Platinum valid from October 1 | September 16, after correction in system time | No matching assertion revision |
| Same revision 2 | October 2, after correction in system time | Platinum, revision 2 |

This is not future scheduling that preserves Gold until October 1. The old
valid coverage is replaced under current knowledge; an earlier `known_at`
still exposes the old belief, subject to today's ACLs and deletion state.
Omitting valid bounds resets them to unbounded rather than inheriting old ones.

## API and concurrency

`POST /v1/assertions/{memory_id}/revisions` requires `Idempotency-Key` and the
full body defined in [status](../STATUS.md#assertion-revision-contract):
`expected_revision` (integer 1–1000), `value`, 1–32 distinct episode `evidence`
with literal quotes in the same scope, `explicit_intent: true`, optional
`valid_from`/`valid_to`, and a 1–256-character `reason`.
Subject/predicate/scope are not writable through this endpoint.

The existing tenant session lock, short transaction, and buffered
commit-before-send boundary remain. The service checks read/write access and
the expected head. `201` returns `memory_id`, revision `expected_revision + 1`,
and `epistemic_status: "reported"`. Mismatch yields `409 revision_conflict`;
attempting revision 1001 from head 1000 yields `422 revision_limit_exceeded`.
Hidden, deleted, or non-assertion targets yield `404`.

The normalized request hash includes the target `memory_id`. A committed
identical-key retry is checked before comparing the new head and returns its
original revision reference even after later corrections. Changing target or
payload with that key is a conflict. Current access/tombstone checks precede
reuse: idempotency is not a historical authorization bypass.

## Database invariants

`memory.assertion` holds identity and `current_revision`;
`memory.assertion_revision` holds versioned content.
`memory.provenance_edge.child_revision` ties each evidence quote to its exact
revision. Composite tenant/scope foreign keys and RLS remain mandatory.
Literal quote validation verifies a source span, not semantic truth;
status remains `reported` and confidence remains null.

The BEFORE INSERT trigger `memory.adopt_revision` locks the assertion head,
uses `clock_timestamp()`, closes the preceding system range, and advances the
head atomically. It assigns the new system range itself; callers cannot
backdate or choose system time. Closed revisions retain their original starts.
System intervals are contiguous `[)` ranges, with only the head open-ended.
A GiST exclusion constraint using `btree_gist` rejects overlap; deferred
history/evidence constraints reject gaps, missing revisions, and revisions
without evidence. Trigger functions do not use `SECURITY DEFINER`.

`recall` evaluates both `as_of` and `known_at` and searches identity text plus
the selected revision's value. The returned revision and sources come from
that same version; old text is not combined with current evidence.
`explain` accepts explicit revisions 1–1000, but omission remains **revision 1,
not latest**, for compatibility. It includes `recorded_at`, `known_until`, and
`correction_reason` with revision-specific evidence. Episodes accept only 1;
nonexistent versions return `404`.

## Deletion and migration

Any source used by any revision conservatively invalidates the **entire
assertion history** on purge. Delete all revision values, reasons, and evidence,
not just the current version or the edge to the deleted source. The
episode → assertion closure stays bounded at 10,000 dependent assertions.
Payload deletion precedes insertion of `memory_ops.object_tombstone` in the
same transaction. Historical explain/recall/replay never bypass current
tombstones or permissions. Retained opaque/HMAC records and operator-managed
backups still prevent claiming complete erasure.

Keep `001_initial.sql` unchanged. Packaged `002_assertion_revisions.sql`
backfills existing values, valid/system intervals, status, and evidence into
revision 1 without resetting actual times. The adoption trigger is installed
after this backfill. Source-event and idempotency records are retained;
legacy request serialization order must remain compatible for exact replay.

The migration loop validates sequential history, applies pending scripts and
ledger entries atomically, is rerunnable, and uses a 5-second lock timeout.
Migration 002 requires `btree_gist` and a superuser or qualified `BYPASSRLS`
administrator with the required DDL rights. `row_security = off` makes RLS
filtering fail closed rather than silently backfilling only visible tenants;
it does not grant bypass privileges. Runtime remains unprivileged.

Stop all old/new API traffic and processes, take a backup, migrate, then start
only the matching new API. Its startup check requires ledger `[1, 2]` exactly.
**The old API has no equivalent guard and must remain stopped.** There is no
rolling old-API compatibility or downgrade path. Follow the
[current maintenance protocol](../operations/README.md#v003-maintenance-migration);
backup restoration still requires quarantine and current deletion/ACL replay.

## Validation and remaining scope

For v0.0.2 commit 5458402, Apple Container and native Docker linux/amd64 and
linux/arm64 each passed 32 tests, Ruff, mypy, and production HTTP health smoke.
See [status](../STATUS.md#validation-evidence) for the checked CI logs, migration
and bulk-seeded boundary fixtures, and public HTTP boundary checks.
This ADR does not claim completed M1, measured performance/quality, or DR.
Checkpoints, workers, synthesis, vectors, graphs, MCP, SDKs, and cross-assertion
supersession remain outside this milestone.
