# ADR 0020: Assertion metadata history

[日本語](0020-assertion-history-jp.md) | [Contract](../STATUS.md#assertion-metadata-history) | [Operations](../operations/README.md#assertion-metadata-history)

- Date: 2026-09-18
- Status: accepted and verified in bounded v0.0.20/schema 10, locally and on both native architectures
- Extends: [assertion revisions](0002-assertion-revisions.md), [relational graph](0005-relational-graph.md), and [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/performance/production/DR qualification

## Decision and request boundary

Add Native JWT authenticated read-only `POST /v1/assertions/history`, requiring
current read access but no `Idempotency-Key` or write permission.
Closed `AssertionHistory` accepts required UUID `memory_id`, strict integer
`max_items` (1–100, default 20), and nullable strict integer `before_revision`
(1–1001, default null). Invalid UUIDs, bounds, booleans/floats for integers,
or extra fields give **422 `invalid_request`**.
Only currently readable ordinary assertions and canonical relation assertions
are eligible. Missing, private, or wrong-kind objects give generic **404 `not_found`**.
No identity/scope override, `as_of`, `known_at`, historical ACL, offset, watch,
or full-value selector is added.

## Exclusive ordinal pages under current authorization

Order by revision descending with exclusive `revision < before_revision`.
Omitted/null starts newest; `before_revision: 1` gives an empty page.
`1001` includes a current head up to the existing maximum revision 1000,
without changing the write limit.
Fetch `max_items + 1`, return at most `max_items`, and emit `next_before_revision`
only on overflow from the **last returned ordinal**, not the lookahead row.
Otherwise it is null; null ends continuation, while a new null request restarts.

The position is not authority, snapshot, receipt, cache, or retention object.
Every page applies current ACL/source/deletion guards and the existing tenant
response-delivery/drain barrier, not historical permissions.
New revisions between pages require restarting without a position.
A former head's `known_until` can close; there is no cross-page snapshot.
`current_revision` is a point-in-time head, not a CAS reservation or proof that
an uncertain mutation committed. Reconcile with that mutation's original
idempotency key/body rather than automatically replacing its expected revision/key.

Missing/gapped selected metadata or no readable evidence fails the **whole page**
with **409 `assertion_invalidated`**. Missing canonical relation endpoints give
**409 `relation_invalidated`**. No partial success, silent skip, or fallback.
Contiguity covers the bounded fetched window including the lookahead ordinal;
evidence and relation validation covers returned page items.

## Metadata, not content-free output

Exactly **200 `AssertionHistoryPage`** returns `memory_id`, `scope_id`, `subject`,
`predicate`, `current_revision`, at most 100 `revisions`, nullable
`next_before_revision`, and `consistency` (`access_epoch`, `deletion_epoch`).
Revision metadata contains `revision`, nullable `valid_from`/`valid_to`,
`recorded_at` (lower system-time bound), nullable `known_until` (upper bound),
nullable `correction_reason`, `epistemic_status: "reported"`, up to 32 exact
episode `evidence_refs` at revision 1, and `relation` containing `source_entity`/
`target_entity` UUIDs or null.
Items use `AssertionRevisionMetadata`; SDK parsing also enforces at most 100
revisions and 1–32 evidence references.

Use bounded SQL metadata queries without fetching full values or evidence quotes.
Subject, predicate, and correction reason remain human text; **not fetching
values/quotes does not make the response content-free**. Treat it as evidence,
not trusted instructions or current truth, and do not disclose private metadata.
Use unchanged `Explain` with exact `memory_id` and revision for full content
under fresh checks. Omitted revision remains **1, not latest**.
Recall time semantics, evidence identity, and purge behavior are unchanged.

## SDK and deployment

Async `get_assertion_history(AssertionHistory) -> AssertionHistoryPage` uses
required internal `mutation=False`, call-time request validation, normal
**256 KiB request / 2 MiB response** bounds, sanitized read-only
`outcome_unknown: false`, and no automatic pagination/retry.
SDK recognizes `assertion_invalidated` and retains `relation_invalidated`;
MCP/hook safe-error allowlists do not change.
No mutation, cache, provider call, watch, or retention object is added.
There are 28 Native/SDK resource methods, four unchanged MCP tools, and an
unchanged closed hook, with no history tool/field or SDK admin/worker function.

Require exact service 0.0.20 / API v1 / schema 10 across adapters.
Stage `m2-assertion-history` adds `assertion_history` metadata:
`endpoint: "/v1/assertions/history"`, `order: "revision_desc"`,
`pagination: "exclusive_revision"`, `max_items: 100`, `includes_values: false`,
`includes_evidence_quotes: false`.
v19→v20 is application-only: no SQL migration, dependency/provider/artifact-pin
change beyond project version. History 1–10 and schema 10 remain.
Stop/drain old components and start matching versions; no mixed-version promise.

## Validation boundary

**Final local and native implementation validation passed.** The full Apple
Container `./scripts/test-containers.sh` completed with **695 passed,
1 existing warning, 359.74 s**: **663 retained + 32 new cases**
(16 contract, 7 revision-history, 1 graph missing-endpoint, 7 SDK—6 mock + 1 real—
and 1 `assertion_invalidated` case added to the existing safe-code parameterization).
Implementation
[`6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97`](https://github.com/rioriost/pg_agmemory/commit/6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97)
passed [CI 35247519977](https://github.com/rioriost/pg_agmemory/actions/runs/35247519977)
on that exact SHA: native amd64 **695 passed, 527.99 s**; arm64
**695 passed, 615.07 s**.
Ruff, mypy **19 source files + 1 strict SDK consumer**, core/hook/sdk-only
installations, and all previous production smokes plus assertion history passed
in all three environments.
Existing exact-1000-revision coverage was extended to **10 explicit pages of 100**
for both ordinary assertions and canonical relation assertions.
Existing temporal relation and purge cases were also extended and passed.
Elapsed time is not a benchmark; no MVP, performance, memory-quality, production,
or DR qualification is claimed. These are implementation results, not a
final-docs CI result. See [verified evidence](../STATUS.md#v0020--schema-10).
V19 implementation and separate final-docs CI remain
[historical evidence](../STATUS.md#v0019--schema-10), not v20 qualification.
