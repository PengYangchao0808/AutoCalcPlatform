/**
 * ACP Structure Editor — Wave 6 skeleton + animation lock (todo 30)
 * @version 0.2.0
 *
 * Namespace: window.ACPStructureEditor
 *
 * Exposes:
 *   - setLocked(true|false)  (called by ACPVibrationViewer while the mode
 *     animation plays — geometry editing must be disabled then)
 *   - isLocked()             (Wave 6 editor consumes this before any edit)
 *
 * TODO(todo-32): adjacency graph + provenance + fragments
 * TODO(todo-33): bond-length edit + move-side toggle
 * TODO(todo-34): bond-angle edit + collinear fallback
 * TODO(todo-35): dihedral edit
 * TODO(todo-36): undo/redo stack + dirty state
 * TODO(todo-37): save-as-asset + provenance metadata
 */
(function () {
  "use strict";

  /** Version tag — bump on every structural change. */
  var VERSION = "0.2.0";

  /** True while the vibration mode animation owns the canvas. */
  var locked = false;

  /* ---- public namespace ---- */
  window.ACPStructureEditor = {
    version: VERSION,
    setLocked: function (value) {
      locked = value === true;
    },
    isLocked: function () {
      return locked;
    },
  };
})();
