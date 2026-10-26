/**
 * Bridge surface: research semantics <-> public platform client.
 *
 * The bridge depends only on public JSON shapes and a `PlatformClient`
 * interface. It deliberately contains no transport implementation: no fetch,
 * no DSH source import, no platform store, and no process/scientific
 * execution. A concrete platform client is injected by the caller (CLI or
 * DSH plugin) in a later task.
 */

import {
  MemorySubmitIntentStore,
  SubmitIntentConflict,
  SubmitIntentLedger,
  type SubmitIntentStore,
} from "@rph/research";
import type { ResearchRecordRef } from "@rph/research";
import {
  assertEvidenceView,
  parseTaskRef,
  taskRefKey,
  type EvidenceView,
  type TaskRef,
} from "@rph/research";

/** DSH exposes MCP tools to the model with exactly this prefix. */
export const BRIDGE_MODEL_PREFIX = "mcp__acp__";

/** Operations the experience bridge may request from a platform client. */
export type BridgeOperation =
  | "discover"
  | "validate"
  | "submit"
  | "status"
  | "evidence"
  | "prepare"
  | "read";

/** Canonical operation order; useful for exhaustive iteration in consumers. */
export const BRIDGE_OPERATIONS: readonly BridgeOperation[] = Object.freeze([
  "discover",
  "validate",
  "submit",
  "status",
  "evidence",
  "prepare",
  "read",
]);

/** Request fields whose presence depends on the operation. */
export type BridgeField =
  | "project"
  | "input"
  | "idempotencyKey"
  | "task"
  | "attempt"
  | "generation"
  | "resource";

/**
 * Per-operation required-field matrix (frozen contract, see
 * `tool-semantics.md` and plan C T-10).
 *
 * - discovery needs none of task/generation;
 * - validate/submit need project + input + idempotency key, and never the
 *   result generation;
 * - status needs task + attempt;
 * - evidence/prepare need task + attempt + resource;
 * - read needs task + attempt + generation + resource.
 */
export const OPERATION_FIELDS: Readonly<
  Record<BridgeOperation, readonly BridgeField[]>
> = Object.freeze({
  discover: Object.freeze([]),
  validate: Object.freeze(["project", "input", "idempotencyKey"] as const),
  submit: Object.freeze(["project", "input", "idempotencyKey"] as const),
  status: Object.freeze(["task", "attempt"] as const),
  evidence: Object.freeze(["task", "attempt", "resource"] as const),
  prepare: Object.freeze(["task", "attempt", "resource"] as const),
  read: Object.freeze([
    "task",
    "attempt",
    "generation",
    "resource",
  ] as const),
});

/** Required fields for a single bridge operation. */
export function requiredFieldsFor(
  operation: BridgeOperation,
): readonly BridgeField[] {
  return OPERATION_FIELDS[operation];
}

/** A bridge request before transport mapping. */
export interface BridgeRequest {
  readonly operation: BridgeOperation;
  readonly project?: string;
  readonly input?: unknown;
  readonly idempotencyKey?: string;
  readonly task?: string;
  readonly attempt?: number;
  readonly generation?: number;
  readonly resource?: string;
  readonly workflow?: string;
  readonly method?: unknown;
  readonly resources?: unknown;
  readonly explicit?: boolean;
  readonly phase?: BridgePhase;
  /**
   * Research identity of a submit (M1-02 / D-IDENT-07).
   *
   * `research` + `round` + `planDigest` let the bridge derive the stable
   * submit idempotency key (`project + research + round + plan_digest`) and
   * persist it before the call.  They never replace `idempotencyKey`: a
   * caller-supplied key is forwarded verbatim.
   */
  readonly research?: string;
  readonly round?: number;
  readonly planDigest?: string;
}

/** One tool result as returned by a platform client (public envelope only). */
export interface BridgeToolResult {
  readonly isError: boolean;
  readonly structuredContent: Readonly<Record<string, unknown>>;
}

/**
 * Minimal platform-client interface.
 *
 * Implementations are injected. The bridge never owns HTTP, SSE, the platform
 * store, or an MCP subprocess; it only translates operations into tool calls.
 */
export interface PlatformClient {
  callTool(
    rawName: string,
    params: Readonly<Record<string, unknown>>,
  ): Promise<BridgeToolResult>;
}

function isPresent(field: BridgeField, request: BridgeRequest): boolean {
  switch (field) {
    case "project":
      return typeof request.project === "string" && request.project.length > 0;
    case "input":
      return request.input !== undefined && request.input !== null;
    case "idempotencyKey":
      return (
        typeof request.idempotencyKey === "string" &&
        request.idempotencyKey.length > 0
      );
    case "task":
      return typeof request.task === "string" && request.task.length > 0;
    case "attempt":
      return (
        typeof request.attempt === "number" &&
        Number.isInteger(request.attempt) &&
        request.attempt >= 0
      );
    case "generation":
      return (
        typeof request.generation === "number" &&
        Number.isInteger(request.generation) &&
        request.generation >= 0
      );
    case "resource":
      return (
        typeof request.resource === "string" && request.resource.length > 0
      );
  }
}

/** One research-round submit identity (all four segments are required). */
export interface SubmitIntentIdentity {
  readonly project: string;
  readonly research: string;
  readonly round: number;
  readonly planDigest: string;
}

/** The research-round dimensions of a request, or `null` when incomplete. */
export function resolveSubmitIntentIdentity(
  request: BridgeRequest,
): SubmitIntentIdentity | null {
  if (request.operation !== "submit") {
    return null;
  }
  const { project, research, round, planDigest } = request;
  if (
    typeof project !== "string" ||
    project.length === 0 ||
    typeof research !== "string" ||
    research.length === 0 ||
    typeof round !== "number" ||
    !Number.isInteger(round) ||
    round < 0 ||
    typeof planDigest !== "string" ||
    planDigest.length === 0
  ) {
    return null;
  }
  return Object.freeze({ project, research, round, planDigest });
}

/** Whether a submit can derive (and persist) its own idempotency key. */
export function canDeriveSubmitKey(request: BridgeRequest): boolean {
  return resolveSubmitIntentIdentity(request) !== null;
}

/** Fields required by the operation but absent/invalid on the request. */
export function missingFieldsFor(request: BridgeRequest): BridgeField[] {
  return requiredFieldsFor(request.operation).filter((field) => {
    if (field === "idempotencyKey" && canDeriveSubmitKey(request)) {
      return false;
    }
    return !isPresent(field, request);
  });
}

/**
 * Validate a raw (server-facing) tool name.
 *
 * The model-visible `mcp__acp__` prefix is one-way: it must be stripped before
 * `tools/call`. A "raw" name that still contains the prefix is rejected.
 */
export function assertRawToolName(raw: string): string {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new TypeError("raw tool name must be a non-empty string");
  }
  if (raw.includes(BRIDGE_MODEL_PREFIX)) {
    throw new Error(
      `raw tool name must not contain ${BRIDGE_MODEL_PREFIX}`,
    );
  }
  return raw;
}

/** Strip exactly one model-visible prefix from a DSH tool name. */
export function stripModelPrefix(modelVisible: string): string {
  if (!modelVisible.startsWith(BRIDGE_MODEL_PREFIX)) {
    throw new Error(`tool name is not model-visible: ${modelVisible}`);
  }
  const raw = modelVisible.slice(BRIDGE_MODEL_PREFIX.length);
  return assertRawToolName(raw);
}

/** A research record may be referenced by the bridge without owning storage. */
export type BridgeRecordRef = ResearchRecordRef;

// ---------------------------------------------------------------------------
// Structured contract errors
// ---------------------------------------------------------------------------

/**
 * Error class names come from the platform envelope (`authorization`,
 * `invalid-parameter`, `conflict`, `not-prepared`, `retryable`, `unsupported`).
 */
export type BridgeErrorClass =
  | "unsupported"
  | "invalid-parameter"
  | "authorization"
  | "conflict"
  | "not-prepared"
  | "retryable";

/** Stable, machine-readable error carried by a `BridgeContractError`. */
export interface BridgeErrorObject {
  readonly class: BridgeErrorClass;
  readonly code: string;
  readonly params: Readonly<Record<string, unknown>>;
}

/**
 * A bridge-level contract violation (never a scientific result).
 *
 * The bridge refuses locally what the server would refuse remotely: an
 * unmapped operation, a P2 operation without the negotiated extension, a
 * reused idempotency key with changed science, or a payload-supplied approver.
 */
export class BridgeContractError extends Error {
  readonly error: BridgeErrorObject;

  constructor(error: BridgeErrorObject) {
    super(`${error.class}:${error.code}`);
    this.name = "BridgeContractError";
    this.error = error;
  }
}

function contractError(
  cls: BridgeErrorClass,
  code: string,
  params: Readonly<Record<string, unknown>>,
): never {
  throw new BridgeContractError({ class: cls, code, params });
}

// ---------------------------------------------------------------------------
// P1: the frozen eight-tool surface
// ---------------------------------------------------------------------------

/** The eight frozen P1 raw tool names (`tools/call.name`). */
export const P1_TOOL_NAMES: readonly string[] = Object.freeze([
  "acp_capabilities",
  "acp_preflight",
  "acp_submit",
  "acp_status",
  "acp_cancel",
  "acp_prepare_artifact",
  "acp_read_artifact",
  "acp_analyze",
] as const);

export type P1ToolName =
  | "acp_capabilities"
  | "acp_preflight"
  | "acp_submit"
  | "acp_status"
  | "acp_cancel"
  | "acp_prepare_artifact"
  | "acp_read_artifact"
  | "acp_analyze";

/**
 * Explicit phase required by the DA entry paths (plan C T-09).
 *
 * B18a preflight and B20a ready/exec are separate phases and must never share
 * an implicit default; the caller states which phase it is dispatching.
 */
export type BridgePhase = "da-preflight" | "da-submit";

/** One P1 operation translated into exactly one official MCP tool. */
export interface OperationMapping {
  readonly operation: BridgeOperation;
  readonly tool: P1ToolName;
  readonly phase: BridgePhase | null;
  readonly longTask: boolean;
}

const OPERATION_TOOL: Readonly<Record<BridgeOperation, P1ToolName>> =
  Object.freeze({
    discover: "acp_capabilities",
    validate: "acp_preflight",
    submit: "acp_submit",
    status: "acp_status",
    // P1 has no separate "evidence" tool. `acp_analyze` is the only explicit
    // analysis entry (it requires `explicit: true`); `acp_read_artifact` only
    // reads an already-prepared artifact and never analyses. See
    // `p2-operation-map.md` §2 for the justification.
    evidence: "acp_analyze",
    prepare: "acp_prepare_artifact",
    read: "acp_read_artifact",
  });

/** Tools that reserve a long-task slot and return a reference immediately. */
const LONG_TASK_TOOL_SET: ReadonlySet<string> = new Set([
  "acp_submit",
  "acp_prepare_artifact",
  "acp_analyze",
]);

/**
 * Map one bridge operation to the single official MCP tool that performs it.
 *
 * `phase` is optional for ordinary calls and mandatory for a DA dispatch (see
 * `resolveDaPhase`). A phase on an operation that has no phase is refused, so
 * a caller can never "explain away" a mismatch.
 */
export function mapOperationToTool(
  operation: BridgeOperation,
  phase?: BridgePhase,
): OperationMapping {
  const tool = OPERATION_TOOL[operation] as P1ToolName | undefined;
  if (tool === undefined) {
    contractError("invalid-parameter", "unknown_operation", { operation });
  }
  let resolvedPhase: BridgePhase | null = null;
  if (phase !== undefined) {
    if (phase !== "da-preflight" && phase !== "da-submit") {
      contractError("invalid-parameter", "phase_dispatch", {
        rule: "unknown_phase",
        phase,
        operation,
      });
    }
    const requiredFor: BridgeOperation =
      phase === "da-preflight" ? "validate" : "submit";
    if (operation !== requiredFor) {
      contractError("invalid-parameter", "phase_dispatch", {
        rule: "phase_not_applicable",
        phase,
        operation,
        expected_operation: requiredFor,
      });
    }
    resolvedPhase = phase;
  }
  return Object.freeze({
    operation,
    tool,
    phase: resolvedPhase,
    longTask: LONG_TASK_TOOL_SET.has(tool),
  });
}

/**
 * Resolve the explicit DA phase for a preflight/submit dispatch.
 *
 * A missing phase is `phase_dispatch` (never a silently chosen default), which
 * is the C-side half of the acceptance rule "record NOT_VERIFIED when the phase
 * is absent".
 */
export function resolveDaPhase(
  operation: BridgeOperation,
  phase?: BridgePhase,
): BridgePhase {
  const expected: BridgePhase | null =
    operation === "validate"
      ? "da-preflight"
      : operation === "submit"
        ? "da-submit"
        : null;
  if (expected === null) {
    contractError("invalid-parameter", "phase_dispatch", {
      rule: "phase_not_applicable",
      operation,
    });
  }
  if (phase === undefined) {
    contractError("invalid-parameter", "phase_dispatch", {
      rule: "phase_required",
      operation,
      expected_phase: expected,
    });
  }
  if (phase !== expected) {
    contractError("invalid-parameter", "phase_dispatch", {
      rule: "phase_mismatch",
      operation,
      expected_phase: expected,
      observed_phase: phase,
    });
  }
  return expected;
}

/** Model-visible name for a raw tool (`mcp__acp__` + raw name, verbatim). */
export function toModelToolName(raw: string): string {
  return `${BRIDGE_MODEL_PREFIX}${assertRawToolName(raw)}`;
}

// ---------------------------------------------------------------------------
// P2: versioned MCP extension `acp.research.v1` (B03 delivered)
// ---------------------------------------------------------------------------

/**
 * Frozen extension identity from B03's table
 * (`docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json`).
 */
export const BRIDGE_EXTENSION_ID = "acp.research.v1";
export const BRIDGE_EXTENSION_VERSION = "1.0.0";

/** One P2 research operation exposed by the extension. */
export type P2Operation =
  | "proposal.create"
  | "proposal.read"
  | "proposal.update_cas"
  | "approval.request"
  | "approval.query"
  | "approval.approve"
  | "approval.reject"
  | "approval.revoke"
  | "research.wait"
  | "research.events"
  | "research.snapshot"
  | "research.resume";

/** C-plan wiring task that consumes this operation. */
export type P2WiringTask = "T-25" | "T-27" | "T-28" | "T-32";

/** B03 row identity (`P2-R1` … `P2-R5`). */
export type P2Row = "P2-R1" | "P2-R2" | "P2-R3" | "P2-R4" | "P2-R5";

/** One P2 operation mapped to the tool published in B03's committed table. */
export interface P2OperationMapping {
  readonly operation: P2Operation;
  readonly row: P2Row;
  readonly tool: string;
  readonly namespacedId: string;
  readonly toolVersion: string;
  readonly controllerClientMethod: string;
  readonly errorCodes: readonly string[];
  readonly wiringTask: P2WiringTask;
  readonly longTask: boolean;
}

/** Canonical order; the doc table must contain exactly these operations. */
export const P2_OPERATIONS: readonly P2Operation[] = Object.freeze([
  "proposal.create",
  "proposal.read",
  "proposal.update_cas",
  "approval.request",
  "approval.query",
  "approval.approve",
  "approval.reject",
  "approval.revoke",
  "research.wait",
  "research.events",
  "research.snapshot",
  "research.resume",
] as const);

const R1_ERRORS = Object.freeze([
  "authorization",
  "invalid-parameter",
  "conflict",
  "not-prepared",
  "retryable",
] as const);
const R2_ERRORS = Object.freeze([
  "authorization",
  "invalid-parameter",
  "conflict",
  "retryable",
] as const);
const R4_ERRORS = Object.freeze([
  "authorization",
  "invalid-parameter",
  "not-prepared",
  "retryable",
] as const);
const R5_ERRORS = Object.freeze([
  "authorization",
  "invalid-parameter",
  "conflict",
  "retryable",
  "not-prepared",
] as const);

function p2Mapping(
  operation: P2Operation,
  row: P2Row,
  tool: string,
  namespacedId: string,
  controllerClientMethod: string,
  errorCodes: readonly string[],
  wiringTask: P2WiringTask,
  longTask = false,
): P2OperationMapping {
  return Object.freeze({
    operation,
    row,
    tool,
    namespacedId,
    toolVersion: BRIDGE_EXTENSION_VERSION,
    controllerClientMethod,
    errorCodes,
    wiringTask,
    longTask,
  });
}

/**
 * Frozen P2 operation table.
 *
 * Every `tool` is a real name in B03's committed 18-tool allowlist (the eight
 * P1 names plus these ten P2 names). The bridge never invents a tool, never
 * adds an untyped passthrough and never shells out.
 */
export const P2_OPERATION_MAP: Readonly<
  Record<P2Operation, P2OperationMapping>
> = Object.freeze({
  "proposal.create": p2Mapping(
    "proposal.create",
    "P2-R1",
    "acp_research_proposal_create_v1",
    "acp.research.proposal_create",
    "create_proposal",
    R1_ERRORS,
    "T-25",
  ),
  "proposal.read": p2Mapping(
    "proposal.read",
    "P2-R1",
    "acp_research_proposal_read_v1",
    "acp.research.proposal_read",
    "read_proposal",
    R1_ERRORS,
    "T-25",
  ),
  "proposal.update_cas": p2Mapping(
    "proposal.update_cas",
    "P2-R1",
    "acp_research_proposal_update_cas_v1",
    "acp.research.proposal_update_cas",
    "update_proposal_cas",
    R1_ERRORS,
    "T-25",
  ),
  "approval.request": p2Mapping(
    "approval.request",
    "P2-R2",
    "acp_research_approval_request_v1",
    "acp.research.approval_request",
    "request_approval",
    R2_ERRORS,
    "T-27",
  ),
  "approval.query": p2Mapping(
    "approval.query",
    "P2-R2",
    "acp_research_approval_query_v1",
    "acp.research.approval_query",
    "query_approval",
    R2_ERRORS,
    "T-27",
  ),
  "approval.approve": p2Mapping(
    "approval.approve",
    "P2-R3",
    "acp_research_approval_decide_v1",
    "acp.research.approval_decide",
    "decide_approval",
    R2_ERRORS,
    "T-28",
  ),
  "approval.reject": p2Mapping(
    "approval.reject",
    "P2-R3",
    "acp_research_approval_decide_v1",
    "acp.research.approval_decide",
    "decide_approval",
    R2_ERRORS,
    "T-28",
  ),
  "approval.revoke": p2Mapping(
    "approval.revoke",
    "P2-R3",
    "acp_research_approval_revoke_v1",
    "acp.research.approval_revoke",
    "revoke_approval",
    R2_ERRORS,
    "T-28",
  ),
  "research.wait": p2Mapping(
    "research.wait",
    "P2-R4",
    "acp_research_wait_v1",
    "acp.research.wait",
    "wait_research",
    R4_ERRORS,
    "T-32",
    true,
  ),
  "research.events": p2Mapping(
    "research.events",
    "P2-R4",
    "acp_research_wait_v1",
    "acp.research.wait",
    "wait_research",
    R4_ERRORS,
    "T-32",
  ),
  "research.snapshot": p2Mapping(
    "research.snapshot",
    "P2-R4",
    "acp_research_snapshot_v1",
    "acp.research.snapshot",
    "snapshot_research",
    R4_ERRORS,
    "T-32",
  ),
  "research.resume": p2Mapping(
    "research.resume",
    "P2-R5",
    "acp_research_resume_v1",
    "acp.research.resume",
    "resume_research",
    R5_ERRORS,
    "T-32",
  ),
});

const P2_OPERATION_SET: ReadonlySet<string> = new Set(P2_OPERATIONS);

/** True when `value` is a P2 operation name. */
export function isP2Operation(value: string): value is P2Operation {
  return P2_OPERATION_SET.has(value);
}

/** Look up one P2 operation mapping (pure; no negotiation check). */
export function mapP2Operation(operation: P2Operation): P2OperationMapping {
  const mapping = P2_OPERATION_MAP[operation] as
    | P2OperationMapping
    | undefined;
  if (mapping === undefined) {
    contractError("invalid-parameter", "unknown_operation", { operation });
  }
  return mapping;
}

/**
 * A negotiated extension is either the bare id (`"acp.research.v1"`) or the
 * `{id, version}` pair a client asks for. M0-09 (ADR-006 D7) carries that
 * request in a `tools/call` to `acp_capabilities`
 * (`research_extension: {id, version}`), never in `initialize`: the frozen DSH
 * client sends `capabilities: {}` and discards a top-level `initialize`
 * extensions block.
 */
export type NegotiatedExtension =
  | string
  | { readonly id: string; readonly version?: string };

function extensionId(entry: NegotiatedExtension): string {
  return typeof entry === "string" ? entry : entry.id;
}

/** True when the client negotiated the research extension at any version. */
export function negotiatesResearchExtension(
  negotiated: readonly NegotiatedExtension[],
): boolean {
  return negotiated.some((entry) => extensionId(entry) === BRIDGE_EXTENSION_ID);
}

/**
 * Refuse a P2 operation unless `acp.research.v1` was negotiated.
 *
 * P1 operations are always allowed. A negotiated id with a different extension
 * version is refused as `unsupported_capability` too: the bridge does not
 * guess a compatible shape.
 */
export function assertOperationAllowed(
  operation: BridgeOperation | P2Operation,
  negotiated: readonly NegotiatedExtension[],
): void {
  if (!isP2Operation(operation)) {
    return;
  }
  const entry = negotiated.find(
    (candidate) => extensionId(candidate) === BRIDGE_EXTENSION_ID,
  );
  if (entry === undefined) {
    contractError("unsupported", "unsupported_capability", {
      rule: "extension_not_negotiated",
      extension: BRIDGE_EXTENSION_ID,
      operation,
    });
  }
  const version = typeof entry === "string" ? undefined : entry.version;
  if (version !== undefined && version !== BRIDGE_EXTENSION_VERSION) {
    contractError("unsupported", "unsupported_capability", {
      rule: "extension_version_mismatch",
      extension: BRIDGE_EXTENSION_ID,
      expected_version: BRIDGE_EXTENSION_VERSION,
      observed_version: version,
    });
  }
}

// ---------------------------------------------------------------------------
// M0-09: application-level research-extension handshake (tools/call carrier)
// ---------------------------------------------------------------------------

/**
 * The tool that carries the extension handshake.
 *
 * `initialize` makes no extension claim (M0-09/ADR-006 D7); this P1 discovery
 * tool answers `extensions[]` for every capability extension, and the client
 * decides what to do with `implemented=false` / `negotiated=false`.
 */
export const RESEARCH_HANDSHAKE_TOOL = "acp_capabilities";

/** One advertised extension as the server reports it (never inferred). */
export interface ExtensionAdvertisement {
  readonly id: string;
  readonly version: string;
  readonly supported: boolean;
  readonly supportedVersions: readonly string[];
  readonly requestedId: string | null;
  readonly requestedVersion: string | null;
  readonly negotiatedBy: string | null;
  readonly implemented: boolean;
  readonly negotiated: boolean;
  readonly refusal: BridgeErrorObject | null;
}

/**
 * Handshake outcomes.
 *
 * - `negotiated`: the server reports implemented **and** negotiated for the
 *   requested version — the only state that unlocks P2 calls;
 * - `advertised`: the extension is advertised but not negotiable (today: the ten
 *   research tools are a promise, not a registration);
 * - `refused`: the server answered a typed in-band refusal
 *   (`unsupported_capability` / `access_denied`);
 * - `absent`: the server does not advertise this extension at all.
 */
export type ResearchHandshakeStatus =
  | "negotiated"
  | "advertised"
  | "refused"
  | "absent";

/** What a caller asks the server to negotiate (audit facts only besides id). */
export interface ResearchHandshakeRequest {
  readonly id?: string;
  readonly version?: string;
  /** Advisory Workspace hint; the server binding still decides the scope. */
  readonly projectId?: string;
  /** Client identifier recorded by the server for audit only. */
  readonly client?: {
    readonly name: string;
    readonly version?: string;
  };
}

/** Deterministic handshake result; feeds `BridgeOptions.negotiatedExtensions`. */
export interface ResearchHandshake {
  readonly status: ResearchHandshakeStatus;
  readonly extension: ExtensionAdvertisement | null;
  readonly advertised: readonly ExtensionAdvertisement[];
  /** Extensions a `Bridge`/`callP2Operation` may treat as negotiated. */
  readonly negotiatedExtensions: readonly NegotiatedExtension[];
  readonly refusal: BridgeErrorObject | null;
  /** The effective request (id/version actually asked for). */
  readonly requested: { readonly id: string; readonly version: string };
}

function errorClassOf(code: string, rawClass: unknown): BridgeErrorClass {
  if (
    rawClass === "unsupported" ||
    rawClass === "invalid-parameter" ||
    rawClass === "authorization" ||
    rawClass === "conflict" ||
    rawClass === "not-prepared" ||
    rawClass === "retryable"
  ) {
    return rawClass;
  }
  if (code === "access_denied") {
    return "authorization";
  }
  if (code === "unsupported_capability") {
    return "unsupported";
  }
  return "invalid-parameter";
}

function errorObjectFrom(value: unknown): BridgeErrorObject | null {
  if (!isRecord(value)) {
    return null;
  }
  const code = value["code"];
  if (typeof code !== "string" || code.length === 0) {
    return null;
  }
  return {
    class: errorClassOf(code, value["class"]),
    code,
    params: isRecord(value["params"]) ? value["params"] : {},
  };
}

function advertisementFrom(value: unknown): ExtensionAdvertisement | null {
  if (!isRecord(value) || typeof value["id"] !== "string") {
    return null;
  }
  const version = value["version"];
  if (typeof version !== "string" || version.length === 0) {
    return null;
  }
  const requested = isRecord(value["requested"]) ? value["requested"] : null;
  const supportedVersions = Array.isArray(value["supported_versions"])
    ? value["supported_versions"].filter(
        (item): item is string => typeof item === "string",
      )
    : [version];
  return {
    id: value["id"],
    version,
    supported: value["supported"] !== false,
    supportedVersions,
    requestedId:
      requested !== null && typeof requested["id"] === "string"
        ? requested["id"]
        : null,
    requestedVersion:
      requested !== null && typeof requested["version"] === "string"
        ? requested["version"]
        : null,
    negotiatedBy:
      typeof value["negotiated_by"] === "string"
        ? value["negotiated_by"]
        : null,
    implemented: value["implemented"] === true,
    negotiated: value["negotiated"] === true,
    refusal: errorObjectFrom(value["refusal"]),
  };
}

/**
 * Parse the `extensions[]` projection out of a `tools/call` result payload.
 *
 * The projection is read from `data.extensions`; a payload whose `extensions`
 * key is not an array is a server/contract bug and is refused locally instead of
 * being silently treated as "no extensions".
 */
export function parseExtensionAdvertisements(
  structuredContent: Readonly<Record<string, unknown>>,
): readonly ExtensionAdvertisement[] {
  const data = structuredContent["data"];
  if (!isRecord(data) || !Object.hasOwn(data, "extensions")) {
    return Object.freeze([]);
  }
  const raw = data["extensions"];
  if (!Array.isArray(raw)) {
    contractError("invalid-parameter", "bridge_payload_invalid", {
      rule: "extensions_not_an_array",
      tool: RESEARCH_HANDSHAKE_TOOL,
    });
  }
  const entries: ExtensionAdvertisement[] = [];
  for (const item of raw) {
    const advertisement = advertisementFrom(item);
    if (advertisement === null) {
      contractError("invalid-parameter", "bridge_payload_invalid", {
        rule: "extension_entry_invalid",
        tool: RESEARCH_HANDSHAKE_TOOL,
      });
    }
    entries.push(advertisement);
  }
  return Object.freeze(entries);
}

/**
 * Interpret one `acp_capabilities` result as a research handshake.
 *
 * `negotiated` is taken **only** from the server advertisement, and only when
 * the server also reports `implemented: true` at the requested version. A request
 * is therefore never upgraded into a claim by the client, and a typed server
 * refusal (`isError` + `unsupported_capability`/`access_denied`) is surfaced in
 * the result instead of thrown, so a supervisor can continue on P1.
 */
export function parseResearchHandshake(
  result: BridgeToolResult,
  requested: ResearchHandshakeRequest = {},
): ResearchHandshake {
  const advertised = parseExtensionAdvertisements(result.structuredContent);
  const extension =
    advertised.find((entry) => entry.id === BRIDGE_EXTENSION_ID) ?? null;
  const effective = Object.freeze({
    id: requested.id ?? BRIDGE_EXTENSION_ID,
    version: requested.version ?? BRIDGE_EXTENSION_VERSION,
  });
  const serverError = errorObjectFrom(
    isRecord(result.structuredContent["error"])
      ? result.structuredContent["error"]
      : null,
  );

  if (extension === null) {
    return Object.freeze({
      status: "absent" as const,
      extension: null,
      advertised,
      negotiatedExtensions: Object.freeze([]),
      refusal:
        serverError ??
        Object.freeze({
          class: "unsupported" as const,
          code: "unsupported_capability",
          params: Object.freeze({
            rule: "extension_not_advertised",
            extension: BRIDGE_EXTENSION_ID,
          }),
        }),
      requested: effective,
    });
  }

  const negotiated =
    !result.isError &&
    extension.implemented &&
    extension.negotiated &&
    effective.id === extension.id &&
    effective.version === extension.version;

  if (negotiated) {
    return Object.freeze({
      status: "negotiated" as const,
      extension,
      advertised,
      negotiatedExtensions: Object.freeze([
        Object.freeze({ id: extension.id, version: extension.version }),
      ]),
      refusal: null,
      requested: effective,
    });
  }

  const refusal =
    serverError ??
    extension.refusal ??
    Object.freeze({
      class: "unsupported" as const,
      code: "unsupported_capability",
      params: Object.freeze({
        rule: "extension_not_implemented",
        extension: BRIDGE_EXTENSION_ID,
        supported_version: extension.version,
      }),
    });
  return Object.freeze({
    status: result.isError || extension.refusal !== null
      ? ("refused" as const)
      : ("advertised" as const),
    extension,
    advertised,
    negotiatedExtensions: Object.freeze([]),
    refusal,
    requested: effective,
  });
}

/**
 * Ask the server to negotiate the research extension through `tools/call`.
 *
 * Builds the one legal request (`research_extension{id,version}` plus optional
 * audit facts), calls the P1 discovery tool and parses the answer. It never
 * throws for a server refusal: an un-negotiated extension is a normal, typed
 * outcome, because `acp_capabilities` is always callable when the tool exists.
 */
export async function negotiateResearch(
  client: PlatformClient,
  request: ResearchHandshakeRequest = {},
): Promise<ResearchHandshake> {
  const raw = assertRawToolName(RESEARCH_HANDSHAKE_TOOL);
  const args: Record<string, unknown> = {
    research_extension: {
      id: request.id ?? BRIDGE_EXTENSION_ID,
      version: request.version ?? BRIDGE_EXTENSION_VERSION,
    },
  };
  if (request.projectId !== undefined) {
    args["project_id"] = request.projectId;
  }
  if (request.client !== undefined) {
    args["client"] = {
      name: request.client.name,
      ...(request.client.version === undefined
        ? {}
        : { version: request.client.version }),
    };
  }

  let result: BridgeToolResult;
  try {
    result = await client.callTool(raw, Object.freeze(args));
  } catch (error) {
    return Object.freeze({
      status: "refused" as const,
      extension: null,
      advertised: Object.freeze([]),
      negotiatedExtensions: Object.freeze([]),
      refusal: Object.freeze({
        class: "retryable" as const,
        code: "backend_unavailable",
        params: Object.freeze({
          rule: "handshake_call_failed",
          tool: raw,
          message: error instanceof Error ? error.message : String(error),
        }),
      }),
      requested: Object.freeze({
        id: request.id ?? BRIDGE_EXTENSION_ID,
        version: request.version ?? BRIDGE_EXTENSION_VERSION,
      }),
    });
  }
  return parseResearchHandshake(result, request);
}

const PAYLOAD_IDENTITY_KEYS: readonly string[] = Object.freeze([
  "approver",
  "approver_ref",
  "principal",
  "requested_by",
  "role",
  "tenant",
]);

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Refuse any payload-supplied identity.
 *
 * Approval authority comes from the authenticated Controller channel only; a
 * model can never name itself (or anyone else) as the approver.
 */
export function assertNoPayloadIdentity(
  args: Readonly<Record<string, unknown>>,
): void {
  for (const key of PAYLOAD_IDENTITY_KEYS) {
    if (Object.hasOwn(args, key)) {
      contractError("authorization", "access_denied", {
        rule: "approver_identity_from_payload",
        field: key,
      });
    }
  }
}

const WAIT_MAX_SECONDS = 60;
const WAIT_MAX_LIMIT = 200;

/**
 * Build the tool arguments for one P2 operation.
 *
 * Only the caller-provided arguments are forwarded (a shallow copy). The
 * bridge fixes no scientific value and adds no principal: `approve`/`reject`
 * must state a `decision` that matches the mapped operation, waiting is bounded
 * to the B03 limits, and payload identity is refused.
 */
export function buildP2Arguments(
  operation: P2Operation,
  args: Readonly<Record<string, unknown>> = {},
): Readonly<Record<string, unknown>> {
  mapP2Operation(operation);
  assertNoPayloadIdentity(args);
  const params: Record<string, unknown> = { ...args };
  if (operation === "approval.approve" || operation === "approval.reject") {
    const expected = operation === "approval.approve" ? "approve" : "reject";
    const decision = params["decision"];
    if (decision === undefined) {
      contractError("invalid-parameter", "input_invalid", {
        rule: "decision_required",
        operation,
        expected_decision: expected,
      });
    }
    if (decision !== expected) {
      contractError("invalid-parameter", "decision_mismatch", {
        operation,
        expected_decision: expected,
        observed_decision: decision,
      });
    }
  } else if (Object.hasOwn(params, "decision")) {
    contractError("invalid-parameter", "input_invalid", {
      rule: "unexpected_decision",
      operation,
    });
  }
  if (operation === "research.wait") {
    const maxWait = params["max_wait_s"];
    if (
      maxWait !== undefined &&
      (typeof maxWait !== "number" ||
        !Number.isFinite(maxWait) ||
        maxWait < 0 ||
        maxWait > WAIT_MAX_SECONDS)
    ) {
      contractError("invalid-parameter", "bound_exceeded", {
        rule: "max_wait_s",
        max_wait_s: WAIT_MAX_SECONDS,
        observed: maxWait,
      });
    }
    const limit = params["limit"];
    if (
      limit !== undefined &&
      (typeof limit !== "number" ||
        !Number.isInteger(limit) ||
        limit < 1 ||
        limit > WAIT_MAX_LIMIT)
    ) {
      contractError("invalid-parameter", "bound_exceeded", {
        rule: "limit",
        max_limit: WAIT_MAX_LIMIT,
        observed: limit,
      });
    }
  }
  return Object.freeze(params);
}

// ---------------------------------------------------------------------------
// Results: isError and structuredContent are never rewritten
// ---------------------------------------------------------------------------

/** Stable platform reference for a long task (job/operation). */
export interface PlatformReference {
  readonly kind: string;
  readonly id: string;
}

/**
 * Model-facing projection.
 *
 * Deliberately tiny: an outcome derived *only* from `isError`, the platform
 * reference (long tasks) and the artifact digest (large artifacts). There is
 * no synthetic progress/status field, because the server is the only authority
 * on job state.
 */
export interface BridgeProjection {
  readonly tool: string;
  readonly outcome: "ok" | "error";
  readonly isError: boolean;
  readonly reference: PlatformReference | string | null;
  readonly digest: string | null;
  readonly errorCode: string | null;
}

const DIGEST_KEYS: readonly string[] = Object.freeze([
  "digest",
  "artifact_digest",
  "content_digest",
  "proposal_digest",
  "sha256",
]);

function extractReference(value: unknown): PlatformReference | string | null {
  if (typeof value === "string" && value.length > 0) {
    return value;
  }
  if (
    isRecord(value) &&
    typeof value["kind"] === "string" &&
    typeof value["id"] === "string"
  ) {
    return { kind: value["kind"], id: value["id"] };
  }
  return null;
}

function extractDigest(data: unknown): string | null {
  if (!isRecord(data)) {
    return null;
  }
  for (const key of DIGEST_KEYS) {
    const value = data[key];
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
  }
  return null;
}

/**
 * Refuse an inconsistent projection.
 *
 * Exported so a negative test can prove the guard: a projection that reports
 * `outcome: "ok"` while `isError` is true is refused, never promoted.
 */
export function assertBridgeProjectionConsistent(
  projection: BridgeProjection,
): void {
  if (projection.isError && projection.outcome !== "error") {
    contractError("invalid-parameter", "bridge_outcome_invalid", {
      rule: "error_reported_as_success",
      tool: projection.tool,
    });
  }
  if (!projection.isError && projection.outcome !== "ok") {
    contractError("invalid-parameter", "bridge_outcome_invalid", {
      rule: "success_reported_as_error",
      tool: projection.tool,
    });
  }
}

/**
 * Credential-bearing result keys.
 *
 * A tool result may never carry a material credential, even as an opaque
 * value. Approval authority and credentials live in the authenticated
 * Controller channel, not in a model-visible payload.
 */
const SENSITIVE_RESULT_KEY_RE =
  /(?:^|[._-])(?:secret|token|password|passwd|credential|authorization|api[_-]?key|access[_-]?key|private[_-]?key)(?:$|[._-])/i;

/** Values that are recognizably credentials regardless of their key. */
const CREDENTIAL_VALUE_RE =
  /(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,})|(?:sk-[A-Za-z0-9]{16,})|(?:AKIA[0-9A-Z]{16})/;

/** POSIX absolute path or Windows drive path embedded in a result string. */
const ABSOLUTE_PATH_RE =
  /(?:^|[\s"'`=:([{])((?:\/(?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+)|(?:[A-Za-z]:[\\/][^\s"'`)\]}]+))/;

/** Remote/filesystem URI that would disclose a server-side location. */
const REMOTE_URI_RE = /\b(?:sftp|ssh|file):\/\/\S+/i;

/**
 * Refuse a result that leaks a credential or an absolute remote path.
 *
 * The bridge is a model-facing surface: IDs, digests and platform references
 * are allowed, but a secret value or a server filesystem path is not. The
 * guard is a **refusal** (never a silent rewrite), so a caller can decide
 * whether to redact at the transport layer.
 */
export function assertResultRedacted(
  value: unknown,
  path = "$",
): void {
  if (typeof value === "string") {
    if (CREDENTIAL_VALUE_RE.test(value)) {
      contractError("authorization", "access_denied", {
        rule: "credential_in_result",
        path,
      });
    }
    if (REMOTE_URI_RE.test(value) || ABSOLUTE_PATH_RE.test(value)) {
      contractError("authorization", "access_denied", {
        rule: "absolute_path_in_result",
        path,
      });
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((child, index) =>
      assertResultRedacted(child, `${path}[${index}]`),
    );
    return;
  }
  if (!isRecord(value)) {
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    if (SENSITIVE_RESULT_KEY_RE.test(key)) {
      contractError("authorization", "access_denied", {
        rule: "credential_in_result",
        path: `${path}.${key}`,
      });
    }
    assertResultRedacted(child, `${path}.${key}`);
  }
}

/** Project a raw `tools/call` result without rewriting its payload. */
export function projectToolResult(
  tool: string,
  result: BridgeToolResult,
): BridgeProjection {
  assertResultRedacted(result.structuredContent);
  const structured = result.structuredContent;
  const data = structured["data"];
  const error = structured["error"];
  const projection: BridgeProjection = Object.freeze({
    tool,
    outcome: result.isError ? "error" : "ok",
    isError: result.isError,
    reference: isRecord(data) ? extractReference(data["reference"]) : null,
    digest: extractDigest(data),
    errorCode: isRecord(error) && typeof error["code"] === "string"
      ? error["code"]
      : null,
  });
  assertBridgeProjectionConsistent(projection);
  return projection;
}

// ---------------------------------------------------------------------------
// P1 request building and execution
// ---------------------------------------------------------------------------

/** One bridge execution result: the raw envelope plus its projection. */
export interface BridgeResult {
  readonly operation: BridgeOperation;
  readonly tool: string;
  readonly isError: boolean;
  readonly structuredContent: Readonly<Record<string, unknown>>;
  readonly projection: BridgeProjection;
}

/** P2 execution result (same envelope, operation is a P2 name). */
export interface P2BridgeResult {
  readonly operation: P2Operation;
  readonly tool: string;
  readonly isError: boolean;
  readonly structuredContent: Readonly<Record<string, unknown>>;
  readonly projection: BridgeProjection;
}

/**
 * Build the official tool arguments for a P1 request.
 *
 * Only caller-provided scientific values are forwarded. The bridge never fills
 * or switches `method`, charge, spin or resources; an absent value stays
 * absent so the server decides. `generation` is never a server argument: it is
 * a client staleness counter (`tool-semantics.md` §5).
 *
 * `prepare` is built as the v1 HTTP prepare body (`purpose`/`artifact_id`), so
 * the RPH `resource` is sent as `artifact_id` and never as `resource_id`
 * (D-IDENT-10: `resource_id` is server-derived and an unknown request field).
 */
export function buildToolArguments(
  request: BridgeRequest,
  idempotencyKeyOverride?: string,
): Readonly<Record<string, unknown>> {
  const args: Record<string, unknown> = {};
  const idempotencyKey = idempotencyKeyOverride ?? request.idempotencyKey;
  const put = (key: string, value: unknown): void => {
    if (value !== undefined) {
      args[key] = value;
    }
  };
  switch (request.operation) {
    case "discover":
      return Object.freeze(args);
    case "validate":
    case "submit":
    case "evidence":
      put("workflow", request.workflow);
      put("input", request.input);
      put("method", request.method);
      put("project_id", request.project);
      put("resources", request.resources);
      put("idempotency_key", idempotencyKey);
      if (request.operation === "evidence") {
        put("explicit", request.explicit);
      }
      return Object.freeze(args);
    case "status":
      put("reference", request.task);
      return Object.freeze(args);
    case "prepare":
      // D-IDENT-10 (M0-11): the prepare arguments are the v1 HTTP prepare body,
      // not a bridge-local vocabulary. `purpose` is required by the Controller
      // (the RPH prepare operation is always the artifact prepare), the artifact
      // is named `artifact_id` (the Controller derives `resource_id` from it and
      // rejects a client-sent `resource_id` as an unknown field), and
      // `generation` stays client-only.
      put("purpose", "artifact");
      put("job_id", request.task);
      put("attempt", request.attempt);
      put("artifact_id", request.resource);
      put("idempotency_key", idempotencyKey);
      return Object.freeze(args);
    case "read":
      put("job_id", request.task);
      put("attempt", request.attempt);
      put("resource_id", request.resource);
      return Object.freeze(args);
  }
}

/** Artifact identity: `(job, attempt, resource)`; `generation` is client-only. */
export function artifactKey(input: {
  readonly task?: string;
  readonly attempt?: number;
  readonly resource?: string;
}): string {
  return JSON.stringify([
    input.task ?? null,
    input.attempt ?? null,
    input.resource ?? null,
  ]);
}

function canonicalJson(value: unknown): string {
  const encoded = JSON.stringify(value);
  if (value === null || typeof value !== "object") {
    return encoded ?? "null";
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  const record = value as Readonly<Record<string, unknown>>;
  const fields = Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`);
  return `{${fields.join(",")}}`;
}

/** Bridge options; the caller owns negotiation and the read boundary. */
export interface BridgeOptions {
  readonly negotiatedExtensions?: readonly NegotiatedExtension[];
  readonly requirePreparedRead?: boolean;
  /**
   * Storage for derived submit intents (M1-02 / D-IDENT-07).
   *
   * The bridge persists the derived key here *before* it calls the platform, so
   * a crash between derive and call replays the same key.  Defaults to an
   * in-memory store (a cache, not a durable database); a durable caller injects
   * its own `SubmitIntentStore`.
   */
  readonly submitIntentStore?: SubmitIntentStore;
}

function notPreparedResult(request: BridgeRequest): BridgeResult {
  const structuredContent = Object.freeze({
    schema_version: 1,
    operation: "acp_read_artifact",
    status: "error",
    exit_code: 2,
    dry_run: false,
    operation_identity: null,
    diagnostic_ref: null,
    data: null,
    error: Object.freeze({
      code: "not_prepared",
      message: "artifact is not prepared; call prepare before read",
      params: Object.freeze({
        rule: "prepare_required",
        job_id: request.task ?? null,
        attempt: request.attempt ?? null,
        resource_id: request.resource ?? null,
      }),
      retryable: true,
      diagnostic_ref: null,
    }),
  });
  const result: BridgeToolResult = { isError: true, structuredContent };
  return Object.freeze({
    operation: "read",
    tool: "acp_read_artifact",
    isError: true,
    structuredContent,
    projection: projectToolResult("acp_read_artifact", result),
  });
}

/**
 * Ownership record for one prepared artifact.
 *
 * `project` is the caller-supplied hint recorded at prepare time; `read` may
 * not silently switch to a different project or attempt.
 */
interface PreparedArtifact {
  readonly project: string | null;
  readonly task: string;
  readonly attempt: number;
  readonly resource: string;
}

interface IdempotencyEntry {
  readonly signature: string;
  readonly result: BridgeResult | null;
}

function isIdempotentOperation(operation: BridgeOperation): boolean {
  return (
    operation === "validate" ||
    operation === "submit" ||
    operation === "evidence"
  );
}

/**
 * Stateful bridge session.
 *
 * Holds only ephemeral guards, never scientific state:
 * - prepared artifact identities, so `read` never implies `prepare`;
 * - the canonical scientific signature per idempotency key, so a retry with a
 *   changed method/input is a conflict instead of a silent second operation;
 * - the first successful result per idempotency key, replayed without a second
 *   tool call;
 * - the project/attempt recorded when an artifact was prepared.
 *
 * The platform client is injected; the bridge owns no transport, no fetch, no
 * store and no process execution.
 */
export class Bridge {
  private readonly client: PlatformClient;
  private readonly negotiated: readonly NegotiatedExtension[];
  private readonly requirePreparedRead: boolean;
  private readonly prepared = new Set<string>();
  private readonly ownership = new Map<string, PreparedArtifact>();
  private readonly idempotency = new Map<string, IdempotencyEntry>();
  /** Derived submit intents: persisted before the platform call (M1-02). */
  readonly submitIntents: SubmitIntentLedger;

  constructor(client: PlatformClient, options: BridgeOptions = {}) {
    this.client = client;
    this.negotiated = options.negotiatedExtensions ?? [];
    this.requirePreparedRead = options.requirePreparedRead ?? true;
    this.submitIntents = new SubmitIntentLedger(
      options.submitIntentStore ?? new MemorySubmitIntentStore(),
    );
  }

  /** Execute one P1 operation; throws a `BridgeContractError` on a guard hit. */
  async execute(request: BridgeRequest): Promise<BridgeResult> {
    const mapping = mapOperationToTool(request.operation, request.phase);
    const missing = missingFieldsFor(request);
    if (missing.length > 0) {
      contractError("invalid-parameter", "input_invalid", {
        rule: "missing_required_field",
        operation: request.operation,
        fields: missing,
      });
    }
    if (request.operation === "read") {
      this.assertArtifactOwnership(request);
      if (this.requirePreparedRead && !this.prepared.has(artifactKey(request))) {
        return notPreparedResult(request);
      }
    }
    const key = this.resolveSubmitKey(request);
    const replay = this.replayIdempotentResult(request, key);
    if (replay !== null) {
      return replay;
    }
    // An empty key means "no key": never send `idempotency_key: ""`.
    const params = buildToolArguments(request, key.length > 0 ? key : undefined);
    const raw = assertRawToolName(mapping.tool);
    const result = await this.client.callTool(raw, params);
    if (request.operation === "submit" && !result.isError) {
      // The key was already persisted by `resolveSubmitKey`; marking the
      // intent submitted is a second durable fact, not a new key.  A
      // caller-supplied opaque key never went through `prepare`, so the
      // ledger simply reports a miss.
      const identity = resolveSubmitIntentIdentity(request);
      if (identity !== null && key.length > 0) {
        this.submitIntents.markSubmitted({
          project_id: identity.project,
          research_id: identity.research,
          round: identity.round,
        });
      }
    }
    if (request.operation === "prepare" && !result.isError) {
      this.rememberPreparedArtifact(request);
    }
    const bridgeResult: BridgeResult = Object.freeze({
      operation: request.operation,
      tool: raw,
      isError: result.isError,
      structuredContent: result.structuredContent,
      projection: projectToolResult(raw, result),
    });
    this.rememberIdempotentResult(request, key, bridgeResult);
    return bridgeResult;
  }

  /**
   * Resolve the idempotency key a submit will send.
   *
   * A caller key is opaque and forwarded verbatim.  Without one, the bridge
   * derives the D-IDENT-07 key from the research identity and persists it
   * **before** the call; a changed plan digest for the same round is a typed
   * conflict that opens a new round instead of reusing the old key.
   */
  private resolveSubmitKey(request: BridgeRequest): string {
    if (request.operation !== "submit") {
      return request.idempotencyKey ?? "";
    }
    if (request.idempotencyKey !== undefined && request.idempotencyKey.length > 0) {
      return request.idempotencyKey;
    }
    const identity = resolveSubmitIntentIdentity(request);
    if (identity === null) {
      contractError("invalid-parameter", "input_invalid", {
        rule: "missing_required_field",
        operation: "submit",
        fields: ["idempotencyKey"],
      });
    }
    try {
      return this.submitIntents.prepare({
        project_id: identity.project,
        research_id: identity.research,
        round: identity.round,
        plan_definition_digest: identity.planDigest,
      }).intent.key;
    } catch (error) {
      if (error instanceof SubmitIntentConflict) {
        return contractError("conflict", "revision_conflict", error.toParams());
      }
      throw error;
    }
  }

  private rememberPreparedArtifact(request: BridgeRequest): void {
    const key = artifactKey(request);
    this.prepared.add(key);
    this.ownership.set(
      key,
      Object.freeze({
        project: request.project ?? null,
        task: request.task ?? "",
        attempt: request.attempt ?? -1,
        resource: request.resource ?? "",
      }),
    );
  }

  private assertArtifactOwnership(request: BridgeRequest): void {
    const exact = this.ownership.get(artifactKey(request));
    if (request.project !== undefined) {
      const candidate =
        exact ??
        [...this.ownership.values()].find(
          (scope) =>
            scope.task === request.task && scope.resource === request.resource,
        );
      if (
        candidate !== undefined &&
        candidate.project !== null &&
        candidate.project !== request.project
      ) {
        contractError("authorization", "access_denied", {
          rule: "project_ownership_mismatch",
          expected_project: candidate.project,
          observed_project: request.project,
          task: request.task ?? null,
          attempt: request.attempt ?? null,
          resource: request.resource ?? null,
        });
      }
    }
    if (exact === undefined) {
      const otherAttempt = [...this.ownership.values()].find(
        (scope) =>
          scope.task === request.task &&
          scope.resource === request.resource &&
          scope.attempt !== request.attempt,
      );
      if (otherAttempt !== undefined) {
        contractError("authorization", "access_denied", {
          rule: "attempt_ownership_mismatch",
          expected_attempt: otherAttempt.attempt,
          observed_attempt: request.attempt ?? null,
          task: request.task ?? null,
          resource: request.resource ?? null,
        });
      }
    }
  }

  private signatureFor(request: BridgeRequest, key?: string): string | null {
    const resolved = key ?? request.idempotencyKey;
    if (resolved === undefined || resolved.length === 0) {
      return null;
    }
    return canonicalJson({
      workflow: request.workflow ?? null,
      input: request.input ?? null,
      method: request.method ?? null,
      resources: request.resources ?? null,
      project: request.project ?? null,
    });
  }

  private replayIdempotentResult(
    request: BridgeRequest,
    key?: string,
  ): BridgeResult | null {
    if (!isIdempotentOperation(request.operation)) {
      return null;
    }
    const resolvedKey = key ?? request.idempotencyKey;
    if (resolvedKey === undefined || resolvedKey.length === 0) {
      return null;
    }
    const signature = this.signatureFor(request, resolvedKey);
    if (signature === null) {
      return null;
    }
    const previous = this.idempotency.get(resolvedKey);
    if (previous === undefined) {
      this.idempotency.set(resolvedKey, { signature, result: null });
      return null;
    }
    if (previous.signature !== signature) {
      contractError("conflict", "revision_conflict", {
        rule: "idempotency_key_reuse",
        idempotency_key: resolvedKey,
      });
    }
    return previous.result;
  }

  private rememberIdempotentResult(
    request: BridgeRequest,
    key: string,
    result: BridgeResult,
  ): void {
    if (!isIdempotentOperation(request.operation) || result.isError) {
      return;
    }
    const signature = this.signatureFor(request, key);
    if (key.length === 0 || signature === null) {
      return;
    }
    this.idempotency.set(key, { signature, result });
  }

  /** Execute one P2 operation (negotiation is checked before any call). */
  async executeP2(
    operation: P2Operation,
    args: Readonly<Record<string, unknown>> = {},
  ): Promise<P2BridgeResult> {
    assertOperationAllowed(operation, this.negotiated);
    const mapping = mapP2Operation(operation);
    const params = buildP2Arguments(operation, args);
    const raw = assertRawToolName(mapping.tool);
    const result = await this.client.callTool(raw, params);
    return Object.freeze({
      operation,
      tool: raw,
      isError: result.isError,
      structuredContent: result.structuredContent,
      projection: projectToolResult(raw, result),
    });
  }
}

/**
 * Execute one P2 operation with a stateless call (no prepared/signature guard).
 * P2 resources carry their own revision/cursor; the bridge only negotiates,
 * guards identity and forwards typed arguments.
 */
export async function callP2Operation(
  client: PlatformClient,
  operation: P2Operation,
  args: Readonly<Record<string, unknown>> = {},
  negotiated: readonly NegotiatedExtension[] = [],
): Promise<P2BridgeResult> {
  assertOperationAllowed(operation, negotiated);
  const mapping = mapP2Operation(operation);
  const params = buildP2Arguments(operation, args);
  const raw = assertRawToolName(mapping.tool);
  const result = await client.callTool(raw, params);
  return Object.freeze({
    operation,
    tool: raw,
    isError: result.isError,
    structuredContent: result.structuredContent,
    projection: projectToolResult(raw, result),
  });
}

// ---------------------------------------------------------------------------
// Evidence attribution (M1-03, G07/G08): parse and attribute — nothing else
// ---------------------------------------------------------------------------
//
// The bridge is not an evidence model. It parses the platform's
// `acp2.runtime.evidence-projection.v1` payload into the single
// `@rph/research` `EvidenceView` and refuses any view that names a foreign
// project / research / round / task attempt. It never derives a claim status,
// never upgrades an execution status and never persists anything.

/** Attribution the caller knows before it has seen the view. */
export interface EvidenceAttribution {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
}

/**
 * Attribute a task reference to the expected research scope.
 *
 * A foreign project is a typed conflict, never a projection the caller may
 * silently adopt.
 */
export function attributeTaskRef(
  ref: TaskRef,
  expected: EvidenceAttribution,
): TaskRef {
  if (ref.project_id !== expected.project_id) {
    contractError("authorization", "foreign_project", {
      expected_project: expected.project_id,
      observed_project: ref.project_id,
    });
  }
  return ref;
}

/**
 * Parse and attribute one evidence-projection payload.
 *
 * The view must already carry a digest-bound task reference; a foreign
 * project/research/round is refused before any consumer sees a status.
 */
export function attributeEvidenceView(
  payload: unknown,
  expected: EvidenceAttribution,
): EvidenceView {
  const view = assertEvidenceView(payload);
  attributeTaskRef(view.task_ref, expected);
  if (view.research.research_id !== expected.research_id) {
    contractError("authorization", "foreign_research", {
      expected_research: expected.research_id,
      observed_research: view.research.research_id,
    });
  }
  if (view.research.round !== expected.round) {
    contractError("conflict", "foreign_round", {
      expected_round: expected.round,
      observed_round: view.research.round,
    });
  }
  return view;
}

/** Stable key of the attributed attempt (`project|task|attempt|release|digest`). */
export function attributedTaskRefKey(ref: TaskRef): string {
  return taskRefKey(ref);
}

/** Parse a platform task reference through the single normalisation. */
export function parsePlatformTaskRef(
  payload: unknown,
  projectId = "",
): TaskRef {
  return parseTaskRef(payload, projectId);
}
