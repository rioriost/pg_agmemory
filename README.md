# pg_agmemory

[日本語](README-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**PostgreSQL-backed agent memory, licensed under MIT.** The public repository
is [`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory); the local checkout directory, Python package,
and service are `pg_agmemory`. Run the commands below from that local checkout.

**Current bounded implementation: v0.0.22/schema 10 explicit batch capture.
Graph follow-up in progress; qualification pending.
Verified v0.0.21 and earlier results below are historical, not v0.0.22 evidence.
Not a completed M0/M1/M2/M3, MVP, or production release.**
Implemented: authenticated observation, explicitly reported structured memory
with same-scope episode evidence, PostgreSQL full-text recall, evidence
explanation, transactional idempotency, and synchronous active-store purge.
Tenant/scope permissions are enforced in both the service and PostgreSQL RLS.
Every mutation commits before its response is sent. Assertion revisions retain
server-controlled system-time history and revision-specific evidence.
Typed checkpoints support restore-to-new-branch envelopes and a durable
tool-effect ledger. Explicit entities and revisioned relation assertions support
bounded, read-only SQL graph traversal, alongside explicitly queued structured
publication through a fixed-principal worker and opt-in, versioned Japanese lexical
search and a local, fixed-identity MCP adapter over the Native API.
The optional vendor-neutral implicit recall hook remains read-only.
The retained v0.0.10 capture atomically commits one episode and one explicitly requested
structured-publication job—not a published assertion, automatic capture,
natural-language synthesis, or a measured retrieval-quality improvement.

Cross-assertion supersession/fact arbitration, provider receipt verification, vendor-specific harness adapters,
automatic enqueue/extraction, general multi-tenant scheduling,
automatic embedding generation, ANN/HNSW, AGE/SQL/PGQ, remote MCP HTTP/SSE/OAuth/delegation,
synchronous/TypeScript SDKs, and postgresem adapters
remain roadmap work. Performance, memory quality, disaster recovery, and
full-erasure acceptance remain unmeasured or unqualified.
Consult [the current contract and limitations](docs/STATUS.md)
before using the service.

## Explicit batch capture

**v0.0.22/schema 10 contract retained; graph follow-up qualification pending.**
Authenticated `POST /v1/captures/batch` requires a caller-owned `Idempotency-Key`.
Closed `CaptureBatch` contains unchanged `episode: Observe` and **1–16**
`memories: list[CapturedMemory]`. Every proposal is explicit, same-scope, and
supported by exactly one literal quote from that episode; no extraction,
provider, or automatic capture. Duplicate normalized model JSON candidates,
including trim-equivalent entries, give **422 `invalid_request`**.
Existing single-job `Capture` / `CaptureResult` and `/v1/captures` remain unchanged.

**201 `CaptureBatchResult`** returns the episode `memory_id`, `revision: 1`,
and **1–16 `synthesis_job_ids` in request order**, not published assertions.
All new episode/lexical/job/identity/receipt/audit writes are one atomic admission.
A late invalid quote, quota exhaustion midway, or audit failure rolls back all
new writes, leaving pre-existing rows unchanged. The existing **100 active jobs
per scope** quota applies only to new jobs; 16 is the per-request limit.
Workers publish independently afterward, without atomic completion or execution
order. Poll/query/cancel/retry individual jobs; no batch job/status/cancel API.

Fresh keys for the same source/intents reuse original IDs, even for terminal
jobs, without revival. A fresh key with reordered candidates returns reordered
existing IDs; reordering under the same key is **409**.
Exact-key replay rechecks current write access and **every job's liveness**:
source or one-job purge gives whole-receipt **404**, not partial replay or
resurrection. Source purge keeps the existing dependent-job/assertion closure.

Async `capture_batch(CaptureBatch, *, idempotency_key) -> CaptureBatchResult`
uses `mutation=True` and expects 201. Preserve the same key/body after uncertainty;
no retry, splitting, or replacement keys are automatic. Request/response bounds
remain **256 KiB / 2 MiB**: 16 individually valid large candidates can still
exceed the Native body limit and receive **413**.
Native/SDK has **30 resource methods**; four MCP tools and the closed hook are unchanged.
Stage `m2-batch-capture` adds `atomic_batch_structured_capture` and
`atomic_batch_capture` metadata, without changing single-job `atomic_capture`.
Schema 10 has no migration, dependency, backend, or provider change.
See [the contract](docs/STATUS.md#explicit-batch-capture),
[the async example](docs/operations/README.md#explicit-batch-capture),
[ADR 0022](docs/adr/0022-batch-capture.md), and
[qualification evidence](docs/STATUS.md#v0022--schema-10).

## Exact entity query and pagination

**v0.0.22/schema 10 contract retained; graph follow-up qualification pending.**
Authenticated read-only `POST /v1/entities/query` requires no `Idempotency-Key`.
Closed `QueryEntities` accepts distinct `scope_ids` (1–32 UUIDs), nullable
`entity_type` (one of the existing eight `EntityType` values), nullable
`canonical_label` (`ShortText`, 1–256 characters after existing whitespace
stripping), strict integer `max_items` (1–100, default 20), and nullable `before`
(`EntityCursor`). Filters and `before` default to null.
The closed cursor requires aware `recorded_at` and UUID `memory_id`.
Invalid fields, duplicates, empty labels, or bounds give **422 `invalid_request`**.

Type and label filters combine with **AND before LIMIT**. Label equality uses
**exact, case-sensitive `C` collation** after normal stripping; omitted/null
filters leave all currently readable candidates eligible in the requested scopes.
There is no alias, fuzzy/substring/wildcard/Unicode-normalization matching,
embedding search, merge, or automatic identity choice.
Same-label entities remain distinct IDs across pages. Inspect evidence using
unchanged `GET /v1/entities/{memory_id}` / SDK `get_entity`, then explicitly
choose graph seeds; matching a label does not prove identity.

Unlike owned-job query, **entity query has no ownership filter**: it includes
readable shared-scope entities under current tenant/scope/source RLS.
Unknown/private scopes contribute nothing; no matches gives **200** with
`entities: []` and `next_cursor: null`, without hidden reasons or total counts.
Selected items' visible evidence count through `entity_evidence JOIN episode`
must equal stored `reference_count`, or the **whole page** fails with
**409 `entity_invalidated`**. No silent skip or partial success; SQL fetches no quotes.
Every page uses the current tenant response-delivery/drain barrier.

Order is `recorded_at DESC, memory_id DESC`, where `recorded_at` is the entity
object's `created_at`, with exclusive
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`.
Fetch `max_items + 1`; only overflow emits `next_cursor` from the **last returned
item**, never the lookahead. The unsigned cursor is position, not authority,
snapshot, receipt, or retained object. It need not name an existing object;
old/deleted/forged positions only narrow current authorized rows.
Newer entities above the boundary require an explicit restart without `before`.

Exactly **200 `EntityPage`** returns at most 100 existing `EntitySummary` items
(`memory_id`, revision 1, `scope_id`, `entity_type`, `canonical_label`, `recorded_at`),
`next_cursor`, and `consistency` (`access_epoch`, `deletion_epoch`).
There are no evidence quotes, source IDs, or total counts; known-ID `EntityDetail`
is unchanged. Labels are still human text, not trusted instructions or current truth.
Async SDK `query_entities(QueryEntities) -> EntityPage` uses `mutation=False`,
normal **256 KiB request / 2 MiB response** bounds, read-only
`outcome_unknown: false`, and no automatic paging/retry or provider calls.
Native/SDK has **30 resource methods**; MCP's four tools and the closed hook are
unchanged. Schema 10 has no migration, dependency/provider, or AGE change.
See [the contract](docs/STATUS.md#exact-entity-query-and-pagination),
[paging example](docs/operations/README.md#exact-entity-query-and-pagination),
and [ADR 0021](docs/adr/0021-entity-query.md).

## Assertion metadata history

**v0.0.22/schema 10 contract retained; graph follow-up qualification pending.**
Authenticated read-only `POST /v1/assertions/history` requires no `Idempotency-Key`.
Closed `AssertionHistory` accepts only required UUID `memory_id`, strict integer
`max_items` (1–100, default 20), and nullable strict integer `before_revision`
(1–1001, default null). Invalid fields/values give **422 `invalid_request`**.
It covers currently readable ordinary assertions and canonical relation assertions,
not other object kinds. Missing, private, or wrong-kind objects give generic
**404 `not_found`**. There is no `as_of`, `known_at`, or historical-ACL selector.

Revisions descend by ordinal, exclusively `revision < before_revision`.
Omitted/null starts newest; `before_revision: 1` gives an empty page,
while `1001` includes a current head up to the existing revision limit of 1000.
Fetch `max_items + 1`, return at most `max_items`, and emit `next_before_revision`
only on overflow, from the **last returned ordinal**, not the lookahead row.
The position is not authority, a snapshot, receipt, or retained cursor.
Each page rechecks current ACL/source/deletion visibility and uses the tenant
response-drain barrier. New revisions between pages require an explicit restart;
a former head's `known_until` may close. `current_revision` is neither a CAS
reservation nor proof that an uncertain write committed.

Exactly **200 `AssertionHistoryPage`** returns `memory_id`, `scope_id`, `subject`,
`predicate`, `current_revision`, at most 100 `revisions`, `next_before_revision`,
and `consistency` (`access_epoch`, `deletion_epoch`).
Each revision contains its ordinal, nullable `valid_from`/`valid_to`,
`recorded_at` (system-time lower bound), nullable `known_until` (upper bound),
nullable `correction_reason`, `epistemic_status: "reported"`, up to 32 exact
episode `evidence_refs` at revision 1, and `relation` with `source_entity`/
`target_entity` or null. Bounded SQL does not fetch full values or evidence quotes.
**Metadata is not content-free:** subject, predicate, and correction reason are
human text. Treat returned data as evidence, not instructions or current truth.
For full content use unchanged `Explain` with the exact `memory_id` and revision;
omitting revision still selects **1, not latest**.

Missing/gapped selected revision metadata or no readable evidence fails the
**whole page** with **409 `assertion_invalidated`**; missing relation endpoints
give **409 `relation_invalidated`**. No partial success, skip, or fallback.
Async SDK `get_assertion_history(AssertionHistory) -> AssertionHistoryPage`
uses `mutation=False`, normal **256 KiB request / 2 MiB response** bounds,
and read-only `outcome_unknown: false`. No automatic paging/retry, mutation,
cache, provider call, watch, or retention object is added.
The Native/SDK surface has **30 resource methods**; MCP's four tools and the
closed hook are unchanged, with no history tool or field. Schema 10 is unchanged.
See [the contract](docs/STATUS.md#assertion-metadata-history),
[paging example](docs/operations/README.md#assertion-metadata-history),
and [ADR 0020](docs/adr/0020-assertion-history.md).

## Owned-job query and pagination

**v0.0.22/schema 10 contract retained; graph follow-up qualification pending.**
Authenticated read-only `POST /v1/jobs/query` requires no `Idempotency-Key`.
Closed `QueryJobs` accepts distinct `scope_ids` (1–32 UUIDs), distinct `states`
(at most five; `[]`/omitted means all), strict integer `max_items` (1–100,
default 20), and `before: JobCursor | None` (default null).
`JobState` retains exactly `pending`, `running`, `succeeded`, `failed`, `cancelled`.
A closed `JobCursor` requires an aware `created_at` timestamp and UUID `job_id`.
Invalid fields/values give **422 `invalid_request`**; no owner/principal/tenant,
kind, payload query, offset, watch, or `after` selector is accepted.

Selection is **current-caller-owned jobs only**, within the tenant, requested
scopes, current RLS/source/deletion visibility, and optional state filter.
This is deliberately narrower than known-ID GET: readable same-scope jobs owned
by another principal remain GET-readable, but are never discovered by this query,
even with scope-admin permission. Unknown or unreadable scopes contribute nothing.
No matches gives **200** with `jobs: []` and `next_cursor: null`, without hidden
reasons or totals.

Order is `created_at DESC, id DESC`; `before` applies an exclusive
`(created_at, id) < (before.created_at, before.job_id)` boundary.
The query fetches `max_items + 1`; only overflow produces `next_cursor`, from
the **last returned job**, never the lookahead row. At most `max_items` jobs return.
The unsigned cursor is a transparent position, not authority, a receipt,
snapshot, cache, or retention object; its job need not exist. Forged/stale/deleted
cursor positions still operate only within current authorized, owned rows.

Success is typed **200 `JobPage`**: `jobs` of `ListedJob`, `next_cursor`, and current
`Consistency` (`access_epoch`, `deletion_epoch`).
The SDK also validates the response-model limit of 100 jobs.
`ListedJob` adds `scope_id` to full existing `JobDetail`; GET's shape is unchanged.
References, state/timing, safe errors, retry parent, and original result revision 1
are returned, not stored payload, evidence quotes, lease token, or intent digest.
Every selected page item passes `Jobs.get` reference-count/result-liveness checks;
an actual invalid selected job fails the **whole page** with existing 404/409,
never a silent skip or partial success.

Each page uses current permissions and the tenant response-drain barrier, not a
cross-page snapshot. State transitions preserve original `created_at`; changing
state/access/deletion can change membership between pages. Newer jobs above an
old cursor need an explicit restart without `before`.
SDK `query_jobs(QueryJobs) -> JobPage` is async, uses `mutation=False` and normal
**256 KiB request / 2 MiB response** bounds, with read-only `outcome_unknown: false`.
There is no automatic pagination/retry, claim, cancellation, worker/provider call,
or state change. Native/SDK now has **30 resource methods**; MCP's four tools and
the hook are unchanged, with no job tool.
See [the contract](docs/STATUS.md#owned-job-query-and-pagination),
[paging example](docs/operations/README.md#owned-job-query-and-pagination),
and [ADR 0019](docs/adr/0019-job-query.md).

## Checkpoint-head lookup

**Retained checkpoint-head contract; v0.0.22 graph follow-up qualification pending.**
Authenticated `POST /v1/checkpoints/head` is read-only and requires no
`Idempotency-Key`. Its closed `CheckpointBranch` body contains exactly three
required UUIDs: `scope_id`, `run_id`, and `branch_id`. It selects only that exact,
currently readable scope/run/branch, without identity override, `expected_head`,
harness selectors, `as_of`, cross-branch latest selection, or history listing.

The lookup uses the existing API tenant session-lock/drain barrier and SQL
`SELECT` without `FOR UPDATE`. It creates no branch/run and writes no state,
audit event, or idempotency receipt.
Unknown, private, wrong-scope, cross-tenant, or non-invalidated empty branches
return generic **404 `not_found`**, not IDs/content or empty success.
Source `forget` marks affected branches `invalidated=true` and deletes canonical
checkpoint/reference payloads, but retains opaque `head_id` and `sequence`,
`memory.object` anchors, and tombstones. Lookup checks invalidation first:
a readable invalidated branch gives **409 `checkpoint_invalidated`** and never
returns the retained head ID. Revoked access instead hides the branch with 404.
There is **no ancestor, sibling, or default-branch fallback**.

Success is exactly **200 `CheckpointEnvelope`**, reusing existing load/envelope
checks and returning saved state/HMAC/references/epochs plus current epochs,
`tool_effects`, `requires_reconciliation`, `untracked_effects`, `resume_allowed`,
and `automatic_reexecution: false`.
Run `effects_invalidated`, HMAC/state/reference failures, and invisible heads
retain existing rejection behavior. Loaded scope/run/branch/sequence must match
the selected pointer or fail with 409.

Stable branch IDs can recover the current head when its checkpoint ID is lost,
but “latest” means a **point-in-time pointer**, not a watch, reservation, or
successor guarantee. `CreateCheckpoint.expected_head` remains mandatory caller
CAS; another writer can advance it after the read.
Head lookup **does not prove an uncertain write committed**: retry that mutation's
original key/body for its receipt, then inspect the head. Do not automatically
retry with a new head/key, restore, transition effects, execute, or approve.
GET by checkpoint ID can still load a live surviving ancestor under existing
checks, but is not “latest”; restored forks have independent branch heads.

SDK `get_checkpoint_head(CheckpointBranch) -> CheckpointEnvelope` is async and
read-only (`mutation=False`), with the normal **256 KiB request / 2 MiB response**
bounds, sanitized errors, `outcome_unknown: false`, and no automatic retry.
With batch capture, the current Native/SDK surface is **30 methods**; MCP's **four tools** and the
hook are unchanged, with no checkpoint tool. No SQL migration from v20 or
dependency/provider/artifact-pin change is added.
See [the contract](docs/STATUS.md#checkpoint-head-lookup),
[the practical lookup and upgrade](docs/operations/README.md#checkpoint-head-lookup),
and [ADR 0018](docs/adr/0018-checkpoint-head.md).

## Exact structured recall filters

**Retained recall-filter contract; v0.0.22 graph follow-up qualification pending.**
Existing Native `POST /v1/recall`, typed SDK `recall`, and MCP `memory_recall`
accept `Recall.filters: RecallFilters | None = None`. The closed nested model has
only nullable `kind` (`"episode"` or `"assertion"`), `subject` (`ShortText`,
1–256 characters after existing whitespace trimming), and `predicate`
(`^[a-z][a-z0-9_]{0,63}$`). Fields default to null. Unknown fields, invalid values,
or `kind: "episode"` with non-null subject/predicate give **422 `invalid_request`**.
Omission, null, `{}`, and all-null fields preserve baseline results in all three
retrieval modes.

Non-null fields combine with **AND**. Subject/predicate imply assertions;
`kind: "assertion"` includes relation assertions, while `"episode"` excludes
all assertions. Subject/predicate use **exact, case-sensitive `C`-collation
equality** after normal contract trimming: no substring/FTS matching, Unicode
normalization, fuzzy matching, aliases, or entity resolution.
Empty-query lexical recall browses the filtered candidates. Filters do not
bypass a nonempty query's normal lexical matching.

Filters apply **inside the shared materialized candidates, before lexical,
vector, or hybrid ranking, coverage, and required-reference eligibility**.
Ranking and lexical/vector incompleteness use the filtered eligible universe;
`coverage.jobs_pending` intentionally remains a requested-scope signal, not a
structured job match. Frozen `as_of`/`known_at`, request scopes, current RLS,
evidence visibility, and deletion gates remain.
Required refs keep their separate lexical-only keyword bypass and request order,
but must match filters too: any mismatch gives whole-request **404 `not_found`**,
without IDs or partial context. Existing byte-budget/error behavior is unchanged.

There are still **30 Native/SDK resource methods and four MCP tools**, with no
new safe error code. Hook input rejects `filters`; its internal default is
`None`, preserving trusted startup boundaries. No SQL migration from v20,
dependency/provider/index change, persisted priority, or cache is added.
Filters express caller selection, not trusted instructions or verified truth.
See [the contract](docs/STATUS.md#exact-structured-recall-filters),
[examples and upgrade](docs/operations/README.md#exact-structured-recall-filters),
and [ADR 0017](docs/adr/0017-recall-filters.md).

## Required-context recall

**Retained required-context contract; v0.0.22 graph follow-up qualification pending.**
Existing Native `POST /v1/recall`, SDK `recall`, and MCP `memory_recall` accept
`Recall.required_memory_refs`: omitted or `[]` by default, at most **16**
`MemoryReference` entries. Each selects a UUID and exact revision **1–1000**;
omitted revision is **1, not latest**. IDs must be unique even across revisions,
and the count must fit `max_items`. Nonempty references require
`retrieval_mode: "lexical"`; explicit and implicit recall both work within the
existing limits, including implicit mode's **2,000-byte** cap.

Required items use the same currently authorized episode/assertion candidates,
request scopes, and frozen `as_of`/`known_at` as ordinary recall.
They bypass keyword matching and the ranking cutoff, **not ACL, scope, time,
or structured recall filters**. Any missing, unreadable, purged, wrong-kind, or ineligible exact
revision fails the whole request with generic **404 `not_found`**.
There is no latest/revision fallback or missing-reference disclosure.

The complete required prefix comes first in **request order**, then ordinary
lexical-ranked optional items without duplicate IDs. All items count toward
`max_items` and the entire compact `ContextPack` UTF-8 byte budget.
If any whole required item cannot fit, **422 `budget_exhausted`** returns no
partial context; optional items retain greedy whole-item omission and
`coverage.truncated`. Omitted/empty references preserve existing ordering,
packing, and all three retrieval modes within the selected structured filters.

Both `simple-v1` and `ja-janome-0.5.0-v1` support exact references, including
canonical items whose Japanese projection is missing; `lexical_incomplete`
still reports missing coverage. This is not index repair or automatic detection
of essential constraints. A caller-selected reference is **not policy authority
or verified approval**; memory remains evidence, not trusted instructions.
There are still **30 Native/SDK resource methods and four MCP tools**.
The hook rejects this input field and always constructs recall with empty
references, so it adds no host pinning. No write, idempotency, persistent priority,
cache, inference, provider call, or schema migration is added.
See [the contract](docs/STATUS.md#required-context-recall),
[examples and upgrade](docs/operations/README.md#required-context-recall),
and [ADR 0016](docs/adr/0016-required-context.md).

## Explicit job cancellation

**Retained job-cancellation contract; v0.0.22 graph follow-up qualification pending.**
`POST /v1/jobs/{job_id}/cancel` requires Native authentication, a caller-retained
`Idempotency-Key`, and exactly `expected_state` (`pending` or `running`) plus
strict integer `expected_attempt` (0–5; running requires at least 1).
Read the job first and explicitly select that **state/attempt CAS**, not a tenant
access epoch or external tool version. Only the authenticated owner with current
scope read/write access and valid visible inputs may cancel; another readable
same-scope principal cannot, even with `admin` permission.

HTTP **200** returns a committed `JobReceipt`; GET confirms terminal `cancelled`.
Pending or running jobs, including expired leases, can be cancelled. The same job
keeps its identity, attempt, source/intent references, retry parent, and creation
time, but clears its stored job payload, lease, and error, with no result.
State change, `job_cancelled` audit, and idempotency receipt commit atomically
under the existing tenant session barrier; no access/deletion epoch advances.
This is not an asynchronous cancellation job, worker kill, provider interruption,
or `forget`. Source episodes, dedup anchors, prepared worker memory, WAL, and
backups are not erased by cancellation.

If cancellation wins, stale worker publication is fenced; if publication wins,
cancel returns `409 job_cancel_conflict` and does not unpublish the result.
Unknown delivery requires the **same key/body** or a fresh GET, never blind new
CAS values. Failed-only retry rejects cancelled jobs; enqueue/capture dedup
returns the cancelled job instead of reviving it. Source purge still invalidates
the job and denies cancellation replay.
The SDK retains `cancel_job`; batch capture brings the current surface to **30 methods**;
MCP's four tools and the read-only hook are unchanged.
See [the full contract](docs/STATUS.md#explicit-job-cancellation),
[operations and schema-10 migration](docs/operations/README.md#explicit-job-cancellation),
and [ADR 0015](docs/adr/0015-job-cancellation.md).

## Runtime readiness

**Retained readiness contract; v0.0.22/schema 10 contract retained; graph follow-up qualification pending.**
`GET /healthz` remains process liveness after successful startup:
`{"status":"ok"}`, without DB calls. Public, unauthenticated `GET /readyz`
returns HTTP **200** with exactly `{"status":"ready"}` or an expected-failure
**503** with exactly `{"status":"not_ready"}`. Both readiness responses carry
`Cache-Control: no-store` and a generated UUID `X-Request-ID`, with no private
details. Supplied authorization is ignored; no tenant/principal is selected.

Each admitted check opens a fresh **runtime** DB connection and reads only the
role/schema/extension contract: no privileged runtime role or application-table
ownership, exact migration history 1–10, and `vector` 0.8.6 in `public`.
Validation sessions are explicitly read-only. No memory payload, tenant lock,
audit/epoch/job/receipt write, migration, retry, cache, or background check is involved.
One check is admitted per API app/process; concurrent probes immediately return
503 without another connection. The **5.0 s active-check budget is not a hard
wall-clock SLA**: cancellation/connection cleanup may add latency.

Readiness is a point-in-time signal, **not proof of writability, complete
authorization, JWT/tokenizer/provider health, capacity, or production readiness**.
A SELECT-only DB may pass. Resource routes do not invoke this probe or acquire
a new permanent readiness gate; existing Native authorization remains enforced.
Use readiness to control traffic, not as dependency-based liveness that creates
restart storms. Configure failure/recovery thresholds and perimeter restrictions/
rate limits; no Kubernetes, Compose, or Docker `HEALTHCHECK` wiring is added.
Readiness adds no SDK/MCP/hook probe method; batch capture
brings the current Native/SDK memory surface to 30.
See [the full contract](docs/STATUS.md#runtime-readiness),
[probe operations](docs/operations/README.md#runtime-readiness),
and [ADR 0014](docs/adr/0014-runtime-readiness.md).

## Scope-access administration

**Retained scope-access contract; v0.0.22 graph follow-up qualification pending.**
The privileged `pg-agmemory scope-access get|set|revoke` CLI manages membership
for existing same-tenant scope/principal UUIDs. It requires
`PGAG_ADMIN_DATABASE_URL`, an RLS-bypassing administrator with the appropriate
SQL privileges, and explicit caller-selected IDs—not JWTs, runtime credentials,
or identity from retrieved text. There is **no HTTP/MCP/SDK admin method**.

`get` returns current membership and the tenant-wide `access_epoch`.
`set` fully replaces permissions and requires an explicit future aware expiry
or `--no-expiry`; `revoke` deletes membership. Both require
`--expected-access-epoch`. Stale CAS fails even if the desired state already
matches. Actual changes atomically update membership, advance the epoch, and
write a privileged-only audit event; reads and no-ops do neither.
Natural expiry is not an epoch change or an in-flight response drain.

The CLI holds the shared **tenant session advisory lock** through commit and
JSON stdout flush. It can cooperate with online same-version API clients;
migration still requires stop/drain. A lost mutation result requires inspection
with `get` and privileged audit before an explicit new CAS operation—no blind
retry or idempotency receipt. It cannot retract delivered context or resurrect
purged data. The audit is not tamper-proof or a DR solution.

`009_scope_access.sql` introduced scope-access audit in schema 9; schema 10 retains it.
PostgreSQL 18.6/pgvector 0.8.6 pinned images and dependency versions stay unchanged.
The current stage is `m2-batch-capture`. See [the full contract](docs/STATUS.md#scope-access-administration),
[get → set → revoke example and migration](docs/operations/README.md#scope-access-administration),
and [ADR 0013](docs/adr/0013-scope-access.md).

## Python SDK

**SDK adds explicit batch capture: 30 methods; v0.0.22 graph follow-up qualification pending.** From the matching checkout:

```bash
python -m pip install '.[sdk]'
```

The optional `pg-agmemory[sdk]` extra adds only **httpx==0.28.1**.
This is the existing core distribution, still including FastAPI, psycopg, and
Janome—not a standalone lightweight SDK package or a claim of PyPI publication.
It adds a PEP 561 `py.typed` marker. HTTPX supplied by `mcp`/`hook` also satisfies
the import dependency; without HTTPX, SDK import raises a static installation
`ImportError`.

Use explicit arguments from trusted configuration, not memory/tool input.
The following environment names are examples, **not automatically read by the SDK**.
The scope must already be provisioned and authorized. This read-only example
does not print private memories, tokens, inputs, or raw error responses.

```python
import asyncio
import os
from uuid import UUID

from pg_agmemory.models import Recall
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


async def main() -> None:
    request = Recall(
        scope_ids=[UUID(os.environ["PGAG_SDK_SCOPE_ID"])],
        query="synthetic fixture",
        purpose="read-only SDK example",
    )
    try:
        async with AsyncMemoryClient(
            os.environ["PGAG_SDK_API_URL"], os.environ["PGAG_SDK_API_TOKEN"]
        ) as memory:
            result = await memory.recall(request)
            if not result.coverage.retrieval_complete:
                print("Recall coverage is incomplete; do not infer absence.")
    except MemoryClientError as exc:
        if exc.error.outcome_unknown:
            print("Outcome unknown: retain the existing key and body; reconcile.")
        else:
            print("Memory request failed; no automatic retry was attempted.")


asyncio.run(main())
```

Configuration errors from `NativeSettings` are sanitized `ValueError`, not
`MemoryClientError`. Request-model construction can separately raise Pydantic
`ValidationError`; do not log its private input details.
SDK call-time validation produces sanitized SDK errors.

`AsyncMemoryClient` validates a fixed HTTPS origin or loopback HTTP origin and
token shape; the server authenticates the token. Context entry owns the HTTP
client and requires authenticated **service 0.0.22 / API v1 / schema 10**
capabilities. Use only inside one context; no re-entry or automatic retries.
Await outstanding tasks, or cancel and await them, **before exiting the context**.
Client close is not a request scheduler/cancellation manager or a DB rollback.
Exit closes connections, **not stored memory**. Scopes only narrow server ACLs.
Returned memory is evidence, not trusted instructions or guaranteed current facts.

The SDK covers all 30 public memory resource methods, including batch capture, entity query, explicit job cancellation,
embedding, jobs, graph, checkpoints, and tool effects; not CLI administration or
worker execution. Every mutation needs a caller-retained keyword-only
`idempotency_key`. Uncertain mutation outcomes—including in-flight cancellation—
require reconciliation with the **same key and body**, never a replacement key
or an assumed rollback. See [the typed method/error contract](docs/STATUS.md#python-sdk),
[operations and upgrade](docs/operations/README.md#python-sdk-operations), and
[ADR 0012](docs/adr/0012-python-sdk.md). SDK calls remain HTTP-only, not administration.

## Container checks

Local development uses **Apple Container**, not Docker Desktop. Install and
start [Apple Container](https://github.com/apple/container), then:

```bash
container system start
./scripts/test-containers.sh
```

Install `jq` on the runner too: the script requires it for **both Apple Container
and Docker**, including disposable smoke configuration.

The script builds locked Python dependencies, runs Ruff, mypy, and unit and
PostgreSQL integration tests, then checks production API HTTP health and runs
the actual `pg-agmemory worker --subject ... --once` in the **non-root production
image**. The worker smoke uses a disposable provisioned principal and runtime-only
credentials, asserts `{"outcome":"idle"}`, and logs `Production worker smoke passed`.
The non-root runtime-image tokenizer smoke also checks `東京都` → `東京` / `都`
and emits `Production Japanese tokenizer smoke passed` on success; this is not
an end-to-end recall or segmentation-quality assessment.
The v0.0.8 runner also executes an actual `pg-agmemory mcp` child in the non-root
production image. It connects to the loopback Native API with a fixed token and
provisioned scope, lists all four tools, and calls recall in **both** modern
`2026-07-28` and legacy `2025-11-25` modes. Japanese/API/worker smokes remain.
It uses isolated disposable PostgreSQL containers and removes only its
own containers/networks. Existing databases and containers are not touched.
Python/PostgreSQL/uv image versions and digests are pinned in the container files.

GitHub Actions executes the same script with Docker on native **linux/amd64**
and **linux/arm64** runners. The historical v0.0.8 step is
`Test containers and smoke-test production API, worker, and MCP`:

```bash
./scripts/test-containers.sh docker
```

No hosted model key or external memory database is required. Container images
and Python dependencies must be downloadable on the first run.
**v0.0.22 graph follow-up is in progress; qualification pending.**
Final-docs revision
[`af91974d091feb276db79baf838c7fa2bab8904f`](https://github.com/rioriost/pg_agmemory/commit/af91974d091feb276db79baf838c7fa2bab8904f)
**failed** [CI 35265233011](https://github.com/rioriost/pg_agmemory/actions/runs/35265233011):
amd64 **772 passed, 1 failed, 631.34 s**, with **503 `QueryCanceled`** at the
existing 100-path auto-plan graph case; arm64 **773 passed, 701.79 s**, with all smokes.
A retained disposable diagnostic shows residual canonical-metadata and endpoint
rescans after `adjacent` materialization; the actual failed CI plan was **not captured**.
The follow-up retains `adjacent` materialization and separately materializes
scope/predicate-filtered assertions, time-filtered revisions, and distinct
authorized/evidence-valid endpoints. Precomputed ID arrays are intended to
prevent reversed semijoins and repeated protected canonical scans.
RLS, scope/time/evidence checks, ordering, limits, and the **5000 ms** timeout
remain unchanged. Planner-mode and scan-loop regression checks are being extended;
no follow-up fix revision or passing qualification results are recorded yet.
This is not a performance benchmark or production qualification.

**Initial v22 implementation evidence, not qualification of the follow-up:**
The full Apple Container `./scripts/test-containers.sh` completed with
**773 passed, 1 existing warning, 447.40 s**. Implementation
[`75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe`](https://github.com/rioriost/pg_agmemory/commit/75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe)
passed [CI 35262682028](https://github.com/rioriost/pg_agmemory/actions/runs/35262682028)
on that exact SHA: native amd64 **773 passed, 621.56 s**;
arm64 **773 passed, 702.01 s**.
Ruff, mypy **19 source files + 1 strict SDK consumer**, core/hook/sdk-only
installations, and all previous production smokes plus batch capture
worker/replay/purge passed in all three environments.
These initial results do not override the later failed docs CI or qualify the follow-up.
See [qualification evidence](docs/STATUS.md#v0022--schema-10).

**Historical v0.0.21 follow-up verified locally and on both native architectures.**
Fix [`956b232f38caeeb7d0421a2d6fd3d8340206bcbc`](https://github.com/rioriost/pg_agmemory/commit/956b232f38caeeb7d0421a2d6fd3d8340206bcbc)
materializes bounded graph adjacency to remove repeated relation-revision scans
reproduced under a forced generic prepared plan, preserving RLS, temporal/scope/
evidence checks, ordering, and limits. The failing CI plan was not captured;
the diagnostic is not a production performance benchmark.
The full Apple Container `./scripts/test-containers.sh` passed with
**730 passed, 1 existing warning, 430.12 s** (**729 retained + 1 generic-plan
regression**), including Ruff, mypy **19 source files + 1 strict SDK consumer**,
core/hook/sdk-only installations, and all production smokes.
[CI 35257534254](https://github.com/rioriost/pg_agmemory/actions/runs/35257534254)
passed on that exact fix SHA: native amd64 **730 passed, 1 warning, 748.37 s**;
arm64 **730 passed, 1 warning, 654.52 s**. Both native runs also passed the same
checks, optional installations, and all production smokes.
This qualifies new code, not a successful retry of the failed docs revision.
Separate final v21 docs
[`8f60790e3309368a340f16a771ad44d285aaafde`](https://github.com/rioriost/pg_agmemory/commit/8f60790e3309368a340f16a771ad44d285aaafde)
passed [CI 35259655219](https://github.com/rioriost/pg_agmemory/actions/runs/35259655219):
native amd64 **730 cases, 727.92 s**, arm64 **730 cases, 628.61 s**;
all checks, optional installations, and production smokes passed.
These final-docs results are separate from the code-fix CI and do not qualify v22.
Version/schema/API and the 29 resource methods are unchanged; no SQL migration,
timeout increase, JIT disable, or RLS weakening.

**Earlier v21 evidence, not qualification of the fix:** initial implementation
[`a34477d7511f22202f0bd981772f51408634a9af`](https://github.com/rioriost/pg_agmemory/commit/a34477d7511f22202f0bd981772f51408634a9af)
passed [CI 35252290223](https://github.com/rioriost/pg_agmemory/actions/runs/35252290223)
on that exact SHA: local **729 passed, 1 existing warning, 390.27 s**;
native amd64 **729 passed, 764.95 s**, arm64 **729 passed, 614.98 s**;
all checks/installations/smokes passed.
Later docs revision
[`4d97e92ec08d845ebd2c969819a999e9f0fd6f83`](https://github.com/rioriost/pg_agmemory/commit/4d97e92ec08d845ebd2c969819a999e9f0fd6f83)
had a **failed** [final-docs CI 35254318489](https://github.com/rioriost/pg_agmemory/actions/runs/35254318489):
amd64 **728 passed, 1 failed, 549.51 s**, with `QueryCanceled` / statement timeout
at the existing 100-path graph limit test; arm64 **729 passed, 626.14 s**.
See [qualification evidence](docs/STATUS.md#v0021--schema-10).

**Historical v0.0.20 implementation verified locally and on both native architectures.**
The full Apple Container `./scripts/test-containers.sh` completed with
**695 passed, 1 existing warning, 359.74 s**. Implementation
[`6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97`](https://github.com/rioriost/pg_agmemory/commit/6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97)
passed [CI 35247519977](https://github.com/rioriost/pg_agmemory/actions/runs/35247519977)
on that exact SHA: native amd64 **695 passed, 527.99 s**; arm64
**695 passed, 615.07 s**. Ruff, mypy **19 source files + 1 strict SDK consumer**,
core/hook/sdk-only installations, and all previous production smokes plus
assertion history passed in all three environments.
Separate final v0.0.20 docs
[`c8977088d92f060c1b9f2594db6300f79cea3963`](https://github.com/rioriost/pg_agmemory/commit/c8977088d92f060c1b9f2594db6300f79cea3963)
passed [CI 35249560753](https://github.com/rioriost/pg_agmemory/actions/runs/35249560753):
each native architecture **695 cases**, **660.52 s amd64 / 642.26 s arm64**,
with all previous Ruff/mypy **19 + 1**, optional installs, and production smokes.
These docs timings are separate from implementation CI 35247519977;
neither run qualifies v0.0.21. See [historical evidence](docs/STATUS.md#v0020--schema-10).

**Historical v0.0.19 implementation verified locally and on both native architectures.**
Apple Container `./scripts/test-containers.sh` exited **0** with **663 passed,
1 existing warning, 366.14 s (6:06)**. Implementation
[`e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf`](https://github.com/rioriost/pg_agmemory/commit/e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf)
passed [CI 35242118110](https://github.com/rioriost/pg_agmemory/actions/runs/35242118110)
on that exact SHA. Actual native logs verified **663 passed, 1 warning** each:
**638.06 s amd64 / 586.58 s arm64**.
Ruff, strict mypy **19 source files + 1 SDK consumer**, genuine core/hook/sdk-only
installs, and all non-root production smokes, including owned-job query, passed
in all three environments. Separate final v0.0.19 docs
[`12b7628af6ad19b1073600b27a713e8c52789bec`](https://github.com/rioriost/pg_agmemory/commit/12b7628af6ad19b1073600b27a713e8c52789bec)
passed [CI 35244626331](https://github.com/rioriost/pg_agmemory/actions/runs/35244626331):
each native architecture **663 tests**, **554.26 s amd64 / 601.49 s arm64**,
with all previous Ruff/mypy **19 + 1**, optional installs, and production smokes.
These docs timings are separate from implementation CI 35242118110;
neither run qualifies v0.0.20. See [historical evidence](docs/STATUS.md#v0019--schema-10).

**Historical v0.0.18 implementation verified locally and on both native architectures.** Apple Container
`./scripts/test-containers.sh` exited **0**, with **630 passed, 1 existing warning,
369.39 s (6:09)**. Ruff, strict mypy **19 source files + 1 SDK consumer**, genuine
core/hook/sdk-only installs, and all non-root production smokes passed in all three environments, including
checkpoint-head lookup. Implementation
[`4accd38408a8384b4376f6250d953bb2fa480ec8`](https://github.com/rioriost/pg_agmemory/commit/4accd38408a8384b4376f6250d953bb2fa480ec8)
passed [CI 35235315016](https://github.com/rioriost/pg_agmemory/actions/runs/35235315016)
on both native Docker architectures. Actual logs verified **630 passed, 1 warning**
each: **663.91 s amd64 / 544.14 s arm64**.
Separate final v0.0.18 docs
[`3f01faf56563202c40a00d73cee72830022220b1`](https://github.com/rioriost/pg_agmemory/commit/3f01faf56563202c40a00d73cee72830022220b1)
passed [CI 35237907862](https://github.com/rioriost/pg_agmemory/actions/runs/35237907862):
each native architecture **630 tests, 1 warning**, **644.25 s amd64 / 570.20 s arm64**,
with Ruff, mypy **19 + 1**, optional installs, and all production smokes.
These docs timings are separate from implementation CI 35235315016.
Neither run qualifies v0.0.19; see [historical evidence](docs/STATUS.md#v0018--schema-10).

**Historical v0.0.17 implementation qualification passed locally and on both native architectures.**
Implementation
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894)
passed [CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)
on that exact SHA. Each environment passed **604 tests, 1 existing warning**:
local Apple Container **321.56 s (5:21)**, native Docker amd64 **638.66 s**,
arm64 **544.33 s**. Ruff, strict mypy **19 source files + 1 strict SDK consumer**,
genuine core/hook/sdk-only installs, and all non-root production smokes passed
in all three environments, including structured recall filters.
Separate final v0.0.17 docs
[`5226c81fae7a50a5668109478d5d9f23fc4d7761`](https://github.com/rioriost/pg_agmemory/commit/5226c81fae7a50a5668109478d5d9f23fc4d7761)
passed [CI 35232139680](https://github.com/rioriost/pg_agmemory/actions/runs/35232139680):
native amd64 **465.50 s**, arm64 **557.08 s**, each **604 tests, 1 warning**,
with Ruff, strict mypy **19 source files + 1 consumer**, optional installs, and all production smokes.
These docs timings are separate from implementation CI 35230044140.
Neither run qualifies v0.0.19; see [historical evidence](docs/STATUS.md#v0017--schema-10).

**Historical v0.0.16 implementation qualification passed locally and on both native architectures.**
Implementation
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57)
passed [CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)
on that exact SHA. Each environment passed **566 tests, 1 existing warning**:
local Apple Container **298.15 s (4:58)**, native Docker amd64 **601.73 s**,
arm64 **565.34 s**. Ruff, strict mypy **19 source files + 1 strict SDK consumer**,
genuine core/hook/sdk-only installs, and all non-root production smokes passed
in all three environments, including required-context recall.
Separate final v0.0.16 docs
[`520990d95718b17ae93a7d5259d600e379991df2`](https://github.com/rioriost/pg_agmemory/commit/520990d95718b17ae93a7d5259d600e379991df2)
passed [CI 35226313891](https://github.com/rioriost/pg_agmemory/actions/runs/35226313891):
native amd64 **476.15 s**, arm64 **478.08 s**, each **566 tests, 1 warning**,
with all checks, optional installs, and production smokes.
These docs timings differ from implementation CI 35224189967.
Neither run qualifies v0.0.19; see [historical evidence](docs/STATUS.md#v0016--schema-10).

**Historical v0.0.15 implementation qualification passed locally and on both native architectures.**
Implementation
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)
passed [CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)
on that exact SHA. Each environment passed **535 tests, 1 existing warning**:
local Apple Container **298.29 s (4:58)**, native Docker amd64 **406.88 s**,
arm64 **490.57 s**. Actual native logs verified Ruff, strict mypy
**19 source files + 1 SDK consumer**, genuine optional installs, and all production
smokes, including explicit job cancellation; the same checks passed locally.
Elapsed times are not performance benchmarks. Separate final v0.0.15 docs
[`9d34d5329c9db580e7de0459b743511235ad6fb8`](https://github.com/rioriost/pg_agmemory/commit/9d34d5329c9db580e7de0459b743511235ad6fb8)
passed [CI 35218254940](https://github.com/rioriost/pg_agmemory/actions/runs/35218254940):
native logs verified **535 tests, 1 warning** each, **605.83 s amd64 / 473.57 s arm64**,
Ruff, strict mypy **19 source files + 1 consumer**, optional installs, and all smokes.
These docs timings are distinct from implementation CI 35216770999.
Neither v0.0.15 run validates v0.0.19. See [historical evidence](docs/STATUS.md#v0015--schema-10).

**Historical v0.0.14 final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **495 tests,
1 existing warning**, Ruff, strict mypy (**19 source files + 1 SDK consumer**),
genuine core/hook/sdk-only installs, and all non-root production smokes,
including readiness fault/liveness/recovery. The total is **464 retained +
16 readiness unit + 15 integration tests (31 new)**. Schema 9 is unchanged.
Implementation
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
passed [CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965).
Actual native logs verified that exact SHA, counts, and all checks/smokes.
Test elapsed: **295.67 s local / 385.41 s amd64 / 470.16 s arm64**—not
performance benchmarks. See [validation evidence](docs/STATUS.md#v0014--schema-9).
The separate final v0.0.14 docs
[d4b24f6](https://github.com/rioriost/pg_agmemory/commit/d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68)
passed [CI 35202931424](https://github.com/rioriost/pg_agmemory/actions/runs/35202931424):
actual logs verified 495 tests/1 warning per native architecture and all checks/smokes,
**319.51 s amd64 / 503.56 s arm64**. Neither v0.0.14 run validates v0.0.15.

**Historical v0.0.13 final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **464 tests,
1 existing warning**, Ruff, strict mypy (**19 source files + 1 SDK consumer**),
genuine core/hook/sdk-only install checks, and all non-root production smokes,
including scope-access. The total is **426 retained + 22 scope-admin unit +
16 integration tests (38 new)**. Schema-8→9 rollback/retry and prior migrations passed.
Implementation
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413)
passed [CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448).
Actual native logs verified that exact SHA, counts, and all checks/smokes.
Test elapsed: **297.52 s local / 539.86 s amd64 / 460.73 s arm64**—not
performance benchmarks. See [validation evidence](docs/STATUS.md#v0013--schema-9).
The separate final v0.0.13 docs
[185f433](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)
passed [CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967):
actual logs verified 464 tests/1 warning per native architecture and all checks/smokes,
**499.25 s amd64 / 454.37 s arm64**. Neither v0.0.13 run validates v0.0.14.

**Historical v0.0.12 final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **426 tests,
1 existing warning**, Ruff, strict mypy (**18 source files**), a separate strict typed
consumer (**1 file**), genuine core/hook/sdk wheel-install checks, packaged
`py.typed`, and all non-root production smokes, including the new SDK lifecycle.
This includes **76 SDK unit + 5 SDK integration tests (81 new)** alongside 345 retained tests.
Implementation
[88e1206](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)
passed [CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945).
Actual native logs verified that exact SHA, counts, and all checks.
Test elapsed: **292.76 s local / 484.79 s amd64 / 472.49 s arm64**—not
performance benchmarks. See [validation evidence](docs/STATUS.md#v0012--schema-8).
Final v0.0.12 docs [0e00abd](https://github.com/rioriost/pg_agmemory/commit/0e00abdae930dcc1e2d2015fbf5931a74701fe7d)
also passed [CI 35194141510](https://github.com/rioriost/pg_agmemory/actions/runs/35194141510):
actual logs verified 426 tests per native architecture and all checks/smokes,
**488.20 s amd64 / 454.88 s arm64**. This is a separate docs run, not the
implementation timings above or v0.0.13 validation.

**Historical v0.0.11/schema 8: verified 2026-09-17 JST.** Apple Container and native Docker
amd64/arm64 each passed **345 tests, 1 existing warning**, Ruff, strict mypy
(**17 source files**), core-only/hook-only installation checks, and all non-root
production smokes, including exact/hybrid episode/assertion vector retrieval and purge.
Test elapsed: **283.44 s local**, **404.40 s amd64**, **433.46 s arm64**; these are
not performance benchmarks. Implementation
[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)
passed [CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403).
See [the qualification evidence](docs/STATUS.md#v0011--schema-8).
Final v0.0.11 documentation
[dccd5cb](https://github.com/rioriost/pg_agmemory/commit/dccd5cb5571873515aace8621ce4adb3de250d3a)
also passed [CI 35190495385](https://github.com/rioriost/pg_agmemory/actions/runs/35190495385):
actual native logs verified **345 tests, 1 warning**, Ruff, mypy **17 source files**,
installation checks, and all smokes; **506.38 s amd64 / 460.18 s arm64**.
These are documentation-run observations, separate from the implementation
timings above; neither run validates v0.0.12.

The adopted DB profile is the prebuilt
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`.
Artifact inspection verified **both amd64/arm64 images**: PostgreSQL
**18.6-1.pgdg12+2**, native ELF, and `vector.control` **0.8.6**.
The PostgreSQL version stays 18.6, but **this is a new upstream DB image/profile
with a different base digest**, not the unchanged old library PostgreSQL image.
There is no new DB Dockerfile, source-build, or host-APT workflow in this profile.
Artifact verification is separate from the passing application/migration/CI evidence above.
For historical v0.0.11, Python dependencies stayed unchanged apart from project-version metadata;
raw parameter-bound vector casts need no pgvector Python package.

**Historical v0.0.10/schema 7: final local and native results verified 2026-09-17 JST.**
Apple Container and native Docker amd64/arm64 each passed **304 tests, 1 existing
warning**, plus **Ruff, strict mypy (16 source files), genuine core-only/hook-only
installation checks, and all non-root production smokes**.
These cover Japanese/API/worker, both MCP eras, all three hook events, and the
new atomic **capture → actual worker → recall → replay → purge** workflow.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **275.53 s** |
| Docker, native `linux/amd64` | **467.75 s** |
| Docker, native `linux/arm64` | **434.40 s** |

The final local source matches published implementation
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f).
Both native jobs in
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)
passed; actual logs verified that exact SHA, counts, timings, and checks,
not just job status.
Test elapsed time is not a performance benchmark.
See [v0.0.10 evidence](docs/STATUS.md#v0010--schema-7).
Final v0.0.10 documentation
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)
also passed **304 tests per native architecture** in
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760).
That docs run is distinct from the implementation-run timings above.
Neither run validates v0.0.11/schema 8.

**Historical v0.0.9/schema 7, final results verified 2026-09-17 JST:** all three environments
passed **274 tests, 1 existing warning**, Ruff, strict mypy (**15 source files**),
genuine core-only/hook-only installation checks, and all non-root production
Japanese/API/worker smokes, MCP **`2026-07-28` and `2025-11-25`**, and hook
**`session_start`, `task_switch`, and `after_compaction`**.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **248.29 s** |
| Docker, native `linux/amd64` | **482.21 s** |
| Docker, native `linux/arm64` | **374.33 s** |

The final local source matches published implementation
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050).
Actual logs from both native jobs in
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)
verified that exact SHA and all checks above, not just job status.
Timings are test observations, not performance benchmarks. See
[v0.0.9 validation evidence](docs/STATUS.md#v009--schema-7) for scope;
no original milestone or acceptance gate is completed by these checks.
The final v0.0.9 documentation commit
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)
also passed **274 tests** on both native architectures in
[CI run 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689).
Neither v0.0.9 run validates v0.0.10 atomic capture.

**Historical v0.0.8/schema 7:** implementation
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)
passed **214 tests** (1 existing warning), Ruff, strict mypy (13 source files),
and all production smokes in Apple Container and native Docker amd64/arm64.
Both MCP protocol modes above passed. See
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)
and [validation evidence](docs/STATUS.md#validation-evidence).
The subsequent bilingual documentation commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)
passed **214 tests on both native architectures** in
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509).
Neither v0.0.8 run validates the v0.0.9 hook or shared-client extraction.

**Historical v0.0.7/schema 7 evidence only:** implementation commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473),
Apple Container and native Docker **linux/amd64** and **linux/arm64** each passed
**144 tests** (2 existing warnings), Ruff, strict mypy (12 source files), and all
three non-root production smokes: Japanese tokenizer, API HTTP, and actual CLI
worker `--once` idle execution. Final results were verified **2026-09-17 JST**.
Both CI jobs ran that exact SHA; their actual logs confirm all checks. See
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)
and the [validation evidence](docs/STATUS.md#validation-evidence).
The final bilingual documentation commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)
also passed both native jobs in
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899).
These older runs are not v0.0.8 or v0.0.9 results.

The **historical v0.0.7** final lock retained the existing package-feed registry. All **36 packages'**
versions, dependency metadata, and artifact hashes are byte-for-byte equivalent
to the tested PyPI-resolved lock. Relative to v6, only Janome 0.5.0 was added and
the project version became v0.0.7: no unrelated upgrades or registry migration.
Native CI built that retained-registry lock. The v0.0.8 MCP extra adds dependencies;
the old package count and lock comparison do not describe the new lock.

## Run the API

Use the pinned upstream pgvector DB profile above (PostgreSQL 18.6, pgvector 0.8.6).
The following are application commands to run inside an
image built from `Dockerfile` (the final stage is the runtime image).

1. Run `pg-agmemory migrate` with `PGAG_ADMIN_DATABASE_URL` pointing to the
   intended empty Memory database. Migrations are transactional and rerunnable.
   The administrator must bypass forced RLS (superuser or appropriately
   privileged `BYPASSRLS`) and have the required role/schema/table DDL and
   `btree_gist` installation rights. These are not runtime privileges.
2. Create a dedicated login with `NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime`
   and a securely assigned password. **Do not grant membership in the migration
   owner's role.** Set `PGAG_DATABASE_URL` to this restricted login.
3. Run `pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT` with the admin
   URL to create a private tenant, principal, and scope. Save the returned
   `scope_id`. A subject is unique within the configured issuer.
4. Set `PGAG_JWT_PUBLIC_KEY` to a PEM RSA public key (at least 2048 bits),
   `PGAG_JWT_ISSUER` to the exact issuer, and `PGAG_JWT_AUDIENCE` to this
   service's audience. Tokens must be RS256-signed and contain `sub`, `iss`,
   `aud`, `iat`, and `exp`. Tenant/principal IDs in request bodies are rejected.
5. Run `pg-agmemory serve`. Terminate TLS at a trusted reverse proxy; port 8000
   itself serves HTTP. Do not expose it directly to an untrusted network.

Keep the admin URL and signing private key out of the runtime environment.
There are no built-in credentials, default tokens, or authentication bypasses.
Migration/provisioning/rebuild access is administrative and must never be exposed as a
public endpoint. The runtime process refuses superuser, RLS-bypass, and
table-owner roles at startup.

**v0.0.22 retains schema 10 and adds no migration.** Existing schema-10 databases
use the [application-only upgrade](docs/operations/README.md#schema-10-application-only-upgrade).
Older schemas still require `010_job_cancellation.sql`, introduced in v0.0.15;
follow the [retained migration sequence](docs/operations/README.md#schema-10-job-cancellation-upgrade).
Older schemas also require the retained migration sequence, including
`009_scope_access.sql` for durable admin audit.
Older databases still require the v0.0.11 `008_pgvector.sql` migration. PostgreSQL must provide
`vector` **0.8.6 in `public`**; migration rejects an existing extension with the
wrong version or schema. The prebuilt profile supplies the matching extension.
API, worker, and `migrate` validate it even when schema 10 is already recorded.
Before a required migration, stop/drain **all old/new APIs, workers, adapters,
hook launches, SDK callers, and admin commands**, preserve a backup and current
deletion/ACL records, then migrate offline.
Older databases also apply the retained migrations, including migration 007's
lexical backfill. **There is no embedding backfill or automatic embedding rebuild**.
Only matching v0.0.22 processes may restart; API/worker startup requires exact
history `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` and extension `vector` 0.8.6 in schema `public`.
Older-schema processes are not rolling-compatible with schema 10.
Keep old images stopped; v0.0.1 lacks a schema-compatibility guard.
No rolling coexistence or downgrade is supported. Follow the
[current schema-10 procedure](docs/operations/README.md#schema-10-application-only-upgrade).

With `MEMORY_URL`, `TOKEN`, and the provisioned `SCOPE_ID` in your shell:

```bash
curl --fail-with-body "$MEMORY_URL/v1/observe" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-observation-1' \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"source_namespace\":\"demo\",\
\"source_event_id\":\"contract-1\",\"occurred_at\":\"2026-09-01T00:00:00Z\",\
\"content\":\"ACME contract is Gold\",\"consent_reference\":\"demo-consent\"}"

curl --fail-with-body "$MEMORY_URL/v1/recall" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"scope_ids\":[\"$SCOPE_ID\"],\"query\":\"Gold\",\
\"purpose\":\"demo\",\"token_budget\":2000}"
```

`consent_reference` records the caller's assertion of consent; this initial
service does **not** verify an external consent registry or automatically
redact secrets/PII. Only send approved, already-sanitized data.
Interactive schema documentation is at `/docs`; OpenAPI is at `/openapi.json`.
`/healthz` is process liveness after startup validation, not continuous DB readiness.
See [`/readyz` and its limits](docs/STATUS.md#runtime-readiness) for the bounded runtime check.

## Pgvector retrieval foundation

**Implemented and verified in v0.0.11/schema 8.** This is an experimental,
provider-independent mathematical foundation, not qualified semantic retrieval.
The old lexical default stays unchanged; vectors are explicitly supplied.

- Native read-only `POST /v1/embedding-inputs` takes existing Explain
  `{memory_id, revision}` (default revision **1**, not latest), without an
  idempotency key. It returns authorized canonical text, SHA-256 UTF-8
  `input_digest`, and `input_format: "memory-content-v1"`.
  Episode text is normalized content; assertion text is exactly
  `subject / predicate: value`, including relation display values, without IDs/times.
- Native `POST /v1/embeddings` requires `Idempotency-Key`, the exact digest,
  caller-declared model name/revision, and **768 finite JSON numbers**.
  Fixed space: cosine / `l2-f32-v1`; server normalization uses float64 then
  pgvector float32. No booleans, numeric strings, zero vectors, truncation, or
  dimension coercion. Read/write scope comes from the canonical parent.
- One immutable vector per canonical revision/model namespace; identical
  normalized float32 values/digest deduplicate across HTTP keys. A different
  vector conflicts; replacement requires a new model revision. Maximum **8 model
  versions per canonical revision**, with existing duplicates still allowed at the cap.
  The stored idempotency result is only `{memory_id, revision}`, with no plaintext
  digest/model names/vectors. Full response metadata is rebuilt from currently
  readable canonical input and its projection; HMACs/opaque anchors persist.
  Replay with a live parent but an administrator-removed projection returns
  **409 `embedding_unavailable`**, not a rebuilt projection.
- Recall adds `retrieval_mode: "lexical" | "vector" | "hybrid"` and inline
  `vector_query`. Lexical rejects vectors; vector requires an empty text query;
  hybrid requires nonempty text. No silently ignored query or broad fallback.
  Vector uses exact cosine over materialized, currently authorized/time-eligible
  candidates; hybrid fuses deterministic lexical/vector ranks with **RRF k=60**.
  Omitted `as_of`/`known_at` are frozen once before selection and coverage;
  explicit times are unchanged. UUID breaks actual distance/score ties, not a
  guarantee of bitwise-identical arbitrary float results/rankings on all CPUs.
  There is no ANN/HNSW or neighbor-based scope widening.
- Missing eligible visible projections produce explicit `vector_incomplete`
  coverage; unauthorized items never affect coverage. Ranking metadata is not
  confidence or truth. The whole-JSON UTF-8 context-pack budget remains unchanged.
  Parent purge cascades vectors/digests/model metadata; they are not new memory
  identities or provenance vertices, and there is no standalone model registry.

Default response fields are additive: `MemoryItem.retrieval: null`,
`RecallResult.retrieval_mode: "lexical"`, `embedding_model: null`, and
`coverage.vector_incomplete: false`. Non-null `retrieval` carries `exact_cosine`
or `rrf-60` ranking evidence with nullable rank/distance/fusion fields.
Default lexical semantics remain unchanged, **not the byte-for-byte HTTP JSON shape**.
Capabilities add `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"`.

MCP keeps **four tools**: generated Recall arguments gain vector/hybrid fields,
but no embedding input/upload tool is added. The hook remains **read-only and
lexical-only**, rejecting Native responses with non-lexical `retrieval_mode`,
non-null `embedding_model`/item `retrieval`, or true `coverage.vector_incomplete`.
Capture, Observe, jobs, and workers never generate embeddings.
Do not log canonical input or send it to third parties without explicit approval.
The [synthetic basis-vector example](docs/operations/README.md#synthetic-vector-example)
makes no external model call and is not a production embedding model.
See [the proposed contract](docs/STATUS.md#pgvector-exact-and-hybrid-retrieval)
and [ADR 0011](docs/adr/0011-pgvector-retrieval.md).

## Atomic structured capture

For 1–16 proposals, use the separate [batch route](#explicit-batch-capture);
the single-job contract below remains unchanged.

**Retained capture contract, also verified in v0.0.11; v0.0.10 results are historical.**
Native `POST /v1/captures` requires
`Idempotency-Key` and `{episode: <unchanged Observe>, memory: <one structured intent>}`.
The memory intent contains `subject`, `predicate`, `value`, one `evidence_quote`,
`explicit_intent: true`, and optional aware/null `valid_from`/`valid_to`.
It accepts **no scope, evidence IDs, or identity fields**: scope and the single
episode evidence ID are derived inside the transaction. The 1–4,096-character
quote must occur literally in the normalized episode. Existing Remember field
limits/valid-time rules and reported, uncalibrated semantics remain unchanged.

**HTTP 201** returns `{memory_id: <episode UUID>, revision: 1,
synthesis_job_id: <job UUID>}`. It acknowledges a committed episode plus one
`structured_remember` / `structured-remember-v1` job, **not assertion publication**.
Capture may reuse an existing, even terminal, job: **201 does not guarantee a
fresh or pending job**. These are historical references; GET is authoritative for current status.
Use `GET /v1/jobs/{synthesis_job_id}` and the existing fixed-subject worker for
eventual publication. Existing 100-active-jobs/scope and five-attempt limits,
leases, epoch checks, and publication fencing still apply.
`POST /v1/observe` remains unchanged: `synthesis_job_id: null`, no automatic job.
Explicit `/v1/jobs` and synchronous `/v1/remember` remain unchanged too.

Same key/normalized body returns the same episode/job pair; a changed body with
that key conflicts. New HTTP keys with the same episode/intent/principal deduplicate
the pair. A different explicit intent may create a separate job on a retained
episode; another authorized principal has independent job identity/ownership.
The transaction covers episode/projection, job data/identity, idempotency, and
audit. Transaction failure rolls back new changes, not an episode that already existed independently.

Replay checks **current ACLs and deletion for both IDs**. Purged episode/job/result
dependencies can invalidate the pair with `404`; a purged job identity is not
recreated by a new key. Retrying a failed job is an explicit existing job-retry
operation; capture replay still returns its original job, not a retry child.
This remains true after an explicit retry child has been created.
This is not a permanent seal on a retained source against new, different intent.
IDs are historical references; GET supplies fresh state.

Capture is **not an MCP tool** and the recall hook never captures automatically.
Capture does not generate embeddings, invoke an LLM/provider, extract intent,
automatically synthesize, or qualify semantic truth. The Native HTTP response-drain and host-erasure boundaries
are unchanged. See the [full delta contract](docs/STATUS.md#atomic-structured-capture),
[operator curl example](docs/operations/README.md#atomic-structured-capture-operations),
and [ADR 0010](docs/adr/0010-atomic-capture.md).

## Local stdio MCP

Owned-job query adds no MCP job tool; it is Native/SDK-only.

Checkpoint-head lookup is Native/SDK-only; no checkpoint tool is added to these four bindings.

`memory_recall` also accepts typed `filters`; exact pre-ranking selection and
required-reference intersection follow the [filter contract](#exact-structured-recall-filters).
No new tool or safe error code is added.

`memory_recall` accepts additive `required_memory_refs` in its Native request
wrapper. Required-prefix budget failure returns a safe `budget_exhausted` tool
error, not a successful empty result; the four-tool surface is unchanged.

A plain `pg-agmemory` package installation does **not** install optional `mcp`,
`hook`, or `sdk` dependencies. Select `pg-agmemory[mcp]` for MCP,
`pg-agmemory[hook]` for recall-hook, or `pg-agmemory[sdk]` for the Python SDK.
Repository Docker test/runtime images intentionally include all three extras;
that is **not** the base-package default.

Install the optional `pg-agmemory[mcp]` package extra, or use the repository image,
whose v0.0.22 test and runtime stages retain `mcp`, `hook`, and `sdk` extras.
The MCP extra pins the official **mcp 2.2.0** SDK
and **httpx 0.28.1**. From this checkout, `uv sync --frozen --extra mcp` prepares
the locked environment. A trusted local MCP host launches:

```bash
pg-agmemory mcp
```

Supply **`PGAG_MCP_API_URL`** and **`PGAG_MCP_API_TOKEN`** through trusted startup
configuration, not tool arguments or checked-in host configuration. The URL must
be an HTTPS origin or loopback HTTP origin, with no credentials, path, query, or
fragment. The token is for the **Native API audience**, which the Native API
checks; it is not forwarded MCP caller identity. Startup makes an authenticated
capabilities request and requires API `v1`, service `0.0.22`, and schema `10`.
Configuration/authentication/version failures exit nonzero without secrets.
Restart to refresh the fixed token. `--subject` and `--once` are rejected.

Exactly four tools expose schemas from the Native Pydantic models:

| Tool | Arguments | Native operation |
|---|---|---|
| `memory_recall` | `{request: <Recall body>}` | `POST /v1/recall` |
| `memory_remember` | `{request: <Remember body>, idempotency_key: "..."}` | `POST /v1/remember` |
| `memory_explain` | `{request: <Explain body>}` | `POST /v1/explain` |
| `memory_forget` | `{request: <Forget body>, idempotency_key: "..."}` | `POST /v1/forget` |

Both mutation tools require a caller-owned key of **1–256 visible ASCII
characters** (no whitespace), including forget preview. Keys are not trimmed or
rewritten: exactly 256 characters is allowed, 257 is rejected.
Native forget preview and purge **both return HTTP 202**, unchanged.
After uncertainty, reuse the
**same key and body, even across stdio restarts**. No automatic retries or
generated keys are provided. Transport failures, 5xx, or invalid mutation
responses mean `outcome_unknown`, **not rollback**. Results use
`structuredContent: {result: <Native result>, error: null}`; failures use
`isError: true` and `{result: null, error: {code, retryable, outcome_unknown,
native_status, request_id}}`. Short text does not duplicate evidence.

Remember is explicit structured publication only. Store standalone episodes with
Native `/v1/observe`; use Native `/v1/captures` for explicit episode-plus-job
atomicity. Neither is an MCP tool. UTF-8 byte budgeting (not model tokens), opt-in Japanese
recall, current Native authentication/ACLs, deletion checks, and historical
idempotency references are unchanged. MCP session/request IDs are neither memory
run IDs nor HTTP idempotency keys.

**One adapter per trusted identity; do not share it or expose it over a network.**
There are no per-call headers, identity, or URL overrides, remote MCP HTTP/SSE,
OAuth, or delegation. The adapter is a trusted local Native API client. The
Native response-drain barrier ends at HTTP delivery to that adapter, **not an
atomic barrier through stdio, host UI, or LLM context**. Buffered/already-delivered
context cannot be retracted. The host must discard cached context after forget or
ACL changes; there is no MCP deletion notification or adapter response/semantic cache.
See [the full contract](docs/STATUS.md#local-stdio-mcp),
[startup and recovery](docs/operations/README.md#local-stdio-mcp-operations), and
[ADR 0008](docs/adr/0008-local-mcp.md), including protocol validation limits.
v0.0.11 retains both modern `2026-07-28` and legacy `2025-11-25` protocol
contracts and all MCP semantics. Historical v0.0.9 regression and both protocol
smokes passed locally and on both native Docker architectures.
Historical v0.0.10/v0.0.11 and final local/native v0.0.12 checks passed.

## Implicit recall hook

Owned-job query adds no hook field or job operation.

Checkpoint-head lookup adds no hook field or checkpoint operation.

Hook input rejects `filters` as an unknown field; its internal `Recall.filters`
defaults to `None`. This adds no per-event override of trusted startup settings.

Hook input rejects `required_memory_refs`; its internal `Recall` uses `[]`,
so there is no host pinning. Its optional-only successful-empty
`empty_reason: "budget_exhausted"` is distinct from the required-context
Native/SDK/MCP `422 budget_exhausted` error.

**Retained read-only, lexical-only hook, verified in v0.0.11.** Historical v0.0.9 local/native
checks passed. Install optional
`pg-agmemory[hook]` (`uv sync --frozen --extra hook` in this checkout).
It pins **httpx 0.28.1, not the MCP SDK**; Docker test/runtime include `mcp`, `hook`, and `sdk`.
`pg-agmemory recall-hook` is a one-shot, vendor-neutral local Native HTTP client.
It does not register itself with any host and does **not** claim Copilot, Claude,
or Codex integration. No external model invocation or database credentials are needed.

Send exactly one UTF-8 JSON document on stdin, then **close stdin**:

```json
{"event":"session_start","query":""}
```

`event` is exactly `session_start`, `task_switch`, or `after_compaction`.
`query` is required, at most 4,096 Unicode characters; empty means canonical
browsing within configured scopes. Retrieval intent comes only from the JSON
`query`; `event` is a lifecycle label, not query text. Stdin is capped at 32,768 bytes. Extra fields,
including identity, scopes, purpose, mode, budgets, URLs, headers, tools, and times,
are forbidden. Event text cannot authorize access. `--subject`/`--once` are rejected.

Only **trusted startup environment**, never prompts or event data, sets
required `PGAG_HOOK_API_URL`, required fixed Native-audience `PGAG_HOOK_API_TOKEN`, required
`PGAG_HOOK_SCOPE_IDS` (JSON array of 1–32 unique UUIDs), and recall settings.
Defaults are purpose `implicit_context`, budget **2,000 UTF-8 bytes, not model
tokens** (64–2,000), max items 20 (1–20), search profile `simple-v1` (Japanese
`ja-janome-0.5.0-v1` opt-in), and timeout 2.0 seconds (finite 0.1–20).
Purpose is 1–256 characters. See the [complete environment table](docs/STATUS.md#implicit-recall-hook).
HTTPS origins or loopback HTTP only; no URL credentials, application path, query,
or fragment; a root `/` is accepted. All URL/token/scope-ID settings are required;
an absent URL is `invalid_hook_configuration`, not a default destination.
Shared `NativeSettings` also uses `httpx.URL` to reject control characters and
invalid IDNA before transport. Redirects/proxy environment are disabled and TLS is verified.

Each invocation freshly checks authenticated capabilities for exact
**service `0.0.22` / API `v1` / schema `10`**, then posts Native recall with
`mode: "implicit"` and Native current-time defaults. The deadline covers **both
HTTP steps together**, excluding process startup, stdin input/waiting, and output.
It is not an LLM latency SLO.
The harness needs a separate subprocess timeout. Shared bounded Native HTTP
transport preserves MCP invariants; request/response caps are 256 KiB/2 MiB.
`context_pack.byte_count` counts the **entire compact JSON-serialized context
pack**, including metadata/citations, **not just text**: UTF-8 with
`ensure_ascii=False`, `separators=(",", ":")`. The hook verifies that count
and the configured budget. Returned items cannot exceed `max_items`, and
the returned search profile must match configuration. Mismatches fail, without
broad fallback.
No writes, capture, queue, LLM provider, cache, retry, or idempotency key is added.

Validated hook runtime outcomes emit one JSON result plus newline on stdout;
stderr diagnostics are sanitized:
success is `{status: "ok", event, result: <full Native RecallResult>, error: null}`.
Failure is `{status: "error", event: <validated event or null>, result: null,
error: {code, retryable, outcome_unknown: false, native_status, request_id}}`.
Native status and request UUID are nullable; see the
[confirmed error-code table](docs/STATUS.md#bounds-results-and-failure-handling).
Exit **0** includes genuine Native `not_found`/`budget_exhausted`/`index_incomplete`
empty results; **2** is invalid configuration/input; **1** is Native/network/version/
protocol failure. **Failed retrieval is never empty success.**
An empty pack itself costs roughly 192 bytes: a valid requested budget of 64
may yield explicit Native **422 `budget_too_small` / hook exit 1**.
`budget_exhausted` is instead **200 / exit 0** when the pack fits but candidates
do not. Missing-index `index_incomplete` can also be 200/exit 0 with incomplete coverage.
Recall silently filters unauthorized scopes or revoked membership, returning
the authorized subset or no items/`not_found`, **not a scope-existence 404**.
Token authentication failures remain explicit **401 / hook exit 1**.
These are unchanged Native semantics.
**Invocation exception:** rejected CLI flags or a missing `hook` extra use
argparse stderr and exit **2 without a JSON envelope**. Host timeout/kill can
also prevent an envelope; the harness must handle non-JSON/invalid output.

The host must surface errors/coverage and explicitly pause or continue without memory.
Keep returned memory as **untrusted evidence, never instructions or policy**;
do not log queries or copy them into errors. The Native session advisory barrier
ends at HTTP delivery to the trusted hook—not atomically through buffers, stdout,
or host context. There is no retraction, deletion notification, or host-erasure proof.
After forget/ACL changes, discard previous context and run a fresh hook.
Hooks never expand permissions. See the executable
[vendor-neutral Python harness example](docs/operations/README.md#vendor-neutral-python-harness-example)
and [ADR 0009](docs/adr/0009-implicit-recall-hook.md).

## Opt-in Japanese lexical recall

`POST /v1/recall` defaults to `search_profile: "simple-v1"`, preserving PostgreSQL
`simple`/`plainto_tsquery`/`ts_rank_cd`. Select `"ja-janome-0.5.0-v1"` explicitly
for Japanese-script surface/wakati segmentation; the response echoes the profile.
Janome **0.5.0** uses bundled **mecab-ipadic-2.7.0-20070801** with Janome additions.
Only matching Japanese-script runs are segmented; ASCII identifiers/English pass
through the segmenter unchanged. No Unicode/width normalization, lemma/stemming,
synonyms, or segmentation-quality guarantee is provided. Han-script handling
also affects Chinese characters; Chinese recall is not qualified.

Janome is lazy-imported only for Japanese-script runs. Its input-prefix cache is
disabled (`max_cached_word_len=0`); only packaged dictionary-resource caches are
retained. Test/runtime container builds sequentially precompile **only static
Janome package bytecode**, not user text or a memory index/cache. Cold host
installations without that precompilation can have much larger initialization
peaks; deployment resource sizing remains unqualified.

`tokenizer_id: "utf8-bytes-v1"` still budgets context bytes, not Japanese tokens.
Scope, time, evidence, ACL, deletion, and byte limits remain in force.
Missing authorized time-eligible Japanese projections set
`coverage.lexical_incomplete: true` and `coverage.retrieval_complete: false`, without
silently falling back. No candidates with missing projections gives
`empty_reason: "index_incomplete"`; existing budget exhaustion remains distinct.
In lexical mode, an empty query still browses canonical items, even with an incomplete-index flag.
The flag measures projection coverage, not query relevance or queued work.

Writes index episodes and every assertion revision atomically, including typed
relations and job publication. Lexical runtime grants are `SELECT`/`INSERT` only:
no `UPDATE` or direct `DELETE`. Canonical parent purge removes derived rows by FK
cascade in the same barrier, without child DELETE grants; they are not separate
memories. Offline `pg-agmemory reindex-lexical` rebuilds **all tenants in the
selected database** using `PGAG_ADMIN_DATABASE_URL`; `--subject` is rejected,
not a scope filter, and `--once` is worker-only. Stop/drain APIs and workers,
back up, rebuild lexical projections, then restart matching v0.0.22 processes only.
This does not populate or rebuild embeddings. There is no automatic
repair worker, external model/provider, or file-based memory index.
See [the contract](docs/STATUS.md#japanese-lexical-profile),
[maintenance](docs/operations/README.md#lexical-profile-and-reindex-operations),
and [ADR 0007](docs/adr/0007-japanese-fts.md).

## Durable structured-publication jobs

`POST /v1/jobs` requires `Idempotency-Key` and
`{kind: "structured_remember", memory: <unchanged Remember request>}`.
Supply explicit intent and literal same-scope episode evidence under current
read/write access. `202` returns `{job_id, kind, recipe_version:
"structured-remember-v1"}`: a job reference, **not completed publication**.
Identical canonical intent/recipe within one principal/scope deduplicates across
HTTP keys; evidence order is canonicalized for job dedup, but the same HTTP key
still requires the same normalized request.

`GET /v1/jobs/{job_id}` returns safe state, attempts, timing, input references,
and the original revision-1 result reference—not the request, lease token, or
owner principal. Each scope allows 100 pending/running jobs, each with at most
5 attempts. Terminal jobs erase request JSON. The owner can explicitly retry a
failed job with `POST /v1/jobs/{job_id}/retry`, the full original body, and a key.
The owner can [cancel pending/running work](#explicit-job-cancellation) with a
state/attempt CAS. Cancelled jobs are terminal, cannot be retried, and are excluded
from the active-job cap and `jobs_pending`; same-intent enqueue/capture still dedups to them.
Repeated retries of that parent reuse one child; retry the child if it later fails.

Run with restricted `PGAG_DATABASE_URL` credentials after provisioning the subject:

```bash
pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT --once
```

Omit `--once` for continuous operation. The worker claims only that principal's
jobs. `--subject` is trusted deployment configuration, not HTTP impersonation;
the worker needs no JWT keys or admin URL. Committed claims, expiring tokenized
leases, current authorization/epoch rechecks, and atomic assertion/job publication
fence stale attempts. This is at-least-once processing with at most one committed
result per job, not external exactly-once execution.
Assertion recorded/system time starts at worker publication, not enqueue.
Worker stdout/logged outcome references are historical, not current read
authorization; job GET and explain recheck current access and deletion.

`observe` still enqueues nothing (`synthesis_job_id: null`); synchronous
`remember` is unchanged. Recall reports readable queued work through
`coverage.jobs_pending`, retaining `synthesis_pending: false` and `graph_used: false`.
Jobs are not recall/explain items or checkpoint/effect reference kinds.
Source/result purge removes dependent jobs and retry descendants and fences
running publishers. **Deleting only a job or retry chain does not delete its
already-published assertion or source episodes**; purge the result/source explicitly
to erase that fact. See [the contract](docs/STATUS.md#durable-jobs),
[worker operations](docs/operations/README.md#durable-job-and-worker-operations),
and [ADR 0006](docs/adr/0006-durable-jobs.md).

## Assertion corrections

Use [assertion metadata history](#assertion-metadata-history) to discover exact
revision ordinals without fetching values/quotes; it does not change correction CAS.

`POST /v1/assertions/{memory_id}/revisions` requires `Idempotency-Key` and a
full replacement body: `expected_revision`, `value`, same-scope episode
`evidence`, `explicit_intent: true`, valid bounds, and `reason`. Subject,
predicate, and scope stay immutable. A successful correction returns `201`
with the next revision; a head mismatch returns `409 revision_conflict`.

This replaces the **entire valid interval**; omitted bounds are unbounded.
It does not split time or preserve the old value before a future-dated bound.
Historical `known_at` queries can still select the old revision. `explain`
defaults to revision `1`, **not latest**, when the revision is omitted.
Deleting a source used by any revision purges the entire assertion history.
See [the full contract](docs/STATUS.md#assertion-revision-contract) and
[ADR 0002](docs/adr/0002-assertion-revisions.md).

## Entities and SQL graph oracle

Use [exact entity query](#exact-entity-query-and-pagination) to discover distinct
IDs in readable scopes, inspect each candidate's GET evidence, and explicitly
choose graph seeds. Label equality does not resolve identity.

`POST /v1/entities` creates an immutable revision-1 identity with an allowlisted
type, canonical label, 1–32 same-scope episode quotes, and `explicit_intent: true`.
Use `GET /v1/entities/{memory_id}` for metadata/evidence; entities are excluded
from recall/explain. These are caller-reported identities, not verified facts or
trusted instructions. There is no aliasing, merging, name resolution, semantic
deduplication, or label-correction endpoint. Same HTTP key/body reuses the anchor;
a different key can create another entity with the same label.

`POST /v1/relations` creates **one canonical assertion**, not a second relation
object, between same-scope entity UUIDs with same-scope episode evidence.
Predicates are `depends_on`, `part_of`, `affects`, `works_for`, or `decides`;
all are multi-valued reported declarations, without fact arbitration.
`POST /v1/relations/{memory_id}/revisions` changes the target/evidence and replaces
the entire valid interval under revision-CAS; source/predicate stay fixed.
The generic assertion correction endpoint rejects typed relations with
`409 relation_revision_required`. Free-text `remember` never becomes a relation
by matching a label/predicate. Recall still uses FTS with `graph_used: false`;
relation items/explanations carry exact entity IDs, also included in budgeted context text.

Authenticated `POST /v1/graph/expand` is read-only and needs no `Idempotency-Key`.
It uses fixed parameterized SQL joins, not AGE, SQL/PGQ, Cypher, or dynamic
labels/queries. Explicit scope/seed/predicate filters only narrow current access.
Deterministic breadth-first simple paths are bounded to 1–2 hops and 1–100 paths;
all prefixes count. Results declare `backend: "sql"`, `projection_watermark: null`,
temporal bounds, consistency epochs, and bounded coverage—not complete knowledge.
Hidden seeds are not echoed; visible isolated seeds may appear without paths.
Incoming traversal does not infer inverse facts.

Entity revision 1 and exact relation assertion revisions can be declared in
checkpoint/effect `memory_refs`. Source purge follows entity evidence and **all
historical relation targets**, then existing checkpoint/effect dependencies.
Other surviving entities are not deleted merely because a relation is removed.
See [the contract](docs/STATUS.md#entities-and-sql-graph-oracle) and
[ADR 0005](docs/adr/0005-relational-graph.md). This is a correctness reference for
future backends, not measured graph utility or full M1/M3 acceptance.

## Typed checkpoints

`POST /v1/checkpoints` stores schema-1 typed state under a scope-local run/branch,
with mandatory `expected_head` (`null` for the first checkpoint), a nondecreasing
event watermark, exact memory references, and an HMAC checksum.
`GET /v1/checkpoints/{checkpoint_id}` returns a currently authorized, checked
envelope for that ID, not necessarily the current head.
[Head lookup](#checkpoint-head-lookup) resolves an exact scope/run/branch to the
same checked envelope without creating or restoring anything.
Checkpoints do not appear in `recall` or `explain`.

`POST /v1/checkpoints/restore` requires an exact harness/version match and
creates a new branch; it never rewinds the original branch. Dispatched effects
in the run ledger become unknown atomically with the fork. GET/head/restore merge
all live run effects, including those added after the snapshot. Untracked hints,
even planned ones, block resumption; this intentionally tightens legacy behavior.
`automatic_reexecution` is always false. Saved assertion references keep their exact historical revisions;
restore neither selects the latest revision nor refreshes current external facts.
Callers must declare every copied entity or assertion revision dependency
in `memory_refs` and sanitize all state;
undeclared copied text is not discovered automatically.

Deleting a source propagates through assertion history, checkpoint references,
and the entire descendant/fork lineage. Affected branch heads cannot be reopened.
See [the checkpoint contract](docs/STATUS.md#checkpoint-contract) and
[ADR 0003](docs/adr/0003-checkpoints.md). This is not full M1 or disaster recovery.

## Tool-effect ledger

Create a bootstrap checkpoint first: `POST /v1/tool-effects` requires an existing
scope-local run. Record a caller-generated operation UUID, tool name, canonical
action's lowercase 64-hex `action_hash`, and all exact memory dependencies.
Raw arguments/hash are not persisted; GET returns a tenant-HMAC fingerprint
and stable external idempotency key. Each run allows 100 effects for its lifetime.

`POST /v1/tool-effects/{memory_id}/transitions` appends CAS-checked state changes.
The harness must durably record dispatch **before** calling the tool and use the
stable external key where supported. Plan/transition responses are historical
revision references, not current-state snapshots or execution authorization.
With current authorization, surviving effects can replay old references even
after the run is sealed; fresh dispatch remains rejected.
Confirmed/failed outcomes require caller-reported
receipt references; the server does not verify them or query providers.
There is no external exactly-once guarantee, approval service, or automatic execution.

Purging any effect, directly or through a declared source, removes every
checkpoint payload in its run and permanently seals the run against new
effects, dispatch, checkpoints, or resumption. Independent surviving effects
remain readable/reconcilable. New run/operation IDs are not semantic deduplication.
See [the ledger contract](docs/STATUS.md#tool-effect-ledger),
[operations](docs/operations/README.md#tool-effect-operations), and
[ADR 0004](docs/adr/0004-tool-effects.md).

## Documentation

| English | 日本語 |
|---|---|
| [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md) |
| [Current contract and limitations](docs/STATUS.md) | [現在の契約と制限](docs/STATUS-jp.md) |
| [Initial architecture decisions](docs/adr/0001-initial-slice.md) | [初期アーキテクチャ決定](docs/adr/0001-initial-slice-jp.md) |
| [Assertion revision decisions](docs/adr/0002-assertion-revisions.md) | [Assertion revisionの決定](docs/adr/0002-assertion-revisions-jp.md) |
| [Checkpoint decisions](docs/adr/0003-checkpoints.md) | [Checkpointの決定](docs/adr/0003-checkpoints-jp.md) |
| [Tool-effect ledger decisions](docs/adr/0004-tool-effects.md) | [Tool-effect ledgerの決定](docs/adr/0004-tool-effects-jp.md) |
| [SQL graph oracle decisions](docs/adr/0005-relational-graph.md) | [SQL graph oracleの決定](docs/adr/0005-relational-graph-jp.md) |
| [Durable-job decisions](docs/adr/0006-durable-jobs.md) | [Durable jobの決定](docs/adr/0006-durable-jobs-jp.md) |
| [Japanese lexical FTS decisions](docs/adr/0007-japanese-fts.md) | [日本語lexical FTSの決定](docs/adr/0007-japanese-fts-jp.md) |
| [Local MCP decisions](docs/adr/0008-local-mcp.md) | [Local MCPの決定](docs/adr/0008-local-mcp-jp.md) |
| [Implicit recall hook decisions](docs/adr/0009-implicit-recall-hook.md) | [Implicit recall hookの決定](docs/adr/0009-implicit-recall-hook-jp.md) |
| [Atomic structured capture decisions](docs/adr/0010-atomic-capture.md) | [Atomic structured captureの決定](docs/adr/0010-atomic-capture-jp.md) |
| [Pgvector retrieval decisions](docs/adr/0011-pgvector-retrieval.md) | [Pgvector retrievalの決定](docs/adr/0011-pgvector-retrieval-jp.md) |
| [Python SDK decisions](docs/adr/0012-python-sdk.md) | [Python SDKの決定](docs/adr/0012-python-sdk-jp.md) |
| [Scope-access administration](docs/adr/0013-scope-access.md) | [Scope-access管理](docs/adr/0013-scope-access-jp.md) |
| [Runtime readiness](docs/adr/0014-runtime-readiness.md) | [Runtime readiness](docs/adr/0014-runtime-readiness-jp.md) |
| [Job cancellation](docs/adr/0015-job-cancellation.md) | [Job取消](docs/adr/0015-job-cancellation-jp.md) |
| [Required-context recall](docs/adr/0016-required-context.md) | [Required-context recall](docs/adr/0016-required-context-jp.md) |
| [Exact structured recall filters](docs/adr/0017-recall-filters.md) | [構造化recallの完全一致filter](docs/adr/0017-recall-filters-jp.md) |
| [Checkpoint-head lookup](docs/adr/0018-checkpoint-head.md) | [Checkpoint headの照会](docs/adr/0018-checkpoint-head-jp.md) |
| [Owned-job query and pagination](docs/adr/0019-job-query.md) | [所有jobの照会とpagination](docs/adr/0019-job-query-jp.md) |
| [Assertion metadata history](docs/adr/0020-assertion-history.md) | [Assertion metadata履歴](docs/adr/0020-assertion-history-jp.md) |
| [Exact entity query and pagination](docs/adr/0021-entity-query.md) | [完全一致entity照会とpagination](docs/adr/0021-entity-query-jp.md) |
| [Explicit batch capture](docs/adr/0022-batch-capture.md) | [明示batch capture](docs/adr/0022-batch-capture-jp.md) |
| [Operations](docs/operations/README.md) | [運用](docs/operations/README-jp.md) |
| [Contributing](CONTRIBUTING.md) | [貢献方法](CONTRIBUTING-jp.md) |

## Dependency licensing

Project code is [MIT-licensed](LICENSE); **dependencies are not all MIT**.
[Janome 0.5.0 is Apache-2.0](https://github.com/mocobeta/janome/blob/0.5.0/LICENSE.txt).
Its bundled mecab-ipadic dictionary/statistical data has separate
[IPADIC copyright/license notices (NAIST/ICOT)](https://github.com/mocobeta/janome/blob/0.5.0/NOTICE.txt);
[Janome's dictionary additions](https://github.com/mocobeta/janome/blob/0.5.0/ipadic/Noun.proper.csv.patch)
are part of that pinned release. Preserve upstream license/notice files when
redistributing packages or images. The packaged dictionary is a code dependency,
not stored user memory; only PostgreSQL stores memory projections.
No LLM weights or benchmark conversation histories are bundled.
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6) uses the
**PostgreSQL License**, not the project's MIT license. Preserve its upstream license
when redistributing the DB image. Its stable release date is **2026-07-29**;
the verified official tag commit is `8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`
([pinned changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)).
The adopted artifact is the pinned prebuilt upstream image, not a local source build.
Both final native images retain `/usr/share/doc/pgvector/LICENSE`, verified
byte-identical to the pinned upstream license, SHA-256
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`.
