/*
 * node:test coverage for the ACP x DSH browser half.
 *
 * Run with the bundled node:
 *   node --test frontend/dsh-plugin/test/client.test.mjs
 *
 * Everything here is a fake environment: no DSH host, no browser, no ACP
 * controller. The tests prove the plugin's own contracts (ModuleLoader bundle
 * shape, slot registrations, mount/unmount state machine) and nothing about
 * real DSH rendering.
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

import { REQUIRED_SHELL_IDS } from "./shell-ids.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLIENT_SRC = path.join(HERE, "..", "client", "client.js");
const MOUNT_SRC = path.join(HERE, "..", "client", "mount.js");

/* The fixture skeleton is built from the runtime's own contract
 * (client/mount.js REQUIRED_IDS) instead of a second hand-kept list, so the
 * fake DOM can never drift from the shell ids mount() actually demands. */
const REQUIRED_IDS = REQUIRED_SHELL_IDS;

const BANNER_ATTR = "data-acp-dsh-banner";
const CSS_MARKER = "data-acp-dsh-css";

const DEFAULT_CONFIG = {
  uiBase: "/acp",
  apiPrefix: "/acp-api",
  readOnly: true,
  controllerAvailable: true,
  focusEndpoint: "/acp-api/ui/focus",
  healthEndpoint: "/acp-api/health",
  assetBase: "/acp"
};

/* ------------------------------------------------------------ fake DOM */

/* Minimal CSSStyleDeclaration: enough for setProperty/removeProperty, which is
 * all the rail uses on document.documentElement. */
function makeStyleObject() {
  const declared = {};
  return {
    setProperty(name, value) {
      declared[name] = String(value);
    },
    getPropertyValue(name) {
      return Object.prototype.hasOwnProperty.call(declared, name) ? declared[name] : "";
    },
    removeProperty(name) {
      delete declared[name];
      return "";
    },
    __declared: declared
  };
}

function createFakeDocument() {
  const byId = new Map();
  const all = [];
  let autoLoad = null;

  function makeElement(tagName) {
    const el = {
      tagName: String(tagName || "div").toUpperCase(),
      children: [],
      attributes: {},
      className: "",
      textContent: "",
      parentNode: null,
      style: {},
      appendChild(child) {
        if (!child) {
          return child;
        }
        /* Real DOM semantics: appending a parented node moves it. */
        if (child.parentNode && child.parentNode !== el
          && typeof child.parentNode.removeChild === "function") {
          child.parentNode.removeChild(child);
        }
        child.parentNode = el;
        el.children.push(child);
        if (child.tagName === "SCRIPT" && typeof autoLoad === "function") {
          const loader = autoLoad;
          setTimeout(() => {
            loader(child);
          }, 0);
        }
        return child;
      },
      insertBefore(child, reference) {
        child.parentNode = el;
        const at = el.children.indexOf(reference);
        if (at < 0) {
          el.children.push(child);
        } else {
          el.children.splice(at, 0, child);
        }
        return child;
      },
      removeChild(child) {
        const at = el.children.indexOf(child);
        if (at >= 0) {
          el.children.splice(at, 1);
          child.parentNode = null;
        }
        return child;
      },
      setAttribute(name, value) {
        el.attributes[name] = String(value);
        if (name === "id") {
          byId.set(String(value), el);
        }
      },
      getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(el.attributes, name) ? el.attributes[name] : null;
      },
      removeAttribute(name) {
        delete el.attributes[name];
      },
      insertAdjacentHTML(_position, html) {
        const text = String(html);
        const re = /id="([^"]+)"/g;
        let match = re.exec(text);
        while (match !== null) {
          const node = makeElement("div");
          node.setAttribute("id", match[1]);
          node.attributes["data-skeleton"] = "1";
          node.parentNode = el;
          el.children.push(node);
          match = re.exec(text);
        }
      },
      remove() {
        if (el.parentNode) {
          el.parentNode.removeChild(el);
        }
      }
    };
    all.push(el);
    return el;
  }

  const head = makeElement("head");
  const body = makeElement("body");

  function isAttached(el) {
    let node = el;
    while (node) {
      if (node === head || node === body) {
        return true;
      }
      node = node.parentNode;
    }
    return false;
  }

  const doc = {
    head,
    body,
    documentElement: Object.assign(makeElement("html"), { lang: "en", style: makeStyleObject() }),
    createElement: (tagName) => makeElement(tagName),
    getElementById: (id) => byId.get(String(id)) || null,
    querySelectorAll: (selector) => {
      const match = /^\[([^\]="]+)(?:="([^"]*)")?\]$/.exec(String(selector));
      if (!match) {
        return [];
      }
      const name = match[1];
      const value = match[2];
      return all.filter((el) => isAttached(el)
        && Object.prototype.hasOwnProperty.call(el.attributes, name)
        && (value === undefined || el.attributes[name] === String(value)));
    },
    __setAutoLoad(fn) {
      autoLoad = fn;
    },
    __byId: byId,
    __all: all
  };

  return doc;
}

function makeSkeleton(omit = []) {
  const parts = ["<svg data-acp-sprite=\"1\"></svg>"];
  for (const id of REQUIRED_IDS) {
    if (omit.includes(id)) {
      continue;
    }
    parts.push(`<div id="${id}"></div>`);
  }
  parts.push("<button id=\"acp-tab-jobs\"></button>");
  parts.push("<section id=\"acp-slot-research-main\"></section>");
  return parts.join("");
}

/* --------------------------------------------------------- fake runtime */

function createFakeLoader() {
  const registry = new Map();
  let loadCalls = 0;
  return {
    load(entry) {
      loadCalls += 1;
      if (!entry || typeof entry.id !== "string" || typeof entry.factory !== "function") {
        throw new Error("invalid ModuleLoader entry");
      }
      if (registry.has(entry.id)) {
        return registry.get(entry.id);
      }
      registry.set(entry.id, entry);
      return entry;
    },
    __registry: registry,
    get loadCalls() {
      return loadCalls;
    }
  };
}

function createFakeReact() {
  const created = [];
  const hookSlots = [];
  const effects = [];
  let cursor = 0;

  const api = {
    createElement(type, props, ...children) {
      const el = { type, props: { ...(props || {}) }, children };
      created.push(el);
      return el;
    },
    useRef(initial) {
      const index = cursor++;
      if (!hookSlots[index]) {
        hookSlots[index] = { current: initial === undefined ? null : initial };
      }
      return hookSlots[index];
    },
    useState(initial) {
      const index = cursor++;
      if (!hookSlots[index]) {
        hookSlots[index] = { value: typeof initial === "function" ? initial() : initial };
      }
      const slot = hookSlots[index];
      return [slot.value, (next) => {
        slot.value = typeof next === "function" ? next(slot.value) : next;
      }];
    },
    useEffect(fn) {
      const index = cursor++;
      effects.push({ index, fn });
      return undefined;
    },
    useLayoutEffect(fn) {
      return api.useEffect(fn);
    },
    __render(Component, props = {}) {
      cursor = 0;
      created.length = 0;
      effects.length = 0;
      const out = Component(props);
      return { out, created, effects };
    },
    __reset() {
      cursor = 0;
      created.length = 0;
      effects.length = 0;
      hookSlots.length = 0;
    }
  };
  return api;
}

function createFakeCtx(options = {}) {
  const registrations = [];
  const effects = [];
  const disposers = [];
  const injected = [];
  let effectDepth = 0;
  const declared = new Set();
  const pending = new Map();
  const tabTypes = [];
  const opened = [];
  const mountedListeners = [];
  const closeHandlers = new Map();
  let mountedSession = options.mounted === undefined ? null : options.mounted;
  const openTabs = { list: options.openTabs || [] };
  const mounted = {
    getSnapshot: () => mountedSession,
    subscribe(fn) {
      mountedListeners.push(fn);
      return () => {
        const at = mountedListeners.indexOf(fn);
        if (at >= 0) mountedListeners.splice(at, 1);
      };
    }
  };
  function declare(key) {
    declared.add(key);
    if (pending.has(key)) pending.get(key)();
  }

  const ctx = {
    sidebarRightTabs: { register(def) { tabTypes.push(def); return () => {}; } },
    sidebarRight: {
      openResource(address) {
        opened.push(address);
        if (typeof options.openResource === "function") return options.openResource(address);
        return { ok: true };
      },
      mounted,
      openTabs: { getSnapshot: () => openTabs.list },
      registerCloseHandler(id, fn) {
        closeHandlers.set(id, fn);
        return () => { closeHandlers.delete(id); };
      }
    },
    effect(fn) {
      effects.push(fn);
      effectDepth += 1;
      let disposer;
      try {
        disposer = fn();
      } finally {
        effectDepth -= 1;
      }
      if (typeof disposer === "function") {
        disposers.push(disposer);
      }
      return disposer;
    },
    slots: {
      inject(key, callback) {
        injected.push(key);
        pending.set(key, callback);
        if (options.delayed) return () => pending.delete(key);
        declared.add(key);
        const disposer = callback();
        return typeof disposer === "function" ? disposer : () => {};
      },
      register(opts, component) {
        assert.ok(declared.has(opts.name), "slot must be declared before registration");
        if (opts.name.startsWith("sidebar.right.pane.tab")) assert.equal(typeof opts.key, "string", "keyed slot requires options.key");
        const disposer = () => {};
        registrations.push({ opts, component, disposer, inEffect: effectDepth > 0 });
        return disposer;
      }
    }
  };

  return { ctx, registrations, effects, disposers, injected, declare, tabTypes, opened,
    mountedListeners, closeHandlers, openTabs,
    emitMounted(sid) {
      mountedSession = sid;
      for (const fn of mountedListeners.slice()) fn(sid);
    } };
}

/* ------------------------------------------------------- fake ACP parts */

function createFakeAdapters(log) {
  return {
    dsh: {
      env: {
        install(win, opts) {
          log.push({ step: "env.install", opts });
          const env = {
            apiPrefix: opts.apiPrefix,
            assetBase: opts.assetBase,
            sessionId: opts.sessionId,
            generation: opts.generation,
            focusEndpoint: opts.focusEndpoint,
            storagePrefix: opts.storagePrefix
          };
          win.ACP_ENV = env;
          return env;
        }
      },
      baseUrl: {
        installAll(win, opts) {
          log.push({ step: "baseUrl.installAll", opts });
          return true;
        }
      },
      restrictedClient: {
        create(client, opts) {
          log.push({ step: "restrictedClient.create", allowWrite: opts ? opts.allowWrite : null, source: client });
          return {
            transport: client.transport,
            readOnly: true,
            isRestrictedFacade: true
          };
        },
        describe(client) {
          log.push({ step: "restrictedClient.describe", client });
          return { readOnly: true, namespaces: ["jobs", "science"], writeNamespacesPresent: [] };
        },
        assertReadOnly() {
          return true;
        }
      },
      theme: {
        linkStyles(doc, opts) {
          log.push({ step: "theme.linkStyles", opts });
          const link = doc.createElement("link");
          link.setAttribute(opts.marker, "1");
          link.setAttribute("rel", "stylesheet");
          link.setAttribute("href", opts.base + opts.hrefs[0]);
          doc.head.appendChild(link);
          return [link];
        },
        apply(doc, opts) {
          log.push({ step: "theme.apply", opts });
          return true;
        }
      },
      focusBridge: {
        create(opts) {
          log.push({ step: "focusBridge.create", opts });
          return {
            publish() {},
            attach(context) {
              log.push({ step: "focusBridge.attach", context });
            },
            detach() {
              log.push({ step: "focusBridge.detach" });
            },
            pending() {
              return 0;
            },
            stats() {
              return { published: 0 };
            }
          };
        }
      }
    }
  };
}

function createFakeShell(log, doc, onBoot) {
  return {
    context: { subscribe() { return () => {}; } },
    bootstrap: {
      boot() {
        onBoot();
        log.push({ step: "boot" });
        const shellEl = doc.getElementById("acp-shell");
        if (shellEl) {
          shellEl.setAttribute("data-acp-ready", "true");
        }
        return true;
      }
    },
    loader: { setResolver() {} },
    i18n: {},
    registry: {},
    layout: {}
  };
}

function createFakeClient(log) {
  return {
    transport: {
      request() {
        return Promise.resolve({ ok: true });
      }
    },
    events: {
      closeStream() {
        log.push({ step: "events.closeStream" });
      }
    }
  };
}

function createFakeEnv(options = {}) {
  const doc = createFakeDocument();
  const log = [];
  const storage = new Map();
  let bootCount = 0;

  const win = {
    document: doc,
    sessionStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => {
        storage.set(key, String(value));
      },
      removeItem: (key) => {
        storage.delete(key);
      }
    },
    setTimeout,
    clearTimeout,
    __ACP_PANELS_CONFIG__: { ...DEFAULT_CONFIG, ...(options.config || {}) },
    fetch: options.fetch || (() => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ok: true })
    }))
  };

  if (options.config === null) {
    delete win.__ACP_PANELS_CONFIG__;
  }

  const originalClient = createFakeClient(log);
  win.ACPClient = originalClient;

  if (options.skeleton !== null) {
    win.ACP_DSH_SKELETON = options.skeleton || makeSkeleton();
  }

  win.ACPAdapters = createFakeAdapters(log);

  if (options.shell !== false) {
    win.ACPShell = createFakeShell(log, doc, () => {
      bootCount += 1;
    });
  }

  const container = doc.createElement("div");
  doc.body.appendChild(container);

  /* Opt-in private runtime scope, mirroring the real ACPDSHScope seam. This
   * exercises the focus-lease (CAS acquire) branch of attachBridge. */
  let scopeWindow = null;
  if (options.scope) {
    win.ACPDSHScope = {
      create(base) {
        const scoped = Object.create(base);
        scoped.document = base.document;
        scoped.pause = () => {};
        scoped.resume = () => {};
        scoped.dispose = () => {};
        scopeWindow = scoped;
        return { window: scoped, pause() {}, resume() {}, dispose() {} };
      }
    };
    win.ACP_DSH_CREATE_RUNTIME = function () {};
  }

  return {
    win,
    doc,
    log,
    container,
    originalClient,
    bootCount: () => bootCount,
    storage,
    scopeWindow: () => scopeWindow
  };
}

function stepEntries(log, step) {
  return log.filter((entry) => entry.step === step);
}

/* ------------------------------------------------------- script loading */

function runClientScript(fakeWin) {
  const sandbox = { window: fakeWin, setTimeout, clearTimeout, console };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(CLIENT_SRC, "utf8"), sandbox, { filename: "client.js" });
  return sandbox;
}

function materializeClient(fakeWin, fakeReact) {
  const loader = createFakeLoader();
  fakeWin.__ModuleLoader__ = loader;
  runClientScript(fakeWin);
  const entry = loader.__registry.get("@acp/dsh-panels");
  const requireCalls = [];
  const mod = entry.factory((specifier) => {
    requireCalls.push(specifier);
    return fakeReact;
  });
  return { loader, entry, mod, requireCalls };
}

/* Browser-ish window for the rail tests: fake DOM, localStorage, innerWidth
 * and a listener registry so pointer listeners can be asserted. */
function createFakeWindow(options = {}) {
  const doc = createFakeDocument();
  const storage = new Map(Object.entries(options.storage || {}));
  const listeners = new Map();
  const win = {
    document: doc,
    innerWidth: options.innerWidth || 1440,
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => {
        storage.set(key, String(value));
      },
      removeItem: (key) => {
        storage.delete(key);
      }
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) {
        listeners.set(type, []);
      }
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const bucket = listeners.get(type);
      if (!bucket) {
        return;
      }
      const at = bucket.indexOf(fn);
      if (at >= 0) {
        bucket.splice(at, 1);
      }
    },
    setTimeout,
    clearTimeout,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) })
  };
  Object.defineProperty(win, "__storage", { value: storage, enumerable: false });
  Object.defineProperty(win, "__listeners", { value: listeners, enumerable: false });
  Object.defineProperty(win, "__emit", {
    value(type, event) {
      const bucket = (listeners.get(type) || []).slice();
      for (const fn of bucket) {
        fn(event);
      }
      return bucket.length;
    },
    enumerable: false
  });
  return win;
}

function installFakeRuntime(win) {
  const mountCalls = [];
  const unmountCalls = [];
  const reassociateCalls = [];
  let reassociateImpl = null;
  win.ACPDSH = {
    mount(el, opts) {
      mountCalls.push({ el, opts });
      return Promise.resolve({ ok: true, degraded: false });
    },
    unmount(...args) {
      unmountCalls.push(args);
      return { ok: true, disposed: [] };
    },
    reassociate(sessionId) {
      reassociateCalls.push(sessionId);
      if (typeof reassociateImpl === "function") return reassociateImpl(sessionId);
      return { ok: true, changed: true, sessionId };
    },
    state() {
      return {};
    },
    version: "0.1.0"
  };
  return {
    mountCalls,
    unmountCalls,
    reassociateCalls,
    setReassociate(fn) {
      reassociateImpl = fn;
    }
  };
}

function registrationByName(fake, name) {
  return fake.registrations.find((registration) => registration.opts.name === name);
}

function elementsWith(elements, prop) {
  return elements.filter((el) => el.props && el.props[prop] !== undefined);
}

/* Render one component, set the ACP host ref (when present) and run every
 * effect once, exactly like React would on mount. */
function renderRail(react, Component) {
  const rendered = react.__render(Component);
  const elements = rendered.created.slice();
  const host = elements.find((el) => el.props && el.props.className === "acp-dsh-host");
  const container = { kind: "container" };
  if (host && host.props.ref) {
    host.props.ref.current = container;
  }
  const cleanups = [];
  for (const effect of rendered.effects) {
    const cleanup = effect.fn();
    if (typeof cleanup === "function") {
      cleanups.push(cleanup);
    }
  }
  return { out: rendered.out, elements, effects: rendered.effects.slice(), container, cleanups };
}

function loadMountRuntime(win) {
  const sandbox = { window: win, setTimeout, clearTimeout, console };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(MOUNT_SRC, "utf8"), sandbox, { filename: "mount.js" });
  return win.ACPDSH;
}

function stripComments(source) {
  return String(source)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'])\/\/[^\n]*/g, "$1");
}

/* Values produced inside a vm context carry that realm's prototypes, which
 * deepStrictEqual rejects. Normalise them before structural comparison.
 */
function plain(value) {
  if (value === undefined) {
    return undefined;
  }
  return JSON.parse(JSON.stringify(value));
}

/* =========================================================== client.js */

test("client.js registers exactly one bundle and is idempotent on reload", () => {
  const fakeWin = { document: undefined };
  const loader = createFakeLoader();
  fakeWin.__ModuleLoader__ = loader;

  runClientScript(fakeWin);
  assert.equal(loader.loadCalls, 1);
  assert.deepEqual([...loader.__registry.keys()], ["@acp/dsh-panels"]);

  assert.doesNotThrow(() => runClientScript(fakeWin));
  assert.equal(loader.loadCalls, 2);
  assert.deepEqual([...loader.__registry.keys()], ["@acp/dsh-panels"]);

  const entry = loader.__registry.get("@acp/dsh-panels");
  assert.equal(typeof entry.factory, "function");
  assert.equal(typeof entry.factory, "function");
});

test("factory touches no DOM, installs no global and only requires react", () => {
  const trappingDocument = new Proxy({}, {
    get() {
      throw new Error("factory touched document");
    }
  });
  const fakeWin = { document: trappingDocument };
  const loader = createFakeLoader();
  fakeWin.__ModuleLoader__ = loader;
  runClientScript(fakeWin);

  const entry = loader.__registry.get("@acp/dsh-panels");
  const keysBefore = Object.keys(fakeWin).sort().join(",");
  const fakeReact = createFakeReact();
  const requireCalls = [];

  const mod = entry.factory((specifier) => {
    requireCalls.push(specifier);
    return fakeReact;
  });

  assert.deepEqual(requireCalls, ["react"]);
  assert.deepEqual([...new Set(requireCalls)], ["react"]);
  assert.deepEqual(Object.keys(mod).sort(), ["apply", "inject"]);
  assert.deepEqual([...mod.inject], ["slots", "sidebarRightTabs", "sidebarRight"]);
  assert.equal(typeof mod.apply, "function");

  assert.equal(Object.keys(fakeWin).sort().join(","), keysBefore);
  assert.equal(fakeWin.ACPDSH, undefined);
  assert.equal(globalThis.ACPDSH, undefined);
});

test("native ACP type registers real keyed body and header without an overlay", () => {
  const react = createFakeReact(), win = createFakeWindow();
  const { mod } = materializeClient(win, react), fake = createFakeCtx(); mod.apply(fake.ctx);
  assert.deepEqual(fake.registrations.map(r=>r.opts.name), ["sidebar.right.pane.tab","sidebar.right.pane.tab.title","conversation.session.header.utilities"]);
  assert.equal(fake.tabTypes[0].keepMounted, true);
  assert.equal(fake.tabTypes[0].id, fake.registrations[0].opts.key);
  assert.equal(fake.tabTypes[0].canOpen("dsh-resource://acp-workbench/session-1"),true);
  assert.equal(fake.tabTypes[0].canOpen("https://other"),false);
  for (const r of fake.registrations) assert.equal(r.inEffect,true);
  assert.equal(fake.injected.includes("shell.overlay"), false, "the window workbench must stay a native tab");
  assert.deepEqual([...fake.injected], ["sidebar.right.pane.tab","sidebar.right.pane.tab.title","conversation.session.header.utilities"]);
  assert.equal(fake.mountedListeners.length, 1, "pinned docking subscribes to sidebarRight.mounted");
  assert.ok(fake.closeHandlers.has("acp-workbench"), "closing the tab must be observable");
  assert.equal(fs.readFileSync(CLIENT_SRC,"utf8").includes("shell.overlay"), false);
  const css=fs.readFileSync(path.join(HERE,"../client/host.css"),"utf8");
  assert.equal(css.includes("data-rightbar-col"),false);
  assert.equal(css.includes("padding-right"),false);
});

test("late native slots register when declared and entry navigates the actual session", () => {
  const react=createFakeReact(), win=createFakeWindow();
  const {mod}=materializeClient(win,react), fake=createFakeCtx({delayed:true});mod.apply(fake.ctx);
  assert.equal(fake.registrations.length,0);
  for(const slot of ["sidebar.right.pane.tab","sidebar.right.pane.tab.title","conversation.session.header.utilities"]) fake.declare(slot);
  const Header=registrationByName(fake,"conversation.session.header.utilities").component;
  react.__render(Header,{sessionId:"session with space"}).out.props.onClick();
  assert.deepEqual(fake.opened,["dsh-resource://acp-workbench/session%20with%20space"]);
  assert.equal(react.__render(Header,{}).out.props.disabled,true);
});

function nativeBody(react, Component, sessionId, visible, container) {
  const rendered=react.__render(Component,{sessionId,useTabInfo:()=>({tab:{visible}})});
  const host=rendered.created.find(e=>e.props.className==="acp-dsh-mount");host.props.ref.current=container;
  const cleanups=rendered.effects.map(e=>e.fn()).filter(x=>typeof x==="function");
  return {rendered,cleanups};
}

/* Depth-first lookup of the first node carrying the given attribute. */
function findAttr(node, name) {
  if (!node || typeof node.getAttribute !== "function") return null;
  if (node.getAttribute(name) !== null) return node;
  for (const child of node.children || []) {
    const found = findAttr(child, name);
    if (found) return found;
  }
  return null;
}

test("native tab passes real session and never mounts hidden bodies", async () => {
  const react=createFakeReact(),win=createFakeWindow(),runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react),fake=createFakeCtx();mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const doc=win.document;
  const hiddenContainer=doc.createElement("div");
  nativeBody(react,Body,"real-1",false,hiddenContainer);
  assert.equal(runtime.mountCalls.length,0);
  assert.equal(hiddenContainer.children.length,0,"a hidden body must not create the workbench root");
  const shownContainer=doc.createElement("div");
  const shown=nativeBody(react,Body,"real-1",true,shownContainer);await Promise.resolve();
  assert.equal(runtime.mountCalls.length,1);
  assert.equal(runtime.mountCalls[0].opts.sessionId,"real-1");
  assert.equal(shownContainer.children.length,1);
  assert.equal(shownContainer.children[0].getAttribute("data-acp-workbench-root"),"1");
  for(const cleanup of shown.cleanups.reverse()) cleanup();
  assert.equal(shownContainer.children.length,0,"closing the native tab detaches the root");
  assert.equal(runtime.unmountCalls.length,0,"closing the tab must not unmount the runtime");
  fake.disposers.reverse().forEach(fn=>fn());
  assert.ok(runtime.unmountCalls.length>0,"the plugin disposer releases the runtime");
});

test("first attach creates one root and mounts the persisted linked session once", async () => {
  const react=createFakeReact();
  const win=createFakeWindow({storage:{"acp-dsh:workbench.linked":"linked-session"}});
  const runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react),fake=createFakeCtx({mounted:"docked-session"});mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const container=win.document.createElement("div");
  const body=nativeBody(react,Body,"docked-session",true,container);
  await Promise.resolve();
  assert.equal(runtime.mountCalls.length,1);
  assert.equal(runtime.mountCalls[0].opts.sessionId,"linked-session","mount binds the linked research session");
  const root=container.children[0];
  assert.equal(root.getAttribute("data-acp-workbench-linked"),"linked-session");
  assert.equal(root.getAttribute("data-acp-workbench-docked"),"docked-session");
  const diff=findAttr(root,"data-acp-workbench-diff");
  assert.ok(diff&&diff.textContent.includes("linked-session".slice(-6)),"the bar explains the link difference");
  body.cleanups.forEach(fn=>fn());
});

test("the session capsule is docked into the ACP topbar, never a second title bar", async () => {
  const react=createFakeReact();
  const win=createFakeWindow();
  const doc=win.document;
  const topbar=doc.createElement("header");
  topbar.setAttribute("class","acp-shell-topbar");
  const slot=doc.createElement("div");
  slot.setAttribute("id","acp-dsh-session-slot");
  slot.closest=(selector)=>selector===".acp-shell-topbar"?topbar:null;
  topbar.appendChild(slot);
  doc.body.appendChild(topbar);
  const {mod}=materializeClient(win,react),fake=createFakeCtx({mounted:"conv-top"});mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const container=doc.createElement("div");
  const body=nativeBody(react,Body,"conv-top",true,container);
  await Promise.resolve(); await Promise.resolve();
  const root=container.children[0];
  assert.ok(root&&root.getAttribute("data-acp-workbench-root")==="1");
  assert.equal(findAttr(root,"data-acp-workbench-bar"),null,"the workbench root keeps no second bar row");
  const bar=slot.children[0];
  assert.ok(bar&&String(bar.className).includes("acp-dsh-workbench-bar"),"the single session capsule moved into the topbar slot");
  assert.equal(findAttr(bar,"data-acp-workbench-title"),null,"no duplicated ACP workbench title");
  assert.ok(findAttr(bar,"data-acp-workbench-linked-info"),"the linked conversation remains visible");
  assert.ok(findAttr(bar,"data-acp-workbench-mode-button"),"pin/follow remains in the capsule");
  body.cleanups.forEach(fn=>fn());
});

test("switching conversations moves the same root and never remounts", async () => {
  const react=createFakeReact(),win=createFakeWindow(),runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react),fake=createFakeCtx({mounted:"conv-A"});mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const doc=win.document,a=doc.createElement("div"),b=doc.createElement("div");
  nativeBody(react,Body,"conv-A",true,a);await Promise.resolve();
  const root=a.children[0];
  assert.ok(root&&root.getAttribute("data-acp-workbench-root")==="1");
  assert.equal(runtime.mountCalls.length,1);
  react.__reset();
  nativeBody(react,Body,"conv-B",true,b);await Promise.resolve();
  assert.equal(runtime.mountCalls.length,1,"switching conversations must not remount the runtime");
  assert.equal(b.children[0],root,"the same DOM node object moves between native bodies");
  assert.equal(root.parentNode,b);
  assert.equal(a.children.length,0,"the old native body no longer owns the root");
});

test("a late cleanup from the previous body leaves the new attachment intact", async () => {
  const react=createFakeReact(),win=createFakeWindow(),runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react),fake=createFakeCtx();mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const doc=win.document,a=doc.createElement("div"),b=doc.createElement("div");
  const first=nativeBody(react,Body,"conv-A",true,a);await Promise.resolve();
  const root=a.children[0];
  react.__reset();
  const second=nativeBody(react,Body,"conv-B",true,b);await Promise.resolve();
  assert.equal(b.children[0],root);
  first.cleanups.forEach(fn=>fn());
  assert.equal(root.parentNode,b,"a late old cleanup must never move the new attachment");
  assert.equal(b.children[0],root);
  assert.equal(runtime.mountCalls.length,1);
  second.cleanups.forEach(fn=>fn());
});

test("pinned auto-docks a conversation without a workbench tab only once", () => {
  const react=createFakeReact(),win=createFakeWindow();
  const {mod}=materializeClient(win,react);
  const fake=createFakeCtx({mounted:"conv-1"});
  mod.apply(fake.ctx);
  assert.deepEqual(fake.opened,["dsh-resource://acp-workbench/conv-1"],"the first load docks the current conversation");
  fake.emitMounted("conv-2");
  assert.deepEqual(fake.opened.slice(1),["dsh-resource://acp-workbench/conv-2"]);
  assert.equal(new Set(fake.opened).size,2);
});

test("an existing workbench tab is never reopened and closing suppresses auto-dock", () => {
  const react=createFakeReact(),win=createFakeWindow();
  const {mod}=materializeClient(win,react);
  const fake=createFakeCtx({mounted:"conv-1",openTabs:[{sessionId:"conv-1",kind:"acp-workbench"}]});
  mod.apply(fake.ctx);
  assert.equal(fake.opened.length,0,"an already docked tab must not be reopened or refocused");
  fake.emitMounted("conv-2");
  assert.equal(fake.opened.length,1);

  const win2=createFakeWindow();
  const react2=createFakeReact();
  const {mod:mod2}=materializeClient(win2,react2);
  const closed=createFakeCtx({mounted:"conv-1"});
  mod2.apply(closed.ctx);
  assert.equal(closed.opened.length,1);
  closed.closeHandlers.get("acp-workbench")();
  assert.equal(win2.localStorage.getItem("acp-dsh:workbench.intent"),"0");
  closed.emitMounted("conv-2");
  assert.equal(closed.opened.length,1,"a closed workbench must not be reopened by a conversation switch");
});

test("auto-dock retries the bounded no-surface failure and then succeeds", async () => {
  let calls=0;
  const react=createFakeReact(),win=createFakeWindow();
  const {mod}=materializeClient(win,react);
  const fake=createFakeCtx({mounted:"conv-retry",openResource(address){
    calls+=1;
    if(calls===1) throw new Error("sidebarRight: no session surface is mounted");
    return {ok:true,address};
  }});
  mod.apply(fake.ctx);
  assert.equal(calls,1);
  await new Promise((resolve)=>setTimeout(resolve,200));
  assert.equal(calls,2,"the bounded 80ms retry runs once the surface exists");
  assert.deepEqual(fake.opened,["dsh-resource://acp-workbench/conv-retry","dsh-resource://acp-workbench/conv-retry"]);
});

test("a pending auto-dock retry survives the previous tab cleanup", async () => {
  let calls=0;
  const react=createFakeReact(),win=createFakeWindow(),runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react);
  const fake=createFakeCtx({mounted:"conv-A",openResource(address){
    calls+=1;
    if(calls===2) throw new Error("sidebarRight: no session surface is mounted");
    return {ok:true,address};
  }});
  mod.apply(fake.ctx);
  assert.equal(calls,1);
  const container=win.document.createElement("div");
  const body=nativeBody(react,registrationByName(fake,"sidebar.right.pane.tab").component,"conv-A",true,container);
  await Promise.resolve();
  fake.emitMounted("conv-B");
  assert.equal(calls,2);
  body.cleanups.forEach(fn=>fn());
  assert.equal(runtime.unmountCalls.length,0);
  await new Promise((resolve)=>setTimeout(resolve,200));
  assert.equal(calls,3,"the dock retry must not be cancelled by the old tab cleanup");
  assert.equal(fake.opened.at(-1),"dsh-resource://acp-workbench/conv-B");
});

test("pinned browsing keeps the linked session until it is explicitly linked", async () => {
  const react=createFakeReact(),win=createFakeWindow(),runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react),fake=createFakeCtx({mounted:"conv-A"});mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const doc=win.document,a=doc.createElement("div"),b=doc.createElement("div");
  const bodyA=nativeBody(react,Body,"conv-A",true,a);await Promise.resolve();
  const root=a.children[0];
  react.__reset();
  const bodyB=nativeBody(react,Body,"conv-B",true,b);await Promise.resolve();
  assert.equal(runtime.mountCalls.length,1);
  assert.equal(root.getAttribute("data-acp-workbench-linked"),"conv-A","pinned keeps the linked research session");
  assert.equal(root.getAttribute("data-acp-workbench-docked"),"conv-B");
  const diff=findAttr(root,"data-acp-workbench-diff");
  assert.equal(diff.style.display,"","the bar shows the browsing/link difference");
  assert.ok(diff.textContent.includes("conv-B".slice(-6)));
  const associate=findAttr(root,"data-acp-workbench-associate");
  assert.equal(associate.style.display,"");
  associate.onclick();
  await Promise.resolve();await Promise.resolve();
  assert.deepEqual(runtime.reassociateCalls,["conv-B"]);
  assert.equal(root.getAttribute("data-acp-workbench-linked"),"conv-B");
  assert.equal(win.localStorage.getItem("acp-dsh:workbench.linked"),"conv-B");
  assert.equal(findAttr(root,"data-acp-workbench-diff").style.display,"none");
  bodyA.cleanups.forEach(fn=>fn());
  bodyB.cleanups.forEach(fn=>fn());
});

test("mode switching persists and closing only detaches the runtime", async () => {
  const react=createFakeReact(),win=createFakeWindow(),runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react),fake=createFakeCtx();mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const container=win.document.createElement("div");
  const body=nativeBody(react,Body,"conv-A",true,container);await Promise.resolve();
  const root=container.children[0];
  const mode=findAttr(root,"data-acp-workbench-mode-button");
  mode.onclick();
  assert.equal(root.getAttribute("data-acp-workbench-mode"),"follow");
  assert.equal(win.localStorage.getItem("acp-dsh:workbench.mode"),"follow");
  mode.onclick();
  assert.equal(root.getAttribute("data-acp-workbench-mode"),"pinned");
  assert.equal(win.localStorage.getItem("acp-dsh:workbench.mode"),"pinned");
  assert.equal(runtime.unmountCalls.length,0);
  fake.closeHandlers.get("acp-workbench")();
  assert.equal(win.localStorage.getItem("acp-dsh:workbench.intent"),"0");
  assert.equal(root.getAttribute("data-acp-workbench-intent"),"closed");
  body.cleanups.forEach(fn=>fn());
  assert.equal(container.children.length,0);
  assert.equal(runtime.unmountCalls.length,0,"closing the native tab only detaches");
  fake.disposers.reverse().forEach(fn=>fn());
  assert.equal(runtime.unmountCalls.length,1,"the plugin disposer is the only unmount path");
});

test("the window workbench never writes host geometry or a second root", async () => {
  const react=createFakeReact(),win=createFakeWindow(),runtime=installFakeRuntime(win);
  const {mod}=materializeClient(win,react),fake=createFakeCtx({mounted:"conv-A"});mod.apply(fake.ctx);
  const Body=registrationByName(fake,"sidebar.right.pane.tab").component;
  const doc=win.document,a=doc.createElement("div"),b=doc.createElement("div");
  const first=nativeBody(react,Body,"conv-A",true,a);await Promise.resolve();
  react.__reset();
  const second=nativeBody(react,Body,"conv-B",true,b);await Promise.resolve();
  const roots=doc.__all.filter(el=>el.attributes["data-acp-workbench-root"]==="1");
  assert.equal(roots.length,1,"exactly one ACP workbench root exists");
  assert.deepEqual(Object.keys(doc.documentElement.style.__declared),[],"no host CSS variables are written");
  assert.equal(doc.body.children.filter(el=>el.className==="acp-dsh-workbench").length,0);
  assert.equal(runtime.mountCalls.length,1);
  const source=fs.readFileSync(CLIENT_SRC,"utf8");
  assert.equal(source.includes("document.body"),false);
  assert.equal(source.includes("documentElement.style"),false);
  assert.equal(source.includes("setProperty"),false);
  const css=fs.readFileSync(path.join(HERE,"../client/host.css"),"utf8");
  assert.equal(css.includes("padding-right"),false);
  assert.equal(css.includes("data-rightbar-col"),false);
  first.cleanups.forEach(fn=>fn());
  second.cleanups.forEach(fn=>fn());
});

test("native tab probes late runtime and cancels the pending retry on removal", () => {
  const timers=new Map();let seq=0,entry;
  const win=createFakeWindow(),react=createFakeReact();
  win.__ModuleLoader__={load(v){entry=v;}};
  vm.runInNewContext(fs.readFileSync(CLIENT_SRC,"utf8"),{window:win,setTimeout(fn){timers.set(++seq,fn);return seq;},clearTimeout(id){timers.delete(id);},Promise});
  const fake=createFakeCtx();entry.factory(()=>react).apply(fake.ctx);
  const container=win.document.createElement("div");
  const body=nativeBody(react,registrationByName(fake,"sidebar.right.pane.tab").component,"late",true,container);
  assert.equal(timers.size,1);
  const tick=timers.values().next().value;timers.clear();tick();assert.equal(timers.size,1);
  body.cleanups.forEach(fn=>fn());assert.equal(timers.size,0);
});

test("mount.js publishes exactly the ACPDSH surface", () => {
  const win = {};
  const runtime = loadMountRuntime(win);

  assert.equal(runtime, win.ACPDSH);
  assert.deepEqual(Object.keys(runtime).sort(), ["mount", "reassociate", "resume", "state", "suspend", "unmount", "version"]);
  assert.equal(runtime.version, "0.1.0");
  assert.deepEqual(plain(runtime.state()), {
    mounted: false,
    readOnly: true,
    degraded: false,
    sessionId: null,
    namespaces: [],
    booted: false
  });
});

test("mount success path installs the env, restricts the client and boots once", async () => {
  const env = createFakeEnv();
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, { sessionId: "sess-1" });

  assert.equal(result.ok, true);
  assert.equal(result.degraded, false);
  assert.equal(result.readOnly, true);
  assert.equal(result.booted, true);
  assert.equal(result.sessionId, "sess-1");
  assert.deepEqual(plain(result.namespaces), ["jobs", "science"]);
  assert.equal(env.bootCount(), 1);

  const envInstalls = stepEntries(env.log, "env.install");
  assert.equal(envInstalls.length, 1);
  assert.equal(envInstalls[0].opts.apiPrefix, "/acp-api");
  assert.equal(envInstalls[0].opts.assetBase, "/acp");
  assert.equal(envInstalls[0].opts.sessionId, "sess-1");
  assert.ok(Number.isSafeInteger(envInstalls[0].opts.generation));
  assert.equal(envInstalls[0].opts.focusEndpoint, "/acp-api/ui/focus");
  assert.equal(envInstalls[0].opts.storagePrefix, "acp-dsh:");
  assert.equal(envInstalls[0].opts.manageDocument, false);

  assert.equal(stepEntries(env.log, "baseUrl.installAll").length, 1);
  assert.equal(stepEntries(env.log, "baseUrl.installAll")[0].opts.apiPrefix, "/acp-api");

  const restrials = stepEntries(env.log, "restrictedClient.create");
  assert.equal(restrials.length, 1);
  assert.equal(restrials[0].allowWrite, false);
  assert.equal(restrials[0].source, env.originalClient);
  assert.notEqual(env.win.ACPClient, env.originalClient);
  assert.equal(env.win.ACPClient.isRestrictedFacade, true);

  assert.equal(env.win.ACP_ENV.apiPrefix, "/acp-api");
  assert.equal(env.win.ACP_ENV.sessionId, "sess-1");

  assert.equal(stepEntries(env.log, "theme.linkStyles").length, 1);
  assert.equal(stepEntries(env.log, "theme.apply").length, 1);

  const bridgeCreates = stepEntries(env.log, "focusBridge.create");
  assert.equal(bridgeCreates.length, 1);
  assert.equal(bridgeCreates[0].opts.endpoint, "/acp-api/ui/focus");
  assert.equal(bridgeCreates[0].opts.sessionId, "sess-1");
  assert.equal(bridgeCreates[0].opts.generation, envInstalls[0].opts.generation);
  assert.equal(typeof bridgeCreates[0].opts.transport, "function");
  assert.equal(stepEntries(env.log, "focusBridge.attach").length, 1);

  assert.ok(env.doc.getElementById("acp-shell"));
  assert.equal(env.doc.getElementById("acp-shell").getAttribute("data-acp-ready"), "true");

  const hosts = env.container.children.filter((el) => el.attributes["data-acp-host"] === "dsh");
  assert.equal(hosts.length, 1);
  assert.equal(hosts[0].className, "acp-dsh-host");

  assert.deepEqual(plain(runtime.state()), {
    mounted: true,
    readOnly: true,
    degraded: false,
    sessionId: "sess-1",
    namespaces: ["jobs", "science"],
    booted: true
  });
});

test("mount generates a stable per-tab session id when none is supplied", async () => {
  const env = createFakeEnv();
  const runtime = loadMountRuntime(env.win);

  const first = await runtime.mount(env.container, {});
  assert.equal(first.ok, true);
  assert.match(first.sessionId, /^dsh-[a-z0-9]+-[a-z0-9]+$/);
  assert.equal(first.sessionId, env.storage.get("acp-dsh-session"));
  assert.equal(runtime.unmount().ok, true);

  const second = await runtime.mount(env.container, {});
  assert.equal(second.sessionId, first.sessionId);
});

test("reassociate refuses missing and invalid targets", async () => {
  const env = createFakeEnv();
  const runtime = loadMountRuntime(env.win);

  assert.deepEqual(plain(runtime.reassociate("sess-x")), { ok: false, code: "not_mounted" });

  await runtime.mount(env.container, { sessionId: "sess-1" });
  assert.deepEqual(plain(runtime.reassociate("")), { ok: false, code: "bad_session_id" });
  assert.deepEqual(plain(runtime.reassociate(null)), { ok: false, code: "bad_session_id" });
  assert.deepEqual(plain(runtime.reassociate(42)), { ok: false, code: "bad_session_id" });
  assert.equal(runtime.state().sessionId, "sess-1");
  assert.deepEqual(plain(runtime.reassociate("sess-1")), {
    ok: true, changed: false, sessionId: "sess-1"
  });
  assert.equal(env.bootCount(), 1);
});

test("reassociate swaps the session identity without rebuilding or replaying", async () => {
  const focusCalls = [];
  let generation = 7;
  const env = createFakeEnv({
    scope: true,
    fetch(url, opts) {
      const post = !!(opts && opts.method === "POST");
      focusCalls.push({ url, post });
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(post
          ? { generation: (generation += 1) }
          : { focus: { generation, snapshot: { taskRef: "old-task" } } })
      });
    }
  });
  const runtime = loadMountRuntime(env.win);
  const mounted = await runtime.mount(env.container, { sessionId: "sess-A" });
  assert.equal(mounted.ok, true);
  const scopeWin = env.scopeWindow();
  assert.equal(scopeWin.ACP_ENV.sessionId, "sess-A");
  assert.equal(scopeWin.ACP_ENV.generation, 8);
  const host = env.container.children.filter((el) => el.attributes["data-acp-host"] === "dsh")[0];
  const shellEl = env.doc.getElementById("acp-shell");
  const bridgeCreates = () => stepEntries(env.log, "focusBridge.create");
  assert.equal(bridgeCreates().length, 1);

  const moved = await runtime.reassociate("sess-B");

  assert.equal(moved.ok, true);
  assert.equal(moved.changed, true);
  assert.equal(moved.sessionId, "sess-B");
  assert.equal(moved.previousSessionId, "sess-A");
  assert.equal(moved.generation, 9);
  assert.equal(runtime.state().sessionId, "sess-B");
  assert.equal(scopeWin.ACP_ENV.sessionId, "sess-B");
  assert.equal(env.bootCount(), 1, "reassociation must not boot a second shell");
  assert.equal(env.container.children.filter((el) => el.attributes["data-acp-host"] === "dsh")[0], host,
    "the same runtime host DOM stays in place");
  assert.equal(env.doc.getElementById("acp-shell"), shellEl);
  assert.equal(bridgeCreates().length, 2, "a new focus bridge is negotiated");
  assert.equal(bridgeCreates()[1].opts.sessionId, "sess-B");
  assert.equal(bridgeCreates()[1].opts.generation, 9, "the new generation comes from the CAS acquire");
  assert.ok(stepEntries(env.log, "focusBridge.detach").length >= 1, "the old focus bridge is released");
  assert.ok(focusCalls.some((entry) => entry.post), "the acquire is a real POST");

  const after = await runtime.reassociate("sess-B");
  assert.deepEqual(plain(after), { ok: true, changed: false, sessionId: "sess-B" });
  runtime.unmount();
});

test("mount with readOnly false keeps the full client and skips restriction", async () => {
  const env = createFakeEnv({ config: { readOnly: false } });
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, { readOnly: false });

  assert.equal(result.ok, true);
  assert.equal(result.readOnly, false);
  assert.equal(env.win.ACPClient, env.originalClient);
  assert.equal(stepEntries(env.log, "restrictedClient.create").length, 0);
  assert.equal(stepEntries(env.log, "env.install").length, 1);
});

test("degraded path mounts with an amber banner when health fails", async () => {
  const env = createFakeEnv({ fetch: () => Promise.reject(new Error("controller down")) });
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, { sessionId: "sess-degraded" });

  assert.equal(result.ok, true);
  assert.equal(result.degraded, true);
  assert.equal(result.booted, true);
  assert.equal(env.bootCount(), 1);
  assert.equal(runtime.state().degraded, true);

  const banners = env.container.children.filter((el) => el.attributes[BANNER_ATTR] === "degraded");
  assert.equal(banners.length, 1);
  assert.equal(banners[0].className, "acp-dsh-banner");
  assert.equal(banners[0].textContent, "ACP Controller unreachable");
  assert.equal(env.container.children[0], banners[0], "banner must render above the shell");

  const hosts = env.container.children.filter((el) => el.attributes["data-acp-host"] === "dsh");
  assert.equal(hosts.length, 1);
});

test("degraded path also triggers on a not-ok health payload", async () => {
  const env = createFakeEnv({
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: false, code: "E_CTRL" }) })
  });
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, { sessionId: "sess-degraded-2" });

  assert.equal(result.ok, true);
  assert.equal(result.degraded, true);
});

test("a second mount is refused with already_mounted", async () => {
  const env = createFakeEnv();
  const runtime = loadMountRuntime(env.win);
  await runtime.mount(env.container, { sessionId: "sess-live" });

  const secondContainer = env.doc.createElement("div");
  env.doc.body.appendChild(secondContainer);
  const result = await runtime.mount(secondContainer, { sessionId: "sess-second" });

  assert.equal(result.ok, false);
  assert.equal(result.code, "already_mounted");
  assert.equal(env.bootCount(), 1);
  assert.equal(runtime.state().mounted, true);
  assert.equal(runtime.state().sessionId, "sess-live");
  assert.equal(secondContainer.children.length, 1);
  assert.equal(secondContainer.children[0].attributes["data-acp-dsh-error"], "already_mounted");
});

test("missing host config renders host_config_unavailable and never boots", async () => {
  const env = createFakeEnv({ config: null });
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, {});

  assert.equal(result.ok, false);
  assert.equal(result.code, "host_config_unavailable");
  assert.equal(result.hostCode, "missing");
  assert.equal(result.field, "");
  assert.equal(env.bootCount(), 0);
  assert.equal(runtime.state().mounted, false);
  assert.equal(env.container.children.length, 1);
  assert.equal(env.container.children[0].attributes["data-acp-dsh-error"], "host_config_unavailable");
});

test("invalid host config carries the host code and field", async () => {
  const env = createFakeEnv();
  env.win.__ACP_PANELS_CONFIG__ = { ok: false, code: "E_HOST_CONFIG", field: "apiPrefix" };
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, {});

  assert.equal(result.ok, false);
  assert.equal(result.code, "host_config_unavailable");
  assert.equal(result.hostCode, "E_HOST_CONFIG");
  assert.equal(result.field, "apiPrefix");
  assert.equal(env.bootCount(), 0);
  assert.equal(env.container.children[0].attributes["data-acp-dsh-host-code"], "E_HOST_CONFIG");
  assert.equal(env.container.children[0].attributes["data-acp-dsh-host-field"], "apiPrefix");
});

test("skeleton_incomplete reports missing ids and cleans the container", async () => {
  const missingId = "acp-platform-panel";
  assert.ok(REQUIRED_IDS.includes(missingId), "the omitted id must be part of the shell contract");
  const env = createFakeEnv({ skeleton: makeSkeleton([missingId]) });
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, { sessionId: "sess-skel" });

  assert.equal(result.ok, false);
  assert.equal(result.code, "skeleton_incomplete");
  assert.deepEqual(plain(result.missing), [missingId]);
  assert.equal(env.bootCount(), 0);
  assert.equal(runtime.state().mounted, false);
  assert.equal(env.win.ACP_ENV, null);
  assert.equal(env.win.ACPClient, env.originalClient);
  assert.equal(env.container.children.length, 1);
  assert.equal(env.container.children[0].attributes["data-acp-dsh-error"], "skeleton_incomplete");
});

test("skeleton_incomplete also reports missing tab and slot markup", async () => {
  const env = createFakeEnv({ skeleton: "<div id=\"acp-shell\"></div>" });
  const runtime = loadMountRuntime(env.win);

  const result = await runtime.mount(env.container, {});

  assert.equal(result.ok, false);
  assert.equal(result.code, "skeleton_incomplete");
  assert.ok(result.missing.includes("acp-shell-main"));
  assert.ok(result.missing.includes("acp-tab-*"));
  assert.ok(result.missing.includes("acp-slot-*"));
});

test("missing assets are loaded through script tags exactly once", async () => {
  const env = createFakeEnv({ shell: false });
  const requested = [];

  env.doc.__setAutoLoad((scriptEl) => {
    requested.push(scriptEl.src);
    if (scriptEl.src.endsWith("/core.bundle.js")) {
      env.win.ACPShell = createFakeShell(env.log, env.doc, () => {});
    }
    if (typeof scriptEl.onload === "function") {
      scriptEl.onload();
    }
  });

  const runtime = loadMountRuntime(env.win);
  const result = await runtime.mount(env.container, { sessionId: "sess-assets" });

  assert.equal(result.ok, true);
  assert.deepEqual(requested, ["/acp/core.bundle.js"]);
  assert.equal(env.doc.head.children.filter((el) => el.tagName === "SCRIPT").length, 1);
});

test("unmount is idempotent, restores the client and clears ACP_ENV", async () => {
  const env = createFakeEnv();
  const runtime = loadMountRuntime(env.win);
  await runtime.mount(env.container, { sessionId: "sess-unmount" });

  assert.equal(env.doc.querySelectorAll(`[${CSS_MARKER}]`).length, 1);

  const first = runtime.unmount();

  assert.equal(first.ok, true);
  assert.ok(first.disposed.includes("focus_bridge.detach"));
  assert.ok(first.disposed.includes("client.events.closeStream"));
  assert.ok(first.disposed.includes("host.remove"));
  assert.ok(first.disposed.includes("stylesheets.remove"));
  assert.ok(first.disposed.includes("ACP_ENV.reset"));
  assert.ok(first.disposed.includes("ACPClient.restore"));

  assert.equal(env.win.ACP_ENV, null);
  assert.equal(env.win.ACPClient, env.originalClient);
  assert.equal(env.doc.querySelectorAll(`[${CSS_MARKER}]`).length, 0);
  assert.equal(env.container.children.length, 0);
  assert.equal(stepEntries(env.log, "events.closeStream").length, 1);

  assert.deepEqual(plain(runtime.state()), {
    mounted: false,
    readOnly: true,
    degraded: false,
    sessionId: null,
    namespaces: [],
    booted: false
  });

  const second = runtime.unmount();
  assert.equal(second.ok, true);
  assert.deepEqual(plain(second.disposed), []);
  assert.equal(stepEntries(env.log, "events.closeStream").length, 1);
});

test("mount never throws on an invalid container", async () => {
  const env = createFakeEnv();
  const runtime = loadMountRuntime(env.win);

  assert.equal((await runtime.mount(null, {})).code, "invalid_container");
  assert.equal((await runtime.mount(undefined, {})).code, "invalid_container");
  assert.equal((await runtime.mount({}, {})).code, "invalid_container");
  assert.equal(env.bootCount(), 0);
});

test("unmount without a mount is a no-op", () => {
  const env = createFakeEnv();
  const runtime = loadMountRuntime(env.win);

  const result = runtime.unmount();
  assert.equal(result.ok, true);
  assert.deepEqual(plain(result.disposed), []);
  assert.equal(env.win.ACP_ENV, undefined);
});

/* ======================================================== style audit */

test("client.js and mount.js are ES5 style", () => {
  for (const file of [CLIENT_SRC, MOUNT_SRC]) {
    const name = path.basename(file);
    const raw = fs.readFileSync(file, "utf8");
    const code = stripComments(raw);

    assert.equal(/=>/.test(raw), false, `${name} must not use arrow functions`);
    assert.equal(raw.includes("`"), false, `${name} must not use template literals`);
    assert.equal(/\b(let|const)\b/.test(code), false, `${name} must not use let/const`);
    assert.equal(/\bclass\b/.test(code), false, `${name} must not use classes`);
    assert.equal(/^\s*import\s/m.test(code), false, `${name} must not be an ES module`);
    assert.equal(/^\s*export\s/m.test(code), false, `${name} must not be an ES module`);
    assert.match(code, /\bvar\s/, `${name} must use var`);
  }

  const clientRaw = fs.readFileSync(CLIENT_SRC, "utf8");
  assert.ok(clientRaw.includes("window.__ModuleLoader__.load("));
  assert.ok(clientRaw.includes('id: "@acp/dsh-panels"'));

  const mountRaw = fs.readFileSync(MOUNT_SRC, "utf8");
  assert.ok(mountRaw.includes("root.ACPDSH = {"));
  assert.ok(mountRaw.includes('version: VERSION'));
});
