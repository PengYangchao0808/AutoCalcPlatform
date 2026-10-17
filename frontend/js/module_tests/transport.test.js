/*
 * Behavior tests for frontend/js/client/transport.js (OC-1 audit).
 * Verifies the module is behavior-equivalent to the legacy inline apiRequest:
 * timeout/cancel split, error flattening, JSON/text/FormData passthrough.
 */
"use strict";

function makeFetchStub(handler) {
  return function fetchStub(url, opts) {
    return Promise.resolve().then(() => handler(url, opts));
  };
}

function jsonResponse(status, body, headers) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => ((headers || {})[k.toLowerCase()]) || null },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

function textResponse(status, text, contentType) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => (k.toLowerCase() === "content-type" ? contentType : null) },
    json: () => Promise.reject(new Error("not json")),
    text: () => Promise.resolve(text),
  };
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadTransport(fetchStub, translate) {
    const sandbox = makeSandbox({ fetch: fetchStub });
    loadScript("js/client/transport.js", sandbox);
    const client = sandbox.window.ACPClient;
    if (!client || !client.transport) throw new Error("ACPClient.transport missing");
    client.transport.init({
      defaultTimeoutMs: 8000,
      remoteTimeoutMs: 60000,
      translate: translate || ((key, vars) => key + ":" + JSON.stringify(vars || {})),
    });
    return client.transport;
  }

  await test("registers public API only (init/request/remote), no auto network", () => {
    const calls = [];
    const t = loadTransport(makeFetchStub(() => { calls.push(1); return jsonResponse(200, {}); }));
    assertEq(Object.keys(t).sort(), ["init", "remote", "request"]);
    assertEq(calls.length, 0, "module must not issue requests at load/init");
  });

  await test("JSON response parsed by content-type", async () => {
    const t = loadTransport(makeFetchStub(() => jsonResponse(200, { hello: 1 }, { "content-type": "application/json" })));
    const out = await t.request("/api/x");
    assertEq(out, { hello: 1 });
  });

  await test("text response returned verbatim (non-JSON content-type)", async () => {
    const t = loadTransport(makeFetchStub(() => textResponse(200, "plain-body", "text/plain")));
    const out = await t.request("/files/x.log");
    assertEq(out, "plain-body");
  });

  await test("FormData body passed through untouched", async () => {
    let seenOpts = null;
    const t = loadTransport(makeFetchStub((url, opts) => { seenOpts = opts; return jsonResponse(200, {}); }));
    const fd = { __formDataStub: true };
    await t.request("/upload", { method: "POST", body: fd });
    assertEq(seenOpts.body, { __formDataStub: true }, "FormData body must not be transformed");
  });

  await test("string detail error message preserved", async () => {
    const t = loadTransport(makeFetchStub(() => jsonResponse(409, { detail: "name revision conflict" })));
    let err = null;
    await t.request("/api/v2/tasks/1").catch((e) => { err = e; });
    assert(err && err.message === "name revision conflict", "got: " + (err && err.message));
  });

  await test("FastAPI 422 validation array flattened to loc: msg; ...", async () => {
    const t = loadTransport(makeFetchStub(() => jsonResponse(422, { detail: [
      { loc: ["body", "name"], msg: "field required", type: "missing" },
      { loc: ["body", "spec"], msg: "not a valid workflow", type: "value_error" },
    ] })));
    let err = null;
    await t.request("/jobs").catch((e) => { err = e; });
    assertEq(err.message, "body.name: field required; body.spec: not a valid workflow");
  });

  await test("object detail stringified; missing detail keeps HTTP status", async () => {
    const t1 = loadTransport(makeFetchStub(() => jsonResponse(500, { detail: { code: 7 } })));
    let e1 = null; await t1.request("/x").catch((e) => { e1 = e; });
    assertEq(e1.message, '{"code":7}');

    const t2 = loadTransport(makeFetchStub(() => jsonResponse(503, { nope: 1 })));
    let e2 = null; await t2.request("/x").catch((e) => { e2 = e; });
    assertEq(e2.message, "HTTP 503");
  });

  await test("own timeout converts to translated Error, never raw AbortError", async () => {
    const sandbox = makeSandbox({
      fetch: (url, opts) => new Promise((resolve, reject) => {
        opts.signal.addEventListener("abort", () => {
          const e = new Error("aborted");
          e.name = "AbortError";
          reject(e);
        });
      }),
    });
    // immediate timeout fire
    sandbox.setTimeout = (fn) => { setImmediate(fn); return 1; };
    sandbox.clearTimeout = () => {};
    loadScript("js/client/transport.js", sandbox);
    const tr = sandbox.window.ACPClient.transport;
    tr.init({ defaultTimeoutMs: 8000, translate: (k, v) => "T[" + k + "|" + v.seconds + "]" });
    let err = null;
    await tr.request("/slow").catch((e) => { err = e; });
    assert(err && err.message === "T[api.timeout|8]", "got: " + (err && err.message));
    assert(err.name !== "AbortError", "own timeout must not leak AbortError");
  });

  await test("caller signal abort rethrows original AbortError name", async () => {
    // Real fetch consumes opts.signal synchronously at call time; the stub must
    // attach its listener synchronously too (an aborted-earlier signal must
    // reject immediately), mirroring browser fetch semantics.
    const t = loadTransport(function (url, opts) {
      return new Promise((resolve, reject) => {
        const abortNow = () => {
          const e = new Error("The operation was aborted");
          e.name = "AbortError";
          reject(e);
        };
        if (opts.signal.aborted) { abortNow(); return; }
        opts.signal.addEventListener("abort", abortNow);
      });
    });
    const ac = new AbortController();
    const p = t.request("/jobs/1", { signal: ac.signal }).catch((e) => e);
    ac.abort();
    const err = await p;
    assertEq(err.name, "AbortError", "caller abort keeps AbortError for silent-cancel filters");
  });

  await test("timeoutMs override + remote() applies 60s budget without touching URL", async () => {
    let seenOpts = null;
    const t = loadTransport(makeFetchStub((url, opts) => { seenOpts = { url, opts: Object.assign({}, opts) }; return jsonResponse(200, {}); }));
    await t.remote("/api/v2/results?job_id=1");
    // remote passes URL through; the caller (apiV2Remote wrapper) owns prefixes.
    assertEq(seenOpts.url, "/api/v2/results?job_id=1");
    assertEq(seenOpts.opts.timeoutMs, undefined, "timeoutMs must be consumed before fetch");
    assert(typeof seenOpts.opts.signal === "object" && seenOpts.opts.signal !== null,
      "own timeout signal injected (same as legacy apiRequest)");

    let seen2 = null;
    const t2 = loadTransport(makeFetchStub((url, opts) => { seen2 = Object.assign({}, opts); return jsonResponse(200, {}); }));
    await t2.request("/x", { timeoutMs: 250 });
    assertEq(seen2.timeoutMs, undefined, "explicit timeoutMs consumed");
  });

  // --- FE-07 additive error contract: err.status + err.body ---

  await test("HTTP errors carry err.status and parsed err.body (string detail)", async () => {
    const t = loadTransport(makeFetchStub(() => jsonResponse(409, { detail: "name revision conflict", current: { revision: 3 } })));
    let err = null;
    await t.request("/api/v2/x").catch((e) => { err = e; });
    assert(err && typeof err.status === "number", "err.status must be attached");
    assertEq(err.status, 409);
    assertEq(err.body, { detail: "name revision conflict", current: { revision: 3 } });
    assertEq(err.message, "name revision conflict", "message text unchanged by additive contract");
  });

  await test("422 validation array errors carry status + structured body", async () => {
    const detail = [
      { loc: ["body", "name"], msg: "field required", type: "missing" },
      { loc: ["body", "spec"], msg: "bad workflow", type: "value_error" },
    ];
    const t = loadTransport(makeFetchStub(() => jsonResponse(422, { detail })));
    let err = null;
    await t.request("/x").catch((e) => { err = e; });
    assertEq(err.status, 422);
    assertEq(err.body.detail, detail, "parsed body object attached");
    assertEq(err.message, "body.name: field required; body.spec: bad workflow");
  });

  await test("non-JSON error body: status attached, body undefined, message keeps HTTP status", async () => {
    const t = loadTransport(makeFetchStub(() => textResponse(500, "boom", "text/plain")));
    let err = null;
    await t.request("/x").catch((e) => { err = e; });
    assertEq(err.status, 500);
    assertEq(err.body, undefined, "unparseable body stays undefined (json() rejects)");
    assertEq(err.message, "HTTP 500");
  });

  // --- timeoutMs: 0 = explicit disable (wizard candidateApi no-budget contract) ---

  await test("timeoutMs: 0 disables own timeout — no wrapper signal, caller signal passes through as-is", async () => {
    let seenOpts = null;
    const t = loadTransport(function (url, opts) {
      seenOpts = opts;
      // Resolve after a macrotask: a regression treating 0 as "set timer"
      // would schedule setTimeout(fn, 0) and abort before this fires.
      return new Promise((resolve) => {
        setTimeout(() => resolve(jsonResponse(200, { ok: 1 }, { "content-type": "application/json" })), 15);
      });
    });
    const ac = new AbortController();
    const out = await t.request("/api/v2/structure-sources?limit=500", { timeoutMs: 0, signal: ac.signal });
    assertEq(out, { ok: 1 }, "request completes despite macrotask delay (no 0ms own timer)");
    assert(seenOpts.signal === ac.signal,
      "caller signal must be forwarded as-is (no wrapper AbortController injected)");
    assertEq(seenOpts.timeoutMs, undefined, "timeoutMs consumed before fetch");
  });

  // --- opts.fetchImpl injection seam ---

  await test("opts.fetchImpl overrides global fetch and is stripped before fetch", async () => {
    let globalCalled = false;
    const t = loadTransport(makeFetchStub(() => { globalCalled = true; return jsonResponse(200, {}); }));
    let seenUrl = null;
    let seenOpts = null;
    const injected = (url, opts) => {
      seenUrl = url;
      seenOpts = Object.assign({}, opts);
      return Promise.resolve(jsonResponse(200, { via: "injected" }, { "content-type": "application/json" }));
    };
    const out = await t.request("/api/v2/picker?x=1", { fetchImpl: injected });
    assertEq(globalCalled, false, "global fetch must not run when fetchImpl injected");
    assertEq(seenUrl, "/api/v2/picker?x=1");
    assertEq(seenOpts.fetchImpl, undefined, "fetchImpl must be deleted before fetch");
    assertEq(seenOpts.timeoutMs, undefined, "timeoutMs still consumed on injected path");
    assertEq(out, { via: "injected" }, "JSON body parsed on injected path");
  });

  await test("caller signal abort through injected fetchImpl keeps AbortError name", async () => {
    const t = loadTransport(makeFetchStub(() => { throw new Error("global fetch must not be called"); }));
    const ac = new AbortController();
    const p = t.request("/jobs/1", {
      fetchImpl: function (url, opts) {
        return new Promise((resolve, reject) => {
          const abortNow = () => {
            const e = new Error("The operation was aborted");
            e.name = "AbortError";
            reject(e);
          };
          if (opts.signal.aborted) { abortNow(); return; }
          opts.signal.addEventListener("abort", abortNow);
        });
      },
      signal: ac.signal,
    }).catch((e) => e);
    ac.abort();
    const err = await p;
    assertEq(err.name, "AbortError", "caller abort keeps AbortError for silent-cancel filters");
  });
};
