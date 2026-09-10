/**
 * ACP Structure Viewer — state store + catalog fetch + stale-response guard
 * @version 0.2.0
 *
 * Namespace: window.ACPStructureViewer
 *
 * Exposes:
 *   - state                     (the live structureViewerState object)
 *   - loadStructureViewer(jobId, opts)
 *   - selectEntry(entryId, origin)
 *   - refreshIfChanged()
 *   - loadSelectedGeometry()    (stub; todo 17 fills)
 *
 * Internal (test-overridable via namespace property):
 *   - _fetchImpl                (default: window.fetch; tests inject a fake)
 *   - _applyCatalogResponse     (pure: applies a server response to state)
 *
 * TODO(todo-16): render structure list + inspector from state
 * TODO(todo-17): wire selectJob -> loadStructureViewer, geometry loading
 * TODO(todo-18): energy-graph selection push via selectEntry + selectionToken
 * TODO(todo-19): phase-A contract tests + i18n completeness
 */
(function () {
  "use strict";

  var VERSION = "0.2.0";

  var STR_NEWER_AVAILABLE = "\u6709\u65b0\u7ed3\u6784\u53ef\u7528"; // 有新结构可用

  /** @returns {Function|null} */
  function _getFetchImpl() {
    return (typeof window !== "undefined" && window.ACPStructureViewer && window.ACPStructureViewer._fetchImpl) || null;
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
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") { return; }
        if (capturedToken !== structureViewerState.requestToken) { return; }
        structureViewerState.error = err && err.message ? err.message : String(err);
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

  /* ---- public namespace ---- */
  window.ACPStructureViewer = {
    version: VERSION,
    state: structureViewerState,
    loadStructureViewer: loadStructureViewer,
    selectEntry: selectEntry,
    refreshIfChanged: refreshIfChanged,
    loadSelectedGeometry: loadSelectedGeometry,
    _applyCatalogResponse: _applyCatalogResponse,
    _fetchImpl: (typeof window !== "undefined" && window.fetch) ? window.fetch.bind(window) : null,
  };
})();
