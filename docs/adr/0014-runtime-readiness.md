# ADR 0014: Bounded runtime readiness

[日本語](0014-runtime-readiness-jp.md) | [Contract](../STATUS.md#runtime-readiness) | [Operations](../operations/README.md#runtime-readiness)

- Date: 2026-09-17
- Status: accepted and verified in bounded v0.0.14/schema 9, locally and on both native architectures
- Extends: [runtime identity boundary](0001-initial-slice.md), [pgvector contract](0011-pgvector-retrieval.md), and [schema-9 administration](0013-scope-access.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/production/performance/quality/HA/DR/full-erasure qualification

**Historical version notice:** this ADR records verified v0.0.14/schema 9,
including its application-only upgrade, stage, and 24-resource SDK boundary.
[ADR 0015](0015-job-cancellation.md) records v0.0.15/schema 10 job cancellation,
which needs migration 010 and adds the 25th resource/SDK method. Readiness semantics
are retained with exact schema history 1–10; v0.0.15 is verified locally and on both native architectures.
Separate final v0.0.14 docs CI 35202931424 is recorded in
[historical evidence](../STATUS.md#v0014--schema-9).

## Decision

Preserve public `GET /healthz` as process liveness after successful startup,
returning exactly `{"status":"ok"}` without DB calls.
Add public, unauthenticated `GET /readyz` outside `/v1`, with no tenant/principal
selection; any supplied authorization header is ignored.
Expected responses are **200** with exactly `{"status":"ready"}` or **503** with
exactly `{"status":"not_ready"}`. Both carry `Cache-Control: no-store` and a
generated UUID `X-Request-ID`. The body exposes no reason, DSN, token, memory
payload, identity, or schema inventory.
Both OpenAPI responses use typed `ReadinessStatus`, not Native `ErrorBody`;
a wrong HTTP method returns 405 without running the probe.

Each admitted request runs existing `validate_runtime` on a fresh connection
with the configured runtime DSN, never admin credentials or fallback.
Explicitly set `default_transaction_read_only = on` for validation sessions,
including existing API-startup and worker validation. This setting is confined
to dedicated validation connections; later Native mutations remain writable.
Use at most four explicit SQL statements: one `SET`, then three `SELECT`
statements for role catalogs, schema history, and extension catalogs.
Reject superuser, `BYPASSRLS`, and table ownership/owner-role membership in
`memory`/`memory_ops`, including `NOINHERIT`; require exact history `[1,2,3,4,5,6,7,8,9]` and
`vector` 0.8.6 in `public`.
Do not read memory payloads, take tenant locks, or write audit/epoch/job/receipt,
source, or tombstone records. No migration, provider call, cache, background
check, or automatic retry is introduced.

Existing startup remains fail-closed. `RuntimeValidationError` subclasses
`RuntimeError`, preserves prior messages, and provides static expected-drift codes
without treating arbitrary programming errors as readiness failures.

## Admission and diagnostic boundary

Admit only one active check per API app/process. Concurrent requests immediately
return 503/log reason `probe_busy`, with no second DB connection, waiting, or
cached success. This is not a global rate limiter or request-flood qualification.
Keep a fixed `asyncio` **5.0 s active-check timeout budget** and the existing
**5 s connect/statement/lock budgets**. Cancellation/connection cleanup may add
latency, so this is not a hard wall-clock SLA. Cancellation propagates and
releases the gate/connection.

Expected failures log `readiness_unavailable`, generated `request_id`, and a
static `reason`: `runtime_role_invalid`, `schema_unavailable`,
`schema_version_mismatch`, `extension_version_mismatch`, `probe_busy`, or the
exception class name. No raw error string, traceback, DSN, token, or payload
belongs in that diagnostic.
Expected `RuntimeValidationError`, `psycopg.Error`, and `TimeoutError` produce
503. Unexpected exceptions, including ordinary `RuntimeError`, are not
converted into not-ready responses.

## What ready does not mean

This is a point-in-time connection/runtime-role/schema/vector contract, not
complete principal authorization, a table-grant/RLS-policy integrity audit,
a write transaction/writability/primary check, ongoing JWT verification,
tokenizer/provider readiness, backlog/load/HA/DR, or performance/quality/
production qualification. A SELECT-only DB may pass.
Resource routes do not invoke readiness or acquire a new permanent fail-closed
gate after drift. Existing Native authorization remains enforced; the signal
helps an operator stop traffic, not replace an authorization firewall.

Keep liveness separate from dependency readiness to avoid restart storms.
Deployments must choose failure/recovery thresholds, handle busy 503 responses,
and restrict/rate-limit public probes at their perimeter.
No Kubernetes, Compose, or Docker `HEALTHCHECK` integration is added.

## Compatibility and validation

v0.0.14 retains schema 9, PostgreSQL 18.6, pgvector 0.8.6, pinned images, and
dependency versions: no new migration or dependency is required.
Stop/drain old APIs/workers/adapters/hooks/SDK callers/admin commands and use only
matching service **0.0.14 / API v1 / schema 9**; no mixed-version rollout is claimed.
Authenticated capabilities use stage `m2-runtime-readiness` and add `health_probes`
metadata: `liveness: "/healthz"`, `readiness: "/readyz"`,
`readiness_timeout_seconds: 5.0`, `readiness_max_in_flight_per_process: 1`.
There are still 24 Native memory resource methods. No SDK/MCP/hook probe method
is added; health paths are excluded from SDK resource-route coverage.

**Final local and native v0.0.14 results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **495 tests,
1 existing warning**; 464 retained + 16 readiness unit + 15 integration tests (31 new).
Ruff, strict mypy (19 source files + 1 SDK consumer), genuine core/hook/sdk-only
installs, and all non-root production smokes passed in all three environments.
The passing disposable-DB readiness smoke uses the same API process after the normal HTTP smoke:
ready 200 → schema-ledger rename → ready 503 while health
stays 200 → ledger restoration → ready 200, with retained authenticated smokes
and no source/tombstone writes. Do not execute drift examples on a live DB.
Implementation is published at
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277).
[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)
passed on that exact SHA; actual native logs verified counts, checks, and smokes.
Test elapsed: **295.67 s local / 385.41 s amd64 / 470.16 s arm64**.
These are implementation results, not a subsequent final-docs CI run.
See [validation evidence](../STATUS.md#v0014--schema-9); test elapsed is not a performance benchmark.
Verified v0.0.13 implementation CI 35196930448 and final-docs CI 35198499967 are
distinct [historical results](../STATUS.md#v0013--schema-9), not v0.0.14 evidence.
