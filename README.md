# pg_agmemory

[日本語](README-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**PostgreSQL-backed agent memory, licensed under MIT.** The public repository
is [`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory); the local checkout directory, Python package,
and service are `pg_agmemory`. Run the commands below from that local checkout.

**Status: v0.0.8/schema 7 local stdio MCP implemented;
local and native Docker checks passed.
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
search. The new milestone adds a local, fixed-identity MCP adapter over the Native
API—not remote MCP, automatic synthesis, or a measured retrieval-quality improvement.

Cross-assertion supersession/fact arbitration, provider receipt verification, harness adapters,
automatic enqueue/extraction, general multi-tenant scheduling,
pgvector, AGE/SQL/PGQ, remote MCP HTTP/SSE/OAuth/delegation, application SDKs, and postgresem adapters
remain roadmap work. No performance or memory-quality acceptance targets have
been measured. Consult [the current contract and limitations](docs/STATUS.md)
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
and **linux/arm64** runners. The step is
`Test containers and smoke-test production API, worker, and MCP`:

```bash
./scripts/test-containers.sh docker
```

No hosted model key or external memory database is required. Container images
and Python dependencies must be downloadable on the first run.
**v0.0.8/schema 7:** implementation
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)
passed **214 tests** (1 existing warning), Ruff, strict mypy (13 source files),
and all production smokes in Apple Container and native Docker amd64/arm64.
Both MCP protocol modes above passed. See
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)
and [validation evidence](docs/STATUS.md#validation-evidence).

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
These older runs are not v0.0.8 results.

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

**v0.0.8 retains schema 7; there is no new migration from v0.0.7.**
Stop/drain old APIs, workers, and adapters before replacing them with matching
v0.0.8 processes; do not assume mixed-version compatibility.
For databases older than schema 7, a maintenance stop and backup are required. Stop/drain all
old/new APIs **and workers**, apply pending migrations through `007_japanese_fts.sql`
with its atomic Python lexical backfill, then start only matching v0.0.8 APIs/workers.
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

Install the optional `pg-agmemory[mcp]` package extra, or use the repository image,
whose test and runtime stages include it. It pins the official **mcp 2.2.0** SDK
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
capabilities request and requires API `v1`, service `0.0.8`, and schema `7`.
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
back up, rebuild, then restart matching v0.0.8 processes only. There is no automatic
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
