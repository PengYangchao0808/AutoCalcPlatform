#!/usr/bin/env node
/**
 * M4-01 Workbench adapter leg (real frontend modules + real Controller HTTP).
 *
 * Mirrors the M3-05b live layer (`tests/test_acp2_m305_research_commands_live.py`
 * + its driver): the REAL Workbench client modules run in Node, given a REAL
 * HTTP transport built on Node's global `fetch`, pointed at the live uvicorn
 * Controller the Python harness started.  Nothing is faked:
 *
 *   frontend/js/client/resource-store.js          (the shared resource cache)
 *   frontend/js/client/research-query.js          (proposal/record/evidence reads)
 *   frontend/js/workbench/contracts/experience_contract.js  (frozen validators)
 *   frontend/js/workbench/features/research-evidence-view.js (pure projection)
 *   frontend/js/workbench/features/research-approval.js      (approval card state)
 *
 * The driver only calls those modules; it never re-implements a validator, a
 * projection or a platform read.  Evidence is projected by the real view module,
 * so a Workbench-visible row is the same row an operator would see.
 *
 * Usage: node scripts/acceptance/acp2_full_chain_workbench.mjs <scenario.json>
 * Output: one JSON document on stdout.
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const scenario = JSON.parse(readFileSync(process.argv[2], "utf8"));
const steps = [];

function load(relative) {
  const code = readFileSync(scenario.repo_root + "/" + relative, "utf8");
  vm.runInThisContext(code, { filename: relative });
}

global.window = global;
for (const module of scenario.modules) load(module);

async function request(path, opts) {
  const response = await fetch(scenario.base_url + path, opts);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch (error) {
    body = text;
  }
  if (!response.ok) {
    const httpError = new Error("HTTP " + response.status);
    httpError.status = response.status;
    httpError.body = body;
    throw httpError;
  }
  return body;
}

const v1 = (suffix, opts) => request("/api/v1" + suffix, opts);
const v2 = (suffix, opts) => request("/api/v2" + suffix, opts);

function typed(error) {
  return {
    ok: false,
    code: error && error.code ? error.code : null,
    status:
      error && error.httpStatus
        ? error.httpStatus
        : error && error.status
          ? error.status
          : null,
    message: String((error && error.message) || error),
  };
}

async function step(name, run) {
  try {
    const value = await run();
    steps.push({ name, ok: true, value });
    return value;
  } catch (error) {
    steps.push(Object.assign({ name }, typed(error)));
    throw error;
  }
}

async function expectedRefusal(name, run) {
  try {
    const value = await run();
    steps.push({ name, ok: false, problem: "the call was not refused", value });
    return null;
  } catch (error) {
    steps.push(Object.assign({ name, refused: true }, typed(error)));
    return typed(error);
  }
}

async function main() {
  ACPClient.research.init({ v2 });
  ACPResearchApproval.init({ now: () => Date.parse(scenario.now) });

  const scope = {
    projectId: scenario.project_id,
    permission: "read",
    sessionSeed: scenario.session_seed,
  };

  const proposal = await step("proposal.read", () =>
    ACPClient.research.fetchProposal(
      { id: scenario.proposal_id, revision: scenario.proposal_revision },
      scope
    )
  );
  if (proposal.proposal_id !== scenario.proposal_id) {
    throw new Error("research-query returned a different proposal");
  }

  const record = await step("record.read", () =>
    ACPClient.research.fetchRecord({ id: scenario.research_id }, scope)
  );
  const evidence = await step("evidence.read", () =>
    ACPClient.research.fetchEvidence(
      { id: scenario.research_id },
      scope,
      { round: scenario.round }
    )
  );

  const projected = await step("evidence.project", async () =>
    ACPResearchEvidence.project(evidence)
  );
  if (!projected.ok) throw new Error("the Workbench evidence projection refused the view");

  const summary = await step("evidence.summarize", async () =>
    ACPClient.research.summarizeEvidence(evidence)
  );

  ACPResearchApproval.setProposal(proposal);
  const approvalRecord = await step("approval.read", () =>
    v2("/research/approvals/" + encodeURIComponent(scenario.approval_ref))
  );
  if (approvalRecord.approval_ref !== scenario.approval_ref) {
    throw new Error("the platform returned a different approval record");
  }
  // `setCard` takes the platform *record* (it applies `cardFromRecord` itself).
  ACPResearchApproval.setCard(approvalRecord);
  const card = await step("approval.card", async () => ACPResearchApproval.getState());
  if (card.validity !== "ok") {
    throw new Error("the approval card is not valid: " + String(card.validity));
  }

  // A foreign project must be indistinguishable from an unknown resource: the
  // real HTTP 404 is surfaced as a refusal, never as an empty record.
  const foreign = await expectedRefusal("record.foreign_project", () =>
    ACPClient.research.fetchRecord({ id: scenario.research_id }, {
      projectId: scenario.foreign_project_id,
      permission: "read",
      sessionSeed: scenario.session_seed,
    })
  );

  const output = {
    schema: "acp2.m4-01.full-chain.workbench.v1",
    ok: true,
    steps,
    evidence_rows: projected.rows,
    evidence_supported: projected.supported || false,
    evidence_summary: {
      execution_terminal_state: summary.execution_terminal_state,
      evidence_status: summary.evidence_status,
      overall_claim_status: summary.overall_claim_status,
      display: summary.display,
      projection_digest: summary.projection_digest,
      round: summary.round,
    },
    approval_validity: {
      validity: card.validity,
      status: card.status,
      binding_status: card.bindingStatus,
      revision: card.revision,
    },
    record_round: record.round,
    record_decision: record.decision,
    record_revision: record.record_revision,
    foreign_refusal: foreign,
  };
  process.stdout.write(JSON.stringify(output) + "\n");
}

main().catch((error) => {
  process.stdout.write(
    JSON.stringify({
      schema: "acp2.m4-01.full-chain.workbench.v1",
      ok: false,
      steps,
      error: typed(error),
    }) + "\n"
  );
  process.exitCode = 1;
});
