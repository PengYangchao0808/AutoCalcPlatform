/*
 * ACP Client — unified HTTP transport (frontend architecture slice "Client transport").
 *
 * Owner: frontend/Client slice (ACP_Workbench_v2 integration keeps the legacy
 * `apiRequest(url, opts)` name as a same-signature delegator over this module).
 * Version: v20261016.1
 *
 * Behavior is a verbatim port of the inline `apiRequest` in
 * ACP_Workbench_v2.html (formerly lines 10136-10196):
 *   - per-call `opts.timeoutMs` (default from init settings) with own-timeout
 *     AbortController; an OWN timeout is converted to a localized Error and
 *     never leaks a raw AbortError (root AGENTS anti-pattern #28).
 *     `timeoutMs: 0` explicitly disables the own timeout: no timer is set
 *     and no wrapper signal is injected (a caller signal passes through
 *     as-is) — used by legacy no-budget callers, e.g. wizard candidateApi.
 *   - caller-provided `opts.signal` aborts are forwarded and rethrown as-is so
 *     existing silent-cancel filters (AbortError name checks) keep working.
 *   - non-2xx responses are converted to Error(message) with FastAPI detail
 *     flattening (string detail, 422 validation arrays, JSON stringification).
 *     FE-07 additive contract: the thrown Error also carries `err.status`
 *     (response.status) and `err.body` (the parsed JSON body, or undefined
 *     when the error body is not valid JSON). Message text is unchanged.
 *   - JSON vs text response selection by content-type (FormData uploads and
 *     text/plain endpoints keep working unchanged).
 *   - `opts.fetchImpl` (function) overrides the global fetch for this call
 *     and is stripped from the options forwarded to fetch (module-level test
 *     seam, e.g. structure_source_picker._fetchImpl passthrough).
 *
 * URL building stays with the callers (api()/apiV2() wrappers in the workbench
 * HTML own API_BASE and the "/api/v2" prefix); `remote()` only applies the
 * longer remote budget to an already-built URL, mirroring inline apiRemote().
 */
window.ACPClient = window.ACPClient || {};
ACPClient.transport = (function () {
  "use strict";

  var settings = {
    defaultTimeoutMs: 8000,
    remoteTimeoutMs: 60000,
    translate: null,
  };

  function init(opts) {
    opts = opts || {};
    if (opts.defaultTimeoutMs != null) settings.defaultTimeoutMs = opts.defaultTimeoutMs;
    if (opts.remoteTimeoutMs != null) settings.remoteTimeoutMs = opts.remoteTimeoutMs;
    if (typeof opts.translate === "function") settings.translate = opts.translate;
  }

  function request(url, opts) {
    opts = Object.assign({}, opts || {});
    // Test seam: caller-injected fetch (removed from the options handed to
    // fetch itself). Resolved before the timeout bookkeeping mutates opts.
    var fetchImpl = (typeof opts.fetchImpl === "function") ? opts.fetchImpl : fetch;
    delete opts.fetchImpl;
    var timeoutMs = (opts.timeoutMs != null) ? opts.timeoutMs : settings.defaultTimeoutMs;
    delete opts.timeoutMs;
    // timeoutMs 0 = explicit disable: skip the timer AND the wrapper
    // controller so opts.signal stays the caller's own signal (forwarded to
    // fetch untouched).
    var timedOut = false;
    var _timer = null;
    var callerSignal = opts.signal;
    var forwardAbort = null;
    if (typeof AbortController !== "undefined" && timeoutMs > 0) {
      var _ac = new AbortController();
      _timer = setTimeout(function() { timedOut = true; _ac.abort(); }, timeoutMs);
      opts.signal = _ac.signal;
      if (callerSignal) {
        forwardAbort = function() { _ac.abort(callerSignal.reason); };
        if (callerSignal.aborted) forwardAbort();
        else callerSignal.addEventListener("abort", forwardAbort, { once: true });
      }
    }
    return fetchImpl(url, opts).then(function(response) {
      if (!response.ok) {
        var msg = "HTTP " + response.status;
        var parsedBody; // stays undefined when the body is not valid JSON
        // FastAPI error body flattening (same as legacy inline apiRequest).
        var readBody = response.json().then(function(body) {
          parsedBody = body;
          if (body && body.detail) {
            var d = body.detail;
            if (typeof d === "string") {
              return d;
            } else if (Array.isArray(d)) {
              // FastAPI 422 request-validation errors: detail is a list of
              // {loc, msg, type} objects — flatten them into readable text
              // instead of letting String() collapse them to "[object Object]".
              return d.map(function(item) {
                if (item && typeof item === "object") {
                  var loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
                  return (loc ? loc + ": " : "") + (item.msg || JSON.stringify(item));
                }
                return String(item);
              }).join("; ");
            }
            return JSON.stringify(d);
          }
          return null;
        }).catch(function() { return null; });
        return readBody.then(function(detail) {
          if (detail) msg = detail;
          var err = new Error(msg);
          // FE-07 additive contract: HTTP errors carry the status and the
          // parsed body so callers can branch on status (409 conflicts) or
          // read structured detail without re-fetching. Message unchanged.
          err.status = response.status;
          err.body = parsedBody;
          throw err;
        });
      }
      var ct = response.headers.get("content-type") || "";
      return ct.includes("application/json") ? response.json() : response.text();
    }).catch(function(e) {
      if (timedOut) {
        // Own timeout: never leak the raw "signal is aborted without reason".
        var seconds = Math.round(timeoutMs / 1000);
        if (settings.translate) throw new Error(settings.translate("api.timeout", { seconds: seconds }));
        throw new Error("Request timed out (" + seconds + "s)");
      }
      // Caller-provided signal aborts (job/tab switches) keep their AbortError
      // name so existing silent-cancel filters continue to work.
      throw e;
    }).finally(function() {
      if (_timer) clearTimeout(_timer);
      if (callerSignal && forwardAbort) callerSignal.removeEventListener("abort", forwardAbort);
    });
  }

  function remote(url, opts) {
    opts = Object.assign({}, opts || {});
    if (opts.timeoutMs == null) opts.timeoutMs = settings.remoteTimeoutMs;
    return request(url, opts);
  }

  return { init: init, request: request, remote: remote };
})();
