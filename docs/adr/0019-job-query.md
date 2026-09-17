# ADR 0019: Owned-job query and pagination

[日本語](0019-job-query-jp.md) | [Contract](../STATUS.md#owned-job-query-and-pagination) | [Operations](../operations/README.md#owned-job-query-and-pagination)

- Date: 2026-09-18
- Status: accepted and verified in bounded v0.0.19/schema 10, locally and on both native architectures
- Extends: [durable jobs](0006-durable-jobs.md), [job cancellation](0015-job-cancellation.md), and [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/performance/production/DR qualification

**Historical version notice:** this ADR records verified v0.0.19/schema 10.
[ADR 0020](0020-assertion-history.md) records assertion metadata history in
v0.0.20/schema 10 with 28 Native/SDK resources, verified locally and on both native architectures.
Separate final v19 docs CI 35244626331 is recorded in
[historical evidence](../STATUS.md#v0019--schema-10), distinct from implementation CI.

## Decision and request boundary

Add authenticated read-only `POST /v1/jobs/query`, without `Idempotency-Key`
or write-permission requirements. Closed `QueryJobs` accepts required distinct
`scope_ids` (1–32 UUIDs), distinct `states` (at most five, default `[]` means all),
strict integer `max_items` (1–100, default 20), and `before: JobCursor | None`
(default null). `JobState` reuses exactly `pending`, `running`, `succeeded`,
`failed`, `cancelled`, without lifecycle changes.
Closed `JobCursor` requires an aware `created_at` timestamp and UUID `job_id`.
Invalid shapes, duplicates, bounds, timestamps/UUIDs, or extra fields give
**422 `invalid_request`**. No owner/principal/tenant/kind/payload-query filters,
offset, watch, `after`, or historical-ACL selectors are accepted.

## Ownership and complete-page validation

Select only current-caller-owned jobs in the current tenant, requested scopes,
current RLS/source/deletion visibility, and optional states.
Scope-admin permission does not bypass ownership. Known-ID
`GET /v1/jobs/{job_id}` remains broader: currently readable same-scope jobs
owned by another principal still work there, not in query discovery.
Unknown/unreadable scopes contribute nothing, like recall.
No matches returns **200** with `jobs: []` and `next_cursor: null`, not hidden
reasons, totals, or a cross-principal discovery signal.

Reload every selected page item through `Jobs.get`, retaining reference-count
and result-liveness validation. An actual invalid selected job fails the
whole page with existing **404 `not_found` / 409 `job_invalidated`** behavior.
Do not silently skip or return partial success. Current permissions, deletion
checks, and the tenant response-delivery/drain lock apply to every page.
No claim, cancellation, state change, worker/provider call, or automatic retry occurs.

## Exclusive position without snapshot authority

Order by `created_at DESC, id DESC`, using the exclusive boundary
`(created_at, id) < (before.created_at, before.job_id)`.
Fetch `max_items + 1`, return at most `max_items`, and emit `next_cursor` only
on overflow, from the **last returned item**, not the lookahead row.
Omitted/null `before` starts newest; null `next_cursor` ends this continuation.

The cursor is unsigned transparent position, not authority, a receipt, cache,
snapshot, or retention object. It need not refer to an existing job; forged,
stale, or deleted cursor positions only bound current authorized owned rows.
State transitions retain original `created_at`; ordering is not by `updated_at`.
Every page is point-in-time, with no stable membership across state/access/
deletion changes. Newer jobs above the old cursor require an explicit restart
without `before`. There is no total count, snapshot watermark, global LSN,
persistent cursor, or automatic pagination.

## Typed response and SDK

Exactly **200 `JobPage`** contains `jobs: list[ListedJob]`,
`next_cursor: JobCursor | None`, and existing `Consistency`
(`access_epoch`, `deletion_epoch`), not a snapshot promise.
The response model caps `jobs` at 100, and the SDK validates the same bound.
Shared `MemoryService.epochs` reuses the checkpoint epoch reader for checkpoint,
recall, and job query without persistence, schema, or mutation-hash changes.
`ListedJob` adds UUID `scope_id` to full existing `JobDetail`; GET shape stays unchanged.
References, state/timing, safe error, `retry_of`, and original result revision 1
are included, not stored payload, evidence quotes, `lease_token`, or intent digest.

Async `query_jobs(QueryJobs) -> JobPage` uses required internal `mutation=False`,
call-time model validation, normal **256 KiB request / 2 MiB response** bounds,
sanitized read-only `outcome_unknown: false`, and no automatic retry/pagination.
Existing safe `job_invalidated` is reused; no new error code is added.
There are 27 Native/SDK resource methods and four unchanged MCP tools;
no job tool or hook field, and no SDK admin/worker function is added.

## Deployment and validation boundary

Require exact service 0.0.19 / API v1 / schema 10 across adapters.
Stage `m2-job-query` adds `job_query`:
`endpoint: "/v1/jobs/query"`, `ownership: "caller"`,
`order: ["created_at_desc", "job_id_desc"]`, `pagination: "exclusive_keyset"`,
`max_items: 100`.
v18→v19 is application-only: no SQL migration, dependency/provider/artifact-pin
change beyond project version. Stop/drain old components and use matching versions.
Exact history 1–10 remains; older schemas still need retained offline migrations.

**Final local and native implementation validation passed.** Apple Container
`./scripts/test-containers.sh` exited **0** with **663 passed, 1 existing warning,
366.14 s (6:06)**: **630 retained + 33 new tests** (16 `tests/test_contract.py`,
10 `tests/test_jobs.py`, 7 `tests/test_sdk.py`).
Implementation
[`e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf`](https://github.com/rioriost/pg_agmemory/commit/e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf)
passed [CI 35242118110](https://github.com/rioriost/pg_agmemory/actions/runs/35242118110)
on that exact SHA. Actual native logs verified **663 passed, 1 warning** each:
**638.06 s amd64 / 586.58 s arm64**.
Ruff, strict mypy **19 source files + 1 SDK consumer**, genuine core/hook/sdk-only
installs, and all non-root production smokes passed in all three environments.
Full suites include genuine 100-item pages/101-row overflow, equal-time UUID
boundaries, all five states and changes, deleted/forged cursors, current ACL/
scope-admin ownership exclusion, whole-page validation, read-only SQL, three
explicit typed SDK pages, payload non-disclosure, and no automatic pagination.
The new SDK production sequence passed source + three jobs → first pending page
of one item → explicitly cancel the middle job → next page only the oldest →
query cancelled jobs → source purge `object_count: 4` → empty pending query.
Elapsed time is not a benchmark. These are implementation results, not a later
final-docs CI result. See [verified evidence](../STATUS.md#v0019--schema-10).
V18 implementation and separate final-docs CI remain
[historical evidence](../STATUS.md#v0018--schema-10), not v19 qualification.
