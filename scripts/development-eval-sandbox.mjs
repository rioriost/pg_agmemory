import { spawn } from "node:child_process";
import { performance } from "node:perf_hooks";
import {
  canonicalJson, cleanupFailure, EvaluationError, exactKeys, gradeCandidate, LIMITS,
  parseJson, remainingMilliseconds, requireCondition, safePath, sha256, utf8,
} from "./development-eval-protocol.mjs";

const HELPER = "/opt/pgag-eval/guest.py";
const CANDIDATE_MARKER = Buffer.from("PGAG-CANDIDATE-START-v1\n");
const ENVIRONMENT = [
  "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/home/task", "LANG=C.UTF-8",
  "PYTHONDONTWRITEBYTECODE=1", "TMPDIR=/tmp",
];

export function runProcess(executable, args, {
  input = Buffer.alloc(0), timeoutMs = 30000, maximum = 1048576, signal,
} = {}) {
  if (signal?.aborted) return Promise.resolve({
    stdout: Buffer.alloc(0), stderr: Buffer.alloc(0), exitCode: null, exitSignal: null,
    timedOut: false, overflow: false, cancelled: true, durationSeconds: 0,
  });
  return new Promise((resolve, reject) => {
    const started = performance.now();
    const child = spawn(executable, args, { stdio: ["pipe", "pipe", "pipe"], shell: false });
    const output = { stdout: [], stderr: [] };
    let bytes = 0;
    let timedOut = false;
    let overflow = false;
    let cancelled = false;
    let killTimer;
    const terminate = () => {
      child.kill("SIGTERM");
      killTimer ??= setTimeout(() => child.kill("SIGKILL"), 1000);
    };
    const abort = () => { cancelled = true; terminate(); };
    const timer = setTimeout(() => { timedOut = true; terminate(); }, timeoutMs);
    if (signal?.aborted) abort();
    signal?.addEventListener("abort", abort, { once: true });
    for (const stream of ["stdout", "stderr"]) {
      child[stream].on("data", (chunk) => {
        const remaining = Math.max(0, maximum - bytes);
        if (remaining) output[stream].push(chunk.subarray(0, remaining));
        bytes += chunk.length;
        if (bytes > maximum) { overflow = true; terminate(); }
      });
    }
    child.stdin.on("error", (error) => {
      if (error.code !== "EPIPE") { terminate(); reject(error); }
    });
    child.stdin.end(input);
    const cleanup = () => {
      clearTimeout(timer);
      clearTimeout(killTimer);
      signal?.removeEventListener("abort", abort);
    };
    child.once("error", (error) => { cleanup(); reject(error); });
    child.once("close", (exitCode, exitSignal) => {
      cleanup();
      resolve({
        stdout: Buffer.concat(output.stdout), stderr: Buffer.concat(output.stderr),
        exitCode, exitSignal, timedOut, overflow, cancelled,
        durationSeconds: (performance.now() - started) / 1000,
      });
    });
  });
}

export function taskArguments(name, argv, interactive = false) {
  return [
    "exec", ...(interactive ? ["--interactive"] : []),
    "--user", "10001:10001", "--workdir", "/workspace",
    "--ulimit", "nproc=64:64", "--ulimit", "fsize=8388608:8388608",
    name, "/usr/bin/env", "-i", ...ENVIRONMENT, ...argv,
  ];
}

export function validateArtifact(value, allowedPaths) {
  exactKeys(value, ["status", "files", "bytes", "tree_sha256"]);
  requireCondition(value.status === "ok" && Array.isArray(value.files)
    && value.files.length <= LIMITS.files, "invalid_artifact");
  const allowed = new Set(allowedPaths.map(safePath));
  const names = new Set();
  let total = 0;
  for (const row of value.files) {
    exactKeys(row, ["path", "size", "sha256", "content_base64"]);
    safePath(row.path);
    requireCondition(allowed.has(row.path) && !names.has(row.path), "unexpected_artifact_path");
    names.add(row.path);
    requireCondition(typeof row.content_base64 === "string"
      && /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(row.content_base64),
    "invalid_artifact_encoding");
    const bytes = Buffer.from(row.content_base64, "base64");
    requireCondition(Number.isSafeInteger(row.size) && row.size === bytes.length
      && row.sha256 === sha256(bytes), "artifact_identity_mismatch");
    total += bytes.length;
  }
  requireCondition(total === value.bytes && total <= LIMITS.artifact, "artifact_limit");
  const manifest = value.files.map(({ path, size, sha256: hash }) => ({ path, size, sha256: hash }));
  const ordered = [...manifest].sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  requireCondition(canonicalJson(manifest) === canonicalJson(ordered)
    && sha256(canonicalJson(manifest)) === value.tree_sha256, "artifact_tree_mismatch");
  return value;
}

export class ExecutionGuest {
  constructor({ name, image, record, runner = runProcess }) {
    requireCondition(/^pgag-dev-[a-z0-9-]{8,100}$/.test(name), "invalid_owned_guest_name");
    requireCondition(typeof image === "string" && image.length > 0, "execution_image_required");
    this.name = name;
    this.image = image;
    this.record = record;
    this.runner = runner;
    this.owned = false;
    this.closed = false;
  }

  async start(files, { signal, deadline = Infinity } = {}) {
    requireCondition(!signal?.aborted, "operation_cancelled");
    requireCondition(!this.owned && !this.closed, "guest_already_started");
    const inventory = await this.inventory({ signal, deadline });
    requireCondition(!inventory.some((row) => row.configuration?.id === this.name),
      "owned_guest_name_collision");
    this.record({ kind: "guest_create_intent", name: this.name, image: this.image });
    this.owned = true;
    const created = await this.runner("container", [
      "run", "--detach", "--name", this.name, "--network", "none", "--read-only",
      "--cap-drop", "ALL", "--cpus", "1", "--memory", "512M",
      "--tmpfs", "/workspace:size=64m,mode=1777",
      "--tmpfs", "/home/task:size=8m,mode=1777",
      "--tmpfs", "/tmp:size=16m,mode=1777",
      this.image,
    ], { timeoutMs: remainingMilliseconds(deadline, 30000), signal });
    requireCondition(created.exitCode === 0 && !created.timedOut && !created.overflow,
      "guest_start_failed");
    this.started = true;
    const probe = await this.helper("probe", null, { signal, deadline });
    requireCondition(probe.uid === 0 && canonicalJson(probe.network_interfaces) === '["lo"]'
      && probe.routes.length === 0 && /^0+$/.test(probe.effective_capabilities)
      && probe.root_mount.some((line) => line.split(" ")[3].split(",").includes("ro"))
      && probe.workspace_mount.some((line) => line.includes("tmpfs")
        && line.includes("size=65536k"))
      && probe.task_processes.length === 0, "guest_isolation_failed");
    this.record({ kind: "guest_isolation_verified", name: this.name, probe });
    const seeded = await this.helper("seed", { files }, { signal, deadline });
    const manifest = files.map(({ path, size, sha256: hash }) => ({ path, size, sha256: hash }));
    requireCondition(seeded.files === files.length
      && seeded.bytes === files.reduce((sum, row) => sum + row.size, 0)
      && seeded.tree_sha256 === sha256(canonicalJson(manifest)), "guest_seed_mismatch");
    this.startingTree = seeded.tree_sha256;
    this.record({ kind: "guest_seeded", name: this.name, ...seeded });
    return seeded;
  }

  async inventory({ signal, deadline = Infinity, code = "run_deadline" } = {}) {
    const result = await this.runner("container", ["list", "--all", "--format", "json"],
      { signal, timeoutMs: remainingMilliseconds(deadline, 30000, code) });
    requireCondition(result.exitCode === 0 && !result.timedOut && !result.overflow,
      "guest_inventory_failed");
    const inventory = parseJson(result.stdout);
    requireCondition(Array.isArray(inventory), "guest_inventory_invalid");
    return inventory;
  }

  async helper(operation, body = null, { signal, deadline = Infinity } = {}) {
    requireCondition(this.owned && !this.closed, "guest_not_active");
    const result = await this.runner("container", [
      "exec", "--interactive", "--user", operation === "seed" ? "10001:10001" : "0:0",
      this.name, "python", "-I", HELPER, operation,
    ], { input: body === null ? Buffer.alloc(0) : Buffer.from(JSON.stringify(body)),
      maximum: 2 * LIMITS.artifact, timeoutMs: remainingMilliseconds(deadline, 10000), signal });
    requireCondition(!result.cancelled && !signal?.aborted, "operation_cancelled");
    requireCondition(!result.timedOut && !result.overflow, "guest_helper_incomplete");
    const response = parseJson(result.stdout, { maximum: 2 * LIMITS.artifact });
    if (result.exitCode !== 0 || response.status !== "ok") {
      this.record({ kind: "guest_helper_failed", name: this.name, operation,
        code: typeof response.code === "string" ? response.code : "unknown" });
      const error = new EvaluationError("guest_helper_failed");
      error.guest_code = typeof response.code === "string" ? response.code : "unknown";
      error.guest_operation = operation;
      throw error;
    }
    return response;
  }

  async execute(command, { signal, timeoutMs = 30000 } = {}) {
    requireCondition(this.owned && !this.closed, "guest_not_active");
    utf8(command, LIMITS.command);
    requireCondition(command.trim().length > 0, "empty_command");
    requireCondition(!/\$\{[^}]*@P\}|\$\{!/.test(command), "unsafe_shell_expansion");
    this.record({ kind: "guest_execute_intent", name: this.name, command_sha256: sha256(command) });
    const result = await this.runner("container",
      taskArguments(this.name, ["/bin/sh", "-c", command]),
      { timeoutMs: Math.min(30000, timeoutMs), maximum: LIMITS.capture, signal });
    this.record({ kind: "guest_execute_result", name: this.name,
      exit_code: result.exitCode, timed_out: result.timedOut, overflow: result.overflow,
      cancelled: result.cancelled, duration_seconds: result.durationSeconds,
      stdout_base64: result.stdout.toString("base64"), stderr_base64: result.stderr.toString("base64") });
    if (result.timedOut || result.cancelled || result.overflow || result.exitCode === null) {
      const cleanupDeadline = Math.min(Date.now() + 15000, signal?.reason?.cleanupDeadline ?? Infinity);
      await this.close({ deadline: cleanupDeadline });
      const error = new EvaluationError(result.timedOut ? "command_timeout" : result.cancelled
        ? "command_cancelled" : result.overflow ? "command_output_limit" : "command_unknown");
      error.cleanupDeadline = cleanupDeadline;
      throw error;
    }
    return result;
  }

  async export(allowedPaths, { signal, deadline = Infinity } = {}) {
    requireCondition(this.owned && !this.closed, "guest_not_active");
    const stopped = await this.runner("container",
      taskArguments(this.name, ["python", "-I", HELPER, "stop-tasks"]),
      { timeoutMs: remainingMilliseconds(deadline, 5000), maximum: LIMITS.capture, signal });
    requireCondition(!stopped.cancelled && !signal?.aborted, "operation_cancelled");
    requireCondition(stopped.exitCode === 0 && !stopped.timedOut && !stopped.overflow,
      "task_quiescence_failed");
    const artifact = validateArtifact(await this.helper("export", { allowed_paths: allowedPaths },
      { signal, deadline }),
      allowedPaths);
    this.record({ kind: "artifact_captured", name: this.name,
      tree_sha256: artifact.tree_sha256, bytes: artifact.bytes, files: artifact.files.length });
    await this.close({ deadline: Math.min(Date.now() + 15000,
      signal?.reason?.cleanupDeadline ?? Infinity) });
    return artifact;
  }

  async grade(entryPoint, inputBytes, expectedBytes, { signal, deadline = Infinity } = {}) {
    requireCondition(this.owned && !this.closed, "guest_not_active");
    safePath(entryPoint);
    const input = parseJson(inputBytes, { maximum: LIMITS.input, integersOnly: true });
    const stdin = Buffer.from(canonicalJson(input) + "\n");
    requireCondition(stdin.length <= LIMITS.input, "candidate_input_limit");
    const result = await this.runner("container",
      taskArguments(this.name, [
        "python", "-I", HELPER, "candidate", "--entry-point", entryPoint,
      ], true),
      { input: stdin, timeoutMs: remainingMilliseconds(deadline, 5000),
        maximum: LIMITS.capture + CANDIDATE_MARKER.length, signal });
    const started = result.stdout.subarray(0, CANDIDATE_MARKER.length).equals(CANDIDATE_MARKER);
    const stdout = started ? result.stdout.subarray(CANDIDATE_MARKER.length) : result.stdout;
    this.lastCandidate = {
      candidate_started: started, exit_code: result.exitCode,
      stdout_sha256: sha256(stdout), stderr_sha256: sha256(result.stderr),
      duration_seconds: result.durationSeconds,
    };
    this.record({ kind: "candidate_execution", name: this.name, ...this.lastCandidate,
      stdout_base64: result.stdout.toString("base64"), stderr_base64: result.stderr.toString("base64"),
      timed_out: result.timedOut, cancelled: result.cancelled, overflow: result.overflow });
    const cleanupDeadline = Math.min(Date.now() + 15000, signal?.reason?.cleanupDeadline ?? Infinity);
    await this.close({ deadline: cleanupDeadline });
    requireCondition(!signal?.aborted && !result.cancelled, "operation_cancelled");
    remainingMilliseconds(deadline);
    const grade = gradeCandidate({
      ...result, stdout, started: started && !result.cancelled,
    }, expectedBytes);
    const details = {
      ...grade, artifact_sha256: this.startingTree,
      guest_started: true, candidate_started: started && !result.cancelled,
      exit_code: result.exitCode, stdout_sha256: sha256(stdout), stderr_sha256: sha256(result.stderr),
      duration_seconds: result.durationSeconds,
    };
    this.record({ kind: "candidate_graded", name: this.name, ...grade,
      stdout_base64: result.stdout.toString("base64"), stderr_base64: result.stderr.toString("base64"),
      input_sha256: sha256(stdin), ...details });
    return details;
  }

  async close({ deadline = Date.now() + 15000 } = {}) {
    if (!this.owned || this.closed) return;
    try {
      const started = Date.now();
      const removed = await this.runner("container", ["rm", "--force", this.name],
        { timeoutMs: remainingMilliseconds(deadline, 10000, "owned_cleanup_failed") });
      const absent = !(await this.inventory({ deadline, code: "owned_cleanup_failed" }))
        .some((row) => row.configuration?.id === this.name);
      const elapsed = Date.now() - started;
      const graceExceeded = elapsed > 15000 || Date.now() > deadline;
      this.record({ kind: "guest_cleanup", name: this.name, absent,
        removal_exit_code: removed.exitCode, removal_timed_out: removed.timedOut,
        elapsed_ms: elapsed, grace_exceeded: graceExceeded });
      this.closed = absent;
      requireCondition(absent && !graceExceeded, "owned_cleanup_failed");
    } catch (error) { throw cleanupFailure(error, deadline); }
  }
}
