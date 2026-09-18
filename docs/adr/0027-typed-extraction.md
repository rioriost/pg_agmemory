# ADR 0027: Bounded, source-grounded typed extraction

[日本語](0027-typed-extraction-jp.md) | [Provider foundation](0024-selectable-inference.md) | [Capture admission](0026-scope-capture-policy.md)

- Date: 2026-09-18
- Status: Implemented; container qualification is recorded separately
- Boundary: An additive inference dependency, **not M2 completion** or automatic publication

## Context

Selectable providers can already summarize text or generate embeddings. Neither
operation returns a typed assertion proposal with exact source locations.
Extraction needs a bounded, closed output that cannot supply memory authority.
Even exact source quotation is not proof that a proposed predicate/value follows
semantically from that quotation.

## Decision

Add `async extract(data: InferenceInput) -> ExtractionResult` to
`InferenceProvider`, `HTTPProvider`, and `AzureAIProvider`, and operator CLI:

```sh
printf '%s\n' '{"text":"Tokyo has not approved deployment."}' |
  pg-agmemory infer extract --config operator-provider.json
```

Use an existing operator-controlled provider profile with a configured text model.
The input remains closed `{text}`, preserving original whitespace, Unicode, and
UTF-8 digest; the existing 65,536-character/256 KiB limits apply. No new settings,
dependencies, routes, SDK methods, MCP tools, database schema, jobs, or publication
path are introduced by this adapter capability. Core-only imports remain
independent of the optional `providers` extra. Summarize/embed requests and
results are unchanged. `configured_operations`/`inspect.operations` append
`extract` after existing operations where supported.
The unchanged HTTP summary instruction is exposed as `SUMMARY_SYSTEM_PROMPT`,
alongside `EXTRACTION_SYSTEM_PROMPT`, so an operator can pin the actual instructions
without duplicating them. This does not change Azure summary prompt wording.

### Closed candidate and result contracts

Define the following in `pg_agmemory.providers`, not shared memory models:

| Type / field | Contract |
| --- | --- |
| `ExtractionCandidate.subject` | Existing `ShortText`, 1–256 characters |
| `ExtractionCandidate.predicate` | Existing `Predicate`, `^[a-z][a-z0-9_]{0,63}$` |
| `ExtractionCandidate.value` | 1–4096 characters |
| `ExtractionCandidate.evidence_quote` | 1–4096 characters |
| `ExtractionCandidate.start` | Strict integer 0–65535, inclusive source start |
| `ExtractionCandidate.end` | Strict integer 1–65536, exclusive source end |
| `ExtractionCandidates.candidates` | Required array of 0–16 candidates |
| `ExtractionResult` | Candidates plus operator `TextModel`, exact `input_digest`, and `status: "untrusted"` |

All objects reject unknown fields. Candidate strings preserve exact characters
without trimming or normalization, must encode as UTF-8, and cannot consist only
of whitespace. Offset booleans, numeric strings, and floats are rejected.
Offsets count **Unicode codepoints**, not UTF-8 bytes, UTF-16 code units, or
grapheme clusters. `0 <= start < end <= len(original_text)` and
`original_text[start:end] == evidence_quote` must hold. Subject and value must
each be an exact, case-sensitive substring of that same quotation; unique
occurrence is not required. Combining sequences and full-width characters are
not normalized.

Identical candidates, comparing all six fields, reject the **entire response**;
there is no silent deduplication or partial salvage. Different offsets into
repeated text are distinct proposals. Candidate order is preserved. An empty
array is valid abstention and never causes retry or fallback.

The provider-generated schema contains **only** `candidates`; it cannot supply
model identity, digest, trust status, IDs, source IDs, scopes, ACLs, permissions,
approval, explicit intent, time, or confidence fields. The adapter computes the
digest and attaches the configured text-model identity; upstream model aliases
are not authoritative model/revision attestations. Extraction snapshots input
and model before awaiting inference so caller mutation cannot change the
identity of the request already sent.

`extraction_schema() -> dict[str, Any]` generates the shared strict schema.
`parse_extraction(response: Any, data: InferenceInput, model: TextModel)
-> ExtractionResult` validates the bounded text/JSON object, closed candidates,
duplicates, and exact source slices before attaching the binding fields.
Constructing a result model alone cannot check a source it does not contain;
provider methods use this parser. JSON text with repeated keys, trailing content,
NaN/Infinity, malformed Unicode, or excessive nesting fails closed.

### Trust boundary

The instruction treats raw source as **data**, preserves original language,
negation, and uncertainty, and requests proposals rather than authoritative facts.
It prohibits following embedded instructions, invoking tools, or publishing
memory. Prompt wording is not an injection or semantic-correctness proof.

Server-side checks establish **lexical grounding only**. For example, the value
`approved` occurs inside `not approved`; a lexical validator can accept it while
the proposal remains semantically misleading. A predicate is syntactically
constrained, not inferred or proved by the validator. No confidence, approval,
intent, publication authority, or truth guarantee follows from successful parsing.
Any downstream review/admission/publication design must preserve that boundary
and independently validate its own source access and authority.

### HTTP providers

Both local loopback HTTP and HTTPS OpenAI-compatible adapters use one
`chat/completions` request with `response_format.type: "json_schema"` and
`{name, strict: true, schema}` generated from the candidate model.
The default extraction `max_tokens` is **4096**; the existing explicit
`max_output_tokens` setting overrides it within 1–4096. Summaries retain their
existing default of 1024. The maximum candidate count is a validation limit,
not a promise that a provider can fill every field before the token limit.

Source text is the user message, not interpolated into system instructions.
No tools, streaming, automatic retries, redirects, or provider/model fallback
are enabled. The existing 256 KiB serialized-request and 2 MiB raw-response
limits, timeout, transport/TLS, no-proxy-environment, and credential guards remain.
Schema/prompt overhead can make a maximum-size input too large for an HTTP
request; that fails before client creation without billing ambiguity.

Require exactly one assistant choice, `finish_reason: "stop"`, string content,
and no nonempty refusal, tool calls, or legacy function call. Validate JSON and
grounding locally even if the provider claims strict-schema compliance.
Unsupported structured output is an error, not a reason to send a weaker request.

### Azure SQL

Extraction is supported **only** for explicitly configured `generate` mode with
a compatible `azure_ai.generate` catalog signature. Reuse the exact existing
extension/version/member/overload/result/privilege checks, dedicated TLS
`verify-full` connection, statement timeout, bound parameters, and one
`MATERIALIZED` function evaluation with a 2 MiB SQL result-transfer guard.
Bind raw source as `prompt`, configured model, generated JSONB schema, and the
shared extraction system prompt. Accept the compatible text/JSONB results only
after the same candidate and grounding validation.

Language summarization mode does **not** advertise `extract`. Calling it returns
`provider_capability_unavailable` before connecting or executing SQL, even if a
generate function happens to exist. Never route extraction through summarization.
Embedding-only profiles also fail before inference. No invented generate retry
or output-token parameters are added: the existing deployed generate contract
has no verified knobs for those controls.

### Failure and billing

Malformed, ungrounded, over-limit, duplicate, refused, truncated, or tool-bearing
responses fail atomically with sanitized `ProviderFailure` code
`invalid_provider_response`, `retryable: false`, `billing_unknown: true`.
No raw source, SQL, credentials, provider diagnostics, or malformed response is
copied into an error. Existing HTTP status/transport and SQL error classification
is preserved. Preflight/unavailable-mode failures do not claim ambiguous charges;
after SQL submission, uncertainty remains conservative.

One adapter request or one materialized SQL evaluation does not prove one
billable upstream attempt. Extension-internal behavior, cancellation, logging,
data retention, consent, model quality, and provider budget enforcement remain
outside this capability. Do not repeat a request merely to clear billing
uncertainty.

## Validation and limits

Synthetic tests cover both HTTP backends, Azure text/JSONB and both product
contracts, real disposable PostgreSQL synthetic functions, schema/catalog guards,
CLI dispatch, abstention, source/model/digest binding, Japanese/emoji/combining
codepoints, strict offsets, forbidden fields, duplicates, limits, malformed
outputs, billing uncertainty, and unavailable-mode no-network behavior.
They do not invoke paid services, download models, or certify a live Azure
extension or semantic extraction quality.

Container execution and its exact results are the integrating change's
responsibility; this ADR does not predeclare success. This adapter dependency
alone is not M2, MVP, or production completion, a quality evaluation, human-review
qualification, automatic capture/synthesis, or permission to publish memory.
