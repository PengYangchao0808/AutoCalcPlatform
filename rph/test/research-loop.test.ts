/**
 * Research Supervisor + four explicit skill contracts.
 *
 * The named matrix is the criterion:
 *
 * 1. interpreter input/output schema;
 * 2. synthesizer input/output schema;
 * 3. reviewer input/output schema;
 * 4. planner input/output schema;
 * 5. happy path runs Understand -> ... -> Evaluate -> Stop(accepted);
 * 6. no-evidence report goes to Stop, never another submit;
 * 7. duplicate terminal event cannot start another round;
 * 8. round cap stops before the next submit;
 * 9. preflight failure blocks submit;
 * 10. admission failure blocks submit;
 * 11. model advice cannot override refuted/inconclusive;
 * 12. Wait never calls a skill/model;
 * 13. budget cap stops before a second submit;
 * 14. an invalid EvidenceReport never becomes a verdict.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EVIDENCE_REPORT_SCHEMA,
  INTERPRETER_INPUT_SCHEMA,
  INTERPRETER_SKILL,
  INTERPRETER_SKILL_VERSION,
  PLANNER_INPUT_SCHEMA,
  PLANNER_SKILL,
  PLANNER_SKILL_VERSION,
  REVIEWER_INPUT_SCHEMA,
  REVIEWER_SKILL,
  REVIEWER_SKILL_VERSION,
  SYNTHESIZER_INPUT_SCHEMA,
  SYNTHESIZER_SKILL,
  SYNTHESIZER_SKILL_VERSION,
  decideFromReport,
  runInterpreter,
  runPlanner,
  runReviewer,
  runSupervisor,
  runSynthesizer,
  validateEvidenceReport,
  validateInterpreterInput,
  validateInterpreterOutput,
  validatePlannerInput,
  validatePlannerOutput,
  validateReviewerInput,
  validateReviewerOutput,
  validateSynthesizerInput,
  validateSynthesizerOutput,
  type EvidenceReport,
  type InterpreterInput,
  type PlanBudget,
  type PlannerInput,
  type ReviewerInput,
  type SupervisorInput,
  type SupervisorPorts,
  type SupervisorResult,
  type SupervisorSkills,
  type SynthesizerInput,
  type TaskRef,
  type WaitEvent,
  type WaitResult,
} from "@rph/dsh-bundle";

import { TEST_OPTIONS } from "./harness.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const PROJECT = "proj-diels-alder";
const RESEARCH = "res-diels-alder";
const TASK: TaskRef = {
  project_id: PROJECT,
  job_id: "20261023_001_Optimize",
  attempt: 1,
  release_id: "release-1",
};

function budget(overrides: Partial<PlanBudget> = {}): PlanBudget {
  return {
    model_requests: 64,
    input_tokens: 100_000,
    output_tokens: 50_000,
    task_refs: 8,
    recipe_steps: 8,
    qc_invocations: 8,
    wall_seconds: 72_000,
    queue_seconds: 7_200,
    ...overrides,
  };
}

function report(overrides: Partial<EvidenceReport> = {}): EvidenceReport {
  return {
    schema: EVIDENCE_REPORT_SCHEMA,
    report_id: "report-r0",
    project_id: PROJECT,
    research_id: RESEARCH,
    round: 0,
    task_ref: TASK,
    status: "complete",
    decision: "accept",
    claims: [
      {
        claim_id: "claim-endo-lower",
        statement: "The endo product is lower in energy.",
        status: "supported",
        evidence_refs: ["ev-1"],
        reason: null,
      },
    ],
    evidence_refs: ["ev-1"],
    terminal_event_key: "job.completed:0",
    ...overrides,
  };
}

function supervisorInput(
  overrides: Partial<SupervisorInput> = {},
): SupervisorInput {
  return {
    project_id: PROJECT,
    research_id: RESEARCH,
    question: "Which Diels-Alder product is preferred?",
    question_ref: "question-diels-alder",
    constraints: ["no_ts_seed"],
    available_inputs: ["diene", "dienophile"],
    recipe_digest: DIGEST,
    initial_input: {
      input_id: "reactant-complex",
      sha256: DIGEST,
      format: "xyz",
      role: "reactant_complex",
    },
    limits: { max_rounds: 3, budget: budget() },
    plan_definition_digest: () => DIGEST,
    ...overrides,
  };
}

interface PortLog {
  submitters: string[];
  persisted: TaskRef[];
  preflights: number;
  admissions: number;
  approvals: number;
  waits: number;
  duringWaitSkillCalls: number;
}

function ports(
  options: {
    readonly reports?: readonly (EvidenceReport | null)[];
    readonly waitEvents?: readonly WaitEvent[][];
    readonly preflightOk?: boolean;
    readonly admissionOk?: boolean;
    readonly approvalOk?: boolean;
    readonly skillsState?: { calls: number };
  } = {},
): { readonly ports: SupervisorPorts; readonly log: PortLog } {
  const log: PortLog = {
    submitters: [],
    persisted: [],
    preflights: 0,
    admissions: 0,
    approvals: 0,
    waits: 0,
    duringWaitSkillCalls: 0,
  };
  const reports = options.reports ?? [report()];
  const waitEvents = options.waitEvents;
  return {
    log,
    ports: {
      preflight: async () => {
        log.preflights += 1;
        return {
          ok: options.preflightOk !== false,
          reason: options.preflightOk === false ? "scientific_preflight_failed" : "ok",
          checks: ["method_defined", "inputs_complete"],
        };
      },
      admission: async () => {
        log.admissions += 1;
        return {
          ok: options.admissionOk !== false,
          reason: options.admissionOk === false ? "budget_or_approval_denied" : "ok",
          approval_ref: options.admissionOk === false ? null : "approval-1",
        };
      },
      approve: async () => {
        log.approvals += 1;
        return {
          approved: options.approvalOk !== false,
          approval_ref: options.approvalOk === false ? null : "approval-1",
          reason: options.approvalOk === false ? "approval_rejected" : "ok",
        };
      },
      submit: async (request) => {
        log.submitters.push(`round-${request.round}`);
        return {
          ok: true,
          task_ref: { ...TASK, attempt: request.round + 1 },
          reason: "submitted",
        };
      },
      wait: async (): Promise<WaitResult> => {
        const index = log.waits;
        log.waits += 1;
        const before = options.skillsState?.calls ?? 0;
        const provided = waitEvents?.[index];
        const selected = provided ?? [
          {
            event_key: `job.completed:${index}`,
            type: "job.completed",
            task_ref: { ...TASK, attempt: index + 1 },
            report: reports[Math.min(index, reports.length - 1)] ?? null,
          },
        ];
        // No skill may run between entering and leaving the wait port.
        log.duringWaitSkillCalls += (options.skillsState?.calls ?? 0) - before;
        return { events: selected, cursor: `cursor-${index}` };
      },
      persistTaskRef: async (ref) => {
        log.persisted.push(ref);
      },
      persistReport: async () => undefined,
    },
  };
}

function countingSkills(): {
  readonly skills: SupervisorSkills;
  readonly state: { calls: number };
} {
  const state = { calls: 0 };
  const skills: SupervisorSkills = {
    interpret: async (input) => {
      state.calls += 1;
      return runInterpreter(input);
    },
    synthesize: async (input) => {
      state.calls += 1;
      return runSynthesizer(input);
    },
    review: async (input) => {
      state.calls += 1;
      return runReviewer(input);
    },
    plan: async (input) => {
      state.calls += 1;
      return runPlanner(input);
    },
  };
  return { skills, state };
}

function phases(result: SupervisorResult): string[] {
  return result.phases.map((item) => item.phase);
}

function assertStop(result: SupervisorResult, reason: string): void {
  assert.equal(result.status, "stopped");
  assert.equal(result.stop_reason, reason);
  assert.equal(phases(result).at(-1), "stop");
}

// ---------------------------------------------------------------------------
// 1-4: explicit skill schemas
// ---------------------------------------------------------------------------

test("interpreter: versioned input/output schema accepts a valid request", TEST_OPTIONS, () => {
  const input: InterpreterInput = {
    schema: INTERPRETER_INPUT_SCHEMA,
    skill: INTERPRETER_SKILL,
    skill_version: INTERPRETER_SKILL_VERSION,
    project_id: PROJECT,
    question: "Which product?",
    question_ref: null,
    constraints: ["no_ts_seed"],
    available_inputs: ["diene"],
  };
  assert.deepEqual(validateInterpreterInput(input), []);
  const output = runInterpreter(input);
  assert.deepEqual(validateInterpreterOutput(output), []);
  assert.equal(output.intent, "research");

  assert.notDeepEqual(
    validateInterpreterInput({ ...input, schema: "acp2.skill.interpreter.input.v2" }),
    [],
  );
  assert.notDeepEqual(validateInterpreterInput({ ...input, extra: true }), []);
});

test("synthesizer: advice is schema-checked and its evidence must be a real report", TEST_OPTIONS, () => {
  const input: SynthesizerInput = {
    schema: SYNTHESIZER_INPUT_SCHEMA,
    skill: SYNTHESIZER_SKILL,
    skill_version: SYNTHESIZER_SKILL_VERSION,
    project_id: PROJECT,
    round: 0,
    interpretation: {
      normalized_question: "Which product?",
      scope: PROJECT,
      assumptions: ["no_ts_seed"],
    },
    evidence: null,
    prior_hypotheses: [],
  };
  assert.deepEqual(validateSynthesizerInput(input), []);
  const output = runSynthesizer(input);
  assert.deepEqual(validateSynthesizerOutput(output), []);
  assert.equal(output.advice, "continue");

  assert.notDeepEqual(
    validateSynthesizerOutput({ ...output, advice: "trust_model" }),
    [],
  );
  assert.notDeepEqual(
    validateSynthesizerInput({
      ...input,
      evidence: { schema: "acp2.chat.summary.v1" },
    }),
    [],
  );
});

test("reviewer: severity enum and verdict are explicit", TEST_OPTIONS, () => {
  const input: ReviewerInput = {
    schema: REVIEWER_INPUT_SCHEMA,
    skill: REVIEWER_SKILL,
    skill_version: REVIEWER_SKILL_VERSION,
    project_id: PROJECT,
    round: 0,
    hypothesis: "endo is lower",
    plan_definition_digest: DIGEST,
    evidence: null,
  };
  assert.deepEqual(validateReviewerInput(input), []);
  const output = runReviewer(input);
  assert.deepEqual(validateReviewerOutput(output), []);
  assert.equal(output.verdict, "approve");

  assert.notDeepEqual(
    validateReviewerOutput({ ...output, verdict: "looks_right" }),
    [],
  );
  assert.notDeepEqual(
    validateReviewerInput({ ...input, plan_definition_digest: "not-a-digest" }),
    [],
  );
});

test("planner: output is a complete plan with a digest, science and budget", TEST_OPTIONS, () => {
  const input: PlannerInput = {
    schema: PLANNER_INPUT_SCHEMA,
    skill: PLANNER_SKILL,
    skill_version: PLANNER_SKILL_VERSION,
    project_id: PROJECT,
    round: 0,
    hypothesis: "endo is lower",
    normalized_question: "Which product?",
    recipe_digest: DIGEST,
    plan_definition_digest: DIGEST,
    initial_input: {
      input_id: "reactant-complex",
      sha256: DIGEST,
      format: "xyz",
      role: "reactant_complex",
    },
    critique_findings: [],
    evidence: null,
    remaining_budget: budget(),
  };
  assert.deepEqual(validatePlannerInput(input), []);
  const output = runPlanner(input);
  assert.deepEqual(validatePlannerOutput(output), []);
  assert.equal(output.science.method, "r2SCAN-3c");
  assert.equal(output.budget.task_refs, 1);

  assert.notDeepEqual(validatePlannerOutput({ ...output, budget: {} }), []);
  assert.notDeepEqual(
    validatePlannerOutput({ ...output, science: { ...output.science, capability_steps: [] } }),
    [],
  );
});

// ---------------------------------------------------------------------------
// 5-14: state machine
// ---------------------------------------------------------------------------

test("supervisor: happy path runs every phase once and stops on an accepted report", TEST_OPTIONS, async () => {
  const { ports: supervisorPorts, log } = ports();
  const result = await runSupervisor(supervisorInput(), supervisorPorts);
  assert.equal(result.status, "completed");
  assert.equal(result.stop_reason, "accepted");
  assert.equal(result.submits, 1);
  assert.deepEqual(phases(result), [
    "understand",
    "hypothesize",
    "critique",
    "plan",
    "approve",
    "wait",
    "evaluate",
    "stop",
  ]);
  assert.equal(log.preflights, 1);
  assert.equal(log.admissions, 1);
  assert.equal(log.approvals, 1);
  assert.equal(log.persisted.length, 1);
  assert.equal(result.reports.length, 1);
  assert.equal(result.last_report?.report_id, "report-r0");
});

test("supervisor: no-evidence report goes to Stop and never submits again", TEST_OPTIONS, async () => {
  const empty = report({
    status: "missing",
    decision: "revise",
    claims: [],
    evidence_refs: [],
  });
  const { ports: supervisorPorts, log } = ports({ reports: [empty] });
  const result = await runSupervisor(supervisorInput(), supervisorPorts);
  assertStop(result, "no_evidence");
  assert.equal(result.submits, 1);
  assert.equal(log.submitters.length, 1);
  assert.deepEqual(decideFromReport(empty), {
    action: "stop",
    reason: "no_evidence",
  });
});

test("supervisor: a duplicate terminal event cannot start another round", TEST_OPTIONS, async () => {
  const revise = report({
    report_id: "report-r0",
    decision: "revise",
  });
  const event: WaitEvent = {
    event_key: "job.completed:same",
    type: "job.completed",
    task_ref: TASK,
    report: revise,
  };
  // Round 0 gets the terminal event once (revise); round 1 receives the same
  // event key again. The replay may not authorize round 2.
  const { ports: supervisorPorts, log } = ports({
    waitEvents: [[event], [event]],
    reports: [revise],
  });
  const result = await runSupervisor(
    supervisorInput({ limits: { max_rounds: 4, budget: budget() } }),
    supervisorPorts,
  );
  assertStop(result, "duplicate_terminal");
  assert.equal(result.submits, 2);
  assert.equal(log.submitters.length, 2);
  assert.equal(result.duplicate_terminal_events, 1);
});

test("supervisor: round cap stops before the next submit", TEST_OPTIONS, async () => {
  const revise = (index: number) =>
    report({
      report_id: `report-r${index}`,
      round: index,
      decision: "revise",
    });
  const events = [0, 1, 2].map((index) => [
    {
      event_key: `job.completed:${index}`,
      type: "job.completed",
      task_ref: { ...TASK, attempt: index + 1 },
      report: revise(index),
    },
  ]);
  const { ports: supervisorPorts, log } = ports({ waitEvents: events });
  const result = await runSupervisor(
    supervisorInput({ limits: { max_rounds: 2, budget: budget() } }),
    supervisorPorts,
  );
  assertStop(result, "round_cap");
  assert.equal(result.submits, 2);
  assert.equal(log.submitters.length, 2);
  assert.equal(result.rounds_completed, 2);
});

test("supervisor: preflight failure blocks submit", TEST_OPTIONS, async () => {
  const { ports: supervisorPorts, log } = ports({ preflightOk: false });
  const result = await runSupervisor(supervisorInput(), supervisorPorts);
  assertStop(result, "preflight_failed");
  assert.equal(result.submits, 0);
  assert.equal(log.submitters.length, 0);
  assert.equal(log.admissions, 0);
  assert.equal(log.approvals, 0);
});

test("supervisor: admission failure blocks submit", TEST_OPTIONS, async () => {
  const { ports: supervisorPorts, log } = ports({ admissionOk: false });
  const result = await runSupervisor(supervisorInput(), supervisorPorts);
  assertStop(result, "admission_denied");
  assert.equal(result.submits, 0);
  assert.equal(log.submitters.length, 0);
  assert.equal(log.approvals, 0);
});

test("supervisor: model advice cannot override a refuted report", TEST_OPTIONS, async () => {
  const refuted = report({
    decision: "accept",
    claims: [
      {
        claim_id: "claim-endo-lower",
        statement: "The endo product is lower in energy.",
        status: "refuted",
        evidence_refs: ["ev-1"],
        reason: "the exo product is lower",
      },
    ],
  });
  const { skills } = countingSkills();
  const continueAdvice: SupervisorSkills = {
    ...skills,
    synthesize: async () => ({
      schema: "acp2.skill.synthesizer.output.v1",
      skill: "synthesizer",
      skill_version: "1.0.0",
      hypothesis: "keep trying",
      rationale: "the model wants another expensive round",
      advice: "continue",
      expected_claims: [],
      evidence_refs: [],
    }),
  };
  const { ports: supervisorPorts } = ports({ reports: [refuted] });
  const result = await runSupervisor(
    supervisorInput(),
    supervisorPorts,
    continueAdvice,
  );
  assertStop(result, "refuted");
  assert.equal(result.submits, 1);
  assert.equal(result.last_advice, "continue");
});

test("supervisor: model advice cannot override an inconclusive report", TEST_OPTIONS, async () => {
  const inconclusive = report({
    decision: "revise",
    claims: [
      {
        claim_id: "claim-endo-lower",
        statement: "The endo product is lower in energy.",
        status: "inconclusive",
        evidence_refs: ["ev-1"],
        reason: "thermochemistry missing",
      },
    ],
  });
  const { ports: supervisorPorts, log } = ports({ reports: [inconclusive] });
  const result = await runSupervisor(supervisorInput(), supervisorPorts);
  assertStop(result, "inconclusive");
  assert.equal(result.submits, 1);
  assert.equal(log.submitters.length, 1);
});

test("supervisor: Wait does not call any skill/model", TEST_OPTIONS, async () => {
  const { skills, state } = countingSkills();
  const { ports: supervisorPorts, log } = ports({ skillsState: state });
  const result = await runSupervisor(supervisorInput(), supervisorPorts, skills);
  assert.equal(result.stop_reason, "accepted");
  assert.equal(log.waits, 1);
  assert.equal(log.duringWaitSkillCalls, 0);
  // understand + hypothesize + critique + plan + evaluate; wait contributes 0.
  assert.equal(state.calls, 5);
  assert.equal(result.model_invocations, 5);
  assert.equal(result.wait_invocations, 1);
});

test("supervisor: budget cap stops before a second submit", TEST_OPTIONS, async () => {
  const revise = report({ decision: "revise" });
  const { ports: supervisorPorts, log } = ports({ reports: [revise] });
  const result = await runSupervisor(
    supervisorInput({
      limits: { max_rounds: 4, budget: budget({ task_refs: 1 }) },
    }),
    supervisorPorts,
  );
  assertStop(result, "budget_cap");
  assert.equal(result.submits, 1);
  assert.equal(log.submitters.length, 1);
});

test("supervisor: an invalid report never becomes a verdict", TEST_OPTIONS, async () => {
  const invalid = {
    ...report(),
    claims: [{ claim_id: "claim-1", statement: "x", status: "trust_me", evidence_refs: [] }],
  } as unknown as EvidenceReport;
  assert.notDeepEqual(validateEvidenceReport(invalid), []);
  const { ports: supervisorPorts } = ports({ reports: [invalid] });
  const result = await runSupervisor(supervisorInput(), supervisorPorts);
  assertStop(result, "invalid_report");
  assert.equal(result.submits, 1);
});
