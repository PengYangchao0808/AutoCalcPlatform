/**
 * `interpreter` skill — Understand phase.
 *
 * Input is the user question plus the project/constraint context. Output is a
 * normalized research intent. It never decides a scientific claim and never
 * submits work; the Supervisor alone drives the state machine.
 */

import type { FieldSpec, SkillSpec, ValidationIssue } from "./schema.js";
import { validateSkillObject } from "./schema.js";
import type { ScienceSeed } from "./models.js";
import { SCIENCE_SEED_SPEC, validateScienceSeed } from "./models.js";

export const INTERPRETER_SKILL = "interpreter";
// 1.1.0 (M1-04a): typed science seed + explicit clarifications.  A missing
// charge/multiplicity is a `clarify` intent with the field named, never 0/1.
export const INTERPRETER_SKILL_VERSION = "1.1.0";
export const INTERPRETER_INPUT_SCHEMA = "acp2.skill.interpreter.input.v1";
export const INTERPRETER_OUTPUT_SCHEMA = "acp2.skill.interpreter.output.v1";

export interface InterpreterInput {
  readonly schema: typeof INTERPRETER_INPUT_SCHEMA;
  readonly skill: typeof INTERPRETER_SKILL;
  readonly skill_version: typeof INTERPRETER_SKILL_VERSION;
  readonly project_id: string;
  readonly question: string;
  readonly question_ref: string | null;
  readonly constraints: readonly string[];
  readonly available_inputs: readonly string[];
  /** Typed scientific values parsed so far (absent = not known yet). */
  readonly science_seed?: ScienceSeed | null;
}

export interface InterpreterOutput {
  readonly schema: typeof INTERPRETER_OUTPUT_SCHEMA;
  readonly skill: typeof INTERPRETER_SKILL;
  readonly skill_version: typeof INTERPRETER_SKILL_VERSION;
  readonly intent: "research" | "clarify" | "reject";
  readonly normalized_question: string;
  readonly scope: string;
  readonly assumptions: readonly string[];
  readonly missing_inputs: readonly string[];
  /** Critical scientific values that must be asked for (never defaulted). */
  readonly missing_science: readonly string[];
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

export const INTERPRETER_INPUT_SPEC: SkillSpec = {
  skill: INTERPRETER_SKILL,
  schema: INTERPRETER_INPUT_SCHEMA,
  version: INTERPRETER_SKILL_VERSION,
  fields: {
    project_id: stringField(),
    question: stringField(),
    question_ref: { kind: "string", required: true, nullable: true },
    constraints: stringArray({ unique: true }),
    available_inputs: stringArray({ unique: true }),
    science_seed: { ...SCIENCE_SEED_SPEC, required: false, nullable: true },
  },
};

export const INTERPRETER_OUTPUT_SPEC: SkillSpec = {
  skill: INTERPRETER_SKILL,
  schema: INTERPRETER_OUTPUT_SCHEMA,
  version: INTERPRETER_SKILL_VERSION,
  fields: {
    intent: stringField({ enum: ["research", "clarify", "reject"] }),
    normalized_question: stringField(),
    scope: stringField(),
    assumptions: stringArray({ unique: true }),
    missing_inputs: stringArray({ unique: true }),
    missing_science: stringArray({ unique: true }),
  },
};

export function validateInterpreterInput(
  value: unknown,
): ValidationIssue[] {
  return validateSkillObject(value, INTERPRETER_INPUT_SPEC);
}

export function validateInterpreterOutput(
  value: unknown,
): ValidationIssue[] {
  return validateSkillObject(value, INTERPRETER_OUTPUT_SPEC);
}

/**
 * Deterministic offline runner.
 *
 * A live deployment may replace this with a model-backed implementation; the
 * output still has to pass `validateInterpreterOutput` before the Supervisor
 * accepts it. A missing required input makes the intent `clarify`; it never
 * silently invents scientific parameters.
 */
export function runInterpreter(input: InterpreterInput): InterpreterOutput {
  const issues = validateInterpreterInput(input);
  if (issues.length > 0) {
    throw new TypeError(
      `interpreter input invalid: ${issues.map((item) => item.path).join(",")}`,
    );
  }
  const missing = input.available_inputs.length === 0 ? ["initial_input"] : [];
  // D06/G10: a critical scientific value that was not supplied is a question.
  // The interpreter never answers with a default charge/multiplicity.
  const missingScience: string[] = [];
  const seed = input.science_seed ?? null;
  if (seed === null) {
    missingScience.push("charge", "multiplicity");
  } else {
    const issues = validateScienceSeed(seed);
    for (const item of issues) {
      const field = item.path.replace("$.", "");
      if (
        (item.code === "required" || item.code === "type") &&
        !missingScience.includes(field)
      ) {
        missingScience.push(field);
      }
    }
    if (seed.structure_provenance.length === 0) {
      missingScience.push("structure_provenance");
    }
  }
  const intent: InterpreterOutput["intent"] =
    missing.length > 0 || missingScience.length > 0 ? "clarify" : "research";
  return Object.freeze({
    schema: INTERPRETER_OUTPUT_SCHEMA,
    skill: INTERPRETER_SKILL,
    skill_version: INTERPRETER_SKILL_VERSION,
    intent,
    normalized_question: input.question.trim().replace(/\s+/g, " "),
    scope: input.project_id,
    assumptions: Object.freeze([...input.constraints]),
    missing_inputs: Object.freeze([...missing]),
    missing_science: Object.freeze([...missingScience]),
  });
}
