/**
 * ACP Vibration Viewer — frequency inspector list (Wave 5, todo 27)
 * @version 0.2.0
 *
 * Namespace: window.ACPVibrationViewer
 *
 * Exposes:
 *   - state                            (the live vibrationState object)
 *   - loadVibrations(jobId, entryId, opts)  (fetch GET .../vibrations; stale guard)
 *   - renderFrequencyInspector(container)   (render mode list / reason; XSS-safe)
 *   - selectMode(modeIndex)            (row click -> select + highlight)
 *
 * Pure helpers (Node-testable):
 *   - sortModesNegativesFirst(modes)   (most-negative first, then ascending; stable)
 *   - defaultModeIndex(modes)          (most-negative imaginary, else first mode)
 *   - reasonText(reason)               (reason code -> localized text)
 *
 * Internal (test-overridable via namespace property):
 *   - _fetchImpl                       (default: window.fetch; tests inject a fake)
 *   - _t                               (i18n lookup with STR-table fallback)
 *   - _esc                             (XSS-safe text conversion)
 *
 * Contract (backend, ready): GET /api/v1/jobs/{id}/structure-viewer/entries/{entryId}/vibrations
 *   -> {available, reason|null, threshold_cm1, threshold_source,
 *       modes:[{mode_index, frequency_cm1, imaginary, ir_intensity|null, vectors}],
 *       atom_count, geometry_product_id|null, source|null}
 *   reasons: no_normal_modes / geometry_mismatch / pending_fetch / historical_unavailable
 *   NEVER fabricate frequencies; NEVER render modes when available=false.
 *
 * Remaining Wave 5 todos:
 *   TODO(todo-28): displacement arrows + mode selector + toggles
 *   TODO(todo-29): requestAnimationFrame animation + controls + camera
 *   TODO(todo-30): editing/animation mutual exclusion + equilibrium restore
 *   TODO(todo-31): TS judgment hints + phase-B contract tests
 */
(function () {
  "use strict";

  /** Version tag — bump on every structural change. */
  var VERSION = "0.2.0";

  /* ---- user-visible strings (zh fallback; primary source is I18N dict via _t()) ---- */
  var STR = {
    LOADING: "\u52a0\u8f7d\u4e2d\u2026",                                   // 加载中…
    NONE: "\u65e0\u632f\u52a8\u6570\u636e",                               // 无振动数据
    IMAGINARY: "\u865a\u9891",                                             // 虚频
    IMAGINARY_SUMMARY: "\u865a\u9891 {count} / {total}\u3001{freq} cm\u207b\u00b9", // 虚频 {count} / {total}、{freq} cm⁻¹
    FREQ_UNIT: "cm\u207b\u00b9",                                           // cm⁻¹
    IR_UNIT: "km/mol",
    REASONS: {
      no_normal_modes: "\u65e0\u632f\u52a8\u6a21\u5f0f\u6570\u636e",           // 无振动模式数据
      geometry_mismatch: "\u6a21\u5f0f\u4e0e\u5f53\u524d\u51e0\u4f55\u4e0d\u5339\u914d",   // 模式与当前几何不匹配
      pending_fetch: "\u7b49\u5f85\u8fdc\u7a0b\u7ed3\u679c\u62c9\u53d6",       // 等待远程结果拉取
      historical_unavailable: "\u5386\u53f2\u4efb\u52a1\u6570\u636e\u4e0d\u53ef\u7528",   // 历史任务数据不可用
    },
  };

  /**
   * i18n lookup with STR-table fallback.
   * Tries the app's t(key) first (browser); falls back to the STR table
   * for Node.js test environments where the app's t() is unavailable.
   *
   * @param {string} key        - i18n key (e.g. "structure.vib.loading")
   * @param {string} fallback   - fallback value (STR table constant)
   * @returns {string}
   */
  function _t(key, fallback) {
    if (typeof t === "function") {
      var val = t(key);
      if (val && val !== key) return val;
    }
    return fallback;
  }

  /**
   * XSS-safe text conversion.  Values are only ever assigned to
   * textContent, never to innerHTML with server strings.
   *
   * @param {*} val
   * @returns {string}
   */
  function _esc(val) {
    if (val == null) return "";
    return String(val);
  }

  /** @returns {Function|null} */
  function _getFetchImpl() {
    return (typeof window !== "undefined" && window.ACPVibrationViewer && window.ACPVibrationViewer._fetchImpl) || null;
  }

  /* ---- state ---- */

  /**
   * @typedef {Object} VibrationState
   * @property {string|null} jobId
   * @property {string|null} entryId
   * @property {Object|null} data          - last vibrations response (or null)
   * @property {number|null} selectedModeIndex - native mode_index of the selected mode
   * @property {boolean} loading
   * @property {string|null} error
   * @property {number} requestToken       - monotonic; stale-response guard
   */
  var vibrationState = {
    jobId: null,
    entryId: null,
    data: null,
    selectedModeIndex: null,
    loading: false,
    error: null,
    requestToken: 0,
  };

  /* ---- pure helpers (exported for tests) ---- */

  /**
   * A mode is imaginary when the server flags it, or (defensive display
   * fallback) when its frequency is negative.  Never fabricates values.
   *
   * @param {Object} mode
   * @returns {boolean}
   */
  function _isImaginary(mode) {
    if (!mode) return false;
    if (mode.imaginary === true) return true;
    return typeof mode.frequency_cm1 === "number" && mode.frequency_cm1 < 0;
  }

  /**
   * Sort modes negatives-first: all negative frequencies (most-negative
   * first) before non-negative ones (ascending).  Stable on ties.
   *
   * @param {Array<Object>|null} modes
   * @returns {Array<Object>} new sorted array (input untouched)
   */
  function sortModesNegativesFirst(modes) {
    if (!modes || !modes.length) return [];
    var tagged = [];
    for (var i = 0; i < modes.length; i++) {
      tagged.push({ mode: modes[i], index: i });
    }
    tagged.sort(function (a, b) {
      var fa = a.mode.frequency_cm1;
      var fb = b.mode.frequency_cm1;
      var na = typeof fa === "number" && fa < 0;
      var nb = typeof fb === "number" && fb < 0;
      if (na && !nb) return -1;
      if (!na && nb) return 1;
      if (fa < fb) return -1;
      if (fa > fb) return 1;
      return a.index - b.index;
    });
    var out = [];
    for (var j = 0; j < tagged.length; j++) {
      out.push(tagged[j].mode);
    }
    return out;
  }

  /**
   * Default selected mode: the most-negative imaginary mode (TS default),
   * else the first mode.  Returns the native mode_index, or null when
   * there are no modes.
   *
   * @param {Array<Object>|null} modes
   * @returns {number|null}
   */
  function defaultModeIndex(modes) {
    if (!modes || !modes.length) return null;
    var best = null;
    for (var i = 0; i < modes.length; i++) {
      var m = modes[i];
      if (!_isImaginary(m)) continue;
      if (best === null || m.frequency_cm1 < best.frequency_cm1) {
        best = m;
      }
    }
    if (best !== null) return best.mode_index;
    return modes[0].mode_index;
  }

  /**
   * Map a vibrations `reason` code to localized display text.
   * Unknown reason codes are returned verbatim (exact server reason,
   * escaped by the caller) — never hidden, never fabricated.
   *
   * @param {string|null} reason
   * @returns {string|null}
   */
  function reasonText(reason) {
    if (!reason) return null;
    if (Object.prototype.hasOwnProperty.call(STR.REASONS, reason)) {
      return _t("structure.vib.reason." + reason, STR.REASONS[reason]);
    }
    return String(reason);
  }

  /* ---- fetch ---- */

  /**
   * Canonical vibrations endpoint URL.
   *
   * @param {string} jobId
   * @param {string} entryId
   * @returns {string}
   */
  function _vibrationsUrl(jobId, entryId) {
    return "/api/v1/jobs/" + encodeURIComponent(jobId) +
      "/structure-viewer/entries/" + encodeURIComponent(entryId) + "/vibrations";
  }

  /**
   * Load vibrations for a job entry.
   *
   * Stale guard: responses are discarded when a newer request (different
   * jobId/entryId or a newer token) has superseded this one.  Same
   * job+entry re-calls short-circuit to a re-render without refetching.
   *
   * @param {string} jobId
   * @param {string} entryId
   * @param {{ endpoint?: string, force?: boolean }} [opts]
   * @returns {Promise<void>}
   */
  function loadVibrations(jobId, entryId, opts) {
    opts = opts || {};
    var state = vibrationState;

    if (!opts.force && state.jobId === jobId && state.entryId === entryId &&
        (state.data || state.loading)) {
      renderFrequencyInspector();
      return Promise.resolve();
    }

    state.jobId = jobId;
    state.entryId = entryId;
    state.data = null;
    state.selectedModeIndex = null;
    state.error = null;
    state.loading = true;
    state.requestToken += 1;
    var capturedToken = state.requestToken;

    var fetchFn = _getFetchImpl();
    if (!fetchFn) {
      state.loading = false;
      state.error = "fetch not available";
      renderFrequencyInspector();
      return Promise.resolve();
    }

    var url = opts.endpoint || _vibrationsUrl(jobId, entryId);

    renderFrequencyInspector();

    return fetchFn(url, { headers: { "Accept": "application/json" } })
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status + " " + resp.statusText);
        }
        return resp.json();
      })
      .then(function (data) {
        if (state.requestToken !== capturedToken) return;
        state.loading = false;
        state.data = (data && typeof data === "object") ? data : null;
        if (state.data && state.data.available === true && state.data.modes && state.data.modes.length) {
          state.selectedModeIndex = defaultModeIndex(state.data.modes);
        } else {
          state.selectedModeIndex = null;
        }
        renderFrequencyInspector();
      })
      .catch(function (err) {
        if (state.requestToken !== capturedToken) return;
        state.loading = false;
        state.error = (err && err.message) ? err.message : String(err);
        renderFrequencyInspector();
      });
  }

  /* ---- render ---- */

  /**
   * Select a mode by native mode_index and re-render (row highlight).
   *
   * @param {number} modeIndex
   */
  function selectMode(modeIndex) {
    vibrationState.selectedModeIndex = modeIndex;
    renderFrequencyInspector();
  }

  /**
   * Render the frequency inspector into a container.
   *
   * available=true  -> imaginary summary header (when imaginary modes
   *                    exist) + all mode rows negatives-first; each row:
   *                    mode index + frequency (2dp, cm⁻¹) + 虚频 chip for
   *                    imaginary modes + IR intensity (1dp, km/mol) when
   *                    present.  Rows are click-selectable.
   * available=false -> ONLY the reason text; no mode rows are rendered.
   *
   * @param {HTMLElement} [container] - defaults to #structure-inspector-vibrations
   */
  function renderFrequencyInspector(container) {
    if (typeof document === "undefined") return;
    if (!container) {
      container = document.getElementById("structure-inspector-vibrations");
    }
    if (!container) return;

    container.textContent = "";

    var state = vibrationState;

    if (state.loading) {
      var loading = document.createElement("div");
      loading.className = "sv-vib-loading";
      loading.textContent = _t("structure.vib.loading", STR.LOADING);
      container.appendChild(loading);
      return;
    }

    if (state.error) {
      var errBox = document.createElement("div");
      errBox.className = "sv-notice sv-notice-error";
      errBox.textContent = _esc(state.error);
      container.appendChild(errBox);
      return;
    }

    var data = state.data;
    if (!data || data.available === false) {
      /* Non-available: exact reason ONLY — never render mode rows here. */
      var reasonLine = document.createElement("div");
      reasonLine.className = "sv-inspector-value sv-muted";
      reasonLine.textContent = reasonText(data && data.reason) || _t("structure.vib.none", STR.NONE);
      container.appendChild(reasonLine);
      return;
    }

    var modes = data.modes || [];
    if (!modes.length) {
      var emptyLine = document.createElement("div");
      emptyLine.className = "sv-inspector-value sv-muted";
      emptyLine.textContent = reasonText("no_normal_modes");
      container.appendChild(emptyLine);
      return;
    }

    var sorted = sortModesNegativesFirst(modes);

    /* imaginary summary header (虚频 K / N、{freq} cm⁻¹) */
    var imagCount = 0;
    for (var ci = 0; ci < modes.length; ci++) {
      if (_isImaginary(modes[ci])) imagCount++;
    }
    if (imagCount > 0) {
      var selMode = _findMode(modes, state.selectedModeIndex) || sorted[0];
      var summary = document.createElement("div");
      summary.className = "sv-vib-summary";
      summary.textContent = _format(
        _t("structure.vib.imaginary_summary", STR.IMAGINARY_SUMMARY),
        { count: imagCount, total: modes.length, freq: selMode.frequency_cm1.toFixed(2) }
      );
      container.appendChild(summary);
    }

    var list = document.createElement("div");
    list.className = "sv-vib-list";
    for (var ri = 0; ri < sorted.length; ri++) {
      list.appendChild(_renderModeRow(sorted[ri]));
    }
    container.appendChild(list);
  }

  function _findMode(modes, modeIndex) {
    for (var i = 0; i < modes.length; i++) {
      if (modes[i].mode_index === modeIndex) return modes[i];
    }
    return null;
  }

  function _renderModeRow(mode) {
    var imag = _isImaginary(mode);
    var row = document.createElement("div");
    row.className = "sv-vib-row" + (imag ? " sv-vib-row-imaginary" : "");
    if (mode.mode_index === vibrationState.selectedModeIndex) {
      row.className += " sv-active";
    }
    row.setAttribute("data-mode-index", _esc(mode.mode_index));

    var idx = document.createElement("span");
    idx.className = "sv-vib-mode-idx";
    idx.textContent = "#" + _esc(mode.mode_index);
    row.appendChild(idx);

    var freq = document.createElement("span");
    freq.className = "sv-vib-freq" + (imag ? " sv-vib-freq-imag" : "");
    freq.textContent = mode.frequency_cm1.toFixed(2) + " " + STR.FREQ_UNIT;
    row.appendChild(freq);

    if (imag) {
      var chip = document.createElement("span");
      chip.className = "sv-vib-chip";
      chip.textContent = _t("structure.vib.imaginary_chip", STR.IMAGINARY);
      row.appendChild(chip);
    }

    if (mode.ir_intensity != null && typeof mode.ir_intensity === "number") {
      var ir = document.createElement("span");
      ir.className = "sv-vib-ir";
      ir.textContent = mode.ir_intensity.toFixed(1) + " " + STR.IR_UNIT;
      row.appendChild(ir);
    }

    row.addEventListener("click", function () {
      selectMode(mode.mode_index);
    });

    return row;
  }

  /**
   * Replace {placeholder} tokens in a template string.
   *
   * @param {string} template
   * @param {Object} vars
   * @returns {string}
   */
  function _format(template, vars) {
    return String(template).replace(/\{(\w+)\}/g, function (_all, name) {
      return Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : "{" + name + "}";
    });
  }

  /* ---- public namespace ---- */
  window.ACPVibrationViewer = {
    version: VERSION,
    state: vibrationState,
    STR: STR,
    loadVibrations: loadVibrations,
    renderFrequencyInspector: renderFrequencyInspector,
    selectMode: selectMode,
    sortModesNegativesFirst: sortModesNegativesFirst,
    defaultModeIndex: defaultModeIndex,
    reasonText: reasonText,
    _t: _t,
    _esc: _esc,
    _vibrationsUrl: _vibrationsUrl,
    _fetchImpl: (typeof window !== "undefined" && window.fetch) ? window.fetch.bind(window) : null,
  };
})();
