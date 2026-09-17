# pg_agmemory

[日本語](README-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**PostgreSQL-backed agent memory, licensed under MIT.** The public repository
is [`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory); the local checkout directory, Python package,
and service are `pg_agmemory`. Run the commands below from that local checkout.

**Current bounded milestone: v0.0.11/schema 8 pgvector exact/hybrid retrieval foundation.
Implementation, local Apple Container, and both native Docker architectures are verified.
Verified v0.0.10 and earlier results remain historical.
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
application SDKs, and postgresem adapters
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
**v0.0.11/schema 8: verified 2026-09-17 JST.** Apple Container and native Docker
amd64/arm64 each passed **345 tests, 1 existing warning**, Ruff, strict mypy
(**17 source files**), core-only/hook-only installation checks, and all non-root
production smokes, including exact/hybrid episode/assertion vector retrieval and purge.
Test elapsed: **283.44 s local**, **404.40 s amd64**, **433.46 s arm64**; these are
not performance benchmarks. Implementation
[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)
passed [CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403).
See [the qualification evidence](docs/STATUS.md#v0011--schema-8).

The adopted DB profile is the prebuilt
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`.
Artifact inspection verified **both amd64/arm64 images**: PostgreSQL
**18.6-1.pgdg12+2**, native ELF, and `vector.control` **0.8.6**.
The PostgreSQL version stays 18.6, but **this is a new upstream DB image/profile
with a different base digest**, not the unchanged old library PostgreSQL image.
There is no new DB Dockerfile, source-build, or host-APT workflow in this profile.
Artifact verification is separate from the passing application/migration/CI evidence above.
Python dependencies stay unchanged apart from project-version metadata;
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

**v0.0.11 requires schema 8 and new `008_pgvector.sql`.** PostgreSQL must provide
`vector` **0.8.6 in `public`**; migration rejects an existing extension with the
wrong version or schema. The prebuilt profile supplies the matching extension.
API, worker, and `migrate` validate it even when schema 8 is already recorded.
Stop/drain **all old/new APIs, workers, adapters, and hook launches**, preserve a
backup and current deletion/ACL records, then migrate offline.
Older databases also apply the retained migrations, including migration 007's
lexical backfill. **There is no embedding backfill or automatic embedding rebuild**.
Only matching v0.0.11 processes may restart; API/worker startup requires exact
history `[1, 2, 3, 4, 5, 6, 7, 8]` and extension `vector` 0.8.6 in schema `public`.
Schema-7 processes are not rolling-compatible with schema 8.
Keep old images stopped; v0.0.1 lacks a schema-compatibility guard.
No rolling coexistence or downgrade is supported. Follow the
[schema-8 procedure](docs/operations/README.md#schema-8-pgvector-upgrade).

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

A plain `pg-agmemory` package installation does **not** install optional `mcp`
or `hook` dependencies. Select `pg-agmemory[mcp]` for MCP or
`pg-agmemory[hook]` for recall-hook. Repository Docker test/runtime images
intentionally include both extras; that is **not** the base-package default.

Install the optional `pg-agmemory[mcp]` package extra, or use the repository image,
whose v0.0.11 test and runtime stages retain both `mcp` and `hook` extras.
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
capabilities request and requires API `v1`, service `0.0.11`, and schema `8`.
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
Historical v0.0.10 and current v0.0.11 checks also passed.

## Implicit recall hook

**Retained read-only, lexical-only hook, verified in v0.0.11.** Historical v0.0.9 local/native
checks passed. Install optional
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
**service `0.0.11` / API `v1` / schema `8`**, then posts Native recall with
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
back up, rebuild lexical projections, then restart matching v0.0.11 processes only.
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
| [Atomic structured capture decisions](docs/adr/0010-atomic-capture.md) | [Atomic structured captureの決定](docs/adr/0010-atomic-capture-jp.md) |
| [Pgvector retrieval decisions](docs/adr/0011-pgvector-retrieval.md) | [Pgvector retrievalの決定](docs/adr/0011-pgvector-retrieval-jp.md) |
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
