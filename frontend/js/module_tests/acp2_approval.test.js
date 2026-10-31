"use strict";
/*
 * Module tests — frontend/js/workbench/features/research-approval.js (M3-05b).
 *
 * The card is a pure consumer: status / revision / approver / task_ref come
 * from the platform approval record, and every mutation (request/decide/revoke/
 * submit) goes through an injected `researchCommands` adapter. The tests below
 * inject a fake command layer plus the REAL approval module; the real-Controller
 * HTTP layer is exercised by tests/test_acp2_m305_research_commands_live.py.
 */
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const FEATURE = "js/workbench/features/research-approval.js";
const FEATURE_PATH = path.join(REPO_ROOT, "frontend", FEATURE);
const FIXTURE_PROPOSAL = path.join(
  REPO_ROOT, "docs", "reference", "next-change", "acp2-contracts",
  "experience", "fixtures", "proposal.valid.json"
);
const T03_REFERENCE_DIGEST =
  "sha256:32bd96dd1c8d2eb962a419590ff91ebf695adef1c587c147185f522be5422d9b";

function makeEl(id) {
  return {
    id: id,
    innerHTML: "",
    textContent: "",
    style: {},
    dataset: {},
    attrs: {},
    listeners: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    getAttribute(name) {
      return this.attrs[name] === undefined ? null : this.attrs[name];
    },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    appendChild() {},
  };
}

function makeDom() {
  const elements = new Map();
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeEl(id));
      return elements.get(id);
    },
    addEventListener() {},
    querySelectorAll: () => [],
    createElement: (tag) => makeEl(tag),
  };
}

function hex64(char) {
  return "sha256:" + char.repeat(64);
}

function baseProposal() {
  return {
    proposal_id: "prop-ra-0001",
    project_id: "proj-acp2-ra",
    question_ref: "q-ra-1",
    question_revision: 1,
    reaction_ref: "rxn-ra-1",
    reaction_revision: 1,
    hypothesis: "the endo pathway is favored",
    recipe_digest: hex64("a"),
    plan_definition_digest: hex64("b"),
    science: {
      capability_steps: [
        { step_index: 0, capability: "geometry_optimization", version: "v1" },
      ],
      initial_inputs: [
        { input_id: "in-1", sha256: hex64("c"), format: "xyz" },
      ],
      method: "r2SCAN-3c",
      mapping: "mapping-butadiene-ethene-endo-v1",
      charge: 0,
      multiplicity: 1,
      allowed_substitutions: [],
    },
    budget: {
      model_requests: 24,
      input_tokens: 120000,
      output_tokens: 24000,
      task_refs: 1,
      recipe_steps: 2,
      qc_invocations: 2,
      wall_seconds: 3600,
      queue_seconds: 600,
      unit_basis: "per_scenario",
      notes: "fixture round; one new local TaskRef",
    },
    approval_ref: null,
    allowed_change_scope: { mode: "none", finite_substitutions: [] },
    round: 1,
    decision: "proposed",
    created_at: "2026-10-23T00:00:00.000Z",
    human_edits: [],
  };
}

function user(id) {
  return { kind: "user", user_id: id };
}

function throwsWith(prefix, fn) {
  let caught = null;
  try {
    fn();
  } catch (error) {
    caught = error;
  }
  return caught && new RegExp("^" + prefix).test(caught.message) ? caught : null;
}

function rejectionCode(promise) {
  return promise.then(
    () => ({ ok: true, code: null }),
    (error) => ({ ok: false, code: error && error.code, error: error })
  );
}

function commandError(code, status, rule) {
  const error = new Error(code + ": rejected");
  error.code = code;
  if (status != null) error.httpStatus = status;
  error.rule = rule || null;
  return error;
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadApproval(stubs, clock) {
    const sandbox = makeSandbox(Object.assign({
      document: makeDom(),
      console: { log() {}, warn() {}, error() {} },
      t: (key) => key,
    }, stubs || {}));
    loadScript(FEATURE, sandbox);
    const ra = sandbox.ACPResearchApproval;
    ra.init({
      now: typeof clock === "function"
        ? clock
        : () => Date.parse("2026-10-23T00:30:00.000Z"),
    });
    return ra;
  }

  function serverRecord(ra, proposal, overrides) {
    const digest = ra.computePlanDefinitionDigest(proposal);
    return Object.assign({
      approval_ref: "ap_ra_0001",
      schema_version: 1,
      status: "pending",
      state: "pending",
      revision: 1,
      project_id: proposal.project_id,
      proposal_ref: proposal.proposal_id,
      proposal_digest: hex64("1"),
      plan_digest: hex64("2"),
      science_digest: digest,
      binding_status: "full",
      science_projection: {
        recipe_digest: proposal.recipe_digest,
        science: proposal.science,
        budget: proposal.budget,
      },
      plan_definition: {
        workflow: "singlepoint",
        input: { source_type: "xyz_text", source: "O 0 0 0" },
        method: { schema_id: "dft_singlepoint" },
        resources: { nproc: 4 },
      },
      budget: { unit: "task_count", count: 1, timeout_s: 600 },
      requester_ref: "principal-requester",
      approver_ref: null,
      requested_at: "2026-10-23T00:00:00.000Z",
      expires_at: "2026-10-23T01:00:00.000Z",
      decided_at: null,
      reason: null,
      task_ref: null,
    }, overrides || {});
  }

  function approvedRecord(ra, proposal, overrides) {
    return serverRecord(ra, proposal, Object.assign({
      status: "approved",
      state: "approved",
      revision: 2,
      approver_ref: "principal-reviewer",
      decided_at: "2026-10-23T00:10:00.000Z",
    }, overrides || {}));
  }

  function fakeCommands(handlers) {
    handlers = handlers || {};
    const calls = [];
    return {
      calls,
      query(scope, opts) {
        calls.push({ command: "query", scope, opts });
        return Promise.resolve(handlers.query
          ? handlers.query(scope, opts)
          : { approvals: [] });
      },
      request(payload, opts) {
        calls.push({ command: "request", payload, opts });
        return Promise.resolve(handlers.request(payload, opts));
      },
      decide(scope, ref, action) {
        calls.push({ command: "decide", scope, ref, action });
        return Promise.resolve(handlers.decide(ref, action));
      },
      revoke(scope, ref, action) {
        calls.push({ command: "revoke", scope, ref, action });
        return Promise.resolve(handlers.revoke(ref, action));
      },
      submit(scope, body, opts) {
        calls.push({ command: "submit", scope, body, opts });
        return Promise.resolve(handlers.submit(body, opts));
      },
    };
  }

  function mountedCard(ra, proposal, record, extra) {
    const dom = makeDom();
    ra.mount(dom, Object.assign({
      autoDiscover: false,
      proposal: proposal,
      record: record,
      plan: { workflow: "singlepoint", input: { source: "O 0 0 0" } },
    }, extra || {}));
    return dom;
  }

  await test("approval card renders proposal and recipe digests", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    const card = ra.cardFromRecord(record);
    const html = ra.renderApprovalCard(card, proposal);
    const computed = ra.computePlanDefinitionDigest(proposal);
    assert(html.indexOf(computed) !== -1, "full plan_definition_digest rendered");
    assert(html.indexOf(proposal.recipe_digest) !== -1, "recipe digest rendered");
    assert(html.indexOf(record.science_digest) !== -1, "bound science digest rendered");
    assert(html.indexOf(record.plan_digest) !== -1, "execution plan digest rendered");
    assert(html.indexOf('data-ra-field="plan_definition_digest"') !== -1, "digest row present");
    assert(html.indexOf('data-ra-field="recipe_digest"') !== -1, "recipe row present");
    assert(html.indexOf('data-ra-field="bound_digest"') !== -1, "bound digest row present");
    assert(html.indexOf('data-ra-field="execution_plan_digest"') !== -1,
      "execution digest row present");
  });

  await test("approval card renders scientific limits, resource caps and cost basis", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    const html = ra.renderApprovalCard(ra.cardFromRecord(record), proposal);
    assert(html.indexOf("r2SCAN-3c") !== -1, "method rendered");
    assert(html.indexOf("mapping-butadiene-ethene-endo-v1") !== -1, "mapping rendered");
    ["method", "mapping", "charge", "multiplicity"].forEach((field) => {
      assert(html.indexOf('data-ra-field="' + field + '"') !== -1,
        "scientific limit row rendered: " + field);
    });
    ["model_requests", "input_tokens", "output_tokens", "task_refs", "recipe_steps",
      "qc_invocations", "wall_seconds", "queue_seconds"].forEach((field) => {
      assert(html.indexOf('data-ra-field="budget.' + field + '"') !== -1,
        "resource cap rendered: " + field);
    });
    assert(html.indexOf('data-ra-field="unit_basis"') !== -1, "cost basis row rendered");
    assert(html.indexOf("per scenario") !== -1, "cost basis unit_basis humanized and rendered");
    assert(html.indexOf("fixture round") !== -1, "cost basis notes rendered");
  });

  await test("approval card shows the science plan and the execution plan side by side with both budgets", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    const html = ra.renderApprovalCard(ra.cardFromRecord(record), proposal);
    const scienceAt = html.indexOf('data-ra-plan="science"');
    const executionAt = html.indexOf('data-ra-plan="execution"');
    assert(scienceAt !== -1 && executionAt !== -1, "both plan summaries rendered");
    assert(html.indexOf('data-ra-plan-compare="1"') !== -1, "plans are a comparison block");
    assert(html.indexOf("singlepoint") !== -1, "execution workflow rendered");
    assert(html.indexOf('data-ra-field="execution.workflow"') !== -1, "execution row present");
    assert(html.indexOf('data-ra-budget="approved"') !== -1, "approved budget rendered");
    assert(html.indexOf('data-ra-budget="current"') !== -1, "current budget rendered");
    assert(html.indexOf('data-ra-field="approved_budget.count"') !== -1,
      "approved launch budget row rendered");
  });

  await test("approval card displays the approval expiry", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    const html = ra.renderApprovalCard(ra.cardFromRecord(record), proposal);
    assert(html.indexOf("2026-10-23T01:00:00.000Z") !== -1, "expires_at rendered");
    assert(html.indexOf('data-ra-expired="false"') !== -1, "expiry shown as valid");

    const later = loadApproval({}, () => Date.parse("2026-10-23T02:00:00.000Z"));
    const laterProposal = baseProposal();
    later.setProposal(laterProposal);
    later.setCard(approvedRecord(later, laterProposal));
    assertEq(later.getState().expired, true, "state reports expiry");
    assertEq(later.approvalValidity(later.cardFromRecord(later.getState() && later.getState().taskRef
      ? approvedRecord(later, laterProposal) : approvedRecord(later, laterProposal)), laterProposal).reason,
    "approval_expired", "expired approval is not valid");
    const expiredHtml = later.renderApprovalCard(later.cardFromRecord(approvedRecord(later, laterProposal)),
      laterProposal);
    assert(expiredHtml.indexOf('data-ra-expired="true"') !== -1, "expired state displayed");
  });

  await test("plan digest matches the frozen T-03 reference vector", () => {
    const ra = loadApproval();
    const fixture = JSON.parse(fs.readFileSync(FIXTURE_PROPOSAL, "utf8"));
    assertEq(ra.computePlanDefinitionDigest(fixture), T03_REFERENCE_DIGEST,
      "frozen fixture digest");
  });

  await test("bind/invalidate matrix: method, mapping, initial input and budget changes invalidate the approval", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = ra.cardFromRecord(approvedRecord(ra, proposal));
    const boundDigest = card.binding.plan_definition_digest;
    assert(ra.approvalValidity(card, proposal).valid === true, "baseline approval valid");

    const mutations = {
      method: (p) => { p.science.method = "wB97X-D4"; },
      mapping: (p) => { p.science.mapping = "mapping-v2"; },
      initial_input: (p) => { p.science.initial_inputs[0].sha256 = hex64("d"); },
      budget: (p) => { p.budget.model_requests = 25; },
    };
    Object.keys(mutations).forEach((name) => {
      const mutated = baseProposal();
      mutations[name](mutated);
      const digest = ra.computePlanDefinitionDigest(mutated);
      assert(digest !== boundDigest, name + " change moves plan_definition_digest");
      const check = ra.approvalValidity(card, mutated);
      assertEq(check.valid, false, name + " invalidates the approval");
      assertEq(check.reason, "approval_binding_mismatch", name + " reason");
    });
  });

  await test("bind/invalidate matrix: appended task and evidence refs keep the approval valid", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = ra.cardFromRecord(approvedRecord(ra, proposal));
    const withRefs = baseProposal();
    withRefs.task_refs = [
      { project_id: "proj-acp2-ra", job_id: "job-1", attempt: 1, release_id: "rel-1" },
    ];
    withRefs.evidence_refs = [
      { evidence_id: "ev-1", kind: "output", sha256: hex64("e") },
    ];
    assertEq(ra.computePlanDefinitionDigest(withRefs),
      ra.computePlanDefinitionDigest(proposal), "digest ignores appended refs");
    const check = ra.approvalValidity(card, withRefs);
    assertEq(check.valid, true, "appended refs do not invalidate");
    assertEq(check.reason, "ok", "stays ok");
    assert(ra.canSubmitPlan(card, withRefs) === true, "submit still allowed");
  });

  await test("bind/invalidate matrix: display name and timestamp changes keep the approval valid", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = ra.cardFromRecord(approvedRecord(ra, proposal));
    const renamed = baseProposal();
    renamed.display_name = "Round 2 selection";
    renamed.updated_at = "2026-10-24T09:00:00.000Z";
    renamed.created_at = "2026-10-22T09:00:00.000Z";
    renamed.round = 7;
    assertEq(ra.computePlanDefinitionDigest(renamed),
      ra.computePlanDefinitionDigest(proposal), "digest ignores names/timestamps");
    const check = ra.approvalValidity(card, renamed);
    assertEq(check.valid, true, "display/timestamp changes do not invalidate");
  });

  await test("dsh tool approval never changes platform approval state", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    const session = { platform: record, dsh: [] };
    const next = ra.recordDshToolApproval(session, {
      toolCallId: "tc-1",
      decision: "allow",
      principal: user("alice"),
    });
    assert(next.platform === record, "platform record object is untouched");
    assertEq(next.platform.status, "approved", "platform status unchanged");
    assertEq(next.dsh.length, 1, "dsh decision recorded in its own mapping");
    assertEq(next.dsh[0].mapping, "dsh_tool", "recorded under the dsh mapping");
    assertEq(next.dsh[0].platform_effect, "none", "declares no platform effect");
    assertEq(ra.APPROVAL_MAPPINGS.dsh_tool.mutates.length, 0,
      "dsh mapping mutates no platform state");
    assertEq(ra.APPROVAL_MAPPINGS.platform_research.authority, "platform",
      "platform approvals are decided by the platform");
    const dispatched = ra.applyApprovalMapping("dsh_tool", session, {
      toolCallId: "tc-2",
      decision: "deny",
      principal: user("alice"),
    });
    assert(dispatched.platform === record, "mapping dispatch leaves platform untouched");
    assert(ra.canSubmitPlan(ra.cardFromRecord(record), proposal) === true,
      "platform submit state unchanged by dsh approval");
  });

  await test("platform record mapping: status, revision, approver and task_ref come from the server", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal, {
      task_ref: { task_id: "20261027_001_x", job_id: "20261027_001_x", attempt: 1 },
    });
    ra.setProposal(proposal);
    ra.setCard(record);
    const snapshot = ra.getState();
    assertEq(snapshot.status, "approved", "status from the record");
    assertEq(snapshot.revision, 2, "revision from the record");
    assertEq(snapshot.approverRef, "principal-reviewer", "approver from the record");
    assertEq(snapshot.requesterRef, "principal-requester", "requester from the record");
    assertEq(snapshot.taskRef.task_id, "20261027_001_x", "task_ref from the record");
    assertEq(snapshot.bindingStatus, "full", "binding from the science digest");
    assertEq(snapshot.validity, "ok", "validity verified against the current proposal");
  });

  await test("platform transport required: no commands means no local approval", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    ra.setProposal(proposal);
    ra.setCard(serverRecord(ra, proposal));
    assertEq(ra.getState().transportRequired, true, "no commands injected");
    const decide = await rejectionCode(ra.decide("approve", { expectedRevision: 1 }));
    assertEq(decide.code, "platform_transport_required", "decide refuses without a transport");
    const submit = await rejectionCode(ra.submit({ body: { workflow: "singlepoint" } }));
    assertEq(submit.code, "platform_transport_required",
      "submit refuses without a platform transport");
    const dom = mountedCard(ra, proposal, serverRecord(ra, proposal));
    const html = dom.getElementById("research-approval-body").innerHTML;
    assert(html.indexOf('data-ra-notice="platform_transport_required"') !== -1,
      "the card states the missing transport");
    assert(html.indexOf('data-ra-action="approve" disabled') !== -1,
      "the approve button is disabled without a transport");
    assert(html.indexOf('data-ra-action="refresh" disabled') !== -1,
      "refresh is disabled without a platform transport");
    const expiredDom = mountedCard(ra, proposal, serverRecord(ra, proposal, {
      status: "expired",
      expires_at: "2026-10-22T00:00:00.000Z",
    }));
    assert(expiredDom.getElementById("research-approval-body").innerHTML
      .indexOf('data-ra-action="request"') !== -1,
    "an expired record offers a (disabled) re-request");
  });

  await test("pending to approved to revoked chain follows the server record", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const store = { record: serverRecord(ra, proposal) };
    let queryCalls = 0;
    const commands = fakeCommands({
      query() { queryCalls += 1; return { approvals: [store.record] }; },
      decide(ref, action) {
        if (action.expectedRevision !== store.record.revision) {
          throw commandError("revision_conflict", 409, "revision_conflict");
        }
        store.record = approvedRecord(ra, proposal, {
          revision: store.record.revision + 1,
          approver_ref: "principal-reviewer",
        });
        return { approval: store.record };
      },
      revoke(ref, action) {
        assertEq(action.expectedRevision, store.record.revision, "revoke carries the revision");
        store.record = Object.assign({}, store.record, {
          status: "revoked", state: "revoked", revision: store.record.revision + 1,
        });
        return { approval: store.record };
      },
    });
    ra.init({ commands });
    ra.setProposal(proposal);
    ra.setCard(store.record);
    assertEq(ra.getState().status, "pending", "starts pending");
    assertEq(ra.getState().validity, "approval_not_granted", "pending is not an approval");

    const approved = await ra.performAction("approve");
    assertEq(approved.status, "approved", "server approved the record");
    assertEq(ra.getState().status, "approved", "card adopts the server state");
    assertEq(ra.getState().validity, "ok", "approved record is valid");
    assertEq(ra.getState().revision, 2, "revision advanced by the server");

    const revoked = await ra.performAction("revoke");
    assertEq(revoked.status, "revoked", "server revoked the record");
    assertEq(ra.getState().status, "revoked", "card adopts the revoked state");
    assertEq(ra.getState().validity, "approval_revoked", "revoked is never valid");
    assertEq(queryCalls, 0, "no local state invention");
  });

  await test("stale revision 409 refreshes and requires manual re-review without replaying", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const store = {
      record: serverRecord(ra, proposal),
      newer: approvedRecord(ra, proposal, { revision: 5, approver_ref: "principal-reviewer" }),
    };
    let decideCalls = 0;
    let queryCalls = 0;
    const commands = fakeCommands({
      query() {
        queryCalls += 1;
        return { approvals: [store.newer] };
      },
      decide() {
        decideCalls += 1;
        throw commandError("revision_conflict", 409, "revision_conflict");
      },
    });
    ra.init({ commands });
    ra.setProposal(proposal);
    ra.setCard(store.record);
    const dom = mountedCard(ra, proposal, store.record, { commands });
    const conflict = await rejectionCode(ra.performAction("approve"));
    assertEq(conflict.code, "revision_conflict", "409 is surfaced");
    assertEq(decideCalls, 1, "the decision is never replayed");
    assertEq(queryCalls >= 1, true, "the record was refreshed after the conflict");
    assertEq(ra.getState().revision, 5, "the refreshed revision is adopted");
    assertEq(ra.getState().reviewRequired, true, "human re-review is required");
    assert(dom.getElementById("research-approval-body").innerHTML
      .indexOf('data-ra-review-required="true"') !== -1, "the review banner is rendered");
    assertEq(ra.getState().status, "approved", "the refreshed status is shown");
  });

  await test("another user's approve is rejected", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = serverRecord(ra, proposal, {
      requester_ref: "principal-alice",
      approver_ref: "principal-bob",
    });
    const commands = fakeCommands({
      decide() {
        throw commandError("access_denied", 403, "requester_approver_separation_required");
      },
      query() { return { approvals: [record] }; },
    });
    ra.init({ commands });
    ra.setProposal(proposal);
    ra.setCard(record);
    const refused = await rejectionCode(ra.performAction("approve"));
    assertEq(refused.code, "access_denied", "the platform refused the decision");
    assertEq(ra.getState().status, "pending", "the card stays pending");
    assertEq(ra.getState().revision, 1, "the revision is unchanged");
    assertEq(ra.getState().notice, "access_denied", "the refusal is visible");
    assertEq(ra.getState().validity, "approval_not_granted", "still not an approval");
  });

  await test("a model claim of user agreement is never an approval", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = serverRecord(ra, proposal);
    const commands = fakeCommands({ decide() { throw new Error("must not be called"); } });
    ra.init({ commands });
    ra.setProposal(proposal);
    ra.setCard(record);
    const refused = await rejectionCode(ra.decide("approve", {
      expectedRevision: 1,
      approvalClaim: { user_agreed: true },
    }));
    assertEq(refused.code, "model_claim_not_authority", "a claim never decides");
    assertEq(commands.calls.length, 0, "no decision reached the platform");
    assertEq(ra.getState().status, "pending", "the record is unchanged");

    assertEq(ra.isUserApprovalAuthority({ kind: "model", user_agreed: true }), false,
      "model principal has no approval authority");
    assertEq(ra.isUserApprovalAuthority(user("alice")), true, "user principal has authority");
    assert(throwsWith("model_claim_not_authority", () => {
      ra.assertNotModelApprovalClaim({ source: "model", user_agreed: true });
    }), "model claim assertion refuses");
  });

  await test("cross principal or project refusal leaves the record unchanged", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal, { revision: 4 });
    let mode = "other-principal";
    const commands = fakeCommands({
      decide() {
        throw commandError(mode === "other-principal" ? "access_denied" : "not_found",
          403, "approval_not_yours");
      },
      revoke() { throw commandError("not_found", 404, "not_found"); },
      query() { return { approvals: [record] }; },
    });
    ra.init({ commands });
    ra.setProposal(proposal);
    ra.setCard(record);

    const otherPrincipal = await rejectionCode(ra.performAction("revoke"));
    assertEq(otherPrincipal.code, "not_found", "cross-principal revoke is refused");
    assertEq(ra.getState().status, "approved", "state unchanged after refusal");
    assertEq(ra.getState().revision, 4, "revision unchanged after refusal");

    mode = "cross-project";
    ra.setCard(serverRecord(ra, proposal));
    const crossProject = await rejectionCode(ra.performAction("approve"));
    assertEq(crossProject.code, "not_found",
      "cross-project decide is not disclosed (same not-found shape)");
    assertEq(ra.getState().status, "pending", "still pending, never locally approved");
  });

  await test("submit requires a real job_id, attempt and task_ref", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    const calls = [];
    const commands = fakeCommands({
      query() { return { approvals: [Object.assign({}, record,
        { task_ref: { task_id: "20261027_009_x", attempt: 1 } })] }; },
      submit() {
        calls.push("submit");
        return { submitted: true, job_id: "20261027_009_x", attempt: 1, task_ref: null };
      },
    });
    const taskView = {
      invalidateAll() {},
      fetchSummary(jobId) { calls.push("summary:" + jobId); return Promise.resolve({}); },
    };
    ra.init({
      commands,
      taskView,
      buildSubmitBody: () => ({ workflow: "singlepoint", input: { source: "O 0 0 0" } }),
    });
    ra.setProposal(proposal);
    ra.setCard(record);
    const unconfirmed = await rejectionCode(ra.submit({}));
    assertEq(unconfirmed.code, "submit_unconfirmed", "no TaskRef is never submitted");
    assertEq(ra.getState().submitted, false, "not submitted without a TaskRef");

    const realCommands = fakeCommands({
      query() { return { approvals: [Object.assign({}, record,
        { task_ref: { task_id: "20261027_010_x", attempt: 1 } })] }; },
      submit() {
        calls.push("submit-real");
        return {
          submitted: true,
          job_id: "20261027_010_x",
          attempt: 1,
          task_ref: { task_id: "20261027_010_x", job_id: "20261027_010_x", attempt: 1 },
        };
      },
    });
    ra.init({ commands: realCommands, taskView, buildSubmitBody: () => ({ workflow: "singlepoint" }) });
    ra.setCard(record);
    const dom = mountedCard(ra, proposal, record, {
      commands: realCommands, taskView,
      buildSubmitBody: () => ({ workflow: "singlepoint" }),
    });
    const submitted = await ra.submit({});
    assertEq(submitted.job_id, "20261027_010_x", "the real job id is reported");
    assertEq(ra.getState().submitted, true, "submitted with a real TaskRef");
    assertEq(ra.getState().submittedJobId, "20261027_010_x", "job id in state");
    assertEq(ra.getState().taskRef.task_id, "20261027_010_x", "task ref adopted");
    assert(calls.indexOf("summary:20261027_010_x") !== -1, "task view refreshed");
    assert(calls.indexOf("submit-real") !== -1, "the approved submit reached the commands");
    const html = dom.getElementById("research-approval-body").innerHTML;
    assert(html.indexOf('data-ra-submitted="true"') !== -1, "submitted is displayed");
    assert(html.indexOf("20261027_010_x#1") !== -1, "job#attempt is displayed");
  });

  await test("network failure on submit is unconfirmed, never success", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    const commands = fakeCommands({
      submit() {
        const error = new TypeError("Failed to fetch");
        throw error;
      },
    });
    ra.init({ commands, buildSubmitBody: () => ({ workflow: "singlepoint" }) });
    ra.setProposal(proposal);
    ra.setCard(record);
    const failed = await rejectionCode(ra.submit({}));
    assertEq(failed.ok, false, "network failure is not success");
    assertEq(failed.code, "submit_unconfirmed", "unknown outcome is unconfirmed");
    assertEq(ra.getState().submitted, false, "never reported as submitted");
    assertEq(ra.getState().notice, "submit_unconfirmed", "the unconfirmed notice is visible");
  });

  await test("unbound submit is blocked without the bound digest", async () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const record = approvedRecord(ra, proposal);
    let submitCalls = 0;
    const commands = fakeCommands({ submit() { submitCalls += 1; return {}; } });
    ra.init({ commands, buildSubmitBody: () => ({ workflow: "singlepoint" }) });
    ra.setProposal(proposal);
    ra.setCard(record);

    const mutated = baseProposal();
    mutated.science.method = "wB97X-D4";
    ra.setProposal(mutated);
    const changed = await rejectionCode(ra.submit({}));
    assertEq(changed.code, "approval_binding_mismatch", "a changed plan cannot ride the approval");

    ra.setProposal(proposal);
    ra.init({ commands, buildSubmitBody: () => null });
    const noBody = await rejectionCode(ra.submit({}));
    assertEq(noBody.code, "submit_body_required", "submit needs the one normalized body");

    ra.init({ commands: fakeCommands({}), buildSubmitBody: () => ({ workflow: "singlepoint" }) });
    ra.dispose();
    ra.setProposal(proposal);
    const noRecord = await rejectionCode(ra.submit({}));
    assertEq(noRecord.code, "not_requested", "submit needs a card");
    assertEq(submitCalls, 0, "no refused submit reached the platform");
  });

  await test("approval module owns no network call", () => {
    const source = fs.readFileSync(FEATURE_PATH, "utf8");
    const bareCall = /(^|[^A-Za-z0-9_$])fetch\s*\(/;
    assert(!bareCall.test(source), "no direct fetch call in the approval module");
    assert(source.indexOf("XMLHttpRequest") === -1, "no XMLHttpRequest in the approval module");
    assert(source.indexOf("apr-") === -1, "no locally invented apr-* approval ids");
    assert(source.indexOf("dsp-") === -1, "no locally invented dsp-* dispatch refs");
    assert(source.indexOf("local_approved") === -1, "no local success fallback");
  });

  await test("second client reads the approval written by the first client", async () => {
    const server = {
      records: {},
      next(ref) { return server.records[ref]; },
    };
    function commandsForClient(ra, proposal) {
      return fakeCommands({
        request(payload) {
          const record = serverRecord(ra, proposal, { approval_ref: "ap_shared_0001" });
          server.records[record.approval_ref] = record;
          return { approval: record };
        },
        query() {
          return { approvals: Object.keys(server.records).map((key) => server.records[key]) };
        },
        decide(ref) {
          const current = server.records[ref];
          server.records[ref] = approvedRecord(ra, proposal, {
            approval_ref: ref,
            revision: current.revision + 1,
          });
          return { approval: server.records[ref] };
        },
      });
    }
    const proposal = baseProposal();
    const clientA = loadApproval();
    const clientB = loadApproval();
    const commandsA = commandsForClient(clientA, proposal);
    const commandsB = commandsForClient(clientB, proposal);
    clientA.init({ commands: commandsA });
    clientB.init({ commands: commandsB });
    clientA.setProposal(proposal);
    clientB.setProposal(proposal);

    const requested = await clientA.requestApproval({
      proposal: proposal,
      platformProposal: { proposal_id: proposal.proposal_id, project_id: proposal.project_id },
      plan: { workflow: "singlepoint" },
      budget: { unit: "task_count", count: 1 },
    });
    assertEq(requested.status, "pending", "client A requested the approval");
    await clientB.refresh({});
    assertEq(clientB.getState().status, "pending", "client B observes the same record");
    assertEq(clientB.getState().approvalRef, "ap_shared_0001", "same approval ref");

    await clientA.performAction("approve");
    await clientB.refresh({});
    assertEq(clientB.getState().status, "approved", "client B sees the decision");
    assertEq(clientB.getState().revision, 2, "client B sees the server revision");
  });

  await test("approval card uses the real ACPClient.research namespace, not an injected fake", async () => {
    const sandbox = makeSandbox({
      document: makeDom(),
      console: { log() {}, warn() {}, error() {} },
      t: (key) => key,
    });
    loadScript("js/client/resource-store.js", sandbox);
    loadScript("js/workbench/contracts/experience_contract.js", sandbox);
    loadScript("js/client/research-query.js", sandbox);
    loadScript("js/client/research-commands.js", sandbox);
    loadScript(FEATURE, sandbox);
    const ra = sandbox.ACPResearchApproval;
    const fetched = [];
    sandbox.ACPClient.research.init({
      v2: (target) => {
        fetched.push(target);
        return Promise.resolve(JSON.parse(fs.readFileSync(FIXTURE_PROPOSAL, "utf8")));
      },
    });
    sandbox.ACPClient.researchCommands.init({
      v1: () => Promise.resolve({}),
      v2: () => Promise.resolve({ approvals: [] }),
    });
    ra.init({ now: () => Date.parse("2026-10-23T00:30:00.000Z") });
    ra.mount(sandbox.document, {
      autoDiscover: false,
      proposal: baseProposal(),
    });
    const after = ra.mount(sandbox.document, {
      proposalRef: "prop-da-selectivity-001",
      projectId: "proj-acp2-da",
      autoDiscover: true,
    });
    assertEq(after.mounted, true, "mounted");
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert(fetched.length >= 1, "proposal read flowed through ACPClient.research");
    assert(fetched[0].indexOf("/research/proposals/") !== -1, "research proposal path");
    assertEq(after.transportRequired, false, "the page commands were picked up");
    const html = sandbox.document.getElementById("research-approval-body").innerHTML;
    assert(html.indexOf("proj-acp2-da") !== -1,
      "fetched proposal adopted and rendered via the real client");
  });
};
