# Operations for the initial slice

[日本語](README-jp.md) | [Project README](../../README.md) | [Current contract](../STATUS.md)

**Not a production runbook or a verified disaster-recovery procedure.**
Use approved, sanitized, disposable test data for this initial release.
Destructive operations—including purge drills, schema resets, and restore
experiments—must run only against disposable test databases, never business
databases or real user histories.

## Bootstrap and role separation

Use PostgreSQL 18 and an image built from the repository's `Dockerfile`.
The CLI is `pg-agmemory`; the import package is `pg_agmemory`.
The local checkout is `pg_agmemory`; GitHub remains `rioriost/pgag_memory`.
The v0.0.6 durable-jobs milestone requires schema 6. Apple Container and native
Docker amd64/arm64 each passed 114 tests (2 existing warnings), Ruff, strict mypy
(11 source files), and non-root production API HTTP plus actual CLI worker
`--once` idle smoke. Final SHA, CI logs, timings, and separate historical v5
evidence are in
[validation evidence](../STATUS.md#validation-evidence).

| Setting | Consumer | Purpose |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | Administrative CLI only | Migration and private tenant/principal/scope provisioning |
| `PGAG_DATABASE_URL` | API and worker runtime | Dedicated restricted login belonging to `pgag_runtime` |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | Static PEM RSA verification key, at least 2048 bits; never the signing private key |
| `PGAG_JWT_ISSUER` | API runtime | Exact trusted issuer |
| `PGAG_JWT_AUDIENCE` | API runtime | Exact audience for this service |

1. Confirm that the admin URL identifies the intended empty, disposable Memory
   DB. Run `pg-agmemory migrate` from the application image. The migration is
   transactional and version-recorded in `public.pgag_schema_migration`.
   The migration loop accepts only sequential supported history and skips
   applied versions on rerun; lock acquisition has a 5-second timeout.
   Any existing DB upgrade requires the maintenance procedure below.
2. The unchanged `src/pg_agmemory/storage/001_initial.sql` and
   `src/pg_agmemory/storage/002_assertion_revisions.sql` and
   `src/pg_agmemory/storage/003_checkpoints.sql` and
   `src/pg_agmemory/storage/004_tool_effects.sql` and
   `src/pg_agmemory/storage/005_relational_graph.sql`, followed by additive
   `src/pg_agmemory/storage/006_durable_jobs.sql`, are installed package
   resources. Do not substitute the illustrative DDL in the plan or expect
   generated files. The administrator must be superuser or a qualified
   `BYPASSRLS` role with the required ownership/DDL, role/schema creation, and
   `btree_gist` extension installation rights. Bypass alone does not grant DDL.
   Migration 002 uses `row_security = off` to fail closed if RLS would filter
   its backfill; that setting does not bypass forced RLS by itself.
3. With a separate administrator, create a dedicated runtime login using
   `NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime` and securely assign its password.
   Grant neither table ownership nor membership in the migration owner's role.
   Do not grant role/database creation privileges to this login.
4. Run `pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT` with the admin
   URL. It creates a private tenant, principal, and scope and returns their IDs;
   provisioning is not an endpoint and is not a membership-update command.
   Use a subject issued by the configured trusted issuer.
5. Supply only the runtime settings and run `pg-agmemory serve`. The process
   rejects superuser, RLS-bypass, and application-table-owner connections at
   startup, including owner-role membership. It also requires the schema
   ledger to equal `[1, 2, 3, 4, 5, 6]` exactly; missing, older, newer, or incomplete
   history is rejected. The worker reuses these role/schema checks without
   requiring the API's JWT settings.

Keep the admin URL, signing private key, tokens, and tenant HMAC secrets out of
source control, issue reports, logs, and the runtime environment where not
needed. Never hand runtime DB credentials to agents as an arbitrary SQL entry
point: the service's fixed queries and trusted identity context are part of the
authorization boundary.

<a id="v003-maintenance-migration"></a>
<a id="v004-maintenance-migration"></a>
<a id="v005-maintenance-migration"></a>

## v0.0.6 maintenance migration

**No rolling old/new API/worker coexistence or downgrade is supported.**
Rehearse upgrades only in disposable test databases. Passing migration tests
does not qualify a production upgrade or disaster recovery.
Follow this maintenance protocol:

1. Stop and drain **all old and new APIs and workers**, including replicas,
   continuous worker loops, and automatic restarts. The migration advisory lock
   is not a substitute for stopping API traffic and worker claims/publication.
2. Take a backup and record the old application/schema versions. Preserve the
   latest deletion ledger and ACL revocations independently as required for
   restore quarantine. Do not overwrite the only pre-migration backup.
3. With the privileged migration administrator and the new image, run
   `pg-agmemory migrate`. It applies pending scripts and ledger updates in one
   transaction under the migration lock. A 5-second lock timeout aborts rather
   than waiting indefinitely; diagnose contention while traffic remains stopped.
4. Migration 006 adds `memory_ops.job`, immutable `job_input`, retained HMAC
   `job_identity`, and the `job` object kind, with RLS, same-scope foreign keys,
   input/lease/attempt/terminal guards, and limited job lifecycle UPDATE grants.
   Migrations 001–005 remain unchanged; older DBs receive missing versions
   sequentially. Preserve graph/assertion/effect histories, checkpoint checksums,
   timestamps, source-event/idempotency records, and `Remember` JSON/HMAC ordering.
   The v4 ledger's stricter resume rules remain: untracked hints, even planned
   ones, block resumption.
5. Confirm exact history `[1, 2, 3, 4, 5, 6]`, then start **only matching v6 APIs/workers**
   with restricted runtime credentials and the intended fixed worker subjects.
   Check capabilities/schema and the milestone's enqueue/retry, lease takeover,
   expiry rollback, authorization/epoch, purge, worker, and historical-compatibility
   behavior before restoring traffic. A health response alone does not validate these.
6. On failure, leave APIs/workers stopped. Do not launch the old image against the
   changed schema or assume a downgrade exists. Any backup restore remains
   quarantined until the latest deletion/ACL state is reapplied and validated.

**The old v0.0.1 API does not contain the new schema-compatibility guard.**
It may start against an incompatible schema; operators must keep it stopped.
The new runtime's refusal of schema mismatches does not protect old processes.

## Durable-job and worker operations

The worker publishes caller-supplied structured assertions; it is not an automatic
synthesis/NL extraction/LLM/provider, embedding, compaction, or tool-effect executor.
Provision a subject in advance, then run with only restricted `PGAG_DATABASE_URL`
credentials and trusted deployment identity:

```bash
pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT --once
```

The subject is 1–256 characters and must match the preprovisioned principal in the
configured issuer. It is not caller-controlled HTTP impersonation. Do not give
agents runtime DB credentials or authority to select worker subjects. No JWT
signing/public key or admin URL is needed by the worker; never use superuser,
table-owner/owner-member, or `BYPASSRLS` credentials. Startup shares API role/schema
validation. Only that principal's currently writable jobs can be claimed.
Same-scope readers can GET jobs but cannot run or retry another principal's work.

Omit `--once` for continuous operation: idle poll interval is 1 second, transient
DB loop delay is 2 seconds. `--once` handles at most one due job and prints JSON
`outcome` (`idle`, `succeeded`, `pending`, `failed`, or `lease_lost`), with applicable
opaque IDs/result reference. It does not wait for the entire queue or retry cycle;
other commands reject `--once`. Startup/unrecoverable errors are failures, not
successful idle results. This fixed-principal profile is not a global scheduler
or a qualified fairness/cost-pool implementation.
Worker stdout/logs contain opaque historical outcome references, not current
read authorization or a live snapshot. Read job GET/explain under current
access/deletion checks instead of relying on an earlier CLI outcome.

1. Submit `POST /v1/jobs` with an HTTP key and
   `{kind: "structured_remember", memory: <original Remember body>}`. Use current
   scope read/write access, explicit intent, and exact same-scope episode quotes.
   `202` is the committed job reference with fixed recipe `structured-remember-v1`,
   not publication completion. Save the original request securely for explicit
   retry; sanitize it before submission.
2. Retry uncertain enqueue with the same normalized request/key. Canonical
   intent/recipe also deduplicates across keys within one principal/scope,
   normalizing evidence order for job identity only. Another principal or
   different source identity is not semantic deduplication.
   Respect the scope's 100 pending/running cap; do not bypass it with other identities.
3. Poll job GET for state, attempts (maximum 5), scheduling/lease timestamps,
   safe error code, immutable episode input references, retry parent, and result.
   GET never returns request JSON, owner principal, or lease token. Terminal
   success/failure erases the request; succeeded results stay revision 1 after
   later corrections. Use the result's exact assertion revision for explain.
   Assertion recorded/system time begins at worker publication, not job enqueue;
   do not substitute job `created_at` for the assertion's adoption time.
4. Automatic retriable failures use `2^attempt + [0,1)` seconds of backoff/jitter.
   `invalid_input` fails immediately; an expired fifth claim fails with
   `attempt_limit`, without a sixth attempt. Diagnose safe
   `dependency_unavailable`/`stale_context` codes without logging payloads.
   `lease_lost` does not authorize another publication from an old prepared body.
5. For an owned terminal failed job, POST the full original `EnqueueJob` body
   to `/v1/jobs/{job_id}/retry` with a key. Current evidence/permissions and HMAC
   intent are rechecked; changed intent is `409 job_intent_conflict`, nonfailed
   parent is `409 job_retry_conflict`, and nonowner is `404`. Repeated retry of
   that parent reuses one child even across keys. If the child fails, retry its
   ID for another explicit five-attempt cycle. Never reset terminal rows/recipes in SQL.

Claims commit under `FOR UPDATE SKIP LOCKED` before payload preparation outside
the transaction. Default lease is 30 seconds with a fresh token and current epochs;
internal 1–300-second claim bounds are for controlled tests, not operator tuning.
Publication rechecks current identity/access, inputs/exact body, lease/token/expiry,
and epochs; output/provenance/job success/audit commit together. Final-update expiry
rolls back output. Internal heartbeat validates lease/epochs, but the deterministic
processor needs no background heartbeat task or external call. There are no public
claim/publish/heartbeat endpoints. At-least-once attempts produce at most one
committed result per job, not external exactly-once execution.

`observe` never auto-enqueues; synchronous `remember` and its legacy JSON/HMAC
remain unchanged. Recall's `jobs_pending` covers readable pending/running jobs
in requested scopes; `synthesis_pending` and `graph_used` remain false.
Jobs are not recall/explain items or checkpoint/effect reference kinds.
See [the contract](../STATUS.md#durable-jobs) and
[ADR 0006](../adr/0006-durable-jobs.md); M0/M1/M2/M3, MVP/production, performance,
quality, and DR acceptance remain incomplete.

## Entity and graph operations

1. Create explicit entities from approved same-scope episode quotes with
   `POST /v1/entities`, an allowlisted type, bounded canonical label, and
   `explicit_intent: true`. Save each returned revision-1 UUID. Labels/types are
   caller reports, not trusted instructions or verified facts. Use entity GET
   for metadata/evidence, not recall/explain. There is no alias/merge/name-resolution
   or label-correction endpoint; a new HTTP key may create a separate same-label
   identity. Retry uncertain creation with the original key/body.
2. Create relations only through `POST /v1/relations`, passing same-scope source/
   target entity UUIDs and episode evidence. The returned ID is the canonical
   assertion, not a second relation object. Matching free-text `remember` data
   stays untyped. All allowlisted predicates are multi-valued reported declarations.
3. Correct with `POST /v1/relations/{memory_id}/revisions`, an exact expected
   revision, target UUID, replacement evidence/valid bounds, explicit intent, and
   reason. Source/predicate stay fixed; omission of bounds is unbounded and
   replaces the entire interval. Generic assertion correction returns
   `409 relation_revision_required`. Inspect exact historical revisions through
   explain; omitted revision is 1, not latest. Never edit typed links or values in SQL.
4. Call authenticated, read-only `POST /v1/graph/expand` with explicit distinct
   scopes, entity seeds, predicates, and purpose; no `Idempotency-Key` is needed.
   Limits are 32 scopes, 16 seeds, 5 predicates, 1–2 hops, and 1–100 paths.
   Inspect effective `as_of`/`known_at`, coverage, and epochs. Prefixes count;
   cycles cannot repeat nodes within paths. Incoming/both is traversal orientation,
   not inferred inverse truth. Hidden seeds are not echoed; visible isolated
   seeds may be returned with no paths. Empty/bounded results do not prove absence.
5. Treat `409 graph_invalidated` as an invalidated read and DB `503` as failure,
   never as an empty graph. PostgreSQL canonical joins need no AGE/SQL/PGQ
   installation, projection rebuild, or lag/watermark operation:
   `backend: "sql"`, `projection_watermark: null`. There is no dynamic graph
   SQL/Cypher/label input. Recall remains FTS with `graph_used: false`.
6. Declare every copied entity revision 1 or exact assertion revision in
   checkpoint/effect `memory_refs`, including graph-derived dependencies.
   Entity GET and relation explain supply evidence; expansion nodes omit quotes.
   Do not treat a path or canonical label as permission to execute an action.

See [the contract](../STATUS.md#entities-and-sql-graph-oracle) and
[ADR 0005](../adr/0005-relational-graph.md). This bounded correctness reference
is not graph-utility/performance evidence, full M0/M1/M3, MVP, or production/DR qualification.

## Checkpoint operations

1. Capture only sanitized schema-1 state. Declare every copied memory source in
   `memory_refs`, including the exact revision. Undeclared copies are not
   discovered by a semantic scanner.
2. Create under the intended scope/run/branch with an explicit `expected_head`;
   use null only for a new branch. Resolve `409` head/watermark/harness conflicts
   rather than silently resetting the head. Save the returned checkpoint ID.
3. Load through the checkpoint GET endpoint, not recall/explain. Treat checksum
   or reference-validation failures as invalidation, not permission to bypass
   validation or edit the stored payload.
4. Restore only to a never-used target branch with the exact harness ID/version
   and state schema. The source branch remains unchanged. Saved assertion
   references keep their exact historical revisions; restore does not select
   the latest revision or automatically refresh external facts. Inspect
   `tool_effects`, `untracked_effects`, `requires_reconciliation`, and `resume_allowed`
   before handing state to a harness. These include all live run effects, not
   just snapshot-time effects. Untracked planned hints also block; a conflicting
   unknown hint plus a tracked planned effect needs uncertainty/receipt reconciliation.
   Restore atomically marks dispatched ledger records unknown before creating
   the fork; CAS rejects stale ledger writers, not in-flight external calls.
   `automatic_reexecution` is always false. No provider receipt lookup or code execution
   is performed by this API.
5. Retry uncertain writes with the same key and payload. Only the original
   result reference is retained in idempotency records, not state. Current
   authorization/checksum checks still apply; a purged checkpoint returns `404`.

A saved epoch or `resume_allowed: true` is not an approval or an external-effect
receipt. Typed pending effects are snapshot hints; the durable ledger is separate.
Checkpoint creation allows a 1 MiB body; other endpoints allow 256 KiB.
See [the contract](../STATUS.md#checkpoint-contract) and
[ADR 0004](../adr/0004-tool-effects.md). No production/DR qualification is implied.

## Tool-effect operations

1. Bootstrap the intended scope-local run with a checkpoint before planning
   effects. Compute a stable lowercase 64-hex hash of the host's canonical action,
   then POST its operation UUID, tool name, hash, and all exact memory dependencies.
   The service does not store arguments or the raw hash, or verify the intended
   external call. Sanitize tool names, reasons, receipt references, and state.
2. Save the returned `memory_id` and GET the latest record, including its stable
   `external_idempotency_key` and `run_invalidated` flag. Identity is scoped by
   tenant/scope/run/operation; new run/operation IDs do not deduplicate equivalent
   real-world actions. The lifetime cap is 100 effects per run, including terminals.
3. The host must enforce permissions/approvals and durably record a CAS transition
   to `dispatched` **before** any outside call. Use the stable external key if the
   provider supports it. The host owns execution coordination: a replayed old
   dispatch acknowledgment is not fresh permission to send or blindly resend.
4. On an uncertain outcome, record `unknown` and reconcile with the provider
   outside this service. `planned → unknown` can capture a legacy/off-protocol
   attempt; it is not permission to execute. `unknown → dispatched` is forbidden.
   Terminal `confirmed`/`failed` requires a bounded receipt reference plus
   `provider_receipt` or `operator_review`; both are caller-reported, not verified.
   Terminal outcomes are immutable and do not authorize automatic retries.
5. Retry uncertain ledger writes with the same key/body. Changed intent conflicts;
   a new key for the same recorded intent still returns its original revision-1
   reference. Plan/dispatch responses are historical revision references, not
   current-state snapshots or execution authorization. Under current authorization,
   replay for a surviving effect may still succeed after run sealing, without
   allowing fresh dispatch. Read GET for current state rather than trusting an old response.
   Do not delete checkpoint hints to bypass reconciliation or reuse IDs to evade
   uncertainty. An untracked hint must be resolved explicitly by the host.
6. After an effect purge seals the run, do not create new intents, dispatch, checkpoint, or
   resume it. Independent surviving effects remain GET-readable and can use
   allowed reconciliation transitions; `unknown → confirmed/failed` remains valid.
   Preserve the opaque operation registry, run flag, and tombstones.

This is a ledger, not a worker, harness adapter, provider-query client, approval
service, or external exactly-once mechanism. See [the contract](../STATUS.md#tool-effect-ledger)
and [ADR 0004](../adr/0004-tool-effects.md).

## Revision operations

Corrections append a full replacement revision, with immutable subject,
predicate, and scope. Preserve the same `Idempotency-Key`, target ID, and body
when retrying an uncertain correction. A successful replay returns its original
revision reference even if newer revisions exist; it is not a read of the head.
For `409 revision_conflict`, resolve the stale expected head rather than
silently overwriting. There are at most 1000 total revisions per assertion.

`explain` with no revision still means revision 1, not latest. Use the exact
revision returned by a mutation/recall when inspecting that result. Each
historical read remains subject to current ACLs and tombstones. A future-dated
replacement does not preserve the previous value before its new valid interval.
See [the revision contract](../STATUS.md#assertion-revision-contract) and
[ADR 0002](../adr/0002-assertion-revisions.md).

## Authentication, transport, and health

The verifier accepts RS256 with required `sub`, `iss`, `aud`, `iat`, and `exp`,
validates signature/issuer/audience/time, and resolves the external subject in
PostgreSQL. There is no JWKS refresh, overlapping-key rotation workflow, or
delegated identity. Changing the issuer requires reviewing subject mappings;
the database does not namespace principals by multiple issuers.

The API listens on HTTP port 8000. Terminate TLS at a trusted reverse proxy and
do not expose the runtime port directly to an untrusted network. No built-in
credentials or authentication bypass is provided. `/docs` and `/openapi.json`
are runtime-generated schema views, not deployment authorization.

`GET /healthz` is process liveness after startup validation. A successful probe
does not establish current database connectivity, authorization correctness,
or readiness for production. Database/lock failures can return `503`; replay
an uncertain mutation with the same key and unchanged payload rather than
inventing a new key. A committed mutation may have lost its HTTP acknowledgment.

## Membership maintenance and request drain

**Administrative permission changes must cooperate with the API's lock.**
There is no runtime membership-management endpoint. The API opens a short-lived
connection per request and takes the session advisory lock
`pg_advisory_lock(hashtextextended(tenant_uuid::text, 0))` using canonical UUID
text. It holds that lock through commit and buffered response sending.

On one dedicated administrative connection:

1. Acquire that same tenant **session** lock before modifying membership.
2. Begin a transaction, update permissions/membership, and increment that
   tenant's `access_epoch` in the same transaction.
3. Verify the intended tenant/scope/principal and affected rows, then commit.
4. Only after commit, release the lock or close the connection. On failure,
   roll back before releasing it; do not leave a locked session in a pool.

For a disposable test DB, the following `psql` example narrows an existing
membership to read-only. Supply `tenant_uuid`, `scope_uuid`, and `principal_uuid`
as `psql` variables for the provisioned test records. Run the sequence on the
same administrative connection and check affected rows before `COMMIT`.
The UUID cast normalizes text to match the runtime's lock key.

```sql
\set ON_ERROR_STOP on
SELECT pg_advisory_lock(hashtextextended(:'tenant_uuid'::uuid::text, 0));
BEGIN;
UPDATE memory.scope_member
SET permissions = ARRAY['read']::text[]
WHERE tenant_id = :'tenant_uuid'::uuid
  AND scope_id = :'scope_uuid'::uuid
  AND principal_id = :'principal_uuid'::uuid;
UPDATE memory.tenant
SET access_epoch = access_epoch + 1
WHERE id = :'tenant_uuid'::uuid;
COMMIT;
SELECT pg_advisory_unlock(hashtextextended(:'tenant_uuid'::uuid::text, 0));
```

A transaction-only advisory lock, a different hash/seed, unlocking before
commit, or updating ACLs without this lock does **not** satisfy the drain
protocol. Such administrative races are not covered. Tenant-wide serialization
also means a slow response can delay unrelated requests within that tenant;
performance is unmeasured. The protocol cannot revoke context already delivered
or bytes already passed to the network.

## Purge and retained records

Use explicit IDs and inspect `preview` before a destructive test. Preview does
not freeze targets; authorization and dependencies are evaluated again for
purge. Only `preview` and `purge` are accepted, even though future-mode names
may appear in internal schema constraints.

Purge traverses episode/entity/assertion history, job dependencies/retry lineage,
declared checkpoint/effect references, and
every descendant/fork checkpoint through the complete parent lineage. Its
limit is 10,000 dependents in total plus requested roots. A source used only
by an old assertion revision still removes the entire assertion history and
all affected checkpoint state. Episode evidence also leads to entities and all
relation histories using them as source or **any historical target**; direct entity
purge follows the same relation dependencies. Direct entity references in
checkpoints/effects participate. Other surviving entities are not removed merely
because a relation disappears. Semantic relation cycles are not provenance cycles:
entities depend only on episodes.
Job closure follows episode inputs → jobs, result assertions → jobs, and parent
jobs → retry descendants within the same 10,000-dependent bound.
**Deleting a job or failed-parent retry chain does not delete an already-published
independent assertion or source episode.** Purge its output/source explicitly to
erase the fact. Output assertions retain their own direct episode provenance;
any revision-source deletion removes the whole assertion and its dependent jobs.
There is no job → result dependency cycle.
Branches whose heads are affected are permanently
invalidated; do not try to reopen their IDs or remove lineage to avoid deletion.
Purging any effect also removes **all checkpoint payloads in that scope/run**,
including older empty snapshots, and permanently sets `effects_invalidated`.
It blocks new plans, dispatch, checkpoints, and resumption, but does not purge
independent effects merely for sharing the run; surviving records remain reconcilable.
Job request/input rows are removed before assertion/episode rows and tombstones
under the same tenant barrier, fencing running publishers. Purged-job GET/replay
returns `404`; retained job identity prevents exact-job resurrection.
Payloads, entity evidence, typed links, quotes, references, and effect events
(reason/receipt references included)
are SQL-deleted from active tables before timestamped markers enter
`memory_ops.object_tombstone` in the same transaction. Object SELECT RLS hides
those anchors; there is no soft-delete `deleted_at` update on `memory.object`
or privileged deletion helper. The barrier/receipt commits before responding.
The tenant session lock covers closure, run/branch invalidation, and read draining.
Purge does not enqueue a worker or rebuild affected content.
The receipt's `active_store_purged` is not
full erasure.

Opaque job identities, operation registry/run flags, run/branch metadata, objects and tombstones,
audit/receipt metadata, and
tenant-keyed HMAC source/idempotency tombstones remain for the tenant lifetime.
Do not manually remove
them or change `dedup_secret` to “finish” a purge: doing so can defeat replay
protection. Exact replay of deleted source identities or memory results returns
`404`; payload conflicts remain `409`. No automatic full tenant-erasure
procedure is provided. Historical references/replay cannot recover purged
entity labels, relation values, or receipts. Backup limits below are unchanged.

## Backups, restoration, and release evidence

Checkpoint restore copies typed state inside the Memory DB; it is not database
backup restoration, a separate working-snapshot compaction system, or disaster recovery.
A database backup can also roll back effect states. Keep external execution stopped
and reconcile provider outcomes separately; the ledger does not automate safe recovery.

Deletion receipts report `backup_status: "operator_managed"` with
`backup_retention_deadline: null`. SQL row deletion is not proof of physical
media sanitization, removal from WAL/replicas/backups, or erasure of delivered
context. There is no implemented backup-retention deadline enforcement,
automated restore replay, HA/PITR workflow, or verified RPO/RTO.

Required restoration boundary, **not yet an implemented automated procedure**:

1. Keep any restored database quarantined: no API, worker, agent, or user access.
2. Obtain the latest deletion ledger and ACL revocations from a source that
   was not rolled back with the backup; the old backup's own records are not
   sufficient.
3. Apply those deletions and permissions, including the corresponding epochs,
   before considering exposure. Preserve the tenant deduplication state.
4. Validate absence of deleted content and unauthorized access on the restored
   state. If current records are unavailable or cannot be safely applied, keep
   it quarantined. There is no supported command here that automates these steps.

Exercise only disposable restore drills; do not claim DR or production
compliance from this checklist or a health probe. Record actual test commands,
environment, architecture, and outcomes separately from the plan's unmeasured
targets. See [contributing](../../CONTRIBUTING.md) for the Apple Container and
native dual-architecture Docker CI checks.

`scripts/test-containers.sh` covers both production API HTTP smoke and actual
worker CLI smoke in the non-root production image. It provisions a disposable
principal, supplies runtime-only credentials to `worker --subject ... --once`,
asserts `{"outcome":"idle"}`, and logs `Production worker smoke passed`.
The CI step is `Test containers and smoke-test production API and worker`.
This idle-worker check is not a publication test or production/DR qualification.
Final v6 local and native Docker results are recorded in
[STATUS](../STATUS.md#validation-evidence).
