/* ACP 3.0 module test — nmr-report (A-52 / A-53, P18).
 *
 * Loads the real module in the zero-dependency vm sandbox and drives it through
 * a fake DOM, a routed fake request adapter and a fake WorkspaceContext.
 * `window.fetch` is never defined, so a direct network call cannot pass.
 *
 * Pinned contracts:
 *   - descriptor/factory registration + four states (empty/loading/ready/error);
 *   - report file probe order and remote `content.report` unwrap;
 *   - DP4/DP5 values are the SERVER values (never recomputed, never 0-filled);
 *     the server `summary.winner` is the only winner badge source;
 *   - candidate list, assignment table and observation-claim table rendering;
 *   - assignment highlight (row ↔ observation ↔ signal-group/atom linkage);
 *   - A-53 candidate switching never refetches/recomputes/mutates the report,
 *     preserves the active tab + scroll, and a generation/select token guard
 *     drops late report and geometry responses (A→B→A);
 *   - dispose clears the host, unsubscribes, removes listeners and drops late
 *     responses; the module never calls a write route and never uses fetch.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const MODULE_PATH = "js/modules/nmr-report/module.js";
const MODULE_ABS = path.join(REPO_ROOT, "frontend", MODULE_PATH);
const API = "/api/v1";
const JOB_ID = "job-1";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const flush = async (n = 60) => {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < 8; i++) await Promise.resolve();
  };

  // --------------------------------------------------------------- fake DOM --
  function makeDom() {
    const docListeners = new Map();
    function makeEl(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        value: "",
        hidden: false,
        disabled: false,
        type: "",
        title: "",
        scrollTop: 0,
        style: {},
        attrs: {},
        listeners,
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        replaceChildren(...nodes) {
          this.children.forEach((c) => { c.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((n) => { n.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, ev) {
          const event = Object.assign({
            type,
            target: this,
            currentTarget: this,
            preventDefault() {},
            stopPropagation() {},
          }, ev || {});
          let node = this;
          while (node) {
            const set = node.listeners && node.listeners.get(type);
            if (set) {
              event.currentTarget = node;
              [...set].forEach((fn) => fn(event));
            }
            node = node.parentNode;
          }
        },
        activeListeners() {
          let n = 0;
          listeners.forEach((s) => { n += s.size; });
          return n;
        },
      };
    }
    const document = {
      createElement: (tag) => makeEl(tag),
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      dispatch(type) {
        const set = docListeners.get(type);
        if (set) [...set].forEach((fn) => fn({ type }));
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let n = 0;
        docListeners.forEach((s) => { n += s.size; });
        return n;
      },
    };
    return { document, makeEl };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((child) => { out.push(child); walk(child, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute(attr) === value) || null;
  }

  function findAny(root, attr) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute(attr) != null) || null;
  }

  function findAll(root, attr) {
    return walk(root).filter((n) => n.getAttribute && n.getAttribute(attr) != null);
  }

  function textOf(node) {
    if (!node) return "";
    let text = typeof node.textContent === "string" ? node.textContent : "";
    (node.children || []).forEach((child) => { text += textOf(child); });
    return text;
  }

  function byText(root, text) {
    return walk(root).find((n) => textOf(n).indexOf(text) !== -1) || null;
  }

  function byClass(root, cls) {
    return walk(root).find((n) => n.className && n.className.split(/\s+/).indexOf(cls) !== -1) || null;
  }

  function moduleRoot(host) {
    return host.children[0] || null;
  }

  function moduleState(host) {
    const root = moduleRoot(host);
    return root ? root.getAttribute("data-acp-module-state") : null;
  }

  function rootAttr(host, name) {
    const root = moduleRoot(host);
    return root ? root.getAttribute(name) : null;
  }

  // ------------------------------------------------------------ fake context --
  function makeContext(initial) {
    const subs = [];
    let snapshot = Object.assign({
      projectId: "proj-acp2",
      researchRef: null,
      taskRef: { jobId: JOB_ID, attempt: 1 },
      structureRef: null,
      revision: 0,
      source: "test",
    }, initial || {});
    return {
      getSnapshot: () => snapshot,
      subscribe(fn) {
        subs.push(fn);
        return () => {
          const idx = subs.indexOf(fn);
          if (idx >= 0) subs.splice(idx, 1);
        };
      },
      setFocus(patch, source) {
        snapshot = Object.assign({}, snapshot, patch, {
          revision: snapshot.revision + 1,
          source: source || "test:setFocus",
        });
        subs.slice().forEach((fn) => fn(snapshot));
        return snapshot;
      },
      count: () => subs.length,
    };
  }

  // ----------------------------------------------------------- fake request --
  function makeTransport(handler) {
    const calls = [];
    return {
      calls,
      request(pathArg, opts) {
        const options = Object.assign({ method: "GET" }, opts || {});
        calls.push({ path: pathArg, opts: options });
        return Promise.resolve().then(() => handler(pathArg, options, calls));
      },
    };
  }

  function httpError(status, message) {
    const error = new Error(message || ("HTTP " + status));
    error.status = status;
    error.body = { detail: message || ("HTTP " + status) };
    return error;
  }

  function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  function reportCalls(calls) {
    return calls.filter((call) => call.path.indexOf("nmr_report.json") !== -1);
  }

  function geometryCalls(calls) {
    return calls.filter((call) => call.path.indexOf(".xyz") !== -1);
  }

  function isLocalFile(call) {
    return call.path.indexOf("/files/") !== -1;
  }

  // ---------------------------------------------------------------- fixtures --
  function assignment(overrides) {
    return Object.assign({
      atom: "C1",
      element: "C",
      exp_ppm: 130.1,
      calc_ppm: 129.8,
      scaled_ppm: 130.05,
      residual: -0.05,
      observation_id: "C:0",
      signal_group: {
        atom_uids: ["C1", "C2"],
        coefficients: [1.0],
        equivalence_basis: "topology",
        peak_capacity: 1,
        experiment_ref: null,
      },
    }, overrides || {});
  }

  function probability(status, value, mode, reasons) {
    return {
      model_id: status === "valid" ? "goodman" : "placeholder",
      model_version: "goodman-legacy",
      status,
      probability: value,
      mode: mode == null ? null : mode,
      calibration_status: status === "valid" ? "calibrated" : "unknown",
      reasons: reasons || [],
      atom_diagnostics: [],
    };
  }

  function regression() {
    return {
      "13C": {
        slope: 1.02,
        intercept: -2.1,
        r_squared: 0.998,
        r2_regression: 0.998,
        r2_prediction: 0.997,
        mae: 0.12,
      },
      "1H": {
        slope: 0.99,
        intercept: 0.05,
        r_squared: 0.995,
        r2_regression: 0.995,
        r2_prediction: 0.994,
        mae: 0.03,
      },
    };
  }

  function coverage(matched) {
    return {
      expected_signals: 10,
      matched_signals: matched == null ? 10 : matched,
      per_nucleus: { "13C": { expected: 5, matched: 5 }, "1H": { expected: 5, matched: 5 } },
      n_conformers_selected: 5,
      n_conformers_successful: 5,
      selected_population: 1.0,
      successful_population: 0.99,
    };
  }

  function candidate(index, label, dp4, dp5, assignments, overrides) {
    return Object.assign({
      index,
      label,
      dp4_probability: dp4,
      dp5_probability: dp5,
      dp5_diagnostic_score: null,
      dp5_kernel: null,
      evidence: {
        status: "valid",
        total_matched: 10,
        observation_ids: ["C:0", "C:1", "H:0"],
        exclusion_reasons: [],
        per_nucleus: { "13C": { expected: 5, matched: 5 }, "1H": { expected: 5, matched: 5 } },
      },
      probability: {
        dp4: probability(dp4 == null ? "invalid" : "valid", dp4, null, []),
        dp5: probability(dp5 == null ? "unavailable" : "valid", dp5, "fallback",
          dp5 == null ? ["dp5_probability_unavailable"] : []),
      },
      diagnostics: {
        kind: "atomic_diagnostics_v1",
        candidate_index: index,
        label,
        dp4: { dp4_combined: dp4 },
        signals: [{ atom_label: "C1", z_score: 0.4 }],
        dp5: null,
        atom_support: [{ atom_label: "C1", out_of_domain: false }],
        leave_one_signal_out: [],
      },
      analysis_status: "valid",
      coverage: coverage(),
      ensemble_quality: {
        quality_status: "ok",
        quality_flags: [],
        n_discovered: 12,
        n_selected: 5,
        n_successful: 5,
        preselection_population: 1.0,
        selected_population: 1.0,
        successful_population: 0.99,
        uncovered_population: 0.0,
        population_gate_dropped: 0.0,
        conformers: [],
      },
      signal_groups: [],
      n_conformers: assignments.length,
      regression: regression(),
      assignment: assignments,
      conformers: [{ id: 1, boltzmann_weight: 0.6 }, { id: 2, boltzmann_weight: 0.4 }],
    }, overrides || {});
  }

  function makeReport(overrides) {
    const candidates = [
      candidate(0, "cand_A", 0.12, null, [
        assignment({ atom: "C1", observation_id: "C:0", residual: -0.05 }),
        assignment({ atom: "H1", element: "H", observation_id: "H:0", exp_ppm: 7.2, calc_ppm: 7.1, scaled_ppm: 7.18, residual: -0.02, signal_group: { atom_uids: ["H1"], coefficients: [1], equivalence_basis: "topology", peak_capacity: 1, experiment_ref: null } }),
      ]),
      candidate(1, "cand_B", 0.831, 0.71, [
        assignment({ atom: "C1", observation_id: "C:0", residual: -0.05 }),
        assignment({ atom: "C2", observation_id: "C:1", exp_ppm: 128.4, calc_ppm: 128.2, scaled_ppm: 128.36, residual: -0.04 }),
        assignment({ atom: "H1", element: "H", observation_id: "H:0", exp_ppm: 7.2, calc_ppm: 7.1, scaled_ppm: 7.18, residual: -0.02, signal_group: { atom_uids: ["H1"], coefficients: [1], equivalence_basis: "topology", peak_capacity: 1, experiment_ref: null } }),
      ]),
      candidate(2, "cand_C", 0.049, 0.29, [
        assignment({ atom: "C1", observation_id: "C:0", residual: 0.11 }),
      ], { analysis_status: "evidence_insufficient" }),
    ];
    const base = {
      schema_version: 2,
      protocol_id: "proto-acp2-nmr",
      summary: {
        n_candidates: 3,
        winner: { index: 1, label: "cand_B", dp4: 0.831, dp5: 0.71 },
        dp4_ranking: "normal",
        nuclei: ["1H", "13C"],
      },
      candidates,
      config: { nmr_method: "mPW1PW91", nmr_basis: "6-311G(d)", solvent: "chloroform" },
      error_model: "goodman-legacy",
      dp5_mode: "fallback",
      fchl_kernel: "",
      provenance: { error_model: "goodman-legacy", dp5_mode: "fallback" },
      sensitivity: null,
      processing_quality: [
        {
          nucleus: "1H",
          element: "H",
          source_dir: "exp/1H",
          status: "ok",
          reasons: [],
          formal_usable: true,
          phase_method: "manual",
          reference_method: "tms",
          reference_ppm: 0.0,
          applied_shift_ppm: 0.0,
          quality: null,
          digital_filter: null,
        },
      ],
      diagnostics: {
        kind: "atomic_diagnostics_v1",
        conflict_matrix: {
          candidate_indices: [0, 1, 2],
          observation_ids: ["C:0", "C:1", "H:0"],
          conflicting_observation_ids: ["C:0"],
          claims: [
            { observation_id: "C:0", nucleus: "13C", exp_ppm: 130.1, candidate_indices: [0, 1, 2], atom_labels: ["C1", "C1", "C1"], n_claims: 3, conflict: true },
            { observation_id: "C:1", nucleus: "13C", exp_ppm: 128.4, candidate_indices: [1], atom_labels: ["C2"], n_claims: 1, conflict: false },
            { observation_id: "H:0", nucleus: "1H", exp_ppm: 7.2, candidate_indices: [0, 1], atom_labels: ["H1", "H1"], n_claims: 2, conflict: true },
          ],
        },
        leave_one_signal_out: { n_cases: 3, n_winner_flips: 1, winner_flips: [] },
      },
      note: "",
    };
    return Object.assign({}, base, overrides || {});
  }

  function reportWithNoDp() {
    const report = makeReport();
    report.candidates = report.candidates.map((cand) => Object.assign({}, cand, {
      dp4_probability: null,
      dp5_probability: null,
      probability: null,
    }));
    report.summary = Object.assign({}, report.summary, {
      winner: null,
      dp4_ranking: "not_applicable",
    });
    return report;
  }

  function reportWithNoCandidates() {
    const report = makeReport();
    report.candidates = [];
    report.summary = Object.assign({}, report.summary, { n_candidates: 0, winner: null });
    return report;
  }

  function xyz(symbols) {
    return String(symbols.length) + "\nframe\n" +
      symbols.map((symbol) => symbol + " 0.000 0.000 0.000").join("\n") + "\n";
  }

  // ------------------------------------------------------------- mount helper --
  function load() {
    const dom = makeDom();
    const sandbox = makeSandbox({ document: dom.document });
    loadScript(MODULE_PATH, sandbox);
    return { sandbox, dom };
  }

  async function setup(options) {
    options = options || {};
    const { sandbox, dom } = load();
    const transport = makeTransport(options.handler || (() => {
      throw httpError(404, "not found");
    }));
    const context = options.context || makeContext(options.initial || undefined);
    const host = dom.makeEl("div");
    const deps = { doc: dom.document };
    if (!options.noTransport) deps.request = transport.request;
    if (options.jobDetail) deps.jobDetail = options.jobDetail;
    if (options.apiBase) deps.apiBase = options.apiBase;
    const instance = sandbox.ACPNextModules["nmr-report"].factory(deps);
    await instance.mount(host, options.noContext ? {} : { context });
    await flush();
    return { sandbox, dom, transport, context, host, instance };
  }

  function readyHandler(report, extra) {
    return (pathArg) => {
      if (extra) {
        const handled = extra(pathArg);
        if (handled !== undefined) return handled;
      }
      if (pathArg.indexOf("nmr_report.json") !== -1 && pathArg.indexOf("/files/") !== -1) return report;
      throw httpError(404, "not found");
    };
  }

  function selectCandidate(host, pos) {
    const button = walk(host).find((n) => n.getAttribute && n.getAttribute("data-candidate-pos") === String(pos));
    assert(!!button, "candidate button " + pos + " exists");
    button.fire("click");
  }

  function selectTab(host, tab) {
    const button = walk(host).find((n) => n.getAttribute && n.getAttribute("data-tab") === tab);
    assert(!!button, "tab " + tab + " exists");
    button.fire("click");
  }

  function assignmentRows(host) {
    return findAll(host, "data-nmr-row-index");
  }

  function peakRows(host) {
    return findAll(host, "data-nmr-peak-index");
  }

  // -------------------------------------------------------------------- tests --

  await test("nmr-report: registers the ACPNextModules descriptor and lifecycle", async () => {
    const { sandbox } = load();
    const entry = sandbox.ACPNextModules["nmr-report"];
    assert(!!entry, "registered module entry");
    assertEq(entry.descriptor.id, "nmr-report", "descriptor id");
    assertEq(entry.descriptor.interfaceVersion, 1, "interface version");
    assert(/^\d+\.\d+\.\d+$/.test(entry.descriptor.version), "semver-ish version");
    assertEq(entry.descriptor.binds, ["taskRef"], "binds taskRef");
    assert(entry.descriptor.provides.indexOf("nmr.report") !== -1, "provides nmr.report");
    assertEq(typeof entry.factory, "function", "factory function");
    const instance = entry.factory({ doc: makeDom().document });
    ["mount", "updateContext", "suspend", "resume", "dispose"].forEach((name) => {
      assertEq(typeof instance[name], "function", name + " lifecycle");
    });
    assertEq(sandbox.fetch, undefined, "no global fetch in the sandbox");
    assertEq(sandbox.nmrPanelState, undefined, "no classic page singleton leaked");
  });

  await test("nmr-report: no selected task is an explicit empty state and makes no request", async () => {
    let called = 0;
    const context = makeContext({ projectId: "proj-acp2", taskRef: null });
    const t = await setup({
      context,
      handler: () => { called += 1; throw httpError(500, "must not be called"); },
    });
    assertEq(moduleState(t.host), "empty", "empty state");
    assertEq(rootAttr(t.host, "data-acp-nmr-empty-reason"), "no_job", "empty reason");
    assert(byText(t.host, "未选择任务") !== null, "no-job copy");
    assertEq(called, 0, "no network call without a task");
    assertEq(t.transport.calls.length, 0, "transport untouched");
    t.instance.dispose();
  });

  await test("nmr-report: a non-NMR task is an explicit empty state and never reads a report", async () => {
    const jobDetail = {
      calls: [],
      fetchDetail(jobId) {
        this.calls.push(jobId);
        return Promise.resolve({ job: { id: jobId, spec: { workflow: "singlepoint" } } });
      },
    };
    const t = await setup({
      jobDetail,
      handler: () => { throw new Error("must not read a report for a non-NMR task"); },
    });
    assertEq(moduleState(t.host), "empty", "empty state");
    assertEq(rootAttr(t.host, "data-acp-nmr-empty-reason"), "not_nmr", "not-nmr reason");
    assert(byText(t.host, "不是 NMR 任务") !== null, "not-nmr copy");
    assertEq(jobDetail.calls, [JOB_ID], "job detail consulted once");
    assertEq(t.transport.calls.length, 0, "no report request");
    t.instance.dispose();
  });

  await test("nmr-report: loading state while the report probe is in flight", async () => {
    const pending = deferred();
    const t = await setup({ handler: () => pending.promise });
    assertEq(moduleState(t.host), "loading", "loading state");
    assert(byText(t.host, "正在读取 NMR 报告") !== null, "loading copy");
    pending.resolve(makeReport());
    await flush();
    assertEq(moduleState(t.host), "ready", "ready after the response");
    t.instance.dispose();
  });

  await test("nmr-report: ready renders the server summary, candidate list and report path", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    assertEq(moduleState(t.host), "ready", "ready state");
    assertEq(find(t.host, "data-field", "winner").textContent, "cand_B", "server winner label");
    assertEq(find(t.host, "data-field", "winner_dp4").textContent, "0.831", "server winner DP4");
    assertEq(find(t.host, "data-field", "winner_dp5").textContent, "0.710", "server winner DP5");
    assertEq(find(t.host, "data-field", "candidates").textContent, "3", "candidate count");
    assertEq(find(t.host, "data-field", "nuclei").textContent, "1H, 13C", "nuclei");
    assertEq(find(t.host, "data-field", "ranking").textContent, "正常（服务端）", "server ranking");
    assertEq(find(t.host, "data-field", "report").textContent, "RESULT/reports/nmr_report.json", "report path");
    const buttons = findAll(t.host, "data-candidate-pos");
    assertEq(buttons.length, 3, "three candidate buttons");
    assertEq(buttons[1].getAttribute("data-winner"), "true", "server winner badge");
    assertEq(buttons[0].getAttribute("data-winner"), "false", "non-winner has no badge");
    assertEq(buttons[0].getAttribute("data-dp4"), "0.12", "raw DP4 value");
    assertEq(buttons[1].getAttribute("data-dp5"), "0.71", "raw DP5 value");
    assertEq(t.instance.snapshot().selectedIndex, 1, "server winner is the initial selection");
    t.instance.dispose();
  });

  await test("nmr-report: probes the current RESULT/reports layout before the legacy spellings", async () => {
    const report = makeReport();
    let resultCalls = 0;
    const t = await setup({
      handler: (pathArg) => {
        if (pathArg.indexOf("RESULT/reports/nmr_report.json") !== -1) {
          resultCalls += 1;
          throw httpError(404, "not found");
        }
        if (pathArg.indexOf("reports/nmr_report.json") !== -1) return report;
        throw httpError(404, "not found");
      },
    });
    assertEq(resultCalls, 1, "first probe attempted the current layout");
    assertEq(moduleState(t.host), "ready", "ready after the legacy layout");
    assertEq(reportCalls(t.transport.calls).length, 2, "two probes");
    assertEq(find(t.host, "data-field", "report").textContent, "reports/nmr_report.json", "legacy path recorded");
    t.instance.dispose();
  });

  await test("nmr-report: a remote job uses the preview?mode=report endpoint and unwraps content.report", async () => {
    const report = makeReport();
    const jobDetail = {
      fetchDetail: () => Promise.resolve({ job: { id: JOB_ID, spec: { workflow: "nmr" }, node_id: "alpha" } }),
    };
    const t = await setup({
      jobDetail,
      handler: (pathArg) => {
        if (pathArg.indexOf("/remote-files/") !== -1 && pathArg.indexOf("nmr_report.json") !== -1) {
          return {
            content: {
              type: "json_report",
              file_path: "RESULT/reports/nmr_report.json",
              report,
            },
          };
        }
        throw httpError(404, "not found");
      },
    });
    assertEq(moduleState(t.host), "ready", "remote report ready");
    const call = reportCalls(t.transport.calls)[0];
    assert(call.path.indexOf("/remote-files/RESULT/reports/nmr_report.json/preview?mode=report") !== -1,
      "remote preview path");
    assertEq(call.path.indexOf("/files/"), -1, "no local file read for a remote job");
    t.instance.dispose();
  });

  await test("nmr-report: a missing report file is an explicit empty state, never a fabricated score", async () => {
    const t = await setup({
      handler: () => { throw httpError(404, "not found"); },
    });
    assertEq(moduleState(t.host), "empty", "empty state");
    assertEq(rootAttr(t.host, "data-acp-nmr-empty-reason"), "no_report", "no-report reason");
    assert(byText(t.host, "未找到 nmr_report.json") !== null, "no-report copy");
    assert(byText(t.host, "不会估算") !== null, "explicit no-fabrication hint");
    assertEq(assignmentRows(t.host).length, 0, "no assignment table");
    assertEq(reportCalls(t.transport.calls).length, 3, "all three legacy paths probed");
    t.instance.dispose();
  });

  await test("nmr-report: a report with no candidates is an explicit empty state", async () => {
    const t = await setup({ handler: readyHandler(reportWithNoCandidates()) });
    assertEq(moduleState(t.host), "empty", "empty state");
    assertEq(rootAttr(t.host, "data-acp-nmr-empty-reason"), "no_candidates", "no-candidates reason");
    assert(byText(t.host, "报告中没有候选") !== null, "no-candidates copy");
    t.instance.dispose();
  });

  await test("nmr-report: candidates without any DP4/DP5 probability are an explicit empty state", async () => {
    const t = await setup({ handler: readyHandler(reportWithNoDp()) });
    assertEq(moduleState(t.host), "empty", "empty state");
    assertEq(rootAttr(t.host, "data-acp-nmr-empty-reason"), "no_dp4_data", "no-DP4 reason");
    const hint = byText(t.host, "没有 DP4/DP5 概率");
    assert(hint !== null, "no-DP4 copy");
    assert(byText(t.host, "不会本地计算") !== null, "explicit no-local-compute hint");
    assert(byText(t.host, "3") !== null, "candidate count surfaced");
    assertEq(assignmentRows(t.host).length, 0, "no assignment table in the empty state");
    t.instance.dispose();
  });

  await test("nmr-report: no spectrum/assignment data is a distinct empty state", async () => {
    const report = reportWithNoDp();
    report.candidates = report.candidates.map((cand) => Object.assign({}, cand, { assignment: [] }));
    report.processing_quality = null;
    const t = await setup({ handler: readyHandler(report) });
    assertEq(moduleState(t.host), "empty", "empty state");
    assertEq(rootAttr(t.host, "data-acp-nmr-empty-reason"), "no_spectrum_data", "no-spectrum reason");
    assert(byText(t.host, "没有谱图/归属数据") !== null, "no-spectrum copy");
    t.instance.dispose();
  });

  await test("nmr-report: a read failure is an error state and retry re-reads", async () => {
    let attempts = 0;
    const report = makeReport();
    const t = await setup({
      handler: (pathArg) => {
        if (pathArg.indexOf("nmr_report.json") !== -1) {
          attempts += 1;
          if (attempts === 1) throw httpError(500, "server exploded");
          return report;
        }
        throw httpError(404, "not found");
      },
    });
    assertEq(moduleState(t.host), "error", "error state");
    assertEq(rootAttr(t.host, "data-acp-nmr-error-code"), "read_failed", "error code");
    assert(byText(t.host, "server exploded") !== null, "server detail surfaced");
    const retry = find(t.host, "data-action", "retry");
    assert(!!retry && retry.hidden === false, "retry visible");
    retry.fire("click");
    await flush();
    assertEq(moduleState(t.host), "ready", "retry recovers");
    assertEq(attempts, 2, "retry issued exactly one more read");
    t.instance.dispose();
  });

  await test("nmr-report: a missing transport is explicit and makes no network call", async () => {
    const t = await setup({ noTransport: true });
    assertEq(moduleState(t.host), "error", "error state without a request adapter");
    assertEq(rootAttr(t.host, "data-acp-nmr-error-code"), "no_transport", "no-transport code");
    assert(byText(t.host, "未注入请求通道") !== null, "transport copy");
    assertEq(t.transport.calls.length, 0, "no network call");
    assert(find(t.host, "data-action", "retry").hidden === false, "retry visible");
    t.instance.dispose();
  });

  await test("nmr-report: an invalid JSON report is an error, not an empty state", async () => {
    const t = await setup({ handler: () => "{not valid json" });
    assertEq(moduleState(t.host), "error", "error state");
    assertEq(rootAttr(t.host, "data-acp-nmr-error-code"), "invalid_report", "invalid-report code");
    assert(byText(t.host, "不是有效的 JSON 报告") !== null, "invalid-report copy");
    t.instance.dispose();
  });

  await test("nmr-report: a null probability renders as an em dash, never as 0", async () => {
    const report = makeReport();
    report.candidates[2] = Object.assign({}, report.candidates[2], {
      dp4_probability: null,
      dp5_probability: null,
      probability: null,
    });
    const t = await setup({ handler: readyHandler(report) });
    selectCandidate(t.host, 2);
    await flush();
    const button = walk(t.host).find((n) => n.getAttribute && n.getAttribute("data-candidate-pos") === "2");
    assertEq(button.getAttribute("data-dp4"), "null", "null DP4 raw marker");
    assertEq(button.getAttribute("data-dp5"), "null", "null DP5 raw marker");
    assert(textOf(button).indexOf("—") !== -1, "em dash displayed");
    assert(textOf(button).indexOf("0.000") === -1, "null never coerced to 0.000");
    const metric = find(t.host, "data-field", "dp4_probability");
    assertEq(metric.textContent, "—", "metric em dash");
    t.instance.dispose();
  });

  await test("nmr-report: the server summary.winner is authoritative (no local ranking)", async () => {
    const report = makeReport();
    report.candidates[0].dp4_probability = 0.99;
    report.candidates[0].dp5_probability = 0.98;
    report.candidates[1].dp4_probability = 0.01;
    report.candidates[1].dp5_probability = 0.02;
    report.summary.winner = { index: 0, label: "cand_A", dp4: 0.99, dp5: 0.98 };
    const t = await setup({ handler: readyHandler(report) });
    const buttons = findAll(t.host, "data-candidate-pos");
    assertEq(buttons[0].getAttribute("data-winner"), "true", "server winner is A despite lower... higher value");
    assertEq(buttons[1].getAttribute("data-winner"), "false", "B is not locally promoted");
    assertEq(find(t.host, "data-field", "winner").textContent, "cand_A", "server winner label");
    assertEq(t.instance.snapshot().selectedIndex, 0, "server winner selected");
    t.instance.dispose();
  });

  await test("nmr-report: assignment table shows the exact server values", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    const rows = assignmentRows(t.host);
    assertEq(rows.length, 3, "three assignment rows for the winner");
    const cells = rows[0].children;
    assertEq(cells[0].textContent, "C1", "atom");
    assertEq(cells[1].textContent, "C", "element");
    assertEq(cells[2].textContent, "130.1000", "exp ppm formatted from server");
    assertEq(cells[2].getAttribute("data-value"), "130.1", "exp ppm raw");
    assertEq(cells[4].getAttribute("data-value"), "130.05", "scaled ppm raw");
    assertEq(cells[5].getAttribute("data-value"), "-0.05", "residual raw");
    assert(cells[5].className.indexOf("acp-nmr-residual--neg") !== -1, "negative residual styling");
    assertEq(cells[6].textContent, "C:0", "observation id");
    const metric = find(t.host, "data-field", "dp4_probability");
    assertEq(metric.getAttribute("data-value"), "0.831", "metric raw DP4");
    t.instance.dispose();
  });

  await test("nmr-report: clicking an assignment row highlights it and its signal-group relatives", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    const rows = assignmentRows(t.host);
    rows[0].fire("click");
    assertEq(rows[0].getAttribute("data-acp-nmr-highlight"), "active", "clicked row active");
    assertEq(rows[1].getAttribute("data-acp-nmr-highlight"), "related", "same signal group related");
    assertEq(rows[2].getAttribute("data-acp-nmr-highlight"), "", "unrelated row untouched");
    assert(byText(t.host, "原子 C1") !== null, "highlight summary shows the atom");
    assert(find(t.host, "data-action", "clear-highlight").hidden === false, "clear enabled");
    find(t.host, "data-action", "clear-highlight").fire("click");
    assertEq(rows[0].getAttribute("data-acp-nmr-highlight"), "", "highlight cleared");
    assert(byText(t.host, "未选择归属") !== null, "clear summary");
    t.instance.dispose();
  });

  await test("nmr-report: observation claims are rendered and a peak click links the assignment row", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    selectTab(t.host, "peaks");
    const peaks = peakRows(t.host);
    assertEq(peaks.length, 3, "three claim rows");
    assert(byText(t.host, "冲突 2") !== null, "conflict count");
    assertEq(peaks[0].getAttribute("data-nmr-observation"), "C:0", "first observation id");
    peaks[0].fire("click");
    assertEq(assignmentRows(t.host)[0].getAttribute("data-acp-nmr-highlight"), "active", "assignment row linked");
    selectTab(t.host, "assignment");
    assert(byText(t.host, "观测峰 C:0") !== null, "peak highlight summary");
    t.instance.dispose();
  });

  await test("nmr-report: candidate switching changes only the local view (no refetch, no mutation)", async () => {
    const report = makeReport();
    const before = JSON.stringify(report);
    const t = await setup({ handler: readyHandler(report) });
    const reportReadsBefore = reportCalls(t.transport.calls).length;
    selectCandidate(t.host, 0);
    await flush();
    assertEq(find(t.host, "data-candidate-pos", "0").getAttribute("data-selected"), "true", "A selected");
    assertEq(find(t.host, "data-candidate-pos", "1").getAttribute("data-selected"), "false", "B deselected");
    assertEq(assignmentRows(t.host).length, 2, "A has two assignment rows");
    assertEq(find(t.host, "data-field", "dp4_probability").getAttribute("data-value"), "0.12", "A DP4");
    assertEq(reportCalls(t.transport.calls).length, reportReadsBefore, "no report refetch on switch");
    selectCandidate(t.host, 1);
    await flush();
    assertEq(assignmentRows(t.host).length, 3, "B has three assignment rows");
    assertEq(find(t.host, "data-field", "dp4_probability").getAttribute("data-value"), "0.831", "B DP4");
    assertEq(JSON.stringify(report), before, "report object is not mutated");
    assertEq(JSON.stringify(t.instance.snapshot().report), before, "module exposes the same report bytes");
    t.instance.dispose();
  });

  await test("nmr-report: switching preserves the active tab and scroll position", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    selectTab(t.host, "metrics");
    const scroll = byClass(t.host, "acp-nmr-scroll");
    assert(!!scroll, "scroll container exists");
    scroll.scrollTop = 120;
    selectCandidate(t.host, 2);
    await flush();
    assertEq(find(t.host, "data-tab", "metrics").getAttribute("data-active"), "true", "metrics tab preserved");
    assertEq(find(t.host, "data-tab", "assignment").getAttribute("data-active"), "false", "assignment tab stays hidden");
    assertEq(byClass(t.host, "acp-nmr-scroll").scrollTop, 120, "scroll position preserved");
    selectCandidate(t.host, 0);
    await flush();
    assertEq(find(t.host, "data-tab", "metrics").getAttribute("data-active"), "true", "tab preserved across another switch");
    assertEq(byClass(t.host, "acp-nmr-scroll").scrollTop, 120, "scroll preserved across another switch");
    t.instance.dispose();
  });

  await test("nmr-report: switching candidates clears a highlight from the previous candidate", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    const rows = assignmentRows(t.host);
    rows[0].fire("click");
    assertEq(rows[0].getAttribute("data-acp-nmr-highlight"), "active", "highlight on B");
    selectCandidate(t.host, 0);
    await flush();
    assignmentRows(t.host).forEach((row) => {
      assertEq(row.getAttribute("data-acp-nmr-highlight"), "", "no stale highlight after switch");
    });
    t.instance.dispose();
  });

  await test("nmr-report: a late report for job A never paints over job B", async () => {
    const deferredA = deferred();
    const reportA = makeReport();
    const reportB = makeReport();
    reportB.summary.winner = { index: 2, label: "cand_C", dp4: 0.049, dp5: 0.29 };
    let aCalls = 0;
    const t = await setup({
      initial: { projectId: "proj-acp2", taskRef: { jobId: "job-A", attempt: 1 } },
      handler: (pathArg) => {
        if (pathArg.indexOf("job-A") !== -1) {
          aCalls += 1;
          return aCalls === 1 ? deferredA.promise : reportA;
        }
        if (pathArg.indexOf("job-B") !== -1) return reportB;
        throw httpError(404, "not found");
      },
    });
    assertEq(moduleState(t.host), "loading", "A stays loading");
    t.context.setFocus({ taskRef: { jobId: "job-B", attempt: 1 } }, "test");
    await flush();
    assertEq(moduleState(t.host), "ready", "B is ready");
    assertEq(find(t.host, "data-field", "winner").textContent, "cand_C", "B winner");
    deferredA.resolve(reportA);
    await flush();
    assertEq(moduleState(t.host), "ready", "still ready");
    assertEq(find(t.host, "data-field", "winner").textContent, "cand_C", "late A never paints");
    assertEq(t.instance.snapshot().report.summary.winner.label, "cand_C", "state still B");
    t.context.setFocus({ taskRef: { jobId: "job-A", attempt: 1 } }, "test");
    await flush();
    assertEq(find(t.host, "data-field", "winner").textContent, "cand_B", "back to A reads A again");
    t.instance.dispose();
  });

  await test("nmr-report: A→B→A candidate switching drops every late geometry response", async () => {
    const report = makeReport();
    const geometryA = [deferred(), deferred()];
    const geometryB = [deferred(), deferred()];
    let aCalls = 0;
    let bCalls = 0;
    const t = await setup({
      handler: (pathArg) => {
        if (pathArg.indexOf("nmr_report.json") !== -1) return report;
        if (pathArg.indexOf("cand_A/conformers/input.xyz") !== -1) {
          const d = geometryA[aCalls] || deferred();
          aCalls += 1;
          return d.promise;
        }
        if (pathArg.indexOf("cand_B/conformers/input.xyz") !== -1) {
          const d = geometryB[bCalls] || deferred();
          bCalls += 1;
          return d.promise;
        }
        throw httpError(404, "not found");
      },
    });
    // Initial selection is the server winner B. Open the structure tab to
    // start B's geometry load, then walk B → A → B → A while all four reads
    // are still in flight.
    selectTab(t.host, "structure");
    await flush(3);
    selectCandidate(t.host, 0);
    await flush(2);
    selectCandidate(t.host, 1);
    await flush(2);
    selectCandidate(t.host, 0);
    await flush(2);
    assertEq(aCalls, 2, "two A geometry reads (first stale, second current)");
    assertEq(bCalls, 2, "two B geometry reads");
    const structurePanel = find(t.host, "data-panel", "structure");
    assertEq(structurePanel.getAttribute("data-acp-nmr-geometry"), "loading", "current A geometry still loading");

    geometryB[0].resolve(xyz(["C", "C", "C"]));
    await flush(2);
    geometryA[0].resolve(xyz(["C", "C", "C", "C"]));
    await flush(2);
    geometryB[1].resolve(xyz(["C", "C", "C", "C", "C", "C"]));
    await flush(2);
    assertEq(structurePanel.getAttribute("data-acp-nmr-geometry"), "loading",
      "late responses never flip the current selection to ready");
    assert(byText(t.host, "原子 3") === null, "stale A/B atom counts not painted");

    geometryA[1].resolve(xyz(["C", "C", "H", "H", "O"]));
    await flush(3);
    assertEq(structurePanel.getAttribute("data-acp-nmr-geometry"), "ready", "current A geometry wins");
    assert(byText(t.host, "原子 5") !== null, "current atom count painted");
    assertEq(t.instance.snapshot().selectedIndex, 0, "current candidate is still A");
    t.instance.dispose();
  });

  await test("nmr-report: the structure tab shows read-only geometry and links atom chips to assignments", async () => {
    const report = makeReport();
    const t = await setup({
      handler: (pathArg) => {
        if (pathArg.indexOf("nmr_report.json") !== -1) return report;
        if (pathArg.indexOf("cand_B/conformers/input.xyz") !== -1) {
          return xyz(["C", "C", "H", "H", "O"]);
        }
        throw httpError(404, "not found");
      },
    });
    selectTab(t.host, "structure");
    await flush();
    assertEq(find(t.host, "data-panel", "structure").getAttribute("data-acp-nmr-geometry"), "ready", "geometry ready");
    assert(byText(t.host, "原子 5") !== null, "atom count");
    assert(byText(t.host, "C×2") !== null, "element composition");
    const chips = findAll(t.host, "data-nmr-atom-chip");
    assertEq(chips.length, 5, "five atom chips");
    const c1 = chips.find((chip) => chip.getAttribute("data-nmr-atom-chip") === "C1");
    assert(!!c1, "C1 chip");
    c1.fire("click");
    assertEq(find(t.host, "data-tab", "assignment").getAttribute("data-active"), "true", "chip click returns to assignments");
    assertEq(assignmentRows(t.host)[0].getAttribute("data-acp-nmr-highlight"), "active", "C1 assignment highlighted");
    t.instance.dispose();
  });

  await test("nmr-report: dispose clears the host, unsubscribes, removes listeners and drops late reports", async () => {
    const pending = deferred();
    const t = await setup({ handler: () => pending.promise });
    assertEq(moduleState(t.host), "loading", "loading before dispose");
    assertEq(t.context.count(), 1, "context subscription active");
    assertEq(t.dom.document.activeDocListeners(), 1, "language listener active");
    t.instance.dispose();
    assertEq(t.host.children.length, 0, "host cleared");
    assertEq(t.context.count(), 0, "context unsubscribed");
    assertEq(t.dom.document.activeDocListeners(), 0, "document listener removed");
    pending.resolve(makeReport());
    await flush();
    assertEq(t.host.children.length, 0, "late report never re-creates DOM");
    assertEq(t.instance.snapshot().report, null, "late report not stored");
    t.instance.dispose();
    assertEq(t.host.children.length, 0, "dispose is idempotent");
  });

  await test("nmr-report: the module is read-only and never calls fetch/XHR or a write route", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    selectCandidate(t.host, 0);
    await flush();
    t.transport.calls.forEach((call) => {
      const method = String(call.opts.method || "GET").toUpperCase();
      assertEq(method, "GET", "only GET requests: " + call.path);
    });
    assertEq(t.transport.calls.filter((call) => /\/submit|\/jobs$/.test(call.path)).length, 0, "no submit route");
    const source = fs.readFileSync(MODULE_ABS, "utf8");
    assert(/\bfetch\s*\(/.test(source) === false, "no direct fetch call");
    assert(source.indexOf("XMLHttpRequest") === -1, "no XHR");
    assert(source.indexOf("innerHTML") === -1, "DOM built without innerHTML");
    assert(source.indexOf("compute_dp4") === -1 && source.indexOf("normalize_dp4") === -1,
      "no local DP4 computation");
    t.instance.dispose();
  });

  await test("nmr-report: legacy reports are shown with an explicit validation note", async () => {
    const report = makeReport();
    delete report.schema_version;
    const t = await setup({ handler: readyHandler(report) });
    assertEq(moduleState(t.host), "ready", "legacy report still readable");
    const note = find(t.host, "data-acp-nmr", "legacy-note");
    assert(!!note && note.hidden === false, "legacy note visible");
    assert(byText(t.host, "历史报告") !== null, "legacy note copy");
    t.instance.dispose();
  });

  await test("nmr-report: every emitted i18n key is namespaced and has an inline zh fallback", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    const nodes = findAll(t.host, "data-i18n");
    assert(nodes.length >= 20, "enough static keys (" + nodes.length + ")");
    nodes.forEach((node) => {
      const key = node.getAttribute("data-i18n");
      assert(key.indexOf("next.nmr.") === 0, "namespaced key: " + key);
      assert(node.textContent !== key, "zh fallback applied for: " + key);
    });
    const source = fs.readFileSync(MODULE_ABS, "utf8");
    assert(source.indexOf('"next.nmr.errorTitle"') !== -1, "descriptor error title key");
    assert(source.indexOf('"next.nmr.errorMessage"') !== -1, "descriptor error message key");
    t.instance.dispose();
  });

  await test("nmr-report: no report is requested for an empty context even after a failed job switch", async () => {
    const report = makeReport();
    const t = await setup({ handler: readyHandler(report) });
    assertEq(moduleState(t.host), "ready", "ready first");
    t.context.setFocus({ taskRef: null }, "test");
    await flush();
    assertEq(moduleState(t.host), "empty", "empty after clearing the task");
    assertEq(rootAttr(t.host, "data-acp-nmr-empty-reason"), "no_job", "no-job reason");
    const callsBefore = t.transport.calls.length;
    await flush();
    assertEq(t.transport.calls.length, callsBefore, "no request while no task is selected");
    t.instance.dispose();
  });
};
