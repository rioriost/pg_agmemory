import { spawn, spawnSync } from "node:child_process";
import { constants } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";

const MAX_PROMPT_BYTES = 65536;
const MAX_EVENT_BYTES = 8 * 1024 * 1024;
const EFFORTS = new Set(["low", "medium", "high", "xhigh"]);

export function parseArguments(argv) {
  const fields = new Map();
  for (let i = 0; i < argv.length; i += 2) {
    if (!["--directory", "--model", "--reasoning-effort", "--max-calls", "--run-id"]
      .includes(argv[i]) || !argv[i + 1] || fields.has(argv[i])) {
      throw new Error("invalid_bridge_arguments");
    }
    fields.set(argv[i], argv[i + 1]);
  }
  const result = {
    directory: fields.get("--directory"),
    model: fields.get("--model"),
    reasoning_effort: fields.get("--reasoning-effort"),
    max_calls: Number(fields.get("--max-calls")),
    run_id: fields.get("--run-id"),
  };
  if (!result.directory || !path.isAbsolute(result.directory)
      || !/^[a-z0-9][a-z0-9._-]{0,99}$/.test(result.model ?? "")
      || !EFFORTS.has(result.reasoning_effort)
      || !Number.isSafeInteger(result.max_calls) || result.max_calls < 1
      || result.max_calls > 160
      || !/^agent-eval-[a-z0-9]{8,32}$/.test(result.run_id ?? "")) {
    throw new Error("invalid_bridge_arguments");
  }
  return result;
}

export function validateRequest(value, callId) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join(",") !== "call_id,format,prompt"
      || value.format !== "pgag-copilot-request-v1" || value.call_id !== callId
      || typeof value.prompt !== "string" || !value.prompt.trim()
      || Buffer.byteLength(value.prompt) > MAX_PROMPT_BYTES) {
    throw new Error("invalid_bridge_request");
  }
  return value;
}

export function auditEvents(text, model, reasoningEffort = "high") {
  const events = text.trim().split("\n").map((line) => JSON.parse(line));
  const results = events.filter((event) => event.type === "result");
  if (results.length !== 1 || results[0].exitCode !== 0) {
    throw new Error("copilot_unsuccessful");
  }
  if (events.some((event) => event.type.startsWith("tool.execution")
      || (event.data?.toolRequests?.length ?? 0) > 0)) {
    throw new Error("copilot_tool_use_forbidden");
  }
  const observations = events.filter((event) => event.type === "session.usage_checkpoint")
    .flatMap((event) => event.data?.promptCacheBreakState ?? [])
    .flatMap((conversation) => Object.values(conversation.models ?? {}));
  if (!observations.length || observations.some((item) =>
    item.model !== model || item.tool_count !== 0 || item.reasoning_effort !== reasoningEffort)) {
    throw new Error("copilot_model_or_tool_audit_failed");
  }
  const messages = events.filter((event) => event.type === "assistant.message");
  const content = messages.at(-1)?.data?.content;
  if (typeof content !== "string" || !content.trim()
      || Buffer.byteLength(content) > MAX_PROMPT_BYTES) {
    throw new Error("copilot_response_invalid");
  }
  return content;
}

export function usageSummary(raw, model) {
  const entry = raw?.modelMetrics?.[model];
  if (raw?.currentModel !== model || !entry || !entry.usage) {
    throw new Error("copilot_usage_invalid");
  }
  const result = {
    input_tokens: entry.usage.inputTokens,
    output_tokens: entry.usage.outputTokens,
    cache_read_tokens: entry.usage.cacheReadTokens,
    cache_write_tokens: entry.usage.cacheWriteTokens,
    reasoning_tokens: entry.usage.reasoningTokens ?? null,
    api_requests: entry.requests?.count,
    premium_requests: entry.requests?.cost,
    nano_aiu: entry.totalNanoAiu ?? null,
    api_duration_ms: raw.totalApiDurationMs,
    monetary_cost_verified: false,
  };
  for (const [key, value] of Object.entries(result)) {
    if (key === "monetary_cost_verified" || value === null) continue;
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
      throw new Error("copilot_usage_invalid");
    }
  }
  return result;
}

async function privateDirectory(directory) {
  const stat = await fs.lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()
      || stat.uid !== process.getuid() || (stat.mode & 0o077) !== 0
      || await fs.realpath(directory) !== directory) {
    throw new Error("private_bridge_directory_required");
  }
}

async function readPrivate(file, maximum) {
  const handle = await fs.open(file, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.uid !== process.getuid()
        || (stat.mode & 0o077) !== 0 || stat.size > maximum) {
      throw new Error("invalid_private_bridge_file");
    }
    const buffer = Buffer.alloc(maximum + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead > maximum) throw new Error("bridge_file_too_large");
    return buffer.subarray(0, bytesRead).toString("utf8");
  } finally {
    await handle.close();
  }
}

async function writeNew(file, value) {
  const temporary = `${file}.writing`;
  const handle = await fs.open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify(value) + "\n");
    await handle.sync();
  } finally {
    await handle.close();
  }
  // link, unlike rename, never overwrites an existing response or prior run.
  await fs.link(temporary, file);
  await fs.unlink(temporary);
}

async function present(file) {
  try {
    await fs.lstat(file);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

let activeChild;
let interrupted = false;
async function invokeCopilot(config, callId, prompt) {
  const callDirectory = path.join(config.directory, `call-${callId}`);
  await fs.mkdir(callDirectory, { mode: 0o700 });
  const usageFile = path.join(callDirectory, "usage.json");
  const stdoutFile = await fs.open(path.join(callDirectory, "events.jsonl"), "wx", 0o600);
  const stderrFile = await fs.open(path.join(callDirectory, "stderr.log"), "wx", 0o600);
  const args = [
    "-C", callDirectory, "--model", config.model, "--reasoning-effort", config.reasoning_effort,
    "--no-custom-instructions", "--disable-builtin-mcps",
    "--available-tools=pgag_evaluation_no_tools", "--no-ask-user", "--no-auto-update",
    "--no-remote", "--no-remote-export", "--output-format", "json",
    "--usage-output-file", usageFile, "-p", prompt,
  ];
  let output = "";
  let bytes = 0;
  let failed;
  try {
    const result = await new Promise((resolve, reject) => {
      const child = spawn("copilot", args, {
        cwd: callDirectory, stdio: ["ignore", "pipe", stderrFile.fd],
        env: { ...process.env, COPILOT_ALLOW_ALL: "false" },
      });
      activeChild = child;
      let killTimer;
      const terminate = (reason) => {
        failed ??= reason;
        child.kill("SIGTERM");
        killTimer ??= setTimeout(() => child.kill("SIGKILL"), 2000);
      };
      const timer = setTimeout(() => terminate("copilot_timeout"), 150000);
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        bytes += Buffer.byteLength(chunk);
        if (bytes > MAX_EVENT_BYTES) {
          terminate("copilot_output_limit");
        } else {
          output += chunk;
        }
      });
      child.once("error", (error) => {
        clearTimeout(timer);
        clearTimeout(killTimer);
        reject(error);
      });
      child.once("close", (code) => {
        clearTimeout(timer);
        clearTimeout(killTimer);
        activeChild = undefined;
        resolve(code);
      });
    });
    await stdoutFile.writeFile(output);
    await stdoutFile.sync();
    if (interrupted) throw new Error("copilot_interrupted");
    if (failed || result !== 0) throw new Error(failed ?? "copilot_unsuccessful");
    const content = auditEvents(output, config.model, config.reasoning_effort);
    const usage = usageSummary(JSON.parse(await readPrivate(usageFile, MAX_EVENT_BYTES)), config.model);
    return { content, usage };
  } finally {
    await stdoutFile.close();
    await stderrFile.close();
  }
}

export async function main(argv) {
  process.umask(0o077);
  const config = parseArguments(argv);
  await privateDirectory(config.directory);
  if ((await fs.readdir(config.directory)).length) throw new Error("new_bridge_directory_required");
  const version = spawnSync("copilot", ["--version"], { encoding: "utf8", timeout: 10000 });
  if (version.status !== 0) throw new Error("copilot_unavailable");
  const versionMatch = version.stdout.match(/GitHub Copilot CLI ([0-9]+\.[0-9]+\.[0-9]+)/);
  if (!versionMatch) throw new Error("copilot_version_unavailable");
  await writeNew(path.join(config.directory, "transport.json"), {
    format: "pgag-copilot-transport-v1", run_id: config.run_id,
    model: config.model, reasoning_effort: config.reasoning_effort,
    cli_version: versionMatch[1], max_calls: config.max_calls,
    fresh_session_per_call: true, custom_instructions: false, tools_allowed: false,
    model_weights_revision_verified: false,
  });
  let completed = 0;
  let errors = 0;
  while (!interrupted && completed < config.max_calls) {
    if (await present(path.join(config.directory, "stop.json"))) break;
    const callId = String(completed + 1).padStart(6, "0");
    const requestFile = path.join(config.directory, "queue", `${callId}.request.json`);
    if (!await present(requestFile)) {
      await sleep(100);
      continue;
    }
    const started = performance.now();
    let content = null;
    let usage = null;
    let error = null;
    try {
      const request = validateRequest(JSON.parse(await readPrivate(requestFile, 70000)), callId);
      ({ content, usage } = await invokeCopilot(config, callId, request.prompt));
    } catch (failure) {
      const safe = new Set([
        "invalid_bridge_request", "invalid_private_bridge_file", "bridge_file_too_large",
        "copilot_unsuccessful", "copilot_tool_use_forbidden", "copilot_model_or_tool_audit_failed",
        "copilot_response_invalid", "copilot_usage_invalid", "copilot_timeout",
        "copilot_output_limit", "copilot_interrupted",
      ]);
      error = safe.has(failure.message) ? failure.message : "copilot_transport_failed";
      errors += 1;
    }
    await writeNew(path.join(config.directory, "queue", `${callId}.response.json`), {
      format: "pgag-copilot-response-v1", call_id: callId, status: error ? "error" : "ok",
      content, error, duration_seconds: (performance.now() - started) / 1000,
      model: config.model, reasoning_effort: config.reasoning_effort, usage,
    });
    completed += 1;
  }
  await writeNew(path.join(config.directory, "bridge-summary.json"), {
    run_id: config.run_id, calls: completed, errors, interrupted,
    automatic_retry: false, model_weights_revision_verified: false,
  });
  if (interrupted) process.exitCode = 130;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      interrupted = true;
      const child = activeChild;
      if (child) {
        child.kill("SIGTERM");
        setTimeout(() => child.kill("SIGKILL"), 2000).unref();
      }
    });
  }
  main(process.argv.slice(2)).catch(() => {
    console.error("copilot_bridge_failed");
    process.exitCode = 1;
  });
}
