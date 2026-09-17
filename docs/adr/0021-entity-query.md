# ADR 0021: Exact entity query and pagination

[日本語](0021-entity-query-jp.md) | [Contract](../STATUS.md#exact-entity-query-and-pagination) | [Operations](../operations/README.md#exact-entity-query-and-pagination)

- Date: 2026-09-18
- Status: accepted and verified in bounded v0.0.21/schema 10, locally and on both native architectures
- Extends: [entities and graph](0005-relational-graph.md) and [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/performance/identity-resolution/memory-quality/production/DR qualification

## Decision and request boundary

Add Native JWT authenticated read-only `POST /v1/entities/query`, requiring
current read access but no write permission or `Idempotency-Key`.
Closed `QueryEntities` accepts required distinct UUID `scope_ids` (1–32),
nullable `entity_type` (`EntityType`, default null), nullable `canonical_label`
(`ShortText`, 1–256 characters after normal whitespace stripping, default null),
strict integer `max_items` (1–100, default 20), and `before: EntityCursor | None`
(default null). Closed `EntityCursor` requires aware `recorded_at` and UUID `memory_id`.
Invalid shapes/types, duplicates, empty/whitespace-only labels, bounds,
UUIDs/timestamps, or extra fields give **422 `invalid_request`**.
No owner/principal override, offset, watch, historical ACL, or arbitrary query is added.

## Exact filters and explicit identity choice

`EntityType` retains exactly `person`, `organization`, `project`, `component`,
`incident`, `task`, `decision`, `other`. Optional type and label filters combine
with **AND before LIMIT**. Label equality uses exact, case-sensitive `C`
collation after existing whitespace stripping; omitted/null filters leave all
currently readable entities eligible in the requested scopes.
No alias, fuzzy/substring/wildcard/Unicode-normalization matching, embeddings,
merge, or automatic identity choice is introduced.
Same-label matches retain every distinct ID across pages, not one resolved identity.
The caller uses unchanged `GET /v1/entities/{memory_id}` / SDK `get_entity` to
inspect evidence, then explicitly chooses graph seeds. Query does not expand a graph.

## Current visibility and whole-page failure

Current tenant/scope/source RLS applies, with **no ownership filter**.
Readable shared-scope entities are included, unlike caller-owned-only job query.
Unknown/private scopes contribute nothing; no matches returns **200**,
`entities: []`, and `next_cursor: null` with current epochs, not hidden reasons/counts.
Each page uses the current tenant response-delivery/drain barrier.
For every selected returned item, the visible evidence count from
`entity_evidence JOIN episode` must equal stored `reference_count`;
otherwise the **whole page** fails with **409 `entity_invalidated`**.
No silent skip, partial success, or fallback. SQL fetches metadata and counts,
not quotes; the lookahead row is only for overflow.

## Exclusive position and typed metadata

Order by `object.created_at DESC, entity.id DESC`, exposed as
`recorded_at DESC, memory_id DESC`, with exclusive
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`.
Fetch `max_items + 1`, return at most `max_items`, and emit `next_cursor` only
on overflow from the **last returned item**, not lookahead.
Omitted/null `before` starts newest; null `next_cursor` ends continuation.
The unsigned transparent position is not authority, snapshot, receipt, cache,
or retention object. It need not refer to an existing object.
Old/deleted/forged cursors only narrow current authorized rows.
Each page is point-in-time; access/source/deletion can change membership.
New entities above an old boundary require an explicit restart without `before`.

Exactly **200 `EntityPage`** returns `entities: list[EntitySummary]` (at most 100),
`next_cursor: EntityCursor | None`, and `consistency` (`access_epoch`, `deletion_epoch`).
Existing `EntitySummary` contains `memory_id`, `revision: 1`, `scope_id`,
`entity_type`, `canonical_label`, and `recorded_at` (object creation, not source event time).
No evidence quotes, source IDs, or total count; known-ID `EntityDetail` is unchanged.
Labels remain human text, not content-free data, trusted instructions, or verified truth.

## SDK and deployment

Async `query_entities(QueryEntities) -> EntityPage` uses internal `mutation=False`,
call-time request validation, normal **256 KiB request / 2 MiB response** bounds,
sanitized read-only `outcome_unknown: false`, and no automatic pagination/retry.
SDK parsing also enforces the response-model 100-entity bound.
Existing `entity_invalidated` is reused; MCP/hook safe-error behavior is unchanged.
Native/SDK has 29 resource methods, four unchanged MCP tools, and an unchanged
closed hook without an entity tool/field. No SDK admin/worker function, mutation,
provider call, cache, or automatic identity selection is added.
Require exact service 0.0.21 / API v1 / schema 10 across adapters.
Stage `m2-entity-query` adds `entity_query`:
`endpoint: "/v1/entities/query"`, `match: "exact"`,
`order: ["recorded_at_desc", "memory_id_desc"]`, `pagination: "exclusive_keyset"`,
`max_items: 100`.
v20→v21 is application-only: schema 10/history 1–10 and pinned artifacts remain,
without SQL migration, dependency/provider, or AGE changes.
Stop/drain old components and use matching versions; no mixed-version promise.

## Validation boundary

**Final local and native implementation validation passed.** The full Apple
Container `./scripts/test-containers.sh` completed with **729 passed,
1 existing warning, 390.27 s**: **695 retained + 34 new cases**
(20 contract, 7 graph-query, 7 SDK—6 mock + 1 real).
Implementation
[`a34477d7511f22202f0bd981772f51408634a9af`](https://github.com/rioriost/pg_agmemory/commit/a34477d7511f22202f0bd981772f51408634a9af)
passed [CI 35252290223](https://github.com/rioriost/pg_agmemory/actions/runs/35252290223)
on that exact SHA: native amd64 **729 passed, 764.95 s**; arm64
**729 passed, 614.98 s**.
Ruff, mypy **19 source files + 1 strict SDK consumer**, core/hook/sdk-only
installations, and all previous production smokes plus entity query passed
in all three environments.
Coverage includes the exact **101-entity fixture with timestamp ties and UUID
ordering**, all eight types, Japanese/punctuation/case/Unicode distinctions,
other-owner shared-scope read visibility, ACL changes/current epochs,
deleted/forged cursors, **32-scope and 100-item page caps**, no quotes, and
explicit graph-seed selection.
Elapsed time is not a benchmark; no MVP, performance, identity-resolution,
memory-quality, production, or DR qualification is claimed.
These are implementation results, not a final-docs CI result.
See [verified evidence](../STATUS.md#v0021--schema-10).
V20 implementation and separate final-docs CI remain
[historical evidence](../STATUS.md#v0020--schema-10), not v21 qualification.
