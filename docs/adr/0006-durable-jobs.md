# ADR 0006: Durable structured-publication jobs and fenced workers

[日本語](0006-durable-jobs-jp.md) | [Current contract](../STATUS.md#durable-jobs) | [Operations](../operations/README.md#durable-job-and-worker-operations)

- Date: 2026-09-17
- Status: v0.0.6/schema 6 implemented; local and native Docker checks passed; M0/M1/M2/M3 incomplete
- Extends: explicit assertion publication and dependency purge; preserves the
  graph, checkpoint, and effect histories from [ADR 0005](0005-relational-graph.md)
- Naming: local directory/package/service `pg_agmemory`; public repository `rioriost/pgag_memory`

Historical scope: schema-6 requirements and evidence below describe v0.0.6 only.
Schema-7 lexical indexing and current maintenance requirements are in
[ADR 0007](0007-japanese-fts.md); v6 results do not validate that milestone.

## Decision and scope

Store explicitly requested asynchronous **structured memory publication** in
PostgreSQL and process it with a fixed-principal worker. This is not automatic
synthesis, natural-language extraction, LLM/provider processing, embedding,
compaction, or tool execution. `observe` remains synchronous observation with no
automatic enqueue and `synthesis_job_id: null`. Synchronous `remember` retains
its contract and legacy normalized JSON/HMAC ordering.

The initial profile is not a global multi-tenant scheduler or qualified
fairness/cost-pool design. M0/M1/M2/M3, MVP/production, performance, memory quality,
full-erasure, and backup/DR acceptance remain incomplete.

## Explicit job identity and safe status

`POST /v1/jobs` requires `Idempotency-Key` and
`{kind: "structured_remember", memory: <unchanged Remember request>}`.
Require current scope read/write access, explicit intent, and 1-32 distinct
same-scope readable episode IDs with literal quotes of 1-4096 characters.
The only recipe is `structured-remember-v1`; `202` returns
`{job_id, kind, recipe_version}`, not a completed assertion.

`memory_ops.job` has a kind-`job` object anchor and immutable episode revision-1
`job_input` dependencies. Retained `job_identity` stores tenant/principal/scope
HMAC identity. Canonical intent/recipe deduplicates across HTTP keys, with sorted
evidence for job dedup. Same HTTP key still requires the same normalized request.
Different principals may submit independent jobs; different source identities
are not semantically deduplicated. Each scope allows 100 pending/running jobs,
and each job has at most five attempts.

`GET /v1/jobs/{job_id}` uses current scope read access, including for another
principal's same-scope job. It returns state (`pending`, `running`, `succeeded`,
`failed`), attempt/max-attempt counts, scheduling/lease timestamps, safe error
code, input references, retry parent, and a nullable original revision-1 result.
Later assertion correction does not change that result reference. Request
payload, lease token, and owner principal are never exposed. Terminal success
and failure erase request JSON; immutable input ID references persist until purge.

Jobs are not checkpoint/effect reference kinds or recall/explain items.
Recall adds `coverage.jobs_pending` for readable pending/running jobs in requested
scopes, while retaining `synthesis_pending: false` and `graph_used: false`.
It does not automatically expand the SQL graph or claim completed synthesis.

## Explicit retry, not terminal mutation

`POST /v1/jobs/{job_id}/retry` takes a key and the full original `EnqueueJob`
body, since the failed request was erased. Only the failed parent's owner may
retry, under current permissions/evidence and HMAC intent checks. Changed intent
is `409 job_intent_conflict`, nonfailed parent is `409 job_retry_conflict`, and
nonowner is `404`.

One failed parent has one deduplicated retry child, even across HTTP keys.
The child gets a new five-attempt allowance; the old terminal is immutable.
If the child fails, retry that child ID for another explicit cycle. Arbitrary
recipe resets are not supported. Retry lineage is retained for dependency purge.

## Claims and atomic publication

`pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT [--once]` runs
with restricted `PGAG_DATABASE_URL`. Subject is trusted deployment configuration
for one preprovisioned principal in the configured issuer, not an HTTP
impersonation facility. No JWT signing/public key or admin URL is needed.
API startup role/schema validation also protects the worker; superuser,
table-owner/owner-member, and `BYPASSRLS` credentials are rejected.
Only jobs belonging to that principal can be claimed or published.

API and worker share `principal_connection` lookup, `bind_identity` revalidation,
and the same tenant **session** advisory lock. The API still holds the lock
through commit and HTTP response delivery. Worker transactions are short:

1. Claim one due job with `FOR UPDATE SKIP LOCKED`, increment the attempt, capture
   current access/deletion epochs, and assign a fresh UUID token with a 30-second
   lease. Validate current permissions and complete immutable inputs, then commit.
   Internal claim bounds of 1-300 seconds are for controlled tests, not CLI tuning.
2. Validate/prepare the stored structured request outside the transaction.
   The deterministic processor makes no external call.
3. Rebind identity and recheck current scope, complete episode inputs, exact
   original prepared body, token/lease/expiry, and epochs. Use the shared
   assertion-publication helper, committing result assertion, provenance, job
   success, and audit together. Check expiry again on the final update and roll
   back all output if the lease expired mid-publication.

Assertion recorded/system time starts at worker publication, not enqueue.
Job creation time is not assertion adoption time; caller valid bounds remain
independent of server-controlled system history.

Lease expiry/takeover fences stale writers. Internal heartbeat checks lease and
epochs and renews the 30-second lease; it has no HTTP endpoint. There are no
public claim/publish APIs or long-running heartbeat task for this processor.
At-least-once attempts yield at most one committed result per job, not an external
exactly-once guarantee.

Retriable errors use `2^attempt + [0,1)` seconds of backoff/jitter, at most five
attempts. Nonretryable invalid input fails immediately. An expired fifth claim
becomes failed with `attempt_limit`, never a sixth attempt. Logs/status use safe
codes (`dependency_unavailable`, `stale_context`, `invalid_input`, `attempt_limit`),
not payloads. Explicit retry is the recovery path for terminal failures.

Continuous mode polls idle work every second and delays two seconds after
transient DB loop failures. `--once` processes at most one due job and emits JSON
`idle`/`succeeded`/`pending`/`failed`/`lease_lost` with applicable opaque IDs/result
reference, then exits; other commands reject the option.
Stdout/log outcomes are opaque historical references, not current read
authorization. Job GET and exact-revision explain apply current access/deletion.

## Purge direction and compatibility

Dependency closure adds episode inputs -> jobs, result assertions -> jobs, and
parent jobs -> retry descendants within the existing 10,000-dependent bound.
**Job/control-record or failed-parent retry-chain deletion does not delete
already-published independent assertion outputs or source episodes.** Output
assertions have their own direct episode provenance: purge output/source
explicitly to erase the fact. Any revision-source deletion purges the entire
assertion and dependent jobs. There is no job -> result dependency cycle.

Job request/input rows are removed before assertion/episode rows and tombstones,
atomically under the tenant barrier. Purge fences running publishers; job GET/
replay returns `404` after purge. Retained job identities prevent exact-job
resurrection. Opaque anchors, job dedup, and HTTP receipts remain for the tenant
lifetime. Backup/WAL/replica/delivered-data erasure and DR are not guaranteed.

Additive `006_durable_jobs.sql` leaves 001-005 unchanged. Forced RLS, same-scope
foreign keys, input completeness, bounded lifecycle UPDATE grants, and job
transition guards protect intent, leases, attempts, and terminal immutability.
Existing typed graph/effect/checkpoint histories and legacy idempotency are
preserved. Jobs do not become checkpoint/effect reference kinds.

Stop/drain **all old/new APIs and workers**, back up, run `pg-agmemory migrate`,
verify exact history `[1, 2, 3, 4, 5, 6]`, then start only matching API/worker
versions. No rolling coexistence or downgrade is supported. Old v0.0.1 lacks
a startup schema guard and must remain stopped. Restored databases stay
quarantined from APIs/workers until deletion/ACL state is reapplied.

## Evidence boundary

For implementation
[a4aa7f6](https://github.com/rioriost/pgag_memory/commit/a4aa7f6c8a9ccc52f906619c64e70a8d00eae0d8),
Apple Container and native Docker amd64/arm64 each passed 114 tests (2 existing
warnings), Ruff, strict mypy (11 source files), and both non-root production API
HTTP and actual CLI worker smoke. Both CI jobs ran that exact SHA in
[run 35168437396](https://github.com/rioriost/pgag_memory/actions/runs/35168437396);
actual logs verified the results.

Coverage includes job identity/retry/caps, lease takeover and expiry rollback,
current authorization/epochs, atomic publication, dependency purge, and historical
compatibility. Worker smoke used a disposable principal and runtime-only
credentials for `pg-agmemory worker --subject ... --once`, verified
`{"outcome":"idle"}`, and logged `Production worker smoke passed`. This idle result
does not itself establish queued publication. See
[STATUS](../STATUS.md#validation-evidence) for timings and scope.
Historical v5 evidence remains separate in [ADR 0005](0005-relational-graph.md).
Passing checks does not complete M0/M1/M2/M3 or qualify automatic synthesis,
performance, memory quality, MVP/production, full erasure, or backup/DR.
