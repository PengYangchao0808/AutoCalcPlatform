/*
 * ACP Client — system/resource endpoint adapters (T22).
 *
 * Owner: frontend/js/client/** (OpenCode). Version: v20261018.1
 *
 * Single request path for the System & Resources panels. Every legacy request
 * site (`reaction-builder.js` loadNodes/loadBackends) is re-homed here; the
 * Workbench use case (controllers/system_resources_controller.js) never calls
 * fetch directly.
 *
 * Endpoint contract (URLs unchanged):
 *   1. fetchNodes(opts)             — GET {API_BASE}/nodes
 *      Legacy: api("/nodes") → /api/v1/nodes, default 8 s budget.
 *   2. fetchBackends(opts)          — GET /api/backends (LITERAL root path)
 *      Legacy: apiRequest("/api/backends") — this endpoint is not under
 *      /api/v1; the base URL is intentionally NOT applied.
 *   3. fetchSoftwareDiscovery(opts) — GET {API_BASE}/software/discovery
 *      Legacy: api("/software/discovery"), default 8 s budget.
 *
 * Errors/timeouts are the existing transport contract, unchanged:
 *   - non-2xx rejects with an Error whose message is the FastAPI detail
 *     (or "HTTP <status>"); `err.status` and `err.body` are additive.
 *   - own timeouts become localized "Request timed out" errors; caller
 *     signals are forwarded and keep their AbortError name.
 *   - `opts` is passed through verbatim (callers may inject `signal` or
 *     `fetchImpl` for tests).
 *
 * Wiring: `init({ v1, direct })` from the page compat/api.js chokepoint
 * (the same place every other Client module receives its adapters). `v1` is
 * the page api() wrapper (owns API_BASE + non-GET invalidation); `direct` is
 * ACPClient.transport.request (needed for the literal /api/backends path).
 * None of these are GET-invalidating, so no cache chokepoint is bypassed.
 */
window.ACPClient = window.ACPClient || {};
ACPClient.systemEndpoints = (function () {
  "use strict";

  var adapters = { v1: null, direct: null };
  var baseUrl = "/api/v1";

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.direct === "function") adapters.direct = deps.direct;
    if (typeof deps.baseUrl === "string") baseUrl = deps.baseUrl;
  }

  function call(implName, path, opts) {
    var impl = adapters[implName];
    if (typeof impl !== "function") {
      return Promise.reject(new Error(
        "systemEndpoints: init({ " + implName + " }) missing"
      ));
    }
    try {
      return Promise.resolve(impl(path, opts));
    } catch (e) {
      return Promise.reject(e);
    }
  }

  // 1. GET {API_BASE}/nodes — node list (legacy api("/nodes")).
  function fetchNodes(opts) {
    return call("v1", "/nodes", opts);
  }

  // 2. GET /api/backends — literal root path (legacy apiRequest("/api/backends")).
  function fetchBackends(opts) {
    return call("direct", "/api/backends", opts);
  }

  // 3. GET {API_BASE}/software/discovery — best-effort version/path discovery.
  function fetchSoftwareDiscovery(opts) {
    return call("v1", "/software/discovery", opts);
  }

  function getBaseUrl() {
    return baseUrl;
  }

  return {
    init: init,
    fetchNodes: fetchNodes,
    fetchBackends: fetchBackends,
    fetchSoftwareDiscovery: fetchSoftwareDiscovery,
    getBaseUrl: getBaseUrl,
  };
})();
