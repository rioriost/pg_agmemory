import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ControllerSession, verifyControllerAccounting } from "./development-eval-controller.mjs";
import { NativeInfrastructure } from "./development-eval-infrastructure.mjs";
import { artifactFromFiles, qualifyPack, schedule } from "./development-eval-pack.mjs";
import {
  ARMS, canonicalJson, EvaluationError, InvocationLedger, MAINTENANCE_PROTOCOL, RETRIEVAL_POLICY,
  RunBudget, sha256, WORK_PROTOCOL,
} from "./development-eval-protocol.mjs";
import { ExecutionGuest } from "./development-eval-sandbox.mjs";
import {
  frozenImage, gradeCase, parseArguments, runEvaluation, ScriptedTransport, validateQualification,
} from "./evaluate-development-memory.mjs";

function files(text) {
  const data = Buffer.from(text);
  return [{ path: "main.py", size: data.length, sha256: sha256(data),
    content_base64: data.toString("base64") }];
}

const SOLUTION = "import json,sys\nprint(json.dumps(json.load(sys.stdin)))\n";

function protocolPack() {
  return {
    format: "pgag-development-pack-v1",
    projects: ["toy-a", "toy-b"].map((project_id) => ({
      project_id, milestones: [1, 2, 3].map((number) => ({
        number, brief: "Protocol fixture only: preserve the integer value. Implement JSON echo.",
        files: files("print(0)\n"), allowed_paths: ["main.py"], entry_point: "main.py",
        reference_files: files(SOLUTION),
        cases: [{ case_id: "echo", input: { value: 3 }, expected: { value: 3 },
          tags: ["milestone-work"], history_origin: number === 1 ? null : {
            milestone: 1, visible_quote: "preserve the integer value",
          } }],
        negative_controls: [{ control_id: "wrong", files: files("print(0)\n"),
          fails_cases: ["echo"] }],
      })),
    })),
  };
}

test("evaluation entry point requires explicit mode, fixed argument names and model choice", () => {
  const base = ["--directory", "/private/new", "--pack", "/private/pack",
    "--execution-image", "execution"];
  assert.equal(parseArguments([...base, "--qualify"]).qualify, true);
  assert.throws(() => parseArguments([...base, "--qualify", "--allow-copilot"]),
    /conflicting_evaluation_modes/);
  const work = [...base, "--runtime-image", "runtime", "--model", "gpt-6-astra",
    "--reasoning-effort", "high"];
  assert.throws(() => parseArguments(work), /explicit_transport_or_qualification_required/);
  assert.equal(parseArguments([...work, "--scripted-responses", "/private/replies"]).live, false);
  assert.throws(() => parseArguments([...work, "--allow-copilot"]),
    /explicit_transport_or_qualification_required/);
  assert.equal(parseArguments([...work, "--allow-copilot", "--qualification", "/private/gate"]).live,
    true);
  assert.throws(() => parseArguments([...work, "--model", "other"]), /invalid_arguments/);
});

test("imported runner cannot start real model calls without affirmative authorization", async () => {
  await assert.rejects(runEvaluation({ pack: protocolPack() }), /explicit_live_permission_required/);
});

test("scripted transport labels synthetic usage and does not recycle an admission", async () => {
  const records = [];
  const record = (event) => records.push(event);
  const ledger = new InvocationLedger({
    record, runId: "dry-run", model: "gpt-6-astra", effort: "high",
  });
  const transport = new ScriptedTransport(ledger,
    [{ arm: "handoff", phase: "work", text: '{"final":"done"}' }], record);
  const context = { arm: "handoff", phase: "work", sessionId: "session", slotId: "slot",
    sequence: 1, prompt: "Synthetic prompt." };
  const response = await transport.invoke(context);
  assert.equal(response.receipt_ref.global_ordinal, 1);
  assert.equal(records.at(-1).real_model_call, false);
  await assert.rejects(transport.invoke(context), /scripted_response_mismatch/);
  assert.equal(ledger.ordinal, 1);
});

test("host verifies controller counts and exact receipts instead of trusting Submitted", () => {
  const receipt = { global_ordinal: 4, bridge_id: "bridge", bridge_call_id: "000003" };
  const operations = [
    { request: { operation: "invoke_model", body: { phase: "work" } },
      result: { status: "ok", receipt_ref: receipt, usage: {
        input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0,
        reasoning_tokens: null, api_requests: 1, premium_requests: 0, nano_aiu: null,
        api_duration_ms: 1, monetary_cost_verified: false,
      } } },
  ];
  const result = {
    memory_maintenance_protocol: MAINTENANCE_PROTOCOL,
    memory_retrieval_policy: RETRIEVAL_POLICY,
    work_protocol: WORK_PROTOCOL,
    mode: "work", status: "submitted", reason: null, outcome_unknown: false,
    upstream_exit_status: "Submitted", query_attempts: 1, admitted_invocations: 1,
    execute_attempts: 0, provider_api_requests: 1, invocation_receipts: [receipt],
  };
  assert.equal(verifyControllerAccounting(result, operations).complete, true);
  assert.equal(verifyControllerAccounting(result, operations).transport_healthy, true);
  assert.deepEqual(verifyControllerAccounting({ ...result, invocation_receipts: [] }, operations)
    .mismatches, ["invocation_receipts"]);
  operations[0].result.usage = null;
  assert.deepEqual(verifyControllerAccounting(result, operations).mismatches, ["provider_api_requests"]);
  assert.equal(verifyControllerAccounting(result, operations).transport_healthy, false);
});

test("image gate binds the committed source label and resolves a digest, not a moving tag", async () => {
  const digest = `sha256:${"a".repeat(64)}`;
  const runner = async () => ({ exitCode: 0, stdout: Buffer.from(JSON.stringify([{
    configuration: { descriptor: { digest } },
    variants: [{ config: { config: { Labels: { "io.pg-agmemory.evaluation.source": "commit" } } } }],
  }])) });
  assert.equal(await frozenImage("image:tag", "commit", runner), `image@${digest}`);
  await assert.rejects(frozenImage("image:tag", "other-commit", runner),
    /evaluation_image_source_mismatch/);
});

test("a qualification declaration cannot replace a complete bound execution matrix", async () => {
  const pack = protocolPack();
  const matrix = await qualifyPack(pack, {
    record: () => {},
    grade: async (files, milestone) => {
      const reference = Buffer.from(files[0].content_base64, "base64").toString() === SOLUTION;
      return {
        status: reference ? "passed" : "failed", reason: reference ? null : "mismatch",
        artifact_sha256: artifactFromFiles(files, milestone.allowed_paths).tree_sha256,
        guest_started: true, candidate_started: true, exit_code: 0,
        stdout_sha256: "a".repeat(64), stderr_sha256: "b".repeat(64),
      };
    },
  });
  const qualification = { ...matrix, source_revision: "commit", execution_image: "image@digest",
    real_models: false };
  validateQualification(qualification, pack, "commit", "image@digest");
  assert.throws(() => validateQualification({ ...qualification, matrix: [] }, pack, "commit",
    "image@digest"), /qualification_matrix_incomplete/);
  const damaged = structuredClone(qualification);
  damaged.matrix[0].candidate_started = false;
  assert.throws(() => validateQualification(damaged, pack, "commit", "image@digest"),
    /qualification_execution_unverified/);
  assert.throws(() => validateQualification(qualification, pack, "commit", "other-image"),
    /qualified_frozen_pack_required/);
});

test("pre-start cancellation preserves all eighteen unrun slots without starting infrastructure", async () => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-dev-cancel-test-")));
  const cancellation = new AbortController();
  cancellation.abort();
  try {
    const result = await runEvaluation({
      pack: protocolPack(), directory: root, runId: `pgag-dev-${randomBytes(12).toString("hex")}`,
      runtimeImage: "unused", executionImage: "unused", model: "gpt-6-astra", effort: "high",
      sourceRevision: "unit-fixture", scriptedResponses: [], signal: cancellation.signal,
    });
    assert.equal(result.failure, "operation_cancelled");
    assert.equal(result.coordinator_invocations, 0);
    assert.equal(result.outcomes.length, 18);
    assert.ok(result.outcomes.every((slot) => slot.status === "unrun"));
    assert.equal(result.usage.real_model_invocations, 0);
    assert.equal(result.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
    assert.equal(result.memory_retrieval_policy, RETRIEVAL_POLICY);
    assert.equal(result.work_protocol, WORK_PROTOCOL);
    const recipe = JSON.parse(await fs.readFile(path.join(root, "recipe.json")));
    assert.equal(recipe.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
    assert.equal(recipe.memory_retrieval_policy, RETRIEVAL_POLICY);
    assert.equal(recipe.work_protocol, WORK_PROTOCOL);
    assert.equal(sha256(canonicalJson(recipe)), result.recipe_sha256);
    const legacy = { ...recipe };
    delete legacy.memory_maintenance_protocol;
    assert.notEqual(sha256(canonicalJson(legacy)), result.recipe_sha256);
    const previousRetrieval = { ...recipe };
    delete previousRetrieval.memory_retrieval_policy;
    assert.notEqual(sha256(canonicalJson(previousRetrieval)), result.recipe_sha256);
    const previousWork = { ...recipe };
    delete previousWork.work_protocol;
    assert.notEqual(sha256(canonicalJson(previousWork)), result.recipe_sha256);
  } finally { await fs.rm(root, { recursive: true }); }
});

test("active deadline aborts without waiting for the next admission check", (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"] });
  const budget = new RunBudget(100);
  t.mock.timers.tick(100);
  assert.equal(budget.signal.aborted, true);
  assert.throws(() => budget.assertActive(), /run_deadline/);
  budget.close();
});

test("a final grading result returned after expiry cannot turn the run into success", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"] });
  const budget = new RunBudget(100);
  const guest = {
    async start() {},
    async grade() { t.mock.timers.tick(100); return { status: "passed" }; },
  };
  const milestone = protocolPack().projects[0].milestones[0];
  try {
    await assert.rejects(gradeCase(guest, milestone, milestone.cases[0],
      artifactFromFiles(milestone.reference_files, milestone.allowed_paths), budget), (error) => {
      assert.equal(error.code, "run_deadline");
      assert.equal(error.grade.status, "infrastructure_unknown");
      return true;
    });
  } finally { budget.close(); }
});

test("grader infrastructure failure preserves submitted work and every case in the summary", async (t) => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-dev-grade-fault-")));
  const pack = protocolPack();
  const milestone = pack.projects[0].milestones[0];
  milestone.cases.push({ ...milestone.cases[0], case_id: "later" });
  const artifact = artifactFromFiles(milestone.reference_files, milestone.allowed_paths);
  let controllers = 0;
  t.mock.method(NativeInfrastructure.prototype, "start", async () => {});
  t.mock.method(NativeInfrastructure.prototype, "storageStatus", async () => ({ database_bytes: 0,
    relations: [] }));
  t.mock.method(NativeInfrastructure.prototype, "binding", () => ({ scope_id: "unit-scope" }));
  t.mock.method(NativeInfrastructure.prototype, "close", async () => {});
  t.mock.method(ControllerSession.prototype, "run", async function () {
    controllers += 1;
    return { status: "submitted", reason: null, transcript: null };
  });
  t.mock.method(ExecutionGuest.prototype, "start", async function () {
    if (this.name.includes("-grade-")) throw new EvaluationError("guest_start_failed");
  });
  t.mock.method(ExecutionGuest.prototype, "export", async function () {
    this.closed = true;
    return artifact;
  });
  t.mock.method(ExecutionGuest.prototype, "close", async () => {});
  try {
    const result = await runEvaluation({
      pack, directory: root, runId: `pgag-dev-${randomBytes(12).toString("hex")}`,
      runtimeImage: "unit", executionImage: "unit", model: "gpt-6-astra", effort: "high",
      sourceRevision: "unit-fixture", scriptedResponses: [],
    });
    assert.equal(result.status, "failed");
    assert.equal(result.failure, "guest_start_failed");
    assert.equal(controllers, 1);
    const first = result.outcomes[0];
    assert.equal(first.work_result.status, "submitted");
    assert.equal(first.status, "submitted");
    assert.equal(first.outcome_unknown, true);
    assert.equal(first.task_success, null);
    assert.deepEqual(first.checks.map((check) => check.status),
      ["infrastructure_unknown", "unavailable"]);
    assert.ok(result.outcomes.slice(1).every((slot) => slot.status === "unrun"));
  } finally { await fs.rm(root, { recursive: true }); }
});

test("no-model full run executes eighteen isolated sessions and separate memory boundaries", {
  skip: !process.env.PGAG_DEVELOPMENT_RUNTIME_IMAGE || !process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
  timeout: 600000,
}, async () => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-dev-full-test-")));
  const pack = protocolPack();
  const responses = [];
  for (const slot of schedule(pack)) {
    responses.push({ arm: slot.arm, phase: "work",
      text: JSON.stringify({ command: `cat > main.py <<'PY'\n${SOLUTION}PY` }) });
    responses.push({ arm: slot.arm, phase: "work", text: '{"final":"Protocol fixture submitted."}' });
    if (slot.milestone < 3 && slot.arm !== "no_memory") {
      responses.push({ arm: slot.arm, phase: slot.arm === "handoff" ? "handoff" : "memory_decision",
        text: slot.arm === "handoff" ? '{"items":["Preserve the integer value."]}'
          : '{"create":[],"revise":[],"propose_forget":[]}' });
    }
  }
  const summary = await runEvaluation({
    pack, directory: root, runId: `pgag-dev-${randomBytes(12).toString("hex")}`,
    runtimeImage: process.env.PGAG_DEVELOPMENT_RUNTIME_IMAGE,
    executionImage: process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
    model: "gpt-6-astra", effort: "high", sourceRevision: "protocol-self-test-uncommitted",
    scriptedResponses: responses,
  });
  assert.equal(summary.status, "completed", JSON.stringify({ root, summary }));
  assert.equal(summary.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
  assert.equal(summary.memory_retrieval_policy, RETRIEVAL_POLICY);
  assert.equal(summary.work_protocol, WORK_PROTOCOL);
  assert.equal(summary.real_models, false);
  assert.equal(summary.usage.real_model_invocations, 0);
  assert.equal(summary.usage.provider_usage, null);
  assert.equal(summary.coordinator_invocations, 44);
  assert.equal(summary.outcomes.length, 18);
  assert.equal(summary.outcomes.filter((outcome) => outcome.boundary_result !== null).length, 8);
  for (const arm of ARMS) {
    assert.deepEqual(summary.arms[arm], { slots: 6, task_successes: 6, unrun: 0, unknown_slots: 0 });
  }
  for (const outcome of summary.outcomes) {
    assert.equal(outcome.work_result.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
    assert.equal(outcome.work_result.memory_retrieval_policy, RETRIEVAL_POLICY);
    assert.equal(outcome.work_result.work_protocol, WORK_PROTOCOL);
    assert.equal(outcome.work_result.host_accounting.complete, true);
    assert.notEqual(outcome.artifact_sha256, outcome.starting_tree_sha256);
    if (outcome.arm === "handoff" && outcome.milestone > 1) {
      assert.match(outcome.work_result.memory_delivery.text, /Preserve the integer value/);
    }
    if (outcome.boundary_result) {
      assert.equal(outcome.boundary_result.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
      assert.equal(outcome.boundary_result.memory_retrieval_policy, RETRIEVAL_POLICY);
      assert.equal(outcome.boundary_result.work_protocol, WORK_PROTOCOL);
      assert.equal(outcome.boundary_result.memory_state.format, "development-memory-state-v2");
      assert.equal(Object.hasOwn(outcome.boundary_result.memory_state, "memory_retrieval_policy"), false);
      assert.equal(Object.hasOwn(outcome.boundary_result.memory_state, "work_protocol"), false);
    }
    if (outcome.arm === "no_memory") assert.equal(outcome.work_result.memory_delivery.byte_count, 0);
  }
  const recipe = JSON.parse(await fs.readFile(path.join(root, "recipe.json")));
  assert.equal(recipe.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
  assert.equal(recipe.memory_retrieval_policy, RETRIEVAL_POLICY);
  assert.equal(recipe.work_protocol, WORK_PROTOCOL);
  assert.equal(sha256(canonicalJson(recipe)), summary.recipe_sha256);
  assert.equal(JSON.parse(await fs.readFile(path.join(root, "summary.json"))).status, "completed");
  await fs.rm(root, { recursive: true });
});
