/*
 * ACP 3.0 experiment-entry module — energy-profile (Track A / A-38).
 *
 * Classic script, no build. Consumes the A-00 `acp.view.v1` energy envelope
 * (through view_source.js) and renders with the ONE canonical energy chart
 * renderer (js/visualization/energy_chart_renderer.js). This module never
 * fetches directly, never re-implements chart geometry, and remains read-only:
 * the only frame action is “save as candidate”, which belongs to A-40 and is
 * NOT wired here yet.
 *
 * States: empty / loading / ready / error (+ denied mapped to error). Fixture
 * data is always visibly badged. Late async results carry a generation guard.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "energy-profile";
  var DEFAULT_API_BASE = "/api/v1";

  function modelApi() {
    var model = window.ACPView && window.ACPView.model;
    if (!model) throw new Error("ACPView.model 未加载（检查 view_model.js 脚本顺序）");
    return model;
  }

  function sourceApi() {
    var source = window.ACPView && window.ACPView.source;
    if (!source) throw new Error("ACPView.source 未加载（检查 view_source.js 脚本顺序）");
    return source;
  }

  function translate(key, vars) {
    var fallback = {
      "energy.header.status.unknown": "未知",
      "energy.point.title": "帧 {n} · {series} {energy}{unit}",
      "energy.band.frame": "第 {n} 帧",
      "energy.band.overflow": "另有 {count} 个",
      "energy.band.tooltip_frame": "第 {n} 帧",
      "energy.band.tooltip_index": "frame_index {n}",
      "energy.band.tooltip_step": "扫描步 {n}",
      "energy.band.tooltip_coord": "坐标 {val}{unit}",
      "energy.band.tooltip_energy": "能量 {val}",
      "energy.band.tooltip_saved": "已保存候选",
      "energy.band.tooltip_unsaved": "未保存",
      "energy.band.missing_node": "缺少对应节点"
    };
    var template = key;
    if (window.ACPShell && window.ACPShell.i18n && typeof window.ACPShell.i18n.t === "function") {
      var value = window.ACPShell.i18n.t(key);
      if (value && value !== key) template = value;
    }
    if (template === key && fallback[key]) template = fallback[key];
    if (!vars) return template;
    return String(template).replace(/\{(\w+)\}/g, function (match, name) {
      return vars[name] == null ? "" : String(vars[name]);
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function clearChildren(el) {
    if (!el) return;
    if (typeof el.replaceChildren === "function") el.replaceChildren();
    else el.textContent = "";
  }

  function finite(value) {
    return typeof value === "number" && isFinite(value);
  }

  /**
   * Project the normalized energy view back to the canonical renderer's
   * legacy input (`nodes` + `series.values/x_values`). This is a display
   * projection only; no backend field is exposed outside the module.
   */
  function toRendererData(view) {
    var series = Array.isArray(view && view.series) ? view.series : [];
    var frames = Array.isArray(view && view.frames) ? view.frames : [];
    var firstPoints = series.length && Array.isArray(series[0].points) ? series[0].points : [];

    var nodes;
    if (frames.length) {
      nodes = frames.map(function (frame, index) {
        var point = firstPoints[index] || {};
        return {
          id: point.nodeId || ("n" + index),
          frame_index: finite(frame.index) ? frame.index : index,
          energy: finite(frame.energy) ? frame.energy : (finite(point.y) ? point.y : null),
          status: frame.status || "",
          x: finite(point.x) ? point.x : null
        };
      });
    } else if (firstPoints.length) {
      nodes = firstPoints.map(function (point, index) {
        return {
          id: point.nodeId || ("n" + index),
          frame_index: finite(point.frameIndex) ? point.frameIndex : index,
          energy: finite(point.y) ? point.y : null,
          status: "",
          x: finite(point.x) ? point.x : null
        };
      });
    } else {
      nodes = [];
    }

    var provenance = view && view.provenance ? view.provenance : {};
    var axes = view && view.axes ? view.axes : null;
    return {
      view_type: provenance.viewType || "energy",
      title: translate("next.energy.title"),
      x_axis: {
        label: axes && axes.x ? axes.x.label : "",
        unit: axes && axes.x ? axes.x.unit : ""
      },
      metadata: { default_x_axis_mode: provenance.xAxisMode || "scanned_coordinate" },
      series: series.map(function (item) {
        return {
          id: item.id,
          label: item.label,
          unit: item.unit,
          values: (item.points || []).map(function (point) { return point.y; }),
          x_values: (item.points || []).map(function (point) { return point.x; })
        };
      }),
      nodes: nodes,
      annotations: (view && Array.isArray(view.annotations) ? view.annotations : []).map(function (annotation) {
        var payload = annotation.payload && typeof annotation.payload === "object" ? annotation.payload : {};
        return Object.assign({}, payload, {
          id: annotation.id,
          type: annotation.kind,
          label: annotation.label,
          x: annotation.x,
          y: annotation.y
        });
      }),
      default_series: provenance.defaultSeries || (series[0] ? series[0].id : "")
    };
  }

  function createInstance(deps) {
    deps = deps || {};
    var context = deps.context || null;
    var source = null;
    var renderer = null;

    var alive = false;
    var mounted = false;
    var suspended = false;
    var host = null;
    var root = null;
    var chartEl = null;
    var overlayEl = null;
    var msgEl = null;
    var detailEl = null;
    var retryBtn = null;
    var titleEl = null;
    var metaEl = null;
    var fixtureBadge = null;
    var warnEl = null;
    var candidateBar = null;
    var candidateFrameEl = null;
    var candidateRoleTs = null;
    var candidateRoleInt = null;
    var candidateSaveBtn = null;
    var candidateStatusEl = null;
    var candidateToggle = null;
    var candidateDrawer = null;
    var candidateDrawerStatus = null;
    var candidateDrawerList = null;
    var candidateDrawerDetail = null;
    var selectedCandidateId = null;
    var currentTaskKey = "__unset__";
    var generation = 0;
    var candidateGeneration = 0;
    var candidateRevision = 0;
    var candidateState = "idle";
    var selectedFrameIndex = null;
    var selectedRole = "ts";
    var lastView = null;
    var lastRendererData = null;
    var unsubscribeContext = null;
    var graphState = {
      xAxisMode: "scanned_coordinate",
      selectedNodeId: "",
      selectedAnnotationId: "",
      seriesId: "",
      jobId: "",
      layout: null,
      xDomain: null,
      yDomain: null,
      viewSize: { width: 0, height: 0 },
      frameCandidates: [],
      candidateListFilter: "all"
    };
    var s2State = { selectionLocked: false, candidates: [], dirty: false };

    var COPY = {
      empty: { text: "暂无能量曲线", i18n: "next.energy.empty" },
      loading: { text: "能量曲线加载中…", i18n: "next.energy.loading" },
      ready: { text: "", i18n: "next.energy.ready" },
      error: { text: "能量曲线加载失败", i18n: "next.energy.errorTitle" }
    };

    function ensureSource() {
      if (source) return source;
      if (deps.viewSource && typeof deps.viewSource.load === "function") {
        source = deps.viewSource;
        return source;
      }
      source = sourceApi().create({
        mode: deps.viewMode,
        baseUrl: deps.apiBase || DEFAULT_API_BASE,
        fetch: deps.fetch,
        fixtures: deps.fixtures,
        location: window.location,
        storage: window.localStorage
      });
      return source;
    }

    function candidateClient() {
      if (deps.candidateApi) return deps.candidateApi;
      if (window.ACPClient && window.ACPClient.frameCandidates) return window.ACPClient.frameCandidates;
      return null;
    }

    function renderAnnotations(opts) {
      if (!opts || !renderer) return { svg: "", bandSvg: "", bandHeight: 0, plotSvg: "" };
      var items = [];
      (Array.isArray(opts.annotations) ? opts.annotations : []).forEach(function (annotation) {
        if (!annotation || (annotation.type !== "ts" && annotation.type !== "intermediate")) return;
        var node = renderer.energyGraphNodeForAnnotation({ nodes: opts.nodes || [] }, annotation);
        var dataX = node ? renderer.energyGraphNodeX(node, opts.xAxisMode) : annotation.x;
        var dataY = node ? node.energy : annotation.y;
        if (!finite(dataX) || !finite(dataY)) return;
        var frameIndex = annotation.frame_index != null ? Number(annotation.frame_index) :
          (node && node.frame_index != null ? Number(node.frame_index) : null);
        if (!finite(frameIndex)) return;
        items.push({
          annotation: annotation,
          dataX: dataX,
          dataY: dataY,
          frameIndex: frameIndex,
          type: annotation.type
        });
      });
      if (!items.length) return { svg: "", bandSvg: "", bandHeight: 0, plotSvg: "" };
      var band = renderer.energyCandidateBandLayout(
        items, opts.geom, opts.xFor, opts.yFor, { selectionLocked: true }
      );
      return { svg: "", bandSvg: band.bandSvg, bandHeight: band.bandHeight, plotSvg: band.plotSvg };
    }

    function ensureRenderer() {
      if (renderer) return renderer;
      renderer = window.ACPVisualization && window.ACPVisualization.energyChart;
      if (!renderer || typeof renderer.energyGraphBuildSvg !== "function") {
        throw new Error("ACPVisualization.energyChart 未加载（检查 energy_chart_renderer.js 脚本顺序）");
      }
      renderer.init({
        t: translate,
        energyGraphState: graphState,
        s2scanState: s2State,
        currentLang: window.ACPShell && window.ACPShell.i18n &&
          typeof window.ACPShell.i18n.getLang === "function" ? window.ACPShell.i18n.getLang() : "zh-CN",
        translateStatus: function (status) { return String(status || ""); },
        escapeHtml: escapeHtml,
        energyGenCandidateName: function (frameIndex) { return "candidate_" + frameIndex; },
        energyGraphFrameCandidateFor: function () { return null; },
        energyInspectorMetadataRows: function () { return ""; },
        renderTrajectoryAnnotations: renderAnnotations
      });
      return renderer;
    }

    function buildDom() {
      var doc = window.document;
      if (!doc || typeof doc.createElement !== "function") throw new Error("energy-profile: document 不可用");
      root = doc.createElement("div");
      root.className = "acp-mod-energy";
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-acp-fixture", "false");

      var head = doc.createElement("div");
      head.className = "acp-mod-energy-head";
      titleEl = doc.createElement("h2");
      titleEl.className = "acp-mod-energy-title";
      titleEl.setAttribute("data-i18n", "next.energy.title");
      titleEl.textContent = "能量曲线";
      metaEl = doc.createElement("span");
      metaEl.className = "acp-mod-energy-meta";
      head.appendChild(titleEl);
      head.appendChild(metaEl);
      candidateToggle = doc.createElement("button");
      candidateToggle.type = "button";
      candidateToggle.className = "acp-mod-energy-candidates-toggle";
      candidateToggle.setAttribute("data-i18n", "next.energy.candidates");
      candidateToggle.textContent = "候选";
      candidateToggle.addEventListener("click", function () {
        if (!candidateDrawer) return;
        candidateDrawer.hidden = !candidateDrawer.hidden;
        if (!candidateDrawer.hidden) renderCandidateDrawer();
      });
      head.appendChild(candidateToggle);
      root.appendChild(head);

      chartEl = doc.createElement("div");
      chartEl.className = "acp-mod-energy-chart";
      chartEl.addEventListener("click", onChartClick);
      root.appendChild(chartEl);

      candidateBar = doc.createElement("div");
      candidateBar.className = "acp-mod-energy-candidate-bar";
      candidateBar.setAttribute("data-acp-candidate-state", "idle");
      candidateBar.hidden = true;
      candidateFrameEl = doc.createElement("span");
      candidateFrameEl.className = "acp-mod-energy-candidate-frame";
      candidateRoleTs = doc.createElement("button");
      candidateRoleTs.type = "button";
      candidateRoleTs.className = "acp-mod-energy-candidate-role";
      candidateRoleTs.setAttribute("data-energy-candidate-role", "ts");
      candidateRoleTs.textContent = "TS";
      candidateRoleTs.addEventListener("click", function () { selectRole("ts"); });
      candidateRoleInt = doc.createElement("button");
      candidateRoleInt.type = "button";
      candidateRoleInt.className = "acp-mod-energy-candidate-role";
      candidateRoleInt.setAttribute("data-energy-candidate-role", "int");
      candidateRoleInt.textContent = "INT";
      candidateRoleInt.addEventListener("click", function () { selectRole("int"); });
      candidateSaveBtn = doc.createElement("button");
      candidateSaveBtn.type = "button";
      candidateSaveBtn.className = "acp-mod-energy-candidate-save";
      candidateSaveBtn.setAttribute("data-energy-action", "save-candidate");
      candidateSaveBtn.textContent = "保存为候选";
      candidateSaveBtn.addEventListener("click", onSaveCandidate);
      candidateStatusEl = doc.createElement("span");
      candidateStatusEl.className = "acp-mod-energy-candidate-status";
      candidateStatusEl.setAttribute("role", "status");
      candidateBar.appendChild(candidateFrameEl);
      candidateBar.appendChild(candidateRoleTs);
      candidateBar.appendChild(candidateRoleInt);
      candidateBar.appendChild(candidateSaveBtn);
      candidateBar.appendChild(candidateStatusEl);
      root.appendChild(candidateBar);

      candidateDrawer = doc.createElement("aside");
      candidateDrawer.className = "acp-mod-energy-candidate-drawer";
      candidateDrawer.hidden = true;
      candidateDrawer.setAttribute("data-acp-candidate-list-state", "empty");
      var drawerHead = doc.createElement("div");
      drawerHead.className = "acp-mod-energy-candidate-drawer-head";
      var drawerTitle = doc.createElement("span");
      drawerTitle.className = "acp-mod-energy-candidate-drawer-title";
      drawerTitle.setAttribute("data-i18n", "next.energy.candidates");
      drawerTitle.textContent = "候选";
      var drawerClose = doc.createElement("button");
      drawerClose.type = "button";
      drawerClose.className = "acp-mod-energy-candidate-drawer-close";
      drawerClose.setAttribute("aria-label", "关闭候选面板");
      drawerClose.textContent = "×";
      drawerClose.addEventListener("click", function () { candidateDrawer.hidden = true; });
      drawerHead.appendChild(drawerTitle);
      drawerHead.appendChild(drawerClose);
      candidateDrawer.appendChild(drawerHead);
      candidateDrawerStatus = doc.createElement("p");
      candidateDrawerStatus.className = "acp-mod-energy-candidate-drawer-status";
      candidateDrawerStatus.setAttribute("role", "status");
      candidateDrawerStatus.hidden = true;
      candidateDrawer.appendChild(candidateDrawerStatus);
      candidateDrawerList = doc.createElement("div");
      candidateDrawerList.className = "acp-mod-energy-candidate-drawer-list";
      candidateDrawer.appendChild(candidateDrawerList);
      candidateDrawerDetail = doc.createElement("div");
      candidateDrawerDetail.className = "acp-mod-energy-candidate-drawer-detail";
      candidateDrawerDetail.hidden = true;
      candidateDrawer.appendChild(candidateDrawerDetail);
      root.appendChild(candidateDrawer);

      fixtureBadge = doc.createElement("span");
      fixtureBadge.className = "acp-mod-energy-fixture";
      fixtureBadge.setAttribute("data-i18n", "next.view.fixture");
      fixtureBadge.textContent = "演示数据（fixture）";
      fixtureBadge.hidden = true;
      root.appendChild(fixtureBadge);

      warnEl = doc.createElement("p");
      warnEl.className = "acp-mod-energy-warning";
      warnEl.setAttribute("role", "status");
      warnEl.hidden = true;
      root.appendChild(warnEl);

      overlayEl = doc.createElement("div");
      overlayEl.className = "acp-mod-energy-overlay";
      var box = doc.createElement("div");
      box.className = "acp-mod-energy-box";
      msgEl = doc.createElement("p");
      msgEl.className = "acp-mod-energy-message";
      msgEl.setAttribute("aria-live", "polite");
      detailEl = doc.createElement("p");
      detailEl.className = "acp-mod-energy-detail";
      detailEl.hidden = true;
      retryBtn = doc.createElement("button");
      retryBtn.type = "button";
      retryBtn.className = "acp-mod-energy-retry";
      retryBtn.setAttribute("data-i18n", "next.energy.retry");
      retryBtn.textContent = "重试";
      retryBtn.hidden = true;
      retryBtn.addEventListener("click", function () { loadTask(currentTaskRef()); });
      box.appendChild(msgEl);
      box.appendChild(detailEl);
      box.appendChild(retryBtn);
      overlayEl.appendChild(box);
      root.appendChild(overlayEl);
      host.appendChild(root);
    }

    function setState(name, detail) {
      if (!root) return;
      root.setAttribute("data-acp-module-state", name);
      var copy = COPY[name] || COPY.empty;
      msgEl.textContent = copy.text;
      msgEl.setAttribute("data-i18n", copy.i18n);
      if (detail) {
        detailEl.textContent = String(detail);
        detailEl.hidden = false;
      } else {
        detailEl.textContent = "";
        detailEl.hidden = true;
      }
      overlayEl.hidden = name === "ready";
      retryBtn.hidden = name !== "error";
      if (typeof updateCandidateBar === "function") updateCandidateBar();
    }

    function warningMessage(view) {
      var warnings = view && Array.isArray(view.warnings) ? view.warnings : [];
      if (!warnings.length) return "";
      var first = warnings[0] || {};
      return String(first.message || first.code || "数据不全");
    }

    function applyViewMeta(view) {
      if (!root) return;
      var fixture = !!(view && view.source === "fixture");
      root.setAttribute("data-acp-fixture", fixture ? "true" : "false");
      if (fixtureBadge) fixtureBadge.hidden = !fixture;
      var warnings = view && Array.isArray(view.warnings) ? view.warnings : [];
      root.setAttribute("data-acp-warning-count", String(warnings.length));
      if (warnEl) {
        if (!warnings.length) {
          warnEl.textContent = "";
          warnEl.hidden = true;
        } else {
          var first = warnings[0] || {};
          warnEl.textContent = "数据不全：" + String(first.message || first.code || "unknown") +
            (warnings.length > 1 ? "（另有 " + (warnings.length - 1) + " 条）" : "");
          warnEl.hidden = false;
        }
      }
      if (metaEl) {
        var firstSeries = view && Array.isArray(view.series) && view.series.length ? view.series[0] : null;
        metaEl.textContent = firstSeries ? (firstSeries.label + (firstSeries.unit ? " · " + firstSeries.unit : "")) : "";
      }
    }

    function viewTypeOf(view) {
      return view && view.provenance && view.provenance.viewType ? String(view.provenance.viewType) : "energy";
    }

    function applyCandidatePayload(payload) {
      candidateRevision = Number(payload && payload.revision) || 0;
      var list = payload && Array.isArray(payload.candidates) ? payload.candidates : [];
      graphState.frameCandidates = {
        revision: candidateRevision,
        list: list.map(function (item) {
          return {
            candidate_id: String(item.candidate_id || ""),
            view_type: String(item.view_type || ""),
            frame_index: Number(item.frame_index),
            role: String(item.role || ""),
            display_label: String(item.display_label || item.name || ""),
            name: String(item.name || ""),
            saved_at: String(item.saved_at || ""),
            structure_path: String(item.structure_path || ""),
            saved: true
          };
        })
      };
      renderCandidateDrawer();
    }

    function candidateForFrame(frameIndex) {
      var list = graphState.frameCandidates && Array.isArray(graphState.frameCandidates.list)
        ? graphState.frameCandidates.list : [];
      if (!list.length) return null;
      var viewType = viewTypeOf(lastView);
      var matches = list.filter(function (item) { return Number(item.frame_index) === Number(frameIndex); });
      return matches.find(function (item) { return item.view_type === viewType; }) || matches[0] || null;
    }

    function setCandidateState(state, message, isError) {
      candidateState = state;
      if (candidateBar) candidateBar.setAttribute("data-acp-candidate-state", state);
      if (candidateStatusEl) {
        candidateStatusEl.textContent = message || "";
        candidateStatusEl.className = "acp-mod-energy-candidate-status" + (isError ? " is-error" : "");
      }
    }

    function updateCandidateBar() {
      if (!candidateBar) return;
      var ready = root && root.getAttribute("data-acp-module-state") === "ready";
      if (!ready || selectedFrameIndex == null || !lastView) {
        candidateBar.hidden = true;
        return;
      }
      candidateBar.hidden = false;
      var frame = (lastView.frames || []).find(function (item) {
        return Number(item.index) === Number(selectedFrameIndex);
      });
      candidateFrameEl.textContent = "帧 " + (Number(selectedFrameIndex) + 1) +
        (frame && frame.label ? " · " + frame.label : "");
      var saved = candidateForFrame(selectedFrameIndex);
      var fixture = lastView.source === "fixture";
      var api = candidateClient();
      if (saved) {
        selectedRole = String(saved.role).toLowerCase() === "ts" ? "ts" : "int";
        if (candidateState !== "saving" && candidateState !== "error") {
          setCandidateState("saved", "已保存：" + (saved.candidate_id || ""));
        }
      } else if (fixture) {
        setCandidateState("idle", "演示数据不可保存");
      } else if (!api) {
        setCandidateState("idle", "候选接口未接入");
      } else if (candidateState !== "saving" && candidateState !== "error") {
        setCandidateState("idle", "");
      }
      var disabled = candidateState === "saving" || !!saved || fixture || !api;
      candidateSaveBtn.disabled = disabled;
      candidateSaveBtn.textContent = saved ? "已保存" : "保存为候选";
      candidateRoleTs.className = "acp-mod-energy-candidate-role" + (selectedRole === "ts" ? " is-active" : "");
      candidateRoleInt.className = "acp-mod-energy-candidate-role" + (selectedRole === "int" ? " is-active" : "");
      candidateRoleTs.disabled = candidateState === "saving" || !!saved;
      candidateRoleInt.disabled = candidateState === "saving" || !!saved;
      renderCandidateDrawer();
    }

    function setCandidateDrawerState(state, message, isError) {
      if (candidateDrawer) candidateDrawer.setAttribute("data-acp-candidate-list-state", state);
      if (candidateDrawerStatus) {
        candidateDrawerStatus.textContent = message || "";
        candidateDrawerStatus.hidden = !message;
        candidateDrawerStatus.className = "acp-mod-energy-candidate-drawer-status" + (isError ? " is-error" : "");
      }
    }

    function renderCandidateDrawer() {
      if (!candidateDrawer || !candidateDrawerList) return;
      var list = graphState.frameCandidates && Array.isArray(graphState.frameCandidates.list)
        ? graphState.frameCandidates.list : [];
      if (candidateToggle) candidateToggle.textContent = "候选" + (list.length ? " (" + list.length + ")" : "");
      if (candidateDrawer.hidden) return;
      clearChildren(candidateDrawerList);
      clearChildren(candidateDrawerDetail);
      if (!list.length) {
        selectedCandidateId = null;
        candidateDrawerDetail.hidden = true;
        setCandidateDrawerState("empty", "暂无候选");
        return;
      }
      if (selectedCandidateId && !list.some(function (item) { return item.candidate_id === selectedCandidateId; })) {
        selectedCandidateId = null;
      }
      setCandidateDrawerState("ready", "");
      var sorted = list.slice().sort(function (a, b) {
        return Number(a.frame_index) - Number(b.frame_index) ||
          String(a.candidate_id).localeCompare(String(b.candidate_id));
      });
      sorted.forEach(function (candidate) {
        var row = window.document.createElement("button");
        row.type = "button";
        row.className = "acp-mod-energy-candidate-row" +
          (candidate.candidate_id === selectedCandidateId ? " is-selected" : "");
        row.setAttribute("data-candidate-id", candidate.candidate_id);
        var role = window.document.createElement("span");
        role.className = "acp-mod-energy-candidate-row-role";
        role.textContent = String(candidate.role || "").toUpperCase() === "TS" ? "TS" : "INT";
        var label = window.document.createElement("span");
        label.className = "acp-mod-energy-candidate-row-label";
        label.textContent = candidate.display_label || candidate.candidate_id;
        var meta = window.document.createElement("span");
        meta.className = "acp-mod-energy-candidate-row-meta";
        meta.textContent = "帧 " + (Number(candidate.frame_index) + 1) + " · " + candidate.candidate_id;
        row.appendChild(role);
        row.appendChild(label);
        row.appendChild(meta);
        row.addEventListener("click", function () { selectCandidate(candidate); });
        candidateDrawerList.appendChild(row);
      });
      if (!selectedCandidateId) {
        candidateDrawerDetail.hidden = true;
        return;
      }
      var selected = list.find(function (item) { return item.candidate_id === selectedCandidateId; });
      if (!selected) {
        candidateDrawerDetail.hidden = true;
        return;
      }
      candidateDrawerDetail.hidden = false;
      var title = window.document.createElement("h4");
      title.className = "acp-mod-energy-candidate-detail-title";
      title.textContent = selected.display_label || selected.candidate_id;
      candidateDrawerDetail.appendChild(title);
      var fields = [
        ["Candidate ID", selected.candidate_id],
        ["角色", String(selected.role || "").toUpperCase()],
        ["帧", String(Number(selected.frame_index) + 1)],
        ["名称", selected.name || "—"],
        ["保存时间", selected.saved_at || "—"],
        ["结构路径", selected.structure_path || "—"]
      ];
      var dl = window.document.createElement("dl");
      dl.className = "acp-mod-energy-candidate-detail-fields";
      fields.forEach(function (pair) {
        var dt = window.document.createElement("dt");
        dt.textContent = pair[0];
        var dd = window.document.createElement("dd");
        dd.textContent = pair[1];
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      candidateDrawerDetail.appendChild(dl);
      var nameInput = window.document.createElement("input");
      nameInput.className = "acp-mod-energy-candidate-name";
      nameInput.setAttribute("data-energy-candidate-name", "1");
      nameInput.value = selected.name || "";
      nameInput.placeholder = "候选名称";
      candidateDrawerDetail.appendChild(nameInput);
      var rename = window.document.createElement("button");
      rename.type = "button";
      rename.className = "acp-mod-energy-candidate-rename";
      rename.setAttribute("data-energy-action", "rename-candidate");
      rename.textContent = "重命名";
      rename.addEventListener("click", function () { renameCandidate(selected, nameInput.value); });
      candidateDrawerDetail.appendChild(rename);
      var remove = window.document.createElement("button");
      remove.type = "button";
      remove.className = "acp-mod-energy-candidate-remove";
      remove.setAttribute("data-energy-action", "remove-candidate");
      remove.textContent = "删除候选";
      remove.addEventListener("click", function () { removeCandidate(selected); });
      candidateDrawerDetail.appendChild(remove);
    }

    function selectCandidate(candidate) {
      if (!candidate) return;
      selectedCandidateId = candidate.candidate_id;
      selectedRole = String(candidate.role || "").toLowerCase() === "ts" ? "ts" : "int";
      if (candidate.frame_index != null) selectFrame(candidate.frame_index, "");
      renderCandidateDrawer();
    }

    function renameCandidate(candidate, nextName) {
      if (!candidate || !lastView) return;
      var name = String(nextName == null ? "" : nextName).trim();
      if (!name || name === candidate.name) return;
      var api = candidateClient();
      if (!api || typeof api.save !== "function") {
        setCandidateDrawerState("error", "候选重命名接口未接入", true);
        return;
      }
      var jobId = lastView.provenance && lastView.provenance.jobId;
      if (!jobId) return;
      var gen = generation;
      candidateGeneration += 1;
      setCandidateDrawerState("loading", "重命名中…");
      var payload = {
        view_type: candidate.view_type,
        frame_index: candidate.frame_index,
        role: candidate.role,
        name: name,
        expected_revision: candidateRevision
      };
      if (lastView.provenance.itemId) payload.item_id = String(lastView.provenance.itemId);
      try {
        Promise.resolve(api.save(String(jobId), payload)).then(function (response) {
          if (!alive || gen !== generation) return;
          applyCandidatePayload(response);
          if (lastView) renderView(lastView);
          setCandidateDrawerState("ready", "已重命名");
          renderCandidateDrawer();
        }).catch(function (error) {
          if (!alive || gen !== generation) return;
          var status = error && error.status;
          setCandidateDrawerState("error", status === 409 ? "候选已更新，请重试" :
            (error && error.message ? String(error.message) : String(error)), true);
        });
      } catch (error) {
        setCandidateDrawerState("error", error && error.message ? String(error.message) : String(error), true);
      }
    }

    function removeCandidate(candidate) {
      if (!candidate || !lastView) return;
      var api = candidateClient();
      if (!api || typeof api.remove !== "function") {
        setCandidateDrawerState("error", "候选删除接口未接入", true);
        return;
      }
      var jobId = lastView.provenance && lastView.provenance.jobId;
      if (!jobId) return;
      if (typeof window.confirm === "function" && !window.confirm("删除候选 " + candidate.candidate_id + "？")) return;
      var id = candidate.candidate_id;
      var gen = generation;
      candidateGeneration += 1;
      setCandidateDrawerState("loading", "删除中…");
      try {
        Promise.resolve(api.remove(String(jobId), id, candidateRevision)).then(function (response) {
          if (!alive || gen !== generation) return;
          applyCandidatePayload(response);
          if (selectedCandidateId === id) selectedCandidateId = null;
          if (lastView) renderView(lastView);
          setCandidateDrawerState("ready", "已删除 " + id);
          renderCandidateDrawer();
        }).catch(function (error) {
          if (!alive || gen !== generation) return;
          var status = error && error.status;
          if (status === 409 || /conflict|revision/i.test(String(error && error.message))) {
            setCandidateDrawerState("error", "候选已更新，请重试", true);
            var reload = candidateClient();
            if (reload) {
              Promise.resolve(reload.list(String(jobId))).then(function (payload) {
                if (!alive || gen !== generation) return;
                applyCandidatePayload(payload);
                if (lastView) renderView(lastView);
                renderCandidateDrawer();
                setCandidateDrawerState("error", "候选已更新，请重试", true);
              }).catch(function () { /* keep the conflict message */ });
            }
          } else {
            setCandidateDrawerState("error", error && error.message ? String(error.message) : String(error), true);
          }
        });
      } catch (error) {
        setCandidateDrawerState("error", error && error.message ? String(error.message) : String(error), true);
      }
    }

    function selectRole(role) {
      if (candidateState === "saving") return;
      selectedRole = role === "int" ? "int" : "ts";
      updateCandidateBar();
    }

    function loadCandidates(jobId) {
      candidateGeneration += 1;
      var gen = candidateGeneration;
      var api = candidateClient();
      if (!api || !jobId || (source && source.mode === "fixture")) {
        applyCandidatePayload({ revision: 0, candidates: [] });
        updateCandidateBar();
        return;
      }
      try {
        Promise.resolve(api.list(jobId)).then(function (payload) {
          if (!alive || gen !== candidateGeneration) return;
          applyCandidatePayload(payload);
          if (lastView) renderView(lastView);
        }).catch(function () {
          if (!alive || gen !== candidateGeneration) return;
          applyCandidatePayload({ revision: 0, candidates: [] });
          if (lastView) renderView(lastView);
          setCandidateState("error", "候选列表加载失败", true);
          updateCandidateBar();
        });
      } catch (error) {
        applyCandidatePayload({ revision: 0, candidates: [] });
        setCandidateState("error", error && error.message ? error.message : String(error), true);
        updateCandidateBar();
      }
    }

    function reloadCandidatesAfterConflict(jobId, gen) {
      var api = candidateClient();
      if (!api) return;
      Promise.resolve(api.list(jobId)).then(function (payload) {
        if (!alive || gen !== generation) return;
        applyCandidatePayload(payload);
        if (lastView) renderView(lastView);
        setCandidateState("error", "候选已更新，请重试", true);
        updateCandidateBar();
      }).catch(function () { /* the save error is already surfaced */ });
    }

    function onSaveCandidate() {
      if (candidateState === "saving" || selectedFrameIndex == null || !lastView) return;
      var api = candidateClient();
      if (!api) {
        setCandidateState("idle", "候选接口未接入");
        return;
      }
      if (lastView.source === "fixture") {
        setCandidateState("idle", "演示数据不可保存");
        return;
      }
      var jobId = lastView.provenance && lastView.provenance.jobId;
      if (!jobId) {
        setCandidateState("error", "缺少 jobId，无法保存候选", true);
        return;
      }
      var payload = {
        view_type: viewTypeOf(lastView),
        frame_index: Number(selectedFrameIndex),
        role: selectedRole === "int" ? "INT" : "TS",
        expected_revision: candidateRevision
      };
      if (lastView.provenance && lastView.provenance.itemId) payload.item_id = String(lastView.provenance.itemId);
      if (graphState.selectedNodeId) payload.frame_id = String(graphState.selectedNodeId);
      var gen = generation;
      // Invalidate an in-flight candidate-list read: its revision predates this
      // write and must not overwrite the save receipt.
      candidateGeneration += 1;
      setCandidateState("saving", "保存中…");
      candidateSaveBtn.disabled = true;
      try {
        Promise.resolve(api.save(String(jobId), payload)).then(function (response) {
          if (!alive || gen !== generation) return;
          applyCandidatePayload(response);
          var candidate = response && response.candidate;
          setCandidateState("saved", "已保存：" + (candidate && candidate.candidate_id || ""));
          if (lastView) renderView(lastView);
        }).catch(function (error) {
          if (!alive || gen !== generation) return;
          var status = error && error.status;
          var message = error && error.message ? String(error.message) : String(error);
          if (status === 409 || /conflict|revision/i.test(message)) {
            setCandidateState("error", "候选已更新，请重试", true);
            reloadCandidatesAfterConflict(String(jobId), gen);
          } else {
            setCandidateState("error", message, true);
            if (candidateSaveBtn) candidateSaveBtn.disabled = false;
          }
          updateCandidateBar();
        });
      } catch (error) {
        setCandidateState("error", error && error.message ? error.message : String(error), true);
        updateCandidateBar();
      }
    }

    function attrFromEvent(target, name) {
      var node = target;
      while (node && node !== chartEl) {
        if (typeof node.getAttribute === "function") {
          var value = node.getAttribute(name);
          if (value != null) return value;
        }
        node = node.parentNode;
      }
      return null;
    }

    function locateFrame(frameIndex) {
      if (!lastView || frameIndex == null || !finite(Number(frameIndex))) return;
      var index = Number(frameIndex);
      var frame = (lastView.frames || []).find(function (item) { return Number(item.index) === index; });
      if (!frame || !frame.geometryRef) return;
      var jobId = lastView.provenance && lastView.provenance.jobId;
      if (!jobId || !deps.commands || typeof deps.commands.openStructure !== "function") return;
      try {
        deps.commands.openStructure({
          source: "energy-profile",
          kind: "job-file",
          jobId: String(jobId),
          path: frame.geometryRef,
          frameIndex: index,
          preview: true,
          attempt: lastView.provenance.attempt != null ? Number(lastView.provenance.attempt) : null,
          view: "energy"
        });
      } catch (e) { /* locating the structure is auxiliary; chart selection stands */ }
    }

    function selectFrame(frameIndex, annotationId) {
      if (!finite(Number(frameIndex))) return;
      var index = Number(frameIndex);
      var nodes = lastRendererData && Array.isArray(lastRendererData.nodes) ? lastRendererData.nodes : [];
      var node = nodes.find(function (item) { return Number(item.frame_index) === index; });
      graphState.selectedNodeId = node ? node.id : "";
      graphState.selectedAnnotationId = annotationId || "";
      selectedFrameIndex = index;
      var saved = candidateForFrame(index);
      selectedRole = saved && String(saved.role).toLowerCase() === "int" ? "int" :
        (saved ? "ts" : selectedRole);
      if (candidateState === "saved" || candidateState === "error") candidateState = "idle";
      if (lastView) renderView(lastView);
      locateFrame(index);
    }

    function onChartClick(event) {
      var target = event && event.target;
      if (!target) return;
      var frame = attrFromEvent(target, "data-energy-candidate-jump") ||
        attrFromEvent(target, "data-energy-frame");
      var annotationId = attrFromEvent(target, "data-energy-annotation");
      if (frame == null && annotationId == null) return;
      selectFrame(frame != null ? frame : "0", annotationId);
    }

    function renderView(view) {
      lastView = view;
      applyViewMeta(view);
      if (!view || view.availability === "error" || view.availability === "denied") {
        if (chartEl) chartEl.innerHTML = "";
        setState("error", warningMessage(view) || (view && view.availability === "denied" ? "无权限访问能量曲线" : ""));
        return;
      }
      if (view.availability !== "ready" || !Array.isArray(view.series) || !view.series.length) {
        if (chartEl) chartEl.innerHTML = "";
        setState("empty");
        return;
      }
      try {
        ensureRenderer();
        graphState.jobId = view.provenance && view.provenance.jobId ? String(view.provenance.jobId) : "";
        graphState.xAxisMode = (view.provenance && view.provenance.xAxisMode) || "scanned_coordinate";
        graphState.seriesId = "";
        graphState.layout = null;
        graphState.xDomain = null;
        graphState.yDomain = null;
        graphState.viewSize = {
          width: chartEl && chartEl.clientWidth ? chartEl.clientWidth : 640,
          height: chartEl && chartEl.clientHeight ? chartEl.clientHeight : 320
        };
        var data = toRendererData(view);
        lastRendererData = data;
        var svg = renderer.energyGraphBuildSvg(data);
        if (!svg || svg.indexOf("energy-empty") !== -1) {
          chartEl.innerHTML = "";
          setState("empty");
          return;
        }
        chartEl.innerHTML = svg;
        setState("ready");
      } catch (error) {
        if (chartEl) chartEl.innerHTML = "";
        setState("error", error && error.message ? error.message : String(error));
      }
    }

    function loadTask(task) {
      if (!alive) return;
      generation += 1;
      var gen = generation;
      graphState.selectedNodeId = "";
      graphState.selectedAnnotationId = "";
      graphState.seriesId = "";
      graphState.layout = null;
      graphState.frameCandidates = { revision: 0, list: [] };
      lastRendererData = null;
      selectedFrameIndex = null;
      selectedRole = "ts";
      candidateState = "idle";
      candidateRevision = 0;
      selectedCandidateId = null;
      if (candidateDrawer) candidateDrawer.hidden = true;
      setState("loading");
      try {
        var adapter = ensureSource();
        var jobId = task && task.jobId ? String(task.jobId) : "";
        if (jobId) {
          loadCandidates(jobId);
        } else {
          candidateGeneration += 1;
        }
        var promise;
        if (jobId) {
          promise = adapter.load("energy:" + jobId, { family: "energy", jobId: jobId });
        } else if (adapter.mode === "fixture") {
          promise = adapter.load("energy:fixture", { family: "energy" });
        } else {
          setState("empty");
          return;
        }
        promise.then(function (view) {
          if (!alive || gen !== generation) return;
          renderView(view);
        }).catch(function (error) {
          if (!alive || gen !== generation) return;
          setState("error", error && error.message ? error.message : String(error));
        });
      } catch (error) {
        setState("error", error && error.message ? error.message : String(error));
      }
    }

    function currentTaskRef() {
      if (!context || typeof context.getSnapshot !== "function") return null;
      try {
        var snapshot = context.getSnapshot();
        if (snapshot && snapshot.taskRef) return snapshot.taskRef;
        if (snapshot && snapshot.structureRef && snapshot.structureRef.jobId) {
          return { jobId: snapshot.structureRef.jobId };
        }
      } catch (e) { /* context is best-effort */ }
      return null;
    }

    function applySnapshot(snap) {
      if (!alive || !mounted) return;
      var task = snap && snap.taskRef ? snap.taskRef : null;
      if (!task && snap && snap.structureRef && snap.structureRef.jobId) {
        task = { jobId: snap.structureRef.jobId };
      }
      var key = task && task.jobId ? String(task.jobId) : (task ? "?" : null);
      if (key === currentTaskKey) return;
      currentTaskKey = key;
      loadTask(task);
    }

    function onContextNotify() {
      if (!alive || !mounted || !context || typeof context.getSnapshot !== "function") return;
      try { applySnapshot(context.getSnapshot()); } catch (e) { /* ignore malformed snapshot */ }
    }

    function removeDom() {
      if (root && root.parentNode && typeof root.parentNode.removeChild === "function") {
        try { root.parentNode.removeChild(root); } catch (e) { /* already detached */ }
      }
      root = null;
      chartEl = null;
      overlayEl = null;
      msgEl = null;
      detailEl = null;
      retryBtn = null;
      titleEl = null;
      metaEl = null;
      fixtureBadge = null;
      warnEl = null;
      candidateBar = null;
      candidateFrameEl = null;
      candidateRoleTs = null;
      candidateRoleInt = null;
      candidateSaveBtn = null;
      candidateStatusEl = null;
      candidateToggle = null;
      candidateDrawer = null;
      candidateDrawerStatus = null;
      candidateDrawerList = null;
      candidateDrawerDetail = null;
    }

    function renderMountFailure(error) {
      try {
        if (!root) buildDom();
        setState("error", (error && error.message ? error.message : String(error)) + "（模块局部错误，不影响其它面板）");
      } catch (e) {
        if (typeof console !== "undefined" && console && console.error) {
          console.error("[energy-profile] mount failed and error surface unavailable:", e);
        }
      }
    }

    return {
      mount: function (hostEl, initial) {
        if (mounted) return Promise.reject(new Error("energy-profile: already mounted (dispose() first)"));
        if (!hostEl || typeof hostEl.appendChild !== "function") {
          return Promise.reject(new TypeError("energy-profile mount: host 必须是 DOM 元素"));
        }
        alive = true;
        mounted = true;
        host = hostEl;
        try {
          buildDom();
          if (!window.ACPVisualization || !window.ACPVisualization.energyChart) {
            throw new Error("ACPVisualization.energyChart 未加载（检查 energy_chart_renderer.js 脚本顺序）");
          }
          if (context && typeof context.subscribe === "function") {
            unsubscribeContext = context.subscribe(onContextNotify);
          }
          var snap = initial;
          if (snap == null && context && typeof context.getSnapshot === "function") snap = context.getSnapshot();
          applySnapshot(snap);
        } catch (error) {
          if (unsubscribeContext) { try { unsubscribeContext(); } catch (e) { /* best effort */ } unsubscribeContext = null; }
          alive = true;
          mounted = true;
          renderMountFailure(error);
        }
        return Promise.resolve();
      },

      updateContext: function (next) { applySnapshot(next || null); },

      resize: function () {
        if (!alive || !lastView || suspended) return;
        if (root && root.getAttribute("data-acp-module-state") === "ready") renderView(lastView);
      },

      suspend: function () { suspended = true; },
      resume: function () { suspended = false; if (lastView) renderView(lastView); },

      dispose: function () {
        if (!alive && !mounted && !root) return;
        alive = false;
        mounted = false;
        generation += 1;
        candidateGeneration += 1;
        selectedCandidateId = null;
        if (unsubscribeContext) {
          try { unsubscribeContext(); } catch (e) { /* best effort */ }
          unsubscribeContext = null;
        }
        removeDom();
        host = null;
        lastView = null;
        source = null;
      }
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: {
      id: MODULE_ID,
      interfaceVersion: 1,
      version: "0.1.0",
      dependencies: [],
      provides: ["energy.profile"],
      binds: ["taskRef"],
      capabilities: { requires: [], provides: ["energy.profile"] },
      hosts: ["acp-web"],
      supportedInstancing: "single",
      configSchemaVersion: 0,
      error: { titleKey: "next.energy.errorTitle", messageKey: "next.energy.errorMessage" }
    },
    factory: function (deps) { return createInstance(deps || {}); },
    _internals: { toRendererData: toRendererData }
  };
})();
