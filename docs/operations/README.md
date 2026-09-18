# Operations for the initial slice

[日本語](README-jp.md) | [Project README](../../README.md) | [Current contract](../STATUS.md)

**Not a production runbook or a verified disaster-recovery procedure.**
Use approved, sanitized, disposable test data for this initial release.
Destructive operations—including purge drills, schema resets, and restore
experiments—must run only against disposable test databases, never business
databases or real user histories.

## Bootstrap and role separation

Use the pinned prebuilt upstream pgvector DB profile below and the application
image built from the repository's existing `Dockerfile`.
The CLI is `pg-agmemory`; the import package is `pg_agmemory`.
The local checkout is `pg_agmemory`; GitHub is `rioriost/pg_agmemory`.
The current bounded implementation is **v0.0.24/schema 10 selectable inference foundation**.
**Implementation and synthetic-provider contracts qualified, not M2 completion**.
Verified v0.0.23 and earlier results are historical, not v0.0.24 evidence.
Existing `008_pgvector.sql` requires **`vector` 0.8.6 in `public`** and rejects an
existing extension at another version or in another schema.
Existing `009_scope_access.sql` supplies the privileged audit table.
Retained `010_job_cancellation.sql`, introduced in v0.0.15, changes job constraints and the terminal-state
guard without adding tables. The pinned DB profile and dependencies are unchanged.
See [job cancellation](#explicit-job-cancellation).
**Historical v0.0.8:** 214 tests and production smokes passed in Apple Container
and native Docker amd64/arm64. These are not v0.0.9 results.
M0–M3, MVP, production, performance,
quality, DR, and full-erasure acceptance remain incomplete.
**Historical v0.0.7 only:** Apple Container and native Docker amd64/arm64 each passed **144 tests**
(2 existing warnings), Ruff, strict mypy (12 source files), and all three non-root
production smokes: Japanese tokenizer, API HTTP, and actual worker CLI `--once`
idle execution. Final SHA, CI logs, and timings are in
[validation evidence](../STATUS.md#validation-evidence).

The historical v0.0.7 final lock retained the existing package-feed registry. All
36 packages' versions, dependency metadata, and artifact hashes are byte-for-byte
equivalent to the tested PyPI-resolved lock. Relative to v6, only Janome 0.5.0 was
added and the project version became v0.0.7; no unrelated upgrades or registry
migration occurred. Native CI built that retained-registry lock. This is not a
package-count or validation claim about v0.0.8/v0.0.9. Use the current locked build;
the optional MCP extra pins `mcp==2.2.0` and `httpx==0.28.1` and is included in
both Docker test and runtime stages. v0.0.24 retains `hook` and `sdk` in both stages;
`pg-agmemory[hook]` pins `httpx==0.28.1` **without the MCP SDK**.
The container-check script requires runner-side `jq` for **both Apple Container
and Docker**, including disposable smoke configuration.

| Setting | Consumer | Purpose |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | Administrative CLI only | Migration, offline all-tenant lexical rebuild, and private tenant/principal/scope provisioning |
| `PGAG_DATABASE_URL` | API and worker runtime | Dedicated restricted login belonging to `pgag_runtime` |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | Static PEM RSA verification key, at least 2048 bits; never the signing private key |
| `PGAG_JWT_ISSUER` | API runtime | Exact trusted issuer |
| `PGAG_JWT_AUDIENCE` | API runtime | Exact audience for this service |
| `PGAG_MCP_API_URL` | Local MCP adapter only | Fixed trusted Native API HTTPS origin or loopback HTTP origin; no URL credentials, application path, query, or fragment |
| `PGAG_MCP_API_TOKEN` | Local MCP adapter only | Fixed Native API audience bearer token; supplied securely at startup, never per call |

1. Confirm that the admin URL identifies the intended empty, disposable Memory
   DB. Use the pinned upstream DB image with the matching extension available.
   An operator-managed alternative must supply the same extension version/schema;
   no host-APT or source-build procedure is provided.
   Run `pg-agmemory migrate` from the matching application image. The migration is
   transactional and version-recorded in `public.pgag_schema_migration`.
   The migration loop accepts only sequential supported history and skips
   applied versions on rerun; lock acquisition has a 5-second timeout.
   Any existing DB upgrade requires the maintenance procedure below.
2. The unchanged `src/pg_agmemory/storage/001_initial.sql` and
   `src/pg_agmemory/storage/002_assertion_revisions.sql` and
   `src/pg_agmemory/storage/003_checkpoints.sql` and
   `src/pg_agmemory/storage/004_tool_effects.sql` and
   `src/pg_agmemory/storage/005_relational_graph.sql` and
   `src/pg_agmemory/storage/006_durable_jobs.sql`, followed by additive
   `src/pg_agmemory/storage/007_japanese_fts.sql`, existing `008_pgvector.sql`,
   existing `009_scope_access.sql`, and retained `010_job_cancellation.sql` are installed package
   resources. Do not substitute the illustrative DDL in the plan or expect
   generated files. The administrator must be superuser or a qualified
   `BYPASSRLS` role with the required ownership/DDL, role/schema creation, and
   `btree_gist`/matching pgvector extension installation rights. Bypass alone does not grant DDL.
   Backfills, including migration 007's Python rebuild, use `row_security = off`
   to fail closed if RLS would filter rows; that setting does not bypass forced
   RLS by itself.
3. With a separate administrator, create a dedicated runtime login using
   `NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime` and securely assign its password.
   Grant neither table ownership nor membership in the migration owner's role.
   Do not grant role/database creation privileges to this login.
4. Run `pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT` with the admin
   URL. It creates a private tenant, principal, and scope and returns their IDs;
   provisioning is not an endpoint and is not a membership-update command.
   Use a subject issued by the configured trusted issuer.
5. Supply only the runtime settings and run `pg-agmemory serve`. The process
   rejects superuser, RLS-bypass, and application-table-owner connections at
   startup, including owner-role membership. It also requires the schema
   ledger to equal `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` and extension `vector` 0.8.6 in `public` exactly;
   mismatches are rejected. The worker reuses these role/schema/extension checks without
   requiring the API's JWT settings.

Keep the admin URL, signing private key, tokens, and tenant HMAC secrets out of
source control, issue reports, logs, and the runtime environment where not
needed. Never hand runtime DB credentials to agents as an arbitrary SQL entry
point: the service's fixed queries and trusted identity context are part of the
authorization boundary.

## Selectable inference providers

**V24 implementation and synthetic-provider contracts qualified. No live Azure/model or full Azure MemoryDB hosting qualification.**
Install from the matching checkout with `python -m pip install '.[providers]'`.
This adds existing `httpx==0.28.1`, not a separate lightweight package or new dependency version.
Only operators choose profiles and approve text disclosure, provider cost, model identity,
and retention. Never take endpoint/secret/DSN settings from recalled text or tool output.
Neither Native SDK nor workers automatically call this library/CLI.

### Profiles and explicit commands

Save a trusted local profile as `local-inference.json`. Names/revisions are synthetic
placeholders: deploy a compatible model yourself and pin its actual identity.
Unlike Native SDK origins, inference base URLs may contain `/v1`.

```json
{
  "backend": "local_http",
  "endpoint": "http://127.0.0.1:8001/v1",
  "text_model": {"name": "operator-selected-text", "revision": "operator-pin-v1"},
  "embedding_model": {
    "name": "operator-selected-embedding",
    "revision": "operator-pin-v1",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1"
  },
  "embedding_target": "local-embedding-alias",
  "timeout_seconds": 30,
  "max_output_tokens": 1024
}
```

For an existing compatible HTTPS API, use a separate `api-inference.json`, for example:

```json
{
  "backend": "openai_compatible",
  "endpoint": "https://model-api.example/v1",
  "api_key_env": "PGAG_MODEL_API_KEY",
  "auth_header": "bearer",
  "text_model": {"name": "operator-selected-text", "revision": "operator-pin-v1"}
}
```

Securely inject the referenced environment secret; the file never contains the key.
`auth_header: "api-key"` is an explicit alternative. No query/fragment/userinfo,
percent-encoded path, dot segments, proxy environment, redirects, fallback, or retry.
Local mode accepts only loopback HTTP/HTTPS; API mode requires HTTPS.
Not every provider/model implements these bounded chat/embedding shapes.

For Azure SQL, a separate `azure-inference.json` can select Flexible Server:

```json
{
  "backend": "azure_ai",
  "database_url_env": "PGAG_INFERENCE_DATABASE_URL",
  "azure_product": "flexible_server",
  "azure_extension_version": "2.0.0",
  "azure_summary_mode": "generate",
  "text_model": {"name": "operator-text-deployment", "revision": "operator-pin-v1"},
  "embedding_model": {
    "name": "operator-selected-embedding",
    "revision": "operator-pin-v1",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1"
  },
  "embedding_target": "operator-embedding-deployment",
  "timeout_seconds": 30
}
```

The SQL environment variable holds a dedicated inference DSN, not the canonical
administrator/runtime connection. Provision extension/model credentials and grants
outside this application; managed identity is recommended where supported.
For HorizonDB use `azure_product: "horizondb"` and the installed version
(documented PG17 example: `"2.2.1"`); `text_model.name` and `embedding_target`
must be registered model aliases, not deployment names.
The Flexible example uses the documented PG18 version, not proof of deployment readiness.
No HTTP settings or `max_output_tokens` belong in the SQL profile.
For embedding-only profiles omit both `text_model` and `azure_summary_mode`.
An HTTP `max_output_tokens` value requires a `text_model`; omit it for embedding-only HTTP profiles too.
Flexible Language mode must be selected explicitly with `azure_summary_mode: "language"`,
`text_model.name: "azure_cognitive.summarize_abstractive"`, optional `language`,
and `sentence_count` 1–20 (default 3). It is not supported on HorizonDB.
A nondefault `sentence_count` is rejected outside Flexible Language mode.
For new setups prefer `generate`; Language Summarization retires **2029-03-31**.
See [official version/preview/lifecycle references](../adr/0024-selectable-inference.md#azure-reference-boundary).

Save approved synthetic input as `inference-input.json`:

```json
{"text": "Synthetic example: the decision is tentative, not approved."}
```

The following are **operator-triggered inference calls**, not validation instructions
to run against real data. Each call chooses one profile; summarize can use local
while embed uses Azure. Protect output files: successful output contains model data.

```bash
umask 077
pg-agmemory infer inspect --config local-inference.json
pg-agmemory infer inspect --config azure-inference.json
pg-agmemory infer summarize --config local-inference.json < inference-input.json > summary-result.json
pg-agmemory infer embed --config azure-inference.json < inference-input.json > embedding-result.json
```

`inspect` takes no input: HTTP constructs/closes a client without network calls,
validating configuration and credential headers; SQL connects for read-only
catalog/role/extension/overload/SQL-permission checks, not model inference.
Neither proves provider access, quota, connectivity, or output quality.
`summarize`/`embed` read one closed JSON object from stdin, not raw text.
CLI inference results use one `{status, result, error}` envelope. Invalid-input/configuration
errors exit 2; other provider failures exit 1. Errors expose only
`code`, `retryable`, `billing_unknown`; do not log raw validation failures or secrets.
Config ≤32 KiB; input text 1–65,536 non-whitespace characters with original bytes,
UTF-8/JSON input and HTTP request ≤256 KiB; HTTP/SQL result guard 2 MiB.
Serialization overhead counts. Strict `timeout_seconds` is 1–120 (default 30);
HTTP-only `max_output_tokens` is 1–4096 (default 1024).

### Library, output review, and explicit upload

The typed library uses the same profile contract, without an implicit memory client:

```python
import asyncio
from pathlib import Path

from pg_agmemory.providers import (
    InferenceInput, ProviderFailure, make_provider, parse_settings,
)


async def main() -> None:
    try:
        provider = make_provider(parse_settings(Path("local-inference.json").read_bytes()))
        result = await provider.summarize(
            InferenceInput(text="Synthetic example: no approval has been given.")
        )
        print(result.status)
    except ProviderFailure as exc:
        print(exc.error.code)
        if exc.error.billing_unknown:
            print("Billing may have occurred; no automatic retry was attempted.")


asyncio.run(main())
```

`SummaryResult` is `{model, input_digest, summary, status: "untrusted"}`.
Review it privately; it is not a grounded assertion, approval, or compaction snapshot.
`GeneratedEmbedding` is existing `VectorQuery` plus `input_digest`, with exactly
768 finite values and finite nonzero norm. No padding/truncation or model-space inference.
Pin model identity/revision yourself; aliases and AIMM upgrades are not attested.

For memory upload, explicitly obtain `source = await memory.embedding_input(Explain(...))`,
then `generated = await provider.embed(InferenceInput(text=source.text))`.
Construct `PutEmbedding` with `source.memory_id`, `source.revision`,
`generated.model`, `generated.values`, and `generated.input_digest`, and send it
using a retained caller-owned idempotency key. Keep the exact returned text/digest;
Native checks current ACL, model/digest, and purge state again. The provider does
not upload or inherit authority from a cursor, summary, or digest.
After an uncertain upload, retry only the **same generated payload/key**, not another
model invocation. Provider `billing_unknown` is distinct from Native `outcome_unknown`.
Cancellation propagates and may not stop remote execution/charges.

SQL uses a dedicated autocommit connection with `verify-full` TLS/system CA or an
operator-specified CA file, not a canonical transaction/session lock.
Each function statement still has a DB transaction. Preflight rejects privileged
roles/ownership and requires pinned extension-owned compatible overloads plus
SQL `USAGE`/`EXECUTE` before each call; it never reads model registries or key settings.
One `MATERIALIZED` result evaluation plus a server-side size guard prevents repeated
inference evaluation in the adapter's result query, not provider-side retries.
The adapter explicitly requests `max_attempts => 1` where the documented function
supports it (SQL embedding/Language). The application does not retry.
`azure_ai.generate` has no verified retry or output-token knob; extension-internal
behavior and charges are not guaranteed. One `MATERIALIZED` SQL invocation is
**not proof of one billable upstream call**.
Language mode preserves all summary parts and requests `disable_service_logs => true`;
this is **not full erasure**. Server query logs, provider logs, retention, and budgets
remain operator responsibilities.
See [full settings/SQL contract](../STATUS.md#selectable-inference-providers)
and [qualification evidence](../STATUS.md#v0024--schema-10).

## Episode query and pagination

**v0.0.24/schema 10 contract qualified.**
Use Native JWT authentication and trusted scope UUIDs with current read access.
Read-only `POST /v1/episodes/query` requires no write permission or `Idempotency-Key`.
This synthetic first-page request is illustrative; do not run it against live data.

```json
{
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "occurred_from": "2026-09-17T00:00:00Z",
  "occurred_to": "2026-09-18T00:00:00Z",
  "max_items": 20,
  "before": null
}
```

Closed `QueryEpisodes` requires 1–32 distinct scope UUIDs.
Aware `occurred_from`/`occurred_to` are nullable, default null, and form the
half-open range **`occurred_from <= occurred_at < occurred_to`**.
Omitted/null sides are unbounded; equal/reversed bounds are invalid.
Strict integer `max_items` is 1–100, default 20; booleans/floats are rejected.
`before` defaults to null; closed `EpisodeCursor` requires aware `recorded_at`
and UUID `memory_id`. Duplicate scopes, naive timestamps, invalid UUIDs/ranges,
or extra fields give **422 `invalid_request`**.
Scope/time filters apply before LIMIT. There is no `source_namespace` filter:
source identity is retained as HMAC anchors, not a plaintext queryable field.
Time filters are not `as_of`, `known_at`, historical ACLs, or bitemporal reconstruction.

Within an already-open `AsyncMemoryClient` configured from trusted
`PGAG_SDK_API_URL`/`PGAG_SDK_API_TOKEN`, import `QueryEpisodes` and `Explain` from
`pg_agmemory.models`, build `request = QueryEpisodes.model_validate(data)`,
and call `page = await memory.query_episodes(request)`.
For explicit continuation, preserve intended scopes/time bounds and pass
`page.next_cursor` as `before` in a new `QueryEpisodes`; stop on null.
Submitting null again restarts newest, not an exhausted continuation.
After **explicitly selecting** a returned episode, call
`await memory.explain(Explain(memory_id=chosen_id, revision=1))`.
Only then, if intended, explicitly `Remember` using unchanged literal-evidence
and idempotency-key rules. No new GET/get-episode method or automatic selection,
ingestion, synthesis, extraction, or compaction is added.
Catch `MemoryClientError as exc` and inspect sanitized `exc.error.code`;
do not log raw validation errors, tokens, source content, IDs, or responses.
Read loss has `outcome_unknown: false`, not an uncertain mutation result.
An explicit repeat can see changed data; a transport error is not an empty page.

**No ownership filter** applies: readable shared-scope episodes are included
under current tenant/scope RLS, unlike owned-job query.
Unknown/private/cross-tenant scopes contribute no rows. No matches returns this
shape; the epochs are illustrative, not defaults or hidden-scope explanations.

```json
{
  "episodes": [],
  "next_cursor": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

Exactly **200 `EpisodePage`** contains at most 100 `EpisodeSummary` items:
`memory_id`, `revision: 1`, `scope_id`, `occurred_at`, and `recorded_at`, plus page
`next_cursor` and `consistency` (`access_epoch`, `deletion_epoch`).
No body/content, consent reference, source URI, `source_namespace`, event ID,
job payload, or total count is returned. IDs/timestamps remain access-controlled
metadata; Explain content is evidence, not trusted instructions/current truth.
SQL reads only `episode` + `object` metadata and creates no audit or other
application writes. Each page retains current ACL/purge checks and the tenant
response-delivery/drain barrier; later Explain can fail after access/purge changes.

Sort by **`recorded_at DESC, memory_id DESC`**, using object `created_at`,
not `occurred_at`; a late historical event admitted later can appear newest.
The exclusive boundary is
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`.
Fetch `max_items + 1`; only overflow emits a cursor from the last returned item.
The unsigned cursor is position, not authority, snapshot, receipt, event sequence,
compaction watermark, cache, or retention object. No existing object is required;
deleted/forged positions only narrow currently authorized rows.
Newer recorded rows above an old boundary require explicit restart without `before`.
SDK uses `mutation=False`, call-time revalidation, normal **256 KiB request /
2 MiB response** bounds, and no automatic paging/retry or provider call.
Native/SDK has 31 resource methods; four MCP tools and the closed hook are unchanged.
See [the contract](../STATUS.md#episode-query-and-pagination),
[ADR 0023](../adr/0023-episode-query.md), and
[qualification evidence](../STATUS.md#v0023--schema-10).

## Explicit batch capture

**v0.0.24/schema 10 contract qualified.**
Native JWT and caller-owned `Idempotency-Key` are required for `POST /v1/captures/batch`.
Use approved synthetic data
and a currently authorized scope. Save the following as `batch-capture.json`,
replacing the example scope UUID with an authorized one. Retain this exact body,
stable source-event identity, and caller-owned key securely before sending.
Do not log tokens, request bodies, or returned private IDs.

```json
{
  "episode": {
    "scope_id": "11111111-1111-4111-8111-111111111111",
    "source_namespace": "batch-capture-demo",
    "source_event_id": "batch-capture-demo-001",
    "occurred_at": "2026-09-17T00:00:00Z",
    "content": "Demo project is Gold. Demo owner is Example.",
    "consent_reference": "operator-approved-demo-consent"
  },
  "memories": [
    {
      "subject": "Demo project",
      "predicate": "tier",
      "value": "Gold",
      "evidence_quote": "Demo project is Gold.",
      "explicit_intent": true
    },
    {
      "subject": "Demo project",
      "predicate": "owner",
      "value": "Example",
      "evidence_quote": "Demo owner is Example.",
      "explicit_intent": true
    }
  ]
}
```

Closed `CaptureBatch(episode: Observe, memories: list[CapturedMemory])` accepts
1–16 proposals. Existing field/time/quote bounds apply; each proposal uses one
literal quote from this episode and cannot override scope/evidence/identity.
Duplicate normalized model JSON candidates, including trim-equivalent entries,
are **422 `invalid_request`**. Same-subject different predicates above are distinct
explicit intents, not automatically extracted facts.

With the matching SDK installed, supply trusted `PGAG_SDK_API_URL`,
`PGAG_SDK_API_TOKEN`, and a retained `PGAG_BATCH_CAPTURE_KEY`:

```python
import asyncio
import os
from pathlib import Path

from pg_agmemory.models import CaptureBatch, CaptureBatchResult
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


async def capture() -> CaptureBatchResult:
    request = CaptureBatch.model_validate_json(
        Path("batch-capture.json").read_text(encoding="utf-8")
    )
    key = os.environ["PGAG_BATCH_CAPTURE_KEY"]
    async with AsyncMemoryClient(
        os.environ["PGAG_SDK_API_URL"], os.environ["PGAG_SDK_API_TOKEN"]
    ) as memory:
        try:
            return await memory.capture_batch(request, idempotency_key=key)
        except MemoryClientError as exc:
            if exc.error.outcome_unknown:
                print("Outcome unknown: reconcile with the original key and body.")
            raise


receipt = asyncio.run(capture())
```

Save `receipt` securely. Exactly **201** returns `CaptureBatchResult`:
`memory_id` is the episode UUID, `revision` is 1, and `synthesis_job_ids` contains
1–16 job UUIDs in request order. No assertion IDs or aggregate job status are returned.
Use `get_job` for each ID, or current caller-owned `query_jobs`; cancel/retry
individual jobs explicitly. Workers publish independently, with no execution-order
guarantee or atomic batch completion. There is no group cancel or automatic polling.

Admission commits all new episode/lexical/job/identity/receipt/audit writes in
one existing tenant transaction/barrier. Late invalid quotes, remaining
100-active-job-per-scope quota exhaustion midway, or audit failure roll back the
whole new admission, leaving existing rows unchanged. Reused jobs do not consume
new-job quota; 16 is a request limit, not a new global quota.
Fresh keys for identical source/intents reuse original IDs even when
cancelled/failed/succeeded, without revival. Fresh-key reordering returns
reordered existing IDs; same-key reordering conflicts with **409**.
Replay checks current write access and every job's liveness; source or one-job
purge gives whole-receipt **404**, not partial replay or resurrection.
Source purge keeps the existing dependent-job/assertion closure.

The SDK uses `mutation=True`, strict call-time validation, normal **256 KiB
request / 2 MiB response** bounds, and no automatic retry or splitting.
Sixteen individually valid large entries may exceed the Native body cap (**413**).
After response loss, retain the original key/body; cancellation of an in-flight
mutation does not prove rollback. Context exit closes connections, not stored data.
No LLM/provider, extraction, automatic capture, or publication-quality claim.
The separate single-job `/v1/captures` and `atomic_capture` remain unchanged.
Native/SDK has 31 resources; four MCP tools and the closed hook gain no batch action.
See [the full contract](../STATUS.md#explicit-batch-capture),
[ADR 0022](../adr/0022-batch-capture.md), and [qualification evidence](../STATUS.md#v0022--schema-10).

## Exact entity query and pagination

**v0.0.24/schema 10 contract qualified.**
Use Native JWT authentication and trusted scope UUIDs with current read access.
Read-only `POST /v1/entities/query` needs no write permission or `Idempotency-Key`.
This synthetic first-page request uses both exact filters; do not execute
documentation examples against live data.

```json
{
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "entity_type": "project",
  "canonical_label": "synthetic-entity-example",
  "max_items": 20,
  "before": null
}
```

Closed `QueryEntities` requires 1–32 distinct `scope_ids`; optional `entity_type`
is one of the existing eight `EntityType` literals, and `canonical_label` is
nullable `ShortText` (1–256 characters after normal whitespace stripping).
Both filters default to null. They combine with **AND before LIMIT**; label
equality is exact, case-sensitive `C` collation. Omitted/null filters leave all
currently readable entities in the requested scopes eligible.
Empty/whitespace-only labels give `422 invalid_request`, not an unfiltered query.
There is no alias, fuzzy/substring/wildcard/Unicode-normalization match,
embedding lookup, merge, or automatic identity choice.
`max_items` is a strict integer 1–100, default 20; `before` defaults to null.
Closed `EntityCursor` requires aware `recorded_at` and UUID `memory_id`.
Invalid UUIDs/timestamps/bounds, duplicate scopes, booleans/floats for `max_items`,
or unknown fields give `422 invalid_request`.

Within an already-open trusted `AsyncMemoryClient`, import `QueryEntities` from
`pg_agmemory.models`, build `request = QueryEntities.model_validate(data)`,
and call `page = await memory.query_entities(request)`.
For caller-chosen continuation, keep the intended scopes/filters and pass
`page.next_cursor` as `before` in a new `QueryEntities`; stop on null.
Submitting null again restarts newest, not an exhausted continuation.
Same-label results are distinct IDs: do not choose the first as a resolved identity.
After explicitly choosing a candidate, inspect `await memory.get_entity(chosen_id)`
for unchanged `EntityDetail` evidence, then explicitly choose graph seeds.
The query neither resolves identity nor expands a graph. Catch
`MemoryClientError as exc` and inspect sanitized `exc.error.code`;
do not log raw validation errors, labels, source data, tokens, or responses.

Unlike owned-job query, **there is no ownership filter**: readable shared-scope
entities are included under current tenant/scope/source RLS.
Unknown/private scopes contribute no rows. Zero matches returns this shape;
epochs below are illustrative, not defaults or a hidden-scope explanation.

```json
{
  "entities": [],
  "next_cursor": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

Exactly `200 EntityPage` contains at most 100 existing `EntitySummary` items:
`memory_id`, `revision: 1`, `scope_id`, `entity_type`, `canonical_label`,
`recorded_at`, plus page `next_cursor` and `consistency`
(`access_epoch`, `deletion_epoch`). There are no evidence quotes, source IDs, or
total counts. SQL selects metadata/counts without fetching quotes; labels are
still human text, not content-free data or trusted instructions/current truth.
Selected returned items' visible evidence counts through
`entity_evidence JOIN episode` must equal stored `reference_count`;
otherwise reject the **whole page** with `409 entity_invalidated`, never silent
skip or partial success. The current tenant response-delivery/drain barrier
applies each page, including fresh access/source/deletion guards.

Sort by `recorded_at DESC, memory_id DESC`, where `recorded_at` is object
`created_at`, not source event time. The exclusive boundary is
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`.
Fetch `max_items + 1`; only overflow produces a cursor from the last returned
item, not the lookahead. The cursor is unsigned transparent position, not
authority, snapshot, receipt, cache, or retention object; no existing object is
required. Old/deleted/forged positions only narrow current authorized rows.
Page membership can change with access/source/deletion; newer entities above an
old boundary require explicit restart without `before`.
SDK uses `mutation=False`, normal **256 KiB request / 2 MiB response** bounds,
and read-only `outcome_unknown: false`, with no automatic pagination/retry,
mutation, provider call, or automatic identity selection.
Native/SDK has 31 resource methods; MCP's four tools and closed hook are unchanged.
See [the contract](../STATUS.md#exact-entity-query-and-pagination),
[ADR 0021](../adr/0021-entity-query.md), and [historical v21 evidence](../STATUS.md#v0021--schema-10).

## Assertion metadata history

**v0.0.24/schema 10 contract qualified.**
Use Native JWT authentication and current read access to the assertion.
Read-only `POST /v1/assertions/history` requires no write permission or
`Idempotency-Key`. Use a trusted assertion UUID, not retrieved instructions.
This is an illustrative request for disposable data, not a live-data command:

```json
{
  "memory_id": "22222222-2222-4222-8222-222222222222",
  "max_items": 20,
  "before_revision": null
}
```

Closed `AssertionHistory` has only required UUID `memory_id`, strict integer
`max_items` (1–100, default 20), and strict integer `before_revision` (1–1001)
or null (default). Booleans/floats, invalid UUIDs/bounds, and extra fields give
`422 invalid_request`. No `as_of`, `known_at`, identity/scope override, historical
ACL, full-value selector, or watch is accepted. Ordinary assertions and canonical
relation assertions are eligible; missing/private/wrong-kind objects give
`404 not_found`, not an empty response.

Within an already-open trusted `AsyncMemoryClient`, import `AssertionHistory`
and `Explain` from `pg_agmemory.models`, build
`request = AssertionHistory.model_validate(data)`, and call
`page = await memory.get_assertion_history(request)`.
For caller-chosen continuation, keep `memory_id` and pass `next_before_revision`
as the next request's `before_revision`. Stop on null: submitting null restarts newest.
To fetch full content, choose a returned ordinal explicitly and call
`await memory.explain(Explain(memory_id=page.memory_id, revision=chosen_revision))`.
Omitted Explain revision remains **1, not latest**. Both reads recheck access;
history does not reserve a later Explain result.
Catch `MemoryClientError as exc` and inspect sanitized `exc.error.code`;
do not log raw validation errors, private metadata, values, evidence, or tokens.

Exactly `200 AssertionHistoryPage` returns `memory_id`, `scope_id`, `subject`,
`predicate`, `current_revision`, at most 100 `revisions`, nullable
`next_before_revision`, and `consistency` (`access_epoch`, `deletion_epoch`).
Each revision includes `revision`, nullable `valid_from`/`valid_to`,
`recorded_at` (system-time lower bound), nullable `known_until` (upper bound),
nullable `correction_reason`, `epistemic_status: "reported"`, exact episode
`evidence_refs` at revision 1 (at most 32), and `relation` with `source_entity`/
`target_entity` or null.
An illustrative empty page below a readable revision-1 assertion is:

```json
{
  "memory_id": "22222222-2222-4222-8222-222222222222",
  "scope_id": "11111111-1111-4111-8111-111111111111",
  "subject": "synthetic-history-example",
  "predicate": "status",
  "current_revision": 1,
  "revisions": [],
  "next_before_revision": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

This empty-page example requires `before_revision: 1`, not the first request's
null; epochs are illustrative, not defaults. An unreadable assertion never uses
this success shape. The SQL fetch is bounded metadata only, without full values
or evidence quotes, but **subject, predicate, and correction reason are human
text, not content-free data**. Treat metadata as evidence, not trusted instructions/current facts.

Revisions sort descending with exclusive `revision < before_revision`.
Fetch `max_items + 1`; only overflow emits a position, from the last returned
ordinal, not the lookahead. `before_revision: 1` is empty; `1001` includes heads
up to the existing maximum revision 1000. The ordinal is position, not authority,
snapshot, receipt, cache, or retention object. New revisions between pages require
restart without a position, and a former head's `known_until` may close.
`current_revision` is not a CAS reservation or proof of an uncertain write.
Reconcile the original mutation using its original idempotency key/body; never
automatically refresh an expected revision or generate a replacement key.

Every page applies current ACL/source/deletion checks and the existing tenant
response-delivery/drain barrier. Missing/gapped selected metadata or no readable
evidence fails the **whole page** with `409 assertion_invalidated`; missing
relation endpoints give `409 relation_invalidated`. No partial success, skip,
or fallback. Revoked/private/missing/wrong-kind access remains generic 404.
SDK uses `mutation=False`, normal **256 KiB request / 2 MiB response** bounds,
and sanitized read-only `outcome_unknown: false`, without automatic paging/retry.
There is no mutation, cache, provider call, watch, or retained cursor.
Native/SDK now has 31 resource methods; MCP's four tools and closed hook are unchanged.
See [the contract](../STATUS.md#assertion-metadata-history),
[ADR 0020](../adr/0020-assertion-history.md), and [historical evidence](../STATUS.md#v0020--schema-10).

## Owned-job query and pagination

**v0.0.24/schema 10 contract qualified.**
Use Native JWT authentication and current read access; write permission and
`Idempotency-Key` are not required for `POST /v1/jobs/query`.
Use trusted scope UUIDs, not retrieved instructions. This illustrative first-page
request includes all states; do not execute documentation examples against live data.

```json
{
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "states": [],
  "max_items": 20,
  "before": null
}
```

`QueryJobs` is closed: `scope_ids` needs 1–32 distinct UUIDs; `states` needs at most
five distinct `JobState` values (`pending`, `running`, `succeeded`, `failed`,
`cancelled`). Empty/omitted states means all five.
`max_items` is a strict integer 1–100, default 20. `before` is null/omitted for
newest, otherwise a closed `JobCursor` with required aware `created_at` and UUID
`job_id`. No owner/principal/tenant/kind/payload filter, offset, watch, or `after`
input is accepted. Invalid fields/values give `422 invalid_request`.

Within an already-open trusted `AsyncMemoryClient`, import `QueryJobs` from
`pg_agmemory.models`, build `request = QueryJobs.model_validate(data)`, and call
`page = await memory.query_jobs(request)`. The result is typed `JobPage`.
For a caller-chosen continuation, keep the intended scopes/states and pass the
returned `next_cursor` as `before` in a new `QueryJobs`. Stop when the cursor is
null: submitting null again restarts newest, not an exhausted-page continuation.
There is no SDK automatic pagination or retry. Catch `MemoryClientError as exc`
and inspect sanitized `exc.error.code`; do not log raw model-construction errors,
private job data, input, tokens, or responses.

Query discovers **only the current caller's owned jobs**, including for callers
with scope-admin permission. Known-ID GET still permits another principal's
currently readable same-scope job; do not treat its wider visibility as query authority.
Tenant, requested scopes, current RLS/source/deletion visibility, and optional
state filters all apply. Unknown/private scopes contribute nothing. This example
of an empty result has illustrative current epochs, not a hidden-scope reason:

```json
{
  "jobs": [],
  "next_cursor": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

The complete response contains `ListedJob` items: full existing `JobDetail` plus
`scope_id`, including references, state/timing, safe error, `retry_of`, and original
result revision 1. GET's shape is unchanged. No stored payload, evidence quotes,
`lease_token`, intent digest, or total count is returned.
The response model caps `jobs` at 100; the SDK validates this bound too.
Each selected page item passes `Jobs.get` reference-count/result-liveness checks.
An actual invalid selected job fails the whole page with existing
`404 not_found` / `409 job_invalidated`; never silently skip it or accept partial data.

Rows sort by `created_at DESC, id DESC`; continuation is exclusively
`(created_at, id) < (before.created_at, before.job_id)`. Fetching `max_items + 1`
detects overflow; only then is `next_cursor` built from the last returned item,
not the lookahead row. At most `max_items` jobs return.
Cursor data is unsigned position, not authority, a receipt, cache, snapshot,
or retention object. Its job need not exist; fabricated/stale/deleted positions
only bound current authorized owned jobs.
State changes preserve original `created_at`, but state/access/deletion changes
can alter membership between pages. Newer jobs above the cursor require an
explicit restart without `before`. `Consistency` is current epochs, not stable
membership, a snapshot watermark, or global LSN.
The current permission/deletion/tenant response-drain barrier applies each page.

SDK uses `mutation=False`, normal **256 KiB request / 2 MiB response** limits,
and read-only `outcome_unknown: false`. No state change, claim, cancellation,
worker/provider call, persistent cursor, or new safe error code is added.
**Historical v0.0.19 smoke only:** the production SDK smoke passed locally and on both native architectures:
source + three jobs → first pending page of one item → explicitly cancel the
middle job → current next page contains only the oldest job → query cancelled
jobs → source purge `object_count: 4` → empty pending query.
Cancellation is a separate explicit mutation, not a query side effect.
There are **31 Native/SDK resource methods**, four unchanged MCP tools, and no
job tool or hook field. See [the contract](../STATUS.md#owned-job-query-and-pagination),
[ADR 0019](../adr/0019-job-query.md), and [historical evidence](../STATUS.md#v0019--schema-10).

## Checkpoint-head lookup

**Retained checkpoint-head contract; v0.0.24 qualified.**
Use the existing Native JWT identity with current read permission on the exact
scope. Write permission is not required. Send `POST /v1/checkpoints/head` with
the following `CheckpointBranch` body; no `Idempotency-Key` is needed.
Replace the illustrative UUIDs with a known scope-local run/branch.
Do not execute these examples against a live database during documentation review.

```json
{
  "scope_id": "11111111-1111-4111-8111-111111111111",
  "run_id": "22222222-2222-4222-8222-222222222222",
  "branch_id": "33333333-3333-4333-8333-333333333333"
}
```

All three UUID fields are required; the model is closed to extras.
Do not send identity overrides, `expected_head`, harness selectors, `as_of`,
cross-branch latest selectors, or history-listing fields.
Within an already-open `AsyncMemoryClient`, import `CheckpointBranch` from
`pg_agmemory.models`, build `branch = CheckpointBranch.model_validate(data)`,
and call `head = await memory.get_checkpoint_head(branch)`.
The typed result is `CheckpointEnvelope`. Catch `MemoryClientError as exc` and use
`exc.error.code` for sanitized handling; never log raw private state, input, token, or responses.
Initial model-construction errors may contain private input and must not be logged raw.

| Outcome | Handling |
|---|---|
| `200` | Full checked envelope for the point-in-time exact branch head, not an execution instruction |
| `422 invalid_request` | Missing/invalid UUID or extra request field; correct the caller input |
| `404 not_found` | Missing/private/wrong-scope/cross-tenant/empty non-invalidated branch, or an invisible head; no requested IDs/content or empty success |
| `409 checkpoint_invalidated` | Invalidated readable branch, run/integrity rejection under existing envelope rules, or loaded scope/run/branch/sequence mismatch; never fall back |

Source `forget` marks affected branches `invalidated=true` and deletes canonical
checkpoint/reference payloads, retaining opaque `head_id` and `sequence`,
`memory.object` anchors, and tombstones. Lookup checks invalidation first:
a still-readable invalidated branch returns 409, never the retained head ID.
After access revocation the branch is hidden with 404.
Do not seek an earlier ancestor, sibling, or default branch to avoid invalidation.
GET by a known checkpoint ID may still load a live surviving ancestor under its
existing checks, but does not identify latest. Restored forks have independent
target heads; head lookup neither restores nor transitions effects.

The same envelope helper checks HMAC/state/references and current authorization,
retains run `effects_invalidated` rejection, and returns saved/current epochs plus
the current run-wide `tool_effects`, `requires_reconciliation`, `untracked_effects`,
`resume_allowed`, and `automatic_reexecution: false`.
Saved epochs and `resume_allowed: true` are not permission, approval, or provider receipts.
Inspect and reconcile effects before a host considers continuation.
This is no harness adapter, compaction mechanism, or general recovery qualification.

Keep the stable scope/run/branch IDs to locate the head after losing a checkpoint ID.
A read is not a watch, reservation, or successor guarantee.
For an intended write, explicitly choose `CreateCheckpoint.expected_head`
(the exact current checkpoint ID, or null only for an empty branch).
Another writer can advance the head after the lookup: handle
`409 checkpoint_head_conflict` by reconsidering the intended write, not by automatic retry.
**Do not use head lookup to prove an uncertain mutation committed.**
Retry the original mutation with its original idempotency key/body for its
original receipt under current replay guards, then inspect the head.
No fresh key, ancestor fallback, automatic restore/execution, or implicit approval follows.

The lookup uses SQL `SELECT` without `FOR UPDATE` under the existing API tenant
session-lock/drain barrier. It creates no branch/run, state change, audit event,
or idempotency receipt. SDK `_post` uses `mutation=False`, normal **256 KiB request /
2 MiB response** bounds, read-only `outcome_unknown: false`, and no automatic retry.
There are **31 Native/SDK resource methods**, but still **four MCP tools**;
no checkpoint tool or hook field is added. `checkpoint_invalidated` is an existing
SDK safe code, not a new error code.
The historical v0.0.18 production SDK smoke passed locally and on both native architectures:
source + two checkpoints → head sequence 2 / historical GET sequence 1 →
source purge `object_count: 3` → head `409 checkpoint_invalidated`.
See [the contract](../STATUS.md#checkpoint-head-lookup),
[ADR 0018](../adr/0018-checkpoint-head.md), and [verified evidence](../STATUS.md#v0018--schema-10).

## Exact structured recall filters

**Retained recall-filter contract; v0.0.24 qualified.**
Use the existing authenticated `POST /v1/recall`. The following synthetic
request browses assertions with an exact stored subject/predicate in an already
authorized scope. Replace the illustrative UUID with a provisioned scope;
do not execute documentation examples against a live database.

```json
{
  "query": "",
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "mode": "explicit",
  "retrieval_mode": "lexical",
  "filters": {
    "kind": "assertion",
    "subject": "Synthetic Project",
    "predicate": "uses"
  },
  "max_items": 4,
  "token_budget": 2000
}
```

With an already-open `AsyncMemoryClient`, import `Recall` and `RecallFilters`
from `pg_agmemory.models`; parse the object with `Recall.model_validate(data)`
and call `await memory.recall(request)`. Alternatively construct the nested
value with `RecallFilters(kind="assertion", subject="Synthetic Project", predicate="uses")`.
No idempotency key is needed. Handle `MemoryClientError` through sanitized
`exc.error.code`; read-only errors have `outcome_unknown: false`, with no automatic
retry or raw response/input logging. Initial model-construction errors can contain
private input and must not be logged raw.
MCP accepts the same object in `memory_recall`'s `{request: <Recall>}` wrapper.
The hook rejects `filters`; its internal default is `None`.

`Recall.filters` defaults to `None`. The closed `RecallFilters` model accepts only
nullable `kind` (`"episode"`/`"assertion"`), `subject` (`ShortText`, 1–256 characters
after existing whitespace trim), and `predicate` (`^[a-z][a-z0-9_]{0,63}$`);
each field defaults to null. Omitted/null/`{}`/all-null filters preserve the
existing results in all three retrieval modes.
Non-null fields are ANDed. Subject/predicate select assertion candidates, including
relations; `"episode"` excludes all assertions and cannot combine with those fields.
Equality is exact and case-sensitive under `C` collation after normal string trim.
`"Synthetic Project"` is not `"synthetic project"`; no substring, FTS, Unicode
normalization, fuzzy matching, alias, or entity resolution is performed.
Use no ranges, array-valued filters, inferred selectors, or arbitrary SQL.

Empty lexical query browses filtered candidates; normal nonempty lexical queries
still require matching. Filters do not provide query bypass.
They apply inside the shared materialized candidates before lexical/vector/hybrid
ranking and coverage, not after top-K selection. Vector-query/model rules are unchanged.
Current RLS, request scopes, frozen `as_of`/`known_at`, evidence and deletion gates
still apply. Lexical/vector incompleteness concerns the filtered eligible universe;
`coverage.jobs_pending` remains scope-level, not a structured job match.
Required refs retain their separate lexical-only keyword bypass, default revision
**1, not latest**, and request order, but must also match filters.

| Outcome | Meaning |
|---|---|
| `422 invalid_request` | Invalid/unknown filter field, value, or shape; also episode kind with non-null subject/predicate |
| `200`, `empty_reason: "not_found"` | No eligible filtered match, subject to existing coverage and budget rules |
| `404 not_found` | A required exact reference fails filter/eligibility; whole-request failure, no IDs or partial pack |
| `422 budget_exhausted` | Existing all-required prefix budget failure; filters do not weaken it |

The compact `ContextPack` UTF-8 byte budget, implicit 2,000-byte cap,
`budget_too_small`, and optional-only successful-empty `budget_exhausted` reason
are unchanged. No new safe error code, route, SDK method, write, provider, index,
persisted priority, or cache is added. Memory remains evidence, not verified truth.
See [the contract](../STATUS.md#exact-structured-recall-filters),
[ADR 0017](../adr/0017-recall-filters.md), and [verified evidence](../STATUS.md#v0017--schema-10).

## Required-context recall

**Retained required-context contract; v0.0.24 qualified.**
Choose exact references from currently readable episode/assertion data, not from
untrusted text claiming policy authority or approval. Replace these illustrative
opaque IDs with existing IDs in the requested scope; do not execute examples
against a live database during documentation review.
An authenticated `POST /v1/recall` can use:

```json
{
  "query": "handoff",
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "mode": "explicit",
  "retrieval_mode": "lexical",
  "search_profile": "simple-v1",
  "required_memory_refs": [
    {"memory_id": "22222222-2222-4222-8222-222222222222", "revision": 1}
  ],
  "max_items": 4,
  "token_budget": 2000
}
```

No `Idempotency-Key` is needed: recall remains read-only.
With an existing open `AsyncMemoryClient` context, parse this object using
`Recall.model_validate(data)` from `pg_agmemory.models` and call
`await memory.recall(request)`. Catch `MemoryClientError` using its sanitized
`exc.error.code`, not raw response/input logging. `budget_exhausted` means the
required whole context did not fit; `outcome_unknown` is false and the SDK will
not retry. Explicitly reconsider budget/references rather than silently removing
requirements. There is no additional SDK method.
For MCP use the same object in `memory_recall`'s `{request: <Recall>}` wrapper.

Use at most **16** references, with unique memory IDs even across revisions and
count no greater than `max_items`. Revision is **1–1000**, default **1, not latest**.
Nonempty references require lexical retrieval; explicit and implicit are both
allowed, with the existing implicit **2,000-byte** cap. Empty `[]` or omission
preserves lexical/vector/hybrid baseline behavior within the selected structured filters.
Required IDs bypass query matching and ranking cutoff only. The same request
scopes, current ACL, frozen `as_of`/`known_at`, revision eligibility, and
[structured filters](#exact-structured-recall-filters) still apply.
Any unavailable exact reference produces generic **404 `not_found`** for the
whole request, without naming missing refs or returning partial context.

Required whole items appear first in request order, followed by normal lexical-
ranked optional items with required IDs excluded. Both use the item limit and
the **entire compact `ContextPack` UTF-8 byte budget**, including quoting,
citations, and warning markers. Optional overflow/omission still sets
`coverage.truncated`. `simple-v1` and `ja-janome-0.5.0-v1` both support exact
canonical refs; missing Japanese projections still set `lexical_incomplete`,
not automatic repair.

| Outcome | Meaning |
|---|---|
| `422 invalid_request` | Invalid combination/count/duplicate ID/reference shape, including nonempty refs in vector/hybrid |
| `404 not_found` | At least one exact reference is not an eligible, currently readable requested-scope candidate |
| `422 budget_exhausted` | A whole required item cannot fit; no successful empty/partial context |
| `422 budget_too_small` | Existing empty-envelope failure without required refs |
| `200`, `empty_reason: "budget_exhausted"` | Existing optional-only successful empty result, not the new error |

Required recall may return `budget_exhausted` at budget 64 even if the empty
envelope cannot fit. Do not equate `token_budget` with exact model tokens.
SDK and MCP propagate the new safe code. The hook rejects
`required_memory_refs` as an extra input field; its internal `Recall` uses `[]`,
so its normal optional-only budget/coverage behavior and three events are unchanged.
No host pinning, persistent priority, write, inference, provider call, or cache is added.
Selected memory remains evidence, not trusted instructions, approval, or a
guaranteed current fact. See [the contract](../STATUS.md#required-context-recall)
and [ADR 0016](../adr/0016-required-context.md).

## Schema 10 application-only upgrade

**v0.0.24 qualified.** v23→v24 keeps schema 10 and adds **no migration**.
Do not apply a new schema version just to match the application version.

1. Stop/drain old APIs, workers, SDK callers, MCP adapters, hook/inference launches, and
   admin commands, including replicas/restarts. Preserve backups and current
   ACL/deletion records; no rolling/mixed-version compatibility is claimed.
2. Keep the pinned PostgreSQL **18.6** / `vector` **0.8.6 in `public`** image and
   exact migration history `[1,2,3,4,5,6,7,8,9,10]`. Existing schema-10 databases
   need no DDL/backfill for this milestone. Older schemas require the retained
   [migration sequence through 010](#schema-10-job-cancellation-upgrade) while stopped.
3. Start only matching v0.0.24 API/worker/SDK/MCP/hook components.
   All adapter handshakes require **service 0.0.24 / API v1 / schema 10**;
   startup/readiness keep their exact history and role/extension checks.
4. Check authenticated stage `m2-selectable-inference`, feature `optional_provider_adapters`,
   and the [exact `model_inference` metadata](../STATUS.md#deployment-and-qualification-boundary).
   `live_provider_qualified` remains `false` after synthetic contract qualification.
   Native episode/capture/batch and other resource contracts remain.
   Install the optional provider extra only where needed and approve each profile
   separately. Rehearse synthetic HTTP/SQL guards and explicit non-publishing inference;
   SQL `inspect` is not live-model qualification or MemoryDB hosting certification.
   Readiness alone is insufficient; the implementation evidence below does not
   qualify production deployment.

There are now 31 Native/SDK resource methods and four MCP tools; the hook accepts
neither filters nor required references. No dependency-version or MemoryDB image upgrade is introduced;
provider execution is confined to the separate operator library/CLI.
**V24 implementation and synthetic-provider contracts qualified.**
Implementation
[`88975a862ff97873c60e5ce53e066e1aa7b52686`](https://github.com/rioriost/pg_agmemory/commit/88975a862ff97873c60e5ce53e066e1aa7b52686)
passed the full Apple Container `./scripts/test-containers.sh`:
**987 passed, 1 warning, 493.68 s**.
Exact-SHA [CI 35292285229](https://github.com/rioriost/pg_agmemory/actions/runs/35292285229)
passed: **amd64 987 / 762.27 s; arm64 987 / 809.06 s**.
All three environments passed Ruff, mypy **22 source files + 1 strict SDK consumer**,
all four core/hook/sdk/providers installation profiles, and all production smokes.
The new smoke uses the actual operator CLI against synthetic HTTP, then explicit
Native vector upload/replay/purge. **987 = 821 retained + 166 new cases**.
The SQL tests use synthetic functions/extension membership, not the Azure extension binary.
No live Azure/real-model calls, billed resources, or private-data egress were used.
These results do not establish live compatibility, quality, or M2 completion.
See [qualification evidence](../STATUS.md#v0024--schema-10).
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
**821 = 780 retained + 41 new cases**; see [qualification evidence](../STATUS.md#v0023--schema-10).
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
A negative control against unmodified pre-fix source in an earlier test image
failed as expected. Its retained synthetic plan is not the failed runner's plan.

Final-docs revision
[`af91974d091feb276db79baf838c7fa2bab8904f`](https://github.com/rioriost/pg_agmemory/commit/af91974d091feb276db79baf838c7fa2bab8904f)
**failed** [CI 35265233011](https://github.com/rioriost/pg_agmemory/actions/runs/35265233011):
amd64 **772 passed, 1 failed, 631.34 s**, with **503 `QueryCanceled`** at the
existing 100-path auto-plan graph case; arm64 **773 passed, 701.79 s**, with all smokes.
The actual failed CI plan was **not captured**. A retained disposable diagnostic
shows residual canonical-metadata and endpoint rescans after `adjacent` materialization.
The follow-up retains that CTE and separately materializes scope/predicate-filtered
assertions, time-filtered revisions, and distinct authorized/evidence-valid
endpoints, using precomputed ID arrays to prevent reversed semijoins and repeated
protected canonical scans. The outer join uses materialized authorized metadata.
RLS, scope/time/evidence checks, ordering, limits, and the **5000 ms** timeout
remain unchanged; raising the timeout or rerunning unchanged code is not the fix.
The qualified directional extension covers all nine combinations and their scan-loop checks.
This diagnostic is not a performance benchmark or production qualification.
Version/API/schema and 30 resource methods remain unchanged: a matching v0.0.22
handshake alone cannot identify the fixed build. Track commit/image provenance
when distinguishing the initial batch build, canonical SQL fix, and qualified directional revision.

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
See [qualification evidence](../STATUS.md#v0022--schema-10).

**Historical v0.0.21 follow-up verified locally and on both native architectures.**
Fix [`956b232f38caeeb7d0421a2d6fd3d8340206bcbc`](https://github.com/rioriost/pg_agmemory/commit/956b232f38caeeb7d0421a2d6fd3d8340206bcbc)
materializes bounded graph adjacency to remove repeated relation-revision scans
reproduced under a forced generic prepared plan. RLS, temporal/scope/evidence
checks, ordering, and limits are preserved; no timeout increase or JIT disable
is used. The failing CI plan was not captured, and the disposable-fixture
diagnostic is not a production performance benchmark.
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
Version/schema/API and the 29 resource methods are unchanged; no SQL migration.
A matching version handshake cannot distinguish fixed and earlier v21 builds;
track the application commit/image provenance.

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
See [qualification evidence](../STATUS.md#v0021--schema-10).

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
neither run qualifies v0.0.21. See [historical evidence](../STATUS.md#v0020--schema-10).

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
neither run qualifies v0.0.20. See [historical evidence](../STATUS.md#v0019--schema-10).

**Historical v0.0.18 implementation verified locally and on both native architectures.** Apple Container
`./scripts/test-containers.sh` exited **0**, with **630 passed, 1 existing warning,
369.39 s (6:09)**. Ruff, strict mypy **19 source files + 1 SDK consumer**, genuine
core/hook/sdk-only installs, and all non-root production smokes passed in all three environments, including
the new checkpoint-head sequence above. Implementation
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
Neither run qualifies v0.0.19; see [historical evidence](../STATUS.md#v0018--schema-10).

**Historical v0.0.17 implementation evidence only:**
Apple Container and both native Docker runs each passed **604 tests, 1 existing warning**:
local **321.56 s (5:21)**, amd64 **638.66 s**, arm64 **544.33 s**.
[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)
passed on exact implementation
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894).
All three environments passed Ruff, strict mypy **19 source files + 1 SDK consumer**,
genuine core/hook/sdk-only installs, and all non-root production smokes.
The new SDK smoke passed observe + remember, exact three-field match with
`max_items: 1` and no truncation, required source/assertion-filter mismatch yielding
`404 not_found` with read-only `outcome_unknown: false`, source purge returning
`object_count: 2`, and empty filtered read.
Separate final v0.0.17 docs
[`5226c81fae7a50a5668109478d5d9f23fc4d7761`](https://github.com/rioriost/pg_agmemory/commit/5226c81fae7a50a5668109478d5d9f23fc4d7761)
passed [CI 35232139680](https://github.com/rioriost/pg_agmemory/actions/runs/35232139680):
native amd64 **465.50 s**, arm64 **557.08 s**, each **604 tests, 1 warning**,
with Ruff, strict mypy **19 source files + 1 consumer**, optional installs, and all production smokes.
These docs timings are separate from implementation CI 35230044140.
Neither run qualifies v0.0.19; see [historical evidence](../STATUS.md#v0017--schema-10).

**Historical v0.0.16 implementation evidence only:**
The local Apple Container and both native Docker runs each passed **566 tests,
1 existing warning**: local **298.15 s (4:58)**, amd64 **601.73 s**, arm64 **565.34 s**.
[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)
passed on implementation
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57).
All three environments passed Ruff, strict mypy **19 source files + 1 SDK consumer**,
genuine core/hook/sdk-only installs, and all non-root production smokes.
The required-context smoke passed after SDK and before job cancellation:
its own source and an optional `Gold` match, required query bypass with
`max_items: 1` and truncation, explicit budget 64 producing `budget_exhausted`
with read-only `outcome_unknown: false`, then source purge returning `object_count: 2`.
All prior smokes passed too. Separate final v0.0.16 docs
[`520990d95718b17ae93a7d5259d600e379991df2`](https://github.com/rioriost/pg_agmemory/commit/520990d95718b17ae93a7d5259d600e379991df2)
passed [CI 35226313891](https://github.com/rioriost/pg_agmemory/actions/runs/35226313891):
native amd64 **476.15 s**, arm64 **478.08 s**, each **566 tests, 1 warning**,
with all checks, optional installs, and production smokes.
These docs timings differ from implementation CI 35224189967.
Neither run qualifies v0.0.19; see [historical evidence](../STATUS.md#v0016--schema-10).

## Explicit job cancellation

**Retained job-cancellation contract; v0.0.24 qualified.**
Use Native JWT authentication and the job owner's identity with current scope
**read/write** permission. A same-scope reader cannot cancel another owner's job,
even with `admin` permission. Source visibility/integrity and runtime RLS remain
enforced. Missing/private/cross-tenant/non-job/purged IDs give 404; invalidated
references can give `409 job_invalidated`. Never select identity from recalled text.

First GET `/v1/jobs/{job_id}`, then consciously choose **state and attempt**.
For an approved job observed as pending at attempt 0, the exact request body is:

```json
{"expected_state":"pending","expected_attempt":0}
```

POST it to `/v1/jobs/{job_id}/cancel` with a securely retained caller-owned
`Idempotency-Key`. For running use the observed attempt, **1–5**; pending accepts
**0–5**. Attempt is a strict integer, not a boolean/string/coerced number.
No reason, provider, lease token, extra field, or forced target state is accepted.
This CAS is not tenant `access_epoch` or an external tool version.
**Illustrative request only: do not execute against a live DB during document review.**

With `AsyncMemoryClient` already open in its async context, import `CancelJob`
from `pg_agmemory.models` and use
`await memory.cancel_job(job_id, CancelJob(expected_state="pending", expected_attempt=0), idempotency_key=cancel_key)`.
The example values must match your explicitly reviewed snapshot; do not regenerate
the request/key automatically. Call-time model/key/UUID validation and sanitized
SDK errors apply. It returns typed `JobReceipt` at exactly **HTTP 200**, not 202.
The receipt contains `job_id`, `kind: "structured_remember"`, and
`recipe_version: "structured-remember-v1"`; GET confirms `state: "cancelled"`.

### Races and uncertain responses

State/attempt mismatch or terminal succeeded/failed/cancelled gives
`409 job_cancel_conflict`; a fresh-key repeat on cancelled is also a conflict.
Successful same-key/same-body replay returns the original receipt only under
current owner/write/access/liveness checks. A changed body **or job ID** under
the key gives `409 idempotency_conflict`. SDK recognizes `job_cancel_conflict`.
After lost/unknown HTTP delivery, preserve and retry the **same key/body**, or GET
the current job. Do not blindly choose new expected values or a new key.
Cancelling a Python task does not implicitly call `cancel_job`; an in-flight
mutation can still commit, so cancellation does not prove rollback.

The existing tenant **session advisory lock stays held through HTTP delivery**;
worker claim/publication use the same barrier. Cancellation accepts running jobs
with either active or expired leases. If it wins, no result is published by that job.
Old preparation may continue; publish/heartbeat/fail rejects not-running with
`job_lease_conflict`, and the worker reports `lease_lost`.
If publication commits first, cancellation conflicts and the succeeded result
remains. No worker kill, provider abort, compensation, or unpublishing is promised.
Use explicit `forget` when data removal is intended.

### What is retained

Terminal cancelled clears the stored job payload, lease token/deadline, and error,
with no result. It preserves job ID, attempt, input/source/intent references,
retry parent, and creation time; DB `updated_at` records completion.
No private cancellation reason is stored.
Job transition, `job_cancelled` audit, and the HMAC-backed request/key idempotency
receipt commit atomically or all roll back. The stored replay result is only `{job_id}`.
No access/deletion epoch advances. DB constraints/trigger prevent terminal
revival or rewrite; new jobs must still start pending at attempt 0.

Cancelled jobs are excluded from the 100 pending/running cap and `jobs_pending`.
Retry remains failed-only (`409 job_retry_conflict` for cancelled). Same-intent
enqueue/capture dedups to the cancelled job; capture replay keeps the original
episode/job pair. New HTTP keys or invented recipe/intent keys are not a revival path.
Cancellation is **not purge**: canonical episodes/evidence, intent/dedup anchors,
prepared worker memory, WAL, and backups are not erased.
Source forget still purges dependent jobs and denies cancellation replay;
later grants cannot resurrect purged payload. See [the contract](../STATUS.md#explicit-job-cancellation)
and [ADR 0015](../adr/0015-job-cancellation.md).

## Schema 10 job-cancellation upgrade

**Retained migration for schemas below 10; v0.0.24 qualified.**
Existing schema-10 databases use the [application-only upgrade](#schema-10-application-only-upgrade).
Schema 9→10 requires **`010_job_cancellation.sql`**, introduced in v0.0.15.
It modifies existing job state/payload constraints and the guard trigger; no
table is added. Providers, dependencies, PostgreSQL 18.6/pgvector 0.8.6, and pinned
images are unchanged.

1. Stop/drain old/new APIs, workers, SDK callers, MCP adapters, hook launches,
   and admin commands, including replicas/restarts. Preserve current ACL/deletion
   records and backups; rehearse only with disposable data.
2. Use matching v0.0.24 migration tooling and `PGAG_ADMIN_DATABASE_URL` to run
   `pg-agmemory migrate`, applying 010 after exact history 001–009.
   Older schemas must apply all retained migrations too. Runtime credentials
   must not migrate or bypass the schema guard.
3. Verify exact history `[1,2,3,4,5,6,7,8,9,10]` and `vector` 0.8.6 in `public`.
   Start only matching v0.0.24 API/worker/SDK/MCP/hook components, requiring
   **service 0.0.24 / API v1 / schema 10**. No mixed-version rollout or downgrade.
4. Check authenticated stage `m2-selectable-inference`, retained `job_cancellation` metadata
   (`endpoint: "/v1/jobs/{job_id}/cancel"`, `compare_and_swap: ["state", "attempt"]`,
   `terminal_state: "cancelled"`, `provider_interruption: false`), bounded readiness,
   and approved resource/adapter checks before reopening traffic.

Historical v0.0.15 implementation
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)
passed [CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)
on that exact SHA. Actual logs verified **535 tests, 1 existing warning** per native
architecture: **406.88 s amd64 / 490.57 s arm64**; local Apple Container passed
**535 tests, 1 existing warning, 298.29 s (4:58)**.
Ruff, strict mypy **19 source files + 1 SDK consumer**, genuine optional installs,
and all production smokes passed in all three environments.
Schema-9→10 ledger failure after DDL restored the prior guard function,
constraints, and schema-9 history before retry; legacy v6 job state/attempt/payload
remained unchanged. The production smoke passed actual SDK enqueue → cancel →
same-key replay → GET cancelled → worker `--once` idle → source purge,
returning `object_count: 2` for that fixture.
Separate final v0.0.15 docs
[`9d34d5329c9db580e7de0459b743511235ad6fb8`](https://github.com/rioriost/pg_agmemory/commit/9d34d5329c9db580e7de0459b743511235ad6fb8)
passed [CI 35218254940](https://github.com/rioriost/pg_agmemory/actions/runs/35218254940):
native logs verified **535 tests, 1 warning** each, **605.83 s amd64 / 473.57 s arm64**,
Ruff, strict mypy **19 source files + 1 consumer**, optional installs, and all smokes.
These docs timings differ from implementation CI 35216770999; neither run validates v0.0.24.
Elapsed times are not performance benchmarks; see [historical evidence](../STATUS.md#v0015--schema-10).
There are now 31 Native resource/SDK methods. MCP's four tools and read-only
hook are unchanged. No MVP/production/quality/DR qualification is claimed.

## Runtime readiness

**Retained readiness contract; v0.0.24/schema 10 qualified.**
Keep liveness and dependency readiness separate:
`GET /healthz` returns exactly `{"status":"ok"}` after successful startup and
does not contact the DB. Public, unauthenticated `GET /readyz` returns exactly
`{"status":"ready"}` with **200**, or `{"status":"not_ready"}` with an
expected-failure **503**. Both readiness responses carry `Cache-Control: no-store`
and a generated UUID `X-Request-ID`. Authorization headers are ignored; do not
send a token or select a tenant/principal. No private reason or schema inventory
is returned in the readiness body.
Both OpenAPI responses use `ReadinessStatus`, not Native `ErrorBody`.
A wrong HTTP method returns 405 without a readiness check.

### Inspect without changing data

**Illustrative commands only; do not run against a live DB during documentation review.**
Replace this placeholder with the trusted, approved API origin. No credentials
or automatic retries are needed. `curl`'s example 10-second limit is a caller
budget, not the server's wall-clock guarantee; an HTTP 503 exits curl with 22.

```bash
PROBE_API_URL='https://memory.example.invalid'
curl --include --silent --show-error --fail-with-body --max-time 10 \
  "$PROBE_API_URL/healthz"
curl --include --silent --show-error --fail-with-body --max-time 10 \
  "$PROBE_API_URL/readyz"
```

Every admitted readiness check opens a fresh connection with `PGAG_DATABASE_URL`
through `validate_runtime`; never supply `PGAG_ADMIN_DATABASE_URL` or add a fallback.
It uses at most four explicit SQL statements: one `SET` and three `SELECT`
statements for role/schema/extension catalogs and the schema ledger.
`default_transaction_read_only = on` is confined to dedicated validation
connections, including startup/worker validation; later Native mutations remain
writable. It rejects superuser, `BYPASSRLS`, and table ownership/owner-role
membership in `memory`/`memory_ops`, including `NOINHERIT`, and requires exact
history `[1,2,3,4,5,6,7,8,9,10]` plus `vector` 0.8.6 in `public`.
It reads no memory payload, takes no tenant lock, and writes no audit, epoch,
job, receipt, source, or tombstone. No migration, cache, background polling,
provider call, or retry is performed.

### Interpret failures without restart storms

One active check is admitted per app/process. Concurrent requests immediately
return 503/log reason `probe_busy`, with no second connection or waiting.
The fixed **5.0 s active-check budget**, plus retained DB connect/statement/lock
budgets of **5 s**, is **not a hard wall-clock SLA**: cancellation/connection
cleanup can take longer. Cancellation propagates and releases the gate/connection.

Correlate the generated `X-Request-ID` with `readiness_unavailable` log
`request_id`. The static `reason` is `runtime_role_invalid`, `schema_unavailable`,
`schema_version_mismatch`, `extension_version_mismatch`, `probe_busy`, or an
exception class name. Expected `RuntimeValidationError`, `psycopg.Error`, and
`TimeoutError` produce 503 without logging raw error text, tracebacks, DSNs,
credentials, or payloads. Unexpected programming exceptions are not converted
to 503; do not treat an ordinary `RuntimeError` as expected drift.
Investigate trusted configuration/database state separately; never elevate the
runtime role or trigger automatic migration to make a probe pass.

A ready result is only a point-in-time connection/runtime-role/schema/vector
check. A SELECT-only DB may pass: it does not prove writes/primary status,
all principal permissions, table grants/RLS policy integrity, JWT verification,
tokenizer/provider health, backlog/load, HA/DR, or production/quality/performance.
Resource routes do not call readiness or acquire a permanent post-drift gate;
existing Native authorization still applies. Use this signal to stop traffic,
not as an authorization firewall.
Do not wire dependency readiness as liveness and create restart storms.
Configure failure/recovery thresholds, allowing for busy 503 responses, and
restrict/rate-limit public probes at the deployment perimeter.
Admission is per process, not a global rate limiter or request-flood qualification.
No Kubernetes, Compose, or Docker `HEALTHCHECK` wiring is supplied.

### Schema 9 application-only upgrade

**Historical v0.0.13→v0.0.14 procedure only, not the v0.0.24 upgrade.**
Use [schema-10 maintenance](#schema-10-job-cancellation-upgrade) for current tooling.

v0.0.13→v0.0.14 adds **no migration** or dependency/image upgrade.
Keep PostgreSQL 18.6, `vector` 0.8.6 in `public`, and exact schema history 1–9.
Stop/drain old APIs, workers, adapters, hook launches, SDK callers, and admin
commands, including replicas/restarts; preserve current ACL/deletion records
and backups. Deploy only matching v0.0.14 components, not a mixed-version rollout.
Existing startup still fails closed; SDK/MCP/hook require exact
**service 0.0.14 / API v1 / schema 9**.
Check authenticated capabilities stage `m2-runtime-readiness` and `health_probes`
metadata (`liveness: "/healthz"`, `readiness: "/readyz"`,
`readiness_timeout_seconds: 5.0`, `readiness_max_in_flight_per_process: 1`).
Then check liveness, bounded readiness, and approved authenticated resource/
adapter behavior before reopening traffic; ready alone is insufficient.
That historical procedure required migrations through 009. Current upgrades
from older schemas instead need the [sequence through 010](#schema-10-job-cancellation-upgrade).

The historical v0.0.14 disposable-DB production smoke uses the same API process after
the normal HTTP smoke: ready 200 → schema-ledger
rename → ready 503 while health stays 200 → ledger restoration → ready 200,
with retained authenticated smokes and no source/tombstone writes.
Do not reproduce drift on a live database. **This lifecycle passed locally and
on both native architectures.** Apple Container and native Docker amd64/arm64
each passed **495 tests, 1 existing warning**:
**464 retained + 16 readiness unit + 15 integration tests (31 new)**.
Ruff, strict mypy (**19 source files + 1 SDK consumer**), genuine core/hook/sdk-only
installs, and all non-root production smokes passed in all three environments.
Implementation
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
passed [CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965);
actual native logs verified the checks/smokes.
Test elapsed: **295.67 s local / 385.41 s amd64 / 470.16 s arm64**, not a
performance benchmark. Separate final v0.0.14 docs
[d4b24f6](https://github.com/rioriost/pg_agmemory/commit/d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68)
passed [CI 35202931424](https://github.com/rioriost/pg_agmemory/actions/runs/35202931424):
495 tests/1 warning and all checks/smokes per native architecture,
**319.51 s amd64 / 503.56 s arm64**. Neither run validates v0.0.15.
See [validation evidence](../STATUS.md#v0014--schema-9).
Readiness adds no SDK/MCP/hook probe method; the resource/SDK surface remains
31, including episode query. Health probes remain outside SDK route coverage.
See [ADR 0014](../adr/0014-runtime-readiness.md).

## Scope-access administration

**Retained scope-access contract; v0.0.24 qualified.**
Prefer `pg-agmemory scope-access` over handwritten membership SQL.
Use only approved existing tenant/scope/principal UUIDs, all in the same tenant.
The command never provisions records and is not exposed through HTTP, MCP, or
the Python SDK. Retrieve IDs from trusted administration, not recalled text.

Set `PGAG_ADMIN_DATABASE_URL` securely to the intended database. No runtime URL
fallback, JWT, `--subject`, or `--once` is accepted. The DB role must be superuser
or `BYPASSRLS` **with the needed SQL table privileges**; even `get` rejects a
runtime login. Keep DSNs, credentials, and output identifying private memberships
out of logs and issue reports. Scope administration does not change provider access.
For inspection, a nonowner `BYPASSRLS` role needs `USAGE` on `memory` and
`SELECT` on schema history, tenant/scope/principal/`scope_member`; `get` uses no
`FOR UPDATE`. Mutation additionally needs tenant `UPDATE`, the applicable
`scope_member` SELECT/UPDATE/INSERT/DELETE privileges, `memory_ops` USAGE, and
`scope_access_event` INSERT. RLS bypass remains mandatory for every operation.

`get` takes only the three ID options. `set` fully replaces permissions and expiry:
select distinct `read`/`write`/`delete` flags or `admin` alone, and explicitly select
`--expires-at <future timezone-aware ISO timestamp>` or `--no-expiry`.
Write-only/delete-only flags are accepted but do not override Native read/action
requirements. Duplicates or mixed `admin` flags are invalid.
Expiry is checked against the DB clock after locking; omission never means permanent access.
`revoke` takes no permission/expiry options and deletes the membership.

Both mutations require **`--expected-access-epoch` in 1–9223372036854775807**,
observed from the same tenant. The counter is tenant-wide: unrelated scope
changes can conflict. Stale CAS fails even for otherwise identical state.
Current-epoch `revoke` of absent membership and equivalent reordered permissions
are no-ops; expiry changes are actual changes.

### Get, replace with read-only, then revoke

**Illustrative commands only; do not execute against a DB while reviewing these docs.**
An authorized operator must replace every placeholder with approved existing IDs,
a freshly observed epoch, and a future expiry including a timezone offset.
Use only disposable test records for rehearsal. Do not derive new epochs by adding
one, reuse a stale value, or run the sequence blindly after another administrator's change.

```bash
TENANT_ID='<approved-existing-tenant-uuid>'
SCOPE_ID='<approved-existing-scope-uuid>'
PRINCIPAL_ID='<approved-existing-principal-uuid>'

pg-agmemory scope-access get --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID"

pg-agmemory scope-access set --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID" \
  --expected-access-epoch '<access_epoch-observed-in-first-get>' \
  --permissions read --expires-at '<approved-future-ISO-timestamp-with-offset>'

pg-agmemory scope-access get --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID"

pg-agmemory scope-access revoke --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID" \
  --expected-access-epoch '<access_epoch-observed-in-second-get>'

pg-agmemory scope-access get --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID"
```

Success is one unwrapped JSON line:
`operation`, `tenant_id`, `scope_id`, `principal_id`, `access_epoch`, `changed`,
`membership_exists`, `permissions`, `expires_at`, `effective_permissions`, `evaluated_at`.
Absent membership is false/empty/null; a legacy empty-permission row still exists.
Configured flags use read/write/delete/admin order. Effective flags are empty if
expired at DB `evaluated_at`, all four for effective `admin`, otherwise configured
flags—not a guarantee of Native action authorization or future validity.
Natural expiry neither advances the epoch nor removes payload/audit or drains
in-flight HTTP; use explicit revoke/barrier for a strong drain.

### Drain, audit, and recovery

The dedicated synchronous autocommit connection uses **5 s connect/statement/lock
timeouts**, validates role/schema/extension, and acquires the API/worker's **same
tenant session advisory lock**, including for `get` and no-ops.
It holds that lock through transaction commit **and JSON stdout flush**;
connection close releases it on success/failure. Do not pool it or replace it
with an xact-only lock. A slow earlier response can delay the command; timeout
while waiting means no change. Cooperating same-version API clients can stay online.

Actual changes atomically update `memory.scope_member`, increment the tenant
epoch, and append `memory_ops.scope_access_event`. No-op/get/conflict produces
neither epoch advance nor event; mid-change failure rolls all three back.
The audit stores target opaque IDs, epoch, `set`/`revoke`, before/after flags/
expiry, `current_user` as `database_role`, and DB-clock `recorded_at`—not content,
subjects, or DSNs. It is forced-RLS, privileged-only, with no runtime policies/grants.
`evaluated_at` is response-only, not an audit field.
The recorded database role is the executing `current_user`, not an end-user actor.
It is not tamper-proof against privileged DB administrators or a standalone
revocation-recovery ledger.

Syntax/model/config errors produce static sanitized stderr, exit **2**, no JSON.
Invalid CLI syntax reports `invalid_scope_access_arguments`; missing
`PGAG_ADMIN_DATABASE_URL` also exits 2 with static stderr and no stdout.
DB/domain errors produce stdout `{"error":{"code":"...","outcome_unknown":false}}`,
exit **1**, never raw DB errors/credentials. See [the complete error catalog](../STATUS.md#scope-access-administration).
Commit transport failures can set `outcome_unknown: true`; pre-commit-attempt
failure is false. Treat cancellation, process kill, or lost stdout as an unknown
mutation outcome too. Inspect fresh `get` plus privileged audit before explicitly
authorizing a new CAS operation. **No automatic retry, Idempotency-Key, or
mutation receipt exists**; stale epochs must not be blindly replayed.
The stdout barrier cannot retract already-delivered context. Restoring latest
ACL/deletion records is still manual; grants never resurrect purged data.

## Schema 9 scope-access upgrade

**Retained schema-9 migration step; not a complete v0.0.24 upgrade.**
Current tooling must continue through the [schema-10 upgrade](#schema-10-job-cancellation-upgrade),
including for an existing schema-9 database. v0.0.24 qualified.
Retain the pinned PostgreSQL **18.6** / `vector` **0.8.6 in `public`** image below.
Migration 009 introduced durable privileged audit in v0.0.13; it is not new in v0.0.24.

1. Stop/drain all old/new APIs, workers, adapters, hook launches, SDK callers,
   and administrative commands, including replicas/restarts. Preserve backups
   and current ACL/deletion records; rehearse only on disposable databases.
2. Run `pg-agmemory migrate` from matching v0.0.24 tooling with the migration
   administrator. Apply `009_scope_access.sql` after 001–008 in the recorded
   migration sequence, then apply 010. Historical schema-8→9 rollback/retry after
   a ledger-write failure passed in v0.0.14; the v0.0.15 full local/native suites also passed.
   Existing ACL rows are preserved, with no audit backfill for prior manual changes.
   No embedding backfill or implicit ownership/purge change is added.
3. Verify exact history `[1,2,3,4,5,6,7,8,9,10]`, the extension version/schema,
   and privileged-only audit table. Start only matching v0.0.24 API/workers;
   SDK/MCP/hook require service **0.0.24**, API **v1**, schema **10**.
4. Check authenticated capabilities stage `m2-selectable-inference` and
   `scope_access_administration` metadata (`transport: "admin-cli"`,
   `command: "scope-access"`, `compare_and_swap: "tenant_access_epoch"`,
   `audit: "database_role"`), then exercise approved disposable CLI/ACL/drain
   and retained-resource checks before reopening traffic. No mixed-version or
   downgrade compatibility is promised. On failure keep old processes stopped.

**Historical v0.0.13 evidence:** Apple Container and native Docker amd64/arm64
each passed **464 tests, 1 existing warning**, Ruff, strict mypy
(**19 source files + 1 SDK consumer**), genuine
core/hook/sdk-only installs, and all non-root production smokes, including the
real scope-access CLI/SDK lifecycle. The total is **426 retained + 22 scope-admin
unit + 16 integration tests (38 new)**. Implementation
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413)
passed [CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448);
actual native logs verified the checks/smokes.
Test elapsed: **297.52 s local / 539.86 s amd64 / 460.73 s arm64**, not a
performance benchmark. The separate final v0.0.13 docs
[185f433](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)
passed [CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967):
464 tests/1 warning and all checks/smokes per native architecture,
**499.25 s amd64 / 454.37 s arm64**. Neither run validates v0.0.14.
See [validation evidence](../STATUS.md#v0013--schema-9) and
[ADR 0013](../adr/0013-scope-access.md). This is not production/DR qualification.

## Python SDK operations

**SDK retains 31 memory methods; providers use a separate library/CLI; v0.0.24 qualified.**
Install from the matching checkout with `python -m pip install '.[sdk]'`.
The `pg-agmemory[sdk]` extra pins only `httpx==0.28.1`, not the MCP SDK;
the same core package still includes FastAPI, psycopg, and Janome.
This is neither a standalone lightweight distribution nor a PyPI publication claim.
Missing HTTPX raises a static SDK import `ImportError`; HTTPX installed through
`hook`/`mcp` also satisfies the dependency. The package supplies `py.typed`.

1. Provision the scope and token using trusted administration; **do not pass DB
   credentials or signing keys to the SDK**. Use explicit constructor arguments
   from trusted configuration, never memory content, tool arguments, or redirects.
   `PGAG_SDK_API_URL`, `PGAG_SDK_API_TOKEN`, and `PGAG_SDK_SCOPE_ID` in the
   [async example](../../README.md#python-sdk) are example environment names,
   not SDK environment readers.
2. Choose a fixed HTTPS origin or loopback HTTP origin without application path,
   userinfo, query, or fragment. Constructor validation covers URL/token shape
   and raises sanitized `ValueError`, not `MemoryClientError`;
   the server authenticates the bearer token and enforces current ACLs.
   Request scopes only narrow that authority.
3. Enter `async with AsyncMemoryClient(api_url, api_token) as memory:`.
   Entry creates its HTTP client and requires authenticated capabilities
   **service 0.0.24 / API v1 / schema 10**. Never use before/after the context or
   re-enter the same instance (`client_not_open` / `client_already_used`).
   Exit closes connections, **not stored memory**.
   Await outstanding tasks, or cancel and await them, before exiting.
   Client close does not schedule/cancel requests or roll back the database.
4. Pass Native request models from `pg_agmemory.models`; mutable instances are
   revalidated at call time. Pydantic model construction can separately raise
   `ValidationError` outside the SDK call; do not log its private input details.
   The validated request is snapshotted before the first outbound network await.
   Use UUID objects for resource IDs.
   Each mutation needs a keyword-only `idempotency_key` of **1–256 visible ASCII
   characters, without trimming**. This includes `forget` preview and purge,
   both returning Native HTTP 202 with the mode-specific typed response.
5. Retain each exact key/body securely before dispatch. `MemoryClientError`
   aliases `AdapterFailure`; inspect `.error.code`, `.retryable`,
   `.outcome_unknown`, `.native_status`, and `.request_id`, never log raw
   memory/input/token/response content. SDK domain codes are catalogued;
   unknown server codes become `native_api_error`. MCP/hook safe codes are unchanged.
6. If a mutation outcome is unknown after network/5xx/malformed/wrong-success
   responses, reconcile with the **same key/body**. There is no automatic retry,
   key replacement, or assumption of no commit. Cancellation propagates:
   in-flight mutation cancellation also requires reconciliation, not an assumed
   rollback. Local invalid request/key/UUID errors occur before dispatch with
   sanitized `invalid_request` and `outcome_unknown: false`.

Transport bounds remain **20 s per exchange; 10 s I/O / 5 s connect;
4 connections; verified TLS; no proxy environment or redirects**.
Responses are capped at **2 MiB**, requests at **256 KiB**, except
`create_checkpoint` at **1 MiB**. MCP/hook bounds are not increased.
The client has no cache, provider calls, host registration/delegation, automatic
jobs, token refresh, sync/TypeScript client, or arbitrary HTTP method.
It covers memory resources, not admin/worker CLI execution; capabilities are an
internal probe, not a public SDK health/OpenAPI download method.
Returned memory is evidence, not trusted instructions/current facts.
Preserve Native byte budgets, coverage flags, current ACLs, purge, and
host/backup/WAL limits; context exit cannot remove copies already returned.
See [all 31 typed methods](../STATUS.md#python-sdk) and
[ADR 0012](../adr/0012-python-sdk.md).

<a id="v0012-application-update"></a>

## v0.0.12 application update (schema unchanged)

**Historical schema-8-only procedure, not the v0.0.24 upgrade.**
Use the [current schema-10 update](#schema-10-application-only-upgrade),
which includes the retained earlier migrations.
**Maintenance procedure, not production-upgrade or disaster-recovery qualification.**
For an existing schema-8 database, retain the pinned PostgreSQL 18.6 /
`vector` 0.8.6-in-`public` DB image below. No schema 9 migration, embedding
backfill, or provider call is added.

1. Stop/drain old APIs, workers, adapters, hook launches, and SDK callers,
   including replicas and automatic restarts. Preserve current deletion/ACL
   records and backups; rehearse only on disposable databases.
2. Build/install matching v0.0.12 API/worker/adapters/hook/SDK components from
   the locked source. Existing schema 8 retains exact history `[1,2,3,4,5,6,7,8]`;
   `migrate`, API, and worker still validate the extension version/schema.
   Older schemas first need the retained migrations described below.
3. Start only matching v0.0.12 components; confirm authenticated capabilities,
   stage `m2-python-sdk`, and `python_sdk` metadata (`installation: "sdk-extra"`,
   `async: true`, `automatic_retry: false`). Metadata does not add an endpoint.
4. Verify retained adapters plus SDK lifecycle/recovery/purge using synthetic
   data before resuming traffic. No mixed-version/rolling compatibility or
   downgrade is promised. On failure keep processes stopped and investigate.

Final local Apple Container and native Docker amd64/arm64 tests,
strict source/typed-consumer checks, all five
real-HTTP SDK integration tests, genuine core/hook/sdk wheel-install checks,
and all non-root production smokes passed. Exact counts, timings, and scope are
in [verified evidence](../STATUS.md#v0012--schema-8):
**426 tests, 1 warning per environment**, **292.76 s local / 484.79 s amd64 /
472.49 s arm64**. Implementation
[88e1206](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)
passed [CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945);
actual logs verified the exact SHA and checks. Timings are not performance benchmarks.
The SDK smoke observes a pending job without invoking a worker; the retained
actual capture-worker smoke remains separate.
The distinct historical v0.0.11 implementation and final-docs runs, including
docs CI 35190495385, remain [historical evidence](../STATUS.md#v0011--schema-8).

## Schema 8 pgvector upgrade

**v0.0.11 application/migration checks passed; production qualification remains incomplete.**
Migration 008 was introduced and verified in v0.0.11. Current v0.0.24 tooling
also applies retained migrations 009 and 010 to older schemas; v0.0.24 qualified.
Follow the [schema-10 boundary](#schema-10-job-cancellation-upgrade), not the
historical v0.0.12 application-only procedure.

Adopt the prebuilt image:

```text
docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a
```

[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6) is the verified
**2026-07-29** stable release, official tag commit
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`
([pinned changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)).
It uses the **PostgreSQL License**; preserve upstream license files on redistribution.
Both final native images retain `/usr/share/doc/pgvector/LICENSE`, verified
byte-identical to the pinned upstream license, SHA-256
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`.
Inspection of both final amd64/arm64 images verified PostgreSQL
**18.6-1.pgdg12+2**, native ELF, and `vector.control` **0.8.6**.
**The PostgreSQL version is unchanged, but the DB image/base digest is different**
from the old library PostgreSQL profile. This is a new pinned upstream vector DB
profile, not a source build on an unchanged base. No new DB Dockerfile, mutable
host-APT installation, or source-build workflow is part of the implemented profile.

1. Use the full image tag **and digest** above, not an arbitrary latest image.
   For an operator-managed PostgreSQL alternative, the matching extension must
   be available before migration; this document supplies no host-install workflow.
2. Stop/drain **all old/new APIs, workers, adapters, and hook launches**, including
   replicas and automatic restarts. The migration lock does not protect against
   an old schema-7 process continuing to serve. No rolling coexistence is supported.
3. Preserve a backup, application/schema/extension versions, and current deletion
   and ACL records. Rehearse only on disposable databases; restore quarantine and
   DR/full-erasure gaps remain.
4. With the migration administrator and matching v0.0.24 application, run
   `pg-agmemory migrate`. Apply `008_pgvector.sql` after unchanged 001–007,
   followed by `009_scope_access.sql` and `010_job_cancellation.sql`;
   older databases still need migration 007's lexical backfill.
   Migration requires `vector` **0.8.6 in `public`** and refuses an existing
   extension with the wrong version/schema.
   `migrate` also validates the extension when schema 8 is already recorded;
   an already-applied migration does not bypass the check.
   New episode/assertion-revision vector projections have forced RLS,
   canonical `ON DELETE CASCADE`, and runtime **SELECT/INSERT only**.
   **No existing data receives embedding backfill**.
5. Confirm exact history `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` and extension `vector` 0.8.6 in `public`
   before starting only matching v0.0.24 APIs/workers. Check authenticated
   capabilities, lexical compatibility, synthetic vector/hybrid ranking,
   coverage, RLS/time filters, replay/purge, and retained adapters before traffic.
   API/worker startup rejects schema/extension mismatches.
6. On failure, leave processes stopped. Do not start old images against changed
   schema or assume downgrade support. No automatic embedding rebuild/provider
   exists; lexical reindex does not populate vectors.

Migration 008 is the **historical schema-8 migration**; current tooling also needs
the schema-10 upgrade above, including 009. This differs from the historical v0.0.10 application-only
update below. Artifact/version/license inspection and v0.0.11 application checks are verified
separately. See [the current contract](../STATUS.md#pgvector-exact-and-hybrid-retrieval)
and [ADR 0011](../adr/0011-pgvector-retrieval.md).

## Explicit vector operations

Use currently authorized episode/assertion revisions only. Read-only
`POST /v1/embedding-inputs` takes Explain `{memory_id, revision}` (default **1,
not latest**) without an idempotency key. It returns private canonical text and
its SHA-256 UTF-8 digest under `memory-content-v1`. Do not log it or send it to
third parties without explicit approval. The digest does not attest model
provenance, semantic support, or quality.

Upload via Native `POST /v1/embeddings` with a retained caller-owned
`Idempotency-Key`, exact digest, caller-declared model name/revision (1–256
characters each), fixed 768/cosine/`l2-f32-v1` metadata, and 768 finite JSON numbers.
Server normalization computes float64 then stores pgvector float32.
No zero/non-finite vector, boolean, numeric string, truncation, or dimension
coercion is accepted. Scope/identity come from the canonical parent and require
read/write access. Preserve the exact key/request before dispatch; do not log them.

Each parent revision/model namespace is immutable. Identical normalized
float32 vector/digest deduplicates across keys; changes conflict
(`409 embedding_conflict`), and digest mismatch is `409 embedding_input_mismatch`.
Use a new model revision for replacement. The **8-model-versions-per-canonical-
revision** cap rejects a ninth with `422 embedding_limit_exceeded`, but permits
existing duplicates. The upload response has `{memory_id, revision, model, input_digest}`,
not an independent embedding ID. **The stored idempotency result is only
`{memory_id, revision}`**, with no plaintext digest/model names/vectors in the
receipt. The full response model/digest is rebuilt from currently readable
canonical input and its matching projection; request HMACs/opaque anchors persist.
Replay rechecks live parent and projection. A live parent with an
administrator-removed projection yields **409 `embedding_unavailable`**, not
projection recreation. Projection/idempotency/audit writes are atomic.

Lexical remains default and rejects a vector query; vector-only requires empty
text plus a vector; hybrid requires nonempty text plus a vector. Exact cosine is
computed only after materializing current ACL/time-eligible candidates; hybrid
uses deterministic **RRF k=60**, not an approximate neighbor index.
Omitted `as_of`/`known_at` are frozen once before selection and coverage, so both
paths use the same resolved times across future boundaries; explicit times are
unchanged. UUID breaks ties in actual distance/score, without guaranteeing
bitwise-identical arbitrary floating-point results/rankings across all CPUs.
Do not mix model name/revision spaces. Surface `vector_incomplete`,
`lexical_incomplete`, `retrieval_complete`, and Native empty/budget outcomes:
missing visible vectors are not a silently successful complete index.
Ranking metadata is not confidence. The whole-JSON byte budget and 5 s DB
statement timeout remain; neither measures semantic quality or a latency SLO.
Response defaults add `MemoryItem.retrieval: null`, `retrieval_mode: "lexical"`,
`embedding_model: null`, and `coverage.vector_incomplete: false`.
Non-null `MemoryItem.retrieval` contains `method` (`exact_cosine`/`rrf-60`),
`lexical_rank`, `vector_rank`, `vector_distance`, and `fusion_score`, with nullable
values where appropriate. **Lexical behavior is preserved, not byte-for-byte
HTTP response/schema shape**. Adapt strict consumers to these additive fields.
The v0.0.11 vector feature added no Python dependency; the service uses raw parameter-bound vector casts.
Capabilities add `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"`.

Canonical purge cascades vectors/digests/declared model names with lexical data.
There is no standalone model registry retaining this metadata.
Vectors have no independent provenance vertex or deletion count. There is no
projection-only delete endpoint or automatic generation/rebuild. Retained anchors
and current ACL/deletion checks still prevent resurrection; host/backup/WAL erasure
is not certified. MCP retains four tools and only gains Recall arguments;
embedding input/upload are Native routes also covered by the SDK, not MCP tools. Hook, Observe, capture, jobs, and workers
never generate embeddings; the hook remains lexical-only.
It rejects Native responses with non-lexical `retrieval_mode`, non-null
`embedding_model`/item `retrieval`, or true `coverage.vector_incomplete`;
unexpected vector output is an error, not a silent downgrade.

### Synthetic vector example

**Draft request example, not a production model or retrieval-quality benchmark.**
In a dedicated disposable scope containing only synthetic data, use unchanged
`POST /v1/observe` to create an episode with content exactly
`synthetic vector fixture`. Supply trusted operator environment values:
`PGAG_DEMO_API_URL` (Native origin), `PGAG_DEMO_API_TOKEN` (Native-audience token),
`PGAG_DEMO_SCOPE_ID`, `PGAG_DEMO_MEMORY_ID` (that episode UUID), and
`PGAG_DEMO_EMBEDDING_KEY` (retained caller-owned key).
These environment-variable names belong only to this example.
Do not derive startup settings from prompts, enable shell tracing, or log content/tokens.
Reusing the example after uncertainty must keep its key/body.

```bash
python - <<'PY'
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("redirect disabled")

try:
    base = os.environ["PGAG_DEMO_API_URL"].rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
    ):
        raise ValueError("invalid origin")
    token = os.environ["PGAG_DEMO_API_TOKEN"]
    memory_id = str(uuid.UUID(os.environ["PGAG_DEMO_MEMORY_ID"]))
    scope_id = str(uuid.UUID(os.environ["PGAG_DEMO_SCOPE_ID"]))
    key = os.environ["PGAG_DEMO_EMBEDDING_KEY"]
    if not token or not 1 <= len(key) <= 256 or not all(33 <= ord(c) <= 126 for c in key):
        raise ValueError("invalid operator configuration")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def post(path, body, idempotency_key=None):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with opener.open(request, timeout=10) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("response too large")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid response")
        return result

    source = post("/v1/embedding-inputs", {"memory_id": memory_id, "revision": 1})
    expected = {
        "memory_id": memory_id, "revision": 1, "type": "episode",
        "text": "synthetic vector fixture", "input_format": "memory-content-v1",
    }
    if any(source.get(name) != value for name, value in expected.items()):
        raise ValueError("not the synthetic fixture")
    digest = hashlib.sha256(expected["text"].encode("utf-8")).hexdigest()
    if source.get("input_digest") != digest:
        raise ValueError("digest mismatch")
    model = {
        "name": "synthetic-basis-demo", "revision": "basis-v1", "dimensions": 768,
        "distance_metric": "cosine", "normalization": "l2-f32-v1",
    }
    values = [1.0] + [0.0] * 767
    receipt = post("/v1/embeddings", {
        "memory_id": memory_id, "revision": 1, "input_digest": digest,
        "model": model, "values": values,
    }, key)
    if any(receipt.get(name) != value for name, value in {
        "memory_id": memory_id, "revision": 1, "model": model, "input_digest": digest,
    }.items()):
        raise ValueError("invalid receipt")
    for mode, query in (("vector", ""), ("hybrid", "synthetic")):
        result = post("/v1/recall", {
            "scope_ids": [scope_id], "query": query, "mode": "explicit", "purpose": "synthetic_fixture",
            "token_budget": 2000, "max_items": 20, "search_profile": "simple-v1",
            "retrieval_mode": mode, "vector_query": {"model": model, "values": values},
        })
        coverage = result.get("coverage")
        names = ("retrieval_complete", "vector_incomplete", "lexical_incomplete")
        if result.get("retrieval_mode") != mode or not isinstance(coverage, dict):
            raise ValueError("invalid recall response")
        if any(type(coverage.get(name)) is not bool for name in names):
            raise ValueError("invalid coverage")
        print(json.dumps({"retrieval_mode": mode, "synthetic_only": True,
                          "coverage": {name: coverage[name] for name in names}}))
except (KeyError, ValueError, TypeError, OSError, urllib.error.URLError):
    print("Synthetic vector example failed; inspect sanitized Native diagnostics.", file=sys.stderr)
    raise SystemExit(1) from None
PY
```

This uses only Python's standard library and the trusted Native API; **no external
model, registry, provider, or pgvector Python package** is invoked. It deliberately
uses a mathematical basis vector, checks synthetic canonical input/digest, and
prints only fixed coverage fields—not private text, vectors, receipts, or errors.
Proxy environment and redirects are disabled; HTTPS uses default TLS verification.
The illustrative 10 s I/O timeout is not a total deadline or production SLO.
Inspect incomplete coverage explicitly; adding only one projection never proves
a larger corpus is complete. Do not label this a semantic embedding model.

## Atomic structured capture operations

For 1–16 proposals, use the separate [batch route](#explicit-batch-capture);
the single-job contract below remains unchanged.

**Retained capture contract, verified in v0.0.11.** Bootstrap Native roles
as above and use matching service `0.0.24`, API `v1`, schema `10`.
The historical v0.0.10 stage `m2-atomic-capture` did not complete M2.
Capture is a Native route, **not an MCP tool or automatic recall-hook action**.
The retained schema-8/9 migrations are separate from capture semantics.
Schema 10 adds terminal cancellation; same-intent capture still dedups without
reviving the job. Capture never generates embeddings.

### Explicit request example

Use approved, sanitized, disposable data. The operator supplies `MEMORY_URL`
(trusted API origin), `TOKEN` (Native-audience token), `SCOPE_ID` (authorized
UUID), `SOURCE_EVENT_ID` (stable source identity), and `CAPTURE_KEY` (caller-owned
idempotency key). These are placeholders, not embedded credentials.
Retain the exact request/key securely before sending; do not enable shell tracing
or copy tokens/content into logs or issue reports.

```bash
curl --fail-with-body "${MEMORY_URL%/}/v1/captures" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Idempotency-Key: ${CAPTURE_KEY}" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<JSON
{
  "episode": {
    "scope_id": "${SCOPE_ID}",
    "source_namespace": "atomic-capture-demo",
    "source_event_id": "${SOURCE_EVENT_ID}",
    "occurred_at": "2026-09-17T00:00:00Z",
    "content": "ACME contract is Gold",
    "consent_reference": "operator-approved-demo-consent"
  },
  "memory": {
    "subject": "ACME",
    "predicate": "contract_tier",
    "value": "Gold",
    "evidence_quote": "ACME contract is Gold",
    "explicit_intent": true,
    "valid_from": null,
    "valid_to": null
  }
}
JSON
```

`episode` is unchanged Observe. `memory` is exactly one structured intent with
no scope, evidence IDs, or identity overrides; the server derives its scope and
single episode evidence ID inside the transaction. Its one quote must be a
literal 1–4,096-character substring of the normalized episode.
Remember bounds remain: subject 1–256, predicate `^[a-z][a-z0-9_]{0,63}$`,
value 1–65,536, explicit intent true, and aware/null valid bounds with start
before end when both are present. The literal quote is not proof of semantic truth;
published assertions remain reported and uncalibrated.

### Follow the job, not a presumed assertion

HTTP **201** gives `{memory_id: <episode UUID>, revision: 1,
synthesis_job_id: <job UUID>}`. This acknowledges the atomic **episode plus
structured_remember / structured-remember-v1 job commit**, not assertion
publication. Existing jobs, including terminal ones, may be reused:
**201 does not mean fresh or pending**. Save both historical IDs and use GET as
the authority for current job status. With `CAPTURE_JOB_ID` set to the returned job UUID:

```bash
curl --fail-with-body "${MEMORY_URL%/}/v1/jobs/${CAPTURE_JOB_ID}" \
  -H "Authorization: Bearer ${TOKEN}"
```

Run the existing worker under the **same owning principal's trusted fixed
subject**, with restricted runtime DB credentials, then GET again for fresh state.
`--once` processes at most one due owned job; it need not be this job and is not
a queue drain. Only a successful job result supplies the published assertion ID.
Existing quota (100 pending/running jobs per scope), five attempts, leases,
epochs, and publication fencing apply unchanged. See
[worker operations](#durable-job-and-worker-operations).

Pure `POST /v1/observe` still returns `synthesis_job_id: null` and never queues
automatically. Direct `/v1/jobs` and synchronous `/v1/remember` are unchanged,
including Observe/Remember serialization/HMAC compatibility.
Capture adds no LLM/provider, extraction, natural-language synthesis, automatic embeddings,
or new semantic-quality qualification.

### Replay, failures, and deletion

- Reuse the **same capture key and normalized body** after uncertainty, including
  API restart; HTTP response loss is not proof of rollback. Changed body with
  the same key is `409`. IDs are historical: GET supplies fresh job state.
- New keys with the same episode/intent/principal deduplicate **both IDs**.
  A previously observed identical event is reusable. Changed episode body for
  the same source identity conflicts with `409`, without new partial writes.
- A new, different explicit intent may create another job using the retained
  episode. Another authorized principal has independent job identity/worker
  ownership; source deduplication does not grant permissions.
- Transaction failure after episode/projection, job data/identity, outer
  idempotency receipt, or audit rolls back new changes together. An independently
  pre-existing episode remains; no partial new job survives. At most one job
  belongs to a capture operation.
- Current ACLs/deletion win for **both returned IDs**. Episode purge closes
  job/assertion descendants. Job-only purge leaves the episode and independently
  stored published output, but old-pair replay or a new key for the same intent
  returns `404`; the purged job identity is not recreated. Result-assertion purge
  removes its dependent job but keeps source, invalidating the pair.
- This is not a permanent whole-source seal: new explicit **different** intent
  on retained source follows existing job semantics.
- To retry a failed job, use existing `POST /v1/jobs/{job_id}/retry` with the full
  original `EnqueueJob` intent and caller-owned key. Reconstruct its existing
  Remember evidence from the returned episode ID and retained quote/intent.
  Capture replay returns the original failed job reference, **not a retry child**,
  even after that child is created.

Retained opaque source/job/idempotency anchors include internal composition keys
derived by server HMAC from the caller key. Do not supply or synthesize these
internal keys; they are not MCP autogenerated caller keys or a new API input.
The Native tenant HTTP response-drain boundary is unchanged. Retained anchors,
host context, backups, WAL, and delivered data have no new full-erasure guarantee.
Capability feature `atomic_structured_capture` has `atomic_capture` metadata:
`endpoint: "/v1/captures"`, `max_jobs: 1`,
`recipe_version: "structured-remember-v1"`, `automatic_capture: false`.
See [the delta contract](../STATUS.md#atomic-structured-capture)
and [ADR 0010](../adr/0010-atomic-capture.md).

## Local stdio MCP operations

Owned-job query is Native/SDK-only; no job tool joins the four MCP bindings.

Checkpoint-head lookup is Native/SDK-only; no checkpoint tool joins the four MCP bindings.

`memory_recall` accepts the same typed `filters` in its Native request wrapper;
see [exact structured selection](#exact-structured-recall-filters). No extra tool or safe error code.

### Install and start with one trusted identity

1. Bootstrap the Native API and provision the intended subject/scope using the
   role separation above. The adapter needs **no database URL, admin credentials,
   signing key, or worker `--subject`**. Native API authentication and current
   ACL/deletion checks remain authoritative on every call.
2. Install `pg-agmemory[mcp]`, or use the repository image with the extra already
   present. For the source checkout, prepare the lock with
   `uv sync --frozen --extra mcp`. The pins are official `mcp==2.2.0` and
   `httpx==0.28.1`, not a similarly named third-party MCP package.
3. In the trusted local host's process environment, securely supply
   `PGAG_MCP_API_URL` and `PGAG_MCP_API_TOKEN`. Do not commit tokens into a host
   configuration or put them in command-line arguments, examples, logs, or
   issue reports. The token targets the **Native API audience**, not the MCP
   host; the Native API validates it. This is a fixed trusted Native client,
   not forwarding of an MCP caller's identity.
4. Use an HTTPS origin such as `https://memory.example.com`, or a loopback
   HTTP origin such as `http://127.0.0.1:8000`. Do not include credentials,
   `/v1` or another application path, query, or fragment; a root `/` is accepted.
   Non-loopback plain HTTP is rejected. Loopback is relative to the adapter's
   process/container, not automatically the Mac host or a sibling container.
   Container-hosted adapters therefore need a reachable trusted HTTPS origin
   unless the API shares their loopback boundary. TLS verification stays on;
   redirects and proxy environment settings are not used.
5. Configure the host to launch the installed executable with argument `mcp`:

   ```bash
   pg-agmemory mcp
   ```

   Use `uv run --frozen --extra mcp pg-agmemory mcp` in a checkout environment
   if the virtualenv executable is not on PATH. Do not add `--subject` or
   `--once`: both are rejected. Keep stdin/stdout attached for MCP messages,
   not human prompts or ordinary log output. Diagnostics use sanitized stderr.
6. Startup must authenticate `GET /v1/capabilities` and match API `v1`, service
   `0.0.24`, schema `10` before serving tools. A bad setting/token, unreachable API,
   or version mismatch exits nonzero without logging secrets. A passing
   `/healthz` alone is insufficient. Fix trusted configuration and restart;
   do not bypass the check or change tool arguments to override identity/URL.

There is no remote MCP HTTP/SSE transport, OAuth, delegated identity, or
per-call header/URL/token override. Run **one adapter per trust identity**;
do not share the connection across trust domains or expose it through a network
wrapper. Restart with a securely supplied replacement token to refresh it;
no automatic token refresh is provided. Preserve the same authorized subject
when recovering a previous operation.

### Invoke and recover without accidental duplicate writes

Only `memory_recall`, `memory_remember`, `memory_explain`, and `memory_forget`
are tools. Each wraps the **Native Pydantic request body** as `{request: ...}`.
Remember and forget additionally require `idempotency_key`, **1–256 visible
ASCII characters (`0x21`–`0x7e`, no whitespace)**, even for forget preview.
Keys are not trimmed or rewritten: 256 characters is allowed and 257 is rejected.
Native forget preview and purge both return **HTTP 202**, unchanged; inspect the
Native result variant rather than treating the status as proof of purge.
Have the host/caller retain the key and exact body securely **before dispatch**,
so it can reuse them after an interrupted response or stdio restart.
MCP session/request IDs are not memory run IDs or Native HTTP idempotency keys.
Do not use a new key just because the host reconnects.

Success has `structuredContent: {result: <Native result>, error: null}`.
Failure has `isError: true` and `{result: null, error: {code, retryable,
outcome_unknown, native_status, request_id}}` in `structuredContent`;
the Native status/request UUID can be null. Short text does not duplicate
the evidence payload. Inspect structured output, not text alone.
Transport errors, timeouts, 5xx, and invalid mutation responses mean the outcome
may be unknown, **not that the write rolled back**. After uncertainty, retry
only with the same key/body and intended identity, including after token
replacement. Neither retries nor keys are generated automatically.
`retryable` does not authorize changing the body or prove non-commit.
Current authorization/deletion may deny a replay; historical references are
not current evidence or permission to restore deleted content.

The HTTP client has a **20 s total / 10 s I/O / 5 s connect** bound,
**4 connections**, **256 KiB serialized request**, and **2 MiB response** limits.
No response/semantic cache is maintained. These limits do not qualify throughput
or all host buffer sizes. Recall budgets remain **UTF-8 bytes, not model tokens**;
Japanese recall still needs explicit `ja-janome-0.5.0-v1` selection.
Remember only publishes explicitly requested structured assertions with Native
episode evidence; capture episodes with Native `observe`, not MCP. Explain
without a revision still requests revision 1, not latest.

### Deletion and host-context handling

Treat retrieved text as untrusted evidence, not instructions. The adapter is
the trusted Native HTTP recipient: the API's response-drain barrier ends at
HTTP delivery to it, **not atomically at stdio delivery, the host UI, or LLM
context consumption**. A host may still hold a response that predates purge or
ACL revocation. In-flight buffers/already-delivered context cannot be retracted.
After forget or permission changes, the host must discard cached context and
obtain fresh authorized data rather than reuse old output. There is **no MCP
deletion notification** that does this automatically. Purge does not certify
host-context, backup, WAL, replica, or physical-media erasure.

Historical v0.0.8 actual stdio SDK `Client` connections and raw JSON fixtures exercised:

- Modern `2026-07-28`: `Client(mode="auto")` and `server/discover`. Raw requests
  carry `params._meta` keys `io.modelcontextprotocol/protocolVersion`,
  `io.modelcontextprotocol/clientInfo`, and `io.modelcontextprotocol/clientCapabilities`.
- Legacy `2025-11-25`: `Client(mode="legacy")`, `initialize`, then
  `notifications/initialized`, before tool calls.

The response-loss regression drops an actual HTTP response **after remember
commits**, then retries the same key/body and checks that only one assertion
persists. This tests caller-driven recovery, not an automatic retry.
Historical local/native CI evidence is recorded in STATUS. v0.0.11 retains both
protocol eras, semantics, MCP bounds, and the shared Native HTTP client.
v0.0.9 checks passed locally and on both native Docker architectures;
Historical v0.0.10/v0.0.11 and final local/native v0.0.12 checks passed.
These specific checks do not prove compatibility with untested
older clients or a named host.
See [the full contract](../STATUS.md#local-stdio-mcp) and
[ADR 0008](../adr/0008-local-mcp.md).

## Implicit recall hook operations

Owned-job query adds no hook field or job operation.

Checkpoint-head lookup adds no hook field or checkpoint operation.

Do not add `filters` to hook events: unknown fields are rejected, while the
internally created `Recall.filters` remains `None`. Trusted startup boundaries stay unchanged.

Hook input rejects `required_memory_refs`; its internal `Recall` keeps `[]`.
Successful-empty `empty_reason: "budget_exhausted"` remains `200` / hook exit 0,
not the required-context Native/SDK/MCP `422 budget_exhausted` error.
Do not add a host pinning field to the hook packet.

**Retained read-only, lexical-only hook, verified in v0.0.11.** This is a vendor-neutral,
one-shot harness-side command, not MCP, a model caller, or an automatically
registered host plugin. No Copilot/Claude/Codex integration is claimed.

### Install and configure from a trusted operator environment

1. Provision the Native API subject/scopes using the role separation above.
   The hook requires **no DB credentials**, admin URL, JWT signing key, or
   external model key. It only uses the configured Native audience token.
2. Install `pg-agmemory[hook]` or use the v0.0.24 repository image with
   `mcp`, `hook`, and `sdk` extras. For the checkout use `uv sync --frozen --extra hook`.
   Hook-only installation pins `httpx==0.28.1`, **not the MCP SDK**.
3. Have the operator securely supply the following environment before starting
   the trusted harness. Do not derive it from prompts, queries, event fields,
   tools, or retrieved text. Do not place tokens in command-line arguments,
   checked-in examples, logs, or issue reports.

   | Variable | Operator setting / default |
   |---|---|
   | `PGAG_HOOK_API_URL` | Required, no default. Trusted HTTPS origin or loopback HTTP origin; no userinfo, application path, query, or fragment. Root `/` is accepted; absent URL gives `invalid_hook_configuration` |
   | `PGAG_HOOK_API_TOKEN` | Required fixed Native API audience bearer token |
   | `PGAG_HOOK_SCOPE_IDS` | Required JSON array of 1–32 unique provisioned scope UUIDs |
   | `PGAG_HOOK_PURPOSE` | Default `implicit_context`; 1–256 characters |
   | `PGAG_HOOK_TOKEN_BUDGET` | Default `2000`; integer 64–2,000 **UTF-8 bytes, not model tokens** |
   | `PGAG_HOOK_MAX_ITEMS` | Default `20`; integer 1–20 |
   | `PGAG_HOOK_SEARCH_PROFILE` | Default `simple-v1`; opt in explicitly to `ja-janome-0.5.0-v1` |
   | `PGAG_HOOK_TIMEOUT_SECONDS` | Default `2.0`; finite 0.1–20 seconds |

   URL, token, and scope IDs are **all required**. Shared `NativeSettings` also
   parses origins with `httpx.URL`, rejecting control characters and invalid
   IDNA before transport. These checks passed in all three historical v0.0.9 environments.

   Loopback is relative to the hook process/container; do not assume it reaches
   a sibling container or the Mac host. Non-loopback HTTP is rejected.
   Redirects and proxy environment settings are disabled; TLS verification is on.
4. Launch `pg-agmemory recall-hook` with exactly one JSON document on stdin, then
   EOF. `--subject`/`--once` are rejected. Only `event` and `query` are allowed:
   `event` is `session_start`, `task_switch`, or `after_compaction`; `query` is a
   required string of at most 4,096 Unicode characters. Empty query is canonical
   browsing in the configured scopes, not a missing field. Retrieval intent is
   supplied only by JSON `query`; `event` is a lifecycle label. No identity,
   `scope_ids`, purpose, mode, budget, URL, header, tool, time, or other field
   is accepted. Event text never authorizes access.
5. Each invocation freshly checks authenticated `GET /v1/capabilities` for exact
   service `0.0.24`, API `v1`, schema `10`, then sends `POST /v1/recall` with
   `mode: "implicit"`, trusted recall settings, and Native current-time defaults.
   The same fixed token is used for both requests; no caching of authorization
   or responses occurs. Replace credentials only through trusted startup configuration.

Recall silently filters unauthorized scopes and revoked memberships. Expect the
authorized subset, or a successful no-items/`not_found` result, **not a
scope-existence 404**. Token authentication failure instead returns explicit
Native **401**, which is hook **exit 1 with an error envelope**.
These are unchanged Native behaviors, not a hook fallback or permission grant.

Stdin is limited to **32,768 bytes**. Invalid UTF-8/JSON, oversized input, and
validation failures produce explicit errors. The network deadline is shared by
**capabilities and recall together**, not two independent allowances.
Its default 2 s (finite 0.1–20 s) excludes process startup, stdin input/waiting
for EOF, and output;
it is **not** a total-process or LLM latency SLO. Set a separate host subprocess
timeout and close stdin. Request/response bounds are **256 KiB/2 MiB**; the
**entire compact JSON-serialized context pack** must have UTF-8 byte length
equal to `context_pack.byte_count` and obey the configured budget.
Serialization uses `ensure_ascii=False`, `separators=(",", ":")` and includes
metadata/citations, **not just text**. Returned item count must not exceed configured
`max_items`; the returned profile must match configuration. Mismatches fail
without broad fallback. These are not blanket
host-buffer limits. Shared-client extraction must preserve the MCP bounds above.
The hook does not write, capture, enqueue, invoke an LLM/provider, cache, retry,
or send an idempotency key.

Budget 64 is valid configuration, but even an empty pack costs roughly 192 bytes.
If its metadata cannot fit, Native returns **422 `budget_too_small`** and the hook
returns an error envelope with **exit 1**, not empty success. Do not treat the
approximate empty-pack size as a guaranteed constant. When the pack fits but
candidates do not, `budget_exhausted` is Native **200 / hook exit 0** instead.
Missing-index `index_incomplete` is also **200 / exit 0**, with
`coverage.lexical_incomplete: true` and `coverage.retrieval_complete: false`
when projections are missing and no candidates exist. The host must surface
that incomplete coverage even though the hook succeeded.

### Vendor-neutral Python harness example

After preparing the executable and operator environment, run this shell block.
It uses only Python's standard library. The example's host policy is to **pause
on failure or incomplete retrieval**, not silently continue; a different host
must explicitly choose and surface any continuation without memory.
The 30 s subprocess timeout is an illustrative **separate host policy**, not a
measured startup guarantee or service SLO. Set it for your deployment.
The trusted executable/PATH and startup environment must be operator-controlled.
The empty sample query requires no external model; a host may supply untrusted
task text only as `query`, never as configuration. Do not log it.

```bash
python - <<'PY'
import json
import os
import shutil
import subprocess
import sys

def pause(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)

setting_names = (
    "PGAG_HOOK_API_URL",
    "PGAG_HOOK_API_TOKEN",
    "PGAG_HOOK_SCOPE_IDS",
    "PGAG_HOOK_PURPOSE",
    "PGAG_HOOK_TOKEN_BUDGET",
    "PGAG_HOOK_MAX_ITEMS",
    "PGAG_HOOK_SEARCH_PROFILE",
    "PGAG_HOOK_TIMEOUT_SECONDS",
)
child_env = {name: os.environ[name] for name in setting_names if name in os.environ}
child_env["PATH"] = os.environ.get("PATH", os.defpath)
if not all(child_env.get(name) for name in setting_names[:3]):
    pause("Memory configuration missing; task paused.")
executable = shutil.which("pg-agmemory", path=child_env["PATH"])
if executable is None:
    pause("Memory executable unavailable; task paused.")

event = {"event": "session_start", "query": ""}
untrusted_memory_evidence = None
try:
    completed = subprocess.run(
        [executable, "recall-hook"],
        input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=child_env,
        timeout=30,
        check=False,
    )
except (OSError, subprocess.TimeoutExpired):
    pause("Memory subprocess failed or timed out; task paused.")

# Never echo raw stderr or exception/response text.
try:
    envelope = json.loads(completed.stdout.decode("utf-8"))
except (UnicodeDecodeError, ValueError):
    pause("Memory output invalid; task paused.")
if not isinstance(envelope, dict):
    pause("Memory envelope invalid; task paused.")
if completed.returncode != 0 or envelope.get("status") != "ok":
    pause("Memory retrieval failed; task paused.")
if (
    set(envelope) != {"status", "event", "result", "error"}
    or envelope.get("event") != event["event"]
    or envelope.get("error") is not None
    or not isinstance(envelope.get("result"), dict)
):
    pause("Memory success envelope invalid; task paused.")

result = envelope["result"]
coverage = result.get("coverage")
empty_reason = result.get("empty_reason")
if (
    not isinstance(coverage, dict)
    or type(coverage.get("retrieval_complete")) is not bool
    or empty_reason not in (None, "not_found", "budget_exhausted", "index_incomplete")
):
    pause("Memory coverage invalid; task paused.")
coverage_notice = {"retrieval_complete": coverage["retrieval_complete"]}
for name in ("truncated", "lexical_incomplete"):
    if name in coverage:
        if type(coverage[name]) is not bool:
            pause("Memory coverage invalid; task paused.")
        coverage_notice[name] = coverage[name]
print(json.dumps({
    "memory_status": "ok",
    "coverage": coverage_notice,
    "empty_reason": empty_reason,
}))
if not coverage["retrieval_complete"]:
    pause("Memory coverage incomplete; task paused.")

# Keep the full Native result separate from trusted instructions and policy.
untrusted_memory_evidence = result
print("Memory is separate UNTRUSTED evidence; no model or tool was invoked.")
PY
```

`subprocess.run(input=...)` sends one JSON document and closes the child's stdin.
It checks both exit code and structured status without printing raw stderr or
query-bearing exceptions. The hook validates the full Native RecallResult;
the example checks the envelope/coverage before retaining it separately.
Logs use only fixed coverage keys with validated boolean values and the
validated empty-reason enum, not raw response content. Full coverage remains
in the separate result. Invocation failures with no JSON, malformed envelopes,
and subprocess timeouts all pause without echoing raw content.
Retain all Native evidence/coverage fields; never promote the result into system
instructions, policy, or verified external truth.
This example is not vendor integration, a model call, or host-erasure proof.

### Failures, coverage, and deletion

Validated hook runtime outcomes emit one JSON envelope plus newline on stdout and sanitized
diagnostics on stderr. Success has `status: "ok"`, validated `event`, the full
Native `result`, and `error: null`. Failure has `status: "error"`,
validated `event` or null, **`result: null`**, and
`error: {code, retryable, outcome_unknown: false, native_status, request_id}`.
Native status and validated request UUID can be null.

| Runtime code | Exit | Handling |
|---|---|---|
| `invalid_hook_configuration` | `2` | Correct trusted startup configuration |
| `invalid_hook_input` | `2` | Correct UTF-8/JSON or event/query validation errors without logging the input |
| `hook_input_too_large` | `2` | Keep stdin within 32,768 bytes |
| `hook_input_unavailable` | `2` | Supply readable stdin |
| `hook_deadline_exceeded` | `1` | Combined network deadline exceeded; `retryable: true` |
| `native_api_unavailable` | `1` | Native API transport unavailable; `retryable: true` |
| `native_version_mismatch` | `1` | Use matching service/API/schema versions |
| `invalid_native_response` | `1` | Reject the invalid protocol/response, without fallback |
| `budget_too_small` | `1` | Native `422`: the pack metadata cannot fit; adjust the trusted byte budget |
| Mapped sanitized Native codes | `1` | Surface the Native failure without forwarding raw details |

**Invocation-error exception:** rejected CLI flags (including `--subject`/
`--once`) and a missing `hook` extra use argparse stderr and **exit 2 without
a JSON envelope**. All validated hook runtime errors, including the configuration/
input codes above, return the error envelope. Do not assume stdout is JSON
before checking/parsing it; never echo raw stderr or invalid output.

Exit **0** includes genuine Native `not_found`, `budget_exhausted`, and
`index_incomplete` empty reasons; inspect coverage even for successful retrieval.
Exit **2** is invalid configuration/input; exit **1** is Native/network/version/
protocol failure. A process killed by the host may have no envelope.
**Never map failed retrieval to empty success or hide it with stale context.**
Surface errors/coverage, then explicitly pause or continue without memory.
`retryable` is only a hint; the hook has no automatic retry or idempotency key,
and `outcome_unknown: false` reflects its read-only operations, not MCP mutation behavior.

The Native tenant session advisory barrier ends at HTTP delivery to this
**trusted local hook**. Hook/stdout/pipe buffers and host context are not
atomically covered. No retraction or deletion notifications exist.
After forget or ACL changes, stop using/discard previous context and invoke a
fresh hook under current authorization; do not assume an in-flight pre-change
result is fresh. The hook never expands permissions or proves host, WAL,
replica, backup, or physical-media erasure. Engineering tests do not qualify
specific vendor integration, semantic quality, or performance.
See [the complete contract](../STATUS.md#implicit-recall-hook) and
[ADR 0009](../adr/0009-implicit-recall-hook.md).

<a id="v008-application-update-schema-unchanged"></a>

<a id="v009-application-update-schema-unchanged"></a>

<a id="v0010-application-update-schema-unchanged"></a>

## Historical v0.0.10 application update (schema unchanged)

**Historical schema-7-only workflow, not the v0.0.11 upgrade.**
For the current version use the [schema-10 application update](#schema-10-application-only-upgrade).

For an existing v0.0.7/v0.0.8/v0.0.9 schema-7 database there is **no migration 008/009/010 or new
backfill**. Record the application/schema versions and preserve a backup and
current deletion/ACL records. Stop/drain old APIs, workers, and MCP adapters,
and suspend hook launches, including auto-restarts; replace them with matching v0.0.10 images. Confirm exact
schema history `[1, 2, 3, 4, 5, 6, 7]`, then start the restricted Native API/workers,
check authenticated capabilities, and start each fixed-identity adapter/hook.
Same schema does not establish rolling mixed-version compatibility or a
supported downgrade. v0.0.10 packaged runtime and retained schema-contract
checks passed locally and on both native Docker architectures. Historical results stay separate in
[validation evidence](../STATUS.md#validation-evidence).
For older schemas in this historical workflow, use the matching v0.0.10 image.
Reindex remains separate offline maintenance, not an MCP/hook command.

<a id="v003-maintenance-migration"></a>
<a id="v004-maintenance-migration"></a>
<a id="v005-maintenance-migration"></a>
<a id="v006-maintenance-migration"></a>

## v0.0.7 maintenance migration

**Historical schema-7 procedure for v0.0.7–v0.0.10 only.**
Upgrading an older schema to v0.0.24 must also apply migrations
[008](#schema-8-pgvector-upgrade), [009](#schema-9-scope-access-upgrade),
and [010](#schema-10-job-cancellation-upgrade)
and must not restart the schema-7 processes described here.

This is the retained schema-7 migration introduced in v0.0.7, for older
databases; it is **not a new v0.0.8/v0.0.9/v0.0.10 migration**.

**No rolling old/new API/worker coexistence or downgrade is supported.**
Rehearse upgrades only in disposable test databases. Passing migration tests
does not qualify a production upgrade or disaster recovery.
Follow this maintenance protocol:

1. Stop and drain **all old and new APIs and workers**, including replicas,
   continuous worker loops, and automatic restarts. The migration advisory lock
   is not a substitute for stopping API traffic and worker claims/publication.
2. Take a backup and record the old application/schema versions. Preserve the
   latest deletion ledger and ACL revocations independently as required for
   restore quarantine. Do not overwrite the only pre-migration backup.
3. With the privileged migration administrator and the new image, run
   `pg-agmemory migrate`. It applies pending scripts, Python lexical backfill,
   and ledger updates in one transaction under the migration lock.
   A 5-second lock timeout aborts rather
   than waiting indefinitely; diagnose contention while traffic remains stopped.
4. Migration 007 adds `memory.episode_lexical` and `memory.assertion_lexical`,
   with forced RLS, same-scope canonical foreign keys, and cascade deletion.
   Runtime grants are `SELECT`/`INSERT` only: no `UPDATE` or direct `DELETE`.
   Canonical parent purge cascades without child DELETE grants.
   Python backfill covers all retained episodes and every
   assertion revision, skips tombstones, and completes before schema 7 is recorded.
   Failure even after backfill completes rolls back projection DDL/data and the
   schema ledger together; a schema-6 upgrade remains at 6.
   Migrations 001–006 remain unchanged; older DBs receive missing versions
   sequentially. Preserve graph/job/assertion/effect histories, checkpoint checksums,
   canonical IDs/system times, source-event/idempotency receipts, and `Remember`
   JSON/HMAC ordering. Use the pinned Janome 0.5.0 dependency and bundled dictionary.
   The v4 ledger's stricter resume rules remain: untracked hints, even planned
   ones, block resumption.
5. Confirm exact history `[1, 2, 3, 4, 5, 6, 7]`, then start **only matching v0.0.10 APIs/workers**
   with restricted runtime credentials and the intended fixed worker subjects.
   Check capabilities/schema, default/opt-in recall and projection coverage,
   historical revision selection, authorization, purge, atomic publication, and
   compatibility before restoring traffic. A health response alone does not
   validate these. Migration/rebuild duration and resource use are unqualified.
6. On failure, leave APIs/workers stopped. Do not launch the old image against the
   changed schema or assume a downgrade exists. Any backup restore remains
   quarantined until the latest deletion/ACL state is reapplied and validated.

**The old v0.0.1 API does not contain the new schema-compatibility guard.**
It may start against an incompatible schema; operators must keep it stopped.
The new runtime's refusal of schema mismatches does not protect old processes.

## Lexical profile and reindex operations

Recall defaults to `search_profile: "simple-v1"`; explicitly request
`"ja-janome-0.5.0-v1"` for Japanese-script surface/wakati segmentation. The
response echoes the selected profile. Janome 0.5.0 uses bundled
mecab-ipadic-2.7.0-20070801 with Janome additions. ASCII identifiers/English pass
through the segmenter unchanged; PostgreSQL still performs lexical processing.
There is no Unicode/width normalization, lemma/stemming, synonym matching, or
segmentation/recall-quality qualification. Han handling can affect Chinese
characters without qualifying Chinese recall. This lexical profile is not an
embedding model or file-based memory index; vector/hybrid modes are separate. Context budgeting
remains the separate `utf8-bytes-v1` contract.

Use the supported container build profile: test and runtime builds sequentially
precompile **only static Janome package bytecode**, including dictionary modules.
It is packaged code, not a memory index/cache or compiled user input. Janome is
lazy-imported only for Japanese-script runs; English-only operations do not load
it. `max_cached_word_len=0` disables matcher input-prefix caching, retaining only
packaged dictionary-resource caches. Cold host installations without precompiled
dictionary code can have much larger initialization peaks.
The fresh Linux subprocess guard requires initialization peak RSS **below 256 MiB**
and no Janome import for English-only operations. Do not use that test threshold
as a deployment memory limit: request processing, concurrency, migration/rebuild,
and resource sizing are not qualified. See the bounded diagnostic observations
in [ADR 0007](../adr/0007-japanese-fts.md#runtime-initialization-boundary).

For the Japanese profile, missing currently authorized, requested-scope,
time-eligible projections set `coverage.lexical_incomplete: true` and
`coverage.retrieval_complete: false`, regardless of query relevance or job state.
Available matches may still return; in lexical mode an empty query browses canonical items even
with the flag. Exact required refs can also select canonical items without repairing
projections. No query/required candidates with missing projections gives
`empty_reason: "index_incomplete"`. If no optional-only candidates fit, success
retains `"budget_exhausted"`; a required item that cannot fit gives `422 budget_exhausted`.
A nonempty result has null `empty_reason`.
There is no silent simple-profile fallback or repair worker.
Choosing simple search does not repair the Japanese projection.
Corrupt-dictionary logs are sanitized to `japanese_dictionary_error`, without
input text. Janome `SystemExit` becomes tokenizer-unavailable and API
`503 dependency_unavailable`, not `index_incomplete`; workers follow existing
bounded dependency retries without input echo.

Reindex rebuilds **all tenants in the selected database**, not one worker
principal or scope. `--subject` is explicitly rejected rather than narrowing
access; `--once` is also rejected as worker-only.
To rebuild lexical projections in a migrated schema-8 database from canonical data
(verified with v0.0.11 tooling; this never rebuilds embeddings):

1. Stop/drain **all APIs and workers**, including automatic restarts, and back up
   as for migration. This is offline maintenance, not a live administrative API.
2. Use the matching v0.0.24 image and **`PGAG_ADMIN_DATABASE_URL`**, with forced-RLS
   bypass and the required table privileges, then run:

   ```bash
   pg-agmemory reindex-lexical
   ```

3. Use exact history `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` and the matching extension; the command takes the migration
   advisory lock with a 5-second lock timeout, and replaces both projection
   tables in one transaction. It segments every retained episode and assertion
   revision, not just heads, excluding tombstones. Canonical IDs, system times,
   evidence, receipts, and synchronous request hashes do not change.
   JSON output contains `profile: "ja-janome-0.5.0-v1"` and integer
   `episodes`/`assertion_revisions` counts only, never source text or tokens.
4. On failure, even after partial replacement, the existing lexical projections
   remain intact. Keep traffic stopped and diagnose
   schema, privileges, or lock contention. Never grant runtime bypass or edit
   canonical text, timestamps, or receipts to repair an index.
5. Restart only matching v0.0.24 APIs/workers. Before reopening traffic, inspect
   profile/coverage and authorized current/historical recall with approved test
   data. For exact `known_at` boundaries, use server-returned assertion
   `recorded_at`, not host/VM wall-clock samples.
   Counts alone do not certify relevance, completeness of world knowledge,
   or performance. No automatic recovery or DR guarantee is implied.

The packaged dictionary is a code dependency, while projections live only in
PostgreSQL. Preserve Janome's Apache-2.0 license and bundled IPADIC copyright/
license notices when redistributing images; see
[dependency licensing](../../README.md#dependency-licensing) and
[ADR 0007](../adr/0007-japanese-fts.md). M0/M1/M2/M3 and MVP/production acceptance
remain incomplete.

## Durable-job and worker operations

The worker publishes caller-supplied structured assertions; it is not an automatic
synthesis/NL extraction/LLM/provider, embedding, compaction, or tool-effect executor.
Provision a subject in advance, then run with only restricted `PGAG_DATABASE_URL`
credentials and trusted deployment identity:

```bash
pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT --once
```

The subject is 1–256 characters and must match the preprovisioned principal in the
configured issuer. It is not caller-controlled HTTP impersonation. Do not give
agents runtime DB credentials or authority to select worker subjects. No JWT
signing/public key or admin URL is needed by the worker; never use superuser,
table-owner/owner-member, or `BYPASSRLS` credentials. Startup shares API role/schema
validation. Only that principal's currently writable jobs can be claimed.
Same-scope readers can GET jobs but cannot run, retry, or cancel another principal's work.
[Owned-job query](#owned-job-query-and-pagination) discovers only the caller's own
jobs, even with scope-admin permission; it does not invoke the worker.

Omit `--once` for continuous operation: idle poll interval is 1 second, transient
DB loop delay is 2 seconds. `--once` handles at most one due job and prints JSON
`outcome` (`idle`, `succeeded`, `pending`, `failed`, or `lease_lost`), with applicable
opaque IDs/result reference. It does not wait for the entire queue or retry cycle;
other commands reject `--once`. Startup/unrecoverable errors are failures, not
successful idle results. This fixed-principal profile is not a global scheduler
or a qualified fairness/cost-pool implementation.
Worker stdout/logs contain opaque historical outcome references, not current
read authorization or a live snapshot. Read job GET/explain under current
access/deletion checks instead of relying on an earlier CLI outcome.

1. Submit `POST /v1/jobs` with an HTTP key and
   `{kind: "structured_remember", memory: <original Remember body>}`. Use current
   scope read/write access, explicit intent, and exact same-scope episode quotes.
   `202` is the committed job reference with fixed recipe `structured-remember-v1`,
   not publication completion. Save the original request securely for explicit
   retry; sanitize it before submission.
2. Retry uncertain enqueue with the same normalized request/key. Canonical
   intent/recipe also deduplicates across keys within one principal/scope,
   normalizing evidence order for job identity only. Another principal or
   different source identity is not semantic deduplication.
   Respect the scope's 100 pending/running cap; do not bypass it with other identities.
3. Poll job GET for state, attempts (maximum 5), scheduling/lease timestamps,
   safe error code, immutable episode input references, retry parent, and result.
   GET never returns request JSON, owner principal, or lease token. Terminal
   success/failure/cancellation erases the request; succeeded results stay revision 1 after
   later corrections. Use the result's exact assertion revision for explain.
   Assertion recorded/system time begins at worker publication, not job enqueue;
   do not substitute job `created_at` for the assertion's adoption time.
4. Automatic retriable failures use `2^attempt + [0,1)` seconds of backoff/jitter.
   `invalid_input` fails immediately; an expired fifth claim fails with
   `attempt_limit`, without a sixth attempt. Diagnose safe
   `dependency_unavailable`/`stale_context` codes without logging payloads.
   `lease_lost` does not authorize another publication from an old prepared body.
5. For an owned terminal failed job, POST the full original `EnqueueJob` body
   to `/v1/jobs/{job_id}/retry` with a key. Current evidence/permissions and HMAC
   intent are rechecked; changed intent is `409 job_intent_conflict`, nonfailed
   parent, including cancelled, is `409 job_retry_conflict`, and nonowner is `404`. Repeated retry of
   that parent reuses one child even across keys. If the child fails, retry its
   ID for another explicit five-attempt cycle. Never reset terminal rows/recipes in SQL.

Claims commit under `FOR UPDATE SKIP LOCKED` before payload preparation outside
the transaction. Default lease is 30 seconds with a fresh token and current epochs;
internal 1–300-second claim bounds are for controlled tests, not operator tuning.
Publication rechecks current identity/access, inputs/exact body, lease/token/expiry,
and epochs; output/provenance/job success/audit commit together. Final-update expiry
rolls back output. Internal heartbeat validates lease/epochs, but the deterministic
processor needs no background heartbeat task or external call. There are no public
claim/publish/heartbeat endpoints. At-least-once attempts produce at most one
committed result per job, not external exactly-once execution.
For [explicit cancellation](#explicit-job-cancellation), state/attempt CAS and
the shared tenant barrier decide the race: cancellation fences old publication,
but cannot unpublish an already-committed result or abort external work.

`observe` never auto-enqueues; synchronous `remember` and its legacy JSON/HMAC
remain unchanged. Recall's `jobs_pending` covers readable pending/running jobs
in requested scopes, excluding cancelled; `synthesis_pending` and `graph_used` remain false.
Jobs are not recall/explain items or checkpoint/effect reference kinds.
See [the contract](../STATUS.md#durable-jobs) and
[ADR 0006](../adr/0006-durable-jobs.md); M0/M1/M2/M3, MVP/production, performance,
quality, and DR acceptance remain incomplete.

## Entity and graph operations

Use [exact entity query](#exact-entity-query-and-pagination) to find distinct
candidate IDs across readable scopes. It includes shared-scope entities without
an ownership filter; inspect GET evidence and explicitly choose graph seeds.

1. Create explicit entities from approved same-scope episode quotes with
   `POST /v1/entities`, an allowlisted type, bounded canonical label, and
   `explicit_intent: true`. Save each returned revision-1 UUID. Labels/types are
   caller reports, not trusted instructions or verified facts. Use entity GET
   for metadata/evidence, not recall/explain. There is no alias/merge/name-resolution
   or label-correction endpoint; a new HTTP key may create a separate same-label
   identity. Retry uncertain creation with the original key/body.
2. Create relations only through `POST /v1/relations`, passing same-scope source/
   target entity UUIDs and episode evidence. The returned ID is the canonical
   assertion, not a second relation object. Matching free-text `remember` data
   stays untyped. All allowlisted predicates are multi-valued reported declarations.
3. Correct with `POST /v1/relations/{memory_id}/revisions`, an exact expected
   revision, target UUID, replacement evidence/valid bounds, explicit intent, and
   reason. Source/predicate stay fixed; omission of bounds is unbounded and
   replaces the entire interval. Generic assertion correction returns
   `409 relation_revision_required`. Inspect exact historical revisions through
   explain; omitted revision is 1, not latest. Never edit typed links or values in SQL.
4. Call authenticated, read-only `POST /v1/graph/expand` with explicit distinct
   scopes, entity seeds, predicates, and purpose; no `Idempotency-Key` is needed.
   Limits are 32 scopes, 16 seeds, 5 predicates, 1–2 hops, and 1–100 paths.
   Inspect effective `as_of`/`known_at`, coverage, and epochs. Prefixes count;
   cycles cannot repeat nodes within paths. Incoming/both is traversal orientation,
   not inferred inverse truth. Hidden seeds are not echoed; visible isolated
   seeds may be returned with no paths. Empty/bounded results do not prove absence.
5. Treat `409 graph_invalidated` as an invalidated read and DB `503` as failure,
   never as an empty graph. PostgreSQL canonical joins need no AGE/SQL/PGQ
   installation, graph projection rebuild, or lag/watermark operation:
   `backend: "sql"`, `projection_watermark: null`. There is no dynamic graph
   SQL/Cypher/label input. Recall remains FTS with `graph_used: false`.
6. Declare every copied entity revision 1 or exact assertion revision in
   checkpoint/effect `memory_refs`, including graph-derived dependencies.
   Entity GET and relation explain supply evidence; expansion nodes omit quotes.
   Do not treat a path or canonical label as permission to execute an action.

See [the contract](../STATUS.md#entities-and-sql-graph-oracle) and
[ADR 0005](../adr/0005-relational-graph.md). This bounded correctness reference
is not graph-utility/performance evidence, full M0/M1/M3, MVP, or production/DR qualification.

## Checkpoint operations

1. Capture only sanitized schema-1 state. Declare every copied memory source in
   `memory_refs`, including the exact revision. Undeclared copies are not
   discovered by a semantic scanner.
2. Create under the intended scope/run/branch with an explicit `expected_head`;
   use null only for a new branch. Resolve `409` head/watermark/harness conflicts
   rather than silently resetting the head. Save the returned checkpoint ID.
3. Load a known checkpoint ID through GET, or use [head lookup](#checkpoint-head-lookup)
   for an exact scope/run/branch, not recall/explain. GET need not be the latest head.
   Head lookup never falls back from an invalidated branch. Treat checksum
   or reference-validation failures as invalidation, not permission to bypass
   validation or edit the stored payload.
4. Restore only to a never-used target branch with the exact harness ID/version
   and state schema. The source branch remains unchanged. Saved assertion
   references keep their exact historical revisions; restore does not select
   the latest revision or automatically refresh external facts. Inspect
   `tool_effects`, `untracked_effects`, `requires_reconciliation`, and `resume_allowed`
   before handing state to a harness. These include all live run effects, not
   just snapshot-time effects. Untracked planned hints also block; a conflicting
   unknown hint plus a tracked planned effect needs uncertainty/receipt reconciliation.
   Restore atomically marks dispatched ledger records unknown before creating
   the fork; CAS rejects stale ledger writers, not in-flight external calls.
   `automatic_reexecution` is always false. No provider receipt lookup or code execution
   is performed by this API.
5. Retry uncertain writes with the same key and payload, then inspect the head;
   head lookup alone does not prove that write committed. Only the original
   result reference is retained in idempotency records, not state. Current
   authorization/checksum checks still apply; a purged checkpoint returns `404`.

A saved epoch or `resume_allowed: true` is not an approval or an external-effect
receipt. Typed pending effects are snapshot hints; the durable ledger is separate.
Checkpoint creation allows a 1 MiB body; other endpoints allow 256 KiB.
See [the contract](../STATUS.md#checkpoint-contract) and
[ADR 0004](../adr/0004-tool-effects.md). No production/DR qualification is implied.

## Tool-effect operations

1. Bootstrap the intended scope-local run with a checkpoint before planning
   effects. Compute a stable lowercase 64-hex hash of the host's canonical action,
   then POST its operation UUID, tool name, hash, and all exact memory dependencies.
   The service does not store arguments or the raw hash, or verify the intended
   external call. Sanitize tool names, reasons, receipt references, and state.
2. Save the returned `memory_id` and GET the latest record, including its stable
   `external_idempotency_key` and `run_invalidated` flag. Identity is scoped by
   tenant/scope/run/operation; new run/operation IDs do not deduplicate equivalent
   real-world actions. The lifetime cap is 100 effects per run, including terminals.
3. The host must enforce permissions/approvals and durably record a CAS transition
   to `dispatched` **before** any outside call. Use the stable external key if the
   provider supports it. The host owns execution coordination: a replayed old
   dispatch acknowledgment is not fresh permission to send or blindly resend.
4. On an uncertain outcome, record `unknown` and reconcile with the provider
   outside this service. `planned → unknown` can capture a legacy/off-protocol
   attempt; it is not permission to execute. `unknown → dispatched` is forbidden.
   Terminal `confirmed`/`failed` requires a bounded receipt reference plus
   `provider_receipt` or `operator_review`; both are caller-reported, not verified.
   Terminal outcomes are immutable and do not authorize automatic retries.
5. Retry uncertain ledger writes with the same key/body. Changed intent conflicts;
   a new key for the same recorded intent still returns its original revision-1
   reference. Plan/dispatch responses are historical revision references, not
   current-state snapshots or execution authorization. Under current authorization,
   replay for a surviving effect may still succeed after run sealing, without
   allowing fresh dispatch. Read GET for current state rather than trusting an old response.
   Do not delete checkpoint hints to bypass reconciliation or reuse IDs to evade
   uncertainty. An untracked hint must be resolved explicitly by the host.
6. After an effect purge seals the run, do not create new intents, dispatch, checkpoint, or
   resume it. Independent surviving effects remain GET-readable and can use
   allowed reconciliation transitions; `unknown → confirmed/failed` remains valid.
   Preserve the opaque operation registry, run flag, and tombstones.

This is a ledger, not a worker, tool-execution harness adapter, provider-query client, approval
service, or external exactly-once mechanism. See [the contract](../STATUS.md#tool-effect-ledger)
and [ADR 0004](../adr/0004-tool-effects.md).

## Revision operations

Use [assertion metadata history](#assertion-metadata-history) for bounded read-only
discovery; it is neither historical authorization nor a CAS reservation.

Corrections append a full replacement revision, with immutable subject,
predicate, and scope. Preserve the same `Idempotency-Key`, target ID, and body
when retrying an uncertain correction. A successful replay returns its original
revision reference even if newer revisions exist; it is not a read of the head.
For `409 revision_conflict`, resolve the stale expected head rather than
silently overwriting. There are at most 1000 total revisions per assertion.

`explain` with no revision still means revision 1, not latest. Use the exact
revision returned by a mutation/recall when inspecting that result. Each
historical read remains subject to current ACLs and tombstones. A future-dated
replacement does not preserve the previous value before its new valid interval.
See [the revision contract](../STATUS.md#assertion-revision-contract) and
[ADR 0002](../adr/0002-assertion-revisions.md).

## Authentication, transport, and health

The verifier accepts RS256 with required `sub`, `iss`, `aud`, `iat`, and `exp`,
validates signature/issuer/audience/time, and resolves the external subject in
PostgreSQL. There is no JWKS refresh, overlapping-key rotation workflow, or
delegated identity. Changing the issuer requires reviewing subject mappings;
the database does not namespace principals by multiple issuers.

The API listens on HTTP port 8000. Terminate TLS at a trusted reverse proxy and
do not expose the runtime port directly to an untrusted network. No built-in
credentials or authentication bypass is provided. `/docs` and `/openapi.json`
are runtime-generated schema views, not deployment authorization.

`GET /healthz` is process liveness after startup validation. A successful probe
does not establish current database connectivity, authorization correctness,
or readiness for production. [Runtime readiness](#runtime-readiness) adds
`GET /readyz` for the bounded read-only contract, not complete authorization or
writability. Neither probe replaces authenticated resource checks.
Database/lock failures on memory resource requests can return `503`; replay
an uncertain mutation with the same key and unchanged payload rather than
inventing a new key. A committed mutation may have lost its HTTP acknowledgment.

## Membership maintenance and request drain

**Current default: use the CAS-safe [scope-access CLI](#scope-access-administration).**
It holds the session lock through commit and stdout flush and supplies atomic
epoch/audit updates. The handwritten sequence below is a **historical expert
fallback**, not a schema-9/10-ready recipe: it lacks expected-epoch CAS, conditional
epoch advancement, and `scope_access_event` audit. Do not run it unchanged on
schema 9 or 10. An expert override must implement all current invariants; otherwise
use the supported CLI. Direct SQL bypasses are not covered by its guarantees.

**Administrative permission changes must cooperate with the API's lock.**
There is no runtime membership-management endpoint. The API opens a short-lived
connection per request and takes the session advisory lock
`pg_advisory_lock(hashtextextended(tenant_uuid::text, 0))` using canonical UUID
text. It holds that lock through commit and buffered response sending.

On one dedicated administrative connection:

1. Acquire that same tenant **session** lock before modifying membership.
2. Begin a transaction, update permissions/membership, and increment that
   tenant's `access_epoch` in the same transaction.
3. Verify the intended tenant/scope/principal and affected rows, then commit.
4. Only after commit, release the lock or close the connection. On failure,
   roll back before releasing it; do not leave a locked session in a pool.

For a disposable test DB, the following `psql` example narrows an existing
membership to read-only. Supply `tenant_uuid`, `scope_uuid`, and `principal_uuid`
as `psql` variables for the provisioned test records. Run the sequence on the
same administrative connection and check affected rows before `COMMIT`.
The UUID cast normalizes text to match the runtime's lock key.

```sql
\set ON_ERROR_STOP on
SELECT pg_advisory_lock(hashtextextended(:'tenant_uuid'::uuid::text, 0));
BEGIN;
UPDATE memory.scope_member
SET permissions = ARRAY['read']::text[]
WHERE tenant_id = :'tenant_uuid'::uuid
  AND scope_id = :'scope_uuid'::uuid
  AND principal_id = :'principal_uuid'::uuid;
UPDATE memory.tenant
SET access_epoch = access_epoch + 1
WHERE id = :'tenant_uuid'::uuid;
COMMIT;
SELECT pg_advisory_unlock(hashtextextended(:'tenant_uuid'::uuid::text, 0));
```

A transaction-only advisory lock, a different hash/seed, unlocking before
commit, or updating ACLs without this lock does **not** satisfy the drain
protocol. Such administrative races are not covered. Tenant-wide serialization
also means a slow response can delay unrelated requests within that tenant;
performance is unmeasured. The protocol cannot revoke context already delivered
or bytes already passed to the network.

## Purge and retained records

Use explicit IDs and inspect `preview` before a destructive test. Preview does
not freeze targets; authorization and dependencies are evaluated again for
purge. Only `preview` and `purge` are accepted, even though future-mode names
may appear in internal schema constraints.

Purge traverses episode/entity/assertion history, job dependencies/retry lineage,
declared checkpoint/effect references, and
every descendant/fork checkpoint through the complete parent lineage. Its
limit is 10,000 dependents in total plus requested roots. A source used only
by an old assertion revision still removes the entire assertion history and
all affected checkpoint state. Episode evidence also leads to entities and all
relation histories using them as source or **any historical target**; direct entity
purge follows the same relation dependencies. Direct entity references in
checkpoints/effects participate. Other surviving entities are not removed merely
because a relation disappears. Semantic relation cycles are not provenance cycles:
entities depend only on episodes.
Job closure follows episode inputs → jobs, result assertions → jobs, and parent
jobs → retry descendants within the same 10,000-dependent bound.
**Deleting a job or failed-parent retry chain does not delete an already-published
independent assertion or source episode.** Purge its output/source explicitly to
erase the fact. Output assertions retain their own direct episode provenance;
any revision-source deletion removes the whole assertion and its dependent jobs.
There is no job → result dependency cycle.
Branches whose heads are affected are permanently
invalidated; do not try to reopen their IDs or remove lineage to avoid deletion.
Purging any effect also removes **all checkpoint payloads in that scope/run**,
including older empty snapshots, and permanently sets `effects_invalidated`.
It blocks new plans, dispatch, checkpoints, and resumption, but does not purge
independent effects merely for sharing the run; surviving records remain reconcilable.
Job request/input rows are removed before assertion/episode rows and tombstones
under the same tenant barrier, fencing running publishers. Purged-job GET/replay
returns `404`; retained job identity prevents exact-job resurrection.
Canonical episode/assertion deletion cascades all corresponding lexical
revisions in that same barrier before tombstones commit. These rows are derived
payload, not separate memory/provenance vertices; rebuild skips tombstones and
cannot recover purged source content.
Payloads, entity evidence, typed links, quotes, references, and effect events
(reason/receipt references included)
are SQL-deleted from active tables before timestamped markers enter
`memory_ops.object_tombstone` in the same transaction. Object SELECT RLS hides
those anchors; there is no soft-delete `deleted_at` update on `memory.object`
or privileged deletion helper. The barrier/receipt commits before responding.
The tenant session lock covers closure, run/branch invalidation, and read draining.
Purge does not enqueue a worker or rebuild affected content.
The receipt's `active_store_purged` is not
full erasure.

Opaque job identities, operation registry/run flags, run/branch metadata, objects and tombstones,
audit/receipt metadata, and
tenant-keyed HMAC source/idempotency tombstones remain for the tenant lifetime.
Do not manually remove
them or change `dedup_secret` to “finish” a purge: doing so can defeat replay
protection. Exact replay of deleted source identities or memory results returns
`404`; payload conflicts remain `409`. No automatic full tenant-erasure
procedure is provided. Historical references/replay cannot recover purged
entity labels, relation values, or receipts. Backup limits below are unchanged.

## Backups, restoration, and release evidence

Checkpoint restore copies typed state inside the Memory DB; it is not database
backup restoration, a separate working-snapshot compaction system, or disaster recovery.
A database backup can also roll back effect states. Keep external execution stopped
and reconcile provider outcomes separately; the ledger does not automate safe recovery.

Deletion receipts report `backup_status: "operator_managed"` with
`backup_retention_deadline: null`. SQL row deletion is not proof of physical
media sanitization, removal from WAL/replicas/backups, or erasure of delivered
context. There is no implemented backup-retention deadline enforcement,
automated restore replay, HA/PITR workflow, or verified RPO/RTO.

Required restoration boundary, **not yet an implemented automated procedure**:

1. Keep any restored database quarantined: no API, worker, agent, or user access.
2. Obtain the latest deletion ledger and ACL revocations from a source that
   was not rolled back with the backup; the old backup's own records are not
   sufficient.
3. Apply those deletions and permissions, including the corresponding epochs,
   before considering exposure. Preserve the tenant deduplication state.
4. Validate absence of deleted content and unauthorized access on the restored
   state. If current records are unavailable or cannot be safely applied, keep
   it quarantined. There is no supported command here that automates these steps.

Exercise only disposable restore drills; do not claim DR or production
compliance from this checklist or a health probe. Record actual test commands,
environment, architecture, and outcomes separately from the plan's unmeasured
targets. See [contributing](../../CONTRIBUTING.md) for the Apple Container and
native dual-architecture Docker CI checks.

`scripts/test-containers.sh` covers both production API HTTP smoke and actual
worker CLI smoke in the non-root production image. It provisions a disposable
principal, supplies runtime-only credentials to `worker --subject ... --once`,
asserts `{"outcome":"idle"}`, and logs `Production worker smoke passed`.
It also checks `東京都` → `東京` / `都` segmentation in the non-root runtime image
and emits `Production Japanese tokenizer smoke passed` on success. This tests
packaged tokenizer initialization/segmentation, not end-to-end recall or quality.
The historical v0.0.8 runner also launches an actual `pg-agmemory mcp` child in the
non-root production image. Its fixed token and provisioned scope access the
loopback Native API; it lists all four tools and calls recall in **both**
modern `2026-07-28` and legacy `2025-11-25` modes. Existing Japanese/API/worker
smokes remain. These checks passed in all three v0.0.8 environments.
The v0.0.8 CI step is `Test containers and smoke-test production API, worker, and MCP`.
This idle-worker check is not a publication test or production/DR qualification.
Historical v0.0.7 implementation
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)
passed local Apple Container and exact-SHA native Docker
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029);
all three production smokes passed in each environment. Detailed results are in
[STATUS](../STATUS.md#validation-evidence), not a production/DR acceptance claim.
Historical final-docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)
also passed both native jobs in
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899).
**Historical v0.0.8:** implementation
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)
passed 214 tests, Ruff, strict mypy (13 files), and all production smokes locally
and in [CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)
on both native architectures. The two v0.0.7 runs do not validate MCP.
The subsequent v0.0.8 bilingual documentation commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)
passed **214 tests on each native architecture** in
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509).
None of these historical runs validates the v0.0.9 hook or shared-client extraction.
**Historical final v0.0.9 results verified 2026-09-17 JST:** Apple Container and native Docker
amd64/arm64 each passed **274 tests, 1 existing warning**, Ruff, strict mypy
(**15 source files**), genuine core-only/hook-only installation checks, and all
non-root production Japanese/API/worker smokes, MCP **`2026-07-28` and `2025-11-25`**,
and hook **`session_start`, `task_switch`, and `after_compaction`**.
Test elapsed times were **248.29 s** locally, **482.21 s** on native amd64, and
**374.33 s** on native arm64. The tested final local source matches published implementation
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050).
Actual logs from both jobs in
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)
confirmed that exact SHA and all checks, not just job status.
Timings are not performance benchmarks.
See [v0.0.9 evidence](../STATUS.md#v009--schema-7), not a production/DR acceptance claim.
The implemented Docker **`adapter-extras-check`** target checks genuine core-only
installation/missing extras, then hook-only **without MCP**, including explicit
JSON for failed HTTP. The container script builds it on local Apple Container
and both native Docker architectures. It **passed in all three environments**.
No original milestone or acceptance gate is complete.
Final v0.0.9 documentation
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)
also passed **274 tests** on both native architectures in
[CI 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689).
These are historical results, not v0.0.10 evidence.

**Historical v0.0.10 final local and native results verified 2026-09-17 JST.**
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
not just job status.
Coverage includes rollback faults, source/key deduplication races,
quota/RLS/deletion, and API process restart with an actual worker.
For a fresh fixture, the production smoke passed in all three environments.
It follows MCP/hook checks with Native capture →
pending job → actual worker CLI `--once` → episode/assertion recall →
same capture replay → source purge → job GET `404` and capture replay `404`.
All three final runs also cover committed HTTP 201 response loss followed by same-key
recovery of the exact pair with one publication, and stable replay of the original
failed capture job after explicit retry-child creation.
Elapsed time is not a performance benchmark; all acceptance gates remain incomplete.
See [v0.0.10 evidence](../STATUS.md#v0010--schema-7).
Final v0.0.10 documentation
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)
also passed **304 tests per native architecture** in
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760).
Those docs-run results are distinct from the implementation-run timings above;
neither run validates v0.0.11/schema 8.

**v0.0.11 application checks passed in all three environments**: the newly pinned upstream
DB profile, migration/schema/extension-version/schema guards,
normalization/digest/immutable model-space limits, exact/RRF math, ACL/time
prefiltering, coverage, purge/replay, and retained lexical/MCP/hook/capture checks.
Implemented fixtures include DB norm/dimension/composite-FK/eight-model guards,
direct RLS visibility/denied updates, ACL revocation, and actual schema-7→8
ledger-failure rollback of DDL/extension followed by retry without backfill.
The production vector smoke uploads **both episode and assertion projections**,
checks basis distances **[0, 1]** and RRF, then source purge and upload replay `404`.
Each environment passed **345 tests, 1 existing warning**, Ruff, strict mypy
(**17 source files**), core-only/hook-only installation checks, and all production smokes.
Test elapsed was **283.44 s local**, **404.40 s amd64**, **433.46 s arm64**, not
a performance benchmark. Published implementation
[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)
passed [CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403);
see [the exact evidence](../STATUS.md#v0011--schema-8).
Artifact inspection and the extension pin/license are verified separately.
The synthetic basis-vector fixture cannot qualify semantic quality, performance,
untrusted-vector robustness, production, DR, or full erasure.
