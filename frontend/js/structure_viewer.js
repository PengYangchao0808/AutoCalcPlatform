/**
 * ACP Structure Viewer — Wave 3 skeleton
 * @version 0.1.0
 *
 * Namespace: window.ACPStructureViewer
 *
 * Exposes:
 *   - structureViewerState   (null-initialized placeholder; todo 15 fills fields)
 *   - loadStructureViewer(jobId, opts)  (placeholder; todo 15 wires real fetch)
 *   - selectEntry(entryId, origin)      (placeholder; todo 15)
 *   - refreshIfChanged()                (placeholder; todo 15)
 *
 * TODO(todo-14..19): merge tabs, state store, list/inspector, auto-load,
 *                    energy-graph sync, phase-A contracts.
 */
(function () {
  "use strict";

  /** Version tag — bump on every structural change. */
  var VERSION = "0.1.0";

  /**
   * Structure viewer state store.
   * Null until a job is selected; fields added by todo 15.
   */
  var structureViewerState = null;

  /**
   * Load the structure-viewer catalog for a job.
   *
   * @param {string} jobId - The scheduler job id.
   * @param {Object} [opts] - Options (e.g. { itemId: "..." }).
   * @returns {Promise<void>} Resolved immediately in this skeleton.
   */
  function loadStructureViewer(jobId, opts) {
    // TODO(todo-15): real fetch with AbortController + requestToken
    structureViewerState = {
      jobId: jobId,
      opts: opts || {},
      payload: null,
      revision: null,
      selectedEntryId: null,
      selectionOrigin: null,
      selectionToken: 0,
      dirty: false,
      editState: null,
      requestToken: 0,
    };
    return Promise.resolve();
  }

  /**
   * Select an entry in the structure list.
   *
   * @param {string} entryId - Entry id from the catalog.
   * @param {string} [origin] - Selection origin (e.g. "energy_graph").
   * @returns {void}
   */
  function selectEntry(entryId, origin) {
    // TODO(todo-15): token-guard + geometry fetch + viewer load
    if (structureViewerState) {
      structureViewerState.selectedEntryId = entryId;
      structureViewerState.selectionOrigin = origin || "user";
    }
  }

  /**
   * Refresh the viewer if the catalog revision changed since last load.
   * Preserves dirty coordinates by surfacing a hint instead of replacing.
   *
   * @returns {void}
   */
  function refreshIfChanged() {
    // TODO(todo-15): compare revision, show "有新结构可用" when dirty
  }

  /* ---- public namespace ---- */
  window.ACPStructureViewer = {
    version: VERSION,
    structureViewerState: structureViewerState,
    loadStructureViewer: loadStructureViewer,
    selectEntry: selectEntry,
    refreshIfChanged: refreshIfChanged,
  };
})();
