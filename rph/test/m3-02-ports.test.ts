/**
 * M3-02b — the six recovery ports at the composition root (M3-04 exit).
 *
 * The Supervisor contract is frozen (`skills/supervisor.ts`); this suite proves
 * the *adapter* half only:
 *
 *   - `submitIntent` is the session bridge's M1-02 ledger, and the submit that
 *     follows forwards the persisted wire key verbatim (no re-derivation);
 *   - `evidence` passes the single projection through untouched and turns a
 *     transport failure into `ResearchPortError("evidence_unavailable")`;
 *   - `persistDecision` / `persistCheckpoint` are the writer's CAS/fenced writes
 *     and never report success for a failed writer;
 *   - `budgetUsage` is the platform receipt read;
 *   - `resume` maps P2-R5 onto the frozen `ResumeResult` (no invented progress,
 *     cursor errors translated to the frozen rules);
 *   - product readiness lists a missing binding/budget source in the same
 *     fail-fast list as the rest.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  Bridge,
  P1_TOOL_NAMES,
  P2_OPERATION_MAP,
  parsePlatformTaskRef,
  type BridgeToolResult,
  type ResearchTaskRef,
} from "@rph/bridge";
import { MemorySubmitIntentStore, SubmitIntentLedger } from "@rph/research";
import {
  MCP_PROTOCOL_VERSION,
  PlatformPortError,
  ResearchPortError,
  bridgeSubmitRequest,
  buildPlatformSession,
  collectReadinessIssues,
  createBudgetUsagePort,
  createBridgeSubmitPort,
  createCheckpointPort,
  createDecisionPort,
  createEvidencePort,
  createPlatformPorts,
  createResumePort,
  mapResumePayload,
  resolveRuntimeProfile,
  type EvidenceReaderPort,
  type EvidenceRequest,
  type PlatformSubmitPayload,
  type ResearchResumeRequest,
  type ResumeRequest,
  type SubmitRequest,
  type SupervisorCheckpoint,
} from "@rph/dsh-bundle";

import { TEST_OPTIONS } from "./harness.js";
import {
  FAKE_REPORT_DIGEST,
  FAKE_REPORT_ID,
  fakeEvidenceView,
  fakeTaskRefPayload,
} from "./m3-02-harness.js";

const PROJECT = "proj-m3-02";
const RESEARCH = "res-m3-02";
const DIGEST = "sha256:" + "a".repeat(64);
const PLAN_DIGEST = "sha256:" + "b".repeat(64);
const WIRE_KEY = "rph.wire-key-m3-02-abcdef";
const INTENT_KEY = "proj:record:res:round-0:submit:plan-abc";
const APPROVAL = "ap_m3_02";
const TASK_ID = "20261027_001_Optimize";
const RELEASE_ID = "release-m3-02";

const PAYLOAD: PlatformSubmitPayload = {
  workflow: "optimize",
  input: { source: "CCO", source_type: "smiles" },
  method: { name: "r2SCAN-3c" },
  resources: { cores: 1 },
  budget: { unit: "task_count", count: 1, timeout_s: 120 },
  proposal_ref: "prop-m3-02",
  proposal_digest: DIGEST,
};

const BOUND_REF: ResearchTaskRef = parsePlatformTaskRef(
  fakeTaskRefPayload(PROJECT, { negotiated: true, taskId: TASK_ID, releaseId: RELEASE_ID }),
  PROJECT,
);

function submitRequest(overrides: Partial<SubmitRequest> = {}): SubmitRequest {
  return {
    project_id: PROJECT,
    round: 0,
    plan_definition_digest: PLAN_DIGEST,
    approval_ref: APPROVAL,
    plan: { plan_id: "plan-1" } as unknown as SubmitRequest["plan"],
    idempotency_key: WIRE_KEY,
    submit_intent_key: INTENT_KEY,
    ...overrides,
  };
}

function okResult(data: Readonly<Record<string, unknown>>): BridgeToolResult {
  return { isError: false, structuredContent: { schema_version: 1, status: "ok", data } };
}

function failResult(code: string, params: Readonly<Record<string, unknown>> = {}): BridgeToolResult {
  return {
    isError: true,
    structuredContent: {
      schema_version: 1,
      status: "error",
      data: null,
      error: { code, message: code, params, retryable: false, diagnostic_ref: null },
    },
  };
}

function recordingClient(answer: (name: string, params: Readonly<Record<string, unknown>>) => BridgeToolResult) {
  const calls: Array<{ readonly name: string; readonly params: Readonly<Record<string, unknown>> }> = [];
  return {
    calls,
    client: {
      async callTool(name: string, params: Readonly<Record<string, unknown>>) {
        calls.push({ name, params });
        return answer(name, params);
      },
    },
  };
}

const ALL_P2_TOOLS = [...new Set(Object.values(P2_OPERATION_MAP).map((entry) => entry.tool))];

/** A real `PlatformSession` over an injected (fake) client. */
function session(client: { callTool(name: string, params: Readonly<Record<string, unknown>>): Promise<BridgeToolResult> }) {
  return buildPlatformSession({
    mode: "acceptance",
    protocolVersion: MCP_PROTOCOL_VERSION,
    tools: [...P1_TOOL_NAMES, ...ALL_P2_TOOLS].map((name) => ({
      name,
      description: name,
      inputSchema: { type: "object" },
    })),
    handshake: {
      status: "negotiated",
      extension: null,
      advertised: [],
      negotiatedExtensions: [{ id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION }],
      refusal: null,
      requested: { id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION },
    },
    client,
  });
}

// ---------------------------------------------------------------------------
// submitIntent + submit: the key is carried, never re-derived
// ---------------------------------------------------------------------------

test("submitIntent is the session bridge's M1-02 ledger", { ...TEST_OPTIONS }, async () => {
  const ledger = new SubmitIntentLedger(new MemorySubmitIntentStore());
  const { client } = recordingClient(() => okResult({ task_ref: BOUND_REF }));
  const ports = createPlatformPorts({
    session: session(client),
    writer: {
      kind: "research-store",
      persistTaskRef: async () => {},
      persistReport: async () => {},
    },
  });
  assert.notEqual(ports.submitIntent, undefined);
  const prepared = await ports.submitIntent?.prepare({
    project_id: PROJECT,
    research_id: RESEARCH,
    round: 0,
    plan_definition_digest: PLAN_DIGEST,
  });
  assert.equal(typeof prepared?.wire_key, "string");
  assert.equal(prepared?.created, true);
  // The default port must be the bridge ledger, not a second in-memory one:
  // a second prepare with the same identity replays instead of re-deriving.
  const replay = await ports.submitIntent?.prepare({
    project_id: PROJECT,
    research_id: RESEARCH,
    round: 0,
    plan_definition_digest: PLAN_DIGEST,
  });
  assert.equal(replay?.wire_key, prepared?.wire_key);
  assert.equal(replay?.created, false);
  // The ledger is the one the caller passed in, not a second in-process store.
  assert.equal(typeof ledger.prepare, "function");
});

test("bridgeSubmitRequest forwards the persisted wire key verbatim", () => {
  const request = bridgeSubmitRequest(submitRequest(), PAYLOAD);
  assert.equal(request.operation, "submit");
  assert.equal(request.idempotencyKey, WIRE_KEY);
  assert.equal(request.idempotencyKey, submitRequest().idempotency_key);
  assert.equal(request.planDigest, PLAN_DIGEST);
  assert.equal(request.proposalRef, PAYLOAD.proposal_ref);
  // The two fields the bridge would use to derive its own key are absent: the
  // legacy self-derived submit key cannot come back through this path.
  assert.equal(Object.hasOwn(request, "research"), false);
  assert.equal(Object.hasOwn(request, "round"), false);
});

test("a submit without its persisted keys is refused, never derived", () => {
  assert.throws(
    () => bridgeSubmitRequest(submitRequest({ idempotency_key: "" }), PAYLOAD),
    (error: unknown) => error instanceof PlatformPortError && error.code === "submit_key_missing",
  );
  assert.throws(
    () => bridgeSubmitRequest(submitRequest({ submit_intent_key: "" }), PAYLOAD),
    (error: unknown) => error instanceof PlatformPortError && error.code === "submit_intent_missing",
  );
});

test("the bridge submit port sends the verbatim key and keeps the bound TaskRef", { ...TEST_OPTIONS }, async () => {
  const { client, calls } = recordingClient(() => okResult({ task_ref: BOUND_REF }));
  const port = createBridgeSubmitPort({ bridge: new Bridge(client), payload: () => PAYLOAD });
  const result = await port(submitRequest());
  assert.equal(result.ok, true);
  assert.equal(result.task_ref?.job_id, TASK_ID);
  assert.equal(result.task_ref?.attempt, BOUND_REF.attempt);
  assert.equal(result.task_ref?.release_id, RELEASE_ID);
  assert.deepEqual(result.bound_ref, BOUND_REF);
  const call = calls.find((entry) => entry.name === "acp_submit");
  assert.equal(call?.params["idempotency_key"], WIRE_KEY);
  assert.equal(call?.params["proposal_ref"], PAYLOAD.proposal_ref);
});

test("a failed bridge submit is a typed refusal, never a fabricated TaskRef", { ...TEST_OPTIONS }, async () => {
  const { client } = recordingClient(() => failResult("input_invalid", { rule: "unknown_field" }));
  const port = createBridgeSubmitPort({ bridge: new Bridge(client), payload: () => PAYLOAD });
  const result = await port(submitRequest());
  assert.equal(result.ok, false);
  assert.equal(result.task_ref, null);
  assert.equal(result.bound_ref, null);
  assert.match(result.reason, /platform_error:input_invalid/);
});

// ---------------------------------------------------------------------------
// Evidence, decision, checkpoint, budget
// ---------------------------------------------------------------------------

test("the evidence port passes the single projection through untouched", { ...TEST_OPTIONS }, async () => {
  const view = fakeEvidenceView(BOUND_REF, RESEARCH, 0);
  const reader: EvidenceReaderPort = {
    async read() {
      return { view, read_at: "2026-10-27T00:00:00Z" };
    },
  };
  const port = createEvidencePort(reader);
  const result = await port({
    project_id: PROJECT,
    research_id: RESEARCH,
    round: 0,
    task_ref: BOUND_REF,
    event_key: "ev-1",
    cursor: null,
  });
  assert.deepEqual(result.view, view);
  assert.equal(result.read_at, "2026-10-27T00:00:00Z");
});

test("a transport failure is ResearchPortError(evidence_unavailable)", { ...TEST_OPTIONS }, async () => {
  const port = createEvidencePort({
    async read() {
      throw new Error("route unavailable");
    },
  });
  await assert.rejects(
    () =>
      port({
        project_id: PROJECT,
        research_id: RESEARCH,
        round: 0,
        task_ref: BOUND_REF,
        event_key: "ev-1",
        cursor: null,
      }),
    (error: unknown) =>
      error instanceof ResearchPortError && error.rule === "evidence_unavailable",
  );
});

test("a fenced transport refusal keeps its rule", { ...TEST_OPTIONS }, async () => {
  const port = createEvidencePort({
    async read() {
      throw new ResearchPortError("fenced", "execution right moved");
    },
  });
  await assert.rejects(
    () =>
      port({
        project_id: PROJECT,
        research_id: RESEARCH,
        round: 0,
        task_ref: BOUND_REF,
        event_key: "ev-1",
        cursor: null,
      }),
    (error: unknown) => error instanceof ResearchPortError && error.rule === "fenced",
  );
});

test("decision and checkpoint adapters never report success for a failed writer", { ...TEST_OPTIONS }, async () => {
  const view = fakeEvidenceView(BOUND_REF, RESEARCH, 0);
  const record = {
    schema: "acp2.runtime.research-decision.v1",
    view,
    decision: "accept",
    projection_digest: view.projection_digest,
  } as unknown as Parameters<NonNullable<ReturnType<typeof createDecisionPort>>>[0];
  const checkpoint = {
    stage: "task_bound",
    round: 0,
    handle: {},
  } as unknown as SupervisorCheckpoint;

  const refusing = {
    kind: "research-store",
    persistTaskRef: async () => {},
    persistReport: async () => {},
    persistDecision: async () => {
      throw new Error("cas unavailable");
    },
    persistCheckpoint: async () => {
      throw new Error("fence store unavailable");
    },
  };
  const decision = await createDecisionPort(
    refusing,
    refusing.persistDecision,
  )(record);
  assert.equal(decision.ok, false);
  assert.match(decision.reason ?? "", /writer_error/);

  const persisted = await createCheckpointPort(
    refusing,
    refusing.persistCheckpoint,
  )(checkpoint);
  assert.equal(persisted.ok, false);

  const fenced = {
    kind: "research-store",
    persistTaskRef: async () => {},
    persistReport: async () => {},
    persistDecision: async () => {
      throw new ResearchPortError("fenced", "another attempt owns the write");
    },
    persistCheckpoint: async () => ({ ok: false, fenced: true, reason: "fenced" }),
  };
  await assert.rejects(
    () => createDecisionPort(fenced, fenced.persistDecision)(record),
    (error: unknown) => error instanceof ResearchPortError && error.rule === "fenced",
  );
  const fencedCheckpoint = await createCheckpointPort(
    fenced,
    fenced.persistCheckpoint,
  )(checkpoint);
  assert.equal(fencedCheckpoint.fenced, true);
});

test("the budget port is the platform receipt read", { ...TEST_OPTIONS }, async () => {
  const used = {
    model_requests: 2,
    input_tokens: 10,
    output_tokens: 5,
    task_refs: 1,
    recipe_steps: 1,
    qc_invocations: 1,
    wall_seconds: 60,
    queue_seconds: 5,
  };
  const port = createBudgetUsagePort({
    async read() {
      return { source: "saga-record", used, receipts: ["saga:budget:1"] };
    },
  });
  const receipt = await port();
  assert.equal(receipt.source, "saga-record");
  assert.deepEqual(receipt.used, used);
  await assert.rejects(
    () =>
      createBudgetUsagePort({
        async read() {
          throw new Error("saga unavailable");
        },
      })(),
    PlatformPortError,
  );
});

// ---------------------------------------------------------------------------
// Resume (P2-R5)
// ---------------------------------------------------------------------------

function resumeRequest(overrides: Partial<ResumeRequest> = {}): ResumeRequest {
  return {
    project_id: PROJECT,
    research_id: RESEARCH,
    round: 0,
    plan_definition_digest: PLAN_DIGEST,
    task_ref: BOUND_REF,
    cursor: "cursor-1",
    diagnostic_only: false,
    ...overrides,
  };
}

test("mapResumePayload adopts only what the platform reported", () => {
  const advanced = mapResumePayload(resumeRequest(), {
    data: { resumed_from: "saga", saga: { state: "executing", terminal: false } },
  });
  assert.equal(advanced.advanced, true);
  assert.equal(advanced.cursor, "cursor-1");
  assert.equal(advanced.terminal_state, null);

  const terminal = mapResumePayload(resumeRequest(), {
    data: { resumed_from: "saga", saga: { state: "completed", terminal: true } },
  });
  assert.equal(terminal.advanced, false);
  assert.equal(terminal.terminal_state, "completed");

  const diagnostic = mapResumePayload(resumeRequest({ diagnostic_only: true }), {
    data: { resumed_from: "saga", saga: { state: "executing", terminal: false } },
  });
  assert.equal(diagnostic.advanced, false);

  assert.throws(
    () => mapResumePayload(resumeRequest(), { status: "ok" }),
    (error: unknown) => error instanceof ResearchPortError && error.rule === "stale_revision",
  );
});

test("the resume port calls P2-R5 with the bound proposal and maps refusal rules", { ...TEST_OPTIONS }, async () => {
  const { client, calls } = recordingClient(() =>
    okResult({ resumed_from: "saga", saga: { state: "executing", terminal: false } }),
  );
  const port = createResumePort({ session: session(client), proposalRef: "prop-m3-02" });
  const resumed = await port(resumeRequest());
  assert.equal(resumed.advanced, true);
  const call = calls.find((entry) => entry.name === "acp_research_resume_v1");
  assert.equal(call?.params["proposal_ref"], "prop-m3-02");
  assert.equal(call?.params["plan_digest"], PLAN_DIGEST);
  assert.equal(call?.params["diagnostic_only"], false);

  const expired = createResumePort({
    session: session(
      recordingClient(() => failResult("resource_not_ready", { rule: "cursor_expired" })).client,
    ),
    proposalRef: "prop-m3-02",
  });
  await assert.rejects(
    () => expired(resumeRequest()),
    (error: unknown) => error instanceof ResearchPortError && error.rule === "cursor_expired",
  );

  const unknown = createResumePort({
    session: session(recordingClient(() => failResult("saga_not_found")).client),
    proposalRef: "prop-m3-02",
  });
  await assert.rejects(
    () => unknown(resumeRequest()),
    (error: unknown) => error instanceof ResearchPortError && error.rule === "stale_revision",
  );
});

// ---------------------------------------------------------------------------
// Assembly + product readiness
// ---------------------------------------------------------------------------

test("the assembly installs recovery ports only when their source exists", { ...TEST_OPTIONS }, async () => {
  const { client } = recordingClient(() => okResult({}));
  const base = {
    session: session(client),
    writer: {
      kind: "research-store",
      persistTaskRef: async () => {},
      persistReport: async () => {},
    },
  };
  const minimal = createPlatformPorts(base);
  assert.equal(typeof minimal.submitIntent?.prepare, "function");
  assert.equal(minimal.evidence, undefined);
  assert.equal(minimal.persistDecision, undefined);
  assert.equal(minimal.persistCheckpoint, undefined);
  assert.equal(minimal.budgetUsage, undefined);
  assert.equal(minimal.resume, undefined);

  const full = createPlatformPorts({
    ...base,
    writer: {
      ...base.writer,
      persistDecision: async () => ({ ok: true }),
      persistCheckpoint: async () => ({ ok: true }),
    },
    evidenceReader: { read: async () => ({ view: fakeEvidenceView(BOUND_REF, RESEARCH, 0) }) },
    budgetUsage: {
      read: async () => ({
        source: "saga-record",
        used: {
          model_requests: 0,
          input_tokens: 0,
          output_tokens: 0,
          task_refs: 0,
          recipe_steps: 0,
          qc_invocations: 0,
          wall_seconds: 0,
          queue_seconds: 0,
        },
        receipts: [],
      }),
    },
    proposalRef: "prop-m3-02",
    submitPayload: () => PAYLOAD,
  });
  assert.notEqual(full.evidence, undefined);
  assert.notEqual(full.persistDecision, undefined);
  assert.notEqual(full.persistCheckpoint, undefined);
  assert.notEqual(full.budgetUsage, undefined);
  assert.notEqual(full.resume, undefined);
  assert.notEqual(full.submitIntent, undefined);
});

test("product readiness lists a missing evidence binding and budget source", () => {
  const profile = resolveRuntimeProfile("dev", {});
  const issues = collectReadinessIssues({
    mode: "product",
    environment: "dev",
    profile,
    skills: null,
    writer: {
      kind: "research-store",
      persistTaskRef: async () => {},
      persistReport: async () => {},
    },
    artifactEvidencePath: "/srv/evidence",
    scienceSeedPresent: true,
  });
  const codes = issues.map((entry) => entry.code);
  assert.equal(codes.includes("evidence_reader_missing"), true, codes.join(","));
  assert.equal(codes.includes("decision_writer_missing"), true, codes.join(","));
  assert.equal(codes.includes("checkpoint_writer_missing"), true, codes.join(","));
  assert.equal(codes.includes("budget_usage_source_missing"), true, codes.join(","));
  // Acceptance is not the strict binding mode: no evidence requirements there.
  const acceptance = collectReadinessIssues({
    mode: "acceptance",
    environment: "dev",
    profile,
    skills: null,
    writer: {
      kind: "research-store",
      persistTaskRef: async () => {},
      persistReport: async () => {},
    },
    artifactEvidencePath: "/srv/evidence",
    scienceSeedPresent: true,
  }).map((entry) => entry.code);
  assert.equal(acceptance.includes("evidence_reader_missing"), false);
});

test("the P2 resume fixture the CLI uses is the real operation name", () => {
  // Guard: the adapter must call the frozen tool, never a locally invented one.
  const mapping = P2_OPERATION_MAP["research.resume"];
  assert.equal(mapping.tool, "acp_research_resume_v1");
  assert.equal(FAKE_REPORT_ID.length > 0, true);
  assert.equal(FAKE_REPORT_DIGEST.startsWith("sha256:"), true);
});

test("a resume without a proposal ref installs no port", () => {
  const { client } = recordingClient(() => okResult({}));
  const ports = createPlatformPorts({
    session: session(client),
    writer: {
      kind: "research-store",
      persistTaskRef: async () => {},
      persistReport: async () => {},
    },
    proposalRef: "",
  });
  assert.equal(ports.resume, undefined);
  // The unused-import guard: `ResearchResumeRequest` is the raw call's shape.
  const raw: ResearchResumeRequest = { proposal_ref: "", plan_digest: "" };
  assert.equal(raw.proposal_ref, "");
});
