import assert from "node:assert/strict";
import test from "node:test";
import { sha256 } from "./development-eval-protocol.mjs";
import { qualifyPack, schedule, validatePack, visibleMilestone } from "./development-eval-pack.mjs";

function files(text) {
  const data = Buffer.from(text);
  return [{ path: "main.py", size: data.length, sha256: sha256(data),
    content_base64: data.toString("base64") }];
}

// Protocol self-test data, never a held-out task or a measured work-model run.
function toyPack() {
  return {
    format: "pgag-development-pack-v1",
    projects: ["toy-a", "toy-b"].map((project_id) => ({
      project_id, milestones: [1, 2, 3].map((number) => ({
        number, brief: "Protocol fixture: preserve the integer value.",
        files: files("initial"), allowed_paths: ["main.py"], entry_point: "main.py",
        reference_files: files("reference"),
        cases: [{ case_id: "one", input: { value: 3 }, expected: { value: 3 },
          tags: ["milestone-work"], history_origin: number === 1 ? null : {
            milestone: 1, visible_quote: "preserve the integer value",
          } }],
        negative_controls: [{ control_id: "defective", files: files("defective"),
          fails_cases: ["one"] }],
      })),
    })),
  };
}

test("visible packaging strips hidden cases, controls, references and future briefs", () => {
  const pack = validatePack(toyPack());
  const visible = visibleMilestone(pack.projects[0], 1);
  assert.deepEqual(Object.keys(visible).sort(),
    ["project_id", "milestone", "brief", "files", "allowed_paths", "entry_point"].sort());
  assert.ok(!JSON.stringify(visible).includes("reference"));
  assert.ok(!JSON.stringify(visible).includes("expected"));
});

test("eighteen-slot schedule rotates arms and preserves each project's milestone order", () => {
  const slots = schedule(toyPack());
  assert.equal(slots.length, 18);
  assert.deepEqual(slots.slice(0, 3).map((slot) => slot.arm),
    ["no_memory", "handoff", "pg_agmemory"]);
  assert.deepEqual(slots.slice(3, 6).map((slot) => slot.arm),
    ["handoff", "pg_agmemory", "no_memory"]);
  for (const project of ["toy-a", "toy-b"]) {
    for (const arm of ["no_memory", "handoff", "pg_agmemory"]) {
      assert.deepEqual(slots.filter((slot) => slot.project_id === project && slot.arm === arm)
        .map((slot) => slot.milestone), [1, 2, 3]);
    }
  }
});

test("pack qualification requires positive and negative controls, not a declaration", async () => {
  const records = [];
  const grade = async (candidate) => ({
    status: Buffer.from(candidate[0].content_base64, "base64").toString() === "reference"
      ? "passed" : "failed", reason: null,
  });
  const result = await qualifyPack(toyPack(), { grade, record: (row) => records.push(row) });
  assert.equal(result.status, "qualified");
  assert.equal(result.matrix.length, 18);
  await assert.rejects(qualifyPack(toyPack(), {
    grade: async () => ({ status: "passed" }), record: () => {},
  }), /canonical_requires_no_work/);
  await assert.rejects(qualifyPack(toyPack(), {
    grade: async () => ({ status: "infrastructure_unknown" }), record: () => {},
  }), /reference_qualification_failed/);
});

test("historical expectations need prior visible origins and negative coverage", () => {
  const pack = toyPack();
  pack.projects[0].milestones[1].cases[0].history_origin.visible_quote = "not in the past";
  assert.throws(() => validatePack(pack), /history_origin_not_previously_visible/);
  const missing = toyPack();
  missing.projects[0].milestones[1].negative_controls[0].fails_cases = ["absent"];
  assert.throws(() => validatePack(missing), /invalid_negative_cases/);
});

test("unknown fields, leaked paths and noninteger case data fail before execution", () => {
  assert.throws(() => validatePack({ ...toyPack(), trusted: true }), /unexpected_fields/);
  const paths = toyPack();
  paths.projects[0].milestones[0].allowed_paths.push(".git/config");
  assert.throws(() => validatePack(paths), /unsafe_artifact_path/);
  const floats = toyPack();
  floats.projects[0].milestones[0].cases[0].input = { value: 1.5 };
  assert.throws(() => validatePack(floats), /integer_required/);
});
