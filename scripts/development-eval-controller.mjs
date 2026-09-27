import { constants } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import {
  canonicalJson, EvaluationError, exactKeys, parseJson, PROTOCOL, requireCondition, sha256,
  remainingMilliseconds, validateRequest, validateUsage,
} from "./development-eval-protocol.mjs";
import { runProcess } from "./development-eval-sandbox.mjs";
import { privateDirectory, readPrivate, writeNew } from "./development-eval-transport.mjs";

const nanoseconds = (seconds) => {
  requireCondition(typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0,
    "invalid_duration");
  const result = Math.round(seconds * 1e9);
  requireCondition(Number.isSafeInteger(result) && result >= 0, "invalid_duration");
  return result;
};

const ERROR_CODES = new Map([
  ["text_limit", "prompt_budget_exhausted"],
  ["bridge_request_limit", "prompt_budget_exhausted"],
  ["model_response_failed", "model_response_invalid"],
  ["command_timeout", "command_timeout"],
  ["command_output_limit", "execute_helper_failed"],
]);

const LOCAL_WORK_FAILURES = new Set([
  "invalid_action", "step_limit_exceeded", "prompt_budget_exhausted", "session_deadline",
]);
const MEMORY_FAILURES = new Set([
  "invalid_handoff_note", "invalid_memory_decision", "invalid_memory_provenance",
  "invalid_memory_revision", "invalid_memory_forget_proposal", "memory_capacity_exceeded",
  "memory_prompt_budget_exhausted", "memory_inventory_too_large",
]);
const TIMEOUT_REASONS = new Set(["response_timeout", "late_ipc_reply", "session_deadline"]);

const errorCode = (error) => typeof error?.code === "string" ? error.code : "coordinator_failed";
const same = (left, right) => left !== undefined && right !== undefined
  && canonicalJson(left) === canonicalJson(right);
const count = (value) => Number.isSafeInteger(value) && value >= 0;

function usageAccounting(usage) {
  if (usage === null || typeof usage !== "object" || Array.isArray(usage)) {
    return { known: false, healthy: false };
  }
  try {
    // Validate the full shape independently of the one-provider-request continuation gate.
    validateUsage({ ...usage, api_requests: 1 });
    requireCondition(count(usage.api_requests)
      && (usage.nano_aiu === null || count(usage.nano_aiu)), "failed_transport_accounting");
  } catch (error) {
    if (!(error instanceof EvaluationError)) throw error;
    return { known: false, healthy: false };
  }
  return { known: true, healthy: usage.api_requests === 1, requests: usage.api_requests };
}

function validReceipt(receipt) {
  if (receipt === null || typeof receipt !== "object" || Array.isArray(receipt)) return false;
  return Object.keys(receipt).sort().join(",") === "bridge_call_id,bridge_id,global_ordinal"
    && count(receipt.global_ordinal) && receipt.global_ordinal >= 1 && receipt.global_ordinal <= 318
    && typeof receipt.bridge_id === "string"
    && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(receipt.bridge_id)
    && typeof receipt.bridge_call_id === "string" && /^[0-9]{6}$/.test(receipt.bridge_call_id)
    && Number(receipt.bridge_call_id) >= 1 && Number(receipt.bridge_call_id) <= 160;
}

function commandReply(operation) {
  if (operation?.request.operation !== "invoke_model"
    || operation.request.body.phase !== "work" || operation.result.status !== "ok"
    || typeof operation.result.text !== "string") return false;
  let action;
  try { action = parseJson(Buffer.from(operation.result.text), { maximum: 65536 }); }
  catch (error) {
    if (!(error instanceof EvaluationError)) throw error;
    return false;
  }
  return action !== null && typeof action === "object" && !Array.isArray(action)
    && Object.keys(action).join(",") === "command" && typeof action.command === "string"
    && action.command.trim().length > 0 && !action.command.includes("\0")
    && Buffer.byteLength(action.command) <= 8192;
}

class EventTail {
  constructor(file, sessionId, record) {
    this.file = file;
    this.sessionId = sessionId;
    this.record = record;
    this.offset = 0;
    this.pending = Buffer.alloc(0);
    this.sequence = 0;
    this.workStarted = null;
    this.finished = false;
    this.replies = new Map();
    this.nativeFailures = new Map();
    this.controllerFailure = null;
  }

  async drain() {
    let handle;
    try { handle = await fs.open(this.file, constants.O_RDONLY | constants.O_NOFOLLOW); }
    catch (error) { if (error.code === "ENOENT") return; throw error; }
    try {
      const info = await handle.stat();
      requireCondition(info.isFile() && info.nlink === 1 && info.uid === process.getuid()
        && (info.mode & 0o077) === 0 && info.size >= this.offset, "controller_event_file_invalid");
      while (this.offset < info.size) {
        const data = Buffer.alloc(Math.min(65536, info.size - this.offset));
        const { bytesRead } = await handle.read(data, 0, data.length, this.offset);
        requireCondition(bytesRead > 0, "controller_event_read_failed");
        this.offset += bytesRead;
        this.pending = Buffer.concat([this.pending, data.subarray(0, bytesRead)]);
        let newline;
        while ((newline = this.pending.indexOf(10)) !== -1) {
          const event = parseJson(this.pending.subarray(0, newline), { maximum: 524288 });
          this.pending = this.pending.subarray(newline + 1);
          exactKeys(event, ["protocol", "session_id", "sequence", "kind", "at", "elapsed_ns", "data"]);
          requireCondition(event.protocol === PROTOCOL && event.session_id === this.sessionId
            && event.sequence === ++this.sequence && this.sequence <= 1024,
          "controller_event_identity");
          if (event.kind === "work_started") {
            requireCondition(this.workStarted === null && !this.finished
              && event.data.work_limit_seconds === 900, "work_deadline_reset");
            this.workStarted = Date.now();
          }
          if (["work_finished", "controller_failed", "boundary_finished"].includes(event.kind)) {
            this.finished = true;
          }
          if (event.kind === "ipc_reply") {
            const reply = event.data.reply ?? event.data.invalid_reply;
            requireCondition(reply !== null && typeof reply === "object"
              && Number.isSafeInteger(reply.sequence) && reply.sequence >= 1
              && !this.replies.has(reply.sequence), "controller_reply_event_invalid");
            this.replies.set(reply.sequence, reply);
          }
          if (event.kind === "native_failure" && typeof event.data.error?.code === "string") {
            this.nativeFailures.set(event.data.error.code, event.data);
          }
          if (event.kind === "controller_failed") {
            requireCondition(this.controllerFailure === null, "duplicate_controller_failure");
            this.controllerFailure = event.data;
          }
          this.record({ kind: "controller_event", event });
        }
        requireCondition(this.pending.length <= 524288, "controller_event_limit");
      }
    } finally { await handle.close(); }
  }
}

export class ControllerSession {
  constructor({ infrastructure, transport, config, guest, record, runDeadline, runner = runProcess,
    signal }) {
    this.infrastructure = infrastructure;
    this.transport = transport;
    this.config = config;
    this.guest = guest;
    this.record = record;
    this.runDeadline = runDeadline;
    this.runner = runner;
    this.sequence = 1;
    this.requests = new Map();
    this.operations = [];
    this.fatalFailure = null;
    this.cleanupDeadline = null;
    this.abort = new AbortController();
    this.outerSignal = signal;
    this.onAbort = () => this.latchFatal(
      typeof signal?.reason?.code === "string" ? signal.reason
        : new EvaluationError(Date.now() >= this.runDeadline ? "run_deadline" : "operation_cancelled"),
    );
    if (signal?.aborted) this.onAbort();
    signal?.addEventListener("abort", this.onAbort, { once: true });
  }

  latchFatal(error) {
    if (this.fatalFailure === null) this.fatalFailure = error;
    this.cleanupDeadline ??= Date.now() + 15000;
    if (Number.isFinite(error?.cleanupDeadline)) {
      this.cleanupDeadline = Math.min(this.cleanupDeadline, error.cleanupDeadline);
    }
    this.transport.ledger?.stop();
    this.abort.abort(this.fatalFailure);
    return this.fatalFailure;
  }

  assertActive() {
    if (this.fatalFailure !== null) throw this.fatalFailure;
    requireCondition(!this.abort.signal.aborted, "operation_cancelled");
    requireCondition(!this.transport.ledger?.stopped, "admission_stopped");
    requireCondition(Date.now() < this.runDeadline, "run_deadline");
  }

  deadline(events) {
    return Math.min(this.runDeadline, events.workStarted === null
      ? Infinity : events.workStarted + 900000);
  }

  async publish(reply) {
    this.assertActive();
    requireCondition(Buffer.byteLength(JSON.stringify(reply) + "\n") <= 70000,
      "controller_reply_limit");
    const filename = `${String(reply.sequence).padStart(6, "0")}.json`;
    await writeNew(path.join(this.root, "staging", filename), reply);
    const result = await this.runner("container", [
      "exec", this.infrastructure.api, "python", "-I", "/app/scripts/development-eval-native.py",
      "--publish-reply", `${this.guestRoot}/staging/${filename}`,
      `${this.guestRoot}/ipc/replies/${filename}`,
    ], { timeoutMs: remainingMilliseconds(this.activeDeadline, 10000), signal: this.abort.signal });
    this.assertActive();
    requireCondition(result.exitCode === 0 && !result.timedOut && !result.overflow && !result.cancelled,
      "controller_reply_publication_failed");
  }

  async dispatch(request, events) {
    this.assertActive();
    const config = this.config;
    const phase = request.operation === "execute" ? "execute" : request.body.phase;
    if (phase === "execute" || phase === "work") {
      requireCondition(config.mode === "work" && events.workStarted !== null && !events.finished,
        "work_before_deadline_event");
    } else if (phase === "memory_plan") {
      requireCondition(config.mode === "work" && config.slot.arm === "pg_agmemory"
        && config.slot.milestone > 1 && events.workStarted === null, "planning_outside_retrieval");
    } else {
      requireCondition(config.mode === "boundary"
        && (phase === "handoff" && config.slot.arm === "handoff"
          || phase === "memory_decision" && config.slot.arm === "pg_agmemory"),
      "boundary_phase_mismatch");
    }
    requireCondition(Date.now() < this.deadline(events), "session_deadline");
    if (request.operation === "execute") {
      const result = await this.guest.execute(request.body.command, {
        signal: this.abort.signal, timeoutMs: Math.min(30000, this.deadline(events) - Date.now()),
      });
      const output = Buffer.concat([result.stdout, result.stderr]);
      return {
        status: "ok", exit_code: result.exitCode, output_base64: output.toString("base64"),
        captured_bytes: output.length, output_truncated: false,
        duration_ns: nanoseconds(result.durationSeconds),
      };
    }
    const result = await this.transport.invoke({
      arm: config.slot.arm, sessionId: config.session_id,
      slotId: `${config.slot.project_id}-${config.slot.milestone}-${config.slot.arm}`,
      phase, prompt: request.body.prompt, sequence: request.sequence,
      planningRound: request.body.planning_round,
    }, { deadline: Math.min(this.deadline(events), Date.now() + 180000), signal: this.abort.signal });
    if (!validReceipt(result.receipt_ref) || !usageAccounting(result.usage).healthy) {
      const error = new EvaluationError("failed_transport_accounting");
      error.receipt_ref = result.receipt_ref ?? null;
      error.usage = result.usage ?? null;
      throw error;
    }
    let duration;
    try { duration = nanoseconds(result.duration_seconds); }
    catch (error) {
      error.receipt_ref = result.receipt_ref;
      error.usage = result.usage;
      throw error;
    }
    const response = {
      status: "ok", text: result.text, receipt_ref: result.receipt_ref,
      model: config.model.model, reasoning_effort: config.model.reasoning_effort,
      usage: result.usage, duration_ns: duration,
    };
    const envelopeBytes = Buffer.byteLength(JSON.stringify({
      protocol: PROTOCOL, session_id: config.session_id, sequence: request.sequence,
      operation: request.operation, result: response,
    }) + "\n");
    if (typeof result.text !== "string" || !result.text.trim()
      || Buffer.byteLength(result.text) > 65536 || envelopeBytes > 70000) {
      const error = new EvaluationError("model_response_failed");
      error.receipt_ref = result.receipt_ref;
      error.usage = result.usage;
      throw error;
    }
    return response;
  }

  ordinaryDispatchFailure(error, request, admissionBefore) {
    if (this.fatalFailure !== null || this.abort.signal.aborted || this.transport.ledger?.stopped
      || error?.cleanup_failed === true || error?.cleanup_failures?.length) return false;
    const code = errorCode(error);
    if (request.operation === "execute") {
      return ["command_timeout", "command_output_limit"].includes(code)
        && this.guest?.closed === true;
    }
    if (["text_limit", "bridge_request_limit"].includes(code)) {
      return error.receipt_ref == null && error.usage == null
        && count(admissionBefore) && this.transport.ledger?.ordinal === admissionBefore;
    }
    return code === "model_response_failed" && validReceipt(error.receipt_ref)
      && usageAccounting(error.usage).healthy;
  }

  async handleRequest(request, events) {
    this.assertActive();
    this.activeDeadline = this.deadline(events);
    const admissionBefore = this.transport.ledger?.ordinal;
    let result;
    let originalCode = null;
    try { result = await this.dispatch(request, events); }
    catch (error) {
      originalCode = errorCode(error);
      const ordinary = this.ordinaryDispatchFailure(error, request, admissionBefore);
      if (!ordinary) this.latchFatal(error);
      this.record({ kind: "controller_operation_failed", session_id: this.config.session_id,
        sequence: request.sequence, operation: request.operation, original_code: originalCode,
        fatal: !ordinary, receipt_ref: error.receipt_ref ?? null, usage: error.usage ?? null,
        admission_before: admissionBefore ?? null,
        admission_after: this.transport.ledger?.ordinal ?? null });
      if (!ordinary) throw this.fatalFailure;
      const model = request.operation === "invoke_model";
      result = {
        status: "error", code: ERROR_CODES.get(originalCode),
        outcome: error.receipt_ref ? "known_failure" : model ? "not_started" : "known_failure",
        receipt_ref: model ? error.receipt_ref ?? null : null,
        usage: model ? error.usage ?? null : null, duration_ns: null,
      };
    }
    const admissionAfter = this.transport.ledger?.ordinal;
    if (request.operation === "invoke_model") {
      const admitted = result.receipt_ref !== null;
      const validAdmission = count(admissionBefore) && count(admissionAfter)
        && admissionAfter === admissionBefore + (admitted ? 1 : 0)
        && (!admitted || validReceipt(result.receipt_ref)
          && result.receipt_ref.global_ordinal === admissionAfter
          && result.receipt_ref.bridge_id === `${this.transport.ledger.runId}-${this.config.slot.arm}`
          && Number(result.receipt_ref.bridge_call_id)
            === this.transport.ledger.arms[this.config.slot.arm]);
      if (!validAdmission) {
        const error = new EvaluationError("controller_host_admission_mismatch");
        error.receipt_ref = result.receipt_ref;
        error.usage = result.usage;
        this.latchFatal(error);
        throw error;
      }
    }
    this.assertActive();
    const operation = { request, result, original_code: originalCode, reply_published: false };
    this.operations.push(operation);
    await this.publish({
      protocol: PROTOCOL, session_id: this.config.session_id, sequence: this.sequence,
      operation: request.operation, result,
    });
    operation.reply_published = true;
    this.sequence += 1;
  }

  async checkRequests() {
    const directory = path.join(this.root, "ipc", "requests");
    let files;
    try { files = await fs.readdir(directory); }
    catch (error) { if (error.code === "ENOENT") return null; throw error; }
    for (const sequence of this.requests.keys()) {
      requireCondition(files.includes(`${String(sequence).padStart(6, "0")}.json`),
        "controller_request_removed");
    }
    for (const file of files) {
      requireCondition(/^[0-9]{6}\.json$/.test(file), "unexpected_controller_request_file");
      const sequence = Number(file.slice(0, 6));
      requireCondition(sequence >= 1 && sequence <= this.sequence, "controller_request_sequence");
      const raw = await readPrivate(path.join(directory, file), 70000, { singleLink: true });
      if (sequence < this.sequence) {
        requireCondition(this.requests.get(sequence) === sha256(raw), "controller_request_modified");
      }
    }
    const filename = `${String(this.sequence).padStart(6, "0")}.json`;
    if (!files.includes(filename)) return null;
    const raw = await readPrivate(path.join(directory, filename), 70000, { singleLink: true });
    const request = validateRequest(parseJson(raw), this.config.session_id, this.sequence);
    this.requests.set(this.sequence, sha256(raw));
    return request;
  }

  async run() {
    const { config } = this;
    let execution = Promise.resolve();
    let exited = false;
    let processResult;
    let processFailure;
    try {
      this.assertActive();
      requireCondition(/^[a-zA-Z0-9-]{1,128}$/.test(config.session_id), "invalid_controller_session");
      this.root = path.join(this.infrastructure.directory, "controllers", config.session_id);
      this.guestRoot = `/run/controllers/${config.session_id}`;
      await privateDirectory(this.root, { create: true });
      requireCondition((await fs.readdir(this.root)).length === 0, "new_controller_directory_required");
      for (const name of ["ipc", "output", "home", "staging"]) {
        await privateDirectory(path.join(this.root, name), { create: true });
      }
      await writeNew(path.join(this.root, "input.json"), config);
      const args = [
        "exec", this.infrastructure.api, "/usr/bin/env", "-i",
        "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
      ];
      if (config.slot.arm === "pg_agmemory") {
        args.push("PGAG_DEVELOPMENT_API_URL=http://127.0.0.1:8000",
          `PGAG_DEVELOPMENT_API_TOKEN=${
            this.infrastructure.bearer(config.slot.project_id, config.slot.arm)}`);
      }
      args.push("python", "-I", "/app/scripts/evaluate-development-session.py",
        "--config", `${this.guestRoot}/input.json`, "--ipc", `${this.guestRoot}/ipc`,
        "--output", `${this.guestRoot}/output`, "--home", `${this.guestRoot}/home`);
      const events = new EventTail(path.join(this.root, "output", "events.jsonl"),
        config.session_id, this.record);
      execution = this.runner("container", args, {
        timeoutMs: remainingMilliseconds(this.runDeadline, this.runDeadline - Date.now()),
        maximum: 65536, signal: this.abort.signal,
      }).then((result) => { processResult = result; }, (error) => { processFailure = error; })
        .finally(() => { exited = true; });
      this.record({ kind: "controller_launched", session_id: config.session_id, mode: config.mode });
      while (!exited) {
        this.assertActive();
        await events.drain();
        if (exited) break;
        if (Date.now() >= this.deadline(events)) {
          throw new EvaluationError(Date.now() >= this.runDeadline ? "run_deadline" : "session_deadline");
        }
        const request = await this.checkRequests();
        if (request !== null) {
          await events.drain();
          await this.handleRequest(request, events);
        } else { await sleep(25); }
      }
      await execution;
      this.assertActive();
      if (processFailure) throw processFailure;
      requireCondition(processResult !== undefined && !processResult.timedOut
        && !processResult.overflow && !processResult.cancelled, "controller_process_incomplete");
      await events.drain();
      requireCondition(events.pending.length === 0, "controller_event_incomplete");
      await writeNew(path.join(this.root, "process.json"), {
        exit_code: processResult.exitCode, duration_seconds: processResult.durationSeconds,
        stdout_sha256: sha256(processResult.stdout), stderr_sha256: sha256(processResult.stderr),
      });
      const resultBytes = await readPrivate(path.join(this.root, "output", "result.json"), 1048576);
      const result = parseJson(resultBytes);
      requireCondition(result.protocol === PROTOCOL && result.session_id === config.session_id
        && result.mode === config.mode && result.slot.project_id === config.slot.project_id
        && result.slot.arm === config.slot.arm && result.slot.milestone === config.slot.milestone
        && ["submitted", "boundary_completed", "failed"].includes(result.status)
        && processResult.exitCode === (result.status === "failed" ? 1 : 0),
      "controller_result_identity");
      const pending = await this.checkRequests();
      requireCondition(pending === null, "controller_unanswered_request");
      const accounting = verifyControllerAccounting(result, this.operations, {
        workStarted: events.workStarted !== null, replies: events.replies,
      });
      this.record({ kind: "controller_accounting", session_id: config.session_id, ...accounting });
      requireCondition(accounting.complete, accounting.incomplete
        ? "controller_accounting_incomplete" : "controller_accounting_mismatch");
      requireCondition(accounting.transport_healthy, "failed_transport_accounting");
      if (result.status === "failed") {
        const reported = this.operations.at(-1);
        const hostFailure = reported?.original_code !== null && reported?.original_code !== undefined
          && reported.result.status === "error" && reported.result.code === result.reason;
        const priorSuccess = this.operations.every((operation) => operation.result.status === "ok");
        const localFailure = priorSuccess && config.mode === "work"
          && LOCAL_WORK_FAILURES.has(result.reason)
          && (result.reason !== "invalid_action"
            || result.upstream_exit_status === "RepeatedFormatError" && result.query_attempts >= 1)
          && (result.reason !== "step_limit_exceeded"
            || result.upstream_exit_status === "LimitsExceeded"
              && result.query_attempts === 16 && result.execute_attempts === 16);
        const native = events.nativeFailures.get(result.reason);
        const nativeFailure = native !== undefined && native.operation !== "sdk_capability_setup"
          && result.reason !== "invalid_native_response"
          && (result.reason === "native_api_unavailable"
            || Number.isInteger(native.error.native_status)
              && native.error.native_status >= 400 && native.error.native_status <= 599);
        const memoryFailure = config.slot.arm !== "no_memory"
          && priorSuccess && events.workStarted === null
          && (MEMORY_FAILURES.has(result.reason) || nativeFailure);
        const failure = events.controllerFailure;
        const retrievalFailure = config.mode === "work" && config.slot.arm === "pg_agmemory"
          && config.slot.milestone > 1 && events.workStarted === null && priorSuccess
          && result.outcome_unknown === false
          && failure?.origin === "pg_agmemory.bounded_recall.BoundedRecallError"
          && failure.exception_type === "BoundedRecallError" && failure.phase === "memory_delivery"
          && failure.code === result.reason && failure.outcome_unknown === false;
        const plannerFailure = retrievalFailure && result.reason === "invalid_search_plan"
          && reported?.request.operation === "invoke_model"
          && reported.request.body.phase === "memory_plan" && reported.result.status === "ok";
        const retrievalBudget = retrievalFailure && result.reason === "search_prompt_too_large"
          && this.operations.every((operation) => operation.request.operation === "invoke_model"
            && operation.request.body.phase === "memory_plan");
        if (!(hostFailure || localFailure || memoryFailure || plannerFailure || retrievalBudget)) {
          const error = new EvaluationError("controller_failure_requires_abort");
          error.controller_reason = result.reason;
          throw error;
        }
      }
      this.record({ kind: "controller_result", session_id: config.session_id,
        status: result.status, result_sha256: sha256(resultBytes) });
      return { ...result, host_accounting: accounting };
    } catch (error) {
      const original = this.latchFatal(error);
      const cleanupDeadline = this.cleanupDeadline;
      const cancellationStarted = cleanupDeadline - 15000;
      const cleanup = [];
      if (this.transport.stop) cleanup.push(["bridge", () => this.transport.stop(
        config.slot.arm, { cancel: true, deadline: cleanupDeadline },
      )]);
      if (this.guest) cleanup.push(["guest", () => this.guest.close({ deadline: cleanupDeadline })]);
      if (this.infrastructure.owned.has(this.infrastructure.api)) {
        cleanup.push(["native_api", () => this.infrastructure.remove(
          this.infrastructure.api, { deadline: cleanupDeadline },
        )]);
      }
      cleanup.push(["controller", async () => {
        await execution;
        if (processFailure) throw processFailure;
      }]);
      const outcomes = await Promise.all(cleanup.map(async ([operation, action]) => {
        let timer;
        try {
          await Promise.race([
            Promise.resolve().then(action),
            new Promise((_, reject) => {
              timer = setTimeout(() => reject(new EvaluationError("cleanup_deadline")),
                Math.max(0, cleanupDeadline - Date.now()));
            }),
          ]);
          requireCondition(Date.now() <= cleanupDeadline, "cleanup_deadline");
          return { operation, status: "fulfilled" };
        } catch (failure) {
          return { operation, status: "rejected", code: errorCode(failure) };
        } finally { clearTimeout(timer); }
      }));
      const failures = [
        ...(original.cleanup_failures ?? []),
        ...outcomes.filter((outcome) => outcome.status === "rejected"),
      ];
      if (error !== original) failures.push({ operation: "failure_handler", code: errorCode(error) });
      original.cleanup_failures = failures;
      original.cleanupDeadline = cleanupDeadline;
      try {
        this.record({ kind: "controller_aborted", session_id: config.session_id,
          code: errorCode(original), outcome_unknown: true,
          controller_reason: original.controller_reason ?? null,
          receipt_ref: original.receipt_ref ?? null, usage: original.usage ?? null,
          host_invocation_receipts: this.operations
            .filter((operation) => operation.request.operation === "invoke_model")
            .map((operation) => operation.result.receipt_ref).filter((receipt) => receipt !== null),
          cancellation_elapsed_ms: Date.now() - cancellationStarted,
          cleanup_deadline: cleanupDeadline, cancellation_grace_exceeded: Date.now() > cleanupDeadline,
          cleanup_failures: failures });
      } catch (failure) {
        failures.push({ operation: "abort_record", code: errorCode(failure) });
      }
      throw original;
    } finally {
      this.outerSignal?.removeEventListener("abort", this.onAbort);
    }
  }

}

export function verifyControllerAccounting(result, operations, { workStarted = false, replies } = {}) {
  const models = operations.filter((operation) => operation.request.operation === "invoke_model");
  const receipts = [];
  const issues = new Set();
  let providerRequests = 0;
  let transportHealthy = true;
  for (const operation of models) {
    const value = operation.result;
    if (value.receipt_ref === null) {
      if (!(value.status === "error" && value.outcome === "not_started" && value.usage === null)) {
        providerRequests = null;
        transportHealthy = false;
      }
      continue;
    }
    const receipt = value.receipt_ref;
    const previous = receipts.at(-1);
    if (!validReceipt(receipt) || previous && (
      receipt.global_ordinal <= previous.global_ordinal || receipt.bridge_id !== previous.bridge_id
      || Number(receipt.bridge_call_id) <= Number(previous.bridge_call_id)
    )) issues.add("host_receipt_identity");
    receipts.push(receipt);
    const usage = usageAccounting(value.usage);
    transportHealthy &&= usage.healthy;
    if (!usage.known) providerRequests = null;
    else if (providerRequests !== null) {
      providerRequests += usage.requests;
      if (!count(providerRequests)) {
        providerRequests = null;
        transportHealthy = false;
        issues.add("provider_total_overflow");
      }
    }
  }
  const expected = {
    query_attempts: models.filter((operation) => operation.request.body.phase === "work").length,
    admitted_invocations: receipts.length,
    execute_attempts: operations.length - models.length,
    provider_api_requests: providerRequests,
    invocation_receipts: receipts,
  };
  const queryDelta = result.query_attempts - expected.query_attempts;
  const executeDelta = result.execute_attempts - expected.execute_attempts;
  const local = result.status === "failed" && result.mode === "work" && workStarted
    && result.upstream_exit_status === "EvaluationFailure" && result.outcome_unknown === false
    && operations.every((operation) => operation.result.status === "ok");
  const extraQuery = local && queryDelta === 1 && executeDelta === 0
    && ["prompt_budget_exhausted", "session_deadline"].includes(result.reason)
    && result.execute_attempts === result.query_attempts - 1;
  const extraExecute = local && queryDelta === 0 && executeDelta === 1
    && result.reason === "session_deadline" && result.execute_attempts === result.query_attempts
    && commandReply(operations.at(-1));
  if (!count(result.query_attempts) || result.query_attempts > 16
    || queryDelta !== 0 && !extraQuery) issues.add("query_attempts");
  if (!count(result.execute_attempts) || result.execute_attempts > 16
    || executeDelta !== 0 && !extraExecute
    || result.execute_attempts > result.query_attempts) issues.add("execute_attempts");
  if (result.mode === "boundary" && (result.query_attempts !== 0 || result.execute_attempts !== 0)) {
    issues.add("boundary_attempts");
  }
  if (result.status === "submitted" && (result.query_attempts < 1
    || result.execute_attempts !== result.query_attempts - 1 || result.outcome_unknown !== false)) {
    issues.add("submitted_attempts");
  }
  for (const key of ["admitted_invocations", "provider_api_requests", "invocation_receipts"]) {
    if (!same(result[key], expected[key])) issues.add(key);
  }
  const unacknowledged = [];
  if (replies !== undefined) {
    for (const operation of operations) {
      const { request } = operation;
      const acknowledged = replies.get(request.sequence);
      if (acknowledged === undefined) unacknowledged.push(operation);
      else if (!same(acknowledged, {
        protocol: PROTOCOL, session_id: request.session_id, sequence: request.sequence,
        operation: request.operation, result: operation.result,
      })) issues.add("reply_acknowledgement_mismatch");
    }
    if ([...replies.keys()].some((sequence) =>
      !operations.some((operation) => operation.request.sequence === sequence))) {
      issues.add("unexpected_reply_acknowledgement");
    }
    if (unacknowledged.length) issues.add("unacknowledged_reply");
  }
  const tail = operations.at(-1);
  const observed = result.invocation_receipts;
  const incomplete = result.status === "failed" && result.outcome_unknown === true
    && TIMEOUT_REASONS.has(result.reason) && tail?.reply_published === true
    && tail.request.operation === "invoke_model" && validReceipt(tail.result.receipt_ref)
    && unacknowledged.length === 1 && unacknowledged[0] === tail
    && Array.isArray(observed) && same(observed, receipts.slice(0, -1))
    && result.admitted_invocations === observed.length && result.provider_api_requests === null
    && [...issues].every((key) => [
      "unacknowledged_reply", "invocation_receipts", "admitted_invocations", "provider_api_requests",
    ].includes(key));
  return {
    complete: issues.size === 0, incomplete, transport_healthy: transportHealthy,
    mismatches: [...issues], host_expected: expected,
    local_unpublished_query_attempts: extraQuery ? 1 : 0,
    local_unpublished_execute_attempts: extraExecute ? 1 : 0,
  };
}
