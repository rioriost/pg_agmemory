# pgag_memory

[日本語](README-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**PostgreSQL-backed agent memory, licensed under MIT.** The public repository
remains `rioriost/pgag_memory`; the local checkout directory, Python package,
and service are `pg_agmemory`. Run the commands below from that local checkout.

**Status: v0.0.4/schema 4 tool-effect ledger implemented; local and native Docker checks passed.
Not a completed M1, MVP, or production release.**
Implemented: authenticated observation, explicitly reported structured memory
with same-scope episode evidence, PostgreSQL full-text recall, evidence
explanation, transactional idempotency, and synchronous active-store purge.
Tenant/scope permissions are enforced in both the service and PostgreSQL RLS.
Every mutation commits before its response is sent. Assertion revisions retain
server-controlled system-time history and revision-specific evidence.
Typed checkpoints support restore-to-new-branch envelopes. The new milestone
adds a durable tool-effect intent/outcome ledger, not harness or tool execution.

Cross-assertion supersession/fact arbitration, provider receipt verification, harness adapters,
workers, automatic synthesis,
pgvector, Japanese tokenization, AGE/SQL/PGQ, MCP, SDKs, and postgresem adapters
remain roadmap work. No performance or memory-quality acceptance targets have
been measured. Consult [the current contract and limitations](docs/STATUS.md)
before using the service.

## Container checks

Local development uses **Apple Container**, not Docker Desktop. Install and
start [Apple Container](https://github.com/apple/container), then:

```bash
container system start
./scripts/test-containers.sh
```

The script builds locked Python dependencies, runs Ruff, mypy, and unit and
PostgreSQL integration tests, then starts the production image and checks HTTP
health. It uses isolated disposable PostgreSQL containers and removes only its
own containers/networks. Existing databases and containers are not touched.
Python/PostgreSQL/uv image versions and digests are pinned in the container files.

GitHub Actions executes the same script with Docker on native **linux/amd64**
and **linux/arm64** runners, including runtime-image startup:

```bash
./scripts/test-containers.sh docker
```

No hosted model key or external memory database is required. Container images
and Python dependencies must be downloadable on the first run.
For **v0.0.4/schema 4**, implementation commit
[4a7d3f8](https://github.com/rioriost/pgag_memory/commit/4a7d3f8), Apple Container
and native Docker **linux/amd64** and **linux/arm64** each passed **73 tests**
(2 existing warnings), Ruff, strict mypy (8 source files), and production HTTP
health smoke. See
[CI run 35098507356](https://github.com/rioriost/pgag_memory/actions/runs/35098507356)
and the [validation evidence](docs/STATUS.md#validation-evidence).

## Run the API

Use PostgreSQL 18. The following are application commands to run inside an
image built from `Dockerfile` (the final stage is the runtime image).

1. Run `pg-agmemory migrate` with `PGAG_ADMIN_DATABASE_URL` pointing to the
   intended empty Memory database. Migrations are transactional and rerunnable.
   The administrator must bypass forced RLS (superuser or appropriately
   privileged `BYPASSRLS`) and have the required role/schema/table DDL and
   `btree_gist` installation rights. These are not runtime privileges.
2. Create a dedicated login with `NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime`
   and a securely assigned password. **Do not grant membership in the migration
   owner's role.** Set `PGAG_DATABASE_URL` to this restricted login.
3. Run `pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT` with the admin
   URL to create a private tenant, principal, and scope. Save the returned
   `scope_id`. A subject is unique within the configured issuer.
4. Set `PGAG_JWT_PUBLIC_KEY` to a PEM RSA public key (at least 2048 bits),
   `PGAG_JWT_ISSUER` to the exact issuer, and `PGAG_JWT_AUDIENCE` to this
   service's audience. Tokens must be RS256-signed and contain `sub`, `iss`,
   `aud`, `iat`, and `exp`. Tenant/principal IDs in request bodies are rejected.
5. Run `pg-agmemory serve`. Terminate TLS at a trusted reverse proxy; port 8000
   itself serves HTTP. Do not expose it directly to an untrusted network.

Keep the admin URL and signing private key out of the runtime environment.
There are no built-in credentials, default tokens, or authentication bypasses.
Migration/provisioning access is administrative and must never be exposed as a
public endpoint. The runtime process refuses superuser, RLS-bypass, and
table-owner roles at startup.

**Upgrading to v0.0.4 requires a maintenance stop and backup.** Stop all
old/new API traffic and images, apply pending migrations through `004_tool_effects.sql`,
then start only the new API. The new runtime requires schema history exactly
`[1, 2, 3, 4]`. Keep old images stopped; v0.0.1 lacks a schema-compatibility guard.
No rolling old-API compatibility or downgrade is supported. Follow the
[migration procedure](docs/operations/README.md#v004-maintenance-migration).

With `MEMORY_URL`, `TOKEN`, and the provisioned `SCOPE_ID` in your shell:

```bash
curl --fail-with-body "$MEMORY_URL/v1/observe" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-observation-1' \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"source_namespace\":\"demo\",\
\"source_event_id\":\"contract-1\",\"occurred_at\":\"2026-09-01T00:00:00Z\",\
\"content\":\"ACME contract is Gold\",\"consent_reference\":\"demo-consent\"}"

curl --fail-with-body "$MEMORY_URL/v1/recall" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"scope_ids\":[\"$SCOPE_ID\"],\"query\":\"Gold\",\
\"purpose\":\"demo\",\"token_budget\":2000}"
```

`consent_reference` records the caller's assertion of consent; this initial
service does **not** verify an external consent registry or automatically
redact secrets/PII. Only send approved, already-sanitized data.
Interactive schema documentation is at `/docs`; OpenAPI is at `/openapi.json`.
`/healthz` is process liveness after startup validation, not continuous DB readiness.

## Assertion corrections

`POST /v1/assertions/{memory_id}/revisions` requires `Idempotency-Key` and a
full replacement body: `expected_revision`, `value`, same-scope episode
`evidence`, `explicit_intent: true`, valid bounds, and `reason`. Subject,
predicate, and scope stay immutable. A successful correction returns `201`
with the next revision; a head mismatch returns `409 revision_conflict`.

This replaces the **entire valid interval**; omitted bounds are unbounded.
It does not split time or preserve the old value before a future-dated bound.
Historical `known_at` queries can still select the old revision. `explain`
defaults to revision `1`, **not latest**, when the revision is omitted.
Deleting a source used by any revision purges the entire assertion history.
See [the full contract](docs/STATUS.md#assertion-revision-contract) and
[ADR 0002](docs/adr/0002-assertion-revisions.md).

## Typed checkpoints

`POST /v1/checkpoints` stores schema-1 typed state under a scope-local run/branch,
with mandatory `expected_head` (`null` for the first checkpoint), a nondecreasing
event watermark, exact memory references, and an HMAC checksum.
`GET /v1/checkpoints/{checkpoint_id}` returns a currently authorized, checked
envelope. Checkpoints do not appear in `recall` or `explain`.

`POST /v1/checkpoints/restore` requires an exact harness/version match and
creates a new branch; it never rewinds the original branch. Dispatched effects
in the run ledger become unknown atomically with the fork. GET/restore merge
all live run effects, including those added after the snapshot. Untracked hints,
even planned ones, block resumption; this intentionally tightens legacy behavior.
`automatic_reexecution` is always false. Saved assertion references keep their exact historical revisions;
restore neither selects the latest revision nor refreshes current external facts.
Callers must declare every memory dependency and sanitize all state;
undeclared copied text is not discovered automatically.

Deleting a source propagates through assertion history, checkpoint references,
and the entire descendant/fork lineage. Affected branch heads cannot be reopened.
See [the checkpoint contract](docs/STATUS.md#checkpoint-contract) and
[ADR 0003](docs/adr/0003-checkpoints.md). This is not full M1 or disaster recovery.

## Tool-effect ledger

Create a bootstrap checkpoint first: `POST /v1/tool-effects` requires an existing
scope-local run. Record a caller-generated operation UUID, tool name, canonical
action's lowercase 64-hex `action_hash`, and all exact memory dependencies.
Raw arguments/hash are not persisted; GET returns a tenant-HMAC fingerprint
and stable external idempotency key. Each run allows 100 effects for its lifetime.

`POST /v1/tool-effects/{memory_id}/transitions` appends CAS-checked state changes.
The harness must durably record dispatch **before** calling the tool and use the
stable external key where supported. Plan/transition responses are historical
revision references, not current-state snapshots or execution authorization.
With current authorization, surviving effects can replay old references even
after the run is sealed; fresh dispatch remains rejected.
Confirmed/failed outcomes require caller-reported
receipt references; the server does not verify them or query providers.
There is no external exactly-once guarantee, approval service, or automatic execution.

Purging any effect, directly or through a declared source, removes every
checkpoint payload in its run and permanently seals the run against new
effects, dispatch, checkpoints, or resumption. Independent surviving effects
remain readable/reconcilable. New run/operation IDs are not semantic deduplication.
See [the ledger contract](docs/STATUS.md#tool-effect-ledger),
[operations](docs/operations/README.md#tool-effect-operations), and
[ADR 0004](docs/adr/0004-tool-effects.md).

## Documentation

| English | 日本語 |
|---|---|
| [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md) |
| [Current contract and limitations](docs/STATUS.md) | [現在の契約と制限](docs/STATUS-jp.md) |
| [Initial architecture decisions](docs/adr/0001-initial-slice.md) | [初期アーキテクチャ決定](docs/adr/0001-initial-slice-jp.md) |
| [Assertion revision decisions](docs/adr/0002-assertion-revisions.md) | [Assertion revisionの決定](docs/adr/0002-assertion-revisions-jp.md) |
| [Checkpoint decisions](docs/adr/0003-checkpoints.md) | [Checkpointの決定](docs/adr/0003-checkpoints-jp.md) |
| [Tool-effect ledger decisions](docs/adr/0004-tool-effects.md) | [Tool-effect ledgerの決定](docs/adr/0004-tool-effects-jp.md) |
| [Operations](docs/operations/README.md) | [運用](docs/operations/README-jp.md) |
| [Contributing](CONTRIBUTING.md) | [貢献方法](CONTRIBUTING-jp.md) |

See [LICENSE](LICENSE). Dependencies retain their own licenses; no model
weights, third-party datasets, or benchmark conversation histories are bundled.
