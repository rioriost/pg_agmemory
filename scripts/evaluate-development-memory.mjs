import { randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { ControllerSession } from "./development-eval-controller.mjs";
import { NativeInfrastructure } from "./development-eval-infrastructure.mjs";
import { artifactFromFiles, qualifyPack, schedule, validatePack } from "./development-eval-pack.mjs";
import {
  ARMS, canonicalJson, EvaluationError, InvocationLedger, MAINTENANCE_PROTOCOL, parseJson, PROTOCOL,
  requireCondition, RunBudget, sha256,
} from "./development-eval-protocol.mjs";
import { ExecutionGuest, runProcess } from "./development-eval-sandbox.mjs";
import {
  CopilotTransport, Journal, privateDirectory, readPrivate, writeNew,
} from "./development-eval-transport.mjs";

const SYNTHETIC_USAGE = Object.freeze({
  input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
  reasoning_tokens: null, api_requests: 1, premium_requests: 0, nano_aiu: null,
  api_duration_ms: 0, monetary_cost_verified: false,
});

export class ScriptedTransport {
  constructor(ledger, responses, record) {
    this.ledger = ledger;
    this.responses = responses;
    this.record = record;
    this.position = 0;
  }

  async invoke(context) {
    const response = this.responses[this.position];
    requireCondition(response !== undefined && response.arm === context.arm
      && response.phase === context.phase, "scripted_response_mismatch");
    const admitted = this.ledger.reserve(context);
    this.position += 1;
    this.record({ kind: "scripted_response", receipt: admitted.receipt,
      response_sha256: sha256(response.text), real_model_call: false });
    return { text: response.text, receipt_ref: admitted.receipt,
      usage: SYNTHETIC_USAGE, duration_seconds: 0 };
  }

  async close() {}
}

async function runCanaries(transport, record, deadline, signal) {
  for (const arm of ARMS) {
    const nonce = randomBytes(24).toString("hex");
    const first = await transport.invoke({
      arm, sessionId: `canary-${arm}`, slotId: `canary-${arm}`, phase: "canary",
      sequence: 1, prompt: `Return exactly this nonce, without quotes or commentary: ${nonce}`,
    }, { deadline, signal });
    requireCondition(first.text === nonce, "canary_acknowledgement_failed");
    const second = await transport.invoke({
      arm, sessionId: `canary-${arm}`, slotId: `canary-${arm}`, phase: "canary",
      sequence: 2, prompt: "What exact nonce was supplied in your previous independent call? "
        + "If it is unavailable in this call, return UNKNOWN. Do not invent a nonce.",
    }, { deadline, signal });
    const leaked = second.text.includes(nonce);
    record({ kind: "isolation_canary", arm, nonce_sha256: sha256(nonce),
      first_receipt: first.receipt_ref, second_receipt: second.receipt_ref, leaked });
    requireCondition(!leaked, "isolation_canary_failed");
  }
}

function usageSummary(records, realModels) {
  if (!realModels) return {
    real_model_invocations: 0, provider_usage: null, synthetic_usage_not_measured: true,
  };
  const admitted = records.filter((event) => event.kind === "model_admitted");
  const returned = records.filter((event) => event.kind === "model_response_received");
  const measured = returned.filter((event) => event.usage !== null
    && typeof event.usage === "object" && !Array.isArray(event.usage));
  const fields = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "api_requests", "premium_requests", "nano_aiu", "api_duration_ms"];
  const totals = Object.fromEntries(fields.map((field) => [field,
    measured.every((event) => Number.isFinite(event.usage[field]) && event.usage[field] >= 0)
      ? measured.reduce((sum, event) => sum + event.usage[field], 0) : null]));
  return {
    real_model_invocations: admitted.length, calls_with_reported_usage: measured.length,
    calls_with_unknown_provider_requests: admitted.length - measured.filter((event) =>
      Number.isSafeInteger(event.usage.api_requests) && event.usage.api_requests >= 0).length,
    known_usage_subtotal: totals, monetary_cost_verified: false,
    overlapping_token_categories_not_summed: true,
  };
}

export async function gradeCase(guest, slot, sample, artifact, budget) {
  let guestStarted = false;
  try {
    budget.assertActive();
    await guest.start(artifact.files, { signal: budget.signal, deadline: budget.deadline });
    guestStarted = true;
    budget.assertActive();
    const result = await guest.grade(slot.entry_point, canonicalJson(sample.input),
      canonicalJson(sample.expected), { signal: budget.signal, deadline: budget.deadline });
    budget.assertActive();
    if (result.status === "infrastructure_unknown") {
      const error = new EvaluationError("grader_infrastructure_failed");
      error.grade = { case_id: sample.case_id, ...result, tags: sample.tags };
      throw error;
    }
    return { case_id: sample.case_id, ...result, tags: sample.tags };
  } catch (error) {
    error.grade ??= {
      case_id: sample.case_id, artifact_sha256: artifact.tree_sha256,
      status: "infrastructure_unknown", reason: error.code ?? "grader_infrastructure_failed",
      tags: sample.tags, guest_started: guestStarted || guest.started === true,
      candidate_started: guest.lastCandidate?.candidate_started ?? false,
      exit_code: guest.lastCandidate?.exit_code ?? null,
      stdout_sha256: guest.lastCandidate?.stdout_sha256 ?? null,
      stderr_sha256: guest.lastCandidate?.stderr_sha256 ?? null,
      duration_seconds: guest.lastCandidate?.duration_seconds ?? null,
    };
    throw error;
  }
}

export async function runEvaluation({
  pack, directory, runId, runtimeImage, executionImage, model, effort,
  sourceRevision, scriptedResponses = null, durationSeconds = 10800, signal: outerSignal,
  allowCopilot = false,
}) {
  validatePack(pack);
  const realModels = scriptedResponses === null;
  requireCondition(!realModels || allowCopilot, "explicit_live_permission_required");
  requireCondition(durationSeconds === 10800, "fixed_run_deadline_required");
  await privateDirectory(directory);
  const events = [];
  const journal = new Journal(path.join(directory, "events.jsonl"));
  const record = (event) => { journal.record(event); events.push(event); };
  const recipe = {
    format: "pgag-development-recipe-v1", run_id: runId, source_revision: sourceRevision,
    memory_maintenance_protocol: MAINTENANCE_PROTOCOL,
    pack_sha256: sha256(canonicalJson(pack)), runtime_image: runtimeImage,
    execution_image: executionImage, model, reasoning_effort: effort,
    real_models: realModels, coordinator_invocation_limit: 318, work_steps: 16,
    memory_bytes: 2048, fresh_canonical_milestones: true, automatic_retry: false,
    provider_internal_retry_absence_verified: false,
    dependency_lock_sha256: sha256(await fs.readFile(new URL("../uv.lock", import.meta.url))),
    mini_swe_agent: { version: "2.4.6", commit: "a83fcae82d2a08f0ee0c688f9d137b3566c097f8" },
  };
  await writeNew(path.join(directory, "recipe.json"), recipe);
  const recipeHash = sha256(canonicalJson(recipe));
  const infrastructure = new NativeInfrastructure({
    runId, directory: path.join(directory, "runtime"), runtimeImage, record,
  });
  const ledger = new InvocationLedger({ record, runId, model, effort });
  const bridgeDirectory = path.join(directory, "bridge");
  await privateDirectory(bridgeDirectory, { create: true });
  const transport = realModels
    ? new CopilotTransport({ directory: bridgeDirectory, runId, model, effort, ledger, record })
    : new ScriptedTransport(ledger, scriptedResponses, record);
  const slots = schedule(pack);
  const outcomes = [];
  const memory = new Map();
  const blocked = new Map();
  const guests = [];
  let operation = 0;
  let failure = null;
  let activeOutcome = null;
  let storageBefore = null;
  let storageAfter = null;
  const artifactDirectory = path.join(directory, "artifacts");
  await privateDirectory(artifactDirectory, { create: true });
  const makeGuest = (purpose) => {
    const guest = new ExecutionGuest({
      name: `${runId}-${purpose}-${++operation}`, image: executionImage, record,
    });
    guests.push(guest);
    return guest;
  };
  const started = Date.now();
  const budget = new RunBudget(durationSeconds * 1000, { signal: outerSignal });
  const { signal, deadline } = budget;
  const stopAdmission = () => ledger.stop();
  signal.addEventListener("abort", stopAdmission, { once: true });
  let cleanupFailures = [];
  let cleanupDeadline = Infinity;
  try {
    budget.assertActive();
    await infrastructure.start(pack.projects.map((project) => project.project_id), { signal, deadline });
    storageBefore = await infrastructure.storageStatus({ signal, deadline });
    budget.assertActive();
    if (realModels) await runCanaries(transport, record, deadline, signal);
    for (const slot of slots) {
      budget.assertActive();
      requireCondition(!ledger.stopped, "run_admission_stopped");
      const key = `${slot.project_id}:${slot.arm}`;
      if (blocked.has(key)) {
        outcomes.push({ slot_id: slot.slot_id, project_id: slot.project_id, milestone: slot.milestone,
          arm: slot.arm, status: "unrun", reason: blocked.get(key), task_success: false });
        continue;
      }
      activeOutcome = {
        slot_id: slot.slot_id, project_id: slot.project_id, milestone: slot.milestone, arm: slot.arm,
        status: "started", reason: null, task_success: false, artifact_sha256: null,
        checks: [], work_result: null, boundary_result: null,
      };
      const milestone = pack.projects.find((project) => project.project_id === slot.project_id)
        .milestones[slot.milestone - 1];
      activeOutcome.checks = milestone.cases.map((sample) => ({
        case_id: sample.case_id, status: "unavailable", reason: "run_stopped_before_check",
        tags: sample.tags, artifact_sha256: null,
        guest_started: false, candidate_started: false, exit_code: null,
        stdout_sha256: null, stderr_sha256: null, duration_seconds: null,
      }));
      outcomes.push(activeOutcome);
      const provisioned = infrastructure.binding(slot.project_id, slot.arm);
      const binding = { run_id: runId, project_id: slot.project_id, arm: slot.arm,
        scope_id: provisioned.scope_id };
      const common = {
        protocol: PROTOCOL, run_id: runId,
        memory_maintenance_protocol: recipe.memory_maintenance_protocol,
        slot: { project_id: slot.project_id, milestone: slot.milestone, arm: slot.arm },
        recipe_sha256: recipeHash, model: { model, reasoning_effort: effort },
        memory_binding: binding, memory_state: memory.get(key) ?? null,
      };
      const initial = artifactFromFiles(slot.files, slot.allowed_paths);
      activeOutcome.starting_tree_sha256 = initial.tree_sha256;
      const guest = makeGuest("work");
      await guest.start(slot.files, { signal, deadline });
      budget.assertActive();
      const workId = `${runId}-work-${operation}`;
      const work = await new ControllerSession({
        infrastructure, transport, guest, record, runDeadline: deadline, signal,
        config: {
          ...common, mode: "work", session_id: workId, brief: slot.brief,
          starting_tree_sha256: initial.tree_sha256, allowed_output_paths: slot.allowed_paths,
          entry_point: slot.entry_point,
        },
      }).run();
      activeOutcome.work_result = work;
      activeOutcome.status = work.status === "submitted" ? "submitted" : "failed";
      activeOutcome.reason = work.reason;
      let artifact = null;
      if (!guest.closed) {
        try {
          artifact = await guest.export(slot.allowed_paths, { signal, deadline });
          activeOutcome.artifact_sha256 = artifact.tree_sha256;
          await writeNew(path.join(artifactDirectory, `${slot.slot_id}.json`), artifact);
          budget.assertActive();
        } catch (error) {
          const rejected = new Set([
            "unexpected_file", "unexpected_directory", "artifact_symlink",
            "artifact_regular_single_link_required", "artifact_limit", "PermissionError",
          ]);
          if (error.guest_operation !== "export" || !rejected.has(error.guest_code)) throw error;
          await guest.close();
          activeOutcome.status = "failed";
          activeOutcome.reason = "artifact_rejected";
          record({ kind: "artifact_rejected", slot_id: slot.slot_id, reason: error.guest_code });
        }
      }
      let boundary = null;
      budget.assertActive();
      if (slot.milestone < 3 && slot.arm !== "no_memory") {
        if (work.transcript === null || ledger.stopped) {
          blocked.set(key, "boundary_input_or_transport_unavailable");
        } else {
          const boundaryId = `${runId}-boundary-${operation}`;
          const keys = slot.arm === "pg_agmemory" ? {
            observe: `${boundaryId}-observe`,
            create: Array.from({ length: 6 }, (_, i) => `${boundaryId}-create-${i}`),
            revise: Array.from({ length: 4 }, (_, i) => `${boundaryId}-revise-${i}`),
          } : null;
          record({ kind: "boundary_keys_reserved", boundary_id: boundaryId, keys });
          boundary = await new ControllerSession({
            infrastructure, transport, guest: null, record, runDeadline: deadline, signal,
            config: { ...common, mode: "boundary", session_id: boundaryId,
              transcript: work.transcript, boundary_id: boundaryId, keys },
          }).run();
          activeOutcome.boundary_result = boundary;
          if (boundary.status === "boundary_completed") memory.set(key, boundary.memory_state);
          else blocked.set(key, "memory_boundary_failed");
        }
      }
      const checks = activeOutcome.checks;
      for (const [index, sample] of milestone.cases.entries()) {
        budget.assertActive();
        if (artifact === null) {
          checks[index].reason = "no_captured_artifact";
          continue;
        }
        const grader = makeGuest("grade");
        record({ kind: "grade_case_started", slot_id: slot.slot_id, case_id: sample.case_id,
          guest: grader.name, artifact_sha256: artifact.tree_sha256,
          input_sha256: sha256(canonicalJson(sample.input) + "\n"),
          expected_sha256: sha256(canonicalJson(sample.expected)) });
        try {
          checks[index] = await gradeCase(grader, slot, sample, artifact, budget);
        } catch (error) {
          checks[index] = error.grade ?? { ...checks[index], status: "infrastructure_unknown",
            reason: error.code ?? "grader_infrastructure_failed", artifact_sha256: artifact.tree_sha256 };
          throw error;
        }
      }
      budget.assertActive();
      activeOutcome.task_success = activeOutcome.status === "submitted"
        && artifact !== null && checks.every((check) => check.status === "passed");
      record({ kind: "slot_completed", slot_id: slot.slot_id, task_success: activeOutcome.task_success,
        status: activeOutcome.status });
      activeOutcome = null;
    }
    if (!realModels) requireCondition(transport.position === scriptedResponses.length,
      "unused_scripted_responses");
    storageAfter = await infrastructure.storageStatus({ signal, deadline });
    budget.assertActive();
  } catch (error) {
    failure = error.code ?? signal.reason?.code ?? "coordinator_failed";
    cleanupDeadline = Math.min(error.cleanupDeadline ?? Infinity,
      signal.reason?.cleanupDeadline ?? Infinity);
    if (activeOutcome) {
      activeOutcome.run_failure = failure;
      activeOutcome.outcome_unknown = true;
      activeOutcome.task_success = activeOutcome.status === "failed"
        || activeOutcome.checks.some((check) => check.status === "failed") ? false : null;
      if (activeOutcome.status === "started") activeOutcome.status = "infrastructure_unknown";
    }
    ledger.stop();
    record({ kind: "evaluation_failed", code: failure });
  } finally {
    budget.close();
    signal.removeEventListener("abort", stopAdmission);
    const cleanupStarted = Date.now();
    cleanupDeadline = Math.min(cleanupDeadline, cleanupStarted + 15000,
      signal.reason?.cleanupDeadline ?? Infinity);
    const cleanup = await Promise.allSettled([
      transport.close({ deadline: cleanupDeadline }),
      ...guests.map((guest) => guest.close({ deadline: cleanupDeadline })),
      infrastructure.close({ deadline: cleanupDeadline }),
    ]);
    cleanupFailures = cleanup.filter((outcome) => outcome.status === "rejected")
      .map((outcome) => outcome.reason.code ?? "cleanup_failed");
    const elapsed = Date.now() - cleanupStarted;
    if (elapsed > 15000) cleanupFailures.push("cleanup_grace_exceeded");
    if (cleanupFailures.length && failure === null) failure = "owned_cleanup_failed";
    record({ kind: "evaluation_cleanup", complete: cleanupFailures.length === 0,
      elapsed_ms: elapsed, failures: cleanupFailures });
  }
  for (const slot of slots) {
    if (!outcomes.some((outcome) => outcome.slot_id === slot.slot_id)) {
      outcomes.push({ slot_id: slot.slot_id, project_id: slot.project_id, milestone: slot.milestone,
        arm: slot.arm, status: "unrun", reason: failure ?? "not_started", task_success: false });
    }
  }
  outcomes.sort((a, b) => slots.findIndex((slot) => slot.slot_id === a.slot_id)
    - slots.findIndex((slot) => slot.slot_id === b.slot_id));
  const summary = {
    format: "pgag-development-evaluation-v1", run_id: runId,
    memory_maintenance_protocol: recipe.memory_maintenance_protocol,
    status: failure === null ? "completed" : "failed", failure, real_models: realModels,
    cleanup_failures: cleanupFailures, termination_reason: signal.reason?.code ?? null,
    evidence_kind: realModels ? "synthetic_development_pilot" : "no_model_protocol_dry_run",
    recipe_sha256: recipeHash, source_revision: sourceRevision,
    coordinator_invocations: ledger.ordinal, usage: usageSummary(events, realModels),
    elapsed_seconds: (Date.now() - started) / 1000,
    storage: { before: storageBefore, after: storageAfter,
      measurement: "owned_database_and_allocated_memory_relations", per_fact_cost_claimed: false },
    human_rescues: 0, production_qualified: false, outcomes,
    arms: Object.fromEntries(ARMS.map((arm) => [arm, {
      slots: 6, task_successes: outcomes.filter((outcome) => outcome.arm === arm
        && outcome.task_success).length,
      unrun: outcomes.filter((outcome) => outcome.arm === arm && outcome.status === "unrun").length,
      unknown_slots: outcomes.filter((outcome) => outcome.arm === arm && outcome.outcome_unknown).length,
    }])),
  };
  await writeNew(path.join(directory, "summary.json"), summary);
  journal.close();
  return summary;
}

export function parseArguments(argv) {
  const allowed = new Set([
    "--directory", "--pack", "--runtime-image", "--execution-image", "--model",
    "--reasoning-effort", "--scripted-responses", "--qualification",
  ]);
  const fields = new Map();
  let live = false;
  let qualify = false;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--allow-copilot") { requireCondition(!live, "duplicate_argument"); live = true; }
    else if (argv[i] === "--qualify") { requireCondition(!qualify, "duplicate_argument"); qualify = true; }
    else {
      requireCondition(allowed.has(argv[i]) && !fields.has(argv[i]) && argv[i + 1]
        && !argv[i + 1].startsWith("--"), "invalid_arguments");
      fields.set(argv[i], argv[++i]);
    }
  }
  for (const name of ["--directory", "--pack", "--execution-image"]) {
    requireCondition(fields.has(name), "missing_argument");
  }
  requireCondition(!(live && qualify) && !(live && fields.has("--scripted-responses")),
    "conflicting_evaluation_modes");
  if (!qualify) {
    for (const name of ["--runtime-image", "--model", "--reasoning-effort"]) {
      requireCondition(fields.has(name), "missing_argument");
    }
    requireCondition(live ? fields.has("--qualification") : fields.has("--scripted-responses"),
      "explicit_transport_or_qualification_required");
    requireCondition(/^[a-z0-9][a-z0-9._-]{0,99}$/.test(fields.get("--model"))
      && ["low", "medium", "high", "xhigh"].includes(fields.get("--reasoning-effort")),
    "invalid_model_selection");
  }
  return { fields, live, qualify };
}

export async function frozenImage(image, sourceRevision, runner = runProcess) {
  const inspected = await runner("container", ["image", "inspect", image]);
  requireCondition(inspected.exitCode === 0 && !inspected.timedOut && !inspected.overflow,
    "evaluation_image_inspection_failed");
  const values = parseJson(inspected.stdout);
  requireCondition(Array.isArray(values) && values.length === 1, "evaluation_image_identity");
  const value = values[0];
  const digest = value.configuration?.descriptor?.digest;
  requireCondition(/^sha256:[0-9a-f]{64}$/.test(digest) && value.variants?.length === 1
    && value.variants[0].config?.config?.Labels?.["io.pg-agmemory.evaluation.source"]
      === sourceRevision, "evaluation_image_source_mismatch");
  const reference = `${image.split("@")[0].replace(/:[^/:]+$/, "")}@${digest}`;
  let pinned = await runner("container", ["image", "inspect", reference]);
  if (pinned.exitCode !== 0) {
    requireCondition(!pinned.timedOut && !pinned.overflow
      && pinned.stderr.toString().startsWith("Error: image not found: "),
    "evaluation_image_alias_inspection_failed");
    const registered = await runner("container", ["image", "tag", image, reference]);
    requireCondition(registered.exitCode === 0 && !registered.timedOut && !registered.overflow,
      "evaluation_image_alias_failed");
    pinned = await runner("container", ["image", "inspect", reference]);
  }
  requireCondition(pinned.exitCode === 0 && !pinned.timedOut && !pinned.overflow,
    "evaluation_image_alias_failed");
  const resolved = parseJson(pinned.stdout);
  requireCondition(Array.isArray(resolved) && resolved.length === 1
    && resolved[0].configuration?.descriptor?.digest === digest, "evaluation_image_alias_mismatch");
  return reference;
}

export function validateQualification(qualification, pack, sourceRevision, executionImage) {
  requireCondition(qualification.status === "qualified" && qualification.real_models === false
    && qualification.source_revision === sourceRevision
    && qualification.pack_sha256 === sha256(canonicalJson(pack))
    && qualification.execution_image === executionImage && Array.isArray(qualification.matrix),
  "qualified_frozen_pack_required");
  const expected = [];
  const add = (project, milestone, candidate, sample, files, status) => expected.push({
    project_id: project.project_id, milestone: milestone.number, candidate, case_id: sample.case_id,
    status, artifact_sha256: artifactFromFiles(files, milestone.allowed_paths).tree_sha256,
  });
  for (const project of pack.projects) {
    for (const milestone of project.milestones) {
      for (const sample of milestone.cases) {
        add(project, milestone, "reference", sample, milestone.reference_files, "passed");
        if (sample.tags.includes("milestone-work")) {
          add(project, milestone, "canonical_start", sample, milestone.files, "failed");
        }
      }
      for (const control of milestone.negative_controls) {
        for (const id of control.fails_cases) add(project, milestone, control.control_id,
          milestone.cases.find((sample) => sample.case_id === id), control.files, "failed");
      }
    }
  }
  requireCondition(qualification.matrix.length === expected.length, "qualification_matrix_incomplete");
  const actual = qualification.matrix.map((row) => {
    requireCondition(row.guest_started === true && row.candidate_started === true
      && typeof row.stdout_sha256 === "string" && /^[0-9a-f]{64}$/.test(row.stdout_sha256)
      && typeof row.stderr_sha256 === "string" && /^[0-9a-f]{64}$/.test(row.stderr_sha256)
      && (row.status === "passed" ? row.exit_code === 0 && row.reason === null
        : typeof row.reason === "string"), "qualification_execution_unverified");
    return Object.fromEntries(Object.keys(expected[0]).map((key) => [key, row[key]]));
  });
  requireCondition(canonicalJson(actual.map(canonicalJson).sort())
    === canonicalJson(expected.map(canonicalJson).sort()), "qualification_matrix_mismatch");
}

async function main(argv) {
  if (argv.length === 1 && ["--help", "-h"].includes(argv[0])) {
    console.log("Usage: node scripts/evaluate-development-memory.mjs --directory NEW_PRIVATE_DIR "
      + "--pack PRIVATE_PACK --execution-image BAKED_IMAGE [--qualify | --runtime-image BAKED_IMAGE "
      + "--model MODEL --reasoning-effort EFFORT "
      + "(--scripted-responses PRIVATE_JSON | --allow-copilot --qualification PRIVATE_RESULT)]");
    return;
  }
  const { fields, live, qualify } = parseArguments(argv);
  requireCondition(await fs.realpath(process.cwd())
    === await fs.realpath(fileURLToPath(new URL("../", import.meta.url))),
  "evaluation_repository_cwd_required");
  requireCondition(!["PGAG_DATABASE_URL", "PGAG_ADMIN_DATABASE_URL", "PGHOST", "PGSERVICE"]
    .some((key) => process.env[key]), "external_database_target_forbidden");
  const source = await runProcess("git", ["rev-parse", "HEAD"]);
  const status = await runProcess("git", ["status", "--porcelain", "--untracked-files=normal"]);
  requireCondition(source.exitCode === 0 && status.exitCode === 0 && status.stdout.length === 0,
    "frozen_committed_checkout_required");
  const sourceRevision = source.stdout.toString().trim();
  const directory = path.resolve(fields.get("--directory"));
  await privateDirectory(directory, { create: true });
  requireCondition((await fs.readdir(directory)).length === 0, "new_run_directory_required");
  const pack = validatePack(parseJson(await readPrivate(path.resolve(fields.get("--pack")), 16777216),
    { maximum: 16777216, integersOnly: true }));
  const executionImage = await frozenImage(fields.get("--execution-image"), sourceRevision);
  if (qualify) {
    const journal = new Journal(path.join(directory, "qualification-events.jsonl"));
    const cancellation = new AbortController();
    const stop = () => cancellation.abort();
    process.on("SIGINT", stop);
    process.on("SIGTERM", stop);
    let count = 0;
    try {
      const result = await qualifyPack(pack, {
        record: (event) => journal.record(event),
        grade: async (files, milestone, sample) => {
          requireCondition(!cancellation.signal.aborted, "operation_cancelled");
          const guest = new ExecutionGuest({
            name: `pgag-dev-qualify-${randomBytes(12).toString("hex")}-${++count}`,
            image: executionImage, record: (event) => journal.record(event),
          });
          try {
            await guest.start(files, { signal: cancellation.signal });
            const result = await guest.grade(milestone.entry_point, canonicalJson(sample.input),
              canonicalJson(sample.expected), { signal: cancellation.signal });
            requireCondition(!cancellation.signal.aborted, "operation_cancelled");
            return result;
          } finally { await guest.close(); }
        },
      });
      await writeNew(path.join(directory, "qualification.json"), {
        ...result, source_revision: sourceRevision, execution_image: executionImage,
        real_models: false,
      });
    } finally {
      journal.close();
      process.removeListener("SIGINT", stop);
      process.removeListener("SIGTERM", stop);
    }
    return;
  }
  if (live) {
    const qualification = parseJson(await readPrivate(path.resolve(fields.get("--qualification"))));
    validateQualification(qualification, pack, sourceRevision, executionImage);
  }
  const runtimeImage = await frozenImage(fields.get("--runtime-image"), sourceRevision);
  const scripted = fields.has("--scripted-responses")
    ? parseJson(await readPrivate(path.resolve(fields.get("--scripted-responses")))) : null;
  requireCondition(scripted === null || Array.isArray(scripted), "scripted_responses_required");
  const cancellation = new AbortController();
  const stop = () => cancellation.abort();
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  let result;
  try {
    result = await runEvaluation({
      pack, directory, runId: `pgag-dev-${randomBytes(12).toString("hex")}`,
      runtimeImage, executionImage,
      model: fields.get("--model"), effort: fields.get("--reasoning-effort"),
      sourceRevision, scriptedResponses: scripted, signal: cancellation.signal, allowCopilot: live,
    });
  } finally {
    process.removeListener("SIGINT", stop);
    process.removeListener("SIGTERM", stop);
  }
  console.log(JSON.stringify({ status: result.status, real_models: result.real_models,
    arms: result.arms, failure: result.failure }));
  if (result.status !== "completed") process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error instanceof EvaluationError ? error.code : "development_evaluation_failed");
    process.exitCode = 1;
  });
}
