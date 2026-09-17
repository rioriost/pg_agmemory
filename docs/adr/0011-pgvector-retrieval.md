# ADR 0011: Pgvector exact and hybrid retrieval foundation

[日本語](0011-pgvector-retrieval-jp.md) | [Draft contract](../STATUS.md#pgvector-exact-and-hybrid-retrieval) | [Operations](../operations/README.md#schema-8-pgvector-upgrade)

- Date: 2026-09-17
- Status: accepted and verified in v0.0.11/schema 8
- Extends: [ADR 0007](0007-japanese-fts.md) and [ADR 0010](0010-atomic-capture.md)
- Repository/license: `rioriost/pg_agmemory`; project MIT unchanged; bilingual documentation maintained
- Acceptance: full M0–M3, MVP, production, performance, memory quality, DR, and full-erasure gates remain incomplete

## Decision and authority boundary

Add explicit, provider-independent vector projections and exact/hybrid retrieval.
**Lexical remains the default**. This is an experimental mathematical foundation,
not an embedding provider, model registry, automatic generation/backfill, or
qualified semantic search. PostgreSQL remains the sole application persistence store.

Read-only Native `POST /v1/embedding-inputs` uses existing Explain
`{memory_id, revision}` (default **1, not latest**) without an idempotency key.
Only currently readable episode/assertion revisions are accepted.
It returns `{memory_id, revision, type, text, input_digest,
input_format: "memory-content-v1"}`. Episode text is normalized content;
assertion text is exactly `subject / predicate: value`, matching
`MemoryItem.content`, including relation assertion display values. No IDs/times
are added to embedding text.

The digest is SHA-256 of UTF-8 text, **not semantic support, model quality,
external verification, or proof that a vector came from this text**.
Canonical input is private content: do not log it or send it to third parties
without explicit approval. Examples use only synthetic data and no external model.

## Explicit immutable upload

Native `POST /v1/embeddings` requires caller-owned `Idempotency-Key`,
`memory_id`, canonical `revision` (default 1), lower-case 64-hex `input_digest`,
`model`, and `values`. Model name/revision are required 1–256-character strings;
dimensions **768**, distance metric **`cosine`**, and normalization
**`l2-f32-v1`** are fixed. Exactly 768 finite JSON numbers are required.
Normalize in float64, then store pgvector float32; reject zero/non-finite/
un-normalizable vectors, booleans, numeric strings, wrong dimensions, and truncation.

Scope/identity come from the canonical parent and require current read/write
authorization. Model metadata is **caller-declared**, not trusted provenance.
Model spaces separate the complete name/revision pair, even at equal dimensions.
Digest mismatch for the exact authorized canonical revision is
**409 `embedding_input_mismatch`**.

One vector per canonical revision/model namespace is immutable. Identical
normalized float32 vector/digest deduplicates across HTTP keys; differing values
are **409 `embedding_conflict`** and require a new model revision for replacement.
Same key with a different normalized request conflicts. At most **8 model
versions total per canonical revision** are allowed; the ninth is
**422 `embedding_limit_exceeded`**, while existing duplicates remain allowed.
Projection creation, receipt, and audit are atomic.
The returned `{memory_id, revision, model, input_digest}` has no independent
embedding ID. Replay checks live parent and projection, not only the old receipt.
Store **only `{memory_id, revision}` in the idempotency result**, never plaintext
input digest, model names, or vectors. Rebuild the full response model/digest
from currently readable canonical input and its matching projection; retain
request HMACs and opaque anchors. If an administrator removes only the projection
while the parent is live, replay returns **409 `embedding_unavailable`** rather
than rebuilding it.

## Exact ranking and honest coverage

Recall adds `retrieval_mode` (`lexical` by default, `vector`, `hybrid`) and
`vector_query` (null by default, otherwise the declared model and 768 values).
Lexical rejects vector queries; vector-only requires empty text; hybrid requires
nonempty text. Both non-lexical modes require a vector. No ignored text or
fallback to another model/mode is allowed.

Scopes, `as_of`/`known_at`, budgets, and search-profile rules remain.
Freeze omitted `as_of`/`known_at` once before selection and coverage; all paths
use the same resolved times despite future boundaries. Explicit times are unchanged.
**`MATERIALIZED` currently authorized/time-eligible canonical candidates are
filtered before cosine distance or ranking**. No ANN/HNSW, approximate neighbor
expansion, or scope/tenant widening is added. Historical revision vectors can be
populated separately, but past reads never bypass current ACL/deletion checks.
Hybrid combines deterministic existing FTS and exact-vector ranks with
**RRF k=60**: `1/(60+lexical_rank) + 1/(60+vector_rank)`; absent paths contribute zero.
Lexical matches without vectors can participate, with incomplete coverage explicit.

Any missing eligible visible model projection sets `vector_incomplete: true`;
private/ineligible items never contribute to coverage/counts. Lexical defaults
keep it false. Japanese `lexical_incomplete` applies only to lexical/hybrid.
Either incomplete active path makes `retrieval_complete` false.
Empty selections with no candidates yield `index_incomplete` when active-index
coverage is missing, or `not_found` for empty authorized corpora.
Candidates that cannot fit retain `budget_exhausted`; nonempty results retain
null `empty_reason` and expose incomplete coverage.
Do not hide failure or index gaps as complete empty recall.

Defaults add `MemoryItem.retrieval: null`, `RecallResult.retrieval_mode: "lexical"`,
`embedding_model: null`, and `coverage.vector_incomplete: false`.
Non-null `MemoryItem.retrieval` contains `method` (`exact_cosine`/`rrf-60`),
`lexical_rank`, `vector_rank`, `vector_distance`, and `fusion_score`;
the rank/distance/fusion fields are nullable where not supplied by the path/method.
UUID breaks ties in actual computed distance/score, not a promise of
bitwise-identical arbitrary floating-point results or rankings on all CPUs.
These are additive fields:
**unchanged lexical semantics are not byte-for-byte HTTP response/schema compatibility**.
Ranking is **not confidence or truth**.
Whole compact JSON UTF-8 context-pack budgeting, reported assertions, and
null/uncalibrated confidence remain. The existing 5 s DB statement timeout is
not a performance SLO; quality/performance/untrusted-vector robustness remain unqualified.

## Schema, deletion, and packaging

**`008_pgvector.sql` advances to schema 8 and requires `vector` 0.8.6 in `public`**,
refusing an existing extension with a different version/schema and adding per-episode
and per-assertion-revision projections with forced RLS, runtime SELECT/INSERT
only, and canonical `ON DELETE CASCADE`. There is **no embedding backfill**.
Parent purge cascades vectors, digests, and declared model names with lexical
payloads; these are not new memory identities, provenance vertices, or deletion counts.
No standalone model registry retains this metadata.
No projection-only deletion endpoint or automatic embedding rebuild/provider exists.
Retained canonical source/idempotency anchors still prevent resurrection.
The Native response-drain and host/backup/WAL/full-erasure limits are unchanged.

Adopt the prebuilt upstream DB profile:
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`.
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6) is the verified
**2026-07-29** stable release, official tag commit
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`
([pinned changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)),
under the **PostgreSQL License**; retain its upstream license.
Both final native images retain `/usr/share/doc/pgvector/LICENSE`, verified
byte-identical to the pinned upstream license, SHA-256
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`.
Inspection of both final amd64/arm64 images verified PostgreSQL **18.6-1.pgdg12+2**,
native ELF, and `vector.control` **0.8.6**.
**PostgreSQL stays 18.6, but the upstream DB image/base digest changes**:
this is not the unchanged old library PostgreSQL image.
No new DB Dockerfile, source build, or host-APT workflow is part of the implemented profile.
An operator-managed alternative must provide the same extension version/schema;
no host-install procedure is supplied here.
Stop/drain **all old/new APIs, workers, adapters,
and hook launches**. v0.0.11 API/worker startup requires exact schema history
`[1, 2, 3, 4, 5, 6, 7, 8]` and extension `vector` 0.8.6 in `public`. Old schema-7 processes are not
rolling-compatible; downgrade is unsupported.
API, worker, and `migrate` validate extension version/schema even with schema 8
already recorded; previously applying the migration does not bypass the guard.
No new Python dependency is needed: raw parameter-bound vector casts avoid a
pgvector Python package; project-version metadata alone changes Python dependencies.

MCP retains four tools and both protocol eras; generated Recall arguments gain
inline query vectors, but no embedding input/upload tool is exposed.
The hook is **lexical-only and read-only**; event input cannot set vector fields.
It rejects non-lexical Native `retrieval_mode`, non-null `embedding_model`/item
`retrieval`, and true `coverage.vector_incomplete`, never silently downgrading them.
Observe/capture/jobs/workers never generate embeddings. Adapter startup matches
service **0.0.11**, API **v1**, schema **8**.
Capabilities add `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"`. The API stage is `m2-pgvector-retrieval`;
embedding input returns HTTP 200 and upload/replay returns HTTP 201.

## Evidence and consequences

**v0.0.11 passed local Apple Container and native Docker amd64/arm64 checks**:
each passed **345 tests, 1 existing warning**, Ruff, strict mypy (**17 source files**),
core-only/hook-only installation checks, and all non-root production smokes.
Test elapsed was **283.44 s local**, **404.40 s amd64**, **433.46 s arm64**.
Implementation [f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)
passed [CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403);
see [the evidence](../STATUS.md#v0011--schema-8). Verified artifact inspection
is separate; no source-build workflow is included. Passing checks cover normalization/digest/
immutability/model limits, deterministic exact/RRF math, ACL/time prefiltering,
coverage, deletion/replay, migration/version/role guards, and retained behaviors
on local Apple Container and native Docker amd64/arm64.
Implemented fixtures include DB norm/dimension/composite-FK/eight-model guards,
direct RLS visibility/denied updates, ACL revocation, and actual schema-7→8
ledger-failure rollback of DDL/extension followed by retry without backfill.
The production vector smoke uploads episode **and** assertion projections,
checks basis distances **[0, 1]** and RRF, then source purge and upload replay `404`.
These checks passed in all three environments; test elapsed is not a performance benchmark.
The [synthetic basis-vector example](../operations/README.md#synthetic-vector-example)
is not a production model, quality benchmark, or permission to export private data.

Historical v0.0.10/schema 7 implementation
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f),
[CI 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814),
passed 304 tests per environment: local **275.53 s**, native amd64 **467.75 s**,
native arm64 **434.40 s**. Final docs
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)
passed [CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760)
with 304 tests per native architecture, distinct from those implementation timings.
Neither run validates schema 8 or vector retrieval; see [historical evidence](../STATUS.md#v0010--schema-7).
