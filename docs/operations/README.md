# Operations for the initial slice

[日本語](README-jp.md) | [Project README](../../README.md) | [Current contract](../STATUS.md)

**Not a production runbook or a verified disaster-recovery procedure.**
Use approved, sanitized, disposable test data for this initial release.
Destructive operations—including purge drills, schema resets, and restore
experiments—must run only against disposable test databases, never business
databases or real user histories.

## Bootstrap and role separation

Use the pinned prebuilt upstream pgvector DB profile below and the application
image built from the repository's existing `Dockerfile`.
The CLI is `pg-agmemory`; the import package is `pg_agmemory`.
The local checkout is `pg_agmemory`; GitHub is `rioriost/pg_agmemory`.
The current bounded milestone is **v0.0.11/schema 8 pgvector exact/hybrid retrieval**.
**Implementation, local Apple Container, and both native Docker architectures are verified**.
New `008_pgvector.sql` requires **`vector` 0.8.6 in `public`** and rejects an
existing extension at another version or in another schema.
Python dependencies stay unchanged apart from project-version metadata.
**Historical v0.0.8:** 214 tests and production smokes passed in Apple Container
and native Docker amd64/arm64. These are not v0.0.9 results.
M0–M3, MVP, production, performance,
quality, DR, and full-erasure acceptance remain incomplete.
**Historical v0.0.7 only:** Apple Container and native Docker amd64/arm64 each passed **144 tests**
(2 existing warnings), Ruff, strict mypy (12 source files), and all three non-root
production smokes: Japanese tokenizer, API HTTP, and actual worker CLI `--once`
idle execution. Final SHA, CI logs, and timings are in
[validation evidence](../STATUS.md#validation-evidence).

The historical v0.0.7 final lock retained the existing package-feed registry. All
36 packages' versions, dependency metadata, and artifact hashes are byte-for-byte
equivalent to the tested PyPI-resolved lock. Relative to v6, only Janome 0.5.0 was
added and the project version became v0.0.7; no unrelated upgrades or registry
migration occurred. Native CI built that retained-registry lock. This is not a
package-count or validation claim about v0.0.8/v0.0.9. Use the current locked build;
the optional MCP extra pins `mcp==2.2.0` and `httpx==0.28.1` and is included in
both Docker test and runtime stages. v0.0.11 retains `hook` in both stages;
`pg-agmemory[hook]` pins `httpx==0.28.1` **without the MCP SDK**.
The container-check script requires runner-side `jq` for **both Apple Container
and Docker**, including disposable smoke configuration.

| Setting | Consumer | Purpose |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | Administrative CLI only | Migration, offline all-tenant lexical rebuild, and private tenant/principal/scope provisioning |
| `PGAG_DATABASE_URL` | API and worker runtime | Dedicated restricted login belonging to `pgag_runtime` |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | Static PEM RSA verification key, at least 2048 bits; never the signing private key |
| `PGAG_JWT_ISSUER` | API runtime | Exact trusted issuer |
| `PGAG_JWT_AUDIENCE` | API runtime | Exact audience for this service |
| `PGAG_MCP_API_URL` | Local MCP adapter only | Fixed trusted Native API HTTPS origin or loopback HTTP origin; no URL credentials, application path, query, or fragment |
| `PGAG_MCP_API_TOKEN` | Local MCP adapter only | Fixed Native API audience bearer token; supplied securely at startup, never per call |

1. Confirm that the admin URL identifies the intended empty, disposable Memory
   DB. Use the pinned upstream DB image with the matching extension available.
   An operator-managed alternative must supply the same extension version/schema;
   no host-APT or source-build procedure is provided.
   Run `pg-agmemory migrate` from the matching application image. The migration is
   transactional and version-recorded in `public.pgag_schema_migration`.
   The migration loop accepts only sequential supported history and skips
   applied versions on rerun; lock acquisition has a 5-second timeout.
   Any existing DB upgrade requires the maintenance procedure below.
2. The unchanged `src/pg_agmemory/storage/001_initial.sql` and
   `src/pg_agmemory/storage/002_assertion_revisions.sql` and
   `src/pg_agmemory/storage/003_checkpoints.sql` and
   `src/pg_agmemory/storage/004_tool_effects.sql` and
   `src/pg_agmemory/storage/005_relational_graph.sql` and
   `src/pg_agmemory/storage/006_durable_jobs.sql`, followed by additive
   `src/pg_agmemory/storage/007_japanese_fts.sql` and new `008_pgvector.sql`, are installed package
   resources. Do not substitute the illustrative DDL in the plan or expect
   generated files. The administrator must be superuser or a qualified
   `BYPASSRLS` role with the required ownership/DDL, role/schema creation, and
   `btree_gist`/matching pgvector extension installation rights. Bypass alone does not grant DDL.
   Backfills, including migration 007's Python rebuild, use `row_security = off`
   to fail closed if RLS would filter rows; that setting does not bypass forced
   RLS by itself.
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
   startup, including owner-role membership. It also requires the schema
   ledger to equal `[1, 2, 3, 4, 5, 6, 7, 8]` and extension `vector` 0.8.6 in `public` exactly;
   mismatches are rejected. The worker reuses these role/schema/extension checks without
   requiring the API's JWT settings.

Keep the admin URL, signing private key, tokens, and tenant HMAC secrets out of
source control, issue reports, logs, and the runtime environment where not
needed. Never hand runtime DB credentials to agents as an arbitrary SQL entry
point: the service's fixed queries and trusted identity context are part of the
authorization boundary.

## Schema 8 pgvector upgrade

**v0.0.11 application/migration checks passed; production qualification remains incomplete.**

Adopt the prebuilt image:

```text
docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a
```

[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6) is the verified
**2026-07-29** stable release, official tag commit
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`
([pinned changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)).
It uses the **PostgreSQL License**; preserve upstream license files on redistribution.
Both final native images retain `/usr/share/doc/pgvector/LICENSE`, verified
byte-identical to the pinned upstream license, SHA-256
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`.
Inspection of both final amd64/arm64 images verified PostgreSQL
**18.6-1.pgdg12+2**, native ELF, and `vector.control` **0.8.6**.
**The PostgreSQL version is unchanged, but the DB image/base digest is different**
from the old library PostgreSQL profile. This is a new pinned upstream vector DB
profile, not a source build on an unchanged base. No new DB Dockerfile, mutable
host-APT installation, or source-build workflow is part of the implemented profile.

1. Use the full image tag **and digest** above, not an arbitrary latest image.
   For an operator-managed PostgreSQL alternative, the matching extension must
   be available before migration; this document supplies no host-install workflow.
2. Stop/drain **all old/new APIs, workers, adapters, and hook launches**, including
   replicas and automatic restarts. The migration lock does not protect against
   an old schema-7 process continuing to serve. No rolling coexistence is supported.
3. Preserve a backup, application/schema/extension versions, and current deletion
   and ACL records. Rehearse only on disposable databases; restore quarantine and
   DR/full-erasure gaps remain.
4. With the migration administrator and matching v0.0.11 application, run
   `pg-agmemory migrate`. Apply `008_pgvector.sql` after unchanged 001–007;
   older databases still need migration 007's lexical backfill.
   Migration requires `vector` **0.8.6 in `public`** and refuses an existing
   extension with the wrong version/schema.
   `migrate` also validates the extension when schema 8 is already recorded;
   an already-applied migration does not bypass the check.
   New episode/assertion-revision vector projections have forced RLS,
   canonical `ON DELETE CASCADE`, and runtime **SELECT/INSERT only**.
   **No existing data receives embedding backfill**.
5. Confirm exact history `[1, 2, 3, 4, 5, 6, 7, 8]` and extension `vector` 0.8.6 in `public`
   before starting only matching v0.0.11 APIs/workers. Check authenticated
   capabilities, lexical compatibility, synthetic vector/hybrid ranking,
   coverage, RLS/time filters, replay/purge, and retained adapters before traffic.
   API/worker startup rejects schema/extension mismatches.
6. On failure, leave processes stopped. Do not start old images against changed
   schema or assume downgrade support. No automatic embedding rebuild/provider
   exists; lexical reindex does not populate vectors.

This is a **schema-8 migration**, unlike the historical v0.0.10 application-only
update below. Artifact/version/license inspection and application checks are verified
separately. See [the current contract](../STATUS.md#pgvector-exact-and-hybrid-retrieval)
and [ADR 0011](../adr/0011-pgvector-retrieval.md).

## Explicit vector operations

Use currently authorized episode/assertion revisions only. Read-only
`POST /v1/embedding-inputs` takes Explain `{memory_id, revision}` (default **1,
not latest**) without an idempotency key. It returns private canonical text and
its SHA-256 UTF-8 digest under `memory-content-v1`. Do not log it or send it to
third parties without explicit approval. The digest does not attest model
provenance, semantic support, or quality.

Upload via Native `POST /v1/embeddings` with a retained caller-owned
`Idempotency-Key`, exact digest, caller-declared model name/revision (1–256
characters each), fixed 768/cosine/`l2-f32-v1` metadata, and 768 finite JSON numbers.
Server normalization computes float64 then stores pgvector float32.
No zero/non-finite vector, boolean, numeric string, truncation, or dimension
coercion is accepted. Scope/identity come from the canonical parent and require
read/write access. Preserve the exact key/request before dispatch; do not log them.

Each parent revision/model namespace is immutable. Identical normalized
float32 vector/digest deduplicates across keys; changes conflict
(`409 embedding_conflict`), and digest mismatch is `409 embedding_input_mismatch`.
Use a new model revision for replacement. The **8-model-versions-per-canonical-
revision** cap rejects a ninth with `422 embedding_limit_exceeded`, but permits
existing duplicates. The upload response has `{memory_id, revision, model, input_digest}`,
not an independent embedding ID. **The stored idempotency result is only
`{memory_id, revision}`**, with no plaintext digest/model names/vectors in the
receipt. The full response model/digest is rebuilt from currently readable
canonical input and its matching projection; request HMACs/opaque anchors persist.
Replay rechecks live parent and projection. A live parent with an
administrator-removed projection yields **409 `embedding_unavailable`**, not
projection recreation. Projection/idempotency/audit writes are atomic.

Lexical remains default and rejects a vector query; vector-only requires empty
text plus a vector; hybrid requires nonempty text plus a vector. Exact cosine is
computed only after materializing current ACL/time-eligible candidates; hybrid
uses deterministic **RRF k=60**, not an approximate neighbor index.
Omitted `as_of`/`known_at` are frozen once before selection and coverage, so both
paths use the same resolved times across future boundaries; explicit times are
unchanged. UUID breaks ties in actual distance/score, without guaranteeing
bitwise-identical arbitrary floating-point results/rankings across all CPUs.
Do not mix model name/revision spaces. Surface `vector_incomplete`,
`lexical_incomplete`, `retrieval_complete`, and Native empty/budget outcomes:
missing visible vectors are not a silently successful complete index.
Ranking metadata is not confidence. The whole-JSON byte budget and 5 s DB
statement timeout remain; neither measures semantic quality or a latency SLO.
Response defaults add `MemoryItem.retrieval: null`, `retrieval_mode: "lexical"`,
`embedding_model: null`, and `coverage.vector_incomplete: false`.
Non-null `MemoryItem.retrieval` contains `method` (`exact_cosine`/`rrf-60`),
`lexical_rank`, `vector_rank`, `vector_distance`, and `fusion_score`, with nullable
values where appropriate. **Lexical behavior is preserved, not byte-for-byte
HTTP response/schema shape**. Adapt strict consumers to these additive fields.
No Python SDK dependency is added; the service uses raw parameter-bound vector casts.
Capabilities add `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"`.

Canonical purge cascades vectors/digests/declared model names with lexical data.
There is no standalone model registry retaining this metadata.
Vectors have no independent provenance vertex or deletion count. There is no
projection-only delete endpoint or automatic generation/rebuild. Retained anchors
and current ACL/deletion checks still prevent resurrection; host/backup/WAL erasure
is not certified. MCP retains four tools and only gains Recall arguments;
embedding input/upload are Native-only. Hook, Observe, capture, jobs, and workers
never generate embeddings; the hook remains lexical-only.
It rejects Native responses with non-lexical `retrieval_mode`, non-null
`embedding_model`/item `retrieval`, or true `coverage.vector_incomplete`;
unexpected vector output is an error, not a silent downgrade.

### Synthetic vector example

**Draft request example, not a production model or retrieval-quality benchmark.**
In a dedicated disposable scope containing only synthetic data, use unchanged
`POST /v1/observe` to create an episode with content exactly
`synthetic vector fixture`. Supply trusted operator environment values:
`PGAG_DEMO_API_URL` (Native origin), `PGAG_DEMO_API_TOKEN` (Native-audience token),
`PGAG_DEMO_SCOPE_ID`, `PGAG_DEMO_MEMORY_ID` (that episode UUID), and
`PGAG_DEMO_EMBEDDING_KEY` (retained caller-owned key).
These environment-variable names belong only to this example.
Do not derive startup settings from prompts, enable shell tracing, or log content/tokens.
Reusing the example after uncertainty must keep its key/body.

```bash
python - <<'PY'
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("redirect disabled")

try:
    base = os.environ["PGAG_DEMO_API_URL"].rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
    ):
        raise ValueError("invalid origin")
    token = os.environ["PGAG_DEMO_API_TOKEN"]
    memory_id = str(uuid.UUID(os.environ["PGAG_DEMO_MEMORY_ID"]))
    scope_id = str(uuid.UUID(os.environ["PGAG_DEMO_SCOPE_ID"]))
    key = os.environ["PGAG_DEMO_EMBEDDING_KEY"]
    if not token or not 1 <= len(key) <= 256 or not all(33 <= ord(c) <= 126 for c in key):
        raise ValueError("invalid operator configuration")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def post(path, body, idempotency_key=None):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with opener.open(request, timeout=10) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("response too large")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid response")
        return result

    source = post("/v1/embedding-inputs", {"memory_id": memory_id, "revision": 1})
    expected = {
        "memory_id": memory_id, "revision": 1, "type": "episode",
        "text": "synthetic vector fixture", "input_format": "memory-content-v1",
    }
    if any(source.get(name) != value for name, value in expected.items()):
        raise ValueError("not the synthetic fixture")
    digest = hashlib.sha256(expected["text"].encode("utf-8")).hexdigest()
    if source.get("input_digest") != digest:
        raise ValueError("digest mismatch")
    model = {
        "name": "synthetic-basis-demo", "revision": "basis-v1", "dimensions": 768,
        "distance_metric": "cosine", "normalization": "l2-f32-v1",
    }
    values = [1.0] + [0.0] * 767
    receipt = post("/v1/embeddings", {
        "memory_id": memory_id, "revision": 1, "input_digest": digest,
        "model": model, "values": values,
    }, key)
    if any(receipt.get(name) != value for name, value in {
        "memory_id": memory_id, "revision": 1, "model": model, "input_digest": digest,
    }.items()):
        raise ValueError("invalid receipt")
    for mode, query in (("vector", ""), ("hybrid", "synthetic")):
        result = post("/v1/recall", {
            "scope_ids": [scope_id], "query": query, "mode": "explicit", "purpose": "synthetic_fixture",
            "token_budget": 2000, "max_items": 20, "search_profile": "simple-v1",
            "retrieval_mode": mode, "vector_query": {"model": model, "values": values},
        })
        coverage = result.get("coverage")
        names = ("retrieval_complete", "vector_incomplete", "lexical_incomplete")
        if result.get("retrieval_mode") != mode or not isinstance(coverage, dict):
            raise ValueError("invalid recall response")
        if any(type(coverage.get(name)) is not bool for name in names):
            raise ValueError("invalid coverage")
        print(json.dumps({"retrieval_mode": mode, "synthetic_only": True,
                          "coverage": {name: coverage[name] for name in names}}))
except (KeyError, ValueError, TypeError, OSError, urllib.error.URLError):
    print("Synthetic vector example failed; inspect sanitized Native diagnostics.", file=sys.stderr)
    raise SystemExit(1) from None
PY
```

This uses only Python's standard library and the trusted Native API; **no external
model, registry, provider, or pgvector Python package** is invoked. It deliberately
uses a mathematical basis vector, checks synthetic canonical input/digest, and
prints only fixed coverage fields—not private text, vectors, receipts, or errors.
Proxy environment and redirects are disabled; HTTPS uses default TLS verification.
The illustrative 10 s I/O timeout is not a total deadline or production SLO.
Inspect incomplete coverage explicitly; adding only one projection never proves
a larger corpus is complete. Do not label this a semantic embedding model.

## Atomic structured capture operations

**Retained capture contract, verified in v0.0.11.** Bootstrap Native roles
as above and use matching service `0.0.11`, API `v1`, schema `8`.
The historical v0.0.10 stage `m2-atomic-capture` did not complete M2.
Capture is a Native route, **not an MCP tool or automatic recall-hook action**.
The new schema-8 migration is separate from capture semantics; capture never generates embeddings.

### Explicit request example

Use approved, sanitized, disposable data. The operator supplies `MEMORY_URL`
(trusted API origin), `TOKEN` (Native-audience token), `SCOPE_ID` (authorized
UUID), `SOURCE_EVENT_ID` (stable source identity), and `CAPTURE_KEY` (caller-owned
idempotency key). These are placeholders, not embedded credentials.
Retain the exact request/key securely before sending; do not enable shell tracing
or copy tokens/content into logs or issue reports.

```bash
curl --fail-with-body "${MEMORY_URL%/}/v1/captures" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Idempotency-Key: ${CAPTURE_KEY}" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<JSON
{
  "episode": {
    "scope_id": "${SCOPE_ID}",
    "source_namespace": "atomic-capture-demo",
    "source_event_id": "${SOURCE_EVENT_ID}",
    "occurred_at": "2026-09-17T00:00:00Z",
    "content": "ACME contract is Gold",
    "consent_reference": "operator-approved-demo-consent"
  },
  "memory": {
    "subject": "ACME",
    "predicate": "contract_tier",
    "value": "Gold",
    "evidence_quote": "ACME contract is Gold",
    "explicit_intent": true,
    "valid_from": null,
    "valid_to": null
  }
}
JSON
```

`episode` is unchanged Observe. `memory` is exactly one structured intent with
no scope, evidence IDs, or identity overrides; the server derives its scope and
single episode evidence ID inside the transaction. Its one quote must be a
literal 1–4,096-character substring of the normalized episode.
Remember bounds remain: subject 1–256, predicate `^[a-z][a-z0-9_]{0,63}$`,
value 1–65,536, explicit intent true, and aware/null valid bounds with start
before end when both are present. The literal quote is not proof of semantic truth;
published assertions remain reported and uncalibrated.

### Follow the job, not a presumed assertion

HTTP **201** gives `{memory_id: <episode UUID>, revision: 1,
synthesis_job_id: <job UUID>}`. This acknowledges the atomic **episode plus
structured_remember / structured-remember-v1 job commit**, not assertion
publication. Existing jobs, including terminal ones, may be reused:
**201 does not mean fresh or pending**. Save both historical IDs and use GET as
the authority for current job status. With `CAPTURE_JOB_ID` set to the returned job UUID:

```bash
curl --fail-with-body "${MEMORY_URL%/}/v1/jobs/${CAPTURE_JOB_ID}" \
  -H "Authorization: Bearer ${TOKEN}"
```

Run the existing worker under the **same owning principal's trusted fixed
subject**, with restricted runtime DB credentials, then GET again for fresh state.
`--once` processes at most one due owned job; it need not be this job and is not
a queue drain. Only a successful job result supplies the published assertion ID.
Existing quota (100 pending/running jobs per scope), five attempts, leases,
epochs, and publication fencing apply unchanged. See
[worker operations](#durable-job-and-worker-operations).

Pure `POST /v1/observe` still returns `synthesis_job_id: null` and never queues
automatically. Direct `/v1/jobs` and synchronous `/v1/remember` are unchanged,
including Observe/Remember serialization/HMAC compatibility.
Capture adds no LLM/provider, extraction, natural-language synthesis, automatic embeddings,
or new semantic-quality qualification.

### Replay, failures, and deletion

- Reuse the **same capture key and normalized body** after uncertainty, including
  API restart; HTTP response loss is not proof of rollback. Changed body with
  the same key is `409`. IDs are historical: GET supplies fresh job state.
- New keys with the same episode/intent/principal deduplicate **both IDs**.
  A previously observed identical event is reusable. Changed episode body for
  the same source identity conflicts with `409`, without new partial writes.
- A new, different explicit intent may create another job using the retained
  episode. Another authorized principal has independent job identity/worker
  ownership; source deduplication does not grant permissions.
- Transaction failure after episode/projection, job data/identity, outer
  idempotency receipt, or audit rolls back new changes together. An independently
  pre-existing episode remains; no partial new job survives. At most one job
  belongs to a capture operation.
- Current ACLs/deletion win for **both returned IDs**. Episode purge closes
  job/assertion descendants. Job-only purge leaves the episode and independently
  stored published output, but old-pair replay or a new key for the same intent
  returns `404`; the purged job identity is not recreated. Result-assertion purge
  removes its dependent job but keeps source, invalidating the pair.
- This is not a permanent whole-source seal: new explicit **different** intent
  on retained source follows existing job semantics.
- To retry a failed job, use existing `POST /v1/jobs/{job_id}/retry` with the full
  original `EnqueueJob` intent and caller-owned key. Reconstruct its existing
  Remember evidence from the returned episode ID and retained quote/intent.
  Capture replay returns the original failed job reference, **not a retry child**,
  even after that child is created.

Retained opaque source/job/idempotency anchors include internal composition keys
derived by server HMAC from the caller key. Do not supply or synthesize these
internal keys; they are not MCP autogenerated caller keys or a new API input.
The Native tenant HTTP response-drain boundary is unchanged. Retained anchors,
host context, backups, WAL, and delivered data have no new full-erasure guarantee.
Capability feature `atomic_structured_capture` has `atomic_capture` metadata:
`endpoint: "/v1/captures"`, `max_jobs: 1`,
`recipe_version: "structured-remember-v1"`, `automatic_capture: false`.
See [the delta contract](../STATUS.md#atomic-structured-capture)
and [ADR 0010](../adr/0010-atomic-capture.md).

## Local stdio MCP operations

### Install and start with one trusted identity

1. Bootstrap the Native API and provision the intended subject/scope using the
   role separation above. The adapter needs **no database URL, admin credentials,
   signing key, or worker `--subject`**. Native API authentication and current
   ACL/deletion checks remain authoritative on every call.
2. Install `pg-agmemory[mcp]`, or use the repository image with the extra already
   present. For the source checkout, prepare the lock with
   `uv sync --frozen --extra mcp`. The pins are official `mcp==2.2.0` and
   `httpx==0.28.1`, not a similarly named third-party MCP package.
3. In the trusted local host's process environment, securely supply
   `PGAG_MCP_API_URL` and `PGAG_MCP_API_TOKEN`. Do not commit tokens into a host
   configuration or put them in command-line arguments, examples, logs, or
   issue reports. The token targets the **Native API audience**, not the MCP
   host; the Native API validates it. This is a fixed trusted Native client,
   not forwarding of an MCP caller's identity.
4. Use an HTTPS origin such as `https://memory.example.com`, or a loopback
   HTTP origin such as `http://127.0.0.1:8000`. Do not include credentials,
   `/v1` or another application path, query, or fragment; a root `/` is accepted.
   Non-loopback plain HTTP is rejected. Loopback is relative to the adapter's
   process/container, not automatically the Mac host or a sibling container.
   Container-hosted adapters therefore need a reachable trusted HTTPS origin
   unless the API shares their loopback boundary. TLS verification stays on;
   redirects and proxy environment settings are not used.
5. Configure the host to launch the installed executable with argument `mcp`:

   ```bash
   pg-agmemory mcp
   ```

   Use `uv run --frozen --extra mcp pg-agmemory mcp` in a checkout environment
   if the virtualenv executable is not on PATH. Do not add `--subject` or
   `--once`: both are rejected. Keep stdin/stdout attached for MCP messages,
   not human prompts or ordinary log output. Diagnostics use sanitized stderr.
6. Startup must authenticate `GET /v1/capabilities` and match API `v1`, service
   `0.0.11`, schema `8` before serving tools. A bad setting/token, unreachable API,
   or version mismatch exits nonzero without logging secrets. A passing
   `/healthz` alone is insufficient. Fix trusted configuration and restart;
   do not bypass the check or change tool arguments to override identity/URL.

There is no remote MCP HTTP/SSE transport, OAuth, delegated identity, or
per-call header/URL/token override. Run **one adapter per trust identity**;
do not share the connection across trust domains or expose it through a network
wrapper. Restart with a securely supplied replacement token to refresh it;
no automatic token refresh is provided. Preserve the same authorized subject
when recovering a previous operation.

### Invoke and recover without accidental duplicate writes

Only `memory_recall`, `memory_remember`, `memory_explain`, and `memory_forget`
are tools. Each wraps the **Native Pydantic request body** as `{request: ...}`.
Remember and forget additionally require `idempotency_key`, **1–256 visible
ASCII characters (`0x21`–`0x7e`, no whitespace)**, even for forget preview.
Keys are not trimmed or rewritten: 256 characters is allowed and 257 is rejected.
Native forget preview and purge both return **HTTP 202**, unchanged; inspect the
Native result variant rather than treating the status as proof of purge.
Have the host/caller retain the key and exact body securely **before dispatch**,
so it can reuse them after an interrupted response or stdio restart.
MCP session/request IDs are not memory run IDs or Native HTTP idempotency keys.
Do not use a new key just because the host reconnects.

Success has `structuredContent: {result: <Native result>, error: null}`.
Failure has `isError: true` and `{result: null, error: {code, retryable,
outcome_unknown, native_status, request_id}}` in `structuredContent`;
the Native status/request UUID can be null. Short text does not duplicate
the evidence payload. Inspect structured output, not text alone.
Transport errors, timeouts, 5xx, and invalid mutation responses mean the outcome
may be unknown, **not that the write rolled back**. After uncertainty, retry
only with the same key/body and intended identity, including after token
replacement. Neither retries nor keys are generated automatically.
`retryable` does not authorize changing the body or prove non-commit.
Current authorization/deletion may deny a replay; historical references are
not current evidence or permission to restore deleted content.

The HTTP client has a **20 s total / 10 s I/O / 5 s connect** bound,
**4 connections**, **256 KiB serialized request**, and **2 MiB response** limits.
No response/semantic cache is maintained. These limits do not qualify throughput
or all host buffer sizes. Recall budgets remain **UTF-8 bytes, not model tokens**;
Japanese recall still needs explicit `ja-janome-0.5.0-v1` selection.
Remember only publishes explicitly requested structured assertions with Native
episode evidence; capture episodes with Native `observe`, not MCP. Explain
without a revision still requests revision 1, not latest.

### Deletion and host-context handling

Treat retrieved text as untrusted evidence, not instructions. The adapter is
the trusted Native HTTP recipient: the API's response-drain barrier ends at
HTTP delivery to it, **not atomically at stdio delivery, the host UI, or LLM
context consumption**. A host may still hold a response that predates purge or
ACL revocation. In-flight buffers/already-delivered context cannot be retracted.
After forget or permission changes, the host must discard cached context and
obtain fresh authorized data rather than reuse old output. There is **no MCP
deletion notification** that does this automatically. Purge does not certify
host-context, backup, WAL, replica, or physical-media erasure.

Historical v0.0.8 actual stdio SDK `Client` connections and raw JSON fixtures exercised:

- Modern `2026-07-28`: `Client(mode="auto")` and `server/discover`. Raw requests
  carry `params._meta` keys `io.modelcontextprotocol/protocolVersion`,
  `io.modelcontextprotocol/clientInfo`, and `io.modelcontextprotocol/clientCapabilities`.
- Legacy `2025-11-25`: `Client(mode="legacy")`, `initialize`, then
  `notifications/initialized`, before tool calls.

The response-loss regression drops an actual HTTP response **after remember
commits**, then retries the same key/body and checks that only one assertion
persists. This tests caller-driven recovery, not an automatic retry.
Historical local/native CI evidence is recorded in STATUS. v0.0.11 retains both
protocol eras, semantics, MCP bounds, and the shared Native HTTP client.
v0.0.9 checks passed locally and on both native Docker architectures;
Historical v0.0.10 and current v0.0.11 checks also passed.
These specific checks do not prove compatibility with untested
older clients or a named host.
See [the full contract](../STATUS.md#local-stdio-mcp) and
[ADR 0008](../adr/0008-local-mcp.md).

## Implicit recall hook operations

**Retained read-only, lexical-only hook, verified in v0.0.11.** This is a vendor-neutral,
one-shot harness-side command, not MCP, a model caller, or an automatically
registered host plugin. No Copilot/Claude/Codex integration is claimed.

### Install and configure from a trusted operator environment

1. Provision the Native API subject/scopes using the role separation above.
   The hook requires **no DB credentials**, admin URL, JWT signing key, or
   external model key. It only uses the configured Native audience token.
2. Install `pg-agmemory[hook]` or use the v0.0.11 repository image with both
   `mcp` and `hook` extras. For the checkout use `uv sync --frozen --extra hook`.
   Hook-only installation pins `httpx==0.28.1`, **not the MCP SDK**.
3. Have the operator securely supply the following environment before starting
   the trusted harness. Do not derive it from prompts, queries, event fields,
   tools, or retrieved text. Do not place tokens in command-line arguments,
   checked-in examples, logs, or issue reports.

   | Variable | Operator setting / default |
   |---|---|
   | `PGAG_HOOK_API_URL` | Required, no default. Trusted HTTPS origin or loopback HTTP origin; no userinfo, application path, query, or fragment. Root `/` is accepted; absent URL gives `invalid_hook_configuration` |
   | `PGAG_HOOK_API_TOKEN` | Required fixed Native API audience bearer token |
   | `PGAG_HOOK_SCOPE_IDS` | Required JSON array of 1–32 unique provisioned scope UUIDs |
   | `PGAG_HOOK_PURPOSE` | Default `implicit_context`; 1–256 characters |
   | `PGAG_HOOK_TOKEN_BUDGET` | Default `2000`; integer 64–2,000 **UTF-8 bytes, not model tokens** |
   | `PGAG_HOOK_MAX_ITEMS` | Default `20`; integer 1–20 |
   | `PGAG_HOOK_SEARCH_PROFILE` | Default `simple-v1`; opt in explicitly to `ja-janome-0.5.0-v1` |
   | `PGAG_HOOK_TIMEOUT_SECONDS` | Default `2.0`; finite 0.1–20 seconds |

   URL, token, and scope IDs are **all required**. Shared `NativeSettings` also
   parses origins with `httpx.URL`, rejecting control characters and invalid
   IDNA before transport. These checks passed in all three historical v0.0.9 environments.

   Loopback is relative to the hook process/container; do not assume it reaches
   a sibling container or the Mac host. Non-loopback HTTP is rejected.
   Redirects and proxy environment settings are disabled; TLS verification is on.
4. Launch `pg-agmemory recall-hook` with exactly one JSON document on stdin, then
   EOF. `--subject`/`--once` are rejected. Only `event` and `query` are allowed:
   `event` is `session_start`, `task_switch`, or `after_compaction`; `query` is a
   required string of at most 4,096 Unicode characters. Empty query is canonical
   browsing in the configured scopes, not a missing field. Retrieval intent is
   supplied only by JSON `query`; `event` is a lifecycle label. No identity,
   `scope_ids`, purpose, mode, budget, URL, header, tool, time, or other field
   is accepted. Event text never authorizes access.
5. Each invocation freshly checks authenticated `GET /v1/capabilities` for exact
   service `0.0.11`, API `v1`, schema `8`, then sends `POST /v1/recall` with
   `mode: "implicit"`, trusted recall settings, and Native current-time defaults.
   The same fixed token is used for both requests; no caching of authorization
   or responses occurs. Replace credentials only through trusted startup configuration.

Recall silently filters unauthorized scopes and revoked memberships. Expect the
authorized subset, or a successful no-items/`not_found` result, **not a
scope-existence 404**. Token authentication failure instead returns explicit
Native **401**, which is hook **exit 1 with an error envelope**.
These are unchanged Native behaviors, not a hook fallback or permission grant.

Stdin is limited to **32,768 bytes**. Invalid UTF-8/JSON, oversized input, and
validation failures produce explicit errors. The network deadline is shared by
**capabilities and recall together**, not two independent allowances.
Its default 2 s (finite 0.1–20 s) excludes process startup, stdin input/waiting
for EOF, and output;
it is **not** a total-process or LLM latency SLO. Set a separate host subprocess
timeout and close stdin. Request/response bounds are **256 KiB/2 MiB**; the
**entire compact JSON-serialized context pack** must have UTF-8 byte length
equal to `context_pack.byte_count` and obey the configured budget.
Serialization uses `ensure_ascii=False`, `separators=(",", ":")` and includes
metadata/citations, **not just text**. Returned item count must not exceed configured
`max_items`; the returned profile must match configuration. Mismatches fail
without broad fallback. These are not blanket
host-buffer limits. Shared-client extraction must preserve the MCP bounds above.
The hook does not write, capture, enqueue, invoke an LLM/provider, cache, retry,
or send an idempotency key.

Budget 64 is valid configuration, but even an empty pack costs roughly 192 bytes.
If its metadata cannot fit, Native returns **422 `budget_too_small`** and the hook
returns an error envelope with **exit 1**, not empty success. Do not treat the
approximate empty-pack size as a guaranteed constant. When the pack fits but
candidates do not, `budget_exhausted` is Native **200 / hook exit 0** instead.
Missing-index `index_incomplete` is also **200 / exit 0**, with
`coverage.lexical_incomplete: true` and `coverage.retrieval_complete: false`
when projections are missing and no candidates exist. The host must surface
that incomplete coverage even though the hook succeeded.

### Vendor-neutral Python harness example

After preparing the executable and operator environment, run this shell block.
It uses only Python's standard library. The example's host policy is to **pause
on failure or incomplete retrieval**, not silently continue; a different host
must explicitly choose and surface any continuation without memory.
The 30 s subprocess timeout is an illustrative **separate host policy**, not a
measured startup guarantee or service SLO. Set it for your deployment.
The trusted executable/PATH and startup environment must be operator-controlled.
The empty sample query requires no external model; a host may supply untrusted
task text only as `query`, never as configuration. Do not log it.

```bash
python - <<'PY'
import json
import os
import shutil
import subprocess
import sys

def pause(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)

setting_names = (
    "PGAG_HOOK_API_URL",
    "PGAG_HOOK_API_TOKEN",
    "PGAG_HOOK_SCOPE_IDS",
    "PGAG_HOOK_PURPOSE",
    "PGAG_HOOK_TOKEN_BUDGET",
    "PGAG_HOOK_MAX_ITEMS",
    "PGAG_HOOK_SEARCH_PROFILE",
    "PGAG_HOOK_TIMEOUT_SECONDS",
)
child_env = {name: os.environ[name] for name in setting_names if name in os.environ}
child_env["PATH"] = os.environ.get("PATH", os.defpath)
if not all(child_env.get(name) for name in setting_names[:3]):
    pause("Memory configuration missing; task paused.")
executable = shutil.which("pg-agmemory", path=child_env["PATH"])
if executable is None:
    pause("Memory executable unavailable; task paused.")

event = {"event": "session_start", "query": ""}
untrusted_memory_evidence = None
try:
    completed = subprocess.run(
        [executable, "recall-hook"],
        input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=child_env,
        timeout=30,
        check=False,
    )
except (OSError, subprocess.TimeoutExpired):
    pause("Memory subprocess failed or timed out; task paused.")

# Never echo raw stderr or exception/response text.
try:
    envelope = json.loads(completed.stdout.decode("utf-8"))
except (UnicodeDecodeError, ValueError):
    pause("Memory output invalid; task paused.")
if not isinstance(envelope, dict):
    pause("Memory envelope invalid; task paused.")
if completed.returncode != 0 or envelope.get("status") != "ok":
    pause("Memory retrieval failed; task paused.")
if (
    set(envelope) != {"status", "event", "result", "error"}
    or envelope.get("event") != event["event"]
    or envelope.get("error") is not None
    or not isinstance(envelope.get("result"), dict)
):
    pause("Memory success envelope invalid; task paused.")

result = envelope["result"]
coverage = result.get("coverage")
empty_reason = result.get("empty_reason")
if (
    not isinstance(coverage, dict)
    or type(coverage.get("retrieval_complete")) is not bool
    or empty_reason not in (None, "not_found", "budget_exhausted", "index_incomplete")
):
    pause("Memory coverage invalid; task paused.")
coverage_notice = {"retrieval_complete": coverage["retrieval_complete"]}
for name in ("truncated", "lexical_incomplete"):
    if name in coverage:
        if type(coverage[name]) is not bool:
            pause("Memory coverage invalid; task paused.")
        coverage_notice[name] = coverage[name]
print(json.dumps({
    "memory_status": "ok",
    "coverage": coverage_notice,
    "empty_reason": empty_reason,
}))
if not coverage["retrieval_complete"]:
    pause("Memory coverage incomplete; task paused.")

# Keep the full Native result separate from trusted instructions and policy.
untrusted_memory_evidence = result
print("Memory is separate UNTRUSTED evidence; no model or tool was invoked.")
PY
```

`subprocess.run(input=...)` sends one JSON document and closes the child's stdin.
It checks both exit code and structured status without printing raw stderr or
query-bearing exceptions. The hook validates the full Native RecallResult;
the example checks the envelope/coverage before retaining it separately.
Logs use only fixed coverage keys with validated boolean values and the
validated empty-reason enum, not raw response content. Full coverage remains
in the separate result. Invocation failures with no JSON, malformed envelopes,
and subprocess timeouts all pause without echoing raw content.
Retain all Native evidence/coverage fields; never promote the result into system
instructions, policy, or verified external truth.
This example is not vendor integration, a model call, or host-erasure proof.

### Failures, coverage, and deletion

Validated hook runtime outcomes emit one JSON envelope plus newline on stdout and sanitized
diagnostics on stderr. Success has `status: "ok"`, validated `event`, the full
Native `result`, and `error: null`. Failure has `status: "error"`,
validated `event` or null, **`result: null`**, and
`error: {code, retryable, outcome_unknown: false, native_status, request_id}`.
Native status and validated request UUID can be null.

| Runtime code | Exit | Handling |
|---|---|---|
| `invalid_hook_configuration` | `2` | Correct trusted startup configuration |
| `invalid_hook_input` | `2` | Correct UTF-8/JSON or event/query validation errors without logging the input |
| `hook_input_too_large` | `2` | Keep stdin within 32,768 bytes |
| `hook_input_unavailable` | `2` | Supply readable stdin |
| `hook_deadline_exceeded` | `1` | Combined network deadline exceeded; `retryable: true` |
| `native_api_unavailable` | `1` | Native API transport unavailable; `retryable: true` |
| `native_version_mismatch` | `1` | Use matching service/API/schema versions |
| `invalid_native_response` | `1` | Reject the invalid protocol/response, without fallback |
| `budget_too_small` | `1` | Native `422`: the pack metadata cannot fit; adjust the trusted byte budget |
| Mapped sanitized Native codes | `1` | Surface the Native failure without forwarding raw details |

**Invocation-error exception:** rejected CLI flags (including `--subject`/
`--once`) and a missing `hook` extra use argparse stderr and **exit 2 without
a JSON envelope**. All validated hook runtime errors, including the configuration/
input codes above, return the error envelope. Do not assume stdout is JSON
before checking/parsing it; never echo raw stderr or invalid output.

Exit **0** includes genuine Native `not_found`, `budget_exhausted`, and
`index_incomplete` empty reasons; inspect coverage even for successful retrieval.
Exit **2** is invalid configuration/input; exit **1** is Native/network/version/
protocol failure. A process killed by the host may have no envelope.
**Never map failed retrieval to empty success or hide it with stale context.**
Surface errors/coverage, then explicitly pause or continue without memory.
`retryable` is only a hint; the hook has no automatic retry or idempotency key,
and `outcome_unknown: false` reflects its read-only operations, not MCP mutation behavior.

The Native tenant session advisory barrier ends at HTTP delivery to this
**trusted local hook**. Hook/stdout/pipe buffers and host context are not
atomically covered. No retraction or deletion notifications exist.
After forget or ACL changes, stop using/discard previous context and invoke a
fresh hook under current authorization; do not assume an in-flight pre-change
result is fresh. The hook never expands permissions or proves host, WAL,
replica, backup, or physical-media erasure. Engineering tests do not qualify
specific vendor integration, semantic quality, or performance.
See [the complete contract](../STATUS.md#implicit-recall-hook) and
[ADR 0009](../adr/0009-implicit-recall-hook.md).

<a id="v008-application-update-schema-unchanged"></a>

<a id="v009-application-update-schema-unchanged"></a>

<a id="v0010-application-update-schema-unchanged"></a>

## Historical v0.0.10 application update (schema unchanged)

**Historical schema-7-only workflow, not the v0.0.11 upgrade.**
For the current release use [schema 8 pgvector upgrade](#schema-8-pgvector-upgrade).

For an existing v0.0.7/v0.0.8/v0.0.9 schema-7 database there is **no migration 008/009/010 or new
backfill**. Record the application/schema versions and preserve a backup and
current deletion/ACL records. Stop/drain old APIs, workers, and MCP adapters,
and suspend hook launches, including auto-restarts; replace them with matching v0.0.10 images. Confirm exact
schema history `[1, 2, 3, 4, 5, 6, 7]`, then start the restricted Native API/workers,
check authenticated capabilities, and start each fixed-identity adapter/hook.
Same schema does not establish rolling mixed-version compatibility or a
supported downgrade. v0.0.10 packaged runtime and retained schema-contract
checks passed locally and on both native Docker architectures. Historical results stay separate in
[validation evidence](../STATUS.md#validation-evidence).
For older schemas in this historical workflow, use the matching v0.0.10 image.
Reindex remains separate offline maintenance, not an MCP/hook command.

<a id="v003-maintenance-migration"></a>
<a id="v004-maintenance-migration"></a>
<a id="v005-maintenance-migration"></a>
<a id="v006-maintenance-migration"></a>

## v0.0.7 maintenance migration

**Historical schema-7 procedure for v0.0.7–v0.0.10 only.**
The current v0.0.11 upgrade must also apply [migration 008](#schema-8-pgvector-upgrade)
and must not restart the schema-7 processes described here.

This is the retained schema-7 migration introduced in v0.0.7, for older
databases; it is **not a new v0.0.8/v0.0.9/v0.0.10 migration**.

**No rolling old/new API/worker coexistence or downgrade is supported.**
Rehearse upgrades only in disposable test databases. Passing migration tests
does not qualify a production upgrade or disaster recovery.
Follow this maintenance protocol:

1. Stop and drain **all old and new APIs and workers**, including replicas,
   continuous worker loops, and automatic restarts. The migration advisory lock
   is not a substitute for stopping API traffic and worker claims/publication.
2. Take a backup and record the old application/schema versions. Preserve the
   latest deletion ledger and ACL revocations independently as required for
   restore quarantine. Do not overwrite the only pre-migration backup.
3. With the privileged migration administrator and the new image, run
   `pg-agmemory migrate`. It applies pending scripts, Python lexical backfill,
   and ledger updates in one transaction under the migration lock.
   A 5-second lock timeout aborts rather
   than waiting indefinitely; diagnose contention while traffic remains stopped.
4. Migration 007 adds `memory.episode_lexical` and `memory.assertion_lexical`,
   with forced RLS, same-scope canonical foreign keys, and cascade deletion.
   Runtime grants are `SELECT`/`INSERT` only: no `UPDATE` or direct `DELETE`.
   Canonical parent purge cascades without child DELETE grants.
   Python backfill covers all retained episodes and every
   assertion revision, skips tombstones, and completes before schema 7 is recorded.
   Failure even after backfill completes rolls back projection DDL/data and the
   schema ledger together; a schema-6 upgrade remains at 6.
   Migrations 001–006 remain unchanged; older DBs receive missing versions
   sequentially. Preserve graph/job/assertion/effect histories, checkpoint checksums,
   canonical IDs/system times, source-event/idempotency receipts, and `Remember`
   JSON/HMAC ordering. Use the pinned Janome 0.5.0 dependency and bundled dictionary.
   The v4 ledger's stricter resume rules remain: untracked hints, even planned
   ones, block resumption.
5. Confirm exact history `[1, 2, 3, 4, 5, 6, 7]`, then start **only matching v0.0.10 APIs/workers**
   with restricted runtime credentials and the intended fixed worker subjects.
   Check capabilities/schema, default/opt-in recall and projection coverage,
   historical revision selection, authorization, purge, atomic publication, and
   compatibility before restoring traffic. A health response alone does not
   validate these. Migration/rebuild duration and resource use are unqualified.
6. On failure, leave APIs/workers stopped. Do not launch the old image against the
   changed schema or assume a downgrade exists. Any backup restore remains
   quarantined until the latest deletion/ACL state is reapplied and validated.

**The old v0.0.1 API does not contain the new schema-compatibility guard.**
It may start against an incompatible schema; operators must keep it stopped.
The new runtime's refusal of schema mismatches does not protect old processes.

## Lexical profile and reindex operations

Recall defaults to `search_profile: "simple-v1"`; explicitly request
`"ja-janome-0.5.0-v1"` for Japanese-script surface/wakati segmentation. The
response echoes the selected profile. Janome 0.5.0 uses bundled
mecab-ipadic-2.7.0-20070801 with Janome additions. ASCII identifiers/English pass
through the segmenter unchanged; PostgreSQL still performs lexical processing.
There is no Unicode/width normalization, lemma/stemming, synonym matching, or
segmentation/recall-quality qualification. Han handling can affect Chinese
characters without qualifying Chinese recall. This lexical profile is not an
embedding model or file-based memory index; vector/hybrid modes are separate. Context budgeting
remains the separate `utf8-bytes-v1` contract.

Use the supported container build profile: test and runtime builds sequentially
precompile **only static Janome package bytecode**, including dictionary modules.
It is packaged code, not a memory index/cache or compiled user input. Janome is
lazy-imported only for Japanese-script runs; English-only operations do not load
it. `max_cached_word_len=0` disables matcher input-prefix caching, retaining only
packaged dictionary-resource caches. Cold host installations without precompiled
dictionary code can have much larger initialization peaks.
The fresh Linux subprocess guard requires initialization peak RSS **below 256 MiB**
and no Janome import for English-only operations. Do not use that test threshold
as a deployment memory limit: request processing, concurrency, migration/rebuild,
and resource sizing are not qualified. See the bounded diagnostic observations
in [ADR 0007](../adr/0007-japanese-fts.md#runtime-initialization-boundary).

For the Japanese profile, missing currently authorized, requested-scope,
time-eligible projections set `coverage.lexical_incomplete: true` and
`coverage.retrieval_complete: false`, regardless of query relevance or job state.
Available matches may still return; in lexical mode an empty query browses canonical items even
with the flag. No candidates with missing projections gives
`empty_reason: "index_incomplete"`; candidates dropped for budget still give
`"budget_exhausted"`. There is no silent simple-profile fallback or repair worker.
Choosing simple search does not repair the Japanese projection.
Corrupt-dictionary logs are sanitized to `japanese_dictionary_error`, without
input text. Janome `SystemExit` becomes tokenizer-unavailable and API
`503 dependency_unavailable`, not `index_incomplete`; workers follow existing
bounded dependency retries without input echo.

Reindex rebuilds **all tenants in the selected database**, not one worker
principal or scope. `--subject` is explicitly rejected rather than narrowing
access; `--once` is also rejected as worker-only.
To rebuild lexical projections in a migrated schema-8 database from canonical data
(verified with v0.0.11 tooling; this never rebuilds embeddings):

1. Stop/drain **all APIs and workers**, including automatic restarts, and back up
   as for migration. This is offline maintenance, not a live administrative API.
2. Use the matching v0.0.11 image and **`PGAG_ADMIN_DATABASE_URL`**, with forced-RLS
   bypass and the required table privileges, then run:

   ```bash
   pg-agmemory reindex-lexical
   ```

3. Use exact history `[1, 2, 3, 4, 5, 6, 7, 8]` and the matching extension; the command takes the migration
   advisory lock with a 5-second lock timeout, and replaces both projection
   tables in one transaction. It segments every retained episode and assertion
   revision, not just heads, excluding tombstones. Canonical IDs, system times,
   evidence, receipts, and synchronous request hashes do not change.
   JSON output contains `profile: "ja-janome-0.5.0-v1"` and integer
   `episodes`/`assertion_revisions` counts only, never source text or tokens.
4. On failure, even after partial replacement, the existing lexical projections
   remain intact. Keep traffic stopped and diagnose
   schema, privileges, or lock contention. Never grant runtime bypass or edit
   canonical text, timestamps, or receipts to repair an index.
5. Restart only matching v0.0.11 APIs/workers. Before reopening traffic, inspect
   profile/coverage and authorized current/historical recall with approved test
   data. For exact `known_at` boundaries, use server-returned assertion
   `recorded_at`, not host/VM wall-clock samples.
   Counts alone do not certify relevance, completeness of world knowledge,
   or performance. No automatic recovery or DR guarantee is implied.

The packaged dictionary is a code dependency, while projections live only in
PostgreSQL. Preserve Janome's Apache-2.0 license and bundled IPADIC copyright/
license notices when redistributing images; see
[dependency licensing](../../README.md#dependency-licensing) and
[ADR 0007](../adr/0007-japanese-fts.md). M0/M1/M2/M3 and MVP/production acceptance
remain incomplete.

## Durable-job and worker operations

The worker publishes caller-supplied structured assertions; it is not an automatic
synthesis/NL extraction/LLM/provider, embedding, compaction, or tool-effect executor.
Provision a subject in advance, then run with only restricted `PGAG_DATABASE_URL`
credentials and trusted deployment identity:

```bash
pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT --once
```

The subject is 1–256 characters and must match the preprovisioned principal in the
configured issuer. It is not caller-controlled HTTP impersonation. Do not give
agents runtime DB credentials or authority to select worker subjects. No JWT
signing/public key or admin URL is needed by the worker; never use superuser,
table-owner/owner-member, or `BYPASSRLS` credentials. Startup shares API role/schema
validation. Only that principal's currently writable jobs can be claimed.
Same-scope readers can GET jobs but cannot run or retry another principal's work.

Omit `--once` for continuous operation: idle poll interval is 1 second, transient
DB loop delay is 2 seconds. `--once` handles at most one due job and prints JSON
`outcome` (`idle`, `succeeded`, `pending`, `failed`, or `lease_lost`), with applicable
opaque IDs/result reference. It does not wait for the entire queue or retry cycle;
other commands reject `--once`. Startup/unrecoverable errors are failures, not
successful idle results. This fixed-principal profile is not a global scheduler
or a qualified fairness/cost-pool implementation.
Worker stdout/logs contain opaque historical outcome references, not current
read authorization or a live snapshot. Read job GET/explain under current
access/deletion checks instead of relying on an earlier CLI outcome.

1. Submit `POST /v1/jobs` with an HTTP key and
   `{kind: "structured_remember", memory: <original Remember body>}`. Use current
   scope read/write access, explicit intent, and exact same-scope episode quotes.
   `202` is the committed job reference with fixed recipe `structured-remember-v1`,
   not publication completion. Save the original request securely for explicit
   retry; sanitize it before submission.
2. Retry uncertain enqueue with the same normalized request/key. Canonical
   intent/recipe also deduplicates across keys within one principal/scope,
   normalizing evidence order for job identity only. Another principal or
   different source identity is not semantic deduplication.
   Respect the scope's 100 pending/running cap; do not bypass it with other identities.
3. Poll job GET for state, attempts (maximum 5), scheduling/lease timestamps,
   safe error code, immutable episode input references, retry parent, and result.
   GET never returns request JSON, owner principal, or lease token. Terminal
   success/failure erases the request; succeeded results stay revision 1 after
   later corrections. Use the result's exact assertion revision for explain.
   Assertion recorded/system time begins at worker publication, not job enqueue;
   do not substitute job `created_at` for the assertion's adoption time.
4. Automatic retriable failures use `2^attempt + [0,1)` seconds of backoff/jitter.
   `invalid_input` fails immediately; an expired fifth claim fails with
   `attempt_limit`, without a sixth attempt. Diagnose safe
   `dependency_unavailable`/`stale_context` codes without logging payloads.
   `lease_lost` does not authorize another publication from an old prepared body.
5. For an owned terminal failed job, POST the full original `EnqueueJob` body
   to `/v1/jobs/{job_id}/retry` with a key. Current evidence/permissions and HMAC
   intent are rechecked; changed intent is `409 job_intent_conflict`, nonfailed
   parent is `409 job_retry_conflict`, and nonowner is `404`. Repeated retry of
   that parent reuses one child even across keys. If the child fails, retry its
   ID for another explicit five-attempt cycle. Never reset terminal rows/recipes in SQL.

Claims commit under `FOR UPDATE SKIP LOCKED` before payload preparation outside
the transaction. Default lease is 30 seconds with a fresh token and current epochs;
internal 1–300-second claim bounds are for controlled tests, not operator tuning.
Publication rechecks current identity/access, inputs/exact body, lease/token/expiry,
and epochs; output/provenance/job success/audit commit together. Final-update expiry
rolls back output. Internal heartbeat validates lease/epochs, but the deterministic
processor needs no background heartbeat task or external call. There are no public
claim/publish/heartbeat endpoints. At-least-once attempts produce at most one
committed result per job, not external exactly-once execution.

`observe` never auto-enqueues; synchronous `remember` and its legacy JSON/HMAC
remain unchanged. Recall's `jobs_pending` covers readable pending/running jobs
in requested scopes; `synthesis_pending` and `graph_used` remain false.
Jobs are not recall/explain items or checkpoint/effect reference kinds.
See [the contract](../STATUS.md#durable-jobs) and
[ADR 0006](../adr/0006-durable-jobs.md); M0/M1/M2/M3, MVP/production, performance,
quality, and DR acceptance remain incomplete.

## Entity and graph operations

1. Create explicit entities from approved same-scope episode quotes with
   `POST /v1/entities`, an allowlisted type, bounded canonical label, and
   `explicit_intent: true`. Save each returned revision-1 UUID. Labels/types are
   caller reports, not trusted instructions or verified facts. Use entity GET
   for metadata/evidence, not recall/explain. There is no alias/merge/name-resolution
   or label-correction endpoint; a new HTTP key may create a separate same-label
   identity. Retry uncertain creation with the original key/body.
2. Create relations only through `POST /v1/relations`, passing same-scope source/
   target entity UUIDs and episode evidence. The returned ID is the canonical
   assertion, not a second relation object. Matching free-text `remember` data
   stays untyped. All allowlisted predicates are multi-valued reported declarations.
3. Correct with `POST /v1/relations/{memory_id}/revisions`, an exact expected
   revision, target UUID, replacement evidence/valid bounds, explicit intent, and
   reason. Source/predicate stay fixed; omission of bounds is unbounded and
   replaces the entire interval. Generic assertion correction returns
   `409 relation_revision_required`. Inspect exact historical revisions through
   explain; omitted revision is 1, not latest. Never edit typed links or values in SQL.
4. Call authenticated, read-only `POST /v1/graph/expand` with explicit distinct
   scopes, entity seeds, predicates, and purpose; no `Idempotency-Key` is needed.
   Limits are 32 scopes, 16 seeds, 5 predicates, 1–2 hops, and 1–100 paths.
   Inspect effective `as_of`/`known_at`, coverage, and epochs. Prefixes count;
   cycles cannot repeat nodes within paths. Incoming/both is traversal orientation,
   not inferred inverse truth. Hidden seeds are not echoed; visible isolated
   seeds may be returned with no paths. Empty/bounded results do not prove absence.
5. Treat `409 graph_invalidated` as an invalidated read and DB `503` as failure,
   never as an empty graph. PostgreSQL canonical joins need no AGE/SQL/PGQ
   installation, graph projection rebuild, or lag/watermark operation:
   `backend: "sql"`, `projection_watermark: null`. There is no dynamic graph
   SQL/Cypher/label input. Recall remains FTS with `graph_used: false`.
6. Declare every copied entity revision 1 or exact assertion revision in
   checkpoint/effect `memory_refs`, including graph-derived dependencies.
   Entity GET and relation explain supply evidence; expansion nodes omit quotes.
   Do not treat a path or canonical label as permission to execute an action.

See [the contract](../STATUS.md#entities-and-sql-graph-oracle) and
[ADR 0005](../adr/0005-relational-graph.md). This bounded correctness reference
is not graph-utility/performance evidence, full M0/M1/M3, MVP, or production/DR qualification.

## Checkpoint operations

1. Capture only sanitized schema-1 state. Declare every copied memory source in
   `memory_refs`, including the exact revision. Undeclared copies are not
   discovered by a semantic scanner.
2. Create under the intended scope/run/branch with an explicit `expected_head`;
   use null only for a new branch. Resolve `409` head/watermark/harness conflicts
   rather than silently resetting the head. Save the returned checkpoint ID.
3. Load through the checkpoint GET endpoint, not recall/explain. Treat checksum
   or reference-validation failures as invalidation, not permission to bypass
   validation or edit the stored payload.
4. Restore only to a never-used target branch with the exact harness ID/version
   and state schema. The source branch remains unchanged. Saved assertion
   references keep their exact historical revisions; restore does not select
   the latest revision or automatically refresh external facts. Inspect
   `tool_effects`, `untracked_effects`, `requires_reconciliation`, and `resume_allowed`
   before handing state to a harness. These include all live run effects, not
   just snapshot-time effects. Untracked planned hints also block; a conflicting
   unknown hint plus a tracked planned effect needs uncertainty/receipt reconciliation.
   Restore atomically marks dispatched ledger records unknown before creating
   the fork; CAS rejects stale ledger writers, not in-flight external calls.
   `automatic_reexecution` is always false. No provider receipt lookup or code execution
   is performed by this API.
5. Retry uncertain writes with the same key and payload. Only the original
   result reference is retained in idempotency records, not state. Current
   authorization/checksum checks still apply; a purged checkpoint returns `404`.

A saved epoch or `resume_allowed: true` is not an approval or an external-effect
receipt. Typed pending effects are snapshot hints; the durable ledger is separate.
Checkpoint creation allows a 1 MiB body; other endpoints allow 256 KiB.
See [the contract](../STATUS.md#checkpoint-contract) and
[ADR 0004](../adr/0004-tool-effects.md). No production/DR qualification is implied.

## Tool-effect operations

1. Bootstrap the intended scope-local run with a checkpoint before planning
   effects. Compute a stable lowercase 64-hex hash of the host's canonical action,
   then POST its operation UUID, tool name, hash, and all exact memory dependencies.
   The service does not store arguments or the raw hash, or verify the intended
   external call. Sanitize tool names, reasons, receipt references, and state.
2. Save the returned `memory_id` and GET the latest record, including its stable
   `external_idempotency_key` and `run_invalidated` flag. Identity is scoped by
   tenant/scope/run/operation; new run/operation IDs do not deduplicate equivalent
   real-world actions. The lifetime cap is 100 effects per run, including terminals.
3. The host must enforce permissions/approvals and durably record a CAS transition
   to `dispatched` **before** any outside call. Use the stable external key if the
   provider supports it. The host owns execution coordination: a replayed old
   dispatch acknowledgment is not fresh permission to send or blindly resend.
4. On an uncertain outcome, record `unknown` and reconcile with the provider
   outside this service. `planned → unknown` can capture a legacy/off-protocol
   attempt; it is not permission to execute. `unknown → dispatched` is forbidden.
   Terminal `confirmed`/`failed` requires a bounded receipt reference plus
   `provider_receipt` or `operator_review`; both are caller-reported, not verified.
   Terminal outcomes are immutable and do not authorize automatic retries.
5. Retry uncertain ledger writes with the same key/body. Changed intent conflicts;
   a new key for the same recorded intent still returns its original revision-1
   reference. Plan/dispatch responses are historical revision references, not
   current-state snapshots or execution authorization. Under current authorization,
   replay for a surviving effect may still succeed after run sealing, without
   allowing fresh dispatch. Read GET for current state rather than trusting an old response.
   Do not delete checkpoint hints to bypass reconciliation or reuse IDs to evade
   uncertainty. An untracked hint must be resolved explicitly by the host.
6. After an effect purge seals the run, do not create new intents, dispatch, checkpoint, or
   resume it. Independent surviving effects remain GET-readable and can use
   allowed reconciliation transitions; `unknown → confirmed/failed` remains valid.
   Preserve the opaque operation registry, run flag, and tombstones.

This is a ledger, not a worker, tool-execution harness adapter, provider-query client, approval
service, or external exactly-once mechanism. See [the contract](../STATUS.md#tool-effect-ledger)
and [ADR 0004](../adr/0004-tool-effects.md).

## Revision operations

Corrections append a full replacement revision, with immutable subject,
predicate, and scope. Preserve the same `Idempotency-Key`, target ID, and body
when retrying an uncertain correction. A successful replay returns its original
revision reference even if newer revisions exist; it is not a read of the head.
For `409 revision_conflict`, resolve the stale expected head rather than
silently overwriting. There are at most 1000 total revisions per assertion.

`explain` with no revision still means revision 1, not latest. Use the exact
revision returned by a mutation/recall when inspecting that result. Each
historical read remains subject to current ACLs and tombstones. A future-dated
replacement does not preserve the previous value before its new valid interval.
See [the revision contract](../STATUS.md#assertion-revision-contract) and
[ADR 0002](../adr/0002-assertion-revisions.md).

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

Purge traverses episode/entity/assertion history, job dependencies/retry lineage,
declared checkpoint/effect references, and
every descendant/fork checkpoint through the complete parent lineage. Its
limit is 10,000 dependents in total plus requested roots. A source used only
by an old assertion revision still removes the entire assertion history and
all affected checkpoint state. Episode evidence also leads to entities and all
relation histories using them as source or **any historical target**; direct entity
purge follows the same relation dependencies. Direct entity references in
checkpoints/effects participate. Other surviving entities are not removed merely
because a relation disappears. Semantic relation cycles are not provenance cycles:
entities depend only on episodes.
Job closure follows episode inputs → jobs, result assertions → jobs, and parent
jobs → retry descendants within the same 10,000-dependent bound.
**Deleting a job or failed-parent retry chain does not delete an already-published
independent assertion or source episode.** Purge its output/source explicitly to
erase the fact. Output assertions retain their own direct episode provenance;
any revision-source deletion removes the whole assertion and its dependent jobs.
There is no job → result dependency cycle.
Branches whose heads are affected are permanently
invalidated; do not try to reopen their IDs or remove lineage to avoid deletion.
Purging any effect also removes **all checkpoint payloads in that scope/run**,
including older empty snapshots, and permanently sets `effects_invalidated`.
It blocks new plans, dispatch, checkpoints, and resumption, but does not purge
independent effects merely for sharing the run; surviving records remain reconcilable.
Job request/input rows are removed before assertion/episode rows and tombstones
under the same tenant barrier, fencing running publishers. Purged-job GET/replay
returns `404`; retained job identity prevents exact-job resurrection.
Canonical episode/assertion deletion cascades all corresponding lexical
revisions in that same barrier before tombstones commit. These rows are derived
payload, not separate memory/provenance vertices; rebuild skips tombstones and
cannot recover purged source content.
Payloads, entity evidence, typed links, quotes, references, and effect events
(reason/receipt references included)
are SQL-deleted from active tables before timestamped markers enter
`memory_ops.object_tombstone` in the same transaction. Object SELECT RLS hides
those anchors; there is no soft-delete `deleted_at` update on `memory.object`
or privileged deletion helper. The barrier/receipt commits before responding.
The tenant session lock covers closure, run/branch invalidation, and read draining.
Purge does not enqueue a worker or rebuild affected content.
The receipt's `active_store_purged` is not
full erasure.

Opaque job identities, operation registry/run flags, run/branch metadata, objects and tombstones,
audit/receipt metadata, and
tenant-keyed HMAC source/idempotency tombstones remain for the tenant lifetime.
Do not manually remove
them or change `dedup_secret` to “finish” a purge: doing so can defeat replay
protection. Exact replay of deleted source identities or memory results returns
`404`; payload conflicts remain `409`. No automatic full tenant-erasure
procedure is provided. Historical references/replay cannot recover purged
entity labels, relation values, or receipts. Backup limits below are unchanged.

## Backups, restoration, and release evidence

Checkpoint restore copies typed state inside the Memory DB; it is not database
backup restoration, a separate working-snapshot compaction system, or disaster recovery.
A database backup can also roll back effect states. Keep external execution stopped
and reconcile provider outcomes separately; the ledger does not automate safe recovery.

Deletion receipts report `backup_status: "operator_managed"` with
`backup_retention_deadline: null`. SQL row deletion is not proof of physical
media sanitization, removal from WAL/replicas/backups, or erasure of delivered
context. There is no implemented backup-retention deadline enforcement,
automated restore replay, HA/PITR workflow, or verified RPO/RTO.

Required restoration boundary, **not yet an implemented automated procedure**:

1. Keep any restored database quarantined: no API, worker, agent, or user access.
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

`scripts/test-containers.sh` covers both production API HTTP smoke and actual
worker CLI smoke in the non-root production image. It provisions a disposable
principal, supplies runtime-only credentials to `worker --subject ... --once`,
asserts `{"outcome":"idle"}`, and logs `Production worker smoke passed`.
It also checks `東京都` → `東京` / `都` segmentation in the non-root runtime image
and emits `Production Japanese tokenizer smoke passed` on success. This tests
packaged tokenizer initialization/segmentation, not end-to-end recall or quality.
The historical v0.0.8 runner also launches an actual `pg-agmemory mcp` child in the
non-root production image. Its fixed token and provisioned scope access the
loopback Native API; it lists all four tools and calls recall in **both**
modern `2026-07-28` and legacy `2025-11-25` modes. Existing Japanese/API/worker
smokes remain. These checks passed in all three v0.0.8 environments.
The v0.0.8 CI step is `Test containers and smoke-test production API, worker, and MCP`.
This idle-worker check is not a publication test or production/DR qualification.
Historical v0.0.7 implementation
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)
passed local Apple Container and exact-SHA native Docker
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029);
all three production smokes passed in each environment. Detailed results are in
[STATUS](../STATUS.md#validation-evidence), not a production/DR acceptance claim.
Historical final-docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)
also passed both native jobs in
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899).
**Historical v0.0.8:** implementation
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)
passed 214 tests, Ruff, strict mypy (13 files), and all production smokes locally
and in [CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)
on both native architectures. The two v0.0.7 runs do not validate MCP.
The subsequent v0.0.8 bilingual documentation commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)
passed **214 tests on each native architecture** in
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509).
None of these historical runs validates the v0.0.9 hook or shared-client extraction.
**Historical final v0.0.9 results verified 2026-09-17 JST:** Apple Container and native Docker
amd64/arm64 each passed **274 tests, 1 existing warning**, Ruff, strict mypy
(**15 source files**), genuine core-only/hook-only installation checks, and all
non-root production Japanese/API/worker smokes, MCP **`2026-07-28` and `2025-11-25`**,
and hook **`session_start`, `task_switch`, and `after_compaction`**.
Test elapsed times were **248.29 s** locally, **482.21 s** on native amd64, and
**374.33 s** on native arm64. The tested final local source matches published implementation
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050).
Actual logs from both jobs in
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)
confirmed that exact SHA and all checks, not just job status.
Timings are not performance benchmarks.
See [v0.0.9 evidence](../STATUS.md#v009--schema-7), not a production/DR acceptance claim.
The implemented Docker **`adapter-extras-check`** target checks genuine core-only
installation/missing extras, then hook-only **without MCP**, including explicit
JSON for failed HTTP. The container script builds it on local Apple Container
and both native Docker architectures. It **passed in all three environments**.
No original milestone or acceptance gate is complete.
Final v0.0.9 documentation
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)
also passed **274 tests** on both native architectures in
[CI 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689).
These are historical results, not v0.0.10 evidence.

**Historical v0.0.10 final local and native results verified 2026-09-17 JST.**
Apple Container and native Docker amd64/arm64 each passed **304 tests, 1 existing
warning**, plus **Ruff, strict mypy (16 source files), genuine core-only/hook-only
installation checks, and all non-root production smokes**:
Japanese/API/worker, both MCP eras, all three hook events, and atomic capture.
Test elapsed: **275.53 s** on Apple Container, **467.75 s** on native amd64,
and **434.40 s** on native arm64.
The final local source matches published implementation
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f).
Both native jobs in
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)
passed; actual logs verified that exact SHA, counts, timings, and all checks,
not just job status.
Coverage includes rollback faults, source/key deduplication races,
quota/RLS/deletion, and API process restart with an actual worker.
For a fresh fixture, the production smoke passed in all three environments.
It follows MCP/hook checks with Native capture →
pending job → actual worker CLI `--once` → episode/assertion recall →
same capture replay → source purge → job GET `404` and capture replay `404`.
All three final runs also cover committed HTTP 201 response loss followed by same-key
recovery of the exact pair with one publication, and stable replay of the original
failed capture job after explicit retry-child creation.
Elapsed time is not a performance benchmark; all acceptance gates remain incomplete.
See [v0.0.10 evidence](../STATUS.md#v0010--schema-7).
Final v0.0.10 documentation
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)
also passed **304 tests per native architecture** in
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760).
Those docs-run results are distinct from the implementation-run timings above;
neither run validates v0.0.11/schema 8.

**v0.0.11 application checks passed in all three environments**: the newly pinned upstream
DB profile, migration/schema/extension-version/schema guards,
normalization/digest/immutable model-space limits, exact/RRF math, ACL/time
prefiltering, coverage, purge/replay, and retained lexical/MCP/hook/capture checks.
Implemented fixtures include DB norm/dimension/composite-FK/eight-model guards,
direct RLS visibility/denied updates, ACL revocation, and actual schema-7→8
ledger-failure rollback of DDL/extension followed by retry without backfill.
The production vector smoke uploads **both episode and assertion projections**,
checks basis distances **[0, 1]** and RRF, then source purge and upload replay `404`.
Each environment passed **345 tests, 1 existing warning**, Ruff, strict mypy
(**17 source files**), core-only/hook-only installation checks, and all production smokes.
Test elapsed was **283.44 s local**, **404.40 s amd64**, **433.46 s arm64**, not
a performance benchmark. Published implementation
[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)
passed [CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403);
see [the exact evidence](../STATUS.md#v0011--schema-8).
Artifact inspection and the extension pin/license are verified separately.
The synthetic basis-vector fixture cannot qualify semantic quality, performance,
untrusted-vector robustness, production, DR, or full erasure.
