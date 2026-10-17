/*
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
 *   t, energyGraphState, s2scanState, currentLang, translateStatus, escapeHtml, energyGenCandidateName, energyGraphFrameCandidateFor, energyInspectorMetadataRows, renderTrajectoryAnnotations
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
  var t, energyGraphState, s2scanState, currentLang, translateStatus, escapeHtml, energyGenCandidateName, energyGraphFrameCandidateFor, energyInspectorMetadataRows, renderTrajectoryAnnotations;
var _egMeasureCanvas = null;
var _egMeasureCtx = null;
var _egFontFamily = "'IBM Plex Sans', 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif";
  function init(deps) {
    deps = deps || {};
    t = deps.t;
    energyGraphState = deps.energyGraphState;
    s2scanState = deps.s2scanState;
    currentLang = deps.currentLang;
    translateStatus = deps.translateStatus;
    escapeHtml = deps.escapeHtml;
    energyGenCandidateName = deps.energyGenCandidateName;
    energyGraphFrameCandidateFor = deps.energyGraphFrameCandidateFor;
    energyInspectorMetadataRows = deps.energyInspectorMetadataRows;
    renderTrajectoryAnnotations = deps.renderTrajectoryAnnotations;
  }

function energyGraphSeries(data) {
  const series = data && Array.isArray(data.series) ? data.series : [];
  if (!series.length) return null;
  let selected = series.find(function(item) { return item.id === energyGraphState.seriesId; });
  if (!selected) selected = series.find(function(item) { return item.id === "relative_energy"; });
  if (!selected) selected = series.find(function(item) { return String(item.unit || "").toLowerCase().indexOf("kcal") >= 0; });
  if (!selected) selected = series.find(function(item) { return item.id === data.default_series; });
  if (!selected) selected = series[0];
  energyGraphState.seriesId = selected.id;
  return selected;
}

function energyGraphValue(value) {
  return typeof value === "number" && isFinite(value) ? value : null;
}

function energyGraphFormatNumber(value, digits) {
  const number = energyGraphValue(value);
  if (number === null) return "--";
  const places = digits == null ? (Math.abs(number) >= 100 ? 2 : 3) : digits;
  return number.toFixed(places);
}

function energyGraphCandidateAnnotations(data) {
  return (data && Array.isArray(data.annotations) ? data.annotations : []).filter(function(item) {
    return item && (item.type === "ts" || item.type === "intermediate");
  });
}

function energyGraphFrameIndexForNode(data, nodeId) {
  const node = (data.nodes || []).find(function(item) { return String(item.id) === String(nodeId); });
  return node && node.frame_index != null ? Number(node.frame_index) : null;
}

function energyGraphSeriesValueAtFrame(data, series, frameIndex) {
  const nodes = Array.isArray(data.nodes) ? data.nodes : [];
  let nodeIndex = nodes.findIndex(function(node) { return String(node.id) === String(energyGraphState.selectedNodeId); });
  if (nodeIndex < 0) nodeIndex = nodes.findIndex(function(node) { return Number(node.frame_index) === Number(frameIndex); });
  const values = Array.isArray(series && series.values) ? series.values : [];
  return nodeIndex >= 0 ? energyGraphValue(values[nodeIndex]) : null;
}

function energyGraphMeasureText(text, fontSize, bold) {
  if (!_egMeasureCanvas) {
    try { _egMeasureCanvas = document.createElement("canvas"); _egMeasureCtx = _egMeasureCanvas.getContext("2d"); } catch (_e) { _egMeasureCanvas = null; _egMeasureCtx = null; }
  }
  var fs = fontSize || 11;
  if (_egMeasureCtx) {
    _egMeasureCtx.font = (bold ? "600 " : "") + fs + "px " + _egFontFamily;
    return _egMeasureCtx.measureText(String(text || "")).width;
  }
  /* CJK/fullwidth-aware fallback: CJK ~1.0*fs, ASCII ~0.59*fs */
  var w = 0, s = String(text || "");
  for (var i = 0; i < s.length; i += 1) {
    var c = s.charCodeAt(i);
    w += (c > 0x2e7f && c < 0xa000) || (c > 0xf8ff && c < 0xfb00) || (c > 0xfe2f && c < 0xfe70) || (c > 0xfeff && c < 0xff60) || (c > 0xffe0) || (c > 0x3000 && c < 0x3040) ? fs : fs * 0.59;
  }
  return w;
}

function energyGraphChartGeometry() {
  var vs = energyGraphState.viewSize || { width: 0, height: 0 };
  var W = Math.max(280, vs.width || 1040);
  var H = Math.max(200, vs.height || 520);
  var wClamp = Math.min(Math.max(W, 280), 2400);
  var padLeft = Math.min(82, Math.max(48, Math.round(82 * wClamp / 1040)));
  var padRight = Math.min(36, Math.max(20, Math.round(28 * wClamp / 1040)));
  var padTop = 38;
  var padBottom = 72;
  var pad = { left: padLeft, right: padRight, top: padTop, bottom: padBottom };
  return { W: W, H: H, pad: pad, plotRect: { left: pad.left, top: pad.top, right: W - pad.right, bottom: H - pad.bottom } };
}

function energyChartNiceTicks(lo, hi, targetCount, integerMode) {
  /* Returns {values: [...], step: N} using 1/2/2.5/5×10^k step sizes.
   * All returned ticks are clamped to [lo, hi].  Iterates candidate steps
   * from the largest ≤ rawStep upward, picking the first that yields
   * ≤ targetCount+1 in-domain ticks.  integerMode forces integer values.
   * Handles lo===hi and tiny spans gracefully.
   */
  if (lo === hi) { lo -= 0.5; hi += 0.5; }
  var range = hi - lo;
  if (range <= 0) range = 1;
  targetCount = targetCount || 5;
  /* Build sorted candidate steps: {1,2[,2.5],5} × 10^k; integerMode restricts k≥0 so step≥1 */
  var baseSteps = integerMode ? [1, 2, 5] : [1, 2, 2.5, 5];
  var candidates = [];
  var kMin = integerMode ? 0 : -10;
  for (var k = kMin; k <= 10; k++) {
    var mult = Math.pow(10, k);
    for (var b = 0; b < baseSteps.length; b++) { candidates.push(baseSteps[b] * mult); }
  }
  candidates.sort(function(a, b) { return a - b; });
  /* Find largest candidate ≤ rawStep */
  var rawStep = range / targetCount;
  var startIdx = 0;
  for (var i = candidates.length - 1; i >= 0; i--) {
    if (candidates[i] <= rawStep + 1e-12) { startIdx = i; break; }
  }
  /* Iterate upward from startIdx; pick first step with ≤ targetCount+1 in-domain ticks */
  for (var j = startIdx; j < candidates.length; j++) {
    var step = candidates[j];
    var nLo = Math.ceil(lo / step - 1e-9) * step;
    var nHi = Math.floor(hi / step + 1e-9) * step;
    if (nHi < nLo - 1e-9) continue;                      /* no in-domain ticks */
    var count = Math.round((nHi - nLo) / step) + 1;
    if (count <= targetCount + 1) {
      var ticks = [];
      for (var t = nLo; t <= nHi + step * 0.001; t += step) {
        var v = integerMode ? Math.round(t) : +t.toPrecision(12);
        if (v >= lo - 1e-9 && v <= hi + 1e-9) ticks.push(v);
      }
      return { values: ticks, step: step };
    }
  }
  /* Fallback (should not happen for reasonable domains) */
  var step = candidates[candidates.length - 1];
  var nLo = Math.ceil(lo / step - 1e-9) * step;
  var nHi = Math.floor(hi / step + 1e-9) * step;
  var ticks = [];
  for (var t = nLo; t <= nHi + step * 0.001; t += step) {
    var v = integerMode ? Math.round(t) : +t.toPrecision(12);
    if (v >= lo - 1e-9 && v <= hi + 1e-9) ticks.push(v);
  }
  /* Integer-mode fallback: if no integer ticks in domain, retry without integerMode */
  if (integerMode && ticks.length === 0) {
    return energyChartNiceTicks(lo, hi, targetCount, false);
  }
  return { values: ticks, step: step };
}

function energyGraphAxesMarkup(xDom, yDom, geom, xTickMode) {
  var W = geom.W, H = geom.H, pad = geom.pad;
  var markup = "";
  function xFor(v) { return pad.left + ((v - xDom[0]) / (xDom[1] - xDom[0] || 1)) * (W - pad.left - pad.right); }
  function yFor(v) { return pad.top + (1 - ((v - yDom[0]) / (yDom[1] - yDom[0] || 1))) * (H - pad.top - pad.bottom); }
  /* Y axis: nice-number ticks */
  var yTicks = energyChartNiceTicks(yDom[0], yDom[1], 5, false);
  for (var i = 0; i < yTicks.values.length; i += 1) {
    var y = yTicks.values[i];
    var py = yFor(y);
    markup += '<line class="energy-gridline" x1="' + pad.left + '" y1="' + py + '" x2="' + (W - pad.right) + '" y2="' + py + '"></line>';
    markup += '<text class="energy-axis-text" x="' + (pad.left - 12) + '" y="' + (py + 4) + '" text-anchor="end">' + escapeHtml(energyGraphFormatNumber(y, 2)) + '</text>';
  }
  /* X axis: nice-number or integer ticks */
  var xIntMode = xTickMode === "integer" || (xTickMode !== "nice" && xDom[0] === Math.round(xDom[0]) && xDom[1] === Math.round(xDom[1]));
  var xTicks = energyChartNiceTicks(xDom[0], xDom[1], 5, xIntMode);
  var xDigits = xTicks.step >= 1 ? 0 : 3;
  for (var j = 0; j < xTicks.values.length; j += 1) {
    var x = xTicks.values[j];
    var px = xFor(x);
    var xAnchor = j === 0 ? "start" : j === xTicks.values.length - 1 ? "end" : "middle";
    var xLabel = j === 0 ? px + 2 : j === xTicks.values.length - 1 ? px - 2 : px;
    markup += '<text class="energy-axis-text" x="' + xLabel + '" y="' + (H - pad.bottom + 23) + '" text-anchor="' + xAnchor + '">' + escapeHtml(energyGraphFormatNumber(x, xDigits)) + '</text>';
  }
  markup += '<line class="energy-axis" x1="' + pad.left + '" y1="' + pad.top + '" x2="' + pad.left + '" y2="' + (H - pad.bottom) + '"></line>';
  markup += '<line class="energy-axis" x1="' + pad.left + '" y1="' + (H - pad.bottom) + '" x2="' + (W - pad.right) + '" y2="' + (H - pad.bottom) + '"></line>';
  return markup;
}

function energyGraphNodeX(node, mode) {
  if (!node) return null;
  var meta = node.metadata || {};
  if (mode === "path_length" && meta.cumulative_arclength_A != null) {
    return energyGraphValue(meta.cumulative_arclength_A);
  }
  if (mode === "reaction_progress" && meta.reaction_progress != null) {
    return energyGraphValue(meta.reaction_progress);
  }
  if (mode === "frame_index") {
    return Number(node.frame_index != null ? node.frame_index : 0);
  }
  return energyGraphValue(node.x);
}

function energyProjectCandidatesAsAnnotations(data) {
  var fc = energyGraphState.frameCandidates;
  if (!fc || !Array.isArray(fc.list) || !fc.list.length) return [];
  var viewType = String((data && data.view_type) || "");
  var existing = Array.isArray(data && data.annotations) ? data.annotations : [];
  var existingIds = {};
  existing.forEach(function(a) { if (a && a.id) existingIds[String(a.id)] = true; });
  var projected = [];
  fc.list.forEach(function(c) {
    if (String(c.view_type || "") !== viewType) return;
    var cid = String(c.candidate_id || "");
    var projId = "candidate:" + cid;
    if (existingIds[projId]) return;
    var role = String(c.role || "");
    var label = String(c.display_label || c.role || "");
    projected.push({
      id: projId,
      candidate_id: cid,
      type: role === "ts" ? "ts" : "intermediate",
      label: label,
      frame_index: Number(c.frame_index),
      x: null,
      y: null,
      status: "",
      geometry_ref: "",
      selected: false,
      saved: true,
      selection_source: "manual_frame",
    });
  });
  return projected;
}

function energyCandidateBandLayoutCore(chips, plotLeft, plotRight) {
  var chipGap = 8, maxRows = 2;
  var rows = [[], []];
  /* The active candidate is a navigation invariant: lay it out before
   * ordinary chips so narrowing the chart can never silently fold it away. */
  chips.sort(function(a, b) {
    var aSelected = a._selected ? 0 : 1;
    var bSelected = b._selected ? 0 : 1;
    return aSelected - bSelected || a.anchorPx - b.anchorPx;
  });
  chips.forEach(function(chip) {
    var anchorPx = chip.anchorPx;
    var placed = false;
    for (var r = 0; r < maxRows && !placed; r++) {
      var row = rows[r];
      var startX = Math.max(plotLeft, Math.min(plotRight - chip.chipW, anchorPx - chip.chipW / 2));
      if (row.length > 0) {
        var last = row[row.length - 1];
        var minStart = last._x + last.chipW + chipGap;
        if (startX < minStart) startX = minStart;
      }
      if (startX + chip.chipW <= plotRight + 1) {
        chip._x = startX;
        chip._row = r;
        row.push(chip);
        placed = true;
      }
    }
    if (!placed) chip._skip = true;
  });
}

function energyCandidateBandLayout(items, geom, xFor, yFor, opts) {
  if (!items.length) return { bandSvg: "", plotSvg: "", bandHeight: 0 };
  var W = geom.W, pad = geom.pad;
  var plotLeft = pad.left, plotRight = W - pad.right;
  var rowHeight = 28, rowGap = 8, chipH = 22;
  /* ── Group by frame_index: same frame → single chip ── */
  var byFrame = {};
  items.forEach(function(item) {
    var fi = Number(item.frameIndex);
    if (!byFrame[fi]) byFrame[fi] = [];
    byFrame[fi].push(item);
  });
  var merged = [];
  var candidateOrdinal = 0;
  Object.keys(byFrame).sort(function(a, b) { return Number(a) - Number(b); }).forEach(function(fi) {
    var group = byFrame[fi];
    var first = group[0];
    candidateOrdinal += 1;
    var firstAnn = first.annotation;
    var displayLabel = String(firstAnn.display_label || firstAnn.label || "");
    if (!displayLabel) {
      var role = String(first.type || firstAnn.role || "").toUpperCase();
      displayLabel = (role === "TS" ? "TS" : "INT") + "-" + String(candidateOrdinal).padStart(2, "0");
    }
    var frameNumber = firstAnn.frame_number != null ? Number(firstAnn.frame_number) : Number(fi) + 1;
    var chipText = displayLabel + " " + t("energy.band.frame", { n: frameNumber }) + (group.length > 1 ? " (+" + (group.length - 1) + ")" : "");
    /* Build tooltip text */
    var tooltipLines = [t("energy.band.tooltip_frame", { n: frameNumber })];
    tooltipLines.push(t("energy.band.tooltip_index", { n: fi }));
    if (firstAnn.scan_step != null) tooltipLines.push(t("energy.band.tooltip_step", { n: firstAnn.scan_step }));
    tooltipLines.push(t("energy.band.tooltip_coord", { val: energyGraphFormatNumber(first.dataX, 3), unit: "" }));
    tooltipLines.push(t("energy.band.tooltip_energy", { val: energyGraphFormatNumber(first.dataY, 2) }));
    tooltipLines.push(firstAnn.saved ? t("energy.band.tooltip_saved") : t("energy.band.tooltip_unsaved"));
    var tooltip = tooltipLines.join("\n");
    var typeClass = first.type === "ts" ? " type-ts" : (first.type === "intermediate" ? " type-int" : "");
    merged.push({ items: group, frameIndex: Number(fi), chipText: chipText, tooltip: tooltip, typeClass: typeClass,
      anchorPx: xFor(first.dataX), dataX: first.dataX, dataY: first.dataY, type: first.type,
      _selected: group.some(function(item) { return String(item.annotation.id) === String(energyGraphState.selectedAnnotationId); }) });
  });
  /* ── Measure chip widths ── */
  merged.forEach(function(m) {
    m.chipW = energyGraphMeasureText(m.chipText, 11, true) + 18;
  });
  /* First probe for overflow, then reserve its slot before laying out regular
   * chips.  The old code did this in the opposite order, allowing the only
   * disclosure of hidden candidates to be hidden as well. */
  energyCandidateBandLayoutCore(merged, plotLeft, plotRight);
  var initialSkipped = merged.filter(function(m) { return m._skip; });
  if (initialSkipped.length) {
    var initialCount = initialSkipped.reduce(function(total, m) { return total + m.items.length; }, 0);
    var reservedWidth = energyGraphMeasureText(t("energy.band.overflow", { count: initialCount }), 11, true) + 18;
    merged.forEach(function(m) { delete m._x; delete m._row; delete m._skip; });
    energyCandidateBandLayoutCore(merged, plotLeft, Math.max(plotLeft, plotRight - reservedWidth - 8));
  }
  var placed = merged.filter(function(m) { return !m._skip; });
  var skipped = merged.filter(function(m) { return m._skip; });
  /* ── Overflow merge chip ── */
  var overflowChip = null;
  if (skipped.length > 0) {
    var collapsedCandidateCount = skipped.reduce(function(total, m) { return total + m.items.length; }, 0);
    var overflowText = t("energy.band.overflow", { count: collapsedCandidateCount });
    var overflowW = energyGraphMeasureText(overflowText, 11, true) + 18;
    /* The reserved right-hand slot makes this placement unconditional. */
    overflowChip = { chipW: overflowW, anchorPx: plotRight - overflowW / 2,
      _isOverflow: true, _skipCount: collapsedCandidateCount,
      _x: Math.max(plotLeft, plotRight - overflowW), _row: 0 };
    placed.push(overflowChip);
  }
  var renderedCandidateLabels = placed.filter(function(m) { return !m._isOverflow; })
    .reduce(function(total, m) { return total + m.items.length; }, 0);
  var collapsedCount = skipped.reduce(function(total, m) { return total + m.items.length; }, 0);
  var selectedCandidateCount = merged.reduce(function(total, m) { return total + m.items.length; }, 0);
  if (selectedCandidateCount !== renderedCandidateLabels + collapsedCount) {
    console.error("Candidate label count invariant failed", { selectedCandidateCount: selectedCandidateCount, renderedCandidateLabels: renderedCandidateLabels, collapsedCandidateCount: collapsedCount });
  }
  var usedRows = 0;
  placed.forEach(function(m) { if (m._row >= usedRows) usedRows = m._row + 1; });
  var bandHeight = usedRows * rowHeight + (usedRows > 1 ? rowGap : 0);
  /* ── Render band SVG (chips + leader lines) ── */
  var bandSvg = "";
  var plotSvg = "";
  var selectionLocked = !!(opts && opts.selectionLocked);
  placed.forEach(function(m) {
    var cx = m._x + m.chipW / 2;
    var cy = m._row === 0 ? rowHeight / 2 : rowHeight + rowGap + rowHeight / 2;
    if (m._isOverflow) {
      /* Overflow merge chip */
      bandSvg += '<rect class="energy-band-chip-bg energy-band-chip-overflow" x="' + m._x + '" y="' + (cy - chipH / 2) + '" width="' + m.chipW + '" height="' + chipH + '" rx="4"/>';
      var overflowText = t("energy.band.overflow", { count: m._skipCount });
      bandSvg += '<text class="energy-band-chip-label energy-band-chip-label-overflow" x="' + cx + '" y="' + (cy + 4) + '" text-anchor="middle">' + escapeHtml(overflowText) + '</text>';
      return;
    }
    /* Normal chip */
    var savedClass = m.items[0].annotation.saved ? " saved" : " pending";
    var isSelected = !!m._selected;
    var selectedClass = isSelected ? " selected" : "";
    var typeClass = m.typeClass || "";
    bandSvg += '<rect class="energy-band-chip-bg' + selectedClass + savedClass + typeClass + '" data-energy-candidate-frame="' + m.frameIndex + '" x="' + m._x + '" y="' + (cy - chipH / 2) + '" width="' + m.chipW + '" height="' + chipH + '" rx="4"' + (m.tooltip ? ' title="' + escapeHtml(m.tooltip) + '"' : '') + '/>';
    /* Chip text */
    bandSvg += '<text class="energy-band-chip-label' + selectedClass + '" x="' + cx + '" y="' + (cy + 4) + '" text-anchor="middle">' + escapeHtml(m.chipText) + '</text>';
    /* Leader line from chip bottom to candidate point */
    var pointX = xFor(m.dataX);
    var pointY = yFor(m.dataY);
    var leaderClass = isSelected ? " selected" : "";
    bandSvg += '<line class="energy-band-leader' + leaderClass + '" x1="' + cx + '" y1="' + (cy + chipH / 2) + '" x2="' + pointX + '" y2="' + pointY + '"/>';
    /* Candidate markers at real coordinates (inside plot group) */
    m.items.forEach(function(item) {
      var annotation = item.annotation;
      var ax = xFor(item.dataX), ay = yFor(item.dataY);
      var isTS = item.type === "ts";
      var stateClass = annotation.saved ? " saved" : " pending";
      var annSelected = annotation.id === energyGraphState.selectedAnnotationId;
      var annSelClass = annSelected ? " selected" : "";
      var dragAttr = !selectionLocked ? ' data-energy-draggable="1"' : "";
      plotSvg += '<g data-energy-annotation="' + escapeHtml(String(annotation.id)) + '" data-energy-frame="' + escapeHtml(String(item.frameIndex)) + '"' + dragAttr + '>';
      if (isTS) {
        plotSvg += '<path class="energy-marker-ts' + annSelClass + stateClass + '" d="M ' + ax + ' ' + (ay - 7) + ' L ' + (ax + 7) + ' ' + ay + ' L ' + ax + ' ' + (ay + 7) + ' L ' + (ax - 7) + ' ' + ay + ' Z"></path>';
      } else {
        plotSvg += '<circle class="energy-marker-int' + annSelClass + stateClass + '" cx="' + ax + '" cy="' + ay + '" r="6"></circle>';
      }
      plotSvg += '</g>';
    });
  });
  /* Folded candidates lose only their floating label, never their plot marker.
   * This keeps every TS/INT point discoverable even before the list is opened. */
  skipped.forEach(function(m) {
    m.items.forEach(function(item) {
      var annotation = item.annotation;
      var ax = xFor(item.dataX), ay = yFor(item.dataY);
      var isTS = item.type === "ts";
      var stateClass = annotation.saved ? " saved" : " pending";
      var annSelected = annotation.id === energyGraphState.selectedAnnotationId;
      var annSelClass = annSelected ? " selected" : "";
      var dragAttr = !selectionLocked ? ' data-energy-draggable="1"' : "";
      plotSvg += '<g data-energy-annotation="' + escapeHtml(String(annotation.id)) + '" data-energy-frame="' + escapeHtml(String(item.frameIndex)) + '"' + dragAttr + '>';
      if (isTS) {
        plotSvg += '<path class="energy-marker-ts' + annSelClass + stateClass + '" d="M ' + ax + ' ' + (ay - 7) + ' L ' + (ax + 7) + ' ' + ay + ' L ' + ax + ' ' + (ay + 7) + ' L ' + (ax - 7) + ' ' + ay + ' Z"></path>';
      } else {
        plotSvg += '<circle class="energy-marker-int' + annSelClass + stateClass + '" cx="' + ax + '" cy="' + ay + '" r="6"></circle>';
      }
      plotSvg += '</g>';
    });
  });
  /* Chip click hit targets */
  bandSvg = '<g class="energy-candidate-band" data-selected-candidate-count="' + selectedCandidateCount + '" data-rendered-candidate-labels="' + renderedCandidateLabels + '" data-collapsed-candidate-count="' + collapsedCount + '">' + bandSvg;
  placed.forEach(function(m) {
    if (m._isOverflow) {
      bandSvg += '<rect class="energy-band-chip-hit" data-energy-candidate-expand="1" x="' + m._x + '" y="' + (m._row === 0 ? 0 : rowHeight + rowGap) + '" width="' + m.chipW + '" height="' + rowHeight + '" fill="transparent" rx="4"/>';
    } else {
      bandSvg += '<rect class="energy-band-chip-hit" data-energy-candidate-jump="' + m.frameIndex + '" x="' + m._x + '" y="' + (m._row === 0 ? 0 : rowHeight + rowGap) + '" width="' + m.chipW + '" height="' + rowHeight + '" fill="transparent" rx="4"/>';
    }
  });
  bandSvg += '</g>';
  return { bandSvg: bandSvg, plotSvg: plotSvg, bandHeight: bandHeight };
}

function energyCandidateBandPreHeight(items, xFor, plotLeft, plotRight) {
  if (!items.length) return 0;
  var rowHeight = 28, rowGap = 8;
  /* Group by frame_index */
  var byFrame = {};
  items.forEach(function(item) {
    var fi = Number(item.frameIndex);
    if (!byFrame[fi]) byFrame[fi] = [];
    byFrame[fi].push(item);
  });
  var merged = [];
  var candidateOrdinal = 0;
  Object.keys(byFrame).sort(function(a, b) { return Number(a) - Number(b); }).forEach(function(fi) {
    var group = byFrame[fi];
    var first = group[0];
    candidateOrdinal += 1;
    var firstAnn = first.annotation;
    var displayLabel = String(firstAnn.display_label || firstAnn.label || "");
    if (!displayLabel) {
      var role = String(first.type || firstAnn.role || "").toUpperCase();
      displayLabel = (role === "TS" ? "TS" : "INT") + "-" + String(candidateOrdinal).padStart(2, "0");
    }
    var frameNumber = firstAnn.frame_number != null ? Number(firstAnn.frame_number) : Number(fi) + 1;
    var chipText = displayLabel + " " + t("energy.band.frame", { n: frameNumber }) + (group.length > 1 ? " (+" + (group.length - 1) + ")" : "");
    merged.push({ chipW: energyGraphMeasureText(chipText, 11, true) + 18, anchorPx: xFor(first.dataX), items: group,
      _selected: group.some(function(item) { return String(item.annotation.id) === String(energyGraphState.selectedAnnotationId); }) });
  });
  /* Greedy2-row dry run (shared core) */
  energyCandidateBandLayoutCore(merged, plotLeft, plotRight);
  var initialSkipped = merged.filter(function(m) { return m._skip; });
  if (initialSkipped.length) {
    var initialCount = initialSkipped.reduce(function(total, m) { return total + m.items.length; }, 0);
    var overflowW = energyGraphMeasureText(t("energy.band.overflow", { count: initialCount }), 11, true) + 18;
    merged.forEach(function(m) { delete m._x; delete m._row; delete m._skip; });
    energyCandidateBandLayoutCore(merged, plotLeft, Math.max(plotLeft, plotRight - overflowW - 8));
  }
  var placed = merged.filter(function(m) { return !m._skip; });
  var skipped = merged.filter(function(m) { return m._skip; });
  if (skipped.length) placed.push({ _x: Math.max(plotLeft, plotRight - 1), _row: 0, chipW: 1 });
  var usedRows = 0;
  placed.forEach(function(m) { if (m._row >= usedRows) usedRows = m._row + 1; });
  return usedRows * rowHeight + (usedRows > 1 ? rowGap : 0);
}

function energyGraphNodeForAnnotation(data, annotation) {
  if (!data || !annotation) return null;
  var nodes = Array.isArray(data.nodes) ? data.nodes : [];
  if (annotation.node_id) {
    var byId = nodes.find(function(n) { return String(n.id) === String(annotation.node_id); });
    if (byId) return byId;
  }
  if (annotation.frame_index != null) {
    var fi = Number(annotation.frame_index);
    return nodes.find(function(n) { return Number(n.frame_index) === fi; }) || null;
  }
  return null;
}

function energyCandidateListMarkup(data) {
  var fc = energyGraphState.frameCandidates;
  if (!fc || !Array.isArray(fc.list) || !fc.list.length) return "";
  var viewType = String((data && data.view_type) || "");
  var candidates = fc.list.filter(function(c) { return String(c.view_type || "") === viewType; });
  if (!candidates.length) return "";
  /* Frame order is the stable candidate order everywhere: plot chips, this
   * complete list, and the structure viewer selection. */
  candidates.sort(function(a, b) { return Number(a.frame_index) - Number(b.frame_index) || String(a.candidate_id).localeCompare(String(b.candidate_id)); });
  var filter = energyGraphState.candidateListFilter || "all";
  var allCandidates = candidates.slice();
  if (filter === "ts") candidates = candidates.filter(function(c) { return String(c.role || "").toLowerCase() === "ts"; });
  if (filter === "int") candidates = candidates.filter(function(c) { return String(c.role || "").toLowerCase() !== "ts"; });
  var nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
  var selectedFrame = null;
  var selNode = nodes.find(function(n) { return String(n.id) === String(energyGraphState.selectedNodeId); });
  if (selNode) selectedFrame = Number(selNode.frame_index);
  /* Group by frame_index */
  var byFrame = {};
  candidates.forEach(function(c) {
    var fi = Number(c.frame_index);
    if (!byFrame[fi]) byFrame[fi] = [];
    byFrame[fi].push(c);
  });
  var rows = "";
  /* Collect consistency_warnings from data.metadata */
  var consistencyWarnings = (data && data.metadata && Array.isArray(data.metadata.consistency_warnings)) ? data.metadata.consistency_warnings : [];
  var missingFrameSet = {};
  consistencyWarnings.forEach(function(w) { if (w && w.frame_index != null) missingFrameSet[Number(w.frame_index)] = true; });
  Object.keys(byFrame).sort(function(a, b) { return Number(a) - Number(b); }).forEach(function(fi) {
    var group = byFrame[fi];
    var frameNumber = null;
    /* Try to get frame_number from annotation */
    group.forEach(function(c) {
      if (frameNumber == null && c.frame_number != null) frameNumber = Number(c.frame_number);
    });
    if (frameNumber == null) frameNumber = Number(fi) + 1;
    var groupLabel = "\u7b2c " + frameNumber + " \u5e27";
    rows += '<div class="energy-candidate-group-head" data-energy-candidate-frame="' + fi + '">' + escapeHtml(groupLabel) + '</div>';
    group.forEach(function(c) {
      var role = String(c.role || "");
      var isTS = role === "ts";
      var glyph = isTS ? "\u25c6" : "\u25cf";
      var glyphClass = isTS ? "ts" : "int";
      var displayLabel = String(c.display_label || c.label || "");
      if (!displayLabel) {
        var ordinal = allCandidates.indexOf(c) + 1;
        displayLabel = (isTS ? "TS" : "INT") + "-" + String(ordinal).padStart(2, "0");
      }
      var cycleText = "F" + frameNumber + " \u00b7 frame_index " + fi;
      var energy = "";
      var cNode = energyGraphNodeForAnnotation(data, c);
      var isMissing = !cNode && missingFrameSet[Number(fi)];
      if (cNode && cNode.energy != null) energy = energyGraphFormatNumber(cNode.energy, 3);
      var userName = String(c.name || "");
      var isActive = selectedFrame === Number(fi) ? " active" : "";
      var missingClass = isMissing ? " missing-node" : "";
      var annotationId = String(c.id || c.candidate_id || "");
      rows += '<div class="energy-candidate-row' + isActive + missingClass + '" data-energy-candidate-jump="' + fi + '" data-energy-candidate-annotation="' + escapeHtml(annotationId) + '">' +
        '<span class="energy-candidate-glyph ' + glyphClass + '">' + glyph + '</span>' +
        '<div class="energy-candidate-info">' +
          '<div class="energy-candidate-label">' + escapeHtml(displayLabel) + (energy ? ' <span style="color:var(--text-tertiary);font-weight:400;font-size:10px;">' + escapeHtml(energy) + '</span>' : '') + '</div>' +
          '<div class="energy-candidate-meta">' + escapeHtml(cycleText) + (c.saved === false ? ' \u00b7 \u672a\u4fdd\u5b58' : ' \u00b7 \u5df2\u4fdd\u5b58') + '</div>' +
          (isMissing ? '<div class="energy-candidate-missing-note">' + escapeHtml(t("energy.band.missing_node")) + '</div>' : '') +
          (userName ? '<div class="energy-candidate-name">' + escapeHtml(userName) + '</div>' : '') +
        '</div>' +
      '</div>';
    });
  });
  return '<div class="energy-candidate-list">' +
    '<div class="energy-candidate-list-head">' + escapeHtml(t("energy.candidates.title")) + ' <span class="energy-candidate-list-count">' + candidates.length + ' / ' + allCandidates.length + '</span></div>' +
    '<div class="energy-annotation-toolbar" role="group" aria-label="Candidate type filter">' +
      '<button class="energy-annotation-toolbar-btn' + (filter === "all" ? " active" : "") + '" data-energy-candidate-filter="all" type="button">全部</button>' +
      '<button class="energy-annotation-toolbar-btn' + (filter === "ts" ? " active" : "") + '" data-energy-candidate-filter="ts" type="button">TS</button>' +
      '<button class="energy-annotation-toolbar-btn' + (filter === "int" ? " active" : "") + '" data-energy-candidate-filter="int" type="button">INT</button>' +
    '</div>' +
    rows +
  '</div>';
}

function energyChartBuildSvg(cfg) {
  /* Unified SVG chart core.
   * Energy path (default): axes after plot, selected+annotations inside plot group.
   * Optimization path: axesBeforePlot, selectedInPlotGroup=false, annotationsInPlotGroup=false.
   * All new config fields default to energy-chart values for backward compat.
   */
  var geom = cfg.geom, pad = geom.pad;
  var W = geom.W, H = geom.H;
  var xDom = cfg.xDom, yDom = cfg.yDom;
  var y2Dom = cfg.y2Dom || null;
  var plotAttr = cfg.plotAttr || "data-energy-plot";
  var overlayAttr = cfg.overlayAttr || "data-energy-overlay";
  var axesAttr = cfg.axesAttr || "data-energy-axes";
  var gridClass = cfg.gridClass || "energy-gridline";
  var axisTextClass = cfg.axisTextClass || "energy-axis-text";
  var axisLineClass = cfg.axisLineClass || "energy-axis";
  var selectedLineClass = cfg.selectedLineClass || "energy-current-line";
  var pointClass = cfg.pointClass || "energy-point";
  var zeroLineClass = cfg.zeroLineClass || "energy-zero-line";
  var Pw = W - pad.left - pad.right, Ph = H - pad.top - pad.bottom;
  var svg = cfg.svgOpen;
  /* clipPath */
  svg += '<defs><clipPath id="' + cfg.clipId + '"><rect x="' + pad.left + '" y="' + pad.top + '" width="' + Pw + '" height="' + Ph + '"/></clipPath></defs>';
  /* axes before plot group (optimization) */
  if (cfg.axesBeforePlot) {
    var _yDig = cfg.yDigits != null ? cfg.yDigits : 5;
    var _xDig = cfg.xDigits != null ? cfg.xDigits : 0;
    var _yTOff = cfg.yTickXOffset != null ? cfg.yTickXOffset : -8;
    var _xTOff = cfg.xTickYOffset != null ? cfg.xTickYOffset : 20;
    var _xTickMode = cfg.xTickMode || "integer";
    (function() {
      function xF(v) { return pad.left + ((v - xDom[0]) / (xDom[1] - xDom[0] || 1)) * Pw; }
      function yF(v) { return pad.top + (1 - ((v - yDom[0]) / (yDom[1] - yDom[0] || 1))) * Ph; }
      function y2F(v) { var d = y2Dom || [0, 1]; return pad.top + (1 - ((v - d[0]) / (d[1] - d[0] || 1))) * Ph; }
      /* Y axis: nice-number ticks */
      var yNice = energyChartNiceTicks(yDom[0], yDom[1], 5, false);
      for (var gi = 0; gi < yNice.values.length; gi += 1) {
        var gy = yNice.values[gi];
        var py = yF(gy);
        svg += '<line class="' + gridClass + '" x1="' + pad.left + '" y1="' + py + '" x2="' + (W - pad.right) + '" y2="' + py + '"></line>';
        svg += '<text class="' + axisTextClass + '" x="' + (pad.left + _yTOff) + '" y="' + (py + 4) + '" text-anchor="end">' + escapeHtml(energyGraphFormatNumber(gy, _yDig)) + '</text>';
      }
      /* Y2 axis: nice-number ticks */
      if (y2Dom) {
        var y2Nice = energyChartNiceTicks(y2Dom[0], y2Dom[1], 5, false);
        for (var ri = 0; ri < y2Nice.values.length; ri += 1) {
          var rv = y2Nice.values[ri];
          svg += '<text class="' + axisTextClass + ' right" x="' + (W - pad.right + 8) + '" y="' + (y2F(rv) + 4) + '" text-anchor="start">' + escapeHtml(energyGraphFormatNumber(rv, 4)) + '</text>';
        }
        svg += '<line class="' + axisLineClass + '" x1="' + (W - pad.right) + '" y1="' + pad.top + '" x2="' + (W - pad.right) + '" y2="' + (H - pad.bottom) + '"></line>';
      }
      /* X axis: nice-number or integer ticks */
      var xIntMode = _xTickMode === "integer";
      var xNice = energyChartNiceTicks(xDom[0], xDom[1], 5, xIntMode);
      var xDigits = xNice.step >= 1 ? 0 : Math.max(_xDig, 3);
      for (var xi = 0; xi < xNice.values.length; xi += 1) {
        var xv = xNice.values[xi];
        var xA = xi === 0 ? "start" : xi === xNice.values.length - 1 ? "end" : "middle";
        var xP = xi === 0 ? xF(xv) + 2 : xi === xNice.values.length - 1 ? xF(xv) - 2 : xF(xv);
        svg += '<text class="' + axisTextClass + '" x="' + xP + '" y="' + (H - pad.bottom + _xTOff) + '" text-anchor="' + xA + '">' + escapeHtml(energyGraphFormatNumber(xv, xDigits)) + '</text>';
      }
      svg += '<line class="' + axisLineClass + '" x1="' + pad.left + '" y1="' + pad.top + '" x2="' + pad.left + '" y2="' + (H - pad.bottom) + '"></line>';
      svg += '<line class="' + axisLineClass + '" x1="' + pad.left + '" y1="' + (H - pad.bottom) + '" x2="' + (W - pad.right) + '" y2="' + (H - pad.bottom) + '"></line>';
      if (cfg.unitLabels) {
        if (cfg.unitLabels.left) svg += '<text class="' + axisTextClass + '" x="' + (pad.left - 8) + '" y="' + (pad.top - 9) + '" text-anchor="end">' + escapeHtml(cfg.unitLabels.left) + '</text>';
        if (cfg.unitLabels.right) svg += '<text class="' + axisTextClass + ' right" x="' + (W - pad.right + 8) + '" y="' + (pad.top - 9) + '" text-anchor="start">' + escapeHtml(cfg.unitLabels.right) + '</text>';
      }
    })();
  }
  /* plot group */
  svg += '<g ' + plotAttr + ' clip-path="url(#' + cfg.clipId + ')">';
  /* zero baseline (inside plot group) */
  if (cfg.zeroBaseline && yDom[0] < 0 && yDom[1] > 0) {
    var y0 = pad.top + (1 - ((0 - yDom[0]) / (yDom[1] - yDom[0] || 1))) * Ph;
    svg += '<line class="' + zeroLineClass + '" x1="' + pad.left + '" y1="' + y0 + '" x2="' + (W - pad.right) + '" y2="' + y0 + '"></line>';
  }
  /* threshold lines (inside plot group) */
  if (cfg.thresholds) {
    for (var ti = 0; ti < cfg.thresholds.length; ti += 1) {
      var th = cfg.thresholds[ti];
      svg += '<line class="' + th.lineClass + '" x1="' + pad.left + '" y1="' + th.y + '" x2="' + (W - pad.right) + '" y2="' + th.y + '"></line>';
      svg += '<text class="' + th.labelClass + '" x="' + (W - pad.right - 4) + '" y="' + (th.y - 4) + '" text-anchor="end">' + escapeHtml(th.label) + '</text>';
    }
  }
  /* multi-series paths (optimization) */
  if (cfg.series) {
    for (var si = 0; si < cfg.series.length; si += 1) {
      var s = cfg.series[si];
      if (s.linePath) svg += '<path class="' + s.cssClass + '" d="' + s.linePath + '"></path>';
    }
  }
  /* area + line (single series, energy) */
  if (cfg.areaPath) svg += '<path class="energy-area" d="' + cfg.areaPath + '"></path>';
  if (cfg.linePath) svg += '<path class="energy-line" d="' + cfg.linePath + '"></path>';
  /* selected guide (inside plot group for energy, after overlay for optimization) */
  if (cfg.selectedInPlotGroup !== false && cfg.selectedGuide) {
    svg += '<line class="' + selectedLineClass + '" x1="' + cfg.selectedGuide.x + '" y1="' + cfg.selectedGuide.y1 + '" x2="' + cfg.selectedGuide.x + '" y2="' + cfg.selectedGuide.y2 + '"></line>';
  }
  /* dot layer: per-point <g> wrapping hit circle + visible circle */
  var pts = cfg.points || [];
  var hitClass = pointClass + "-hit";
  var groupClass = pointClass + "-group";
  for (var pi = 0; pi < pts.length; pi += 1) {
    var p = pts[pi];
    var frameStr = escapeHtml(String(p.frameIndex));
    var nodeStr = escapeHtml(String(p.nodeId));
    var titleStr = p.title != null ? p.title : (p.titleText || "");
    svg += '<g class="' + groupClass + '" data-energy-frame="' + frameStr + '" data-energy-node="' + nodeStr + '">';
    if (titleStr) svg += '<title>' + escapeHtml(titleStr) + '</title>';
    /* hit circle (transparent, large r) */
    svg += '<circle class="' + hitClass + '" cx="' + p.cx + '" cy="' + p.cy + '" r="10"></circle>';
    /* visible circle */
    if (p.title != null) {
      /* optimization style */
      svg += '<circle class="' + pointClass + p.cssClass + '" cx="' + p.cx + '" cy="' + p.cy + '" r="' + p.r + '"';
      if (p.fill) svg += ' fill="' + p.fill + '"';
      if (p.stroke) svg += ' stroke="' + p.stroke + '"';
      if (p.strokeWidth) svg += ' stroke-width="' + p.strokeWidth + '"';
      svg += '></circle>';
    } else {
      /* energy style */
      svg += '<circle class="' + pointClass + p.cssClass + '" cx="' + p.cx + '" cy="' + p.cy + '" r="' + p.r + '"></circle>';
    }
    svg += '</g>';
  }
  /* annotation layer (inside plot group for energy) */
  var _annResult = { svg: "", bandSvg: "", bandHeight: 0, plotSvg: "" };
  if (cfg.annotationsInPlotGroup !== false) {
    _annResult = renderTrajectoryAnnotations(cfg.annotationOpts);
    svg += _annResult.svg;
    svg += _annResult.plotSvg;
  }
  svg += '</g>';
  /* overlay */
  svg += '<g ' + overlayAttr + '></g>';
  /* selected guide (after overlay for optimization) */
  if (cfg.selectedInPlotGroup === false && cfg.selectedGuide) {
    svg += '<line class="' + selectedLineClass + '" x1="' + cfg.selectedGuide.x + '" y1="' + cfg.selectedGuide.y1 + '" x2="' + cfg.selectedGuide.x + '" y2="' + cfg.selectedGuide.y2 + '"></line>';
  }
  /* annotation layer (after overlay for optimization) */
  if (cfg.annotationsInPlotGroup === false) {
    _annResult = renderTrajectoryAnnotations(cfg.annotationOpts);
    svg += _annResult.svg;
    svg += _annResult.plotSvg;
  }
  /* axes after plot group (energy) */
  if (!cfg.axesBeforePlot) {
    svg += '<g ' + axesAttr + '>';
    svg += energyGraphAxesMarkup(xDom, yDom, geom, cfg.xTickMode || "auto");
    svg += '<text class="' + axisTextClass + '" x="' + ((pad.left + W - pad.right) / 2) + '" y="' + (H - 17) + '" text-anchor="middle">' + escapeHtml(cfg.xLabel) + (cfg.xUnit ? ' (' + escapeHtml(cfg.xUnit) + ')' : '') + '</text>';
    svg += '<text class="' + axisTextClass + '" x="18" y="' + (H / 2) + '" text-anchor="middle" transform="rotate(-90 18 ' + (H / 2) + ')">' + escapeHtml(cfg.yLabel) + (cfg.yUnit ? ' (' + escapeHtml(cfg.yUnit) + ')' : '') + '</text>';
    svg += '</g>';
  }
  /* bottom label (optimization) */
  if (cfg.bottomLabel) {
    svg += '<text class="' + axisTextClass + '" x="' + ((pad.left + W - pad.right) / 2) + '" y="' + (H - 8) + '" text-anchor="middle">' + escapeHtml(cfg.bottomLabel) + '</text>';
  }
  /* candidate band (outside clip, above plot in pad.top area) */
  if (_annResult.bandSvg) {
    svg += _annResult.bandSvg;
  }
  svg += '</svg>';
  return svg;
}

function energyGraphBuildSvg(data) {
  var series = energyGraphSeries(data);
  var nodes = Array.isArray(data.nodes) ? data.nodes : [];
  if (!series || !nodes.length) return '<div class="energy-empty"><div><strong>暂无能量曲线</strong><span>当前任务还没有可用的曲线或结构帧。</span></div></div>';
  var currentXMode = energyGraphState.xAxisMode || (data.metadata && data.metadata.default_x_axis_mode) || (data.x_axis && data.x_axis.default_mode) || "scanned_coordinate";
  var values = Array.isArray(series.values) ? series.values : [];
  var xValues = Array.isArray(series.x_values) && series.x_values.length
    ? series.x_values
    : nodes.map(function(node) { return energyGraphNodeX(node, currentXMode); });
  var points = [];
  for (var pi = 0; pi < Math.min(values.length, xValues.length); pi += 1) {
    var pxv = energyGraphValue(xValues[pi]);
    var pyv = energyGraphValue(values[pi]);
    if (pxv !== null && pyv !== null) points.push({ x: pxv, y: pyv, index: pi, node: nodes[pi] });
  }
  if (!points.length) return '<div class="energy-empty"><div><strong>暂无可绘制数据</strong><span>曲线数据正在生成，完成后会自动刷新。</span></div></div>';
  var geom = energyGraphChartGeometry();
  var W = geom.W, H = geom.H, pad = geom.pad;
  /* ── Pre-compute candidate band height and adjust pad.top ── */
  var _preAnnotations = Array.isArray(data.annotations) ? data.annotations : [];
  var _preProjected = energyProjectCandidatesAsAnnotations(data);
  var _preMerged = _preAnnotations.concat(_preProjected);
  var _preDeduped = [];
  var _preSeen = {};
  _preMerged.forEach(function(a) { if (a && a.id && !_preSeen[a.id]) { _preSeen[a.id] = true; _preDeduped.push(a); } });
  var _preCandidates = _preDeduped.filter(function(a) { return a && (a.type === "ts" || a.type === "intermediate"); });
  var _preItems = [];
  _preCandidates.forEach(function(a) {
    var fi = a.frame_index;
    var annNode = nodes.find(function(item) { return Number(item.frame_index) === Number(fi); });
    var dataX = annNode ? energyGraphNodeX(annNode, currentXMode) : null;
    if (dataX === null) return;
    var dataY = annNode ? (energyGraphValue(annNode.energy) || energyGraphValue(a.y) || 0) : 0;
    _preItems.push({ annotation: a, dataX: dataX, dataY: dataY, frameIndex: fi, type: String(a.type || "") });
  });
  _preItems.sort(function(a, b) { return a.dataX - b.dataX; });
  /* Temporary xFor using base domain (pad.top doesn't affect x mapping) */
  var _preXExtent = [Math.min.apply(null, points.map(function(p) { return p.x; })), Math.max.apply(null, points.map(function(p) { return p.x; }))];
  var _preXSpread = Math.max(1e-9, _preXExtent[1] - _preXExtent[0]);
  var _preBaseX = [_preXExtent[0] - _preXSpread * 0.06, _preXExtent[1] + _preXSpread * 0.06];
  var _prePw = W - pad.left - pad.right;
  function _preXFor(value) { return pad.left + ((value - _preBaseX[0]) / (_preBaseX[1] - _preBaseX[0] || 1)) * _prePw; }
  var bandHeight = energyCandidateBandPreHeight(_preItems, _preXFor, pad.left, W - pad.right);
  if (bandHeight > 0) {
    pad.top += bandHeight;
    geom.pad.top = pad.top;
    H = geom.H;
  }
  var layout = energyGraphState.layout;
  if (!layout || layout._seriesId !== energyGraphState.seriesId || layout._jobId !== energyGraphState.jobId || layout._xAxisMode !== currentXMode) {
    var xExtent = [Math.min.apply(null, points.map(function(p) { return p.x; })), Math.max.apply(null, points.map(function(p) { return p.x; }))];
    var validPoints = points.filter(function(p) { return !p.node || p.node.status !== "failed"; });
    var yTargetPoints = validPoints.length ? validPoints : points;
    var yExtent = [Math.min.apply(null, yTargetPoints.map(function(p) { return p.y; })), Math.max.apply(null, yTargetPoints.map(function(p) { return p.y; }))];
    var xSpread = Math.max(1e-9, xExtent[1] - xExtent[0]);
    var ySpread = Math.max(1e-9, yExtent[1] - yExtent[0]);
    var baseX = [xExtent[0] - xSpread * 0.06, xExtent[1] + xSpread * 0.06];
    var baseY = [yExtent[0] - ySpread * 0.12, yExtent[1] + ySpread * 0.12];
    layout = { points: points, baseX: baseX, baseY: baseY, _seriesId: energyGraphState.seriesId, _jobId: energyGraphState.jobId, _xAxisMode: currentXMode, allAnnotations: null };
    energyGraphState.layout = layout;
  }
  var Pw = W - pad.left - pad.right, Ph = H - pad.top - pad.bottom;
  var xDom = Array.isArray(energyGraphState.xDomain) ? energyGraphState.xDomain : layout.baseX;
  var yDom = Array.isArray(energyGraphState.yDomain) ? energyGraphState.yDomain : layout.baseY;
  energyGraphState.currentDomain = { x: xDom.slice(), y: yDom.slice() };
  /* ── Dynamic padLeft: prevent Y-axis title/tick overlap ── */
  (function() {
    var maxTickW = 0;
    var yNice = energyChartNiceTicks(yDom[0], yDom[1], 5, false);
    for (var ti = 0; ti < yNice.values.length; ti += 1) {
      var tw = energyGraphMeasureText(energyGraphFormatNumber(yNice.values[ti], 2), 11, false);
      if (tw > maxTickW) maxTickW = tw;
    }
    /* title at x=18 rotated occupies ~11px in x-direction (font-size); tick text
       at pad.left-12 anchor=end extends left by maxTickW.
       Need: pad.left - 12 - maxTickW > 18 + 6 + 4  (title right + gap) */
    var needed = Math.ceil(40 + maxTickW);
    if (needed > pad.left) {
      pad.left = Math.min(130, needed);
      geom.pad.left = pad.left;
      geom.plotRect.left = pad.left;
      Pw = W - pad.left - pad.right;
    }
  })();
  energyGraphState._cachedGeom = geom;
  function xFor(v) { return pad.left + ((v - xDom[0]) / (xDom[1] - xDom[0] || 1)) * Pw; }
  function yFor(v) { return pad.top + (1 - ((v - yDom[0]) / (yDom[1] - yDom[0] || 1))) * Ph; }
  var visiblePoints = points.filter(function(p) { return p.x >= xDom[0] && p.x <= xDom[1] && p.y >= yDom[0] && p.y <= yDom[1]; });
  /* ── (b) Continuous-segment path drawing: break at hidden frames ── */
  var segments = [];
  var seg = null;
  for (var si = 0; si < visiblePoints.length; si += 1) {
    if (!seg) { seg = [visiblePoints[si]]; }
    else if (visiblePoints[si].index === seg[seg.length - 1].index + 1) { seg.push(visiblePoints[si]); }
    else { segments.push(seg); seg = [visiblePoints[si]]; }
  }
  if (seg) segments.push(seg);
  var linePath = "";
  var areaPath = "";
  segments.forEach(function(s) {
    var cmds = s.map(function(pt, idx) { return (idx ? "L" : "M") + xFor(pt.x) + " " + yFor(pt.y); }).join(" ");
    linePath += cmds + " ";
    if (s.length >= 2) {
      areaPath += cmds + " L " + xFor(s[s.length - 1].x) + " " + yFor(yDom[0]) + " L " + xFor(s[0].x) + " " + yFor(yDom[0]) + " Z ";
    }
  });
  /* ── Selected frame guide line (no halo) ── */
  var selectedGuide = null;
  var selectedFrame = energyGraphFrameIndexForNode(data, energyGraphState.selectedNodeId);
  if (selectedFrame != null) {
    var selNode = nodes.find(function(node) { return String(node.id) === String(energyGraphState.selectedNodeId); })
      || nodes.find(function(node) { return Number(node.frame_index) === selectedFrame; });
    if (selNode) {
      var selX = energyGraphNodeX(selNode, currentXMode);
      if (selX !== null) {
        var selInDomain = selX >= xDom[0] && selX <= xDom[1];
        if (selInDomain) {
          selectedGuide = { x: xFor(selX), y1: pad.top, y2: H - pad.bottom };
        }
      }
    }
  }
  /* ── Points config ── */
  var seriesLabel = series ? (series.label || series.id || t("energy.header.status.unknown")) : t("energy.header.status.unknown");
  var seriesUnit = series && series.unit ? series.unit : "";
  var pointCfgs = [];
  points.forEach(function(point) {
    var nodeId = point.node && point.node.id != null ? point.node.id : "";
    var selected = String(nodeId) === String(energyGraphState.selectedNodeId) ? " selected" : "";
    var failed = point.node && point.node.status === "failed" ? " failed" : "";
    var frameIdx = point.node && point.node.frame_index != null ? point.node.frame_index : point.index;
    var energyVal = energyGraphValue(point.y);
    var energyStr = energyVal !== null ? energyGraphFormatNumber(energyVal, 2) : "--";
    var unitStr = seriesUnit ? " " + seriesUnit : "";
    var titleText = t("energy.point.title", { n: frameIdx, series: seriesLabel, energy: energyStr, unit: unitStr });
    pointCfgs.push({
      cx: xFor(point.x), cy: yFor(point.y),
      r: selected ? 6 : 4,
      cssClass: selected + failed,
      frameIndex: frameIdx,
      nodeId: nodeId,
      titleText: titleText,
    });
  });
  /* ── IRC merged view: colour the two directions separately ── */
  var ircLines = null;
  if (String(data.view_type || "") === "irc" && String(series && series.id || "") === "irc_path") {
    var reversePts = [], forwardPts = [], tsPoint = null;
    visiblePoints.forEach(function(pt) {
      var dir = pt.node && pt.node.metadata ? String(pt.node.metadata.direction || "") : "";
      if (dir === "reverse") reversePts.push(pt);
      else if (dir === "forward") forwardPts.push(pt);
      else if (dir === "ts") tsPoint = pt;
    });
    function ircCmds(seq) {
      return seq.map(function(pt, idx) { return (idx ? "L" : "M") + xFor(pt.x) + " " + yFor(pt.y); }).join(" ");
    }
    ircLines = [];
    var reverseSeq = reversePts.concat(tsPoint ? [tsPoint] : []);
    var forwardSeq = (tsPoint ? [tsPoint] : []).concat(forwardPts);
    if (reverseSeq.length > 1) ircLines.push({ cssClass: "energy-line energy-line-reverse", linePath: ircCmds(reverseSeq) });
    if (forwardSeq.length > 1) ircLines.push({ cssClass: "energy-line energy-line-forward", linePath: ircCmds(forwardSeq) });
    if (!ircLines.length) ircLines = null;
  }
  /* ── Annotations: merge backend + projected candidates, delegate to shared renderer ── */
  var annotations = Array.isArray(data.annotations) ? data.annotations : [];
  var projectedCandidates = energyProjectCandidatesAsAnnotations(data);
  var mergedAnnotations = annotations.concat(projectedCandidates);
  var deduped = [];
  var seenIds = {};
  mergedAnnotations.forEach(function(a) {
    if (!a || !a.id) return;
    if (seenIds[a.id]) return;
    seenIds[a.id] = true;
    deduped.push(a);
  });
  /* ── SVG open tag ── */
  var svgOpen = '<svg class="energy-chart-svg' + (energyGraphState.viewMode === "box" ? " mode-box" : "") + (energyGraphState.viewMode === "pan" ? " mode-pan" : "") + (energyGraphState.annotationMode === "hide" ? " labels-hidden" : "") + '" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + escapeHtml(String(data.title || "能量与轨迹")) + '" data-energy-chart>';
  var activeXLabel = String((data.x_axis || {}).label || "坐标");
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
  /* ── Build SVG via unified core ── */
  return energyChartBuildSvg({
    svgOpen: svgOpen,
    clipId: "energy-plot-clip",
    geom: geom,
    xDom: xDom, yDom: yDom,
    linePath: ircLines ? "" : linePath,
    areaPath: ircLines ? "" : areaPath,
    series: ircLines || undefined,
    selectedGuide: selectedGuide,
    points: pointCfgs,
    annotationOpts: {
      annotations: deduped,
      nodes: nodes,
      xFor: xFor,
      yFor: yFor,
      geom: geom,
      xDom: xDom,
      yDom: yDom,
      mode: "full",
      viewType: "energy",
      xAxisMode: currentXMode,
      selectedNodeId: String(energyGraphState.selectedNodeId || ""),
      selectionLocked: !!s2scanState.selectionLocked,
    },
    xLabel: activeXLabel,
    xUnit: activeXUnit,
    yLabel: String(series.label || "能量"),
    yUnit: String(series.unit || ""),
  });
}

function energyGraphSelectedNode(data) {
  const nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
  let node = nodes.find(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); });
  if (!node) {
    const annotation = (data.annotations || []).find(function(item) { return item.selected && item.frame_index != null; }) || (data.annotations || []).find(function(item) { return ["ts", "intermediate"].indexOf(item.type) >= 0; });
    if (annotation) node = nodes.find(function(item) { return Number(item.frame_index) === Number(annotation.frame_index); });
  }
  return node || nodes[0] || null;
}

function energyGraphSelectedAnnotation(data, node) {
  if (!data) return null;
  return (data.annotations || []).find(function(item) { return String(item.id) === String(energyGraphState.selectedAnnotationId); })
    || (data.annotations || []).find(function(item) { return node && Number(item.frame_index) === Number(node.frame_index) && ["ts", "intermediate"].indexOf(item.type) >= 0; })
    || null;
}

function energyGraphLegendMarkup(data) {
  var items = '';
  var annotations = (data && data.annotations) || [];
  var nodes = (data && data.nodes) || [];
  var hasTS = annotations.some(function(a) { return a.type === 'ts'; });
  var hasINT = annotations.some(function(a) { return a.type === 'intermediate'; });
  var hasMin = annotations.some(function(a) { return a.type === 'minimum'; });
  var hasFailed = nodes.some(function(n) { return n.status === 'failed'; });
  if (hasTS) items += '<span class="energy-legend-item"><i class="energy-legend-swatch ts"></i>TS 候选</span>';
  if (hasINT) items += '<span class="energy-legend-item"><i class="energy-legend-swatch int"></i>中间体</span>';
  if (hasMin) items += '<span class="energy-legend-item"><i class="energy-legend-swatch min"></i>最低能量</span>';
  if (hasFailed) items += '<span class="energy-legend-item"><i class="energy-legend-swatch failed"></i>未收敛</span>';
  if (!items) return '';
  return '<div class="energy-legend">' + items + '</div>';
}
  return {
    init: init,
    energyGraphSeries,
    energyGraphValue,
    energyGraphFormatNumber,
    energyGraphCandidateAnnotations,
    energyGraphFrameIndexForNode,
    energyGraphSeriesValueAtFrame,
    energyGraphMeasureText,
    energyGraphChartGeometry,
    energyChartNiceTicks,
    energyGraphAxesMarkup,
    energyGraphNodeX,
    energyProjectCandidatesAsAnnotations,
    energyCandidateBandLayoutCore,
    energyCandidateBandLayout,
    energyCandidateBandPreHeight,
    energyGraphNodeForAnnotation,
    energyCandidateListMarkup,
    energyChartBuildSvg,
    energyGraphBuildSvg,
    energyGraphSelectedNode,
    energyGraphSelectedAnnotation,
    energyGraphLegendMarkup,
  };
})();
