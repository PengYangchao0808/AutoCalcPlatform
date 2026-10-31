/* ACP 3.0 module test — node-status (A-30 / A-31).
 *
 * Loads the module as the classic script the Shell loader consumes
 * (window.ACPNextModules["node-status"] = { descriptor, factory }) inside the
 * zero-dependency node vm harness with a fake DOM. Every request goes through
 * an injected adapter (`deps.fetch` or `deps.request` / `deps.client`), so no
 * test can reach the network: the sandbox has no fetch at all.
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four states — loading while the initial requests are in flight, ready
 *     with node/backend/software rows, empty for the v1 "not configured" 503,
 *     error for a real failure (with a working retry);
 *  3. per-node ping/bootstrap: POST to the existing command routes only, busy
 *     state + disabled buttons, clear zh failure text, stderr tail on failure;
 *  4. A-31 honesty: unprobed software/backends render 未知/未发现 and never a
 *     guessed version or path;
 *  5. generation/token guards: dispose and a newer refresh drop late results;
 *  6. dispose is idempotent and removes DOM, listeners and the bootstrap
 *     elapsed-time timer.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/node-status/module.js";
  const API = "/api/v1";

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 10) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 4; i++) await Promise.resolve();
  }

  // ------------------------------------------------------------ fake DOM --

  function makeDom() {
    const docListeners = new Map();

    function makeEl(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        hidden: false,
        disabled: false,
        attrs: {},
        listeners,
        setAttribute(key, value) { this.attrs[key] = String(value); },
        getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; },
        removeAttribute(key) { delete this.attrs[key]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        replaceChildren(...nodes) {
          this.children.forEach((child) => { child.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((node) => { node.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, event) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: this }, event || {})));
        }
      };
    }

    const document = {
      createElement: makeEl,
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let count = 0;
        docListeners.forEach((set) => { count += set.size; });
        return count;
      }
    };
    return { document };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((child) => { out.push(child); walk(child, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((node) => node.getAttribute && node.getAttribute(attr) === value) || null;
  }

  function findAll(root, attr, value) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) === value);
  }

  function findNodeAction(root, nodeName, action) {
    return walk(root).find((node) => node.getAttribute &&
      node.getAttribute("data-node") === nodeName &&
      node.getAttribute("data-action") === action) || null;
  }

  function subtreeText(node) {
    let text = node.textContent || "";
    (node.children || []).forEach((child) => { text += " " + subtreeText(child); });
    return text;
  }

  function trackTimers(sandbox) {
    const timers = { active: new Set(), started: 0, cleared: 0 };
    sandbox.setInterval = (fn) => {
      timers.started += 1;
      const id = timers.started;
      if (timers.active) timers.active.add(id);
      return id;
    };
    sandbox.clearInterval = (id) => {
      timers.cleared += 1;
      timers.active.delete(id);
    };
    return timers;
  }

  // ------------------------------------------------------- fixture payloads --

  const NODES = {
    auto_select: true,
    nodes: [
      {
        name: "alpha", host: "10.0.0.11", status: "online", running_jobs: 1, max_jobs: 4,
        disk_usage_pct: 62, last_check: "2026-10-31T10:00:00", error: null, queue: "normal",
        software: { orca: { resolved: "/opt/orca/orca", version: "6.0.0" } },
        capability_state: "declared", declared_ok: true, mismatch: [], probe_note: null
      },
      {
        name: "beta", host: "10.0.0.12", status: "degraded", running_jobs: 4, max_jobs: 4,
        disk_usage_pct: 91, last_check: "", error: "load high", queue: null,
        software: {},
        capability_state: "probe-inferred", declared_ok: null, mismatch: [], probe_note: "ssh ok"
      }
    ]
  };

  const BACKENDS = {
    backends: [
      {
        name: "orca", available: true, path: "/opt/orca/orca", version: "6.0.0",
        capabilities: [
          { name: "opt", available: true },
          { name: "freq", available: false }
        ],
        remote_available: true, remote_nodes: ["alpha"],
        remote_path: "/opt/orca/orca", remote_version: "6.0.0"
      },
      {
        name: "xtb", available: false, path: "", version: "",
        capabilities: [], remote_available: false, remote_nodes: [],
        remote_path: "", remote_version: ""
      }
    ]
  };

  const SOFTWARE = {
    software: [
      {
        name: "orca", resolved: "/opt/orca/orca", version: "6.0.0", source: "path",
        multiple: true,
        candidates: [
          { path: "/opt/orca/orca", version: "6.0.0", source: "path" },
          { path: "/usr/bin/orca", version: "", source: "scan" }
        ]
      },
      { name: "xtb", resolved: null, version: "", source: null, multiple: false, candidates: [] }
    ]
  };

  const PING_OK = { reachable: true, node: "alpha", status: "online", error: null };
  const PING_UNREACHABLE = { reachable: false, node: "beta", status: "offline", error: "ssh timeout" };
  const BOOTSTRAP_OK = {
    node: "alpha", reachable: true, ok: true, exit_code: 0, python_executable: "python3",
    requirements_path: "/opt/acp/requirements-node.txt", sync_uploaded: 3, sync_errors: [],
    stdout_tail: "Successfully installed openpyxl", stderr_tail: "", error: null
  };
  const BOOTSTRAP_FAILED = {
    node: "beta", reachable: true, ok: false, exit_code: 1, python_executable: "python3",
    requirements_path: "", sync_uploaded: 0, sync_errors: [],
    stdout_tail: "", stderr_tail: "ERROR: no module named openpyxl", error: "pip failed"
  };

  // -------------------------------------------------------------- adapters --

  function responseLike(payload, ok = true, status = 200) {
    return {
      ok,
      status,
      json: () => Promise.resolve(payload),
      text: () => Promise.resolve(JSON.stringify(payload))
    };
  }

  function makeFetchAdapter(routes) {
    const calls = [];
    function fetchImpl(url, init) {
      calls.push({ url, method: (init && init.method) || "GET" });
      const handler = routes[url];
      if (!handler) return Promise.reject(new Error("unexpected request " + url));
      try {
        return Promise.resolve(handler(url, init));
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return { calls, fetch: fetchImpl };
  }

  function makeRequestAdapter(routes) {
    const calls = [];
    // A request-like transport resolves the parsed JSON body (never a
    // Response); the same fixture handlers therefore get unwrapped here.
    function unwrap(value) {
      if (value && typeof value === "object" && typeof value.json === "function") {
        return Promise.resolve(value.json());
      }
      return value;
    }
    function request(path, opts) {
      calls.push({
        url: path,
        method: (opts && opts.method) || "GET",
        timeoutMs: opts ? opts.timeoutMs : undefined
      });
      const handler = routes[path];
      if (!handler) return Promise.reject(new Error("unexpected request " + path));
      try {
        return Promise.resolve(handler(path, opts)).then(unwrap);
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return { calls, request };
  }

  function apiRoutes(overrides) {
    const routes = {
      [API + "/nodes"]: () => responseLike(NODES),
      [API + "/backends"]: () => responseLike(BACKENDS),
      [API + "/software/discovery"]: () => responseLike(SOFTWARE),
      [API + "/nodes/alpha/status"]: () => responseLike(NODES.nodes[0]),
      [API + "/nodes/beta/status"]: () => responseLike(NODES.nodes[1]),
      [API + "/nodes/alpha/ping"]: () => responseLike(PING_OK),
      [API + "/nodes/beta/ping"]: () => responseLike(PING_UNREACHABLE),
      [API + "/nodes/alpha/bootstrap"]: () => responseLike(BOOTSTRAP_OK),
      [API + "/nodes/beta/bootstrap"]: () => responseLike(BOOTSTRAP_FAILED)
    };
    return Object.assign(routes, overrides || {});
  }

  async function mountWith(routes, options) {
    options = options || {};
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const timers = options.trackTimers ? trackTimers(sandbox) : null;
    loadScript(MODULE_PATH, sandbox);
    const adapter = options.mode === "request" ? makeRequestAdapter(routes) : makeFetchAdapter(routes);
    const deps = { doc: dom.document };
    if (options.mode === "request") deps.request = adapter.request;
    else deps.fetch = adapter.fetch;
    if (options.deps) Object.assign(deps, options.deps);
    const instance = sandbox.ACPNextModules["node-status"].factory(deps);
    const host = dom.document.createElement("div");
    await instance.mount(host);
    return { dom, sandbox, adapter, instance, host, timers };
  }

  function stateOf(host) {
    return host.children[0] ? host.children[0].getAttribute("data-acp-module-state") : null;
  }

  function callList(adapter) {
    return adapter.calls.map((call) => call.method + " " + call.url);
  }

  // ------------------------------------------------------------------ tests --

  await test("node-status: registers exactly one Shell-valid module and no other global", () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const before = new Set(Object.keys(sandbox));
    loadScript(MODULE_PATH, sandbox);

    const added = Object.keys(sandbox).filter((key) => !before.has(key));
    assertEq(added, ["ACPNextModules"]);
    assert(sandbox.ACPNextNodeStatus === undefined, "no helper global");

    const table = sandbox.ACPNextModules;
    assert(table && typeof table === "object", "ACPNextModules table");
    assertEq(Object.keys(table), ["node-status"]);

    const entry = table["node-status"];
    assert(entry && entry.descriptor && typeof entry.factory === "function", "{descriptor, factory}");
    assertEq(entry.descriptor.id, "node-status");
    assertEq(entry.descriptor.interfaceVersion, 1);
    assertEq(entry.descriptor.version, "0.1.0");
    assertEq(entry.descriptor.provides, ["system.read"]);
    assertEq(entry.descriptor.supportedInstancing, "single");
    assertEq(entry.descriptor.error.titleKey, "next.nodes.errorTitle");

    loadScript("js/shell/module-registry.js", sandbox);
    assertEq(sandbox.ACPShell.registry.validateDescriptor(entry.descriptor), null);
    assert(sandbox.ACPShell.registry.collectFrom(table).registered.indexOf("node-status") !== -1,
      "Shell bulk-collect accepts the module");

    const instance = entry.factory({ doc: dom.document });
    ["mount", "updateContext", "suspend", "resume", "dispose"].forEach((name) => {
      assert(typeof instance[name] === "function", "ModuleInstance." + name);
    });
  });

  await test("node-status: loading -> ready renders nodes, backends and software rows", async () => {
    const nodesDeferred = deferred();
    const backendsDeferred = deferred();
    const softwareDeferred = deferred();
    const routes = apiRoutes({
      [API + "/nodes"]: () => nodesDeferred.promise,
      [API + "/backends"]: () => backendsDeferred.promise,
      [API + "/software/discovery"]: () => softwareDeferred.promise
    });
    const dom = makeDom();
    const sandbox = makeSandbox({});
    loadScript(MODULE_PATH, sandbox);
    const adapter = makeFetchAdapter(routes);
    const instance = sandbox.ACPNextModules["node-status"].factory({ doc: dom.document, fetch: adapter.fetch });
    const host = dom.document.createElement("div");
    await instance.mount(host);
    await flush(2);
    assertEq(stateOf(host), "loading", "initial state is loading");

    nodesDeferred.resolve(responseLike(NODES));
    backendsDeferred.resolve(responseLike(BACKENDS));
    softwareDeferred.resolve(responseLike(SOFTWARE));
    await flush();

    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(root.getAttribute("data-nodes-count"), "2");
    assertEq(root.getAttribute("data-backends-count"), "2");
    assertEq(root.getAttribute("data-software-count"), "2");

    const alphaRow = find(host, "data-node", "alpha");
    assert(!!alphaRow, "alpha row");
    assertEq(alphaRow.getAttribute("data-node-status"), "online");
    assert(subtreeText(alphaRow).indexOf("在线") !== -1, "status label");
    assert(subtreeText(alphaRow).indexOf("1/4") !== -1, "running/max jobs");
    assert(subtreeText(alphaRow).indexOf("62%") !== -1, "disk usage");
    assert(subtreeText(alphaRow).indexOf("normal") !== -1, "queue");
    assertEq(find(host, "data-node", "beta").getAttribute("data-node-status"), "degraded");
    assert(subtreeText(find(host, "data-node", "beta")).indexOf("load high") !== -1, "node error text");

    const orcaBackend = find(host, "data-backend", "orca");
    assertEq(orcaBackend.getAttribute("data-acp-availability"), "available");
    assert(subtreeText(orcaBackend).indexOf("6.0.0") !== -1, "backend version");
    assertEq(findAll(host, "data-capability", "opt")[0].getAttribute("data-available"), "true");
    assertEq(findAll(host, "data-capability", "freq")[0].getAttribute("data-available"), "false");

    const orcaSoftware = find(host, "data-software", "orca");
    assertEq(orcaSoftware.getAttribute("data-acp-availability"), "resolved");
    assert(subtreeText(orcaSoftware).indexOf("/opt/orca/orca") !== -1, "resolved path");
    assert(subtreeText(orcaSoftware).indexOf("候选安装") !== -1, "multi-install disclosure");
    instance.dispose();
  });

  await test("node-status: v1 'not configured' 503 with no other data is an explicit empty state", async () => {
    const routes = apiRoutes({
      [API + "/nodes"]: () => responseLike({ detail: "Remote execution is not configured on this server" }, false, 503),
      [API + "/backends"]: () => responseLike({ backends: [] }),
      [API + "/software/discovery"]: () => responseLike({ software: [] })
    });
    const { host, instance } = await mountWith(routes);
    await flush();

    assertEq(stateOf(host), "empty");
    const text = subtreeText(host);
    assert(text.indexOf("未配置远程节点") !== -1, "not-configured copy");
    assert(text.indexOf("没有可显示的系统信息") !== -1, "empty copy");
    assertEq(findAll(host, "data-action", "ping").length, 0, "no node rows");
    assertEq(find(host, "data-action", "retry").hidden, true, "no retry for the normal empty case");
    instance.dispose();
  });

  await test("node-status: failing read endpoints render a local error state and retry recovers", async () => {
    let fail = true;
    const routes = apiRoutes({
      [API + "/nodes"]: () => (fail ? responseLike({ detail: "boom" }, false, 500) : responseLike(NODES)),
      [API + "/backends"]: () => (fail ? responseLike({ detail: "boom" }, false, 500) : responseLike(BACKENDS)),
      [API + "/software/discovery"]: () => (fail ? responseLike({ detail: "boom" }, false, 500) : responseLike(SOFTWARE))
    });
    const { host, instance, adapter } = await mountWith(routes);
    await flush();

    assertEq(stateOf(host), "error");
    const text = subtreeText(host);
    assert(text.indexOf("系统信息加载失败") !== -1, "error heading");
    assert(text.indexOf("boom") !== -1, "server detail surfaced");
    const retry = find(host, "data-action", "retry");
    assert(retry && retry.hidden === false, "retry button visible");

    fail = false;
    retry.fire("click", {});
    await flush();
    assertEq(stateOf(host), "ready");
    assertEq(host.children[0].getAttribute("data-nodes-count"), "2");
    assert(callList(adapter).filter((call) => call === "GET " + API + "/nodes").length >= 2, "retry re-fetched");
    instance.dispose();
  });

  await test("node-status: a partial failure keeps the healthy sections visible", async () => {
    const routes = apiRoutes({
      [API + "/nodes"]: () => responseLike({ detail: "node list down" }, false, 500)
    });
    const { host, instance } = await mountWith(routes);
    await flush();

    assertEq(stateOf(host), "ready", "software/backends still render");
    const text = subtreeText(host);
    assert(text.indexOf("远程节点加载失败") !== -1, "section error for nodes");
    assert(text.indexOf("node list down") !== -1, "node failure text");
    assertEq(findAll(host, "data-backend", "orca").length, 1, "backends kept");
    assertEq(findAll(host, "data-software", "orca").length, 1, "software kept");
    instance.dispose();
  });

  await test("node-status: FastAPI error detail shapes (string / object / validation list)", async () => {
    async function errorText(detailPayload) {
      const routes = apiRoutes({
        [API + "/nodes"]: () => responseLike(detailPayload, false, 500),
        [API + "/backends"]: () => responseLike(detailPayload, false, 500),
        [API + "/software/discovery"]: () => responseLike(detailPayload, false, 500)
      });
      const { host, instance } = await mountWith(routes);
      await flush();
      assertEq(stateOf(host), "error");
      const text = subtreeText(host);
      instance.dispose();
      return text;
    }

    assert((await errorText({ detail: "plain detail" })).indexOf("plain detail") !== -1);
    assert((await errorText({ detail: { code: "E_NODE", message: "kaput" } })).indexOf("kaput") !== -1);
    assert((await errorText({ detail: [{ msg: "bad field" }] })).indexOf("bad field") !== -1);
  });

  await test("node-status: ping posts to the node command and shows busy then a clear result", async () => {
    const pingDeferred = deferred();
    const routes = apiRoutes({ [API + "/nodes/alpha/ping"]: () => pingDeferred.promise });
    const { host, instance, adapter } = await mountWith(routes);
    await flush(2);

    const pingBtn = findNodeAction(host, "alpha", "ping");
    assert(!!pingBtn, "ping button");
    assertEq(find(host, "data-node", "alpha").getAttribute("data-node-busy"), "");
    pingBtn.fire("click", {});
    await flush(1);

    const busyRow = find(host, "data-node", "alpha");
    assertEq(busyRow.getAttribute("data-node-busy"), "ping", "busy state");
    assertEq(findNodeAction(host, "alpha", "ping").disabled, true, "busy disables the button");
    assert(subtreeText(busyRow).indexOf("测试中") !== -1, "busy copy");
    assertEq(findNodeAction(host, "alpha", "bootstrap").disabled, true, "no parallel bootstrap");

    pingDeferred.resolve(responseLike(PING_OK));
    await flush();

    const doneRow = find(host, "data-node", "alpha");
    assertEq(doneRow.getAttribute("data-node-busy"), "");
    assertEq(doneRow.getAttribute("data-node-result"), "ping-ok");
    assert(subtreeText(doneRow).indexOf("可达") !== -1, "success copy");
    const calls = callList(adapter);
    assertEq(calls.filter((call) => call === "POST " + API + "/nodes/alpha/ping"), ["POST " + API + "/nodes/alpha/ping"]);
    assert(doneRow !== null);
    assert(calls.indexOf("GET " + API + "/nodes/alpha/status") !== -1, "status refresh after ping");
    assert(calls.indexOf("POST " + API + "/nodes/alpha/bootstrap") === -1, "no bootstrap issued");
    instance.dispose();
  });

  await test("node-status: ping failure text separates unreachable from transport errors", async () => {
    const unreachable = { reachable: false, node: "beta", status: "offline", error: "ssh timeout" };
    const offlineNode = Object.assign({}, NODES.nodes[1], { status: "offline", error: "ssh timeout" });
    const routes = apiRoutes({
      [API + "/nodes/beta/ping"]: () => responseLike(unreachable),
      [API + "/nodes/beta/status"]: () => responseLike(offlineNode)
    });
    const { host, instance } = await mountWith(routes);
    await flush();

    findNodeAction(host, "beta", "ping").fire("click", {});
    await flush();
    let row = find(host, "data-node", "beta");
    assertEq(row.getAttribute("data-node-result"), "ping-error");
    assertEq(row.getAttribute("data-node-status"), "offline", "status updated from the ping reply");
    assert(subtreeText(row).indexOf("不可达：ssh timeout") !== -1, "unreachable copy");

    const transportError = new Error("HTTP 500");
    transportError.status = 500;
    transportError.body = { detail: "ssh client crashed" };
    const failingRoutes = apiRoutes({ [API + "/nodes/alpha/ping"]: () => Promise.reject(transportError) });
    const second = await mountWith(failingRoutes);
    await flush();
    findNodeAction(second.host, "alpha", "ping").fire("click", {});
    await flush();
    const alphaRow = find(second.host, "data-node", "alpha");
    assertEq(alphaRow.getAttribute("data-node-result"), "ping-error");
    assert(subtreeText(alphaRow).indexOf("连通测试失败：ssh client crashed") !== -1, "transport failure copy");
    instance.dispose();
    second.instance.dispose();
  });

  await test("node-status: bootstrap shows a busy timer, success text and stderr tail on failure", async () => {
    const bootDeferred = deferred();
    const routes = apiRoutes({ [API + "/nodes/alpha/bootstrap"]: () => bootDeferred.promise });
    const { host, instance, adapter, timers } = await mountWith(routes, { trackTimers: true });
    await flush(2);

    findNodeAction(host, "alpha", "bootstrap").fire("click", {});
    await flush(1);
    assertEq(find(host, "data-node", "alpha").getAttribute("data-node-busy"), "bootstrap");
    assertEq(timers.started, 1, "elapsed-time ticker started for the slow command");
    assert(timers.active.size === 1, "one owned interval");
    assert(subtreeText(find(host, "data-node", "alpha")).indexOf("引导中") !== -1, "busy copy");

    bootDeferred.resolve(responseLike(BOOTSTRAP_OK));
    await flush();
    const row = find(host, "data-node", "alpha");
    assertEq(row.getAttribute("data-node-result"), "bootstrap-ok");
    assert(subtreeText(row).indexOf("引导完成") !== -1, "success copy");
    assertEq(timers.active.size, 0, "ticker stopped when no bootstrap is running");
    assert(adapter.calls.some((call) => (call.method || "GET") + " " + call.url ===
      "POST " + API + "/nodes/alpha/bootstrap"), "bootstrap POST issued");

    findNodeAction(host, "beta", "bootstrap").fire("click", {});
    await flush();
    const failedRow = find(host, "data-node", "beta");
    assertEq(failedRow.getAttribute("data-node-result"), "bootstrap-error");
    const failedText = subtreeText(failedRow);
    assert(failedText.indexOf("引导失败：pip failed") !== -1, "failure copy");
    assert(failedText.indexOf("ERROR: no module named openpyxl") !== -1, "stderr tail rendered");
    const details = walk(failedRow).find((node) => node.tagName === "DETAILS" && node.hidden === false);
    assert(!!details, "stderr details opened");
    instance.dispose();
  });

  await test("node-status: deps.request and deps.client.transport.request are both supported", async () => {
    const first = await mountWith(apiRoutes(), { mode: "request" });
    await flush();
    assertEq(stateOf(first.host), "ready");
    assert(callList(first.adapter).indexOf("GET " + API + "/nodes") !== -1);
    first.instance.dispose();

    const dom = makeDom();
    const sandbox = makeSandbox({});
    loadScript(MODULE_PATH, sandbox);
    const adapter = makeRequestAdapter(apiRoutes());
    const client = { transport: { request: adapter.request } };
    const instance = sandbox.ACPNextModules["node-status"].factory({ doc: dom.document, client });
    const host = dom.document.createElement("div");
    await instance.mount(host);
    await flush();
    assertEq(stateOf(host), "ready");
    assertEq(host.children[0].getAttribute("data-nodes-count"), "2");
    assert(callList(adapter).indexOf("GET " + API + "/software/discovery") !== -1);

    findNodeAction(host, "alpha", "bootstrap").fire("click", {});
    await flush();
    const bootstrapCall = adapter.calls.find((call) => call.method === "POST" &&
      call.url === API + "/nodes/alpha/bootstrap");
    assert(!!bootstrapCall, "bootstrap issued through the client transport");
    assertEq(bootstrapCall.timeoutMs, 600000, "bootstrap keeps the route's 600 s budget");
    instance.dispose();
  });

  await test("node-status: without an injected adapter it never touches the network and says so", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    loadScript(MODULE_PATH, sandbox);
    assertEq(sandbox.fetch, undefined, "sandbox has no fetch");
    const instance = sandbox.ACPNextModules["node-status"].factory({ doc: dom.document });
    const host = dom.document.createElement("div");
    await instance.mount(host);
    await flush();

    assertEq(stateOf(host), "error");
    const text = subtreeText(host);
    assert(text.indexOf("未注入 fetch 或 client.transport.request") !== -1, "adapter-missing copy");
    assertEq(find(host, "data-action", "retry").hidden, true, "no pointless retry without an adapter");
    instance.dispose();
  });

  await test("node-status: unprobed software/backends stay 未知 (never a guessed version)", async () => {
    const { host, instance } = await mountWith(apiRoutes());
    await flush();

    const unprobed = find(host, "data-software", "xtb");
    assertEq(unprobed.getAttribute("data-acp-availability"), "unknown");
    const text = subtreeText(unprobed);
    assert(text.indexOf("未发现") !== -1, "resolved=false badge");
    assert(text.indexOf("未知") !== -1, "version unknown");
    assert(text.indexOf("未发现可执行文件") !== -1, "path missing, not invented");
    assert(text.indexOf("来源未知") !== -1, "source unknown");

    const backend = find(host, "data-backend", "xtb");
    assertEq(backend.getAttribute("data-acp-availability"), "unavailable");
    const backendText = subtreeText(backend);
    assert(backendText.indexOf("不可用") !== -1, "availability label");
    assert(backendText.indexOf("未知") !== -1, "backend version unknown");
    assert(backendText.indexOf("未发现可执行文件") !== -1, "backend path missing");
    assert(backendText.indexOf("v0.0.0") === -1, "no fabricated version");

    // alpha has a probed version; it is rendered verbatim, not normalized.
    const softwareOrca = find(host, "data-software", "orca");
    assert(subtreeText(softwareOrca).indexOf("6.0.0") !== -1);
    instance.dispose();
  });

  await test("node-status: a newer refresh supersedes an older in-flight response", async () => {
    let nodeCalls = 0;
    const slow = deferred();
    const latest = deferred();
    const routes = apiRoutes();
    routes[API + "/nodes"] = () => {
      nodeCalls += 1;
      if (nodeCalls === 1) return responseLike(NODES);
      if (nodeCalls === 2) return slow.promise;
      return latest.promise;
    };
    const { host, instance } = await mountWith(routes);
    await flush();
    assertEq(host.children[0].getAttribute("data-nodes-count"), "2");

    instance.refresh();
    await flush(2);
    instance.refresh();
    await flush(2);

    latest.resolve(responseLike({
      auto_select: true,
      nodes: [{
        name: "gamma", host: "10.0.0.13", status: "online", running_jobs: 0, max_jobs: 2,
        disk_usage_pct: 5, last_check: "", error: null, queue: null, software: {},
        capability_state: "unknown", declared_ok: null, mismatch: [], probe_note: null
      }]
    }));
    await flush();
    assertEq(host.children[0].getAttribute("data-nodes-count"), "1");
    assert(!!find(host, "data-node", "gamma"), "latest payload rendered");

    slow.resolve(responseLike(NODES));
    await flush();
    assertEq(host.children[0].getAttribute("data-nodes-count"), "1", "stale refresh dropped");
    assert(!find(host, "data-node", "alpha"), "stale rows never reappear");
    instance.dispose();
  });

  await test("node-status: dispose removes DOM, listeners and the timer; late results write nothing", async () => {
    const nodesDeferred = deferred();
    const bootDeferred = deferred();
    const routes = apiRoutes({
      [API + "/nodes"]: () => nodesDeferred.promise,
      [API + "/nodes/alpha/bootstrap"]: () => bootDeferred.promise
    });
    const { host, instance, dom, timers } = await mountWith(routes, { trackTimers: true });
    await flush(2);
    assertEq(stateOf(host), "loading");
    assert(dom.document.activeDocListeners() >= 1, "language listener mounted");

    // A second instance exercises the bootstrap timer path on live rows.
    const live = await mountWith(apiRoutes({ [API + "/nodes/alpha/bootstrap"]: () => bootDeferred.promise }), { trackTimers: true });
    await flush();
    findNodeAction(live.host, "alpha", "bootstrap").fire("click", {});
    await flush(1);
    assertEq(live.timers.active.size, 1, "timer running while bootstrap is in flight");

    live.instance.dispose();
    assertEq(live.timers.active.size, 0, "dispose cleared the interval");
    assert(live.timers.cleared >= 1, "clearInterval called");
    assertEq(live.host.children.length, 0, "host DOM removed");
    assertEq(live.dom.document.activeDocListeners(), 0, "document listeners released");
    bootDeferred.resolve(responseLike(BOOTSTRAP_OK));
    await flush();
    assertEq(live.host.children.length, 0, "late bootstrap result cannot re-create DOM");
    live.instance.dispose();
    assertEq(live.host.children.length, 0, "dispose is idempotent");

    instance.dispose();
    assertEq(host.children.length, 0, "initial host cleared");
    assertEq(dom.document.activeDocListeners(), 0, "initial document listeners released");
    nodesDeferred.resolve(responseLike(NODES));
    await flush();
    assertEq(host.children.length, 0, "late load result cannot re-create DOM");
    instance.dispose();

    // A disposed instance can mount again (fresh lifecycle, fresh generation).
    const remountHost = dom.document.createElement("div");
    await instance.mount(remountHost);
    await flush();
    assertEq(stateOf(remountHost), "ready", "re-mount after dispose loads again");
    assertEq(remountHost.children[0].getAttribute("data-nodes-count"), "2");
    instance.dispose();
    assertEq(remountHost.children.length, 0, "second dispose clears the new host");
  });

  await test("node-status: only ping/bootstrap POSTs are issued (no task submission)", async () => {
    const { host, instance, adapter } = await mountWith(apiRoutes(), {});
    await flush();
    assertEq(stateOf(host), "ready");

    findNodeAction(host, "alpha", "ping").fire("click", {});
    await flush();
    findNodeAction(host, "alpha", "bootstrap").fire("click", {});
    await flush();

    const allowed = new Set([
      "GET " + API + "/nodes",
      "GET " + API + "/backends",
      "GET " + API + "/software/discovery",
      "GET " + API + "/nodes/alpha/status",
      "GET " + API + "/nodes/beta/status",
      "POST " + API + "/nodes/alpha/ping",
      "POST " + API + "/nodes/alpha/bootstrap"
    ]);
    callList(adapter).forEach((call) => {
      assert(allowed.has(call), "unexpected request " + call);
      assert(call.indexOf("/jobs") === -1, "no task submission");
    });
    const posts = callList(adapter).filter((call) => call.indexOf("POST ") === 0);
    assertEq(posts.sort(), ["POST " + API + "/nodes/alpha/bootstrap", "POST " + API + "/nodes/alpha/ping"]);
    assertEq(walk(host).filter((node) => node.tagName === "BUTTON" &&
      ["submit", "cancel", "pause", "purge"].indexOf(node.getAttribute("data-action")) !== -1).length, 0,
      "no task-mutating controls");
    instance.dispose();
  });
};
