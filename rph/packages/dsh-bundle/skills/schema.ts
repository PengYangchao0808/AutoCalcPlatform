/**
 * Explicit, versioned skill I/O schemas.
 *
 * Every skill input/output is an ordinary TypeScript interface plus a runtime
 * `SkillSpec`. The spec is the single source for required fields, enums,
 * patterns and `additionalProperties:false`; a model response that does not
 * validate is never handed to the Supervisor as a typed result.
 *
 * This module is deliberately dependency-free: no JSON-Schema library, no
 * network, no DSH private import. A skill can therefore be validated offline
 * and in a fixture run without a model.
 */

export interface ValidationIssue {
  readonly path: string;
  readonly code: string;
  readonly message: string;
}

export type FieldKind =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "object"
  | "array";

export interface FieldSpec {
  readonly kind: FieldKind;
  readonly required?: boolean;
  readonly nullable?: boolean;
  readonly enum?: readonly string[];
  readonly pattern?: RegExp;
  readonly min?: number;
  readonly minLength?: number;
  readonly minItems?: number;
  readonly unique?: boolean;
  readonly items?: FieldSpec;
  readonly fields?: Readonly<Record<string, FieldSpec>>;
  /** Accept arbitrary nested keys (a platform-owned opaque object). */
  readonly passthrough?: boolean;
}

/** Versioned object contract shared by all four skills. */
export interface SkillSpec {
  readonly skill: string;
  readonly schema: string;
  readonly version: string;
  readonly fields: Readonly<Record<string, FieldSpec>>;
}

/** Common envelope every skill request/response carries. */
export interface SkillEnvelope {
  readonly schema: string;
  readonly skill: string;
  readonly skill_version: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function issue(
  issues: ValidationIssue[],
  path: string,
  code: string,
  message: string,
): void {
  issues.push({ path, code, message });
}

function validateFields(
  value: Readonly<Record<string, unknown>>,
  fields: Readonly<Record<string, FieldSpec>>,
  path: string,
  issues: ValidationIssue[],
): void {
  const allowed = new Set(Object.keys(fields));
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) {
      issue(issues, `${path}.${key}`, "additional_property", "not allowed");
    }
  }
  for (const [key, spec] of Object.entries(fields)) {
    const at = `${path}.${key}`;
    const present = Object.hasOwn(value, key);
    const raw = value[key];
    if (!present) {
      if (spec.required === true) {
        issue(issues, at, "required", "is required");
      }
      continue;
    }
    if (raw === null) {
      if (spec.nullable !== true) {
        issue(issues, at, "type", "null is not allowed");
      }
      continue;
    }
    validateField(raw, spec, at, issues);
  }
}

function validateField(
  raw: unknown,
  spec: FieldSpec,
  path: string,
  issues: ValidationIssue[],
): void {
  switch (spec.kind) {
    case "string":
      if (!isText(raw)) {
        issue(issues, path, "type", "must be a non-empty string");
        return;
      }
      if (spec.minLength !== undefined && raw.length < spec.minLength) {
        issue(issues, path, "min_length", `must have length >= ${spec.minLength}`);
      }
      if (spec.enum !== undefined && !spec.enum.includes(raw)) {
        issue(issues, path, "enum", `must be one of ${spec.enum.join(", ")}`);
      }
      if (spec.pattern !== undefined && !spec.pattern.test(raw)) {
        issue(issues, path, "pattern", `must match ${String(spec.pattern)}`);
      }
      return;
    case "integer":
      if (
        typeof raw !== "number" ||
        !Number.isInteger(raw) ||
        !Number.isSafeInteger(raw)
      ) {
        issue(issues, path, "type", "must be an integer");
        return;
      }
      if (spec.min !== undefined && raw < spec.min) {
        issue(issues, path, "minimum", `must be >= ${spec.min}`);
      }
      return;
    case "number":
      if (typeof raw !== "number" || !Number.isFinite(raw)) {
        issue(issues, path, "type", "must be a finite number");
        return;
      }
      if (spec.min !== undefined && raw < spec.min) {
        issue(issues, path, "minimum", `must be >= ${spec.min}`);
      }
      return;
    case "boolean":
      if (typeof raw !== "boolean") {
        issue(issues, path, "type", "must be a boolean");
      }
      return;
    case "object":
      if (!isRecord(raw)) {
        issue(issues, path, "type", "must be an object");
        return;
      }
      if (spec.passthrough === true) {
        return;
      }
      validateFields(raw, spec.fields ?? {}, path, issues);
      return;
    case "array": {
      if (!Array.isArray(raw)) {
        issue(issues, path, "type", "must be an array");
        return;
      }
      if (spec.minItems !== undefined && raw.length < spec.minItems) {
        issue(issues, path, "min_items", `must have at least ${spec.minItems} item(s)`);
      }
      const itemSpec = spec.items;
      if (itemSpec === undefined) {
        return;
      }
      raw.forEach((item, index) =>
        validateField(item, itemSpec, `${path}[${index}]`, issues),
      );
      if (spec.unique === true) {
        const seen = new Set<string>();
        raw.forEach((item, index) => {
          const key = JSON.stringify(item);
          if (seen.has(key)) {
            issue(issues, `${path}[${index}]`, "unique", "duplicate item");
          }
          seen.add(key);
        });
      }
      return;
    }
  }
}

/**
 * Validate one skill request/response against its versioned spec.
 *
 * The envelope is validated before the body so an old or renamed skill can
 * never be accepted as the current contract.
 */
export function validateSkillObject(
  value: unknown,
  spec: SkillSpec,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  if (value["schema"] !== spec.schema) {
    issue(issues, "$.schema", "version", `must be ${spec.schema}`);
  }
  if (value["skill"] !== spec.skill) {
    issue(issues, "$.skill", "skill", `must be ${spec.skill}`);
  }
  if (value["skill_version"] !== spec.version) {
    issue(issues, "$.skill_version", "version", `must be ${spec.version}`);
  }
  const body: Record<string, unknown> = { ...value };
  delete body["schema"];
  delete body["skill"];
  delete body["skill_version"];
  validateFields(body, spec.fields, "$", issues);
  return issues;
}

/**
 * Validate a plain object against a field map (no skill envelope).
 *
 * Used for contracts that arrive from the platform (e.g. EvidenceReport) and
 * therefore cannot be required to carry DSH skill metadata.
 */
export function validateObjectFields(
  value: unknown,
  fields: Readonly<Record<string, FieldSpec>>,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    issue(issues, "$", "type", "must be an object");
    return issues;
  }
  validateFields(value, fields, "$", issues);
  return issues;
}

/** True when the value validates against the spec. */
export function isSkillObject<T>(value: unknown, spec: SkillSpec): value is T {
  return validateSkillObject(value, spec).length === 0;
}

/** Empty body used by the deterministic offline runners. */
export function envelope<S extends string>(
  schema: S,
  skill: string,
  version: string,
): { readonly schema: S; readonly skill: string; readonly skill_version: string } {
  return { schema, skill, skill_version: version };
}
