# Mini-swe-agent development-memory evaluation

Status: **revision 2 approved by Astra/xhigh for implementation, subject to the
separate CI gate and pre-edit interface agreement**. No implementation or live
evaluation is approved as complete.

This is the second evaluation stage, following the bounded phase-one findings
published in `b543bd1`. The first paired cost data remain tied to `a31e7c9`;
the later cleanup-only fix does not replace those measurements. Targeted
validation and the final cleanup smoke passed. Measured-source CI completed
with seven successful jobs and one cancelled amd64 Native job, not an
all-jobs pass. Its cancellation and the latest source CI must be resolved
before implementation. The same 40-minute Native amd64 timeout recurred at
`b543bd1`, again with seven successful jobs. The Native CI allowance alone
is increased to 60 minutes without removing checks or changing evaluation
deadlines; the next complete CI result is still a prerequisite.

## Question and scope

Does cross-session memory help a fresh development agent produce correct
code, retain current constraints, and avoid repeating mistakes, at a disclosed
latency and usage cost? Compare:

1. **No memory:** no cross-session narrative, notes, trajectories or memory reads.
2. **Fixed handoff:** one model-written handoff note, at most 2,048 UTF-8 bytes,
   replaced after each nonfinal session.
3. **pg_agmemory:** model-selected assertions and revisions backed by observed
   session evidence; bounded lexical planning and freshly validated retrieval;
   proposed forgetting remains review-only.

This is a small, synthetic development-work pilot, not production
qualification or an independently human-authored benchmark. It measures
actual code artifacts with hidden acceptance cases, not a memory-QA score.
There is no requirement that pg_agmemory outperform handoff notes.

## Pinned agent and inference

Use unmodified upstream `minisweagent.agents.default.DefaultAgent` from
**mini-swe-agent 2.4.6**, release commit
`a83fcae82d2a08f0ee0c688f9d137b3566c097f8`.
Record the installed distribution version, locked wheel/source hash, upstream
identity and dependency lock hash. Add an optional evaluation extra and a
dedicated container target; do not add the agent to production dependencies.
The lock must retain upstream's excluded compromised LiteLLM versions; the
adapter does not invoke LiteLLM or its retry machinery.

The first run uses **GPT-6 Astra/high through the existing Copilot bridge** for
work, handoff writing, memory decisions and query planning. The model and effort
remain configurable and are frozen in each run recipe. No transparent switch
to OpenAI, another model or a local fallback is allowed. OpenAI transport is
out of scope for this first implementation.

The bridge remains tool-free, fresh-call, non-resuming and without custom
instructions, built-in MCPs or remote export. Copilot authentication stays
on the host; no credentials are passed to agents or execution guests.
The host authentication/configuration HOME is not recreated per call.
Fresh-call flags and canaries therefore support, but do not prove, absence
of hidden CLI/provider state. Record CLI version, prompts, audited events
and actual usage. Managed weight revision and monetary cost remain unverified.

The adapter implements the actual v2 Model protocol: `query`,
`format_message`, `format_observation_messages`, `get_template_vars`,
and `serialize`. Actions are strict JSON with exactly one of:
`{"command":"..."}` or `{"final":"..."}`. They are converted to the upstream
action/observation and `Submitted` flow; do not implement a substitute agent
loop. Reject duplicate keys, nonfinite values, extra fields and fenced/repaired
JSON. The first format error terminates the session, with its call charged.

Use `step_limit=16`, `max_consecutive_format_errors=1` and a fresh agent/model/
environment object in a **fresh controller process and HOME for every session**.
Do not rely only on `run()` resetting messages: other upstream counters and
template state persist on a reused object. Set a fresh, empty
`MSWEA_GLOBAL_CONFIG_DIR` before import; upstream loads its `.env` automatically.
Disable upstream monetary cost limiting explicitly, because subscription usage
is not a verified dollar price. Label its zero-initialized internal cost
counter as disabled, never as a measured zero-dollar cost.

## Isolation and allowed continuity

Use three trust domains:

- A trusted host coordinator starts owned guests and the existing inference
  bridge, validates the operation ledger, enforces deadlines and archives
  evidence. Never execute a model command with a host shell.
- A trusted, freshly started Linux controller runs mini-swe-agent and memory
  adapters. A small private file-IPC directory carries typed requests to the
  host. The controller has no agent-accessible shell, and its trajectories,
  memory credentials, task manifest and grader data are never mounted into
  the execution guest.
- A fresh unprivileged Linux execution guest contains only the Python runtime,
  an immutable execution/export helper and the current visible project state.
  It has no network, host mounts, credentials, evaluator source, future task
  briefs, hidden graders, memory database access or previous HOME.

Locally verified Apple Container 1.4.1 supports `--network none` (only loopback,
no route), `--read-only`, `--cap-drop ALL`, numeric unprivileged users, `--tmpfs`
with a size limit, and process/file-size limits. Use a dedicated minimal
execution image, not the repository test image. Writable paths are bounded
tmpfs mounts: workspace 64 MiB, HOME 8 MiB, temporary files 16 MiB.
Use 1 CPU / 512 MiB for execution, at most 64 processes, and an 8 MiB per-file
limit. No privileged mode or external network fallback. Fail preflight if the
required isolation cannot be established.

The host dispatches only fixed operations (`execute`, `export`, `grade`,
and lifecycle operations) for its registered container IDs. A command string
is an argument to a guest process, never interpolated into host shell syntax.
Guest helpers are immutable and launch commands with a minimal environment,
fixed working directory, bounded output and process deadlines. A timed-out
command terminates the session and its entire owned guest; no orphaned task
process is reused.

At a session boundary export only manifest-allowed regular files. Reject
symlinks, hardlinks, special files, traversal, unexpected paths, more than
32 files or more than 256 KiB total. The captured bytes and their hashes define
the submission; stop/remove the task guest before grading the captured bytes
in a new guest. Never grade a mutable live workspace or an agent-written
test/result file.

**All arms receive the same frozen canonical starting tree at each milestone.**
It is the fixture author's reference continuation, not the preceding arm's
submission. This prevents prior coding failure and covert notes in source,
comments, tests, `.git`, caches or generated files from becoming an uncontrolled
memory channel. Grade each submitted artifact before advancing the milestone.
No preceding arm-authored files are imported into the next session.

This deliberately evaluates **stage-gated project handoffs**, not autonomous
development on one continuously evolving agent-owned branch. Report that
limitation prominently. Within a session, ordinary message history and source
files are allowed identically in all arms. Current source and current brief
are authoritative; memory is fallible historical evidence.

## Tasks, blinding and grading

Start with **two small Python/standard-library projects, three sessions each**:
six milestones and eighteen arm/session slots. Each milestone requires real
code editing and has visible smoke examples plus hidden functional and
constraint-preservation cases. At least some requirements concern a prior
transient decision, a later correction, and avoiding a previously exposed
pitfall; do not make every task depend on memory.

Freeze framework code and prompts before a separate Astra/high fixture author
creates the task pack. That author must not implement/tune the memory adapters.
The pack contains current visible trees/briefs, permitted output paths, entry
points and hidden black-box input/expected-output cases. Its private contents
are not supplied to implementation workers or work-model calls. Publish a
hash-only manifest and recipe before the first live call.

No public SWE benchmark is claimed as training-unseen. These newly authored
tasks are held out from implementation/prompt tuning, not guaranteed absent
from model training in a semantic sense. Publish the authoring method and,
after all first outcomes are frozen, the sanitized pack for reproducibility.
Exposed tasks become regression tasks if the framework or prompts are tuned;
fresh holdouts are required for a renewed held-out claim.

Hidden grading is external and deterministic: the trusted controller compares
outputs of the frozen candidate entry point with fixed expected values.
Candidate code runs in a fresh networkless guest for each acceptance case;
neither test code nor expected answers enter that guest. Hidden inputs may be sent to the candidate only
after submission, with no feedback to subsequent work or memory prompts.
Bounded export/grade helpers and controller-owned records determine results,
not candidate exit messages or a candidate-modifiable test harness.

Session success requires a valid submission and all frozen acceptance and
constraint checks to pass. Report artifact-check results separately when the
agent times out or fails to submit. Task-owned tests may help the agent but
cannot change scoring. Tag historical constraints and error categories before
running, so repeated mistakes are measured without post-hoc causal claims.

### Private pack qualification, after authoring and before live calls

The separate fixture author validates the actual pack without model calls,
using the final execution/export/grading images. This is a distinct gate
from the earlier fake-model framework dry run:

- Private reference submissions pass every acceptance case, and the starting
  canonical trees fail the cases that require new milestone work.
- Designated defective submissions fail designated checks, including historical
  constraints. Record a positive/negative-control matrix, not just exit codes.
- Each history-sensitive expected value has a recorded origin in an earlier
  visible requirement. It must not depend on an arbitrary implementation choice
  an arm might have made, contradict current authoritative information, or
  require a prior hidden grading result.
- Packaging audits show identical starting bytes for all arms and no hidden
  graders, references, future briefs or private metadata in visible trees.

Keep pack contents and reference implementations away from implementation
workers. The parent receives qualification status and hashes, not tuning hints.
Pack defects may be corrected before any work-model exposure, with changes
logged and the qualification repeated. If exposure motivates framework/prompt
changes, retire that pack from held-out use and author a new one. Freeze and
publish the final hash-only manifest only after qualification passes.

### Candidate input/output and outcome contract

The manifest specifies an allowlisted Python entry point, invoked with fixed
interpreter options and no candidate-selected grader arguments. A case sends
one UTF-8 JSON value followed by LF and closes stdin. Input is at most 8 KiB;
combined stdout/stderr is at most 16 KiB. Exit status must be zero, and stdout
must contain exactly one JSON value, with only JSON whitespace around it.
Stderr is retained as diagnostics within the combined limit.

Allowed JSON values are objects with string keys, arrays, strings, booleans,
null and integers of magnitude at most 9,007,199,254,740,991. Reject floats,
duplicate keys, nonfinite constants, invalid UTF-8/surrogates and nesting
deeper than 32. Comparison is structural and type-strict: object key order
and surrounding JSON whitespace do not matter; array order, string contents,
case, Unicode code points and value types do. In particular, true is not 1.
No coercion, fuzzy comparison, string trimming, JSON repair or output
commentary is accepted. Invalid application inputs have specified JSON error
results, not a special allowance for nonzero process exits.

| Observation | Frozen classification |
|---|---|
| Valid candidate output equal to expected value | Acceptance check passed |
| Valid but unequal candidate output | Acceptance check failed |
| Candidate nonzero exit, syntax/import error, malformed output or output overflow | Acceptance check failed, with reason |
| Candidate process started and exceeds its 5 s deadline; teardown confirmed | Acceptance check failed, candidate timeout |
| Guest cannot start, trusted helper/protocol fails, or capture cannot be read reliably | Infrastructure-unknown, not a candidate answer |
| Session teardown leaves no captured artifact | Artifact checks unavailable; do not reconstruct a submission |
| Cleanup cannot be verified | Run invalid; no further live dispatches |

Only controller-owned comparison records score cases. Candidate programs
cannot provide test counts or declare their own acceptance. Record whether
the guest and candidate actually started to support these classifications.

## Memory-arm contract

Memory delivered to the work agent is capped at **2,048 UTF-8 bytes** in both
memory arms, under one common prompt envelope. Check the final delivered text,
not an upstream proxy counter. No-memory supplies the empty section. The
current task/source limits and work-step limits are identical.

Only session-visible inputs, agent messages and bounded tool observations may
feed handoff/capture. Never include hidden grades, expected answers or future
briefs. Use a deterministic, explicitly reported 24 KiB boundary transcript
cap, retaining the current brief and recent visible messages; omissions are
recorded. Handoff and pg_agmemory receive the same boundary transcript.

After sessions one and two of each project:

- Fixed handoff makes exactly one model call using the previous note and
  boundary transcript, producing a replacement note within the byte cap.
  Oversized or malformed output is a failed boundary, not silently truncated.
- pg_agmemory first Observes that transcript, then makes exactly one structured
  decision call. Supply a bounded inventory of current assertion references
  and contents fetched from Native, not a private text cache. The model can
  create at most six facts (each at most 256 UTF-8 bytes), revise at most four
  known current facts, and propose forgetting known facts. Validate identities,
  provenance and limits before any mutation. Retained facts not mentioned
  remain retained. Record explicit decisions, revisions and Native receipts.

Cap each project at twelve current assertions and inventory delivery at
16 KiB. An inventory that cannot be retrieved completely under its declared
limits is a failed boundary, not a partial success. Revisions require current
Native references and source provenance. No helper may invent a reference or
silently coerce a malformed proposal.

Observe episodes are **provenance-only**, never a second retrieval channel.
Search and final validation explicitly request assertion objects, current
temporal selection, and the run/project's scope. Validate every selected
reference against the registered current assertion inventory; unexpected
objects/revisions are an error. Do not inject the entire registry as required
references into a search, which would bypass lexical matching.
Only references selected by the bounded workflow enter final validation.

The maintenance model sees an allowlist of active current assertion IDs/
revisions and fact text, plus pending IDs/status **without pending contents**,
together with the common boundary
transcript. It does not see source episodes, historical revision contents,
explanation/provenance quotes, hidden grades or a private transcript archive.
Capture code may use episode references for provenance without rendering their
contents again to retrieval planning or the work agent.

Use `review_retention` for proposed exclusions. Pending references remain
readable independently, are excluded only from this workflow, and cannot
authorize Native Forget, suppression, epoch mutation or purge. Metadata
persisted outside Native consists only of references, review state and event
identities, not a second memory-content store. Preserve negative decisions
and verify zero destructive calls in the evaluation arm.
Pending exclusions are monotonic in this pilot: a later decision cannot revise
or reactivate a pending fact. This is not a general retention-lifecycle design.
Zero created facts and zero matches are legitimate outcomes. With no registered
eligible facts, deliver empty memory without planning or browse calls. With
zero observed assertions, record an explicit empty review event rather than
calling `review_retention`, which requires at least one reference. A valid
decision has all required create/revise/propose-forget lists, even when empty;
missing fields are not defaulted.
Create/revise proposals must cite a checked span of the current boundary
transcript, not a quotation from an inventory or old source episode. Pending
exclusion is reference-scoped, not a claim of semantic erasure: a genuinely
new visible statement can reintroduce information, and proposal quality must
still be reported rather than assumed correct.

Before sessions two and three, pg_agmemory uses the existing bounded
sequential planner: at most four one-query rounds, explicit
`en-snowball-v1`, at most eight selected items, and fresh final validation.
Use the existing round-robin admission and pending-reference exclusions.
No empty-query fallback or extra diagnostic model search is permitted.
Render the validated Native context within the shared 2,048-byte delivery cap;
retain whole items and report omissions rather than clipping facts/citations.
No separate reader-model answer is generated: the work agent consumes the
memory and performs the coding task.

The first planner question is a deterministic prefix of the current public
brief: at most 512 Unicode code points and 1,024 UTF-8 bytes, cut only between
code points, with omission recorded. Do not author per-case privileged search
hints. Follow-up prompts may add only permitted assertion results and bounded
count feedback, never provenance episodes or hidden/cross-session transcripts.
The work agent in every arm still receives the complete current visible brief.
Test with an episode-only nonce, a pending assertion and a historical revision:
none may reach a later planner/work prompt via search, inventory, explanation
or final-validation rendering.

Use a separate tenant/scope identity per run/project/arm and fresh authorized
requests. A memory write failure or uncertain commit is not retried with a
new identity; preserve the outcome and stop dependent sessions for that
project/arm. Unrelated project/arm slots may proceed only if isolation and
the overall transport remain healthy.

## Budgets and schedule

Freeze all limits in the recipe before calls:

| Resource | Ceiling / behavior |
|---|---|
| Work calls | 16 per session; 288 over eighteen slots |
| Handoff writes | 4 calls total |
| Memory decisions | 4 calls total |
| Memory query planning | 16 calls total |
| Isolation canaries | 6 calls total, two per arm |
| Coordinator-dispatched model invocations | **318**, including failed/uncertain dispatches; no uncounted helpers |
| One model call / response wait | Existing 150 s / 180 s deadlines |
| Model prompt / response | Existing 65,536-byte transport limits; serialized request at most 70,000 bytes; action command at most 8 KiB |
| Current brief / system prompt | At most 4,096 UTF-8 bytes each |
| Rendered retrieval-planner prompt | Existing 8,000-byte limit, checked before dispatch |
| Session wall time | 900 s for work calls/tools; cancellation grace at most 15 s, reported separately |
| Whole live run | 10,800 s; stop on deadline, retain unrun slots |
| One shell action | 30 s; 16 KiB capture, at most 2 KiB delivered with explicit omission marker |
| Candidate grading | At most 24 black-box cases per milestone; 5 s and 16 KiB per invocation |
| Local concurrency | One active work/model operation; one execution or grading guest |
| Memory server | Owned 2 CPU / 2 GiB PostgreSQL; no shared or production DB |

The 318-invocation ceiling is 288 + 4 + 4 + 16 + 6. Use one existing bridge
instance per arm, each at most 160 calls, with an independent global ledger.
The work-step ceiling is equal; memory maintenance consumes additional,
explicitly reported calls, rather than secretly increasing the work budget.
Account for actual API requests and usage as well as logical calls.

This is **not a hard ceiling of 318 provider API requests**. The CLI can
perform internal operations that the coordinator cannot admit individually.
For the initial protocol, an audited successful invocation must report exactly
one API request. A multi-request response or missing/invalid usage stops
further live dispatches with `failed_transport_accounting`; preserve reported
or unknown usage and unrun slots. Do not claim that detecting a violation
afterward prevented it. No harness retry is allowed; absence of undocumented
CLI/provider-internal retries is not attested.

The unmodified agent retains its history. Sixteen steps are an upper bound,
not a guarantee that sixteen maximum-size actions fit. Check fully serialized
prompts and bridge envelopes before dispatch, including worst-case UTF-8 and
JSON escaping. Local prompt-budget exhaustion is a distinct failed-session
reason with no provider dispatch, not a provider error or permission to add
summarization/repair calls. Report upstream query attempts, admitted transport
invocations and provider-reported requests separately.

Order arms with a preregistered balanced rotation across the six milestones,
while maintaining per-project session order. All local operations are serial;
archive wall-clock order and provider timings. This reduces simple order bias
without claiming a randomized causal effect or independent-host capacity.

### Dispatch and cancellation ownership

The host owns a monotonically increasing global invocation ordinal. Before
writing any bridge request it validates prompt/envelope limits, reserves the
global/per-arm budget, and durably records the owning session, arm, bridge
identity, bridge-local call ID and prompt hash. Bridge-local IDs increase
across controller restarts and are never reused; `(bridge identity, call ID)`
is unique even though each arm's existing bridge starts at 000001.
Controllers cannot allocate IDs or spend directly from the bridge queue.

Each IPC request carries protocol version, registered session ID, monotonic
session sequence, operation and a strict operation-specific body. Bind replies
to that tuple; reject stale, duplicate, cross-session or unexpected replies.
Only the host can map it to a bridge call or an owned guest operation.

On a hard deadline, stop admission first, mark any active invocation
failed/uncertain, and terminate the specifically owned controller, execution
guest and active arm's bridge/child processes. Await process exit and
inventory-verified guest removal before another operation begins. Archive any
late output, but never deliver it to a different session or retry its prompt.
If cancellation terminates an arm's bridge, its remaining slots are unrun;
do not transparently restart that bridge. Other arms can proceed only after
termination is acknowledged and the overall coordinator remains healthy.
Deadlines trigger cancellation, not merely an admission cutoff; the
150-second child deadline is not a reason to keep an active call running.
Allow at most 15 seconds of separately recorded termination/cleanup grace.
An unacknowledged termination after that grace is an explicit cleanup failure
and invalidates the run; do not claim exact deadline compliance or continue
dispatching while an owned process/resource remains uncertain.

## Failures and isolation gates

Before task calls, deterministic checks prove that fresh process/agent/HOME
state is used, no inherited `.env` is loaded, the execution guest lacks network
and private mounts, and previous files/environment variables do not carry over.
Use fake models for full three-arm protocol tests.

Then run a two-call random-information canary for each arm with all explicit
memory disabled: one fresh call sees a generated nonce, another fresh call is
asked for it without receiving it. Record exact inputs, outputs and nonce
hashes. Pair this with separate filesystem/process carryover probes. A canary
match aborts live evaluation; a mismatch is supporting evidence, not proof
that undocumented provider memory cannot exist.

An ordinary shell nonzero exit is an observation within the fixed budget.
A model transport/format failure, step exhaustion or session deadline is a
failed session, not grounds for JSON repair, model retry, a replacement answer
or human rescue. A subsequent boundary memory call is a preregistered
maintenance operation, not a retry of the failed work call, and is allowed
only if the bridge and accounting remain healthy. Missing usage follows the
stricter transport-accounting stop rule above.

An invalid memory boundary makes dependent sessions unrun/failed in the
original denominator; it never silently becomes the no-memory arm. Isolation
failure, cleanup failure or coordinator corruption invalidates the run and
stops further calls. Grader infrastructure failures are unknown outcomes,
not incorrect candidate answers or passing tests. Retain every first outcome
and separately label any later diagnostic or regression run.

## Evidence and reporting

Archive in a new private directory: source and dependency identities, image
digests, guest isolation manifests, frozen task/recipe hashes, ordered events,
all model requests/responses/usage, tool outcomes, submitted file bytes/hashes,
memory decisions/references/receipts, pending review state and grader results.
Keep credentials out of artifacts; clean only owned resources and verify
absence before reporting cleanup complete.

Publish per-project/session/arm results, not just a winning aggregate:
task success, hidden-check and constraint coverage, repeated-error categories,
failed/unrun/unknown slots, intervention count, tool/model/helper counts,
elapsed time split by work/retrieval/capture/maintenance/grading, delivered
memory bytes, truncation, storage and actual token/API/premium/nano-AIU usage.
Do not sum overlapping token categories or invent dollar prices.
Report independent source/reference validity separately from task usefulness.
One eighteen-slot pilot supports descriptive findings, not generalization or
statistical superiority claims.

## Implementation ownership and gates

After Astra/xhigh review and resolution of meaningful findings:

- Astra/high worker A owns `development_memory.py` and its tests: capture/
  revision/review decisions, bounded planning, Native provenance and budgets.
- Astra/high worker B owns `development_evaluation.py`, `development_agent.py`,
  the fresh-controller entry point and their tests: typed contracts, real
  DefaultAgent adapters, state isolation and evidence events.
- Astra/xhigh parent owns the host coordinator and its Node tests, lifecycle
  wrapper, dependency/lock/container integration, docs and integration gates.
  Agree on strict IPC and memory interfaces before parallel edits.

Implementation must pass targeted type/lint/unit tests, real DefaultAgent
fake-model trajectories, Linux guest isolation/cleanup fault tests, Native
memory round trips and a complete no-model three-arm dry run. No source overlays
or host Python execution. Prior phase-one CI failures, if any, must be resolved
before these implementation tasks start.

Only then does a separate Astra/high author create and privately qualify the
held-out pack, including reference and negative-control runs on the final images.
Freeze the final pack hashes, recipe and framework source, perform canaries, execute the
first comparison once, audit evidence, and publish all outcomes. No user API
credentials or paid alternative provider are required for this Copilot-first
plan.

## Verified upstream references

- [Release v2.4.6](https://github.com/SWE-agent/mini-swe-agent/releases/tag/v2.4.6)
- [DefaultAgent at the pinned release](https://github.com/SWE-agent/mini-swe-agent/blob/a83fcae82d2a08f0ee0c688f9d137b3566c097f8/src/minisweagent/agents/default.py)
- [Model/Environment protocols and global dotenv loading](https://github.com/SWE-agent/mini-swe-agent/blob/a83fcae82d2a08f0ee0c688f9d137b3566c097f8/src/minisweagent/__init__.py)
- [Control-flow exceptions](https://github.com/SWE-agent/mini-swe-agent/blob/a83fcae82d2a08f0ee0c688f9d137b3566c097f8/src/minisweagent/exceptions.py)

## Design review record

The initial Astra/xhigh review found four implementation blockers. Revision 2
was re-reviewed with **no remaining true design blockers**:

| Finding | Resolution in revision 2 |
|---|---|
| Raw episodes could bypass assertion selection and pending exclusions | Assertion-only current retrieval, rendering allowlists, provenance-only episodes and explicit empty states |
| Final held-out pack lacked qualification | Private final-image reference/negative-control, historical-origin and packaging gates |
| Invocation cap could be mistaken for a provider-request cap | Separate dispatch/API accounting, internal-behavior caveat and fail-closed usage condition |
| Grading/cancellation allowed inconsistent outcomes | Strict JSON comparison, outcome table, host-owned admission/IDs and acknowledged bounded teardown |

The review also resolved prompt-admission limits, including UTF-8/envelope
expansion, without adding inference-based summarization. This is design
approval only. The CI prerequisite, interface agreement, implementation
validation, private task qualification and live isolation gates remain.
