# ADR 0015: Explicit job cancellation

[日本語](0015-job-cancellation-jp.md) | [Contract](../STATUS.md#explicit-job-cancellation) | [Operations](../operations/README.md#explicit-job-cancellation)

- Date: 2026-09-17
- Status: accepted and verified in bounded v0.0.15/schema 10, locally and on both native architectures
- Extends: [durable jobs](0006-durable-jobs.md), [capture](0010-atomic-capture.md), [SDK](0012-python-sdk.md), and [runtime readiness](0014-runtime-readiness.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/production/performance/quality/DR/full-erasure qualification

**Historical version notice:** this ADR records verified v0.0.15/schema 10 job
cancellation. [ADR 0016](0016-required-context.md) records v0.0.16 required-context
recall on the same schema and 25-resource surface, with application-only
v15→v16 deployment. The implementation is verified locally and on both native architectures.
Separate final v0.0.15 docs CI 35218254940 is recorded in [historical evidence](../STATUS.md#v0015--schema-10).

## Decision and authority

Add authenticated `POST /v1/jobs/{job_id}/cancel` with caller-owned
`Idempotency-Key` and `CancelJob`: only `expected_state` (`pending`/`running`) and
strict integer `expected_attempt` 0–5, with running requiring at least 1.
Reject extra fields, reason/private free text, provider/lease-token parameters,
and arbitrary state forcing.
The caller explicitly chooses a state/attempt CAS from job GET, not tenant
access epoch or external tool state/version.

Require authenticated current ownership (`principal_id`) and scope read/write
permissions, plus source visibility/integrity. Even an `admin` same-scope reader
cannot cancel another owner's job. Runtime `job_update` RLS is unchanged.
Missing/private/cross-tenant/non-job/purged targets give 404; invalidated references
give `409 job_invalidated`.
An atomic UPDATE matches expected state and attempt; success is **HTTP 200**
with `JobReceipt` (`job_id`, fixed kind `structured_remember`, fixed recipe
`structured-remember-v1`). GET confirms cancelled. This is committed cancellation,
not a 202 cancellation queue or worker kill.

## Terminal transition and races

Allow pending or running, including active/expired leases, to become `cancelled`.
Clear stored job payload, lease token/deadline, and error, with no result.
Keep the same job ID, attempt, source/evidence/opaque intent references, retry
parent, and `created_at`; DB `updated_at` records completion.
Transition, `job_cancelled` audit, and the HMAC-backed request/key idempotency
receipt are atomic or all roll back. The stored replay result is only `{job_id}`.
Do not advance access/deletion epochs.

Use the existing tenant session advisory lock through HTTP delivery, also used
by worker claim/publication. Old preparation may continue, but cancelled is not
running: publish/heartbeat/fail gets `job_lease_conflict`, and worker outcome is
`lease_lost`. If cancellation wins, that job publishes no result.
If publication commits first, cancellation conflicts and the succeeded result
remains. No provider abort, external compensation, or unpublishing is promised.

## Replay and retention

State/attempt mismatch or terminal state gives `409 job_cancel_conflict`,
including a new-key cancel of already-cancelled work. Successful same-key/body
replay returns the original receipt only under current owner/write/access/
liveness checks; changed body or job ID under that key is `409 idempotency_conflict`.
Unknown delivery requires the same key/body or current job GET, not automatic
retry, a new key, or blindly updated expected values.

Cancelled remains terminal and cannot use failed-only retry (`409 job_retry_conflict`).
Same-intent enqueue/capture dedups to it rather than reviving it; capture replay
keeps the original episode/job pair. Do not invent recipe versions or intent keys
to bypass dedup. The active-job cap and `jobs_pending` exclude cancelled.
Cancellation is not `forget`: canonical episodes/evidence, intent/dedup anchors,
prepared worker memory, WAL, and backups are not erased.
Source forget still purges dependent jobs and denies cancellation replay;
later grants do not resurrect purged payload. Explicit forget remains the data-removal path.

## Schema and adapters

New `010_job_cancellation.sql` changes job-state/payload constraints and the guard
trigger, adding no table. Cancelled is immutable: no revival/rewrite; payload,
result, lease, and error must be null. New jobs still require pending/attempt 0.
Other existing states retain their semantics. Schema 9→10 requires stop/drain
and matching migration tooling; the v13→v14 application-only procedure is historical.
PostgreSQL 18.6, pgvector 0.8.6, dependency/provider choices, and artifact pins stay unchanged.

Add async SDK `cancel_job(UUID, CancelJob, *, idempotency_key) -> JobReceipt`,
with call-time revalidation, exact HTTP 200, and safe `job_cancel_conflict`.
Internal SDK POSTs explicitly classify reads/mutations, not by success status:
200 cancellation still validates its key and conservatively treats uncertain
outcomes as mutations. `forget` preview retains conservative mutation handling.
There are now 25 Native memory resource/SDK methods; MCP's four tools and
the read-only hook are unchanged. Python task cancellation does not invoke this endpoint.
Require exact service 0.0.15 / API v1 / schema 10 across adapters and readiness
history 1–10. Stage becomes `m2-job-cancellation`; capabilities add
`job_cancellation`: `endpoint: "/v1/jobs/{job_id}/cancel"`,
`compare_and_swap: ["state", "attempt"]`, `terminal_state: "cancelled"`,
`provider_interruption: false`.

## Validation boundary

**v0.0.15 implementation qualification passed locally and on both native architectures.**
Implementation
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)
passed [CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)
on that exact SHA. Each environment passed **535 tests, 1 existing warning**:
**495 retained + 40 new**, without a unit/integration split.
Local Apple Container took **298.29 s (4:58)**; actual native logs verified
**406.88 s amd64 / 490.57 s arm64**. These are not performance benchmarks.
Ruff, strict mypy **19 source files + 1 strict SDK consumer**, genuine optional
installs, and all production smokes passed in all three environments.
Schema-9→10 ledger failure after DDL restored the prior guard function,
constraints, and schema-9 history before successful retry; legacy v6 job
state/attempt/payload remained unchanged.
Actual committed cancellation-response loss produced `outcome_unknown: true`;
identical replay succeeded, while a different key gave `409 job_cancel_conflict`
with `outcome_unknown: false`.
The production SDK enqueue/cancel/same-key replay/GET cancelled/worker `--once`
idle/source-purge smoke passed, with `object_count: 2` for that fixture.
No later final-docs publication/CI result is claimed.
See [implementation evidence](../STATUS.md#v0015--schema-10).
The distinct verified v0.0.14 implementation CI 35201615965 and final-docs
CI 35202931424 remain [historical evidence](../STATUS.md#v0014--schema-9).
