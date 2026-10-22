/**
 * Bridge surface: research semantics <-> public platform client.
 *
 * The bridge depends only on public JSON shapes and a `PlatformClient`
 * interface. It deliberately contains no transport implementation: no fetch,
 * no DSH source import, no platform store, and no process/scientific
 * execution. A concrete platform client is injected by the caller (CLI or
 * DSH plugin) in a later task.
 */

import type { ResearchRecordRef } from "@rph/research";

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

/** Fields required by the operation but absent/invalid on the request. */
export function missingFieldsFor(request: BridgeRequest): BridgeField[] {
  return requiredFieldsFor(request.operation).filter(
    (field) => !isPresent(field, request),
  );
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
