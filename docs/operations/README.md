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
The v0.0.3 checkpoint milestone requires schema 3. Its 54-test Apple Container
and native Docker results are recorded in [validation evidence](../STATUS.md#validation-evidence).

| Setting | Consumer | Purpose |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | Administrative CLI only | Migration and private tenant/principal/scope provisioning |
| `PGAG_DATABASE_URL` | API runtime | Dedicated restricted login belonging to `pgag_runtime` |
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
   `src/pg_agmemory/storage/002_assertion_revisions.sql`, followed by the additive
   `src/pg_agmemory/storage/003_checkpoints.sql`, are installed package
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
   ledger to equal `[1, 2, 3]` exactly; missing, older, newer, or incomplete history
   is rejected.

Keep the admin URL, signing private key, tokens, and tenant HMAC secrets out of
source control, issue reports, logs, and the runtime environment where not
needed. Never hand runtime DB credentials to agents as an arbitrary SQL entry
point: the service's fixed queries and trusted identity context are part of the
authorization boundary.

## v0.0.3 maintenance migration

**No rolling old/new API coexistence or downgrade is supported.**
Rehearse upgrades only in disposable test databases. Passing migration tests
does not qualify a production upgrade or disaster recovery.
Follow this maintenance protocol:

1. Stop and drain **all old and new API traffic and processes**, including
   replicas and automatic restarts. The migration advisory lock is not a
   substitute for stopping API traffic.
2. Take a backup and record the old application/schema versions. Preserve the
   latest deletion ledger and ACL revocations independently as required for
   restore quarantine. Do not overwrite the only pre-migration backup.
3. With the privileged migration administrator and the new image, run
   `pg-agmemory migrate`. It applies pending scripts and ledger updates in one
   transaction under the migration lock. A 5-second lock timeout aborts rather
   than waiting indefinitely; diagnose contention while traffic remains stopped.
4. Migration 003 adds checkpoint runs, branches, payloads, references, and
   constraints. Migrations 001/002 remain unchanged; an older DB receives any
   pending 002 migration before 003. Preserve assertion history, source-event/
   idempotency records, timestamps, and replay compatibility; do not reset them.
5. Confirm ledger versions are exactly `[1, 2, 3]`, then start **only the new API**
   with restricted runtime credentials. Check its capabilities/schema and run
   the milestone's migration, checkpoint CAS, restore, and lineage-deletion checks
   before restoring traffic. A health response alone does not validate these.
6. On failure, leave traffic stopped. Do not launch the old image against the
   changed schema or assume a downgrade exists. Any backup restore remains
   quarantined until the latest deletion/ACL state is reapplied and validated.

**The old v0.0.1 API does not contain the new schema-compatibility guard.**
It may start against an incompatible schema; operators must keep it stopped.
The new runtime's refusal of schema mismatches does not protect old processes.

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
   `requires_reconciliation` and `resume_allowed` before handing state to a
   harness; reconcile unknown/dispatched operations with the external system.
   `automatic_reexecution` is always false. No receipt lookup or code execution
   is performed by this API.
5. Retry uncertain writes with the same key and payload. Only the original
   result reference is retained in idempotency records, not state. Current
   authorization/checksum checks still apply; a purged checkpoint returns `404`.

A saved epoch or `resume_allowed: true` is not an approval or an external-effect
receipt. Typed pending effects are snapshot hints, not a durable effect ledger.
Checkpoint creation allows a 1 MiB body; other endpoints allow 256 KiB.
See [the contract](../STATUS.md#checkpoint-contract) and
[ADR 0003](../adr/0003-checkpoints.md). No production/DR qualification is implied.

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

Purge traverses episode/assertion history, declared checkpoint references, and
every descendant/fork checkpoint through the complete parent lineage. Its
limit is 10,000 dependents in total plus requested roots. A source used only
by an old assertion revision still removes the entire assertion history and
all affected checkpoint state. Branches whose heads are affected are permanently
invalidated; do not try to reopen their IDs or remove lineage to avoid deletion.
Payloads, quotes, and references are removed before timestamped markers enter
`memory_ops.object_tombstone` in the same transaction. Object SELECT RLS hides
those anchors; there is no soft-delete `deleted_at` update on `memory.object`
or privileged deletion helper. The barrier/receipt commits before responding.
The tenant session lock covers closure, branch invalidation, and read draining.
Purge does not enqueue a worker or rebuild affected content.
The receipt's `active_store_purged` is not
full erasure.

Opaque run/branch metadata, objects and their tombstones, audit/receipt metadata, and
tenant-keyed HMAC source/idempotency tombstones remain for the tenant lifetime.
Do not manually remove
them or change `dedup_secret` to “finish” a purge: doing so can defeat replay
protection. Exact replay of deleted source identities or memory results returns
`404`; payload conflicts remain `409`. No automatic full tenant-erasure
procedure is provided.

## Backups, restoration, and release evidence

Checkpoint restore copies typed state inside the Memory DB; it is not database
backup restoration, a separate working-snapshot compaction system, or disaster recovery.

Deletion receipts report `backup_status: "operator_managed"` with
`backup_retention_deadline: null`. SQL row deletion is not proof of physical
media sanitization, removal from WAL/replicas/backups, or erasure of delivered
context. There is no implemented backup-retention deadline enforcement,
automated restore replay, HA/PITR workflow, or verified RPO/RTO.

Required restoration boundary, **not yet an implemented automated procedure**:

1. Keep any restored database quarantined: no API, agent, or user access.
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
