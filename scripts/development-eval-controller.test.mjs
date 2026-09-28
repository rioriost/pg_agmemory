import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ControllerSession, verifyControllerAccounting } from "./development-eval-controller.mjs";
import { NativeInfrastructure } from "./development-eval-infrastructure.mjs";
import { artifactFromFiles } from "./development-eval-pack.mjs";
import {
  EvaluationError, InvocationLedger, MAINTENANCE_PROTOCOL, PROTOCOL, sha256,
} from "./development-eval-protocol.mjs";
import { ExecutionGuest } from "./development-eval-sandbox.mjs";

const usage = {
  input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0,
  reasoning_tokens: null, api_requests: 1, premium_requests: 0, nano_aiu: null,
  api_duration_ms: 1, monetary_cost_verified: false,
};

const fractionalNanoUsage = {
  input_tokens: 4380, output_tokens: 2100, cache_read_tokens: 0, cache_write_tokens: 4377,
  reasoning_tokens: 442, api_requests: 1, premium_requests: 1, nano_aiu: 15974250000.000002,
  api_duration_ms: 30857, monetary_cost_verified: false,
};

const reference = (ordinal) => ({
  global_ordinal: ordinal, bridge_id: "test-run-no_memory",
  bridge_call_id: String(ordinal).padStart(6, "0"),
});
const request = (sequence, operation = "invoke_model") => ({
  protocol: PROTOCOL, session_id: "unit-session", sequence, operation,
  body: operation === "execute" ? { command: "synthetic command" }
    : { phase: "work", prompt: "Synthetic protocol input.", planning_round: null },
});
const modelOperation = (sequence, text = '{"final":"done"}', ordinal = sequence) => ({
  request: request(sequence), reply_published: true,
  result: { status: "ok", text, receipt_ref: reference(ordinal), usage: { ...usage },
    model: "gpt-6-astra", reasoning_effort: "high", duration_ns: 1 },
});
const executeOperation = (sequence) => ({
  request: request(sequence, "execute"), reply_published: true,
  result: { status: "ok", exit_code: 0, output_base64: "", captured_bytes: 0,
    output_truncated: false, duration_ns: 1 },
});
function controllerResult(operations, overrides = {}) {
  const models = operations.filter((row) => row.request.operation === "invoke_model");
  const refs = models.map((row) => row.result.receipt_ref).filter((ref) => ref !== null);
  return {
    protocol: PROTOCOL, session_id: "unit-session", mode: "work",
    memory_maintenance_protocol: MAINTENANCE_PROTOCOL, memory_state: null,
    slot: { project_id: "toy-a", milestone: 1, arm: "no_memory" },
    status: "submitted", reason: null, outcome_unknown: false, upstream_exit_status: "Submitted",
    query_attempts: models.filter((row) => row.request.body.phase === "work").length,
    execute_attempts: operations.length - models.length, admitted_invocations: refs.length,
    invocation_receipts: refs, provider_api_requests: refs.length, ...overrides,
  };
}
function repliesFor(operations) {
  return new Map(operations.map(({ request: req, result }) => [req.sequence, {
    protocol: PROTOCOL, session_id: req.session_id, sequence: req.sequence,
    operation: req.operation, result,
  }]));
}

test("unit: maintenance protocol preflight is required for every mode and initial arm", () => {
  assert.equal(PROTOCOL, "pgag-development-controller-v1");
  assert.equal(MAINTENANCE_PROTOCOL, "development-maintenance-v2");
  for (const mode of ["work", "boundary"]) for (const arm of ["no_memory", "handoff", "pg_agmemory"]) {
    for (const invalid of [undefined, null, false, 2, "", "development-maintenance-v1"]) {
      let callbacks = 0;
      const ledger = new InvocationLedger({
        record: () => { callbacks += 1; }, runId: "test-run", model: "gpt-6-astra", effort: "high",
      });
      const config = {
        protocol: PROTOCOL, mode, session_id: "unit-session",
        slot: { project_id: "toy-a", milestone: 1, arm }, memory_state: null,
        maintenance_protocol: MAINTENANCE_PROTOCOL,
      };
      if (invalid !== undefined) config.memory_maintenance_protocol = invalid;
      assert.throws(() => new ControllerSession({
        config, transport: { ledger, invoke: () => { callbacks += 1; } },
        infrastructure: { bearer: () => { callbacks += 1; } }, guest: null,
        record: () => { callbacks += 1; }, runner: () => { callbacks += 1; },
        runDeadline: Date.now() + 60000,
      }), { code: "invalid_memory_maintenance_protocol" });
      assert.equal(callbacks, 0);
      assert.equal(ledger.ordinal, 0);
      assert.equal(ledger.stopped, true);
    }
  }
});

test("unit: old or missing state versions cannot enter a v2 host controller", () => {
  for (const mode of ["work", "boundary"]) for (const arm of ["no_memory", "handoff", "pg_agmemory"]) {
    for (const state of [undefined, {}, { format: "development-memory-state-v1" }]) {
      const ledger = new InvocationLedger({
        record: () => assert.fail("no admission"), runId: "test-run", model: "gpt-6-astra", effort: "high",
      });
      assert.throws(() => new ControllerSession({
        config: { protocol: PROTOCOL, memory_maintenance_protocol: MAINTENANCE_PROTOCOL,
          mode, slot: { project_id: "toy-a", milestone: 1, arm }, memory_state: state },
        transport: { ledger }, infrastructure: {}, guest: null,
        record: () => assert.fail("no event"), runner: () => assert.fail("no controller operation"),
        runDeadline: Date.now() + 60000,
      }), { code: "invalid_memory_state" });
      assert.equal(ledger.ordinal, 0);
      assert.equal(ledger.stopped, true);
    }
  }
});

test("unit: failed accounting allows only one explained unpublished attempt", () => {
  const operations = [modelOperation(1, '{"command":"synthetic command"}'), executeOperation(2)];
  const failed = controllerResult(operations, {
    status: "failed", reason: "prompt_budget_exhausted", upstream_exit_status: "EvaluationFailure",
    query_attempts: 2,
  });
  const options = { workStarted: true, replies: repliesFor(operations) };
  const audit = verifyControllerAccounting(failed, operations, options);
  assert.equal(audit.complete, true);
  assert.equal(audit.local_unpublished_query_attempts, 1);
  for (const change of [
    { query_attempts: 3 }, { query_attempts: 0 }, { reason: "invalid_action" },
    { execute_attempts: 2 }, { outcome_unknown: true }, { status: "submitted" },
    { upstream_exit_status: "Submitted" }, { query_attempts: true },
  ]) {
    assert.equal(verifyControllerAccounting({ ...failed, ...change }, operations, options).complete,
      false, JSON.stringify(change));
  }
  assert.equal(verifyControllerAccounting(failed, operations, {
    ...options, workStarted: false,
  }).complete, false);
  const beforeExecute = operations.slice(0, 1);
  const deadline = controllerResult(beforeExecute, {
    status: "failed", reason: "session_deadline", upstream_exit_status: "EvaluationFailure",
    execute_attempts: 1,
  });
  assert.equal(verifyControllerAccounting(deadline, beforeExecute, {
    workStarted: true, replies: repliesFor(beforeExecute),
  }).local_unpublished_execute_attempts, 1);
  assert.equal(verifyControllerAccounting({ ...deadline, reason: "invalid_command" },
    beforeExecute, { workStarted: true }).complete, false);
});

test("unit: failed accounting never waives dropped reordered substituted or duplicated receipts", () => {
  const operations = [
    modelOperation(1, '{"command":"synthetic command"}'), executeOperation(2), modelOperation(3),
  ];
  const result = controllerResult(operations, {
    status: "failed", reason: "invalid_action", upstream_exit_status: "RepeatedFormatError",
  });
  const good = result.invocation_receipts;
  for (const receipts of [
    good.slice(0, 1), [...good].reverse(), [good[0], reference(2)], [good[0], good[0]],
  ]) {
    const audit = verifyControllerAccounting({
      ...result, invocation_receipts: receipts, admitted_invocations: receipts.length,
    }, operations, { replies: repliesFor(operations) });
    assert.equal(audit.complete, false);
    assert.equal(audit.incomplete, false);
    assert.ok(audit.mismatches.includes("invocation_receipts"));
  }
});

test("unit: known provider totals and transport health are independent", () => {
  for (const apiRequests of [0, 2]) {
    const operation = modelOperation(1);
    operation.result.usage.api_requests = apiRequests;
    const result = controllerResult([operation], {
      status: "failed", reason: "failed_transport_accounting", outcome_unknown: true,
      provider_api_requests: apiRequests,
    });
    const audit = verifyControllerAccounting(result, [operation]);
    assert.equal(audit.complete, true);
    assert.equal(audit.host_expected.provider_api_requests, apiRequests);
    assert.equal(audit.transport_healthy, false);
  }
  for (const outcome of ["not_started", "known_failure", "unknown"]) {
    const operation = {
      request: request(1), result: {
        status: "error", code: "model_transport_failed", outcome,
        receipt_ref: null, usage: null, duration_ns: null,
      },
    };
    const expected = outcome === "not_started" ? 0 : null;
    const audit = verifyControllerAccounting(controllerResult([operation], {
      status: "failed", reason: "model_transport_failed", provider_api_requests: expected,
    }), [operation]);
    assert.equal(audit.complete, true);
    assert.equal(audit.host_expected.provider_api_requests, expected);
    assert.equal(audit.transport_healthy, outcome === "not_started");
  }
  const malformed = modelOperation(1);
  malformed.result.usage.nano_aiu = "0.5";
  assert.equal(verifyControllerAccounting(controllerResult([malformed], {
    status: "failed", provider_api_requests: null,
  }), [malformed]).transport_healthy, false);
});

test("unit: financial usage accepts nullable finite fractions without changing the reported value",
  () => {
    for (const nano of [null, 0, 15974250000, 0.5, 15974250000.000002]) {
      const operation = modelOperation(1);
      operation.result.usage = { ...fractionalNanoUsage, nano_aiu: nano };
      const original = JSON.stringify(operation.result.usage);
      const result = controllerResult([operation]);
      const audit = verifyControllerAccounting(result, [operation]);
      assert.equal(audit.complete, true);
      assert.equal(audit.transport_healthy, true);
      assert.equal(audit.host_expected.provider_api_requests, 1);
      assert.equal(JSON.stringify(operation.result.usage), original);
      assert.equal(JSON.parse(original).nano_aiu, nano);
    }
    for (const nano of ["0.5", true, false, -0.5, NaN, Infinity, -Infinity]) {
      const operation = modelOperation(1);
      operation.result.usage = { ...fractionalNanoUsage, nano_aiu: nano };
      const audit = verifyControllerAccounting(controllerResult([operation], {
        status: "failed", provider_api_requests: null,
      }), [operation]);
      assert.equal(audit.transport_healthy, false);
    }
  });

test("unit: fractional financial metadata does not relax API and token count guards", () => {
  for (const field of [
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "api_requests",
  ]) {
    for (const value of [1.5, true, "1"]) {
      const operation = modelOperation(1);
      operation.result.usage = { ...fractionalNanoUsage, [field]: value };
      const audit = verifyControllerAccounting(controllerResult([operation], {
        status: "failed", provider_api_requests: null,
      }), [operation]);
      assert.equal(audit.transport_healthy, false, field);
    }
  }
});

test("unit: incomplete accounting requires an evidenced unacknowledged uncertainty tail", () => {
  const operations = [modelOperation(1)];
  const result = controllerResult(operations, {
    status: "failed", reason: "response_timeout", outcome_unknown: true,
    admitted_invocations: 0, invocation_receipts: [], provider_api_requests: null,
  });
  const incomplete = verifyControllerAccounting(result, operations, { replies: new Map() });
  assert.equal(incomplete.complete, false);
  assert.equal(incomplete.incomplete, true);
  for (const [changedResult, changedOperations, replies] of [
    [result, operations, repliesFor(operations)],
    [{ ...result, reason: "invalid_action" }, operations, new Map()],
    [result, [{ ...operations[0], reply_published: false }], new Map()],
    [{ ...result, invocation_receipts: [reference(2)], admitted_invocations: 1 },
      operations, new Map()],
  ]) {
    assert.equal(verifyControllerAccounting(changedResult, changedOperations, { replies }).incomplete,
      false);
  }
});

async function unitSession(t, options = {}) {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-controller-unit-")));
  t.after(() => fs.rm(root, { recursive: true }));
  await fs.mkdir(path.join(root, "controllers"), { mode: 0o700 });
  const records = [];
  const cleanup = [];
  const ledger = new InvocationLedger({
    record: (event) => records.push(event), runId: "test-run", model: "gpt-6-astra", effort: "high",
  });
  const infrastructure = {
    directory: root, api: "unit-api", owned: new Set(["unit-api"]),
    bearer: () => "synthetic.identity.signature",
    async remove(name, { deadline }) {
      cleanup.push({ operation: "native_api", deadline });
      if (options.removeError) throw options.removeError;
      this.owned.delete(name);
    },
  };
  const guest = {
    closed: false,
    async execute() {
      if (options.executeError) {
        this.closed = true;
        throw options.executeError;
      }
      return { stdout: Buffer.from("ok\n"), stderr: Buffer.alloc(0),
        exitCode: 0, durationSeconds: 0.001 };
    },
    async close({ deadline }) {
      cleanup.push({ operation: "guest", deadline });
      this.closed = true;
    },
  };
  let calls = 0;
  const transport = {
    ledger,
    async invoke(context) {
      calls += 1;
      if (options.beforeAdmissionError) throw options.beforeAdmissionError;
      const admitted = ledger.reserve(context);
      if (options.modelError) {
        options.modelError.receipt_ref = admitted.receipt;
        options.modelError.usage = { ...usage };
        throw options.modelError;
      }
      return {
        text: options.modelText ?? (options.requests?.length > 1 && context.sequence === 1
          ? '{"command":"synthetic command"}' : '{"final":"done"}'),
        receipt_ref: admitted.receipt, usage: { ...usage, ...options.usage },
        duration_seconds: options.duration ?? 0.001,
      };
    },
    async stop(arm, { deadline, cancel }) {
      cleanup.push({ operation: "bridge", deadline, cancel });
      if (options.hungStop) return new Promise(() => {});
    },
  };
  const slot = options.config?.slot ?? { project_id: "toy-a", milestone: 1, arm: "no_memory" };
  const binding = {
    run_id: "test-run", project_id: slot.project_id, arm: slot.arm,
    scope_id: "00000000-0000-4000-8000-000000000001",
  };
  const state = slot.arm !== "no_memory" && slot.milestone > 1 ? {
    format: "development-memory-state-v2", binding, completed_boundaries: slot.milestone - 1,
    last_boundary_id: "prior-boundary", note: slot.arm === "handoff" ? "Historical fact." : null,
    assertions: slot.arm === "pg_agmemory"
      ? [{ memory_id: "00000000-0000-4000-8000-000000000002", revision: 1, status: "active" }] : [],
  } : null;
  const config = {
    protocol: PROTOCOL, run_id: "test-run", session_id: "unit-session", mode: "work",
    memory_maintenance_protocol: MAINTENANCE_PROTOCOL, memory_state: state, memory_binding: binding,
    slot,
    model: { model: "gpt-6-astra", reasoning_effort: "high" },
    ...options.config,
  };
  let resolveProcess;
  let launches = 0;
  let eventSequence = 0;
  let session;
  const published = [];
  async function event(kind, data) {
    await fs.appendFile(path.join(session.root, "output", "events.jsonl"), JSON.stringify({
      protocol: PROTOCOL, session_id: config.session_id, sequence: ++eventSequence,
      kind, at: new Date().toISOString(), elapsed_ns: eventSequence, data,
    }) + "\n", { mode: 0o600 });
  }
  async function requestFile(value) {
    const name = `${String(value.sequence).padStart(6, "0")}.json`;
    const staging = path.join(session.root, "staging", `request-${name}`);
    await fs.writeFile(staging, JSON.stringify(value), { mode: 0o600, flag: "wx" });
    await fs.rename(staging, path.join(session.root, "ipc", "requests", name));
  }
  async function finish() {
    const operations = published.map((reply) => ({
      request: options.requests?.find((req) => req.sequence === reply.sequence)
        ?? request(reply.sequence), result: reply.result,
    }));
    let result = controllerResult(operations, {
      mode: config.mode, slot: config.slot, memory_state: config.memory_state,
    });
    const last = operations.at(-1);
    if (last?.result.status === "error") {
      result = { ...result, status: "failed", reason: last.result.code,
        upstream_exit_status: "EvaluationFailure" };
    }
    if (options.result) result = options.result(result);
    if (options.unanswered) await requestFile(request(published.length + 1));
    if (options.failureEvent) await event("controller_failed", options.failureEvent);
    else await event("work_finished", { status: result.status, reason: result.reason });
    await fs.writeFile(path.join(session.root, "output", "result.json"), JSON.stringify(result),
      { mode: 0o600, flag: "wx" });
    resolveProcess({
      exitCode: result.status === "failed" ? 1 : 0, durationSeconds: 0.001,
      stdout: Buffer.alloc(0), stderr: Buffer.alloc(0),
      timedOut: false, overflow: false, cancelled: false,
    });
  }
  const runner = async (executable, args, { signal }) => {
    launches += 1;
    assert.equal(executable, "container");
    assert.equal(args[0], "exec");
    const completed = new Promise((resolve) => { resolveProcess = resolve; });
    signal.addEventListener("abort", () => resolveProcess({
      exitCode: 1, timedOut: false, overflow: false, cancelled: true,
      stdout: Buffer.alloc(0), stderr: Buffer.alloc(0), durationSeconds: 0,
    }), { once: true });
    await fs.mkdir(path.join(session.root, "ipc", "requests"), { mode: 0o700 });
    await fs.mkdir(path.join(session.root, "ipc", "replies"), { mode: 0o700 });
    if (!options.omitStarted) await event("controller_started", {
      memory_maintenance_protocol: config.memory_maintenance_protocol,
      ...options.started,
    });
    if (!options.retrieval) await event("work_started", { work_limit_seconds: 900 });
    if (options.noRequests) await finish();
    else await requestFile(options.requests?.[0] ?? request(1));
    return completed;
  };
  session = new ControllerSession({
    infrastructure, transport, guest, config, runner, signal: options.signal,
    record: (row) => {
      records.push(row);
      if (row.kind === "controller_operation_failed" && row.fatal) {
        assert.equal(ledger.stopped, true);
        assert.equal(session.abort.signal.aborted, true);
      }
      if (options.recordError && row.kind === "controller_aborted") throw options.recordError;
    },
    runDeadline: Date.now() + 60000,
  });
  session.publish = async (reply) => {
    if (options.publishError) throw options.publishError;
    published.push(reply);
    if (!options.omitAcknowledgement) await event("ipc_reply", { reply });
    const next = options.requests?.[published.length];
    if (next) await requestFile(next);
    else await finish();
  };
  return {
    session, transport, infrastructure, guest, config, runner, records, cleanup, published,
    calls: () => calls, launches: () => launches,
  };
}

test("unit: changed configuration is fatal before any controller or model launch", async (t) => {
  for (const change of [
    { memory_maintenance_protocol: "development-maintenance-v1" },
    { memory_state: { format: "development-memory-state-v1" } },
  ]) {
    const fixture = await unitSession(t);
    Object.assign(fixture.config, change);
    await assert.rejects(fixture.session.run(), {
      code: change.memory_state ? "invalid_memory_state" : "invalid_memory_maintenance_protocol",
    });
    assert.equal(fixture.launches(), 0);
    assert.equal(fixture.calls(), 0);
    assert.equal(fixture.transport.ledger.stopped, true);
  }
});

test("unit: controller startup must bind the maintenance identity before dispatch", async (t) => {
  for (const options of [
    { omitStarted: true },
    { started: { memory_maintenance_protocol: undefined } },
    { started: { memory_maintenance_protocol: "development-maintenance-v1" } },
  ]) {
    const fixture = await unitSession(t, options);
    await assert.rejects(fixture.session.run(), { code: "controller_maintenance_protocol" });
    assert.equal(fixture.calls(), 0);
    assert.equal(fixture.published.length, 0);
    assert.equal(fixture.transport.ledger.stopped, true);
  }
});

test("unit: result identity cannot drop the version or reintroduce v1 state", async (t) => {
  for (const change of [
    { memory_maintenance_protocol: undefined },
    { memory_maintenance_protocol: "development-maintenance-v1" },
    { memory_state: { format: "development-memory-state-v1" } },
    { boundary_result: { state: { format: "development-memory-state-v1" } } },
  ]) {
    const fixture = await unitSession(t, { result: (value) => ({ ...value, ...change }) });
    await assert.rejects(fixture.session.run(), {
      code: change.memory_state || change.boundary_result
        ? "invalid_memory_state" : "invalid_memory_maintenance_protocol",
    });
    assert.equal(fixture.calls(), 1);
    assert.equal(fixture.transport.ledger.stopped, true);
    assert.equal(fixture.session.operations[0].result.receipt_ref.global_ordinal, 1);
    assert.equal(fixture.records.some((row) => row.kind === "controller_result"), false);
  }
});

test("unit: maintenance and state integrity errors never become ordinary boundary failures", async (t) => {
  for (const reason of ["invalid_memory_maintenance_protocol", "invalid_memory_state"]) {
    const fixture = await unitSession(t, {
      retrieval: true, noRequests: true,
      config: { mode: "boundary", slot: { project_id: "toy-a", milestone: 1, arm: "handoff" } },
      result: (value) => ({ ...value, status: "failed", reason }),
    });
    await assert.rejects(fixture.session.run(), {
      code: "controller_failure_requires_abort", controller_reason: reason,
    });
    assert.equal(fixture.transport.ledger.stopped, true);
    assert.equal(fixture.calls(), 0);
  }
});

test("unit: cleanup failure is latched before IPC and blocks a later boundary", async (t) => {
  const original = new EvaluationError("owned_cleanup_failed");
  original.cleanupDeadline = Date.now() + 5000;
  const secondary = new EvaluationError("infrastructure_inventory_failed");
  const fixture = await unitSession(t, {
    requests: [request(1), request(2, "execute")],
    executeError: original, removeError: secondary,
  });
  await assert.rejects(fixture.session.run(), (error) => error === original);
  assert.equal(fixture.guest.closed, true);
  assert.equal(fixture.transport.ledger.stopped, true);
  assert.equal(fixture.published.length, 1);
  assert.equal(fixture.calls(), 1);
  assert.equal(fixture.session.operations[0].result.receipt_ref.global_ordinal, 1);
  assert.ok(fixture.cleanup.every((row) => row.deadline === original.cleanupDeadline));
  assert.ok(original.cleanup_failures.some((row) => row.code === secondary.code));
  const aborted = fixture.records.find((row) => row.kind === "controller_aborted");
  assert.equal(aborted.code, "owned_cleanup_failed");
  const next = new ControllerSession({
    infrastructure: fixture.infrastructure, transport: fixture.transport, guest: null,
    config: { ...fixture.config, mode: "boundary", session_id: "unit-boundary" },
    record: () => {}, runner: fixture.runner, runDeadline: Date.now() + 60000,
  });
  await assert.rejects(next.run(), { code: "admission_stopped" });
  assert.equal(fixture.launches(), 1);
  assert.equal(fixture.calls(), 1);
});

test("unit: fatal identity failure preserves receipt and original code through cleanup errors",
  async (t) => {
    const original = new EvaluationError("model_response_identity_mismatch");
    const fixture = await unitSession(t, {
      modelError: original, removeError: new EvaluationError("owned_cleanup_failed"),
      recordError: new EvaluationError("journal_failed"),
    });
    await assert.rejects(fixture.session.run(), (error) => error === original);
    assert.equal(original.receipt_ref.global_ordinal, 1);
    assert.equal(original.usage.api_requests, 1);
    assert.equal(fixture.published.length, 0);
    assert.equal(fixture.transport.ledger.ordinal, 1);
    assert.ok(original.cleanup_failures.some((row) => row.code === "owned_cleanup_failed"));
    assert.ok(original.cleanup_failures.some((row) => row.code === "journal_failed"));
  });

test("unit: trusted cleanup failure flag makes an ordinary model error fatal without losing evidence",
  async (t) => {
    for (const details of [[], [{ code: "bridge_termination_unacknowledged" }]]) {
      const original = new EvaluationError("model_response_failed");
      original.cleanup_failed = true;
      original.cleanup_failures = details;
      original.cleanupDeadline = Date.now() + 5000;
      const fixture = await unitSession(t, { modelError: original });
      await assert.rejects(fixture.session.run(), (error) => error === original);
      assert.equal(original.code, "model_response_failed");
      assert.equal(original.cleanup_failed, true);
      assert.deepEqual(original.cleanup_failures, details);
      assert.equal(original.receipt_ref.global_ordinal, 1);
      assert.equal(original.usage.api_requests, 1);
      assert.equal(fixture.published.length, 0);
      assert.equal(fixture.transport.ledger.stopped, true);
      assert.ok(fixture.cleanup.every((row) => row.deadline === original.cleanupDeadline));
      const next = new ControllerSession({
        infrastructure: fixture.infrastructure, transport: fixture.transport, guest: null,
        config: { ...fixture.config, mode: "boundary", session_id: "unit-boundary" },
        record: () => {}, runner: fixture.runner, runDeadline: Date.now() + 60000,
      });
      await assert.rejects(next.run(), { code: "admission_stopped" });
      assert.equal(fixture.launches(), 1);
      assert.equal(fixture.calls(), 1);
    }
  });

test("unit: shared cleanup deadline bounds a noncooperating teardown without replacing fatal code",
  async (t) => {
    const original = new EvaluationError("controller_protocol_corruption");
    const fixture = await unitSession(t, { modelError: original, hungStop: true });
    original.cleanupDeadline = Date.now() + 100;
    await assert.rejects(fixture.session.run(), (error) => error === original);
    assert.ok(fixture.cleanup.every((row) => row.deadline === original.cleanupDeadline));
    assert.ok(original.cleanup_failures.some((row) =>
      row.operation === "bridge" && row.code === "cleanup_deadline"));
  });

test("unit: an already exhausted helper cleanup grace is never restarted", async (t) => {
  const original = new EvaluationError("owned_cleanup_failed");
  original.cleanupDeadline = Date.now() - 1;
  const fixture = await unitSession(t, {
    requests: [request(1), request(2, "execute")], executeError: original,
  });
  const deadline = original.cleanupDeadline;
  await assert.rejects(fixture.session.run(), (error) => error === original);
  assert.equal(fixture.guest.closed, true);
  assert.equal(original.cleanupDeadline, deadline);
  assert.ok(fixture.cleanup.every((row) => row.deadline === deadline));
  assert.equal(fixture.records.find((row) => row.kind === "controller_aborted")
    .cancellation_grace_exceeded, true);
  assert.equal(fixture.transport.ledger.stopped, true);
});

test("unit: acknowledged shell timeout remains an ordinary failed work session", async (t) => {
  const fixture = await unitSession(t, {
    requests: [request(1), request(2, "execute")],
    executeError: new EvaluationError("command_timeout"),
  });
  const result = await fixture.session.run();
  assert.equal(result.status, "failed");
  assert.equal(result.reason, "command_timeout");
  assert.equal(result.host_accounting.complete, true);
  assert.equal(fixture.transport.ledger.stopped, false);
  assert.equal(fixture.published.length, 2);
  assert.equal(fixture.cleanup.length, 0);
});

test("unit: local pre-IPC query exhaustion remains accounted without a host model request",
  async (t) => {
    const fixture = await unitSession(t, {
      noRequests: true,
      result: (value) => ({ ...value, status: "failed", reason: "prompt_budget_exhausted",
        upstream_exit_status: "EvaluationFailure", query_attempts: 1 }),
    });
    const result = await fixture.session.run();
    assert.equal(result.host_accounting.local_unpublished_query_attempts, 1);
    assert.equal(fixture.calls(), 0);
    assert.equal(fixture.transport.ledger.stopped, false);
  });

test("unit: host prompt rejection counts a published request without a provider admission",
  async (t) => {
    const fixture = await unitSession(t, {
      beforeAdmissionError: new EvaluationError("bridge_request_limit"),
    });
    const result = await fixture.session.run();
    assert.equal(result.status, "failed");
    assert.equal(result.query_attempts, 1);
    assert.equal(result.admitted_invocations, 0);
    assert.equal(result.provider_api_requests, 0);
    assert.equal(result.host_accounting.local_unpublished_query_attempts, 0);
    assert.equal(fixture.transport.ledger.ordinal, 0);
    assert.equal(fixture.transport.ledger.stopped, false);
  });

test("unit: accounted model format failure is not mistaken for controller corruption", async (t) => {
  const fixture = await unitSession(t, {
    modelText: "not action JSON",
    result: (value) => ({ ...value, status: "failed", reason: "invalid_action",
      upstream_exit_status: "RepeatedFormatError" }),
  });
  const result = await fixture.session.run();
  assert.equal(result.status, "failed");
  assert.equal(result.host_accounting.complete, true);
  assert.equal(fixture.transport.ledger.ordinal, 1);
  assert.equal(fixture.transport.ledger.stopped, false);
});

test("unit: reported fractional nano AIU passes future dispatch and reply publication unchanged",
  async (t) => {
    const snapshot = JSON.stringify(fractionalNanoUsage);
    const fixture = await unitSession(t, { usage: fractionalNanoUsage });
    const result = await fixture.session.run();
    assert.equal(result.status, "submitted");
    assert.equal(result.host_accounting.complete, true);
    assert.equal(result.provider_api_requests, 1);
    assert.equal(fixture.transport.ledger.stopped, false);
    assert.equal(fixture.calls(), 1);
    assert.deepEqual(fixture.published[0].result.usage, fractionalNanoUsage);
    const encoded = JSON.stringify(fixture.published[0]);
    assert.ok(encoded.includes('"nano_aiu":15974250000.000002'));
    assert.equal(JSON.parse(encoded).result.usage.nano_aiu, fractionalNanoUsage.nano_aiu);
    assert.equal(JSON.stringify(fractionalNanoUsage), snapshot);
  });

test("unit: acknowledged model response failure retains its known admission without poisoning ledger",
  async (t) => {
    const fixture = await unitSession(t, { modelError: new EvaluationError("model_response_failed") });
    const result = await fixture.session.run();
    assert.equal(result.status, "failed");
    assert.equal(result.reason, "model_response_invalid");
    assert.equal(result.admitted_invocations, 1);
    assert.equal(result.provider_api_requests, 1);
    assert.equal(fixture.transport.ledger.stopped, false);
  });

test("unit: invalid admitted usage and duration retain receipts and stop before reply publication",
  async (t) => {
    for (const options of [{ usage: { api_requests: 2 } }, { duration: -1 }]) {
      const fixture = await unitSession(t, options);
      await assert.rejects(fixture.session.run(), (error) => {
        assert.equal(error.code, options.usage ? "failed_transport_accounting" : "invalid_duration");
        assert.equal(error.receipt_ref.global_ordinal, 1);
        assert.equal(error.usage.api_requests, options.usage ? 2 : 1);
        return true;
      });
      assert.equal(fixture.transport.ledger.stopped, true);
      assert.equal(fixture.published.length, 0);
    }
  });

test("unit: publication failure retains the host receipt even without controller acknowledgement",
  async (t) => {
    const original = new EvaluationError("controller_reply_publication_failed");
    const fixture = await unitSession(t, { publishError: original });
    await assert.rejects(fixture.session.run(), (error) => error === original);
    assert.equal(fixture.session.operations[0].reply_published, false);
    assert.equal(fixture.transport.ledger.ordinal, 1);
    assert.deepEqual(fixture.records.find((row) => row.kind === "controller_aborted")
      .host_invocation_receipts, [reference(1)]);
  });

test("unit: outer cancellation preserves an existing shared grace without launching a controller",
  async (t) => {
    const abort = new AbortController();
    const original = new EvaluationError("run_deadline");
    original.cleanupDeadline = Date.now() + 5000;
    abort.abort(original);
    const fixture = await unitSession(t, { signal: abort.signal });
    await assert.rejects(fixture.session.run(), (error) => error === original);
    assert.equal(fixture.launches(), 0);
    assert.equal(fixture.calls(), 0);
    assert.ok(fixture.cleanup.every((row) => row.deadline === original.cleanupDeadline));
  });

test("unit: a failed result cannot disguise a cleanup or controller integrity error", async (t) => {
  const fixture = await unitSession(t, {
    result: (value) => ({ ...value, status: "failed", reason: "cleanup_failed" }),
  });
  await assert.rejects(fixture.session.run(), {
    code: "controller_failure_requires_abort", controller_reason: "cleanup_failed",
  });
  assert.equal(fixture.transport.ledger.stopped, true);
});

async function retrievalFailureSession(t, code, options = {}) {
  const plan = request(1);
  plan.body.phase = "memory_plan";
  plan.body.planning_round = 1;
  return unitSession(t, {
    retrieval: true,
    config: { slot: { project_id: "toy-a", milestone: 2, arm: "pg_agmemory" } },
    requests: [plan], modelText: code === "search_prompt_too_large"
      ? '{"queries":[{"terms":["evidence"]}]}' : '{"queries":[]}',
    failureEvent: {
      code, outcome_unknown: false, exception_type: "BoundedRecallError",
      origin: "pg_agmemory.bounded_recall.BoundedRecallError", phase: "memory_delivery",
      ...options.failureEvent,
    },
    result: (value) => ({ ...value, status: "failed", reason: code, upstream_exit_status: null }),
    ...Object.fromEntries(Object.entries(options).filter(([key]) => key !== "failureEvent")),
  });
}

test("unit: typed invalid planner output is an accounted failed retrieval, not coordinator corruption",
  async (t) => {
    const fixture = await retrievalFailureSession(t, "invalid_search_plan");
    const result = await fixture.session.run();
    assert.equal(result.status, "failed");
    assert.equal(result.reason, "invalid_search_plan");
    assert.equal(result.query_attempts, 0);
    assert.equal(result.execute_attempts, 0);
    assert.equal(result.admitted_invocations, 1);
    assert.equal(result.provider_api_requests, 1);
    assert.equal(result.host_accounting.complete, true);
    assert.equal(fixture.transport.ledger.stopped, false);
    assert.equal(fixture.calls(), 1);
  });

test("unit: typed retrieval prompt exhaustion preserves zero or earlier planning admissions",
  async (t) => {
    for (const noRequests of [true, false]) {
      const fixture = await retrievalFailureSession(t, "search_prompt_too_large", { noRequests });
      const result = await fixture.session.run();
      assert.equal(result.status, "failed");
      assert.equal(result.reason, "search_prompt_too_large");
      assert.equal(result.admitted_invocations, noRequests ? 0 : 1);
      assert.equal(result.provider_api_requests, noRequests ? 0 : 1);
      assert.equal(result.host_accounting.local_unpublished_query_attempts, 0);
      assert.equal(fixture.transport.ledger.stopped, false);
    }
  });

for (const invalid of [
  "no_successful_plan", "unsuccessful_plan", "wrong_origin", "wrong_type", "wrong_phase", "wrong_code",
  "generic_exception", "ambiguous_helper", "other_model_phase",
]) {
  test(`unit: retrieval failure ${invalid} does not widen the typed planner whitelist`, async (t) => {
    const code = invalid === "generic_exception" ? "controller_exception"
      : invalid === "ambiguous_helper" ? "invalid_planning_schedule" : "invalid_search_plan";
    const options = {};
    if (invalid === "no_successful_plan") options.noRequests = true;
    if (invalid === "unsuccessful_plan") {
      options.modelError = new EvaluationError("model_response_failed");
    }
    if (invalid === "wrong_origin") options.failureEvent = { origin: "another.helper" };
    if (invalid === "wrong_type") options.failureEvent = { exception_type: "ValueError" };
    if (invalid === "wrong_phase") options.failureEvent = { phase: "memory_boundary" };
    if (invalid === "wrong_code") options.failureEvent = { code: "search_prompt_too_large" };
    if (invalid === "other_model_phase") {
      const handoff = request(1);
      handoff.body.phase = "handoff";
      options.requests = [handoff];
      options.config = {
        mode: "boundary", slot: { project_id: "toy-a", milestone: 1, arm: "handoff" },
      };
    }
    const fixture = await retrievalFailureSession(t, code, options);
    await assert.rejects(fixture.session.run(), {
      code: "controller_failure_requires_abort", controller_reason: code,
    });
    assert.equal(fixture.transport.ledger.stopped, true);
  });
}

for (const mode of ["dropped", "substituted", "duplicated", "unanswered", "uncertain_tail"]) {
  test(`unit: failed controller ${mode} evidence cannot authorize a boundary`, async (t) => {
    const fixture = await unitSession(t, {
      omitAcknowledgement: mode === "uncertain_tail", unanswered: mode === "unanswered",
      result: (value) => {
        const result = { ...value, status: "failed", reason: "invalid_action",
          upstream_exit_status: "RepeatedFormatError" };
        if (mode === "dropped" || mode === "uncertain_tail") {
          result.invocation_receipts = [];
          result.admitted_invocations = 0;
        }
        if (mode === "substituted") result.invocation_receipts = [reference(2)];
        if (mode === "duplicated") {
          result.invocation_receipts = [reference(1), reference(1)];
          result.admitted_invocations = 2;
        }
        if (mode === "uncertain_tail") {
          result.reason = "response_timeout";
          result.outcome_unknown = true;
          result.provider_api_requests = null;
        }
        return result;
      },
    });
    await assert.rejects(fixture.session.run(), {
      code: mode === "unanswered" ? "controller_unanswered_request"
        : mode === "uncertain_tail" ? "controller_accounting_incomplete"
          : "controller_accounting_mismatch",
    });
    assert.equal(fixture.transport.ledger.stopped, true);
    assert.equal(fixture.transport.ledger.ordinal, 1);
    assert.equal(fixture.calls(), 1);
  });
}

test("real DefaultAgent and separate boundary controllers use host-owned single-link IPC", {
  skip: !process.env.PGAG_DEVELOPMENT_RUNTIME_IMAGE || !process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
  timeout: 180000,
}, async () => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-dev-controller-test-")));
  const runId = `pgag-dev-${randomBytes(12).toString("hex")}`;
  const records = [];
  const record = (event) => records.push(event);
  const infrastructure = new NativeInfrastructure({
    runId, directory: path.join(root, "runtime"),
    runtimeImage: process.env.PGAG_DEVELOPMENT_RUNTIME_IMAGE, record,
  });
  const ledger = new InvocationLedger({ record, runId, model: "gpt-6-astra", effort: "high" });
  const nanoAiu = 15974250000.000002;
  const handoffItems = [
    "Preserve the integer value. ".repeat(34),
    "Keep output as JSON. ".repeat(43),
    "Omitted lower-ranked fact. ".repeat(36),
  ];
  const handoffReply = JSON.stringify({ items: handoffItems });
  const proposedHandoff = handoffItems.join("\n\n");
  const packedHandoff = handoffItems.slice(0, 2).join("\n\n");
  assert.ok(handoffItems.every((item) => Buffer.byteLength(item) <= 2048));
  assert.ok(Buffer.byteLength(packedHandoff) <= 2048 && Buffer.byteLength(proposedHandoff) > 2048);
  const transport = {
    ledger,
    async invoke(context) {
      const admission = ledger.reserve(context);
      const text = context.phase === "handoff" ? handoffReply
        : context.phase === "memory_decision" ? '{"create":[],"revise":[],"propose_forget":[]}'
          : '{"final":"Synthetic protocol submission."}';
      return {
        text, receipt_ref: admission.receipt,
        usage: context.phase === "work" ? { ...usage, nano_aiu: nanoAiu } : usage,
        duration_seconds: 0.001,
      };
    },
  };
  const model = { model: "gpt-6-astra", reasoning_effort: "high" };
  const raw = Buffer.from("print(0)\n");
  const files = [{ path: "main.py", size: raw.length, sha256: sha256(raw),
    content_base64: raw.toString("base64") }];
  const seed = artifactFromFiles(files, ["main.py"]);
  const guests = [];
  try {
    await infrastructure.start(["toy-a", "toy-b"]);
    for (const arm of ["no_memory", "handoff", "pg_agmemory"]) {
      const provisioned = infrastructure.binding("toy-a", arm);
      const binding = { run_id: runId, project_id: "toy-a", arm, scope_id: provisioned.scope_id };
      const common = {
        protocol: PROTOCOL, run_id: runId,
        memory_maintenance_protocol: MAINTENANCE_PROTOCOL,
        slot: { project_id: "toy-a", milestone: 1, arm }, recipe_sha256: "a".repeat(64),
        model, memory_binding: binding, memory_state: null,
      };
      const sessionId = `session-${arm.replaceAll("_", "-")}`;
      const guest = new ExecutionGuest({
        name: `${runId}-${arm.replaceAll("_", "-")}`, image: process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
        record,
      });
      guests.push(guest);
      await guest.start(files);
      const work = await new ControllerSession({
        infrastructure, transport, guest, record, runDeadline: Date.now() + 120000,
        config: {
          ...common, mode: "work", session_id: sessionId, brief: "Protocol self-test only.",
          starting_tree_sha256: seed.tree_sha256, allowed_output_paths: ["main.py"],
          entry_point: "main.py",
        },
      }).run();
      assert.equal(work.status, "submitted", JSON.stringify({ arm, work,
        recent_events: records.slice(-5) }));
      assert.equal(work.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
      assert.equal(records.find((row) => row.kind === "controller_event"
        && row.event.session_id === sessionId && row.event.kind === "controller_started")
        .event.data.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
      assert.equal(work.query_attempts, 1);
      assert.equal(work.admitted_invocations, 1);
      assert.equal(work.provider_api_requests, 1);
      assert.equal(work.host_accounting.complete, true);
      assert.equal(work.host_accounting.transport_healthy, true);
      const replyBytes = await fs.readFile(path.join(
        infrastructure.directory, "controllers", sessionId, "ipc", "replies", "000001.json",
      ), "utf8");
      assert.ok(replyBytes.includes('"nano_aiu":15974250000.000002'));
      assert.equal(JSON.parse(replyBytes).result.usage.nano_aiu, nanoAiu);
      const consumed = records.filter((row) => row.kind === "controller_event"
        && row.event.session_id === sessionId && row.event.kind === "ipc_reply");
      assert.equal(consumed.length, 1);
      assert.equal(consumed[0].event.data.reply.result.usage.nano_aiu, nanoAiu);
      const captured = await guest.export(["main.py"]);
      assert.equal(captured.tree_sha256, seed.tree_sha256);
      if (arm === "no_memory") continue;
      const boundary = await new ControllerSession({
        infrastructure, transport, guest: null, record, runDeadline: Date.now() + 120000,
        config: {
          ...common, mode: "boundary", session_id: `${sessionId}-boundary`,
          transcript: work.transcript, boundary_id: `${sessionId}-boundary`,
          keys: arm === "handoff" ? null : {
            observe: `${sessionId}-observe`,
            create: Array.from({ length: 6 }, (_, i) => `${sessionId}-create-${i}`),
            revise: Array.from({ length: 4 }, (_, i) => `${sessionId}-revise-${i}`),
          },
        },
      }).run();
      assert.equal(boundary.status, "boundary_completed", JSON.stringify({ arm, boundary,
        recent_events: records.slice(-8) }));
      assert.equal(boundary.memory_state.completed_boundaries, 1);
      assert.equal(boundary.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
      assert.equal(boundary.memory_state.format, "development-memory-state-v2");
      assert.equal(boundary.memory_state.note,
        arm === "handoff" ? packedHandoff : null);
      if (arm === "handoff") {
        assert.ok(!JSON.stringify(boundary.memory_state).includes(handoffItems[2]));
        const packing = records.find((row) => row.kind === "controller_event"
          && row.event.session_id === `${sessionId}-boundary` && row.event.kind === "memory_event"
          && row.event.data.kind === "memory_handoff_packing").event.data.data;
        assert.equal(packing.memory_maintenance_protocol, MAINTENANCE_PROTOCOL);
        assert.deepEqual(packing.receipt_ref, boundary.invocation_receipts[0]);
        assert.equal(packing.proposed_count, 3);
        assert.equal(packing.included_count, 2);
        assert.equal(packing.omitted_count, 1);
        assert.deepEqual(packing.included_indices, [0, 1]);
        assert.deepEqual(packing.omitted_indices, [2]);
        assert.equal(packing.raw_reply_bytes, Buffer.byteLength(handoffReply));
        assert.equal(packing.proposed_bytes, Buffer.byteLength(proposedHandoff));
        assert.equal(packing.delivered_bytes, Buffer.byteLength(packedHandoff));
        assert.equal(packing.raw_reply_sha256, sha256(handoffReply));
        assert.equal(packing.proposed_sha256, sha256(proposedHandoff));
        assert.equal(packing.delivered_sha256, sha256(packedHandoff));
      }
      assert.equal(records.filter((row) => row.kind === "model_admitted"
        && row.session_id === `${sessionId}-boundary`).length, 1);
    }
    assert.equal(ledger.ordinal, 5);
  } finally {
    for (const guest of guests) await guest.close();
    await infrastructure.close();
    await fs.rm(root, { recursive: true });
  }
});
