/*
 * ACP Client — wizard endpoint adapters (R3 slice b, plan §42.2 wizard row /
 * §42.4 "命令和表单（R3）").
 *
 * Owner: frontend/js/client/** (OpenCode). One method per former raw-fetch()
 * site in features/wizard.js. Every method mirrors what that legacy site did
 * — URL (+ query), method, headers, body construction, timeout budget and
 * user-facing error text — so wizard.js keeps its local orchestration
 * (tokens, debounces, AbortControllers, UI writes) and only swaps the
 * transport call. Per §42.4 the legacy sites' "no timeout" exception is
 * preserved verbatim: every call passes `timeoutMs: 0` (transport: no own
 * timer, caller signal forwarded untouched) — never the 8s api() default.
 *
 * Adapter wiring: `init({ v1, direct, baseUrl })` from wizard.js module scope
 * (that file loads after compat/api.js, so the page `api()` and API_BASE
 * exist). `direct` is ACPClient.transport.request; `baseUrl` is API_BASE.
 * NONE of the current sites route through `v1`: all seven were raw fetch()
 * calls that never entered the api() non-GET task-view invalidation
 * chokepoint, and that no-invalidation semantics is PRESERVED here (no new
 * invalidation is introduced). `v1` is stored for future slices only.
 *
 * Per-site contract freeze (legacy → new; deviations marked DEV):
 *
 * 1. uploadStructure(formData)            — was POST {API_BASE}/uploads,
 *    FormData body, no explicit Content-Type, no timeout. Legacy error text:
 *    `errBody.detail || "Upload failed"` where errBody = parsed error-body
 *    JSON or `{detail: resp.statusText}`. The transport has already parsed
 *    the error body into err.body, so the adapter re-derives the RAW detail
 *    onto `err.detail` (it may be a non-string, exactly like legacy) and the
 *    "Upload failed" literal stays at the call site.
 *    DEV: when the error body is not JSON, legacy fell back to resp.statusText;
 *    the transport does not preserve statusText, so `err.detail` is undefined
 *    and the caller shows "Upload failed" (legacy showed e.g. "Not Found").
 * 2. parseStructure({content,format,filename}) — was POST {API_BASE}/
 *    structures/parse, JSON headers/body, NO resp.ok check: `await resp.json()`
 *    let a JSON error body flow through the ok:false presentation path. The
 *    adapter RESOLVES with the parsed error body (jsonErrorBodyOrThrow).
 *    DEV: non-JSON error body — legacy surfaced the browser resp.json()
 *    SyntaxError text; here the transport message ("HTTP <status>") survives.
 *    Success with a text/plain body: legacy resp.json() threw SyntaxError;
 *    the transport resolves with the text (unreachable for this endpoint).
 * 3. fetchStructureSource(id, {signal})   — was GET {API_BASE}/structure-
 *    sources/<encodeURIComponent(id)>, no explicit method key, no body.
 *    Legacy error text: ALWAYS `new Error("HTTP " + status)` — the error body
 *    was never read, so the adapter forces that message even when the
 *    transport flattened a FastAPI detail. Success returns the parsed body.
 *    {signal} is additive (legacy had none); a pre-aborted signal rejects
 *    with an AbortError without calling the adapter.
 * 4. uploadNmrBruker(formData, projectId) — was POST {API_BASE}/uploads?
 *    parse=false&project_id=<encodeURIComponent(projectId)>, FormData body.
 *    Legacy error text: `new Error(body.detail || resp.statusText)`.
 *    DEV: without a parsed JSON error body the statusText fallback becomes
 *    "HTTP <status>".
 * 5. hessianPreview(payload, {signal})    — was POST /api/v1/hessian-preview
 *    (LITERAL path — this site never went through API_BASE; baseUrl is
 *    intentionally NOT applied), JSON headers/body. Legacy error text:
 *    `(e.errors || []).map(x => x.message).join("; ") || ("HTTP " + status)`
 *    read from the parsed error body — NOT the FastAPI detail flattening;
 *    reproduced on err.body. DEV: non-JSON error body → "HTTP <status>"
 *    instead of the browser SyntaxError text. {signal} additive.
 * 6. batchConfigPreview(payload, {signal}) — was POST /api/v1/batch-optimize/
 *    config-preview (LITERAL path, baseUrl NOT applied), JSON headers/body,
 *    caller AbortController signal forwarded. Legacy error text: ALWAYS
 *    `new Error("HTTP " + status)`. AbortError keeps its name (caller filter).
 * 7. validateMethod({schema_id, levels})  — was POST {API_BASE}/validate-
 *    method, JSON headers/body, NO resp.ok check (see site 2): a JSON error
 *    body resolved and simply skipped the normalized write-back. Same DEV
 *    notes as site 2.
 */
window.ACPClient = window.ACPClient || {};
ACPClient.wizardEndpoints = (function () {
  "use strict";

  var adapters = { v1: null, direct: null };
  var baseUrl = "";

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.direct === "function") adapters.direct = deps.direct;
    if (typeof deps.baseUrl === "string") baseUrl = deps.baseUrl;
  }

  /** Uniform promise front over the injected direct adapter. */
  function callDirect(url, opts) {
    var impl = adapters.direct;
    if (typeof impl !== "function") {
      return Promise.reject(new Error("wizardEndpoints: init({ direct }) missing"));
    }
    try {
      return Promise.resolve(impl(url, opts));
    } catch (e) {
      return Promise.reject(e);
    }
  }

  /** Error carrying the transport's additive status/body contract. */
  function httpError(message, source) {
    var err = new Error(message);
    if (source && typeof source.status === "number") err.status = source.status;
    if (source && source.body !== undefined) err.body = source.body;
    return err;
  }

  function abortError() {
    var err = new Error("Aborted");
    err.name = "AbortError";
    return err;
  }

  /** Shared by the two legacy "no resp.ok check" sites (structures/parse,
   * validate-method): a non-2xx response whose body parsed as JSON resolves
   * with that body exactly like the legacy `await resp.json()` did. */
  function jsonErrorBodyOrThrow(err) {
    if (err && typeof err.status === "number" && err.body !== undefined) return err.body;
    throw err;
  }

  function preAborted(opts) {
    return !!(opts && opts.signal && opts.signal.aborted);
  }

  // 1. POST {API_BASE}/uploads — structure upload (FormData passthrough).
  function uploadStructure(formData) {
    return callDirect(baseUrl + "/uploads", {
      method: "POST",
      body: formData,
      timeoutMs: 0,
    }).catch(function (err) {
      if (err && typeof err.status === "number") {
        // Legacy showed `errBody.detail || "Upload failed"` with errBody =
        // parsed error JSON (or {detail: statusText}). Re-derive the RAW
        // detail (may be non-string) onto err.detail; the "Upload failed"
        // literal stays at the call site, exactly like legacy.
        var out = httpError("Upload failed", err);
        out.detail = (err.body && err.body.detail !== undefined) ? err.body.detail : undefined;
        throw out;
      }
      throw err;
    });
  }

  // 2. POST {API_BASE}/structures/parse — legacy had NO resp.ok check.
  function parseStructure(payload) {
    return callDirect(baseUrl + "/structures/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: payload.content,
        format: payload.format,
        filename: payload.filename,
      }),
      timeoutMs: 0,
    }).catch(jsonErrorBodyOrThrow);
  }

  // 3. GET {API_BASE}/structure-sources/<id> — plain "HTTP <status>" errors.
  function fetchStructureSource(sourceId, opts) {
    if (preAborted(opts)) return Promise.reject(abortError());
    var reqOpts = { timeoutMs: 0 };
    if (opts && opts.signal) reqOpts.signal = opts.signal;
    return callDirect(baseUrl + "/structure-sources/" + encodeURIComponent(sourceId), reqOpts)
      .catch(function (err) {
        // Legacy threw `new Error("HTTP " + response.status)` for every
        // non-2xx and never read the body — force the same message even when
        // the transport flattened a FastAPI detail into err.message.
        if (err && typeof err.status === "number") {
          throw httpError("HTTP " + err.status, err);
        }
        throw err;
      });
  }

  // 4. POST {API_BASE}/uploads?parse=false&project_id=<id> — NMR Bruker file.
  function uploadNmrBruker(formData, projectId) {
    return callDirect(
      baseUrl + "/uploads?parse=false&project_id=" + encodeURIComponent(projectId),
      { method: "POST", body: formData, timeoutMs: 0 }
    ).catch(function (err) {
      // Legacy: `new Error(body.detail || resp.statusText)` over the parsed
      // error body (defaulting to {}). DEV: the statusText fallback becomes
      // "HTTP <status>" when the transport has no parsed JSON body.
      if (err && typeof err.status === "number") {
        throw httpError((err.body && err.body.detail) || ("HTTP " + err.status), err);
      }
      throw err;
    });
  }

  // 5. POST /api/v1/hessian-preview — literal legacy path; error text comes
  // from the error body's errors[].message list, not FastAPI detail.
  function hessianPreview(payload, opts) {
    if (preAborted(opts)) return Promise.reject(abortError());
    var reqOpts = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeoutMs: 0,
    };
    if (opts && opts.signal) reqOpts.signal = opts.signal;
    return callDirect("/api/v1/hessian-preview", reqOpts).catch(function (err) {
      if (err && typeof err.status === "number") {
        throw httpError(hessianErrorMessage(err), err);
      }
      throw err;
    });
  }

  /** Legacy lambda, verbatim: `(e.errors || []).map(x => x.message)
   * .join("; ") || ("HTTP " + status)`. */
  function hessianErrorMessage(err) {
    var body = (err && err.body) || {};
    var msgs = (body.errors || []).map(function (x) { return x.message; }).join("; ");
    return msgs || ("HTTP " + err.status);
  }

  // 6. POST /api/v1/batch-optimize/config-preview — literal legacy path;
  // always "HTTP <status>"; caller AbortController signal forwarded as-is.
  function batchConfigPreview(payload, opts) {
    if (preAborted(opts)) return Promise.reject(abortError());
    var reqOpts = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeoutMs: 0,
    };
    if (opts && opts.signal) reqOpts.signal = opts.signal;
    return callDirect("/api/v1/batch-optimize/config-preview", reqOpts).catch(function (err) {
      if (err && typeof err.status === "number") {
        throw httpError("HTTP " + err.status, err);
      }
      throw err;
    });
  }

  // 7. POST {API_BASE}/validate-method — legacy had NO resp.ok check.
  function validateMethod(payload) {
    return callDirect(baseUrl + "/validate-method", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ schema_id: payload.schema_id, levels: payload.levels }),
      timeoutMs: 0,
    }).catch(jsonErrorBodyOrThrow);
  }

  return {
    init: init,
    uploadStructure: uploadStructure,
    parseStructure: parseStructure,
    fetchStructureSource: fetchStructureSource,
    uploadNmrBruker: uploadNmrBruker,
    hessianPreview: hessianPreview,
    batchConfigPreview: batchConfigPreview,
    validateMethod: validateMethod,
  };
})();
