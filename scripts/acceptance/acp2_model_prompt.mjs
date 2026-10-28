/**
 * Shared planner prompt contract for the M4-02 acceptance drivers.
 *
 * The chain driver (`acp2_full_chain_mcp.mjs`) and the injection driver both
 * need to tell a real model the exact key set the shipped DSH planner validator
 * (`skills/planner.js::PLANNER_OUTPUT_SPEC`) will check.  Deriving that shape
 * from the validator spec itself — instead of hand-writing a prompt — keeps the
 * two drivers from drifting and never weakens the validator: the model still
 * has to return a document that `validatePlannerOutput` and
 * `validateModelPlanCandidate` accept, and the harness never fills a value the
 * model omitted.
 */

/** Sample value for one field spec (a shape hint, never a filled answer). */
function sampleOf(field, skeletonOf) {
  if (field.nullable === true && field.kind === "object") return null;
  if (field.kind === "string") return "<string>";
  if (field.kind === "integer") return field.min !== undefined && field.min > 0 ? field.min : 0;
  if (field.kind === "number") return 0;
  if (field.kind === "boolean") return false;
  if (field.kind === "array") {
    if (field.items !== undefined && field.items.kind === "object") {
      return [skeletonOf(field.items)];
    }
    return ["<string>"];
  }
  if (field.kind === "object") return skeletonOf(field);
  return null;
}

/** The required key set of one skill spec. */
export function skeletonOf(spec) {
  const out = {};
  for (const [key, field] of Object.entries(spec.fields ?? {})) {
    if (field.required !== true) continue;
    out[key] = field.kind === "object" ? skeletonOf(field) : sampleOf(field, skeletonOf);
  }
  return out;
}

/**
 * The planner output contract for one PlannerInput, as a JSON string.
 *
 * Values that must be copied verbatim from the typed payload are pre-filled from
 * the payload (so the model copies instead of inventing); values the model has
 * to decide are left as descriptive placeholders.
 */
export function plannerFormatContract(spec, input, options = {}) {
  const seed = input.science_seed ?? {};
  const skeleton = skeletonOf(spec);
  const copy = (value, fallback) => (value === undefined ? fallback : value);
  const capability = String(options.capability ?? "singlepoint");
  skeleton.schema = spec.schema;
  skeleton.skill = spec.skill;
  skeleton.skill_version = spec.version;
  skeleton.plan_id = `plan-${input.project_id}-r${input.round}`;
  skeleton.project_id = input.project_id;
  skeleton.round = input.round;
  skeleton.hypothesis = input.hypothesis;
  skeleton.recipe_digest = input.recipe_digest;
  skeleton.plan_definition_digest = input.plan_definition_digest;
  skeleton.science = {
    capability_steps: [{ step_index: 0, capability, version: "1.0.0", parameters: {} }],
    initial_inputs: (seed.structure_provenance ?? []).map((item) => ({
      input_id: item.input_id,
      sha256: item.sha256,
      format: item.format,
      role: item.role ?? null,
    })),
    method: copy(seed.method, "<string>"),
    mapping: copy(seed.mapping, "<string>"),
    charge: copy(seed.charge, 0),
    multiplicity: copy(seed.multiplicity, 1),
    allowed_substitutions: [],
  };
  skeleton.science_inputs = {
    charge: copy(seed.charge, 0),
    multiplicity: copy(seed.multiplicity, 1),
    electronic_state: seed.electronic_state ?? null,
    structure_provenance: (seed.structure_provenance ?? []).map((item) => ({ ...item })),
    constraints: (seed.constraints ?? []).map((item) => ({ ...item })),
  };
  skeleton.budget = { ...(input.remaining_budget ?? {}) };
  skeleton.notes = ["m4-02 live plan"];
  return JSON.stringify(skeleton);
}

/** One extra user message carrying the contract (added to the skill prompt). */
export function withFormatContract(transport, contract) {
  return {
    async plan(request) {
      const messages = [
        ...request.messages,
        {
          role: "user",
          content:
            "Answer with exactly this JSON object shape (same keys, same nesting, no extra keys). " +
            "Replace the descriptive values with the corresponding payload values: " +
            contract,
        },
      ];
      return transport.plan({ ...request, messages });
    },
  };
}

/** One extra user message carrying a validator rejection (bounded repair). */
export function withRepairNote(transport, note) {
  return {
    async plan(request) {
      const messages = [
        ...request.messages,
        {
          role: "user",
          content:
            "Your previous answer was rejected by the platform validator: " +
            note +
            ". Answer again with exactly ONE JSON object and nothing else. " +
            "Copy science.method, science.mapping, science.charge, science.multiplicity, " +
            "science.initial_inputs, science_inputs.charge, science_inputs.multiplicity, " +
            "science_inputs.electronic_state, science_inputs.structure_provenance and " +
            "science_inputs.constraints verbatim from the science_seed payload, and copy " +
            "project_id, round, hypothesis, recipe_digest and plan_definition_digest verbatim too.",
        },
      ];
      return transport.plan({ ...request, messages });
    },
  };
}
