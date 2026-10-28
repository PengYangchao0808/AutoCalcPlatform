/**
 * In-memory P2 platform client for the research-store tests.
 *
 * It is deliberately dumb and synchronous underneath the async signature: it
 * stores the versioned research resources the store creates and emulates the
 * server-side CAS/idempotency rules.  No network, database, file or process is
 * touched.  Every call is recorded so a test can prove the store stayed on the
 * T-09 allowlist.
 */

import {
  RESEARCH_P2_TOOLS,
  type PlatformToolResult,
  type Proposal,
  type ResearchPlatformClient,
  type ResearchRecord,
} from "@rph/research";

export interface FixtureCall {
  readonly name: string;
  readonly params: Readonly<Record<string, unknown>>;
}

interface StoredRecord {
  record: ResearchRecord;
  revision: number;
}

interface StoredProposalResource {
  proposal: Proposal;
  revision: number;
}

interface IdempotencyEntry {
  readonly signature: string;
  readonly result: PlatformToolResult;
}

const ALLOWED_TOOLS: ReadonlySet<string> = new Set(
  Object.values(RESEARCH_P2_TOOLS),
);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value) ?? "null";
  }
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`)
    .join(",")}}`;
}

function ok(data: Readonly<Record<string, unknown>>): PlatformToolResult {
  return {
    isError: false,
    structuredContent: {
      schema_version: 1,
      status: "ok",
      exit_code: 0,
      data,
      error: null,
    },
  };
}

function failure(
  code: string,
  params: Readonly<Record<string, unknown>> = {},
): PlatformToolResult {
  return {
    isError: true,
    structuredContent: {
      schema_version: 1,
      status: "error",
      exit_code: 1,
      data: null,
      error: {
        code,
        message: code,
        params,
        retryable: false,
        diagnostic_ref: null,
      },
    },
  };
}

function textParam(
  params: Readonly<Record<string, unknown>>,
  key: string,
): string | null {
  const value = params[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function recordKey(project: string, resource: string): string {
  return `${project}:record:${resource}`;
}

function proposalKey(project: string, resource: string): string {
  return `${project}:proposal:${resource}`;
}

/** In-memory versioned resource store with P2 semantics. */
export class FixturePlatformClient implements ResearchPlatformClient {
  readonly calls: FixtureCall[] = [];
  private readonly records = new Map<string, StoredRecord>();
  private readonly proposals = new Map<string, StoredProposalResource>();
  private readonly idempotency = new Map<string, IdempotencyEntry>();

  async callTool(
    rawName: string,
    params: Readonly<Record<string, unknown>>,
  ): Promise<PlatformToolResult> {
    this.calls.push({ name: rawName, params });
    if (!ALLOWED_TOOLS.has(rawName)) {
      throw new Error(`fixture received a non-allowlisted tool: ${rawName}`);
    }
    const idempotencyKey = textParam(params, "idempotency_key");
    if (idempotencyKey !== null) {
      const signature = stableStringify({
        tool: rawName,
        params: { ...params, idempotency_key: undefined },
      });
      const previous = this.idempotency.get(idempotencyKey);
      if (previous !== undefined) {
        if (previous.signature !== signature) {
          return failure("revision_conflict", {
            rule: "idempotency_key_reuse",
            idempotency_key: idempotencyKey,
          });
        }
        return previous.result;
      }
      const result = this.dispatch(rawName, params);
      this.idempotency.set(idempotencyKey, { signature, result });
      return result;
    }
    return this.dispatch(rawName, params);
  }

  recordCount(): number {
    return this.records.size;
  }

  proposalCount(): number {
    return this.proposals.size;
  }

  getRecord(project: string, resource: string): ResearchRecord | null {
    return this.records.get(recordKey(project, resource))?.record ?? null;
  }

  getProposal(project: string, resource: string): Proposal | null {
    return this.proposals.get(proposalKey(project, resource))?.proposal ?? null;
  }

  updateCasCallCount(): number {
    return this.calls.filter(
      (call) => call.name === RESEARCH_P2_TOOLS["proposal.update_cas"],
    ).length;
  }

  private dispatch(
    rawName: string,
    params: Readonly<Record<string, unknown>>,
  ): PlatformToolResult {
    if (rawName === RESEARCH_P2_TOOLS["proposal.create"]) {
      return this.create(params);
    }
    if (rawName === RESEARCH_P2_TOOLS["proposal.read"]) {
      return this.read(params);
    }
    if (rawName === RESEARCH_P2_TOOLS["proposal.update_cas"]) {
      return this.updateCas(params);
    }
    return failure("invalid_parameter", { rule: "unknown_operation", rawName });
  }

  private create(
    params: Readonly<Record<string, unknown>>,
  ): PlatformToolResult {
    const kind = params["kind"];
    const project = textParam(params, "project_id");
    const resource = textParam(params, "resource_id");
    if (project === null || resource === null) {
      return failure("invalid_parameter", {
        rule: "project_and_resource_required",
      });
    }
    if (kind === "research_record") {
      const raw = params["record"];
      if (!isRecord(raw)) {
        return failure("invalid_parameter", { rule: "record_required" });
      }
      const record = raw as unknown as ResearchRecord;
      if (record.project_id !== project) {
        return failure("project_mismatch", {
          expected_project: project,
          observed_project: record.project_id,
        });
      }
      const key = recordKey(project, resource);
      if (this.records.has(key)) {
        return failure("duplicate_round", { resource_id: resource });
      }
      this.records.set(key, { record, revision: 1 });
      return ok({
        reference: { kind: "research_record", id: resource },
        record,
        revision: 1,
        created: true,
      });
    }
    if (kind === "proposal") {
      const raw = params["proposal"];
      if (!isRecord(raw)) {
        return failure("invalid_parameter", { rule: "proposal_required" });
      }
      const proposal = raw as unknown as Proposal;
      if (proposal.project_id !== project) {
        return failure("project_mismatch", {
          expected_project: project,
          observed_project: proposal.project_id,
        });
      }
      const key = proposalKey(project, resource);
      if (this.proposals.has(key)) {
        return failure("revision_conflict", { resource_id: resource });
      }
      this.proposals.set(key, { proposal, revision: 1 });
      return ok({
        reference: { kind: "proposal", id: resource },
        proposal,
        revision: 1,
        created: true,
      });
    }
    return failure("invalid_parameter", { rule: "unknown_kind", kind });
  }

  private read(
    params: Readonly<Record<string, unknown>>,
  ): PlatformToolResult {
    const kind = params["kind"];
    const project = textParam(params, "project_id");
    const resource = textParam(params, "resource_id");
    if (project === null || resource === null) {
      return failure("invalid_parameter", {
        rule: "project_and_resource_required",
      });
    }
    const requested = params["revision"];
    if (kind === "research_record") {
      const stored = this.records.get(recordKey(project, resource));
      if (stored === undefined) {
        return failure("not_prepared", { resource_id: resource });
      }
      if (
        typeof requested === "number" &&
        requested !== stored.revision
      ) {
        return failure("stale_reference", {
          expected_revision: requested,
          current_revision: stored.revision,
        });
      }
      return ok({ record: stored.record, revision: stored.revision });
    }
    if (kind === "proposal") {
      const stored = this.proposals.get(proposalKey(project, resource));
      if (stored === undefined) {
        return failure("not_prepared", { resource_id: resource });
      }
      if (
        typeof requested === "number" &&
        requested !== stored.revision
      ) {
        return failure("stale_reference", {
          expected_revision: requested,
          current_revision: stored.revision,
        });
      }
      return ok({ proposal: stored.proposal, revision: stored.revision });
    }
    return failure("invalid_parameter", { rule: "unknown_kind", kind });
  }

  private updateCas(
    params: Readonly<Record<string, unknown>>,
  ): PlatformToolResult {
    const project = textParam(params, "project_id");
    const resource = textParam(params, "resource_id");
    const expected = params["expected_revision"];
    if (
      project === null ||
      resource === null ||
      typeof expected !== "number" ||
      !Number.isInteger(expected)
    ) {
      return failure("invalid_parameter", {
        rule: "project_resource_expected_required",
      });
    }
    const key = recordKey(project, resource);
    const stored = this.records.get(key);
    if (stored === undefined) {
      return failure("not_prepared", { resource_id: resource });
    }
    if (stored.revision !== expected) {
      return failure("revision_conflict", {
        rule: "expected_revision_mismatch",
        expected_revision: expected,
        current_revision: stored.revision,
      });
    }
    const raw = params["record"];
    if (!isRecord(raw)) {
      return failure("invalid_parameter", { rule: "record_required" });
    }
    const next = raw as unknown as ResearchRecord;
    if (next.project_id !== project) {
      return failure("project_mismatch", {
        expected_project: project,
        observed_project: next.project_id,
      });
    }
    if (next.record_revision !== expected + 1) {
      return failure("revision_conflict", {
        rule: "invalid_next_revision",
        expected_revision: expected + 1,
        next_revision: next.record_revision,
      });
    }
    this.records.set(key, { record: next, revision: next.record_revision });
    return ok({ record: next, revision: next.record_revision });
  }
}
