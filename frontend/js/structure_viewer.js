/**
 * ACP Structure Viewer — state store + catalog fetch + stale-response guard
 * @version 0.3.0
 *
 * Namespace: window.ACPStructureViewer
 *
 * Exposes:
 *   - state                     (the live structureViewerState object)
 *   - loadStructureViewer(jobId, opts)
 *   - selectEntry(entryId, origin)
 *   - refreshIfChanged()
 *   - loadSelectedGeometry()    (stub; todo 17 fills)
 *   - renderStructureViewer()   (renders list panel from state.payload)
 *   - renderInspector()         (renders inspector for selected entry)
 *   - toggleListDrawer()        (narrow-screen drawer toggle)
 *   - toggleInspectorDrawer()   (narrow-screen drawer toggle)
 *
 * Internal (test-overridable via namespace property):
 *   - _fetchImpl                (default: window.fetch; tests inject a fake)
 *   - _applyCatalogResponse     (pure: applies a server response to state)
 *   - _esc                      (XSS-safe text insertion)
 *
 * TODO(todo-17): wire selectJob -> loadStructureViewer, geometry loading
 * TODO(todo-18): energy-graph selection push via selectEntry + selectionToken
 * TODO(todo-19): phase-A contract tests + i18n completeness
 */
(function () {
  "use strict";

  var VERSION = "0.3.0";

  /* ---- user-visible strings (zh constants; todo 19 moves to i18n) ---- */
  var STR = {
    NEWER_AVAILABLE: "\u6709\u65b0\u7ed3\u6784\u53ef\u7528",       // 有新结构可用
    PENDING_FETCH: "\u7b49\u5f85\u8fdc\u7a0b\u7ed3\u679c",         // 等待远程结果
    PENDING_RETRY: "\u8fdc\u7a0b\u6587\u4ef6\u5c1a\u672a\u540c\u6b65\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5", // 远程文件尚未同步，请稍后重试
    NO_ENTRY: "\u672a\u9009\u62e9\u7ed3\u6784",                     // 未选择结构
    NO_ENTRY_HINT: "\u8bf7\u4ece\u5de6\u4fa7\u5217\u8868\u9009\u62e9\u4e00\u4e2a\u7ed3\u6784", // 请从左侧列表选择一个结构
    REFRESH: "\u5237\u65b0",                                         // 刷新
    LIST_TITLE: "\u7ed3\u6784\u5217\u8868",                         // 结构列表
    INSPECTOR_TITLE: "\u68c0\u67e5\u5668",                           // 检查器
    SOURCE: "\u6765\u6e90",                                           // 来源
    STATUS: "\u72b6\u6001",                                           // 状态
    ENERGY: "\u80fd\u91cf",                                           // 能量
    DELTA_E: "\u0394E",                                               // ΔE
    WEIGHT: "Boltzmann \u6743\u91cd",                                // Boltzmann 权重
    VIBRATIONS: "\u632f\u52a8\u6a21\u5f0f",                         // 振动模式
    MEASUREMENTS: "\u6d4b\u91cf",                                     // 测量
    WARNINGS: "\u8b66\u544a",                                         // 警告
    COMPLETED: "\u5df2\u5b8c\u6210",                                 // 已完成
    FAILED: "\u5931\u8d25",                                           // 失败
    SOURCE_KINDS: {
      formal_result: "\u6b63\u5f0f\u7ed3\u679c",                   // 正式结果
      algorithm_recommendation: "\u81ea\u52a8\u63a8\u8350",         // 自动推荐
      manual_review: "\u4eba\u5de5\u786e\u8ba4",                   // 人工确认
      last_valid_cycle: "\u6700\u540e\u6709\u6548\u5468\u671f",     // 最后有效周期
      calculation_input: "\u8ba1\u7b97\u8f93\u5165",               // 计算输入
      manual_file: "\u624b\u52a8\u6587\u4ef6",                     // 手动文件
    },
    VIBRATIONS_NA: "\u6682\u4e0d\u53ef\u7528",                     // 暂不可用
    MEASUREMENTS_PLACEHOLDER: "\u9009\u62e9\u539f\u5b50\u540e\u663e\u793a\u6d4b\u91cf\u7ed3\u679c", // 选择原子后显示测量结果
    EDIT_PLACEHOLDER: "\u7f16\u8f91\u529f\u80fd\u5c06\u5728\u540e\u7eed\u7248\u672c\u5f00\u653e", // 编辑功能将在后续版本开放
  };

  /** @returns {Function|null} */
  function _getFetchImpl() {
    return (typeof window !== "undefined" && window.ACPStructureViewer && window.ACPStructureViewer._fetchImpl) || null;
  }

  /**
   * XSS-safe text insertion.  Converts a value to a safe string for use
   * in textContent (never innerHTML with server strings).
   *
   * @param {*} val
   * @returns {string}
   */
  function _esc(val) {
    if (val == null) return "";
    return String(val);
  }

  /**
   * @typedef {Object} StructureViewerState
   * @property {string|null} jobId
   * @property {Object|null} payload
   * @property {string|null} revision
   * @property {string|null} selectedEntryId
   * @property {string|null} selectionOrigin
   * @property {number} selectionToken
   * @property {boolean} dirty
   * @property {Object|null} editState
   * @property {number} requestToken
   * @property {string} availability
   * @property {boolean} newerAvailable
   * @property {string|null} error
   * @property {AbortController|null} _abortController
   */
  var structureViewerState = {
    jobId: null,
    payload: null,
    revision: null,
    selectedEntryId: null,
    selectionOrigin: null,
    selectionToken: 0,
    dirty: false,
    editState: null,
    requestToken: 0,
    availability: "",
    newerAvailable: false,
    error: null,
    _abortController: null,
  };

  /**
   * Apply a catalog response to the state object.  Pure-ish: mutates the
   * supplied state but performs no I/O and is deterministic for a given input.
   *
   * @param {StructureViewerState} state
   * @param {number} capturedToken - the requestToken at fetch-initiation time
   * @param {Object} data          - parsed JSON from the server
   * @returns {boolean} true if applied, false if discarded (stale)
   */
  function _applyCatalogResponse(state, capturedToken, data) {
    if (capturedToken !== state.requestToken) {
      return false;
    }
    state.payload = data;
    state.revision = data && data.revision ? data.revision : null;
    state.availability = data && data.availability ? data.availability : "";
    state.error = null;
    state.newerAvailable = false;

    if (data && data.default_entry_id && !state.selectedEntryId) {
      state.selectedEntryId = data.default_entry_id;
      state.selectionOrigin = "auto";
    }
    return true;
  }

  /**
   * @param {string} jobId
   * @param {string|null} itemId
   * @returns {string}
   */
  function _catalogUrl(jobId, itemId) {
    var base = "/api/v1/jobs/" + encodeURIComponent(jobId) + "/structure-viewer";
    if (itemId) {
      base += "?item_id=" + encodeURIComponent(itemId);
    }
    return base;
  }

  /**
   * Load (or reload) the structure-viewer catalog for a job.
   *
   * @param {string} jobId
   * @param {{ itemId?: string }} [opts]
   * @returns {Promise<void>}
   */
  function loadStructureViewer(jobId, opts) {
    opts = opts || {};
    var itemId = opts.itemId || null;

    if (structureViewerState._abortController) {
      try { structureViewerState._abortController.abort(); } catch (_) { /* ignore */ }
    }

    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    structureViewerState._abortController = controller;

    structureViewerState.requestToken += 1;
    var capturedToken = structureViewerState.requestToken;

    structureViewerState.jobId = jobId;
    structureViewerState.payload = null;
    structureViewerState.revision = null;
    structureViewerState.availability = "";
    structureViewerState.error = null;
    structureViewerState.newerAvailable = false;
    structureViewerState.selectedEntryId = null;
    structureViewerState.selectionOrigin = null;

    var fetchFn = _getFetchImpl();
    if (!fetchFn) {
      structureViewerState.error = "fetch not available";
      return Promise.resolve();
    }

    var url = _catalogUrl(jobId, itemId);
    var fetchOpts = { headers: { "Accept": "application/json" } };
    if (controller) {
      fetchOpts.signal = controller.signal;
    }

    return fetchFn(url, fetchOpts)
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status + " " + resp.statusText);
        }
        return resp.json();
      })
      .then(function (data) {
        _applyCatalogResponse(structureViewerState, capturedToken, data);
        renderStructureViewer();
        renderInspector();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") { return; }
        if (capturedToken !== structureViewerState.requestToken) { return; }
        structureViewerState.error = err && err.message ? err.message : String(err);
        renderStructureViewer();
        renderInspector();
      });
  }

  /**
   * Select an entry in the structure list.
   *
   * @param {string} entryId
   * @param {string} [origin]
   * @returns {number} The new selectionToken
   */
  function selectEntry(entryId, origin) {
    structureViewerState.selectionToken += 1;
    structureViewerState.selectedEntryId = entryId;
    structureViewerState.selectionOrigin = origin || "user";
    renderStructureViewer();
    renderInspector();
    loadSelectedGeometry();
    return structureViewerState.selectionToken;
  }

  /**
   * Refresh the catalog if the server revision changed since last load.
   *
   * @returns {Promise<void>}
   */
  function refreshIfChanged() {
    if (!structureViewerState.jobId) {
      return Promise.resolve();
    }

    var oldRevision = structureViewerState.revision;
    var wasDirty = structureViewerState.dirty;
    var prevSelected = structureViewerState.selectedEntryId;

    var fetchFn = _getFetchImpl();
    if (!fetchFn) {
      return Promise.resolve();
    }

    var url = _catalogUrl(structureViewerState.jobId, null);
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var fetchOpts = { headers: { "Accept": "application/json" } };
    if (controller) {
      fetchOpts.signal = controller.signal;
    }

    return fetchFn(url, fetchOpts)
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status + " " + resp.statusText);
        }
        return resp.json();
      })
      .then(function (data) {
        var newRevision = data && data.revision ? data.revision : null;
        if (newRevision === oldRevision) { return; }
        if (wasDirty) {
          structureViewerState.newerAvailable = true;
          renderInspector();
          return;
        }
        _applyCatalogResponse(structureViewerState, structureViewerState.requestToken, data);
        if (prevSelected && structureViewerState.payload) {
          var entries = structureViewerState.payload.entries || [];
          var found = false;
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].id === prevSelected) { found = true; break; }
          }
          if (found) {
            structureViewerState.selectedEntryId = prevSelected;
          }
        }
        renderStructureViewer();
        renderInspector();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") { return; }
      });
  }

  /**
   * Load geometry for the currently selected entry.  Stub — todo 17 fills.
   * @returns {Promise<void>}
   */
  function loadSelectedGeometry() {
    return Promise.resolve();
  }

  /* ---- badge rendering helpers ---- */

  function _badgeClass(badge) {
    if (badge === "\u672a\u786e\u8ba4") return "sv-badge-unconfirmed";       // 未确认
    if (badge === "\u517c\u5bb9\u6a21\u5f0f") return "sv-badge-legacy";     // 兼容模式
    if (badge === "failed-last-frame") return "sv-badge-failed-frame";
    if (badge === "TS" || badge === "INT") return "sv-badge-role";
    if (badge.indexOf("rank-") === 0) return "sv-badge-rank";
    return "sv-badge-role";
  }

  function _renderBadgeChip(badge) {
    var span = document.createElement("span");
    span.className = "sv-badge " + _badgeClass(badge);
    span.textContent = _esc(badge);
    return span;
  }

  /* ---- render: structure list ---- */

  /**
   * Render the structure list panel from state.payload.
   * Groups section rendered only when >1 entry or a named group exists.
   * Single-entry payloads hide the list panel entirely.
   */
  function renderStructureViewer() {
    if (typeof document === "undefined") return;
    var listHeader = document.getElementById("sv-list-header");
    var listBody = document.getElementById("sv-list-body");
    var layout = document.getElementById("sv-layout");
    if (!listHeader || !listBody || !layout) return;

    listBody.innerHTML = "";

    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var groups = (payload && payload.groups) || [];

    /* availability / error notices */
    if (structureViewerState.availability === "pending_fetch") {
      var notice = document.createElement("div");
      notice.className = "sv-notice sv-notice-pending";
      notice.textContent = STR.PENDING_FETCH + "\u2014" + STR.PENDING_RETRY;
      listBody.appendChild(notice);
    }
    if (structureViewerState.error) {
      var errNotice = document.createElement("div");
      errNotice.className = "sv-notice sv-notice-error";
      errNotice.textContent = _esc(structureViewerState.error);
      listBody.appendChild(errNotice);
    }

    /* hide list for single-entry payloads (no named groups) */
    var hasNamedGroup = false;
    for (var g = 0; g < groups.length; g++) {
      if (groups[g].label && groups[g].label !== groups[g].id) {
        hasNamedGroup = true;
        break;
      }
    }
    var showList = entries.length > 1 || hasNamedGroup;
    layout.classList.toggle("sv-list-hidden", !showList);

    if (!showList) {
      listHeader.textContent = "";
      return;
    }

    listHeader.textContent = STR.LIST_TITLE;

    /* group entries by group_id */
    var groupMap = {};
    for (var gi = 0; gi < groups.length; gi++) {
      groupMap[groups[gi].id] = groups[gi];
    }

    var entriesByGroup = {};
    for (var ei = 0; ei < entries.length; ei++) {
      var e = entries[ei];
      var gid = e.group_id || "__ungrouped";
      if (!entriesByGroup[gid]) entriesByGroup[gid] = [];
      entriesByGroup[gid].push(e);
    }

    var groupIds = Object.keys(entriesByGroup);
    for (var gk = 0; gk < groupIds.length; gk++) {
      var groupId = groupIds[gk];
      var group = groupMap[groupId];
      var groupEntries = entriesByGroup[groupId];

      /* group header (only if named group exists) */
      if (group && group.label) {
        var header = document.createElement("div");
        header.className = "sv-group-header";
        header.textContent = _esc(group.label);
        listBody.appendChild(header);
      }

      for (var ej = 0; ej < groupEntries.length; ej++) {
        var entry = groupEntries[ej];
        var row = _renderEntryRow(entry);
        listBody.appendChild(row);
      }
    }
  }

  function _renderEntryRow(entry) {
    var row = document.createElement("div");
    row.className = "sv-entry-row";
    if (entry.id === structureViewerState.selectedEntryId) {
      row.className += " sv-active";
    }
    row.setAttribute("data-entry-id", _esc(entry.id));

    /* status dot */
    var dot = document.createElement("span");
    dot.className = "sv-status-dot sv-status-" + _esc(entry.status || "completed");
    row.appendChild(dot);

    /* label */
    var label = document.createElement("span");
    label.className = "sv-entry-label";
    label.textContent = _esc(entry.label || entry.id);
    row.appendChild(label);

    /* badges */
    var badges = entry.badges || [];
    for (var bi = 0; bi < badges.length; bi++) {
      row.appendChild(_renderBadgeChip(badges[bi]));
    }

    /* energy */
    if (entry.energy && entry.energy.value != null) {
      var energySpan = document.createElement("span");
      energySpan.className = "sv-entry-energy";
      energySpan.textContent = _formatEnergy(entry.energy);
      row.appendChild(energySpan);
    }

    /* boltzmann bar */
    if (entry.boltzmann_weight != null) {
      var bar = document.createElement("span");
      bar.className = "sv-boltzmann-bar";
      var fill = document.createElement("span");
      fill.className = "sv-boltzmann-bar-fill";
      fill.style.width = Math.round(entry.boltzmann_weight * 100) + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
    }

    /* click handler */
    row.addEventListener("click", function () {
      selectEntry(entry.id, "list");
    });

    return row;
  }

  function _formatEnergy(energy) {
    if (!energy || energy.value == null) return "";
    var val = energy.value;
    var unit = energy.unit || "hartree";
    if (unit === "hartree") {
      return val.toFixed(6) + " Eh";
    }
    return val.toFixed(4) + " " + _esc(unit);
  }

  /* ---- render: inspector ---- */

  /**
   * Render the inspector panel for the currently selected entry.
   */
  function renderInspector() {
    if (typeof document === "undefined") return;
    var inspHeader = document.getElementById("sv-inspector-header");
    var inspBody = document.getElementById("sv-inspector-body");
    if (!inspHeader || !inspBody) return;

    inspBody.innerHTML = "";
    inspHeader.textContent = STR.INSPECTOR_TITLE;

    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var selectedId = structureViewerState.selectedEntryId;

    /* newer-available notice */
    if (structureViewerState.newerAvailable) {
      var newerNotice = document.createElement("div");
      newerNotice.className = "sv-notice sv-notice-newer";
      newerNotice.textContent = STR.NEWER_AVAILABLE;
      var refreshBtn = document.createElement("button");
      refreshBtn.className = "sv-refresh-btn";
      refreshBtn.textContent = STR.REFRESH;
      refreshBtn.addEventListener("click", function () {
        refreshIfChanged();
      });
      newerNotice.appendChild(document.createElement("br"));
      newerNotice.appendChild(refreshBtn);
      inspBody.appendChild(newerNotice);
    }

    /* find selected entry */
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === selectedId) { entry = entries[i]; break; }
    }

    if (!entry) {
      var noEntry = document.createElement("div");
      noEntry.className = "sv-inspector-value sv-muted";
      noEntry.textContent = STR.NO_ENTRY;
      inspBody.appendChild(noEntry);
      return;
    }

    /* status section */
    inspBody.appendChild(_inspectorSection(STR.STATUS,
      entry.status === "completed" ? STR.COMPLETED : STR.FAILED));

    /* source section */
    var sourceKind = (entry.source && entry.source.kind) || "";
    var sourceLabel = STR.SOURCE_KINDS[sourceKind] || _esc(sourceKind);
    inspBody.appendChild(_inspectorSection(STR.SOURCE, sourceLabel));

    /* energy section */
    if (entry.energy && entry.energy.value != null) {
      var energyDiv = document.createElement("div");
      energyDiv.className = "sv-inspector-section";

      var lbl = document.createElement("div");
      lbl.className = "sv-inspector-label";
      lbl.textContent = STR.ENERGY;
      energyDiv.appendChild(lbl);

      var valRow = document.createElement("div");
      valRow.className = "sv-inspector-energy";
      var valSpan = document.createElement("span");
      valSpan.className = "sv-inspector-energy-value";
      valSpan.textContent = entry.energy.value.toFixed(6);
      valRow.appendChild(valSpan);
      var unitSpan = document.createElement("span");
      unitSpan.className = "sv-inspector-energy-unit";
      unitSpan.textContent = _esc(entry.energy.unit || "hartree");
      valRow.appendChild(unitSpan);
      energyDiv.appendChild(valRow);

      if (entry.energy.kind) {
        var kindSpan = document.createElement("div");
        kindSpan.className = "sv-inspector-value sv-muted";
        kindSpan.textContent = _esc(entry.energy.kind);
        energyDiv.appendChild(kindSpan);
      }

      if (entry.energy.temperature_k != null) {
        var tempSpan = document.createElement("div");
        tempSpan.className = "sv-inspector-value sv-muted";
        tempSpan.textContent = "T = " + entry.energy.temperature_k + " K";
        energyDiv.appendChild(tempSpan);
      }

      inspBody.appendChild(energyDiv);
    }

    /* delta E */
    if (entry.relative_energy_kcal != null) {
      var deltaDiv = document.createElement("div");
      deltaDiv.className = "sv-inspector-section";
      var deltaLbl = document.createElement("div");
      deltaLbl.className = "sv-inspector-label";
      deltaLbl.textContent = STR.DELTA_E;
      deltaDiv.appendChild(deltaLbl);
      var deltaVal = document.createElement("div");
      deltaVal.className = "sv-inspector-delta";
      deltaVal.textContent = entry.relative_energy_kcal.toFixed(2) + " kcal/mol";
      deltaDiv.appendChild(deltaVal);
      inspBody.appendChild(deltaDiv);
    }

    /* boltzmann weight */
    if (entry.boltzmann_weight != null) {
      var weightDiv = document.createElement("div");
      weightDiv.className = "sv-inspector-section";
      var weightLbl = document.createElement("div");
      weightLbl.className = "sv-inspector-label";
      weightLbl.textContent = STR.WEIGHT;
      weightDiv.appendChild(weightLbl);
      var weightVal = document.createElement("div");
      weightVal.className = "sv-inspector-value";
      weightVal.textContent = (entry.boltzmann_weight * 100).toFixed(1) + "%";
      weightDiv.appendChild(weightVal);
      var weightBar = document.createElement("div");
      weightBar.className = "sv-inspector-weight-bar";
      var weightFill = document.createElement("div");
      weightFill.className = "sv-inspector-weight-bar-fill";
      weightFill.style.width = Math.round(entry.boltzmann_weight * 100) + "%";
      weightBar.appendChild(weightFill);
      weightDiv.appendChild(weightBar);
      inspBody.appendChild(weightDiv);
    }

    /* vibrations placeholder */
    var vibDiv = document.createElement("div");
    vibDiv.className = "sv-inspector-section";
    var vibLbl = document.createElement("div");
    vibLbl.className = "sv-inspector-label";
    vibLbl.textContent = STR.VIBRATIONS;
    vibDiv.appendChild(vibLbl);
    var vibVal = document.createElement("div");
    vibVal.className = "sv-inspector-value sv-muted";
    var vibAvail = entry.vibrations && entry.vibrations.available;
    vibVal.textContent = vibAvail ? "\u53ef\u7528" : STR.VIBRATIONS_NA;
    vibDiv.appendChild(vibVal);
    inspBody.appendChild(vibDiv);

    /* measurements placeholder */
    var measDiv = document.createElement("div");
    measDiv.className = "sv-inspector-section";
    var measLbl = document.createElement("div");
    measLbl.className = "sv-inspector-label";
    measLbl.textContent = STR.MEASUREMENTS;
    measDiv.appendChild(measLbl);
    var measVal = document.createElement("div");
    measVal.className = "sv-inspector-value sv-muted";
    measVal.textContent = STR.MEASUREMENTS_PLACEHOLDER;
    measDiv.appendChild(measVal);
    inspBody.appendChild(measDiv);

    /* edit placeholder */
    var editDiv = document.createElement("div");
    editDiv.className = "sv-inspector-section";
    var editVal = document.createElement("div");
    editVal.className = "sv-inspector-value sv-muted";
    editVal.textContent = STR.EDIT_PLACEHOLDER;
    editDiv.appendChild(editVal);
    inspBody.appendChild(editDiv);

    /* warnings */
    var warnings = (payload && payload.warnings) || [];
    if (warnings.length > 0) {
      var warnDiv = document.createElement("div");
      warnDiv.className = "sv-inspector-section";
      var warnLbl = document.createElement("div");
      warnLbl.className = "sv-inspector-label";
      warnLbl.textContent = STR.WARNINGS;
      warnDiv.appendChild(warnLbl);
      var warnList = document.createElement("ul");
      warnList.className = "sv-warning-list";
      for (var wi = 0; wi < warnings.length; wi++) {
        var warnItem = document.createElement("li");
        warnItem.className = "sv-warning-item";
        warnItem.textContent = _esc(warnings[wi]);
        warnList.appendChild(warnItem);
      }
      warnDiv.appendChild(warnList);
      inspBody.appendChild(warnDiv);
    }
  }

  function _inspectorSection(label, value) {
    var div = document.createElement("div");
    div.className = "sv-inspector-section";
    var lbl = document.createElement("div");
    lbl.className = "sv-inspector-label";
    lbl.textContent = _esc(label);
    div.appendChild(lbl);
    var val = document.createElement("div");
    val.className = "sv-inspector-value";
    val.textContent = _esc(value);
    div.appendChild(val);
    return div;
  }

  /* ---- drawer toggles ---- */

  function toggleListDrawer() {
    if (typeof document === "undefined") return;
    var panel = document.getElementById("structure-list-panel");
    var overlay = document.getElementById("sv-drawer-overlay");
    if (!panel) return;
    var isOpen = panel.classList.contains("sv-drawer-open");
    panel.classList.toggle("sv-drawer-open", !isOpen);
    if (overlay) overlay.classList.toggle("sv-drawer-open", !isOpen);
  }

  function toggleInspectorDrawer() {
    if (typeof document === "undefined") return;
    var panel = document.getElementById("structure-inspector-panel");
    var overlay = document.getElementById("sv-drawer-overlay");
    if (!panel) return;
    var isOpen = panel.classList.contains("sv-drawer-open");
    panel.classList.toggle("sv-drawer-open", !isOpen);
    if (overlay) overlay.classList.toggle("sv-drawer-open", !isOpen);
  }

  function closeAllDrawers() {
    if (typeof document === "undefined") return;
    var list = document.getElementById("structure-list-panel");
    var insp = document.getElementById("structure-inspector-panel");
    var overlay = document.getElementById("sv-drawer-overlay");
    if (list) list.classList.remove("sv-drawer-open");
    if (insp) insp.classList.remove("sv-drawer-open");
    if (overlay) overlay.classList.remove("sv-drawer-open");
  }

  /* ---- public namespace ---- */
  window.ACPStructureViewer = {
    version: VERSION,
    state: structureViewerState,
    STR: STR,
    loadStructureViewer: loadStructureViewer,
    selectEntry: selectEntry,
    refreshIfChanged: refreshIfChanged,
    loadSelectedGeometry: loadSelectedGeometry,
    renderStructureViewer: renderStructureViewer,
    renderInspector: renderInspector,
    toggleListDrawer: toggleListDrawer,
    toggleInspectorDrawer: toggleInspectorDrawer,
    closeAllDrawers: closeAllDrawers,
    _applyCatalogResponse: _applyCatalogResponse,
    _esc: _esc,
    _fetchImpl: (typeof window !== "undefined" && window.fetch) ? window.fetch.bind(window) : null,
  };
})();
