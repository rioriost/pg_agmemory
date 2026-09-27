import { createHash } from "node:crypto";

export const ARMS = Object.freeze(["no_memory", "handoff", "pg_agmemory"]);
export const PHASES = Object.freeze(["work", "handoff", "memory_decision", "memory_plan"]);
export const PROTOCOL = "pgag-development-controller-v1";
export const LIMITS = Object.freeze({
  invocations: 318, bridgeInvocations: 160, work: 16, prompt: 65536,
  bridgeRequest: 70000, memory: 2048, command: 8192, capture: 16384,
  observation: 2048, files: 32, artifact: 262144, input: 8192,
});

export class EvaluationError extends Error {
  constructor(code) {
    super(code);
    this.name = "EvaluationError";
    this.code = code;
  }
}

export function requireCondition(condition, code) {
  if (!condition) throw new EvaluationError(code);
}

export function remainingMilliseconds(deadline = Infinity, maximum = 30000,
  code = "run_deadline") {
  const remaining = Math.floor(deadline - Date.now());
  requireCondition(remaining > 0, code);
  return Math.min(maximum, remaining);
}

export class RunBudget {
  constructor(milliseconds, { signal } = {}) {
    requireCondition(Number.isSafeInteger(milliseconds) && milliseconds > 0
      && milliseconds <= 2147483647, "invalid_deadline");
    this.deadline = Date.now() + milliseconds;
    this.controller = new AbortController();
    this.signal = this.controller.signal;
    this.outerSignal = signal;
    this.cancel = () => this.abort("operation_cancelled");
    this.timer = setTimeout(() => this.abort("run_deadline"), milliseconds);
    if (signal?.aborted) this.cancel();
    signal?.addEventListener("abort", this.cancel, { once: true });
  }

  assertActive() {
    if (!this.signal.aborted && Date.now() >= this.deadline) {
      this.abort("run_deadline");
    }
    if (this.signal.aborted) throw this.signal.reason;
  }

  abort(code) {
    if (this.signal.aborted) return;
    const error = new EvaluationError(code);
    error.cleanupDeadline = Date.now() + 15000;
    this.controller.abort(error);
  }

  close() {
    clearTimeout(this.timer);
    this.outerSignal?.removeEventListener("abort", this.cancel);
  }
}

export function cleanupFailure(error, deadline) {
  error.cleanupDeadline = Math.min(deadline, error.cleanupDeadline ?? Infinity);
  error.cleanup_failed = true;
  return error;
}

export function utf8(value, maximum) {
  requireCondition(typeof value === "string", "text_required");
  for (const character of value) {
    const point = character.codePointAt(0);
    requireCondition(point < 0xd800 || point > 0xdfff, "invalid_unicode");
  }
  requireCondition(Buffer.byteLength(value) <= maximum, "text_limit");
  return value;
}

export function exactKeys(value, keys) {
  requireCondition(value !== null && typeof value === "object" && !Array.isArray(value),
    "object_required");
  requireCondition(Object.keys(value).sort().join("\0") === [...keys].sort().join("\0"),
    "unexpected_fields");
  return value;
}

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function safePath(value) {
  utf8(value, 256);
  requireCondition(value.split("/").every((part) =>
    /^[A-Za-z0-9_-][A-Za-z0-9._-]*$/.test(part)),
  "unsafe_artifact_path");
  return value;
}

// JSON.parse alone loses duplicate keys and the distinction between integer and float syntax.
export function parseJson(raw, { maximum = 1048576, integersOnly = false } = {}) {
  const bytes = Buffer.isBuffer(raw) ? raw : Buffer.from(utf8(raw, maximum));
  requireCondition(bytes.length <= maximum, "json_limit");
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { throw new EvaluationError("invalid_utf8"); }
  let offset = 0;
  const whitespace = () => {
    while (offset < text.length && /[ \t\r\n]/.test(text[offset])) offset += 1;
  };
  const string = () => {
    requireCondition(text[offset] === '"', "invalid_json");
    const start = offset++;
    while (offset < text.length) {
      if (text[offset] === "\\") { offset += 2; continue; }
      if (text[offset++] !== '"') continue;
      let result;
      try { result = JSON.parse(text.slice(start, offset)); }
      catch { throw new EvaluationError("invalid_json"); }
      return utf8(result, maximum);
    }
    throw new EvaluationError("invalid_json");
  };
  const value = (depth) => {
    whitespace();
    if (text[offset] === '"') return string();
    if (text[offset] === "{" || text[offset] === "[") {
      requireCondition(depth < 32, "json_depth");
      const object = text[offset++] === "{";
      const end = object ? "}" : "]";
      const result = object ? Object.create(null) : [];
      const keys = new Set();
      whitespace();
      if (text[offset] === end) { offset += 1; return result; }
      while (offset < text.length) {
        whitespace();
        let key;
        if (object) {
          key = string();
          requireCondition(!keys.has(key), "duplicate_json_key");
          keys.add(key);
          whitespace();
          requireCondition(text[offset++] === ":", "invalid_json");
        }
        const child = value(depth + 1);
        if (object) result[key] = child;
        else result.push(child);
        whitespace();
        if (text[offset] === end) { offset += 1; return result; }
        requireCondition(text[offset++] === ",", "invalid_json");
      }
      throw new EvaluationError("invalid_json");
    }
    for (const [literal, result] of [["true", true], ["false", false], ["null", null]]) {
      if (text.startsWith(literal, offset)) { offset += literal.length; return result; }
    }
    const number = text.slice(offset).match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
    requireCondition(number !== null, "invalid_json");
    requireCondition(!integersOnly || !/[.eE]/.test(number[0]), "integer_required");
    const result = Number(number[0]);
    requireCondition(Number.isFinite(result), "nonfinite_json_number");
    requireCondition(!integersOnly || Number.isSafeInteger(result), "integer_limit");
    offset += number[0].length;
    return result;
  };
  const result = value(0);
  whitespace();
  requireCondition(offset === text.length, "invalid_json");
  return result;
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function gradeCandidate({ stdout, stderr, exitCode, started, timedOut, overflow },
  expectedBytes) {
  const expected = parseJson(expectedBytes, { maximum: LIMITS.capture, integersOnly: true });
  if (!started) return { status: "infrastructure_unknown", reason: "candidate_not_started" };
  if (timedOut) return { status: "failed", reason: "candidate_timeout" };
  if (overflow || stdout.length + stderr.length > LIMITS.capture) {
    return { status: "failed", reason: "output_limit" };
  }
  if (exitCode !== 0) return { status: "failed", reason: "candidate_exit" };
  let actual;
  try { actual = parseJson(stdout, { maximum: LIMITS.capture, integersOnly: true }); }
  catch (error) {
    if (!(error instanceof EvaluationError)) throw error;
    return { status: "failed", reason: "invalid_candidate_output", detail: error.code };
  }
  return canonicalJson(actual) === canonicalJson(expected)
    ? { status: "passed", reason: null } : { status: "failed", reason: "mismatch" };
}

export function validateRequest(value, sessionId, sequence) {
  exactKeys(value, ["protocol", "session_id", "sequence", "operation", "body"]);
  requireCondition(value.protocol === PROTOCOL && value.session_id === sessionId
    && value.sequence === sequence, "ipc_identity_mismatch");
  if (value.operation === "execute") {
    exactKeys(value.body, ["command"]);
    utf8(value.body.command, LIMITS.command);
    requireCondition(value.body.command.trim().length > 0, "empty_command");
  } else {
    requireCondition(value.operation === "invoke_model", "unknown_operation");
    exactKeys(value.body, ["phase", "prompt", "planning_round"]);
    requireCondition(PHASES.includes(value.body.phase), "invalid_model_phase");
    utf8(value.body.prompt, LIMITS.prompt);
    requireCondition(value.body.prompt.trim().length > 0, "empty_prompt");
    requireCondition(value.body.phase === "memory_plan"
      ? Number.isInteger(value.body.planning_round) && value.body.planning_round >= 1
        && value.body.planning_round <= 4 : value.body.planning_round === null,
    "invalid_planning_round");
  }
  return value;
}

export function validateUsage(value) {
  exactKeys(value, [
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "api_requests", "premium_requests", "nano_aiu",
    "api_duration_ms", "monetary_cost_verified",
  ]);
  requireCondition(value.monetary_cost_verified === false && value.api_requests === 1,
    "failed_transport_accounting");
  for (const [key, count] of Object.entries(value)) {
    if (key === "monetary_cost_verified") continue;
    if (count === null && ["reasoning_tokens", "nano_aiu"].includes(key)) continue;
    requireCondition(typeof count === "number" && Number.isFinite(count) && count >= 0,
      "failed_transport_accounting");
    requireCondition(!key.endsWith("_tokens") || Number.isSafeInteger(count),
      "failed_transport_accounting");
  }
  return value;
}

export class InvocationLedger {
  constructor({ record, runId, model, effort }) {
    this.record = record;
    this.runId = runId;
    this.model = model;
    this.effort = effort;
    this.ordinal = 0;
    this.arms = Object.fromEntries(ARMS.map((arm) => [arm, 0]));
    this.phases = new Map();
    this.stopped = false;
  }

  reserve({ arm, sessionId, slotId, phase, prompt, sequence, planningRound = null }) {
    requireCondition(!this.stopped, "admission_stopped");
    requireCondition(ARMS.includes(arm), "invalid_arm");
    requireCondition(PHASES.includes(phase) || phase === "canary", "invalid_model_phase");
    requireCondition(phase !== "handoff" || arm === "handoff", "phase_arm_mismatch");
    requireCondition(!["memory_plan", "memory_decision"].includes(phase)
      || arm === "pg_agmemory", "phase_arm_mismatch");
    requireCondition(phase !== "memory_plan" || Number.isInteger(planningRound)
      && planningRound >= 1 && planningRound <= 4, "invalid_planning_round");
    utf8(prompt, LIMITS.prompt);
    requireCondition(prompt.trim().length > 0, "empty_prompt");
    const key = `${slotId}\0${phase}`;
    const count = this.phases.get(key) ?? 0;
    const cap = phase === "work" ? LIMITS.work : phase === "memory_plan" ? 4
      : phase === "canary" ? 2 : 1;
    requireCondition(count < cap && this.ordinal < LIMITS.invocations
      && this.arms[arm] < LIMITS.bridgeInvocations, "invocation_limit");
    const callId = String(this.arms[arm] + 1).padStart(6, "0");
    const request = { format: "pgag-copilot-request-v1", call_id: callId, prompt };
    requireCondition(Buffer.byteLength(JSON.stringify(request) + "\n") <= LIMITS.bridgeRequest,
      "bridge_request_limit");
    const receipt = {
      global_ordinal: this.ordinal + 1,
      bridge_id: `${this.runId}-${arm}`,
      bridge_call_id: callId,
    };
    // A failed durable admission is uncertain, never a reusable reservation.
    this.ordinal += 1;
    this.arms[arm] += 1;
    this.phases.set(key, count + 1);
    try {
      this.record({
        kind: "model_admitted", session_id: sessionId, slot_id: slotId, phase,
        sequence, planning_round: planningRound, receipt,
        prompt_sha256: sha256(prompt), model: this.model, reasoning_effort: this.effort,
      });
    } catch (error) {
      this.stopped = true;
      throw error;
    }
    return { receipt, request };
  }

  stop() { this.stopped = true; }
}
