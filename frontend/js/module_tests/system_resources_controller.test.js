/*
 * Behavior tests for the System & Resources use-case controller (T22).
 *
 * Pins:
 *  - single cache owner: loadNodes/loadBackends write nodesCache /
 *    backendsCache / softwareDiscoveryCache, and panel rendering reads them;
 *  - request orchestration goes ONLY through the injected Client facade
 *    (a poisoned global fetch is never touched);
 *  - /nodes failure presentation is preserved: 503 renders `.node-error`
 *    with "nodes.load_error: HTTP 503" (not a silent success), "not
 *    configured" renders the muted empty state;
 *  - /api/backends failure renders backend.load_error and skips discovery;
 *    discovery failure is silent and the list still renders;
 *  - retry (second load) clears the stored error and re-renders.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function makeEl(id) {
    return {
      id: id || "", className: "", textContent: "", innerHTML: "",
      title: "", style: {}, children: [],
      appendChild(c) { this.children.push(c); return c; },
      addEventListener(type, fn) { this["on" + type] = fn; },
      setAttribute() {},
      removeAttribute() {},
      querySelectorAll() { return []; },
      querySelector() { return null; },
    };
  }

  function build(opts) {
    opts = opts || {};
    const els = {
      "sysres-node-list": makeEl("sysres-node-list"),
      "sysres-backend-list": makeEl("sysres-backend-list"),
    };
    if (opts.withoutNodeList) delete els["sysres-node-list"];
    if (opts.withoutBackendList) delete els["sysres-backend-list"];

    const calls = [];
    const pings = [];
    let nodesImpl = () => Promise.resolve({ nodes: [] });
    let backendsImpl = () => Promise.resolve({ backends: [] });
    let discoveryImpl = () => Promise.resolve({ software: [] });

    const client = {
      systemEndpoints: {
        fetchNodes: (...a) => { calls.push("nodes"); return nodesImpl(...a); },
        fetchBackends: (...a) => { calls.push("backends"); return backendsImpl(...a); },
        fetchSoftwareDiscovery: (...a) => { calls.push("discovery"); return discoveryImpl(...a); },
      },
    };

    let bareFetchCalls = 0;
    const sandbox = makeSandbox({
      document: {
        getElementById: (id) => els[id] || null,
        createElement: () => makeEl(),
        querySelectorAll: () => [],
      },
      nodesCache: [],
      backendsCache: [],
      softwareDiscoveryCache: {},
      fetch: () => { bareFetchCalls++; throw new Error("controller must not fetch directly"); },
    });
    loadScript("js/workbench/controllers/system_resources_controller.js", sandbox);
    const controller = sandbox.window.ACPWorkbench.controllers.systemResources;
    controller.init({
      client: client,
      document: sandbox.document,
      escapeHtml: (v) => String(v == null ? "" : v),
      translate: (k) => k,
      onPingNode: (name) => { pings.push(name); },
    });

    return {
      sandbox, controller, els, calls, pings,
      setNodes(impl) { nodesImpl = impl; },
      setBackends(impl) { backendsImpl = impl; },
      setDiscovery(impl) { discoveryImpl = impl; },
      bareFetchCalls: () => bareFetchCalls,
    };
  }

  await test("loadNodes: writes nodesCache and renders rows from it", async () => {
    const ctx = build();
    ctx.setNodes(() => Promise.resolve({
      nodes: [{ name: "n1", host: "h1", status: "online", running_jobs: 1, max_jobs: 4, disk_usage_pct: 10 }],
    }));
    await ctx.controller.loadNodes();
    assertEq(ctx.sandbox.nodesCache.length, 1, "cache written by the controller");
    const list = ctx.els["sysres-node-list"];
    assertEq(list.children.length, 1, "one row rendered");
    const info = list.children[0].children[1];
    assertEq(info.children[0].textContent, "n1  h1");
    assertEq(info.children[1].textContent, "nodes.status.online · 1/4 · DISK 10%");
    assertEq(ctx.controller.getState().nodesError, null);
    assertEq(ctx.bareFetchCalls(), 0, "no direct fetch");
  });

  await test("loadNodes 503: existing error presentation preserved, cache cleared, no silent success", async () => {
    const ctx = build();
    ctx.setNodes(() => Promise.reject(new Error("HTTP 503")));
    const result = await ctx.controller.loadNodes();
    assertEq(result, undefined, "legacy swallowed the failure (resolves)");
    const list = ctx.els["sysres-node-list"];
    assertEq(ctx.sandbox.nodesCache, [], "stale nodes dropped");
    assert(list.innerHTML.indexOf("node-error") >= 0, "error state rendered");
    assert(list.innerHTML.indexOf("nodes.load_error: HTTP 503") >= 0, "existing error text preserved");
    assertEq(ctx.controller.getState().nodesError.message, "HTTP 503");
  });

  await test("loadNodes 'not configured' keeps the muted empty state", async () => {
    const ctx = build();
    ctx.setNodes(() => Promise.reject(new Error("Remote nodes not configured")));
    await ctx.controller.loadNodes();
    const html = ctx.els["sysres-node-list"].innerHTML;
    assert(html.indexOf("nodes.empty") >= 0, "muted empty state");
    assert(html.indexOf("node-error") < 0, "not the error state");
  });

  await test("loadNodes: no request while the panel element is absent", async () => {
    const ctx = build({ withoutNodeList: true });
    await ctx.controller.loadNodes();
    assertEq(ctx.calls, [], "legacy early return preserved");
  });

  await test("loadBackends: cache + discovery (version/path) and single request order", async () => {
    const ctx = build();
    ctx.setBackends(() => Promise.resolve({
      backends: [{ name: "orca", available: true, path: "/opt/orca" }],
    }));
    ctx.setDiscovery(() => Promise.resolve({
      software: [{ name: "orca", version: "5.0.4", resolved: "/opt/orca" }],
    }));
    await ctx.controller.loadBackends();
    assertEq(ctx.sandbox.backendsCache.length, 1);
    assertEq(ctx.sandbox.softwareDiscoveryCache.orca.version, "5.0.4");
    assertEq(ctx.calls, ["backends", "discovery"], "backends then discovery");
    const info = ctx.els["sysres-backend-list"].children[0].children[1];
    const nameEl = info.children[0];
    assert(nameEl.children.some((c) => c.className === "backend-version" && c.textContent === "5.0.4"),
      "version badge from discovery");
    assertEq(info.children[1].textContent, "/opt/orca", "resolved path rendered");
  });

  await test("loadBackends: multi-install disclosure renders candidates + hint", async () => {
    const ctx = build();
    ctx.setBackends(() => Promise.resolve({
      backends: [{ name: "orca", available: true, path: "/b" }],
    }));
    ctx.setDiscovery(() => Promise.resolve({
      software: [{
        name: "orca", multiple: true, resolved: "/b",
        candidates: [{ path: "/a", version: "1", source: "env" }, { path: "/b", version: "2" }],
      }],
    }));
    await ctx.controller.loadBackends();
    const nameEl = ctx.els["sysres-backend-list"].children[0].children[1].children[0];
    const details = nameEl.children.find((c) => c.className === "backend-multi");
    assert(details, "multi-install <details> present");
    const body = details.children[1];
    assertEq(body.children.length, 3, "two candidates + hint");
    assertEq(body.children[2].className, "backend-multi-hint");
  });

  await test("loadBackends failure: backend.load_error and discovery skipped", async () => {
    const ctx = build();
    ctx.setBackends(() => Promise.reject(new Error("HTTP 500")));
    await ctx.controller.loadBackends();
    assertEq(ctx.calls, ["backends"], "no discovery after backend failure");
    assertEq(ctx.sandbox.backendsCache, []);
    assert(ctx.els["sysres-backend-list"].innerHTML.indexOf("backend.load_error") >= 0);
  });

  await test("loadBackends discovery failure is silent; backends still render", async () => {
    const ctx = build();
    ctx.setBackends(() => Promise.resolve({ backends: [{ name: "xtb", available: true }] }));
    ctx.setDiscovery(() => Promise.reject(new Error("HTTP 404")));
    await ctx.controller.loadBackends();
    assertEq(ctx.calls, ["backends", "discovery"]);
    assertEq(ctx.els["sysres-backend-list"].children.length, 1, "backends rendered");
    assertEq(ctx.controller.getState().discoveryFailed, true);
  });

  await test("retry: second loadNodes clears the stored error and re-renders", async () => {
    const ctx = build();
    ctx.setNodes(() => Promise.reject(new Error("HTTP 503")));
    await ctx.controller.loadNodes();
    assert(ctx.controller.getState().nodesError, "error stored");
    ctx.setNodes(() => Promise.resolve({ nodes: [{ name: "n2", host: "h2", status: "online" }] }));
    await ctx.controller.loadNodes();
    assertEq(ctx.controller.getState().nodesError, null);
    assertEq(ctx.els["sysres-node-list"].innerHTML, "", "old error markup cleared by re-render");
    assertEq(ctx.els["sysres-node-list"].children.length, 1);
  });

  await test("ping button delegates to the injected onPingNode callback", async () => {
    const ctx = build();
    ctx.setNodes(() => Promise.resolve({ nodes: [{ name: "n9", host: "h9", status: "online" }] }));
    await ctx.controller.loadNodes();
    const actions = ctx.els["sysres-node-list"].children[0].children[2];
    actions.children[0].onclick({ stopPropagation() {} });
    assertEq(ctx.pings, ["n9"]);
  });
};
