/* Runtime smoke + behavior tests for the extracted energy chart renderer (OC-3).
 * The synthetic payload follows the S0 energy-graph node/series contract
 * (nodes: frame_index/id/energy/status; series: values/x_values/unit).
 * A missing dependency would throw ReferenceError inside the sandbox — this is
 * the final arbiter after the static extraction scan. */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadRenderer(overrides) {
    const calls = { trajectory: 0, frameCandidateFor: 0, metaRows: 0, lastOpts: null };
    const energyGraphState = {
      xAxisMode: "scanned_coordinate", selectedNodeId: "n1", layout: null,
      xDomain: null, yDomain: null, seriesId: "s1", jobId: "job-1", currentDomain: null,
    };
    const s2scanState = { selectionLocked: false, candidates: [], dirty: false };
    const deps = Object.assign({
      t: (key, vars) => (vars ? key + ":" + JSON.stringify(vars) : key),
      currentLang: "zh-CN",
      energyGraphState,
      s2scanState,
      translateStatus: (s) => "ST:" + s,
      escapeHtml: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
      energyGenCandidateName: (frameIndex, role) => "cand_" + frameIndex + "_" + (role || "ts"),
      energyGraphFrameCandidateFor: (viewType, frameIndex) => { calls.frameCandidateFor++; return frameIndex === 3 ? { id: "c3", role: "ts" } : null; },
      energyInspectorMetadataRows: () => { calls.metaRows++; return "<dl></dl>"; },
      renderTrajectoryAnnotations: (opts) => {
        calls.trajectory++;
        calls.lastOpts = opts;
        return { svg: "", bandSvg: "", bandHeight: 0, plotSvg: "" };
      },
    }, overrides || {});
    const sandbox = makeSandbox({
      document: {
        createElement: () => ({ getContext: () => ({ measureText: (txt) => ({ width: String(txt).length * 6 }) }), width: 0, height: 0 }),
      },
    });
    loadScript("js/visualization/energy_chart_renderer.js", sandbox);
    const mod = sandbox.window.ACPVisualization.energyChart;
    mod.init(deps);
    return { sandbox, mod, deps, calls };
  }

  function sampleData() {
    return {
      view_type: "energy",
      metadata: { default_x_axis_mode: "scanned_coordinate" },
      nodes: [
        { id: "n1", frame_index: 1, energy: -10.5, status: "ok", scanned_coordinate: 1.0 },
        { id: "n2", frame_index: 2, energy: -8.25, status: "ok", scanned_coordinate: 1.5 },
        { id: "n3", frame_index: 3, energy: -12.125, status: "failed", scanned_coordinate: 2.0 },
        { id: "n4", frame_index: 4, energy: -11.0, status: "ok", scanned_coordinate: 2.5 },
      ],
      series: [ { id: "s1", label: "S0 energy", unit: "kcal/mol", values: [-10.5, -8.25, -12.125, -11.0], x_values: [1.0, 1.5, 2.0, 2.5] } ],
      annotations: [ { id: "ann_ts_3", type: "ts", frame_index: 3, y: -12.1 } ],
    };
  }

  await test("renderer renders full SVG with series, points, axes, clip, units (deps complete)", () => {
    const { mod, deps } = loadRenderer();
    const svg = mod.energyGraphBuildSvg(sampleData());
    assert(typeof svg === "string" && svg.indexOf("<svg") !== -1, "svg root present");
    assert(svg.indexOf("kcal/mol") !== -1, "series unit preserved");
    assert(svg.indexOf('data-energy-node="n1"') !== -1, "node id preserved (frame identity)");
    assert(svg.indexOf("clipPath") !== -1, "clip path emitted");
    assert(svg.indexOf("M") !== -1, "path drawing emitted");
    assertEq(deps.energyGraphState.currentDomain.x.length, 2, "domain cached on shared state object");
  });

  await test("empty and undrawable payloads keep legacy empty-state markup (no fake success)", () => {
    const { mod } = loadRenderer();
    const empty = mod.energyGraphBuildSvg({ nodes: [], series: null });
    assert(empty.indexOf("energy-empty") !== -1, "empty state markup");
    const noPoints = mod.energyGraphBuildSvg({ nodes: [{ id: "n1", frame_index: 1, energy: null, status: "ok" }], series: { values: [null], x_values: [1] } });
    assert(noPoints.indexOf("energy-empty") !== -1, "undrawable -> empty state, not zero-filled");
  });

  await test("stable identity semantics: selection by node id, frame lookup, failed-point marker", () => {
    const { mod } = loadRenderer();
    const data = sampleData();
    const node = mod.energyGraphSelectedNode(data);
    assertEq(node && node.id, "n1", "selected node found by id");
    assertEq(mod.energyGraphFrameIndexForNode(data, "n3"), 3, "frame_index for node id");
    const svg = mod.energyGraphBuildSvg(data);
    assert(svg.indexOf("failed") !== -1, "failed node marked (status preserved)");
  });

  await test("helpers stay pure/deterministic: measure fallback, number format, nice ticks", () => {
    const { mod } = loadRenderer();
    assertEq(mod.energyGraphFormatNumber(-12.125, 2), "-12.13");
    const ticks = mod.energyChartNiceTicks(0, 10, 5, false);
    assert(ticks.values.length >= 4 && ticks.values.length <= 7, "reasonable tick count");
    assert(mod.energyGraphMeasureText("abc", 11, false) > 0, "measure works with canvas stub");
  });

  await test("annotation projection + candidate band geometry run without page globals", () => {
    const { mod, calls, deps } = loadRenderer();
    const data = sampleData();
    const projected = mod.energyProjectCandidatesAsAnnotations(data);
    assertEq(Array.isArray(projected), true);
    const svg = mod.energyGraphBuildSvg(data);
    // Annotation markers are embedded by the page-side renderTrajectoryAnnotations
    // renderer; the module hands it the merged annotation set via annotationOpts —
    // assert the stable id + live shared-state reads arrive intact.
    assert(calls.trajectory >= 1, "trajectory annotation renderer invoked");
    const opts = calls.lastOpts || {};
    assert((opts.annotations || []).some(function (a) { return String(a.id) === "ann_ts_3"; }),
      "ts annotation id passed through with stable identity");
    assertEq(opts.selectionLocked, false, "s2scanState read is a live shared-object view");
    assertEq(opts.selectedNodeId, "n1", "selection passed via opts, not global task read");
    assert(svg.indexOf("M") !== -1, "chart path still rendered");
  });

  await test("module exposes only public API; init twice is safe; no network/3Dmol/task-state globals", () => {
    const { sandbox, mod } = loadRenderer();
    mod.init({}); // re-init with empty deps must not throw at init time
    const api = Object.keys(mod);
    assert(api.indexOf("init") !== -1 && api.indexOf("energyGraphBuildSvg") !== -1);
    assert(api.indexOf("energyCandidateCardMarkup") === -1, "task-state-coupled markup NOT moved");
    const raw = require("fs").readFileSync(require("path").resolve(__dirname, "../visualization/energy_chart_renderer.js"), "utf8");
    // Strip comments: the header doc legitimately *mentions* the boundary terms.
    const codeOnly = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const bad of ["fetch(", "EventSource", "$3Dmol", "selectedJobId", "jobsCache", "lastSelectedJob"]) {
      assert(codeOnly.indexOf(bad) === -1, "forbidden reference in module code: " + bad);
    }
  });
};
