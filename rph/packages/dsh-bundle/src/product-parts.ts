/** Installed product composition. All platform access uses the negotiated session. */
import { createHash, randomUUID } from "node:crypto";
import type { DecisionRecord } from "@rph/bridge";
import { composeProductionSkills } from "../skills/composition.js";
import { createModelPlanner, type ModelPlanGatewayResponse } from "../skills/model-planner.js";
import type { PlanBudget, ScienceSeed } from "../skills/models.js";
import { validatePlannerOutput, type PlannerOutput } from "../skills/planner.js";
import type { ModelGatewayUsage, SupervisorCheckpoint, SupervisorInput } from "../skills/supervisor.js";
import { BridgeContractError, reconcileTaskRef } from "@rph/bridge";
import { PlatformPortError, bridgeSubmitRequest, type PlatformSubmitPayload, type ResearchWriterPort,
  type EvidenceReaderPort, type BudgetUsageReaderPort, type PlatformPortAdapters,
  type SubmitPayloadMapper } from "./research-ports.js";
import type { PlatformSession } from "./platform-session.js";
import type { ResolvedRuntimeProfile } from "./runtime-profile.js";

/** The capability step this deployment's confirmed workflow uses. */
function subjectCapability(config: ProductScienceConfig | null | undefined): string {
  return WORKFLOW_CAPABILITY[String(config?.submission.workflow)] ?? "electronic.energy";
}

export const PRODUCT_TOOL_NAMES = Object.freeze([
  "model_plan", "model_usage", "model_statement", "record_create", "record_read",
  "record_append", "evidence_read", "evidence_prepare", "budget_read", "readiness", "checkpoint",
].map((operation) => `acp_research_${operation}_v1`));

type Json = Readonly<Record<string, unknown>>;
function object(value: unknown, name: string): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new PlatformPortError("product_response_invalid", `${name} must be an object`);
  }
  return value as Json;
}
function text(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new PlatformPortError("science_mapping_missing", `${name} must be explicit`);
  }
  return value;
}
function count(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new PlatformPortError("product_response_invalid", `${name} must be a nonnegative integer`);
  }
  return value;
}

/** No caller can create a transport or assert a negotiated extension here. */
export async function callResearchConsumer(session: PlatformSession, operation: string, args: Json): Promise<Json> {
  const tool = `acp_research_${operation}_v1`;
  if (!session.researchEnabled || !session.toolNames.includes(tool)) {
    throw new PlatformPortError("research_consumer_unavailable", `${tool} requires a negotiated, observed tool`);
  }
  return callTool(session, tool, args);
}
async function callTool(session: PlatformSession, tool: string, args: Json): Promise<Json> {
  const result = await session.client.callTool(tool, args);
  const payload = object(result.structuredContent, tool);
  if (result.isError) {
    const error = object(payload["error"], "error");
    throw new PlatformPortError(String(error["code"] ?? "platform_error"), String(error["message"] ?? tool),
      typeof error["params"] === "object" && error["params"] !== null ? error["params"] as Json : {});
  }
  return object(payload["data"], `${tool}.data`);
}

export interface ProductScienceConfig {
  readonly science_seed: ScienceSeed;
  readonly initial_input: SupervisorInput["initial_input"];
  /** Explicit platform input descriptor, method options and resource limits. */
  readonly submission: {
    readonly workflow: string;
    readonly input: Json;
    readonly method: Json;
    readonly resources: Json;
    readonly budget: Json;
  };
  readonly planning_budget: PlanBudget;
}

export interface ProductPartsInput {
  readonly profile: ResolvedRuntimeProfile;
  readonly session: PlatformSession | null;
  readonly science?: ProductScienceConfig | null;
  readonly research?: string;
  readonly restore?: { readonly proposal: Json; readonly execution: Json; readonly plan: PlannerOutput; readonly revision: number };
}

const WORKFLOW_CAPABILITY: Readonly<Record<string, string>> = Object.freeze({
  singlepoint: "electronic.energy",
  xtb_optimize: "geometry.optimize",
});

/** Map only the supported product scenarios; never supply scientific defaults. */
export function singlepointPayload(plan: PlannerOutput, config: ProductScienceConfig): Omit<PlatformSubmitPayload, "proposal_ref" | "proposal_digest"> {
  const submission = config.submission;
  if (config.science_seed.mapping !== "identity") throw new PlatformPortError("unsupported_science_mapping", "first product mapper requires identity atom mapping");
  const step = plan.science.capability_steps.length === 1 ? plan.science.capability_steps[0] : undefined;
  if (submission === undefined || !["singlepoint", "xtb_optimize"].includes(String(submission.workflow)) ||
      step === undefined || step.capability !== WORKFLOW_CAPABILITY[String(submission.workflow)]) {
    throw new PlatformPortError("unsupported_science_mapping",
      `product submission requires singlepoint/electronic.energy or xtb_optimize/geometry.optimize, saw ${String(submission?.workflow)}`);
  }
  const workflow = String(submission.workflow);
  const method = { ...object(submission.method, "submission.method") };
  const resources = object(submission.resources, "submission.resources");
  const parameters = step.parameters;
  if (plan.science.initial_inputs.length !== 1 || config.science_seed.structure_provenance.length !== 1 ||
      plan.science.allowed_substitutions.length !== 0 || plan.science_inputs.constraints.length !== 0) {
    throw new PlatformPortError("unsupported_science_mapping", "first singlepoint scenario requires one input, no substitutions and no geometric constraints");
  }
  const state = plan.science_inputs.electronic_state;
  if (state !== null && state !== undefined && (state.state_label != null || state.reference_state != null)) {
    throw new PlatformPortError("unsupported_science_mapping", "labelled/reference electronic states need a dedicated mapping; confirm a basic charge/multiplicity seed");
  }
  const allowed = new Set(["ncores", "memory_gb", "method", "basis", "charge", "multiplicity"]);
  for (const [field, value] of Object.entries(parameters)) {
    if (!allowed.has(field) || (field !== "ncores" && field !== "memory_gb" && value !== method[field])) {
      throw new PlatformPortError("science_mapping_mismatch", `unmapped or changed singlepoint parameter: ${field}`);
    }
  }
  if (method["method"] !== config.science_seed.method || plan.science.method !== config.science_seed.method ||
      method["charge"] !== plan.science_inputs.charge || method["multiplicity"] !== plan.science_inputs.multiplicity) {
    throw new PlatformPortError("science_mapping_mismatch", "method, charge and multiplicity must match seed and plan");
  }
  if (!("basis" in method)) throw new PlatformPortError("science_mapping_missing", "method.basis must be explicit (null for a composite method)");
  if (method["basis"] === null) {
    // Explicit no-basis choice is only for composite methods: r2SCAN-3c for
    // the singlepoint scenario, and the GFN family for xtb_optimize (the xTB
    // methods carry no separate basis).
    const composite = workflow === "xtb_optimize"
      ? /^GFN[0-9]?-xTB$/i.test(String(method["method"]))
      : method["method"] === "r2SCAN-3c";
    if (!composite) throw new PlatformPortError("unsupported_science_mapping", "basis:null is supported only for a confirmed composite method (r2SCAN-3c singlepoint, GFN-xTB xtb_optimize)");
    // Canonical execution plans exclude null fields. This never supplies a
    // basis for another method.
    delete method["basis"];
  } else text(method["basis"], "method.basis");
  for (const field of ["ncores", "memory_gb"] as const) {
    if (count(resources[field], `resources.${field}`) < 1 || parameters[field] !== resources[field]) {
      throw new PlatformPortError("science_mapping_mismatch", `${field} must be positive and equal the model plan parameter`);
    }
  }
  const input = object(submission.input, "submission.input");
  if (input["input_id"] !== config.initial_input.input_id || input["sha256"] !== config.initial_input.sha256) {
    throw new PlatformPortError("science_mapping_mismatch", "input id/digest must equal the confirmed initial input");
  }
  const body = input["xyz_text"] ?? input["smiles"];
  if (typeof body !== "string" || `sha256:${createHash("sha256").update(body).digest("hex")}` !== config.initial_input.sha256) {
    throw new PlatformPortError("science_mapping_mismatch", "inline input bytes must match the confirmed sha256");
  }
  if (!config.science_seed.structure_provenance.some((ref) => ref.input_id === config.initial_input.input_id && ref.sha256 === config.initial_input.sha256)) {
    throw new PlatformPortError("science_mapping_mismatch", "initial input must be bound by seed provenance");
  }
  if (plan.budget.wall_seconds < 1 || plan.budget.qc_invocations !== 1 || plan.budget.task_refs !== 1 || plan.budget.recipe_steps !== 1) {
    throw new PlatformPortError("science_mapping_mismatch", "singlepoint requires one task/QC and a positive wall deadline");
  }
  const launchBudget = object(submission.budget, "submission.budget");
  if (launchBudget["unit"] !== "task_count" || launchBudget["count"] !== plan.budget.task_refs ||
      launchBudget["timeout_s"] !== plan.budget.wall_seconds) {
    throw new PlatformPortError("science_mapping_mismatch", "launch budget unit/count/timeout must equal the singlepoint plan");
  }
  // The platform submit contract carries the structure inline: the intake
  // validation requires a non-empty `xyz_text`, while the runner materializes
  // from `source` (v1_schemas JobInputSource / runner._extract_input_source).
  // Both fields therefore carry the same confirmed bytes; the input_id/sha256
  // provenance stays bound in the proposal document.
  const platformInput = input["xyz_text"] !== undefined
    ? { source_type: "xyz_text", source: text(input["xyz_text"], "input.xyz_text"),
        xyz_text: text(input["xyz_text"], "input.xyz_text") }
    : input["smiles"] !== undefined
      ? { source_type: "smiles", source: text(input["smiles"], "input.smiles") }
      : input;
  return { workflow: submission.workflow, input: platformInput, method, resources,
    budget: submission.budget };
}

/** Default parts are live adapters. A failed/missing session leaves readiness incomplete. */
export async function createProductParts(input: ProductPartsInput) {
  const session = input.session;
  if (session === null) return null;
  const missing = PRODUCT_TOOL_NAMES.filter((tool) => !session.toolNames.includes(tool));
  if (!session.researchEnabled || missing.length > 0) {
    throw new PlatformPortError("research_consumer_unavailable", `missing negotiated product tools: ${missing.join(", ")}`);
  }
  const seed = input.research ?? randomUUID();
  const project = input.profile.get("project_id");
  const scope = { session_seed: seed, ...(project === null ? {} : { project_id: project }) };
  const consume = (operation: string, args: Json = {}) => callResearchConsumer(session, operation, args);
  const readiness = await consume("readiness", project === null ? {} : { project_id: project });
  const facts = readiness["facts"];
  if (!Array.isArray(facts)) throw new PlatformPortError("controller_not_ready", "Controller readiness facts are missing");
  const required = ["model", "permissions", "software", "storage", "execution"];
  const unavailable = required.filter((id) => !facts.some((fact) => {
    const entry = object(fact, "readiness fact");
    return entry["id"] === id && (entry["status"] === "ok" || entry["status"] === "warn");
  }));
  if (unavailable.length > 0) {
    throw new PlatformPortError("controller_not_ready", `Controller prerequisites failed: ${unavailable.join(", ")}; run rph doctor --connect`);
  }
  const usage = () => consume("model_usage", scope);
  const initialUsage = await usage();
  const identity = object(initialUsage["identity"], "model usage identity");
  const researchId = text(identity["research_id"], "research_id");
  const proposalRef = `prop-${text(identity["session_id"], "session_id")}`;
  let proposal: Json | null = input.restore?.proposal ?? null;
  let execution: Json | null = input.restore?.execution ?? null;
  let currentPlan: PlannerOutput | null = input.restore?.plan ?? null;
  let view: Json | null = null;
  let reportRef: Json | null = null;
  let revision = input.restore?.revision ?? 0;
  let recordRevision: number | null = null;
  const config = input.science;
  if (config !== null && config !== undefined) {
    const budget = object(config.planning_budget, "planning_budget");
    for (const field of ["model_requests", "input_tokens", "output_tokens", "task_refs", "recipe_steps", "qc_invocations", "wall_seconds", "queue_seconds"]) count(budget[field], `planning_budget.${field}`);
    if (config.planning_budget.model_requests > Number(input.profile.get("model_requests_budget"))) {
      throw new PlatformPortError("planning_budget_exceeded", "planning_budget exceeds RPH_MODEL_REQUESTS");
    }
  }
  const modelGateway = async (): Promise<ModelGatewayUsage> => {
    const data = await usage();
    const rows = data["receipt_rows"];
    if (!Array.isArray(rows)) throw new PlatformPortError("model_usage_invalid", "receipt_rows missing");
    return { source: "platform-receipt", model_requests: count(data["model_requests"], "model_requests"),
      receipts: rows.map((row) => text(object(row, "receipt")["receipt_id"], "receipt_id")) };
  };
  const planner = createModelPlanner({
    research_id: researchId,
    model: text(input.profile.get("model_id"), "RPH_MODEL_ID"),
    transport: { async plan(request): Promise<ModelPlanGatewayResponse> {
      return await consume("model_plan", { ...request, ...scope,
        messages: [...request.messages, { role: "system", content: JSON.stringify({
          supported_capability: subjectCapability(config), confirmed_submission: config?.submission,
          instruction: `Use exactly one ${subjectCapability(config)} step. Its parameters object must contain EXACTLY ncores and memory_gb, copied from confirmed_submission.resources — no workflow key, no method/basis/charge/multiplicity, no other keys. The plan's budget must state exactly what this plan still needs: task_refs 1, recipe_steps 1, qc_invocations 1, wall_seconds equal to confirmed_submission.budget.timeout_s, queue_seconds never above the remaining queue allowance, and model_requests 0, input_tokens 0, output_tokens 0 (no further model calls are planned this round). Never widen the confirmed budget.`,
        }) }],
      }) as unknown as ModelPlanGatewayResponse;
    } },
  });
  const skills = composeProductionSkills({ model_phases: input.restore === undefined ? ["plan"] : [], planner: { kind: "model", async plan(request) {
    if (config === null || config === undefined) throw new PlatformPortError("science_mapping_missing", "--science requires submission and planning_budget");
    if (input.restore !== undefined && currentPlan !== null) return currentPlan;
    const candidate = await planner.plan(request);
    const payload = singlepointPayload(candidate, config);
    execution = { workflow: payload.workflow, input: payload.input, method: payload.method, resources: payload.resources };
    const document = { proposal_id: proposalRef, project_id: request.project_id, round: request.round,
      recipe_digest: candidate.recipe_digest, science: candidate.science, science_inputs: candidate.science_inputs,
      budget: candidate.budget, question: request.normalized_question, decision: "proposed",
      rph_product: { session_seed: seed, science: config, model_plan: candidate, execution,
        question: request.normalized_question },
    };
    proposal = await callTool(session, "acp_research_proposal_create_v1", {
      ...scope, proposal: document, plan: execution, budget: payload.budget,
      idempotency_key: `product-proposal:${proposalRef}:${request.round}`,
    });
    revision = count(proposal["revision"], "proposal revision");
    const digests = object(proposal["digests"], "proposal digests");
    // Freeze the validated model candidate through the sole platform digest authority.
    // The candidate digest was never an approval or execution fact.
    currentPlan = { ...candidate, plan_definition_digest: text(digests["science_digest"], "science_digest") };
    return currentPlan;
  } } });
  const checkpoint = async (kind: string, value: unknown): Promise<void> => {
    if (proposal === null || execution === null) throw new PlatformPortError("proposal_missing", "no frozen proposal");
    proposal = await consume("checkpoint", { ...scope, proposal_ref: proposalRef,
      expected_revision: revision, plan: execution, entry: { kind, value } });
    revision = count(proposal["revision"], "proposal revision");
  };
  const writer: ResearchWriterPort = {
    kind: "platform-research-cas",
    persistTaskRef: (ref) => checkpoint("task_ref", ref),
    persistReport: (report) => checkpoint("report", report),
    async persistCheckpoint(value: SupervisorCheckpoint) {
      await checkpoint("checkpoint", value);
      return { ok: true, replayed: false };
    },
    async persistDecision(decision: DecisionRecord) {
      if (proposal === null || view === null || currentPlan === null || reportRef === null) {
        throw new PlatformPortError("decision_binding_missing", "decision requires the exact projected evidence and proposal");
      }
      const approval = object(proposal["approval"], "approval");
      const task = decision.task_ref;
      if (recordRevision === null) {
        try {
          const existing = await consume("record_read", { research_id: researchId,
            ...(project === null ? {} : { project_id: project }) });
          const refs = existing["task_refs"];
          const bound = Array.isArray(refs) ? object(refs[0], "record task_ref") : {};
          const evidence = existing["evidence_refs"];
          if (existing["round"] === decision.round) {
            if (existing["decision"] !== decision.decision || bound["job_id"] !== task.task_id ||
                bound["attempt"] !== task.attempt || bound["release_id"] !== task.release.release_id ||
                existing["plan_definition_digest"] !== currentPlan.plan_definition_digest ||
                !Array.isArray(evidence) || !evidence.some((ref) => object(ref, "record evidence")["sha256"] === reportRef?.["sha256"])) {
              throw new PlatformPortError("revision_conflict", "existing decision differs from this evidence binding");
            }
            return { ok: true, replayed: true, revision: count(existing["record_revision"], "record revision") };
          }
          if (existing["round"] !== decision.round - 1) throw new PlatformPortError("revision_conflict", "record round cannot be appended");
          recordRevision = count(existing["record_revision"], "record revision");
        } catch (error) {
          if (!(error instanceof PlatformPortError) || !["resource_not_ready", "not_found"].includes(error.code)) throw error;
        }
      }
      const record = { project_id: decision.project_id, research_id: researchId, round: decision.round,
        decision: decision.decision, plan_definition_digest: currentPlan.plan_definition_digest,
        task_refs: [{ project_id: task.project_id, job_id: task.task_id, attempt: task.attempt, release_id: task.release.release_id }],
        proposal_ref: proposalRef, approval_ref: approval["approval_id"] ?? approval["approval_ref"],
        evidence_refs: [reportRef, ...decision.evidence_refs] };
      const result = recordRevision === null
        ? await consume("record_create", { ...scope, ...record, evidence_view: view })
        : await consume("record_append", { ...scope, research_id: researchId, expected_revision: recordRevision, record, evidence_view: view });
      recordRevision = count(result["revision"], "record revision");
      return { ok: true, revision: recordRevision };
    },
  };
  const evidenceReader: EvidenceReaderPort = { async read(request) {
    if (reportRef === null) throw new PlatformPortError("evidence_unavailable", "terminal status has no exact evidence_report artifact reference");
    view = await consume("evidence_read", { ...scope, research_id: researchId, round: request.round,
      task_ref: request.task_ref, report_ref: reportRef });
    return { view };
  } };
  const budgetUsage: BudgetUsageReaderPort = { async read() {
    const result = await consume("budget_read", { ...scope, proposal_ref: proposalRef });
    const used = object(result["used"], "budget used");
    const fields = ["model_requests", "input_tokens", "output_tokens", "task_refs", "recipe_steps", "qc_invocations", "wall_seconds", "queue_seconds"] as const;
    for (const field of fields) count(used[field], `budget.${field}`);
    if (!Array.isArray(result["receipts"])) throw new PlatformPortError("budget_usage_invalid", "receipts missing");
    return { source: "platform-receipt", used: used as unknown as PlanBudget, receipts: result["receipts"] as string[] };
  } };
  const submitPayload: SubmitPayloadMapper = (request) => {
    if (proposal === null || config === null || config === undefined) throw new PlatformPortError("proposal_missing", "no frozen proposal");
    const digests = object(proposal["digests"], "digests");
    return { ...singlepointPayload(request.plan, config), proposal_ref: proposalRef,
      proposal_digest: text(digests["proposal_digest"], "proposal_digest"),
      plan_digest: text(digests["plan_digest"], "plan_digest") };
  };
  const adapters: PlatformPortAdapters = {
    async submit(request) {
      // The platform answers a submit with the job identity synchronously;
      // the frozen attempt/release may exist only moments later because the
      // scheduler launches asynchronously. When the bridge answer carries no
      // complete TaskRef, reconcile it through acp_status (bounded polling,
      // never inventing a field) before declaring the submit failed.
      const payload = submitPayload(request);
      const bridgeRequest = bridgeSubmitRequest(request, payload);
      let result: Awaited<ReturnType<PlatformSession["bridge"]["execute"]>>;
      try {
        result = await session.bridge.execute(bridgeRequest);
      } catch (error) {
        if (error instanceof BridgeContractError) {
          return Object.freeze({ ok: false, task_ref: null, bound_ref: null,
            reason: `bridge_refused:${error.error.code}` });
        }
        throw error;
      }
      if (result.isError) {
        const err = result.structuredContent["error"];
        const code = typeof err === "object" && err !== null && "code" in err ? String(err["code"]) : "submit_error";
        return Object.freeze({ ok: false, task_ref: null, bound_ref: null, reason: `platform_error:${code}` });
      }
      let bound = result.taskRef;
      if (bound === null) {
        const taskId = submitAnswerTaskId(result.structuredContent);
        if (taskId !== null) {
          for (let poll = 0; poll < 6; poll += 1) {
            if (poll > 0) await new Promise((resolve) => setTimeout(resolve, 500));
            const raw = await session.client.callTool("acp_status",
              { reference: { kind: "job", id: taskId } });
            const reconciled = reconcileTaskRef(
              { isError: raw.isError === true, structuredContent: normalizeStatusDigest(raw.structuredContent) },
              { projectId: request.project_id, taskId },
            );
            if (reconciled.taskRef !== null) { bound = reconciled.taskRef; break; }
          }
        }
      }
      if (bound === null) {
        return Object.freeze({ ok: false, task_ref: null, bound_ref: null,
          reason: `task_ref_incomplete:${result.taskRefReconciliation}` });
      }
      return Object.freeze({ ok: true,
        task_ref: { project_id: bound.project_id, job_id: bound.task_id, attempt: bound.attempt,
          release_id: bound.release.release_id },
        bound_ref: bound, reason: "submitted" });
    },
    async resume(request) {
      // This product's recovery authority is the persisted proposal checkpoint.
      // Observe the existing task; never start a new Saga/attempt on resume.
      const data = await callTool(session, "acp_status", { reference: { kind: "job", id: request.task_ref.task_id } });
      const task = object(data["task"] ?? data, "resume status");
      const observed = object(task["task_ref"] ?? task, "resume task_ref");
      const release = object(observed["release"], "resume release");
      if (observed["attempt"] !== request.task_ref.attempt || release["release_id"] !== request.task_ref.release.release_id ||
          !sameDigest(release["release_digest"], request.task_ref.release.release_digest)) {
        throw new PlatformPortError("fenced", "resume observed a different attempt/release");
      }
      return { advanced: false, cursor: request.cursor, terminal_state: null };
    },
    async preflight(request) {
      if (proposal === null || config === null || config === undefined) throw new PlatformPortError("proposal_missing", "no frozen proposal");
      const data = await callTool(session, "acp_preflight", singlepointPayload(request.plan, config));
      const estimate = object(data["estimate"] ?? data, "preflight");
      const digests = object(proposal["digests"], "proposal digests");
      return { ok: (estimate["ok"] === true || estimate["feasible"] === true) && estimate["plan_digest"] === digests["plan_digest"],
        reason: String(estimate["reason"] ?? "platform preflight"), checks: ["platform-preflight"] };
    },
    async admission(request) {
      if (proposal === null || currentPlan?.plan_definition_digest !== request.plan_definition_digest) {
        return { ok: false, reason: "proposal_digest_mismatch", approval_ref: null };
      }
      const approval = object(proposal["approval"], "approval");
      return { ok: true, reason: "platform proposal/approval request persisted; execution admission remains server-enforced",
        approval_ref: text(approval["approval_id"] ?? approval["approval_ref"], "approval_ref") };
    },
    async wait(request) {
      const status = await callTool(session, "acp_status", { reference: { kind: "job", id: request.task_ref.job_id } });
      const task = object(status["task"] ?? status, "task status");
      if (typeof task["attempt"] === "number" && task["attempt"] !== request.task_ref.attempt) {
        throw new PlatformPortError("fenced", "task attempt changed");
      }
      const state = String(task["status"] ?? task["state"] ?? "").toUpperCase();
      const terminal = ["COMPLETED", "FAILED", "CANCELLED"].includes(state);
      if (task["evidence_report"] !== undefined) reportRef = object(task["evidence_report"], "evidence_report");
      const artifacts = task["artifacts"];
      if (Array.isArray(artifacts)) {
        const published = artifacts.find((artifact) => typeof artifact === "object" && artifact !== null && artifact.kind === "evidence_report");
        if (published !== undefined) reportRef = object(published, "published evidence report");
      }
      // No platform-published report reference on the status surface: ask the
      // platform to assemble the attempt's canonical evidence report (A-line
      // vocabulary, deterministic, digest-bound). Any refusal degrades to
      // "no binding" — the run then stops honestly instead of citing a
      // document nobody produced.
      if (reportRef === null && state === "COMPLETED") {
        try {
          const produced = await callTool(session, "acp_research_evidence_prepare_v1", {
            ...scope, research_id: researchId, round: request.cursor === null ? 0 : request.round,
            task_ref: {
              project_id: request.project_id, task_id: request.task_ref.job_id,
              job_id: request.task_ref.job_id, attempt: request.task_ref.attempt,
              release_id: request.task_ref.release_id,
            },
          });
          const reference = produced["report_ref"];
          if (typeof reference === "object" && reference !== null) {
            reportRef = object(reference, "report_ref");
          }
        } catch {
          reportRef = null;
        }
      }
      return { cursor: request.cursor, timed_out: !terminal, events: terminal ? [{
        event_key: `status:${request.task_ref.job_id}:${request.task_ref.attempt}:${state}`,
        type: `job.${state.toLowerCase()}`, task_ref: request.task_ref, report: null,
        report_id: reportRef === null ? null : String(reportRef["report_id"] ?? reportRef["evidence_id"] ?? ""),
        report_digest: reportRef === null ? null : text(reportRef["sha256"], "report digest"),
      }] : [] };
    },
  };
  return { skills, writer, modelGateway, adapters, evidenceReader, budgetUsage, submitPayload,
    proposalRef, researchId, sessionSeed: seed,
    resumeContext: () => ({ proposal_ref: proposalRef, research_id: researchId, session_seed: seed,
      plan_digest: proposal === null ? null : String(object(proposal["digests"], "digests")["plan_digest"]) }),
    ...(config === undefined || config === null ? {} : { scienceSeed: config.science_seed,
      initialInput: config.initial_input, limits: { max_rounds: 1, budget: config.planning_budget } }),
  };
}


/** Rehydrate installed-product state through public reads; legacy Sagas remain compatible. */
/** Canonical JSON with recursively sorted keys (order-insensitive compare). */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/** Best-effort job id from a submit answer that carried no complete TaskRef. */
function submitAnswerTaskId(payload: Readonly<Record<string, unknown>>): string | null {
  const data = payload["data"];
  const root = typeof data === "object" && data !== null && !Array.isArray(data) ? data as Record<string, unknown> : payload;
  const sources = [root, root["task_ref"], root["reference"]].filter(
    (value): value is Record<string, unknown> =>
      typeof value === "object" && value !== null && !Array.isArray(value));
  for (const source of sources) {
    for (const key of ["task_id", "job_id", "id"] as const) {
      const candidate = source[key];
      if (typeof candidate === "string" && candidate.length > 0) return candidate;
    }
  }
  return null;
}

/**
 * The platform status projection carries the release digest as bare hex while
 * the bridge TaskRef contract requires the ``sha256:`` scheme; this shallow
 * copy prefixes exactly that one field and never rewrites anything else.
 */
function normalizeStatusDigest(structured: unknown): Readonly<Record<string, unknown>> {
  if (typeof structured !== "object" || structured === null || Array.isArray(structured)) {
    return structured as Record<string, unknown>;
  }
  const payload = structured as Record<string, unknown>;
  const data = payload["data"];
  if (typeof data !== "object" || data === null || Array.isArray(data)) return payload;
  const body = data as Record<string, unknown>;
  const taskRef = body["task_ref"];
  if (typeof taskRef !== "object" || taskRef === null || Array.isArray(taskRef)) return payload;
  const reference = taskRef as Record<string, unknown>;
  const release = reference["release"];
  if (typeof release !== "object" || release === null || Array.isArray(release)) return payload;
  const releaseRecord = release as Record<string, unknown>;
  const digest = releaseRecord["release_digest"];
  if (typeof digest !== "string" || !/^[0-9a-f]{64}$/.test(digest)) return payload;
  return {
    ...payload,
    data: { ...body, task_ref: { ...reference,
      release: { ...releaseRecord, release_digest: `sha256:${digest}` } } },
  };
}

/** Digest equality ignoring the optional ``sha256:`` scheme prefix. */
function sameDigest(a: unknown, b: unknown): boolean {
  const strip = (value: unknown): string =>
    typeof value === "string" ? value.replace(/^sha256:/, "") : String(value ?? "");
  return strip(a) === strip(b);
}

export async function restoreProductParts(input: {
  readonly profile: ResolvedRuntimeProfile;
  readonly session: PlatformSession;
  readonly proposalRef: string;
  readonly planDigest: string;
  readonly task?: string;
  readonly cursor?: string;
}) {
  const project = input.profile.get("project_id");
  const read = await callTool(input.session, "acp_research_proposal_read_v1", {
    proposal_ref: input.proposalRef, ...(project === null ? {} : { project_id: project }),
  });
  // The platform wire format returns the stored proposal document verbatim
  // (no envelope, no revision); an in-process caller may still wrap it under
  // "proposal" with a revision. Accept exactly those two shapes.
  const document = read["proposal"] !== undefined
    ? object(read["proposal"], "proposal")
    : read;
  if (document["rph_product"] === undefined) return null;
  let proposalRevision: number;
  if (typeof read["revision"] === "number") {
    proposalRevision = count(read["revision"], "proposal revision");
  } else {
    // The authoritative current revision comes from the P2 snapshot (the read
    // route returns the document verbatim by contract, so it never carries it).
    // The snapshot projection redacts the ref value, so the single requested
    // proposal entry is matched by kind, not by its (redacted) ref string.
    const snap = await callTool(input.session, "acp_research_snapshot_v1", {
      refs: [`proposal:${input.proposalRef}`], ...(project === null ? {} : { project_id: project }),
    });
    const snapBody = snap["snapshot"] !== undefined ? object(snap["snapshot"], "snapshot") : snap;
    const entries = snapBody["refs"];
    const entry = Array.isArray(entries)
      ? entries.map((row) => object(row, "snapshot ref"))
          .find((row) => row["kind"] === "proposal") ?? null
      : null;
    if (entry === null || entry["observed"] !== true) {
      throw new PlatformPortError("resume_binding_missing", "the snapshot did not observe the proposal");
    }
    proposalRevision = count(entry["revision"], "proposal revision from snapshot");
  }
  const saved = object(document["rph_product"], "rph_product");
  const approvals = await callTool(input.session, "acp_research_approval_query_v1", {
    proposal_ref: input.proposalRef,
    // The approval query is project-scoped server-side; without the project the
    // answer is an empty list and every restore would look unbound.
    ...(project === null ? {} : { project_id: project }),
  });
  const rows = approvals["approvals"];
  if (!Array.isArray(rows)) throw new PlatformPortError("resume_binding_missing", "approval rows missing");
  const approval = rows.map((row) => object(row, "approval")).find((row) =>
    (row["approval_id"] ?? row["approval_ref"]) === document["approval_ref"]);
  if (approval === undefined || approval["plan_digest"] !== input.planDigest ||
      approval["science_digest"] !== document["plan_definition_digest"]) {
    throw new PlatformPortError("resume_binding_mismatch", "requested execution digest/approval differs from the stored proposal");
  }
  const config = object(saved["science"], "saved science") as unknown as ProductScienceConfig;
  const plan = { ...object(saved["model_plan"], "saved model plan"),
    plan_definition_digest: document["plan_definition_digest"] } as unknown as PlannerOutput;
  if (validatePlannerOutput(plan).length > 0) throw new PlatformPortError("resume_plan_invalid", "saved plan does not validate");
  const mapped = singlepointPayload(plan, config);
  const execution = object(saved["execution"], "saved execution");
  for (const field of ["workflow", "input", "method", "resources"] as const) {
    // The platform store re-serializes documents with alphabetically ordered
    // keys, so the comparison uses a canonical (key-sorted) form instead of
    // raw JSON.stringify, which is insertion-order sensitive.
    if (canonicalJson(mapped[field]) !== canonicalJson(execution[field])) {
      throw new PlatformPortError("resume_binding_mismatch", `saved execution changed: ${field}`);
    }
  }
  const sessionSeed = text(saved["session_seed"], "saved session seed");
  const parts = await createProductParts({ profile: input.profile, session: input.session,
    science: config, research: sessionSeed, restore: { execution, plan,
      revision: proposalRevision, proposal: { document, approval,
        digests: { plan_digest: approval["plan_digest"], science_digest: approval["science_digest"], proposal_digest: approval["proposal_digest"] } },
    } });
  if (parts === null) throw new PlatformPortError("resume_parts_missing", "no restored product parts");
  if (parts.proposalRef !== input.proposalRef) throw new PlatformPortError("resume_identity_mismatch", "session identity differs from the stored proposal");
  const state = document["rph_state"] === undefined ? {} : object(document["rph_state"], "rph_state");
  const checkpoint = state["checkpoint"] === undefined ? null : object(state["checkpoint"], "checkpoint");
  const handle = checkpoint === null ? null : object(checkpoint["handle"], "checkpoint handle");
  if (input.task !== undefined) {
    const bound = handle === null ? object(state["task_ref"], "persisted task_ref") : object(handle["task_ref"], "checkpoint task_ref");
    if ((bound["task_id"] ?? bound["job_id"]) !== input.task) throw new PlatformPortError("resume_binding_mismatch", "--task differs from the persisted task");
  }
  if (input.cursor !== undefined && input.cursor !== handle?.["cursor"]) {
    throw new PlatformPortError("resume_cursor_mismatch", "--cursor differs from the persisted recovery cursor; no cursor jump is allowed");
  }
  const supervisorInput: SupervisorInput = {
    project_id: text(document["project_id"], "project_id"), research_id: parts.researchId,
    question: text(saved["question"], "saved question"), question_ref: null,
    constraints: [], available_inputs: [config.initial_input.input_id], recipe_digest: plan.recipe_digest,
    initial_input: config.initial_input, science_seed: config.science_seed,
    limits: { max_rounds: 1, budget: config.planning_budget },
    plan_definition_digest: () => plan.plan_definition_digest,
    require_evidence_binding: true, require_model_gateway: true,
    ...(checkpoint === null ? {} : { resume_handle: checkpoint["handle"] as SupervisorCheckpoint["handle"] }),
  };
  return { parts, supervisorInput };
}
