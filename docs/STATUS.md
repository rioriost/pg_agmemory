# Current contract and limitations

[日本語](STATUS-jp.md) | [Project README](../README.md) | [Implementation plan](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)

## M5 development: operator migration, monitoring and recovery checks

**0.4.0.dev1 / API v1 / schema 22** adds two core-only administrator commands,
without changing shipped migrations, dependencies, runtime vector selection or
serving authority:

| Surface | Implemented contract |
|---|---|
| `embedding-migration` | Read-only exact revision/input-digest coverage for declared source/target model spaces under the selected principal's runtime RLS. `ready`, `incomplete`, `blocked` and `empty` are distinct; neither an empty set nor equal row counts authorize cutover |
| `monitoring-export` | One-shot Prometheus textfile and explicit operator-policy alerts using existing snapshots. Private atomic publication, unknown states and failure-only output prevent zero-valued/stale success claims; external freshness checks remain mandatory |
| Schema21→22 upgrade | Rollback/retry assertions now cover AGE projection guard and captured-schema constraint as well as canonical validators, grants, RLS and triggers |
| Physical PITR | Required WAL continuity is derived from backup/target LSNs with timeline/segment boundaries and bundled-WAL limits, then checked again before owned-primary destruction |

The migration checker does not perform inference, register/delete models,
switch callers or authorize rollback. Explicit existing embedding upload/query
flows remain responsible for population and selection. Monitoring does not
create a scheduler, public endpoint, incident response, historical retention
store or automatic remediation. Neither new command mutates database state.
The existing upgrade contract remains maintenance-only with no in-place
downgrade; a verified isolated backup and matching old code are required.

Frozen local Apple Container arm64 / PostgreSQL 18.6 / pgvector 0.8.6 evidence:
**4,476 core cases passed / 134 optional skips**, Ruff, strict typing for
**57 source modules + 3 consumers**, then **18 separate COMMIT cases** and
**13 request-deadline cases**. All optional-install profiles and non-root
packaged lifecycle/recovery checks pass, including the new migration command's
empty-not-ready result and monitoring's private atomic success/failure output.
The separate actual physical PITR and complete HA v5 labs both pass with the
tightened shared WAL inspector. Earlier focused counts overlap this suite and
must not be added. Native CI qualification of this operator increment is pending.

**M5 production acceptance remains open, not silently implemented by these
tools.** The target deployment must supply load/concurrency, host/storage
failure domains, RPO/RTO, backup backend/inventory, recovery/retention windows,
legal holds, independently protected latest deletion/source authority, and
collector/alert/history ownership. Only then can backend-specific retention
enforcement and production recovery/upgrade acceptance be implemented and
measured. No production storage deletion, cloud deployment, paid inference,
arbitrary network-partition qualification or restart is performed.
See the [implementation/acceptance matrix](PG_AGMEMORY_IMPLEMENTATION_PLAN.md#m5-implementation-versus-deployment-acceptance)
and [operator contracts](operations/README.md#m5-operational-foundations).

## Previous M5 increment: controlled replication connection loss

**0.4.0.dev1 / API v1 / schema 22** adds a controlled connection-loss case after
synchronous renewal. Only the owned replication role is temporarily rejected
in the promoted node's HBA; exactly its replacement sender is disconnected.
Administrator/Native access and `remote_apply` remain unchanged. This is not a
host firewall modification or a shared-cluster fault.

One distinct guarded observation must time out in real `SyncRep` within the
existing five-second COMMIT contract, with no success receipt. Admission is
restored only after measured unknown client exit. The pending private evidence
does not authorize retry or success. Read-only reconciliation then waits for
the same synchronous replica and checks the exact receipt and state, without
replaying the write or executing effects. Original uncertainty, acknowledged
renewal state, source and checkpoints remain preserved.

Report/reference formats advance to v5; passing requires final reconciliation
and another live original-primary fence check. All production, serving, restart
and effect authority remains false. This qualifies neither blackholed transport,
arbitrary network partition, primary rejoin nor production RPO/RTO.
See [the owned HA contract](operations/README.md#explicitly-fenced-owned-ha-rehearsal).

Local Apple Container arm64 / PostgreSQL 18.6 / pgvector 0.8.6 passes the full
v5 lifecycle in **41 seconds**. During rejected replication admission, the
client exits unknown after **5.0017 seconds**, including **4.9765 seconds** of
observed `SyncRep`. Only afterward is the original HBA restored and the same
synchronous peer's exact receipt/state reconciled. Persisted v5 report and
both reconnected references validate; pending evidence never becomes a client
success receipt. The final fence check succeeds with no effect execution.

Ruff, strict typing for 55 modules and **915 targeted cases / 25 existing
live-DB skips** pass. Tests cover pending/final separation, pre-write isolation,
lost isolation during COMMIT, cleanup/cancellation, exact no-retry reconciliation
and scoped HBA/PID/restore failure paths. No service/schema/dependency or timeout
change is introduced. The exact implementation `123d0ec` subsequently passes
**all eight native jobs** in [run35977080118](https://github.com/rioriost/pg_agmemory/actions/runs/35977080118):
core, HA, PITR and patched AGE on amd64/arm64. Each core passes **4,262 cases /
134 skips**, followed by **18 separate COMMIT** and **13 request** cases and
the installation/packaged recovery lifecycle. This qualifies the declared
controlled connection-loss lab, not arbitrary network partitions.

## Previous M5 increment: isolated synchronous renewal

**0.4.0.dev1 / API v1 / schema 22** extends the owned SQL-only HA lab with a
separate synchronous-renewal phase after fresh replacement and fence verification.
It revalidates baseline, identity, timeline and asynchronous peer before changing
the owned promoted node's configuration. The restricted writer then verifies
`remote_apply` and commits one uniquely identified synthetic observation through
the shared guard, with unchanged five-second query/lock/COMMIT limits.

A sub-second replacement-replay pause must block acknowledgement in actual
`SyncRep`; after resume, guarded acknowledgement and exact primary/replica state
must agree. Only the declared single observation may change state. Prior COMMIT
uncertainty, checkpoint, source and dispatched-effect state remain intact.
Historical asynchronous evidence is retained as such; a final host-side fence
recheck is required. Reports/references advance to v4 rather than retroactively
qualifying v3.

This is a controlled synchronous-policy rehearsal, not authorization to restart
service/workers or effects, nor production HA, RPO/RTO, partition/rejoin or
independent failure-domain qualification. All such authority flags remain false.
See [the owned HA contract](operations/README.md#explicitly-fenced-owned-ha-rehearsal).

Local Apple Container arm64 / PostgreSQL 18.6 / pgvector 0.8.6 passes the full
v4 lifecycle in **33 seconds**, including a measured **0.0897-second** replacement
replay pause. The restricted `remote_apply` writer blocks in `SyncRep`, then
acknowledges the single new observation; exact renewed references and persisted
v4 report validate. Original uncertainty, earlier asynchronous evidence and
effect state remain unchanged, and both host fence rechecks succeed.
Ruff, strict typing for 55 modules and **767 targeted cases / 25 existing
live-DB skips** pass. No service/schema/dependency change or timeout relaxation
is introduced. The exact implementation `4895ca6` subsequently passes
**all eight native jobs** in [run35974807055](https://github.com/rioriost/pg_agmemory/actions/runs/35974807055).
Each core architecture passes **4,114 cases / 134 skips**, the separate
**18 COMMIT** and **13 request** cases and the installation/packaged recovery
lifecycle; HA, PITR and patched AGE pass on both architectures.

## Previous M5 increment: fresh replacement standby

**0.4.0.dev1 / API v1 / schema 22** extends the owned SQL-only HA rehearsal
after promotion with a fresh asynchronous replacement standby. A new physical
basebackup comes from the promoted node, not the destroyed original primary.
Both backup creation and restoration verify the manifest. Exact post-probe
memory, checkpoint, processing, source and effect state are compared read-only
on the replacement, preserving the original uncertain-COMMIT evidence.

The lab also checks streaming WAL advancement beyond that backup and rechecks
that the original primary remains absent. No old data directory, `pg_rewind`,
same-key replay, application write, effect execution or daemon startup is used
for this rebuild. The replacement is asynchronous: the promoted node retains
`synchronous_commit=on` without synchronous standby policy, and serving remains
unauthorized. New report/reference formats are `pgag-ha-drill-v3` and
`pgag-ha-reference-v3`; prior v2 evidence does not qualify this additional step.

This is a controlled fresh-node replacement, **not partitioned-primary rejoin,
automatic failover, renewed synchronous durability or a production RPO/RTO**.
See [the owned HA contract](operations/README.md#explicitly-fenced-owned-ha-rehearsal).

Local Apple Container arm64 / PostgreSQL 18.6 / pgvector 0.8.6 passes the full
v3 lifecycle in **25 seconds** (observation, not RTO). The fresh timeline-2
backup is **53,729,280 bytes**; replacement receive/replay reaches `0/7000000`
beyond backup end `0/5000120`. Original uncertainty and exact post-probe state
match, the sender remains asynchronous, and the final original-primary fence
check succeeds. Persisted report/reference models validate, and all three
temporary credential/config files are removed.

Ruff, strict typing for 55 modules and **643 targeted cases / 25 existing
live-DB skips** pass, including nonempty-directory rejection, stale/mismatched
backup/state/lineage, absent evidence and timeout failures. No service/schema/
dependency change is introduced. The exact implementation `ee2be6f` subsequently
passes **all eight native jobs** in [run35972913962](https://github.com/rioriost/pg_agmemory/actions/runs/35972913962):
core, HA, PITR and patched AGE on amd64 and arm64. Each core passes **3,990 cases /
134 skips**, then the separate **18 COMMIT** and **13 request** cases and complete
installation/packaged recovery lifecycle. Earlier in-progress observations do
not change the final result or expand the asynchronous replacement contract.

## Previous M5 increment: owned HA uncertain-COMMIT reconciliation

**0.4.0.dev1 / API v1 / schema 22** extends the opt-in SQL-only HA rehearsal
with a separate replay-pause COMMIT deadline case. Restricted runtime writers
use the shared guard and unchanged five-second acknowledgement limit. The
client must return `commit_outcome_unknown` and close its connection before
replay resumes; no success receipt, retry or compensating write is allowed.

Read-only primary/standby reconciliation follows replay resumption. The
uncertain operation remains distinct from the three acknowledged writes,
including through the existing owned-primary destruction, fenced promotion,
exact state comparison and degraded probe. Checkpoint, source cursor and
dispatched-effect state remain intact; no external effect or daemon starts.
The new report/reference formats are `pgag-ha-drill-v2` and
`pgag-ha-reference-v2`. Historical v1 qualification does not cover this case.
See [the owned HA contract](operations/README.md#explicitly-fenced-owned-ha-rehearsal).

Local Apple Container arm64 / PostgreSQL 18.6 / pgvector 0.8.6 passes the
complete v2 rehearsal: COMMIT wait **5.001 seconds**, observed `SyncRep`
**4.986 seconds**, then read-only replica agreement and timeline **1→2** with
original uncertainty and dispatched-effect state preserved. The client exits
before replay resumes; the entire drill takes 21 seconds, not an RTO claim.
The persisted report and reconciled reference validate against their v2 models.
Ruff, strict typing for 55 modules and **493 targeted cases / 25 existing live-DB
skips** pass. The exact implementation `05900a6` subsequently passed **all eight
native jobs** in [run35968449645](https://github.com/rioriost/pg_agmemory/actions/runs/35968449645):
core, HA, PITR and patched AGE on amd64 and arm64. Each core passes **3,840 cases /
134 skips**, followed by the separate **18 COMMIT** and **13 request** cases and
the complete installation/packaged recovery lifecycle. The v2 HA jobs preserve
the same five-second deadline and all no-restart/no-production-qualification flags.

The first live attempt correctly failed closed on an invalid test assumption:
the local receipt is not necessarily visible to an ordinary MVCC snapshot
during `SyncRep`, before transaction-array removal. The corrected observation
records a pre-COMMIT WAL-insert lower bound, not a commit-record watermark,
and checks the exact receipt only after replay resumes. No timing threshold
or production timeout was relaxed to obtain the passing result.

This adds one controlled physical-replica observation, not a general
reconciliation API or production failover controller. Catch-up occurs before
fencing; network partition, uncertain-catch-up promotion, rejoin, independent
failure domains and production RPO/RTO remain unqualified. All service/effect
restart and production-qualification flags remain false.

## Previous M5 increment: Native request deadlines

**0.4.0.dev1 / API v1 / schema 22** adds a cumulative **30-second `/v1/`
application budget**, including **one second reserved for error delivery**.
Authentication, receive, connection/admission, handler, COMMIT and response
work share the first 29 seconds; existing shorter phase limits remain.
No migration, dependency or resource-profile change is introduced.

An expired request closes its owned connection before task cancellation.
Pre-COMMIT expiry is `503 request_deadline_exceeded`; in/after-COMMIT expiry
before response delivery is non-retryable `commit_outcome_unknown`. Buffered
success remains suppressed, partial delivery is never followed by a second
response, and normal/error sends both have an absolute endpoint. External
cancellation remains cancellation; completed timers cannot affect later requests.
See [the request deadline contract](operations/README.md#native-request-deadline).

Local Apple Container arm64 evidence (PostgreSQL 18.6 / pgvector 0.8.6):
Ruff, strict typing (**55 modules + 3 consumers**), and **3,695 core cases
passed / 134 skipped**. Separate fresh missing-standby primaries passed the
unchanged **18 COMMIT cases** and **13 request cases** (57.90 seconds for the
request stage), including the production-default cumulative budget, startup/
admission waits, lost ACK/cancel-channel blackhole, response stalls and timer
isolation. All optional-install and packaged API/SDK/worker/MCP/hook/recovery
smokes passed. The exact implementation `df1055b` subsequently passed **all eight
native jobs** in [run35961680440](https://github.com/rioriost/pg_agmemory/actions/runs/35961680440):
core, HA, PITR and patched AGE on both amd64 and arm64. Each core job passes
**3,695 cases / 134 skips**, **18 separate COMMIT cases**, **13 separate request
cases**, optional installations and the full packaged lifecycle. These results
qualify the bounded application contract, not production HA or network RPO/RTO.

The admission regression measures client exit before releasing its owned
blocker and observes backend cleanup separately. Each fault pytest process
receives a fresh cluster for the legacy-migration fixture; tests do not weaken
the fixture or mistake a still-waiting backend for a failed client deadline.

This does not prove backend termination, rollback, remote durability or safe
restart. A responsive event loop is required; client/proxy latency and blocking
code are not covered by a network SLA. Authoritative reconciliation,
partition/rejoin, independent failure domains and production RPO/RTO remain open.

## Previous M5 increment: page-bounded assertion history

**0.4.0.dev1 / API v1 / schema 22** additionally materializes each history page
and limits relation targets/evidence to that page plus lookahead. Referenced
episodes are materialized before evidence aggregation. This is an
application query correction, not another migration or a timeout increase.
Current RLS, metadata-only output, descending ordinals, missing-row rejection
and whole-page evidence/endpoint validation remain unchanged.

The schema-22 [run35940492128](https://github.com/rioriost/pg_agmemory/actions/runs/35940492128)
at `8bb7073` passed **seven of eight native jobs**: amd64 core and both HA, PITR
and patched AGE jobs. Arm64 core passed the exact-limit COMMIT, replay and
Explain checks but failed the subsequent typed history page with a five-second
statement timeout (**3,633 passed / 133 skipped / 1 failed**).
The earlier local plan already showed repeated protected target scans
(`950 rows × 101 loops` on the first page); the native failure exposed the same
remaining read-path cost, not a COMMIT-outcome regression.
That failed job did not reach its isolated COMMIT stage.

Local Linux arm64/PostgreSQL 18.6 passes **27 focused history/revision cases**
and **30 SDK/contract/relation cases** (overlapping, not additive), plus Ruff
and strict typing for 54 source files and three consumers. Four real runtime
plan variants—automatic, forced custom, actual prepared generic and generic
nested-loop-only—check first/middle/last pages under the unchanged five-second
statement timeout. Target scans are limited to at most 101 rows in one pass.
A single linear evidence/history scan is permitted when chosen by the planner,
not repeated full-history scans per returned revision. Hidden historical
targets still fail selected pages but do not invalidate an unreturned sentinel.
The exact implementation `13fa132` subsequently passed **all eight native jobs**
in [run35943204446](https://github.com/rioriost/pg_agmemory/actions/runs/35943204446):
core, HA, PITR and patched AGE on both amd64 and arm64. Each core job passes
**3,639 cases / 133 optional skips**, followed by **18 separate real COMMIT
cancellation/deadline cases**, installation profiles, production smokes and
isolated recovery. This closes the native failures recorded above without
relaxing the five-second limits or RLS. It does not qualify production HA,
RPO/RTO, partition/rejoin, whole-request deadlines or the v6 resource profile.

## Previous M5 increment: bounded revision validation

**0.4.0.dev1 / API v1 / schema 22** adds migration 022 for deferred assertion
history and relation-shape checks. Bounded materialized inputs avoid repeatedly
scanning evidence and targets under invoker RLS. The 1,000-revision contract,
integrity checks and fixed five-second COMMIT acknowledgement budget are
preserved; no constraint work is moved outside that budget.
Use [matching components and the schema-22 upgrade procedure](operations/README.md#schema-22-revision-validation).
Schema-21 artifacts are historical, not authority to serve the upgraded graph.

Native [run35934493283](https://github.com/rioriost/pg_agmemory/actions/runs/35934493283)
at `3daae55` passed both HA, PITR and patched AGE jobs but failed both core jobs
at the exact 1,000-revision boundary (**3,618 passed / 130 skipped / 2 failed**
per architecture). Unlike the earlier packaging mock failure, this was a real
deferred-validation cost: local PostgreSQL `auto_explain` observed about
**16.4 seconds**, **500,500 evidence rows** and **7 million shared-buffer hits**
for one evidence check; the target join also visited nearly a million rows.
The deadline correctly withheld success, exposing a pre-existing quadratic
query plan. The correction does not increase the deadline or bypass RLS.
The failed run never reached the isolated COMMIT fault stage.

The frozen local Linux arm64/PostgreSQL 18.6 core run passes **3,634 cases /
133 optional skips**, plus **18 separate real cancellation/deadline cases**.
The exact-limit regressions measure successful guarded COMMIT at **less than
five seconds** for both assertion kinds and preserve replay/history. Schema
21→22 upgrade rollback and successful replacement preserve function identity,
invoker security, grants, RLS policies and existing memory constraints/triggers.
Invalid historical gaps, evidence, heads, intervals and relation targets still
produce confirmed rollback. All five installation profiles, non-root runtime
smokes and isolated schema-22 operational-state recovery pass.

The separate patched AGE run passes **218 cases** and real HTTP publication,
stale/disabled rejection and explicit rebuild checks, including historical
schema-20/21 receipts. Counts overlap and must not be added. Ruff and strict
typing for 54 source files and three consumers pass. These are local observations,
not completed native CI qualification of this checkpoint. The schema-22 v6 graph
recipe preserves workload/thresholds but has no new resource qualification;
frozen M4 v4 and schema-21 M5 v5 recipes retain their original identities.

Whole-request deadlines, authoritative reconciliation, partition/rejoin,
independent failure domains and production RPO/RTO qualification remain open.

## Previous M5 increment: bounded COMMIT acknowledgement

**0.4.0.dev1 / API v1 / schema 21** adds a fixed **5-second local
acknowledgement budget** at guarded outer COMMIT boundaries, for synchronous
administrator commands and asynchronous API/worker transactions. Expiry does
not depend on a server cancel request succeeding. It interrupts the exact
connection and preserves `commit_outcome_unknown`, suppressed success receipts,
non-retryable adapter errors and worker termination. Successful commits must
disarm the deadline before the connection can be reused.

Final local Linux arm64/PostgreSQL 18.6 checks pass **1,542 focused cases /
26 optional AGE/logical-slot skips**, **2,609 non-integration cases / 179
database-dependent skips**, and **18 separate cancellation/deadline cases**.
The first two counts overlap and must not be added. Real sync/async SyncRep
and dropped-COMMIT-ACK tests enforce **4.5 <= elapsed < 8 seconds** for the
fixed five-second budget, without external cancellation. They preserve local
rows, suppress buffered 201/admin receipts, stop worker reservation continuation
and leave successful connections usable across transaction bodies longer than
five seconds. SyncRep backend teardown occurs after measuring the client exit;
it is not proof that disconnect itself releases the backend.
Ruff, strict typing for 54 source files and three typed consumers pass; the
non-root packaged runtime exercises watchdog creation/disarm without source
mounts. These are local observations, not completed native CI qualification.

This does not change transaction-body, migration, provider or whole-request
limits. Client disconnect is not proof of backend termination, rollback or
replication. The budget is not a hard real-time guarantee under host failure;
external cancellation also retains the driver's separate cleanup semantics.
End-to-end deadlines, partition/rejoin, independent storage, authoritative
reconciliation and production RPO/RTO qualification remain open.
See [the deadline contract](operations/README.md#commit-acknowledgement-deadline).

## Previous M5 increment: unconfirmed COMMIT outcomes

**0.4.0.dev1 / API v1 / schema 21** now guards write-capable synchronous and
asynchronous transaction exits. A synchronous-replication cancellation warning
after local commit or a lost COMMIT acknowledgement cannot release a buffered
success response. Native API returns sanitized `503 commit_outcome_unknown`
with `retryable:false`; mutation adapters retain `outcome_unknown:true`.
Administrative mutations use the same error and uncertainty flag.
Workers stop without compensating failure writes, another model dispatch or
automatic loop retry. Migration, lexical rebuild and provisioning are covered;
read-only operator snapshots retain their non-mutating contract.

Local Linux arm64/PostgreSQL 18.6 passes **1,474 focused cases / 26 optional
AGE/logical-slot skips**, plus **10 separate real COMMIT-outcome cases** on an
owned primary configured with a missing synchronous standby. These observe
`SyncRep`, cancel only the named test backend, and confirm local persistence,
suppressed success receipts, worker stopping and ordinary pre-COMMIT rollback.
Warning-suppressing session defaults and local-only same-key replay are covered.
Ruff, strict typing for 53 source files and three typed consumers pass.
The main container runner now includes the isolated cancellation stage in both
native CI architectures; this local evidence is not completed native CI or
production qualification. No timeout or acceptance threshold was relaxed.

Native [run35924742146](https://github.com/rioriost/pg_agmemory/actions/runs/35924742146)
at `e5c7d55` passed both HA, PITR and patched AGE jobs, but both core jobs
failed one offline packaging-ledger test (**3,564 passed / 125 skipped** each).
Its unconstrained connection mock was mistaken for pipeline mode by the new
guard; this was not a database failure. The isolated test correction mocks and
asserts the guarded boundary explicitly while preserving all migration SQL/ledger
checks. Packaging plus guard contracts pass **100 local cases**. The failed
native runs did not reach the separate COMMIT-cancellation stage or qualify core.
The correction at `aa3eb98` subsequently passed **all eight native jobs** in
[run35932899722](https://github.com/rioriost/pg_agmemory/actions/runs/35932899722).
That qualifies the earlier cancellation increment, not the later deadline or
schema-22 changes.

Lookup and same-key replay can reconcile local receipts, not certify remote
durability or authorize promotion/restart. Production HA, partition/rejoin,
independent failure domains and RPO/RTO remain unqualified. Statement timeout
alone does not reliably bound synchronous COMMIT waiting; end-to-end deadlines
and lost-acknowledgement/partition recovery still need separate qualification.
See [the COMMIT contract](operations/README.md#unconfirmed-commit-outcomes).

## Previous M5 increment: replication observations and owned HA

The next **0.4.0.dev1 / API v1 / schema 21** increment adds `replication-status`
and a two-node SQL-only HA rehearsal. The observer distinguishes physical and
logical senders, preserves role-specific WAL fields, exposes no connection
details and grants no promotion/fencing authority. Live statistics are explicitly
non-atomic, and the observer's settings do not prove every writer's policy.

The owned lab requires explicit opt-in, verifies `remote_apply` on actual
restricted writer connections, observes a short `SyncRep` wait, rejects
promotion before fencing, and destroys/verifies absence of its primary before
promoting the exact standby. Three acknowledged writes, source cursor,
checkpoint and dispatched effect state survive timeline **1→2**. A single
post-promotion Native probe runs without starting an API or worker; the node
remains explicitly degraded and `serving_authorized:false`.

Packaged-image Linux checks pass **349 cases / one optional logical-slot skip**,
Ruff and strict typing for 52 source files. The logical configuration separately
passes all **80 replication-observer cases** without skips; counts overlap,
not additive. All five isolated install profiles pass. The actual HA run passes
with a **92.30 ms** controlled pause and a **16-second** observed total excluding
image construction; these are not latency or RTO guarantees. Its production,
failure-domain, network-partition and commit-timeout qualification flags remain false.
Native CI now runs independent core, AGE, PITR and HA pairs on amd64/arm64.

PostgreSQL synchronous wait cancellation can warn after local commit. This
lab rejects notices and never cancels/retries its writer; production API/worker/
administrator-wide cancellation, lost-acknowledgement and partition/rejoin
semantics remain an explicit M5 gate. No general HA or RPO-zero claim is made.
See [the HA contract](operations/README.md#explicitly-fenced-owned-ha-rehearsal).

## Previous M5 foundation: operator snapshots and paused PITR

Current development is **0.4.0.dev1 / API v1 / schema 21**, stage
`m5-production-candidate`, not a production-qualified release. This first
increment adds an administrator-only, read-only operational metadata snapshot
and a SQL-only physical basebackup/WAL point-in-time-recovery lab. The snapshot
does not take the tenant admission barrier or expose memory text, credentials
or source labels. The lab destroys its owned primary, restores to a named point,
and requires recovery to remain paused/read-only because later source authority
differs. Neither feature promotes a server, starts clients/workers or claims
HA, RPO/RTO, independent failure domains or backup-retention enforcement.

The new development graph recipe has its own v5 identity; the qualified M4 v4
recipe is preserved byte-for-byte in `examples/graph-resource-profile-m4-v4.json`.
No M4 timing result or qualification is relabeled for this development version.
Local Linux arm64 passes **662 focused cases**, strict typing for 51 source
files, and all five isolated package profiles. After composing monitoring with
the paused restore, the final **80 PITR contracts** pass separately (overlapping
the focused suite, not an additional aggregate total).
The actual physical drill passes: a verified **53,637,120-byte** basebackup and
**67,123,200-byte** WAL archive restore the named point after primary destruction.
It proves the post-backup target write survives, the later write is absent,
latest source history differs, and the real standby snapshot remains
non-authorizing. Its observed 17-second total excludes runtime-image construction
and is not an RTO claim. Private physical files are retained locally, not published.
The first native run exposed a test-image COPY omission masked by local source
mounts: PITR/AGE passed, but both core jobs stopped during test collection.
The isolated packaging fix **`0a5cd95`** passed image-only collection and contracts;
[run35876011552](https://github.com/rioriost/pg_agmemory/actions/runs/35876011552)
then passed all six jobs. Core each passed **3,304 cases / 116 optional skips**
and packaged/restore smokes; AGE each passed 84 profile and 214 enabled cases
plus canonical-only recovery; both native PITR labs passed. These qualify that
foundation, not the later HA increment.
See [M5 operations](operations/README.md#m5-operational-foundations).

## M4 integration pilot: v0.3.0

**M4 is complete within the original explicit-retention integration-pilot
scope:** service **0.3.0 / API v1 / schema 21**, stage `m4-integration-pilot`.
Version promotion adds no schema or locked dependency changes.
Frozen **`a86962994249bfd3678405bc7f8f7aa682540160`**
[run35848700066](https://github.com/rioriost/pg_agmemory/actions/runs/35848700066)
passed all four native jobs. Core amd64/arm64 each passed **3,159 cases /
116 optional skips**, packaged integration smokes and actual isolated recovery;
patched AGE each passed 84 profile cases and 214 enabled cases, original
traversal checks, real HTTP and canonical-only restore/rebuild.
The publication handoff changes documentation only; runtime source, dependency
lock, build scripts, tests, examples and workflows match that qualified checkpoint.

Its fresh exact-commit v4 graph run passes all six strata: **396 samples,
36 semantic probes, zero errors**, with AGE p95 **111.21–941.50 ms**, below the
unchanged strict **1,500 ms** target; SQL p95 is **30.11–85.65 ms**.
Recipe digest:
`cba4b77ce48090e5e675406fd3a26d6d1f4be1a8efbaa7837f4a1cd76f0aff55`.
Raw result SHA-256:
`2a4e96ca8dd9fc46717862bf7371a54051509b04b5ec9f70a03a78022c0886fb`.
These are warm quiescent service calls with DB 6 CPU/24 GiB and application
2 CPU/8 GiB, plus one separately recorded overhead CPU per VM, on a shared host.
These are not HTTP, cold, concurrent or general capacity
qualification. No planner/JIT/timeout setting or threshold was relaxed.

Two earlier exact runs on `dfe5d47` failed the chain-medium administrative
artifact-export setup and therefore remain **unqualified**, despite passing
other strata. Standalone diagnostics did not reproduce their cause. The final
checkpoint adds only allowlisted administrative error reporting and tests,
not a speculative runtime fix; the failure evidence is retained. The successful
fresh run is not a claim of a startup reliability SLO or a relabeling of those
earlier results.

The final feature increment adds administrator-planned snapshot discovery and
Native provenance purge. All registered readers must first be terminal-deleted,
and every dedicated scope must have capture disabled. Discovery verifies actual
scope/source/time/content identity for every episode, refuses mixed scopes and
oversized inventories, and requires exact plan/epoch equality. Deletion executes
as the actual non-owner `pgag_runtime` maintenance identity, not with an
administrator RLS bypass. Its receipt is replayable, capture stays disabled,
and backup retention remains operator-owned.

Local Linux arm64 passes **922 combined cases**, including four composed
real-HTTP integration scenarios connecting snapshots, signed notices, compiled
LangGraph, checkpoint/effect reconciliation, task isolation, partial capture
outcomes and source purge. Source-purge cases exercise 100/101/107 roots/bindings,
32/33 scopes, role/permission boundaries, stale plans, rollback, uncertain
commit and response barriers. Ruff, 50-source/three-usage strict typing and
all isolated package profiles pass without adding SDK dependencies to core.
The [original M4 acceptance inventory](PG_AGMEMORY_IMPLEMENTATION_PLAN.md#m4-explicit-retention-pilot-acceptance)
separates this explicit-retention pilot from optional postgresem deployment and
automatic shared-business retention. Real source-specific production transport,
outbox operation, HA/PITR/RPO/RTO and backup-retention enforcement are not
claimed. The trusted publishing harness and lease-bound reader are distinct;
single-scope checkpoints and operator-owned source authorization remain explicit.
M5 is the next production-candidate phase, not an unfinished M4 release gate.

## Previous M4 increment: signed source-notice ingress

The current increment adds a local `source-notice apply` receiver for bounded
RS256 deliveries. One trusted profile pins the signer/public key, exact
issuer/audience/subject and existing source/reader mapping. Closed signed
headers/claims and a maximum 300-second envelope lifetime are verified before
database access; existing source-access transactions enforce original lease
expiry, sequence, replay and terminal deletion semantics. The result separates
verified notification signatures from independently verified upstream
authorization, which remains false. There is no remote key fetch, public
webhook, implicit binding, new ledger/migration/dependency or automatic retry.
See the [signed delivery contract](operations/README.md#signed-source-notice-receiver).
The real source connector, reliable delivery and complete source-to-memory
deletion mapping remain open; automatic shared-business retention stays off.

Local Linux arm64 passes **621 combined signed-notice/source/dataset/scope/
readiness/snapshot/recovery cases**, including 272 signed-notice cases.
Original-byte signatures exercise duplicate JSON keys, wrong keys/algorithms,
identity and claim restrictions, exact token/file/time bounds, and rejection
before database/network access. Database/real-HTTP cases preserve replay,
lease expiry, gap/deletion denial, response barriers and uncertain-commit
classification. Ruff, strict typing for 48 source files and three usage
contracts pass. All five isolated package profiles verify an actual signature
using an ephemeral in-memory RSA key; core needs neither SDK nor HTTPX.
These are bounded local results, not real upstream delivery or full M4
qualification. Service/API/schema remain 0.3.0.dev1/v1/21.

Frozen **`7ba2bc716b034731919efaf7e8f7a9f02be789fc`**
[run35837311344](https://github.com/rioriost/pg_agmemory/actions/runs/35837311344)
passed all four native jobs: core amd64/arm64 each passed **3,020 cases /
116 optional skips**, packaged smokes and ordinary recovery; patched AGE each
passed 84 profile cases and 214 enabled cases, real HTTP and canonical-only
recovery. This qualifies the signed-notice checkpoint, not the newer purge
and composed pilot increment.

## Previous M4 increment: dataset and source-access coordination

The schema-preserving dataset increment adds administrator-only
`source-dataset get/revoke`: discover at most 100 registered readers and revoke
their memberships atomically using both tenant-epoch and exact target-set CAS.
Empty/oversized matches are explicit errors; no target list is silently
truncated. It leaves source notification cursors/history and unrelated grants
unchanged. It is not a durable dataset block, source authentication or payload
purge: later valid source allows and new bindings remain possible.
See [registered reader operations](operations/README.md#registered-dataset-readers).

Local Linux arm64 passes **349 dataset/source/scope/recovery/readiness/snapshot
cases**, including 92 new dataset cases. The bound is exercised with 100, 101 and
107 actual bindings, checking that discovery fetches at most 101 rows and never
applies a partial oversized batch. Coverage includes same-epoch target drift,
transaction/epoch-exhaustion rollback, concurrent mutation and response barriers,
uncertain commit, real HTTP denial, unchanged-source signed recovery, exact
notice replay and explicit later reopening. Ruff, strict source/usage typing and
isolated core/hook/sdk/providers/LangGraph installs pass; the core administrator
command needs no SDK. These results do not claim full M4 or upstream integration
acceptance. Service/API/schema remain 0.3.0.dev1/v1/21.

Frozen **`20ce3cae7ac01ca365e70c9bae4deb3a93917f41`**
[run35820790247](https://github.com/rioriost/pg_agmemory/actions/runs/35820790247)
subsequently passed all four native jobs: core amd64/arm64 each passed
**2,748 cases / 116 optional skips**, packaged smokes and ordinary recovery;
patched AGE each passed 84 profile cases and 214 enabled cases, real HTTP and
canonical-only recovery. This qualifies the dataset checkpoint, not the newer
signed-notice receiver.

Current development is **0.3.0.dev1 / API v1 / schema 21**, stage
`m4-integration-pilot`, not a v0.3 release. A private administrator-managed
binding and notification ledger now coordinate one source reader's access:
read-only leases are limited to 300 seconds from the asserted verification time,
exact duplicate notices do not renew grants, sequence gaps/invalid leases deny
access atomically, and source deletion is terminal for the binding.
Membership, access audit and notification state/history share one transaction.
Ordinary `scope-access set` cannot bypass a bound target; emergency revoke and
unrelated scopes remain supported.

The [coordinator contract](operations/README.md#m4-durable-source-access-coordinator)
requires trusted upstream authentication and stable target/sequence mapping.
This is a local admin command, not a webhook or upstream signature verifier.
It does not discover dataset-wide targets, physically purge on a notification,
execute tools or enable automatic shared-business retention.
Full M4 integration acceptance remains open.

The upgrade requires migration 021 and matching components. Old graph-generation
history remains readable but stale; rebuild schema-21 artifacts before opting
into AGE. Source state and event tables are exact recovery fingerprints, not
new mutable import surfaces: recovery refuses changed source authority since a
backup. This bounded policy must not be described as arbitrary notification
replay, automatic reactivation or general source-system disaster recovery.
The published v0.2.0 tag and its schema-20 resource evidence remain unchanged.

For the preceding per-reader coordinator increment, local Linux arm64 passes
**940 focused coordinator/SDK/integration/recovery
cases**, Ruff and strict source/usage typing. Separate core/hook/sdk/providers/
LangGraph installations pass, including the administrator CLI without SDK
dependencies. Actual ordinary backup/restore and canonical-only patched-AGE
restore pass with schema 21. Patched AGE passes **214 enabled cases** and the
real non-root HTTP smoke, including explicit stale schema-20 receipt refusal
and schema-21 rebuild. Another **57 runner/recovery contracts** cover explicit
runtime reuse and caller-owned image preservation. The local runtime used
an independently staged build context with byte-identical package inputs after
the local builder failed to transfer the allowlisted context; the default
native-CI build path is unchanged. Source-history drift is rejected in both directions;
unchanged source leases/cursors are preserved without grant refresh. These are
bounded local results, not upstream authentication, a new resource timing
qualification or complete dual-native M4 acceptance.

The preceding coordinator checkpoint
**`ab4de8889a26ed7bd89275c46d5b34f6362309d8`**
[run35818645092](https://github.com/rioriost/pg_agmemory/actions/runs/35818645092)
subsequently passed all four native jobs. Core amd64/arm64 each passed
**2,655 cases / 116 optional skips**, packaged smokes and ordinary recovery;
patched AGE each passed 84 profile cases and 214 enabled cases, real HTTP and
canonical-only recovery. This qualifies that schema-21 coordinator checkpoint,
not the newer dataset-administration increment above.

## Previous M4 increment: explicit external-source snapshots

The next bounded increment adds a Native SDK adapter for a versioned historical
source-result envelope: source system/dataset/subject, semantic revision, query
ID, observation time, ACL version and exact-text digest. Source-query success
and Memory capture success/failure/unknown outcome are separate. There is no
source query, automatic lease renewal, model call, migration or new dependency.
Snapshots always require a fresh source query before use as current values.

The deployment profile uses dedicated source scopes, existing expiring reader
grants and explicit administrator revocation, plus Native provenance/forget for
source deletion. These controls are server-enforced, not just adapter checks.
Envelope metadata is caller-asserted, not a signed authorization receipt.
See the [source contract](operations/README.md#m4-external-source-snapshot-pilot).
Upstream authentication/notification coordination, dataset-wide deletion target
discovery and automatic shared-business retention remain unqualified and off;
this is not full M4 completion or a postgresem connector.

Local Linux arm64 passes **290 combined source/scope-access/SDK/LangGraph
cases**, Ruff and strict source/usage typing. The six source integrations include
real HTTP capture and a simulated lost post-commit response with explicit stable
retry, configured lease expiry, administrator CAS revocation/renewal, independent
task-memory access, and original/derived/checkpoint purge. Tests preserve
tombstone identities and distinguish invalidated checkpoint heads from hidden
objects; neither old nor new idempotency keys resurrect the purged source event.
Separate core/hook/sdk/providers/LangGraph installation checks pass without a new
dependency. These are focused local results, not upstream connector or full
native distribution qualification of this newer increment.

Frozen **`e7e644f9175b209124bff09d74319f838729191d`**
[run35816137319](https://github.com/rioriost/pg_agmemory/actions/runs/35816137319)
subsequently passed all four native jobs: core amd64/arm64 each passed
**2,483 cases / 113 optional skips**, packaged smokes and isolated recovery;
patched AGE each passed 84 profile cases and 207 enabled cases, plus traversal
checks and canonical-only recovery. This qualifies the schema-20 snapshot
increment, not the newer schema-21 coordinator.

## Previous M4 increment: explicit LangGraph pilot

The first integration increment is an optional LangGraph 1.2.11 safe-boundary
bridge over the Native SDK. One trusted scope/run/branch, explicit admitted
capture, bounded recall, caller-owned planning and typed checkpoint publication
remain separate from model judgment. Restore returns the complete current
effect/reconciliation envelope and never executes a node, tool or approval.
It is not a general LangGraph checkpoint saver or scheduler-resume mechanism.
The [operations contract](operations/README.md#m4-langgraph-safe-boundary-pilot)
records partial outcomes, identity, source freshness and retry limits.

The extra is absent from the ordinary service runtime. Existing locked packages,
API v1/schema 20 and M3 release semantics are unchanged. The development package
at that checkpoint identified as 0.2.0; that increment was not a new release or complete
M4 qualification. External-source connectors/freshness propagation and further
integration acceptance remain open. The v0.2.0 tag at `82149e5` is unchanged;
both publication workflows 35810379994 and 35810381591 passed all four native
core/AGE jobs.

Local Linux arm64 qualification of this increment passes **215 combined
SDK/pilot cases**, including a real HTTP graph/checkpoint/unknown-effect restore,
source-purge denial, revoked membership, reference-union limits and failure
propagation. Ruff and strict source/usage typing pass. Separate isolated-extra
builds cover core/hook/sdk/providers/LangGraph; the non-root production image
remains free of LangGraph, LangChain and LangSmith. These are focused local
results, not full dual-native M4 distribution or external-source qualification.

Frozen **`365b1488a035c1f8227c5a656c4e5bad1bf5549a`**
[run35813098865](https://github.com/rioriost/pg_agmemory/actions/runs/35813098865)
subsequently passed all four native jobs. Core amd64/arm64 each passed
**2,446 cases / 113 optional skips**, packaged smokes and isolated recovery;
patched AGE each passed 84 profile cases and 207 enabled cases, plus original
traversal checks and canonical-only recovery. This qualifies distribution of the
LangGraph increment, not the newer external-source code above.

## M3 v0.2.0 release contract

The release identity is **service0.2.0 / APIv1 / schema20**, stage
`m3-graph-mvp`. Promotion changes version/capability metadata and release-profile
identity, not query semantics, schema, dependency pins or the selected AGE source.
The graph resource v2 digest is
`37b0379d66341047d2def85621feff9f949cc5a42e3826d3746f51c175e0db0d`;
only profile name/service version differ from v1. The exact release build passed native distribution and a new resource run before
tag publication. No previous raw
artifact is relabeled or given new qualification flags.

Frozen **`4204892fa90fb93a62a24f78545ef89a14abbc2e`**
[run35807408792](https://github.com/rioriost/pg_agmemory/actions/runs/35807408792)
passed all four native jobs. Core amd64/arm64 each passed **2,394 cases /
113 optional skips**, all packaged smokes and ordinary v7 restore; patched AGE
each passed84 profile contracts, all59 original checks,207 enabled cases, real
HTTP and exact canonical-only recovery. Core pytest took1655.75/1408.79 seconds;
AGE cases368.62/372.59. Both ordinary restores retain35 canonical tables,
20 denials and11 reservations. Both AGE restores retain35 canonical/24 operational
fingerprints apart from the intended projection delta, old generation/key lineage,
registry1→2→3, purge/ACL/history controls and zero automatic activation/model calls.

The separate exact release resource run passes all six strata,396 samples and
36 semantic probes, with AGE p95 **116.54–1,018.09 ms** below the unchanged
strict1,500 ms target. SQL p95 is28.14–89.64 ms. It remains a declared warm,
quiescent graph-only profile, not a speedup or full-S/cold/concurrent capacity claim.
The publication checkpoint changes only qualification documents from the tested
commit; all runtime/package/build/test/example/workflow inputs are identical.
The `v0.2.0` source tag and release are the M3 handoff; M4 implementation follows
on a separate development branch.

The last pre-release **`37f9c21b2b118c4bdc22bdd3147042179ba74e4c`**
[run35743467738](https://github.com/rioriost/pg_agmemory/actions/runs/35743467738)
passed all four jobs. Each core architecture passed2,378 cases/113 optional skips,
all packaged smokes and ordinary v7 restore; patched AGE passed84 profile cases,
all59 original checks,207 enabled cases, real HTTP and canonical-only restore.
Core pytest took1571.02/1330.41 seconds on amd64/arm64; AGE cases282.33/385.81.
`992fe8a` also passed all four jobs in run35738577570 before the readiness-only
fix. Preserve the earlier unconfirmed setup failure as history, not a release gate
silently treated as passed.

M3's declared scope is canonical-authority graph traversal, exact bounded
ordering/time/authorization, verified artifacts/generation CAS, fail-closed
freshness, and isolated canonical-only restore followed by explicit rebuild.
SQL remains default. Full AGE catalog restoration, arbitrary newer content,
automatic reactivation, larger/co-resident/concurrent/cold graph costs and
production HA/PITR are excluded. M3 is complete within that declared scope.

## Historical v0.1.3 graph resource recipe and freshness decision

The six-case graph-only v1 recipe was frozen at `d1b894d` in `examples/graph-resource-profile.json`
(digest `c89ed11ad1fc31038b2e168a56309c27d01521a627f2fed2e7b4ac6852fb2212`).
It uses visible/hidden scopes, 12/64-node chain/fanout/multiseed shapes, three
warmup plus 30 measured pairs each, and the unchanged strict 1,500 ms p95 target.
The non-exact development run collected 396 samples and 36 semantic probes
without errors; all six strata met the timing target, but its qualification
flags remain false. Later stricter classification is preserved separately from
the original raw report.

The benchmark has 35 offline contracts for profile/sample/order/oracle integrity,
private errors, runtime identity, interruption handling and owned cleanup.
It uses the actual native adapter and publisher, never the fixed-hop experiment.
No production query, permission, migration or source-mutation behavior is changed.
The decision for this bounded profile is to **retain the full canonical and
physical-projection completeness proof**, not introduce a new constant-time
counter merely for speed. Larger graphs and co-resident full-S/concurrent/cold
loads remain separate work; the current artifact maximum is not a latency claim.
The exact archived **`d1b894d`** run passes all six strata: AGE p95 ranges
111.89–1,292.52 ms, SQL 22.30–73.80 ms, with 396 samples/36 probes and zero errors.
The raw-evidence recheck also passes. This explicitly scoped
`resource_qualified:true` is not `m3_qualified:true`, nor an AGE speedup over SQL.
Apple Container's configured 6/2 workload CPUs also have one overhead CPU per VM;
the shared host is not an exclusive 8-core capacity claim. Full measurements and
scope are in [EVALUATION](EVALUATION.md#exact-warm-graph-run-d1b894d).

Run35734293423 for the recipe commit passed both core jobs and AGE arm64, but
AGE amd64 exited2 during setup before tests; its redirected setup log was not
available from the completed runner, so the precise cause is unconfirmed.
The launcher now exposes safe build/extension setup diagnostics and waits for
TCP readiness, not the temporary Unix-socket-only initialization server.
Eight offline launcher cases cover both engines and all setup failure stages.
This is an explicit incomplete distribution result, not a graph measurement
failure or a passed four-job run.

## v0.1.3 / schema 20: isolated recovery of an enabled AGE baseline

`recovery-apply apply --isolated --disable-age-projection` adds an explicit
recovery-and-disable transition. The signed bundle, exact restored CAS, canonical
payloads, generation history and entire projection receipt are verified without
relaxing the existing checks. After exact latest-state application is verified,
the same transaction disables that projection with revision+1. Postchecks allow
only the projection fingerprint to differ; failures roll back both recovery and
disabling. Without the option, an enabled registry is still refused before writes.

The final state is intentionally **not identical** to the enabled reference:
the CLI reports `processing_state_matches:false`, successful operational recovery,
the explicit projection disabling and the sole differing table. It neither
rewrites generic generation receipts nor imports newer graph history, executes
AGE, or starts a service. Separate current-artifact construction and verified
publication are still required before operator-approved startup.
There is no schema migration, permission change, new model call or automatic retry.
The 36 focused recovery contracts cover matching/no-op paths, authentication/CAS,
isolation, monotonic reservations, revision bounds, failure rollback and CLI output.

The first real full-AGE `pg_dump`/restore attempt reached successful quarantine
and disabled HTTP refusal, but subsequent graph creation failed with
`ag_graph_graphid_index` duplication. The failed run is preserved; no catalog OID
or allocation state was repaired to manufacture a pass. The supported approach
therefore treats AGE physical graphs/catalogs as **rebuildable projections**:
an explicitly canonical-only backup retains canonical memory, generation history,
the enabled registry and recovery keys; AGE is freshly installed from the trusted
patched image after restore. Full-AGE catalog round-trip is not qualified.

Missing-graph publication requires `--rebuild-missing`, an existing disabled
receipt, complete absence in both physical schema and AGE graph catalog, current
generation/registry CAS and a verified current artifact. Partial metadata or an
existing graph is refused for this mode, with no arbitrary drop or repair.
Construction and the serving switch remain atomic. This is not automatic
reconciliation of arbitrary old/new graph generations.

The real arm64 development drill completed with the packaged non-root runtime:
source destroyed before restore, an audited canonical-only archive, exact
35-table canonical fingerprints and recovery-key preservation, registry revisions
1→2→3, disabled HTTP409, three purged targets invisible, reader revocation and
current/historical native-SQL equality after explicit rebuild. Four nodes/three
relation revisions become three/two; eight canonical anchor IDs remain stable.
No model calls or workers start. Thirty offline drill contracts also pass.
The 22 new missing-projection contracts and 22 existing publisher cases pass
together on a fresh patched cluster. Catalog namespace comparison uses OIDs
explicitly rather than comparing PostgreSQL's rendered `regnamespace` name to
an integer. The ordinary SQL-only v7 restore also retains its 35-table payload,
20-denial and 11-reservation result without enabling AGE.
The frozen **`5a2172856e9625f57af8e5935c287775aa8f41a4`**
[run35725941968](https://github.com/rioriost/pg_agmemory/actions/runs/35725941968)
then passed all four native jobs. Core amd64/arm64 each passed **2,335 cases /
113 optional skips**, all production smokes and exact ordinary v7 restore
(pytest 1446.42/1379.22 seconds). Patched AGE each passed **84 profile contracts**,
the original 59 checks, **207 enabled-profile cases**, real HTTP publication and
the new exact canonical-only recovery drill. Both recovery reports bind the
frozen commit, preserve 35 canonical/24 operational fingerprints apart from the
explicit registry transition, retain revisions 1→2→3 and keep full-catalog
recovery/M3 qualification false. AGE cases took 270.58/364.16 seconds.
Graph resource qualification and the freshness cost decision remain separate.

## v0.1.2 / schema 20: optional patched native AGE enabled

Exact **`07417131782270ecff460018c0f2139a43d24441`**
[native run 35709955807](https://github.com/rioriost/pg_agmemory/actions/runs/35709955807)
passed all four jobs: core amd64/arm64 **2,269 passes/91 optional skips each**,
all packaged smokes and exact schema20 v7 restore; patched AGE on both architectures
**84 profile contracts**, all original **19 native/direct + 40 fixed checks**,
**185 enabled-profile cases** and real HTTP equality/disable/stale/rebuild/SQL-switch.
Core pytest took 1540.10/1360.17 seconds, AGE cases 326.64/361.90 seconds.
Both core restores preserve 35 payload/24 operational fingerprints, 20 denials,
11 reservations and the unchanged stale/non-serving generic generation receipt.
The earlier `6f2a8fa` run35709038844 failed only the final description's E501 lint;
`0741713` changes its wrapping, not AGE behavior. This frozen evidence predates
the v0.1.3 recovery extension and does not qualify that later increment.

The local AGE fix **`72707aab7ce982bf13cad3d102bd869dab07d64b`** is incorporated as
a reproducible patch over upstream `fa109ef1ddb1c7a945a1c340195d650000e49713`.
Archive, patch, resulting Git tree, build stamp and preload diagnostic hashes
are pinned in `patches/age/source.json`. The image retains Apache LICENSE/NOTICE.
The historical `Dockerfile.age`, failing native probe and rejected fixed-hop
candidate remain unchanged. This is not an assertion about all upstream builds.

`PGAG_GRAPH_BACKEND=age` selects the real VLE adapter after startup validates the
patched image identity. Native one-/two-hop traversal, not host-side fixed-hop
BFS, supplies candidates. Forced-RLS label policies apply canonical scope,
evidence, predicate and temporal constraints before traversal; canonical joins,
cycle exclusion and deterministic ordering apply before the path limit.
Returned labels and assertion details still come from canonical SQL.
Responses use `backend:"age"` and a generation UUID `projection_watermark`.

Admin `age-projection publish` rechecks the current recorded head, exact artifact
and both revisions, creates/analyzes an owned physical graph, and atomically
switches a tenant-scoped registry. Replacement removes only the previous
registered graph in the same transaction; failures roll back graph and registry.
Disable retains the receipt. Runtime has no graph/registry write or ownership
privileges. The optional image exposes a narrowly scoped, fixed-setting boolean
preload diagnostic; it does not grant `pg_read_all_settings` or bypass data RLS.
Ordinary migrations do not install AGE or that diagnostic definer.

The patched image passed all 19 original native/direct and 40 fixed-template
checks. The integrated development runner passes **185 cases** plus a non-root
packaged **real HTTP** smoke: native two-hop/SQL equality, disable rejection,
same-head rebuild, stale-input rejection and explicit SQL restart selection.
An initial combined-run fixture failure assumed AGE was absent before migration;
the corrected invariant verifies migration preserves an already installed AGE
unchanged and still installs none in the ordinary profile. Failed evidence remains.

Freshness currently checks captured ACL/deletion epochs and bounded
request-visible canonical topology completeness on every request. It is **not
constant-time**, a full-S graph cost qualification, or automatic regeneration.
The source probe's safety result does not imply inexpensive production reads.
Missing/disabled projections return 409, eligible additions/revisions or epoch
changes return stale 409, and an unqualified runtime build returns 503; none
silently invokes SQL. A configured SQL deployment remains independent of AGE.

Current recovery snapshots contain 24 operational tables. Enabled projection
registries block `recovery-apply` before writes; disabled registry metadata is
strictly compared, never imported. Use the disabled-before-backup procedure in
[operations](operations/README.md#patched-age-enabled-profile). Old authenticated
schema-19 generation receipts remain immutable/readable and stale; abandon old
pending builds or create a new schema-20 child, never relabel old artifacts.
M3 resource/restore scope and full frozen-build distribution are separate gates.

## v0.1.1 / schema 19: rebuildable canonical graph artifacts

`graph-artifact export/check` adds bounded, administrator-only canonical graph
data construction. It binds ordered node IDs and all retained temporal edge
revisions to an existing pending generation or recorded head, its input snapshot,
profile declaration and private recovery-key lineage. It exports neither node
labels, source text nor evidence quotes. Files are private, exclusive-create and
never overwrite an existing destination. An unchanged recorded head rebuilds
byte-for-byte, including the signature and digest.

Verification checks current ledger revision, current canonical input, keyed
signature, exact canonical topology and (for a recorded head) the receipt's file
digest. A correctly signed but different topology still fails. A stale generation
cannot be rebuilt from current data under its old identity. Export/check do not
change any database rows, and the tenant barrier spans file creation/output.
Filesystem writes and subsequent receipt recording are separate operations,
not a distributed transaction or an automatic retry.

Here **`artifact_verified=true` means only that the artifact matches the current
canonical build input**. The generic generation receipt still always reports
`artifact_verified=false`; neither command grants serving authority. Artifacts
include retained topology across tenant scopes/history and are not filtered for
a runtime principal. `permission_filter_required=true` and
`serving_enabled=false` remain mandatory.

Independent artifact contracts and the non-root packaged smoke cover exact
history/rebuild, private files, tampering, failed writes, source mutation and
read-only metadata. No migration, inference, AGE load or Native graph selector
is added. Native serving, constant-time freshness and actual AGE projection
activation remain unimplemented. See [operations](operations/README.md#canonical-graph-artifacts).

## v0.1.1 / schema 19: graph-generation metadata

M3's backend-neutral coordinator records generations without enabling a graph
backend. New admin-only, forced-RLS tables hold an immutable generation history
and a CAS-fenced head/building ledger. `graph-generation` accepts closed JSON
requests for `get`, `begin`, `record` and `abandon`; runtime roles have no table
access. One build per tenant, server-assigned completion time, terminal-history
immutability and deferred pointer consistency are database constraints.

Input equality is a keyed digest of seven graph-relevant canonical table
snapshots plus current access/deletion epochs, captured under the tenant barrier
and repeatable-read. It is not a wall-clock cursor or a constant-time mutation
counter. Unrelated observations/assertions do not invalidate it; graph changes
and changed authority/deletion epochs do. `record` rechecks both source and ledger
CAS. `abandon` can close a stale/oversized build without rescanning its source.
`source_matches` is not permission to use an artifact or extend an ACL expiry:
**`artifact_verified=false` and `serving_enabled=false` are unconditional.**

Forty-four focused Linux lifecycle/recovery cases pass. Schema rollback and existing
recovery contracts are also checked; the development v6 actual dump/restore
preserves a nonempty recorded-generation receipt and ledger while marking its
input stale after newer deletions/ACL changes. Core 35 payload fingerprints,
20 hidden targets and 11 call reservations remain intact. The operational
snapshot now includes 23 tables; generation history is compared as immutable
content and is never imported as replacement rows. A missing/different history
therefore refuses application rather than recreating or activating it.

This increment is **metadata coordination**, not graph-data construction,
constant-time read-side freshness, actual projection rebuild, AGE activation,
M3 completion or production DR certification. Current graph reads remain SQL;
the fixed-hop non-adoption and preserved native strategy below are unchanged.
Use [schema-19 operations](operations/README.md#graph-generation-metadata-schema-19).

The first [native run 35686566134](https://github.com/rioriost/pg_agmemory/actions/runs/35686566134)
at `50ed5e2` passed 2,096 tests/31 optional skips per architecture but **failed
overall**: the production recovery-export smoke still expected 21 operational
tables. Its assertion now requires 23, includes both generation tables and
excludes them from replacement rows. The exact corrected smoke passes on a
non-root production image with both empty and recorded-generation ledgers.
The corrected **`a017ae5c6d2c22e31447f54ddb7253f58ce15d69`**
[native run 35689800671](https://github.com/rioriost/pg_agmemory/actions/runs/35689800671)
passed on both architectures: **2,096 tests / 31 optional skips each**, all
packaged smokes and exact v6 restore. Both reports retain the unchanged recorded
generation at ledger revision 2, mark its input stale and keep it non-serving;
35 payload fingerprints, 20 denials and 11 reservations match. Pytest took
1413.20 s amd64 / 1353.32 s arm64. This qualifies the metadata checkpoint only;
the subsequent artifact exporter has separate evidence and is not covered by
that earlier run. The 31 skips include eight optional model cases and 23 AGE cases.

## M3 development: graph qualification foundation

The `feat/m3-graph` branch adds an isolated AGE build/probe and an independent
graph oracle. **The released v0.1.0 runtime, schema 18 and SQL default are
unchanged; AGE is not enabled in the API.** Packaged Linux checks without source
mounts pass 82 graph/profile tests; the independent oracle exercises 88 expansions
across 17 cases.

Exact **`976d558d89a5aded1088aa99dbd84acddf2071df`** subsequently passed
[native core CI 35572874356](https://github.com/rioriost/pg_agmemory/actions/runs/35572874356)
on amd64 and arm64: **1,994 tests / 8 optional live skips each**, all packaged
smokes and the unchanged v5 isolated-recovery checks. Pytest took
1093.04/1317.14 s. This CI includes the offline AGE harness contracts but **does
not run the AGE extension**. It establishes core compatibility, not full M3 or
dual-architecture AGE qualification; the negative AGE result below remains.

Pinned upstream **`PG18/v1.8.0-rc0`** builds on PostgreSQL 18.6/pgvector 0.8.6.
The release title/catalog says 1.8.0, but the actual tag is an rc0. The real
non-owner, non-superuser, NOBYPASSRLS probe forces RLS on base and child labels:
**six native variable-length traversal checks fail**, including reachability
through hidden intermediates/edges and after deny-all edge policies.
The helper deliberately exits 1 and retains `qualified=false`.

A separate fixed-label one-hop/prepared-query and host-expansion candidate
passes 40 checks, including changed synthetic tenant/actor context and deny-all
policies. This is a viable next implementation path, **not a qualified adapter**:
canonical ACL/time joins, generation/watermark, revocation races, rebuild and
restore remain to be integrated. Native variable-length Cypher stays excluded.
See [the reproducible probe and limits](operations/README.md#m3-age-qualification-profile)
and [the M3 boundary](PG_AGMEMORY_IMPLEMENTATION_PLAN.md#123-m3-implementation-boundary).

The next increment factors canonical neighbor authorization, time filtering,
ordering and budget enforcement into one shared internal SQL boundary. Backend
parameters cannot replace the tenant, scopes, effective times or path budget.
The default SQL traversal is unchanged; 60 focused Linux checks pass, including
the existing prepared/nested-loop plan checks. Their query recorder now handles
composed SQL without removing any plan assertions.

The native-VLE implementation and negative probe remain preserved, not replaced
by the workaround. A future qualified upstream fix must pass the same canonical
contract before that strategy can be selected. Workaround adoption also requires
a paired cost measurement; no performance acceptance is inferred from correctness.
The first candidate run encountered an agtype integer-to-JSON conversion error
and never reached cost measurement. The preserved correction uses the direct
`agtype::bigint` conversion for revisions; a subsequent run passes 20 live
conformance cases, including the independent oracle, and all three paired-cost
experiments without weakening permissions or running native VLE.

**The initial workaround is not adopted: its measured read cost is too high.**
For chain/fanout/multiseed, 30 measured pairs after three warmup pairs show
candidate request medians of 543.16/3100.83/428.28 ms versus SQL
25.58/49.65/47.04 ms (21.23x/62.45x/9.11x). Candidate p95 is
602.54/3196.43/447.09 ms; all 198 warmup/measured requests succeeded.
These are small sequential, instrumented fixtures on separate 2-vCPU/2-GiB
database and client guests, not production capacity. Projection build time and
storage are separate, and the projections include earlier fixture rows.
An independent statistics-only diagnostic reduced the overhead but did not make
it low-cost: after `ANALYZE`, chain/fanout candidate request medians were
101.74/422.10 ms versus SQL 22.74/62.02 ms (4.47x/6.81x).
It used three requests per backend/phase, not a new p95 measurement. JIT time
fell from about 202–210 ms to zero without changing runtime settings or
permissions. This does not overwrite the original paired run or justify adoption.
The SQL default remains selected; both candidate and preserved native strategy
remain unavailable as production backends. See the
[candidate runner](operations/README.md#fixed-hop-candidate-experiment).

## M2 core MVP / v0.1.0 / API v1 / schema 18

**M2 is complete for the declared bounded core-MVP profile.**
[Native run 35565944016](https://github.com/rioriost/pg_agmemory/actions/runs/35565944016)
at **`af878fc51fa50cefecca69de2df22edfef2a321b`** passed on amd64 and arm64:
**1,945 tests / 8 optional live skips each**, all packaged API/worker/SDK/MCP/hook
smokes and the v5 actual backup/application drill. Pytest took 1397.44/1263.10 s.
Both recovery reports bind exact inputs, preserve all 35 canonical and 21
operational fingerprints, five original receipts and 11 reservations, and deny
all 20 tombstoned targets. Live-model skips are explicit, not model passes.

Release **`v0.1.0`** uses those qualified build inputs: service 0.1.0,
API v1, schema 18, capability stage `m2-core-mvp`. The publication checkpoint
adds only bilingual qualification documentation to the tested commit; runtime,
root README/package metadata, lock, Dockerfile, scripts, tests and examples are
unchanged. The release's qualification is not inferred from the preceding
0.0.35 run. No new migration or model call was needed.
One existing policy/lease recovery test returned `idle` after a fixed 5.1-second
sleep in the local candidate run. It now reuses the chaos tests' bounded
database-clock expiry check before asserting recovery; production lease behavior
and the stale-context/second-attempt assertions are unchanged.

| Qualified M2 boundary | Limits that remain |
|---|---|
| Atomic scoped memory, revisions/provenance, SQL/vector/hybrid recall, typed checkpoint/compaction | No guaranteed semantic truth, approvals or model-selected answers; no ANN or AGE/SQL-PGQ adapter |
| Durable generation/embedding jobs, adoption, budgets and epoch/lease fencing | Administrator-pinned local profiles only; no unknown-call retry or billing exactly-once guarantee |
| Isolated logical restore and latest ACL/policy/accounting application | Matching canonical content/anchors/jobs and key lineage; disjoint unchanged mixed prefix, purge suffix at most 100 expanded targets per receipt; 10k rows/table and 16 MiB bundle |
| S resources, deletion/limit probes and reference example | Original SHAs and allocations remain binding; guest-cold is not physical-host cold, controlled responses are not live inference cost, the reference is not semantic qualification |

Use the [current deployment contract](operations/README.md#m2-core-mvp-deployment).
There is no automatic activation after restore, arbitrary-content recovery,
production HA/PITR/RPO/RTO or backup-retention certification. M3 graph integration,
M4 harness pilot and M5 production qualification remain separate. Historical
entries below retain the status at their own checkpoints; legacy
`m2_qualified=false`/human-quality fields are not rewritten.

## v0.0.35 / schema 18: derived-memory recovery coverage

The v5 disposable backup drill includes inferred and explicitly adopted assertions,
quarantined candidates, source/assertion embeddings, typed working snapshots with
uncompacted tails, SQL graph derivatives and unknown tool effects. Each has both
retained and purged fixtures. An unchanged mixed suppress/purge baseline is
preserved without replaying it; new suppress, overlapping targets, changed
prefixes and purge suffixes above 100 expanded targets remain refused.

The exact **`593087f`** Linux drill restores the old dump after destroying the source,
replays three purges, applies the authenticated latest operational bundle and
matches all 35 canonical and 21 operational fingerprints. All 20 tombstoned
anchors remain unreadable; 11 synthetic reservations survive without refund.
Retained vectors/provenance/graph remain readable. Typed checkpoint restoration
preserves pending approvals and the unknown-effect fence; stale working snapshots
explicitly refuse read/resume after epoch changes instead of silently reviving.
No external model calls, migration or automatic service activation are added.

The initial expanded run exposed a harness-only ordering ambiguity for one
principal in several scopes; snapshots now order memberships by the full key.
The exact local report records `exact_commit_inputs=true`; native qualification
is complete at `6d967c4` in
[run 35563611773](https://github.com/rioriost/pg_agmemory/actions/runs/35563611773),
with the final v0.1.0 run recorded above.
Missing/newer canonical content,
arbitrary histories, HA/PITR and provider-side reconciliation remain outside
this bounded application. The [single reference benchmark](EVALUATION.md#one-reference-memory-benchmark)
is now packaged with its original source/JUnit binding, pinned profile, observed
state/coverage/tail/timing/size and preserved failures; it is not a new model run.
This implementation's engineering gates are complete; v0.1.0 publication is tracked above.

## v0.0.34 / schema 18: bounding large-tombstone read checks

Exact schema-17 probes at `cacb47b` passed small/large deletion and limit checks,
but some post-10k-purge warm samples exceeded 500 ms. These are not a cold/steady
latency pass. Scalar tombstone membership checks remained costly even after
fixing projection joins. Migration 018 applies the original membership/expiry
predicate as a statement-local set under forced RLS. An isolated 10k-tombstone
diagnostic changed from 429 ms to 58 ms.

Exact **`51293b4`** now completes the full 30-minute S window and deletion/limit
probes: observe/recall transaction p95 **37.08/106.24 ms**, mixed small-purge p95
**125.22 ms**, 10k-object purge **4.43 s**. All 45,000 steady requests and 9,300
warmup+steady jobs succeeded. Twelve fresh-guest first queries were
**122.10–242.90 ms**, not a physical-cold p95. See [EVALUATION](EVALUATION.md)
for limits, failure history, footprint and the projection-plan tradeoff.

Native amd64/arm64 each passed **1,940 tests / 8 optional skips**; arm64 completed
all packaged/recovery smokes. Amd64 hit the old 25-minute workflow deadline
after pytest. CI now allows 40 minutes without changing product timing gates.
The replacement [run 35560939791](https://github.com/rioriost/pg_agmemory/actions/runs/35560939791)
at runtime-identical **`7a2fd88`** completed on both architectures, including all
production/recovery smokes and **1,940 tests / 8 optional skips** each.
**M2 remains incomplete:** broader declared restore/deployment coverage and the
single reference memory benchmark/release handoff still remain.

## v0.0.33 / schema 17: preserving bounded recall after deletion

The exact `6beb38c` resource probe failed during mixed-load small purges:
post-deletion recall saturated database connections and the tenant barrier timed
out. Preserve that failure; the earlier steady-only S pass did not exercise this.
Runtime-role plans isolated an underestimated embedding join that compared about
100 million pairs. JIT added overhead but disabling it did not fix the join.

Migration 017 expresses the same tombstone exclusion as a tenant-local set,
with NOT NULL IDs and existing forced RLS. On an isolated full-S diagnostic copy,
the post-deletion vector query changed from roughly 2.39 seconds to 55 ms;
hybrid changed from 2.44 seconds to 53 ms. Those isolated SQL diagnostics are
not Native latency qualification. Exact resource/distribution qualification is
still pending; no thresholds, planner settings or model-quality gates changed.

Recall also uses unique-key scalar embedding lookups rather than optional vector
joins. After a large tombstone set, low-selectivity joins could still rescan the
whole model projection set per candidate. Each lookup retains tenant, object,
revision and model-space keys plus forced RLS; absent projections remain NULL
and continue to mark incomplete coverage. Lexical fallbacks and rank fusion are
unchanged. This does not weaken tombstone permissions or enable ANN.

## v0.0.32 / schema 16: reducing recall scans without relaxing authorization

The frozen `1d898c9` full-data S preflight failed the unchanged latency gates:
observe transaction p95 **1146.85 ms**, recall **1453.98 ms**. All responses and
225 controlled jobs succeeded; this was a latency failure, not qualification.
The profile used 100k episodes, 10k assertions and 110k vectors, but only 30
steady seconds. Its immutable archive/artifacts are retained.

Runtime-role plans showed two candidate scans for vector/hybrid and per-row
visibility SQL. Ranking/coverage now share one materialization; migration 016
uses equivalent statement-local membership/visible-ID sets while retaining
forced RLS, expiry/tombstones and all write/barrier behavior. Disposable-clone
diagnostics reduced vector SQL from roughly 394+368 ms to 52 ms; those plans are
not a replacement for mixed-load qualification. Oracle/prepared-context,
cross-tenant/scope, retained-payload tombstone and Native denial regressions
cover the intended boundary. Full-duration S and remaining resource probes
were initially open; the completed steady run below does not finish all M2 gates.

Exact code **`9c7db01289a07ea6ce7f7ded37c485d4110e95d1`** passed
[native CI 35513420525](https://github.com/rioriost/pg_agmemory/actions/runs/35513420525)
on both architectures: **1,916 tests / 8 optional skips**, including the 10,000
actual Native denial checks and all production/recovery smokes. The new
original-predicate oracle matrix, prepared context/expiry/revoke checks and
single-materialization coverage cases passed; recovery still preserves exact
operational state and unknown/quota fences.

The unchanged `S-fixed-v1` recipe then completed **1,800 steady seconds** on the
same frozen commit: **36,000 recalls / 9,000 observes**, no invalid responses,
missing timings or scheduled drops, and all **9,300 warmup+steady jobs succeeded**.
Observe/recall transaction p95 was **40.83 / 76.98 ms**; every recall mode/selectivity
stratum met 500 ms (worst p95 **102.40 ms**). These are real HTTP/database/worker
measurements with fixed vectors and a 10 ms controlled provider, not live-model
quality or exclusive-host production capacity. Small-forget/large-purge,
concurrent limit probes and physical cold-cache coverage remain unmeasured;
`resource_qualified=false` and `m2_qualified=false` remain explicit.

## v0.0.31 / schema 15: retained instrumentation and initial resource diagnostics

Opt-in Native request timing preserves the ordinary response and authorization
path. It separates connection/barrier, handler, commit and total transaction
time without body/query/actor labels; missing phases stay missing. The frozen
S recipe and Linux Apple Container harness use actual HTTP, two real job handlers
and a controlled loopback provider, with private per-request and guest-resource
artifacts. See [resource operations](operations/README.md#resource-measurements).

Development diagnostics used 2,000 episodes/200 assertions, not the complete S
dataset. The first attempt correctly retained 175 unknown failed reservations
because the synthetic provider fixture omitted required wire fields. The fixture,
not the production parser, was corrected. The next dirty-worktree development
run had 600 steady recalls/150 observes and 175 successful warmup+steady jobs;
observe/recall transaction p95 was 27.77/112.20 ms. These are preparatory
diagnostics, not exact-commit S or M2 qualification. Thirty-minute S measurements,
deletion/limit probes and cold-cache coverage remain open.

## v0.0.30 / schema 15: retained operational-state application evidence

`recovery-apply export/apply` now applies exact latest operational rows to an
isolated target whose retained canonical content already matches. Original
receipts, idempotency, ACL/policy, job states and call reservations survive
without regenerating their identities. Bundles are authenticated by a separate
admin-only per-tenant key introduced by migration 015, not the runtime-readable
dedup secret. CAS, immutable-content checks, monotonic histories/reservations,
active structural constraints and post-write fingerprints must all pass in one
transaction; failures roll back. Runtime roles cannot activate historical writes.

The v4 actual backup drill now matches both 35 canonical fingerprints and all
21 operational fingerprints, including three synthetic unknown/failed/succeeded
reservations. Unknown retries stay blocked, semantic job IDs stay stable and
consumed quota is not refunded. It uses the packaged apply CLI; no external model
requests or long-running API/worker processes are started.

This is bounded application, not a universal restore/activation tool. Canonical
content/deletion effects must already match; new/missing content, different job
sets, legacy unmapped deletions and oversized bundles fail closed. General audit
history/sequences are not replaced. Broader derivative/recovery profiles,
resource qualification and the reference memory benchmark remain open; no M2
completion or HA/PITR claim is made. Follow [schema-15 operations](operations/README.md#schema-15-operational-state-application),
including a fresh post-upgrade backup with the recovery key.

Exact implementation **`21187702c43aff55c83341aa45f0de4d285fd64e`** passed the
local packaged run and [native CI 35498710001](https://github.com/rioriost/pg_agmemory/actions/runs/35498710001):
**1,861 tests / 8 optional skips** in each run, all production smokes and the
actual v4 recovery/application CLI drill. All three reports identify exact
commit inputs, three original receipts and three reservations retained,
all 21 operational fingerprints matched, and unknown retry/quota/semantic-ID
fences intact. These qualify the documented bounded application, not M2 as a whole.

## v0.0.29 / schema 14: retained comparison evidence

`processing-recovery export/check` adds an admin-only read-only comparison of
21 fixed operational tables, epochs and dedup lineage. It includes policies,
durable model reservations/outcomes, semantic job identities and job state.
References contain tenant-keyed fingerprints, never raw payloads or secrets;
incomplete, oversized or wrong-lineage inputs fail. A mismatch returns nonzero,
and even a match returns `restore_authorized=false`.

Real PostgreSQL cases cover unknown, known-failed and successful call reservations
and policy-budget changes. The v3 backup drill matches the restored baseline but
deliberately detects the regenerated operational history after bounded replay,
despite 35 canonical table matches. These are comparison guarantees, **not**
an implemented latest-state import or automatic worker-start fence. Preserving
or explicitly reconciling operational identities, current policies and model
accounting remains M2-B work. No new schema migration or model-quality gate is added.
See [the procedure and limits](operations/README.md#processing-state-recovery-check).

Exact code **`d3b1b222784c544410bb9b4eda956e7b602bda62`** passed 109 focused
local cases and the actual v3 backup drill.
[Native CI 35483209713](https://github.com/rioriost/pg_agmemory/actions/runs/35483209713)
passed **1,847 tests / 8 optional skips** on both architectures, all production
smokes and the v3 drill. Each exact-input report confirms baseline equality and
the intended latest-operational-state mismatch, with no restart authorization.
This qualifies the read-only comparison, not the missing state application.

## v0.0.28 / schema 14: retained deletion recovery evidence

The recorded components require **service 0.0.28 / API v1 / schema 14**. Migration
014 adds exact receipt-to-target manifests, deferred completeness and tombstone
checks, same-transaction insertion and bounded unique ordinals. Old receipts
retain `target_manifest_version=0`; no per-receipt history is guessed.
`pg-agmemory deletion-history export` provides an admin-only, bounded, private,
content-free snapshot; incomplete/legacy histories fail explicitly. The export
does **not** include ACL/policy/call accounting and never authorizes restore.
Public `forget` still supports preview/purge, not suppress.

Exact implementation `99e71bd74445c2eab6fb82fe62c25b1678cdc69b` passed local
Apple Container distribution checks and
[native amd64/arm64 CI 35445005807](https://github.com/rioriost/pg_agmemory/actions/runs/35445005807):
**1,784 passed / 8 optional live skips** in each full run, including the new
packaged administrative export and all prior production smokes. The actual
single-purge backup/restore reconciled 35 canonical table fingerprints and four
manifest targets with zero model calls. This qualifies the bounded dependency,
not general logical restore or M2 completion; later documentation commits do not
inherit the tested SHA.

M2-A's core contract inventory is now in [EVALUATION](EVALUATION.md). The next
part of M2-B is latest policy/model reservation/quota reconciliation.
The [bounded multi-receipt drill](operations/README.md#bounded-multi-receipt-recovery-drill-2026-09-20)
now restores a nonempty deletion baseline, two later purges and two ordered ACL
changes, checking six tombstones and a live control. It still rejects model/policy
state, grants, changed identities and arbitrary histories; it is not a production
restore tool. Resource qualification and one reference memory benchmark
remain M2-C/D. No model calls, human labels, new API resources or relaxed
provider permissions are introduced here. See [schema-14 operations](operations/README.md#schema-14-deletion-manifests).

The subsequent multi-receipt implementation **`84871e085131aa673543ac9455386874d9eadf77`**
passed the exact local restore drill and
[native CI 35479478777](https://github.com/rioriost/pg_agmemory/actions/runs/35479478777).
Both architectures passed **1,802 tests / 8 optional skips** and all packaged
smokes. All three recovery reports identify exact commit inputs, one baseline
receipt, two replayed receipts, two ACL changes, six tombstones and 35 matching
canonical table fingerprints. Model calls remain zero and `m2_qualified=false`.

## v0.0.27 / schema 13: retained implementation evidence

The version labels and measured runs in this section are historical. The
unchanged processing contracts remain applicable, but do not qualify schema 14.

The recorded runtime was **service 0.0.27 / API v1 / schema 13**,
stage **`m2-background-processing`**, with exact migration history 1–13,
PostgreSQL 18.6 and `vector` 0.8.6 in `public`.
All adapters must match; Native/SDK has 38 resources and MCP retains four tools.

New current contracts are specified in [ADR 0028](adr/0028-background-processing.md):
default-deny synthesis policy, local profile/prompt pinning, durable
extract/embed/compact jobs, unknown-call fencing, quarantine and explicit
caller adoption (`human_review_verified=false`), exact typed working snapshots,
and explicit read-only after-compaction restoration. `observe.auto_extract` and
`auto_embed` default false; an ordinary observation still creates no model job.
The snapshot hook remains disabled unless its separate operator byte budget is set.

Implementation `1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6` passed
[native amd64/arm64 CI 35331248426](https://github.com/rioriost/pg_agmemory/actions/runs/35331248426):
1,754 tests / 8 optional live skips on each architecture, including all packaged
production smokes, synthetic M2 lifecycle and the bounded backup-recovery drill.
The 223 new offline collector/review/pilot cases are included, not additional.
The separate real three-call local-model lifecycle used
`e4f5d76ad2a4919349054165ce531b92fa650818`.
Do not attribute those results to a later publication commit.
[EVALUATION](EVALUATION.md) records the separately pinned 600-question held-out
retrieval and 10,000-case actual authorization experiments, failed attempts, and
remaining gates. None establishes human semantic precision or M2 completion.
The public oracle diagnostic at `0552151` retained 72 invalid outputs among 144
answer attempts. Corrected wire schema `9c95816` eliminated those contract errors
in a fresh 412-call run, but 136/144 abstentions and 22/144 mechanical matches
still do not establish answer quality. Prior evidence remains unchanged.

[Human review preparation](HUMAN_REVIEW.md) was recorded at exact code
`1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6`: six revision-pinned Wikipedia excerpts,
24 local calls, five summaries, eleven QA records and eight retained generation
failures. There are no accepted extraction claims and no human labels.
Under the **2026-09-19 scope revision**, those forms are optional historical
diagnostics; there is no human-label dependency for core implementation.
This provider-only pilot does not measure the memory pipeline and is not its
acceptance test. Its pending forms and failures remain unchanged.

The [revised plan, sections 1.3 and 17–18](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)
defines pg_agmemory as a memory system, not a judgment system. Model semantic
accuracy, answer success and 20 real agent tasks are no longer M2+ release gates;
multi-model comparison is not a separate project workstream. Keep one pinned
reference benchmark, with no model-quality pass score.

The remaining M2 engineering sequence is **M2-A contract/evidence inventory →
M2-B safe logical restore and accounting → M2-C bounded resource qualification →
M2-D reference benchmark and release packaging**. M2-B is the next implementation
priority: add versioned per-target deletion receipt/mode history, cover mixed and
multiple histories and derived objects, and reconcile current ACL/policy/model
reservations/quotas before API or worker restart. Missing authoritative records
must leave the restore isolated. Do not rewrite published migration 13 or infer
missing historical linkage. No automatic DR or full-erasure qualification is claimed.
The new isolated single-purge/single-revocation drill exercises actual backup
restoration and canonical reconciliation without starting API/model workers.
It rejects mixed/multiple histories and model-accounting state; it is not the
missing general recovery tool. See the [bounded procedure](operations/README.md).

M3 qualifies one graph backend against canonical SQL and deletion/ACL rules;
M4 qualifies agent/harness and optional external integrations; M5 qualifies
production load, HA/PITR, retention and version/model-space migrations. This
plan revision changes no runtime, schema, provider permission or quality flag
and does not itself complete M2.

## Retained v26 contract and historical evidence

The version-specific text below records the schema-11 contract and its earlier
history. Its “current” version labels, 31-resource counts, no-model-job statements
and next-work notes are **historical**, not overrides of the schema-13 contract
above. Unchanged capture-policy and default-off behavior remain available.

**Historical bounded milestone: v0.0.26/schema 11 scope capture policy.
Implementation `c07630009ff4dcc34542e3ea80064d4f10c4d8b5` passed local qualification
and exact-SHA native Docker amd64/arm64 CI.
Verified v0.0.25 and earlier results remain historical evidence, not v0.0.26 qualification.
This is not completion of M0/M1/M2/M3, an MVP, or a production-qualified release.**
The implementation plan describes future requirements, not the current API.
Performance, memory quality, disaster recovery, and full-erasure acceptance
targets remain unmeasured or unqualified. Passing local and CI checks does not
complete these gates.
Local and native results below qualify the [exact implementation SHA](#v0026--schema-11),
not a later documentation-only publication commit.

## Scope capture policy

**Active contract: service 0.0.26 / API v1 / schema 11,
stage `m2-scope-capture-policy`; implementation verified locally and in native CI.**
All current API, worker, SDK, MCP, hook, and administrative components must match.
API/worker/readiness and admin validation require exact migration history **1–11**
and **`vector` 0.8.6 in `public`**. Pinned PostgreSQL remains **18.6**.
Older qualified releases below do not qualify this tree.

`pg-agmemory scope-capture get|set` is an administrator-only CLI using
`PGAG_ADMIN_DATABASE_URL`, not a memory REST route, SDK resource, or MCP tool.
Native/SDK retains **31 memory resources**, MCP **four tools**, and the hook unchanged.
Authenticated capabilities add:

```json
{
  "capture_policy": {
    "transport": "admin-cli",
    "command": "scope-capture",
    "compare_and_swap": "tenant_access_epoch",
    "fields": ["enabled", "source_namespaces", "consent_references", "max_content_bytes"],
    "enforced_on": ["observe", "capture", "capture_batch"],
    "replay_revalidated": true,
    "unconfigured": "legacy_admission",
    "secret_pii_detection": false,
    "provider_egress_control": false
  }
}
```

### Policy document and mutation

This is a closed, full replacement: all four fields are required; unknown fields
are rejected. `enabled` is a strict boolean. `source_namespaces` and
`consent_references` are each `null` (unrestricted) or at most **64 distinct**
trimmed strings of **1–256 characters**, with no C0 controls or invalid UTF-8.
Lists are sorted canonically; whitespace-normalized duplicates are invalid.
`[]` denies all. Matching is exact and case-sensitive after the existing request
whitespace normalization; there is no wildcard, case folding, or consent lookup.
`max_content_bytes` is a strict integer **1–262144**, applying to normalized
`Observe.content` encoded as UTF-8, not raw JSON or the whole capture request.
The existing **65,536-character** content and **256 KiB** Native request-body
limits remain independent.

No row means `{enabled: true, source_namespaces: null, consent_references: null,
max_content_bytes: 262144}`. `get` accepts tenant/scope UUIDs, not mutation arguments.
`set` also requires `--expected-access-epoch` and a complete JSON `--policy-file`,
bounded to **64 KiB**. `access_epoch` is the current tenant epoch;
`policy_access_epoch` is the last policy-change epoch or `null` without a row.
`configured` reports row presence; `changed` reports this operation's real change.
The tenant CAS includes other scopes' policy and membership changes, not just this scope.

A stale epoch gives `access_epoch_conflict`, even for an equivalent policy.
Equivalent policies (including reordered lists and the default with no row) are
no-ops: no epoch increment, row creation, or audit event. Restoring the explicit
full legacy policy preserves a previously configured row. Real changes atomically
write policy, increment the tenant epoch exactly once, and append private
`memory_ops.capture_policy_event` before/after snapshots with `database_role`.
At signed bigint maximum **9223372036854775807**, a correct-epoch no-op is allowed;
a real change fails with `access_epoch_exhausted`, without partial writes.

The shared admin helper retains scope-access error/import compatibility, schema/
extension/role validation, and a tenant session advisory barrier through commit
**and CLI output delivery**. The role must bypass forced RLS and have the required
table/schema privileges; runtime credentials are never administrative credentials.
Existing `pg_agmemory.scope_access` imports of `ScopeAccessError`, `MAX_EPOCH`,
and `Epoch` remain available; `ScopeAccessError` aliases
`pg_agmemory.admin.AdminError`. This does not add a public SDK admin resource.
Schema 11 adds FORCE-RLS `memory.scope_capture_policy`, with runtime **SELECT only**
for currently readable scopes, and FORCE-RLS private audit with **no runtime access**.
Administrative failures expose only `{error: {code, outcome_unknown}}`.
After possible commit/transport ambiguity, use `get`, inspect current state/epoch
and privileged audit as needed, then reconcile; do not blindly resubmit a mutation.

### Admission ordering and limits

Current scope **read and write** authorization is checked before policy lookup or
denial. Missing/unauthorized scope gives **404 `not_found`**. Observe, capture, and
batch capture validate policy before idempotency replay and source-event dedup.
A newly disabled/restricted policy can therefore deny an old exact replay with
**403 `capture_policy_denied`**. Denial performs no source, idempotency, episode,
job, or audit writes. Invalid stored policy is **503 `capture_policy_invalid`**
(fail closed). Invalid capture-content UTF-8 fails existing Pydantic request
validation with **422 `invalid_request`**; there is no new capture-specific error.
Normal request-shape/size validation still applies. OpenAPI declares the typed
403 error response for policy denial.

The SDK preserves `capture_policy_denied` and `capture_policy_invalid` as known
Native codes. For mutations, 403 denial is `retryable: false` /
`outcome_unknown: false`; 503 remains conservatively `retryable: true` /
`outcome_unknown: true`, even for a fail-closed policy response. These flags
do not trigger automatic retry or permit changing the original key/body.

This is **prospective episode admission**, not retroactive deletion or cancellation.
Existing content remains readable under ACL; explicit remember and jobs using
existing episodes remain allowed. A real change fences already-claimed jobs and
old context/checkpoint epochs, but workers can recover under the new epoch.
Disabling capture does not promise that queued publication stops forever.
It is not consent verification, secret/PII detection, provider-egress authorization,
automatic capture/extraction/synthesis, or compaction. Consent references are only
caller-supplied labels; operator inference remains a separate path.

See [administration and migration](operations/README.md#scope-capture-administration),
[ADR 0026](adr/0026-scope-capture-policy.md), and [qualification evidence](#v0026--schema-11).

## Implemented surface

PostgreSQL is the sole application persistence store, including the durable job
queue and lexical projections. The canonical memory path needs no external model
service, memory database, queue, or file-based memory index. Optional operator
inference is separate and never publishes memory automatically.
Janome's packaged dictionary is a software dependency, not stored application memory.

| Endpoint | Current behavior |
|---|---|
| `POST /v1/observe` | Stores one episode with caller-supplied event time and consent reference. Returns revision `1`; `synthesis_job_id` is `null`, and no job is enqueued |
| `POST /v1/episodes/query` | Retained read-only episode metadata in currently readable scopes, half-open occurred-time bounds and recorded-time keyset pages; v26 implementation verified locally and in native CI |
| `POST /v1/captures` | Atomically commits/reuses one episode and one explicit structured-publication job; `201` returns the episode/job pair, not a published assertion |
| `POST /v1/captures/batch` | Atomically admits one episode and 1–16 explicit same-scope proposals; ordered job references, independent publication |
| `POST /v1/remember` | Stores an explicitly requested, structured assertion with literal evidence from readable episodes in the same scope |
| `POST /v1/jobs` | Explicitly queues structured memory publication; `202` is a job reference, not completion |
| `POST /v1/jobs/query` | Read-only current-caller-owned jobs in requested readable scopes, with exclusive keyset pagination and whole-page validation |
| `GET /v1/jobs/{job_id}` | Returns currently readable state, safe errors/timing, exact input references, and original result revision 1 |
| `POST /v1/jobs/{job_id}/retry` | Creates/deduplicates one child of an owned failed job after full intent and current-access checks |
| `POST /v1/jobs/{job_id}/cancel` | Owner-only state/attempt CAS; HTTP 200 commits terminal cancellation, not worker interruption or data removal |
| `POST /v1/assertions/{memory_id}/revisions` | Appends a full replacement revision to the same assertion using an expected head, explicit intent, reason, and revision-specific episode evidence |
| `POST /v1/assertions/history` | Read-only descending assertion/relation revision metadata under current access; exclusive ordinal pages, no values or evidence quotes |
| `POST /v1/entities` | Creates an immutable, evidence-backed, caller-reported entity identity at revision 1 |
| `POST /v1/entities/query` | Read-only exact type/label entity metadata discovery in currently readable scopes; exclusive keyset pages and whole-page evidence-count validation |
| `GET /v1/entities/{memory_id}` | Returns currently readable entity metadata and literal episode evidence |
| `POST /v1/relations` | Creates a typed relation as one canonical assertion between same-scope entity UUIDs |
| `POST /v1/relations/{memory_id}/revisions` | Replaces the target, evidence, and entire valid interval under assertion revision-CAS |
| `POST /v1/graph/expand` | Authenticated read-only, bounded SQL traversal over canonical relation revisions; no `Idempotency-Key` required |
| `POST /v1/checkpoints` | Stores typed state with a branch-head CAS and returns an immutable checkpoint reference/checksum |
| `GET /v1/checkpoints/{checkpoint_id}` | Checks current access and integrity, then returns state, references, epochs, and reconciliation hints |
| `POST /v1/checkpoints/head` | Read-only exact scope/run/branch head lookup; returns the full checked envelope, never an ancestor fallback |
| `POST /v1/checkpoints/restore` | Copies a compatible checkpoint into a new target branch; never runs code or repeats external effects |
| `POST /v1/tool-effects` | Records/deduplicates an intent within an existing checkpoint run; returns its initial revision reference |
| `POST /v1/tool-effects/{memory_id}/transitions` | Appends a CAS-checked ledger transition; does not call the tool |
| `GET /v1/tool-effects/{memory_id}` | Returns current state, immutable event history, references, HMAC identifiers, and the run-invalidated flag |
| `POST /v1/recall` | Lexical/vector/hybrid recall with exact structured pre-ranking filters and byte-budgeted packs; required refs must satisfy filters and remain lexical-only |
| `POST /v1/embedding-inputs` | HTTP 200 read-only canonical embedding input for an authorized episode/assertion revision; no idempotency key |
| `POST /v1/embeddings` | HTTP 201 explicit immutable vector upload under the canonical parent's scope; mandatory caller-owned idempotency key |
| `POST /v1/explain` | Returns the requested assertion revision and its evidence. Omitted revision still means `1`, not latest. Episodes have only revision `1`; no ranking trace API |
| `POST /v1/forget` | Accepts explicit IDs with `preview` or `purge`; no arbitrary selector or `suppress` mode |
| `GET /v1/deletions/{receipt_id}` | Returns an authorized deletion receipt and the unresolved, operator-managed backup status |
| `GET /v1/capabilities` | Reports current features, limits, and unavailable capabilities; requires authentication |

Typed request and response models define the OpenAPI schemas exposed through
`/docs` and `/openapi.json`; no generated schema file is required.
`/healthz` reports process liveness following startup validation, not continuous
PostgreSQL readiness. `/readyz` adds the bounded check below, outside `/v1`.

## Selectable inference providers

**Retained provider contract; v26 implementation verified locally and in native CI, not M2 completion.**
The following CA results are historical v25 evidence, not v26 qualification.
The bounded change is the Docker base's
`SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt` selection, with a non-root
production smoke for that environment value and a loaded root-CA store.
Nine local real-libpq TLS cases and targeted Ruff passed; a separate non-root
runtime check confirmed the environment value and 150 loaded trusted CAs.
These are separate from the v24 Azure live run's explicit DSN CA file.
The full local suite and all checks/smokes passed; exact-SHA native CI passed.
The historical v25 stage was `m2-selectable-inference`; `auto_synthesis: false` and
`model_inference.live_provider_qualified: false` are unchanged.
The optional `providers` extra pins existing **httpx==0.28.1** without new dependency
versions. This is the same core distribution, not a separate lightweight package.
`pg_agmemory.providers` exposes `ProviderSettings`, `InferenceInput`, `TextModel`,
`SummaryResult`, `GeneratedEmbedding`, `InferenceProvider`, `ProviderFailure`,
`parse_settings`, `parse_input`, and `make_provider`.
HTTPX supplied by another extra also satisfies the dependency; missing HTTPX
raises a static installation `ImportError`.
The operator CLI is `pg-agmemory infer inspect|summarize|embed --config FILE`;
one trusted profile is selected per call, without fallback or automatic retries.
It adds no Native/SDK resource or MCP/hook action: **31 memory resources/four tools** remain.
It does not persist memory, enqueue work, automatically summarize, ingest, or compact.

### Closed configuration and input

`ProviderSettings` is closed and frozen; at least one model must be configured.
Configuration parsing is bounded to **32 KiB**. Environment references are names,
not raw secrets: uppercase `[A-Z][A-Z0-9_]{0,127}`, supplied by trusted operators.

| Field | Contract |
|---|---|
| `backend` | `local_http`, `openai_compatible`, or `azure_ai` |
| `endpoint` | Required for HTTP; base path such as `/v1` allowed, unlike Native SDK origins. No userinfo/query/fragment, whitespace, percent-encoded path or dot segments. Local requires loopback HTTP/HTTPS; API requires HTTPS |
| `api_key_env`, `auth_header` | Optional HTTP secret reference; `bearer` default or `api-key` |
| `database_url_env`, `azure_product`, `azure_extension_version` | Required for SQL; product `flexible_server` or `horizondb`, exact extension pin. No HTTP endpoint/key; no `api-key` header mode |
| `text_model` | Optional `TextModel` with bounded `name` and `revision` |
| `embedding_model` | Optional existing `EmbeddingModel`: 768, `cosine`, `l2-f32-v1` |
| `embedding_target` | Optional deployment/alias distinct from canonical model identity; requires `embedding_model` |
| `timeout_seconds` | Strict integer 1–120, default 30 |
| `max_output_tokens` | Optional strict integer 1–4096; requires an HTTP `text_model`, defaults to 1024 for HTTP summaries. Forbidden for SQL: no verified token-limit knob |
| `azure_summary_mode` | Explicit `generate` or `language` with a SQL text model; absent without one |
| `language`, `sentence_count` | Language selection (optional ISO-style code) is Language-mode-only; strict sentence count 1–20, default 3. A nondefault count is allowed only in Flexible Language mode |

Language mode is **Flexible Server only**, with
`text_model.name: "azure_cognitive.summarize_abstractive"`. Horizon Language is unsupported.
HTTP profiles cannot include Azure connection/mode/language settings.
Closed `InferenceInput` contains only `text`: non-whitespace, 1–65,536 characters,
UTF-8 ≤256 KiB, with original whitespace/bytes preserved. JSON input and HTTP
requests are also ≤256 KiB; encoding/wrapper overhead can exceed that bound.
`input_digest` is SHA-256 of those exact UTF-8 text bytes.

### HTTP, SQL, and output boundaries

HTTP uses `chat/completions` and `embeddings` under the configured base path,
verified TLS, no proxy environment or redirects, one connection, bounded timeout
and **2 MiB response** limit. Compatible wire shape is not universal model/vendor support.
Summary requests use `max_tokens`, no streaming; partial/tool/refusal outputs are rejected.
`inspect` constructs and closes the HTTP client without network calls, validating
configuration and credential headers, not provider connectivity.

SQL uses a dedicated autocommit connection, **TLS `verify-full`** with system CAs
unless an operator supplies a CA file, a 5-second connect/lock timeout, and configured
statement/overall timeout. It does not join a canonical memory transaction or session lock.
Each function statement still necessarily has a database transaction; external
provider execution/charges can outlive cancellation.
Reject superuser/BYPASSRLS, canonical namespace ownership, and membership in
`azure_pg_admin`, `azure_ai_settings_manager`, or `model_registry_manager`.
Before every call, check the exact `azure_ai` version, extension ownership through
`pg_depend`, a unique compatible non-set-returning overload, required argument/result
types, and schema `USAGE`/function `EXECUTE`. Missing/ambiguous contracts fail closed.
The adapter neither installs/configures the extension nor reads credential settings/model registries.
SQL `inspect` uses read-only catalog checks, never inference; successful preflight
does not prove provider connectivity, quota, or remote model permission.
Inference results are evaluated once in a `MATERIALIZED` CTE with a server-side
2 MiB serialized-result guard before transfer.
This is one SQL invocation, **not proof of one billable upstream call**.
The application does not retry; SQL embedding/Language explicitly set
`max_attempts => 1`. `azure_ai.generate` has no verified retry or output-token knob,
so extension-internal behavior and charges are not guaranteed by this adapter.

| SQL operation | Deliberately supported contract |
|---|---|
| `embed` | `azure_openai.create_embeddings`; Flexible first arg is deployment name, Horizon is model-registry alias. Named `dimensions => 768`, `timeout_ms`, `throw_on_error => true`, `max_attempts => 1`; `real[]` |
| `summarize` / `generate` | `azure_ai.generate` with named `prompt`, `model`, `json_schema`, `system_prompt`; schema wrapper `{name, strict: true, schema}`. Inspect deployed overload, accept text JSON or JSONB result, validate closed `{summary}`. No invented token/timeout arguments; statement timeout applies |
| `summarize` / `language` | Flexible `azure_cognitive.summarize_abstractive`; text/language, sentence count, `disable_service_logs => true`, explicit timeout/error/max-attempts controls. Preserve all `text[]` parts joined by blank lines, not just the first |

`SummaryResult` has `model`, `input_digest`, `summary`, and `status: "untrusted"`;
it is not a grounded assertion, approval, or compaction snapshot.
`GeneratedEmbedding` extends existing `VectorQuery` with `input_digest`; require
exactly **768 finite values with finite nonzero norm**, without padding/truncation.
Operator-pinned metadata/revisions do not cryptographically verify remote aliases,
deployment changes, or AIMM upgrades; a new model space needs a new identity/revision.
Use explicit Native `embedding_input` → provider `embed` → `PutEmbedding` with
unchanged digest/model/current-ACL/purge checks. There is no automatic upload.
Retain the exact generated payload and caller write key for uncertain write recovery,
not a newly generated embedding from a repeated model call.

`ProviderFailure.error` exposes sanitized `code`, `retryable`, `billing_unknown`;
no raw provider response, prompt, secret, or DSN is included in errors.
Request loss, timeout, or failed/malformed inference conservatively preserves possible billing.
`retryable` is not automatic retry or proof of no charge.
Cancellation propagates; do not infer provider rollback. Billing uncertainty is
separate from Native mutation `outcome_unknown` and is not a memory commit receipt.
CLI success is `{status: "ok", result, error: null}`; provider failure is
`{status: "error", result: null, error}`. Invalid-input/configuration errors exit 2,
other provider errors exit 1. Successful output can contain private/untrusted content.

### Deployment and qualification boundary

Stage `m2-scope-capture-policy` retains feature `optional_provider_adapters` and:

```json
{
  "model_inference": {
    "interface": "operator_cli_and_python",
    "extra": "providers",
    "backends": ["local_http", "openai_compatible", "azure_ai"],
    "azure_products": ["flexible_server", "horizondb"],
    "operations": ["inspect", "summarize", "embed"],
    "automatic": false,
    "publishes_memory": false,
    "live_provider_qualified": false
  }
}
```

Require exact service **0.0.26 / API v1 / schema 11**, schema history 1–11, and the
MemoryDB PostgreSQL 18.6 / `vector` 0.8.6 artifact pins. Schema 11 adds capture policy
migration 011 with no dependency-version change; inference DSN may point to a separate Azure database.
This is not qualification of full MemoryDB hosting on Flexible Server/HorizonDB.
Documented Azure versions/preview limits are not live verification; see
[official references and lifecycle limits](adr/0024-selectable-inference.md#azure-reference-boundary).
Operators own credentials, model deployment, privacy, logging/retention, and budgets.
The [exact-profile live results](INFERENCE_PROFILES.md#recorded-azure-live-evidence)
do not certify all Azure/LLM versions or models. One English Azure summary was
returned in Spanish; language/grounding/quality gates remain open.
No performance, MVP, or M2 completion is claimed.
See [profiles/operations](operations/README.md#selectable-inference-providers)
and [v25 qualification status](#v0025--schema-10).

## Episode query and pagination

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
Native JWT authenticated `POST /v1/episodes/query` is read-only, requiring current
read access but no write permission or `Idempotency-Key`.
Closed `QueryEpisodes` accepts only:

| Field | Contract |
|---|---|
| `scope_ids` | Required distinct UUID list, 1–32 entries |
| `occurred_from` | Timezone-aware timestamp or null, default null; inclusive lower bound |
| `occurred_to` | Timezone-aware timestamp or null, default null; exclusive upper bound |
| `max_items` | Strict integer 1–100, default 20; booleans/floats rejected |
| `before` | Closed `EpisodeCursor` or null, default null |

`EpisodeCursor` requires aware `recorded_at` and UUID `memory_id`.
Invalid UUIDs/naive timestamps, duplicate scopes, unknown fields, invalid limits,
or equal/reversed occurred-time bounds give **422 `invalid_request`**.
No owner/principal override, offset, arbitrary text query, `as_of`, or `known_at`.
There is no `source_namespace` filter: source identity is retained as HMAC anchors,
not a plaintext queryable source field.

### Occurred-time filter and current visibility

Scope and time predicates combine **before LIMIT**.
The interval is half-open `[from, to)`:
`occurred_from <= occurred_at < occurred_to`; omitted/null bounds are unbounded.
This filters reported event time, not historical ACLs or bitemporal reconstruction.
SQL selects metadata from `episode` joined to `object`, under current tenant/scope
RLS, without selecting content or creating audit/receipt/job/application writes.
**There is no ownership filter**: readable shared-scope episodes are included,
unlike caller-owned-only job query. Unknown/private/cross-tenant scopes contribute
no rows. No matches returns **200**, `episodes: []`, and `next_cursor: null` with
current epochs, not a hidden reason or total count.
Current ACL/purge visibility and the tenant response-delivery/drain barrier
apply on every page.

### Recorded-time cursor and metadata response

Order by `object.created_at DESC, episode.id DESC`, exposed as
`recorded_at DESC, memory_id DESC`, with exclusive
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`.
Fetch `max_items + 1`, return at most `max_items`, and emit `next_cursor` only on
overflow from the **last returned item**, never the lookahead.
Omitted/null `before` starts newest; null `next_cursor` ends continuation.
Ordering is **not `occurred_at`**: a late historical event admitted later can
appear newest among matching rows.
The unsigned cursor is position, not authority, snapshot, receipt, event sequence,
compaction watermark, cache, or retention object. It need not name an existing
episode; deleted/forged positions only narrow currently authorized rows.
Page membership can change with ACL/purge; newer recorded rows above an old
boundary require explicit restart without `before`.

Exactly **200 `EpisodePage`** returns `episodes: list[EpisodeSummary]` (at most 100),
`next_cursor: EpisodeCursor | None`, and existing `consistency`
(`access_epoch`, `deletion_epoch`).
Each summary contains only `memory_id`, `revision: 1`, `scope_id`, `occurred_at`,
and `recorded_at`. No content/body, consent reference, source URI,
`source_namespace`, event ID, or job payload is returned.
Explicitly select an episode, use `Explain(memory_id, revision=1)` for currently
authorized content, then optionally issue an explicit `Remember` with literal
episode evidence. A later Explain can fail after access/purge changes.
This does not ingest, synthesize, extract, or compact automatically; returned
content remains evidence, not trusted instructions or verified/current facts.

### SDK, surfaces, and deployment

Async `query_episodes(QueryEpisodes) -> EpisodePage` uses `mutation=False`,
call-time request revalidation, and normal **256 KiB request / 2 MiB response**
bounds. SDK response parsing also enforces the 100-episode bound.
Read-only response loss has sanitized `outcome_unknown: false`, not an uncertain
mutation outcome; an explicit repeat can see changed current data.
There is no automatic pagination/retry, provider call, cache, or SDK admin/worker method.
Native/SDK has **31 resource methods**; four MCP tools and the closed hook are
unchanged, without an episode-query tool or hook field.
Require exact service **0.0.26 / API v1 / schema 11**.
Stage `m2-scope-capture-policy` retains feature `episode_query`:

```json
{
  "episode_query": {
    "endpoint": "/v1/episodes/query",
    "order": ["recorded_at_desc", "memory_id_desc"],
    "pagination": "exclusive_keyset",
    "occurred_time_bounds": "half_open",
    "max_items": 100,
    "includes_content": false
  }
}
```

Current v26 requires schema 11/history 1–11 and migration 011, with unchanged
dependency versions and pinned artifacts. Stop/drain old components before migration
and use matching versions; no mixed-version promise.
See [operations](operations/README.md#episode-query-and-pagination),
[ADR 0023](adr/0023-episode-query.md), and [qualification evidence](#v0023--schema-10).

## Explicit batch capture

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.** Native JWT and caller-owned
`Idempotency-Key` are required for `POST /v1/captures/batch`, under existing
current tenant/scope read/write authorization, RLS, and response-drain barrier.
Closed `CaptureBatch` has exactly `episode: Observe` and
`memories: list[CapturedMemory]` with **1–16** entries. Both nested models retain
the [single-capture fields, normalization, and bounds](#atomic-structured-capture).
Each proposal has explicit intent and exactly one literal quote from the normalized
episode; scope and its revision-1 evidence ID are derived, never overridden.
Duplicate normalized model JSON candidates, including trim-equivalent entries,
give **422 `invalid_request`**. Order matters; this is not semantic deduplication,
LLM extraction, provider invocation, or automatic capture. Literal evidence does
not prove truth; publication remains reported and uncalibrated.

Exactly **201 `CaptureBatchResult`** returns:
`memory_id` (episode UUID), `revision: 1`, and
`synthesis_job_ids` (1–16 UUIDs in request order).
Existing terminal jobs may be returned: 201 does not mean fresh/pending, completed
publication, or an assertion ID. Existing `Capture`, `CaptureResult`,
`POST /v1/captures`, and `atomic_capture` retain their single-job shape.

### Atomic admission and per-job publication

Current scope authorization and [capture policy](#scope-capture-policy) precede
both exact receipt replay and source/intents deduplication. The outcomes below
assume policy permits the episode; policy denial writes nothing.

Observe and `Jobs.enqueue` execute in one existing tenant transaction/barrier.
Operation `capture_batch` has its receipt/audit; internal composition uses the
separate `capture-batch-observe-v1` namespace and indexed
`capture-batch-job-v1:keyhash:index` keys. These are server internals, not request
fields or client-generated replacement keys.
All **new** episode, lexical projection, job, identity, receipt, and audit writes
commit together. A late invalid quote, exhaustion of the remaining **100 active
jobs per scope** quota midway, or audit failure rolls back the whole new admission.
Pre-existing rows are unchanged. Existing quota applies only to newly admitted
jobs; the per-request 16-entry limit is not a new global quota.

Admitted jobs publish independently in existing worker transactions.
No atomic batch completion or execution-order guarantee follows from request
order. Use individual `get_job`, `query_jobs`, `cancel_job`, and `retry_job`;
there is no batch job, aggregate status, group cancel, or worker/provider call
inside admission. Existing attempts, leases, epochs, and publication fences remain.

### Ordered identity, current-access replay, and purge

| Request situation | Result under current access/deletion checks |
|---|---|
| Same key and normalized body | Same episode and ordered original job IDs |
| Same key with reordered body | `409`, not reordered replay |
| Fresh key, same source/intents | Original IDs, including cancelled/failed/succeeded jobs, without revival |
| Fresh key with reordered candidates | Reordered existing IDs |
| Source or any returned job purged | Whole-receipt `404`, no partial replay or resurrection |

Exact-key receipt replay rechecks current write access and **every** job object's
liveness. Source purge uses the existing closure over dependent jobs/assertions.
Single-job purge never permits reconstructing that purged identity.
Retry of an individual failed job remains explicit; batch replay still returns
original job IDs, not retry children. Existing source/principal intent identity
rules remain authoritative, and a batch is not a permanent whole-source seal.

### SDK, bounds, and deployment

Async `capture_batch(CaptureBatch, *, idempotency_key) -> CaptureBatchResult`
uses `mutation=True`, expects 201, and revalidates requests at call time.
The caller preserves the same key/body after an uncertain mutation outcome;
cancellation does not prove rollback. No automatic retry, split, or new keys.
Normal **256 KiB request / 2 MiB response** limits remain. Sixteen individually
valid large candidates can exceed the Native body limit and receive **413**;
the SDK does not bypass the bound or split the request.
Native/SDK has **31 resource methods**; MCP's four tools and the closed read-only
hook are unchanged, with no batch operation/field.
Require exact service **0.0.26 / API v1 / schema 11**.
Stage `m2-scope-capture-policy` retains feature `atomic_batch_structured_capture`:

```json
{
  "atomic_batch_capture": {
    "endpoint": "/v1/captures/batch",
    "max_jobs": 16,
    "recipe_version": "structured-remember-v1",
    "automatic_capture": false,
    "admission_atomic": true,
    "publication_atomic": false
  }
}
```

Existing `atomic_capture` metadata is unchanged with `max_jobs: 1`.
Current v26 requires schema 11/history 1–11 and migration 011, with unchanged
dependency versions and pinned artifacts. Stop/drain old components before migration
and use matching versions; no mixed-version promise.
See [operations](operations/README.md#explicit-batch-capture),
[ADR 0022](adr/0022-batch-capture.md), and [qualification evidence](#v0022--schema-10).

## Exact entity query and pagination

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
Native JWT authenticated `POST /v1/entities/query` is read-only, requiring
current read access but no write permission or `Idempotency-Key`.
Closed `QueryEntities` accepts only:

| Field | Contract |
|---|---|
| `scope_ids` | Required distinct UUID list, 1–32 entries |
| `entity_type` | Existing `EntityType` or null, default null |
| `canonical_label` | `ShortText` or null, default null; 1–256 characters after existing whitespace stripping |
| `max_items` | Strict integer 1–100, default 20; booleans/floats rejected |
| `before` | Closed `EntityCursor` or null, default null |

`EntityType` retains exactly `person`, `organization`, `project`, `component`,
`incident`, `task`, `decision`, `other`.
`EntityCursor` requires an aware `recorded_at` timestamp and UUID `memory_id`.
Invalid fields/types, duplicate scopes, empty/whitespace-only labels, invalid
UUIDs/timestamps/bounds, or extra fields give **422 `invalid_request`**.
No owner/principal override, offset, watch, historical ACL, or arbitrary query is accepted.

### Exact filters, not identity resolution

Optional type and label constraints combine with **AND before LIMIT**.
The type is an exact existing literal; label equality uses **case-sensitive
`C` collation** after normal contract whitespace stripping.
Omitted/null filters impose no additional filter on currently readable entities
in the requested scopes. Empty labels are invalid, not “all.”
There is no alias, fuzzy/substring/wildcard matching, Unicode normalization,
embedding lookup, merging, or automatic identity choice.
Same-label entities all retain distinct IDs in the paginated results.
Use unchanged known-ID `GET /v1/entities/{memory_id}` / SDK `get_entity` to inspect
evidence, then explicitly choose graph seeds; query does not prove that labels
refer to one identity or automatically run graph expansion.

### Current visibility and complete-page validation

Current tenant, requested scopes, source visibility, and RLS apply.
**There is no ownership filter**: currently readable shared-scope entities are
included, unlike caller-owned-only job query. Unknown/private scopes contribute
nothing. Zero matches returns **200**, `entities: []`, and `next_cursor: null`
with current epochs, not a hidden reason or total count.
Every page uses the current tenant response-delivery/drain barrier.
For each selected returned item, visible evidence count from
`entity_evidence JOIN episode` must equal stored `reference_count`.
A mismatch fails the **whole page** with **409 `entity_invalidated`**;
no silent skip, partial success, or fallback. Bounded SQL selects metadata and
counts without fetching evidence quotes; lookahead is only for overflow.

### Exclusive keyset position and typed response

Order by `object.created_at DESC, entity.id DESC`, exposed as
`recorded_at DESC, memory_id DESC`. The exclusive cursor boundary is
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`.
Fetch `max_items + 1` and return at most `max_items`.
Only overflow emits `next_cursor` from the **last returned item**, not the lookahead;
otherwise it is null. Omitted/null `before` starts newest; stop on a null
`next_cursor`, because submitting null again restarts.
The unsigned transparent cursor is position, not authority, snapshot, receipt,
cache, or retention object. It need not refer to an existing object.
Old/deleted/forged positions only narrow already-current authorized rows.
Each page is point-in-time, not stable membership across access/source/deletion
changes. New entities above an old boundary require an explicit restart without `before`.

Exactly **200 `EntityPage`** contains `entities: list[EntitySummary]` (at most 100),
`next_cursor: EntityCursor | None`, and existing `consistency`
(`access_epoch`, `deletion_epoch`). Each existing `EntitySummary` has only
`memory_id`, `revision: 1`, `scope_id`, `entity_type`, `canonical_label`, and
`recorded_at` (object creation time, not source event time).
No evidence quotes, source IDs, or total counts are returned.
Known-ID `EntityDetail` is unchanged. Labels remain human text: not content-free,
trusted instructions, or verified/current facts.

### SDK, surfaces, and deployment

Async `query_entities(QueryEntities) -> EntityPage` uses internal `mutation=False`,
call-time request validation, normal **256 KiB request / 2 MiB response** bounds,
sanitized read-only `outcome_unknown: false`, and no automatic pagination/retry.
SDK response parsing also enforces the 100-entity bound.
Existing `entity_invalidated` is reused; MCP/hook safe-error behavior is unchanged.
Native/SDK has **31 resource methods**; MCP has four unchanged tools and the
closed hook has no new entity field/operation. No SDK admin/worker function,
mutation, provider call, cache, or automatic identity selection is added.
Require exact **service 0.0.26 / API v1 / schema 11** across adapters.
Stage `m2-scope-capture-policy` retains `entity_query`:
`endpoint: "/v1/entities/query"`, `match: "exact"`,
`order: ["recorded_at_desc", "memory_id_desc"]`, `pagination: "exclusive_keyset"`,
`max_items: 100`.
Current v26 requires schema 11/history 1–11 and migration 011,
with no dependency-version, pinned-artifact, or AGE change.
Stop/drain old components and start matching versions; no mixed-version promise.
See [operations](operations/README.md#exact-entity-query-and-pagination),
[ADR 0021](adr/0021-entity-query.md), and [historical v21 evidence](#v0021--schema-10).

## Assertion metadata history

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
Native JWT authentication and current read access are required for
read-only `POST /v1/assertions/history`; no `Idempotency-Key` or write permission.
The closed `AssertionHistory` model accepts only:

| Field | Contract |
|---|---|
| `memory_id` | Required UUID of an ordinary assertion or canonical relation assertion |
| `max_items` | Strict integer 1–100, default 20; booleans/floats rejected |
| `before_revision` | Strict integer 1–1001 or null, default null; booleans/floats rejected |

Invalid shapes, UUIDs, bounds, or extra fields give **422 `invalid_request`**.
There is no identity/scope override, `as_of`, `known_at`, historical-ACL selector,
offset, watch, or full-value option. Wrong kind, private, or missing objects give
generic **404 `not_found`**, not an empty-success substitute.

### Exclusive revision position and current access

For a currently readable assertion, order by revision ordinal descending and
apply exclusive `revision < before_revision`. Omitted/null starts at newest;
`before_revision: 1` gives an empty page. `1001` includes the current head up to
the existing maximum revision 1000; it does not raise the revision-write limit.
Fetch `max_items + 1`, return at most `max_items`, and emit `next_before_revision`
only on overflow, using the **last returned ordinal**, never the lookahead row.
Otherwise it is null. Stop on null; submitting null again restarts newest.

The ordinal position is not authority, a snapshot, receipt, cache, or retained
server cursor. Current ACL/source/deletion guards and the existing tenant
response-delivery/drain barrier apply to every page. This is not historical access.
New revisions between pages are seen only by restarting without a position;
a former head's `known_until` can close. There is no cross-page snapshot promise.
`current_revision` is a point-in-time head, not a CAS reservation or proof of an
uncertain write's commit. Reconcile such a write with its original idempotency
key/body; do not automatically advance an expected revision or create a new key.

Missing/gapped selected revision metadata or no readable evidence fails the
**whole page** with **409 `assertion_invalidated`**. Missing canonical relation
endpoints fail the whole page with **409 `relation_invalidated`**.
No partial success, silent skip, or fallback to another revision is allowed.
The bounded fetched window, including the lookahead ordinal, must be contiguous;
evidence and relation checks apply to returned page items.

### Metadata response, not content-free data

Exactly **200 `AssertionHistoryPage`** contains `memory_id`, `scope_id`,
`subject`, `predicate`, `current_revision`, `revisions` (at most 100),
nullable `next_before_revision`, and existing `consistency`
(`access_epoch`, `deletion_epoch`).
Each item is typed `AssertionRevisionMetadata`; SDK response parsing enforces
the 100-item page bound and 1–32 evidence-reference bound.

| Revision field | Meaning |
|---|---|
| `revision` | Exact ordinal, within the existing 1–1000 revision bound |
| `valid_from`, `valid_to` | Nullable valid-time bounds |
| `recorded_at` | Lower system-time bound, not the episode event time |
| `known_until` | Nullable upper system-time bound; a subsequent revision can close it |
| `correction_reason` | Nullable caller-supplied reason |
| `epistemic_status` | Always `"reported"`, not verified truth |
| `evidence_refs` | 1–32 exact episode references, each ID at revision 1 |
| `relation` | `source_entity` and `target_entity` UUIDs for a canonical relation revision, otherwise null |

Bounded SQL selects metadata without fetching full assertion values or evidence
quotes. **Subject, predicate, and correction reason are still human text**:
do not claim the response is content-free, publish private metadata, or treat it
as trusted instructions/current facts. Use unchanged `Explain` with the exact
`memory_id` and chosen revision for full content and evidence under fresh checks.
Omitted Explain revision remains **1, not latest**; history does not change recall
time semantics, evidence identity, or purge behavior.

### SDK, surfaces, and deployment

Async `get_assertion_history(AssertionHistory) -> AssertionHistoryPage` uses
required internal `mutation=False`, call-time request validation, normal
**256 KiB request / 2 MiB response** bounds, sanitized read-only
`outcome_unknown: false`, and no automatic paging/retry.
The SDK recognizes sanitized `assertion_invalidated` and retains
`relation_invalidated`; MCP/hook safe-error allowlists are unchanged.
It adds no mutation, cache, provider call, watch, or retention object.
Native/SDK has **31 resource methods**; MCP's four tools and the closed hook stay
unchanged, with no history tool/field or SDK admin/worker function.
Require exact **service 0.0.26 / API v1 / schema 11** across adapters.
Stage `m2-scope-capture-policy` retains `assertion_history` metadata:
`endpoint: "/v1/assertions/history"`, `order: "revision_desc"`,
`pagination: "exclusive_revision"`, `max_items: 100`, `includes_values: false`,
`includes_evidence_quotes: false`.
Current v26 requires schema 11/history 1–11 and migration 011,
with no dependency-version or pinned-artifact change. Stop/drain old components
and start only matching versions; no mixed-version compatibility is claimed.
See [operations](operations/README.md#assertion-metadata-history),
[ADR 0020](adr/0020-assertion-history.md), and [historical evidence](#v0020--schema-10).

## Owned-job query and pagination

**Retained contract on v0.0.26/schema 11; implementation verified locally and in native CI.**
Native JWT authentication is required for read-only `POST /v1/jobs/query`;
no `Idempotency-Key` or write permission is required.
The closed `QueryJobs` model has these fields only:

| Field | Contract |
|---|---|
| `scope_ids` | Required distinct UUID list, 1–32 entries |
| `states` | Distinct `JobState` list, at most five; default `[]` means all five states |
| `max_items` | Strict integer 1–100, default 20; booleans/floats are not accepted |
| `before` | `JobCursor` or null, default null; omitted/null starts newest |

`JobState` reuses the existing exact literals `pending`, `running`, `succeeded`,
`failed`, `cancelled`, without changing lifecycle semantics.
Closed `JobCursor` requires both aware `created_at` timestamp and UUID `job_id`.
Invalid shapes, duplicates, bounds, timestamps, UUIDs, or extra fields give
**422 `invalid_request`**. There is no owner/principal/tenant/kind/payload-query
selector, offset, watch, `after`, or historical-ACL input.

### Caller ownership, visibility, and page failures

Select only the current tenant and authenticated principal's **own jobs**,
intersected with requested scopes, current RLS/source/deletion visibility,
and optional states. Scope-admin permission does not bypass caller ownership.
This is intentionally narrower than `GET /v1/jobs/{job_id}`, which retains
current-readable access to another principal's same-scope job when its ID is known.
Unknown/unreadable scopes contribute nothing, like recall. No matching rows gives
**200** with `jobs: []` and `next_cursor: null`, without a hidden reason or total count.

Every selected page item is reloaded through existing `Jobs.get` to check
reference counts and result liveness. An actual invalid selected job fails
the **whole page** with existing **404 `not_found` / 409 `job_invalidated`** behavior:
no quiet skip, partial success, or partial response.
Current permission/deletion checks and the tenant response-delivery/drain lock
apply to each page. Query does not change state, claim/cancel work, or invoke
workers/providers.

### Exclusive keyset position, not a snapshot

SQL order is `created_at DESC, id DESC`; UUID `id` breaks timestamp ties.
`before` applies `(created_at, id) < (before.created_at, before.job_id)`.
Fetch `max_items + 1` rows, return at most `max_items`, and emit `next_cursor`
only on overflow, using the **last returned job**, not the lookahead row.
Otherwise `next_cursor` is null, including an empty page.

The cursor is unsigned transparent position data, not authority, a receipt,
cache, snapshot, or retained server object. It need not name an existing job.
A forged/stale/deleted cursor can only bound the already-current authorized
owned candidate set; it cannot expand access or discover another principal's jobs.
`created_at` stays original through state transitions, not `updated_at` ordering.
Every page is point-in-time: state/access/deletion changes can change membership
between pages. Newer rows above the old cursor are not included below it;
restart without `before` explicitly. There is no stable-membership promise,
total count, snapshot watermark, global LSN, persistent cursor, or automatic pagination.

### Response, SDK, and deployment

Exactly **200 `JobPage`** returns `jobs: list[ListedJob]`,
`next_cursor: JobCursor | None`, and existing `Consistency`
(`access_epoch`, `deletion_epoch`), not snapshot authority.
`JobPage.jobs` is bounded to 100 by the response model, also validated by the SDK.
The existing checkpoint epoch reader is shared as `MemoryService.epochs` by
checkpoint, recall, and job query; this extraction changes no persistence,
schema, or mutation hashes.
`ListedJob` extends the complete existing `JobDetail` with UUID `scope_id`;
known-ID GET's `JobDetail` shape is unchanged.
Entries include references, state/timing, safe error, `retry_of`, and original
result revision 1, but no stored payload, evidence quotes, `lease_token`, or intent digest.

Async `query_jobs(QueryJobs) -> JobPage` uses required internal `mutation=False`,
call-time model validation, normal **256 KiB request / 2 MiB response** bounds,
sanitized read-only errors with `outcome_unknown: false`, and no automatic retry
or pagination. Existing SDK safe `job_invalidated` is reused; no new error code.
There are **31 Native/SDK resource methods**, four unchanged MCP tools, and no
MCP job tool or hook field. Administration/worker CLI functions are not SDK resources.
Require exact **service 0.0.26 / API v1 / schema 11** across adapters.
Stage `m2-scope-capture-policy` retains `job_query` metadata:
`endpoint: "/v1/jobs/query"`, `ownership: "caller"`,
`order: ["created_at_desc", "job_id_desc"]`, `pagination: "exclusive_keyset"`,
`max_items: 100`.
Historical v24→v25 had no SQL migration or dependency-version/artifact-pin change.
Current v26 requires migration 011/history 1–11 after stop/drain.
No performance, MVP, production, or DR qualification is claimed.
See [operations](operations/README.md#owned-job-query-and-pagination),
[ADR 0019](adr/0019-job-query.md), and [historical evidence](#v0019--schema-10).

## Checkpoint-head lookup

**Retained checkpoint-head contract; v0.0.26 implementation verified locally and in native CI.**
`POST /v1/checkpoints/head` requires Native JWT authentication and current read
access to the scope, not write access. No `Idempotency-Key` is required.
The closed typed `CheckpointBranch` body has exactly three required UUIDs:
`scope_id`, `run_id`, and `branch_id`. Invalid shapes/UUIDs or extra fields give
**422 `invalid_request`**. It accepts no identity override, `expected_head`,
harness selector, `as_of`, cross-branch latest selector, or history-listing input.

### Exact pointer and current authorization

Select only the existing branch with that exact tenant/scope/run/branch identity.
The lookup runs under the existing API tenant session-lock/drain barrier using
SQL `SELECT` without `FOR UPDATE`. It creates no run/branch and writes no state,
audit, or idempotency receipt.
Missing/private/wrong-scope/cross-tenant branches and empty non-invalidated
branches return generic **404 `not_found`**, without requested IDs/content or
empty success. A currently readable invalidated branch returns **409
`checkpoint_invalidated`** before using its retained head pointer.
There is no earlier-ancestor, sibling, or default-branch fallback.

Load the selected head through the existing envelope path, retaining current
scope permissions, HMAC, typed state, memory-reference, and live effect checks.
Run `effects_invalidated` and existing integrity failures retain their existing
rejections; an invisible head remains 404. Compare loaded `scope_id`, `run_id`,
`branch_id`, and `sequence` with the selected branch: a mismatch fails with
**409 `checkpoint_invalidated`**.
Source `forget` marks affected branches `invalidated=true` and deletes canonical
checkpoint/reference payloads. Opaque branch `head_id` and `sequence`,
`memory.object` anchors, and tombstones remain. Head lookup checks invalidation
first and returns 409 for a still-readable invalidated branch, never the retained
head ID. Revoked scope access instead hides the branch with 404;
neither path revives deleted state.

### Full envelope, not a reservation or commit receipt

Success is exactly **200 `CheckpointEnvelope`**, not a minimal ID or a new shape.
It includes state, the saved HMAC checksum and references,
`saved_access_epoch`, `saved_deletion_epoch`, `current_access_epoch`,
`current_deletion_epoch`, the current run-wide `tool_effects` ledger view,
`requires_reconciliation`, `untracked_effects`, `resume_allowed`, and
`automatic_reexecution: false`. The same envelope helper is reused:
saved epochs never authorize stale permissions, and `resume_allowed: true`
is neither approval nor proof of provider execution.
No restore, effect transition, harness execution, or automatic approval occurs on read.

Stable scope/run/branch IDs locate the head when a checkpoint ID is lost.
“Latest” is the point-in-time current pointer for that branch, not a watch,
reservation, successor guarantee, or latest-across-branches query.
`CreateCheckpoint.expected_head` remains required UUID-or-null caller CAS.
Writes can advance after the read; **409 `checkpoint_head_conflict`** requires
caller reconsideration, not an automatic retry with a refreshed head or new key.
Looking up the head does **not** prove the caller's uncertain mutation committed:
another writer may have advanced it. Retry the original mutation key/body to
obtain its original receipt under existing replay guards, then inspect the head.
GET by checkpoint ID can still load a live surviving ancestor under existing
checks, but does not promise latest. Restored forks have independent target heads.

### SDK, compatibility, and limits

Async `get_checkpoint_head(CheckpointBranch) -> CheckpointEnvelope` uses SDK
`_post` with `mutation=False`, normal **256 KiB request / 2 MiB response** bounds,
and call-time model validation. Read-only transport failures have
`outcome_unknown: false`; there is no automatic retry.
`checkpoint_invalidated` is already a recognized SDK safe code; no new error code is added.
There are now **31 Native/SDK resource methods**, but still **four MCP tools**.
MCP and hook surfaces are unchanged: no checkpoint tool or hook input field.
Require exact **service 0.0.26 / API v1 / schema 11** across adapters.
API/worker/readiness require exact history 1–11. Historical v24→v25 added no SQL migration or
dependency-version/artifact-pin change; stop/drain old components and use matching versions.
Stage is `m2-scope-capture-policy`; capabilities retain `checkpoint_head`:
`endpoint: "/v1/checkpoints/head"`, `read_only: true`,
`branch_identity: ["scope_id", "run_id", "branch_id"]`, `fallback_to_ancestor: false`.
This is not harness integration, compaction, MVP, or general recovery/production/DR qualification.
See [operations](operations/README.md#checkpoint-head-lookup),
[ADR 0018](adr/0018-checkpoint-head.md), and [verified evidence](#v0018--schema-10).

## Exact structured recall filters

**Retained recall-filter contract; v0.0.26 implementation verified locally and in native CI.**
Add `Recall.filters: RecallFilters | None = None` to the existing request.
`RecallFilters` is a shared, typed, closed nested contract: unknown fields are rejected.

| Field | Contract |
|---|---|
| `kind` | `"episode"`, `"assertion"`, or null; default null |
| `subject` | `ShortText` or null; default null; 1–256 characters after existing whitespace trim |
| `predicate` | String matching `^[a-z][a-z0-9_]{0,63}$` or null; default null |

`kind: "episode"` cannot combine with non-null subject or predicate.
Invalid fields, shapes, values, or combinations give **422 `invalid_request`**.
There are no field aliases, arbitrary SQL, model-inferred filters, ranges, or array-valued
selectors. Omission, null, `{}`, and all-null fields preserve identical baseline
results across lexical/vector/hybrid modes.

### Exact selection, not query expansion

Non-null fields combine with **AND**. Subject or predicate implies assertion
candidates, including relations. `kind: "assertion"` alone includes relation
assertions; `kind: "episode"` excludes all assertions.
Subject/predicate equality is **exact and case-sensitive using `C` collation**
after normal `Contract` string trimming. No substring/FTS, Unicode normalization,
fuzzy matching, or entity resolution is applied. A subject names the stored
assertion subject, not a resolved entity alias.
Empty-query lexical recall browses exact filtered candidates; normal nonempty
lexical queries still require their lexical match. Filters do not confer query bypass.
The separate required-reference contract retains its keyword bypass only.

### Candidate, ranking, and coverage boundary

Apply filters **inside the shared materialized candidate relation, before
lexical/vector/hybrid ranking, coverage, and required-reference eligibility**,
not after selecting top-K results. Ranks, cosine candidates, and RRF contributions
are calculated over the filtered eligible universe.
The same frozen `as_of`/`known_at`, requested scopes, current RLS, evidence
visibility/integrity, and deletion gates remain; filters only narrow selection.
Lexical/vector incompleteness reflects this filtered eligible candidate universe,
not excluded objects. It is still independent of query relevance and item limits.
`coverage.jobs_pending` intentionally remains a requested-scope signal:
it does **not** claim structured matching of job payloads.

Nonempty `required_memory_refs` remain lexical-only, with default revision **1,
not latest**, existing time selection, and request-order prefix.
Every required ref must also satisfy filters. A mismatch gives generic
**404 `not_found` for the whole request**, with no missing IDs or partial pack;
required refs never bypass filters. The complete compact `ContextPack` UTF-8 byte
budget, optional omission/truncation, required **422 `budget_exhausted`**, and
existing `budget_too_small`/successful-empty reasons are unchanged.

### Surfaces, deployment, and limits

Existing typed SDK `recall(Recall)` and MCP `memory_recall` expose the additive field.
There are **31 Native/SDK resource methods and four MCP tools**; filtering itself
adds no route or safe error code. SDK recall remains read-only with `outcome_unknown: false`,
sanitized call-time errors, and no automatic retry.
Hook input does **not** accept `filters`; unknown fields are rejected, and its
internal `Recall.filters` defaults to `None`, preserving trusted startup boundaries.
Filters do not grant authority, verify content, or infer intent.
Recall filters themselves add no dependency-version/index change, persisted priority,
or cache. Schema 11 and exact history 1–11 are required; use matching
**service 0.0.26 / API v1 / schema 11** components after stop/drain and migration.
Stage is `m2-scope-capture-policy`; capabilities retain `recall_filters`:
`fields: ["kind", "subject", "predicate"]`, `match: "exact"`, `combination: "and"`,
`retrieval_modes: ["lexical", "vector", "hybrid"]`.
MVP, semantic-quality, performance, production, and DR qualification are not claimed.
See [operations](operations/README.md#exact-structured-recall-filters),
[ADR 0017](adr/0017-recall-filters.md), and [historical filter evidence](#v0017--schema-10).

## Required-context recall

**Retained required-context contract; v0.0.26 implementation verified locally and in native CI.**
This is an additive field on existing `Recall`, not a new route or SDK method.

| Request rule | Contract |
|---|---|
| `required_memory_refs` | Omitted or `[]` by default; at most 16 `MemoryReference` entries |
| Reference | `memory_id` UUID and revision 1–1000; omitted revision is **1, not latest** |
| Identity/count | Unique memory IDs, even across revisions; count must be at most `max_items` |
| Retrieval | Nonempty references require `retrieval_mode: "lexical"`; vector/hybrid reject them |
| Recall mode | Both explicit and implicit; the existing implicit 2,000-byte cap remains |

Invalid field combinations, duplicate IDs, excessive count, or invalid reference shapes/ranges
return **422 `invalid_request`**. Omitting the field or supplying `[]` retains
baseline lexical/vector/hybrid ordering, results, and pack semantics within the selected structured filters.
No new read writes, idempotency requirement, priority metadata, persistent cache,
model inference, or provider call is introduced.

### Candidate eligibility and ordering

Required references use the **same currently authorized, materialized
episode/assertion candidate relation** as normal recall, with the requested
`scope_ids` and frozen `as_of`/`known_at`. They do not expand scopes, override
ownership/ACLs, bypass time or [structured filters](#exact-structured-recall-filters),
select latest, or fall back to another revision.
Existing bitemporal rules select one revision per object at a given `known_at`.
Relation assertions remain eligible under the same filters; entity objects are
wrong-kind references, not recall candidates.
Any missing/unreadable/purged/wrong-kind/out-of-request-scope/time-ineligible or
wrong-revision reference fails the **whole request with generic 404 `not_found`**.
No partial context or names of missing references are disclosed.

Required references bypass **query keyword matching and ranking cutoff only**.
They appear first in **request order**, followed by normal lexical-ranked optional
items. Required IDs are excluded from optional candidates to prevent duplicates.
Both groups count within `max_items`; the extra optional overflow candidate still
signals `coverage.truncated`.
Both `simple-v1` and `ja-janome-0.5.0-v1` work. Exact canonical references may be
included when Japanese projections are missing, but `coverage.lexical_incomplete`
and the existing incomplete-coverage metadata remain; this is not index repair.

### Whole required prefix or error

The budget still covers the **entire compact `ContextPack` JSON in UTF-8 bytes**,
including citations, quoting, and the warning/`refresh_required` marker.
Relation entity UUIDs and citation overhead remain inside that same byte budget.
`token_budget` is not a promise of exact model-token accounting.
If any whole required item cannot fit, Native returns **422** with
`ErrorBody.code` equal to `budget_exhausted`: no successful empty, omitted-required,
or partial-required context. This can occur at the minimum budget 64 even when
the empty envelope itself would not fit.
Optional items retain greedy whole-item omission and `coverage.truncated`.
Without required references, the existing empty-envelope **422 `budget_too_small`**
and successful-empty `empty_reason: "budget_exhausted"` behavior are unchanged.
The new error code and that existing successful-empty reason are distinct.

Native SDK and MCP propagate safe `budget_exhausted` without raw content.
SDK recall remains read-only: errors have `outcome_unknown: false`, with no
automatic retry. Explicitly reconsider the caller-selected refs/budget; do not
silently weaken the required prefix or widen access.
A selected reference is **not policy authority, verified approval, trusted
instructions, or a current-fact guarantee**. There is no automatic essential-
constraint detection, semantic-quality/performance qualification, or MVP claim.

### Surfaces and compatibility

Existing typed SDK `recall(Recall)` and MCP `memory_recall` accept the field;
there are still **31 Native/SDK resource methods and four MCP tools**.
The hook input does **not** accept `required_memory_refs`: extra fields are rejected,
and its internally constructed `Recall` defaults to `[]`. No host pinning is added.
The shared restricted safe-code catalog adds `budget_exhausted`; SDK retains its
broader Native error catalog.
Require exact **service 0.0.26 / API v1 / schema 11** across adapters.
API/worker/readiness require exact schema history 1–11. Historical v24→v25 added **no migration**,
dependency upgrade, or pinned-image change; older schemas still migrate after stop/drain.
Stage is `m2-scope-capture-policy`; capabilities retain `required_context` with
`retrieval_modes: ["lexical"]`, `max_refs: 16`, `order: "request_order"`,
and `budget_policy: "all_required_or_error"`.
See [operations](operations/README.md#required-context-recall),
[ADR 0016](adr/0016-required-context.md), and [historical evidence](#v0016--schema-10).

## Explicit job cancellation

**Retained job-cancellation contract; v0.0.26 implementation verified locally and in native CI.**
`POST /v1/jobs/{job_id}/cancel` requires Native JWT authentication and the caller's
`Idempotency-Key`. `CancelJob` accepts exactly:

```json
{"expected_state":"pending","expected_attempt":0}
```

`expected_state` is `pending` or `running`. `expected_attempt` is a **strict
integer 0–5**, with running requiring **at least 1**; booleans/coerced integers
and extra fields are invalid. There is no reason, provider, lease token, or
arbitrary state-forcing input. Obtain state/attempt from job GET and explicitly
choose that CAS. It is **not tenant `access_epoch` or external tool state/version**.

### Authority and atomic outcome

The job's `principal_id` must match the authenticated current principal, with
current **read and write** scope permissions and retained source visibility/
integrity checks. Another readable same-scope principal cannot cancel, even with
`admin` permission; runtime `job_update` RLS is unchanged.
Missing/private/cross-tenant/non-job/purged targets return `404`; existing
invalidated references give `409 job_invalidated`.
An atomic SQL UPDATE matches **both expected state and attempt**.

HTTP **200**, not 202, returns a committed `JobReceipt`:

```text
{job_id, kind: "structured_remember", recipe_version: "structured-remember-v1"}
```

GET confirms `state: "cancelled"`. Pending or running jobs, with active **or
expired** leases, may transition to terminal cancelled. It clears stored job
payload, `lease_token`, `lease_until`, and `error_code`, with no result.
It preserves the same job identity, attempt, source/intent/input references,
`retry_of`, and `created_at`; DB-clock `updated_at` records completion.
No cancellation reason/private free text is stored.
State change, `job_cancelled` audit, and idempotency receipt are atomic.
The stored idempotency result is only `{job_id}`, with HMAC-protected request/key.
Transaction failure rolls back all three. No access/deletion epoch advances.

The existing tenant **session advisory lock is held through HTTP delivery**;
worker claim/publication use the same barrier. Cancellation is not a queued
cancellation job, worker kill, or provider interruption/compensation.
Old preparation may continue, but publish/heartbeat/fail sees not-running and
returns `job_lease_conflict`; the worker reports `lease_lost`.
If cancellation wins, that job publishes no result. If publication commits first,
cancel conflicts and the succeeded result remains: **no unpublishing** occurs.

### Conflicts, replay, and retention

`409 job_cancel_conflict` means the state/attempt changed or the job is terminal
(`succeeded`, `failed`, or `cancelled`), including a fresh-key cancel of an
already-cancelled job. Same successful key/body replays the original receipt
only while current owner/write/access/liveness checks still pass.
Changing the body **or job ID** under that key gives `409 idempotency_conflict`.
After unknown HTTP delivery, use the **same key/body or current job GET**;
never blindly choose fresh expected values or a new key. No automatic retry.

Cancellation is **not `forget`**. Canonical source episodes/evidence, opaque
intent references, and dedup anchors remain. No erasure of prepared worker memory,
WAL, or backups is promised. Source forget still purges the dependent job and
denies cancellation replay; granting access again cannot resurrect purged payload.
Retry remains **failed-only**, so cancelled jobs give `409 job_retry_conflict`.
Ordinary same-intent enqueue/capture dedups to the cancelled job, and capture
replay retains its original episode/job pair; neither revives it.
Do not bypass dedup with recipe versions or invented intent keys.
Cancelled jobs count toward neither the 100 active-job cap nor `jobs_pending`.

### Schema and SDK boundary

`010_job_cancellation.sql` modifies job-state/payload constraints and the guard
trigger, adding no table. Cancelled state is DB-immutable: no revival or rewrite.
Constraints require null payload/result/lease/error; initial jobs must still be
pending at attempt 0. Existing pending/running/succeeded/failed semantics remain.
Schema-9→10 migration requires stopping/draining old processes; PostgreSQL 18.6,
pgvector 0.8.6, dependency versions, and pinned artifacts are unchanged.

SDK `cancel_job(UUID, CancelJob, *, idempotency_key) -> JobReceipt` is asynchronous,
revalidates models, expects exactly HTTP 200, and includes `job_cancel_conflict`
in its safe Native error catalog. With entity query, the current Native
resource/SDK surface is **31 methods**. MCP's four tools and the read-only hook are unchanged.
Cancelling a Python task does not invoke this job-cancellation endpoint.
All adapters require **service 0.0.26 / API v1 / schema 11**; readiness checks
exact history 1–11. The historical v0.0.15 stage was `m2-job-cancellation`;
the current stage is `m2-scope-capture-policy`. Capabilities retain
`job_cancellation` metadata: `endpoint: "/v1/jobs/{job_id}/cancel"`,
`compare_and_swap: ["state", "attempt"]`, `terminal_state: "cancelled"`,
`provider_interruption: false`.
See [operations](operations/README.md#explicit-job-cancellation) and
[ADR 0015](adr/0015-job-cancellation.md).

## Runtime readiness

**Retained readiness contract; v0.0.26/schema 11 implementation verified locally and in native CI.**
Health probes are public, unauthenticated paths, not Native memory resource
routes. `GET /healthz` retains exactly `{"status":"ok"}` after successful startup
without DB calls. `GET /readyz`, introduced in v0.0.14, ignores supplied authorization headers and
selects no tenant/principal. Expected readiness responses are:

| HTTP status | Exact body | Headers |
|---|---|---|
| `200` | `{"status":"ready"}` | `Cache-Control: no-store`; generated UUID `X-Request-ID` |
| `503` | `{"status":"not_ready"}` | `Cache-Control: no-store`; generated UUID `X-Request-ID` |

OpenAPI describes both responses with typed `ReadinessStatus`, not Native
`ErrorBody`. A wrong HTTP method returns 405 without running the readiness check.

No reason, DSN, token, payload, identity, or schema inventory appears in the
public body. Each admitted probe calls existing `validate_runtime` on a fresh
connection using the **same runtime DSN**—never admin credentials or fallback.
Validation sessions, including API startup and worker validation, explicitly set
`default_transaction_read_only = on`. Validation uses at most four explicit SQL
statements: one `SET`, then three `SELECT` statements for role catalogs, schema
history, and extension catalogs. Read-only is confined to this dedicated
validation connection; later Native mutations remain writable:

- Reject superuser, `BYPASSRLS`, and table ownership/owner-role membership in
  `memory` or `memory_ops`, including `NOINHERIT` membership.
- Require exact migration history `[1,2,3,4,5,6,7,8,9,10,11]`.
- Require `vector` **0.8.6 in `public`**.

There are no memory-payload reads, tenant locks, audit/epoch/job/receipt writes,
migrations, provider calls, successful-result cache, background checks, or retries.
Existing fail-closed startup behavior is retained.
`RuntimeValidationError` subclasses `RuntimeError`, retaining previous startup
messages while identifying expected configuration drift with a static code.

### Admission, cancellation, and diagnostics

One active check is admitted per API app/process. A concurrent request returns
503 immediately with log reason `probe_busy`, **without a second connection,
waiting, or cached success**. This is per-process admission, not a global rate
limit or request-flood qualification.
The fixed `asyncio` active-check timeout budget is **5.0 s**; DB connect,
statement, and lock budgets remain **5 s**. Cancellation/connection cleanup can
add latency: this is **not a hard wall-clock SLA**. Cancellation propagates and
releases the gate/connection; no retry is performed.

Expected failures log `readiness_unavailable` with generated `request_id` and
a static `reason`: `runtime_role_invalid`, `schema_unavailable`,
`schema_version_mismatch`, `extension_version_mismatch`, `probe_busy`, or an
exception class name. The readiness diagnostic includes no raw error string,
traceback, DSN, credentials, or payload. `psycopg.Error` and `TimeoutError` become
503. Unexpected programming exceptions, including an ordinary `RuntimeError`,
are **not** caught and disguised as not-ready responses.

### Interpretation and compatibility

This is a **point-in-time connection/runtime-role/schema/vector contract**, not:
complete principal authorization; table-grant/RLS-policy integrity auditing;
a write transaction, writability, or primary check; ongoing JWT verification;
tokenizer/provider readiness; backlog/load/HA/DR/performance/quality/production
qualification. A SELECT-only database can pass these bounded checks.
Resource routes do not call the probe and do not gain a permanent fail-closed
gate after drift. Readiness helps operators stop traffic, **not replace the
authorization boundary**; existing Native authorization remains enforced.

Use `/healthz` for liveness, not dependency readiness that can cause restart
storms. Configure orchestrator failure/recovery thresholds, including for busy
503 responses, and restrict/rate-limit public probes at the deployment perimeter.
No Kubernetes, Compose, or Docker `HEALTHCHECK` configuration is added.

The historical v0.0.14 stage was `m2-runtime-readiness`; the current stage is
`m2-scope-capture-policy`. Authenticated capabilities retain
`health_probes` metadata: `liveness: "/healthz"`, `readiness: "/readyz"`,
`readiness_timeout_seconds: 5.0`, `readiness_max_in_flight_per_process: 1`.
The public memory surface retains **31 resource methods**, including episode query; health paths are
excluded from SDK resource-route coverage. No SDK/MCP/hook probe method is added.
All matching adapters require **service 0.0.26 / API v1 / schema 11**.
Readiness behavior is retained; schema 11 requires migration 011 after retained
010 for job cancellation. No dependency or pinned-image changes are added.
See [operations](operations/README.md#runtime-readiness) and
[ADR 0014](adr/0014-runtime-readiness.md).

## Scope-access administration

**Retained scope-access contract; v0.0.26 implementation verified locally and in native CI.**
`pg-agmemory scope-access get|set|revoke --tenant-id UUID --scope-id UUID --principal-id UUID`
is a privileged administrative CLI, not an agent tool or runtime API.
It targets **existing same-tenant** tenant/scope/principal records; it never
creates identities or scopes. IDs are parsed as UUIDs and normalized for the
lock key. Select approved opaque IDs from trusted administration, never retrieved text.
The public Native memory surface is 31 resource methods: **no HTTP, MCP,
or Python SDK administration method** is added.

### Authority and compare-and-swap

Only `PGAG_ADMIN_DATABASE_URL` is accepted. The connected DB role must have
`rolsuper` or `rolbypassrls` **and appropriate SQL table privileges**.
Runtime credentials are rejected even for `get`; RLS bypass does not itself
grant table privileges. There is no JWT, `--subject`, `--once`, or runtime-URL fallback.
`get` uses no `FOR UPDATE`: a nonowner `BYPASSRLS` role can inspect with
`USAGE` on `memory` and `SELECT` on schema history, tenant, scope, principal,
and `scope_member`. Mutation privileges additionally include `UPDATE` on the
tenant, the applicable `SELECT`/`UPDATE`/`INSERT`/`DELETE` on `scope_member`,
and `USAGE` on `memory_ops` plus `INSERT` on `scope_access_event`.
The session advisory barrier is still required for reads; read-only table
privileges do not remove the mandatory RLS-bypassing role requirement.
Before operation, the CLI checks the role, exact schema history **1–11**, and
`vector` **0.8.6 in `public`**.

| Operation | Required intent | Semantics |
|---|---|---|
| `get` | Three IDs only; no mutation options | Current membership and tenant epoch, evaluated under the response-drain barrier |
| `set` | `--expected-access-epoch`, `--permissions`, and exactly one expiry choice | Full replacement of permissions and expiry, not merge |
| `revoke` | `--expected-access-epoch`; no permission/expiry options | Delete the membership row; already absent at the current epoch is a no-op |

Every mutation requires an explicit expected epoch in **1–9223372036854775807**.
Compare against the **tenant-wide** `access_epoch` under lock, before checking
whether the requested state already matches. A stale expectation always yields
`access_epoch_conflict`; an unrelated scope change can conflict too.
No automatic retry, HTTP `Idempotency-Key`, mutation receipt, or receipt queue exists.

`set` accepts distinct flags from `read`, `write`, `delete`, or `admin` alone.
Duplicates and mixing `admin` with other flags are rejected. Write-only and
delete-only configurations are allowed as DB flags; they do not override
Native operation requirements, including read access where required.
Permissions are returned in canonical **read, write, delete, admin** order;
reordering is equivalent and does not create a change.
Choose **`--expires-at` with a timezone-aware ISO timestamp or `--no-expiry`**.
Naive timestamps are invalid; an expiration at/before the DB clock sampled
after acquiring the lock is rejected. Permanent access must be explicit:
omitting expiry never silently removes it. Expiry-only changes advance the epoch.

### Results, expiry, and audit

Success is **one JSON line, with no result wrapper**:

```text
{
  operation, tenant_id, scope_id, principal_id, access_epoch, changed,
  membership_exists, permissions, expires_at, effective_permissions, evaluated_at
}
```

Absent membership has `membership_exists: false`, `permissions: []`, and
`expires_at: null`. A legacy empty-permission row still has
`membership_exists: true`. `effective_permissions` is empty when expired at the
DB `evaluated_at`, all four flags for effective `admin`, otherwise the configured
flags. This is **point-in-time membership interpretation, not complete Native
action authorization**; access can expire immediately after the result.
Natural expiration does not advance `access_epoch`, delete payload/audit, or
drain in-flight HTTP. Use an explicit revoke/barrier when strong drain is needed.

Retained **`009_scope_access.sql`** creates privileged-only
`memory_ops.scope_access_event`, with forced RLS and **no runtime policies/grants**.
The primary key is `(tenant_id, access_epoch)`; same-tenant foreign keys bind the
target scope/principal. Events record `set`/`revoke`, before/after permission and
expiry values, DB `recorded_at`, and `database_role` captured from `current_user`.
`recorded_at` uses the database clock; `evaluated_at` is response-only, not an audit field.
This identifies the executing database role, not an impersonated end-user actor.
They contain no plaintext memory content, external subjects, or DSNs.
Existing ACL rows are preserved; **prior manual changes receive no audit backfill**.
The command adds no implicit ownership or purge behavior.
**Only actual changes** atomically update membership, increment the tenant epoch,
and append an audit event. Reads, no-ops, conflicts, and rolled-back changes
create no event or epoch advance. Epoch exhaustion rejects a change.
Privileged administrators can alter the database: this is **not tamper-proof,
a standalone revocation recovery ledger, or a DR solution**.

### Response-drain barrier and failures

A dedicated synchronous admin connection uses autocommit and **5 s connect,
statement, and lock timeouts**. It acquires the API/worker's same **session**
advisory lock, `pg_advisory_lock(hashtextextended(canonical_tenant_uuid, 0))`.
Hold it through the membership transaction's commit **and the CLI JSON stdout
flush**; closing the connection releases it. `get` and mutation no-ops also
acquire the barrier. Do not pool this connection or substitute an xact-only lock.
An earlier slow response can block administration; a lock timeout fails without
change. Mid-change failure rolls back membership, epoch, and audit together.
Failure cleanup closes the session and releases its lock.
Cooperating same-version API clients can stay online during these commands;
**migration still requires stopping/draining old processes**.

Syntax, model, and configuration errors use static sanitized stderr, exit **2**,
and no JSON. Invalid CLI syntax reports `invalid_scope_access_arguments`;
missing `PGAG_ADMIN_DATABASE_URL` also produces a static stderr diagnostic,
exit 2, and no stdout. DB/domain operational failures use stdout, exit **1**:

```json
{"error":{"code":"access_epoch_conflict","outcome_unknown":false}}
```

The error catalog is `admin_role_required`, `admin_privilege_required`,
`schema_unavailable`, `schema_version_mismatch`, `extension_version_mismatch`,
`not_found`, `access_epoch_conflict`, `access_epoch_exhausted`, `invalid_expiration`,
`admin_database_unavailable`, and `admin_database_error`.
No raw DB error, DSN, credentials, or private data is printed.
Commit transport failure is conservatively `outcome_unknown: true`; a failure
before commit attempt is false. Cancellation, process kill, or lost stdout can
also leave a mutation outcome unknown. **Do not blindly replay a stale CAS**:
inspect with `get` and privileged audit, then explicitly authorize a new operation
using the freshly observed epoch. There is no automatic retry.

The barrier cannot retract already-delivered context. Restoring latest ACL and
deletion records remains manual; granting access cannot resurrect purged data.
The historical v0.0.13 stage was `m2-scope-access`; the current stage is
`m2-scope-capture-policy`. Capabilities retain
`scope_access_administration` metadata:
`transport: "admin-cli"`, `command: "scope-access"`,
`compare_and_swap: "tenant_access_epoch"`, `audit: "database_role"`.
PostgreSQL 18.6/pgvector 0.8.6 pinned images and Python dependency versions are
unchanged. See [operations and example](operations/README.md#scope-access-administration)
and [ADR 0013](adr/0013-scope-access.md).

## Python SDK

**SDK retains 31 memory methods; providers use a separate library/CLI; v0.0.26 implementation verified locally and in native CI.**
`from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError` exposes an
async-only client for the existing public Native memory resources. Import
request/response types from `pg_agmemory.models`; requests are revalidated at call
time, including mutable model instances, and responses use those same typed models.
The package includes a PEP 561 `py.typed` marker.

### Installation, lifecycle, and authority

Install the matching checkout with `python -m pip install '.[sdk]'`.
`pg-agmemory[sdk]` adds only `httpx==0.28.1`; **the core distribution still includes
FastAPI, psycopg, and Janome**. This is not a separately published lightweight
SDK or a PyPI publication claim. SDK import without HTTPX raises a clear static
`ImportError`; HTTPX supplied by `mcp`/`hook`/`providers` also works. Detection is dependency
availability, not the identity of the selected extra. Docker test/runtime include
`sdk` and `providers` alongside `mcp` and `hook`; core-only/hook-only/sdk-only/providers-only
checks are implemented, including absence of the MCP SDK in all four profiles.
All four genuine noneditable core/hook/sdk/providers installation profiles and
packaged `py.typed` verification passed in the historical v24 987-test qualification,
locally and on both native architectures; this is not v26 qualification.
There are no Python dependency upgrades.

Construct with explicit `AsyncMemoryClient(api_url, api_token)`, never untrusted
per-call configuration. `NativeSettings` requires a fixed HTTPS origin or
loopback HTTP origin, without application path, userinfo, query, or fragment.
Constructor validation checks bearer-token **shape only** and retains sanitized
`ValueError` configuration errors, not `MemoryClientError`; the server performs
authentication and authorization. Request scopes narrow current server ACLs,
never select another identity or grant access.

Use `async with ... as memory:` exactly once per client instance. Entry creates
an owned HTTP client and performs a mandatory authenticated capabilities probe
requiring exact **service `0.0.26` / API `v1` / schema `11`** before resource use.
Failed entry closes owned resources in `finally`. Calls before entry or after exit
raise `client_not_open`; re-entry raises `client_already_used`.
Exit releases connections only: **it does not forget data**.
The caller must await its outstanding tasks, or cancel and await them, **before
exiting the context**. Client close is not a request scheduling/cancellation
manager or a DB rollback. Cancellation of in-flight mutations still needs reconciliation.
No automatic environment loading, retry, cache, DB credentials, provider call,
host registration, delegation, or automatic job is introduced.

### Typed resource methods

All methods are asynchronous. Body names below are native request models; ID
arguments are UUIDs, not arbitrary paths. Every mutation requires a caller-owned
keyword-only `idempotency_key`; this includes `forget` preview and purge,
both HTTP 202. Other read-only methods need no key and preserve HTTP 200;
other writes retain their Native 201/202 status; `cancel_job` requires exactly
HTTP 200. A cancellation receipt confirms committed cancellation, not publication.
Each internal SDK POST explicitly distinguishes reads from mutations rather than
inferring mutation from the expected success status. HTTP 200 cancellation still
requires key validation and conservative mutation-outcome handling; `forget`
preview retains that conservative handling too.

| Method / request | Typed result |
|---|---|
| `observe(Observe)` | `ObserveResult` |
| `query_episodes(QueryEpisodes)` | `EpisodePage` |
| `capture(Capture)` | `CaptureResult` |
| `capture_batch(CaptureBatch)` | `CaptureBatchResult` |
| `remember(Remember)` | `RememberResult` |
| `revise_assertion(UUID, ReviseAssertion)` | `RevisionResult` |
| `get_assertion_history(AssertionHistory)` | `AssertionHistoryPage` |
| `recall(Recall)` | `RecallResult` |
| `explain(Explain)` | `EpisodeExplanation \| AssertionExplanation` |
| `forget(Forget)` | `DeletionPreview \| DeletionResult`, selected by request mode |
| `get_deletion(UUID)` | `DeletionProgress` |
| `embedding_input(Explain)` | `EmbeddingInput` |
| `put_embedding(PutEmbedding)` | `EmbeddingReceipt` |
| `create_entity(CreateEntity)` | `EntityReceipt` |
| `get_entity(UUID)` | `EntityDetail` |
| `query_entities(QueryEntities)` | `EntityPage` |
| `create_relation(CreateRelation)` | `RememberResult` |
| `revise_relation(UUID, ReviseRelation)` | `RevisionResult` |
| `expand_graph(ExpandGraph)` | `GraphResult` |
| `enqueue_job(EnqueueJob)` | `JobReceipt` |
| `get_job(UUID)` | `JobDetail` |
| `query_jobs(QueryJobs)` | `JobPage` |
| `retry_job(UUID, EnqueueJob)` | `JobReceipt` |
| `cancel_job(UUID, CancelJob)` | `JobReceipt` |
| `create_checkpoint(CreateCheckpoint)` | `CheckpointReceipt` |
| `get_checkpoint(UUID)` | `CheckpointEnvelope` |
| `get_checkpoint_head(CheckpointBranch)` | `CheckpointEnvelope` |
| `restore_checkpoint(RestoreCheckpoint)` | `CheckpointEnvelope` |
| `plan_tool_effect(PlanToolEffect)` | `ToolEffectReceipt` |
| `get_tool_effect(UUID)` | `ToolEffectDetail` |
| `transition_tool_effect(UUID, TransitionToolEffect)` | `ToolEffectReceipt` |

This covers public memory resource routes, **not CLI admin/worker functions**.
Capabilities probing is internal; no public health, OpenAPI-download, or raw
arbitrary-request method is added. There is no sync client, TypeScript SDK, or
token-refresh flow.

### Bounds, errors, and recovery

The shared Native HTTP transport retains MCP/hook bounds: **20 s per exchange,
10 s I/O / 5 s connect**, at most **4 connections**, verified TLS,
no proxy environment and no redirects. Response limit is **2 MiB**; requests
are **256 KiB**, except SDK `create_checkpoint` at **1 MiB**. This does not raise
MCP/hook request limits.

`MemoryClientError` is an alias of existing `AdapterFailure`. Inspect
`exc.error.code`, `.retryable`, `.outcome_unknown`, `.native_status`, and
`.request_id`; diagnostics are sanitized, never raw response/input/token content.
The SDK allows the catalog of current Native domain errors, including
`capture_policy_denied` and `capture_policy_invalid`; the restricted
shared MCP/hook safe-code set adds `budget_exhausted` for required-context errors.
Unknown server codes become `native_api_error`.
Invalid call-time request models, path UUIDs, and idempotency keys fail **before
dispatch** as sanitized `invalid_request`, with `outcome_unknown: false`.
Validation snapshots the request before the first outbound network await;
subsequent mutation of the caller's model does not change that dispatched body.
Pydantic request-model construction can separately raise `ValidationError`;
that happens outside the SDK call and is not converted to `MemoryClientError`.
Ordinary Pydantic errors may contain private input details; do not log them.
Keys must be **1–256 visible ASCII characters, without trimming**.
Invalid cancellation keys, including `None`, are rejected before network access.

Retain each mutation's exact key and body before dispatch. Network errors, 5xx,
malformed responses, wrong success statuses, or invalid success bodies are
conservatively outcome-unknown for mutations. The SDK does not retry, generate
replacement keys, or infer that no commit occurred. `retryable` is information,
not an automatic retry instruction. Reconcile with the **same key and body**;
current ACL, capture policy, deletion, revision, and replay guards still apply.
Policy denial of a replay does not prove that its original admission never committed.
Python task cancellation propagates rather than becoming `MemoryClientError`: an in-flight
mutation must likewise be treated as unknown and reconciled. Cancellation is
**not rollback** and does not call `cancel_job`.

Returned memory remains evidence, not trusted instructions or guaranteed
current facts. Whole-JSON UTF-8 byte budgeting, explicit incomplete coverage,
current ACL checks, and purge/host/backup/WAL limitations are unchanged.
The historical v0.0.12 stage was **`m2-python-sdk`**; capabilities retain `python_sdk`
metadata (`installation: "sdk-extra"`, `async: true`, `automatic_retry: false`),
not a server endpoint. See the [practical example](../README.md#python-sdk),
[operations](operations/README.md#python-sdk-operations), and
[ADR 0012](adr/0012-python-sdk.md).

## Pgvector exact and hybrid retrieval

**Implemented and verified in v0.0.11/schema 8.**
The lexical default remains unchanged. This adds explicit, provider-independent
vector storage and exact/hybrid ranking, not automatic embedding generation or
qualified semantic retrieval. PostgreSQL remains the only application persistence store.

### Canonical input and explicit upload

Read-only **`POST /v1/embedding-inputs`** accepts the existing Explain body
`{memory_id, revision}`; omitted revision means **1, not latest**.
It needs no `Idempotency-Key` and accepts only currently readable episodes and
assertion revisions, including relation assertions—not entities, jobs, checkpoints,
or tool effects. The returned canonical input is:

```text
{memory_id, revision, type, text, input_digest, input_format: "memory-content-v1"}
```

Episode `text` is its normalized content. Assertion `text` is exactly
`subject / predicate: value`, as `MemoryItem.content`, including a relation
assertion's display value. IDs/times are not inserted into embedding text.
`input_digest` is SHA-256 of UTF-8 `text`; it is not a model-quality measure,
external verification, or proof that the supplied vector was generated from that text.
This is private canonical content: **do not log it or send it to a third party
without explicit approval**. Documentation fixtures are synthetic and invoke no model.

**`POST /v1/embeddings`** requires a caller-owned `Idempotency-Key`:

```text
{
  memory_id, revision: 1, input_digest: <64 lowercase hex characters>,
  model: {
    name: <1–256 characters>, revision: <1–256 characters>,
    dimensions: 768, distance_metric: "cosine", normalization: "l2-f32-v1"
  },
  values: <exactly 768 finite JSON numbers>
}
```

Canonical `revision` defaults to 1; model `revision` is a separate required string.
Identity/scope are derived from the canonical parent, with current **read and write**
authorization. Model metadata is **caller-declared**, not a registry, trusted
origin, provider attestation, or semantic-quality claim. Model spaces separate the
full name/revision pair; equal dimensions never make different spaces compatible.
Dimensions, metric, and normalization are fixed as above.

Server L2 normalization computes in float64, then stores pgvector float32 values.
Reject zero, non-finite, or un-normalizable vectors, booleans, numeric strings,
and any length other than 768. There is no truncation or dimension coercion.
The submitted digest must match the exact currently authorized canonical revision:
otherwise **409 `embedding_input_mismatch`**.

| Situation | Result |
|---|---|
| Same canonical revision/model, same normalized float32 vector and digest | Deduplicate, including across HTTP keys |
| Different vector for the same parent revision/model | **409 `embedding_conflict`**; use a new model revision for replacement |
| Same HTTP key, different validated request | **409 `idempotency_conflict`**; do not change keys to resolve an uncertain response |
| Ninth model version for one canonical revision | **422 `embedding_limit_exceeded`** |
| Existing duplicate at the eight-model limit | Still allowed |

The cap is **8 model versions total per canonical revision**, not eight per model
name. Request hashing preserves the validated values **before L2 normalization**:
rescaling a vector with the same HTTP key conflicts even if its normalized
projection would match. Keep the same key and body after an uncertain response.
A successful upload returns `{memory_id, revision, model, input_digest}`,
with **no independent embedding object ID**. Projection creation, idempotency
receipt, and audit are atomic. Same-key replay checks that both the canonical
parent remains live/readable and the projection still exists.
The **stored idempotency result contains only `{memory_id, revision}`**:
no plaintext input digest, model names, or vectors are retained in that receipt.
The full response model/digest is rebuilt from the currently readable canonical
input and matching projection; request HMACs and opaque anchors persist.
If the parent is live but an administrator removed only the projection, replay
returns **409 `embedding_unavailable`**, not a reconstructed projection.
No endpoint deletes only a projection, and no provider/rebuild/generation runs automatically.

### Recall modes, exact ranking, and coverage

Recall adds `retrieval_mode` (default **`"lexical"`**) and `vector_query`
(default **null**). A vector query contains the same `model` and 768-value format
as an upload and uses the same normalization/validation rules.
`retrieval_mode` is separate from the existing implicit/explicit `mode`; it
selects a retrieval path, not identity or authority.

| Mode | Text `query` | `vector_query` | Ranking |
|---|---|---|---|
| `lexical` | Existing empty browsing/nonempty FTS behavior | Must be null | Existing lexical semantics |
| `vector` | Must be empty | Required | Exact cosine distance |
| `hybrid` | Must be nonempty | Required | Lexical/vector reciprocal-rank fusion |

Nonempty `required_memory_refs` are lexical-only and prepend the
[required prefix](#required-context-recall); the table's lexical ranking then
applies to optional items. Empty/omitted refs preserve all modes unchanged.

All three modes also accept [exact structured filters](#exact-structured-recall-filters).
Ranking and index coverage use the filtered eligible universe, not a top-K post-filter.
Required refs must belong to that universe; `coverage.jobs_pending` remains scope-level.

Do not silently ignore text, select a different model, or fall back to another mode.
Existing scopes, `as_of`, `known_at`, byte budget, item limits, and `search_profile`
rules remain. Historical revision vectors may be populated separately; a vector
for another revision is not a substitute for the revision selected by time.
Past reads never override **current ACLs or deletion**.
Omitted `as_of`/`known_at` are frozen once per recall before selection and coverage;
both paths use those resolved times, so a future boundary cannot split them.
Explicit time values are unchanged.

Vector ranking operates over **`MATERIALIZED` currently authorized and time-eligible
canonical candidates for the requested model**, with those filters applied
**before distance/ranking**. It is exact cosine, not ANN/HNSW, approximate
neighbor expansion, or a tenant/scope-widening search.
Hybrid uses the existing FTS rank and exact vector rank, both deterministic, with
**RRF k=60**:

```text
fusion_score = 1 / (60 + lexical_rank) + 1 / (60 + vector_rank)
```

An absent path contributes zero. A lexical match without a vector may participate
in hybrid results, but missing-vector coverage must remain explicit.
Missing **any eligible visible projection within the structured filters** for the requested model sets
`coverage.vector_incomplete: true`; hidden/ineligible items never contribute to
coverage or counts. Lexical defaults set `vector_incomplete: false`.
Existing Japanese `lexical_incomplete` applies only to the lexical/hybrid path.
`retrieval_complete` is false if either active path is incomplete.
For an empty selection with no candidates, missing active-index coverage yields
`index_incomplete`; a truly empty authorized corpus yields `not_found`.
Without required refs, if no candidates fit, success retains
`empty_reason: "budget_exhausted"`; a required item that cannot fit instead gives
`422 budget_exhausted`. Nonempty results retain null `empty_reason` while still
exposing incomplete coverage.
Never turn missing-index retrieval into an unqualified empty success.

The additive defaults are `MemoryItem.retrieval: null`,
`RecallResult.retrieval_mode: "lexical"`, `embedding_model: null`, and
`coverage.vector_incomplete: false`. Non-null `MemoryItem.retrieval` contains
`method` (`exact_cosine` or `rrf-60`), `lexical_rank`, `vector_rank`,
`vector_distance`, and `fusion_score`. Rank/distance/fusion fields are nullable
when their path/scoring method does not supply them; vector-only fusion score is null.
UUID breaks ties in the actual computed distance/score. This does **not** promise
bitwise-identical arbitrary floating-point results or rankings across all CPUs.
These fields are **additive response/schema changes**: unchanged default lexical
semantics do not promise byte-for-byte identical HTTP JSON or generated MCP schemas.
These are **ranking measurements, not confidence, calibration, or truth**.
The context-pack format and **whole compact JSON UTF-8 byte budget** remain unchanged,
as do reported assertions and null/uncalibrated confidence.
Exact SQL remains subject to the existing **5 s DB statement timeout**, not a
performance SLO. Retrieval quality, performance, and untrusted-vector robustness are unqualified.

### Projection lifecycle, packaging, and adapters

New **`008_pgvector.sql`** requires **`vector` 0.8.6 in `public`**, refuses an
existing extension with the wrong version/schema, and adds per-episode and
per-assertion-revision projections with `ON DELETE CASCADE`, forced RLS, and
runtime **SELECT/INSERT only**. There is **no existing-data embedding backfill**.
API, worker, and `migrate` validate the extension version/schema even when
schema 8 is already recorded; an already-applied migration does not bypass this guard.
Canonical parent purge removes vectors, digests, and declared model names alongside
lexical projections. They are not new provenance vertices or deletion-count objects.
There is no standalone model registry retaining this metadata.
Retained canonical source/idempotency anchors still prevent resurrection.
The Native response-drain boundary and incomplete host/backup/WAL erasure guarantees remain.

The adopted prebuilt DB image is
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`.
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6) is the verified
stable release of **2026-07-29**, official tag commit
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`
([pinned changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)),
under the **PostgreSQL License**. Preserve its upstream license.
Both final native images retain `/usr/share/doc/pgvector/LICENSE`, verified
byte-identical to the pinned upstream license, SHA-256
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`.
Artifact inspection verified both native amd64/arm64 final images: installed
PostgreSQL **18.6-1.pgdg12+2**, native ELF, and `vector.control` **0.8.6**.
**PostgreSQL stays 18.6, but the upstream DB image/base digest changes** from the
old library PostgreSQL image. This is a new pinned DB profile, not an unchanged image.
There is no new DB Dockerfile, source-build, or host-APT procedure in the
implemented profile. An operator-managed PostgreSQL alternative must provide the
same extension version/schema; no such host-install workflow is supplied here.
In historical v0.0.11, Python dependencies remained unchanged apart from project-version metadata:
raw parameter-bound vector casts need no pgvector Python package.
Artifact verification is not application/migration/CI validation or attestation
of any caller-declared embedding model.

MCP still exposes **four tools**. Generated Recall arguments accept the new modes
and inline query vectors, but no embedding-input/upload tool is added.
The fixed-startup hook stays **lexical-only and read-only**; event JSON cannot pass
`retrieval_mode`/`vector_query`. It also rejects a Native response with non-lexical
`retrieval_mode`, non-null `embedding_model`/item `retrieval`, or true
`coverage.vector_incomplete`; it does not silently downgrade unexpected vector output.
Observe, capture, jobs, and workers do not generate
embeddings or call providers. Both MCP protocol eras remain; startup matching is
**service `0.0.26` / API `v1` / schema `11`** for v0.0.26.
Capabilities add `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"`. The v0.0.11 stage was `m2-pgvector-retrieval`;
embedding input returns HTTP 200 and upload/replay returns HTTP 201. Full M0–M3/MVP/production/DR/erasure/
performance/quality gates remain incomplete.
See [ADR 0011](adr/0011-pgvector-retrieval.md),
[schema-8 upgrade](operations/README.md#schema-8-pgvector-upgrade), and
[the synthetic example](operations/README.md#synthetic-vector-example).

## Atomic structured capture

Schema 11 checks [scope capture policy](#scope-capture-policy) before idempotency
or source deduplication, including former exact replays. All retained admission/
replay outcomes below are subject to current authorization and policy approval.

For 1–16 proposals, use the separate [batch route](#explicit-batch-capture);
the single-job contract below remains unchanged.

**Retained v0.0.10 contract, also verified in v0.0.11.** `POST /v1/captures` requires
`Idempotency-Key` and one body:

```text
{episode: <unchanged Observe>,
 memory: {subject, predicate, value, evidence_quote, explicit_intent: true,
          valid_from: <aware timestamp or null>, valid_to: <aware timestamp or null>}}
```

The request model is `Capture(episode: Observe, memory: CapturedMemory)`.
The episode is the existing `Observe` model, not an alternate capture format.
Exactly one structured memory intent is accepted, not a list or free-text
extraction request. Memory fields retain the synchronous Remember rules:

| Memory field | Contract |
|---|---|
| `subject` | 1–256 characters |
| `predicate` | Matches `^[a-z][a-z0-9_]{0,63}$` |
| `value` | 1–65,536 characters |
| `evidence_quote` | One 1–4,096-character literal substring of the normalized captured episode |
| `explicit_intent` | Must be `true` |
| `valid_from`, `valid_to` | Optional timezone-aware timestamps or null/unbounded; when both are present, start must be before end |

Memory accepts **no scope, evidence IDs, or identity fields**. The transaction
derives scope from the episode and binds its single evidence reference to that
episode's memory ID/revision 1. Current Native authentication, tenant/scope
read/write authorization, RLS, normalization, and source-event deduplication
remain authoritative; nothing silently widens scope or tenant. Literal matching
establishes provenance, **not semantic support or truth**. Publication retains
`epistemic_status: "reported"` and uncalibrated confidence (`score: null`,
`method: "uncalibrated"`).

### Commit and publication are separate

**HTTP 201** returns `CaptureResult`:
`{memory_id: <episode UUID>, revision: 1, synthesis_job_id: <job UUID>}`.
`memory_id` is **not an assertion ID**, and `synthesis_job_id` is a job reference,
not a promise that synthesis occurred. The episode plus at most one
`structured_remember` / `structured-remember-v1` job are committed atomically.
Capture may reuse an existing job, including a terminal one. **201 does not
guarantee a newly created or pending job**; GET is authoritative for current status.
Use `GET /v1/jobs/{synthesis_job_id}` for fresh state and the existing
fixed-subject worker for eventual assertion publication.

The existing **100 pending/running jobs per scope**, **five attempts per job**,
leases, access/deletion epochs, and publication fencing remain unchanged.
Worker publication remains a separate atomic transaction; enqueue does not set
the assertion's server-recorded publication time.
`POST /v1/observe` retains unchanged `ObserveResult`, including literal-null `synthesis_job_id`,
and **never automatically enqueues a job**. Explicit `POST /v1/jobs`, synchronous `/v1/remember`,
and pure Observe/Remember normalized serialization/HMAC remain byte-compatible.

### Deduplication and transaction boundary

| Request situation | Result under current access/deletion checks |
|---|---|
| Same capture key and normalized body | Same episode/job pair |
| Changed body with the same capture key | `409`, no new partial writes |
| Different HTTP keys, same episode/intent/principal | Both IDs deduplicate to the same pair |
| Previously observed identical event | Reuse its episode; the explicit wrapper supplies the job intent |
| New key and different explicit memory intent | May create a separate job using the same retained episode; intentional, not semantic deduplication |
| Another authorized principal using the same event | Episode source deduplication remains; job identity/worker ownership are independent |
| Same source-event identity, changed episode body | `409`, no new partial writes |

One transaction covers episode and lexical projection, job input/control/identity,
idempotency receipts (including the outer capture receipt), and audit.
Transaction failure after either write or the outer receipt rolls back **all new changes**.
An episode that existed independently before capture remains on failure; no
partial new job survives. There is no distributed transaction or external provider.

Retained opaque idempotency/source/job identity anchors include internal
composition keys, derived by server HMAC from the caller's key. They are not
client-supplied fields, autogenerated MCP caller keys, or a change to caller-owned
HTTP key reuse. Retained anchors are not fresh memory content or full-erasure proof.

### Replay, deletion, and failed-job retry

**Current ACLs and deletion override replay for both returned IDs**, including
an exact capture replay after API process restart. The pair is historical;
GET checks fresh job state rather than capture silently changing the pair.
Shared replay checks a non-null `synthesis_job_id` as well as `memory_id`;
the unchanged Observe result still has no job reference.

| Purge target | Effect on capture |
|---|---|
| Episode | Closes dependent jobs and assertion descendants through existing purge dependencies |
| Job alone | Leaves the episode and independently stored published output; old capture replay and a new key for the same intent return `404`, not a recreated job identity |
| Published result assertion | Removes the dependent job, keeps its source episode, and invalidates the old pair |

A **new explicit different intent on a retained source** is still allowed by
existing job semantics. Capture is not a permanent whole-source seal.
Failed jobs use existing `POST /v1/jobs/{job_id}/retry` with the original complete
`EnqueueJob` intent and a caller-owned key. Retry creates/reuses a child under
existing rules; replaying capture returns the **original failed job reference**,
not that child, even after the child has been created. See [durable jobs](#durable-jobs).

### Scope and capabilities

The historical v0.0.10 API stage was **`m2-atomic-capture`**. Retained capability feature
**`atomic_structured_capture`**. The exact `atomic_capture` metadata fragment is:

```json
{
  "atomic_capture": {
    "endpoint": "/v1/captures",
    "max_jobs": 1,
    "recipe_version": "structured-remember-v1",
    "automatic_capture": false
  }
}
```

This describes at most one job per explicit capture, not automatic capture.
The stage label does not complete M2 or any other acceptance gate.

Capture is a **Native resource also covered by the SDK**, not a fifth MCP tool. Recall-hook stays read-only;
neither adapter automatically captures. MCP/hook startup requires exact
**service `0.0.26` / API `v1` / schema `11`**. The retained schema-8 migration is separate
from the retained capture semantics. Capture does not generate embeddings,
invoke LLM/providers, extract intent, perform natural-language/automatic synthesis,
or establish semantic quality.
The tenant HTTP response-drain barrier is unchanged: no atomic host-context
delivery, retraction, or host/backup/WAL/full-erasure guarantee follows.
See [ADR 0010](adr/0010-atomic-capture.md) and the
[operator example](operations/README.md#atomic-structured-capture-operations).

## Local stdio MCP

Owned-job query is Native/SDK-only; no job tool joins the four MCP tools.

The new checkpoint-head resource is Native/SDK-only; this adapter still exposes
four tools and no checkpoint tool.

`memory_recall` accepts typed `filters` under the [structured-filter contract](#exact-structured-recall-filters).
It adds no tool or safe error code and cannot bypass required-reference eligibility.

v0.0.8 introduced `pg-agmemory mcp`, a **stdio-only, trusted local Native API
client**, not a second persistence or authorization service. Optional
`pg-agmemory[mcp]` pins official `mcp==2.2.0` and `httpx==0.28.1`; repository
v0.0.26 Docker test/runtime stages retain `mcp`, `hook`, and `sdk` extras.
The extracted shared bounded Native HTTP client must retain all MCP invariants
below. Historical v0.0.9 checks passed locally and on both native Docker
architectures. Historical v0.0.10/v0.0.11 and final local/native v0.0.12 checks passed.
Shared `NativeSettings` additionally parses origins with `httpx.URL`, rejecting
control characters and invalid IDNA before transport. No remote MCP HTTP/SSE listener,
OAuth, delegated caller identity, semantic cache, or response cache is provided.

### Tools and Native semantics

Exactly four tools derive input/output JSON Schemas from the Native Pydantic
models rather than maintaining a separate handwritten request contract:

| Tool | Input wrapper | Native route / success status |
|---|---|---|
| `memory_recall` | `{request: <Recall>}` | `POST /v1/recall` / `200` |
| `memory_remember` | `{request: <Remember>, idempotency_key: "..."}` | `POST /v1/remember` / `201` |
| `memory_explain` | `{request: <Explain>}` | `POST /v1/explain` / `200` |
| `memory_forget` | `{request: <Forget>, idempotency_key: "..."}` | `POST /v1/forget` / preview and purge both `202` (unchanged) |

`request` contains the existing Native body, not a new natural-language format.
For `memory_recall`, this includes `required_memory_refs` under the
[required-context contract](#required-context-recall). A required-prefix budget
failure is a tool error with safe `budget_exhausted`, not a successful empty pack.
Remember still requires `explicit_intent: true`, structured fields, and literal
same-scope episode evidence. Episode capture remains Native `observe`; it is
not an MCP tool. Jobs, graph, revisions, checkpoint/effect execution, and deletion
receipt lookup are not additional MCP tools.
Recall retains current scopes/ACLs, temporal selection, evidence/coverage,
`search_profile: "simple-v1"` by default, and explicit
`"ja-janome-0.5.0-v1"` opt-in. `tokenizer_id: "utf8-bytes-v1"` and the Native
`token_budget` field still mean **UTF-8 bytes, not model tokens**.
Explain's omitted revision remains **1, not latest**. Memory text is untrusted
evidence, not instructions or verified current external truth.

Both mutation wrappers require `idempotency_key`: **1–256 visible ASCII
characters (`0x21`–`0x7e`, no whitespace)**, including forget preview.
Whitespace is rejected, not trimmed; the key is not rewritten. Exactly 256
characters is accepted and 257 is rejected. The adapter forwards it as Native
`Idempotency-Key`. The caller must retain and
reuse the **same key plus the same body after uncertainty, across stdio restart
and token refresh**. No autogenerated keys, automatic retries, or adapter
durable retry store exist. A changed body with the same key can conflict; a new
key is not uncertainty recovery. Native idempotency references are historical,
not fresh reads: current authorization and deletion override replay. A replay
must not resurrect purged data. MCP session/request IDs are neither durable
memory run IDs nor HTTP idempotency keys.

### Results, failures, and bounded transport

Successful tool calls return
`structuredContent: {result: <validated Native result>, error: null}`.
Tool failures return `isError: true` and
`structuredContent: {result: null, error: {code, retryable, outcome_unknown,
native_status, request_id}}`. `native_status` and the validated Native UUID
`request_id` are nullable; the latter is not an MCP request ID.
Short text accompanies the structured result but does not duplicate evidence or
echo raw request/response bodies, URLs, or credentials. Native errors use safe
codes; invalid responses are not passed through as evidence.

Transport failure/timeout, Native 5xx, and invalid or unexpected responses on a
mutation are conservatively **`outcome_unknown: true`**. An API commit may already
have happened; never describe these as rolled back. `retryable` is only a hint,
not an automatic retry, rollback proof, or permission to change the key/body.
Local validation failures occur before HTTP submission. An interrupted stdio
session can lose the acknowledgement even if the Native API completed the write.

Each HTTP exchange is bounded by **20 seconds total**, **10 seconds I/O**,
**5 seconds connect**, and a pool of **4 connections**. The serialized Native
request body is limited to **256 KiB** and the received HTTP response to **2 MiB**.
These are HTTP bounds, not a model-token budget or a claim that every host/stdio
buffer has the same cap. Redirects and proxy environment settings are disabled;
TLS verification stays enabled. There is no semantic/response caching.

### Fixed identity and deletion boundary

Only trusted startup configuration supplies `PGAG_MCP_API_URL` and
`PGAG_MCP_API_TOKEN`. The URL must be an HTTPS origin or loopback HTTP origin:
no URL credentials, application path, query, or fragment (a root `/` is accepted).
The bearer token targets the **Native API audience**; the Native API verifies
issuer/audience/signature/time and resolves its subject. It is not an
MCP-caller token-forwarding or identity-delegation mechanism. Tool arguments
cannot override URL, headers, token, or identity. `--subject` and `--once` are
rejected for `mcp`; do not confuse it with the fixed-subject database worker.

Before serving stdio, authenticated `GET /v1/capabilities` must report
`api_version: "v1"`, `service_version: "0.0.26"`, and `schema_version: 11`.
Configuration, authentication, and version errors terminate nonzero with
sanitized diagnostics. v0.0.26 requires schema 11; the adapter itself performs no migration.
Restart the adapter to refresh its fixed token; there is no refresh grant.
Startup validation does not cache authorization: Native authentication,
current ACLs, and deletion checks run on every call.

Run **one adapter per trusted identity** and do not share its stdio connection
with other trust domains or wrap it in a network service. Anyone controlling
that local host can exercise the configured Native identity's permissions.
The Native response-drain barrier ends with HTTP delivery to the adapter,
**not an atomic delivery barrier through stdio, the host UI, or the LLM**.
Even without an adapter response cache, in-flight buffers and already-delivered
context cannot be retracted. The host must discard cached context after forget
or ACL changes; no MCP deletion notification implements this for it.
Active-store purge is not full erasure of host context, WAL, replicas, or backups.

### Protocol evidence boundary

The official [Python SDK v2.2.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0)
was published **2026-09-07**. Upstream
[protocol documentation](https://py.sdk.modelcontextprotocol.io/protocol-versions/)
describes `server/discover` for `2026-07-28` and legacy `initialize` through
`2025-11-25`. These are SDK facts, **not adapter/client qualification results**.
Historical v0.0.8 targeted checks exercised actual stdio SDK `Client` connections and raw
JSON fixtures for both modes:

- **Modern `2026-07-28`:** `Client(mode="auto")` uses `server/discover`.
  Raw requests carry per-request `params._meta` entries
  `io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientInfo`,
  and `io.modelcontextprotocol/clientCapabilities`; the version value is
  `"2026-07-28"`. This is not the legacy initialization handshake.
- **Legacy `2025-11-25`:** `Client(mode="legacy")` and the raw fixture send
  `initialize` with `protocolVersion`, `clientInfo`, and `capabilities`, followed
  by `notifications/initialized` before tool calls.

The runtime smoke launches the actual `pg-agmemory mcp` child in the non-root
production image, uses a fixed token and provisioned scope against its loopback
Native API, and verifies the four-tool list and recall in **both modes**.
Regression coverage includes real HTTP response loss **after remember commits**,
followed by same-key/body retry and an assertion that only one assertion exists.
There is no automatic retry. Exact 256/257-character key boundaries and rejection
without whitespace trimming are also covered.

Historical v0.0.8/v0.0.9/v0.0.10 local/native CI results are recorded below.
v0.0.11 retains and passes both protocol eras. These exercised paths do not qualify
untested older clients, named host applications, or every protocol version.
See [ADR 0008](adr/0008-local-mcp.md) and
[operations](operations/README.md#local-stdio-mcp-operations).

## Implicit recall hook

Owned-job query adds no hook field or job operation.

Checkpoint-head lookup adds no hook field or checkpoint operation.

Hook input rejects `filters` as an unknown field. Its internal `Recall.filters`
defaults to `None`, without a per-event override of trusted startup settings.

Unlike Native/SDK/MCP recall, hook input rejects `required_memory_refs`.
Its constructed `Recall` retains `[]`: no host pinning is added.
Its `200` / `empty_reason: "budget_exhausted"` remains an optional-only success,
distinct from required-context Native `422 budget_exhausted`.

**Retained read-only, lexical-only contract, verified in v0.0.11.**
`pg-agmemory recall-hook` is an optional, vendor-neutral **harness-side** local
Native HTTP client. There is no automatic registration into a host and no
Copilot, Claude, or Codex integration claim. The host chooses when to invoke it;
the service does not observe host lifecycle events itself. `pg-agmemory[hook]`
pins **httpx==0.28.1, not the MCP SDK**. Both Docker test/runtime stages include
`mcp`, `hook`, and `sdk`; historical v0.0.9 core-only/hook-only isolation checks passed
locally and on both native Docker architectures.
The hook needs no database credentials, signing key, or LLM/provider key.

### Input and trusted startup configuration

One invocation consumes **one UTF-8 JSON document on stdin followed by EOF**,
with a **32,768-byte** input cap. Invalid UTF-8, malformed JSON, oversized input,
and input validation failures are explicit errors, not ignored events:

```json
{"event":"session_start","query":""}
```

Only these two fields are accepted:

| Field | Contract |
|---|---|
| `event` | Exactly `session_start`, `task_switch`, or `after_compaction` |
| `query` | Required string, 0–4,096 Unicode characters; empty browses canonical accessible items under configured scopes and current-time limits |

Retrieval intent comes only from the JSON `query`; `event` is a lifecycle label,
not alternate query text or a natural-language instruction channel.

**All extra fields are forbidden**, including identity, `scope_ids`, `purpose`,
`mode`, budget, URLs, headers, tools, and times. Event/query text cannot authorize
access or configure transport. `--subject` and `--once` are rejected.

Only the **trusted startup environment** supplies routing, authentication, and
recall settings. Do not construct that environment from untrusted prompts,
queries, tool output, or retrieved memory. Never log queries or copy them into
errors. The fixed token is for the **Native API audience**, not a host/vendor
audience, and cannot be overridden by the event.

| Environment variable | Default / constraint |
|---|---|
| `PGAG_HOOK_API_URL` | Required; no default. Trusted HTTPS origin or loopback HTTP origin; no userinfo, application path, query, or fragment. Root `/` is accepted; absent URL gives `invalid_hook_configuration` |
| `PGAG_HOOK_API_TOKEN` | Required fixed Native-audience bearer token, securely supplied by the operator |
| `PGAG_HOOK_SCOPE_IDS` | Required JSON array of 1–32 unique UUIDs; requested scopes narrow existing permissions, never grant them |
| `PGAG_HOOK_PURPOSE` | `implicit_context`; 1–256 characters |
| `PGAG_HOOK_TOKEN_BUDGET` | `2000`; integer 64–2,000 **UTF-8 bytes, not model tokens** |
| `PGAG_HOOK_MAX_ITEMS` | `20`; integer 1–20 |
| `PGAG_HOOK_SEARCH_PROFILE` | `simple-v1`; explicit `ja-janome-0.5.0-v1` opt-in only |
| `PGAG_HOOK_TIMEOUT_SECONDS` | `2.0`; finite number 0.1–20 seconds |

URL, token, and scope IDs are **all required**. Shared `NativeSettings` uses
`httpx.URL` as well as the origin restrictions, rejecting control characters
and invalid IDNA before transport. These invalid-origin cases are covered by
the historical v0.0.9 local and both native CI suites.

Every invocation makes a fresh authenticated `GET /v1/capabilities`, requires
exact **service `0.0.26` / API `v1` / schema `11`**, then sends `POST /v1/recall`
with `mode: "implicit"`, configured scopes/settings, and Native current-time
defaults (no event-supplied historical times). Both calls use the same fixed
token. Current Native authentication, ACLs, time selection, deletion, evidence,
and coverage remain authoritative. A capabilities probe is not cached
authorization. A replacement token is supplied at trusted startup of the next invocation.
Recall scopes **silently narrow** to current authorization. An unauthorized
scope or revoked membership is filtered out: recall returns the authorized
subset, or no items/`not_found`, rather than a scope-existence `404`.
Token authentication failures are different: Native returns explicit `401`,
and the hook returns an error with exit `1`. This preserves Native behavior;
a successful empty recall neither grants access nor proves a scope exists.

### Bounds, results, and failure handling

The network deadline covers **capabilities plus recall together**, default 2.0 s
(finite 0.1–20 s), not a separate full allowance for each request.
It excludes process/interpreter startup, stdin input/waiting, and output, and is **not an LLM
latency SLO or performance qualification**. The harness must close stdin and
set a separate subprocess timeout. The extracted shared Native HTTP client
keeps **256 KiB serialized request / 2 MiB HTTP response** caps, disables
redirects and proxy environment settings, and keeps TLS verification enabled.
These HTTP limits do not cap every host buffer. The serialized context pack
must also fit the configured byte budget; the full RecallResult is not itself
limited to that smaller context budget.
`context_pack.byte_count` is the UTF-8 byte length of the **entire compact
JSON-serialized context pack**, with `ensure_ascii=False` and
`separators=(",", ":")`, including all metadata/citations—not just its text.
The hook verifies this same serialization count against reported `byte_count`
and the configured budget. It also checks that returned item count is at most
configured `max_items` and that the returned search profile matches
configuration. Inconsistent responses fail validation; there is no broad fallback.

Even an empty pack costs **roughly 192 bytes**; this is not a new fixed minimum
configuration value. The accepted budget range still starts at 64. If pack
metadata cannot fit, Native returns **422 `budget_too_small`**, and the hook
returns an explicit error envelope with **exit 1 / `result: null`**, never empty
success. If the pack fits but a candidate cannot, Native can return **200**
with `empty_reason: "budget_exhausted"` and hook **exit 0**.
Missing lexical projections with no candidates instead produce **200 / exit 0**
with `empty_reason: "index_incomplete"`, `coverage.lexical_incomplete: true`,
and `coverage.retrieval_complete: false`. These outcomes preserve Native
semantics; successful HTTP retrieval does not imply complete coverage.

Validated hook runtime outcomes produce exactly one JSON result and a trailing
newline on stdout. Diagnostics
use sanitized stderr, never raw queries, bodies, URLs, headers, or credentials.
The following describes the envelope shape, not literal JSON placeholder values:

```text
{status: "ok", event: <event>, result: <full Native RecallResult>, error: null}
{status: "error", event: <validated event or null>, result: null,
 error: {code, retryable, outcome_unknown: false,
         native_status: <integer or null>, request_id: <UUID or null>}}
```

`request_id` is a validated Native request UUID, not a host event ID.
`retryable` is only a hint: there is no automatic retry. Because the hook only reads,
`outcome_unknown` is always `false`; this does not change MCP mutation semantics.

| Exit | Meaning |
|---|---|
| `0` | Valid Native success, including `empty_reason` of `not_found`, `budget_exhausted`, or `index_incomplete` |
| `2` | Invalid configuration or input, including invalid UTF-8/JSON and oversized stdin |
| `1` | Native, network, version, or protocol failure |

Confirmed runtime error codes:

| Code | Exit | Meaning |
|---|---|---|
| `invalid_hook_configuration` | `2` | Invalid trusted startup configuration |
| `invalid_hook_input` | `2` | Invalid UTF-8/JSON or event/query validation failure |
| `hook_input_too_large` | `2` | Stdin exceeds the byte limit |
| `hook_input_unavailable` | `2` | Stdin cannot be read |
| `hook_deadline_exceeded` | `1` | Combined network deadline exceeded; `retryable: true` |
| `native_api_unavailable` | `1` | Native API transport unavailable; `retryable: true` |
| `native_version_mismatch` | `1` | Capabilities version mismatch |
| `invalid_native_response` | `1` | Invalid Native protocol/response |
| `budget_too_small` | `1` | Mapped Native `422`: even the context pack metadata does not fit |
| Mapped sanitized Native codes | `1` | Native API error, without forwarding raw details |

**Invocation errors are an exception to the JSON envelope contract:** rejected
CLI flags (including `--subject`/`--once`) and a missing `hook` extra use argparse
stderr and **exit 2 without a JSON envelope**. All validated hook runtime errors,
including configuration/input failures above, have the error envelope.
The harness must also handle non-JSON/invalid envelopes and subprocess timeouts;
never echo raw stderr or response/exception content in host logs.

**There is no result on error; failed retrieval is never mapped to empty success.**
A genuine empty result can still have incomplete coverage. The host must inspect
exit code **and** structured status, surface errors/coverage, and explicitly
decide whether to pause or continue without memory. Do not reuse old context to
hide failure. A host-killed/timed-out process may not produce an envelope; that
is a host-observed failure, not an empty result.

There are **no writes, capture, queue submission, LLM/provider calls, caches,
retries, or idempotency keys** in this hook. An event label does not imply
checkpoint creation, compaction, tool dispatch, synthesis, or permission expansion.
Keep memory separate as **untrusted evidence**, never host instructions or policy.

### Deletion and qualification boundary

The Native tenant session advisory response-drain barrier ends at HTTP delivery
to the **trusted local hook**. Hook buffers, stdout/pipe buffers, and host context
are **not atomically covered**. There is no retraction or deletion notification.
After forget or ACL changes, the host must discard previous context and perform
a fresh hook invocation under current authorization. No host-erasure proof,
backup/WAL/replica erasure, or lifecycle-wide access guarantee follows.

Engineering tests cannot qualify a specific vendor integration, semantic quality,
or performance. Full M0–M3/MVP/production/performance/quality/DR/full-erasure
gates remain incomplete. See [ADR 0009](adr/0009-implicit-recall-hook.md) and the
[executable vendor-neutral harness example](operations/README.md#vendor-neutral-python-harness-example).

## Identity and authorization

- A static PEM RSA public key of at least 2048 bits verifies RS256 signatures.
  The configured issuer and audience, required `sub`, `iss`, `aud`, `iat`, and
  `exp` claims, and token time validity are checked.
- The verified external subject is mapped to a principal and tenant in
  PostgreSQL. The deployment has one configured issuer; the subject mapping
  is not a caller-selected tenant. Unknown subjects are unauthenticated.
- Request bodies cannot supply tenant/principal identity. Requested scopes
  narrow access; service checks and RLS enforce membership and permissions.
  Hidden or deleted object lookups return `404` without an existence distinction;
  recall scope filtering instead silently returns only authorized items, as above.
- Runtime credentials must not be superuser, bypass RLS, or own application
  tables, including through owner-role membership. Admin migration/provisioning/rebuild
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
API and worker share `principal_connection` identity lookup and `bind_identity`
revalidation, and acquire the **same tenant session lock**. API response draining
still extends past commit; worker claim/publication transactions are short, and
payload preparation happens outside them. The worker's configured subject is a
trusted deployment identity, not a public impersonation interface.

Use the privileged **scope-access CLI** for membership administration.
It acquires the **same session lock**, compares the tenant epoch, atomically
updates changed membership/epoch/audit, and holds the lock through commit and
CLI JSON stdout flush before closing the connection. Changes outside this
protocol are not covered by the request/drain race guarantee. See the
[operations procedure](operations/README.md#membership-maintenance-and-request-drain).

## Evidence, consent, and time

`remember` requires `explicit_intent: true` and 1–32 distinct episode evidence
IDs. Each quote must occur literally in its episode's content; both objects
must be in the same scope. An assertion cannot serve as another assertion's
source in this slice. Free-text subjects/values are not resolved to entity IDs;
only explicit entity/relation endpoints create typed graph data.

Literal quote validation establishes provenance, **not semantic support or
truth**. The service does not infer that a quote proves the supplied assertion.
Memory is `reported`, with confidence `score: null` and `method: "uncalibrated"`;
retrieved items require source refresh before asserting current external facts.
Retrieved content is evidence, not trusted instructions.

`consent_reference` records the caller's consent assertion. Scope capture policy
may allowlist that label, but does not verify a consent registry or automatically
redact secrets/PII. There is no general-purpose consent or egress policy engine.
Callers must supply only approved, already-sanitized data.

### Assertion revision contract

For bounded, read-only revision discovery, see
[assertion metadata history](#assertion-metadata-history).
It does not change the exact-revision Explain default or the mutation CAS below.

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
Typed relations require their dedicated revision endpoint; generic correction
returns `409 relation_revision_required`.

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

For exact `known_at` revision-boundary checks, use the server-returned assertion
`recorded_at`, not a host/VM wall-clock sample.

## Retrieval and budgets

Recall defaults to `retrieval_mode: "lexical"` and `search_profile: "simple-v1"`, preserving PostgreSQL's `simple`
configuration, `plainto_tsquery`, and `ts_rank_cd`. The optional Japanese profile
below adds segmentation, not BM25 or vectors; vector/hybrid modes are separate.
Responses echo `search_profile`; unsupported profiles return `422`. In lexical mode an empty `query` is
allowed and browses canonical accessible items under scope/time constraints,
subject to item and byte limits, even if lexical projections are incomplete.
Entities themselves are excluded from recall/explain. Relation assertions remain
FTS candidates; recall never automatically expands the graph and retains
`graph_used: false`. Items and assertion explanations include nullable
`relation: {source_entity, target_entity}` for the exact returned revision.
Relation context text includes both entity UUIDs within the same byte budget.
`coverage.jobs_pending` reports currently readable pending/running jobs in the
requested scopes, not query relevance, historical queue state, or completed
synthesis. `synthesis_pending: false` and `graph_used: false` remain unchanged.
Jobs themselves are excluded from recall/explain and checkpoint/effect references.

Nonempty [required references](#required-context-recall) prepend eligible exact
items in request order, bypassing only lexical matching/ranking, not scope/time/ACLs.
The ordinary lexical ordering applies to optional items after that prefix.

Despite the request field name `token_budget`, `utf8-bytes-v1` budgets the
entire compact JSON-serialized context pack in **UTF-8 bytes**, including its
metadata and citations, with `ensure_ascii=False` and `separators=(",", ":")`.
The response declares `budget_unit: "utf8_bytes"`, `token_count: null`, and
`exact_token_count: false`. This is a conservative fallback, not an exact model
tokenizer or a size limit for the entire HTTP response. This context
`tokenizer_id` is unrelated to Japanese search segmentation.
Only optional items may be omitted whole. A required item that cannot fit gives
`422 budget_exhausted`, with no partial context. Without required refs, if even
pack metadata will not fit, the request retains `422 budget_too_small`,
not a successful empty result.

Limits include a 1 MiB body for checkpoint creation and 256 KiB for other
endpoints, 100 returned recall items at most, and budget
values of 64–8,000 (implicit mode at most 2,000). `coverage.truncated` signals
optional item/budget omissions. A successful empty selection without required
refs uses `empty_reason` of `not_found`, `budget_exhausted`, or `index_incomplete`
as defined below;
`retrieval_complete` does not mean complete knowledge of the world. Native implicit
mode remains a request option. The retained [hook](#implicit-recall-hook) invokes it
only when a trusted harness launches the command; no host is automatically registered.

### Japanese lexical profile

Select `search_profile: "ja-janome-0.5.0-v1"` explicitly. Exact dependency
**Janome 0.5.0** uses bundled **mecab-ipadic-2.7.0-20070801**, including Janome
additions. Only matching Japanese-script runs in source and query text undergo
surface/wakati segmentation; ASCII identifiers and English pass through the
segmenter unchanged before PostgreSQL lexical processing. There is no
Unicode/width normalization, lemma/stemming, synonym expansion, or claim of
segmentation/recall quality. Han-script ranges also affect Chinese characters;
Chinese recall is not qualified. No external model/provider is called.
Preserving Latin text does not turn `simple-v1` into substring search: an
embedded `Gold` without a token boundary need not match standalone `Gold`.

Janome is lazy-imported only when a Japanese-script run requires segmentation;
API import and English-only segmentation do not load it. The matcher's
input-prefix cache is disabled with `max_cached_word_len=0`; only packaged
dictionary-resource caches are retained, not source text or token streams.
Both test and runtime container builds sequentially precompile only static
Janome package bytecode, including dictionary modules. This is code preparation,
not a memory index/cache. A fresh Linux subprocess regression guard requires
no Janome import for English-only operations and initialization peak RSS
**below 256 MiB**. It is not a deployed memory limit, a bound on request/backfill
memory use, or release qualification on its own; cold uncompiled host installations and
deployment resource sizing remain unqualified.

`memory.episode_lexical` and `memory.assertion_lexical` store derived `tsvector`
payloads for this profile. Episode rows represent revision 1; assertion rows
identify the exact revision. Forced RLS and same-scope canonical foreign keys
apply, with `ON DELETE CASCADE`. Runtime grants are `SELECT`/`INSERT` only, with
no `UPDATE` or direct `DELETE`; canonical parent purge cascades without child
DELETE grants. Episode content and
assertion subject/predicate/exact-revision value are segmented, then indexed
with `to_tsvector('simple', ...)`. The query uses segmented text with
`plainto_tsquery('simple', ...)` and `ts_rank_cd`; the simple profile still uses
the existing canonical vectors. Entities and jobs do not become recall items.

Observe and all assertion publication/revision paths write projections in the
same transaction, including typed relations and durable-job publication.
Canonical IDs, timestamps, evidence, synchronous normalized JSON/HMAC, and
historical revision selection are unchanged. Migration backfills all retained
episodes and **all assertion revisions**, not only current heads, and skips
tombstones. Offline administrative rebuild uses the same canonical sources.
Japanese episode content retains the **65,536-character** limit; **65,537**
characters are rejected rather than truncated. The separate 256 KiB HTTP body
cap still applies, including JSON encoding overhead.

For the Japanese profile, any missing projection among currently authorized,
requested-scope, time-eligible canonical candidates matching structured filters sets
`coverage.lexical_incomplete: true` and `coverage.retrieval_complete: false`.
This check is independent of query relevance and the item limit. There is **no
silent fallback** to simple search or automatic repair worker. Available matches
may still be returned with the incomplete flag; in lexical mode an empty query still browses
canonical items. Eligible required refs also use canonical items despite missing
projections, without repairing them. No query/required candidates plus missing
projections gives `empty_reason: "index_incomplete"`. If no optional-only
candidates fit, success retains `"budget_exhausted"`; required items
that cannot fit instead give `422 budget_exhausted`. A nonempty result has null `empty_reason`.
Without missing projections, `lexical_incomplete` is false and ordinary
`not_found`/budget rules apply. Projection coverage is not query relevance,
queue state, or knowledge/quality completeness; `jobs_pending` remains separate.
Janome corrupt-dictionary diagnostics are sanitized to `japanese_dictionary_error`
without input text. Library `SystemExit` becomes tokenizer-unavailable:
API `503 dependency_unavailable`, not an incomplete-index success; workers use
the existing bounded `dependency_unavailable` retry path without input echo.

Capabilities retain feature `japanese_fts`, both
`search_profiles`, `default_search_profile: "simple-v1"`, and pinned tokenizer/
dictionary metadata with `normalization: "none"` and
`segmentation: "japanese-script-runs"`. Context budgeting stays `utf8-bytes-v1`;
`auto_synthesis` and recall `graph_used` remain false. The v0.0.11 vector/hybrid
foundation adds `retrieval_modes: ["lexical", "vector", "hybrid"]` and
`default_retrieval_mode: "lexical"` without changing lexical defaults.
Historical stages `m2-japanese-fts` and `m2-atomic-capture`
never represented full M2 acceptance. See
[ADR 0007](adr/0007-japanese-fts.md),
[offline rebuild](operations/README.md#lexical-profile-and-reindex-operations),
and [dependency licensing](../README.md#dependency-licensing).

## Durable jobs

### Explicit structured publication

`POST /v1/jobs` requires `Idempotency-Key` and
`{kind: "structured_remember", memory: <Remember request>}`. The only recipe is
`structured-remember-v1`. `memory` has the unchanged synchronous `Remember`
contract: scope, subject, predicate, value, 1–32 distinct readable same-scope
episode IDs with literal quotes of 1–4,096 characters, `explicit_intent: true`,
and optional aware valid bounds. Enqueue requires current scope read/write
access. This is asynchronous **structured publication**, not automatic synthesis,
natural-language extraction, an LLM/provider call, embedding, or compaction.
`observe` still enqueues nothing and returns `synthesis_job_id: null`.
Synchronous `remember`, including legacy normalized JSON/HMAC, is unchanged.

`202` returns `{job_id, kind: "structured_remember",
recipe_version: "structured-remember-v1"}`. It acknowledges a committed job
reference, not a published assertion. Canonical intent plus recipe deduplicates
within the same tenant/principal/scope, including across HTTP keys; evidence
order is canonicalized for job identity. Same HTTP key still requires the same
normalized request (`409 idempotency_conflict` on change). Another principal
can submit its own job; different source identities are not semantically deduped.
There are at most **100 pending/running jobs per scope** (`422 job_limit_exceeded`)
and **5 attempts per job**. Cancelled jobs are excluded from the active-job cap
and recall's `jobs_pending`; same-intent enqueue/capture still dedups to them.
Capabilities advertise `durable_jobs`, `job_kinds: ["structured_remember"]`,
`auto_synthesis: false`, and the 100-job/5-attempt/30-second lease limits.
These bounded jobs are not full M2 acceptance.

`GET /v1/jobs/{job_id}` requires current read access. Same-scope readers may read
another principal's job but cannot claim, publish, retry, or cancel it.
[Owned-job query](#owned-job-query-and-pagination) is narrower: it discovers only
the current caller's jobs, even with scope-admin permission. It returns `JobPage`
of `ListedJob` (full `JobDetail` plus `scope_id`), without changing GET's shape.
The GET response includes:

| Field | Contract |
|---|---|
| `job_id`, `kind`, `recipe_version`, `retry_of` | Opaque job identity, fixed kind/recipe, and nullable retry parent |
| `state` | `pending`, `running`, `succeeded`, `failed`, or `cancelled` |
| `attempt`, `max_attempts` | Attempts already claimed; maximum is 5 |
| `available_at`, `lease_until`, `created_at`, `updated_at` | Scheduling/lease and server timestamps; lease is null outside running |
| `error_code` | Null or `dependency_unavailable`, `stale_context`, `invalid_input`, `attempt_limit` |
| `input_refs` | Exact immutable episode revision-1 references |
| `result` | Null or the original assertion `{memory_id, revision: 1}`, even after later corrections |

GET never exposes request payload, lease token, or owner principal. Succeeded,
failed, and cancelled terminal jobs erase request JSON; input ID references remain
until purge. Terminal records are immutable.
See [explicit cancellation](#explicit-job-cancellation) for state/attempt CAS,
publication races, replay, and the distinction from `forget`.

### Explicit retry of terminal failure

`POST /v1/jobs/{job_id}/retry` requires `Idempotency-Key` and the full original
`EnqueueJob` body. The owner must reprovide it because the failed payload is no
longer stored. Current permissions/evidence are rechecked and the intent is
verified against its HMAC. Changed intent gives `409 job_intent_conflict`;
a nonfailed parent, including cancelled, gives `409 job_retry_conflict`; a nonowner gets `404`.

Retry creates **one new child** with a fresh five-attempt allowance, never resets
the old terminal record. Repeated retries of the same failed parent reuse that
child even across HTTP keys. If the child itself fails, retry the child ID for
another explicit cycle. The fixed recipe cannot be reset or replaced through
this endpoint. Retry lineage participates in purge.

### Fixed-principal worker and publication fence

`pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT [--once]` uses
restricted `PGAG_DATABASE_URL` credentials and the API's startup role/schema
checks. The subject must be preprovisioned within the configured issuer and is
trusted deployment configuration. No JWT signing/public key or admin URL is
needed. Superuser, owner-role, and `BYPASSRLS` runtime credentials are rejected.
This initial profile claims only that principal's jobs, not a global multi-tenant
scheduler; fairness and cost-pool behavior are not qualified.

Claims use `FOR UPDATE SKIP LOCKED` in a short transaction and **commit before
payload validation outside the transaction**. A fresh UUID lease token increments
the attempt, captures current access/deletion epochs, and grants a 30-second lease
(internal 1–300-second claim bounds exist for controlled tests, not CLI tuning).
Current permissions and complete immutable episode inputs are checked.

Publication rechecks principal/scope, exact original prepared body, input evidence,
lease/token/expiry, and captured epochs. The shared assertion-publication helper
commits the assertion, provenance, job success, and audit atomically. The final
job update checks expiry again; mid-publication expiry rolls back the output.
The assertion's `recorded_at`/system interval starts at **worker publication,
not enqueue**; job `created_at` is not the assertion's adoption time. Caller
valid-time bounds remain independent of this server-controlled system time.
Lease expiry/takeover fences stale publishers. Internal heartbeat checks lease
and epochs and renews the 30-second lease; no public claim/publish/heartbeat
endpoint exists. The deterministic processor makes no external call and needs
no long-running heartbeat task.

Retriable job failures schedule `2^attempt + [0,1)` seconds of jittered backoff,
up to five attempts. Nonretryable `invalid_input` fails immediately. An expired
fifth claim becomes `failed`/`attempt_limit`; no sixth attempt is granted.
Errors are logged as safe codes, without payloads. Processing is at-least-once
attempts with **at most one committed result per job**, not external exactly-once.

Continuous mode polls idle work every 1 second and delays 2 seconds after transient
DB loop failures. `--once` processes at most one due job, emits a JSON outcome
(`idle`, `succeeded`, `pending`, `failed`, or `lease_lost`) with applicable opaque
IDs/result reference, then exits. Stdout/log outcome references are opaque
historical records, not current read authorization or a live state snapshot.
Job GET and exact-revision explain apply current access/deletion checks.
It does not drain the queue and is rejected on other CLI commands.
See [operations](operations/README.md#durable-job-and-worker-operations)
and [ADR 0006](adr/0006-durable-jobs.md).

## Entities and SQL graph oracle

### Entity identity

For exact type/label discovery across readable scopes, see
[entity query](#exact-entity-query-and-pagination). It does not resolve identities;
inspect known-ID evidence and explicitly select graph seeds.

Creation requires `Idempotency-Key`, current scope read/write access, and:

| `POST /v1/entities` field | Contract |
|---|---|
| `scope_id` | Required scope UUID |
| `entity_type` | Literal `person`, `organization`, `project`, `component`, `incident`, `task`, `decision`, or `other` |
| `canonical_label` | 1–256 characters |
| `evidence` | 1–32 distinct readable same-scope **episode** IDs, each with a literal `quote` of 1–4,096 characters |
| `explicit_intent` | Must be `true` |

`201` returns `memory_id` and `revision: 1`. Identity metadata and evidence are
immutable caller reports, not verified facts. There are no aliases, entity
merging, name-based resolution, semantic deduplication, or label-correction
endpoint. Same HTTP key/body reuses the anchor under current authorization;
a different key may create a separate same-label entity.
Names and types are untrusted data, never instructions.

`GET /v1/entities/{memory_id}` returns ID/revision, scope, type, canonical label,
recorded time, and episode evidence. Entities do not appear in recall/explain;
use this dedicated GET. Entity revision 1 is allowed in checkpoint/tool-effect
`memory_refs`; declare every copied entity or assertion revision dependency.

### Canonical relation assertions

`POST /v1/relations` requires `Idempotency-Key`, `scope_id`, `source_entity` and
`target_entity` UUIDs, `predicate`, 1–32 distinct literal episode evidence quotes,
and `explicit_intent: true`. Both endpoints **and evidence must share that scope**
and be currently readable. Quote bounds are 1–4,096 characters. Optional
`valid_from`/`valid_to` are timezone-aware; null/omitted is unbounded, and start
must precede end. The predicate allowlist is `depends_on`, `part_of`, `affects`,
`works_for`, `decides`. All predicates allow multiple reported declarations;
there is no arbitration, verified truth, or inferred inverse fact.

A relation has **one canonical assertion identity (`memory_id`)**, not an
independent object ID: `memory.relation` fixes its source, and
`memory.relation_revision` records the exact target of each assertion revision.
Subject is the immutable source label; each revision's immutable value is its
target's canonical label. Valid/system time, truth status, and episode evidence
are the existing assertion revision's, not a parallel graph history.
Creation returns the existing `RememberResult` (`memory_id`, revision 1,
`epistemic_status: "reported"`). A free-text `remember` with matching
label/predicate never automatically becomes a relation.

`POST /v1/relations/{memory_id}/revisions` requires `Idempotency-Key`,
strict `expected_revision` 1–1000, `target_entity`, replacement episode `evidence`,
`explicit_intent: true`, optional aware valid bounds, and `reason` 1–256 characters.
Source/predicate/scope are fixed. It replaces the **entire valid interval**, like
generic assertion corrections; it does not split time or retain the prior value
outside new bounds. Old target IDs, evidence, and intervals remain tied to their
exact historical revision. CAS, idempotent historical replay, and the 1000-total-
revision bound are unchanged (`409 revision_conflict`, `422 revision_limit_exceeded`).
Use `explain` for exact relation evidence; omitted revision still means **1**.

### Bounded expansion

Authenticated `POST /v1/graph/expand` is read-only; no `Idempotency-Key` is required.

| Field | Contract |
|---|---|
| `scope_ids` | Required 1–32 distinct scope UUIDs |
| `seeds` | Required 1–16 distinct entity UUIDs |
| `relation_types` | Required 1–5 distinct allowlisted predicates above |
| `purpose` | Required 1–256-character text |
| `direction` | `outgoing` (default), `incoming`, or `both` |
| `max_hops` | Strict integer 1–2, default 2 |
| `max_paths` | Strict integer 1–100, default 100 |
| `as_of`, `known_at` | Optional timezone-aware timestamps; defaults captured once per expansion |

Canonical PostgreSQL SQL joins are the only backend: no AGE, SQL/PGQ, Cypher,
dynamic SQL, or dynamic labels in graph requests/traversal. Fixed parameterized
neighbor queries enforce time and current RLS visibility for seeds, edges,
intermediate nodes, and evidence, with same-scope foreign keys. Scope/seed
filters only narrow access. Hidden, nonexistent, or temporally unavailable
seeds are silently excluded, not echoed. The existing tenant transaction and
response-drain lock protect the read boundary.

Traversal is deterministic breadth-first **simple paths**: sorted seed UUIDs,
then each hop's assertion ID/revision/next entity ID. No entity repeats within
a path; semantic cycle edges do not create repeating-node paths. All prefixes
count toward the global path budget. A limit-plus-one probe detects additional
eligible paths; at most `max_paths` are returned. Incoming/both changes traversal
orientation only; edge source/target and reported facts are not inverted.

Results contain `backend: "sql"`, `projection_watermark: null` (no graph projection,
lag, or watermark guarantee is needed), effective `as_of`/`known_at`, and:
- `nodes`: canonical entity summaries, without full evidence quotes;
- `edges`: canonical assertion ID/revision, source/target UUIDs, predicate,
  valid interval, recorded time, and `epistemic_status: "reported"`;
- `paths`: `{nodes: [UUIDs], assertions: [{memory_id, revision}]}`;
- `coverage`: `max_hops`, `truncated`, and `complete_within_bounds`;
- `consistency`: current access/deletion epochs;
- `empty_reason`: `not_found` when no paths exist, otherwise null.

Visible isolated seeds may still appear in `nodes` without paths. No-path and
bounded completeness are **not proof that no fact exists**. Entity GET/relation
explain provide evidence; expansion summaries do not. A missing node on the
final recheck fails closed with `409 graph_invalidated`; DB errors return `503`,
not an empty-success fallback. Capabilities expose `graph_backend: "sql"`,
entity/relation type allowlists, and graph caps. This is a correctness reference
for future backend conformance, not measured graph utility or full M1/M3 acceptance.
See [ADR 0005](adr/0005-relational-graph.md).

## Checkpoint contract

Checkpoint creation and restoration require `Idempotency-Key` and current
read/write access to the scope. GET and [head lookup](#checkpoint-head-lookup)
require current read access, without an idempotency key. Run/branch
UUIDs are caller-supplied identities within a tenant/scope, not global sessions.

| Creation field | Contract |
|---|---|
| `scope_id`, `run_id`, `branch_id` | UUIDs identifying the scope-local run and branch |
| `expected_head` | Required UUID or `null`; null only for an empty branch, otherwise the exact current checkpoint ID |
| `harness_id`, `harness_version` | Required nonempty text, at most 256 characters each; fixed for the run |
| `state_schema_version` | Only `1`, also the default |
| `event_watermark` | Required nonnegative 64-bit integer; cannot decrease relative to the parent |
| `state` | Typed `goal`, `constraints`, `completed_actions`, `decisions`, `unresolved_questions`, `next_actions`, and `pending_effects`; no arbitrary object/pickle |
| `memory_refs` | Up to 100 distinct `(memory_id, revision)` pairs in the same scope; episodes/entities use revision 1, assertion revisions (including relations) must exist; omitted list is empty and omitted revision defaults to 1, not latest |

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
and explain; use checkpoint GET for a known ID or head lookup for an exact branch.
GET can load a live surviving ancestor but does not promise the latest head.
Head lookup reuses these checks and never falls back from an invalidated branch.

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
GET/head/restore merge **all live effects in the run**, including other branches and
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
no provider-query service, automatic execution, or checkpoint-execution harness adapter is implemented.

Identical-key retries retain the original checkpoint reference, not a new head.
Replay records contain no state; reads/restore retries rebuild envelopes under
current authorization, so current epoch metadata can change. Purged-reference
replay returns `404`.

**Callers must declare every memory dependency in `memory_refs`.** The
dependency DAG covers declared references, complete parent lineage, and the
run-wide effect-to-checkpoint dependency described below;
no semantic scanner discovers copied but undeclared source text. Callers remain
responsible for consent and secret/PII sanitization. Working snapshots,
compaction, and checkpoint-execution harness integration remain separate future work.
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
| `memory_refs` | Up to 100 distinct exact same-scope references: episode/entity revision 1 or existing assertion revision 1–1000 (including relations); defaults to empty, omitted revision is 1, not latest |

**Declare every memory dependency used by the action.** Checkpoints/effects/jobs are
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
Entity evidence adds **episode → entity → relations using it as source or any
historical target → entire assertion history**. Direct entity purge has the
same relation closure. Entity references in checkpoints/effects also participate.
Other surviving entity identities are not deleted merely because a relation is
removed. Entities depend only on episodes: semantic graph cycles do not introduce
provenance cycles.
Declared episode/entity/assertion-to-effect references add
**source → tool effect → every checkpoint in that scope/run**, including old
checkpoints with empty references and snapshots predating the effect.
Jobs add **episode → job** through immutable inputs, **result assertion → job**,
and **parent job → retry descendants**. Jobs count within the same closure limit.
Deleting any source, including one used only by a later revision of a result
assertion, purges that entire assertion history and dependent jobs.
**Deleting a job/control record or failed-parent retry chain does not delete
already-published independent assertion outputs or source episodes.** Outputs
have direct episode provenance; purge the output/source explicitly to erase the
fact. There is no job → result dependency cycle.
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

Purge deletes job input/request rows before assertion/episode payloads and
tombstones, fencing running publishers under the same tenant barrier.
Purged-job GET and exact HTTP replay return `404`; retained job identity prevents
resurrection of the same exact job.
Purge synchronously SQL-deletes target episode/entity/assertion/checkpoint/effect/job
payloads, entity evidence, typed relation links, effect events (including reasons/
receipt references), and dependent quotes/references,
then inserts scope-bound opaque deletion markers with timestamps
in `memory_ops.object_tombstone` **in the same transaction**. `memory.object`
has no `deleted_at` column; its SELECT RLS excludes objects with tombstones.
The transaction also advances `deletion_epoch` and commits a receipt.
HTTP `202` with `active_store_purged` is **not** a queued purge job or certification
of complete erasure. Opaque operation registry/run flags, run/branch metadata,
object records, tombstones, audit/receipt
metadata, job identities, and tenant-keyed HMAC source/idempotency tombstones persist for the
tenant lifetime; there is no automatic expiry or full tenant-erasure workflow.
Historical references and replay cannot resurrect purged labels, values, or receipts.
Canonical deletion also cascades every affected episode/assertion lexical
revision under the same tenant barrier, before tombstones commit. These derived
payloads are not separate memory identities or provenance vertices. Rebuild
skips tombstones and does not regenerate purged content.

Receipts report `backup_status: "operator_managed"` and
`backup_retention_deadline: null`. Old database pages, WAL, replicas, backups,
and previously delivered context are not certified erased. The service is not
qualified for full-erasure guarantees or production compliance. Databases
restored from backups must remain quarantined until the latest deletion ledger
and ACL revocations have been reapplied; automated backup recovery/ledger replay
and DR qualification are not implemented.

## Schema compatibility

**v0.0.26 requires schema 11 and migration `011_capture_policy.sql`.**
Existing schema-10 databases use the [schema-11 maintenance upgrade](operations/README.md#schema-11-scope-capture-policy-upgrade).
Older schemas still apply `010_job_cancellation.sql`, introduced in v0.0.15 for
job-state/payload constraints and the terminal guard, via the
[migration sequence through 011](operations/README.md#schema-11-scope-capture-policy-upgrade).
Historical v0.0.13 introduced `009_scope_access.sql` for durable admin audit.
The retained privileged-only `memory_ops.scope_access_event` table uses forced RLS,
no runtime policy/grant, and atomic membership/epoch/audit changes.
Retain the pinned PostgreSQL 18.6/pgvector 0.8.6 images.
Stop/drain old APIs, workers, adapters, hooks, and SDK callers, then deploy only
matching v0.0.26 components; no mixed-version/rolling-compatibility claim is made.
All older schemas need sequential migrations through 011.
Older databases still need v0.0.11's `008_pgvector.sql`.
Migration requires **`vector` 0.8.6 in `public`** and rejects
an existing extension in another schema or at another version.
Use the pinned prebuilt upstream DB profile above, not an assumed unchanged old
PostgreSQL image or an unpinned extension. Do not start older-schema processes against schema 11.
The retained episode/assertion-revision projections use forced RLS, canonical
`ON DELETE CASCADE`, and runtime SELECT/INSERT only. **No embedding backfill**
runs for existing data; generation/rebuild/provider calls remain explicit and external.
The MCP adapter, hook, and SDK use HTTP only, perform no DDL, and require matching
service `0.0.26`, API `v1`, schema `11`.
The retained migration history below still applies to databases older than schema 7.

Additive `007_japanese_fts.sql` follows unchanged migrations 001–006. It creates
the two lexical projection tables; the migration runner performs Python backfill
in the **same transaction** before recording schema 7. All retained episodes and
assertion revisions are covered without changing canonical IDs/system times,
receipts, or tombstones. Failure even after backfill completes rolls back
projection DDL/data and the schema ledger together: a schema-6 upgrade remains at
6. Failed explicit reindex instead preserves existing schema-7 projections.
Typed graph/job/effect/checkpoint histories and guards,
legacy `Remember` JSON/HMAC ordering, source identities, and checkpoint checksums
remain unchanged. Projections add no checkpoint/effect reference kinds.
The v0.0.26 API **and worker** require exact history `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]`
and extension `vector` 0.8.6 in schema `public`, rejecting mismatches and unsafe runtime roles.

Migration/rebuild requires a forced-RLS-bypassing administrator with appropriate
rights; migration also requires DDL rights, `btree_gist`, and the matching pgvector
extension installed on the PostgreSQL server. `row_security = off`
fails closed if RLS would filter backfill; it does not grant bypass privileges.
`pg-agmemory reindex-lexical` is an **all-tenant offline admin operation** on the
selected database. Use matching v0.0.26/schema-11 tooling with `PGAG_ADMIN_DATABASE_URL`.
It atomically replaces only lexical projections under the migration lock, emitting the
`profile` and `episodes`/`assertion_revisions` counts, not source content.
`--subject` is explicitly rejected, not a principal/scope filter; `--once` is
also rejected as worker-only.
Stop/drain all old/new APIs **and workers**, back up, migrate/rebuild atomically,
then start only matching v0.0.26 processes. Stop adapters, hook launches, SDK callers, and admin commands during maintenance too.
Lexical reindex does not generate, populate, or rebuild vectors.
**Keep all old images stopped; v0.0.1 has no schema startup guard.**
No rolling coexistence or downgrade is supported. Follow
[current schema-11 operations](operations/README.md#schema-11-scope-capture-policy-upgrade).
Schema-10 binaries reject schema 11. Code revert alone is not DB rollback;
there is no downgrade command. Preserve a pre-upgrade backup and reconcile
current ACL/deletion/policy decisions before any restored database serves traffic.

## Validation evidence

Public repository: [rioriost/pg_agmemory](https://github.com/rioriost/pg_agmemory).

<a id="v0026--schema-11"></a>

### v0.0.26 / schema 11 — implementation `c07630009ff4dcc34542e3ea80064d4f10c4d8b5`: local and native CI qualified

The active milestone is scope capture policy, stage `m2-scope-capture-policy`,
on feature branch `feat/scope-capture-policy`.
The **qualified schema-11 code rollback checkpoint is
`c07630009ff4dcc34542e3ea80064d4f10c4d8b5`**.
Inherited **unqualified rollback checkpoint `9740f96`** preserves the resumed work;
it is not a qualification SHA or a stable schema-10 fallback.
The stable pre-migration **v25/schema-10 code reference is `869b854`**.
None of these code references replaces a database backup or downgrades a schema-11 database.
Do not infer v26 results from historical v25/v24 records below.

The **initial local attempt had 1064 passed, 4 failed, and 5 skipped**; it is
**not qualification**. Follow-up fixes cover SDK policy-code preservation, typed
OpenAPI 403, the existing 422 `invalid_request` contract, schema-ledger expectations,
and lease-recovery fixtures that expire the actual lease instead of rewinding
protected timestamps. The corrected recovery coverage checks the same job at attempt 2.
That failed historical attempt is separate from the successful local and native runs below.

Implementation
[`c07630009ff4dcc34542e3ea80064d4f10c4d8b5`](https://github.com/rioriost/pg_agmemory/commit/c07630009ff4dcc34542e3ea80064d4f10c4d8b5)
passed **`./scripts/test-containers.sh` on Apple Container, native Linux arm64,
exit 0**. Pytest reported **1083 passed, 5 opt-in live skips, 1 existing Starlette
deprecation warning / 499.15 s**.
Ruff, mypy **24 source files**, the strict SDK consumer **1 file**, all
**core/hook/sdk/providers** optional installation profiles, and the full non-root
runtime smoke suite passed.

The scope-capture smoke passed administrator CAS, all three admission paths,
replay denial, restore, and purge; the existing scope-access smoke also passed.
Retained CA, Japanese tokenizer, API, readiness, worker, both MCP protocol versions,
all three hook events, capture, vector, SDK, required-context, recall-filter,
checkpoint-head, cancellation, job-query, assertion-history, entity, batch,
episode-query, and synthetic-provider smokes all passed.
**No live model/profile calls or cloud resources were used.**
These are results for the implementation SHA above, not the uncommitted docs or
a future documentation-only commit.

**Exact-SHA native CI completed successfully** in
[run 35308638587](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587)
on implementation `c07630009ff4dcc34542e3ea80064d4f10c4d8b5`:

| Native Docker environment | Job | Pytest result |
| --- | --- | --- |
| Linux amd64 | [105485882110](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587/job/105485882110) | 1083 passed / 5 opt-in live skips / 1 existing warning / **558.30 s** |
| Linux arm64 | [105485882271](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587/job/105485882271) | 1083 passed / 5 opt-in live skips / 1 existing warning / **784.48 s** |

Both jobs passed Ruff, mypy **24 source files**, the strict SDK consumer **1 file**,
all four optional installation profiles, and **all non-root runtime smokes**,
including new capture-policy and retained scope-access coverage.
Both reached the final `Container tests ... passed (docker)` confirmation.
There were **no live calls**. These are implementation-CI results, not evidence
that a later docs-only publication SHA ran the suite.

| Required evidence | Current status |
| --- | --- |
| Qualified implementation SHA and command/environment | `c07630009ff4dcc34542e3ea80064d4f10c4d8b5`; Apple Container native Linux arm64 and native Docker Linux amd64/arm64; `./scripts/test-containers.sh` passed |
| Local suite counts, warnings/skips, elapsed time | 1083 passed / 5 opt-in live skips / 1 existing warning / 499.15 s |
| Ruff, strict mypy/source + SDK consumer counts, installation profiles | Passed; 24 source files + 1 strict SDK consumer; all four profiles |
| Production smokes including scope-capture CAS/deny/replay/restore and retained scope-access barrier | Passed in all three environments, including purge and all retained non-root smokes |
| Schema 10→11 migration, RLS/audit isolation, max-epoch and ambiguity regressions | Full local and both native suites passed |
| Exact-SHA native amd64 and arm64 CI URLs, results, timings | [Source CI 35308638587](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587) succeeded; amd64 558.30 s / arm64 784.48 s, each 1083 passed / 5 skipped / 1 warning |
| Later documentation-only publication commit | Not the tested implementation SHA; no suite-run claim for that later commit |

Update only with observed evidence, preserving implementation versus final-doc
SHAs and local versus CI results separately. No new live provider trial is implied.
M2/MVP/production, performance, memory quality, DR, and full-erasure gates remain open.
No unresolved decision blocks this bounded scope-capture-policy milestone.
The remaining work below is separately gated future scope, not an unfinished v26 qualification.

**Next bounded resume point after v26 (not implemented here): M2 automatic
synthesis, embedding integration, and compaction**, with remaining quality and
task-replay gates. Start with a separately specified opt-in extraction/proposal
workflow over approved episodes: explicit consent/provider-egress approval,
untrusted proposal review and evidence validation, stable intent/replay handling,
and EN/JA quality/cost evaluation before automatic publication.
Bounded compaction and task-replay acceptance must remain explicit gates, not
assumed consequences of episode admission. Capture policy does not authorize
provider egress or complete these quality/performance requirements.
See [ADR 0026](adr/0026-scope-capture-policy.md).

<a id="v0025--schema-10"></a>

### Historical v0.0.25 / schema 10 — local qualification passed; native CI passed

The full Apple Container `./scripts/test-containers.sh` passed with
**999 passed, 5 live skipped, 1 known warning, 494.49 s**.
Ruff, mypy **22 source files + 1 strict SDK consumer**, all four
core/hook/sdk/providers installation smokes, and **all production smokes,
including the new system-CA smoke**, passed.
Implementation
[`adbead0ab42dfa4a5465f9464d3855c5f64d85c1`](https://github.com/rioriost/pg_agmemory/commit/adbead0ab42dfa4a5465f9464d3855c5f64d85c1)
passed exact-SHA [CI 35303758871](https://github.com/rioriost/pg_agmemory/actions/runs/35303758871):
**amd64 999 passed, 5 live skipped, 1 warning / 600.77 s;
arm64 999 passed, 5 live skipped, 1 warning / 797.75 s**.
Both native jobs passed Ruff, mypy **22 source files + 1 strict SDK consumer**,
all four core/hook/sdk/providers installation profiles, and all production smokes
including the new system-CA smoke. This is implementation CI, not a
qualification claim for a later final-documentation commit.
The five skipped live cases were not additional provider calls.

The implementation sets the Docker base's `SSL_CERT_FILE` to the installed OS
CA bundle and adds a runtime production smoke checking the environment and
`ssl.create_default_context().cert_store_stats()["x509_ca"] > 0`.
**Nine real psycopg/libpq TLS startup cases passed in Apple Container**, along with
targeted Ruff. The loopback minimal PostgreSQL startup fixture uses an ephemeral
CA/server key and bounded socket/thread teardown; it is not a live Azure test.
Coverage includes system-CA environment selection, explicit DSN root-CA precedence,
missing/untrusted CA and hostname rejection, no `require` downgrade from
`verify-full`, and sanitized errors.
A separate **non-root v25 runtime check confirmed the environment value and
150 loaded trusted CAs**. These targeted checks and the full-suite result above
remain distinct from the historical Azure run and from the exact-SHA implementation CI.
No new Azure calls or resources are needed; the completed Azure trial's cleanup
was verified at **12:07:30 JST**.

The v24 `aa364c3` Azure run used an **explicit DSN CA file**, not the new image
environment default. Its five contract passes and CLI success do not qualify
the v25 transport default or language quality: an English summary returned in
Spanish once. The exact Ollama/Azure profile evidence and published v24 CI below
remain historical, not v25 release results.
Schema 10, API v1, 31 Native/SDK resources, four MCP tools, and stage
`m2-selectable-inference` remain. `auto_synthesis` and global
`model_inference.live_provider_qualified` remain **false**.
See [ADR 0025](adr/0025-live-provider-qualification.md) and
[the TLS known issue](INFERENCE_PROFILES.md#tls-trust-store-known-issue).

<a id="v0024--schema-10"></a>

### v0.0.24 / schema 10 — implementation and synthetic-provider contracts qualified

**Selectable inference foundation qualified, 2026-09-18 JST; not M2 completion.**
Implementation
[`88975a862ff97873c60e5ce53e066e1aa7b52686`](https://github.com/rioriost/pg_agmemory/commit/88975a862ff97873c60e5ce53e066e1aa7b52686)
(`feat: support selectable HTTP and Azure SQL inference`) passed the full Apple
Container `./scripts/test-containers.sh`: **987 passed, 1 warning, 493.68 s**.
Exact-SHA [CI 35292285229](https://github.com/rioriost/pg_agmemory/actions/runs/35292285229)
passed: **amd64 987 / 762.27 s; arm64 987 / 809.06 s**.
All three environments passed Ruff, mypy **22 source files + 1 strict SDK consumer**,
all four core/hook/sdk/providers installation profiles, and all production smokes.

**987 = 821 retained + 166 new cases**: 51 HTTP/configuration/CLI/lifecycle and
115 Azure SQL cases. The latter include **11 real disposable PostgreSQL integration
cases with synthetic SQL functions and extension membership**, not a vendor Azure
extension binary. Verified coverage includes closed settings/input, loopback/HTTPS and secret references,
bounded HTTP responses, model/vector validation, sanitized failures/billing uncertainty,
SQL role/extension-ownership/overload/permission guards, single result evaluation,
and explicit non-publishing CLI/library workflows.
The new production smoke runs the actual operator CLI against synthetic HTTP,
followed by explicit Native vector upload/replay/purge.
No live Azure/real-model calls, billed resources, or private-data egress were used.
These results do not qualify live provider compatibility, MemoryDB Azure hosting,
quality, budget controls, human-review effectiveness, or M2/MVP completion.
`live_provider_qualified` remains **false**. Final-docs CI for this update has not run.
See [the contract](#selectable-inference-providers) and
[ADR 0024](adr/0024-selectable-inference.md).

**Local-model workflow follow-up (2026-09-18 JST):** separately from the 987-case
synthetic qualification above, four live contract cases passed in **23.97 s**;
the Native lifecycle's synthetic/live pair passed in **3.98 s**. These are **five
live cases across separate runs**, not one combined timed run. Actual CLI
`inspect`/`summarize`/`embed` also succeeded: **eight real model calls** total
(four contract + two Native + two CLI; inspect performs no inference), all using
synthetic text on Apple Container Linux arm64 through a guarded host bridge to
host-loopback Ollama 0.34.1. Native upload/replay/recall/purge and rejection of
post-purge replay/stale upload passed; its pinned PostgreSQL 18.6 / pgvector 0.8.6
tmpfs container was removed. The full Apple Container `./scripts/test-containers.sh`
then reported **989 passed, 5 live skipped, 1 warning, 500.46 s**; Ruff, mypy
**22 source files + 1 strict SDK consumer**, all four core/hook/sdk/providers
installation checks/smokes, and all production smokes passed.
Owned Ollama/host-relay/client-relay processes were stopped by tracked IDs;
the named live client container/test image were removed, and the full script
cleaned up its own containers/images. Only approved model weights remain.
Published commit
[`aa364c3969fda48b52c8fef19c8794ec99cd354b`](https://github.com/rioriost/pg_agmemory/commit/aa364c3969fda48b52c8fef19c8794ec99cd354b)
passed exact-SHA [CI 35298758297](https://github.com/rioriost/pg_agmemory/actions/runs/35298758297):
**amd64 989 passed, 5 skipped / 685.52 s; arm64 989 passed, 5 skipped / 796.17 s**,
with Ruff, mypy **22 + 1**, all four optional installation checks, and all production
smokes passing on both. Live cases were skipped in CI; local live evidence is separate.
This CI predates the new Azure template/trial and does not qualify that work.
The recorded service **0.0.24 / API v1 / schema 10** runs are not human quality
certification or M2 completion.

**Azure exact-profile live follow-up (2026-09-18 JST):** Apple Container with
mounted `aa364c3` code passed **5 live cases, 54 deselected, 1 known warning /
20.12 s**, plus actual CLI inspect/summarize/embed. There were **8 application
inference calls**, no application retries; SQL inspect reported
`contract_verified: true`, `inference_tested: false`.
The West US 3 trial used AI Services S0, GPT-4.1-mini version 2025-04-14
(GlobalStandard capacity 10), embedding-3-small version 1 (capacity 1), and
PostgreSQL 18.6 B1ms/32 GiB with **azure_ai 2.0.1**. Managed identity had
account-scoped Cognitive Services OpenAI User; API-key auth was disabled and SQL
used a restricted login. TLS was **verify-full/TLS 1.3 with an explicit OS CA file**.
Native lifecycle checks used separate local PostgreSQL 18.6 / vector 0.8.6;
no MemoryDB schemas were created in Azure.
**An English input produced a Spanish summary once despite the original-language
instruction.** Contract passes are not language, grounding, or quality-gate passes.
Summaries remained untrusted; embeddings were 768-dimensional, finite/nonzero.

Cleanup was verified at **12:07:30 JST**, before the 13:52 deadline: both
deployments, account, server, new resource group, and account-scoped RBAC were
deleted; the owned soft-deleted Foundry record was purged. The local test DB and
six secret files were removed. No owned Azure trial resources remain or extra
cloud calls are planned. OpenAI remains untested; HorizonDB was waived and no
Azure Language calls were tested.

Historical **2.0.0** references/fixtures and the earlier no-host-mount template
check (**157 unit passes, 13 integration deselected / 2.26 s**, Ruff/mypy **22 + 1**)
remain distinct. Offline TLS diagnosis found missing bundled OpenSSL default CA
files; both builds honor `SSL_CERT_FILE`. The v25 Docker base sets
it to `/etc/ssl/certs/ca-certificates.crt`; the separate local TLS/runtime evidence
is recorded in [v25 qualification status](#v0025--schema-10). **Azure live success used
the explicit DSN CA file, not this environment fix.**
`model_inference.live_provider_qualified` remains **false**; no new full-suite/CI
result or blanket provider certification is claimed.
See [Azure evidence and limitations](INFERENCE_PROFILES.md#recorded-azure-live-evidence)
and the [TLS known issue](INFERENCE_PROFILES.md#tls-trust-store-known-issue).

<a id="v0023--schema-10"></a>

### v0.0.23 / schema 10 — historical implementation qualification

**Episode query implemented and qualified, 2026-09-18 JST.**
Implementation
[`bf53a30625ffcfb0f23f986abcec5e2d608dcb68`](https://github.com/rioriost/pg_agmemory/commit/bf53a30625ffcfb0f23f986abcec5e2d608dcb68)
(`feat: discover scoped episode metadata`) passed the full Apple Container
`./scripts/test-containers.sh`: **821 passed, 1 warning, 499.94 s**.
Targeted checks also passed: **333 passed, 1 warning, 55.66 s**.
Exact-SHA [CI 35280254044](https://github.com/rioriost/pg_agmemory/actions/runs/35280254044)
passed on both native architectures: amd64 **821 passed, 1 warning, 821.33 s**;
arm64 **821 passed, 1 warning, 823.87 s**.
All three environments passed Ruff, mypy **19 source files + 1 strict SDK consumer**,
all optional installation checks, and all production smokes, including episode query.

**821 = 780 retained + 41 new cases**: 27 contract, 7 Native episode-query,
and 7 SDK cases (6 mock + 1 real workflow). The prior 780-case baseline is unchanged.
Verified coverage includes closed request/cursor models, timezone/range validation,
100/101-row timestamp/UUID ties, half-open bounds, late historical events in
recorded-time order, shared-scope visibility, current ACL/purge/epochs,
metadata-only read-only SQL without audit writes, typed SDK mock/real workflows,
and production SDK explicit query/select/Explain/Remember.
Service 0.0.23 / API v1 / schema 10, 31 Native/SDK resources, four MCP tools,
and the closed hook retain the documented contract, with no dependency or migration change.
Separate final v23 docs
[`9bc5092e5919997abb945554ec8363e4bf0e6dae`](https://github.com/rioriost/pg_agmemory/commit/9bc5092e5919997abb945554ec8363e4bf0e6dae)
completed [CI 35282317544](https://github.com/rioriost/pg_agmemory/actions/runs/35282317544)
successfully on attempt 2: **821 cases each, amd64 851.59 s / arm64 802.42 s**.
Attempt 1's amd64 Docker Hub authentication connection reset occurred **before tests**;
only that failed job was retried, without product code changes.
This final-docs run is distinct from implementation CI; neither is v24 qualification.
See [the contract](#episode-query-and-pagination) and [ADR 0023](adr/0023-episode-query.md).
No MVP, production, performance, memory-quality, or DR acceptance is claimed.

<a id="v0022--schema-10"></a>

### v0.0.22 / schema 10 — historical all-direction qualification

**Final all-direction qualification verified, 2026-09-18 JST.**
Directional follow-up
[`3f56c51434333428fe742bb6a464d1d3117e8e26`](https://github.com/rioriost/pg_agmemory/commit/3f56c51434333428fe742bb6a464d1d3117e8e26)
(`test: cover bounded graph plans in every direction`) passed the full Apple
Container `./scripts/test-containers.sh`: **780 passed, 1 warning, 507.09 s**.
The targeted nine combinations also passed: **9 cases, 75.55 s**.
[CI 35271311062](https://github.com/rioriost/pg_agmemory/actions/runs/35271311062)
passed on that exact SHA: amd64 **780 passed, 1 warning, 819.23 s**;
arm64 **780 passed, 1 warning, 745.25 s**.
All three environments passed Ruff, mypy **19 source files + 1 strict SDK consumer**,
all optional installation checks, and all production smokes.

The qualified regression crosses `auto`, `generic`, and `nested_loop` with
`outgoing`, `incoming`, and `both`, covering both adjacency branches:
**3 modes × 3 directions = 9 cases**.
The final count is **780 = 773 batch baseline + 1 nested-loop case + 6 direction cases**.
This directional extension changes regression coverage, not product SQL, version,
or schema.

Separate final v22 docs
[`36dc19d8897db6768badaf39019445e2a22d23da`](https://github.com/rioriost/pg_agmemory/commit/36dc19d8897db6768badaf39019445e2a22d23da)
passed [CI 35273848787](https://github.com/rioriost/pg_agmemory/actions/runs/35273848787):
**780 cases each, amd64 862.46 s / arm64 730.09 s**, with all checks/smokes.
This final-docs evidence is distinct from the directional implementation run,
and none of the v22 results qualifies v23.

**Earlier 774-test graph-fix qualification:**
Fix [`bf7429327955071239fdc2f7b60d1a5d47dfff7e`](https://github.com/rioriost/pg_agmemory/commit/bf7429327955071239fdc2f7b60d1a5d47dfff7e)
(`fix: bound protected canonical graph rescans`) passed the full Apple Container
suite: **774 passed, 445.86 s**, with all smokes. Targeted Apple Container checks
also passed **100 tests, 199.92 s**.
[CI 35268438022](https://github.com/rioriost/pg_agmemory/actions/runs/35268438022)
passed on that exact SHA: amd64 **774 passed, 779.45 s**;
arm64 **774 passed, 682.78 s**. Both passed Ruff, mypy **19 source files +
1 strict SDK consumer**, all installation checks, and all production smokes.

**Negative control:** the regression against unmodified pre-fix source in an
earlier test image produced **1 expected failure, 8.29 s**. The retained
`graph-canonical-baseline-plan.json` synthetic diagnostic showed `assertion`
and `assertion_revision` at **400 loops**, and endpoint-evidence at **4 loops**.
It is not the failed CI plan or a production performance benchmark.

Final-docs revision
[`af91974d091feb276db79baf838c7fa2bab8904f`](https://github.com/rioriost/pg_agmemory/commit/af91974d091feb276db79baf838c7fa2bab8904f)
**failed** [CI 35265233011](https://github.com/rioriost/pg_agmemory/actions/runs/35265233011):
amd64 **772 passed, 1 failed, 631.34 s**; arm64 **773 passed, 701.79 s**, with all smokes.
The existing 100-path auto-plan case of
`test_exact_graph_path_seed_and_entity_evidence_limits` again received **503**
with `QueryCanceled` / statement timeout. Its actual CI execution plan was **not captured**.

The retained disposable diagnostic `graph-materialized-plan.json` shows that
`adjacent` materialization alone left `assertion` and `assertion_revision` scans
at 100 loops each, with endpoint/entity-evidence scans repeated 100 times per
endpoint. This is evidence from that diagnostic, not the failed runner's plan.
The follow-up keeps `adjacent` **MATERIALIZED** and separately materializes
scope/predicate-filtered assertions, time-filtered revisions, and distinct
authorized/evidence-valid endpoints. Precomputed ID arrays are intended to prevent
semijoin reversal and repeated scans of protected canonical tables; the outer
join uses materialized authorized metadata. Current RLS, scope, time, evidence,
ordering, limits, and the **5000 ms** statement timeout are unchanged.
This is a code follow-up, not an unchanged-code rerun or timeout increase.
Version v0.0.22, API v1, schema 10, and 30 Native/SDK resource methods remain unchanged.

The qualified all-direction regression covers `auto`, `generic`, and `nested_loop`,
preserving the actual generic-prepared-usage assertion. Runtime-role
`EXPLAIN ANALYZE` on the actual SQL and parameters checks 100 returned rows at
100 paths, `assertion` and `assertion_revision` scan loops **<= 1**, and
`entity` and `entity_evidence` scan loops **<= 2**.
All nine mode/direction combinations passed these checks. No performance benchmark
or production completion is claimed.

**Initial v22 implementation evidence, not qualification of the follow-up:**
the full Apple Container `./scripts/test-containers.sh` completed with
**773 passed, 1 existing warning, 447.40 s**.
The total is **730 retained + 43 new cases**: **8 contract**, **18 capture**
(17 new cases plus one batch parameter in an existing actual response-loss test),
and **17 SDK** (9 bad-key parameters, 6 mock cases, 1 real workflow, and one batch
parameter in an existing actual response-loss test). These extend
`tests/test_contract.py`, `tests/test_capture.py`, and `tests/test_sdk.py`.
The existing 100-active-job quota test also covers late batch rollback without
adding a test case to the count.

Committed and pushed implementation:
[`75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe`](https://github.com/rioriost/pg_agmemory/commit/75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe)
(`feat: atomically admit batches of structured memory jobs`).
[CI 35262682028](https://github.com/rioriost/pg_agmemory/actions/runs/35262682028)
passed on that exact SHA for both native Docker architectures:
amd64 **773 passed, 621.56 s**; arm64 **773 passed, 702.01 s**.
All three environments passed Ruff, mypy **19 source files + 1 strict SDK consumer**,
genuine core-only/hook-only/sdk-only installation checks, and all previous
production smokes plus **batch capture worker/replay/purge**.

Coverage includes exact 1/16-entry acceptance and 17-entry rejection, normalized
duplicates, late quote/audit/quota rollback, per-principal ownership, current
write ACLs, ordered replay and changed-body conflicts, legacy capture/Observe
interoperability, independent failed/cancelled/succeeded terminal jobs,
source/job/result purge, replay validation of all children, and old-lease fencing.
Actual committed response loss produces SDK `outcome_unknown: true` and supports
explicit recovery with the original stable key/body; the 256 KiB body bound is checked.

The contract, 30 Native/SDK resource methods, four MCP tools, and closed hook
remain as documented. Schema 10/history 1–10 and dependencies/backend/providers
are unchanged; no SQL migration. These initial results remain distinct from the
later failed final-docs CI, the qualified 774-test graph fix, and the qualified 780-test directional extension.
Elapsed time is not a benchmark; no M0–M3/MVP, performance, memory-quality,
production, or DR qualification is claimed.

<a id="v0021--schema-10"></a>

### Historical v0.0.21 / schema 10 — follow-up verified

**Historical follow-up verified locally and natively, 2026-09-18 JST:** committed and pushed
[`956b232f38caeeb7d0421a2d6fd3d8340206bcbc`](https://github.com/rioriost/pg_agmemory/commit/956b232f38caeeb7d0421a2d6fd3d8340206bcbc)
(`fix: stabilize bounded graph adjacency under prepared plans`).
The full Apple Container `./scripts/test-containers.sh` requalification passed:
**730 passed, 1 existing warning, 430.12 s** (**729 retained + 1 generic-plan
regression**). Ruff, mypy **19 source files + 1 strict SDK consumer**,
genuine core/hook/sdk-only installations, and all production smokes listed below
passed locally. Native [CI 35257534254](https://github.com/rioriost/pg_agmemory/actions/runs/35257534254)
passed on that exact fix SHA: amd64 **730 passed, 1 warning, 748.37 s**;
arm64 **730 passed, 1 warning, 654.52 s**. Both native Docker runs also passed
Ruff, mypy **19 source files + 1 strict SDK consumer**, all optional installation
checks, and all production smokes listed below.
This qualifies the new code fix, not a successful retry of the failed docs revision.
Separate final v21 docs
[`8f60790e3309368a340f16a771ad44d285aaafde`](https://github.com/rioriost/pg_agmemory/commit/8f60790e3309368a340f16a771ad44d285aaafde)
passed [CI 35259655219](https://github.com/rioriost/pg_agmemory/actions/runs/35259655219):
native amd64 **730 cases, 727.92 s**, arm64 **730 cases, 628.61 s**.
Both passed Ruff, mypy **19 source files + 1 strict SDK consumer**, all optional
installation checks, and all production smokes. This final-docs run is separate
from code-fix CI 35257534254 and is not v22 qualification.

The preceding final-docs revision
[`4d97e92ec08d845ebd2c969819a999e9f0fd6f83`](https://github.com/rioriost/pg_agmemory/commit/4d97e92ec08d845ebd2c969819a999e9f0fd6f83)
**failed** [CI 35254318489](https://github.com/rioriost/pg_agmemory/actions/runs/35254318489):
amd64 **728 passed, 1 failed, 549.51 s**, while arm64 passed
**729 tests, 626.14 s**. The existing
`test_exact_graph_path_seed_and_entity_evidence_limits` received **503**
with `QueryCanceled` / statement timeout while expanding 100 paths.
That CI execution plan was not captured.

A separate disposable-fixture diagnostic reproduced repeated `relation_revision`
scans under a forced generic prepared plan. Materializing the `adjacent` CTE
removes that scan multiplication while retaining all RLS, temporal, scope,
evidence, ordering, and limit semantics. The diagnostic is not a production
performance benchmark or proof of the uncaptured CI plan.
An added regression uses the existing 100/101-path fixture and verifies actual
generic prepared execution through `pg_prepared_statements.generic_plans > 0`.
No timeout increase, JIT disable, or RLS weakening is used.
Service v0.0.21, API v1, schema 10, and 29 Native/SDK resource methods remain
unchanged; the fix adds no migration, dependency, or provider change.

**Historical initial v21 implementation evidence, not qualification of the fix:**
the full Apple Container `./scripts/test-containers.sh` completed with
**729 passed, 1 existing warning, 390.27 s**.
The total is **695 retained + 34 new cases**: **20 contract**, **7 graph-query**,
and **7 SDK (6 mock + 1 real)**, extending `tests/test_contract.py`,
`tests/test_graphs.py`, and `tests/test_sdk.py`.

Committed and pushed implementation:
[`a34477d7511f22202f0bd981772f51408634a9af`](https://github.com/rioriost/pg_agmemory/commit/a34477d7511f22202f0bd981772f51408634a9af)
(`feat: discover scoped entities with exact metadata pages`).
[CI 35252290223](https://github.com/rioriost/pg_agmemory/actions/runs/35252290223)
passed on that exact SHA for both native Docker architectures:
amd64 **729 passed, 764.95 s**; arm64 **729 passed, 614.98 s**.
Ruff, mypy (**19 source files + 1 strict SDK consumer**), and genuine
core-only/hook-only/sdk-only installation checks passed in all three environments.

All previous non-root production smokes plus **entity query** passed in all
three environments: Japanese tokenizer, HTTP, readiness, worker, both MCP
protocol modes, all three hook events, capture, pgvector, SDK lifecycle,
required context, recall filters, checkpoint head, job cancellation,
owned-job query, scope access, assertion history, and the new entity-query smoke.
Coverage includes an exact **101-entity fixture with timestamp ties and UUID
ordering**, all eight entity types, Japanese/punctuation/case/Unicode distinctions,
readable shared-scope entities owned by another principal, ACL changes/current
epochs, deleted/forged cursors, the **32-scope and 100-item page caps**, no quotes,
and the explicit graph-seed workflow.

The bounded request/filter, current-access, cursor, metadata, and SDK contracts
are unchanged. Schema 10/history 1–10 and dependencies/provider/artifact pins
remain unchanged; v20→v21 has no SQL migration or AGE change.
The initial implementation results remain distinct from the later failed
final-docs CI and the qualified follow-up.
Elapsed time is not a performance benchmark; no MVP, performance,
identity-resolution, memory-quality, production, or DR qualification is claimed.

<a id="v0020--schema-10"></a>

### Historical v0.0.20 / schema 10 — verified

**Final local and native implementation results verified, 2026-09-18 JST:**
the full Apple Container `./scripts/test-containers.sh` completed with
**695 passed, 1 existing warning, 359.74 s**.
The total is **663 retained + 32 new cases**: **16 contract**, **7 revision-history**,
**1 graph missing-endpoint**, **7 SDK (6 mock + 1 real)**, and **1 additional
`assertion_invalidated` case in the existing safe-code parameterization**.
These extend `tests/test_contract.py`, `tests/test_revisions.py`,
`tests/test_graphs.py`, and `tests/test_sdk.py`, not a new test file.

Committed and pushed implementation:
[`6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97`](https://github.com/rioriost/pg_agmemory/commit/6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97)
(`feat: expose bounded assertion revision metadata history`).
[CI 35247519977](https://github.com/rioriost/pg_agmemory/actions/runs/35247519977)
passed on that exact SHA for both native Docker architectures:
amd64 **695 passed, 527.99 s**; arm64 **695 passed, 615.07 s**.
Ruff, mypy (**19 source files + 1 strict SDK consumer**), and genuine
core-only/hook-only/sdk-only installation checks passed in all three environments.

All previous non-root production smokes plus **assertion history** passed in all
three environments: Japanese tokenizer, HTTP, readiness, worker, both MCP
protocol modes, all three hook events, capture, pgvector, SDK lifecycle,
required context, recall filters, checkpoint head, job cancellation,
owned-job query, scope access, and the new assertion-history smoke.
Existing exact-1000-revision coverage now walks **10 explicit pages of 100**
for **both ordinary assertions and canonical relation assertions**.
Existing temporal relation and purge cases were also extended and passed.

The documented request, metadata, current-access, whole-page failure, and SDK
contracts are unchanged from the bounded design. Schema 10/history 1–10 and
dependency/provider/artifact pins are unchanged; v19→v20 requires no SQL migration.
Separate final v0.0.20 docs
[`c8977088d92f060c1b9f2594db6300f79cea3963`](https://github.com/rioriost/pg_agmemory/commit/c8977088d92f060c1b9f2594db6300f79cea3963)
passed [CI 35249560753](https://github.com/rioriost/pg_agmemory/actions/runs/35249560753):
each native architecture **695 cases**, **660.52 s amd64 / 642.26 s arm64**,
with Ruff, mypy **19 + 1**, all optional installs, and all previous production smokes.
These docs timings are separate from implementation CI 35247519977;
neither run qualifies v0.0.21.
Elapsed time is not a performance benchmark; no MVP, performance, memory-quality,
production, or DR qualification is claimed.

<a id="v0019--schema-10"></a>

### Historical v0.0.19 / schema 10 — verified

**Final local and native implementation results verified, 2026-09-18 JST:**
Apple Container `./scripts/test-containers.sh` exited **0** with **663 passed,
1 existing warning, 366.14 s (6:06)**.
The total is **630 retained + 33 new tests**: **16 cases in
`tests/test_contract.py`, 10 in `tests/test_jobs.py`, and 7 in `tests/test_sdk.py`**.
Committed and pushed implementation:
[`e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf`](https://github.com/rioriost/pg_agmemory/commit/e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf)
(`feat: query caller-owned jobs with bounded keyset pages`).
[CI 35242118110](https://github.com/rioriost/pg_agmemory/actions/runs/35242118110)
passed on that exact SHA for native Docker amd64 and arm64.
Actual logs verified **663 passed, 1 warning** each:
**638.06 s amd64 / 586.58 s arm64**.
Ruff, strict mypy (**19 source files + 1 strict SDK consumer**), and genuine
core-only/hook-only/sdk-only installation checks passed in all three environments.

All non-root production smokes passed in all three environments: Japanese
tokenizer, HTTP, readiness, worker, both MCP protocol modes, all three hook events,
capture, pgvector, SDK lifecycle, required context, recall filters, checkpoint head,
job cancellation, **owned-job query**, and scope access.
The new production SDK sequence passed source + three jobs → first pending page
of one item → explicitly cancel the middle job → current next page contains only
the oldest job → query cancelled jobs → source purge `object_count: 4` →
empty pending query. The query itself performs no cancellation or automatic paging.

The full suites passed genuine 100-item pages with 101-row overflow, exclusive
UUID boundaries at equal timestamps, all five states, current state changes,
deleted/forged cursors, current ACLs and scope-admin ownership exclusion,
whole-page GET validation, read-only SQL, three explicit typed SDK pages,
payload non-disclosure, and no automatic pagination.
The SDK also validates the response-model 100-job bound; existing GET shape is unchanged.
Shared `MemoryService.epochs` preserves checkpoint/recall/job-query consistency
without persistence, schema, or mutation-hash changes.
Schema 10 has no SQL migration or dependency/provider/artifact-pin changes.
Separate final v0.0.19 docs
[`12b7628af6ad19b1073600b27a713e8c52789bec`](https://github.com/rioriost/pg_agmemory/commit/12b7628af6ad19b1073600b27a713e8c52789bec)
passed [CI 35244626331](https://github.com/rioriost/pg_agmemory/actions/runs/35244626331):
each native architecture **663 tests**, **554.26 s amd64 / 601.49 s arm64**,
with Ruff, mypy **19 + 1**, all optional installs, and all previous production smokes.
These docs timings are separate from implementation CI 35242118110;
neither run qualifies v0.0.20. Test elapsed is not a performance benchmark;
no performance/MVP/production/DR qualification.

<a id="v0018--schema-10"></a>

### Historical v0.0.18 / schema 10 — verified

**Final local and native implementation results verified, 2026-09-17 JST:** Apple Container
`./scripts/test-containers.sh` exited **0**, with **630 passed, 1 existing warning,
369.39 s (6:09)**. The total is **604 retained + 26 new tests**:
**8 cases in `tests/test_contract.py`, 11 in `tests/test_checkpoints.py`, and
7 in `tests/test_sdk.py`**, not a new test file. Existing historical/CAS/fork/ACL/
purge/large-SDK/effect/OpenAPI/26-route coverage was also extended.
Ruff, strict mypy (**19 source files + 1 strict SDK consumer**), and genuine
core-only/hook-only/sdk-only installation checks passed in all three environments.

All non-root production smokes passed in all three environments: Japanese tokenizer, HTTP, readiness,
worker, both MCP protocol modes, all three hook events, capture, pgvector,
SDK lifecycle, required context, recall filters, **checkpoint-head lookup**,
job cancellation, and scope access.
The new real SDK smoke passed in all three environments: source + two checkpoints → head sequence 2 /
historical GET sequence 1 → source purge `object_count: 3` →
head `409 checkpoint_invalidated`.

The full runs include corrected complete `ErrorBody` SDK fixtures and the
actual-schema tombstoned-head fixture: head lookup gives 404 even while a
surviving ancestor GET gives 200. That deliberately non-invalidated fixture
is not normal `forget` behavior and adds **no object TTL feature**.
Actual read-only SQL head transactions and scope-read-only access passed.
Real committed POST create 201 response loss yielded `outcome_unknown: true`,
with same-key receipt/head recovery; POST head 200 response loss yielded
`outcome_unknown: false` with explicit repeat. Large responses, current
effect-ledger/head/fork parity, and all **26 resource routes** passed.
Source purge retains opaque `head_id`/`sequence`, sets the invalidation flag,
and lookup returns 409 without the retained ID.

Committed and pushed implementation:
[`4accd38408a8384b4376f6250d953bb2fa480ec8`](https://github.com/rioriost/pg_agmemory/commit/4accd38408a8384b4376f6250d953bb2fa480ec8)
(`feat: discover current checkpoint branch heads`).
[CI 35235315016](https://github.com/rioriost/pg_agmemory/actions/runs/35235315016)
passed on this exact implementation SHA for native Docker amd64 and arm64.
Actual logs verified **630 passed, 1 warning** on each architecture:
**663.91 s amd64 / 544.14 s arm64**, together with Ruff, mypy **19 + 1**,
all optional-install checks, and every production smoke listed above.
Separate final v0.0.18 docs
[`3f01faf56563202c40a00d73cee72830022220b1`](https://github.com/rioriost/pg_agmemory/commit/3f01faf56563202c40a00d73cee72830022220b1)
passed [CI 35237907862](https://github.com/rioriost/pg_agmemory/actions/runs/35237907862):
native amd64 **644.25 s**, arm64 **570.20 s**, each **630 tests, 1 warning**.
Ruff, mypy **19 + 1**, all optional installs, and all production smokes passed
on both architectures. These docs results are separate from implementation
CI 35235315016; neither run qualifies v0.0.19.
Schema 10 has no SQL migration;
artifact pins and providers are unchanged. Test elapsed is not a benchmark.
No harness/compaction/MVP or general recovery/production/DR qualification.

<a id="v0017--schema-10"></a>

### Historical v0.0.17 / schema 10 — verified

**Final local and native implementation results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **604 tests,
1 existing warning**. Local `./scripts/test-containers.sh` exited **0**.
The total is **566 retained + 38 new tests**: **37 cases in `tests/test_recall_filters.py`**
and **1 real hook-CLI filter-rejection case**.
Ruff, strict mypy (**19 source files + 1 strict SDK consumer**), and genuine
core-only/hook-only/sdk-only installation checks passed in all three environments.
The consumer explicitly constructs `RecallFilters`.
Published implementation:
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894)
(`feat: add exact structured recall filters`).
[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)
passed on that exact SHA on both native Linux architectures. **Actual logs**,
not only job status, verified each architecture's counts, checks, and smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **321.56 s (5:21)** |
| Docker, native `linux/amd64` | **638.66 s** |
| Docker, native `linux/arm64` | **544.33 s** |

Test elapsed is not a performance benchmark. Separate final v0.0.17 docs
[`5226c81fae7a50a5668109478d5d9f23fc4d7761`](https://github.com/rioriost/pg_agmemory/commit/5226c81fae7a50a5668109478d5d9f23fc4d7761)
passed [CI 35232139680](https://github.com/rioriost/pg_agmemory/actions/runs/35232139680).
Actual native logs verified **604 tests, 1 warning** each:
**465.50 s amd64 / 557.08 s arm64**, Ruff, strict mypy **19 source files + 1 consumer**,
all optional installs, and all production smokes. These docs results are separate
from implementation CI 35230044140; neither run qualifies v0.0.18.

Passing coverage includes all three modes' pre-ranking and coverage filtering,
required-reference mismatch, invalid nested list/string filters, kind-only
overflow in all three modes, Unicode composed/decomposed exactness without
normalization, and OpenAPI schema assertions. The five later-added cases are
included in the final count, not additional tests beyond it.
The shared `Predicate` alias preserves existing `Remember`/`CapturedMemory`
field order and semantics.

All non-root production smokes passed in all three environments: Japanese tokenizer, HTTP,
readiness fault/recovery, worker, both MCP modes, all three hook events, capture,
pgvector, Python SDK, required context, structured recall filters, job
cancellation, and scope access.
The new SDK smoke passed observe + remember → exact three-field match with
`max_items: 1` and no truncation → required source reference conflicting with
the assertion filter (`404 not_found`, read-only `outcome_unknown: false`) →
source purge returning `object_count: 2` → empty filtered read.
The project version advances to 0.0.17; schema 10, dependency versions, and
artifact pins are unchanged. MVP/semantic-quality/performance/production/DR qualification is not claimed.

<a id="v0016--schema-10"></a>

### Historical v0.0.16 / schema 10 — verified

**Final local and native implementation results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **566 tests,
1 existing warning**. Local `./scripts/test-containers.sh` exited **0**.
The total is **535 retained + 31 new tests**: **29 `test_required_context` cases**,
**1 real hook-rejection case**, and **1 SDK safe-code parameter case**.
Ruff, strict mypy (**19 source files + 1 strict SDK consumer**), and genuine
core-only/hook-only/sdk-only installation checks passed in all three environments.
Published implementation:
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57)
(`feat: preserve required recall references within context budgets`).
[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)
passed on that exact SHA on both native Linux architectures. **Actual logs**,
not only job status, verified each architecture's counts, checks, and smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **298.15 s (4:58)** |
| Docker, native `linux/amd64` | **601.73 s** |
| Docker, native `linux/arm64` | **565.34 s** |

Test elapsed is not a performance benchmark. Separate final v0.0.16 docs
[`520990d95718b17ae93a7d5259d600e379991df2`](https://github.com/rioriost/pg_agmemory/commit/520990d95718b17ae93a7d5259d600e379991df2)
passed [CI 35226313891](https://github.com/rioriost/pg_agmemory/actions/runs/35226313891).
Actual native logs verified **566 tests, 1 warning** each:
**476.15 s amd64 / 478.08 s arm64**, Ruff, strict mypy **19 source files + 1 consumer**,
all optional installs, and all production smokes. These docs results are separate
from implementation CI 35224189967; neither run qualifies v0.0.17.

Passing checks include actual SQL with exactly 16 refs, the exact byte boundary
and one byte below it, scope/ACL filtering, default revision 1 with historical
time, revision 2 without latest fallback, source purge, and missing Japanese
projections with `lexical_incomplete`.
Native/SDK and MCP auto/legacy exact parity and error propagation passed.
Required-relation preservation includes entity-UUID/citation byte overhead;
entity wrong-kind `404` and real hook-CLI rejection of well-formed
`required_memory_refs` as an unknown field also passed.

All non-root production smokes passed in all three environments: Japanese tokenizer, HTTP,
readiness, worker, both MCP modes, all three hook events, capture, pgvector,
Python SDK, required-context recall, job cancellation, and scope access.
The required-context smoke ran after SDK and before cancellation, using its own
source and an optional `Gold` match. Required query bypass with `max_items: 1`
and truncation passed; explicit budget 64 produced `budget_exhausted` with
read-only `outcome_unknown: false`, followed by source purge returning `object_count: 2`.
Only the project version advances to 0.0.16: schema 10, dependencies, and pinned
artifacts are unchanged. MVP/semantic-quality/performance/production/DR limitations remain.

<a id="v0015--schema-10"></a>

### Historical v0.0.15 / schema 10 — verified

**Final local and native implementation results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **535 tests,
1 existing warning**. Local `./scripts/test-containers.sh` exited **0**.
The total is **495 retained + 40 new tests**; no unit/integration split is claimed.
Ruff, strict mypy (**19 source files + 1 strict SDK consumer**), and genuine
core-only/hook-only/sdk-only installation checks passed in all three environments.
Published implementation:
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)
(`feat: add fenced durable job cancellation`).
[CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)
passed on that exact SHA on both native Linux architectures. **Actual logs**,
not only job status, verified each architecture's counts, checks, and smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **298.29 s (4:58)** |
| Docker, native `linux/amd64` | **406.88 s** |
| Docker, native `linux/arm64` | **490.57 s** |

Test elapsed is not a performance benchmark. Separate final v0.0.15 docs
[`9d34d5329c9db580e7de0459b743511235ad6fb8`](https://github.com/rioriost/pg_agmemory/commit/9d34d5329c9db580e7de0459b743511235ad6fb8)
passed [CI 35218254940](https://github.com/rioriost/pg_agmemory/actions/runs/35218254940).
Actual native logs verified **535 tests, 1 warning** per architecture,
**605.83 s amd64 / 473.57 s arm64**, Ruff, strict mypy **19 source files + 1 consumer**,
genuine optional installs, and all production smokes.
These docs results are distinct from implementation CI 35216770999 above.
Neither v0.0.15 run validates v0.0.16.
All production smokes passed in all three environments: Japanese tokenizer, HTTP, readiness,
worker, modern/legacy MCP, all three hook events, capture, pgvector, Python SDK,
explicit job cancellation, and scope access.
The cancellation smoke used actual SDK enqueue → cancel → same-key replay →
GET cancelled → worker `--once` reporting idle → source purge.
That fixture's purge returned `object_count: 2`.

Job-cancellation DB/CAS/race/ownership/purge/rollback/worker `lease_lost` checks
and SDK coverage of all **25 resource routes** passed.
Actual committed HTTP 200 cancellation-response loss produced
`outcome_unknown: true`, and identical key/body replay recovered the receipt.
A different key returned `409 job_cancel_conflict` with `outcome_unknown: false`.
Malformed/wrong-status cancellation responses remained outcome-unknown, while
invalid keys, including `None`, failed before network access.
Internal SDK POSTs explicitly classify mutations independently of success status.

Schema-9→10 ledger failure after DDL restored the prior guard function,
constraints, and schema-9 history before successful retry. Legacy v6 job
state, attempt, and payload remained unchanged.
Existing MVP/production/quality/DR limitations remain.

<a id="v0014--schema-9"></a>

### Historical v0.0.14 / schema 9 — verified

**Final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **495 tests,
1 existing warning**, Ruff, strict mypy (**19 source files + 1 separate strict SDK
consumer**), and genuine core-only/hook-only/sdk-only installation checks.
Local `./scripts/test-containers.sh` exited **0**.
The total is **464 retained + 16 readiness unit + 15 integration tests (31 new)**.
Schema 9 is unchanged; no migration was added.
Published implementation:
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
(`feat: add bounded runtime readiness probe`).
[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)
passed on that exact SHA on both native Linux architectures. **Actual logs**,
not only job status, verified each architecture's counts, checks, and smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **295.67 s (4:55)** |
| Docker, native `linux/amd64` | **385.41 s** |
| Docker, native `linux/arm64` | **470.16 s** |

Test elapsed is not a performance benchmark.
The separate final v0.0.14 docs
[`d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68`](https://github.com/rioriost/pg_agmemory/commit/d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68)
passed [CI 35202931424](https://github.com/rioriost/pg_agmemory/actions/runs/35202931424).
Actual native logs verified **495 tests, 1 warning** per architecture, Ruff,
strict mypy **19 source files + 1 SDK consumer**, optional installs, and all
production smokes: **319.51 s amd64 / 503.56 s arm64**.
These docs timings are separate from implementation CI 35201615965 above.
Neither v0.0.14 run validates v0.0.15/schema 10.

The real 5 s locked-schema timeout check passed within its 4.5–10 s assertion
window; this is not a wall-clock SLA. Cancellation left no runtime backend leak;
connection-refusal recovery and retained liveness also passed.
Passing DB variants include `NOINHERIT` owner-role membership, schema-ledger
SELECT revocation/restoration, and extension namespace move/restoration.

All non-root production smokes passed in all three environments: Japanese tokenizer, HTTP/liveness,
readiness schema-fault/recovery, worker, MCP **2026-07-28/2025-11-25**, all three
hook events, capture, pgvector, Python SDK, and scope-access.
The passing disposable-DB readiness smoke uses the **same API process**:
**ready 200 → rename schema ledger → ready 503 while health stays 200 →
restore ledger → ready 200**. It follows the normal HTTP smoke, with subsequent
Native/SDK smokes unchanged and no source/tombstone writes.
This lifecycle passed in all three environments; never rehearse drift on a live DB.
No MVP/production/performance/quality/DR/full-erasure
qualification is claimed.

<a id="v0013--schema-9"></a>

### Historical v0.0.13 / schema 9 — verified

**Final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **464 tests,
1 existing warning**, Ruff, strict mypy (**19 source files** plus the separate
**1-file SDK consumer**), and genuine core-only/hook-only/sdk-only installation
checks. The total is **426 retained + 22 scope-admin unit + 16 integration
tests (38 new)**. Local `./scripts/test-containers.sh` exited **0**.
Published implementation:
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413).
[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)
passed on that exact SHA on both native Linux architectures. **Actual logs**,
not only job status, verified each architecture's counts, checks, and smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **297.52 s (4:57)** |
| Docker, native `linux/amd64` | **539.86 s** |
| Docker, native `linux/arm64` | **460.73 s** |

Test elapsed is not a performance benchmark.
The separate final v0.0.13 docs
[`185f433aa49479810b8955f1bec2e856f2715f7b`](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)
passed [CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967).
Actual native logs verified **464 tests, 1 warning** per architecture, Ruff,
strict mypy **19 source files + 1 consumer**, optional-install checks, and all
production smokes: **499.25 s amd64 / 454.37 s arm64**.
Those docs timings are separate from implementation CI 35196930448 above.
Neither run validates v0.0.14.

Passing integration covers nonowner `BYPASSRLS` inspection without `FOR UPDATE`,
maximum-epoch change rejection/no-op behavior, schema-8→9 ledger failure after
DDL, transactional rollback and retry, and prior migrations. It also covers
holding the output lock after real
commit, consumer-failure connection cleanup, and injected `OperationalError`
after a real commit: the stored ACL/audit remained committed,
`outcome_unknown` was true, and replaying the stale expected epoch conflicted.
Passing cases include a real 5 s lock timeout with no change, cross-scope
tenant-global CAS with a legacy empty-permission row, and schema-9 legacy-ACL
preservation/no audit backfill/forced RLS/capabilities.

All non-root production smokes passed in all three environments: Japanese tokenizer, HTTP API,
worker `--once` idle, MCP **2026-07-28/2025-11-25**, all three hook events, capture,
pgvector, Python SDK, and scope-access.
The scope-access CLI subprocess smoke uses dedicated admin
credentials for **get → CAS set read-only → SDK read succeeds / write replay 404 →
CAS revoke → SDK empty recall → get inactive**. Admin credentials belong only
to the administrative subprocess, not the SDK/API. This verified lifecycle is
not external ACL-provider integration. Production/quality/performance/DR/full-erasure
gates remain incomplete.

<a id="v0012--schema-8"></a>

### Historical v0.0.12 / schema 8 — verified

**Final local and native results verified, 2026-09-17 JST:**
Apple Container and native Docker amd64/arm64 each passed **426 tests,
1 existing warning**, Ruff, strict mypy for **18 source files**, and the separate
strict typed consumer for **1 file** covering all 24 method annotations.
All three genuine **core-only/hook-only/sdk-only noneditable wheel-install
checks** passed, including packaged `py.typed` and absence of MCP from hook/sdk-only
installs. All checks passed in all three environments.
The suite consists of **345 retained + 76 SDK unit + 5 SDK integration tests (81 new)**.
These are final results, not the superseded fixture-key failure.
Published implementation:
[`88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f`](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)
(`feat: add typed asynchronous Native Python SDK`).
[CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945)
passed on that exact SHA. **Actual logs**, not only job status, verified each
native architecture's SHA, counts, checks, and production smokes.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **292.76 s** |
| Docker, native `linux/amd64` | **484.79 s** |
| Docker, native `linux/arm64` | **472.49 s** |

Elapsed times are test observations, not performance benchmarks.

Final v0.0.12 documentation
[0e00abd](https://github.com/rioriost/pg_agmemory/commit/0e00abdae930dcc1e2d2015fbf5931a74701fe7d)
passed [CI 35194141510](https://github.com/rioriost/pg_agmemory/actions/runs/35194141510).
Actual native logs verified **426 tests per architecture**, all checks and smokes;
docs-run elapsed was **488.20 s amd64 / 454.88 s arm64**.
This is distinct from implementation CI 35193004945 and the timings above.
Neither v0.0.12 run validates v0.0.13/schema 9.

All five SDK integration tests passed over actual HTTP against disposable
PostgreSQL databases in all three environments, covering all 24 resources:
graph/relation revision, failed-job retry with an actual worker,
checkpoint create/read above 256 KiB with tool-effect restore reconciliation,
authorization revocation, and real post-commit response loss with same-key recovery.
Passing unit checks include entry-cancellation cleanup, single-use after
failed entry, exit after a caller-body exception, 256-character keys, exact GET
success status, and wrong response shapes for both forget modes.
All non-root production smokes passed in all three environments:
Japanese tokenizer, HTTP API,
actual worker `--once` idle, MCP **2026-07-28/2025-11-25**, all three hook events,
atomic capture with an actual worker, pgvector exact/hybrid retrieval, and the
new Python SDK lifecycle.
The SDK smoke performs **capture/replay →
pending `get_job` → embedding input/upload → exact vector recall → preview/purge →
deletion progress → capture replay 404**. **It does not invoke a worker**;
the earlier actual capture-worker smoke is retained separately.
The SDK smoke passed locally and on both native architectures.
The implementation and final-docs runs above are distinct historical evidence,
not v0.0.13 qualification. M0–M3/MVP/production/performance/quality/DR/full-erasure gates remain incomplete.
Historical v0.0.11 evidence below does not qualify SDK changes.

<a id="v0011--schema-8"></a>

### Historical v0.0.11 / schema 8 — verified

**Final local and native results verified 2026-09-17 JST.** Implementation
[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)
passed [CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403)
on that exact SHA. Apple Container and both native Docker jobs each passed
**345 tests, 1 existing warning**, Ruff, strict mypy (**17 source files**),
core-only/hook-only installation checks, and all non-root production smokes:
Japanese tokenizer, HTTP API, worker, MCP **2026-07-28/2025-11-25**, all three
hook events, atomic capture lifecycle, and pgvector exact/hybrid retrieval and purge.

| Environment | Test elapsed |
|---|---|
| Local Apple Container | **283.44 s** |
| Docker, native `linux/amd64` | **404.40 s** |
| Docker, native `linux/arm64` | **433.46 s** |

Final v0.0.11 documentation
[dccd5cb](https://github.com/rioriost/pg_agmemory/commit/dccd5cb5571873515aace8621ce4adb3de250d3a)
passed [CI 35190495385](https://github.com/rioriost/pg_agmemory/actions/runs/35190495385).
Actual logs verified **345 tests, 1 warning** per native architecture, Ruff,
strict mypy (**17 source files**), installation checks, and all smokes.
Docs-run elapsed: **506.38 s amd64 / 460.18 s arm64**.
These are distinct from implementation CI 35189448403 and its local/native
timings above; neither run validates v0.0.12.

Actual logs, not only job status, establish these results. Test elapsed is not
a performance benchmark. Artifact inspection separately verified the pinned
upstream pgvector 0.8.6 profile on both architectures. Passing checks cover the new
DB profile with schema-8 migration/role/extension-version/schema guards, canonical digest
binding, float normalization/immutability/model isolation/eight-model cap,
deterministic exact/RRF mathematics, pre-ranking ACL/time filters, coverage,
purge/replay, and retained lexical/MCP/hook/capture behavior.
Implemented fixtures additionally cover DB norm/dimension/composite-FK/eight-model
guards, direct RLS visibility and denied updates, ACL revocation, and actual
schema-7→8 migration ledger-failure rollback of DDL/extension followed by retry,
without embedding backfill. The production vector smoke uploads **both episode
and assertion projections**, checks basis-vector distances **[0, 1]** and RRF,
then purges the source and checks upload replay `404`. These checks passed in all
three environments.
Tests and the synthetic basis-vector example cannot establish semantic quality,
production performance, provider provenance, or robustness to untrusted vectors.
No source-build workflow is part of the adopted profile.
All original M0–M3/MVP/production/DR/full-erasure/performance/quality gates remain incomplete.

<a id="v0010--schema-7"></a>

### Historical v0.0.10 / schema 7 — verified

**Final local Apple Container and native CI results verified 2026-09-17 JST.**
The final local source matches published implementation
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f).
Both native jobs in
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)
passed. Actual logs verified the exact SHA, test counts, timings, and checks
below, not just the job status.

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | **304 passed, 1 existing warning** | **275.53 s** |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | **304 passed, 1 existing warning** | **467.75 s** |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | **304 passed, 1 existing warning** | **434.40 s** |

All three final runs also passed **Ruff, strict mypy (16 source files), genuine
core-only/hook-only installation checks, and every non-root production smoke**:
Japanese tokenizer, API HTTP, worker CLI, MCP in **`2026-07-28` and `2025-11-25`
modes**, all three hook events (**`session_start`, `task_switch`,
`after_compaction`**), and atomic capture. Timings are test observations,
not performance benchmarks.

Coverage includes rollback faults after either write and the outer receipt,
source/key deduplication races, quota, RLS/deletion, and API process restart with
an actual worker. The production smoke passed in all three environments.
For a fresh fixture, after MCP/hook checks, it exercises Native capture →
pending job → actual worker CLI `--once` → recall of the episode/assertion pair →
same capture replay → source purge → job GET `404` and capture replay `404`.
The final suites also cover actual HTTP 201 response loss after commit:
same-key retry returns the exact episode/job pair with only one publication.
It also checks that a failed original capture job remains the replay target
after explicit creation of its retry child. These regressions passed locally
and on both native Docker architectures.
No dependency is added; project v0.0.10 lock metadata changes only.
All M0–M3/MVP/production/performance/quality/DR/full-erasure gates remain incomplete.
Final v0.0.10 documentation
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)
also passed **304 tests per native architecture** in
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760).
That docs run is distinct from the implementation-run timings above.
Neither run validates v0.0.11/schema 8.

<a id="v009--schema-7"></a>

### Historical v0.0.9 / schema 7

**Final local and native CI results verified on 2026-09-17 JST.**
The tested final local source matches published implementation
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050).
Both native jobs in
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)
passed; their actual logs verified the exact SHA, counts, and checks below,
not just the job status.
No v0.0.7/v0.0.8 result is reused as v0.0.9 evidence.

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | **274 passed, 1 existing warning** | **248.29 s** |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | **274 passed, 1 existing warning** | **482.21 s** |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | **274 passed, 1 existing warning** | **374.33 s** |

All three final runs also passed **Ruff, strict mypy (15 source files), genuine
core-only/hook-only installation checks, and all non-root production smokes**:
Japanese tokenizer, API HTTP, worker CLI, MCP in **both `2026-07-28` and
`2025-11-25` modes**, and recall-hook for **`session_start`, `task_switch`,
and `after_compaction`**.
The suite covers the extracted shared `native_client.py`, hook validation and
failure handling, entire-pack byte accounting, missing-index
`index_incomplete`/incomplete coverage, budget outcomes, silent scope/revocation
filtering, and explicit token-authentication failures, while retaining Native
and MCP semantics. Test elapsed time is an observation, not a performance benchmark.

The suite also covers shared `NativeSettings` origin validation with `httpx.URL`,
rejecting control characters and invalid IDNA before transport.

The automated Docker **`adapter-extras-check`** target checks genuine core-only
installation/missing extras, then hook-only **without MCP**, including explicit
JSON for failed HTTP. The container script builds it on local Apple Container
and both native Docker architectures. These genuine installation and failed-HTTP
checks **passed in all three environments**, along with the production smokes above.
Full M0–M3/MVP/production/performance/quality/DR/full-erasure gates remain incomplete.

The final v0.0.9 documentation commit
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)
also passed **274 tests** in each native architecture in
[CI run 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689).
This final-docs run is distinct from the implementation-run timings above.
Neither v0.0.9 run validates v0.0.10 atomic capture.

<a id="v008--schema-7"></a>

### Historical v0.0.8 / schema 7

Implementation
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)
was verified on **2026-09-17 JST**:

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | 214 passed, 1 existing warning | 240.83 s |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | 214 passed, 1 existing warning | 415.46 s |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | 214 passed, 1 existing warning | 389.47 s |

All three passed **Ruff, strict mypy (13 source files), and non-root production
Japanese tokenizer, API HTTP, worker CLI, and MCP stdio smokes**. MCP checks cover
both `2026-07-28` and `2025-11-25`, using SDK 2.2.0 and separate raw wire fixtures.
Both jobs in [CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)
ran the exact SHA above; actual logs verified counts and smokes. Timings are
test observations, not performance benchmarks. The remaining warning concerns
the existing anyio BlockingPortal alias.

A separate fresh core-only install (`uv sync --frozen --no-dev --no-editable`)
was verified in Apple Container: Native API import succeeded with neither `mcp`
nor `httpx` installed, and `pg-agmemory mcp` exited with the explicit missing-extra
diagnostic. This additional check was local, not a separate Docker CI assertion.
All M0–M3/MVP/production/performance/quality/DR/full-erasure acceptance gates
remain incomplete. Historical v0.0.7 results below do not validate MCP.

The subsequent bilingual documentation commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)
passed **214 tests in each native architecture** (`linux/amd64`, `linux/arm64`) in
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509).
This is the v0.0.8 documentation run, separate from the implementation-run
timings above. Neither run validates v0.0.9 or the recall hook.

### Historical v0.0.7 / schema 7

For **v0.0.7/schema 7 only**, implementation commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473),
final results were verified on **2026-09-17 JST**:

| Environment | Command | Tests | Test elapsed |
|---|---|---|---|
| Local Apple Container | `./scripts/test-containers.sh` | 144 passed, 2 existing warnings | 206.54 s |
| Docker, native `linux/amd64` | `./scripts/test-containers.sh docker` | 144 passed, 2 existing warnings | 386.32 s |
| Docker, native `linux/arm64` | `./scripts/test-containers.sh docker` | 144 passed, 2 existing warnings | 331.59 s |

All three final runs also passed **Ruff, strict mypy (12 source files), and all
three non-root production smokes: Japanese tokenizer, API HTTP, and actual CLI
worker**. Both native Docker jobs in
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)
ran the exact SHA above; actual logs verified the counts and checks, not just job
status. Elapsed times are test-run observations, not performance benchmarks.
The final bilingual documentation commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)
also passed both native jobs in
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899).
That is the historical final-docs CI run, distinct from the implementation-run
timings above; neither run validates v0.0.8 or v0.0.9.

Coverage includes default/opt-in lexical behavior, Japanese/ASCII handling,
exact 65,536-character indexing and 65,537-character rejection, lazy loading and
the fresh Linux initialization guard, temporal/RLS and incomplete-index/budget
behavior, and canonical purge without child DELETE grants. It also covers
schema-6 rollback after actual initial backfill, preservation of old projections
after partial reindex failure, rejected reindex scope/worker flags, and retained
job/graph/effect/checkpoint and legacy idempotency behavior. Exact historical
checks use server-recorded assertion times, not VM wall-clock samples.

The tokenizer smoke checks `東京都` → `東京` / `都` and logs
`Production Japanese tokenizer smoke passed`; API smoke checks HTTP health.
For worker smoke, a disposable principal ran actual
`pg-agmemory worker --subject ... --once` with runtime-only credentials in the
non-root production image, verified
`{"outcome":"idle"}`, and logged `Production worker smoke passed`.
The CI step is `Test containers and smoke-test production API and worker`.
These smokes check packaged tokenizer behavior, API liveness, and worker startup/
idle execution, not end-to-end recall quality or queued-publication correctness;
publication behavior is covered separately by the test suite.

The v0.0.7 final lock retained the prior package-feed registry. All **36 packages'**
versions, dependency metadata, and artifact hashes were verified byte-for-byte
equivalent to the tested PyPI-resolved lock. Relative to v6, only Janome 0.5.0
was added and the project version changed to v0.0.7; there were no unrelated
upgrades or registry migration. Native CI built the final retained-registry lock.
This package count/comparison is historical, not a claim about the v0.0.8 MCP lock.

Earlier v5 evidence remains historical in [ADR 0005](adr/0005-relational-graph.md);
v6 decisions/evidence remain in [ADR 0006](adr/0006-durable-jobs.md).
Checks do not establish complete M0/M1/M2/M3, measured performance/quality, external
exactly-once behavior, an MVP, production readiness, backup/DR, or full-erasure qualification.

## Still roadmap work

Automatic enqueue/NL extraction/synthesis, automatic LLM/provider memory processing, global
multi-tenant scheduling/fairness/cost pools, separate working snapshots/
compaction, automatic embedding/provider integration, ANN/HNSW, qualified vector/hybrid
retrieval quality and performance, AGE, SQL/PGQ,
provider receipt verification, vendor-specific harness integration and execution/recovery,
cross-assertion supersession/fact arbitration, remote MCP HTTP/SSE/OAuth/delegation,
synchronous/TypeScript SDKs, and postgresem integration are absent.
The v0.0.12 async Python SDK is verified locally and on both native architectures. The vendor-neutral hook
does not register or qualify any host. Local stdio MCP,
opt-in lexical segmentation, explicit structured jobs, the bounded SQL
graph oracle, and typed checkpoint envelopes do not complete the planned
bitemporal, graph, provenance, or deletion architecture.

See [ADR 0001](adr/0001-initial-slice.md) for these choices,
[operations](operations/README.md) for safe administration, and
[contributing](../CONTRIBUTING.md) for the container validation workflow.
