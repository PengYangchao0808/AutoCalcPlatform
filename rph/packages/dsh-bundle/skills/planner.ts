/**
 * `planner` skill — Plan phase.
 *
 * Produces the materialized plan draft that preflight/admission/approval act
 * on. `plan_definition_digest` is supplied by the approval-bound platform
 * projection; this package does not re-implement the frozen canonical digest
 * (that lives in `@rph/research` and the platform). A planner can never widen
 * the remaining budget.
 */

import type { EvidenceReport, PlanBudget, PlanScience } from "./models.js";
import {
  DIGEST_RE,
  EVIDENCE_REPORT_SCHEMA,
  PLAN_BUDGET_SPEC,
  PLAN_SCIENCE_SPEC,
} from "./models.js";
import type { FieldSpec, SkillSpec, ValidationIssue } from "./schema.js";
import { validateSkillObject } from "./schema.js";

export const PLANNER_SKILL = "planner";
export const PLANNER_SKILL_VERSION = "1.0.0";
export const PLANNER_INPUT_SCHEMA = "acp2.skill.planner.input.v1";
export const PLANNER_OUTPUT_SCHEMA = "acp2.skill.planner.output.v1";

export interface PlannerInput {
  readonly schema: typeof PLANNER_INPUT_SCHEMA;
  readonly skill: typeof PLANNER_SKILL;
  readonly skill_version: typeof PLANNER_SKILL_VERSION;
  readonly project_id: string;
  readonly round: number;
  readonly hypothesis: string;
  readonly normalized_question: string;
  readonly recipe_digest: string;
  readonly plan_definition_digest: string;
  readonly initial_input: {
    readonly input_id: string;
    readonly sha256: string;
    readonly format: string;
    readonly role: string | null;
  };
  readonly critique_findings: readonly string[];
  readonly evidence: EvidenceReport | null;
  readonly remaining_budget: PlanBudget;
}

export interface PlannerOutput {
  readonly schema: typeof PLANNER_OUTPUT_SCHEMA;
  readonly skill: typeof PLANNER_SKILL;
  readonly skill_version: typeof PLANNER_SKILL_VERSION;
  readonly plan_id: string;
  readonly project_id: string;
  readonly round: number;
  readonly hypothesis: string;
  readonly recipe_digest: string;
  readonly plan_definition_digest: string;
  readonly science: PlanScience;
  readonly budget: PlanBudget;
  readonly notes: readonly string[];
}

const stringField = (extra: Partial<FieldSpec> = {}): FieldSpec => ({
  kind: "string",
  required: true,
  ...extra,
});
const stringArray = (extra: Partial<FieldSpec> = {}): FieldSpec => ({
  kind: "array",
  required: true,
  items: { kind: "string", required: true, minLength: 1 },
  ...extra,
});

const INITIAL_INPUT_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    input_id: stringField(),
    sha256: stringField({ pattern: DIGEST_RE }),
    format: stringField(),
    role: { kind: "string", required: true, nullable: true },
  },
};

export const PLANNER_INPUT_SPEC: SkillSpec = {
  skill: PLANNER_SKILL,
  schema: PLANNER_INPUT_SCHEMA,
  version: PLANNER_SKILL_VERSION,
  fields: {
    project_id: stringField(),
    round: { kind: "integer", required: true, min: 0 },
    hypothesis: stringField(),
    normalized_question: stringField(),
    recipe_digest: stringField({ pattern: DIGEST_RE }),
    plan_definition_digest: stringField({ pattern: DIGEST_RE }),
    initial_input: INITIAL_INPUT_SPEC,
    critique_findings: stringArray({ unique: true }),
    evidence: { kind: "object", required: true, nullable: true, passthrough: true },
    remaining_budget: PLAN_BUDGET_SPEC,
  },
};

export const PLANNER_OUTPUT_SPEC: SkillSpec = {
  skill: PLANNER_SKILL,
  schema: PLANNER_OUTPUT_SCHEMA,
  version: PLANNER_SKILL_VERSION,
  fields: {
    plan_id: stringField(),
    project_id: stringField(),
    round: { kind: "integer", required: true, min: 0 },
    hypothesis: stringField(),
    recipe_digest: stringField({ pattern: DIGEST_RE }),
    plan_definition_digest: stringField({ pattern: DIGEST_RE }),
    science: PLAN_SCIENCE_SPEC,
    budget: PLAN_BUDGET_SPEC,
    notes: stringArray({ unique: true }),
  },
};

export function validatePlannerInput(value: unknown): ValidationIssue[] {
  const issues = validateSkillObject(value, PLANNER_INPUT_SPEC);
  const raw = value as { readonly evidence?: unknown } | null;
  if (
    raw !== null &&
    typeof raw === "object" &&
    raw.evidence !== null &&
    raw.evidence !== undefined
  ) {
    const report = raw.evidence as { readonly schema?: unknown };
    if (report.schema !== EVIDENCE_REPORT_SCHEMA) {
      issues.push({
        path: "$.evidence.schema",
        code: "version",
        message: `must be ${EVIDENCE_REPORT_SCHEMA}`,
      });
    }
  }
  return issues;
}

export function validatePlannerOutput(value: unknown): ValidationIssue[] {
  return validateSkillObject(value, PLANNER_OUTPUT_SPEC);
}

const BUDGET_FIELDS = [
  "model_requests",
  "input_tokens",
  "output_tokens",
  "task_refs",
  "recipe_steps",
  "qc_invocations",
  "wall_seconds",
  "queue_seconds",
] as const;

/** Clamp a requested budget so it can never exceed the remaining budget. */
export function clampBudget(
  requested: PlanBudget,
  remaining: PlanBudget,
): PlanBudget {
  const out: Record<string, number> = {};
  for (const field of BUDGET_FIELDS) {
    out[field] = Math.min(requested[field], remaining[field]);
  }
  return Object.freeze(out as unknown as PlanBudget);
}

/** Deterministic offline planner; a live model may replace it only via validation. */
export function runPlanner(input: PlannerInput): PlannerOutput {
  const issues = validatePlannerInput(input);
  if (issues.length > 0) {
    throw new TypeError(
      `planner input invalid: ${issues.map((item) => item.path).join(",")}`,
    );
  }
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
      method: "r2SCAN-3c",
      mapping: "identity",
      charge: 0,
      multiplicity: 1,
      allowed_substitutions: Object.freeze([]),
    }),
    budget,
    notes: Object.freeze([
      "budget is clamped to the remaining admission budget",
      ...input.critique_findings,
    ]),
  });
}
