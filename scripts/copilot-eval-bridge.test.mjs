import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import test from "node:test";
import { auditEvents, parseArguments, usageSummary, validateRequest } from "./copilot-eval-bridge.mjs";

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
  { timeout: 60000 }, async () => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), "pgag-bridge-test-"));
    const canonical = await fs.realpath(directory);
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
      const extra = `${String(ceiling + 1).padStart(6, "0")}.response.json`;
      await assert.rejects(fs.stat(path.join(bridge, "queue", extra)), { code: "ENOENT" });
    } finally {
      if (child.exitCode === null) {
        child.kill("SIGTERM");
        await exited;
      }
      await fs.rm(canonical, { recursive: true });
    }
  });
}
