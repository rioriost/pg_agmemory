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

| Setting | Consumer | Purpose |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | Administrative CLI only | Migration and private tenant/principal/scope provisioning |
| `PGAG_DATABASE_URL` | API runtime | Dedicated restricted login belonging to `pgag_runtime` |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | Static PEM RSA verification key, at least 2048 bits; never the signing private key |
| `PGAG_JWT_ISSUER` | API runtime | Exact trusted issuer |
| `PGAG_JWT_AUDIENCE` | API runtime | Exact audience for this service |

1. Confirm that the admin URL identifies the intended empty, disposable Memory
   DB. Run `pg-agmemory migrate` from the application image. The migration is
   transactional and version-recorded in `public.pgag_schema_migration`; reruns
   skip an already-applied version. Do not treat this as a general upgrade or
   rollback framework.
2. The schema is packaged at `src/pg_agmemory/storage/001_initial.sql` and loaded
   as an installed package resource. Do not substitute the illustrative DDL in
   the implementation plan or expect a generated migration file.
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
   startup, including owner-role membership.

Keep the admin URL, signing private key, tokens, and tenant HMAC secrets out of
source control, issue reports, logs, and the runtime environment where not
needed. Never hand runtime DB credentials to agents as an arbitrary SQL entry
point: the service's fixed queries and trusted identity context are part of the
authorization boundary.

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

Purge handles the current episode → assertion closure synchronously, up to
10,000 dependent assertions. It removes active episode/assertion bodies and
evidence quotes first, then inserts scope-bound timestamped markers into
`memory_ops.object_tombstone` in the same transaction. Object SELECT RLS hides
those anchors; there is no soft-delete `deleted_at` update on `memory.object`
or privileged deletion helper. The barrier/receipt commits before responding.
Purge does not enqueue a worker. A multi-source assertion affected by source
removal is deleted, not rebuilt. The receipt's `active_store_purged` is not
full erasure.

Opaque objects and their object tombstones, audit/receipt metadata, and
tenant-keyed HMAC source/idempotency tombstones remain for the tenant lifetime.
Do not manually remove
them or change `dedup_secret` to “finish” a purge: doing so can defeat replay
protection. Exact replay of deleted source identities or memory results returns
`404`; payload conflicts remain `409`. No automatic full tenant-erasure
procedure is provided.

## Backups, restoration, and release evidence

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
