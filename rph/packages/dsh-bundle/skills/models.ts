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

// ---------------------------------------------------------------------------
// M1-04a: typed scientific inputs (D06/G10)
//
// A research plan is not a job payload.  charge, multiplicity, the electronic
// state, the provenance of every structure and the geometric constraints are
// scientific values: a missing charge is a *clarification*, never 0, and a
// missing multiplicity is never 1.  These fields are carried next to the frozen
// seven-field `science` projection, so the C `plan_definition_digest` stays
// byte-identical (the typed block is bound by the skill version and by the
// platform `acp2.runtime.science-input.v1` validator).
// ---------------------------------------------------------------------------

export type ScienceConstraintKind =
  | "bond"
  | "angle"
  | "dihedral"
  | "distance"
  | "coordinate"
  | "atom_index";

/** Canonical field order of the typed science block (platform: science_inputs.py). */
export const SCIENCE_FIELD_ORDER = Object.freeze([
  "method",
  "mapping",
  "charge",
  "multiplicity",
  "electronic_state",
  "structure_provenance",
  "constraints",
] as const);

/** The two values that are never defaulted. */
export const CRITICAL_SCIENCE_FIELDS = Object.freeze([
  "charge",
  "multiplicity",
] as const);

export interface ElectronicState {
  readonly charge: number;
  readonly multiplicity: number;
  readonly state_label?: string | null;
  readonly reference_state?: string | null;
  /** 2S; when present it must equal multiplicity - 1. */
  readonly spin_polarization?: number | null;
}

export interface StructureProvenance {
  readonly input_id: string;
  readonly sha256: string;
  readonly format: string;
  readonly role: string;
  readonly source?: string | null;
}

export interface ScienceConstraint {
  readonly kind: ScienceConstraintKind;
  readonly atoms: readonly number[];
  readonly target: string;
  readonly unit?: string | null;
}

export interface ScienceSeed {
  readonly method: string;
  readonly mapping: string;
  readonly charge: number;
  readonly multiplicity: number;
  readonly electronic_state?: ElectronicState | null;
  readonly structure_provenance: readonly StructureProvenance[];
  readonly constraints: readonly ScienceConstraint[];
  readonly electron_count?: number | null;
}

/** The typed block a plan carries next to the frozen `science` projection. */
export interface PlanScienceInputs {
  readonly charge: number;
  readonly multiplicity: number;
  readonly electronic_state: ElectronicState | null;
  readonly structure_provenance: readonly StructureProvenance[];
  readonly constraints: readonly ScienceConstraint[];
}

/** A missing critical scientific value; the caller must ask a human. */
export class ScienceClarificationError extends Error {
  readonly missing: readonly string[];

  constructor(missing: readonly string[]) {
    super(`science clarification required: ${missing.join(",")}`);
    this.name = "ScienceClarificationError";
    this.missing = Object.freeze([...missing]);
  }
}

const STRUCTURE_PROVENANCE_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    input_id: stringField(),
    sha256: stringField({ pattern: DIGEST_RE }),
    format: stringField(),
    role: stringField(),
    source: { kind: "string", required: false, nullable: true },
  },
};

const ELECTRONIC_STATE_SPEC: FieldSpec = {
  kind: "object",
  required: false,
  nullable: true,
  fields: {
    charge: integerField(Number.MIN_SAFE_INTEGER),
    multiplicity: integerField(1),
    state_label: { kind: "string", required: false, nullable: true },
    reference_state: { kind: "string", required: false, nullable: true },
    spin_polarization: {
      kind: "integer",
      required: false,
      nullable: true,
      min: 0,
    },
  },
};

const SCIENCE_CONSTRAINT_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    kind: stringField({
      enum: ["bond", "angle", "dihedral", "distance", "coordinate", "atom_index"],
    }),
    atoms: {
      kind: "array",
      required: true,
      minItems: 1,
      items: { kind: "integer", required: true, min: 0 },
    },
    target: stringField(),
    unit: { kind: "string", required: false, nullable: true },
  },
};

export const SCIENCE_SEED_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    method: stringField(),
    mapping: stringField(),
    // No default: an omitted charge/multiplicity fails the spec instead of
    // becoming 0/1 (the pre-M1-04 planner fabricated both).
    charge: integerField(Number.MIN_SAFE_INTEGER),
    multiplicity: integerField(1),
    electronic_state: ELECTRONIC_STATE_SPEC,
    structure_provenance: {
      kind: "array",
      required: true,
      items: STRUCTURE_PROVENANCE_SPEC,
    },
    constraints: {
      kind: "array",
      required: true,
      items: SCIENCE_CONSTRAINT_SPEC,
    },
    electron_count: { kind: "integer", required: false, nullable: true, min: 1 },
  },
};

/** Validate one typed science seed (no fabrication, no untyped coercion). */
export function validateScienceSeed(value: unknown): ValidationIssue[] {
  const issues = validateObjectFields(value, SCIENCE_SEED_SPEC.fields ?? {});
  const seed = value as Partial<ScienceSeed> | null;
  if (seed !== null && typeof seed === "object") {
    const state = seed.electronic_state;
    if (state && typeof state === "object") {
      const spin = state.spin_polarization;
      if (typeof spin === "number" && spin !== (seed.multiplicity ?? 0) - 1) {
        issues.push({
          path: "$.electronic_state.spin_polarization",
          code: "mismatch",
          message: "2S must equal multiplicity - 1",
        });
      }
    }
    const electrons = seed.electron_count;
    const charge = seed.charge;
    const multiplicity = seed.multiplicity;
    if (
      typeof electrons === "number" &&
      typeof charge === "number" &&
      typeof multiplicity === "number"
    ) {
      const count = electrons - charge;
      if (count >= 1 && count % 2 !== (multiplicity - 1) % 2) {
        issues.push({
          path: "$.multiplicity",
          code: "mismatch",
          message: `${count} electrons require the other multiplicity parity`,
        });
      }
    }
  }
  return issues;
}

/** Project a validated seed onto the typed plan block (canonical order). */
export function toPlanScienceInputs(seed: ScienceSeed): PlanScienceInputs {
  const issues = validateScienceSeed(seed);
  if (issues.length > 0) {
    const missing = issues
      .filter((issue) => issue.code === "required")
      .map((issue) => issue.path.replace("$.", ""));
    if (missing.length > 0) {
      throw new ScienceClarificationError(missing);
    }
    throw new TypeError(
      `science seed invalid: ${issues.map((issue) => issue.path).join(",")}`,
    );
  }
  return Object.freeze({
    charge: seed.charge,
    multiplicity: seed.multiplicity,
    electronic_state: seed.electronic_state
      ? Object.freeze({
          charge: seed.electronic_state.charge,
          multiplicity: seed.electronic_state.multiplicity,
          state_label: seed.electronic_state.state_label ?? null,
          reference_state: seed.electronic_state.reference_state ?? null,
          spin_polarization: seed.electronic_state.spin_polarization ?? null,
        })
      : null,
    structure_provenance: Object.freeze(
      seed.structure_provenance.map((item) =>
        Object.freeze({
          input_id: item.input_id,
          sha256: item.sha256,
          format: item.format,
          role: item.role,
          source: item.source ?? null,
        }),
      ),
    ),
    constraints: Object.freeze(
      seed.constraints.map((item) =>
        Object.freeze({
          kind: item.kind,
          atoms: Object.freeze([...item.atoms]),
          target: item.target,
          unit: item.unit ?? null,
        }),
      ),
    ),
  });
}

// ---------------------------------------------------------------------------
// M1-04b: budget shape -> resource limits (D09 mapping, one resolver)
//
// wall_seconds -> ResourceBounds/timeout (local termination, CCCP
// resources.timeout_s at subprocess level, LSF -W); queue_seconds -> queue
// deadline; input_tokens/output_tokens -> recorded-only (the client throttle is
// this process, never a platform enforced claim).  None of the four is dropped
// and an unknown non-empty shape is a typed error.
// ---------------------------------------------------------------------------

export interface ResourceLimitProjection {
  readonly local_terminate_after_s: number | null;
  readonly ccp_resources_timeout_s: number | null;
  readonly lsf_walltime_minutes: number | null;
  readonly queue_deadline_s: number | null;
  readonly recorded_only: Readonly<Record<string, number>>;
  readonly bounded: boolean;
}

const BUDGET_SHAPE_FIELDS = new Set([
  "model_requests",
  "input_tokens",
  "output_tokens",
  "task_refs",
  "recipe_steps",
  "qc_invocations",
  "wall_seconds",
  "queue_seconds",
]);

export class BudgetShapeError extends Error {
  readonly fields: readonly string[];

  constructor(fields: readonly string[]) {
    super(`unknown budget field(s): ${fields.join(",")}`);
    this.name = "BudgetShapeError";
    this.fields = Object.freeze([...fields]);
  }
}

function nonNegative(value: unknown, label: string): number | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new BudgetShapeError([label]);
  }
  return value === 0 ? null : value;
}

/** Map one plan budget onto the three termination surfaces plus recorded usage. */
export function toResourceLimits(budget: PlanBudget): ResourceLimitProjection {
  const unknown = Object.keys(budget as unknown as Record<string, unknown>).filter(
    (key) => !BUDGET_SHAPE_FIELDS.has(key),
  );
  if (unknown.length > 0) {
    throw new BudgetShapeError(unknown);
  }
  const wall = nonNegative(budget.wall_seconds, "wall_seconds");
  const queue = nonNegative(budget.queue_seconds, "queue_seconds");
  const recorded: Record<string, number> = {};
  const inputTokens = nonNegative(budget.input_tokens, "input_tokens");
  const outputTokens = nonNegative(budget.output_tokens, "output_tokens");
  if (inputTokens !== null) {
    recorded.input_tokens = inputTokens;
  }
  if (outputTokens !== null) {
    recorded.output_tokens = outputTokens;
  }
  return Object.freeze({
    local_terminate_after_s: wall,
    ccp_resources_timeout_s: wall,
    lsf_walltime_minutes: wall === null ? null : Math.max(1, Math.ceil(wall / 60)),
    queue_deadline_s: queue,
    recorded_only: Object.freeze(recorded),
    bounded: wall !== null || queue !== null,
  });
}

/**
 * The platform submit budget (v1 wire): `unit` + `count` + `timeout_s`.
 *
 * `timeout_s` is the wall bound.  An unbounded plan cannot invent one, so it is
 * `null` here and the caller must not send a `budget` object at all (the v1
 * field forbids an implicit default; a non-empty budget without a bound is the
 * D09 "silently dropped" defect).
 */
export function toPlatformBudget(
  budget: PlanBudget,
): Readonly<{ unit: "task_count"; count: number; timeout_s: number | null }> {
  const limits = toResourceLimits(budget);
  return Object.freeze({
    unit: "task_count",
    count: budget.task_refs,
    timeout_s: limits.local_terminate_after_s,
  });
}

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
