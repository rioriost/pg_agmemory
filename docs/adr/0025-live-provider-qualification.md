# ADR 0025: Live provider evidence and CA-image fix

[日本語](0025-live-provider-qualification-jp.md) | [Current status](../STATUS.md#v0025--schema-10) | [Profiles](../INFERENCE_PROFILES.md)

- Date: 2026-09-18
- Status: v0.0.25/schema 10 locally qualified; exact-SHA native CI passed
- Extends: [ADR 0024: Selectable inference foundation](0024-selectable-inference.md)
- Boundary: exact-profile contract evidence, not blanket provider certification or M2 completion

## Decision

Release the bounded CA-image/default-trust-store correction and observed provider
profile documentation as **v0.0.25**, retaining **API v1/schema 10**.
The Docker base sets
`SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`.
The non-root production smoke checks that environment value and that
`ssl.create_default_context().cert_store_stats()["x509_ca"] > 0`.
The separate local real-libpq TLS regression and non-root runtime CA check have
passed, as recorded below. Full local qualification also passed:
**999 passed, 5 live skipped, 1 known warning / 494.49 s**.
Exact-SHA implementation CI also passed, as recorded below; the verified
historical v24 run remains separate.

This is a minimal packaging/transport correction, not a new inference API:
the provider library and operator CLI retain their contracts, SQL TLS `verify-full`,
explicit CA-file support, strict role/catalog guards, bounded I/O, sanitized
errors, and no application retries/fallback. No certificate or hostname check is
disabled. HTTP clients retain `trust_env=False` and do not adopt environment
proxies. No SQL migration, Python dependency-version change, or canonical
PostgreSQL **18.6 / pgvector 0.8.6** image change is required.

Stage remains `m2-selectable-inference`; Native/SDK retains **31 memory resources**,
MCP **four tools**, and the hook is unchanged.
`auto_synthesis: false` and global
`model_inference.live_provider_qualified: false` remain. There is no automatic
ingestion, extraction, summarization, publication, or compaction.
Use matching **service 0.0.25 / API v1 / schema 10** components after stop/drain;
there is no mixed-version rollout claim.

## Why the CA selection changes

The Azure trial's psycopg-binary client failed with `sslrootcert=system`.
Offline inspection found missing compiled-in default CA files in two bundled
OpenSSL builds; both honor `SSL_CERT_FILE`. Selecting the installed OS CA bundle
addresses trust-store discovery without weakening TLS.

The successful Azure live run instead supplied
`sslrootcert=/etc/ssl/certs/ca-certificates.crt` explicitly in its environment-held
DSN and retained **verify-full/TLS 1.3**. It used **v24 `aa364c3` client code**,
not the v25 image environment default. Those live results therefore do **not**
qualify the ENV correction. The local TLS regression and runtime CA check below
are independent evidence, not an Azure environment-variant test.
Azure resources were not reprovisioned for them.
See [operator instructions](../operations/README.md#tls-trust-store-selection).

## Separate local v25 TLS evidence

**All nine real psycopg/libpq TLS startup cases passed in Apple Container**, with
the targeted Ruff check also passing.
[The regression](../../tests/test_provider_tls.py) uses an ephemeral CA/server key
and a loopback minimal PostgreSQL startup fixture with bounded sockets and thread
teardown. It exercises the actual TLS/client libraries, not a live Azure server
or a full PostgreSQL database engine.

The cases verify `SSL_CERT_FILE` with `sslrootcert=system`, explicit DSN root-CA
precedence, fail-closed behavior for missing/untrusted CAs and wrong hostnames,
prevention of a `require` downgrade from `verify-full`, and sanitized errors.
A **separate non-root v25 runtime check** verified the expected `SSL_CERT_FILE`
value and **150 loaded trusted CAs**; 150 is the observed count, not a new minimum
contract. These targeted checks are distinct from the full local result below
and do not substitute for native CI.

## V25 local and native qualification

Apple Container `./scripts/test-containers.sh` passed with
**999 passed, 5 live skipped, 1 known warning, 494.49 s**.
Ruff, mypy **22 source files + 1 strict SDK consumer**, all four
core/hook/sdk/providers installation smokes, and **all production smokes,
including the new system-CA smoke**, passed.
The five opt-in live cases were skipped; no Azure inference was rerun after cleanup.
The CA ENV correction is verified locally and in native CI, not tested as an Azure
environment variant. Implementation
[`adbead0ab42dfa4a5465f9464d3855c5f64d85c1`](https://github.com/rioriost/pg_agmemory/commit/adbead0ab42dfa4a5465f9464d3855c5f64d85c1)
passed exact-SHA [CI 35303758871](https://github.com/rioriost/pg_agmemory/actions/runs/35303758871):
**amd64 999 passed, 5 live skipped, 1 warning / 600.77 s;
arm64 999 passed, 5 live skipped, 1 warning / 797.75 s**.
Both native jobs passed Ruff, mypy **22 source files + 1 strict SDK consumer**,
all four core/hook/sdk/providers installation profiles, and all production smokes
including the new system-CA smoke. This is implementation CI, not a
qualification claim for a later final-documentation commit.

## What was actually observed

The [profile guide](../INFERENCE_PROFILES.md) records exact model identities,
configuration, topology, outputs, and limits. These are historical v24 runs:

| Scope | Evidence |
| --- | --- |
| Local Ollama 0.34.1 | Qwen2.5 7B and Qwen3 embedding 0.6B: four EN/JA contract cases passed / 23.97 s; separate synthetic/live Native pair passed / 3.98 s. Five live cases across the two runs plus CLI checks made eight real model calls. |
| Azure Flexible Server | Mounted `aa364c3` code in Apple Container: **5 passed, 54 deselected, 1 known warning / 20.12 s**; CLI inspect/summarize/embed succeeded. Eight application inference calls, no application retries. |
| Published baseline CI | `aa364c3969fda48b52c8fef19c8794ec99cd354b`, [CI 35298758297](https://github.com/rioriost/pg_agmemory/actions/runs/35298758297): amd64 **989 passed, 5 skipped / 685.52 s**; arm64 **989 passed, 5 skipped / 796.17 s**, with all checks/smokes. Live cases were skipped; this is not v25 qualification. |

Azure used West US 3 AI Services S0 and PostgreSQL **18.6/B1ms/32 GiB** with
**azure_ai 2.0.1**, the observed available/installed extension version.
The profile was updated from proposed **2.0.0** after operator approval;
earlier official-documentation and synthetic-fixture 2.0.0 evidence is preserved.
Summary used `gpt-4.1-mini` version `2025-04-14`, GlobalStandard capacity 10;
embedding used `text-embedding-3-small` version 1, GlobalStandard capacity 1,
with 768 dimensions. Revision labels are operator declarations, not
cryptographic guarantees about future aliases/model upgrades.

The server used managed identity with account-scoped **Cognitive Services OpenAI
User**, API-key authentication disabled, and a separate restricted SQL login.
SQL inspect returned `contract_verified: true`, `inference_tested: false`.
Inference then returned untrusted summaries and 768 finite embedding values with
finite nonzero norms. Canonical input, explicit upload, exact replay, vector recall,
purge, and post-purge rejection passed using a **separate local** canonical
PostgreSQL 18.6/vector 0.8.6 database. No MemoryDB schemas were created in Azure.
This is not full MemoryDB-on-Azure hosting qualification.

## Quality and provider limits

**One English-input Azure summary came back in Spanish despite the
original-language instruction.** Passing shape, digest, and lifecycle contracts
does not pass language-fidelity, grounding, or semantic-quality gates.
Do not hide the deviation or label the run quality-qualified. Summaries remain
untrusted and subject to review.

Direct OpenAI API calls remain untested because the user key was not configured.
Separate HorizonDB live testing was waived, not passed; Azure Language calls were
not tested. Success for these exact Ollama/Azure profiles does not certify every
model, version, provider, or future deployment, hence the unchanged global flag.
No M2/MVP/production/performance or human-quality completion is claimed.

Eight application inference calls are not proof of eight billable upstream calls.
The application does not retry; SQL embedding requests `max_attempts => 1`.
`azure_ai.generate` has no verified retry/output-token knob, and one
`MATERIALIZED` SQL evaluation does not constrain extension-internal billing.
Cancellation does not prove stopped charges. Keep generated payloads and caller
idempotency keys for uncertain Native writes rather than invoking a model again.

## Cleanup and qualification gate

Azure cleanup was verified at **12:07:30 JST on 2026-09-18**, before the 13:52
deadline. Both deployments, account, SQL server, new resource group, and
account-scoped RBAC were deleted; the owned soft-deleted Foundry record was purged.
The local test database and six secret files were removed. No owned Azure trial
resources remain and no extra cloud calls are planned. The separately approved
local Ollama weights remain for future M2 work, not in Git.

Keep credentials in operator process-environment references, never common JSON
or repository files. Public documentation excludes private resource identifiers
and endpoints. Purge does not imply universal provider/platform-log erasure.

The nine local TLS cases, targeted Ruff, and separate runtime CA check are verified.
The full v25 suite and all runtime/installation/type/lint checks also passed;
exact-SHA native CI passed. The earlier 157-case template-unit run,
v24 ordinary 989-case suite, live trials, and published CI are distinct evidence,
not substitutes for the recorded v25 local result or the exact-SHA implementation CI.
Track new results in [current qualification status](../STATUS.md#v0025--schema-10).
