/*
 * ACP Client — task-view resource query (jobs list) + job summary (detail).
 *
 * Owner: frontend/js/client/** (OpenCode). Fetches go through the injected
 * request adapters (no fetch/URL construction of its own beyond the documented
 * query strings); ResourceStore is the only cache write path.
 * Version: v20261016.1
 *
 * S0 endpoints used (unchanged URLs/params):
 *   GET /api/v2/task-view  — params ported verbatim from the legacy inline
 *     refreshJobs param builder (group_by/sort/group_limit=200/max_total=200/
 *     offset/include_facets + status,workflow,molecule,tag,batch,remark filters,
 *     q, archived, running_first, project_id).
 *   GET /jobs/{id}/summary — lightweight selected-job summary (v1).
 *
 * Facet cache: 30s TTL keyed by (project, archived), exactly like the legacy
 * taskFacetCache in ACP_Workbench_v2.html. Facets are NOT part of the store
 * query key (include_facets toggles independently of the cache entry).
 */
window.ACPClient = window.ACPClient || {};
ACPClient.taskView = (function () {
  "use strict";

  var store = ACPClient.store;
  var adapters = { v1: null, v2: null }; // injected: v1(url, opts), v2(path, opts)
  var facetCache = Object.create(null);  // facetKey -> { at, value }
  var FACET_TTL_MS = 30000;

  var GROUP = "task-view";
  var SUMMARY_GROUP = "job-summary";

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.v2 === "function") adapters.v2 = deps.v2;
  }

  /** Ported verbatim from legacy refreshJobs: builds the URLSearchParams for
   * /api/v2/task-view. `needFacets` and `offset` are appended exactly like the
   * legacy code (after queryKey was captured). */
  function buildParams(prefs, selectedProjectId, offset, needFacets) {
    prefs = prefs || {};
    var params = new URLSearchParams();
    if (selectedProjectId) params.set("project_id", selectedProjectId);
    params.set("group_by", prefs.groupBy || "molecule");
    params.set("sort", prefs.sort || "created_desc");
    params.set("group_limit", "200");
    ["status", "workflow", "molecule", "tag", "batch", "remark"].forEach(function (dim) {
      var arr = prefs.filters && prefs.filters[dim];
      if (arr && arr.length) params.set(dim, arr.join(","));
    });
    if (prefs.q) params.set("q", prefs.q);
    if (prefs.archived && prefs.archived !== "exclude") params.set("archived", prefs.archived);
    if (prefs.runningFirst) params.set("running_first", "true");
    params.set("max_total", "200");
    params.set("offset", String(offset));
    params.set("include_facets", String(needFacets));
    return params;
  }

  /** Legacy `queryKey`: params.toString() BEFORE max_total/offset/include_facets
   * are appended — used to detect filter changes that reset pagination. */
  function queryKeyOf(prefs, selectedProjectId) {
    var params = new URLSearchParams();
    if (selectedProjectId) params.set("project_id", selectedProjectId);
    params.set("group_by", (prefs && prefs.groupBy) || "molecule");
    params.set("sort", (prefs && prefs.sort) || "created_desc");
    params.set("group_limit", "200");
    ["status", "workflow", "molecule", "tag", "batch", "remark"].forEach(function (dim) {
      var arr = prefs && prefs.filters && prefs.filters[dim];
      if (arr && arr.length) params.set(dim, arr.join(","));
    });
    if (prefs && prefs.q) params.set("q", prefs.q);
    if (prefs && prefs.archived && prefs.archived !== "exclude") params.set("archived", prefs.archived);
    if (prefs && prefs.runningFirst) params.set("running_first", "true");
    return params.toString();
  }

  function facetKeyOf(selectedProjectId, archived) {
    return (selectedProjectId || "__all__") + ":" + (archived || "exclude");
  }

  function getFacets(selectedProjectId, archived) {
    var cached = facetCache[facetKeyOf(selectedProjectId, archived)];
    return (cached && Date.now() - cached.at <= FACET_TTL_MS) ? cached.value : null;
  }

  function putFacets(selectedProjectId, archived, value) {
    facetCache[facetKeyOf(selectedProjectId, archived)] = { at: Date.now(), value: value || {} };
  }

  /**
   * fetchView(prefs, selectedProjectId, offset, opts) -> Promise<body>
   * Facet behavior mirrors legacy refreshJobs: when fresh cached facets exist,
   * include_facets=false is requested and the cached facets are re-attached to
   * the returned body (body.facets = cached). Late/stale responses reject with
   * name 'StaleDiscard'; caller aborts reject with AbortError.
   */
  function fetchView(prefs, selectedProjectId, offset, opts) {
    opts = opts || {};
    if (!adapters.v2) return Promise.reject(new Error("ACPClient.taskView.init({v2}) not configured"));
    var archived = (prefs && prefs.archived) || "exclude";
    var cachedFacets = getFacets(selectedProjectId, archived);
    var needFacets = !cachedFacets;
    var params = buildParams(prefs, selectedProjectId, offset == null ? 0 : offset, needFacets);
    var key = GROUP + ":" + queryKeyOf(prefs, selectedProjectId) + ":" + (offset == null ? 0 : offset);
    return store.query(key, {
      group: GROUP,
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v2("/task-view?" + params.toString(), { signal: signal }).then(function (body) {
          var validation = ACPWorkbench.contracts.taskView.validateBody(body);
          if (!validation.ok) throw new Error("task-view: " + validation.problems.join("; "));
          var result = Object.assign({}, body);
          if (!needFacets) result.facets = cachedFacets;
          return result;
        });
      },
    }).then(function (body) {
      if (needFacets) putFacets(selectedProjectId, archived, body.facets || {});
      // Page sorting must not mutate the shared resource snapshot.
      return Object.assign({}, body, { groups: body.groups.map(function (group) {
        return Object.assign({}, group, { jobs: group.jobs.slice() });
      }) });
    });
  }

  /** Lightweight selected-job summary. Guard against selection change stays
   * with the caller (controller) — the store only guarantees freshness of the
   * cached copy per job id. */
  function fetchSummary(jobId, opts) {
    opts = opts || {};
    if (!adapters.v1) return Promise.reject(new Error("ACPClient.taskView.init({v1}) not configured"));
    var id = String(jobId);
    return store.query(SUMMARY_GROUP + ":" + id, {
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v1("/jobs/" + encodeURIComponent(id) + "/summary", { signal: signal });
      },
    });
  }

  /** Post-write invalidation hook — Antigravity wires the legacy
   * invalidateTaskViewMetadata() to also call this. Clears facet cache and
   * drops cached task-view pages so the next refresh refetches. */
  function invalidateAll() {
    facetCache = Object.create(null);
    store.invalidate(GROUP + ":*");
  }

  function invalidateSummary(jobId) {
    if (jobId == null) store.invalidate(SUMMARY_GROUP + ":*");
    else store.invalidate(SUMMARY_GROUP + ":" + String(jobId));
  }

  return {
    init: init,
    buildParams: buildParams,
    queryKeyOf: queryKeyOf,
    fetchView: fetchView,
    fetchSummary: fetchSummary,
    invalidateAll: invalidateAll,
    invalidateSummary: invalidateSummary,
    snapshotView: function (prefs, selectedProjectId, offset) {
      return store.snapshot(GROUP + ":" + queryKeyOf(prefs, selectedProjectId) + ":" + (offset == null ? 0 : offset));
    },
    subscribeView: function (prefs, selectedProjectId, offset, listener) {
      return store.subscribe(GROUP + ":" + queryKeyOf(prefs, selectedProjectId) + ":" + (offset == null ? 0 : offset), listener);
    },
  };
})();
