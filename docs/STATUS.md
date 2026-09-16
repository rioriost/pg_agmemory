# Current contract and limitations

[日本語](STATUS-jp.md) | [Project README](../README.md) | [Implementation plan](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**v0.0.3/schema 3 checkpoints are implemented; local and native Docker checks passed.
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
| `POST /v1/checkpoints` | Stores typed state with a branch-head CAS and returns an immutable checkpoint reference/checksum |
| `GET /v1/checkpoints/{checkpoint_id}` | Checks current access and integrity, then returns state, references, epochs, and reconciliation hints |
| `POST /v1/checkpoints/restore` | Copies a compatible checkpoint into a new target branch; never runs code or repeats external effects |
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

Limits include a 1 MiB body for checkpoint creation and 256 KiB for other
endpoints, 100 returned recall items at most, and budget
values of 64–8,000 (implicit mode at most 2,000). `coverage.truncated` signals
item/budget omissions. An empty selection is `not_found` or `budget_exhausted`;
`retrieval_complete` does not mean complete knowledge of the world. Implicit
mode is a request option, not an implemented automatic harness hook.

## Checkpoint contract

Checkpoint creation and restoration require `Idempotency-Key` and current
read/write access to the scope. GET requires current read access. Run/branch
UUIDs are caller-supplied identities within a tenant/scope, not global sessions.

| Creation field | Contract |
|---|---|
| `scope_id`, `run_id`, `branch_id` | UUIDs identifying the scope-local run and branch |
| `expected_head` | Required UUID or `null`; null only for an empty branch, otherwise the exact current checkpoint ID |
| `harness_id`, `harness_version` | Required nonempty text, at most 256 characters each; fixed for the run |
| `state_schema_version` | Only `1`, also the default |
| `event_watermark` | Required nonnegative 64-bit integer; cannot decrease relative to the parent |
| `state` | Typed `goal`, `constraints`, `completed_actions`, `decisions`, `unresolved_questions`, `next_actions`, and `pending_effects`; no arbitrary object/pickle |
| `memory_refs` | Up to 100 distinct `(memory_id, revision)` pairs in the same scope; episodes use revision 1, assertion revisions must exist; omitted list is empty and omitted revision defaults to 1, not latest |

The goal and state text entries are nonempty and at most 4,096 characters.
Constraints, decisions, unresolved questions, and next actions allow 64 entries
each; completed actions and pending effects allow 100. Each pending effect has
a unique UUID `operation_id`, a 1–256-character `description`, and a
`planned / dispatched / unknown` status. These are snapshot hints, not evidence
that an external action was executed or confirmed.

The server assigns checkpoint UUIDs and branch-local sequences starting at 1.
Branch-head CAS rejects stale heads with `409 checkpoint_head_conflict`;
decreasing watermarks yield `409 checkpoint_watermark_conflict`. An invalidated
branch cannot be reopened (`409 checkpoint_invalidated`). Existing checkpoint
payloads are immutable. The HMAC checksum uses `hmac-sha256-v1` and covers the
saved envelope, including references and capture epochs. GET checks checksum,
typed state, visible references, and current authorization. An invalid envelope
fails closed; hidden/deleted checkpoints return `404`.

The envelope includes `saved_access_epoch`, `saved_deletion_epoch`,
`current_access_epoch`, and `current_deletion_epoch`. Saved epochs are metadata,
not permission to use old authorization. Checkpoints are excluded from recall
and explain; use the checkpoint GET endpoint for their state.

### Restore and dependency boundary

Restore takes `checkpoint_id`, a never-used `target_branch_id`, and exactly
matching `harness_id`, `harness_version`, and `state_schema_version`.
It creates a new checkpoint at sequence 1 in the same scope/run, with the source
checkpoint as parent—even when that parent is on another branch. The original
branch/checkpoint is unchanged; restore never rewinds a head. An existing target
branch gives `409 checkpoint_branch_conflict`; incompatible harness/schema gives
`422 checkpoint_incompatible`.

State and references are copied, but dispatched pending effects become unknown.
Saved assertion references retain their exact historical revisions. Restore
neither selects the latest assertion revision nor automatically refreshes
current external facts; obtain fresh observations separately when needed.
GET/restore list dispatched or unknown operation IDs in `requires_reconciliation`
and set `resume_allowed: false` while any remain. `automatic_reexecution` is
always false, even when resumption is allowed. The caller must reconcile with
the external system: there is no durable effect ledger, receipt-query service,
actual code execution, or harness adapter.

Identical-key retries retain the original checkpoint reference, not a new head.
Replay records contain no state; reads/restore retries rebuild envelopes under
current authorization, so current epoch metadata can change. Purged-reference
replay returns `404`.

**Callers must declare every memory dependency in `memory_refs`.** The
dependency DAG covers declared references and the complete parent lineage only;
no semantic scanner discovers copied but undeclared source text. Callers remain
responsible for consent and secret/PII sanitization. Working snapshots,
compaction, and a durable external-effect ledger remain separate future work.
See [ADR 0003](adr/0003-checkpoints.md).

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
reserved selector token. `purge` accepts 1–100 root IDs and follows
**episode → assertion (any revision) → checkpoint references → descendant/fork
checkpoints**. Direct episode-to-checkpoint references also participate.
The total limit is 10,000 dependents plus requested roots, not 10,000 per layer.
Larger closures fail with `422` without partial purge. Any historical source
conservatively removes the assertion's entire history and all dependent
checkpoint payloads, even when later snapshots omit that source. Every child
inherits its full parent lineage; forks cannot escape it.

Affected branch heads are permanently invalidated and their IDs cannot be
reopened. Deleting a checkpoint does not delete its ancestors or source
episodes. There is no regeneration. The existing tenant session lock keeps
the closure, payload purge, branch invalidation, and read barrier atomic.

Purge synchronously removes target episode/assertion/checkpoint bodies and
dependent quotes/references, then inserts scope-bound opaque deletion markers with timestamps
in `memory_ops.object_tombstone` **in the same transaction**. `memory.object`
has no `deleted_at` column; its SELECT RLS excludes objects with tombstones.
The transaction also advances `deletion_epoch` and commits a receipt.
HTTP `202` with `active_store_purged` is **not** a queued purge job or certification
of complete erasure. Opaque run/branch metadata, object records, tombstones, audit/receipt
metadata, and tenant-keyed HMAC source/idempotency tombstones persist for the
tenant lifetime; there is no automatic expiry or full tenant-erasure workflow.

Receipts report `backup_status: "operator_managed"` and
`backup_retention_deadline: null`. Old database pages, WAL, replicas, backups,
and previously delivered context are not certified erased. The service is not
qualified for full-erasure guarantees or production compliance. Databases
restored from backups must remain quarantined until the latest deletion ledger
and ACL revocations have been reapplied; automated backup recovery/ledger replay
and DR qualification are not implemented.

## Schema compatibility

Additive schema `003_checkpoints.sql` follows unchanged `001_initial.sql` and
`002_assertion_revisions.sql`. It adds checkpoint runs, branches, payloads,
references, and their constraints without rewriting prior assertion history.
The v0.0.3 runtime requires the ledger to equal `[1, 2, 3]` exactly and rejects
older, newer, or incomplete histories.

Migration requires a forced-RLS-bypassing administrator with DDL rights and
`btree_gist`. `row_security = off` fails closed if RLS would filter the
backfill; it does not grant bypass privileges. Stop all old/new API traffic,
back up, migrate atomically, then start only the matching new API.
**Keep all old images stopped; v0.0.1 has no schema startup guard.**
No rolling old-API compatibility or downgrade is supported. Follow
[operations](operations/README.md#v003-maintenance-migration).

## Validation evidence

Public repository: [rioriost/pgag_memory](https://github.com/rioriost/pgag_memory).
For the **v0.0.3 checkpoint milestone**, implementation commit
[8adb40a](https://github.com/rioriost/pgag_memory/commit/8adb40a), the implementation
session supplied the following successful results on 2026-09-16:

| Environment | Command | Result |
|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | 54 tests, Ruff, strict mypy (7 source files), and production HTTP health smoke passed |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | 54 tests, Ruff, strict mypy (7 source files), and production HTTP health smoke passed |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | 54 tests, Ruff, strict mypy (7 source files), and production HTTP health smoke passed |

Both Docker jobs succeeded in
[GitHub Actions run 35088907082](https://github.com/rioriost/pgag_memory/actions/runs/35088907082).
The implementation session checked each job's exact logs, including test counts,
lint/type checks, and production HTTP health smoke—not just job status.
These are the reported implementation/CI runs, not independent reruns by the
documentation task.

The suite covers real API-process crash/restart with checkpoint/idempotency
recovery, actual 1 MiB body/100-reference boundaries, and staged schema
001 → 002 with actual revision-2 data → 003 preserving historical assertion data.
Passing these checks does not establish complete M1, measured performance/quality,
backup/DR guarantees, or full-erasure qualification.

## Still roadmap work

Workers, job enqueue/status APIs, automatic synthesis, separate working snapshots/
compaction, embeddings/pgvector, Japanese tokenization, AGE, SQL/PGQ, a SQL/graph oracle,
durable external-effect ledgers, actual harness execution/recovery,
cross-assertion supersession/fact arbitration, MCP, SDKs, and postgresem integration
are absent. These typed checkpoint envelopes do not complete the planned
bitemporal, graph, provenance, or deletion architecture.

See [ADR 0001](adr/0001-initial-slice.md) for these choices,
[operations](operations/README.md) for safe administration, and
[contributing](../CONTRIBUTING.md) for the container validation workflow.
