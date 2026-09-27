import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import test from "node:test";
import { canonicalJson, sha256 } from "./development-eval-protocol.mjs";
import {
  ExecutionGuest, runProcess, taskArguments, validateArtifact,
} from "./development-eval-sandbox.mjs";

function artifact(files) {
  const records = Object.entries(files).sort(([a], [b]) => a < b ? -1 : 1)
    .map(([path, text]) => {
      const bytes = Buffer.from(text);
      return { path, size: bytes.length, sha256: sha256(bytes),
        content_base64: bytes.toString("base64") };
    });
  return {
    status: "ok", files: records, bytes: records.reduce((n, row) => n + row.size, 0),
    tree_sha256: sha256(canonicalJson(records.map(({ path, size, sha256: hash }) =>
      ({ path, size, sha256: hash })))),
  };
}

test("artifact validation verifies actual bytes, hashes, paths and ordering", () => {
  const value = artifact({ "main.py": "pass\n" });
  assert.equal(validateArtifact(value, ["main.py"]), value);
  for (const changed of [
    { ...value, bytes: 0 },
    { ...value, tree_sha256: "0".repeat(64) },
    { ...value, files: [value.files[0], value.files[0]] },
    { ...value, files: [{ ...value.files[0], content_base64: "invalid=" }] },
    { ...value, files: [{ ...value.files[0], path: "../main.py" }] },
  ]) assert.throws(() => validateArtifact(changed, ["main.py"]));
});

test("model commands are guest arguments, never host shell text or environment overrides", () => {
  const command = 'printf "$HOME"; echo "$(uname)"';
  const args = taskArguments("pgag-dev-owned-12345678", ["/bin/sh", "-c", command]);
  assert.equal(args.at(-1), command);
  assert.deepEqual(args.slice(-3), ["/bin/sh", "-c", command]);
  assert.ok(args.includes("10001:10001"));
  assert.ok(args.includes("-i"));
  assert.ok(args.includes("HOME=/home/task"));
});

test("process runner captures bounded stdout/stderr and real exit codes", async () => {
  const result = await runProcess(process.execPath, [
    "-e", 'process.stdout.write("a");process.stderr.write("b");process.exitCode=7;',
  ]);
  assert.equal(result.stdout.toString(), "a");
  assert.equal(result.stderr.toString(), "b");
  assert.equal(result.exitCode, 7);
  const overflow = await runProcess(process.execPath,
    ["-e", 'process.stdout.write("a".repeat(10000));'], { maximum: 100 });
  assert.equal(overflow.overflow, true);
  assert.equal(overflow.stdout.length, 100);
  const timeout = await runProcess(process.execPath,
    ["-e", "setInterval(()=>{},1000)"], { timeoutMs: 50 });
  assert.equal(timeout.timedOut, true);
});

test("cleanup is acknowledged only by a successful complete inventory", async () => {
  const name = "pgag-dev-owned-12345678";
  const records = [];
  let remains = true;
  const runner = async (_exe, args) => ({
    exitCode: args[0] === "rm" ? 1 : 0, timedOut: false, overflow: false,
    stdout: Buffer.from(args[0] === "list"
      ? JSON.stringify(remains ? [{ configuration: { id: name } }] : []) : ""),
    stderr: Buffer.alloc(0),
  });
  const guest = new ExecutionGuest({ name, image: "owned:image",
    record: (event) => records.push(event), runner });
  guest.owned = true;
  await assert.rejects(guest.close(), /owned_cleanup_failed/);
  assert.equal(guest.closed, false);
  remains = false;
  await guest.close();
  assert.equal(guest.closed, true);
  assert.equal(records.at(-1).removal_exit_code, 1);
  assert.equal(records.at(-1).absent, true);
});

test("failed inventories never masquerade as successful removal", async () => {
  const guest = new ExecutionGuest({
    name: "pgag-dev-owned-12345678", image: "owned:image", record: () => {},
    runner: async () => ({ exitCode: 1, timedOut: false, overflow: false,
      stdout: Buffer.from("[]"), stderr: Buffer.alloc(0) }),
  });
  guest.owned = true;
  await assert.rejects(guest.close(), /guest_inventory_failed/);
  assert.equal(guest.closed, false);
});

test("acknowledged removal after the cleanup grace still invalidates execution", async (t) => {
  let now = 1000;
  t.mock.method(Date, "now", () => now);
  const guest = new ExecutionGuest({
    name: "pgag-dev-owned-12345678", image: "owned:image", record: () => {},
    runner: async (_executable, args) => {
      if (args[0] === "list") now += 15001;
      return { exitCode: 0, timedOut: false, overflow: false, stdout: Buffer.from("[]") };
    },
  });
  guest.owned = true;
  await assert.rejects(guest.close(), /owned_cleanup_failed/);
  assert.equal(guest.closed, true);
});

test("startup expiry prevents helper execution after the deadline", async (t) => {
  let now = 0;
  t.mock.method(Date, "now", () => now);
  const calls = [];
  const guest = new ExecutionGuest({
    name: "pgag-dev-deadline-12345678", image: "unit", record: () => {},
    runner: async (_executable, args, options) => {
      calls.push(args[0]);
      assert.ok(options.timeoutMs <= 1000);
      if (args[0] === "run") now = 1001;
      return { exitCode: 0, stdout: Buffer.from("[]"), timedOut: false, overflow: false };
    },
  });
  await assert.rejects(guest.start([], { deadline: 1000 }), /run_deadline/);
  assert.deepEqual(calls, ["list", "run"]);
});

test("export expiry prevents reading an artifact after quiescence consumes the deadline", async (t) => {
  let now = 0;
  t.mock.method(Date, "now", () => now);
  const calls = [];
  const guest = new ExecutionGuest({
    name: "pgag-dev-deadline-12345678", image: "unit", record: () => {},
    runner: async (_executable, args, options) => {
      calls.push(args);
      assert.equal(options.timeoutMs, 1000);
      now = 1001;
      return { exitCode: 0, stdout: Buffer.alloc(0), timedOut: false, overflow: false };
    },
  });
  guest.owned = true;
  await assert.rejects(guest.export(["main.py"], { deadline: 1000 }), /run_deadline/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].at(-1), "stop-tasks");
});

test("cleanup inventory receives only the remaining shared grace", async (t) => {
  let now = 0;
  t.mock.method(Date, "now", () => now);
  const timeouts = [];
  const guest = new ExecutionGuest({
    name: "pgag-dev-deadline-12345678", image: "unit", record: () => {},
    runner: async (_executable, args, options) => {
      timeouts.push(options.timeoutMs);
      now = args[0] === "rm" ? 800 : 1001;
      return { exitCode: 0, stdout: Buffer.from("[]"), timedOut: false, overflow: false };
    },
  });
  guest.owned = true;
  await assert.rejects(guest.close({ deadline: 1000 }), /owned_cleanup_failed/);
  assert.deepEqual(timeouts, [1000, 200]);
  assert.equal(guest.closed, true);
});

test("owned guest uses read-only networkless runtime, root helper and unprivileged candidate", {
  skip: !process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
  timeout: 120000,
}, async () => {
  const image = process.env.PGAG_DEVELOPMENT_GUEST_IMAGE;
  const nonce = randomBytes(6).toString("hex");
  const records = [];
  const work = new ExecutionGuest({
    name: `pgag-dev-work-${nonce}`, image, record: (event) => records.push(event),
  });

  let captured;
  try {
    await work.start(artifact({ "main.py": 'print("not yet")\n' }).files);
    const identity = await work.execute(
      "python -c 'import os; print(os.getuid()); print(open(\"/proc/net/route\").read())'",
    );
    assert.match(identity.stdout.toString(), /^10001\n/);
    await work.execute(
      "printf 'import json,sys\\nvalue=json.load(sys.stdin)\\nprint(json.dumps(value))\\n' > main.py",
    );
    captured = await work.export(["main.py"]);
    assert.equal(work.closed, true);
  } finally { await work.close(); }
  assert.ok(records.some((row) => row.kind === "guest_isolation_verified"));
  assert.ok(captured);
  const grader = new ExecutionGuest({
    name: `pgag-dev-grade-${nonce}`, image, record: (event) => records.push(event),
  });
  try {
    await grader.start(captured.files);
    const result = await grader.grade("main.py", '{"value":17}', '{"value":17}');
    assert.equal(result.status, "passed");
    assert.equal(grader.closed, true);
  } finally { await grader.close(); }
});

test("task UID can atomically replace, rename and delete seeded files and directories", {
  skip: !process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
  timeout: 120000,
}, async () => {
  const guest = new ExecutionGuest({
    name: `pgag-dev-edit-${randomBytes(8).toString("hex")}`,
    image: process.env.PGAG_DEVELOPMENT_GUEST_IMAGE, record: () => {},
  });
  try {
    await guest.start(artifact({ "main.py": "print(0)\n", "pkg/helper.py": "VALUE = 1\n" }).files);
    const result = await guest.execute("python - <<'PY'\n"
      + "import os\nfrom pathlib import Path\n"
      + "for name in ('main.py', 'pkg', 'pkg/helper.py'):\n"
      + "    assert Path(name).stat().st_uid == 10001\n"
      + "Path('main.py').write_text('print(1)\\n')\n"
      + "Path('replacement.py').write_text('print(2)\\n')\n"
      + "os.replace('replacement.py', 'main.py')\n"
      + "Path('main.py').rename('renamed.py')\nPath('renamed.py').unlink()\n"
      + "Path('pkg/helper.py').rename('pkg/other.py')\nPath('pkg/other.py').unlink()\n"
      + "Path('pkg').rename('renamed_pkg')\nPath('renamed_pkg').rmdir()\n"
      + "Path('main.py').write_text('print(3)\\n')\nprint('ok')\nPY");
    assert.equal(result.exitCode, 0, result.stderr.toString());
    assert.equal(result.stdout.toString(), "ok\n");
    const captured = await guest.export(["main.py", "pkg/helper.py"]);
    assert.deepEqual(captured.files.map((row) => row.path), ["main.py"]);
  } finally { await guest.close(); }
});

test("orphaned task processes cannot continue writing during artifact capture", {
  skip: !process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
  timeout: 60000,
}, async () => {
  const guest = new ExecutionGuest({
    name: `pgag-dev-quiesce-${randomBytes(6).toString("hex")}`,
    image: process.env.PGAG_DEVELOPMENT_GUEST_IMAGE, record: () => {},
  });
  try {
    await guest.start(artifact({ "main.py": "pass\n" }).files);
    const result = await guest.execute(
      "python -c 'import subprocess,sys; subprocess.Popen([sys.executable,\"-c\","
      + "\"import time; time.sleep(120)\"],start_new_session=True,"
      + "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)'",
    );
    assert.equal(result.exitCode, 0);
    const captured = await guest.export(["main.py"]);
    assert.equal(Buffer.from(captured.files[0].content_base64, "base64").toString(), "pass\n");
    assert.equal(guest.closed, true);
  } finally { await guest.close(); }
});

test("a zombie main thread cannot hide a live worker from quiescence checks", {
  skip: !process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
  timeout: 60000,
}, async () => {
  const guest = new ExecutionGuest({
    name: `pgag-dev-thread-group-${randomBytes(6).toString("hex")}`,
    image: process.env.PGAG_DEVELOPMENT_GUEST_IMAGE, record: () => {},
  });
  try {
    await guest.start(artifact({ "main.py": "pass\n" }).files);
    const result = await guest.execute(`python - <<'PY'
import subprocess,sys
code='''import ctypes,os,threading,time
from pathlib import Path
def work():
    for attempt in range(500):
        status=Path(f"/proc/{os.getpid()}/status").read_text().splitlines()
        if any(line.startswith("State:") and line.split()[1]=="Z" for line in status):
            break
        time.sleep(0.01)
    else:
        raise RuntimeError("main_thread_did_not_exit")
    Path("/tmp/thread-heartbeat").write_text("-1")
    print("ready",flush=True)
    for index in range(600):
        Path("/tmp/thread-heartbeat.tmp").write_text(str(index))
        os.replace("/tmp/thread-heartbeat.tmp","/tmp/thread-heartbeat")
        time.sleep(0.1)
threading.Thread(target=work).start()
ctypes.CDLL(None).pthread_exit(None)
'''
child=subprocess.Popen([sys.executable,"-c",code],stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,text=True)
assert child.stdout.readline()=="ready\\n"
print(child.pid)
PY`);
    assert.equal(result.exitCode, 0);
    const pid = Number(result.stdout.toString().trim());
    assert.ok(Number.isSafeInteger(pid) && pid > 1);
    const inspect = async (advance = false) => {
      const observed = await guest.runner("container", [
        "exec", "--user", "0:0", guest.name, "python", "-I", "-c", `import json,time
from pathlib import Path
heartbeat=Path("/tmp/thread-heartbeat")
before=int(heartbeat.read_text())
after=before
deadline=time.monotonic()+5
while ${advance ? 1 : 0} and after==before and time.monotonic()<deadline:
    time.sleep(0.05)
    after=int(heartbeat.read_text())
try:
    values=dict(line.split(":",1) for line in Path("/proc/${pid}/status").read_text().splitlines())
except FileNotFoundError:
    values=None
print(json.dumps({"exists":values is not None,
    "state":values["State"].strip().split()[0] if values else None,
    "threads":int(values["Threads"]) if values else None,
    "before":before,"after":after}))
`,
      ], { timeoutMs: 10000 });
      assert.equal(observed.exitCode, 0, observed.stderr.toString());
      return JSON.parse(observed.stdout);
    };
    const running = await inspect(true);
    assert.equal(running.state, "Z");
    assert.ok(running.threads >= 2);
    assert.ok(running.after > running.before);
    await assert.rejects(guest.helper("probe"), (error) =>
      error.code === "guest_helper_failed" && error.guest_code === "task_processes_still_present");
    let stoppedBeforeRemoval = false;
    const close = guest.close.bind(guest);
    guest.close = async (options) => {
      if (guest.closed) return;
      try {
        const stopped = await inspect();
        assert.ok(!stopped.exists || ["Z", "X"].includes(stopped.state) && stopped.threads === 1);
        assert.ok(stopped.after < 599, "worker must stop before natural expiry");
        stoppedBeforeRemoval = true;
      } finally { await close(options); }
    };
    const captured = await guest.export(["main.py"]);
    assert.equal(captured.files[0].sha256, sha256("pass\n"));
    assert.equal(stoppedBeforeRemoval, true);
    assert.equal(guest.closed, true);
  } finally { await guest.close(); }
});

test("a started candidate timeout is a failed check and the guest is removed", {
  skip: !process.env.PGAG_DEVELOPMENT_GUEST_IMAGE,
  timeout: 60000,
}, async () => {
  const guest = new ExecutionGuest({
    name: `pgag-dev-timeout-${randomBytes(6).toString("hex")}`,
    image: process.env.PGAG_DEVELOPMENT_GUEST_IMAGE, record: () => {},
  });
  try {
    await guest.start(artifact({ "main.py": "import time\ntime.sleep(60)\n" }).files);
    const result = await guest.grade("main.py", "{}", "{}");
    assert.equal(result.status, "failed");
    assert.equal(result.reason, "candidate_timeout");
    assert.equal(guest.closed, true);
  } finally { await guest.close(); }
});
