/**
 * M3-03: the Supervisor's `used.model_requests` is synchronised from the
 * platform gateway — never settled from a process-local counter.
 *
 * The old code contained a dead `used.model_requests = used.model_requests`
 * assignment.  It is replaced by a real read of the authoritative platform
 * receipt count; a higher count (external/previous spend) is honoured and a
 * count below the calls this process already reserved fails closed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EVIDENCE_REPORT_SCHEMA,
  PLANNER_INPUT_SCHEMA,
  PLANNER_SKILL,
  PLANNER_SKILL_VERSION,
  ModelPlanRejectedError,
  composeProductionSkills,
  createModelPlanner,
  runFixturePlanner,
  runInterpreter,
  runReviewer,
  runSupervisor,
  runSynthesizer,
  type EvidenceReport,
  type PlanBudget,
  type SupervisorInput,
  type SupervisorPorts,
  type SupervisorSkills,
  type TaskRef,
} from "@rph/dsh-bundle";

import { TEST_OPTIONS } from "./harness.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const PROJECT = "project-m3-03";
const RESEARCH = "res-m3-03";
const TASK: TaskRef = {
  project_id: PROJECT,
  job_id: "20261027_001_Optimize",
  attempt: 1,
  release_id: "release-1",
};

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

function report(): EvidenceReport {
  return {
    schema: EVIDENCE_REPORT_SCHEMA,
    report_id: "report-m3-03",
    project_id: PROJECT,
    research_id: RESEARCH,
    round: 0,
    task_ref: TASK,
    status: "complete",
    decision: "accept",
    claims: [
      {
        claim_id: "claim-1",
        statement: "the optimized geometry converged",
        status: "supported",
        evidence_refs: ["ev-1"],
        reason: null,
      },
    ],
    evidence_refs: ["ev-1"],
    terminal_event_key: "evt-1",
  };
}

function supervisorInput(overrides: Partial<SupervisorInput> = {}): SupervisorInput {
  return {
    project_id: PROJECT,
    research_id: RESEARCH,
    question: "which path is lower?",
    question_ref: null,
    constraints: [],
    available_inputs: ["in-1"],
    recipe_digest: `sha256:${"c".repeat(64)}`,
    initial_input: {
      input_id: "in-1",
      sha256: DIGEST,
      format: "xyz",
      role: "minimum",
    },
    science_seed: {
      method: "r2SCAN-3c",
      mapping: "identity",
      charge: 0,
      multiplicity: 1,
      electronic_state: null,
      structure_provenance: [
        { input_id: "in-1", sha256: DIGEST, format: "xyz", role: "minimum" },
      ],
      constraints: [],
    },
    limits: { max_rounds: 1, budget: budget() },
    plan_definition_digest: () => DIGEST,
    ...overrides,
  };
}

function passingPorts(): SupervisorPorts {
  return {
    preflight: async () => ({ ok: true, reason: "", checks: [] }),
    admission: async () => ({ ok: true, reason: "", approval_ref: "approval-1" }),
    approve: async () => ({ approved: true, approval_ref: "approval-1", reason: "" }),
    submit: async () => ({ ok: true, task_ref: TASK, reason: "" }),
    wait: async () => ({
      events: [{ event_key: "evt-1", type: "job.completed", task_ref: TASK, report: report() }],
      cursor: "cursor-1",
    }),
    persistTaskRef: async () => undefined,
    persistReport: async () => undefined,
  };
}

function countingSkills(counter: { calls: number }): SupervisorSkills {
  const bump = async <T>(body: () => Promise<T>): Promise<T> => {
    counter.calls += 1;
    return body();
  };
  return {
    planner_kind: "fixture",
    interpret: (input) => bump(async () => runInterpreter(input)),
    synthesize: (input) => bump(async () => runSynthesizer(input)),
    review: (input) => bump(async () => runReviewer(input)),
    plan: (input) => bump(async () => runFixturePlanner(input)),
  };
}

// ---------------------------------------------------------------------------
// fail-fast composition
// ---------------------------------------------------------------------------

test("product mode refuses the fixture planner before any call", TEST_OPTIONS, async () => {
  const counters = { calls: 0 };
  const result = await runSupervisor(
    supervisorInput({ require_model_gateway: true }),
    passingPorts(),
    countingSkills(counters),
  );
  assert.equal(result.status, "stopped");
  assert.equal(result.stop_reason, "fixture_planner_rejected");
  assert.equal(counters.calls, 0);
});

test("product mode without the gateway stops before any model call", TEST_OPTIONS, async () => {
  const counters = { calls: 0 };
  const result = await runSupervisor(
    supervisorInput({ require_model_gateway: true }),
    passingPorts(),
    { ...countingSkills(counters), planner_kind: "model" },
  );
  assert.equal(result.stop_reason, "model_gateway_unavailable");
  assert.equal(counters.calls, 0);
});

test("an invalid model answer cannot reach preflight, approval or submit", TEST_OPTIONS, async () => {
  const counters = { preflight: 0, approval: 0, submit: 0 };
  const ports: SupervisorPorts = {
    preflight: async () => {
      counters.preflight += 1;
      return { ok: true, reason: "", checks: [] };
    },
    admission: async () => ({ ok: true, reason: "", approval_ref: "approval-1" }),
    approve: async () => {
      counters.approval += 1;
      return { approved: true, approval_ref: "approval-1", reason: "" };
    },
    submit: async () => {
      counters.submit += 1;
      return { ok: true, task_ref: TASK, reason: "" };
    },
    wait: async () => ({ events: [], cursor: null }),
    modelGateway: async () => ({ model_requests: 0, receipts: [], source: "platform-receipt" }),
  };
  const planner = createModelPlanner({
    research_id: RESEARCH,
    model: "approved-model",
    transport: {
      plan: async () => ({ output: "not a plan", usage: null, receipt_id: "modelrcpt|x" }),
    },
  });
  await assert.rejects(
    runSupervisor(
      supervisorInput({ require_model_gateway: true }),
      ports,
      composeProductionSkills({ planner }),
    ),
    ModelPlanRejectedError,
  );
  assert.deepEqual(counters, { preflight: 0, approval: 0, submit: 0 });
});

// ---------------------------------------------------------------------------
// authoritative usage
// ---------------------------------------------------------------------------

test("a higher platform count is honoured before the next call", TEST_OPTIONS, async () => {
  const counters = { calls: 0 };
  const ports: SupervisorPorts = {
    ...passingPorts(),
    modelGateway: async () => ({
      model_requests: 8,
      receipts: ["modelrcpt|external"],
      source: "platform-receipt",
    }),
  };
  const result = await runSupervisor(supervisorInput(), ports, countingSkills(counters));
  assert.equal(result.stop_reason, "budget_cap");
  assert.equal(result.model_requests_used, 8);
  assert.equal(counters.calls, 0);
});

test("a deterministic helper does not consume the gateway model allowance", TEST_OPTIONS, async () => {
  // Limit 1: four deterministic skill calls must not exhaust the one real
  // model request; the plan phase is still allowed.
  const modelCalls = { calls: 0 };
  const ports: SupervisorPorts = {
    ...passingPorts(),
    modelGateway: async () => ({
      model_requests: modelCalls.calls,
      receipts: [],
      source: "platform-receipt",
    }),
  };
  const skills: SupervisorSkills = {
    planner_kind: "fixture",
    model_phases: ["plan"],
    interpret: async (input) => runInterpreter(input),
    synthesize: async (input) => runSynthesizer(input),
    review: async (input) => runReviewer(input),
    plan: async (input) => {
      modelCalls.calls += 1;
      return runFixturePlanner(input);
    },
  };
  const result = await runSupervisor(
    supervisorInput({ limits: { max_rounds: 1, budget: budget({ model_requests: 1 }) } }),
    ports,
    skills,
  );
  assert.equal(result.stop_reason, "accepted");
  assert.equal(result.model_requests_used, 1);
});

test("a malformed platform count fails closed", TEST_OPTIONS, async () => {
  const ports: SupervisorPorts = {
    ...passingPorts(),
    modelGateway: async () => ({ model_requests: -1, receipts: [], source: "platform-receipt" }),
  };
  const counters = { calls: 0 };
  const result = await runSupervisor(supervisorInput(), ports, countingSkills(counters));
  assert.equal(result.stop_reason, "model_usage_mismatch");
  assert.equal(counters.calls, 0);
});

test("a lower platform count is authoritative when phases are deterministic", TEST_OPTIONS, async () => {
  // Only the plan phase is a real gateway call; interpret/synthesize/review are
  // deterministic helpers without a receipt.  The Supervisor must follow the
  // platform count, not its local reservation counter.
  const counter = { calls: 0 };
  const ports: SupervisorPorts = {
    ...passingPorts(),
    modelGateway: async () => ({
      model_requests: counter.calls > 0 ? 1 : 0,
      receipts: [],
      source: "platform-receipt",
    }),
  };
  const result = await runSupervisor(supervisorInput(), ports, countingSkills(counter));
  assert.equal(result.stop_reason, "accepted");
  // Five skill calls happened, but only one is a platform receipt.
  assert.equal(result.model_requests_used, 1);
});

test("the post-submit sync reads the platform receipt count, not memory", TEST_OPTIONS, async () => {
  // Every skill call is one platform model request; the fake gateway observes
  // the same count (a real gateway settles each call).
  const counter = { calls: 0 };
  const ports: SupervisorPorts = {
    ...passingPorts(),
    modelGateway: async () => ({
      model_requests: counter.calls,
      receipts: [],
      source: "platform-receipt",
    }),
  };
  const result = await runSupervisor(supervisorInput(), ports, countingSkills(counter));
  assert.equal(result.status, "completed");
  assert.equal(result.stop_reason, "accepted");
  assert.equal(result.model_requests_used, 5);
  assert.equal(counter.calls, 5);
});
