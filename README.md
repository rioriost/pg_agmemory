# pg_agmemory

[日本語](README-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**PostgreSQL-backed agent memory, licensed under MIT.** The public repository
is [`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory); the local checkout directory, Python package,
and service are `pg_agmemory`. Run the commands below from that local checkout.

**v0.0.9/schema 7 implicit recall hook implemented; local Apple Container and
native Docker amd64/arm64 checks passed. v0.0.8 evidence stays historical.
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
The v0.0.9 contract adds an optional vendor-neutral, harness-side implicit recall
hook—not automatic host registration, remote MCP, automatic synthesis, or a
measured retrieval-quality improvement.

Cross-assertion supersession/fact arbitration, provider receipt verification, vendor-specific harness adapters,
automatic enqueue/extraction, general multi-tenant scheduling,
pgvector, AGE/SQL/PGQ, remote MCP HTTP/SSE/OAuth/delegation, application SDKs, and postgresem adapters
remain roadmap work. Performance, memory quality, disaster recovery, and
full-erasure acceptance remain unmeasured or unqualified.
Consult [the current contract and limitations](docs/STATUS.md)
before using the service.

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
**v0.0.9/schema 7, final results verified 2026-09-17 JST:** all three environments
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

Use PostgreSQL 18. The following are application commands to run inside an
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

**v0.0.9 retains exact schema 7; there is no migration 008 or 009,
DDL, or new backfill from v0.0.7/v0.0.8.**
Stop/drain old APIs, workers, and adapters before replacing them with matching
v0.0.9 processes; do not assume mixed-version compatibility.
For databases older than schema 7, a maintenance stop and backup are required. Stop/drain all
old/new APIs **and workers**, apply pending migrations through `007_japanese_fts.sql`
with its atomic Python lexical backfill, then start only matching v0.0.9 APIs/workers.
Both require exact history `[1, 2, 3, 4, 5, 6, 7]`.
Keep old images stopped; v0.0.1 lacks a schema-compatibility guard.
No rolling coexistence or downgrade is supported. Follow the
[migration procedure](docs/operations/README.md#v007-maintenance-migration).

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

## Local stdio MCP

A plain `pg-agmemory` package installation does **not** install optional `mcp`
or `hook` dependencies. Select `pg-agmemory[mcp]` for MCP or
`pg-agmemory[hook]` for recall-hook. Repository Docker test/runtime images
intentionally include both extras; that is **not** the base-package default.

Install the optional `pg-agmemory[mcp]` package extra, or use the repository image,
whose v0.0.9 test and runtime stages include both `mcp` and `hook` extras.
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
capabilities request and requires API `v1`, service `0.0.9`, and schema `7`.
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

Remember is explicit structured publication only; capture episodes through Native
`observe`, not MCP. UTF-8 byte budgeting (not model tokens), opt-in Japanese
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
v0.0.9 retains both modern `2026-07-28` and legacy `2025-11-25` protocol
contracts and all MCP semantics. Regression and both protocol smokes passed
locally and on both native Docker architectures.

## Implicit recall hook

**v0.0.9: local and both native Docker checks passed.** Install optional
`pg-agmemory[hook]` (`uv sync --frozen --extra hook` in this checkout).
It pins **httpx 0.28.1, not the MCP SDK**; Docker test/runtime include both extras.
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
**service `0.0.9` / API `v1` / schema `7`**, then posts Native recall with
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
An empty query still browses canonical items, even with an incomplete-index flag.
The flag measures projection coverage, not query relevance or queued work.

Writes index episodes and every assertion revision atomically, including typed
relations and job publication. Lexical runtime grants are `SELECT`/`INSERT` only:
no `UPDATE` or direct `DELETE`. Canonical parent purge removes derived rows by FK
cascade in the same barrier, without child DELETE grants; they are not separate
memories. Offline `pg-agmemory reindex-lexical` rebuilds **all tenants in the
selected database** using `PGAG_ADMIN_DATABASE_URL`; `--subject` is rejected,
not a scope filter, and `--once` is worker-only. Stop/drain APIs and workers,
back up, rebuild, then restart matching v0.0.9 processes only. There is no automatic
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
envelope. Checkpoints do not appear in `recall` or `explain`.

`POST /v1/checkpoints/restore` requires an exact harness/version match and
creates a new branch; it never rewinds the original branch. Dispatched effects
in the run ledger become unknown atomically with the fork. GET/restore merge
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
