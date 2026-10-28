/*
 * M3-05b real-Controller driver (Node).
 *
 * Loads the REAL client modules (research-commands.js + research-approval.js)
 * and injects a REAL HTTP transport built on Node's global fetch, pointed at a
 * live uvicorn Controller started by
 * tests/test_acp2_m305_research_commands_live.py. Nothing is faked: approval
 * request/query/decide/revoke and the job submit all cross a socket.
 *
 * The frozen plan body/digest is supplied by the Python test (the platform's
 * single canonicalization), never recomputed here.
 *
 * Output: one JSON document on stdout with every step result.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const scenario = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const steps = [];

function load(relative) {
  const code = fs.readFileSync(scenario.repo_root + "/" + relative, "utf8");
  vm.runInThisContext(code, { filename: relative });
}

global.window = global;
load(scenario.commands_module);
load(scenario.approval_module);

async function request(url, opts) {
  const response = await fetch(url, opts);
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

const v1 = (suffix, opts) => request(scenario.base_url + "/api/v1" + suffix, opts);
const v2 = (suffix, opts) => request(scenario.base_url + "/api/v2" + suffix, opts);

const commands = ACPClient.researchCommands;
const approval = ACPResearchApproval;
commands.init({ v1, v2 });
approval.init({ now: () => Date.parse("2026-10-23T00:30:00.000Z") });

function typed(error) {
  return {
    ok: false,
    code: error && error.code ? error.code : null,
    status: error && error.httpStatus ? error.httpStatus : (error && error.status ? error.status : null),
    rule: error && error.rule ? error.rule : null,
    message: String((error && error.message) || error),
  };
}

async function step(name, run) {
  try {
    const value = await run();
    steps.push({ name: name, ok: true, value: value });
    return value;
  } catch (error) {
    steps.push(Object.assign({ name: name }, typed(error)));
    throw error;
  }
}

async function main() {
  const proposal = scenario.science_projection;
  const budget = scenario.budget;

  const empty = await step("query.empty", () => commands.query({ projectId: scenario.project_id }));
  if (empty.approvals.length !== 0) throw new Error("expected no approvals yet");

  const requested = await step("request", () => commands.request({
    proposalRef: scenario.proposal_ref,
    proposal: scenario.platform_proposal,
    scienceProjection: proposal,
    plan: scenario.plan,
    budget: budget,
    scope: { projectId: scenario.project_id },
  }));
  const pending = requested.approval;

  approval.setProposal(proposal);
  approval.setCard(pending);
  const pendingState = approval.getState();
  if (pendingState.validity !== "approval_not_granted") {
    throw new Error("pending record must not be a valid approval: " + pendingState.validity);
  }
  steps.push({
    name: "approval.pending_view",
    ok: true,
    value: {
      status: pendingState.status,
      revision: pendingState.revision,
      binding: pendingState.bindingStatus,
      validity: pendingState.validity,
    },
  });

  const approved = await step("decide.approve", () => commands.decide(
    { projectId: scenario.project_id },
    pending.approval_ref,
    { decision: "approve", expectedRevision: pending.revision, reason: "m3-05 live" }
  ));
  approval.setCard(approved.approval);
  const approvedState = approval.getState();
  if (approvedState.validity !== "ok") {
    throw new Error("approved record must verify against the current proposal: " + approvedState.validity);
  }
  if (approvedState.approverRef !== scenario.expected_principal) {
    throw new Error("approver must come from the authenticated channel: " + approvedState.approverRef);
  }
  steps.push({
    name: "approval.approved_view",
    ok: true,
    value: {
      status: approvedState.status,
      revision: approvedState.revision,
      approver: approvedState.approverRef,
      binding: approvedState.bindingStatus,
      validity: approvedState.validity,
    },
  });

  const secondClient = {};
  global.window = global;
  // A second, independent client (fresh module instances) reading the same
  // Controller: this is the cross-refresh / second-client read.
  const sandbox = vm.createContext({ console: console, window: null });
  sandbox.window = sandbox;
  vm.runInContext(fs.readFileSync(scenario.repo_root + "/" + scenario.commands_module, "utf8"), sandbox, {
    filename: "research-commands-second.js",
  });
  sandbox.ACPClient.researchCommands.init({ v1: v1, v2: v2 });
  const other = sandbox.ACPClient.researchCommands;
  const otherRead = await step("second_client.query", () => other.query({ projectId: scenario.project_id }));
  if (otherRead.approvals.length !== 1 || otherRead.approvals[0].status !== "approved") {
    throw new Error("the second client must read the same approved record");
  }
  secondClient.approval_ref = otherRead.approvals[0].approval_ref;
  secondClient.status = otherRead.approvals[0].status;

  await step("stale_revision_409", async () => {
    try {
      // The record is approved at revision 2; revoking with the stale
      // revision 1 must be a typed 409 and must not touch the record.
      await commands.revoke({ projectId: scenario.project_id }, pending.approval_ref, {
        expectedRevision: pending.revision,
      });
    } catch (error) {
      if (error.code !== "revision_conflict" || error.httpStatus !== 409) {
        throw new Error("expected a typed 409, got " + error.code + "/" + error.httpStatus);
      }
      const after = await commands.query({ projectId: scenario.project_id },
        { approvalRef: pending.approval_ref });
      if (after.approvals[0].status !== "approved") {
        throw new Error("a stale-revision refusal changed the record");
      }
      steps.push({
        name: "stale_revision_409.result",
        ok: true,
        value: { code: error.code, status: error.httpStatus },
      });
      return { code: error.code, status: error.httpStatus };
    }
    throw new Error("a stale revision revoke was applied");
  });

  await step("submit.changed_plan_refused", async () => {
    try {
      await commands.submit({ projectId: scenario.project_id },
        Object.assign({}, scenario.submit_body, { plan_digest: scenario.other_digest }),
        { approval: approved.approval });
    } catch (error) {
      if (error.code !== "approved_plan_digest_mismatch") {
        throw new Error("a changed plan must be refused locally: " + error.code);
      }
      return { code: error.code };
    }
    throw new Error("a changed plan was submitted");
  });

  const submitted = await step("submit", () => commands.submit(
    { projectId: scenario.project_id },
    scenario.submit_body,
    { approval: approved.approval, idempotencyKey: scenario.idempotency_key }
  ));
  if (!submitted.job_id || !submitted.attempt || !submitted.task_ref.task_id) {
    throw new Error("submit without a real TaskRef must fail");
  }

  const bound = await step("approval.task_ref", async () => {
    const read = await commands.query({ projectId: scenario.project_id },
      { approvalRef: pending.approval_ref });
    const record = read.approvals[0];
    if (!record.task_ref || !record.task_ref.task_id) {
      throw new Error("the platform did not bind the TaskRef to the approval");
    }
    return { task_ref: record.task_ref, status: record.status, revision: record.revision };
  });

  const revoked = await step("decide.revoke", () => commands.revoke(
    { projectId: scenario.project_id },
    pending.approval_ref,
    { expectedRevision: bound.revision, reason: "m3-05 live revoke" }
  ));
  approval.setCard(revoked.approval);
  const revokedState = approval.getState();
  if (revokedState.validity !== "approval_revoked") {
    throw new Error("a revoked record is never valid: " + revokedState.validity);
  }

  const after = await step("second_client.after_revoke", () => other.query({ projectId: scenario.project_id }));
  if (after.approvals[0].status !== "revoked") {
    throw new Error("the second client must observe the revoke");
  }

  return {
    ok: true,
    steps: steps,
    submitted: {
      job_id: submitted.job_id,
      attempt: submitted.attempt,
      task_ref: submitted.task_ref,
      approval_ref: submitted.approval_ref,
    },
    record: {
      approval_ref: pending.approval_ref,
      status_after_revoke: revoked.approval.status,
      task_ref: bound.task_ref,
      second_client: secondClient,
    },
  };
}

main().then((result) => {
  process.stdout.write(JSON.stringify(result));
}, (error) => {
  process.stdout.write(JSON.stringify({
    ok: false,
    steps: steps,
    error: String((error && error.stack) || error),
  }));
  process.exitCode = 1;
});
