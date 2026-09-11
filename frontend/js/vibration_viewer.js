/**
 * ACP Vibration Viewer — frequency inspector + arrows + animation (Wave 5, todos 27-31)
 * @version 0.7.0
 *
 * Namespace: window.ACPVibrationViewer
 *
 * Exposes:
 *   - state                            (the live vibrationState object)
 *   - loadVibrations(jobId, entryId, opts)  (fetch GET .../vibrations; stale guard)
 *   - renderFrequencyInspector(container)   (render mode list / reason; XSS-safe)
 *   - selectMode(modeIndex)            (row/dropdown click -> select + highlight)
 *   - arrowState                       (the live arrow display state)
 *   - refreshArrows()                  (clear + re-apply arrows on the MAIN viewer)
 *   - setDisplayMode(mode)             ("arrows" | "animation" | "combo")
 *   - setAmplitude(v)                  (clamped amplitude setter)
 *   - playAnimation() / pauseAnimation() / togglePlay()
 *   - stopAnimationAndRestore()      (cancel rAF + exact equilibrium restore
 *                                     + editor unlock; idempotent)
 *   - stopAnimation()                (alias of stopAnimationAndRestore)
 *   - isAnimationActive()            (mutual-exclusion probe)
 *   - handleTeardown()               (tab/job switch + viewer destroy hook)
 *   - setSpeed(v) / toggleInvertPhase()
 *   - tsJudgment(modes, thresholdCm1)  (PURE: significant-imaginary evidence)
 *   - tsHintText(hint) / tsSuffixText() (TS evidence i18n)
 *   - thresholdText(cm1, source)       (PURE: threshold display line)
 *   - geometryMismatch(entry, vibData) (PURE: product-id mismatch guard)
 *   - animationState                   (the live animation state)
 *
 * Pure helpers (Node-testable):
 *   - sortModesNegativesFirst(modes)   (most-negative first, then ascending; stable)
 *   - defaultModeIndex(modes)          (most-negative imaginary, else first mode)
 *   - reasonText(reason)               (reason code -> localized text)
 *   - computeArrowEndpoints(coords, vectors, amplitude) (per-atom {start,end}|null)
 *   - clampAmplitude(v)                ([0.05, 0.6], default 0.25)
 *   - hydrogenSkipMask(symbols, atomCount) (skip H above H_SKIP_ATOM_THRESHOLD=50)
 *   - applyArrows(viewer, endpoints, symbols, opts) -> count (viewer.addArrow)
 *   - clearArrows(viewer)              (viewer.removeAllShapes)
 *   - computeDisplacedCoords(coords, vectors, amp, phase, opts) (r_i = r_i0 + A·sin(φ)·n_i)
 *   - buildDisplacedXyz(symbols, coords, comment)     (atom order unchanged)
 *   - clampSpeed(v)                    ([0.25, 2.0], default 1)
 *
 * Internal (test-overridable via namespace property):
 *   - _fetchImpl                       (default: window.fetch; tests inject a fake)
 *   - _viewerImpl                      (default: resolves the app's main `viewer`)
 *   - _rafImpl / _cancelRafImpl / _nowImpl (rAF loop injectables for Node tests)
 *   - _t                               (i18n lookup with STR-table fallback)
 *   - _esc                             (XSS-safe text conversion)
 *
 * Contract (backend, ready): GET /api/v1/jobs/{id}/structure-viewer/entries/{entryId}/vibrations
 *   -> {available, reason|null, threshold_cm1, threshold_source,
 *       modes:[{mode_index, frequency_cm1, imaginary, ir_intensity|null, vectors}],
 *       atom_count, geometry_product_id|null, source|null}
 *   reasons: no_normal_modes / geometry_mismatch / pending_fetch / historical_unavailable
 *   NEVER fabricate frequencies; NEVER render modes when available=false.
 *   Stored mode vectors are NEVER mutated (normalization is display-only).
 *   Arrows/animation are drawn on the app's MAIN 3Dmol viewer instance only.
 *   The animation loop NEVER uses the viewer built-in animation API (it has
 *   no variable speed or phase) and NEVER changes atom ordering; the camera
 *   is preserved via getView()/setView() around every per-frame rebuild.
 *   While playing, geometry editing is disabled via
 *   ACPStructureEditor.setLocked(true) (mutual exclusion, todo 30); every
 *   teardown path (tab switch / job switch / entry switch / viewer destroy)
 *   funnels through stopAnimationAndRestore()/handleTeardown() so no loop
 *   or stale arrows survive.
 *   or stale arrows survive.  TS judgment hints are DISPLAY EVIDENCE only —
 *   they never replace the BatchOptimize/IRC validation status.
 *
 * Wave 5 complete (todos 27-31). Wave 6 (geometry editor) follows.
 */
(function () {
  "use strict";

  /** Version tag — bump on every structural change. */
  var VERSION = "0.7.0";

  /* ---- user-visible strings (zh fallback; primary source is I18N dict via _t()) ---- */
  var STR = {
    LOADING: "\u52a0\u8f7d\u4e2d\u2026",
    NONE: "\u65e0\u632f\u52a8\u6570\u636e",
    IMAGINARY: "\u865a\u9891",
    IMAGINARY_SUMMARY: "\u663e\u8457\u865a\u9891 {count} \u4e2a",
    FREQ_UNIT: "cm\u207b\u00b9",
    IR_UNIT: "km\u00b7mol\u207b\u00b9",
    MODE: "\u6a21\u5f0f",
    AMPLITUDE: "\u632f\u5e45",
    DISPLAY_ARROWS: "\u7bad\u5934",
    DISPLAY_ANIMATION: "\u52a8\u753b",
    DISPLAY_COMBO: "\u7bad\u5934+\u52a8\u753b",
    NO_GEOMETRY: "\u6682\u65e0\u5f53\u524d\u7ed3\u6784\u7684\u51e0\u4f55\u5750\u6807",
    UNIT_ANGSTROM: "\u00c5",
    PLAY: "\u64ad\u653e",
    PAUSE: "\u6682\u505c",
    SPEED: "\u901f\u5ea6",
    INVERT: "\u76f8\u4f4d\u53cd\u8f6c",
    LOCKED_HINT: "\u52a8\u753b\u64ad\u653e\u4e2d\uff0c\u7f16\u8f91\u5df2\u6682\u505c",
    DOCK_TITLE: "\u632f\u52a8\u6a21\u5f0f",
    FILTER_IMAGINARY: "\u865a\u9891 {count}",
    FILTER_VALID: "\u6709\u6548\u6a21\u5f0f {count}",
    FILTER_ALL: "\u5168\u90e8 {count}",
    ZERO_MODES: "\u5e73\u79fb/\u8f6c\u52a8\u96f6\u6a21 {count} \u4e2a",
    POSITIVE_LABEL: "\u6b63\u9891",
    IMAGINARY_LABEL: "\u865a\u9891",
    TS_HINTS: {
      first_order: "\u9891\u7387\u6570\u91cf\u7b26\u5408\u4e00\u9636\u978d\u70b9",
      no_evidence: "\u4e0d\u662f\u4e00\u9636\u978d\u70b9\u8bc1\u636e",
      higher_order: "\u9ad8\u9636\u978d\u70b9\u6216\u672a\u5145\u5206\u4f18\u5316",
    },
    TS_SUFFIX: "\u4ecd\u9700\u68c0\u67e5\u632f\u52a8\u65b9\u5411\u53ca IRC",
    THRESHOLD_LABEL: "\u663e\u8457\u865a\u9891\u9608\u503c",
    SOURCE_DEFAULT: "\u9ed8\u8ba4",
    SOURCE_JOB_CONFIG: "\u4efb\u52a1\u914d\u7f6e",
    MISMATCH_REASON: "\u6a21\u5f0f\u4e0e\u5f53\u524d\u51e0\u4f55\u4e0d\u5339\u914d",
    REASONS: {
      no_normal_modes: "\u65e0\u632f\u52a8\u6a21\u5f0f\u6570\u636e",
      geometry_mismatch: "\u6a21\u5f0f\u4e0e\u5f53\u524d\u51e0\u4f55\u4e0d\u5339\u914d",
      pending_fetch: "\u7b49\u5f85\u8fdc\u7a0b\u7ed3\u679c\u62c9\u53d6",
      historical_unavailable: "\u5386\u53f2\u4efb\u52a1\u6570\u636e\u4e0d\u53ef\u7528",
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

  /* ---- arrow display constants (todo 28) ---- */

  /** Above this atom count, hydrogen displacement arrows are skipped. */
  var H_SKIP_ATOM_THRESHOLD = 50;
  var AMP_MIN = 0.05;
  var AMP_MAX = 0.6;
  var AMP_DEFAULT = 0.25;
  var ARROW_RADIUS = 0.06;
  /** Vectors with norm below this are treated as zero-length (no arrow). */
  var VECTOR_EPS = 1e-8;
  var COLOR_IMAGINARY = "#e55353";
  var COLOR_NEUTRAL = "#4ea1ff";

  /**
   * @typedef {Object} ArrowState
   * @property {boolean} enabled      - arrows applicable (mode + geometry available)
   * @property {number} amplitude     - arrow length in Å (clamped [AMP_MIN, AMP_MAX])
   * @property {number|null} modeIndex - mode the arrows depict (mirrors selection)
   * @property {string} displayMode   - "arrows" | "animation" | "combo"
   * @property {number} _lastCount    - arrows actually drawn by the last refresh
   * @property {string|null} _hint    - why arrows are unavailable (shown in UI)
   */
  var arrowState = {
    enabled: false,
    amplitude: AMP_DEFAULT,
    modeIndex: null,
    displayMode: "arrows",
    _lastCount: 0,
    _hint: null,
  };

  /* ---- animation constants (todo 29) ---- */

  /** Minimum interval between rendered frames (~30 fps throttle). */
  var FRAME_MIN_MS = 33;
  /** Oscillation rate in cycles per second at speed 1x. */
  var CYCLE_RATE_HZ = 0.6;
  var SPEED_MIN = 0.25;
  var SPEED_MAX = 2.0;
  var SPEED_OPTIONS = [0.25, 0.5, 1, 2];

  /**
   * @typedef {Object} AnimationState
   * @property {boolean} playing
   * @property {number} phase        - oscillation phase in radians
   * @property {number} speed        - playback speed multiplier (0.25-2.0)
   * @property {number} amplitude    - mirrors arrowState.amplitude while playing
   * @property {boolean} invertPhase - flip the displacement sign
   * @property {number|null} rafHandle
   * @property {number|null} lastFrameTs
   * @property {Object|null} savedView   - last camera view captured per frame
   * @property {string|null} equilibriumXyz - snapshot for exact restore on stop
   * @property {boolean} _active      - a session has played since last stop
   */
  var animationState = {
    playing: false,
    phase: 0,
    speed: 1,
    amplitude: AMP_DEFAULT,
    invertPhase: false,
    rafHandle: null,
    lastFrameTs: null,
    savedView: null,
    equilibriumXyz: null,
    _active: false,
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
    var bestSigNeg = null;
    var bestOtherNeg = null;
    var firstPos = null;
    var threshold = -50.0;
    for (var i = 0; i < modes.length; i++) {
      var m = modes[i];
      var f = m.frequency_cm1;
      if (typeof f !== "number" || !isFinite(f)) continue;
      if (f < 0) {
        if (f <= threshold) {
          if (bestSigNeg === null || f < bestSigNeg.frequency_cm1) bestSigNeg = m;
        } else {
          if (bestOtherNeg === null || f < bestOtherNeg.frequency_cm1) bestOtherNeg = m;
        }
      } else if (f > 0 && firstPos === null) {
        firstPos = m;
      }
    }
    if (bestSigNeg !== null) return bestSigNeg.mode_index;
    if (bestOtherNeg !== null) return bestOtherNeg.mode_index;
    if (firstPos !== null) return firstPos.mode_index;
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

  /* ---- displacement arrows (todo 28) ---- */

  /**
   * Clamp an amplitude value to [AMP_MIN, AMP_MAX]; non-finite input
   * falls back to AMP_DEFAULT.
   *
   * @param {*} v
   * @returns {number}
   */
  function clampAmplitude(v) {
    var n = typeof v === "number" ? v : parseFloat(v);
    if (!isFinite(n)) return AMP_DEFAULT;
    return Math.min(AMP_MAX, Math.max(AMP_MIN, n));
  }

  /**
   * Per-atom hydrogen skip mask.  At or below H_SKIP_ATOM_THRESHOLD atoms
   * nothing is skipped; above it, hydrogen rows are masked (skipped).
   *
   * @param {Array<string>|null} symbols
   * @param {number} [atomCount] - defaults to symbols.length
   * @returns {Array<boolean>}
   */
  function hydrogenSkipMask(symbols, atomCount) {
    if (!symbols || !symbols.length) return [];
    var n = (atomCount == null) ? symbols.length : atomCount;
    var mask = [];
    for (var i = 0; i < symbols.length; i++) {
      var sym = String(symbols[i] || "").trim().toUpperCase();
      mask.push(n > H_SKIP_ATOM_THRESHOLD && sym === "H");
    }
    return mask;
  }

  /**
   * Compute per-atom arrow endpoints: end = start + normalize(v) * amp.
   *
   * Zero-length (norm < VECTOR_EPS) and NaN-bearing vectors yield null rows
   * (no arrow, never NaN).  The stored vectors array is read-only here —
   * normalization results go into fresh arrays, never written back.
   *
   * @param {Array<Array<number>>|null} equilibriumCoords - [[x,y,z], ...]
   * @param {Array<Array<number>>|null} vectors           - [[x,y,z], ...]
   * @param {number} amplitude
   * @returns {Array<{start:[number,number,number], end:[number,number,number]}|null>|null}
   */
  function computeArrowEndpoints(equilibriumCoords, vectors, amplitude) {
    if (!equilibriumCoords || !vectors) return null;
    var amp = clampAmplitude(amplitude);
    var rows = [];
    for (var i = 0; i < equilibriumCoords.length; i++) {
      var c = equilibriumCoords[i];
      var v = vectors[i];
      if (!c || !v || c.length < 3 || v.length < 3) {
        rows.push(null);
        continue;
      }
      var cx = +c[0], cy = +c[1], cz = +c[2];
      var vx = +v[0], vy = +v[1], vz = +v[2];
      var norm = Math.sqrt(vx * vx + vy * vy + vz * vz);
      if (!isFinite(norm) || norm < VECTOR_EPS ||
          !isFinite(cx) || !isFinite(cy) || !isFinite(cz)) {
        rows.push(null);
        continue;
      }
      var k = amp / norm;
      rows.push({
        start: [cx, cy, cz],
        end: [cx + vx * k, cy + vy * k, cz + vz * k],
      });
    }
    return rows;
  }

  /**
   * Draw arrows on a 3Dmol viewer via viewer.addArrow; returns the number
   * of arrows added.  Null endpoints and masked (skipped-H) rows draw
   * nothing.
   *
   * @param {Object} viewerObj  - 3Dmol viewer (must expose addArrow)
   * @param {Array<Object>|null} endpoints - from computeArrowEndpoints
   * @param {Array<string>|null} symbols   - for the H skip mask
   * @param {{ color?: string }} [opts]
   * @returns {number}
   */
  function applyArrows(viewerObj, endpoints, symbols, opts) {
    opts = opts || {};
    if (!viewerObj || typeof viewerObj.addArrow !== "function" || !endpoints) {
      return 0;
    }
    var color = opts.color || COLOR_NEUTRAL;
    var mask = symbols ? hydrogenSkipMask(symbols, endpoints.length) : null;
    var count = 0;
    for (var i = 0; i < endpoints.length; i++) {
      var ep = endpoints[i];
      if (!ep || (mask && mask[i])) continue;
      viewerObj.addArrow({
        start: { x: ep.start[0], y: ep.start[1], z: ep.start[2] },
        end: { x: ep.end[0], y: ep.end[1], z: ep.end[2] },
        radius: ARROW_RADIUS,
        color: color,
      });
      count += 1;
    }
    return count;
  }

  /**
   * Clear all shapes (arrows) from a viewer via viewer.removeAllShapes().
   *
   * @param {Object} viewerObj
   */
  function clearArrows(viewerObj) {
    if (viewerObj && typeof viewerObj.removeAllShapes === "function") {
      viewerObj.removeAllShapes();
    }
  }

  /**
   * Resolve the app's MAIN 3Dmol viewer instance (the one displaying the
   * current entry).  Never creates a viewer.  Tests (and future callers)
   * may inject `_viewerImpl` on the namespace.
   *
   * @returns {Object|null}
   */
  function _getMainViewer() {
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer._viewerImpl === "function") {
      return window.ACPVibrationViewer._viewerImpl();
    }
    /* The app declares the main viewer as a top-level `let viewer` in the
       inline script — reachable here as a global lexical binding. */
    try {
      if (typeof viewer !== "undefined" && viewer) return viewer;
    } catch (_) { /* not loaded — stay null */ }
    return null;
  }

  function _selectedMode() {
    var data = vibrationState.data;
    if (!data || data.available === false || !data.modes) return null;
    return _findMode(data.modes, vibrationState.selectedModeIndex);
  }

  /**
   * Gather the animation/arrow geometry context: selected mode + the
   * displayed structure's coords/symbols, guarded by entry identity and
   * atom-count match.  Shared by refreshArrows and the animation loop.
   *
   * @returns {{ok: boolean, hint: string|null, mode: Object|null,
   *            coords: Array|null, symbols: Array|null}}
   */
  function _vibGeometry() {
    var mode = _selectedMode();
    if (!mode || !mode.vectors) {
      return { ok: false, hint: null, mode: mode, coords: null, symbols: null };
    }
    var svState = (typeof window !== "undefined" && window.ACPStructureViewer)
      ? window.ACPStructureViewer.state
      : null;
    var coords = svState ? svState.displayedCoords : null;
    var symbols = svState ? svState.displayedSymbols : null;
    var sameEntry = !!(svState && svState.displayedEntryId &&
      svState.displayedEntryId === vibrationState.entryId);
    if (!coords || !sameEntry) {
      return {
        ok: false,
        hint: _t("structure.vib.arrow.no_geometry", STR.NO_GEOMETRY),
        mode: mode, coords: coords, symbols: symbols,
      };
    }
    if (mode.vectors.length !== coords.length) {
      return {
        ok: false,
        hint: reasonText("geometry_mismatch"),
        mode: mode, coords: coords, symbols: symbols,
      };
    }
    if (geometryMismatch(_catalogEntry(), vibrationState.data)) {
      return {
        ok: false,
        hint: _t("structure.vib.ts.mismatch_reason", STR.MISMATCH_REASON),
        mode: mode, coords: coords, symbols: symbols,
      };
    }
    return { ok: true, hint: null, mode: mode, coords: coords, symbols: symbols };
  }

  /**
   * Whether arrows are drawn for the current displayMode.  "animation"
   * hides arrows until todo 29 lands; "arrows" and "combo" show them.
   *
   * @returns {boolean}
   */
  function _arrowsVisible() {
    return arrowState.enabled && arrowState.displayMode !== "animation";
  }

  /**
   * Clear and re-apply displacement arrows on the main viewer for the
   * selected mode.  Sets arrowState.enabled/_hint; never throws.  Arrows
   * are disabled with a hint when the displayed geometry is missing or
   * its atom count does not match the mode vectors.  While the animation
   * loop is playing it owns the canvas — this is a no-op.
   */
  function refreshArrows() {
    if (animationState.playing) return;
    var viewerObj = _getMainViewer();
    clearArrows(viewerObj);
    arrowState._lastCount = 0;
    arrowState.modeIndex = vibrationState.selectedModeIndex;

    var ctx = _vibGeometry();
    var hint = ctx.hint;
    if (ctx.ok && _arrowsVisible()) {
      var endpoints = computeArrowEndpoints(ctx.coords, ctx.mode.vectors, arrowState.amplitude);
      var color = _isImaginary(ctx.mode) ? COLOR_IMAGINARY : COLOR_NEUTRAL;
      arrowState._lastCount = applyArrows(viewerObj, endpoints, ctx.symbols, { color: color });
      if (viewerObj && typeof viewerObj.render === "function") {
        try { viewerObj.render(); } catch (_) { /* render is best-effort */ }
      }
      arrowState.enabled = true;
      arrowState._hint = null;
      return;
    }
    arrowState.enabled = false;
    arrowState._hint = hint;
  }

  /**
   * Three-state display toggle: "arrows" | "animation" | "combo".
   * Switching to pure "arrows" while playing stops the loop (equilibrium
   * restore + static arrows); animation<->combo switches keep playing and
   * the loop picks up arrow visibility on the next frame.
   *
   * @param {string} mode
   */
  function setDisplayMode(mode) {
    if (mode !== "arrows" && mode !== "animation" && mode !== "combo") return;
    arrowState.displayMode = mode;
    if (animationState.playing && mode === "arrows") {
      stopAnimation();
      return;
    }
    if (animationState.playing) {
      return;
    }
    refreshArrows();
    renderFrequencyInspector();
  }

  /**
   * Clamped amplitude setter (programmatic + slider handler).
   *
   * @param {*} v
   */
  function setAmplitude(v) {
    arrowState.amplitude = clampAmplitude(v);
    refreshArrows();
  }

  /* ---- mode animation (todo 29) ---- */

  /**
   * Displaced coordinates at oscillation phase `phase`:
   * r_i = r_i0 + A * sin(phase) * normalize(v_i) (sign flipped when
   * opts.invert).  Zero-length/NaN vectors and opts.skipMask rows stay at
   * equilibrium.  The stored vectors array is read-only here.
   *
   * @param {Array<Array<number>>|null} equilibriumCoords
   * @param {Array<Array<number>>|null} vectors
   * @param {number} amplitude
   * @param {number} phase - radians
   * @param {{ invert?: boolean, skipMask?: Array<boolean> }} [opts]
   * @returns {Array<Array<number>>|null}
   */
  function computeDisplacedCoords(equilibriumCoords, vectors, amplitude, phase, opts) {
    if (!equilibriumCoords || !vectors) return null;
    opts = opts || {};
    var amp = clampAmplitude(amplitude);
    var sign = opts.invert ? -1 : 1;
    var s = amp * Math.sin(phase) * sign;
    var out = [];
    for (var i = 0; i < equilibriumCoords.length; i++) {
      var c = equilibriumCoords[i];
      var v = vectors[i];
      if (!c || c.length < 3) { out.push(null); continue; }
      var cx = +c[0], cy = +c[1], cz = +c[2];
      if (!isFinite(cx) || !isFinite(cy) || !isFinite(cz)) { out.push(null); continue; }
      var placed = false;
      if (v && v.length >= 3 && !(opts.skipMask && opts.skipMask[i])) {
        var vx = +v[0], vy = +v[1], vz = +v[2];
        var norm = Math.sqrt(vx * vx + vy * vy + vz * vz);
        if (isFinite(norm) && norm >= VECTOR_EPS) {
          var k = s / norm;
          out.push([cx + vx * k, cy + vy * k, cz + vz * k]);
          placed = true;
        }
      }
      if (!placed) {
        out.push([cx, cy, cz]);
      }
    }
    return out;
  }

  /**
   * Build XYZ text from symbols + coordinates.  The symbol sequence (atom
   * ordering) is preserved exactly; coordinates are written at 6 decimals
   * (matching the app's atomsToXYZ).
   *
   * @param {Array<string>|null} symbols
   * @param {Array<Array<number>>|null} coords
   * @param {string} [comment]
   * @returns {string} XYZ text ("" when inputs are unusable)
   */
  function buildDisplacedXyz(symbols, coords, comment) {
    if (!symbols || !coords || symbols.length !== coords.length || !symbols.length) {
      return "";
    }
    var out = String(coords.length) + "\n" + (comment || "") + "\n";
    for (var i = 0; i < coords.length; i++) {
      var c = coords[i];
      if (!c || c.length < 3) return "";
      out += symbols[i] + " " + (+c[0]).toFixed(6) + " " +
        (+c[1]).toFixed(6) + " " + (+c[2]).toFixed(6) + "\n";
    }
    return out;
  }

  /**
   * Clamp a playback speed multiplier to [SPEED_MIN, SPEED_MAX]
   * (non-finite input falls back to 1).
   *
   * @param {*} v
   * @returns {number}
   */
  function clampSpeed(v) {
    var n = typeof v === "number" ? v : parseFloat(v);
    if (!isFinite(n)) return 1;
    return Math.min(SPEED_MAX, Math.max(SPEED_MIN, n));
  }

  /* ---- TS judgment evidence (todo 31 — display only) ---- */

  /**
   * Count significant imaginary frequencies (frequency_cm1 <= threshold,
   * matching the backend _count_significant_imaginary at-or-below rule)
   * and classify the evidence hint:
   *   1  -> "first_order"   (频率数量符合一阶鞍点)
   *   0  -> "no_evidence"   (不是一阶鞍点证据)
   *   >1 -> "higher_order"  (高阶鞍点或未充分优化)
   * Evidence only — never replaces BatchOptimize/IRC validation status.
   *
   * @param {Array<Object>|null} modes
   * @param {number} [thresholdCm1] - defaults to -50.0 when non-finite
   * @returns {{significantCount: number, hint: string}}
   */
  function tsJudgment(modes, thresholdCm1) {
    var thr = (typeof thresholdCm1 === "number" && isFinite(thresholdCm1))
      ? thresholdCm1
      : -50.0;
    var count = 0;
    if (modes && modes.length) {
      for (var i = 0; i < modes.length; i++) {
        var f = modes[i] ? modes[i].frequency_cm1 : null;
        if (typeof f === "number" && isFinite(f) && f <= thr) {
          count += 1;
        }
      }
    }
    var hint = count === 1 ? "first_order" : (count > 1 ? "higher_order" : "no_evidence");
    return { significantCount: count, hint: hint };
  }

  /**
   * Localized TS evidence hint text (without the always-on suffix).
   *
   * @param {string} hint - "first_order" | "no_evidence" | "higher_order"
   * @returns {string} "" for unknown hints
   */
  function tsHintText(hint) {
    if (!Object.prototype.hasOwnProperty.call(STR.TS_HINTS, hint)) return "";
    return _t("structure.vib.ts." + hint, STR.TS_HINTS[hint]);
  }

  /**
   * The always-appended verification reminder (仍需检查振动方向及 IRC).
   *
   * @returns {string}
   */
  function tsSuffixText() {
    return _t("structure.vib.ts.suffix", STR.TS_SUFFIX);
  }

  /**
   * Threshold display line, e.g. "显著虚频阈值 ≤ -50.0 cm⁻¹ (默认)".
   *
   * @param {number} thresholdCm1
   * @param {string} [thresholdSource] - "default" | "job_config"
   * @returns {string}
   */
  function thresholdText(thresholdCm1, thresholdSource) {
    var val = (typeof thresholdCm1 === "number" && isFinite(thresholdCm1))
      ? thresholdCm1
      : -50.0;
    var srcLabel = thresholdSource === "job_config"
      ? _t("structure.vib.ts.source_job_config", STR.SOURCE_JOB_CONFIG)
      : _t("structure.vib.ts.source_default", STR.SOURCE_DEFAULT);
    return _t("structure.vib.ts.threshold_label", STR.THRESHOLD_LABEL) +
      " \u2264 " + val.toFixed(1) + " " + STR.FREQ_UNIT + " (" + srcLabel + ")";
  }

  /**
   * Geometry-identity mismatch guard: true only when BOTH the vibrations
   * response carries a geometry_product_id AND the catalog entry has a
   * source.product_id AND they differ.  Either side null/unknown -> false
   * (allow; historical projection has a null product id by design).
   *
   * @param {Object|null} entry   - catalog entry (entry.source.product_id)
   * @param {Object|null} vibData - vibrations response (geometry_product_id)
   * @returns {boolean}
   */
  function geometryMismatch(entry, vibData) {
    if (!entry || !vibData) return false;
    var vibPid = vibData.geometry_product_id;
    var entryPid = (entry.source && entry.source.product_id) ? entry.source.product_id : null;
    if (vibPid == null || entryPid == null) return false;
    return String(vibPid) !== String(entryPid);
  }

  /**
   * Resolve the currently selected catalog entry from the structure
   * viewer's payload (by vibrationState.entryId).
   *
   * @returns {Object|null}
   */
  function _catalogEntry() {
    var svState = (typeof window !== "undefined" && window.ACPStructureViewer)
      ? window.ACPStructureViewer.state
      : null;
    if (!svState || !svState.payload || !svState.payload.entries) return null;
    var entries = svState.payload.entries;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === vibrationState.entryId) return entries[i];
    }
    return null;
  }

  /** @returns {Function|null} */
  function _getRafImpl() {
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer._rafImpl === "function") {
      return window.ACPVibrationViewer._rafImpl;
    }
    if (typeof requestAnimationFrame === "function") {
      return requestAnimationFrame;
    }
    return null;
  }

  /** @returns {Function|null} */
  function _getCancelRafImpl() {
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer._cancelRafImpl === "function") {
      return window.ACPVibrationViewer._cancelRafImpl;
    }
    if (typeof cancelAnimationFrame === "function") {
      return cancelAnimationFrame;
    }
    return null;
  }

  /** @returns {number} monotonic-ish timestamp in ms */
  function _now() {
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer._nowImpl === "function") {
      return window.ACPVibrationViewer._nowImpl();
    }
    return Date.now();
  }

  /**
   * Equilibrium XYZ text: svState.equilibriumXyz raw text when available,
   * otherwise rebuilt from displayedCoords/Symbols (displayed geometry IS
   * the equilibrium — the app loads it via the _svLoadXyzToViewer bridge).
   *
   * @returns {string|null}
   */
  function _buildEquilibriumXyz() {
    var svState = (typeof window !== "undefined" && window.ACPStructureViewer)
      ? window.ACPStructureViewer.state
      : null;
    if (!svState) return null;
    if (typeof svState.equilibriumXyz === "string" && svState.equilibriumXyz) {
      return svState.equilibriumXyz;
    }
    if (svState.displayedCoords && svState.displayedSymbols &&
        svState.displayedCoords.length === svState.displayedSymbols.length) {
      return buildDisplacedXyz(svState.displayedSymbols, svState.displayedCoords, "");
    }
    return null;
  }

  /**
   * Re-apply the app's model style after a per-frame rebuild so the look
   * matches the main viewer (applyStylePreset(molDoc.style) + invisible
   * clickspheres, mirroring renderMolDoc).  Falls back to a neutral
   * sphere/stick style when the app functions are unavailable.
   *
   * @param {Object} viewerObj
   */
  function _styleModel(viewerObj) {
    try {
      if (typeof applyStylePreset === "function" &&
          typeof molDoc !== "undefined" && molDoc && molDoc.style) {
        applyStylePreset(molDoc.style);
        if (typeof getCurrentSphereScale === "function" &&
            typeof viewerObj.addStyle === "function") {
          viewerObj.addStyle({}, {
            clicksphere: { radius: Math.max(0.3, getCurrentSphereScale() * 1.5) },
          });
        }
        return;
      }
    } catch (_) { /* fall through to the default style below */ }
    if (typeof viewerObj.addStyle === "function") {
      viewerObj.addStyle({}, { sphere: { scale: 0.22 }, stick: { radius: 0.12 } });
    }
  }

  /**
   * Render one animation frame: capture the camera ONCE, rebuild the model
   * from displaced XYZ (removeAllModels + addModel + style), clear shapes,
   * re-apply arrows anchored at the displaced positions when the display
   * mode is "combo", then restore the camera and render.  Never uses the
   * viewer built-in animation API; never re-frames the camera.
   *
   * @param {Object} viewerObj
   * @param {Array} displacedCoords
   * @param {Array} symbols
   * @param {{ mode: Object, coords: Array, vectors: Array }} ctx
   */
  function _renderAnimationFrame(viewerObj, displacedCoords, symbols, ctx) {
    var savedView = (typeof viewerObj.getView === "function") ? viewerObj.getView() : null;
    if (typeof viewerObj.removeAllModels === "function") viewerObj.removeAllModels();
    var xyzText = buildDisplacedXyz(symbols, displacedCoords, "");
    viewerObj.addModel(xyzText, "xyz");
    _styleModel(viewerObj);
    if (typeof viewerObj.removeAllShapes === "function") viewerObj.removeAllShapes();
    if (arrowState.displayMode === "combo") {
      var endpoints = computeArrowEndpoints(displacedCoords, ctx.mode.vectors, arrowState.amplitude);
      var color = _isImaginary(ctx.mode) ? COLOR_IMAGINARY : COLOR_NEUTRAL;
      applyArrows(viewerObj, endpoints, symbols, { color: color });
    }
    if (savedView !== null && typeof viewerObj.setView === "function") {
      viewerObj.setView(savedView);
    }
    animationState.savedView = savedView;
    if (typeof viewerObj.render === "function") {
      try { viewerObj.render(); } catch (_) { /* render is best-effort */ }
    }
  }

  /**
   * The rAF tick: throttled to ~30fps (FRAME_MIN_MS), advances the phase
   * by dt * speed * 2π * CYCLE_RATE_HZ, and rebuilds the model with the
   * displacement at the new phase (H atoms frozen above the skip
   * threshold).  Stops itself via stopAnimation() when the geometry
   * context becomes invalid mid-play.
   *
   * @param {number} [ts] - rAF timestamp (ms); _nowImpl() when absent
   */
  function tick(ts) {
    if (!animationState.playing) return;
    var now = (ts != null) ? ts : _now();
    if (animationState.lastFrameTs != null &&
        now - animationState.lastFrameTs < FRAME_MIN_MS) {
      animationState.rafHandle = _scheduleNextFrame();
      return;
    }
    var dt = animationState.lastFrameTs == null ? 0 : (now - animationState.lastFrameTs) / 1000;
    if (dt < 0) dt = 0;
    animationState.lastFrameTs = now;
    animationState.phase += dt * animationState.speed * 2 * Math.PI * CYCLE_RATE_HZ;

    var ctx = _vibGeometry();
    var viewerObj = _getMainViewer();
    if (!ctx.ok || !viewerObj) {
      stopAnimation();
      return;
    }
    var mask = (ctx.symbols && ctx.coords.length > H_SKIP_ATOM_THRESHOLD)
      ? hydrogenSkipMask(ctx.symbols, ctx.coords.length)
      : null;
    var displaced = computeDisplacedCoords(
      ctx.coords, ctx.mode.vectors, arrowState.amplitude, animationState.phase,
      { invert: animationState.invertPhase, skipMask: mask }
    );
    _renderAnimationFrame(viewerObj, displaced, ctx.symbols, ctx);
    animationState.rafHandle = _scheduleNextFrame();
  }

  function _scheduleNextFrame() {
    var raf = _getRafImpl();
    if (!raf) {
      animationState.playing = false;
      return null;
    }
    try {
      return raf(tick);
    } catch (_) {
      animationState.playing = false;
      return null;
    }
  }

  /**
   * Start (or resume) the animation loop.  No-op when already playing or
   * when the geometry context is invalid (hint surfaced in the UI).  While
   * a session is active, geometry editing is locked (mutual exclusion).
   */
  function playAnimation() {
    if (animationState.playing) return;
    var ctx = _vibGeometry();
    if (!ctx.ok) {
      arrowState.enabled = false;
      arrowState._hint = ctx.hint;
      renderFrequencyInspector();
      return;
    }
    animationState.equilibriumXyz = _buildEquilibriumXyz();
    animationState.playing = true;
    animationState._active = true;
    _setEditorLocked(true);
    animationState.lastFrameTs = null;
    animationState.rafHandle = _scheduleNextFrame();
    renderFrequencyInspector();
  }

  /**
   * Pause the loop but keep the currently displayed displaced model.
   */
  function pauseAnimation() {
    if (!animationState.playing) return;
    if (animationState.rafHandle != null) {
      var cancel = _getCancelRafImpl();
      if (cancel) {
        try { cancel(animationState.rafHandle); } catch (_) { /* already gone */ }
      }
      animationState.rafHandle = null;
    }
    animationState.playing = false;
    renderFrequencyInspector();
  }

  /**
   * Full stop (todo 30 hardening of the todo-29 stop): cancel the rAF
   * loop, rebuild the EXACT equilibrium model (same XYZ the app loaded),
   * restore the saved camera, clear shapes, re-apply static arrows when
   * the display mode includes them, reset the phase, and release the
   * editor lock.  Idempotent — the second call performs no viewer calls.
   */
  function stopAnimationAndRestore() {
    if (animationState.rafHandle != null) {
      var cancel = _getCancelRafImpl();
      if (cancel) {
        try { cancel(animationState.rafHandle); } catch (_) { /* already gone */ }
      }
      animationState.rafHandle = null;
    }
    animationState.playing = false;
    animationState.lastFrameTs = null;
    animationState.phase = 0;
    _setEditorLocked(false);
    if (!animationState._active) {
      return;
    }
    animationState._active = false;

    var viewerObj = _getMainViewer();
    if (viewerObj) {
      var savedView = animationState.savedView !== null
        ? animationState.savedView
        : (typeof viewerObj.getView === "function" ? viewerObj.getView() : null);
      if (typeof viewerObj.removeAllModels === "function") viewerObj.removeAllModels();
      var eqXyz = animationState.equilibriumXyz || _buildEquilibriumXyz();
      if (eqXyz) {
        viewerObj.addModel(eqXyz, "xyz");
      }
      _styleModel(viewerObj);
      if (typeof viewerObj.removeAllShapes === "function") viewerObj.removeAllShapes();
      if (savedView !== null && typeof viewerObj.setView === "function") {
        viewerObj.setView(savedView);
      }
      if (typeof viewerObj.render === "function") {
        try { viewerObj.render(); } catch (_) { /* render is best-effort */ }
      }
    }
    animationState.savedView = null;
    if (arrowState.displayMode !== "animation") {
      refreshArrows();
    }
    renderFrequencyInspector();
  }

  /**
   * Back-compat alias for the todo-29 API surface.
   */
  function stopAnimation() {
    stopAnimationAndRestore();
  }

  /**
   * Mutual-exclusion probe: true while the animation loop is playing.
   *
   * @returns {boolean}
   */
  function isAnimationActive() {
    return animationState.playing;
  }

  /**
   * Teardown hook for tab switch / job switch / viewer destroy: stop and
   * restore, clear every arrow, reset the vibration state (a new job or
   * re-selection refetches anyway), and release the editor lock.  Must
   * never leave a running loop or stale arrows behind.
   */
  function handleTeardown() {
    stopAnimationAndRestore();
    var viewerObj = _getMainViewer();
    clearArrows(viewerObj);
    arrowState.enabled = false;
    arrowState.modeIndex = null;
    arrowState._hint = null;
    arrowState._lastCount = 0;
    vibrationState.data = null;
    vibrationState.selectedModeIndex = null;
    vibrationState.error = null;
    vibrationState.loading = false;
    vibrationState.jobId = null;
    vibrationState.entryId = null;
    _setEditorLocked(false);
  }

  /**
   * Set the geometry-editor lock (best-effort; the editor is a Wave 6
   * skeleton — the contract exists today).
   *
   * @param {boolean} value
   */
  function _setEditorLocked(value) {
    if (typeof window !== "undefined" && window.ACPStructureEditor &&
        typeof window.ACPStructureEditor.setLocked === "function") {
      try { window.ACPStructureEditor.setLocked(value); } catch (_) { /* editor absent */ }
    }
  }

  /**
   * Play/pause toggle for the controls button.
   */
  function togglePlay() {
    if (animationState.playing) {
      pauseAnimation();
    } else {
      playAnimation();
    }
  }

  /**
   * @param {*} v - clamped into [SPEED_MIN, SPEED_MAX]
   */
  function setSpeed(v) {
    animationState.speed = clampSpeed(v);
  }

  /**
   * Flip the displacement sign (phase inversion).
   */
  function toggleInvertPhase() {
    animationState.invertPhase = !animationState.invertPhase;
    renderFrequencyInspector();
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
        refreshArrows();
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
    refreshArrows();
    renderFrequencyInspector();
  }

  /**
   * Render the frequency inspector into a container.
   *
   * available=true  -> imaginary summary header (when imaginary modes
   *                    exist) + all mode rows negatives-first; each row:
   *                    mode index + frequency (2dp, cm⁻¹) + 虚频 chip for
   *                    imaginary modes + IR intensity (1dp, km·mol⁻¹) when
   *                    present.  Rows are click-selectable.
   * available=false -> ONLY the reason text; no mode rows are rendered.
   *
   * @param {HTMLElement} [container] - defaults to #structure-inspector-vibrations
   */
  function renderFrequencyInspector(container) {
    if (typeof document === "undefined") return;
    if (!container) {
      container = document.getElementById("sv-vibration-dock");
    }
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

    if (container.id === "sv-vibration-dock") {
      _renderDockContent(container, modes, data);
    } else {
      _renderLegacyInspector(container, modes, data);
    }
  }

  function _renderLegacyInspector(container, modes, data) {
    var sorted = sortModesNegativesFirst(modes);
    var imagCount = 0;
    for (var ci = 0; ci < modes.length; ci++) {
      if (_isImaginary(modes[ci])) imagCount++;
    }
    if (imagCount > 0) {
      var summary = document.createElement("div");
      summary.className = "sv-vib-summary";
      summary.textContent = _format(
        _t("structure.vib.imaginary_summary", STR.IMAGINARY_SUMMARY),
        { count: imagCount, total: modes.length }
      );
      container.appendChild(summary);
    }
    var judgment = tsJudgment(modes, data.threshold_cm1);
    var tsBlock = document.createElement("div");
    tsBlock.className = "sv-vib-ts-hint";
    var hintLine = document.createElement("div");
    hintLine.className = "sv-vib-ts-line" +
      (judgment.hint === "first_order" ? " sv-vib-ts-ok" : " sv-vib-ts-warn");
    hintLine.textContent = tsHintText(judgment.hint);
    tsBlock.appendChild(hintLine);
    var suffixLine = document.createElement("div");
    suffixLine.className = "sv-vib-ts-suffix";
    suffixLine.textContent = tsSuffixText();
    tsBlock.appendChild(suffixLine);
    var thrLine = document.createElement("div");
    thrLine.className = "sv-vib-ts-threshold";
    thrLine.textContent = thresholdText(data.threshold_cm1, data.threshold_source);
    tsBlock.appendChild(thrLine);
    container.appendChild(tsBlock);
    var list = document.createElement("div");
    list.className = "sv-vib-list";
    for (var ri = 0; ri < sorted.length; ri++) {
      list.appendChild(_renderModeRow(sorted[ri]));
    }
    container.appendChild(list);
    if (geometryMismatch(_catalogEntry(), data)) {
      var mmReason = document.createElement("div");
      mmReason.className = "sv-vib-hint";
      mmReason.textContent = _t("structure.vib.ts.mismatch_reason", STR.MISMATCH_REASON);
      container.appendChild(mmReason);
    } else {
      _appendArrowControls(container, modes);
    }
  }

  function _categorizeModes(modes) {
    var significant = [];
    var otherNeg = [];
    var positives = [];
    var zeros = [];
    var threshold = -50.0;
    for (var i = 0; i < modes.length; i++) {
      var m = modes[i];
      var f = m.frequency_cm1;
      if (typeof f !== "number" || !isFinite(f)) { zeros.push(m); continue; }
      if (Math.abs(f) < 0.5) { zeros.push(m); continue; }
      if (f < 0) {
        if (f <= threshold) { significant.push(m); }
        else { otherNeg.push(m); }
      } else {
        positives.push(m);
      }
    }
    significant.sort(function (a, b) { return a.frequency_cm1 - b.frequency_cm1; });
    otherNeg.sort(function (a, b) { return a.frequency_cm1 - b.frequency_cm1; });
    positives.sort(function (a, b) { return a.frequency_cm1 - b.frequency_cm1; });
    return {
      imaginary: significant.concat(otherNeg),
      valid: positives,
      all: modes,
      zeros: zeros,
      significantCount: significant.length,
      validCount: positives.length,
      totalCount: modes.length,
    };
  }

  function _renderDockContent(dock, modes, data) {
    dock.textContent = "";
    var cats = _categorizeModes(modes);
    var selMode = _findMode(modes, vibrationState.selectedModeIndex);

    var header = document.createElement("div");
    header.className = "sv-vib-dock-header";
    var grip = document.createElement("div");
    grip.className = "sv-vib-dock-grip";
    grip.setAttribute("aria-label", "drag to resize");
    header.appendChild(grip);
    var title = document.createElement("span");
    title.className = "sv-vib-dock-title";
    title.textContent = _t("structure.vib.dock_title", STR.DOCK_TITLE);
    if (selMode) {
      var modeSpan = document.createElement("span");
      modeSpan.className = "sv-vib-dock-title-mode";
      var isNeg = _isImaginary(selMode);
      modeSpan.textContent = "#" + selMode.mode_index + "  " +
        selMode.frequency_cm1.toFixed(2) + " " + STR.FREQ_UNIT +
        " \u00b7 " + (isNeg
          ? _t("structure.vib.imaginary_label", STR.IMAGINARY_LABEL)
          : _t("structure.vib.positive_label", STR.POSITIVE_LABEL));
      title.appendChild(modeSpan);
    }
    header.appendChild(title);
    var closeBtn = document.createElement("button");
    closeBtn.className = "sv-vib-dock-close";
    closeBtn.setAttribute("aria-label", "close");
    closeBtn.textContent = "\u00d7";
    closeBtn.addEventListener("click", function () {
      if (typeof window !== "undefined" && window.ACPStructureViewer &&
          typeof window.ACPStructureViewer.closeVibrationDock === "function") {
        window.ACPStructureViewer.closeVibrationDock();
      }
    });
    header.appendChild(closeBtn);
    dock.appendChild(header);

    var body = document.createElement("div");
    body.className = "sv-vib-dock-body";

    var tsCol = document.createElement("div");
    tsCol.className = "sv-vib-dock-col-ts";
    _renderTsColumn(tsCol, modes, data);
    body.appendChild(tsCol);

    var modesCol = document.createElement("div");
    modesCol.className = "sv-vib-dock-col-modes";
    _renderModesColumn(modesCol, modes, cats);
    body.appendChild(modesCol);

    var ctrlCol = document.createElement("div");
    ctrlCol.className = "sv-vib-dock-col-controls";
    if (geometryMismatch(_catalogEntry(), data)) {
      var mmReason = document.createElement("div");
      mmReason.className = "sv-vib-hint";
      mmReason.textContent = _t("structure.vib.ts.mismatch_reason", STR.MISMATCH_REASON);
      ctrlCol.appendChild(mmReason);
    } else {
      _renderControlsColumn(ctrlCol, modes);
    }
    body.appendChild(ctrlCol);

    dock.appendChild(body);

    if (animationState._active) {
      var lockedHint = document.createElement("div");
      lockedHint.className = "sv-vib-dock-locked-hint";
      lockedHint.textContent = _t("structure.vib.locked_hint", STR.LOCKED_HINT);
      dock.appendChild(lockedHint);
    }
  }

  function _renderTsColumn(col, modes, data) {
    var label = document.createElement("div");
    label.className = "sv-vib-dock-section-label";
    label.textContent = "TS \u8bc1\u636e";
    col.appendChild(label);

    var imagCount = 0;
    for (var i = 0; i < modes.length; i++) {
      if (_isImaginary(modes[i])) imagCount++;
    }
    var verdict = document.createElement("div");
    verdict.className = "sv-vib-dock-ts-verdict";
    var judgment = tsJudgment(modes, data.threshold_cm1);
    verdict.className += judgment.hint === "first_order" ? " sv-vib-ts-ok" : " sv-vib-ts-warn";
    verdict.textContent = _format(
      _t("structure.vib.imaginary_summary", STR.IMAGINARY_SUMMARY),
      { count: imagCount }
    );
    col.appendChild(verdict);

    var hintLine = document.createElement("div");
    hintLine.className = "sv-vib-dock-ts-suffix";
    hintLine.textContent = tsHintText(judgment.hint);
    col.appendChild(hintLine);

    var thrLine = document.createElement("div");
    thrLine.className = "sv-vib-dock-ts-threshold";
    thrLine.textContent = thresholdText(data.threshold_cm1, data.threshold_source);
    col.appendChild(thrLine);

    var suffixLine = document.createElement("div");
    suffixLine.className = "sv-vib-dock-ts-suffix";
    suffixLine.textContent = tsSuffixText();
    col.appendChild(suffixLine);
  }

  function _renderModesColumn(col, modes, cats) {
    if (!vibrationState._filterTab) {
      vibrationState._filterTab = "imaginary";
      if (cats.significantCount === 0) {
        vibrationState._filterTab = cats.validCount > 0 ? "valid" : "all";
      }
    }

    var tabs = document.createElement("div");
    tabs.className = "sv-vib-filter-tabs";
    var tabDefs = [
      { key: "imaginary", label: _format(_t("structure.vib.filter_imaginary", STR.FILTER_IMAGINARY), { count: cats.imaginary.length }) },
      { key: "valid", label: _format(_t("structure.vib.filter_valid", STR.FILTER_VALID), { count: cats.validCount }) },
      { key: "all", label: _format(_t("structure.vib.filter_all", STR.FILTER_ALL), { count: cats.totalCount }) },
    ];
    for (var ti = 0; ti < tabDefs.length; ti++) {
      var tab = document.createElement("button");
      tab.className = "sv-vib-filter-tab" +
        (vibrationState._filterTab === tabDefs[ti].key ? " sv-active" : "");
      tab.setAttribute("type", "button");
      tab.textContent = tabDefs[ti].label;
      tab.setAttribute("data-filter", tabDefs[ti].key);
      tab.addEventListener("click", function (key) {
        return function () {
          vibrationState._filterTab = key;
          renderFrequencyInspector();
        };
      }(tabDefs[ti].key));
      tabs.appendChild(tab);
    }
    col.appendChild(tabs);

    var track = document.createElement("div");
    track.className = "sv-vib-mode-track";
    track.setAttribute("tabindex", "0");

    var filtered;
    if (vibrationState._filterTab === "imaginary") {
      filtered = cats.imaginary;
    } else if (vibrationState._filterTab === "valid") {
      filtered = cats.valid;
    } else {
      filtered = cats.all;
    }

    var zeroShown = false;
    for (var ri = 0; ri < filtered.length; ri++) {
      var m = filtered[ri];
      if (Math.abs(m.frequency_cm1) < 0.5) {
        if (!zeroShown && vibrationState._filterTab !== "all") {
          zeroShown = true;
          var collapseBtn = document.createElement("button");
          collapseBtn.className = "sv-vib-zero-collapse";
          collapseBtn.setAttribute("type", "button");
          collapseBtn.textContent = _format(
            _t("structure.vib.zero_modes", STR.ZERO_MODES),
            { count: cats.zeros.length }
          );
          collapseBtn.addEventListener("click", function () {
            vibrationState._filterTab = "all";
            renderFrequencyInspector();
          });
          track.appendChild(collapseBtn);
        }
        if (vibrationState._filterTab !== "all") continue;
      }
      track.appendChild(_renderModeItem(m));
    }

    track.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown") {
        ev.preventDefault();
        _navigateMode(filtered, 1);
      } else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") {
        ev.preventDefault();
        _navigateMode(filtered, -1);
      }
    });
    col.appendChild(track);

    var currentInfo = document.createElement("div");
    currentInfo.className = "sv-vib-current-info";
    var selMode = _findMode(modes, vibrationState.selectedModeIndex);
    if (selMode) {
      var isNeg = _isImaginary(selMode);
      currentInfo.innerHTML = "#" + _esc(selMode.mode_index) + "  " +
        '<span class="' + (isNeg ? "sv-vib-freq-imag" : "") + '">' +
        selMode.frequency_cm1.toFixed(2) + " " + STR.FREQ_UNIT + "</span>";
      if (selMode.ir_intensity != null && typeof selMode.ir_intensity === "number") {
        currentInfo.innerHTML += " \u00b7 IR " + selMode.ir_intensity.toFixed(1) + " " + STR.IR_UNIT;
      }
    }
    col.appendChild(currentInfo);
  }

  function _renderModeItem(mode) {
    var imag = _isImaginary(mode);
    var item = document.createElement("div");
    item.className = "sv-vib-mode-item" +
      (mode.mode_index === vibrationState.selectedModeIndex ? " sv-active" : "") +
      (imag ? " sv-vib-mode-item-imag" : "");
    item.setAttribute("data-mode-index", _esc(mode.mode_index));
    item.setAttribute("title", "#" + mode.mode_index + "  " +
      mode.frequency_cm1.toFixed(2) + " " + STR.FREQ_UNIT +
      (mode.ir_intensity != null ? " \u00b7 IR " + mode.ir_intensity.toFixed(1) + " " + STR.IR_UNIT : ""));

    var idx = document.createElement("span");
    idx.className = "sv-vib-mode-item-idx";
    idx.textContent = "#" + _esc(mode.mode_index);
    item.appendChild(idx);

    var freq = document.createElement("span");
    freq.className = "sv-vib-mode-item-freq" + (imag ? " sv-imag" : "");
    freq.textContent = mode.frequency_cm1.toFixed(2);
    item.appendChild(freq);

    if (mode.ir_intensity != null && typeof mode.ir_intensity === "number") {
      var ir = document.createElement("span");
      ir.className = "sv-vib-mode-item-ir";
      ir.textContent = mode.ir_intensity.toFixed(1);
      item.appendChild(ir);
    }

    item.addEventListener("click", function () {
      selectMode(mode.mode_index);
    });
    return item;
  }

  function _navigateMode(filtered, delta) {
    if (!filtered || !filtered.length) return;
    var curIdx = -1;
    for (var i = 0; i < filtered.length; i++) {
      if (filtered[i].mode_index === vibrationState.selectedModeIndex) { curIdx = i; break; }
    }
    var nextIdx = curIdx + delta;
    if (nextIdx < 0) nextIdx = filtered.length - 1;
    if (nextIdx >= filtered.length) nextIdx = 0;
    selectMode(filtered[nextIdx].mode_index);
  }

  function _renderControlsColumn(col, modes) {
    var dispLabel = document.createElement("div");
    dispLabel.className = "sv-vib-dock-section-label";
    dispLabel.textContent = "\u663e\u793a";
    col.appendChild(dispLabel);

    var grid = document.createElement("div");
    grid.className = "sv-vib-ctrl-grid";

    var lbl1 = document.createElement("span");
    lbl1.className = "sv-vib-ctrl-label";
    lbl1.textContent = _t("structure.vib.arrow.amplitude", STR.AMPLITUDE);
    grid.appendChild(lbl1);
    var ampField = document.createElement("div");
    ampField.className = "sv-vib-ctrl-field";
    var slider = document.createElement("input");
    slider.className = "sv-vib-amp-slider";
    slider.setAttribute("type", "range");
    slider.setAttribute("min", String(AMP_MIN));
    slider.setAttribute("max", String(AMP_MAX));
    slider.setAttribute("step", "0.01");
    slider.setAttribute("value", String(arrowState.amplitude));
    ampField.appendChild(slider);
    var ampVal = document.createElement("span");
    ampVal.className = "sv-vib-amp-value";
    ampVal.textContent = arrowState.amplitude.toFixed(2) + " " + STR.UNIT_ANGSTROM;
    ampField.appendChild(ampVal);
    slider.addEventListener("input", function () {
      setAmplitude(parseFloat(slider.value));
      ampVal.textContent = arrowState.amplitude.toFixed(2) + " " + STR.UNIT_ANGSTROM;
    });
    grid.appendChild(ampField);

    var lbl2 = document.createElement("span");
    lbl2.className = "sv-vib-ctrl-label";
    lbl2.textContent = "\u663e\u793a";
    grid.appendChild(lbl2);
    var toggleField = document.createElement("div");
    toggleField.className = "sv-vib-ctrl-field";
    var group = document.createElement("div");
    group.className = "sv-vib-toggle-group";
    var toggles = [
      { mode: "arrows", key: "structure.vib.arrow.display_arrows", fb: STR.DISPLAY_ARROWS },
      { mode: "animation", key: "structure.vib.arrow.display_animation", fb: STR.DISPLAY_ANIMATION },
      { mode: "combo", key: "structure.vib.arrow.display_combo", fb: STR.DISPLAY_COMBO },
    ];
    for (var ti = 0; ti < toggles.length; ti++) {
      var tg = toggles[ti];
      var btn = document.createElement("button");
      btn.setAttribute("type", "button");
      btn.className = "sv-vib-toggle" +
        (arrowState.displayMode === tg.mode ? " sv-active" : "");
      btn.setAttribute("data-display-mode", tg.mode);
      btn.textContent = _t(tg.key, tg.fb);
      btn.addEventListener("click", function (choice) {
        return function () { setDisplayMode(choice); };
      }(tg.mode));
      group.appendChild(btn);
    }
    toggleField.appendChild(group);
    grid.appendChild(toggleField);

    var lbl3 = document.createElement("span");
    lbl3.className = "sv-vib-ctrl-label";
    lbl3.textContent = _t("structure.vib.anim.play", STR.PLAY);
    grid.appendChild(lbl3);
    var playField = document.createElement("div");
    playField.className = "sv-vib-ctrl-field";
    var playBtn = document.createElement("button");
    playBtn.setAttribute("type", "button");
    playBtn.className = "sv-vib-play-btn";
    playBtn.textContent = animationState.playing
      ? _t("structure.vib.anim.pause", STR.PAUSE)
      : _t("structure.vib.anim.play", STR.PLAY);
    playBtn.addEventListener("click", function () { togglePlay(); });
    playField.appendChild(playBtn);
    grid.appendChild(playField);

    var lbl4 = document.createElement("span");
    lbl4.className = "sv-vib-ctrl-label";
    lbl4.textContent = _t("structure.vib.anim.speed", STR.SPEED);
    grid.appendChild(lbl4);
    var speedField = document.createElement("div");
    speedField.className = "sv-vib-ctrl-field";
    var speedSelect = document.createElement("select");
    speedSelect.className = "sv-vib-speed-select";
    for (var spi = 0; spi < SPEED_OPTIONS.length; spi++) {
      var sopt = document.createElement("option");
      sopt.value = String(SPEED_OPTIONS[spi]);
      sopt.textContent = SPEED_OPTIONS[spi] + "x";
      if (SPEED_OPTIONS[spi] === animationState.speed) sopt.selected = true;
      speedSelect.appendChild(sopt);
    }
    speedSelect.addEventListener("change", function () {
      setSpeed(parseFloat(speedSelect.value));
    });
    speedField.appendChild(speedSelect);
    var invertBtn = document.createElement("button");
    invertBtn.setAttribute("type", "button");
    invertBtn.className = "sv-vib-toggle" + (animationState.invertPhase ? " sv-active" : "");
    invertBtn.setAttribute("data-invert-phase", "1");
    invertBtn.textContent = _t("structure.vib.anim.invert", STR.INVERT);
    invertBtn.addEventListener("click", function () { toggleInvertPhase(); });
    speedField.appendChild(invertBtn);
    grid.appendChild(speedField);

    col.appendChild(grid);

    if (arrowState._hint) {
      var hintBox = document.createElement("div");
      hintBox.className = "sv-vib-hint";
      hintBox.textContent = arrowState._hint;
      col.appendChild(hintBox);
    }
  }

  /**
   * Append the arrow controls: mode selector (synced with the list
   * selection), amplitude slider, and the three-state 箭头/动画/箭头+动画
   * toggle group (animation playback itself arrives in todo 29).
   *
   * @param {HTMLElement} container
   * @param {Array<Object>} modes
   */
  function _appendArrowControls(container, modes) {
    var controls = document.createElement("div");
    controls.className = "sv-vib-controls";

    /* mode selector */
    var modeRow = document.createElement("div");
    modeRow.className = "sv-vib-control-row";
    var modeLbl = document.createElement("span");
    modeLbl.className = "sv-vib-control-label";
    modeLbl.textContent = _t("structure.vib.arrow.mode", STR.MODE);
    modeRow.appendChild(modeLbl);

    var select = document.createElement("select");
    select.className = "sv-vib-mode-select";
    var sorted = sortModesNegativesFirst(modes);
    for (var mi = 0; mi < sorted.length; mi++) {
      var m = sorted[mi];
      var opt = document.createElement("option");
      opt.value = String(m.mode_index);
      opt.textContent = "#" + m.mode_index + "  " + m.frequency_cm1.toFixed(2) + " " + STR.FREQ_UNIT +
        (_isImaginary(m) ? " " + _t("structure.vib.imaginary_chip", STR.IMAGINARY) : "");
      if (m.mode_index === vibrationState.selectedModeIndex) {
        opt.selected = true;
      }
      select.appendChild(opt);
    }
    select.addEventListener("change", function () {
      var idx = parseInt(select.value, 10);
      if (!isNaN(idx)) selectMode(idx);
    });
    modeRow.appendChild(select);
    controls.appendChild(modeRow);

    /* amplitude slider */
    var ampRow = document.createElement("div");
    ampRow.className = "sv-vib-control-row";
    var ampLbl = document.createElement("span");
    ampLbl.className = "sv-vib-control-label";
    ampLbl.textContent = _t("structure.vib.arrow.amplitude", STR.AMPLITUDE);
    ampRow.appendChild(ampLbl);

    var slider = document.createElement("input");
    slider.className = "sv-vib-amp-slider";
    slider.setAttribute("type", "range");
    slider.setAttribute("min", String(AMP_MIN));
    slider.setAttribute("max", String(AMP_MAX));
    slider.setAttribute("step", "0.01");
    slider.setAttribute("value", String(arrowState.amplitude));
    ampRow.appendChild(slider);

    var ampVal = document.createElement("span");
    ampVal.className = "sv-vib-amp-value";
    ampVal.textContent = arrowState.amplitude.toFixed(2) + " " + STR.UNIT_ANGSTROM;
    ampRow.appendChild(ampVal);
    controls.appendChild(ampRow);

    slider.addEventListener("input", function () {
      setAmplitude(parseFloat(slider.value));
      /* no full re-render here — the slider must survive the drag */
      ampVal.textContent = arrowState.amplitude.toFixed(2) + " " + STR.UNIT_ANGSTROM;
    });

    /* three-state toggle group */
    var toggleRow = document.createElement("div");
    toggleRow.className = "sv-vib-control-row";
    var group = document.createElement("div");
    group.className = "sv-vib-toggle-group";
    var toggles = [
      { mode: "arrows", key: "structure.vib.arrow.display_arrows", fb: STR.DISPLAY_ARROWS },
      { mode: "animation", key: "structure.vib.arrow.display_animation", fb: STR.DISPLAY_ANIMATION },
      { mode: "combo", key: "structure.vib.arrow.display_combo", fb: STR.DISPLAY_COMBO },
    ];
    for (var ti = 0; ti < toggles.length; ti++) {
      var tg = toggles[ti];
      var btn = document.createElement("button");
      btn.setAttribute("type", "button");
      btn.className = "sv-vib-toggle" +
        (arrowState.displayMode === tg.mode ? " sv-active" : "");
      btn.setAttribute("data-display-mode", tg.mode);
      btn.textContent = _t(tg.key, tg.fb);
      btn.addEventListener("click", function (choice) {
        return function () { setDisplayMode(choice); };
      }(tg.mode));
      group.appendChild(btn);
    }
    toggleRow.appendChild(group);
    controls.appendChild(toggleRow);

    /* playback controls: play/pause + speed + phase inversion */
    var playRow = document.createElement("div");
    playRow.className = "sv-vib-control-row";

    var playBtn = document.createElement("button");
    playBtn.setAttribute("type", "button");
    playBtn.className = "sv-vib-play-btn";
    playBtn.textContent = animationState.playing
      ? _t("structure.vib.anim.pause", STR.PAUSE)
      : _t("structure.vib.anim.play", STR.PLAY);
    playBtn.addEventListener("click", function () {
      togglePlay();
    });
    playRow.appendChild(playBtn);

    var speedLbl = document.createElement("span");
    speedLbl.className = "sv-vib-control-label";
    speedLbl.textContent = _t("structure.vib.anim.speed", STR.SPEED);
    playRow.appendChild(speedLbl);

    var speedSelect = document.createElement("select");
    speedSelect.className = "sv-vib-speed-select";
    for (var spi = 0; spi < SPEED_OPTIONS.length; spi++) {
      var sopt = document.createElement("option");
      sopt.value = String(SPEED_OPTIONS[spi]);
      sopt.textContent = SPEED_OPTIONS[spi] + "x";
      if (SPEED_OPTIONS[spi] === animationState.speed) {
        sopt.selected = true;
      }
      speedSelect.appendChild(sopt);
    }
    speedSelect.addEventListener("change", function () {
      setSpeed(parseFloat(speedSelect.value));
    });
    playRow.appendChild(speedSelect);

    var invertBtn = document.createElement("button");
    invertBtn.setAttribute("type", "button");
    invertBtn.className = "sv-vib-toggle" +
      (animationState.invertPhase ? " sv-active" : "");
    invertBtn.setAttribute("data-invert-phase", "1");
    invertBtn.textContent = _t("structure.vib.anim.invert", STR.INVERT);
    invertBtn.addEventListener("click", function () {
      toggleInvertPhase();
    });
    playRow.appendChild(invertBtn);
    controls.appendChild(playRow);

    /* arrow-availability hint */
    if (arrowState._hint) {
      var hintBox = document.createElement("div");
      hintBox.className = "sv-vib-hint";
      hintBox.textContent = arrowState._hint;
      controls.appendChild(hintBox);
    }

    /* editing paused while an animation session is active (todo 30) */
    if (animationState._active) {
      var lockedHint = document.createElement("div");
      lockedHint.className = "sv-vib-hint sv-vib-locked-hint";
      lockedHint.textContent = _t("structure.vib.locked_hint", STR.LOCKED_HINT);
      controls.appendChild(lockedHint);
    }

    container.appendChild(controls);
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
    arrowState: arrowState,
    animationState: animationState,
    H_SKIP_ATOM_THRESHOLD: H_SKIP_ATOM_THRESHOLD,
    loadVibrations: loadVibrations,
    renderFrequencyInspector: renderFrequencyInspector,
    selectMode: selectMode,
    refreshArrows: refreshArrows,
    setDisplayMode: setDisplayMode,
    setAmplitude: setAmplitude,
    playAnimation: playAnimation,
    pauseAnimation: pauseAnimation,
    togglePlay: togglePlay,
    stopAnimation: stopAnimation,
    stopAnimationAndRestore: stopAnimationAndRestore,
    isAnimationActive: isAnimationActive,
    handleTeardown: handleTeardown,
    setSpeed: setSpeed,
    toggleInvertPhase: toggleInvertPhase,
    sortModesNegativesFirst: sortModesNegativesFirst,
    defaultModeIndex: defaultModeIndex,
    reasonText: reasonText,
    computeArrowEndpoints: computeArrowEndpoints,
    clampAmplitude: clampAmplitude,
    hydrogenSkipMask: hydrogenSkipMask,
    applyArrows: applyArrows,
    clearArrows: clearArrows,
    computeDisplacedCoords: computeDisplacedCoords,
    buildDisplacedXyz: buildDisplacedXyz,
    clampSpeed: clampSpeed,
    tsJudgment: tsJudgment,
    tsHintText: tsHintText,
    tsSuffixText: tsSuffixText,
    thresholdText: thresholdText,
    geometryMismatch: geometryMismatch,
    _categorizeModes: _categorizeModes,
    _catalogEntry: _catalogEntry,
    _t: _t,
    _esc: _esc,
    _vibrationsUrl: _vibrationsUrl,
    _getMainViewer: _getMainViewer,
    _arrowsVisible: _arrowsVisible,
    _viewerImpl: null,
    _rafImpl: null,
    _cancelRafImpl: null,
    _nowImpl: null,
    _fetchImpl: (typeof window !== "undefined" && window.fetch) ? window.fetch.bind(window) : null,
  };
})();
