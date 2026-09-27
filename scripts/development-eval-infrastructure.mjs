import { generateKeyPairSync, randomBytes, sign } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import {
  cleanupFailure, EvaluationError, parseJson, remainingMilliseconds, requireCondition,
} from "./development-eval-protocol.mjs";
import { runProcess } from "./development-eval-sandbox.mjs";
import { privateDirectory, writeNew } from "./development-eval-transport.mjs";

const POSTGRES = "docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:"
  + "2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a";

export class NativeInfrastructure {
  constructor({ runId, directory, runtimeImage, record, runner = runProcess }) {
    requireCondition(/^pgag-dev-[0-9a-f]{24}$/.test(runId), "invalid_owned_run_id");
    this.runId = runId;
    this.directory = directory;
    this.image = runtimeImage;
    this.record = record;
    this.runner = runner;
    this.owned = new Set();
    this.credentials = new Set();
    this.api = `${runId}-api`;
    this.db = `${runId}-db`;
    this.bindings = [];
  }

  async inventory({ deadline = Infinity, signal, code = "run_deadline" } = {}) {
    const result = await this.runner("container", ["list", "--all", "--format", "json"],
      { timeoutMs: remainingMilliseconds(deadline, 30000, code), signal });
    requireCondition(result.exitCode === 0 && !result.timedOut && !result.overflow,
      "infrastructure_inventory_failed");
    requireCondition(!signal?.aborted && !result.cancelled, "operation_cancelled");
    const values = parseJson(result.stdout);
    requireCondition(Array.isArray(values), "infrastructure_inventory_invalid");
    return values;
  }

  async ownedRun(name, args, options = {}) {
    requireCondition(name.startsWith(`${this.runId}-`), "foreign_container_forbidden");
    requireCondition(!(await this.inventory(options)).some((row) => row.configuration?.id === name),
      "container_name_collision");
    this.record({ kind: "infrastructure_create_intent", name });
    this.owned.add(name);
    const result = await this.runner("container", ["run", "--name", name, ...args],
      { ...options, timeoutMs: remainingMilliseconds(options.deadline, options.timeoutMs) });
    requireCondition(result.exitCode === 0 && !result.timedOut && !result.overflow,
      "infrastructure_start_failed");
    return result;
  }

  async credentialFile(name, text) {
    const file = path.join(this.directory, name);
    this.credentials.add(file);
    const handle = await fs.open(file, "wx", 0o600);
    try { await handle.writeFile(text); await handle.sync(); }
    finally { await handle.close(); }
    return file;
  }

  async remove(name, { deadline = Date.now() + 15000 } = {}) {
    requireCondition(this.owned.has(name), "foreign_container_forbidden");
    try {
      const started = Date.now();
      const removed = await this.runner("container", ["rm", "--force", name],
        { timeoutMs: remainingMilliseconds(deadline, 10000, "owned_cleanup_failed") });
      const absent = !(await this.inventory({ deadline, code: "owned_cleanup_failed" }))
        .some((row) => row.configuration?.id === name);
      const elapsed = Date.now() - started;
      const graceExceeded = elapsed > 15000 || Date.now() > deadline;
      this.record({ kind: "infrastructure_removed", name, absent,
        removal_exit_code: removed.exitCode, elapsed_ms: elapsed, grace_exceeded: graceExceeded });
      if (absent) this.owned.delete(name);
      requireCondition(absent && !graceExceeded, "owned_cleanup_failed");
    } catch (error) { throw cleanupFailure(error, deadline); }
  }

  async start(projects, { signal, deadline = Infinity } = {}) {
    requireCondition(!signal?.aborted, "operation_cancelled");
    await privateDirectory(this.directory, { create: true });
    requireCondition((await fs.readdir(this.directory)).length === 0,
      "new_infrastructure_directory_required");
    const databaseSecret = randomBytes(32).toString("hex");
    const runtimeSecret = randomBytes(32).toString("hex");
    const dbFile = await this.credentialFile("database.env",
      `POSTGRES_PASSWORD=${databaseSecret}\nPOSTGRES_DB=pgag_development\n`);
    await this.ownedRun(this.db, [
      "--detach", "--cpus", "2", "--memory", "2G", "--env-file", dbFile, POSTGRES,
    ], { timeoutMs: 90000, signal, deadline });
    let ready = false;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      requireCondition(!signal?.aborted, "operation_cancelled");
      const result = await this.runner("container", [
        "exec", this.db, "pg_isready", "-t", "1", "-U", "postgres", "-d", "pgag_development",
      ], { timeoutMs: remainingMilliseconds(deadline, 3000), signal });
      if (result.exitCode === 0 && !result.timedOut) { ready = true; break; }
      await sleep(1000);
    }
    requireCondition(ready, "owned_database_not_ready");
    const db = (await this.inventory({ deadline, signal }))
      .find((row) => row.configuration?.id === this.db);
    const address = db?.status?.networks?.[0]?.ipv4Address?.split("/")[0];
    requireCondition(typeof address === "string" && /^[0-9.]+$/.test(address),
      "owned_database_address_missing");
    const adminUrl = `postgresql://postgres:${databaseSecret}@${address}:5432/pgag_development`;
    const setupFile = await this.credentialFile("setup.env",
      `PGAG_ADMIN_DATABASE_URL=${adminUrl}\nPGAG_DEVELOPMENT_OWNED_RUN=${this.runId}\n`
      + `PGAG_DEVELOPMENT_RUNTIME_PASSWORD=${runtimeSecret}\n`);
    const setupName = `${this.runId}-setup`;
    const setup = await this.ownedRun(setupName, [
      "--cpus", "1", "--memory", "1G", "--env-file", setupFile,
      this.image, "python", "/app/scripts/development-eval-native.py", "--run-id", this.runId,
      ...projects.flatMap((project) => ["--project", project]),
    ], { timeoutMs: 90000, signal, deadline });
    const provisioned = parseJson(setup.stdout);
    requireCondition(provisioned.format === "pgag-development-native-v1"
      && provisioned.run_id === this.runId && provisioned.bindings?.length === 6
      && provisioned.mini_swe_agent?.version === "2.4.6"
      && provisioned.mini_swe_agent.default_agent_sha256
        === "e8ef8aa365942d739c2ec5cb0879f60f377d2dc2de8ec670aaedf3bafb45a4c2",
    "invalid_provisioned_identities");
    this.bindings = provisioned.bindings;
    await writeNew(path.join(this.directory, "bindings.json"), provisioned);
    await this.remove(setupName, {
      deadline: Math.min(Date.now() + 15000, signal?.reason?.cleanupDeadline ?? Infinity),
    });
    const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    this.privateKey = privateKey;
    const pem = publicKey.export({ type: "spki", format: "pem" });
    const runtimeFile = await this.credentialFile("runtime.env",
      `PGAG_DATABASE_URL=postgresql://pgag_development_runtime:${runtimeSecret}`
      + `@${address}:5432/pgag_development\nPGAG_JWT_ISSUER=${this.runId}\n`
      + `PGAG_JWT_AUDIENCE=${this.runId}\n`);
    const controllerDirectory = path.join(this.directory, "controllers");
    await privateDirectory(controllerDirectory, { create: true });
    await this.ownedRun(this.api, [
      "--detach", "--cpus", "2", "--memory", "2G", "--read-only", "--cap-drop", "ALL",
      "--user", `${process.getuid()}:${process.getgid()}`, "--tmpfs", "/tmp:size=32m,mode=1777",
      "--mount", `type=bind,source=${controllerDirectory},target=/run/controllers`,
      "--env-file", runtimeFile, "-e", `PGAG_JWT_PUBLIC_KEY=${pem}`, this.image,
    ], { timeoutMs: 30000, signal, deadline });
    let apiReady = false;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      requireCondition(!signal?.aborted, "operation_cancelled");
      const result = await this.runner("container", ["exec", this.api, "python", "-I", "-c",
        "import json,urllib.request; "
        + "r=urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=1); "
        + "assert json.load(r)=={'status':'ready'}",
      ], { timeoutMs: remainingMilliseconds(deadline, 3000), signal });
      if (result.exitCode === 0 && !result.timedOut) { apiReady = true; break; }
      await sleep(1000);
    }
    requireCondition(apiReady, "owned_api_not_ready");
    for (const file of this.credentials) await fs.unlink(file);
    this.credentials.clear();
    this.record({ kind: "native_ready", api: this.api, database: this.db,
      bindings: this.bindings, permissions: ["read", "write"],
      mini_swe_agent: provisioned.mini_swe_agent });
    return provisioned;
  }

  binding(projectId, arm) {
    const binding = this.bindings.find((row) => row.project_id === projectId && row.arm === arm);
    requireCondition(binding !== undefined, "unknown_native_binding");
    return binding;
  }

  bearer(projectId, arm) {
    requireCondition(this.privateKey !== undefined, "native_not_ready");
    const subject = this.binding(projectId, arm).subject;
    const now = Math.floor(Date.now() / 1000);
    const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT" })).toString("base64url");
    const body = Buffer.from(JSON.stringify({
      sub: subject, iss: this.runId, aud: this.runId, iat: now - 10, exp: now + 14400,
    })).toString("base64url");
    const signed = `${header}.${body}`;
    return `${signed}.${sign("RSA-SHA256", Buffer.from(signed), this.privateKey)
      .toString("base64url")}`;
  }

  async storageStatus({ deadline = Infinity, signal } = {}) {
    requireCondition(this.owned.has(this.db), "owned_database_required");
    const query = "SELECT json_build_object('database_bytes',pg_database_size(current_database()),"
      + "'relations',(SELECT json_agg(json_build_object('relation',c.relname,"
      + "'bytes',pg_total_relation_size(c.oid)) ORDER BY c.relname)"
      + " FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
      + " WHERE n.nspname='memory' AND c.relkind='r'))";
    const result = await this.runner("container", [
      "exec", this.db, "psql", "-XqAt", "--single-transaction", "-v", "ON_ERROR_STOP=1",
      "-U", "postgres", "--dbname", "pgag_development",
      "--command", "SET TRANSACTION READ ONLY", "--command", query,
    ], { timeoutMs: remainingMilliseconds(deadline), signal });
    requireCondition(result.exitCode === 0 && !result.timedOut && !result.overflow,
      "owned_storage_measurement_failed");
    const value = parseJson(result.stdout, { integersOnly: true });
    requireCondition(Number.isSafeInteger(value.database_bytes) && value.database_bytes >= 0
      && Array.isArray(value.relations) && value.relations.every((row) =>
        typeof row.relation === "string" && Number.isSafeInteger(row.bytes) && row.bytes >= 0),
    "owned_storage_measurement_invalid");
    this.record({ kind: "owned_storage", database: this.db, ...value });
    return value;
  }

  async close({ deadline = Date.now() + 15000 } = {}) {
    const failures = [];
    for (const name of [...this.owned].reverse()) {
      try { await this.remove(name, { deadline }); }
      catch (error) { failures.push({ name, code: error.code ?? "owned_cleanup_failed" }); }
    }
    for (const file of this.credentials) {
      try { await fs.unlink(file); }
      catch (error) {
        if (error.code !== "ENOENT") failures.push({ name: path.basename(file), code: error.code });
      }
    }
    this.credentials.clear();
    this.privateKey = undefined;
    if (failures.length) {
      this.record({ kind: "infrastructure_cleanup_failed", failures });
      throw cleanupFailure(new EvaluationError("owned_cleanup_failed"), deadline);
    }
  }
}
