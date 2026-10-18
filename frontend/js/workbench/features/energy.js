// Keep legacy call sites on the single renderer, with live language/state inputs.
/* R4: candidate-review state is owned by ACPWorkbench.controllers.candidateReview.
   The chart renderer keeps its live shared-object read via this projection —
   same fields the renderer always read off s2scanState, sourced from the
   single owner (fresh getState() on every property read). */
function energyReviewSharedView() {
  var ctl = candidateReviewCtl();
  return {
    get selectionLocked() { return ctl ? !!ctl.getState().selectionLocked : false; },
    get candidates() { return ctl ? ctl.getState().candidates : []; },
    get dirty() { return ctl ? !!ctl.getState().dirty : false; },
  };
}

function candidateReviewCtl() {
  return (window.ACPWorkbench && ACPWorkbench.controllers && ACPWorkbench.controllers.candidateReview) || null;
}

/* Snapshot of the candidate-review state (single owner). Falls back to the
   controller's default shape if the controller script is not loaded, so the
   page degrades to "no review state" exactly like the former empty literals. */
function candidateReviewState() {
  var ctl = candidateReviewCtl();
  if (ctl) return ctl.getState();
  return {
    selectedTs: [], selectedInt: [],
    reviewConfirmed: false, selectionLocked: false, selectionEditMode: false,
    candidates: [], dirty: false, savedAt: null, reviewStatus: "", revision: 0,
  };
}

function getEnergyChartRenderer() {
  var renderer = ACPVisualization.energyChart;
  renderer.init({ t: t, energyGraphState: energyGraphState, s2scanState: energyReviewSharedView(), currentLang: currentLang, translateStatus: translateStatus, escapeHtml: escapeHtml, energyGenCandidateName: energyGenCandidateName, energyGraphFrameCandidateFor: energyGraphFrameCandidateFor, energyInspectorMetadataRows: energyInspectorMetadataRows, renderTrajectoryAnnotations: renderTrajectoryAnnotations });
  return renderer;
}

/* ── Generic Inspector Field Specs ── */
var ENERGY_INSPECTOR_FIELD_SPECS = {
  scan: [
    { key: "target_coordinate", i18nKey: "\u76ee\u6807\u5750\u6807", unit: "", decimals: 3 },
    { key: "actual_coordinate", i18nKey: "\u5b9e\u9645\u5750\u6807", unit: "", decimals: 3 },
    { key: "scan_energy", i18nKey: "\u626b\u63cf\u80fd", unit: "", decimals: 4 },
    { key: "sp_energy", i18nKey: "\u5355\u70b9\u80fd", unit: "", decimals: 4 },
    { key: "sp_status", i18nKey: "SP \u72b6\u6001", unit: "", decimals: -1 },
  ],
  scan_trajectory: [
    { key: "coordinate_values", i18nKey: "\u5750\u6807\u503c", unit: "", decimals: 3 },
    { key: "energy_hartree", i18nKey: "\u80fd\u91cf (Ha)", unit: "", decimals: 6 },
  ],
  optimization: [
    { key: "cycle", i18nKey: "\u4f18\u5316\u5468\u671f", unit: "", decimals: 0 },
    { key: "scf_energy", i18nKey: "SCF \u80fd\u91cf", unit: "Ha", decimals: 6 },
    { key: "delta_energy", i18nKey: "\u0394E", unit: "kcal/mol", decimals: 3 },
    { key: "rms_gradient", i18nKey: "RMS \u68af\u5ea6", unit: "Eh/Bohr", decimals: 6 },
    { key: "max_gradient", i18nKey: "MAX \u68af\u5ea6", unit: "Eh/Bohr", decimals: 6 },
    { key: "rms_displacement", i18nKey: "RMS \u4f4d\u79fb", unit: "bohr", decimals: 6 },
    { key: "max_displacement", i18nKey: "MAX \u4f4d\u79fb", unit: "bohr", decimals: 6 },
    { key: "scf_iterations", i18nKey: "SCF \u8fed\u4ee3", unit: "", decimals: 0 },
  ],
  conformer: [
    { key: "rank", i18nKey: "\u6392\u540d", unit: "", decimals: 0 },
    { key: "gibbs_energy", i18nKey: "Gibbs \u80fd\u91cf", unit: "Ha", decimals: 6 },
    { key: "relative_energy_kcal", i18nKey: "\u76f8\u5bf9\u80fd\u91cf", unit: "kcal/mol", decimals: 2 },
    { key: "boltzmann_weight", i18nKey: "Boltzmann \u6743\u91cd", unit: "", decimals: 4 },
  ],
  sampling: [
    { key: "time_ps", i18nKey: "\u6a21\u62df\u65f6\u95f4", unit: "ps", decimals: 2 },
    { key: "step", i18nKey: "MD \u6b65", unit: "", decimals: 0 },
    { key: "energy_kcal_mol", i18nKey: "\u52bf\u80fd", unit: "kcal/mol", decimals: 2 },
    { key: "relative_energy_kcal_mol", i18nKey: "\u76f8\u5bf9\u80fd\u91cf", unit: "kcal/mol", decimals: 2 },
    { key: "basin_id", i18nKey: "\u76c6\u5730 ID", unit: "", decimals: 0 },
  ],
};

var _INSPECTOR_COMMON_KEYS = {
  status: 1, frame_index: 1, target_coordinate: 1, actual_coordinate: 1,
  actual_coordinates: 1, target_coordinates: 1, scan_energy: 1, sp_energy: 1,
  sp_status: 1, cycle: 1, scf_energy: 1, delta_energy: 1, rms_gradient: 1,
  max_gradient: 1, rms_displacement: 1, max_displacement: 1, scf_iterations: 1,
  rank: 1, gibbs_energy: 1, relative_energy_kcal: 1, boltzmann_weight: 1,
  time_ps: 1, step: 1, energy_kcal_mol: 1, relative_energy_kcal_mol: 1,
  basin_id: 1, coordinate_values: 1, energy_hartree: 1, review: 1,
  weight: 1, gibbs_hartree: 1,
};

var _INSPECTOR_KEY_ALIASES = { boltzmann_weight: "weight", gibbs_energy: "gibbs_hartree" };

/* ── Frame-candidate state (server-authoritative via /frame-candidates) ── */
function energyGraphFrameCandidateFor(viewType, frameIndex) {
  var fc = energyGraphState.frameCandidates;
  if (!fc || !Array.isArray(fc.list)) return null;
  for (var i = 0; i < fc.list.length; i++) {
    var c = fc.list[i];
    if (String(c.view_type || "") === String(viewType || "") && Number(c.frame_index) === Number(frameIndex)) return c;
  }
  return null;
}

function energyGraphApplyFrameCandidates(payload) {
  if (!payload) return;
  var src = Array.isArray(payload.candidates) ? payload.candidates : (Array.isArray(payload.list) ? payload.list : []);
  energyGraphState.frameCandidates = {
    revision: Number(payload.revision) || 0,
    list: src,
  };
}

async function energyGraphFetchFrameCandidates(jobId) {
  try {
    var resp = await api("/jobs/" + encodeURIComponent(jobId) + "/frame-candidates");
    return { revision: Number(resp && resp.revision) || 0, list: (resp && Array.isArray(resp.candidates)) ? resp.candidates : [] };
  } catch (e) { return null; }
}

/* ── R5/T23 task-session identity for candidate/result writes ──
 * Candidate saves and PES review/restore are scoped to the task selection
 * that started them. Capture the identity before the request and apply the
 * response only while that same job/session is still displayed — the same
 * token rule files-logs.js applies to /files and /logs tails. The token is
 * the taskSession controller's monotonic session token (authoritative under
 * A→B→A and same-job reselect); the jobId compare covers the no-controller
 * fallback. Rejecting (rather than applying late) keeps a stale A response
 * from repopulating B's candidate review state. */
function energyGraphResultSession(jobId) {
  var cur = null;
  if (window.ACPWorkbench && ACPWorkbench.controllers && ACPWorkbench.controllers.taskSession) {
    cur = ACPWorkbench.controllers.taskSession.current();
  }
  return {
    jobId: String(jobId == null ? (energyGraphState.jobId || "") : jobId),
    token: cur ? cur.token : null,
  };
}

function energyGraphResultSessionCurrent(session) {
  if (!session) return false;
  if (String(energyGraphState.jobId || "") !== String(session.jobId)) return false;
  var cur = null;
  if (window.ACPWorkbench && ACPWorkbench.controllers && ACPWorkbench.controllers.taskSession) {
    cur = ACPWorkbench.controllers.taskSession.current();
  }
  var nowToken = cur ? cur.token : null;
  return nowToken === session.token;
}

/** Invalidate any in-flight energy workspace / geometry request. Called by
 * the page's task-session reset so a response started for the previous job
 * cannot land between the reset and the new job's first load. */
function energyGraphInvalidateInFlight() {
  energyGraphState.requestToken = (Number(energyGraphState.requestToken) || 0) + 1;
  if (energyGraphState.abortController) {
    try { energyGraphState.abortController.abort(); } catch (e) {}
    energyGraphState.abortController = null;
  }
  if (energyGraphState.structureAbort) {
    try { energyGraphState.structureAbort.abort(); } catch (e) {}
    energyGraphState.structureAbort = null;
  }
}

/* ── Shared-loader delegation (phase D, todo 38) ──
   The energy mini canvas registers its load adapter with the SHARED
   geometry loader in structure_viewer.js (one parse/style/load path for
   every canvas). This canvas keeps its own element + scene instance; only
   the duplicate load logic is removed. The lazy creation below is
   the SAME creation point that always lived here — not a new viewer.
   T9: energyGraphState.viewer holds a bounded ACPVisualization.scene
   instance (never a raw 3Dmol handle); all model/style/teardown/PNG/framing
   access goes through the instance's restricted API. */
if (window.ACPStructureViewer && typeof window.ACPStructureViewer.registerCanvasLoader === "function") {
  window.ACPStructureViewer.registerCanvasLoader("energy-mini", function(xyzText, styleSpec) {
    var container = document.getElementById("energy-structure-viewer");
    if (!container) return false;
    if (energyGraphState.viewer && container.querySelector("canvas")) {
      energyGraphState.viewer.removeAllModels();
    } else {
      container.innerHTML = "";
      /* scene.create returns null when the 3Dmol library (or the scene
         module) is missing — the loader degrades to false with no throw,
         keeping the "3D 查看器未加载" empty state. */
      var sceneNs = ACPVisualization.scene;
      energyGraphState.viewer = (sceneNs && typeof sceneNs.create === "function")
        ? sceneNs.create({ container: container, options: { backgroundColor: "0x0e1419", antialias: true } })
        : null;
      if (!energyGraphState.viewer) return false;
    }
    energyGraphState.viewer.addModel(xyzText, "xyz");
    energyGraphState.viewer.setStyle({}, styleSpec);
    return true;
  });
  window.ACPStructureViewer._canvasViewerImpl = function(canvasId) {
    return canvasId === "energy-mini" ? energyGraphState.viewer : null;
  };
}

/* ── Unified Geometry Loader ── */
async function energyGraphLoadFrameGeometry(node) {
  var data = energyGraphState.data;
  var container = document.getElementById("energy-structure-viewer");
  if (!data || !container || !node) return;
  var frameIndex = node.frame_index;
  if (frameIndex == null) return;
  if (Number(frameIndex) === energyGraphState.loadedFrameIndex && String(node.id || "") === energyGraphState.loadedFrameId && energyGraphState.viewer && container.isConnected && container.querySelector("canvas")) {
    var _fs = energyGraphState.structureFraming;
    if (_fs.settled && (container.clientWidth !== _fs.lastW || container.clientHeight !== _fs.lastH)) {
      var _tk = energyGraphState.requestToken;
      scheduleViewerFraming(container, _fs, function() { return energyGraphState.viewer; }, { isValid: function() { return _tk === energyGraphState.requestToken; } });
    }
    return;
  }
  if (energyGraphState.structureAbort) { energyGraphState.structureAbort.abort(); energyGraphState.structureAbort = null; }
  var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
  energyGraphState.structureAbort = ac;
  var tk = energyGraphState.requestToken;
  var jobId = energyGraphState.jobId;
  var view = String(data.view_type || "");
  try {
    var xyz = "";
    var geoRef = node.geometry_ref || "";
    if (view === "irc") {
      try {
        var ircDirection = String((node.metadata && node.metadata.direction) || "");
        var ircUrl = "/jobs/" + encodeURIComponent(jobId) + "/irc/frames/" + encodeURIComponent(ircDirection) + "/" + encodeURIComponent(frameIndex) + "/geometry";
        var ircResp = await api(ircUrl, ac ? { signal: ac.signal } : {});
        if (tk !== energyGraphState.requestToken) return;
        if (ircResp && ircResp.xyz) xyz = ircResp.xyz;
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
      }
    }
    /* Each source runs in its own try/catch so one failure (e.g. a 404 from
       the files endpoint) never blocks the remaining fallbacks; AbortError
       is rethrown so cancellation still short-circuits the whole load. */
    /* (1) optimization → dedicated frame endpoint FIRST: geometry_ref is
       relative to the trajectory file (e.g. WORK/03_OPT/...), so the
       job-root files endpoint cannot resolve it and would 404. */
    if (view === "optimization") {
      try {
        var optUrl = "/jobs/" + encodeURIComponent(jobId) + "/optimization/frame/" + encodeURIComponent(frameIndex);
        if (data.metadata && data.metadata.item_id) optUrl += "?item_id=" + encodeURIComponent(String(data.metadata.item_id));
        var optResp = await api(optUrl, ac ? { signal: ac.signal } : {});
        if (tk !== energyGraphState.requestToken) return;
        if (optResp && optResp.xyz) xyz = optResp.xyz;
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
      }
    }
    /* (2) geometry_ref → files endpoint */
    if (!xyz && geoRef && view !== "irc") {
      try {
        var fileResp = await api("/jobs/" + encodeURIComponent(jobId) + "/files/" + encodeURIComponent(geoRef), ac ? { signal: ac.signal } : {});
        if (tk !== energyGraphState.requestToken) return;
        if (fileResp && typeof fileResp === "string") {
          xyz = fileResp;
        } else if (fileResp && fileResp.xyz) {
          xyz = fileResp.xyz;
        }
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
      }
    }
    /* (3) sampling → sampling frame endpoint */
    if (!xyz && view === "sampling") {
      try {
        var sampUrl = "/jobs/" + encodeURIComponent(jobId) + "/sampling/frame/" + encodeURIComponent(frameIndex);
        var sampResp = await api(sampUrl, ac ? { signal: ac.signal } : {});
        if (tk !== energyGraphState.requestToken) return;
        if (sampResp && sampResp.xyz) xyz = sampResp.xyz;
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
      }
    }
    /* (4) fallback: legacy s2/frame for scan */
    if (!xyz && (view === "scan" || view === "scan_trajectory")) {
      try {
        var s2Url = "/jobs/" + encodeURIComponent(jobId) + "/s2/frame/" + encodeURIComponent(frameIndex);
        var s2Resp = await apiRemote(s2Url, ac ? { signal: ac.signal } : {});
        if (tk !== energyGraphState.requestToken) return;
        if (s2Resp && s2Resp.xyz) xyz = s2Resp.xyz;
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
      }
    }
    if (!container.isConnected) return;
    if (!xyz) {
      container.className = "energy-structure-viewer empty";
      container.textContent = "\u5f53\u524d\u5e27\u6ca1\u6709\u53ef\u7528 XYZ \u7ed3\u6784";
      return;
    }
    container.className = "energy-structure-viewer";
    /* Phase D (todo 38): the LOAD goes through the shared geometry loader
       (style preset from the shared store). The frame-endpoint FETCHes above
       are energy-viewer-specific and stay here; only the duplicated
       parse/style/model-load logic was removed. */
    var sharedLoad = window.ACPStructureViewer ? window.ACPStructureViewer.sharedLoadGeometry : null;
    var sharedResult = null;
    try {
      sharedResult = sharedLoad ? sharedLoad.call(window.ACPStructureViewer, xyz, { canvasId: "energy-mini", source: "energy_graph" }) : null;
    } catch (e) { sharedResult = null; }
    if (!sharedResult || !sharedResult.loaded || !energyGraphState.viewer) {
      container.className = "energy-structure-viewer empty";
      container.textContent = "3D \u67e5\u770b\u5668\u672a\u52a0\u8f7d";
      return;
    }
    /* Reframe AFTER layout settles using the shared framing helper.
       Token+isConnected guards keep a stale load from clobbering a newer one. */
    scheduleViewerFraming(container, energyGraphState.structureFraming, function() { return energyGraphState.viewer; }, { isValid: function() { return tk === energyGraphState.requestToken; } });
    energyGraphState.loadedFrameIndex = Number(frameIndex);
    energyGraphState.loadedFrameId = String(node.id || "");
  } catch (e) {
    if (e && e.name === "AbortError") return;
    if (tk !== energyGraphState.requestToken) return;
    if (!container.isConnected) return;
    container.className = "energy-structure-viewer empty";
    container.textContent = e.message || String(e);
  }
}

/* ── Fetch Frame XYZ (for export, no viewer update) ── */
async function energyGraphFetchFrameXyz(node) {
  var data = energyGraphState.data;
  if (!data || !node) return null;
  var frameIndex = node.frame_index;
  if (frameIndex == null) return null;
  var jobId = energyGraphState.jobId;
  var view = String(data.view_type || "");
  var geoRef = node.geometry_ref || "";
  if (view === "irc") {
    try {
      var ircDirection = String((node.metadata && node.metadata.direction) || "");
      var ircUrl = "/jobs/" + encodeURIComponent(jobId) + "/irc/frames/" + encodeURIComponent(ircDirection) + "/" + encodeURIComponent(frameIndex) + "/geometry";
      var ircResp = await api(ircUrl);
      if (ircResp && ircResp.xyz) return ircResp.xyz;
    } catch (e) {}
    return null;
  }
  if (geoRef) {
    try {
      var fileResp = await api("/jobs/" + encodeURIComponent(jobId) + "/files/" + encodeURIComponent(geoRef));
      if (fileResp && typeof fileResp === "string") return fileResp;
      if (fileResp && fileResp.xyz) return fileResp.xyz;
    } catch (e) {}
  }
  if (view === "optimization") {
    try {
      var optUrl = "/jobs/" + encodeURIComponent(jobId) + "/optimization/frame/" + encodeURIComponent(frameIndex);
      if (data.metadata && data.metadata.item_id) optUrl += "?item_id=" + encodeURIComponent(String(data.metadata.item_id));
      var optResp = await api(optUrl);
      if (optResp && optResp.xyz) return optResp.xyz;
    } catch (e) {}
  }
  if (view === "sampling") {
    try {
      var sampResp = await api("/jobs/" + encodeURIComponent(jobId) + "/sampling/frame/" + encodeURIComponent(frameIndex));
      if (sampResp && sampResp.xyz) return sampResp.xyz;
    } catch (e) {}
  }
  if (view === "scan" || view === "scan_trajectory") {
    try {
      var s2Resp = await apiRemote("/jobs/" + encodeURIComponent(jobId) + "/s2/frame/" + encodeURIComponent(frameIndex));
      if (s2Resp && s2Resp.xyz) return s2Resp.xyz;
    } catch (e) {}
  }
  return null;
}

/* ── Generic Metadata Renderer ── */
function energyInspectorMetadataRows(data, node, metadata) {
  var view = String(data.view_type || "");
  var specs = ENERGY_INSPECTOR_FIELD_SPECS[view] || [];
  var html = "";
  specs.forEach(function(spec) {
    var val = metadata[spec.key];
    if (val == null && _INSPECTOR_KEY_ALIASES[spec.key]) val = metadata[_INSPECTOR_KEY_ALIASES[spec.key]];
    if (val == null) return;
    if (spec.decimals >= 0 && typeof val === "number") {
      html += '<dt>' + escapeHtml(spec.i18nKey) + '</dt><dd>' + escapeHtml(energyGraphFormatNumber(val, spec.decimals)) + (spec.unit ? ' ' + escapeHtml(spec.unit) : '') + '</dd>';
    } else {
      html += '<dt>' + escapeHtml(spec.i18nKey) + '</dt><dd>' + escapeHtml(String(val)) + '</dd>';
    }
  });
  var known = _INSPECTOR_COMMON_KEYS;
  Object.keys(metadata).forEach(function(key) {
    if (known[key]) return;
    var val = metadata[key];
    if (val == null || typeof val === "object") return;
    html += '<dt>' + escapeHtml(key) + '</dt><dd>' + escapeHtml(String(val)) + '</dd>';
  });
  return html;
}

// ── Energy and inspector charts ──
function mechanismRoleLabel(role) {
  const key = "mech.role." + String(role || "unknown").toLowerCase();
  const translated = t(key);
  return translated === key ? (role || t("mech.role.unknown")) : translated;
}

function formatMechanismNumber(value, digits) {
  if (typeof value !== "number" || !isFinite(value)) return t("common.none");
  return value.toFixed(digits == null ? 2 : digits);
}

function stringifyMechanismPayload(value) {
  if (value == null) return t("common.none");
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (e) {
    return String(value);
  }
}

function summarizeDecisionEvidence(decision) {
  let payload = decision && decision.payload ? decision.payload : {};
  if (typeof payload === "string") {
    try { payload = JSON.parse(payload); } catch (e) { return payload; }
  }
  const lines = [];
  if (payload.evidence_summary) lines.push(String(payload.evidence_summary));
  if (Array.isArray(payload.quality_gates) && payload.quality_gates.length) {
    lines.push(payload.quality_gates.map(function(gate) {
      return [gate.gate_id || "gate", gate.status || "unknown", gate.suggested_action || ""].filter(Boolean).join(" · ");
    }).join("\n"));
  }
  if (payload.energy_profile_snippet) lines.push(stringifyMechanismPayload(payload.energy_profile_snippet));
  if (payload.evidence) lines.push(stringifyMechanismPayload(payload.evidence));
  if (!lines.length) lines.push(stringifyMechanismPayload(payload));
  return lines.join("\n\n");
}

function getMechanismViewerContainer() {
  return document.getElementById("viewer-mechanism");
}

function getEnergyViewerContainer() { return document.getElementById("viewer-energy"); }

/* Unified header status helper — returns {color, text} for a projection data.status */
function energyHeaderStatusInfo(data) {
  var s = String((data && data.status) || "unknown").toLowerCase();
  var color = (s === "running" || s === "partial") ? "var(--accent)"
    : (s === "completed" || s === "converged" || s === "ready_for_review") ? "var(--green)"
    : (s === "failed" || s === "interrupted") ? "var(--red)"
    : "var(--text-tertiary)";
  var key = "energy.header.status." + s;
  var text = t(key);
  if (text === key) text = translateStatus(s);
  return { color: color, text: text };
}

/* Unified header strip markup: <title> · N 帧 · ● status */
function energyHeaderStripMarkup(data, titleFallback) {
  var title = escapeHtml(String((data && data.title) || titleFallback || t("tab.energy")));
  var series = energyGraphSeries(data);
  var seriesLabel = series ? escapeHtml(series.label || series.id) : '';
  var seriesUnit = series && series.unit ? escapeHtml(series.unit) : '';
  var seriesSuffix = seriesLabel ? ' \u00b7 ' + seriesLabel + (seriesUnit ? ' (' + seriesUnit + ')' : '') : '';
  var frameCount = (data && Array.isArray(data.nodes)) ? data.nodes.length : ((data && data.metadata && data.metadata.frame_count) || 0);
  var info = energyHeaderStatusInfo(data);
  return '<div class="energy-chart-head">' +
    '<div class="energy-chart-title">' + title + seriesSuffix + ' \u00b7 ' + frameCount + ' ' + t("energy.header.frames") + '</div>' +
    '<div class="energy-chart-subtitle" style="color:' + info.color + ';">\u25cf ' + escapeHtml(info.text) + '</div>' +
  '</div>';
}

function energyGraphRenderState(state, message) {
  var screen = getEnergyViewerContainer();
  if (!screen) return;
  energyGraphDestroyViewer();
  var icon = "";
  var title = "";
  var spinner = "";
  var retryBtn = "";
  if (state === "loading") {
    spinner = '<div class="energy-state-spinner"></div>';
    title = t("energy.loading");
  } else if (state === "error") {
    icon = '<div class="energy-state-icon">\u26a0</div>';
    title = t("energy.error");
    retryBtn = '<button class="btn ghost" data-energy-action="retry">' + t("energy.retry") + '</button>';
  } else if (state === "empty") {
    icon = '<div class="energy-state-icon">\u2610</div>';
    title = t("energy.empty");
  } else {
    icon = '<div class="energy-state-icon">\u2691</div>';
    title = t("energy.idle_prompt");
  }
  screen.innerHTML = '<div class="energy-state-card">' +
    spinner + icon +
    '<div class="energy-state-title">' + escapeHtml(title) + '</div>' +
    '<div class="energy-state-msg">' + escapeHtml(message || "") + '</div>' +
    retryBtn +
  '</div>';
  if (retryBtn) {
    var btn = screen.querySelector("[data-energy-action=\"retry\"]");
    if (btn) btn.addEventListener("click", function() { renderEnergyGraphWorkspace(lastSelectedJob); });
  }
}

function getPrimaryMechanismStudy(jobId) {
  const studies = mechanismStudiesByJob[String(jobId || "")] || [];
  if (!studies.length) return null;
  return studies.slice().sort(function(a, b) {
    const ap = a && a.n_decisions_pending ? 0 : 1;
    const bp = b && b.n_decisions_pending ? 0 : 1;
    if (ap !== bp) return ap - bp;
    const ad = new Date(a && (a.updated_at || a.created_at) || 0).getTime();
    const bd = new Date(b && (b.updated_at || b.created_at) || 0).getTime();
    return bd - ad;
  })[0];
}

async function fetchMechanismStudiesForJob(jobId, force) {
  if (!jobId) return [];
  // Cache-hit guard: without force, keep the fetch-once semantics; with
  // force (live refresh while a mechanism job runs) always re-fetch.
  if (!force && mechanismStudiesByJob[String(jobId)]) return mechanismStudiesByJob[String(jobId)];
  try {
    const body = await api("/mechanism-studies?job_id=" + encodeURIComponent(jobId));
    const studies = Array.isArray(body) ? body : (body && Array.isArray(body.studies) ? body.studies : []);
    mechanismStudiesByJob[String(jobId)] = studies;
    return studies;
  } catch (e) {
    // Failed re-fetch keeps the previous cache so chips don't flicker away.
    if (!mechanismStudiesByJob[String(jobId)]) mechanismStudiesByJob[String(jobId)] = [];
    return mechanismStudiesByJob[String(jobId)] || [];
  }
}

async function fetchMechanismStudyDetail(studyId, force) {
  if (!studyId) return null;
  if (!force && mechanismStudyDetailsById[String(studyId)]) return mechanismStudyDetailsById[String(studyId)];
  try {
    const body = await api("/mechanism-studies/" + encodeURIComponent(studyId));
    mechanismStudyDetailsById[String(studyId)] = body || null;
    return body || null;
  } catch (e) {
    return null;
  }
}

async function fetchMechanismStudyReport(studyId) {
  if (!studyId) return null;
  if (Object.prototype.hasOwnProperty.call(mechanismStudyReportsById, String(studyId))) {
    return mechanismStudyReportsById[String(studyId)];
  }
  try {
    const body = await api("/mechanism-studies/" + encodeURIComponent(studyId) + "/report");
    mechanismStudyReportsById[String(studyId)] = body || null;
    return body || null;
  } catch (e) {
    mechanismStudyReportsById[String(studyId)] = null;
    return null;
  }
}

function resetMechanismCachesForJob(jobId) {
  if (!jobId) return;
  delete mechanismStudiesByJob[String(jobId)];
  Object.keys(mechanismStudyDetailsById).forEach(function(key) {
    const detail = mechanismStudyDetailsById[key];
    if (detail && String(detail.job_id || "") === String(jobId)) delete mechanismStudyDetailsById[key];
  });
  Object.keys(mechanismStudyReportsById).forEach(function(key) {
    const detail = mechanismStudyDetailsById[key];
    if (!detail || String(detail.job_id || "") === String(jobId)) delete mechanismStudyReportsById[key];
  });
}

function jobIdOf(job) {
  return job ? String(job.id || job.job_id || "") : "";
}

function buildReactionNetworkSvg(report) {
  const network = report && report.reaction_network ? report.reaction_network : null;
  if (!network || !Array.isArray(network.nodes) || !network.nodes.length || !Array.isArray(network.edges) || !network.edges.length) return "";
  const nodes = network.nodes.slice();
  const edges = network.edges.slice();
  const nodeMap = {};
  nodes.forEach(function(node) { nodeMap[String(node.state_id)] = node; });
  const indegree = {};
  const outgoing = {};
  nodes.forEach(function(node) {
    indegree[String(node.state_id)] = 0;
    outgoing[String(node.state_id)] = [];
  });
  edges.forEach(function(edge) {
    const sourceId = String(edge.source_state_id || "");
    const sinkId = String(edge.sink_state_id || "");
    outgoing[sourceId] = outgoing[sourceId] || [];
    outgoing[sourceId].push(edge);
    indegree[sinkId] = (indegree[sinkId] || 0) + 1;
  });
  const depth = {};
  const queue = [];
  nodes.forEach(function(node) {
    const id = String(node.state_id);
    if (!indegree[id]) {
      depth[id] = 0;
      queue.push(id);
    }
  });
  if (!queue.length && nodes.length) {
    depth[String(nodes[0].state_id)] = 0;
    queue.push(String(nodes[0].state_id));
  }
  while (queue.length) {
    const id = queue.shift();
    const nextEdges = outgoing[id] || [];
    nextEdges.forEach(function(edge) {
      const sinkId = String(edge.sink_state_id || "");
      const nextDepth = (depth[id] || 0) + 1;
      if (depth[sinkId] == null || nextDepth > depth[sinkId]) depth[sinkId] = nextDepth;
      indegree[sinkId] = (indegree[sinkId] || 0) - 1;
      if (indegree[sinkId] <= 0) queue.push(sinkId);
    });
  }
  nodes.forEach(function(node) {
    const id = String(node.state_id);
    if (depth[id] == null) depth[id] = 0;
  });
  const byDepth = {};
  nodes.forEach(function(node) {
    const level = depth[String(node.state_id)] || 0;
    if (!byDepth[level]) byDepth[level] = [];
    byDepth[level].push(node);
  });
  Object.keys(byDepth).forEach(function(level) {
    byDepth[level].sort(function(a, b) { return String(a.state_id).localeCompare(String(b.state_id)); });
  });
  const pairCount = {};
  edges.forEach(function(edge) {
    const key = String(edge.source_state_id) + "->" + String(edge.sink_state_id);
    pairCount[key] = (pairCount[key] || 0) + 1;
  });
  const pairIndex = {};
  const nodePos = {};
  const slotUsage = {};
  nodes.forEach(function(node) { slotUsage[String(node.state_id)] = 0; });
  const colWidth = 240;
  const rowHeight = 120;
  const nodeWidth = 132;
  const nodeHeight = 54;
  Object.keys(byDepth).sort(function(a, b) { return Number(a) - Number(b); }).forEach(function(levelKey) {
    const list = byDepth[levelKey];
    list.forEach(function(node) {
      const id = String(node.state_id);
      const y = 76 + slotUsage[id] * rowHeight;
      slotUsage[id] += 1;
      nodePos[id] = { x: 80 + Number(levelKey) * colWidth, y: y };
    });
  });
  edges.forEach(function(edge) {
    const key = String(edge.source_state_id) + "->" + String(edge.sink_state_id);
    const index = pairIndex[key] || 0;
    pairIndex[key] = index + 1;
    const sourceId = String(edge.source_state_id || "");
    const sinkId = String(edge.sink_state_id || "");
    const source = nodePos[sourceId];
    const sink = nodePos[sinkId];
    if (!source || !sink) return;
    const total = pairCount[key] || 1;
    const offset = (index - (total - 1) / 2) * 48;
    slotUsage[sourceId] = Math.max(slotUsage[sourceId], 1);
    slotUsage[sinkId] = Math.max(slotUsage[sinkId], 1);
    const y = ((source.y + sink.y) / 2) + offset;
    edge.__layout = {
      sx: source.x + nodeWidth,
      sy: source.y + nodeHeight / 2,
      tx: sink.x,
      ty: sink.y + nodeHeight / 2,
      mx: (source.x + nodeWidth + sink.x) / 2,
      my: y,
    };
  });
  const maxDepth = Math.max.apply(null, Object.keys(byDepth).map(function(k) { return Number(k); }));
  const maxY = Math.max.apply(null, nodes.map(function(node) { return (nodePos[String(node.state_id)] || { y: 0 }).y; }).concat(edges.map(function(edge) { return edge.__layout ? edge.__layout.my : 0; })));
  const width = Math.max(860, 220 + (maxDepth + 1) * colWidth);
  const height = Math.max(360, maxY + 150);
  const roleFill = {
    reactant: "rgba(78, 161, 255, 0.18)",
    product: "rgba(56, 193, 114, 0.18)",
    intermediate: "rgba(242, 184, 75, 0.16)",
  };
  let svg = '<svg class="viewer-mechanism-svg" viewBox="0 0 ' + width + ' ' + height + '" xmlns="http://www.w3.org/2000/svg">';
  svg += '<defs><linearGradient id="mech-edge-glow" x1="0" x2="1"><stop offset="0%" stop-color="#4ea1ff" stop-opacity="0.25"/><stop offset="100%" stop-color="#d7b8ff" stop-opacity="0.18"/></linearGradient></defs>';
  svg += '<rect width="100%" height="100%" fill="transparent"/>';
  edges.forEach(function(edge, index) {
    if (!edge.__layout) return;
    const l = edge.__layout;
    const barrier = edge.barrier_forward_kcal != null ? formatMechanismNumber(edge.barrier_forward_kcal, 1) + ' kcal/mol' : '';
    svg += '<g class="mech-edge" data-edge-index="' + index + '" style="cursor:pointer">';
    svg += '<path d="M ' + l.sx + ' ' + l.sy + ' L ' + l.mx + ' ' + l.my + ' L ' + l.tx + ' ' + l.ty + '" stroke="url(#mech-edge-glow)" stroke-width="2.2" fill="none"/>';
    svg += '<path d="M ' + (l.mx - 12) + ' ' + l.my + ' L ' + l.mx + ' ' + (l.my - 15) + ' L ' + (l.mx + 12) + ' ' + l.my + ' L ' + l.mx + ' ' + (l.my + 15) + ' Z" fill="#d7b8ff" fill-opacity="0.18" stroke="#d7b8ff" stroke-width="1.4"/>';
    if (barrier) svg += '<text x="' + l.mx + '" y="' + (l.my - 22) + '" text-anchor="middle" fill="#e5edf5" font-size="11">' + escapeHtml(barrier) + '</text>';
    if (edge.ts_id) svg += '<text x="' + l.mx + '" y="' + (l.my + 31) + '" text-anchor="middle" fill="#8fa1b5" font-size="10">' + escapeHtml(String(edge.ts_id)) + '</text>';
    svg += '</g>';
  });
  nodes.forEach(function(node) {
    const id = String(node.state_id);
    const p = nodePos[id];
    if (!p) return;
    const role = String(node.role || "unknown").toLowerCase();
    const fill = roleFill[role] || 'rgba(255,255,255,0.05)';
    const energy = node.energy_hartree == null ? t("common.none") : formatMechanismNumber(node.energy_hartree, 6) + ' Ha';
    svg += '<g>';
    svg += '<rect x="' + p.x + '" y="' + p.y + '" rx="14" ry="14" width="' + nodeWidth + '" height="' + nodeHeight + '" fill="' + fill + '" stroke="rgba(255,255,255,0.14)"/>';
    svg += '<text x="' + (p.x + 14) + '" y="' + (p.y + 20) + '" fill="#e5edf5" font-size="12" font-weight="700">' + escapeHtml(mechanismRoleLabel(role)) + '</text>';
    svg += '<text x="' + (p.x + 14) + '" y="' + (p.y + 36) + '" fill="#b0bec8" font-size="11">' + escapeHtml(id) + '</text>';
    svg += '<text x="' + (p.x + 14) + '" y="' + (p.y + 50) + '" fill="#6b7b8a" font-size="10">' + escapeHtml(energy) + '</text>';
    svg += '</g>';
  });
  svg += '</svg>';
  return svg;
}

function buildEnergyProfileSvg(report) {
  const profile = report && report.mechanism_profile ? report.mechanism_profile : null;
  if (!profile || !Array.isArray(profile.routes) || !profile.routes.length) return "";
  const routes = profile.routes.filter(function(route) { return route && Array.isArray(route.points) && route.points.length; });
  if (!routes.length) return "";
  const width = 920;
  const height = 420;
  const pad = { left: 74, right: 28, top: 30, bottom: 54 };
  const allEnergies = [];
  routes.forEach(function(route) {
    route.points.forEach(function(point) {
      if (typeof point.energy_kcal === "number" && isFinite(point.energy_kcal)) allEnergies.push(point.energy_kcal);
    });
  });
  if (!allEnergies.length) return "";
  const minEnergy = Math.min.apply(null, allEnergies.concat([0]));
  const maxEnergy = Math.max.apply(null, allEnergies.concat([0]));
  const spread = Math.max(10, maxEnergy - minEnergy || 10);
  const yMin = minEnergy - spread * 0.12;
  const yMax = maxEnergy + spread * 0.12;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const routeColors = ["#4ea1ff", "#38c172", "#f2b84b", "#d7b8ff", "#ff8f66"];
  function xFor(v) {
    const raw = typeof v === "number" && isFinite(v) ? v : 0;
    return pad.left + Math.max(0, Math.min(1, raw)) * plotW;
  }
  function yFor(v) {
    return pad.top + (1 - ((v - yMin) / (yMax - yMin || 1))) * plotH;
  }
  let svg = '<svg class="viewer-mechanism-svg" viewBox="0 0 ' + width + ' ' + height + '" xmlns="http://www.w3.org/2000/svg">';
  svg += '<rect width="100%" height="100%" fill="transparent"/>';
  for (let i = 0; i <= 4; i += 1) {
    const energy = yMin + ((yMax - yMin) * i / 4);
    const y = yFor(energy);
    svg += '<line x1="' + pad.left + '" y1="' + y + '" x2="' + (width - pad.right) + '" y2="' + y + '" stroke="rgba(255,255,255,0.08)" stroke-dasharray="4 6"/>';
    svg += '<text x="' + (pad.left - 12) + '" y="' + (y + 4) + '" text-anchor="end" fill="#8fa1b5" font-size="10">' + escapeHtml(formatMechanismNumber(energy, 1)) + '</text>';
  }
  const zeroY = yFor(0);
  svg += '<line x1="' + pad.left + '" y1="' + zeroY + '" x2="' + (width - pad.right) + '" y2="' + zeroY + '" stroke="rgba(78,161,255,0.32)" stroke-width="1.2"/>';
  svg += '<line x1="' + pad.left + '" y1="' + pad.top + '" x2="' + pad.left + '" y2="' + (height - pad.bottom) + '" stroke="rgba(255,255,255,0.18)"/>';
  svg += '<line x1="' + pad.left + '" y1="' + (height - pad.bottom) + '" x2="' + (width - pad.right) + '" y2="' + (height - pad.bottom) + '" stroke="rgba(255,255,255,0.18)"/>';
  routes.forEach(function(route, routeIndex) {
    const color = routeColors[routeIndex % routeColors.length];
    const path = [];
    route.points.forEach(function(point) {
      if (typeof point.energy_kcal !== "number" || !isFinite(point.energy_kcal)) return;
      path.push((path.length ? 'L' : 'M') + xFor(point.progress) + ' ' + yFor(point.energy_kcal));
    });
    if (!path.length) return;
    svg += '<path d="' + path.join(' ') + '" fill="none" stroke="' + color + '" stroke-width="2.4"/>';
    route.points.forEach(function(point, pointIndex) {
      if (typeof point.energy_kcal !== "number" || !isFinite(point.energy_kcal)) return;
      const x = xFor(point.progress);
      const y = yFor(point.energy_kcal);
      const isTs = pointIndex % 2 === 1;
      if (isTs) {
        svg += '<line x1="' + x + '" y1="' + (height - pad.bottom) + '" x2="' + x + '" y2="' + y + '" stroke="' + color + '" stroke-opacity="0.34" stroke-dasharray="5 5"/>';
      }
      svg += '<circle cx="' + x + '" cy="' + y + '" r="' + (isTs ? 4.5 : 3.6) + '" fill="' + color + '" fill-opacity="' + (isTs ? '0.82' : '1') + '" stroke="#0b0f14" stroke-width="1.2"/>';
      svg += '<text x="' + x + '" y="' + (y - 10) + '" text-anchor="middle" fill="#d6deea" font-size="10">' + escapeHtml(formatMechanismNumber(point.energy_kcal, 1)) + '</text>';
    });
    svg += '<text x="' + (width - pad.right - 8) + '" y="' + (pad.top + 16 + routeIndex * 16) + '" text-anchor="end" fill="' + color + '" font-size="11">' + escapeHtml(String(route.route_id || ('route-' + (routeIndex + 1)))) + '</text>';
  });
  svg += '<text x="' + (width / 2) + '" y="' + (height - 16) + '" text-anchor="middle" fill="#8fa1b5" font-size="11">' + escapeHtml(t("mech.energy.axis_x")) + '</text>';
  svg += '<text x="18" y="' + (height / 2) + '" text-anchor="middle" fill="#8fa1b5" font-size="11" transform="rotate(-90 18 ' + (height / 2) + ')">' + escapeHtml(t("mech.energy.axis_y")) + '</text>';
  svg += '</svg>';
  return svg;
}

function energyGraphSeries(data) {
  return getEnergyChartRenderer().energyGraphSeries.apply(null, arguments);
}

function energyGraphValue(value) {
  return getEnergyChartRenderer().energyGraphValue.apply(null, arguments);
}

function energyGraphFormatNumber(value, digits) {
  return getEnergyChartRenderer().energyGraphFormatNumber.apply(null, arguments);
}

function energyGraphCandidateAnnotations(data) {
  return getEnergyChartRenderer().energyGraphCandidateAnnotations.apply(null, arguments);
}

function energyGraphFrameIndexForNode(data, nodeId) {
  return getEnergyChartRenderer().energyGraphFrameIndexForNode.apply(null, arguments);
}

function energyGraphSeriesValueAtFrame(data, series, frameIndex) {
  return getEnergyChartRenderer().energyGraphSeriesValueAtFrame.apply(null, arguments);
}

/* ── Text measurement: canvas-based with CJK-aware fallback ── */
function energyGraphMeasureText(text, fontSize, bold) {
  return getEnergyChartRenderer().energyGraphMeasureText.apply(null, arguments);
}

function energyGraphChartGeometry() {
  return getEnergyChartRenderer().energyGraphChartGeometry.apply(null, arguments);
}

/* ── Nice-number tick generator ── */
function energyChartNiceTicks(lo, hi, targetCount, integerMode) {
  return getEnergyChartRenderer().energyChartNiceTicks.apply(null, arguments);
}

function energyGraphAxesMarkup(xDom, yDom, geom, xTickMode) {
  return getEnergyChartRenderer().energyGraphAxesMarkup.apply(null, arguments);
}

function activatePlotTool(mode) {
  energyGraphState.viewMode = mode || "";
  var container = getEnergyViewerContainer();
  if (container) {
    container.querySelectorAll("[data-plot-tool]").forEach(function(button) {
      button.classList.toggle("active", button.dataset.plotTool === mode);
    });
    var svg = container.querySelector("[data-energy-chart]");
    if (svg) {
      svg.classList.toggle("mode-box", mode === "box");
      svg.classList.toggle("mode-pan", mode === "pan");
    }
  }
}

function energyGraphNodeX(node, mode) {
  return getEnergyChartRenderer().energyGraphNodeX.apply(null, arguments);
}

var energyViewFramePending = false;
function energyGraphScheduleViewUpdate() {
  if (energyViewFramePending) return;
  energyViewFramePending = true;
  requestAnimationFrame(function() { energyViewFramePending = false; energyGraphUpdateChart(); });
}

function energyGraphUpdateChart() {
  var screen = getEnergyViewerContainer();
  if (!screen || !energyGraphState.data || activeViewerTab !== "energy") return;
  if (energyGraphState.data.view_type === "optimization") {
    optimizationGraphRenderCurrent();
    return;
  }
  energyGraphUpdateSelectionOnData();
  var scrollEl = screen.querySelector("[data-energy-chart-scroll]");
  if (!scrollEl) return;
  scrollEl.innerHTML = energyGraphBuildSvg(energyGraphState.data);
  var root = screen.querySelector("[data-energy-workspace]");
  if (root) energyGraphBindChartInteractions(root);
}

function energyGraphUpdateSelection(frameChanged, skipSvg) {
  var data = energyGraphState.data;
  if (!data) return;
  if (data.view_type === "optimization") {
    optimizationGraphRenderCurrent();
    return;
  }
  var container = getEnergyViewerContainer();
  var svg = container ? container.querySelector("[data-energy-chart]") : null;
  if (svg && !skipSvg) {
    svg.querySelectorAll(".energy-point.selected").forEach(function(el) { el.classList.remove("selected"); el.setAttribute("r", "4"); });
    svg.querySelectorAll(".energy-current-line, .energy-crosshair.selected-crosshair").forEach(function(el) { el.remove(); });
    var selectedNodeId = energyGraphState.selectedNodeId;
    if (selectedNodeId) {
      var point = svg.querySelector('.energy-point-group[data-energy-node="' + selectedNodeId + '"] .energy-point');
      if (point) { point.classList.add("selected"); point.setAttribute("r", "6"); }
      var frame = energyGraphFrameIndexForNode(data, selectedNodeId);
      if (frame != null) {
        var layout = energyGraphState.layout;
        if (layout) {
          var node = data.nodes.find(function(n) { return String(n.id) === String(energyGraphState.selectedNodeId); })
            || data.nodes.find(function(n) { return Number(n.frame_index) === frame; });
          if (node) {
            var currentXMode = energyGraphState.xAxisMode || (data.metadata && data.metadata.default_x_axis_mode) || (data.x_axis && data.x_axis.default_mode) || "scanned_coordinate";
            var nodeX = energyGraphNodeX(node, currentXMode);
            if (nodeX !== null) {
              var series = energyGraphSeries(data);
              var yVal = energyGraphSeriesValueAtFrame(data, series, frame);
              if (yVal !== null) {
                var xDom = energyGraphState.currentDomain ? energyGraphState.currentDomain.x : layout.baseX;
                var yDom = energyGraphState.currentDomain ? energyGraphState.currentDomain.y : layout.baseY;
                var geom = energyGraphState._cachedGeom || energyGraphChartGeometry();
                var Lp = geom.pad.left, Tp = geom.pad.top, Bp = geom.pad.bottom;
                var inDomain = nodeX >= xDom[0] && nodeX <= xDom[1] && yVal >= yDom[0] && yVal <= yDom[1];
                if (inDomain) {
                  var Pw = geom.W - Lp - geom.pad.right, Ph = geom.H - Tp - Bp;
                  var px = Lp + ((nodeX - xDom[0]) / (xDom[1] - xDom[0] || 1)) * Pw;
                  var plotGroup = svg.querySelector("[data-energy-plot]");
                  if (plotGroup) {
                    var firstFrame = svg.querySelector("[data-energy-frame]");
                    var clSvg = '<line class="energy-current-line energy-crosshair selected-crosshair" x1="' + px + '" y1="' + Tp + '" x2="' + px + '" y2="' + (geom.H - Bp) + '"></line>';
                    var py = Tp + ((yDom[1] - yVal) / (yDom[1] - yDom[0] || 1)) * Ph;
                    clSvg += '<line class="energy-crosshair selected-crosshair" x1="' + Lp + '" y1="' + py + '" x2="' + (geom.W - geom.pad.right) + '" y2="' + py + '" style="stroke:var(--accent);stroke-width:1;stroke-dasharray:3 3;opacity:.25;vector-effect:non-scaling-stroke;pointer-events:none;"></line>';
                    if (firstFrame) firstFrame.insertAdjacentHTML("beforebegin", clSvg);
                    else plotGroup.insertAdjacentHTML("afterbegin", clSvg);
                  }
                }
              }
            }
          }
        }
      }
    }
  }
  var inspectorBody = container ? container.querySelector(".energy-inspector-body") : null;
  if (inspectorBody) inspectorBody.outerHTML = energyGraphInspectorMarkup(data);
  var root = container ? container.querySelector("[data-energy-workspace]") : null;
  if (root) energyGraphBindInspectorButtons(root);
  if (frameChanged) {
    var selNode = energyGraphSelectedNode(data);
    if (selNode && selNode.frame_index != null) energyGraphLoadStructure(selNode.frame_index);
  }
}

function energyGraphBindInspectorButtons(root) {
  if (!root) return;
  var data = energyGraphState.data;
  root.querySelectorAll(".frame-nav-row [data-energy-action]").forEach(function(button) {
    button.addEventListener("click", function() {
      var action = button.getAttribute("data-energy-action");
      if (!data) return;
      var node = energyGraphCurrentNode(data);
      var frame = node && node.frame_index != null ? Number(node.frame_index) : null;
      if (frame == null) return;
      var nodes = data.nodes || [];
      var index = nodes.findIndex(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); });
      if (index < 0) index = nodes.findIndex(function(item) { return Number(item.frame_index) === frame; });
      if (action === "prev" && index > 0) { energyGraphState.liveFollow = false; energyGraphSelectFrame(nodes[index - 1].frame_index, undefined, nodes[index - 1].id); }
      if (action === "next" && index >= 0 && index < nodes.length - 1) { energyGraphState.liveFollow = false; energyGraphSelectFrame(nodes[index + 1].frame_index, undefined, nodes[index + 1].id); }
    });
  });
  root.querySelectorAll("[data-energy-role]").forEach(function(button) {
    button.addEventListener("click", function(event) {
      event.preventDefault();
      if (!data) return;
      var node = energyGraphCurrentNode(data);
      if (!node || node.frame_index == null) return;
      energyGraphSetFrameRole(node.frame_index, button.getAttribute("data-energy-role"));
    });
  });
  var nameInput = root.querySelector("[data-energy-candidate-name]");
  if (nameInput) {
    nameInput.addEventListener("change", function() {
      var review = candidateReviewState();
      if (!data || review.selectionLocked) return;
      var node = energyGraphCurrentNode(data);
      if (!node || node.frame_index == null) return;
      var sanitized = energyGraphSanitizeName(nameInput.value);
      var renamed = candidateReviewCtl().setCandidateName(node.frame_index, sanitized);
      if (renamed) energyGraphRenderCurrent();
    });
  }
  /* ── Candidate card actions ── */
  /* Type button selection (non-PES mode: select type, enable save) */
  root.querySelectorAll('.energy-card-type-btn[data-energy-candidate-role]').forEach(function(button) {
    button.addEventListener("click", function() {
      var card = button.closest(".energy-candidate-card");
      if (!card) return;
      var state = card.getAttribute("data-energy-card-state");
      if (state === "disabled" || state === "scan") return;
      /* Highlight selected type */
      card.querySelectorAll(".energy-card-type-btn").forEach(function(btn) { btn.classList.remove("active"); });
      button.classList.add("active");
      /* Enable save button */
      var saveBtn = card.querySelector('[data-energy-action="save-candidate"]');
      if (saveBtn) saveBtn.disabled = false;
      /* Transition to type-selected */
      card.setAttribute("data-energy-card-state", "type-selected");
      /* Hide error */
      var errorEl = card.querySelector("[data-energy-card-error]");
      if (errorEl) errorEl.style.display = "none";
    });
  });
  /* Save candidate button */
  root.querySelectorAll('[data-energy-action="save-candidate"]').forEach(function(button) {
    button.addEventListener("click", async function() {
      var card = button.closest(".energy-candidate-card");
      if (!card) return;
      var state = card.getAttribute("data-energy-card-state");
      if (state !== "type-selected") return;
      if (!data) return;
      var node = energyGraphCurrentNode(data);
      if (!node || node.frame_index == null) return;
      /* Get selected role */
      var activeBtn = card.querySelector(".energy-card-type-btn.active");
      if (!activeBtn) return;
      var role = String(activeBtn.getAttribute("data-energy-candidate-role") || "");
      if (!role) return;
      /* Saving state: disable all */
      card.setAttribute("data-energy-card-state", "saving");
      card.querySelectorAll("button").forEach(function(btn) { btn.disabled = true; });
      var session = energyGraphResultSession(energyGraphState.jobId);
      try {
        var body = {
          view_type: String(data.view_type || ""),
          frame_index: Number(node.frame_index),
          frame_id: String(node.id || ""),
          role: role,
          expected_revision: Number(energyGraphState.frameCandidates.revision) || 0,
        };
        if (energyGraphState.itemId) body.item_id = String(energyGraphState.itemId);
        var resp = await api("/jobs/" + encodeURIComponent(energyGraphState.jobId) + "/frame-candidate", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        if (!energyGraphResultSessionCurrent(session)) return;
        if (resp) {
          energyGraphApplyFrameCandidates(resp);
          energyGraphRenderCurrent();
        }
      } catch (e) {
        if (!energyGraphResultSessionCurrent(session)) return;
        var errMsg = String((e && e.message) || e);
        /* Conflict: 409 / expected_revision mismatch */
        if (errMsg.indexOf("409") >= 0 || errMsg.indexOf("revision") >= 0 || errMsg.indexOf("conflict") >= 0) {
          /* Save conflict state before re-render destroys the card DOM */
          energyGraphState.pendingCardError = { message: t("energy.card.conflict"), role: role };
          /* Re-fetch candidates */
          var fresh = await energyGraphFetchFrameCandidates(energyGraphState.jobId);
          if (fresh && energyGraphResultSessionCurrent(session)) { energyGraphApplyFrameCandidates(fresh); energyGraphRenderCurrent(); }
        } else {
          var errorEl2 = card.querySelector("[data-energy-card-error]");
          if (errorEl2) { errorEl2.textContent = errMsg; errorEl2.style.display = ""; }
          /* Restore type-selected state */
          card.setAttribute("data-energy-card-state", "type-selected");
          card.querySelectorAll(".energy-card-type-btn").forEach(function(btn) { btn.disabled = false; });
          button.disabled = false;
        }
      }
    });
  });
  /* Change role button (saved state → show type row) */
  root.querySelectorAll('[data-energy-action="change-role"]').forEach(function(button) {
    button.addEventListener("click", function() {
      var card = button.closest(".energy-candidate-card");
      if (!card) return;
      var typeRow = card.querySelector("[data-energy-card-type-row]");
      var actionRow = card.querySelector("[data-energy-card-action-row]");
      if (typeRow) typeRow.style.display = "";
      if (actionRow) actionRow.style.display = "";
      /* Pre-select current role */
      var node = data ? energyGraphCurrentNode(data) : null;
      var candEntry = node ? energyGraphFrameCandidateFor(data.view_type, node.frame_index) : null;
      if (candEntry) {
        var currentRole = String(candEntry.role || "").toLowerCase();
        card.querySelectorAll(".energy-card-type-btn").forEach(function(btn) {
          var btnRole = String(btn.getAttribute("data-energy-candidate-role") || "").toLowerCase();
          btn.classList.toggle("active", btnRole === currentRole);
        });
        var saveBtn = card.querySelector('[data-energy-action="save-candidate"]');
        if (saveBtn) saveBtn.disabled = false;
      }
      card.setAttribute("data-energy-card-state", "type-selected");
      button.style.display = "none";
    });
  });
  /* Cancel button */
  root.querySelectorAll('[data-energy-action="card-cancel"]').forEach(function(button) {
    button.addEventListener("click", function() {
      var card = button.closest(".energy-candidate-card");
      if (!card) return;
      /* If there's a saved candidate for this frame, re-render to restore saved view */
      if (data) {
        var node = energyGraphCurrentNode(data);
        if (node && node.frame_index != null) {
          var savedEntry = energyGraphFrameCandidateFor(data.view_type, node.frame_index);
          if (savedEntry) {
            energyGraphRenderCurrent();
            return;
          }
        }
      }
      /* No saved candidate: reset to idle */
      card.querySelectorAll(".energy-card-type-btn").forEach(function(btn) { btn.classList.remove("active"); });
      var saveBtn = card.querySelector('[data-energy-action="save-candidate"]');
      if (saveBtn) saveBtn.disabled = true;
      card.setAttribute("data-energy-card-state", "idle");
      var errorEl = card.querySelector("[data-energy-card-error]");
      if (errorEl) errorEl.style.display = "none";
    });
  });
  /* More menu / export / remove (preserved from old handler) */
  root.querySelectorAll('[data-energy-action="more-menu"]').forEach(function(button) {
    button.addEventListener("click", function() {
      var menu = root.querySelector("[data-energy-more-menu]");
      if (menu) menu.style.display = menu.style.display === "none" ? "" : "none";
    });
  });
  root.querySelectorAll('[data-energy-action="export-frame"]').forEach(function(button) {
    button.addEventListener("click", async function() {
      var openMenu = root.querySelector("[data-energy-more-menu]");
      if (openMenu) openMenu.style.display = "none";
      if (!data) return;
      var node = energyGraphCurrentNode(data);
      if (!node) return;
      try {
        var xyz = await energyGraphFetchFrameXyz(node);
        if (!xyz) throw new Error("frame geometry unavailable");
        var blob = new Blob([xyz], { type: "chemical/x-xyz" });
        var fname = String(energyGraphState.jobId || "job") + "_" + String(node.id || "frame") + ".xyz";
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
      } catch (e) {
        alert(String((e && e.message) || e));
      }
    });
  });
  root.querySelectorAll('[data-energy-action="remove-candidate"]').forEach(function(button) {
    button.addEventListener("click", async function() {
      if (!data) return;
      var node = energyGraphCurrentNode(data);
      if (!node) return;
      var entry = energyGraphFrameCandidateFor(data.view_type, node.frame_index);
      if (!entry) return;
      if (!confirm(t("energy.inspector.remove_candidate") + ": " + String(entry.candidate_id || "") + "?")) return;
      button.disabled = true;
      var session = energyGraphResultSession(energyGraphState.jobId);
      try {
        var delUrl = "/jobs/" + encodeURIComponent(energyGraphState.jobId) + "/frame-candidate/" + encodeURIComponent(String(entry.candidate_id)) + "?expected_revision=" + String(Number(energyGraphState.frameCandidates.revision) || 0);
        var delResp = await api(delUrl, { method: "DELETE" });
        if (!energyGraphResultSessionCurrent(session)) return;
        if (delResp) {
          energyGraphApplyFrameCandidates(delResp);
          energyGraphRenderCurrent();
        }
      } catch (e) {
        if (!energyGraphResultSessionCurrent(session)) return;
        alert(String((e && e.message) || e));
      } finally {
        button.disabled = false;
      }
    });
  });
  /* ── Annotation toolbar mode switching ── */
  root.querySelectorAll("[data-energy-annotation-mode]").forEach(function(button) {
    button.addEventListener("click", function() {
      var mode = button.getAttribute("data-energy-annotation-mode");
      if (mode && mode !== energyGraphState.annotationMode) {
        energyGraphState.annotationMode = mode;
        energyGraphRenderCurrent();
      }
    });
  });
  /* ── Candidate list row click → jump to frame ── */
  root.querySelectorAll("[data-energy-candidate-jump]").forEach(function(row) {
    row.addEventListener("click", function() {
      var fi = Number(row.getAttribute("data-energy-candidate-jump"));
      if (!isNaN(fi)) {
        energyGraphState.liveFollow = false;
        energyGraphSelectFrame(fi);
      }
    });
  });
  root.querySelectorAll("[data-energy-candidate-filter]").forEach(function(button) {
    button.addEventListener("click", function() {
      energyGraphState.candidateListFilter = button.getAttribute("data-energy-candidate-filter") || "all";
      energyGraphRenderCurrent();
    });
  });
  /* ── Rename candidate action ── */
  var renameBtn = root.querySelector('[data-energy-action="rename-candidate"]');
  if (renameBtn) {
    renameBtn.addEventListener("click", function() {
      var openMenu = root.querySelector("[data-energy-more-menu]");
      if (openMenu) openMenu.style.display = "none";
      var data = energyGraphState.data;
      if (!data) return;
      var node = energyGraphCurrentNode(data);
      if (!node || node.frame_index == null) return;
      var entry = energyGraphFrameCandidateFor(data.view_type, node.frame_index);
      if (!entry) return;
      var currentName = String(entry.name || entry.display_label || entry.role || "");
      var newName = prompt(t("energy.inspector.change_role") || "Rename candidate", currentName);
      if (newName !== null && newName !== currentName) {
        /* Refresh after rename will happen on next data poll */
      }
    });
  }
  /* ── Expandable long fields (geometry_ref etc.) ── */
  root.querySelectorAll("[data-expandable]").forEach(function(el) {
    el.addEventListener("click", function() {
      el.classList.toggle("expanded");
    });
  });
  /* ── Restore pending conflict notice after re-render ── */
  if (energyGraphState.pendingCardError) {
    var pending = energyGraphState.pendingCardError;
    energyGraphState.pendingCardError = null;
    var card = root.querySelector(".energy-candidate-card");
    if (card) {
      var errorEl = card.querySelector("[data-energy-card-error]");
      if (errorEl) { errorEl.textContent = pending.message; errorEl.style.display = ""; }
      /* Un-hide type/action rows (saved variant renders them hidden) */
      var typeRow = card.querySelector("[data-energy-card-type-row]");
      if (typeRow) typeRow.style.display = "";
      var actionRow = card.querySelector("[data-energy-card-action-row]");
      if (actionRow) actionRow.style.display = "";
      /* Restore type-selected state with the user's chosen role re-highlighted */
      card.setAttribute("data-energy-card-state", "type-selected");
      card.querySelectorAll(".energy-card-type-btn").forEach(function(btn) {
        var btnRole = String(btn.getAttribute("data-energy-candidate-role") || "");
        if (btnRole === pending.role) btn.classList.add("active");
        btn.disabled = false;
      });
      var saveBtn = card.querySelector('[data-energy-action="save-candidate"]');
      if (saveBtn) saveBtn.disabled = false;
    }
  }
}

/* ── Candidate projection: derive annotation-shaped objects from frameCandidates ── */
function energyProjectCandidatesAsAnnotations(data) {
  return getEnergyChartRenderer().energyProjectCandidatesAsAnnotations.apply(null, arguments);
}

/* ── Candidate band layout: two compact rows plus a guaranteed overflow entry ── */
/* items: [{annotation, dataX, dataY, frameIndex, type, value}] sorted by dataX
 * geom: {W, H, pad}
 * Returns {bandSvg, plotSvg, bandHeight} — bandSvg for pad.top area, plotSvg for markers at real coords */
/* ── Shared greedy2-row chip layout core ── */
/* chips: [{chipW, anchorPx}] — already measured, anchorPx in pixel coords
 * plotLeft/plotRight: pixel bounds of the plot area
 * Returns chips with ._x and ._row set; unplaceable chips get ._skip = true */
function energyCandidateBandLayoutCore(chips, plotLeft, plotRight) {
  return getEnergyChartRenderer().energyCandidateBandLayoutCore.apply(null, arguments);
}

function energyCandidateBandLayout(items, geom, xFor, yFor, opts) {
  return getEnergyChartRenderer().energyCandidateBandLayout.apply(null, arguments);
}

/* ── Candidate band height pre-compute (dry run, no SVG) ── */
/* items: [{annotation, dataX, frameIndex, type}] — dataX in data coords
 * xFor: data→pixel mapper (pad.top doesn't affect x mapping)
 * plotLeft/plotRight: pixel bounds */
function energyCandidateBandPreHeight(items, xFor, plotLeft, plotRight) {
  return getEnergyChartRenderer().energyCandidateBandPreHeight.apply(null, arguments);
}

/* ── Node resolution for annotations: prefer node_id, fallback to frame_index ── */
function energyGraphNodeForAnnotation(data, annotation) {
  return getEnergyChartRenderer().energyGraphNodeForAnnotation.apply(null, arguments);
}

/* ── Shared annotation renderer (reusable by PES + OPT + scan/conformer) ── */
/* opts = {annotations, nodes, xFor, yFor, geom, clipId, mode, formatValue, selectedNodeId, selectionLocked}
   mode: "full" = PES markers+labels+caption; "flag" = OPT compact (vertical line + shape, no caption)
   Returns {svg, bandSvg, bandHeight} — svg for plot group, bandSvg for pad.top area */
function renderTrajectoryAnnotations(opts) {
  var annotations = opts.annotations || [];
  var nodes = opts.nodes || [];
  var xFor = opts.xFor;
  var yFor = opts.yFor;
  var geom = opts.geom;
  var mode = opts.mode || "full";
  var formatValue = opts.formatValue || function(v) { return energyGraphFormatNumber(v, 2); };
  var selectedNodeId = opts.selectedNodeId || "";
  var selectionLocked = !!opts.selectionLocked;
  if (!annotations.length || !nodes.length || !xFor || !yFor) return { svg: "", bandSvg: "", bandHeight: 0, missingAnnotations: [] };
  var W = geom.W, H = geom.H, pad = geom.pad;
  /* ── Defensive filter: remove maximum type (backend no longer produces) ── */
  annotations = annotations.filter(function(a) { return a && a.type !== "maximum"; });
  /* ── Resolve annotation data-space positions using node_id first, frame_index fallback ── */
  var allItems = [];
  var missingAnnotations = [];
  annotations.forEach(function(annotation) {
    var annNode = energyGraphNodeForAnnotation({ nodes: nodes }, annotation);
    if (!annNode && (annotation.type === "ts" || annotation.type === "intermediate")) {
      missingAnnotations.push(annotation);
      return;
    }
    if (!annNode) return;
    var currentXMode = (opts && opts.xAxisMode) || energyGraphState.xAxisMode || "scanned_coordinate";
    var dataX = energyGraphNodeX(annNode, currentXMode);
    var dataY = null;
    if (annNode) {
      dataY = energyGraphValue(annNode.energy);
      if (dataY === null) dataY = energyGraphValue(annotation.y);
      if (dataY === null && annNode.metadata) dataY = energyGraphValue(annNode.metadata.relative_energy);
    }
    if (dataX === null) return;
    if (dataY === null) dataY = 0;
    allItems.push({ annotation: annotation, dataX: dataX, dataY: dataY, value: dataY, frameIndex: annotation.frame_index, type: String(annotation.type || "") });
  });
  /* ── Filter: domain + annotationMode ── */
  var visibleAnnotations = allItems.filter(function(item) {
    if (item.dataX < (opts.xDom ? opts.xDom[0] : -Infinity) || item.dataX > (opts.xDom ? opts.xDom[1] : Infinity)) return false;
    if (energyGraphState.annotationMode === "all") return true;
    if (energyGraphState.annotationMode === "candidate") {
      return ["ts", "intermediate"].indexOf(item.type) >= 0 || item.annotation.saved === true || item.annotation.selection_source === "manual_frame";
    }
    if (energyGraphState.annotationMode === "hide") return false;
    return energyGraphState.annotationMode === item.type;
  });
  /* ── Split candidates vs system annotations ── */
  var candidateItems = [];
  var systemItems = [];
  visibleAnnotations.forEach(function(item) {
    if (item.type === "ts" || item.type === "intermediate") {
      candidateItems.push(item);
    } else {
      systemItems.push(item);
    }
  });
  /* ── Candidate band layout ── */
  var bandResult = energyCandidateBandLayout(candidateItems, geom, xFor, yFor, { selectionLocked: selectionLocked });
  /* ── System annotations: flag mode (minimum/failed/new_basin) ── */
  var svg = "";
  var pixelItems = systemItems.map(function(item) {
    return { annotation: item.annotation, dataX: item.dataX, x: xFor(item.dataX), y: yFor(item.dataY), value: item.value, frameIndex: item.frameIndex, type: item.type,
      label: String(item.annotation.label || item.annotation.id), caption: item.value !== null ? formatValue(item.value) : "" };
  });
  pixelItems.sort(function(a, b) { return a.x - b.x; });
  /* ── Render system annotations ── */
  pixelItems.forEach(function(item) {
    var annotation = item.annotation;
    var ax = item.x, ay = item.y, at = item.type;
    var isSelected = annotation.id === energyGraphState.selectedAnnotationId;
    var selectedClass = isSelected ? " selected" : "";
    var labelClass = isSelected ? " selected-label" : "";
    var isEnergyView = opts.viewType === "energy";
    svg += '<g data-energy-annotation="' + escapeHtml(String(annotation.id)) + '" data-energy-frame="' + escapeHtml(String(item.frameIndex)) + '">';
    if (at === "minimum" && isEnergyView) {
      /* Full mode for minimum in energy view: circle at data coords, line from bottom */
      svg += '<circle class="energy-marker-min' + selectedClass + '" cx="' + ax + '" cy="' + ay + '" r="6"></circle>';
      svg += '<line class="energy-marker-line" x1="' + ax + '" y1="' + (H - pad.bottom) + '" x2="' + ax + '" y2="' + ay + '"></line>';
      svg += '<text class="energy-marker-label' + labelClass + '" x="' + ax + '" y="' + (ay - 12) + '" text-anchor="middle">' + escapeHtml(String(annotation.label || annotation.id)) + '</text>';
    } else {
      /* Flag mode: vertical line + shape at top */
      svg += '<line class="energy-marker-line" x1="' + ax + '" y1="' + (H - pad.bottom) + '" x2="' + ax + '" y2="' + pad.top + '"></line>';
      if (at === "minimum") {
        svg += '<circle class="energy-marker-min' + selectedClass + '" cx="' + ax + '" cy="' + (pad.top + 8) + '" r="4"></circle>';
      } else if (at === "failed") {
        svg += '<path class="energy-marker-failed" d="M ' + (ax - 6) + ' ' + (pad.top + 2) + ' L ' + (ax + 6) + ' ' + (pad.top + 14) + ' M ' + (ax + 6) + ' ' + (pad.top + 2) + ' L ' + (ax - 6) + ' ' + (pad.top + 14) + '"></path>';
      } else if (at === "new_basin") {
        svg += '<path class="energy-marker-basin' + selectedClass + '" d="M ' + ax + ' ' + (pad.top + 2) + ' L ' + (ax + 6) + ' ' + (pad.top + 8) + ' L ' + ax + ' ' + (pad.top + 14) + ' L ' + (ax - 6) + ' ' + (pad.top + 8) + ' Z"></path>';
      }
      svg += '<text class="energy-marker-label' + labelClass + '" x="' + ax + '" y="' + (pad.top + 24) + '" text-anchor="middle">' + escapeHtml(String(annotation.label || annotation.id)) + '</text>';
    }
    svg += '</g>';
  });
  return { svg: svg, bandSvg: bandResult.bandSvg, bandHeight: bandResult.bandHeight, plotSvg: bandResult.plotSvg, missingAnnotations: missingAnnotations };
}

/* ── Candidate list markup for inspector panel ── */
function energyCandidateListMarkup(data) {
  return getEnergyChartRenderer().energyCandidateListMarkup.apply(null, arguments);
}

/* ── Consistency warning strip for missing-node candidates ── */
function energyConsistencyWarningMarkup(data) {
  var warnings = (data && data.metadata && Array.isArray(data.metadata.consistency_warnings)) ? data.metadata.consistency_warnings : [];
  if (!warnings.length) return "";
  var items = warnings.map(function(w) {
    var parts = [];
    if (w.code) parts.push(w.code);
    if (w.candidate_id) parts.push(w.candidate_id);
    if (w.frame_index != null) parts.push("frame " + w.frame_index);
    if (w.message) parts.push(w.message);
    return '<div class="energy-warning-item">' + escapeHtml(parts.join(" \u00b7 ")) + '</div>';
  }).join("");
  return '<div class="energy-warning-strip">' +
    escapeHtml(t("energy.band.warning_strip", { count: warnings.length })) +
    items +
  '</div>';
}

/* ── Annotation toolbar markup ── */
function energyAnnotationToolbarMarkup() {
  var mode = energyGraphState.annotationMode || "all";
  return '<div class="energy-annotation-toolbar">' +
    '<button class="energy-annotation-toolbar-btn' + (mode === "all" ? " active" : "") + '" data-energy-annotation-mode="all" type="button">' + escapeHtml(t("energy.annotation.all")) + '</button>' +
    '<button class="energy-annotation-toolbar-btn' + (mode === "candidate" ? " active" : "") + '" data-energy-annotation-mode="candidate" type="button">' + escapeHtml(t("energy.annotation.candidate")) + '</button>' +
    '<button class="energy-annotation-toolbar-btn' + (mode === "hide" ? " active" : "") + '" data-energy-annotation-mode="hide" type="button">' + escapeHtml(t("energy.annotation.hide")) + '</button>' +
  '</div>';
}

/* ── Unified SVG chart core ── */
/* Config shape (energy + optimisation reusable):
 * {
 *   svgOpen,               // <svg ...> open tag (pre-built by caller)
 *   clipId,                // clipPath id
 *   geom,                  // {W, H, pad, plotRect}
 *   xDom, yDom,            // left-axis domains
 *   linePath, areaPath,    // pre-computed SVG path d strings
 *   selectedGuide,         // {x, y1, y2} | null  (vertical guide line)
 *   points,                // [{cx, cy, r, cssClass, frameIndex, nodeId}]
 *   annotationOpts,        // passed to renderTrajectoryAnnotations
 *   xLabel, xUnit, yLabel, yUnit,  // axis label texts
 *   /* group/data-attr overrides (energy defaults) *​/
 *   plotAttr, overlayAttr, axesAttr,
 *   gridClass, axisTextClass, axisLineClass,
 * }
 * Returns complete <svg>...</svg> string.
 */
function energyChartBuildSvg(cfg) {
  return getEnergyChartRenderer().energyChartBuildSvg.apply(null, arguments);
}

function energyGraphBuildSvg(data) {
  return getEnergyChartRenderer().energyGraphBuildSvg.apply(null, arguments);
}

/* ── Deterministic jitter for MDS scatter (seeded by frame_index) ── */
function samplingDeterministicJitter(frameIndex, domainRange) {
  var seed = Number(frameIndex) || 0;
  seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF;
  seed = ((seed >>> 16) ^ seed) * 0x45d9f3b & 0xFFFFFFFF;
  seed = ((seed >>> 16) ^ seed) * 0x45d9f3b & 0xFFFFFFFF;
  seed = (seed >>> 16) ^ seed;
  var normalized = (seed & 0xFFFF) / 0xFFFF;
  return (normalized - 0.5) * 0.06 * domainRange;
}

/* ── Sampling Energy Trajectory Chart (subview 1) ── */
function samplingEnergyTrajectorySvg(data) {
  var series = energyGraphSeries(data);
  var nodes = Array.isArray(data.nodes) ? data.nodes : [];
  if (!series || !nodes.length) return '<div class="energy-empty"><div><strong>' + escapeHtml(t("energy.sampling.no_data")) + '</strong></div></div>';
  var values = Array.isArray(series.values) ? series.values : [];
  var xValues = Array.isArray(series.x_values) && series.x_values.length
    ? series.x_values
    : nodes.map(function(node) { return energyGraphValue(node.x); });
  var points = [];
  for (var pi = 0; pi < Math.min(values.length, xValues.length); pi += 1) {
    var pxv = energyGraphValue(xValues[pi]);
    var pyv = energyGraphValue(values[pi]);
    if (pxv !== null && pyv !== null) points.push({ x: pxv, y: pyv, index: pi, node: nodes[pi] });
  }
  if (!points.length) return '<div class="energy-empty"><div><strong>' + escapeHtml(t("energy.sampling.no_data")) + '</strong></div></div>';
  var geom = energyGraphChartGeometry();
  var W = geom.W, H = geom.H, pad = geom.pad;
  var xExtent = [Math.min.apply(null, points.map(function(p) { return p.x; })), Math.max.apply(null, points.map(function(p) { return p.x; }))];
  var yExtent = [Math.min.apply(null, points.map(function(p) { return p.y; })), Math.max.apply(null, points.map(function(p) { return p.y; }))];
  var xSpread = Math.max(1e-9, xExtent[1] - xExtent[0]);
  var ySpread = Math.max(1e-9, yExtent[1] - yExtent[0]);
  var xDom = [xExtent[0] - xSpread * 0.06, xExtent[1] + xSpread * 0.06];
  var yDom = [yExtent[0] - ySpread * 0.12, yExtent[1] + ySpread * 0.12];
  var Pw = W - pad.left - pad.right, Ph = H - pad.top - pad.bottom;
  function xFor(v) { return pad.left + ((v - xDom[0]) / (xDom[1] - xDom[0] || 1)) * Pw; }
  function yFor(v) { return pad.top + (1 - ((v - yDom[0]) / (yDom[1] - yDom[0] || 1))) * Ph; }
  var svg = '<svg class="energy-chart-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Sampling Energy Trajectory" data-sampling-chart>';
  svg += '<defs><clipPath id="sampling-plot-clip"><rect x="' + pad.left + '" y="' + pad.top + '" width="' + Pw + '" height="' + Ph + '"/></clipPath></defs>';
  svg += '<g data-energy-plot clip-path="url(#sampling-plot-clip)">';
  var segments = [];
  var seg = null;
  for (var si = 0; si < points.length; si += 1) {
    if (!seg) { seg = [points[si]]; }
    else if (points[si].index === seg[seg.length - 1].index + 1) { seg.push(points[si]); }
    else { segments.push(seg); seg = [points[si]]; }
  }
  if (seg) segments.push(seg);
  var linePath = "";
  segments.forEach(function(s) {
    var cmds = s.map(function(pt, idx) { return (idx ? "L" : "M") + xFor(pt.x) + " " + yFor(pt.y); }).join(" ");
    linePath += cmds + " ";
  });
  if (linePath) svg += '<path class="energy-line" d="' + linePath + '"></path>';
  var selectedFrame = energyGraphFrameIndexForNode(data, energyGraphState.selectedNodeId);
  points.forEach(function(point) {
    var nodeId = point.node && point.node.id != null ? point.node.id : "";
    var selected = String(nodeId) === String(energyGraphState.selectedNodeId) ? " selected" : "";
    var basinId = point.node && point.node.metadata ? point.node.metadata.basin_id : 0;
    var basinColor = samplingBasinColor(basinId);
    svg += '<g data-energy-frame="' + escapeHtml(String(point.node && point.node.frame_index != null ? point.node.frame_index : point.index)) + '" data-energy-node="' + escapeHtml(String(nodeId)) + '">';
    svg += '<circle class="energy-point' + selected + '" cx="' + xFor(point.x) + '" cy="' + yFor(point.y) + '" r="' + (selected ? 6 : 4) + '" fill="' + basinColor + '" stroke="' + (selected ? '#fff' : 'none') + '" stroke-width="' + (selected ? 2 : 0) + '"></circle>';
    svg += '</g>';
  });
  var annotations = Array.isArray(data.annotations) ? data.annotations : [];
  annotations.forEach(function(annotation) {
    if (annotation.type !== "new_basin") return;
    var fi = annotation.frame_index;
    var annNode = nodes.find(function(item) { return Number(item.frame_index) === Number(fi); });
    if (!annNode || energyGraphValue(annNode.x) === null) return;
    var annSeries = energyGraphSeries(data);
    var annVal = energyGraphSeriesValueAtFrame(data, annSeries, fi);
    if (annVal === null) return;
    var ax = xFor(energyGraphValue(annNode.x)), ay = yFor(annVal);
    svg += '<path class="energy-marker-basin" d="M ' + ax + ' ' + (ay - 7) + ' L ' + (ax + 7) + ' ' + ay + ' L ' + ax + ' ' + (ay + 7) + ' L ' + (ax - 7) + ' ' + ay + ' Z"></path>';
  });
  svg += '</g>';
  svg += '<g data-energy-overlay></g>';
  svg += '<g data-energy-axes>';
  svg += energyGraphAxesMarkup(xDom, yDom, geom);
  var xAxisKey = samplingState.xAxis || "time_ps";
  var xAxisLabel = t("energy.sampling.axis." + xAxisKey);
  svg += '<text class="energy-axis-text" x="' + ((pad.left + W - pad.right) / 2) + '" y="' + (H - 17) + '" text-anchor="middle">' + escapeHtml(xAxisLabel) + '</text>';
  svg += '<text class="energy-axis-text" x="18" y="' + (H / 2) + '" text-anchor="middle" transform="rotate(-90 18 ' + (H / 2) + ')">' + escapeHtml(String(series.label || "能量")) + ' (' + escapeHtml(String(series.unit || "")) + ')</text>';
  svg += '</g>';
  svg += '</svg>';
  return svg;
}

/* ── Basin color palette ── */
function samplingBasinColor(basinId) {
  var palette = ['#55aaff', '#42d6a4', '#ffb85d', '#ff8f6b', '#b792ff', '#5dd6ff', '#ff6b9d', '#9dff6b', '#ff6b6b', '#6b9dff'];
  return palette[Number(basinId) % palette.length] || palette[0];
}

/* ── Sampling Space Scatter (subview 2) ── */
function samplingSpaceScatterSvg(data) {
  var nodes = Array.isArray(data.nodes) ? data.nodes : [];
  var metadata = data.metadata || {};
  var basins = Array.isArray(metadata.basins) ? metadata.basins : [];
  if (!nodes.length) return '<div class="energy-empty"><div><strong>' + escapeHtml(t("energy.sampling.no_data")) + '</strong></div></div>';
  var hasMds = nodes.some(function(n) { return n.metadata && n.metadata.mds && n.metadata.mds[0] != null; });
  if (!hasMds) return '<div class="energy-empty"><div><strong>' + escapeHtml(t("energy.sampling.no_data")) + '</strong></div></div>';
  var mdsPoints = nodes.map(function(n, idx) {
    var mds = (n.metadata && n.metadata.mds) || [null, null];
    return { x: mds[0], y: mds[1], node: n, index: idx };
  }).filter(function(p) { return p.x != null && p.y != null; });
  if (!mdsPoints.length) return '<div class="energy-empty"><div><strong>' + escapeHtml(t("energy.sampling.no_data")) + '</strong></div></div>';
  var xExtent = [Math.min.apply(null, mdsPoints.map(function(p) { return p.x; })), Math.max.apply(null, mdsPoints.map(function(p) { return p.x; }))];
  var yExtent = [Math.min.apply(null, mdsPoints.map(function(p) { return p.y; })), Math.max.apply(null, mdsPoints.map(function(p) { return p.y; }))];
  var xSpread = Math.max(1e-9, xExtent[1] - xExtent[0]);
  var ySpread = Math.max(1e-9, yExtent[1] - yExtent[0]);
  var xDom = [xExtent[0] - xSpread * 0.1, xExtent[1] + xSpread * 0.1];
  var yDom = [yExtent[0] - ySpread * 0.1, yExtent[1] + ySpread * 0.1];
  var geom = energyGraphChartGeometry();
  var W = geom.W, H = geom.H, pad = geom.pad;
  var Pw = W - pad.left - pad.right, Ph = H - pad.top - pad.bottom;
  function xFor(v) { return pad.left + ((v - xDom[0]) / (xDom[1] - xDom[0] || 1)) * Pw; }
  function yFor(v) { return pad.top + (1 - ((v - yDom[0]) / (yDom[1] - yDom[0] || 1))) * Ph; }
  var representativeFrames = {};
  basins.forEach(function(b) {
    if (b.representative_frame != null) {
      representativeFrames[b.representative_frame] = b.basin_id;
    }
  });
  var svg = '<svg class="energy-chart-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Sampling Space" data-sampling-space-chart>';
  svg += '<defs><clipPath id="sampling-space-clip"><rect x="' + pad.left + '" y="' + pad.top + '" width="' + Pw + '" height="' + Ph + '"/></clipPath></defs>';
  svg += '<g clip-path="url(#sampling-space-clip)">';
  mdsPoints.forEach(function(point) {
    var nodeId = point.node.id || "";
    var selected = String(nodeId) === String(energyGraphState.selectedNodeId);
    var basinId = point.node.metadata ? point.node.metadata.basin_id : 0;
    var color = samplingBasinColor(basinId);
    var jitterX = samplingDeterministicJitter(point.node.frame_index, xSpread);
    var jitterY = samplingDeterministicJitter(point.node.frame_index + 1000, ySpread);
    var px = xFor(point.x + jitterX);
    var py = yFor(point.y + jitterY);
    var isRepresentative = representativeFrames[point.index] != null;
    svg += '<g data-energy-frame="' + escapeHtml(String(point.node.frame_index != null ? point.node.frame_index : point.index)) + '" data-energy-node="' + escapeHtml(String(nodeId)) + '">';
    if (isRepresentative) {
      svg += '<circle cx="' + px + '" cy="' + py + '" r="8" fill="none" stroke="' + color + '" stroke-width="2" opacity="0.6"></circle>';
    }
    svg += '<circle class="energy-point' + (selected ? ' selected' : '') + '" cx="' + px + '" cy="' + py + '" r="' + (selected ? 6 : 3) + '" fill="' + color + '" stroke="' + (selected ? '#fff' : 'none') + '" stroke-width="' + (selected ? 2 : 0) + '"></circle>';
    svg += '</g>';
  });
  svg += '</g>';
  svg += '<g data-energy-overlay></g>';
  svg += '<g data-energy-axes>';
  svg += energyGraphAxesMarkup(xDom, yDom, geom);
  svg += '<text class="energy-axis-text" x="' + ((pad.left + W - pad.right) / 2) + '" y="' + (H - 17) + '" text-anchor="middle">MDS 1</text>';
  svg += '<text class="energy-axis-text" x="18" y="' + (H / 2) + '" text-anchor="middle" transform="rotate(-90 18 ' + (H / 2) + ')">MDS 2</text>';
  svg += '</g>';
  svg += '</svg>';
  return svg;
}

/* ── Sampling Coverage Metrics (subview 3) ── */
function samplingCoverageMarkup(data) {
  var metadata = data.metadata || {};
  var saturation = metadata.saturation;
  var cumulativeUnique = Array.isArray(metadata.cumulative_unique) ? metadata.cumulative_unique : [];
  var basins = Array.isArray(metadata.basins) ? metadata.basins : [];
  if (!saturation) return '<div class="energy-empty"><div><strong>' + escapeHtml(t("energy.sampling.no_data")) + '</strong></div></div>';
  var satLevel = String(saturation.level || "LOW").toUpperCase();
  var satDots = "";
  var dotCount = satLevel === "HIGH" ? 5 : satLevel === "MEDIUM" ? 3 : 1;
  for (var i = 0; i < 5; i++) {
    satDots += '<span class="sampling-sat-dot' + (i < dotCount ? ' active' : '') + '">●</span>';
  }
  var satLevelI18n = satLevel === "HIGH" ? t("energy.sampling.saturation_high") : satLevel === "MEDIUM" ? t("energy.sampling.saturation_medium") : t("energy.sampling.saturation_low");
  var cardsHtml = '';
  cardsHtml += '<div class="sampling-metric-card"><div class="sampling-metric-label">' + escapeHtml(t("energy.sampling.unique_clusters")) + '</div><div class="sampling-metric-value">' + escapeHtml(String(saturation.unique_clusters || 0)) + '</div></div>';
  cardsHtml += '<div class="sampling-metric-card"><div class="sampling-metric-label">' + escapeHtml(t("energy.sampling.new_clusters_20pct")) + '</div><div class="sampling-metric-value">' + escapeHtml(String(saturation.new_clusters_last_20pct || 0)) + '</div></div>';
  cardsHtml += '<div class="sampling-metric-card"><div class="sampling-metric-label">' + escapeHtml(t("energy.sampling.last_new_basin")) + '</div><div class="sampling-metric-value">' + escapeHtml(saturation.last_new_basin_ps != null ? saturation.last_new_basin_ps.toFixed(2) + ' ps' : '--') + '</div></div>';
  cardsHtml += '<div class="sampling-metric-card"><div class="sampling-metric-label">' + escapeHtml(t("energy.sampling.revisit_ratio")) + '</div><div class="sampling-metric-value">' + escapeHtml(saturation.revisit_ratio != null ? (saturation.revisit_ratio * 100).toFixed(1) + '%' : '--') + '</div></div>';
  cardsHtml += '<div class="sampling-metric-card"><div class="sampling-metric-label">' + escapeHtml(t("energy.sampling.energy_window")) + '</div><div class="sampling-metric-value">' + escapeHtml(saturation.energy_window_kcal_mol != null ? saturation.energy_window_kcal_mol.toFixed(2) + ' kcal/mol' : '--') + '</div></div>';
  cardsHtml += '<div class="sampling-metric-card"><div class="sampling-metric-label">' + escapeHtml(t("energy.sampling.saturation")) + '</div><div class="sampling-metric-value">' + satDots + ' ' + escapeHtml(satLevelI18n) + '</div></div>';
  var chartHtml = '';
  if (cumulativeUnique.length > 1) {
    var geom = energyGraphChartGeometry();
    var W = geom.W, H = 160, pad = { left: 50, right: 20, top: 20, bottom: 30 };
    var Pw = W - pad.left - pad.right, Ph = H - pad.top - pad.bottom;
    var xExtent = [cumulativeUnique[0].time_ps, cumulativeUnique[cumulativeUnique.length - 1].time_ps];
    var yExtent = [0, Math.max.apply(null, cumulativeUnique.map(function(p) { return p.unique; }))];
    var xSpread = Math.max(1e-9, xExtent[1] - xExtent[0]);
    var ySpread = Math.max(1e-9, yExtent[1] - yExtent[0]);
    var xDom = [xExtent[0] - xSpread * 0.02, xExtent[1] + xSpread * 0.02];
    var yDom = [0, yExtent[1] + ySpread * 0.1];
    function xFor(v) { return pad.left + ((v - xDom[0]) / (xDom[1] - xDom[0] || 1)) * Pw; }
    function yFor(v) { return pad.top + (1 - ((v - yDom[0]) / (yDom[1] - yDom[0] || 1))) * Ph; }
    var linePath = cumulativeUnique.map(function(pt, idx) {
      return (idx === 0 ? "M" : "L") + xFor(pt.time_ps) + " " + yFor(pt.unique);
    }).join(" ");
    chartHtml = '<div class="sampling-cumulative-chart"><svg viewBox="0 0 ' + W + ' ' + H + '">';
    chartHtml += '<path class="energy-line" d="' + linePath + '"></path>';
    chartHtml += energyGraphAxesMarkup(xDom, yDom, { W: W, H: H, pad: pad, plotRect: { left: pad.left, top: pad.top, width: Pw, height: Ph } });
    chartHtml += '<text class="energy-axis-text" x="' + ((pad.left + W - pad.right) / 2) + '" y="' + (H - 5) + '" text-anchor="middle">' + escapeHtml(t("energy.sampling.axis.time_ps")) + '</text>';
    chartHtml += '<text class="energy-axis-text" x="18" y="' + (H / 2) + '" text-anchor="middle" transform="rotate(-90 18 ' + (H / 2) + ')">' + escapeHtml(t("energy.sampling.cumulative_unique")) + '</text>';
    chartHtml += '</svg></div>';
  }
  var timelineHtml = '';
  if (basins.length > 0) {
    var nodes = Array.isArray(data.nodes) ? data.nodes : [];
    var timelineWidth = 600;
    var timelineHeight = 30;
    var timeRange = nodes.length > 1 ? (nodes[nodes.length - 1].x - nodes[0].x) : 1;
    timelineHtml = '<div class="sampling-timeline-ribbon"><svg viewBox="0 0 ' + timelineWidth + ' ' + timelineHeight + '">';
    var segments = [];
    var currentBasin = null;
    var segStart = 0;
    nodes.forEach(function(node, idx) {
      var bid = node.metadata ? node.metadata.basin_id : 0;
      if (bid !== currentBasin) {
        if (currentBasin !== null) {
          segments.push({ basin: currentBasin, start: segStart, end: idx - 1 });
        }
        currentBasin = bid;
        segStart = idx;
      }
    });
    if (currentBasin !== null) {
      segments.push({ basin: currentBasin, start: segStart, end: nodes.length - 1 });
    }
    segments.forEach(function(seg) {
      var x1 = (seg.start / (nodes.length - 1 || 1)) * timelineWidth;
      var x2 = (seg.end / (nodes.length - 1 || 1)) * timelineWidth;
      timelineHtml += '<rect x="' + x1 + '" y="0" width="' + Math.max(1, x2 - x1) + '" height="' + timelineHeight + '" fill="' + samplingBasinColor(seg.basin) + '" opacity="0.7"></rect>';
    });
    basins.forEach(function(b) {
      if (b.first_seen_index != null && nodes.length > 0) {
        var tickX = (b.first_seen_index / (nodes.length - 1 || 1)) * timelineWidth;
        timelineHtml += '<line x1="' + tickX + '" y1="0" x2="' + tickX + '" y2="' + timelineHeight + '" stroke="#fff" stroke-width="1.5" opacity="0.9"></line>';
      }
    });
    timelineHtml += '</svg></div>';
  }
  var legendHtml = '<div class="sampling-basin-legend">';
  basins.forEach(function(b) {
    legendHtml += '<span class="sampling-legend-item"><span class="sampling-legend-swatch" style="background:' + samplingBasinColor(b.basin_id) + '"></span>';
    legendHtml += escapeHtml(t("energy.sampling.basin_legend")) + ' ' + escapeHtml(String(b.basin_id));
    legendHtml += ' · ' + escapeHtml(t("energy.sampling.first_seen")) + ': ' + escapeHtml(b.first_seen_ps != null ? b.first_seen_ps.toFixed(2) + 'ps' : '--');
    legendHtml += ' · ' + escapeHtml(t("energy.sampling.visit_count")) + ': ' + escapeHtml(String(b.visit_count || 0));
    legendHtml += '</span>';
  });
  legendHtml += '</div>';
  return '<div class="sampling-coverage-panel">' +
    '<div class="sampling-metrics-grid">' + cardsHtml + '</div>' +
    chartHtml +
    timelineHtml +
    legendHtml +
  '</div>';
}

/* ── Sampling Workspace Markup ── */
function samplingWorkspaceMarkup(data) {
  var metadata = data.metadata || {};
  var axisOptions = metadata.axis_options || {};
  var xOptions = Array.isArray(axisOptions.x) ? axisOptions.x : ["time_ps", "step", "frame"];
  var yOptions = Array.isArray(axisOptions.y) ? axisOptions.y : ["potential", "relative"];
  var activeSubview = samplingState.activeSubview || "energy";
  if (samplingState.jobId !== energyGraphState.jobId) {
    samplingState.activeSubview = "energy";
    samplingState.xAxis = "time_ps";
    samplingState.yAxis = "energy_potential";
    samplingState.jobId = energyGraphState.jobId;
    activeSubview = "energy";
  }
  var series = Array.isArray(data.series) ? data.series : [];
  var currentSeries = series.find(function(s) { return s.id === samplingState.yAxis; }) || series[0];
  if (currentSeries) {
    data = Object.assign({}, data, { series: [currentSeries] });
  }
  var tabButtons = [
    { id: "energy", label: t("energy.sampling.tabs.energy") },
    { id: "space", label: t("energy.sampling.tabs.space") },
    { id: "coverage", label: t("energy.sampling.tabs.coverage") }
  ].map(function(tab) {
    return '<button class="sampling-subview-tab' + (tab.id === activeSubview ? ' active' : '') + '" data-sampling-view="' + tab.id + '">' + escapeHtml(tab.label) + '</button>';
  }).join("");
  var xSelectorHtml = '<select class="sampling-axis-select" data-sampling-x-axis>';
  xOptions.forEach(function(opt) {
    xSelectorHtml += '<option value="' + escapeHtml(opt) + '"' + (opt === samplingState.xAxis ? ' selected' : '') + '>' + escapeHtml(t("energy.sampling.axis." + opt)) + '</option>';
  });
  xSelectorHtml += '</select>';
  var ySelectorHtml = '<select class="sampling-axis-select" data-sampling-y-axis>';
  yOptions.forEach(function(opt) {
    var seriesId = opt === "potential" ? "energy_potential" : "relative_energy";
    var seriesObj = (Array.isArray(data.series) ? data.series : []).find(function(s) { return s.id === seriesId; });
    var label = seriesObj ? seriesObj.label : opt;
    ySelectorHtml += '<option value="' + escapeHtml(seriesId) + '"' + (seriesId === samplingState.yAxis ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
  });
  ySelectorHtml += '</select>';
  var subviewContent = '';
  if (activeSubview === "energy") {
    subviewContent = '<div class="sampling-subview-content" data-sampling-subview="energy">' +
      '<div class="sampling-axis-controls">' + xSelectorHtml + ySelectorHtml + '</div>' +
      '<div class="energy-chart-scroll" data-energy-chart-scroll>' + samplingEnergyTrajectorySvg(data) + '</div>' +
    '</div>';
  } else if (activeSubview === "space") {
    subviewContent = '<div class="sampling-subview-content" data-sampling-subview="space">' +
      '<div class="energy-chart-scroll" data-energy-chart-scroll>' + samplingSpaceScatterSvg(data) + '</div>' +
    '</div>';
  } else {
    subviewContent = '<div class="sampling-subview-content" data-sampling-subview="coverage">' +
      samplingCoverageMarkup(data) +
    '</div>';
  }
  var viewToggleHtml = '';
  var availableViews = Array.isArray(data.available_views) ? data.available_views : [];
  if (availableViews.indexOf("conformer") >= 0 && availableViews.indexOf("sampling") >= 0) {
    viewToggleHtml = '<div class="sampling-view-toggle">' +
      '<button class="sampling-view-toggle-btn' + (data.view_type === "conformer" ? ' active' : '') + '" data-sampling-switch-view="conformer">' + escapeHtml(t("energy.sampling.view_toggle.conformer")) + '</button>' +
      '<button class="sampling-view-toggle-btn' + (data.view_type === "sampling" ? ' active' : '') + '" data-sampling-switch-view="sampling">' + escapeHtml(t("energy.sampling.view_toggle.sampling")) + '</button>' +
    '</div>';
  }
  return '<div class="sampling-workspace" data-energy-workspace data-energy-view="sampling">' +
    '<div class="energy-workspace-header">' +
      '<div class="energy-workspace-title">' + escapeHtml(String(data.title || "")) + '</div>' +
      viewToggleHtml +
    '</div>' +
    '<div class="sampling-subview-tabs">' + tabButtons + '</div>' +
    subviewContent +
    '<div class="energy-inspector-panel">' +
      energyGraphInspectorMarkup(data) +
    '</div>' +
  '</div>';
}

function energyGraphSelectedNode(data) {
  return getEnergyChartRenderer().energyGraphSelectedNode.apply(null, arguments);
}

function energyGraphSelectedAnnotation(data, node) {
  return getEnergyChartRenderer().energyGraphSelectedAnnotation.apply(null, arguments);
}

/* Candidate action card markup */
function energyCandidateCardMarkup(data, node, opts) {
  var frameIdStr = String(node.id || "");
  var frameIndexNum = node.frame_index == null ? null : Number(node.frame_index);
  var isPES = !!(lastSelectedJob && lastSelectedJob.spec && lastSelectedJob.spec.workflow === "PESsearch");
  var canEditRole = opts.canEditRole;
  var selectionLocked = opts.selectionLocked;
  var workingEntry = opts.workingEntry;
  var jobTerminal = opts.jobTerminal;
  var candEntry = opts.candEntry;
  var currentRole = opts.currentRole;
  var recommendedRole = opts.recommendedRole;
  var nodeGeometryOk = opts.nodeGeometryOk !== false;
  var frameUnconverged = !!opts.frameUnconverged;

  /* Dual mode: scan/PES uses s2scanState; non-PES uses POST /frame-candidate.
     The card flavour is decided by workflow + view_type — NEVER by
     canEditRole.  canEditRole also encodes !selectionLocked, so keying the
     branch on it made a *locked* PES selection degrade to the generic
     save-candidate card, whose handler POSTs /frame-candidate — an endpoint
     the backend rejects for PESsearch jobs.  canEditRole only decides
     whether the PES role buttons below are disabled. */
  var isScanPES = isPES && String(data.view_type || "") === "scan";

  /* Disabled state: job not terminal (must precede saved variant) */
  if (!jobTerminal && !isScanPES) {
    var savedLabel = "";
    if (candEntry) {
      var sl = String(candEntry.display_label || candEntry.role || "");
      savedLabel = '<div class="energy-card-status"><span class="energy-card-status-text">' + escapeHtml(t("energy.card.saved") + (sl ? " · " + sl : "")) + '</span></div>';
    }
    return '<div class="energy-candidate-card" data-energy-card-state="disabled">' +
      '<div class="energy-card-title">' + escapeHtml(t("energy.card.title")) + '</div>' +
      savedLabel +
      '<div class="energy-card-subtitle">' + escapeHtml(t("energy.card.subtitle_save")) + '</div>' +
      '<div class="energy-card-type-row">' +
        '<button type="button" class="energy-card-type-btn" data-energy-candidate-role="TS" data-candidate-type="ts" data-energy-frame-id="' + escapeHtml(frameIdStr) + '" disabled>' + escapeHtml(t("energy.card.type_ts")) + '</button>' +
        '<button type="button" class="energy-card-type-btn" data-energy-candidate-role="INT" data-candidate-type="int" data-energy-frame-id="' + escapeHtml(frameIdStr) + '" disabled>' + escapeHtml(t("energy.card.type_int")) + '</button>' +
      '</div>' +
      '<div class="energy-card-action-row">' +
        '<button type="button" class="energy-card-save-btn" data-energy-action="save-candidate" data-energy-frame-id="' + escapeHtml(frameIdStr) + '" disabled>' + escapeHtml(t("energy.inspector.save_candidate")) + '</button>' +
      '</div>' +
      '<div class="energy-card-disabled-note">' + escapeHtml(t("energy.inspector.save_after_complete")) + '</div>' +
      '<div class="energy-card-error" data-energy-card-error style="display:none;"></div>' +
    '</div>';
  }

  /* Saved state: show status + "修改类型" */
  if (candEntry && !isScanPES) {
    var statusLabel = String(candEntry.display_label || candEntry.role || "");
    var statusText = t("energy.card.saved") + (statusLabel ? " · " + statusLabel : "");
    return '<div class="energy-candidate-card" data-energy-card-state="saved">' +
      '<div class="energy-card-title">' + escapeHtml(t("energy.card.title")) + '</div>' +
      '<div class="energy-card-status">' +
        '<span class="energy-card-status-text">' + escapeHtml(statusText) + '</span>' +
        '<button type="button" class="energy-card-change-btn" data-energy-action="change-role" title="' + escapeHtml(t("energy.inspector.change_role")) + '">' + escapeHtml(t("energy.inspector.change_role")) + '</button>' +
      '</div>' +
      '<div class="energy-card-type-row" data-energy-card-type-row style="display:none;">' +
        '<button type="button" class="energy-card-type-btn" data-energy-candidate-role="TS" data-candidate-type="ts" data-energy-frame-id="' + escapeHtml(frameIdStr) + '">' + escapeHtml(t("energy.card.type_ts")) + '</button>' +
        '<button type="button" class="energy-card-type-btn" data-energy-candidate-role="INT" data-candidate-type="int" data-energy-frame-id="' + escapeHtml(frameIdStr) + '">' + escapeHtml(t("energy.card.type_int")) + '</button>' +
      '</div>' +
      '<div class="energy-card-action-row" data-energy-card-action-row style="display:none;">' +
        '<button type="button" class="energy-card-save-btn" data-energy-action="save-candidate" data-energy-frame-id="' + escapeHtml(frameIdStr) + '" disabled>' + escapeHtml(t("energy.inspector.save_candidate")) + '</button>' +
        '<button type="button" class="energy-card-cancel-btn" data-energy-action="card-cancel">' + escapeHtml(t("energy.card.cancel")) + '</button>' +
      '</div>' +
      '<div class="energy-card-error" data-energy-card-error style="display:none;"></div>' +
    '</div>';
  }

  /* Scan/PES mode: immediate role selection */
  if (isScanPES) {
    var roleStateText = selectionLocked
      ? t("energy.card.locked")
      : workingEntry
        ? (workingEntry.saved && !candidateReviewState().dirty ? t("energy.card.saved") : t("energy.card.modified"))
        : t("energy.card.unmarked");
    var roleStateNote = selectionLocked ? ' · ' + t("energy.card.locked_note") : '';
    var noneHint = escapeHtml(t("energy.card.type_none_hint"));
    return '<div class="energy-candidate-card" data-energy-card-state="scan">' +
      '<div class="energy-card-title">' + escapeHtml(t("energy.card.title")) + '</div>' +
      '<div class="energy-card-subtitle">' + escapeHtml(t("energy.card.subtitle_mark")) + '</div>' +
      '<div class="energy-card-type-row">' +
        '<button type="button" class="energy-card-type-btn' + (currentRole === "ts" ? " active" : "") + '" data-energy-role="ts" data-candidate-type="ts"' + (canEditRole ? "" : " disabled") + '>' + escapeHtml(t("energy.card.type_ts")) + '</button>' +
        '<button type="button" class="energy-card-type-btn' + (currentRole === "intermediate" ? " active" : "") + '" data-energy-role="intermediate" data-candidate-type="int"' + (canEditRole ? "" : " disabled") + '>' + escapeHtml(t("energy.card.type_int")) + '</button>' +
        '<button type="button" class="energy-card-type-btn' + (currentRole === "" ? " active" : "") + '" data-energy-role="none" title="' + noneHint + '"' + (canEditRole ? "" : " disabled") + '>' + escapeHtml(t("energy.card.type_none")) + '</button>' +
      '</div>' +
      (canEditRole ? '<div class="energy-card-disabled-note">' + noneHint + '</div>' : '') +
      (!nodeGeometryOk ? '<div class="energy-card-disabled-note">该帧缺少有效几何 · 仅可查看，不能确认为候选</div>' : '') +
      (frameUnconverged && workingEntry ? '<div class="energy-card-disabled-note">未收敛帧 · 标注为人工判断，待后续验证</div>' : '') +
      (recommendedRole ? '<div class="energy-card-subtitle">' + escapeHtml(t("energy.card.algo_rec", { role: recommendedRole === "ts" ? "TS" : "INT" })) + '</div>' : '') +
      '<div class="energy-card-disabled-note">' + escapeHtml(roleStateText) + escapeHtml(roleStateNote) + '</div>' +
      '<div class="energy-card-error" data-energy-card-error style="display:none;"></div>' +
    '</div>';
  }

  /* Default: idle state for non-PES terminal */
  return '<div class="energy-candidate-card" data-energy-card-state="idle">' +
    '<div class="energy-card-title">' + escapeHtml(t("energy.card.title")) + '</div>' +
    '<div class="energy-card-subtitle">' + escapeHtml(t("energy.card.subtitle_save")) + '</div>' +
    '<div class="energy-card-type-row">' +
      '<button type="button" class="energy-card-type-btn" data-energy-candidate-role="TS" data-candidate-type="ts" data-energy-frame-id="' + escapeHtml(frameIdStr) + '">' + escapeHtml(t("energy.card.type_ts")) + '</button>' +
      '<button type="button" class="energy-card-type-btn" data-energy-candidate-role="INT" data-candidate-type="int" data-energy-frame-id="' + escapeHtml(frameIdStr) + '">' + escapeHtml(t("energy.card.type_int")) + '</button>' +
    '</div>' +
    '<div class="energy-card-action-row">' +
      '<button type="button" class="energy-card-save-btn" data-energy-action="save-candidate" data-energy-frame-id="' + escapeHtml(frameIdStr) + '" disabled>' + escapeHtml(t("energy.inspector.save_candidate")) + '</button>' +
      '<button type="button" class="energy-card-cancel-btn" data-energy-action="card-cancel">' + escapeHtml(t("energy.card.cancel")) + '</button>' +
    '</div>' +
    '<div class="energy-card-error" data-energy-card-error style="display:none;"></div>' +
  '</div>';
}

function energyGraphInspectorMarkup(data) {
  const node = energyGraphSelectedNode(data);
  if (!node) return '<div class="energy-inspector-body"><div class="energy-empty">暂无选中节点</div></div>';
  const annotation = energyGraphSelectedAnnotation(data, node);
  energyGraphState.selectedNodeId = String(node.id || "");
  const type = annotation ? String(annotation.type || "") : String(node.type || "frame");
  const badgeClass = type === "ts" ? "ts" : type === "intermediate" ? "int" : node.status === "failed" ? "failed" : "ok";
  /* Header: prefer display_label, then label, then node label */
  const displayLabel = annotation ? String(annotation.display_label || annotation.label || annotation.id) : String(node.label || node.id || "当前节点");
  const metadata = node.metadata || {};
  const frame = node.frame_index == null ? "--" : String(node.frame_index);
  const frameIndexNum = node.frame_index == null ? null : Number(node.frame_index);
  const frameNumber = annotation && annotation.frame_number != null ? Number(annotation.frame_number) : (frameIndexNum != null ? frameIndexNum + 1 : null);
  const scanStep = annotation && annotation.scan_step != null ? Number(annotation.scan_step) : (metadata.scan_step != null ? Number(metadata.scan_step) : null);
  const geometry = String(node.geometry_ref || annotation && annotation.geometry_ref || "");
  const review = candidateReviewState();
  const workingEntry = frameIndexNum == null ? null : review.candidates.find(function(entry) {
    return Number(entry.frame_index) === frameIndexNum;
  });
  const selectionLocked = !!review.selectionLocked;
  const currentRole = workingEntry ? String(workingEntry.role) : "";
  const recommendedRole = workingEntry && workingEntry.recommended_type ? String(workingEntry.recommended_type) : (annotation && annotation.recommended_type ? String(annotation.recommended_type) : "");
  const roleStateText = selectionLocked
    ? "已锁定"
    : workingEntry
      ? (workingEntry.saved && !review.dirty ? "已保存" : "已修改，尚未保存")
      : "未标记";
  const hasReview = !!(data.metadata && data.metadata.review);
  const jobStatus = String(energyGraphState.jobStatus || "");
  const jobTerminal = jobStatus === "completed" || jobStatus === "failed" || jobStatus === "cancelled";
  /* Energy-without-geometry frames (interrupted scans) are view-only: the
     backend refuses to confirm them, so the role buttons stay disabled. */
  const nodeGeometryOk = metadata.selectable !== false && !!String(node.geometry_ref || "");
  const canEditRole = data.view_type === "scan" && frameIndexNum != null && !selectionLocked && hasReview && jobTerminal && nodeGeometryOk;
  const scanFrameUnconverged = data.view_type === "scan" && String(node.status || "") === "failed";
  const nodeStatusText = scanFrameUnconverged ? "未收敛 · 待后续验证" : translateStatus(node.status || "unknown");
  const isScanFrame = data.view_type === "scan" && frameIndexNum != null;
  const nodes = Array.isArray(data.nodes) ? data.nodes : [];
  const nodeIndex = nodes.findIndex(function(item) { return String(item.id) === String(node.id); });
  const frameTotal = nodes.length;
  const frameDisplay = frameTotal > 0 ? ((nodeIndex >= 0 ? nodeIndex + 1 : 1) + ' / ' + frameTotal) : frame;
  const currentXMode = energyGraphState.xAxisMode || (data.metadata && data.metadata.default_x_axis_mode) || (data.x_axis && data.x_axis.default_mode) || "scanned_coordinate";
  var activeXValue = energyGraphNodeX(node, currentXMode);
  var activeXLabel = data.view_type === "scan" ? "扫描坐标" : "横坐标";
  var activeXUnit = String((data.x_axis || {}).unit || "");
  if (currentXMode === "path_length") {
    activeXLabel = "累积路径长度";
    activeXUnit = "Å";
  } else if (currentXMode === "reaction_progress") {
    activeXLabel = "反应进度";
    activeXUnit = "";
  } else if (currentXMode === "frame_index") {
    activeXLabel = "帧序号";
    activeXUnit = "";
  }
  const xAxisLabel = activeXLabel;
  var candidateAttrHtml = "";
  if (isScanFrame && workingEntry) {
    var autoName = energyGenCandidateName(frameIndexNum, workingEntry.role);
    var displayName = workingEntry.name || "";
    var namePlaceholder = escapeHtml(autoName);
    var nameValue = escapeHtml(displayName);
    var nameControl = canEditRole
      ? '<input type="text" class="energy-attr-input" data-energy-candidate-name value="' + nameValue + '" placeholder="' + namePlaceholder + '" maxlength="80" />'
      : '<span class="energy-attr-value" title="点击顶部“选点已锁定”可进入编辑模式">' + escapeHtml(displayName || autoName) + '</span>';
    var candidateIdText = workingEntry.candidate_id ? escapeHtml(workingEntry.candidate_id) : '<span style="color:var(--text-tertiary);font-style:italic;">保存时自动生成</span>';
    var saveStateLabel = workingEntry.saved && !review.dirty ? "已保存" : workingEntry.saved && review.dirty ? "已修改" : "未保存";
    var saveStateClass = workingEntry.saved && !review.dirty ? "ok" : "pending";
    var selSourceText = workingEntry.selection_source === "algorithm" ? "算法推荐" : "手动标记";
    var recText = recommendedRole ? (recommendedRole === "ts" ? "TS" : "INT") : "\u2014";
    candidateAttrHtml =
      '<div class="energy-candidate-attr">' +
      '<div class="energy-candidate-attr-title">候选点属性</div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">结构名称</span>' + nameControl + '</div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">Candidate ID</span><span class="energy-attr-value">' + candidateIdText + '</span></div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">扫描帧</span><span class="energy-attr-value">' + escapeHtml(frameDisplay) + '</span></div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">' + escapeHtml(activeXLabel) + '</span><span class="energy-attr-value">' + escapeHtml(energyGraphFormatNumber(activeXValue, 3)) + (activeXUnit ? ' ' + escapeHtml(activeXUnit) : '') + '</span></div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">能量</span><span class="energy-attr-value">' + escapeHtml(energyGraphFormatNumber(node.energy, 4)) + '</span></div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">算法推荐</span><span class="energy-attr-value">' + escapeHtml(recText) + '</span></div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">标记来源</span><span class="energy-attr-value">' + escapeHtml(selSourceText) + '</span></div>' +
        '<div class="energy-attr-row"><span class="energy-attr-label">保存状态</span><span class="energy-badge ' + saveStateClass + '">' + escapeHtml(saveStateLabel) + '</span></div>' +
      '</div>';
  }
  var isPES = !!(lastSelectedJob && lastSelectedJob.spec && lastSelectedJob.spec.workflow === "PESsearch");
  var frameIdStr = String(node.id || "");
  var candEntry = isPES ? null : energyGraphFrameCandidateFor(data.view_type, node.frame_index);
  var savedBadge = candEntry ? ' <span class="energy-badge ok">' + escapeHtml(t("energy.inspector.saved_badge")) + ' · ' + escapeHtml(String(candEntry.role || "")) + '</span>' : '';
  /* Frame number display: "第 N / total 帧" */
  var frameNavText = frameNumber != null ? ('\u7b2c ' + frameNumber + ' / ' + frameTotal + ' \u5e27') : ('\u5e27 ' + escapeHtml(frameDisplay));
  /* Tertiary debug line: "Step N · frame_index N" */
  var tertiaryParts = [];
  if (scanStep != null) tertiaryParts.push('Step ' + scanStep);
  if (frameIndexNum != null) tertiaryParts.push('frame_index ' + frameIndexNum);
  var tertiaryLine = tertiaryParts.length ? '<div style="color:var(--text-tertiary);font-size:10px;margin-bottom:8px;">' + escapeHtml(tertiaryParts.join(' \u00b7 ')) + '</div>' : '';
  return '<div class="energy-inspector-body">' +
    '<div class="energy-selected-title"><strong>' + escapeHtml(displayLabel) + '</strong><span class="energy-badge ' + badgeClass + '">' + escapeHtml(annotation ? String(annotation.type).toUpperCase() : nodeStatusText) + '</span></div>' +
    '<div class="frame-nav-row" style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">' +
      '<button class="frame-nav-btn" data-energy-action="prev" type="button" title="\u4e0a\u4e00\u5e27" aria-label="\u4e0a\u4e00\u5e27" data-tip="\u4e0a\u4e00\u5e27" data-tip-keys="←"><svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4l-6 6 6 6"/></svg></button>' +
      '<span class="frame-nav-label">' + escapeHtml(frameNavText) + '</span>' +
      '<button class="frame-nav-btn" data-energy-action="next" type="button" title="\u4e0b\u4e00\u5e27" aria-label="\u4e0b\u4e00\u5e27" data-tip="\u4e0b\u4e00\u5e27" data-tip-keys="→"><svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4l6 6-6 6"/></svg></button>' +
    '</div>' +
    tertiaryLine +
    '<dl class="energy-data-list">' +
      '<dt>\u72b6\u6001</dt><dd>' + escapeHtml(nodeStatusText) + '</dd>' +
      (isScanFrame ? '<dt>' + escapeHtml(activeXLabel) + '</dt><dd>' + escapeHtml(energyGraphFormatNumber(activeXValue, 3)) + (activeXUnit ? ' ' + escapeHtml(activeXUnit) : '') + '</dd>' : '') +
      (metadata.cumulative_arclength_A != null && currentXMode !== "path_length" ? '<dt>累积路径长度</dt><dd>' + escapeHtml(energyGraphFormatNumber(metadata.cumulative_arclength_A, 3)) + ' Å</dd>' : '') +
      (metadata.reaction_progress != null && currentXMode !== "reaction_progress" ? '<dt>反应进度</dt><dd>' + escapeHtml((Number(metadata.reaction_progress) * 100).toFixed(1)) + '%</dd>' : '') +
      (metadata.step_rmsd_A != null ? '<dt>步长位移 (RMSD)</dt><dd>' + escapeHtml(energyGraphFormatNumber(metadata.step_rmsd_A, 3)) + ' Å</dd>' : '') +
      '<dt>\u76f8\u5bf9\u80fd\u91cf</dt><dd>' + escapeHtml(energyGraphFormatNumber(node.energy, 4)) + ' kcal/mol</dd>' +
      (metadata.single_point_energy_hartree != null || metadata.scan_energy_hartree != null
        ? '<dt>\u7edd\u5bf9\u80fd\u91cf</dt><dd>' + escapeHtml(energyGraphFormatNumber(metadata.single_point_energy_hartree || metadata.scan_energy_hartree, 6)) + ' E<sub>h</sub></dd>'
        : '') +
      (function() {
        var actualMap = metadata.actual_coordinates || {};
        var keys = Object.keys(actualMap);
        if (keys.length < 2) return "";
        var targetMap = metadata.target_coordinates || {};
        return keys.map(function(key) {
          return '<dt>' + escapeHtml(key) + '</dt><dd>'
            + escapeHtml(energyGraphFormatNumber(targetMap[key], 3)) + ' / '
            + escapeHtml(energyGraphFormatNumber(actualMap[key], 3)) + '</dd>';
        }).join("");
      })() +
      energyInspectorMetadataRows(data, node, metadata) +
      '<dt>\u7ed3\u6784\u6765\u6e90</dt><dd data-expandable title="' + escapeHtml(geometry || "--") + '">' + escapeHtml(geometry || "--") + '</dd>' +
    '</dl>' +
    energyCandidateCardMarkup(data, node, {
      canEditRole: canEditRole,
      selectionLocked: selectionLocked,
      workingEntry: workingEntry,
      jobTerminal: jobTerminal,
      candEntry: candEntry,
      currentRole: currentRole,
      recommendedRole: recommendedRole,
      nodeGeometryOk: nodeGeometryOk,
      frameUnconverged: scanFrameUnconverged,
    }) +
    candidateAttrHtml +
    energyCandidateListMarkup(data) +
    '<div class="energy-inspector-more" style="display:flex;justify-content:flex-end;padding:6px 0;">' +
      '<div style="position:relative;display:inline-block;">' +
        '<button type="button" class="energy-inspector-action-btn" data-energy-action="more-menu" title="' + escapeHtml(t("energy.inspector.more")) + '">⋯</button>' +
        '<div class="energy-inspector-more-menu" data-energy-more-menu style="display:none;position:absolute;right:0;bottom:calc(100% + 4px);z-index:30;background:var(--bg-secondary,#1b212b);border:1px solid rgba(255,255,255,0.1);border-radius:6px;padding:4px;min-width:140px;">' +
          '<button type="button" class="energy-inspector-action-btn" data-energy-action="rename-candidate" style="width:100%;text-align:left;border:0;border-radius:4px;padding:6px 8px;background:transparent;color:var(--text-primary);font-size:11px;cursor:pointer;" onmouseover="this.style.background=\'rgba(78,161,255,0.14)\'" onmouseout="this.style.background=\'transparent\'">' + escapeHtml(t("energy.inspector.change_role")) + '</button>' +
          '<button type="button" class="energy-inspector-action-btn" data-energy-action="export-frame" data-energy-frame-id="' + escapeHtml(frameIdStr) + '" title="' + escapeHtml(t("energy.inspector.export")) + '" style="width:100%;text-align:left;border:0;border-radius:4px;padding:6px 8px;background:transparent;color:var(--text-primary);font-size:11px;cursor:pointer;" onmouseover="this.style.background=\'rgba(78,161,255,0.14)\'" onmouseout="this.style.background=\'transparent\'">' + escapeHtml(t("energy.inspector.export")) + '</button>' +
          (candEntry ? '<button type="button" class="energy-inspector-action-btn danger" data-energy-action="remove-candidate" style="width:100%;text-align:left;border:0;border-radius:4px;padding:6px 8px;background:transparent;font-size:11px;cursor:pointer;" onmouseover="this.style.background=\'rgba(229,83,83,0.14)\'" onmouseout="this.style.background=\'transparent\'">' + escapeHtml(t("energy.inspector.remove_candidate")) + '</button>' : '') +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="detail-hint">\u7ed3\u6784\u67e5\u770b\u5668\u4f1a\u968f\u5f53\u524d\u9009\u4e2d\u5e27\u81ea\u52a8\u540c\u6b65\uff1b\u70b9\u51fb\u56fe\u4e2d\u8282\u70b9\u53ef\u5207\u6362\u5e27\uff0c\u62d6\u52a8 TS/INT \u6807\u8bb0\u53ef\u8c03\u6574\u5019\u9009\u5e27\u3002</div>' +
  '</div>';
}

function energyGraphLegendMarkup(data) {
  return getEnergyChartRenderer().energyGraphLegendMarkup.apply(null, arguments);
}

function optimizationGraphSeries(data, id) {
  var series = Array.isArray(data && data.series) ? data.series : [];
  var selected = series.find(function(item) { return String(item.id) === String(id); });
  if (!selected) selected = series.find(function(item) { return String(item.id) === String(data && data.default_series); });
  if (!selected) selected = series.find(function(item) { return String(item.id) === "relative_energy"; });
  return selected || series[0] || null;
}

function optimizationGraphExactSeries(data, id) {
  var series = Array.isArray(data && data.series) ? data.series : [];
  return series.find(function(item) { return String(item.id) === String(id); }) || null;
}

var OPTIMIZATION_VIEW_IDS = ["energy", "force", "energy_derivative", "force_derivative"];
var OPTIMIZATION_SERIES_COLORS = {
  relative_energy: "#55aaff",
  scf_energy: "#55aaff",
  delta_energy: "#b792ff",
  rms_gradient: "#42d6a4",
  max_gradient: "#ffb85d",
  rms_displacement: "#ff8f6b",
  max_displacement: "#5dd6ff",
  rms_gradient_delta: "#42d6a4",
  max_gradient_delta: "#ffb85d",
};

function optimizationViewConfig(view) {
  var configs = {
    energy: { label: "能量", title: "能量变化", subtitle: "相对初始能量 / SCF 能量" },
    force: { label: "受力", title: "受力与位移", subtitle: "梯度 Eh/Bohr（左轴） · 位移 bohr（右轴）", dual: true },
    energy_derivative: { label: "能量导数", title: "能量导数", subtitle: "步间能量变化 ΔE · kcal/mol" },
    force_derivative: { label: "受力导数", title: "受力导数", subtitle: "步间梯度变化 Δg · Eh/Bohr" },
  };
  return configs[view] || configs.energy;
}

function optimizationViewSeries(data, view) {
  function unique(list) {
    return list.filter(function(item, index, all) {
      return item && all.findIndex(function(other) { return other && other.id === item.id; }) === index;
    });
  }
  function byIds(ids) {
    return unique(ids.map(function(id) { return optimizationGraphExactSeries(data, id); }));
  }
  if (view === "force") return { left: byIds(["rms_gradient", "max_gradient"]), right: byIds(["rms_displacement", "max_displacement"]) };
  if (view === "energy_derivative") return { left: byIds(["delta_energy"]), right: [] };
  if (view === "force_derivative") return { left: byIds(["rms_gradient_delta", "max_gradient_delta"]), right: [] };
  var selected = optimizationGraphSeries(data, energyGraphState.energySeriesId);
  if (selected) energyGraphState.energySeriesId = String(selected.id);
  return { left: selected ? [selected] : [], right: [] };
}

function optimizationSeriesClass(id) {
  if (id === "rms_gradient" || id === "rms_gradient_delta") return "optimization-series-rms";
  if (id === "max_gradient" || id === "max_gradient_delta") return "optimization-series-max";
  if (id === "rms_displacement") return "optimization-series-rmsd";
  if (id === "max_displacement") return "optimization-series-maxd";
  if (id === "delta_energy") return "optimization-series-delta";
  return "optimization-series-energy";
}

function optimizationChartGeometry(hasRightAxis) {
  var vs = energyGraphState.optimizationViewSize || { width: 0, height: 0 };
  var W = Math.max(280, vs.width || 820);
  var H = Math.max(220, vs.height || 340);
  var pad = { left: 64, right: hasRightAxis ? 68 : 24, top: 24, bottom: 44 };
  return { W: W, H: H, pad: pad };
}

function optimizationSeriesExtent(seriesList, nodeX) {
  var values = [];
  seriesList.forEach(function(series) {
    (Array.isArray(series.values) ? series.values : []).forEach(function(value, index) {
      var y = energyGraphValue(value);
      if (y !== null && nodeX[index] != null) values.push(y);
    });
  });
  if (!values.length) return null;
  var lo = Math.min.apply(null, values);
  var hi = Math.max.apply(null, values);
  if (lo === hi) { var span = Math.max(Math.abs(lo) * 0.1, 0.001); lo -= span; hi += span; }
  var pad = (hi - lo) * 0.12;
  return [lo - pad, hi + pad];
}

function optimizationStoreDomains(view, x, y, y2) {
  energyGraphState.optimizationDomains[view] = { x: x, y: y, y2: y2 };
}

var optimizationFramePending = false;
function optimizationGraphScheduleRender() {
  if (optimizationFramePending) return;
  optimizationFramePending = true;
  requestAnimationFrame(function() { optimizationFramePending = false; optimizationGraphRenderCurrent(); });
}

function optimizationGraphBuildSvg(data, view) {
  /* Data processing: view config, series groups, domains, coordinate mappers. */
  var nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
  if (!nodes.length) return '<div class="energy-empty"><div><strong>暂无优化周期</strong><span>优化数据正在生成。</span></div></div>';
  var cfg = optimizationViewConfig(view);
  var groups = optimizationViewSeries(data, view);
  if (!groups.left.length && !groups.right.length) {
    var emptyHint = view === "energy_derivative" || view === "force_derivative"
      ? "导数视图需要至少 2 个优化周期。"
      : "当前输出还没有该类收敛数据。";
    return '<div class="energy-empty"><div><strong>暂无可绘制指标</strong><span>' + emptyHint + '</span></div></div>';
  }
  var hasRight = groups.right.length > 0;
  var geom = optimizationChartGeometry(hasRight);
  var W = geom.W, H = geom.H, pad = geom.pad;
  /* ── Pre-compute candidate band height and adjust pad.top ── */
  var _optAnnotations = Array.isArray(data.annotations) ? data.annotations : [];
  var _optProjected = energyProjectCandidatesAsAnnotations(data);
  var _optMerged = _optAnnotations.concat(_optProjected);
  var _optDeduped = [];
  var _optSeen = {};
  _optMerged.forEach(function(a) { if (a && a.id && !_optSeen[a.id]) { _optSeen[a.id] = true; _optDeduped.push(a); } });
  var _optCandidates = _optDeduped.filter(function(a) { return a && (a.type === "ts" || a.type === "intermediate"); });
  var _optItems = [];
  _optCandidates.forEach(function(a) {
    var fi = a.frame_index;
    var annNode = nodes.find(function(item) { return Number(item.frame_index) === Number(fi); });
    var dataX = annNode ? energyGraphValue(annNode.x) : null;
    if (dataX === null) return;
    var dataY = annNode ? (energyGraphValue(annNode.energy) || energyGraphValue(a.y) || 0) : 0;
    _optItems.push({ annotation: a, dataX: dataX, dataY: dataY, frameIndex: fi, type: String(a.type || "") });
  });
  _optItems.sort(function(a, b) { return a.dataX - b.dataX; });
  /* Temporary xFor using base domain (pad.top doesn't affect x mapping) */
  var _optNodeX = nodes.map(function(node, index) { return energyGraphValue(node.x) == null ? index + 1 : Number(node.x); });
  var _optXMin = Math.min.apply(null, _optNodeX), _optXMax = Math.max.apply(null, _optNodeX);
  if (_optXMin === _optXMax) { _optXMin -= 0.5; _optXMax += 0.5; }
  var _optBaseX = [_optXMin, _optXMax];
  var _optPw = W - pad.left - pad.right;
  function _optXFor(value) { return pad.left + ((value - _optBaseX[0]) / (_optBaseX[1] - _optBaseX[0] || 1)) * _optPw; }
  var _optBandHeight = energyCandidateBandPreHeight(_optItems, _optXFor, pad.left, W - pad.right);
  if (_optBandHeight > 0) {
    pad.top += _optBandHeight;
    geom.pad.top = pad.top;
    H = geom.H;
  }
  var nodeX = nodes.map(function(node, index) { return energyGraphValue(node.x) == null ? index + 1 : Number(node.x); });
  var xMin = Math.min.apply(null, nodeX), xMax = Math.max.apply(null, nodeX);
  if (xMin === xMax) { xMin -= 0.5; xMax += 0.5; }
  var baseX = [xMin, xMax];
  var baseY = optimizationSeriesExtent(groups.left, nodeX) || [0, 1];
  var baseY2 = hasRight ? optimizationSeriesExtent(groups.right, nodeX) : null;
  if (view === "force" || view === "energy_derivative" || view === "force_derivative") {
    if (baseY[0] > 0) baseY[0] = 0;
    if (baseY2 && baseY2[0] > 0) baseY2[0] = 0;
    if (view !== "force" && baseY[1] < 0) baseY[1] = 0;
  }
  var base = { x: baseX, y: baseY, y2: baseY2 ? baseY2.slice() : null };
  var stored = (energyGraphState.optimizationDomains || {})[view];
  var xDom = stored && stored.x ? stored.x.slice() : baseX.slice();
  var yDom = stored && stored.y ? stored.y.slice() : baseY.slice();
  var y2Dom = stored && stored.y2 ? stored.y2.slice() : (baseY2 ? baseY2.slice() : null);
  energyGraphState.optimizationPlot = { view: view, geom: geom, base: base, xDom: xDom, yDom: yDom, y2Dom: y2Dom };
  var Pw = W - pad.left - pad.right, Ph = H - pad.top - pad.bottom;
  function xFor(value) { return pad.left + ((value - xDom[0]) / (xDom[1] - xDom[0] || 1)) * Pw; }
  function yFor(value) { return pad.top + (1 - ((value - yDom[0]) / (yDom[1] - yDom[0] || 1))) * Ph; }
  function y2For(value) { var dom = y2Dom || [0, 1]; return pad.top + (1 - ((value - dom[0]) / (dom[1] - dom[0] || 1))) * Ph; }

  /* Build series paths + per-point configs. */
  var seriesCfgs = [];
  var allPoints = [];
  function buildSeries(series, axisFor) {
    var values = Array.isArray(series.values) ? series.values : [];
    var color = OPTIMIZATION_SERIES_COLORS[series.id] || "#55aaff";
    var path = [];
    values.forEach(function(value, index) {
      var y = energyGraphValue(value);
      if (y === null || nodeX[index] == null) return;
      if (nodeX[index] < xDom[0] || nodeX[index] > xDom[1]) return;
      path.push((path.length ? "L" : "M") + xFor(nodeX[index]) + " " + axisFor(y));
    });
    if (path.length) seriesCfgs.push({ linePath: path.join(" "), cssClass: optimizationSeriesClass(String(series.id)) });
    values.forEach(function(value, index) {
      var y = energyGraphValue(value);
      if (y === null || nodeX[index] == null || !nodes[index]) return;
      if (nodeX[index] < xDom[0] || nodeX[index] > xDom[1]) return;
      var isSelected = String(nodes[index].id) === String(energyGraphState.selectedNodeId);
      allPoints.push({
        cx: xFor(nodeX[index]), cy: axisFor(y),
        r: isSelected ? '5.5' : '3.5',
        cssClass: isSelected ? ' selected' : '',
        frameIndex: nodes[index].frame_index, nodeId: nodes[index].id,
        fill: color, stroke: "#0b0f14", strokeWidth: "1.2",
        title: String(nodes[index].label || "Cycle") + ' · ' + String(series.label || series.id) + ': ' + energyGraphFormatNumber(y, 6) + ' ' + String(series.unit || ""),
      });
    });
  }
  groups.left.forEach(function(series) { buildSeries(series, yFor); });
  groups.right.forEach(function(series) { buildSeries(series, y2For); });

  /* Zero baseline. */
  var zeroBaseline = (view !== "energy" && view !== "force" && yDom[0] < 0 && yDom[1] > 0);

  /* Threshold lines (force view). */
  var thresholdsCfg = null;
  if (view === "force") {
    var thresholdData = (data.metadata || {}).thresholds || {};
    var thresholdItems = [
      { key: "rms_gradient", label: "RMS 阈值", disp: false }, { key: "max_gradient", label: "MAX 阈值", disp: false },
      { key: "rms_displacement", label: "RMS 位移阈值", disp: true }, { key: "max_displacement", label: "MAX 位移阈值", disp: true },
    ];
    thresholdItems.forEach(function(item) {
      var dom = item.disp ? y2Dom : yDom;
      if (!dom) return;
      var threshold = energyGraphValue(thresholdData[item.key]);
      if (threshold === null || threshold < dom[0] || threshold > dom[1]) return;
      var ty = item.disp ? y2For(threshold) : yFor(threshold);
      if (!thresholdsCfg) thresholdsCfg = [];
      thresholdsCfg.push({
        y: ty, label: item.label,
        lineClass: "optimization-threshold" + (item.disp ? " disp" : ""),
        labelClass: "optimization-threshold-label" + (item.disp ? " disp" : ""),
      });
    });
  }

  /* Selected guide. */
  var selectedGuide = null;
  var selectedNode = energyGraphSelectedNode(data);
  if (selectedNode && energyGraphValue(selectedNode.x) !== null) {
    var sx = Number(selectedNode.x);
    if (sx >= xDom[0] && sx <= xDom[1]) selectedGuide = { x: xFor(sx), y1: pad.top, y2: H - pad.bottom };
  }

  /* Annotations: merge backend + projected candidates. */
  var optAnnotations = Array.isArray(data.annotations) ? data.annotations : [];
  var optProjected = energyProjectCandidatesAsAnnotations(data);
  var optMerged = optAnnotations.concat(optProjected);
  var optDeduped = [];
  var optSeenIds = {};
  optMerged.forEach(function(a) {
    if (!a || !a.id) return;
    if (optSeenIds[a.id]) return;
    optSeenIds[a.id] = true;
    optDeduped.push(a);
  });
  var optAnnMode = view === "energy" ? "full" : "flag";

  /* Unit labels. */
  var leftUnit = String((groups.left[0] || {}).unit || "");
  var rightUnit = hasRight ? String((groups.right[0] || {}).unit || "") : "";
  var unitLabels = (leftUnit || rightUnit) ? { left: leftUnit || undefined, right: rightUnit || undefined } : null;

  /* SVG open tag. */
  var svgOpen = '<svg class="optimization-chart-svg' + (energyGraphState.optimizationTool === "box" ? " mode-box" : "") + '" data-optimization-chart viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + escapeHtml(cfg.title) + '">';

  /* Build SVG via unified core. */
  return energyChartBuildSvg({
    svgOpen: svgOpen,
    clipId: "optimization-plot-clip",
    geom: geom,
    xDom: xDom, yDom: yDom, y2Dom: y2Dom,
    /* axes */
    axesBeforePlot: true,
    gridClass: "optimization-gridline",
    axisTextClass: "optimization-axis-text",
    axisLineClass: "optimization-axis",
    yDigits: view === "energy" ? 2 : 5,
    xDigits: 0,
    yTickXOffset: -8,
    xTickYOffset: 20,
    xTickMode: "integer",
    unitLabels: unitLabels,
    /* plot content */
    plotAttr: "data-optimization-plot",
    series: seriesCfgs,
    zeroBaseline: zeroBaseline,
    zeroLineClass: "optimization-zero-line",
    thresholds: thresholdsCfg,
    /* selected */
    selectedGuide: selectedGuide,
    selectedLineClass: "optimization-current-line",
    selectedInPlotGroup: false,
    /* points */
    points: allPoints,
    pointClass: "optimization-point",
    /* annotations */
    annotationOpts: {
      annotations: optDeduped,
      nodes: nodes,
      xFor: xFor,
      yFor: yFor,
      geom: geom,
      xDom: xDom,
      mode: optAnnMode,
      viewType: "optimization",
      selectedNodeId: String(energyGraphState.selectedNodeId || ""),
      selectionLocked: false,
    },
    annotationsInPlotGroup: false,
    /* overlay + bottom label */
    overlayAttr: "data-optimization-overlay",
    bottomLabel: "优化周期",
  });
}

function optimizationQualityBadge(data) {
  var quality = (data && data.metadata && data.metadata.quality) || {};
  var status = String(quality.status || (data && data.complete ? "complete" : "partial"));
  if (["complete", "partial", "missing"].indexOf(status) < 0) status = "partial";
  var text = status === "complete" ? "数据完整" : status === "missing" ? "数据缺失" : "数据不完整";
  var issueLabels = {
    single_cycle: "仅采集到 1 个周期，曲线只能是相对基准的零值点",
    energy_missing: "部分周期缺少能量",
    rms_gradient_partial: "部分周期缺少 RMS 梯度",
    max_gradient_partial: "部分周期缺少最大梯度",
    rms_displacement_partial: "部分周期缺少 RMS 位移",
    max_displacement_partial: "部分周期缺少最大位移",
  };
  var detail = (Array.isArray(quality.issues) ? quality.issues : []).map(function(issue) {
    return issueLabels[issue] || String(issue);
  }).join("；");
  return '<span class="optimization-quality q-' + status + '" data-optimization-quality' + (detail ? ' title="' + escapeHtml(detail) + '"' : '') + '>' + escapeHtml(text) + '</span>';
}

function optimizationLegendMarkup(data, view) {
  var groups = optimizationViewSeries(data, view);
  var allSeries = groups.left.concat(groups.right);
  if (allSeries.length <= 1) return '';
  var chipFor = function(id) {
    if (id === "rms_gradient" || id === "rms_gradient_delta") return "rms";
    if (id === "max_gradient" || id === "max_gradient_delta") return "max";
    if (id === "rms_displacement") return "rmsd";
    if (id === "max_displacement") return "maxd";
    if (id === "delta_energy") return "delta";
    return "";
  };
  return allSeries.map(function(series) {
    return '<span><i class="' + chipFor(String(series.id)) + '"></i>' + escapeHtml(String(series.label || series.id)) + '</span>';
  }).join("");
}

function energyOptCriteriaRows(data, cycleMetadata) {
  var metadata = data.metadata || {};
  var thresholds = metadata.thresholds || {};
  var cycle = cycleMetadata || {};
  var criteria = [
    { key: "rms_gradient", i18n: "energy.conv.rms_gradient", unit: "Eh/Bohr" },
    { key: "max_gradient", i18n: "energy.conv.max_gradient", unit: "Eh/Bohr" },
    { key: "rms_displacement", i18n: "energy.conv.rms_displacement", unit: "bohr" },
    { key: "max_displacement", i18n: "energy.conv.max_displacement", unit: "bohr" }
  ];
  return criteria.map(function(c) {
    var value = cycle[c.key];
    var threshold = thresholds[c.key];
    var valueStr = value != null ? energyGraphFormatNumber(value, 6) : null;
    var thresholdStr = threshold != null ? energyGraphFormatNumber(threshold, 6) : null;
    var status, badgeClass;
    if (value == null) {
      status = t("energy.conv.value_missing");
      badgeClass = "missing";
    } else if (threshold == null) {
      status = t("energy.conv.threshold_missing");
      badgeClass = "missing";
    } else if (value <= threshold) {
      status = t("energy.conv.met");
      badgeClass = "ok";
    } else {
      status = t("energy.conv.unmet");
      badgeClass = "err";
    }
    return {
      label: t(c.i18n),
      value: valueStr,
      threshold: thresholdStr,
      unit: c.unit,
      status: status,
      badgeClass: badgeClass
    };
  });
}

/* ── Dual-mode status panel: explicit user pick → per-cycle convergence;
   otherwise → task status card.  The model functions are pure so the render
   path never reads metadata.last_cycle directly. ── */
function optimizationExplicitSelectedNode(data) {
  if (energyGraphState.selectionOrigin !== "user") return null;
  var nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
  return nodes.find(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); }) || null;
}

function buildSelectedCycleConvergenceModel(data, node) {
  var nodeMeta = (node && node.metadata) || {};
  var cycle = nodeMeta.cycle != null ? nodeMeta.cycle : (node && node.x != null ? node.x : null);
  return {
    mode: "selected",
    cycle: cycle,
    rows: energyOptCriteriaRows(data, nodeMeta)
  };
}

function optimizationLastCycleIncomplete(data) {
  var nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
  if (!nodes.length) return false;
  var lastMeta = nodes[nodes.length - 1].metadata || {};
  return ["rms_gradient", "max_gradient", "rms_displacement", "max_displacement"].some(function(key) {
    return lastMeta[key] == null;
  });
}

function buildTaskStatusModel(data, job) {
  var meta = (data && data.metadata) || {};
  var nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
  var lastNode = nodes.length ? nodes[nodes.length - 1] : null;
  var rawStatus = String((job && job.status) || (data && data.status) || "").toLowerCase();
  var currentCycle = meta.current_cycle != null ? meta.current_cycle : (lastNode && lastNode.x != null ? lastNode.x : null);
  var lastCycle = lastNode && lastNode.x != null ? lastNode.x : (meta.n_cycles != null ? meta.n_cycles : null);
  var kind;
  if (rawStatus === "queued" || rawStatus === "starting" || rawStatus === "pending") kind = "queued";
  else if (rawStatus === "running" || rawStatus === "partial") kind = "running";
  else if (rawStatus === "paused") kind = "paused";
  else if (rawStatus === "cancelling") kind = "cancelling";
  else if (rawStatus === "waiting_review") kind = "waiting_review";
  else if (rawStatus === "completed") kind = "completed";
  else if (rawStatus === "failed") kind = "failed";
  else if (rawStatus === "cancelled") kind = "cancelled";
  else kind = "";
  var statusLabel = kind ? t("energy.task.status." + kind) : translateStatus(rawStatus || "unknown");
  var notes = [];
  if ((kind === "running" || kind === "paused") && job && job.current_stage && job.current_stage !== "optimize") {
    notes.push(translateStageName(job.current_stage, job.stage_label));
  }
  if ((kind === "running" || kind === "paused") && currentCycle != null) {
    notes.push(t("energy.task.current_cycle", { n: currentCycle }));
  }
  if (kind === "completed" && data && data.complete) {
    notes.push(t("energy.task.converged"));
  }
  if ((kind === "failed" || kind === "cancelled") && lastCycle != null) {
    notes.push(t("energy.task.last_cycle", { n: lastCycle }));
  }
  if (kind === "failed" && optimizationLastCycleIncomplete(data)) {
    notes.push(t("energy.task.incomplete_cycle"));
  }
  return { mode: "task", kind: kind, statusLabel: statusLabel, notes: notes };
}

function optimizationStatusPanelModel(data, job) {
  var selected = optimizationExplicitSelectedNode(data);
  if (selected) {
    return buildSelectedCycleConvergenceModel(data, selected);
  }
  return buildTaskStatusModel(data, job);
}

function optimizationStatusPanelMarkup(data) {
  var model = optimizationStatusPanelModel(data, lastSelectedJob);
  if (model.mode === "selected") {
    var rowsHtml = model.rows.map(function(r) {
      var valueDisplay = r.value != null ? escapeHtml(r.value) + ' ' + escapeHtml(r.unit) : '--';
      var thresholdDisplay = r.threshold != null ? escapeHtml(r.threshold) + ' ' + escapeHtml(r.unit) : '--';
      return '<div class="energy-conv-row">' +
        '<span class="energy-conv-label">' + escapeHtml(r.label) + '</span>' +
        '<span class="energy-conv-value">' + valueDisplay + '</span>' +
        '<span class="energy-conv-sep">vs</span>' +
        '<span class="energy-conv-threshold">' + thresholdDisplay + '</span>' +
        '<span class="energy-badge ' + escapeHtml(r.badgeClass) + '">' + escapeHtml(r.status) + '</span>' +
      '</div>';
    }).join("");
    return '<div class="energy-convergence-panel" data-optimization-convergence>' +
      '<div class="energy-conv-title">' + escapeHtml(t("energy.conv.cycle_title", { n: model.cycle != null ? model.cycle : "--" })) + '</div>' +
      rowsHtml +
    '</div>';
  }
  var badgeClass = model.kind === "completed" ? "ok" : (model.kind === "failed" || model.kind === "cancelled") ? "err" : model.kind === "paused" ? "missing" : "";
  var statusRow = '<div class="energy-conv-row"><span class="energy-badge' + (badgeClass ? ' ' + badgeClass : '') + '">' + escapeHtml(model.statusLabel) + '</span></div>';
  var notesHtml = model.notes.map(function(note) {
    return '<div class="energy-conv-row"><span class="energy-task-note">' + escapeHtml(note) + '</span></div>';
  }).join("");
  return '<div class="energy-convergence-panel" data-optimization-convergence>' +
    '<div class="energy-conv-title">' + escapeHtml(t("energy.task.title")) + '</div>' +
    statusRow +
    notesHtml +
  '</div>';
}

function optimizationWorkspaceMarkup(data) {
  var job = lastSelectedJob || {};
  var series = Array.isArray(data.series) ? data.series : [];
  var energySeries = series.filter(function(item) { return ["relative_energy", "delta_energy", "scf_energy"].indexOf(String(item.id)) >= 0; });
  var selectedNode = energyGraphSelectedNode(data);
  var status = String(data.status || "").toLowerCase();
  var statusText = status === "running" || status === "partial" ? "● 实时采集中" : status === "completed" ? "● 已收敛" : status === "failed" ? "● 失败" : translateStatus(status);
  var currentCycle = (data.metadata || {}).current_cycle || (selectedNode && selectedNode.x) || "--";
  var energyOptions = energySeries.map(function(item) { return '<option value="' + escapeHtml(String(item.id)) + '"' + (String(item.id) === String(energyGraphState.energySeriesId) ? ' selected' : '') + '>' + escapeHtml(String(item.label || item.id)) + ' · ' + escapeHtml(String(item.unit || '')) + '</option>'; }).join("");
  var selectedItem = selectedNode && selectedNode.frame_index != null ? ' · 周期 ' + escapeHtml(String(selectedNode.x)) : '';
  var view = OPTIMIZATION_VIEW_IDS.indexOf(energyGraphState.optimizationView) >= 0 ? energyGraphState.optimizationView : "energy";
  energyGraphState.optimizationView = view;
  var cfg = optimizationViewConfig(view);
  var viewButtons = OPTIMIZATION_VIEW_IDS.map(function(id) {
    return '<button class="optimization-view-btn' + (id === view ? ' active' : '') + '" data-optimization-view="' + id + '" type="button" role="tab" aria-selected="' + (id === view) + '">' + escapeHtml(optimizationViewConfig(id).label) + '</button>';
  }).join("");
  var energyPickerHidden = view === "energy" ? "" : ' style="display:none;"';
  var nCycles = (data.metadata || {}).n_cycles || (Array.isArray(data.nodes) ? data.nodes.length : 0);
  return '<div class="optimization-workspace" data-energy-workspace data-energy-view="optimization">' +
    '<div class="optimization-workspace-head"><div><div class="optimization-workspace-kicker">任务观察 · Optimization Viewer</div><div class="optimization-workspace-title">' + escapeHtml(String(job.name || job.id || data.job_id || "几何结构优化")) + '</div></div><div class="optimization-workspace-meta"><span>几何优化</span><span>当前周期 ' + escapeHtml(String(currentCycle)) + '</span><span class="live">' + escapeHtml(statusText) + '</span></div></div>' +
    '<div class="optimization-workspace-toolbar"><div class="optimization-view-switch" role="tablist">' + viewButtons + '</div><label for="optimization-energy-series" data-optimization-energy-label' + energyPickerHidden + '>能量曲线</label><select id="optimization-energy-series" class="tool-select" data-optimization-energy' + energyPickerHidden + '>' + energyOptions + '</select>' + energyAnnotationToolbarMarkup() + '<span class="optimization-toolbar-spacer"></span><div class="optimization-action-group"><button class="plot-tool' + (energyGraphState.optimizationTool === "box" ? ' active' : '') + '" data-optimization-action="box" type="button" title="框选放大" aria-label="框选放大">⬚</button><button class="plot-tool' + (energyGraphState.optimizationTool !== "box" ? ' active' : '') + '" data-optimization-action="pan" type="button" title="拖动平移" aria-label="拖动平移">✥</button><button class="plot-tool" data-optimization-action="reset" type="button" title="重置视图" aria-label="重置视图">⟲</button><button class="optimization-follow-btn' + (energyGraphState.liveFollow ? ' active' : '') + '" data-optimization-action="follow" type="button">' + (energyGraphState.liveFollow ? '跟随最新周期' : '回到最新周期') + '</button><button class="plot-tool optimization-reload-btn" data-optimization-action="reload" type="button" title="刷新" aria-label="刷新"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10a7 7 0 1 1 2 5M3 10v4M3 10h4"/></svg><span class="reload-label">刷新</span></button></div></div>' +
    '<div class="optimization-grid"><div class="optimization-charts"><section class="optimization-chart-card"><div class="optimization-chart-card-head"><span class="optimization-chart-card-title" data-optimization-title>' + escapeHtml(cfg.title) + ' \u00b7 ' + ((data.nodes || []).length) + ' ' + t("energy.header.frames") + '</span><span class="optimization-chart-card-subtitle" data-optimization-subtitle style="color:' + energyHeaderStatusInfo(data).color + ';">\u25cf ' + escapeHtml(energyHeaderStatusInfo(data).text) + '</span></div><div class="optimization-chart-scroll" data-optimization-scroll="main">' + optimizationGraphBuildSvg(data, view) + '</div><div class="optimization-chart-legend" data-optimization-legend>' + optimizationLegendMarkup(data, view) + '</div></section>' + optimizationStatusPanelMarkup(data) + '</div><aside class="energy-inspector-panel"><div class="energy-inspector-head"><div class="energy-inspector-title">结构查看器</div><div class="energy-inspector-subtitle">点选周期查看对应几何</div></div><div id="energy-structure-viewer" class="energy-structure-viewer' + (selectedNode && selectedNode.geometry_ref ? '' : ' empty') + '">' + (selectedNode && selectedNode.geometry_ref ? '' : '当前周期没有可加载的 XYZ 结构') + '</div>' + energyGraphInspectorMarkup(data) + '</aside></div>' +
    '<div class="optimization-workspace-footer"><span>数据源：<strong>' + escapeHtml(String(data.source || '--')) + '</strong> · 周期 ' + escapeHtml(String(nCycles)) + selectedItem + '</span><span>' + optimizationQualityBadge(data) + ' · revision <strong>' + escapeHtml(String(data.revision || '--')) + '</strong></span></div></div>';
}

function optimizationGraphRenderCurrent() {
  var screen = getEnergyViewerContainer();
  var data = energyGraphState.data;
  if (!screen || !data || data.view_type !== "optimization") return;
  var root = screen.querySelector('[data-energy-view="optimization"]');
  if (!root) {
    screen.style.display = "block";
    screen.innerHTML = optimizationWorkspaceMarkup(data);
    root = screen.querySelector('[data-energy-view="optimization"]');
    if (root) optimizationGraphBind(root);
    return;
  }
  var view = OPTIMIZATION_VIEW_IDS.indexOf(energyGraphState.optimizationView) >= 0 ? energyGraphState.optimizationView : "energy";
  energyGraphState.optimizationView = view;
  var cfg = optimizationViewConfig(view);
  var title = root.querySelector("[data-optimization-title]");
  if (title) title.textContent = cfg.title;
  var subtitle = root.querySelector("[data-optimization-subtitle]");
  if (subtitle) subtitle.textContent = cfg.subtitle;
  root.querySelectorAll("[data-optimization-view]").forEach(function(button) {
    var active = button.getAttribute("data-optimization-view") === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  var energyPicker = view === "energy";
  var select = root.querySelector("[data-optimization-energy]");
  if (select) { select.style.display = energyPicker ? "" : "none"; select.value = energyGraphState.energySeriesId; }
  var selectLabel = root.querySelector("[data-optimization-energy-label]");
  if (selectLabel) selectLabel.style.display = energyPicker ? "" : "none";
  root.querySelectorAll('[data-optimization-action="box"], [data-optimization-action="pan"]').forEach(function(button) {
    var action = button.getAttribute("data-optimization-action");
    button.classList.toggle("active", action === "pan" ? energyGraphState.optimizationTool !== "box" : energyGraphState.optimizationTool === "box");
  });
  var scroll = root.querySelector('[data-optimization-scroll="main"]');
  if (scroll) {
    scroll.innerHTML = optimizationGraphBuildSvg(data, view);
    optimizationGraphBindChart(root);
  }
  var legend = root.querySelector("[data-optimization-legend]");
  if (legend) legend.innerHTML = optimizationLegendMarkup(data, view);
  var qualitySlot = root.querySelector("[data-optimization-quality]");
  if (qualitySlot) qualitySlot.outerHTML = optimizationQualityBadge(data);
  var convergenceSlot = root.querySelector("[data-optimization-convergence]");
  if (convergenceSlot) {
    convergenceSlot.outerHTML = optimizationStatusPanelMarkup(data);
  } else {
    var chartsDiv = root.querySelector(".optimization-charts");
    if (chartsDiv) {
      var chartCard = chartsDiv.querySelector(".optimization-chart-card");
      if (chartCard) chartCard.insertAdjacentHTML("afterend", optimizationStatusPanelMarkup(data));
    }
  }
  var body = root.querySelector(".energy-inspector-body");
  if (body) body.outerHTML = energyGraphInspectorMarkup(data);
  energyGraphBindInspectorButtons(root);
  var followButton = root.querySelector('[data-optimization-action="follow"]');
  if (followButton) { followButton.classList.toggle("active", !!energyGraphState.liveFollow); followButton.textContent = energyGraphState.liveFollow ? "跟随最新周期" : "回到最新周期"; }
  var current = energyGraphSelectedNode(data);
  if (current && current.frame_index != null) energyGraphLoadStructure(current.frame_index);
}

function optimizationGraphBindChart(root) {
  var svg = root.querySelector("[data-optimization-chart]");
  if (!svg) return;
  function plotContext() {
    var ctx = energyGraphState.optimizationPlot;
    return ctx && ctx.geom ? ctx : null;
  }
  function clientToData(event) {
    var ctx = plotContext();
    if (!ctx) return null;
    var pt = svg.createSVGPoint();
    pt.x = event.clientX;
    pt.y = event.clientY;
    var ctm = svg.getScreenCTM();
    if (!ctm) return null;
    pt = pt.matrixTransform(ctm.inverse());
    var pad = ctx.geom.pad;
    var Pw = ctx.geom.W - pad.left - pad.right;
    var Ph = ctx.geom.H - pad.top - pad.bottom;
    var fx = (pt.x - pad.left) / Pw;
    var fy = (pt.y - pad.top) / Ph;
    return {
      svgX: pt.x,
      svgY: pt.y,
      fx: fx,
      fy: fy,
      x: ctx.xDom[0] + fx * (ctx.xDom[1] - ctx.xDom[0]),
      y: ctx.yDom[1] - fy * (ctx.yDom[1] - ctx.yDom[0]),
      y2: ctx.y2Dom ? ctx.y2Dom[1] - fy * (ctx.y2Dom[1] - ctx.y2Dom[0]) : null,
    };
  }
  svg.addEventListener("wheel", function(event) {
    event.preventDefault();
    var ctx = plotContext();
    var anchor = clientToData(event);
    if (!ctx || !anchor) return;
    var factor = event.deltaY < 0 ? 0.82 : 1.22;
    function zoomAround(dom, anchorValue, base) {
      var lo = anchorValue - (anchorValue - dom[0]) * factor;
      var hi = anchorValue + (dom[1] - anchorValue) * factor;
      var minSpan = (base[1] - base[0]) / 500;
      if (hi - lo < minSpan) { var mid = (lo + hi) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
      return [lo, hi];
    }
    var nx = zoomAround(ctx.xDom, anchor.x, ctx.base.x);
    var ny = zoomAround(ctx.yDom, anchor.y, ctx.base.y);
    var ny2 = ctx.y2Dom && anchor.y2 !== null && ctx.base.y2 ? zoomAround(ctx.y2Dom, anchor.y2, ctx.base.y2) : ctx.y2Dom;
    optimizationStoreDomains(ctx.view, nx, ny, ny2 ? ny2.slice() : null);
    optimizationGraphScheduleRender();
  }, { passive: false });
  svg.addEventListener("dblclick", function(event) {
    event.preventDefault();
    var ctx = plotContext();
    if (!ctx) return;
    delete energyGraphState.optimizationDomains[ctx.view];
    optimizationGraphRenderCurrent();
  });
  var dragState = null;
  svg.addEventListener("pointerdown", function(event) {
    if (event.button !== 0) return;
    if (event.target.closest && event.target.closest("[data-energy-frame]")) return;
    var ctx = plotContext();
    var start = clientToData(event);
    if (!ctx || !start) return;
    event.preventDefault();
    svg.setPointerCapture(event.pointerId);
    if (energyGraphState.optimizationTool === "box") {
      var overlay = svg.querySelector("[data-optimization-overlay]");
      var rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("class", "energy-box-select-rect");
      rect.setAttribute("x", start.svgX);
      rect.setAttribute("y", start.svgY);
      rect.setAttribute("width", "0");
      rect.setAttribute("height", "0");
      if (overlay) overlay.appendChild(rect);
      dragState = { id: event.pointerId, mode: "box", startFx: start.fx, startFy: start.fy, rect: rect };
    } else {
      dragState = { id: event.pointerId, mode: "pan", startSvgX: start.svgX, startSvgY: start.svgY, xDom: ctx.xDom.slice(), yDom: ctx.yDom.slice(), y2Dom: ctx.y2Dom ? ctx.y2Dom.slice() : null };
      svg.classList.add("panning");
    }
  });
  svg.addEventListener("pointermove", function(event) {
    if (!dragState || event.pointerId !== dragState.id) return;
    var ctx = plotContext();
    if (!ctx) return;
    var pt = svg.createSVGPoint();
    pt.x = event.clientX;
    pt.y = event.clientY;
    var ctm = svg.getScreenCTM();
    if (!ctm) return;
    pt = pt.matrixTransform(ctm.inverse());
    if (dragState.mode === "pan") {
      var pad = ctx.geom.pad;
      var Pw = ctx.geom.W - pad.left - pad.right;
      var Ph = ctx.geom.H - pad.top - pad.bottom;
      var dxRatio = (pt.x - dragState.startSvgX) / Pw;
      var dyRatio = (pt.y - dragState.startSvgY) / Ph;
      function shifted(dom, dr, invert) {
        if (!dom) return null;
        var span = dom[1] - dom[0];
        return invert ? [dom[0] + dr * span, dom[1] + dr * span] : [dom[0] - dr * span, dom[1] - dr * span];
      }
      optimizationStoreDomains(ctx.view, shifted(dragState.xDom, dxRatio, false), shifted(dragState.yDom, dyRatio, true), shifted(dragState.y2Dom, dyRatio, true));
      optimizationGraphScheduleRender();
      return;
    }
    if (dragState.rect) {
      var startXpx = ctx.geom.pad.left + dragState.startFx * (ctx.geom.W - ctx.geom.pad.left - ctx.geom.pad.right);
      var startYpx = ctx.geom.pad.top + dragState.startFy * (ctx.geom.H - ctx.geom.pad.top - ctx.geom.pad.bottom);
      dragState.rect.setAttribute("x", Math.min(startXpx, pt.x));
      dragState.rect.setAttribute("y", Math.min(startYpx, pt.y));
      dragState.rect.setAttribute("width", Math.abs(pt.x - startXpx));
      dragState.rect.setAttribute("height", Math.abs(pt.y - startYpx));
    }
  });
  svg.addEventListener("pointerup", function(event) {
    if (!dragState || event.pointerId !== dragState.id) return;
    var ctx = plotContext();
    svg.releasePointerCapture(event.pointerId);
    svg.classList.remove("panning");
    if (dragState.mode === "box" && dragState.rect && ctx) {
      var rx = parseFloat(dragState.rect.getAttribute("x"));
      var ry = parseFloat(dragState.rect.getAttribute("y"));
      var rw = parseFloat(dragState.rect.getAttribute("width"));
      var rh = parseFloat(dragState.rect.getAttribute("height"));
      dragState.rect.remove();
      if (rw >= 6 && rh >= 6) {
        var pad = ctx.geom.pad;
        var Pw = ctx.geom.W - pad.left - pad.right;
        var Ph = ctx.geom.H - pad.top - pad.bottom;
        var fx0 = (rx - pad.left) / Pw, fx1 = (rx + rw - pad.left) / Pw;
        var fy0 = (ry - pad.top) / Ph, fy1 = (ry + rh - pad.top) / Ph;
        function zoomedBox(dom, lo, hi, invert) {
          if (!dom) return null;
          var span = dom[1] - dom[0];
          return invert ? [dom[1] - hi * span, dom[1] - lo * span] : [dom[0] + lo * span, dom[0] + hi * span];
        }
        optimizationStoreDomains(ctx.view, zoomedBox(ctx.xDom, fx0, fx1, false), zoomedBox(ctx.yDom, fy0, fy1, true), zoomedBox(ctx.y2Dom, fy0, fy1, true));
        energyGraphState.optimizationTool = "pan";
      }
    }
    dragState = null;
    optimizationGraphRenderCurrent();
  });
  svg.addEventListener("pointercancel", function(event) {
    if (!dragState || event.pointerId !== dragState.id) return;
    if (dragState.rect) dragState.rect.remove();
    svg.classList.remove("panning");
    svg.releasePointerCapture(event.pointerId);
    dragState = null;
  });
}

function optimizationGraphBind(root) {
  if (!root) return;
  var select = root.querySelector("[data-optimization-energy]");
  if (select) select.addEventListener("change", function() { energyGraphState.energySeriesId = select.value; optimizationGraphRenderCurrent(); });
  root.querySelectorAll("[data-optimization-view]").forEach(function(button) {
    button.addEventListener("click", function() {
      var view = String(button.getAttribute("data-optimization-view") || "energy");
      if (view !== energyGraphState.optimizationView) {
        energyGraphState.optimizationView = view;
        optimizationGraphRenderCurrent();
      }
    });
  });
  root.querySelectorAll("[data-optimization-action]").forEach(function(button) {
    button.addEventListener("click", function() {
      var action = button.getAttribute("data-optimization-action");
      if (action === "reload") { renderEnergyGraphWorkspace(lastSelectedJob); return; }
      if (action === "reset") {
        delete energyGraphState.optimizationDomains[energyGraphState.optimizationView];
        optimizationGraphRenderCurrent();
        return;
      }
      if (action === "box" || action === "pan") {
        energyGraphState.optimizationTool = action === "box" && energyGraphState.optimizationTool !== "box" ? "box" : "pan";
        optimizationGraphRenderCurrent();
        return;
      }
      if (action === "follow") {
        energyGraphState.liveFollow = true;
        var nodes = energyGraphState.data && energyGraphState.data.nodes || [];
        if (nodes.length) energyGraphSelectFrame(nodes[nodes.length - 1].frame_index, "follow", nodes[nodes.length - 1].id);
      }
    });
  });
  root.addEventListener("click", function(event) {
    var target = event.target.closest ? event.target.closest("[data-energy-node], [data-energy-frame]") : null;
    if (!target) return;
    event.stopPropagation();
    energyGraphState.liveFollow = false;
    var clickedNodeId = target.getAttribute("data-energy-node") || "";
    energyGraphSelectFrame(Number(target.getAttribute("data-energy-frame")), undefined, clickedNodeId || undefined);
  });
  energyGraphBindInspectorButtons(root);
  /* ResizeObserver: the chart geometry follows the live container size, so
     sidebar drags and window resizes re-render the active view instead of
     clipping a fixed-size SVG. */
  if (energyGraphState.resizeObserver) { energyGraphState.resizeObserver.disconnect(); energyGraphState.resizeObserver = null; }
  var chartScroll = root.querySelector('[data-optimization-scroll="main"]');
  if (chartScroll && typeof ResizeObserver !== "undefined") {
    var resizeTimer = null;
    var ro = new ResizeObserver(function() {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function() {
        resizeTimer = null;
        if (!chartScroll.isConnected || !energyGraphState.data) return;
        energyGraphState.optimizationViewSize = { width: chartScroll.clientWidth, height: chartScroll.clientHeight };
        optimizationGraphRenderCurrent();
      }, 150);
    });
    ro.observe(chartScroll);
    energyGraphState.resizeObserver = ro;
    if (chartScroll.clientWidth > 0 && chartScroll.clientHeight > 0) {
      energyGraphState.optimizationViewSize = { width: chartScroll.clientWidth, height: chartScroll.clientHeight };
      optimizationGraphRenderCurrent();
    }
  }
  optimizationGraphBindChart(root);
  var current = energyGraphSelectedNode(energyGraphState.data);
  if (current && current.frame_index != null) energyGraphLoadStructure(current.frame_index);
}

function energyGraphWorkspaceMarkup(data) {
  const series = Array.isArray(data.series) ? data.series : [];
  const job = lastSelectedJob || {};
  const projectId = String(s2scanState.projectId || "");
  const reviewState = candidateReviewState();
  const candidateCount = reviewState.candidates.length;
  const reviewMeta = (data.metadata || {}).review || {};
  const hasReview = !!reviewMeta.status;
  const options = series.map(function(item) {
    return '<option value="' + escapeHtml(String(item.id)) + '"' + (String(item.id) === String(energyGraphState.seriesId) ? " selected" : "") + '>' + escapeHtml(String(item.label || item.id)) + ' · ' + escapeHtml(String(item.unit || "")) + '</option>';
  }).join("");
  const status = String(data.status || "unknown").toLowerCase();
  const statusText = status === "running" || status === "partial" ? "● 计算中" : status === "interrupted" ? "● 扫描中断、数据不完整" : status === "completed" || status === "ready_for_review" ? "● 已完成" : translateStatus(status);
  const dirtyBadge = '<span class="energy-dirty-badge" data-energy-dirty-badge' + (reviewState.dirty ? "" : ' style="display:none;"') + '>未保存修改</span>';
  const savedText = reviewState.savedAt ? "已保存 " + String(reviewState.savedAt).replace("T", " ").slice(0, 19) : (reviewMeta.saved || reviewState.reviewStatus === "confirmed" ? "已保存" : "尚未保存");
  const selectionButtonClass = reviewState.selectionLocked ? "locked" : "editable";
  const vm = energyGraphState.viewMode || "fit";
  const labelsActive = energyGraphState.annotationMode !== "hide";
  const standaloneBadge = hasReview && !projectId ? '<span class="energy-standalone-badge">独立 PESsearch 任务</span>' : "";
  const ircCounts = data.view_type === "irc" ? ((data.metadata || {}).directions || {}) : null;
  const ircProgressText = ircCounts ? '正向 ' + Number(ircCounts.forward || 0) + ' 点 · 反向 ' + Number(ircCounts.reverse || 0) + ' 点' : '';
  const ircUpdatedAt = data.view_type === "irc" ? String((data.metadata || {}).updated_at || "") : "";
  const xModes = (data.metadata && data.metadata.x_axis_modes) || (data.x_axis && data.x_axis.modes) || [];
  const currentXMode = energyGraphState.xAxisMode || (data.metadata && data.metadata.default_x_axis_mode) || (data.x_axis && data.x_axis.default_mode) || "scanned_coordinate";
  var xAxisToolbarHtml = "";
  if (data.view_type === "scan" || xModes.length > 0) {
    var availableModes = xModes.length > 0 ? xModes : [
      { id: "path_length", label: "累积路径长度", unit: "Å" },
      { id: "reaction_progress", label: "反应进度", unit: "" },
      { id: "scanned_coordinate", label: String((data.x_axis || {}).label || "扫描坐标"), unit: String((data.x_axis || {}).unit || "") },
      { id: "frame_index", label: "帧序号", unit: "" }
    ];
    var xOptions = availableModes.map(function(m) {
      var u = m.unit ? ' (' + m.unit + ')' : '';
      return '<option value="' + escapeHtml(m.id) + '"' + (m.id === currentXMode ? ' selected' : '') + '>' + escapeHtml(m.label + u) + '</option>';
    }).join("");
    xAxisToolbarHtml = '<span class="energy-tool-label" style="margin-left:8px;">横轴</span>' +
      '<select class="tool-select" data-energy-x-axis aria-label="横轴类型">' + xOptions + '</select>';
  }
  return '<div class="energy-workspace" data-energy-workspace data-energy-reviewable="' + (hasReview ? "1" : "0") + '">' +
    '<div class="energy-workspace-head">' +
      '<div style="min-width:0"><div class="energy-workspace-kicker">\u4efb\u52a1\u89c2\u5bdf \u00b7 Calculation Viewer</div><div class="energy-workspace-title">' + escapeHtml(String(job.name || job.id || data.job_id || "\u5f53\u524d\u4efb\u52a1")) + '</div></div>' +
      '<div class="energy-workspace-meta"><span>' + escapeHtml(String(data.title || "能量与轨迹")) + '</span><span>' + escapeHtml(ircProgressText || String((data.metadata || {}).frame_count || (data.nodes || []).length) + ' frames') + '</span><span class="energy-live">' + escapeHtml(statusText) + '</span>' + standaloneBadge + '</div>' +
    '</div>' +
    '<div class="energy-workspace-toolbar">' +
      '<span class="energy-tool-label">\u5f53\u524d\u66f2\u7ebf</span>' +
      '<select class="tool-select" data-energy-series aria-label="\u66f2\u7ebf\u7c7b\u578b">' + options + '</select>' +
      xAxisToolbarHtml +
      '<div class="tool-group">' +
        '<button class="plot-tool" data-energy-action="global" type="button" title="\u5168\u5c40\u89c6\u56fe" aria-label="\u5168\u5c40\u89c6\u56fe" data-tip="\u5168\u5c40\u89c6\u56fe"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8V3h5M17 8V3h-5M3 12v5h5M17 12v5h-5"/></svg></button>' +
        '<button class="plot-tool" data-energy-action="fit" type="button" title="\u9002\u914d\u6570\u636e" aria-label="\u9002\u914d\u6570\u636e" data-tip="\u9002\u914d\u6570\u636e" data-tip-keys="F"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h-4v4M14 2h4v4M6 18H2v-4M14 18h4v-4"/></svg></button>' +
      '</div>' +
      '<div class="tool-divider"></div>' +
      '<div class="tool-group">' +
        '<button class="plot-tool' + (vm === "box" ? " active" : "") + '" data-energy-action="box" data-plot-tool="box" type="button" title="\u6846\u9009\u653e\u5927" aria-label="\u6846\u9009\u653e\u5927" data-tip="\u6846\u9009\u653e\u5927" data-tip-keys="B"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="3 2"><rect x="3" y="3" width="14" height="14" rx="1"/></svg></button>' +
        '<button class="plot-tool' + (vm === "pan" ? " active" : "") + '" data-energy-action="pan" data-plot-tool="pan" type="button" title="\u5e73\u79fb" aria-label="\u5e73\u79fb" data-tip="\u5e73\u79fb\uff08\u6309\u4f4f Space \u62d6\u52a8\uff09" data-tip-keys="V"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2v16M2 10h16M10 2l-2 2M10 2l2 2M10 18l-2-2M10 18l2-2M2 10l2-2M2 10l2 2M18 10l-2-2M18 10l-2 2"/></svg></button>' +
      '</div>' +
      '<div class="tool-divider"></div>' +
      '<div class="tool-group">' +
        '<button class="plot-tool" data-energy-action="reset" type="button" title="\u91cd\u7f6e\u89c6\u56fe" aria-label="\u91cd\u7f6e\u89c6\u56fe" data-tip="\u91cd\u7f6e\u89c6\u56fe" data-tip-keys="R"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10a7 7 0 1 1 7 7M3 10V5M3 10h5"/></svg></button>' +
        '<button class="plot-tool' + (labelsActive ? " active" : "") + '" data-energy-action="labels" type="button" title="\u663e\u793a\u6807\u6ce8" aria-label="\u663e\u793a\u6807\u6ce8" data-tip="\u663e\u793a\u6807\u6ce8" data-tip-keys="L"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4h6l2 2h8M2 4v10h16V6l-2-2H10L8 4"/><circle cx="7" cy="11" r="1"/></svg></button>' +
        '<button class="plot-tool" data-energy-action="reload" type="button" title="\u91cd\u65b0\u52a0\u8f7d" aria-label="\u91cd\u65b0\u52a0\u8f7d" data-tip="\u91cd\u65b0\u52a0\u8f7d" data-tip-keys="Ctrl+R"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10a7 7 0 1 1 2 5M3 10v4M3 10h4"/></svg></button>' +
      '</div>' +
      '<div class="tool-divider"></div>' +
      '<div class="tool-group">' +
        '<button class="plot-tool" data-energy-action="focus-curve" type="button" title="\u4e13\u6ce8\u66f2\u7ebf" aria-label="\u4e13\u6ce8\u66f2\u7ebf" data-tip="\u4e13\u6ce8\u66f2\u7ebf"><svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3h14v14H3z"/><path d="M7 3v14M3 7h14" opacity="0.3"/></svg></button>' +
      '</div>' +
      '<div class="tool-divider"></div>' +
      energyAnnotationToolbarMarkup() +
      '<span class="energy-toolbar-spacer"></span>' +
      (hasReview
        ? '<span class="energy-selection-count" data-energy-selection-count>\u5df2\u9009\u5019\u9009\uff1a' + candidateCount + '</span>' +
          dirtyBadge +
          '<span class="energy-selection-count" data-energy-saved-state>' + escapeHtml(savedText) + '</span>' +
          (!reviewMeta.saved ? '<span class="energy-selection-count" data-energy-unconfirmed-hint style="opacity:0.7;">未确认 · 确认选点后才会进入 BatchOptimize</span>' : '') +
          '<button class="btn primary energy-selection-lock-btn ' + selectionButtonClass + '" data-energy-action="toggle-selection-lock" title="' + escapeHtml(energyGraphSelectionActionTitle()) + '" aria-pressed="' + (reviewState.selectionLocked ? "true" : "false") + '">' + escapeHtml(energyGraphSelectionActionLabel()) + '</button>'
        : '') +
    '</div>' +
    '<div class="energy-workspace-grid">' +
      '<section class="energy-chart-panel" data-energy-chart-panel>' +
        energyHeaderStripMarkup(data) +
        energyConsistencyWarningMarkup(data) +
        '<div class="energy-chart-scroll" data-energy-chart-scroll>' + energyGraphBuildSvg(data) + '</div>' +
        energyGraphLegendMarkup(data) +
      '</section>' +
      '<aside class="energy-inspector-panel">' +
        '<div class="energy-inspector-head"><div class="energy-inspector-title">结构查看器</div><div class="energy-inspector-subtitle">与能量节点联动</div></div>' +
        '<div id="energy-structure-viewer" class="energy-structure-viewer' + (energyGraphSelectedNode(data) && energyGraphSelectedNode(data).geometry_ref ? "" : " empty") + '">' + (energyGraphSelectedNode(data) && energyGraphSelectedNode(data).geometry_ref ? "" : "当前节点没有可加载的结构") + '</div>' +
        energyGraphInspectorMarkup(data) +
      '</aside>' +
    '</div>' +
    '<div class="energy-workspace-footer"><span>数据源：<strong>' + escapeHtml(String(data.source || "--")) + '</strong>' + (ircUpdatedAt ? ' · 更新 ' + escapeHtml(ircUpdatedAt.replace('T', ' ').slice(0, 19)) : '') + '</span><span>revision <strong>' + escapeHtml(String(data.revision || "--")) + '</strong> · ' + ((data.metadata || {}).scan_interrupted ? "扫描中断、数据不完整" : data.complete ? "数据完整" : "数据部分完成") + '</span></div>' +
    '<div class="energy-context-menu" data-energy-menu>' +
      '<div class="energy-context-title">曲线类型</div>' +
      series.map(function(item) { return '<button class="energy-menu-item' + (item.id === energyGraphState.seriesId ? " active" : "") + '" data-energy-menu-series="' + escapeHtml(String(item.id)) + '"><span>' + escapeHtml(String(item.label || item.id)) + '</span>' + (item.id === energyGraphState.seriesId ? '<span class="energy-check">✓</span>' : '<span></span>') + '</button>'; }).join("") +
      '<div class="energy-menu-divider"></div><div class="energy-context-title">标注</div>' +
      '<button class="energy-menu-item' + (energyGraphState.annotationMode === "all" ? " active" : "") + '" data-energy-menu-annotation="all"><span>显示全部标注</span><span></span></button>' +
      '<button class="energy-menu-item' + (energyGraphState.annotationMode === "candidate" ? " active" : "") + '" data-energy-menu-annotation="candidate"><span>仅显示候选</span><span></span></button>' +
      '<button class="energy-menu-item' + (energyGraphState.annotationMode === "hide" ? " active" : "") + '" data-energy-menu-annotation="hide"><span>隐藏全部标注</span><span></span></button>' +
    '</div>' +
  '</div>';
}

function energyGraphDestroyViewer() {
  // Vibration teardown (todo 30): this path also fires from clearViewer().
  if (window.ACPVibrationViewer && typeof window.ACPVibrationViewer.handleTeardown === "function") {
    window.ACPVibrationViewer.handleTeardown();
  }
  if (energyGraphState.resizeObserver) { energyGraphState.resizeObserver.disconnect(); energyGraphState.resizeObserver = null; }
  disposeViewerFraming(energyGraphState.structureFraming);
  if (energyGraphState.structureAbort) { energyGraphState.structureAbort.abort(); energyGraphState.structureAbort = null; }
  if (energyGraphState.viewer) {
    // dispose() is idempotent: clearScene + destroyImpl run exactly once,
    // tokens/callbacks die immediately; a re-entry after nulling is a no-op.
    energyGraphState.viewer.dispose();
    energyGraphState.viewer = null;
  }
  energyGraphState.loadedFrameIndex = null;
  energyGraphState.loadedFrameId = "";
}

function energyGraphCurrentNode(data) {
  return energyGraphSelectedNode(data);
}

async function energyGraphLoadStructure(frameIndex) {
  var data = energyGraphState.data;
  if (!data || frameIndex == null) return;
  var nodes = Array.isArray(data.nodes) ? data.nodes : [];
  var node = nodes.find(function(n) { return String(n.id) === String(energyGraphState.selectedNodeId); })
    || nodes.find(function(n) { return Number(n.frame_index) === Number(frameIndex); })
    || nodes[0];
  if (node) return energyGraphLoadFrameGeometry(node);
}

function s2CandidateLabel(entry, index) {
  const role = String(entry.role || "ts").toLowerCase() === "ts" ? "TS" : "INT";
  const candidateId = String(entry.candidate_id || "");
  let suffix = candidateId ? candidateId.split("_").pop() : "";
  if (!suffix && entry.frame_index != null) suffix = String(entry.frame_index).padStart(3, "0");
  if (!suffix) suffix = String(index + 1).padStart(2, "0");
  return role + "-" + suffix;
}

function energyGraphSyncS2Selection(data) {
  if (!data || data.view_type !== "scan") return;
  // Once the user explicitly enters edit mode, polling must not silently
  // restore the server snapshot over the local working selection.
  var review = candidateReviewState();
  if (review.selectionEditMode || review.dirty) return;
  candidateReviewCtl().syncFromServerSnapshot((data.metadata || {}).review, data.annotations);
  s2scanState.activeCount = candidateReviewState().candidates.length;
}

function energyGraphApplyWorkingCandidates() {
  const data = energyGraphState.data;
  if (!data || data.view_type !== "scan") return;
  const serverCandidates = {};
  (data.annotations || []).forEach(function(annotation) {
    if (["ts", "intermediate"].indexOf(String(annotation.type)) >= 0 && annotation.candidate_id) {
      serverCandidates[String(annotation.candidate_id)] = annotation;
    }
  });
  const others = (data.annotations || []).filter(function(annotation) {
    return ["ts", "intermediate"].indexOf(String(annotation.type)) < 0;
  });
  const working = candidateReviewState().candidates.map(function(entry, index) {
    const base = (entry.candidate_id && serverCandidates[entry.candidate_id]) || {};
    return {
      id: entry.candidate_id || ("working_" + entry.frame_index + "_" + entry.role),
      candidate_id: entry.candidate_id || null,
      type: entry.role,
      label: s2CandidateLabel(entry, index),
      frame_index: entry.frame_index,
      x: null,
      y: null,
      status: "working",
      geometry_ref: base.geometry_ref || "",
      selected: true,
      active: true,
      saved: !!entry.saved,
      recommended_type: entry.recommended_type || null,
      selection_source: entry.selection_source || "manual",
      confidence: base.confidence || null,
      reason: base.reason || null,
    };
  });
  data.annotations = others.concat(working);
  if (energyGraphState.layout) energyGraphState.layout.allAnnotations = null;
}

function energyGraphUpdateSelectionOnData() {
  energyGraphApplyWorkingCandidates();
}

function energyGraphClampDomains() {
  var layout = energyGraphState.layout;
  if (!layout) return;
  var bx = layout.baseX, by = layout.baseY;
  var baseXSpan = bx[1] - bx[0];
  var baseYSpan = by[1] - by[0];
  var minXSpan = baseXSpan / 500;
  var minYSpan = baseYSpan / 500;
  var xPad = baseXSpan * 4;
  var yPad = baseYSpan * 4;
  if (Array.isArray(energyGraphState.xDomain)) {
    var xd = energyGraphState.xDomain;
    var xSpan = xd[1] - xd[0];
    if (xSpan < minXSpan) { var cx = (xd[0] + xd[1]) / 2; xd = [cx - minXSpan / 2, cx + minXSpan / 2]; }
    xd = [Math.max(xd[0], bx[0] - xPad), Math.min(xd[1], bx[1] + xPad)];
    energyGraphState.xDomain = xd;
  }
  if (Array.isArray(energyGraphState.yDomain)) {
    var yd = energyGraphState.yDomain;
    var ySpan = yd[1] - yd[0];
    if (ySpan < minYSpan) { var cy = (yd[0] + yd[1]) / 2; yd = [cy - minYSpan / 2, cy + minYSpan / 2]; }
    yd = [Math.max(yd[0], by[0] - yPad), Math.min(yd[1], by[1] + yPad)];
    energyGraphState.yDomain = yd;
  }
}

function energyGraphSetDomains(mode) {
  var layout = energyGraphState.layout;
  if (!layout) return;
  if (mode === "global") {
    energyGraphState.xDomain = null;
    energyGraphState.yDomain = null;
    energyGraphUpdateChart();
    return;
  }
  if (mode === "fit") {
    var data = energyGraphState.data;
    if (!data) return;
    var series = energyGraphSeries(data);
    var nodes = Array.isArray(data.nodes) ? data.nodes : [];
    var values = Array.isArray(series && series.values) ? series.values : [];
    var currentXMode = energyGraphState.xAxisMode || (data.metadata && data.metadata.default_x_axis_mode) || (data.x_axis && data.x_axis.default_mode) || "scanned_coordinate";
    var xValues = (currentXMode !== "scanned_coordinate" || !(Array.isArray(series && series.x_values) && series.x_values.length))
      ? nodes.map(function(node) { return energyGraphNodeX(node, currentXMode); })
      : series.x_values;
    var points = [];
    for (var pi = 0; pi < Math.min(values.length, xValues.length); pi += 1) {
      var pxv = energyGraphValue(xValues[pi]);
      var pyv = energyGraphValue(values[pi]);
      if (pxv !== null && pyv !== null) points.push({ x: pxv, y: pyv, node: nodes[pi] });
    }
    var annotations = Array.isArray(data.annotations) ? data.annotations : [];
    var annNodes = [];
    annotations.forEach(function(ann) {
      if (energyGraphState.annotationMode === "hide") return;
      if (energyGraphState.annotationMode === "candidate" && ["ts", "intermediate"].indexOf(ann.type) < 0) return;
      if (energyGraphState.annotationMode !== "all" && energyGraphState.annotationMode !== "candidate" && energyGraphState.annotationMode !== "hide" && energyGraphState.annotationMode !== ann.type) return;
      var fi = ann.frame_index;
      var annNode = nodes.find(function(item) { return Number(item.frame_index) === Number(fi); });
      if (annNode && energyGraphNodeX(annNode, currentXMode) !== null) {
        var annVal = energyGraphSeriesValueAtFrame(data, series, fi);
        if (annVal !== null) annNodes.push({ x: energyGraphNodeX(annNode, currentXMode), y: annVal, node: annNode });
      }
    });
    var allPts = points.concat(annNodes);
    if (!allPts.length) return;
    var xExt = [Math.min.apply(null, allPts.map(function(p) { return p.x; })), Math.max.apply(null, allPts.map(function(p) { return p.x; }))];
    var validPts = allPts.filter(function(p) { return !p.node || p.node.status !== "failed"; });
    var yTargetPts = validPts.length ? validPts : allPts;
    var yExt = [Math.min.apply(null, yTargetPts.map(function(p) { return p.y; })), Math.max.apply(null, yTargetPts.map(function(p) { return p.y; }))];
    var xSpread = Math.max(1e-9, xExt[1] - xExt[0]);
    var ySpread = Math.max(1e-9, yExt[1] - yExt[0]);
    energyGraphState.xDomain = [xExt[0] - xSpread * 0.06, xExt[1] + xSpread * 0.06];
    energyGraphState.yDomain = [yExt[0] - ySpread * 0.12, yExt[1] + ySpread * 0.12];
    energyGraphUpdateChart();
    return;
  }
  if (mode === "reset") {
    energyGraphState.xDomain = null;
    energyGraphState.yDomain = null;
    energyGraphState.annotationMode = "all";
    activatePlotTool("");
    var container = getEnergyViewerContainer();
    if (container) {
      var annMenuItems = container.querySelectorAll('[data-energy-menu-annotation]');
      annMenuItems.forEach(function(item) { item.classList.toggle("active", item.getAttribute("data-energy-menu-annotation") === "all"); });
      var labelsBtn = container.querySelector('[data-energy-action="labels"]');
      if (labelsBtn) labelsBtn.classList.add("active");
      var chartSvg = container.querySelector("[data-energy-chart]");
      if (chartSvg) chartSvg.classList.remove("labels-hidden");
    }
    energyGraphUpdateChart();
    return;
  }
}

function energyGraphClientToPlotPoint(event, svg) {
  var layout = energyGraphState.layout;
  if (!layout || !svg) return null;
  var pt = svg.createSVGPoint();
  pt.x = event.clientX;
  pt.y = event.clientY;
  var ctm = svg.getScreenCTM();
  if (!ctm) return null;
  pt = pt.matrixTransform(ctm.inverse());
  var geom = energyGraphState._cachedGeom || energyGraphChartGeometry();
  var plotRect = geom.plotRect;
  if (pt.x < plotRect.left || pt.x > plotRect.right || pt.y < plotRect.top || pt.y > plotRect.bottom) return null;
  var xDom = energyGraphState.currentDomain ? energyGraphState.currentDomain.x : layout.baseX;
  var yDom = energyGraphState.currentDomain ? energyGraphState.currentDomain.y : layout.baseY;
  var Pw = plotRect.right - plotRect.left, Ph = plotRect.bottom - plotRect.top;
  return {
    x: xDom[0] + ((pt.x - plotRect.left) / (Pw || 1)) * (xDom[1] - xDom[0]),
    y: yDom[1] - ((pt.y - plotRect.top) / (Ph || 1)) * (yDom[1] - yDom[0]),
    svgX: pt.x,
    svgY: pt.y,
    plotRect: plotRect,
  };
}

let energyGraphDragState = null;
let energyGraphDragBound = false;

function energyGraphNearestFrame(clientX, svg) {
  var data = energyGraphState.data;
  if (!data || !svg) return null;
  var layout = energyGraphState.layout;
  if (!layout) return null;
  var pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = 0;
  var ctm = svg.getScreenCTM();
  if (!ctm) return null;
  pt = pt.matrixTransform(ctm.inverse());
  var xDom = energyGraphState.currentDomain ? energyGraphState.currentDomain.x : layout.baseX;
  var geom = energyGraphState._cachedGeom || energyGraphChartGeometry();
  var Pw = geom.W - geom.pad.left - geom.pad.right;
  var dataX = xDom[0] + ((pt.x - geom.pad.left) / (Pw || 1)) * (xDom[1] - xDom[0]);
  var best = null;
  var bestDist = Infinity;
  var currentXMode = energyGraphState.xAxisMode || (data.metadata && data.metadata.default_x_axis_mode) || (data.x_axis && data.x_axis.default_mode) || "scanned_coordinate";
  (data.nodes || []).forEach(function(node) {
    var x = energyGraphNodeX(node, currentXMode);
    if (x === null) return;
    var dist = Math.abs(x - dataX);
    if (dist < bestDist) {
      bestDist = dist;
      best = node;
    }
  });
  return best;
}

function energyGraphBindMarkerDrag(root) {
  root.querySelectorAll("[data-energy-draggable]").forEach(function(element) {
    element.addEventListener("pointerdown", function(event) { event.stopPropagation(); });
    element.addEventListener("mousedown", function(event) {
      event.preventDefault();
      event.stopPropagation();
      energyGraphDragState = {
        frameIndex: Number(element.getAttribute("data-energy-frame")),
        startX: event.clientX,
        element: element,
        moved: false,
      };
      element.classList.add("dragging");
    });
  });
  if (energyGraphDragBound) return;
  energyGraphDragBound = true;
  window.addEventListener("mousemove", function(event) {
    if (!energyGraphDragState) return;
    const state = energyGraphDragState;
    if (!state.element || !state.element.isConnected) {
      energyGraphDragState = null;
      return;
    }
    const dx = event.clientX - state.startX;
    if (Math.abs(dx) < 4 && !state.moved) return;
    state.moved = true;
    state.element.style.transform = "translate(" + dx + "px, 0)";
  });
  window.addEventListener("mouseup", function(event) {
    if (!energyGraphDragState) return;
    const state = energyGraphDragState;
    energyGraphDragState = null;
    state.element.classList.remove("dragging");
    if (!state.moved) return;
    state.element.style.transform = "";
    const svg = state.element.ownerSVGElement;
    const target = energyGraphNearestFrame(event.clientX, svg);
    if (!target || target.frame_index == null) return;
    if (energyGraphMoveCandidate(state.frameIndex, Number(target.frame_index))) {
      energyGraphRenderCurrent();
    } else {
      energyGraphRenderCurrent();
    }
  });
}

function energyGraphBindChartInteractions(root) {
  var svg = root.querySelector("[data-energy-chart]");
  if (!svg) return;
  /* ── Wheel zoom (anchored, axis-modifier aware) ── */
  svg.addEventListener("wheel", function(event) {
    event.preventDefault();
    var anchor = energyGraphClientToPlotPoint(event, svg);
    var domain = energyGraphState.currentDomain;
    var layout = energyGraphState.layout;
    if (!anchor || !domain || !layout) return;
    var factor = event.deltaY < 0 ? 0.82 : 1.22;
    var xOnly = event.shiftKey && !event.altKey;
    var yOnly = event.altKey && !event.shiftKey;
    var baseXSpan = layout.baseX[1] - layout.baseX[0];
    var baseYSpan = layout.baseY[1] - layout.baseY[0];
    var minXSpan = baseXSpan / 500;
    var minYSpan = baseYSpan / 500;
    if (!xOnly) {
      var newX0 = anchor.x - (anchor.x - domain.x[0]) * factor;
      var newX1 = anchor.x + (domain.x[1] - anchor.x) * factor;
      if (newX1 - newX0 < minXSpan) { var cx = (newX0 + newX1) / 2; newX0 = cx - minXSpan / 2; newX1 = cx + minXSpan / 2; }
      energyGraphState.xDomain = [newX0, newX1];
    }
    if (!yOnly) {
      var newY0 = anchor.y - (anchor.y - domain.y[0]) * factor;
      var newY1 = anchor.y + (domain.y[1] - anchor.y) * factor;
      if (newY1 - newY0 < minYSpan) { var cy = (newY0 + newY1) / 2; newY0 = cy - minYSpan / 2; newY1 = cy + minYSpan / 2; }
      energyGraphState.yDomain = [newY0, newY1];
    }
    energyGraphScheduleViewUpdate();
  }, { passive: false });
  /* ── Double-click = reset ── */
  svg.addEventListener("dblclick", function(event) {
    event.preventDefault();
    energyGraphSetDomains("reset");
    energyGraphUpdateChart();
  });
  /* ── Pointer-based box-select ── */
  var boxState = null;
  svg.addEventListener("pointerdown", function(event) {
    if (event.button !== 0) return;
    if (event.target.closest && (event.target.closest("[data-energy-frame]") || event.target.closest("[data-energy-annotation]") || event.target.closest("[data-energy-draggable]"))) return;
    var pt = energyGraphClientToPlotPoint(event, svg);
    if (!pt) return;
    if (energyGraphState.viewMode === "box") {
      event.preventDefault();
      svg.setPointerCapture(event.pointerId);
      var overlay = svg.querySelector("[data-energy-overlay]");
      boxState = { id: event.pointerId, startX: pt.svgX, startY: pt.svgY, overlay: overlay, rect: null };
      var r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      r.setAttribute("class", "energy-box-select-rect");
      r.setAttribute("x", pt.svgX);
      r.setAttribute("y", pt.svgY);
      r.setAttribute("width", "0");
      r.setAttribute("height", "0");
      if (overlay) overlay.appendChild(r);
      boxState.rect = r;
    } else if (energyGraphState.viewMode === "pan") {
      event.preventDefault();
      svg.setPointerCapture(event.pointerId);
      var layout = energyGraphState.layout;
      var startXDom = (energyGraphState.xDomain || layout.baseX).slice();
      var startYDom = (energyGraphState.yDomain || layout.baseY).slice();
      boxState = { id: event.pointerId, startSvgX: pt.svgX, startSvgY: pt.svgY, startXDom: startXDom, startYDom: startYDom, mode: "pan" };
      svg.classList.add("panning");
    }
  });
  svg.addEventListener("pointermove", function(event) {
    if (!boxState || event.pointerId !== boxState.id) return;
    if (boxState.mode === "pan") {
      var layout = energyGraphState.layout;
      if (!layout) return;
      var pt = svg.createSVGPoint();
      pt.x = event.clientX; pt.y = event.clientY;
      var ctm = svg.getScreenCTM();
      if (!ctm) return;
      pt = pt.matrixTransform(ctm.inverse());
      var geom = energyGraphState._cachedGeom || energyGraphChartGeometry();
      var Pw = geom.W - geom.pad.left - geom.pad.right;
      var Ph = geom.H - geom.pad.top - geom.pad.bottom;
      var dxRatio = (pt.x - boxState.startSvgX) / Pw;
      var dyRatio = (pt.y - boxState.startSvgY) / Ph;
      var xSpan = boxState.startXDom[1] - boxState.startXDom[0];
      var ySpan = boxState.startYDom[1] - boxState.startYDom[0];
      energyGraphState.xDomain = [boxState.startXDom[0] - dxRatio * xSpan, boxState.startXDom[1] - dxRatio * xSpan];
      energyGraphState.yDomain = [boxState.startYDom[0] + dyRatio * ySpan, boxState.startYDom[1] + dyRatio * ySpan];
      energyGraphClampDomains();
      energyGraphScheduleViewUpdate();
      return;
    }
    if (!boxState.rect) return;
    var pt2 = svg.createSVGPoint();
    pt2.x = event.clientX; pt2.y = event.clientY;
    var ctm2 = svg.getScreenCTM();
    if (!ctm2) return;
    pt2 = pt2.matrixTransform(ctm2.inverse());
    var sx = Math.min(boxState.startX, pt2.x);
    var sy = Math.min(boxState.startY, pt2.y);
    var sw = Math.abs(pt2.x - boxState.startX);
    var sh = Math.abs(pt2.y - boxState.startY);
    boxState.rect.setAttribute("x", sx);
    boxState.rect.setAttribute("y", sy);
    boxState.rect.setAttribute("width", sw);
    boxState.rect.setAttribute("height", sh);
  });
  svg.addEventListener("pointerup", function(event) {
    if (!boxState || event.pointerId !== boxState.id) return;
    if (boxState.mode === "pan") {
      svg.releasePointerCapture(event.pointerId);
      svg.classList.remove("panning");
      boxState = null;
      energyGraphUpdateChart();
      return;
    }
    svg.releasePointerCapture(event.pointerId);
    if (boxState.rect) {
      var rx = Number(boxState.rect.getAttribute("x"));
      var ry = Number(boxState.rect.getAttribute("y"));
      var rw = Number(boxState.rect.getAttribute("width"));
      var rh = Number(boxState.rect.getAttribute("height"));
      boxState.rect.remove();
      if (rw >= 6 && rh >= 6) {
        var layout = energyGraphState.layout;
        if (layout) {
          var domain = energyGraphState.currentDomain;
          if (domain) {
            var geom = energyGraphState._cachedGeom || energyGraphChartGeometry();
            var Pw = geom.W - geom.pad.left - geom.pad.right;
            var Ph = geom.H - geom.pad.top - geom.pad.bottom;
            var x0 = domain.x[0] + ((rx - geom.pad.left) / Pw) * (domain.x[1] - domain.x[0]);
            var x1 = domain.x[0] + ((rx + rw - geom.pad.left) / Pw) * (domain.x[1] - domain.x[0]);
            var y1 = domain.y[1] - ((ry - geom.pad.top) / Ph) * (domain.y[1] - domain.y[0]);
            var y0 = domain.y[1] - ((ry + rh - geom.pad.top) / Ph) * (domain.y[1] - domain.y[0]);
            energyGraphState.xDomain = [Math.min(x0, x1), Math.max(x0, x1)];
            energyGraphState.yDomain = [Math.min(y0, y1), Math.max(y0, y1)];
            energyGraphClampDomains();
            boxState = null;
            activatePlotTool("");
            energyGraphUpdateChart();
            return;
          }
        }
      }
    }
    boxState = null;
  });
  svg.addEventListener("pointercancel", function(event) {
    if (!boxState || event.pointerId !== boxState.id) return;
    if (boxState.rect) boxState.rect.remove();
    if (boxState.mode === "pan") svg.classList.remove("panning");
    svg.releasePointerCapture(event.pointerId);
    boxState = null;
  });
  /* ── Frame / annotation clicks ── */
  root.querySelectorAll("[data-energy-frame]").forEach(function(element) {
    element.addEventListener("click", function(event) {
      event.stopPropagation();
      var frame = Number(element.getAttribute("data-energy-frame"));
      var annotationId = element.getAttribute("data-energy-annotation") || "";
      var node = (energyGraphState.data.nodes || []).find(function(item) { return Number(item.frame_index) === frame; });
      if (!node) return;
      energyGraphState.liveFollow = false;
      var ann = annotationId ? (energyGraphState.data.annotations || []).find(function(a) { return String(a.id) === annotationId; }) : null;
      var candId = ann ? (ann.candidate_id || ann.id || "") : "";
      energyGraphSetSelection({
        nodeId: String(node.id),
        frameIndex: frame,
        annotationId: annotationId,
        candidateId: candId,
        origin: "user"
      });
      if (annotationId) energyGraphUpdateChart();
    });
  });
  /* ── Candidate band chip clicks → jump to frame ── */
  svg.querySelectorAll("[data-energy-candidate-jump]").forEach(function(chip) {
    chip.addEventListener("click", function(event) {
      event.stopPropagation();
      var fi = Number(chip.getAttribute("data-energy-candidate-jump"));
      if (!isNaN(fi)) {
        energyGraphState.liveFollow = false;
        energyGraphSelectFrame(fi);
        /* Scroll right panel candidate list to frame group */
        var list = root.querySelector(".energy-candidate-list");
        if (list) {
          var groupHead = list.querySelector('[data-energy-candidate-frame="' + fi + '"]');
          if (groupHead) {
            list.scrollTop = groupHead.offsetTop - list.offsetTop;
            /* Highlight matching rows */
            list.querySelectorAll('[data-energy-candidate-jump="' + fi + '"]').forEach(function(row) {
              row.classList.add("energy-candidate-list-highlight");
              setTimeout(function() { row.classList.remove("energy-candidate-list-highlight"); }, 1500);
            });
          }
        }
      }
    });
  });
  /* ── Overflow chip click → scroll candidate list to top ── */
  svg.querySelectorAll("[data-energy-candidate-expand]").forEach(function(chip) {
    chip.addEventListener("click", function(event) {
      event.stopPropagation();
      var list = root.querySelector(".energy-candidate-list");
      if (list) {
        list.scrollTop = 0;
        /* Highlight all rows briefly */
        list.querySelectorAll(".energy-candidate-row").forEach(function(row) {
          row.classList.add("energy-candidate-list-highlight");
          setTimeout(function() { row.classList.remove("energy-candidate-list-highlight"); }, 1500);
        });
      }
    });
  });
  /* ── Hover linkage: chip hit rects, annotation markers, candidate list rows ── */
  function _energyHoverSet(nodeId, annotationId, pointerEvent) {
    energyGraphState.hover = { nodeId: nodeId, annotationId: annotationId || "" };
    var chartRoot = svg.closest("[data-energy-chart-panel]") || svg.closest("[data-energy-workspace]");
    if (chartRoot) chartRoot.classList.add("energy-hovering");
    var hoveredPoint = null;
    svg.querySelectorAll(".energy-point").forEach(function(el) {
      var nid = el.closest("[data-energy-node]") ? el.closest("[data-energy-node]").getAttribute("data-energy-node") : "";
      if (nid === nodeId) { el.classList.add("hovered"); el.setAttribute("r", "7"); hoveredPoint = el; }
      else { el.classList.remove("hovered"); el.setAttribute("r", "4"); }
    });
    svg.querySelectorAll("[data-energy-annotation]").forEach(function(g) {
      var aid = g.getAttribute("data-energy-annotation");
      var marker = g.querySelector(".energy-marker-ts, .energy-marker-int");
      if (marker) {
        if (aid === annotationId) marker.classList.add("hovered");
        else marker.classList.remove("hovered");
      }
    });
    var hoverFi = "";
    if (annotationId) {
      var hoverAnn = (energyGraphState.data.annotations || []).find(function(a) { return String(a.id) === annotationId; });
      if (hoverAnn) hoverFi = String(Number(hoverAnn.frame_index));
    }
    svg.querySelectorAll(".energy-band-chip-bg").forEach(function(rect) {
      var fi = rect.getAttribute("data-energy-candidate-frame");
      if (fi === hoverFi) rect.classList.add("hovered");
      else rect.classList.remove("hovered");
    });
    _energyHoverRenderCrosshair(hoveredPoint);
    _energyHoverRenderTooltip(nodeId, annotationId, pointerEvent);
  }
  function _energyHoverClear() {
    energyGraphState.hover = null;
    var chartRoot = svg.closest("[data-energy-chart-panel]") || svg.closest("[data-energy-workspace]");
    if (chartRoot) chartRoot.classList.remove("energy-hovering");
    svg.querySelectorAll(".energy-point.hovered").forEach(function(el) { el.classList.remove("hovered"); el.setAttribute("r", "4"); });
    svg.querySelectorAll(".energy-marker-ts.hovered, .energy-marker-int.hovered").forEach(function(el) { el.classList.remove("hovered"); });
    svg.querySelectorAll(".energy-band-chip-bg.hovered").forEach(function(el) { el.classList.remove("hovered"); });
    svg.querySelectorAll(".energy-crosshair.hover-crosshair").forEach(function(el) { el.remove(); });
    var existingTip = document.querySelector(".energy-hover-tooltip");
    if (existingTip) existingTip.remove();
  }
  function _energyHoverRenderCrosshair(hoveredPoint) {
    svg.querySelectorAll(".energy-crosshair.hover-crosshair").forEach(function(el) { el.remove(); });
    if (!hoveredPoint) return;
    var geom = energyGraphState._cachedGeom || (typeof energyGraphChartGeometry === "function" ? energyGraphChartGeometry() : null);
    if (!geom) return;
    var cx = parseFloat(hoveredPoint.getAttribute("cx"));
    var cy = parseFloat(hoveredPoint.getAttribute("cy"));
    if (isNaN(cx) || isNaN(cy)) return;
    var plotGroup = svg.querySelector("[data-energy-plot]");
    if (!plotGroup) return;
    var y1 = geom.pad.top, y2 = geom.H - geom.pad.bottom;
    var x1 = geom.pad.left, x2 = geom.W - geom.pad.right;
    var crossSvg = '<line class="energy-crosshair hover-crosshair" x1="' + cx + '" y1="' + y1 + '" x2="' + cx + '" y2="' + y2 + '"/>';
    crossSvg += '<line class="energy-crosshair hover-crosshair" x1="' + x1 + '" y1="' + cy + '" x2="' + x2 + '" y2="' + cy + '"/>';
    plotGroup.insertAdjacentHTML("beforeend", crossSvg);
  }
  function _energyHoverRenderTooltip(nodeId, annotationId, pointerEvent) {
    var existingTip = document.querySelector(".energy-hover-tooltip");
    if (existingTip) existingTip.remove();
    if (!pointerEvent) return;
    var data = energyGraphState.data;
    if (!data) return;
    var node = (data.nodes || []).find(function(n) { return String(n.id) === nodeId; });
    if (!node) return;
    var annotation = annotationId ? (data.annotations || []).find(function(a) { return String(a.id) === annotationId; }) : null;
    var displayLabel = annotation ? String(annotation.display_label || annotation.label || annotation.id) : String(node.label || node.id);
    var frameNumber = annotation && annotation.frame_number != null ? Number(annotation.frame_number) : (node.frame_index != null ? Number(node.frame_index) + 1 : "?");
    var currentXMode = energyGraphState.xAxisMode || (data.metadata && data.metadata.default_x_axis_mode) || (data.x_axis && data.x_axis.default_mode) || "scanned_coordinate";
    var currentXVal = energyGraphNodeX(node, currentXMode);
    var xVal = currentXVal != null ? energyGraphFormatNumber(currentXVal, 3) : "?";
    var xModes = (data.metadata && data.metadata.x_axis_modes) || (data.x_axis && data.x_axis.modes) || [];
    var modeObj = xModes.find(function(m) { return m.id === currentXMode; });
    var xUnit = modeObj ? String(modeObj.unit || "") : String(((data.x_axis || {}).unit) || "");
    var energyVal = node.energy != null ? energyGraphFormatNumber(node.energy, 3) : "?";
    var tipHtml = '<div style="font-weight:600;">' + escapeHtml(displayLabel) + ' \u00b7 F' + frameNumber + '</div>';
    tipHtml += '<div>' + escapeHtml(xVal) + (xUnit ? ' ' + escapeHtml(xUnit) : '') + '</div>';
    tipHtml += '<div>' + escapeHtml(energyVal) + ' kcal/mol</div>';
    var chartPanel = svg.closest("[data-energy-chart-panel]");
    if (!chartPanel) return;
    var tip = document.createElement("div");
    tip.className = "energy-hover-tooltip";
    tip.innerHTML = tipHtml;
    chartPanel.style.position = chartPanel.style.position || "relative";
    chartPanel.appendChild(tip);
    var rect = chartPanel.getBoundingClientRect();
    var tx = pointerEvent.clientX - rect.left + 12;
    var ty = pointerEvent.clientY - rect.top - 10;
    if (tx + tip.offsetWidth > rect.width) tx = tx - tip.offsetWidth - 24;
    if (ty < 0) ty = 0;
    tip.style.left = tx + "px";
    tip.style.top = ty + "px";
  }
  /* Hover on annotation markers */
  svg.querySelectorAll("[data-energy-annotation]").forEach(function(g) {
    g.addEventListener("pointerenter", function(ev) {
      var aid = g.getAttribute("data-energy-annotation");
      var fi = g.getAttribute("data-energy-frame");
      var node = fi != null ? (energyGraphState.data.nodes || []).find(function(n) { return Number(n.frame_index) === Number(fi); }) : null;
      if (node) _energyHoverSet(String(node.id), aid, ev);
    });
    g.addEventListener("pointerleave", function() { _energyHoverClear(); });
  });
  /* Hover on band chip hit rects */
  svg.querySelectorAll(".energy-band-chip-hit[data-energy-candidate-jump]").forEach(function(chip) {
    chip.addEventListener("pointerenter", function(ev) {
      var fi = Number(chip.getAttribute("data-energy-candidate-jump"));
      if (isNaN(fi)) return;
      var data = energyGraphState.data;
      var ann = (data.annotations || []).find(function(a) { return Number(a.frame_index) === fi && ["ts", "intermediate"].indexOf(a.type) >= 0; });
      var node = (data.nodes || []).find(function(n) { return Number(n.frame_index) === fi; });
      if (node) _energyHoverSet(String(node.id), ann ? String(ann.id) : "", ev);
    });
    chip.addEventListener("pointerleave", function() { _energyHoverClear(); });
    chip.addEventListener("pointermove", function(ev) {
      var existingTip = document.querySelector(".energy-hover-tooltip");
      if (!existingTip) return;
      var chartPanel = svg.closest("[data-energy-chart-panel]");
      if (!chartPanel) return;
      var rect = chartPanel.getBoundingClientRect();
      var tx = ev.clientX - rect.left + 12;
      var ty = ev.clientY - rect.top - 10;
      if (tx + existingTip.offsetWidth > rect.width) tx = tx - existingTip.offsetWidth - 24;
      if (ty < 0) ty = 0;
      existingTip.style.left = tx + "px";
      existingTip.style.top = ty + "px";
    });
  });
  /* Hover on candidate list rows (in the inspector panel) */
  var listPanel = root ? root.querySelector(".energy-candidate-list") : null;
  if (listPanel) {
    listPanel.querySelectorAll("[data-energy-candidate-jump]").forEach(function(row) {
      row.addEventListener("pointerenter", function(ev) {
        var fi = Number(row.getAttribute("data-energy-candidate-jump"));
        var annId = row.getAttribute("data-energy-candidate-annotation") || "";
        var node = (energyGraphState.data.nodes || []).find(function(n) { return Number(n.frame_index) === fi; });
        if (node) _energyHoverSet(String(node.id), annId, ev);
      });
      row.addEventListener("pointerleave", function() { _energyHoverClear(); });
    });
  }
  energyGraphBindMarkerDrag(root);
}

function energyGraphBind(root) {
  const data = energyGraphState.data;
  if (!data) return;
  energyGraphBindChartInteractions(root);
  const seriesSelect = root.querySelector("[data-energy-series]");
  if (seriesSelect) seriesSelect.addEventListener("change", function() {
    energyGraphState.seriesId = seriesSelect.value;
    energyGraphState.xDomain = null;
    energyGraphState.yDomain = null;
    energyGraphState.layout = null;
    energyGraphRenderCurrent();
  });
  const xAxisSelect = root.querySelector("[data-energy-x-axis]");
  if (xAxisSelect) xAxisSelect.addEventListener("change", function() {
    energyGraphState.xAxisMode = xAxisSelect.value;
    energyGraphState.xDomain = null;
    energyGraphState.yDomain = null;
    energyGraphState.layout = null;
    energyGraphRenderCurrent();
  });
  root.querySelectorAll("[data-energy-action]").forEach(function(button) {
    button.addEventListener("click", function() {
      const action = button.getAttribute("data-energy-action");
      if (action === "global" || action === "fit" || action === "reset") {
        energyGraphSetDomains(action);
        return;
      }
      if (action === "box" || action === "pan") {
        activatePlotTool(energyGraphState.viewMode === action ? "" : action);
        return;
      }
      if (action === "labels") {
        energyGraphState.annotationMode = energyGraphState.annotationMode === "hide" ? "all" : "hide";
        var svg = root.querySelector("[data-energy-chart]");
        if (svg) {
          svg.classList.toggle("labels-hidden", energyGraphState.annotationMode === "hide");
          var labelsBtn = root.querySelector('[data-energy-action="labels"]');
          if (labelsBtn) labelsBtn.classList.toggle("active", energyGraphState.annotationMode !== "hide");
        }
        return;
      }
      if (action === "toggle-selection-lock") { energyGraphToggleSelectionLock(); return; }
      if (action === "reload") { renderEnergyGraphWorkspace(lastSelectedJob); return; }
      if (action === "retry") { renderEnergyGraphWorkspace(lastSelectedJob); return; }
      if (action === "focus-curve") {
        var grid = root.querySelector(".energy-workspace-grid");
        if (grid) {
          grid.classList.toggle("energy-focus-mode");
          button.classList.toggle("active", grid.classList.contains("energy-focus-mode"));
        }
        return;
      }
      const node = energyGraphCurrentNode(data);
      const frame = node && node.frame_index != null ? Number(node.frame_index) : null;
      if (frame == null) return;
      const nodes = data.nodes || [];
      const index = nodes.findIndex(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); });
      const fallbackIndex = index >= 0 ? index : nodes.findIndex(function(item) { return Number(item.frame_index) === frame; });
      if (action === "prev" && fallbackIndex > 0) energyGraphSelectFrame(nodes[fallbackIndex - 1].frame_index, undefined, nodes[fallbackIndex - 1].id);
      if (action === "next" && fallbackIndex >= 0 && fallbackIndex < nodes.length - 1) energyGraphSelectFrame(nodes[fallbackIndex + 1].frame_index, undefined, nodes[fallbackIndex + 1].id);
    });
  });
  /* ── ResizeObserver ── */
  if (energyGraphState.resizeObserver) { energyGraphState.resizeObserver.disconnect(); energyGraphState.resizeObserver = null; }
  var chartScroll = root.querySelector("[data-energy-chart-scroll]");
  if (chartScroll && typeof ResizeObserver !== "undefined") {
    var resizeTimer = null;
    var ro = new ResizeObserver(function() {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function() {
        resizeTimer = null;
        if (!chartScroll.isConnected || !energyGraphState.data) return;
        energyGraphState.viewSize = { width: chartScroll.clientWidth, height: chartScroll.clientHeight };
        energyGraphUpdateChart();
      }, 150);
    });
    ro.observe(chartScroll);
    energyGraphState.resizeObserver = ro;
  }
  const menu = root.querySelector("[data-energy-menu]");
  function closeMenu() { if (menu) menu.classList.remove("open"); }
  var chartPanel = root.querySelector("[data-energy-chart-panel]") || chartScroll;
  if (chartPanel && menu) {
    chartPanel.addEventListener("contextmenu", function(event) {
      event.preventDefault();
      const bounds = root.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(bounds.width - 218, event.clientX - bounds.left - 80)) + "px";
      menu.style.top = Math.max(80, Math.min(bounds.height - 240, event.clientY - bounds.top - 20)) + "px";
      menu.classList.add("open");
    });
    root.addEventListener("click", function(event) {
      if (!event.target.closest("[data-energy-menu]") && !event.target.closest("[data-energy-chart-panel]")) closeMenu();
    });
  }
  root.querySelectorAll("[data-energy-menu-series]").forEach(function(button) {
    button.addEventListener("click", function() {
      energyGraphState.seriesId = button.getAttribute("data-energy-menu-series");
      energyGraphState.xDomain = null;
      energyGraphState.yDomain = null;
      closeMenu();
      energyGraphRenderCurrent();
    });
  });
  root.querySelectorAll("[data-energy-role]").forEach(function(button) {
    button.addEventListener("click", function(event) {
      event.preventDefault();
      const node = energyGraphCurrentNode(data);
      if (!node || node.frame_index == null) return;
      energyGraphSetFrameRole(node.frame_index, button.getAttribute("data-energy-role"));
    });
  });
  root.querySelectorAll("[data-energy-menu-annotation]").forEach(function(button) {
    button.addEventListener("click", function() {
      energyGraphState.annotationMode = button.getAttribute("data-energy-menu-annotation");
      closeMenu();
      energyGraphUpdateChart();
    });
  });
  const current = energyGraphCurrentNode(data);
  if (current && current.frame_index != null) energyGraphLoadStructure(current.frame_index);
  energyGraphBindInspectorButtons(root);
}

/* ── Unified selection writer: single source of truth for all selection changes ── */
function energyGraphSetSelection(opts) {
  var nodeId = opts.nodeId || "";
  var frameIndex = opts.frameIndex;
  var annotationId = opts.annotationId || "";
  var candidateId = opts.candidateId || "";
  var origin = opts.origin || "user";
  var data = energyGraphState.data;
  if (!data) return;
  var nodes = Array.isArray(data.nodes) ? data.nodes : [];
  var node = nodeId
    ? nodes.find(function(n) { return String(n.id) === String(nodeId); })
    : (frameIndex != null ? nodes.find(function(n) { return Number(n.frame_index) === Number(frameIndex); }) : null);
  if (!node) return;
  var effectiveFrame = Number(node.frame_index);
  var oldFrame = energyGraphFrameIndexForNode(data, energyGraphState.selectedNodeId);
  /* Store unified selection object */
  energyGraphState.selection = {
    nodeId: String(node.id),
    frameIndex: effectiveFrame,
    annotationId: annotationId,
    candidateId: candidateId,
    origin: origin
  };
  /* Mirror into legacy fields for backward compat */
  energyGraphState.selectedNodeId = String(node.id);
  energyGraphState.selectionOrigin = origin === "follow" ? "follow" : "user";
  if (data.view_type === "irc" && origin !== "follow") energyGraphState.liveFollow = false;
  /* Resolve annotation if not explicitly provided */
  if (!annotationId) {
    var ann = (data.annotations || []).find(function(a) {
      return Number(a.frame_index) === effectiveFrame && ["ts", "intermediate"].indexOf(a.type) >= 0;
    });
    annotationId = ann ? String(ann.id) : "";
    if (ann && !candidateId) candidateId = ann.candidate_id || ann.id || "";
  }
  energyGraphState.selectedAnnotationId = annotationId;
  energyGraphUpdateSelection(Number(effectiveFrame) !== Number(oldFrame));
  /* Push selection to structure viewer */
  if (window.ACPStructureViewer && window.ACPStructureViewer.onEnergyNodeSelected) {
    var view = String(data.view_type || "");
    var entryMeta = { kind: view, frameIndex: Number(effectiveFrame) };
    if (view === "conformer") {
      entryMeta.kind = "conformer";
      entryMeta.key = String(node.id || "").replace(/^conf_/, "");
    } else if (view === "scan" || view === "scan_trajectory") {
      entryMeta.kind = "scan";
    } else if (view === "optimization") {
      entryMeta.kind = "optimization";
    } else if (view === "sampling") {
      entryMeta.kind = "optimization";
    } else if (view === "irc") {
      entryMeta.kind = "irc";
      entryMeta.endpoint = String((node.metadata && node.metadata.direction) || "");
    }
    if (candidateId) {
      entryMeta.kind = "pes";
      entryMeta.candidateId = String(candidateId);
    }
    entryMeta.geometryRef = node.geometry_ref || "";
    entryMeta.label = node.label || "";
    window.ACPStructureViewer.onEnergyNodeSelected(energyGraphState.jobId, entryMeta);
  }
}

function energyGraphSelectFrame(frameIndex, origin, nodeId) {
  var data = energyGraphState.data;
  if (!data) return;
  var nodes = data.nodes || [];
  var node = nodeId
    ? nodes.find(function(item) { return String(item.id) === String(nodeId); })
    : nodes.find(function(item) { return Number(item.frame_index) === Number(frameIndex); });
  if (!node) return;
  var annotation = (data.annotations || []).find(function(item) {
    return Number(item.frame_index) === Number(node.frame_index) && ["ts", "intermediate"].indexOf(item.type) >= 0;
  });
  energyGraphSetSelection({
    nodeId: String(node.id),
    frameIndex: Number(node.frame_index),
    annotationId: annotation ? String(annotation.id) : "",
    candidateId: annotation ? (annotation.candidate_id || annotation.id || "") : "",
    origin: origin || "user"
  });
}

window._energyGraphSyncFromStructure = function(entryId) {
  if (!entryId || !energyGraphState.data || !energyGraphState.jobId) return;
  var data = energyGraphState.data;
  var nodes = data.nodes || [];
  var annotations = data.annotations || [];
  var view = String(data.view_type || "");
  var matchedNode = null;

  if (entryId.indexOf("conf_") === 0) {
    var key = entryId.replace(/^conf_/, "");
    matchedNode = nodes.find(function(n) { return String(n.id) === entryId || String(n.id) === "conf_" + key; });
  } else if (entryId.indexOf("pes_") === 0) {
    var candidateId = entryId.replace(/^pes_/, "");
    var ann = annotations.find(function(a) { return a.candidate_id === candidateId; });
    if (ann) matchedNode = nodes.find(function(n) { return Number(n.frame_index) === Number(ann.frame_index); });
  } else if (entryId.indexOf("scan_frame_") === 0) {
    var fi = Number(entryId.replace("scan_frame_", ""));
    matchedNode = nodes.find(function(n) { return Number(n.frame_index) === fi; });
  } else if (entryId.indexOf("batch_") === 0) {
    matchedNode = nodes.find(function(n) { return String(n.id) === entryId; });
  } else {
    matchedNode = nodes.find(function(n) { return String(n.id) === entryId; });
  }

  if (matchedNode && String(matchedNode.id) !== String(energyGraphState.selectedNodeId)) {
    energyGraphState.liveFollow = false;
    var syncAnn = annotations.find(function(a) {
      return Number(a.frame_index) === Number(matchedNode.frame_index) && ["ts", "intermediate"].indexOf(a.type) >= 0;
    });
    if (activeViewerTab === "energy") {
      energyGraphSetSelection({
        nodeId: String(matchedNode.id),
        frameIndex: Number(matchedNode.frame_index),
        annotationId: syncAnn ? String(syncAnn.id) : "",
        candidateId: syncAnn ? (syncAnn.candidate_id || syncAnn.id || "") : "",
        origin: "structure_sync"
      });
    } else {
      energyGraphState.selectedNodeId = String(matchedNode.id);
      energyGraphState.selectionOrigin = "structure_sync";
    }
  }
};

function energyGenCandidateName(frameIndex, role) {
  var tag = role === "ts" ? "TS" : "INT";
  return "frame_" + String(frameIndex).padStart(3, "0") + "__TAG_" + tag;
}

function energyGraphSanitizeName(raw) {
  if (!raw) return "";
  return String(raw).trim().slice(0, 80).replace(/[\\\/:*?"<>|\s]+/g, "_");
}

function energyGraphUpsertCandidate(frameIndex, patch) {
  return candidateReviewCtl().upsertCandidate(frameIndex, patch);
}

function energyGraphRemoveCandidate(frameIndex) {
  return candidateReviewCtl().removeCandidate(frameIndex);
}

function energyGraphSetFrameRole(frameIndex, role) {
  var changed = candidateReviewCtl().setFrameRole(frameIndex, role);
  if (changed) energyGraphRenderCurrent();
}

function energyGraphMoveCandidate(frameIndexFrom, frameIndexTo) {
  var result = candidateReviewCtl().moveCandidate(frameIndexFrom, frameIndexTo);
  if (result.occupied) {
    window.alert("帧 " + Number(frameIndexTo) + " 已有 " + (result.occupied.role === "ts" ? "TS" : "INT") + " 标记，每帧只能有一个候选。");
    return false;
  }
  return result.moved;
}

function energyGraphApplyReviewResult(result) {
  candidateReviewCtl().applyReviewResult(result);
  s2scanState.activeCount = (typeof result.selected_count === "number") ? result.selected_count : candidateReviewState().candidates.length;
}

function energyGraphReviewPayload() {
  var review = candidateReviewState();
  var payload = {
    candidates: review.candidates.map(function(entry) {
      var p = { frame_index: entry.frame_index, role: entry.role };
      if (entry.candidate_id) p.candidate_id = entry.candidate_id;
      if (entry.name) p.name = entry.name;
      return p;
    }),
  };
  if (review.revision) payload.expected_revision = review.revision;
  return payload;
}

function energyGraphSelectionActionLabel() {
  var review = candidateReviewState();
  if (review.selectionLocked) return "选点已锁定 · 点击解锁";
  if (review.reviewStatus === "confirmed" && !review.dirty) return "选点可编辑";
  if (review.reviewStatus === "confirmed") return "保存并锁定选点";
  return "确认并锁定选点";
}

function energyGraphSelectionActionTitle() {
  var review = candidateReviewState();
  if (review.selectionLocked) return "点击解锁，编辑手动选点";
  if (review.reviewStatus === "confirmed" && !review.dirty) return "当前已进入编辑模式，点击重新锁定选点";
  return "保存手动选点结果并锁定";
}

function energyGraphUpdateSelectionToolbar() {
  var review = candidateReviewState();
  var screen = getEnergyViewerContainer();
  if (!screen) return;
  var button = screen.querySelector('[data-energy-action="toggle-selection-lock"]');
  if (button) {
    button.textContent = energyGraphSelectionActionLabel();
    button.title = energyGraphSelectionActionTitle();
    button.setAttribute("aria-pressed", review.selectionLocked ? "true" : "false");
    button.classList.toggle("locked", !!review.selectionLocked);
    button.classList.toggle("editable", !review.selectionLocked);
  }
  var dirtyBadge = screen.querySelector("[data-energy-dirty-badge]");
  if (dirtyBadge) dirtyBadge.style.display = review.dirty ? "inline-flex" : "none";
  var unconfirmedHint = screen.querySelector("[data-energy-unconfirmed-hint]");
  if (unconfirmedHint) unconfirmedHint.style.display = review.reviewStatus === "confirmed" ? "none" : "";
  var savedState = screen.querySelector("[data-energy-saved-state]");
  if (savedState) {
    var reviewMeta = (energyGraphState.data && energyGraphState.data.metadata || {}).review || {};
    var savedText = review.savedAt
      ? "已保存 " + String(review.savedAt).replace("T", " ").slice(0, 19)
      : (reviewMeta.saved || review.reviewStatus === "confirmed" ? "已保存" : "尚未保存");
    savedState.textContent = savedText;
  }
}

function energyGraphToggleSelectionLock() {
  var ctl = candidateReviewCtl();
  var review = candidateReviewState();
  if (review.selectionLocked) {
    ctl.setLock(false);
    ctl.setSelectionMode(true);
    energyGraphRenderCurrent();
    return;
  }
  // Re-lock an unchanged saved review locally. Only changed selections need
  // to go through the persistence endpoint again.
  if (review.reviewStatus === "confirmed" && !review.dirty) {
    ctl.setLock(true);
    ctl.setSelectionMode(false);
    energyGraphRenderCurrent();
    return;
  }
  energyGraphOpenSaveDialog();
}

function energyGraphOpenSaveDialog() {
  var jobId = String(energyGraphState.jobId || "");
  if (!jobId) { window.alert("当前没有选中任务，无法保存。"); return; }
  var existing = document.getElementById("energy-save-dialog");
  if (existing) existing.remove();

  var tsCount = 0, intCount = 0;
  var review = candidateReviewState();
  review.candidates.forEach(function(e) { if (e.role === "ts") tsCount++; else if (e.role === "intermediate") intCount++; });
  var totalCount = review.candidates.length;
  var dialogTitle = review.reviewStatus === "confirmed" ? "保存并锁定 PES 选点" : "确认 PES 选点";

  var candidateRows = review.candidates.map(function(entry, idx) {
    var label = entry.name || energyGenCandidateName(entry.frame_index, entry.role);
    var roleLabel = entry.role === "ts" ? "TS" : "INT";
    var idChip = entry.candidate_id
      ? '<span class="energy-save-candidate-frame" title="已保存的稳定候选 ID">' + escapeHtml(String(entry.candidate_id)) + '</span>'
      : '<span class="energy-save-candidate-frame" style="opacity:0.65;">保存后生成候选 ID</span>';
    var stateChip = entry.saved
      ? '<span class="energy-save-candidate-frame" style="color:var(--green,#7ad97a);">已保存</span>'
      : '<span class="energy-save-candidate-frame" style="color:var(--amber,#e8b45a);">工作区</span>';
    return '<div class="energy-save-candidate-item"><span class="energy-save-candidate-role ' + escapeHtml(entry.role) + '">' + escapeHtml(roleLabel) + '</span><span class="energy-save-candidate-name">' + escapeHtml(label) + '</span><span class="energy-save-candidate-frame">帧 ' + escapeHtml(String(entry.frame_index)) + '</span>' + idChip + stateChip + '</div>';
  }).join("");

  var overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "energy-save-dialog";
  overlay.style.display = "flex";
  overlay.innerHTML =
    '<div class="modal-dialog" style="max-width:520px;">' +
      '<div class="modal-header">' +
        '<h2>' + escapeHtml(dialogTitle) + '</h2>' +
        '<button class="modal-close" data-energy-dialog-close>&times;</button>' +
      '</div>' +
      '<div class="modal-body">' +
        '<div class="modal-section">' +
          '<p style="font-size:12px;color:var(--text-secondary);margin:0 0 8px;">TS <strong>' + tsCount + '</strong> · INT <strong>' + intCount + '</strong> · 总计 <strong>' + totalCount + '</strong></p>' +
          (candidateRows
            ? '<div class="energy-save-candidate-list">' + candidateRows + '</div>'
            : '<p style="font-size:11px;color:var(--text-tertiary);margin:0;">当前没有标记任何候选点。</p>') +
        '</div>' +
        '<div class="modal-section" style="margin-top:10px;">' +
          '<label style="display:block;font-size:11px;color:var(--text-tertiary);margin-bottom:4px;">备注（可选）</label>' +
          '<textarea data-energy-dialog-note rows="2" style="width:100%;resize:vertical;padding:6px 8px;border:1px solid var(--border-default);border-radius:6px;background:rgba(255,255,255,0.03);color:var(--text-primary);font-size:12px;"></textarea>' +
        '</div>' +
        '<div class="modal-section" style="margin-top:10px;">' +
          '<label style="display:block;font-size:11px;color:var(--text-tertiary);margin-bottom:4px;">历史选点（每次修改前的自动备份）</label>' +
          '<div data-energy-review-history><div class="muted" style="font-size:11px;">加载中…</div></div>' +
        '</div>' +
        '<div class="energy-dialog-error" style="display:none;color:var(--red);font-size:11px;margin-top:6px;"></div>' +
      '</div>' +
      '<div class="modal-footer">' +
        '<button class="btn ghost" data-energy-dialog-close>取消</button>' +
        '<button class="btn primary" data-energy-dialog-action="save"' + (review.dirty ? "" : ' title="当前没有未保存修改，但仍可重新写入候选结果"') + '>保存并锁定</button>' +
      '</div>' +
    '</div>';

  document.body.appendChild(overlay);

  function closeDialog() { overlay.remove(); }

  (function loadReviewHistory() {
    var box = overlay.querySelector("[data-energy-review-history]");
    if (!box) return;
    apiRemote("/jobs/" + encodeURIComponent(jobId) + "/pes/review").then(function(state) {
      if (!box.isConnected) return;
      var backups = (state && state.backups) || [];
      if (!backups.length) {
        box.innerHTML = '<div class="muted" style="font-size:11px;">暂无历史备份（每次确认/修改选点后自动生成）。</div>';
        return;
      }
      box.innerHTML = backups.map(function(bk) {
        return '<div class="energy-save-candidate-item" style="justify-content:space-between;">' +
          '<span>第 ' + escapeHtml(String(bk.n)) + ' 轮</span>' +
          '<span class="energy-save-candidate-frame">' + escapeHtml(String(bk.confirmed_at || "").replace("T", " ").slice(0, 16)) + '</span>' +
          '<span class="energy-save-candidate-frame">' + escapeHtml(String(bk.selected_count || 0)) + ' 个选点</span>' +
          (bk.note ? '<span class="energy-save-candidate-frame">' + escapeHtml(String(bk.note)) + '</span>' : '') +
          '<button type="button" class="btn ghost" data-energy-review-restore="' + escapeHtml(String(bk.n)) + '" style="font-size:11px;padding:2px 8px;">恢复此轮</button>' +
        '</div>';
      }).join("");
      box.querySelectorAll("[data-energy-review-restore]").forEach(function(btn) {
        btn.addEventListener("click", async function() {
          var n = parseInt(btn.getAttribute("data-energy-review-restore"), 10);
          var errEl = overlay.querySelector(".energy-dialog-error");
          var session = energyGraphResultSession(jobId);
          if (!energyGraphResultSessionCurrent(session)) { closeDialog(); return; }
          btn.disabled = true;
          try {
            var payload = { backup: n };
            if (candidateReviewState().revision) payload.expected_revision = candidateReviewState().revision;
            var result = await apiRemote("/jobs/" + encodeURIComponent(jobId) + "/pes/review/restore", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            });
            if (!energyGraphResultSessionCurrent(session)) { closeDialog(); return; }
            energyGraphApplyReviewResult(result);
            closeDialog();
            energyGraphRenderCurrent();
          } catch (e) {
            if (!energyGraphResultSessionCurrent(session)) { closeDialog(); return; }
            if (errEl) {
              errEl.style.display = "block";
              errEl.textContent = "恢复第 " + n + " 轮失败：" + (e.message || String(e));
            }
            btn.disabled = false;
          }
        });
      });
    }).catch(function() {
      if (box.isConnected) box.innerHTML = '<div class="muted" style="font-size:11px;">历史备份加载失败。</div>';
    });
  })();

  overlay.querySelectorAll("[data-energy-dialog-close]").forEach(function(btn) {
    btn.addEventListener("click", closeDialog);
  });
  overlay.addEventListener("mousedown", function(e) {
    if (e.target === overlay) closeDialog();
  });

  overlay.querySelectorAll("[data-energy-dialog-action]").forEach(function(btn) {
    btn.addEventListener("click", async function() {
      var noteInput = overlay.querySelector("[data-energy-dialog-note]");
      var note = noteInput ? noteInput.value.trim() : "";
      var errEl = overlay.querySelector(".energy-dialog-error");
      if (errEl) { errEl.style.display = "none"; errEl.textContent = ""; }
      var session = energyGraphResultSession(jobId);
      if (!energyGraphResultSessionCurrent(session)) { closeDialog(); return; }

      var payload = energyGraphReviewPayload();
      if (note) payload.note = note;

      btn.disabled = true;
      try {
        var result = await apiRemote("/jobs/" + encodeURIComponent(jobId) + "/pes/review", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!energyGraphResultSessionCurrent(session)) { closeDialog(); return; }
        energyGraphApplyReviewResult(result);
        closeDialog();
        energyGraphRenderCurrent();
      } catch (e) {
        if (!energyGraphResultSessionCurrent(session)) { closeDialog(); return; }
        if (errEl) {
          errEl.style.display = "block";
          errEl.textContent = (e.message || String(e));
        }
        btn.disabled = false;
      }
    });
  });
}

function energyGraphRenderCurrent() {
  var screen = getEnergyViewerContainer();
  if (!screen || !energyGraphState.data || activeViewerTab !== "energy") return;
  if (energyGraphState.data.view_type === "optimization") {
    optimizationGraphRenderCurrent();
    return;
  }
  if (energyGraphState.data.view_type === "sampling") {
    samplingGraphRenderCurrent();
    return;
  }
  energyGraphUpdateSelectionOnData();
  var root = screen.querySelector("[data-energy-workspace]");
  if (root && root.getAttribute("data-energy-view") === "optimization") root = null;
  if (root && root.getAttribute("data-energy-view") === "sampling") root = null;
  // A live PES scan gains the review toolbar when the job becomes terminal.
  // The usual in-place update patches only the chart and inspector.
  var hasReview = !!(energyGraphState.data.metadata && energyGraphState.data.metadata.review);
  if (root && root.getAttribute("data-energy-reviewable") !== (hasReview ? "1" : "0")) root = null;
  if (!root) {
    // Mount once.  Replacing the complete workspace would also detach the
    // 3Dmol container and reset the user's chart view on every data update.
    screen.style.display = "block";
    screen.innerHTML = energyGraphWorkspaceMarkup(energyGraphState.data);
    root = screen.querySelector("[data-energy-workspace]");
    if (root) energyGraphBind(root);
    return;
  }

  // Existing workspaces are patched in place: only the SVG and inspector are
  // refreshed.  The structure viewer, selected frame, and zoom domains stay
  // alive across ordinary updates.
  energyGraphUpdateChart();
  energyGraphUpdateSelection(false, true);
  var currentXMode = energyGraphState.xAxisMode || (energyGraphState.data.metadata && energyGraphState.data.metadata.default_x_axis_mode) || (energyGraphState.data.x_axis && energyGraphState.data.x_axis.default_mode) || "scanned_coordinate";
  var xSel = screen.querySelector("[data-energy-x-axis]");
  if (xSel && xSel.value !== currentXMode) xSel.value = currentXMode;
  var countEl = screen.querySelector("[data-energy-selection-count]");
  if (countEl) countEl.textContent = "已选候选：" + candidateReviewState().candidates.length;
  energyGraphUpdateSelectionToolbar();
}

/* ── Sampling Graph Render ── */
function samplingGraphRenderCurrent() {
  var screen = getEnergyViewerContainer();
  if (!screen || !energyGraphState.data || energyGraphState.data.view_type !== "sampling") return;
  var root = screen.querySelector('[data-energy-view="sampling"]');
  if (!root) {
    screen.style.display = "block";
    screen.innerHTML = samplingWorkspaceMarkup(energyGraphState.data);
    root = screen.querySelector('[data-energy-view="sampling"]');
    if (root) samplingGraphBind(root);
    return;
  }
  var activeSubview = samplingState.activeSubview || "energy";
  var subviewContent = root.querySelector('.sampling-subview-content');
  if (subviewContent) {
    var data = energyGraphState.data;
    var series = Array.isArray(data.series) ? data.series : [];
    var currentSeries = series.find(function(s) { return s.id === samplingState.yAxis; }) || series[0];
    if (currentSeries) {
      data = Object.assign({}, data, { series: [currentSeries] });
    }
    if (activeSubview === "energy") {
      subviewContent.innerHTML = '<div class="sampling-axis-controls">' +
        samplingAxisSelectHtml(data, "x") + samplingAxisSelectHtml(data, "y") +
      '</div><div class="energy-chart-scroll" data-energy-chart-scroll>' + samplingEnergyTrajectorySvg(data) + '</div>';
    } else if (activeSubview === "space") {
      subviewContent.innerHTML = '<div class="energy-chart-scroll" data-energy-chart-scroll>' + samplingSpaceScatterSvg(data) + '</div>';
    } else {
      subviewContent.innerHTML = samplingCoverageMarkup(data);
    }
    subviewContent.setAttribute("data-sampling-subview", activeSubview);
  }
  var inspectorBody = root.querySelector(".energy-inspector-body");
  if (inspectorBody) inspectorBody.outerHTML = energyGraphInspectorMarkup(energyGraphState.data);
  samplingGraphBind(root);
}

function samplingAxisSelectHtml(data, axis) {
  var metadata = data.metadata || {};
  var axisOptions = metadata.axis_options || {};
  if (axis === "x") {
    var xOptions = Array.isArray(axisOptions.x) ? axisOptions.x : ["time_ps", "step", "frame"];
    var html = '<select class="sampling-axis-select" data-sampling-x-axis>';
    xOptions.forEach(function(opt) {
      html += '<option value="' + escapeHtml(opt) + '"' + (opt === samplingState.xAxis ? ' selected' : '') + '>' + escapeHtml(t("energy.sampling.axis." + opt)) + '</option>';
    });
    html += '</select>';
    return html;
  } else {
    var yOptions = Array.isArray(axisOptions.y) ? axisOptions.y : ["potential", "relative"];
    var html = '<select class="sampling-axis-select" data-sampling-y-axis>';
    yOptions.forEach(function(opt) {
      var seriesId = opt === "potential" ? "energy_potential" : "relative_energy";
      var seriesObj = (Array.isArray(data.series) ? data.series : []).find(function(s) { return s.id === seriesId; });
      var label = seriesObj ? seriesObj.label : opt;
      html += '<option value="' + escapeHtml(seriesId) + '"' + (seriesId === samplingState.yAxis ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
    });
    html += '</select>';
    return html;
  }
}

/* ── Sampling Graph Bind ── */
function samplingGraphBind(root) {
  if (!root) return;
  var data = energyGraphState.data;
  root.querySelectorAll('.sampling-subview-tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
      samplingState.activeSubview = tab.getAttribute('data-sampling-view');
      root.querySelectorAll('.sampling-subview-tab').forEach(function(t) { t.classList.remove('active'); });
      tab.classList.add('active');
      samplingGraphRenderCurrent();
    });
  });
  root.querySelectorAll('[data-sampling-x-axis]').forEach(function(select) {
    select.addEventListener('change', function() {
      samplingState.xAxis = select.value;
      samplingGraphRenderCurrent();
    });
  });
  root.querySelectorAll('[data-sampling-y-axis]').forEach(function(select) {
    select.addEventListener('change', function() {
      samplingState.yAxis = select.value;
      samplingGraphRenderCurrent();
    });
  });
  root.querySelectorAll('[data-sampling-switch-view]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var view = btn.getAttribute('data-sampling-switch-view');
      if (view && view !== energyGraphState.data.view_type) {
        renderEnergyGraphWorkspace(lastSelectedJob, { view: view });
      }
    });
  });
  root.querySelectorAll('[data-energy-frame]').forEach(function(el) {
    el.addEventListener('click', function(event) {
      event.stopPropagation();
      var frame = el.getAttribute('data-energy-frame');
      if (frame != null) {
        energyGraphSelectFrame(Number(frame));
      }
    });
  });
  var current = energyGraphCurrentNode(data);
  if (current && current.frame_index != null) energyGraphLoadStructure(current.frame_index);
}

function energyGraphRenderEmpty(message) {
  var screen = getEnergyViewerContainer();
  if (!screen) return;
  energyGraphRenderState("empty", message);
}

function energyGraphSetReloadDisabled(disabled) {
  var screen = getEnergyViewerContainer();
  if (!screen) return;
  screen.querySelectorAll('[data-energy-action="reload"]').forEach(function(btn) { btn.disabled = !!disabled; });
}

// A status tick is allowed to fetch a fresh snapshot, but a fresh snapshot is
// not automatically a reason to rebuild the workspace.  Prefer a server
// supplied revision; the fallback contains only graph inputs so volatile
// metadata cannot make the chart redraw on every poll.
function energyGraphDataRevision(data) {
  if (!data) return "";
  // Terminal status and manual review change editability without changing
  // scan frames. Keep them in the revision even when the server supplies one.
  var metadata = data.metadata || {};
  var stateRevision = JSON.stringify({
    status: data.status || "",
    job_status: metadata.job_status || "",
    review: metadata.review || {},
    scan_interrupted: !!metadata.scan_interrupted,
  });
  if (data.revision) return String(data.revision) + "|" + stateRevision;
  if (data.data_revision) return String(data.data_revision) + "|" + stateRevision;
  try {
    return JSON.stringify({
      view_type: data.view_type || "",
      title: data.title || "",
      default_series: data.default_series || "",
      x_axis: data.x_axis || {},
      nodes: Array.isArray(data.nodes) ? data.nodes : [],
      series: Array.isArray(data.series) ? data.series : [],
      annotations: Array.isArray(data.annotations) ? data.annotations : [],
      review: data.metadata && data.metadata.review ? data.metadata.review : {},
    });
  } catch (e) {
    // API responses are JSON values, so this is only a defensive fallback.
    return "unhashable:" + String(Date.now());
  }
}

function optimizationJobItemId(job) {
  if (!job) return "";
  var resources = job.resources || (job.spec && job.spec.resources) || {};
  return String(job.item_id || resources.item_id || "");
}

function energyIrcLatestNode(data) {
  var nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
  var reverse = nodes.filter(function(node) { return node.metadata && node.metadata.direction === "reverse"; });
  var forward = nodes.filter(function(node) { return node.metadata && node.metadata.direction === "forward"; });
  var directionNodes = reverse.length ? reverse : forward;
  return directionNodes.reduce(function(latest, node) {
    return !latest || Number(node.frame_index) > Number(latest.frame_index) ? node : latest;
  }, null);
}

async function renderEnergyGraphWorkspace(job, options) {
  options = options || {};
  var screen = getEnergyViewerContainer();
  if (!screen) return false;
  var jobId = jobIdOf(job);
  if (!jobId) {
    energyGraphState.data = null;
    energyGraphState.dataRevision = "";
    energyGraphState.status = "idle";
    energyGraphRenderState("idle");
    return false;
  }
  if (energyGraphState.abortController) { energyGraphState.abortController.abort(); energyGraphState.abortController = null; }
  var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
  energyGraphState.abortController = ac;
  var sameJob = !!energyGraphState.data && String(energyGraphState.jobId) === String(jobId);
  var wasReady = sameJob && energyGraphState.status === "ready" && !!screen.querySelector("[data-energy-workspace]");
  var previousRevision = energyGraphState.dataRevision || "";
  var token = ++energyGraphState.requestToken;
  if (!sameJob) {
    energyGraphState.itemId = "";
    energyGraphState.optimizationDomains = {};
    energyGraphState.optimizationPlot = null;
    energyGraphState.frameCandidates = { revision: 0, list: [] };
    energyGraphState.lastFetchedJobStatus = "";
  }
  energyGraphState.jobId = String(jobId);
  energyGraphState.jobStatus = String((job && job.status) || "").toLowerCase();
  energyGraphState.loading = true;
  // Silent polling must not turn a working view into a loading state.  That
  // state transition was the reason the old silent fast path was unreachable.
  if (!options.silent) energyGraphState.status = "loading";
  energyGraphSetReloadDisabled(true);
  if (!options.silent) energyGraphRenderState("loading", t("energy.loading"));
  try {
    // Batch jobs share one work dir across items: once the backend has told
    // us which trajectory item this job maps to, lock the identifier into
    // every later request so polling can never drift to a sibling item.
    var graphUrl = "/jobs/" + encodeURIComponent(jobId) + "/energy-graph";
    var lockedItemId = energyGraphState.itemId || optimizationJobItemId(job);
    if (lockedItemId) graphUrl += "?item_id=" + encodeURIComponent(lockedItemId);
    if (options.view) graphUrl += (lockedItemId ? "&" : "?") + "view=" + encodeURIComponent(options.view);
    var candPromise = energyGraphFetchFrameCandidates(jobId);
    var data = await api(graphUrl, ac ? { signal: ac.signal } : {});
    if (token !== energyGraphState.requestToken || activeViewerTab !== "energy") return false;
    var candPayload = await candPromise;
    if (token !== energyGraphState.requestToken || activeViewerTab !== "energy") return false;
    energyGraphState.lastFetchedJobStatus = String((job && job.status) || "").toLowerCase();
    // Restore server-authoritative saved-candidate state so badges survive
    // page refreshes, job re-selection and graph reloads.
    if (candPayload) energyGraphApplyFrameCandidates(candPayload);
    if (data && String(data.view_type || "") === "unsupported") {
      energyGraphState.data = data;
      energyGraphState.dataRevision = energyGraphDataRevision(data);
      energyGraphState.loading = false;
      energyGraphState.status = "empty";
      energyGraphRenderState("empty", t("energy.unsupported"));
      return true;
    }
    if (data && String(data.view_type || "") === "irc" && !(Array.isArray(data.nodes) && data.nodes.length) && !(Array.isArray(data.series) && data.series.length)) {
      energyGraphState.data = data;
      energyGraphState.dataRevision = energyGraphDataRevision(data);
      energyGraphState.loading = false;
      energyGraphState.status = "empty";
      var ircReason = String((data.metadata && data.metadata.reason) || "");
      var ircTerminal = ["completed", "failed", "cancelled"].indexOf(String(data.status || "").toLowerCase()) >= 0;
      var ircMessage = ircReason.indexOf("remote_fetch") === 0 ? t("energy.irc_remote_error") : ircTerminal ? t("energy.irc_missing") : t("energy.irc_pending");
      energyGraphRenderState("empty", ircMessage);
      return true;
    }
    if (data && String(data.view_type || "") === "scan" && !(Array.isArray(data.nodes) && data.nodes.length) && !(Array.isArray(data.series) && data.series.length) && data.metadata && data.metadata.reason === "pes_scan_pending") {
      energyGraphState.data = data;
      energyGraphState.dataRevision = energyGraphDataRevision(data);
      energyGraphState.loading = false;
      energyGraphState.status = "empty";
      energyGraphRenderState("empty", t("energy.pes_pending"));
      return true;
    }
    var nextRevision = energyGraphDataRevision(data);
    if (options.silent && wasReady && previousRevision === nextRevision) {
      // The task may still be polled for status, but unchanged graph data is a
      // strict no-op: do not replace SVG, inspector, or the 3D viewer.
      energyGraphState.loading = false;
      energyGraphState.status = "ready";
      energyGraphSetReloadDisabled(false);
      return true;
    }
    energyGraphState.data = data;
    energyGraphState.dataRevision = nextRevision;
    energyGraphState.layout = null;
    if (data && data.metadata && data.metadata.item_id) {
      energyGraphState.itemId = String(data.metadata.item_id);
    }
    if (String(data.view_type || "") === "optimization") {
      var optSeries = Array.isArray(data.series) ? data.series : [];
      if (!optSeries.some(function(item) { return String(item.id) === String(energyGraphState.energySeriesId); })) {
        energyGraphState.energySeriesId = String(data.default_series || (optSeries[0] && optSeries[0].id) || "");
      }
      var optNodes = Array.isArray(data.nodes) ? data.nodes : [];
      var selectedOpt = optNodes.find(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); });
      if (energyGraphState.liveFollow) {
        // Auto-follow the newest cycle: never counts as an explicit user pick.
        if (optNodes.length) energyGraphState.selectedNodeId = String(optNodes[optNodes.length - 1].id);
        energyGraphState.selectionOrigin = "follow";
      } else if (!selectedOpt) {
        // The user's picked cycle vanished from the refreshed trajectory:
        // fall back to the task-status mode and re-attach to the latest cycle.
        if (optNodes.length) energyGraphState.selectedNodeId = String(optNodes[optNodes.length - 1].id);
        energyGraphState.selectionOrigin = "follow";
        energyGraphState.liveFollow = true;
      }
      // Otherwise the user's pick survived the refresh: keep "user" as-is.
    }
    if (String(data.view_type || "") === "scan") {
      var scanNodes = Array.isArray(data.nodes) ? data.nodes : [];
      var selectedScan = scanNodes.find(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); });
      if (energyGraphState.liveFollow) {
        if (scanNodes.length) energyGraphState.selectedNodeId = String(scanNodes[scanNodes.length - 1].id);
        energyGraphState.selectionOrigin = "follow";
      } else if (!selectedScan) {
        if (scanNodes.length) energyGraphState.selectedNodeId = String(scanNodes[scanNodes.length - 1].id);
        energyGraphState.selectionOrigin = "follow";
        energyGraphState.liveFollow = true;
      }
    }
    if (String(data.view_type || "") === "irc") {
      var ircNodes = Array.isArray(data.nodes) ? data.nodes : [];
      var selectedIrc = ircNodes.find(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); });
      if (energyGraphState.liveFollow || !selectedIrc) {
        var latestIrc = energyIrcLatestNode(data);
        if (latestIrc) energyGraphState.selectedNodeId = String(latestIrc.id);
        energyGraphState.selectionOrigin = "follow";
      }
    }
    if (!energyGraphState.seriesId) {
      var allSeries = Array.isArray(data.series) ? data.series : [];
      var preferredSeries = allSeries.find(function(item) { return item.id === "relative_energy"; })
        || allSeries.find(function(item) { return String(item.unit || "").toLowerCase().indexOf("kcal") >= 0; });
      energyGraphState.seriesId = preferredSeries ? String(preferredSeries.id) : String(data.default_series || "");
    }
    var projectId = String(s2scanState.projectId || "");
    s2scanState.projectId = projectId;
    energyGraphSyncS2Selection(data);
    energyGraphState.loading = false;
    var hasNodes = Array.isArray(data.nodes) && data.nodes.length > 0;
    var hasSeries = Array.isArray(data.series) && data.series.length > 0;
    if (!hasNodes && !hasSeries) {
      energyGraphState.status = "empty";
      energyGraphRenderState("empty", t("energy.empty"));
    } else {
      energyGraphState.status = "ready";
      energyGraphRenderCurrent();
    }
    return true;
  } catch (e) {
    if (e && e.name === "AbortError") return false;
    if (token !== energyGraphState.requestToken || activeViewerTab !== "energy") return false;
    energyGraphState.loading = false;
    if (options.silent && energyGraphState.data) {
      energyGraphSetReloadDisabled(false);
      return false;
    }
    energyGraphState.status = "error";
    energyGraphState.data = null;
    energyGraphState.dataRevision = "";
    energyGraphRenderState("error", e.message || t("energy.error"));
    return false;
  }
}

function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function normalizeMechanismQuality(value) {
  const normalized = String(value || "").toLowerCase();
  return normalized === "high" || normalized === "medium" ? normalized : "";
}

function normalizeMechanismFidelity(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return /^s\d+$/.test(normalized) ? normalized : "";
}

function extractMechanismSummary(report, detail) {
  const sources = [
    report && report.mechanism_profile,
    report && report.reaction_network,
    report && report.stationary_points,
    report && report.quality_gates,
    report && report.provenance,
    detail && detail.study,
    detail && detail.study_json,
  ];
  const summary = {
    quality: "",
    effectiveFidelity: "",
    highFidelity: null,
    hasHighFidelityField: false,
  };
  sources.forEach(function(source) {
    if (!isPlainObject(source)) return;
    if (!summary.quality) summary.quality = normalizeMechanismQuality(source.quality);
    if (!summary.effectiveFidelity) {
      summary.effectiveFidelity = normalizeMechanismFidelity(source.effective_fidelity || source.effectiveFidelity);
    }
    if (Object.prototype.hasOwnProperty.call(source, "high_fidelity")) {
      summary.hasHighFidelityField = true;
      if (isPlainObject(source.high_fidelity)) summary.highFidelity = source.high_fidelity;
    }
    const metadata = isPlainObject(source.metadata) ? source.metadata : null;
    if (metadata && Object.prototype.hasOwnProperty.call(metadata, "high_fidelity")) {
      summary.hasHighFidelityField = true;
      if (!summary.highFidelity && isPlainObject(metadata.high_fidelity)) {
        summary.highFidelity = metadata.high_fidelity;
      }
    }
  });
  return summary;
}

function getMechanismS4Status(summary) {
  if (!summary || !summary.hasHighFidelityField) return "";
  if (!summary.highFidelity) return "not_run";
  return summary.quality === "high" ? "done" : "partial";
}

function buildMechanismSummaryMarkup(report, detail, studyId) {
  const summary = extractMechanismSummary(report, detail);
  const chips = [];
  // Phase E: unified study status chip first (from cached detail/list data).
  const unifiedStatus = getMechanismUnifiedStatus(studyId);
  if (unifiedStatus) {
    chips.push('<span class="badge ' + getStudyStatusClass(unifiedStatus) + '" title="' + escapeHtml(String(unifiedStatus)) + '">' +
      escapeHtml(translateStudyStatus(unifiedStatus)) + '</span>');
  }
  if (summary.quality) {
    chips.push('<span class="badge ' + escapeHtml(summary.quality) + '">' + escapeHtml(summary.quality.toUpperCase()) + '</span>');
  }
  if (summary.effectiveFidelity) {
    chips.push('<span class="viewer-mechanism-chip"><span>' + escapeHtml(t("mech.summary.fidelity")) + '</span><strong>' + escapeHtml(summary.effectiveFidelity) + '</strong></span>');
  }
  const s4Status = getMechanismS4Status(summary);
  if (s4Status) {
    chips.push('<span class="viewer-mechanism-chip"><span>' + escapeHtml(t("mech.summary.s4")) + '</span>' +
      '<span class="badge s4_' + escapeHtml(s4Status) + '">' + escapeHtml(t("mech.summary.s4_" + s4Status)) + '</span></span>');
  }
  if (!chips.length) return "";
  return '<div class="viewer-mechanism-summary">' + chips.join("") + '</div>';
}

function buildMechanismPanelMarkup(kind, title, meta, svg, detail, emptyText, summaryHtml) {
  if (!svg) return "";
  return '<div class="viewer-mechanism-panel" data-kind="' + escapeHtml(kind) + '">' +
    '<div class="viewer-mechanism-head"><div class="viewer-mechanism-head-main"><div class="viewer-mechanism-title">' + escapeHtml(title) + '</div><div class="viewer-mechanism-meta">' + escapeHtml(meta) + '</div></div>' + (summaryHtml || '') + '</div>' +
    '<div class="viewer-mechanism-canvas">' + svg + '</div>' +
    '<div class="viewer-mechanism-detail">' + escapeHtml(detail || emptyText || '') + '</div>' +
    '</div>';
}

function renderMechanismPlaceholder() {
  const mech = getMechanismViewerContainer();
  if (!mech) return;
  mech.innerHTML = "";
  mech.style.display = "none";
}

function bindMechanismViewerInteractions() {
  const mech = getMechanismViewerContainer();
  if (!mech) return;
  mech.querySelectorAll("[data-edge-index]").forEach(function(el) {
    el.addEventListener("click", function() {
      const index = parseInt(el.getAttribute("data-edge-index"), 10);
      const report = mechanismStudyReportsById[mechanismViewerState.studyId] || null;
      const network = report && report.reaction_network ? report.reaction_network : null;
      const edge = network && Array.isArray(network.edges) ? network.edges[index] : null;
      if (!edge) return;
      mechanismViewerState.pathDetail = t("mech.network.edge_detail", {
        stepId: edge.step_id || "step",
        fidelity: edge.fidelity || t("common.none"),
        strategy: edge.path_strategy || t("common.none"),
        status: translateStatus(edge.status || t("common.unknown")),
      });
      const detail = mech.querySelector(".viewer-mechanism-detail");
      if (detail) detail.textContent = mechanismViewerState.pathDetail;
    });
  });
}

function renderMechanismTabFromCache(tab) {
  const mech = getMechanismViewerContainer();
  if (!mech) return false;
  const jobId = mechanismViewerState.jobId;
  const studyId = mechanismViewerState.studyId;
  const studies = mechanismStudiesByJob[String(jobId || "")] || [];
  const report = mechanismStudyReportsById[String(studyId || "")];
  const detail = mechanismStudyDetailsById[String(studyId || "")] || null;
  const summaryHtml = buildMechanismSummaryMarkup(report, detail, studyId);
  if (!jobId || !studies.length || !report) return false;
  if (tab === "path") {
    const svg = buildReactionNetworkSvg(report);
    if (!svg) return false;
    mech.innerHTML = buildMechanismPanelMarkup("path", t("mech.network.title"), t("mech.network.meta", {
      states: report.reaction_network && report.reaction_network.nodes ? report.reaction_network.nodes.length : 0,
      edges: report.reaction_network && report.reaction_network.edges ? report.reaction_network.edges.length : 0,
    }), svg, mechanismViewerState.pathDetail || t("review.detail_hint"), t("mech.network.empty"), summaryHtml);
    mech.style.display = "block";
    bindMechanismViewerInteractions();
    return true;
  }
  if (tab === "energy") {
    const svg = buildEnergyProfileSvg(report);
    if (!svg) return false;
    const routeCount = report.mechanism_profile && report.mechanism_profile.routes ? report.mechanism_profile.routes.length : 0;
    mech.innerHTML = buildMechanismPanelMarkup("energy", t("mech.energy.title"), t("mech.energy.meta", { routes: routeCount }), svg, mechanismViewerState.energyDetail || t("mech.energy.axis_y"), t("mech.energy.empty"), summaryHtml);
    mech.style.display = "block";
    return true;
  }
  return false;
}

async function ensureMechanismViewerData(job) {
  if (!job || (job.spec && job.spec.workflow) !== "mechanism") return false;
  const jobId = jobIdOf(job);
  if (!jobId) return false;
  const studies = await fetchMechanismStudiesForJob(jobId);
  if (!studies.length) return false;
  const primary = getPrimaryMechanismStudy(jobId);
  if (!primary || !primary.id) return false;
  mechanismViewerState.jobId = jobId;
  mechanismViewerState.studyId = String(primary.id);
  await fetchMechanismStudyDetail(primary.id);
  await fetchMechanismStudyReport(primary.id);
  return true;
}

async function renderMechanismTab(job, tab) {
  if (!job || (job.spec && job.spec.workflow) !== "mechanism") return false;
  const ready = await ensureMechanismViewerData(job);
  if (!ready) return false;
  return renderMechanismTabFromCache(tab);
}

// Retained for the SR display path, which still calls this legacy hook.
function updateSrPickButtons() { /* retired */ }

