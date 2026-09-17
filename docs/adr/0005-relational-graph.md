# ADR 0005: Canonical relational graph and bounded SQL oracle

[日本語](0005-relational-graph-jp.md) | [Current contract](../STATUS.md#entities-and-sql-graph-oracle) | [Operations](../operations/README.md#entity-and-graph-operations)

- Date: 2026-09-16
- Status: v0.0.5/schema 5 implemented; local and native Docker checks passed; M0/M1/M3 incomplete
- Extends: assertion revisions, declared checkpoint/effect dependencies, and purge
  in [ADR 0002](0002-assertion-revisions.md), [ADR 0003](0003-checkpoints.md),
  and [ADR 0004](0004-tool-effects.md)
- Naming: local directory/package/service `pg_agmemory`; public repository `rioriost/pgag_memory`

Historical scope: schema-5 requirements and evidence below remain v5-specific.
[ADR 0006](0006-durable-jobs.md) adds durable jobs and schema 6; use current
operations for API/worker upgrades, not this ADR's historical schema requirement.

## Decision and scope

Use canonical PostgreSQL tables and fixed parameterized SQL joins as the graph
correctness reference. There is no AGE, SQL/PGQ, Cypher, dynamic request SQL/label,
projection, or graph use automatically added to recall. This is a bounded oracle
for future backend conformance, not measured graph utility, full M1/M3 acceptance,
an MVP, or production/performance/quality/DR qualification.

## Explicit identity, not entity resolution

`POST /v1/entities` creates an immutable revision-1 memory anchor: scope UUID,
allowlisted entity type (`person`, `organization`, `project`, `component`,
`incident`, `task`, `decision`, `other`), canonical label of 1–256 characters,
1–32 distinct readable same-scope episode IDs with literal quotes of 1–4,096
characters, and `explicit_intent: true`. Literal matching establishes provenance,
not semantic support. Identity metadata is caller-reported, not a verified fact;
names and types must never be treated as trusted instructions.

There are no aliases, entity merging, name-based resolution, semantic
deduplication, or label-correction endpoint. Same HTTP idempotency key/body reuses
the anchor; a different key can create a separate same-label identity.
`GET /v1/entities/{memory_id}` returns metadata and evidence. Entities are
excluded from recall/explain. Checkpoint/effect `memory_refs` now permit entity
revision 1 in addition to episodes and exact assertion revisions.

## One assertion identity and one temporal history

`POST /v1/relations` requires scope, source/target entity UUIDs, allowlisted
predicate, same-scope literal episode evidence, and explicit intent. **Both
endpoints and evidence must share the scope.** Predicates are `depends_on`,
`part_of`, `affects`, `works_for`, and `decides`; all are multi-valued reported
declarations. There is no truth arbitration or inferred inverse fact.

A relation is the canonical assertion `memory_id`, not a second memory object.
`memory.relation` fixes its source; `memory.relation_revision` binds each exact
assertion revision to its target. Subject is the immutable source canonical
label; each revision's immutable value is its target canonical label. Evidence,
reported truth status, valid intervals, and server-controlled system history
come from the existing assertion revision, not a parallel graph model.
Creation uses the existing `RememberResult` ID/revision/reported-status response.
Free-text `remember` with matching labels/predicates remains untyped.

`POST /v1/relations/{memory_id}/revisions` takes strict `expected_revision` 1–1000,
target UUID, complete episode evidence, explicit intent, optional aware
`valid_from`/`valid_to`, and a 1–256-character reason. Source/predicate stay fixed.
It replaces the entire valid interval: null/omitted bounds are unbounded, not
retained. Old target references, evidence, and time remain revision-specific.
CAS, historical idempotent replay, and the 1000-total-revision bound are retained.
Generic assertion corrections return `409 relation_revision_required`.

Relations remain ordinary FTS assertion candidates with `graph_used: false`.
Recall items and assertion explain add nullable `relation` endpoints for the
exact revision. Context text includes both UUIDs under the existing UTF-8 byte
budget. Explain's omitted revision remains 1, not latest.

## Bounded, authorized graph reads

Authenticated `POST /v1/graph/expand` is read-only and needs no `Idempotency-Key`.
Require 1–32 distinct scopes, 1–16 distinct entity seeds, 1–5 distinct allowed
relation types, and purpose. Direction is outgoing (default), incoming, or both.
Strict limits are 1–2 hops (default 2) and 1–100 paths (default 100).
Optional aware `as_of`/`known_at` defaults are captured once per expansion.

Scope/seed filters only narrow access. Current RLS, same-scope foreign keys,
time filtering, and the existing tenant transaction/response-drain lock govern
seeds, intermediate nodes, edges, and evidence. Hidden, nonexistent, or
temporally unavailable seeds are silently excluded and not echoed.

Fixed neighbor SQL produces deterministic breadth-first simple paths, ordered
by seed UUID, then assertion ID/revision/next UUID at each hop. No entity repeats
within a path. All prefixes count toward the global path cap; a limit-plus-one
probe determines truncation. Semantic cycles cannot produce repeating-node
paths. Incoming/both is traversal orientation, not a new inverse assertion.

Responses declare `backend: "sql"`, `projection_watermark: null`, effective time,
access/deletion epochs, canonical node summaries without quotes, edges with exact
assertion references/endpoints/predicate/valid interval/recorded time/reported
status, and paths of node UUIDs plus assertion references. Coverage declares
`max_hops`, `truncated`, and `complete_within_bounds`. No projection/lag/watermark
guarantee is needed for direct canonical joins.

No paths gives `empty_reason: "not_found"`; otherwise it is null. Visible isolated
seeds may appear without paths. Neither no-path nor bounded completeness proves
fact absence. Entity GET and relation explain provide evidence quotes.
A missing node at final recheck fails with `409 graph_invalidated`; database
failure is `503`, never empty-success fallback. Capabilities expose
`graph_backend: "sql"`, entity/relation types, and graph limits.

## Dependency closure and storage guards

Entities depend only on episodes, so semantic relation cycles add no provenance
cycles. Deletion follows episode evidence → entity → relations using it as
source or **any historical target** → entire assertion history → declared
checkpoint/effect references and descendant/fork checkpoints. Direct entity
purge and direct entity checkpoint/effect references participate. Other surviving
entities are not deleted merely because a relation is removed.

Purging any effect still removes every checkpoint payload in its run and
permanently seals the run; independent surviving effects remain reconcilable.
Payloads, entity evidence, typed links, quotes, and affected receipts are purged
before opaque tombstones atomically. Historical references/replay cannot revive
labels, values, or receipts. Opaque anchors/idempotency persist; backup/WAL/
delivered-data and restore-quarantine limitations are unchanged. Callers must
declare every copied entity or assertion revision dependency in `memory_refs`.

Additive `005_relational_graph.sql` leaves 001–004 unchanged. It adds `entity`,
`entity_evidence`, `relation`, and `relation_revision`, RLS and same-scope foreign
keys, and entity checkpoint/effect reference kinds. Runtime gets no payload
UPDATE grant. Deferred checks require complete entity evidence and the exact
typed target/value for every relation revision; markers/links cannot be stripped
or generic values changed to bypass the typed contract.
`assertion.is_relation DEFAULT false` protects legacy free-text data without
changing `Remember` JSON field/hash order, checkpoint checksums, or old histories.

Stop all API traffic, back up, run `pg-agmemory migrate`, verify exact schema
history `[1, 2, 3, 4, 5]`, and restart only v5. No rolling coexistence or downgrade
is supported. Old v0.0.1 lacks a startup schema guard and must remain stopped.

## Evidence boundary

Apple Container and native Docker amd64/arm64 each passed 91 tests (2 existing
warnings), Ruff, strict mypy (9 source files), and production HTTP health smoke.
Coverage includes two-tenant graph golden/temporal/hidden/budget/deletion cases,
v4 effect/history and v3 checkpoint checksum/idempotency compatibility, and exact
1000-revision boundaries for both free-text and typed relation assertions.
See [STATUS](../STATUS.md#validation-evidence) for final commit/CI links and the
local strengthened-case follow-up. These checks do not complete M0/M1/M3 or
qualify performance, memory quality, MVP/production, full erasure, or backup/DR.
