# Local Ollama, OpenAI, and Azure SQL inference profiles

[日本語](INFERENCE_PROFILES-jp.md) | [README](../README.md)

## Current v27 additions

The current service is **0.0.27 / API v1 / schema 13**. Explicit operator inference
now includes `pg-agmemory infer extract --config FILE` as well as inspect,
summarize and embed. HTTP and Azure generate support extraction; Azure Language
does not. Model proposals contain four fields and trusted code resolves exact
unique quote spans; returned candidates remain six-field, untrusted proposals.
See [ADR 0027](adr/0027-typed-extraction.md).

These profile files alone still do not authorize background processing. The
separate default-deny, local-only worker requires an administrator-pinned digest,
consent labels, allowed recipes and call/input/output limits:
[current worker operations](operations/README.md#schema-13-background-processing).
Remote OpenAI-compatible/Azure profiles remain explicit operator paths, not
automatic worker egress. Prompt changes require a new worker digest authorization.
Current live-model evidence and failed attempts are in [EVALUATION](EVALUATION.md).

## Retained v24/v25 profile evidence

The version/resource counts and eight-call measurements below are historical.
They do not describe v27's 38-resource surface or qualify its new extraction,
embedding jobs, compaction or evaluation results.

These profiles select an explicit provider call; they do not enable automatic
synthesis, extraction, ingestion, publication, or compaction. This is a practical
guide for the current v0.0.25/schema 10 follow-up, **not M2 completion**.
The recorded live runs used service 0.0.24, API v1, and schema 10.
The separate TLS packaging follow-up is not qualified by those runs.
**V25 full local qualification passed; native CI passed:** the Docker base sets `SSL_CERT_FILE`
and the runtime production smoke checks it plus a loaded root-CA store.
Targeted local real-libpq TLS and runtime CA checks passed.
The full Apple Container `./scripts/test-containers.sh` reported
**999 passed, 5 live skipped, 1 known warning, 494.49 s**; Ruff, mypy
**22 source files + 1 strict SDK consumer**, all four core/hook/sdk/providers
installation smokes, and **all production smokes including the new system-CA
smoke** passed. Implementation
[`adbead0ab42dfa4a5465f9464d3855c5f64d85c1`](https://github.com/rioriost/pg_agmemory/commit/adbead0ab42dfa4a5465f9464d3855c5f64d85c1)
passed exact-SHA [CI 35303758871](https://github.com/rioriost/pg_agmemory/actions/runs/35303758871):
**amd64 999 passed, 5 live skipped, 1 warning / 600.77 s;
arm64 999 passed, 5 live skipped, 1 warning / 797.75 s**.
Both native jobs passed Ruff, mypy **22+1**, all four installation profiles, and
all production smokes including system CA. This is implementation CI, not a
qualification claim for a later final-documentation commit. No Azure inference was rerun.
Stage `m2-selectable-inference`, 31 Native/SDK resources, four MCP tools,
`auto_synthesis: false`, and global `live_provider_qualified: false` remain.
See [ADR 0025](adr/0025-live-provider-qualification.md) and
[v25 qualification status](STATUS.md#v0025--schema-10).
The [provider contract](STATUS.md#selectable-inference-providers) and
[operator reference](operations/README.md#selectable-inference-providers) remain authoritative.

## Evidence boundary

**Exact Ollama and Azure Flexible Server profiles have bounded live contract
evidence; Azure cleanup is verified. This is not a quality-gate pass or blanket
provider certification.** The published Ollama/OpenAI profile baseline passed both
native CI architectures. Existing
[v24 qualification](STATUS.md#v0024--schema-10) covers synthetic HTTP and SQL
fixtures, separately from the live runs below. Neither that CI nor the template
alone establishes live compatibility or human quality certification.

| Route | Current scope and evidence |
| --- | --- |
| Local Ollama | Five live cases passed across the separate contract and Native lifecycle runs below; actual operator CLI checks also succeeded. There were **eight real model calls**, all with synthetic text. The ordinary suite, published baseline's native CI, and owned local-resource cleanup are complete. Configured digest labels are not automatic verification of installed weights. |
| OpenAI | Ready configuration template only. The user has not supplied `OPENAI_API_KEY`; no live OpenAI verification is claimed. |
| Azure Foundry + Flexible Server | The exact `azure_ai` 2.0.1 profile passed **5 live cases, 54 deselected, 1 known warning / 20.12 s**, plus CLI checks. Eight application inference calls, no application retries. **One English input produced a Spanish summary**; quality is not qualified. Cleanup was verified at **12:07:30 JST**. |
| HorizonDB | A separate live test is explicitly waived for this scope, not passed or qualified by that waiver. Existing synthetic SQL coverage is not live HorizonDB evidence. |

Each live result must identify its runtime, model artifacts, profile, topology,
and observed outcomes. A passing shape/digest check is not a retrieval-quality,
summary-faithfulness, privacy, cost, or production-readiness qualification.
`model_inference.live_provider_qualified` remains **false**: the recorded checks
apply only to their exact profiles/runtimes/model artifacts, not all versions,
models, or providers. OpenAI and HorizonDB remain live-untested.

### Recorded local contract evidence

On 2026-09-18 JST, the following separate checks used an **Apple Container Linux
arm64 client → guarded host bridge → host-loopback Ollama 0.34.1** path.
All input was synthetic text.

| Check | Observed result | Real model calls |
| --- | --- | --- |
| Four standalone EN/JA summary/embedding contract cases | **4 passed / 23.97 s** | 4 |
| Native embedding lifecycle, `[synthetic]` and `[live]` | **2 passed / 3.98 s**, including one live case | 2, from the live branch |
| Actual operator CLI `inspect`, `summarize`, `embed` | All succeeded; summary retained the not-approved negation and embedding returned 768 values | 2; `inspect` performs no inference |

That is **five live cases across two targeted runs, plus CLI checks**, not a
single five-case timed run or a full-suite result. The total is **eight real
model calls: four contract + two Native + two CLI**.

The Native run used an owned, pinned **PostgreSQL 18.6 / pgvector 0.8.6 tmpfs
container**, removed after the test. It exercised real canonical input → Qwen
768-vector → `PutEmbedding` → exact replay → vector recall → source purge →
`not_found` for replay and stale upload. The actual CLI checks are separate from
this explicit Native publication workflow.

The selected Qwen2.5 model returned the following contract-test summaries, both
with `status: "untrusted"` and exact input-digest matches:

| Sample | Observed summary |
| --- | --- |
| English | Project Cedar is paused. Deployment has not been approved. The next review is scheduled for September 20. |
| Japanese | Cedar計画は一時停止中です。デプロイは承認されていません。次回のレビューは9月20日です。 |

Agent inspection observed preserved negation and dates in **these two samples
only**; this is not human quality certification or evidence for other inputs.
The EN/JA Qwen3 embedding calls returned exactly 768 finite values with finite
nonzero norms, generated by the server's MRL dimension selection, not application
padding/truncation. The duration is a test-run record, not a performance benchmark.
These bounded results do not qualify other inputs, providers, or model spaces.

### Ordinary-suite qualification

The historical v24 full Apple Container `./scripts/test-containers.sh` run reported
**989 passed, 5 live skipped, 1 warning, 500.46 s**.
Ruff, mypy **22 source files + 1 strict SDK consumer**, all four
core/hook/sdk/providers installation checks/smokes, and all production smokes passed.
The five live skips are intentional in this ordinary run; the separate live
evidence above is not silently included in its 989 passes.
The 500.46 s measurement belongs to this local run, not to native CI.

### Published baseline native CI

Published v24 commit
[`aa364c3969fda48b52c8fef19c8794ec99cd354b`](https://github.com/rioriost/pg_agmemory/commit/aa364c3969fda48b52c8fef19c8794ec99cd354b)
passed exact-SHA [CI 35298758297](https://github.com/rioriost/pg_agmemory/actions/runs/35298758297):
**amd64 989 passed, 5 skipped / 685.52 s; arm64 989 passed, 5 skipped / 796.17 s**.
Both passed Ruff, mypy **22 source files + 1 strict SDK consumer**, all four
core/hook/sdk/providers installation checks, and all production smokes.
The five live cases were skipped in CI; the eight real model calls above belong
to the separate local Ollama runs. This CI predates the Azure template/trial and
does **not** qualify it or any subsequent changes.

## Configuration files

Run examples from the repository root with Python 3.12+ in an isolated environment.
Install from this checkout; this is not a claim of a separately published package:

```sh
python3 -m pip install '.[providers]'
```

The optional extra supplies HTTPX 0.28.1. It is still the core `pg-agmemory`
distribution, including its existing web/database dependencies.

| File | Purpose |
| --- | --- |
| [ollama.json](../examples/inference/ollama.json) | `local_http` at `http://127.0.0.1:11434/v1`, selected local text/embedding models, 120-second timeout, 512 maximum summary-output tokens. No API key setting. |
| [openai.json](../examples/inference/openai.json) | `openai_compatible` at `https://api.openai.com/v1`, bearer credential from `OPENAI_API_KEY`, 60-second timeout, 512 maximum summary-output tokens. |
| [azure-flexible-server.json](../examples/inference/azure-flexible-server.json) | `azure_ai` SQL profile with scoped live contract evidence; DSN environment reference only, extension 2.0.1 pin, 60-second timeout. Tested resources are deleted; language-quality and TLS-packaging limits are recorded below. |
| [.env.example](../examples/inference/.env.example) | Placeholder only; documents the environment variable, not a usable credential. |

The CLI reads process environment variables; it **does not automatically load
`.env` files**. Supply the real OpenAI key through an approved secret manager or
your local environment, never through JSON, a committed file, command-line
arguments, or captured logs. Do not send requests using the placeholder.
Keep any real `.env` and generated outputs outside version control. The Azure DSN
reference follows the same rule: no credentials or private server endpoints in JSON.

One profile is selected per command. You may summarize with the local profile
and embed with the OpenAI profile, or create trusted single-operation profiles.
This is explicit selection, not fallback. A profile requires at least one model;
remove `max_output_tokens` when creating an embedding-only profile.

## Local Ollama

| Role | Selected model | Operator-supplied artifact metadata |
| --- | --- | --- |
| Summary | `qwen2.5:7b` | 7.6B, Q4_K_M, **4683087332 bytes** (approximately 4.7 GB). |
| Embedding | `qwen3-embedding:0.6b` | 595.78M, Q8_0, **639150858 bytes** (approximately 639 MB). |

The corresponding profile revisions are:

- `qwen2.5:7b`: `ollama-sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`
- `qwen3-embedding:0.6b`: `ollama-sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`

These are artifact records, not RAM requirements, inference results, or latency
guarantees. Both selected models have Apache 2.0 licenses. Upstream describes
English/Japanese support for Qwen2.5 and 100+ languages for Qwen3 embedding.
The 0.6B embedding model supports MRL dimensions 32–1024; this profile requests
**768**. The [Qwen3 0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
is the dimensionality reference; do not apply the 8B model's dimensions to 0.6B.
The application rejects a response with the wrong dimension; it does not pad or
truncate a 1024-dimensional response to make it fit.

Use a server bound only to host loopback. If an authorized server is not already
running, start one in a separate terminal:

```sh
OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

The following explicit downloads are authorized for the current local experiment.
Skip them when the intended artifacts are already installed:

```sh
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen2.5:7b
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3-embedding:0.6b
```

Downloads contact the model registry; they are not an automatic application step.
Tags can change. **After a pull and before publishing embeddings**, manually check
the local tag digests:

```sh
curl --fail --silent --show-error http://127.0.0.1:11434/api/tags
```

For each selected model, compare the returned `sha256:...` digest with the profile
revision after removing its `ollama-` prefix. The adapter does **not** perform this
check or verify remote weights. A mismatch requires an explicit model-space
decision; do not publish altered-space vectors under the old identity/revision,
or relabel and republish old vectors as if the model were unchanged.
The approved downloaded weights, approximately **5.32 GB decimal** in total,
are retained for future M2 tests, **not committed**.
The Native lifecycle's disposable database container has been removed.
Ollama was off before this experiment. The owned Ollama server, host relay, and
client relay were stopped using their tracked process IDs; the named live client
container and test image were removed. The full test script also cleaned up its
own containers/images. Only the approved model weights remain from the temporary
runtime setup.
Stop only owned processes/resources, never unrelated ones.

Run the following in the **same host network context** as Ollama. Input is one
JSON object, not raw text. These samples contain only synthetic text:

```sh
pg-agmemory infer inspect --config examples/inference/ollama.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused. Deployment is not approved."}' |
  pg-agmemory infer summarize --config examples/inference/ollama.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused."}' |
  pg-agmemory infer embed --config examples/inference/ollama.json
```

HTTP `inspect` only constructs/closes a client and validates configuration and
credential-header shape. It sends no request and proves neither connectivity
nor model availability. The subsequent two commands actually invoke models.

### Apple Container is a different network context

Container `127.0.0.1` is **not** the host's `127.0.0.1`. The checked-in host profile
cannot directly reach host Ollama from Apple Container using container localhost.
The verified local experiment used two guarded temporary relays across the
loopback/network boundary, without broadening Ollama's bind or production
`local_http` validation. The passing live cases and CLI establish the bounded client →
guarded host bridge → host-loopback path recorded above, not a generic container
networking recipe. The temporary relays have now been stopped. Detailed relay
configuration is not published here; do not infer unreported interfaces or ports.

Do not replace loopback with `0.0.0.0` or weaken provider URL checks to make this
example work. Run the CLI on the host, or use a separately reviewed and verified
test topology. The relays are not an installed product feature.

## OpenAI template

The [OpenAI profile](../examples/inference/openai.json) selects the text snapshot
`gpt-4.1-mini-2025-04-14`, with operator revision `2025-04-14`.
Embedding uses `text-embedding-3-small`, requesting 768 dimensions, with operator
revision `operator-openai-text-embedding-3-small-768-v1`.
The embedding revision is a local declaration, not an immutable remote-model pin.
Model access, current service terms, quotas, and billing remain the operator's
responsibility; no key or live OpenAI result is included here.

After supplying `OPENAI_API_KEY` and approving the external, potentially billed
calls, use the same interface:

```sh
pg-agmemory infer inspect --config examples/inference/openai.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused. Deployment is not approved."}' |
  pg-agmemory infer summarize --config examples/inference/openai.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused."}' |
  pg-agmemory infer embed --config examples/inference/openai.json
```

These calls use OpenAI-compatible `chat/completions` and `embeddings`, not a
universal vendor/model adapter. The application does not retry, redirect, use
proxy environment settings, or switch providers automatically. A lost response
can leave billing unknown; cancellation is not proof that inference or charges stopped.

## Azure Flexible Server template

### Recorded Azure live evidence

The approved trial used **West US 3**, AI Services **S0**, and PostgreSQL **18.6,
B1ms, 32 GiB** with installed `azure_ai` **2.0.1**. Summary inference used
`gpt-4.1-mini`, version `2025-04-14`, GlobalStandard capacity **10**; embeddings used
`text-embedding-3-small`, version **1**, GlobalStandard capacity **1**, requesting
768 dimensions. Authentication used the server's managed identity with
account-scoped **Cognitive Services OpenAI User**, **API-key authentication
disabled**, and a separate restricted SQL login.

An Apple Container client with **mounted `aa364c3` code** ran the five selected
live cases: **5 passed, 54 deselected, 1 known warning / 20.12 s**.
Actual CLI `inspect`/`summarize`/`embed` also succeeded. There were **eight
application inference calls** (six from pytest, two from CLI) and **no application
retries**; this is not a count or guarantee of billable upstream calls.
SQL `inspect` returned `contract_verified: true`, `inference_tested: false`:
inspection itself does not invoke a model.
Summaries remained `untrusted`; embeddings contained 768 finite values with
finite nonzero norm. The Native canonical-input/upload/replay/recall/purge and
post-purge rejection lifecycle passed against a **separate local PostgreSQL 18.6 /
pgvector 0.8.6** database. No MemoryDB schemas were created in the cloud server.

**Observed quality deviation:** one English-input summary was returned in
**Spanish**, despite the original-language instruction. The passing tests check
contracts, not language fidelity, grounding, or semantic quality. Do not suppress
this deviation or treat the run as a language/grounding/quality-gate pass.
Generated summaries require review and remain untrusted; `model_inference.live_provider_qualified`
stays **false**. No HorizonDB or Azure Language calls were tested, and direct
OpenAI API calls remain untested.

The Azure run used **TLS 1.3 with `verify-full` and an explicit OS CA-bundle path**.
It is separate from CI 35298758297, which skipped live cases, and does not test the
new Docker `SSL_CERT_FILE` default. The earlier **2.0.0 template** was packaged
without a host mount and passed Ruff, mypy **22 + 1**, and **157 unit contracts,
13 integration cases deselected / 2.26 s**; that was existing coverage plus one
template-parse case, not a live Azure run. Historical 2.0.0 documentation/fixture
evidence is preserved separately from the observed and tested **2.0.1**.
No new full-suite/CI result for the TLS packaging follow-up is claimed.

The [Azure profile](../examples/inference/azure-flexible-server.json) selects:

| Setting | Declared value |
| --- | --- |
| Backend/product | `azure_ai` / `flexible_server` |
| SQL connection reference | `database_url_env: "PGAG_AZURE_INFERENCE_DATABASE_URL"` |
| Required extension pin | `azure_extension_version: "2.0.1"` |
| Summary | `azure_summary_mode: "generate"`; deployment `pgag-summary`, revision `gpt-4.1-mini-2025-04-14` |
| Embedding identity | `text-embedding-3-small`, revision `azure-openai-text-embedding-3-small-1-768-v1`, 768 dimensions, cosine, `l2-f32-v1` |
| Embedding deployment | `embedding_target: "pgag-embed"` |
| Per-call timeout | `timeout_seconds: 60` |

Deployment names in this template are reusable operator-selected labels, not
account identifiers or a promise that resources still exist: the tested resources
were deleted. Revision labels
record operator intent; the adapter does not cryptographically verify cloud model
versions or alias upgrades. On Flexible Server, `embedding_target` is a deployment
name; HorizonDB's registry-alias contract is different and is not tested here.
SQL forbids `max_output_tokens`; do not copy the HTTP profile's 512-token setting.

### Privilege separation and setup

The operations owner provisions a **new, temporary, dedicated PostgreSQL 18
inference server and cloud model resources**, separate from canonical MemoryDB.
Use synthetic text only. Provisioning, administrator setup, model calls, and
cleanup belong to that one owner; the profile/CLI does not create Azure resources.
Keep subscription IDs, resource names, actual service endpoints, DSNs, and
credentials out of public documentation and captured example output.

1. **Administrator setup:** allowlist/install `azure_ai`, verify the installed
   version against the 2.0.1 pin, deploy the approved models, and configure their
   routing outside the application. Restrict network access to the approved test
   client/path; do not open the server broadly to make a test pass.
   See [extension setup](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-overview).
2. **Upstream identity:** enable the Flexible Server's system-assigned managed
   identity and grant **Cognitive Services OpenAI User** at the dedicated model
   resource scope, not across the subscription. The administrator configures
   `azure_openai.auth_type` as `managed-identity` and the approved endpoint.
   See [managed identity setup](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-enable-managed-identity-azure-ai).
   This is server-to-model authentication; it does not authenticate the CLI's
   PostgreSQL connection or grant SQL privileges.
3. **Separate runtime login:** create a new least-privilege SQL login, distinct
   from the setup administrator. Grant only required database `CONNECT`, schema
   `USAGE`, and `EXECUTE` on the deployed compatible inference functions; review
   effective inherited/PUBLIC grants. Do not give it superuser, `BYPASSRLS`,
   canonical memory ownership, or membership in `azure_pg_admin`,
   `azure_ai_settings_manager`, or `model_registry_manager`.
   Do not solve a failed runtime check by substituting the admin DSN.
4. **Client connection:** supply that runtime login's DSN through
   `PGAG_AZURE_INFERENCE_DATABASE_URL` using an approved secret channel.
   The adapter enforces TLS `verify-full` with system CA or an explicitly approved
   CA file. Its dedicated autocommit connection is not a canonical memory
   transaction/session lock; each function statement still has a SQL transaction.
   Do not disable TLS or weaken the extension pin/catalog guards.

### TLS trust-store known issue

The tested psycopg-binary client failed
with `sslrootcert=system`. An explicit DSN setting
`sslrootcert=/etc/ssl/certs/ca-certificates.crt` worked in the Linux client with
`sslmode=verify-full` and TLS 1.3 retained throughout the Azure live run.
This was an operator-selected CA file, not an
automatic fallback or TLS downgrade. Keep the complete DSN in the process
environment, never in the common JSON profile or repository.
Offline inspection found missing compiled-in CA-file defaults in two bundled
OpenSSL builds; both honor `SSL_CERT_FILE`. On Linux with this readable OS bundle,
the explicit trust-store setting is:

```sh
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
```

The v25 Docker base sets this environment variable; its non-root runtime smoke
checks the environment value and a populated root-CA store. **Nine local real-libpq
TLS cases and targeted Ruff passed; a separate non-root v25 runtime check confirmed
the environment value and 150 trusted CAs.** The full local suite and all production
smokes also passed; native CI passed.
The Azure live run used the explicit DSN CA file, **not this environment
fix**; do not claim that the latter was live-Azure tested. Certificate/hostname
verification must stay enabled. No cloud reprovisioning or extra cloud calls are
planned for the local TLS regression.

The adapter does not install/configure the extension or read key settings/model
registries. Before each operation it checks the exact extension version,
extension-owned compatible non-set-returning overloads, and SQL permissions.
For Native lifecycle tests, `PGAG_TEST_DATABASE_URL` is a **separate local disposable
canonical test database**, not this Azure inference DSN and never production data.
This does not certify full MemoryDB hosting on Azure: the canonical PostgreSQL
18.6 / pgvector 0.8.6 requirements remain unchanged.

### Inspect before explicit inference

After administrator setup, use the least-privilege login for a live, read-only
catalog inspection. It does not invoke a model:

```sh
pg-agmemory infer inspect --config examples/inference/azure-flexible-server.json
```

A successful SQL `inspect` does not prove upstream identity propagation, model
access, quota, service availability, or inference success. Only after approval
for the potentially billed calls, invoke synthetic inputs explicitly:

```sh
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused. Deployment is not approved."}' |
  pg-agmemory infer summarize --config examples/inference/azure-flexible-server.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused."}' |
  pg-agmemory infer embed --config examples/inference/azure-flexible-server.json
```

These are retained examples for a future separately approved setup, not a reason
to recreate the deleted trial resources or make extra calls now. Summaries remain
untrusted; embeddings must be exactly 768 finite values with nonzero norm.
SQL embeddings request `dimensions => 768` and `max_attempts => 1`.
The application never retries, but `azure_ai.generate` has no verified retry or
output-token knob. One `MATERIALIZED` invocation does not guarantee one billable
upstream call, and cancellation does not prove charges stopped. Preserve exact
generated payloads for any subsequent idempotent Native write.
See [Flexible embeddings](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-openai)
and [AI functions (preview)](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-ai-functions);
documented capabilities do not replace the installed catalog or live evidence.

### Two-hour cleanup plan

The approved window is **2026-09-18, 11:52–13:52 JST**, including creation, testing,
and cleanup **before 13:52 JST**. Cleanup was verified at **12:07:30 JST**:
both model deployments, the AI Services account, SQL server, newly created
resource group, and account-scoped RBAC assignment were deleted; the owned
soft-deleted Foundry account record was purged. The local test database and
**six secret files** were removed. No owned Azure trial resources remain and
no additional cloud calls are planned. The window was an operator deadline,
not an automatic resource TTL.

The operations owner must stop calls, delete owned model deployments first,
remove the dedicated SQL server and other owned trial resources, and purge the
soft-deleted Foundry/AI Services account. Verify both resource deletion and the
absence of its soft-deleted account; deleting the resource group alone is not
proof of purge. Purge is irreversible and requires separate management-plane
permission, never a privilege for the SQL runtime login or inference identity.
[Microsoft's recovery/purge guidance](https://learn.microsoft.com/en-us/azure/ai-services/recover-purge-resources)
also warns that provisioned deployment charges can continue until purge.
Record sanitized test and cleanup evidence without private identifiers.
Account purge is not a blanket guarantee about provider/platform log retention.

## Model space, exact input, and explicit publication

All three embedding profiles declare `dimensions: 768`, `distance_metric: "cosine"`,
and `normalization: "l2-f32-v1"`. Equal dimensions or similar model names do **not**
make Qwen, OpenAI, and Azure vectors interchangeable. Keep the full model
identity/revision and input format
consistent across generation, stored embeddings, and vector queries. Assign a
new identity/revision for a changed model space; do not relabel old vectors.
Revision strings, even digest-shaped strings, are not cryptographic enforcement.

`InferenceInput` preserves the supplied text bytes; `input_digest` is SHA-256 of
that exact UTF-8 text. No task prefix is silently added for Qwen or another model.
For canonical memory, pass the exact text returned by `embedding_input` to the
provider. Do not prepend instructions, normalize whitespace, or invent a digest
for a different input. Any different input-format design needs explicit versioning
and compatibility work, not a hidden prefix in this profile.

CLI output has a success/error envelope. A successful summary is still
`status: "untrusted"`, not evidence-backed memory or an approval. A generated
embedding is not automatically uploaded. To publish, explicitly use
`embedding_input` → provider `embed` → `PutEmbedding`, preserving current
ACL, revision, digest, model-space, and purge checks.
See the [explicit upload workflow](operations/README.md#selectable-inference-providers).
After an uncertain memory write, retain the exact generated payload and caller
idempotency key; do not rerun the model to reconstruct the retry.

## Opt-in live checks

[Live tests](../tests/live/test_inference.py) select a profile with
`PGAG_LIVE_PROVIDER_CONFIG`; **an unset variable skips live inference**.
A selected profile with invalid content or a missing required API key/SQL DSN fails;
it does not silently skip. Provider failures remain sanitized.
A profile without a text or embedding model skips the corresponding cases.
Set this variable only for a deliberately authorized live run.

With both model capabilities configured, `-m live` selects **five cases making
six model calls**:

| Cases | Model calls | Scope |
| --- | --- | --- |
| Four in `tests/live/test_inference.py` | Four: EN/JA summary and EN/JA embedding | Summary identity/digest/nonempty untrusted output; embedding identity/digest/exactly 768 finite values with a finite nonzero norm. |
| One `[live]` case in [test_providers.py](../tests/test_providers.py) | Two additional embedding calls | The existing Native embedding lifecycle shares its logic with `[synthetic]`: canonical input, explicit upload, same-key replay, vector recall, and rejection of replay/stale upload after purge. |

The Native case requires `PGAG_TEST_DATABASE_URL` pointing to a **disposable
PostgreSQL database** with the test harness's setup privileges. Never use a
production memory database. Without that database, its fixture skips: four
model-only cases are not a complete five-case qualification.

The recorded Ollama and Azure profile checks used **Apple Container**, including
the four model-only cases; Azure inference used the explicit DSN CA file and a
separate local canonical test database. Inside a separately approved, prepared
test container, from the repository root,
set `PGAG_LIVE_PROVIDER_CONFIG` to a profile path available in that container and
prepare the disposable database and, for host Ollama, the reviewed relay topology.
A host environment variable or the checked-in host-loopback endpoint does not set up that topology.
With test dependencies already installed, the selector is:

```sh
python3 -m pytest -q -m live tests/live/test_inference.py tests/test_providers.py
```

These are contract/lifecycle checks using synthetic text, not semantic-quality
evaluations. Only the Native lifecycle case explicitly publishes test memory.
The published Ollama/OpenAI baseline's two profile unit cases are included in its
verified **989 ordinary passes (987 retained + two profile cases)**, with five
live cases skipped in that run. See [ordinary-suite qualification](#ordinary-suite-qualification) for the
measured result, separate from the opt-in live runs and the new Azure work.
Selecting OpenAI can incur charges and requires the user's key; no live OpenAI
run is claimed. Do not enable live inference globally in CI or infer a passing
run from the presence of this command.

## Licenses and upstream references

The repository's MIT license does **not** relicense model weights or external
API services. The selected Qwen artifacts are separately licensed under Apache
2.0; preserve applicable notices and check the specific artifact's terms.
The official Qwen3-Embedding-0.6B model-card metadata confirms `apache-2.0`.
OpenAI and Azure services remain subject to their own terms, access controls,
data handling, retention, and charges. Local download authorization is not
permission to send confidential memory to any provider.

- [Ollama Qwen2.5 7B](https://ollama.com/library/qwen2.5:7b)
- [Ollama Qwen3 embedding 0.6B](https://ollama.com/library/qwen3-embedding:0.6b)
- [Qwen3 embedding 0.6B model card and license](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [OpenAI GPT-4.1 mini snapshot](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [Azure SQL boundaries and official references](adr/0024-selectable-inference.md)
