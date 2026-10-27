/**
 * `planner` skill — Plan phase.
 *
 * Produces the materialized plan draft that preflight/admission/approval act
 * on. `plan_definition_digest` is supplied by the approval-bound platform
 * projection; this package does not re-implement the frozen canonical digest
 * (that lives in `@rph/research` and the platform). A planner can never widen
 * the remaining budget.
 */

import type {
  EvidenceReport,
  PlanBudget,
  PlanScience,
  PlanScienceInputs,
  ScienceSeed,
} from "./models.js";
import {
  DIGEST_RE,
  EVIDENCE_REPORT_SCHEMA,
  PLAN_BUDGET_SPEC,
  PLAN_SCIENCE_SPEC,
  SCIENCE_SEED_SPEC,
} from "./models.js";
import type { FieldSpec, SkillSpec, ValidationIssue } from "./schema.js";
import { validateSkillObject } from "./schema.js";

export const PLANNER_SKILL = "planner";
// 1.1.0 (M1-04a): typed science seed (charge/multiplicity/electronic state/
// structure provenance/constraints) is required; the seven-field `science`
// projection that feeds the frozen plan_definition_digest is unchanged.
export const PLANNER_SKILL_VERSION = "1.1.0";
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
  /** Typed scientific values; a missing critical value is a clarification. */
  readonly science_seed: ScienceSeed;
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
  /** Typed science block (M1-04a); digest projection stays the seven fields. */
  readonly science_inputs: PlanScienceInputs;
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
    science_seed: SCIENCE_SEED_SPEC,
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
    science_inputs: {
      kind: "object",
      required: true,
      fields: {
        charge: { kind: "integer", required: true, min: Number.MIN_SAFE_INTEGER },
        multiplicity: { kind: "integer", required: true, min: 1 },
        electronic_state: { kind: "object", required: true, nullable: true, passthrough: true },
        structure_provenance: {
          kind: "array",
          required: true,
          items: { kind: "object", required: true, passthrough: true },
        },
        constraints: {
          kind: "array",
          required: true,
          items: { kind: "object", required: true, passthrough: true },
        },
      },
    },
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

// The deterministic offline planner is a **fixture**, not a product fallback.
// It lives in `fixture-planner.ts` (explicit fixture module) and is rejected by
// `composition.ts`; a product composition must inject the model adapter.

/**
 * No production planner was composed.  Thrown instead of falling back to the
 * deterministic fixture planner (M3-03: a product start must never silently
 * plan with a placeholder).
 */
export class ProductionPlannerRequiredError extends Error {
  constructor(message = "a production (model) planner must be composed before starting") {
    super(message);
    this.name = "ProductionPlannerRequiredError";
  }
}

/** A composition handed the fixture planner to a product surface (fail-fast). */
export class FixturePlannerRejectedError extends Error {
  constructor(
    message = "the fixture planner is test/demo-only and cannot be a product fallback",
  ) {
    super(message);
    this.name = "FixturePlannerRejectedError";
  }
}
