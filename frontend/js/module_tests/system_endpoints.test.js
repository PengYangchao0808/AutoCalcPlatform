/*
 * Behavior tests for the Client system/resource endpoint facade (T22).
 *
 * Pins:
 *  - exactly one request path per endpoint: GET /nodes and
 *    GET /software/discovery through the v1 wrapper (API_BASE applied),
 *    GET /api/backends through the literal-path transport adapter;
 *  - URL/query shape unchanged from the legacy sites;
 *  - errors/timeouts keep the transport contract (message/status/body);
 *  - a missing adapter rejects instead of silently falling back to fetch().
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadFacade() {
    const sandbox = makeSandbox({});
    loadScript("js/client/transport.js", sandbox);
    loadScript("js/client/system-endpoints.js", sandbox);
    const C = sandbox.window.ACPClient;
    const calls = [];
    C.systemEndpoints.init({
      v1: (path, opts) => { calls.push(["v1", path, opts]); return Promise.resolve({ via: "v1", path: path }); },
      direct: (url, opts) => { calls.push(["direct", url, opts]); return Promise.resolve({ via: "direct", path: url }); },
      baseUrl: "/api/v1",
    });
    return { sandbox, C, ep: C.systemEndpoints, calls };
  }

  function fakeResponse(status, body, contentType) {
    return {
      ok: status >= 200 && status < 300,
      status: status,
      headers: { get: () => contentType || "application/json" },
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(String(body)),
    };
  }

  await test("fetchNodes: one GET through v1 at /nodes", async () => {
    const { ep, calls } = loadFacade();
    const result = await ep.fetchNodes();
    assertEq(calls.length, 1, "exactly one request path");
    assertEq(calls[0][0], "v1");
    assertEq(calls[0][1], "/nodes", "v1 owns API_BASE; path is /nodes");
    assertEq(result, { via: "v1", path: "/nodes" });
  });

  await test("fetchBackends: literal /api/backends via the transport adapter", async () => {
    const { ep, calls } = loadFacade();
    await ep.fetchBackends();
    assertEq(calls, [["direct", "/api/backends", undefined]]);
  });

  await test("fetchSoftwareDiscovery: one GET through v1 at /software/discovery", async () => {
    const { ep, calls } = loadFacade();
    await ep.fetchSoftwareDiscovery();
    assertEq(calls, [["v1", "/software/discovery", undefined]]);
  });

  await test("caller opts (signal) pass through verbatim", async () => {
    const { ep, calls } = loadFacade();
    const signal = {};
    await ep.fetchNodes({ signal });
    assertEq(calls[0][2], { signal });
  });

  await test("non-2xx keeps the transport contract (message + status + body)", async () => {
    const sandbox = makeSandbox({
      fetch: () => Promise.resolve(fakeResponse(503, { detail: "nodes unavailable" })),
    });
    loadScript("js/client/transport.js", sandbox);
    loadScript("js/client/system-endpoints.js", sandbox);
    const C = sandbox.window.ACPClient;
    C.transport.init({ defaultTimeoutMs: 8000, remoteTimeoutMs: 60000, translate: (k) => k });
    C.systemEndpoints.init({
      v1: (path, opts) => C.transport.request("/api/v1" + path, opts),
      direct: (url, opts) => C.transport.request(url, opts),
    });
    let caught = null;
    await C.systemEndpoints.fetchNodes().catch((e) => { caught = e; });
    assert(caught instanceof Error, "rejects like the transport");
    assertEq(caught.message, "nodes unavailable");
    assertEq(caught.status, 503);
    assertEq(caught.body, { detail: "nodes unavailable" });
  });

  await test("uninitialized facade rejects; never falls back to global fetch", async () => {
    const sandbox = makeSandbox({
      fetch: () => { throw new Error("bare fetch must not be used"); },
    });
    loadScript("js/client/system-endpoints.js", sandbox);
    let caught = null;
    await sandbox.window.ACPClient.systemEndpoints.fetchBackends().catch((e) => { caught = e; });
    assert(caught && /systemEndpoints: init/.test(caught.message));
  });
};
