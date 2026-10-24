/* T-31 / C21: scientific evidence view — claim states and lifecycle.
 *
 * Runs the REAL `workbench/features/scientific-evidence.js` and the frozen
 * `contracts/fixtures/evidence-bundle.js` in the zero-dependency vm harness.
 *
 * Pinned contracts (plan C21 naming matrix):
 *   1. the four claim states render distinctly (supported/refuted/
 *      inconclusive/not_evaluated);
 *   2. `inconclusive` shows the visible "live not verified" state and is
 *      never success-colored (no supported/green class on its badge);
 *   3. execution status is shown NEXT TO the claim state and never merged
 *      into one verdict (COMPLETED execution + inconclusive claim coexist);
 *   4. expanding a claim shows raw artifact refs, validator, method/version,
 *      missing validation and the RPH decision source;
 *   5. dispose() then tick() fires zero callbacks (and dispose is idempotent);
 *   6. no `fetch(` in the feature or the visualization modules;
 *   7. the energy chart exposes ONLY save-as-candidate (never submit);
 *   8. switching tasks clears the old claim state;
 *   9. hide() pauses ticking and show() resumes; repeated mount is safe;
 *  10. a fifth claim state is refused, never rendered;
 *  11. no ORCA output parsing and no thermochemistry computation in the page;
 *  12. live reads go through ACPClient only.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FRONTEND = path.resolve(__dirname, "..", "..");
const FEATURE_REL = "js/workbench/features/scientific-evidence.js";
const FIXTURE_REL = "js/workbench/contracts/fixtures/evidence-bundle.js";
const VIZ_DIR = path.join(FRONTEND, "js", "visualization");

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

function setup(h, options) {
  const dom = makeDom();
  const sandbox = h.makeSandbox({ document: dom, console: { log() {}, warn() {}, error() {} } });
  h.loadScript(FEATURE_REL, sandbox);
  const mod = sandbox.window.ACPScientificEvidence;
  const body = dom.getElementById("scientific-evidence-body");
  mod.mount(dom, options || {});
  return { sandbox, dom, mod, body };
}

function fixtureBundle(h) {
  const sandbox = h.makeSandbox({});
  h.loadScript(FIXTURE_REL, sandbox);
  const fixture = sandbox.window.ACP_EVIDENCE_BUNDLE_FIXTURE;
  if (!fixture || typeof fixture !== "object") throw new Error("fixture missing");
  if (!fixture.evidence_report || !fixture.view) throw new Error("fixture incomplete");
  return fixture;
}

function detailBlock(body, claimId) {
  const html = String(body.innerHTML || "");
  const marker = `data-se-detail="${claimId}"`;
  const at = html.indexOf(marker);
  if (at === -1) return "";
  const rest = html.slice(at);
  const end = rest.indexOf('data-se-detail="', marker.length);
  return end === -1 ? rest : rest.slice(0, end);
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  await test("claims render the four states distinctly", () => {
    const { mod, body } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    const html = String(body.innerHTML);
    ["supported", "refuted", "inconclusive", "not_evaluated"].forEach((status) => {
      assert(html.indexOf(`data-claim-status="${status}"`) !== -1, `row for ${status}`);
      assert(html.indexOf(`se-state-${status}`) !== -1, `badge class for ${status}`);
      assert(html.indexOf(`data-claim-state-badge="${status}"`) !== -1, `state badge for ${status}`);
    });
    assertEq(mod.getState().claimStates.slice().sort(),
      ["inconclusive", "not_evaluated", "refuted", "supported"], "four distinct states");
  });

  await test("mount adopts the injected frozen fixture with the demo badge", () => {
    const dom = makeDom();
    const sandbox = h.makeSandbox({ document: dom, console: { log() {}, warn() {}, error() {} } });
    h.loadScript(FIXTURE_REL, sandbox);
    h.loadScript(FEATURE_REL, sandbox);
    const mod = sandbox.window.ACPScientificEvidence;
    const body = dom.getElementById("scientific-evidence-body");
    mod.mount(dom, {});
    const html = String(body.innerHTML);
    assert(html.indexOf("演示数据") !== -1, "fixture run carries the demo badge");
    assert(html.indexOf('data-se-evidence-kind="fixture"') !== -1, "evidence kind fixture");
    assertEq(mod.getState().fixture, true);
    assertEq(mod.getState().claims.length, 4);
  });

  await test("inconclusive is not success-colored and shows the live-not-verified marker", () => {
    const { mod, body } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    const html = String(body.innerHTML);
    const row = html.slice(html.indexOf('data-claim-status="inconclusive"'));
    assert(row.indexOf("se-state-inconclusive") !== -1, "inconclusive badge class present");
    assert(row.indexOf("se-state-supported") === -1, "inconclusive row never wears the supported/success class");
    assert(row.indexOf("se-state-refuted") === -1, "inconclusive row never wears the refuted class");
    assert(row.indexOf(mod.LIVE_UNVERIFIED_TEXT) !== -1, "visible live not verified marker");
    assert(html.indexOf('data-se-live-unverified="1"') !== -1, "marker is machine-visible");
  });

  await test("execution status is shown next to the claim state, never conflated", () => {
    const { mod, body } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    const html = String(body.innerHTML);
    assert(html.indexOf('data-exec-state="completed"') !== -1, "execution badge present");
    assert(html.indexOf("se-exec") !== -1, "execution badge class distinct from claim state class");
    const inconclusiveRow = html.slice(html.indexOf('data-claim-status="inconclusive"'));
    const rowEnd = inconclusiveRow.indexOf("</li>");
    const row = inconclusiveRow.slice(0, rowEnd === -1 ? 2000 : rowEnd);
    assert(row.indexOf('data-exec-state="completed"') !== -1, "COMPLETED execution on the row");
    assert(row.indexOf('data-claim-state-badge="inconclusive"') !== -1,
      "claim stays inconclusive while execution is COMPLETED");
    assertEq(mod.getState().executionState, "completed");
  });

  await test("expand shows raw artifact refs, validator, method/version, missing validation and RPH decision source", () => {
    const { mod, body } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    assert(mod.toggleClaim("claim_ts_mode") === true, "expands");
    const detail = detailBlock(body, "claim_ts_mode");
    assert(detail.indexOf('data-se-detail-block="artifacts"') !== -1, "artifacts block");
    assert(detail.indexOf('data-se-artifact-path="work/freq_orca/freq_output.log"') !== -1,
      "raw artifact ref path");
    assert(detail.indexOf('data-se-detail-block="validator"') !== -1, "validator block");
    assert(detail.indexOf('data-se-validator-id="check.freq.imaginary_mode_count"') !== -1,
      "validator id");
    assert(detail.indexOf("1.2.0") !== -1, "validator version");
    assert(detail.indexOf('data-se-detail-block="method"') !== -1, "method block");
    assert(detail.indexOf('data-se-method-field="method"') !== -1, "method field");
    assert(detail.indexOf("r2SCAN-3c") !== -1, "method id");
    assert(detail.indexOf("6.0.1") !== -1, "software version");
    assert(detail.indexOf('data-se-detail-block="decision"') !== -1, "decision block");
    assert(detail.indexOf('data-se-decision-source="research_record:res-fixture-da-0001@record_revision=3"') !== -1,
      "RPH decision source");
  });

  await test("expand shows missing validation with blocking flags", () => {
    const { mod, body } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    mod.toggleClaim("claim_mechanism_pathway");
    const detail = detailBlock(body, "claim_mechanism_pathway");
    assert(detail.indexOf('data-se-detail-block="missing"') !== -1, "missing block");
    assert(detail.indexOf('data-se-check-id="missing_irc"') !== -1, "missing_irc check");
    assert(detail.indexOf('data-se-check-blocking="1"') !== -1, "blocking flag visible");
    assert(detail.indexOf('data-se-check-id="standard_state_unspecified"') !== -1, "second check");
    assert(detail.indexOf("blocking") !== -1, "blocking label rendered");
  });

  await test("expand with no validator never claims a pass", () => {
    const { mod, body } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    mod.toggleClaim("claim_free_energy_barrier");
    const detail = detailBlock(body, "claim_free_energy_barrier");
    assert(detail.indexOf('data-se-validator-id="none"') !== -1, "explicit no-validator marker");
    assert(detail.indexOf("不得视为通过") !== -1, "no-validator is not a pass");
  });

  await test("dispose then tick fires no callback; dispose is idempotent", () => {
    const { mod } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    const token = mod.registerTick(() => {});
    assert(token !== null, "tick registered while live");
    assert(mod.tick() === 1, "tick runs the callback before dispose");
    const fired = mod.getState().callbacksFired;
    mod.dispose();
    assertEq(mod.getState().disposed, true);
    assertEq(mod.tick(), 0, "no tick after dispose");
    mod.registerTick(() => { throw new Error("late callback must not run"); });
    assertEq(mod.tick(), 0, "late registration is refused");
    mod.dispose();
    assertEq(mod.getState().callbacksFired, fired, "callback counter frozen after dispose");
  });

  await test("scene instances are disposed with the feature", () => {
    const scenes = [];
    const { mod, body } = setup(h, {
      sceneFactory: () => {
        const rec = {
          models: 0, disposed: false, hidden: null,
          addModel() { this.models += 1; return { setStyle() {} }; },
          removeModel() { return true; },
          setHidden(v) { this.hidden = v; },
          pausePlayback() {}, resumePlayback() {},
          dispose() { this.disposed = true; },
        };
        scenes.push(rec);
        return rec;
      },
    });
    body.querySelector = (selector) => (String(selector).indexOf("scene-host") !== -1 ? makeEl("host") : null);
    mod.setEvidence(fixtureBundle(h));
    assert(scenes.length >= 1, "scene created through the visualization seam");
    assertEq(mod.getState().sceneCount, scenes.length);
    mod.dispose();
    assert(scenes.every((scene) => scene.disposed === true), "every scene disposed");
  });

  await test("hide pauses ticking and show resumes; repeated mount is safe", () => {
    const { mod, dom } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    mod.registerTick(() => {});
    assert(mod.tick() === 1, "ticks while visible");
    mod.hide();
    assertEq(mod.getState().hidden, true);
    assertEq(mod.tick(), 0, "no tick while hidden");
    mod.show();
    assertEq(mod.tick(), 1, "ticks again after show");
    const before = mod.getState().framesRendered;
    mod.mount(dom, {});
    assert(mod.getState().framesRendered > before, "re-mount re-renders");
    assertEq(mod.getState().disposed, false);
  });

  await test("switching tasks clears the old claim state", () => {
    const { mod, body } = setup(h);
    mod.setEvidence(fixtureBundle(h));
    mod.toggleClaim("claim_ts_mode");
    assertEq(mod.getState().claims.length, 4);
    mod.switchTask({ job_id: "other_job", attempt: 2 });
    const state = mod.getState();
    assertEq(state.claims.length, 0, "old claims cleared");
    assertEq(state.claimStates, [], "no stale claim states");
    assertEq(Object.keys(state.expanded).length, 0, "expansion state cleared");
    assertEq(state.taskRef.job_id, "other_job");
    assert(String(body.innerHTML).indexOf('data-se-empty="1"') !== -1, "empty state rendered");
  });

  await test("a fifth claim state is refused, never rendered", () => {
    const { mod, body } = setup(h);
    const fixture = fixtureBundle(h);
    fixture.evidence_report.claims[0].status = "confirmed";
    mod.setEvidence(fixture);
    assertEq(mod.getState().status, "claim_state_refused");
    assertEq(mod.getState().claims.length, 0, "no claims adopted");
    assert(String(body.innerHTML).indexOf('data-claim-status="confirmed"') === -1, "fifth state absent");
  });

  await test("energy chart exposes only save-as-candidate (no submit)", () => {
    const saved = [];
    const { mod, body } = setup(h, { onSaveCandidate: (record) => saved.push(record) });
    mod.setEvidence(fixtureBundle(h));
    assertEq(mod.ENERGY_ACTIONS, ["save-candidate"], "frozen action list");
    assertEq(mod.getState().energyActions, ["save-candidate"], "state exposes one action");
    const html = String(body.innerHTML);
    assert(html.indexOf('data-se-action="save-candidate"') !== -1, "save-as-candidate button");
    assert(html.indexOf('data-se-action="submit"') === -1, "no submit action");
    assert(html.indexOf("energyGraphConfirmAndBatch") === -1, "forbidden identifier absent");
    assert(typeof mod.submit === "undefined", "feature exports no submit");
    const record = mod.saveEnergyCandidate({ frame_index: 2, role: "ts" });
    assert(record !== null, "candidate recorded");
    assertEq(saved.length, 1, "only the save-as-candidate callback ran");
    assertEq(mod.getState().savedCandidates.length, 1);
  });

  await test("no fetch( in the feature or visualization modules", () => {
    const featureSource = fs.readFileSync(path.join(FRONTEND, FEATURE_REL), "utf8");
    assert(featureSource.indexOf("fetch(") === -1, "feature owns no fetch");
    assert(featureSource.indexOf("XMLHttpRequest") === -1, "feature owns no XHR");
    for (const name of fs.readdirSync(VIZ_DIR).filter((f) => f.endsWith(".js"))) {
      const source = fs.readFileSync(path.join(VIZ_DIR, name), "utf8");
      assert(source.indexOf("fetch(") === -1, `${name} owns no fetch`);
    }
  });

  await test("no ORCA output parsing and no thermochemistry computation in the feature", () => {
    const source = fs.readFileSync(path.join(FRONTEND, FEATURE_REL), "utf8");
    ["SCF CONVERGED", "TOTAL SCF ENERGY", "THERMOCHEMISTRY", "627.5", "627.509",
      "HARTREE_TO_KCAL", "computeThermo", "calcGibbs", "parseFloat(line"].forEach((needle) => {
      assert(source.indexOf(needle) === -1, `forbidden page-side computation: ${needle}`);
    });
  });

  await test("live reads go through the real ACPClient namespace (science view + research round)", async () => {
    const calls = { view: 0, round: 0 };
    const dom = makeDom();
    const sandbox = h.makeSandbox({ document: dom, console: { log() {}, warn() {}, error() {} } });
    h.loadScript("js/client/resource-store.js", sandbox);
    h.loadScript("js/workbench/contracts/experience_contract.js", sandbox);
    h.loadScript("js/client/science-query.js", sandbox);
    h.loadScript("js/client/research-query.js", sandbox);
    h.loadScript(FEATURE_REL, sandbox);
    const C = sandbox.window.ACPClient;
    assert(C && C.science && C.research, "real ACPClient.science/research namespaces are loaded");
    assertEq(C.research.readOnly, true, "real research client is read-only");
    const viewBody = {
      snapshot_revision: 9,
      project_id: "p1",
      task_ref: { job_id: "live_job", attempt: 1 },
      manifest_generation: 3,
      artifacts: [],
      claims: [{
        claim_id: "claim_live", statement: "live claim", status: "supported",
        evidence_refs: ["work/a.log"], reason: null,
      }],
      clarifications: [],
      provenance: {
        source: "projection", producer_revision: "a".repeat(40),
        produced_at: "2026-10-23T00:00:00Z", digest: "sha256:" + "0".repeat(64),
      },
      read_only: true,
      not_authoritative_for_science: true,
    };
    const recordBody = {
      research_id: "res-live-1",
      project_id: "p1",
      round: 1,
      decision: "accept",
      plan_definition_digest: "sha256:" + "1".repeat(64),
      step_identity_refs: [],
      task_refs: [{
        project_id: "p1", job_id: "live_job", attempt: 1, release_id: "release-1",
      }],
      evidence_refs: [],
      missing_validation: [],
      human_edits: [],
      approval_ref: "apr-1",
      created_at: "2026-10-23T00:00:00Z",
      record_revision: 2,
    };
    C.science.init({
      v2: (path) => {
        calls.view += 1;
        assert(path.indexOf("/research/") === -1, "science adapter never reads research paths");
        return Promise.resolve(JSON.parse(JSON.stringify(viewBody)));
      },
    });
    C.research.init({
      v2: (path) => {
        calls.round += 1;
        assert(path.indexOf("/research/records/") !== -1, "research adapter reads record paths");
        return Promise.resolve(JSON.parse(JSON.stringify(recordBody)));
      },
    });
    const mod = sandbox.window.ACPScientificEvidence;
    const body = dom.getElementById("scientific-evidence-body");
    mod.mount(dom, {});
    await mod.loadViaClient(
      { view_ref: { jobId: "live_job", attempt: 1 }, record_ref: { id: "res-live-1", revision: 2 } },
      { projectId: "p1", permission: "read" }
    );
    assertEq(calls.view, 1, "view read through the real ACPClient.science adapter");
    assertEq(calls.round, 1, "RPH decision read through the real ACPClient.research adapter");
    const html = String(body.innerHTML);
    assert(html.indexOf('data-claim-status="supported"') !== -1, "live claim rendered");
    assert(html.indexOf("res-live-1") !== -1, "decision source rendered");
  });

  await test("workbench page loads js/client/research-query.js before bootstrap and inits it", () => {
    const html = fs.readFileSync(path.join(FRONTEND, "ACP_Workbench_v2.html"), "utf8");
    const researchAt = html.indexOf('src="js/client/research-query.js"');
    const bootstrapAt = html.indexOf('src="js/workbench/bootstrap.js"');
    assert(researchAt !== -1, "research-query.js is loaded by the workbench page");
    assert(bootstrapAt !== -1, "bootstrap.js is loaded by the workbench page");
    assert(researchAt < bootstrapAt, "research-query.js loads before bootstrap.js");
    const apiSource = fs.readFileSync(
      path.join(FRONTEND, "js", "workbench", "compat", "api.js"), "utf8");
    assert(/ACPClient\.research\.init\(\{\s*v2:\s*apiV2\s*\}\)/.test(apiSource),
      "compat/api.js initializes ACPClient.research with the v2 adapter");
  });

  await test("save-candidate confirms visibly and persists the candidate", () => {
    const written = [];
    const persistence = {
      read: () => null,
      write: (key, value) => written.push({ key, value: JSON.parse(JSON.stringify(value)) }),
    };
    const dom = makeDom();
    const sandbox = h.makeSandbox({ document: dom, console: { log() {}, warn() {}, error() {} } });
    h.loadScript(FEATURE_REL, sandbox);
    const mod = sandbox.window.ACPScientificEvidence;
    mod.mount(dom, { persistence });
    mod.setEvidence(fixtureBundle(h));
    const beforeHtml = String(dom.getElementById("scientific-evidence-body").innerHTML);
    assert(beforeHtml.indexOf("data-se-save-confirm") === -1, "no confirmation before a save");
    const record = mod.saveEnergyCandidate({ frame_index: 2, role: "ts" });
    assert(record !== null, "candidate recorded");
    const html = String(dom.getElementById("scientific-evidence-body").innerHTML);
    assert(html.indexOf('data-se-save-confirm="1"') !== -1, "visible confirmation after save");
    assert(html.indexOf("已保存为候选") !== -1, "confirmation copy is human-readable");
    assertEq(written.length, 1, "candidate persisted exactly once");
    assertEq(written[0].key, "acp_evidence_saved_candidates", "persistence key");
    assertEq(written[0].value.length, 1, "persisted record list");
    assertEq(written[0].value[0].role, "ts", "persisted payload kept");
    const actions = html.slice(html.indexOf("data-se-energy-actions"));
    const actionsBlock = actions.slice(0, actions.indexOf("</div>"));
    assert(actionsBlock.indexOf('data-se-action="save-candidate"') !== -1, "sole action kept");
    assert(html.indexOf('data-se-action="submit"') === -1, "no submit action appears");
    mod.saveEnergyCandidate({ frame_index: 3 });
    assert(String(dom.getElementById("scientific-evidence-body").innerHTML)
      .indexOf('data-se-save-confirm="2"') !== -1, "confirmation count tracks saves");
    assertEq(written[1].value.length, 2, "persistence keeps the full list");
  });

  await test("vibration autoplay has a pause control and honors prefers-reduced-motion", () => {
    const dom = makeDom();
    const sandbox = h.makeSandbox({
      document: dom,
      console: { log() {}, warn() {}, error() {} },
      matchMedia: (query) => ({ matches: String(query).indexOf("reduced-motion") !== -1, media: query }),
    });
    h.loadScript(FEATURE_REL, sandbox);
    const mod = sandbox.window.ACPScientificEvidence;
    mod.mount(dom, {});
    mod.setEvidence(fixtureBundle(h));
    assertEq(mod.getState().vibrationPaused, true, "reduced motion starts paused");
    const html = String(dom.getElementById("scientific-evidence-body").innerHTML);
    assert(html.indexOf('data-se-action="toggle-vibration"') !== -1, "pause control rendered");
    assert(html.indexOf('data-vibration-paused="1"') !== -1, "paused state visible");
    assertEq(mod.toggleVibrationPlayback(), false, "toggle resumes playback");
    assertEq(mod.getState().vibrationPaused, false);
    assert(String(dom.getElementById("scientific-evidence-body").innerHTML)
      .indexOf('data-vibration-paused="0"') !== -1, "resumed state visible");
    assertEq(mod.toggleVibrationPlayback(), true, "toggle pauses again");
  });
};

