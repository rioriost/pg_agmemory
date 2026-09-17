# ADR 0010: Atomic structured capture

[日本語](0010-atomic-capture-jp.md) | [Delta contract](../STATUS.md#atomic-structured-capture) | [Operator example](../operations/README.md#atomic-structured-capture-operations)

- Date: 2026-09-17
- Status: v0.0.10/schema 7 implemented; final local and native amd64/arm64 checks passed
- Extends: [ADR 0006](0006-durable-jobs.md) and [ADR 0009](0009-implicit-recall-hook.md), preserving Native authorization, jobs, and read-only hook semantics
- Naming/license: `rioriost/pg_agmemory`; package/service `pg_agmemory`; MIT unchanged; bilingual documentation maintained
- Acceptance: full M0–M3, MVP, production, performance, memory quality, DR, and full-erasure gates remain incomplete

**Historical scope:** this ADR records v0.0.10/schema 7 and its verified evidence.
[ADR 0011](0011-pgvector-retrieval.md) records v0.0.11/schema 8 vector retrieval;
its migration and startup requirements are separate. Capture never automatically generates embeddings.

## Decision and scope

Add Native **`POST /v1/captures`** with a mandatory caller-owned
`Idempotency-Key`. One explicit request atomically commits or reuses one episode
and one structured-publication job. This closes the partial-write gap between
separate Observe and enqueue calls; it does not infer intent from arbitrary
conversation or turn every observation into a job.

The request is:

```text
{
  episode: <unchanged Observe>,
  memory: {
    subject, predicate, value, evidence_quote, explicit_intent: true,
    valid_from: <aware timestamp or null>,
    valid_to: <aware timestamp or null>
  }
}
```

`memory` is one structured intent, not a list. It has **no scope, evidence IDs,
or identity fields**. The server derives scope from the episode and binds the
single episode ID/revision 1 as evidence inside the transaction.
The quote must be a literal 1–4,096-character substring of the normalized captured
episode. Keep Remember subject 1–256 characters, predicate
`^[a-z][a-z0-9_]{0,63}$`, value 1–65,536 characters, explicit intent true, and
aware/null valid bounds with start before end when both are specified.
Normalization and Native tenant/scope authorization remain authoritative;
capture does not widen access.

There is **no LLM, extraction provider, automatic synthesis, natural-language
synthesis, pgvector, or semantic truth qualification**. Literal evidence
establishes provenance, not whether a quote supports a proposition.
Publication retains `epistemic_status: "reported"` and uncalibrated confidence
(`score: null`, `method: "uncalibrated"`).

## Commit is not publication

HTTP **201** returns:

```text
{memory_id: <episode UUID>, revision: 1, synthesis_job_id: <job UUID>}
```

This is the committed episode/job pair, **not a published assertion**.
The job may already exist or be terminal: **201 guarantees neither a fresh
nor a pending job**. GET is authoritative for current status; returned IDs are historical.
There is at most one `structured_remember` / `structured-remember-v1` job per
capture operation. Use `GET /v1/jobs/{synthesis_job_id}` for fresh state and the
existing fixed-subject worker for eventual assertion publication.
Worker publication remains a separate atomic transaction and sets the
assertion's server-recorded publication time then, not at capture enqueue.
The existing 100 pending/running jobs per scope, five attempts, leases,
access/deletion epochs, and publication fencing remain.
This is not an external exactly-once guarantee.

Pure `POST /v1/observe` keeps its exact schema/result, including
`synthesis_job_id: null`, and **does not automatically enqueue**.
Explicit `POST /v1/jobs` and synchronous `/v1/remember` remain unchanged.
Pure Observe/Remember normalized serialization and HMAC are byte-compatible.

## Atomicity and identity

One PostgreSQL transaction covers episode storage and lexical projection,
job input/control/identity, idempotency including the outer capture receipt,
and audit. Transaction faults after either write or the outer receipt roll back
**all new changes**. An episode that already existed independently remains;
no partial new job survives. An HTTP timeout after commit is not rollback proof:
retain the original key/body and recover through replay with current access.

Under current ACL/deletion checks:

| Request | Result |
|---|---|
| Same capture key and normalized body | Identical episode/job pair |
| Same key and changed body | `409`, no new partial writes |
| Different HTTP keys, same episode/intent/principal | Both IDs deduplicate |
| Previously observed identical event | Reuse the episode; the wrapper explicitly supplies job intent |
| New key and different explicit intent | May create a separate job using the retained episode |
| Another authorized principal, same event | Shared episode source deduplication, independent job identity/worker ownership |
| Same source-event identity, changed episode body | `409`, no new partial writes |

Existing source identity uses tenant/scope and the HMAC of the
source-namespace/event-ID pair, not a globally unique event ID.
Different-intent jobs are intentional, not evidence of semantic deduplication.
Retained opaque source/job/idempotency anchors include internal composition keys
server-HMAC-derived from the caller's key. These are neither client-supplied
fields nor autogenerated MCP caller keys; caller key-reuse duties do not change.

## Current access, deletion, and retries

Capture IDs are historical references, not fresh state. **Current ACL/deletion
checks win for both returned IDs**, including replay after API process restart.
Do not return a partial pair when either side is no longer readable.

| Purge target | Consequence |
|---|---|
| Source episode | Closes dependent jobs and assertion descendants |
| Job alone | Leaves episode and independently stored published output; old capture replay or a new key for the same intent returns `404`, never recreating the purged job identity |
| Published result assertion | Removes its dependent job but retains source; the old pair becomes invalid |

A new explicit **different intent** on retained source is allowed by existing
job semantics. This is not a permanent whole-source seal.
Failed jobs may be explicitly retried via existing
`POST /v1/jobs/{job_id}/retry`, with the complete original `EnqueueJob` intent
and caller key. The retry child follows existing identity/ownership rules.
Old capture replay returns its original failed job reference, not the child
and not an automatically requeued job, even after explicit retry-child creation.

The Native tenant HTTP response-drain advisory barrier is unchanged.
Delivery to trusted local clients does not atomically cover host buffers,
stdout, host context, or external copies. There is no retraction/notification
mechanism, host-erasure proof, or new backup/WAL/full-erasure guarantee.
Opaque retained anchors are not erased-memory content or a full-erasure claim.

## Version and adapter compatibility

Use **v0.0.10 / API v1 / exact schema 7**, retaining exact history
`[1, 2, 3, 4, 5, 6, 7]`. Existing v0.0.7/v0.0.8/v0.0.9 schema-7 databases
need **no migration 008/009/010, DDL, or new backfill**.
No dependencies are added; dependency-lock changes are project-version metadata
only. Stop/drain old APIs, workers, adapters, and hook launches before starting
matching versions; equal schema does not establish mixed-version/downgrade support.

MCP/hook startup checks require exact service **`0.0.10`**, API **`v1`**,
schema **`7`**. Capture is **not a fifth MCP tool**. Both MCP eras,
`2026-07-28` and `2025-11-25`, retain their existing contracts.
The recall hook stays read-only and never captures automatically.
Optional `[mcp]`/`[hook]` dependencies, genuine dependency isolation, bounded
Native transport, root-origin URL acceptance, fixed-token authentication,
and sanitized error semantics remain unchanged.

Implemented API stage: **`m2-atomic-capture`**. Feature:
**`atomic_structured_capture`**. Exact metadata is
`atomic_capture: {endpoint: "/v1/captures", max_jobs: 1,
recipe_version: "structured-remember-v1", automatic_capture: false}`.
Neither this stage label nor engineering tests completes M2 or qualifies
specific vendor integration, memory quality, or performance.

## Validation status

**Final local and native v0.0.10 results verified 2026-09-17 JST.**
Apple Container and native Docker amd64/arm64 each passed **304 tests, 1 existing
warning**, plus **Ruff, strict mypy (16 source files), genuine core-only/hook-only
installation checks, and all non-root production smokes**:
Japanese/API/worker, both MCP eras, all three hook events, and atomic capture.
Test elapsed: **275.53 s** on Apple Container, **467.75 s** on native amd64,
and **434.40 s** on native arm64.
The final local source matches published implementation
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f).
Both native jobs in
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)
passed; actual logs verified that exact SHA, counts, timings, and all checks,
not just job status. Elapsed time is not a performance benchmark.

Coverage includes rollback faults, source/key deduplication concurrency,
quota, RLS/deletion, and API process restart with an actual worker.
For a fresh fixture, the production smoke passed in all three environments.
It runs after MCP/hooks: Native capture → pending
job → actual worker CLI `--once` → episode/assertion recall → same capture
replay → source purge → job GET `404` and capture replay `404`.
All three final runs include actual HTTP 201 response loss after commit, same-key
recovery of the exact pair with one publication, and continued replay of the
original failed capture job after explicit retry-child creation.
See [v0.0.10 evidence](../STATUS.md#v0010--schema-7); all acceptance gates remain incomplete.

Historical v0.0.9 implementation
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)
passed [CI 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488).
Final v0.0.9 documentation
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)
passed [CI 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689).
Both native architectures passed 274 tests in each run; these are **historical
v0.0.9**, not atomic-capture evidence.
See [the preserved evidence](../STATUS.md#v009--schema-7) for exact scope.
