# ADR 0024: Selectable inference foundation

[日本語](0024-selectable-inference-jp.md) | [Contract](../STATUS.md#selectable-inference-providers) | [Operations](../operations/README.md#selectable-inference-providers)

- Date: 2026-09-18
- Status: draft for v0.0.24/schema 10; qualification pending
- Extends: [Explicit vectors](0011-pgvector-retrieval.md), [Python SDK](0012-python-sdk.md)
- Repository/license: `rioriost/pg_agmemory`; MIT unchanged; bilingual documentation
- Boundary: provider foundation, not M2/MVP/production/performance/quality completion

## Decision

Provide an optional `pg-agmemory[providers]` extra using existing `httpx==0.28.1`,
typed `pg_agmemory.providers`, and operator CLI
`pg-agmemory infer inspect|summarize|embed --config FILE`.
Select one closed, frozen `ProviderSettings` profile per call:
loopback-only `local_http`, HTTPS `openai_compatible`, or SQL `azure_ai` with
explicit `flexible_server`/`horizondb` selection and extension version pin.
Profiles reference environment names, never embedded secrets. A base URL path is
allowed for inference, unlike Native SDK origins. At least one model is required.
Separate calls can summarize locally and embed through Azure; there is no fallback.
See the [complete settings table](../STATUS.md#closed-configuration-and-input).

This adds no server route, SDK memory method, MCP tool, or hook action:
Native/SDK **31 resources**, MCP **four tools**, and the hook remain unchanged.
Inference does not persist memory, enqueue, extract assertions, publish, or compact.
Schema 10/history 1–10 and dependency versions remain; no migration.
Stage `m2-selectable-inference` adds `optional_provider_adapters` and `model_inference`
with `interface: "operator_cli_and_python"`, `extra: "providers"`,
the three backends, both Azure products, `inspect`/`summarize`/`embed`,
`automatic: false`, `publishes_memory: false`, `live_provider_qualified: false`.

## Trust, billing, and persistence boundary

Closed input is `{text}`, preserving exact UTF-8 bytes, non-whitespace and
1–65,536 characters/256 KiB. Configuration is ≤32 KiB; HTTP requests ≤256 KiB,
responses/SQL serialized results ≤2 MiB. Operator timeout is 1–120 seconds,
default 30. HTTP `max_output_tokens` is 1–4096, default 1024; SQL rejects it.
No automatic retry, redirect, proxy environment, or backend fallback.
HTTP uses bounded OpenAI-compatible chat/embedding shapes, not universal vendor support.

`SummaryResult` carries declared model, input digest, summary, and `untrusted` status:
not a grounded assertion, approval, or compaction snapshot.
`GeneratedEmbedding` is existing `VectorQuery` plus input digest, requiring exactly
768 finite values and finite nonzero norm; no padding/truncation.
Model revisions are operator pins, not cryptographic evidence about remote aliases
or AIMM upgrades. A changed model space needs a new identity/revision.
Explicit Native input → provider embed → `PutEmbedding` preserves current
ACL/digest/model/purge guards. Uncertain upload recovery reuses the exact generated
payload/write key, not a newly executed model response.
Sanitized `ProviderFailure.error` has `code`, `retryable`, `billing_unknown`;
possible external charges are separate from Native mutation `outcome_unknown`.
Cancellation does not prove rollback or stopped billing.

SQL inference uses a dedicated autocommit, TLS `verify-full` connection with
system CA or an operator-supplied CA file, not a canonical transaction/session lock.
Each SQL function statement still has a transaction. Privileged roles and
canonical ownership are rejected. Every call checks exact extension version,
extension membership through `pg_depend`, one compatible non-set-returning
overload, argument/result types, and SQL `USAGE`/`EXECUTE`.
One `MATERIALIZED` evaluation and a server-side result-size guard bound result transfer.
The adapter installs/configures nothing and does not read key settings/model registries.
HTTP `inspect` is configuration-only; SQL `inspect` is read-only catalog inspection.
Neither performs inference or proves model permission, quota, connectivity, or quality.
Operators configure credentials/registrations outside the app; managed identity
is the project's recommendation where supported. Query/provider logs, retention,
consent, and budgets remain operator responsibilities.

## Azure reference boundary

Official references checked 2026-09-18, **not live-service verification**:

- [Flexible embeddings](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-openai): `azure_openai.create_embeddings` takes deployment name; `real[]` result. `dimensions` is documented since 1.1.0 for compatible models despite omission from displayed signatures. Inspect the deployed overload; 768 is not supported by every model.
- [Horizon embeddings](https://learn.microsoft.com/en-us/azure/horizondb/ai/generate-vector-embeddings): first argument is a registered model alias, not a deployment name. Both adapters explicitly use `dimensions => 768`, `timeout_ms`, `throw_on_error => true`, `max_attempts => 1`.
- [Flexible AI functions](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-ai-functions) and [Horizon AI functions](https://learn.microsoft.com/en-us/azure/horizondb/ai/ai-functions): `generate` uses `prompt`, `model`, `json_schema`, `system_prompt`, with `{name, strict: true, schema}`. Documentation describes text/JSONB responses without a complete deployed overload contract. Require JSONB schema input, inspect text/JSONB result types, and validate closed `{summary}`. Do not invent token-limit, timeout, or retry arguments; statement timeout applies.
- [Flexible Language](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-cognitive): explicit `language` mode uses `azure_cognitive.summarize_abstractive`, optional language and sentence count 1–20/default 3; preserve all `text[]` parts joined by paragraphs. Request `disable_service_logs => true`, timeout, throw-on-error, and one attempt. This is not full erasure. Horizon Language is unverified and unsupported.
- [Language lifecycle](https://learn.microsoft.com/en-us/azure/ai-services/language-service/summarization/overview): Summarization retires **2029-03-31**, with new projects directed to Foundry. This project therefore prefers `generate` for new setups while retaining explicit Flexible Language mode.
- [Flexible versions](https://learn.microsoft.com/en-us/azure/postgresql/extensions/concepts-extensions-versions#azure_ai): PG18 documents `azure_ai` **2.0.0**, PG12–17 **1.3.1**. [Horizon versions](https://learn.microsoft.com/en-us/azure/horizondb/extensions/concepts-extensions-versions#azure_ai): PG17 **2.2.1**. Availability tables are not installed-version or feature-parity proof.
- [Horizon overview](https://learn.microsoft.com/en-us/azure/horizondb/overview#limitations) marks the service preview; [AIMM](https://learn.microsoft.com/en-us/azure/horizondb/ai/ai-model-management) is limited preview requiring approval. Flexible AI functions are preview. Model aliases do not establish immutable weights or automatic compatibility.

The inference DSN may target a separate database. The canonical MemoryDB remains
pinned to PostgreSQL 18.6 / `vector` 0.8.6; this does not certify full MemoryDB
hosting on either Azure product.

## Qualification boundary

V24 qualification is pending; no local/native count, implementation SHA, install
result, or smoke success is recorded. Planned HTTP and SQL checks use synthetic
services/catalog fixtures, **not a vendor extension binary or live Azure/LLM**.
Even successful fixture qualification cannot establish live compatibility,
human-review effectiveness, provider budget controls, quality, or M2 completion.
See [pending evidence](../STATUS.md#v0024--schema-10) and
[historical v23 qualification](../STATUS.md#v0023--schema-10).
