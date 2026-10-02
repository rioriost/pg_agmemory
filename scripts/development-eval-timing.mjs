import { requireCondition } from "./development-eval-protocol.mjs";

export const MAINTENANCE_TIMING_PROFILE = "maintenance-300s-v1";

const LEGACY_TIMING = Object.freeze({ model_ms: 150000, response_ms: 180000 });
const MAINTENANCE_TIMING = Object.freeze({ model_ms: 270000, response_ms: 300000 });

export function evaluationTiming(profile) {
  requireCondition(profile === undefined || profile === MAINTENANCE_TIMING_PROFILE,
    "invalid_timing_profile");
  return profile === undefined ? LEGACY_TIMING : MAINTENANCE_TIMING;
}
