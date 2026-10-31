/**
 * DSH model planner adapter (M3-03): a **candidate** typed plan, never a submit.
 *
 * The model is called through an injected gateway transport
 * (`ModelPlanGatewayTransport`).  The adapter holds no provider URL and no
 * credential: the Controller model gateway owns both and reserves the planning
 * `model_request` before the provider call.
 *
 * The adapter's job is to turn one model answer into either:
 *
 *   - `status: "ok"` — a plan that passed `validatePlannerOutput` **and** the
 *     scientific validator, with charge/multiplicity/electronic state/method/
 *     input provenance/constraints preserved verbatim from the typed seed;
 *   - `status: "clarification"` — a missing critical value; the caller must ask
 *     the human.  Nothing is defaulted (charge is never 0, multiplicity never 1);
 *   - `status: "invalid"` — a schema/validator/digest failure.
 *
 * `createModelPlanner()` is the Supervisor skill: it throws
 * `ModelPlanRejectedError` for clarification/invalid, so the Supervisor stops
 * before preflight/approval/submit.  There is no automatic submit path here.
 *
 * The platform `plan_definition_digest` (the approval-bound scientific digest)
 * is supplied by the platform in `PlannerInput`.  A model answer carrying a
 * different digest is rejected (`platform_digest_mismatch`); this adapter never
 * recomputes or rewrites the frozen D06 identity (that lives in `@rph/research`).
 */

import type {
  ElectronicState,
  PlanBudget,
  PlanScience,
  PlanScienceInputs,
  ScienceConstraint,
  ScienceSeed,
  StructureProvenance,
} from "./models.js";
import { validateScienceSeed } from "./models.js";
import type { PlannerInput, PlannerOutput } from "./planner.js";
import {
  PLANNER_INPUT_SCHEMA,
  PLANNER_OUTPUT_SCHEMA,
  PLANNER_SKILL,
  PLANNER_SKILL_VERSION,
  validatePlannerOutput,
} from "./planner.js";
import type { ValidationIssue } from "./schema.js";

/** The product binding kind; `composition.ts` accepts only this kind. */
export const MODEL_PLANNER_KIND = "model" as const;
export const MODEL_PLANNER_SKILL_VERSION = "1.0.0";
export const MODEL_PLANNER_SYSTEM_PROMPT = [
  "You are the ACP2 research planner.",
  "Return exactly ONE JSON object and nothing else (no markdown, no prose).",
  "The object must carry schema=acp2.skill.planner.output.v1, skill=planner,",
  `skill_version=${PLANNER_SKILL_VERSION}, plan_id, project_id, round, hypothesis,`,
  "recipe_digest, plan_definition_digest, science, science_inputs, budget, notes.",
  "science MUST contain exactly: capability_steps (an array of objects",
  "{step_index, capability, version, parameters}), initial_inputs (an array",
  "of {input_id, sha256, format, role} taken from the seed's",
  "structure_provenance), method, mapping, charge, multiplicity,",
  "allowed_substitutions (an array). science MUST NOT contain",
  "electronic_state, structure_provenance or constraints.",
  "science_inputs MUST be ONE OBJECT with exactly charge, multiplicity,",
  "electronic_state, structure_provenance and constraints, copied EXACTLY",
  "from science_seed (electronic_state: null when the seed has none);",
  "it is never an array.",
  "budget declares what this plan itself STILL NEEDS — never a copy of",
  "remaining_budget: model_requests, input_tokens and output_tokens reserve",
  "only FUTURE model calls of this research (use 0 when this plan needs no",
  "further model calls); task_refs, recipe_steps, qc_invocations,",
  "wall_seconds and queue_seconds state the execution it requests, each",
  "never above remaining_budget. notes is an ARRAY of short strings.",
  "Echo recipe_digest, plan_definition_digest, project_id and round verbatim",
  "from the request; never default, round, re-map or invent scientific values.",
  "If a critical value is missing, return",
  '{"status":"clarification","missing":["<field>",...],"questions":["<question>",...]}',
  "instead of guessing.",
  "Never claim an approval, never submit, never widen the budget.",
].join(" ");

/** Trusted usage reported by the platform gateway (never recomputed here). */
export interface ModelPlanGatewayUsage {
  readonly input_tokens: number | null;
  readonly output_tokens: number | null;
  readonly complete: boolean;
  readonly source: string;
}

/** One gateway-routed planning call (the transport is injected, not owned). */
export interface ModelPlanGatewayRequest {
  readonly phase: "plan";
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly model: string;
  readonly messages: readonly {
    readonly role: "system" | "user";
    readonly content: string;
  }[];
  readonly max_output_tokens: number;
}

/** The gateway's answer; `receipt_id` names the platform planning receipt. */
export interface ModelPlanGatewayResponse {
  readonly output: string;
  readonly usage: ModelPlanGatewayUsage | null;
  readonly receipt_id: string;
}

/** The only provider seam the adapter knows (Controller gateway). */
export interface ModelPlanGatewayTransport {
  plan(request: ModelPlanGatewayRequest): Promise<ModelPlanGatewayResponse>;
}

export interface ModelPlannerOptions {
  readonly research_id: string;
  readonly model: string;
  readonly transport: ModelPlanGatewayTransport;
  readonly max_output_tokens?: number;
}

export interface PlannerCandidateIssue {
  readonly path: string;
  readonly code: string;
  readonly message: string;
}

/** A typed plan candidate; only `ok` may reach the approval pipeline. */
export type PlannerCandidate =
  | {
      readonly status: "ok";
      readonly plan: PlannerOutput;
      readonly usage: ModelPlanGatewayUsage | null;
      readonly receipt_id: string;
    }
  | {
      readonly status: "clarification";
      readonly missing: readonly string[];
      readonly questions: readonly string[];
      readonly receipt_id: string;
    }
  | {
      readonly status: "invalid";
      readonly reason: string;
      readonly issues: readonly PlannerCandidateIssue[];
      readonly receipt_id: string;
    };

/** The product planner binding (kind is explicit for composition fail-fast). */
export interface ProductionPlannerBinding {
  readonly kind: typeof MODEL_PLANNER_KIND;
  plan(input: PlannerInput): Promise<PlannerOutput>;
}

/** A candidate that must not be submitted; carries the typed candidate. */
export type RejectedPlannerCandidate = Extract<
  PlannerCandidate,
  { readonly status: "clarification" | "invalid" }
>;

/** A candidate that must not be submitted; carries the typed candidate. */
export class ModelPlanRejectedError extends Error {
  readonly status: "clarification" | "invalid";
  readonly candidate: RejectedPlannerCandidate;

  constructor(candidate: RejectedPlannerCandidate) {
    super(
      candidate.status === "clarification"
        ? `science clarification required: ${candidate.missing.join(",")}`
        : `planner output rejected: ${candidate.reason}`,
    );
    this.name = "ModelPlanRejectedError";
    this.status = candidate.status === "clarification" ? "clarification" : "invalid";
    this.candidate = candidate;
  }
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Deterministic key-sorted JSON for drift comparisons (arrays stay ordered). */
function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(",")}]`;
  }
  if (isRecord(value)) {
    const keys = Object.keys(value).sort();
    return `{${keys
      .filter((key) => value[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

function provenanceProjection(
  items: readonly StructureProvenance[],
): readonly Readonly<Record<string, unknown>>[] {
  return items.map((item) => ({
    input_id: item.input_id,
    sha256: item.sha256,
    format: item.format,
    role: item.role,
    source: item.source ?? null,
  }));
}

function constraintProjection(
  items: readonly ScienceConstraint[],
): readonly Readonly<Record<string, unknown>>[] {
  return items.map((item) => ({
    kind: item.kind,
    atoms: [...item.atoms],
    target: item.target,
    unit: item.unit ?? null,
  }));
}

function stateProjection(
  state: ElectronicState | null,
): Readonly<Record<string, unknown>> | null {
  if (state === null) {
    return null;
  }
  return {
    charge: state.charge,
    multiplicity: state.multiplicity,
    state_label: state.state_label ?? null,
    reference_state: state.reference_state ?? null,
    spin_polarization: state.spin_polarization ?? null,
  };
}

function inputProjection(
  items: readonly {
    readonly input_id: string;
    readonly sha256: string;
    readonly format: string;
    readonly role: string | null;
  }[],
): readonly Readonly<Record<string, unknown>>[] {
  return items.map((item) => ({
    input_id: item.input_id,
    sha256: item.sha256,
    format: item.format,
    role: item.role,
  }));
}

function parseModelAnswer(text: string): Readonly<Record<string, unknown>> | null {
  let body = text.trim();
  if (body.startsWith("```")) {
    const newline = body.indexOf("\n");
    body = newline >= 0 ? body.slice(newline + 1) : body;
    const fence = body.lastIndexOf("```");
    if (fence >= 0) {
      body = body.slice(0, fence);
    }
  }
  try {
    const parsed: unknown = JSON.parse(body);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Collect the never-defaulted scientific values that the model omitted. */
function missingCriticalValues(answer: Readonly<Record<string, unknown>>): readonly string[] {
  const missing: string[] = [];
  const science = isRecord(answer["science"]) ? answer["science"] : null;
  const inputs = isRecord(answer["science_inputs"]) ? answer["science_inputs"] : null;
  // A missing block is a schema failure (invalid), not an uncertain value;
  // only an absent value inside a present block is a clarification.
  if (science !== null) {
    for (const field of ["method", "mapping"] as const) {
      const value = science[field];
      if (value === undefined || value === null || value === "") {
        missing.push(field);
      }
    }
  }
  if (inputs !== null) {
    for (const field of ["charge", "multiplicity"] as const) {
      const value = inputs[field];
      // Only an absent value is a clarification; a wrong type is an invalid
      // output and is left to the schema validator.
      if (value === undefined || value === null) {
        missing.push(field);
      }
    }
    const provenance = inputs["structure_provenance"];
    if (!Array.isArray(provenance) || provenance.length === 0) {
      missing.push("structure_provenance");
    }
  }
  return Object.freeze([...new Set(missing)]);
}

function clarificationQuestions(missing: readonly string[]): readonly string[] {
  const labels: Readonly<Record<string, string>> = Object.freeze({
    charge: "What is the total charge of the system?",
    multiplicity: "What is the spin multiplicity (2S+1) of the electronic state?",
    method: "Which approved electronic-structure method must be used?",
    mapping: "Which atom/input mapping identifies the structure?",
    structure_provenance: "Which exact input bytes (id + sha256) define the structure?",
    science: "Which scientific plan values apply to this round?",
    science_inputs: "Which typed scientific inputs apply to this round?",
  });
  return Object.freeze(
    missing.map((field) => labels[field] ?? `Please confirm ${field}.`),
  );
}

function issueOf(issue: ValidationIssue): PlannerCandidateIssue {
  return Object.freeze({ path: issue.path, code: issue.code, message: issue.message });
}

function invalid(
  reason: string,
  issues: readonly ValidationIssue[],
  receipt_id: string,
): PlannerCandidate {
  return Object.freeze({
    status: "invalid" as const,
    reason,
    issues: Object.freeze(issues.map(issueOf)),
    receipt_id,
  });
}

function candidateAnswer(text: string): Readonly<Record<string, unknown>> | null {
  const answer = parseModelAnswer(text);
  if (answer === null) {
    return null;
  }
  if (!("schema" in answer) && !("skill" in answer) && !("skill_version" in answer)) {
    // The model answered with the body only; add the non-scientific envelope.
    return {
      ...answer,
      schema: PLANNER_OUTPUT_SCHEMA,
      skill: PLANNER_SKILL,
      skill_version: PLANNER_SKILL_VERSION,
    };
  }
  return answer;
}

/** Validate one model answer against the schema, the science and the platform digest. */
export function validateModelPlanCandidate(
  text: string,
  input: PlannerInput,
  receiptId = "",
): PlannerCandidate {
  const answer = candidateAnswer(text);
  if (answer === null) {
    return invalid(
      "planner_output_not_json",
      [{ path: "$", code: "type", message: "model output is not one JSON object" }],
      receiptId,
    );
  }
  if (answer["status"] === "clarification") {
    const rawMissing = answer["missing"];
    const missing = Array.isArray(rawMissing)
      ? rawMissing.filter((item): item is string => typeof item === "string" && item.length > 0)
      : [];
    if (missing.length === 0) {
      return invalid(
        "clarification_without_missing_fields",
        [{ path: "$.missing", code: "required", message: "clarification needs fields" }],
        receiptId,
      );
    }
    const rawQuestions = answer["questions"];
    const questions = Array.isArray(rawQuestions)
      ? rawQuestions.filter((item): item is string => typeof item === "string" && item.length > 0)
      : [];
    return Object.freeze({
      status: "clarification" as const,
      missing: Object.freeze(missing),
      questions: Object.freeze(
        questions.length > 0 ? questions : clarificationQuestions(missing),
      ),
      receipt_id: receiptId,
    });
  }

  const missing = missingCriticalValues(answer);
  if (missing.length > 0) {
    return Object.freeze({
      status: "clarification" as const,
      missing,
      questions: clarificationQuestions(missing),
      receipt_id: receiptId,
    });
  }

  const issues = validatePlannerOutput(answer);
  if (issues.length > 0) {
    return invalid("planner_output_invalid", issues, receiptId);
  }
  const plan = answer as unknown as PlannerOutput;
  const science = plan.science;
  const scienceInputs = plan.science_inputs;

  const seedIssues = validateScienceSeed({
    method: science.method,
    mapping: science.mapping,
    charge: scienceInputs.charge,
    multiplicity: scienceInputs.multiplicity,
    electronic_state: scienceInputs.electronic_state,
    structure_provenance: scienceInputs.structure_provenance,
    constraints: scienceInputs.constraints,
  });
  if (seedIssues.length > 0) {
    const required = seedIssues.filter((issue) => issue.code === "required");
    if (required.length > 0) {
      const fields = required.map((issue) => issue.path.replace("$.", ""));
      return Object.freeze({
        status: "clarification" as const,
        missing: Object.freeze(fields),
        questions: clarificationQuestions(fields),
        receipt_id: receiptId,
      });
    }
    return invalid("science_seed_invalid", seedIssues, receiptId);
  }

  // The model may reason about the plan but may never change the confirmed
  // science.  Any drift is a rejected candidate, not a silent correction.
  const drift: ValidationIssue[] = [];
  const expect = (
    path: string,
    actual: unknown,
    expected: unknown,
    code = "mismatch",
  ): void => {
    if (stableJson(actual) !== stableJson(expected)) {
      drift.push({ path, code, message: "must equal the typed science seed" });
    }
  };
  expect("$.science.method", science.method, input.science_seed.method);
  expect("$.science.mapping", science.mapping, input.science_seed.mapping);
  expect("$.science.charge", science.charge, input.science_seed.charge);
  expect("$.science.multiplicity", science.multiplicity, input.science_seed.multiplicity);
  expect("$.science_inputs.charge", scienceInputs.charge, input.science_seed.charge);
  expect(
    "$.science_inputs.multiplicity",
    scienceInputs.multiplicity,
    input.science_seed.multiplicity,
  );
  expect(
    "$.science_inputs.electronic_state",
    stateProjection(scienceInputs.electronic_state),
    stateProjection(input.science_seed.electronic_state ?? null),
  );
  expect(
    "$.science_inputs.structure_provenance",
    provenanceProjection(scienceInputs.structure_provenance),
    provenanceProjection(input.science_seed.structure_provenance),
  );
  expect(
    "$.science_inputs.constraints",
    constraintProjection(scienceInputs.constraints ?? []),
    constraintProjection(input.science_seed.constraints ?? []),
  );
  expect(
    "$.science.initial_inputs",
    inputProjection(science.initial_inputs),
    inputProjection(
      input.science_seed.structure_provenance.map((item) => ({
        input_id: item.input_id,
        sha256: item.sha256,
        format: item.format,
        role: item.role,
      })),
    ),
  );
  expect("$.project_id", plan.project_id, input.project_id);
  expect("$.round", plan.round, input.round);
  expect("$.hypothesis", plan.hypothesis, input.hypothesis);
  expect("$.recipe_digest", plan.recipe_digest, input.recipe_digest);
  if (drift.length > 0) {
    return invalid("science_drift", drift, receiptId);
  }

  // The platform's approval-bound scientific digest is the confirmation; the
  // adapter never re-derives or rewrites it.
  if (plan.plan_definition_digest !== input.plan_definition_digest) {
    return invalid(
      "platform_digest_mismatch",
      [
        {
          path: "$.plan_definition_digest",
          code: "mismatch",
          message: "model plan digest does not match the platform-approved digest",
        },
      ],
      receiptId,
    );
  }

  // A planner can never widen the remaining budget; it may only request within it.
  const overBudget: ValidationIssue[] = [];
  for (const field of [
    "model_requests",
    "input_tokens",
    "output_tokens",
    "task_refs",
    "recipe_steps",
    "qc_invocations",
    "wall_seconds",
    "queue_seconds",
  ] as const) {
    if ((plan.budget[field] as number) > (input.remaining_budget[field] as number)) {
      overBudget.push({
        path: `$.budget.${field}`,
        code: "budget_exceeds_remaining",
        message: "must not exceed the remaining admission budget",
      });
    }
  }
  if (overBudget.length > 0) {
    return invalid("budget_widened", overBudget, receiptId);
  }

  return Object.freeze({
    status: "ok" as const,
    plan: freezePlan(plan),
    usage: null,
    receipt_id: receiptId,
  });
}

function freezePlan(plan: PlannerOutput): PlannerOutput {
  const science: PlanScience = Object.freeze({
    ...plan.science,
    capability_steps: Object.freeze(plan.science.capability_steps.map((step) => Object.freeze({ ...step }))),
    initial_inputs: Object.freeze(plan.science.initial_inputs.map((item) => Object.freeze({ ...item }))),
    allowed_substitutions: Object.freeze([...plan.science.allowed_substitutions]),
  });
  const scienceInputs: PlanScienceInputs = Object.freeze({
    ...plan.science_inputs,
    electronic_state:
      plan.science_inputs.electronic_state === null
        ? null
        : Object.freeze({ ...plan.science_inputs.electronic_state }),
    structure_provenance: Object.freeze(
      plan.science_inputs.structure_provenance.map((item) => Object.freeze({ ...item })),
    ),
    constraints: Object.freeze(
      plan.science_inputs.constraints.map((item) =>
        Object.freeze({ ...item, atoms: Object.freeze([...item.atoms]) }),
      ),
    ),
  });
  const budget: PlanBudget = Object.freeze({ ...plan.budget });
  return Object.freeze({ ...plan, science, science_inputs: scienceInputs, budget });
}

function buildPrompt(input: PlannerInput, researchId: string): readonly {
  readonly role: "system" | "user";
  readonly content: string;
}[] {
  return Object.freeze([
    Object.freeze({ role: "system" as const, content: MODEL_PLANNER_SYSTEM_PROMPT }),
    Object.freeze({
      role: "user" as const,
      content: JSON.stringify({
        project_id: input.project_id,
        research_id: researchId,
        round: input.round,
        hypothesis: input.hypothesis,
        normalized_question: input.normalized_question,
        recipe_digest: input.recipe_digest,
        plan_definition_digest: input.plan_definition_digest,
        initial_input: input.initial_input,
        science_seed: input.science_seed,
        critique_findings: input.critique_findings,
        evidence: input.evidence,
        remaining_budget: input.remaining_budget,
      }),
    }),
  ]);
}

/** Run one model answer through the gateway and validate it; never submits. */
export async function planCandidate(
  input: PlannerInput,
  options: ModelPlannerOptions,
): Promise<PlannerCandidate> {
  const messages = buildPrompt(input, options.research_id);
  const response = await options.transport.plan({
    phase: "plan",
    project_id: input.project_id,
    research_id: options.research_id,
    round: input.round,
    model: options.model,
    messages,
    max_output_tokens: options.max_output_tokens ?? 800,
  });
  const candidate = validateModelPlanCandidate(response.output, input, response.receipt_id);
  if (candidate.status !== "ok") {
    return candidate;
  }
  return Object.freeze({ ...candidate, usage: response.usage });
}

/**
 * The production planner skill: a candidate `ok` plan or a typed rejection.
 *
 * The rejection is what keeps "invalid/clarification" from reaching
 * preflight/approval/submit: the Supervisor sees an exception, not a plan.
 */
export function createModelPlanner(options: ModelPlannerOptions): ProductionPlannerBinding {
  return Object.freeze({
    kind: MODEL_PLANNER_KIND,
    async plan(input: PlannerInput): Promise<PlannerOutput> {
      const candidate = await planCandidate(input, options);
      if (candidate.status !== "ok") {
        throw new ModelPlanRejectedError(candidate);
      }
      return candidate.plan;
    },
  });
}

/** The input schema a model answer is validated against (diagnostics). */
export const MODEL_PLANNER_INPUT_SCHEMA = PLANNER_INPUT_SCHEMA;
