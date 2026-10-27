"use strict";
/*
 * Module tests — frontend/js/client/research-commands.js (M3-05b).
 *
 * The commands layer is the ONLY writer of research approvals: request/query/
 * decide/revoke through the injected v2 transport and submit through the
 * injected v1 transport. Every test injects a fake transport, so the module is
 * exercised without a network; the real-Controller integration layer lives in
 * tests/test_acp2_m305_research_commands_live.py.
 */
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const MODULE = "js/client/research-commands.js";
const MODULE_PATH = path.join(REPO_ROOT, "frontend", MODULE);

function hex64(char) {
  return "sha256:" + char.repeat(64);
}

function proposal() {
  return {
    proposal_id: "prop-rc-0001",
    project_id: "proj-rc",
    recipe_digest: hex64("a"),
    science: {
      capability_steps: [{ step_index: 0, capability: "geometry_optimization", version: "v1" }],
      initial_inputs: [{ input_id: "in-1", sha256: hex64("c"), format: "xyz" }],
      method: "r2SCAN-3c",
      mapping: "mapping-rc-v1",
      charge: 0,
      multiplicity: 1,
      allowed_substitutions: [],
    },
    budget: {
      model_requests: 4, input_tokens: 4000, output_tokens: 800, task_refs: 1,
      recipe_steps: 1, qc_invocations: 1, wall_seconds: 600, queue_seconds: 60,
      unit_basis: "per_task", notes: "commands fixture",
    },
    allowed_change_scope: { mode: "none", finite_substitutions: [] },
  };
}

function record(overrides) {
  return Object.assign({
    approval_ref: "ap_rc_0001",
    schema_version: 1,
    status: "pending",
    state: "pending",
    revision: 1,
    project_id: "proj-rc",
    proposal_ref: "prop-rc-0001",
    proposal_digest: hex64("1"),
    plan_digest: hex64("2"),
    science_digest: hex64("3"),
    binding_status: "full",
    science_projection: { recipe_digest: hex64("a"), science: {}, budget: {} },
    plan_definition: { workflow: "fake", input: { source: "CCO" }, method: {}, resources: {} },
    budget: { unit: "task_count", count: 1, timeout_s: 600 },
    requester_ref: "principal-requester",
    approver_ref: null,
    expires_at: "2026-10-28T01:00:00.000Z",
    task_ref: null,
  }, overrides || {});
}

function submissionBody() {
  return {
    workflow: "fake",
    name: "rc-submit",
    input: { source: "CCO" },
    method: { protocol: "ext" },
    resources: { nproc: 1 },
    project_id: "proj-rc",
  };
}

function approvedRecord() {
  return record({ status: "approved", state: "approved", revision: 2, approver_ref: "principal-reviewer" });
}

function httpError(status, code, rule) {
  const error = new Error("HTTP " + status);
  error.status = status;
  error.body = { detail: { code: code, message: "rejected", params: rule ? { rule: rule } : {} } };
  return error;
}

function typedError(promise) {
  return promise.then(
    (value) => ({ ok: true, value }),
    (error) => ({ ok: false, error })
  );
}

/** A command may refuse synchronously (argument shape) or asynchronously; both
 * are typed refusals for the caller. */
function typedCall(run) {
  try {
    return typedError(Promise.resolve(run()));
  } catch (error) {
    return Promise.resolve({ ok: false, error });
  }
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadCommands() {
    const sandbox = makeSandbox({ console: { log() {}, warn() {}, error() {} } });
    loadScript(MODULE, sandbox);
    return sandbox.ACPClient.researchCommands;
  }

  function fakeTransport(handler) {
    const calls = [];
    const fn = (target, opts) => {
      const call = {
        target: target,
        opts: opts || {},
        body: opts && typeof opts.body === "string" ? JSON.parse(opts.body) : null,
      };
      calls.push(call);
      return Promise.resolve().then(() => handler(call));
    };
    fn.calls = calls;
    return fn;
  }

  await test("commands require the injected v1/v2 transport (no direct fetch)", () => {
    const source = fs.readFileSync(MODULE_PATH, "utf8");
    assert(!/(^|[^A-Za-z0-9_$])fetch\s*\(/.test(source), "no direct fetch in the commands module");
    assert(source.indexOf("XMLHttpRequest") === -1, "no XMLHttpRequest in the commands module");
    const rc = loadCommands();
    const codes = [];
    [
      () => rc.query({ projectId: "proj-rc" }),
      () => rc.fetchApproval({ projectId: "proj-rc" }, "ap_rc_0001"),
      () => rc.request({ proposalRef: "prop-rc-0001", proposalDigest: hex64("1"), planDigest: hex64("2") }),
      () => rc.decide({}, "ap_rc_0001", { decision: "approve", expectedRevision: 1 }),
      () => rc.revoke({}, "ap_rc_0001", { expectedRevision: 1 }),
      () => rc.submit({}, submissionBody(), { approval: approvedRecord() }),
    ].forEach((run) => {
      try {
        run();
        codes.push("no-error");
      } catch (error) {
        codes.push(error.code || error.message);
      }
    });
    assertEq(codes, [
      "platform_transport_required", "platform_transport_required",
      "platform_transport_required", "platform_transport_required",
      "platform_transport_required", "platform_transport_required",
    ], "every command refuses without a transport");
  });

  await test("request posts the C science projection and never an identity field", async () => {
    const v2 = fakeTransport(() => record());
    const rc = loadCommands();
    rc.init({ v2 });
    const result = await rc.request({
      proposalRef: "prop-rc-0001",
      proposal: { proposal_id: "prop-rc-0001", project_id: "proj-rc", plan_digest: hex64("2") },
      scienceProjection: proposal(),
      plan: { workflow: "fake", input: { source: "CCO" } },
      budget: { unit: "task_count", count: 1, timeout_s: 600 },
      expiresAt: "2026-10-28T01:00:00.000Z",
      scope: { projectId: "proj-rc" },
    });
    assertEq(v2.calls.length, 1, "one approval request");
    const call = v2.calls[0];
    assertEq(call.target, "/research/approvals", "approval request path");
    assertEq(call.opts.method, "POST", "POST");
    assertEq(call.body.proposal_ref, "prop-rc-0001", "proposal ref");
    assertEq(call.body.project_id, "proj-rc", "project scope");
    assert(call.body.science_projection && call.body.science_projection.science.method === "r2SCAN-3c",
      "C science projection sent");
    assert(call.body.plan && call.body.plan.workflow === "fake", "execution plan sent");
    ["approver", "approver_ref", "approver_id", "approver_source", "approved_by",
      "role", "roles", "tenant", "tenant_id", "principal_id"].forEach((field) => {
      assert(!(field in call.body), "identity field never sent: " + field);
    });
    assertEq(result.approval.status, "pending", "server record adopted");
    assertEq(result.approval.revision, 1, "server revision adopted");
  });

  await test("query and fetch read the authoritative record and fail closed on unknown state", async () => {
    const v2 = fakeTransport((call) => (call.target.indexOf("/research/approvals/") === -1
      ? { schema_version: 1, approvals: [record({ status: "pending" })], snapshot_revision: 4 }
      : record({ status: "pending" })));
    const rc = loadCommands();
    rc.init({ v2 });
    const body = await rc.query({ projectId: "proj-rc" }, { approvalRef: "ap_rc_0001", limit: 5 });
    assertEq(body.approvals.length, 1, "one record");
    assertEq(body.snapshot_revision, 4, "snapshot revision");
    assertEq(v2.calls[0].target,
      "/research/approvals?limit=5&project_id=proj-rc&approval_ref=ap_rc_0001",
      "bounded query path");

    const ok = await rc.fetchApproval({ projectId: "proj-rc" }, "ap_rc_0001");
    assertEq(ok.approval.approval_ref, "ap_rc_0001", "single read");
    assertEq(v2.calls[1].target, "/research/approvals/ap_rc_0001?project_id=proj-rc",
      "single read path");

    const bad = fakeTransport(() => ({ approvals: [record({ status: "maybe" })] }));
    rc.init({ v2: bad });
    const unknown = await typedCall(() => rc.query({ projectId: "proj-rc" }));
    assertEq(unknown.ok, false, "unknown status refused");
    assertEq(unknown.error.code, "invalid_approval_record", "typed failure");

    const full = fakeTransport(() => record({ science_digest: "", binding_status: "full" }));
    rc.init({ v2: full });
    const unbound = await typedCall(() => rc.fetchApproval({}, "ap_rc_0001"));
    assertEq(unbound.error.code, "invalid_approval_record", "a full binding needs a digest");
  });

  await test("decide and revoke send the observed revision and surface typed 409/403", async () => {
    const v2 = fakeTransport(() => record({ status: "approved", revision: 2 }));
    const rc = loadCommands();
    rc.init({ v2 });
    const decided = await rc.decide({ projectId: "proj-rc" }, "ap_rc_0001",
      { decision: "approve", expectedRevision: 1, reason: "ok" });
    assertEq(v2.calls[0].target, "/research/approvals/ap_rc_0001/approve", "approve path");
    assertEq(v2.calls[0].body, { expected_revision: 1, reason: "ok" }, "decision body");
    assertEq(decided.approval.status, "approved", "approved record adopted");

    const rejected = await rc.decide({}, "ap_rc_0001", { decision: "reject", expectedRevision: 1 });
    assertEq(v2.calls[1].target, "/research/approvals/ap_rc_0001/reject", "reject path");
    assertEq(rejected.approval.revision, 2, "revision adopted");

    const revoked = await rc.revoke({}, "ap_rc_0001", { expectedRevision: 2, reason: "superseded" });
    assertEq(v2.calls[2].target, "/research/approvals/ap_rc_0001/revoke", "revoke path");
    assertEq(v2.calls[2].body.expected_revision, 2, "revoke revision");
    assertEq(revoked.approval.status, "approved", "response adopted verbatim");

    const conflict = fakeTransport(() => { throw httpError(409, "revision_conflict", "revision_conflict"); });
    rc.init({ v2: conflict });
    const stale = await typedCall(() => rc.decide({}, "ap_rc_0001", { decision: "approve", expectedRevision: 1 }));
    assertEq(stale.error.code, "revision_conflict", "409 maps to a typed conflict");
    assertEq(stale.error.httpStatus, 409, "http status preserved");
    assertEq(stale.error.rule, "revision_conflict", "rule preserved");

    const denied = fakeTransport(() => { throw httpError(403, "access_denied", "requester_approver_separation_required"); });
    rc.init({ v2: denied });
    const refused = await typedCall(() => rc.decide({}, "ap_rc_0001", { decision: "approve", expectedRevision: 1 }));
    assertEq(refused.error.code, "access_denied", "403 maps to access_denied");
    assertEq(refused.error.rule, "requester_approver_separation_required", "policy rule preserved");
  });

  await test("submit attaches the approved binding to the ONE normalized body", async () => {
    const v1 = fakeTransport(() => ({
      job_id: "20261027_001_fake",
      attempt: 1,
      task_ref: { task_id: "20261027_001_fake", job_id: "20261027_001_fake", attempt: 1 },
      plan_digest: hex64("2"),
      proposal_digest: hex64("1"),
      approval_ref: "ap_rc_0001",
      submit_class: "research",
      idempotency_key: "idem-1",
    }));
    const rc = loadCommands();
    rc.init({ v1 });
    const body = submissionBody();
    const result = await rc.submit({ projectId: "proj-rc" }, body,
      { approval: approvedRecord(), idempotencyKey: "idem-1" });
    assertEq(v1.calls.length, 1, "one submit");
    assertEq(v1.calls[0].target, "/jobs", "v1 submit path");
    const posted = v1.calls[0].body;
    assertEq(posted.workflow, "fake", "caller body kept (no second builder)");
    assertEq(posted.input, { source: "CCO" }, "caller input kept");
    assertEq(posted.plan_digest, hex64("2"), "approved plan digest attached");
    assertEq(posted.proposal_ref, "prop-rc-0001", "approved proposal ref attached");
    assertEq(posted.proposal_digest, hex64("1"), "approved proposal digest attached");
    assertEq(posted.budget, { unit: "task_count", count: 1, timeout_s: 600 },
      "approved budget attached when the body has none");
    assertEq(posted.idempotency_key, "idem-1", "idempotency key carried");
    assertEq(result.submitted, true, "submitted only with a TaskRef");
    assertEq(result.job_id, "20261027_001_fake", "job id");
    assertEq(result.attempt, 1, "attempt");
    assertEq(result.task_ref.task_id, "20261027_001_fake", "task ref");
    assertEq(body.plan_digest, undefined, "the caller body is never mutated");
  });

  await test("submit without a real TaskRef is unconfirmed, never success", async () => {
    const v1 = fakeTransport(() => ({
      job_id: "20261027_002_fake",
      attempt: 1,
      task_ref: null,
    }));
    const rc = loadCommands();
    rc.init({ v1 });
    const missing = await typedCall(() => rc.submit({}, submissionBody(), { approval: approvedRecord() }));
    assertEq(missing.ok, false, "no task ref is not a submit");
    assertEq(missing.error.code, "submit_unconfirmed", "typed unconfirmed");

    const noJob = fakeTransport(() => ({ status: "queued", attempt: 1, task_ref: { task_id: "x" } }));
    rc.init({ v1: noJob });
    const absent = await typedCall(() => rc.submit({}, submissionBody(), { approval: approvedRecord() }));
    assertEq(absent.error.code, "submit_unconfirmed", "missing job_id is unconfirmed");
  });

  await test("network failure on submit is unconfirmed and retryable", async () => {
    const v1 = fakeTransport(() => { throw new TypeError("Failed to fetch"); });
    const rc = loadCommands();
    rc.init({ v1 });
    const failed = await typedCall(() => rc.submit({}, submissionBody(), { approval: approvedRecord() }));
    assertEq(failed.ok, false, "network failure is not success");
    assertEq(failed.error.code, "submit_unconfirmed", "network failure is unconfirmed");
    assertEq(failed.error.retryable, true, "retryable");
  });

  await test("submit refuses a changed plan, proposal or budget before the wire", async () => {
    const v1 = fakeTransport(() => { throw new Error("must not be reached"); });
    const rc = loadCommands();
    rc.init({ v1 });

    const changedPlan = Object.assign(submissionBody(), { plan_digest: hex64("9") });
    const plan = await typedCall(() => rc.submit({}, changedPlan, { approval: approvedRecord() }));
    assertEq(plan.error.code, "approved_plan_digest_mismatch", "changed plan refused");

    const changedProposal = Object.assign(submissionBody(), { proposal_digest: hex64("9") });
    const digest = await typedCall(() => rc.submit({}, changedProposal, { approval: approvedRecord() }));
    assertEq(digest.error.code, "approved_proposal_digest_mismatch", "changed proposal refused");

    const changedBudget = Object.assign(submissionBody(), {
      budget: { unit: "task_count", count: 5, timeout_s: 600 },
    });
    const budget = await typedCall(() => rc.submit({}, changedBudget, { approval: approvedRecord() }));
    assertEq(budget.error.code, "approved_budget_mismatch", "changed budget refused");

    const pending = await typedCall(() => rc.submit({}, submissionBody(), { approval: record() }));
    assertEq(pending.error.code, "approval_not_granted", "a pending approval never submits");

    const none = await typedCall(() => rc.submit({}, submissionBody(), {}));
    assertEq(none.error.code, "approval_required", "an approval is required");
    assertEq(v1.calls.length, 0, "no refused submit reached the wire");
  });

  await test("the module owns no second body builder: submit is the caller's body plus binding", () => {
    const source = fs.readFileSync(MODULE_PATH, "utf8");
    assert(source.indexOf("buildStructureJobBody") === -1,
      "the commands layer never builds a job body");
    assert(source.indexOf("frozen_plan_definition") === -1,
      "no second plan normalization");
    ["canonicalJson", "sha256Hex", "computePlanDefinitionDigest"].forEach((token) => {
      assert(source.indexOf(token) === -1, "no second digest implementation: " + token);
    });
  });
};
