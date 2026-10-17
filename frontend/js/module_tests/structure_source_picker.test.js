/*
 * Behavior tests for frontend/js/structure_source_picker.js request layer
 * (FE-07): _apiV2/_apiV1 delegate to ACPClient.transport.request with the
 * /api/v2 + /api/v1 prefixes, the 8s budget, and the test-overridable
 * _fetchImpl passed through opts.fetchImpl. Pins the unified error contract:
 * err.status + err.body attached, 422 flattening (v2 with loc, v1 legacy
 * text without loc), caller-abort AbortError preservation.
 */
"use strict";

function jsonResponse(status, body, headers) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => ((headers || {})[k.toLowerCase()]) || null },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadPicker(fetchStub) {
    const sandbox = makeSandbox({
      fetch: fetchStub,
      // Minimal DOM stub: only _esc touches document (rendering paths are not
      // exercised here).
      document: {
        createElement: () => ({
          appendChild: () => {},
          set innerHTML(_v) {},
          get innerHTML() { return ""; },
        }),
        createTextNode: (s) => ({ text: s }),
      },
      localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    });
    // Load order mirrors the workbench HTML (picker before transport); the
    // delegation must resolve ACPClient lazily at call time.
    loadScript("js/structure_source_picker.js", sandbox);
    loadScript("js/client/transport.js", sandbox);
    const picker = sandbox.window.ACPSourcePicker;
    const client = sandbox.window.ACPClient;
    if (!picker || !client || !client.transport) throw new Error("picker/transport namespaces missing");
    client.transport.init({
      defaultTimeoutMs: 8000,
      remoteTimeoutMs: 60000,
      translate: (key, vars) => key + ":" + JSON.stringify(vars || {}),
    });
    return { sandbox, picker, transport: client.transport };
  }

  await test("picker namespace exposes request layer for tests", () => {
    const { picker } = loadPicker(() => jsonResponse(200, {}));
    assertEq(typeof picker._apiV2, "function");
    assertEq(typeof picker._apiV1, "function");
    assertEq(typeof picker._fetchImpl, "function");
  });

  await test("_apiV2 delegates to transport: /api/v2 prefix, 8s budget, _fetchImpl passthrough", async () => {
    let seen = null;
    const { picker } = loadPicker(() => { throw new Error("global fetch must not be called"); });
    // Marker: the module-level _fetchImpl (set via the test setter) must reach
    // the transport as opts.fetchImpl — the global fetch above stays untouched.
    picker._fetchImpl = (url, opts) => {
      seen = { url, opts: Object.assign({}, opts) };
      return Promise.resolve(jsonResponse(200, { items: [1] }, { "content-type": "application/json" }));
    };
    const body = await picker._apiV2("/structure-sources?limit=1");
    assertEq(body, { items: [1] });
    assertEq(seen.url, "/api/v2/structure-sources?limit=1", "prefix owned by picker");
    assertEq(seen.opts.fetchImpl, undefined, "fetchImpl stripped before fetch");
    assertEq(seen.opts.timeoutMs, undefined, "8s budget consumed by transport");
    assert(seen.opts.signal && typeof seen.opts.signal.addEventListener === "function",
      "own 8s AbortController signal present");
  });

  await test("_apiV2 keeps unified error contract: message flattening + err.status + err.body", async () => {
    const { picker } = loadPicker(() => Promise.resolve(jsonResponse(409, {
      detail: { current: { metadata_revision: 7 } },
    })));
    let err = null;
    await picker._apiV2("/structure-sources/ss_x/metadata", { method: "PATCH" }).catch((e) => { err = e; });
    assertEq(err.status, 409, "err.status attached (rename conflict branch)");
    assertEq(err.body, { detail: { current: { metadata_revision: 7 } } }, "err.body attached");
    assertEq(err.message, '{"current":{"metadata_revision":7}}');
  });

  await test("_apiV1 keeps legacy 422 array message WITHOUT loc prefix", async () => {
    const { picker } = loadPicker(() => Promise.resolve(jsonResponse(422, { detail: [
      { loc: ["query", "limit"], msg: "not a valid integer", type: "type_error" },
    ] })));
    let err = null;
    await picker._apiV1("/structure-sources/recent?limit=x").catch((e) => { err = e; });
    assertEq(err.message, "not a valid integer", "pre-migration v1 text (no loc prefix)");
    assertEq(err.status, 422, "status attached (new unified contract)");
    assert(Array.isArray(err.body.detail), "body attached (new unified contract)");
  });

  await test("_apiV2 preserves caller-signal AbortError through injected _fetchImpl", async () => {
    const { picker } = loadPicker(() => { throw new Error("global fetch must not be called"); });
    picker._fetchImpl = function (url, opts) {
      return new Promise((resolve, reject) => {
        const abortNow = () => {
          const e = new Error("The operation was aborted");
          e.name = "AbortError";
          reject(e);
        };
        if (opts.signal.aborted) { abortNow(); return; }
        opts.signal.addEventListener("abort", abortNow);
      });
    };
    const ac = new AbortController();
    const p = picker._apiV2("/structure-sources", { signal: ac.signal }).catch((e) => e);
    ac.abort();
    const err = await p;
    assertEq(err.name, "AbortError", "silent-cancel filters keep working after migration");
  });
};
