/* ACP 2.0 Next Shell — host environment seam (P1).
 *
 * Pins js/shell/host-env.js (window.ACP_ENV → window.ACPShell.env) and the
 * guarded reads that consume it:
 *   1. no ACP_ENV → today's Workbench defaults, byte-for-byte;
 *   2. an injected DSH config drives every accessor (api bases, asset prefix,
 *      storage namespace, doc/win/location, manageDocument);
 *   3. assetUrl joins with exactly one slash and tolerates whitespace;
 *   4. ACP_ENV is read lazily, so a late install is picked up;
 *   5. bootstrap._initClient / _api / _apiV2 use the injected bases only;
 *   6. i18n.applyI18n with manageDocument:false never writes documentElement.lang
 *      but still translates;
 *   7. layout-preferences persists under the injected storage prefix;
 *   8. a full boot() against a DOM stub with manageDocument:false still reaches
 *      data-acp-ready="true" and leaves the page title alone.
 */
"use strict";

// ── minimal DOM stub (same technique as acp2_shell_bootstrap.test.js) ──

function makeClassList() {
  const set = new Set();
  return {
    add(...names) { names.forEach((n) => set.add(n)); },
    remove(...names) { names.forEach((n) => set.delete(n)); },
    toggle(name, force) {
      const want = force === undefined ? !set.has(name) : !!force;
      if (want) set.add(name); else set.delete(name);
      return want;
    },
    contains(name) { return set.has(name); },
    _set: set,
  };
}

function matchesSelector(node, selector) {
  const s = String(selector).trim();
  let m = /^([A-Za-z0-9_-]+)$/.exec(s);
  if (m) return node.tagName === m[1].toUpperCase();
  m = /^\.([A-Za-z0-9_-]+)$/.exec(s);
  if (m) return node.classList.contains(m[1]);
  m = /^\[([A-Za-z0-9_-]+)\]$/.exec(s);
  if (m) return node.getAttribute(m[1]) !== null;
  m = /^([A-Za-z0-9_-]+)\[([A-Za-z0-9_-]+)\]$/.exec(s);
  if (m) return node.tagName === m[1].toUpperCase() && node.getAttribute(m[2]) !== null;
  return false;
}

function queryDeep(node, selector, all) {
  const out = [];
  (function walk(current) {
    for (const child of current.children) {
      if (matchesSelector(child, selector)) {
        out.push(child);
        if (!all) return;
      }
      walk(child);
    }
  })(node);
  return all ? out : (out[0] || null);
}

function makeElement(doc, tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    ownerDocument: doc,
    parentNode: null,
    children: [],
    attrs: {},
    textContent: "",
    hidden: false,
    listeners: {},
    classList: null,
    focus() { doc.activeElement = node; },
    blur() { if (doc.activeElement === node) doc.activeElement = null; },
    setAttribute(name, value) {
      node.attrs[name] = String(value);
      if (name === "class") {
        node.classList._set.clear();
        String(value).split(/\s+/).filter(Boolean).forEach((c) => node.classList._set.add(c));
      }
      if (name === "hidden") node.hidden = true;
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(node.attrs, name) ? node.attrs[name] : null;
    },
    hasAttribute(name) { return node.getAttribute(name) !== null; },
    removeAttribute(name) { delete node.attrs[name]; },
    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = node;
      node.children.push(child);
      doc._all.push(child);
      return child;
    },
    removeChild(child) {
      const idx = node.children.indexOf(child);
      if (idx >= 0) node.children.splice(idx, 1);
      child.parentNode = null;
      const allIdx = doc._all.indexOf(child);
      if (allIdx >= 0) doc._all.splice(allIdx, 1);
      return child;
    },
    addEventListener(type, handler) {
      (node.listeners[type] = node.listeners[type] || []).push(handler);
    },
    removeEventListener(type, handler) {
      const list = node.listeners[type] || [];
      const idx = list.indexOf(handler);
      if (idx >= 0) list.splice(idx, 1);
    },
    dispatch(type, event) {
      (node.listeners[type] || []).slice().forEach((h) => h(event));
    },
    querySelector(selector) { return queryDeep(node, selector, false); },
    querySelectorAll(selector) { return queryDeep(node, selector, true); },
    get firstChild() { return node.children[0] || null; },
  };
  node.classList = makeClassList(node);
  return node;
}

function makeDocument() {
  const byId = new Map();
  const docListeners = {};
  const doc = {
    _all: [],
    documentElement: { lang: "zh-CN" },
    title: "",
    activeElement: null,
    createElement(tag) {
      const node = makeElement(doc, tag);
      const origSet = node.setAttribute;
      node.setAttribute = function (name, value) {
        origSet.call(node, name, value);
        if (name === "id") byId.set(String(value), node);
      };
      return node;
    },
    createElementNS(ns, tag) { return doc.createElement(tag); },
    getElementById(id) { return byId.get(String(id)) || null; },
    querySelectorAll(selector) { return doc._all.filter((n) => matchesSelector(n, selector)); },
    querySelector(selector) { return doc._all.find((n) => matchesSelector(n, selector)) || null; },
    addEventListener(type, handler) {
      (docListeners[type] = docListeners[type] || []).push(handler);
    },
    removeEventListener(type, handler) {
      const list = docListeners[type] || [];
      const idx = list.indexOf(handler);
      if (idx >= 0) list.splice(idx, 1);
    },
    dispatchEvent(event) {
      (docListeners[event && event.type] || []).slice().forEach((h) => h(event));
      return true;
    },
    _docListeners: docListeners,
  };
  return doc;
}

/** Mirrors frontend/next/index.html §1.1 structure (ids only). */
function buildShellDom(doc) {
  const shell = doc.createElement("div");
  shell.setAttribute("id", "acp-shell");
  shell.setAttribute("data-workspace", "research");
  shell.setAttribute("data-acp-ready", "false");

  const main = doc.createElement("main");
  main.setAttribute("id", "acp-shell-main");
  shell.appendChild(main);

  ["research", "drawing", "molecular", "config", "execution", "files"].forEach((ws, i) => {
    const section = doc.createElement("section");
    section.setAttribute("id", "acp-ws-" + ws);
    if (i > 0) section.hidden = true;
    main.appendChild(section);
    const slotNames = ["main", "side", "bottom"];
    slotNames.forEach((slotName) => {
      const slot = doc.createElement("div");
      slot.setAttribute("id", "acp-slot-" + ws + "-" + slotName);
      section.appendChild(slot);
    });
    const tab = doc.createElement("button");
    tab.setAttribute("id", "acp-tab-" + ws);
    tab.setAttribute("aria-selected", i === 0 ? "true" : "false");
    shell.appendChild(tab);
  });

  const fatal = doc.createElement("div");
  fatal.setAttribute("id", "acp-shell-fatal");
  fatal.hidden = true;
  shell.appendChild(fatal);
  return { shell, main, fatal };
}

// ── helpers ───────────────────────────────────────────────────────────

const SHELL_SCRIPTS = [
  "js/shell/i18n.js",
  "js/shell/workspace-context.js",
  "js/shell/module-registry.js",
  "js/shell/module-loader.js",
  "js/shell/commands.js",
  "js/shell/layout-manager.js",
  "js/shell/recipes.js",
  "js/shell/layout-preferences.js",
  "js/shell/bootstrap.js",
];

function makeStorageStub(store) {
  return {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
  };
}

/** Fresh sandbox with host-env.js loaded (and optionally ACP_ENV already set). */
function loadEnv(h, acpEnv, extra) {
  const sandbox = h.makeSandbox(Object.assign({}, extra || {}));
  if (acpEnv) sandbox.ACP_ENV = acpEnv;
  h.loadScript("js/shell/host-env.js", sandbox);
  return sandbox;
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const flush = async (rounds) => {
    for (let i = 0; i < (rounds || 6); i++) await new Promise((r) => setTimeout(r, 0));
  };

  // ── 1. defaults (no ACP_ENV) ──

  await test("hostenv: no ACP_ENV keeps every Workbench default", async () => {
    const docStub = { documentElement: { lang: "zh-CN" }, title: "" };
    const sandbox = loadEnv(h, null, { document: docStub });
    const env = sandbox.ACPShell.env;
    assert(env, "ACPShell.env published");

    assertEq(env.apiBase(), "/api/v1");
    assertEq(env.apiV2Base(), "/api/v2");
    assertEq(env.assetUrl("/js/x.js"), "/js/x.js", "empty asset base leaves the path alone");
    assertEq(env.storageKey("k"), "k", "empty storage prefix leaves the key alone");
    assertEq(env.isDomControlled(), true);
    assertEq(env.manageDocument(), true);
    assertEq(env.host(), "acp-web");
    assertEq(env.homeHref(), "/");
    assertEq(env.sessionId(), null);
    assertEq(env.degraded(), false);
    assertEq(env.focusBridge(), null);
    assert(env.doc() === docStub, "doc() falls back to window.document");
    assert(env.win().document === docStub, "win() falls back to window");
    assert(env.location() === undefined || env.location() === null || typeof env.location() === "object",
      "location() falls back to window.location");

    // raw() is a frozen copy of the defaults.
    const raw = env.raw();
    assertEq(Object.isFrozen(raw), true, "raw() is frozen");
    assertEq(raw.apiBase, "/api/v1");
    assertEq(raw.assetBase, "");
    try { raw.apiBase = "/hijacked"; } catch (e) { /* frozen */ }
    assertEq(env.apiBase(), "/api/v1", "raw() cannot be mutated into the accessors");

    // Unknown / empty strings fall back to the defaults.
    sandbox.ACP_ENV = { host: "", apiBase: "  ", apiV2Base: 42, homeHref: "", manageDocument: "yes" };
    assertEq(env.host(), "acp-web");
    assertEq(env.apiBase(), "/api/v1");
    assertEq(env.apiV2Base(), "/api/v2");
    assertEq(env.homeHref(), "/");
    assertEq(env.manageDocument(), true, "only manageDocument === false opts out");
    assertEq(env.isDomControlled(), true);
  });

  // ── 2. injected DSH config ──

  await test("hostenv: injected DSH config drives every accessor", async () => {
    const docStub = { tag: "dsh-doc" };
    const store = {};
    const winStub = { localStorage: makeStorageStub(store), marker: "dsh-win" };
    const locStub = { search: "?panel=1" };
    const bridge = { focus: function () {} };

    const sandbox = loadEnv(h, {
      host: "dsh",
      assetBase: "/acp",
      apiBase: "/acp-api/v1",
      apiV2Base: "/acp-api/v2",
      storagePrefix: "dsh:",
      manageDocument: false,
      doc: docStub,
      win: winStub,
      location: locStub,
      homeHref: "/acp/classic",
      sessionId: "sess-7",
      focusBridge: bridge,
      degraded: true,
    });
    const env = sandbox.ACPShell.env;

    assertEq(env.host(), "dsh");
    assertEq(env.apiBase(), "/acp-api/v1");
    assertEq(env.apiV2Base(), "/acp-api/v2");
    assertEq(env.assetUrl("/js/x.js"), "/acp/js/x.js");
    assertEq(env.storageKey("acp-lang"), "dsh:acp-lang");
    assertEq(env.isDomControlled(), false);
    assertEq(env.manageDocument(), false);
    assert(env.doc() === docStub, "doc() is the injected stub");
    assert(env.win() === winStub, "win() is the injected stub");
    assert(env.location() === locStub, "location() is the injected stub");
    assertEq(env.homeHref(), "/acp/classic");
    assertEq(env.sessionId(), "sess-7");
    assertEq(env.degraded(), true);
    assert(env.focusBridge() === bridge, "focusBridge is a passthrough object");

    // Storage goes through win().localStorage with the prefix applied once.
    env.storage.set("acp-lang", "en-US");
    assertEq(store["dsh:acp-lang"], "en-US", "write is namespaced");
    assertEq(env.storage.get("acp-lang"), "en-US", "read is namespaced");
    env.storage.remove("acp-lang");
    assert(!Object.prototype.hasOwnProperty.call(store, "dsh:acp-lang"), "remove is namespaced");
    assertEq(env.storage.get("acp-lang"), null, "missing key reads as null");

    // A throwing localStorage never escapes the seam.
    sandbox.ACP_ENV = { win: { get localStorage() { throw new Error("blocked"); } }, storagePrefix: "dsh:" };
    assertEq(env.storage.get("acp-lang"), null, "throwing getItem degrades to null");
    env.storage.set("acp-lang", "zh-CN"); // must not throw
    env.storage.remove("acp-lang");       // must not throw
    assertEq(env.storageKey("acp-lang"), "dsh:acp-lang");
  });

  // ── 3. asset base joining ──

  await test("hostenv: assetUrl joins with exactly one slash", async () => {
    const sandbox = loadEnv(h, null);
    const env = sandbox.ACPShell.env;

    sandbox.ACP_ENV = { assetBase: "/acp/" };
    assertEq(env.assetUrl("/js/x.js"), "/acp/js/x.js", "trailing slash on the base");
    sandbox.ACP_ENV = { assetBase: "/acp" };
    assertEq(env.assetUrl("/js/x.js"), "/acp/js/x.js", "no trailing slash on the base");
    assertEq(env.assetUrl("js/x.js"), "/acp/js/x.js", "no leading slash on the path");
    assertEq(env.assetUrl("///js/x.js"), "/acp/js/x.js", "leading slashes collapse");
    sandbox.ACP_ENV = { assetBase: " /acp/ " };
    assertEq(env.assetUrl("/js/x.js"), "/acp/js/x.js", "base whitespace is trimmed");
    sandbox.ACP_ENV = { assetBase: "" };
    assertEq(env.assetUrl("/js/x.js"), "/js/x.js", "empty base returns the path unchanged");
    sandbox.ACP_ENV = { assetBase: "/acp" };
    assertEq(env.assetUrl(null), null, "non-string paths pass through");
    assertEq(env.assetUrl({ a: 1 }), { a: 1 }, "objects pass through");
    assertEq(env.assetUrl(42), 42, "numbers pass through");
    assertEq(env.storageKey(7), 7, "non-string keys pass through");
  });

  // ── 4. late binding ──

  await test("hostenv: ACP_ENV installed after load is picked up", async () => {
    const sandbox = loadEnv(h, null);
    const env = sandbox.ACPShell.env;
    assertEq(env.apiBase(), "/api/v1");

    sandbox.ACP_ENV = { host: "dsh", assetBase: "/acp", apiBase: "/acp-api/v1", storagePrefix: "dsh:" };
    assertEq(env.apiBase(), "/acp-api/v1", "apiBase follows the late install");
    assertEq(env.assetUrl("/js/x.js"), "/acp/js/x.js");
    assertEq(env.storageKey("k"), "dsh:k");
    assertEq(env.host(), "dsh");

    sandbox.ACP_ENV = null;
    assertEq(env.apiBase(), "/api/v1", "clearing ACP_ENV returns to the defaults");
    assertEq(env.host(), "acp-web");
  });

  // ── 5. bootstrap api-base injection ──

  await test("hostenv: bootstrap client init and api() use the injected bases only", async () => {
    const sandbox = makeSandbox({
      location: { search: "" },
      fetch: function () { throw new Error("fetch is forbidden in the shell bootstrap"); },
    });
    const log = [];
    const requests = [];
    function recorder(name) {
      return {
        init(opts) { log.push({ name: name, opts: opts || {} }); },
      };
    }
    sandbox.ACPClient = {
      transport: {
        init(opts) { log.push({ name: "transport", opts: opts || {} }); },
        request(url, opts) { requests.push(url); return { url: url, opts: opts }; },
      },
      events: recorder("events"),
      taskView: recorder("taskView"),
      research: recorder("research"),
      researchCommands: recorder("researchCommands"),
      eventInvalidation: recorder("eventInvalidation"),
      jobDetail: recorder("jobDetail"),
      jobCommands: recorder("jobCommands"),
      frameCandidates: recorder("frameCandidates"),
      science: recorder("science"),
      wizardEndpoints: recorder("wizardEndpoints"),
      systemEndpoints: recorder("systemEndpoints"),
    };
    sandbox.ACPShell = { i18n: { t: function (key) { return key; } } };

    sandbox.ACP_ENV = { host: "dsh", apiBase: "/acp-api/v1", apiV2Base: "/acp-api/v2" };
    loadScript("js/shell/host-env.js", sandbox);
    loadScript("js/shell/bootstrap.js", sandbox);
    const A = sandbox.ACPShell;

    A.bootstrap._initClient();

    // Every init() that carries a baseUrl got the injected v1 base.
    const withBase = log.filter((entry) => entry.opts && entry.opts.baseUrl !== undefined);
    assertEq(withBase.map((e) => e.name).sort(), ["events", "systemEndpoints", "wizardEndpoints"],
      "all three baseUrl consumers are initialised");
    withBase.forEach((entry) => assertEq(entry.opts.baseUrl, "/acp-api/v1", entry.name + " baseUrl"));
    assertEq(log.find((e) => e.name === "events").opts.baseUrl, "/acp-api/v1", "event-stream base");

    // The injected v1/v2 callbacks resolve against the injected bases.
    const v1Entry = log.find((e) => e.name === "taskView");
    assertEq(await v1Entry.opts.v1("/jobs"), { url: "/acp-api/v1/jobs", opts: undefined });
    assertEq(await v1Entry.opts.v2("/frames"), { url: "/acp-api/v2/frames", opts: undefined });
    assertEq(await A.bootstrap._api("/jobs"), { url: "/acp-api/v1/jobs", opts: undefined });
    assertEq(await A.bootstrap._apiV2("/jobs"), { url: "/acp-api/v2/jobs", opts: undefined });
    const remote = await A.bootstrap._apiRemote("/jobs");
    assertEq(remote.url, "/acp-api/v1/jobs");
    assertEq(remote.opts.timeoutMs, 60000, "remote budget unchanged");

    requests.forEach((url) => assert(url.indexOf("/acp-api/") === 0, "no request escaped the injected base: " + url));
    requests.forEach((url) => assert(url.indexOf("/api/v1") !== 0 && url.indexOf("/api/v2") !== 0,
      "the literal Workbench bases are never used when ACP_ENV is present: " + url));

    // Without the seam (fresh bootstrap, no ACP_ENV) the literals are back.
    const plain = makeSandbox({});
    plain.ACPClient = sandbox.ACPClient;
    plain.ACPShell = { i18n: { t: function (key) { return key; } } };
    loadScript("js/shell/bootstrap.js", plain);
    assertEq((await plain.ACPShell.bootstrap._api("/jobs")).url, "/api/v1/jobs", "fallback v1 literal");
    assertEq((await plain.ACPShell.bootstrap._apiV2("/jobs")).url, "/api/v2/jobs", "fallback v2 literal");
  });

  // ── 6. applyI18n must not own documentElement.lang ──

  await test("hostenv: applyI18n with manageDocument:false translates but leaves <html lang> alone", async () => {
    const store = { "dsh:acp-lang": "en-US" };
    const winStub = { localStorage: makeStorageStub(store) };
    const docStub = makeDocument();
    const sandbox = makeSandbox({
      document: docStub,
      win: winStub,
      localStorage: makeStorageStub({}),
      CustomEvent: function CustomEvent(type, init) {
        this.type = type;
        this.detail = (init && init.detail) || {};
      },
    });
    // The shell asks win() for storage; make the global window stub carry it.
    sandbox.localStorage = winStub.localStorage;

    const root = makeDocument();
    const known = root.createElement("span");
    known.setAttribute("data-i18n", "shell.panel.reload");
    known.textContent = "old copy";
    const holder = root.createElement("div");
    holder.appendChild(known);

    sandbox.ACP_ENV = { host: "dsh", storagePrefix: "dsh:", manageDocument: false, doc: docStub, win: sandbox };
    loadScript("js/shell/host-env.js", sandbox);
    loadScript("js/shell/i18n.js", sandbox);
    const i18n = sandbox.ACPShell.i18n;

    assertEq(i18n.getLang(), "en-US", "stored language came from the namespaced key");

    i18n.applyI18n(root);
    assertEq(docStub.documentElement.lang, "zh-CN", "documentElement.lang is NOT written");
    assertEq(known.textContent, "Reload", "data-i18n nodes are still translated");

    i18n.setLang("zh-CN");
    assertEq(docStub.documentElement.lang, "zh-CN", "setLang also leaves documentElement.lang alone");
    assertEq(i18n.getLang(), "zh-CN", "language still switches");
    assertEq(store["dsh:acp-lang"], "zh-CN", "language persists under the injected prefix");

    // manageDocument false never disables the langchange event; it is dispatched
    // on the injected doc root.
    let seen = null;
    docStub.addEventListener("acp:langchange", function (event) { seen = event.detail.lang; });
    i18n.setLang("en-US");
    assertEq(seen, "en-US", "acp:langchange dispatched on the injected doc");

    // Opting back in restores the standalone write.
    sandbox.ACP_ENV = { storagePrefix: "" };
    i18n.setLang("en-US");
    assertEq(docStub.documentElement.lang, "en-US", "default host still writes documentElement.lang");
  });

  // ── 7. layout preferences storage prefix ──

  await test("hostenv: layout preferences persist under the injected storage prefix", async () => {
    const store = {};
    const winStub = { localStorage: makeStorageStub(store) };
    const sandbox = makeSandbox({});
    sandbox.ACP_ENV = { host: "dsh", storagePrefix: "dsh:", win: winStub };
    loadScript("js/shell/host-env.js", sandbox);
    loadScript("js/shell/recipes.js", sandbox);
    loadScript("js/shell/layout-preferences.js", sandbox);
    const A = sandbox.ACPShell;

    const value = A.preferences.get("molecular");
    value.sideWidth = 44;
    assertEq(A.preferences.save("molecular", value).persisted, true);

    assert(store["dsh:acp-next-layout-v2"], "layout persisted under the prefixed key");
    assert(!Object.prototype.hasOwnProperty.call(store, "acp-next-layout-v2"),
      "the raw key is never written when a prefix is set");
    const saved = JSON.parse(store["dsh:acp-next-layout-v2"]);
    assertEq(saved.version, 2);
    assertEq(saved.workspaces.molecular.sideWidth, 44);

    // Round-trip through a fresh load sees the same namespaced value.
    const again = makeSandbox({});
    again.ACP_ENV = { host: "dsh", storagePrefix: "dsh:", win: winStub };
    loadScript("js/shell/host-env.js", again);
    loadScript("js/shell/recipes.js", again);
    loadScript("js/shell/layout-preferences.js", again);
    assertEq(again.ACPShell.preferences.get("molecular").sideWidth, 44, "prefixed value is read back");

    // No seam → today's raw key, unchanged.
    const plainStore = {};
    const plain = makeSandbox({ localStorage: makeStorageStub(plainStore) });
    loadScript("js/shell/recipes.js", plain);
    loadScript("js/shell/layout-preferences.js", plain);
    assertEq(plain.ACPShell.preferences.save("molecular", plain.ACPShell.preferences.get("molecular")).persisted, true);
    assert(plainStore["acp-next-layout-v2"], "default host keeps the unprefixed key");
  });

  // ── 8. full boot against a stub DOM with the DSH seam ──

  await test("hostenv: full boot reaches data-acp-ready with manageDocument:false and injected bases", async () => {
    const doc = makeDocument();
    const dom = buildShellDom(doc);
    const store = {};
    const winStub = { localStorage: makeStorageStub(store) };
    const sandbox = makeSandbox({
      document: doc,
      localStorage: winStub.localStorage,
      location: { search: "" },
      CustomEvent: function CustomEvent(type, init) {
        this.type = type;
        this.detail = (init && init.detail) || {};
      },
      fetch: function () { throw new Error("fetch is forbidden in the shell bootstrap"); },
    });
    sandbox.ACP_ENV = {
      host: "dsh",
      assetBase: "/acp",
      apiBase: "/acp-api/v1",
      apiV2Base: "/acp-api/v2",
      storagePrefix: "dsh:",
      manageDocument: false,
      doc: doc,
      win: sandbox,
      location: { search: "" },
      homeHref: "/acp/classic",
    };
    loadScript("js/shell/host-env.js", sandbox);
    SHELL_SCRIPTS.forEach((rel) => loadScript(rel, sandbox));
    const A = sandbox.ACPShell;

    const applied = A.bootstrap.boot();
    assertEq(applied.ok, true, JSON.stringify(applied));
    await flush();

    assertEq(dom.shell.getAttribute("data-acp-ready"), "true", "shell reports ready through the injected doc");
    assertEq(doc.title, "", "a manageDocument:false host keeps its own title");
    assertEq(doc.documentElement.lang, "zh-CN", "documentElement.lang is never written");

    // Workspace switches also leave the title alone and still work.
    A.layout.switchWorkspace("molecular");
    await flush(2);
    assertEq(A.layout.getCurrentWorkspace(), "molecular");
    assertEq(doc.title, "", "title still untouched after a workspace switch");

    // The collapse preference is namespaced, not written raw.
    const collapseKeys = Object.keys(store).filter((k) => k.indexOf("acp-next-collapse") >= 0);
    collapseKeys.forEach((k) => assertEq(k.indexOf("dsh:"), 0, "collapse key is namespaced: " + k));
    const layoutKeys = Object.keys(store).filter((k) => k.indexOf("acp-next-layout") >= 0);
    layoutKeys.forEach((k) => assertEq(k.indexOf("dsh:"), 0, "layout key is namespaced: " + k));
    assertEq(A.bootstrap.isStarted(), true);
  });
};
