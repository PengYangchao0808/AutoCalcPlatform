/* T-23 / C17a: QuestionSpec/ReactionSpec binding (workbench).
 *
 * Runs the REAL reaction-builder spec functions and the science-question
 * QuestionSpec extension in the zero-dependency vm harness. The sandbox
 * injects the legacy module globals (`reactionDefinition`, `parseXyzAtoms`
 * as a recording stub) the way the page provides them.
 *
 * Pinned contracts:
 *   1. duplicate atom map is rejected;
 *   2. missing product is rejected;
 *   3. charge non-conservation is rejected;
 *   4. a net multi-step reaction entered as a single TS yields a
 *      clarification item (view-model field names), not a submit;
 *   5. CDXML input is marked DEFERRED and never parsed — in particular never
 *      as an empty structure;
 *   6. P2 input range frozen to MOL/SDF/SMILES + structure upload;
 *   7. atom labels / mapping / stereo preserved across edits (no silent
 *      rewrite; a structure change marks them stale instead);
 *   8. charge/spin and solvent/temperature/standard state are visible;
 *   9. human confirmations / approximations are recorded with input revision;
 *  10. net reaction vs elementary step distinction is shown;
 *  11. no silent identity fill: missing charge/spin stay "未指定";
 *  12. science-question submit is blocked while clarifications are open.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FRONTEND = path.resolve(__dirname, "..", "..");
const REACTION_REL = "js/workbench/features/reaction-builder.js";
const QUESTION_REL = "js/workbench/features/science-question.js";

function makeEl(id) {
  return {
    id,
    innerHTML: "",
    textContent: "",
    value: "",
    disabled: false,
    hidden: false,
    style: {},
    dataset: {},
    attrs: {},
    listeners: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    getAttribute(name) { return this.attrs[name] === undefined ? null : this.attrs[name]; },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
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

function setupReaction(h) {
  const dom = makeDom();
  const sandbox = h.makeSandbox({
    document: dom,
    t: () => "",
    escapeHtml: (value) => String(value == null ? "" : value),
    requestAnimationFrame: (fn) => { setImmediate(fn); return 1; },
    console: { log() {}, warn() {}, error() {} },
  });
  h.loadScript(REACTION_REL, sandbox);
  const parseCalls = [];
  sandbox.parseXyzAtoms = function recordingParseXyzAtoms(xyzText) {
    parseCalls.push(String(xyzText));
    const lines = String(xyzText).split("\n");
    const atoms = [];
    for (let i = 2; i < lines.length; i += 1) {
      const parts = lines[i].trim().split(/\s+/);
      if (parts.length >= 4) {
        atoms.push({ elem: parts[0], x: 0, y: 0, z: 0, id: i - 2 });
      }
    }
    return atoms;
  };
  return {
    sandbox,
    dom,
    parseCalls,
    evalJs: (code) => vm.runInContext(code, sandbox),
    specPanel: () => dom.getElementById("mech-reaction-spec"),
  };
}

function makeState() {
  return { spec: null };
}

function addBasicReaction(R, state) {
  const S = R.sandbox;
  S.addReactionSpecParticipant(state, "reactant", {
    ref: "r1", label: "butadiene", charge: 0, multiplicity: 1,
    stereo: "E", atomLabels: ["C1", "C2", "C3", "C4"], atomMap: [1, 2, 3, 4],
  });
  S.addReactionSpecParticipant(state, "product", {
    ref: "p1", label: "cyclohexene", charge: 0, multiplicity: 1,
    stereo: "R", atomLabels: ["C1", "C2", "C3", "C4", "C5", "C6"], atomMap: [1, 2, 3, 4, 5, 6],
  });
  return state;
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const QSCRIPTS = [
    "js/client/resource-store.js",
    "js/workbench/contracts/experience_contract.js",
    "js/client/science-query.js",
    "js/client/wizard-endpoints.js",
    "js/workbench/features/wizard/submit-edit.js",
    QUESTION_REL,
  ];

  function setupQuestion() {
    const dom = makeDom();
    const log = [];
    const fetchCalls = [];
    const sandbox = makeSandbox({
      document: dom,
      t: (key) => key,
      fetch: function () {
        fetchCalls.push(Array.prototype.slice.call(arguments));
        throw new Error("fetch is forbidden in the question/reaction binding");
      },
    });
    QSCRIPTS.forEach((rel) => loadScript(rel, sandbox));
    const W = sandbox.window;
    const parseCalls = [];
    const submitCalls = [];
    W.ACPScienceQuestion.init({
      science: {
        fetchCapabilities: () => Promise.resolve({
          protocol_version: "acp-science/1",
          tools: ["acp_capabilities", "acp_preflight", "acp_submit", "acp_status",
            "acp_cancel", "acp_prepare_artifact", "acp_read_artifact", "acp_analyze"],
          capabilities: {
            fixture: true,
            questions: [{
              question_id: "single_point_energy",
              label: "单点能",
              workflow: "singlepoint",
              method_schema_id: "dft_singlepoint",
              level_id: "single_point",
              engine: "orca",
              basis: "def2-TZVPP",
              defaults: {
                method: { value: "wB97M-V", source: "capability:fixture.method" },
                charge: { value: 0, source: "capability:fixture.charge" },
                multiplicity: { value: 1, source: "capability:fixture.multiplicity" },
                solvent: { value: "none", source: "capability:fixture.solvent" },
              },
              limitations: [],
            }],
          },
        }),
        preflight: () => Promise.resolve({ executed: false, plan: {}, estimate: { feasible: true } }),
        fetchView: () => Promise.resolve({
          snapshot_revision: 1,
          task_ref: { job_id: "task-1", attempt: 1 },
          artifacts: [], claims: [], clarifications: [], read_only: true,
        }),
      },
      // M5-03 F6: binding is explicit; discovery no longer defaults to a demo
      // project id.
      projectId: "proj-m503",
      wizardEndpoints: {
        parseStructure: (payload) => {
          parseCalls.push(payload);
          return Promise.resolve({
            ok: true, detected_format: "xyz", errors: [], warnings: [],
            structures: [{ name: "demo", atom_count: 3, charge: 0, multiplicity: 1 }],
          });
        },
      },
      buildJobBody: (spec) => ({ workflow: spec.workflow, input: spec.input, method: spec.method, resources: spec.resources }),
      submit: (body) => {
        submitCalls.push(body);
        return Promise.resolve({ job_id: "task-1", attempt: 1 });
      },
      catalog: () => [
        { id: "singlepoint", label: "Singlepoint", status: "active", visible: true, method_schema_id: "dft_singlepoint" },
      ],
    });
    W.ACPScienceQuestion.mount(dom, { autoDiscover: false });
    return {
      sandbox, dom, log, fetchCalls, parseCalls, submitCalls,
      Q: W.ACPScienceQuestion,
      bodyHtml: () => dom.getElementById("science-question-body").innerHTML,
    };
  }

  await test("rejects duplicate atom map in the reaction spec", async () => {
    const R = setupReaction(h);
    const state = makeState();
    R.sandbox.addReactionSpecParticipant(state, "reactant", {
      ref: "r1", charge: 0, multiplicity: 1, atomLabels: ["C1", "C2"], atomMap: [1, 1],
    });
    R.sandbox.addReactionSpecParticipant(state, "product", {
      ref: "p1", charge: 0, multiplicity: 1, atomLabels: ["C1"], atomMap: [2],
    });
    const result = R.sandbox.validateReactionSpec(state);
    assertEq(result.ok, false);
    assert(result.errors.some((error) => error.code === "duplicate_atom_map"),
      "duplicate map numbers must be rejected: " + JSON.stringify(result.errors));
    assertEq(R.sandbox.canSubmitReactionSpec(state), false);
  });

  await test("rejects missing product in the reaction spec", async () => {
    const R = setupReaction(h);
    const state = makeState();
    R.sandbox.addReactionSpecParticipant(state, "reactant", {
      ref: "r1", charge: 0, multiplicity: 1, atomLabels: ["C1"], atomMap: [1],
    });
    const result = R.sandbox.validateReactionSpec(state);
    assertEq(result.ok, false);
    assert(result.errors.some((error) => error.code === "missing_product"),
      "missing product must be rejected: " + JSON.stringify(result.errors));
  });

  await test("rejects charge non-conservation in the reaction spec", async () => {
    const R = setupReaction(h);
    const state = makeState();
    R.sandbox.addReactionSpecParticipant(state, "reactant", {
      ref: "r1", charge: 0, multiplicity: 1, atomLabels: ["C1"], atomMap: [1],
    });
    R.sandbox.addReactionSpecParticipant(state, "product", {
      ref: "p1", charge: 1, multiplicity: 2, atomLabels: ["C1"], atomMap: [1],
    });
    const result = R.sandbox.validateReactionSpec(state);
    assertEq(result.ok, false);
    assert(result.errors.some((error) => error.code === "charge_non_conservation"),
      "charge non-conservation must be rejected: " + JSON.stringify(result.errors));
  });

  await test("net multi-step reaction as a single TS yields a clarification item, not a submit", async () => {
    const R = setupReaction(h);
    const state = makeState();
    addBasicReaction(R, state);
    R.sandbox.setReactionSpecConditions(state, {
      solvent: "water", temperature: "298.15 K", standardState: "1 atm",
    });
    R.sandbox.setReactionSpecKind(state, "net_reaction");
    R.sandbox.addReactionSpecCandidateStep(state, { step_id: "step-1", reactant_refs: ["r1"], product_refs: ["p1"] });
    R.sandbox.addReactionSpecCandidateStep(state, { step_id: "step-2", reactant_refs: ["r1"], product_refs: ["p1"] });
    R.sandbox.setReactionSpecSubmitAs(state, "single_ts");
    const result = R.sandbox.validateReactionSpec(state);
    assertEq(result.errors.length, 0, JSON.stringify(result.errors));
    assertEq(result.clarifications.length, 1, JSON.stringify(result.clarifications));
    const clarification = result.clarifications[0];
    assertEq(clarification.status, "open");
    assertEq(clarification.answer, null);
    assert(typeof clarification.clarification_id === "string" && clarification.clarification_id.length > 0);
    assert(typeof clarification.field_path === "string" && clarification.field_path.length > 0);
    assert(typeof clarification.prompt === "string" && clarification.prompt.length > 0);
    assert(Array.isArray(clarification.options) && clarification.options.length >= 1);
    assertEq(typeof clarification.revision, "number");
    assertEq(result.ok, false, "a clarification must block submit");
    assertEq(R.sandbox.canSubmitReactionSpec(state), false);
    const evaluated = R.sandbox.evaluateReactionSpecSubmit
      ? R.sandbox.evaluateReactionSpecSubmit(state)
      : null;
    if (evaluated) {
      assert(evaluated.action !== "submit", "must not hand back a submit action");
    }
  });

  await test("CDXML input is marked DEFERRED and never parsed as an empty structure", async () => {
    const R = setupReaction(h);
    const state = makeState();
    const classification = R.sandbox.classifyReactionInputFormat("cdxml");
    assertEq(classification.status, "DEFERRED");
    assertEq(classification.supported, false);
    const record = R.sandbox.adoptReactionSpecInput(state, {
      input_id: "cdxml-1", format: "cdxml", content: "<cdxml>unparseable</cdxml>",
    });
    assertEq(record.status, "DEFERRED");
    assertEq(record.structure, null, "a deferred input must not become a structure");
    assertEq(R.parseCalls.length, 0, "parseXyzAtoms must never see CDXML content");
    const stored = state.spec.inputFormats;
    assertEq(stored.length, 1);
    assert(stored[0].structure == null, "recorded deferred input carries no structure");

    const q = setupQuestion();
    const classified = q.Q.classifyInputFormat("cdxml");
    assertEq(classified.status, "DEFERRED");
    assertEq(classified.supported, false);
    q.Q.setInputFormat("cdxml");
    let rejected = false;
    await q.Q.previewStructure().catch(() => { rejected = true; });
    assert(rejected, "preview must refuse a DEFERRED format");
    assertEq(q.parseCalls.length, 0, "no parse call for CDXML");
    assertEq(q.submitCalls.length, 0);
    const qState = q.Q.getState();
    assertEq(qState.spec.inputFormatStatus, "DEFERRED");
    assertEq(qState.submitEnabled, false);
  });

  await test("P2 input range is frozen to MOL/SDF/SMILES plus structure upload", async () => {
    const R = setupReaction(h);
    const policy = R.sandbox.reactionInputFormatPolicy();
    assertEq(policy.accepted, ["mol", "sdf", "smiles", "structure_upload"]);
    assertEq(policy.deferred, ["cdxml"]);
    ["mol", "sdf", "smiles", "structure_upload"].forEach((format) => {
      const classification = R.sandbox.classifyReactionInputFormat(format);
      assertEq(classification.status, "accepted", format + " must be accepted");
      assertEq(classification.supported, true);
    });
    const unknown = R.sandbox.classifyReactionInputFormat("cdxml-batch");
    assertEq(unknown.status, "unsupported");
    assertEq(unknown.supported, false, "formats outside the frozen range are never claimed");
  });

  await test("atom labels, mapping and stereo are preserved across edits", async () => {
    const R = setupReaction(h);
    const state = makeState();
    addBasicReaction(R, state);
    const before = state.spec.products[0];
    const report = R.sandbox.applyReactionSpecEdit(state, {
      type: "set_charge", side: "product", index: 0, charge: 0,
    });
    assertEq(report.preserved.atomLabels, true);
    assertEq(report.preserved.atomMap, true);
    assertEq(report.preserved.stereo, true);
    const after = state.spec.products[0];
    assertEq(after.atomLabels, before.atomLabels, "labels survive an unrelated edit");
    assertEq(after.atomMap, before.atomMap, "mapping survives an unrelated edit");
    assertEq(after.stereo, before.stereo, "stereo survives an unrelated edit");
    const staleReport = R.sandbox.applyReactionSpecEdit(state, {
      type: "replace_structure", side: "product", index: 0, atomCount: 9,
    });
    assert(staleReport.stale.indexOf("atom_mapping") !== -1,
      "an atom-count change marks mapping stale instead of rewriting it");
    assertEq(after.atomLabels, before.atomLabels, "stale, never silently rewritten");
    assertEq(after.stereo, before.stereo);
    assertEq(state.spec.atomMapping.stale, true);
  });

  await test("charge/spin and solvent/temperature/standard state are visible", async () => {
    const R = setupReaction(h);
    const state = makeState();
    addBasicReaction(R, state);
    R.sandbox.setReactionSpecConditions(state, {
      solvent: "water", temperature: "298.15 K", standardState: "1 atm",
    });
    R.evalJs("reactionDefinition = " + JSON.stringify(state) + ";");
    R.sandbox.renderReactionSpecPanel();
    const html = R.specPanel().innerHTML;
    assert(html.indexOf("charge/spin") !== -1, "charge/spin block rendered");
    assert(html.indexOf("charge 0") !== -1, "reactant charge visible");
    assert(html.indexOf("spin 1") !== -1, "reactant spin visible");
    assert(html.indexOf("solvent/temperature/standard state") !== -1, "conditions block rendered");
    assert(html.indexOf("water") !== -1 && html.indexOf("298.15 K") !== -1 && html.indexOf("1 atm") !== -1,
      "solvent, temperature and standard state values visible");
    const view = R.sandbox.describeReactionSpec(state);
    assertEq(view.conditions.solvent, "water");
    assertEq(view.conditions.temperature, "298.15 K");
    assertEq(view.conditions.standardState, "1 atm");
    assertEq(view.chargeSpin.length, 2);
  });

  await test("human confirmations and approximations are recorded with the input revision", async () => {
    const R = setupReaction(h);
    const state = makeState();
    addBasicReaction(R, state);
    const revisionBefore = state.spec.inputRevision;
    R.sandbox.recordReactionSpecConfirmation(state, { item: "stereo", value: "R", note: "user confirmed" });
    R.sandbox.recordReactionSpecApproximation(state, { item: "temperature", approximation: "298.15 K", note: "standard" });
    const view = R.sandbox.describeReactionSpec(state);
    assertEq(view.humanConfirmations.length, 1);
    assertEq(view.humanConfirmations[0].item, "stereo");
    assertEq(view.humanConfirmations[0].value, "R");
    assert(view.humanConfirmations[0].inputRevision >= revisionBefore, "confirmation carries an input revision");
    assertEq(view.approximations.length, 1);
    assertEq(view.approximations[0].approximation, "298.15 K");
    assert(view.inputRevision > revisionBefore, "input revision advances with records");

    const q = setupQuestion();
    q.Q.recordHumanConfirmation({ item: "charge", value: "0" });
    q.Q.recordApproximation({ item: "solvent", approximation: "none" });
    const qState = q.Q.getState();
    assertEq(qState.spec.humanConfirmations.length, 1);
    assertEq(qState.spec.approximations.length, 1);
    assert(qState.spec.inputRevision >= 2, "question input revision tracked");
  });

  await test("net reaction vs elementary step distinction is shown", async () => {
    const R = setupReaction(h);
    const netState = makeState();
    addBasicReaction(R, netState);
    R.sandbox.setReactionSpecKind(netState, "net_reaction");
    const netView = R.sandbox.describeReactionSpec(netState);
    assertEq(netView.kind, "net_reaction");
    assert(netView.kindLabel.indexOf("net reaction") !== -1, "net label: " + netView.kindLabel);
    const stepState = makeState();
    addBasicReaction(R, stepState);
    R.sandbox.setReactionSpecKind(stepState, "elementary_step");
    const stepView = R.sandbox.describeReactionSpec(stepState);
    assertEq(stepView.kind, "elementary_step");
    assert(stepView.kindLabel.indexOf("elementary step") !== -1, "step label: " + stepView.kindLabel);
    assert(netView.kindLabel !== stepView.kindLabel, "the two kinds must read differently");
    R.evalJs("reactionDefinition = " + JSON.stringify(stepState) + ";");
    R.sandbox.renderReactionSpecPanel();
    assert(R.specPanel().innerHTML.indexOf("elementary step") !== -1, "distinction rendered in the panel");
  });

  await test("no silent identity fill: missing charge/spin/stereo stay unspecified", async () => {
    const R = setupReaction(h);
    const state = makeState();
    R.sandbox.addReactionSpecParticipant(state, "reactant", {
      ref: "r1", atomLabels: ["C1"], atomMap: [1],
    });
    R.sandbox.addReactionSpecParticipant(state, "product", {
      ref: "p1", atomLabels: ["C1"], atomMap: [1],
    });
    const participant = state.spec.reactants[0];
    assertEq(participant.charge, null, "absent charge must stay null");
    assertEq(participant.multiplicity, null, "absent spin must stay null");
    assertEq(participant.stereo, null, "absent stereo must stay null");
    const result = R.sandbox.validateReactionSpec(state);
    assert(result.errors.some((error) => error.code === "missing_identity"),
      "unspecified identity must surface as an error, never a default");
    const view = R.sandbox.describeReactionSpec(state);
    const line = R.sandbox.formatReactionSpecParticipantLine(view.reactants[0]);
    assert(line.indexOf("unspecified") !== -1, "unspecified identity is displayed as such: " + line);
  });

  await test("science-question submit is blocked while reaction clarifications are open", async () => {
    const q = setupQuestion();
    await q.Q.discover();
    q.Q.loadDemoStructure();
    await q.Q.runPreflight();
    assertEq(q.Q.canSubmit(), true, "fixture flow is submittable without clarifications");
    q.Q.setReactionSpecView({
      kind: "net_reaction",
      kindLabel: "净反应 (net reaction)",
      candidateSteps: [{ step_id: "step-1" }, { step_id: "step-2" }],
      atomMapping: { status: "mapped", stale: false, pairs: [] },
      chargeSpin: [{ ref: "r1", charge: 0, multiplicity: 1 }],
      stereo: [{ ref: "r1", stereo: null }],
      clarifications: [{
        clarification_id: "clar-net-multi-step-single-ts",
        field_path: "reaction.candidate_steps",
        prompt: "net reaction with 2 candidate elementary steps",
        status: "open",
        options: [{ value: "split_steps", label: "split" }],
        answer: null,
        revision: 0,
      }],
    });
    assertEq(q.Q.canSubmit(), false, "open clarification must block submit");
    assertEq(q.Q.openReactionClarifications().length, 1);
    let rejected = false;
    await q.Q.submit().catch(() => { rejected = true; });
    assert(rejected, "submit must refuse while a clarification is open");
    assertEq(q.submitCalls.length, 0, "zero submit adapter calls");
    const html = q.bodyHtml();
    assert(html.indexOf("net_reaction") !== -1 || html.indexOf("net reaction") !== -1,
      "bound reaction kind is shown in the question panel");
    assert(html.indexOf("DEFERRED") !== -1, "input policy shows CDXML DEFERRED");
    assert(html.indexOf("input revision") !== -1, "input revision is shown");
  });

  // ── T-24 / C17b: chemistry ambiguity confirmation and negative inputs ──

  const REPO = path.resolve(FRONTEND, "..");
  const CONTRACTS = path.join(REPO, "docs", "reference", "next-change", "acp2-contracts", "experience");
  const VIEW_MODEL_SCHEMA = path.join(CONTRACTS, "view-model.schema.json");
  const PROPOSAL_SCHEMA = path.join(CONTRACTS, "proposal.schema.json");

  function addFullySpecifiedBut(R, state, gaps) {
    const S = R.sandbox;
    const withGap = (entry, gapName) => Object.assign({}, entry, gaps[gapName] || {});
    S.addReactionSpecParticipant(state, "reactant", withGap({
      ref: "r1", charge: 0, multiplicity: 1, stereo: "E", atomLabels: ["C1"], atomMap: [1],
    }, "reactant"));
    S.addReactionSpecParticipant(state, "product", withGap({
      ref: "p1", charge: 0, multiplicity: 1, stereo: "R", atomLabels: ["C1"], atomMap: [1],
    }, "product"));
    if (!gaps.skipConditions) {
      S.setReactionSpecConditions(state, {
        solvent: "water", temperature: "298.15 K", standardState: "1 atm",
      });
    }
    return state;
  }

  await test("unconfirmed clarification blocks submit: missing_stereo must be explicitly confirmed", async () => {
    const R = setupReaction(h);
    const S = R.sandbox;
    const state = addFullySpecifiedBut(R, makeState(), { product: { stereo: null } });
    const result = S.validateReactionSpec(state);
    assertEq(result.errors.length, 0, JSON.stringify(result.errors));
    assertEq(result.clarifications.length, 1, JSON.stringify(result.clarifications));
    assertEq(result.clarifications[0].field_path, "missing_stereo");
    assertEq(result.clarifications[0].status, "open");
    assertEq(result.clarifications[0].answer, null);
    assertEq(result.ok, false);
    assertEq(S.canSubmitReactionSpec(state), false);
    assertEq(S.evaluateReactionSpecSubmit(state).action, "clarify");
    const q = setupQuestion();
    await q.Q.discover();
    q.Q.loadDemoStructure();
    await q.Q.runPreflight();
    q.Q.setReactionSpecView(S.describeReactionSpec(state));
    assertEq(q.Q.canSubmit(), false, "an unconfirmed clarification must block submit");
    let rejected = false;
    await q.Q.submit().catch(() => { rejected = true; });
    assert(rejected, "submit must refuse an unconfirmed clarification");
    assertEq(q.submitCalls.length, 0, "zero submit adapter calls while unconfirmed");
    const resolved = S.resolveReactionSpecClarification(state, {
      field_path: "missing_stereo", mode: "confirm", value: "confirm_no_stereo",
      note: "explicit user confirmation", editor_ref: "user-1",
    });
    assertEq(resolved.ok, true);
    assertEq(resolved.clarification.status, "answered");
    assertEq(resolved.clarification.answer, "confirm_no_stereo");
    assert(resolved.record.inputRevision >= 1, "the decision carries the input revision");
    assertEq(S.canSubmitReactionSpec(state), true);
    assertEq(S.evaluateReactionSpecSubmit(state).action, "submit");
    q.Q.setReactionSpecView(S.describeReactionSpec(state));
    assertEq(q.Q.canSubmit(), true, "an explicit confirmation unblocks submit");
  });

  await test("approximation hidden/omitted blocks submit: missing_conditions approximation must stay visible", async () => {
    const R = setupReaction(h);
    const S = R.sandbox;
    const state = addFullySpecifiedBut(R, makeState(), { skipConditions: true });
    const result = S.validateReactionSpec(state);
    assertEq(result.clarifications.length, 1, JSON.stringify(result.clarifications));
    assertEq(result.clarifications[0].field_path, "missing_conditions");
    const omitted = S.resolveReactionSpecClarification(state, {
      field_path: "missing_conditions", mode: "approximation",
    });
    assertEq(omitted.ok, false);
    assertEq(omitted.error, "approximation_required", "an omitted approximation is never accepted");
    assertEq(S.canSubmitReactionSpec(state), false, "an omitted approximation must not resolve");
    const resolved = S.resolveReactionSpecClarification(state, {
      field_path: "missing_conditions", mode: "approximation",
      approximation: "298.15 K, 1 atm, gas phase", note: "standard ambient", editor_ref: "user-1",
    });
    assertEq(resolved.ok, true);
    assertEq(resolved.clarification.status, "waived");
    const view = S.describeReactionSpec(state);
    assertEq(view.approximations.length, 1);
    assertEq(view.approximations[0].item, "missing_conditions");
    assert(view.approximations[0].approximation.indexOf("298.15") !== -1, "the approximation is visible");
    assertEq(S.canSubmitReactionSpec(state), true, "a visible approximation resolves the item");
    state.spec.approximations = [];
    const afterHide = S.validateReactionSpec(state);
    assertEq(afterHide.clarifications[0].status, "open", "a hidden approximation reopens the item");
    assertEq(S.canSubmitReactionSpec(state), false, "hidden approximations never unblock submit");
    const q = setupQuestion();
    await q.Q.discover();
    q.Q.loadDemoStructure();
    await q.Q.runPreflight();
    const hiddenView = {
      kind: "net_reaction", kindLabel: "净反应 (net reaction)", candidateSteps: [],
      atomMapping: { status: "mapped", stale: false, pairs: [] },
      chargeSpin: [{ ref: "r1", charge: 0, multiplicity: 1 }],
      stereo: [{ ref: "r1", stereo: "E" }],
      humanConfirmations: [],
      approximations: [],
      parseErrors: [],
      clarifications: [{
        clarification_id: "clar-missing-conditions",
        field_path: "missing_conditions",
        prompt: "missing conditions",
        status: "waived",
        options: [{ value: "confirm_conditions", label: "confirm" }, { value: "approximate", label: "approximate" }],
        answer: "approximate",
        revision: 3,
      }],
    };
    q.Q.setReactionSpecView(hiddenView);
    assertEq(q.Q.openReactionClarifications().length, 1, "a hidden approximation is treated as open");
    assertEq(q.Q.canSubmit(), false);
    let rejected = false;
    await q.Q.submit().catch(() => { rejected = true; });
    assert(rejected, "submit must refuse a hidden approximation");
    assertEq(q.submitCalls.length, 0);
    hiddenView.approximations = [{
      item: "missing_conditions", approximation: "298.15 K, 1 atm, gas phase", note: "", inputRevision: 3,
    }];
    q.Q.setReactionSpecView(hiddenView);
    assertEq(q.Q.openReactionClarifications().length, 0);
    assertEq(q.Q.canSubmit(), true, "a visible approximation unblocks submit");
  });

  await test("CDXML parse error shows error and no submit", async () => {
    const R = setupReaction(h);
    const S = R.sandbox;
    const state = makeState();
    const record = S.adoptReactionSpecInput(state, {
      input_id: "cdxml-1", format: "cdxml", content: "<cdxml>broken</cdxml>",
      parseResult: { ok: false, errors: ["cdxml parse failure"], structures: [] },
    });
    assertEq(record.status, "ERROR");
    assertEq(record.error, "cdxml parse failure");
    assertEq(record.structure, null, "a parse failure must never degrade to an empty structure");
    assertEq(R.parseCalls.length, 0, "CDXML content never reaches parseXyzAtoms");
    const result = S.validateReactionSpec(state);
    assert(result.errors.some((error) => error.code === "parse_error"),
      "the parse failure surfaces as an error: " + JSON.stringify(result.errors));
    assertEq(S.evaluateReactionSpecSubmit(state).action, "reject");
    assertEq(S.canSubmitReactionSpec(state), false);
    const q = setupQuestion();
    q.Q.init({
      wizardEndpoints: {
        parseStructure: () => Promise.resolve({
          ok: false, detected_format: "cdxml", errors: ["cdxml parse failure"], warnings: [], structures: [],
        }),
      },
    });
    await q.Q.discover();
    q.Q.loadDemoStructure();
    const preview = await q.Q.previewStructure();
    assertEq(preview.ok, false);
    assertEq(preview.atomCount, null, "no empty structure is created from a failed parse");
    const qState = q.Q.getState();
    assertEq(qState.spec.parseError.message, "cdxml parse failure");
    assert(q.bodyHtml().indexOf("cdxml parse failure") !== -1, "the parse error is shown");
    await q.Q.runPreflight();
    assertEq(q.Q.canSubmit(), false, "a parse error blocks submit");
    let rejected = false;
    await q.Q.submit().catch(() => { rejected = true; });
    assert(rejected, "submit must refuse after a parse error");
    assertEq(q.submitCalls.length, 0, "no submit after a CDXML parse error");
  });

  await test("mapping ambiguity (cross-side pairing) is surfaced: mapping_ambiguity", async () => {
    const R = setupReaction(h);
    const S = R.sandbox;
    const state = addFullySpecifiedBut(R, makeState(), {});
    S.applyReactionSpecEdit(state, { type: "set_mapping", pairs: [[0, 0], [0, 1], [1, 1]], status: "mapped" });
    const result = S.validateReactionSpec(state);
    assertEq(result.errors.length, 0, JSON.stringify(result.errors));
    assertEq(result.clarifications.length, 1, JSON.stringify(result.clarifications));
    assertEq(result.clarifications[0].field_path, "mapping_ambiguity");
    assert(result.clarifications[0].prompt.indexOf("cross-side pairing conflict") !== -1,
      result.clarifications[0].prompt);
    assertEq(result.clarifications[0].status, "open");
    assertEq(result.ok, false, "an unresolved mapping ambiguity blocks submit");
    assertEq(S.evaluateReactionSpecSubmit(state).action, "clarify");
    const staleState = addFullySpecifiedBut(R, makeState(), {});
    S.applyReactionSpecEdit(staleState, { type: "replace_structure", side: "product", index: 0, atomCount: 7 });
    const staleResult = S.validateReactionSpec(staleState);
    assert(staleResult.clarifications.some((item) => item.field_path === "mapping_ambiguity"),
      "a stale mapping is ambiguous too: " + JSON.stringify(staleResult.clarifications));
  });

  await test("unknown multiplicity requires explicit input: unknown_multiplicity", async () => {
    const R = setupReaction(h);
    const S = R.sandbox;
    const state = addFullySpecifiedBut(R, makeState(), { reactant: { multiplicity: null } });
    const result = S.validateReactionSpec(state);
    assertEq(result.clarifications.length, 1, JSON.stringify(result.clarifications));
    assertEq(result.clarifications[0].field_path, "unknown_multiplicity");
    const empty = S.resolveReactionSpecClarification(state, {
      field_path: "unknown_multiplicity", mode: "confirm",
    });
    assertEq(empty.ok, false);
    assertEq(empty.error, "explicit_input_required", "unknown multiplicity demands explicit input");
    assertEq(state.spec.reactants[0].multiplicity, null, "no auto-fill of the unknown multiplicity");
    assertEq(S.canSubmitReactionSpec(state), false);
    const resolved = S.resolveReactionSpecClarification(state, {
      field_path: "unknown_multiplicity", mode: "confirm", value: "1", note: "user supplied singlet",
    });
    assertEq(resolved.ok, true);
    assertEq(resolved.record.value, "1");
    assertEq(resolved.clarification.answer, "provide_multiplicity");
    assertEq(state.spec.reactants[0].multiplicity, null,
      "the participant identity stays untouched until an explicit edit");
    assertEq(S.canSubmitReactionSpec(state), true);
  });

  await test("net multi-step prompt cannot be bypassed: reaction.candidate_steps", async () => {
    const R = setupReaction(h);
    const S = R.sandbox;
    const state = addFullySpecifiedBut(R, makeState(), {});
    S.addReactionSpecCandidateStep(state, { step_id: "step-1", reactant_refs: ["r1"], product_refs: ["p1"] });
    S.addReactionSpecCandidateStep(state, { step_id: "step-2", reactant_refs: ["r1"], product_refs: ["p1"] });
    S.setReactionSpecSubmitAs(state, "split_steps");
    let result = S.validateReactionSpec(state);
    assertEq(result.clarifications.length, 1, JSON.stringify(result.clarifications));
    assertEq(result.clarifications[0].field_path, "reaction.candidate_steps");
    assertEq(result.clarifications[0].status, "open", "flipping submitAs cannot bypass the prompt");
    assertEq(S.evaluateReactionSpecSubmit(state).action, "clarify");
    assertEq(S.evaluateReactionSpecSubmit(state, { submitAs: "split_steps" }).action, "clarify",
      "an options.submitAs override cannot bypass the prompt");
    assertEq(S.canSubmitReactionSpec(state, { submitAs: "multi_ts" }), false);
    const smuggled = S.resolveReactionSpecClarification(state, {
      field_path: "reaction.candidate_steps", mode: "confirm", value: "confirm_single_ts",
    });
    assertEq(smuggled.ok, false);
    assertEq(smuggled.error, "approximation_required", "a single-TS approximation cannot pass as a confirmation");
    assertEq(S.validateReactionSpec(state).clarifications[0].status, "open");
    const q = setupQuestion();
    await q.Q.discover();
    q.Q.loadDemoStructure();
    await q.Q.runPreflight();
    q.Q.setReactionSpecView(S.describeReactionSpec(state));
    let rejected = false;
    await q.Q.submit().catch(() => { rejected = true; });
    assert(rejected, "the net multi-step prompt cannot be bypassed");
    assertEq(q.submitCalls.length, 0);
    const resolved = S.resolveReactionSpecClarification(state, {
      field_path: "reaction.candidate_steps", mode: "approximation",
      approximation: "single_ts", note: "user declared single-TS approximation",
    });
    assertEq(resolved.ok, true);
    assertEq(resolved.clarification.status, "waived");
    assertEq(resolved.clarification.answer, "confirm_single_ts");
    assertEq(S.canSubmitReactionSpec(state), true);
    S.addReactionSpecCandidateStep(state, { step_id: "step-3", reactant_refs: ["r1"], product_refs: ["p1"] });
    const afterEdit = S.validateReactionSpec(state);
    assertEq(afterEdit.clarifications[0].status, "open", "a stale answer is rejected after the input changes");
    assertEq(S.canSubmitReactionSpec(state), false);
  });

  await test("clarification item field names match view-model.schema.json", async () => {
    const schema = JSON.parse(fs.readFileSync(VIEW_MODEL_SCHEMA, "utf8"));
    const itemSchema = schema.properties.clarifications.items;
    const expectedKeys = Object.keys(itemSchema.properties).sort();
    assertEq(itemSchema.required.slice().sort(), expectedKeys,
      "every view-model clarification field is required");
    const R = setupReaction(h);
    const S = R.sandbox;
    assertEq(S.reactionSpecClarificationItemFields().slice().sort(), expectedKeys);
    assertEq(S.reactionSpecClarificationFields().slice().sort(),
      ["mapping_ambiguity", "missing_conditions", "missing_stereo", "reaction.candidate_steps", "unknown_multiplicity"]);
    const state = makeState();
    S.addReactionSpecParticipant(state, "reactant", {
      ref: "r1", charge: 0, multiplicity: null, stereo: null, atomLabels: ["C1"], atomMap: [1],
    });
    S.addReactionSpecParticipant(state, "product", {
      ref: "p1", charge: 0, multiplicity: null, stereo: null, atomLabels: ["C1"], atomMap: [1],
    });
    S.addReactionSpecCandidateStep(state, { step_id: "step-1", reactant_refs: ["r1"], product_refs: ["p1"] });
    S.addReactionSpecCandidateStep(state, { step_id: "step-2", reactant_refs: ["r1"], product_refs: ["p1"] });
    S.applyReactionSpecEdit(state, { type: "set_mapping", pairs: [[0, 0], [0, 1]], status: "mapped" });
    const result = S.validateReactionSpec(state);
    assertEq(result.clarifications.map((item) => item.field_path).sort(),
      ["mapping_ambiguity", "missing_conditions", "missing_stereo", "reaction.candidate_steps", "unknown_multiplicity"],
      "the full clarification matrix is generated: " + JSON.stringify(result.clarifications));
    result.clarifications.forEach((item) => {
      assertEq(Object.keys(item).sort(), expectedKeys,
        "clarification fields must match view-model exactly: " + item.field_path);
      item.options.forEach((option) => {
        assertEq(Object.keys(option).sort(), ["label", "value"]);
      });
      assert(["open", "answered", "waived"].indexOf(item.status) !== -1, "status enum: " + item.status);
      assertEq(item.revision, 0);
    });
  });

  await test("proposal payload writes confirmations, approximations and the input revision with frozen proposal.schema.json field names", async () => {
    const schema = JSON.parse(fs.readFileSync(PROPOSAL_SCHEMA, "utf8"));
    const proposalFields = Object.keys(schema.properties).sort();
    const humanEditFields = Object.keys(schema.$defs.human_edit.properties).sort();
    const R = setupReaction(h);
    const S = R.sandbox;
    assertEq(S.reactionSpecProposalFields().slice().sort(), proposalFields);
    assertEq(S.reactionSpecProposalHumanEditFields().slice().sort(), humanEditFields);
    const state = addFullySpecifiedBut(R, makeState(), {
      product: { stereo: null }, skipConditions: true,
    });
    S.resolveReactionSpecClarification(state, {
      field_path: "missing_stereo", mode: "confirm", value: "confirm_no_stereo",
      note: "no stereo centers", editor_ref: "user-1",
    });
    S.resolveReactionSpecClarification(state, {
      field_path: "missing_conditions", mode: "approximation",
      approximation: "298.15 K, 1 atm", note: "standard ambient", editor_ref: "user-1",
    });
    const revisionAfterDecisions = state.spec.inputRevision;
    const payload = S.buildReactionSpecProposalInput(state.spec, {
      proposal_id: "prop-1",
      project_id: "proj-1",
      question_ref: "question-1",
      question_revision: 2,
      reaction_ref: "reaction-1",
      hypothesis: "the cycloaddition proceeds via a single TS",
      recipe_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      plan_definition_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      science: {},
      budget: {},
      approval_ref: null,
      allowed_change_scope: { mode: "none", finite_substitutions: [] },
      round: 0,
      decision: "proposed",
      created_at: "2026-10-23T00:00:00Z",
      human_edits: [],
    });
    schema.required.forEach((field) => {
      assert(Object.prototype.hasOwnProperty.call(payload, field), "missing required proposal field: " + field);
    });
    Object.keys(payload).forEach((key) => {
      assert(proposalFields.indexOf(key) !== -1, "parallel proposal shape key: " + key);
    });
    assertEq(payload.question_revision, 2);
    assertEq(payload.reaction_revision, revisionAfterDecisions, "the input revision is written into the proposal");
    assertEq(payload.human_edits.length, 2);
    payload.human_edits.forEach((edit) => {
      assertEq(Object.keys(edit).sort(), humanEditFields);
      assertEq(edit.editor_ref, "user-1");
    });
    const confirmEdit = payload.human_edits.filter((edit) => edit.field_path === "missing_stereo")[0];
    assert(confirmEdit.note.indexOf("confirmed missing_stereo=confirm_no_stereo (input revision ") !== -1,
      confirmEdit.note);
    const approxEdit = payload.human_edits.filter((edit) => edit.field_path === "missing_conditions")[0];
    assert(approxEdit.note.indexOf("approximation missing_conditions=298.15 K, 1 atm (input revision ") !== -1,
      approxEdit.note);
  });

  await test("a parallel proposal shape is rejected", async () => {
    const R = setupReaction(h);
    const S = R.sandbox;
    let threw = false;
    try {
      S.buildReactionSpecProposalInput({ inputRevision: 1, humanConfirmations: [], approximations: [] }, {
        proposal_id: "prop-1",
        confirmations: [],
        approximations: [],
      });
    } catch (error) {
      threw = true;
      assert(String(error.message).indexOf("proposal.schema.json") !== -1, String(error.message));
    }
    assert(threw, "a parallel proposal shape must be rejected");
    const q = setupQuestion();
    let failClosed = false;
    try {
      q.Q.proposalInput({ proposal_id: "prop-1" });
    } catch (error) {
      failClosed = true;
    }
    assert(failClosed, "the question side fails closed without the single frozen-shape builder");
    q.sandbox.buildReactionSpecProposalInput = S.buildReactionSpecProposalInput;
    q.Q.recordHumanConfirmation({ item: "charge", value: "0", note: "explicit", editor_ref: "user-1" });
    const payload = q.Q.proposalInput({
      proposal_id: "prop-2",
      project_id: "proj-1",
      question_ref: "question-1",
      reaction_ref: "reaction-1",
      hypothesis: "h",
      recipe_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      plan_definition_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      science: {},
      budget: {},
      approval_ref: null,
      allowed_change_scope: { mode: "none", finite_substitutions: [] },
      round: 0,
      decision: "proposed",
      created_at: "2026-10-23T00:00:00Z",
      human_edits: [],
    });
    assertEq(payload.question_revision, q.Q.getState().spec.inputRevision);
    assertEq(payload.human_edits.length, 1);
    assertEq(payload.human_edits[0].field_path, "charge");
  });
};
