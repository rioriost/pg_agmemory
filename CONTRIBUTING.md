# Contributing

[日本語](CONTRIBUTING-jp.md) | [README](README.md) | [Current contract](docs/STATUS.md)

Contributions to `pg_agmemory` are made under the project's [MIT license](LICENSE).
The Python package/service is `pg_agmemory`. Git is already initialized; work
on a branch rather than reinitializing the repository. This is an incremental
implementation, not a completed MVP. Read [ADR 0001](docs/adr/0001-initial-slice.md) before
changing authorization, evidence, transactions, or deletion.

## Container-first validation

Local validation uses **Apple Container**, not Docker Desktop or a silent host
fallback. Install/start Apple Container and ensure `jq` is available, then run
from the repository root:

```bash
container system start
./scripts/test-containers.sh
```

The script builds the test image and runs Ruff, mypy, a strict typed SDK consumer
check, unit tests and PostgreSQL integration tests.
It verifies actual core-only, hook-only, and SDK-only installations
without the MCP SDK, then starts the non-root runtime image and checks the
Japanese tokenizer, API liveness, worker, both MCP protocol modes, and all three
implicit recall hook events. The atomic capture smoke also exercises the actual
worker, retrieval, replay, and source purge. The pgvector smoke uses synthetic
episode/assertion vectors to check exact/hybrid retrieval and purge; this does
not qualify a real embedding model's semantic quality. The database image pins
PostgreSQL 18.6 and pgvector 0.8.6; see [ADR 0011](docs/adr/0011-pgvector-retrieval.md)
for pgvector requirements. Schema 9 additionally records administrative ACL changes.
The Python SDK smoke exercises typed
capture/replay, job/input reads, explicit vector recall, and deletion. SDK requests
must preserve caller-owned keys and Native authorization/uncertainty semantics;
see [ADR 0012](docs/adr/0012-python-sdk.md).
The scope-access CLI smoke uses disposable administrative credentials to inspect,
narrow, and revoke membership while checking Native read/write behavior. Preserve
the tenant session-lock drain, epoch CAS, and atomic audit contract described in
[ADR 0013](docs/adr/0013-scope-access.md); never grant runtime RLS bypass.
The readiness smoke verifies `/readyz` returns 200, then 503 while the disposable
schema ledger is unavailable, while `/healthz` stays live; readiness recovers
after restoration without an API restart. Keep the read-only, bounded probe
separate from liveness and resource authorization; see
[ADR 0014](docs/adr/0014-runtime-readiness.md).
The COMMIT cancellation stage uses a separate owned PostgreSQL primary started
with a missing synchronous standby and a `local` default. Only target test
transactions select `remote_apply`; no shared cluster configuration is changed.
`tests/test_commit_outcomes.py` observes actual `SyncRep`, cancels the exact
test backend, and verifies local persistence without success receipts or worker
retry. It also covers warning-suppressing session defaults and pre-COMMIT
rollback. Ordinary database runs skip the missing-standby cases; the main
container runner executes them separately on both CI architectures.
See [the uncertainty contract](docs/operations/README.md#unconfirmed-commit-outcomes).
Schema 10 adds terminal job cancellation. Its smoke uses the SDK to enqueue,
cancel, replay, confirm worker idleness, and purge the source dependency. Preserve
state/attempt CAS, owner/current-access checks, atomic audit/receipt writes, and
stale-worker fencing. HTTP 200 cancellation is a mutation, including for SDK
key validation and uncertain outcomes; see [ADR 0015](docs/adr/0015-job-cancellation.md).
The required-context smoke checks exact-reference selection ahead of optional
keyword matches, item limits, all-required byte-budget errors, and source purge.
Required references must never bypass scope, current authorization, or time filters;
see [ADR 0016](docs/adr/0016-required-context.md). Schema 10 is unchanged.
The structured-recall smoke checks exact kind/subject/predicate filtering and
required-reference mismatches. Apply filters before every ranking and projection-
coverage calculation, without bypassing current ACLs or temporal eligibility;
see [ADR 0017](docs/adr/0017-recall-filters.md). Hook input remains unchanged.
The checkpoint-head smoke checks scoped head discovery, branch advancement,
historical GET, and fail-closed lookup after source purge. Head reads must reuse
integrity/reconciliation checks, never create branches or fall back to an ancestor;
see [ADR 0018](docs/adr/0018-checkpoint-head.md). A head read does not reserve the CAS.
The job-query smoke checks caller-owned keyset pages, current state filtering,
and source purge. Apply current visibility and ownership before the page limit;
each cursor is a position, not authority or a stable snapshot. Reuse job GET
integrity checks; see [ADR 0019](docs/adr/0019-job-query.md).
The assertion-history smoke checks descending metadata pages, exact-revision
explanation, and source purge. Do not include full values or evidence quotes in
history pages or treat an ordinal cursor as authority or a fixed snapshot;
see [ADR 0020](docs/adr/0020-assertion-history.md). Current ACLs apply to every page.
The entity-query smoke checks exact scoped label/type lookup, duplicate identities,
explicit graph-seed selection, and source purge. Keep shared-scope visibility
distinct from owner-only job discovery; equal labels must not merge identities.
Metadata pages omit evidence quotes; see [ADR 0021](docs/adr/0021-entity-query.md).
Graph path-limit checks exercise automatic and actual generic prepared plans,
including nested-loop-only planning, for outgoing, incoming, and bidirectional
traversal. Execution-plan assertions bound protected
metadata and endpoint-evidence rescans at the 100-path boundary. Preserve the
materialized adjacency, assertion, revision, and distinct-endpoint boundaries and
their precomputed ID arrays; do not mask regressions by increasing timeouts.
The batch-capture smoke checks one episode with multiple independently published
jobs, ordered replay, and source purge. Admission is atomic, worker publication
is not. Preserve late-failure rollback and current checks for every replayed job;
see [ADR 0022](docs/adr/0022-batch-capture.md).
The episode-query smoke checks metadata pages, explicit source selection through
Explain and Remember, and source purge. Filter current readable scopes and
half-open occurrence ranges before pagination; order by admission time, not
occurrence time. Do not expose content or consent references in metadata pages,
or treat the cursor as authority, a snapshot, or a compaction watermark;
see [ADR 0023](docs/adr/0023-episode-query.md).
The selectable-inference smoke uses a synthetic local HTTP model, the operator
CLI, and explicit Native vector publication/purge. Provider tests exercise
Azure SQL contracts with synthetic fixtures, not the managed extension binary
or a live Azure deployment. Preserve TLS, catalog/privilege checks, parameter
binding, input/output limits, and the absence of automatic retries/fallbacks.
Never use real conversation data, provision paid resources, or invoke billable
models merely to run the default suite. Real-model quality and managed-service
qualification are separate; see [ADR 0024](docs/adr/0024-selectable-inference.md).
Real-provider cases are opt-in: the `live` pytest marker uses
`PGAG_LIVE_PROVIDER_CONFIG` and skips when it is unset. Only configure it in an
authorized, containerized run using synthetic data. A full two-model profile
selects five cases and six model calls, including the existing Native embedding
publication/replay/recall/purge scenario; that scenario also requires a disposable
`PGAG_TEST_DATABASE_URL`. These cases check contracts and lifecycle behavior, not
M2 quality gates. Default local and CI runs never inject live profiles or keys.
See [inference profiles](docs/INFERENCE_PROFILES.md) for model selection, safe
OpenAI configuration, and the host/container loopback distinction. Never loosen
the loopback restriction or expose Ollama on all interfaces merely to run a test.
The image explicitly selects Debian's trusted CA bundle with `SSL_CERT_FILE`;
keep `verify-full` for Azure SQL and preserve explicit operator CA overrides.
Local TLS transport regressions use an ephemeral test CA, not cloud credentials
or changes to the host trust store. The production smoke also verifies that the
configured system bundle loads trusted CA certificates. Neither check proves
every managed service/version is qualified; record live results by exact profile.
Schema 11 adds scoped capture admission policies. The `scope-capture` smoke sets
a complete policy with tenant-epoch CAS, exercises observe/capture/batch through
the SDK, denies both new-key and exact replays after disabling capture, restores
the policy, and purges its synthetic sources. Preserve the shared administrator
response-drain barrier, atomic audit/epoch updates, and policy checks before
idempotency/source-event deduplication. Scope permission checks must precede
policy denial. Missing overrides intentionally preserve legacy admission; these
controls are not a secret/PII detector, proof of consent, or provider-egress
authorization. See [ADR 0026](docs/adr/0026-scope-capture-policy.md).
It uses separate disposable PostgreSQL containers for tests and
the startup smoke check and cleans up its own resources. Do not aim tests,
schema resets, purge drills, or restore experiments at a persistent/shared DB.
`PGAG_TEST_DATABASE_URL` is for disposable test data only; prefer letting the
script create it rather than supplying a personal database.

GitHub Actions runs the same script with **Docker on native `linux/amd64` and
`linux/arm64` runners**, not build-only or emulation-only checks:

```bash
./scripts/test-containers.sh docker
```

That command is the CI path, not the local default. A single local run does not
prove both CI architectures passed. Report the actual engine, architecture,
command, and result; identify unavailable checks rather than claiming success.
No model API key is required. Initial runs need access to pinned images and
Python packages. Change dependency manifests and lock data together when needed;
do not loosen pins or add unrelated tools to bypass a failing check.

## Change scope and documentation

- Keep patches focused and add regression tests for changed behavior. Preserve
  tenant/scope isolation, current authorization on replay, literal same-scope
  episode evidence, commit-before-send, and the deletion drain protocol.
- Do not advertise workers, job enqueue, vector/graph search, checkpoints,
  corrections, SDKs, or other roadmap features before implementation and
  validation. Do not replace null confidence or byte-budget caveats with
  unsupported truth/token guarantees.
- Update English and Japanese documentation **in the same change**, including
  matching examples, limits, status, and operational caveats. Maintain reciprocal
  links and use the existing `-jp.md` names. Distinguish design targets from
  implemented behavior and measured results.
- OpenAPI is exposed at runtime through `/openapi.json`; no generated
  documentation files are required. Update source models and explain contract
  changes in the paired documentation.
- Schema changes belong in the packaged PostgreSQL storage resources with an
  explicit migration approach. Do not silently edit an already-applied schema
  version and assume rerunning migration will upgrade an existing DB.

## Safe submissions

Use synthetic fixtures only. Do not commit tokens, passwords, signing keys,
private connection URLs, personal conversations, customer data, or copied
production backups. Do not send private data to third-party services. Sanitize
logs and screenshots before including them in issues or pull requests.

In a pull request, summarize the change and limitations, list actual validation
results, and link related issues or ADRs. Record remaining checks honestly.
Claims of full erasure, DR, production readiness, performance, or memory quality
require separate evidence; a passing health probe or container build is not
that evidence. Keep dependency/dataset licensing separate from the project's
MIT license and do not add unlicensed or sensitive fixtures.
