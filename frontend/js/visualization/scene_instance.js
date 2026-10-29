/*
 * ACP Visualization — scene instance: bounded per-canvas viewer wrapper (v2).
 *
 * Owner: frontend/js/visualization/** (OpenCode). Version: v20261018.2
 *
 * Plan §42.3 R5 + T7 (bounded instance API v2, module-only slice — the file is
 * verified as a module and is NOT wired into the page here; T8 wires it). One
 * instance owns exactly one viewer, its animation interval, its model / shape /
 * label token registry, and its teardown (clearScene + destroyImpl, each run
 * exactly once).
 *
 * Purity: no DOM access, no network, no event streams, and no direct reference
 * to the underlying viewer library — only the guarded ACPVisualization.mol3d
 * namespace is consulted (create + frame delegation + default destroy), and
 * even that is optional: the instance works against any viewer-shaped object,
 * including plain recording stubs (module tests).
 *
 * Bounded API contract (T7):
 *   - create({ container, options }) builds and HOLDS the viewer internally via
 *     ACPVisualization.mol3d.create. An explicit `opts.viewer` injection seam
 *     is kept ONLY for module tests (annotated below) and is never used by the
 *     page.
 *   - addModel(data, spec) returns an instance-internal opaque model token — the
 *     native 3Dmol model is never exposed. Tokens provide setStyle / addStyle /
 *     setClickable / setHoverable / selectedAtoms.
 *   - Instance-level setStyle / addStyle / setClickable / setHoverable /
 *     selectedAtoms act on the current (last-added) model — equivalent for the
 *     single-model call sites. Overlay call sites must use getModel(index)
 *     tokens: each addModel token maps independently, so setting B's style never
 *     touches A.
 *   - getModelCount() / getModel(index) read from OUR registry. Vendor
 *     3Dmol-2.5.5 has NO native getModelCount; it is never forwarded.
 *   - Shapes and labels are opaque instance tokens too (addSphere / addCylinder /
 *     addArrow -> shape token; addLabel -> label token). Tokens from another
 *     instance are rejected, and tokens die immediately after removeModel /
 *     removeShape / removeLabel / removeAll* / clearScene / dispose.
 *   - Mutators return a boolean or a restricted token — native chained returns
 *     (native `this`) are never passed through.
 *   - selectedAtoms returns an independent snapshot with a field whitelist.
 *     Click/hover callbacks receive a data atom snapshot, the restricted
 *     instance (or undefined) and a whitelisted event — never a native object.
 *     Callbacks from a previous scene/model generation are dropped immediately.
 *   - Error contract: restricted methods return explicit failure values
 *     (null/false) on unavailable / disposed / native failure; lastError()
 *     reports the latest failure. Only a later SUCCESS of the SAME method
 *     clears its record; render/resize successes never clear parse-class
 *     failures. clearError() resets explicitly. Never throws, never fakes
 *     success (this replaces the old silent-swallow forward()).
 *
 * Lifecycle contract (§42.4 实例生命周期): repeated mount, hide/show, task
 * switch and dispose are all safe — play() restarts cleanly (one interval per
 * instance, no double timers), setHidden() pauses ticking without losing the
 * playing state, and dispose() is idempotent with a late-tick guard so that
 * no onTick or native viewer call fires afterwards (销毁无旧回调).
 */
window.ACPVisualization = window.ACPVisualization || {};
ACPVisualization.scene = (function () {
  "use strict";

  /** Resolve the mol3d namespace lazily and tolerantly (may be absent). */
  function mol3d() {
    var ns = window.ACPVisualization;
    return ns && ns.mol3d ? ns.mol3d : null;
  }

  function defaultDestroyImpl() {
    var m = mol3d();
    // mol3d.destroy is no-op-safe for stub viewers (WeakSet + guarded clear),
    // so handing it a non-native viewer is tolerated by design.
    return m && typeof m.destroy === "function" ? m.destroy : null;
  }

  /** Method owner for the error record. */
  var ATOM_FIELDS = ["serial", "index", "elem", "x", "y", "z"];
  // Event whitelist only: never a DOM target or any method-bearing object.
  var EVENT_FIELDS = [
    "type", "button", "buttons", "detail", "which",
    "x", "y", "pageX", "pageY", "clientX", "clientY", "offsetX", "offsetY",
    "shiftKey", "altKey", "ctrlKey", "metaKey",
  ];

  /** Plain-data atom snapshot: whitelist fields only (no native methods). */
  function snapshotAtom(atom) {
    if (atom == null) return null;
    if (typeof atom !== "object") return atom;
    var out = {};
    for (var i = 0; i < ATOM_FIELDS.length; i++) {
      var k = ATOM_FIELDS[i];
      var v = atom[k];
      if (v === undefined) continue;
      out[k] = typeof v === "number" ? v : (typeof v === "string" && v !== "" && isFinite(Number(v)) ? Number(v) : v);
    }
    if (out.elem === undefined && typeof atom.atom === "string") out.elem = atom.atom;
    return out;
  }

  /** Plain-data event snapshot: whitelist fields only. */
  function snapshotEvent(event) {
    if (event == null) return undefined;
    if (typeof event !== "object") return event;
    var out = {};
    for (var i = 0; i < EVENT_FIELDS.length; i++) {
      var k = EVENT_FIELDS[i];
      if (event[k] !== undefined) out[k] = event[k];
    }
    return out;
  }

  function nativeMethod(obj, name) {
    if (!obj) return null;
    var fn = obj[name];
    return typeof fn === "function" ? fn : null;
  }

  function create(opts) {
    opts = opts || {};

    // TEST-ONLY injection seam (module tests only — the page never passes a
    // viewer; production always goes through ACPVisualization.mol3d.create).
    var injectedViewer = opts.viewer || null;
    var viewer = injectedViewer;
    if (!viewer) {
      if (!opts.container) return null; // no canvas → no viewer
      var lib = mol3d();
      if (!lib || typeof lib.create !== "function") return null; // library missing
      viewer = lib.create(opts.container, opts.options || {});
      if (!viewer) return null; // create degraded to null (library unavailable)
    }

    var destroyImpl = typeof opts.destroyImpl === "function"
      ? opts.destroyImpl
      : defaultDestroyImpl();
    var onDispose = typeof opts.onDispose === "function" ? opts.onDispose : null;

    var state = {
      viewer: viewer, // internal only — never exposed on the instance
      container: opts.container || null, // size probe target for resize()
      resizeW: null, // last container size actually applied by a native resize
      resizeH: null,
      disposed: false,
      playing: false,
      paused: false,
      hidden: false,
      timerId: null,
      playSeq: 0, // late-tick guard: bumped on stop/restart/dispose
      models: [], // ordered records: { native, token, alive, cbGen }
      shapes: [], // ordered records: { token, native }
      labels: [], // ordered records: { token, native }
      sceneGen: 0, // bumped on clearScene / removeAllModels / dispose
      lastError: null, // { method, message }
    };

    /** Public restricted instance — assigned before any callback can fire. */
    var instanceApi = null;

    function fail(method, message) {
      state.lastError = { method: method, message: message || "failed" };
      return false;
    }

    function failNull(method, message) {
      state.lastError = { method: method, message: message || "failed" };
      return null;
    }

    /** Success clears only a prior record of the SAME method. */
    function ok(method) {
      if (state.lastError && state.lastError.method === method) state.lastError = null;
      return true;
    }

    function okVal(method, value) {
      if (state.lastError && state.lastError.method === method) state.lastError = null;
      return value;
    }

    function currentRecord() {
      return state.models.length ? state.models[state.models.length - 1] : null;
    }

    function tokenIndex(list, handle) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].token === handle) return i;
      }
      return -1;
    }

    function stopTimer() {
      state.playSeq += 1;
      if (state.timerId !== null) {
        clearInterval(state.timerId);
        state.timerId = null;
      }
    }

    function clearSceneNative(v) {
      var methods = ["removeAllModels", "removeAllShapes", "removeAllLabels"];
      for (var i = 0; i < methods.length; i++) {
        var fn = nativeMethod(v, methods[i]);
        if (fn) {
          try { fn.call(v); } catch (e) { /* scene already released */ }
        }
      }
    }

    function invalidateAllModels() {
      for (var i = 0; i < state.models.length; i++) {
        state.models[i].alive = false;
        state.models[i].cbGen.v += 1;
      }
      state.models.length = 0;
    }

    // --- generic restricted model operations -------------------------------
    function styleOn(record, method, args) {
      if (state.disposed) return fail(method, "disposed");
      if (!record || !record.alive) return fail(method, "no live model");
      var fn = nativeMethod(record.native, method);
      if (!fn) return fail(method, method + " unavailable");
      try {
        fn.apply(record.native, args); // discard native chained return
      } catch (e) {
        return fail(method, e && e.message);
      }
      return ok(method);
    }

    /** Additive style. Vendor 3Dmol-2.5.5 GLModel has NO addStyle (only the
     * viewer does); it exposes the additive flag as setStyle(sel, style, true).
     * We therefore emulate model-level addStyle through setStyle(..., true) so
     * overlay tokens keep per-model scoping instead of touching every model. */
    function addStyleOn(record, args) {
      var method = "addStyle";
      if (state.disposed) return fail(method, "disposed");
      if (!record || !record.alive) return fail(method, "no live model");
      var sel, style;
      if (args.length >= 2) { sel = args[0]; style = args[1]; }
      else { sel = {}; style = args[0]; }
      var nativeAdd = nativeMethod(record.native, "addStyle");
      var nativeSet = nativeMethod(record.native, "setStyle");
      if (!nativeAdd && !nativeSet) return fail(method, "addStyle unavailable");
      try {
        if (nativeAdd) nativeAdd.call(record.native, sel, style);
        else nativeSet.call(record.native, sel, style, true);
      } catch (e) {
        return fail(method, e && e.message);
      }
      return ok(method);
    }

    function selectedAtomsOn(record, sel) {
      var method = "selectedAtoms";
      if (state.disposed) return failNull(method, "disposed");
      if (!record || !record.alive) return failNull(method, "no live model");
      var fn = nativeMethod(record.native, method);
      if (!fn) return failNull(method, "selectedAtoms unavailable");
      var atoms;
      try {
        atoms = fn.call(record.native, sel);
      } catch (e) {
        return failNull(method, e && e.message);
      }
      if (!atoms || typeof atoms.length !== "number") return failNull(method, "selectedAtoms returned a non-array");
      var out = [];
      for (var i = 0; i < atoms.length; i++) out.push(snapshotAtom(atoms[i]));
      return okVal(method, out);
    }

    /** Wrap a business callback so it only ever sees plain data + the
     * restricted instance, and dies with its scene/model generation. */
    function wrapCallback(record, cb) {
      var scene = state.sceneGen;
      var recGen = record ? record.cbGen.v : 0;
      return function (atom, nativeViewer, event) {
        if (state.disposed) return;
        if (state.sceneGen !== scene) return; // scene/task generation changed
        if (record && (!record.alive || record.cbGen.v !== recGen)) return; // model gone
        if (typeof cb !== "function") return;
        try {
          cb(snapshotAtom(atom), instanceApi, snapshotEvent(event));
        } catch (e) { /* keep the native event loop alive */ }
      };
    }

    function bindClickable(record, sel, val, cb) {
      var method = "setClickable";
      if (state.disposed) return fail(method, "disposed");
      if (!record || !record.alive) return fail(method, "no live model");
      var fn = nativeMethod(record.native, method);
      if (!fn) return fail(method, "setClickable unavailable");
      try {
        fn.call(record.native, sel, val, wrapCallback(record, cb));
      } catch (e) {
        return fail(method, e && e.message);
      }
      return ok(method);
    }

    function bindHoverable(record, sel, val, cbIn, cbOut) {
      var method = "setHoverable";
      if (state.disposed) return fail(method, "disposed");
      if (!record || !record.alive) return fail(method, "no live model");
      var fn = nativeMethod(record.native, method);
      if (!fn) return fail(method, "setHoverable unavailable");
      try {
        fn.call(record.native, sel, val, wrapCallback(record, cbIn), wrapCallback(record, cbOut));
      } catch (e) {
        return fail(method, e && e.message);
      }
      return ok(method);
    }

    function makeModelToken(record) {
      var token = {
        setStyle: function (sel, style) { return styleOn(record, "setStyle", arguments); },
        addStyle: function (sel, style) { return addStyleOn(record, arguments); },
        setClickable: function (sel, val, cb) { return bindClickable(record, sel, val, cb); },
        setHoverable: function (sel, val, cbIn, cbOut) { return bindHoverable(record, sel, val, cbIn, cbOut); },
        selectedAtoms: function (sel) { return selectedAtomsOn(record, sel); },
      };
      return Object.freeze(token);
    }

    // --- model registry ----------------------------------------------------
    function addModel(data, spec) {
      var method = "addModel";
      if (state.disposed) return failNull(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (!fn) return failNull(method, "viewer.addModel unavailable");
      var nativeModel;
      try {
        nativeModel = fn.call(state.viewer, data, spec);
      } catch (e) {
        return failNull(method, e && e.message);
      }
      if (!nativeModel) return failNull(method, "native addModel returned nothing");
      var record = { native: nativeModel, token: null, alive: true, cbGen: { v: 0 } };
      record.token = makeModelToken(record);
      state.models.push(record);
      return okVal(method, record.token);
    }

    function removeModel(handle) {
      var method = "removeModel";
      if (state.disposed) return fail(method, "disposed");
      var idx;
      if (handle === undefined || handle === null) {
        idx = state.models.length - 1;
      } else {
        idx = -1;
        for (var i = 0; i < state.models.length; i++) {
          if (state.models[i].token === handle) { idx = i; break; }
        }
        if (idx === -1) return fail(method, "unknown or foreign model handle");
      }
      if (idx < 0) return fail(method, "no model to remove");
      var record = state.models[idx];
      var fn = nativeMethod(state.viewer, "removeModel");
      if (fn) {
        try { fn.call(state.viewer, record.native); } catch (e) { return fail(method, e && e.message); }
      }
      record.alive = false;
      record.cbGen.v += 1;
      state.models.splice(idx, 1);
      return ok(method);
    }

    function removeAllModels() {
      var method = "removeAllModels";
      if (state.disposed) return fail(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (fn) {
        try { fn.call(state.viewer); } catch (e) { return fail(method, e && e.message); }
      }
      invalidateAllModels();
      state.sceneGen += 1; // scene/task generation changed → old callbacks dead
      return ok(method);
    }

    function getModelCount() {
      if (state.disposed) return 0;
      return state.models.length;
    }

    function getModel(index) {
      var method = "getModel";
      if (state.disposed) return failNull(method, "disposed");
      if (typeof index !== "number" || index !== (index | 0) || index < 0 || index >= state.models.length) {
        return failNull(method, "index out of range");
      }
      return okVal(method, state.models[index].token);
    }

    // --- shape / label tokens ---------------------------------------------
    function addShapeToken(method, args) {
      if (state.disposed) return failNull(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (!fn) return failNull(method, method + " unavailable");
      var nativeShape;
      try {
        nativeShape = fn.apply(state.viewer, args);
      } catch (e) {
        return failNull(method, e && e.message);
      }
      var token = Object.freeze({});
      state.shapes.push({ token: token, native: nativeShape });
      return okVal(method, token);
    }

    function removeShape(handle) {
      var method = "removeShape";
      if (state.disposed) return fail(method, "disposed");
      var idx = tokenIndex(state.shapes, handle);
      if (idx === -1) return fail(method, "unknown or foreign shape handle");
      var entry = state.shapes[idx];
      var fn = nativeMethod(state.viewer, "removeShape");
      if (fn) {
        try { fn.call(state.viewer, entry.native); } catch (e) { return fail(method, e && e.message); }
      }
      state.shapes.splice(idx, 1);
      return ok(method);
    }

    function removeAllShapes() {
      var method = "removeAllShapes";
      if (state.disposed) return fail(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (fn) {
        try { fn.call(state.viewer); } catch (e) { return fail(method, e && e.message); }
      }
      state.shapes.length = 0;
      return ok(method);
    }

    /** Business never passes a native model: an object anchor keeps its own
     * selection fields, any native model/viewer reference is stripped, and the
     * internal current model is injected when a serial anchor is used. */
    function resolveAnchor(anchor) {
      if (anchor == null || typeof anchor !== "object") return null;
      var out = {};
      for (var k in anchor) {
        if (Object.prototype.hasOwnProperty.call(anchor, k)) out[k] = anchor[k];
      }
      if (out.viewer) delete out.viewer;
      if (out.model && !isOwnedNative(out.model)) delete out.model;
      if (out.serial !== undefined && out.model === undefined) {
        var rec = currentRecord();
        if (rec) out.model = rec.native;
      }
      return out;
    }

    function isOwnedNative(candidate) {
      for (var i = 0; i < state.models.length; i++) {
        if (state.models[i].native === candidate) return true;
      }
      return false;
    }

    function addLabel(text, labelOpts, anchor) {
      var method = "addLabel";
      if (state.disposed) return failNull(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (!fn) return failNull(method, "addLabel unavailable");
      var resolved = resolveAnchor(anchor);
      var nativeLabel;
      try {
        nativeLabel = fn.call(state.viewer, text, labelOpts || {}, resolved === null ? undefined : resolved);
      } catch (e) {
        return failNull(method, e && e.message);
      }
      var token = Object.freeze({});
      state.labels.push({ token: token, native: nativeLabel });
      return okVal(method, token);
    }

    function removeLabel(handle) {
      var method = "removeLabel";
      if (state.disposed) return fail(method, "disposed");
      var idx = tokenIndex(state.labels, handle);
      if (idx === -1) return fail(method, "unknown or foreign label handle");
      var entry = state.labels[idx];
      var fn = nativeMethod(state.viewer, "removeLabel");
      if (fn) {
        try { fn.call(state.viewer, entry.native); } catch (e) { return fail(method, e && e.message); }
      }
      state.labels.splice(idx, 1);
      return ok(method);
    }

    function removeAllLabels() {
      var method = "removeAllLabels";
      if (state.disposed) return fail(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (fn) {
        try { fn.call(state.viewer); } catch (e) { return fail(method, e && e.message); }
      }
      state.labels.length = 0;
      return ok(method);
    }

    // --- camera / render ---------------------------------------------------
    function simpleArgs(method, args) {
      if (state.disposed) return fail(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (!fn) return fail(method, method + " unavailable");
      try {
        fn.apply(state.viewer, args);
      } catch (e) {
        return fail(method, e && e.message);
      }
      return ok(method);
    }

    function getView() {
      var method = "getView";
      if (state.disposed) return failNull(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (!fn) return failNull(method, "getView unavailable");
      var view;
      try { view = fn.call(state.viewer); } catch (e) { return failNull(method, e && e.message); }
      if (view == null) return failNull(method, "no view");
      if (typeof view.slice === "function") return okVal(method, view.slice());
      if (typeof view.length === "number") return okVal(method, Array.prototype.slice.call(view));
      return okVal(method, view);
    }

    function pngURI() {
      var method = "pngURI";
      if (state.disposed) return failNull(method, "disposed");
      var fn = nativeMethod(state.viewer, method);
      if (!fn) return failNull(method, "pngURI unavailable");
      var data;
      try { data = fn.call(state.viewer); } catch (e) { return failNull(method, e && e.message); }
      if (!data) return failNull(method, "no image data");
      return okVal(method, data);
    }

    // --- public instance ---------------------------------------------------
    var instance = {
      // model / style (instance scope = current model)
      addModel: addModel,
      removeModel: removeModel,
      removeAllModels: removeAllModels,
      getModelCount: getModelCount,
      getModel: getModel,
      setStyle: function (sel, style) { return styleOn(currentRecord(), "setStyle", arguments); },
      addStyle: function (sel, style) { return addStyleOn(currentRecord(), arguments); },
      setClickable: function (sel, val, cb) { return bindClickable(currentRecord(), sel, val, cb); },
      setHoverable: function (sel, val, cbIn, cbOut) { return bindHoverable(currentRecord(), sel, val, cbIn, cbOut); },
      selectedAtoms: function (sel) { return selectedAtomsOn(currentRecord(), sel); },

      clearScene: function () {
        var method = "clearScene";
        if (state.disposed) return fail(method, "disposed");
        clearSceneNative(state.viewer);
        invalidateAllModels();
        state.shapes.length = 0;
        state.labels.length = 0;
        state.sceneGen += 1;
        return ok(method);
      },

      // shapes / labels
      addSphere: function (spec) { return addShapeToken("addSphere", arguments); },
      addCylinder: function (spec) { return addShapeToken("addCylinder", arguments); },
      addArrow: function (spec) { return addShapeToken("addArrow", arguments); },
      removeShape: removeShape,
      removeAllShapes: removeAllShapes,
      addLabel: addLabel,
      removeLabel: removeLabel,
      removeAllLabels: removeAllLabels,
      setBackgroundColor: function (color) { return simpleArgs("setBackgroundColor", arguments); },

      // camera / render
      setView: function (view) { return simpleArgs("setView", arguments); },
      getView: getView,
      center: function (sel, duration) { return simpleArgs("center", arguments); },
      zoom: function (factor, duration) { return simpleArgs("zoom", arguments); },
      zoomTo: function (sel, duration) { return simpleArgs("zoomTo", arguments); },
      render: function () { return simpleArgs("render", arguments); },
      /** Size-guarded resize: 3Dmol's resize() destroys and recreates the
       * canvas (and its WebGL context); when the container size did not
       * change, the native call is skipped so spurious resize triggers
       * cannot churn contexts into loss (audit F09). */
      resize: function () {
        if (!state.disposed && state.container && state.container.isConnected) {
          var w = state.container.clientWidth;
          var h = state.container.clientHeight;
          if (w > 0 && h > 0 && w === state.resizeW && h === state.resizeH) {
            return ok("resize");
          }
        }
        var result = simpleArgs("resize", arguments);
        if (result !== false && state.container && state.container.isConnected) {
          state.resizeW = state.container.clientWidth;
          state.resizeH = state.container.clientHeight;
        }
        return result;
      },
      pngURI: pngURI,
      /** Single framing entry (framing.js via mol3d.frame); null when the
       * namespace is unavailable or the scene is disposed. */
      frame: function (opts2) {
        if (state.disposed) return null;
        var m = mol3d();
        if (m && typeof m.frame === "function" && state.viewer) {
          return m.frame(state.viewer, opts2);
        }
        return null;
      },

      // --- animation ownership ---------------------------------------------
      /** play(onTick, intervalMs) -> id. One interval per instance; starting
       * while running restarts cleanly (previous timer cancelled at once). */
      play: function (onTick, intervalMs) {
        if (state.disposed || typeof onTick !== "function") return null;
        stopTimer(); // cancels any previous interval + bumps the tick guard
        state.playing = true;
        state.paused = false;
        var seq = state.playSeq;
        var ms = typeof intervalMs === "number" && intervalMs > 0 ? intervalMs : 16;
        state.timerId = setInterval(function () {
          if (state.disposed || seq !== state.playSeq) return; // no late ticks
          if (state.paused || state.hidden) return;
          try { onTick(); } catch (e) { /* keep the loop alive */ }
        }, ms);
        return seq;
      },
      stopPlayback: function () {
        if (state.disposed || !state.playing) return;
        state.playing = false;
        state.paused = false;
        stopTimer();
      },
      pausePlayback: function () {
        if (state.disposed || !state.playing) return;
        state.paused = true;
      },
      resumePlayback: function () {
        if (state.disposed || !state.playing || !state.paused) return;
        state.paused = false;
      },
      /** Pause ticking while hidden without losing the playing state; does
       * not start playback on a stopped instance. */
      setHidden: function (hidden) {
        if (state.disposed) return;
        state.hidden = !!hidden;
      },

      // --- error contract ---------------------------------------------------
      lastError: function () {
        return state.lastError ? { method: state.lastError.method, message: state.lastError.message } : null;
      },
      clearError: function () {
        state.lastError = null;
        return true;
      },

      // --- lifecycle ---------------------------------------------------------
      isDisposed: function () {
        return state.disposed;
      },
      /** Idempotent teardown: stop timers (no late ticks), clear the scene,
       * run destroyImpl exactly once, drop the internal viewer reference,
       * then notify onDispose once. */
      dispose: function () {
        if (state.disposed) return;
        state.disposed = true;
        state.playing = false;
        state.paused = false;
        stopTimer();
        var v = state.viewer;
        clearSceneNative(v);
        invalidateAllModels();
        state.shapes.length = 0;
        state.labels.length = 0;
        state.sceneGen += 1; // invalidate any in-flight callbacks immediately
        if (typeof destroyImpl === "function") {
          try { destroyImpl(v); } catch (e) { /* release is best-effort */ }
        }
        state.viewer = null;
        if (onDispose) {
          try { onDispose(); } catch (e) { /* notify is best-effort */ }
        }
      },
    };

    instanceApi = instance;
    return Object.freeze(instance);
  }

  return { create: create };
})();
