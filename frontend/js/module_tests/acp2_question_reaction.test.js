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
};
