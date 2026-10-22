/**
 * `reviewer` skill — Critique phase.
 *
 * The reviewer can block a round (a safety veto) or request changes, but its
 * verdict is advice: the Supervisor still requires preflight, platform
 * admission and approval, and the next round is decided by the real
 * `EvidenceReport`, not by this verdict.
 */

import type { EvidenceReport } from "./models.js";
import { EVIDENCE_REPORT_SCHEMA, blockingClaimStatus } from "./models.js";
import type { FieldSpec, SkillSpec, ValidationIssue } from "./schema.js";
import { validateSkillObject } from "./schema.js";

export const REVIEWER_SKILL = "reviewer";
export const REVIEWER_SKILL_VERSION = "1.0.0";
export const REVIEWER_INPUT_SCHEMA = "acp2.skill.reviewer.input.v1";
export const REVIEWER_OUTPUT_SCHEMA = "acp2.skill.reviewer.output.v1";

export interface ReviewerFinding {
  readonly code: string;
  readonly severity: "info" | "warning" | "blocking";
  readonly message: string;
}

export interface ReviewerInput {
  readonly schema: typeof REVIEWER_INPUT_SCHEMA;
  readonly skill: typeof REVIEWER_SKILL;
  readonly skill_version: typeof REVIEWER_SKILL_VERSION;
  readonly project_id: string;
  readonly round: number;
  readonly hypothesis: string;
  readonly plan_definition_digest: string | null;
  readonly evidence: EvidenceReport | null;
}

export interface ReviewerOutput {
  readonly schema: typeof REVIEWER_OUTPUT_SCHEMA;
  readonly skill: typeof REVIEWER_SKILL;
  readonly skill_version: typeof REVIEWER_SKILL_VERSION;
  readonly verdict: "approve" | "revise" | "stop";
  readonly findings: readonly ReviewerFinding[];
  readonly required_changes: readonly string[];
  readonly advice: string;
  readonly evidence_refs: readonly string[];
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

export const REVIEWER_INPUT_SPEC: SkillSpec = {
  skill: REVIEWER_SKILL,
  schema: REVIEWER_INPUT_SCHEMA,
  version: REVIEWER_SKILL_VERSION,
  fields: {
    project_id: stringField(),
    round: { kind: "integer", required: true, min: 0 },
    hypothesis: stringField(),
    plan_definition_digest: {
      kind: "string",
      required: true,
      nullable: true,
      pattern: /^sha256:[0-9a-f]{64}$/,
    },
    evidence: { kind: "object", required: true, nullable: true, passthrough: true },
  },
};

export const REVIEWER_OUTPUT_SPEC: SkillSpec = {
  skill: REVIEWER_SKILL,
  schema: REVIEWER_OUTPUT_SCHEMA,
  version: REVIEWER_SKILL_VERSION,
  fields: {
    verdict: stringField({ enum: ["approve", "revise", "stop"] }),
    findings: {
      kind: "array",
      required: true,
      items: {
        kind: "object",
        required: true,
        fields: {
          code: stringField(),
          severity: stringField({ enum: ["info", "warning", "blocking"] }),
          message: stringField(),
        },
      },
    },
    required_changes: stringArray({ unique: true }),
    advice: stringField(),
    evidence_refs: stringArray({ unique: true }),
  },
};

export function validateReviewerInput(value: unknown): ValidationIssue[] {
  const issues = validateSkillObject(value, REVIEWER_INPUT_SPEC);
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

export function validateReviewerOutput(value: unknown): ValidationIssue[] {
  return validateSkillObject(value, REVIEWER_OUTPUT_SPEC);
}

/**
 * Deterministic offline critic.
 *
 * It flags a missing plan digest as blocking and refuses to keep going on a
 * refuted/inconclusive report. No check here can upgrade a claim to supported.
 */
export function runReviewer(input: ReviewerInput): ReviewerOutput {
  const issues = validateReviewerInput(input);
  if (issues.length > 0) {
    throw new TypeError(
      `reviewer input invalid: ${issues.map((item) => item.path).join(",")}`,
    );
  }
  const findings: ReviewerFinding[] = [];
  const report = input.evidence;
  const blocking = report === null ? null : blockingClaimStatus(report);
  if (blocking !== null) {
    findings.push({
      code: `evidence_${blocking}`,
      severity: "blocking",
      message: `the report carries a ${blocking} claim; no expensive retry`,
    });
  }
  if (input.plan_definition_digest === null) {
    findings.push({
      code: "plan_digest_missing",
      severity: "blocking",
      message: "a plan is required before submission",
    });
  }
  const verdict: ReviewerOutput["verdict"] =
    blocking !== null
      ? "stop"
      : findings.some((finding) => finding.severity === "blocking")
        ? "revise"
        : "approve";
  return Object.freeze({
    schema: REVIEWER_OUTPUT_SCHEMA,
    skill: REVIEWER_SKILL,
    skill_version: REVIEWER_SKILL_VERSION,
    verdict,
    findings: Object.freeze(findings),
    required_changes: Object.freeze(
      findings
        .filter((finding) => finding.severity !== "info")
        .map((finding) => finding.code),
    ),
    advice:
      verdict === "approve"
        ? "preflight and admission may proceed"
        : "do not submit until the blocking finding is resolved",
    evidence_refs: Object.freeze(report === null ? [] : [...report.evidence_refs]),
  });
}
