/**
 * The single evidence model of the experience client (M1-03, gaps G07/G08).
 *
 * Three things live here and nowhere else in `rph/`:
 *
 * 1. `TaskRef` — the normalised platform task reference
 *    `{project_id, task_id, job_id, attempt, release{release_id, release_digest}}`.
 *    `attempt` and `release` are required: a missing attempt is not attempt 1
 *    and a missing release is not an empty release.
 * 2. `EvidenceView` — the platform projection (`acp2.runtime.evidence-projection.v1`).
 *    It carries references, statuses and digest pointers only; it never carries
 *    a report body and it never upgrades a status. The four evidence statuses
 *    (`complete`/`partial`/`incomplete`/`failed`) are execution completeness and
 *    are disjoint from the four claim statuses of the science contract.
 * 3. `ResearchDecision` — the frozen five-value decision vocabulary
 *    (`accept`/`revise`/`reject`/`defer`/`escalate`) plus the independent
 *    `DecisionRecord` envelope. The envelope is closed and carries derived
 *    rationale *codes* only: there is no field a model narrative could occupy.
 *
 * `cli -> dsh-bundle -> bridge -> research` is the frozen direction, so this
 * module imports nothing from the upper layers. It has no database, no file
 * access and no subprocess.
 */

import type { ValidationIssue } from "./record.js";

// ---------------------------------------------------------------------------
// Frozen vocabularies
// ---------------------------------------------------------------------------

/** Wire ids of the three projections defined here. */
export const EVIDENCE_VIEW_SCHEMA = "acp2.runtime.evidence-projection.v1";
export const EVIDENCE_VIEW_VERSION = "1.0.0";
export const TASK_REF_SCHEMA = "acp2.runtime.task-ref.v1";
export const RESEARCH_DECISION_SCHEMA = "acp2.runtime.research-decision.v1";

/** Execution completeness (worker qualification + step + publication). */
export const EVIDENCE_STATUSES = [
  "complete",
  "partial",
  "incomplete",
  "failed",
] as const;

export type EvidenceStatus = (typeof EVIDENCE_STATUSES)[number];

/** The science contract's frozen claim statuses (never invented here). */
export const CLAIM_STATUSES = [
  "supported",
  "refuted",
  "inconclusive",
  "not_evaluated",
] as const;

export type ClaimStatus = (typeof CLAIM_STATUSES)[number];

/** Backwards-compatible alias for the pre-M1-03 export name. */
export type EvidenceClaimStatus = ClaimStatus;

/** The M1-02 research decision vocabulary (identical to the record schema). */
export const RESEARCH_DECISIONS = [
  "accept",
  "revise",
  "reject",
  "defer",
  "escalate",
] as const;

export type ResearchDecision = (typeof RESEARCH_DECISIONS)[number];

/** Derived rationale codes; a decision record carries no free text. */
export const DECISION_RATIONALE_CODES = [
  "execution_completed",
  "execution_not_completed",
  "evidence_complete",
  "evidence_partial",
  "evidence_incomplete",
  "evidence_failed",
  "claim_supported",
  "claim_refuted",
  "claim_inconclusive",
  "claim_not_evaluated",
  "blocking_missing_check",
  "digest_bound_evidence",
  "no_digest_bound_evidence",
] as const;

export type DecisionRationaleCode = (typeof DECISION_RATIONALE_CODES)[number];

/**
 * `accept` preconditions in evaluation order (mirror of the platform evidence
 * projection policy). A blocking missing check is reported before the generic
 * claim-status rule so the cause is actionable.
 */
export const ACCEPT_PRECONDITION_CODES = [
  "execution_not_completed",
  "evidence_not_complete",
  "blocking_missing_check",
  "claim_status_not_supported",
  "evidence_ref_required",
] as const;

/** Source kinds that denote a model/agent conclusion, never scientific data. */
export const FORBIDDEN_MODEL_EVIDENCE_SOURCES: readonly string[] = Object.freeze([
  "model_assumption",
  "model",
  "llm",
  "agent",
  "reasoning",
  "chain_of_thought",
  "prompt",
] as const);

const ASSUMPTION_KEYS: readonly string[] = Object.freeze([
  "assumption",
  "model_assumption",
  "llm_conclusion",
  "model_conclusion",
] as const);

/**
 * Fields that would archive a prompt or a hidden chain-of-thought.
 *
 * A memory entry and an evidence document must never carry these: the
 * auditable artifact is the decision summary and its references, not the
 * model's internal text.
 */
export const FORBIDDEN_ARCHIVE_FIELDS: readonly string[] = Object.freeze([
  "prompt",
  "prompts",
  "system_prompt",
  "chain_of_thought",
  "cot",
  "reasoning",
  "scratchpad",
  "thoughts",
  "hidden_state",
  "messages",
  "transcript",
  "conversation",
  "completion",
  "model_output",
  "raw_model_output",
] as const);

const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const TASK_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

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

function isOneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
): value is T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value);
}

// ---------------------------------------------------------------------------
// TaskRef: explicit normalisation, no defaulted attempt/release
// ---------------------------------------------------------------------------

export interface TaskRefRelease {
  readonly release_id: string;
  readonly release_digest: string;
}

export interface TaskRef {
  readonly project_id: string;
  readonly task_id: string;
  /** Legacy alias, always normalised to `task_id`. */
  readonly job_id: string;
  readonly attempt: number;
  readonly release: TaskRefRelease;
}

const TASK_REF_FIELDS = new Set([
  "schema",
  "project_id",
  "task_id",
  "job_id",
  "attempt",
  "release",
  "release_id",
  "release_digest",
]);

/**
 * Validate a task reference.
 *
 * `attempt` and `release` are required; a missing attempt is never treated as
 * attempt 1 and a missing release is never an empty release.
 */
export function validateTaskRef(value: unknown, projectId = ""): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  for (const key of extraKeys(value, TASK_REF_FIELDS)) {
    issue(issues, `$.${key}`, "additional_property", "not allowed");
  }
  const project = isText(value["project_id"]) ? value["project_id"] : projectId;
  if (!isText(project)) {
    issue(issues, "$.project_id", "required", "project_id is required");
  }
  const taskId = value["task_id"];
  const jobId = value["job_id"];
  if (!isText(taskId) && !isText(jobId)) {
    issue(issues, "$.task_id", "required", "task_id (or its job_id alias) is required");
  } else if (isText(taskId) && TASK_ID_RE.test(taskId) === false) {
    issue(issues, "$.task_id", "format", "must match the task id pattern");
  } else if (isText(taskId) && isText(jobId) && taskId !== jobId) {
    issue(issues, "$.job_id", "conflict", "job_id is the legacy alias of task_id");
  }
  if (!Object.hasOwn(value, "attempt")) {
    issue(issues, "$.attempt", "required", "attempt is required; it is never defaulted");
  } else if (!isInteger(value["attempt"], 1)) {
    issue(issues, "$.attempt", "type", "must be an integer >= 1");
  }
  const release = value["release"];
  let releaseId: unknown;
  let releaseDigest: unknown;
  if (release === undefined) {
    releaseId = value["release_id"];
    releaseDigest = value["release_digest"];
  } else if (isRecord(release)) {
    releaseId = release["release_id"] ?? release["id"];
    releaseDigest = release["release_digest"] ?? release["digest"];
  } else {
    issue(issues, "$.release", "type", "must be an object");
  }
  if (!isText(releaseId)) {
    issue(issues, "$.release.release_id", "required", "release.release_id is required");
  }
  if (!isText(releaseDigest)) {
    issue(
      issues,
      "$.release.release_digest",
      "required",
      "release.release_digest is required",
    );
  } else if (!DIGEST_RE.test(releaseDigest)) {
    issue(issues, "$.release.release_digest", "format", "must be sha256:<64 lowercase hex>");
  }
  return issues;
}

/**
 * Normalise a task reference; throws on any violation (no silent defaults).
 */
export function parseTaskRef(value: unknown, projectId = ""): TaskRef {
  const issues = validateTaskRef(value, projectId);
  if (issues.length > 0) {
    throw new TypeError(
      `invalid TaskRef: ${issues
        .map((item) => `${item.path}: [${item.code}] ${item.message}`)
        .join("; ")}`,
    );
  }
  const record = value as Record<string, unknown>;
  const release = isRecord(record["release"]) ? record["release"] : {};
  const taskId = (record["task_id"] ?? record["job_id"]) as string;
  const releaseId = (release["release_id"] ?? release["id"] ?? record["release_id"]) as string;
  const releaseDigest = (release["release_digest"] ??
    release["digest"] ??
    record["release_digest"]) as string;
  return Object.freeze({
    project_id: (isText(record["project_id"]) ? record["project_id"] : projectId) as string,
    task_id: taskId,
    job_id: taskId,
    attempt: record["attempt"] as number,
    release: Object.freeze({ release_id: releaseId, release_digest: releaseDigest }),
  });
}

/** Stable key of one attempt (no display fields). */
export function taskRefKey(ref: TaskRef): string {
  return [
    ref.project_id,
    ref.task_id,
    `attempt=${ref.attempt}`,
    ref.release.release_id,
    ref.release.release_digest,
  ].join("|");
}

/** True when two references address the same project/task/attempt/release. */
export function isSameTaskRef(a: TaskRef, b: TaskRef): boolean {
  return taskRefKey(a) === taskRefKey(b);
}

/** The frozen ResearchRecord task reference (flat, no release digest). */
export interface RecordTaskRefLike {
  readonly project_id: string;
  readonly job_id: string;
  readonly attempt: number;
  readonly release_id: string;
}

/**
 * Normalise a `ResearchRecord.task_refs` entry into a `TaskRef`.
 *
 * A record reference carries no release digest, so it is **not** a TaskRef by
 * itself: the verified digest must be supplied explicitly. A missing digest is
 * a refusal — the empty string is never a valid release identity.
 */
export function taskRefFromRecordRef(
  recordRef: RecordTaskRefLike,
  releaseDigest: string,
): TaskRef {
  if (typeof releaseDigest !== "string" || !DIGEST_RE.test(releaseDigest)) {
    throw new TypeError(
      "a RecordTaskRef becomes a TaskRef only with an explicit verified release digest",
    );
  }
  return parseTaskRef({
    project_id: recordRef.project_id,
    task_id: recordRef.job_id,
    job_id: recordRef.job_id,
    attempt: recordRef.attempt,
    release: { release_id: recordRef.release_id, release_digest: releaseDigest },
  });
}

// ---------------------------------------------------------------------------
// EvidenceView: the platform projection
// ---------------------------------------------------------------------------

export interface ResearchAttribution {
  readonly principal_id: string;
  readonly project_id: string;
  readonly research_id: string;
  readonly session_id: string;
  readonly round: number;
}

export interface EvidenceClaimView {
  readonly claim_id: string;
  readonly status: ClaimStatus;
  readonly validator_id: string;
  readonly validator_version: string;
  readonly source_ref_count: number;
  readonly blocking_missing_checks: readonly string[];
}

export interface EvidenceRefView {
  readonly evidence_id: string;
  readonly kind: string;
  readonly sha256: string;
  readonly relative_path: string | null;
}

export interface EvidenceReportRef {
  readonly report_id: string;
  readonly digest: string;
  readonly schema_version: number;
  readonly subject_id: string;
}

export interface EvidenceView {
  readonly schema: typeof EVIDENCE_VIEW_SCHEMA;
  readonly contract_version: string;
  readonly research: ResearchAttribution;
  readonly task_ref: TaskRef;
  readonly report: EvidenceReportRef;
  readonly execution_terminal_state: string;
  readonly qualification: string;
  readonly evidence_status: EvidenceStatus;
  readonly claims: readonly EvidenceClaimView[];
  readonly overall_claim_status: ClaimStatus;
  readonly blocking_missing_checks: readonly string[];
  readonly evidence_refs: readonly EvidenceRefView[];
  readonly limitations: readonly string[];
  readonly provenance_digest: string;
  readonly normal_reuse: boolean;
  readonly projection_digest: string;
}

const VIEW_FIELDS = new Set([
  "schema",
  "contract_version",
  "research",
  "task_ref",
  "report",
  "execution_terminal_state",
  "qualification",
  "evidence_status",
  "claims",
  "overall_claim_status",
  "blocking_missing_checks",
  "evidence_refs",
  "limitations",
  "provenance_digest",
  "normal_reuse",
  "projection_digest",
]);
const RESEARCH_FIELDS = new Set([
  "principal_id",
  "project_id",
  "research_id",
  "session_id",
  "round",
]);
const REPORT_FIELDS = new Set(["report_id", "digest", "schema_version", "subject_id"]);
const CLAIM_VIEW_FIELDS = new Set([
  "claim_id",
  "status",
  "validator_id",
  "validator_version",
  "source_ref_count",
  "blocking_missing_checks",
]);
const EVIDENCE_REF_FIELDS = new Set([
  "evidence_id",
  "kind",
  "sha256",
  "relative_path",
]);

/**
 * Validate an `EvidenceView` payload.
 *
 * A view is a *projection*: everything it cites must be digest-addressed, and
 * its attribution must be internally consistent with its task reference.
 */
export function validateEvidenceView(value: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  for (const key of extraKeys(value, VIEW_FIELDS)) {
    issue(issues, `$.${key}`, "additional_property", "not allowed");
  }
  if (value["schema"] !== EVIDENCE_VIEW_SCHEMA) {
    issue(issues, "$.schema", "enum", `must be ${EVIDENCE_VIEW_SCHEMA}`);
  }
  const research = value["research"];
  if (!isRecord(research)) {
    issue(issues, "$.research", "type", "must be the research attribution object");
  } else {
    for (const key of extraKeys(research, RESEARCH_FIELDS)) {
      issue(issues, `$.research.${key}`, "additional_property", "not allowed");
    }
    for (const key of ["principal_id", "project_id", "research_id", "session_id"]) {
      if (!isText(research[key])) {
        issue(issues, `$.research.${key}`, "type", "must be a non-empty string");
      }
    }
    if (!isInteger(research["round"], 0)) {
      issue(issues, "$.research.round", "type", "must be an integer >= 0");
    }
  }
  const taskRef = value["task_ref"];
  validateTaskRef(taskRef, isRecord(research) && isText(research["project_id"]) ? research["project_id"] : "").forEach(
    (nested) => issue(issues, `$.task_ref${nested.path.slice(1)}`, nested.code, nested.message),
  );
  if (
    isRecord(research) &&
    isRecord(taskRef) &&
    isText(research["project_id"]) &&
    isText(taskRef["project_id"]) &&
    research["project_id"] !== taskRef["project_id"]
  ) {
    issue(issues, "$.task_ref.project_id", "conflict", "must equal research.project_id");
  }
  const report = value["report"];
  if (!isRecord(report)) {
    issue(issues, "$.report", "type", "must be the report reference object");
  } else {
    for (const key of extraKeys(report, REPORT_FIELDS)) {
      issue(issues, `$.report.${key}`, "additional_property", "not allowed");
    }
    if (!isText(report["report_id"])) {
      issue(issues, "$.report.report_id", "type", "must be a non-empty string");
    }
    if (typeof report["digest"] !== "string" || !DIGEST_RE.test(report["digest"])) {
      issue(issues, "$.report.digest", "format", "must be sha256:<64 lowercase hex>");
    }
    if (!isInteger(report["schema_version"], 1)) {
      issue(issues, "$.report.schema_version", "type", "must be an integer >= 1");
    }
    if (!isText(report["subject_id"])) {
      issue(issues, "$.report.subject_id", "type", "must be a non-empty string");
    }
  }
  if (!isText(value["execution_terminal_state"])) {
    issue(issues, "$.execution_terminal_state", "type", "must be a non-empty string");
  }
  if (!isText(value["qualification"])) {
    issue(issues, "$.qualification", "type", "must be a non-empty string");
  }
  if (!isOneOf(value["evidence_status"], EVIDENCE_STATUSES)) {
    issue(
      issues,
      "$.evidence_status",
      "enum",
      `must be one of ${EVIDENCE_STATUSES.join(", ")}`,
    );
  }
  const claims = value["claims"];
  if (!Array.isArray(claims) || claims.length === 0) {
    issue(issues, "$.claims", "type", "must be a non-empty array");
  } else {
    claims.forEach((claim, index) => {
      const path = `$.claims[${index}]`;
      if (!isRecord(claim)) {
        issue(issues, path, "type", "must be an object");
        return;
      }
      for (const key of extraKeys(claim, CLAIM_VIEW_FIELDS)) {
        issue(issues, `${path}.${key}`, "additional_property", "not allowed");
      }
      if (!isText(claim["claim_id"])) {
        issue(issues, `${path}.claim_id`, "type", "must be a non-empty string");
      }
      if (!isOneOf(claim["status"], CLAIM_STATUSES)) {
        issue(issues, `${path}.status`, "enum", `must be one of ${CLAIM_STATUSES.join(", ")}`);
      }
      if (typeof claim["validator_id"] !== "string") {
        issue(issues, `${path}.validator_id`, "type", "must be a string");
      }
      if (typeof claim["validator_version"] !== "string") {
        issue(issues, `${path}.validator_version`, "type", "must be a string");
      }
      if (!isInteger(claim["source_ref_count"], 0)) {
        issue(issues, `${path}.source_ref_count`, "type", "must be an integer >= 0");
      }
      const missing = claim["blocking_missing_checks"];
      if (!Array.isArray(missing) || missing.some((item) => !isText(item))) {
        issue(issues, `${path}.blocking_missing_checks`, "type", "must be an array of strings");
      }
    });
  }
  if (!isOneOf(value["overall_claim_status"], CLAIM_STATUSES)) {
    issue(
      issues,
      "$.overall_claim_status",
      "enum",
      `must be one of ${CLAIM_STATUSES.join(", ")}`,
    );
  }
  const blocking = value["blocking_missing_checks"];
  if (!Array.isArray(blocking) || blocking.some((item) => !isText(item))) {
    issue(issues, "$.blocking_missing_checks", "type", "must be an array of strings");
  }
  const evidenceRefs = value["evidence_refs"];
  if (!Array.isArray(evidenceRefs)) {
    issue(issues, "$.evidence_refs", "type", "must be an array");
  } else {
    evidenceRefs.forEach((ref, index) => {
      const path = `$.evidence_refs[${index}]`;
      if (!isRecord(ref)) {
        issue(issues, path, "type", "must be an object");
        return;
      }
      for (const key of extraKeys(ref, EVIDENCE_REF_FIELDS)) {
        issue(issues, `${path}.${key}`, "additional_property", "not allowed");
      }
      if (!isText(ref["evidence_id"])) {
        issue(issues, `${path}.evidence_id`, "type", "must be a non-empty string");
      }
      if (!isText(ref["kind"])) {
        issue(issues, `${path}.kind`, "type", "must be a non-empty string");
      }
      if (typeof ref["sha256"] !== "string" || !DIGEST_RE.test(ref["sha256"])) {
        issue(issues, `${path}.sha256`, "format", "must be sha256:<64 lowercase hex>");
      }
      const relative = ref["relative_path"];
      if (relative !== null && relative !== undefined && !isText(relative)) {
        issue(issues, `${path}.relative_path`, "type", "must be a string or null");
      }
    });
  }
  const limitations = value["limitations"];
  if (!Array.isArray(limitations) || limitations.some((item) => !isText(item))) {
    issue(issues, "$.limitations", "type", "must be an array of strings");
  }
  if (typeof value["provenance_digest"] !== "string") {
    issue(issues, "$.provenance_digest", "type", "must be a string");
  } else if (
    value["provenance_digest"].length > 0 &&
    !DIGEST_RE.test(value["provenance_digest"])
  ) {
    issue(issues, "$.provenance_digest", "format", "must be sha256:<64 lowercase hex> or empty");
  }
  if (typeof value["normal_reuse"] !== "boolean") {
    issue(issues, "$.normal_reuse", "type", "must be a boolean");
  }
  if (typeof value["projection_digest"] !== "string" || !DIGEST_RE.test(value["projection_digest"])) {
    issue(issues, "$.projection_digest", "format", "must be sha256:<64 lowercase hex>");
  }
  flagArchiveFields(value, "$", issues);
  return issues;
}

/** True when a value validates as an `EvidenceView`. */
export function isEvidenceView(value: unknown): value is EvidenceView {
  return validateEvidenceView(value).length === 0;
}

/** Assert an `EvidenceView`; throws with the violation list. */
export function assertEvidenceView(value: unknown): EvidenceView {
  const issues = validateEvidenceView(value);
  if (issues.length > 0) {
    throw new TypeError(
      `invalid EvidenceView: ${issues
        .map((item) => `${item.path}: [${item.code}] ${item.message}`)
        .join("; ")}`,
    );
  }
  return value as EvidenceView;
}

/** True when an `accept` decision is publishable for this view. */
export function isAcceptableEvidence(view: EvidenceView): boolean {
  return evaluateDecision(view, "accept").allowed;
}

// ---------------------------------------------------------------------------
// ResearchDecision: independent decision record + frozen policy
// ---------------------------------------------------------------------------

export interface DecisionEvidence {
  readonly status: EvidenceStatus;
  readonly overall_claim_status: ClaimStatus;
  readonly blocking_missing_checks: readonly string[];
}

export interface DecisionRecord {
  readonly schema: typeof RESEARCH_DECISION_SCHEMA;
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly decision: ResearchDecision;
  readonly task_ref: TaskRef;
  readonly evidence: DecisionEvidence;
  readonly evidence_refs: readonly EvidenceRefView[];
  readonly rationale_codes: readonly DecisionRationaleCode[];
  readonly projection_digest: string;
}

export interface DecisionEvaluation {
  readonly allowed: boolean;
  /** The first failing precondition code, or `null` when allowed. */
  readonly code: string | null;
}

/**
 * Evaluate the frozen decision policy for one view.
 *
 * `accept` is the only decision with preconditions (in
 * `ACCEPT_PRECONDITION_CODES` order); `revise`/`reject`/`defer`/`escalate` stay
 * always available. Every decision still needs at least one digest-bound
 * evidence ref: a decision without evidence is not auditable.
 */
export function evaluateDecision(
  view: EvidenceView,
  decision: string,
): DecisionEvaluation {
  if (!(RESEARCH_DECISIONS as readonly string[]).includes(decision)) {
    return Object.freeze({ allowed: false, code: "unknown_decision" });
  }
  if (view.evidence_refs.length === 0) {
    return Object.freeze({ allowed: false, code: "evidence_ref_required" });
  }
  if (decision !== "accept") {
    return Object.freeze({ allowed: true, code: null });
  }
  if (view.execution_terminal_state !== "completed") {
    return Object.freeze({ allowed: false, code: "execution_not_completed" });
  }
  if (view.evidence_status !== "complete") {
    return Object.freeze({ allowed: false, code: "evidence_not_complete" });
  }
  if (view.blocking_missing_checks.length > 0) {
    return Object.freeze({ allowed: false, code: "blocking_missing_check" });
  }
  if (view.overall_claim_status !== "supported") {
    return Object.freeze({ allowed: false, code: "claim_status_not_supported" });
  }
  return Object.freeze({ allowed: true, code: null });
}

function rationaleFor(view: EvidenceView): readonly DecisionRationaleCode[] {
  const codes = new Set<string>([
    view.execution_terminal_state === "completed"
      ? "execution_completed"
      : "execution_not_completed",
    `evidence_${view.evidence_status}`,
    `claim_${view.overall_claim_status}`,
  ]);
  if (view.blocking_missing_checks.length > 0) {
    codes.add("blocking_missing_check");
  }
  codes.add(view.evidence_refs.length > 0 ? "digest_bound_evidence" : "no_digest_bound_evidence");
  return Object.freeze(
    DECISION_RATIONALE_CODES.filter((code) => codes.has(code)),
  );
}

/**
 * Build the decision record for *view*; throws when the policy refuses it.
 */
export function buildDecisionRecord(
  view: EvidenceView,
  decision: ResearchDecision,
): DecisionRecord {
  const evaluation = evaluateDecision(view, decision);
  if (!evaluation.allowed) {
    throw new TypeError(
      `decision ${decision} is not publishable: [${evaluation.code ?? "unknown"}] ` +
        `evidence_status=${view.evidence_status} ` +
        `overall_claim_status=${view.overall_claim_status}`,
    );
  }
  return Object.freeze({
    schema: RESEARCH_DECISION_SCHEMA,
    project_id: view.research.project_id,
    research_id: view.research.research_id,
    round: view.research.round,
    decision,
    task_ref: view.task_ref,
    evidence: Object.freeze({
      status: view.evidence_status,
      overall_claim_status: view.overall_claim_status,
      blocking_missing_checks: Object.freeze([...view.blocking_missing_checks]),
    }),
    evidence_refs: Object.freeze([...view.evidence_refs]),
    rationale_codes: rationaleFor(view),
    projection_digest: view.projection_digest,
  });
}

const DECISION_FIELDS = new Set([
  "schema",
  "project_id",
  "research_id",
  "round",
  "decision",
  "task_ref",
  "evidence",
  "evidence_refs",
  "rationale_codes",
  "projection_digest",
]);
const DECISION_EVIDENCE_FIELDS = new Set([
  "status",
  "overall_claim_status",
  "blocking_missing_checks",
]);

/**
 * Validate a decision record against the view it cites.
 *
 * The envelope is closed (no `reason`/`summary`/model text), the attribution
 * must equal the view's, the evidence must be digest-bound and the policy must
 * allow the decision.
 */
export function validateDecisionRecord(
  view: EvidenceView,
  value: unknown,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  for (const key of extraKeys(value, DECISION_FIELDS)) {
    issue(
      issues,
      `$.${key}`,
      "decision_field_not_allowed",
      "a decision carries rationale codes, not free text",
    );
  }
  if (value["schema"] !== RESEARCH_DECISION_SCHEMA) {
    issue(issues, "$.schema", "enum", `must be ${RESEARCH_DECISION_SCHEMA}`);
  }
  if (value["project_id"] !== view.research.project_id) {
    issue(issues, "$.project_id", "foreign_project", "must equal the view project");
  }
  if (value["research_id"] !== view.research.research_id) {
    issue(issues, "$.research_id", "foreign_research", "must equal the view research");
  }
  if (value["round"] !== view.research.round) {
    issue(issues, "$.round", "foreign_round", "must equal the view round");
  }
  if (!isOneOf(value["decision"], RESEARCH_DECISIONS)) {
    issue(issues, "$.decision", "enum", `must be one of ${RESEARCH_DECISIONS.join(", ")}`);
  }
  try {
    const taskRef = parseTaskRef(value["task_ref"], view.task_ref.project_id);
    if (taskRefKey(taskRef) !== taskRefKey(view.task_ref)) {
      issue(issues, "$.task_ref", "foreign_task_ref", "must equal the view task reference");
    }
  } catch (error) {
    issue(
      issues,
      "$.task_ref",
      "invalid",
      error instanceof Error ? error.message : "invalid task reference",
    );
  }
  const evidence = value["evidence"];
  if (!isRecord(evidence)) {
    issue(issues, "$.evidence", "type", "must be the evidence status object");
  } else {
    for (const key of extraKeys(evidence, DECISION_EVIDENCE_FIELDS)) {
      issue(issues, `$.evidence.${key}`, "decision_field_not_allowed", "not allowed");
    }
    if (!isOneOf(evidence["status"], EVIDENCE_STATUSES)) {
      issue(issues, "$.evidence.status", "enum", `must be one of ${EVIDENCE_STATUSES.join(", ")}`);
    }
    if (!isOneOf(evidence["overall_claim_status"], CLAIM_STATUSES)) {
      issue(
        issues,
        "$.evidence.overall_claim_status",
        "enum",
        `must be one of ${CLAIM_STATUSES.join(", ")}`,
      );
    }
    if (evidence["status"] !== view.evidence_status) {
      issue(issues, "$.evidence.status", "conflict", "must equal the view evidence status");
    }
    if (evidence["overall_claim_status"] !== view.overall_claim_status) {
      issue(
        issues,
        "$.evidence.overall_claim_status",
        "conflict",
        "must equal the view claim status",
      );
    }
  }
  const refs = value["evidence_refs"];
  if (!Array.isArray(refs) || refs.length === 0) {
    issue(issues, "$.evidence_refs", "evidence_ref_required", "must cite digest-bound evidence");
  } else {
    refs.forEach((ref, index) => {
      if (!isRecord(ref) || typeof ref["sha256"] !== "string" || !DIGEST_RE.test(ref["sha256"])) {
        issue(
          issues,
          `$.evidence_refs[${index}].sha256`,
          "format",
          "must be sha256:<64 lowercase hex>",
        );
      }
    });
  }
  const codes = value["rationale_codes"];
  if (!Array.isArray(codes)) {
    issue(issues, "$.rationale_codes", "type", "must be an array");
  } else {
    codes.forEach((code, index) => {
      if (!isOneOf(code, DECISION_RATIONALE_CODES)) {
        issue(issues, `$.rationale_codes[${index}]`, "enum", "not a derived rationale code");
      }
    });
  }
  if (value["projection_digest"] !== view.projection_digest) {
    issue(issues, "$.projection_digest", "foreign_projection", "must equal the view projection");
  }
  if (isOneOf(value["decision"], RESEARCH_DECISIONS)) {
    const evaluation = evaluateDecision(view, value["decision"]);
    if (!evaluation.allowed) {
      issue(
        issues,
        "$.decision",
        evaluation.code ?? "denied",
        "the frozen decision policy refuses this decision for the view",
      );
    }
  }
  flagArchiveFields(value, "$", issues);
  return issues;
}

/** True when a value is a valid decision record for *view*. */
export function isDecisionRecord(view: EvidenceView, value: unknown): value is DecisionRecord {
  return validateDecisionRecord(view, value).length === 0;
}

/** Assert a decision record for *view*; throws with the violation list. */
export function assertDecisionRecord(view: EvidenceView, value: unknown): DecisionRecord {
  const issues = validateDecisionRecord(view, value);
  if (issues.length > 0) {
    throw new TypeError(
      `invalid ResearchDecision: ${issues
        .map((item) => `${item.path}: [${item.code}] ${item.message}`)
        .join("; ")}`,
    );
  }
  return value as DecisionRecord;
}

// ---------------------------------------------------------------------------
// Archive/assumption guard shared by every evidence-shaped document
// ---------------------------------------------------------------------------

function flagArchiveFields(
  value: Readonly<Record<string, unknown>>,
  path: string,
  issues: ValidationIssue[],
): void {
  for (const key of FORBIDDEN_ARCHIVE_FIELDS) {
    if (Object.hasOwn(value, key)) {
      issue(
        issues,
        `${path}.${key}`,
        "prompt_archive_forbidden",
        "evidence must not archive prompts or hidden chain-of-thought",
      );
    }
  }
  for (const key of ASSUMPTION_KEYS) {
    if (Object.hasOwn(value, key)) {
      issue(
        issues,
        `${path}.${key}`,
        "model_assumption_not_evidence",
        "a model assumption is not validator evidence",
      );
    }
  }
}

// ---------------------------------------------------------------------------
// EvidenceReport-shaped consumption documents (single model, pre-M1-03 name)
// ---------------------------------------------------------------------------

export interface EvidenceItem {
  readonly source_kind: string;
  readonly sha256?: string | null;
  readonly ref?: string | null;
}

export interface EvidenceReportClaim {
  readonly claim_id: string;
  readonly status: ClaimStatus;
  readonly validator_id: string;
  readonly validator_version: string;
  readonly source_refs: readonly string[];
  readonly evidence: readonly EvidenceItem[];
  readonly limitations?: readonly string[];
}

/**
 * A science-contract evidence report *as consumed* (never authored) by the
 * experience client. `EvidenceView` is the platform projection of the same
 * facts; both live here so a second parallel field model cannot appear.
 */
export interface EvidenceReport {
  readonly report_id: string;
  readonly project_id: string;
  readonly claims: readonly EvidenceReportClaim[];
}

function validateEvidenceItem(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, path, "type", "must be an object");
    return;
  }
  flagArchiveFields(value, path, issues);
  const sourceKind = value["source_kind"];
  if (!isText(sourceKind)) {
    issue(issues, `${path}.source_kind`, "type", "must be a non-empty string");
  } else if (FORBIDDEN_MODEL_EVIDENCE_SOURCES.includes(sourceKind)) {
    issue(
      issues,
      `${path}.source_kind`,
      "model_assumption_not_evidence",
      `source_kind ${sourceKind} is model output, not scientific evidence`,
    );
  }
}

function validateEvidenceReportClaim(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, path, "type", "must be an object");
    return;
  }
  flagArchiveFields(value, path, issues);
  if (!isText(value["claim_id"])) {
    issue(issues, `${path}.claim_id`, "type", "must be a non-empty string");
  }
  if (!isOneOf(value["status"], CLAIM_STATUSES)) {
    issue(issues, `${path}.status`, "enum", `must be one of ${CLAIM_STATUSES.join(", ")}`);
  }
  if (!isText(value["validator_id"])) {
    issue(issues, `${path}.validator_id`, "type", "must be a non-empty string");
  }
  if (!isText(value["validator_version"])) {
    issue(issues, `${path}.validator_version`, "type", "must be a non-empty string");
  }
  const sourceRefs = value["source_refs"];
  if (!Array.isArray(sourceRefs)) {
    issue(issues, `${path}.source_refs`, "type", "must be an array of strings");
  }
  const evidence = value["evidence"];
  if (!Array.isArray(evidence)) {
    issue(issues, `${path}.evidence`, "type", "must be an array");
  } else {
    evidence.forEach((item, index) =>
      validateEvidenceItem(item, `${path}.evidence[${index}]`, issues),
    );
  }
}

/**
 * Validate an EvidenceReport-shaped value.
 *
 * Rejects any evidence item whose `source_kind` is a model/agent conclusion
 * and any claim/evidence key that carries a model assumption or an archived
 * prompt/chain-of-thought. The scientific verdict vocabulary is fixed; a model
 * can never author a `supported` claim through this surface.
 */
export function validateEvidenceReport(value: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  flagArchiveFields(value, "$", issues);
  if (!isText(value["report_id"])) {
    issue(issues, "$.report_id", "type", "must be a non-empty string");
  }
  if (!isText(value["project_id"])) {
    issue(issues, "$.project_id", "type", "must be a non-empty string");
  }
  const claims = value["claims"];
  if (!Array.isArray(claims)) {
    issue(issues, "$.claims", "type", "must be an array");
  } else {
    claims.forEach((claim, index) =>
      validateEvidenceReportClaim(claim, `$.claims[${index}]`, issues),
    );
  }
  return issues;
}

/** True when a value validates as an EvidenceReport. */
export function isEvidenceReport(value: unknown): value is EvidenceReport {
  return validateEvidenceReport(value).length === 0;
}

/** Assert an EvidenceReport-shaped value; throws with the violation list. */
export function assertEvidenceReport(value: unknown): EvidenceReport {
  const issues = validateEvidenceReport(value);
  if (issues.length > 0) {
    throw new TypeError(
      `invalid EvidenceReport: ${issues
        .map((item) => `${item.path}: [${item.code}] ${item.message}`)
        .join("; ")}`,
    );
  }
  return value as EvidenceReport;
}
