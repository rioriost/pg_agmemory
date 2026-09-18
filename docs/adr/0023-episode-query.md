# ADR 0023: Episode query and pagination

[日本語](0023-episode-query-jp.md) | [Contract](../STATUS.md#episode-query-and-pagination) | [Operations](../operations/README.md#episode-query-and-pagination)

- Date: 2026-09-18
- Status: implemented and qualified for bounded v0.0.23/schema 10
- Extends: [Initial slice](0001-initial-slice.md) and [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/performance/memory-quality/production/DR qualification

**Historical version notice:** this ADR preserves the v0.0.23/schema 10 decision
and original implementation qualification. Separate final-docs CI 35282317544
completed successfully on attempt 2 after retrying only an amd64 Docker Hub
authentication connection reset before tests, without product code changes.
See [historical evidence](../STATUS.md#v0023--schema-10).
[ADR 0024](0024-selectable-inference.md) drafts the v0.0.24 provider foundation;
qualification is pending and M2 remains incomplete. The body below is unchanged.

## Decision and request boundary

Add authenticated read-only `POST /v1/episodes/query`, requiring current read
access but no write permission or `Idempotency-Key`.
Closed `QueryEpisodes` accepts only distinct UUID `scope_ids` (1–32),
`occurred_from` and `occurred_to` (timezone-aware timestamps or null, default null),
strict integer `max_items` (1–100, default 20), and `before: EpisodeCursor | None`
(default null). Closed `EpisodeCursor` requires aware `recorded_at` and UUID `memory_id`.
Unknown fields, duplicate scopes, invalid UUIDs/naive timestamps, or bounds give
**422 `invalid_request`**; strict `max_items` rejects booleans and floats.

Occurred-time bounds are **half-open `[from, to)`**:
`occurred_from <= occurred_at < occurred_to`. Either omitted/null bound is unbounded;
equal or reversed bounds are invalid. Scope and time filters combine before LIMIT.
These are event-time filters, not `as_of`, `known_at`, historical ACLs, or
bitemporal reconstruction. Source identity is retained as HMAC anchors, so no
plaintext `source_namespace` filter or invented source field is added.

## Current metadata, not content or owner-only discovery

SQL selects only metadata from `episode` joined to `object`, under current
tenant/scope RLS. Shared readable scopes are included: **no ownership filter**.
Unknown/private/cross-tenant scopes contribute no rows; no matches returns
**200**, `episodes: []`, and `next_cursor: null`, without hidden reasons or counts.
Current permissions, purge visibility, and the tenant response-delivery/drain
barrier apply on every page. The read creates no audit record, receipt, job, or
other application write.

Exactly **200 `EpisodePage`** contains `episodes: list[EpisodeSummary]` (at most 100),
`next_cursor: EpisodeCursor | None`, and `consistency` (`access_epoch`, `deletion_epoch`).
Each summary has only `memory_id`, `revision: 1`, `scope_id`, `occurred_at`, and
`recorded_at`. No body/content, consent reference, source URI, `source_namespace`,
event ID, or job payload is returned.
The caller explicitly selects an episode, uses `Explain(memory_id, revision=1)`
for currently authorized content, then may explicitly `Remember` with literal
evidence. This is not automatic ingestion, synthesis, extraction, or compaction.
Content remains evidence, not trusted instructions or verified/current facts.

## Recorded-time keyset, not event order or a watermark

Order by `object.created_at DESC, episode.id DESC`, exposed as
`recorded_at DESC, memory_id DESC`, with exclusive
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`.
Fetch `max_items + 1`, return at most `max_items`, and emit a cursor only on
overflow, from the **last returned item**, never lookahead.
Omitted/null `before` starts newest; null `next_cursor` ends continuation.
Ordering is **not occurred time**: a late historical event admitted later can
appear newest among matching rows.

The unsigned cursor is position, not authority, snapshot, receipt, event sequence,
compaction watermark, or retention object. It need not refer to an existing episode.
Deleted/forged positions only narrow currently authorized rows.
Each page is point-in-time; ACL/purge changes can alter membership.
Newer recorded rows above the boundary require an explicit restart without `before`.

## SDK and deployment

Async `query_episodes(QueryEpisodes) -> EpisodePage` uses `mutation=False`,
call-time request revalidation, and normal **256 KiB request / 2 MiB response**
bounds. SDK response parsing enforces the 100-item bound.
Read loss has sanitized `outcome_unknown: false`, not an uncertain mutation
outcome; an explicit repeat can see different current data.
No automatic pagination/retry, mutation, provider call, or SDK admin/worker method.
Native/SDK has 31 resource methods; four MCP tools and the closed hook are unchanged.
Require exact service 0.0.23 / API v1 / schema 10. Stage `m2-episode-query` adds
feature `episode_query` and metadata:
`endpoint: "/v1/episodes/query"`, `order: ["recorded_at_desc", "memory_id_desc"]`,
`pagination: "exclusive_keyset"`, `occurred_time_bounds: "half_open"`,
`max_items: 100`, `includes_content: false`.
v22→v23 is application-only: schema 10/history 1–10 and pinned artifacts remain,
with no SQL migration or dependency/provider change. Stop/drain old components
and use matching versions; no mixed-version promise.

## Validation boundary

V23 implementation
[`bf53a30625ffcfb0f23f986abcec5e2d608dcb68`](https://github.com/rioriost/pg_agmemory/commit/bf53a30625ffcfb0f23f986abcec5e2d608dcb68)
passed the full Apple Container `./scripts/test-containers.sh`:
**821 passed, 1 warning, 499.94 s**.
Exact-SHA [CI 35280254044](https://github.com/rioriost/pg_agmemory/actions/runs/35280254044)
passed: amd64 **821 passed, 1 warning, 821.33 s**;
arm64 **821 passed, 1 warning, 823.87 s**.
All three environments passed Ruff, mypy **19 source files + 1 strict SDK consumer**,
all optional installation checks, and all production smokes, including episode query.
**821 = 780 retained + 41 new cases**: 27 contract, 7 Native episodes,
7 SDK (6 mock + 1 real workflow).
Verified coverage includes closed models, timezone/range boundaries,
100/101-row tied timestamps, late historical ordering, shared-scope ACL/purge/epochs,
metadata-only read-only SQL without audit writes, typed SDK pages, and explicit
select/Explain/Remember. See [qualification evidence](../STATUS.md#v0023--schema-10).
These are implementation results; final-docs CI for this update has not run.
V22's initial implementation, failed docs run, qualified graph fixes, and separate
final-docs CI remain [historical evidence](../STATUS.md#v0022--schema-10), not v23 qualification.
