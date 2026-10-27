/**
 * Bridge surface: research semantics <-> public platform client.
 *
 * The bridge depends only on public JSON shapes and a `PlatformClient`
 * interface. It deliberately contains no transport implementation: no fetch,
 * no DSH source import, no platform store, and no process/scientific
 * execution. A concrete platform client is injected by the caller (CLI or
 * DSH plugin) in a later task.
 */

import { createHash } from "node:crypto";

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
  | "resource"
  | "artifactDigest"
  | "analysisId"
  | "inputRefs";

/**
 * Per-operation required-field matrix (frozen contract, see
 * `tool-semantics.md`, the M1-01 frozen operation table and plan C T-10).
 *
 * - discovery needs none of task/generation;
 * - validate/submit need project + input + idempotency key, and never the
 *   result generation;
 * - status needs task + attempt;
 * - prepare needs task + attempt + resource (the artifact token);
 * - read needs the **full frozen `artifact_ref` identity**: task + attempt +
 *   generation + resource + digest.  A read without the digest is refused
 *   locally, never downgraded to the pre-M1-01 flat `resource_id` spelling the
 *   frozen read schema rejects;
 * - evidence is the **analysis** direction (`acp_analyze`): it needs
 *   `analysis_id` + `input_refs` and `explicit: true`.  Reading an existing
 *   evidence artifact is the `read` operation, which never starts computation.
 */
export const OPERATION_FIELDS: Readonly<
  Record<BridgeOperation, readonly BridgeField[]>
> = Object.freeze({
  discover: Object.freeze([]),
  validate: Object.freeze(["project", "input", "idempotencyKey"] as const),
  submit: Object.freeze(["project", "input", "idempotencyKey"] as const),
  status: Object.freeze(["task", "attempt"] as const),
  prepare: Object.freeze(["task", "attempt", "resource"] as const),
  read: Object.freeze([
    "task",
    "attempt",
    "generation",
    "resource",
    "artifactDigest",
  ] as const),
  evidence: Object.freeze(["analysisId", "inputRefs"] as const),
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
  /**
   * Research-launch binding forwarded to `acp_submit` (M1-06).
   *
   * `planDigest` is the frozen *execution* plan digest; `proposalRef` /
   * `proposalDigest` name the approved proposal.  Without them the submit is a
   * legacy launch: the platform would accept it without an approval, so the
   * bridge forwards them verbatim and never fabricates a value.
   */
  readonly proposalRef?: string;
  readonly proposalDigest?: string;
  /** Approved launch budget (`{unit, count, timeout_s}`), forwarded verbatim. */
  readonly budget?: unknown;
  /** Artifact body path relative to the attempt work dir (prepare only). */
  readonly relativePath?: string;
  /** `sha256:<64 hex>` of the prepared body; with it a read sends `artifact_ref`. */
  readonly artifactDigest?: string;
  /**
   * Explicit prepare purpose (M1-01 frozen prepare table).
   *
   * `artifact` is the only purpose the RPH bridge uses; `structure-input` is
   * forwarded verbatim when a caller explicitly asks for it.  A default is
   * never invented for a caller that omitted it: the builder sends `artifact`
   * because that is what "prepare this artifact" means on this surface.
   */
  readonly purpose?: "artifact" | "structure-input";
  /** Trusted digest expectation for prepare (`sha256:<64 hex>`), forwarded verbatim. */
  readonly expectedDigest?: string;
  /** Trusted size expectation for prepare, forwarded verbatim. */
  readonly expectedSizeBytes?: number;
  /**
   * Frozen analysis identity (M1-01 analyze table): `analysis_id`, `input_refs`
   * and `explicit: true`.  They are what separates "execute an analysis" from
   * "read an existing evidence artifact" (the `read` operation).
   */
  readonly analysisId?: string;
  readonly inputRefs?: readonly unknown[];
  /** Analysis parameters forwarded unchanged to the A-owned analyzer. */
  readonly parameters?: unknown;
  /**
   * Status/cancel reference.  A string is the historical job id; the object
   * form addresses an operation (`{kind:"operation", id}`) returned by a
   * prepare.  When absent, `task` is the reference.
   */
  readonly reference?: string | { readonly kind: string; readonly id: string };
  /** Bounded read size (frozen read table); never silently clamped by the bridge. */
  readonly maxBytes?: number;
  /** Read encoding (`identity` default, `base64` for binary bodies). */
  readonly encoding?: "identity" | "base64";
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
    case "artifactDigest":
      return (
        typeof request.artifactDigest === "string" &&
        request.artifactDigest.length > 0
      );
    case "analysisId":
      return (
        typeof request.analysisId === "string" && request.analysisId.length > 0
      );
    case "inputRefs":
      return Array.isArray(request.inputRefs) && request.inputRefs.length > 0;
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

/**
 * Re-export of the single ADR-003 task reference type (owned by
 * `@rph/research`).
 *
 * A re-export, never a second declaration: the dependency direction is
 * `dsh-bundle -> bridge -> research`, so consumers of the bridge must be able to
 * name the reference without importing `@rph/research` themselves.
 */
export type { TaskRef } from "@rph/research";

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

/**
 * Published status vocabulary that may legitimately live under a
 * credential-sounding key.
 *
 * `data.admission.model_token_enforcement = "NOT_VERIFIED"` and
 * `data.capability_request.authorization = "none: audit and advisory hints
 * only"` are *policy statements*, not secrets (M2-01c/F2).  The exemption is
 * value-shaped and deliberately narrow: an all-caps enum token, or a phrase
 * that contains whitespace.  A single opaque run under a sensitive key (a real
 * credential) is still refused, and `Bearer`/`sk-`/`AKIA` values are refused by
 * `CREDENTIAL_VALUE_RE` regardless of the key.
 */
const PUBLISHED_ENUM_VALUE_RE = /^[A-Z][A-Z0-9_]*$/;

function isPublishedEnumValue(value: unknown): boolean {
  if (typeof value === "boolean" || value === null || typeof value === "number") {
    return true;
  }
  if (typeof value !== "string") {
    return false;
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return true;
  }
  return PUBLISHED_ENUM_VALUE_RE.test(trimmed) || /\s/.test(trimmed);
}

/** POSIX absolute path or Windows drive path embedded in a result string. */
const ABSOLUTE_PATH_RE =
  /(?:^|[\s"'`=:([{])((?:\/(?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+)|(?:[A-Za-z]:[\\/][^\s"'`)\]}]+))/;

/** Remote/filesystem URI that would disclose a server-side location. */
const REMOTE_URI_RE = /\b(?:sftp|ssh|file):\/\/\S+/i;

/**
 * Published reference/id shapes that may appear inside an inert projection.
 *
 * A projection such as `artifact_ref.authorization` names a principal, a
 * project, a scope and an action version — those are identifiers and enums,
 * never key material, so a sensitive-looking *parent* key must not make them
 * unreadable.
 */
const PUBLISHED_REFERENCE_RE =
  /^(?:sha256:[0-9a-f]{64}|acp[_a-z0-9]*|rph[_a-z0-9]*|acp\.[a-z0-9_.]+|rph\.[a-z0-9_.]+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|(?:ap|rsv|res|proj|project|proposal|prop|approval|reservation|research|session|principal|account|workspace|record|release|rel|attempt|epoch|idc|key|op|operation|job|task)_[A-Za-z0-9_.:-]+)$/;

/** A short, readable enum/word (`local-single`, `read`, `NOT_VERIFIED`). */
function isPublishedWord(value: string): boolean {
  return (
    /^[a-z][a-z0-9_-]{0,31}$/.test(value) ||
    PUBLISHED_ENUM_VALUE_RE.test(value) ||
    /\s/.test(value.slice(0, 64))
  );
}

/**
 * Refuse a result that leaks a credential or an absolute remote path.
 *
 * The bridge is a model-facing surface: IDs, digests and platform references
 * are allowed, but a secret value or a server filesystem path is not. The
 * guard is a **refusal** (never a silent rewrite), so a caller can decide
 * whether to redact at the transport layer.
 *
 * A credential is **material**: a scalar.  A *record* under a
 * sensitive-sounding key is an inert platform projection
 * (`artifact_ref.authorization = {principal_id, project_id, scope, action,
 * allowed, authorization_version}`), so it is recursed instead of refused, and
 * inside it only material (long, unstructured, non-identifier) strings are
 * refused.  M3-01b/F2: refusing the projection itself made the prepare/read
 * path unreadable while adding no protection.
 */
export function assertResultRedacted(
  value: unknown,
  path = "$",
  inProjection = false,
): void {
  if (typeof value === "string") {
    if (CREDENTIAL_VALUE_RE.test(value)) {
      contractError("authorization", "access_denied", {
        rule: "credential_in_result",
        path,
      });
    }
    if (
      inProjection &&
      value.trim().length >= 24 &&
      !PUBLISHED_REFERENCE_RE.test(value.trim()) &&
      !isPublishedWord(value.trim())
    ) {
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
      assertResultRedacted(child, `${path}[${index}]`, inProjection),
    );
    return;
  }
  if (!isRecord(value)) {
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    const sensitive = SENSITIVE_RESULT_KEY_RE.test(key);
    if (sensitive && isRecord(child)) {
      // An inert projection: recurse with the material-only rule.
      assertResultRedacted(child, `${path}.${key}`, true);
      continue;
    }
    if (
      sensitive &&
      !isPublishedEnumValue(child) &&
      !CREDENTIAL_VALUE_RE.test(typeof child === "string" ? child : "")
    ) {
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
  /**
   * The full ADR-003 TaskRef when the payload carried a complete one
   * (`submit`/`status`), else `null`.  An idempotent replay returns the very
   * same reference: a retry never re-derives it from a projection.
   */
  readonly taskRef: TaskRef | null;
  /**
   * Set when the platform returned a reference the bridge refused to complete
   * on its own; `acp_status` is the reconciliation authority.
   */
  readonly taskRefReconciliation: TaskRefReconciliation;
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
 * absent so the server decides. `generation` is a frozen `artifact_ref` field
 * (M1-01), so it is forwarded on prepare and read — it is never stripped as a
 * client-only counter.
 *
 * The three artifact/analysis shapes follow the M1-01 frozen table exactly
 * (`docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json`):
 *
 * - `prepare` is the v1 HTTP prepare body (`purpose`/`artifact_id`/
 *   `generation`/`expected_digest`), so the RPH `resource` is sent as
 *   `artifact_id` and never as `resource_id` (D-IDENT-10: `resource_id` is
 *   server-derived and an unknown request field);
 * - `read` sends the complete frozen `artifact_ref`
 *   (`task_id`/`attempt`/`artifact_id`/`generation`/`digest`).  A read without
 *   the digest is a typed local refusal, never the pre-M1-01 flat
 *   `job_id`/`resource_id` spelling (that shape is `input_invalid` on the
 *   frozen schema);
 * - `evidence` is the explicit analyze direction: `analysis_id` + `input_refs`
 *   + `explicit`, with `parameters`/`plan_digest`/`idempotency_key`.  It never
 *   carries the submit fields (`workflow`/`input`/`method`/`resources`), which
 *   the frozen analyze schema rejects as unknown fields.
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
      put("workflow", request.workflow);
      put("input", request.input);
      put("method", request.method);
      put("project_id", request.project);
      put("resources", request.resources);
      put("idempotency_key", idempotencyKey);
      put("plan_digest", request.planDigest);
      put("proposal_ref", request.proposalRef);
      put("proposal_digest", request.proposalDigest);
      put("budget", request.budget);
      return Object.freeze(args);
    case "evidence":
      // M1-01 analyze table: an explicit analysis, never an evidence read.
      put("analysis_id", request.analysisId);
      put("input_refs", request.inputRefs);
      put("explicit", request.explicit);
      put("parameters", request.parameters);
      put("plan_digest", request.planDigest);
      put("idempotency_key", idempotencyKey);
      return Object.freeze(args);
    case "status":
      put("reference", request.reference ?? request.task);
      return Object.freeze(args);
    case "prepare":
      put("purpose", request.purpose ?? "artifact");
      put("job_id", request.task);
      put("attempt", request.attempt);
      put("artifact_id", request.resource);
      put("generation", request.generation);
      put("relative_path", request.relativePath);
      put("expected_digest", request.expectedDigest);
      put("expected_size_bytes", request.expectedSizeBytes);
      put("idempotency_key", idempotencyKey);
      return Object.freeze(args);
    case "read":
      if (!isDigest(request.artifactDigest)) {
        contractError("invalid-parameter", "artifact_ref_incomplete", {
          rule: "artifact_digest_required",
          operation: "read",
          field: "artifact_ref.digest",
          job_id: request.task ?? null,
          attempt: request.attempt ?? null,
          artifact_id: request.resource ?? null,
          generation: request.generation ?? null,
          why: "the frozen artifact_ref requires the digest returned by prepare; a read never guesses it",
        });
      }
      put("artifact_ref", {
        task_id: request.task,
        attempt: request.attempt,
        artifact_id: request.resource,
        generation: request.generation,
        digest: request.artifactDigest,
      });
      put("max_bytes", request.maxBytes);
      put("encoding", request.encoding);
      return Object.freeze(args);
  }
}

const DIGEST_PATTERN_RE = /^sha256:[0-9a-f]{64}$/;

function isDigest(value: unknown): value is string {
  return typeof value === "string" && DIGEST_PATTERN_RE.test(value);
}

const RAW_SHA256_RE = /^[0-9a-f]{64}$/;

/**
 * Accept a digest as the frozen `sha256:<64 hex>` wire value or as the bare
 * 64-hex `sha256` member the status projection carries; never invent one.
 */
function asDigest(value: unknown): string | null {
  if (isDigest(value)) {
    return value;
  }
  if (typeof value === "string" && RAW_SHA256_RE.test(value)) {
    return `sha256:${value}`;
  }
  return null;
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

// ---------------------------------------------------------------------------
// F4 (M3-01b): the explicit availability vocabulary bridge
// ---------------------------------------------------------------------------
//
// The platform `Data Availability` vocabulary (application-api.v1
// `operations.prepare.response.availability`) is
// `absent | preparing | ready | unavailable`, while the frozen experience view
// model (`experience/view-model.schema.json`) speaks
// `unprepared | preparing | readable | failed`.  Before M3-01b a consumer that
// trusted the C vocabulary saw no valid state for a successful prepare
// (`ready`), so a prepared artifact looked unreadable.  The mapping is defined
// **once**, here, and is asserted by `rph/test/m3-01-findings.test.ts`.

/** The four observable availability states of the experience view model. */
export const C_AVAILABILITY_STATES = Object.freeze([
  "unprepared",
  "preparing",
  "readable",
  "failed",
] as const);

/** The platform Data Availability vocabulary (server-owned). */
export const PLATFORM_AVAILABILITY_STATES = Object.freeze([
  "absent",
  "preparing",
  "ready",
  "unavailable",
] as const);

export type ArtifactAvailability = (typeof C_AVAILABILITY_STATES)[number];
export type PlatformAvailability =
  (typeof PLATFORM_AVAILABILITY_STATES)[number];

/**
 * Platform → experience availability, one frozen table.
 *
 * - `absent` → `unprepared` (nothing was prepared yet);
 * - `preparing` → `preparing`;
 * - `ready` → `readable` (this is the F4 miss: the words differ, the fact does
 *   not);
 * - `unavailable` → `failed`.
 */
export const AVAILABILITY_MAP: Readonly<
  Record<PlatformAvailability, ArtifactAvailability>
> = Object.freeze({
  absent: "unprepared",
  preparing: "preparing",
  ready: "readable",
  unavailable: "failed",
});

/**
 * Map one platform availability value onto the experience vocabulary.
 *
 * A value outside both vocabularies is a typed refusal (`availability_unknown`):
 * a fifth state is never invented and an unknown state is never mapped to
 * `readable`.
 */
export function mapAvailability(value: unknown): ArtifactAvailability {
  if (typeof value === "string" && Object.hasOwn(AVAILABILITY_MAP, value)) {
    return AVAILABILITY_MAP[value as PlatformAvailability];
  }
  contractError("invalid-parameter", "bridge_payload_invalid", {
    rule: "availability_unknown",
    observed: value ?? null,
    platform_states: [...PLATFORM_AVAILABILITY_STATES],
  });
}

/** The `availability` member of a tool result payload (or `null`). */
export function availabilityOf(result: BridgeToolResult): unknown {
  const data = result.structuredContent["data"];
  return isRecord(data) ? (data["availability"] ?? null) : null;
}

// ---------------------------------------------------------------------------
// F3 (M3-01b): prepare response → frozen artifact_ref
// ---------------------------------------------------------------------------
//
// The prepare response carries `artifact_ref` in the Controller's prepare
// projection: `resource_id` (the server-derived name), an empty `digest` and
// `availability` before the body exists.  The frozen read contract requires
// `artifact_id` + a digest + the generation.  The rename and the "digest only
// appears once the operation ran" rule are one client mapping, recorded here
// instead of being re-derived (and re-guessed) by every caller.

/** One artifact identity as the client tracks it between prepare and read. */
export interface ArtifactRefView {
  readonly task_id: string;
  readonly attempt: number;
  readonly artifact_id: string;
  /** Monotonic artifact generation within the attempt (`>= 1`). */
  readonly generation: number;
  /** `sha256:<64 hex>` once the prepare operation produced the body. */
  readonly digest: string | null;
  readonly availability: ArtifactAvailability;
  /** Operation reference a caller polls with `status` until `readable`. */
  readonly operation_reference: PlatformReference | null;
}

/**
 * Read one prepare result as an artifact view.
 *
 * Returns `null` when the payload carries no artifact identity at all (a
 * structure-input prepare has none).  The returned view is **not** a fabricated
 * read handle: `digest` stays `null` and `availability` is `unprepared` until
 * the platform reports the body, and `readRequestFor` refuses to build a read
 * from an incomplete view.
 */
export function preparedArtifactView(
  result: BridgeToolResult,
  expected: {
    readonly task: string;
    readonly attempt: number;
    readonly resource: string;
  },
): ArtifactRefView | null {
  const data = result.structuredContent["data"];
  if (!isRecord(data)) {
    return null;
  }
  const raw = isRecord(data["artifact_ref"]) ? data["artifact_ref"] : null;
  const artifactId =
    raw !== null && typeof raw["artifact_id"] === "string"
      ? raw["artifact_id"]
      : raw !== null && typeof raw["resource_id"] === "string"
        ? raw["resource_id"]
        : expected.resource;
  const taskId =
    raw !== null && typeof raw["task_id"] === "string"
      ? raw["task_id"]
      : raw !== null && typeof raw["job_id"] === "string"
        ? raw["job_id"]
        : expected.task;
  if (artifactId.length === 0 || taskId.length === 0) {
    return null;
  }
  const rawAttempt = raw?.["attempt"];
  const attempt =
    typeof rawAttempt === "number" && Number.isInteger(rawAttempt) && rawAttempt >= 1
      ? rawAttempt
      : expected.attempt;
  const rawGeneration = raw?.["generation"];
  const generation =
    typeof rawGeneration === "number" &&
    Number.isInteger(rawGeneration) &&
    rawGeneration >= 1
      ? rawGeneration
      : 1;
  const digest = asDigest(raw?.["digest"]);
  const availability =
    data["availability"] === undefined || data["availability"] === null
      ? digest === null
        ? "unprepared"
        : "readable"
      : mapAvailability(data["availability"]);
  const operation = extractReference(data["operation_ref"] ?? data["reference"]);
  return Object.freeze({
    task_id: taskId,
    attempt,
    artifact_id: artifactId,
    generation,
    digest,
    availability,
    operation_reference:
      operation !== null && typeof operation !== "string" ? operation : null,
  });
}

/**
 * Apply a `status` answer (operation or job) to an artifact view.
 *
 * The poll is how the digest/generation become known; the update never invents
 * them (`digest` stays `null` while the platform has none) and never upgrades
 * an unknown availability.
 */
export function applyArtifactStatus(
  view: ArtifactRefView,
  result: BridgeToolResult,
): ArtifactRefView {
  const data = result.structuredContent["data"];
  if (!isRecord(data)) {
    return view;
  }
  const digest = asDigest(data["sha256"] ?? data["digest"]) ?? view.digest;
  const rawGeneration = data["generation"] ?? data["expected_revision"];
  const generation =
    typeof rawGeneration === "number" &&
    Number.isInteger(rawGeneration) &&
    rawGeneration >= 1
      ? rawGeneration
      : view.generation;
  const availability =
    data["availability"] === undefined || data["availability"] === null
      ? view.availability
      : mapAvailability(data["availability"]);
  return Object.freeze({ ...view, digest, generation, availability });
}

/**
 * Build the read request for a prepared artifact view.
 *
 * A view without a digest is `artifact_ref_incomplete`: the read target must
 * come from the platform, never from a guessed generation or digest.
 */
export function readRequestFor(
  view: ArtifactRefView,
  options: {
    readonly project?: string;
    readonly maxBytes?: number;
    readonly encoding?: "identity" | "base64";
  } = {},
): BridgeRequest {
  if (view.digest === null) {
    contractError("invalid-parameter", "artifact_ref_incomplete", {
      rule: "artifact_digest_required",
      task_id: view.task_id,
      attempt: view.attempt,
      artifact_id: view.artifact_id,
      generation: view.generation,
      availability: view.availability,
      why: "the prepare operation has not reported a digest yet",
    });
  }
  if (view.availability !== "readable") {
    contractError("not-prepared", "resource_not_ready", {
      rule: "artifact_not_readable",
      task_id: view.task_id,
      attempt: view.attempt,
      artifact_id: view.artifact_id,
      availability: view.availability,
    });
  }
  return Object.freeze({
    operation: "read" as const,
    ...(options.project === undefined ? {} : { project: options.project }),
    task: view.task_id,
    attempt: view.attempt,
    resource: view.artifact_id,
    generation: view.generation,
    artifactDigest: view.digest,
    ...(options.maxBytes === undefined ? {} : { maxBytes: options.maxBytes }),
    ...(options.encoding === undefined ? {} : { encoding: options.encoding }),
  });
}

// ---------------------------------------------------------------------------
// TaskRef (ADR-003): one normalizer, no defaulted attempt/release
// ---------------------------------------------------------------------------
//
// `@rph/research` owns the `TaskRef` type and `parseTaskRef`.  The bridge only
// locates the reference inside a tool payload and reports whether it is
// complete; a missing attempt is never attempt 1, a missing release is never
// an empty release, and a legacy non-release job reference is reconciled
// through `status` instead of being guessed.

/** Why a payload is not (yet) a TaskRef. */
export type TaskRefReconciliation = "not_required" | "required" | "failed";

/** The outcome of reading one task reference out of a tool payload. */
export interface TaskRefOutcome {
  readonly taskRef: TaskRef | null;
  /**
   * `not_required`: the payload is complete (or carries no reference at all);
   * `required`: a reference exists but is incomplete, so the caller must
   * reconcile it through `status`; `failed`: the status answer was still
   * incomplete and no further guess is allowed.
   */
  readonly reconciliation: TaskRefReconciliation;
  /** The missing/invalid reference fields, when `reconciliation !== "not_required"`. */
  readonly missing: readonly string[];
}

function taskReferencePayload(
  result: BridgeToolResult,
): Readonly<Record<string, unknown>> | null {
  const data = result.structuredContent["data"];
  if (!isRecord(data)) {
    return null;
  }
  for (const key of ["task_ref", "reference"]) {
    const value = data[key];
    if (isRecord(value)) {
      return value;
    }
  }
  return null;
}

function missingReferenceFields(error: unknown): readonly string[] {
  const message = error instanceof Error ? error.message : String(error);
  const fields: string[] = [];
  for (const match of message.matchAll(/\$([.[\]\w]+):/g)) {
    const field = match[1];
    if (field !== undefined) {
      fields.push(field.replace(/^\./, ""));
    }
  }
  return fields;
}

/**
 * Normalize a task reference out of one tool payload.
 *
 * Never throws for an incomplete reference: an incomplete reference is a
 * `required` reconciliation result, because the platform `status` answer is the
 * only authority that can complete it.
 */
export function taskRefFromPayload(
  payload: unknown,
  projectId = "",
  reconciliation: TaskRefReconciliation = "required",
): TaskRefOutcome {
  if (payload === null || payload === undefined) {
    return Object.freeze({
      taskRef: null,
      reconciliation: "not_required" as const,
      missing: Object.freeze([]),
    });
  }
  try {
    const taskRef = parseTaskRef(payload, projectId);
    return Object.freeze({
      taskRef,
      reconciliation: "not_required" as const,
      missing: Object.freeze([]),
    });
  } catch (error) {
    return Object.freeze({
      taskRef: null,
      reconciliation,
      missing: Object.freeze(missingReferenceFields(error)),
    });
  }
}

/** Normalize the TaskRef of a `submit` result (never defaults a field). */
export function submitTaskRef(
  result: BridgeToolResult,
  projectId = "",
): TaskRefOutcome {
  return taskRefFromPayload(
    taskReferencePayload(result),
    projectId,
    "required",
  );
}

/**
 * Reconcile an incomplete submit reference through a `status` answer.
 *
 * The known identity (`project`, `attempt`) narrows the parse; anything the
 * status answer still does not carry stays missing (`failed`) instead of being
 * defaulted.
 */
export function reconcileTaskRef(
  status: BridgeToolResult,
  expected: {
    readonly projectId?: string;
    readonly taskId?: string;
    readonly attempt?: number;
    /**
     * Release identity the caller *declared* (a real release builder fact).
     *
     * It completes the reference only when neither the submit answer nor the
     * status answer carries one; when it is absent the outcome stays `failed`
     * rather than inventing an empty/pending release.
     */
    readonly release?: { readonly release_id: string; readonly release_digest: string };
  } = {},
): TaskRefOutcome {
  const payload = taskReferencePayload(status);
  if (payload === null) {
    return Object.freeze({
      taskRef: null,
      reconciliation: "failed" as const,
      missing: Object.freeze(["task_ref"]),
    });
  }
  const merged: Record<string, unknown> = { ...payload };
  if (!Object.hasOwn(merged, "task_id") && expected.taskId !== undefined) {
    merged["task_id"] = expected.taskId;
  }
  if (!Object.hasOwn(merged, "attempt") && expected.attempt !== undefined) {
    merged["attempt"] = expected.attempt;
  }
  const hasRelease =
    isRecord(merged["release"]) ||
    Object.hasOwn(merged, "release_id") ||
    Object.hasOwn(merged, "release_digest");
  if (!hasRelease && expected.release !== undefined) {
    merged["release"] = {
      release_id: expected.release.release_id,
      release_digest: expected.release.release_digest,
    };
  }
  return taskRefFromPayload(merged, expected.projectId ?? "", "failed");
}

/**
 * The complete research binding of one submit (F5, M3-01b).
 *
 * RPH attribution never depends on the plaintext `project_id`/`approval_ref`
 * members of the MCP response (the machine envelope redacts long opaque ids):
 * the project comes from the digest-bound **TaskRef**, the research identity and
 * approval reference from the caller's own records, and the response is only
 * ever read for the attempt identity.
 */
export interface ResearchSubmitBinding {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly approval_ref: string | null;
  readonly task_ref: TaskRef;
}

/** Attribution the caller already knows (never taken from a response body). */
export interface SubmitBindingExpectation {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly approval_ref?: string | null;
}

/**
 * Bind one submit result to the expected research scope.
 *
 * A foreign project in the TaskRef is refused (`foreign_project`); a redacted
 * `project_id`/`approval_ref` member in `data` is ignored rather than treated
 * as a mismatch, because the TaskRef — not the response summary — is the
 * platform's attribution fact.
 */
export function attributedSubmitBinding(
  result: BridgeToolResult,
  expected: SubmitBindingExpectation,
): ResearchSubmitBinding {
  const outcome = submitTaskRef(result, expected.project_id);
  if (outcome.taskRef === null) {
    contractError("invalid-parameter", "task_ref_incomplete", {
      rule: "task_ref_reconciliation_required",
      missing: [...outcome.missing],
      reconciliation: outcome.reconciliation,
      project_id: expected.project_id,
      why: "reconcile the reference through acp_status; the bridge never defaults attempt/release",
    });
  }
  const taskRef = outcome.taskRef;
  if (taskRef.project_id !== expected.project_id) {
    contractError("authorization", "foreign_project", {
      expected_project: expected.project_id,
      observed_project: taskRef.project_id,
      task_id: taskRef.task_id,
    });
  }
  return Object.freeze({
    project_id: taskRef.project_id,
    research_id: expected.research_id,
    round: expected.round,
    approval_ref: expected.approval_ref ?? null,
    task_ref: taskRef,
  });
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

/** M1-01b wire-key grammar: 8..128 chars from `[A-Za-z0-9._:-]`. */
const WIRE_IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9._:-]{8,128}$/;

/**
 * Project the M1-02 intent key onto the M1-01b wire-key grammar.
 *
 * The RPH intent key is the durable, human-auditable identity
 * (`project:record:research:round-N:submit:plan-<hex>`); the wire only needs a
 * stable high-entropy opaque key.  The frozen layout can exceed the 128-char
 * wire limit, so when it does the bridge sends a deterministic `rph.<sha256>`
 * projection of the *persisted* intent key.  The same intent always maps to the
 * same wire key, a changed plan digest still conflicts before any call, and the
 * platform never sees a truncated or re-derived value.
 */
export function submitWireKey(intentKey: string): string {
  if (WIRE_IDEMPOTENCY_KEY_RE.test(intentKey)) {
    return intentKey;
  }
  return "rph." + createHash("sha256").update(intentKey).digest("hex");
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
    taskRef: null,
    taskRefReconciliation: "not_required" as const,
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
  /**
   * In-flight executions per `(operation, key)`.
   *
   * Two concurrent identical writes join one platform call instead of racing
   * two: the platform ledger would replay the second one anyway, but a client
   * that "calls twice" is exactly what G21 forbids observing as success.
   */
  private readonly inFlight = new Map<string, Promise<BridgeResult>>();
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
    this.assertEvidenceIsAnalysis(request);
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
    const flightKey = key.length > 0 ? `${request.operation}:${key}` : "";
    if (flightKey.length > 0) {
      const joined = this.inFlight.get(flightKey);
      if (joined !== undefined) {
        return joined;
      }
    }
    const flight = this.dispatch(request, raw, params, key);
    if (flightKey.length > 0) {
      this.inFlight.set(flightKey, flight);
    }
    try {
      return await flight;
    } finally {
      if (flightKey.length > 0) {
        this.inFlight.delete(flightKey);
      }
    }
  }

  /** One platform call plus the ledger/ownership/result bookkeeping. */
  private async dispatch(
    request: BridgeRequest,
    raw: string,
    params: Readonly<Record<string, unknown>>,
    key: string,
  ): Promise<BridgeResult> {
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
    const reference =
      request.operation === "submit" || request.operation === "status"
        ? submitTaskRef(result, request.project ?? "")
        : Object.freeze({
            taskRef: null,
            reconciliation: "not_required" as const,
            missing: Object.freeze([]),
          });
    const bridgeResult: BridgeResult = Object.freeze({
      operation: request.operation,
      tool: raw,
      isError: result.isError,
      structuredContent: result.structuredContent,
      projection: projectToolResult(raw, result),
      taskRef: reference.taskRef,
      taskRefReconciliation: reference.reconciliation,
    });
    this.rememberIdempotentResult(request, key, bridgeResult);
    return bridgeResult;
  }

  /**
   * Keep "read an existing evidence artifact" and "execute an analysis" apart.
   *
   * `evidence` maps to `acp_analyze` (the only explicit analysis entry), so it
   * must state the frozen analysis identity.  A caller that only names a task
   * is reading evidence, which is the `read` operation; the bridge refuses the
   * ambiguous request instead of letting a read start computation.
   */
  private assertEvidenceIsAnalysis(request: BridgeRequest): void {
    if (request.operation !== "evidence") {
      return;
    }
    if (request.analysisId === undefined && request.inputRefs === undefined) {
      if (request.task !== undefined || request.resource !== undefined) {
        contractError("invalid-parameter", "evidence_read_is_not_analysis", {
          rule: "use_read_operation",
          operation: "evidence",
          why: "reading an existing evidence artifact is `read` with a complete artifact_ref; `evidence` is an explicit analysis",
          field: "analysis_id",
        });
      }
      return;
    }
    // The frozen analyze table requires `explicit: true`; a heuristic/implicit
    // analysis is refused locally as well as on the server.
    if (request.explicit !== true) {
      contractError("invalid-parameter", "input_invalid", {
        rule: "explicit_required",
        operation: "evidence",
        field: "explicit",
      });
    }
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
      const prepared = this.submitIntents.prepare({
        project_id: identity.project,
        research_id: identity.research,
        round: identity.round,
        plan_definition_digest: identity.planDigest,
      });
      return submitWireKey(prepared.intent.key);
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
      operation: request.operation,
      workflow: request.workflow ?? null,
      input: request.input ?? null,
      method: request.method ?? null,
      resources: request.resources ?? null,
      project: request.project ?? null,
      plan_digest: request.planDigest ?? null,
      proposal_ref: request.proposalRef ?? null,
      proposal_digest: request.proposalDigest ?? null,
      budget: request.budget ?? null,
      analysis_id: request.analysisId ?? null,
      input_refs: request.inputRefs ?? null,
      parameters: request.parameters ?? null,
    });
  }

  /**
   * Namespace of one idempotent operation: `(operation, key)`.
   *
   * The raw key is a **global** namespace on the platform (M1-01b), so reusing
   * one key for a preflight and then a submit must not replay the preflight
   * result as if it were a submit.  The bridge keys its replay cache by
   * operation too, so two different operations never share a result.
   */
  private idempotencyScope(request: BridgeRequest, key: string): string {
    return `${request.operation}:${key}`;
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
    const scope = this.idempotencyScope(request, resolvedKey);
    const previous = this.idempotency.get(scope);
    if (previous === undefined) {
      this.idempotency.set(scope, { signature, result: null });
      return null;
    }
    if (previous.signature !== signature) {
      contractError("conflict", "revision_conflict", {
        rule: "idempotency_key_reuse",
        idempotency_key: resolvedKey,
        operation: request.operation,
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
    this.idempotency.set(this.idempotencyScope(request, key), { signature, result });
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
