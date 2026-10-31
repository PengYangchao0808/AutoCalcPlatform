/**
 * ResearchRecord model and validation.
 *
 * The field names, required set and `not` clauses are the frozen T-03
 * contract (`experience/research-record.schema.json`).  This module owns only
 * the persisted scientific/provenance record: a DSH chat/session is not a
 * ResearchRecord and session fields are rejected.
 *
 * A record is stored by the platform behind the P2 resource operations; this
 * module never reads a file, opens a database or shells out.
 */

import type { ResearchRecordRef } from "./index.js";
import { RESEARCH_DECISIONS, type ResearchDecision } from "./evidence.js";

export const RESEARCH_RECORD_SCHEMA = "acp2.experience.research-record.v1";

export interface StepIdentityRef {
  readonly step_index: number;
  readonly step_identity: string;
  readonly implementation_version: string;
  readonly upstream_digests: readonly string[];
  readonly materialized_parameters_digest: string;
}

/**
 * The frozen ResearchRecord task reference: flat and **without** a release
 * digest. It is not a `TaskRef` (see `./evidence.ts`): a record reference only
 * becomes one when the verified release digest is supplied explicitly.
 */
export interface RecordTaskRef {
  readonly project_id: string;
  readonly job_id: string;
  readonly attempt: number;
  readonly release_id: string;
}

export interface EvidenceRef {
  readonly evidence_id: string;
  readonly kind: string;
  readonly sha256: string;
  readonly relative_path?: string | null;
}

export interface MissingValidation {
  readonly field_path: string;
  readonly reason: string;
  readonly blocking: boolean;
}

export interface HumanEdit {
  readonly edited_at: string;
  readonly field_path: string;
  readonly editor_ref: string;
  readonly note?: string | null;
}

export interface ResearchRecord {
  readonly research_id: string;
  readonly project_id: string;
  readonly round: number;
  readonly decision: ResearchDecision;
  readonly plan_definition_digest: string;
  readonly step_identity_refs: readonly StepIdentityRef[];
  readonly task_refs: readonly RecordTaskRef[];
  readonly evidence_refs: readonly EvidenceRef[];
  readonly missing_validation: readonly MissingValidation[];
  readonly human_edits: readonly HumanEdit[];
  readonly approval_ref: string | null;
  readonly created_at: string;
  readonly record_revision: number;
}

export interface ValidationIssue {
  readonly path: string;
  readonly code: string;
  readonly message: string;
}

export const RESEARCH_RECORD_FIELDS: readonly string[] = Object.freeze([
  "research_id",
  "project_id",
  "round",
  "decision",
  "plan_definition_digest",
  "step_identity_refs",
  "task_refs",
  "evidence_refs",
  "missing_validation",
  "human_edits",
  "approval_ref",
  "created_at",
  "record_revision",
] as const);

/** Chat/session fields that can never be record authority. */
export const FORBIDDEN_SESSION_FIELDS: readonly string[] = Object.freeze([
  "session_id",
  "chat_id",
  "messages",
  "transcript",
  "conversation",
  "model_role",
] as const);

const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const RESEARCH_ID_RE = /^res-[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const TIMESTAMP_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;

const STEP_IDENTITY_FIELDS = new Set([
  "step_index",
  "step_identity",
  "implementation_version",
  "upstream_digests",
  "materialized_parameters_digest",
]);
const TASK_REF_FIELDS = new Set([
  "project_id",
  "job_id",
  "attempt",
  "release_id",
]);
const EVIDENCE_REF_FIELDS = new Set([
  "evidence_id",
  "kind",
  "sha256",
  "relative_path",
]);
const MISSING_VALIDATION_FIELDS = new Set([
  "field_path",
  "reason",
  "blocking",
]);
const HUMAN_EDIT_FIELDS = new Set([
  "edited_at",
  "field_path",
  "editor_ref",
  "note",
]);

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

function isDigest(value: unknown): value is string {
  return typeof value === "string" && DIGEST_RE.test(value);
}

function isTimestamp(value: unknown): value is string {
  return typeof value === "string" && TIMESTAMP_RE.test(value);
}

function extraKeys(
  value: Readonly<Record<string, unknown>>,
  allowed: ReadonlySet<string>,
): string[] {
  return Object.keys(value).filter((key) => !allowed.has(key));
}

function issue(
  issues: ValidationIssue[],
  path: string,
  code: string,
  message: string,
): void {
  issues.push({ path, code, message });
}

function validateStepIdentity(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, path, "type", "must be an object");
    return;
  }
  for (const key of extraKeys(value, STEP_IDENTITY_FIELDS)) {
    issue(issues, `${path}.${key}`, "additional_property", "not allowed");
  }
  if (!isInteger(value["step_index"], 0)) {
    issue(issues, `${path}.step_index`, "type", "must be a non-negative integer");
  }
  if (!isText(value["step_identity"])) {
    issue(issues, `${path}.step_identity`, "type", "must be a non-empty string");
  }
  if (!isText(value["implementation_version"])) {
    issue(
      issues,
      `${path}.implementation_version`,
      "type",
      "must be a non-empty string",
    );
  }
  const upstream = value["upstream_digests"];
  if (!Array.isArray(upstream) || upstream.length === 0) {
    issue(
      issues,
      `${path}.upstream_digests`,
      "type",
      "must be a non-empty array of digests",
    );
  } else {
    upstream.forEach((digest, index) => {
      if (!isDigest(digest)) {
        issue(
          issues,
          `${path}.upstream_digests[${index}]`,
          "pattern",
          "must be sha256:<64 lowercase hex>",
        );
      }
    });
  }
  if (!isDigest(value["materialized_parameters_digest"])) {
    issue(
      issues,
      `${path}.materialized_parameters_digest`,
      "pattern",
      "must be sha256:<64 lowercase hex>",
    );
  }
}

function validateTaskRef(
  value: unknown,
  path: string,
  recordProject: string | null,
  issues: ValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, path, "type", "must be a RecordTaskRef object, not a string");
    return;
  }
  for (const key of extraKeys(value, TASK_REF_FIELDS)) {
    issue(issues, `${path}.${key}`, "additional_property", "not allowed");
  }
  if (!isText(value["project_id"])) {
    issue(issues, `${path}.project_id`, "type", "must be a non-empty string");
  } else if (recordProject !== null && value["project_id"] !== recordProject) {
    issue(
      issues,
      `${path}.project_id`,
      "project_mismatch",
      `task_ref project ${String(value["project_id"])} != record project ${recordProject}`,
    );
  }
  if (!isText(value["job_id"])) {
    issue(issues, `${path}.job_id`, "type", "must be a non-empty string");
  }
  if (!isInteger(value["attempt"], 1)) {
    issue(issues, `${path}.attempt`, "type", "must be an integer >= 1");
  }
  if (!isText(value["release_id"])) {
    issue(issues, `${path}.release_id`, "type", "must be a non-empty string");
  }
}

function validateEvidenceRef(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, path, "type", "must be an object");
    return;
  }
  for (const key of extraKeys(value, EVIDENCE_REF_FIELDS)) {
    issue(issues, `${path}.${key}`, "additional_property", "not allowed");
  }
  if (!isText(value["evidence_id"])) {
    issue(issues, `${path}.evidence_id`, "type", "must be a non-empty string");
  }
  if (!isText(value["kind"])) {
    issue(issues, `${path}.kind`, "type", "must be a non-empty string");
  }
  if (!isDigest(value["sha256"])) {
    issue(issues, `${path}.sha256`, "pattern", "must be sha256:<64 lowercase hex>");
  }
  const relative = value["relative_path"];
  if (
    relative !== undefined &&
    relative !== null &&
    typeof relative !== "string"
  ) {
    issue(issues, `${path}.relative_path`, "type", "must be a string or null");
  }
}

function validateMissingValidation(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, path, "type", "must be an object");
    return;
  }
  for (const key of extraKeys(value, MISSING_VALIDATION_FIELDS)) {
    issue(issues, `${path}.${key}`, "additional_property", "not allowed");
  }
  if (!isText(value["field_path"])) {
    issue(issues, `${path}.field_path`, "type", "must be a non-empty string");
  }
  if (!isText(value["reason"])) {
    issue(issues, `${path}.reason`, "type", "must be a non-empty string");
  }
  if (typeof value["blocking"] !== "boolean") {
    issue(issues, `${path}.blocking`, "type", "must be a boolean");
  }
}

function validateHumanEdit(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, path, "type", "must be an object");
    return;
  }
  for (const key of extraKeys(value, HUMAN_EDIT_FIELDS)) {
    issue(issues, `${path}.${key}`, "additional_property", "not allowed");
  }
  if (!isTimestamp(value["edited_at"])) {
    issue(issues, `${path}.edited_at`, "format", "must be an RFC3339 date-time");
  }
  if (!isText(value["field_path"])) {
    issue(issues, `${path}.field_path`, "type", "must be a non-empty string");
  }
  if (!isText(value["editor_ref"])) {
    issue(issues, `${path}.editor_ref`, "type", "must be a non-empty string");
  }
  const note = value["note"];
  if (note !== undefined && note !== null && typeof note !== "string") {
    issue(issues, `${path}.note`, "type", "must be a string or null");
  }
}

/**
 * Validate one ResearchRecord against the frozen field set.
 *
 * Returns every issue at once so a caller can report all contract violations
 * instead of stopping at the first.  The `code` value is stable:
 * `project_mismatch` is promoted to the store's structured CAS error code.
 */
export function validateResearchRecord(value: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  for (const key of FORBIDDEN_SESSION_FIELDS) {
    if (Object.hasOwn(value, key)) {
      issue(
        issues,
        `$.${key}`,
        "session_authority",
        "a DSH chat/session is not a ResearchRecord",
      );
    }
  }
  for (const key of extraKeys(
    value,
    new Set(RESEARCH_RECORD_FIELDS),
  )) {
    if (!FORBIDDEN_SESSION_FIELDS.includes(key)) {
      issue(issues, `$.${key}`, "additional_property", "not allowed");
    }
  }

  const researchId = value["research_id"];
  if (typeof researchId !== "string" || !RESEARCH_ID_RE.test(researchId)) {
    issue(
      issues,
      "$.research_id",
      "pattern",
      "must match ^res-[A-Za-z0-9][A-Za-z0-9._:-]*$",
    );
  }
  const project = value["project_id"];
  if (!isText(project)) {
    issue(issues, "$.project_id", "type", "must be a non-empty string");
  }
  if (!isInteger(value["round"], 0)) {
    issue(issues, "$.round", "type", "must be a non-negative integer");
  }
  const decision = value["decision"];
  if (
    typeof decision !== "string" ||
    !(RESEARCH_DECISIONS as readonly string[]).includes(decision)
  ) {
    issue(
      issues,
      "$.decision",
      "enum",
      `must be one of ${RESEARCH_DECISIONS.join(", ")}`,
    );
  }
  if (!isDigest(value["plan_definition_digest"])) {
    issue(
      issues,
      "$.plan_definition_digest",
      "pattern",
      "must be sha256:<64 lowercase hex>",
    );
  }

  const projectText = isText(project) ? project : null;
  const stepRefs = value["step_identity_refs"];
  if (!Array.isArray(stepRefs) || stepRefs.length === 0) {
    issue(
      issues,
      "$.step_identity_refs",
      "type",
      "must be a non-empty array",
    );
  } else {
    stepRefs.forEach((item, index) =>
      validateStepIdentity(item, `$.step_identity_refs[${index}]`, issues),
    );
  }

  const taskRefs = value["task_refs"];
  if (!Array.isArray(taskRefs)) {
    issue(issues, "$.task_refs", "type", "must be an array of RecordTaskRef objects");
  } else {
    taskRefs.forEach((item, index) =>
      validateTaskRef(item, `$.task_refs[${index}]`, projectText, issues),
    );
  }

  for (const field of ["evidence_refs", "missing_validation", "human_edits"]) {
    if (!Array.isArray(value[field])) {
      issue(issues, `$.${field}`, "type", "must be an array");
    }
  }
  if (Array.isArray(value["evidence_refs"])) {
    value["evidence_refs"].forEach((item, index) =>
      validateEvidenceRef(item, `$.evidence_refs[${index}]`, issues),
    );
  }
  if (Array.isArray(value["missing_validation"])) {
    value["missing_validation"].forEach((item, index) =>
      validateMissingValidation(
        item,
        `$.missing_validation[${index}]`,
        issues,
      ),
    );
  }
  if (Array.isArray(value["human_edits"])) {
    value["human_edits"].forEach((item, index) =>
      validateHumanEdit(item, `$.human_edits[${index}]`, issues),
    );
  }

  const approval = value["approval_ref"];
  if (approval !== null && !isText(approval)) {
    issue(issues, "$.approval_ref", "type", "must be a string or null");
  }
  if (!isTimestamp(value["created_at"])) {
    issue(issues, "$.created_at", "format", "must be an RFC3339 date-time");
  }
  if (!isInteger(value["record_revision"], 1)) {
    issue(
      issues,
      "$.record_revision",
      "type",
      "must be an integer >= 1 (optimistic concurrency revision)",
    );
  }

  return issues;
}

/** True when a value validates as a ResearchRecord. */
export function isResearchRecord(value: unknown): value is ResearchRecord {
  return validateResearchRecord(value).length === 0;
}

/** Stable resource identity for one round of one research. */
export function researchRecordId(record: {
  readonly research_id: string;
  readonly round: number;
}): string {
  return `${requireTextLocal(record.research_id, "research_id")}:round-${record.round}`;
}

/**
 * Round idempotency key.
 *
 * Two writes for the same project/research/round address the same resource
 * write; the store must replay the first result or reject a different body,
 * never append a second record.
 */
export function researchRoundKey(record: {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
}): string {
  return [
    requireTextLocal(record.project_id, "project_id"),
    "record",
    requireTextLocal(record.research_id, "research_id"),
    `round-${record.round}`,
  ].join(":");
}

/** Event idempotency key for one round event (e.g. a decision revision). */
export function researchEventKey(
  record: {
    readonly project_id: string;
    readonly research_id: string;
    readonly round: number;
  },
  eventId: string,
): string {
  return `${researchRoundKey(record)}:event:${requireTextLocal(eventId, "eventId")}`;
}

/** Reference to one persisted record revision. */
export function recordRefFor(record: ResearchRecord): ResearchRecordRef {
  return Object.freeze({
    projectId: record.project_id,
    recordId: researchRecordId(record),
    revision: record.record_revision,
  });
}

function requireTextLocal(value: string, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`${label} must be a non-empty string`);
  }
  return value;
}
