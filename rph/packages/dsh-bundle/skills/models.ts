/**
 * Shared I/O models for the four research skills.
 *
 * These are data contracts only. They mirror the frozen ResearchRecord /
 * Proposal names (TaskRef, budget counters, science identity) without
 * importing `@rph/research`: the dependency direction is
 * `dsh-bundle -> bridge -> research`, so a skill receives plain JSON-shaped
 * values and the platform remains the persistence authority.
 */

import type { FieldSpec, SkillSpec, ValidationIssue } from "./schema.js";
import { validateObjectFields } from "./schema.js";

export const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
export const TASK_REF_FIELDS = new Set([
  "project_id",
  "job_id",
  "attempt",
  "release_id",
]);

export interface TaskRef {
  readonly project_id: string;
  readonly job_id: string;
  readonly attempt: number;
  readonly release_id: string;
}

export interface PlannedStep {
  readonly step_index: number;
  readonly capability: string;
  readonly version: string;
  readonly parameters: Readonly<Record<string, string | number | boolean>>;
}

export interface PlannedInput {
  readonly input_id: string;
  readonly sha256: string;
  readonly format: string;
  readonly role: string | null;
}

export interface PlanScience {
  readonly capability_steps: readonly PlannedStep[];
  readonly initial_inputs: readonly PlannedInput[];
  readonly method: string;
  readonly mapping: string;
  readonly charge: number;
  readonly multiplicity: number;
  readonly allowed_substitutions: readonly string[];
}

export interface PlanBudget {
  readonly model_requests: number;
  readonly input_tokens: number;
  readonly output_tokens: number;
  readonly task_refs: number;
  readonly recipe_steps: number;
  readonly qc_invocations: number;
  readonly wall_seconds: number;
  readonly queue_seconds: number;
}

export type ClaimStatus =
  | "supported"
  | "refuted"
  | "inconclusive"
  | "not_evaluated";

export interface EvidenceClaim {
  readonly claim_id: string;
  readonly statement: string;
  readonly status: ClaimStatus;
  readonly evidence_refs: readonly string[];
  readonly reason: string | null;
}

export type EvidenceDecision = "accept" | "revise" | "stop";

export interface EvidenceReport {
  readonly schema: "acp2.evidence.report.v1";
  readonly report_id: string;
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly task_ref: TaskRef;
  readonly status: "complete" | "partial" | "missing" | "failed";
  readonly decision: EvidenceDecision;
  readonly claims: readonly EvidenceClaim[];
  readonly evidence_refs: readonly string[];
  readonly terminal_event_key: string | null;
}

const stringField = (
  extra: Partial<FieldSpec> = {},
): FieldSpec => ({ kind: "string", required: true, ...extra });
const integerField = (
  minimum: number,
  extra: Partial<FieldSpec> = {},
): FieldSpec => ({ kind: "integer", required: true, min: minimum, ...extra });
const stringArray = (extra: Partial<FieldSpec> = {}): FieldSpec => ({
  kind: "array",
  required: true,
  items: { kind: "string", required: true, minLength: 1 },
  ...extra,
});

export const TASK_REF_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    project_id: stringField(),
    job_id: stringField(),
    attempt: integerField(1),
    release_id: stringField(),
  },
};

export const PLAN_BUDGET_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    model_requests: integerField(0),
    input_tokens: integerField(0),
    output_tokens: integerField(0),
    task_refs: integerField(0),
    recipe_steps: integerField(0),
    qc_invocations: integerField(0),
    wall_seconds: integerField(0),
    queue_seconds: integerField(0),
  },
};

export const PLAN_SCIENCE_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    capability_steps: {
      kind: "array",
      required: true,
      minItems: 1,
      items: {
        kind: "object",
        required: true,
        fields: {
          step_index: integerField(0),
          capability: stringField(),
          version: stringField(),
          parameters: { kind: "object", required: true, passthrough: true },
        },
      },
    },
    initial_inputs: {
      kind: "array",
      required: true,
      minItems: 1,
      items: {
        kind: "object",
        required: true,
        fields: {
          input_id: stringField(),
          sha256: stringField({ pattern: DIGEST_RE }),
          format: stringField(),
          role: { kind: "string", required: true, nullable: true },
        },
      },
    },
    method: stringField(),
    mapping: stringField(),
    charge: integerField(Number.MIN_SAFE_INTEGER),
    multiplicity: integerField(1),
    allowed_substitutions: stringArray(),
  },
};

export const TASK_REF_SCHEMA = "acp2.research.task-ref.v1";
export const TASK_REF_VERSION = "1.0.0";
export const TASK_REF_SPEC_ROOT: SkillSpec = {
  skill: "task-ref",
  schema: TASK_REF_SCHEMA,
  version: TASK_REF_VERSION,
  fields: {
    project_id: stringField(),
    job_id: stringField(),
    attempt: integerField(1),
    release_id: stringField(),
  },
};

export function validateTaskRef(value: unknown): ValidationIssue[] {
  return validateObjectFields(value, {
    project_id: stringField(),
    job_id: stringField(),
    attempt: integerField(1),
    release_id: stringField(),
  });
}

export const EVIDENCE_REPORT_SCHEMA = "acp2.evidence.report.v1";
export const EVIDENCE_REPORT_VERSION = "1.0.0";

const EVIDENCE_CLAIM_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    claim_id: stringField(),
    statement: stringField(),
    status: stringField({
      enum: ["supported", "refuted", "inconclusive", "not_evaluated"],
    }),
    evidence_refs: stringArray({ unique: true }),
    reason: { kind: "string", required: true, nullable: true },
  },
};

export const EVIDENCE_REPORT_SPEC: SkillSpec = {
  skill: "evidence-report",
  schema: EVIDENCE_REPORT_SCHEMA,
  version: EVIDENCE_REPORT_VERSION,
  fields: {
    schema: stringField({ enum: [EVIDENCE_REPORT_SCHEMA] }),
    report_id: stringField(),
    project_id: stringField(),
    research_id: stringField(),
    round: integerField(0),
    task_ref: TASK_REF_SPEC,
    status: stringField({ enum: ["complete", "partial", "missing", "failed"] }),
    decision: stringField({ enum: ["accept", "revise", "stop"] }),
    claims: {
      kind: "array",
      required: true,
      items: EVIDENCE_CLAIM_SPEC,
    },
    evidence_refs: stringArray({ unique: true }),
    terminal_event_key: {
      kind: "string",
      required: true,
      nullable: true,
    },
  },
};

/** Validate one real EvidenceReport; a model summary is not a report. */
export function validateEvidenceReport(value: unknown): ValidationIssue[] {
  return validateObjectFields(value, EVIDENCE_REPORT_SPEC.fields);
}

/**
 * Claims that make an expensive retry unsafe.
 *
 * A report carrying either state is authoritative: no later model advice may
 * turn it into "keep submitting".
 */
export function blockingClaimStatus(
  report: EvidenceReport,
): "refuted" | "inconclusive" | null {
  if (report.claims.some((claim) => claim.status === "refuted")) {
    return "refuted";
  }
  if (report.claims.some((claim) => claim.status === "inconclusive")) {
    return "inconclusive";
  }
  return null;
}

/** A report with no executed/usable claims is a no-evidence branch. */
export function reportHasEvidence(report: EvidenceReport): boolean {
  if (report.status === "missing" || report.status === "failed") {
    return false;
  }
  return report.claims.some((claim) => claim.status !== "not_evaluated");
}
