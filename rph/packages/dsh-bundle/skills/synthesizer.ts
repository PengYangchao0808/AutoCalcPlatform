/**
 * `synthesizer` skill — Hypothesize and Evaluate advice.
 *
 * The output is explicitly advisory (`advice`). The Supervisor never promotes
 * this field into a scientific verdict: a real `EvidenceReport` decides
 * accept/revise/stop. This is the boundary that prevents "the model says it
 * worked" from becoming a PASS.
 */

import type { EvidenceReport } from "./models.js";
import { EVIDENCE_REPORT_SCHEMA, blockingClaimStatus } from "./models.js";
import type { FieldSpec, SkillSpec, ValidationIssue } from "./schema.js";
import { validateSkillObject } from "./schema.js";

export const SYNTHESIZER_SKILL = "synthesizer";
export const SYNTHESIZER_SKILL_VERSION = "1.0.0";
export const SYNTHESIZER_INPUT_SCHEMA = "acp2.skill.synthesizer.input.v1";
export const SYNTHESIZER_OUTPUT_SCHEMA = "acp2.skill.synthesizer.output.v1";

export interface SynthesizerInput {
  readonly schema: typeof SYNTHESIZER_INPUT_SCHEMA;
  readonly skill: typeof SYNTHESIZER_SKILL;
  readonly skill_version: typeof SYNTHESIZER_SKILL_VERSION;
  readonly project_id: string;
  readonly round: number;
  readonly interpretation: {
    readonly normalized_question: string;
    readonly scope: string;
    readonly assumptions: readonly string[];
  };
  readonly evidence: EvidenceReport | null;
  readonly prior_hypotheses: readonly string[];
}

export interface ExpectedClaim {
  readonly claim_id: string;
  readonly statement: string;
}

export interface SynthesizerOutput {
  readonly schema: typeof SYNTHESIZER_OUTPUT_SCHEMA;
  readonly skill: typeof SYNTHESIZER_SKILL;
  readonly skill_version: typeof SYNTHESIZER_SKILL_VERSION;
  readonly hypothesis: string | null;
  readonly rationale: string;
  /** Advisory only. The Supervisor ignores it when the report blocks. */
  readonly advice: "continue" | "revise" | "stop";
  readonly expected_claims: readonly ExpectedClaim[];
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

const INTERPRETATION_SPEC: FieldSpec = {
  kind: "object",
  required: true,
  fields: {
    normalized_question: stringField(),
    scope: stringField(),
    assumptions: stringArray({ unique: true }),
  },
};

export const SYNTHESIZER_INPUT_SPEC: SkillSpec = {
  skill: SYNTHESIZER_SKILL,
  schema: SYNTHESIZER_INPUT_SCHEMA,
  version: SYNTHESIZER_SKILL_VERSION,
  fields: {
    project_id: stringField(),
    round: { kind: "integer", required: true, min: 0 },
    interpretation: INTERPRETATION_SPEC,
    evidence: { kind: "object", required: true, nullable: true, passthrough: true },
    prior_hypotheses: stringArray({ unique: true }),
  },
};

export const SYNTHESIZER_OUTPUT_SPEC: SkillSpec = {
  skill: SYNTHESIZER_SKILL,
  schema: SYNTHESIZER_OUTPUT_SCHEMA,
  version: SYNTHESIZER_SKILL_VERSION,
  fields: {
    hypothesis: { kind: "string", required: true, nullable: true },
    rationale: stringField(),
    advice: stringField({ enum: ["continue", "revise", "stop"] }),
    expected_claims: {
      kind: "array",
      required: true,
      items: {
        kind: "object",
        required: true,
        fields: {
          claim_id: stringField(),
          statement: stringField(),
        },
      },
    },
    evidence_refs: stringArray({ unique: true }),
  },
};

export function validateSynthesizerInput(value: unknown): ValidationIssue[] {
  const issues = validateSkillObject(value, SYNTHESIZER_INPUT_SPEC);
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

export function validateSynthesizerOutput(value: unknown): ValidationIssue[] {
  return validateSkillObject(value, SYNTHESIZER_OUTPUT_SPEC);
}

/**
 * Deterministic offline synthesizer.
 *
 * It produces one candidate hypothesis and an explicitly advisory direction.
 * A refuted/inconclusive report yields `stop` advice here as a convenience,
 * but the Supervisor does not depend on that: it re-checks the report itself.
 */
export function runSynthesizer(input: SynthesizerInput): SynthesizerOutput {
  const issues = validateSynthesizerInput(input);
  if (issues.length > 0) {
    throw new TypeError(
      `synthesizer input invalid: ${issues.map((item) => item.path).join(",")}`,
    );
  }
  const report = input.evidence;
  const blocking = report === null ? null : blockingClaimStatus(report);
  if (report !== null && (blocking !== null || report.status === "missing")) {
    return Object.freeze({
      schema: SYNTHESIZER_OUTPUT_SCHEMA,
      skill: SYNTHESIZER_SKILL,
      skill_version: SYNTHESIZER_SKILL_VERSION,
      hypothesis: null,
      rationale:
        blocking === null
          ? "the report carries no evidence"
          : `the report contains a ${blocking} claim`,
      advice: "stop",
      expected_claims: Object.freeze([]),
      evidence_refs: Object.freeze([...report.evidence_refs]),
    });
  }
  const suggestions = input.prior_hypotheses.map((item) => item.trim());
  let candidate = `Round ${input.round}: ${input.interpretation.normalized_question}`;
  let suffix = 1;
  while (suggestions.includes(candidate)) {
    suffix += 1;
    candidate = `Round ${input.round} (${suffix}): ${input.interpretation.normalized_question}`;
  }
  return Object.freeze({
    schema: SYNTHESIZER_OUTPUT_SCHEMA,
    skill: SYNTHESIZER_SKILL,
    skill_version: SYNTHESIZER_SKILL_VERSION,
    hypothesis: candidate,
    rationale:
      "Test the normalized question with the frozen recipe; the EvidenceReport, not this summary, decides the outcome.",
    advice: "continue",
    expected_claims: Object.freeze([
      {
        claim_id: `claim-${input.round}-outcome`,
        statement: input.interpretation.normalized_question,
      },
    ]),
    evidence_refs: Object.freeze(report === null ? [] : [...report.evidence_refs]),
  });
}
