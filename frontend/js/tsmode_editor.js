/**
 * ACP TS Mode Editor — TS Mode Optimization wizard for ACP Workbench v2
 * @version 0.1.0
 *
 * Namespace: window.ACPTsmodeEditor
 *
 * Exposes:
 *   - open({sourceJobId, entryId, modeIndex})  (opens the modal wizard)
 *   - submit()                                  (POST /api/v1/jobs with workflow tsmode)
 *   - close()                                   (close modal, reset state)
 *   - state                                     (live wizard state — read-only)
 *
 * Panels:
 *   a. 来源 (source): select a frequency source (job + entry)
 *   b. 模式 (mode): pick an imaginary mode as optimization target
 *   c. 设置 (settings): configure tsmode options
 *
 * Contract (backend):
 *   GET /api/v1/jobs/{id}/frequency-sources
 *     -> {sources: [{entry_id, item_id, label, output_path, hess_path, complete,
 *         hessian_available, imaginary_count, atom_count, mode_count}], warnings: [...]}
 *   POST /api/v1/jobs {workflow:"tsmode", name, input:{source_job_id, entry_id,
 *     source_mode_index, allow_unverified_mapping?, recalc_hess?, max_iterations?,
 *     trust_radius?, retry_limit?, final_frequency?}}
 *
 * i18n: tsmode.* keys in I18N dictionaries (zh-CN + en-US).
 */
(function () {
  "use strict";

  var VERSION = "0.1.0";

  /* ---- fallback strings (zh; primary source is I18N dict via _t()) ---- */
  var STR = {
    MODAL_TITLE: "TS \u6a21\u5f0f\u4f18\u5316",
    PANEL_SOURCE: "\u6765\u6e90",
    PANEL_MODE: "\u6a21\u5f0f",
    PANEL_SETTINGS: "\u8bbe\u7f6e",
    SELECT_JOB: "\u9009\u62e9\u6e90\u4efb\u52a1\u2026",
    LOADING: "\u52a0\u8f7d\u4e2d\u2026",
    NO_SOURCES: "\u65e0\u53ef\u7528\u9891\u7387\u6e90",
    SELECT_SOURCE_HINT: "\u8bf7\u9009\u62e9\u4e00\u4e2a\u9891\u7387\u6e90",
    HESS_AVAILABLE: "Hessian",
    HESS_MISSING: "\u7f3a\u5c11 Hessian",
    ATOM_COUNT: "\u539f\u5b50",
    IMAG_COUNT: "\u865a\u9891",
    MODE_COUNT: "\u6a21\u5f0f",
    NO_IMAGINARY: "\u65e0\u865a\u9891\u6a21\u5f0f \u2014 \u5efa\u8bae\u4f7f\u7528\u666e\u901a optimize/frequency",
    SET_TARGET: "\u8bbe\u4e3a\u4f18\u5316\u76ee\u6807",
    TARGET_CONFIRMED: "\u5df2\u786e\u8ba4",
    PREVIEW_DIFFERS: "\u5f53\u524d\u9884\u89c8\u4e0d\u540c\u4e8e\u5df2\u786e\u8ba4\u76ee\u6807",
    FREQ_UNIT: "cm\u207b\u00b9",
    INHERITED_LEVEL: "\u7ee7\u627f\u7ea7\u522b",
    RECALC_HESS: "\u91cd\u7b97 Hessian",
    MAX_ITERATIONS: "\u6700\u5927\u8fed\u4ee3",
    TRUST_RADIUS: "\u4fe1\u4efb\u534a\u5f84",
    RETRY_LIMIT: "\u91cd\u8bd5\u4e0a\u9650",
    FINAL_FREQ: "\u6700\u7ec8\u9891\u7387\u9a8c\u8bc1",
    ALLOW_UNVERIFIED: "\u5141\u8bb8\u672a\u9a8c\u8bc1\u6620\u5c04",
    ALLOW_UNVERIFIED_WARN: "\u6620\u5c04\u672a\u7ecf\u771f\u5b9e ORCA \u7248\u672c\u9a8c\u8bc1\uff08P0 \u5f85\u5b8c\u6210\uff09\u2014\u2014\u786e\u8ba4\u540e\u65b9\u53ef\u63d0\u4ea4",
    BACK: "\u4e0a\u4e00\u6b65",
    NEXT: "\u4e0b\u4e00\u6b65",
    SUBMIT: "\u63d0\u4ea4",
    CANCEL: "\u53d6\u6d88",
    SUBMIT_FAILED: "\u63d0\u4ea4\u5931\u8d25",
    SOURCE_REQUIRED: "\u8bf7\u5148\u9009\u62e9\u9891\u7387\u6e90",
    TARGET_REQUIRED: "\u8bf7\u5148\u786e\u8ba4\u4f18\u5316\u76ee\u6807\u6a21\u5f0f",
    JOB_NAME_PREFIX: "TSMode",
  };

  /* ---- i18n helper ---- */
  function _t(key, fallback) {
    if (typeof t === "function") {
      var val = t(key);
      if (val && val !== key) return val;
    }
    return fallback;
  }

  /* ---- XSS-safe text ---- */
  function _esc(val) {
    if (val == null) return "";
    return String(val);
  }

  /* ---- state ---- */
  var state = {
    /** @type {number} monotonic stale-response guard */
    requestToken: 0,
    /** @type {string|null} preprovided source job id (fixed if set) */
    sourceJobId: null,
    /** @type {string|null} preprovided entry id */
    entryId: null,
    /** @type {number|null} preprovided mode index */
    modeIndex: null,
    /** @type {Array<Object>} jobs list for the job picker */
    jobs: [],
    /** @type {Array<Object>} frequency sources for the selected job */
    sources: [],
    /** @type {Object|null} selected source entry */
    selectedSource: null,
    /** @type {Array<Object>} vibration modes for preview */
    modes: [],
    /** @type {number|null} currently previewed mode index */
    previewModeIndex: null,
    /** @type {number|null} confirmed target mode index */
    confirmedModeIndex: null,
    /** @type {number} current panel (0=source, 1=mode, 2=settings) */
    currentPanel: 0,
    /** @type {boolean} loading state */
    loading: false,
    /** @type {string|null} error message */
    error: null,
    /** @type {boolean} whether the editor modal is open */
    isOpen: false,
  };

  /* ---- DOM refs ---- */
  var _modal = null;
  var _root = null;

  /* ---- fetch helpers ---- */
  function _getFetchImpl() {
    if (typeof window !== "undefined" && window.ACPTsmodeEditor &&
        typeof window.ACPTsmodeEditor._fetchImpl === "function") {
      return window.ACPTsmodeEditor._fetchImpl;
    }
    if (typeof window !== "undefined" && typeof window.fetch === "function") {
      return window.fetch.bind(window);
    }
    return null;
  }

  async function _apiV1(path, opts) {
    opts = opts || {};
    var fetchFn = _getFetchImpl();
    if (!fetchFn) throw new Error("fetch not available");
    if (!opts.signal && typeof AbortController !== "undefined") {
      var ac = new AbortController();
      var timer = setTimeout(function () { ac.abort(); }, 8000);
      opts.signal = ac.signal;
    }
    var resp = await fetchFn("/api/v1" + path, opts);
    if (timer) clearTimeout(timer);
    if (!resp.ok) {
      var msg = "HTTP " + resp.status;
      try {
        var body = await resp.json();
        if (body && body.detail) {
          var d = body.detail;
          if (typeof d === "string") { msg = d; }
          else if (d.error) { msg = d.error + (d.detail ? ": " + d.detail : ""); }
          else if (typeof d === "object") { msg = JSON.stringify(d); }
        }
      } catch (e) { /* ignore parse errors */ }
      var err = new Error(msg);
      err.status = resp.status;
      throw err;
    }
    var ct = resp.headers.get("content-type") || "";
    return ct.includes("application/json") ? resp.json() : resp.text();
  }

  /* ---- open / close ---- */

  /**
   * Open the TS Mode Editor wizard.
   * @param {{sourceJobId?: string|null, entryId?: string|null, modeIndex?: number|null}} [opts]
   */
  function open(opts) {
    opts = opts || {};
    state.sourceJobId = opts.sourceJobId || null;
    state.entryId = opts.entryId || null;
    state.modeIndex = (typeof opts.modeIndex === "number") ? opts.modeIndex : null;
    state.selectedSource = null;
    state.modes = [];
    state.previewModeIndex = null;
    state.confirmedModeIndex = null;
    state.currentPanel = 0;
    state.loading = false;
    state.error = null;
    state.isOpen = true;

    if (!_modal) _buildModal();
    _modal.style.display = "flex";
    _render();

    /* If sourceJobId is preprovided, load its frequency sources immediately. */
    if (state.sourceJobId) {
      _loadFrequencySources(state.sourceJobId);
    } else {
      _loadJobs();
    }
  }

  function close() {
    state.isOpen = false;
    state.requestToken += 1;
    if (_modal) _modal.style.display = "none";
  }

  /* ---- modal DOM ---- */

  function _buildModal() {
    _modal = document.createElement("div");
    _modal.className = "modal-overlay";
    _modal.id = "tsmode-editor-modal";
    _modal.style.display = "none";
    _modal.setAttribute("role", "dialog");
    _modal.setAttribute("aria-label", _t("tsmode.modal_title", STR.MODAL_TITLE));

    var inner = document.createElement("div");
    inner.className = "modal-inner";
    inner.style.maxWidth = "640px";

    /* Header */
    var header = document.createElement("div");
    header.className = "modal-header";
    var title = document.createElement("span");
    title.className = "modal-title";
    title.textContent = _t("tsmode.modal_title", STR.MODAL_TITLE);
    header.appendChild(title);
    var closeBtn = document.createElement("button");
    closeBtn.className = "modal-close-btn";
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", _t("tsmode.cancel", STR.CANCEL));
    closeBtn.textContent = "\u00d7";
    closeBtn.addEventListener("click", close);
    header.appendChild(closeBtn);
    inner.appendChild(header);

    /* Body */
    _root = document.createElement("div");
    _root.className = "tsme-root";
    inner.appendChild(_root);

    _modal.appendChild(inner);
    document.body.appendChild(_modal);

    /* Click overlay to close */
    _modal.addEventListener("click", function (e) {
      if (e.target === _modal) close();
    });
    _modal.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); close(); }
    });
  }

  /* ---- render ---- */

  function _render() {
    if (!_root) return;
    _root.textContent = "";

    /* Panel navigation indicator */
    var nav = document.createElement("div");
    nav.className = "tsme-panels";
    var panelNames = [
      _t("tsmode.panel_source", STR.PANEL_SOURCE),
      _t("tsmode.panel_mode", STR.PANEL_MODE),
      _t("tsmode.panel_settings", STR.PANEL_SETTINGS),
    ];
    var navRow = document.createElement("div");
    navRow.style.display = "flex";
    navRow.style.gap = "4px";
    for (var pi = 0; pi < panelNames.length; pi++) {
      var dot = document.createElement("span");
      dot.className = "tsme-badge" + (pi === state.currentPanel ? " tsme-badge-hess" : "");
      dot.textContent = (pi + 1) + ". " + panelNames[pi];
      navRow.appendChild(dot);
    }
    _root.appendChild(navRow);

    /* Panel content */
    if (state.currentPanel === 0) _renderSourcePanel();
    else if (state.currentPanel === 1) _renderModePanel();
    else if (state.currentPanel === 2) _renderSettingsPanel();

    /* Footer */
    _renderFooter();
  }

  /* ---- Panel 0: Source ---- */

  function _renderSourcePanel() {
    var panel = document.createElement("div");
    panel.className = "tsme-panel tsme-active";

    var label = document.createElement("div");
    label.className = "tsme-panel-label";
    label.textContent = _t("tsmode.panel_source", STR.PANEL_SOURCE);
    panel.appendChild(label);

    /* Job picker (only when sourceJobId is not preprovided) */
    if (!state.sourceJobId) {
      var selectRow = document.createElement("div");
      selectRow.className = "tsme-job-select-row";
      var select = document.createElement("select");
      select.className = "tsme-job-select";
      select.setAttribute("aria-label", _t("tsmode.select_job", STR.SELECT_JOB));
      var defaultOpt = document.createElement("option");
      defaultOpt.value = "";
      defaultOpt.textContent = _t("tsmode.select_job", STR.SELECT_JOB);
      select.appendChild(defaultOpt);
      for (var ji = 0; ji < state.jobs.length; ji++) {
        var job = state.jobs[ji];
        var opt = document.createElement("option");
        opt.value = _esc(job.id);
        opt.textContent = _esc(job.name || job.id) + " (" + _esc(job.status) + ")";
        select.appendChild(opt);
      }
      select.addEventListener("change", function () {
        if (select.value) _loadFrequencySources(select.value);
      });
      selectRow.appendChild(select);
      panel.appendChild(selectRow);
    } else {
      var fixedJob = document.createElement("div");
      fixedJob.className = "tsme-source-note";
      fixedJob.textContent = _t("tsmode.source_job_fixed", "\u6e90\u4efb\u52a1") + ": " + _esc(state.sourceJobId);
      panel.appendChild(fixedJob);
    }

    /* Source list */
    if (state.loading) {
      var loading = document.createElement("div");
      loading.className = "tsme-loading";
      loading.textContent = _t("tsmode.loading", STR.LOADING);
      panel.appendChild(loading);
    } else if (state.error) {
      var errBox = document.createElement("div");
      errBox.className = "tsme-source-note tsme-err";
      errBox.textContent = _esc(state.error);
      panel.appendChild(errBox);
    } else if (state.sources.length === 0) {
      var empty = document.createElement("div");
      empty.className = "tsme-source-note";
      empty.textContent = _t("tsmode.no_sources", STR.NO_SOURCES);
      panel.appendChild(empty);
    } else {
      var list = document.createElement("div");
      list.className = "tsme-source-list";
      for (var si = 0; si < state.sources.length; si++) {
        list.appendChild(_renderSourceRow(state.sources[si]));
      }
      panel.appendChild(list);
    }

    _root.appendChild(panel);
  }

  function _renderSourceRow(src) {
    var disabled = !src.hessian_available;
    var selected = state.selectedSource && state.selectedSource.entry_id === src.entry_id;
    var row = document.createElement("div");
    row.className = "tsme-source-row" +
      (disabled ? " tsme-disabled" : "") +
      (selected ? " tsme-selected" : "");

    var label = document.createElement("span");
    label.className = "tsme-source-label";
    label.textContent = src.label || src.entry_id;
    row.appendChild(label);

    var meta = document.createElement("span");
    meta.className = "tsme-source-meta";
    meta.textContent = _t("tsmode.atom_count", STR.ATOM_COUNT) + ": " + src.atom_count +
      " \u00b7 " + _t("tsmode.mode_count", STR.MODE_COUNT) + ": " + src.mode_count;
    row.appendChild(meta);

    if (src.hessian_available) {
      var hessBadge = document.createElement("span");
      hessBadge.className = "tsme-badge tsme-badge-hess";
      hessBadge.textContent = _t("tsmode.hess_available", STR.HESS_AVAILABLE);
      row.appendChild(hessBadge);
    } else {
      var noHessBadge = document.createElement("span");
      noHessBadge.className = "tsme-badge tsme-badge-no-hess";
      noHessBadge.textContent = _t("tsmode.hess_missing", STR.HESS_MISSING);
      noHessBadge.title = "\u7f3a\u5c11 Hessian \u6570\u636e";
      row.appendChild(noHessBadge);
    }

    if (typeof src.imaginary_count === "number" && src.imaginary_count > 0) {
      var imagBadge = document.createElement("span");
      imagBadge.className = "tsme-badge tsme-badge-imag";
      imagBadge.textContent = _t("tsmode.imag_count", STR.IMAG_COUNT) + ": " + src.imaginary_count;
      row.appendChild(imagBadge);
    }

    if (!disabled) {
      row.addEventListener("click", function () {
        _selectSource(src);
      });
    } else {
      row.title = _t("tsmode.hess_missing", STR.HESS_MISSING);
    }

    return row;
  }

  function _selectSource(src) {
    state.selectedSource = src;
    state.previewModeIndex = null;
    state.confirmedModeIndex = null;
    state.currentPanel = 1;
    _loadVibrations();
    _render();
  }

  /* ---- Panel 1: Mode ---- */

  function _renderModePanel() {
    var panel = document.createElement("div");
    panel.className = "tsme-panel tsme-active";

    var label = document.createElement("div");
    label.className = "tsme-panel-label";
    label.textContent = _t("tsmode.panel_mode", STR.PANEL_MODE);
    panel.appendChild(label);

    if (state.loading) {
      var loading = document.createElement("div");
      loading.className = "tsme-loading";
      loading.textContent = _t("tsmode.loading", STR.LOADING);
      panel.appendChild(loading);
      _root.appendChild(panel);
      return;
    }

    if (!state.modes.length) {
      var empty = document.createElement("div");
      empty.className = "tsme-mode-empty";
      empty.textContent = _t("tsmode.no_sources", STR.NO_SOURCES);
      panel.appendChild(empty);
      _root.appendChild(panel);
      return;
    }

    /* Filter to imaginary modes */
    var imaginary = [];
    for (var i = 0; i < state.modes.length; i++) {
      var m = state.modes[i];
      if (typeof m.frequency_cm1 === "number" && m.frequency_cm1 < 0) {
        imaginary.push(m);
      }
    }

    if (imaginary.length === 0) {
      var noImag = document.createElement("div");
      noImag.className = "tsme-mode-empty";
      noImag.textContent = _t("tsmode.no_imaginary", STR.NO_IMAGINARY);
      panel.appendChild(noImag);
      _root.appendChild(panel);
      return;
    }

    /* Mode list */
    var list = document.createElement("div");
    list.className = "tsme-mode-list";
    for (var ri = 0; ri < imaginary.length; ri++) {
      list.appendChild(_renderModeRow(imaginary[ri]));
    }
    panel.appendChild(list);

    /* Hint: preview differs from confirmed */
    if (state.confirmedModeIndex != null &&
        state.previewModeIndex != null &&
        state.previewModeIndex !== state.confirmedModeIndex) {
      var hint = document.createElement("div");
      hint.className = "tsme-mode-hint";
      hint.textContent = _t("tsmode.preview_differs", STR.PREVIEW_DIFFERS);
      panel.appendChild(hint);
    }

    _root.appendChild(panel);
  }

  function _renderModeRow(mode) {
    var isPreview = state.previewModeIndex === mode.mode_index;
    var isConfirmed = state.confirmedModeIndex === mode.mode_index;
    var row = document.createElement("div");
    row.className = "tsme-mode-row" +
      (isPreview ? " tsme-preview" : "") +
      (isConfirmed ? " tsme-confirmed" : "");
    row.setAttribute("data-mode-index", _esc(mode.mode_index));

    var idx = document.createElement("span");
    idx.className = "tsme-mode-idx";
    idx.textContent = "#" + _esc(mode.mode_index);
    row.appendChild(idx);

    var freq = document.createElement("span");
    freq.className = "tsme-mode-freq tsme-imag";
    freq.textContent = mode.frequency_cm1.toFixed(2) + " " + STR.FREQ_UNIT;
    row.appendChild(freq);

    if (isConfirmed) {
      var confirmedLabel = document.createElement("span");
      confirmedLabel.className = "tsme-badge tsme-badge-hess";
      confirmedLabel.textContent = _t("tsmode.target_confirmed", STR.TARGET_CONFIRMED);
      row.appendChild(confirmedLabel);
    }

    /* Confirm button */
    var confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "tsme-mode-confirm-btn";
    confirmBtn.setAttribute("aria-label", _t("tsmode.set_target", STR.SET_TARGET));
    confirmBtn.textContent = _t("tsmode.set_target", STR.SET_TARGET);
    if (isConfirmed) {
      confirmBtn.textContent = _t("tsmode.target_confirmed", STR.TARGET_CONFIRMED);
      confirmBtn.disabled = true;
    }
    confirmBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      state.confirmedModeIndex = mode.mode_index;
      state.previewModeIndex = mode.mode_index;
      _render();
    });
    row.appendChild(confirmBtn);

    /* Click row = preview only (not confirm) */
    row.addEventListener("click", function () {
      state.previewModeIndex = mode.mode_index;
      _render();
    });

    return row;
  }

  /* ---- Panel 2: Settings ---- */

  function _renderSettingsPanel() {
    var panel = document.createElement("div");
    panel.className = "tsme-panel tsme-active";

    var label = document.createElement("div");
    label.className = "tsme-panel-label";
    label.textContent = _t("tsmode.panel_settings", STR.PANEL_SETTINGS);
    panel.appendChild(label);

    var grid = document.createElement("div");
    grid.className = "tsme-settings-grid";

    /* Inherited level (read-only) */
    var lvlLabel = document.createElement("span");
    lvlLabel.className = "tsme-settings-label";
    lvlLabel.textContent = _t("tsmode.inherited_level", STR.INHERITED_LEVEL);
    grid.appendChild(lvlLabel);
    var lvlValue = document.createElement("span");
    lvlValue.className = "tsme-settings-value";
    var src = state.selectedSource;
    lvlValue.textContent = (src && src.output_path) ? src.output_path : "--";
    grid.appendChild(lvlValue);

    /* Max iterations */
    var miLabel = document.createElement("span");
    miLabel.className = "tsme-settings-label";
    miLabel.textContent = _t("tsmode.max_iterations", STR.MAX_ITERATIONS);
    grid.appendChild(miLabel);
    var miInput = document.createElement("input");
    miInput.type = "number";
    miInput.className = "tsme-settings-input";
    miInput.id = "tsme-max-iterations";
    miInput.placeholder = "\u9ed8\u8ba4";
    miInput.min = "1";
    grid.appendChild(miInput);

    /* Trust radius */
    var trLabel = document.createElement("span");
    trLabel.className = "tsme-settings-label";
    trLabel.textContent = _t("tsmode.trust_radius", STR.TRUST_RADIUS);
    grid.appendChild(trLabel);
    var trInput = document.createElement("input");
    trInput.type = "number";
    trInput.className = "tsme-settings-input";
    trInput.id = "tsme-trust-radius";
    trInput.placeholder = "\u9ed8\u8ba4";
    trInput.step = "0.01";
    trInput.min = "0.01";
    grid.appendChild(trInput);

    /* Retry limit */
    var rlLabel = document.createElement("span");
    rlLabel.className = "tsme-settings-label";
    rlLabel.textContent = _t("tsmode.retry_limit", STR.RETRY_LIMIT);
    grid.appendChild(rlLabel);
    var rlInput = document.createElement("input");
    rlInput.type = "number";
    rlInput.className = "tsme-settings-input";
    rlInput.id = "tsme-retry-limit";
    rlInput.placeholder = "\u9ed8\u8ba4";
    rlInput.min = "0";
    grid.appendChild(rlInput);

    panel.appendChild(grid);

    /* Checkboxes */
    var cbRow1 = document.createElement("div");
    cbRow1.className = "tsme-settings-checkbox-row";
    var cbFinalFreq = document.createElement("input");
    cbFinalFreq.type = "checkbox";
    cbFinalFreq.id = "tsme-final-freq";
    cbFinalFreq.checked = true;
    cbFinalFreq.disabled = true;
    cbRow1.appendChild(cbFinalFreq);
    var cbFinalFreqLabel = document.createElement("label");
    cbFinalFreqLabel.className = "tsme-settings-checkbox-label";
    cbFinalFreqLabel.htmlFor = "tsme-final-freq";
    cbFinalFreqLabel.textContent = _t("tsmode.final_freq", STR.FINAL_FREQ);
    cbRow1.appendChild(cbFinalFreqLabel);
    panel.appendChild(cbRow1);

    var cbRow2 = document.createElement("div");
    cbRow2.className = "tsme-settings-checkbox-row";
    var cbUnverified = document.createElement("input");
    cbUnverified.type = "checkbox";
    cbUnverified.id = "tsme-allow-unverified";
    cbRow2.appendChild(cbUnverified);
    var cbUnverifiedLabel = document.createElement("label");
    cbUnverifiedLabel.className = "tsme-settings-checkbox-label";
    cbUnverifiedLabel.htmlFor = "tsme-allow-unverified";
    cbUnverifiedLabel.textContent = _t("tsmode.allow_unverified", STR.ALLOW_UNVERIFIED);
    cbRow2.appendChild(cbUnverifiedLabel);
    panel.appendChild(cbRow2);

    var unverifiedWarn = document.createElement("div");
    unverifiedWarn.className = "tsme-settings-warning";
    unverifiedWarn.textContent = _t("tsmode.allow_unverified_warn", STR.ALLOW_UNVERIFIED_WARN);
    panel.appendChild(unverifiedWarn);

    /* Recalc hess checkbox */
    var cbRow3 = document.createElement("div");
    cbRow3.className = "tsme-settings-checkbox-row";
    var cbRecalc = document.createElement("input");
    cbRecalc.type = "checkbox";
    cbRecalc.id = "tsme-recalc-hess";
    cbRow3.appendChild(cbRecalc);
    var cbRecalcLabel = document.createElement("label");
    cbRecalcLabel.className = "tsme-settings-checkbox-label";
    cbRecalcLabel.htmlFor = "tsme-recalc-hess";
    cbRecalcLabel.textContent = _t("tsmode.recalc_hess", STR.RECALC_HESS);
    cbRow3.appendChild(cbRecalcLabel);
    panel.appendChild(cbRow3);

    /* Error display */
    if (state.error) {
      var errDiv = document.createElement("div");
      errDiv.className = "tsme-error";
      errDiv.textContent = _esc(state.error);
      panel.appendChild(errDiv);
    }

    _root.appendChild(panel);
  }

  /* ---- Footer ---- */

  function _renderFooter() {
    var footer = document.createElement("div");
    footer.className = "tsme-footer";

    var nav = document.createElement("div");
    nav.className = "tsme-footer-nav";

    if (state.currentPanel > 0) {
      var backBtn = document.createElement("button");
      backBtn.type = "button";
      backBtn.className = "tsme-btn";
      backBtn.setAttribute("aria-label", _t("tsmode.back", STR.BACK));
      backBtn.textContent = _t("tsmode.back", STR.BACK);
      backBtn.addEventListener("click", function () {
        state.currentPanel = Math.max(0, state.currentPanel - 1);
        _render();
      });
      nav.appendChild(backBtn);
    }

    footer.appendChild(nav);

    var actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.gap = "6px";

    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "tsme-btn";
    cancelBtn.setAttribute("aria-label", _t("tsmode.cancel", STR.CANCEL));
    cancelBtn.textContent = _t("tsmode.cancel", STR.CANCEL);
    cancelBtn.addEventListener("click", close);
    actions.appendChild(cancelBtn);

    if (state.currentPanel < 2) {
      var nextBtn = document.createElement("button");
      nextBtn.type = "button";
      nextBtn.className = "tsme-btn tsme-btn-primary";
      nextBtn.setAttribute("aria-label", _t("tsmode.next", STR.NEXT));
      nextBtn.textContent = _t("tsmode.next", STR.NEXT);
      nextBtn.disabled = !_canAdvance();
      nextBtn.addEventListener("click", function () {
        if (_canAdvance()) {
          state.currentPanel += 1;
          _render();
        }
      });
      actions.appendChild(nextBtn);
    } else {
      var submitBtn = document.createElement("button");
      submitBtn.type = "button";
      submitBtn.className = "tsme-btn tsme-btn-primary";
      submitBtn.setAttribute("aria-label", _t("tsmode.submit", STR.SUBMIT));
      submitBtn.textContent = _t("tsmode.submit", STR.SUBMIT);
      submitBtn.disabled = !state.selectedSource || state.confirmedModeIndex == null;
      submitBtn.addEventListener("click", function () { submit(); });
      actions.appendChild(submitBtn);
    }

    footer.appendChild(actions);
    _root.appendChild(footer);
  }

  function _canAdvance() {
    if (state.currentPanel === 0) return !!state.selectedSource;
    if (state.currentPanel === 1) return state.confirmedModeIndex != null;
    return true;
  }

  /* ---- data loading ---- */

  async function _loadJobs() {
    state.loading = true;
    state.error = null;
    state.jobs = [];
    _render();
    var capturedToken = ++state.requestToken;
    try {
      /* Fetch recent jobs; filter to frequency/optimize/BatchOptimize/scan completed */
      var data = await _apiV1("/jobs?limit=100");
      if (state.requestToken !== capturedToken) return;
      var jobs = (data && data.jobs) || data || [];
      var filtered = [];
      for (var i = 0; i < jobs.length; i++) {
        var j = jobs[i];
        if (j.status !== "completed") continue;
        var wf = (j.workflow || j.spec && j.spec.workflow || "").toLowerCase();
        if (wf === "frequency" || wf === "optimize" || wf === "batchoptimize" ||
            wf === "scan" || wf === "confsearch") {
          filtered.push(j);
        }
      }
      state.jobs = filtered;
      state.loading = false;
      _render();
    } catch (e) {
      if (state.requestToken !== capturedToken) return;
      state.loading = false;
      state.error = e.message || String(e);
      _render();
    }
  }

  async function _loadFrequencySources(jobId) {
    state.loading = true;
    state.error = null;
    state.sources = [];
    state.selectedSource = null;
    _render();
    var capturedToken = ++state.requestToken;
    try {
      var data = await _apiV1("/jobs/" + encodeURIComponent(jobId) + "/frequency-sources");
      if (state.requestToken !== capturedToken) return;
      state.sources = (data && data.sources) || [];
      state.loading = false;
      /* Auto-select if only one source or if entryId matches */
      if (state.sources.length === 1) {
        _selectSource(state.sources[0]);
        return;
      }
      if (state.entryId) {
        for (var i = 0; i < state.sources.length; i++) {
          if (state.sources[i].entry_id === state.entryId) {
            _selectSource(state.sources[i]);
            return;
          }
        }
      }
      _render();
    } catch (e) {
      if (state.requestToken !== capturedToken) return;
      state.loading = false;
      state.error = e.message || String(e);
      _render();
    }
  }

  async function _loadVibrations() {
    if (!state.sourceJobId || !state.selectedSource) return;
    state.loading = true;
    state.modes = [];
    var capturedToken = ++state.requestToken;
    try {
      var entryId = state.selectedSource.entry_id;
      var data = await _apiV1(
        "/jobs/" + encodeURIComponent(state.sourceJobId) +
        "/structure-viewer/entries/" + encodeURIComponent(entryId) + "/vibrations"
      );
      if (state.requestToken !== capturedToken) return;
      state.modes = (data && data.available !== false && data.modes) ? data.modes : [];
      state.loading = false;
      /* If modeIndex was preprovided, auto-confirm it */
      if (state.modeIndex != null) {
        for (var i = 0; i < state.modes.length; i++) {
          if (state.modes[i].mode_index === state.modeIndex) {
            state.confirmedModeIndex = state.modeIndex;
            state.previewModeIndex = state.modeIndex;
            break;
          }
        }
      }
      _render();
    } catch (e) {
      if (state.requestToken !== capturedToken) return;
      state.loading = false;
      state.modes = [];
      _render();
    }
  }

  /* ---- submit ---- */

  async function submit() {
    if (!state.selectedSource || state.confirmedModeIndex == null) {
      state.error = !state.selectedSource
        ? _t("tsmode.source_required", STR.SOURCE_REQUIRED)
        : _t("tsmode.target_required", STR.TARGET_REQUIRED);
      _render();
      return;
    }

    state.loading = true;
    state.error = null;
    _render();

    var body = {
      workflow: "tsmode",
      name: _t("tsmode.job_name_prefix", STR.JOB_NAME_PREFIX) + "_" +
            state.selectedSource.entry_id + "_mode" + state.confirmedModeIndex,
      input: {
        source_job_id: state.sourceJobId || "",
        entry_id: state.selectedSource.entry_id,
        source_mode_index: state.confirmedModeIndex,
      },
    };

    /* Optional settings */
    var cbUnverified = document.getElementById("tsme-allow-unverified");
    if (cbUnverified && cbUnverified.checked) {
      body.input.allow_unverified_mapping = true;
    }
    var cbRecalc = document.getElementById("tsme-recalc-hess");
    if (cbRecalc && cbRecalc.checked) {
      body.input.recalc_hess = true;
    }
    var miInput = document.getElementById("tsme-max-iterations");
    if (miInput && miInput.value) {
      var mi = parseInt(miInput.value, 10);
      if (!isNaN(mi) && mi > 0) body.input.max_iterations = mi;
    }
    var trInput = document.getElementById("tsme-trust-radius");
    if (trInput && trInput.value) {
      var tr = parseFloat(trInput.value);
      if (isFinite(tr) && tr > 0) body.input.trust_radius = tr;
    }
    var rlInput = document.getElementById("tsme-retry-limit");
    if (rlInput && rlInput.value) {
      var rl = parseInt(rlInput.value, 10);
      if (!isNaN(rl) && rl >= 0) body.input.retry_limit = rl;
    }
    /* final_frequency is always true (checkbox disabled+checked) */
    body.input.final_frequency = true;

    try {
      var result = await _apiV1("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      state.loading = false;
      close();
      /* Refresh job list if available */
      if (typeof refreshJobs === "function") {
        try { refreshJobs(); } catch (_) { /* best-effort */ }
      }
      /* Select the new job if result contains an id */
      if (result && result.id && typeof selectJob === "function") {
        try { selectJob(result.id); } catch (_) { /* best-effort */ }
      }
    } catch (e) {
      if (state.requestToken !== capturedToken) return;
      state.loading = false;
      state.error = _t("tsmode.submit_failed", STR.SUBMIT_FAILED) + ": " + (e.message || String(e));
      /* Go back to settings panel to show error */
      state.currentPanel = 2;
      _render();
    }
  }

  /* ---- public namespace ---- */
  window.ACPTsmodeEditor = {
    version: VERSION,
    open: open,
    close: close,
    submit: submit,
    state: state,
    _fetchImpl: null,
  };
})();
