# Evaluation contract and evidence boundaries

[日本語](EVALUATION-jp.md)

## Versioned evidence-admission correction

`bounded-lexical-v4` changes **caller-side evidence admission**, not Native
search, model selection or the planning prompt. It selects
`evidence_selection="round-robin-v1"`: query result lists contribute distinct
whole items in turn, with follow-up-round lists visited first and original
Native rank retained within each list. At most four lists of eight candidate
items are retained; selected context still has at most eight items / 8,000
Native context bytes and receives one fresh required-reference validation.
The earlier `bounded-lexical-v3` keeps its first-admitted behavior.

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-admission-v4-01 gpt-6-astra high --allow-copilot \
  --cohort distractor-synthetic-v1 --query-policy bounded-lexical-v4 \
  --retention-policy review-v1
```

This is a **known-cohort regression**, following the published 13/20 first
measurement below. Keep cases/gold, query-v2, planning/answer/retention prompt
text, scoring, review behavior and model identifier/effort fixed. The ceilings
remain **120 model calls, two planning rounds, four searches plus one final
validation per case**. The selected evidence may change subsequent planning
inputs and therefore model output; no identical stochastic response is promised.
No additional model ranker, hidden retrieval, retry or native SQL change is used.

The shared helper source hash changes honestly for the new selection branch.
Historical exact-source reproduction uses the recorded commit; preserving v3
behavior does not mean pretending its current module bytes have the old hash.
The v4 recipe explicitly binds the selection identity and actual helper source.
An offline replay may compare selected references from recorded responses,
but is not a fresh authorization check or a new answer-quality measurement.
Any live repeat is frozen in a clean commit before inference, and the original
adverse result remains published without retrospective rescoring.

### Known-cohort v4 result: admission loss fixed, discovery gaps remain

The frozen **`357a57b`** run uses GPT-6 Astra/high and completes **120 model
calls / 60 arm outcomes**, with no malformed responses, retries or unmeasured
arms. Cases/gold, scorer, planner/answer/retention prompt text and review
policy remain unchanged. All fixed control/retention prompt hashes match
the first run. This is **known-cohort reuse**, not new held-out evidence.
See the [reviewed aggregate](../examples/copilot-memory-admission-v4-result.json).

| Measure | First-use v3 | Known-cohort v4 |
|---|---:|---:|
| Native exact correctness | 13/20 | **16/20** |
| Answerable exact correctness | 9/16 | **12/16** |
| Raw-search required-source coverage | 81.25% | 81.25% |
| Delivered required-source coverage | 59.375% | **81.25%** |
| Cited required-source recall | 56.25% | **81.25%** |
| Two-event chains correct | 0/4 | **1/4** |
| Planning, reads and answer sum p95 | 35.92 s | 39.01 s |

Controls remain **4/20** without memory and **8/20** with two recent events.
All four formerly discarded required sources (cases 01, 02, 05, 08) now
reach the answerer; no returned required source is lost in this run.
Cases 01, 02 and 08 gain exact correctness, with no previously correct
case regressing. All four twice-corrected histories remain correct.
Before inference, replaying all 20 original trajectories exactly reproduced
v3's final references and improved v4's selected coverage from 59.375% to
81.25%. That replay used no new model/database calls and did not claim
fresh authorization or predict an answer score.

The four remaining exact-match misses have different causes. Cases
**03, 06 and 17** still retrieve neither required source and abstain;
reselection cannot recover evidence that no search returned. Case **05**
now receives and cites the correct source, but returns **`640 tiles per file`**
instead of the frozen scalar **`640 tiles`**. The longer phrase is present
in its cited source. It still fails the unchanged exact-string oracle:
the score remains **16/20**, not a post-hoc semantic 17/20. The fixed scorer
also flags this miss as `unsupported_answer`; that flag is not a separate
hallucination judgment. The three remaining retrieval misses and scalar
output contract require different follow-up work.

Retention again matches all **504 keep / 136 forget labels**. Review defers
all 136 proposals and verifies they remain readable; zero Native Forget
calls/purges occur. This does not repair the earlier cohort's wrong proposal
or complete forgetting. V4 performs **65 searches and 20 fresh validations**
versus 63 and 20, within unchanged four-plus-one per-case ceilings.
Usage is **467,782 input / 18,274 output tokens**, 467,422 cache-write and
zero cache-read tokens, with 120 reported API/premium requests. Reported
676,007,500,000 nano-AIU is not a verified monetary bill.

Reader-path p50/p95 are **28.92/39.01 s**; these sum two planning calls,
actual Native reads and the answer call, excluding retention/setup.
Runner-side call times include queue wait; aggregate Copilot-call
percentiles use bridge-reported duration. Overall runner duration is
**1,166.404 s**. Single stochastic cloud runs do not establish a causal
latency regression, a production SLA or unseen generalization.
No helper, prompt, model policy or scorer is changed after this measurement,
and no extra model call is used to diagnose it.

Local validation caveat: an initial real-HTTP, scripted-model run had seven
empty-context failures across both v3 and v4. The unchanged source subsequently
passed the eight targeted cases and two complete 889-case runs, including a
final run without diagnostics or source overlays. A read-only clock diagnostic
did not reproduce the failure; its root cause remains unconfirmed. No sleep,
temporal-filter relaxation or cached fallback was introduced. These engineering
test reruns are separate from the single, unretried real-model measurement.

The exact measured source passes all eight native jobs in
[run 36213207824](https://github.com/rioriost/pg_agmemory/actions/runs/36213207824).
Both core suites report **5,365 passed / 134 skipped**, with separate 18 COMMIT
and 13 request cases and six offline bridge checks. Packaged installation,
HA, PITR and patched AGE also pass on both architectures; each AGE run passes
218 cases. This validates the implementation, not product acceptance.

## Fixed-policy evaluation with longer distractor histories

`--cohort distractor-synthetic-v1` selects a separate, explicitly versioned
synthetic cohort: **20 cases / 640 events**, ten English and ten Japanese.
Each history contains 32 events, including 24 durable near-topic
distractors that the existing retention policy requires keeping. These are
not merely transient rows that disappear from the reader after retention.
The five existing categories remain balanced, with 16 scalar-answer questions
and four required abstentions. Evidence positions vary; four answerable cases
have all required evidence in the recent two events. Four two-event chains and
four twice-corrected histories exercise evidence selection, not just row presence.
Gold retention labels comprise **504 keeps / 136 forgets**.

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-distractor-01 gpt-6-astra high --allow-copilot \
  --cohort distractor-synthetic-v1 --query-policy bounded-lexical-v3 \
  --retention-policy review-v1
```

The new cohort uses an explicit extended case type. It does not loosen the
original 6–8-event case contract, modify either earlier dataset or change
retention/answer prompts and scoring formulas. Freeze and review the new
histories and gold labels in a clean commit before inference. Keep the
bounded planner, review helper, model identifier/effort and resource ceilings
unchanged: at most **120 model calls**, four searches and one fresh validation
per case, and eight items / 8,000 Native context bytes. No result-driven
query tuning, automatic retry or hidden browse is part of this experiment.

This is first-use **project-aware synthetic stress data**, not independently
human-authored or blinded tasks, a representative workload, a large-corpus
benchmark or proof of training-data exclusion. Authored distractor patterns
are not a substitute for real agent histories. The measured 20/20 on the
previous known cohort is a reference, not an expected score or a pass
threshold for these different cases. Report all three arms, failures,
direct retrieved coverage, cited-source recall, retention proposal errors,
actual deferred/deleted counts, usage and latency regardless of outcome.
Subsequent runs of this cohort must be labeled reuse.
The runner leaves first-use/reuse status unknown for this cohort; the reviewed
result must establish it from recorded history, not a new output directory.

### First longer-history result: useful follow-up evidence is discarded

The first run at **`f12a3f4`** completes **120 model calls / 60 arm outcomes**
without malformed responses, retries or missing arms. The dataset was reviewed
and committed before this first recorded evaluation; no model, query/helper,
retention policy, prompt, case or scoring formula changed after inference.
The [reviewed aggregate](../examples/copilot-memory-distractor-result.json)
records the new cohort identity and unchanged policy/scorer hashes.

| Arm | Correct / 20 | Answerable correct / 16 | Abstentions |
|---|---:|---:|---:|
| No memory | 4 | 0 | 20 |
| Recent two events | 8 | 4 | 16 |
| Native bounded memory | **13** | **9** | **11** |

All four required abstentions and four twice-corrected histories are correct.
Seven answerable questions abstain, including **all four evidence chains**.
There are no incorrect non-abstained answers. The earlier 20/20 therefore does
not establish reliable retrieval with durable distractors. This is a different
cohort, not a paired causal comparison or a real-world quality certification.

Replaying the recorded responses through the unchanged helper reproduces all
20 final reference sets without additional database or model calls.
The journal and frozen implementation separate two failure mechanisms:

- In **four cases (01, 02, 05, 08)**, follow-up Native searches actually return
  missing gold evidence, but the caller's eight-item context is already full.
  `BoundedRecall.record()` keeps the first admitted items and drops later
  additions instead of reconsidering the evidence set. For case 08, the
  planner correctly follows `空輪審査経路`; Native returns the department
  directory entry, yet the final context still lacks it. Fresh validation
  validates the admitted rows, not their relevance or sufficiency.
- In **three cases (03, 06, 17)**, no search returns required evidence.
  Broad topic results crowd out the route/lesson; refinement still has lexical
  gaps, such as `approve` versus stored `approval`, or `failure` versus
  stored `failed`. The fixed simple profile does not stem these words.

Across 16 answerable cases, required-source coverage in the **union of raw
Native search responses is 81.25%**, but only **59.375%** reaches the answerer.
The existing direct retrieved metric measures that delivered context, not
every intermediate result. Citation-based recall is separately **56.25%**.
Nine cases receive all required evidence, one receives half a chain and six
receive none. All 20 truncation flags remain reported.

Retention proposals match **all 504 keep / 136 forget labels**, including
every durable distractor. This does not repair the mistake on the previous
cohort or establish generally correct retention. All **136 proposals are
deferred and verified readable**; Native Forget calls and physical purges
remain zero. No deletion is approved or completed.

Evidence retrieval uses **63 searches + 20 fresh validations**, within the
unchanged four-plus-one per-case cap. Search p95 is **66.68 ms**, final
validation p95 **73.83 ms**, and the two-plan/read/answer sum is
**p50 27.87 s / p95 35.92 s**, excluding retention/setup. Timing clocks follow
the bounded/review report below. Usage is **467,877 input / 17,862 output
tokens**, 120 reported premium requests, and no extra diagnostic model calls;
these are not monetary bills and exclude authoring usage. Neither a speed
improvement nor production usefulness is claimed. The first result remains
unretuned; evidence admission and remaining lexical gaps are product work.

## Bounded evidence and review-only retention comparison

The opt-in pair `bounded-lexical-v3` / `review-v1` addresses the observed
query/chain and irreversible-retention risks without altering Native SQL,
RLS, the original cases, the original query-v2 module or answer/scoring formulas.
It is a repeat of an already measured synthetic cohort, **not another unseen
test**. Two policies change together, so any improvement is not a controlled
attribution to search alone.

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-bounded-review-01 gpt-6-astra high --allow-copilot \
  --cohort unseen-synthetic-v1 --query-policy bounded-lexical-v3 \
  --retention-policy review-v1
```

V3 uses **at most 120 model calls**: the unchanged 20 retention proposals,
40 control answers, 20 final Native answers and up to 40 planning calls.
This is an explicit increase from the earlier 100-call recipe, not a hidden
retry. Each case allows two planning rounds, at most two literal queries per
round, at most four Native searches and one final reference-only validation.
Scope/filter/profile and `as_of`/`known_at` stay fixed. Pending review IDs are
removed before follow-up planning and reader context. The whole-item merge is
bounded to eight items / 8,000 Native context bytes; the planning prompt also
has an 8,000-byte limit and marks omitted items. Incomplete indexes, changed
epochs, conflicting responses or failed fresh validation terminate the case;
there is no cached-success fallback or empty-query browse. A final empty query
is allowed only with nonempty exact required refs and an equal `max_items`.

`review-v1` does **not** perform Native Forget preview or purge. All model
forget suggestions become pending review. Their rows remain accessible to
authorized operators and are verified present; exclusion is local to this
workflow, not global access revocation or completed forgetting. Later deletion
requires an independent trusted decision through existing Native mechanisms.
The harness's eventual destruction of its owned lab is operator cleanup,
not model-directed deletion.

The report labels retention scores as **proposal quality**, including wrong
forget proposals, rather than reusing `unsafe_deleted` as though a purge had
occurred. It separately records zero executed Native purges, pending counts
and `deletion_completed=false`. No gold label approves or vetoes an action.
Keeping recoverable rows must not be reported as perfect retention selection,
privacy erasure, a persistent review queue or a production deletion safeguard
for clients that bypass this optional helper.

No flags retain the older `lexical-v2` / `model-purge-v1` behavior and 100-call
limit. Selecting v3 without a retention flag chooses review mode; explicit
policy overrides remain available for separately versioned comparisons.
Raw plans, issued queries, fresh-validation outcomes, proposal/actions and
resource budgets are bound to recipe v4. All failures remain in the
denominators; the model identifier/effort is fixed and exact model weights
remain provider-managed and unattested.

### Measured bounded/review result

The first combined-policy run at **`6610f0b`** completes **120 model calls /
60 observations**, with no malformed responses, retries or unmeasured arms.
The [reviewed aggregate](../examples/copilot-memory-bounded-review-result.json)
binds the case, scorer, both helper modules and private artifacts. GPT-6
Astra/high and the existing answer/retention prompts are unchanged.

| Measurement on the same cohort | Previous single-query/purge run | Bounded/review run |
|---|---:|---:|
| No-memory correctness | 4/20 | 4/20 |
| Recent-window correctness | 8/20 | 8/20 |
| Native correctness | 9/20 | **20/20** |
| Native correctness on answerable questions | 5/16 | **16/16** |
| Direct required-evidence coverage | 34.375% | **100%** |
| Wrong model forget proposals | 1 | **1** |
| Executed Native purges | 91 objects | **0** |
| Model calls | 100 | **120** |

The retention partitions are identical across all 20 cases: 48 correct keep
proposals and 91 forget proposals, of which one incorrectly targets the
durable other-team constraint `unseen-02-e5`. That mistake is **not fixed by
the model**. Review mode leaves all 91 suggestions pending, verifies their
rows remain readable to an authorized caller and sends no Native Forget
requests. No deletion is completed or approved. Reader/planner exclusion is
local to this workflow, not a global privacy-erasure guarantee.

There are **48 search requests plus 20 fresh reference validations**, with a
per-case maximum of four searches and one validation. Both previously missed
evidence chains are answered correctly. For example, `Copperwheel billing`
and `Copperwheel` find the routing rule; the follow-up searches `Ledger review`
and `Ledger` find the directory entry. The final answer is `Billing desk`,
with both actual source events cited after fresh validation. Queries and refs
come from observed evidence, not gold labels. Bounded truncation flags remain
reported in all cases; full required-gold coverage is not exhaustive retrieval.

The extra planning is not free: the sum of two model plans, all search/final
reads and the final answer has **p50 28.96 s / p95 37.89 s**, versus **19.34 /
23.25 s** before. Native searches alone have **p50 50.20 ms / p95 69.77 ms**;
fresh validation is **52.64 / 67.36 ms**. Copilot-call p95 is **13.00 s**,
including CLI/bridge overhead. The Copilot-call summary uses bridge-reported
duration; answer-call timings and read-path sums use runner-observed elapsed
time, including queue wait. The sums exclude retention/setup.
Observed evaluation input/output usage is
**421,659 / 12,220 tokens**, with 120 reported premium requests and no extra
diagnostic calls. This excludes development/authoring usage and is not a bill.

This is a successful known-cohort regression with increased budgets and two
policy changes. It does not establish generalization, production retention
correctness or an end-to-end speed improvement. The original 9/20 run and its
destructive mistake remain published unchanged.

## Selecting a separately frozen evaluation cohort

`--cohort pilot-v1` remains the default and selects the original 20 cases.
`--cohort unseen-synthetic-v1` selects 20 newly authored histories; it does not
modify the original cases or the fixed `lexical-v2` query planner. Freeze the
chosen dataset and implementation in a clean commit before model calls:

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-unseen-01 gpt-6-astra high --allow-copilot \
  --query-policy lexical-v2 --cohort unseen-synthetic-v1
```

The new cohort has **139 events / 20 cases**, 10 English and 10 Japanese:
cross-session preferences, project constraints, corrected values, explicit
forgetting, and workflow failure lessons. Histories contain 6–8 events with
varied evidence positions, similarly named distractors, two evidence-chain
questions and three twice-corrected histories. Four answerable cases place
their evidence in the recent window, rather than making that control empty
of useful information by construction. Gold labels comprise **49 keeps /
90 forgets, 16 scalar answers and four abstentions**.

The author used the public event/retention contracts, not the evaluation
model's answers or private previous traces. The histories and labels were
reviewed before inference; they are nevertheless **project-aware synthetic
authorship**, not independent human histories, blinded review, a public
benchmark or proof of training-data exclusion. "Unseen" refers to this cohort's
first evaluation use. Repeated runs reuse it; a new output directory is not
proof of unseen data. Reports keep external held-out/general qualification
flags false and do not certify prior model exposure.

Model, query-planning source, retention/answer prompts and per-case scoring
formulas are unchanged. A small report generalization supplies the selected
case collection instead of duplicating scoring logic. Its source hash changes
honestly: `pgag-agent-memory-cohort-recipe-v3` binds the dataset, actual scorer,
protected prompt/case/scoring prefix, and query recipe. Previous published
results retain their original identities. Use their recorded source revision
for exact historical reproduction, not the current file hash.

Legacy/v2 runs use 21 isolated tenant identities and at most 100 model calls,
with no retry, query expansion or empty-query fallback. Every arm/case remains
in the report, including invalid and unmeasured outcomes. Keeping model calls
tool-free and passing only selected evidence prevents retrieval from hidden
files, but is not an autonomous real-world agent or a background worker test.

**Retrieval and citation are separate measurements.** Historical
`required_source_recall` is computed from *cited* required IDs; it must not be
interpreted as direct retrieval coverage. The new `evidence_coverage` report
adds `retrieved_required_source_recall` from the validated, budgeted context
actually delivered to the answerer. It preserves a valid denominator,
unknown-before-retrieval outcomes, and non-applicable abstention cases.
An answer failure after a known retrieval does not erase that measured context.
No historical metric or published score is recalculated under a different rule.

### First new-cohort result: generalization remains insufficient

At **`c521329`**, the first `unseen-synthetic-v1` run completed **100 calls /
60 observations** with GPT-6 Astra/high and Copilot CLI 1.0.88. No model call
failed, required response repair or was retried. The query-planning module
hash is exactly the one used in the prior 20/20 regression result.
The [reviewed aggregate](../examples/copilot-memory-unseen-result.json) binds
the separately frozen cohort, actual scorer, recipe and private artifacts.

| Arm | Correct / 20 | Correct on answerable questions / 16 | Abstentions |
|---|---:|---:|---:|
| No memory | 4 (20%) | 0 | 20 |
| Recent two events | 8 (40%) | 4 | 16 |
| Native lexical memory | **9 (45%)** | **5** | 14 |

All arms correctly abstained on the four explicit-forget questions. In the
Native arm, five answerable cases received complete required evidence, one
received half of its two-event chain, and ten received no required evidence.
Direct retrieved coverage is **34.375%**, averaged across all 16 answerable
cases with no unknown denominator. Citation recall happens to equal that value
in this run; the two metrics are still computed separately. Twelve queries
returned some context, so "nonempty recall" alone would hide substantial misses.
The five-point advantage over recent context is not evidence of broadly
reliable memory, nor a controlled causal comparison with the different old cohort.

The traces identify three distinct gaps:

- **Literal vocabulary and incomplete correction context:** `triage inbox`
  misses the stored wording "support shifts"; `Pebblegate warranty` misses
  the retained correction's plural "warranties". Short queries still require
  every lexeme, and a correction need not repeat the original description.
- **Evidence-chain retrieval:** `Copperwheel billing` retrieves the routing
  rule but not the separate directory entry naming the desk. The answer
  returns `Ledger` instead of the required `Billing desk`. This is one
  incorrect non-abstained answer, despite a citation pointing to a real source.
- **Retention:** 48 of 49 gold-keep events were kept; all 90 gold-forget
  events were removed, but one additional durable constraint was purged.
  The mistaken event `unseen-02-e5` belongs to another team's continuing
  review workflow. It is not that question's answer evidence; the relevant
  answer event survived but was missed by retrieval. Aggregate keep recall
  98.33% and forget precision 99.17% are per-case means, not micro-averages.

These are product-quality gaps, not infrastructure failures. No production
data was used: model-selected purge affected only the explicitly consented,
owned synthetic fixture. The finding does not authorize automatic production
deletion or demonstrate that simply replacing the model will solve the problem.
No case, gold label, query prompt or score was tuned after this measurement.
Future retrieval/retention changes require a new recipe and separately reported
reuse of this now-measured cohort.

Native recall latency was **p50 43.10 ms / p95 56.27 ms** over 20 calls.
Copilot calls, including fresh CLI/bridge overhead, were **p50 9.41 s /
p95 12.78 s**. Query-generation + recall + answer sums were **p50 19.34 s /
p95 23.25 s**, excluding retention/setup. Usage was **344,416 input /
7,254 output tokens**, 100 reported premium requests, with no additional
diagnostic model calls in this increment. These are observed usage units,
not a verified bill or production latency qualification.
These counts cover the evaluation transport only, not development or
cohort-authoring assistant usage; total session billing is not measured here.

## Fixed-model Copilot agent-memory pilot (2026-09-25)

The product evaluation requested on 2026-09-25 is separate from database
conformance and production HA acceptance. It asks whether an actual model makes
useful retention/forgetting decisions and answers better with Native memory.
It does not revive the old universal semantic percentages or compare competing
models. The dataset and scorer must be committed before inference; results may
not be used to silently tune the measured dataset or discard failures.

The pilot contains **20 synthetic scenarios / 140 events**, split equally
between English and Japanese: cross-session preferences, project constraints,
corrected values, explicit forgetting, and workflow failure lessons. There are
16 scalar answers and four intended abstentions. Gold retention/source/answer
labels are accessible only to the scorer, never serialized into model prompts.
Three arms share one model: `no_memory`, the last-two-event `recent_window`, and
`pg_agmemory`. Each call starts a fresh Copilot session without CLI memory,
custom instructions or available tools; the bridge verifies the emitted
zero-tool/model metadata before accepting its answer.

For the Native arm, the model partitions events into keep/forget proposals.
Only the owned synthetic database executes the requested purge. A separate
model call generates the recall query; real Native HTTP/RLS/SQL retrieval
supplies the subsequent answer context. The harness also checks denial of a
separate tenant's scope/object. API keys, administrator URLs and signing keys
are never passed in model prompts or copied into the host Copilot process.

```bash
mkdir -p .review-artifacts
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-pilot-01 gpt-6-astra high --allow-copilot --query-policy legacy-v1
```

This requires a clean committed checkout, Apple Container, Node.js and an
authenticated host Copilot CLI. The model and reasoning effort are arguments,
not product constants; use an available compatible Copilot model explicitly.
Initial configuration is GPT-6 Astra/high. Model revision is managed by Copilot:
the CLI name/version, requested/reported model and call usage are recorded,
but exact model-weight revision is **not independently attested**.
At most **100 application calls** are admitted: 20 retention, 20 query and
60 answer calls. There is no application retry or repair of malformed answers.
Copilot may consume subscription credits; failures and diagnostic probes are
not represented as free successful calls.

The overall guest run has a 40-minute ceiling; individual Copilot processes
have a 150-second ceiling. Whole serialized answer prompts are bounded to
8,000 UTF-8 bytes (2,000 for recent context), excluding Copilot's own system
prompt. Actual CLI token/cache/API usage is recorded separately when reported;
unknown usage remains null, and credit units are not presented as verified
monetary billing. Host CLI startup and transport overhead remain part of
observed latency, rather than being relabeled as Native retrieval latency.

The report retains every planned arm/case, failed/invalid/unknown calls,
independent keep/forget scores, mechanically graded answers, citations,
required-source coverage and latency. Failure-inclusive answer accuracy and
valid-answer-only accuracy have explicit denominators. Unknown destructive
outcomes are not scored as safe. A complete run is not automatically a passing
product: `release_qualified` remains false.

This first pilot uses **lexical retrieval**, not fabricated Copilot embeddings.
It measures caller-controlled retention/query/answer behavior, not the existing
background extraction/compaction worker, semantic-vector quality, free-form
reasoning, multimodal memory or a public benchmark score. Existing provider
profiles remain user-configurable; background workers still require `local_http`
and vectors still require their declared 768-dimensional model contract.
The CLI bridge is evaluation-only, not a new production inference backend.
Raw prompts/responses and journals stay private in the new output directory;
publish only reviewed aggregate results. The harness destroys its owned
database/API and removes temporary credentials, never an external dataset.

### Lexical-v2 query planning comparison

The improved policy is explicit: `--query-policy lexical-v2` (the wrapper's new
default). It consumes the same production query-planning contract advertised by
the real Native API, asks the selected model for 1–3 literal terms, validates
the structured plan, and submits its compiled query through the normal SDK.
An incompatible server contract fails before model dispatch and is rechecked
before fixture purge. No server matching/ranking or authorization rule changes.

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-pilot-v2-01 gpt-6-astra high --allow-copilot \
  --query-policy lexical-v2
```

In the measured `0a8be6e` comparison, the original `agent_evaluation.py` source,
scenario/gold data, retention/answer prompts and scoring were byte-identical.
At that revision, `legacy-v1` preserves the original recipe digest and
unstructured query generation. V2 binds a different recipe
digest containing the original recipe, query-planning module and advertised
contract; raw model plans, compiled queries and policy are retained privately.
The 100-call limit, contexts, model choice, no-retry rule and failure denominators
are unchanged. This is a **post-diagnostic repeat of the same regression cohort**,
not blinded held-out evaluation, a model comparison or general product acceptance.
The first result below is never overwritten by a later improvement.

#### Measured lexical-v2 result

The first v2 run at **`0a8be6e`** completed all **100 model calls / 60 observations**
with **GPT-6 Astra/high / Copilot CLI 1.0.88**. There were no malformed/failed
model responses, application retries or browse fallbacks.
[The reviewed v2 aggregate](../examples/copilot-memory-lexical-v2-result.json)
binds the new recipe/module and private artifact checksums; its case digest and
base recipe digest equal the initial experiment.

| Measurement | Original query policy | Lexical-v2 |
|---|---:|---:|
| No-memory correctness | 4/20 | 4/20 |
| Recent-window correctness | 4/20 | 4/20 |
| Native memory correctness | 5/20 | **20/20** |
| Native correctness on answerable questions | 1/16 | **16/16** |
| Required-source recall on answerable questions | 6.25% | **100%** |
| Correct keep / forget decisions | 40 / 100 | 40 / 100 |
| Required events incorrectly purged | 0 | 0 |

The four appropriate abstentions are retained. In a concrete trace, the first
policy's expanded question about report locale becomes `{"terms":["report","locale"]}`,
compiled as `report locale`; the actual Native response then contains the
required retained event. No oracle supplies the terms or answer. RLS, time,
deletion filters and SQL AND matching remain unchanged.

Native recall measured **p50 40.99 ms / p95 52.75 ms** over 20 calls. Copilot
calls measured **p50 8.07 s / p95 11.39 s**; the per-case query-generation,
Native-recall and answer-call sum was **p50 18.38 s / p95 22.98 s**, including
CLI/bridge overhead and excluding retention/setup. That sum was slower than
the original **15.95 / 16.63 s**: this is an accuracy improvement, **not an
end-to-end speed claim**. Input/output usage was **344,886 / 6,401 tokens**
and 100 reported premium requests; billing remains unverified.

This is a successful regression comparison on known synthetic cases, not
independent held-out proof. All general product/release/production qualification
flags remain false, and the original poor result remains published unchanged.

### First complete Copilot measurement: product usefulness not qualified

The unchanged scenario/recipe completed at **`862fb2b`**, using Copilot CLI
**1.0.88 / GPT-6 Astra / high**. All **100 model calls** and **60 arm observations**
completed with zero invalid/failed model responses. Each reported the selected
model/effort and zero tools. The [reviewed aggregate result](../examples/copilot-memory-pilot-result.json)
binds source, case/recipe digests and private report/journal checksums.

| Arm | Mechanically correct / 20 | Abstentions | Correct on the 16 answerable questions |
|---|---:|---:|---:|
| No memory | 4 / 20 (20%) | 20 | 0 / 16 |
| Recent two events | 4 / 20 (20%) | 20 | 0 / 16 |
| Native lexical memory | 5 / 20 (25%) | 19 | **1 / 16 (6.25%)** |

The four correct baseline results are intended abstentions, not recovered
knowledge. Retention decisions matched the explicit synthetic policy in all
20 cases: **40 keeps / 100 forgets**, precision/recall 1.0 and no required event
purged. Nevertheless, Native recall returned anything in only **3/20** queries,
and required-source recall on answerable questions was **6.25%**. Thus this
configuration **does not demonstrate adequate useful memory**, despite correct
retention and structurally valid answers. The five-point difference is not
statistical evidence from a representative workload.

A concrete integration mismatch is visible in the frozen traces: the model
expanded a short question about a report locale into a natural-language
question containing many additional terms. Native lexical search uses
`plainto_tsquery('simple', ...)`, requiring all resulting lexemes; the retained
event uses only a small subset of those terms. The query prompt did not teach
that AND contract. This points to query construction/retrieval integration,
not evidence that a different retention model is needed. No query prompt,
dataset or score was changed after inspecting this result; a retrieval fix
requires a new version and separately reported measurement.

Native recall latency, measured separately over 20 actual calls, was **p50
49.68 ms / p95 62.22 ms**. The 100 Copilot calls were **p50 7.31 s / p95 9.47 s**,
including fresh CLI startup and bridge overhead. The per-case sum of model
query generation, Native recall and model answer was **p50 15.95 s / p95
16.63 s**; this excludes retention/fixture setup and is not a production SLA.
Recorded input/output usage was **339,489 / 6,425 tokens**, with 100 reported
premium requests. Credit accounting is not verified monetary billing.

Four earlier harness attempts remain private evidence: unsupported container
address lookup; a mismatched queue path (one guest dispatch unknown, zero host
Copilot invocations observed); strict JWT startup failure; and an incorrectly
rejected legitimate empty RLS response. The last consumed two baseline model
calls. Two additional transport probes also consumed calls. These **four
diagnostic model calls are separate from the completed 100**, not erased or
pooled into its scores. Fixture-only fixes and real HTTP/SQL regressions did
not change the case or recipe digests. Owned services were removed and no
production data, model-generated external effects or backup erasure was used.

## v0.2.0 release identity

The release profile is `M3-bounded-native-graph-v2`, digest
`37b0379d66341047d2def85621feff9f949cc5a42e3826d3746f51c175e0db0d`.
Relative to v1 it changes only name/service version; all six workloads, warmup/
sample counts, allocations and strict1500ms threshold remain unchanged.
A new exact0.2.0 run completed below. The v0.1.3 results retain their original
implementation/profile bindings and false whole-M3 flags.

Frozen **`4204892fa90fb93a62a24f78545ef89a14abbc2e`** passes
[native run35807408792](https://github.com/rioriost/pg_agmemory/actions/runs/35807408792)
on both architectures: core2,394/113 optional skips, all packaged smokes/ordinary
restore, patched AGE84+59+207 checks, real HTTP and canonical-only recovery.
The independent exact release resource run has396 samples/36 probes, zero errors,
`resource_qualified:true` and deliberately `m3_qualified:false`.

| Shape / visible nodes | SQL p95 ms | AGE p95 ms |
|---|---:|---:|
| Chain / 12 | 28.14 | 116.54 |
| Fanout / 12 | 36.30 | 140.71 |
| Multiseed / 12 | 83.66 | 146.98 |
| Chain / 64 | 29.00 | 238.18 |
| Fanout / 64 | 89.64 | 1,018.09 |
| Multiseed / 64 | 78.09 | 464.18 |

The proof remains enabled (24.3–61.8% of measured AGE wall time); native path
queries account for33.4–61.7%. Publication including packaged CLI/check/build/
ANALYZE/commit takes542.01–1,037.13 ms. Projection storage is1,466,368 bytes;
database12,244,671→22,943,423 bytes; generated WAL15,658,840 bytes.
These are fresh observations, not a claim that version metadata made traversal
faster. The shared native ARM host,6/2 workload CPUs plus1 overhead CPU per VM,
24/8 GiB and all warm/quiescent/non-HTTP limitations remain as declared.
Private evidence is `.review-artifacts/graph-resource-v0.2.0-4204892/`.
The release aggregates independently qualified contracts; it does not edit
historical or current per-artifact whole-M3 flags into activation authority.

## Historical v0.1.3 native graph resource profile

The independent oracle gates paired SQL/native AGE measurements before resource
claims. The frozen recipe covers six 12/64-visible-node strata with an equal-sized
hidden scope, 3 warmup/30 measured pairs each, DB6 vCPU/24 GiB and app2 vCPU/8 GiB.
It uses warm quiescent service calls, not HTTP, full S, concurrent load or model
performance. The gate is p95 **strictly below 1,500 ms** per backend/stratum.
The initial non-exact development run has 396 samples, 36 probes, zero errors
and all timing gates passing, but remains unqualified. Its original report and
later raw-evidence reclassification are separate.

The complete freshness proof accounted for about 24–42% of measured AGE wall
time in that diagnostic; native path queries accounted for 55–63%. These are
summed stage shares, not ratios of percentiles or a hardware-counter profile.
No planner/permission overrides or product changes were needed. Retain the
bounded proof rather than silently substituting a mutation counter; larger
deployments need a separate declared profile. Exact committed measurements
were obtained below. See [runner and limits](operations/README.md#native-graph-resource-profile).

### Exact warm graph run: d1b894d

The clean archived run at **`d1b894dd4b5faa67c2e251604d1d9e9012cf7ba7`**
passes this declared profile: **396 samples, 36 semantic probes, zero errors**.
It reports `resource_qualified:true`, `m3_qualified:false`. Subsequent stricter
raw-probe/digest reclassification also passes; its separate record does not
rewrite the original report or create new timing samples.

| Shape / visible nodes | SQL p95 ms | AGE p95 ms | Projected nodes / revisions | Publication ms | Projection bytes |
|---|---:|---:|---:|---:|---:|
| Chain / 12 | 22.30 | 111.89 | 24 / 24 | 599.98 | 163,840 |
| Fanout / 12 | 39.14 | 146.37 | 24 / 66 | 580.63 | 172,032 |
| Multiseed / 12 | 73.80 | 148.62 | 24 / 50 | 1,052.32 | 172,032 |
| Chain / 64 | 24.58 | 242.73 | 128 / 128 | 633.82 | 245,760 |
| Fanout / 64 | 73.37 | 1,292.52 | 128 / 482 | 662.30 | 417,792 |
| Multiseed / 64 | 68.63 | 705.78 | 128 / 258 | 652.75 | 294,912 |

AGE is slower than SQL on these shapes; passing an absolute bound is **not an
acceleration claim**. The full proof accounts for 24.2–42.3% of measured AGE wall
time, native path queries 54.5–62.9%; all AGE reads execute 19 statements.
The proof remains enabled. Publication includes CLI startup/artifact checks/
physical build/policy installation/ANALYZE/commit, not just graph insertion.
Artifact sizes are 13,910–197,749 bytes. Total projection storage is 1,466,368
bytes (507,904 heap, 540,672 index, plus auxiliary storage); database size grows
12,244,671→23,172,799 bytes and generated WAL is 15,808,832 bytes.

Environment: native Linux/aarch64 on the shared Apple-container host, PostgreSQL
18.6, pgvector0.8.6 and the fixed patched AGE72707aa image
`sha256:5edc81da67cf0a6f5620119dda3077de5d5b972d4ef214faeff89dfedd160a79`.
Configured workload allocations are DB6/application2 CPUs, 24/8 GiB. Apple
Container separately records **one overhead CPU per VM**; the application sees
three CPUs. This is not an exclusive eight-core-host capacity measurement.
JIT stays on at its default threshold100000, shared_buffers128 MiB and work_mem4 MiB;
statement/lock limits stay5 seconds. No hardware perf counters were collected.
Runtime is UID10001, non-owner/NOSUPERUSER/NOBYPASSRLS, with forced label RLS.
Build identity, raw timings, allocation inspection, input hashes and cleanup
results are retained privately in `.review-artifacts/graph-resource-d1b894d/`.
No concurrent writer, physical-host cold, co-resident full-S corpus, or artifact-
maximum scale claim follows from this run.

## v0.1.3 / schema 20 isolated AGE recovery

The explicit recovery-and-disable option preserves all signed latest-state and
canonical matching requirements, then quarantines the matching projection in
the same transaction. Its final comparison reports only the intended registry
difference, never a false full-state match. The 36 new contracts cover successful
and no-op application, rejected authority/lineage/CAS/content/history changes,
call monotonicity, isolation, bounded revisions, atomic rollback and CLI output.
Current-source regeneration and publication remain separate admin operations.
This is a bounded restore extension, not automatic reactivation, arbitrary
newer-content import or a general HA/PITR qualification.

Full-AGE catalog restoration is explicitly **not qualified**: the initial real
dump/restore reached correct quarantine but rebuilding failed on duplicate
`ag_graph_graphid_index`. Retained failure evidence is not replaced by a claim
that canonical recovery also restores AGE allocation/catalog state. The
canonical-only path discards those derived physical structures and uses a fresh
trusted extension plus explicit guarded missing-graph publication.
The real arm64 development run removes the source before restore, compares all
35 canonical fingerprints, retains keys/IDs/old generation, progresses registry
1→2→3 and verifies disabled HTTP409 followed by current/historical native-SQL
equality, three purged denials and reader revocation. It makes zero model calls.
Its 30 offline contracts, 36 quarantine contracts and 22 new/22 existing
publication cases passed separately during development.

Frozen `5a21728` subsequently passed
[run35725941968](https://github.com/rioriost/pg_agmemory/actions/runs/35725941968)
on both architectures: core 2,335 passes/113 optional skips, ordinary v7 restore,
AGE 84 profile contracts/59 original checks/207 enabled cases, real HTTP and the
canonical-only restore. Both new restore reports verify the exact commit and
only the intended registry fingerprint difference; neither claims full-AGE
catalog recovery, automatic activation or full M3 qualification.

## v0.1.2 / schema 20 patched AGE

Exact `0741713` [run35709955807](https://github.com/rioriost/pg_agmemory/actions/runs/35709955807)
passed core and AGE jobs on both native architectures: core 2,269 passes/91 optional
skips each with exact v7 restore; AGE 84 profile contracts, all 59 original probe
checks, 185 enabled cases and the real HTTP smoke. Failed `6f2a8fa`'s description
lint and its wrapping-only correction remain recorded in STATUS. This evidence
does not cover the subsequent v0.1.3 recovery changes.

AGE source `72707aab7ce982bf13cad3d102bd869dab07d64b` is separately pinned and
passes the unchanged 19 native/direct plus 40 fixed-template probe checks.
The enabled-profile runner passes 185 integrated cases and a real non-root HTTP
smoke with native two-hop/SQL equality, atomic publication/rebuild, disable/stale
refusal and explicit SQL selection. It tests the image-installed preload helper,
not a fixture substitute. The exact source/build identity and initial failed
combined-fixture run are preserved separately from the old rc0 failure.

This is correctness/packaging evidence for optional AGE, not a full-S graph cost
benchmark. Native freshness scans are bounded but not constant-time. Current
recovery uses 24 operational fingerprints and refuses enabled registry tenants
before writes; active projection DR and automatic reactivation remain excluded.
Frozen amd64/arm64 core and AGE jobs are separate distribution gates.
See [scope and deployment](operations/README.md#patched-age-enabled-profile).

## v0.1.1 / schema 19 canonical graph artifacts

Exact `dc56d006edd0618dec05ec9cc6df0d3f6623f3c4` passed native run
[35692443083](https://github.com/rioriost/pg_agmemory/actions/runs/35692443083)
on amd64 and arm64: 2,132 passes/31 optional skips each, the artifact production
smoke and exact v6 restore. Pytest took 1035.55/1401.69 seconds. Both artifact
reports show 3 nodes/2 revisions, 2,713 bytes, exact rebuild and source mutation
refusal. Both recovery reports retain 35 payload fingerprints, 20 denials,
11 call reservations and the unchanged stale/non-serving generation receipt.
This historical run does not qualify the later schema-20 AGE increment.

The backend-neutral artifact exporter/checker adds 36 focused contracts
(6 offline and 30 database cases). A non-root production-image smoke exercises
three nodes and both revisions of one relation, checks private/content-free
output, records the digest, rebuilds identical bytes and refuses a later source
mutation. This is deterministic graph build-input construction and verification,
not a new AGE execution, runtime permission cache or serving qualification.
The schema-19 generation coordinator and generic receipt semantics below remain
unchanged; no migration or model call is added.
See [artifact scope](operations/README.md#canonical-graph-artifacts).

## Schema 19 generation metadata

The M3 coordinator now has 44 focused lifecycle/recovery cases plus schema-19
migration/compatibility checks. Its development v6 actual backup drill preserves
one nonempty generation receipt, observes stale input after deletion/ACL replay,
and keeps artifact verification/serving disabled. Operational comparison covers
23 tables; generation history must match exactly and is not a replacement-row
import. These are metadata contracts, not AGE qualification or extension-backed
projection reconstruction. Exact `a017ae5` passed native run
[35689800671](https://github.com/rioriost/pg_agmemory/actions/runs/35689800671)
on amd64/arm64 (2,096 passes/31 optional skips each) including packaged smokes and
v6 recovery; the earlier `50ed5e2` smoke failure remains recorded in STATUS.
This distribution evidence predates and does not certify the artifact increment.
See [current limits](STATUS.md) and [operations](operations/README.md#graph-generation-metadata-schema-19).
The historical resource/model observations and published M2 qualification below
remain bound to their original source versions.

## Published M2: core MVP v0.1.0 / schema 18 complete

The frozen release implementation **`af878fc51fa50cefecca69de2df22edfef2a321b`** passed
[native CI 35565944016](https://github.com/rioriost/pg_agmemory/actions/runs/35565944016):
**1,945 passes / 8 optional live skips** on both amd64 and arm64, all production
smokes and exact v5 backup/application reports. Each report has 35 equal
latest/restored canonical fingerprints, 21 matching operational fingerprints,
five original receipts, 11 reservations and 20 unreadable tombstoned targets.
This run tests service 0.1.0/`m2-core-mvp` itself: pytest took 1397.44 s on amd64
and 1263.10 s on arm64. All 13 derivative-table cohorts retain both purged and
surviving cases. Unknown retries remain refused and consumed quotas are preserved.
The `v0.1.0` publication checkpoint adds only qualification documents; all build
inputs remain identical to the tested commit. The preceding `6d967c4` run remains
separate evidence, not a substitute for this final release run.
See [the normative deployment limits](operations/README.md#m2-core-mvp-deployment).

**Scope revision, 2026-09-19:** the [implementation plan, sections 1.3 and 17–18](PG_AGMEMORY_IMPLEMENTATION_PLAN.md)
now governs acceptance. pg_agmemory is memory infrastructure, not a judgment
system. Human semantic scores and 20 successful agent tasks are not release
gates; neither model comparison nor a new human-label campaign is planned.
One pinned reference memory benchmark remains required as an example, without
a model-quality pass score. The final section records acceptance and later milestones.

The experiment results, score thresholds and harness recipes in the historical section below
are **historical diagnostics through 2026-09-18**, not the revised release policy.
Do not rerun all baseline arms or fill review forms merely to satisfy old gates.
Existing `NOT_MEASURED`, `human_review_verified=false`, `human_quality_qualified=false`
and `m2_qualified=false` fields remain truthful and unchanged; they do not replace
the versioned core acceptance inventory. No semantic measurement is invented.

### One reference memory benchmark

The reproducible sample is the existing **three-call memory lifecycle**, using
one fixed configuration, not a new model comparison or semantic-scoring campaign.
Its machine-readable [result](../examples/reference-memory-result.json) binds
the measured **`e4f5d76` / service 0.0.27 / schema 13** implementation, unchanged
[test recipe](../tests/test_processing_live.py), worker-profile digest and retained
JUnit checksum. The [provider profile](../examples/reference-memory-profile.json)
contains model pins, not a credential. This consolidates recorded evidence;
**it is not a new run on schema 18**.

| Observation from the single case | Recorded result |
|---|---|
| Actual local model operations | One extraction, one 768-dimensional embedding, one compaction; three committed reservations |
| Accepted extraction | One inferred preference, no duplicate or quarantined candidates; not verified truth |
| Compaction retention | Exact typed checkpoint JSON, source/revision links, coverage 1–1 and one uncompacted tail event |
| Restore/hook | Exact typed state and accepted untrusted summary; one required tail item; pending approvals/unknown effect do not become permission to execute |
| Replay/deletion | Stable semantic job IDs, no extra calls; purged snapshot/candidates return 404; all three reservations remain |
| Serialized footprint | 2,405-byte working snapshot; a 2,404-byte hook allowance is explicitly refused |
| Timing | **9.621 s** for one whole pytest case, including fixtures and local inference; not per-request latency or p95 |
| Unmeasured | Semantic claim-retention rate, human/task success, DB growth, process RSS and actual billing |

Four earlier failed extraction/diagnostic calls remain separate from the successful
case; their history is retained below and the two pipeline JUnit checksums are in
the result. They are not zero-cost retries or silently discarded observations.
The separate 600-question held-out retrieval experiment below used the same
model configuration and recorded hybrid Recall@20 **0.9981818182**. It is not part
of this three-call case. S resource results use controlled responses, not these
models, and must not be presented as live-model resource measurements.

**Reproduction:** use an isolated checkout at the recorded SHA to reproduce that
implementation, or clearly label a run on current code as new evidence. Prepare
the pinned models and an authenticated loopback relay inside the runner's network
namespace; no model download, remote/paid backend, wildcard listener or automatic
retry is authorized by these instructions. In the repository's Linux test
environment, provide a **fresh disposable PostgreSQL cluster** (the fixtures
drop memory schemas and provision cluster-wide roles), an owner-private copy of
the profile and the relay credential via its named environment variable:

```bash
# PGAG_TEST_DATABASE_URL: administrator URL for the fresh disposable cluster only.
# PGAG_M2_RELAY_TOKEN: supplied privately; never place it in the JSON or commit it.
export PGAG_LIVE_PROVIDER_CONFIG=/absolute/private/reference-memory-profile.json
PGAG_M2_LIVE_PROCESSING=1 pytest -q -o junit_family=legacy \
  --junitxml=/absolute/private/new-run/processing.xml tests/test_processing_live.py
```

Use a new private output directory per attempt; retain nonzero exits, invalid
outputs and partial accounting. The test never substitutes synthetic model
responses or retries a failed provider call. Generated summaries, timing and
serialized byte counts may differ; reproduction must preserve the recorded
contracts, not force the old numbers. Keep the measured SHA, actual configuration,
installed model digests, full JUnit and failure logs together. Credentials and
disposable database/model processes are the operator's cleanup responsibility.
There is no semantic pass score, and this example does not certify production.

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

### Derived-memory backup recovery (2026-09-21)

Exact **`593087f51b87ecec8edb942c637bb7dd8af0e497`**, service 0.0.35/schema 18,
passed the Linux v5 actual backup drill with `exact_commit_inputs=true`.
It destroys the source cluster before restoring the old dump, preserves two
mixed baseline receipts and replays three subsequent purges. The packaged
authenticated apply restores all five original receipts, 35 canonical and 21
operational fingerprints; 20 tombstoned anchors remain unreadable. Forty-three
metadata-only anchors remain, not erased bytes. Eleven synthetic call reservations,
including unknown/failed/succeeded outcomes, survive without quota refund.

Thirteen derivative tables have nonempty purged and retained cases: extraction
derivation/candidates, episode/assertion embeddings, working snapshots/events,
entity/evidence/relation/revision and tool-effect/revision/reference records.
Retained provenance/vector/graph reads succeed; typed restore preserves pending
approvals and unknown effects, while epoch-stale snapshot reads/resume return
explicit errors. A suppressed payload stays physically present but invisible.
Verification mutations roll back and final fingerprints remain unchanged.
No external model request or automatic service activation occurs.

The focused Linux suite passed 149 cases including 68 drill contracts, with
lint/type checks clean; these counts overlap, not additive. The first expanded
development drill detected ambiguous membership ordering for a principal in
several scopes. Ordering by the full membership key fixed the comparator, not
the equality requirement. Both development logs and the exact report are retained.
Native qualification completed at `6d967c4` in
[run 35563611773](https://github.com/rioriost/pg_agmemory/actions/runs/35563611773),
with 1,945/8 on both architectures and identical declared report checks.
New suppress replay, overlapping targets, altered prefixes, oversized suffixes,
missing/newer canonical content, arbitrary histories and HA/PITR are not qualified.

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
duration limits are unchanged.

The subsequent evidence/workflow commit **`7a2fd88587755aab030496082054121bb4897cde`**
has identical runtime, migration, package, test, script and example inputs to
51293b4. [Native run 35560939791](https://github.com/rioriost/pg_agmemory/actions/runs/35560939791)
completed successfully on both architectures, including all production and
schema-18 operational-state recovery smokes: **1,940 tests / 8 optional skips**
each (amd64 pytest 1344.87 s; arm64 1246.19 s). The earlier deadline failure
remains recorded rather than relabeled as success.
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
This is one synthetic lifecycle, **not** a measured human precision rate or real
task-success result. The former 20-task gate is no longer a project requirement.

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

## Current acceptance evidence and release handoff (2026-09-21)

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
| M2-A contract/evidence inventory | Complete for the declared core profile; `af878fc` native 1,945/8 per architecture and all packaged smokes. No universal authorization or semantic guarantee |
| State, provenance and explicit updates | Exact typed values, revision/span/coverage links, model-space isolation, CAS and temporal oracles; do not count semantic summary/answer quality as structural conformance |
| 10,000 actual adversarial ACL cases | **PASS for the recorded generated HTTP matrix** at `101993a6d40679c73899ee2454f6b2ad0dadafff`; bounded evidence, not exhaustive authorization or M2 proof |
| Worker chaos | Four actual SIGKILL/recovery/purge cases passed at `e4f5d76`; deterministic lease/cancel/revocation/policy regression coverage is separate, not an exhaustive distributed-fault guarantee |
| M2-B deletion/ACL/policy/call-accounting restore | Complete for the declared content-matching v5 profile: local `593087f`, final native at `af878fc`; mixed baseline, 35 canonical/21 operational fingerprints, retained/purged derivatives and 11 reservations; 20 tombstoned anchors unreadable. Missing/newer content and unsupported histories remain refused, with no automatic activation |
| M2-C resource qualification | **Measured at `51293b4`:** full 30-minute S, mixed small deletion, 10k purge, concurrent limit/failure probes and declared guest-cold samples pass their checks. Both native distributions complete at runtime-identical `7a2fd88`. Physical host/device cold and exclusive production capacity are not claimed |
| M2-D one reference memory benchmark | Complete: pinned profile, exact historical source/JUnit binding, typed state/coverage/tail results, observed latency/footprint and retained failures, with current deployment handoff. No new live-model run or semantic threshold |
| M2 release packaging | Complete: `af878fc` qualifies the frozen 0.1.0 build on both native architectures; `v0.1.0` adds only publication documents. Upgrade/restore limits are explicit; historical resource/reference observations keep their original SHA bindings |

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

With M2's v0.1.0 handoff complete, the next implementation milestone is
**M3 graph integration**, with SQL-oracle agreement, generation/rebuild
and authorization/deletion barriers. Keep the qualified M2 restore/resource
limits explicit rather than promoting them to arbitrary-history or production
assurance. General HA/PITR and RPO/RTO remain M5.
