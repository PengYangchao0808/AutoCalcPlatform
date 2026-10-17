/*
 * ACP Visualization — 3Dmol adapter: the ONLY module allowed to reference the
 * underlying $3Dmol library.
 *
 * Owner: frontend/js/visualization/** (OpenCode). Version: v20261016.1
 *
 * Rules (plan §4.3 / AR-3): pages and feature controllers must not hold raw
 * $3Dmol viewer handles; they request instances through this adapter and frame
 * them via ACPVisualization.framing. The adapter does no networking, reads no
 * global task state, and does not modify the vendored library
 * (js/vendor/3Dmol-2.5.5.min.js + license stay untouched).
 *
 * Teardown follows the established page pattern (energyGraphDestroyViewer):
 * viewer.clear() in try/catch + drop the reference + dispose framing state +
 * disconnect observers; 3Dmol has no real destroy(), so destroy() is idempotent
 * and callers additionally clear their container content when they own it.
 */
window.ACPVisualization = window.ACPVisualization || {};
ACPVisualization.mol3d = (function () {
  "use strict";

  var framing = ACPVisualization.framing;

  function isAvailable() {
    return typeof $3Dmol !== "undefined";
  }

  /** create(container, options) -> viewer | null.
   * Returns null when the library is not (yet) loaded — callers keep their
   * existing empty-state UI; the adapter never throws for a missing library. */
  function create(container, options) {
    if (typeof $3Dmol === "undefined" || !container) return null;
    try {
      return $3Dmol.createViewer(container, options || {});
    } catch (e) {
      return null;
    }
  }

  /** destroy(viewer) — idempotent scene release mirroring page teardown.
   * Repeated destroy on the same instance is a no-op (safe for re-mount and
   * re-teardown paths). */
  var released = new WeakSet();
  function destroy(viewer) {
    if (!viewer || released.has(viewer)) return;
    released.add(viewer);
    try { viewer.clear(); } catch (e) { /* already released */ }
  }

  /** frame(viewer, options) — single framing pass (see framing.js). */
  function frame(viewer, options) {
    framing.frame3DViewer(viewer, options);
  }

  /* Restricted per-canvas instance API lives in visualization/scene_instance.js
   * (ACPVisualization.scene, §42 R5): the raw viewer handle must not be
   * re-exposed from this adapter — no createInstance/getRawViewer escape hatch. */
  return { isAvailable: isAvailable, create: create, destroy: destroy, frame: frame };
})();
