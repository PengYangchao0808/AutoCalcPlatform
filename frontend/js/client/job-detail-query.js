/*
 * ACP Client — job detail (+ files) resource query.
 *
 * Owner: frontend/js/client/** (OpenCode). Fetches go through the injected
 * request adapters (no fetch/URL construction of its own); ResourceStore is
 * the only cache write path. Version: v20261017.1
 *
 * Endpoints used (unchanged URLs):
 *   GET /jobs/{id}/detail — rich projection (stages / artifacts_summary /
 *     error_detail / disk_state / recovery); may not exist on older servers.
 *   GET /jobs/{id}        — legacy record fallback.
 *   GET /jobs/{id}/files, GET /jobs/{id}/remote-files — task file listings.
 *
 * Legacy-endpoint pin (R1): ONLY an HTTP 404/405 on /detail (err.status —
 * transport errors always carry it) pins the endpoint as missing and routes
 * every later fetch straight to the legacy record. 401/403/409/422/5xx and
 * network/timeout errors are surfaced to the caller and never pin. The pin
 * is reported exactly once through init({onLegacyPin}) so the page keeps its
 * legacy `jobDetailEndpointMissing` global in sync from a single write point.
 *
 * The legacy wrap shape is ported verbatim from the former inline fallback in
 * features/job-detail.js::fetchJobDetail:
 *   { job, stages: null, artifacts_summary: null, error_detail: null,
 *     disk_state: null, recovery: null, legacy: true }
 */
window.ACPClient = window.ACPClient || {};
ACPClient.jobDetail = (function () {
  "use strict";

  var store = window.ACPClient && window.ACPClient.store;
  var adapters = { v1: null, v1Remote: null };
  var endpointMissing = false;
  var pinNotified = false;
  var onLegacyPin = null;

  var DETAIL_GROUP = "job-detail";
  var FILES_GROUP = "job-files";

  function init(deps) {
    deps = deps || {};
    if (deps.store) store = deps.store;
    else if (window.ACPClient && window.ACPClient.store) store = window.ACPClient.store;
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.v1Remote === "function") adapters.v1Remote = deps.v1Remote;
    else if (typeof deps.v1 === "function") adapters.v1Remote = deps.v1;
    if (typeof deps.onLegacyPin === "function") onLegacyPin = deps.onLegacyPin;
    if (deps.endpointMissing != null) {
      endpointMissing = !!deps.endpointMissing;
      // Pre-pinned via init: suppress a redundant late notification — the
      // callback contract is "fired once, at the moment the pin is set".
      pinNotified = endpointMissing;
    }
  }

  function notifyLegacyPin() {
    if (pinNotified) return;
    pinNotified = true;
    if (onLegacyPin) {
      try { onLegacyPin(); } catch (e) { /* page callback must never break the fetch */ }
    }
  }

  // Structured status only: transport errors carry err.status; network
  // failures / own-timeout errors have no status and must surface untouched.
  function is404or405(err) {
    return !!err && (err.status === 404 || err.status === 405);
  }

  // Verbatim port of the legacy record wrap (features/job-detail.js).
  function legacyWrap(job) {
    return {
      job: job,
      stages: null,
      artifacts_summary: null,
      error_detail: null,
      disk_state: null,
      recovery: null,
      legacy: true,
    };
  }

  function fetchLegacyRecord(id, signal) {
    return adapters.v1("/jobs/" + encodeURIComponent(id), { signal: signal }).then(legacyWrap);
  }

  /**
   * fetchDetail(jobId, {signal}) -> Promise<detail>
   * Store key "job-detail:{id}". Loader: GET /jobs/{id}/detail; on HTTP
   * 404/405 pin the endpoint (one onLegacyPin notification) and wrap the
   * legacy GET /jobs/{id} record. A 200 whose body has no `job` object also
   * falls back to the legacy record for THIS call only (no pin — the page
   * behaved exactly this way before). Other errors propagate to the caller
   * and leave the cached entry in "error" status (the next query retries).
   */
  function fetchDetail(jobId, opts) {
    opts = opts || {};
    if (!adapters.v1) return Promise.reject(new Error("ACPClient.jobDetail.init({v1}) not configured"));
    if (!store) return Promise.reject(new Error("ACPClient.store not available"));
    var id = String(jobId || "");
    if (!id) return Promise.reject(new Error("fetchDetail requires a valid jobId"));

    var key = DETAIL_GROUP + ":" + id;
    return store.query(key, {
      group: DETAIL_GROUP,
      signal: opts.signal || null,
      loader: function (signal) {
        if (endpointMissing) return fetchLegacyRecord(id, signal);
        return adapters.v1("/jobs/" + encodeURIComponent(id) + "/detail", { signal: signal }).then(
          function (resp) {
            if (resp && typeof resp.job === "object" && resp.job !== null) return resp;
            // 200 but not the rich projection shape — legacy record, no pin.
            return fetchLegacyRecord(id, signal);
          },
          function (err) {
            if (is404or405(err)) {
              endpointMissing = true;
              notifyLegacyPin();
              return fetchLegacyRecord(id, signal);
            }
            throw err;
          }
        );
      },
    });
  }

  /**
   * fetchFiles(jobId, isRemote, {signal}) -> Promise<{files, pinned, is_remote}>
   * Store key "job-files:{id}:local|remote". Remote listings are remapped to
   * the local shape (name -> path, mtime -> modified). The selected-job
   * selection sequence currently loads files through its page hook (which
   * owns the 60s remote budget call site); this client method is the shared
   * resource path for other consumers.
   */
  function fetchFiles(jobId, isRemote, opts) {
    opts = opts || {};
    var requestFn = isRemote ? adapters.v1Remote : adapters.v1;
    if (!requestFn) return Promise.reject(new Error("ACPClient.jobDetail.init({v1, v1Remote}) not configured"));
    if (!store) return Promise.reject(new Error("ACPClient.store not available"));
    var id = String(jobId || "");
    if (!id) return Promise.reject(new Error("fetchFiles requires a valid jobId"));

    var key = FILES_GROUP + ":" + id + ":" + (isRemote ? "remote" : "local");
    var path = isRemote
      ? "/jobs/" + encodeURIComponent(id) + "/remote-files"
      : "/jobs/" + encodeURIComponent(id) + "/files";

    return store.query(key, {
      group: FILES_GROUP,
      signal: opts.signal || null,
      loader: function (signal) {
        return requestFn(path, { signal: signal }).then(function (resp) {
          resp = resp || {};
          var rawFiles = resp.files || [];
          var files = isRemote
            ? rawFiles.map(function (f) {
                return {
                  path: f.name || f.path,
                  name: f.name || f.path,
                  size: f.size,
                  modified: f.mtime != null ? f.mtime : f.modified,
                  is_dir: !!f.is_dir,
                };
              })
            : rawFiles;
          return {
            files: files,
            pinned: Array.isArray(resp.pinned) ? resp.pinned : [],
            is_remote: !!isRemote,
          };
        });
      },
    });
  }

  function invalidateDetail(jobId) {
    if (!store) return;
    if (jobId == null) store.invalidate(DETAIL_GROUP + ":*");
    else store.invalidate(DETAIL_GROUP + ":" + String(jobId));
  }

  function invalidateFiles(jobId) {
    if (!store) return;
    if (jobId == null) store.invalidate(FILES_GROUP + ":*");
    else store.invalidate(FILES_GROUP + ":" + String(jobId) + ":*");
  }

  function invalidateAll() {
    invalidateDetail();
    invalidateFiles();
  }

  /** "detail" while /jobs/{id}/detail is trusted, "legacy" once 404/405 pinned it missing. */
  function endpointMode() {
    return endpointMissing ? "legacy" : "detail";
  }

  return {
    init: init,
    fetchDetail: fetchDetail,
    fetchFiles: fetchFiles,
    invalidateDetail: invalidateDetail,
    invalidateFiles: invalidateFiles,
    invalidateAll: invalidateAll,
    endpointMode: endpointMode,
    snapshotDetail: function (jobId) {
      return store ? store.snapshot(DETAIL_GROUP + ":" + String(jobId)) : null;
    },
    snapshotFiles: function (jobId, isRemote) {
      return store ? store.snapshot(FILES_GROUP + ":" + String(jobId) + ":" + (isRemote ? "remote" : "local")) : null;
    },
    isEndpointMissing: function () {
      return endpointMissing;
    },
  };
})();
