/**
 * Proposal model, validation and the frozen plan-definition digest.
 *
 * The digest is NOT a second canonicalization: it implements the projection,
 * normalization and serialization frozen in
 * `experience/plan-identity.md` §1 byte-for-byte (the B-owned canonical digest
 * algorithm).  The projection includes exactly recipe_digest, the seven
 * science identity fields and the eight budget counters.  Approval state,
 * timestamps, display names, decisions, human edits, allowed_change_scope and
 * later-appended task/evidence references are excluded.
 */

import { createHash } from "node:crypto";

export const PROPOSAL_SCHEMA = "acp2.experience.proposal.v1";
export const PROPOSAL_DECISIONS = [
  "proposed",
  "approved",
  "rejected",
  "superseded",
  "deferred",
] as const;

export type ProposalDecision = (typeof PROPOSAL_DECISIONS)[number];

export interface CapabilityStep {
  readonly step_index: number;
  readonly capability: string;
  readonly version: string;
  readonly tool?: string | null;
  readonly parameters?: Readonly<Record<string, unknown>>;
}

export interface InitialInput {
  readonly input_id: string;
  readonly sha256: string;
  readonly format: string;
  readonly role?: string | null;
}

export interface AllowedSubstitution {
  readonly field_path: string;
  readonly allowed_values: readonly string[];
}

export interface ProposalScience {
  readonly capability_steps: readonly CapabilityStep[];
  readonly initial_inputs: readonly InitialInput[];
  readonly method: string;
  readonly mapping: string;
  readonly charge: number;
  readonly multiplicity: number;
  readonly allowed_substitutions: readonly AllowedSubstitution[];
}

export interface ProposalBudget {
  readonly model_requests: number;
  readonly input_tokens: number;
  readonly output_tokens: number;
  readonly task_refs: number;
  readonly recipe_steps: number;
  readonly qc_invocations: number;
  readonly wall_seconds: number;
  readonly queue_seconds: number;
  readonly unit_basis?: string;
  readonly notes?: string;
}

export interface AllowedChangeScope {
  readonly mode: "none" | "finite_list";
  readonly finite_substitutions: readonly AllowedSubstitution[];
}

export interface ProposalHumanEdit {
  readonly edited_at: string;
  readonly field_path: string;
  readonly editor_ref: string;
  readonly note?: string | null;
}

export interface Proposal {
  readonly proposal_id: string;
  readonly project_id: string;
  readonly question_ref: string;
  readonly question_revision: number;
  readonly reaction_ref: string;
  readonly reaction_revision: number;
  readonly hypothesis: string;
  readonly recipe_digest: string;
  readonly plan_definition_digest: string;
  readonly science: ProposalScience;
  readonly budget: ProposalBudget;
  readonly approval_ref: string | null;
  readonly allowed_change_scope: AllowedChangeScope;
  readonly round: number;
  readonly decision: ProposalDecision;
  readonly created_at: string;
  readonly human_edits: readonly ProposalHumanEdit[];
  readonly approval_status?: "pending" | "approved" | "rejected" | "revoked";
  readonly updated_at?: string | null;
  readonly display_name?: string | null;
}

export interface PlanDefinitionInput {
  readonly recipe_digest: string;
  readonly science: ProposalScience;
  readonly budget: ProposalBudget;
}

export interface ApprovalBinding {
  readonly approvalRef: string;
  readonly planDefinitionDigest: string;
  readonly projectId: string;
  readonly budget: ProposalBudget;
  readonly allowedChangeScope: AllowedChangeScope;
}

export interface ProposalValidationIssue {
  readonly path: string;
  readonly code: string;
  readonly message: string;
}

export class PlanDigestError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(`${code}: ${message}`);
    this.name = "PlanDigestError";
    this.code = code;
  }
}

const SCIENCE_FIELDS = [
  "capability_steps",
  "initial_inputs",
  "method",
  "mapping",
  "charge",
  "multiplicity",
  "allowed_substitutions",
] as const;

const BUDGET_FIELDS = [
  "model_requests",
  "input_tokens",
  "output_tokens",
  "task_refs",
  "recipe_steps",
  "qc_invocations",
  "wall_seconds",
  "queue_seconds",
] as const;

export const PLAN_SCIENCE_FIELDS: readonly string[] = Object.freeze([
  ...SCIENCE_FIELDS,
]);
export const PLAN_BUDGET_FIELDS: readonly string[] = Object.freeze([
  ...BUDGET_FIELDS,
]);

const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const PROPOSAL_ID_RE = /^prop-[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const TIMESTAMP_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;

const PROPOSAL_FIELDS = new Set([
  "proposal_id",
  "project_id",
  "question_ref",
  "question_revision",
  "reaction_ref",
  "reaction_revision",
  "hypothesis",
  "recipe_digest",
  "plan_definition_digest",
  "science",
  "budget",
  "approval_ref",
  "allowed_change_scope",
  "round",
  "decision",
  "created_at",
  "human_edits",
  "approval_status",
  "updated_at",
  "display_name",
]);

const SCIENCE_KEYS = new Set<string>([
  ...SCIENCE_FIELDS,
]);

const BUDGET_KEYS = new Set<string>([
  ...BUDGET_FIELDS,
  "unit_basis",
  "notes",
]);

const TASK_STATE_FIELDS: readonly string[] = Object.freeze([
  "status",
  "state",
  "lifecycle",
  "job_id",
  "attempt",
  "queue_position",
  "running",
  "queued",
] as const);

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

function issue(
  issues: ProposalValidationIssue[],
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

function validateAllowedSubstitutions(
  value: unknown,
  path: string,
  issues: ProposalValidationIssue[],
): void {
  if (!Array.isArray(value)) {
    issue(issues, path, "type", "must be an array");
    return;
  }
  value.forEach((entry, index) => {
    const at = `${path}[${index}]`;
    if (!isRecord(entry)) {
      issue(issues, at, "type", "must be an object");
      return;
    }
    for (const key of Object.keys(entry)) {
      if (key !== "field_path" && key !== "allowed_values") {
        issue(issues, `${at}.${key}`, "additional_property", "not allowed");
      }
    }
    if (!isText(entry["field_path"])) {
      issue(issues, `${at}.field_path`, "type", "must be a non-empty string");
    }
    const values = entry["allowed_values"];
    if (!Array.isArray(values) || values.length === 0) {
      issue(issues, `${at}.allowed_values`, "type", "must be a non-empty array");
    } else {
      const seen = new Set<string>();
      values.forEach((item, valueIndex) => {
        if (typeof item !== "string") {
          issue(
            issues,
            `${at}.allowed_values[${valueIndex}]`,
            "type",
            "must be a string",
          );
        } else if (seen.has(item)) {
          issue(
            issues,
            `${at}.allowed_values[${valueIndex}]`,
            "unique",
            "duplicate allowed value",
          );
        } else {
          seen.add(item);
        }
      });
    }
  });
}

function validateScience(
  value: unknown,
  issues: ProposalValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, "$.science", "type", "must be an object");
    return;
  }
  for (const key of extraKeys(value, SCIENCE_KEYS)) {
    issue(issues, `$.science.${key}`, "additional_property", "not allowed");
  }

  const steps = value["capability_steps"];
  if (!Array.isArray(steps) || steps.length === 0) {
    issue(
      issues,
      "$.science.capability_steps",
      "type",
      "must be a non-empty array",
    );
  } else {
    steps.forEach((step, index) => {
      const at = `$.science.capability_steps[${index}]`;
      if (!isRecord(step)) {
        issue(issues, at, "type", "must be an object");
        return;
      }
      const allowed = new Set([
        "step_index",
        "capability",
        "tool",
        "version",
        "parameters",
      ]);
      for (const key of extraKeys(step, allowed)) {
        issue(issues, `${at}.${key}`, "additional_property", "not allowed");
      }
      if (!isInteger(step["step_index"], 0)) {
        issue(issues, `${at}.step_index`, "type", "must be a non-negative integer");
      }
      if (!isText(step["capability"])) {
        issue(issues, `${at}.capability`, "type", "must be a non-empty string");
      }
      if (!isText(step["version"])) {
        issue(issues, `${at}.version`, "type", "must be a non-empty string");
      }
      const tool = step["tool"];
      if (tool !== undefined && tool !== null && typeof tool !== "string") {
        issue(issues, `${at}.tool`, "type", "must be a string or null");
      }
      const parameters = step["parameters"];
      if (parameters !== undefined && !isRecord(parameters)) {
        issue(issues, `${at}.parameters`, "type", "must be an object");
      }
    });
  }

  const inputs = value["initial_inputs"];
  if (!Array.isArray(inputs) || inputs.length === 0) {
    issue(
      issues,
      "$.science.initial_inputs",
      "type",
      "must be a non-empty array",
    );
  } else {
    inputs.forEach((input, index) => {
      const at = `$.science.initial_inputs[${index}]`;
      if (!isRecord(input)) {
        issue(issues, at, "type", "must be an object");
        return;
      }
      const allowed = new Set(["input_id", "sha256", "format", "role"]);
      for (const key of extraKeys(input, allowed)) {
        issue(issues, `${at}.${key}`, "additional_property", "not allowed");
      }
      if (!isText(input["input_id"])) {
        issue(issues, `${at}.input_id`, "type", "must be a non-empty string");
      }
      if (!isDigest(input["sha256"])) {
        issue(issues, `${at}.sha256`, "pattern", "must be sha256:<64 lowercase hex>");
      }
      if (!isText(input["format"])) {
        issue(issues, `${at}.format`, "type", "must be a non-empty string");
      }
      const role = input["role"];
      if (role !== undefined && role !== null && typeof role !== "string") {
        issue(issues, `${at}.role`, "type", "must be a string or null");
      }
    });
  }

  for (const key of ["method", "mapping"] as const) {
    if (!isText(value[key])) {
      issue(issues, `$.science.${key}`, "type", "must be a non-empty string");
    }
  }
  if (!isInteger(value["charge"], Number.NEGATIVE_INFINITY)) {
    issue(issues, "$.science.charge", "type", "must be an integer");
  }
  if (!isInteger(value["multiplicity"], 1)) {
    issue(
      issues,
      "$.science.multiplicity",
      "type",
      "must be an integer >= 1",
    );
  }
  validateAllowedSubstitutions(
    value["allowed_substitutions"],
    "$.science.allowed_substitutions",
    issues,
  );
}

function validateBudget(
  value: unknown,
  issues: ProposalValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, "$.budget", "type", "must be an object");
    return;
  }
  for (const key of extraKeys(value, BUDGET_KEYS)) {
    issue(issues, `$.budget.${key}`, "additional_property", "not allowed");
  }
  for (const field of BUDGET_FIELDS) {
    if (!isInteger(value[field], 0)) {
      issue(
        issues,
        `$.budget.${field}`,
        "type",
        "must be a non-negative integer",
      );
    }
  }
  for (const field of ["unit_basis", "notes"] as const) {
    const optional = value[field];
    if (
      optional !== undefined &&
      optional !== null &&
      typeof optional !== "string"
    ) {
      issue(issues, `$.budget.${field}`, "type", "must be a string");
    }
  }
}

function validateAllowedChangeScope(
  value: unknown,
  issues: ProposalValidationIssue[],
): void {
  if (!isRecord(value)) {
    issue(issues, "$.allowed_change_scope", "type", "must be an object");
    return;
  }
  for (const key of Object.keys(value)) {
    if (key !== "mode" && key !== "finite_substitutions") {
      issue(
        issues,
        `$.allowed_change_scope.${key}`,
        "additional_property",
        "not allowed",
      );
    }
  }
  const mode = value["mode"];
  if (mode !== "none" && mode !== "finite_list") {
    issue(
      issues,
      "$.allowed_change_scope.mode",
      "enum",
      "must be none or finite_list",
    );
  }
  validateAllowedSubstitutions(
    value["finite_substitutions"],
    "$.allowed_change_scope.finite_substitutions",
    issues,
  );
}

function validateHumanEdits(
  value: unknown,
  issues: ProposalValidationIssue[],
): void {
  if (!Array.isArray(value)) {
    issue(issues, "$.human_edits", "type", "must be an array");
    return;
  }
  value.forEach((edit, index) => {
    const at = `$.human_edits[${index}]`;
    if (!isRecord(edit)) {
      issue(issues, at, "type", "must be an object");
      return;
    }
    const allowed = new Set(["edited_at", "field_path", "editor_ref", "note"]);
    for (const key of extraKeys(edit, allowed)) {
      issue(issues, `${at}.${key}`, "additional_property", "not allowed");
    }
    if (!isTimestamp(edit["edited_at"])) {
      issue(issues, `${at}.edited_at`, "format", "must be an RFC3339 date-time");
    }
    if (!isText(edit["field_path"])) {
      issue(issues, `${at}.field_path`, "type", "must be a non-empty string");
    }
    if (!isText(edit["editor_ref"])) {
      issue(issues, `${at}.editor_ref`, "type", "must be a non-empty string");
    }
    const note = edit["note"];
    if (note !== undefined && note !== null && typeof note !== "string") {
      issue(issues, `${at}.note`, "type", "must be a string or null");
    }
  });
}

/** Validate one Proposal against the frozen field set. */
export function validateProposal(value: unknown): ProposalValidationIssue[] {
  const issues: ProposalValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  for (const key of TASK_STATE_FIELDS) {
    if (Object.hasOwn(value, key)) {
      issue(
        issues,
        `$.${key}`,
        "task_state_field",
        "a Proposal is not a task/job state machine",
      );
    }
  }
  for (const key of extraKeys(value, PROPOSAL_FIELDS)) {
    if (!TASK_STATE_FIELDS.includes(key)) {
      issue(issues, `$.${key}`, "additional_property", "not allowed");
    }
  }

  const proposalId = value["proposal_id"];
  if (typeof proposalId !== "string" || !PROPOSAL_ID_RE.test(proposalId)) {
    issue(
      issues,
      "$.proposal_id",
      "pattern",
      "must match ^prop-[A-Za-z0-9][A-Za-z0-9._:-]*$",
    );
  }
  for (const field of [
    "project_id",
    "question_ref",
    "reaction_ref",
    "hypothesis",
  ] as const) {
    if (!isText(value[field])) {
      issue(issues, `$.${field}`, "type", "must be a non-empty string");
    }
  }
  for (const field of ["question_revision", "reaction_revision", "round"] as const) {
    if (!isInteger(value[field], 0)) {
      issue(issues, `$.${field}`, "type", "must be a non-negative integer");
    }
  }
  if (!isDigest(value["recipe_digest"])) {
    issue(issues, "$.recipe_digest", "pattern", "must be sha256:<64 lowercase hex>");
  }
  if (!isDigest(value["plan_definition_digest"])) {
    issue(
      issues,
      "$.plan_definition_digest",
      "pattern",
      "must be sha256:<64 lowercase hex>",
    );
  }
  const decision = value["decision"];
  if (
    typeof decision !== "string" ||
    !(PROPOSAL_DECISIONS as readonly string[]).includes(decision)
  ) {
    issue(
      issues,
      "$.decision",
      "enum",
      `must be one of ${PROPOSAL_DECISIONS.join(", ")}`,
    );
  }
  const approval = value["approval_ref"];
  if (approval !== null && !isText(approval)) {
    issue(issues, "$.approval_ref", "type", "must be a string or null");
  }
  if (!isTimestamp(value["created_at"])) {
    issue(issues, "$.created_at", "format", "must be an RFC3339 date-time");
  }

  validateScience(value["science"], issues);
  validateBudget(value["budget"], issues);
  validateAllowedChangeScope(value["allowed_change_scope"], issues);
  validateHumanEdits(value["human_edits"], issues);

  const status = value["approval_status"];
  if (
    status !== undefined &&
    status !== "pending" &&
    status !== "approved" &&
    status !== "rejected" &&
    status !== "revoked"
  ) {
    issue(issues, "$.approval_status", "enum", "invalid approval status");
  }
  const updated = value["updated_at"];
  if (
    updated !== undefined &&
    updated !== null &&
    !isTimestamp(updated)
  ) {
    issue(issues, "$.updated_at", "format", "must be an RFC3339 date-time or null");
  }
  const display = value["display_name"];
  if (
    display !== undefined &&
    display !== null &&
    typeof display !== "string"
  ) {
    issue(issues, "$.display_name", "type", "must be a string or null");
  }

  // The declared digest must be the frozen projection's digest.  A caller may
  // not smuggle a different scientific definition behind a stale digest.
  try {
    const computed = computePlanDefinitionDigest(value as unknown as Proposal);
    if (
      isDigest(value["plan_definition_digest"]) &&
      value["plan_definition_digest"] !== computed
    ) {
      issue(
        issues,
        "$.plan_definition_digest",
        "digest_mismatch",
        `declared ${String(value["plan_definition_digest"])} != computed ${computed}`,
      );
    }
  } catch (error) {
    issue(
      issues,
      "$.plan_definition_digest",
      "digest_uncomputable",
      error instanceof Error ? error.message : String(error),
    );
  }

  return issues;
}

/** True when a value validates as a Proposal. */
export function isProposal(value: unknown): value is Proposal {
  return validateProposal(value).length === 0;
}

// ---------------------------------------------------------------------------
// Frozen plan-definition digest (plan-identity.md §1)
// ---------------------------------------------------------------------------

function dropNulls(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(dropNulls);
  }
  if (isRecord(value)) {
    const out: Record<string, unknown> = {};
    for (const [key, child] of Object.entries(value)) {
      if (child === null || child === undefined) {
        continue;
      }
      out[key] = dropNulls(child);
    }
    return out;
  }
  return value;
}

function compareCodePoints(left: string, right: string): number {
  const a = Array.from(left);
  const b = Array.from(right);
  const length = Math.min(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const leftPoint = a[index]?.codePointAt(0) ?? 0;
    const rightPoint = b[index]?.codePointAt(0) ?? 0;
    if (leftPoint !== rightPoint) {
      return leftPoint - rightPoint;
    }
  }
  return a.length - b.length;
}

function normalizeDigestValue(value: unknown, path: string): unknown {
  if (value === null) {
    throw new PlanDigestError(
      "null_in_digest_input",
      `null at ${path} after projection`,
    );
  }
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isInteger(value) || !Number.isSafeInteger(value)) {
      throw new PlanDigestError(
        "non_canonical_number",
        `non-integer number at ${path}`,
      );
    }
    return value;
  }
  if (typeof value === "string") {
    return value.normalize("NFC");
  }
  if (Array.isArray(value)) {
    return value.map((child, index) =>
      normalizeDigestValue(child, `${path}[${index}]`),
    );
  }
  if (isRecord(value)) {
    const out: Record<string, unknown> = {};
    const keys = Object.keys(value).sort(compareCodePoints);
    for (const key of keys) {
      out[key] = normalizeDigestValue(value[key], `${path}.${key}`);
    }
    return out;
  }
  throw new PlanDigestError(
    "unsupported_value",
    `unsupported ${typeof value} at ${path}`,
  );
}

function canonicalJson(value: unknown): string {
  if (typeof value === "string") {
    return JSON.stringify(value.normalize("NFC"));
  }
  if (typeof value === "number") {
    if (!Number.isInteger(value)) {
      throw new PlanDigestError("non_canonical_number", "floats are not canonical");
    }
    return String(value);
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (isRecord(value)) {
    const keys = Object.keys(value).sort(compareCodePoints);
    return `{${keys
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  throw new PlanDigestError(
    "unsupported_value",
    `unsupported ${typeof value} in canonical JSON`,
  );
}

/**
 * The exact frozen projection hashed by `plan_definition_digest`.
 *
 * Only members present in the projection are copied; `null`/absent optional
 * members are dropped by the normalizer.
 */
export function planDefinitionProjection(
  proposal: PlanDefinitionInput,
): Readonly<Record<string, unknown>> {
  if (!isRecord(proposal)) {
    throw new PlanDigestError("root_not_object", "proposal must be an object");
  }
  const science = proposal.science;
  const budget = proposal.budget;
  if (!isRecord(science) || !isRecord(budget)) {
    throw new PlanDigestError(
      "root_not_object",
      "proposal.science and proposal.budget must be objects",
    );
  }
  const projectedScience: Record<string, unknown> = {};
  for (const field of SCIENCE_FIELDS) {
    projectedScience[field] = science[field];
  }
  const projectedBudget: Record<string, unknown> = {};
  for (const field of BUDGET_FIELDS) {
    projectedBudget[field] = budget[field];
  }
  return {
    recipe_digest: proposal.recipe_digest,
    science: projectedScience,
    budget: projectedBudget,
  };
}

/**
 * Compute the frozen `plan_definition_digest`.
 *
 * Byte-for-byte reproduction of plan-identity.md §1.3: drop nulls, reject
 * floats, NFC-normalize strings, sort object keys by code point, compact JSON,
 * UTF-8 without BOM/newline, `sha256:` prefix.
 */
export function computePlanDefinitionDigest(
  proposal: PlanDefinitionInput,
): string {
  const projection = planDefinitionProjection(proposal);
  const normalized = normalizeDigestValue(dropNulls(projection), "$");
  const canonical = canonicalJson(normalized);
  const bytes = Buffer.from(canonical, "utf8");
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

/** Copy a draft and fill in its computed plan definition digest. */
export function freezeProposal(
  draft: Omit<Proposal, "plan_definition_digest">,
): Proposal {
  const digest = computePlanDefinitionDigest(draft);
  return { ...draft, plan_definition_digest: digest };
}

// ---------------------------------------------------------------------------
// Approval binding (plan-identity.md §3)
// ---------------------------------------------------------------------------

function budgetProjection(budget: ProposalBudget): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of BUDGET_FIELDS) {
    out[field] = budget[field];
  }
  return out;
}

function scopeCanonical(scope: AllowedChangeScope): string {
  return canonicalJson(
    normalizeDigestValue(dropNulls(scope), "$.allowed_change_scope"),
  );
}

/**
 * Canonical binding digest over the exact approval tuple:
 * plan_definition_digest + project_id + budget + allowed_change_scope.
 *
 * This mirrors the approval binding, not a second plan digest: the plan digest
 * itself remains the only scientific identity.
 */
export function approvalBindingDigest(binding: ApprovalBinding): string {
  const payload = normalizeDigestValue(
    dropNulls({
      plan_definition_digest: binding.planDefinitionDigest,
      project_id: binding.projectId,
      budget: budgetProjection(binding.budget),
      allowed_change_scope: binding.allowedChangeScope,
    }),
    "$",
  );
  const bytes = Buffer.from(canonicalJson(payload), "utf8");
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

/** True when a stored approval authorizes exactly this proposal. */
export function approvalBindingMatches(
  proposal: Proposal,
  binding: ApprovalBinding,
): boolean {
  if (
    proposal.approval_ref !== null &&
    proposal.approval_ref !== binding.approvalRef
  ) {
    return false;
  }
  if (proposal.plan_definition_digest !== binding.planDefinitionDigest) {
    return false;
  }
  if (proposal.project_id !== binding.projectId) {
    return false;
  }
  if (
    canonicalJson(budgetProjection(proposal.budget)) !==
    canonicalJson(budgetProjection(binding.budget))
  ) {
    return false;
  }
  return (
    scopeCanonical(proposal.allowed_change_scope) ===
    scopeCanonical(binding.allowedChangeScope)
  );
}

/**
 * Throw when an approval is reused for a different scientific definition,
 * project, budget or allowed-change scope.
 */
export function assertApprovalBinding(
  proposal: Proposal,
  binding: ApprovalBinding,
): void {
  if (!approvalBindingMatches(proposal, binding)) {
    throw new Error(
      "approval_binding_mismatch: approval does not bind this plan_definition_digest + project + budget + allowed_change_scope",
    );
  }
}
