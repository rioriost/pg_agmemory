# Current contract and limitations

[日本語](STATUS-jp.md) | [Project README](../README.md) | [Implementation plan](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**v0.0.2 M1 assertion revisions are implemented; local and native Docker CI passed.
This is not completion of M0/M1, an MVP, or a production-qualified release.**
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
| `POST /v1/assertions/{memory_id}/revisions` | Appends a full replacement revision to the same assertion using an expected head, explicit intent, reason, and revision-specific episode evidence |
| `POST /v1/recall` | Retrieves authorized episodes/assertions with PostgreSQL full-text search and builds a deterministic, byte-budgeted context pack |
| `POST /v1/explain` | Returns the requested assertion revision and its evidence. Omitted revision still means `1`, not latest. Episodes have only revision `1`; no ranking trace API |
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

### Assertion revision contract

`POST /v1/assertions/{memory_id}/revisions` requires `Idempotency-Key` and
read/write access to the assertion's scope. Supply the entire replacement:

| Body field | Contract |
|---|---|
| `expected_revision` | Strict integer 1–1000 matching the current head |
| `value` | Nonempty text, at most 65,536 characters |
| `evidence` | 1–32 distinct episode IDs with nonempty literal `quote` values of at most 4,096 characters, all in the assertion's scope |
| `explicit_intent` | Must be `true` |
| `valid_from`, `valid_to` | Optional timezone-aware bounds; omitted/null means unbounded, not “keep the old bound.” If both exist, start must precede end |
| `reason` | Nonempty correction reason, at most 256 characters |

Subject, predicate, and scope are immutable and are not accepted in this body.
`201` returns `memory_id`, revision `expected_revision + 1`, and
`epistemic_status: "reported"`. A head mismatch is `409 revision_conflict`;
at the matching head of 1000, another revision is `422 revision_limit_exceeded`.
The limit is **1000 total revisions**, including the initial one.
Hidden/deleted/non-assertion targets return `404`.

The target `memory_id` is included in the idempotency request hash. An identical
key retry returns its originally committed revision reference even after later
corrections; it does not create another revision or substitute the latest head.
Reusing a key for a changed target/body is an idempotency conflict. Current
authorization and tombstones still apply to every replay.

### Temporal and evidence semantics

An INSERT trigger uses the DB clock to close the preceding system interval,
advance the assertion head, and assign the new interval atomically. Callers
cannot set system time. System ranges are contiguous `[)` intervals with GiST
non-overlap enforcement through `btree_gist`; deferred DB constraints require
evidence for every revision. Values, reasons, and evidence belong to revisions.

Each correction replaces the **entire valid interval**, not a partial-time
segment. For example, replacing an unbounded Gold assertion with Platinum
valid from October 1 means a September 16 query at the new `known_at` no longer
matches this assertion. It does **not** preserve Gold until October 1 as future
scheduling would. A `known_at` before the correction still selects the former
Gold revision. There is no automatic interval splitting, cross-assertion
supersession, or arbitration between competing facts.

`recall` selects by `as_of`/`known_at`, searches immutable identity text plus
that revision's value, and returns its exact revision and its own sources.
Old values are never paired with newer evidence. Episode filtering still uses
occurrence/recording times. All historical reads apply current ACLs/tombstones.
Context text labels `recorded_at` as `recorded=`: an assertion revision's system
adoption time or an episode's service recording time. It is not an `observed=`
label and does not claim a new external observation; `occurred_at` remains separate.

`explain` accepts an explicit revision from 1–1000, but **omitting it still
requests revision 1**, for compatibility—not the latest revision. Missing
revisions return `404`; episodes accept only revision 1. Assertion explanations
include `recorded_at` (system start), `known_until` (system end, null for the
current head), and `correction_reason` (null for revision 1), alongside that
revision's evidence. See [ADR 0002](adr/0002-assertion-revisions.md).

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
**episode → assertion** dependency relation across **all revisions**, up to
10,000 dependent assertions. Larger closures are rejected with `422`, not
partially purged. A source used by any historical revision conservatively
causes the whole assertion, all revision values/reasons, and all evidence to
be purged—even if the current revision no longer uses that source. There is
no regeneration; deleting an assertion does not delete its source episodes.

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

## Schema compatibility

Schema `002_assertion_revisions.sql` follows the unchanged `001_initial.sql`.
It moves existing assertion values, intervals, status, and evidence into
revision 1, preserving actual timestamps and existing idempotency records.
Legacy request serialization order remains compatible for exact replay.
The new runtime requires the ledger to equal `[1, 2]` exactly and rejects
older, newer, or incomplete histories.

Migration requires a forced-RLS-bypassing administrator with DDL rights and
`btree_gist`. `row_security = off` fails closed if RLS would filter the
backfill; it does not grant bypass privileges. Stop all old/new API traffic,
back up, migrate atomically, then start only the matching new API.
**The old API does not have this startup guard and must remain stopped.**
No rolling old-API compatibility or downgrade is supported. Follow
[operations](operations/README.md#v002-maintenance-migration).

## Validation evidence

Public repository: [rioriost/pgag_memory](https://github.com/rioriost/pgag_memory).
For **v0.0.2**, implementation commit
[5458402](https://github.com/rioriost/pgag_memory/commit/5458402), the implementation
session supplied the following successful results on 2026-09-16:

| Environment | Command | Result |
|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | Ruff, mypy, 32 tests, and runtime HTTP health smoke passed |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | Ruff, mypy, 32 tests, and runtime HTTP health smoke passed |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | Ruff, mypy, 32 tests, and runtime HTTP health smoke passed |

Both Docker jobs succeeded in
[GitHub Actions run 35082970968](https://github.com/rioriost/pgag_memory/actions/runs/35082970968).
The implementation session checked each job's exact logs, including test counts,
lint/type checks, and production HTTP health smoke—not just job status.
These are the reported implementation/CI runs, not independent reruns by the
documentation task.

The migration fixture starts from the actual unchanged
001 schema with two tenants' old values, timestamps, evidence, HMAC replay
records, and tombstones before applying 002. It also covers runtime rejection
of pre-migration schema and future/malformed ledgers.

The boundary fixture bulk-seeds a valid 999-revision prefix while retaining
the anchor's deferred history/evidence validation and FK/GiST constraints. It then exercises
public HTTP adoption from 999 to 1000, rejection of 1001 with `422`, replay at
the cap, explain of revision 1000, and current recall. It does not create the
entire prefix through HTTP.

These checks do not establish M0/M1 completion, measured performance/quality,
DR guarantees, or full-erasure qualification. For historical context, the
19-test **v0.0.1 baseline** is
[run 35078936073](https://github.com/rioriost/pgag_memory/actions/runs/35078936073),
not the current milestone's evidence.

## Still roadmap work

Workers, job enqueue/status APIs, automatic synthesis, compaction, vector
embeddings/pgvector, Japanese tokenization, AGE, SQL/PGQ, checkpoints/recovery,
cross-assertion supersession/fact arbitration, MCP, SDKs, and postgresem integration
are absent. This same-assertion revision milestone does not complete the planned
bitemporal, graph, provenance, or deletion architecture.

See [ADR 0001](adr/0001-initial-slice.md) for these choices,
[operations](operations/README.md) for safe administration, and
[contributing](../CONTRIBUTING.md) for the container validation workflow.
