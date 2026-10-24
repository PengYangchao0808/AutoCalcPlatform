/**
 * Optimistic concurrency for versioned research resources.
 *
 * Every write goes through the T-09 P2 operation map
 * (`experience/p2-operation-map.md`): `proposal.create`, `proposal.read` and
 * `proposal.update_cas` map to the three official B03 research tools.  This
 * module owns no transport, no database, no file and no process: the injected
 * `ResearchPlatformClient` is the only way out.
 *
 * `compareAndSwap` never lets a stale record win:
 *   - a ref/revision disagreement is `stale_reference`,
 *   - a next revision that is not exactly +1 is `revision_conflict`,
 *   - a cross-project write is `project_mismatch`,
 *   - a reused round with a different body is `duplicate_round`.
 */

import type { ResearchRecordRef } from "./index.js";
import {
  researchRecordId,
  validateResearchRecord,
  type ResearchRecord,
} from "./record.js";

export const RESEARCH_EXTENSION_ID = "acp.research.v1";
export const RESEARCH_EXTENSION_VERSION = "1.0.0";

/** P2 operations the research store is allowed to call. */
export type ResearchP2Operation =
  | "proposal.create"
  | "proposal.read"
  | "proposal.update_cas";

/**
 * The exact tool names frozen by B03 and mapped by T-09.
 *
 * Kept as data (not an import from `@rph/bridge`) because the dependency
 * direction is `bridge -> research`; the test suite asserts this table equals
 * the compiled bridge map so the two can never drift.
 */
export const RESEARCH_P2_TOOLS: Readonly<
  Record<ResearchP2Operation, string>
> = Object.freeze({
  "proposal.create": "acp_research_proposal_create_v1",
  "proposal.read": "acp_research_proposal_read_v1",
  "proposal.update_cas": "acp_research_proposal_update_cas_v1",
});

export const RESEARCH_P2_OPERATIONS: readonly ResearchP2Operation[] =
  Object.freeze([
    "proposal.create",
    "proposal.read",
    "proposal.update_cas",
  ] as const);

export type StoreErrorCode =
  | "revision_conflict"
  | "stale_reference"
  | "project_mismatch"
  | "duplicate_round"
  | "not_found"
  | "invalid_record"
  | "invalid_proposal"
  | "invalid_parameter"
  | "approval_binding_mismatch"
  | "platform_error";

export interface StoreError {
  readonly code: StoreErrorCode;
  readonly message: string;
  readonly params: Readonly<Record<string, unknown>>;
}

export type StoreResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: StoreError };

/** Public tool envelope; `structuredContent` is never rewritten. */
export interface PlatformToolResult {
  readonly isError: boolean;
  readonly structuredContent: Readonly<Record<string, unknown>>;
}

/** Structural subset of the T-09 `PlatformClient`. */
export interface ResearchPlatformClient {
  callTool(
    rawName: string,
    params: Readonly<Record<string, unknown>>,
  ): Promise<PlatformToolResult>;
}

export interface CasOptions {
  /** Idempotency key for one round event; a replay never writes twice. */
  readonly eventKey?: string;
}

export function storeOk<T>(value: T): StoreResult<T> {
  return Object.freeze({ ok: true, value });
}

export function storeFail(
  code: StoreErrorCode,
  message: string,
  params: Readonly<Record<string, unknown>> = {},
): StoreResult<never> {
  return Object.freeze({
    ok: false,
    error: Object.freeze({ code, message, params: Object.freeze({ ...params }) }),
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorFromContent(
  structuredContent: Readonly<Record<string, unknown>>,
  fallback: string,
): StoreError {
  const rawError = structuredContent["error"];
  if (isRecord(rawError)) {
    const rawCode = rawError["code"];
    const rawParams = rawError["params"];
    const code =
      typeof rawCode === "string" && rawCode.length > 0 ? rawCode : fallback;
    const params = isRecord(rawParams) ? { ...rawParams } : {};
    const message =
      typeof rawError["message"] === "string"
        ? rawError["message"]
        : code;
    return Object.freeze({
      code: mapPlatformCode(code),
      message,
      params: Object.freeze({ ...params, platform_code: code }),
    });
  }
  return Object.freeze({
    code: mapPlatformCode(fallback),
    message: fallback,
    params: Object.freeze({ platform_code: fallback }),
  });
}

function mapPlatformCode(code: string): StoreErrorCode {
  switch (code) {
    case "revision_conflict":
    case "conflict":
      return "revision_conflict";
    case "stale_reference":
      return "stale_reference";
    case "project_mismatch":
      return "project_mismatch";
    case "duplicate_round":
      return "duplicate_round";
    case "not_prepared":
    case "not_found":
      return "not_found";
    case "invalid_record":
    case "invalid_parameter":
      return "invalid_parameter";
    default:
      return "platform_error";
  }
}

/** Call one allowlisted P2 tool and return its raw envelope. */
export async function callResearchTool(
  client: ResearchPlatformClient,
  operation: ResearchP2Operation,
  params: Readonly<Record<string, unknown>>,
): Promise<StoreResult<Readonly<Record<string, unknown>>>> {
  const tool = RESEARCH_P2_TOOLS[operation];
  if (tool === undefined) {
    return storeFail("invalid_parameter", "unknown research operation", {
      operation,
    });
  }
  const result = await client.callTool(tool, params);
  if (result.isError) {
    return {
      ok: false,
      error: errorFromContent(result.structuredContent, operation),
    };
  }
  return storeOk(result.structuredContent);
}

export function recordValidationError(
  issues: ReturnType<typeof validateResearchRecord>,
): StoreError | null {
  const project = issues.find((item) => item.code === "project_mismatch");
  if (project !== undefined) {
    return Object.freeze({
      code: "project_mismatch",
      message: project.message,
      params: Object.freeze({ path: project.path }),
    });
  }
  if (issues.length > 0) {
    return Object.freeze({
      code: "invalid_record",
      message: issues.map((item) => `${item.path}: ${item.message}`).join("; "),
      params: Object.freeze({
        issues: issues.slice(0, 20).map((item) => ({ ...item })),
      }),
    });
  }
  return null;
}

/**
 * Validate the CAS preconditions that do not require a platform call.
 *
 * Exported so the store can report the same structured error before touching
 * the network and a test can assert each cause independently.
 */
export function validateCasNext(
  ref: ResearchRecordRef,
  expectedRevision: number,
  next: ResearchRecord,
): StoreError | null {
  if (
    typeof ref.projectId !== "string" ||
    ref.projectId.length === 0 ||
    typeof ref.recordId !== "string" ||
    ref.recordId.length === 0 ||
    !Number.isInteger(ref.revision) ||
    ref.revision < 0
  ) {
    return Object.freeze({
      code: "invalid_parameter",
      message: "invalid ResearchRecordRef",
      params: Object.freeze({ ref }),
    });
  }
  if (!Number.isInteger(expectedRevision) || expectedRevision < 1) {
    return Object.freeze({
      code: "invalid_parameter",
      message: "expectedRevision must be a positive integer",
      params: Object.freeze({ expectedRevision }),
    });
  }
  const issues = validateResearchRecord(next);
  const invalid = recordValidationError(issues);
  if (invalid !== null) {
    return invalid;
  }
  if (next.project_id !== ref.projectId) {
    return Object.freeze({
      code: "project_mismatch",
      message: `next record project ${next.project_id} != ref project ${ref.projectId}`,
      params: Object.freeze({
        expected_project: ref.projectId,
        observed_project: next.project_id,
      }),
    });
  }
  if (researchRecordId(next) !== ref.recordId) {
    return Object.freeze({
      code: "stale_reference",
      message: "ref does not address this record/round",
      params: Object.freeze({
        expected_record: ref.recordId,
        observed_record: researchRecordId(next),
      }),
    });
  }
  if (ref.revision !== expectedRevision) {
    return Object.freeze({
      code: "stale_reference",
      message: "ref revision is not the expected revision",
      params: Object.freeze({
        ref_revision: ref.revision,
        expected_revision: expectedRevision,
      }),
    });
  }
  if (next.record_revision !== expectedRevision + 1) {
    return Object.freeze({
      code: "revision_conflict",
      message: "next record_revision must be expectedRevision + 1",
      params: Object.freeze({
        expected_revision: expectedRevision,
        next_revision: next.record_revision,
        rule: "invalid_next_revision",
      }),
    });
  }
  return null;
}

/**
 * Compare-and-set one ResearchRecord revision through `proposal.update_cas`.
 *
 * Returns a discriminated result; a concurrent writer or a stale ref is a
 * structured error, never an overwrite.
 */
export async function compareAndSwap(
  client: ResearchPlatformClient,
  ref: ResearchRecordRef,
  expectedRevision: number,
  next: ResearchRecord,
  options: CasOptions = {},
): Promise<StoreResult<ResearchRecord>> {
  const precondition = validateCasNext(ref, expectedRevision, next);
  if (precondition !== null) {
    return { ok: false, error: precondition };
  }
  const params: Record<string, unknown> = {
    kind: "research_record",
    project_id: ref.projectId,
    resource_id: ref.recordId,
    expected_revision: expectedRevision,
    record: next,
  };
  if (options.eventKey !== undefined) {
    params["idempotency_key"] = options.eventKey;
  }
  const response = await callResearchTool(
    client,
    "proposal.update_cas",
    params,
  );
  if (!response.ok) {
    return response;
  }
  const data = response.value["data"];
  const rawRecord = isRecord(data) ? data["record"] : response.value["record"];
  if (!isRecord(rawRecord)) {
    return storeFail("platform_error", "update_cas response has no record", {
      operation: "proposal.update_cas",
    });
  }
  const issues = validateResearchRecord(rawRecord);
  const invalid = recordValidationError(issues);
  if (invalid !== null) {
    return { ok: false, error: invalid };
  }
  const record = rawRecord as unknown as ResearchRecord;
  if (record.project_id !== ref.projectId) {
    return storeFail(
      "project_mismatch",
      "platform returned a record from a different project",
      {
        expected_project: ref.projectId,
        observed_project: record.project_id,
      },
    );
  }
  if (record.record_revision !== expectedRevision + 1) {
    return storeFail(
      "revision_conflict",
      "platform returned a revision different from the requested CAS",
      {
        expected_revision: expectedRevision + 1,
        observed_revision: record.record_revision,
      },
    );
  }
  return storeOk(record);
}
