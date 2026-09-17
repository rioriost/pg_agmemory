# ADR 0013: Privileged scope-access administration

[日本語](0013-scope-access-jp.md) | [Contract](../STATUS.md#scope-access-administration) | [Operations](../operations/README.md#scope-access-administration)

- Date: 2026-09-17
- Status: accepted and verified in bounded v0.0.13/schema 9, locally and on both native architectures
- Extends: [identity and persistence boundary](0001-initial-slice.md), [durable jobs](0006-durable-jobs.md), and [SDK boundary](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/production/performance/quality/DR/full-erasure completion

**Historical version notice:** this ADR records verified v0.0.13/schema 9,
including stage `m2-scope-access` and its startup-version decisions.
[ADR 0014](0014-runtime-readiness.md) records application-only v0.0.14 runtime
readiness with the same schema 9 and unchanged scope-access contract.
v0.0.14 local and both native checks passed. The separate final v0.0.13 docs CI 35198499967
is recorded in [historical evidence](../STATUS.md#v0013--schema-9).

## Decision and authority

Add `pg-agmemory scope-access get|set|revoke` for trusted administration of
**existing same-tenant** tenant/scope/principal UUIDs. It never creates tenants,
scopes, or principals.
Use only `PGAG_ADMIN_DATABASE_URL` and a DB role with `rolsuper` or `rolbypassrls`
plus the appropriate SQL privileges. Reject runtime credentials even for `get`.
`get` uses no `FOR UPDATE`, permitting a nonowner `BYPASSRLS` inspector with
`memory` USAGE and SELECT on schema history/tenant/scope/principal/`scope_member`.
Mutations additionally require tenant UPDATE, applicable membership DML,
`memory_ops` USAGE, and audit INSERT. The RLS-bypassing role remains mandatory.
No JWT, `--subject`, `--once`, runtime-URL fallback, or identity from retrieved text.
There is no HTTP/MCP/SDK admin method; public memory resources remain unchanged.

Prefer this bounded CLI over the historical handwritten SQL runbook. The CLI
validates the role, exact schema history 1–9, and `vector` 0.8.6 in `public`
before operating. PostgreSQL 18.6/pgvector 0.8.6 pinned images and Python dependency
versions are unchanged. The stage becomes `m2-scope-access`; capabilities add
`scope_access_administration` with `transport: "admin-cli"`,
`command: "scope-access"`, `compare_and_swap: "tenant_access_epoch"`,
`audit: "database_role"`, not a new endpoint.

## Explicit replacement and tenant-wide CAS

`get` accepts only `--tenant-id`, `--scope-id`, and `--principal-id`.
Both mutations require `--expected-access-epoch` in **1–9223372036854775807**.
Compare the tenant-wide counter under lock **before** determining no-op status.
Stale CAS always conflicts, including after lost output or an unrelated scope
change; it is not an idempotency key or replay receipt.

`set` requires a full replacement permission list and exactly one explicit
expiry choice: future timezone-aware `--expires-at` or `--no-expiry`.
Allow distinct read/write/delete flags, including write-only/delete-only, or
`admin` alone; reject duplicates and mixed admin flags. These DB flags do not
override Native action/read requirements. Permission reordering is equivalent.
Reject naive timestamps and expiry at/before the DB clock checked after locking.
Expiry-only changes advance the epoch; omission never silently grants permanent access.
`revoke` deletes the row, accepts no permission/expiry options, and is a no-op
if already absent at the current expected epoch.

Success is one unwrapped JSON line containing `operation`, `tenant_id`,
`scope_id`, `principal_id`, `access_epoch`, `changed`, `membership_exists`,
`permissions`, `expires_at`, `effective_permissions`, and `evaluated_at`.
Absent membership is false/empty/null, distinct from an existing legacy empty
permission row. Flags use read/write/delete/admin order; effective admin expands
to all four flags and expired membership to none.
This is membership state at DB `evaluated_at`, not complete Native authorization
or future access. Natural expiry changes neither epoch nor retained payload/audit
and does not drain in-flight responses. Explicit revoke/barrier is required for strong drain.

## Durable audit requires schema 9

Add `009_scope_access.sql` and privileged-only `memory_ops.scope_access_event`
with forced RLS and no runtime policies/grants. `(tenant_id, access_epoch)` is the
primary key, with same-tenant scope/principal foreign keys. Store `set`/`revoke`,
before/after flags/expiry, DB-clock `recorded_at`, and `database_role` from `current_user`,
not plaintext memory content, external subjects, or DSNs.
`evaluated_at` is response-only, not an audit field.
`database_role` is the executing role, not a claimed end-user actor.
Preserve existing ACL rows without audit backfill for prior manual changes;
no implicit ownership or purge semantics change is introduced.
Only actual changes atomically modify membership, advance the tenant epoch, and
append an event. No-op/get/conflict creates no event or epoch advance; mid-change
failure rolls all three back. Reject epoch exhaustion rather than wrap.

This schema change is necessary for durable administrative audit, but privileged
administrators can alter the database. It is not tamper-proof, a standalone
revocation-recovery ledger, or a DR solution. Grants cannot resurrect purged data;
restoring current ACL/deletion records remains manual.

## Barrier and uncertain outcomes

Use one dedicated synchronous autocommit admin connection with **5 s connect,
statement, and lock timeouts**. Acquire the same canonical tenant **session**
advisory lock as API/worker, including for `get` and no-ops.
Hold it through transaction commit **and CLI JSON stdout flush**; close on all
paths to release it. Do not pool the connection or use an xact-only lock.
Slow preceding responses can delay administration; a lock timeout changes nothing.
Cooperating same-version API clients can remain online during administration.
Migration still requires stopping/draining old APIs/workers/adapters/hooks/SDK callers.
Require exact service 0.0.13 / API v1 / schema 9 thereafter; no rolling coexistence.

Syntax/model/config errors are static sanitized stderr, exit 2, without JSON.
DB/domain errors are stdout `{error: {code, outcome_unknown}}`, exit 1; no raw DB
error/DSN/credentials/data. The [catalog](../STATUS.md#scope-access-administration)
distinguishes role/privilege/schema/extension, not-found/CAS/expiry/exhaustion,
and database failures. Commit transport failure is conservatively unknown;
pre-commit-attempt failures are not. Cancellation, kill, or lost stdout can also
leave mutation outcome unknown. Inspect fresh `get` and privileged audit before
explicitly authorizing a new CAS operation. No automatic retry, Idempotency-Key,
admin mutation receipt, or queue. The barrier does not retract delivered context.

## Validation boundary

**Final local and native v0.0.13 results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **464 tests,
1 existing warning**, Ruff, strict mypy (19 source files + 1 SDK consumer),
genuine core/hook/sdk-only installs, and every non-root production smoke including scope-access.
The total is 426 retained + 22 unit + 16 integration tests (38 new).
Passing fixtures cover schema-8→9 ledger failure after DDL, rollback/retry,
prior migrations, role/CLI/CAS/expiry/audit, response/flush barriers,
uncertain-commit cleanup, and retained resources/adapters.
Implementation is published at
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413).
[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)
passed on that exact SHA; actual native logs verified counts, checks, and smokes.
Test elapsed: **297.52 s local / 539.86 s amd64 / 460.73 s arm64**.
These are implementation results, not a subsequent final-docs CI run.
See [validation evidence](../STATUS.md#v0013--schema-9); elapsed time is not a performance benchmark.
The distinct verified v0.0.12 implementation and final-docs runs are
[historical evidence](../STATUS.md#v0012--schema-8), not schema-9 validation.
