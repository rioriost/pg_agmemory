# ADR 0017: Exact structured recall filters

[日本語](0017-recall-filters-jp.md) | [Contract](../STATUS.md#exact-structured-recall-filters) | [Operations](../operations/README.md#exact-structured-recall-filters)

- Date: 2026-09-17
- Status: accepted and verified in bounded v0.0.17/schema 10, locally and on both native architectures
- Extends: [initial recall](0001-initial-slice.md), [revision selection](0002-assertion-revisions.md), [pgvector retrieval](0011-pgvector-retrieval.md), and [required context](0016-required-context.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/production/semantic-quality/performance/DR/full-erasure qualification

**Historical version notice:** this ADR records verified v0.0.17/schema 10.
[ADR 0018](0018-checkpoint-head.md) records read-only exact-branch head lookup
in v0.0.18 on schema 10, with 26 Native/SDK resources, verified locally and on both native architectures.
Separate final v0.0.17 docs CI 35232139680 is recorded in
[historical evidence](../STATUS.md#v0017--schema-10), distinct from implementation CI.

## Decision and request boundary

Add `Recall.filters: RecallFilters | None = None` to the existing Native request.
The shared typed nested model is closed to unknown fields. Its only fields,
all defaulting to null, are:

| Field | Contract |
|---|---|
| `kind` | `"episode"`, `"assertion"`, or null |
| `subject` | `ShortText` or null; 1–256 characters after existing whitespace trim |
| `predicate` | String matching `^[a-z][a-z0-9_]{0,63}$` or null |

Episode kind with non-null subject or predicate is invalid. Invalid shapes,
values, combinations, and unknown fields produce **422 `invalid_request`**.
Do not introduce field aliases, arbitrary SQL, inferred selectors, ranges, or array-valued filters.
Omission, null, `{}`, and all-null filters preserve baseline results in all three
retrieval modes.

## Exact selection and ranking

AND all non-null fields. Subject/predicate imply assertion candidates, including
relations; assertion kind alone includes relations, while episode kind excludes
all assertions. Compare subject/predicate with exact case-sensitive equality in
`C` collation after normal `Contract` string trimming. Do not perform substring,
FTS, Unicode normalization, fuzzy matching, or entity resolution.
Empty-query lexical recall browses filtered candidates; normal nonempty lexical
queries still require matching. Filters do not grant query bypass.

Apply filters inside the shared materialized candidates **before** lexical,
vector, and hybrid ranking, coverage, and required-reference eligibility,
not as top-K post-processing. Ranking calculations, including vector and RRF,
use the filtered eligible universe. Preserve frozen `as_of`/`known_at`,
requested scopes, current RLS, evidence visibility/integrity, and deletion gates.
Lexical/vector incompleteness reflects that filtered universe; it is not based
on excluded objects, query relevance, or the final item limit.
`coverage.jobs_pending` deliberately remains a scope-level signal, not a
structured match against job payloads.

## Required context, errors, and authority

Required refs stay lexical-only, default to revision **1, not latest**, and
retain their separate keyword bypass and request-order prefix.
They must also satisfy filters; one mismatch produces generic whole-request
**404 `not_found`**, without IDs or partial context. There is no revision, time,
scope, or filter bypass.
Keep existing compact `ContextPack` UTF-8 byte accounting, implicit 2,000-byte
cap, optional omission/truncation, required **422 `budget_exhausted`**,
`budget_too_small`, and successful-empty reasons.
Filters are caller-selected constraints, not policy authority, verified content,
trusted instructions, or automatically inferred intent.

## Surfaces and deployment

Expose the additive field through existing typed SDK `recall(Recall)` and MCP
`memory_recall`: still 25 Native/SDK resource methods and four MCP tools.
Add no route or safe error code. SDK recall remains read-only with sanitized
call-time errors, `outcome_unknown: false`, and no automatic retry.
Hook input rejects `filters`; its internal `Recall.filters` defaults to `None`,
preserving trusted startup boundaries rather than adding per-event overrides.

Require exact service 0.0.17 / API v1 / schema 10 across adapters.
v16→v17 is application-only: no SQL migration, dependency/provider/index change,
persisted priority, or cache. API/worker/readiness retain exact history 1–10 and
existing role/pgvector checks. Stop/drain old processes and use matching components;
older schemas still require retained offline migrations.
Stage is `m2-structured-recall`. Capabilities add `recall_filters`:
`fields: ["kind", "subject", "predicate"]`, `match: "exact"`, `combination: "and"`,
`retrieval_modes: ["lexical", "vector", "hybrid"]`.

## Validation boundary

**Full local and native v0.0.17 implementation qualification passed.**
Apple Container and both native Docker architectures each passed **604 tests,
1 existing warning**: **566 retained + 38 new**, comprising **37 cases in
`tests/test_recall_filters.py` + 1 real hook-CLI filter rejection**.
Local `./scripts/test-containers.sh` exited 0 in **321.56 s (5:21)**;
native amd64 took **638.66 s**, arm64 **544.33 s**.
[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)
passed on exact implementation
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894).
Ruff, strict mypy **19 source files + 1 strict SDK consumer**, genuine
core/hook/sdk-only installs, and all non-root production smokes passed in all three environments.
This includes all three modes' pre-ranking/coverage filtering, required-reference
mismatch, the five later-added invalid-shape/kind-only-overflow cases,
Unicode composed/decomposed exactness without normalization, OpenAPI assertions,
and explicit `RecallFilters` consumer construction.
The shared `Predicate` alias preserves `Remember`/`CapturedMemory` field order and semantics.
The new SDK smoke passed observe/remember, exact three-field selection with
`max_items: 1` and no truncation, required source/filter conflict
(`404 not_found`, `outcome_unknown: false`), source purge returning
`object_count: 2`, and an empty filtered read.
The project version advances to 0.0.17; schema 10, dependency versions, and artifact pins are unchanged.
Elapsed time is not a performance benchmark. These are implementation results,
not a later final-docs CI result. See [verified evidence](../STATUS.md#v0017--schema-10).
Verified v16 implementation and separate final-docs CI remain
[historical evidence](../STATUS.md#v0016--schema-10), not v17 qualification.
