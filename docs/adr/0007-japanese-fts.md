# ADR 0007: Bounded opt-in Japanese lexical FTS

[日本語](0007-japanese-fts-jp.md) | [Current contract](../STATUS.md#japanese-lexical-profile) | [Operations](../operations/README.md#lexical-profile-and-reindex-operations)

- Date: 2026-09-17
- Status: v0.0.7/schema 7 implemented; local and native Docker checks passed; M0/M1/M2/M3 incomplete
- Extends: lexical recall and derived indexing; preserves the graph, job,
  checkpoint, and effect contracts, including [ADR 0006](0006-durable-jobs.md)
- Naming: local directory/package/service `pg_agmemory`; public repository `rioriost/pg_agmemory`

**Historical scope:** this ADR records v0.0.7/schema 7 and its validation/lock
evidence, not the current dependency set or MCP results. The v0.0.8 local stdio
MCP milestone retains schema 7; see [ADR 0008](0008-local-mcp.md).
Its local/native CI validation is recorded in [STATUS](../STATUS.md#validation-evidence).

## Decision and scope

Add an explicit versioned Japanese lexical profile without changing the default
recall algorithm. PostgreSQL remains canonical and stores all memory projections.
This is a bounded lexical milestone, not vector/hybrid retrieval, measured
segmentation/recall quality, or full M2 acceptance.

`POST /v1/recall` defaults to `search_profile: "simple-v1"` and retains PostgreSQL
`simple`, `plainto_tsquery`, and `ts_rank_cd`. Callers opt in per request with
`"ja-janome-0.5.0-v1"`; the response echoes `search_profile`, and unsupported
profiles return `422`. Scope, current ACL/deletion checks, temporal selection,
evidence, item caps, and context byte limits remain unchanged.
The context `tokenizer_id: "utf8-bytes-v1"` is independent of search segmentation:
it still measures UTF-8 bytes, not Japanese or model tokens.

## Pinned segmentation, not semantic retrieval

Pin **Janome 0.5.0** and its bundled **mecab-ipadic-2.7.0-20070801**, including
Janome additions. Only matching Japanese-script runs in source/query text pass
through surface/wakati segmentation. ASCII identifiers and English remain intact
through this segmenter; ordinary PostgreSQL lexical processing follows.
There is no Unicode/width normalization, lemma/stemming, synonym matching, or
segmentation-quality claim. Han-script ranges also affect Chinese characters;
Chinese recall is not qualified.
Unchanged `simple-v1` remains token-based, not substring search; an embedded
`Gold` without a token boundary need not match standalone `Gold`.

No external model/provider is called. Packaged dictionary/statistical resources
are software dependencies, not a file-based memory index or user-memory store.
The profile pins lexical behavior rather than promising relevance or semantic
equivalence. Capabilities report both `search_profiles`,
`default_search_profile: "simple-v1"`, feature `japanese_fts`, and the pinned
tokenizer/dictionary with `normalization: "none"` and
`segmentation: "japanese-script-runs"`. Stage `m2-japanese-fts` is not an
acceptance-gate result.

## Runtime initialization boundary

Lazy-import Janome only for Japanese-script runs; API import and English-only
segmentation do not load it. Disable matcher input-prefix caching with
`max_cached_word_len=0`, retaining only packaged dictionary-resource caches,
not source text or token streams. Both test/runtime container builds sequentially
precompile **only static Janome package bytecode**, including dictionary modules.
This prepares software dependencies, not a memory index/cache or user input.

Engineering diagnosis, **not release/performance qualification**: an uncompiled
cold import ended with exit 137 at approximately **995,496 KiB** peak RSS.
After lazy loading/static precompilation, observed process peaks were
**64,012 KiB** for API import and **136,980 KiB** after tokenizer initialization.
These are bounded diagnostic observations, not deployment sizing or workload
memory limits. Cold host installations without precompilation retain that
initialization-risk caveat.

A fresh Linux subprocess regression guard requires initialization peak RSS
**below 256 MiB** and no Janome import for English-only operations.
The threshold is not a runtime memory cap or deployment-sizing guarantee.
Non-root production-image smoke also checks `東京都` → `東京` / `都` and emits
`Production Japanese tokenizer smoke passed` on success, alongside the existing
API HTTP and actual worker CLI smoke. None establishes recall quality or
deployment resource sizing.

## Canonical revisions and projection lifecycle

`memory.episode_lexical` stores the profile's derived `tsvector` for an episode
(revision 1). `memory.assertion_lexical` keys its projection by exact assertion
revision and profile. Both have forced RLS, same-scope canonical foreign keys
with `ON DELETE CASCADE` and GIN indexes. Runtime grants are `SELECT`/`INSERT`
only, not `UPDATE` or direct `DELETE`. Canonical parent purge removes projection
rows by FK cascade without child DELETE grants.
Episode content and assertion subject/predicate/exact-revision value are
segmented before `to_tsvector('simple', ...)`; the Japanese query uses segmented
text with `plainto_tsquery('simple', ...)` and `ts_rank_cd`. Simple search retains
the existing canonical vectors.
Japanese episode input retains the 65,536-character limit; 65,537 characters
are rejected, not truncated. The separate 256 KiB HTTP body cap still applies.

Observe and every assertion publication/revision write projections atomically
with canonical data. This includes typed relations and durable-job publication;
failed publication rolls back its projection too. Legacy synchronous normalized
JSON/HMAC, canonical IDs/system times, and HTTP receipts do not change.
Job-result assertion recorded/system time begins at publication, not enqueue.
Past `known_at` selects the exact historical revision and its projection/evidence,
not current values. Explain still defaults to revision 1.
Exact `known_at` boundary checks use server-returned assertion `recorded_at`,
not a host/VM wall-clock sample.

Projection rows are derived payload, not independent memories or provenance
vertices. Canonical purge cascades all affected lexical revisions under the same
tenant barrier before tombstones commit. Existing episode/entity/relation,
job-retry, checkpoint, and effect deletion rules remain; removing a job does not
erase independent published output or source episodes. Rebuild skips tombstones
and cannot resurrect purged content. Opaque anchors/receipts remain subject to
the existing retention policy; backups/WAL/delivered context are not certified erased.

## Missing projections are visible, not fallback success

For the Japanese profile, any authorized, requested-scope, time-eligible canonical
candidate missing its projection sets `coverage.lexical_incomplete: true` and
`coverage.retrieval_complete: false`. This check is independent of query
relevance and the item limit; it is not `jobs_pending` or synthesis coverage.
There is no silent simple-profile fallback, lazy repair, or automatic repair worker.

Available matches may still be returned with the incomplete flag. An empty query
continues to browse canonical candidates even if their projections are missing.
No query candidates plus missing projections gives `empty_reason: "index_incomplete"`;
if candidates exist but cannot fit the context, `budget_exhausted` retains
precedence. A nonempty result has null `empty_reason`. Otherwise ordinary
`not_found`/budget rules apply. Complete projection coverage does not prove
relevance, absence of a fact, or completeness of world knowledge.
Janome corrupt-dictionary logs are sanitized to `japanese_dictionary_error`
without input text. Library `SystemExit` is translated to tokenizer-unavailable:
API `503 dependency_unavailable` and existing bounded worker dependency retries,
without input echo. It is not a successful empty/incomplete retrieval.

## Offline migration and repair

Add `007_japanese_fts.sql` without modifying migrations 001-006. The migration
runner creates projections and backfills **all retained episodes and all
assertion revisions** in Python within the same transaction before recording
schema 7. Tombstones are skipped; canonical IDs, system times, and receipts
are preserved.
Failure even after actual backfill completes rolls back projection DDL/data and
the schema ledger together; a schema-6 upgrade remains at 6.

`pg-agmemory reindex-lexical` is an **all-tenant offline admin operation** on the
selected database, not a principal/scope-filtered worker command.
`--subject` is explicitly rejected; `--once` is also rejected as worker-only.
It uses **`PGAG_ADMIN_DATABASE_URL`**, requires exact
schema history `[1, 2, 3, 4, 5, 6, 7]`, and atomically replaces both projections
from canonical sources under the migration advisory lock (5-second lock timeout).
It emits only JSON `profile: "ja-janome-0.5.0-v1"` and integer
`episodes`/`assertion_revisions` counts, not text or tokens.
Partial replacement failure preserves the old schema-7 projections.
Administrative privileges must bypass forced
RLS; `row_security = off` does not grant bypass. Runtime credentials are not
rebuild credentials, and there is no rebuild HTTP endpoint.

For migration **and rebuild**, stop/drain **all APIs and workers**, including
automatic restarts, back up first, perform the offline operation, and start only
matching v7 processes. Both API and worker enforce exact schema/role checks.
No rolling coexistence or downgrade is supported; v0.0.1 lacks a startup schema
guard and must remain stopped. The advisory lock does not drain runtime traffic.
Maintenance duration/resource use and restore/DR safety are not qualified.

## Dependency licensing

Project code remains [MIT](../../LICENSE); dependency terms are separate.
Janome 0.5.0 is [Apache-2.0](https://github.com/mocobeta/janome/blob/0.5.0/LICENSE.txt).
Its bundled IPADIC dictionary/statistical data carries
[NAIST/ICOT copyright, distribution, and no-warranty notices](https://github.com/mocobeta/janome/blob/0.5.0/NOTICE.txt).
The pinned release also includes
[Janome dictionary additions](https://github.com/mocobeta/janome/blob/0.5.0/ipadic/Noun.proper.csv.patch).
Preserve upstream license/notice files when redistributing packages or images.
Do not describe all dependencies as MIT or claim no third-party dictionary data
is bundled.

The final lock retains the prior package-feed registry. All **36 packages'**
versions, dependency metadata, and artifact hashes were verified byte-for-byte
equivalent to the tested PyPI-resolved lock. Relative to v6, only Janome 0.5.0
was added and the project version became v0.0.7; no unrelated upgrades or
registry migration occurred. Native CI built this final retained-registry lock.

## Evidence and non-goals

For implementation commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473),
final v7 results were verified **2026-09-17 JST**:

| Environment | Tests | Test elapsed |
|---|---|---|
| Apple Container | 144 passed, 2 existing warnings | 206.54 s |
| Native Docker `linux/amd64` | 144 passed, 2 existing warnings | 386.32 s |
| Native Docker `linux/arm64` | 144 passed, 2 existing warnings | 331.59 s |

All three runs passed **Ruff, strict mypy (12 source files), and all three
non-root production smokes: Japanese tokenizer, API HTTP, and actual CLI worker
`--once` idle execution**. Actual logs for both native Docker jobs in
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)
confirm the exact SHA, counts, and checks. Timings are test-run observations,
not performance benchmarks. The suite covers profile/temporal/RLS behavior,
missing projections and budgets, exact input bounds, initialization guards,
cascade purge without child DELETE grants, migration/reindex rollback, CLI
restrictions, and prior graph/job/checkpoint/effect compatibility.
See [detailed evidence](../STATUS.md#validation-evidence).
The schema-6 decision and results remain historical in
[ADR 0006](0006-durable-jobs.md).

No automatic synthesis/enqueue/extraction, LLM/provider processing, embeddings/
pgvector, vector/hybrid retrieval, implicit graph expansion, AGE/SQL/PGQ, or
dynamic SQL/Cypher/labels is added. Entities remain immutable caller-reported
identities outside recall/explain; typed relation assertions remain lexical
candidates with `graph_used: false`. Graph traversal is still explicit canonical
SQL, not a graph projection. M0/M1/M2/M3, MVP/production, memory/segmentation
quality, performance, harness integration, full erasure, and backup/DR acceptance
remain incomplete.
