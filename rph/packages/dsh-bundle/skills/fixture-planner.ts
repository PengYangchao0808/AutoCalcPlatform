/**
 * Deterministic offline planner — **explicit fixture module** (M3-03).
 *
 * The original `runPlanner` was a deterministic stub that could be mistaken for
 * a production planner (and was the Supervisor default).  M3-03 renames it to
 * `runFixturePlanner`, moves it out of the skill contract module and marks its
 * binding `kind: "fixture"` so product composition
 * (`composition.ts::requireProductionPlanner`) can reject it fail-fast.
 *
 * The fixture is still useful: offline schema tests, the thin-slice runner and
 * demos use it deliberately.  It never reaches a provider and never becomes a
 * silent fallback when a model planner is missing.
 */

import type { PlanBudget } from "./models.js";
import { toPlanScienceInputs } from "./models.js";
import type { PlannerInput, PlannerOutput } from "./planner.js";
import {
  PLANNER_OUTPUT_SCHEMA,
  PLANNER_SKILL,
  PLANNER_SKILL_VERSION,
  clampBudget,
  validatePlannerInput,
} from "./planner.js";

/** The binding kind product composition must refuse. */
export const FIXTURE_PLANNER_KIND = "fixture" as const;

/** Deterministic offline planner; a live model may replace it only via validation. */
export function runFixturePlanner(input: PlannerInput): PlannerOutput {
  const issues = validatePlannerInput(input);
  if (issues.length > 0) {
    throw new TypeError(
      `planner input invalid: ${issues.map((item) => item.path).join(",")}`,
    );
  }
  // The typed science block is derived from the seed: charge/multiplicity are
  // never fabricated (a missing value throws ScienceClarificationError).
  const scienceInputs = toPlanScienceInputs(input.science_seed);
  const requested: PlanBudget = {
    model_requests: 1,
    input_tokens: 1000,
    output_tokens: 500,
    task_refs: 1,
    recipe_steps: 1,
    qc_invocations: 1,
    wall_seconds: 600,
    queue_seconds: 60,
  };
  const budget = clampBudget(requested, input.remaining_budget);
  return Object.freeze({
    schema: PLANNER_OUTPUT_SCHEMA,
    skill: PLANNER_SKILL,
    skill_version: PLANNER_SKILL_VERSION,
    plan_id: `plan-${input.project_id}-r${input.round}`,
    project_id: input.project_id,
    round: input.round,
    hypothesis: input.hypothesis,
    recipe_digest: input.recipe_digest,
    plan_definition_digest: input.plan_definition_digest,
    science: Object.freeze({
      capability_steps: Object.freeze([
        Object.freeze({
          step_index: 0,
          capability: "geometry_optimization",
          version: "1.0.0",
          parameters: Object.freeze({ max_cycles: 100 }),
        }),
      ]),
      initial_inputs: Object.freeze([
        Object.freeze({
          input_id: input.initial_input.input_id,
          sha256: input.initial_input.sha256,
          format: input.initial_input.format,
          role: input.initial_input.role,
        }),
      ]),
      method: input.science_seed.method,
      mapping: input.science_seed.mapping,
      charge: scienceInputs.charge,
      multiplicity: scienceInputs.multiplicity,
      allowed_substitutions: Object.freeze([]),
    }),
    science_inputs: scienceInputs,
    budget,
    notes: Object.freeze([
      "fixture planner (deterministic); not a product fallback",
      "budget is clamped to the remaining admission budget",
      "charge/multiplicity come from the typed science seed (never defaulted)",
      ...input.critique_findings,
    ]),
  });
}

/** The fixture planner as a typed skill binding (kind is explicit). */
export const FIXTURE_PLANNER = Object.freeze({
  kind: FIXTURE_PLANNER_KIND,
  async plan(input: PlannerInput): Promise<PlannerOutput> {
    return runFixturePlanner(input);
  },
});

export type FixturePlannerBinding = typeof FIXTURE_PLANNER;
