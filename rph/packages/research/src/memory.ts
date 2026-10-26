/**
 * Research memory: authorization-, source- and version-scoped retrieval with an
 * auditable decision trail.
 *
 * A research-memory entry is a *reference-grade* projection of a persisted
 * ResearchRecord or Proposal: the decision, the plan/task/evidence references
 * and the human edits.  It is never a chat transcript and never carries a
 * prompt or a hidden chain-of-thought — those fields are rejected by
 * `validateMemoryEntry`.
 *
 * The evidence/decision types are **not** duplicated here: they live in
 * `./evidence.ts` (`EvidenceView`, `ResearchDecision`, `DecisionRecord`) and
 * this module only persists projection references and decisions.
 *
 * Retrieval is scoped on three axes at once:
 *   * authorization — the principal must be granted `read` on the queried
 *     project inside its tenant (mirrors the frozen platform access contract);
 *   * source — an optional `research_record` / `proposal` filter;
 *   * version — an exact `revision` or a `min_revision` floor; stale revisions
 *     are never returned.
 *
 * A query the principal is not authorized for returns ZERO hits and records a
 * `denied` audit entry.  Another project's data is never returned, even when a
 * principal can read several projects, because every entry is filtered by the
 * queried project id before any hit is produced.
 *
 * This module has no database, no file access and no subprocess: entries are
 * indexed from values already produced through the T-09 P2 mapping.
 */

import type { ResearchMemoryKind } from "./index.js";
import { memoryKey } from "./index.js";
import { FORBIDDEN_ARCHIVE_FIELDS } from "./evidence.js";
import {
  researchRecordId,
  type ResearchRecord,
  type ValidationIssue,
} from "./record.js";
import type { Proposal } from "./proposal.js";

// ---------------------------------------------------------------------------
// Authorization view (mirrors the frozen platform access contract semantics)
// ---------------------------------------------------------------------------

export const MEMORY_READ_ACTION = "read" as const;

export type MemoryPrincipalKind = "user" | "service" | "anonymous";

/** The minimum principal projection memory retrieval needs. */
export interface MemoryPrincipal {
  readonly principal_id: string;
  readonly kind: MemoryPrincipalKind;
  readonly tenant_id: string;
  /** Projects this principal may read; empty means every project in the tenant. */
  readonly project_ids: readonly string[];
  /** Granted actions; `read` is required for memory retrieval. */
  readonly actions: readonly string[];
}

export type MemoryAuthorizationReason =
  | "granted"
  | "no_grant"
  | "action_not_granted"
  | "tenant_mismatch"
  | "scope_mismatch";

export interface MemoryAuthorization {
  readonly allowed: boolean;
  readonly reason: MemoryAuthorizationReason;
}

/**
 * Authorize a memory read of one project.
 *
 * Deny order is deliberate: a tenant mismatch is reported before any scope
 * check so a caller can never widen the tenant boundary; an anonymous principal
 * never has a grant; a scope list that does not contain the project is a
 * `scope_mismatch` (the cross-project case).
 */
export function authorizeMemoryRead(
  principal: MemoryPrincipal,
  projectId: string,
  tenantId?: string | null,
): MemoryAuthorization {
  if (
    tenantId !== undefined &&
    tenantId !== null &&
    principal.tenant_id !== tenantId
  ) {
    return Object.freeze({ allowed: false, reason: "tenant_mismatch" });
  }
  if (principal.kind === "anonymous") {
    return Object.freeze({ allowed: false, reason: "no_grant" });
  }
  if (!principal.actions.includes(MEMORY_READ_ACTION)) {
    return Object.freeze({ allowed: false, reason: "action_not_granted" });
  }
  if (
    principal.project_ids.length > 0 &&
    !principal.project_ids.includes(projectId)
  ) {
    return Object.freeze({ allowed: false, reason: "scope_mismatch" });
  }
  return Object.freeze({ allowed: true, reason: "granted" });
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isInteger(value: unknown, minimum: number): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    Number.isSafeInteger(value) &&
    value >= minimum
  );
}

function issue(
  issues: ValidationIssue[],
  path: string,
  code: string,
  message: string,
): void {
  issues.push({ path, code, message });
}

function extraKeys(
  value: Readonly<Record<string, unknown>>,
  allowed: ReadonlySet<string>,
): string[] {
  return Object.keys(value).filter((key) => !allowed.has(key));
}

// ---------------------------------------------------------------------------
// Human edits: actor / revision / before / after are always retained
// ---------------------------------------------------------------------------

export interface MemoryHumanEdit {
  readonly actor: string;
  readonly revision: number;
  readonly field_path: string;
  readonly before: unknown;
  readonly after: unknown;
  readonly edited_at: string;
  readonly note?: string | null;
}

const HUMAN_EDIT_FIELDS = new Set([
  "actor",
  "revision",
  "field_path",
  "before",
  "after",
  "edited_at",
  "note",
]);

const TIMESTAMP_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;

/**
 * Validate one human edit.
 *
 * `before` and `after` must be *present* (either may be JSON null) so an audit
 * reader always sees the previous and the new value; `actor` and `revision`
 * identify who changed which revision.
 */
export function validateMemoryHumanEdit(value: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  for (const key of extraKeys(value, HUMAN_EDIT_FIELDS)) {
    issue(issues, `$.${key}`, "additional_property", "not allowed");
  }
  if (!isText(value["actor"])) {
    issue(issues, "$.actor", "type", "must be a non-empty string");
  }
  if (!isInteger(value["revision"], 1)) {
    issue(issues, "$.revision", "type", "must be an integer >= 1");
  }
  if (!isText(value["field_path"])) {
    issue(issues, "$.field_path", "type", "must be a non-empty string");
  }
  if (!Object.hasOwn(value, "before")) {
    issue(issues, "$.before", "required", "before must be present");
  }
  if (!Object.hasOwn(value, "after")) {
    issue(issues, "$.after", "required", "after must be present");
  }
  if (!isText(value["edited_at"]) || !TIMESTAMP_RE.test(value["edited_at"])) {
    issue(issues, "$.edited_at", "format", "must be an RFC3339 date-time");
  }
  const note = value["note"];
  if (note !== undefined && note !== null && typeof note !== "string") {
    issue(issues, "$.note", "type", "must be a string or null");
  }
  return issues;
}

/** Build a frozen, validated human edit (throws on a contract violation). */
export function memoryHumanEdit(input: MemoryHumanEdit): MemoryHumanEdit {
  const issues = validateMemoryHumanEdit(input);
  if (issues.length > 0) {
    throw new TypeError(
      `invalid memory human edit: ${issues
        .map((item) => `${item.path}: [${item.code}] ${item.message}`)
        .join("; ")}`,
    );
  }
  return Object.freeze({ ...input });
}

// ---------------------------------------------------------------------------
// Memory entries
// ---------------------------------------------------------------------------

export const MEMORY_SOURCES = ["research_record", "proposal"] as const;
export type MemorySource = (typeof MEMORY_SOURCES)[number];

export interface MemoryEntry {
  readonly key: string;
  readonly project_id: string;
  readonly kind: ResearchMemoryKind;
  readonly id: string;
  readonly revision: number;
  readonly source: MemorySource;
  /** Auditable decision enum; never a free-form model conclusion. */
  readonly decision: string;
  /** Reference strings only (plan/task/evidence/recipe/approval). */
  readonly references: readonly string[];
  readonly human_edits: readonly MemoryHumanEdit[];
  readonly created_at: string;
}

export const MEMORY_ENTRY_FIELDS: readonly string[] = Object.freeze([
  "key",
  "project_id",
  "kind",
  "id",
  "revision",
  "source",
  "decision",
  "references",
  "human_edits",
  "created_at",
] as const);

/** Validate one memory entry; prompt/CoT archive fields are rejected. */
export function validateMemoryEntry(value: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  for (const key of FORBIDDEN_ARCHIVE_FIELDS) {
    if (Object.hasOwn(value, key)) {
      issue(
        issues,
        `$.${key}`,
        "prompt_archive_forbidden",
        "memory must not archive prompts or hidden chain-of-thought",
      );
    }
  }
  for (const key of extraKeys(value, new Set(MEMORY_ENTRY_FIELDS))) {
    if (!FORBIDDEN_ARCHIVE_FIELDS.includes(key)) {
      issue(issues, `$.${key}`, "additional_property", "not allowed");
    }
  }
  if (!isText(value["key"])) {
    issue(issues, "$.key", "type", "must be a non-empty string");
  }
  if (!isText(value["project_id"])) {
    issue(issues, "$.project_id", "type", "must be a non-empty string");
  }
  const kind = value["kind"];
  if (kind !== "record" && kind !== "proposal") {
    issue(issues, "$.kind", "enum", "must be record or proposal");
  }
  if (!isText(value["id"])) {
    issue(issues, "$.id", "type", "must be a non-empty string");
  }
  if (!isInteger(value["revision"], 0)) {
    issue(issues, "$.revision", "type", "must be a non-negative integer");
  }
  const source = value["source"];
  if (
    typeof source !== "string" ||
    !(MEMORY_SOURCES as readonly string[]).includes(source)
  ) {
    issue(issues, "$.source", "enum", `must be one of ${MEMORY_SOURCES.join(", ")}`);
  }
  if (!isText(value["decision"])) {
    issue(issues, "$.decision", "type", "must be a non-empty string");
  }
  const references = value["references"];
  if (!Array.isArray(references)) {
    issue(issues, "$.references", "type", "must be an array of strings");
  } else {
    references.forEach((item, index) => {
      if (!isText(item)) {
        issue(issues, `$.references[${index}]`, "type", "must be a non-empty string");
      }
    });
  }
  const edits = value["human_edits"];
  if (!Array.isArray(edits)) {
    issue(issues, "$.human_edits", "type", "must be an array");
  } else {
    edits.forEach((item, index) =>
      validateMemoryHumanEdit(item).forEach((nested) =>
        issue(
          issues,
          `$.human_edits[${index}]${nested.path.slice(1)}`,
          nested.code,
          nested.message,
        ),
      ),
    );
  }
  if (!isText(value["created_at"])) {
    issue(issues, "$.created_at", "type", "must be a non-empty string");
  }
  return issues;
}

/** True when a value validates as a memory entry. */
export function isMemoryEntry(value: unknown): value is MemoryEntry {
  return validateMemoryEntry(value).length === 0;
}

function referenceForTask(task: ResearchRecord["task_refs"][number]): string {
  return `task:${task.project_id}/${task.job_id}#${task.attempt}@${task.release_id}`;
}

/** Project a persisted ResearchRecord into an auditable memory entry. */
export function memoryEntryFromRecord(
  record: ResearchRecord,
  humanEdits: readonly MemoryHumanEdit[] = [],
): MemoryEntry {
  const id = researchRecordId(record);
  const references = [
    `plan:${record.plan_definition_digest}`,
    ...record.task_refs.map(referenceForTask),
    ...record.evidence_refs.map(
      (evidence) => `evidence:${evidence.evidence_id}@${evidence.sha256}`,
    ),
  ];
  return Object.freeze({
    key: memoryKey({
      projectId: record.project_id,
      kind: "record",
      id,
      revision: record.record_revision,
    }),
    project_id: record.project_id,
    kind: "record",
    id,
    revision: record.record_revision,
    source: "research_record",
    decision: record.decision,
    references: Object.freeze(references),
    human_edits: Object.freeze([...humanEdits]),
    created_at: record.created_at,
  });
}

/** Project a persisted Proposal revision into an auditable memory entry. */
export function memoryEntryFromProposal(
  proposal: Proposal,
  revision: number,
  humanEdits: readonly MemoryHumanEdit[] = [],
): MemoryEntry {
  const references = [
    `plan:${proposal.plan_definition_digest}`,
    `recipe:${proposal.recipe_digest}`,
    `approval:${proposal.approval_ref ?? "none"}`,
  ];
  return Object.freeze({
    key: memoryKey({
      projectId: proposal.project_id,
      kind: "proposal",
      id: proposal.proposal_id,
      revision,
    }),
    project_id: proposal.project_id,
    kind: "proposal",
    id: proposal.proposal_id,
    revision,
    source: "proposal",
    decision: proposal.decision,
    references: Object.freeze(references),
    human_edits: Object.freeze([...humanEdits]),
    created_at: proposal.created_at,
  });
}

/** One-line reference-only decision summary (never model text). */
export function decisionSummary(entry: MemoryEntry): string {
  return `${entry.kind}:${entry.id}@${entry.revision} decision=${entry.decision} refs=${entry.references.length}`;
}

// ---------------------------------------------------------------------------
// Retrieval
// ---------------------------------------------------------------------------

export interface MemoryQuery {
  readonly principal: MemoryPrincipal;
  readonly project_id: string;
  readonly kind?: ResearchMemoryKind | null;
  readonly source?: MemorySource | null;
  readonly id?: string | null;
  /** Exact revision filter. */
  readonly revision?: number | null;
  /** Inclusive revision floor; lower revisions are stale and excluded. */
  readonly min_revision?: number | null;
  readonly tenant_id?: string | null;
  /** Deterministic clock for tests; defaults to the current time. */
  readonly now?: string | null;
}

export type MemoryAuditAction = "retrieve" | "denied";

export interface MemoryAuditEntry {
  readonly entry_id: string;
  readonly actor: string;
  readonly action: MemoryAuditAction;
  readonly allowed: boolean;
  readonly reason: string;
  readonly project_id: string;
  readonly kind: ResearchMemoryKind | null;
  readonly source: MemorySource | null;
  readonly revision: number | null;
  readonly before: unknown;
  readonly after: unknown;
  readonly hits: number;
  readonly decided_at: string;
  readonly summary: string;
}

export interface MemoryRetrieval {
  readonly allowed: boolean;
  readonly hits: readonly MemoryEntry[];
  readonly audit: MemoryAuditEntry;
}

interface AuditInput {
  readonly actor: string;
  readonly action: MemoryAuditAction;
  readonly allowed: boolean;
  readonly reason: string;
  readonly project_id: string;
  readonly kind: ResearchMemoryKind | null;
  readonly source: MemorySource | null;
  readonly revision: number | null;
  readonly before: unknown;
  readonly after: unknown;
  readonly hits: number;
  readonly decided_at: string;
}

/**
 * In-memory, authorization-scoped index over already-persisted research
 * resources.
 *
 * It is not a second store: it holds projections of values created through the
 * T-09 P2 mapping and writes nothing.  Every `retrieve` appends exactly one
 * audit entry, allowed or denied.
 */
export class ResearchMemory {
  private readonly entries = new Map<string, MemoryEntry>();
  private readonly trail: MemoryAuditEntry[] = [];

  /** Index a validated entry; a prompt/CoT archive throws. */
  indexEntry(entry: MemoryEntry): MemoryEntry {
    const issues = validateMemoryEntry(entry);
    if (issues.length > 0) {
      throw new TypeError(
        `invalid memory entry: ${issues
          .map((item) => `${item.path}: [${item.code}] ${item.message}`)
          .join("; ")}`,
      );
    }
    const frozen = Object.freeze(entry);
    this.entries.set(frozen.key, frozen);
    return frozen;
  }

  indexRecord(
    record: ResearchRecord,
    humanEdits: readonly MemoryHumanEdit[] = [],
  ): MemoryEntry {
    return this.indexEntry(memoryEntryFromRecord(record, humanEdits));
  }

  indexProposal(
    proposal: Proposal,
    revision: number,
    humanEdits: readonly MemoryHumanEdit[] = [],
  ): MemoryEntry {
    return this.indexEntry(memoryEntryFromProposal(proposal, revision, humanEdits));
  }

  /** Number of indexed entries. */
  size(): number {
    return this.entries.size;
  }

  /** Every audit entry recorded so far (allowed and denied). */
  auditEntries(): readonly MemoryAuditEntry[] {
    return Object.freeze([...this.trail]);
  }

  /**
   * Retrieve memory for one project.
   *
   * Cross-project / unauthorized queries return zero hits and record a `denied`
   * audit entry; they never throw and never expose another project's data.
   */
  retrieve(query: MemoryQuery): MemoryRetrieval {
    const tenant = query.tenant_id ?? null;
    const authorization = authorizeMemoryRead(
      query.principal,
      query.project_id,
      tenant,
    );
    const decidedAt = query.now ?? new Date().toISOString();
    if (!authorization.allowed) {
      const audit = this.recordAudit({
        actor: query.principal.principal_id,
        action: "denied",
        allowed: false,
        reason: authorization.reason,
        project_id: query.project_id,
        kind: query.kind ?? null,
        source: query.source ?? null,
        revision: query.revision ?? null,
        before: null,
        after: null,
        hits: 0,
        decided_at: decidedAt,
      });
      return Object.freeze({
        allowed: false,
        hits: Object.freeze([]),
        audit,
      });
    }

    const hits = this.filter(query);
    const revisions = hits.map((entry) => entry.revision);
    const maxRevision = revisions.length > 0 ? Math.max(...revisions) : null;
    const before = query.min_revision ?? null;
    const audit = this.recordAudit({
      actor: query.principal.principal_id,
      action: "retrieve",
      allowed: true,
      reason: authorization.reason,
      project_id: query.project_id,
      kind: query.kind ?? null,
      source: query.source ?? null,
      revision: query.revision ?? maxRevision,
      before,
      after: maxRevision,
      hits: hits.length,
      decided_at: decidedAt,
    });
    return Object.freeze({
      allowed: true,
      hits: Object.freeze(hits),
      audit,
    });
  }

  private filter(query: MemoryQuery): MemoryEntry[] {
    const out: MemoryEntry[] = [];
    for (const entry of this.entries.values()) {
      if (entry.project_id !== query.project_id) continue;
      if (query.kind !== undefined && query.kind !== null && entry.kind !== query.kind) {
        continue;
      }
      if (
        query.source !== undefined &&
        query.source !== null &&
        entry.source !== query.source
      ) {
        continue;
      }
      if (query.id !== undefined && query.id !== null && entry.id !== query.id) {
        continue;
      }
      if (
        query.revision !== undefined &&
        query.revision !== null &&
        entry.revision !== query.revision
      ) {
        continue;
      }
      if (
        query.min_revision !== undefined &&
        query.min_revision !== null &&
        entry.revision < query.min_revision
      ) {
        continue;
      }
      out.push(entry);
    }
    out.sort((left, right) =>
      left.key < right.key ? -1 : left.key > right.key ? 1 : 0,
    );
    return out;
  }

  private recordAudit(input: AuditInput): MemoryAuditEntry {
    const entryId = `mem-audit-${this.trail.length + 1}`;
    const summary = `${input.action}:${input.project_id}:hits=${input.hits}:reason=${input.reason}`;
    const audit = Object.freeze({
      entry_id: entryId,
      summary,
      ...input,
    });
    this.trail.push(audit);
    return audit;
  }
}
