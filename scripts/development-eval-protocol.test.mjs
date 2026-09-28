import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalJson, gradeCandidate, InvocationLedger, LIMITS, MAINTENANCE_PROTOCOL, parseJson, PROTOCOL,
  RETRIEVAL_POLICY, safePath, validateRequest, validateUsage, WORK_PROTOCOL,
} from "./development-eval-protocol.mjs";

test("work and maintenance revisions preserve retrieval and decoded command budgets", () => {
  assert.equal(WORK_PROTOCOL, "development-work-v2");
  assert.equal(MAINTENANCE_PROTOCOL, "development-maintenance-v3");
  assert.equal(RETRIEVAL_POLICY, "development-retrieval-v2");
  assert.equal(LIMITS.command, 8192);
  assert.equal(LIMITS.work, 16);
  const prefix = "python - <<'PY'\n#";
  const suffix = "\nPY";
  const command = prefix + "x".repeat(8192 - Buffer.byteLength(prefix + suffix)) + suffix;
  const request = {
    protocol: PROTOCOL, session_id: "session-a", sequence: 1,
    operation: "execute", body: { command },
  };
  assert.ok(Buffer.byteLength(JSON.stringify(request)) > 8192);
  assert.deepEqual(validateRequest(JSON.parse(JSON.stringify(request)), "session-a", 1), request);
  assert.throws(() => validateRequest({
    ...request, body: { command: command + "x" },
  }, "session-a", 1));
  const multibyte = prefix + "界".repeat(2730) + suffix;
  assert.ok(multibyte.length < 8192 && Buffer.byteLength(multibyte) > 8192);
  assert.throws(() => validateRequest({
    ...request, body: { command: multibyte },
  }, "session-a", 1));
});

test("strict JSON compares objects without key order but preserves types and strings", () => {
  assert.equal(canonicalJson(parseJson('{"b":2,"a":[1,true]}')),
    '{"a":[1,true],"b":2}');
  const grade = (actual, expected) => gradeCandidate({
    stdout: Buffer.from(actual), stderr: Buffer.alloc(0), exitCode: 0,
    started: true, timedOut: false, overflow: false,
  }, Buffer.from(expected));
  assert.equal(grade(' {"a":1,"b":2}\n', '{"b":2,"a":1}').status, "passed");
  for (const [actual, expected] of [["true", "1"], ['" x "', '"x"'],
    ['"é"', '"é"'], ["[2,1]", "[1,2]"]]) {
    assert.equal(grade(actual, expected).reason, "mismatch");
  }
});

test("candidate JSON rejects ambiguous syntax, unsafe integers and invalid Unicode", () => {
  const invalid = [
    '{"x":1,"\\u0078":2}', "1.0", "1e0", "NaN", "Infinity", "9007199254740992",
    "01", "0 1", '"\\ud800"', '"\\udc00"', "[1,]", '{"a":1,}', "[", '"',
    '```json\n{}\n```', "\ufeff{}", "[".repeat(33) + "0" + "]".repeat(33),
  ];
  for (const raw of invalid) {
    assert.throws(() => parseJson(raw, { integersOnly: true }), undefined, raw);
  }
  assert.throws(() => parseJson(Buffer.from([0xff])));
  assert.equal(parseJson('"\\ud83d\\ude00"'), "😀");
  assert.doesNotThrow(() => parseJson("[".repeat(32) + "0" + "]".repeat(32)));
  assert.equal(parseJson('{"__proto__":3}').__proto__, 3);
  assert.equal(parseJson("1.25"), 1.25);
  assert.throws(() => parseJson("1e309"));
});

test("grading distinguishes candidate failures from absent infrastructure", () => {
  const result = {
    stdout: Buffer.from("{}"), stderr: Buffer.alloc(0), exitCode: 0,
    started: true, timedOut: false, overflow: false,
  };
  assert.equal(gradeCandidate({ ...result, started: false }, "{}").status,
    "infrastructure_unknown");
  for (const [patch, reason] of [
    [{ timedOut: true }, "candidate_timeout"],
    [{ overflow: true }, "output_limit"],
    [{ exitCode: 1 }, "candidate_exit"],
    [{ stdout: Buffer.from('{"a":1,"a":2}') }, "invalid_candidate_output"],
  ]) assert.equal(gradeCandidate({ ...result, ...patch }, "{}").reason, reason);
  assert.throws(() => gradeCandidate(result, "bad expected data"));
});

test("paths cannot target traversal or hidden carryover directories", () => {
  assert.equal(safePath("src/main.py"), "src/main.py");
  for (const value of ["../key", "/etc/passwd", "a/../b", "a//b", ".git/config",
    "a/", "a\\b", "a\0b", "a\nb"]) assert.throws(() => safePath(value));
});

test("IPC validates full session tuple and denies host-operation fields", () => {
  const request = {
    protocol: PROTOCOL, session_id: "session-a", sequence: 1,
    operation: "execute", body: { command: "printf ok" },
  };
  assert.equal(validateRequest(request, "session-a", 1), request);
  assert.throws(() => validateRequest(request, "session-b", 1));
  assert.throws(() => validateRequest(request, "session-a", 2));
  assert.throws(() => validateRequest({ ...request, operation: "grade" }, "session-a", 1));
  assert.throws(() => validateRequest({
    ...request, body: { command: "true", container: "host" },
  }, "session-a", 1));
  const model = {
    ...request, operation: "invoke_model",
    body: { phase: "memory_plan", prompt: "query", planning_round: 1 },
  };
  assert.equal(validateRequest(model, "session-a", 1), model);
  assert.throws(() => validateRequest({
    ...model, body: { ...model.body, planning_round: null },
  }, "session-a", 1));
});

test("usage allows finite fractional premium accounting but requires one reported request", () => {
  const usage = {
    input_tokens: 1, output_tokens: 2, cache_read_tokens: 0, cache_write_tokens: 0,
    reasoning_tokens: null, api_requests: 1, premium_requests: 0.5, nano_aiu: null,
    api_duration_ms: 1.25, monetary_cost_verified: false,
  };
  assert.equal(validateUsage(usage), usage);
  for (const patch of [{ api_requests: 2 }, { api_requests: 0 }, { input_tokens: null },
    { output_tokens: Infinity }, { input_tokens: 1.5 }, { reasoning_tokens: 0.5 },
    { output_tokens: Number.MAX_SAFE_INTEGER + 1 }, { monetary_cost_verified: true }]) {
    assert.throws(() => validateUsage({ ...usage, ...patch }));
  }
});

const admission = (patch = {}) => ({
  arm: "no_memory", sessionId: "s1", slotId: "p1-1-no_memory",
  phase: "work", prompt: "act", sequence: 1, ...patch,
});
test("durable admission owns call IDs across fresh controller sessions", () => {
  const records = [];
  const ledger = new InvocationLedger({
    record: (event) => records.push(event), runId: "r", model: "gpt-6-astra", effort: "high",
  });
  const first = ledger.reserve(admission());
  const second = ledger.reserve(admission({ sessionId: "s2", slotId: "p1-2-no_memory" }));
  assert.equal(first.receipt.global_ordinal, 1);
  assert.equal(second.receipt.global_ordinal, 2);
  assert.equal(second.receipt.bridge_call_id, "000002");
  assert.equal(records[0].receipt, first.receipt);
  ledger.stop();
  assert.throws(() => ledger.reserve(admission()));
});

test("prompt/envelope checks happen before admission and escaping counts", () => {
  const ledger = new InvocationLedger({
    record: () => {}, runId: "r", model: "gpt-6-astra", effort: "high",
  });
  for (const prompt of ["x".repeat(65537), '"'.repeat(40000), "\ud800"]) {
    assert.throws(() => ledger.reserve(admission({ prompt })));
  }
  assert.equal(ledger.ordinal, 0);
  assert.equal(ledger.reserve(admission()).receipt.global_ordinal, 1);
});

test("failed durable admission cannot be recycled or followed by another dispatch", () => {
  const ledger = new InvocationLedger({
    record: () => { throw new Error("disk full"); },
    runId: "r", model: "gpt-6-astra", effort: "high",
  });
  assert.throws(() => ledger.reserve(admission()), /disk full/);
  assert.equal(ledger.ordinal, 1);
  assert.equal(ledger.arms.no_memory, 1);
  assert.throws(() => ledger.reserve(admission()), /admission_stopped/);
});

test("per-slot, per-arm and global invocation ceilings cannot be bypassed", () => {
  const make = () => new InvocationLedger({
    record: () => {}, runId: "r", model: "gpt-6-astra", effort: "high",
  });
  const work = make();
  for (let i = 0; i < 16; i += 1) work.reserve(admission({ sequence: i + 1 }));
  assert.throws(() => work.reserve(admission()), /invocation_limit/);
  assert.throws(() => make().reserve(admission({ phase: "handoff" })), /phase_arm_mismatch/);
  const arm = make();
  for (let i = 0; i < 160; i += 1) arm.reserve(admission({ slotId: `slot${i}` }));
  assert.throws(() => arm.reserve(admission({ slotId: "extra" })), /invocation_limit/);
  const total = make();
  for (let i = 0; i < 318; i += 1) {
    total.reserve(admission({ slotId: `slot${i}`, arm: i < 159 ? "no_memory" : "handoff" }));
  }
  assert.throws(() => total.reserve(admission({ arm: "pg_agmemory" })), /invocation_limit/);
});
