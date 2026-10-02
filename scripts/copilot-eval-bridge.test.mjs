import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { EventEmitter } from "node:events";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import test from "node:test";
import {
  auditEvents, invokeCopilot, main, observeCopilotProcess, parseArguments, safeCopilotErrorCode,
  usageSummary, validateRequest, writeNew,
} from "./copilot-eval-bridge.mjs";
import { MAINTENANCE_TIMING_PROFILE } from "./development-eval-timing.mjs";

const model = "gpt-6-astra";
function events(changes = {}) {
  return [
    { type: "session.usage_checkpoint", data: { promptCacheBreakState: [
      { models: { [model]: { model, tool_count: 0, reasoning_effort: "high", ...changes } } },
    ] } },
    { type: "assistant.message", data: { content: '{"answer":"synthetic"}' } },
    { type: "result", exitCode: 0 },
  ].map((value) => JSON.stringify(value)).join("\n");
}

function measuredUsage() {
  return { currentModel: model, totalApiDurationMs: 10, modelMetrics: {
    [model]: { requests: { count: 1, cost: 1 }, totalNanoAiu: 123.25, usage: {
      inputTokens: 20, outputTokens: 4, cacheReadTokens: 3, cacheWriteTokens: 17,
    } },
  } };
}

class Clock {
  time = 0;
  serial = 0;
  pending = new Map();
  callbacks = [];
  setTimeout(callback, delay) {
    const id = ++this.serial;
    this.callbacks.push(callback);
    this.pending.set(id, { at: this.time + delay, callback });
    return id;
  }
  clearTimeout(id) { this.pending.delete(id); }
  tick(duration) {
    const until = this.time + duration;
    while (true) {
      const entry = [...this.pending.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!entry || entry[1].at > until) break;
      this.time = entry[1].at;
      this.pending.delete(entry[0]);
      entry[1].callback();
    }
    this.time = until;
  }
}

class FakeChild extends EventEmitter {
  pid = 12345;
  stdout = new EventEmitter();
  signals = [];
  unreferenced = false;
  destroyed = false;
  constructor() {
    super();
    this.stdout.setEncoding = encoding => assert.equal(encoding, "utf8");
    this.stdout.destroy = () => { this.destroyed = true; };
  }
  kill(signal) { this.signals.push(signal); return true; }
  unref() { this.unreferenced = true; }
  complete(code = 0, signal = null) {
    this.emit("exit", code, signal);
    this.emit("close", code, signal);
  }
}

function processHarness({
  child = new FakeChild(), clock = new Clock(), cancellation = new AbortController(), ...options
} = {}) {
  const failure = { code: null };
  const promise = observeCopilotProcess([], {}, {
    spawnProcess: () => child, timers: clock, now: () => clock.time,
    signal: cancellation.signal, failure, ...options,
  });
  return { child, clock, cancellation, failure, promise };
}

async function directoryFixture(t) {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-process-test-")));
  t.after(() => fs.rm(directory, { recursive: true }));
  return directory;
}

async function invocationFixture(t, {
  drive = child => child.complete(), output = events(), publishReceipt = writeNew, fileSystem = fs,
  cancellation = new AbortController(), timingProfile,
} = {}) {
  const directory = await directoryFixture(t), clock = new Clock(), diagnostics = [];
  const child = new FakeChild();
  const promise = invokeCopilot({
    directory, model, reasoning_effort: "high",
    ...(timingProfile === undefined ? {} : { timing_profile: timingProfile }),
  }, "000001", "Synthetic only.", {
    timers: clock, now: () => clock.time, signal: cancellation.signal, fileSystem, publishReceipt,
    diagnostic: code => diagnostics.push(code),
    spawnProcess: (_command, args) => {
      writeFileSync(args[args.indexOf("--usage-output-file") + 1], JSON.stringify(measuredUsage()), { mode: 0o600 });
      queueMicrotask(() => {
        child.emit("spawn");
        child.stdout.emit("data", output);
        drive(child, clock, cancellation);
      });
      return child;
    },
  });
  // Install a rejection handler before returning the fixture to its caller.
  const outcome = promise.then(value => ({ value, error: null }), error => ({ value: null, error }));
  return { directory, child, clock, diagnostics, outcome,
    receipt: () => fs.readFile(path.join(directory, "call-000001", "process.json"), "utf8").then(JSON.parse) };
}

test("safe errors accept only exact allowlisted strings, never object messages or coercion", () => {
  for (const value of [
    "invalid_bridge_request", "invalid_private_bridge_file", "bridge_file_too_large",
    "copilot_unsuccessful", "copilot_tool_use_forbidden", "copilot_model_or_tool_audit_failed",
    "copilot_response_invalid", "copilot_usage_invalid", "copilot_timeout", "copilot_output_limit",
    "copilot_interrupted", "copilot_transport_failed", "copilot_process_receipt_failed",
    "copilot_process_io_failed", "copilot_close_unacknowledged",
  ]) assert.equal(safeCopilotErrorCode(value), value);
  for (const value of [
    undefined, null, 0, {}, [], ["copilot_timeout"], new String("copilot_timeout"),
    new Error("copilot_timeout"), "copilot_timeout\n", "COPILOT_TIMEOUT", "token=synthetic-secret",
    { get message() { throw new Error("must not inspect"); }, toString() { throw new Error("must not coerce"); } },
  ]) assert.equal(safeCopilotErrorCode(value), null);
});

test("importing bridge helpers does not register signal handlers or invoke a CLI", async () => {
  const signals = ["SIGINT", "SIGTERM"];
  const before = signals.map(signal => process.listenerCount(signal));
  const imported = await import("./copilot-eval-bridge.mjs?offline-helper-import");
  assert.equal(imported.safeCopilotErrorCode("copilot_timeout"), "copilot_timeout");
  assert.deepEqual(signals.map(signal => process.listenerCount(signal)), before);
});

test("process success distinguishes spawn, OS exit and stdio close observations", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.child.stdout.emit("data", events());
  h.clock.tick(37);
  h.child.emit("exit", 0, null);
  h.clock.tick(12);
  h.child.emit("close", 0, null);
  const { observed, output } = await h.promise;
  assert.equal(h.failure.code, null);
  assert.equal(observed.spawn_attempted, true);
  assert.equal(observed.spawn_observed, true);
  assert.equal(observed.pid, 12345);
  assert.equal(observed.exit_observed, true);
  assert.equal(observed.close_observed, true);
  assert.equal(observed.exit_code, 0);
  assert.equal(observed.exit_signal, null);
  assert.equal(observed.close_code, 0);
  assert.equal(observed.elapsed_ms, 49);
  assert.equal(observed.stdout_bytes_retained, Buffer.byteLength(output));
  assert.deepEqual(observed.termination_requests, []);
  assert.equal(h.clock.pending.size, 0);
});

for (const [code, signal] of [[7, null], [null, "SIGTERM"]]) {
  test(`OS ${signal ?? code} overrides a successful stdout result without inventing zero`, async () => {
    const h = processHarness();
    h.child.emit("spawn");
    h.child.stdout.emit("data", events());
    h.child.complete(code, signal);
    const { observed } = await h.promise;
    assert.equal(h.failure.code, "copilot_unsuccessful");
    assert.equal(observed.exit_code, code);
    assert.equal(observed.exit_signal, signal);
    assert.equal(observed.close_code, code);
    assert.equal(observed.close_signal, signal);
    assert.deepEqual(h.child.signals, []);
  });
}

test("timeout remains primary even when the child subsequently exits zero", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.child.stdout.emit("data", events());
  h.clock.tick(149999);
  assert.equal(h.failure.code, null);
  h.clock.tick(1);
  assert.equal(h.failure.code, "copilot_timeout");
  assert.deepEqual(h.child.signals, ["SIGTERM"]);
  h.clock.tick(23);
  h.child.complete();
  const { observed } = await h.promise;
  assert.equal(observed.exit_code, 0);
  assert.equal(observed.close_observed, true);
  assert.deepEqual(observed.termination_requests, [
    { signal: "SIGTERM", elapsed_ms: 150000, kill_return: true, error: null },
  ]);
  assert.equal(observed.elapsed_ms, 150023);
  assert.equal(h.clock.pending.size, 0);
});

test("maintenance opt-in times out at 270000ms, not 269999ms, even with a subsequent zero exit", async () => {
  const h = processHarness({ timingProfile: MAINTENANCE_TIMING_PROFILE });
  h.child.emit("spawn");
  h.child.stdout.emit("data", events());
  h.clock.tick(269999);
  assert.equal(h.failure.code, null);
  assert.deepEqual(h.child.signals, []);
  h.clock.tick(1);
  assert.equal(h.failure.code, "copilot_timeout");
  assert.deepEqual(h.child.signals, ["SIGTERM"]);
  h.child.complete();
  const { observed } = await h.promise;
  assert.equal(observed.exit_code, 0);
  assert.equal(observed.close_observed, true);
  assert.equal(observed.elapsed_ms, 270000);
  assert.deepEqual(observed.termination_requests, [
    { signal: "SIGTERM", elapsed_ms: 270000, kill_return: true, error: null },
  ]);
  assert.equal(h.clock.pending.size, 0);
});

test("maintenance missing close stops at 274000ms with unchanged TERM/KILL graces", async () => {
  const h = processHarness({ timingProfile: MAINTENANCE_TIMING_PROFILE });
  h.child.emit("spawn");
  h.clock.tick(270000);
  h.clock.tick(1999);
  assert.deepEqual(h.child.signals, ["SIGTERM"]);
  h.cancellation.abort();
  h.clock.tick(1);
  assert.deepEqual(h.child.signals, ["SIGTERM", "SIGKILL"]);
  h.clock.tick(1999);
  assert.equal(h.child.unreferenced, false);
  h.clock.tick(1);
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_timeout");
  assert.equal(observed.elapsed_ms, 274000);
  assert.equal(observed.close_wait_expired, true);
  assert.equal(observed.close_observed, false);
  assert.equal(observed.exit_observed, false);
  assert.equal(observed.exit_code, null);
  assert.deepEqual(observed.termination_requests.map(x => [x.signal, x.elapsed_ms]),
    [["SIGTERM", 270000], ["SIGKILL", 272000]]);
  assert.equal(h.child.destroyed, true);
  assert.equal(h.child.unreferenced, true);
  assert.equal(h.clock.pending.size, 0);
});

test("maintenance host cancellation before 270s ends the child without a fresh model allowance", async () => {
  const h = processHarness({ timingProfile: MAINTENANCE_TIMING_PROFILE });
  h.child.emit("spawn");
  h.clock.tick(210000);
  h.cancellation.abort();
  h.clock.tick(4000);
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_interrupted");
  assert.equal(observed.elapsed_ms, 214000);
  assert.equal(observed.close_wait_expired, true);
  assert.deepEqual(observed.termination_requests.map(x => [x.signal, x.elapsed_ms]),
    [["SIGTERM", 210000], ["SIGKILL", 212000]]);
  assert.equal(h.clock.pending.size, 0);
});

for (const closeAt of [269999, 270000, 270001]) {
  test(`maintenance elapsed deadline rejects delayed timeout callbacks at ${closeAt}ms`, async () => {
    const h = processHarness({ timingProfile: MAINTENANCE_TIMING_PROFILE });
    h.child.emit("spawn");
    h.child.stdout.emit("data", events());
    h.clock.time = closeAt;
    h.child.complete();
    const { observed } = await h.promise;
    assert.equal(h.failure.code, closeAt < 270000 ? null : "copilot_timeout");
    assert.equal(observed.close_observed, true);
    assert.equal(observed.exit_code, 0);
    assert.deepEqual(h.child.signals, []);
    assert.equal(h.clock.pending.size, 0);
  });
}

test("omitted profile retains the historical close-before-timeout-callback behavior", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.clock.time = 150001;
  h.child.complete();
  await h.promise;
  assert.equal(h.failure.code, null);
  assert.equal(h.clock.pending.size, 0);
});

test("maintenance deadline starts after synchronous spawn returns, not at observation start", async () => {
  const clock = new Clock(), child = new FakeChild();
  const h = processHarness({ clock, child, timingProfile: MAINTENANCE_TIMING_PROFILE,
    spawnProcess() { clock.time = 40000; return child; },
  });
  child.emit("spawn");
  clock.time = 309999;
  child.complete();
  const { observed } = await h.promise;
  assert.equal(h.failure.code, null);
  assert.equal(observed.elapsed_ms, 309999);
  assert.equal(clock.pending.size, 0);
});

for (const timingProfile of [undefined, MAINTENANCE_TIMING_PROFILE]) {
  test(`cancellation during spawn delivers owned TERM once without resetting grace (${timingProfile ?? "legacy"})`, async () => {
    const clock = new Clock(), child = new FakeChild(), cancellation = new AbortController();
    const h = processHarness({ child, clock, cancellation, timingProfile,
      spawnProcess() {
        clock.time = 100;
        cancellation.abort();
        clock.time = 1500;
        return child;
      },
    });
    assert.deepEqual(child.signals, ["SIGTERM"]);
    child.emit("spawn");
    assert.deepEqual(child.signals, ["SIGTERM"]);
    clock.tick(599);
    assert.deepEqual(child.signals, ["SIGTERM"]);
    clock.tick(1);
    assert.deepEqual(child.signals, ["SIGTERM", "SIGKILL"]);
    clock.tick(2000);
    const { observed } = await h.promise;
    assert.equal(h.failure.code, "copilot_interrupted");
    assert.equal(observed.elapsed_ms, 4100);
    assert.deepEqual(observed.termination_requests.map(x => [x.signal, x.elapsed_ms]),
      [["SIGTERM", 1500], ["SIGKILL", 2100]]);
    assert.equal(observed.close_wait_expired, true);
    assert.equal(clock.pending.size, 0);
  });
}

test("maintenance cancellation during spawn waits for PID ownership but never duplicates TERM", async () => {
  const child = new FakeChild(), cancellation = new AbortController();
  child.pid = undefined;
  const h = processHarness({ child, cancellation, timingProfile: MAINTENANCE_TIMING_PROFILE,
    spawnProcess() { cancellation.abort(); return child; },
  });
  assert.deepEqual(child.signals, []);
  h.clock.tick(500);
  child.pid = 12345;
  child.emit("spawn");
  child.emit("spawn");
  assert.deepEqual(child.signals, ["SIGTERM"]);
  h.clock.tick(1500);
  assert.deepEqual(child.signals, ["SIGTERM", "SIGKILL"]);
  child.complete(null, "SIGKILL");
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_interrupted");
  assert.equal(observed.elapsed_ms, 2000);
  assert.equal(h.clock.pending.size, 0);
});

test("overflow retains at most the original 8MiB and does not restart termination", async () => {
  const h = processHarness(), limit = 8 * 1024 * 1024;
  h.child.emit("spawn");
  h.child.stdout.emit("data", "x".repeat(limit));
  assert.equal(h.failure.code, null);
  h.child.stdout.emit("data", "é");
  h.child.stdout.emit("data", "later discarded");
  h.cancellation.abort();
  h.clock.tick(1999);
  assert.deepEqual(h.child.signals, ["SIGTERM"]);
  h.clock.tick(1);
  assert.deepEqual(h.child.signals, ["SIGTERM", "SIGKILL"]);
  h.child.complete(null, "SIGKILL");
  const { observed, output } = await h.promise;
  assert.equal(h.failure.code, "copilot_output_limit");
  assert.equal(Buffer.byteLength(output), limit);
  assert.equal(observed.stdout_bytes_retained, limit);
  assert.equal(observed.stdout_bytes_observed, limit + 2 + 15);
  assert.equal(observed.termination_requests.length, 2);
});

test("outer interruption uses one unchanged TERM/KILL escalation and close wait", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.clock.tick(17);
  h.cancellation.abort();
  h.cancellation.abort();
  h.clock.tick(1999);
  assert.deepEqual(h.child.signals, ["SIGTERM"]);
  h.clock.tick(1);
  h.child.complete(null, "SIGKILL");
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_interrupted");
  assert.deepEqual(observed.termination_requests.map(x => [x.signal, x.elapsed_ms]),
    [["SIGTERM", 17], ["SIGKILL", 2017]]);
  assert.equal(h.clock.pending.size, 0);
});

test("timeout then outer interruption does not replace the cause or extend either timer", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.clock.tick(150000);
  h.clock.tick(1700);
  h.cancellation.abort();
  h.clock.tick(300);
  assert.deepEqual(h.child.signals, ["SIGTERM", "SIGKILL"]);
  h.clock.tick(2000);
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_timeout");
  assert.equal(observed.elapsed_ms, 154000);
  assert.equal(observed.close_wait_expired, true);
  assert.equal(observed.close_observed, false);
  assert.equal(observed.exit_observed, false);
  assert.equal(observed.exit_code, null);
  assert.equal(observed.exit_signal, null);
  assert.equal(h.child.destroyed, true);
  assert.equal(h.child.unreferenced, true);
  assert.equal(h.clock.pending.size, 0);
});

test("kill false and thrown errors are safe observations, not termination acknowledgements", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.child.kill = signal => {
    h.child.signals.push(signal);
    if (signal === "SIGKILL") throw Object.assign(new Error("/secret/path token=value"), { code: "PRIVATE_ERROR" });
    return false;
  };
  h.clock.tick(154000);
  const { observed } = await h.promise;
  assert.deepEqual(observed.termination_requests.map(x => [x.kill_return, x.error]),
    [[false, null], [null, "process_error"]]);
  assert.equal(observed.exit_observed, false);
  assert.equal(observed.close_observed, false);
  assert.doesNotMatch(JSON.stringify(observed), /secret|PRIVATE_ERROR|token/);
});

test("spawn error has attempted but not observed spawn and does not turn close into OS exit", async () => {
  const child = new FakeChild();
  child.pid = undefined;
  const h = processHarness({ child });
  h.child.emit("error", Object.assign(new Error("spawn /private/token ENOENT"), { code: "ENOENT" }));
  h.child.emit("close", -2, null);
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_transport_failed");
  assert.equal(observed.spawn_attempted, true);
  assert.equal(observed.spawn_observed, false);
  assert.equal(observed.pid, null);
  assert.equal(observed.process_error, "ENOENT");
  assert.equal(observed.process_error_after_spawn, false);
  assert.equal(observed.exit_observed, false);
  assert.equal(observed.exit_code, null);
  assert.equal(observed.close_observed, true);
  assert.equal(observed.close_code, -2);
  assert.doesNotMatch(JSON.stringify(observed), /private|token/);
});

test("synchronous spawn failure remains unknown rather than a fabricated exit", async () => {
  const h = processHarness({ spawnProcess() { throw new Error("arbitrary secret /path"); } });
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_transport_failed");
  assert.equal(observed.spawn_attempted, true);
  assert.equal(observed.spawn_observed, false);
  assert.equal(observed.pid, null);
  assert.equal(observed.process_error, "process_error");
  assert.equal(observed.close_observed, false);
  assert.equal(observed.exit_code, null);
  assert.equal(h.clock.pending.size, 0);
});

test("an error after spawn still awaits and records OS exit and stdio close", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.child.emit("error", Object.assign(new Error("kill /private/path failed"), { code: "EPERM" }));
  h.clock.tick(20);
  h.child.complete(4, null);
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_transport_failed");
  assert.equal(observed.spawn_observed, true);
  assert.equal(observed.process_error_after_spawn, true);
  assert.equal(observed.process_error, "EPERM");
  assert.equal(observed.exit_code, 4);
  assert.equal(observed.close_code, 4);
  assert.equal(observed.close_observed, true);
  assert.equal(observed.elapsed_ms, 20);
});

test("close without exit never invents successful OS termination", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.child.emit("close", 0, null);
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_unsuccessful");
  assert.equal(observed.exit_observed, false);
  assert.equal(observed.exit_code, null);
  assert.equal(observed.close_code, 0);
});

test("OS exit zero without stdio close is bounded and never treated as reusable success", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.child.emit("exit", 0, null);
  h.clock.tick(154000);
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_timeout");
  assert.equal(observed.exit_code, 0);
  assert.equal(observed.close_observed, false);
  assert.equal(observed.close_wait_expired, true);
  assert.deepEqual(h.child.signals, []);
});

test("late callbacks cannot mutate frozen observations, reset deadlines or kill the next child", async () => {
  for (const close of [true, false]) {
    const h = processHarness();
    h.child.emit("spawn");
    h.clock.tick(150000);
    if (close) h.child.complete();
    else h.clock.tick(4000);
    const result = await h.promise, snapshot = JSON.stringify(result);
    const next = processHarness();
    next.child.emit("spawn");
    for (const callback of h.clock.callbacks) callback();
    h.child.emit("error", new Error("late secret"));
    h.child.emit("spawn");
    h.child.complete(1, "SIGTERM");
    h.child.stdout.emit("data", "late output");
    h.child.stdout.emit("error", new Error("late stream error"));
    h.cancellation.abort();
    assert.equal(JSON.stringify(result), snapshot);
    assert.deepEqual(next.child.signals, []);
    assert.equal(next.failure.code, null);
    next.child.complete();
    await next.promise;
  }
});

test("already interrupted invocation does not attempt a spawn", async () => {
  const cancellation = new AbortController();
  cancellation.abort();
  const h = processHarness({ signal: cancellation.signal,
    spawnProcess() { assert.fail("must not spawn"); } });
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_interrupted");
  assert.equal(observed.spawn_attempted, false);
  assert.equal(observed.pid, null);
});

test("stdout stream failure is fail-closed and still records the eventual close", async () => {
  const h = processHarness();
  h.child.emit("spawn");
  h.child.stdout.emit("error", new Error("secret stream detail"));
  h.child.complete(null, "SIGTERM");
  const { observed } = await h.promise;
  assert.equal(h.failure.code, "copilot_process_io_failed");
  assert.equal(observed.close_observed, true);
});

test("process receipt is flushed, cleaned up, private and durable before validation", async t => {
  const operations = [];
  const fileSystem = { mkdir: fs.mkdir, async open(file, ...args) {
    const handle = await fs.open(file, ...args), name = path.basename(file);
    return { fd: handle.fd,
      async writeFile(value) { operations.push(`${name}:write`); await handle.writeFile(value); },
      async sync() { operations.push(`${name}:sync`); await handle.sync(); },
      async close() { operations.push(`${name}:close`); await handle.close(); },
    };
  } };
  const h = await invocationFixture(t, { fileSystem, output: "deliberately invalid JSON",
    async publishReceipt(file, receipt) {
      assert.deepEqual(operations, [
        "events.jsonl:write", "events.jsonl:sync", "events.jsonl:close",
        "stderr.log:sync", "stderr.log:close",
      ]);
      await writeNew(file, receipt);
    },
  });
  const { error } = await h.outcome;
  assert(error instanceof SyntaxError);
  const receipt = await h.receipt();
  assert.equal(receipt.format, "pgag-copilot-process-v1");
  assert.equal(receipt.first_failure, null);
  assert.equal(receipt.exit_code, 0);
  assert.deepEqual(receipt.io, { stdout_flushed: true, stderr_flushed: true, stdout_closed: true, stderr_closed: true });
  assert.equal((await fs.stat(path.join(h.directory, "call-000001", "process.json"))).mode & 0o777, 0o600);
  assert.equal((await fs.stat(path.join(h.directory, "call-000001"))).mode & 0o777, 0o700);
  assert.deepEqual(Object.keys(receipt).sort(), [
    "call_id", "close_code", "close_observed", "close_signal", "close_wait_expired", "elapsed_ms",
    "exit_code", "exit_observed", "exit_signal", "first_failure", "format", "invocation_elapsed_ms",
    "io", "observation_scope", "pid", "process_error", "process_error_after_spawn", "process_error_count",
    "secondary_failures", "spawn_attempted", "spawn_observed", "stdout_bytes_observed",
    "stdout_bytes_retained", "termination_requests",
  ]);
  assert.doesNotMatch(JSON.stringify(receipt), /Synthetic|deliberately|prompt|argv|\/private/);
});

for (const elapsed of [269999, 270000, 274000]) {
  test(`invokeCopilot wires maintenance timer and persists its exact policy at ${elapsed}ms`, async t => {
    const h = await invocationFixture(t, { timingProfile: MAINTENANCE_TIMING_PROFILE,
      drive(child, clock) {
        clock.tick(elapsed);
        if (elapsed < 274000) child.complete();
      },
    });
    const { error, value } = await h.outcome, receipt = await h.receipt();
    assert.equal(receipt.timing_profile, MAINTENANCE_TIMING_PROFILE);
    assert.equal(receipt.model_timeout_ms, 270000);
    assert.equal(Object.hasOwn(receipt, "response_timeout_ms"), false);
    assert.equal(receipt.elapsed_ms, elapsed);
    assert.equal(receipt.invocation_elapsed_ms, elapsed);
    assert.equal(receipt.first_failure, elapsed < 270000 ? null : "copilot_timeout");
    if (elapsed < 270000) {
      assert.equal(error, null);
      assert.deepEqual(value, { content: '{"answer":"synthetic"}', usage: usageSummary(measuredUsage(), model) });
      assert.deepEqual(receipt.termination_requests, []);
    } else {
      assert.equal(value, null);
      assert.equal(error.message, "copilot_timeout");
      assert.equal(error.bridge_reusable, elapsed < 274000);
      assert.deepEqual(receipt.termination_requests.map(x => [x.signal, x.elapsed_ms]),
        elapsed < 274000 ? [["SIGTERM", 270000]] : [["SIGTERM", 270000], ["SIGKILL", 272000]]);
    }
    assert.equal(receipt.close_wait_expired, elapsed === 274000);
    assert.equal(receipt.close_observed, elapsed < 274000);
    assert.deepEqual(receipt.secondary_failures, elapsed === 274000 ? ["copilot_close_unacknowledged"] : []);
    assert.equal(h.clock.pending.size, 0);
    assert.doesNotMatch(JSON.stringify(receipt), /Synthetic|prompt|argv|\/private/);
  });
}

test("invokeCopilot maintenance cancellation wins before model timeout and binds the receipt", async t => {
  const h = await invocationFixture(t, { timingProfile: MAINTENANCE_TIMING_PROFILE,
    drive(child, clock, cancellation) {
      clock.tick(269999);
      cancellation.abort();
      clock.tick(1);
      child.complete();
    },
  });
  const { error, value } = await h.outcome, receipt = await h.receipt();
  assert.equal(value, null);
  assert.equal(error.message, "copilot_interrupted");
  assert.equal(receipt.first_failure, "copilot_interrupted");
  assert.equal(receipt.timing_profile, MAINTENANCE_TIMING_PROFILE);
  assert.equal(receipt.model_timeout_ms, 270000);
  assert.equal(receipt.exit_code, 0);
  assert.deepEqual(receipt.termination_requests.map(x => [x.signal, x.elapsed_ms]),
    [["SIGTERM", 269999]]);
  assert.equal(h.clock.pending.size, 0);
});

test("maintenance timeout stays primary if receipt publication also fails", async t => {
  const h = await invocationFixture(t, { timingProfile: MAINTENANCE_TIMING_PROFILE,
    drive(child, clock) { clock.tick(270000); child.complete(); },
    async publishReceipt() { throw new Error("synthetic detail"); },
  });
  const { error, value } = await h.outcome;
  assert.equal(value, null);
  assert.equal(error.message, "copilot_timeout");
  assert.equal(error.bridge_reusable, false);
  assert.deepEqual(h.diagnostics.map(JSON.parse), [{
    format: "pgag-copilot-diagnostic-v1", call_id: "000001",
    primary_code: "copilot_timeout", secondary_code: "copilot_process_receipt_failed",
  }]);
});

test("invalid direct timing profiles reject before observing, creating files, or spawning", async () => {
  const forbidden = () => assert.fail("invalid timing must not cause side effects");
  for (const timingProfile of [null, false, 270000, "", "default", "maintenance-300s-v2", "maintenance-300s-v1 "]) {
    assert.throws(() => observeCopilotProcess([], {}, {
      timingProfile, spawnProcess: forbidden, now: forbidden,
      timers: { setTimeout: forbidden, clearTimeout: forbidden },
    }), { name: "EvaluationError", code: "invalid_timing_profile" });
    await assert.rejects(invokeCopilot({
      get directory() { return forbidden(); },
      model, reasoning_effort: "high", timing_profile: timingProfile,
    }, "000001", "Synthetic only.", {
      spawnProcess: forbidden, now: forbidden, fileSystem: { mkdir: forbidden, open: forbidden },
      publishReceipt: forbidden, diagnostic: forbidden,
    }), { name: "EvaluationError", code: "invalid_timing_profile", message: "invalid_timing_profile" });
  }
});

test("writeNew fsyncs file then parent directory and never overwrites prior evidence", async t => {
  const directory = await directoryFixture(t), file = path.join(directory, "process.json");
  const opened = [], operations = [], originalOpen = fs.open.bind(fs);
  t.mock.method(fs, "open", async (target, ...args) => {
    const handle = await originalOpen(target, ...args);
    opened.push({ target, flags: args[0], mode: args[1] });
    return {
      async writeFile(value) { operations.push("write"); await handle.writeFile(value); },
      async sync() { operations.push(target === directory ? "directory:sync" : "file:sync"); await handle.sync(); },
      async close() { operations.push(target === directory ? "directory:close" : "file:close"); await handle.close(); },
    };
  });
  await writeNew(file, { format: "synthetic", value: 1 });
  assert.deepEqual(operations, ["write", "file:sync", "file:close", "directory:sync", "directory:close"]);
  assert.equal(opened[0].flags, "wx");
  assert.equal(opened[0].mode, 0o600);
  assert.equal(opened[1].target, directory);
  const original = await fs.readFile(file);
  await assert.rejects(writeNew(file, { value: 2 }), { code: "EEXIST" });
  assert.deepEqual(await fs.readFile(file), original);
  assert.equal((await fs.stat(file)).mode & 0o777, 0o600);
});

test("a directory fsync failure prevents success even if process.json is already linked", async t => {
  const originalOpen = fs.open.bind(fs);
  t.mock.method(fs, "open", async (target, ...args) => {
    const handle = await originalOpen(target, ...args);
    if (!String(target).endsWith("call-000001")) return handle;
    return {
      async sync() { throw new Error("secret sync failure"); },
      close: handle.close.bind(handle),
    };
  });
  const h = await invocationFixture(t);
  const { error } = await h.outcome;
  assert.equal(error.message, "copilot_process_receipt_failed");
  assert.equal(error.bridge_reusable, false);
  assert.equal((await h.receipt()).exit_code, 0);
});

test("receipt collision never overwrites existing evidence or permits success", async t => {
  const h = await invocationFixture(t, { async publishReceipt(file, receipt) {
    await fs.writeFile(file, '{"preserved":"synthetic original"}\n', { flag: "wx", mode: 0o600 });
    await writeNew(file, receipt);
  } });
  const { error } = await h.outcome;
  assert.equal(error.message, "copilot_process_receipt_failed");
  assert.equal(error.bridge_reusable, false);
  assert.deepEqual(await h.receipt(), { preserved: "synthetic original" });
});

for (const timedOut of [false, true]) {
  test(`receipt failure ${timedOut ? "preserves timeout and logs safe secondary" : "cannot produce success"}`, async t => {
    const h = await invocationFixture(t, {
      drive(child, clock) { if (timedOut) clock.tick(150000); child.complete(); },
      async publishReceipt() { throw new Error("secret token /private/path"); },
    });
    const { error, value } = await h.outcome;
    assert.equal(value, null);
    assert.equal(error.message, timedOut ? "copilot_timeout" : "copilot_process_receipt_failed");
    assert.equal(error.bridge_reusable, false);
    if (timedOut) assert.deepEqual(h.diagnostics.map(JSON.parse), [{
      format: "pgag-copilot-diagnostic-v1", call_id: "000001",
      primary_code: "copilot_timeout", secondary_code: "copilot_process_receipt_failed",
    }]);
    assert.doesNotMatch(h.diagnostics.join("\n"), /secret|private|token/);
    await assert.rejects(h.receipt(), { code: "ENOENT" });
  });
}

for (const timedOut of [false, true]) {
  test(`handle cleanup failure ${timedOut ? "preserves the primary" : "fails closed"} and attempts both closes`, async t => {
    const closed = [];
    const fileSystem = { mkdir: fs.mkdir, async open(file, ...args) {
      const handle = await fs.open(file, ...args);
      return { fd: handle.fd, writeFile: handle.writeFile.bind(handle), sync: handle.sync.bind(handle),
        async close() {
          await handle.close();
          closed.push(path.basename(file));
          if (file.endsWith("events.jsonl")) throw new Error("secret /path");
        },
      };
    } };
    const h = await invocationFixture(t, { fileSystem,
      drive(child, clock) { if (timedOut) clock.tick(150000); child.complete(); },
    });
    const { error } = await h.outcome, receipt = await h.receipt();
    assert.equal(error.message, timedOut ? "copilot_timeout" : "copilot_process_io_failed");
    assert.equal(error.bridge_reusable, false);
    assert.deepEqual(closed, ["events.jsonl", "stderr.log"]);
    assert.equal(receipt.io.stdout_closed, false);
    assert.equal(receipt.io.stderr_closed, true);
    assert.equal(receipt.first_failure, error.message);
    if (timedOut) assert.equal(JSON.parse(h.diagnostics[0]).secondary_code, "copilot_process_io_failed");
    assert.doesNotMatch(JSON.stringify(receipt) + h.diagnostics.join(""), /secret|\/path/);
  });
}

test("unacknowledged close is persisted as unknown, logged safely and disallows bridge reuse", async t => {
  const h = await invocationFixture(t, { drive(_child, clock) { clock.tick(154000); } });
  const { error } = await h.outcome, receipt = await h.receipt();
  assert.equal(error.message, "copilot_timeout");
  assert.equal(error.bridge_reusable, false);
  assert.equal(receipt.first_failure, "copilot_timeout");
  assert.equal(receipt.close_wait_expired, true);
  assert.equal(receipt.close_observed, false);
  assert.equal(receipt.exit_code, null);
  assert.deepEqual(receipt.secondary_failures, ["copilot_close_unacknowledged"]);
  assert.equal(JSON.parse(h.diagnostics[0]).secondary_code, "copilot_close_unacknowledged");
});

for (const timingProfile of [undefined, MAINTENANCE_TIMING_PROFILE]) {
  test(`main forwards ${timingProfile ?? "legacy"} config and publishes exact transport metadata offline`, async t => {
    const directory = await directoryFixture(t), attempts = [], probes = [];
    const previousUmask = process.umask();
    let completion;
    const config = { directory, model, reasoning_effort: "high", max_calls: 1,
      run_id: "agent-eval-0123456789abcdef",
      ...(timingProfile === undefined ? {} : { timing_profile: timingProfile }),
    };
    try {
      completion = main([
        "--directory", directory, "--model", model, "--reasoning-effort", "high",
        "--max-calls", "1", "--run-id", config.run_id,
        ...(timingProfile === undefined ? [] : ["--timing-profile", timingProfile]),
      ], {
        versionProbe(command, args, options) {
          probes.push({ command, args, options });
          return { status: 0, stdout: "GitHub Copilot CLI 1.0.88" };
        },
        async invoke(actualConfig, callId, prompt) {
          attempts.push({ config: actualConfig, callId, prompt });
          return { content: '{"answer":"synthetic"}', usage: usageSummary(measuredUsage(), model) };
        },
      }).then(() => null, error => error);
      for (let i = 0; i < 100; i++) {
        if ((await fs.readdir(directory)).includes("transport.json")) break;
        await sleep(10);
      }
      const metadata = JSON.parse(await fs.readFile(path.join(directory, "transport.json")));
      assert.deepEqual(metadata, {
        format: "pgag-copilot-transport-v1", run_id: config.run_id,
        model, reasoning_effort: "high", cli_version: "1.0.88", max_calls: 1,
        fresh_session_per_call: true, custom_instructions: false, tools_allowed: false,
        model_weights_revision_verified: false,
        ...(timingProfile === undefined ? {} : {
          timing_profile: MAINTENANCE_TIMING_PROFILE, model_timeout_ms: 270000, response_timeout_ms: 300000,
        }),
      });
      await fs.mkdir(path.join(directory, "queue"), { mode: 0o700 });
      await fs.writeFile(path.join(directory, "queue", "000001.request.json"), JSON.stringify({
        format: "pgag-copilot-request-v1", call_id: "000001", prompt: "Synthetic only.",
      }), { mode: 0o600 });
      assert.equal(await completion, null);
      assert.deepEqual(probes, [{
        command: "copilot", args: ["--version"], options: { encoding: "utf8", timeout: 10000 },
      }]);
      assert.deepEqual(attempts, [{ config, callId: "000001", prompt: "Synthetic only." }]);
      const response = JSON.parse(await fs.readFile(path.join(directory, "queue", "000001.response.json")));
      assert.equal(typeof response.duration_seconds, "number");
      assert.deepEqual({ ...response, duration_seconds: 0 }, {
        format: "pgag-copilot-response-v1", call_id: "000001", status: "ok",
        content: '{"answer":"synthetic"}', error: null, duration_seconds: 0,
        model, reasoning_effort: "high", usage: usageSummary(measuredUsage(), model),
      });
    } finally {
      if (completion) {
        await fs.writeFile(path.join(directory, "stop.json"), "{}", { mode: 0o600 });
        await completion;
      }
      process.umask(previousUmask);
    }
  });
}

test("main rejects unknown timing before inspecting directories or probing a CLI", async t => {
  const previousUmask = process.umask();
  const forbidden = () => assert.fail("invalid timing must not touch the filesystem or CLI");
  t.mock.method(fs, "lstat", forbidden);
  await assert.rejects(main([
    "--directory", path.resolve(".unused-invalid-timing-profile"), "--model", model,
    "--reasoning-effort", "high", "--max-calls", "1", "--run-id", "agent-eval-0123456789abcdef",
    "--timing-profile", "maintenance-300s-v2",
  ], { versionProbe: forbidden, invoke: forbidden }), {
    name: "EvaluationError", code: "invalid_timing_profile",
  });
  assert.equal(process.umask(), previousUmask);
});

test("bridge loop publishes a failed response but never starts the next call after lost close acknowledgement", async t => {
  const directory = await directoryFixture(t);
  const { main } = await import("./copilot-eval-bridge.mjs?offline-main-injection");
  const previousExit = process.exitCode, previousUmask = process.umask();
  const attempts = [];
  let completion;
  try {
    completion = main(["--directory", directory, "--model", model, "--reasoning-effort", "high",
      "--max-calls", "160", "--run-id", "agent-eval-0123456789abcdef"], {
      versionProbe: () => ({ status: 0, stdout: "GitHub Copilot CLI 1.0.88" }),
      async invoke(_config, callId) {
        attempts.push(callId);
        throw Object.assign(new Error("copilot_timeout"), { bridge_reusable: false });
      },
    });
    for (let i = 0; i < 100; i++) {
      if ((await fs.readdir(directory)).includes("transport.json")) break;
      await sleep(10);
    }
    await fs.mkdir(path.join(directory, "queue"), { mode: 0o700 });
    for (const callId of ["000001", "000002"]) {
      await fs.writeFile(path.join(directory, "queue", `${callId}.request.json`), JSON.stringify({
        format: "pgag-copilot-request-v1", call_id: callId, prompt: "Synthetic only.",
      }), { mode: 0o600 });
    }
    await completion;
    assert.deepEqual(attempts, ["000001"]);
    const response = JSON.parse(await fs.readFile(path.join(directory, "queue", "000001.response.json")));
    assert.equal(response.status, "error");
    assert.equal(response.error, "copilot_timeout");
    assert.equal(response.usage, null);
    assert.equal(response.content, null);
    assert.deepEqual(Object.keys(response).sort(), [
      "call_id", "content", "duration_seconds", "error", "format", "model", "reasoning_effort", "status", "usage",
    ]);
    assert.equal(JSON.parse(await fs.readFile(path.join(directory, "bridge-summary.json"))).calls, 1);
    await assert.rejects(fs.stat(path.join(directory, "queue", "000002.response.json")), { code: "ENOENT" });
    assert.equal(process.exitCode, 1);
  } finally {
    if (completion) {
      await fs.writeFile(path.join(directory, "stop.json"), "{}");
      await completion;
    }
    process.exitCode = previousExit;
    process.umask(previousUmask);
  }
});

test("output flush failure preserves timeout and still closes both handles", async t => {
  const closed = [];
  const fileSystem = { mkdir: fs.mkdir, async open(file, ...args) {
    const handle = await fs.open(file, ...args);
    return { fd: handle.fd,
      async writeFile() { throw new Error("synthetic secret filesystem detail"); },
      sync: handle.sync.bind(handle),
      async close() { await handle.close(); closed.push(path.basename(file)); },
    };
  } };
  const h = await invocationFixture(t, { fileSystem,
    drive(child, clock) { clock.tick(150000); child.complete(); },
  });
  assert.equal((await h.outcome).error.message, "copilot_timeout");
  const receipt = await h.receipt();
  assert.equal(receipt.io.stdout_flushed, false);
  assert.equal(receipt.io.stdout_closed, true);
  assert.equal(receipt.io.stderr_closed, true);
  assert.deepEqual(closed, ["events.jsonl", "stderr.log"]);
  assert.equal(JSON.parse(h.diagnostics[0]).secondary_code, "copilot_process_io_failed");
  assert.doesNotMatch(JSON.stringify(receipt) + h.diagnostics.join(""), /secret|filesystem/);
});

test("interruption during receipt publication prevents a successful response", async t => {
  const cancellation = new AbortController();
  const h = await invocationFixture(t, { cancellation, async publishReceipt(file, receipt) {
    await writeNew(file, receipt);
    cancellation.abort();
  } });
  assert.equal((await h.outcome).error.message, "copilot_interrupted");
  assert.equal((await h.receipt()).exit_code, 0);
});

test("interruption during asynchronous usage reading also prevents success", async t => {
  const cancellation = new AbortController(), originalOpen = fs.open.bind(fs);
  t.mock.method(fs, "open", async (file, ...args) => {
    const handle = await originalOpen(file, ...args);
    if (!String(file).endsWith("usage.json")) return handle;
    return { stat: handle.stat.bind(handle), close: handle.close.bind(handle),
      async read(...readArgs) {
        const result = await handle.read(...readArgs);
        cancellation.abort();
        return result;
      },
    };
  });
  const h = await invocationFixture(t, { cancellation });
  assert.equal((await h.outcome).error.message, "copilot_interrupted");
  assert.equal((await h.receipt()).exit_code, 0);
});

for (const outcome of ["success", "nonzero", "signal"]) {
  test(`real local fake copilot ${outcome} produces an actual process receipt without model calls`, async t => {
    const directory = await directoryFixture(t), executable = path.join(directory, "copilot");
    await fs.writeFile(executable, `#!${process.execPath}
const fs = require("node:fs");
fs.writeFileSync(process.argv[process.argv.indexOf("--usage-output-file")+1],
  ${JSON.stringify(JSON.stringify(measuredUsage()))}, {mode:0o600});
fs.writeSync(1, ${JSON.stringify(events())});
${outcome === "nonzero" ? "process.exit(7);" : outcome === "signal" ? 'process.kill(process.pid,"SIGTERM");' : ""}
`, { mode: 0o700 });
    const invoke = invokeCopilot({ directory, model, reasoning_effort: "high" }, "000001", "Synthetic only.", {
      spawnProcess: (_command, args, options) => spawn(executable, args, options),
    });
    if (outcome === "success") assert.equal((await invoke).usage.nano_aiu, 123.25);
    else await assert.rejects(invoke, { message: "copilot_unsuccessful" });
    const receipt = JSON.parse(await fs.readFile(path.join(directory, "call-000001", "process.json")));
    assert.equal(receipt.spawn_attempted, true);
    assert.equal(receipt.spawn_observed, true);
    assert(Number.isSafeInteger(receipt.pid) && receipt.pid > 0);
    assert.equal(receipt.exit_observed, true);
    assert.equal(receipt.close_observed, true);
    assert.equal(receipt.exit_code, outcome === "success" ? 0 : outcome === "nonzero" ? 7 : null);
    assert.equal(receipt.exit_signal, outcome === "signal" ? "SIGTERM" : null);
    assert.equal(receipt.close_code, receipt.exit_code);
    assert.equal(receipt.close_signal, receipt.exit_signal);
    assert.deepEqual(receipt.termination_requests, []);
    assert.equal(receipt.first_failure, outcome === "success" ? null : "copilot_unsuccessful");
    assert.equal((await fs.stat(path.join(directory, "call-000001", "process.json"))).mode & 0o777, 0o600);
    assert.doesNotMatch(JSON.stringify(receipt), /Synthetic|token|PATH|argv|\/Users/);
  });
}

test("arguments require an explicit model, bounded calls and owned run identity", () => {
  const args = ["--directory", "/private/example", "--model", model, "--reasoning-effort", "high",
    "--max-calls", "100", "--run-id", "agent-eval-0123456789abcdef"];
  assert.equal(parseArguments(args).max_calls, 100);
  assert.throws(() => parseArguments(args.concat("--model", "other")));
  assert.equal(parseArguments(args.map((value) => value === "100" ? "120" : value)).max_calls, 120);
  assert.equal(parseArguments(args.map((value) => value === "100" ? "160" : value)).max_calls, 160);
  assert.throws(() => parseArguments(args.map((value) => value === "100" ? "161" : value)));
  assert.throws(() => parseArguments(args.filter((value) => !["--max-calls", "100"].includes(value))));
  assert.throws(() => parseArguments(args.map((value) => value === "high" ? "auto" : value)));
});

test("bridge timing is one explicit named opt-in with unchanged omitted arguments", () => {
  const args = ["--directory", "/private/example", "--model", model, "--reasoning-effort", "high",
    "--max-calls", "100", "--run-id", "agent-eval-0123456789abcdef"];
  const legacy = { directory: "/private/example", model, reasoning_effort: "high",
    max_calls: 100, run_id: "agent-eval-0123456789abcdef",
  };
  assert.deepEqual(parseArguments(args), legacy);
  assert.deepEqual(parseArguments([...args, "--timing-profile", MAINTENANCE_TIMING_PROFILE]), {
    ...legacy, timing_profile: MAINTENANCE_TIMING_PROFILE,
  });
  for (const profile of ["default", "270000", "maintenance-300s-v2", "maintenance-300s-v1 "]) {
    assert.throws(() => parseArguments([...args, "--timing-profile", profile]), {
      name: "EvaluationError", code: "invalid_timing_profile",
    });
  }
  for (const extra of [
    ["--timing-profile"], ["--timing-profile", ""],
    ["--timing-profile", MAINTENANCE_TIMING_PROFILE, "--timing-profile", MAINTENANCE_TIMING_PROFILE],
    ["--model-timeout-ms", "270000"], ["--response-timeout-ms", "300000"],
  ]) assert.throws(() => parseArguments([...args, ...extra]), { message: "invalid_bridge_arguments" });
});

test("requests reject mismatched identities, hidden fields and unbounded input", () => {
  const value = { format: "pgag-copilot-request-v1", call_id: "000001", prompt: "Synthetic only." };
  assert.deepEqual(validateRequest(value, "000001"), value);
  assert.throws(() => validateRequest(value, "000002"));
  assert.throws(() => validateRequest({ ...value, command: "not permitted" }, "000001"));
  assert.throws(() => validateRequest({ ...value, prompt: "x".repeat(65537) }, "000001"));
});

test("responses require evidence of zero tools and exact requested model", () => {
  assert.equal(auditEvents(events(), model), '{"answer":"synthetic"}');
  assert.throws(() => auditEvents(events({ tool_count: 1 }), model));
  assert.throws(() => auditEvents(events({ model: "different" }), model));
  assert.throws(() => auditEvents(events({ reasoning_effort: "low" }), model));
  assert.throws(() => auditEvents(events({ tool_count: null }), model));
  assert.throws(() => auditEvents(events() + '\n{"type":"tool.execution_start"}', model));
  assert.throws(() => auditEvents('{"type":"result","exitCode":0}', model));
  assert.throws(() => auditEvents(events() + '\n{"type":"result","exitCode":0}', model));
});

test("usage is measured rather than invented or labeled as money", () => {
  const raw = { currentModel: model, totalApiDurationMs: 10, modelMetrics: {
    [model]: { requests: { count: 1, cost: 1 }, usage: {
      inputTokens: 20, outputTokens: 4, cacheReadTokens: 3, cacheWriteTokens: 17,
    } },
  } };
  const result = usageSummary(raw, model);
  assert.equal(result.input_tokens, 20);
  assert.equal(result.reasoning_tokens, null);
  assert.equal(result.nano_aiu, null);
  assert.equal(result.monetary_cost_verified, false);
  assert.throws(() => usageSummary({}, model));
  assert.throws(() => usageSummary({ ...raw, currentModel: "other" }, model));
});

test("owned harness reads current and legacy Apple Container IPv4 fields", () => {
  const source = readFileSync(new URL("./evaluate-agent-memory-containers.sh", import.meta.url), "utf8");
  const helper = source.match(/container_host\(\) \{[\s\S]*?\n\}/)[0];
  for (const value of [
    [{ status: { networks: [{ ipv4Address: "192.168.65.20/24" }] } }],
    [{ networks: [{ ipv4Address: "192.168.65.20/24" }] }],
    [{ networks: [{}] }],
  ]) {
    const result = spawnSync("bash", ["-c", `${helper}
container() { printf '%s\\n' '${JSON.stringify(value)}'; }
container_host owned-node`], { encoding: "utf8" });
    if (value[0].networks?.[0]?.ipv4Address || value[0].status) {
      assert.equal(result.status, 0, result.stderr);
      assert.equal(result.stdout.trim(), "192.168.65.20");
    } else {
      assert.notEqual(result.status, 0);
    }
  }
});

test("wrapper ceilings remain explicit per policy", () => {
  const source = readFileSync(new URL("./evaluate-agent-memory-containers.sh", import.meta.url), "utf8");
  const parsing = source.split('[[ "$model" =~')[0];
  for (const [options, policy, retention, ceiling] of [
    [[], "lexical-v2", "model-purge-v1", 100],
    [["--query-policy", "legacy-v1"], "legacy-v1", "model-purge-v1", 100],
    ...["bounded-lexical-v3", "bounded-lexical-v4", "bounded-lexical-v5"].map((policy) =>
      [["--query-policy", policy], policy, "review-v1", 120]),
    [["--query-policy", "bounded-lexical-v6"], "bounded-lexical-v6", "review-v1", 160],
    [["--query-policy", "bounded-lexical-v6", "--retention-policy", "model-purge-v1"],
      "bounded-lexical-v6", "model-purge-v1", 160],
  ]) {
    const result = spawnSync("bash", ["-c", `${parsing}
printf '%s %s %s\\n' "$query_policy" "$retention_policy" "$max_calls"`,
    "harness", "private-test", model, "high", "--allow-copilot", ...options],
    { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), `${policy} ${retention} ${ceiling}`);
  }
});

test("English profile selection is explicit, sequential-only, and forwarded without a budget change", () => {
  const source = readFileSync(new URL("./evaluate-agent-memory-containers.sh", import.meta.url), "utf8");
  const parsing = source.split('[[ "$model" =~')[0];
  for (const [options, accepted, profile, ceiling] of [
    [[], true, "simple-v1", 100],
    [["--english-search-profile", "simple-v1"], true, "simple-v1", 100],
    [["--english-search-profile", "en-snowball-v1"], false],
    [["--query-policy", "bounded-lexical-v5", "--english-search-profile", "en-snowball-v1"], false],
    [["--query-policy", "bounded-lexical-v6", "--english-search-profile", "en-snowball-v1",
      "--cohort", "distractor-synthetic-v1", "--retention-policy", "review-v1"],
      true, "en-snowball-v1", 160],
    [["--english-search-profile", "unknown"], false],
    [["--english-search-profile"], false],
    [["--english-search-profile", "--query-policy"], false],
    [["--english-search-profile", "simple-v1", "--english-search-profile", "simple-v1"], false],
  ]) {
    const result = spawnSync("bash", ["-c", `${parsing}
printf '%s %s\\n' "$english_search_profile" "$max_calls"`,
    "harness", "private-test", model, "high", "--allow-copilot", ...options],
    { encoding: "utf8" });
    assert.equal(result.status, accepted ? 0 : 2, result.stderr);
    if (accepted) assert.equal(result.stdout.trim(), `${profile} ${ceiling}`);
  }
  assert.ok(source.includes('--english-search-profile "$english_search_profile"'));
  assert.ok(source.includes("english_search_profile:$english_profile"));
  assert.ok(source.includes("git status --porcelain"));
});

for (const ceiling of [1, 160]) {
  test(`bridge honors ${ceiling} guest queue calls without model calls`,
  { timeout: 60000 }, async t => {
    const canonical = await directoryFixture(t);
    const bridge = path.join(canonical, "bridge");
    const bin = path.join(canonical, "bin");
    await fs.mkdir(bridge, { mode: 0o700 });
    await fs.mkdir(bin, { mode: 0o700 });
    const fake = `#!${process.execPath}
const fs = require("node:fs");
if (process.argv.includes("--version")) {
  console.log("GitHub Copilot CLI 1.0.88.");
} else {
  const usage = {currentModel:"gpt-6-astra",totalApiDurationMs:1,
    modelMetrics:{"gpt-6-astra":{requests:{count:1,cost:1},
      usage:{inputTokens:10,outputTokens:5,cacheReadTokens:0,cacheWriteTokens:0}}}};
  fs.writeFileSync(process.argv[process.argv.indexOf("--usage-output-file")+1],
    JSON.stringify(usage),{mode:0o600});
  process.stdout.write(${JSON.stringify(events())});
}
`;
    await fs.writeFile(path.join(bin, "copilot"), fake, { mode: 0o700 });
    const child = spawn(process.execPath, [
      new URL("./copilot-eval-bridge.mjs", import.meta.url).pathname,
      "--directory", bridge, "--model", model, "--reasoning-effort", "high",
      "--max-calls", String(ceiling), "--run-id", "agent-eval-0123456789abcdef",
    ], { env: { ...process.env, PATH: `${bin}:${process.env.PATH}` }, stdio: "pipe" });
    let diagnostics = "";
    child.stderr.on("data", (chunk) => { diagnostics += chunk; });
    const exited = new Promise((resolve) => child.once("exit", resolve));
    try {
      for (let i = 0; i < 100; i += 1) {
        const entries = await fs.readdir(bridge);
        if (entries.includes("transport.json")) break;
        if (child.exitCode !== null) throw new Error(diagnostics || "test bridge exited");
        await sleep(20);
      }
      const metadata = JSON.parse(await fs.readFile(path.join(bridge, "transport.json")));
      assert.equal(metadata.tools_allowed, false);
      assert.equal(metadata.max_calls, ceiling);
      await fs.mkdir(path.join(bridge, "queue"), { mode: 0o700 });
      for (let number = 1; number <= ceiling + 1; number += 1) {
        const callId = String(number).padStart(6, "0");
        await fs.writeFile(path.join(bridge, "queue", `${callId}.request.json`), JSON.stringify({
          format: "pgag-copilot-request-v1", call_id: callId, prompt: "Synthetic transport test.",
        }), { mode: 0o600 });
      }
      assert.equal(await exited, 0, diagnostics);
      const last = `${String(ceiling).padStart(6, "0")}.response.json`;
      const response = JSON.parse(await fs.readFile(path.join(bridge, "queue", last)));
      assert.equal(response.status, "ok");
      assert.equal(response.content, '{"answer":"synthetic"}');
      assert.equal(response.usage.input_tokens, 10);
      assert.equal(JSON.parse(await fs.readFile(path.join(bridge, "bridge-summary.json"))).calls, ceiling);
      for (let number = 1; number <= ceiling; number += 1) {
        const processFile = path.join(bridge, `call-${String(number).padStart(6, "0")}`, "process.json");
        const receipt = JSON.parse(await fs.readFile(processFile));
        assert.equal(receipt.format, "pgag-copilot-process-v1");
        assert.equal(receipt.spawn_observed, true);
        assert.equal(receipt.exit_observed, true);
        assert.equal(receipt.close_observed, true);
        assert.equal(receipt.exit_code, 0);
        assert.equal(receipt.first_failure, null);
        assert.equal((await fs.stat(processFile)).mode & 0o777, 0o600);
      }
      const extra = `${String(ceiling + 1).padStart(6, "0")}.response.json`;
      await assert.rejects(fs.stat(path.join(bridge, "queue", extra)), { code: "ENOENT" });
    } finally {
      if (child.exitCode === null) {
        child.kill("SIGTERM");
        await exited;
      }
    }
  });
}
