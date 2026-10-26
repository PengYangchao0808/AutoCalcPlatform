/**
 * The single Research Supervisor.
 *
 * Round shape (one supervisor instance, one state machine):
 *
 *   Understand -> Hypothesize -> Critique -> Plan -> Approve -> Wait
 *              -> Evaluate -> Revise/Stop
 *
 * Invariants this module owns:
 *
 * 1. No submit without a scientific preflight AND platform admission. An
 *    approval is also required; the approval reference is never inferred.
 * 2. After submit the TaskRef is persisted and the Supervisor WAITs through
 *    the injected events/bounded-query port. No skill/model call is made while
 *    waiting, so a model is never busy-waiting on QC.
 * 3. The next step is decided by the real `EvidenceReport`. Skill advice is
 *    recorded but never overrides a refuted/inconclusive report or an accept.
 * 4. A no-evidence report, the round cap, the budget cap, the duplicate
 *    terminal event and a preflight/admission/approval failure all Stop. A
 *    duplicate terminal event can never start another round.
 *
 * The Supervisor has no transport, no process and no persistence backdoor:
 * every effect goes through the injected ports.
 */

import type {
  EvidenceReport,
  PlanBudget,
  PlanScience,
  ScienceSeed,
  TaskRef,
} from "./models.js";
import {
  blockingClaimStatus,
  reportHasEvidence,
  validateEvidenceReport,
} from "./models.js";
import type { InterpreterInput, InterpreterOutput } from "./interpreter.js";
import {
  INTERPRETER_INPUT_SCHEMA,
  INTERPRETER_SKILL,
  INTERPRETER_SKILL_VERSION,
  runInterpreter,
} from "./interpreter.js";
import type { PlannerInput, PlannerOutput } from "./planner.js";
import {
  PLANNER_INPUT_SCHEMA,
  PLANNER_SKILL,
  PLANNER_SKILL_VERSION,
  runPlanner,
} from "./planner.js";
import type {
  ReviewerFinding,
  ReviewerInput,
  ReviewerOutput,
} from "./reviewer.js";
import { runReviewer } from "./reviewer.js";
import type { SynthesizerInput, SynthesizerOutput } from "./synthesizer.js";
import { runSynthesizer } from "./synthesizer.js";

export const SUPERVISOR_VERSION = "1.0.0";

export type SupervisorPhase =
  | "understand"
  | "hypothesize"
  | "critique"
  | "plan"
  | "approve"
  | "wait"
  | "evaluate"
  | "revise"
  | "stop";

export type SupervisorStopReason =
  | "accepted"
  | "intent_reject"
  | "intent_clarify"
  | "no_hypothesis"
  | "critique_stop"
  | "preflight_failed"
  | "admission_denied"
  | "approval_rejected"
  | "submit_failed"
  | "persist_failed"
  | "wait_timeout"
  | "duplicate_terminal"
  | "no_evidence"
  | "invalid_report"
  | "refuted"
  | "inconclusive"
  | "report_stop"
  | "round_cap"
  | "budget_cap";

export interface PhaseRecord {
  readonly phase: SupervisorPhase;
  readonly round: number;
  readonly detail: string;
}

export interface SupervisorLimits {
  readonly max_rounds: number;
  readonly budget: PlanBudget;
}

export interface SupervisorInput {
  readonly project_id: string;
  readonly research_id: string;
  readonly question: string;
  readonly question_ref: string | null;
  readonly constraints: readonly string[];
  readonly available_inputs: readonly string[];
  readonly recipe_digest: string;
  readonly initial_input: PlannerInput["initial_input"];
  /** Typed scientific values (M1-04a); a missing critical value clarifies. */
  readonly science_seed: ScienceSeed;
  readonly limits: SupervisorLimits;
  /** Approval-bound digest for a round; a constant is valid for a fixture. */
  readonly plan_definition_digest?: (round: number) => string;
  readonly prior_evidence?: EvidenceReport | null;
}

/** Four skills, each with its own explicit input/output contract. */
export interface SupervisorSkills {
  interpret(input: InterpreterInput): Promise<InterpreterOutput>;
  synthesize(input: SynthesizerInput): Promise<SynthesizerOutput>;
  review(input: ReviewerInput): Promise<ReviewerOutput>;
  plan(input: PlannerInput): Promise<PlannerOutput>;
}

export const DEFAULT_SUPERVISOR_SKILLS: SupervisorSkills = Object.freeze({
  interpret: async (input: InterpreterInput) => runInterpreter(input),
  synthesize: async (input: SynthesizerInput) => runSynthesizer(input),
  review: async (input: ReviewerInput) => runReviewer(input),
  plan: async (input: PlannerInput) => runPlanner(input),
});

export interface PreflightRequest {
  readonly project_id: string;
  readonly round: number;
  readonly plan: PlannerOutput;
}

export interface PreflightResult {
  readonly ok: boolean;
  readonly reason: string;
  readonly checks: readonly string[];
}

export interface AdmissionRequest {
  readonly project_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
  readonly budget: PlanBudget;
}

export interface AdmissionResult {
  readonly ok: boolean;
  readonly reason: string;
  readonly approval_ref: string | null;
}

export interface ApprovalRequest {
  readonly project_id: string;
  readonly round: number;
  readonly approval_ref: string | null;
  readonly plan_definition_digest: string;
  readonly budget: PlanBudget;
}

export interface ApprovalResult {
  readonly approved: boolean;
  readonly approval_ref: string | null;
  readonly reason: string;
}

export interface SubmitRequest {
  readonly project_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
  readonly approval_ref: string;
  readonly plan: PlannerOutput;
}

export interface SubmitResult {
  readonly ok: boolean;
  readonly task_ref: TaskRef | null;
  readonly reason: string;
}

export interface WaitEvent {
  readonly event_key: string;
  readonly type: string;
  readonly task_ref: TaskRef;
  readonly report: EvidenceReport | null;
}

export interface WaitRequest {
  readonly project_id: string;
  readonly round: number;
  readonly task_ref: TaskRef;
  readonly cursor: string | null;
  readonly max_events: number;
  readonly max_wait_ms: number;
}

export interface WaitResult {
  readonly events: readonly WaitEvent[];
  readonly cursor: string | null;
}

export interface SupervisorPorts {
  preflight(request: PreflightRequest): Promise<PreflightResult>;
  admission(request: AdmissionRequest): Promise<AdmissionResult>;
  approve(request: ApprovalRequest): Promise<ApprovalResult>;
  submit(request: SubmitRequest): Promise<SubmitResult>;
  wait(request: WaitRequest): Promise<WaitResult>;
  persistTaskRef?(ref: TaskRef): Promise<void>;
  persistReport?(report: EvidenceReport): Promise<void>;
}

export interface SupervisorResult {
  readonly status: "completed" | "stopped";
  readonly stop_reason: SupervisorStopReason;
  readonly rounds_completed: number;
  readonly submits: number;
  readonly task_refs: readonly TaskRef[];
  readonly reports: readonly EvidenceReport[];
  readonly phases: readonly PhaseRecord[];
  readonly model_invocations: number;
  readonly wait_invocations: number;
  readonly duplicate_terminal_events: number;
  readonly last_report: EvidenceReport | null;
  readonly last_advice: SynthesizerOutput["advice"] | null;
}

const BUDGET_FIELDS = [
  "model_requests",
  "input_tokens",
  "output_tokens",
  "task_refs",
  "recipe_steps",
  "qc_invocations",
  "wall_seconds",
  "queue_seconds",
] as const;

const TERMINAL_EVENT_TYPES = new Set([
  "job.completed",
  "job.failed",
  "job.cancelled",
  "task.completed",
  "task.failed",
  "evidence.ready",
]);

type MutableBudget = { -readonly [K in keyof PlanBudget]: number };

function zeroBudget(): MutableBudget {
  return {
    model_requests: 0,
    input_tokens: 0,
    output_tokens: 0,
    task_refs: 0,
    recipe_steps: 0,
    qc_invocations: 0,
    wall_seconds: 0,
    queue_seconds: 0,
  };
}

function remainingBudget(limit: PlanBudget, used: PlanBudget): PlanBudget {
  const out: Record<string, number> = {};
  for (const field of BUDGET_FIELDS) {
    out[field] = Math.max(0, limit[field] - used[field]);
  }
  return out as unknown as PlanBudget;
}

function budgetExhausted(remaining: PlanBudget): boolean {
  return remaining.model_requests <= 0;
}

function submitBudgetExceeded(remaining: PlanBudget, plan: PlannerOutput): boolean {
  return (
    remaining.task_refs < plan.budget.task_refs ||
    remaining.qc_invocations < plan.budget.qc_invocations ||
    remaining.recipe_steps < plan.budget.recipe_steps ||
    remaining.wall_seconds < plan.budget.wall_seconds ||
    remaining.queue_seconds < plan.budget.queue_seconds ||
    remaining.input_tokens < plan.budget.input_tokens ||
    remaining.output_tokens < plan.budget.output_tokens
  );
}

function roundDigest(round: number): string {
  return `sha256:${round.toString(16).padStart(64, "0").slice(-64)}`;
}

function addPhase(
  phases: PhaseRecord[],
  phase: SupervisorPhase,
  round: number,
  detail: string,
): void {
  phases.push(Object.freeze({ phase, round, detail }));
}

function stopResult(
  reason: SupervisorStopReason,
  rounds: number,
  state: {
    submits: number;
    taskRefs: TaskRef[];
    reports: EvidenceReport[];
    phases: PhaseRecord[];
    modelInvocations: number;
    waitInvocations: number;
    duplicateTerminals: number;
    lastReport: EvidenceReport | null;
    lastAdvice: SynthesizerOutput["advice"] | null;
  },
): SupervisorResult {
  addPhase(state.phases, "stop", rounds, reason);
  return Object.freeze({
    status: reason === "accepted" ? "completed" : "stopped",
    stop_reason: reason,
    rounds_completed: rounds,
    submits: state.submits,
    task_refs: Object.freeze([...state.taskRefs]),
    reports: Object.freeze([...state.reports]),
    phases: Object.freeze([...state.phases]),
    model_invocations: state.modelInvocations,
    wait_invocations: state.waitInvocations,
    duplicate_terminal_events: state.duplicateTerminals,
    last_report: state.lastReport,
    last_advice: state.lastAdvice,
  });
}

function validateReportOrNull(
  report: unknown,
): { readonly report: EvidenceReport | null; readonly reason: string | null } {
  if (report === null || report === undefined) {
    return { report: null, reason: null };
  }
  const issues = validateEvidenceReport(report);
  if (issues.length > 0) {
    return {
      report: null,
      reason: issues.map((item) => `${item.path}:${item.code}`).join(","),
    };
  }
  return { report: report as EvidenceReport, reason: null };
}

/**
 * Decide the next step from the real EvidenceReport.
 *
 * `advice` is deliberately unused for the verdict: it may explain why a retry
 * is attractive, but it can never turn refuted/inconclusive into a PASS and
 * can never override an `accept`.
 */
export function decideFromReport(
  report: EvidenceReport,
): { readonly action: "accept" | "revise" | "stop"; readonly reason: SupervisorStopReason } {
  if (!reportHasEvidence(report)) {
    return { action: "stop", reason: "no_evidence" };
  }
  const blocking = blockingClaimStatus(report);
  if (blocking !== null) {
    return { action: "stop", reason: blocking };
  }
  if (report.decision === "accept") {
    return { action: "accept", reason: "accepted" };
  }
  if (report.decision === "revise") {
    return { action: "revise", reason: "report_stop" };
  }
  return { action: "stop", reason: "report_stop" };
}

/** Run the single supervisor state machine to a terminal decision. */
export async function runSupervisor(
  input: SupervisorInput,
  ports: SupervisorPorts,
  skills: SupervisorSkills = DEFAULT_SUPERVISOR_SKILLS,
): Promise<SupervisorResult> {
  const phases: PhaseRecord[] = [];
  const used: MutableBudget = zeroBudget();
  const taskRefs: TaskRef[] = [];
  const reports: EvidenceReport[] = [];
  const seenEvents = new Set<string>();
  let submits = 0;
  let modelInvocations = 0;
  let waitInvocations = 0;
  let duplicateTerminals = 0;
  let lastReport: EvidenceReport | null = input.prior_evidence ?? null;
  let lastAdvice: SynthesizerOutput["advice"] | null = null;
  let round = 0;

  const state = () => ({
    submits,
    taskRefs,
    reports,
    phases,
    modelInvocations,
    waitInvocations,
    duplicateTerminals,
    lastReport,
    lastAdvice,
  });

  const remaining = (): PlanBudget =>
    remainingBudget(input.limits.budget, used);

  const callModel = async <T>(
    phase: SupervisorPhase,
    roundForCall: number,
    detail: string,
    body: () => Promise<T>,
  ): Promise<T | null> => {
    if (budgetExhausted(remaining())) {
      return null;
    }
    addPhase(phases, phase, roundForCall, detail);
    modelInvocations += 1;
    used.model_requests += 1;
    return body();
  };

  // Understand.
  const interpretation = await callModel(
    "understand",
    round,
    "normalize the user question",
    async () =>
      skills.interpret({
        schema: INTERPRETER_INPUT_SCHEMA,
        skill: INTERPRETER_SKILL,
        skill_version: INTERPRETER_SKILL_VERSION,
        project_id: input.project_id,
        question: input.question,
        question_ref: input.question_ref,
        constraints: input.constraints,
        available_inputs: input.available_inputs,
        science_seed: input.science_seed,
      }),
  );
  if (interpretation === null) {
    return stopResult("budget_cap", round, state());
  }
  if (interpretation.intent === "reject") {
    return stopResult("intent_reject", round, state());
  }
  if (interpretation.intent === "clarify") {
    return stopResult("intent_clarify", round, state());
  }

  while (true) {
    if (round >= input.limits.max_rounds) {
      return stopResult("round_cap", round, state());
    }
    if (budgetExhausted(remaining())) {
      return stopResult("budget_cap", round, state());
    }
    if (
      remaining().task_refs <= 0 ||
      remaining().qc_invocations <= 0 ||
      remaining().recipe_steps <= 0
    ) {
      return stopResult("budget_cap", round, state());
    }

    // Hypothesize.
    const hypothesis = await callModel(
      "hypothesize",
      round,
      `hypothesis for round ${round}`,
      async () =>
        skills.synthesize({
          schema: "acp2.skill.synthesizer.input.v1",
          skill: "synthesizer",
          skill_version: "1.0.0",
          project_id: input.project_id,
          round,
          interpretation: {
            normalized_question: interpretation.normalized_question,
            scope: interpretation.scope,
            assumptions: interpretation.assumptions,
          },
          evidence: lastReport,
          prior_hypotheses: reports.map((report) => report.report_id),
        }),
    );
    if (hypothesis === null) {
      return stopResult("budget_cap", round, state());
    }
    if (hypothesis.hypothesis === null) {
      return stopResult("no_hypothesis", round, state());
    }

    // Critique.
    const digest = (input.plan_definition_digest ?? roundDigest)(round);
    const critique = await callModel(
      "critique",
      round,
      "critique the hypothesis and prior evidence",
      async () =>
        skills.review({
          schema: "acp2.skill.reviewer.input.v1",
          skill: "reviewer",
          skill_version: "1.0.0",
          project_id: input.project_id,
          round,
          hypothesis: hypothesis.hypothesis ?? "",
          plan_definition_digest: digest,
          evidence: lastReport,
        }),
    );
    if (critique === null) {
      return stopResult("budget_cap", round, state());
    }
    if (critique.verdict === "stop") {
      return stopResult("critique_stop", round, state());
    }

    // Plan.
    const plan = await callModel(
      "plan",
      round,
      "materialize the plan draft",
      async () =>
        skills.plan({
          schema: PLANNER_INPUT_SCHEMA,
          skill: PLANNER_SKILL,
          skill_version: PLANNER_SKILL_VERSION,
          project_id: input.project_id,
          round,
          hypothesis: hypothesis.hypothesis ?? "",
          normalized_question: interpretation.normalized_question,
          recipe_digest: input.recipe_digest,
          plan_definition_digest: digest,
          initial_input: input.initial_input,
          science_seed: input.science_seed,
          critique_findings: critiqueFindings(critique),
          evidence: lastReport,
          remaining_budget: remaining(),
        }),
    );
    if (plan === null) {
      return stopResult("budget_cap", round, state());
    }
    if (submitBudgetExceeded(remaining(), plan)) {
      return stopResult("budget_cap", round, state());
    }

    // Approve (preflight + platform admission + approval; all BEFORE submit).
    addPhase(phases, "approve", round, "scientific preflight and platform admission");
    const preflight = await ports.preflight({
      project_id: input.project_id,
      round,
      plan,
    });
    if (!preflight.ok) {
      return stopResult("preflight_failed", round, state());
    }
    const admission = await ports.admission({
      project_id: input.project_id,
      round,
      plan_definition_digest: plan.plan_definition_digest,
      budget: plan.budget,
    });
    if (!admission.ok) {
      return stopResult("admission_denied", round, state());
    }
    const approval = await ports.approve({
      project_id: input.project_id,
      round,
      approval_ref: admission.approval_ref,
      plan_definition_digest: plan.plan_definition_digest,
      budget: plan.budget,
    });
    if (!approval.approved || approval.approval_ref === null) {
      return stopResult("approval_rejected", round, state());
    }

    const submitted = await ports.submit({
      project_id: input.project_id,
      round,
      plan_definition_digest: plan.plan_definition_digest,
      approval_ref: approval.approval_ref,
      plan,
    });
    if (!submitted.ok || submitted.task_ref === null) {
      return stopResult("submit_failed", round, state());
    }
    submits += 1;
    used.model_requests = used.model_requests;
    used.task_refs += plan.budget.task_refs;
    used.recipe_steps += plan.budget.recipe_steps;
    used.qc_invocations += plan.budget.qc_invocations;
    used.wall_seconds += plan.budget.wall_seconds;
    used.queue_seconds += plan.budget.queue_seconds;
    used.input_tokens += plan.budget.input_tokens;
    used.output_tokens += plan.budget.output_tokens;
    taskRefs.push(submitted.task_ref);
    if (ports.persistTaskRef !== undefined) {
      try {
        await ports.persistTaskRef(submitted.task_ref);
      } catch {
        return stopResult("persist_failed", round, state());
      }
    }

    // Wait: NO skill/model invocation inside this phase.
    addPhase(phases, "wait", round, "bounded platform events; no model call");
    waitInvocations += 1;
    const waited = await ports.wait({
      project_id: input.project_id,
      round,
      task_ref: submitted.task_ref,
      cursor: null,
      max_events: 200,
      max_wait_ms: 60_000,
    });

    const fresh: WaitEvent[] = [];
    const batchSeen = new Set<string>();
    let duplicateTerminalSeen = false;
    for (const event of waited.events) {
      const isTerminal = TERMINAL_EVENT_TYPES.has(event.type);
      if (seenEvents.has(event.event_key) || batchSeen.has(event.event_key)) {
        duplicateTerminals += 1;
        if (isTerminal) {
          duplicateTerminalSeen = true;
        }
        continue;
      }
      batchSeen.add(event.event_key);
      fresh.push(event);
    }
    if (waited.events.length > 0 && fresh.length === 0) {
      return stopResult("duplicate_terminal", round, state());
    }
    const terminalEvents = fresh.filter((event) => TERMINAL_EVENT_TYPES.has(event.type));
    if (terminalEvents.length === 0) {
      return stopResult(
        duplicateTerminalSeen ? "duplicate_terminal" : "wait_timeout",
        round,
        state(),
      );
    }
    for (const event of fresh) {
      seenEvents.add(event.event_key);
    }

    // Evaluate: the real report decides; the model only advises.
    const rawReport = terminalEvents.find((event) => event.report !== null)?.report ?? null;
    const validated = validateReportOrNull(rawReport);
    if (validated.reason !== null) {
      return stopResult("invalid_report", round, state());
    }
    const report = validated.report;
    if (report === null) {
      return stopResult("no_evidence", round, state());
    }
    if (ports.persistReport !== undefined) {
      try {
        await ports.persistReport(report);
      } catch {
        return stopResult("persist_failed", round, state());
      }
    }
    reports.push(report);
    lastReport = report;

    const advice = await callModel(
      "evaluate",
      round,
      "advise on the real EvidenceReport (advice only)",
      async () =>
        skills.synthesize({
          schema: "acp2.skill.synthesizer.input.v1",
          skill: "synthesizer",
          skill_version: "1.0.0",
          project_id: input.project_id,
          round,
          interpretation: {
            normalized_question: interpretation.normalized_question,
            scope: interpretation.scope,
            assumptions: interpretation.assumptions,
          },
          evidence: report,
          prior_hypotheses: reports.map((item) => item.report_id),
        }),
    );
    if (advice === null) {
      return stopResult("budget_cap", round, state());
    }
    lastAdvice = advice.advice;

    const decision = decideFromReport(report);
    if (decision.action === "accept") {
      return stopResult("accepted", round + 1, state());
    }
    if (decision.action === "stop") {
      return stopResult(decision.reason, round, state());
    }

    // Revise: only a report decision of `revise` reaches this point.
    addPhase(phases, "revise", round, "report requested another round");
    round += 1;
  }
}

function critiqueFindings(critique: ReviewerOutput): readonly string[] {
  return critique.findings.map(
    (finding: ReviewerFinding) => `${finding.code}:${finding.severity}`,
  );
}

/** Type-only convenience re-export for consumers that build plans. */
export type { PlanScience };
