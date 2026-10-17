/*
 * ACP Visualization — viewer framing lifecycle.
 *
 * Owner: frontend/js/visualization/** (OpenCode). Verbatim extraction of the
 * inline helpers frame3DViewer / waitForContainerStable / scheduleViewerFraming
 * / disposeViewerFraming from ACP_Workbench_v2.html (behavior-preserving move;
 * the page keeps same-name delegators until Antigravity switches call sites).
 * Version: v20261016.1
 *
 * Contract (root AGENTS anti-pattern #22): 3Dmol viewers must be framed via
 * scheduleViewerFraming/frame3DViewer — pages must not hand-roll resize/zoom
 * sequences. This module never touches the network and never reads global
 * task state; it operates purely on the container/viewer objects it is given.
 */
window.ACPVisualization = window.ACPVisualization || {};
ACPVisualization.framing = (function () {
  "use strict";

  function frame3DViewer(viewer3dmol, options) {
    if (!viewer3dmol) return;
    options = options || {};
    try {
      viewer3dmol.resize();
      if (!options.preserveView) {
        viewer3dmol.center({}, 0);
        viewer3dmol.zoomTo({}, 0);
        if (typeof options.zoomFactor === "number" && options.zoomFactor > 0) {
          viewer3dmol.zoom(options.zoomFactor, 0);
        }
      }
      viewer3dmol.render();
    } catch (e) {}
  }

  function waitForContainerStable(container, callback, options) {
    if (!container || !callback) return;
    options = options || {};
    var maxFrames = typeof options.maxFrames === "number" ? options.maxFrames : 45;
    var isValid = typeof options.isValid === "function" ? options.isValid : null;
    var frames = 0;
    var lastW = 0, lastH = 0;
    var stableCount = 0;
    function tick() {
      if (!container.isConnected) return;
      if (isValid && !isValid()) return;
      frames++;
      var w = container.clientWidth;
      var h = container.clientHeight;
      if (w > 0 && h > 0 && w === lastW && h === lastH) {
        stableCount++;
      } else {
        stableCount = 0;
      }
      lastW = w;
      lastH = h;
      if ((w > 0 && h > 0 && stableCount >= 1) || frames >= maxFrames) {
        if (w > 0 && h > 0) {
          callback();
        }
        return;
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function scheduleViewerFraming(container, framingState, getViewer, options) {
    if (!container || !framingState) return;
    options = options || {};
    if (framingState.timer) { clearTimeout(framingState.timer); framingState.timer = null; }
    framingState.epoch += 1;
    framingState.pending = true;
    framingState.settled = false;
    var epoch = framingState.epoch;
    waitForContainerStable(container, function() {
      if (epoch !== framingState.epoch) return;
      if (!container.isConnected) return;
      var v = getViewer();
      if (!v) return;
      frame3DViewer(v, options);
      framingState.lastW = container.clientWidth;
      framingState.lastH = container.clientHeight;
      framingState.pending = false;
      framingState.settled = true;
    }, {
      isValid: function() {
        return epoch === framingState.epoch && container.isConnected && (options.isValid ? options.isValid() : true);
      }
    });
    if (framingState.observer) { framingState.observer.disconnect(); }
    if (typeof ResizeObserver !== "undefined") {
      framingState.observer = new ResizeObserver(function() {
        if (framingState.timer) clearTimeout(framingState.timer);
        framingState.timer = setTimeout(function() {
          framingState.timer = null;
          if (epoch !== framingState.epoch) return;
          if (framingState.pending) return;
          if (!container.isConnected) return;
          var v = getViewer();
          if (!v) return;
          try { v.resize(); v.render(); } catch (e) {}
        }, 100);
      });
      framingState.observer.observe(container);
    }
  }

  function disposeViewerFraming(framingState) {
    if (!framingState) return;
    if (framingState.observer) { framingState.observer.disconnect(); framingState.observer = null; }
    if (framingState.timer) { clearTimeout(framingState.timer); framingState.timer = null; }
    framingState.epoch += 1;
    framingState.pending = false;
    framingState.settled = false;
  }

  return {
    frame3DViewer: frame3DViewer,
    waitForContainerStable: waitForContainerStable,
    scheduleViewerFraming: scheduleViewerFraming,
    disposeViewerFraming: disposeViewerFraming,
  };
})();
