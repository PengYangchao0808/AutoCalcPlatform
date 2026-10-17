/*
 * ACP Workbench controller — task list use case (list + pagination + facets +
 * jobsCache derivation + refresh coordination).
 *
 * Owner: frontend/js/workbench/controllers/** (OpenCode). Version: v20261016.1
 *
 * This controller ports the legacy refreshJobs() use case from
 * js/workbench/compat/workbench-inline.js with ALL page effects moved to
 * injected view callbacks (§38.3: controller coordinates Client + views; it
 * never touches page-private DOM or globals like selectedJobId/jobsCache —
 * those are read/written only through the injected accessors/callbacks).
 *
 * Preserved legacy behaviors (fidelity contract):
 *   - queryKey change (filters/project/sort) resets pagination offset to 0
 *   - offset beyond total clamps to the last page and refetches once
 *   - 30s facet reuse handled by the Client (ACPClient.taskView)
 *   - jobsCache = flattened group rows in group order (contract.flattenRows)
 *   - stale-detail pruning hook runs BEFORE the render key is computed
 *   - render-key gating: unchanged (queryKey, offset, groups, counts, lang,
 *     detail-cache revision) does not re-render the queue
 *   - AbortError AND StaleDiscard are silent; other errors surface via
 *     views.onError with retry, and empty-cache failures also hit
 *     views.onEmptyError (legacy queue-counts failure text)
 *   - a local serial guard keeps late async follow-ups (merge suggestions)
 *     from running after a newer refresh started
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.controllers = ACPWorkbench.controllers || {};
ACPWorkbench.controllers.taskList = (function () {
  "use strict";

  var deps = null;
  var offset = 0;
  var queryKey = null;
  var serial = 0;
  var bodyCache = null;       // legacy taskViewCache
  var renderKey = null;       // legacy taskQueueRenderKey
  var lastAbort = null;
  var requestKey = null;

  function init(injected) {
    if (lastAbort) lastAbort.abort();
    deps = injected || {};
    offset = 0; queryKey = null; serial = 0; bodyCache = null; renderKey = null; lastAbort = null;
    requestKey = null;
  }

  function isSilentCancel(e) {
    return !!e && (e.name === "AbortError" || e.name === "StaleDiscard");
  }

  function getState() {
    return { offset: offset, queryKey: queryKey, hasBody: !!bodyCache };
  }

  /** The refreshJobs use case. Returns the body (or undefined on silent skip). */
  function refresh() {
    if (!deps || !deps.client || !deps.views) throw new Error("taskList controller: init(deps) required");
    var prefs = deps.getPrefs();
    var projectId = deps.getSelectedProjectId();
    var key = deps.client.queryKeyOf(prefs, projectId);
    if (queryKey !== key) { offset = 0; queryKey = key; }

    var currentSerial = ++serial;
    var nextRequestKey = key + ":" + offset;
    if (requestKey !== nextRequestKey || !lastAbort) {
      if (lastAbort) lastAbort.abort();
      lastAbort = new AbortController();
      requestKey = nextRequestKey;
    }

    return deps.client.fetchView(prefs, projectId, offset, { signal: lastAbort.signal }).then(function (body) {
      if (currentSerial !== serial) return undefined; // superseded by a newer refresh
      if (body.total > 0 && offset >= body.total) {
        // Legacy clamp: jump to the last page and refetch once.
        offset = Math.floor((body.total - 1) / 200) * 200;
        return refresh();
      }
      bodyCache = body;
      if (deps.views.onError) deps.views.onError(null);

      if (body && body.groups) deps.views.sortGroups(body, prefs);
      var flat = deps.contract.flattenRows(body);
      if (deps.views.onRows) deps.views.onRows(flat, body); // page updates jobsCache here
      if (deps.views.pruneDetailsAgainstList) deps.views.pruneDetailsAgainstList();

      var nextRenderKey = deps.renderKeyOf([key, offset, body.groups, body.counts,
        deps.getLanguage(), deps.detailCacheRevision()]);
      if (nextRenderKey !== renderKey) {
        renderKey = nextRenderKey;
        if (deps.views.renderQueue) deps.views.renderQueue(body.counts || {});
      }
      if (deps.views.renderPagination) deps.views.renderPagination(body);
      if (deps.views.afterRender) deps.views.afterRender(body, function () { return currentSerial === serial; });
      return body;
    }, function (error) {
      if (currentSerial !== serial || isSilentCancel(error)) return undefined;
      if (deps.views.onError) deps.views.onError(error, refresh);
      if (!bodyCache && deps.views.onEmptyError) deps.views.onEmptyError();
      throw error;
    });
  }

  /** Pagination action (legacy: taskViewOffset = n; refreshJobs()). */
  function setOffset(next) {
    offset = Math.max(0, Number(next) || 0);
    return refresh();
  }

  /** Post-write invalidation (legacy invalidateTaskViewMetadata → next refresh refetches). */
  function invalidateMetadata() {
    if (deps && deps.client && deps.client.invalidateAll) deps.client.invalidateAll();
  }

  function dispose() {
    serial++;
    if (lastAbort) lastAbort.abort();
    lastAbort = null;
    requestKey = null;
  }

  return {
    init: init,
    refresh: refresh,
    setOffset: setOffset,
    invalidateMetadata: invalidateMetadata,
    getState: getState,
    isSilentCancel: isSilentCancel,
    dispose: dispose,
  };
})();
