# Current contract and limitations

[日本語](STATUS-jp.md) | [Project README](../README.md) | [Implementation plan](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**Current bounded milestone: v0.0.14/schema 9 runtime readiness.
Implementation is verified locally with Apple Container and on both native CI architectures.
Verified v0.0.13 and earlier results remain historical evidence, not v0.0.14 results.
This is not completion of M0/M1/M2/M3, an MVP, or a production-qualified release.**
The implementation plan describes future requirements, not the current API.
Performance, memory quality, disaster recovery, and full-erasure acceptance
targets remain unmeasured or unqualified. Passing local and CI checks does not
complete these gates.

## Implemented surface

PostgreSQL is the sole application persistence store, including the durable job
queue and lexical projections. There is no external memory database, model
service, queue, or file-based memory index. Janome's packaged dictionary is a
software dependency, not stored application memory.

| Endpoint | Current behavior |
|---|---|
| `POST /v1/observe` | Stores one episode with caller-supplied event time and consent reference. Returns revision `1`; `synthesis_job_id` is `null`, and no job is enqueued |
| `POST /v1/captures` | Atomically commits/reuses one episode and one explicit structured-publication job; `201` returns the episode/job pair, not a published assertion |
| `POST /v1/remember` | Stores an explicitly requested, structured assertion with literal evidence from readable episodes in the same scope |
| `POST /v1/jobs` | Explicitly queues structured memory publication; `202` is a job reference, not completion |
| `GET /v1/jobs/{job_id}` | Returns currently readable state, safe errors/timing, exact input references, and original result revision 1 |
| `POST /v1/jobs/{job_id}/retry` | Creates/deduplicates one child of an owned failed job after full intent and current-access checks |
| `POST /v1/assertions/{memory_id}/revisions` | Appends a full replacement revision to the same assertion using an expected head, explicit intent, reason, and revision-specific episode evidence |
| `POST /v1/entities` | Creates an immutable, evidence-backed, caller-reported entity identity at revision 1 |
| `GET /v1/entities/{memory_id}` | Returns currently readable entity metadata and literal episode evidence |
| `POST /v1/relations` | Creates a typed relation as one canonical assertion between same-scope entity UUIDs |
| `POST /v1/relations/{memory_id}/revisions` | Replaces the target, evidence, and entire valid interval under assertion revision-CAS |
| `POST /v1/graph/expand` | Authenticated read-only, bounded SQL traversal over canonical relation revisions; no `Idempotency-Key` required |
| `POST /v1/checkpoints` | Stores typed state with a branch-head CAS and returns an immutable checkpoint reference/checksum |
| `GET /v1/checkpoints/{checkpoint_id}` | Checks current access and integrity, then returns state, references, epochs, and reconciliation hints |
| `POST /v1/checkpoints/restore` | Copies a compatible checkpoint into a new target branch; never runs code or repeats external effects |
| `POST /v1/tool-effects` | Records/deduplicates an intent within an existing checkpoint run; returns its initial revision reference |
| `POST /v1/tool-effects/{memory_id}/transitions` | Appends a CAS-checked ledger transition; does not call the tool |
| `GET /v1/tool-effects/{memory_id}` | Returns current state, immutable event history, references, HMAC identifiers, and the run-invalidated flag |
| `POST /v1/recall` | Preserves default lexical recall and byte-budgeted packs; v0.0.11 adds opt-in exact vector/hybrid modes with explicit coverage |
| `POST /v1/embedding-inputs` | HTTP 200 read-only canonical embedding input for an authorized episode/assertion revision; no idempotency key |
| `POST /v1/embeddings` | HTTP 201 explicit immutable vector upload under the canonical parent's scope; mandatory caller-owned idempotency key |
| `POST /v1/explain` | Returns the requested assertion revision and its evidence. Omitted revision still means `1`, not latest. Episodes have only revision `1`; no ranking trace API |
| `POST /v1/forget` | Accepts explicit IDs with `preview` or `purge`; no arbitrary selector or `suppress` mode |
| `GET /v1/deletions/{receipt_id}` | Returns an authorized deletion receipt and the unresolved, operator-managed backup status |
| `GET /v1/capabilities` | Reports current features, limits, and unavailable capabilities; requires authentication |

Typed request and response models define the OpenAPI schemas exposed through
`/docs` and `/openapi.json`; no generated schema file is required.
`/healthz` reports process liveness following startup validation, not continuous
PostgreSQL readiness. `/readyz` adds the bounded check below, outside `/v1`.

## Runtime readiness

**v0.0.14/schema 9: verified locally and on native Linux amd64/arm64.**
Health probes are public, unauthenticated paths, not Native memory resource
routes. `GET /healthz` retains exactly `{"status":"ok"}` after successful startup
without DB calls. New `GET /readyz` ignores supplied authorization headers and
selects no tenant/principal. Expected readiness responses are:

| HTTP status | Exact body | Headers |
|---|---|---|
| `200` | `{"status":"ready"}` | `Cache-Control: no-store`; generated UUID `X-Request-ID` |
| `503` | `{"status":"not_ready"}` | `Cache-Control: no-store`; generated UUID `X-Request-ID` |

OpenAPI describes both responses with typed `ReadinessStatus`, not Native
`ErrorBody`. A wrong HTTP method returns 405 without running the readiness check.

No reason, DSN, token, payload, identity, or schema inventory appears in the
public body. Each admitted probe calls existing `validate_runtime` on a fresh
connection using the **same runtime DSN**—never admin credentials or fallback.
Validation sessions, including API startup and worker validation, explicitly set
`default_transaction_read_only = on`. Validation uses at most four explicit SQL
statements: one `SET`, then three `SELECT` statements for role catalogs, schema
history, and extension catalogs. Read-only is confined to this dedicated
validation connection; later Native mutations remain writable:

- Reject superuser, `BYPASSRLS`, and table ownership/owner-role membership in
  `memory` or `memory_ops`, including `NOINHERIT` membership.
- Require exact migration history `[1,2,3,4,5,6,7,8,9]`.
- Require `vector` **0.8.6 in `public`**.

There are no memory-payload reads, tenant locks, audit/epoch/job/receipt writes,
migrations, provider calls, successful-result cache, background checks, or retries.
Existing fail-closed startup behavior is retained.
`RuntimeValidationError` subclasses `RuntimeError`, retaining previous startup
messages while identifying expected configuration drift with a static code.

### Admission, cancellation, and diagnostics

One active check is admitted per API app/process. A concurrent request returns
503 immediately with log reason `probe_busy`, **without a second connection,
waiting, or cached success**. This is per-process admission, not a global rate
limit or request-flood qualification.
The fixed `asyncio` active-check timeout budget is **5.0 s**; DB connect,
statement, and lock budgets remain **5 s**. Cancellation/connection cleanup can
add latency: this is **not a hard wall-clock SLA**. Cancellation propagates and
releases the gate/connection; no retry is performed.

Expected failures log `readiness_unavailable` with generated `request_id` and
a static `reason`: `runtime_role_invalid`, `schema_unavailable`,
`schema_version_mismatch`, `extension_version_mismatch`, `probe_busy`, or an
exception class name. The readiness diagnostic includes no raw error string,
traceback, DSN, credentials, or payload. `psycopg.Error` and `TimeoutError` become
503. Unexpected programming exceptions, including an ordinary `RuntimeError`,
are **not** caught and disguised as not-ready responses.

### Interpretation and compatibility

This is a **point-in-time connection/runtime-role/schema/vector contract**, not:
complete principal authorization; table-grant/RLS-policy integrity auditing;
a write transaction, writability, or primary check; ongoing JWT verification;
tokenizer/provider readiness; backlog/load/HA/DR/performance/quality/production
qualification. A SELECT-only database can pass these bounded checks.
Resource routes do not call the probe and do not gain a permanent fail-closed
gate after drift. Readiness helps operators stop traffic, **not replace the
authorization boundary**; existing Native authorization remains enforced.

Use `/healthz` for liveness, not dependency readiness that can cause restart
storms. Configure orchestrator failure/recovery thresholds, including for busy
503 responses, and restrict/rate-limit public probes at the deployment perimeter.
No Kubernetes, Compose, or Docker `HEALTHCHECK` configuration is added.

The stage becomes **`m2-runtime-readiness`**. Authenticated capabilities add
`health_probes` metadata: `liveness: "/healthz"`, `readiness: "/readyz"`,
`readiness_timeout_seconds: 5.0`, `readiness_max_in_flight_per_process: 1`.
The public memory surface stays **24 resource methods**; health paths are
excluded from SDK resource-route coverage. No SDK/MCP/hook probe method is added.
All matching adapters require **service 0.0.14 / API v1 / schema 9**.
This is application-only: no new migration, dependencies, or pinned-image changes.
See [operations](operations/README.md#runtime-readiness) and
[ADR 0014](adr/0014-runtime-readiness.md).

## Scope-access administration

**Retained scope-access contract; v0.0.14 local and native checks passed.**
`pg-agmemory scope-access get|set|revoke --tenant-id UUID --scope-id UUID --principal-id UUID`
is a privileged administrative CLI, not an agent tool or runtime API.
It targets **existing same-tenant** tenant/scope/principal records; it never
creates identities or scopes. IDs are parsed as UUIDs and normalized for the
lock key. Select approved opaque IDs from trusted administration, never retrieved text.
The public Native memory surface remains 24 resource methods: **no HTTP, MCP,
or Python SDK administration method** is added.

### Authority and compare-and-swap

Only `PGAG_ADMIN_DATABASE_URL` is accepted. The connected DB role must have
`rolsuper` or `rolbypassrls` **and appropriate SQL table privileges**.
Runtime credentials are rejected even for `get`; RLS bypass does not itself
grant table privileges. There is no JWT, `--subject`, `--once`, or runtime-URL fallback.
`get` uses no `FOR UPDATE`: a nonowner `BYPASSRLS` role can inspect with
`USAGE` on `memory` and `SELECT` on schema history, tenant, scope, principal,
and `scope_member`. Mutation privileges additionally include `UPDATE` on the
tenant, the applicable `SELECT`/`UPDATE`/`INSERT`/`DELETE` on `scope_member`,
and `USAGE` on `memory_ops` plus `INSERT` on `scope_access_event`.
The session advisory barrier is still required for reads; read-only table
privileges do not remove the mandatory RLS-bypassing role requirement.
Before operation, the CLI checks the role, exact schema history **1–9**, and
`vector` **0.8.6 in `public`**.

| Operation | Required intent | Semantics |
|---|---|---|
| `get` | Three IDs only; no mutation options | Current membership and tenant epoch, evaluated under the response-drain barrier |
| `set` | `--expected-access-epoch`, `--permissions`, and exactly one expiry choice | Full replacement of permissions and expiry, not merge |
| `revoke` | `--expected-access-epoch`; no permission/expiry options | Delete the membership row; already absent at the current epoch is a no-op |

Every mutation requires an explicit expected epoch in **1–9223372036854775807**.
Compare against the **tenant-wide** `access_epoch` under lock, before checking
whether the requested state already matches. A stale expectation always yields
`access_epoch_conflict`; an unrelated scope change can conflict too.
No automatic retry, HTTP `Idempotency-Key`, mutation receipt, or receipt queue exists.

`set` accepts distinct flags from `read`, `write`, `delete`, or `admin` alone.
Duplicates and mixing `admin` with other flags are rejected. Write-only and
delete-only configurations are allowed as DB flags; they do not override
Native operation requirements, including read access where required.
Permissions are returned in canonical **read, write, delete, admin** order;
reordering is equivalent and does not create a change.
Choose **`--expires-at` with a timezone-aware ISO timestamp or `--no-expiry`**.
Naive timestamps are invalid; an expiration at/before the DB clock sampled
after acquiring the lock is rejected. Permanent access must be explicit:
omitting expiry never silently removes it. Expiry-only changes advance the epoch.

### Results, expiry, and audit

Success is **one JSON line, with no result wrapper**:

```text
{
  operation, tenant_id, scope_id, principal_id, access_epoch, changed,
  membership_exists, permissions, expires_at, effective_permissions, evaluated_at
}
```

Absent membership has `membership_exists: false`, `permissions: []`, and
`expires_at: null`. A legacy empty-permission row still has
`membership_exists: true`. `effective_permissions` is empty when expired at the
DB `evaluated_at`, all four flags for effective `admin`, otherwise the configured
flags. This is **point-in-time membership interpretation, not complete Native
action authorization**; access can expire immediately after the result.
Natural expiration does not advance `access_epoch`, delete payload/audit, or
drain in-flight HTTP. Use an explicit revoke/barrier when strong drain is needed.

Retained **`009_scope_access.sql`** creates privileged-only
`memory_ops.scope_access_event`, with forced RLS and **no runtime policies/grants**.
The primary key is `(tenant_id, access_epoch)`; same-tenant foreign keys bind the
target scope/principal. Events record `set`/`revoke`, before/after permission and
expiry values, DB `recorded_at`, and `database_role` captured from `current_user`.
`recorded_at` uses the database clock; `evaluated_at` is response-only, not an audit field.
This identifies the executing database role, not an impersonated end-user actor.
They contain no plaintext memory content, external subjects, or DSNs.
Existing ACL rows are preserved; **prior manual changes receive no audit backfill**.
The command adds no implicit ownership or purge behavior.
**Only actual changes** atomically update membership, increment the tenant epoch,
and append an audit event. Reads, no-ops, conflicts, and rolled-back changes
create no event or epoch advance. Epoch exhaustion rejects a change.
Privileged administrators can alter the database: this is **not tamper-proof,
a standalone revocation recovery ledger, or a DR solution**.

### Response-drain barrier and failures

A dedicated synchronous admin connection uses autocommit and **5 s connect,
statement, and lock timeouts**. It acquires the API/worker's same **session**
advisory lock, `pg_advisory_lock(hashtextextended(canonical_tenant_uuid, 0))`.
Hold it through the membership transaction's commit **and the CLI JSON stdout
flush**; closing the connection releases it. `get` and mutation no-ops also
acquire the barrier. Do not pool this connection or substitute an xact-only lock.
An earlier slow response can block administration; a lock timeout fails without
change. Mid-change failure rolls back membership, epoch, and audit together.
Failure cleanup closes the session and releases its lock.
Cooperating same-version API clients can stay online during these commands;
**migration still requires stopping/draining old processes**.

Syntax, model, and configuration errors use static sanitized stderr, exit **2**,
and no JSON. Invalid CLI syntax reports `invalid_scope_access_arguments`;
missing `PGAG_ADMIN_DATABASE_URL` also produces a static stderr diagnostic,
exit 2, and no stdout. DB/domain operational failures use stdout, exit **1**:

```json
{"error":{"code":"access_epoch_conflict","outcome_unknown":false}}
```

The error catalog is `admin_role_required`, `admin_privilege_required`,
`schema_unavailable`, `schema_version_mismatch`, `extension_version_mismatch`,
`not_found`, `access_epoch_conflict`, `access_epoch_exhausted`, `invalid_expiration`,
`admin_database_unavailable`, and `admin_database_error`.
No raw DB error, DSN, credentials, or private data is printed.
Commit transport failure is conservatively `outcome_unknown: true`; a failure
before commit attempt is false. Cancellation, process kill, or lost stdout can
also leave a mutation outcome unknown. **Do not blindly replay a stale CAS**:
inspect with `get` and privileged audit, then explicitly authorize a new operation
using the freshly observed epoch. There is no automatic retry.

The barrier cannot retract already-delivered context. Restoring latest ACL and
deletion records remains manual; granting access cannot resurrect purged data.
The historical v0.0.13 stage was `m2-scope-access`; the current stage is
`m2-runtime-readiness`. Capabilities retain
`scope_access_administration` metadata:
`transport: "admin-cli"`, `command: "scope-access"`,
`compare_and_swap: "tenant_access_epoch"`, `audit: "database_role"`.
PostgreSQL 18.6/pgvector 0.8.6 pinned images and Python dependency versions are
unchanged. See [operations and example](operations/README.md#scope-access-administration)
and [ADR 0013](adr/0013-scope-access.md).

## Python SDK

**Retained SDK contract; v0.0.14 local and native checks passed.**
`from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError` exposes an
async-only client for the existing public Native memory resources. Import
request/response types from `pg_agmemory.models`; requests are revalidated at call
time, including mutable model instances, and responses use those same typed models.
The package includes a PEP 561 `py.typed` marker.

### Installation, lifecycle, and authority

Install the matching checkout with `python -m pip install '.[sdk]'`.
`pg-agmemory[sdk]` adds only `httpx==0.28.1`; **the core distribution still includes
FastAPI, psycopg, and Janome**. This is not a separately published lightweight
SDK or a PyPI publication claim. SDK import without HTTPX raises a clear static
`ImportError`; HTTPX supplied by `mcp`/`hook` also works. Detection is dependency
availability, not the identity of the selected extra. Docker test/runtime include
`sdk` alongside `mcp` and `hook`; core-only/hook-only/sdk-only checks are implemented,
including absence of the MCP SDK in hook-only and sdk-only installations.
All three genuine noneditable wheel-install checks and packaged `py.typed`
verification passed locally and on both native architectures. There are no Python dependency upgrades.

Construct with explicit `AsyncMemoryClient(api_url, api_token)`, never untrusted
per-call configuration. `NativeSettings` requires a fixed HTTPS origin or
loopback HTTP origin, without application path, userinfo, query, or fragment.
Constructor validation checks bearer-token **shape only** and retains sanitized
`ValueError` configuration errors, not `MemoryClientError`; the server performs
authentication and authorization. Request scopes narrow current server ACLs,
never select another identity or grant access.

Use `async with ... as memory:` exactly once per client instance. Entry creates
an owned HTTP client and performs a mandatory authenticated capabilities probe
requiring exact **service `0.0.14` / API `v1` / schema `9`** before resource use.
Failed entry closes owned resources in `finally`. Calls before entry or after exit
raise `client_not_open`; re-entry raises `client_already_used`.
Exit releases connections only: **it does not forget data**.
The caller must await its outstanding tasks, or cancel and await them, **before
exiting the context**. Client close is not a request scheduling/cancellation
manager or a DB rollback. Cancellation of in-flight mutations still needs reconciliation.
No automatic environment loading, retry, cache, DB credentials, provider call,
host registration, delegation, or automatic job is introduced.

### Typed resource methods

All methods are asynchronous. Body names below are native request models; ID
arguments are UUIDs, not arbitrary paths. Every mutation requires a caller-owned
keyword-only `idempotency_key`; this includes `forget` preview and purge,
both HTTP 202. Other read-only methods need no key and preserve HTTP 200;
writes retain their Native 201/202 status. A job receipt never means publication completed.

| Method / request | Typed result |
|---|---|
| `observe(Observe)` | `ObserveResult` |
| `capture(Capture)` | `CaptureResult` |
| `remember(Remember)` | `RememberResult` |
| `revise_assertion(UUID, ReviseAssertion)` | `RevisionResult` |
| `recall(Recall)` | `RecallResult` |
| `explain(Explain)` | `EpisodeExplanation \| AssertionExplanation` |
| `forget(Forget)` | `DeletionPreview \| DeletionResult`, selected by request mode |
| `get_deletion(UUID)` | `DeletionProgress` |
| `embedding_input(Explain)` | `EmbeddingInput` |
| `put_embedding(PutEmbedding)` | `EmbeddingReceipt` |
| `create_entity(CreateEntity)` | `EntityReceipt` |
| `get_entity(UUID)` | `EntityDetail` |
| `create_relation(CreateRelation)` | `RememberResult` |
| `revise_relation(UUID, ReviseRelation)` | `RevisionResult` |
| `expand_graph(ExpandGraph)` | `GraphResult` |
| `enqueue_job(EnqueueJob)` | `JobReceipt` |
| `get_job(UUID)` | `JobDetail` |
| `retry_job(UUID, EnqueueJob)` | `JobReceipt` |
| `create_checkpoint(CreateCheckpoint)` | `CheckpointReceipt` |
| `get_checkpoint(UUID)` | `CheckpointEnvelope` |
| `restore_checkpoint(RestoreCheckpoint)` | `CheckpointEnvelope` |
| `plan_tool_effect(PlanToolEffect)` | `ToolEffectReceipt` |
| `get_tool_effect(UUID)` | `ToolEffectDetail` |
| `transition_tool_effect(UUID, TransitionToolEffect)` | `ToolEffectReceipt` |

This covers public memory resource routes, **not CLI admin/worker functions**.
Capabilities probing is internal; no public health, OpenAPI-download, or raw
arbitrary-request method is added. There is no sync client, TypeScript SDK, or
token-refresh flow.

### Bounds, errors, and recovery

The shared Native HTTP transport retains MCP/hook bounds: **20 s per exchange,
10 s I/O / 5 s connect**, at most **4 connections**, verified TLS,
no proxy environment and no redirects. Response limit is **2 MiB**; requests
are **256 KiB**, except SDK `create_checkpoint` at **1 MiB**. This does not raise
MCP/hook request limits.

`MemoryClientError` is an alias of existing `AdapterFailure`. Inspect
`exc.error.code`, `.retryable`, `.outcome_unknown`, `.native_status`, and
`.request_id`; diagnostics are sanitized, never raw response/input/token content.
The SDK allows the catalog of current Native domain errors; MCP/hook keep their
existing restricted safe-code set. Unknown server codes become `native_api_error`.
Invalid call-time request models, path UUIDs, and idempotency keys fail **before
dispatch** as sanitized `invalid_request`, with `outcome_unknown: false`.
Validation snapshots the request before the first outbound network await;
subsequent mutation of the caller's model does not change that dispatched body.
Pydantic request-model construction can separately raise `ValidationError`;
that happens outside the SDK call and is not converted to `MemoryClientError`.
Ordinary Pydantic errors may contain private input details; do not log them.
Keys must be **1–256 visible ASCII characters, without trimming**.

Retain each mutation's exact key and body before dispatch. Network errors, 5xx,
malformed responses, wrong success statuses, or invalid success bodies are
conservatively outcome-unknown for mutations. The SDK does not retry, generate
replacement keys, or infer that no commit occurred. `retryable` is information,
not an automatic retry instruction. Reconcile with the **same key and body**;
current ACL, deletion, revision, and replay guards still apply.
Cancellation propagates rather than becoming `MemoryClientError`: an in-flight
mutation must likewise be treated as unknown and reconciled. Cancellation is
**not rollback**.

Returned memory remains evidence, not trusted instructions or guaranteed
current facts. Whole-JSON UTF-8 byte budgeting, explicit incomplete coverage,
current ACL checks, and purge/host/backup/WAL limitations are unchanged.
The historical v0.0.12 stage was **`m2-python-sdk`**; capabilities retain `python_sdk`
metadata (`installation: "sdk-extra"`, `async: true`, `automatic_retry: false`),
not a server endpoint. See the [practical example](../README.md#python-sdk),
[operations](operations/README.md#python-sdk-operations), and
[ADR 0012](adr/0012-python-sdk.md).

## Pgvector exact and hybrid retrieval

**Implemented and verified in v0.0.11/schema 8.**
The lexical default remains unchanged. This adds explicit, provider-independent
vector storage and exact/hybrid ranking, not automatic embedding generation or
qualified semantic retrieval. PostgreSQL remains the only application persistence store.

### Canonical input and explicit upload

Read-only **`POST /v1/embedding-inputs`** accepts the existing Explain body
`{memory_id, revision}`; omitted revision means **1, not latest**.
It needs no `Idempotency-Key` and accepts only currently readable episodes and
assertion revisions, including relation assertions—not entities, jobs, checkpoints,
or tool effects. The returned canonical input is:

```text
{memory_id, revision, type, text, input_digest, input_format: "memory-content-v1"}
```

Episode `text` is its normalized content. Assertion `text` is exactly
`subject / predicate: value`, as `MemoryItem.content`, including a relation
assertion's display value. IDs/times are not inserted into embedding text.
`input_digest` is SHA-256 of UTF-8 `text`; it is not a model-quality measure,
external verification, or proof that the supplied vector was generated from that text.
This is private canonical content: **do not log it or send it to a third party
without explicit approval**. Documentation fixtures are synthetic and invoke no model.

**`POST /v1/embeddings`** requires a caller-owned `Idempotency-Key`:

```text
{
  memory_id, revision: 1, input_digest: <64 lowercase hex characters>,
  model: {
    name: <1–256 characters>, revision: <1–256 characters>,
    dimensions: 768, distance_metric: "cosine", normalization: "l2-f32-v1"
  },
  values: <exactly 768 finite JSON numbers>
}
```

Canonical `revision` defaults to 1; model `revision` is a separate required string.
Identity/scope are derived from the canonical parent, with current **read and write**
authorization. Model metadata is **caller-declared**, not a registry, trusted
origin, provider attestation, or semantic-quality claim. Model spaces separate the
full name/revision pair; equal dimensions never make different spaces compatible.
Dimensions, metric, and normalization are fixed as above.

Server L2 normalization computes in float64, then stores pgvector float32 values.
Reject zero, non-finite, or un-normalizable vectors, booleans, numeric strings,
and any length other than 768. There is no truncation or dimension coercion.
The submitted digest must match the exact currently authorized canonical revision:
otherwise **409 `embedding_input_mismatch`**.

| Situation | Result |
|---|---|
| Same canonical revision/model, same normalized float32 vector and digest | Deduplicate, including across HTTP keys |
| Different vector for the same parent revision/model | **409 `embedding_conflict`**; use a new model revision for replacement |
| Same HTTP key, different validated request | **409 `idempotency_conflict`**; do not change keys to resolve an uncertain response |
| Ninth model version for one canonical revision | **422 `embedding_limit_exceeded`** |
| Existing duplicate at the eight-model limit | Still allowed |

The cap is **8 model versions total per canonical revision**, not eight per model
name. Request hashing preserves the validated values **before L2 normalization**:
rescaling a vector with the same HTTP key conflicts even if its normalized
projection would match. Keep the same key and body after an uncertain response.
A successful upload returns `{memory_id, revision, model, input_digest}`,
with **no independent embedding object ID**. Projection creation, idempotency
receipt, and audit are atomic. Same-key replay checks that both the canonical
parent remains live/readable and the projection still exists.
The **stored idempotency result contains only `{memory_id, revision}`**:
no plaintext input digest, model names, or vectors are retained in that receipt.
The full response model/digest is rebuilt from the currently readable canonical
input and matching projection; request HMACs and opaque anchors persist.
If the parent is live but an administrator removed only the projection, replay
returns **409 `embedding_unavailable`**, not a reconstructed projection.
No endpoint deletes only a projection, and no provider/rebuild/generation runs automatically.

### Recall modes, exact ranking, and coverage

Recall adds `retrieval_mode` (default **`"lexical"`**) and `vector_query`
(default **null**). A vector query contains the same `model` and 768-value format
as an upload and uses the same normalization/validation rules.
`retrieval_mode` is separate from the existing implicit/explicit `mode`; it
selects a retrieval path, not identity or authority.

| Mode | Text `query` | `vector_query` | Ranking |
|---|---|---|---|
| `lexical` | Existing empty browsing/nonempty FTS behavior | Must be null | Existing lexical semantics |
| `vector` | Must be empty | Required | Exact cosine distance |
| `hybrid` | Must be nonempty | Required | Lexical/vector reciprocal-rank fusion |

Do not silently ignore text, select a different model, or fall back to another mode.
Existing scopes, `as_of`, `known_at`, byte budget, item limits, and `search_profile`
rules remain. Historical revision vectors may be populated separately; a vector
for another revision is not a substitute for the revision selected by time.
Past reads never override **current ACLs or deletion**.
Omitted `as_of`/`known_at` are frozen once per recall before selection and coverage;
both paths use those resolved times, so a future boundary cannot split them.
Explicit time values are unchanged.

Vector ranking operates over **`MATERIALIZED` currently authorized and time-eligible
canonical candidates for the requested model**, with those filters applied
**before distance/ranking**. It is exact cosine, not ANN/HNSW, approximate
neighbor expansion, or a tenant/scope-widening search.
Hybrid uses the existing FTS rank and exact vector rank, both deterministic, with
**RRF k=60**:

```text
fusion_score = 1 / (60 + lexical_rank) + 1 / (60 + vector_rank)
```

An absent path contributes zero. A lexical match without a vector may participate
in hybrid results, but missing-vector coverage must remain explicit.
Missing **any eligible visible projection** for the requested model sets
`coverage.vector_incomplete: true`; hidden/ineligible items never contribute to
coverage or counts. Lexical defaults set `vector_incomplete: false`.
Existing Japanese `lexical_incomplete` applies only to the lexical/hybrid path.
`retrieval_complete` is false if either active path is incomplete.
For an empty selection with no candidates, missing active-index coverage yields
`index_incomplete`; a truly empty authorized corpus yields `not_found`.
Candidates that cannot fit retain `budget_exhausted`; nonempty results retain
null `empty_reason` while still exposing incomplete coverage.
Never turn missing-index retrieval into an unqualified empty success.

The additive defaults are `MemoryItem.retrieval: null`,
`RecallResult.retrieval_mode: "lexical"`, `embedding_model: null`, and
`coverage.vector_incomplete: false`. Non-null `MemoryItem.retrieval` contains
`method` (`exact_cosine` or `rrf-60`), `lexical_rank`, `vector_rank`,
`vector_distance`, and `fusion_score`. Rank/distance/fusion fields are nullable
when their path/scoring method does not supply them; vector-only fusion score is null.
UUID breaks ties in the actual computed distance/score. This does **not** promise
bitwise-identical arbitrary floating-point results or rankings across all CPUs.
These fields are **additive response/schema changes**: unchanged default lexical
semantics do not promise byte-for-byte identical HTTP JSON or generated MCP schemas.
These are **ranking measurements, not confidence, calibration, or truth**.
The context-pack format and **whole compact JSON UTF-8 byte budget** remain unchanged,
as do reported assertions and null/uncalibrated confidence.
Exact SQL remains subject to the existing **5 s DB statement timeout**, not a
performance SLO. Retrieval quality, performance, and untrusted-vector robustness are unqualified.

### Projection lifecycle, packaging, and adapters

New **`008_pgvector.sql`** requires **`vector` 0.8.6 in `public`**, refuses an
existing extension with the wrong version/schema, and adds per-episode and
per-assertion-revision projections with `ON DELETE CASCADE`, forced RLS, and
runtime **SELECT/INSERT only**. There is **no existing-data embedding backfill**.
API, worker, and `migrate` validate the extension version/schema even when
schema 8 is already recorded; an already-applied migration does not bypass this guard.
Canonical parent purge removes vectors, digests, and declared model names alongside
lexical projections. They are not new provenance vertices or deletion-count objects.
There is no standalone model registry retaining this metadata.
Retained canonical source/idempotency anchors still prevent resurrection.
The Native response-drain boundary and incomplete host/backup/WAL erasure guarantees remain.

The adopted prebuilt DB image is
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`.
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6) is the verified
stable release of **2026-07-29**, official tag commit
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`
([pinned changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)),
under the **PostgreSQL License**. Preserve its upstream license.
Both final native images retain `/usr/share/doc/pgvector/LICENSE`, verified
byte-identical to the pinned upstream license, SHA-256
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`.
Artifact inspection verified both native amd64/arm64 final images: installed
PostgreSQL **18.6-1.pgdg12+2**, native ELF, and `vector.control` **0.8.6**.
**PostgreSQL stays 18.6, but the upstream DB image/base digest changes** from the
old library PostgreSQL image. This is a new pinned DB profile, not an unchanged image.
There is no new DB Dockerfile, source-build, or host-APT procedure in the
implemented profile. An operator-managed PostgreSQL alternative must provide the
same extension version/schema; no such host-install workflow is supplied here.
In historical v0.0.11, Python dependencies remained unchanged apart from project-version metadata:
raw parameter-bound vector casts need no pgvector Python package.
Artifact verification is not application/migration/CI validation or attestation
of any caller-declared embedding model.

MCP still exposes **four tools**. Generated Recall arguments accept the new modes
and inline query vectors, but no embedding-input/upload tool is added.
The fixed-startup hook stays **lexical-only and read-only**; event JSON cannot pass
`retrieval_mode`/`vector_query`. It also rejects a Native response with non-lexical
`retrieval_mode`, non-null `embedding_model`/item `retrieval`, or true
`coverage.vector_incomplete`; it does not silently downgrade unexpected vector output.
Observe, capture, jobs, and workers do not generate
embeddings or call providers. Both MCP protocol eras remain; startup matching is
**service `0.0.14` / API `v1` / schema `9`** for v0.0.14.
Capabilities add `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"`. The v0.0.11 stage was `m2-pgvector-retrieval`;
embedding input returns HTTP 200 and upload/replay returns HTTP 201. Full M0–M3/MVP/production/DR/erasure/
performance/quality gates remain incomplete.
See [ADR 0011](adr/0011-pgvector-retrieval.md),
[schema-8 upgrade](operations/README.md#schema-8-pgvector-upgrade), and
[the synthetic example](operations/README.md#synthetic-vector-example).

## Atomic structured capture

**Retained v0.0.10 contract, also verified in v0.0.11.** `POST /v1/captures` requires
`Idempotency-Key` and one body:

```text
{episode: <unchanged Observe>,
 memory: {subject, predicate, value, evidence_quote, explicit_intent: true,
          valid_from: <aware timestamp or null>, valid_to: <aware timestamp or null>}}
```

The request model is `Capture(episode: Observe, memory: CapturedMemory)`.
The episode is the existing `Observe` model, not an alternate capture format.
Exactly one structured memory intent is accepted, not a list or free-text
extraction request. Memory fields retain the synchronous Remember rules:

| Memory field | Contract |
|---|---|
| `subject` | 1–256 characters |
| `predicate` | Matches `^[a-z][a-z0-9_]{0,63}$` |
| `value` | 1–65,536 characters |
| `evidence_quote` | One 1–4,096-character literal substring of the normalized captured episode |
| `explicit_intent` | Must be `true` |
| `valid_from`, `valid_to` | Optional timezone-aware timestamps or null/unbounded; when both are present, start must be before end |

Memory accepts **no scope, evidence IDs, or identity fields**. The transaction
derives scope from the episode and binds its single evidence reference to that
episode's memory ID/revision 1. Current Native authentication, tenant/scope
read/write authorization, RLS, normalization, and source-event deduplication
remain authoritative; nothing silently widens scope or tenant. Literal matching
establishes provenance, **not semantic support or truth**. Publication retains
`epistemic_status: "reported"` and uncalibrated confidence (`score: null`,
`method: "uncalibrated"`).

### Commit and publication are separate

**HTTP 201** returns `CaptureResult`:
`{memory_id: <episode UUID>, revision: 1, synthesis_job_id: <job UUID>}`.
`memory_id` is **not an assertion ID**, and `synthesis_job_id` is a job reference,
not a promise that synthesis occurred. The episode plus at most one
`structured_remember` / `structured-remember-v1` job are committed atomically.
Capture may reuse an existing job, including a terminal one. **201 does not
guarantee a newly created or pending job**; GET is authoritative for current status.
Use `GET /v1/jobs/{synthesis_job_id}` for fresh state and the existing
fixed-subject worker for eventual assertion publication.

The existing **100 pending/running jobs per scope**, **five attempts per job**,
leases, access/deletion epochs, and publication fencing remain unchanged.
Worker publication remains a separate atomic transaction; enqueue does not set
the assertion's server-recorded publication time.
`POST /v1/observe` retains unchanged `ObserveResult`, including literal-null `synthesis_job_id`,
and **never automatically enqueues a job**. Explicit `POST /v1/jobs`, synchronous `/v1/remember`,
and pure Observe/Remember normalized serialization/HMAC remain byte-compatible.

### Deduplication and transaction boundary

| Request situation | Result under current access/deletion checks |
|---|---|
| Same capture key and normalized body | Same episode/job pair |
| Changed body with the same capture key | `409`, no new partial writes |
| Different HTTP keys, same episode/intent/principal | Both IDs deduplicate to the same pair |
| Previously observed identical event | Reuse its episode; the explicit wrapper supplies the job intent |
| New key and different explicit memory intent | May create a separate job using the same retained episode; intentional, not semantic deduplication |
| Another authorized principal using the same event | Episode source deduplication remains; job identity/worker ownership are independent |
| Same source-event identity, changed episode body | `409`, no new partial writes |

One transaction covers episode and lexical projection, job input/control/identity,
idempotency receipts (including the outer capture receipt), and audit.
Transaction failure after either write or the outer receipt rolls back **all new changes**.
An episode that existed independently before capture remains on failure; no
partial new job survives. There is no distributed transaction or external provider.

Retained opaque idempotency/source/job identity anchors include internal
composition keys, derived by server HMAC from the caller's key. They are not
client-supplied fields, autogenerated MCP caller keys, or a change to caller-owned
HTTP key reuse. Retained anchors are not fresh memory content or full-erasure proof.

### Replay, deletion, and failed-job retry

**Current ACLs and deletion override replay for both returned IDs**, including
an exact capture replay after API process restart. The pair is historical;
GET checks fresh job state rather than capture silently changing the pair.
Shared replay checks a non-null `synthesis_job_id` as well as `memory_id`;
the unchanged Observe result still has no job reference.

| Purge target | Effect on capture |
|---|---|
| Episode | Closes dependent jobs and assertion descendants through existing purge dependencies |
| Job alone | Leaves the episode and independently stored published output; old capture replay and a new key for the same intent return `404`, not a recreated job identity |
| Published result assertion | Removes the dependent job, keeps its source episode, and invalidates the old pair |

A **new explicit different intent on a retained source** is still allowed by
existing job semantics. Capture is not a permanent whole-source seal.
Failed jobs use existing `POST /v1/jobs/{job_id}/retry` with the original complete
`EnqueueJob` intent and a caller-owned key. Retry creates/reuses a child under
existing rules; replaying capture returns the **original failed job reference**,
not that child, even after the child has been created. See [durable jobs](#durable-jobs).

### Scope and capabilities

The historical v0.0.10 API stage was **`m2-atomic-capture`**. Retained capability feature
**`atomic_structured_capture`**. The exact `atomic_capture` metadata fragment is:

```json
{
  "atomic_capture": {
    "endpoint": "/v1/captures",
    "max_jobs": 1,
    "recipe_version": "structured-remember-v1",
    "automatic_capture": false
  }
}
```

This describes at most one job per explicit capture, not automatic capture.
The stage label does not complete M2 or any other acceptance gate.

Capture is a **Native resource also covered by the SDK**, not a fifth MCP tool. Recall-hook stays read-only;
neither adapter automatically captures. MCP/hook startup requires exact
**service `0.0.14` / API `v1` / schema `9`**. The retained schema-8 migration is separate
from the retained capture semantics. Capture does not generate embeddings,
invoke LLM/providers, extract intent, perform natural-language/automatic synthesis,
or establish semantic quality.
The tenant HTTP response-drain barrier is unchanged: no atomic host-context
delivery, retraction, or host/backup/WAL/full-erasure guarantee follows.
See [ADR 0010](adr/0010-atomic-capture.md) and the
[operator example](operations/README.md#atomic-structured-capture-operations).

## Local stdio MCP

v0.0.8 introduced `pg-agmemory mcp`, a **stdio-only, trusted local Native API
client**, not a second persistence or authorization service. Optional
`pg-agmemory[mcp]` pins official `mcp==2.2.0` and `httpx==0.28.1`; repository
v0.0.14 Docker test/runtime stages retain `mcp`, `hook`, and `sdk` extras.
The extracted shared bounded Native HTTP client must retain all MCP invariants
below. Historical v0.0.9 checks passed locally and on both native Docker
architectures. Historical v0.0.10/v0.0.11 and final local/native v0.0.12 checks passed.
Shared `NativeSettings` additionally parses origins with `httpx.URL`, rejecting
control characters and invalid IDNA before transport. No remote MCP HTTP/SSE listener,
OAuth, delegated caller identity, semantic cache, or response cache is provided.

### Tools and Native semantics

Exactly four tools derive input/output JSON Schemas from the Native Pydantic
models rather than maintaining a separate handwritten request contract:

| Tool | Input wrapper | Native route / success status |
|---|---|---|
| `memory_recall` | `{request: <Recall>}` | `POST /v1/recall` / `200` |
| `memory_remember` | `{request: <Remember>, idempotency_key: "..."}` | `POST /v1/remember` / `201` |
| `memory_explain` | `{request: <Explain>}` | `POST /v1/explain` / `200` |
| `memory_forget` | `{request: <Forget>, idempotency_key: "..."}` | `POST /v1/forget` / preview and purge both `202` (unchanged) |

`request` contains the existing Native body, not a new natural-language format.
Remember still requires `explicit_intent: true`, structured fields, and literal
same-scope episode evidence. Episode capture remains Native `observe`; it is
not an MCP tool. Jobs, graph, revisions, checkpoint/effect execution, and deletion
receipt lookup are not additional MCP tools.
Recall retains current scopes/ACLs, temporal selection, evidence/coverage,
`search_profile: "simple-v1"` by default, and explicit
`"ja-janome-0.5.0-v1"` opt-in. `tokenizer_id: "utf8-bytes-v1"` and the Native
`token_budget` field still mean **UTF-8 bytes, not model tokens**.
Explain's omitted revision remains **1, not latest**. Memory text is untrusted
evidence, not instructions or verified current external truth.

Both mutation wrappers require `idempotency_key`: **1–256 visible ASCII
characters (`0x21`–`0x7e`, no whitespace)**, including forget preview.
Whitespace is rejected, not trimmed; the key is not rewritten. Exactly 256
characters is accepted and 257 is rejected. The adapter forwards it as Native
`Idempotency-Key`. The caller must retain and
reuse the **same key plus the same body after uncertainty, across stdio restart
and token refresh**. No autogenerated keys, automatic retries, or adapter
durable retry store exist. A changed body with the same key can conflict; a new
key is not uncertainty recovery. Native idempotency references are historical,
not fresh reads: current authorization and deletion override replay. A replay
must not resurrect purged data. MCP session/request IDs are neither durable
memory run IDs nor HTTP idempotency keys.

### Results, failures, and bounded transport

Successful tool calls return
`structuredContent: {result: <validated Native result>, error: null}`.
Tool failures return `isError: true` and
`structuredContent: {result: null, error: {code, retryable, outcome_unknown,
native_status, request_id}}`. `native_status` and the validated Native UUID
`request_id` are nullable; the latter is not an MCP request ID.
Short text accompanies the structured result but does not duplicate evidence or
echo raw request/response bodies, URLs, or credentials. Native errors use safe
codes; invalid responses are not passed through as evidence.

Transport failure/timeout, Native 5xx, and invalid or unexpected responses on a
mutation are conservatively **`outcome_unknown: true`**. An API commit may already
have happened; never describe these as rolled back. `retryable` is only a hint,
not an automatic retry, rollback proof, or permission to change the key/body.
Local validation failures occur before HTTP submission. An interrupted stdio
session can lose the acknowledgement even if the Native API completed the write.

Each HTTP exchange is bounded by **20 seconds total**, **10 seconds I/O**,
**5 seconds connect**, and a pool of **4 connections**. The serialized Native
request body is limited to **256 KiB** and the received HTTP response to **2 MiB**.
These are HTTP bounds, not a model-token budget or a claim that every host/stdio
buffer has the same cap. Redirects and proxy environment settings are disabled;
TLS verification stays enabled. There is no semantic/response caching.

### Fixed identity and deletion boundary

Only trusted startup configuration supplies `PGAG_MCP_API_URL` and
`PGAG_MCP_API_TOKEN`. The URL must be an HTTPS origin or loopback HTTP origin:
no URL credentials, application path, query, or fragment (a root `/` is accepted).
The bearer token targets the **Native API audience**; the Native API verifies
issuer/audience/signature/time and resolves its subject. It is not an
MCP-caller token-forwarding or identity-delegation mechanism. Tool arguments
cannot override URL, headers, token, or identity. `--subject` and `--once` are
rejected for `mcp`; do not confuse it with the fixed-subject database worker.

Before serving stdio, authenticated `GET /v1/capabilities` must report
`api_version: "v1"`, `service_version: "0.0.14"`, and `schema_version: 9`.
Configuration, authentication, and version errors terminate nonzero with
sanitized diagnostics. v0.0.14 retains schema 9; the adapter itself performs no migration.
Restart the adapter to refresh its fixed token; there is no refresh grant.
Startup validation does not cache authorization: Native authentication,
current ACLs, and deletion checks run on every call.

Run **one adapter per trusted identity** and do not share its stdio connection
with other trust domains or wrap it in a network service. Anyone controlling
that local host can exercise the configured Native identity's permissions.
The Native response-drain barrier ends with HTTP delivery to the adapter,
**not an atomic delivery barrier through stdio, the host UI, or the LLM**.
Even without an adapter response cache, in-flight buffers and already-delivered
context cannot be retracted. The host must discard cached context after forget
or ACL changes; no MCP deletion notification implements this for it.
Active-store purge is not full erasure of host context, WAL, replicas, or backups.

### Protocol evidence boundary

The official [Python SDK v2.2.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0)
was published **2026-09-07**. Upstream
[protocol documentation](https://py.sdk.modelcontextprotocol.io/protocol-versions/)
describes `server/discover` for `2026-07-28` and legacy `initialize` through
`2025-11-25`. These are SDK facts, **not adapter/client qualification results**.
Historical v0.0.8 targeted checks exercised actual stdio SDK `Client` connections and raw
JSON fixtures for both modes:

- **Modern `2026-07-28`:** `Client(mode="auto")` uses `server/discover`.
  Raw requests carry per-request `params._meta` entries
  `io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientInfo`,
  and `io.modelcontextprotocol/clientCapabilities`; the version value is
  `"2026-07-28"`. This is not the legacy initialization handshake.
- **Legacy `2025-11-25`:** `Client(mode="legacy")` and the raw fixture send
  `initialize` with `protocolVersion`, `clientInfo`, and `capabilities`, followed
  by `notifications/initialized` before tool calls.

The runtime smoke launches the actual `pg-agmemory mcp` child in the non-root
production image, uses a fixed token and provisioned scope against its loopback
Native API, and verifies the four-tool list and recall in **both modes**.
Regression coverage includes real HTTP response loss **after remember commits**,
followed by same-key/body retry and an assertion that only one assertion exists.
There is no automatic retry. Exact 256/257-character key boundaries and rejection
without whitespace trimming are also covered.

Historical v0.0.8/v0.0.9/v0.0.10 local/native CI results are recorded below.
v0.0.11 retains and passes both protocol eras. These exercised paths do not qualify
untested older clients, named host applications, or every protocol version.
See [ADR 0008](adr/0008-local-mcp.md) and
[operations](operations/README.md#local-stdio-mcp-operations).

## Implicit recall hook

**Retained read-only, lexical-only contract, verified in v0.0.11.**
`pg-agmemory recall-hook` is an optional, vendor-neutral **harness-side** local
Native HTTP client. There is no automatic registration into a host and no
Copilot, Claude, or Codex integration claim. The host chooses when to invoke it;
the service does not observe host lifecycle events itself. `pg-agmemory[hook]`
pins **httpx==0.28.1, not the MCP SDK**. Both Docker test/runtime stages include
`mcp`, `hook`, and `sdk`; historical v0.0.9 core-only/hook-only isolation checks passed
locally and on both native Docker architectures.
The hook needs no database credentials, signing key, or LLM/provider key.

### Input and trusted startup configuration

One invocation consumes **one UTF-8 JSON document on stdin followed by EOF**,
with a **32,768-byte** input cap. Invalid UTF-8, malformed JSON, oversized input,
and input validation failures are explicit errors, not ignored events:

```json
{"event":"session_start","query":""}
```

Only these two fields are accepted:

| Field | Contract |
|---|---|
| `event` | Exactly `session_start`, `task_switch`, or `after_compaction` |
| `query` | Required string, 0–4,096 Unicode characters; empty browses canonical accessible items under configured scopes and current-time limits |

Retrieval intent comes only from the JSON `query`; `event` is a lifecycle label,
not alternate query text or a natural-language instruction channel.

**All extra fields are forbidden**, including identity, `scope_ids`, `purpose`,
`mode`, budget, URLs, headers, tools, and times. Event/query text cannot authorize
access or configure transport. `--subject` and `--once` are rejected.

Only the **trusted startup environment** supplies routing, authentication, and
recall settings. Do not construct that environment from untrusted prompts,
queries, tool output, or retrieved memory. Never log queries or copy them into
errors. The fixed token is for the **Native API audience**, not a host/vendor
audience, and cannot be overridden by the event.

| Environment variable | Default / constraint |
|---|---|
| `PGAG_HOOK_API_URL` | Required; no default. Trusted HTTPS origin or loopback HTTP origin; no userinfo, application path, query, or fragment. Root `/` is accepted; absent URL gives `invalid_hook_configuration` |
| `PGAG_HOOK_API_TOKEN` | Required fixed Native-audience bearer token, securely supplied by the operator |
| `PGAG_HOOK_SCOPE_IDS` | Required JSON array of 1–32 unique UUIDs; requested scopes narrow existing permissions, never grant them |
| `PGAG_HOOK_PURPOSE` | `implicit_context`; 1–256 characters |
| `PGAG_HOOK_TOKEN_BUDGET` | `2000`; integer 64–2,000 **UTF-8 bytes, not model tokens** |
| `PGAG_HOOK_MAX_ITEMS` | `20`; integer 1–20 |
| `PGAG_HOOK_SEARCH_PROFILE` | `simple-v1`; explicit `ja-janome-0.5.0-v1` opt-in only |
| `PGAG_HOOK_TIMEOUT_SECONDS` | `2.0`; finite number 0.1–20 seconds |

URL, token, and scope IDs are **all required**. Shared `NativeSettings` uses
`httpx.URL` as well as the origin restrictions, rejecting control characters
and invalid IDNA before transport. These invalid-origin cases are covered by
the historical v0.0.9 local and both native CI suites.

Every invocation makes a fresh authenticated `GET /v1/capabilities`, requires
exact **service `0.0.14` / API `v1` / schema `9`**, then sends `POST /v1/recall`
with `mode: "implicit"`, configured scopes/settings, and Native current-time
defaults (no event-supplied historical times). Both calls use the same fixed
token. Current Native authentication, ACLs, time selection, deletion, evidence,
and coverage remain authoritative. A capabilities probe is not cached
authorization. A replacement token is supplied at trusted startup of the next invocation.
Recall scopes **silently narrow** to current authorization. An unauthorized
scope or revoked membership is filtered out: recall returns the authorized
subset, or no items/`not_found`, rather than a scope-existence `404`.
Token authentication failures are different: Native returns explicit `401`,
and the hook returns an error with exit `1`. This preserves Native behavior;
a successful empty recall neither grants access nor proves a scope exists.

### Bounds, results, and failure handling

The network deadline covers **capabilities plus recall together**, default 2.0 s
(finite 0.1–20 s), not a separate full allowance for each request.
It excludes process/interpreter startup, stdin input/waiting, and output, and is **not an LLM
latency SLO or performance qualification**. The harness must close stdin and
set a separate subprocess timeout. The extracted shared Native HTTP client
keeps **256 KiB serialized request / 2 MiB HTTP response** caps, disables
redirects and proxy environment settings, and keeps TLS verification enabled.
These HTTP limits do not cap every host buffer. The serialized context pack
must also fit the configured byte budget; the full RecallResult is not itself
limited to that smaller context budget.
`context_pack.byte_count` is the UTF-8 byte length of the **entire compact
JSON-serialized context pack**, with `ensure_ascii=False` and
`separators=(",", ":")`, including all metadata/citations—not just its text.
The hook verifies this same serialization count against reported `byte_count`
and the configured budget. It also checks that returned item count is at most
configured `max_items` and that the returned search profile matches
configuration. Inconsistent responses fail validation; there is no broad fallback.

Even an empty pack costs **roughly 192 bytes**; this is not a new fixed minimum
configuration value. The accepted budget range still starts at 64. If pack
metadata cannot fit, Native returns **422 `budget_too_small`**, and the hook
returns an explicit error envelope with **exit 1 / `result: null`**, never empty
success. If the pack fits but a candidate cannot, Native can return **200**
with `empty_reason: "budget_exhausted"` and hook **exit 0**.
Missing lexical projections with no candidates instead produce **200 / exit 0**
with `empty_reason: "index_incomplete"`, `coverage.lexical_incomplete: true`,
and `coverage.retrieval_complete: false`. These outcomes preserve Native
semantics; successful HTTP retrieval does not imply complete coverage.

Validated hook runtime outcomes produce exactly one JSON result and a trailing
newline on stdout. Diagnostics
use sanitized stderr, never raw queries, bodies, URLs, headers, or credentials.
The following describes the envelope shape, not literal JSON placeholder values:

```text
{status: "ok", event: <event>, result: <full Native RecallResult>, error: null}
{status: "error", event: <validated event or null>, result: null,
 error: {code, retryable, outcome_unknown: false,
         native_status: <integer or null>, request_id: <UUID or null>}}
```

`request_id` is a validated Native request UUID, not a host event ID.
`retryable` is only a hint: there is no automatic retry. Because the hook only reads,
`outcome_unknown` is always `false`; this does not change MCP mutation semantics.

| Exit | Meaning |
|---|---|
| `0` | Valid Native success, including `empty_reason` of `not_found`, `budget_exhausted`, or `index_incomplete` |
| `2` | Invalid configuration or input, including invalid UTF-8/JSON and oversized stdin |
| `1` | Native, network, version, or protocol failure |

Confirmed runtime error codes:

| Code | Exit | Meaning |
|---|---|---|
| `invalid_hook_configuration` | `2` | Invalid trusted startup configuration |
| `invalid_hook_input` | `2` | Invalid UTF-8/JSON or event/query validation failure |
| `hook_input_too_large` | `2` | Stdin exceeds the byte limit |
| `hook_input_unavailable` | `2` | Stdin cannot be read |
| `hook_deadline_exceeded` | `1` | Combined network deadline exceeded; `retryable: true` |
| `native_api_unavailable` | `1` | Native API transport unavailable; `retryable: true` |
| `native_version_mismatch` | `1` | Capabilities version mismatch |
| `invalid_native_response` | `1` | Invalid Native protocol/response |
| `budget_too_small` | `1` | Mapped Native `422`: even the context pack metadata does not fit |
| Mapped sanitized Native codes | `1` | Native API error, without forwarding raw details |

**Invocation errors are an exception to the JSON envelope contract:** rejected
CLI flags (including `--subject`/`--once`) and a missing `hook` extra use argparse
stderr and **exit 2 without a JSON envelope**. All validated hook runtime errors,
including configuration/input failures above, have the error envelope.
The harness must also handle non-JSON/invalid envelopes and subprocess timeouts;
never echo raw stderr or response/exception content in host logs.

**There is no result on error; failed retrieval is never mapped to empty success.**
A genuine empty result can still have incomplete coverage. The host must inspect
exit code **and** structured status, surface errors/coverage, and explicitly
decide whether to pause or continue without memory. Do not reuse old context to
hide failure. A host-killed/timed-out process may not produce an envelope; that
is a host-observed failure, not an empty result.

There are **no writes, capture, queue submission, LLM/provider calls, caches,
retries, or idempotency keys** in this hook. An event label does not imply
checkpoint creation, compaction, tool dispatch, synthesis, or permission expansion.
Keep memory separate as **untrusted evidence**, never host instructions or policy.

### Deletion and qualification boundary

The Native tenant session advisory response-drain barrier ends at HTTP delivery
to the **trusted local hook**. Hook buffers, stdout/pipe buffers, and host context
are **not atomically covered**. There is no retraction or deletion notification.
After forget or ACL changes, the host must discard previous context and perform
a fresh hook invocation under current authorization. No host-erasure proof,
backup/WAL/replica erasure, or lifecycle-wide access guarantee follows.

Engineering tests cannot qualify a specific vendor integration, semantic quality,
or performance. Full M0–M3/MVP/production/performance/quality/DR/full-erasure
gates remain incomplete. See [ADR 0009](adr/0009-implicit-recall-hook.md) and the
[executable vendor-neutral harness example](operations/README.md#vendor-neutral-python-harness-example).

## Identity and authorization

- A static PEM RSA public key of at least 2048 bits verifies RS256 signatures.
  The configured issuer and audience, required `sub`, `iss`, `aud`, `iat`, and
  `exp` claims, and token time validity are checked.
- The verified external subject is mapped to a principal and tenant in
  PostgreSQL. The deployment has one configured issuer; the subject mapping
  is not a caller-selected tenant. Unknown subjects are unauthenticated.
- Request bodies cannot supply tenant/principal identity. Requested scopes
  narrow access; service checks and RLS enforce membership and permissions.
  Hidden or deleted object lookups return `404` without an existence distinction;
  recall scope filtering instead silently returns only authorized items, as above.
- Runtime credentials must not be superuser, bypass RLS, or own application
  tables, including through owner-role membership. Admin migration/provisioning/rebuild
  credentials are separate.
- JWKS discovery/rotation, delegated identities, multiple-issuer identity
  management, and public membership-administration APIs are not implemented.

Each authenticated API request uses a fresh, short-lived connection, not a
connection pool; there is no runtime `psycopg-pool` dependency.
A **tenant session advisory lock** serializes processing and
is held through transaction commit and delivery of the buffered HTTP response.
This correctness-first drain prevents a purge from acknowledging its barrier
while an earlier response for that tenant is still being sent by the service.
It cannot retract already-delivered data or bytes already handed to the network.
Slow clients can block that tenant; throughput has not been measured.
API and worker share `principal_connection` identity lookup and `bind_identity`
revalidation, and acquire the **same tenant session lock**. API response draining
still extends past commit; worker claim/publication transactions are short, and
payload preparation happens outside them. The worker's configured subject is a
trusted deployment identity, not a public impersonation interface.

Use the privileged **scope-access CLI** for membership administration.
It acquires the **same session lock**, compares the tenant epoch, atomically
updates changed membership/epoch/audit, and holds the lock through commit and
CLI JSON stdout flush before closing the connection. Changes outside this
protocol are not covered by the request/drain race guarantee. See the
[operations procedure](operations/README.md#membership-maintenance-and-request-drain).

## Evidence, consent, and time

`remember` requires `explicit_intent: true` and 1–32 distinct episode evidence
IDs. Each quote must occur literally in its episode's content; both objects
must be in the same scope. An assertion cannot serve as another assertion's
source in this slice. Free-text subjects/values are not resolved to entity IDs;
only explicit entity/relation endpoints create typed graph data.

Literal quote validation establishes provenance, **not semantic support or
truth**. The service does not infer that a quote proves the supplied assertion.
Memory is `reported`, with confidence `score: null` and `method: "uncalibrated"`;
retrieved items require source refresh before asserting current external facts.
Retrieved content is evidence, not trusted instructions.

`consent_reference` records the caller's consent assertion. There is no consent
registry verification, capture-policy engine, or automatic secret/PII redaction.
Callers must supply only approved, already-sanitized data.

### Assertion revision contract

`POST /v1/assertions/{memory_id}/revisions` requires `Idempotency-Key` and
read/write access to the assertion's scope. Supply the entire replacement:

| Body field | Contract |
|---|---|
| `expected_revision` | Strict integer 1–1000 matching the current head |
| `value` | Nonempty text, at most 65,536 characters |
| `evidence` | 1–32 distinct episode IDs with nonempty literal `quote` values of at most 4,096 characters, all in the assertion's scope |
| `explicit_intent` | Must be `true` |
| `valid_from`, `valid_to` | Optional timezone-aware bounds; omitted/null means unbounded, not “keep the old bound.” If both exist, start must precede end |
| `reason` | Nonempty correction reason, at most 256 characters |

Subject, predicate, and scope are immutable and are not accepted in this body.
`201` returns `memory_id`, revision `expected_revision + 1`, and
`epistemic_status: "reported"`. A head mismatch is `409 revision_conflict`;
at the matching head of 1000, another revision is `422 revision_limit_exceeded`.
The limit is **1000 total revisions**, including the initial one.
Hidden/deleted/non-assertion targets return `404`.
Typed relations require their dedicated revision endpoint; generic correction
returns `409 relation_revision_required`.

The target `memory_id` is included in the idempotency request hash. An identical
key retry returns its originally committed revision reference even after later
corrections; it does not create another revision or substitute the latest head.
Reusing a key for a changed target/body is an idempotency conflict. Current
authorization and tombstones still apply to every replay.

### Temporal and evidence semantics

An INSERT trigger uses the DB clock to close the preceding system interval,
advance the assertion head, and assign the new interval atomically. Callers
cannot set system time. System ranges are contiguous `[)` intervals with GiST
non-overlap enforcement through `btree_gist`; deferred DB constraints require
evidence for every revision. Values, reasons, and evidence belong to revisions.

Each correction replaces the **entire valid interval**, not a partial-time
segment. For example, replacing an unbounded Gold assertion with Platinum
valid from October 1 means a September 16 query at the new `known_at` no longer
matches this assertion. It does **not** preserve Gold until October 1 as future
scheduling would. A `known_at` before the correction still selects the former
Gold revision. There is no automatic interval splitting, cross-assertion
supersession, or arbitration between competing facts.

`recall` selects by `as_of`/`known_at`, searches immutable identity text plus
that revision's value, and returns its exact revision and its own sources.
Old values are never paired with newer evidence. Episode filtering still uses
occurrence/recording times. All historical reads apply current ACLs/tombstones.
Context text labels `recorded_at` as `recorded=`: an assertion revision's system
adoption time or an episode's service recording time. It is not an `observed=`
label and does not claim a new external observation; `occurred_at` remains separate.

`explain` accepts an explicit revision from 1–1000, but **omitting it still
requests revision 1**, for compatibility—not the latest revision. Missing
revisions return `404`; episodes accept only revision 1. Assertion explanations
include `recorded_at` (system start), `known_until` (system end, null for the
current head), and `correction_reason` (null for revision 1), alongside that
revision's evidence. See [ADR 0002](adr/0002-assertion-revisions.md).

For exact `known_at` revision-boundary checks, use the server-returned assertion
`recorded_at`, not a host/VM wall-clock sample.

## Retrieval and budgets

Recall defaults to `retrieval_mode: "lexical"` and `search_profile: "simple-v1"`, preserving PostgreSQL's `simple`
configuration, `plainto_tsquery`, and `ts_rank_cd`. The optional Japanese profile
below adds segmentation, not BM25 or vectors; vector/hybrid modes are separate.
Responses echo `search_profile`; unsupported profiles return `422`. In lexical mode an empty `query` is
allowed and browses canonical accessible items under scope/time constraints,
subject to item and byte limits, even if lexical projections are incomplete.
Entities themselves are excluded from recall/explain. Relation assertions remain
FTS candidates; recall never automatically expands the graph and retains
`graph_used: false`. Items and assertion explanations include nullable
`relation: {source_entity, target_entity}` for the exact returned revision.
Relation context text includes both entity UUIDs within the same byte budget.
`coverage.jobs_pending` reports currently readable pending/running jobs in the
requested scopes, not query relevance, historical queue state, or completed
synthesis. `synthesis_pending: false` and `graph_used: false` remain unchanged.
Jobs themselves are excluded from recall/explain and checkpoint/effect references.

Despite the request field name `token_budget`, `utf8-bytes-v1` budgets the
entire compact JSON-serialized context pack in **UTF-8 bytes**, including its
metadata and citations, with `ensure_ascii=False` and `separators=(",", ":")`.
The response declares `budget_unit: "utf8_bytes"`, `token_count: null`, and
`exact_token_count: false`. This is a conservative fallback, not an exact model
tokenizer or a size limit for the entire HTTP response. This context
`tokenizer_id` is unrelated to Japanese search segmentation. Items are omitted whole;
if even pack metadata will not fit, the request returns `422 budget_too_small`,
not a successful empty result.

Limits include a 1 MiB body for checkpoint creation and 256 KiB for other
endpoints, 100 returned recall items at most, and budget
values of 64–8,000 (implicit mode at most 2,000). `coverage.truncated` signals
item/budget omissions. An empty selection is `not_found`, `budget_exhausted`, or
`index_incomplete` as defined below;
`retrieval_complete` does not mean complete knowledge of the world. Native implicit
mode remains a request option. The retained [hook](#implicit-recall-hook) invokes it
only when a trusted harness launches the command; no host is automatically registered.

### Japanese lexical profile

Select `search_profile: "ja-janome-0.5.0-v1"` explicitly. Exact dependency
**Janome 0.5.0** uses bundled **mecab-ipadic-2.7.0-20070801**, including Janome
additions. Only matching Japanese-script runs in source and query text undergo
surface/wakati segmentation; ASCII identifiers and English pass through the
segmenter unchanged before PostgreSQL lexical processing. There is no
Unicode/width normalization, lemma/stemming, synonym expansion, or claim of
segmentation/recall quality. Han-script ranges also affect Chinese characters;
Chinese recall is not qualified. No external model/provider is called.
Preserving Latin text does not turn `simple-v1` into substring search: an
embedded `Gold` without a token boundary need not match standalone `Gold`.

Janome is lazy-imported only when a Japanese-script run requires segmentation;
API import and English-only segmentation do not load it. The matcher's
input-prefix cache is disabled with `max_cached_word_len=0`; only packaged
dictionary-resource caches are retained, not source text or token streams.
Both test and runtime container builds sequentially precompile only static
Janome package bytecode, including dictionary modules. This is code preparation,
not a memory index/cache. A fresh Linux subprocess regression guard requires
no Janome import for English-only operations and initialization peak RSS
**below 256 MiB**. It is not a deployed memory limit, a bound on request/backfill
memory use, or release qualification on its own; cold uncompiled host installations and
deployment resource sizing remain unqualified.

`memory.episode_lexical` and `memory.assertion_lexical` store derived `tsvector`
payloads for this profile. Episode rows represent revision 1; assertion rows
identify the exact revision. Forced RLS and same-scope canonical foreign keys
apply, with `ON DELETE CASCADE`. Runtime grants are `SELECT`/`INSERT` only, with
no `UPDATE` or direct `DELETE`; canonical parent purge cascades without child
DELETE grants. Episode content and
assertion subject/predicate/exact-revision value are segmented, then indexed
with `to_tsvector('simple', ...)`. The query uses segmented text with
`plainto_tsquery('simple', ...)` and `ts_rank_cd`; the simple profile still uses
the existing canonical vectors. Entities and jobs do not become recall items.

Observe and all assertion publication/revision paths write projections in the
same transaction, including typed relations and durable-job publication.
Canonical IDs, timestamps, evidence, synchronous normalized JSON/HMAC, and
historical revision selection are unchanged. Migration backfills all retained
episodes and **all assertion revisions**, not only current heads, and skips
tombstones. Offline administrative rebuild uses the same canonical sources.
Japanese episode content retains the **65,536-character** limit; **65,537**
characters are rejected rather than truncated. The separate 256 KiB HTTP body
cap still applies, including JSON encoding overhead.

For the Japanese profile, any missing projection among currently authorized,
requested-scope, time-eligible canonical candidates sets
`coverage.lexical_incomplete: true` and `coverage.retrieval_complete: false`.
This check is independent of query relevance and the item limit. There is **no
silent fallback** to simple search or automatic repair worker. Available matches
may still be returned with the incomplete flag; in lexical mode an empty query still browses
canonical items. No query candidates plus missing projections gives
`empty_reason: "index_incomplete"`; candidates that cannot fit the context retain
`"budget_exhausted"`, and a nonempty result has null `empty_reason`.
Without missing projections, `lexical_incomplete` is false and ordinary
`not_found`/budget rules apply. Projection coverage is not query relevance,
queue state, or knowledge/quality completeness; `jobs_pending` remains separate.
Janome corrupt-dictionary diagnostics are sanitized to `japanese_dictionary_error`
without input text. Library `SystemExit` becomes tokenizer-unavailable:
API `503 dependency_unavailable`, not an incomplete-index success; workers use
the existing bounded `dependency_unavailable` retry path without input echo.

Capabilities retain feature `japanese_fts`, both
`search_profiles`, `default_search_profile: "simple-v1"`, and pinned tokenizer/
dictionary metadata with `normalization: "none"` and
`segmentation: "japanese-script-runs"`. Context budgeting stays `utf8-bytes-v1`;
`auto_synthesis` and recall `graph_used` remain false. The v0.0.11 vector/hybrid
foundation adds `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"` without changing lexical defaults.
Historical stages `m2-japanese-fts` and `m2-atomic-capture`
never represented full M2 acceptance. See
[ADR 0007](adr/0007-japanese-fts.md),
[offline rebuild](operations/README.md#lexical-profile-and-reindex-operations),
and [dependency licensing](../README.md#dependency-licensing).

## Durable jobs

### Explicit structured publication

`POST /v1/jobs` requires `Idempotency-Key` and
`{kind: "structured_remember", memory: <Remember request>}`. The only recipe is
`structured-remember-v1`. `memory` has the unchanged synchronous `Remember`
contract: scope, subject, predicate, value, 1–32 distinct readable same-scope
episode IDs with literal quotes of 1–4,096 characters, `explicit_intent: true`,
and optional aware valid bounds. Enqueue requires current scope read/write
access. This is asynchronous **structured publication**, not automatic synthesis,
natural-language extraction, an LLM/provider call, embedding, or compaction.
`observe` still enqueues nothing and returns `synthesis_job_id: null`.
Synchronous `remember`, including legacy normalized JSON/HMAC, is unchanged.

`202` returns `{job_id, kind: "structured_remember",
recipe_version: "structured-remember-v1"}`. It acknowledges a committed job
reference, not a published assertion. Canonical intent plus recipe deduplicates
within the same tenant/principal/scope, including across HTTP keys; evidence
order is canonicalized for job identity. Same HTTP key still requires the same
normalized request (`409 idempotency_conflict` on change). Another principal
can submit its own job; different source identities are not semantically deduped.
There are at most **100 pending/running jobs per scope** (`422 job_limit_exceeded`)
and **5 attempts per job**.
Capabilities advertise `durable_jobs`, `job_kinds: ["structured_remember"]`,
`auto_synthesis: false`, and the 100-job/5-attempt/30-second lease limits.
These bounded jobs are not full M2 acceptance.

`GET /v1/jobs/{job_id}` requires current read access. Same-scope readers may read
another principal's job but cannot claim, publish, or retry it. The response includes:

| Field | Contract |
|---|---|
| `job_id`, `kind`, `recipe_version`, `retry_of` | Opaque job identity, fixed kind/recipe, and nullable retry parent |
| `state` | `pending`, `running`, `succeeded`, or `failed` |
| `attempt`, `max_attempts` | Attempts already claimed; maximum is 5 |
| `available_at`, `lease_until`, `created_at`, `updated_at` | Scheduling/lease and server timestamps; lease is null outside running |
| `error_code` | Null or `dependency_unavailable`, `stale_context`, `invalid_input`, `attempt_limit` |
| `input_refs` | Exact immutable episode revision-1 references |
| `result` | Null or the original assertion `{memory_id, revision: 1}`, even after later corrections |

GET never exposes request payload, lease token, or owner principal. Both succeeded
and failed terminal jobs erase request JSON; input ID references remain until
purge. Terminal records are immutable.

### Explicit retry of terminal failure

`POST /v1/jobs/{job_id}/retry` requires `Idempotency-Key` and the full original
`EnqueueJob` body. The owner must reprovide it because the failed payload is no
longer stored. Current permissions/evidence are rechecked and the intent is
verified against its HMAC. Changed intent gives `409 job_intent_conflict`;
a nonfailed parent gives `409 job_retry_conflict`; a nonowner gets `404`.

Retry creates **one new child** with a fresh five-attempt allowance, never resets
the old terminal record. Repeated retries of the same failed parent reuse that
child even across HTTP keys. If the child itself fails, retry the child ID for
another explicit cycle. The fixed recipe cannot be reset or replaced through
this endpoint. Retry lineage participates in purge.

### Fixed-principal worker and publication fence

`pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT [--once]` uses
restricted `PGAG_DATABASE_URL` credentials and the API's startup role/schema
checks. The subject must be preprovisioned within the configured issuer and is
trusted deployment configuration. No JWT signing/public key or admin URL is
needed. Superuser, owner-role, and `BYPASSRLS` runtime credentials are rejected.
This initial profile claims only that principal's jobs, not a global multi-tenant
scheduler; fairness and cost-pool behavior are not qualified.

Claims use `FOR UPDATE SKIP LOCKED` in a short transaction and **commit before
payload validation outside the transaction**. A fresh UUID lease token increments
the attempt, captures current access/deletion epochs, and grants a 30-second lease
(internal 1–300-second claim bounds exist for controlled tests, not CLI tuning).
Current permissions and complete immutable episode inputs are checked.

Publication rechecks principal/scope, exact original prepared body, input evidence,
lease/token/expiry, and captured epochs. The shared assertion-publication helper
commits the assertion, provenance, job success, and audit atomically. The final
job update checks expiry again; mid-publication expiry rolls back the output.
The assertion's `recorded_at`/system interval starts at **worker publication,
not enqueue**; job `created_at` is not the assertion's adoption time. Caller
valid-time bounds remain independent of this server-controlled system time.
Lease expiry/takeover fences stale publishers. Internal heartbeat checks lease
and epochs and renews the 30-second lease; no public claim/publish/heartbeat
endpoint exists. The deterministic processor makes no external call and needs
no long-running heartbeat task.

Retriable job failures schedule `2^attempt + [0,1)` seconds of jittered backoff,
up to five attempts. Nonretryable `invalid_input` fails immediately. An expired
fifth claim becomes `failed`/`attempt_limit`; no sixth attempt is granted.
Errors are logged as safe codes, without payloads. Processing is at-least-once
attempts with **at most one committed result per job**, not external exactly-once.

Continuous mode polls idle work every 1 second and delays 2 seconds after transient
DB loop failures. `--once` processes at most one due job, emits a JSON outcome
(`idle`, `succeeded`, `pending`, `failed`, or `lease_lost`) with applicable opaque
IDs/result reference, then exits. Stdout/log outcome references are opaque
historical records, not current read authorization or a live state snapshot.
Job GET and exact-revision explain apply current access/deletion checks.
It does not drain the queue and is rejected on other CLI commands.
See [operations](operations/README.md#durable-job-and-worker-operations)
and [ADR 0006](adr/0006-durable-jobs.md).

## Entities and SQL graph oracle

### Entity identity

Creation requires `Idempotency-Key`, current scope read/write access, and:

| `POST /v1/entities` field | Contract |
|---|---|
| `scope_id` | Required scope UUID |
| `entity_type` | Literal `person`, `organization`, `project`, `component`, `incident`, `task`, `decision`, or `other` |
| `canonical_label` | 1–256 characters |
| `evidence` | 1–32 distinct readable same-scope **episode** IDs, each with a literal `quote` of 1–4,096 characters |
| `explicit_intent` | Must be `true` |

`201` returns `memory_id` and `revision: 1`. Identity metadata and evidence are
immutable caller reports, not verified facts. There are no aliases, entity
merging, name-based resolution, semantic deduplication, or label-correction
endpoint. Same HTTP key/body reuses the anchor under current authorization;
a different key may create a separate same-label entity.
Names and types are untrusted data, never instructions.

`GET /v1/entities/{memory_id}` returns ID/revision, scope, type, canonical label,
recorded time, and episode evidence. Entities do not appear in recall/explain;
use this dedicated GET. Entity revision 1 is allowed in checkpoint/tool-effect
`memory_refs`; declare every copied entity or assertion revision dependency.

### Canonical relation assertions

`POST /v1/relations` requires `Idempotency-Key`, `scope_id`, `source_entity` and
`target_entity` UUIDs, `predicate`, 1–32 distinct literal episode evidence quotes,
and `explicit_intent: true`. Both endpoints **and evidence must share that scope**
and be currently readable. Quote bounds are 1–4,096 characters. Optional
`valid_from`/`valid_to` are timezone-aware; null/omitted is unbounded, and start
must precede end. The predicate allowlist is `depends_on`, `part_of`, `affects`,
`works_for`, `decides`. All predicates allow multiple reported declarations;
there is no arbitration, verified truth, or inferred inverse fact.

A relation has **one canonical assertion identity (`memory_id`)**, not an
independent object ID: `memory.relation` fixes its source, and
`memory.relation_revision` records the exact target of each assertion revision.
Subject is the immutable source label; each revision's immutable value is its
target's canonical label. Valid/system time, truth status, and episode evidence
are the existing assertion revision's, not a parallel graph history.
Creation returns the existing `RememberResult` (`memory_id`, revision 1,
`epistemic_status: "reported"`). A free-text `remember` with matching
label/predicate never automatically becomes a relation.

`POST /v1/relations/{memory_id}/revisions` requires `Idempotency-Key`,
strict `expected_revision` 1–1000, `target_entity`, replacement episode `evidence`,
`explicit_intent: true`, optional aware valid bounds, and `reason` 1–256 characters.
Source/predicate/scope are fixed. It replaces the **entire valid interval**, like
generic assertion corrections; it does not split time or retain the prior value
outside new bounds. Old target IDs, evidence, and intervals remain tied to their
exact historical revision. CAS, idempotent historical replay, and the 1000-total-
revision bound are unchanged (`409 revision_conflict`, `422 revision_limit_exceeded`).
Use `explain` for exact relation evidence; omitted revision still means **1**.

### Bounded expansion

Authenticated `POST /v1/graph/expand` is read-only; no `Idempotency-Key` is required.

| Field | Contract |
|---|---|
| `scope_ids` | Required 1–32 distinct scope UUIDs |
| `seeds` | Required 1–16 distinct entity UUIDs |
| `relation_types` | Required 1–5 distinct allowlisted predicates above |
| `purpose` | Required 1–256-character text |
| `direction` | `outgoing` (default), `incoming`, or `both` |
| `max_hops` | Strict integer 1–2, default 2 |
| `max_paths` | Strict integer 1–100, default 100 |
| `as_of`, `known_at` | Optional timezone-aware timestamps; defaults captured once per expansion |

Canonical PostgreSQL SQL joins are the only backend: no AGE, SQL/PGQ, Cypher,
dynamic SQL, or dynamic labels in graph requests/traversal. Fixed parameterized
neighbor queries enforce time and current RLS visibility for seeds, edges,
intermediate nodes, and evidence, with same-scope foreign keys. Scope/seed
filters only narrow access. Hidden, nonexistent, or temporally unavailable
seeds are silently excluded, not echoed. The existing tenant transaction and
response-drain lock protect the read boundary.

Traversal is deterministic breadth-first **simple paths**: sorted seed UUIDs,
then each hop's assertion ID/revision/next entity ID. No entity repeats within
a path; semantic cycle edges do not create repeating-node paths. All prefixes
count toward the global path budget. A limit-plus-one probe detects additional
eligible paths; at most `max_paths` are returned. Incoming/both changes traversal
orientation only; edge source/target and reported facts are not inverted.

Results contain `backend: "sql"`, `projection_watermark: null` (no graph projection,
lag, or watermark guarantee is needed), effective `as_of`/`known_at`, and:
- `nodes`: canonical entity summaries, without full evidence quotes;
- `edges`: canonical assertion ID/revision, source/target UUIDs, predicate,
  valid interval, recorded time, and `epistemic_status: "reported"`;
- `paths`: `{nodes: [UUIDs], assertions: [{memory_id, revision}]}`;
- `coverage`: `max_hops`, `truncated`, and `complete_within_bounds`;
- `consistency`: current access/deletion epochs;
- `empty_reason`: `not_found` when no paths exist, otherwise null.

Visible isolated seeds may still appear in `nodes` without paths. No-path and
bounded completeness are **not proof that no fact exists**. Entity GET/relation
explain provide evidence; expansion summaries do not. A missing node on the
final recheck fails closed with `409 graph_invalidated`; DB errors return `503`,
not an empty-success fallback. Capabilities expose `graph_backend: "sql"`,
entity/relation type allowlists, and graph caps. This is a correctness reference
for future backend conformance, not measured graph utility or full M1/M3 acceptance.
See [ADR 0005](adr/0005-relational-graph.md).

## Checkpoint contract

Checkpoint creation and restoration require `Idempotency-Key` and current
read/write access to the scope. GET requires current read access. Run/branch
UUIDs are caller-supplied identities within a tenant/scope, not global sessions.

| Creation field | Contract |
|---|---|
| `scope_id`, `run_id`, `branch_id` | UUIDs identifying the scope-local run and branch |
| `expected_head` | Required UUID or `null`; null only for an empty branch, otherwise the exact current checkpoint ID |
| `harness_id`, `harness_version` | Required nonempty text, at most 256 characters each; fixed for the run |
| `state_schema_version` | Only `1`, also the default |
| `event_watermark` | Required nonnegative 64-bit integer; cannot decrease relative to the parent |
| `state` | Typed `goal`, `constraints`, `completed_actions`, `decisions`, `unresolved_questions`, `next_actions`, and `pending_effects`; no arbitrary object/pickle |
| `memory_refs` | Up to 100 distinct `(memory_id, revision)` pairs in the same scope; episodes/entities use revision 1, assertion revisions (including relations) must exist; omitted list is empty and omitted revision defaults to 1, not latest |

The goal and state text entries are nonempty and at most 4,096 characters.
Constraints, decisions, unresolved questions, and next actions allow 64 entries
each; completed actions and pending effects allow 100. Each pending effect has
a unique UUID `operation_id`, a 1–256-character `description`, and a
`planned / dispatched / unknown` status. These are snapshot hints, not evidence
that an external action was executed or confirmed.

The server assigns checkpoint UUIDs and branch-local sequences starting at 1.
Branch-head CAS rejects stale heads with `409 checkpoint_head_conflict`;
decreasing watermarks yield `409 checkpoint_watermark_conflict`. An invalidated
branch cannot be reopened (`409 checkpoint_invalidated`). Existing checkpoint
payloads are immutable. The HMAC checksum uses `hmac-sha256-v1` and covers the
saved envelope, including references and capture epochs. GET checks checksum,
typed state, visible references, and current authorization. An invalid envelope
fails closed; hidden/deleted checkpoints return `404`.

The envelope includes `saved_access_epoch`, `saved_deletion_epoch`,
`current_access_epoch`, and `current_deletion_epoch`. Saved epochs are metadata,
not permission to use old authorization. Checkpoints are excluded from recall
and explain; use the checkpoint GET endpoint for their state.

### Restore and dependency boundary

Restore takes `checkpoint_id`, a never-used `target_branch_id`, and exactly
matching `harness_id`, `harness_version`, and `state_schema_version`.
It creates a new checkpoint at sequence 1 in the same scope/run, with the source
checkpoint as parent—even when that parent is on another branch. The original
branch/checkpoint is unchanged; restore never rewinds a head. An existing target
branch gives `409 checkpoint_branch_conflict`; incompatible harness/schema gives
`422 checkpoint_incompatible`.

State and references are copied, but dispatched snapshot hints become unknown.
In the same transaction, restore appends `unknown` events for every dispatched
live effect in the run, with `origin: "checkpoint_restore"`, then creates the fork.
The new revisions fence stale ledger writers through CAS; they cannot cancel
external calls already in flight. Exact restore replay creates neither a new
fork nor duplicate journal events.
Saved assertion references retain their exact historical revisions. Restore
neither selects the latest assertion revision nor automatically refreshes
current external facts; obtain fresh observations separately when needed.
GET/restore merge **all live effects in the run**, including other branches and
effects added after the saved checkpoint. `tool_effects` contains current
summaries; saved state and its checksum remain unchanged by this live view.

| Current ledger state | Snapshot hint | Reconciliation for this operation |
|---|---|---|
| `dispatched` / `unknown` | Any or absent | Required |
| `confirmed` / `failed` | Any or absent | Resolved by the caller-reported terminal record |
| `planned` | Absent or `planned` | Not required; still not execution permission |
| `planned` | `dispatched` / `unknown` | Required; record uncertainty, then reconcile a receipt |
| Untracked | Any hint, **including `planned`** | Required; also listed in `untracked_effects` |

`requires_reconciliation` contains all blocking operation IDs; any blocker makes
`resume_allowed: false`. This is an intentional tightening of v0.0.3's
snapshot-only behavior. `automatic_reexecution` is always false.
The host owns permission checks, approvals, and provider reconciliation;
no provider-query service, automatic execution, or checkpoint-execution harness adapter is implemented.

Identical-key retries retain the original checkpoint reference, not a new head.
Replay records contain no state; reads/restore retries rebuild envelopes under
current authorization, so current epoch metadata can change. Purged-reference
replay returns `404`.

**Callers must declare every memory dependency in `memory_refs`.** The
dependency DAG covers declared references, complete parent lineage, and the
run-wide effect-to-checkpoint dependency described below;
no semantic scanner discovers copied but undeclared source text. Callers remain
responsible for consent and secret/PII sanitization. Working snapshots,
compaction, and checkpoint-execution harness integration remain separate future work.
See [ADR 0003](adr/0003-checkpoints.md) and [ADR 0004](adr/0004-tool-effects.md).

## Tool-effect ledger

Planning and transitions require `Idempotency-Key` and current scope read/write
access; GET requires read access. **Create a bootstrap checkpoint first**:
planning does not create a run, and a missing run returns `404`.

| Planning field | Contract |
|---|---|
| `scope_id`, `run_id`, `operation_id` | Caller UUIDs; operation identity is tenant/scope/run/operation, not global |
| `tool_name` | Nonempty text, at most 256 characters |
| `action_hash` | Required lowercase 64-hex digest of the caller's canonical action; the server cannot verify it against an external call |
| `memory_refs` | Up to 100 distinct exact same-scope references: episode/entity revision 1 or existing assertion revision 1–1000 (including relations); defaults to empty, omitted revision is 1, not latest |

**Declare every memory dependency used by the action.** Checkpoints/effects/jobs are
not permitted reference kinds; undeclared copied data is not discovered.
The service persists only a tenant-HMAC `action_fingerprint` and a stable
64-hex `external_idempotency_key`, not raw action hashes or arguments.
GET exposes these identifiers, reference IDs, latest revision/status,
`run_invalidated`, and immutable history of at most four events.
Effects are excluded from recall/explain; use their dedicated GET endpoint.
Tool names, reasons, and receipt references must still be sanitized by the caller.

Planning returns `201` with `memory_id`, `revision: 1`, `status: "planned"`.
Different idempotency keys with the same operation identity and normalized body
deduplicate to that original revision-1 reference, even after later transitions.
Changed intent returns `409 operation_conflict`; changed body under the same
idempotency key returns `409 idempotency_conflict`. Hidden/purged identities cannot
be recovered by replay (`404` for an exact retry).
Each run allows **100 effects over its lifetime, including terminal records**;
the cap returns `422 effect_limit_exceeded`. Purging does not free capacity for
reuse because it seals the run. New run/operation IDs are not semantic deduplication.

Transitions require strict integer `expected_revision` 1–4, `status`, and a
nonempty `reason` of at most 256 characters. Success returns `201` with the
next revision/status; stale CAS is `409 revision_conflict`, and a forbidden
transition is `409 effect_transition_conflict`.

| Current state | Allowed next state |
|---|---|
| `planned` | `dispatched`, `unknown` |
| `dispatched` | `unknown`, `confirmed`, `failed` |
| `unknown` | `confirmed`, `failed` |
| `confirmed`, `failed` | None; terminal states are immutable |

`planned → unknown` records uncertainty about an off-protocol/legacy attempt;
it does not authorize execution. There is no `unknown → dispatched`.
Terminal transitions require a nonempty `receipt_reference` (at most 256
characters) and `receipt_source: "provider_receipt"` or `"operator_review"`;
other states require both receipt fields to be omitted or null. These are **caller-reported references,
not server-verified outcomes**. GET history includes recorded time, reason,
receipt fields, and `origin`; the DB assigns timestamps and actors and enforces
the FSM, contiguous revisions, head advancement, and reference constraints.
RLS and composite tenant/scope foreign keys remain in force; no privileged helper.

Idempotency retains only the result reference/revision/status and keyed request
digest, not receipt or body copies. Plan and transition responses, including
dispatch acknowledgments, are **historical revision references, not current-state
snapshots or execution authorization**. Under current authorization, same-intent
planning retries and exact transition replays can return the original reference
for a surviving effect even after its run is sealed. This does not unseal the
run: fresh dispatch remains rejected, and exact replay of a purged effect remains `404`.
The harness must durably record dispatch before an outside call and use the
stable external key where the provider supports it. No external exactly-once,
approval, automatic retry/execution, or provider-receipt-query guarantee is made.

## Idempotency and deletion

Mutations require `Idempotency-Key`; mutation and idempotency result commit
together before response delivery. Keys are scoped by tenant, principal, and
operation. Matching normalized requests reuse results after current access
checks; changed payloads return `409`. Exact replay targeting a deleted memory
returns `404`, not its old content.

Source-event deduplication uses a tenant-keyed HMAC of the **namespace + event ID**
pair, keyed in PostgreSQL by tenant/scope. It is not an event-ID-only hash.
An identical event can reuse its live episode even with a new idempotency key;
a conflicting payload returns `409`. Exact replay of a purged event returns
`404`; reusing its original source identity does not resurrect the episode.

`preview` reports the current target count without changing state or issuing a
reserved selector token. `purge` accepts 1–100 root IDs and follows
**episode → assertion (any revision) → checkpoint references → descendant/fork
checkpoints**. Direct episode-to-checkpoint references also participate.
Entity evidence adds **episode → entity → relations using it as source or any
historical target → entire assertion history**. Direct entity purge has the
same relation closure. Entity references in checkpoints/effects also participate.
Other surviving entity identities are not deleted merely because a relation is
removed. Entities depend only on episodes: semantic graph cycles do not introduce
provenance cycles.
Declared episode/entity/assertion-to-effect references add
**source → tool effect → every checkpoint in that scope/run**, including old
checkpoints with empty references and snapshots predating the effect.
Jobs add **episode → job** through immutable inputs, **result assertion → job**,
and **parent job → retry descendants**. Jobs count within the same closure limit.
Deleting any source, including one used only by a later revision of a result
assertion, purges that entire assertion history and dependent jobs.
**Deleting a job/control record or failed-parent retry chain does not delete
already-published independent assertion outputs or source episodes.** Outputs
have direct episode provenance; purge the output/source explicitly to erase the
fact. There is no job → result dependency cycle.
The total limit is 10,000 dependents plus requested roots, not 10,000 per layer.
Larger closures fail with `422` without partial purge. Any historical source
conservatively removes the assertion's entire history and all dependent
checkpoint payloads, even when later snapshots omit that source. Every child
inherits its full parent lineage; forks cannot escape it.

Purging **any** effect permanently sets the run's `effects_invalidated` flag.
New effect plans/dispatch return `409 effect_run_invalidated`; new checkpoints
are rejected with `409 checkpoint_invalidated`, and no checkpoint in that run
can resume. Other independent effects are not automatically purged: GET still
returns their history with `run_invalidated: true`, and allowed reconciliation
transitions remain possible, including `unknown → confirmed/failed`, but no dispatch.

Affected branch heads are permanently invalidated and their IDs cannot be
reopened. Deleting a checkpoint does not delete its ancestors or source
episodes. There is no regeneration. The existing tenant session lock keeps
the closure, payload purge, run/branch invalidation, and read barrier atomic.

Purge deletes job input/request rows before assertion/episode payloads and
tombstones, fencing running publishers under the same tenant barrier.
Purged-job GET and exact HTTP replay return `404`; retained job identity prevents
resurrection of the same exact job.
Purge synchronously SQL-deletes target episode/entity/assertion/checkpoint/effect/job
payloads, entity evidence, typed relation links, effect events (including reasons/
receipt references), and dependent quotes/references,
then inserts scope-bound opaque deletion markers with timestamps
in `memory_ops.object_tombstone` **in the same transaction**. `memory.object`
has no `deleted_at` column; its SELECT RLS excludes objects with tombstones.
The transaction also advances `deletion_epoch` and commits a receipt.
HTTP `202` with `active_store_purged` is **not** a queued purge job or certification
of complete erasure. Opaque operation registry/run flags, run/branch metadata,
object records, tombstones, audit/receipt
metadata, job identities, and tenant-keyed HMAC source/idempotency tombstones persist for the
tenant lifetime; there is no automatic expiry or full tenant-erasure workflow.
Historical references and replay cannot resurrect purged labels, values, or receipts.
Canonical deletion also cascades every affected episode/assertion lexical
revision under the same tenant barrier, before tombstones commit. These derived
payloads are not separate memory identities or provenance vertices. Rebuild
skips tombstones and does not regenerate purged content.

Receipts report `backup_status: "operator_managed"` and
`backup_retention_deadline: null`. Old database pages, WAL, replicas, backups,
and previously delivered context are not certified erased. The service is not
qualified for full-erasure guarantees or production compliance. Databases
restored from backups must remain quarantined until the latest deletion ledger
and ACL revocations have been reapplied; automated backup recovery/ledger replay
and DR qualification are not implemented.

## Schema compatibility

**v0.0.14 retains schema 9 and adds no migration.** Existing schema-9 databases use
the [application-only update](operations/README.md#schema-9-application-only-upgrade).
Historical v0.0.13 introduced `009_scope_access.sql` for durable admin audit.
The retained privileged-only `memory_ops.scope_access_event` table uses forced RLS,
no runtime policy/grant, and atomic membership/epoch/audit changes.
Retain the pinned PostgreSQL 18.6/pgvector 0.8.6 images.
Stop/drain old APIs, workers, adapters, hooks, and SDK callers, then deploy only
matching v0.0.14 components; no mixed-version/rolling-compatibility claim is made.
Older schemas need the [schema-9 upgrade](operations/README.md#schema-9-scope-access-upgrade).
Older databases still need v0.0.11's `008_pgvector.sql`.
Migration requires **`vector` 0.8.6 in `public`** and rejects
an existing extension in another schema or at another version.
Use the pinned prebuilt upstream DB profile above, not an assumed unchanged old
PostgreSQL image or an unpinned extension. Do not start schema-8 processes against schema 9.
The retained episode/assertion-revision projections use forced RLS, canonical
`ON DELETE CASCADE`, and runtime SELECT/INSERT only. **No embedding backfill**
runs for existing data; generation/rebuild/provider calls remain explicit and external.
The MCP adapter, hook, and SDK use HTTP only, perform no DDL, and require matching
service `0.0.14`, API `v1`, schema `9`.
The retained migration history below still applies to databases older than schema 7.

Additive `007_japanese_fts.sql` follows unchanged migrations 001–006. It creates
the two lexical projection tables; the migration runner performs Python backfill
in the **same transaction** before recording schema 7. All retained episodes and
assertion revisions are covered without changing canonical IDs/system times,
receipts, or tombstones. Failure even after backfill completes rolls back
projection DDL/data and the schema ledger together: a schema-6 upgrade remains at
6. Failed explicit reindex instead preserves existing schema-7 projections.
Typed graph/job/effect/checkpoint histories and guards,
legacy `Remember` JSON/HMAC ordering, source identities, and checkpoint checksums
remain unchanged. Projections add no checkpoint/effect reference kinds.
The v0.0.14 API **and worker** require exact history `[1, 2, 3, 4, 5, 6, 7, 8, 9]`
and extension `vector` 0.8.6 in schema `public`, rejecting mismatches and unsafe runtime roles.

Migration/rebuild requires a forced-RLS-bypassing administrator with appropriate
rights; migration also requires DDL rights, `btree_gist`, and the matching pgvector
extension installed on the PostgreSQL server. `row_security = off`
fails closed if RLS would filter backfill; it does not grant bypass privileges.
`pg-agmemory reindex-lexical` is an **all-tenant offline admin operation** on the
selected database. Use matching v0.0.14/schema-9 tooling with `PGAG_ADMIN_DATABASE_URL`.
It atomically replaces only lexical projections under the migration lock, emitting the
`profile` and `episodes`/`assertion_revisions` counts, not source content.
`--subject` is explicitly rejected, not a principal/scope filter; `--once` is
also rejected as worker-only.
Stop/drain all old/new APIs **and workers**, back up, migrate/rebuild atomically,
then start only matching v0.0.14 processes. Stop adapters, hook launches, SDK callers, and admin commands during maintenance too.
Lexical reindex does not generate, populate, or rebuild vectors.
**Keep all old images stopped; v0.0.1 has no schema startup guard.**
No rolling coexistence or downgrade is supported. Follow
[schema-9 operations](operations/README.md#schema-9-scope-access-upgrade).

## Validation evidence

Public repository: [rioriost/pg_agmemory](https://github.com/rioriost/pg_agmemory).

<a id="v0014--schema-9"></a>

### v0.0.14 / schema 9 — verified

**Final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **495 tests,
1 existing warning**, Ruff, strict mypy (**19 source files + 1 separate strict SDK
consumer**), and genuine core-only/hook-only/sdk-only installation checks.
Local `./scripts/test-containers.sh` exited **0**.
The total is **464 retained + 16 readiness unit + 15 integration tests (31 new)**.
Schema 9 is unchanged; no migration was added.
Published implementation:
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
(`feat: add bounded runtime readiness probe`).
[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)
passed on that exact SHA on both native Linux architectures. **Actual logs**,
not only job status, verified each architecture's counts, checks, and smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **295.67 s (4:55)** |
| Docker, native `linux/amd64` | **385.41 s** |
| Docker, native `linux/arm64` | **470.16 s** |

Test elapsed is not a performance benchmark.
These results qualify the implementation SHA above; a subsequent final
documentation publication/CI run is separate and is not reported here.

The real 5 s locked-schema timeout check passed within its 4.5–10 s assertion
window; this is not a wall-clock SLA. Cancellation left no runtime backend leak;
connection-refusal recovery and retained liveness also passed.
Passing DB variants include `NOINHERIT` owner-role membership, schema-ledger
SELECT revocation/restoration, and extension namespace move/restoration.

All non-root production smokes passed in all three environments: Japanese tokenizer, HTTP/liveness,
readiness schema-fault/recovery, worker, MCP **2026-07-28/2025-11-25**, all three
hook events, capture, pgvector, Python SDK, and scope-access.
The passing disposable-DB readiness smoke uses the **same API process**:
**ready 200 → rename schema ledger → ready 503 while health stays 200 →
restore ledger → ready 200**. It follows the normal HTTP smoke, with subsequent
Native/SDK smokes unchanged and no source/tombstone writes.
This lifecycle passed in all three environments; never rehearse drift on a live DB.
No MVP/production/performance/quality/DR/full-erasure
qualification is claimed.

<a id="v0013--schema-9"></a>

### Historical v0.0.13 / schema 9 — verified

**Final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **464 tests,
1 existing warning**, Ruff, strict mypy (**19 source files** plus the separate
**1-file SDK consumer**), and genuine core-only/hook-only/sdk-only installation
checks. The total is **426 retained + 22 scope-admin unit + 16 integration
tests (38 new)**. Local `./scripts/test-containers.sh` exited **0**.
Published implementation:
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413).
[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)
passed on that exact SHA on both native Linux architectures. **Actual logs**,
not only job status, verified each architecture's counts, checks, and smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **297.52 s (4:57)** |
| Docker, native `linux/amd64` | **539.86 s** |
| Docker, native `linux/arm64` | **460.73 s** |

Test elapsed is not a performance benchmark.
The separate final v0.0.13 docs
[`185f433aa49479810b8955f1bec2e856f2715f7b`](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)
passed [CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967).
Actual native logs verified **464 tests, 1 warning** per architecture, Ruff,
strict mypy **19 source files + 1 consumer**, optional-install checks, and all
production smokes: **499.25 s amd64 / 454.37 s arm64**.
Those docs timings are separate from implementation CI 35196930448 above.
Neither run validates v0.0.14.

Passing integration covers nonowner `BYPASSRLS` inspection without `FOR UPDATE`,
maximum-epoch change rejection/no-op behavior, schema-8→9 ledger failure after
DDL, transactional rollback and retry, and prior migrations. It also covers
holding the output lock after real
commit, consumer-failure connection cleanup, and injected `OperationalError`
after a real commit: the stored ACL/audit remained committed,
`outcome_unknown` was true, and replaying the stale expected epoch conflicted.
Passing cases include a real 5 s lock timeout with no change, cross-scope
tenant-global CAS with a legacy empty-permission row, and schema-9 legacy-ACL
preservation/no audit backfill/forced RLS/capabilities.

All non-root production smokes passed in all three environments: Japanese tokenizer, HTTP API,
worker `--once` idle, MCP **2026-07-28/2025-11-25**, all three hook events, capture,
pgvector, Python SDK, and scope-access.
The scope-access CLI subprocess smoke uses dedicated admin
credentials for **get → CAS set read-only → SDK read succeeds / write replay 404 →
CAS revoke → SDK empty recall → get inactive**. Admin credentials belong only
to the administrative subprocess, not the SDK/API. This verified lifecycle is
not external ACL-provider integration. Production/quality/performance/DR/full-erasure
gates remain incomplete.

<a id="v0012--schema-8"></a>

### Historical v0.0.12 / schema 8 — verified

**Final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **426 tests,
1 existing warning**, Ruff, strict mypy for **18 source files**, and the separate
strict typed consumer for **1 file** covering all 24 method annotations.
All three genuine **core-only/hook-only/sdk-only noneditable wheel-install
checks** passed, including packaged `py.typed` and absence of MCP from hook/sdk-only
installs. All checks passed in all three environments.
The suite consists of **345 retained + 76 SDK unit + 5 SDK integration tests (81 new)**.
These are final results, not the superseded fixture-key failure.
Published implementation:
[`88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f`](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)
(`feat: add typed asynchronous Native Python SDK`).
[CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945)
passed on that exact SHA. **Actual logs**, not only job status, verified each
native architecture's SHA, counts, checks, and production smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **292.76 s** |
| Docker, native `linux/amd64` | **484.79 s** |
| Docker, native `linux/arm64` | **472.49 s** |

Elapsed times are test observations, not performance benchmarks.

Final v0.0.12 documentation
[0e00abd](https://github.com/rioriost/pg_agmemory/commit/0e00abdae930dcc1e2d2015fbf5931a74701fe7d)
passed [CI 35194141510](https://github.com/rioriost/pg_agmemory/actions/runs/35194141510).
Actual native logs verified **426 tests per architecture**, all checks and smokes;
docs-run elapsed was **488.20 s amd64 / 454.88 s arm64**.
This is distinct from implementation CI 35193004945 and the timings above.
Neither v0.0.12 run validates v0.0.13/schema 9.

All five SDK integration tests passed over actual HTTP against disposable
PostgreSQL databases in all three environments, covering all 24 resources:
graph/relation revision, failed-job retry with an actual worker,
checkpoint create/read above 256 KiB with tool-effect restore reconciliation,
authorization revocation, and real post-commit response loss with same-key recovery.
Passing unit checks include entry-cancellation cleanup, single-use after
failed entry, exit after a caller-body exception, 256-character keys, exact GET
success status, and wrong response shapes for both forget modes.
All non-root production smokes passed in all three environments:
Japanese tokenizer, HTTP API,
actual worker `--once` idle, MCP **2026-07-28/2025-11-25**, all three hook events,
atomic capture with an actual worker, pgvector exact/hybrid retrieval, and the
new Python SDK lifecycle.
The SDK smoke performs **capture/replay →
pending `get_job` → embedding input/upload → exact vector recall → preview/purge →
deletion progress → capture replay 404**. **It does not invoke a worker**;
the earlier actual capture-worker smoke is retained separately.
The SDK smoke passed locally and on both native architectures.
The implementation and final-docs runs above are distinct historical evidence,
not v0.0.13 qualification. M0–M3/MVP/production/performance/quality/DR/full-erasure gates remain incomplete.
Historical v0.0.11 evidence below does not qualify SDK changes.

<a id="v0011--schema-8"></a>

### Historical v0.0.11 / schema 8 — verified

**Final local and native results verified 2026-09-17 JST.** Implementation
[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)
passed [CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403)
on that exact SHA. Apple Container and both native Docker jobs each passed
**345 tests, 1 existing warning**, Ruff, strict mypy (**17 source files**),
core-only/hook-only installation checks, and all non-root production smokes:
Japanese tokenizer, HTTP API, worker, MCP **2026-07-28/2025-11-25**, all three
hook events, atomic capture lifecycle, and pgvector exact/hybrid retrieval and purge.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **283.44 s** |
| Docker, native `linux/amd64` | **404.40 s** |
| Docker, native `linux/arm64` | **433.46 s** |

Final v0.0.11 documentation
[dccd5cb](https://github.com/rioriost/pg_agmemory/commit/dccd5cb5571873515aace8621ce4adb3de250d3a)
passed [CI 35190495385](https://github.com/rioriost/pg_agmemory/actions/runs/35190495385).
Actual logs verified **345 tests, 1 warning** per native architecture, Ruff,
strict mypy (**17 source files**), installation checks, and all smokes.
Docs-run elapsed: **506.38 s amd64 / 460.18 s arm64**.
These are distinct from implementation CI 35189448403 and its local/native
timings above; neither run validates v0.0.12.

Actual logs, not only job status, establish these results. Test elapsed is not
a performance benchmark. Artifact inspection separately verified the pinned
upstream pgvector 0.8.6 profile on both architectures. Passing checks cover the new
DB profile with schema-8 migration/role/extension-version/schema guards, canonical digest
binding, float normalization/immutability/model isolation/eight-model cap,
deterministic exact/RRF mathematics, pre-ranking ACL/time filters, coverage,
purge/replay, and retained lexical/MCP/hook/capture behavior.
Implemented fixtures additionally cover DB norm/dimension/composite-FK/eight-model
guards, direct RLS visibility and denied updates, ACL revocation, and actual
schema-7→8 migration ledger-failure rollback of DDL/extension followed by retry,
without embedding backfill. The production vector smoke uploads **both episode
and assertion projections**, checks basis-vector distances **[0, 1]** and RRF,
then purges the source and checks upload replay `404`. These checks passed in all
three environments.
Tests and the synthetic basis-vector example cannot establish semantic quality,
production performance, provider provenance, or robustness to untrusted vectors.
No source-build workflow is part of the adopted profile.
All original M0–M3/MVP/production/DR/full-erasure/performance/quality gates remain incomplete.

<a id="v0010--schema-7"></a>

### Historical v0.0.10 / schema 7 — verified

**Final local Apple Container and native CI results verified 2026-09-17 JST.**
The final local source matches published implementation
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f).
Both native jobs in
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)
passed. Actual logs verified the exact SHA, test counts, timings, and checks
below, not just the job status.

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | **304 passed, 1 existing warning** | **275.53 s** |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | **304 passed, 1 existing warning** | **467.75 s** |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | **304 passed, 1 existing warning** | **434.40 s** |

All three final runs also passed **Ruff, strict mypy (16 source files), genuine
core-only/hook-only installation checks, and every non-root production smoke**:
Japanese tokenizer, API HTTP, worker CLI, MCP in **`2026-07-28` and `2025-11-25`
modes**, all three hook events (**`session_start`, `task_switch`,
`after_compaction`**), and atomic capture. Timings are test observations,
not performance benchmarks.

Coverage includes rollback faults after either write and the outer receipt,
source/key deduplication races, quota, RLS/deletion, and API process restart with
an actual worker. The production smoke passed in all three environments.
For a fresh fixture, after MCP/hook checks, it exercises Native capture →
pending job → actual worker CLI `--once` → recall of the episode/assertion pair →
same capture replay → source purge → job GET `404` and capture replay `404`.
The final suites also cover actual HTTP 201 response loss after commit:
same-key retry returns the exact episode/job pair with only one publication.
It also checks that a failed original capture job remains the replay target
after explicit creation of its retry child. These regressions passed locally
and on both native Docker architectures.
No dependency is added; project v0.0.10 lock metadata changes only.
All M0–M3/MVP/production/performance/quality/DR/full-erasure gates remain incomplete.
Final v0.0.10 documentation
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)
also passed **304 tests per native architecture** in
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760).
That docs run is distinct from the implementation-run timings above.
Neither run validates v0.0.11/schema 8.

<a id="v009--schema-7"></a>

### Historical v0.0.9 / schema 7

**Final local and native CI results verified on 2026-09-17 JST.**
The tested final local source matches published implementation
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050).
Both native jobs in
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)
passed; their actual logs verified the exact SHA, counts, and checks below,
not just the job status.
No v0.0.7/v0.0.8 result is reused as v0.0.9 evidence.

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | **274 passed, 1 existing warning** | **248.29 s** |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | **274 passed, 1 existing warning** | **482.21 s** |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | **274 passed, 1 existing warning** | **374.33 s** |

All three final runs also passed **Ruff, strict mypy (15 source files), genuine
core-only/hook-only installation checks, and all non-root production smokes**:
Japanese tokenizer, API HTTP, worker CLI, MCP in **both `2026-07-28` and
`2025-11-25` modes**, and recall-hook for **`session_start`, `task_switch`,
and `after_compaction`**.
The suite covers the extracted shared `native_client.py`, hook validation and
failure handling, entire-pack byte accounting, missing-index
`index_incomplete`/incomplete coverage, budget outcomes, silent scope/revocation
filtering, and explicit token-authentication failures, while retaining Native
and MCP semantics. Test elapsed time is an observation, not a performance benchmark.

The suite also covers shared `NativeSettings` origin validation with `httpx.URL`,
rejecting control characters and invalid IDNA before transport.

The automated Docker **`adapter-extras-check`** target checks genuine core-only
installation/missing extras, then hook-only **without MCP**, including explicit
JSON for failed HTTP. The container script builds it on local Apple Container
and both native Docker architectures. These genuine installation and failed-HTTP
checks **passed in all three environments**, along with the production smokes above.
Full M0–M3/MVP/production/performance/quality/DR/full-erasure gates remain incomplete.

The final v0.0.9 documentation commit
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)
also passed **274 tests** in each native architecture in
[CI run 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689).
This final-docs run is distinct from the implementation-run timings above.
Neither v0.0.9 run validates v0.0.10 atomic capture.

<a id="v008--schema-7"></a>

### Historical v0.0.8 / schema 7

Implementation
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)
was verified on **2026-09-17 JST**:

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | 214 passed, 1 existing warning | 240.83 s |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | 214 passed, 1 existing warning | 415.46 s |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | 214 passed, 1 existing warning | 389.47 s |

All three passed **Ruff, strict mypy (13 source files), and non-root production
Japanese tokenizer, API HTTP, worker CLI, and MCP stdio smokes**. MCP checks cover
both `2026-07-28` and `2025-11-25`, using SDK 2.2.0 and separate raw wire fixtures.
Both jobs in [CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)
ran the exact SHA above; actual logs verified counts and smokes. Timings are
test observations, not performance benchmarks. The remaining warning concerns
the existing anyio BlockingPortal alias.

A separate fresh core-only install (`uv sync --frozen --no-dev --no-editable`)
was verified in Apple Container: Native API import succeeded with neither `mcp`
nor `httpx` installed, and `pg-agmemory mcp` exited with the explicit missing-extra
diagnostic. This additional check was local, not a separate Docker CI assertion.
All M0–M3/MVP/production/performance/quality/DR/full-erasure acceptance gates
remain incomplete. Historical v0.0.7 results below do not validate MCP.

The subsequent bilingual documentation commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)
passed **214 tests in each native architecture** (`linux/amd64`, `linux/arm64`) in
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509).
This is the v0.0.8 documentation run, separate from the implementation-run
timings above. Neither run validates v0.0.9 or the recall hook.

### Historical v0.0.7 / schema 7

For **v0.0.7/schema 7 only**, implementation commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473),
final results were verified on **2026-09-17 JST**:

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | 144 passed, 2 existing warnings | 206.54 s |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | 144 passed, 2 existing warnings | 386.32 s |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | 144 passed, 2 existing warnings | 331.59 s |

All three final runs also passed **Ruff, strict mypy (12 source files), and all
three non-root production smokes: Japanese tokenizer, API HTTP, and actual CLI
worker**. Both native Docker jobs in
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)
ran the exact SHA above; actual logs verified the counts and checks, not just job
status. Elapsed times are test-run observations, not performance benchmarks.
The final bilingual documentation commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)
also passed both native jobs in
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899).
That is the historical final-docs CI run, distinct from the implementation-run
timings above; neither run validates v0.0.8 or v0.0.9.

Coverage includes default/opt-in lexical behavior, Japanese/ASCII handling,
exact 65,536-character indexing and 65,537-character rejection, lazy loading and
the fresh Linux initialization guard, temporal/RLS and incomplete-index/budget
behavior, and canonical purge without child DELETE grants. It also covers
schema-6 rollback after actual initial backfill, preservation of old projections
after partial reindex failure, rejected reindex scope/worker flags, and retained
job/graph/effect/checkpoint and legacy idempotency behavior. Exact historical
checks use server-recorded assertion times, not VM wall-clock samples.

The tokenizer smoke checks `東京都` → `東京` / `都` and logs
`Production Japanese tokenizer smoke passed`; API smoke checks HTTP health.
For worker smoke, a disposable principal ran actual
`pg-agmemory worker --subject ... --once` with runtime-only credentials in the
non-root production image, verified
`{"outcome":"idle"}`, and logged `Production worker smoke passed`.
The CI step is `Test containers and smoke-test production API and worker`.
These smokes check packaged tokenizer behavior, API liveness, and worker startup/
idle execution, not end-to-end recall quality or queued-publication correctness;
publication behavior is covered separately by the test suite.

The v0.0.7 final lock retained the prior package-feed registry. All **36 packages'**
versions, dependency metadata, and artifact hashes were verified byte-for-byte
equivalent to the tested PyPI-resolved lock. Relative to v6, only Janome 0.5.0
was added and the project version changed to v0.0.7; there were no unrelated
upgrades or registry migration. Native CI built the final retained-registry lock.
This package count/comparison is historical, not a claim about the v0.0.8 MCP lock.

Earlier v5 evidence remains historical in [ADR 0005](adr/0005-relational-graph.md);
v6 decisions/evidence remain in [ADR 0006](adr/0006-durable-jobs.md).
Checks do not establish complete M0/M1/M2/M3, measured performance/quality, external
exactly-once behavior, an MVP, production readiness, backup/DR, or full-erasure qualification.

## Still roadmap work

Automatic enqueue/NL extraction/synthesis, LLM/provider processing, global
multi-tenant scheduling/fairness/cost pools, separate working snapshots/
compaction, automatic embedding/provider integration, ANN/HNSW, qualified vector/hybrid
retrieval quality and performance, AGE, SQL/PGQ,
provider receipt verification, vendor-specific harness integration and execution/recovery,
cross-assertion supersession/fact arbitration, remote MCP HTTP/SSE/OAuth/delegation,
synchronous/TypeScript SDKs, and postgresem integration are absent.
The v0.0.12 async Python SDK is verified locally and on both native architectures. The vendor-neutral hook
does not register or qualify any host. Local stdio MCP,
opt-in lexical segmentation, explicit structured jobs, the bounded SQL
graph oracle, and typed checkpoint envelopes do not complete the planned
bitemporal, graph, provenance, or deletion architecture.

See [ADR 0001](adr/0001-initial-slice.md) for these choices,
[operations](operations/README.md) for safe administration, and
[contributing](../CONTRIBUTING.md) for the container validation workflow.
