# Current contract and limitations

[日本語](STATUS-jp.md) | [Project README](../README.md) | [Implementation plan](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**Initial M1 slice, not completion of M0/M1, an MVP, or a production-qualified release.**
The implementation plan describes future requirements, not the current API.
Performance, memory quality, disaster recovery, and full-erasure acceptance
targets remain unmeasured or unqualified. Passing local and CI checks does not
complete these gates.

## Implemented surface

PostgreSQL is the sole application persistence store. There is no external
memory database, model service, durable queue, or file-based memory index.

| Endpoint | Current behavior |
|---|---|
| `POST /v1/observe` | Stores one episode with caller-supplied event time and consent reference. Returns revision `1`; `synthesis_job_id` is `null`, and no job is enqueued |
| `POST /v1/remember` | Stores an explicitly requested, structured assertion with literal evidence from readable episodes in the same scope |
| `POST /v1/recall` | Retrieves authorized episodes/assertions with PostgreSQL full-text search and builds a deterministic, byte-budgeted context pack |
| `POST /v1/explain` | Returns an episode or an assertion's evidence. Only revision `1` is accepted; no historical correction or ranking trace API |
| `POST /v1/forget` | Accepts explicit IDs with `preview` or `purge`; no arbitrary selector or `suppress` mode |
| `GET /v1/deletions/{receipt_id}` | Returns an authorized deletion receipt and the unresolved, operator-managed backup status |
| `GET /v1/capabilities` | Reports current features, limits, and unavailable capabilities; requires authentication |

Typed request and response models define the OpenAPI schemas exposed through
`/docs` and `/openapi.json`; no generated schema file is required.
`/healthz` reports process liveness following startup validation, not continuous
PostgreSQL readiness.

## Identity and authorization

- A static PEM RSA public key of at least 2048 bits verifies RS256 signatures.
  The configured issuer and audience, required `sub`, `iss`, `aud`, `iat`, and
  `exp` claims, and token time validity are checked.
- The verified external subject is mapped to a principal and tenant in
  PostgreSQL. The deployment has one configured issuer; the subject mapping
  is not a caller-selected tenant. Unknown subjects are unauthenticated.
- Request bodies cannot supply tenant/principal identity. Requested scopes
  narrow access; service checks and RLS enforce membership and permissions.
  Hidden or deleted object access returns `404` without an existence distinction.
- Runtime credentials must not be superuser, bypass RLS, or own application
  tables, including through owner-role membership. Admin migration/provisioning
  credentials are separate.
- JWKS discovery/rotation, delegated identities, multiple-issuer identity
  management, and public membership-administration APIs are not implemented.

Each authenticated API request uses a fresh, short-lived connection, not a
connection pool; there is no runtime `psycopg-pool` dependency.
A **tenant session advisory lock** serializes processing and
is held through transaction commit and delivery of the buffered HTTP response.
This correctness-first drain prevents a purge from acknowledging its barrier
while an earlier response for that tenant is still being sent by the service.
It cannot retract already-delivered data or bytes already handed to the network.
Slow clients can block that tenant; throughput has not been measured.

Administrative membership changes must acquire the **same session lock**,
update permissions and `access_epoch` in a transaction, commit, and only then
release the lock or close the connection. Changes outside this protocol are
not covered by the request/drain race guarantee. See the
[operations procedure](operations/README.md#membership-maintenance-and-request-drain).

## Evidence, consent, and time

`remember` requires `explicit_intent: true` and 1–32 distinct episode evidence
IDs. Each quote must occur literally in its episode's content; both objects
must be in the same scope. An assertion cannot serve as another assertion's
source in this slice. Subjects and values are text, not resolved entity graphs.

Literal quote validation establishes provenance, **not semantic support or
truth**. The service does not infer that a quote proves the supplied assertion.
Memory is `reported`, with confidence `score: null` and `method: "uncalibrated"`;
retrieved items require source refresh before asserting current external facts.
Retrieved content is evidence, not trusted instructions.

`consent_reference` records the caller's consent assertion. There is no consent
registry verification, capture-policy engine, or automatic secret/PII redaction.
Callers must supply only approved, already-sanitized data.

Assertions have valid/system intervals but only revision `1`. `as_of` and
`known_at` filter those intervals; episode filtering uses occurrence/recording
times. Omitted valid bounds are open-ended. This is **not** a correction history:
there is no supersession, historical revision update, or fact-conflict resolution.
Past query times do not bypass current authorization or deletion.

## Retrieval and budgets

Full-text search uses PostgreSQL's `simple` configuration, `plainto_tsquery`,
and `ts_rank_cd`. It is not BM25, Japanese word segmentation, vector search,
or hybrid retrieval. An empty `query` is allowed and selects accessible items
under scope/time constraints, subject to item and byte limits.

Despite the request field name `token_budget`, `utf8-bytes-v1` budgets the
serialized context pack in **UTF-8 bytes**, including its metadata and citations.
The response declares `budget_unit: "utf8_bytes"`, `token_count: null`, and
`exact_token_count: false`. This is a conservative fallback, not an exact model
tokenizer or a size limit for the entire HTTP response. Items are omitted whole;
if even pack metadata will not fit, the request returns `422`.

Limits include a 256 KiB request body, 100 returned items at most, and budget
values of 64–8,000 (implicit mode at most 2,000). `coverage.truncated` signals
item/budget omissions. An empty selection is `not_found` or `budget_exhausted`;
`retrieval_complete` does not mean complete knowledge of the world. Implicit
mode is a request option, not an implemented automatic harness hook.

## Idempotency and deletion

Mutations require `Idempotency-Key`; mutation and idempotency result commit
together before response delivery. Keys are scoped by tenant, principal, and
operation. Matching normalized requests reuse results after current access
checks; changed payloads return `409`. Exact replay targeting a deleted memory
returns `404`, not its old content.

Source-event deduplication uses a tenant-keyed HMAC of the **namespace + event ID**
pair, keyed in PostgreSQL by tenant/scope. It is not an event-ID-only hash.
An identical event can reuse its live episode even with a new idempotency key;
a conflicting payload returns `409`. Exact replay of a purged event returns
`404`; reusing its original source identity does not resurrect the episode.

`preview` reports the current target count without changing state or issuing a
reserved selector token. `purge` accepts 1–100 IDs and follows only the current
**episode → assertion** dependency relation, up to 10,000 dependent assertions.
Larger closures are rejected with `422`, not partially purged. Removing one
source deletes an affected multi-source assertion rather than regenerating it;
deleting an assertion does not delete its source episodes.

Purge synchronously removes target episode/assertion bodies and dependent
evidence quotes, then inserts scope-bound opaque deletion markers with timestamps
in `memory_ops.object_tombstone` **in the same transaction**. `memory.object`
has no `deleted_at` column; its SELECT RLS excludes objects with tombstones.
The transaction also advances `deletion_epoch` and commits a receipt.
HTTP `202` with `active_store_purged` is **not** a queued purge job or certification
of complete erasure. Opaque object records, object tombstones, audit/receipt
metadata, and tenant-keyed HMAC source/idempotency tombstones persist for the
tenant lifetime; there is no automatic expiry or full tenant-erasure workflow.

Receipts report `backup_status: "operator_managed"` and
`backup_retention_deadline: null`. Old database pages, WAL, replicas, backups,
and previously delivered context are not certified erased. The service is not
qualified for full-erasure guarantees or production compliance. Restores must
remain quarantined until the latest deletion ledger and ACL revocations have
been reapplied; automated recovery/replay and DR qualification are not implemented.

## Validation evidence

Public repository: [rioriost/pgag_memory](https://github.com/rioriost/pgag_memory).
On 2026-09-16, the implementation session supplied these successful results:

| Environment | Command | Result |
|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | Ruff, mypy, 19 tests, and runtime HTTP health smoke passed |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | Ruff, mypy, 19 tests, and runtime HTTP health smoke passed |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | Ruff, mypy, 19 tests, and runtime HTTP health smoke passed |

Both Docker jobs succeeded in
[GitHub Actions run 35078936073](https://github.com/rioriost/pgag_memory/actions/runs/35078936073).
These are the reported implementation/CI runs, not independent reruns by the
documentation task. They validate the initial test suite, not completion of
M0/M1, performance/quality targets, or disaster-recovery and full-erasure
qualification.

## Still roadmap work

Workers, job enqueue/status APIs, automatic synthesis, compaction, vector
embeddings/pgvector, Japanese tokenization, AGE, SQL/PGQ, checkpoints/recovery,
corrections/supersession, MCP, SDKs, and postgresem integration are absent.
The current interval filters and episode evidence do not complete the planned
bitemporal, graph, provenance, or deletion architecture.

See [ADR 0001](adr/0001-initial-slice.md) for these choices,
[operations](operations/README.md) for safe administration, and
[contributing](../CONTRIBUTING.md) for the container validation workflow.
