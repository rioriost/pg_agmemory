# pgag_memory

[日本語](README-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**PostgreSQL-backed agent memory, licensed under MIT.** The repository is
`pgag_memory`; the Python package and service are `pg_agmemory`.

**Status: initial M1 implementation, not a completed MVP or production release.**
Implemented: authenticated observation, explicitly reported structured memory
with same-scope episode evidence, PostgreSQL full-text recall, evidence
explanation, transactional idempotency, and synchronous active-store purge.
Tenant/scope permissions are enforced in both the service and PostgreSQL RLS.
Every mutation commits before its response is sent.

Corrections/revision history, checkpoints, workers, automatic synthesis,
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

## Run the API

Use PostgreSQL 18. The following are application commands to run inside an
image built from `Dockerfile` (the final stage is the runtime image).

1. Run `pg-agmemory migrate` with `PGAG_ADMIN_DATABASE_URL` pointing to the
   intended empty Memory database. Migrations are transactional and rerunnable.
   The migration administrator must be allowed to create roles and schemas.
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

## Documentation

| English | 日本語 |
|---|---|
| [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md) |
| [Current contract and limitations](docs/STATUS.md) | [現在の契約と制限](docs/STATUS-jp.md) |
| [Initial architecture decisions](docs/adr/0001-initial-slice.md) | [初期アーキテクチャ決定](docs/adr/0001-initial-slice-jp.md) |
| [Operations](docs/operations/README.md) | [運用](docs/operations/README-jp.md) |
| [Contributing](CONTRIBUTING.md) | [貢献方法](CONTRIBUTING-jp.md) |

See [LICENSE](LICENSE). Dependencies retain their own licenses; no model
weights, third-party datasets, or benchmark conversation histories are bundled.
