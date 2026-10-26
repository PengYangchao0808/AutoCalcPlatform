/**
 * Research semantics surface.
 *
 * This package is the bottom of the dependency direction
 * (`cli -> dsh-bundle -> bridge -> research`). It owns only immutable
 * references and pure derivations; it never imports the platform store, the
 * bridge, the DSH bundle or the CLI.
 */

/** Stable reference to one revision of a ResearchRecord. */
export interface ResearchRecordRef {
  readonly projectId: string;
  readonly recordId: string;
  readonly revision: number;
}

/** Stable reference to a research Proposal and its approval binding. */
export interface ProposalRef {
  readonly projectId: string;
  readonly proposalId: string;
  readonly approvalRef: string | null;
}

/** Kind discriminator for the research memory index. */
export type ResearchMemoryKind = "record" | "proposal";

/** Key used to look up durable research memory for a revision. */
export interface ResearchMemoryKey {
  readonly projectId: string;
  readonly kind: ResearchMemoryKind;
  readonly id: string;
  readonly revision: number;
}

function requireText(value: string, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`${label} must be a non-empty string`);
  }
  return value;
}

function requireRevision(value: number, label: string): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
  return value;
}

/** Namespaced key for a ResearchRecord revision. */
export function recordKey(ref: ResearchRecordRef): string {
  return `${requireText(ref.projectId, "projectId")}:record:${requireText(ref.recordId, "recordId")}@${requireRevision(ref.revision, "revision")}`;
}

/** Namespaced key for a Proposal revision. */
export function proposalKey(ref: ProposalRef): string {
  return `${requireText(ref.projectId, "projectId")}:proposal:${requireText(ref.proposalId, "proposalId")}@${ref.approvalRef ?? "unapproved"}`;
}

/** Namespaced key for a research-memory entry. */
export function memoryKey(key: ResearchMemoryKey): string {
  return `${requireText(key.projectId, "projectId")}:${key.kind}:${requireText(key.id, "id")}@${requireRevision(key.revision, "revision")}`;
}

/** True when two record references address the same project/record/revision. */
export function isSameRecord(a: ResearchRecordRef, b: ResearchRecordRef): boolean {
  return recordKey(a) === recordKey(b);
}

/** Move a record reference to a newer revision without mutating the input. */
export function withRecordRevision(
  ref: ResearchRecordRef,
  revision: number,
): ResearchRecordRef {
  return Object.freeze({
    projectId: ref.projectId,
    recordId: ref.recordId,
    revision: requireRevision(revision, "revision"),
  });
}

export * from "./record.js";
export * from "./proposal.js";
export * from "./cas.js";
export * from "./store.js";
export * from "./memory.js";
export * from "./submit-intent.js";
