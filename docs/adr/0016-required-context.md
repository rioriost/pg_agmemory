# ADR 0016: Required-context recall

[日本語](0016-required-context-jp.md) | [Contract](../STATUS.md#required-context-recall) | [Operations](../operations/README.md#required-context-recall)

- Date: 2026-09-17
- Status: accepted and verified in bounded v0.0.16/schema 10, locally and on both native architectures
- Extends: [initial recall](0001-initial-slice.md), [revision selection](0002-assertion-revisions.md), [Japanese lexical recall](0007-japanese-fts.md), and [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/production/semantic-quality/performance/DR/full-erasure qualification

**Historical version notice:** this ADR records verified v0.0.16/schema 10.
[ADR 0017](0017-recall-filters.md) records exact structured recall filters for
v0.0.17 on the same schema and 25-resource surface, verified locally and on both native architectures.
Required refs must also satisfy those filters. Separate final v0.0.16 docs
CI 35226313891 is recorded in [historical evidence](../STATUS.md#v0016--schema-10).

## Decision and request boundary

Add optional `Recall.required_memory_refs`, default `[]`, with at most 16
`MemoryReference` entries: `memory_id` UUID and revision 1–1000, default **1,
not latest**. IDs are unique even across revisions, and count must fit `max_items`.
Nonempty refs require `retrieval_mode: "lexical"`; invalid combinations/counts/
duplicates/shapes return **422 `invalid_request`**. Both explicit and implicit
recall work; the existing implicit 2,000-byte cap remains.
Empty or omitted references preserve all three retrieval modes and baseline
ordering/result/pack semantics.

## Eligibility, order, and authority

Use the same currently authorized, materialized episode/assertion candidate
relation as normal recall, with requested `scope_ids` and frozen `as_of`/`known_at`.
Do not expand scope, override ownership/ACLs, bypass time eligibility, substitute
latest, or fall back to another revision. Existing bitemporal selection admits
one revision per object at a given `known_at`.
Eligible relation assertions retain their entity UUIDs and citation byte overhead;
entity objects themselves remain wrong-kind references.
Any missing/unreadable/purged/wrong-kind/out-of-request-scope/time-ineligible or
wrong-revision ref returns generic **404 `not_found` for the whole request**,
without partial context or missing-reference names.

Required refs bypass keyword matching and the ranking cutoff, not authorization.
Return required items first in **request order**, then normal lexical-ranked
optional items excluding required IDs. Both groups count within `max_items`;
optional overflow plus one retains `coverage.truncated`.
Both `simple-v1` and `ja-janome-0.5.0-v1` support canonical exact refs, including
missing Japanese projections. Existing `lexical_incomplete` metadata remains;
this does not repair the index.

A caller-selected reference is not policy authority, verified approval, trusted
instructions, or guaranteed current truth. Memory remains evidence, with existing
quoting, citations, and warning/`refresh_required` markers. No automatic essential-
constraint detection, persistent priority metadata, inference, write, idempotency,
provider call, or cache is introduced.

## Whole-prefix budget and transport

Budget the entire compact `ContextPack` JSON in UTF-8 bytes, not exact model tokens.
If any whole required item cannot fit, return **422 `budget_exhausted`**
(`ErrorBody.code`), never successful empty/partial/omitted-required context.
This can happen at budget 64 even when the empty envelope would not fit.
Optional items retain greedy whole-item omission and truncation metadata.
Without required refs, preserve **422 `budget_too_small`** for an empty envelope
that cannot fit, and the distinct successful-empty
`empty_reason: "budget_exhausted"` when optional candidates cannot fit.

Existing typed SDK `recall(Recall)` and MCP `memory_recall` accept the additive
field. The shared safe-code catalog adds `budget_exhausted`; SDK/MCP propagate it.
SDK recall stays read-only, with `outcome_unknown: false` and no automatic retry.
The hook rejects `required_memory_refs` as extra input; its internal `Recall`
defaults to `[]`, adding no host pinning.
Keep 25 Native/SDK resource methods and four MCP tools; add no route.

## Deployment and validation

Require exact service 0.0.16 / API v1 / schema 10. API/worker/readiness retain
exact history 1–10; v15→v16 is application-only with no migration, dependency
upgrade, or pinned-image change. Stop/drain old processes and use matching
components; older schemas still require the retained offline migrations.
Stage becomes `m2-required-context`. Capabilities add `required_context`:
`retrieval_modes: ["lexical"]`, `max_refs: 16`, `order: "request_order"`,
`budget_policy: "all_required_or_error"`.

**Full local and native v0.0.16 implementation qualification passed.**
Apple Container and both native Docker architectures each passed **566 tests,
1 existing warning**: **535 retained + 31 new**.
Local `./scripts/test-containers.sh` exited 0 in **298.15 s (4:58)**;
native amd64 took **601.73 s**, arm64 **565.34 s**.
[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)
passed on exact implementation
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57).
The new cases are **29 `test_required_context`**, **1 real hook rejection**,
and **1 SDK safe-code parameter**.
Ruff, strict mypy **19 source files + 1 strict SDK consumer**, genuine optional
installs, and all non-root production smokes passed in all three environments.
SQL ref limits, exact byte boundaries, scope/ACL/time/revision eligibility,
purge, missing Japanese projections, Native/SDK/MCP parity/error propagation,
relation/citation preservation, and real hook-CLI field rejection passed.
The production sequence passed after SDK and before cancellation: query bypass
with `max_items: 1` and truncation, explicit budget 64 returning `budget_exhausted`
with `outcome_unknown: false`, then source purge returning `object_count: 2`.
Only the project version changes; schema 10, dependencies, and artifacts remain
unchanged. Elapsed time is not a performance benchmark. These are implementation
results, not a later final-docs CI result. See [verified evidence](../STATUS.md#v0016--schema-10).
Verified v0.0.15 implementation and separate final-docs CI remain
[historical evidence](../STATUS.md#v0015--schema-10).
