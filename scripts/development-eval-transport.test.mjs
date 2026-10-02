import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { EvaluationError, InvocationLedger } from "./development-eval-protocol.mjs";
import { MAINTENANCE_TIMING_PROFILE } from "./development-eval-timing.mjs";
import {
  CopilotTransport, Journal, privateDirectory, readPrivate, waitPrivate, writeNew,
} from "./development-eval-transport.mjs";

async function temporary() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "pgag-dev-transport-test-"));
  return fs.realpath(directory);
}

test("private evidence files reject overwrites, symlinks and broad permissions", async () => {
  const directory = await temporary();
  try {
    await privateDirectory(directory);
    const file = path.join(directory, "record.json");
    await writeNew(file, { value: 1 });
    await assert.rejects(writeNew(file, { value: 2 }), { code: "EEXIST" });
    assert.equal(JSON.parse(await readPrivate(file)).value, 1);
    await fs.symlink(file, path.join(directory, "link.json"));
    await assert.rejects(readPrivate(path.join(directory, "link.json")));
    await fs.chmod(file, 0o644);
    await assert.rejects(readPrivate(file), /private_file_invalid/);
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("publication guard runs synchronously after staged fsync and close, before link", async (t) => {
  const directory = await temporary();
  const file = path.join(directory, "guarded.json");
  const stages = [];
  const open = fs.open, link = fs.link;
  t.mock.method(fs, "open", async (...args) => {
    const handle = await open(...args);
    if (args[0] === `${file}.writing`) {
      const sync = handle.sync.bind(handle), close = handle.close.bind(handle);
      t.mock.method(handle, "sync", async () => { await sync(); stages.push("synced"); });
      t.mock.method(handle, "close", async () => { await close(); stages.push("closed"); });
    }
    return handle;
  });
  t.mock.method(fs, "link", (...args) => {
    stages.push("link");
    return link(...args);
  });
  try {
    await writeNew(file, { value: 1 }, { beforePublish: () => {
      stages.push("guard");
      assert.equal(readFileSync(`${file}.writing`, "utf8"), '{"value":1}\n');
      assert.equal(existsSync(file), false);
      queueMicrotask(() => stages.push("microtask"));
    } });
    assert.deepEqual(stages, ["synced", "closed", "guard", "link", "microtask"]);
    assert.equal((await readPrivate(file)).toString(), '{"value":1}\n');
    assert.equal(existsSync(`${file}.writing`), false);
  } finally { await fs.rm(directory, { recursive: true }); }
});

for (const code of ["response_deadline", "operation_cancelled", "process_evidence_invalid"]) {
  test(`publication guard rejects ${code} observed during staged I/O`, async (t) => {
    const directory = await temporary();
    const file = path.join(directory, "guarded.json");
    const failure = new EvaluationError(code);
    const open = fs.open;
    let stopped = false;
    let guarded = false;
    t.mock.method(fs, "open", async (...args) => {
      const handle = await open(...args);
      if (args[0] === `${file}.writing`) {
        const sync = handle.sync.bind(handle);
        t.mock.method(handle, "sync", async () => { await sync(); stopped = true; });
      }
      return handle;
    });
    const link = t.mock.method(fs, "link", () => assert.fail("denied request was linked"));
    try {
      await assert.rejects(writeNew(file, { value: 1 }, { beforePublish: () => {
        guarded = true;
        if (stopped) throw failure;
      } }), (error) => error === failure);
      assert.equal(guarded, true);
      assert.equal(link.mock.callCount(), 0);
      assert.equal(existsSync(file), false);
      assert.equal((await readPrivate(`${file}.writing`, 1024, { singleLink: true })).toString(),
        '{"value":1}\n');
    } finally { await fs.rm(directory, { recursive: true }); }
  });
}

for (const [name, beforePublish] of [
  ["async callback", async () => {}],
  ["rejected promise", () => Promise.reject(new Error("unsupported async failure"))],
  ["thenable", () => ({ then: (resolve) => resolve() })],
]) {
  test(`publication guard rejects ${name} without publishing`, async () => {
    const directory = await temporary();
    const file = path.join(directory, "guarded.json");
    try {
      await assert.rejects(writeNew(file, { value: 1 }, { beforePublish }),
        /asynchronous_publication_guard/);
      assert.equal(existsSync(file), false);
      assert.equal((await readPrivate(`${file}.writing`)).toString(), '{"value":1}\n');
    } finally { await fs.rm(directory, { recursive: true }); }
  });
}

test("invalid publication guard is rejected before creating a staged file", async () => {
  const directory = await temporary();
  try {
    const file = path.join(directory, "guarded.json");
    await assert.rejects(writeNew(file, {}, { beforePublish: false }), /invalid_publication_guard/);
    assert.deepEqual(await fs.readdir(directory), []);
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("staging and link failures retain their own error and publication evidence", async (t) => {
  const directory = await temporary();
  const failure = Object.assign(new Error("synthetic staging failure"), { code: "EIO" });
  const file = path.join(directory, "staging.json");
  const open = fs.open;
  t.mock.method(fs, "open", async (...args) => {
    const handle = await open(...args);
    if (args[0] === `${file}.writing`) {
      t.mock.method(handle, "sync", async () => { throw failure; });
    }
    return handle;
  });
  try {
    await assert.rejects(writeNew(file, {}, {
      beforePublish: () => assert.fail("guard called after staging failure"),
    }), (error) => error === failure);
    assert.equal(existsSync(file), false);
    assert.equal((await readPrivate(`${file}.writing`)).toString(), "{}\n");
    const existing = path.join(directory, "existing.json");
    await writeNew(existing, { original: true });
    let calls = 0;
    await assert.rejects(writeNew(existing, { replacement: true }, {
      beforePublish: () => { calls++; },
    }), { code: "EEXIST" });
    assert.equal(calls, 1);
    assert.equal((await readPrivate(existing)).toString(), '{"original":true}\n');
    assert.equal((await readPrivate(`${existing}.writing`)).toString(), '{"replacement":true}\n');
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("post-link failure does not misrepresent a published request as absent", async (t) => {
  const directory = await temporary();
  const file = path.join(directory, "published.json");
  const failure = Object.assign(new Error("synthetic unlink failure"), { code: "EIO" });
  const unlink = fs.unlink;
  t.mock.method(fs, "unlink", async (target) => {
    if (target === `${file}.writing`) throw failure;
    return unlink(target);
  });
  try {
    await assert.rejects(writeNew(file, { value: 1 }, { beforePublish: () => {} }),
      (error) => error === failure);
    assert.equal((await readPrivate(file)).toString(), '{"value":1}\n');
    assert.equal((await fs.stat(file)).ino, (await fs.stat(`${file}.writing`)).ino);
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("uncertain link completion retains both names after a passing guard", async (t) => {
  const directory = await temporary();
  const file = path.join(directory, "uncertain.json");
  const failure = Object.assign(new Error("synthetic link acknowledgement failure"), { code: "EIO" });
  const link = fs.link;
  let guarded = false;
  t.mock.method(fs, "link", async (...args) => {
    assert.equal(guarded, true);
    await link(...args);
    throw failure;
  });
  try {
    await assert.rejects(writeNew(file, { value: 1 }, {
      beforePublish: () => { guarded = true; },
    }), (error) => error === failure);
    assert.equal((await readPrivate(file)).toString(), '{"value":1}\n');
    assert.equal((await fs.stat(file)).ino, (await fs.stat(`${file}.writing`)).ino);
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("transport guard rejection retains the consumed reservation and never publishes", async () => {
  const directory = await temporary();
  const events = [];
  const record = (event) => events.push(event);
  const ledger = new InvocationLedger({ record, runId: "unit", model: "gpt-6-astra", effort: "high" });
  const transport = new CopilotTransport({
    directory, runId: "unit", model: "gpt-6-astra", effort: "high", ledger, record,
  });
  const failure = new EvaluationError("operation_cancelled");
  const context = {
    arm: "no_memory", sessionId: "unit", slotId: "unit", phase: "work", prompt: "unit", sequence: 1,
  };
  const stopped = [];
  try {
    await privateDirectory(path.join(directory, "queue"), { create: true });
    transport.bridge = async () => ({ directory, exited: false });
    transport.stop = async (_arm, options) => { stopped.push(options); };
    await assert.rejects(transport.invoke(context, { beforePublish: () => { throw failure; } }),
      (error) => {
        assert.equal(error, failure);
        assert.equal(error.receipt_ref.global_ordinal, 1);
        assert.equal(error.usage, null);
        return true;
      });
    assert.equal(ledger.ordinal, 1);
    assert.equal(ledger.stopped, true);
    assert.equal(stopped.length, 1);
    assert.equal(stopped[0].cancel, true);
    assert.equal(events.filter((event) => event.kind === "model_admitted").length, 1);
    assert.equal(events.find((event) => event.kind === "model_invocation_failed").code,
      "operation_cancelled");
    const request = path.join(directory, "queue", "000001.request.json");
    assert.equal(existsSync(request), false);
    assert.ok((await readPrivate(`${request}.writing`)).length > 0);
    assert.throws(() => ledger.reserve({ ...context, sequence: 2 }), /admission_stopped/);
    assert.equal(ledger.ordinal, 1);
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("journal durably orders records and cannot append after close", async () => {
  const directory = await temporary();
  try {
    const file = path.join(directory, "events.jsonl");
    const journal = new Journal(file);
    journal.record({ kind: "first", host_event_sequence: 99 });
    journal.record({ kind: "second" });
    journal.close();
    assert.throws(() => journal.record({ kind: "late" }), /journal_closed/);
    const rows = (await readPrivate(file)).toString().trim().split("\n").map(JSON.parse);
    assert.deepEqual(rows.map((row) => row.host_event_sequence), [1, 2]);
    assert.throws(() => new Journal(file), { code: "EEXIST" });
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("waiters bind cancellation and process death rather than silently waiting forever", async () => {
  const directory = await temporary();
  try {
    const file = path.join(directory, "absent.json");
    await assert.rejects(waitPrivate(file, { deadline: Date.now() + 1000, alive: () => false }),
      /transport_process_exited/);
    const abort = new AbortController();
    abort.abort();
    await assert.rejects(waitPrivate(file, {
      deadline: Date.now() + 1000, signal: abort.signal,
    }), /operation_cancelled/);
    await assert.rejects(waitPrivate(file, { deadline: Date.now() - 1 }), /response_deadline/);
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("secondary bridge cleanup failure preserves the original admitted error and receipt", async () => {
  const directory = await temporary();
  const events = [];
  const record = (event) => events.push(event);
  const ledger = new InvocationLedger({ record, runId: "unit", model: "gpt-6-astra", effort: "high" });
  const transport = new CopilotTransport({
    directory, runId: "unit", model: "gpt-6-astra", effort: "high", ledger, record,
  });
  const usage = { input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0,
    reasoning_tokens: null, api_requests: 1, premium_requests: 0, nano_aiu: null,
    api_duration_ms: 1, monetary_cost_verified: false };
  try {
    await privateDirectory(path.join(directory, "queue"), { create: true });
    await writeNew(path.join(directory, "queue", "000001.response.json"), {
      format: "pgag-copilot-response-v1", call_id: "000001", status: "error", content: "",
      error: "copilot_timeout", duration_seconds: 1,
      model: "gpt-6-astra", reasoning_effort: "high", usage,
    });
    transport.bridge = async () => ({ directory, exited: false });
    let deadline;
    transport.stop = async (_arm, options) => {
      deadline = options.deadline;
      throw new EvaluationError("bridge_termination_unacknowledged");
    };
    await assert.rejects(transport.invoke({
      arm: "no_memory", sessionId: "unit", slotId: "unit", phase: "work", prompt: "unit", sequence: 1,
    }), (error) => {
      assert.equal(error.code, "model_response_failed");
      assert.equal(error.bridge_error, "copilot_timeout");
      assert.equal(error.receipt_ref.global_ordinal, 1);
      assert.deepEqual({ ...error.usage }, usage);
      assert.equal(error.cleanup_failed, true);
      assert.equal(error.cleanupDeadline, deadline);
      assert.deepEqual(error.cleanup_failures, [{ code: "bridge_termination_unacknowledged" }]);
      return true;
    });
    assert.equal(ledger.stopped, true);
    assert.equal(ledger.ordinal, 1);
    assert.equal(events.find((row) => row.kind === "model_response_received").error, "copilot_timeout");
    assert.equal(events.find((row) => row.kind === "model_invocation_failed").bridge_error,
      "copilot_timeout");
  } finally { await fs.rm(directory, { recursive: true }); }
});

const meteredUsage = {
  input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0,
  reasoning_tokens: null, api_requests: 1, premium_requests: 0, nano_aiu: null,
  api_duration_ms: 1, monetary_cost_verified: false,
};
const unsafeDiagnostic = "UNTRUSTED_DIAGNOSTIC_SECRET_MUST_NOT_ESCAPE";

for (const [name, change, primary, bridgeError, received] of [
  ["unmetered timeout", { error: "copilot_timeout", usage: null },
    "failed_transport_accounting", "copilot_timeout", true],
  ["metered timeout", { error: "copilot_timeout" }, "model_response_failed", "copilot_timeout", true],
  ["generic bridge failure", { error: "copilot_transport_failed" },
    "model_response_failed", "copilot_transport_failed", true],
  ["unknown string", { error: unsafeDiagnostic }, "model_response_failed", null, true],
  ["unknown object", { error: { message: unsafeDiagnostic } }, "model_response_failed", null, true],
  ["unknown number", { error: 42 }, "model_response_failed", null, true],
  ["null error", { error: null }, "model_response_failed", null, true],
  ["wrong identity", { call_id: "000099", error: "copilot_timeout" },
    "model_response_identity_mismatch", null, false],
  ["wrong model", { model: "other", error: "copilot_timeout" },
    "model_response_identity_mismatch", null, false],
  ["wrong format", { format: "unknown", error: "copilot_timeout" },
    "model_response_identity_mismatch", null, false],
  ["wrong effort", { reasoning_effort: "low", error: "copilot_timeout" },
    "model_response_identity_mismatch", null, false],
  ["missing field", { content: undefined, error: "copilot_timeout" },
    "unexpected_fields", null, false],
  ["extra field", { extra: unsafeDiagnostic, error: "copilot_timeout" },
    "unexpected_fields", null, false],
  ["forged ok error", { status: "ok", error: "copilot_timeout" },
    "model_response_failed", null, true],
  ["invalid status", { status: "unknown", error: "copilot_timeout" },
    "model_response_failed", null, true],
  ["successful response", { status: "ok", error: null }, null, null, true],
]) {
  test(`bridge diagnostic: ${name} preserves admission and primary failure`, async () => {
    const directory = await temporary();
    const events = [], stops = [];
    const record = (event) => events.push(event);
    const ledger = new InvocationLedger({
      record, runId: "unit", model: "gpt-6-astra", effort: "high",
    });
    const transport = new CopilotTransport({
      directory, runId: "unit", model: "gpt-6-astra", effort: "high", ledger, record,
    });
    const response = {
      format: "pgag-copilot-response-v1", call_id: "000001", status: "error",
      content: '{"final":"done"}', error: null, duration_seconds: 1,
      model: "gpt-6-astra", reasoning_effort: "high", usage: meteredUsage, ...change,
    };
    try {
      await privateDirectory(path.join(directory, "queue"), { create: true });
      await writeNew(path.join(directory, "queue", "000001.response.json"), response);
      transport.bridge = async () => ({ directory, exited: false });
      transport.stop = async (_arm, options) => { stops.push(options); };
      const invocation = transport.invoke({
        arm: "no_memory", sessionId: "unit", slotId: "unit", phase: "work",
        prompt: "synthetic diagnostic", sequence: 1,
      });
      const receipt = { global_ordinal: 1, bridge_id: "unit-no_memory", bridge_call_id: "000001" };
      if (primary === null) {
        const result = await invocation;
        assert.deepEqual(result.receipt_ref, receipt);
        assert.equal(result.text, response.content);
        assert.deepEqual({ ...result.usage }, meteredUsage);
        assert.equal(ledger.stopped, false);
        assert.equal(stops.length, 0);
      } else {
        await assert.rejects(invocation, (error) => {
          assert.equal(error.code, primary);
          assert.equal(error.bridge_error, bridgeError);
          assert.deepEqual(error.receipt_ref, receipt);
          assert.deepEqual(error.usage === null ? null : { ...error.usage },
            received ? response.usage : null);
          return true;
        });
        const failed = events.find((event) => event.kind === "model_invocation_failed");
        assert.equal(failed.code, primary);
        assert.equal(failed.bridge_error, bridgeError);
        assert.deepEqual(failed.receipt, receipt);
        assert.equal(failed.usage_unknown, !received || response.usage === null);
        assert.equal(ledger.stopped, true);
        assert.equal(stops.length, 1);
        assert.equal(stops[0].cancel, true);
      }
      const returned = events.filter((event) => event.kind === "model_response_received");
      assert.equal(returned.length, received ? 1 : 0);
      if (received) assert.equal(returned[0].error, bridgeError);
      assert.equal(ledger.ordinal, 1);
      assert.equal(events.filter((event) => event.kind === "model_admitted").length, 1);
      assert.ok(!JSON.stringify(events).includes(unsafeDiagnostic));
    } finally { await fs.rm(directory, { recursive: true }); }
  });
}

for (const timingProfile of [undefined, MAINTENANCE_TIMING_PROFILE]) {
test(`real ${timingProfile ?? "legacy"} bridge wiring preserves IDs and stops on multi-request usage`, {
  timeout: 30000,
}, async () => {
  const directory = await temporary();
  const oldPath = process.env.PATH;
  const transports = [];
  try {
    const bin = path.join(directory, "bin");
    await fs.mkdir(bin, { mode: 0o700 });
    await fs.writeFile(path.join(bin, "copilot"), `#!${process.execPath}
const fs = require("node:fs");
const args = process.argv.slice(2);
if (args.includes("--version")) { console.log("GitHub Copilot CLI 1.0.88."); }
else {
  const model = args[args.indexOf("--model")+1];
  const effort = args[args.indexOf("--reasoning-effort")+1];
  const prompt = args[args.indexOf("-p")+1];
  const count = prompt === "multi" ? 2 : 1;
  fs.writeFileSync(args[args.indexOf("--usage-output-file")+1], JSON.stringify({
    currentModel:model,totalApiDurationMs:1,
    modelMetrics:{[model]:{requests:{count,cost:count},
      usage:{inputTokens:10,outputTokens:5,cacheReadTokens:0,cacheWriteTokens:0}}}
  }), {mode:0o600});
  for (const event of [
    {type:"session.usage_checkpoint",data:{promptCacheBreakState:[
      {models:{[model]:{model,tool_count:0,reasoning_effort:effort}}}]}},
    {type:"assistant.message",data:{content:'{"final":"test"}'}},
    {type:"result",exitCode:0}
  ]) console.log(JSON.stringify(event));
}
`, { mode: 0o700 });
    process.env.PATH = `${bin}:${oldPath}`;
    const events = [];
    const record = (event) => events.push(event);
    const ledger = new InvocationLedger({ record, runId: "test-run",
      model: "gpt-6-astra", effort: "high" });
    const transport = new CopilotTransport({ directory, runId: "test-run",
      model: "gpt-6-astra", effort: "high", ledger, record, timingProfile });
    transports.push(transport);
    const context = { arm: "no_memory", sessionId: "first", slotId: "p1-1-no_memory",
      phase: "work", prompt: "normal", sequence: 1 };
    const first = await transport.invoke(context);
    assert.equal(first.text, '{"final":"test"}');
    const metadata = events.find(row => row.kind === "bridge_ready").transport;
    assert.equal(metadata.timing_profile, timingProfile);
    assert.equal(metadata.model_timeout_ms, timingProfile === undefined ? undefined : 270000);
    assert.equal(metadata.response_timeout_ms, timingProfile === undefined ? undefined : 300000);
    const processReceipt = JSON.parse(await readPrivate(
      path.join(transport.bridges.get("no_memory").directory, "call-000001", "process.json")));
    assert.equal(processReceipt.timing_profile, timingProfile);
    assert.equal(processReceipt.model_timeout_ms, timingProfile === undefined ? undefined : 270000);
    const second = await transport.invoke({ ...context, sessionId: "second", sequence: 2 });
    assert.equal(second.receipt_ref.bridge_call_id, "000002");
    assert.equal(second.receipt_ref.global_ordinal, 2);
    await assert.rejects(transport.invoke({ ...context, sequence: 3, prompt: "multi" }),
      /failed_transport_accounting/);
    assert.equal(ledger.stopped, true);
    assert.equal(ledger.ordinal, 3);
    assert.equal(events.filter((row) => row.kind === "model_response_received").at(-1)
      .usage.api_requests, 2);
    assert.equal(events.filter((row) => row.kind === "model_invocation_failed").at(-1)
      .usage_unknown, false);
    assert.equal(transport.bridges.get("no_memory").exited, true);
  } finally {
    for (const transport of transports) await transport.close();
    process.env.PATH = oldPath;
    await fs.rm(directory, { recursive: true });
  }
});
}
