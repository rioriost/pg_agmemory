import assert from "node:assert/strict";
import { randomBytes, verify } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { NativeInfrastructure } from "./development-eval-infrastructure.mjs";
import { parseJson } from "./development-eval-protocol.mjs";
import { runProcess } from "./development-eval-sandbox.mjs";

test("Native infrastructure cannot remove an unowned container", async () => {
  const infrastructure = new NativeInfrastructure({
    runId: `pgag-dev-${"0".repeat(24)}`, directory: "/not-created",
    runtimeImage: "not-used", record: () => {},
    runner: async () => { throw new Error("must not run"); },
  });
  await assert.rejects(infrastructure.remove("buildkit"), /foreign_container_forbidden/);
});

test("owned PostgreSQL provisioning and loopback API use restricted scoped identities", {
  skip: !process.env.PGAG_DEVELOPMENT_RUNTIME_IMAGE, timeout: 180000,
}, async () => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "pgag-dev-native-test-")));
  const records = [];
  const infrastructure = new NativeInfrastructure({
    runId: `pgag-dev-${randomBytes(12).toString("hex")}`, directory: path.join(root, "runtime"),
    runtimeImage: process.env.PGAG_DEVELOPMENT_RUNTIME_IMAGE,
    record: (event) => records.push(event),
  });
  try {
    const result = await infrastructure.start(["toy-a", "toy-b"]);
    assert.equal(result.bindings.length, 6);
    assert.equal(new Set(result.bindings.map((row) => row.scope_id)).size, 6);
    const token = infrastructure.bearer("toy-a", "pg_agmemory");
    const [header, body, signature] = token.split(".");
    assert.equal(JSON.parse(Buffer.from(body, "base64url")).sub,
      infrastructure.binding("toy-a", "pg_agmemory").subject);
    assert.equal(verify("RSA-SHA256", Buffer.from(`${header}.${body}`),
      infrastructure.privateKey, Buffer.from(signature, "base64url")), true);
    const permissions = await runProcess("container", [
      "exec", infrastructure.db, "psql", "-U", "postgres", "-d", "pgag_development",
      "-At", "-c", "SELECT count(*) FROM memory.scope_member "
        + "WHERE permissions = ARRAY['read','write']::text[];",
    ]);
    assert.equal(permissions.exitCode, 0);
    assert.equal(permissions.stdout.toString().trim(), "6");
    const role = await runProcess("container", [
      "exec", infrastructure.db, "psql", "-U", "postgres", "-d", "pgag_development",
      "-At", "-c", "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls "
        + "FROM pg_roles WHERE rolname = 'pgag_development_runtime';",
    ]);
    assert.equal(role.stdout.toString().trim(), "f");
    const capabilities = await runProcess("container", [
      "exec", infrastructure.api, "/usr/bin/env", "-i", "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin",
      `PGAG_TEST_TOKEN=${token}`, "python", "-c",
      "import asyncio,os; from pg_agmemory.sdk import AsyncMemoryClient\n"
        + "async def check():\n"
        + "  assert 'PGAG_DATABASE_URL' not in os.environ\n"
        + "  async with AsyncMemoryClient('http://127.0.0.1:8000',os.environ['PGAG_TEST_TOKEN']):\n"
        + "    print('scoped_sdk_ready')\n"
        + "asyncio.run(check())",
    ]);
    assert.equal(capabilities.exitCode, 0, capabilities.stderr.toString());
    assert.equal(capabilities.stdout.toString().trim(), "scoped_sdk_ready");
    const session = path.join(infrastructure.directory, "controllers", "publication-test");
    await fs.mkdir(path.join(session, "staging"), { recursive: true, mode: 0o700 });
    await fs.mkdir(path.join(session, "ipc", "replies"), { recursive: true, mode: 0o700 });
    const staged = path.join(session, "staging", "000001.json");
    const published = path.join(session, "ipc", "replies", "000001.json");
    await fs.writeFile(staged, '{"probe":1}\n', { mode: 0o600 });
    const publishArgs = [
      "exec", infrastructure.api, "python", "-I", "/app/scripts/development-eval-native.py",
      "--publish-reply", "/run/controllers/publication-test/staging/000001.json",
      "/run/controllers/publication-test/ipc/replies/000001.json",
    ];
    const publication = await runProcess("container", publishArgs);
    assert.equal(publication.exitCode, 0, publication.stdout.toString());
    assert.equal((await fs.stat(published)).nlink, 1);
    await fs.writeFile(staged, '{"probe":2}\n', { mode: 0o600 });
    const overwrite = await runProcess("container", publishArgs);
    assert.notEqual(overwrite.exitCode, 0);
    assert.equal(JSON.parse(await fs.readFile(published)).probe, 1);
    assert.ok(!(await fs.readdir(infrastructure.directory)).some((file) => file.endsWith(".env")));
    assert.ok(records.some((row) => row.kind === "native_ready"));
  } finally {
    await infrastructure.close();
    const list = await runProcess("container", ["list", "--all", "--format", "json"]);
    assert.ok(!parseJson(list.stdout).some((row) =>
      row.configuration?.id.startsWith(infrastructure.runId)));
    await fs.rm(root, { recursive: true });
  }
});
