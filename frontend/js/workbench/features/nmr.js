// ── NMR report viewer (DevDoc §7 / §11.1, P2) ──────────────────────────
// Renders nmr_report.json for the selected nmr job: winner card, candidate
// ranking (DP4/DP5 bars), per-candidate structure/quality/assignment tables,
// the experimental-peak ledger, and conformer detail rows. Fetches the report
// via the existing job-files content API so no new backend endpoint is
// required.
//
// T40: the panel links three surfaces — structure atoms (3D pick + chips),
// assignment rows, and experimental peaks — through one selection state
// (nmrPanelState) applied by nmrPanelApplySelection(). Structure geometry is
// read from the conformer stage's input.xyz through the existing files
// endpoints; viewer loading/framing reuses the shared structure-viewer
// conventions (registerCanvasLoader + sharedLoadGeometry +
// scheduleViewerFraming). This panel never hand-rolls a trajectory/view
// projection (root AGENTS ANTI-PATTERN 21).

async function refreshNmrReport(job) {
  var panel = document.getElementById("nmr-report-panel");
  var body = document.getElementById("nmr-report-body");
  if (!panel || !body) return;
  var wf = job && job.spec && job.spec.workflow;
  if (wf !== "nmr") { panel.style.display = "none"; body.innerHTML = ""; nmrPanelDispose(); return; }
  panel.style.display = "block";
  body.innerHTML = '<span class="nmr-empty">' + escapeHtml(t("nmr.loading") || "Loading…") + '</span>';
  try {
    var report = await fetchNmrReportJson(job);
    if (!report) {
      body.innerHTML = '<span class="nmr-empty">' + escapeHtml(t("nmr.no_report") || "No nmr_report.json yet") + '</span>';
      return;
    }
    var token = nmrPanelDispose();
    body.innerHTML = "";
    body.appendChild(renderNmrWinner(report));
    if (report.schema_version == null) {
      // T24 contract: absent schema_version = historical v1 report whose
      // validation status is unknown — render the explicit note, never an
      // implicit upgrade.
      var legacyNote = document.createElement("div");
      legacyNote.className = "nmr-note";
      legacyNote.textContent = t("nmr.legacy_note");
      body.appendChild(legacyNote);
    }
    body.appendChild(renderNmrPeaks(report));
    var candidates = report.candidates || [];
    candidates.forEach(function(cand, ordinal) {
      body.appendChild(renderNmrCandidate(cand, report, ordinal));
    });
    if (report.note) {
      var note = document.createElement("div");
      note.className = "nmr-note";
      note.textContent = report.note;
      body.appendChild(note);
    }
    nmrLoadStructureViewers(job, candidates, token);
  } catch (e) {
    console.error("NMR report render failed:", e);
    body.innerHTML = '<span class="nmr-empty">' + escapeHtml(t("nmr.error") || "Failed to load report") + '</span>';
  }
}

// Unified NMR report reader (todo 23): one service for LOCAL and REMOTE jobs.
// Probe order (first hit wins):
//   1. reports/nmr_report.json        — the todo-22 result-manifest spelling
//   2. RESULT/reports/nmr_report.json — the same file work_dir-rooted, which is
//      what /jobs/{id}/files/{path} and the remote fetcher actually serve
//   3. nmr_report.json                — legacy root layout
// Remote unwrap contract (see acp.api.v1_routes._parse_remote_report):
// .../remote-files/<path>/preview?mode=report returns content = the envelope
// {type: "json_report", file_path, report} and the reader unwraps content.report;
// a plain string content (mode=text envelope) is tolerated and JSON-parsed.
var NMR_REPORT_PATHS = [
  "reports/nmr_report.json",
  "RESULT/reports/nmr_report.json",
  "nmr_report.json",
];

async function fetchNmrReportJson(job) {
  for (var i = 0; i < NMR_REPORT_PATHS.length; i++) {
    var report = await fetchNmrReportFromPath(job, NMR_REPORT_PATHS[i]);
    if (report) return report;
  }
  return null;
}

async function fetchNmrReportFromPath(job, path) {
  var jobId = job.id;
  var remote = !!jobRemoteNodeName(job);
  var encoded = path.split("/").map(encodeURIComponent).join("/");
  var url = remote
    ? "/jobs/" + encodeURIComponent(jobId) + "/remote-files/" + encoded + "/preview?mode=report"
    : "/jobs/" + encodeURIComponent(jobId) + "/files/" + encoded;
  var resp;
  try {
    resp = jobRemoteNodeName(job) ? await apiRemote(url) : await api(url);
  } catch (e) {
    return null;
  }
  return remote ? unwrapNmrPreviewReport(resp) : unwrapNmrFileReport(resp);
}

function unwrapNmrFileReport(resp) {
  if (typeof resp === "string") { try { resp = JSON.parse(resp); } catch (e) { return null; } }
  return resp && typeof resp === "object" ? resp : null;
}

function unwrapNmrPreviewReport(resp) {
  if (typeof resp === "string") { try { resp = JSON.parse(resp); } catch (e) { return null; } }
  if (!resp || typeof resp !== "object") return null;
  var content = resp.content;
  if (typeof content === "string") { try { content = JSON.parse(content); } catch (e) { return null; } }
  if (!content || typeof content !== "object") return null;
  if (content.type === "json_report") {
    return (content.report && typeof content.report === "object") ? content.report : null;
  }
  if (content.type === "unknown_report") return null;
  return content;
}

// Null-display contract (gap G13): missing and non-finite numerics render the
// em dash held by NMR_NULL_DISPLAY — never as 0. Winner probabilities,
// regression values and boltzmann weights all route through these helpers.
var NMR_NULL_DISPLAY = "—";
function nmrDisplayNumber(value, digits) {
  if (value == null) return NMR_NULL_DISPLAY;
  var n = Number(value);
  return isFinite(n) ? n.toFixed(digits) : NMR_NULL_DISPLAY;
}
function nmrProbability(value) {
  if (value == null) return null;
  var n = Number(value);
  return isFinite(n) ? n : null;
}

function renderNmrWinner(report) {
  var wrap = document.createElement("div");
  var summary = report.summary || {};
  var winner = summary.winner;
  wrap.className = "nmr-winner";
  var label = document.createElement("div");
  label.className = "nmr-winner-label";
  label.textContent = t("nmr.winner") || "Winner";
  wrap.appendChild(label);
  var name = document.createElement("div");
  name.className = "nmr-winner-name";
  name.textContent = winner ? (winner.label || ("candidate_" + winner.index)) : (t("common.none") || "—");
  wrap.appendChild(name);
  if (winner) {
    var probs = document.createElement("div");
    probs.className = "nmr-winner-probs";
    probs.textContent = "DP4 = " + nmrDisplayNumber(winner.dp4, 3) + "   ·   DP5 = " + nmrDisplayNumber(winner.dp5, 3);
    wrap.appendChild(probs);
  }
  var nuclei = (summary.nuclei || []).join(", ");
  if (nuclei) {
    var meta = document.createElement("div");
    meta.className = "nmr-cand-meta";
    meta.textContent = "nuclei: " + nuclei + "   ·   candidates: " + (summary.n_candidates != null ? summary.n_candidates : (report.candidates || []).length);
    wrap.appendChild(meta);
  }
  return wrap;
}

function renderNmrCandidate(cand, report, ordinal) {
  var wrap = document.createElement("div");
  var summary = report.summary || {};
  var isWinner = summary.winner && String(summary.winner.index) === String(cand.index);
  wrap.className = "nmr-cand" + (isWinner ? " winner" : "");
  wrap.setAttribute("data-nmr-candidate", String(cand.index));
  var head = document.createElement("div");
  head.className = "nmr-cand-head";
  var name = document.createElement("span");
  name.className = "nmr-cand-name";
  name.textContent = cand.label || ("candidate_" + cand.index);
  head.appendChild(name);
  var meta = document.createElement("span");
  meta.className = "nmr-cand-meta";
  meta.textContent = "conf=" + (cand.n_conformers != null ? cand.n_conformers : "?");
  head.appendChild(meta);
  wrap.appendChild(head);

  // DP4 / DP5 probability bars (null probability → no bar, em dash value)
  var dp4 = nmrProbability(cand.dp4_probability);
  var dp5 = nmrProbability(cand.dp5_probability);
  [["DP4", dp4, ""], ["DP5", dp5, "dp5"]].forEach(function(pair) {
    var row = document.createElement("div");
    row.className = "nmr-prob-row";
    var lab = document.createElement("span");
    lab.className = "nmr-prob-label";
    lab.textContent = pair[0];
    row.appendChild(lab);
    var barWrap = document.createElement("span");
    barWrap.className = "nmr-prob-bar-wrap";
    var bar = document.createElement("span");
    bar.className = "nmr-prob-bar" + (pair[2] ? " " + pair[2] : "");
    bar.style.width = (pair[1] == null ? 0 : Math.min(100, Math.max(0, pair[1] * 100))) + "%";
    barWrap.appendChild(bar);
    row.appendChild(barWrap);
    var val = document.createElement("span");
    val.className = "nmr-prob-val";
    val.textContent = pair[1] == null ? NMR_NULL_DISPLAY : pair[1].toFixed(3);
    row.appendChild(val);
    wrap.appendChild(row);
  });

  // Per-candidate quality: evidence/coverage, calibrated probability state
  // (cand.probability) and T39 risk indicators (cand.diagnostics) rendered
  // as two visually distinct, clearly labelled lines.
  wrap.appendChild(renderNmrCandidateQuality(cand, report));
  wrap.appendChild(renderNmrStructure(cand, ordinal));

  // toggle: assignment table vs conformer details
  var actions = document.createElement("div");
  actions.className = "nmr-cand-actions";
  var btnAssign = document.createElement("button");
  btnAssign.className = "nmr-btn active";
  btnAssign.textContent = t("nmr.assignment") || "Assignment";
  var btnConf = document.createElement("button");
  btnConf.className = "nmr-btn";
  btnConf.textContent = t("nmr.conformers") || "Conformers";
  actions.appendChild(btnAssign);
  actions.appendChild(btnConf);
  wrap.appendChild(actions);

  var detailWrap = document.createElement("div");
  detailWrap.className = "nmr-table-wrap";
  wrap.appendChild(detailWrap);
  renderNmrAssignTable(detailWrap, cand);
  btnAssign.addEventListener("click", function() {
    btnAssign.classList.add("active"); btnConf.classList.remove("active");
    detailWrap.innerHTML = ""; renderNmrAssignTable(detailWrap, cand);
    nmrPanelApplySelection();
  });
  btnConf.addEventListener("click", function() {
    btnConf.classList.add("active"); btnAssign.classList.remove("active");
    detailWrap.innerHTML = ""; renderNmrConformerTable(detailWrap, cand);
    nmrPanelApplySelection();
  });
  return wrap;
}

function renderNmrAssignTable(container, cand) {
  var assigns = cand.assignment || [];
  if (!assigns.length) { container.textContent = t("nmr.no_data") || "No assignment data"; return; }
  var table = document.createElement("table");
  table.className = "nmr-table";
  var thead = document.createElement("thead");
  var tr = document.createElement("tr");
  ["atom", "element", "exp_ppm", "calc_ppm", "residual", "obs"].forEach(function(h) {
    var th = document.createElement("th");
    th.textContent = t("nmr.col_" + h) || h;
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  table.appendChild(thead);
  var tbody = document.createElement("tbody");
  assigns.forEach(function(a) {
    var row = document.createElement("tr");
    row.className = "nmr-assign-row";
    row.setAttribute("data-nmr-assign-row", "");
    row.setAttribute("data-nmr-candidate", String(cand.index));
    if (a.atom != null) row.setAttribute("data-nmr-atom", String(a.atom));
    if (a.observation_id != null) row.setAttribute("data-nmr-observation", String(a.observation_id));
    [a.atom, a.element, a.exp_ppm, a.calc_ppm, a.residual, a.observation_id].forEach(function(v) {
      var td = document.createElement("td");
      if (typeof v === "number") td.className = "nmr-resid";
      td.textContent = (typeof v === "number") ? nmrDisplayNumber(v, 3) : (v == null ? NMR_NULL_DISPLAY : String(v));
      row.appendChild(td);
    });
    row.addEventListener("mouseenter", function() { nmrSelectLink(a.atom, a.observation_id, cand.index); });
    row.addEventListener("click", function() { nmrSelectLink(a.atom, a.observation_id, cand.index); });
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  container.appendChild(table);
  var reg = cand.regression || {};
  var nuclei = Object.keys(reg);
  if (nuclei.length) {
    var regLine = document.createElement("div");
    regLine.className = "nmr-cand-meta";
    regLine.style.marginTop = "6px";
    regLine.textContent = nuclei.map(function(n) {
      var r = reg[n] || {};
      return n + ": slope=" + nmrDisplayNumber(r.slope, 3) + ", R²=" + nmrDisplayNumber(r.r_squared, 3) + ", MAE=" + nmrDisplayNumber(r.mae, 3);
    }).join("   ·   ");
    container.appendChild(regLine);
  }
}

function renderNmrConformerTable(container, cand) {
  var confs = cand.conformers || [];
  if (!confs.length) { container.textContent = t("nmr.no_data") || "No conformer data"; return; }
  var table = document.createElement("table");
  table.className = "nmr-table";
  var thead = document.createElement("thead");
  var tr = document.createElement("tr");
  ["id", "weight"].forEach(function(h) {
    var th = document.createElement("th");
    th.textContent = t("nmr.col_" + h) || h;
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  table.appendChild(thead);
  var tbody = document.createElement("tbody");
  confs.forEach(function(c) {
    var row = document.createElement("tr");
    var tdId = document.createElement("td");
    tdId.textContent = String(c.id != null ? c.id : "?");
    row.appendChild(tdId);
    var tdW = document.createElement("td");
    tdW.className = "nmr-resid";
    tdW.textContent = nmrDisplayNumber(c.boltzmann_weight, 4);
    row.appendChild(tdW);
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

// ── T40 linkage engine: structure atoms ↔ assignment rows ↔ peaks ──────
// One selection state (nmrPanelState) drives all three surfaces. Structure
// geometry is read-only from the conformer stage's input.xyz through the
// existing files endpoints; viewer loading/framing goes through the shared
// structure-viewer conventions (registerCanvasLoader + sharedLoadGeometry +
// scheduleViewerFraming). Risk indicators (diagnostics) and calibrated
// probabilities (probability) are rendered by separate helpers and never
// mixed.

//: WebGL contexts are a scarce browser resource; candidates beyond this cap
//: keep the atom-chip surface without a 3D canvas.
var NMR_STRUCTURE_VIEWER_LIMIT = 8;

var nmrPanelState = {
  token: 0,
  selectedAtom: null,
  selectedObservation: null,
  selectedCandidate: null,
  loadingCandidate: null,
  highlightAtoms: {},
  viewers: {},
};

if (window.ACPStructureViewer && typeof window.ACPStructureViewer.registerCanvasLoader === "function") {
  window.ACPStructureViewer.registerCanvasLoader("nmr-structure", function(xyzText, styleSpec) {
    var info = nmrPanelState.viewers[nmrPanelState.loadingCandidate];
    if (!info || !info.viewer) return false;
    try { info.viewer.removeAllModels(); } catch (e) {}
    info.viewer.addModel(xyzText, "xyz");
    info.viewer.setStyle({}, styleSpec);
    return true;
  });
}

function nmrPanelDispose() {
  nmrPanelState.token += 1;
  Object.keys(nmrPanelState.viewers).forEach(function(key) {
    var info = nmrPanelState.viewers[key];
    if (info && info.framing) disposeViewerFraming(info.framing);
    if (info && info.viewer) ACPVisualization.mol3d.destroy(info.viewer);
  });
  nmrPanelState.viewers = {};
  nmrPanelState.selectedAtom = null;
  nmrPanelState.selectedObservation = null;
  nmrPanelState.selectedCandidate = null;
  nmrPanelState.highlightAtoms = {};
  return nmrPanelState.token;
}

function nmrSelectLink(atomLabel, observationId, candIndex) {
  nmrPanelState.selectedAtom = (atomLabel == null || atomLabel === "") ? null : String(atomLabel);
  nmrPanelState.selectedObservation = (observationId == null || observationId === "") ? null : String(observationId);
  nmrPanelState.selectedCandidate = candIndex == null ? null : candIndex;
  nmrPanelApplySelection();
}

function nmrSelectAtom(atomLabel, candIndex) {
  nmrSelectLink(atomLabel, null, candIndex);
}

function nmrSelectObservation(observationId) {
  nmrSelectLink(null, observationId, null);
}

function nmrClearLinkage() {
  nmrSelectLink(null, null, null);
}

function nmrPanelApplySelection() {
  var root = document.getElementById("nmr-report-body");
  if (!root) return;
  var atom = nmrPanelState.selectedAtom;
  var obs = nmrPanelState.selectedObservation;
  var relatedAtoms = {};
  if (atom != null) relatedAtoms[atom] = true;
  root.querySelectorAll("[data-nmr-observation]").forEach(function(el) {
    if (obs == null || el.getAttribute("data-nmr-observation") !== obs) return;
    (el.getAttribute("data-nmr-claim-atoms") || "").split(",").forEach(function(label) {
      if (label) relatedAtoms[label] = true;
    });
    var own = el.getAttribute("data-nmr-atom");
    if (own) relatedAtoms[own] = true;
  });
  root.querySelectorAll("[data-nmr-atom]").forEach(function(el) {
    var label = el.getAttribute("data-nmr-atom") || "";
    var direct = atom != null && label === atom;
    el.classList.toggle("active", direct);
    el.classList.toggle("related", !direct && relatedAtoms[label] === true);
  });
  root.querySelectorAll("[data-nmr-observation]").forEach(function(el) {
    var obsHit = obs != null && el.getAttribute("data-nmr-observation") === obs;
    var label = el.getAttribute("data-nmr-atom");
    var atomHit = label != null && atom != null && label === atom;
    var claimHit = false;
    if (!obsHit && !atomHit && atom != null) {
      (el.getAttribute("data-nmr-claim-atoms") || "").split(",").forEach(function(c) {
        if (c === atom) claimHit = true;
      });
    }
    el.classList.toggle("active", obsHit || atomHit);
    el.classList.toggle("related", !obsHit && !atomHit && claimHit);
  });
  nmrHighlightAtoms(relatedAtoms, atom);
}

function nmrHighlightAtoms(relatedAtoms, activeAtom) {
  nmrPanelState.highlightAtoms = relatedAtoms || {};
  Object.keys(nmrPanelState.viewers).forEach(function(key) {
    var info = nmrPanelState.viewers[key];
    if (!info || !info.viewer) return;
    try { info.viewer.removeAllShapes(); } catch (e) { return; }
    var labels = info.labels || [];
    var atoms = info.atoms || [];
    labels.forEach(function(label, index) {
      if (relatedAtoms[label] !== true || !atoms[index]) return;
      var highlight = activeAtom != null && label === activeAtom;
      try {
        info.viewer.addSphere({
          center: { x: atoms[index].x, y: atoms[index].y, z: atoms[index].z },
          radius: 0.38,
          color: highlight ? "#4ea1ff" : "#f5a623",
          opacity: 0.85
        });
      } catch (e) {}
    });
    try { info.viewer.render(); } catch (e) {}
  });
}

function nmrAtomLabelsFromSymbols(symbols) {
  var counters = {};
  var labels = [];
  (symbols || []).forEach(function(symbol) {
    var sym = String(symbol == null ? "" : symbol);
    if (!counters[sym]) counters[sym] = 0;
    counters[sym] += 1;
    labels.push(sym + counters[sym]);
  });
  return labels;
}

function nmrCandidateAtomLabels(cand) {
  var seen = {};
  var labels = [];
  function push(label) {
    if (label == null || label === "") return;
    var key = String(label);
    if (seen[key]) return;
    seen[key] = true;
    labels.push(key);
  }
  (cand.assignment || []).forEach(function(a) { if (a) push(a.atom); });
  var diag = cand.diagnostics || {};
  (diag.atom_support || []).forEach(function(rec) { if (rec) push(rec.atom_label); });
  (diag.signals || []).forEach(function(rec) { if (rec) push(rec.atom_label); });
  return labels;
}

//: Risk indicators ONLY (T39 diagnostics namespace): finite |z| maximum,
//: out-of-domain atom-support count, and per-candidate leave-one-signal
//: winner flips. Calibrated probabilities live in cand.probability and are
//: rendered elsewhere — never here.
function nmrRiskSummary(diagnostics) {
  var diag = diagnostics || {};
  var signals = diag.signals || [];
  var maxAbsZ = null;
  signals.forEach(function(record) {
    if (!record || record.z_score == null) return;
    var z = Number(record.z_score);
    if (!isFinite(z)) return;
    var abs = Math.abs(z);
    if (maxAbsZ == null || abs > maxAbsZ) maxAbsZ = abs;
  });
  var oodAtoms = 0;
  (diag.atom_support || []).forEach(function(record) {
    if (record && record.out_of_domain === true) oodAtoms += 1;
  });
  var looFlips = 0;
  (diag.leave_one_signal_out || []).forEach(function(record) {
    if (record && record.winner_changed === true) looFlips += 1;
  });
  return { nSignals: signals.length, maxAbsZ: maxAbsZ, oodAtoms: oodAtoms, looFlips: looFlips };
}

function nmrPeakClaims(report) {
  var diag = report && report.diagnostics;
  var claims = (diag && diag.conflict_matrix && diag.conflict_matrix.claims) || [];
  var rows = [];
  claims.forEach(function(claim) {
    if (!claim || claim.observation_id == null) return;
    rows.push({
      observation_id: String(claim.observation_id),
      nucleus: claim.nucleus == null ? "" : String(claim.nucleus),
      exp_ppm: claim.exp_ppm,
      atom_labels: (claim.atom_labels || []).slice(),
      candidate_indices: (claim.candidate_indices || []).slice(),
      n_claims: claim.n_claims,
      conflict: claim.conflict === true
    });
  });
  if (!rows.length) rows = nmrPeakClaimsFromAssignments(report);
  return rows;
}

function nmrPeakClaimsFromAssignments(report) {
  var byId = {};
  var order = [];
  ((report && report.candidates) || []).forEach(function(cand) {
    ((cand && cand.assignment) || []).forEach(function(a) {
      if (!a || a.observation_id == null) return;
      var id = String(a.observation_id);
      if (!byId[id]) {
        byId[id] = {
          observation_id: id,
          nucleus: a.element == null ? "" : String(a.element),
          exp_ppm: a.exp_ppm,
          atom_labels: [],
          candidate_indices: [],
          n_claims: 0,
          conflict: false
        };
        order.push(id);
      }
      var row = byId[id];
      if (row.atom_labels.indexOf(a.atom) < 0) row.atom_labels.push(a.atom);
      if (row.candidate_indices.indexOf(cand.index) < 0) row.candidate_indices.push(cand.index);
    });
  });
  order.forEach(function(id) { byId[id].n_claims = byId[id].atom_labels.length; });
  return order.map(function(id) { return byId[id]; });
}

function nmrI18nLabel(prefix, value) {
  var raw = String(value == null ? "" : value).trim().toLowerCase();
  if (!raw) return NMR_NULL_DISPLAY;
  var key = prefix + raw;
  var label = t(key);
  return label === key ? raw : label;
}

function renderNmrCandidateQuality(cand, report) {
  var wrap = document.createElement("div");
  wrap.className = "nmr-quality";
  var coverage = cand.coverage || {};
  var evidence = cand.evidence || {};
  var status = cand.analysis_status != null ? cand.analysis_status : evidence.status;
  var head = document.createElement("div");
  head.className = "nmr-quality-head";
  head.textContent = t("nmr.quality.title");
  wrap.appendChild(head);
  var evidenceLine = document.createElement("div");
  evidenceLine.className = "nmr-quality-line";
  evidenceLine.textContent = t("nmr.quality.evidence") + ": " + nmrI18nLabel("nmr.quality.status.", status)
    + "   ·   " + t("nmr.quality.coverage") + ": "
    + nmrDisplayNumber(coverage.matched_signals, 0) + " / " + nmrDisplayNumber(coverage.expected_signals, 0);
  wrap.appendChild(evidenceLine);
  var prob = cand.probability || {};
  var dp4 = prob.dp4 || {};
  var dp5 = prob.dp5 || {};
  var calibrated = document.createElement("div");
  calibrated.className = "nmr-quality-line nmr-quality-calibrated";
  calibrated.textContent = t("nmr.quality.calibrated") + ": DP4 " + nmrI18nLabel("nmr.quality.status.", dp4.status)
    + " (" + nmrI18nLabel("nmr.quality.calibration.", dp4.calibration_status) + ")"
    + "   ·   DP5 " + nmrI18nLabel("nmr.quality.status.", dp5.status)
    + " (" + nmrI18nLabel("nmr.quality.calibration.", dp5.calibration_status) + ")";
  wrap.appendChild(calibrated);
  var risk = nmrRiskSummary(cand.diagnostics);
  var riskLine = document.createElement("div");
  riskLine.className = "nmr-quality-line nmr-quality-risk";
  riskLine.title = t("nmr.quality.risk_note");
  riskLine.textContent = t("nmr.quality.risk") + ": "
    + t("nmr.quality.max_z") + " " + (risk.maxAbsZ == null ? NMR_NULL_DISPLAY : risk.maxAbsZ.toFixed(2))
    + "   ·   " + t("nmr.quality.ood_atoms") + " " + String(risk.oodAtoms)
    + "   ·   " + t("nmr.quality.loo_flips") + " " + String(risk.looFlips)
    + (risk.nSignals ? "" : "   ·   " + t("nmr.quality.no_diagnostics"));
  wrap.appendChild(riskLine);
  return wrap;
}

function renderNmrStructure(cand, ordinal) {
  var wrap = document.createElement("div");
  wrap.className = "nmr-structure";
  if (ordinal < NMR_STRUCTURE_VIEWER_LIMIT) {
    var viewer = document.createElement("div");
    viewer.className = "nmr-structure-viewer empty";
    viewer.setAttribute("data-nmr-structure-viewer", String(cand.index));
    viewer.textContent = t("nmr.structure.loading");
    wrap.appendChild(viewer);
  }
  var chips = document.createElement("div");
  chips.className = "nmr-atom-chips";
  chips.title = t("nmr.atoms.hint");
  var labels = nmrCandidateAtomLabels(cand);
  if (!labels.length) {
    var empty = document.createElement("span");
    empty.className = "nmr-cand-meta";
    empty.textContent = t("nmr.atoms.no_atoms");
    chips.appendChild(empty);
  }
  labels.forEach(function(label) {
    var chip = document.createElement("span");
    chip.className = "nmr-atom-chip";
    chip.setAttribute("data-nmr-atom", label);
    chip.setAttribute("data-nmr-candidate", String(cand.index));
    chip.textContent = label;
    chip.addEventListener("mouseenter", function() { nmrSelectAtom(label, cand.index); });
    chip.addEventListener("click", function() { nmrSelectAtom(label, cand.index); });
    chips.appendChild(chip);
  });
  wrap.appendChild(chips);
  return wrap;
}

function renderNmrPeaks(report) {
  var wrap = document.createElement("div");
  wrap.className = "nmr-peaks";
  var head = document.createElement("div");
  head.className = "nmr-linkage-bar";
  var title = document.createElement("span");
  title.className = "nmr-quality-head";
  title.textContent = t("nmr.peaks.title");
  head.appendChild(title);
  var clearBtn = document.createElement("button");
  clearBtn.className = "nmr-btn";
  clearBtn.setAttribute("data-nmr-linkage-clear", "");
  clearBtn.textContent = t("nmr.linkage.clear");
  clearBtn.addEventListener("click", function() { nmrClearLinkage(); });
  head.appendChild(clearBtn);
  wrap.appendChild(head);
  var hint = document.createElement("div");
  hint.className = "nmr-linkage-hint";
  hint.textContent = t("nmr.linkage.hint");
  wrap.appendChild(hint);
  var rows = nmrPeakClaims(report);
  if (!rows.length) {
    var empty = document.createElement("div");
    empty.className = "nmr-empty";
    empty.textContent = t("nmr.peaks.no_data");
    wrap.appendChild(empty);
    return wrap;
  }
  var conflicts = 0;
  rows.forEach(function(row) { if (row.conflict) conflicts += 1; });
  var flips = 0;
  var reportDiag = report && report.diagnostics;
  if (reportDiag && reportDiag.leave_one_signal_out && reportDiag.leave_one_signal_out.n_winner_flips != null) {
    flips = Number(reportDiag.leave_one_signal_out.n_winner_flips);
    if (!isFinite(flips)) flips = 0;
  }
  var summary = document.createElement("div");
  summary.className = "nmr-cand-meta";
  summary.textContent = t("nmr.peaks.summary", { n: rows.length, conflicts: conflicts, flips: flips });
  wrap.appendChild(summary);
  var tableWrap = document.createElement("div");
  tableWrap.className = "nmr-table-wrap";
  var table = document.createElement("table");
  table.className = "nmr-table";
  var thead = document.createElement("thead");
  var headRow = document.createElement("tr");
  ["obs", "nucleus", "exp_ppm", "claims"].forEach(function(h) {
    var th = document.createElement("th");
    th.textContent = t("nmr.col_" + h);
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);
  var tbody = document.createElement("tbody");
  rows.forEach(function(row) {
    var tr = document.createElement("tr");
    tr.className = "nmr-peak-row";
    tr.setAttribute("data-nmr-peak-row", "");
    tr.setAttribute("data-nmr-observation", row.observation_id);
    tr.setAttribute("data-nmr-claim-atoms", row.atom_labels.join(","));
    var tdObs = document.createElement("td");
    tdObs.textContent = row.observation_id;
    tr.appendChild(tdObs);
    var tdNucleus = document.createElement("td");
    tdNucleus.textContent = row.nucleus;
    tr.appendChild(tdNucleus);
    var tdPpm = document.createElement("td");
    tdPpm.className = "nmr-resid";
    tdPpm.textContent = nmrDisplayNumber(row.exp_ppm, 3);
    tr.appendChild(tdPpm);
    var tdClaims = document.createElement("td");
    tdClaims.textContent = row.atom_labels.join(", ");
    if (row.conflict) {
      var badge = document.createElement("span");
      badge.className = "nmr-peak-conflict";
      badge.textContent = " " + t("nmr.peaks.conflict");
      tdClaims.appendChild(badge);
    }
    tr.appendChild(tdClaims);
    tr.addEventListener("mouseenter", function() { nmrSelectObservation(row.observation_id); });
    tr.addEventListener("click", function() { nmrSelectObservation(row.observation_id); });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  tableWrap.appendChild(table);
  wrap.appendChild(tableWrap);
  return wrap;
}

function nmrEnsureViewer(candIndex, container) {
  var info = nmrPanelState.viewers[candIndex];
  if (info && info.viewer && info.container === container && container.querySelector("canvas")) return info;
  if (!ACPVisualization.mol3d.isAvailable()) return null;
  container.innerHTML = "";
  var viewer = ACPVisualization.mol3d.create(container, { backgroundColor: "0x0e1419", antialias: true });
  info = {
    container: container,
    viewer: viewer,
    framing: { epoch: 0, pending: false, settled: false, observer: null },
    labels: [],
    atoms: []
  };
  nmrPanelState.viewers[candIndex] = info;
  (function(index) {
    viewer.setClickable({}, true, function(atom) {
      var current = nmrPanelState.viewers[index];
      if (!current || !current.labels) return;
      var label = current.labels[atom.index];
      if (label) nmrSelectAtom(label, index);
    });
  })(candIndex);
  return info;
}

async function nmrFetchCandidateGeometry(job, cand) {
  var paths = [];
  if (cand && cand.label) {
    paths.push("WORK/02_SEARCH/" + String(cand.label) + "/conformers/input.xyz");
  }
  paths.push("input.xyz");
  for (var i = 0; i < paths.length; i++) {
    var xyz = await nmrFetchGeometryFromPath(job, paths[i]);
    if (xyz) return xyz;
  }
  return null;
}

async function nmrFetchGeometryFromPath(job, path) {
  var jobId = job.id;
  var remote = !!jobRemoteNodeName(job);
  var encoded = path.split("/").map(encodeURIComponent).join("/");
  var url = remote
    ? "/jobs/" + encodeURIComponent(jobId) + "/remote-files/" + encoded + "/preview?mode=structure"
    : "/jobs/" + encodeURIComponent(jobId) + "/files/" + encoded;
  var resp;
  try {
    resp = remote ? await apiRemote(url) : await api(url);
  } catch (e) {
    return null;
  }
  return nmrGeometryText(resp, remote);
}

function nmrGeometryText(resp, remote) {
  if (typeof resp === "string") return resp;
  if (!resp || typeof resp !== "object") return null;
  if (remote) return typeof resp.content === "string" ? resp.content : null;
  return typeof resp.xyz === "string" ? resp.xyz : null;
}

async function nmrLoadStructureViewers(job, candidates, token) {
  for (var i = 0; i < candidates.length; i++) {
    if (token !== nmrPanelState.token) return;
    await nmrLoadCandidateGeometry(job, candidates[i], token);
  }
}

async function nmrLoadCandidateGeometry(job, cand, token) {
  var container = document.querySelector('[data-nmr-structure-viewer="' + cand.index + '"]');
  if (!container) return;
  var xyz = await nmrFetchCandidateGeometry(job, cand);
  if (token !== nmrPanelState.token || !container.isConnected) return;
  var info = null;
  try { info = nmrEnsureViewer(cand.index, container); } catch (e) { info = null; }
  if (!xyz || !info) {
    container.className = "nmr-structure-viewer empty";
    container.textContent = t("nmr.structure.unavailable");
    return;
  }
  nmrPanelState.loadingCandidate = cand.index;
  var sv = window.ACPStructureViewer;
  var result = null;
  try {
    result = (sv && typeof sv.sharedLoadGeometry === "function")
      ? sv.sharedLoadGeometry(xyz, { canvasId: "nmr-structure", source: "nmr_report" })
      : null;
  } catch (e) {
    result = null;
  }
  nmrPanelState.loadingCandidate = null;
  if (!result || !result.loaded) {
    container.className = "nmr-structure-viewer empty";
    container.textContent = t("nmr.structure.unavailable");
    return;
  }
  var model = null;
  try { model = info.viewer.getModel(); } catch (e) { model = null; }
  info.atoms = model ? model.selectedAtoms({}) : [];
  info.labels = nmrAtomLabelsFromSymbols(info.atoms.map(function(atom) { return atom.elem; }));
  container.className = "nmr-structure-viewer";
  container.textContent = "";
  scheduleViewerFraming(container, info.framing, function() { return info.viewer; });
  nmrPanelApplySelection();
}


