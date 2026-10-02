import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import test from "node:test";
import { observeCopilotProcess } from "./copilot-eval-bridge.mjs";
import { EvaluationError, InvocationLedger } from "./development-eval-protocol.mjs";
import { evaluationTiming, MAINTENANCE_TIMING_PROFILE } from "./development-eval-timing.mjs";
import { CopilotTransport, writeNew } from "./development-eval-transport.mjs";

class Clock {
  wall = Date.now();
  monotonic = 0;
  serial = 0;
  pending = new Map();
  runtime = { wallNow: () => this.wall, monotonicNow: () => this.monotonic, timers: this };
  setTimeout(callback, delay) {
    const id = ++this.serial;
    this.pending.set(id, { at: this.monotonic + delay, callback });
    return id;
  }
  clearTimeout(id) { this.pending.delete(id); }
  jump(milliseconds) { this.wall += milliseconds; this.monotonic += milliseconds; }
  tick(milliseconds) {
    const end = this.monotonic + milliseconds;
    while (true) {
      const next = [...this.pending.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > end) break;
      this.jump(Math.max(0, next[1].at - this.monotonic));
      this.pending.delete(next[0]);
      next[1].callback();
    }
    this.jump(Math.max(0, end - this.monotonic));
  }
}

const context = {
  arm: "no_memory", sessionId: "timing", slotId: "timing", phase: "work",
  prompt: "Synthetic timing test only.", sequence: 1,
};
const response = {
  format: "pgag-copilot-response-v1", call_id: "000001", status: "ok", content: '{"final":"done"}',
  error: null, duration_seconds: 1, model: "gpt-6-astra", reasoning_effort: "high",
  usage: { input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0,
    reasoning_tokens: null, api_requests: 1, premium_requests: 0, nano_aiu: null,
    api_duration_ms: 1, monetary_cost_verified: false },
};

async function fixture(t) {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-timing-test-")));
  t.after(() => fs.rm(directory, { recursive: true }));
  await fs.mkdir(path.join(directory, "queue"), { mode: 0o700 });
  const clock = new Clock(), events = [], stops = [];
  const record = event => events.push(event);
  const ledger = new InvocationLedger({ runId: "timing", model: "gpt-6-astra", effort: "high", record });
  const transport = new CopilotTransport({
    directory, runId: "timing", model: "gpt-6-astra", effort: "high", ledger, record,
    timingProfile: MAINTENANCE_TIMING_PROFILE,
  }, clock.runtime);
  const bridge = { directory, exited: false };
  transport.bridge = async () => bridge;
  transport.stop = async (arm, options) => {
    stops.push({ arm, ...options, monotonic: clock.monotonic });
  };
  const request = path.join(directory, "queue", "000001.request.json");
  const responseFile = path.join(directory, "queue", "000001.response.json");
  const invoke = options => transport.invoke(context, options)
    .then(value => ({ value, error: null }), error => ({ value: null, error }));
  return { directory, clock, events, stops, ledger, transport, bridge, request, responseFile, invoke };
}

async function published(file) {
  for (let i = 0; i < 200; i++) {
    if (existsSync(file)) return;
    await sleep(5);
  }
  assert.fail("synthetic request not published");
}

function advanceDuringRead(t, file, advance) {
  const open = fs.open;
  t.mock.method(fs, "open", async (...args) => {
    const handle = await open(...args);
    if (args[0] === file) {
      const read = handle.read.bind(handle);
      t.mock.method(handle, "read", async (...readArgs) => {
        const result = await read(...readArgs);
        advance();
        return result;
      });
    }
    return handle;
  });
}

test("timing selection is explicit, immutable and preserves legacy defaults", () => {
  assert.deepEqual(evaluationTiming(), { model_ms: 150000, response_ms: 180000 });
  assert.deepEqual(evaluationTiming(MAINTENANCE_TIMING_PROFILE),
    { model_ms: 270000, response_ms: 300000 });
  assert.ok(Object.isFrozen(evaluationTiming()));
  assert.ok(Object.isFrozen(evaluationTiming(MAINTENANCE_TIMING_PROFILE)));
  for (const value of [null, "", "300", 300000, {}, "maintenance-300s-v2"]) {
    assert.throws(() => evaluationTiming(value), /invalid_timing_profile/);
    assert.throws(() => new CopilotTransport({ timingProfile: value }), /invalid_timing_profile/);
  }
});

for (const elapsed of [179999, 180000]) {
  test(`omitted timing profile retains the original ${elapsed}ms response boundary`, async t => {
    const f = await fixture(t);
    await writeNew(f.responseFile, response);
    t.mock.method(Date, "now", () => f.clock.wall);
    const transport = new CopilotTransport({
      directory: f.directory, runId: "timing", model: "gpt-6-astra", effort: "high",
      ledger: f.ledger, record: event => f.events.push(event),
    });
    transport.bridge = async () => f.bridge;
    transport.stop = f.transport.stop;
    const invocation = transport.invoke(context, { beforePublish: () => f.clock.jump(elapsed) });
    if (elapsed < 180000) assert.equal((await invocation).text, response.content);
    else await assert.rejects(invocation, /response_deadline/);
    assert.equal(f.ledger.stopped, elapsed === 180000);
    assert.equal(f.clock.pending.size, 0);
  });
}

test("opt-in accepts an actual response at 299999ms without the old 180-second clamp", async t => {
  const f = await fixture(t);
  await writeNew(f.responseFile, response);
  advanceDuringRead(t, f.responseFile, () => f.clock.jump(299999));
  const { value, error } = await f.invoke();
  assert.equal(error, null);
  assert.equal(value.text, response.content);
  assert.equal(f.ledger.stopped, false);
  assert.equal(f.stops.length, 0);
  assert.equal(f.clock.pending.size, 0);
});

for (const [name, advance] of [
  ["wall equality", clock => { clock.wall += 300000; }],
  ["monotonic equality", clock => { clock.monotonic = 300000; }],
  ["both after cutoff", clock => clock.jump(300001)],
  ["backward wall with expired monotonic time", clock => {
    clock.wall -= 600000; clock.monotonic = 300000;
  }],
]) {
  test(`opt-in rejects ${name} even before its timer callback runs`, async t => {
    const f = await fixture(t);
    await writeNew(f.responseFile, response);
    advanceDuringRead(t, f.responseFile, () => advance(f.clock));
    const { value, error } = await f.invoke();
    assert.equal(value, null);
    assert.equal(error.code, "response_deadline");
    assert.equal(error.usage, null);
    assert.equal(f.ledger.ordinal, 1);
    assert.equal(f.ledger.stopped, true);
    assert.equal(f.stops.length, 1);
    assert.equal(f.stops[0].cancel, true);
    assert.equal(f.clock.pending.size, 0);
  });
}

test("monotonic cutoff actively cancels a missing response after delayed startup and wall rollback", async t => {
  const f = await fixture(t);
  f.transport.bridge = async () => { f.clock.tick(60000); return f.bridge; };
  const outcome = f.invoke();
  await published(f.request);
  f.clock.wall -= 600000;
  f.clock.tick(239999);
  assert.equal(f.stops.length, 0);
  f.clock.tick(1);
  assert.equal(f.stops.length, 1);
  assert.equal(f.stops[0].monotonic, 300000);
  assert.equal(f.ledger.stopped, true);
  const { error } = await outcome;
  assert.equal(error.code, "response_deadline");
  assert.equal(error.usage, null);
  assert.equal(f.stops.length, 1);
  assert.equal(error.cleanupDeadline, f.stops[0].deadline);
  assert.equal(f.clock.pending.size, 0);
  await writeNew(f.responseFile, response);
  await assert.rejects(f.transport.invoke({ ...context, sequence: 2 }), /admission_stopped/);
  assert.equal(f.ledger.ordinal, 1);
});

test("the response cutoff interrupts an outstanding read, not just the next polling iteration", async t => {
  const f = await fixture(t);
  await writeNew(f.responseFile, response);
  const open = fs.open;
  let release, reading;
  const started = new Promise(resolve => { reading = resolve; });
  const blocked = new Promise(resolve => { release = resolve; });
  t.mock.method(fs, "open", async (...args) => {
    const handle = await open(...args);
    if (args[0] === f.responseFile) {
      const read = handle.read.bind(handle);
      t.mock.method(handle, "read", async (...readArgs) => {
        reading();
        await blocked;
        return read(...readArgs);
      });
    }
    return handle;
  });
  const outcome = f.invoke();
  await started;
  f.clock.wall -= 600000;
  f.clock.tick(300000);
  assert.equal(f.stops.length, 1);
  const { error } = await outcome;
  assert.equal(error.code, "response_deadline");
  assert.equal(f.ledger.stopped, true);
  release();
  await sleep(10);
  assert.equal(f.events.some(event => event.kind === "model_response_received"), false);
  assert.equal(f.clock.pending.size, 0);
});

test("original host expiry reaches actual bridge observation and transport stop after a late spawn", async t => {
  const f = await fixture(t), modelClock = new Clock(), cancellation = new AbortController();
  modelClock.monotonic = 123456;
  const child = new EventEmitter(), signals = [];
  child.pid = 12345;
  child.stdout = new EventEmitter();
  child.stdout.setEncoding = () => {};
  child.kill = signal => {
    signals.push(signal);
    queueMicrotask(() => { child.emit("exit", 0, null); child.emit("close", 0, null); });
    return true;
  };
  let completed, observed, started;
  const modelStarted = new Promise(resolve => { started = resolve; });
  f.bridge.completion = new Promise(resolve => { completed = resolve; });
  f.bridge.child = { pid: 23456, kill: signal => {
    assert.equal(signal, "SIGTERM");
    cancellation.abort();
    return true;
  } };
  f.transport.bridges.set(context.arm, f.bridge);
  f.transport.stop = CopilotTransport.prototype.stop;
  f.transport.bridge = async () => { f.clock.tick(60000); return f.bridge; };
  const failure = { code: null }, link = fs.link;
  t.mock.method(Date, "now", () => f.clock.wall);
  t.mock.method(fs, "link", async (...args) => {
    await link(...args);
    if (args[1] !== f.request) return;
    observed = observeCopilotProcess([], {}, {
      timingProfile: MAINTENANCE_TIMING_PROFILE, spawnProcess: () => child,
      timers: modelClock, now: () => modelClock.monotonic, signal: cancellation.signal, failure,
    }).then(result => {
      f.bridge.exited = true;
      f.bridge.exitCode = result.observed.exit_code;
      completed();
      return result.observed;
    });
    child.emit("spawn");
    started();
  });
  const outcome = f.invoke();
  await modelStarted;
  await published(f.request);
  f.clock.wall -= 600000;
  modelClock.tick(239999);
  f.clock.tick(239999);
  assert.deepEqual(signals, []);
  modelClock.tick(1);
  f.clock.tick(1);
  assert.deepEqual(signals, ["SIGTERM"]);
  const result = await outcome, receipt = await observed;
  assert.equal(result.error.code, "response_deadline");
  assert.equal(failure.code, "copilot_interrupted");
  assert.equal(receipt.elapsed_ms, 240000);
  assert.equal(receipt.exit_code, 0);
  assert.equal(receipt.close_observed, true);
  assert.equal(f.ledger.stopped, true);
  assert.equal(f.events.filter(event => event.kind === "bridge_stopped").length, 1);
  assert.equal(f.events.find(event => event.kind === "bridge_stopped").acknowledged, true);
  assert.equal(f.clock.pending.size, 0);
  assert.equal(modelClock.pending.size, 0);
});

test("expiry during a stalled stage cancels immediately and cannot publish after the stage completes", async t => {
  const f = await fixture(t), open = fs.open;
  let release, staging;
  const stageStarted = new Promise(resolve => { staging = resolve; });
  const blocked = new Promise(resolve => { release = resolve; });
  t.mock.method(fs, "open", async (...args) => {
    const handle = await open(...args);
    if (args[0] === `${f.request}.writing`) {
      const sync = handle.sync.bind(handle);
      t.mock.method(handle, "sync", async () => {
        staging();
        await blocked;
        return sync();
      });
    }
    return handle;
  });
  const outcome = f.invoke();
  await stageStarted;
  f.clock.tick(300000);
  assert.equal(f.stops.length, 1);
  assert.equal(f.ledger.stopped, true);
  assert.equal(existsSync(f.request), false);
  release();
  assert.equal((await outcome).error.code, "response_deadline");
  assert.equal(existsSync(f.request), false);
  assert.equal(existsSync(`${f.request}.writing`), true);
  assert.equal(f.stops.length, 1);
  assert.equal(f.clock.pending.size, 0);
});

for (const phase of ["staging", "caller guard", "response recording"]) {
  test(`expiry during ${phase} cannot reset the window or return success`, async t => {
    const f = await fixture(t);
    await writeNew(f.responseFile, response);
    let options;
    if (phase === "staging") {
      const open = fs.open;
      t.mock.method(fs, "open", async (...args) => {
        const handle = await open(...args);
        if (args[0] === `${f.request}.writing`) {
          const sync = handle.sync.bind(handle);
          t.mock.method(handle, "sync", async () => { await sync(); f.clock.jump(300000); });
        }
        return handle;
      });
    } else if (phase === "caller guard") {
      options = { beforePublish: () => f.clock.jump(300000) };
    } else {
      f.transport.record = event => {
        f.events.push(event);
        if (event.kind === "model_response_received") f.clock.jump(300000);
      };
    }
    const { error } = await f.invoke(options);
    assert.equal(error.code, phase === "response recording" ? "late_model_response" : "response_deadline");
    assert.equal(f.ledger.ordinal, 1);
    assert.equal(f.ledger.stopped, true);
    assert.equal(f.stops.length, 1);
    assert.equal(existsSync(f.request), phase === "response recording");
    assert.equal(existsSync(`${f.request}.writing`), phase !== "response recording");
    assert.equal(f.clock.pending.size, 0);
  });
}

test("staging consumes the original window but does not demand a new pre-invoke reserve", async t => {
  const f = await fixture(t);
  await writeNew(f.responseFile, response);
  const { error, value } = await f.invoke({ beforePublish: () => f.clock.jump(60000) });
  assert.equal(error, null);
  assert.equal(value.text, response.content);
  assert.equal(f.ledger.ordinal, 1);
  assert.equal(f.clock.pending.size, 0);
});

for (const options of [
  clock => ({ deadline: clock.wall + 120000 }),
  clock => ({ monotonicDeadline: clock.monotonic + 120000 }),
]) {
  test("an earlier caller deadline shortens active waiting without rebasing it", async t => {
    const f = await fixture(t);
    const outcome = f.invoke(options(f.clock));
    await published(f.request);
    f.clock.tick(119999);
    assert.equal(f.stops.length, 0);
    f.clock.tick(1);
    const { error } = await outcome;
    assert.equal(error.code, "response_deadline");
    assert.equal(f.stops[0].monotonic, 120000);
    assert.equal(f.clock.pending.size, 0);
  });
}

test("caller cancellation retains an earlier cleanup deadline and runs cleanup only once", async t => {
  const f = await fixture(t), cancellation = new AbortController();
  const failure = new EvaluationError("run_deadline");
  failure.cleanupDeadline = f.clock.wall + 5000;
  const outcome = f.invoke({ signal: cancellation.signal });
  await published(f.request);
  cancellation.abort(failure);
  assert.equal(f.stops.length, 1);
  assert.equal(f.stops[0].deadline, failure.cleanupDeadline);
  const { error } = await outcome;
  assert.equal(error, failure);
  assert.equal(f.ledger.stopped, true);
  assert.equal(f.stops.length, 1);
  assert.equal(f.clock.pending.size, 0);
  f.clock.tick(300000);
  assert.equal(f.stops.length, 1);
});

test("an unacknowledged stop preserves the response failure, unknown usage and first cleanup cutoff", async t => {
  const f = await fixture(t);
  f.transport.stop = async (_arm, options) => {
    f.stops.push(options);
    f.clock.jump(1000);
    throw new EvaluationError("bridge_termination_unacknowledged");
  };
  const outcome = f.invoke();
  await published(f.request);
  f.clock.tick(300000);
  const { error } = await outcome;
  assert.equal(error.code, "response_deadline");
  assert.equal(error.usage, null);
  assert.equal(error.cleanup_failed, true);
  assert.equal(error.cleanupDeadline, f.stops[0].deadline);
  assert.deepEqual(error.cleanup_failures, [{ code: "bridge_termination_unacknowledged" }]);
  assert.equal(f.stops.length, 1);
  assert.equal(f.clock.pending.size, 0);
});

test("opt-in rejects asynchronous publication guards without an unhandled rejection", async t => {
  const f = await fixture(t);
  const { error } = await f.invoke({ beforePublish: () => {
    f.clock.jump(300000);
    return Promise.reject(new Error("synthetic rejected guard"));
  } });
  assert.equal(error.code, "asynchronous_publication_guard");
  assert.equal(existsSync(f.request), false);
  assert.equal(f.ledger.stopped, true);
  assert.equal(f.clock.pending.size, 0);
});

test("invalid or already expired opt-in deadlines never reach bridge startup", async t => {
  const f = await fixture(t);
  f.transport.bridge = async () => assert.fail("expired or invalid admission reached bridge");
  for (const options of [
    { deadline: NaN }, { monotonicDeadline: "300000" }, { deadline: -Infinity },
  ]) {
    assert.equal((await f.invoke(options)).error.code, "invalid_deadline");
  }
  for (const options of [
    { deadline: f.clock.wall }, { monotonicDeadline: f.clock.monotonic },
  ]) {
    assert.equal((await f.invoke(options)).error.code, "response_deadline");
  }
  assert.equal(f.ledger.ordinal, 0);
  assert.equal(f.stops.length, 0);
  assert.equal(f.clock.pending.size, 0);
});
