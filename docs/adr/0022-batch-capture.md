# ADR 0022: Explicit batch capture

[日本語](0022-batch-capture-jp.md) | [Contract](../STATUS.md#explicit-batch-capture) | [Operations](../operations/README.md#explicit-batch-capture)

- Date: 2026-09-18
- Status: accepted in bounded v0.0.22/schema 10; graph fix and all-direction regression verified locally and natively
- Extends: [atomic capture](0010-atomic-capture.md), [durable jobs](0006-durable-jobs.md), and [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/performance/memory-quality/production/DR qualification

## Decision and request boundary

Add Native JWT authenticated `POST /v1/captures/batch` with a caller-owned
`Idempotency-Key`. Closed `CaptureBatch` has exactly `episode: Observe` and
`memories: list[CapturedMemory]` with **1–16** entries. Observe and CapturedMemory
retain their existing normalization, bounds, and explicit-intent rules.
Each caller-supplied proposal uses the episode's scope and exactly one literal
quote from that normalized episode; it cannot override scope, evidence IDs, or
identity. Duplicate candidates after normalized model JSON serialization,
including trim-equivalent entries, are rejected with **422 `invalid_request`**.
List order matters. No LLM extraction, provider call, automatic capture, or
semantic-truth claim is introduced.

Exactly **201 `CaptureBatchResult`** returns `memory_id` (episode UUID),
`revision: 1`, and `synthesis_job_ids` (1–16 UUIDs in request order).
It returns job references, not assertions or proof that a job is new/pending.
Existing `POST /v1/captures`, `Capture`, `CaptureResult`, and `atomic_capture`
remain single-job contracts with `max_jobs: 1`.

## Atomic admission, independent publication

Reuse Observe and `Jobs.enqueue` in the existing tenant transaction and response
barrier, with operation `capture_batch` receipt/audit. Internal composition uses
the separate `capture-batch-observe-v1` namespace and indexed
`capture-batch-job-v1:keyhash:index` keys, not caller-supplied internal keys.
All **new** episode, lexical projection, job, identity, receipt, and audit writes
commit together. A late invalid quote, exhaustion of the remaining **100 active
jobs per scope** quota midway, or audit failure rolls back the whole new admission;
pre-existing rows remain unchanged. The quota applies only to newly admitted
jobs; the 16-entry request limit is not a new global quota.

Once admitted, workers publish each job independently. Completion is not atomic,
and request order gives no execution-order guarantee. Use existing per-job GET,
owned-job query, cancellation, and retry; there is no batch job, aggregate status,
or group cancellation. No worker/provider is invoked by admission.

## Identity, replay, and deletion

Fresh keys for the same source and job intents reuse original IDs, including
cancelled, failed, and succeeded jobs, without revival. A fresh key with reordered
candidates returns reordered existing IDs; the same key with reordered body
conflicts with **409**.
An exact-key receipt rechecks current write access and **every** returned job's
object liveness. Purging the source or one job causes whole-receipt **404**,
not partial replay or resurrection. Source purge retains the existing closure
over dependent jobs/assertions. Individual failed-job retry remains explicit;
capture replay does not replace original IDs with retry children.

## SDK, bounds, and deployment

Async `capture_batch(CaptureBatch, *, idempotency_key) -> CaptureBatchResult`
uses `mutation=True` and expects 201. Preserve the original key/body after an
uncertain outcome; no automatic retry, key replacement, or batch splitting.
Cancellation of an in-flight mutation does not prove rollback.
Call-time strict request validation and the normal **256 KiB request / 2 MiB
response** bounds remain. Sixteen individually valid large candidates can exceed
the Native request-body limit and receive **413**.

Native/SDK has 30 resource methods, four unchanged MCP tools, and the unchanged
closed read-only hook; no batch tool or hook field is added.
Require exact service 0.0.22 / API v1 / schema 10. Stage `m2-batch-capture` adds
feature `atomic_batch_structured_capture` and `atomic_batch_capture` metadata:
`endpoint: "/v1/captures/batch"`, `max_jobs: 16`,
`recipe_version: "structured-remember-v1"`, `automatic_capture: false`,
`admission_atomic: true`, `publication_atomic: false`.
v21→v22 is application-only: schema 10/history 1–10, pinned artifacts, and
dependencies/backend/providers are unchanged. No SQL migration or mixed-version
promise; stop/drain old components and use matching versions.

## Validation boundary

**Graph fix and all-direction regression verified.**
Directional follow-up
[`3f56c51434333428fe742bb6a464d1d3117e8e26`](https://github.com/rioriost/pg_agmemory/commit/3f56c51434333428fe742bb6a464d1d3117e8e26)
passed the full Apple Container `./scripts/test-containers.sh`:
**780 passed, 1 warning, 507.09 s**.
[CI 35271311062](https://github.com/rioriost/pg_agmemory/actions/runs/35271311062)
passed on that exact SHA: amd64 **780 passed, 1 warning, 819.23 s**;
arm64 **780 passed, 1 warning, 745.25 s**.
All three environments passed Ruff, mypy **19 source files + 1 strict SDK consumer**,
all optional installation checks, and all production smokes.
The nine `auto`/`generic`/`nested_loop` × `outgoing`/`incoming`/`both` combinations
cover both adjacency branches and passed:
**780 = 773 batch baseline + 1 nested-loop case + 6 direction cases**.
This extension changes regression coverage, not product SQL, version, or schema.
Final-docs CI for this update has not run.

**Earlier 774-test graph-fix qualification:**
Fix [`bf7429327955071239fdc2f7b60d1a5d47dfff7e`](https://github.com/rioriost/pg_agmemory/commit/bf7429327955071239fdc2f7b60d1a5d47dfff7e)
passed the full Apple Container suite: **774 passed, 445.86 s**, with all smokes.
[CI 35268438022](https://github.com/rioriost/pg_agmemory/actions/runs/35268438022)
passed on that exact SHA: amd64 **774 passed, 779.45 s**;
arm64 **774 passed, 682.78 s**. Both passed Ruff, mypy **19 source files +
1 strict SDK consumer**, all installation checks, and all production smokes.
The negative control against unmodified pre-fix source in an earlier test image
produced **1 expected failure**. Its retained synthetic
`graph-canonical-baseline-plan.json` showed `assertion`/`assertion_revision`
at **400 loops** and endpoint-evidence at **4 loops**.
This is not the failed CI plan or a production performance benchmark.

Final-docs revision
[`af91974d091feb276db79baf838c7fa2bab8904f`](https://github.com/rioriost/pg_agmemory/commit/af91974d091feb276db79baf838c7fa2bab8904f)
**failed** [CI 35265233011](https://github.com/rioriost/pg_agmemory/actions/runs/35265233011):
amd64 **772 passed, 1 failed, 631.34 s**; arm64 **773 passed, 701.79 s**, with all smokes.
The existing 100-path auto-plan graph limit case again received **503**
with `QueryCanceled` / statement timeout. The actual failed CI plan was **not captured**.
The retained disposable diagnostic `graph-materialized-plan.json`, not that CI
plan, shows residual canonical-metadata and endpoint rescans after `adjacent`
materialization. The follow-up retains that CTE and separately materializes
scope/predicate-filtered assertions, time-filtered revisions, and distinct
authorized/evidence-valid endpoints. Precomputed ID arrays are intended to
prevent semijoin reversal and repeated protected canonical scans; the outer join
uses materialized authorized metadata.
RLS, scope/time/evidence semantics, ordering, limits, and the **5000 ms** timeout
remain unchanged, as do version/API/schema and 30 resource methods.

The qualified all-direction regression covers `auto`, `generic`, and `nested_loop`,
retaining the actual generic-prepared-usage assertion. Runtime-role `EXPLAIN ANALYZE`
of the actual SQL and parameters checks 100 rows at 100 paths with
`assertion`/`assertion_revision` scan loops **<= 1** and
`entity`/`entity_evidence` scan loops **<= 2**.
All nine mode/direction combinations passed these checks; this does not establish
production completion.

**Initial v22 implementation evidence, not qualification of the follow-up.** The full Apple
Container `./scripts/test-containers.sh` completed with **773 passed,
1 existing warning, 447.40 s**: **730 retained + 43 new cases**
(8 contract, 18 capture, 17 SDK).
Capture adds 17 cases and one batch parameter to an existing actual response-loss
test; SDK adds 9 bad-key parameters, 6 mock cases, 1 real workflow, and one batch
parameter to an existing actual response-loss test.
The existing 100-active-job quota test also covers late batch rollback without
an additional counted case.
Implementation
[`75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe`](https://github.com/rioriost/pg_agmemory/commit/75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe)
passed [CI 35262682028](https://github.com/rioriost/pg_agmemory/actions/runs/35262682028)
on that exact SHA: native amd64 **773 passed, 621.56 s**;
arm64 **773 passed, 702.01 s**.
Ruff, mypy **19 source files + 1 strict SDK consumer**, core/hook/sdk-only
installations, and all previous production smokes plus batch capture
worker/replay/purge passed in all three environments.
Coverage includes 1/16/17-entry bounds, normalized duplicates, late
quote/audit/quota rollback, principal ownership/current write ACLs, ordered replay
and conflicts, legacy capture/Observe interoperability, independent terminal
jobs, source/job/result purge, all-child replay validation, and old-lease fencing.
Actual committed response loss produces SDK `outcome_unknown: true`; explicit
same-key/body recovery and the 256 KiB body bound are checked.
The initial results do not override the failed final-docs CI or qualify the follow-up.
Elapsed time is not a benchmark; no M0–M3/MVP, performance, memory-quality,
production, or DR qualification is claimed.
See [verified evidence](../STATUS.md#v0022--schema-10).
V21's failed docs run, qualified code fix, and separate successful final-docs CI
remain [historical evidence](../STATUS.md#v0021--schema-10), not v22 qualification.
