# Current contract and limitations

[日本語](STATUS-jp.md) | [Project README](../README.md) | [Implementation plan](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

**v0.0.4/schema 4 tool-effect ledger is implemented; local and native Docker checks passed.
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
| `POST /v1/tool-effects` | Records/deduplicates an intent within an existing checkpoint run; returns its initial revision reference |
| `POST /v1/tool-effects/{memory_id}/transitions` | Appends a CAS-checked ledger transition; does not call the tool |
| `GET /v1/tool-effects/{memory_id}` | Returns current state, immutable event history, references, HMAC identifiers, and the run-invalidated flag |
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

State and references are copied, but dispatched snapshot hints become unknown.
In the same transaction, restore appends `unknown` events for every dispatched
live effect in the run, with `origin: "checkpoint_restore"`, then creates the fork.
The new revisions fence stale ledger writers through CAS; they cannot cancel
external calls already in flight. Exact restore replay creates neither a new
fork nor duplicate journal events.
Saved assertion references retain their exact historical revisions. Restore
neither selects the latest assertion revision nor automatically refreshes
current external facts; obtain fresh observations separately when needed.
GET/restore merge **all live effects in the run**, including other branches and
effects added after the saved checkpoint. `tool_effects` contains current
summaries; saved state and its checksum remain unchanged by this live view.

| Current ledger state | Snapshot hint | Reconciliation for this operation |
|---|---|---|
| `dispatched` / `unknown` | Any or absent | Required |
| `confirmed` / `failed` | Any or absent | Resolved by the caller-reported terminal record |
| `planned` | Absent or `planned` | Not required; still not execution permission |
| `planned` | `dispatched` / `unknown` | Required; record uncertainty, then reconcile a receipt |
| Untracked | Any hint, **including `planned`** | Required; also listed in `untracked_effects` |

`requires_reconciliation` contains all blocking operation IDs; any blocker makes
`resume_allowed: false`. This is an intentional tightening of v0.0.3's
snapshot-only behavior. `automatic_reexecution` is always false.
The host owns permission checks, approvals, and provider reconciliation;
no provider-query service, automatic execution, or harness adapter is implemented.

Identical-key retries retain the original checkpoint reference, not a new head.
Replay records contain no state; reads/restore retries rebuild envelopes under
current authorization, so current epoch metadata can change. Purged-reference
replay returns `404`.

**Callers must declare every memory dependency in `memory_refs`.** The
dependency DAG covers declared references, complete parent lineage, and the
run-wide effect-to-checkpoint dependency described below;
no semantic scanner discovers copied but undeclared source text. Callers remain
responsible for consent and secret/PII sanitization. Working snapshots,
compaction, and harness integration remain separate future work.
See [ADR 0003](adr/0003-checkpoints.md) and [ADR 0004](adr/0004-tool-effects.md).

## Tool-effect ledger

Planning and transitions require `Idempotency-Key` and current scope read/write
access; GET requires read access. **Create a bootstrap checkpoint first**:
planning does not create a run, and a missing run returns `404`.

| Planning field | Contract |
|---|---|
| `scope_id`, `run_id`, `operation_id` | Caller UUIDs; operation identity is tenant/scope/run/operation, not global |
| `tool_name` | Nonempty text, at most 256 characters |
| `action_hash` | Required lowercase 64-hex digest of the caller's canonical action; the server cannot verify it against an external call |
| `memory_refs` | Up to 100 distinct exact same-scope references: episode revision 1 or existing assertion revision 1–1000; defaults to empty, omitted revision is 1, not latest |

**Declare every memory dependency used by the action.** Checkpoints/effects are
not permitted reference kinds; undeclared copied data is not discovered.
The service persists only a tenant-HMAC `action_fingerprint` and a stable
64-hex `external_idempotency_key`, not raw action hashes or arguments.
GET exposes these identifiers, reference IDs, latest revision/status,
`run_invalidated`, and immutable history of at most four events.
Effects are excluded from recall/explain; use their dedicated GET endpoint.
Tool names, reasons, and receipt references must still be sanitized by the caller.

Planning returns `201` with `memory_id`, `revision: 1`, `status: "planned"`.
Different idempotency keys with the same operation identity and normalized body
deduplicate to that original revision-1 reference, even after later transitions.
Changed intent returns `409 operation_conflict`; changed body under the same
idempotency key returns `409 idempotency_conflict`. Hidden/purged identities cannot
be recovered by replay (`404` for an exact retry).
Each run allows **100 effects over its lifetime, including terminal records**;
the cap returns `422 effect_limit_exceeded`. Purging does not free capacity for
reuse because it seals the run. New run/operation IDs are not semantic deduplication.

Transitions require strict integer `expected_revision` 1–4, `status`, and a
nonempty `reason` of at most 256 characters. Success returns `201` with the
next revision/status; stale CAS is `409 revision_conflict`, and a forbidden
transition is `409 effect_transition_conflict`.

| Current state | Allowed next state |
|---|---|
| `planned` | `dispatched`, `unknown` |
| `dispatched` | `unknown`, `confirmed`, `failed` |
| `unknown` | `confirmed`, `failed` |
| `confirmed`, `failed` | None; terminal states are immutable |

`planned → unknown` records uncertainty about an off-protocol/legacy attempt;
it does not authorize execution. There is no `unknown → dispatched`.
Terminal transitions require a nonempty `receipt_reference` (at most 256
characters) and `receipt_source: "provider_receipt"` or `"operator_review"`;
other states require both receipt fields to be omitted or null. These are **caller-reported references,
not server-verified outcomes**. GET history includes recorded time, reason,
receipt fields, and `origin`; the DB assigns timestamps and actors and enforces
the FSM, contiguous revisions, head advancement, and reference constraints.
RLS and composite tenant/scope foreign keys remain in force; no privileged helper.

Idempotency retains only the result reference/revision/status and keyed request
digest, not receipt or body copies. Plan and transition responses, including
dispatch acknowledgments, are **historical revision references, not current-state
snapshots or execution authorization**. Under current authorization, same-intent
planning retries and exact transition replays can return the original reference
for a surviving effect even after its run is sealed. This does not unseal the
run: fresh dispatch remains rejected, and exact replay of a purged effect remains `404`.
The harness must durably record dispatch before an outside call and use the
stable external key where the provider supports it. No external exactly-once,
approval, automatic retry/execution, or provider-receipt-query guarantee is made.

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
Declared episode/assertion-to-effect references add
**source → tool effect → every checkpoint in that scope/run**, including old
checkpoints with empty references and snapshots predating the effect.
The total limit is 10,000 dependents plus requested roots, not 10,000 per layer.
Larger closures fail with `422` without partial purge. Any historical source
conservatively removes the assertion's entire history and all dependent
checkpoint payloads, even when later snapshots omit that source. Every child
inherits its full parent lineage; forks cannot escape it.

Purging **any** effect permanently sets the run's `effects_invalidated` flag.
New effect plans/dispatch return `409 effect_run_invalidated`; new checkpoints
are rejected with `409 checkpoint_invalidated`, and no checkpoint in that run
can resume. Other independent effects are not automatically purged: GET still
returns their history with `run_invalidated: true`, and allowed reconciliation
transitions remain possible, including `unknown → confirmed/failed`, but no dispatch.

Affected branch heads are permanently invalidated and their IDs cannot be
reopened. Deleting a checkpoint does not delete its ancestors or source
episodes. There is no regeneration. The existing tenant session lock keeps
the closure, payload purge, run/branch invalidation, and read barrier atomic.

Purge synchronously SQL-deletes target episode/assertion/checkpoint/effect payloads,
effect events (including reasons/receipt references), and dependent quotes/references,
then inserts scope-bound opaque deletion markers with timestamps
in `memory_ops.object_tombstone` **in the same transaction**. `memory.object`
has no `deleted_at` column; its SELECT RLS excludes objects with tombstones.
The transaction also advances `deletion_epoch` and commits a receipt.
HTTP `202` with `active_store_purged` is **not** a queued purge job or certification
of complete erasure. Opaque operation registry/run flags, run/branch metadata,
object records, tombstones, audit/receipt
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

Additive schema `004_tool_effects.sql` follows unchanged migrations 001–003.
It adds the effect ledger/operation registry and run-invalidation flag without
rewriting saved checkpoint checksums or assertion history.
The v0.0.4 runtime requires the ledger to equal `[1, 2, 3, 4]` exactly and rejects
older, newer, or incomplete histories.

Migration requires a forced-RLS-bypassing administrator with DDL rights and
`btree_gist`. Migration 002's `row_security = off` fails closed if RLS would filter
its backfill; it does not grant bypass privileges. Stop all old/new API traffic,
back up, migrate atomically, then start only the matching new API.
**Keep all old images stopped; v0.0.1 has no schema startup guard.**
No rolling old-API compatibility or downgrade is supported. Follow
[operations](operations/README.md#v004-maintenance-migration).

## Validation evidence

Public repository: [rioriost/pgag_memory](https://github.com/rioriost/pgag_memory).
For **v0.0.4/schema 4**, implementation commit
[4a7d3f8](https://github.com/rioriost/pgag_memory/commit/4a7d3f8),
the following results were verified on 2026-09-16:

| Environment | Command | Result |
|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | 73 tests, Ruff, strict mypy (8 source files), and production HTTP health smoke passed |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | 73 tests, Ruff, strict mypy (8 source files), and production HTTP health smoke passed |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | 73 tests, Ruff, strict mypy (8 source files), and production HTTP health smoke passed |

Both Docker jobs succeeded in
[GitHub Actions run 35098507356](https://github.com/rioriost/pgag_memory/actions/runs/35098507356).
Each job's actual logs confirmed the test count, lint/type checks, and production
HTTP health smoke, not just job status. All three test runs reported 2 existing warnings.

Coverage includes schema upgrades and unchanged v0.0.3 checkpoint checksums,
effect FSM/CAS and receipt requirements, current authorization, an actual API
process crash after simulated external success, restore/confirmation races,
restore-fence transaction rollback, and historical dispatch replay after permanent
run sealing. Exact 100-effect/run and 100-reference limits, historical assertion
dependencies, and run-wide effect/checkpoint purge are also exercised.
These checks do not establish complete M1, measured performance/quality,
external exactly-once behavior, backup/DR, or full-erasure qualification.

## Still roadmap work

Workers, job enqueue/status APIs, automatic synthesis, separate working snapshots/
compaction, embeddings/pgvector, Japanese tokenization, AGE, SQL/PGQ, a SQL/graph oracle,
provider receipt verification, actual harness integration/execution/recovery,
cross-assertion supersession/fact arbitration, MCP, SDKs, and postgresem integration
are absent. These typed checkpoint envelopes do not complete the planned
bitemporal, graph, provenance, or deletion architecture.

See [ADR 0001](adr/0001-initial-slice.md) for these choices,
[operations](operations/README.md) for safe administration, and
[contributing](../CONTRIBUTING.md) for the container validation workflow.
