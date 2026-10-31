/**
 * Platform-backed supervisor ports (M3-02a seam, M3-04 recovery wiring).
 *
 * The composition root in `compose.ts` builds the Supervisor's ports from
 * **injected** facts only. This module is an *adapter*: it never re-implements
 * the frozen Supervisor semantics (`skills/supervisor.ts`
 * `SUPERVISOR_RECOVERY_SCHEMA` / `EvidenceBinding` / `ResearchPortError`), it
 * only turns platform facts into the port shapes those contracts expect.
 *
 * | port | source | wired here |
 * | --- | --- | --- |
 * | `submitIntent` | M1-02 ledger via `createSubmitIntentPort` | always (the session's bridge ledger) |
 * | `submit` | bridge `submit` (P1) | when a plan→payload mapper is injected |
 * | `approve` | P2 `acp_research_approval_query_v1` | always |
 * | `evidence` | the single `acp2.runtime.evidence-projection.v1` | when a reader is injected |
 * | `resume` | P2-R5 `acp_research_resume_v1` | when the proposal ref is known |
 * | `persistDecision` | research writer CAS | when the writer implements it |
 * | `persistCheckpoint` | research writer fenced checkpoint | when the writer implements it |
 * | `budgetUsage` | platform saga/receipt counters | when a reader is injected |
 * | `preflight`/`admission`/`wait` | platform adapters | injected |
 *
 * Two rules are structural rather than documented:
 *
 * 1. **The submit key is never re-derived.** `bridgeSubmitRequest` forwards
 *    `SubmitRequest.idempotency_key` verbatim and deliberately omits the
 *    `research`/`round` identity, so the bridge's legacy self-derived key path
 *    (`resolveSubmitIntentIdentity`) cannot resurrect: a missing key is a typed
 *    refusal, not a new key.
 * 2. **No port invents a fact.** A missing reader/writer leaves the port
 *    absent (the Supervisor fails closed on its own contract); a transport
 *    failure becomes a typed `ResearchPortError`, never an empty success.
 *
 * Nothing in this module holds a transport: the session owns it.
 */

import {
  BridgeContractError,
  callP2Operation,
  createSubmitIntentPort,
  type Bridge,
  type BridgeRequest,
  type DecisionRecord,
  type EvidenceView,
  type PlatformClient,
  type TaskRef as BoundTaskRef,
} from "@rph/bridge";

import type { EvidenceReport, TaskRef } from "../skills/models.js";
import {
  ResearchPortError,
  type AdmissionRequest,
  type AdmissionResult,
  type ApprovalRequest,
  type ApprovalResult,
  type BudgetUsageReceipt,
  type CheckpointResult,
  type DecisionPersistResult,
  type EvidenceRequest,
  type EvidenceResult,
  type ModelGatewayUsage,
  type PreflightRequest,
  type PreflightResult,
  type ResumeRequest,
  type ResumeResult,
  type SubmitRequest,
  type SubmitResult,
  type SubmitIntentPort,
  type SupervisorCheckpoint,
  type SupervisorPorts,
  type WaitRequest,
  type WaitResult,
} from "../skills/supervisor.js";
import type { PlatformSession } from "./platform-session.js";

/** Writer kinds that are fixtures: a product run refuses them. */
export const FIXTURE_WRITER_KINDS: readonly string[] = Object.freeze([
  "memory-fixture",
]);

/**
 * The durable research writer port (records, evidence and the decision chain).
 *
 * `persistDecision` and `persistCheckpoint` are optional because a writer may
 * only implement record persistence; when they are absent the corresponding
 * Supervisor port is absent too, and a product composition refuses to start
 * (fail-fast `evidence_binding`). The implementation is injected — the
 * store-backed writer belongs to the M3-04 recovery batch.
 */
export interface ResearchWriterPort {
  /** Backend name reported by the doctor and checked by the composition. */
  readonly kind: string;
  persistTaskRef(ref: TaskRef): Promise<void>;
  persistReport(report: EvidenceReport): Promise<void>;
  /** CAS write of a decision record (the writer's own result, never a verdict). */
  persistDecision?(record: DecisionRecord): Promise<DecisionPersistResult>;
  /** Attempt-fenced intermediate checkpoint (client side of `FencedCommitPort`). */
  persistCheckpoint?(checkpoint: SupervisorCheckpoint): Promise<CheckpointResult>;
}

/** A typed composition/port failure (never a scientific result). */
export class PlatformPortError extends Error {
  readonly code: string;
  readonly params: Readonly<Record<string, unknown>>;

  constructor(code: string, message: string, params: Readonly<Record<string, unknown>> = {}) {
    super(`${code}: ${message}`);
    this.name = "PlatformPortError";
    this.code = code;
    this.params = Object.freeze({ ...params });
  }
}

/**
 * In-memory writer for the labelled demo and for tests.
 *
 * It is a fixture: `composeResearchRuntime` refuses it in `product`/`acceptance`
 * mode, so it can never be mistaken for a durable research record.
 */
export interface MemoryResearchWriter extends ResearchWriterPort {
  readonly kind: "memory-fixture";
  taskRefs(): readonly TaskRef[];
  reports(): readonly EvidenceReport[];
  decisions(): readonly DecisionRecord[];
  checkpoints(): readonly SupervisorCheckpoint[];
}

export function createMemoryResearchWriter(): MemoryResearchWriter {
  const taskRefs: TaskRef[] = [];
  const reports: EvidenceReport[] = [];
  const decisions: DecisionRecord[] = [];
  const checkpoints: SupervisorCheckpoint[] = [];
  return Object.freeze({
    kind: "memory-fixture" as const,
    async persistTaskRef(ref: TaskRef): Promise<void> {
      taskRefs.push(ref);
    },
    async persistReport(report: EvidenceReport): Promise<void> {
      reports.push(report);
    },
    async persistDecision(record: DecisionRecord): Promise<DecisionPersistResult> {
      if (decisions.some((item) => item.projection_digest === record.projection_digest)) {
        return Object.freeze({ ok: true, replayed: true, revision: decisions.length });
      }
      decisions.push(record);
      return Object.freeze({ ok: true, replayed: false, revision: decisions.length });
    },
    async persistCheckpoint(checkpoint: SupervisorCheckpoint): Promise<CheckpointResult> {
      checkpoints.push(checkpoint);
      return Object.freeze({ ok: true, replayed: false });
    },
    taskRefs: () => Object.freeze([...taskRefs]),
    reports: () => Object.freeze([...reports]),
    decisions: () => Object.freeze([...decisions]),
    checkpoints: () => Object.freeze([...checkpoints]),
  });
}

/**
 * Ports the caller may inject (preflight/admission/wait and the plan→payload
 * mapping). None of them is re-implemented here: duplicating the submit-intent
 * ledger, the event window or the evidence binding would create a second
 * research state machine.
 */
export interface PlatformPortAdapters {
  readonly preflight?: (request: PreflightRequest) => Promise<PreflightResult>;
  readonly admission?: (request: AdmissionRequest) => Promise<AdmissionResult>;
  /** An explicit submit adapter wins over the bridge-backed one. */
  readonly submit?: (request: SubmitRequest) => Promise<SubmitResult>;
  readonly wait?: (request: WaitRequest) => Promise<WaitResult>;
  readonly modelGateway?: () => Promise<ModelGatewayUsage>;
  readonly resume?: SupervisorPorts["resume"];
}

/** One raw projection read (`GET /research/records/{id}/evidence` shape). */
export interface EvidenceReadResult {
  /** The `acp2.runtime.evidence-projection.v1` payload, unmodified. */
  readonly view: unknown;
  readonly read_at?: string | null;
}

/**
 * Reader of the single platform evidence projection.
 *
 * The platform exposes it as a research-record route; the transport belongs to
 * the caller (the RPH boundary forbids direct HTTP), so the reader is injected
 * and this module only adapts the answer.
 */
export interface EvidenceReaderPort {
  read(request: EvidenceRequest): Promise<EvidenceReadResult>;
}

/** Reader of the platform's authoritative budget counters (saga/receipts). */
export interface BudgetUsageReaderPort {
  read(): Promise<BudgetUsageReceipt>;
}

/** The plan → platform submit payload mapping (scientific, caller-owned). */
export interface PlatformSubmitPayload {
  readonly workflow: string;
  readonly input: unknown;
  readonly method: unknown;
  readonly resources: unknown;
  readonly budget: unknown;
  readonly proposal_ref: string;
  readonly proposal_digest: string;
  readonly plan_digest?: string;
}

export type SubmitPayloadMapper = (request: SubmitRequest) => PlatformSubmitPayload;

export interface PlatformPortInput {
  readonly session: PlatformSession;
  /** Optional project scope for the P2 queries (approvals are project-scoped). */
  readonly projectId?: string | null;
  readonly writer: ResearchWriterPort;
  readonly adapters?: PlatformPortAdapters;
  /** The single evidence projection (absent = no binding port). */
  readonly evidenceReader?: EvidenceReaderPort;
  /** Platform saga/receipt budget counters. */
  readonly budgetUsage?: BudgetUsageReaderPort;
  /** The proposal whose saga a resume advances (P2-R5 needs it). */
  readonly proposalRef?: string;
  /** Enables the real bridge-backed submit port. */
  readonly submitPayload?: SubmitPayloadMapper;
  /** Override the M1-02 ledger port (default: the session bridge's ledger). */
  readonly submitIntent?: SubmitIntentPort;
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function unwired(port: string): PlatformPortError {
  return new PlatformPortError(
    `${port}_adapter_missing`,
    `the ${port} port has no platform adapter; the composition is incomplete`,
    { port },
  );
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// ---------------------------------------------------------------------------
// Submit intent (M1-02) and the bridge-backed submit (P1)
// ---------------------------------------------------------------------------

/**
 * Build one bridge submit request from the Supervisor's frozen submit request.
 *
 * The idempotency key is the **persisted M1-02 wire key** and is forwarded
 * verbatim. `research`/`round` are deliberately omitted: they are the fields
 * the bridge would use to derive its own key (the legacy behaviour M0-05
 * removed from the product path), so a product submit can only ever send the
 * key the ledger already persisted.
 *
 * @throws PlatformPortError when the key is missing/empty.
 */
export function bridgeSubmitRequest(
  request: SubmitRequest,
  payload: PlatformSubmitPayload,
): BridgeRequest {
  const key = request.idempotency_key;
  if (typeof key !== "string" || key.length === 0) {
    throw new PlatformPortError(
      "submit_key_missing",
      "a product submit requires the persisted submit-intent wire key; the bridge never derives one",
      { round: request.round },
    );
  }
  if (
    typeof request.submit_intent_key !== "string" ||
    request.submit_intent_key.length === 0
  ) {
    throw new PlatformPortError(
      "submit_intent_missing",
      "a product submit requires its durable submit-intent key (audit binding)",
      { round: request.round },
    );
  }
  return Object.freeze({
    operation: "submit",
    project: request.project_id,
    workflow: payload.workflow,
    input: payload.input,
    method: payload.method,
    resources: payload.resources,
    budget: payload.budget,
    planDigest: payload.plan_digest ?? request.plan_definition_digest,
    proposalRef: payload.proposal_ref,
    proposalDigest: payload.proposal_digest,
    idempotencyKey: key,
  });
}

function legacyTaskRef(ref: BoundTaskRef): TaskRef {
  return Object.freeze({
    project_id: ref.project_id,
    job_id: ref.task_id,
    attempt: ref.attempt,
    release_id: ref.release.release_id,
  });
}

export interface BridgeSubmitPortInput {
  readonly bridge: Bridge;
  readonly payload: SubmitPayloadMapper;
}

/**
 * The real P1 submit port: one bridge call, the full ADR-003 reference kept.
 *
 * A bridge refusal is a typed `ok: false` (the Supervisor stops on
 * `submit_failed`), never a fabricated TaskRef; an incomplete reference keeps
 * its reconciliation marker in `reason` so the caller can see why.
 */
export function createBridgeSubmitPort(
  input: BridgeSubmitPortInput,
): NonNullable<SupervisorPorts["submit"]> {
  return async (request: SubmitRequest): Promise<SubmitResult> => {
    const bridgeRequest = bridgeSubmitRequest(request, input.payload(request));
    let result: Awaited<ReturnType<Bridge["execute"]>>;
    try {
      result = await input.bridge.execute(bridgeRequest);
    } catch (error) {
      if (error instanceof BridgeContractError) {
        return Object.freeze({
          ok: false,
          task_ref: null,
          bound_ref: null,
          reason: `bridge_refused:${error.error.code}`,
        });
      }
      throw error;
    }
    if (result.isError) {
      const error = result.structuredContent["error"];
      const code = isRecord(error) && typeof error["code"] === "string" ? error["code"] : "submit_error";
      return Object.freeze({
        ok: false,
        task_ref: null,
        bound_ref: null,
        reason: `platform_error:${code}`,
      });
    }
    const bound = result.taskRef;
    if (bound === null) {
      return Object.freeze({
        ok: false,
        task_ref: null,
        bound_ref: null,
        reason: `task_ref_incomplete:${result.taskRefReconciliation}`,
      });
    }
    return Object.freeze({
      ok: true,
      task_ref: legacyTaskRef(bound),
      bound_ref: bound,
      reason: "submitted",
    });
  };
}

// ---------------------------------------------------------------------------
// Evidence (M1-03 single projection), decision CAS, checkpoints, budget, resume
// ---------------------------------------------------------------------------

/**
 * Adapt the platform evidence reader into the Supervisor port.
 *
 * The payload is passed through **unmodified**: the Supervisor attributes it
 * through the bridge's single `attributeEvidenceView` (foreign project/research/
 * round and identity mismatches are its typed verdicts), so validating or
 * rewriting it here would be a second evidence model. Only transport failures
 * become `ResearchPortError("evidence_unavailable")`.
 */
export function createEvidencePort(
  reader: EvidenceReaderPort,
): NonNullable<SupervisorPorts["evidence"]> {
  return async (request: EvidenceRequest): Promise<EvidenceResult> => {
    let read: EvidenceReadResult;
    try {
      read = await reader.read(request);
    } catch (error) {
      if (error instanceof ResearchPortError) throw error;
      throw new ResearchPortError(
        "evidence_unavailable",
        `the evidence projection could not be read: ${messageOf(error)}`,
        {
          project_id: request.project_id,
          research_id: request.research_id,
          round: request.round,
          task_id: request.task_ref.task_id,
          attempt: request.task_ref.attempt,
        },
      );
    }
    return Object.freeze({
      view: read.view as EvidenceView,
      read_at: read.read_at ?? null,
    });
  };
}

/** Adapt the platform budget reader (`saga-record` / `platform-receipt`). */
export function createBudgetUsagePort(
  reader: BudgetUsageReaderPort,
): NonNullable<SupervisorPorts["budgetUsage"]> {
  return async (): Promise<BudgetUsageReceipt> => {
    try {
      return await reader.read();
    } catch (error) {
      if (error instanceof ResearchPortError) throw error;
      // The Supervisor maps any failure here to `budget_receipt_mismatch`
      // (fail closed); the message keeps the real cause diagnosable.
      throw new PlatformPortError(
        "budget_usage_unavailable",
        `the platform budget counters could not be read: ${messageOf(error)}`,
      );
    }
  };
}

/** Adapt the writer's decision CAS. */
export function createDecisionPort(
  writer: ResearchWriterPort,
  persist: NonNullable<ResearchWriterPort["persistDecision"]>,
): NonNullable<SupervisorPorts["persistDecision"]> {
  return async (record: DecisionRecord): Promise<DecisionPersistResult> => {
    try {
      return await persist.call(writer, record);
    } catch (error) {
      if (error instanceof ResearchPortError) throw error;
      // A writer failure is never `accepted`: report it as a refused CAS.
      return Object.freeze({ ok: false, reason: `writer_error:${messageOf(error)}` });
    }
  };
}

/** Adapt the writer's attempt-fenced checkpoint. */
export function createCheckpointPort(
  writer: ResearchWriterPort,
  persist: NonNullable<ResearchWriterPort["persistCheckpoint"]>,
): NonNullable<SupervisorPorts["persistCheckpoint"]> {
  return async (checkpoint: SupervisorCheckpoint): Promise<CheckpointResult> => {
    try {
      return await persist.call(writer, checkpoint);
    } catch (error) {
      if (error instanceof ResearchPortError) throw error;
      return Object.freeze({ ok: false, reason: `writer_error:${messageOf(error)}` });
    }
  };
}

/**
 * Map a refused `research.resume` onto the frozen port rules.
 *
 * Only the platform's own cursor/authorization vocabulary is translated; an
 * unclassifiable refusal becomes `stale_revision`, which stops the run as
 * `wait_failed` without adopting anything (never a fabricated progress).
 */
function researchPortRuleFor(outcome: ResumeOutcome): ResearchPortRuleLike {
  const envelope = outcome.payload;
  const error = isRecord(envelope["error"]) ? envelope["error"] : null;
  const params = error !== null && isRecord(error["params"]) ? error["params"] : null;
  const rule = params === null ? undefined : params["rule"];
  const code = error !== null && typeof error["code"] === "string" ? error["code"] : outcome.code;
  if (rule === "cursor_expired" || code === "cursor_expired") return "cursor_expired";
  if (rule === "cursor_project_mismatch") return "cursor_project_mismatch";
  if (rule === "cursor_invalid") return "cursor_invalid";
  if (rule === "fenced" || code === "fenced") return "fenced";
  return "stale_revision";
}

type ResearchPortRuleLike =
  | "cursor_expired"
  | "cursor_invalid"
  | "cursor_project_mismatch"
  | "fenced"
  | "stale_revision"
  | "evidence_unavailable";

/**
 * Map the P2-R5 answer onto the Supervisor's frozen `ResumeResult`.
 *
 * The platform does not issue a new *wait* cursor from `resume`, and its saga
 * header carries the terminal flag — so the caller's handle cursor stays the
 * anchor while the run is not terminal, exactly as the Supervisor documents.
 */
export function mapResumePayload(
  request: ResumeRequest,
  payload: Readonly<Record<string, unknown>>,
): ResumeResult {
  const data = isRecord(payload["data"]) ? payload["data"] : null;
  if (data === null) {
    throw new ResearchPortError("stale_revision", "research.resume returned no data object", {
      round: request.round,
    });
  }
  const saga = isRecord(data["saga"]) ? data["saga"] : null;
  const terminal = saga !== null && saga["terminal"] === true;
  const state = saga !== null && typeof saga["state"] === "string" ? saga["state"] : null;
  return Object.freeze({
    advanced:
      data["resumed_from"] === "saga" &&
      request.diagnostic_only === false &&
      !terminal,
    cursor: request.cursor,
    terminal_state: terminal ? state : null,
  });
}

export interface ResumePortInput {
  readonly session: PlatformSession;
  /** The proposal the saga was opened for (P2-R5 identity). */
  readonly proposalRef: string;
}

/** Adapt P2-R5 `acp_research_resume_v1` into the Supervisor resume port. */
export function createResumePort(input: ResumePortInput): NonNullable<SupervisorPorts["resume"]> {
  return async (request: ResumeRequest): Promise<ResumeResult> => {
    const outcome = await resumeResearch(input.session, {
      proposal_ref: input.proposalRef,
      plan_digest: request.plan_definition_digest,
      task_ref: request.task_ref.task_id,
      ...(request.cursor === null ? {} : { cursor: request.cursor }),
      diagnostic_only: request.diagnostic_only,
    });
    if (!outcome.ok) {
      throw new ResearchPortError(
        researchPortRuleFor(outcome),
        `research.resume was refused (${outcome.code})`,
        {
          project_id: request.project_id,
          research_id: request.research_id,
          round: request.round,
          code: outcome.code,
        },
      );
    }
    return mapResumePayload(request, outcome.payload);
  };
}

// ---------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------

/**
 * Build the Supervisor ports for one real platform session.
 *
 * The approval port is a real P2 read; `submitIntent` is the session bridge's
 * M1-02 ledger; the six recovery ports are installed only when their fact
 * source exists. A missing *required* port is a typed error, never a
 * fabricated approval/plan/task/event/decision.
 */
export function createPlatformPorts(input: PlatformPortInput): SupervisorPorts {
  const adapters = input.adapters ?? {};
  const client: PlatformClient = input.session.client;
  const negotiated = input.session.negotiatedExtensions;

  const ports: {
    -readonly [K in keyof SupervisorPorts]: SupervisorPorts[K];
  } = {
    async preflight(request: PreflightRequest): Promise<PreflightResult> {
      const adapter = adapters.preflight;
      if (adapter === undefined) throw unwired("preflight");
      return adapter(request);
    },
    async admission(request: AdmissionRequest): Promise<AdmissionResult> {
      const adapter = adapters.admission;
      if (adapter === undefined) throw unwired("admission");
      return adapter(request);
    },
    async approve(request: ApprovalRequest): Promise<ApprovalResult> {
      if (negotiated.length === 0) {
        return Object.freeze({
          approved: false,
          approval_ref: null,
          reason: "research_extension_not_negotiated",
        });
      }
      try {
        const scope = input.projectId === null || input.projectId === undefined ? {} : { project_id: input.projectId };
        const result = await callP2Operation(
          client,
          "approval.query",
          request.approval_ref === null ? scope : { approval_ref: request.approval_ref, ...scope },
          negotiated,
        );
        if (result.isError) {
          return Object.freeze({
            approved: false,
            approval_ref: null,
            reason: "approval_query_error",
          });
        }
        return approvalDecision(request, parseApprovals(result.structuredContent));
      } catch (error) {
        if (error instanceof BridgeContractError) {
          return Object.freeze({
            approved: false,
            approval_ref: null,
            reason: `approval_query_refused:${error.error.code}`,
          });
        }
        if (error instanceof PlatformPortError) throw error;
        throw new PlatformPortError("approval_query_failed", messageOf(error));
      }
    },
    async submit(request: SubmitRequest): Promise<SubmitResult> {
      if (adapters.submit !== undefined) {
        return adapters.submit(request);
      }
      if (input.submitPayload !== undefined) {
        return createBridgeSubmitPort({
          bridge: input.session.bridge,
          payload: input.submitPayload,
        })(request);
      }
      throw unwired("submit");
    },
    async wait(request: WaitRequest): Promise<WaitResult> {
      const adapter = adapters.wait;
      if (adapter === undefined) throw unwired("wait");
      return adapter(request);
    },
    async persistTaskRef(ref: TaskRef): Promise<void> {
      await input.writer.persistTaskRef(ref);
    },
    async persistReport(report: EvidenceReport): Promise<void> {
      await input.writer.persistReport(report);
    },
  };

  // M1-02: the ledger is the session bridge's own, unless a caller injects one
  // (an acceptance harness may share a durable ledger across processes).
  ports.submitIntent =
    input.submitIntent ?? createSubmitIntentPort(input.session.bridge.submitIntents);

  if (input.evidenceReader !== undefined) {
    ports.evidence = createEvidencePort(input.evidenceReader);
  }
  if (input.budgetUsage !== undefined) {
    ports.budgetUsage = createBudgetUsagePort(input.budgetUsage);
  }
  const persistDecision = input.writer.persistDecision;
  if (persistDecision !== undefined) {
    ports.persistDecision = createDecisionPort(input.writer, persistDecision);
  }
  const persistCheckpoint = input.writer.persistCheckpoint;
  if (persistCheckpoint !== undefined) {
    ports.persistCheckpoint = createCheckpointPort(input.writer, persistCheckpoint);
  }
  if (adapters.resume !== undefined) {
    ports.resume = adapters.resume;
  } else if (input.proposalRef !== undefined && input.proposalRef.length > 0) {
    ports.resume = createResumePort({
      session: input.session,
      proposalRef: input.proposalRef,
    });
  }
  if (adapters.modelGateway !== undefined) {
    ports.modelGateway = adapters.modelGateway;
  }
  return Object.freeze(ports) as SupervisorPorts;
}

// The approval mapping helpers live below the assembly so the file reads
// top-down; they are pure and never touch a transport.

interface ApprovalFacts {
  readonly ref: string | null;
  readonly status: string | null;
  readonly planDigest: string | null;
}

/** Read `data.approvals[]` defensively; never invent a status. */
function parseApprovals(payload: Readonly<Record<string, unknown>>): readonly ApprovalFacts[] {
  const data = payload["data"];
  const raw = isRecord(data) ? data["approvals"] : undefined;
  if (!Array.isArray(raw)) {
    throw new PlatformPortError(
      "approval_query_unmapped",
      "approval.query returned no approvals[] array",
      { keys: Object.keys(payload) },
    );
  }
  return raw.map((entry) => {
    if (!isRecord(entry)) {
      throw new PlatformPortError("approval_query_unmapped", "an approval entry is not an object");
    }
    const ref = entry["approval_ref"] ?? entry["ref"] ?? entry["id"];
    const status = entry["status"];
    const planDigest = entry["science_digest"] ?? entry["plan_digest"];
    return Object.freeze({
      ref: typeof ref === "string" ? ref : null,
      status: typeof status === "string" ? status : null,
      planDigest: typeof planDigest === "string" ? planDigest : null,
    });
  });
}

function approvalDecision(
  request: ApprovalRequest,
  facts: readonly ApprovalFacts[],
): ApprovalResult {
  if (request.approval_ref === null) {
    return Object.freeze({
      approved: false,
      approval_ref: null,
      reason: "approval_ref_missing",
    });
  }
  const match = facts.find((entry) => entry.ref === request.approval_ref);
  if (match === undefined) {
    return Object.freeze({
      approved: false,
      approval_ref: null,
      reason: "approval_not_found",
    });
  }
  if (match.status !== "approved") {
    return Object.freeze({
      approved: false,
      approval_ref: null,
      reason: `approval_status:${String(match.status)}`,
    });
  }
  if (match.planDigest !== null && match.planDigest !== request.plan_definition_digest) {
    return Object.freeze({
      approved: false,
      approval_ref: null,
      reason: "approval_plan_digest_mismatch",
    });
  }
  return Object.freeze({
    approved: true,
    approval_ref: request.approval_ref,
    reason: "approved",
  });
}

// ---------------------------------------------------------------------------
// The raw P2-R5 call (also used by the `resume` command)
// ---------------------------------------------------------------------------

/** Resume arguments accepted by the frozen `acp.research.resume` operation. */
export interface ResearchResumeRequest {
  readonly proposal_ref: string;
  readonly plan_digest: string;
  readonly task_ref?: string;
  readonly cursor?: string;
  readonly diagnostic_only?: boolean;
}

export interface ResumeOutcome {
  readonly ok: boolean;
  readonly code: string;
  /** The whole MCP envelope (`data` carries the R5 answer). */
  readonly payload: Readonly<Record<string, unknown>>;
}

/**
 * Ask the platform to resume one research saga (`acp.research.resume`).
 *
 * Read-only from the RPH side: the platform owns the saga identity derived from
 * `(project, proposal_ref, plan_digest)`, so a resume in a new process reaches
 * the same attempt. A missing extension or an invalid argument is a typed
 * outcome, never a locally invented resume.
 */
export async function resumeResearch(
  session: PlatformSession,
  request: ResearchResumeRequest,
): Promise<ResumeOutcome> {
  if (request.proposal_ref.length === 0 || request.plan_digest.length === 0) {
    return Object.freeze({
      ok: false,
      code: "resume_arguments_missing",
      payload: Object.freeze({}),
    });
  }
  const args: Record<string, unknown> = {
    proposal_ref: request.proposal_ref,
    plan_digest: request.plan_digest,
  };
  if (request.task_ref !== undefined) args["task_ref"] = request.task_ref;
  if (request.cursor !== undefined) args["cursor"] = request.cursor;
  if (request.diagnostic_only !== undefined) args["diagnostic_only"] = request.diagnostic_only;
  try {
    const result = await callP2Operation(
      session.client,
      "research.resume",
      args,
      session.negotiatedExtensions,
    );
    return Object.freeze({
      ok: !result.isError,
      code: result.isError ? "platform_error" : "resumed",
      payload: result.structuredContent,
    });
  } catch (error) {
    if (error instanceof BridgeContractError) {
      return Object.freeze({
        ok: false,
        code: error.error.code,
        payload: Object.freeze({ params: error.error.params }),
      });
    }
    return Object.freeze({
      ok: false,
      code: "resume_failed",
      payload: Object.freeze({ message: messageOf(error) }),
    });
  }
}
