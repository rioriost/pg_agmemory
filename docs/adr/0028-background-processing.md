# ADR 0028: Default-deny background processing and working-memory compaction

- Status: implemented; **M2 acceptance incomplete**
- Milestone: v0.0.27 / schema 13; capability stage `m2-background-processing`
- Date: 2026-09-18
- [日本語](0028-background-processing-jp.md)

## Context and qualification boundary

**Acceptance-plan amendment, 2026-09-19:** [plan sections 1.3 and 17–18](../PG_AGMEMORY_IMPLEMENTATION_PLAN.md)
supersede this ADR's historical human-quality/real-task milestone conditions.
The schema-13 runtime, default-deny policies, untrusted-output treatment and
measured evidence below are unchanged. M2 remains incomplete because of core
recovery/accounting/resource work, not missing human ratings.

[ADR 0026](0026-scope-capture-policy.md) qualified capture admission, not
automatic model execution. [ADR 0027](0027-typed-extraction.md) added a typed,
untrusted extraction dependency, not publication authority. This milestone adds
opt-in durable extraction/embedding and bounded working-memory compaction.
It does **not** complete M2, the MVP, human-quality gates, or performance gates.

This is the schema-13 contract, not a reuse of v26/schema-11 qualification.
Implementation `9458034a47b6f7c9901e569a32f198f56369fbf7` passed both native
architectures in [CI 35322238611](https://github.com/rioriost/pg_agmemory/actions/runs/35322238611):
1,487 passed / 8 optional live skips each, including all production smokes and
the packaged synthetic M2 lifecycle. The earlier complete-quote guidance and
actual SIGKILL recovery tests at
`e4f5d76ad2a4919349054165ce531b92fa650818` passed 154 targeted local tests and a
three-call real local-model extract/embed/compact/restore lifecycle.

The separately frozen `101993a` implementation measured 600 held-out synthetic
questions/50 groups and 10,000 actual generated authorization cases.
See [EVALUATION](../EVALUATION.md) for exact run/model digests, failed attempts,
public QA budget corrections and results. These software and synthetic-data
measurements do **not** establish human assertion precision, human important-claim
fidelity, 20 executed real tasks, backup recovery or overall M2 qualification.

## Decision: three separate authorities

1. Current scope read/write authorization remains mandatory.
2. Capture policy controls admission of episodes; it is not permission to send
   their content to a model.
3. A separate administrator-managed **scope synthesis policy**, plus a matching
   local worker profile, permits particular background recipes. Missing policy
   means **deny**. Existing explicit `remember`, structured jobs, and explicit
   provider adapters do not become automatic model processing.

The worker accepts only `local_http`, restricted by the provider adapter to a
loopback endpoint. Remote OpenAI-compatible and Azure profiles cannot be selected
for automatic processing. No remote fallback is added. Operators must still
control the local server, its model installation, logging, and any downstream
connections; a loopback URL is not proof of a server's internal behavior.

Consent references are exact labels checked against policy, not verified consent,
secret/PII detection, or a general authorization to export data.

## Administrative policy and pinned profile

`pg-agmemory scope-synthesis get|set` uses `PGAG_ADMIN_DATABASE_URL`, not HTTP
credentials or the runtime database role. It shares the privileged connection,
schema/pgvector/role checks and tenant session barrier used by scope administration.
The barrier extends through commit and delivery of the command result.

The closed policy object has these defaults and bounds:

| Field | Default | Contract |
| --- | --- | --- |
| `enabled` | `false` | Strict boolean |
| `profile_digest` | `null` | SHA-256 digest; required when enabled |
| `consent_references` | `[]` | At most 64 distinct normalized labels; nonempty when enabled |
| `publish_predicates` | `[]` | At most 32 distinct predicate names |
| `kinds` | `[]` | Distinct subset of `extract`, `embed`, `compact` |
| `max_pending_jobs` | `20` | Strict integer 1–100 |
| `max_input_bytes` | `16384` | Strict integer 1–262144 |
| `max_output_tokens` | `1024` | Strict integer 1–4096 |
| `max_calls` | `100` | Strict integer 1–10000 |

Lists are normalized, sorted, and reject duplicates, invalid UTF-8 and C0
controls. Consent labels are trimmed strings of 1–256 characters; predicate names
follow the existing predicate grammar. Comparisons are case-sensitive.
Unlike capture policy, synthesis fields may be omitted: they receive defaults.
`set` replaces the policy; it does **not** merge omitted fields with the stored
policy. Even `enabled=true` with `kinds=[]` admits no jobs.

`set` requires a current tenant `expected_access_epoch` CAS. A real change
atomically advances that epoch once, stores the policy epoch and a private
before/after administrative audit with database-role provenance. Equivalent
policies are no-ops, but still require the correct CAS. Setting defaults when no
row exists creates neither row nor audit event; restoring defaults after a prior
change preserves the row. An exhausted epoch permits a no-op, not a real change.
Runtime access is readable-scope `SELECT` only on the FORCE-RLS policy table;
the administrative audit is private.

The profile digest binds all normalized `ProviderSettings`, recipe versions,
`local-worker-v1`, and SHA-256 hashes of the **actual extraction and summary
system prompts**. Prompt version labels alone are not sufficient. Credential
environment-variable names, not credential values, are configuration.
Text processing requires an explicitly configured
`max_output_tokens` no greater than the policy cap; embedding requires an
embedding model. Profile changes require deliberate administrator reauthorization.
Declared model revisions are not independent verification of a local server's
loaded model weights.

Safe operator workflow, using approved local files and environment-provided DSNs:

```bash
# Computes configuration identity; it does not perform model inference.
pg-agmemory worker --provider-config local-worker.json --print-profile-digest

# PGAG_ADMIN_DATABASE_URL is supplied by the operator's secret environment.
pg-agmemory scope-synthesis get \
  --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID"

# Review a replacement policy containing the approved profile digest.
# EXPECTED_ACCESS_EPOCH comes from the current administrative read.
pg-agmemory scope-synthesis set \
  --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID" \
  --expected-access-epoch "$EXPECTED_ACCESS_EPOCH" \
  --policy-file approved-synthesis-policy.json

# Uses PGAG_DATABASE_URL and the provisioned worker subject, not the admin role.
pg-agmemory worker --subject "$WORKER_SUBJECT" \
  --provider-config local-worker.json --once
```

The policy file is bounded to 64 KiB; the provider configuration to 32 KiB.
Disabling/restoring default-deny uses a replacement policy file containing `{}`,
with a fresh CAS. Safe administrative failures use
`{"error":{"code":...,"outcome_unknown":...}}`; after a mutation with unknown
outcome, read back the policy/epoch rather than blindly repeating a stale CAS.

## Admission and public interfaces

| Interface | Result / SDK method |
| --- | --- |
| `POST /v1/processing` | 202 job receipt; `process_memory` |
| `GET /v1/jobs/{job_id}/candidates` | Untrusted candidate review page; `get_extraction_candidates` |
| `POST /v1/jobs/{job_id}/candidates/{ordinal}/adopt` | 201 caller-declared reported assertion; `adopt_candidate` |
| `POST /v1/working/events` | 201 event receipt; `append_working_event` |
| `POST /v1/working/events/query` | Event page; `query_working_events` |
| `POST /v1/working/compact` | 202 job receipt; `compact_working` |
| `GET /v1/working/snapshots/{checkpoint_id}` | Typed snapshot; `get_working_snapshot` |

Mutations use the existing idempotency-key contract. Querying events is read-only
despite using POST. Processing takes
`{scope_id, source: {memory_id, revision}, kind}`: extraction accepts an episode;
embedding accepts an episode or assertion.
The source must be live, in the authorized scope, and at the exact revision.
Assertion/checkpoint dependencies are revalidated, including episode provenance.
`ProcessMemory` and `CompactWorking` additionally accept optional UUID `retry_of`
for the explicit, known-outcome retry contract below.
Capabilities retain `auto_synthesis=false`: the separate `background_processing`
metadata advertises policy-gated opt-in, not a global automatic-processing default,
and explicitly reports `human_quality_qualified=false`.

`Observe.auto_extract` and `Observe.auto_embed` are strict booleans, both false by
default. Requested jobs and the episode are admitted in one transaction; a failed
admission does not partially store them. The result adds optional
`synthesis_job_id` / `embedding_job_id`. False flags are omitted from canonical
capture payloads to preserve existing request identity. Atomic structured capture
and batch capture reject these auto flags; they retain their explicit structured
job semantics.

Embedding jobs reuse the existing canonical input, configured-model and vector
validation and publication path. `auto_embed` targets the observed episode;
it does not automatically fan out to assertions produced later by extraction.
Embedding an assertion requires an explicit revision-bound source reference,
with its liveness and current authorization rechecked.

Processing checks current scope authority, enabled policy/kind, consent labels,
current capture admission, source liveness, references and input bounds before
explicit processing replay/deduplication. Invalid stored synthesis/capture policy
fails closed with 503 `synthesis_policy_invalid` / `capture_policy_invalid`;
denied synthesis returns 403 `synthesis_policy_denied`. Missing or inaccessible
objects use the existing 404 boundary. Input is bounded both by policy UTF-8 bytes
and the existing 65,536-character limit.

An exact observe replay with either auto flag enabled revalidates current
synthesis policy and source eligibility for each requested kind **before**
returning its old receipt. Revocation therefore denies an opt-in replay; the
receipt cannot bypass current policy. Flags-off observe keeps capture-only
replay validation for compatibility. Neither replay creates another model call.

Semantic processing identity binds tenant/scope/principal, recipe, profile,
exact source references and applicable compaction input identity. It deliberately
**excludes `policy_epoch`**, while the job payload still captures that epoch for
egress/publication fencing. A new idempotency key or a policy-epoch/budget change
alone cannot turn an existing unknown call into new work. This is not a universal
once-per-source guarantee across genuinely different semantic operations.

## Durable one-call budget and publication fence

1. A matching-profile worker claims a job for its provisioned principal. A worker
   without a provider profile continues structured jobs and skips model jobs.
2. Preparation checks the live lease, current access/deletion epochs, policy
   epoch, profile, sources, recipe and canonical input digest.
3. Before network I/O, a transaction durably reserves a `model_call` row keyed by
   job. It records the lease token, policy epoch, profile digest, tenant-HMAC input
   fingerprint and bounded input/output metadata, initially `outcome=unknown`,
   `billing_unknown=true`.
4. The database session/barrier is released before the model call. Model-job
   leases are 180 seconds; provider deadlines are at most 120 seconds, with a
   125-second worker call wrapper. A heartbeat runs every 20 seconds in a short,
   separate transaction, rechecking lease and epochs. Heartbeat failure ends the
   wait and cancels the local call task; it cannot retract already dispatched
   provider work. Neither the provider task nor its wait owns a DB connection
   or holds a session lock across network I/O.
5. Publication uses a new transaction and repeats current authorization,
   epoch/lease/policy/profile/source checks. The configured model and returned
   input digest must match. Valid publication, the call result and successful job
   state are committed atomically.

Database guards also enforce the reservation's current policy, lease and limits.
A dedicated transaction-level advisory lock serializes scope call reservations;
policy is read with ordinary `SELECT`, not an `UPDATE` grant or update-row lock.
`max_pending_jobs` bounds pending/running scope jobs; `max_calls` counts persisted
reservations across job owners for the scope's **policy epoch**, including
failures and unknown outcomes. It is neither a per-day reset nor a currency,
billing or measured-token-usage guarantee. A real synthesis-policy change
creates a new policy-epoch budget; unrelated tenant access-epoch changes do not.
That fresh budget does not change semantic job identity or authorize replay of
unknown work. Purging a source/job does not refund its persisted reservation.

At most one application-level model invocation is attempted per reserved job.
A crash after reservation may mean **zero** calls or an unknown completed call.
An expired/reclaimed job with a reservation terminates as `billing_unknown`
without calling again. Model failures do not enter automatic retry, including
when a provider's own error might otherwise be retryable. There is no automatic
fallback.

An explicit request to the same processing/compaction API can name `retry_of` to
create a new child job. The parent must be failed, owned by the current principal,
and have the same trusted semantic intent, including recipe/profile and source
references. A previous call with `outcome=unknown` **or**
`billing_unknown=true` rejects the retry with 409 `job_retry_unknown`; there is
no force override. No prior call, or a known call outcome without billing
ambiguity, may pass this check, subject to all current admission checks.
The child has its own one-call reservation and consumes the persisted budget;
retry does not clear the parent's accounting. A known-failure child captures the
current policy epoch; unknown-outcome retry remains denied even after a policy
change. A genuinely different profile/intent is not the same retry. Read job `call` and
`processing_result` fields before deliberately authorizing new work. Existing
structured-job retry semantics are not a substitute for this contract.

Revocation, cancellation, deletion or policy changes while the call is in flight
can prevent publication; they cannot retract bytes already delivered to the local
provider. The implementation does not hold a tenant database barrier across
network waits or pretend to provide exactly-once external execution/billing.

## Extraction: lexical gate, quarantine and human authority

Provider candidates retain the [ADR 0027](0027-typed-extraction.md) bounds:
at most 16, exact Unicode-codepoint `[start,end)` evidence slices, lexical
subject/value containment, and `status="untrusted"`. The model supplies neither
scope/source IDs nor approval, timestamps, provenance authority or ACL changes.
The worker supplies those references from its trusted job input.

The first real-model extraction failed with `invalid_provider_response`. A
separate Bob-source diagnostic returned the exact quote but an end offset of
16 instead of 28. Both explicit failed model/diagnostic calls remain accounted
for; they are not an automatic retry or a successful three-call pipeline.

The implemented adapter correction uses a model-facing proposal with
only `subject`, `predicate`, `value`, `evidence_quote`. The host derives
`start` / `end` only when that quote occurs **exactly once** in the original
input, rejecting missing, repeated and overlapping ambiguous matches rather than
choosing the first occurrence. It then applies unchanged strict
`parse_extraction`; the public candidate/result remains the six-field,
Unicode-codepoint-span contract. This is neither fuzzy matching nor semantic
authority. Complete-quote guidance subsequently fixed subject-omitting proposals;
the three-call lifecycle passed at `e4f5d76`. Prompt changes also change the
worker profile digest and require deliberate policy reauthorization. Earlier
failed attempts remain recorded, not retroactively qualified.

Automatic publication requires **both** policy allowlisting and one of:
`preferred_language`, `preferred_editor`, `preferred_theme`, `preferred_format`.
The entire source input **and** evidence quote must both be exactly:

```text
subject / predicate: value
```

No surrounding whitespace is accepted; the value must match `[\w .+#/-]{1,128}`.
An otherwise matching span embedded in a larger source is not sufficient.
Conflicting candidate values for the same subject/predicate also prevent
automatic publication.
An additional conservative deny-pattern rejects approval/permission, secrets,
execution, negation and uncertainty terms. This is a deliberately restrictive
literal gate, **not semantic entailment**, consent verification, a comprehensive
content detector or a measured precision guarantee. Ordinary natural-language
statements generally remain quarantined.

An existing equal subject/predicate/value is a duplicate, not a new assertion or
new provenance lifetime. Conflicting existing values quarantine the candidate;
the worker does not overwrite them. Accepted new assertions are explicitly
`epistemic_status="inferred"` and `explicit_intent=false`, with untrusted
model/recipe/prompt-digest/input-digest/source derivation. Candidate records preserve ordinal,
disposition (`published`, `duplicate`, `quarantined`), reason and an optional
server-created assertion ID.

### Explicit candidate adoption is not verified human approval

GET returns untrusted proposals, trusted input references and extraction
derivation. The dedicated adoption POST requires strict `explicit_intent=true`,
`expected_input_digest` and `reason`, with the normal idempotency key. It requires
a succeeded extraction job, a quarantined candidate, current source authority
and matching canonical source digest/evidence span; conflicts fail closed.

The server creates a **reported** assertion and records the caller's declaration,
not proof that a human reviewed it: lineage includes
`source_class="caller_explicit_adoption"` and `human_review_verified=false`.
Episode, span, model, prompt, profile, recipe and job lineage are preserved.
Assertion and actor IDs are server-assigned, never accepted as model authority.
Adoption grants no approval/ACL privilege and neither overwrites nor supersedes
another assertion.

The original candidate disposition remains `quarantined`; separate
`adopted_assertion_id` / `adopted_by` identify its single adoption. Re-adoption
returns the live existing adopted assertion rather than making a second one.
Existing explicit remember/revision operations remain available separately,
but they are not a substitute for this candidate-lineage-preserving interface.
Neither adoption nor automatic literal publication satisfies human-quality gates.

## Working events, exact state and bounded compaction

Events reference live same-scope episodes on an existing valid run/branch.
The server locks the branch and assigns contiguous sequence numbers; callers
cannot supply sequence numbers. Re-appending the same source on that branch is
deduplicated. A stream is bounded to 1,000 events. Event queries use ascending
sequence, exclusive `after_sequence`, and pages of 1–100 (default 20).

Compaction specifies `expected_head` and `through_sequence`. This implementation
compacts only the complete prefix **1..N, N ≤ 100**, not an arbitrary rolling
window. Missing/invisible events invalidate the operation. Old checkpoint
references plus covered episode references are bounded to 100 distinct objects;
the old checkpoint itself is a further processing dependency.

The summary input is canonical covered-event text with server-owned references
and a coverage digest. Publication rechecks that input and the old checkpoint
checksum, then advances the checkpoint head under CAS. It copies the saved typed
state **exactly**, including goal/constraints/approvals/tool state and legacy
optional-field omissions. The model cannot rewrite this state. The separate
snapshot stores the untrusted summary, trusted references, coverage, model,
recipe/input digest, job ID and integrity checksum.

A changed head rejects stale publication. Concurrent events beyond the covered
prefix can remain as the tail without changing that head; they are not silently
absorbed or lost. There is no separate tail-length CAS and no deletion of either
covered events or tail by compaction. Snapshot reads recheck integrity,
authorization and saved/current epochs, and return the checkpoint plus a paged
tail. Clients must follow `next_after_sequence`, not assume a page is complete.

### Explicit after-compaction hook context

The implemented hook input adds optional UUID `working_snapshot_id`, accepted
**only** for `event="after_compaction"`. The operator must separately enable
`PGAG_HOOK_WORKING_SNAPSHOT_BUDGET_BYTES` with a value of 1–65536; its default 0
disables snapshot context. Hook input cannot override this budget or the startup
scope allowlist.

The hook validates the Native version, fetches a typed `WorkingSnapshot`, then
performs lexical recall with the tail's source references required. The **entire
serialized snapshot JSON** must fit the separate snapshot byte budget; this does
not enlarge or replace the existing recall-pack budget. Only a requested,
validated snapshot adds `working_snapshot` to the output. The legacy four fields
(`status`, `event`, `result`, `error`) remain unchanged for ordinary hook requests.

Validation checks snapshot/checkpoint ID, the configured scope allowlist,
coverage and tail scope/run/branch, and agreement of current/saved/tail/recall
access and deletion epochs. Missing tail references or an incorrect required
retrieval prefix fail validation. A paginated tail fails with
`working_tail_incomplete`; more than 16 distinct tail references, or more than
configured `max_items`, fails with `budget_exhausted`. Oversized snapshot or
recall context also fails rather than silently dropping or truncating sources.

The hook does not discover the latest snapshot, execute work, approve actions,
run compaction, or elevate the untrusted summary above the exact typed checkpoint
state. Epoch checks do not provide an atomic revocation guarantee after Native
has already delivered bytes; the host must continue discarding context on
revocation. This implementation and the v0.0.27/schema-13 version bump remain
part of the **unqualified** combined milestone, not foundation qualification.

## Deletion, upgrades and remaining work

Purge closure includes processing input/job relationships, inferred and
caller-adopted derivatives, candidate publications, snapshots/checkpoints and
working-event dependencies.
Purging a working source invalidates the affected run and removes its working
events, rather than fabricating a gap-free surviving prefix. Candidate/derivation
and snapshot payloads in the closure are removed; compacted state cannot silently
revive purged sources. Durable non-content call accounting remains for budget
enforcement. Provider logs/caches and backups require their own deletion and
recovery procedures; database purge is not proof of their erasure.

Migrations 012/013 extend the job schema and add policy/call/candidate/working
tables. The unpublished candidate-adoption migration 014 was folded into
`013_working_compaction.sql` and removed: the actual target is **schema 13**,
not a released schema 14 or a supported downgrade from it.
Back up and quiesce older writers/workers before migration, then use
matched schema-13 services/adapters. Code revert alone is not a database
downgrade. Migration 013 adds nullable episode `source_namespace` without
inventing historical values: legacy rows cannot satisfy a restrictive namespace
allowlist during processing merely because they predate the column. An
unrestricted namespace policy still requires enabled synthesis, matching consent
and every other admission check.

The deliberately bounded surfaces above are not defects hidden by a completion
claim: there is no general semantic publication validator, verified-human-approval
endpoint, external automatic egress, destructive log compaction, or unbounded
rolling summary. The bounded 10,000-case generated ACL experiment and four actual
process-crash/purge recovery cases passed. Human precision/fidelity, real-task
replay, backup recovery and cost acceptance remain outstanding.
[Evaluation](../EVALUATION.md) distinguishes what
the current scorer can measure from those separate acceptance obligations.
It also documents the explicitly opted-in real-model evaluation harness and
three-call processing smoke, distinguishing measured dev/held-out/ACL evidence,
versioned public attempts, retained failures and the successful processing
lifecycle rather than inferring overall success.
