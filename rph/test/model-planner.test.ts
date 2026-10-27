/**
 * M3-03: the DSH model planner adapter.
 *
 * A model answer is a *candidate*: it must pass the existing planner schema and
 * the scientific validator, and it must preserve the confirmed charge,
 * multiplicity, electronic state, method, mapping and input provenance.  A
 * missing critical value is a clarification; invalid output is a rejection.
 * Neither path can reach the Supervisor as a plan, so nothing is auto-submitted.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FIXTURE_PLANNER,
  FixturePlannerRejectedError,
  ModelPlanRejectedError,
  PLANNER_INPUT_SCHEMA,
  PLANNER_OUTPUT_SCHEMA,
  PLANNER_SKILL,
  PLANNER_SKILL_VERSION,
  ProductionPlannerRequiredError,
  composeProductionSkills,
  createModelPlanner,
  planCandidate,
  toPlanScienceInputs,
  validatePlannerOutput,
  type ModelPlanGatewayRequest,
  type ModelPlanGatewayResponse,
  type ModelPlanGatewayTransport,
  type PlanBudget,
  type PlannerInput,
  type ScienceSeed,
} from "@rph/dsh-bundle";

import { TEST_OPTIONS } from "./harness.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const OTHER_DIGEST = `sha256:${"b".repeat(64)}`;
const PROJECT = "project-m3-03";
const RESEARCH = "res-m3-03";

function budget(overrides: Partial<PlanBudget> = {}): PlanBudget {
  return {
    model_requests: 8,
    input_tokens: 20_000,
    output_tokens: 10_000,
    task_refs: 4,
    recipe_steps: 4,
    qc_invocations: 4,
    wall_seconds: 7_200,
    queue_seconds: 600,
    ...overrides,
  };
}

function seed(overrides: Partial<ScienceSeed> = {}): ScienceSeed {
  return {
    method: "wB97X-D4",
    mapping: "atom-map-2",
    charge: 1,
    multiplicity: 2,
    electronic_state: {
      charge: 1,
      multiplicity: 2,
      state_label: "doublet",
      spin_polarization: 1,
    },
    structure_provenance: [
      {
        input_id: "in-cation",
        sha256: DIGEST,
        format: "xyz",
        role: "reactant_complex",
      },
    ],
    constraints: [{ kind: "bond" as const, atoms: [0, 1], target: "1.42", unit: "angstrom" }],
    ...overrides,
  };
}

function plannerInput(scienceSeed: ScienceSeed, digest = DIGEST): PlannerInput {
  const first = scienceSeed.structure_provenance[0]!;
  return {
    schema: PLANNER_INPUT_SCHEMA,
    skill: PLANNER_SKILL,
    skill_version: PLANNER_SKILL_VERSION,
    project_id: PROJECT,
    round: 0,
    hypothesis: "a charged doublet reacts through a saddle",
    normalized_question: "is the cation path lower?",
    recipe_digest: `sha256:${"c".repeat(64)}`,
    plan_definition_digest: digest,
    initial_input: {
      input_id: first.input_id,
      sha256: first.sha256,
      format: first.format,
      role: first.role,
    },
    science_seed: scienceSeed,
    critique_findings: ["reviewer:info"],
    evidence: null,
    remaining_budget: budget(),
  };
}

function planBody(input: PlannerInput): Record<string, unknown> {
  const inputs = structuredClone(toPlanScienceInputs(input.science_seed));
  return {
    schema: PLANNER_OUTPUT_SCHEMA,
    skill: PLANNER_SKILL,
    skill_version: PLANNER_SKILL_VERSION,
    plan_id: "plan-model-r0",
    project_id: input.project_id,
    round: input.round,
    hypothesis: input.hypothesis,
    recipe_digest: input.recipe_digest,
    plan_definition_digest: input.plan_definition_digest,
    science: {
      capability_steps: [
        { step_index: 0, capability: "geometry_optimization", version: "1.0.0", parameters: {} },
      ],
      initial_inputs: input.science_seed.structure_provenance.map((item) => ({
        input_id: item.input_id,
        sha256: item.sha256,
        format: item.format,
        role: item.role,
      })),
      method: input.science_seed.method,
      mapping: input.science_seed.mapping,
      charge: inputs.charge,
      multiplicity: inputs.multiplicity,
      allowed_substitutions: [],
    },
    science_inputs: inputs,
    budget: {
      model_requests: 1,
      input_tokens: 1_000,
      output_tokens: 500,
      task_refs: 1,
      recipe_steps: 1,
      qc_invocations: 1,
      wall_seconds: 600,
      queue_seconds: 60,
    },
    notes: ["model candidate"],
  };
}

function answer(input: PlannerInput, mutate?: (body: Record<string, unknown>) => void): string {
  const body = planBody(input);
  mutate?.(body);
  return JSON.stringify(body);
}

class FakeTransport implements ModelPlanGatewayTransport {
  readonly calls: ModelPlanGatewayRequest[] = [];
  output = "";
  usage = { input_tokens: 120, output_tokens: 40, complete: true, source: "provider-reported" };

  async plan(request: ModelPlanGatewayRequest): Promise<ModelPlanGatewayResponse> {
    this.calls.push(request);
    return {
      output: this.output,
      usage: this.usage,
      receipt_id: `modelrcpt|${PROJECT}|${RESEARCH}|ses-1|0|1|${DIGEST}`,
    };
  }
}

function options(transport: ModelPlanGatewayTransport) {
  return { research_id: RESEARCH, model: "approved-model", transport };
}

// ---------------------------------------------------------------------------
// preserved science
// ---------------------------------------------------------------------------

test("model planner: charge=1/multiplicity=2 and a non-default method are preserved", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input);
  const candidate = await planCandidate(input, options(transport));

  assert.equal(candidate.status, "ok");
  if (candidate.status !== "ok") return;
  assert.equal(candidate.plan.science.charge, 1);
  assert.equal(candidate.plan.science.multiplicity, 2);
  assert.equal(candidate.plan.science_inputs.charge, 1);
  assert.equal(candidate.plan.science_inputs.multiplicity, 2);
  assert.equal(candidate.plan.science.method, "wB97X-D4");
  assert.equal(candidate.plan.science.mapping, "atom-map-2");
  assert.deepEqual(candidate.plan.science_inputs.electronic_state, {
    charge: 1,
    multiplicity: 2,
    state_label: "doublet",
    reference_state: null,
    spin_polarization: 1,
  });
  assert.equal(candidate.plan.science_inputs.constraints.length, 1);
  assert.equal(candidate.plan.science_inputs.constraints[0]?.kind, "bond");
  assert.equal(candidate.plan.plan_definition_digest, DIGEST);
  // The platform gateway receipt and usage travel with the candidate.
  assert.match(candidate.receipt_id, /^modelrcpt\|/);
  assert.equal(candidate.usage?.complete, true);
  assert.equal(transport.calls.length, 1);
});

test("model planner: multi-input provenance is preserved in order", TEST_OPTIONS, async () => {
  const multi = seed({
    structure_provenance: [
      { input_id: "in-reactant", sha256: DIGEST, format: "xyz", role: "reactant_complex" },
      { input_id: "in-ts", sha256: OTHER_DIGEST, format: "xyz", role: "transition_state" },
    ],
  });
  const input = plannerInput(multi);
  const transport = new FakeTransport();
  transport.output = answer(input);

  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "ok");
  if (candidate.status !== "ok") return;
  assert.deepEqual(
    candidate.plan.science.initial_inputs.map((item) => item.input_id),
    ["in-reactant", "in-ts"],
  );
  assert.deepEqual(
    candidate.plan.science_inputs.structure_provenance.map((item) => item.role),
    ["reactant_complex", "transition_state"],
  );
});

// ---------------------------------------------------------------------------
// clarification / invalid: never a plan, never a submit
// ---------------------------------------------------------------------------

test("model planner: missing charge is a clarification, never 0", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input, (body) => {
    const science = body["science"] as Record<string, unknown>;
    const inputs = body["science_inputs"] as Record<string, unknown>;
    delete science["charge"];
    delete inputs["charge"];
  });

  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "clarification");
  if (candidate.status !== "clarification") return;
  assert.ok(candidate.missing.includes("charge"));
  assert.ok(candidate.questions.some((question) => /charge/i.test(question)));

  const planner = createModelPlanner(options(transport));
  await assert.rejects(
    planner.plan(input),
    (error: unknown) => error instanceof ModelPlanRejectedError && error.status === "clarification",
  );
  // One candidate call plus the rejected skill attempt; no submit path exists.
  assert.equal(transport.calls.length, 2);
});

test("model planner: missing multiplicity is a clarification, never 1", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input, (body) => {
    const inputs = body["science_inputs"] as Record<string, unknown>;
    delete inputs["multiplicity"];
  });
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "clarification");
  if (candidate.status !== "clarification") return;
  assert.ok(candidate.missing.includes("multiplicity"));
});

test("model planner: non-JSON model text is an invalid candidate", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = "I will submit this plan now.";
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "invalid");
  if (candidate.status !== "invalid") return;
  assert.equal(candidate.reason, "planner_output_not_json");
});

test("model planner: an empty answer is invalid, not a clarification", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = "{}";
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "invalid");
  if (candidate.status !== "invalid") return;
  assert.equal(candidate.reason, "planner_output_invalid");
});

test("model planner: schema-invalid model output is rejected", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input, (body) => {
    const inputs = body["science_inputs"] as Record<string, unknown>;
    inputs["multiplicity"] = "2";
  });
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "invalid");
  if (candidate.status !== "invalid") return;
  assert.equal(candidate.reason, "planner_output_invalid");
  assert.ok(candidate.issues.some((issue) => issue.path.includes("multiplicity")));
});

test("model planner: a changed charge is science drift, not a silent correction", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input, (body) => {
    const science = body["science"] as Record<string, unknown>;
    const inputs = body["science_inputs"] as Record<string, unknown>;
    science["charge"] = 0;
    inputs["charge"] = 0;
  });
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "invalid");
  if (candidate.status !== "invalid") return;
  assert.equal(candidate.reason, "science_drift");
  assert.ok(candidate.issues.some((issue) => issue.path.includes("charge")));
});

test("model planner: a changed method or input provenance is science drift", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input, (body) => {
    const science = body["science"] as Record<string, unknown>;
    science["method"] = "r2SCAN-3c";
  });
  const methodCandidate = await planCandidate(input, options(transport));
  assert.equal(methodCandidate.status, "invalid");
  if (methodCandidate.status !== "invalid") return;
  assert.equal(methodCandidate.reason, "science_drift");

  const transport2 = new FakeTransport();
  transport2.output = answer(input, (body) => {
    const inputs = body["science_inputs"] as Record<string, unknown>;
    const provenance = inputs["structure_provenance"] as Record<string, unknown>[];
    provenance[0]!["sha256"] = OTHER_DIGEST;
  });
  const provenanceCandidate = await planCandidate(input, options(transport2));
  assert.equal(provenanceCandidate.status, "invalid");
});

test("model planner: a platform digest mismatch is rejected", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input, (body) => {
    body["plan_definition_digest"] = OTHER_DIGEST;
  });
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "invalid");
  if (candidate.status !== "invalid") return;
  assert.equal(candidate.reason, "platform_digest_mismatch");
});

test("model planner: a model may not widen the remaining budget", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input, (body) => {
    const planBudget = body["budget"] as Record<string, unknown>;
    planBudget["wall_seconds"] = 999_999;
  });
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "invalid");
  if (candidate.status !== "invalid") return;
  assert.equal(candidate.reason, "budget_widened");
});

test("model planner: candidate schema is the existing planner output schema", TEST_OPTIONS, async () => {
  const input = plannerInput(seed());
  const transport = new FakeTransport();
  transport.output = answer(input);
  const candidate = await planCandidate(input, options(transport));
  assert.equal(candidate.status, "ok");
  if (candidate.status !== "ok") return;
  assert.deepEqual(validatePlannerOutput(candidate.plan), []);
});

// ---------------------------------------------------------------------------
// product composition: fixture is refused fail-fast
// ---------------------------------------------------------------------------

test("composition: the fixture planner is refused as a product fallback", TEST_OPTIONS, () => {
  assert.throws(
    () => composeProductionSkills({ planner: FIXTURE_PLANNER }),
    FixturePlannerRejectedError,
  );
  assert.throws(() => composeProductionSkills({ planner: null }), ProductionPlannerRequiredError);
  assert.throws(
    () => composeProductionSkills({ planner: { kind: "other", plan: FIXTURE_PLANNER.plan } }),
    ProductionPlannerRequiredError,
  );
});

test("composition: the model adapter is the accepted product planner", TEST_OPTIONS, async () => {
  const transport = new FakeTransport();
  const planner = createModelPlanner(options(transport));
  assert.equal(planner.kind, "model");
  const skills = composeProductionSkills({ planner });
  assert.equal(skills.planner_kind, "model");

  const input = plannerInput(seed());
  transport.output = answer(input);
  const plan = await skills.plan(input);
  assert.equal(plan.science.charge, 1);
  assert.equal(plan.science.multiplicity, 2);
});
