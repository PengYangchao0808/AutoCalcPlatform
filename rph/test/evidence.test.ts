/**
 * M1-03 — the experience client's single evidence model.
 *
 * The criterion is the frozen contract sample set, not a second opinion: every
 * vector under `rph/fixtures/evidence-projection/` (a verbatim copy of
 * `docs/reference/next-change/acp2-contracts/runtime/samples/evidence-projection/`)
 * is replayed here and must reach the same accept/refuse decision as the
 * platform projection in `tests/test_acp2_evidence_projection.py`.
 *
 * Covered:
 *
 * 1. TaskRef normalisation: attempt and release are required (no default 1/"");
 * 2. a RecordTaskRef is not a TaskRef without an explicit release digest;
 * 3. EvidenceView parsing: frozen statuses, digest addresses, closed field set;
 * 4. bridge attribution: foreign project / research / round are refused;
 * 5. the decision policy: accept needs completed + complete + no blocking check
 *    + supported claims, and every decision needs digest-bound evidence;
 * 6. the decision envelope is closed: a model narrative has no field to occupy;
 * 7. `memory.ts` reuses the same evidence types (no second report model).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import {
  BridgeContractError,
  attributeEvidenceView,
  attributeTaskRef,
} from "@rph/bridge";
import {
  ACCEPT_PRECONDITION_CODES,
  DECISION_RATIONALE_CODES,
  EVIDENCE_STATUSES,
  RESEARCH_DECISIONS,
  assertDecisionRecord,
  assertEvidenceView,
  buildDecisionRecord,
  evaluateDecision,
  parseTaskRef,
  taskRefFromRecordRef,
  taskRefKey,
  validateDecisionRecord,
  validateEvidenceReport,
  validateEvidenceView,
  validateTaskRef,
  type DecisionRecord,
  type EvidenceView,
  type ResearchDecision,
} from "@rph/research";

// ---------------------------------------------------------------------------
// Fixtures (verbatim copies of the registered contract samples)
// ---------------------------------------------------------------------------

const FIXTURES = new URL("../../fixtures/evidence-projection/", import.meta.url);

function readFixture(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(fileURLToPath(new URL(name, FIXTURES)), "utf8")) as Record<
    string,
    unknown
  >;
}

function expectedValid(document: Record<string, unknown>): boolean {
  const expected = document["expected"];
  assert.ok(typeof expected === "object" && expected !== null, "fixture has an expected block");
  return (expected as Record<string, unknown>)["valid"] === true;
}

function expectedCode(document: Record<string, unknown>): string {
  const expected = document["expected"] as Record<string, unknown>;
  return String(expected["code"] ?? "");
}

function payloadOf(document: Record<string, unknown>): Record<string, unknown> {
  const payload = document["payload"];
  assert.ok(typeof payload === "object" && payload !== null, "fixture has a payload");
  return payload as Record<string, unknown>;
}

const ATTRIBUTION = {
  project_id: "proj-m1-03",
  research_id: "res-m1-03",
  round: 0,
};

// ---------------------------------------------------------------------------
// 1. TaskRef normalisation
// ---------------------------------------------------------------------------

test("A: TaskRef vectors require an explicit attempt and release", () => {
  for (const name of [
    "task-ref.positive.json",
    "task-ref-flat-release.positive.json",
  ]) {
    const document = readFixture(name);
    assert.equal(expectedValid(document), true, name);
    const ref = parseTaskRef(payloadOf(document));
    assert.equal(ref.project_id, "proj-m1-03");
    assert.equal(ref.task_id, "job-m1-03-static");
    assert.equal(ref.job_id, ref.task_id, "the legacy alias is normalised");
    assert.equal(ref.attempt, 1);
    assert.equal(ref.release.release_id, "release-m1-03");
    assert.match(ref.release.release_digest, /^sha256:[0-9a-f]{64}$/);
  }

  const missingAttempt = readFixture("task-ref-missing-attempt.negative.json");
  assert.equal(expectedCode(missingAttempt), "task_ref_missing_attempt");
  const attemptIssues = validateTaskRef(payloadOf(missingAttempt));
  assert.ok(
    attemptIssues.some((item) => item.path === "$.attempt" && item.code === "required"),
    "a missing attempt is a required-field violation",
  );
  assert.throws(() => parseTaskRef(payloadOf(missingAttempt)), /attempt is required/);

  const missingRelease = readFixture("task-ref-missing-release.negative.json");
  assert.equal(expectedCode(missingRelease), "task_ref_missing_release");
  assert.throws(
    () => parseTaskRef(payloadOf(missingRelease)),
    /release\.release_id is required/,
  );
});

test("A: a missing attempt is never coerced to 1 and a missing release is never empty", () => {
  for (const attempt of [0, -1, 1.5, true, null]) {
    assert.throws(
      () =>
        parseTaskRef({
          project_id: "proj-m1-03",
          task_id: "job-m1-03-static",
          attempt,
          release: {
            release_id: "release-m1-03",
            release_digest: `sha256:${"c".repeat(64)}`,
          },
        }),
      /invalid TaskRef/,
      `attempt ${String(attempt)} must be refused`,
    );
  }
  for (const release of [
    undefined,
    {},
    { release_id: "release-m1-03" },
    { release_id: "", release_digest: `sha256:${"c".repeat(64)}` },
    { release_id: "release-m1-03", release_digest: "" },
  ]) {
    assert.throws(
      () =>
        parseTaskRef({
          project_id: "proj-m1-03",
          task_id: "job-m1-03-static",
          attempt: 1,
          release,
        }),
      /invalid TaskRef/,
      `release ${JSON.stringify(release)} must be refused`,
    );
  }
});

test("A: a RecordTaskRef needs an explicit release digest to become a TaskRef", () => {
  const recordRef = {
    project_id: "proj-m1-03",
    job_id: "job-m1-03-static",
    attempt: 1,
    release_id: "release-m1-03",
  };
  const ref = taskRefFromRecordRef(recordRef, `sha256:${"c".repeat(64)}`);
  assert.equal(taskRefKey(ref), taskRefKey(parseTaskRef(payloadOf(readFixture("task-ref.positive.json")))));
  for (const digest of ["", "sha256:", "not-a-digest"]) {
    assert.throws(() => taskRefFromRecordRef(recordRef, digest), /release digest/);
  }
});

// ---------------------------------------------------------------------------
// 2. EvidenceView
// ---------------------------------------------------------------------------

function viewOf(document: Record<string, unknown>): EvidenceView {
  return assertEvidenceView(payloadOf(document));
}

test("B: the positive projection parses and preserves every frozen status", () => {
  const view = viewOf(readFixture("evidence-view.positive.json"));
  assert.equal(view.schema, "acp2.runtime.evidence-projection.v1");
  assert.equal(view.evidence_status, "complete");
  assert.ok((EVIDENCE_STATUSES as readonly string[]).includes(view.evidence_status));
  assert.equal(view.overall_claim_status, "supported");
  assert.equal(view.execution_terminal_state, "completed");
  assert.equal(view.qualification, "complete");
  assert.ok(view.claims.length > 0);
  for (const claim of view.claims) {
    assert.ok(["supported", "refuted", "inconclusive", "not_evaluated"].includes(claim.status));
  }
  assert.match(view.report.digest, /^sha256:[0-9a-f]{64}$/);
  assert.match(view.projection_digest, /^sha256:[0-9a-f]{64}$/);
  assert.ok(view.evidence_refs.length > 0, "a complete view cites digest-bound evidence");
  for (const ref of view.evidence_refs) {
    assert.match(ref.sha256, /^sha256:[0-9a-f]{64}$/);
  }
  assert.equal(validateEvidenceView(view).length, 0);
});

test("B: a view that drops its digest addresses or statuses is invalid", () => {
  const base = payloadOf(readFixture("evidence-view.positive.json"));
  for (const [mutate, code] of [
    [(value: Record<string, unknown>) => delete value["report"], "$.report"],
    [
      (value: Record<string, unknown>) => {
        (value["report"] as Record<string, unknown>)["digest"] = "sha256:short";
      },
      "$.report.digest",
    ],
    [
      (value: Record<string, unknown>) => {
        value["evidence_status"] = "accepted";
      },
      "$.evidence_status",
    ],
    [
      (value: Record<string, unknown>) => {
        value["overall_claim_status"] = "confirmed";
      },
      "$.overall_claim_status",
    ],
    [
      (value: Record<string, unknown>) => {
        (value["evidence_refs"] as Record<string, unknown>[])[0]!["sha256"] = "md5:0";
      },
      "$.evidence_refs[0].sha256",
    ],
    [
      (value: Record<string, unknown>) => {
        value["prompt"] = "the model explains why this is supported";
      },
      "$.prompt",
    ],
  ] as const) {
    const mutated = JSON.parse(JSON.stringify(base)) as Record<string, unknown>;
    mutate(mutated);
    const issues = validateEvidenceView(mutated);
    assert.ok(
      issues.some((item) => item.path === code),
      `expected ${code} in ${JSON.stringify(issues)}`,
    );
  }
});

test("B: the bridge attributes a view and refuses a foreign project/research/round", () => {
  const view = viewOf(readFixture("evidence-view.positive.json"));
  const attributed = attributeEvidenceView(view, ATTRIBUTION);
  assert.equal(attributed.task_ref.project_id, "proj-m1-03");
  assert.equal(attributed.research.research_id, "res-m1-03");

  const foreignProject = payloadOf(readFixture("platform-attribution-foreign-project.negative.json"));
  const foreignRef = parseTaskRef(foreignProject["task_ref"]);
  assert.throws(
    () => attributeTaskRef(foreignRef, ATTRIBUTION),
    (error: unknown) =>
      error instanceof BridgeContractError &&
      error.error.code === "foreign_project",
  );

  const foreignResearch = viewOf(readFixture("evidence-view-foreign-research.negative.json"));
  assert.throws(
    () => attributeEvidenceView(foreignResearch, ATTRIBUTION),
    (error: unknown) =>
      error instanceof BridgeContractError &&
      error.error.code === "foreign_research",
  );

  const foreignRound = viewOf(readFixture("evidence-view-foreign-round.negative.json"));
  assert.throws(
    () => attributeEvidenceView(foreignRound, ATTRIBUTION),
    (error: unknown) =>
      error instanceof BridgeContractError && error.error.code === "foreign_round",
  );
});

// ---------------------------------------------------------------------------
// 3. Decision policy and envelope
// ---------------------------------------------------------------------------

test("C: every decision vector reproduces the platform policy", () => {
  assert.deepEqual([...ACCEPT_PRECONDITION_CODES][0], "execution_not_completed");
  assert.deepEqual([...RESEARCH_DECISIONS], [
    "accept",
    "revise",
    "reject",
    "defer",
    "escalate",
  ]);
  for (const name of [
    "research-decision-accept.positive.json",
    "research-decision-revise-inconclusive.positive.json",
    "research-decision-partial-accept.negative.json",
    "research-decision-incomplete-accept.negative.json",
    "research-decision-inconclusive-accept.negative.json",
    "research-decision-refuted-accept.negative.json",
    "research-decision-not-evaluated-accept.negative.json",
    "research-decision-blocking-missing-check.negative.json",
  ]) {
    const document = readFixture(name);
    const payload = payloadOf(document);
    const view = assertEvidenceView(payload["evidence_view"]);
    const decision = payload["decision"] as ResearchDecision;
    const evaluation = evaluateDecision(view, decision);
    if (expectedValid(document)) {
      assert.equal(evaluation.allowed, true, `${name} must be allowed`);
      const record = buildDecisionRecord(view, decision);
      assert.equal(validateDecisionRecord(view, record).length, 0);
      assert.deepEqual(
        [...record.rationale_codes],
        record.rationale_codes.filter((code) =>
          (DECISION_RATIONALE_CODES as readonly string[]).includes(code),
        ),
      );
    } else {
      assert.equal(evaluation.allowed, false, `${name} must be refused`);
      assert.equal(evaluation.code, expectedCode(document), name);
      assert.throws(() => buildDecisionRecord(view, decision), /not publishable/);
    }
  }
});

test("C: an execution that returned a value is never accepted implicitly", () => {
  const view = viewOf(readFixture("evidence-view.positive.json"));
  const partial = assertEvidenceView({
    ...view,
    qualification: "partial_result",
    evidence_status: "partial",
  });
  assert.deepEqual(evaluateDecision(partial, "accept"), {
    allowed: false,
    code: "evidence_not_complete",
  });
  const notCompleted = assertEvidenceView({
    ...view,
    execution_terminal_state: "failed",
  });
  assert.deepEqual(evaluateDecision(notCompleted, "accept"), {
    allowed: false,
    code: "execution_not_completed",
  });
  for (const decision of ["revise", "reject", "defer", "escalate"] as const) {
    assert.equal(
      evaluateDecision(partial, decision).allowed,
      true,
      `${decision} stays available for partial evidence`,
    );
  }
});

test("C: a foreign or non digest-bound decision record is refused", () => {
  const view = viewOf(readFixture("evidence-view.positive.json"));
  const record = buildDecisionRecord(view, "accept");
  assert.equal(validateDecisionRecord(view, record).length, 0);
  const payload = record as unknown as Record<string, unknown>;

  type Mutation = (value: Record<string, unknown>) => void;
  const mutations: readonly [Mutation, string][] = [
    [(value) => (value["project_id"] = "proj-other"), "$.project_id"],
    [(value) => (value["research_id"] = "res-other"), "$.research_id"],
    [(value) => (value["round"] = 4), "$.round"],
    [
      (value) => {
        const taskRef = value["task_ref"] as Record<string, unknown>;
        (taskRef["release"] as Record<string, unknown>)["release_digest"] = `sha256:${"e".repeat(64)}`;
      },
      "$.task_ref",
    ],
    [(value) => (value["decision"] = "approved"), "$.decision"],
    [(value) => (value["evidence_refs"] = []), "$.evidence_refs"],
    [
      (value) => {
        value["reason"] = "the model concludes the evidence is sufficient";
      },
      "$.reason",
    ],
    [
      (value) => {
        value["rationale_codes"] = ["model_says_supported"];
      },
      "$.rationale_codes[0]",
    ],
  ];
  for (const [mutate, path] of mutations) {
    const mutated = JSON.parse(JSON.stringify(payload)) as Record<string, unknown>;
    mutate(mutated);
    const issues = validateDecisionRecord(view, mutated);
    assert.ok(
      issues.some((item) => item.path === path),
      `expected ${path} in ${JSON.stringify(issues)}`,
    );
  }

  assert.throws(
    () => assertDecisionRecord(view, { ...payload, reason: "model summary" }),
    /decision carries rationale codes/,
  );
});

test("C: the sample decision record round-trips as a closed envelope", () => {
  const document = readFixture("research-decision-accept.positive.json");
  const payload = payloadOf(document);
  const view = assertEvidenceView(payload["evidence_view"]);
  const record = payload["decision_record"] as DecisionRecord;
  assert.equal(assertDecisionRecord(view, record).decision, "accept");
  assert.ok(record.evidence_refs.length > 0);
  assert.equal(record.projection_digest, view.projection_digest);
});

// ---------------------------------------------------------------------------
// 4. One report model: memory.ts reuses these types
// ---------------------------------------------------------------------------

test("D: memory.ts keeps no second report model", () => {
  const source = readFileSync(
    fileURLToPath(new URL("../../packages/research/src/memory.ts", import.meta.url)),
    "utf8",
  );
  for (const forbidden of [
    "EVIDENCE_CLAIM_STATUSES",
    "FORBIDDEN_MODEL_EVIDENCE_SOURCES",
    "validateEvidenceReport",
    "interface EvidenceReport",
  ]) {
    assert.equal(
      source.includes(forbidden),
      false,
      `memory.ts must not redeclare ${forbidden}`,
    );
  }
  // The legacy consumption validator still refuses a model conclusion.
  const report = {
    report_id: "report-1",
    project_id: "proj-m1-03",
    claims: [
      {
        claim_id: "c1",
        status: "supported",
        validator_id: "check.x",
        validator_version: "1.0.0",
        source_refs: ["artifacts/freq.out"],
        evidence: [{ source_kind: "model_assumption", sha256: null, ref: null }],
      },
    ],
  };
  const issues = validateEvidenceReport(report);
  assert.ok(issues.some((item) => item.code === "model_assumption_not_evidence"));
});
