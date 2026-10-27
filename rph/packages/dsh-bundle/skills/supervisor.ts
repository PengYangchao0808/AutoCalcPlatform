/**
 * The single Research Supervisor.
 *
 * Round shape (one supervisor instance, one state machine):
 *
 *   Understand -> Hypothesize -> Critique -> Plan -> Approve -> Submit -> Wait
 *              -> Evaluate -> Revise/Stop
 *
 * Invariants this module owns:
 *
 * 1. No submit without a scientific preflight AND platform admission. An
 *    approval is also required; the approval reference is never inferred.
 * 2. A submit carries a **stable idempotency key** that was persisted (through
 *    the injected M1-02 intent port) *before* the call, and the returned
 *    reference is bound to the research round (M3-04). A retry after a crash
 *    replays the same key, so the platform replays one task.
 * 3. Waiting is a **bounded loop** over the platform P2 wait semantics: each
 *    poll is at most 60 s / 200 events, the cursor is opaque, and an expired
 *    cursor is an explicit snapshot reset (never a silent gap). When the bound
 *    is exhausted without a terminal event the run returns
 *    `status: "waiting"` plus a `resume_handle`; it does **not** invent a second
 *    task state machine and it does not re-plan.
 * 4. The next step is decided by the real `EvidenceReport`/`EvidenceView`.
 *    Skill advice is recorded but never overrides a refuted/inconclusive report
 *    or an accept.
 * 5. In binding mode (`require_evidence_binding` or a present `evidence` port)
 *    the verdict comes only from a digest-bound `EvidenceView` that is
 *    attributable to *this* project/research/round/attempt/release and whose
 *    report digest equals the terminal event's. A foreign event/report, an
 *    ambiguous set of terminal events and a digest mismatch all stop the run and
 *    nothing is persisted as accepted.
 * 6. Claims/limitations/missing checks are read through the single projection;
 *    the `ResearchDecision` is derived independently of any model text, and a
 *    completed-but-inconclusive execution can never become `accept`.
 * 7. Used budget is restored from platform records/receipts (model gateway +
 *    saga budget receipts), never re-estimated from a fresh process memory.
 * 8. An attempt-fenced checkpoint port (`persistCheckpoint`) is the client side
 *    of the platform's `FencedCommitPort`: after a takeover the old attempt's
 *    write is refused and the run stops instead of reporting success.
 *
 * The Supervisor has no transport, no process and no persistence backdoor:
 * every effect goes through the injected ports.
 */

import {
  BridgeContractError,
  attributeEvidenceView,
  attributedTaskRefKey,
  buildDecisionRecord,
  evaluateDecision,
  parsePlatformTaskRef,
  type ClaimStatus,
  type DecisionRecord,
  type EvidenceView,
  type ResearchDecision,
  type ResearchTaskRef,
} from "@rph/bridge";

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
} from "./planner.js";
import { runFixturePlanner } from "./fixture-planner.js";
import type {
  ReviewerFinding,
  ReviewerInput,
  ReviewerOutput,
} from "./reviewer.js";
import { runReviewer } from "./reviewer.js";
import type { SynthesizerInput, SynthesizerOutput } from "./synthesizer.js";
import { runSynthesizer } from "./synthesizer.js";

export const SUPERVISOR_VERSION = "1.1.0";

/** Wire id of the recovery handle a `waiting` run returns. */
export const SUPERVISOR_RECOVERY_SCHEMA = "acp2.experience.supervisor-recovery.v1";

/** Platform P2 wait bound: `max_wait_s <= 60`. */
export const WAIT_MAX_WAIT_MS = 60_000;
/** Platform P2 wait bound: `limit <= 200`. */
export const WAIT_MAX_EVENTS = 200;
/** Platform P2 cursor age (`waits.cursor.max_age_s = 900`). */
export const WAIT_CURSOR_MAX_AGE_MS = 900_000;
/** Bounded polls before a run returns `waiting` (3 × 60 s = 3 min max). */
export const DEFAULT_MAX_WAIT_POLLS = 3;
/** Explicit cursor resets tolerated before `cursor_expired` stops the run. */
export const DEFAULT_MAX_CURSOR_RESETS = 2;

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
  | "submit_intent_required"
  | "persist_failed"
  | "wait_timeout"
  | "wait_pending"
  | "wait_failed"
  | "cursor_expired"
  | "cursor_rejected"
  | "duplicate_terminal"
  | "no_evidence"
  | "invalid_report"
  | "refuted"
  | "inconclusive"
  | "report_stop"
  | "round_cap"
  | "budget_cap"
  | "budget_receipt_mismatch"
  | "fixture_planner_rejected"
  | "model_gateway_unavailable"
  | "model_usage_mismatch"
  // M3-04 evidence ownership (G07/G08/G09)
  | "evidence_binding_required"
  | "evidence_read_failed"
  | "foreign_evidence"
  | "evidence_identity_mismatch"
  | "evidence_digest_mismatch"
  | "ambiguous_terminal"
  | "execution_not_terminal"
  | "decision_denied"
  | "decision_changed"
  | "decision_reject"
  | "decision_defer"
  | "decision_escalate"
  | "plan_changed"
  | "resume_handle_invalid"
  | "fenced";

export interface PhaseRecord {
  readonly phase: SupervisorPhase;
  readonly round: number;
  readonly detail: string;
}

export interface SupervisorLimits {
  readonly max_rounds: number;
  readonly budget: PlanBudget;
  /** Bounded wait polls before `waiting` is returned (default 3). */
  readonly max_wait_polls?: number;
  /** Per-poll wait in ms; clamped to the platform bound of 60 s. */
  readonly wait_poll_ms?: number;
  /** Per-poll event cap; clamped to the platform bound of 200. */
  readonly max_events_per_poll?: number;
  /** Explicit cursor resets tolerated before `cursor_expired` (default 2). */
  readonly max_cursor_resets?: number;
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
  /**
   * Product mode (M3-03): require the platform model gateway and reject the
   * deterministic fixture planner.  A product composition sets this; a fixture
   * run leaves it unset.
   */
  readonly require_model_gateway?: boolean;
  /**
   * Recovery mode (M3-04): the verdict comes only from a digest-bound
   * `EvidenceView`, a submit intent is persisted before the call and a decision
   * is persisted through the CAS port.  Missing ports fail closed.
   */
  readonly require_evidence_binding?: boolean;
  /** Resume a previous `waiting`/`decision_pending` run (never re-plans). */
  readonly resume_handle?: ResumeHandle | null;
}

/** Authoritative model usage read from the platform (receipts, not memory). */
export interface ModelGatewayUsage {
  readonly model_requests: number;
  readonly receipts: readonly string[];
  readonly source: "platform-receipt";
}

/**
 * Authoritative *budget* usage read from the platform (saga record/receipts).
 *
 * Restoring this at the start of a resumed run is what makes "used budget" the
 * platform's fact instead of a fresh process's empty estimate.
 */
export interface BudgetUsageReceipt {
  readonly source: "platform-receipt" | "saga-record";
  readonly used: PlanBudget;
  readonly receipts: readonly string[];
}

type MaybePromise<T> = T | Promise<T>;

/** One persisted submit intent (M1-02 / D-IDENT-07). */
export interface SubmitIntentIdentity {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
}

/** The pre-submit intent key pair; `wire_key` is the opaque platform value. */
export interface SubmitIntentRef {
  readonly key: string;
  readonly wire_key: string;
  readonly created: boolean;
}

/**
 * The pre-submit intent port.
 *
 * `prepare` persists (or replays) the intent *before* the submit call; a
 * changed plan digest for the same round must throw with
 * `code: "plan_digest_changed"` (`SubmitIntentConflict`) rather than deriving a
 * second key.
 */
export interface SubmitIntentPort {
  prepare(identity: SubmitIntentIdentity): MaybePromise<SubmitIntentRef>;
  markSubmitted(
    identity: SubmitIntentIdentity,
  ): MaybePromise<void | boolean | null>;
}

/** Four skills, each with its own explicit input/output contract. */
export interface SupervisorSkills {
  interpret(input: InterpreterInput): Promise<InterpreterOutput>;
  synthesize(input: SynthesizerInput): Promise<SynthesizerOutput>;
  review(input: ReviewerInput): Promise<ReviewerOutput>;
  plan(input: PlannerInput): Promise<PlannerOutput>;
  /** `fixture` is test/demo-only; product composition accepts only `model`. */
  readonly planner_kind?: "fixture" | "model";
  /**
   * Phases that actually spend a platform model request.  A product planner
   * declares `["plan"]`; legacy/fixture skills omit it and keep the historical
   * "every skill call counts" behaviour.
   */
  readonly model_phases?: readonly SupervisorPhase[];
}

/** The three deterministic, non-planning helpers (safe in product too). */
export const DEFAULT_SKILL_HELPERS = Object.freeze({
  interpret: async (input: InterpreterInput) => runInterpreter(input),
  synthesize: async (input: SynthesizerInput) => runSynthesizer(input),
  review: async (input: ReviewerInput) => runReviewer(input),
});

/**
 * Explicit fixture binding (M3-03): offline tests, the thin slice and demos.
 *
 * It is *not* the product default.  `composition.ts::composeProductionSkills`
 * rejects `planner_kind: "fixture"`, and `runSupervisor` refuses it when
 * `require_model_gateway` is set.
 */
export const FIXTURE_SUPERVISOR_SKILLS: SupervisorSkills = Object.freeze({
  ...DEFAULT_SKILL_HELPERS,
  planner_kind: "fixture" as const,
  plan: async (input: PlannerInput) => runFixturePlanner(input),
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
  /** Stable wire key persisted before the call (never re-derived on retry). */
  readonly idempotency_key: string;
  /** The durable M1-02 intent key behind `idempotency_key` (audit only). */
  readonly submit_intent_key: string;
}

export interface SubmitResult {
  readonly ok: boolean;
  readonly task_ref: TaskRef | null;
  /**
   * The digest-bound reference (ADR-003).  Required in binding mode: the
   * platform evidence projection is attributed against it, and a legacy
   * `release_id`-only reference can never stand in for it.
   */
  readonly bound_ref?: ResearchTaskRef | null;
  readonly reason: string;
}

export interface WaitEvent {
  readonly event_key: string;
  readonly type: string;
  readonly task_ref: TaskRef;
  readonly report: EvidenceReport | null;
  /** Report identity declared by the terminal event (binding mode). */
  readonly report_id?: string | null;
  /** Report digest declared by the terminal event (binding mode). */
  readonly report_digest?: string | null;
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
  /** Opaque cursor; the supervisor never parses it. */
  readonly cursor: string | null;
  /** The poll hit its deadline without a change (platform `timed_out`). */
  readonly timed_out?: boolean;
  readonly snapshot_revision?: string | null;
}

/** Rules a platform port may raise; the supervisor maps them, never guesses. */
export type ResearchPortRule =
  | "cursor_expired"
  | "cursor_invalid"
  | "cursor_project_mismatch"
  | "fenced"
  | "stale_revision"
  | "evidence_unavailable";

/** Typed refusal from a research port (no silent coercion into a success). */
export class ResearchPortError extends Error {
  readonly rule: ResearchPortRule;
  readonly params: Readonly<Record<string, unknown>>;

  constructor(
    rule: ResearchPortRule,
    message: string,
    params: Readonly<Record<string, unknown>> = {},
  ) {
    super(message);
    this.name = "ResearchPortError";
    this.rule = rule;
    this.params = Object.freeze({ ...params });
  }

  get fenced(): boolean {
    return this.rule === "fenced";
  }

  toParams(): Readonly<Record<string, unknown>> {
    return Object.freeze({ rule: this.rule, ...this.params });
  }
}

/** One evidence read of the single platform projection (M1-03). */
export interface EvidenceRequest {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly task_ref: ResearchTaskRef;
  /** Terminal event the read is bound to (audit only). */
  readonly event_key: string;
  /** Cursor observed *before* the terminal poll (recovery anchor). */
  readonly cursor: string | null;
}

export interface EvidenceResult {
  readonly view: EvidenceView;
  readonly read_at?: string | null;
}

/**
 * P2-R5 `resume`: advance the platform saga of the same
 * (proposal, plan_digest) without opening a second attempt.
 *
 * The Supervisor never derives the saga identity itself; the port receives the
 * handle's facts and returns the authoritative cursor/state.
 */
export interface ResumeRequest {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
  readonly task_ref: ResearchTaskRef;
  readonly cursor: string | null;
  readonly diagnostic_only: boolean;
}

export interface ResumeResult {
  readonly advanced: boolean;
  readonly cursor: string | null;
  readonly terminal_state: string | null;
}

/** A verified binding: only a verified binding can reach a decision. */
export interface EvidenceBinding {
  readonly event_key: string;
  readonly task_ref: ResearchTaskRef;
  readonly report_id: string;
  readonly report_digest: string;
  readonly projection_digest: string;
  readonly evidence_status: EvidenceView["evidence_status"];
  readonly overall_claim_status: ClaimStatus;
  /** Limitations and missing checks, read only from the projection. */
  readonly limitations: readonly string[];
  readonly blocking_missing_checks: readonly string[];
  readonly verified: true;
}

/** Outcome of a CAS decision write (the research writer's own result). */
export interface DecisionPersistResult {
  readonly ok: boolean;
  readonly replayed?: boolean;
  readonly revision?: number | null;
  readonly reason?: string;
  readonly fenced?: boolean;
}

/** Where an intermediate checkpoint is written. */
export type CheckpointStage =
  | "task_bound"
  | "evidence_bound"
  | "decision_pending"
  | "waiting";

/** One attempt-fenced checkpoint (client side of `FencedCommitPort`). */
export interface SupervisorCheckpoint {
  readonly stage: CheckpointStage;
  readonly round: number;
  readonly handle: ResumeHandle;
}

export interface CheckpointResult {
  readonly ok: boolean;
  readonly replayed?: boolean;
  readonly fenced?: boolean;
  readonly reason?: string;
}

export type ResumeState = "waiting" | "decision_pending";

/**
 * The recovery handle of a run that has not reached a decision.
 *
 * It names the same research round / TaskRef / attempt and the opaque cursor,
 * so a new process resumes the wait instead of creating a task.  It is a
 * *reference*, not a state machine: the platform saga/record remains the
 * authority and a resume goes through the P2 snapshot/resume semantics.
 */
export interface ResumeHandle {
  readonly schema: typeof SUPERVISOR_RECOVERY_SCHEMA;
  readonly state: ResumeState;
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
  readonly approval_ref: string | null;
  readonly submit_intent_key: string;
  readonly task_ref: ResearchTaskRef;
  readonly cursor: string | null;
  readonly wait_polls: number;
  readonly cursor_resets: number;
  readonly resume_count: number;
  readonly consumed_budget: PlanBudget;
  readonly reports: readonly string[];
  readonly pending_decision: ResearchDecision | null;
  readonly pending_projection_digest: string | null;
}

export interface SupervisorPorts {
  preflight(request: PreflightRequest): Promise<PreflightResult>;
  admission(request: AdmissionRequest): Promise<AdmissionResult>;
  approve(request: ApprovalRequest): Promise<ApprovalResult>;
  submit(request: SubmitRequest): Promise<SubmitResult>;
  wait(request: WaitRequest): Promise<WaitResult>;
  /** M1-02 ledger: the intent is persisted before the submit call. */
  submitIntent?: SubmitIntentPort;
  /** The digest-bound evidence projection (M1-03). */
  evidence?(request: EvidenceRequest): Promise<EvidenceResult>;
  /**
   * P2-R5 resume: advance the same saga (idempotent, never a second attempt).
   * A resume of a handle calls this *before* the wait when the port exists.
   */
  resume?(request: ResumeRequest): Promise<ResumeResult>;
  /** The research writer's CAS write of the decision record. */
  persistDecision?(record: DecisionRecord): Promise<DecisionPersistResult>;
  /** Attempt-fenced intermediate checkpoint (FencedCommitPort client side). */
  persistCheckpoint?(
    checkpoint: SupervisorCheckpoint,
  ): MaybePromise<CheckpointResult>;
  /** Platform saga/receipt budget counters (never a fresh-memory estimate). */
  budgetUsage?(): Promise<BudgetUsageReceipt>;
  persistTaskRef?(ref: TaskRef): Promise<void>;
  persistReport?(report: EvidenceReport): Promise<void>;
  /**
   * The platform gateway's authoritative usage read (M3-03).  The Supervisor
   * syncs `used.model_requests` from here; it never settles from its own
   * in-memory counter.
   */
  modelGateway?(): Promise<ModelGatewayUsage>;
}

export interface SupervisorResult {
  readonly status: "completed" | "stopped" | "waiting";
  readonly stop_reason: SupervisorStopReason;
  readonly rounds_completed: number;
  /** Submit calls performed by *this* run (a resume performs none). */
  readonly submits: number;
  /** Bound references of the round(s), restored from the handle on resume. */
  readonly task_refs: readonly TaskRef[];
  readonly reports: readonly EvidenceReport[];
  readonly phases: readonly PhaseRecord[];
  readonly model_invocations: number;
  readonly model_requests_used: number;
  readonly wait_invocations: number;
  readonly wait_polls: number;
  readonly cursor_resets: number;
  readonly duplicate_terminal_events: number;
  readonly last_report: EvidenceReport | null;
  readonly last_advice: SynthesizerOutput["advice"] | null;
  /** Verified evidence bindings, in evaluation order. */
  readonly bindings: readonly EvidenceBinding[];
  /** Persisted decisions, in evaluation order. */
  readonly decisions: readonly DecisionRecord[];
  /** The last persisted decision (never a model conclusion). */
  readonly decision: DecisionRecord | null;
  /** Present exactly when `status === "waiting"`. */
  readonly resume_handle: ResumeHandle | null;
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

/** Execution terminal states of the platform projection (never `not_started`). */
const EXECUTION_TERMINAL_STATES = new Set(["completed", "failed", "cancelled"]);

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

function copyBudget(budget: PlanBudget): MutableBudget {
  return {
    model_requests: budget.model_requests,
    input_tokens: budget.input_tokens,
    output_tokens: budget.output_tokens,
    task_refs: budget.task_refs,
    recipe_steps: budget.recipe_steps,
    qc_invocations: budget.qc_invocations,
    wall_seconds: budget.wall_seconds,
    queue_seconds: budget.queue_seconds,
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

type ModelSyncOutcome =
  | { readonly ok: true; readonly used: number }
  | {
      readonly ok: false;
      readonly reason: "model_gateway_unavailable" | "model_usage_mismatch";
    };

/**
 * Read the authoritative model-request count from the platform gateway.
 *
 * The Supervisor's own `used.model_requests` is a reservation counter, not a
 * settlement.  When a gateway port exists, the platform receipt count wins in
 * both directions: a lower count is legitimate when some phases are
 * deterministic helpers (no provider call, no receipt), and a higher count is
 * external/previous spend that must reduce the remaining allowance.  A
 * malformed count (negative/non-integer) fails closed instead of resetting.
 */
async function syncModelRequests(
  ports: SupervisorPorts,
  reserved: number,
  requireGateway: boolean,
): Promise<ModelSyncOutcome> {
  const gateway = ports.modelGateway;
  if (gateway === undefined) {
    return requireGateway
      ? { ok: false, reason: "model_gateway_unavailable" }
      : { ok: true, used: reserved };
  }
  let usage: ModelGatewayUsage;
  try {
    usage = await gateway();
  } catch {
    return { ok: false, reason: "model_gateway_unavailable" };
  }
  if (!Number.isSafeInteger(usage.model_requests) || usage.model_requests < 0) {
    return { ok: false, reason: "model_usage_mismatch" };
  }
  return { ok: true, used: usage.model_requests };
}

/**
 * Restore the non-model budget counters from the platform's own record.
 *
 * A value below what this run already reserved cannot be adopted (it would let
 * the same allowance be spent twice), so the maximum wins; the model count is
 * owned by the gateway sync when a gateway exists.  A malformed receipt fails
 * closed.
 */
async function applyBudgetReceipts(
  ports: SupervisorPorts,
  used: MutableBudget,
): Promise<"budget_receipt_mismatch" | null> {
  const port = ports.budgetUsage;
  if (port === undefined) {
    return null;
  }
  let receipt: BudgetUsageReceipt;
  try {
    receipt = await port();
  } catch {
    return "budget_receipt_mismatch";
  }
  if (receipt.source !== "platform-receipt" && receipt.source !== "saga-record") {
    return "budget_receipt_mismatch";
  }
  const observed = receipt.used;
  for (const field of BUDGET_FIELDS) {
    const value = observed[field];
    if (!Number.isSafeInteger(value) || value < 0) {
      return "budget_receipt_mismatch";
    }
  }
  for (const field of BUDGET_FIELDS) {
    if (field === "model_requests") {
      if (ports.modelGateway === undefined) {
        used.model_requests = observed.model_requests;
      }
      continue;
    }
    used[field] = Math.max(used[field], observed[field]);
  }
  return null;
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

/**
 * Derive the research decision from the single platform projection (M3-04).
 *
 * `accept` is allowed only through the frozen policy
 * (`@rph/research::evaluateDecision`); a completed-but-inconclusive execution
 * therefore maps to `revise`/`escalate`, never to `accept`.  No model text and
 * no local report body participates in this decision.
 */
export function decideFromView(view: EvidenceView): ResearchDecision {
  if (evaluateDecision(view, "accept").allowed) {
    return "accept";
  }
  if (
    view.execution_terminal_state === "failed" ||
    view.execution_terminal_state === "cancelled"
  ) {
    return "reject";
  }
  if (view.evidence_status === "failed") {
    return "reject";
  }
  if (view.evidence_status === "incomplete") {
    return "escalate";
  }
  if (view.blocking_missing_checks.length > 0) {
    return "escalate";
  }
  return "revise";
}

/** Legacy flat projection of the digest-bound reference (skills are legacy). */
export function legacyTaskRef(ref: ResearchTaskRef): TaskRef {
  return Object.freeze({
    project_id: ref.project_id,
    job_id: ref.task_id,
    attempt: ref.attempt,
    release_id: ref.release.release_id,
  });
}

function isResearchPortRule(error: unknown, rule: ResearchPortRule): boolean {
  return error instanceof ResearchPortError && error.rule === rule;
}

function isPlanDigestConflict(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { code?: unknown }).code === "plan_digest_changed"
  );
}

function boundedWaitPolls(limits: SupervisorLimits): number {
  const value = limits.max_wait_polls;
  if (value === undefined) {
    return DEFAULT_MAX_WAIT_POLLS;
  }
  return Number.isSafeInteger(value) && value > 0 ? value : 1;
}

function boundedPollMs(limits: SupervisorLimits): number {
  const value = limits.wait_poll_ms;
  if (value === undefined || !Number.isFinite(value) || value < 0) {
    return WAIT_MAX_WAIT_MS;
  }
  return Math.min(Math.floor(value), WAIT_MAX_WAIT_MS);
}

function boundedEvents(limits: SupervisorLimits): number {
  const value = limits.max_events_per_poll;
  if (value === undefined || !Number.isSafeInteger(value) || value <= 0) {
    return WAIT_MAX_EVENTS;
  }
  return Math.min(value, WAIT_MAX_EVENTS);
}

function boundedCursorResets(limits: SupervisorLimits): number {
  const value = limits.max_cursor_resets;
  if (value === undefined) {
    return DEFAULT_MAX_CURSOR_RESETS;
  }
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

// ---------------------------------------------------------------------------
// Run state
// ---------------------------------------------------------------------------

interface RunContext {
  readonly input: SupervisorInput;
  readonly ports: SupervisorPorts;
  readonly skills: SupervisorSkills;
  readonly phases: PhaseRecord[];
  readonly used: MutableBudget;
  readonly taskRefs: TaskRef[];
  readonly reports: EvidenceReport[];
  readonly bindings: EvidenceBinding[];
  readonly decisions: DecisionRecord[];
  readonly seenEvents: Set<string>;
  submits: number;
  modelInvocations: number;
  waitInvocations: number;
  waitPolls: number;
  cursorResets: number;
  duplicateTerminals: number;
  lastReport: EvidenceReport | null;
  lastAdvice: SynthesizerOutput["advice"] | null;
  gatewayStop:
    | "model_gateway_unavailable"
    | "model_usage_mismatch"
    | "budget_receipt_mismatch"
    | null;
  boundRef: ResearchTaskRef | null;
  approvalRef: string | null;
  submitIntentKey: string;
  resumeCount: number;
  lastCursor: string | null;
}

function createContext(
  input: SupervisorInput,
  ports: SupervisorPorts,
  skills: SupervisorSkills,
): RunContext {
  return {
    input,
    ports,
    skills,
    phases: [],
    used: zeroBudget(),
    taskRefs: [],
    reports: [],
    bindings: [],
    decisions: [],
    seenEvents: new Set<string>(),
    submits: 0,
    modelInvocations: 0,
    waitInvocations: 0,
    waitPolls: 0,
    cursorResets: 0,
    duplicateTerminals: 0,
    lastReport: input.prior_evidence ?? null,
    lastAdvice: null,
    gatewayStop: null,
    boundRef: null,
    approvalRef: null,
    submitIntentKey: "",
    resumeCount: 0,
    lastCursor: null,
  };
}

function resultOf(
  ctx: RunContext,
  status: SupervisorResult["status"],
  reason: SupervisorStopReason,
  rounds: number,
  handle: ResumeHandle | null,
): SupervisorResult {
  if (status !== "waiting") {
    addPhase(ctx.phases, "stop", rounds, reason);
  }
  return Object.freeze({
    status,
    stop_reason: reason,
    rounds_completed: rounds,
    submits: ctx.submits,
    task_refs: Object.freeze([...ctx.taskRefs]),
    reports: Object.freeze([...ctx.reports]),
    phases: Object.freeze([...ctx.phases]),
    model_invocations: ctx.modelInvocations,
    model_requests_used: ctx.used.model_requests,
    wait_invocations: ctx.waitInvocations,
    wait_polls: ctx.waitPolls,
    cursor_resets: ctx.cursorResets,
    duplicate_terminal_events: ctx.duplicateTerminals,
    last_report: ctx.lastReport,
    last_advice: ctx.lastAdvice,
    bindings: Object.freeze([...ctx.bindings]),
    decisions: Object.freeze([...ctx.decisions]),
    decision: ctx.decisions.at(-1) ?? null,
    resume_handle: handle,
  });
}

function finish(
  ctx: RunContext,
  reason: SupervisorStopReason,
  rounds: number,
  handle: ResumeHandle | null = null,
): SupervisorResult {
  return resultOf(
    ctx,
    reason === "accepted" ? "completed" : "stopped",
    reason,
    rounds,
    handle,
  );
}

function buildHandle(
  ctx: RunContext,
  round: number,
  planDigest: string,
  state: ResumeState,
  pendingDecision: ResearchDecision | null,
  pendingProjectionDigest: string | null,
): ResumeHandle | null {
  if (ctx.boundRef === null) {
    return null;
  }
  return Object.freeze({
    schema: SUPERVISOR_RECOVERY_SCHEMA,
    state,
    project_id: ctx.input.project_id,
    research_id: ctx.input.research_id,
    round,
    plan_definition_digest: planDigest,
    approval_ref: ctx.approvalRef,
    submit_intent_key: ctx.submitIntentKey,
    task_ref: ctx.boundRef,
    cursor: ctx.lastCursor,
    wait_polls: ctx.waitPolls,
    cursor_resets: ctx.cursorResets,
    resume_count: ctx.resumeCount,
    consumed_budget: Object.freeze(copyBudget(ctx.used)),
    reports: Object.freeze(ctx.reports.map((report) => report.report_id)),
    pending_decision: pendingDecision,
    pending_projection_digest: pendingProjectionDigest,
  });
}

// ---------------------------------------------------------------------------
// Phase helpers
// ---------------------------------------------------------------------------

type PhaseOutcome<T> =
  | { readonly done: SupervisorResult }
  | { readonly value: T };

function done<T>(result: SupervisorResult): PhaseOutcome<T> {
  return { done: result };
}

function value<T>(payload: T): PhaseOutcome<T> {
  return { value: payload };
}

/**
 * Fixture-only fallback key.
 *
 * Deterministic over the same identity the M1-02 ledger keys on, so a restart
 * of a fixture run replays the same task; it is never used when the ledger port
 * is present and it never claims to be the durable intent key.
 */
function fallbackSubmitIntent(identity: SubmitIntentIdentity): SubmitIntentRef {
  const parts = [
    identity.project_id,
    identity.research_id,
    `round=${identity.round}`,
    identity.plan_definition_digest,
  ].join("|");
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (let index = 0; index < parts.length; index += 1) {
    hash ^= BigInt(parts.charCodeAt(index));
    hash = (hash * prime) & mask;
  }
  const key = `fixture.round-${identity.round}.${hash.toString(16).padStart(16, "0")}`;
  return Object.freeze({ key, wire_key: key, created: true });
}

async function writeCheckpoint(
  ctx: RunContext,
  stage: CheckpointStage,
  round: number,
  planDigest: string,
  state: ResumeState,
  pendingDecision: ResearchDecision | null = null,
  pendingProjectionDigest: string | null = null,
): Promise<SupervisorResult | null> {
  const handle = buildHandle(
    ctx,
    round,
    planDigest,
    state,
    pendingDecision,
    pendingProjectionDigest,
  );
  const port = ctx.ports.persistCheckpoint;
  if (handle === null || port === undefined) {
    return null;
  }
  let outcome: CheckpointResult;
  try {
    outcome = await port({ stage, round, handle });
  } catch (error) {
    if (!(error instanceof ResearchPortError)) {
      // An unexpected port failure is not a verdict: it propagates untouched so
      // the caller's crash handling (or the harness) observes the real cause.
      throw error;
    }
    return finish(ctx, error.rule === "fenced" ? "fenced" : "persist_failed", round);
  }
  if (outcome.fenced === true) {
    return finish(ctx, "fenced", round);
  }
  if (!outcome.ok) {
    return finish(ctx, "persist_failed", round);
  }
  return null;
}

interface SubmitOutcome {
  /** The digest-bound reference; `null` in legacy fixture mode. */
  readonly bound: ResearchTaskRef | null;
  readonly legacy: TaskRef;
}

/**
 * Persist the submit intent, submit, and bind the digest-bound TaskRef.
 *
 * The key is never derived here when a ledger port exists: the port owns the
 * durable row, and a replayed row returns the same key after a crash.
 */
async function submitPhase(
  ctx: RunContext,
  round: number,
  plan: PlannerOutput,
  approvalRef: string,
): Promise<PhaseOutcome<SubmitOutcome>> {
  const identity: SubmitIntentIdentity = Object.freeze({
    project_id: ctx.input.project_id,
    research_id: ctx.input.research_id,
    round,
    plan_definition_digest: plan.plan_definition_digest,
  });
  let intent: SubmitIntentRef;
  if (ctx.ports.submitIntent !== undefined) {
    try {
      intent = await ctx.ports.submitIntent.prepare(identity);
    } catch (error) {
      if (!isPlanDigestConflict(error)) {
        throw error;
      }
      return done(finish(ctx, "plan_changed", round));
    }
  } else {
    // Fixture/legacy runs have no ledger: derive a restart-stable key from the
    // same identity so a re-run never invents a second task.
    intent = fallbackSubmitIntent(identity);
  }
  ctx.submitIntentKey = intent.key;

  const submitted = await ctx.ports.submit({
    project_id: ctx.input.project_id,
    round,
    plan_definition_digest: plan.plan_definition_digest,
    approval_ref: approvalRef,
    plan,
    idempotency_key: intent.wire_key,
    submit_intent_key: intent.key,
  });
  if (!submitted.ok || submitted.task_ref === null) {
    return done(finish(ctx, "submit_failed", round));
  }
  if (ctx.ports.submitIntent !== undefined) {
    try {
      await ctx.ports.submitIntent.markSubmitted(identity);
    } catch {
      return done(finish(ctx, "submit_failed", round));
    }
  }

  const legacyRef = submitted.task_ref;
  let bound: ResearchTaskRef | null = null;
  if (submitted.bound_ref !== undefined && submitted.bound_ref !== null) {
    try {
      bound = parsePlatformTaskRef(submitted.bound_ref, ctx.input.project_id);
    } catch {
      return done(finish(ctx, "evidence_identity_mismatch", round));
    }
    if (bound.project_id !== ctx.input.project_id) {
      return done(finish(ctx, "foreign_evidence", round));
    }
    if (
      bound.task_id !== legacyRef.job_id ||
      bound.attempt !== legacyRef.attempt ||
      bound.release.release_id !== legacyRef.release_id
    ) {
      // The two answers of one submit may not describe two different attempts.
      return done(finish(ctx, "submit_failed", round));
    }
  } else if (ctx.input.require_evidence_binding === true) {
    return done(finish(ctx, "evidence_binding_required", round));
  }

  ctx.submits += 1;
  const submitSync = await syncModelRequests(
    ctx.ports,
    ctx.used.model_requests,
    ctx.input.require_model_gateway === true,
  );
  if (!submitSync.ok) {
    return done(finish(ctx, submitSync.reason, round));
  }
  ctx.used.model_requests = submitSync.used;
  const receiptStop = await applyBudgetReceipts(ctx.ports, ctx.used);
  if (receiptStop !== null) {
    return done(finish(ctx, receiptStop, round));
  }
  ctx.used.task_refs += plan.budget.task_refs;
  ctx.used.recipe_steps += plan.budget.recipe_steps;
  ctx.used.qc_invocations += plan.budget.qc_invocations;
  ctx.used.wall_seconds += plan.budget.wall_seconds;
  ctx.used.queue_seconds += plan.budget.queue_seconds;
  ctx.used.input_tokens += plan.budget.input_tokens;
  ctx.used.output_tokens += plan.budget.output_tokens;
  ctx.taskRefs.push(legacyRef);
  if (ctx.ports.persistTaskRef !== undefined) {
    try {
      await ctx.ports.persistTaskRef(legacyRef);
    } catch {
      return done(finish(ctx, "persist_failed", round));
    }
  }
  if (bound !== null) {
    ctx.boundRef = bound;
    ctx.lastCursor = null;
    const checkpoint = await writeCheckpoint(
      ctx,
      "task_bound",
      round,
      plan.plan_definition_digest,
      "waiting",
    );
    if (checkpoint !== null) {
      return done(checkpoint);
    }
  }
  return value({ bound, legacy: legacyRef });
}

interface WaitOutcome {
  readonly events: readonly WaitEvent[];
  readonly cursor: string | null;
  /** Cursor observed before the poll that produced the terminal events. */
  readonly observedCursor: string | null;
}

function terminalEventsOf(events: readonly WaitEvent[]): WaitEvent[] {
  return events.filter((event) => TERMINAL_EVENT_TYPES.has(event.type));
}

/**
 * The bounded wait loop.
 *
 * Each poll is bounded by the platform contract (`max_wait_ms <= 60 s`,
 * `max_events <= 200`).  An expired cursor is an explicit snapshot reset: the
 * next poll sends `cursor: null` and adopts the authoritative snapshot instead
 * of silently skipping the gap.  Exhausting the poll bound returns `waiting`
 * with a resume handle — never a fabricated status and never a new task.
 */
async function waitPhase(
  ctx: RunContext,
  round: number,
  planDigest: string,
  bound: ResearchTaskRef,
  startCursor: string | null,
): Promise<PhaseOutcome<WaitOutcome>> {
  let cursor = startCursor;
  let polls = 0;
  const maxPolls = boundedWaitPolls(ctx.input.limits);
  const pollMs = boundedPollMs(ctx.input.limits);
  const maxEvents = boundedEvents(ctx.input.limits);
  const maxResets = boundedCursorResets(ctx.input.limits);
  const legacyRef = legacyTaskRef(bound);

  while (polls < maxPolls) {
    polls += 1;
    ctx.waitInvocations += 1;
    ctx.waitPolls += 1;
    const observedCursor = cursor;
    addPhase(
      ctx.phases,
      "wait",
      round,
      `bounded poll ${polls}/${maxPolls} (max_wait_ms=${pollMs}, max_events=${maxEvents})`,
    );
    let waited: WaitResult;
    try {
      waited = await ctx.ports.wait({
        project_id: ctx.input.project_id,
        round,
        task_ref: legacyRef,
        cursor,
        max_events: maxEvents,
        max_wait_ms: pollMs,
      });
    } catch (error) {
      if (isResearchPortRule(error, "cursor_expired")) {
        ctx.cursorResets += 1;
        addPhase(
          ctx.phases,
          "wait",
          round,
          "cursor expired: explicit snapshot reset (never a silent gap)",
        );
        if (ctx.cursorResets > maxResets) {
          return done(finish(ctx, "cursor_expired", round));
        }
        cursor = null;
        continue;
      }
      if (
        isResearchPortRule(error, "cursor_invalid") ||
        isResearchPortRule(error, "cursor_project_mismatch")
      ) {
        return done(finish(ctx, "cursor_rejected", round));
      }
      if (isResearchPortRule(error, "fenced")) {
        return done(finish(ctx, "fenced", round));
      }
      if (error instanceof ResearchPortError) {
        return done(finish(ctx, "wait_failed", round));
      }
      // A non-typed failure is a crash, never a wait verdict.
      throw error;
    }
    cursor = waited.cursor ?? null;
    ctx.lastCursor = cursor;

    const batchSeen = new Set<string>();
    const fresh: WaitEvent[] = [];
    let duplicateTerminalSeen = false;
    for (const event of waited.events) {
      const isTerminal = TERMINAL_EVENT_TYPES.has(event.type);
      if (ctx.seenEvents.has(event.event_key) || batchSeen.has(event.event_key)) {
        ctx.duplicateTerminals += 1;
        if (isTerminal) {
          duplicateTerminalSeen = true;
        }
        continue;
      }
      batchSeen.add(event.event_key);
      fresh.push(event);
    }
    if (waited.events.length > 0 && fresh.length === 0 && duplicateTerminalSeen) {
      return done(finish(ctx, "duplicate_terminal", round));
    }
    const terminals = terminalEventsOf(fresh);
    if (terminals.length > 0) {
      for (const event of fresh) {
        ctx.seenEvents.add(event.event_key);
      }
      return value({ events: fresh, cursor, observedCursor });
    }
  }

  const checkpoint = await writeCheckpoint(
    ctx,
    "waiting",
    round,
    planDigest,
    "waiting",
  );
  if (checkpoint !== null) {
    return done(checkpoint);
  }
  addPhase(
    ctx.phases,
    "wait",
    round,
    "bounded wait exhausted without a terminal event: waiting + resume handle",
  );
  const handle = buildHandle(ctx, round, planDigest, "waiting", null, null);
  return done(resultOf(ctx, "waiting", "wait_pending", round, handle));
}

/**
 * Read the single platform projection and attribute it to this run.
 *
 * Nothing here derives a claim status: the projection is validated by its own
 * schema and attributed by project/research/round/attempt/release.  A foreign
 * or stale projection is a typed stop, never a downgraded read.
 */
async function readAttributedView(
  ctx: RunContext,
  round: number,
  bound: ResearchTaskRef,
  eventKey: string,
  cursor: string | null,
): Promise<PhaseOutcome<EvidenceView>> {
  const port = ctx.ports.evidence;
  if (port === undefined) {
    return done(finish(ctx, "evidence_binding_required", round));
  }
  let payload: EvidenceResult;
  try {
    payload = await port({
      project_id: ctx.input.project_id,
      research_id: ctx.input.research_id,
      round,
      task_ref: bound,
      event_key: eventKey,
      cursor,
    });
  } catch (error) {
    if (isResearchPortRule(error, "fenced")) {
      return done(finish(ctx, "fenced", round));
    }
    if (error instanceof ResearchPortError) {
      return done(finish(ctx, "evidence_read_failed", round));
    }
    // A non-typed failure is a crash, never an evidence verdict.
    throw error;
  }

  let view: EvidenceView;
  try {
    view = attributeEvidenceView(payload.view, {
      project_id: ctx.input.project_id,
      research_id: ctx.input.research_id,
      round,
    });
  } catch (error) {
    if (error instanceof BridgeContractError) {
      return done(finish(ctx, "foreign_evidence", round));
    }
    return done(finish(ctx, "evidence_binding_required", round));
  }

  let viewRef: ResearchTaskRef;
  try {
    viewRef = parsePlatformTaskRef(view.task_ref, ctx.input.project_id);
  } catch {
    return done(finish(ctx, "evidence_identity_mismatch", round));
  }
  if (attributedTaskRefKey(viewRef) !== attributedTaskRefKey(bound)) {
    return done(finish(ctx, "evidence_identity_mismatch", round));
  }
  if (!EXECUTION_TERMINAL_STATES.has(view.execution_terminal_state)) {
    return done(finish(ctx, "execution_not_terminal", round));
  }
  return value(view);
}

interface BindingOutcome {
  readonly binding: EvidenceBinding;
  readonly view: EvidenceView;
}

/** The verified binding of one already-attributed projection. */
function bindingFromView(
  ctx: RunContext,
  view: EvidenceView,
  eventKey: string,
): EvidenceBinding {
  return Object.freeze({
    event_key: eventKey,
    task_ref: parsePlatformTaskRef(view.task_ref, ctx.input.project_id),
    report_id: view.report.report_id,
    report_digest: view.report.digest,
    projection_digest: view.projection_digest,
    evidence_status: view.evidence_status,
    overall_claim_status: view.overall_claim_status,
    limitations: Object.freeze([...view.limitations]),
    blocking_missing_checks: Object.freeze([...view.blocking_missing_checks]),
    verified: true,
  });
}

/**
 * Verify the terminal events against the single evidence projection.
 *
 * Nothing "first non-empty report" survives here: every terminal event must
 * name the bound attempt/release, all of them must agree on one report digest,
 * the projection must be attributable to this project/research/round, and its
 * report digest must equal the event's.  Any violation stops the run.
 */
async function bindEvidence(
  ctx: RunContext,
  round: number,
  bound: ResearchTaskRef,
  terminals: readonly WaitEvent[],
  observedCursor: string | null,
): Promise<PhaseOutcome<BindingOutcome>> {
  let digest: string | null = null;
  let reportId: string | null = null;
  let eventKey: string | null = null;
  for (const event of terminals) {
    if (event.task_ref.project_id !== bound.project_id) {
      return done(finish(ctx, "foreign_evidence", round));
    }
    if (event.task_ref.job_id !== bound.task_id) {
      return done(finish(ctx, "foreign_evidence", round));
    }
    if (
      event.task_ref.attempt !== bound.attempt ||
      event.task_ref.release_id !== bound.release.release_id
    ) {
      return done(finish(ctx, "evidence_identity_mismatch", round));
    }
    const declared = event.report_digest ?? null;
    if (declared === null) {
      return done(finish(ctx, "evidence_binding_required", round));
    }
    if (digest !== null && declared !== digest) {
      return done(finish(ctx, "ambiguous_terminal", round));
    }
    if (
      reportId !== null &&
      event.report_id !== undefined &&
      event.report_id !== null &&
      event.report_id !== reportId
    ) {
      return done(finish(ctx, "ambiguous_terminal", round));
    }
    digest = declared;
    reportId = event.report_id ?? reportId;
    eventKey = event.event_key;
  }
  if (digest === null || eventKey === null) {
    return done(finish(ctx, "no_evidence", round));
  }

  const read = await readAttributedView(ctx, round, bound, eventKey, observedCursor);
  if ("done" in read) {
    return read;
  }
  const view = read.value;
  if (view.report.digest !== digest) {
    return done(finish(ctx, "evidence_digest_mismatch", round));
  }
  if (reportId !== null && view.report.report_id !== reportId) {
    return done(finish(ctx, "evidence_identity_mismatch", round));
  }
  return value({ binding: bindingFromView(ctx, view, eventKey), view });
}

/**
 * Persist the decision through the research writer CAS.
 *
 * A refusal (stale revision, fencing) is never treated as success: without a
 * durable decision row the run stops and nothing is reported as accepted.
 */
async function persistDecision(
  ctx: RunContext,
  view: EvidenceView,
  decision: ResearchDecision,
  round: number,
): Promise<PhaseOutcome<DecisionRecord>> {
  let record: DecisionRecord;
  try {
    record = buildDecisionRecord(view, decision);
  } catch {
    return done(finish(ctx, "decision_denied", round));
  }
  const port = ctx.ports.persistDecision;
  if (port === undefined) {
    return done(finish(ctx, "persist_failed", round));
  }
  let outcome: DecisionPersistResult;
  try {
    outcome = await port(record);
  } catch (error) {
    if (!(error instanceof ResearchPortError)) {
      // A crash between the decision and the CAS is not a verdict.
      throw error;
    }
    return done(finish(ctx, error.rule === "fenced" ? "fenced" : "persist_failed", round));
  }
  if (outcome.fenced === true) {
    return done(finish(ctx, "fenced", round));
  }
  if (!outcome.ok) {
    return done(finish(ctx, "persist_failed", round));
  }
  ctx.decisions.push(record);
  return value(record);
}

function decisionReason(decision: ResearchDecision): SupervisorStopReason {
  switch (decision) {
    case "accept":
      return "accepted";
    case "reject":
      return "decision_reject";
    case "defer":
      return "decision_defer";
    case "escalate":
      return "decision_escalate";
    case "revise":
      return "report_stop";
  }
}

// ---------------------------------------------------------------------------
// Resume
// ---------------------------------------------------------------------------

function validateHandle(
  ctx: RunContext,
  handle: ResumeHandle,
  planDigest: string,
): SupervisorResult | null {
  if (handle.schema !== SUPERVISOR_RECOVERY_SCHEMA) {
    return finish(ctx, "resume_handle_invalid", 0);
  }
  if (
    handle.project_id !== ctx.input.project_id ||
    handle.research_id !== ctx.input.research_id
  ) {
    return finish(ctx, "resume_handle_invalid", 0);
  }
  if (handle.state !== "waiting" && handle.state !== "decision_pending") {
    return finish(ctx, "resume_handle_invalid", 0);
  }
  if (handle.plan_definition_digest !== planDigest) {
    // A new plan needs a new round/approval; the old handle may not execute it.
    return finish(ctx, "plan_changed", handle.round);
  }
  if (handle.round >= ctx.input.limits.max_rounds) {
    return finish(ctx, "round_cap", handle.round);
  }
  let bound: ResearchTaskRef;
  try {
    bound = parsePlatformTaskRef(handle.task_ref, ctx.input.project_id);
  } catch {
    return finish(ctx, "resume_handle_invalid", handle.round);
  }
  if (bound.project_id !== ctx.input.project_id) {
    return finish(ctx, "foreign_evidence", handle.round);
  }
  for (const field of BUDGET_FIELDS) {
    const consumed = handle.consumed_budget[field];
    if (!Number.isSafeInteger(consumed) || consumed < 0) {
      return finish(ctx, "resume_handle_invalid", handle.round);
    }
  }
  return null;
}

/**
 * Continue a `waiting`/`decision_pending` run in a new process.
 *
 * No plan, no submit and no model call happen here: the round, the TaskRef, the
 * attempt, the approval and the used budget are restored from the durable
 * handle and the platform's own receipts, and the run re-enters the *existing*
 * wait/evidence/decision path.
 */
async function resumeRun(
  ctx: RunContext,
  handle: ResumeHandle,
): Promise<SupervisorResult> {
  const digestFor = ctx.input.plan_definition_digest ?? roundDigest;
  const planDigest = digestFor(handle.round);
  const invalid = validateHandle(ctx, handle, planDigest);
  if (invalid !== null) {
    return invalid;
  }
  const bound = parsePlatformTaskRef(handle.task_ref, ctx.input.project_id);
  ctx.boundRef = bound;
  ctx.approvalRef = handle.approval_ref;
  ctx.submitIntentKey = handle.submit_intent_key;
  ctx.resumeCount = handle.resume_count + 1;
  ctx.cursorResets = handle.cursor_resets;
  ctx.waitPolls = handle.wait_polls;
  ctx.lastCursor = handle.cursor;
  ctx.taskRefs.push(legacyTaskRef(bound));
  ctx.used.model_requests = handle.consumed_budget.model_requests;
  for (const field of BUDGET_FIELDS) {
    if (field !== "model_requests") {
      ctx.used[field] = handle.consumed_budget[field];
    }
  }
  addPhase(
    ctx.phases,
    "wait",
    handle.round,
    `resume from handle (state=${handle.state}); no plan, no submit, no model call`,
  );

  // Used budget is the platform's fact (M3-03 gateway + saga receipts); this
  // process never re-estimates it from empty memory.
  const gatewaySync = await syncModelRequests(
    ctx.ports,
    ctx.used.model_requests,
    ctx.input.require_model_gateway === true,
  );
  if (!gatewaySync.ok) {
    return finish(ctx, gatewaySync.reason, handle.round);
  }
  ctx.used.model_requests = gatewaySync.used;
  const receiptStop = await applyBudgetReceipts(ctx.ports, ctx.used);
  if (receiptStop !== null) {
    return finish(ctx, receiptStop, handle.round);
  }

  // P2-R5: advance the *same* saga (idempotent) and adopt its opaque cursor.
  // This is a resume of the existing chain, never a second attempt.
  let startCursor = handle.cursor;
  if (ctx.ports.resume !== undefined) {
    let resumed: ResumeResult;
    try {
      resumed = await ctx.ports.resume({
        project_id: ctx.input.project_id,
        research_id: ctx.input.research_id,
        round: handle.round,
        plan_definition_digest: planDigest,
        task_ref: bound,
        cursor: handle.cursor,
        diagnostic_only: false,
      });
    } catch (error) {
      if (error instanceof ResearchPortError) {
        if (error.rule === "fenced") {
          return finish(ctx, "fenced", handle.round);
        }
        if (
          error.rule === "cursor_invalid" ||
          error.rule === "cursor_project_mismatch"
        ) {
          return finish(ctx, "cursor_rejected", handle.round);
        }
        if (error.rule === "cursor_expired") {
          return finish(ctx, "cursor_expired", handle.round);
        }
        return finish(ctx, "wait_failed", handle.round);
      }
      throw error;
    }
    // The platform cursor is adopted only while there is no terminal state.
    // Once a terminal is reported, the handle's cursor (the pre-observation
    // anchor) stays authoritative so the wait re-observes the terminal instead
    // of skipping it.
    if (resumed.terminal_state === null) {
      startCursor = resumed.cursor ?? startCursor;
    }
    ctx.lastCursor = startCursor;
    addPhase(
      ctx.phases,
      "wait",
      handle.round,
      `platform saga resumed (advanced=${String(resumed.advanced)})`,
    );
  }

  let view: EvidenceView | null = null;
  if (handle.state === "decision_pending") {
    // The evidence was already read before the crash: re-read the same
    // projection and require the identical projection digest.
    const read = await readAttributedView(
      ctx,
      handle.round,
      bound,
      `resume:${bound.task_id}:attempt=${bound.attempt}`,
      startCursor,
    );
    if ("done" in read) {
      return read.done;
    }
    view = read.value;
    if (
      handle.pending_projection_digest !== null &&
      view.projection_digest !== handle.pending_projection_digest
    ) {
      return finish(ctx, "evidence_digest_mismatch", handle.round);
    }
    ctx.bindings.push(
      bindingFromView(ctx, view, `resume:${bound.task_id}:attempt=${bound.attempt}`),
    );
  } else {
    const waited = await waitPhase(
      ctx,
      handle.round,
      planDigest,
      bound,
      startCursor,
    );
    if ("done" in waited) {
      return waited.done;
    }
    const boundPhase = await bindEvidence(
      ctx,
      handle.round,
      bound,
      terminalEventsOf(waited.value.events),
      waited.value.observedCursor,
    );
    if ("done" in boundPhase) {
      return boundPhase.done;
    }
    view = boundPhase.value.view;
    ctx.bindings.push(boundPhase.value.binding);
  }

  const checkpoint = await writeCheckpoint(
    ctx,
    "evidence_bound",
    handle.round,
    planDigest,
    "decision_pending",
    null,
    view.projection_digest,
  );
  if (checkpoint !== null) {
    return checkpoint;
  }

  const decision = decideFromView(view);
  if (handle.pending_decision !== null && handle.pending_decision !== decision) {
    // The projection is unchanged; a different verdict means the policy or the
    // input drifted.  Never persist a different decision for the same evidence.
    return finish(ctx, "decision_changed", handle.round);
  }
  const pending = await writeCheckpoint(
    ctx,
    "decision_pending",
    handle.round,
    planDigest,
    "decision_pending",
    decision,
    view.projection_digest,
  );
  if (pending !== null) {
    return pending;
  }
  const persisted = await persistDecision(ctx, view, decision, handle.round);
  if ("done" in persisted) {
    return persisted.done;
  }
  if (decision === "accept") {
    return finish(ctx, "accepted", handle.round + 1);
  }
  if (decision === "revise") {
    return finish(ctx, "report_stop", handle.round);
  }
  return finish(ctx, decisionReason(decision), handle.round);
}

// ---------------------------------------------------------------------------
// The supervisor state machine
// ---------------------------------------------------------------------------

/** Run the single supervisor state machine to a terminal decision. */
export async function runSupervisor(
  input: SupervisorInput,
  ports: SupervisorPorts,
  skills: SupervisorSkills,
): Promise<SupervisorResult> {
  const ctx = createContext(input, ports, skills);

  if (skills.planner_kind === "fixture" && input.require_model_gateway === true) {
    return finish(ctx, "fixture_planner_rejected", 0);
  }
  if (input.require_model_gateway === true && ports.modelGateway === undefined) {
    return finish(ctx, "model_gateway_unavailable", 0);
  }
  if (input.require_evidence_binding === true) {
    if (ports.evidence === undefined) {
      return finish(ctx, "evidence_binding_required", 0);
    }
    if (ports.persistDecision === undefined) {
      return finish(ctx, "persist_failed", 0);
    }
    if (ports.submitIntent === undefined) {
      return finish(ctx, "submit_intent_required", 0);
    }
  }

  if (input.resume_handle !== null && input.resume_handle !== undefined) {
    return resumeRun(ctx, input.resume_handle);
  }

  const initialSync = await syncModelRequests(
    ports,
    ctx.used.model_requests,
    input.require_model_gateway === true,
  );
  if (!initialSync.ok) {
    return finish(ctx, initialSync.reason, 0);
  }
  ctx.used.model_requests = initialSync.used;
  const initialReceipts = await applyBudgetReceipts(ports, ctx.used);
  if (initialReceipts !== null) {
    return finish(ctx, initialReceipts, 0);
  }

  const remaining = (): PlanBudget => remainingBudget(input.limits.budget, ctx.used);

  const callModel = async <T>(
    phase: SupervisorPhase,
    roundForCall: number,
    detail: string,
    body: () => Promise<T>,
  ): Promise<T | null> => {
    const spendsModelRequest =
      skills.model_phases === undefined
        ? true
        : skills.model_phases.includes(phase);
    if (ports.modelGateway !== undefined || input.require_model_gateway === true) {
      // Before each phase, the platform count replaces the local reservation
      // counter: deterministic helpers do not consume a model receipt.
      const gatewaySync = await syncModelRequests(
        ports,
        ctx.used.model_requests,
        input.require_model_gateway === true,
      );
      if (!gatewaySync.ok) {
        ctx.gatewayStop = gatewaySync.reason;
        return null;
      }
      ctx.used.model_requests = gatewaySync.used;
      const receiptStop = await applyBudgetReceipts(ports, ctx.used);
      if (receiptStop !== null) {
        ctx.gatewayStop = receiptStop;
        return null;
      }
      ctx.gatewayStop = null;
    }
    if (spendsModelRequest && budgetExhausted(remaining())) {
      return null;
    }
    addPhase(ctx.phases, phase, roundForCall, detail);
    ctx.modelInvocations += 1;
    if (spendsModelRequest) {
      ctx.used.model_requests += 1;
    }
    const produced = await body();
    if (ports.modelGateway !== undefined || input.require_model_gateway === true) {
      // A completed phase is reflected by its platform receipt immediately;
      // the reported `model_requests_used` therefore never lags a real call.
      const postCall = await syncModelRequests(
        ports,
        ctx.used.model_requests,
        input.require_model_gateway === true,
      );
      if (!postCall.ok) {
        ctx.gatewayStop = postCall.reason;
      } else {
        ctx.used.model_requests = postCall.used;
      }
    }
    return produced;
  };

  // Understand.
  const interpretation = await callModel(
    "understand",
    0,
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
    return finish(ctx, ctx.gatewayStop ?? "budget_cap", 0);
  }
  if (interpretation.intent === "reject") {
    return finish(ctx, "intent_reject", 0);
  }
  if (interpretation.intent === "clarify") {
    return finish(ctx, "intent_clarify", 0);
  }

  let round = 0;
  while (true) {
    const roundSync = await syncModelRequests(
      ports,
      ctx.used.model_requests,
      input.require_model_gateway === true,
    );
    if (!roundSync.ok) {
      return finish(ctx, roundSync.reason, round);
    }
    ctx.used.model_requests = roundSync.used;
    const roundReceipts = await applyBudgetReceipts(ports, ctx.used);
    if (roundReceipts !== null) {
      return finish(ctx, roundReceipts, round);
    }
    if (round >= input.limits.max_rounds) {
      return finish(ctx, "round_cap", round);
    }
    if (budgetExhausted(remaining())) {
      return finish(ctx, "budget_cap", round);
    }
    if (
      remaining().task_refs <= 0 ||
      remaining().qc_invocations <= 0 ||
      remaining().recipe_steps <= 0
    ) {
      return finish(ctx, "budget_cap", round);
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
          evidence: ctx.lastReport,
          prior_hypotheses: ctx.reports.map((report) => report.report_id),
        }),
    );
    if (hypothesis === null) {
      return finish(ctx, ctx.gatewayStop ?? "budget_cap", round);
    }
    if (hypothesis.hypothesis === null) {
      return finish(ctx, "no_hypothesis", round);
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
          evidence: ctx.lastReport,
        }),
    );
    if (critique === null) {
      return finish(ctx, ctx.gatewayStop ?? "budget_cap", round);
    }
    if (critique.verdict === "stop") {
      return finish(ctx, "critique_stop", round);
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
          evidence: ctx.lastReport,
          remaining_budget: remaining(),
        }),
    );
    if (plan === null) {
      return finish(ctx, ctx.gatewayStop ?? "budget_cap", round);
    }
    if (submitBudgetExceeded(remaining(), plan)) {
      return finish(ctx, "budget_cap", round);
    }

    // Approve (preflight + platform admission + approval; all BEFORE submit).
    addPhase(ctx.phases, "approve", round, "scientific preflight and platform admission");
    const preflight = await ports.preflight({
      project_id: input.project_id,
      round,
      plan,
    });
    if (!preflight.ok) {
      return finish(ctx, "preflight_failed", round);
    }
    const admission = await ports.admission({
      project_id: input.project_id,
      round,
      plan_definition_digest: plan.plan_definition_digest,
      budget: plan.budget,
    });
    if (!admission.ok) {
      return finish(ctx, "admission_denied", round);
    }
    const approval = await ports.approve({
      project_id: input.project_id,
      round,
      approval_ref: admission.approval_ref,
      plan_definition_digest: plan.plan_definition_digest,
      budget: plan.budget,
    });
    if (!approval.approved || approval.approval_ref === null) {
      return finish(ctx, "approval_rejected", round);
    }
    ctx.approvalRef = approval.approval_ref;

    // Submit: the intent row exists before the call; the TaskRef is bound to
    // the research round afterwards (M3-04 / M1-02 / M3-01).
    const submitted = await submitPhase(ctx, round, plan, approval.approval_ref);
    if ("done" in submitted) {
      return submitted.done;
    }
    const { bound, legacy } = submitted.value;

    // Wait: bounded loop, opaque cursor, NO skill/model invocation.
    ctx.lastCursor = null;
    const waited = await waitPhase(
      ctx,
      round,
      plan.plan_definition_digest,
      bound ?? legacyBoundRef(ctx, legacy),
      ctx.lastCursor,
    );
    if ("done" in waited) {
      return waited.done;
    }
    const terminals = terminalEventsOf(waited.value.events);
    if (terminals.length === 0) {
      return finish(ctx, "wait_timeout", round);
    }

    const bindingMode =
      input.require_evidence_binding === true || ports.evidence !== undefined;
    let report: EvidenceReport | null = null;
    let view: EvidenceView | null = null;

    if (bindingMode) {
      const boundPhase = await bindEvidence(
        ctx,
        round,
        bound ?? legacyBoundRef(ctx, legacy),
        terminals,
        waited.value.observedCursor,
      );
      if ("done" in boundPhase) {
        return boundPhase.done;
      }
      view = boundPhase.value.view;
      ctx.bindings.push(boundPhase.value.binding);
      // A fixture event may also carry the legacy report body; it is only an
      // input to the *advice* call, never the verdict.
      report = validateReportOrNull(
        terminals.find((event) => event.report !== null)?.report ?? null,
      ).report;
    } else {
      const rawReport =
        terminals.find((event) => event.report !== null)?.report ?? null;
      const validated = validateReportOrNull(rawReport);
      if (validated.reason !== null) {
        return finish(ctx, "invalid_report", round);
      }
      report = validated.report;
      if (report === null) {
        return finish(ctx, "no_evidence", round);
      }
    }

    if (report !== null) {
      if (ports.persistReport !== undefined) {
        try {
          await ports.persistReport(report);
        } catch {
          return finish(ctx, "persist_failed", round);
        }
      }
      ctx.reports.push(report);
      ctx.lastReport = report;
    }

    // Evaluate: the projection decides; the model only advises.
    const advice = await callModel(
      "evaluate",
      round,
      view === null
        ? "advise on the real EvidenceReport (advice only)"
        : `advise only; the verdict is projection ${view.projection_digest}`,
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
          prior_hypotheses: ctx.reports.map((item) => item.report_id),
        }),
    );
    if (advice === null) {
      return finish(ctx, ctx.gatewayStop ?? "budget_cap", round);
    }
    ctx.lastAdvice = advice.advice;

    if (view !== null) {
      const decision = decideFromView(view);
      const evidenceCheckpoint = await writeCheckpoint(
        ctx,
        "evidence_bound",
        round,
        plan.plan_definition_digest,
        "decision_pending",
        null,
        view.projection_digest,
      );
      if (evidenceCheckpoint !== null) {
        return evidenceCheckpoint;
      }
      const pendingCheckpoint = await writeCheckpoint(
        ctx,
        "decision_pending",
        round,
        plan.plan_definition_digest,
        "decision_pending",
        decision,
        view.projection_digest,
      );
      if (pendingCheckpoint !== null) {
        return pendingCheckpoint;
      }
      const persisted = await persistDecision(ctx, view, decision, round);
      if ("done" in persisted) {
        return persisted.done;
      }
      if (decision === "accept") {
        return finish(ctx, "accepted", round + 1);
      }
      if (decision !== "revise") {
        return finish(ctx, decisionReason(decision), round);
      }
      addPhase(ctx.phases, "revise", round, "report requested another round");
      round += 1;
      continue;
    }

    const legacyDecision = decideFromReport(report as EvidenceReport);
    if (legacyDecision.action === "accept") {
      return finish(ctx, "accepted", round + 1);
    }
    if (legacyDecision.action === "stop") {
      return finish(ctx, legacyDecision.reason, round);
    }

    // Revise: only a report decision of `revise` reaches this point.
    addPhase(ctx.phases, "revise", round, "report requested another round");
    round += 1;
  }
}

/**
 * Legacy fixture reference.
 *
 * It has no release digest and is never a binding reference: the binding path
 * refuses a run without `bound_ref`, so this value only keeps the legacy wait
 * signature total.
 */
function legacyBoundRef(ctx: RunContext, legacy: TaskRef): ResearchTaskRef {
  return parsePlatformTaskRef(
    {
      project_id: legacy.project_id,
      task_id: legacy.job_id,
      job_id: legacy.job_id,
      attempt: legacy.attempt,
      release: {
        release_id: legacy.release_id,
        release_digest: `sha256:${"0".repeat(64)}`,
      },
    },
    ctx.input.project_id,
  );
}

function critiqueFindings(critique: ReviewerOutput): readonly string[] {
  return critique.findings.map(
    (finding: ReviewerFinding) => `${finding.code}:${finding.severity}`,
  );
}

/** Type-only convenience re-export for consumers that build plans. */
export type { PlanScience };
