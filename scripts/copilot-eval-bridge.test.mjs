import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import { auditEvents, parseArguments, usageSummary, validateRequest } from "./copilot-eval-bridge.mjs";

const model = "gpt-6-astra";
function events(changes = {}) {
  return [
    { type: "session.usage_checkpoint", data: { promptCacheBreakState: [
      { models: { [model]: { model, tool_count: 0, ...changes } } },
    ] } },
    { type: "assistant.message", data: { content: '{"answer":"synthetic"}' } },
    { type: "result", exitCode: 0 },
  ].map((value) => JSON.stringify(value)).join("\n");
}

test("arguments require an explicit model, bounded calls and owned run identity", () => {
  const args = ["--directory", "/private/example", "--model", model, "--reasoning-effort", "high",
    "--max-calls", "100", "--run-id", "agent-eval-0123456789abcdef"];
  assert.equal(parseArguments(args).max_calls, 100);
  assert.throws(() => parseArguments(args.concat("--model", "other")));
  assert.throws(() => parseArguments(args.map((value) => value === "100" ? "101" : value)));
  assert.throws(() => parseArguments(args.map((value) => value === "high" ? "auto" : value)));
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
