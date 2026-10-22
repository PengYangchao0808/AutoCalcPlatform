/**
 * ResearchStore: Proposal / ResearchRecord persistence over the P2 map.
 *
 * The store is deliberately thin: it validates the frozen models, derives the
 * round/event idempotency keys, caches the latest revision it has observed, and
 * delegates every read/write to the injected platform client through the
 * T-09 P2 operations.  It has no database, no file access and no subprocess.
 *
 * DSH session state is never consulted: the store's only authority is the
 * versioned resource returned by the platform.
 */

import type { ProposalRef, ResearchRecordRef } from "./index.js";
import {
  compareAndSwap as casCompareAndSwap,
  callResearchTool,
  recordValidationError,
  storeFail,
  storeOk,
  type CasOptions,
  type ResearchPlatformClient,
  type StoreResult,
} from "./cas.js";
import {
  recordRefFor,
  researchRecordId,
  researchRoundKey,
  validateResearchRecord,
  type ResearchRecord,
} from "./record.js";
import {
  approvalBindingMatches,
  validateProposal,
  type ApprovalBinding,
  type Proposal,
} from "./proposal.js";

export interface RecordWrite {
  readonly ref: ResearchRecordRef;
  readonly record: ResearchRecord;
  readonly created: boolean;
}

export interface StoredProposal {
  readonly proposal: Proposal;
  readonly revision: number;
  readonly created: boolean;
}

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
  const keys = Object.keys(record).sort();
  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`)
    .join(",")}}`;
}

function recordKey(projectId: string, recordId: string): string {
  return `${projectId}:record:${recordId}`;
}

function proposalKey(projectId: string, proposalId: string): string {
  return `${projectId}:proposal:${proposalId}`;
}

function dataOf(
  structuredContent: Readonly<Record<string, unknown>>,
): Readonly<Record<string, unknown>> | null {
  const data = structuredContent["data"];
  return isRecord(data) ? data : null;
}

/** Persistence facade over the frozen P2 research operations. */
export class ResearchStore {
  private readonly client: ResearchPlatformClient;
  private readonly roundIndex = new Map<string, ResearchRecord>();
  private readonly latestRecords = new Map<string, ResearchRecord>();
  private readonly eventIndex = new Map<string, ResearchRecord>();
  private readonly proposals = new Map<string, StoredProposal>();

  constructor(client: ResearchPlatformClient) {
    this.client = client;
  }

  /** Latest revision observed by this store for a record resource. */
  latestRecord(projectId: string, recordId: string): ResearchRecord | null {
    return this.latestRecords.get(recordKey(projectId, recordId)) ?? null;
  }

  /** Latest proposal revision observed by this store. */
  latestProposal(projectId: string, proposalId: string): StoredProposal | null {
    return this.proposals.get(proposalKey(projectId, proposalId)) ?? null;
  }

  /**
   * Create one round record through `proposal.create`.
   *
   * The round key is the idempotency key.  A replay with the identical body
   * returns the existing record (`created: false`) without issuing a write; a
   * different body for the same round is `duplicate_round`.
   */
  async createRecord(
    record: ResearchRecord,
  ): Promise<StoreResult<RecordWrite>> {
    const issues = validateResearchRecord(record);
    const invalid = recordValidationError(issues);
    if (invalid !== null) {
      return { ok: false, error: invalid };
    }
    if (record.record_revision !== 1) {
      return storeFail(
        "invalid_record",
        "a created record must start at record_revision 1",
        { record_revision: record.record_revision },
      );
    }

    const recordId = researchRecordId(record);
    const roundKey = researchRoundKey(record);
    const existing = this.roundIndex.get(roundKey);
    if (existing !== undefined) {
      if (stableStringify(existing) !== stableStringify(record)) {
        return storeFail(
          "duplicate_round",
          "round already has a different record",
          {
            round_key: roundKey,
            existing_revision: existing.record_revision,
          },
        );
      }
      return storeOk({
        ref: recordRefFor(existing),
        record: existing,
        created: false,
      });
    }

    const response = await callResearchTool(this.client, "proposal.create", {
      kind: "research_record",
      project_id: record.project_id,
      resource_id: recordId,
      round: record.round,
      idempotency_key: roundKey,
      record,
    });
    if (!response.ok) {
      return response;
    }
    const data = dataOf(response.value);
    const rawRecord = data?.["record"] ?? response.value["record"];
    if (!isRecord(rawRecord)) {
      return storeFail(
        "platform_error",
        "proposal.create response has no record",
        { operation: "proposal.create" },
      );
    }
    const returnedIssues = validateResearchRecord(rawRecord);
    const returnedInvalid = recordValidationError(returnedIssues);
    if (returnedInvalid !== null) {
      return { ok: false, error: returnedInvalid };
    }
    const persisted = rawRecord as unknown as ResearchRecord;
    if (persisted.project_id !== record.project_id) {
      return storeFail(
        "project_mismatch",
        "platform returned a record from a different project",
        {
          expected_project: record.project_id,
          observed_project: persisted.project_id,
        },
      );
    }
    if (stableStringify(persisted) !== stableStringify(record)) {
      return storeFail(
        "duplicate_round",
        "platform already holds a different body for this round key",
        {
          round_key: roundKey,
          persisted_revision: persisted.record_revision,
        },
      );
    }
    this.rememberRecord(persisted);
    this.roundIndex.set(roundKey, persisted);
    const created = data?.["created"];
    return storeOk({
      ref: recordRefFor(persisted),
      record: persisted,
      created: created !== false,
    });
  }

  /** Read one record revision through `proposal.read`. */
  async readRecord(
    ref: ResearchRecordRef,
  ): Promise<StoreResult<ResearchRecord>> {
    const response = await callResearchTool(this.client, "proposal.read", {
      kind: "research_record",
      project_id: ref.projectId,
      resource_id: ref.recordId,
      revision: ref.revision,
    });
    if (!response.ok) {
      return response;
    }
    const data = dataOf(response.value);
    const rawRecord = data?.["record"] ?? response.value["record"];
    if (!isRecord(rawRecord)) {
      return storeFail(
        "platform_error",
        "proposal.read response has no record",
        { operation: "proposal.read" },
      );
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
        "read returned a record from a different project",
        {
          expected_project: ref.projectId,
          observed_project: record.project_id,
        },
      );
    }
    if (record.record_revision !== ref.revision) {
      return storeFail(
        "stale_reference",
        "read returned a different revision than requested",
        {
          expected_revision: ref.revision,
          observed_revision: record.record_revision,
        },
      );
    }
    this.rememberRecord(record);
    return storeOk(record);
  }

  /**
   * CAS one record revision through `proposal.update_cas`.
   *
   * A locally observed newer revision is rejected as `stale_reference` before
   * any call.  Concurrent writers both pass the local check and race at the
   * platform: exactly one wins and the other gets `revision_conflict`.
   */
  async compareAndSwap(
    ref: ResearchRecordRef,
    expectedRevision: number,
    next: ResearchRecord,
    options: CasOptions = {},
  ): Promise<StoreResult<ResearchRecord>> {
    const eventKey = options.eventKey;
    if (eventKey !== undefined) {
      const replayed = this.eventIndex.get(eventKey);
      if (replayed !== undefined) {
        return storeOk(replayed);
      }
    }
    const latest = this.latestRecord(ref.projectId, ref.recordId);
    if (latest !== null && ref.revision < latest.record_revision) {
      return storeFail(
        "stale_reference",
        "ref revision is older than the latest observed record",
        {
          ref_revision: ref.revision,
          latest_revision: latest.record_revision,
        },
      );
    }
    const result = await casCompareAndSwap(
      this.client,
      ref,
      expectedRevision,
      next,
      options,
    );
    if (!result.ok) {
      return result;
    }
    this.rememberRecord(result.value);
    if (eventKey !== undefined) {
      this.eventIndex.set(eventKey, result.value);
    }
    return result;
  }

  /** Create a Proposal through `proposal.create` (digest already frozen). */
  async createProposal(
    proposal: Proposal,
  ): Promise<StoreResult<StoredProposal>> {
    const issues = validateProposal(proposal);
    if (issues.length > 0) {
      return storeFail(
        "invalid_proposal",
        issues
          .map((item) => `${item.path}: ${item.message}`)
          .join("; "),
        {
          issues: issues.slice(0, 20).map((item) => ({ ...item })),
        },
      );
    }
    const key = proposalKey(proposal.project_id, proposal.proposal_id);
    const existing = this.proposals.get(key);
    if (existing !== undefined) {
      if (stableStringify(existing.proposal) !== stableStringify(proposal)) {
        return storeFail(
          "revision_conflict",
          "proposal id already holds a different definition",
          { proposal_id: proposal.proposal_id },
        );
      }
      return storeOk({ ...existing, created: false });
    }
    const response = await callResearchTool(this.client, "proposal.create", {
      kind: "proposal",
      project_id: proposal.project_id,
      resource_id: proposal.proposal_id,
      round: proposal.round,
      idempotency_key: `${key}:round-${proposal.round}`,
      proposal,
    });
    if (!response.ok) {
      return response;
    }
    const data = dataOf(response.value);
    const rawProposal = data?.["proposal"] ?? response.value["proposal"];
    if (!isRecord(rawProposal)) {
      return storeFail(
        "platform_error",
        "proposal.create response has no proposal",
        { operation: "proposal.create" },
      );
    }
    const returnedIssues = validateProposal(rawProposal);
    if (returnedIssues.length > 0) {
      return storeFail(
        "invalid_proposal",
        "platform persisted an invalid proposal",
        {
          issues: returnedIssues
            .slice(0, 20)
            .map((item) => ({ ...item })),
        },
      );
    }
    const persisted = rawProposal as unknown as Proposal;
    if (persisted.project_id !== proposal.project_id) {
      return storeFail(
        "project_mismatch",
        "platform returned a proposal from a different project",
        {
          expected_project: proposal.project_id,
          observed_project: persisted.project_id,
        },
      );
    }
    const revision = data?.["revision"];
    if (
      typeof revision !== "number" ||
      !Number.isInteger(revision) ||
      revision < 1
    ) {
      return storeFail(
        "platform_error",
        "proposal.create response has no positive revision",
        { operation: "proposal.create" },
      );
    }
    const stored: StoredProposal = {
      proposal: persisted,
      revision,
      created: data?.["created"] !== false,
    };
    this.proposals.set(key, stored);
    return storeOk(stored);
  }

  /** Read one Proposal revision through `proposal.read`. */
  async readProposal(
    ref: Pick<ProposalRef, "projectId" | "proposalId">,
    revision?: number,
  ): Promise<StoreResult<StoredProposal>> {
    const params: Record<string, unknown> = {
      kind: "proposal",
      project_id: ref.projectId,
      resource_id: ref.proposalId,
    };
    if (revision !== undefined) {
      params["revision"] = revision;
    }
    const response = await callResearchTool(
      this.client,
      "proposal.read",
      params,
    );
    if (!response.ok) {
      return response;
    }
    const data = dataOf(response.value);
    const rawProposal = data?.["proposal"] ?? response.value["proposal"];
    if (!isRecord(rawProposal)) {
      return storeFail(
        "platform_error",
        "proposal.read response has no proposal",
        { operation: "proposal.read" },
      );
    }
    const issues = validateProposal(rawProposal);
    if (issues.length > 0) {
      return storeFail(
        "invalid_proposal",
        "platform returned an invalid proposal",
        { issues: issues.slice(0, 20).map((item) => ({ ...item })) },
      );
    }
    const proposal = rawProposal as unknown as Proposal;
    if (proposal.project_id !== ref.projectId) {
      return storeFail(
        "project_mismatch",
        "read returned a proposal from a different project",
        {
          expected_project: ref.projectId,
          observed_project: proposal.project_id,
        },
      );
    }
    const storedRevision = data?.["revision"];
    const resolvedRevision =
      typeof storedRevision === "number" ? storedRevision : revision;
    if (
      resolvedRevision === undefined ||
      !Number.isInteger(resolvedRevision) ||
      resolvedRevision < 1
    ) {
      return storeFail(
        "platform_error",
        "proposal.read response has no positive revision",
        { operation: "proposal.read" },
      );
    }
    if (revision !== undefined && resolvedRevision !== revision) {
      return storeFail(
        "stale_reference",
        "read returned a different proposal revision than requested",
        {
          expected_revision: revision,
          observed_revision: resolvedRevision,
        },
      );
    }
    const stored: StoredProposal = {
      proposal,
      revision: resolvedRevision,
      created: false,
    };
    this.proposals.set(
      proposalKey(proposal.project_id, proposal.proposal_id),
      stored,
    );
    return storeOk(stored);
  }

  /**
   * Verify an approval binds this scientific definition.
   *
   * This is a read-only contract check; approval lifecycle owners (T-27/T-28)
   * remain the authority for the approval's own state.
   */
  verifyApprovalBinding(
    proposal: Proposal,
    binding: ApprovalBinding,
  ): StoreResult<true> {
    if (!approvalBindingMatches(proposal, binding)) {
      return storeFail(
        "approval_binding_mismatch",
        "approval does not bind this plan_definition_digest + project + budget + allowed_change_scope",
        {
          approval_ref: binding.approvalRef,
          plan_definition_digest: proposal.plan_definition_digest,
          project_id: proposal.project_id,
        },
      );
    }
    return storeOk(true);
  }

  private rememberRecord(record: ResearchRecord): void {
    const key = recordKey(record.project_id, researchRecordId(record));
    const latest = this.latestRecords.get(key);
    if (latest === undefined || latest.record_revision <= record.record_revision) {
      this.latestRecords.set(key, record);
    }
  }
}
