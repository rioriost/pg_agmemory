import { spawn, spawnSync } from "node:child_process";
import { constants } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";

const MAX_PROMPT_BYTES = 65536;
const MAX_EVENT_BYTES = 8 * 1024 * 1024;
const EFFORTS = new Set(["low", "medium", "high", "xhigh"]);
const MODEL_TIMEOUT_MS = 150000;
const KILL_GRACE_MS = 2000;
const CLOSE_ACK_GRACE_MS = 2000;
const PROCESS_ERRNOS = new Set([
  "EACCES", "EAGAIN", "EBADF", "ECHILD", "EINTR", "EINVAL", "EIO", "EMFILE", "ENFILE",
  "ENOENT", "ENOMEM", "ENOSPC", "ENOSYS", "ENOTDIR", "EPERM", "EPIPE", "ESRCH",
]);
const PROCESS_SIGNALS = new Set([
  "SIGABRT", "SIGALRM", "SIGBUS", "SIGCHLD", "SIGCONT", "SIGEMT", "SIGFPE", "SIGHUP",
  "SIGILL", "SIGINFO", "SIGINT", "SIGIO", "SIGIOT", "SIGKILL", "SIGPIPE", "SIGPOLL",
  "SIGPROF", "SIGPWR", "SIGQUIT", "SIGSEGV", "SIGSTOP", "SIGSYS", "SIGTERM", "SIGTRAP",
  "SIGTSTP", "SIGTTIN", "SIGTTOU", "SIGURG", "SIGUSR1", "SIGUSR2", "SIGVTALRM",
  "SIGWINCH", "SIGXCPU", "SIGXFSZ",
]);
const COPILOT_ERROR_CODES = new Set([
  "invalid_bridge_request", "invalid_private_bridge_file", "bridge_file_too_large",
  "copilot_unsuccessful", "copilot_tool_use_forbidden", "copilot_model_or_tool_audit_failed",
  "copilot_response_invalid", "copilot_usage_invalid", "copilot_timeout",
  "copilot_output_limit", "copilot_interrupted", "copilot_transport_failed",
  "copilot_process_receipt_failed", "copilot_process_io_failed", "copilot_close_unacknowledged",
]);

export function safeCopilotErrorCode(value) {
  return typeof value === "string" && COPILOT_ERROR_CODES.has(value) ? value : null;
}

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

export async function writeNew(file, value) {
  const temporary = `${file}.writing`;
  const handle = await fs.open(temporary, "wx", 0o600);
  let failure;
  try {
    await handle.writeFile(JSON.stringify(value) + "\n");
    await handle.sync();
  } catch (error) {
    failure = error;
  }
  try { await handle.close(); }
  catch (error) { failure ??= error; }
  if (failure) throw failure;
  // link, unlike rename, never overwrites an existing response or prior run.
  await fs.link(temporary, file);
  await fs.unlink(temporary);
  const directory = await fs.open(path.dirname(file), constants.O_RDONLY);
  try { await directory.sync(); }
  finally { await directory.close(); }
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

let interrupted = false;
const bridgeCancellation = new AbortController();

function processObservation() {
  return {
    spawn_attempted: false, spawn_observed: false, pid: null,
    process_error: null, process_error_after_spawn: null, process_error_count: 0,
    exit_observed: false, exit_code: null, exit_signal: null,
    close_observed: false, close_code: null, close_signal: null,
    termination_requests: [], close_wait_expired: false,
    stdout_bytes_observed: 0, stdout_bytes_retained: 0, elapsed_ms: 0,
  };
}

const safeErrno = value => typeof value === "string" && PROCESS_ERRNOS.has(value) ? value : "process_error";
const exitCode = value => Number.isSafeInteger(value) ? value : null;
const exitSignal = value => value === null ? null : PROCESS_SIGNALS.has(value) ? value : "unknown_signal";

// Injection is for offline tests only; production callers cannot change deadlines via CLI or environment.
export function observeCopilotProcess(args, options, {
  spawnProcess = spawn, timers = globalThis, now = () => performance.now(),
  signal, failure = { code: null },
} = {}) {
  const observed = processObservation();
  const started = now();
  return new Promise((resolve) => {
    let child;
    let output = "";
    let settled = false;
    let stopping = false;
    let overflow = false;
    let modelTimer;
    let killTimer;
    let closeTimer;
    const elapsed = () => Math.max(0, now() - started);
    const latch = code => { failure.code ??= code; };
    const finish = () => {
      if (settled) return;
      settled = true;
      for (const timer of [modelTimer, killTimer, closeTimer]) timers.clearTimeout(timer);
      signal?.removeEventListener("abort", interrupt);
      observed.elapsed_ms = elapsed();
      resolve({ observed, output });
    };
    const requestSignal = requestedSignal => {
      if (settled || !child || observed.exit_observed || observed.pid === null) return;
      const request = { signal: requestedSignal, elapsed_ms: elapsed(), kill_return: null, error: null };
      observed.termination_requests.push(request);
      try {
        const result = child.kill(requestedSignal);
        request.kill_return = typeof result === "boolean" ? result : null;
      } catch (error) {
        request.error = safeErrno(error?.code);
      }
    };
    const stop = code => {
      if (settled) return;
      latch(code);
      if (stopping) return;
      stopping = true;
      // A successful kill() is only a delivery attempt, never an exit/close acknowledgement.
      killTimer = timers.setTimeout(() => requestSignal("SIGKILL"), KILL_GRACE_MS);
      closeTimer = timers.setTimeout(() => {
        if (settled) return;
        observed.close_wait_expired = true;
        finish();
        child?.stdout?.destroy();
        child?.unref();
      }, KILL_GRACE_MS + CLOSE_ACK_GRACE_MS);
      requestSignal("SIGTERM");
    };
    const interrupt = () => stop("copilot_interrupted");
    if (signal?.aborted) {
      latch("copilot_interrupted");
      finish();
      return;
    }
    signal?.addEventListener("abort", interrupt, { once: true });
    observed.spawn_attempted = true;
    try {
      child = spawnProcess("copilot", args, options);
    } catch (error) {
      observed.process_error = safeErrno(error?.code);
      observed.process_error_after_spawn = false;
      observed.process_error_count = 1;
      latch("copilot_transport_failed");
      finish();
      return;
    }
    observed.pid = Number.isSafeInteger(child.pid) && child.pid > 0 ? child.pid : null;
    modelTimer = timers.setTimeout(() => stop("copilot_timeout"), MODEL_TIMEOUT_MS);
    child.on("spawn", () => {
      if (settled) return;
      observed.spawn_observed = true;
      observed.pid = Number.isSafeInteger(child.pid) && child.pid > 0 ? child.pid : null;
    });
    child.on("error", error => {
      if (settled) return;
      observed.process_error_count += 1;
      if (observed.process_error === null) {
        observed.process_error = safeErrno(error?.code);
        observed.process_error_after_spawn = observed.spawn_observed;
      }
      stop("copilot_transport_failed");
    });
    child.on("exit", (code, exit) => {
      if (settled || observed.exit_observed) return;
      observed.exit_observed = true;
      observed.exit_code = exitCode(code);
      observed.exit_signal = exitSignal(exit);
      if (observed.exit_code !== 0 || observed.exit_signal !== null) stop("copilot_unsuccessful");
    });
    child.on("close", (code, closeSignal) => {
      if (settled) return;
      observed.close_observed = true;
      observed.close_code = exitCode(code);
      observed.close_signal = exitSignal(closeSignal);
      if (!observed.spawn_observed || !observed.exit_observed || observed.exit_code !== 0
        || observed.exit_signal !== null || observed.close_code !== 0 || observed.close_signal !== null) {
        latch("copilot_unsuccessful");
      }
      finish();
    });
    child.stdout.setEncoding("utf8");
    child.stdout.on("error", () => stop("copilot_process_io_failed"));
    child.stdout.on("data", chunk => {
      if (settled) return;
      observed.stdout_bytes_observed += Buffer.byteLength(chunk);
      if (overflow) return;
      if (observed.stdout_bytes_observed > MAX_EVENT_BYTES) {
        overflow = true;
        stop("copilot_output_limit");
      } else {
        output += chunk;
        observed.stdout_bytes_retained += Buffer.byteLength(chunk);
      }
    });
  });
}

export async function invokeCopilot(config, callId, prompt, {
  spawnProcess = spawn, timers = globalThis, now = () => performance.now(),
  signal = bridgeCancellation.signal, fileSystem = fs, publishReceipt = writeNew,
  diagnostic = code => console.error(code),
} = {}) {
  const callDirectory = path.join(config.directory, `call-${callId}`);
  await fileSystem.mkdir(callDirectory, { mode: 0o700 });
  const usageFile = path.join(callDirectory, "usage.json");
  const args = [
    "-C", callDirectory, "--model", config.model, "--reasoning-effort", config.reasoning_effort,
    "--no-custom-instructions", "--disable-builtin-mcps",
    "--available-tools=pgag_evaluation_no_tools", "--no-ask-user", "--no-auto-update",
    "--no-remote", "--no-remote-export", "--output-format", "json",
    "--usage-output-file", usageFile, "-p", prompt,
  ];
  const failure = { code: null };
  const interrupt = () => { failure.code ??= "copilot_interrupted"; };
  signal?.addEventListener("abort", interrupt, { once: true });
  if (signal?.aborted) interrupt();
  const started = now();
  let stdoutFile;
  let stderrFile;
  let result = { observed: processObservation(), output: "" };
  const io = { stdout_flushed: false, stderr_flushed: false, stdout_closed: null, stderr_closed: null };
  const secondary = [];
  let reusable = true;
  const recordFailure = code => {
    if (failure.code !== null) {
      secondary.push(code);
      diagnostic(JSON.stringify({ format: "pgag-copilot-diagnostic-v1", call_id: callId,
        primary_code: failure.code, secondary_code: code }));
    } else {
      failure.code = code;
    }
  };
  try {
    try {
      stdoutFile = await fileSystem.open(path.join(callDirectory, "events.jsonl"), "wx", 0o600);
      stderrFile = await fileSystem.open(path.join(callDirectory, "stderr.log"), "wx", 0o600);
      result = await observeCopilotProcess(args, {
        cwd: callDirectory, stdio: ["ignore", "pipe", stderrFile.fd],
        env: { ...process.env, COPILOT_ALLOW_ALL: "false" },
      }, { spawnProcess, timers, now, signal, failure });
      if (result.observed.close_wait_expired) {
        reusable = false;
        recordFailure("copilot_close_unacknowledged");
      }
    } catch {
      reusable = false;
      recordFailure("copilot_process_io_failed");
    }
    for (const [name, handle] of [["stdout", stdoutFile], ["stderr", stderrFile]]) {
      if (!handle) continue;
      try {
        if (name === "stdout") await handle.writeFile(result.output);
        await handle.sync();
        io[`${name}_flushed`] = true;
      } catch {
        reusable = false;
        recordFailure("copilot_process_io_failed");
      }
      try {
        await handle.close();
        io[`${name}_closed`] = true;
      } catch {
        io[`${name}_closed`] = false;
        reusable = false;
        recordFailure("copilot_process_io_failed");
      }
    }
    const receipt = {
      format: "pgag-copilot-process-v1", call_id: callId,
      observation_scope: "subprocess_and_output_cleanup_before_event_usage_validation",
      ...result.observed, first_failure: failure.code, secondary_failures: secondary,
      io, invocation_elapsed_ms: Math.max(0, now() - started),
    };
    try { await publishReceipt(path.join(callDirectory, "process.json"), receipt); }
    catch {
      reusable = false;
      recordFailure("copilot_process_receipt_failed");
    }
    if (failure.code !== null) {
      const error = new Error(failure.code);
      error.bridge_reusable = reusable;
      throw error;
    }
    const content = auditEvents(result.output, config.model, config.reasoning_effort);
    const usage = usageSummary(JSON.parse(await readPrivate(usageFile, MAX_EVENT_BYTES)), config.model);
    if (failure.code !== null) throw new Error(failure.code);
    return { content, usage };
  } catch (error) {
    if (failure.code !== null) {
      const primary = new Error(failure.code);
      primary.bridge_reusable = reusable;
      throw primary;
    }
    throw error;
  } finally {
    signal?.removeEventListener("abort", interrupt);
  }
}

export async function main(argv, { invoke = invokeCopilot, versionProbe = spawnSync } = {}) {
  process.umask(0o077);
  const config = parseArguments(argv);
  await privateDirectory(config.directory);
  if ((await fs.readdir(config.directory)).length) throw new Error("new_bridge_directory_required");
  const version = versionProbe("copilot", ["--version"], { encoding: "utf8", timeout: 10000 });
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
  let reusable = true;
  while (!interrupted && reusable && completed < config.max_calls) {
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
      ({ content, usage } = await invoke(config, callId, request.prompt));
    } catch (failure) {
      error = safeCopilotErrorCode(failure.message) ?? "copilot_transport_failed";
      if (failure.bridge_reusable === false) reusable = false;
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
  else if (!reusable) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      interrupted = true;
      bridgeCancellation.abort();
    });
  }
  main(process.argv.slice(2)).catch(() => {
    console.error("copilot_bridge_failed");
    process.exitCode = 1;
  });
}
