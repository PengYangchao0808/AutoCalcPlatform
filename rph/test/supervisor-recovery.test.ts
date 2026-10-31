/**
 * M3-04 — recoverable Supervisor, evidence ownership and the research decision
 * (G07/G08/G09).
 *
 * The matrix proves, against the injected ports only (no model, no QC, no LSF,
 * no network, no child process):
 *
 * 1. a submit carries a stable idempotency key that was persisted **before**
 *    the call, and a retry after a crash replays the same task;
 * 2. the crash matrix — before submit, after submit before the receipt,
 *    after the TaskRef binding, during the wait, on an expired cursor, after
 *    the evidence read but before the decision, and after the decision but
 *    before the CAS — always resumes the *same* round / TaskRef / attempt
 *    without a second task and without losing the evidence;
 * 3. the wait is a bounded loop over the P2 semantics (60 s / 200 events) and
 *    an exhausted wait returns `waiting` + `resume_handle`, not a fake status;
 * 4. evidence is owned: a foreign event/report, a foreign project/research/
 *    round, an old attempt, a wrong release, an ambiguous set of terminal
 *    events and a digest mismatch all stop the run and nothing is persisted as
 *    accepted;
 * 5. claims/limitations/missing checks come from the single projection, the
 *    decision is derived independently, and a completed-but-inconclusive
 *    execution is never `accept`;
 * 6. the used budget is restored from the platform receipts, never from a fresh
 *    process's empty memory;
 * 7. the M2-04c interlock: after a fencing takeover the old attempt's
 *    checkpoint/decision writes are refused and the run stops instead of
 *    reporting success.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_MAX_WAIT_POLLS,
  FIXTURE_SUPERVISOR_SKILLS,
  ResearchPortError,
  SUPERVISOR_RECOVERY_SCHEMA,
  WAIT_MAX_EVENTS,
  WAIT_MAX_WAIT_MS,
  decideFromView,
  legacyTaskRef,
  runSupervisor,
  type CheckpointResult,
  type CheckpointStage,
  type DecisionPersistResult,
  type EvidenceRequest,
  type EvidenceResult,
  type PlanBudget,
  type ResearchPortRule,
  type ResumeHandle,
  type ResumeRequest,
  type ResumeResult,
  type SubmitRequest,
  type SubmitResult,
  type SupervisorCheckpoint,
  type SupervisorInput,
  type SupervisorPorts,
  type SupervisorResult,
  type WaitEvent,
  type WaitRequest,
  type WaitResult,
} from "@rph/dsh-bundle";

import {
  createSubmitIntentPort,
  parsePlatformTaskRef,
  type DecisionRecord,
  type EvidenceView,
  type ResearchTaskRef,
} from "@rph/bridge";

import { MemorySubmitIntentStore, SubmitIntentLedger } from "@rph/research";

import { TEST_OPTIONS } from "./harness.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const OTHER_DIGEST = `sha256:${"b".repeat(64)}`;
const REPORT_DIGEST = `sha256:${"c".repeat(64)}`;
const PROJECTION_DIGEST = `sha256:${"d".repeat(64)}`;
const RELEASE_DIGEST = `sha256:${"e".repeat(64)}`;
const PROJECT = "project-m3-04";
const OTHER_PROJECT = "project-m3-04-other";
const RESEARCH = "res-m3-04";
const OTHER_RESEARCH = "res-m3-04-other";
const APPROVAL = "approval-m3-04";
const RELEASE_ID = "release-m3-04";
const TASK_ID = "20261027_001_Optimize";

/** Marker thrown from a port to simulate a process death at that seam. */
class CrashSignal extends Error {
  constructor(seam: string) {
    super(`crash:${seam}`);
    this.name = "CrashSignal";
  }
}

function budget(overrides: Partial<PlanBudget> = {}): PlanBudget {
  return {
    model_requests: 8,
    input_tokens: 20_000,
    output_tokens: 10_000,
    task_refs: 4,
    recipe_steps: 4,
    qc_invocations: 4,
    wall_seconds: 7_200,
    queue_seconds: 600,
    ...overrides,
  };
}

function refFor(
  overrides: {
    readonly project?: string;
    readonly attempt?: number;
    readonly release?: string;
  } = {},
): ResearchTaskRef {
  const project = overrides.project ?? PROJECT;
  return parsePlatformTaskRef(
    {
      project_id: project,
      task_id: TASK_ID,
      job_id: TASK_ID,
      attempt: overrides.attempt ?? 1,
      release: {
        release_id: overrides.release ?? RELEASE_ID,
        release_digest: RELEASE_DIGEST,
      },
    },
    project,
  );
}

function viewFor(
  ref: ResearchTaskRef,
  overrides: Partial<EvidenceView> = {},
): EvidenceView {
  return {
    schema: "acp2.runtime.evidence-projection.v1",
    contract_version: "1.0.0",
    research: {
      principal_id: "principal-m3-04",
      project_id: ref.project_id,
      research_id: RESEARCH,
      session_id: "session-m3-04",
      round: 0,
    },
    task_ref: ref,
    report: {
      report_id: "report-m3-04",
      digest: REPORT_DIGEST,
      schema_version: 1,
      subject_id: ref.task_id,
    },
    execution_terminal_state: "completed",
    qualification: "qualified",
    evidence_status: "complete",
    claims: [
      {
        claim_id: "claim-m3-04",
        status: "supported",
        validator_id: "cccp.analysis.research.energy",
        validator_version: "1.0.0",
        source_ref_count: 1,
        blocking_missing_checks: [],
      },
    ],
    overall_claim_status: "supported",
    blocking_missing_checks: [],
    evidence_refs: [
      {
        evidence_id: "ev-m3-04",
        kind: "task_result",
        sha256: DIGEST,
        relative_path: "WORK/task_result.json",
      },
    ],
    limitations: [],
    provenance_digest: DIGEST,
    normal_reuse: true,
    projection_digest: PROJECTION_DIGEST,
    ...overrides,
  };
}

function terminalEvent(
  ref: ResearchTaskRef,
  overrides: Partial<WaitEvent> = {},
): WaitEvent {
  return {
    event_key: `${ref.task_id}:attempt=${ref.attempt}:terminal`,
    type: "job.completed",
    task_ref: legacyTaskRef(ref),
    report: null,
    report_id: "report-m3-04",
    report_digest: REPORT_DIGEST,
    ...overrides,
  };
}

/**
 * The durable platform double.
 *
 * Everything the Supervisor treats as a "platform fact" survives a simulated
 * process death because it lives here: the M1-02 intent ledger, the created
 * task (keyed by the wire idempotency key), the evidence projection, the
 * decision CAS and the attempt-fenced checkpoints.
 */
class FakePlatform {
  readonly ledger = new SubmitIntentLedger(new MemorySubmitIntentStore());
  readonly tasksByKey = new Map<string, ResearchTaskRef>();
  readonly submitKeys: string[] = [];
  readonly submitIntents: string[] = [];
  readonly waitRequests: WaitRequest[] = [];
  readonly evidenceRequests: EvidenceRequest[] = [];
  readonly checkpoints: SupervisorCheckpoint[] = [];
  readonly decisions: DecisionRecord[] = [];
  readonly resumeRequests: ResumeRequest[] = [];
  resumeTerminalState: string | null = null;
  usedBudget: PlanBudget = budget({
    model_requests: 0,
    input_tokens: 0,
    output_tokens: 0,
    task_refs: 0,
    recipe_steps: 0,
    qc_invocations: 0,
    wall_seconds: 0,
    queue_seconds: 0,
  });
  modelRequests = 0;
  terminalEvents: WaitEvent[] = [];
  view: EvidenceView | null = null;
  ref: ResearchTaskRef | null = null;
  crashAt: string | null = null;
  crashStage: CheckpointStage | null = null;
  fenceAfterStage: CheckpointStage | null = null;
  fenced = false;
  postFenceWrites = 0;
  expireCursorTimes = 0;
  rejectCursor = false;
  polls = 0;
  private terminalsPinned = false;

  constructor(readonly attempt = 1) {}

  pinTerminals(events: readonly WaitEvent[]): void {
    this.terminalsPinned = true;
    this.terminalEvents = [...events];
  }

  private ensureTask(idempotencyKey: string): ResearchTaskRef {
    const existing = this.tasksByKey.get(idempotencyKey);
    if (existing !== undefined) {
      return existing;
    }
    const ref = refFor({ attempt: this.attempt });
    this.tasksByKey.set(idempotencyKey, ref);
    this.ref = ref;
    if (!this.terminalsPinned) {
      this.terminalEvents = [terminalEvent(ref)];
    }
    if (this.view === null) {
      this.view = viewFor(ref);
    }
    return ref;
  }

  async submit(request: SubmitRequest): Promise<SubmitResult> {
    this.submitKeys.push(request.idempotency_key);
    this.submitIntents.push(request.submit_intent_key);
    const replayed = this.tasksByKey.has(request.idempotency_key);
    const ref = this.ensureTask(request.idempotency_key);
    if (this.crashAt === "submit_response_lost") {
      // The platform created the task but the caller lost the receipt.
      this.crashAt = null;
      throw new CrashSignal("submit_response_lost");
    }
    return {
      ok: true,
      task_ref: legacyTaskRef(ref),
      bound_ref: ref,
      reason: replayed ? "replayed" : "submitted",
    };
  }

  async wait(request: WaitRequest): Promise<WaitResult> {
    this.waitRequests.push(request);
    if (this.crashAt === "wait") {
      this.crashAt = null;
      throw new CrashSignal("wait");
    }
    if (this.expireCursorTimes > 0) {
      this.expireCursorTimes -= 1;
      throw new ResearchPortError("cursor_expired", "cursor is older than 900 s");
    }
    if (this.rejectCursor) {
      throw new ResearchPortError("cursor_invalid", "cursor is not for this project");
    }
    this.polls += 1;
    return {
      events: this.terminalEvents,
      cursor: `cursor-${this.polls}`,
      timed_out: this.terminalEvents.length === 0,
    };
  }

  async evidence(request: EvidenceRequest): Promise<EvidenceResult> {
    this.evidenceRequests.push(request);
    if (this.view === null) {
      throw new ResearchPortError("evidence_unavailable", "no projection yet");
    }
    return { view: this.view, read_at: "2026-10-27T00:00:00Z" };
  }

  async persist(record: DecisionRecord): Promise<DecisionPersistResult> {
    if (this.crashAt === "decision_before_cas") {
      this.crashAt = null;
      throw new CrashSignal("decision_before_cas");
    }
    if (this.fenced) {
      this.postFenceWrites += 1;
      return { ok: false, fenced: true, reason: "fenced" };
    }
    const existing = this.decisions.find(
      (item) => item.projection_digest === record.projection_digest,
    );
    if (existing !== undefined) {
      if (existing.decision !== record.decision) {
        return { ok: false, reason: "revision_conflict" };
      }
      return { ok: true, replayed: true, revision: this.decisions.length };
    }
    this.decisions.push(record);
    return { ok: true, replayed: false, revision: this.decisions.length };
  }

  async resumeSaga(request: ResumeRequest): Promise<ResumeResult> {
    this.resumeRequests.push(request);
    if (this.fenced) {
      throw new ResearchPortError("fenced", "the execution right moved to another attempt");
    }
    return {
      advanced: true,
      cursor: "cursor-resumed",
      terminal_state: this.resumeTerminalState,
    };
  }

  async checkpoint(checkpoint: SupervisorCheckpoint): Promise<CheckpointResult> {
    if (this.fenced) {
      this.postFenceWrites += 1;
      return { ok: false, fenced: true, reason: "fenced" };
    }
    this.checkpoints.push(checkpoint);
    if (this.crashAt === "checkpoint" && this.crashStage === checkpoint.stage) {
      this.crashAt = null;
      this.crashStage = null;
      throw new CrashSignal(`checkpoint:${checkpoint.stage}`);
    }
    if (this.fenceAfterStage === checkpoint.stage) {
      // The takeover happens right after this write is accepted.
      this.fenced = true;
    }
    return { ok: true, replayed: false };
  }

  lastHandle(): ResumeHandle {
    const checkpoint = this.checkpoints.at(-1);
    if (checkpoint === undefined) {
      throw new Error("no checkpoint was written");
    }
    return checkpoint.handle;
  }

  pendingTerminal(): WaitEvent {
    if (this.ref === null) {
      throw new Error("no task yet");
    }
    return terminalEvent(this.ref);
  }
}

function portsFor(platform: FakePlatform): SupervisorPorts {
  return {
    preflight: async () => ({
      ok: true,
      reason: "ok",
      checks: ["method_defined", "inputs_complete"],
    }),
    admission: async () => ({ ok: true, reason: "ok", approval_ref: APPROVAL }),
    approve: async () => {
      if (platform.crashAt === "pre_submit") {
        platform.crashAt = null;
        throw new CrashSignal("pre_submit");
      }
      return { approved: true, approval_ref: APPROVAL, reason: "ok" };
    },
    submit: (request) => platform.submit(request),
    wait: (request) => platform.wait(request),
    submitIntent: createSubmitIntentPort(platform.ledger),
    evidence: (request) => platform.evidence(request),
    persistDecision: (record) => platform.persist(record),
    persistCheckpoint: (checkpoint) => platform.checkpoint(checkpoint),
    budgetUsage: async () => ({
      source: "saga-record",
      used: platform.usedBudget,
      receipts: ["saga:budget:1"],
    }),
    modelGateway: async () => ({
      model_requests: platform.modelRequests,
      receipts: [],
      source: "platform-receipt",
    }),
    persistTaskRef: async () => undefined,
    persistReport: async () => undefined,
  };
}

function input(overrides: Partial<SupervisorInput> = {}): SupervisorInput {
  return {
    project_id: PROJECT,
    research_id: RESEARCH,
    question: "which path is lower?",
    question_ref: null,
    constraints: [],
    available_inputs: ["in-1"],
    recipe_digest: OTHER_DIGEST,
    initial_input: {
      input_id: "in-1",
      sha256: DIGEST,
      format: "xyz",
      role: "minimum",
    },
    science_seed: {
      method: "r2SCAN-3c",
      mapping: "identity",
      charge: 0,
      multiplicity: 1,
      electronic_state: null,
      structure_provenance: [
        { input_id: "in-1", sha256: DIGEST, format: "xyz", role: "minimum" },
      ],
      constraints: [],
    },
    limits: { max_rounds: 1, budget: budget() },
    plan_definition_digest: () => DIGEST,
    require_evidence_binding: true,
    ...overrides,
  };
}

function run(
  platform: FakePlatform,
  overrides: Partial<SupervisorInput> = {},
): Promise<SupervisorResult> {
  return runSupervisor(input(overrides), portsFor(platform), FIXTURE_SUPERVISOR_SKILLS);
}

async function expectCrash(
  platform: FakePlatform,
  overrides: Partial<SupervisorInput> = {},
): Promise<void> {
  await assert.rejects(
    run(platform, overrides),
    (error: unknown) => error instanceof CrashSignal,
  );
}

function expectPortError(error: unknown, rule: ResearchPortRule): void {
  assert.ok(error instanceof ResearchPortError, String(error));
  assert.equal(error.rule, rule);
}

// ---------------------------------------------------------------------------
// 1. submit intent: persisted before the call, stable across a retry
// ---------------------------------------------------------------------------

test("the submit key is the persisted M1-02 intent, not a fresh derivation", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  const result = await run(platform);

  assert.equal(result.status, "completed");
  assert.equal(result.stop_reason, "accepted");
  assert.equal(platform.submitKeys.length, 1);
  const expectedKey = `${PROJECT}:record:${RESEARCH}:round-0:submit:plan-${"a".repeat(64)}`;
  assert.equal(platform.submitIntents[0], expectedKey);
  assert.equal(platform.submitKeys[0], expectedKey);
  assert.equal(result.decision?.decision, "accept");
  assert.equal(platform.decisions.length, 1);
  assert.equal(result.resume_handle, null);
});

test("a changed plan digest on the same round is a typed conflict, not a second key", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  const first = await run(platform);
  assert.equal(first.stop_reason, "accepted");
  const second = await runSupervisor(
    input({ plan_definition_digest: () => OTHER_DIGEST }),
    portsFor(platform),
    FIXTURE_SUPERVISOR_SKILLS,
  );
  assert.equal(second.status, "stopped");
  assert.equal(second.stop_reason, "plan_changed");
  assert.equal(platform.submitKeys.length, 1);
  assert.equal(platform.tasksByKey.size, 1);
});

// ---------------------------------------------------------------------------
// 2. the crash matrix
// ---------------------------------------------------------------------------

test("crash before submit: no intent, no task, and the retry creates exactly one", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.crashAt = "pre_submit";
  await expectCrash(platform);

  assert.equal(
    platform.ledger.load({ project_id: PROJECT, research_id: RESEARCH, round: 0 }),
    null,
  );
  assert.equal(platform.tasksByKey.size, 0);

  const resumed = await run(platform);
  assert.equal(resumed.stop_reason, "accepted");
  assert.equal(platform.tasksByKey.size, 1);
  assert.equal(resumed.task_refs.length, 1);
  assert.equal(resumed.task_refs[0]?.attempt, 1);
});

test("crash after submit before the receipt: the same key replays the same task", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.crashAt = "submit_response_lost";
  await expectCrash(platform);

  // The platform created the task; only the caller died.
  assert.equal(platform.tasksByKey.size, 1);
  assert.equal(platform.decisions.length, 0);

  const resumed = await run(platform);
  assert.equal(resumed.stop_reason, "accepted");
  assert.equal(platform.tasksByKey.size, 1);
  assert.equal(platform.submitKeys.length, 2);
  assert.equal(platform.submitKeys[0], platform.submitKeys[1]);
  assert.equal(resumed.submits, 1);
  assert.equal(resumed.task_refs[0]?.attempt, 1);
  assert.equal(platform.decisions.length, 1);
});

test("crash after the TaskRef binding: resume re-observes the same attempt", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.crashAt = "wait";
  await expectCrash(platform);

  const handle = platform.lastHandle();
  assert.equal(handle.state, "waiting");
  assert.equal(handle.task_ref.attempt, 1);

  const resumed = await run(platform, { resume_handle: handle });
  assert.equal(resumed.stop_reason, "accepted");
  assert.equal(platform.submitKeys.length, 1); // no second submit
  assert.equal(platform.tasksByKey.size, 1);
  assert.equal(resumed.task_refs[0]?.attempt, 1);
  assert.equal(resumed.model_invocations, 0); // no re-plan, no model call
  assert.equal(resumed.phases[0]?.detail.includes("resume from handle"), true);
  assert.equal(resumed.decision?.decision, "accept");
});

test("no terminal inside the bound: waiting + resume handle, then the same task resumes", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: {
      max_rounds: 1,
      budget: budget(),
      max_wait_polls: 2,
      wait_poll_ms: 30_000,
    },
  });
  assert.equal(first.status, "waiting");
  assert.equal(first.stop_reason, "wait_pending");
  const handle = first.resume_handle;
  assert.ok(handle !== null);
  assert.equal(handle.state, "waiting");
  assert.equal(handle.schema, SUPERVISOR_RECOVERY_SCHEMA);
  assert.equal(handle.task_ref.attempt, 1);
  assert.equal(handle.approval_ref, APPROVAL);
  assert.equal(
    handle.submit_intent_key.startsWith(
      `${PROJECT}:record:${RESEARCH}:round-0:submit:plan-`,
    ),
    true,
  );
  assert.equal(first.submits, 1);
  assert.equal(platform.tasksByKey.size, 1);

  // The platform reaches the terminal state; a new process resumes the handle.
  platform.pinTerminals([platform.pendingTerminal()]);
  const resumed = await run(platform, { resume_handle: handle });
  assert.equal(resumed.stop_reason, "accepted");
  assert.equal(platform.submitKeys.length, 1);
  assert.equal(platform.tasksByKey.size, 1);
  assert.equal(resumed.resume_handle, null);
  assert.equal(resumed.decision?.decision, "accept");
});

test("an expired cursor is an explicit snapshot reset, not a gap", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.expireCursorTimes = 1;
  const result = await run(platform);

  assert.equal(result.stop_reason, "accepted");
  assert.equal(result.cursor_resets, 1);
  assert.equal(platform.waitRequests.length, 2);
  // The reset poll sends no cursor (the platform answers with its snapshot).
  assert.equal(platform.waitRequests[1]?.cursor, null);
  assert.equal(result.decisions.length, 1);
  assert.equal(platform.submitKeys.length, 1);
});

test("more cursor expiries than the bound stop the run", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.expireCursorTimes = 5;
  const result = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_cursor_resets: 0 },
  });
  assert.equal(result.stop_reason, "cursor_expired");
  assert.equal(result.decisions.length, 0);
});

test("a foreign or invalid cursor is refused, never adopted", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.rejectCursor = true;
  const result = await run(platform);
  assert.equal(result.stop_reason, "cursor_rejected");
  assert.equal(result.decisions.length, 0);
});

test("crash after the evidence read but before the decision re-reads the same projection", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.crashAt = "checkpoint";
  platform.crashStage = "evidence_bound";
  await expectCrash(platform);

  const handle = platform.lastHandle();
  assert.equal(handle.state, "decision_pending");
  assert.equal(handle.pending_decision, null);
  assert.equal(handle.pending_projection_digest, PROJECTION_DIGEST);

  const resumed = await run(platform, { resume_handle: handle });
  assert.equal(resumed.stop_reason, "accepted");
  assert.equal(resumed.decisions.length, 1);
  assert.equal(resumed.decisions[0]?.decision, "accept");
  assert.equal(platform.decisions.length, 1);
  assert.equal(platform.submitKeys.length, 1);
  assert.equal(resumed.model_invocations, 0);
  assert.equal(resumed.bindings.length, 1);
  assert.equal(resumed.bindings[0]?.projection_digest ?? undefined, PROJECTION_DIGEST);
  assert.equal(resumed.bindings[0]?.verified ?? undefined, true);
});

test("crash after the decision but before the CAS persists exactly one identical decision", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.crashAt = "decision_before_cas";
  await expectCrash(platform);

  const handle = platform.lastHandle();
  assert.equal(handle.state, "decision_pending");
  assert.equal(handle.pending_decision, "accept");

  const resumed = await run(platform, { resume_handle: handle });
  assert.equal(resumed.stop_reason, "accepted");
  assert.equal(platform.decisions.length, 1);
  assert.equal(platform.decisions[0]?.decision, "accept");
  assert.equal(platform.decisions[0]?.projection_digest, PROJECTION_DIGEST);
  assert.equal(platform.submitKeys.length, 1);
  assert.equal(resumed.model_invocations, 0);

  // A second replay of the same handle is the platform's idempotent replay.
  const replayed = await run(platform, { resume_handle: handle });
  assert.equal(replayed.stop_reason, "accepted");
  assert.equal(platform.decisions.length, 1);
});

// ---------------------------------------------------------------------------
// 3. bounded wait semantics
// ---------------------------------------------------------------------------

test("the wait is bounded per poll and the total may exceed one minute", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const result = await run(platform, {
    limits: {
      max_rounds: 1,
      budget: budget(),
      max_wait_polls: 3,
      wait_poll_ms: 45_000,
      max_events_per_poll: 500,
    },
  });
  assert.equal(result.status, "waiting");
  assert.equal(platform.waitRequests.length, 3);
  for (const request of platform.waitRequests) {
    assert.ok(request.max_wait_ms <= WAIT_MAX_WAIT_MS);
    assert.ok(request.max_events <= WAIT_MAX_EVENTS);
  }
  const total = platform.waitRequests.reduce(
    (sum, request) => sum + request.max_wait_ms,
    0,
  );
  assert.ok(total > 60_000, `expected more than one minute, got ${total}`);
  assert.equal(platform.waitRequests[0]?.max_events, WAIT_MAX_EVENTS);
  assert.equal(result.wait_polls, 3);
});

test("an over-limit poll is clamped to the platform bounds", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const result = await run(platform, {
    limits: {
      max_rounds: 1,
      budget: budget(),
      max_wait_polls: 1,
      wait_poll_ms: 120_000,
    },
  });
  assert.equal(result.status, "waiting");
  assert.equal(platform.waitRequests[0]?.max_wait_ms, WAIT_MAX_WAIT_MS);
  assert.ok(DEFAULT_MAX_WAIT_POLLS > 1);
});

// ---------------------------------------------------------------------------
// 4. evidence ownership (G07/G08)
// ---------------------------------------------------------------------------

async function expectBindingStop(
  reason: string,
  prepare: (platform: FakePlatform) => void,
): Promise<FakePlatform> {
  const platform = new FakePlatform();
  prepare(platform);
  const result = await run(platform);
  assert.equal(result.status, "stopped", String(result.stop_reason));
  assert.equal(result.stop_reason, reason);
  assert.equal(result.decisions.length, 0);
  assert.equal(platform.decisions.length, 0);
  return platform;
}

test("a foreign project's event is refused", TEST_OPTIONS, async () => {
  const platform = await expectBindingStop("foreign_evidence", (fake) => {
    fake.pinTerminals([
      terminalEvent(refFor(), {
        task_ref: {
          project_id: OTHER_PROJECT,
          job_id: TASK_ID,
          attempt: 1,
          release_id: RELEASE_ID,
        },
      }),
    ]);
  });
  assert.equal(platform.tasksByKey.size, 1);
});

test("a foreign project's projection is refused", TEST_OPTIONS, async () => {
  await expectBindingStop("foreign_evidence", (fake) => {
    const foreign = refFor({ project: OTHER_PROJECT });
    fake.view = viewFor(foreign, {
      research: {
        principal_id: "principal-other",
        project_id: OTHER_PROJECT,
        research_id: RESEARCH,
        session_id: "session-other",
        round: 0,
      },
    });
  });
});

test("a projection of another research is refused", TEST_OPTIONS, async () => {
  await expectBindingStop("foreign_evidence", (fake) => {
    const ref = refFor();
    fake.view = viewFor(ref, {
      research: {
        principal_id: "principal-m3-04",
        project_id: PROJECT,
        research_id: OTHER_RESEARCH,
        session_id: "session-m3-04",
        round: 0,
      },
    });
  });
});

test("an old attempt is refused, even with a complete reference", TEST_OPTIONS, async () => {
  await expectBindingStop("evidence_identity_mismatch", (fake) => {
    fake.view = viewFor(refFor({ attempt: 2 }));
  });
});

test("a wrong release is refused", TEST_OPTIONS, async () => {
  await expectBindingStop("evidence_identity_mismatch", (fake) => {
    fake.view = viewFor(refFor({ release: "release-other" }));
  });
});

test("a digest mismatch between event and projection is refused", TEST_OPTIONS, async () => {
  await expectBindingStop("evidence_digest_mismatch", (fake) => {
    const ref = refFor();
    fake.view = viewFor(ref, {
      report: {
        report_id: "report-m3-04",
        digest: OTHER_DIGEST,
        schema_version: 1,
        subject_id: TASK_ID,
      },
    });
  });
});

test("two ambiguous terminal events never reach a decision", TEST_OPTIONS, async () => {
  await expectBindingStop("ambiguous_terminal", (fake) => {
    const ref = refFor();
    fake.pinTerminals([
      terminalEvent(ref, { event_key: "terminal-1" }),
      terminalEvent(ref, { event_key: "terminal-2", report_digest: OTHER_DIGEST }),
    ]);
  });
});

test("a terminal event without a digest-bound report is refused", TEST_OPTIONS, async () => {
  await expectBindingStop("evidence_binding_required", (fake) => {
    fake.pinTerminals([
      terminalEvent(refFor(), { report_id: null, report_digest: null }),
    ]);
  });
});

test("a non-terminal projection is not a decision input", TEST_OPTIONS, async () => {
  await expectBindingStop("execution_not_terminal", (fake) => {
    fake.view = viewFor(refFor(), { execution_terminal_state: "not_started" });
  });
});

test("binding mode fails closed without the evidence/decision/intent ports", TEST_OPTIONS, async () => {
  const base = portsFor(new FakePlatform());
  const withoutEvidence: SupervisorPorts = { ...base };
  delete withoutEvidence.evidence;
  const noEvidence = await runSupervisor(
    input(),
    withoutEvidence,
    FIXTURE_SUPERVISOR_SKILLS,
  );
  assert.equal(noEvidence.stop_reason, "evidence_binding_required");

  const withoutDecision: SupervisorPorts = { ...base };
  delete withoutDecision.persistDecision;
  const noDecision = await runSupervisor(
    input(),
    withoutDecision,
    FIXTURE_SUPERVISOR_SKILLS,
  );
  assert.equal(noDecision.stop_reason, "persist_failed");

  const withoutIntent: SupervisorPorts = { ...base };
  delete withoutIntent.submitIntent;
  const noIntent = await runSupervisor(
    input(),
    withoutIntent,
    FIXTURE_SUPERVISOR_SKILLS,
  );
  assert.equal(noIntent.stop_reason, "submit_intent_required");
});

// ---------------------------------------------------------------------------
// 5. the independent research decision
// ---------------------------------------------------------------------------

test("a completed-but-inconclusive execution is never accepted", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.view = viewFor(refFor(), {
    overall_claim_status: "inconclusive",
    claims: [
      {
        claim_id: "claim-m3-04",
        status: "inconclusive",
        validator_id: "cccp.analysis.research.energy",
        validator_version: "1.0.0",
        source_ref_count: 1,
        blocking_missing_checks: [],
      },
    ],
  });

  const result = await run(platform);
  assert.notEqual(result.stop_reason, "accepted");
  assert.equal(result.status, "stopped");
  assert.equal(platform.decisions.length, 1);
  assert.equal(platform.decisions[0]?.decision, "revise");
  assert.equal(platform.decisions[0]?.evidence.overall_claim_status, "inconclusive");
  assert.equal(result.stop_reason, "round_cap");
});

test("a blocking missing check escalates instead of accepting", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.view = viewFor(refFor(), {
    blocking_missing_checks: ["frequency_performed"],
  });
  const result = await run(platform);
  assert.equal(result.stop_reason, "decision_escalate");
  assert.equal(platform.decisions[0]?.decision, "escalate");
  assert.equal(result.status, "stopped");
});

test("a failed execution is rejected, not accepted", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.view = viewFor(refFor(), {
    execution_terminal_state: "failed",
    evidence_status: "failed",
  });
  const result = await run(platform);
  assert.equal(result.stop_reason, "decision_reject");
  assert.equal(platform.decisions[0]?.decision, "reject");
});

test("the decision policy reads only the projection", TEST_OPTIONS, async () => {
  const ref = refFor();
  assert.equal(decideFromView(viewFor(ref)), "accept");
  assert.equal(
    decideFromView(viewFor(ref, { overall_claim_status: "inconclusive" })),
    "revise",
  );
  assert.equal(
    decideFromView(viewFor(ref, { evidence_status: "incomplete" })),
    "escalate",
  );
  assert.equal(
    decideFromView(
      viewFor(ref, { evidence_status: "partial", overall_claim_status: "refuted" }),
    ),
    "revise",
  );
});

// ---------------------------------------------------------------------------
// 6. duplicate events
// ---------------------------------------------------------------------------

test("a replayed terminal event never authorises a second decision or task", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  const event = terminalEvent(refFor());
  platform.pinTerminals([event, event]);
  const result = await run(platform);
  assert.equal(result.stop_reason, "accepted");
  assert.equal(result.duplicate_terminal_events, 1);
  assert.equal(result.bindings.length, 1);
  assert.equal(platform.decisions.length, 1);
  assert.equal(platform.tasksByKey.size, 1);
});

// ---------------------------------------------------------------------------
// 7. used budget from the platform record
// ---------------------------------------------------------------------------

test("a resumed run restores the used budget from the platform receipts", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  assert.equal(first.status, "waiting");
  const handle = first.resume_handle;
  assert.ok(handle !== null);
  const restored = handle.consumed_budget;
  assert.ok(restored.input_tokens > 0);
  assert.ok(restored.task_refs > 0);

  // The platform's own record shows additional spend (another producer).
  platform.usedBudget = budget({
    input_tokens: restored.input_tokens + 30_000,
    task_refs: 1,
  });
  platform.modelRequests = 3;

  const resumed = await run(platform, {
    resume_handle: handle,
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  assert.equal(resumed.status, "waiting");
  assert.equal(resumed.model_requests_used, 3);
  const nextHandle = resumed.resume_handle;
  assert.ok(nextHandle !== null);
  // max(platform, restored) — never a fresh-memory estimate.
  assert.equal(
    nextHandle.consumed_budget.input_tokens,
    restored.input_tokens + 30_000,
  );
  assert.equal(nextHandle.consumed_budget.task_refs, restored.task_refs);
  assert.equal(nextHandle.consumed_budget.model_requests, 3);
  assert.equal(nextHandle.resume_count, 1);
  assert.equal(resumed.model_invocations, 0);
});

test("a malformed budget receipt fails closed", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.usedBudget = budget({ input_tokens: Number.NaN });
  const result = await run(platform);
  assert.equal(result.stop_reason, "budget_receipt_mismatch");
  assert.equal(platform.decisions.length, 0);
});

// ---------------------------------------------------------------------------
// 8. resume handle ownership
// ---------------------------------------------------------------------------

test("a handle from another project cannot execute", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  const handle = first.resume_handle;
  assert.ok(handle !== null);

  const foreign = await run(platform, {
    project_id: OTHER_PROJECT,
    resume_handle: handle,
  });
  assert.equal(foreign.stop_reason, "resume_handle_invalid");
});

test("a handle beyond the round cap cannot execute", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  const handle = first.resume_handle;
  assert.ok(handle !== null);

  const otherRound = await run(platform, {
    resume_handle: { ...handle, round: 4 },
    limits: { max_rounds: 4, budget: budget() },
  });
  assert.equal(otherRound.stop_reason, "round_cap");
});

test("a handle for another plan is refused before any call", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  const handle = first.resume_handle;
  assert.ok(handle !== null);
  const waitsBefore = platform.waitRequests.length;

  const changed = await run(platform, {
    resume_handle: handle,
    plan_definition_digest: () => OTHER_DIGEST,
  });
  assert.equal(changed.stop_reason, "plan_changed");
  assert.equal(platform.waitRequests.length, waitsBefore);
  assert.equal(platform.decisions.length, 0);
});

// ---------------------------------------------------------------------------
// 9. M2-04c interlock: fencing takeover
// ---------------------------------------------------------------------------

test("after a takeover the old attempt cannot write a checkpoint or a decision", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.crashAt = "checkpoint";
  platform.crashStage = "evidence_bound";
  await expectCrash(platform);
  const handle = platform.lastHandle();
  assert.equal(handle.state, "decision_pending");

  // The new attempt claimed (job, attempt): every old-attempt write is refused.
  platform.fenced = true;
  const resumed = await run(platform, { resume_handle: handle });

  assert.equal(resumed.status, "stopped");
  assert.equal(resumed.stop_reason, "fenced");
  assert.equal(resumed.decision, null);
  assert.equal(platform.decisions.length, 0);
  assert.ok(platform.postFenceWrites >= 1);
  assert.equal(platform.tasksByKey.size, 1);
});

test("a fenced decision CAS is a stop, never an accepted run", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.crashAt = "checkpoint";
  platform.crashStage = "evidence_bound";
  await expectCrash(platform);
  const handle = platform.lastHandle();

  // The takeover happens after the last accepted checkpoint, so the CAS is the
  // first refused write of the old attempt.
  platform.fenceAfterStage = "decision_pending";
  const resumed = await run(platform, { resume_handle: handle });
  assert.equal(resumed.stop_reason, "fenced");
  assert.equal(platform.decisions.length, 0);
  assert.equal(platform.postFenceWrites, 1);
  assert.equal(resumed.decision, null);
});

// ---------------------------------------------------------------------------
// 10. P2 resume and the projection's limitations
// ---------------------------------------------------------------------------

test("a resume advances the same saga through the P2 resume port", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  const handle = first.resume_handle;
  assert.ok(handle !== null);

  platform.pinTerminals([platform.pendingTerminal()]);
  const resumed = await runSupervisor(
    input({ resume_handle: handle }),
    { ...portsFor(platform), resume: (request) => platform.resumeSaga(request) },
    FIXTURE_SUPERVISOR_SKILLS,
  );
  assert.equal(resumed.stop_reason, "accepted");
  assert.equal(platform.resumeRequests.length, 1);
  const request = platform.resumeRequests[0];
  assert.ok(request !== undefined);
  assert.equal(request.plan_definition_digest, DIGEST);
  assert.equal(request.task_ref.task_id, handle.task_ref.task_id);
  assert.equal(request.task_ref.attempt, handle.task_ref.attempt);
  assert.equal(request.diagnostic_only, false);
  // The wait adopted the platform's authoritative cursor.
  assert.equal(platform.waitRequests.at(-1)?.cursor, "cursor-resumed");
  assert.equal(platform.submitKeys.length, 1);
});

test("a resume that reports a terminal keeps the pre-observation cursor", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  const handle = first.resume_handle;
  assert.ok(handle !== null);
  const anchorCursor = handle.cursor;
  assert.equal(anchorCursor, "cursor-1");

  // The saga already reached the terminal state; the platform returns a cursor
  // for the *current* revision, which would hide the terminal from the wait.
  platform.resumeTerminalState = "completed";
  platform.pinTerminals([platform.pendingTerminal()]);
  const resumed = await runSupervisor(
    input({ resume_handle: handle }),
    { ...portsFor(platform), resume: (request) => platform.resumeSaga(request) },
    FIXTURE_SUPERVISOR_SKILLS,
  );
  assert.equal(resumed.stop_reason, "accepted");
  // The wait re-observed the terminal from the handle's anchor.
  assert.equal(platform.waitRequests.at(-1)?.cursor, anchorCursor);
  assert.equal(platform.decisions.length, 1);
});

test("a fenced resume is a stop before any wait", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.pinTerminals([]);
  const first = await run(platform, {
    limits: { max_rounds: 1, budget: budget(), max_wait_polls: 1 },
  });
  const handle = first.resume_handle;
  assert.ok(handle !== null);
  const waitsBefore = platform.waitRequests.length;

  platform.fenced = true;
  const resumed = await runSupervisor(
    input({ resume_handle: handle }),
    { ...portsFor(platform), resume: (request) => platform.resumeSaga(request) },
    FIXTURE_SUPERVISOR_SKILLS,
  );
  assert.equal(resumed.stop_reason, "fenced");
  assert.equal(platform.waitRequests.length, waitsBefore);
  assert.equal(platform.decisions.length, 0);
});

test("the binding exposes the projection's limitations and missing checks", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  platform.view = viewFor(refFor(), {
    limitations: ["no frequency calculation was performed"],
  });
  const result = await run(platform);
  assert.equal(result.stop_reason, "accepted");
  const binding = result.bindings[0];
  assert.ok(binding !== undefined);
  assert.deepEqual(binding.limitations, ["no frequency calculation was performed"]);
  assert.deepEqual(binding.blocking_missing_checks, []);
  assert.equal(binding.verified, true);
  assert.equal(binding.report_digest, REPORT_DIGEST);
  assert.equal(binding.projection_digest, PROJECTION_DIGEST);
});

// ---------------------------------------------------------------------------
// 11. the transport-level typed refusals are not swallowed
// ---------------------------------------------------------------------------

test("an unexpected port failure is a crash, not a verdict", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  const ports: SupervisorPorts = {
    ...portsFor(platform),
    wait: async () => {
      throw new TypeError("socket closed");
    },
  };
  await assert.rejects(
    runSupervisor(input(), ports, FIXTURE_SUPERVISOR_SKILLS),
    (error: unknown) =>
      error instanceof TypeError && error.message === "socket closed",
  );
});

test("a typed evidence refusal is a stop with its own reason", TEST_OPTIONS, async () => {
  const platform = new FakePlatform();
  const ports: SupervisorPorts = {
    ...portsFor(platform),
    evidence: async () => {
      throw new ResearchPortError("evidence_unavailable", "no projection yet");
    },
  };
  const result = await runSupervisor(input(), ports, FIXTURE_SUPERVISOR_SKILLS);
  assert.equal(result.stop_reason, "evidence_read_failed");
  assert.equal(result.decisions.length, 0);
  expectPortError(
    new ResearchPortError("evidence_unavailable", "x"),
    "evidence_unavailable",
  );
});
