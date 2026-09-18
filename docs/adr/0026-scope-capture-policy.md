# ADR 0026: Administrator-controlled scope capture admission

[日本語](0026-scope-capture-policy-jp.md) | [Current status](../STATUS.md#scope-capture-policy) | [Operations](../operations/README.md#scope-capture-administration)

- Date: 2026-09-18
- Status: v0.0.26/schema 11 locally and natively qualified on implementation `c07630009ff4dcc34542e3ea80064d4f10c4d8b5`
- Extends: [ADR 0013: Scope access](0013-scope-access.md), [ADR 0022: Batch capture](0022-batch-capture.md)
- Boundary: prospective episode admission, not consent proof or automatic extraction

## Context

Scope ACLs decide who can read and write, but do not express whether new episode
capture is enabled or which source/consent labels and content sizes are admitted.
The bounded next M2 slice needs a durable administrator-controlled decision shared
by observe, single capture, and batch capture, including their replay paths.
It must not turn caller-supplied consent labels into verified consent, bypass
authorization, or automatically invoke a provider.

## Decision

Use **service 0.0.26 / API v1 / schema 11**, stage
`m2-scope-capture-policy`. Add only administrator CLI
`pg-agmemory scope-capture get|set`, using `PGAG_ADMIN_DATABASE_URL`.
No memory REST route, SDK resource, or MCP tool is added: Native/SDK remains
**31 memory resources**, MCP **four tools**, and the hook unchanged.
Capabilities describe the admin transport, tenant access-epoch CAS, four fields,
three admission paths, replay revalidation, and unconfigured legacy behavior.
`auto_synthesis` and global `model_inference.live_provider_qualified` remain false.

### Complete policy, not a patch

All four fields are required, unknown fields rejected:

| Field | Contract |
| --- | --- |
| `enabled` | Strict boolean |
| `source_namespaces` | `null` or at most 64 distinct normalized strings |
| `consent_references` | `null` or at most 64 distinct normalized strings |
| `max_content_bytes` | Strict integer 1–262144 |

List strings are trimmed, 1–256 characters, valid UTF-8 without C0 controls,
distinct after normalization, and sorted canonically. `null` means unrestricted;
`[]` denies all. Comparison is exact and case-sensitive, with no wildcard or
external consent lookup. The byte bound measures normalized `Observe.content`
encoded as UTF-8, not serialized JSON or a raw request. The existing
65,536-character content and 256 KiB Native body limits still apply.
The CLI policy file is bounded to 64 KiB.

No stored row means the full legacy policy:

```json
{
  "enabled": true,
  "source_namespaces": null,
  "consent_references": null,
  "max_content_bytes": 262144
}
```

`get` reports the effective policy, tenant `access_epoch`, last policy-change
`policy_access_epoch` (null without a row), `configured`, and `changed`.
`set` requires the current tenant epoch and a full policy. A semantically equivalent
set still checks CAS but does not create/update a row, advance an epoch, or emit
audit. In particular, default on an unconfigured scope is a no-op.
Restoring the full legacy policy does not delete a prior row; there is no reset
or delete command. At epoch 9223372036854775807 a correct-epoch no-op is allowed,
but a real change fails with `access_epoch_exhausted`.

### Authorization, transactions, and storage

The shared `admin.py` connection/error helper retains existing scope-access
import/error compatibility, exact schema/pgvector validation, privileged-role
checks, bounded connection/statement/lock waits, and the tenant **session**
advisory barrier. Hold it through transaction commit **and administrative output
delivery**, not merely the SQL transaction.
`pg_agmemory.scope_access.ScopeAccessError` remains import-compatible as an alias
of `pg_agmemory.admin.AdminError`; existing `MAX_EPOCH`/`Epoch` imports also remain.

A real change atomically updates `memory.scope_capture_policy`, advances the
tenant access epoch **once**, and inserts `memory_ops.capture_policy_event`
with complete before/after policies and `database_role`. Scope changes elsewhere
and membership mutations participate in the same tenant CAS.
The config table has FORCE RLS and runtime SELECT only for currently readable
scopes. The private audit has FORCE RLS, no runtime policy, and no runtime grant.
Runtime roles cannot administer either table.

Administrative errors expose only `{error: {code, outcome_unknown}}`, with static
argument errors and no raw SQL/DSN diagnostics. A failed or lost response may
follow commit. Read back with `get`, reconcile current policy/epoch and private
audit as needed, and use current CAS for any subsequent change; never blindly retry.

### Policy before replay

Current scope **read and write** authorization is checked before policy.
Missing or unauthorized scopes remain **404 `not_found`**.
Observe, capture, and batch capture check current policy before idempotency or
source-event deduplication. Thus a newly restrictive policy denies even a former
exact replay with **403 `capture_policy_denied`**, with no writes.
Invalid stored configuration fails closed with **503 `capture_policy_invalid`**;
invalid capture-content UTF-8 fails existing Pydantic validation with
**422 `invalid_request`**, not a new error code. OpenAPI declares typed 403.
Existing request-shape/size validation is unchanged. The SDK preserves both
policy codes; mutation 403 is `retryable: false` / `outcome_unknown: false`,
whereas mutation 503 conservatively remains `true` / `true`, even when policy
fails closed. No automatic retry is introduced.

The policy is prospective admission, not retroactive purge or cancel.
Existing content is still readable under ACL, and explicit remember/jobs can use
existing episodes. A real change invalidates old context/claim epochs;
already-claimed jobs are fenced but workers can recover under the new epoch.
Disabling capture is not a promise to stop all queued publication indefinitely.

## Alternatives and consequences

- A runtime-writable policy or REST administration endpoint would expand the
  authority surface; keep administrator credentials outside runtime/adapters.
- Per-principal allowlists or a separate policy epoch would split the existing
  tenant fence/CAS model; use scope config and the existing tenant access epoch.
- Checking policy only on first insert would allow stale exact replays after
  capture is disabled; revalidate before both deduplication paths instead.
- Deleting a row to restore defaults would blur effective policy and configured
  history; explicit full replacement retains row/audit history.
- Failing open on corrupt stored policy would silently remove restrictions;
  fail closed rather than inventing a permissive fallback.

This policy is **not** consent verification, a secret/PII detector, provider-egress
authorization, automatic capture/extraction/synthesis, or compaction.
Labels and administrative snapshots may themselves be sensitive. Protect them,
backups, and private audit using administrator operational controls.

## Upgrade and qualification

Migration `011_capture_policy.sql` follows all retained migrations. Keep pinned
PostgreSQL **18.6** and **`vector` 0.8.6 in `public`**; require exact ledger **1–11**.
Back up and stop/drain old/new APIs, schema-10 writers, workers, replicas, adapters,
callers, and admin activity before migrating. Restart only matching schema-11
service/adapters. Schema-10 binaries reject schema 11; reverting code alone is
not DB rollback. There is no downgrade command. Use a forward fix or isolated
full-backup restore with matching old components and reconciliation of current
ACL/deletion/policy decisions; see the [runbook](../operations/README.md#schema-11-scope-capture-policy-upgrade).

**Local and exact-SHA native qualification passed.**
Implementation `c07630009ff4dcc34542e3ea80064d4f10c4d8b5` passed Apple Container:
1083 tests, 5 opt-in live skips, 1 existing warning / 499.15 s, plus all
lint/type/installation/non-root smoke checks. No live model or cloud resources
were used.
[Native CI 35308638587](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587)
passed on that exact implementation: Docker Linux **amd64 558.30 s** and
**arm64 784.48 s**, each **1083 passed / 5 skipped / 1 warning**, with all
checks and non-root smokes including capture policy. No live calls occurred.
See [v26 evidence](../STATUS.md#v0026--schema-11) for the job links.
A later docs-only publication commit is not the tested implementation SHA.
Historical v25 CA/CI and v24 live
provider results do not qualify schema 11. This ADR claims no M2/MVP/production,
performance, retrieval-quality, DR, or full-erasure acceptance.

The next bounded resume point after v26 is **M2 automatic synthesis, embedding
integration, and compaction**, plus remaining **quality/task-replay gates**.
Separately design an opt-in extraction/publication workflow over approved episodes:
explicit consent/provider-egress approval, untrusted proposal review, evidence
validation, stable intent/replay handling, and EN/JA quality/cost evaluation before
automation. Compaction and task-replay acceptance need their own bounded evidence.
These capabilities remain unimplemented; capture admission neither enables them
nor authorizes provider egress.
