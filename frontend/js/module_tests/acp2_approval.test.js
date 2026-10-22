"use strict";
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

  function pendingCard(ra, proposal, approverRef) {
    return ra.requestApproval({
      proposal: proposal,
      projectId: proposal.project_id,
      approverRef: approverRef || "usr-bob",
      requestedBy: user("alice"),
      requestedAt: "2026-10-23T00:00:00.000Z",
      expiresAt: "2026-10-23T01:00:00.000Z",
    });
  }

  function approvedCard(ra, proposal, approverRef) {
    const card = pendingCard(ra, proposal, approverRef);
    return ra.decideApproval(card, {
      decision: "approve",
      principal: user(approverRef || "usr-bob"),
    });
  }

  await test("approval card renders proposal and recipe digests", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = approvedCard(ra, proposal);
    const html = ra.renderApprovalCard(card, proposal);
    const computed = ra.computePlanDefinitionDigest(proposal);
    assert(html.indexOf(computed) !== -1, "full plan_definition_digest rendered");
    assert(html.indexOf(proposal.recipe_digest) !== -1, "recipe digest rendered");
    assert(html.indexOf(card.binding.plan_definition_digest) !== -1, "bound digest rendered");
    assert(html.indexOf('data-ra-field="plan_definition_digest"') !== -1, "digest row present");
    assert(html.indexOf('data-ra-field="recipe_digest"') !== -1, "recipe row present");
    assert(html.indexOf('data-ra-field="bound_digest"') !== -1, "bound digest row present");
  });

  await test("approval card renders scientific limits, resource caps and cost basis", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = pendingCard(ra, proposal);
    const html = ra.renderApprovalCard(card, proposal);
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
    assert(html.indexOf("per_scenario") !== -1, "cost basis unit_basis rendered");
    assert(html.indexOf("fixture round") !== -1, "cost basis notes rendered");
  });

  await test("approval card displays the approval expiry", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = pendingCard(ra, proposal);
    const html = ra.renderApprovalCard(card, proposal);
    assert(html.indexOf("2026-10-23T01:00:00.000Z") !== -1, "expires_at rendered");
    assert(html.indexOf('data-ra-expired="false"') !== -1, "expiry shown as valid");

    const later = loadApproval({}, () => Date.parse("2026-10-23T02:00:00.000Z"));
    const laterProposal = baseProposal();
    const staleCard = pendingCard(later, laterProposal);
    later.setCard(staleCard);
    later.setProposal(laterProposal);
    const expiredHtml = later.renderApprovalCard(staleCard, laterProposal);
    assert(expiredHtml.indexOf('data-ra-expired="true"') !== -1, "expired state displayed");
    assertEq(later.getState().expired, true, "state reports expiry");
    assertEq(later.approvalValidity(staleCard, laterProposal).reason, "approval_expired",
      "expired approval is not valid");
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
    const card = approvedCard(ra, proposal);
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
    const card = approvedCard(ra, proposal);
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
    const card = approvedCard(ra, proposal);
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
    const card = approvedCard(ra, proposal);
    const session = { platform: card, dsh: [] };
    const next = ra.recordDshToolApproval(session, {
      toolCallId: "tc-1",
      decision: "allow",
      principal: user("alice"),
    });
    assert(next.platform === card, "platform card object is untouched");
    assertEq(next.platform.status, "approved", "platform status unchanged");
    assertEq(next.dsh.length, 1, "dsh decision recorded in its own mapping");
    assertEq(next.dsh[0].mapping, "dsh_tool", "recorded under the dsh mapping");
    assertEq(next.dsh[0].platform_effect, "none", "declares no platform effect");
    assertEq(ra.APPROVAL_MAPPINGS.dsh_tool.mutates.length, 0,
      "dsh mapping mutates no platform state");
    const dispatched = ra.applyApprovalMapping("dsh_tool", session, {
      toolCallId: "tc-2",
      decision: "deny",
      principal: user("alice"),
    });
    assert(dispatched.platform === card, "mapping dispatch leaves platform untouched");
    assert(ra.canSubmitPlan(dispatched.platform, proposal) === true,
      "platform submit state unchanged by dsh approval");
  });

  await test("unbound submit is blocked without the bound digest", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = approvedCard(ra, proposal);
    const mutated = baseProposal();
    mutated.science.method = "wB97X-D4";
    assert(throwsWith("approval_binding_mismatch", () => {
      ra.submitPlan(card, mutated, { principal: user("alice") });
    }), "mutated plan cannot ride the old approval");

    const pending = pendingCard(ra, baseProposal());
    assert(throwsWith("approval_not_granted", () => {
      ra.submitPlan(pending, baseProposal(), { principal: user("alice") });
    }), "pending card cannot submit");

    assert(throwsWith("approval_binding_mismatch", () => {
      ra.submitPlan(card, proposal, { principal: user("alice"), boundDigest: hex64("f") });
    }), "submit is allowed only with the bound digest");

    const allowed = ra.submitPlan(card, proposal, {
      principal: user("alice"),
      boundDigest: card.binding.plan_definition_digest,
    });
    assertEq(allowed.submitted, true, "bound digest submits");
  });

  await test("another user's approve is rejected", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = pendingCard(ra, proposal);
    assert(throwsWith("approver_mismatch", () => {
      ra.decideApproval(card, { decision: "approve", principal: user("mallory") });
    }), "approve by another user refused");
    const stored = ra.loadCard(proposal.project_id, card.approval_ref);
    assertEq(stored.status, "pending", "card stays pending after refused approve");
    const ok = ra.decideApproval(card, { decision: "approve", principal: user("usr-bob") });
    assertEq(ok.status, "approved", "bound approver may approve");
  });

  await test("a model claim of user agreement is never an approval", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = pendingCard(ra, proposal);
    assert(throwsWith("model_claim_not_authority", () => {
      ra.decideApproval(card, {
        decision: "approve",
        principal: { kind: "model", model_id: "m-1", user_agreed: true },
      });
    }), "model cannot approve");
    assertEq(ra.isUserApprovalAuthority({ kind: "model", user_agreed: true }), false,
      "model principal has no approval authority");
    assertEq(ra.isUserApprovalAuthority(user("alice")), true, "user principal has authority");

    const approved = approvedCard(ra, proposal);
    assert(throwsWith("model_claim_not_authority", () => {
      ra.submitPlan(approved, proposal, {
        principal: user("alice"),
        approvalClaim: { user_agreed: true },
      });
    }), "a claim of user agreement never authorizes a submit");
    assert(throwsWith("model_claim_not_authority", () => {
      ra.assertNotModelApprovalClaim({ source: "model", user_agreed: true });
    }), "model claim assertion refuses");
  });

  await test("revoked approval can never authorize a submit", () => {
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = approvedCard(ra, proposal);
    const revoked = ra.revokeApproval(card, {
      principal: user("usr-bob"),
      reason: "superseded round",
    });
    assertEq(revoked.status, "revoked", "card revoked");
    const check = ra.approvalValidity(revoked, proposal);
    assertEq(check.valid, false, "revoked card invalid");
    assertEq(check.reason, "approval_revoked", "revoked reason");
    assert(throwsWith("approval_revoked", () => {
      ra.submitPlan(revoked, proposal, { principal: user("alice") });
    }), "submit refused after revoke");
  });

  await test("approval state persists across dispose and remount", () => {
    const stored = {};
    const persistence = {
      read: (key) => (stored[key] ? JSON.parse(JSON.stringify(stored[key])) : null),
      write: (key, value) => { stored[key] = JSON.parse(JSON.stringify(value)); },
    };
    const dom = makeDom();
    const ra = loadApproval();
    const proposal = baseProposal();
    const card = approvedCard(ra, proposal);
    ra.mount(dom, {
      autoDiscover: false,
      proposal: proposal,
      card: card,
      persistence: persistence,
    });
    const first = ra.getState();
    assertEq(first.status, "approved", "mounted card approved");
    ra.dispose();
    const dom2 = makeDom();
    ra.mount(dom2, {
      autoDiscover: false,
      proposal: proposal,
      persistence: persistence,
      cardRef: first.cardRef,
    });
    const second = ra.getState();
    assertEq(second.status, "approved", "card restored after remount");
    assertEq(second.approvalRef, card.approval_ref, "same approval ref restored");
    assert(Object.keys(stored).length >= 1, "persistence adapter received the card");
    const html = dom2.getElementById("research-approval-body").innerHTML;
    assert(html.indexOf(card.binding.plan_definition_digest) !== -1,
      "restored card renders the bound digest");
  });

  await test("approval module owns no network call", () => {
    const source = fs.readFileSync(FEATURE_PATH, "utf8");
    const bareCall = /(^|[^A-Za-z0-9_$])fetch\s*\(/;
    assert(!bareCall.test(source), "no direct fetch call in the approval module");
    assert(source.indexOf("XMLHttpRequest") === -1, "no XMLHttpRequest in the approval module");
  });
};
