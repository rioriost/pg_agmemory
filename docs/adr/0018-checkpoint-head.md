# ADR 0018: Checkpoint-head lookup

[日本語](0018-checkpoint-head-jp.md) | [Contract](../STATUS.md#checkpoint-head-lookup) | [Operations](../operations/README.md#checkpoint-head-lookup)

- Date: 2026-09-17
- Status: accepted and verified in bounded v0.0.18/schema 10, locally and on both native architectures
- Extends: [checkpoints](0003-checkpoints.md), [tool-effect ledger](0004-tool-effects.md), and [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not M0–M3/MVP/harness/compaction/general-recovery/production/DR qualification

**Historical version notice:** this ADR records verified v0.0.18/schema 10.
[ADR 0019](0019-job-query.md) records caller-owned job query/pagination in
v0.0.19 on schema 10, with 27 Native/SDK resources, verified locally and on both native architectures.
Separate final v18 docs CI 35237907862 is recorded in
[historical evidence](../STATUS.md#v0018--schema-10), distinct from implementation CI.

## Decision and request boundary

Add authenticated read-only `POST /v1/checkpoints/head`, requiring current scope
read access but no `Idempotency-Key`. The closed typed `CheckpointBranch` body
has exactly three required UUID fields: `scope_id`, `run_id`, and `branch_id`.
Missing/invalid UUIDs or extra fields give **422 `invalid_request`**.
No identity override, `expected_head`, harness selector, `as_of`, cross-branch
latest selection, or history listing is accepted.

Select only the exact currently readable tenant/scope/run/branch.
Use SQL `SELECT`, without `FOR UPDATE`, under the existing API tenant
session-lock/drain barrier. Do not create a run/branch, update state, audit,
or write an idempotency receipt.
Missing/private/wrong-scope/cross-tenant or non-invalidated empty branches give
generic **404 `not_found`**, without requested IDs/content or empty success.

## Invalidation and complete envelope

Source `forget` marks affected branches `invalidated=true` and deletes canonical
checkpoint/reference payloads while retaining opaque `head_id` and `sequence`,
`memory.object` anchors, and tombstones. Lookup checks invalidation first:
a readable invalidated branch gives **409 `checkpoint_invalidated`**, never the
retained head ID. Access revocation hides the branch with 404.
Never fall back to an earlier ancestor, sibling, or default branch.
Reuse the existing load/envelope checks for HMAC, typed state, references,
current authorization, and live run-wide effects. Run `effects_invalidated`
and existing integrity failures retain their existing rejection behavior;
an invisible head remains 404. Loaded `scope_id`, `run_id`, `branch_id`, and
`sequence` must equal the selected branch, or fail with 409.

Success is exactly **200 `CheckpointEnvelope`**: saved state/HMAC/references,
`saved_access_epoch`, `saved_deletion_epoch`, `current_access_epoch`,
`current_deletion_epoch`, current `tool_effects`, `requires_reconciliation`,
`untracked_effects`, `resume_allowed`, and `automatic_reexecution: false`.
No stale-permission or effect-check bypass is added.
Saved epochs and `resume_allowed: true` are not host approval or provider receipts.
Read does not restore, transition effects, execute code, or grant approval.

## Point-in-time lookup and mutation uncertainty

Stable scope/run/branch IDs locate the current head when its checkpoint ID is
lost. Latest means the point-in-time pointer, not a watch, reservation, successor
guarantee, or cross-branch search.
`CreateCheckpoint.expected_head` remains mandatory caller UUID-or-null CAS.
Writes can advance after the read; **409 `checkpoint_head_conflict`** requires
caller reconsideration, not automatic retry with a refreshed head or new key.
Head lookup does not prove the caller's uncertain mutation committed: another
writer may have advanced the pointer. Retry the original mutation key/body for
its original receipt under current replay guards, then inspect the head.
GET by ID still permits a live surviving ancestor under existing checks and is
not latest. Restored forks have independent target heads; read never restores.

## SDK and deployment

Add async `get_checkpoint_head(CheckpointBranch) -> CheckpointEnvelope`, using
SDK `_post` with `mutation=False`, call-time model validation, normal
**256 KiB request / 2 MiB response** bounds, sanitized errors, and
`outcome_unknown: false` for read-only transport failures. There is no automatic retry.
Existing safe `checkpoint_invalidated` is reused, not a new error code.
There are 26 Native/SDK resource methods and four unchanged MCP tools;
no checkpoint tool or hook field is added.

Require exact service 0.0.18 / API v1 / schema 10 across adapters.
v17→v18 is application-only, with no SQL migration or dependency/provider/
artifact-pin change. API/worker/readiness retain exact history 1–10 and existing
role/pgvector checks. Stop/drain old components and use matching versions;
older schemas still require retained offline migrations.
Stage is `m2-checkpoint-head`. Capabilities add `checkpoint_head`:
`endpoint: "/v1/checkpoints/head"`, `read_only: true`,
`branch_identity: ["scope_id", "run_id", "branch_id"]`, `fallback_to_ancestor: false`.

## Validation boundary

**Final local and native implementation validation passed.** Apple Container `./scripts/test-containers.sh`
exited **0** with **630 passed, 1 existing warning, 369.39 s (6:09)**:
**604 retained + 26 new tests** (8 `tests/test_contract.py`, 11
`tests/test_checkpoints.py`, 7 `tests/test_sdk.py`), plus extended existing coverage.
Ruff, strict mypy **19 source files + 1 SDK consumer**, genuine core/hook/sdk-only
installs, and all non-root production smokes passed in all three environments.
This includes actual read-only SQL/scope-read-only head access, complete SDK
error fixtures, invisible tombstoned head 404 despite live ancestor GET 200,
create/head response-loss distinctions, large responses, current effect-ledger/
head/fork parity, and 26-route coverage. No object TTL feature is added.
The new real SDK smoke passed in all three environments: source + two checkpoints → head sequence 2 /
historical GET sequence 1 → source purge `object_count: 3` →
head `409 checkpoint_invalidated`, retaining opaque pointers without returning the ID.
Implementation
[`4accd38408a8384b4376f6250d953bb2fa480ec8`](https://github.com/rioriost/pg_agmemory/commit/4accd38408a8384b4376f6250d953bb2fa480ec8)
passed [CI 35235315016](https://github.com/rioriost/pg_agmemory/actions/runs/35235315016)
on both native Docker architectures. Actual logs verified **630 passed, 1 warning**
each: **663.91 s amd64 / 544.14 s arm64**, all checks/installs, and all production smokes.
These are implementation results, not a later final-docs CI result.
Elapsed time is not a benchmark. See [verified evidence](../STATUS.md#v0018--schema-10).
Verified v17 implementation and separate final-docs CI remain
[historical evidence](../STATUS.md#v0017--schema-10), not v18 qualification.
This lookup is not a harness adapter, compaction mechanism, or general recovery guarantee.
