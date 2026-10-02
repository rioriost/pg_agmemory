import { spawn } from "node:child_process";
import { constants, closeSync, fsyncSync, openSync, writeSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { safeCopilotErrorCode } from "./copilot-eval-bridge.mjs";
import { evaluationTiming } from "./development-eval-timing.mjs";
import {
  ARMS, cleanupFailure, EvaluationError, exactKeys, parseJson, remainingMilliseconds, requireCondition, sha256,
  validateUsage,
} from "./development-eval-protocol.mjs";

export async function privateDirectory(directory, { create = false } = {}) {
  requireCondition(path.isAbsolute(directory), "absolute_private_directory_required");
  let current = path.parse(directory).root;
  for (const part of directory.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    try {
      const info = await fs.lstat(current);
      requireCondition(info.isDirectory() && !info.isSymbolicLink(), "private_path_invalid");
    } catch (error) {
      if (error.code !== "ENOENT" || current !== directory || !create) throw error;
      await fs.mkdir(current, { mode: 0o700 });
    }
  }
  const info = await fs.lstat(directory);
  requireCondition(info.uid === process.getuid() && (info.mode & 0o077) === 0
    && await fs.realpath(directory) === directory, "private_directory_required");
}

export async function readPrivate(file, maximum = 1048576, { singleLink = false } = {}) {
  await privateDirectory(path.dirname(file));
  const handle = await fs.open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await handle.stat();
    requireCondition(info.isFile() && info.uid === process.getuid()
      && (info.mode & 0o077) === 0 && info.size <= maximum
      && (!singleLink || info.nlink === 1), "private_file_invalid");
    const bytes = Buffer.alloc(maximum + 1);
    const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
    requireCondition(bytesRead <= maximum, "private_file_limit");
    return bytes.subarray(0, bytesRead);
  } finally { await handle.close(); }
}

export async function writeNew(file, value, { beforePublish } = {}) {
  requireCondition(beforePublish === undefined || typeof beforePublish === "function",
    "invalid_publication_guard");
  await privateDirectory(path.dirname(file));
  const temporary = `${file}.writing`;
  const handle = await fs.open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify(value) + "\n");
    await handle.sync();
  } finally { await handle.close(); }
  const guarded = beforePublish?.();
  if (guarded !== null && guarded !== undefined && typeof guarded.then === "function") {
    // Reject asynchronous guards without leaving a rejected promise unhandled.
    Promise.resolve(guarded).catch(() => {});
    throw new EvaluationError("asynchronous_publication_guard");
  }
  await fs.link(temporary, file);
  await fs.unlink(temporary);
  const directory = await fs.open(path.dirname(file), constants.O_RDONLY);
  try { await directory.sync(); }
  finally { await directory.close(); }
}

export class Journal {
  constructor(file) {
    this.fd = openSync(file, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL
      | constants.O_NOFOLLOW, 0o600);
    this.sequence = 0;
    this.closed = false;
  }

  record(event) {
    requireCondition(!this.closed, "journal_closed");
    const line = Buffer.from(JSON.stringify({
      ...event, host_event_sequence: this.sequence + 1, timestamp: new Date().toISOString(),
    }) + "\n");
    let written = 0;
    while (written < line.length) {
      const count = writeSync(this.fd, line, written, line.length - written);
      requireCondition(count > 0, "journal_write_incomplete");
      written += count;
    }
    fsyncSync(this.fd);
    this.sequence += 1;
  }

  close() {
    if (!this.closed) { closeSync(this.fd); this.closed = true; }
  }
}

class ResponseWindow {
  constructor(milliseconds, { deadline = Infinity, monotonicDeadline = Infinity, signal }, {
    wallNow = () => Date.now(), monotonicNow = () => performance.now(), timers = globalThis,
  }) {
    requireCondition([deadline, monotonicDeadline].every(value =>
      typeof value === "number" && (Number.isFinite(value) || value === Infinity)),
    "invalid_deadline");
    this.wallNow = wallNow;
    this.monotonicNow = monotonicNow;
    this.timers = timers;
    this.deadline = Math.min(deadline, wallNow() + milliseconds);
    this.monotonicDeadline = Math.min(monotonicDeadline, monotonicNow() + milliseconds);
    this.controller = new AbortController();
    this.signal = this.controller.signal;
    this.outerSignal = signal;
    this.cancel = () => this.abort(signal.reason instanceof EvaluationError
      ? signal.reason : new EvaluationError("operation_cancelled"));
    this.arm = () => {
      if (this.signal.aborted) return;
      const remaining = this.remaining();
      if (remaining <= 0) this.abort(new EvaluationError("response_deadline"));
      else this.timer = timers.setTimeout(this.arm, Math.ceil(remaining));
    };
    if (signal?.aborted) this.cancel();
    else {
      signal?.addEventListener("abort", this.cancel, { once: true });
      this.arm();
    }
  }

  remaining() {
    return Math.min(this.deadline - this.wallNow(),
      this.monotonicDeadline - this.monotonicNow());
  }

  abort(error) {
    if (this.signal.aborted) return;
    error.cleanupDeadline = Math.min(this.wallNow() + 15000,
      error.cleanupDeadline ?? Infinity, this.outerSignal?.reason?.cleanupDeadline ?? Infinity);
    this.timers.clearTimeout(this.timer);
    this.controller.abort(error);
  }

  assertActive(code = "response_deadline") {
    if (!this.signal.aborted && this.remaining() <= 0) this.abort(new EvaluationError(code));
    if (this.signal.aborted) throw this.signal.reason;
  }

  async wait(operation) {
    let cancel;
    const aborted = new Promise((_, reject) => {
      cancel = () => reject(this.signal.reason);
      if (this.signal.aborted) cancel();
      else this.signal.addEventListener("abort", cancel, { once: true });
    });
    try { return await Promise.race([operation, aborted]); }
    finally { this.signal.removeEventListener("abort", cancel); }
  }

  close() {
    this.timers.clearTimeout(this.timer);
    this.outerSignal?.removeEventListener("abort", this.cancel);
  }
}

export async function waitPrivate(file, {
  deadline, signal, alive = () => true, responseWindow,
} = {}) {
  const wallNow = responseWindow?.wallNow ?? (() => Date.now());
  while (wallNow() < deadline) {
    responseWindow?.assertActive();
    requireCondition(!signal?.aborted, "operation_cancelled");
    try {
      const reading = readPrivate(file);
      const result = await (responseWindow ? responseWindow.wait(reading) : reading);
      responseWindow?.assertActive();
      if (responseWindow) requireCondition(wallNow() < deadline, "response_deadline");
      return result;
    }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    responseWindow?.assertActive();
    requireCondition(alive(), "transport_process_exited");
    const waiting = sleep(Math.min(50, Math.max(1, deadline - wallNow())));
    await (responseWindow ? responseWindow.wait(waiting) : waiting);
  }
  responseWindow?.assertActive();
  throw new EvaluationError("response_deadline");
}

export class CopilotTransport {
  // Clock/timer injection is for offline tests, not runtime deadline configuration.
  constructor({ directory, runId, model, effort, ledger, record, timingProfile }, timingRuntime = {}) {
    this.timing = evaluationTiming(timingProfile);
    this.timingProfile = timingProfile;
    this.timingRuntime = timingRuntime;
    this.directory = directory;
    this.runId = runId;
    this.model = model;
    this.effort = effort;
    this.ledger = ledger;
    this.record = record;
    this.bridges = new Map();
    this.active = false;
  }

  async bridge(arm, { deadline = Infinity, signal, responseWindow } = {}) {
    const wallNow = responseWindow?.wallNow ?? (() => Date.now());
    responseWindow?.assertActive("model_admission_deadline");
    requireCondition(!signal?.aborted && wallNow() < deadline, "model_admission_deadline");
    requireCondition(ARMS.includes(arm), "invalid_arm");
    if (this.bridges.has(arm)) {
      const existing = this.bridges.get(arm);
      requireCondition(!existing.exited && !existing.stopped, "bridge_not_reusable");
      return existing;
    }
    const directory = path.join(this.directory, arm);
    await privateDirectory(directory, { create: true });
    requireCondition((await fs.readdir(directory)).length === 0, "new_bridge_directory_required");
    const log = await fs.open(path.join(this.directory, `${arm}.log`), "wx", 0o600);
    const bridgeRunId = `agent-eval-${sha256(`${this.runId}:${arm}`).slice(0, 24)}`;
    if (responseWindow?.signal.aborted || responseWindow?.remaining() <= 0) {
      await log.close();
      responseWindow.assertActive("model_admission_deadline");
    }
    if (signal?.aborted || wallNow() >= deadline) {
      await log.close();
      throw new EvaluationError("model_admission_deadline");
    }
    const child = spawn(process.execPath, [
      fileURLToPath(new URL("./copilot-eval-bridge.mjs", import.meta.url)),
      "--directory", directory, "--model", this.model, "--reasoning-effort", this.effort,
      "--max-calls", "160", "--run-id", bridgeRunId,
      ...(this.timingProfile === undefined ? [] : ["--timing-profile", this.timingProfile]),
    ], { cwd: directory, stdio: ["ignore", log.fd, log.fd], shell: false });
    const bridge = { child, directory, exited: false, stopped: false, exitCode: null, failure: null };
    this.bridges.set(arm, bridge);
    bridge.completion = new Promise((resolve) => {
      child.once("error", (error) => { bridge.failure = error.code; });
      child.once("close", (code) => {
        bridge.exited = true;
        bridge.exitCode = code;
        resolve();
      });
    });
    await log.close();
    this.record({ kind: "bridge_started", arm, bridge_id: `${this.runId}-${arm}`,
      bridge_run_id: bridgeRunId, pid: child.pid ?? null });
    const raw = await waitPrivate(path.join(directory, "transport.json"), {
      deadline: Math.min(deadline, wallNow() + 15000), signal, alive: () => !bridge.exited,
      responseWindow,
    });
    const metadata = parseJson(raw);
    requireCondition(metadata.format === "pgag-copilot-transport-v1"
      && metadata.run_id === bridgeRunId && metadata.model === this.model
      && metadata.reasoning_effort === this.effort && metadata.max_calls === 160
      && metadata.fresh_session_per_call === true && metadata.custom_instructions === false
      && metadata.tools_allowed === false, "bridge_identity_mismatch");
    requireCondition(metadata.timing_profile === this.timingProfile
      && (this.timingProfile === undefined
        ? metadata.model_timeout_ms === undefined && metadata.response_timeout_ms === undefined
        : metadata.model_timeout_ms === this.timing.model_ms
          && metadata.response_timeout_ms === this.timing.response_ms), "bridge_timing_mismatch");
    await privateDirectory(path.join(directory, "queue"), { create: true });
    this.record({ kind: "bridge_ready", arm, transport: metadata });
    return bridge;
  }

  async invoke(context, {
    deadline, monotonicDeadline, signal, beforePublish,
  } = {}) {
    requireCondition(!this.active, "concurrent_model_dispatch_forbidden");
    requireCondition(beforePublish === undefined || typeof beforePublish === "function",
      "invalid_publication_guard");
    const responseWindow = this.timingProfile === undefined ? null : new ResponseWindow(
      this.timing.response_ms, { deadline, monotonicDeadline, signal }, this.timingRuntime);
    const wallNow = responseWindow?.wallNow ?? (() => Date.now());
    if (responseWindow) deadline = responseWindow.deadline;
    else if (deadline === undefined) deadline = Date.now() + this.timing.response_ms;
    signal = responseWindow?.signal ?? signal;
    this.active = true;
    let bridge;
    let admitted;
    let reportedUsage = null;
    let bridgeError = null;
    let cleanupPromise;
    const beginCleanup = error => {
      const cleanupDeadline = Math.min(wallNow() + 15000, error.cleanupDeadline ?? Infinity,
        signal?.reason?.cleanupDeadline ?? Infinity);
      error.cleanupDeadline = cleanupDeadline;
      cleanupPromise ??= this.stop(context.arm, { cancel: true, deadline: cleanupDeadline })
        .then(() => null, cleanupError => cleanupError);
      return cleanupPromise;
    };
    const cancel = () => {
      if (admitted) this.ledger.stop();
      if (bridge || this.bridges.has(context.arm)) beginCleanup(signal.reason);
    };
    responseWindow?.signal.addEventListener("abort", cancel, { once: true });
    try {
      responseWindow?.assertActive("model_admission_deadline");
      bridge = await this.bridge(context.arm, { deadline, signal, responseWindow });
      responseWindow?.assertActive("model_admission_deadline");
      requireCondition(!signal?.aborted && wallNow() < deadline, "model_admission_deadline");
      admitted = this.ledger.reserve(context);
      const callId = admitted.receipt.bridge_call_id;
      const guard = responseWindow ? () => {
        responseWindow.assertActive();
        const result = beforePublish?.();
        if (result !== null && result !== undefined && typeof result.then === "function") return result;
        responseWindow.assertActive();
        return result;
      } : beforePublish;
      await writeNew(path.join(bridge.directory, "queue", `${callId}.request.json`),
        admitted.request, { beforePublish: guard });
      responseWindow?.assertActive();
      const raw = await waitPrivate(
        path.join(bridge.directory, "queue", `${callId}.response.json`),
        { deadline: responseWindow?.deadline ?? Math.min(deadline, Date.now() + this.timing.response_ms),
          signal, alive: () => !bridge.exited, responseWindow },
      );
      const response = parseJson(raw);
      exactKeys(response, [
        "format", "call_id", "status", "content", "error", "duration_seconds",
        "model", "reasoning_effort", "usage",
      ]);
      requireCondition(response.format === "pgag-copilot-response-v1" && response.call_id === callId
        && response.model === this.model && response.reasoning_effort === this.effort,
      "model_response_identity_mismatch");
      bridgeError = response.status === "error" ? safeCopilotErrorCode(response.error) : null;
      responseWindow?.assertActive("late_model_response");
      requireCondition(!signal?.aborted && wallNow() < deadline, "late_model_response");
      reportedUsage = response.usage;
      this.record({ kind: "model_response_received", receipt: admitted.receipt,
        response_sha256: sha256(raw), status: response.status,
        error: safeCopilotErrorCode(bridgeError),
        usage: response.usage, duration_seconds: response.duration_seconds });
      try { validateUsage(response.usage); }
      catch (error) {
        this.ledger.stop();
        throw new EvaluationError("failed_transport_accounting");
      }
      requireCondition(response.status === "ok" && response.error === null
        && typeof response.content === "string"
        && typeof response.duration_seconds === "number"
        && Number.isFinite(response.duration_seconds) && response.duration_seconds >= 0,
      "model_response_failed");
      responseWindow?.assertActive("late_model_response");
      return { text: response.content, receipt_ref: admitted.receipt,
        usage: response.usage, duration_seconds: response.duration_seconds };
    } catch (error) {
      error.receipt_ref = admitted?.receipt ?? null;
      error.usage = reportedUsage;
      error.bridge_error = safeCopilotErrorCode(bridgeError);
      this.record({ kind: "model_invocation_failed",
        receipt: admitted?.receipt ?? null, code: error.code ?? "model_transport_failed",
        bridge_error: safeCopilotErrorCode(error.bridge_error),
        usage_unknown: admitted !== undefined && reportedUsage === null });
      if (admitted) this.ledger.stop();
      if (bridge || this.bridges.has(context.arm)) {
        const cleanupError = await beginCleanup(error);
        if (cleanupError) {
          cleanupFailure(error, error.cleanupDeadline);
          error.cleanup_failures = [{ code: cleanupError.code ?? "bridge_cleanup_failed" }];
        }
      }
      throw error;
    } finally {
      responseWindow?.signal.removeEventListener("abort", cancel);
      responseWindow?.close();
      this.active = false;
    }
  }

  async stop(arm, { cancel = false, deadline = Date.now() + 15000 } = {}) {
    const bridge = this.bridges.get(arm);
    if (!bridge || bridge.stopped && bridge.exited) return;
    let timer;
    try {
      const firstStop = !bridge.stopped;
      bridge.stopped = true;
      if (!bridge.exited) {
        if (cancel) bridge.child.kill("SIGTERM");
        else if (firstStop) {
          await writeNew(path.join(bridge.directory, "stop.json"), {
            reason: "evaluation_complete", run_id: this.runId,
          });
        }
      }
      await Promise.race([
        bridge.completion,
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new EvaluationError("bridge_termination_unacknowledged")),
            remainingMilliseconds(deadline, 15000, "bridge_termination_unacknowledged"));
        }),
      ]);
      this.record({ kind: "bridge_stopped", arm, pid: bridge.child.pid ?? null,
        exit_code: bridge.exitCode, acknowledged: bridge.exited, cancellation_requested: cancel });
    } catch (error) { throw cleanupFailure(error, deadline); }
    finally { clearTimeout(timer); }
  }

  async close({ deadline = Date.now() + 15000 } = {}) {
    const failures = [];
    for (const arm of this.bridges.keys()) {
      try { await this.stop(arm, { deadline }); }
      catch (error) { failures.push({ arm, code: error.code ?? "bridge_cleanup_failed" }); }
    }
    if (failures.length) this.record({ kind: "bridge_cleanup_failed", failures });
    if (failures.length) throw cleanupFailure(new EvaluationError("bridge_cleanup_failed"), deadline);
  }
}
