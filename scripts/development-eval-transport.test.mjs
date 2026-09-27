import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { EvaluationError, InvocationLedger } from "./development-eval-protocol.mjs";
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
  const record = () => {};
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
      error: "synthetic_provider_failure", duration_seconds: 1,
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
      assert.equal(error.receipt_ref.global_ordinal, 1);
      assert.deepEqual({ ...error.usage }, usage);
      assert.equal(error.cleanup_failed, true);
      assert.equal(error.cleanupDeadline, deadline);
      assert.deepEqual(error.cleanup_failures, [{ code: "bridge_termination_unacknowledged" }]);
      return true;
    });
    assert.equal(ledger.stopped, true);
    assert.equal(ledger.ordinal, 1);
  } finally { await fs.rm(directory, { recursive: true }); }
});

test("real bridge wiring uses fake Copilot, preserves IDs and stops on multi-request usage", {
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
      model: "gpt-6-astra", effort: "high", ledger, record });
    transports.push(transport);
    const context = { arm: "no_memory", sessionId: "first", slotId: "p1-1-no_memory",
      phase: "work", prompt: "normal", sequence: 1 };
    const first = await transport.invoke(context);
    assert.equal(first.text, '{"final":"test"}');
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
