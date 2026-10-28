/**
 * Product composition gate (M3-03 / M3-02 seam).
 *
 * The production research loop has exactly one planner kind: a model adapter
 * routed through the Controller model gateway.  The deterministic
 * `runFixturePlanner` remains useful for offline tests and the thin slice, but it
 * can never be a fallback:
 *
 *   - `requireProductionPlanner` throws `ProductionPlannerRequiredError` when
 *     nothing was composed, and `FixturePlannerRejectedError` when a fixture was
 *     handed in;
 *   - `composeProductionSkills` returns a `SupervisorSkills` with
 *     `planner_kind: "model"`, so `runSupervisor(..., { require_model_gateway:
 *     true })` refuses a fixture even if a caller bypassed this module.
 *
 * Composition is synchronous and side-effect free: it validates the injected
 * bindings only.  The real gateway transport (bridge/Controller) is injected by
 * the product entry, never constructed here.
 */

import { FixturePlannerRejectedError, ProductionPlannerRequiredError } from "./planner.js";
import type { PlannerInput, PlannerOutput } from "./planner.js";
import { MODEL_PLANNER_KIND } from "./model-planner.js";
import { DEFAULT_SKILL_HELPERS } from "./supervisor.js";
import type {
  SupervisorPorts,
  SupervisorResult,
  SupervisorSkills,
  SupervisorInput,
} from "./supervisor.js";
import { runSupervisor } from "./supervisor.js";

/** A planner binding as it arrives from a composition root. */
export interface PlannerBindingLike {
  readonly kind?: string;
  plan(input: PlannerInput): Promise<PlannerOutput>;
}

/** Explicit production binding; only `kind: "model"` is accepted. */
export interface ProductionPlanner {
  readonly kind: typeof MODEL_PLANNER_KIND;
  plan(input: PlannerInput): Promise<PlannerOutput>;
}

/**
 * Accept only a model planner; refuse absent/fixture bindings fail-fast.
 *
 * @throws ProductionPlannerRequiredError when no binding was supplied.
 * @throws FixturePlannerRejectedError when the binding is the fixture planner.
 */
export function requireProductionPlanner(
  binding: PlannerBindingLike | null | undefined,
): ProductionPlanner {
  if (binding === null || binding === undefined) {
    throw new ProductionPlannerRequiredError();
  }
  if (typeof binding.plan !== "function") {
    throw new ProductionPlannerRequiredError("the composed planner has no plan() binding");
  }
  if (binding.kind === "fixture") {
    throw new FixturePlannerRejectedError(
      "the deterministic fixture planner was passed as a product planner",
    );
  }
  if (binding.kind !== MODEL_PLANNER_KIND) {
    throw new ProductionPlannerRequiredError(
      `unsupported planner kind ${String(binding.kind)}; only the model adapter is a product planner`,
    );
  }
  return binding as ProductionPlanner;
}

export interface ProductionSkillParts {
  readonly planner: PlannerBindingLike | null | undefined;
  readonly interpret?: SupervisorSkills["interpret"];
  readonly synthesize?: SupervisorSkills["synthesize"];
  readonly review?: SupervisorSkills["review"];
  /** Phases that spend a platform model request; defaults to `["plan"]`. */
  readonly model_phases?: readonly ("understand" | "hypothesize" | "critique" | "plan" | "evaluate")[];
}

/**
 * Compose the product supervisor skills from injected bindings.
 *
 * The three non-planning helpers default to the deterministic implementations
 * (they carry no model/provider credential); the planner is mandatory and must
 * be the model adapter.
 */
export function composeProductionSkills(parts: ProductionSkillParts): SupervisorSkills {
  const planner = requireProductionPlanner(parts.planner);
  return Object.freeze({
    interpret: parts.interpret ?? DEFAULT_SKILL_HELPERS.interpret,
    synthesize: parts.synthesize ?? DEFAULT_SKILL_HELPERS.synthesize,
    review: parts.review ?? DEFAULT_SKILL_HELPERS.review,
    planner_kind: MODEL_PLANNER_KIND,
    model_phases: Object.freeze([...(parts.model_phases ?? ["plan"])]),
    plan: (input: PlannerInput) => planner.plan(input),
  });
}

/**
 * Compose and run one supervisor round in product mode.
 *
 * `require_model_gateway: true` is forced: without the platform gateway the
 * Supervisor stops with `model_gateway_unavailable` instead of accounting locally.
 */
export function runProductionSupervisor(
  input: SupervisorInput,
  ports: SupervisorPorts,
  parts: ProductionSkillParts,
): Promise<SupervisorResult> {
  const skills = composeProductionSkills(parts);
  return runSupervisor({ ...input, require_model_gateway: true }, ports, skills);
}

/** Validate an already-built skills object for product use (fail-fast). */
export function assertProductionSkills(skills: SupervisorSkills): SupervisorSkills {
  if (skills.planner_kind === "fixture") {
    throw new FixturePlannerRejectedError(
      "the supervisor was composed with the fixture planner; a product run requires the model adapter",
    );
  }
  if (skills.planner_kind !== MODEL_PLANNER_KIND) {
    throw new ProductionPlannerRequiredError(
      "the supervisor skills do not declare the model planner kind",
    );
  }
  return skills;
}
