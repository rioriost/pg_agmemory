import {
  ARMS, canonicalJson, exactKeys, LIMITS, parseJson, requireCondition, safePath, sha256, utf8,
} from "./development-eval-protocol.mjs";
import { validateArtifact } from "./development-eval-sandbox.mjs";

export function artifactFromFiles(files, allowedPaths) {
  requireCondition(Array.isArray(files), "pack_files_required");
  const manifest = files.map((row) => ({
    path: row.path, size: row.size, sha256: row.sha256,
  }));
  return validateArtifact({
    status: "ok", files, bytes: files.reduce((n, row) => n + row.size, 0),
    tree_sha256: sha256(canonicalJson(manifest)),
  }, allowedPaths);
}

export function validatePack(pack) {
  exactKeys(pack, ["format", "projects"]);
  requireCondition(pack.format === "pgag-development-pack-v1" && Array.isArray(pack.projects)
    && pack.projects.length === 2, "invalid_development_pack");
  const projectIds = new Set();
  for (const project of pack.projects) {
    let projectHistoryCases = 0;
    exactKeys(project, ["project_id", "milestones"]);
    requireCondition(/^[a-z][a-z0-9-]{0,63}$/.test(project.project_id)
      && !projectIds.has(project.project_id), "invalid_project_id");
    projectIds.add(project.project_id);
    requireCondition(Array.isArray(project.milestones) && project.milestones.length === 3,
      "three_milestones_required");
    for (const [index, milestone] of project.milestones.entries()) {
      exactKeys(milestone, [
        "number", "brief", "files", "allowed_paths", "entry_point", "cases",
        "reference_files", "negative_controls",
      ]);
      requireCondition(milestone.number === index + 1, "milestone_order");
      utf8(milestone.brief, 4096);
      requireCondition(milestone.brief.trim().length > 0, "brief_required");
      requireCondition(Array.isArray(milestone.allowed_paths)
        && milestone.allowed_paths.length <= LIMITS.files
        && new Set(milestone.allowed_paths).size === milestone.allowed_paths.length,
      "invalid_allowed_paths");
      milestone.allowed_paths.forEach(safePath);
      safePath(milestone.entry_point);
      requireCondition(milestone.allowed_paths.includes(milestone.entry_point), "entry_not_allowed");
      artifactFromFiles(milestone.files, milestone.allowed_paths);
      artifactFromFiles(milestone.reference_files, milestone.allowed_paths);
      requireCondition(milestone.reference_files.some((row) => row.path === milestone.entry_point),
        "reference_entry_required");
      requireCondition(Array.isArray(milestone.cases) && milestone.cases.length >= 1
        && milestone.cases.length <= 24, "invalid_case_count");
      const caseIds = new Set();
      let workCases = 0;
      const historyCases = [];
      for (const sample of milestone.cases) {
        exactKeys(sample, ["case_id", "input", "expected", "tags", "history_origin"]);
        requireCondition(/^[a-z][a-z0-9-]{0,63}$/.test(sample.case_id)
          && !caseIds.has(sample.case_id), "invalid_case_id");
        caseIds.add(sample.case_id);
        const input = canonicalJson(sample.input) + "\n";
        parseJson(input, { maximum: LIMITS.input, integersOnly: true });
        parseJson(canonicalJson(sample.expected), { maximum: LIMITS.capture, integersOnly: true });
        requireCondition(Array.isArray(sample.tags) && sample.tags.length >= 1
          && sample.tags.length <= 8 && sample.tags.every((tag) =>
            typeof tag === "string" && /^[a-z][a-z0-9-]{0,63}$/.test(tag)), "invalid_case_tags");
        if (sample.tags.includes("milestone-work")) workCases += 1;
        if (sample.history_origin !== null) {
          exactKeys(sample.history_origin, ["milestone", "visible_quote"]);
          const origin = sample.history_origin;
          requireCondition(Number.isInteger(origin.milestone) && origin.milestone >= 1
            && origin.milestone < milestone.number, "invalid_history_origin");
          utf8(origin.visible_quote, 4096);
          requireCondition(origin.visible_quote.trim().length > 0
            && project.milestones[origin.milestone - 1].brief.includes(origin.visible_quote),
          "history_origin_not_previously_visible");
          historyCases.push(sample.case_id);
          projectHistoryCases += 1;
        }
      }
      requireCondition(workCases > 0, "milestone_work_cases_required");
      requireCondition(Array.isArray(milestone.negative_controls)
        && milestone.negative_controls.length >= 1 && milestone.negative_controls.length <= 4,
      "negative_controls_required");
      const controls = new Set();
      const covered = new Set();
      for (const control of milestone.negative_controls) {
        exactKeys(control, ["control_id", "files", "fails_cases"]);
        requireCondition(/^[a-z][a-z0-9-]{0,63}$/.test(control.control_id)
          && !controls.has(control.control_id), "invalid_control_id");
        controls.add(control.control_id);
        artifactFromFiles(control.files, milestone.allowed_paths);
        requireCondition(Array.isArray(control.fails_cases) && control.fails_cases.length >= 1
          && control.fails_cases.every((id) => caseIds.has(id)), "invalid_negative_cases");
        control.fails_cases.forEach((id) => covered.add(id));
      }
      requireCondition(historyCases.every((id) => covered.has(id)), "history_negative_control_missing");
    }
    requireCondition(projectHistoryCases > 0, "project_history_case_required");
  }
  return pack;
}

export function visibleMilestone(project, number) {
  const milestone = project.milestones[number - 1];
  return {
    project_id: project.project_id, milestone: number, brief: milestone.brief,
    files: milestone.files, allowed_paths: milestone.allowed_paths, entry_point: milestone.entry_point,
  };
}

export function schedule(pack) {
  validatePack(pack);
  const slots = [];
  let position = 0;
  for (let number = 1; number <= 3; number += 1) {
    for (const project of pack.projects) {
      const order = ARMS.map((_, index) => ARMS[(index + position) % ARMS.length]);
      for (const arm of order) {
        slots.push({ slot_id: `${project.project_id}-${number}-${arm}`, arm,
          ...visibleMilestone(project, number) });
      }
      position += 1;
    }
  }
  return slots;
}

export async function qualifyPack(pack, { grade, record }) {
  validatePack(pack);
  const matrix = [];
  for (const project of pack.projects) {
    for (const milestone of project.milestones) {
      const base = { project_id: project.project_id, milestone: milestone.number };
      for (const sample of milestone.cases) {
        const result = await grade(milestone.reference_files, milestone, sample);
        const row = { ...base, candidate: "reference", case_id: sample.case_id, ...result };
        record(row);
        matrix.push(row);
        requireCondition(result.status === "passed", "reference_qualification_failed");
      }
      for (const sample of milestone.cases.filter((item) => item.tags.includes("milestone-work"))) {
        const result = await grade(milestone.files, milestone, sample);
        const row = { ...base, candidate: "canonical_start", case_id: sample.case_id, ...result };
        record(row);
        matrix.push(row);
        requireCondition(result.status === "failed", "canonical_requires_no_work");
      }
      for (const control of milestone.negative_controls) {
        for (const caseId of control.fails_cases) {
          const sample = milestone.cases.find((item) => item.case_id === caseId);
          const result = await grade(control.files, milestone, sample);
          const row = { ...base, candidate: control.control_id, case_id: caseId, ...result };
          record(row);
          matrix.push(row);
          requireCondition(result.status === "failed", "negative_qualification_failed");
        }
      }
    }
  }
  return { status: "qualified", pack_sha256: sha256(canonicalJson(pack)), matrix };
}
