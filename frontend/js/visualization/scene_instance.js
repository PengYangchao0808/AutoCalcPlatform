/*
 * ACP Visualization — scene instance: bounded per-canvas viewer wrapper.
 *
 * Owner: frontend/js/visualization/** (OpenCode). Version: v20261017.1
 *
 * Plan §42.3 R5 (module-only slice — verified as a module, NOT yet wired into
 * the page; see §42.5 five-step process). The raw viewer handle lives here and
 * is never exposed: controllers hold only this restricted instance API. One
 * instance owns exactly one viewer (multiple canvases must not share a
 * viewer), its animation interval, and its teardown (clearScene + destroyImpl,
 * each run exactly once).
 *
 * Purity: no DOM access, no network, no event streams, and no direct reference
 * to the underlying viewer library — only the guarded ACPVisualization.mol3d
 * namespace is consulted (frame delegation + default destroy), and even that
 * is optional: the instance works against any viewer-shaped object, including
 * plain recording stubs (module tests).
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

  function create(opts) {
    opts = opts || {};
    if (!opts.viewer) return null; // viewer is REQUIRED for this slice
    var destroyImpl = typeof opts.destroyImpl === "function"
      ? opts.destroyImpl
      : defaultDestroyImpl();
    var onDispose = typeof opts.onDispose === "function" ? opts.onDispose : null;

    var state = {
      viewer: opts.viewer, // internal only — never exposed on the instance
      disposed: false,
      playing: false,
      paused: false,
      hidden: false,
      timerId: null,
      playSeq: 0, // late-tick guard: bumped on stop/restart/dispose
    };

    function stopTimer() {
      state.playSeq += 1;
      if (state.timerId !== null) {
        clearInterval(state.timerId);
        state.timerId = null;
      }
    }

    /** Forward a call verbatim to the native method, once, guarded:
     * dropped after dispose, tolerated when the method is missing, and
     * exceptions never escape into caller code. */
    function forward(method) {
      return function () {
        if (state.disposed) return undefined;
        var v = state.viewer;
        var fn = v && v[method];
        if (typeof fn !== "function") return undefined;
        try {
          return fn.apply(v, Array.prototype.slice.call(arguments));
        } catch (e) {
          return undefined;
        }
      };
    }

    function clearSceneNative(v) {
      var methods = ["removeAllModels", "removeAllShapes", "removeAllLabels"];
      for (var i = 0; i < methods.length; i++) {
        var fn = v && v[methods[i]];
        if (typeof fn === "function") {
          try { fn.call(v); } catch (e) { /* scene already released */ }
        }
      }
    }

    // --- model / style -----------------------------------------------------
    var instance = {
      addModel: forward("addModel"),
      setStyle: forward("setStyle"),
      addStyle: forward("addStyle"),
      removeAllModels: forward("removeAllModels"),
      clearScene: function () {
        if (state.disposed) return undefined;
        clearSceneNative(state.viewer);
        return undefined;
      },
      addSphere: forward("addSphere"),
      addCylinder: forward("addCylinder"),
      removeAllShapes: forward("removeAllShapes"),
      removeAllLabels: forward("removeAllLabels"),
      setBackgroundColor: forward("setBackgroundColor"),

      // --- camera / render -------------------------------------------------
      setView: forward("setView"),
      zoomTo: forward("zoomTo"),
      render: forward("render"),
      resize: forward("resize"),
      pngURI: forward("pngURI"),
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
        if (typeof destroyImpl === "function") {
          try { destroyImpl(v); } catch (e) { /* release is best-effort */ }
        }
        state.viewer = null;
        if (onDispose) {
          try { onDispose(); } catch (e) { /* notify is best-effort */ }
        }
      },
    };
    return Object.freeze(instance);
  }

  return { create: create };
})();
