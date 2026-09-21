# Evaluation contract and evidence boundaries

[日本語](EVALUATION-jp.md)

## Current status: v0.0.34 / schema 18, M2 acceptance incomplete

**Scope revision, 2026-09-19:** the [implementation plan, sections 1.3 and 17–18](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)
now governs acceptance. pg_agmemory is memory infrastructure, not a judgment
system. Human semantic scores and 20 successful agent tasks are not release
gates; neither model comparison nor a new human-label campaign is planned.
One pinned reference memory benchmark remains required as an example, without
a model-quality pass score. The final section lists current engineering gaps.

The experiment results, score thresholds and harness recipes in the historical section below
are **historical diagnostics through 2026-09-18**, not the revised release policy.
Do not rerun all baseline arms or fill review forms merely to satisfy old gates.
Existing `NOT_MEASURED`, `human_review_verified=false`, `human_quality_qualified=false`
and `m2_qualified=false` fields remain truthful and unchanged; they do not replace
the versioned core acceptance inventory. No semantic measurement is invented.

### M2-A core contract inventory (2026-09-19)

| Boundary / actual surface | Deterministic obligation | Regression evidence |
|---|---|---|
| Observe, structured remember, capture/batch, explicit revisions | Atomic durable admission, idempotency, source/revision binding, caller intent; no implicit model job | `test_integration`, `test_capture`, `test_capture_policy`, `test_revisions` |
| Recall/episode/entity/history/explain, SQL graph | Current tenant/scope/time filters, exact vector-space and RRF rules, bounded whole-item context, provenance; no answer guarantee | `test_recall_filters`, `test_episodes`, `test_graphs`, `test_vectors`, `test_lexical`, `test_required_context` |
| Processing/adoption/worker/jobs | Default-deny profile/policy, durable reservations, invalid-output rejection, lease/epoch fencing, explicit adoption without human-verification claim | `test_processing`, `test_jobs`, `test_extraction`, `test_providers`, `test_processing_chaos` |
| Checkpoint/effect/working compaction/restore | Typed-state preservation, checksum/head CAS, coverage/tail, current permissions, no approval inference or blind side-effect replay | `test_checkpoints`, `test_effects`, compaction cases in `test_processing`, `test_recall_hook` |
| Native SDK, MCP, hook | Same authorization and declared versions; MCP exposes four tools, not every Native operation | `test_sdk`, `test_mcp`, `test_recall_hook`, `test_contract`, `test_readiness`, packaged smokes |
| Scope/capture/synthesis administration | Privileged connection, CAS, caller barrier, default-deny egress and immutable runtime policy | `test_scope_access`, `test_capture_policy`, `test_processing` |
| Forget/receipt/deletion-history export | Native preview/purge only; full dependency closure, barrier, exact per-receipt target recording, sealed manifests, explicit refusal of unmapped history | `test_integration`, purge cases across domain tests, new `test_deletion_history`, `test_recovery_drill` |
| Processing-recovery export/check | Complete fixed-table, epoch and keyed-lineage comparison; explicit mismatch, no payload export, no DB changes or restart authorization | `test_processing_recovery`, v3 actual backup drill, packaged admin CLI smoke |
| Recovery-apply export/apply | Admin-only authenticated bundle, exact target CAS/content, monotonic accounting/history, all-or-nothing application and post-verification; no automatic restart | `test_recovery_apply`, migration-15 rollback/runtime-role cases, v4 backup/application CLI drill |

This inventory describes tested contracts, not universal assurance. The last
schema-13 exact-SHA baseline is `1ea3f6c` (1,754 passes / 8 optional skips per
native architecture). Schema-14 development passed 198 integration-focused
cases, followed by 83 final focused cases including the early-constraint
overfill regression. An earlier schema-14 worktree distribution run passed
1,783 tests / 8 optional skips and all packaged smokes; that run predates the
ordinal guard and packaged export smoke, so is not final-source qualification.
Do not add overlapping counts. Native exact-commit qualification is recorded
separately after publication.

Exact implementation **`99e71bd74445c2eab6fb82fe62c25b1678cdc69b`** subsequently
passed the full local distribution run: **1,784 passed / 8 optional live skips**,
550.73 seconds. [Native CI 35445005807](https://github.com/rioriost/pg_agmemory/actions/runs/35445005807)
passed both architectures with the same 1,784/8 counts (amd64 1022.91s, arm64
967.58s), all packaged smokes including `deletion-history export`, and actual
single-purge backup recovery. That drill retained `m2_qualified=false`, checked
four manifest targets and 35 matching canonical table fingerprints, and made no
model calls. Its 46 contract cases overlap the full suite; do not add them.
The initial focused development attempt had 65 passes / 2 failures from incorrect
test assumptions about Native suppress support and preview HTTP status; these
were corrected without enabling suppress or changing preview behavior.

Legacy evaluation/QA/human-report schemas remain diagnostic contracts.
Their hard-coded `m2_qualified=false` / quality `NOT_MEASURED` fields are not
used to block core engineering or flipped to claim a measurement. A release
decision instead requires evidence for the revised M2-B/C/D obligations below.
No new model-quality scorer or human-review campaign is needed.

### Bounded multi-receipt recovery (2026-09-20)

Exact code `84871e085131aa673543ac9455386874d9eadf77` passed 64 local offline
recovery contracts and an actual old-backup restore after source-cluster removal.
[Native CI 35479478777](https://github.com/rioriost/pg_agmemory/actions/runs/35479478777)
passed **1,802 tests / 8 optional live skips** on both amd64 (1012.79s) and arm64
(975.62s), including all production smokes and the same multi-receipt drill.
The 64 cases overlap the full suite and are not added to its total.

Local/amd64/arm64 recovery reports each set `exact_commit_inputs=true`: one
pre-backup receipt retained, two subsequent purge receipts replayed, two ordered
ACL changes applied, six tombstones checked, 35 latest/restored canonical
fingerprints equal, revoked reader denied and live control retained.
Original-to-replayed suffix receipt IDs are recorded rather than falsely
claiming full audit/idempotency identity. No API/worker or model call is started.
V2 metadata and complete prefix/target validation reject unsupported histories;
the limits in [operations](operations/README.md#bounded-multi-receipt-recovery-drill-2026-09-20)
apply. Policy/model-accounting, arbitrary histories, full derivative coverage,
HA/PITR and M2 as a whole are still unqualified.

### Read-only processing-state comparison (2026-09-20)

Exact implementation **`d3b1b222784c544410bb9b4eda956e7b602bda62`** is service
0.0.29 / schema 14. Local focused qualification passed **109 cases**, including
44 new processing-state cases and 65 recovery-drill cases; actual local backup
restoration passed separately. The real PostgreSQL cases reserve unknown calls
and exercise synthetic known-failure/success outcomes, identity retention and
policy-budget changes. They do not measure a live model's quality or provider billing.

[Native CI 35483209713](https://github.com/rioriost/pg_agmemory/actions/runs/35483209713)
passed **1,847 tests / 8 optional live skips** on amd64 (1017.45s) and arm64
(1022.56s), all packaged smokes including the new administrative CLI, and the
actual v3 backup drill. The focused cases are included in the full count.
Local and both native reports bind exact commit inputs, match the restored old
processing baseline, and detect five latest-state differences after bounded replay:
scope-access events, idempotency, tombstones, deletion receipts and deletion targets.
This is **successful mismatch detection**, not successful latest-state application.
The 35 canonical fingerprints still match; generated operational IDs/timestamps
do not. `restore_authorized=false` and `m2_qualified=false` remain mandatory here.
Initial development had one test-fixture constructor-argument failure, corrected
without changing provider behavior; the final exact-source runs above passed.

### Atomic operational-state application (2026-09-20)

Exact code **`21187702c43aff55c83341aa45f0de4d285fd64e`**, service 0.0.30 /
schema 15, passed 81 focused local cases and an actual v4 restore/application
drill. The final local packaged run passed **1,861 tests / 8 optional skips**
in 551.02s. [Native CI 35498710001](https://github.com/rioriost/pg_agmemory/actions/runs/35498710001)
passed the same counts on amd64 (975.19s) and arm64 (1000.80s), with every production
smoke and the actual packaged apply CLI. The 81 focused cases and separate 65
drill contracts overlap the full count. An earlier worktree distribution run was
cancelled before final admin-key hardening; it is not qualification evidence.

Each final local/native recovery report has `exact_commit_inputs=true`, 35
matching canonical fingerprints, **21 matching operational fingerprints**, six
tombstones, three original receipt IDs and three durable synthetic reservations
(unknown, failed, succeeded). Unknown retry is denied, duplicate semantic jobs
retain their IDs, and a fresh call is denied at the already-consumed quota.
The probes roll back and leave the authenticated reference state unchanged.
The restored old baseline also matches exactly. The admin-only recovery key
survives the logical backup; runtime roles cannot read it or enable historical writes.

The transition from the earlier v3 mismatch is genuine state application, not a
relaxed comparator. The apply transaction preserves original operational rows
and verifies the same strict comparison afterward. Synthetic provider invocations
are three; **external model requests are zero**. No model-quality result or
provider-billing observation is implied. Content mismatch, changed job sets,
legacy gaps and oversized bundles remain explicit unsupported cases; general
audit history/sequences and production HA/PITR are not certified. `m2_qualified=false`
and manual deployment approval remain in force.

### Native deletion/limit and guest-cold probes (2026-09-21)

Exact implementation **`51293b4bc743c97130e45d70d2f9350a079178d0`**
(service 0.0.34/schema 18) restores the retained full-S schema-16 snapshot into
an isolated cluster, applies ordinary migrations, and records `ANALYZE` before
the probes and before the cold restarts. It uses the same shared M4 Max host and
6-vCPU/24-GiB DB plus 2-vCPU/8-GiB API/two-worker allocation, with a separate
client and controlled local provider. No real model calls or billing are measured.
Probe plan digest:
`f50ec1733095062ffa3b4726ccdfb5497fa705c028fce0855caf98315270e4b2`.

| Probe | Exact result |
|---|---|
| Small Native purge | 100 samples, concurrency ceiling 5; transaction p95 **125.22 ms**, E2E p95 **127.80 ms**, below unchanged 1,000 ms |
| Concurrent background window | 30 s, 600 recall + 150 observe, two workers; 150 succeeded jobs, zero stale-context rejections in this run |
| Read barrier / replay | 100 post-purge denials; 100 identical idempotent receipt replays |
| Large closure | One source plus 9,999 derived assertions: preview **1.08 s**, purge **4.43 s**, below unchanged 900 s; 10k tombstones/manifest targets and no canonical source/assertion payloads left |
| Input / body bounds | 20 oversized processing inputs rejected with no durable changes; HTTP 413 for oversized body |
| Queue / calls | 20 concurrent admissions: two accepted, 18 rejected, no calls while paused; ten jobs under call quota one produced exactly one reservation |
| Output / context bounds | 256-token profile rejected against 128-token policy before reservation/egress; 20 bounded 512-byte recall packs; oversized implicit budget rejected |
| Unknown accounting | Malformed controlled response retains unknown call; explicit retry refused with HTTP 409 |
| Database wait bound | Four blocked requests fail explicitly with HTTP 503 in **5.03 s**; recall succeeds after lock release |
| Guest-cold retrieval | 12 distinct guest/postmaster starts, three modes × four selectivities; first transaction **122.10–242.90 ms**; 60 subsequent samples **≤147.01 ms** |

Cold sampling happens after the large purge. It proves distinct Linux guest and
PostgreSQL startup state, not physical host/device-cache eviction. One first
sample per stratum is **not a cold p95**, and this 30-second deletion window is
not the 30-minute steady gate. The exact 51293b4 full steady run also completed
the unchanged **1,800-second S window**: 36,000 recall and 9,000 observe requests,
zero invalid responses/missing timings/drops, and all 9,300 warmup+steady jobs
succeeded. Observe transaction/E2E p95 was **37.08/42.63 ms**; recall
**106.24/110.47 ms**. All 12 strata had 3,000 samples; worst transaction p95
was **124.03 ms**, vector at 100%. Every steady gate passed.

The safer unique-key projection plan is not uniformly faster than the old
empty-tombstone steady baseline (recall p95 was 76.98 ms at 9c7db01); it bounds
the measured deletion-sensitive cases while retaining the 500-ms gate.
DB size was 926,176,959 → 993,900,223 bytes, indexes 184,778,752 bytes,
WAL growth 552,113,776 bytes and logical backup 467,449,947 bytes.
Worker queue/processing p95 was 399.84/84.56 ms. Sampled guest nonavailable-memory
peaks were 1,785,622,528 bytes (DB) and 408,043,520 bytes (application), not process
RSS; full sampled-interval CPU busy seconds were 2604.91/1514.26.

[Native run 35558748669](https://github.com/rioriost/pg_agmemory/actions/runs/35558748669)
passed **1,940 tests / 8 optional skips on each architecture**. Arm64 also
completed all distribution/production/recovery smokes. Amd64 reached the old
25-minute CI ceiling during the subsequent smokes, so the overall run is
**cancelled, not passed**. The workflow ceiling is now 40 minutes to cover the
expanded suite plus packaging/recovery; product latency, DB timeout and S
duration limits are unchanged. Final dual-architecture distribution completion
remains pending.
Reports retain `resource_qualified=false`/`m2_qualified=false`; broader recovery,
deployment and reference-benchmark work is not certified by these probes.

Preserved non-passing evidence matters:
`6beb38c` failed mixed-load deletion because a post-tombstone projection join
compared about 100 million pairs, saturating DB connections. Disabling JIT alone
did not fix it. Schema 17 changed the equivalent tenant-local tombstone set;
unique-key embedding lookups then removed low-selectivity projection cross scans.
The first lookup run (`00f7854`) stopped on two `stale_context` worker rejections:
the harness incorrectly demanded all workers succeed while concurrent purges
changed their captured deletion epoch. The revised plan counts verified
epoch-fenced, unpublished, known-accounting rejections separately, not as
success. `cacb47b` recorded 149 successes plus one such rejection, but still
had post-large-purge warm samples above 500 ms. Schema 18 removes the remaining
scalar tombstone membership calls without changing read/admin/expiry rules.
All old failed/intermediate artifacts remain separate from the final run.

### Frozen S resource measurements and recall correction (2026-09-20)

Instrumentation checkpoint `1d898c999379422f84d89043b0ae83ace734976e` passed
[native CI 35508695585](https://github.com/rioriost/pg_agmemory/actions/runs/35508695585):
1,871 tests / 8 optional skips on each architecture. Its immutable full-data
30-second preflight failed the unchanged 150/500 ms transaction targets:
observe p95 1146.85 ms, recall 1453.98 ms. All 225 controlled jobs and response
contracts were intact. Keep that failure separate from the earlier reduced
development runs and the corrected runs.

Runtime-role plans isolated duplicate candidate scans for ranking/coverage and
per-row visibility SQL. `9c7db01289a07ea6ce7f7ded37c485d4110e95d1` shares candidate
materialization and applies schema-16 equivalent set-based read policies.
The 240 focused cases included 10,000 actual Native denials (20 actors × 100
protected sources × five operations), positive controls, the original scalar
authorization oracle and prepared-context cases. Two initial test-fixture setup
errors (migration transaction boundary and fixture tenant indexing) were corrected;
authorization conditions and provider contracts were not relaxed.
[Native CI 35513420525](https://github.com/rioriost/pg_agmemory/actions/runs/35513420525)
passed **1,916 tests / 8 optional skips** on amd64 (1033.34s) and arm64 (1265.09s),
including all production and exact operational-state recovery smokes.
The 10,000 requests are assertions within one test, not 10,000 additional pytest cases.

The optimized full-data preflight passed all steady gates (observe p95 37.92 ms,
recall 79.03 ms). The subsequent **full 1,800-second S run** used an immutable
archive of the same exact commit and the unchanged profile digest
`0252ea68cc89276b0e6bf4f51ec402f062b1006c4296a2c829a3c85e333a6b48`.
Host: Apple M4 Max, 128 GiB, shared/non-exclusive. Guest allocation:
database 6 vCPU/24 GiB, application plus two workers 2 vCPU/8 GiB, separate client.
Fixed corpus: 10 tenants, 100k 512-byte episodes/chunks, 10k assertions, 110k
768-dimensional dense projections. The controlled provider sleeps 10 ms and
returns valid empty extraction; it measures no model quality or actual billing.

| Full S steady metric | Result |
|---|---|
| Duration / requests | 1,800 s; 36,000 recall + 9,000 observe |
| Observe transaction / E2E p95 | 40.83 / 47.38 ms |
| Recall transaction / E2E p95 | 76.98 / 82.10 ms |
| Worst recall mode/selectivity transaction p95 | 102.40 ms, hybrid at 100%; every stratum ≤500 ms |
| Invalid responses / missing committed timings / scheduled drops | 0 / 0 / 0; all 45,000 steady request IDs independently matched |
| Warmup+steady worker outcomes | 9,300 succeeded; no pending jobs at drain |
| Worker queue / processing p95 | 379.14 / 84.98 ms, warmup+steady+drain period |
| DB before / after | 926,553,791 / 994,694,847 bytes |
| Index bytes / WAL growth / logical backup | 185,622,528 / 520,918,936 / 467,449,236 |
| Guest sampled nonavailable-memory peak: DB / application | 1,844,879,360 / 420,028,416 bytes; not process RSS |
| Guest CPU accounting: DB / application | 2417.07 / 1493.31 busy seconds over their full sampled intervals, not host overhead |

All 12 recall strata (three modes × four selectivities within a tenant) had
3,000 steady samples. Request/server joins, input hashes and container allocations
are retained in private artifacts; credentials and owned containers were removed.
Hardware counters, physical cold cache and exclusive-host capacity are not claimed.
**Only the steady-load part is measured/passing.** Small-forget barrier, 10k-object
purge and concurrent limit/failure probes remain open, as do the one-reference
memory benchmark and broader declared recovery/deployment coverage.
Reports correctly retain `resource_qualified=false` and `m2_qualified=false`.

### Historical schema-13 evidence

Evidence date: **2026-09-18**. Each experiment is bound to its own implementation
SHA; successful software checks are not M2 acceptance.

This document accompanies [ADR 0028](adr/0028-background-processing.md).
Implementation, deterministic fixtures and a scoring command are not completed
M2 evaluation. There is no claim here of MVP completion, production readiness,
human assertion precision, compaction fidelity, or performance qualification.
The qualified v0.0.26/schema-11 history remains separate.

| Evidence | Current meaning |
| --- | --- |
| Pushed foundation commit `e60d6e3`: isolated Apple Container checks, Ruff, mypy on 26 source files, 389 targeted passes / one live skip | Dependency evidence only; not the combined background-processing/schema-13 tree |
| Foundation `e60d6e3` native arm64 CI | Failed the inherited memory gate; not an all-green foundation CI qualification |
| Pushed foundation checkpoint `7343272` | Includes 720/600-question fixture generation, public chronological ordering, dev splits and the `VmHWM` correction; not combined-backend qualification |
| [Native CI 35313803636](https://github.com/rioriost/pg_agmemory/actions/runs/35313803636) | **SUCCESS on both native architectures** for foundation `7343272`, not the later combined M2 tree |
| Historical real-PostgreSQL targeted attempt before the migration fold: 258 passed / 2 failed | Not qualification; blocking heartbeat-test synchronization and a stale schema assertion failed |
| Reported evaluation subsets: 238 unit passes and three Native SDK integration passes within that targeted work | Contract evidence using offline/fake providers, not actual model metrics; do not add overlapping counts |
| Pushed combined implementation `6ac31c31b6a8e248a21de551a41469510d9354b1` | Schema 13 implementation checkpoint, not full M2 qualification |
| Pushed follow-up `101993a6d40679c73899ee2454f6b2ad0dadafff` | Corrects the live harness's exact `ollama-sha256:` revision prefix |
| Combined full attempt: 1437 passed / 7 live skips / 1 failure | Stale SDK route inventory failed; not a successful full qualification |
| Corrective targeted check: 27 passed / 3 live skips; Ruff, mypy 32 source files and strict SDK check passed | Expected route inventory corrected to 38; typed results cover all seven new SDK methods plus provider extraction; not a corrected full rerun |
| Fresh generated HTTP ACL experiment at `101993a6d40679c73899ee2454f6b2ad0dadafff` | **PASS: exactly 10,000 cases**, bounded to the matrix below |
| [Core native CI 35317028037](https://github.com/rioriost/pg_agmemory/actions/runs/35317028037), implementation `2288fdc4757518e1f3bbd79f115ae46c85532266` | Both native architectures passed; does not include subsequent prompt, crash-test, QA-budget or production-M2-smoke changes |
| Follow-up [CI 35321226191](https://github.com/rioriost/pg_agmemory/actions/runs/35321226191) at `e4f5d76` and [CI 35321615670](https://github.com/rioriost/pg_agmemory/actions/runs/35321615670) at `0552151` | Both architectures passed, respectively 1,485 and 1,487 tests / 8 optional skips per architecture; precede packaged M2 smoke |
| Fresh local Apple Container distribution check | **1,485 passed / 8 opt-in live skips**, Ruff, mypy 32+1, adapter-extra installation checks and all production smokes passed; unit image used `e4f5d76`, runtime images were rebuilt after `0552151` |
| Production M2 smoke published as `9458034a47b6f7c9901e569a32f198f56369fbf7` | Non-root runtime passed three synthetic HTTP calls, 1 inferred / 1 quarantined candidate, 768-dimensional embedding, exact snapshot state, 2,448-byte hook context, purge of 10 objects and administrative policy restoration |
| `0552151` QA-budget regression selection | **127 passed**; separate targeted evidence, not added to the distribution count |
| Exact `9458034a47b6f7c9901e569a32f198f56369fbf7`, [native CI 35322238611](https://github.com/rioriost/pg_agmemory/actions/runs/35322238611) | **Both architectures passed 1,487 tests / 8 optional skips**, Ruff, mypy 32+1, installation profiles and all production smokes including M2; amd64 tests 924.26s, arm64 914.22s |
| Operator-local Ollama 0.34.1, pinned qwen2.5:7b / qwen3 embedding profiles | Actual retrieval and three-call processing lifecycle measured; earlier failures retained |
| Synthetic corpus generation and scorer contracts | Reproducible structural diagnostics, not human or real-task acceptance |
| Dev: 120 questions / 10 groups / 440 actual embedding calls | Measured development-only retrieval; not held-out acceptance |
| Frozen held-out: 600 questions / 50 groups / 2,200 embedding calls at `101993a` | Hybrid Recall@20 **99.818%**, equal to vector-only; temporal ranking non-regression passed on this synthetic set |
| Real-model lifecycle at `e4f5d76ad2a4919349054165ce531b92fa650818` | **PASS**, exactly three model calls: extract, embed, compact; explicit snapshot restore/hook/purge checked |
| Actual SIGKILL at reservation, response and committed-publication boundaries, plus purge before recovery | Four crash/recovery cases passed in the 154-case targeted check at `e4f5d76`; no duplicate publication or source resurrection in these cases |
| Public oracle at `055215168c83501f676e643853d9d7b58e9f0c5d` | Corrected budget recipe completed 412 calls, with 72 invalid answers / 144 attempts and 24 additional budget skips; **not a QA pass** |
| Public QA wire-schema correction at `9c958162f9f61bfb8f75b8747d3133c5afcb80a3` | Fresh 412-call run: 0 invalid contracts, 136 abstentions, 22 mechanical matches / 144 attempts; **not semantic quality qualification** |
| Exact `9c20909bc67dacf8e0fd77a52d1caa46c2340e45`, [native CI 35326089452](https://github.com/rioriost/pg_agmemory/actions/runs/35326089452) | Both architectures: **1,531 passed / 8 optional skips**, all production smokes and actual bounded backup recovery; amd64 tests 1015.18s, arm64 939.33s |
| Human-review tooling at `1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6` | Linux targeted checks: **340 passed / 6 database-dependent skips**, Ruff and mypy on 35 source files. Includes 223 new offline review/collector/pilot cases; do not add overlapping counts |
| Actual Wikipedia review pilot at `1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6` | Six revision-pinned EN/JA excerpts, exactly 24 local text calls: 5 summaries, 11 QA records including 6 abstentions, 8 retained generation failures. No accepted extraction claims or human ratings; [handoff, digests and failure history](HUMAN_REVIEW.md) |
| Exact `1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6`, [native CI 35331248426](https://github.com/rioriost/pg_agmemory/actions/runs/35331248426) | Both architectures: **1,754 passed / 8 optional skips**, all packaged production smokes and bounded backup recovery. amd64 tests 1021.93s, arm64 925.23s. The targeted/new cases above are included, not additional |
| Human, real-task and general disaster-recovery acceptance | Human/task acceptance remains **NOT MEASURED**; bounded process and single-purge backup drills do not qualify general DR |

The inherited memory-gate artifact used `ru_maxrss`. The reported worktree fix
uses `/proc/self/status` `VmHWM` while retaining the **256 MiB** cutoff;
the reported checks include a **139360 KiB** cold peak and a **320 MiB**
parent-process regression case. The correction is included in `7343272`, whose
native CI has now passed on both architectures. Do not transfer that foundation
result to the later combined implementation. Candidate adoption was folded from unpublished migration 014
into migration 013; the current target remains **schema 13**, not schema 14.

Abbreviated model digests are not reproducibility pins. Record complete model
revisions, the approved profile digest and the **actual tested implementation
SHA** with any future run. A later documentation-only publication commit must not
be described as the SHA that ran the experiment.

### Local model profile used for the measurements

Ollama **0.34.1** served independently checked installed model artifacts. The
non-secret profile below is shared by the recorded local experiments; recipe and
prompt changes explain the separately recorded worker/evaluation digests.
The loopback endpoint was an operator-owned authenticated relay, not a public
service. Its credential value is omitted; the relay/model processes were stopped
and temporary relay/database credentials removed after the experiments.

```json
{
  "backend": "local_http",
  "endpoint": "http://127.0.0.1:11435/v1",
  "text_model": {
    "name": "qwen2.5:7b",
    "revision": "ollama-sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
  },
  "embedding_model": {
    "name": "qwen3-embedding:0.6b",
    "revision": "ollama-sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1"
  },
  "timeout_seconds": 120,
  "max_output_tokens": 512,
  "api_key_env": "PGAG_M2_RELAY_TOKEN"
}
```

These revision labels are operator pins, not automatic weight attestation.
Reproduction requires approved local provisioning; do not expose a model server
on all interfaces or weaken endpoint validation to reproduce container access.

## Recorded measurements and limitations

### Generated ACL experiment: measured PASS, limited scope

Fresh implementation `101993a6d40679c73899ee2454f6b2ad0dadafff` executed exactly
**20 actors × 100 real sources × 5 operations = 10,000 HTTP cases**:

- 5,000 cross-tenant cases.
- 5,000 same-tenant, cross-scope cases.
- 10,000 recorded `not_found` outcomes, zero unexpected statuses and no
  post-write changes.

The parent-held aggregate is `m2-acl-101993a/summary.json` in session files,
not a bundled corpus or repository artifact. This is actual generated ACL
execution, not an inferred result from synthetic group labels or zero foreign
IDs in retrieval. It passes this bounded generated-ACL experiment only; it is
not exhaustive authorization proof, human-quality evidence or M2 qualification.

### Local embedding dev run: measured, not held-out

The reported run used **120 dev questions / 10 groups**, with **440 actual local
embedding calls**, no unauthorized IDs and no full-context skips.

| Run binding | Recorded value |
| --- | --- |
| Normalized synthetic dataset digest | `7f34f11c375fc4121ea1ed526345e34ccd416a3c83a8a973d2998b52fd21ee33` |
| Independent evaluation profile digest | `de5685812c514e7dcd424ccd6850e90f0c3753d85011212daa11e220aec60b90` |
| Split | `dev` |

| Baseline | Recall@20 | nDCG@10 | MRR | Truncated observations |
| --- | --- | --- | --- | --- |
| `no_memory` | 0 | — | — | 0 |
| `recent_window` | 0.2727272727272727 | — | — | 120 |
| `full_context` | 0.6818181818181818 | — | — | 0 |
| `vector` | 1 | 0.900985737166785 | 0.8943722943722944 | 120 |
| `hybrid` | 1 | 0.900985737166785 | 0.8943722943722944 | 120 |
| `temporal_provenance` | 1 | 0.9211168415174328 | 0.9216450216450216 | 120 |

“—” means not included in this reported summary, not a zero or a fabricated
measurement. Truncation records bounded windows/Native contexts, including the
20-item limit; it must not be hidden behind perfect Recall@20. Full-context
Recall@20 scores the first 20 IDs in its submitted ordering even when the entire
context fits, so its value does not imply a full-context budget skip.
These are synthetic **dev** measurements, not the internal held-out gate,
human precision, answer quality or real-task replay.

Before starting the 600-question test run, the dev profile, dataset and settings
plus a Git archive of `101993a6d40679c73899ee2454f6b2ad0dadafff` were frozen.
No configuration was tuned on its held-out results.
The public dataset has a separate digest and is not assigned the synthetic digest.

### Held-out synthetic retrieval: measured PASS

The frozen implementation measured **600 questions / 50 groups**, with **2,200
local embedding calls**, 600 queries in each of six arms, zero unauthorized IDs
and zero full-context budget skips. Source pools contain 32 episodes per group;
these are template-generated sources, not 50 independent human task histories.

| Baseline | Recall@20 | nDCG@10 | MRR |
| --- | --- | --- | --- |
| `no_memory` | 0 | 0 | 0 |
| `recent_window` | 0.2727272727 | 0.1403306615 | 0.0839393939 |
| `full_context` | 0.6636363636 | 0.1668115936 | 0.1722305288 |
| `vector` | 0.9981818182 | 0.8892563047 | 0.8795083980 |
| `hybrid` | 0.9981818182 | 0.8892563047 | 0.8795083980 |
| `temporal_provenance` | 0.9981818182 | 0.9130237727 | 0.9116296101 |

The 95% session-group-bootstrap Recall@20 interval for all three Native arms is
**[0.9945454545, 1]**. Recent-window and each Native arm mark all 600 observations
truncated; the Native limit is 20 items and this does not imply exhaustive recall.
The hybrid/vector tie is reported as measured, not a claimed hybrid improvement.
These measurements pass the scorer's sample-size, retrieval and ranking gates
only. No answer generation, semantic support or real-task continuation was
measured on the internal held-out set.

### Live processing: failures retained, corrected lifecycle measured

The durable pipeline's first real extraction call failed with
`invalid_provider_response`. One additional, explicitly authorized diagnostic
with a different Bob source returned the exact quote but `end=16` instead of the
correct `end=28`. **Both failed model/diagnostic calls remain in accounting**;
they are separate from the 440 dev embedding calls and are not an automatic
retry or a successful extract/embed/compact smoke.

The correction at `2288fdc` changes the model wire proposal to four fields
(`subject`, `predicate`, `value`, `evidence_quote`) and derives host
`start` / `end` only from a unique exact quote occurrence, including rejection
of repeated/overlapping ambiguity. Public six-field results and strict
`parse_extraction` remain unchanged. No first-match selection, fuzzy grounding
or semantic authority is introduced. Its first pipeline attempt and a separate
Cora-source diagnostic still failed: the quote omitted the subject. Thus **four
failed extraction/diagnostic calls** precede the successful lifecycle; none is
hidden or counted as a successful task replay.

`e4f5d76ad2a4919349054165ce531b92fa650818` clarified complete-quote guidance
without weakening validation. Its fresh lifecycle made exactly **three calls**:
one extraction (one published inferred preference, zero quarantined/duplicate),
one canonical 768-dimensional embedding, and one untrusted summary/compaction.
It verified committed reservations, exact typed checkpoint state, preserved tail,
explicit restoration, 2,405-byte snapshot budget, one required tail item, replay
and purge. Worker profile digest:
`739b306984c93b892df0b4ac2c00556d865d4864a48ce20ee7f74ee0cb010ed5`.
This is one synthetic lifecycle, **not** a measured human precision rate or one
of the required 20 real task replays.

### Bounded logical-backup recovery drill

[`test-recovery-containers.sh`](../scripts/test-recovery-containers.sh) and
[`smoke-recovery.py`](../scripts/smoke-recovery.py) exercise real
`pg_dump`/`pg_restore` on separate disposable PostgreSQL 18.6/pgvector 0.8.6
clusters. The original cluster is removed before restoration. Recovery uses
independently exported, committed tombstones, receipts and ACL metadata,
not target IDs remembered by the test driver.

An old snapshot contains a synthetic source, assertion, checkpoint and queued
structured job. After backup, the source is purged and a reader revoked. The
restore remains isolated, replays through existing service/admin interfaces,
then checks deleted payloads/checkpoint/job invisibility, denial for the revoked
reader, an unrelated positive control, and equality of **35 canonical table
counts/digests** with the latest state. Metadata-only object anchors remain:
four tombstones are not a claim that every object row or backup copy vanished.

This is deliberately limited to one completed purge after an empty deletion
baseline and one later revocation. Mixed/multiple deletion histories,
principal changes, an unauthorized replay actor and any model-processing state
are rejected rather than guessed. Schema 13 does not link each tombstone to its
receipt/mode, so the helper is not a general replay tool. No API/worker is started;
zero model calls do **not** qualify model reservation/quota recovery. Working
compaction, extraction, vectors, graphs, tool effects, HA/PITR and retention
deadlines are not covered by this case.

Initial local development runs passed 31, then 39 contract cases and the actual
restore, observing four blocked targets, one denied reader and one intact control.
Those were **dirty working-tree runs**, not exact-commit qualification.
Implementation is published as `58bad2991ddf3ee3e118ec47ad1e58839396cddb`;
the report records the source SHA, three helper/test hashes and dirty-input flags.
Temporary dumps/metadata are removed; aggregate reports are retained separately.

The first full packaged run at `58bad29` exposed a missing shell-helper fixture:
**1,527 passed / 4 failed / 8 optional skips**. Its native run was deliberately
canceled, not passed. Correction `9c20909bc67dacf8e0fd77a52d1caa46c2340e45`
includes both helpers in the test image; all 39 recovery contract cases then
passed from that image **without host source mounts**. Do not transfer the
earlier dirty-run success to the failed packaging checkpoint.

The corrected local distribution run at `9c20909` passed **1,531 tests / 8
optional live skips**, all production smokes and the isolated recovery drill
(test phase 557.67s). Its recovery report has `exact_commit_inputs=true`, unchanged
helper hashes, equal latest/restored fingerprints for all 35 tables, four
tombstones, one denied reader and one intact control. Access/deletion epochs
advanced from 3/1 to 4/2. This qualifies the declared single-case drill only;
`m2_qualified=false` and the model-accounting limitation remain explicit.

[Native CI 35326089452](https://github.com/rioriost/pg_agmemory/actions/runs/35326089452)
also passed at that exact corrected SHA on amd64 and arm64: **1,531 passed /
8 optional skips each**, plus all production smokes and the actual recovery
drill. Both recovery reports have `exact_commit_inputs=true`, all 35
latest/restored fingerprints equal, four blocked targets, one denied reader
and one intact control. The separately invoked 39 contract cases are already
included in the full suite; do not add overlapping counts. Local evidence is
`m2-recovery-9c20909.json`; the native run retains its own independent reports.

### Public evaluation attempts and budget correction

The first `101993a` public attempt stopped on invalid model abstention/citations.
`2288fdc` retained invalid answers as explicit failed observations, but that run
was interrupted after 304 reserved calls, with the last outcome unknown; it was
not resumed or qualified. The subsequent `e4f5d76` attempt completed 412 calls and all 168 answer records
(24 budget skips, 72 invalid-answer failures, 16 mechanical exact matches).
It uses the same v1 QA recipe and is diagnostic only: reconstructing whole source texts from Native
recall IDs could exceed the snippet-based Native budget.

`055215168c83501f676e643853d9d7b58e9f0c5d` versions the evaluation recipe to v2:
whole source envelopes, in retrieval order, must fit an **8,000-byte UTF-8
evidence budget**. It records answer-specific source IDs, bytes and truncation
separately from the original Native ranking. It does not skip an oversized first
source to cherry-pick later evidence, change the QA prompt/schema, loosen
abstention validation or retry invalid outputs. The budget excludes the fixed
system prompt and question; the recent window remains capped at 2,000 bytes.
Earlier experiments remain in the ledger and are not comparable budget-matched
QA scores.

### Corrected public oracle measurement

The fresh v2 run at `055215168c83501f676e643853d9d7b58e9f0c5d` completed:
**14 questions / 14 groups / 254 sources**, six arms and seeds 17/29.
Dataset digest:
`f81f3442d8a9bfb9020d4923f2c9535771d8f4f2369b1f3fa3efaddcd305cf3c`;
evaluation profile digest:
`59ab9826be24fec46f6a6ac96f6299a21c76118ac56086ea9874d1ece06d1b5f`.
There were **412 calls**: 268 embeddings and 144 answer attempts. All 168
planned answer records remain present, including 24 explicit full-context
budget skips and **72 `invalid_answer_contract` failures**. Failed answers
remain in the non-skipped denominator; none was retried or counted as abstention.

| Arm | Recall@20 | nDCG@10 | MRR | Mechanical exact matches / non-skipped answers | Invalid answers | QA skips | Maximum evidence bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| no_memory | 0 | 0 | 0 | 4/28 | 0 | 0 | 2 |
| recent_window | .104166667 | .085109150 | .125 | 6/28 | 8 | 0 | 1963 |
| full_context | 1 | .530803156 | .375 | 0/4 | 4 | 24 | 4446 |
| vector | .958333333 | .724359805 | .697222222 | 2/28 | 20 | 0 | 7712 |
| hybrid | .958333333 | .724359805 | .697222222 | 2/28 | 20 | 0 | 7712 |
| temporal_provenance | .958333333 | .724359805 | .697222222 | 2/28 | 20 | 0 | 7712 |

Full context skipped **12/14 questions**; its retrieval means describe only two
non-skipped answerable questions, not the full public sample. Each other retrieval
arm's means use 12 answerable questions. All 84 rankings contained zero foreign
IDs. Native arms marked 12/14 rankings truncated and recent window 14/14.
The additional QA envelope limiter truncated none of these already-selected
prefixes; every non-skipped evidence envelope fit its declared budget. Public
QA exact-match totals are **16/144**, with 24/168 planned cases separately skipped.
These are poor mechanical answer results, **not a quality pass**, an unsupported
claim rate or an official LongMemEval score. Zero observed non-abstentions on
unanswerable cases cannot erase the 72 invalid-answer failures.

Artifacts are operator-local `m2-eval-public-0552151/{journal.jsonl,answers.json,
retrieval-run.json,retrieval-report.json}`. The journal retains all 144 raw
answer responses and failed observations; source payloads and raw responses are
not bundled in this repository. All human-review fields remain `not_reviewed`.

### QA schema correction after the recorded v2 measurement

Inspection of all 144 recorded v2 response shapes found that all 72 contract
failures were empty answers with `abstained=true` **and nonempty citations**.
The receiving validator correctly rejected them, but the generated wire schema
did not express its cross-field abstention rule. Recipe
`native-retrieval-grounded-qa-v3` now sends two complete `anyOf` alternatives:
an answer with `abstained=false`, nonempty text and unique nonempty citations,
or `abstained=true`, the literal empty answer and an empty citation array.
The actual compiled wire schema is bound into the profile digest.

The prompt, seeds, evidence budget and semantic grading are unchanged. Local
UTF-8, whitespace, citation-membership and abstention validation remain in force;
a provider ignoring the schema still fails without retry or output repair.
This fixes a schema/validator mismatch, not semantic answer quality. Earlier v2
results remain unchanged; v3 requires its own explicitly versioned measurement.

The fresh `9c958162f9f61bfb8f75b8747d3133c5afcb80a3` measurement completed the
same 14-question matrix with **412 calls**, **zero invalid contracts / 144
answer attempts**, and 24 separately recorded full-context skips. Its profile
digest is `10d7bb829ce181c379a86474baa8db7c9caa68d4747e9dace35d59b966af9688`.
Every evidence budget held, and retrieval scores/skip counts were unchanged.

| Arm | Exact matches / attempts | Abstentions | Invalid answers | QA skips |
| --- | ---: | ---: | ---: | ---: |
| no_memory | 4/28 | 28 | 0 | 0 |
| recent_window | 6/28 | 26 | 0 | 0 |
| full_context | 0/4 | 4 | 0 | 24 |
| vector | 4/28 | 26 | 0 | 0 |
| hybrid | 4/28 | 26 | 0 | 0 |
| temporal_provenance | 4/28 | 26 | 0 | 0 |

The total remains only **22/144 mechanical matches**, with **136/144 abstentions**.
Eliminating malformed abstentions is **not** answer-quality qualification.
This public rerun follows a structural correction discovered on the earlier
public responses; it is not a newly blinded quality experiment. All human-review
labels remain `not_reviewed`. Artifacts are `m2-eval-public-9c95816/`.
Before that run, a separate four-call synthetic schema smoke exercised both
answer and abstention branches without failure; those four calls are not public
benchmark observations. The isolated evaluator regression selection passed 132
tests, independently of live measurement.

## What the scorer actually does

[`evaluation.py`](../src/pg_agmemory/evaluation.py) consumes a normalized dataset
and measured ranked source IDs. It computes metrics from those rankings; it does
not accept precomputed success percentages or generate retrieval/model results.
It does not itself run the service, prove how a ranking was obtained, attest
model identity, check invoices, or provide a semantic answer judge.

### Dataset and run integrity

- Dataset origin is explicit: `synthetic`, `public`, or `authorized_private`.
  Record the license, retrieval unit, source revision/file digest and variant.
- Sources have distinct source IDs, group IDs, timestamps and text. Questions
  have distinct IDs disjoint from source IDs, category, language (`en`/`ja`),
  dev/test split, query, gold relevance, answer and optional `as_of`.
- Gold references must exist and belong to the question's authorized group.
  A group cannot cross dev/test splits. This structural grouping is not proof
  that the service enforced tenant ACLs.
- `EvaluationDataset.digest()` is SHA-256 over the canonical normalized dataset,
  including questions/gold and provenance metadata. A run must match that exact
  digest. Raw public-file SHA-256 and normalized dataset digest are different
  identities and must not be substituted for one another.
- A run records a full 40-hex implementation SHA, model name/revision, profile
  digest, context byte budget and random seed. These fields bind the report to
  declared run metadata; a report still requires independently retained execution
  evidence.
- Runs declare `split="dev"` or `split="test"` (default test). Every question in
  that selected split requires **all six baseline observations**, with no
  duplicate pairs, missing pairs, unknown questions or observations from another
  split. Each ranking contains at most 100 distinct source IDs. Reports separate
  `measured_questions` / `measured_groups` from `held_out_questions` /
  `held_out_groups`; both held-out counts are zero for dev runs.

The baseline identifiers are:

| Identifier | Experiment obligation |
| --- | --- |
| `no_memory` | No retrieved evidence; `ranked_ids` must be empty |
| `recent_window` | Record the actual bounded recent-context selection and ordering |
| `full_context` | Record the actual full-context selection within its declared budget, or an explicit budget skip |
| `vector` | Record actual vector-only retrieval with the pinned embedding/profile |
| `hybrid` | Record actual lexical/vector hybrid retrieval |
| `temporal_provenance` | Record the actual temporal/provenance-aware retrieval configuration and ranking |

Names alone do not demonstrate these algorithms ran. The runner must retain
configuration, response-to-source-ID mappings, timings, cost/footprint metadata
and failures without consulting gold when choosing a ranking. Do not manufacture
rankings from `relevant` or substitute a label for an unimplemented baseline.

Only `full_context` may use
`skipped_reason="full_context_over_budget"`, with an empty ranking. It remains
explicitly unmeasured rather than receiving an invented score. The scorer records
the declared byte budget; it does not inspect model tokenization or prove that
all context actually fit. A complete measurement matrix is not the same as six
fully executed baselines if some full-context rows were skipped.
Observations also record `context_truncated`; each baseline/category measurement
reports its `truncated` count. Truncated contexts are not silently promoted to
complete contexts, nor confused with an explicitly skipped full-context row.

### Metrics and uncertainty

- **Recall@20:** distinct relevant sources in the first 20 results divided by all
  gold sources for that question.
- **nDCG@10:** graded gain `2^relevance - 1` with logarithmic rank discount;
  gold relevance grades are strict integers 1–3.
- **MRR:** reciprocal rank of the first relevant returned source, or zero;
  the submitted ranking is bounded to 100 entries.
- Unknown IDs and IDs outside the question's group are counted as unauthorized;
  they are not silently removed to improve scores.
- Questions with no gold evidence have undefined retrieval metrics, not perfect
  scores. `unanswerable_with_results` counts returned evidence on such questions;
  it is **not** a measurement of hallucinated or unsupported answers.

Reports include aggregate and category-specific baseline measurements. The
95% intervals resample **whole session groups with replacement**, retaining their
question measurements, and use question-weighted means within each resample.
The seed is recorded; bootstrap samples default to 1,000 and must be 100–10,000.
This is not an independent-question bootstrap, a human-quality confidence
interval, or proof of statistical non-inferiority between systems. Current gate
comparisons use point means, not confidence-bound thresholds. Language fields
are retained, but a separate language-level experiment must not be claimed merely
because a corpus contains both English and Japanese.

### Mechanical gates, not M2 qualification

For a non-public dataset's test split, the scorer reports:

| Gate | Implemented check |
| --- | --- |
| `internal_sample` | At least 500 held-out questions in at least 50 groups |
| `observed_scope_leakage` | Zero unauthorized IDs in this submitted retrieval run |
| `recall_at_20` | Sufficient sample; hybrid mean ≥ 90% and ≥ measured vector mean |
| `ranking_non_regression` | Sufficient sample; temporal/provenance mean nDCG@10 and MRR each ≥ hybrid |

Synthetic data can satisfy these **mechanical** checks without becoming an
independent real-world acceptance set. For `origin="public"` or `split="dev"`,
the sample, internal Recall and ranking gates are explicitly `not_measured`:
neither public nor development results can replace internal held-out acceptance.

`human_assertion_precision`, `human_compaction_fidelity`, `answer_quality`,
`real_task_replay`, `public_baseline`, `generated_acl_cases`, `worker_chaos`,
`deletion_recovery`, and `cost_and_footprint` remain `not_measured` in this report.
Even a public retrieval report does not automatically mark `public_baseline`
accepted; its complete experiment and interpretation must be recorded separately.
**`m2_qualified` is always `false`.**
The separately recorded 10,000-case ACL experiment does not rewrite this
retrieval-only report's `generated_acl_cases` field; attach its independent
evidence rather than inventing a scorer result.

The CLI rejects invalid/incomplete inputs with exit 2; each input file is bounded
to 32 MiB. It emits the report and exits 1 if a reported gate failed. Exit 0 means
no mechanical gate failed, **not** that unmeasured gates passed or M2 qualified.

## Standalone real-Native runner prototype

[`evaluation_runner.py`](../src/pg_agmemory/evaluation_runner.py) is an opt-in
execution prototype, separate from the scorer and from the background worker.
It remains under qualification; the measured dev/held-out runs and separate
public attempts are distinguished above. It exercises
explicit Native ingestion/embedding/retrieval, not automatic extraction jobs or
compaction. A successful run would not by itself qualify those backend paths.

### Isolated scopes, real data paths and baseline construction

- Operators preprovision **distinct, empty, isolated scopes**, one per selected
  dataset group. The scope-map JSON maps exactly those group IDs to unique scope
  UUIDs; scopes must not be shared with normal workloads or other writers.
  The runner does not provision scopes or obtain administrator privileges.
- `PGAG_EVAL_API_URL` and `PGAG_EVAL_API_TOKEN` supply Native connection settings.
  Before ingestion, Native recall checks the mapped scopes for existing items or
  truncated coverage. A nonempty-scope rejection performs no ingestion or deletion.
  This preflight does not replace the operator's isolation obligation.
- Real `AsyncMemoryClient` calls observe **source text only**, obtain canonical
  embedding input, call the approved local embedding provider, and publish with
  `PutEmbedding` bound to the canonical digest/model. Source IDs are mapped to
  returned Native memory IDs. Question/gold/category annotations are never
  ingested; gold is not used to construct rankings or model prompts.
- The provider backend must be `local_http`, with an embedding model; external
  automatic fallback is prohibited. This is an explicitly authorized local
  evaluation operation, not permission inferred from capture admission.
- `no_memory` selects no evidence. `recent_window` selects whole source envelopes
  within **2,000 UTF-8 bytes**; `full_context` requires all envelopes within
  **8,000 UTF-8 bytes**, otherwise it explicitly skips that question/baseline.
  Envelopes include source ID, full text and source timestamp; no partial UTF-8
  source is used to manufacture a fit. Recent selection stops at the first
  over-budget envelope.
- `vector` and `hybrid` use actual Native vector/hybrid recall, at most 20 items
  and an 8,000-byte Native context budget, with the existing English/Japanese
  lexical profiles. `temporal_provenance` uses hybrid recall plus the question's
  `as_of` when present. That label does **not** imply a newly implemented
  provenance-ranking algorithm; without `as_of`, its request follows the hybrid
  path. Native coverage and window-selection truncation are recorded.
- Unknown/foreign returned IDs remain visible in the retrieval journal and stop
  answer generation instead of being forwarded to a model.

Known source times require timezone-aware ISO values; mixed known/unknown times
within a group are rejected. For the public `timezone-unknown` corpus, the runner
uses a recorded **benchmark admission time** as Native `occurred_at`, while
preserving the original dates as text. This is explicitly not an invented UTC
source date. The public adapter now sorts paired sessions by their raw benchmark
date strings with an opaque-ID tie-breaker, preserving each turn's original
text/date association. That supplies deterministic benchmark ordering, not a
timezone-qualified event timeline. These public cases cannot qualify Native
valid-time/as-of behavior.

### Independent profile, call cap and crash accounting

The evaluation profile digest independently binds
`native-retrieval-grounded-qa-v3`, whole-source-prefix selection, the compiled
answer-or-empty-abstention schema, normalized
provider settings, the QA system prompt/schema and context budgets. Earlier
retrieval runs retain their v1 digest. It is **not** the background `WorkerProfile`
digest and must not be presented as that policy authorization. Model identity,
dataset digest, implementation SHA, split, admission time and QA seeds are
recorded in the journal; declared revisions still need operator verification.

`--max-calls` defaults to 3000 and permits 1–10000. A preflight requires enough
budget for all source embeddings, one query embedding per question, and each
requested baseline/answer-seed pair; budget overflow rejects the run rather than
silently reducing the matrix. Optional QA can therefore require a higher
explicit budget than retrieval-only measurement.

Each provider call is reserved **before network I/O** in a private
`journal.jsonl`, flushed and `fsync`ed with `billing_unknown=true`. Validated
completion adds a completion record; a failure/crash can leave the outcome
unknown. There are no automatic retries, fallback calls or resume of a used
runner instance/journal. Reconcile uncertain executions before deliberately
starting another run; a new output directory is not proof that a prior call
never happened. This per-run journal/cap is not the background database policy's
durable per-epoch budget or an exactly-once billing guarantee.

The run has a random source namespace and run-specific idempotency keys, so
ingestion does not reuse an earlier run's deduplicated episode. In `finally`,
cleanup attempts to purge **only the admitted Native IDs tracked for this run**,
in bounded batches, not all contents of a scope. It does not delete existing
objects merely to make a scope empty. A hard crash, ambiguous Native write or
cleanup failure still requires operator reconciliation; the prototype does not
prove disaster recovery or deletion of provider logs/caches.

### Optional answer diagnostic, not a quality judge

`--answers` requires a configured text model and explicit output-token limit.
For each non-skipped question/baseline, it requests two answers with seeds
**17 and 29**, temperature 0 and one fixed system prompt. Provider support for
these controls is not proof of deterministic model execution.

The closed answer schema has `answer`, strict-boolean `abstained`, and up to 20
distinct `citations`. Abstention requires empty answer/citations; an answer
requires nonempty text and citations drawn only from supplied source IDs.
Invalid response/contract/citation outputs are explicit `failure_code` observations
with `exact_match=false`, not skipped questions or successful abstentions.
The runner continues other declared cases without retrying the failed one;
transport errors still stop the run with its partial journal preserved.
The prompt treats evidence as untrusted, preserves negation/uncertainty and
requires abstention without support. Schema/citation membership checks do not
establish semantic support or human approval.

`exact_match` is a mechanical comparison of stripped, case-folded answer strings
for answerable questions, and an abstention check for unanswerable questions.
`unanswerable_nonabstention` records failure to abstain on the latter.
Every record remains `human_review="not_reviewed"`. This is **not** the upstream
LongMemEval LLM judge, semantic answer-quality grading, human assertion precision,
or human compaction review. The context budgets bound evidence selection/Native
packs, not the complete provider request or model tokenization.

The output directory must be new. On successful execution it contains
`journal.jsonl`, `retrieval-run.json`, `retrieval-report.json` and `answers.json`
(with an empty answer-record list when QA is disabled). The runner's successful
completion (`measured` or `measured_with_answer_failures`) is not a gate verdict; inspect the report or run the
standalone scorer for its gate-sensitive exit status. Outputs retain
`m2_qualified=false`, including dev diagnostics.

### Opt-in live evaluation harness

[`tests/test_evaluation_live.py`](../tests/test_evaluation_live.py) runs the
prototype against real Native SDK/HTTP paths and the approved local models.
It skips unless explicitly opted in. Unlike the standalone CLI, this test
harness uses disposable PostgreSQL/runtime fixtures and creates distinct empty
test scopes itself; it must never be pointed at an ordinary workload database.

| Environment variable | Required purpose |
| --- | --- |
| `PGAG_M2_EVALUATION_MODE` | Explicit `dev`, `test`, or `public`; unset means skip |
| `PGAG_LIVE_PROVIDER_CONFIG` | Approved local profile file matching the harness's pinned models |
| `PGAG_M2_EVALUATION_OUTPUT` | New, access-controlled operator artifact directory; must not already exist |
| `PGAG_M2_IMPLEMENTATION_SHA` | Actual full 40-hex implementation SHA used for the run |
| `PGAG_M2_LONGMEMEVAL_ORACLE` | Additionally required for public mode; exact pinned oracle artifact |

The harness checks the operator-declared full `ollama-sha256:` revisions for
`qwen2.5:7b` and `qwen3-embedding:0.6b`, local-only backend and
`max_output_tokens=512`. Those
configuration checks are not independent attestation of installed model weights.
Its call cap is 3000 and context byte budget is 8000.

All modes run the six retrieval arms. Dev and test modes use the synthetic corpus
and do not request QA in this harness; **public mode adds QA seeds 17 and 29**.
The standalone runner's separate `--answers` option remains available only with
appropriate approval/budget. Public full-context overflow is still an explicit
skip, not a fabricated answer result.

Run dev first, record its artifacts, then freeze dataset/split, implementation,
model/profile/prompt, budgets and selection/scoring choices **before** held-out
test and public runs. Do not tune from test results and relabel the same run as
independent held-out acceptance. Test mode asserts the mechanical 600-question /
50-group sample and retrieval gates; no mode asserts M2 completion. Dataset,
scope-map, journal, rankings, report and optional answers remain controlled local
artifacts, not repository payloads.

```bash
# Only in the approved disposable DB/runtime test environment, with the
# required profile/output/implementation environment variables already supplied:
PGAG_M2_EVALUATION_MODE=dev pytest tests/test_evaluation_live.py -q
# After the dev freeze, choose a NEW output directory before each next run:
PGAG_M2_EVALUATION_MODE=test pytest tests/test_evaluation_live.py -q
# Public mode also requires PGAG_M2_LONGMEMEVAL_ORACLE.
PGAG_M2_EVALUATION_MODE=public pytest tests/test_evaluation_live.py -q
```

These are execution interfaces, **not additional execution records**; only the
separately reported measurements above establish what has actually run.
Offline contract tests and fake-provider Native integration results do not
substitute for the emitted live rankings, answers or observed costs.

### Separate three-call real-model processing smoke

[`tests/test_processing_live.py`](../tests/test_processing_live.py) is explicitly
opted in with `PGAG_M2_LIVE_PROCESSING=1`, `PGAG_LIVE_PROVIDER_CONFIG`, the harness's
pinned local models and an approved disposable `PGAG_TEST_DATABASE_URL`.
It is separate from retrieval evaluation. One successful smoke makes exactly
**three real provider calls**: extraction, embedding and summarization through
the durable `extract`, `embed`, `compact` jobs, with no automatic retries.

The smoke checks reservation-before-network, result/model/input lineage,
inferred-or-quarantined extraction, canonical embedding persistence, exact
checkpoint state, untrusted summary, retained tail, explicit snapshot
restore/hook context and budgets, and purge with retained call accounting.
Quarantined candidates do not become human-approved merely because the smoke
passes. Neither this one case nor its three calls measures precision, compaction
fidelity, real task completion, retrieval quality or performance acceptance.

```bash
# This opt-in makes real local-model calls; do not enable it for ordinary tests.
PGAG_M2_LIVE_PROCESSING=1 pytest tests/test_processing_live.py -q
```

Initial extraction failures and diagnostics are retained above. The fresh
three-call pipeline passed at `e4f5d76`; dev/held-out retrieval and public QA are
independent experiments and cannot supply its human semantic qualification.

## Internal synthetic fixture: structural coverage only

[`evaluation_fixtures.py`](../src/pg_agmemory/evaluation_fixtures.py) generates
project-owned MIT synthetic sources, not private conversations or copied public
benchmark data. The current generator declares:

- Seed 42 by default, with an explicit integer seed in 0–2147483647.
- Dataset ID `pg-agmemory-internal-synthetic-v1-seed-{seed}` and template revision
  `internal-synthetic-templates-v1`; this template label is **not** a Git SHA.
- `source_file_digest=null`: there is no downloaded source-corpus file whose
  hash this synthetic generator claims.
- 60 groups, **1,920 sources** (32 per group), **720 questions**: 10 dev groups / 120 questions and
  50 held-out groups / **600 questions**. This replaces the earlier planning
  estimate of 600 total / 500 held-out; it is a fixture count, not a result.
- Twelve categories: `same_name`, `exact_reference`, `temporal_history`,
  `temporal_current`, `negation`, `uncertainty`, `preference`, `source_update`,
  `unanswerable`, `quoted_injection`, `failed_approach`, `next_steps`.
- English/Japanese: 360 questions each overall, 60 each in dev, 300 each in test.
  Each category has 60 questions, 30 per language, with 25 per language in test.
  There are three source/query template families per language/category.
- Repeated names across authorization groups, exact identifiers, aware ISO source
  timestamps, dated source updates, negative/uncertain claims and quoted
  adversarial instructions. Scoped, temporally visible candidate pools contain
  more than 20 sources, including near-topic distractors: top 20 is not simply
  the entire eligible group.
- Seeded opaque 128-bit IDs use separate `g_`, `s_`, `q_` namespaces. Sources are
  generated before and independently of question/answer/gold rendering; changing
  question or gold templates/mappings does not change the source corpus.
  Question IDs, gold answers, category and relevance annotations are not memory
  ingestion material. Gold is template-derived, not a human semantic judgment.
- Historical `as_of` lies between old/new sources; current-time gold selects the
  new source only. Source corrections grade old/new evidence 1/3, and next steps
  grade the failed approach/later plan 2/3. The 60 unanswerable questions have
  empty gold and answers; injection answers explicitly deny quoted authority.

Record the digest of the **actual generated dataset** and generator implementation
before freezing a run. The dev digest and retrieval measurements above identify
one recorded instance; the generator alone predicts no model score.
Do not tune on held-out questions, relabel a seed change as the same dataset,
count these groups as real task replays, or count their modeled group boundaries
as 10,000 actual ACL tests. Natural-dialogue diversity, model extraction accuracy,
human compaction review and successful real tasks are not established.
Fixture generation is included in foundation checkpoint `7343272`; the reported
unit/integration contract checks above are not retrieval/model-quality results.
The exact normalized dataset/profile digests and real dev results are recorded
above. Foundation native CI passed on both architectures; neither fact qualifies
the combined M2 tree or turns synthetic dev results into held-out acceptance.

## Public diagnostic: pinned LongMemEval oracle

The public adapter is
[`evaluation_public.py`](../src/pg_agmemory/evaluation_public.py), an independent
normalizer for an operator-supplied artifact. It does not download datasets or
ingest the complete normalized document into memory.

| Provenance field | Pinned value |
| --- | --- |
| Dataset | `xiaowu0162/longmemeval-cleaned` |
| Artifact | `longmemeval_oracle.json` |
| Dataset revision | `98d7416c24c778c2fee6e6f3006e7a073259d48f` |
| Raw SHA-256 | `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c` |
| Exact bytes | `15388478` |
| Declared license | MIT |
| Upstream code | `xiaowu0162/LongMemEval` |
| Upstream code revision | `9e0b455f4ef0e2ab8f2e582289761153549043fc` |
| Selector seed | `pg-agmemory-public-v1` |
| Normalized ID | `longmemeval-cleaned/oracle/pg-agmemory-public-v1` |
| Variant / unit | `oracle-reader-diagnostic` / `turn` |

Sources: the [pinned dataset card's MIT declaration](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/blob/98d7416c24c778c2fee6e6f3006e7a073259d48f/README.md),
the separate [code MIT notice](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/LICENSE#L1-L13),
and the [official artifact links](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/README.md#L34-L42).
The code revision is distinct from the dataset revision; the code license alone
does not establish dataset terms. The actual
[pinned artifact](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_oracle.json)
uses the `resolve` URL; its `raw` counterpart is an LFS pointer, not the dataset.

The loader enforces exact raw byte count, SHA-256 and the 500-record upstream
file shape. It deterministically selects two questions per each of seven strata:
abstention, knowledge-update, multi-session, single-session-assistant,
single-session-preference, single-session-user and temporal-reasoning. Selection
uses the frozen seed and question IDs before scoring, producing **14 questions**
for a reader diagnostic, not the internal 500-question acceptance set.

Each selected question receives an opaque group; each history turn receives an
opaque source ID. Source text contains only its raw source-date string, role and
turn content. `has_answer` supplies **turn-level gold to the scorer only**;
gold answers, answer-session annotations, category and questions must not be
ingested as memory or supplied as retrieval-selection hints. Abstention questions
have no gold evidence. The normalized dataset contains both sources and gold:
ingest **only its source records**, never the entire JSON as model context.

Dates are preserved as source/question text; source `occurred_at` is
`timezone-unknown`. The adapter does not invent UTC offsets or benchmark
valid-time semantics. These dates alone cannot validate Native temporal/as-of
retrieval. Upstream session arrays can be unsorted. The adapter zips
date/session-ID/turn-array associations **before** sorting by raw date string,
with an opaque session-ID tie-breaker; it does not guess a timezone.
Opaque IDs prevent annotation leakage through original session IDs
or abstention suffixes; they do not turn the oracle corpus into a realistic
distractor-heavy retrieval benchmark.

**Oracle is not LongMemEval-S retrieval.** The
[official variant definitions](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/README.md#L74-L88)
describe oracle as containing only evidence sessions, not the realistic S
retrieval workload. The S artifact is `longmemeval_s_cleaned.json`; it has not
been acquired for this evaluation. Do not silently substitute S, M, V2 or an
unpinned dataset-viewer subset for the declared oracle artifact.

Our fraction-of-gold Recall@20 is not upstream's
[recall_any / recall_all](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/src/retrieval/eval_utils.py#L24-L29).
The [official QA evaluator](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/src/evaluation/evaluate_qa.py#L24-L43)
uses an LLM judge; these retrieval scores do not reproduce that answer-quality
protocol. Report oracle results as that exact reader diagnostic, not published
full-benchmark equivalence, the internal 90% Recall gate, human assertion
precision, or 20 real task replays. The versioned public attempts and evidence
budget correction are documented above; a completed diagnostic is not human
answer-quality qualification.

### LoCoMo is not part of this run

[LoCoMo](https://snap-research.github.io/locomo/) data is subject to
[CC BY-NC 4.0](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/LICENSE.txt#L116-L155).
Use requires a
review of the **actual noncommercial purpose**, not simply describing a company
experiment as “research.” Leaving data uncommitted does not remove the restriction
on use. The evaluation runner has not acquired or evaluated LoCoMo. Separate
provenance research read its small JSON in memory to verify shape/metadata,
without persisting files or running evaluation/model calls; therefore a blanket
“never downloaded” claim would be inaccurate. LoCoMo remains unapproved for this
evaluation, unevaluated and unbundled.

Do not bundle its data or evaluator code under this project's MIT license.
A repository's code license does not relicense its datasets. Any future use needs
recorded purpose approval, applicable attribution/license notices, a pinned
artifact and an independently declared experiment. The
[pinned dataset description](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/README.MD#L8-L26)
also distinguishes released conversation data from unreleased images; do not
claim multimodal coverage from text-only access.

## Reproduction without inventing results

The following commands show existing interfaces, not a claim that an evaluation
was executed:

```bash
# Emits the synthetic dataset, including scorer-only questions/gold.
python -m pg_agmemory.evaluation_fixtures --seed 42

# ORACLE_FILE names the operator-supplied exact pinned artifact.
python -m pg_agmemory.evaluation_public \
  --longmemeval-oracle "$ORACLE_FILE"

# DATASET_FILE is normalized JSON; RETRIEVAL_RUN_FILE contains observed rankings.
python -m pg_agmemory.evaluation \
  --dataset "$DATASET_FILE" --run "$RETRIEVAL_RUN_FILE" \
  --bootstrap-samples 1000
```

The runner below **does make local model calls**. Use it only after data-use
approval, empty isolated scope provisioning and profile review; this example
selects dev and does not constitute a recorded run. Its default split is test.
Connection credentials are supplied through the environment, never literals:

```bash
# PGAG_EVAL_API_URL / PGAG_EVAL_API_TOKEN come from the operator environment.
# The scope map must contain exactly the selected dev groups.
python -m pg_agmemory.evaluation_runner \
  --dataset "$DATASET_FILE" \
  --scope-map "$SCOPE_MAP_FILE" \
  --profile "$LOCAL_PROFILE_FILE" \
  --implementation-sha "$IMPLEMENTATION_SHA" \
  --output "$NEW_RUN_OUTPUT_DIRECTORY" \
  --split dev --max-calls 3000
# Add --answers only when its two-seed QA calls are approved and budgeted.
```

Use an approved, access-controlled, version-control-excluded operator
artifact location for generated datasets, per-question runs and provider output.
Do not commit large generated corpora/results, credentials, private conversations
or copied benchmark payloads. Repository evidence should contain only reviewed
metadata, provenance, aggregate results and references to controlled artifacts.

Before a real run, freeze the dataset digest/group split, all six baseline
definitions and budgets, implementation SHA, full model pins, approved local
profile, recipe versions, selection/bootstrap seeds and allowed data use.
Keep gold outside service ingestion, model prompts and ranking construction.
Record actual requests/results, failures and explicit skips; do not infer quality
from installed models, mocked responses, collection counts or a successful scorer.
Capture policy alone never authorizes provider egress.

## Current acceptance evidence and engineering backlog (2026-09-19)

Automatic inferred assertions, quarantined proposals and caller-adopted reported
assertions are distinct evaluation cohorts. The implemented
`POST /v1/jobs/{job_id}/candidates/{ordinal}/adopt` accepts a caller's
`explicit_intent=true`, `expected_input_digest` and `reason`; its lineage records
`human_review_verified=false`. This is an explicit caller declaration, **not**
verified human review or semantic truth. Neither successful adoption nor a
reported assertion label supplies an independent semantic precision label.
Preserve the server-recorded source/span/model/prompt/job
lineage when selecting samples for separate human review. Adoption leaves the
original disposition `quarantined`; separate `adopted_assertion_id` and
`adopted_by` identify its single adoption. Do not count that proposal as an
automatic publication or treat adoption as supersession of another assertion.

| Core obligation | Current evidence / remaining requirement |
| --- | --- |
| M2-A contract/evidence inventory | Recorded above. `9c7db01` native 1,916/8 per architecture is current bounded implementation evidence, not a blanket release decision |
| State, provenance and explicit updates | Exact typed values, revision/span/coverage links, model-space isolation, CAS and temporal oracles; do not count semantic summary/answer quality as structural conformance |
| 10,000 actual adversarial ACL cases | **PASS for the recorded generated HTTP matrix** at `101993a6d40679c73899ee2454f6b2ad0dadafff`; bounded evidence, not exhaustive authorization or M2 proof |
| Worker chaos | Four actual SIGKILL/recovery/purge cases passed at `e4f5d76`; deterministic lease/cancel/revocation/policy regression coverage is separate, not an exhaustive distributed-fault guarantee |
| M2-B deletion/ACL/policy/call-accounting restore | Bounded exact-state application now preserves IDs/policy/job/call accounting, with rollback and runtime isolation. **Open:** broader content/derivative/history profiles and deployment qualification. Missing canonical content is not reconstructed; no automatic activation or blanket restore claim |
| M2-C resource qualification | **Measured at `51293b4`:** full 30-minute S, mixed small deletion, 10k purge, concurrent limit/failure probes and declared guest-cold samples pass their checks. Physical host/device cold and exclusive production capacity are not claimed; final dual-architecture distribution completion remains pending |
| M2-D one reference memory benchmark | Reuse pinned qwen2.5:7b / qwen3-embedding:0.6b retrieval and three-call lifecycle evidence; complete a reproducible memory-path example and release handoff. Retain errors and skips; no model matrix or semantic success threshold |
| M2 release packaging | Exact-commit native distribution checks, upgrade/restore documentation and explicit supported limits after the remaining changes; this plan revision supplies no new runtime qualification |

The old human precision/fidelity, natural-language update, unsupported-answer
and real-task-success targets are **removed from project acceptance**, not passed.
Optional semantic observations remain unmeasured unless genuinely evaluated.
The pending Wikipedia pilot is not a blocker and need not be rated. It made
provider calls only and cannot substitute for a memory-path benchmark.

People still authorize data use, provider egress and deployment policy. Removing
human quality gates does not remove consent, licensing, permissions or publication
controls. Controlled provider responses can establish boundary correctness;
only real calls are live benchmark evidence. Provider retention is an operator
dependency, not erasure that the PostgreSQL service can silently guarantee.

The next implementation priority is **M2-B**, following the M2-A acceptance
inventory: versioned recovery metadata, migration/upgrade behavior and isolated
reconciliation of current deletion/ACL/policy/reservation/quota state. Preserve
unknown outcomes; do not infer missing schema-13 receipt/mode history. General
production HA/PITR and RPO/RTO are M5; safe supported-history logical restore is
still M2. Core recovery/resource gaps remain open despite the scope correction.
