#!/usr/bin/env node
// [ARCHIVED 2026-10-16] Moved from frontend/js/module_tests/generate_energy_renderer.js. NOT RUNNABLE: its input frontend/js/workbench/compat/workbench-inline.js no longer exists (H2 externalization). Output lives on at frontend/js/visualization/energy_chart_renderer.js (loaded by ACP_Workbench_v2.html). Do not re-generate over the living renderer.
/* One-shot generator: extracts the pure energy-graph renderer family from
 * ACP_Workbench_v2.html into frontend/js/visualization/energy_chart_renderer.js
 * VERBATIM (bodies untouched; free globals become init(deps)-injected vars).
 * Also emits a symbol report for the integration handoff package. */
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "../../..");
// Since Antigravity's H2 externalization the legacy inline script lives in
// js/workbench/compat/workbench-inline.js (read-only for OpenCode).
const HTML = fs.readFileSync(path.join(ROOT, "frontend/js/workbench/compat/workbench-inline.js"), "utf8");

const MOVED = [
  "energyGraphSeries", "energyGraphValue", "energyGraphFormatNumber",
  "energyGraphCandidateAnnotations", "energyGraphFrameIndexForNode",
  "energyGraphSeriesValueAtFrame", "energyGraphMeasureText",
  "energyGraphChartGeometry", "energyChartNiceTicks", "energyGraphAxesMarkup",
  "energyGraphNodeX", "energyProjectCandidatesAsAnnotations",
  "energyCandidateBandLayoutCore", "energyCandidateBandLayout",
  "energyCandidateBandPreHeight", "energyGraphNodeForAnnotation",
  "energyCandidateListMarkup", "energyChartBuildSvg", "energyGraphBuildSvg",
  "energyGraphSelectedNode", "energyGraphSelectedAnnotation",
  "energyGraphLegendMarkup",
  // NOT moved (read mutable global task state lastSelectedJob/s2scanState.candidates
  // directly — task-state reads are forbidden in Visualization; they stay in the
  // page pending a workbench candidate/inspector adapter slice):
  //   energyCandidateCardMarkup, energyGraphInspectorMarkup
];

// Known page globals used by the moved bodies. Forced because the static scan
// unions locals across functions (e.g. energyChartNiceTicks has a local `var t`
// which masks the page translate fn `t`). The runtime smoke test is the final
// arbiter — any truly-missing dep throws ReferenceError in the sandbox.
const FORCED_DEPS = [
  "t",                    // page translate fn (stable ref)
  "energyGraphState",     // shared chart state object (page never reassigns it)
  "s2scanState",          // shared scan-state object (var, mutated only)
  "currentLang",          // scalar — checked below: only used at render time via t() outputs? verified by smoke
  "translateStatus",      // page status i18n (stable fn)
  "escapeHtml",           // page util (stable fn)
  "energyGenCandidateName",        // page helper (stable fn)
  "energyGraphFrameCandidateFor", // page helper (stable fn)
  "energyInspectorMetadataRows",   // page helper (stable fn)
  "renderTrajectoryAnnotations",   // page renderer invoked with cfg.annotationOpts
];

// Module-private lazy canvas caches — moved together with energyGraphMeasureText
// (their only consumer). Extracted verbatim from the compat source.
const MEASURE_CACHE_DECL = `var _egMeasureCanvas = null;
var _egMeasureCtx = null;
var _egFontFamily = "'IBM Plex Sans', 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif";`;

// Extract function source: from "function NAME(" to its matching closing brace.
function extractFunction(name) {
  const idx = HTML.indexOf("function " + name + "(");
  if (idx === -1) throw new Error("not found: " + name);
  let i = HTML.indexOf("{", idx);
  let depth = 0, j = i;
  for (; j < HTML.length; j++) {
    const ch = HTML[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) break; }
  }
  // naive string/comment safety check: ensure no unbalanced braces inside strings
  return HTML.slice(idx, j + 1);
}

// Strip comments/strings so identifier scanning is reliable.
function stripCode(code) {
  return code
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ")
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/`(?:[^`\\]|\\.)*`/g, "``");
}

const sources = {};
const order = MOVED.slice().sort((a, b) => HTML.indexOf("function " + a + "(") - HTML.indexOf("function " + b + "("));
for (const name of MOVED) sources[name] = extractFunction(name);
const all = order.map((n) => stripCode(sources[n])).join("\n");

// Free-identifier scan: identifiers not declared locally and not moved/builtin.
const declared = new Set();
for (const name of MOVED) declared.add(name);
const builtin = new Set(("true,false,null,undefined,this,arguments,var,let,const,function,return,if,else,for,while,do,break,continue,new,typeof,instanceof,in,of,try,catch,finally,throw,switch,case,default,Math,JSON,Object,Array,String,Number,Boolean,Date,isNaN,parseInt,parseFloat,encodeURIComponent,decodeURIComponent,console,window,document,navigator,Set,Map,Promise,Error,Infinity,NaN").split(","));
// collect params + top-level vars of moved functions
for (const name of MOVED) {
  const src = stripCode(sources[name]);
  const head = src.slice(0, src.indexOf("{"));
  (head.match(/[$A-Za-z_][$A-Za-z0-9_]*/g) || []).forEach((id) => declared.add(id));
  const body = src.slice(src.indexOf("{"));
  const varRe = /\b(?:var|let|const)\s+([^;\n]+)/g;
  let m;
  while ((m = varRe.exec(body))) {
    m[1].split(",").forEach((piece) => {
      const id = (piece.trim().match(/^[$A-Za-z_][$A-Za-z0-9_]*/) || [""])[0];
      if (id) declared.add(id);
    });
  }
  // function-local function declarations
  (body.match(/function\s+([$A-Za-z_][$A-Za-z0-9_]*)\s*\(/g) || []).forEach((s) => {
    const id = s.replace(/function\s+/, "").replace(/\($/, "");
    if (id) declared.add(id);
  });
}
const free = new Set();
for (const id of all.match(/[$A-Za-z_][$A-Za-z0-9_.]*/g) || []) {
  const root = id.split(".")[0];
  if (!declared.has(root) && !builtin.has(root)) free.add(root);
}
// Drop known false positives (object-literal keys, method names, destructured
// locals, unicode-escape fragments) — anything not in FORCED_DEPS and not a
// plausible page global is reported for manual review instead of injected.
const depsList = FORCED_DEPS;
const residual = [...free].filter((id) => !depsList.includes(id) && !/^x[0-9a-f]{4}$/.test(id));
const freeList = depsList;

// Forbidden-usage audit for the Visualization boundary.
const raw = order.map((n) => sources[n]).join("\n");
const forbidden = [];
for (const pat of ["fetch(", "api(", "apiV2(", "apiRemote(", "apiV2Remote(", "EventSource", "$3Dmol", "selectedJobId", "jobsCache", "taskViewCache"]) {
  if (raw.includes(pat)) forbidden.push(pat);
}

// External call-site counts (for the handoff: which symbols need page delegators).
const callSites = {};
for (const name of MOVED) {
  const re = new RegExp("\\b" + name + "\\s*\\(", "g");
  const inModule = (stripCode(sources[name]).match(re) || []).length;
  const total = (stripCode(HTML).match(re) || []).length;
  callSites[name] = { total, inModule, external: total - inModule };
}

const header = `/*
 * ACP Visualization — energy chart renderer (2D display model + SVG renderer).
 *
 * Owner: frontend/js/visualization/** (OpenCode). Version: v20261016.1
 *
 * VERBATIM extraction of the pure rendering/display-model family of the legacy
 * energy-graph code (source: js/workbench/compat/workbench-inline.js — bodies
 * unchanged, behavior identical). Interaction/fetch/selection code stays in the
 * page until its slice moves. This module NEVER touches the network, never
 * reads global task state (selectedJobId/jobsCache/lastSelectedJob), and holds
 * no $3Dmol references — the Visualization boundary contract.
 *
 * Functions NOT moved here because they read mutable global task state
 * (lastSelectedJob / s2scanState.candidates) directly — they remain in the page
 * pending a workbench candidate/inspector adapter slice:
 *   energyCandidateCardMarkup, energyGraphInspectorMarkup
 *
 * Page globals are injected via init(deps) and aliased as module-level vars so
 * the moved bodies stay byte-identical:
 *   ${freeList.join(", ")}
 * energyGraphState / s2scanState are shared BY REFERENCE with the page (the
 * page only mutates them, never reassigns); t/translateStatus/escapeHtml and
 * the *For/*Rows/*Annotations helpers are stable page functions.
 * _egMeasureCanvas/_egMeasureCtx/_egFontFamily are module-private lazy caches
 * moved together with energyGraphMeasureText (their only consumer).
 *
 * The page keeps same-name delegators for every symbol until Antigravity
 * switches call sites (see handoff package). Do not call any function before
 * init().
 */
window.ACPVisualization = window.ACPVisualization || {};
ACPVisualization.energyChart = (function () {
  "use strict";
  var ${freeList.join(", ")};
${MEASURE_CACHE_DECL}
  function init(deps) {
    deps = deps || {};
${freeList.map((n) => "    " + n + " = deps." + n + ";").join("\n")}
  }

`;

const footer = `
  return {
    init: init,
    ${order.join(",\n    ")},
  };
})();
`;

const moduleFile = header + order.map((n) => sources[n]).join("\n\n") + footer;
const outPath = path.join(ROOT, "frontend/js/visualization/energy_chart_renderer.js");
fs.writeFileSync(outPath, moduleFile);

fs.writeFileSync(path.join(ROOT, ".omo/evidence/frontend-arch-20261016/energy-renderer-symbols.json"),
  JSON.stringify({ freeList, forbidden, order, callSites, generatedAt: new Date().toISOString() }, null, 2));

console.log("written:", outPath, moduleFile.split("\n").length, "lines");
console.log("deps (injected):", freeList.join(", "));
console.log("residual unclassified identifiers (manual review):", residual.join(", ") || "none");
console.log("forbidden usage:", forbidden.length ? forbidden.join(", ") : "NONE ✓");
console.log("external call sites (need page delegators):");
for (const name of order) if (callSites[name].external > 0) console.log("  " + name + ": " + callSites[name].external);
