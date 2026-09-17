# ADR 0012: Typed asynchronous Python SDK

[日本語](0012-python-sdk-jp.md) | [Contract](../STATUS.md#python-sdk) | [Operations](../operations/README.md#python-sdk-operations)

- Date: 2026-09-17
- Status: accepted and verified in bounded v0.0.12/schema 8, locally and on both native architectures
- Extends: [shared Native adapter boundary](0009-implicit-recall-hook.md), [capture](0010-atomic-capture.md), and [pgvector retrieval](0011-pgvector-retrieval.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Acceptance: not completion of M0–M3, MVP, production, performance, memory quality, DR, or full-erasure gates

**Historical version notice:** this ADR records verified v0.0.12/schema 8,
including its application-only upgrade and startup-version decisions.
[ADR 0013](0013-scope-access.md) records verified privileged scope-access administration
with schema 9; SDK resources remain unchanged, but matching v0.0.13 components
are required. v0.0.13 local and both native checks passed. The separate final v0.0.12 docs
CI 35194141510 is recorded in [historical evidence](../STATUS.md#v0012--schema-8).

## Decision

Expose `AsyncMemoryClient` and `MemoryClientError` from `pg_agmemory.sdk`,
reusing typed Native request/response models from `pg_agmemory.models`.
Cover all **24 public memory resource methods** listed in the
[contract](../STATUS.md#typed-resource-methods): observe/capture/remember/revisions,
recall/explain/forget/deletion progress, embedding input/upload, entities/
relations/graph, jobs/retry, checkpoints/restore, and tool-effect intent/history/
transitions. This is not a wrapper around CLI administration or worker execution.
Capabilities are an internal handshake, not an SDK health/OpenAPI download or
raw arbitrary-request surface. No server resource endpoint is added.

The optional `pg-agmemory[sdk]` extra adds only **httpx==0.28.1**.
It is the **same core distribution**, still including FastAPI, psycopg, and
Janome—not a standalone lightweight published package. No PyPI publication is
claimed. Ship a PEP 561 `py.typed` marker.
If HTTPX is unavailable, SDK import raises a static installation `ImportError`.
HTTPX installed by the `mcp`/`hook` extras also satisfies this dependency; Python
does not distinguish which extra supplied it. Docker test/runtime include
`sdk`, `mcp`, and `hook`; isolated sdk-only installation must not install the MCP SDK.

## Lifecycle and authority

Require `async with AsyncMemoryClient(api_url, api_token) as memory:`.
Explicit constructor arguments use fixed `NativeSettings`: HTTPS origin or
loopback HTTP origin, without application path, userinfo, query, or fragment.
Validate token shape, not identity: the server performs actual authentication.
Configuration errors retain sanitized `ValueError`, not `MemoryClientError`.
Scopes narrow current server ACLs and cannot override the bearer identity.

Entry creates an owned HTTP client, performs an authenticated exact
**service 0.0.12 / API v1 / schema 8** capabilities probe, and closes resources
on failed entry in `finally`. Reject use before entry/after exit with
`client_not_open` and re-entry with `client_already_used`.
Exit releases connection resources only, **not stored memory**.
The caller must await outstanding tasks, or cancel and await them, before exit;
client close is not a request scheduling/cancellation manager or DB rollback.
No implicit environment configuration, DB credentials, cache, provider calls,
host registration, delegation, automatic capture/jobs, or token refresh is added.
There is no synchronous client or TypeScript SDK.

## Typed exchanges, bounds, and uncertain outcomes

Revalidate request models at call time to catch invalid mutable instances and
snapshot the request before the first outbound network await.
Model construction can separately raise ordinary Pydantic `ValidationError`
outside the SDK; do not log its private input details.
Validate UUID path arguments and caller-owned keyword-only `idempotency_key`
before dispatch. Keys are **1–256 visible ASCII characters, with no trimming**.
Every mutation, including both `forget` modes, requires a key.
Return Native typed models; `explain` selects episode/assertion shape and
`forget` selects preview/purge shape by request mode. Preserve Native expected
200/201/202 statuses; `forget` preview and purge both remain 202.

Reuse bounded Native HTTP without changing MCP/hook semantics:
**20 s total per exchange, 10 s I/O / 5 s connect, 4 connections, verified TLS,
no proxy environment, no redirects, 2 MiB responses**. Requests are **256 KiB**,
except SDK `create_checkpoint` at **1 MiB**; adapter bounds are not increased.

`MemoryClientError` aliases existing `AdapterFailure`; safe data is under
`.error`: `code`, `retryable`, `outcome_unknown`, `native_status`, `request_id`.
Never return raw response/input/token diagnostics. Permit current Native domain
error codes for the SDK only, preserving MCP/hook's existing safe-code set.
Unknown server codes map to `native_api_error`. Invalid local request/key/UUID
returns sanitized `invalid_request`, `outcome_unknown: false`, before dispatch.

**No automatic retry or replacement key.** Network/5xx/malformed/wrong-success
responses for mutations conservatively mean outcome unknown. Retain the exact
caller key/body before dispatch and reconcile with that same pair, honoring
current ACL, deletion, and replay guards; never infer no commit.
Cancellation propagates rather than becoming an SDK error. An in-flight
mutation cancellation also means reconciliation is necessary; it is not rollback.
The `retryable` flag is information, not an instruction to retry automatically.

## Compatibility and consequences

This is an **application-only schema-8 update**, with no schema 9 migration.
Retain the pinned PostgreSQL **18.6** and `vector` **0.8.6 in `public`** images,
schema history/extension startup checks, and all existing vector/lexical/
capture/checkpoint/job/effect contracts. Stop/drain old APIs, workers, adapters,
hooks, and SDK callers; deploy matching v0.0.12 components, not mixed versions.
The stage is `m2-python-sdk`; capabilities add `python_sdk` metadata:
`installation: "sdk-extra"`, `async: true`, `automatic_retry: false`.

Returned memory is evidence, not trusted instructions or guaranteed current
facts. Native whole-JSON UTF-8 byte budgets, incomplete coverage, current ACLs,
purge/replay, and host/backup/WAL erasure limitations remain unchanged.
Closing a context cannot retract returned copies.

## Validation boundary

Final v0.0.12 **local Apple Container and native Docker amd64/arm64 checks passed**:
426 tests and 1 existing warning per environment; Ruff, strict mypy
(18 source files), separate strict typed consumer
(1 file), genuine core/hook/sdk wheel installs, packaged `py.typed`, and all
non-root production smokes. The total includes 76 SDK unit and 5 SDK integration
tests (81 new alongside 345 retained), with all 24 methods covered over real HTTP.
Passing fixtures cover
lost committed-response recovery, request/error/lifecycle bounds,
isolated core/hook/sdk installs, and a non-root production SDK lifecycle.
The SDK smoke uses capture/replay, pending job read, embedding input/upload,
exact vector recall, preview/purge/deletion progress, and rejected capture replay.
It does not invoke a worker; the existing actual capture-worker smoke is separate.
Implementation [88e1206](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)
passed [CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945).
Actual logs verified the exact SHA, counts, checks, and all production smokes.
Test elapsed: **292.76 s local / 484.79 s amd64 / 472.49 s arm64**.
See [verified evidence](../STATUS.md#v0012--schema-8).
Elapsed time is not a performance benchmark or production qualification.
The verified v0.0.11 implementation CI 35189448403 and final-docs CI 35190495385
are separate [historical records](../STATUS.md#v0011--schema-8), not SDK validation.
