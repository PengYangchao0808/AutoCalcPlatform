/* M1-06 / M3-05a: read-only research evidence view.
 *
 * Runs the REAL `workbench/features/research-evidence-view.js` and the REAL
 * `workbench/contracts/experience_contract.js` in the zero-dependency vm
 * harness (no browser, no network).
 *
 * Pinned contracts:
 *   1. only complete + supported + no blocking check displays as "supported";
 *   2. execution status and claim status are separate rows (never merged);
 *   3. a fifth claim status is refused by the contract validator;
 *   4. a blocking missing check is always visible;
 *   5. the feature owns no fetch/URL/transport;
 *   6. the feature renders text only (textContent), never innerHTML;
 *   7. a foreign/missing identity is an explicit empty state, not a false view.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const FRONTEND = path.resolve(__dirname, "..", "..");
const CONTRACT_REL = "js/workbench/contracts/experience_contract.js";
const FEATURE_REL = "js/workbench/features/research-evidence-view.js";

function sha(char) {
  return "sha256:" + char.repeat(64);
}

function validView(overrides) {
  const view = {
    schema: "acp2.runtime.evidence-projection.v1",
    contract_version: "1.0.0",
    research: {
      principal_id: "principal_local",
      project_id: "proj-m106",
      research_id: "res-m106",
      session_id: "ses-m106",
      round: 0,
    },
    task_ref: {
      project_id: "proj-m106",
      task_id: "job-m106",
      job_id: "job-m106",
      attempt: 1,
      release: { release_id: "rel-m106", release_digest: sha("c") },
    },
    report: {
      report_id: "report-m106",
      digest: sha("d"),
      schema_version: 1,
      subject_id: "job-m106",
    },
    execution_terminal_state: "completed",
    qualification: "complete",
    evidence_status: "complete",
    claims: [{ claim_id: "c-1", status: "supported", validator_id: "v", validator_version: "1" }],
    overall_claim_status: "supported",
    blocking_missing_checks: [],
    evidence_refs: [{ evidence_id: "e-1", kind: "native_output", sha256: sha("e") }],
    limitations: [],
    provenance_digest: "",
    normal_reuse: false,
    projection_digest: sha("f"),
  };
  return Object.assign(view, overrides || {});
}

function makeEl() {
  return {
    textContent: "",
    className: "",
    children: [],
    appendChild(child) {
      this.children.push(child);
    },
  };
}

function makeDom() {
  const elements = new Map();
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeEl());
      return elements.get(id);
    },
    createElement() {
      return makeEl();
    },
  };
}

function setup(h) {
  const dom = makeDom();
  const sandbox = h.makeSandbox({ window: undefined });
  h.loadScript(CONTRACT_REL, sandbox);
  h.loadScript(FEATURE_REL, sandbox);
  return {
    dom,
    contracts: sandbox.ACPWorkbench.contracts.experience,
    view: sandbox.ACPResearchEvidence,
  };
}

module.exports.register = async function register(h) {
  await h.test("the platform evidence projection passes the frozen contract", () => {
    const { contracts } = setup(h);
    const check = contracts.validateEvidenceView(validView());
    h.assert(check.ok, "problems: " + check.problems.join("; "));
  });

  await h.test("a fifth claim status and a non-digest projection are refused", () => {
    const { contracts } = setup(h);
    const view = validView();
    view.claims = [{ claim_id: "c-1", status: "probably_true" }];
    view.projection_digest = "not-a-digest";
    const check = contracts.validateEvidenceView(view);
    h.assert(!check.ok, "the contract accepted a fifth claim status");
    h.assert(
      check.problems.some((item) => item.indexOf("claims[0].status") === 0),
      "expected a claim-status problem",
    );
    h.assert(
      check.problems.some((item) => item.indexOf("projection_digest") === 0),
      "expected a projection-digest problem",
    );
  });

  await h.test("only complete+supported+no-blocking displays as supported", () => {
    const { view } = setup(h);
    h.assert(h.assertEq, "harness");
    const ok = view.project(validView());
    h.assert(ok.ok && ok.supported === true, "the complete view must be supported");
    const partial = view.project(validView({ evidence_status: "partial" }));
    h.assert(partial.supported === false, "a partial execution must not be supported");
    const inconclusive = view.project(validView({ overall_claim_status: "inconclusive" }));
    h.assert(inconclusive.supported === false, "an inconclusive claim must not be supported");
    const blocked = view.project(validView({ blocking_missing_checks: ["c-1/check-1"] }));
    h.assert(blocked.supported === false, "a blocking missing check must not be supported");
  });

  await h.test("execution and claim status stay two rows and blocking checks render", () => {
    const { dom, view } = setup(h);
    const mounted = view.mount(dom, {
      view: validView({ evidence_status: "incomplete", blocking_missing_checks: ["c-1/check-1"] }),
    });
    h.assert(mounted.ok, "mount failed");
    const labels = mounted.rows.map((row) => row.label);
    h.assert(labels.indexOf("execution") >= 0, "missing execution row");
    h.assert(labels.indexOf("claims") >= 0, "missing claims row");
    h.assert(labels.indexOf("verdict") >= 0, "missing verdict row");
    const verdict = mounted.rows.find((row) => row.label === "verdict");
    h.assert(verdict.value === "needs_review", "an incomplete execution read as supported");
    h.assert(
      mounted.rows.some((row) => row.label === "blocking[0]" && row.value === "c-1/check-1"),
      "the blocking check is not visible",
    );
  });

  await h.test("a non-object view is an explicit empty state, never a false pass", () => {
    const { dom, view } = setup(h);
    const mounted = view.mount(dom, {});
    h.assert(mounted.ok === false, "an empty view must not report ok");
    h.assert(mounted.rows.length === 0, "an empty view must not render rows");
  });

  await h.test("the feature owns no fetch/URL/transport", () => {
    const source = fs.readFileSync(path.join(FRONTEND, FEATURE_REL), "utf8");
    h.assert(!/\bfetch\s*\(/.test(source), "fetch( found in the feature");
    h.assert(!/XMLHttpRequest/.test(source), "XMLHttpRequest found in the feature");
    h.assert(!/["']\/api\//.test(source), "a hardcoded API path found in the feature");
    h.assert(!/innerHTML/.test(source), "innerHTML found in the feature");
    h.assert(!/\.submit\s*\(|approve\s*\(|decide\s*\(/.test(source), "a write entry found");
  });
};
