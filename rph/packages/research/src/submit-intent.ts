/**
 * Submit-intent keys and the pre-call intent ledger.
 *
 * The RPH side of D-IDENT-07: a submit idempotency key for a research round is
 * derived deterministically from `project + research + round + plan_digest`
 * **and persisted before the call**, so a retry after a crash replays the same
 * key instead of inventing a second one.  The platform receives the key as an
 * opaque value and never completes or rewrites it.
 *
 * Key layout (additive to the frozen `researchRoundKey`, D-IDENT-06):
 *
 *     <project>:record:<research>:round-<round>:submit:plan-<digest hex>
 *
 * The existing round/event key functions keep their frozen encoding; the new
 * segments are appended at the end, so an already-persisted round key is still
 * addressable.  A changed `plan_definition_digest` for the same round is a typed
 * `plan_digest_changed` conflict carrying `next_round`: the old key is never
 * rewritten and the old approval can no longer authorize the new plan.
 *
 * The ledger is storage-agnostic: a caller injects a `SubmitIntentStore`.  The
 * default in-memory store is a cache, not a durable database; this module
 * deliberately does not open a file or a database (the research package owns no
 * I/O).
 */

import { researchRoundKey } from "./record.js";

/** The stage marker appended to the frozen round key. */
export const SUBMIT_INTENT_SEGMENT = "submit";

/** Plan-digest segment prefix (`plan-<64 hex>`; the digest's own `sha256:` is dropped). */
export const SUBMIT_INTENT_PLAN_SEGMENT = "plan-";

export const SUBMIT_INTENT_STATES = [
  "prepared",
  "submitted",
  "superseded",
] as const;

export type SubmitIntentState = (typeof SUBMIT_INTENT_STATES)[number];

const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;

/** One persisted submit intent (the key is opaque to the platform). */
export interface SubmitIntent {
  readonly key: string;
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
  readonly state: SubmitIntentState;
  readonly revision: number;
}

/** A typed plan-digest conflict: a new round is required, no key rewrite. */
export class SubmitIntentConflict extends Error {
  readonly code = "plan_digest_changed";
  readonly next_round: number;
  readonly previous_key: string;

  constructor(previous: SubmitIntent, nextRound: number) {
    super(
      `plan_digest_changed: round ${previous.round} already holds an intent for another plan`,
    );
    this.name = "SubmitIntentConflict";
    this.next_round = nextRound;
    this.previous_key = previous.key;
  }

  toParams(): Readonly<Record<string, unknown>> {
    return Object.freeze({
      rule: this.code,
      next_round: this.next_round,
      previous_key: this.previous_key,
    });
  }
}

/** Persistence port for submit intents (the caller owns durability). */
export interface SubmitIntentStore {
  load(scope: string): SubmitIntent | null;
  save(intent: SubmitIntent): void;
}

/** In-memory store: a cache for tests and single-process callers. */
export class MemorySubmitIntentStore implements SubmitIntentStore {
  private readonly rows = new Map<string, SubmitIntent>();

  load(scope: string): SubmitIntent | null {
    return this.rows.get(scope) ?? null;
  }

  save(intent: SubmitIntent): void {
    this.rows.set(submitIntentScope(intent), intent);
  }

  /** Every persisted intent, in insertion order (superseded rows included). */
  entries(): readonly SubmitIntent[] {
    return Object.freeze([...this.rows.values()]);
  }
}

function requireText(value: string, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`${label} must be a non-empty string`);
  }
  if (value.includes(":")) {
    throw new TypeError(`${label} must not contain ':' (key segment rule)`);
  }
  return value;
}

function requireRound(value: number): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new TypeError("round must be a non-negative integer");
  }
  return value;
}

/** `project:research:round-<n>` — one submit slot per research round. */
export function submitIntentScope(identity: {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
}): string {
  return `${requireText(identity.project_id, "project_id")}:${requireText(
    identity.research_id,
    "research_id",
  )}:round-${requireRound(identity.round)}`;
}

/**
 * Derive the stable submit key for one research round and plan.
 *
 * Two calls for the same identity and digest always produce the same key; any
 * segment change (including the plan digest) produces a different key.
 */
export function submitIntentKey(identity: {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
}): string {
  const digest = identity.plan_definition_digest;
  if (typeof digest !== "string" || !DIGEST_RE.test(digest)) {
    throw new TypeError("plan_definition_digest must be sha256:<64 lowercase hex>");
  }
  const roundKey = researchRoundKey({
    project_id: identity.project_id,
    research_id: identity.research_id,
    round: identity.round,
  });
  return `${roundKey}:${SUBMIT_INTENT_SEGMENT}:${
    SUBMIT_INTENT_PLAN_SEGMENT
  }${digest.slice("sha256:".length)}`;
}

export interface PreparedSubmitIntent {
  readonly intent: SubmitIntent;
  readonly created: boolean;
}

/**
 * The submit-intent ledger: derive, persist *before* the call, replay safely.
 *
 * `prepare` is the only entry point that may create a key.  It persists the
 * intent through the injected store first and returns it; a caller then uses
 * `intent.key` as `SubmitRequest.idempotency_key`.  A replay for the same
 * project/research/round/digest returns the persisted intent unchanged.  A
 * different digest for the same round throws `SubmitIntentConflict`; the caller
 * must open `next_round` and prepare a fresh intent there.
 */
export class SubmitIntentLedger {
  private readonly store: SubmitIntentStore;

  constructor(store: SubmitIntentStore = new MemorySubmitIntentStore()) {
    this.store = store;
  }

  /** Look up the intent already persisted for this round scope. */
  load(identity: {
    readonly project_id: string;
    readonly research_id: string;
    readonly round: number;
  }): SubmitIntent | null {
    return this.store.load(submitIntentScope(identity));
  }

  /**
   * Persist (or replay) one intent and return it.
   *
   * @throws SubmitIntentConflict when the round already holds another plan.
   */
  prepare(identity: {
    readonly project_id: string;
    readonly research_id: string;
    readonly round: number;
    readonly plan_definition_digest: string;
  }): PreparedSubmitIntent {
    const key = submitIntentKey(identity);
    const existing = this.load(identity);
    if (existing !== null) {
      if (existing.plan_definition_digest !== identity.plan_definition_digest) {
        throw new SubmitIntentConflict(existing, existing.round + 1);
      }
      if (existing.key !== key) {
        // Same digest, different key: the key function changed under a
        // persisted row.  Never silently re-derive; keep the stored key.
        return { intent: existing, created: false };
      }
      return { intent: existing, created: false };
    }
    const intent: SubmitIntent = Object.freeze({
      key,
      project_id: requireText(identity.project_id, "project_id"),
      research_id: requireText(identity.research_id, "research_id"),
      round: requireRound(identity.round),
      plan_definition_digest: identity.plan_definition_digest,
      state: "prepared" as const,
      revision: 1,
    });
    this.store.save(intent);
    return { intent, created: true };
  }

  /**
   * Mark the intent as submitted (in-flight).  Never changes the key.
   *
   * Returns `null` when no derived intent exists for the round (a caller that
   * supplied its own opaque key never went through `prepare`).  A derived
   * submit always calls `prepare` first, so a missing row is an in-memory
   * ledger miss, never a silent key invention.
   */
  markSubmitted(identity: {
    readonly project_id: string;
    readonly research_id: string;
    readonly round: number;
  }): SubmitIntent | null {
    const existing = this.load(identity);
    if (existing === null) {
      return null;
    }
    if (existing.state !== "prepared") {
      return existing;
    }
    const submitted: SubmitIntent = Object.freeze({
      ...existing,
      state: "submitted" as const,
      revision: existing.revision + 1,
    });
    this.store.save(submitted);
    return submitted;
  }
}

/** Freeze the four key segments for diagnostics (never authority). */
export function submitIntentIdentity(identity: {
  readonly project_id: string;
  readonly research_id: string;
}, round: number, planDefinitionDigest: string): {
  readonly project_id: string;
  readonly research_id: string;
  readonly round: number;
  readonly plan_definition_digest: string;
} {
  return Object.freeze({
    project_id: identity.project_id,
    research_id: identity.research_id,
    round: requireRound(round),
    plan_definition_digest: planDefinitionDigest,
  });
}
