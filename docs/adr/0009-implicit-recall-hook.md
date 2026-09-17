# ADR 0009: Vendor-neutral implicit recall hook

[日本語](0009-implicit-recall-hook-jp.md) | [Current contract](../STATUS.md#implicit-recall-hook) | [Operations](../operations/README.md#implicit-recall-hook-operations)

- Date: 2026-09-17
- Status: v0.0.9/schema 7 implemented; final local and native amd64/arm64 checks passed
- Extends: [ADR 0008](0008-local-mcp.md), preserving Native memory semantics and both MCP protocol eras
- Naming/license: public repository `rioriost/pg_agmemory`; package/service
  `pg_agmemory`; MIT unchanged; English/Japanese documentation maintained
- Acceptance: full M0–M3, MVP, production, performance, memory quality, DR, and
  full-erasure gates remain incomplete

## Decision and scope

Provide optional `pg-agmemory recall-hook` as a **vendor-neutral, harness-side,
one-shot local Native HTTP client**. A trusted harness invokes it at a selected
lifecycle boundary. The service does not detect host events or automatically
register a hook in any host. This is **not a Copilot, Claude, or Codex integration
claim**, an MCP tool, a model invocation, or a generalized harness SDK.

The hook reads memory only. It performs **no writes, capture, queue submission,
LLM/provider calls, caching, retries, or idempotency-key generation/transmission**.
No database credentials, admin URL, signing key, or external model key are needed.
PostgreSQL remains the only application persistence store, and the Native
service/RLS remains the authority for identity, scope, time, evidence, and deletion.
Hooks do not expand permissions. Event names do not imply checkpoint creation,
compaction, synthesis, or tool execution.

Application version becomes **v0.0.9; exact schema remains 7**. There is
**no migration 008 or 009**, DDL, or new backfill from v0.0.7/v0.0.8.
API/worker retain exact schema history `[1, 2, 3, 4, 5, 6, 7]`.
Stop/drain old processes and hook launches during application updates;
equal schema does not establish mixed-version compatibility or downgrade support.

## Dependencies and shared transport

Optional **`pg-agmemory[hook]` pins `httpx==0.28.1`, not the MCP SDK**.
The separate MCP extra keeps official `mcp==2.2.0` and `httpx==0.28.1`.
Docker test/runtime stages include **both `mcp` and `hook` extras**.
Extract/reuse a shared bounded Native HTTP client rather than duplicating its
authentication, origin checks, response validation, or sanitized-error rules.
Genuine core-only/hook-only installation checks passed locally and on both native Docker architectures.
Shared `NativeSettings` also uses `httpx.URL` to reject control characters and
invalid IDNA before transport; all three final suites cover these cases.

Keep all MCP invariants: four Native-model tools; current ACL/deletion checks;
explicit evidence-backed remember; explain revision default 1; forget preview/
purge both HTTP 202; UTF-8 byte budgets and Japanese opt-in; caller-owned visible
ASCII idempotency keys; uncertain mutations and same-key/body recovery without
automatic retries; fixed identity; bounded, sanitized transport and no cache.
The **modern `2026-07-28` `server/discover`** and **legacy `2025-11-25`
`initialize`/`notifications/initialized`** contracts both remain.
The shared extraction must not apply the hook's read-only
`outcome_unknown: false` rule to MCP mutations, or replace MCP's existing
20 s total / 10 s I/O / 5 s connect / 4-connection limits with the hook deadline.

## Input and authority separation

Each invocation accepts **one UTF-8 JSON document on stdin, followed by EOF**:

```json
{"event":"session_start","query":""}
```

`event` must be exactly `session_start`, `task_switch`, or `after_compaction`.
`query` is required, at most **4,096 Unicode characters**; empty query browses
canonical accessible items in configured scopes under current-time defaults.
Retrieval intent comes only from JSON `query`; `event` is a lifecycle label.
Stdin is capped at **32,768 bytes**. Invalid UTF-8, JSON, oversized input, or
invalid fields must fail explicitly.

All extra fields are forbidden, including identity, `scope_ids`, `purpose`,
`mode`, budget, URLs, headers, tools, and times. `--subject`/`--once` are rejected.
Event/query text cannot authorize access, select a transport destination, or
override credentials. Never log queries or copy them into errors.

**Trusted startup environment only** supplies:

| Variable | Contract |
|---|---|
| `PGAG_HOOK_API_URL` | Required, no default. Operator-supplied HTTPS origin or loopback HTTP origin; no userinfo, application path, query, or fragment. Root `/` is accepted; absent URL gives `invalid_hook_configuration` |
| `PGAG_HOOK_API_TOKEN` | Required fixed Native API audience bearer token |
| `PGAG_HOOK_SCOPE_IDS` | Required JSON UUID array, 1–32 unique entries |
| `PGAG_HOOK_PURPOSE` | Default `implicit_context`, 1–256 characters |
| `PGAG_HOOK_TOKEN_BUDGET` | Default `2000`, integer 64–2,000 **UTF-8 bytes, not model tokens** |
| `PGAG_HOOK_MAX_ITEMS` | Default `20`, integer 1–20 |
| `PGAG_HOOK_SEARCH_PROFILE` | Default `simple-v1`; explicit `ja-janome-0.5.0-v1` opt-in |
| `PGAG_HOOK_TIMEOUT_SECONDS` | Default `2.0`, finite 0.1–20 seconds |

URL, token, and scope IDs are **all required**.
The host startup environment and executable selection must not be derived from
untrusted prompts, queries, retrieved evidence, or tool output. The token
targets the Native audience and is checked by Native authentication; this is
not host identity delegation. Requested scopes only narrow existing access.
Recall silently filters unauthorized scopes or revoked memberships, returning
the authorized subset or no items/`not_found`, **not a scope-existence 404**.
Token authentication failures remain explicit Native **401 / hook exit 1**.
These are unchanged Native semantics, not silent recovery from failed retrieval.
The trusted local host can exercise the configured identity's permissions;
do not share this boundary across untrusted callers or expose it as a network service.

## Fresh recall and bounded failure

Every invocation freshly authenticates `GET /v1/capabilities`, requiring exact
**`service_version: "0.0.9"`, `api_version: "v1"`, `schema_version: 7`**.
Only then send `POST /v1/recall` with `mode: "implicit"`, the configured recall
settings, and Native current-time defaults. Use the same fixed token for both
requests. No authorization or response cache substitutes for current checks.
Supply any replacement token at the next trusted invocation.

The configured network deadline covers **both capabilities and recall together**,
not a separate full allowance per request. It excludes process/interpreter
startup, stdin input/waiting, and output, and is **not an LLM latency SLO or a performance result**.
The host must close stdin and set a separate subprocess timeout.
The shared client caps serialized Native requests at **256 KiB** and HTTP
responses at **2 MiB**; context packs must additionally obey the configured
byte budget. The whole RecallResult and all host buffers are not limited to
the smaller context budget. `context_pack.byte_count` counts the **entire compact
JSON-serialized pack** in UTF-8, with `ensure_ascii=False` and
`separators=(",", ":")`, including metadata/citations, **not just text**.
Validate this same count against reported `byte_count` and the configured
budget. Also require returned item count to be at most `max_items` and the
returned search profile to match configuration.
Mismatches fail, without broad fallback. Redirects and proxy environment settings are
disabled, and TLS verification remains enabled.

An empty pack itself costs roughly **192 bytes**, not a guaranteed constant or
a replacement for the allowed 64-byte configuration minimum. A requested 64
can therefore yield Native **422 `budget_too_small` / hook exit 1** with an error
envelope, never silent empty success. `budget_exhausted` instead means the pack
fits but candidates cannot, and is Native **200 / hook exit 0**.
Missing projections with no candidates yield `index_incomplete` and
`coverage.lexical_incomplete: true` / `coverage.retrieval_complete: false`,
also **200 / exit 0**. Keep these existing Native outcomes distinct.

Validated hook runtime outcomes produce **one JSON result plus newline** on stdout, with sanitized diagnostics
only on stderr. Envelope shapes (placeholders, not literal JSON):

```text
{status: "ok", event: <event>, result: <full Native RecallResult>, error: null}
{status: "error", event: <validated event or null>, result: null,
 error: {code, retryable, outcome_unknown: false,
         native_status: <integer or null>, request_id: <UUID or null>}}
```

Native status and validated Native request UUID are nullable; no raw query,
body, credential, URL, or header belongs in diagnostics.
`retryable` is a hint, not an automatic retry.

- **Exit 0:** valid Native success, including genuine empty results with
  `not_found`, `budget_exhausted`, or `index_incomplete`.
- **Exit 2:** invalid configuration/input, including invalid UTF-8/JSON and input overflow.
- **Exit 1:** Native/network/version/protocol failure.

Confirmed runtime codes:

| Exit | Codes |
|---|---|
| `2` | `invalid_hook_configuration`, `invalid_hook_input`, `hook_input_too_large`, `hook_input_unavailable` |
| `1`, `retryable: true` | `hook_deadline_exceeded`, `native_api_unavailable` |
| `1` | `native_version_mismatch`, `invalid_native_response`, mapped sanitized Native codes including `budget_too_small` (Native `422`) |

**Invocation-error exception:** rejected CLI flags and a missing `hook` extra
use argparse stderr and **exit 2 without a JSON envelope**. All validated hook
runtime errors, including configuration/input failures, have the error envelope.
Hosts must handle non-JSON/invalid envelopes without echoing raw content.

**No result is returned on error. Never map failed retrieval to empty success.**
A host timeout/kill may prevent any envelope; treat that as failure too.
The host must surface error/coverage and explicitly decide to pause or continue
without memory. Empty success does not establish complete retrieval.
Keep the full Native result as separate **UNTRUSTED evidence**, not instructions,
policy, or verified current external truth. The executable
[stdlib Python harness example](../operations/README.md#vendor-neutral-python-harness-example)
demonstrates operator environment, stdin/EOF, separate timeout, exit/status
checking, and no raw stderr echo, without invoking an external model.

## Deletion boundary

The Native tenant session advisory response-drain barrier ends at HTTP delivery
to the **trusted local hook**. It is **not atomic through hook/pipe/stdout buffers
or host context**. Already-delivered or buffered data cannot be retracted;
there are no deletion notifications.

After forget or ACL changes, the host must discard previous context and make
a fresh hook invocation under current authorization. Do not reuse an in-flight
pre-change result. This is a host responsibility, not proof of host erasure,
nor full erasure of WAL, replicas, backups, or physical media.
Adding the hook changes no Native purge or authorization semantics.

## Evidence boundary and validation status

**Final v0.0.9 local and native CI results verified 2026-09-17 JST.**
The tested final local source matches published implementation
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050).
Both native jobs in
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)
passed, with actual logs confirming that exact SHA and all checks, not just job status.
Apple Container and native Docker amd64/arm64 each passed
**274 tests, 1 existing warning**, Ruff, strict mypy (**15 source files**),
genuine core-only/hook-only installation checks, and all non-root production
Japanese/API/worker smokes, MCP **`2026-07-28` and `2025-11-25`**, and hook
**`session_start`, `task_switch`, and `after_compaction`**.
Test elapsed times were **248.29 s** locally, **482.21 s** on native amd64,
and **374.33 s** on native arm64.
The suite covers Native budget, scope/revocation/authentication, missing-index,
and shared HTTPX invalid-origin handling. Timings are test observations, not
performance benchmarks. [STATUS](../STATUS.md#v009--schema-7) records the final
v0.0.9 evidence separately from historical milestones.

The Docker **`adapter-extras-check`** target checks genuine core-only/missing
extras, then hook-only **without MCP**, including explicit JSON for failed HTTP.
The script builds it on local Apple Container and both native Docker architectures.
It **passed in all three environments**.

Historical v0.0.8 implementation
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)
and [CI 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)
remain in [STATUS](../STATUS.md#validation-evidence), alongside v0.0.7 evidence.
The v0.0.8 bilingual documentation commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)
passed **214 tests on each native architecture** in
[CI 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509).
These historical results do not validate the hook or shared-client extraction.

The hook accepts a root `/`, as MCP does, but requires an explicit API URL:
there is no default destination. All startup URL/token/scope settings are required.
Engineering tests cannot qualify a named vendor integration, semantic quality,
or performance. All acceptance gates listed above remain incomplete.
