# pg_agmemory

[日本語](README-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**PostgreSQL-backed agent memory, licensed under MIT.** The public repository
is [`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory); the local checkout directory, Python package,
and service are `pg_agmemory`. Run the commands below from that local checkout.

## Current development: v0.1.1 / API v1 / schema 19

M3 now adds **administrator-only graph-generation metadata**, not an enabled
graph backend. `pg-agmemory graph-generation` records input fingerprints,
generation lineage and artifact receipts using revision/input CAS. Receipts
never verify an artifact or authorize serving. SQL remains the only active graph
backend; the expensive fixed-hop candidate and unqualified native VLE remain off.
Stop/drain before migration 019, deploy matching components and regenerate
schema-19 recovery artifacts. See [generation operations](docs/operations/README.md#graph-generation-metadata-schema-19).
M3 is incomplete; the published M2 rollback point remains tag `v0.1.0`.

`pg-agmemory graph-artifact export/check` now builds and verifies a private,
deterministic canonical topology artifact for an existing generation. It includes
IDs and temporal edge revisions, not labels/source text; rebuilding an unchanged
recorded generation produces identical bytes. File verification compares both
its keyed signature and current canonical data, without enabling a graph backend
or modifying generation metadata. See [artifact operations](docs/operations/README.md#canonical-graph-artifacts).

## Published M2 contract: v0.1.0 / schema 18

**Memory infrastructure for agents and LLMs, not a judgment system.** M2's
declared engineering gates are complete: scoped storage/retrieval/updates,
non-destructive compaction, bounded isolated restore, durable model-call
accounting, resource limits and one reference benchmark. Version 0.1.0 changes
release metadata and the capability stage to `m2-core-mvp`; it adds no migration.
The frozen release build must pass native qualification before publication.

This is a **core MVP for controlled deployments**, not production/HA/PITR
certification or a guarantee of model judgment. Native API v1/SDK expose 38
memory resources; MCP remains four tools and hooks are read-only. Generation
and embedding processing stay default-deny and require an administrator-pinned
local profile. SQL graph is supported; AGE/SQL-PGQ integration is M3.
Start with the [current deployment/upgrade contract](docs/operations/README.md#m2-core-mvp-deployment)
and [qualification evidence and limits](docs/STATUS.md). Historical sections
below retain their original measurements and must not override this contract.

The bounded recovery drill now covers both retained and purged extraction,
adoption, vectors, working snapshots/tails, SQL graph and tool-effect records.
An unchanged mixed suppress/purge prefix already in the backup is preserved;
new suppress replay remains unsupported. No migration or public API is added.
See [recovery operations](docs/operations/README.md#schema-15-operational-state-application).

The [single reference memory benchmark](docs/EVALUATION.md#one-reference-memory-benchmark)
now has a pinned example profile and machine-readable results: exact typed state,
coverage/tail and call-accounting preservation, with one-case timing/size and
prior failures. These are retained measurements, not a new model comparison,
semantic-quality pass or schema-18 live-model rerun.

Migration 018 also makes tombstone metadata read permissions set-based. The
tenant/scope/expiry rules are unchanged, including for large deletion ledgers.

Migration 017 keeps deletion visibility as a tenant-local, statement-local
tombstone set. It fixes a measured post-deletion query-plan regression without
weakening RLS or changing PostgreSQL planner settings. Stop/drain before migration
and regenerate current-schema recovery artifacts.

Migration 016 makes read visibility set-based while preserving tenant, scope,
expiry and tombstone predicates under forced RLS. Recall now shares candidate
materialization between ranking and coverage checks. It does not disable RLS,
change rank rules, weaken barriers or enable ANN. Use matching components and
current-schema recovery artifacts; schema-15 data/recovery keys are preserved.

Optional `create_app(..., timing_sink=...)` instrumentation now records committed
server timing without request bodies, query strings or actor labels. It is off
by default and does not add an HTTP endpoint/header. A frozen S resource recipe
and real HTTP/two-worker load harness are available; see
[resource measurements](docs/operations/README.md#resource-measurements).
Development/preflight measurements are not S qualification or M2 completion.

`recovery-apply export/apply` can now restore exact operational state on an
isolated, content-matching database: original receipts/idempotency, current
ACL/policy, job state and durable call accounting. Authenticated bundles use a
separate admin-only recovery key. Compare-and-swap, structural constraints and
post-application fingerprints must all pass in one transaction; otherwise it
rolls back. Runtime roles cannot enable the historical-write context.
This is a bounded administrative operation, not automatic erasure, arbitrary
point-in-time recovery or permission to start services. See
[schema-15 recovery operations](docs/operations/README.md#schema-15-operational-state-application).
Take a fresh schema-15 backup containing the recovery key after upgrading.

The admin-only `processing-recovery export/check` command now compares a
consistent processing-state snapshot: current policies, ACLs, call reservations,
semantic job identities, job state and related operational metadata. A mismatch
exits nonzero. It writes no database state and **never authorizes restart**;
the bounded application above has separate preconditions. See
[processing recovery operations](docs/operations/README.md#processing-state-recovery-check).
For current development, use matching v0.1.1 / API v1 / schema 19 components.
No model calls are made by the check.

Schema 14 adds **transaction-bound deletion target manifests** and the
administrator-only `pg-agmemory deletion-history export` command. Each new
receipt records its exact expanded target set; the database rejects incomplete
or subsequently appended manifests. Existing schema-13 receipts retain an
explicit unknown mapping, not an invented backfill.

The export is deletion metadata only: **not a restore command or permission to
restart API/model workers**. Full latest-ACL/policy/model-call/quota reconciliation
remain mandatory; the export alone supplies none of them. Native `forget` still permits
`preview`/`purge` only; the stored `suppress` mode is not enabled as a public API.
Migration 014 introduced those manifests; migration 015 adds recovery support. See
[current operations](docs/operations/README.md#schema-14-deletion-manifests).
No model calls or new Native/MCP resources are added.

## Retained v0.0.27 / schema 13 processing contract

The processing behavior below is retained; its version-specific upgrade and
measurement statements describe the prior release, not schema-14 qualification.

**Memory infrastructure, not a judgment system.** Users select supported models
for their budget; pg_agmemory guarantees memory contracts, not model reasoning.
The [revised M2–M5 plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) separates core
acceptance from one reference benchmark. Multi-model comparison and mandatory
human semantic grading are out of scope. The [Wikipedia review packet](docs/HUMAN_REVIEW.md)
is a retained optional diagnostic, not the next required task.

Stage **`m2-background-processing`** adds default-deny, administrator-authorized
local extraction/embedding jobs, caller adoption of quarantined candidates, and
same-scope working compaction. Native API/SDK now expose **38 memory resources**;
MCP remains four tools. The read-only hook optionally restores an explicitly
identified working snapshot with a separately configured byte budget.
Ordinary observe and hook requests retain their previous behavior.

Extraction models propose subject, predicate, value and an exact quote; trusted
code resolves a unique Unicode span. Inferred publication is limited to
policy-allowlisted literal preferences, not verified intent or semantic truth.
All other candidates remain untrusted. Model calls run outside memory
transactions; durable reservations, lease/epoch checks and purge dependencies
prevent blind retries and stale publication.

**At the historical v0.0.27 checkpoint M2 was incomplete.** Measured evidence
includes 600 held-out synthetic questions across 50 groups (hybrid Recall@20
99.818%, equal to vector-only), 10,000 actual unauthorized Native requests with
zero unexpected outcomes, real worker SIGKILL recovery, and a three-call local
extract/embed/compact/restore lifecycle. Remaining core work includes full
supported-history logical restore, current ACL/model-call accounting
reconciliation and resource qualification, not human labels or 20 agent tasks.
The corrected public oracle QA diagnostic recorded zero invalid outputs in 144
attempts, but 136 abstentions and only 22 mechanical exact matches; it does not
establish answer quality. Earlier invalid outputs remain in the evidence record.
Exact tested SHAs, failures and public-baseline status are in
[EVALUATION](docs/EVALUATION.md); current interfaces and operator commands are in
[ADR 0028](docs/adr/0028-background-processing.md) and
[schema-13 operations](docs/operations/README.md#schema-13-background-processing).

Use matching **service 0.0.27 / API v1 / schema 13** API, worker, SDK, MCP and hook
components. Upgrading requires migrations 012–013; source rollback alone cannot
downgrade the database. Model processing is disabled until an administrator pins
a local worker profile using `scope-synthesis`; capture permission alone does
not authorize model calls.

## Retained v26 guide and historical evidence

The remaining version-specific walkthroughs below describe **v0.0.26/schema 11**,
not the current binary's capability requirements or new processing surfaces.
Use the current contract and schema-13 operations above for v27. The historical
API examples remain useful for unchanged, default-off paths; their version checks
and migration targets must not be used as current v27 instructions.

**Historical bounded milestone: v0.0.26/schema 11 scope capture policy.
Implementation `c07630009ff4dcc34542e3ea80064d4f10c4d8b5` passed local qualification
and exact-SHA native Docker amd64/arm64 CI.
Verified v0.0.25 and earlier results below are historical, not v0.0.26 qualification.
Not a completed M0/M1/M2/M3, MVP, or production release.**
See [the exact implementation's evidence](docs/STATUS.md#v0026--schema-11) and
[source CI 35308638587](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587).
A later documentation-only publication commit is not the tested implementation SHA.
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

## Scope capture policy

**v0.0.26 / API v1 / schema 11; implementation verified locally and in native CI.**
Stage `m2-scope-capture-policy` adds administrator-only
`pg-agmemory scope-capture get|set` via `PGAG_ADMIN_DATABASE_URL`.
There are no new memory REST routes, SDK resources, or MCP tools:
Native/SDK remains **31 memory resources**, MCP **four tools**, and the hook read-only.

The closed policy requires all four fields:

```json
{
  "enabled": true,
  "source_namespaces": null,
  "consent_references": null,
  "max_content_bytes": 262144
}
```

This is also the legacy policy when no row exists. `enabled` is a strict boolean;
each list is `null` (unrestricted) or at most 64 distinct, trimmed, sorted strings
of 1–256 characters, without C0 controls or invalid UTF-8. `[]` denies all;
matching is exact and case-sensitive. `max_content_bytes` is a strict integer
1–262144, measured on normalized `Observe.content` encoded as UTF-8, not raw JSON.
The existing 65,536-character content bound still applies.

Current scope read/write authorization precedes policy checks. Observe, capture,
and batch capture check policy **before idempotency/source-event deduplication**:
changed policy can deny an old exact replay with **403 `capture_policy_denied`**
and no writes. Invalid stored policy fails closed with **503 `capture_policy_invalid`**.
Invalid capture-content UTF-8 is rejected by existing request validation with
**422 `invalid_request`**, not a new capture-specific error.
Missing or unauthorized scopes remain **404 `not_found`**.

`set` replaces the entire policy using the current **tenant access-epoch CAS**.
A real change atomically advances that epoch once and records private before/after
audit snapshots and the DB role. An equivalent policy is a no-op, but still requires
the correct epoch; setting the default on an unconfigured scope creates no row.
Restoring the full legacy policy does not delete an existing row.
The administrator's tenant barrier spans commit **and output delivery**.
After a possibly committed failure, get/read back and reconcile before a new CAS;
do not blindly retry.

This is admission control, **not consent verification, secret/PII detection,
provider-egress authorization, automatic extraction, or compaction**.
Existing content remains readable; explicit remember/jobs on existing episodes
remain allowed. There is no retroactive purge or cancellation. Epoch changes fence
old contexts and already-claimed jobs; workers can recover under the new epoch.
Back up and quiesce schema-10 writers/workers before migration 011, then start only
matching schema-11 components. Code rollback alone cannot undo the migration.
See [get/set/restore and upgrade operations](docs/operations/README.md#scope-capture-administration),
[ADR 0026](docs/adr/0026-scope-capture-policy.md), and
[qualification / next bounded M2 work](docs/STATUS.md#v0026--schema-11).
The next resume point is bounded M2 automatic synthesis, embedding integration,
and compaction, with remaining quality/task-replay gates. None is enabled by this
milestone, and capture admission is not provider-egress authorization.

## Selectable inference providers

**Retained provider foundation; v26 implementation verified locally and in native CI.**
The following CA/live results are historical v25/v24 evidence, not v26 qualification.
The Docker base now selects the OS CA bundle through
`SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`. The non-root production smoke
checks that setting and a populated root-CA store. Targeted local real-libpq TLS
and runtime CA checks passed, as did the full local suite; native CI passed. The successful
v24 Azure run used an explicit DSN CA file, not this image default.
Optional `pg-agmemory[providers]` adds **httpx==0.28.1**, with no new dependency
versions or provider-specific migration. Schema 11's migration is for capture policy.
It remains the same core distribution, not a
separate SDK or a PyPI publication claim.
Use the typed `pg_agmemory.providers` library or operator commands
`pg-agmemory infer inspect|summarize|embed --config FILE`.
Choose one trusted JSON profile per call: `local_http` (loopback HTTP/HTTPS),
`openai_compatible` (HTTPS), or `azure_ai` (SQL on Flexible Server/HorizonDB).
Summarizing locally and embedding through a separately selected Azure profile is explicit.
No automatic backend fallback, retry, ingestion, publication, enqueue, or compaction.

HTTP profiles use bounded OpenAI-compatible `chat/completions` and `embeddings`,
not a promise of compatibility with every vendor/model.
SQL profiles use a dedicated TLS-verified, restricted autocommit connection,
not a canonical memory transaction or session lock. Extension version,
extension-owned compatible function signatures, and SQL permissions are checked.
Operators provision Azure credentials/model registrations outside this application;
we recommend managed identity where supported. The adapter installs/configures nothing.
HTTP `inspect` constructs and closes a client without network calls, validating
configuration and credential headers; SQL `inspect` uses read-only catalog checks.
Neither is inference or proof of model access, quota, or endpoint connectivity.
`max_output_tokens` requires an HTTP summary model; a nondefault `sentence_count`
is allowed only for Flexible Language mode.
The application never retries. SQL embedding/Language explicitly use `max_attempts => 1`;
`azure_ai.generate` has no verified retry or output-token knob. One `MATERIALIZED`
SQL invocation does **not** guarantee one billable upstream call or bounded charges.

Input is closed `{"text": "..."}`, preserving text bytes, bounded to 65,536
characters/256 KiB; HTTP request and response bounds are 256 KiB/2 MiB.
Summaries carry `status: "untrusted"` and are not grounded assertions, approvals,
or compaction snapshots. Embeddings must contain exactly 768 finite, nonzero
values in a declared model space; no padding/truncation.
Operator-pinned model revisions do not verify remote aliases or AIMM upgrades.
Only an explicit `embedding_input` → provider `embed` → `PutEmbedding` workflow
can upload a vector, under unchanged current ACL/digest/purge checks.
After an uncertain upload, retain the generated payload and write key; **do not
rerun the model to reconstruct a retry**. Provider `billing_unknown` is separate
from a Native mutation's `outcome_unknown`; cancellation may not stop charges.

Native/SDK remains **31 memory resources**, MCP **four tools**, and the hook unchanged.
Stage `m2-scope-capture-policy` retains `model_inference` with
`automatic: false`, `publishes_memory: false`, `live_provider_qualified: false`.
Exact Ollama and Azure Flexible Server profiles have
[bounded live evidence](docs/INFERENCE_PROFILES.md#recorded-azure-live-evidence),
not blanket provider or full MemoryDB-on-Azure hosting qualification.
**One English Azure summary was returned in Spanish**; passing contracts do not
pass language/grounding/quality gates.
The MemoryDB PostgreSQL 18.6 / `vector` 0.8.6 pin remains; inference SQL may use
a separate database. Preview/version/lifecycle limits and operator privacy/budget
responsibilities remain explicit; human review, quality, MVP, and M2 gates remain open.
See [the contract](docs/STATUS.md#selectable-inference-providers),
[profiles and commands](docs/operations/README.md#selectable-inference-providers),
[ADR 0025](docs/adr/0025-live-provider-qualification.md), and
[v25 qualification status](docs/STATUS.md#v0025--schema-10).

For concrete Ollama/OpenAI/Azure SQL configuration files, secret handling, and opt-in live
checks, see the [inference profile guide](docs/INFERENCE_PROFILES.md).

## Episode query and pagination

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
Authenticated read-only `POST /v1/episodes/query` requires no `Idempotency-Key`.
Closed `QueryEpisodes` accepts distinct `scope_ids` (1–32 UUIDs), nullable aware
`occurred_from`/`occurred_to`, strict `max_items` (1–100, default 20), and nullable
`before: EpisodeCursor`. The closed cursor requires aware `recorded_at` and UUID
`memory_id`. Equal/reversed time bounds or invalid fields give **422 `invalid_request`**.
Scope and occurred-time filters apply **before LIMIT**, with half-open
`occurred_from <= occurred_at < occurred_to`; omitted/null bounds are unbounded.
They are not `as_of`, `known_at`, or bitemporal reconstruction.

**200 `EpisodePage`** returns at most 100 `EpisodeSummary` items containing only
`memory_id`, `revision: 1`, `scope_id`, `occurred_at`, and `recorded_at`,
plus `next_cursor` and `consistency` (`access_epoch`, `deletion_epoch`).
No content/body, consent reference, source URI, `source_namespace`, event ID, or
job payload is returned. Source identity uses HMAC anchors; there is no plaintext
`source_namespace` filter. Explicitly select an episode, use
`Explain(memory_id, revision=1)` for content, then optionally make an explicit
`Remember` with literal evidence—not automatic ingestion, synthesis, extraction,
or compaction. Content is evidence, not trusted instructions or current truth.

Current tenant/scope RLS applies, **without an ownership filter**: readable shared
scopes are included; unknown/private/cross-tenant scopes contribute no rows.
No matches returns `episodes: []`, `next_cursor: null`, and current epochs.
Each page rechecks current ACL/purge visibility under the response-drain barrier.
SQL reads metadata from `episode` + `object`, without audit or other application writes.
Order is **`recorded_at DESC, memory_id DESC`**, using `object.created_at`,
not occurred time; a late historical event can be newest among matching rows.
The exclusive cursor is position, not authority, snapshot, receipt, event sequence,
or compaction watermark. Deleted/forged positions only narrow current authorized
rows; newer recorded rows above the boundary require explicit restart.
Fetch `max_items + 1`; only overflow emits a cursor from the **last returned item**.

Async `query_episodes(QueryEpisodes) -> EpisodePage` uses `mutation=False`,
**256 KiB request / 2 MiB response** bounds, and no automatic paging/retry.
Read loss has `outcome_unknown: false`, not an unknown mutation outcome.
Native/SDK has **31 resource methods**; four MCP tools and the closed hook are unchanged.
Stage `m2-scope-capture-policy` retains `episode_query`; schema 11 adds migration 011.
See [the contract](docs/STATUS.md#episode-query-and-pagination),
[the paging example](docs/operations/README.md#episode-query-and-pagination),
[ADR 0023](docs/adr/0023-episode-query.md), and
[qualification evidence](docs/STATUS.md#v0023--schema-10).

## Explicit batch capture

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
All admission and replay outcomes below require the
[current scope capture policy](#scope-capture-policy) to permit the episode first.
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
Native/SDK has **31 resource methods**; four MCP tools and the closed hook are unchanged.
Stage `m2-scope-capture-policy` retains `atomic_batch_structured_capture` and
`atomic_batch_capture` metadata, without changing single-job `atomic_capture`.
Schema 11 adds capture policy migration 011; dependency versions are unchanged and provider execution is a separate operator path.
See [the contract](docs/STATUS.md#explicit-batch-capture),
[the async example](docs/operations/README.md#explicit-batch-capture),
[ADR 0022](docs/adr/0022-batch-capture.md), and
[qualification evidence](docs/STATUS.md#v0022--schema-10).

## Exact entity query and pagination

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
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
Native/SDK has **31 resource methods**; MCP's four tools and the closed hook are
unchanged. Schema 11 adds capture policy migration 011, not a dependency-version or AGE change.
See [the contract](docs/STATUS.md#exact-entity-query-and-pagination),
[paging example](docs/operations/README.md#exact-entity-query-and-pagination),
and [ADR 0021](docs/adr/0021-entity-query.md).

## Assertion metadata history

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
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
The Native/SDK surface has **31 resource methods**; MCP's four tools and the
closed hook are unchanged, with no history tool or field. Schema 11 is for capture policy.
See [the contract](docs/STATUS.md#assertion-metadata-history),
[paging example](docs/operations/README.md#assertion-metadata-history),
and [ADR 0020](docs/adr/0020-assertion-history.md).

## Owned-job query and pagination

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
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
or state change. Native/SDK now has **31 resource methods**; MCP's four tools and
the hook are unchanged, with no job tool.
See [the contract](docs/STATUS.md#owned-job-query-and-pagination),
[paging example](docs/operations/README.md#owned-job-query-and-pagination),
and [ADR 0019](docs/adr/0019-job-query.md).

## Checkpoint-head lookup

**Retained checkpoint-head contract; v0.0.26 implementation verified locally and in native CI.**
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
With episode query, the current Native/SDK surface is **31 methods**; MCP's **four tools** and the
hook are unchanged, with no checkpoint tool. Schema 11 requires capture policy
migration 011; dependency versions/artifact pins are unchanged.
See [the contract](docs/STATUS.md#checkpoint-head-lookup),
[the practical lookup and upgrade](docs/operations/README.md#checkpoint-head-lookup),
and [ADR 0018](docs/adr/0018-checkpoint-head.md).

## Exact structured recall filters

**Retained recall-filter contract; v0.0.26 implementation verified locally and in native CI.**
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

There are still **31 Native/SDK resource methods and four MCP tools**, with no
new safe error code. Hook input rejects `filters`; its internal default is
`None`, preserving trusted startup boundaries. Recall filters add no
dependency-version/index change, persisted priority, or cache.
Filters express caller selection, not trusted instructions or verified truth.
See [the contract](docs/STATUS.md#exact-structured-recall-filters),
[examples and upgrade](docs/operations/README.md#exact-structured-recall-filters),
and [ADR 0017](docs/adr/0017-recall-filters.md).

## Required-context recall

**Retained required-context contract; v0.0.26 implementation verified locally and in native CI.**
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
There are still **31 Native/SDK resource methods and four MCP tools**.
The hook rejects this input field and always constructs recall with empty
references, so it adds no host pinning. No write, idempotency, persistent priority,
cache, inference, provider call, or schema migration is added.
See [the contract](docs/STATUS.md#required-context-recall),
[examples and upgrade](docs/operations/README.md#required-context-recall),
and [ADR 0016](docs/adr/0016-required-context.md).

## Explicit job cancellation

**Retained job-cancellation contract; v0.0.26 implementation verified locally and in native CI.**
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
The SDK retains `cancel_job` and episode query within its unchanged **31 methods**;
MCP's four tools and the read-only hook are unchanged.
See [the full contract](docs/STATUS.md#explicit-job-cancellation),
[operations and schema-10 migration](docs/operations/README.md#explicit-job-cancellation),
and [ADR 0015](docs/adr/0015-job-cancellation.md).

## Runtime readiness

**Retained readiness contract; v0.0.26/schema 11 implementation verified locally and in native CI.**
`GET /healthz` remains process liveness after successful startup:
`{"status":"ok"}`, without DB calls. Public, unauthenticated `GET /readyz`
returns HTTP **200** with exactly `{"status":"ready"}` or an expected-failure
**503** with exactly `{"status":"not_ready"}`. Both readiness responses carry
`Cache-Control: no-store` and a generated UUID `X-Request-ID`, with no private
details. Supplied authorization is ignored; no tenant/principal is selected.

Each admitted check opens a fresh **runtime** DB connection and reads only the
role/schema/extension contract: no privileged runtime role or application-table
ownership, exact migration history 1–11, and `vector` 0.8.6 in `public`.
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
Readiness adds no SDK/MCP/hook probe method; the Native/SDK memory surface,
including episode query, remains 31.
See [the full contract](docs/STATUS.md#runtime-readiness),
[probe operations](docs/operations/README.md#runtime-readiness),
and [ADR 0014](docs/adr/0014-runtime-readiness.md).

## Scope-access administration

**Retained scope-access contract; v0.0.26 implementation verified locally and in native CI.**
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

`009_scope_access.sql` introduced scope-access audit in schema 9; schema 11 retains it.
PostgreSQL 18.6/pgvector 0.8.6 pinned images and dependency versions stay unchanged.
The current stage is `m2-scope-capture-policy`. See [the full contract](docs/STATUS.md#scope-access-administration),
[get → set → revoke example and migration](docs/operations/README.md#scope-access-administration),
and [ADR 0013](docs/adr/0013-scope-access.md).

## Python SDK

**SDK retains 31 memory methods; providers use a separate library/CLI; v0.0.26 implementation verified locally and in native CI.** From the matching checkout:

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
client and requires authenticated **service 0.0.26 / API v1 / schema 11**
capabilities. Use only inside one context; no re-entry or automatic retries.
Await outstanding tasks, or cancel and await them, **before exiting the context**.
Client close is not a request scheduler/cancellation manager or a DB rollback.
Exit closes connections, **not stored memory**. Scopes only narrow server ACLs.
Returned memory is evidence, not trusted instructions or guaranteed current facts.

The SDK covers all 31 public memory resource methods, including batch capture, entity query, explicit job cancellation,
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

The synthetic container checks need no hosted model key or external memory database. Container images
and Python dependencies must be downloadable on the first run.
**Historical V25 full local qualification passed; native CI passed.**
These records do not qualify v26; see [separate v26 evidence](docs/STATUS.md#v0026--schema-11).
Apple Container `./scripts/test-containers.sh` reported
**999 passed, 5 live skipped, 1 known warning, 494.49 s**.
Ruff, mypy **22 source files + 1 strict SDK consumer**, all four
core/hook/sdk/providers installation smokes, and **all production smokes,
including the new system-CA smoke**, passed.
Implementation
[`adbead0ab42dfa4a5465f9464d3855c5f64d85c1`](https://github.com/rioriost/pg_agmemory/commit/adbead0ab42dfa4a5465f9464d3855c5f64d85c1)
passed exact-SHA [CI 35303758871](https://github.com/rioriost/pg_agmemory/actions/runs/35303758871):
**amd64 999 passed, 5 live skipped, 1 warning / 600.77 s;
arm64 999 passed, 5 live skipped, 1 warning / 797.75 s**.
Both native jobs passed Ruff, mypy **22+1**, all four installation profiles, and
all production smokes including system CA. This is implementation CI, not a
qualification claim for a later final-documentation commit.
This did not rerun Azure inference or qualify the CA ENV variant on Azure.
See [ADR 0025](docs/adr/0025-live-provider-qualification.md).

**Historical V24 implementation and synthetic-provider contracts qualified.**
Implementation
[`88975a862ff97873c60e5ce53e066e1aa7b52686`](https://github.com/rioriost/pg_agmemory/commit/88975a862ff97873c60e5ce53e066e1aa7b52686)
passed the full Apple Container `./scripts/test-containers.sh`:
**987 passed, 1 warning, 493.68 s**.
Exact-SHA [CI 35292285229](https://github.com/rioriost/pg_agmemory/actions/runs/35292285229)
passed: **amd64 987 / 762.27 s; arm64 987 / 809.06 s**.
All three environments passed Ruff, mypy **22 source files + 1 strict SDK consumer**,
all four core/hook/sdk/providers installation profiles, and all production smokes.
The new smoke runs the actual operator CLI against synthetic HTTP, followed by
explicit Native vector upload/replay/purge. **987 = 821 retained + 166 new cases**.
SQL fixtures include real disposable PostgreSQL execution, **not the Azure extension binary**.
No live Azure/real-model calls, billed resources, or private-data egress were used.
This does not qualify live compatibility, quality, or M2 completion.
See [qualification evidence](docs/STATUS.md#v0024--schema-10).
Final-docs CI for this update has not run.

**Historical v0.0.23 implementation qualified locally and on both native architectures.**
Implementation
[`bf53a30625ffcfb0f23f986abcec5e2d608dcb68`](https://github.com/rioriost/pg_agmemory/commit/bf53a30625ffcfb0f23f986abcec5e2d608dcb68)
passed the full Apple Container `./scripts/test-containers.sh`:
**821 passed, 1 warning, 499.94 s**.
Exact-SHA [CI 35280254044](https://github.com/rioriost/pg_agmemory/actions/runs/35280254044)
passed: amd64 **821 passed, 1 warning, 821.33 s**;
arm64 **821 passed, 1 warning, 823.87 s**.
All three environments passed Ruff, mypy **19 source files + 1 strict SDK consumer**,
all optional installation checks, and all production smokes, including episode query.
**821 = 780 retained + 41 new cases**; see [qualification evidence](docs/STATUS.md#v0023--schema-10).
Separate final v23 docs
[`9bc5092e5919997abb945554ec8363e4bf0e6dae`](https://github.com/rioriost/pg_agmemory/commit/9bc5092e5919997abb945554ec8363e4bf0e6dae)
completed [CI 35282317544](https://github.com/rioriost/pg_agmemory/actions/runs/35282317544)
successfully on attempt 2: **821 cases each, amd64 851.59 s / arm64 802.42 s**.
Attempt 1's amd64 Docker Hub authentication connection reset occurred **before tests**;
only the failed job was retried, without product code changes.
This final-docs evidence is distinct from implementation CI and does not qualify v24.

**Historical v0.0.22 graph fix and all-direction regression verified.**
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
are qualified: **780 = 773 batch baseline + 1 nested-loop case + 6 direction cases**.
This extension changes regression coverage, not product SQL, version, or schema.
Separate final v22 docs
[`36dc19d8897db6768badaf39019445e2a22d23da`](https://github.com/rioriost/pg_agmemory/commit/36dc19d8897db6768badaf39019445e2a22d23da)
passed [CI 35273848787](https://github.com/rioriost/pg_agmemory/actions/runs/35273848787):
**780 cases each, amd64 862.46 s / arm64 730.09 s**, with all checks/smokes.
That final-docs run is distinct from the directional implementation run, not v23 qualification.

**Earlier 774-test graph-fix qualification:**
Fix [`bf7429327955071239fdc2f7b60d1a5d47dfff7e`](https://github.com/rioriost/pg_agmemory/commit/bf7429327955071239fdc2f7b60d1a5d47dfff7e)
passed the full Apple Container suite: **774 passed, 445.86 s**, with all smokes.
[CI 35268438022](https://github.com/rioriost/pg_agmemory/actions/runs/35268438022)
passed on that exact SHA: amd64 **774 passed, 779.45 s**;
arm64 **774 passed, 682.78 s**. Both passed Ruff, mypy **19 source files +
1 strict SDK consumer**, all installation checks, and all production smokes.
A negative control against unmodified pre-fix source failed as expected; its
synthetic diagnostic is not the failed CI plan or a performance benchmark.

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
remain unchanged. The qualified directional extension covers all nine combinations
and their scan-loop checks.
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

**v0.0.26 requires schema 11 and migration `011_capture_policy.sql`.** Existing schema-10 databases
use the [schema-11 maintenance upgrade](docs/operations/README.md#schema-11-scope-capture-policy-upgrade).
Older schemas still require `010_job_cancellation.sql`, introduced in v0.0.15;
follow the [migration sequence through 011](docs/operations/README.md#schema-11-scope-capture-policy-upgrade).
Older schemas also require the retained migration sequence, including
`009_scope_access.sql` for durable admin audit.
Older databases still require the v0.0.11 `008_pgvector.sql` migration. PostgreSQL must provide
`vector` **0.8.6 in `public`**; migration rejects an existing extension with the
wrong version or schema. The prebuilt profile supplies the matching extension.
API, worker, and `migrate` validate it even when schema 11 is already recorded.
Before a required migration, stop/drain **all old/new APIs, workers, adapters,
hook launches, SDK callers, and admin commands**, preserve a backup and current
deletion/ACL records, then migrate offline.
Older databases also apply the retained migrations, including migration 007's
lexical backfill. **There is no embedding backfill or automatic embedding rebuild**.
Only matching v0.0.26 processes may restart; API/worker startup requires exact
history `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]` and extension `vector` 0.8.6 in schema `public`.
Older-schema processes are not rolling-compatible with schema 11.
Keep old images stopped; v0.0.1 lacks a schema-compatibility guard.
No rolling coexistence or downgrade is supported. Follow the
[current schema-11 procedure](docs/operations/README.md#schema-11-scope-capture-policy-upgrade).

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

Schema 11 checks [scope capture policy](#scope-capture-policy) before either
deduplication path, including exact replays. The retained shapes and job behavior
below apply only after current authorization and policy admission.

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
whose v0.0.26 test and runtime stages retain `mcp`, `hook`, and `sdk` extras.
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
capabilities request and requires API `v1`, service `0.0.26`, and schema `11`.
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
**service `0.0.26` / API `v1` / schema `11`**, then posts Native recall with
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
back up, rebuild lexical projections, then restart matching v0.0.26 processes only.
This does not populate or rebuild embeddings. There is no automatic
repair worker or file-based memory index; lexical rebuild does not call providers.
The separate operator inference library does not change this maintenance path.
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
| [Scope capture policy](docs/adr/0026-scope-capture-policy.md) | [Scope capture policy](docs/adr/0026-scope-capture-policy-jp.md) |
| [Runtime readiness](docs/adr/0014-runtime-readiness.md) | [Runtime readiness](docs/adr/0014-runtime-readiness-jp.md) |
| [Job cancellation](docs/adr/0015-job-cancellation.md) | [Job取消](docs/adr/0015-job-cancellation-jp.md) |
| [Required-context recall](docs/adr/0016-required-context.md) | [Required-context recall](docs/adr/0016-required-context-jp.md) |
| [Exact structured recall filters](docs/adr/0017-recall-filters.md) | [構造化recallの完全一致filter](docs/adr/0017-recall-filters-jp.md) |
| [Checkpoint-head lookup](docs/adr/0018-checkpoint-head.md) | [Checkpoint headの照会](docs/adr/0018-checkpoint-head-jp.md) |
| [Owned-job query and pagination](docs/adr/0019-job-query.md) | [所有jobの照会とpagination](docs/adr/0019-job-query-jp.md) |
| [Assertion metadata history](docs/adr/0020-assertion-history.md) | [Assertion metadata履歴](docs/adr/0020-assertion-history-jp.md) |
| [Exact entity query and pagination](docs/adr/0021-entity-query.md) | [完全一致entity照会とpagination](docs/adr/0021-entity-query-jp.md) |
| [Explicit batch capture](docs/adr/0022-batch-capture.md) | [明示batch capture](docs/adr/0022-batch-capture-jp.md) |
| [Episode query and pagination](docs/adr/0023-episode-query.md) | [Episode照会とpagination](docs/adr/0023-episode-query-jp.md) |
| [Selectable inference foundation](docs/adr/0024-selectable-inference.md) | [選択式推論基盤](docs/adr/0024-selectable-inference-jp.md) |
| [Live provider evidence and CA-image fix](docs/adr/0025-live-provider-qualification.md) | [Live provider証拠とCA image修正](docs/adr/0025-live-provider-qualification-jp.md) |
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
