/* ACP 2.0 Next Shell — bootstrap + layout manager (UI-01).
 *
 * Drives the REAL shell scripts (i18n, context, registry, loader, commands,
 * layout-manager, recipes, bootstrap) plus the two mock modules inside the vm
 * harness with a hand-rolled DOM stub (no jsdom, no network).
 *
 * Pinned contracts:
 *   1. applyRecipe renders the recipe's panels into the right slots, with
 *      panel chrome + data-acp-state test hooks;
 *   2. an UNKNOWN module id renders a placeholder panel and never blocks the
 *      sibling panels of the same recipe;
 *   3. panel lifecycle mount → updateContext → dispose runs on the registered
 *      module factories (mock-recorded); repeated applyRecipe creates NO
 *      duplicate instances;
 *   4. Alt+1/2/3 (KeyboardEvent) switches the active workspace + recipe;
 *      workspace switches suspend hidden panels and resume visible ones
 *      (viewer migrates between recipes as the single active instance);
 *   5. ?demo=1 boots the demo recipe from window.ACPNextModules (mock-alpha /
 *      mock-beta), and context focus reaches viewer instances via commands.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FRONTEND = path.resolve(__dirname, "..", "..");

// ── minimal DOM stub ──────────────────────────────────────────────────

function makeClassList(node) {
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
  // Supports "tag", ".class", "[attr]", "tag[attr]" — the forms the shell +
  // this stub's own queries use.
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
    _byId: byId,
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
    querySelectorAll(selector) {
      // Global scan over every element ever created (attribute selectors).
      return doc._all.filter((n) => matchesSelector(n, selector));
    },
    querySelector(selector) {
      return doc._all.find((n) => matchesSelector(n, selector)) || null;
    },
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
    createRange() {
      return { selectNodeContents() {}, };
    },
    _docListeners: docListeners,
  };
  return doc;
}

function buildShellDom(doc) {
  // Mirrors frontend/next/index.html §1.1 structure.
  const shell = doc.createElement("div");
  shell.setAttribute("class", "acp-shell");
  shell.setAttribute("id", "acp-shell");
  shell.setAttribute("data-workspace", "research");
  shell.setAttribute("data-acp-ready", "false");

  const topbar = doc.createElement("header");
  topbar.setAttribute("class", "acp-shell-topbar");
  shell.appendChild(topbar);

  const langBtn = doc.createElement("button");
  langBtn.setAttribute("id", "acp-lang-toggle");
  topbar.appendChild(langBtn);

  ["research", "drawing", "molecular", "config", "execution", "files"].forEach((ws, i) => {
    const tab = doc.createElement("button");
    tab.setAttribute("id", "acp-tab-" + ws);
    tab.setAttribute("role", "tab");
    tab.setAttribute("data-workspace-id", ws);
    tab.setAttribute("aria-selected", i === 0 ? "true" : "false");
    tab.setAttribute("tabindex", i === 0 ? "0" : "-1");
    if (i === 0) tab.classList.add("is-active");
    const label = doc.createElement("span");
    label.setAttribute("data-i18n", "shell.workspace." + ws);
    label.textContent = ws;
    tab.appendChild(label);
    topbar.appendChild(tab);
  });

  const chip = doc.createElement("div");
  chip.setAttribute("id", "acp-focus-chip");
  chip.setAttribute("role", "status");
  chip.setAttribute("data-empty", "true");
  topbar.appendChild(chip);

  const main = doc.createElement("main");
  main.setAttribute("id", "acp-shell-main");
  shell.appendChild(main);

  const slots = {};
  ["research", "drawing", "molecular", "config", "execution", "files"].forEach((ws, i) => {
    const section = doc.createElement("section");
    section.setAttribute("id", "acp-ws-" + ws);
    section.setAttribute("role", "tabpanel");
    section.setAttribute("data-workspace-id", ws);
    if (i > 0) section.hidden = true;
    main.appendChild(section);
    slots[ws] = {};
    const slotNames = ["main", "side", "bottom"];
    slotNames.forEach((slotName) => {
      const slot = doc.createElement("div");
      slot.setAttribute("class", "acp-slot");
      slot.setAttribute("data-slot", slotName);
      slot.setAttribute("id", "acp-slot-" + ws + "-" + slotName);
      section.appendChild(slot);
      slots[ws][slotName] = slot;
    });
  });

  const fatal = doc.createElement("div");
  fatal.setAttribute("id", "acp-shell-fatal");
  fatal.hidden = true;
  shell.appendChild(fatal);

  return { shell, main, chip, fatal, slots, topbar };
}

// ── harness helpers ───────────────────────────────────────────────────

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function flush(rounds) {
  for (let i = 0; i < (rounds || 6); i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const SHELL_SCRIPTS = [
    "js/shell/i18n.js",
    "js/shell/workspace-context.js",
    "js/shell/module-registry.js",
    "js/shell/module-loader.js",
    "js/shell/commands.js",
    "js/shell/layout-manager.js",
    "js/shell/recipes.js",
    "js/shell/bootstrap.js",
  ];

  function setup(options) {
    options = options || {};
    const doc = makeDocument();
    const dom = buildShellDom(doc);
    const storage = {};
    const sandbox = makeSandbox({
      document: doc,
      localStorage: {
        getItem(k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
        setItem(k, v) { storage[k] = String(v); },
        removeItem(k) { delete storage[k]; },
      },
      location: { search: options.search || "" },
      CustomEvent: function CustomEvent(type, opts) {
        this.type = type;
        this.detail = (opts && opts.detail) || {};
      },
      navigator: { clipboard: null },
      fetch: function () { throw new Error("fetch is forbidden in shell bootstrap"); },
    });
    SHELL_SCRIPTS.forEach((rel) => loadScript(rel, sandbox));
    if (options.withMocks) {
      loadScript("js/shell/mock/mock-alpha.js", sandbox);
      loadScript("js/shell/mock/mock-beta.js", sandbox);
    }
    return { sandbox, doc, dom, storage, A: sandbox.window.ACPShell };
  }

  /** Recording module stub registered under a recipe's module id. */
  function registerStub(A, moduleId, log, opts) {
    opts = opts || {};
    const provides = opts.provides || [];
    const binds = opts.binds || [];
    const res = A.registry.register(
      {
        id: moduleId,
        interfaceVersion: 1,
        version: "0.1.0",
        provides: provides,
        binds: binds,
      },
      function (host) {
        const entry = { module: moduleId, instanceId: host && host.instanceId, calls: [], snapshots: [] };
        log.push(entry);
        return {
          mount(hostEl) {
            entry.calls.push("mount");
            if (opts.failMount) throw new Error(moduleId + " forced mount failure");
            if (hostEl && opts.stamp) {
              const stamp = hostEl.ownerDocument.createElement("div");
              stamp.setAttribute("data-stamp", moduleId);
              hostEl.appendChild(stamp);
            }
          },
          updateContext(snapshot) {
            entry.snapshots.push(snapshot || {});
            entry.calls.push("updateContext@" + (snapshot ? snapshot.revision : "?"));
          },
          suspend() { entry.calls.push("suspend"); },
          resume() { entry.calls.push("resume"); },
          dispose() { entry.calls.push("dispose"); },
          loadStructure(ref) { entry.calls.push("loadStructure:" + (ref && ref.name)); },
          openTask(ref) { entry.calls.push("openTask:" + (ref && ref.jobId)); },
        };
      }
    );
    assertEq(res.ok, true, "stub register failed: " + JSON.stringify(res));
  }

  // ── 1. recipe rendering, placeholders, lifecycle, keyboard ──

  await test("acp2_shell_bootstrap: applyRecipe renders slots, placeholders, lifecycle", async () => {
    const { doc, dom, A } = setup();
    const log = [];
    registerStub(A, "molecule-viewer", log, { provides: ["viewer"], binds: ["structureRef"], stamp: true });
    registerStub(A, "energy-profile", log, { provides: ["energy.profile"], binds: ["taskRef"] });
    A.bootstrap.boot();
    const applied = A.layout.applyRecipe("molecular");
    assertEq(applied.ok, true, JSON.stringify(applied));
    assertEq(dom.shell.getAttribute("data-acp-ready"), "true");
    await flush();
    const viewerEl = doc.getElementById("acp-panel-shared-viewer");
    const energyEl = doc.getElementById("acp-panel-mol-energy");
    assert(viewerEl && energyEl, "C view viewer and analysis exist");
    assert(viewerEl.parentNode === dom.slots.molecular.main, "viewer in main slot");
    assert(energyEl.parentNode === dom.slots.molecular.bottom, "energy in bottom slot");
    assertEq(viewerEl.getAttribute("data-instance-id"), "shared-viewer");
    assertEq(viewerEl.getAttribute("role"), "region");
    const viewer = log.find((e) => e.module === "molecule-viewer");
    const energy = log.find((e) => e.module === "energy-profile");
    assert(viewer.calls.includes("mount") && energy.calls.includes("mount"));
    const logLenBefore = log.length;
    A.layout.applyRecipe("molecular"); A.layout.applyRecipe("molecular");
    await flush();
    assertEq(log.length, logLenBefore, "no new factory calls on re-apply");
    assertEq(doc._all.filter((n) => n.getAttribute && n.getAttribute("data-instance-id") === "shared-viewer").length, 1);
    const snap = A.context.setFocus({ structureRef: { name: "caffeine_conf1" }, taskRef: { jobId: "job-42", attempt: 2 } }, "test");
    await flush();
    assert(viewer.calls.includes("updateContext@" + snap.revision));
    assertEq(dom.chip.getAttribute("data-empty"), "false");
    A.layout.disposeAll();
    assertEq(viewer.calls.filter((c) => c === "dispose").length, 1);
    assertEq(energy.calls.filter((c) => c === "dispose").length, 1);
    assertEq(A.layout.listPanels().length, 0);
  });

  await test("acp2_shell_bootstrap: Alt+1–6 switches views and preserves one viewer", async () => {
    const { sandbox, doc, dom, A } = setup();
    const log = [];
    // The research main panel is the project network (network contract §6);
    // agent-workspace is still registered as a module, just not the default.
    registerStub(A, "reaction-network", log, { provides: ["network.read"], binds: ["networkNodeId"] });
    registerStub(A, "molecule-viewer", log, { provides: ["viewer"], binds: ["structureRef"] });
    registerStub(A, "task-explorer", log, { provides: ["task-explorer"], binds: ["taskRef"] });
    registerStub(A, "files-workspace", log, { provides: ["project.files"], binds: ["projectId"] });

    A.bootstrap.boot();
    await flush();
    const network = log.find((e) => e.module === "reaction-network");

    // Alt+3 → molecular
    doc.dispatchEvent({ type: "keydown", key: "3", altKey: true, ctrlKey: false, metaKey: false, preventDefault() {} });
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "molecular");
    assertEq(dom.shell.getAttribute("data-workspace"), "molecular");
    assertEq(doc.getElementById("acp-ws-molecular").hidden, false, "molecular visible");
    assertEq(doc.getElementById("acp-ws-research").hidden, true, "research hidden");
    assertEq(doc.getElementById("acp-tab-molecular").getAttribute("aria-checked"), "true");
    assertEq(doc.getElementById("acp-tab-molecular").getAttribute("aria-current"), "page");
    assertEq(doc.getElementById("acp-tab-research").getAttribute("aria-checked"), "false");
    assertEq(doc.getElementById("acp-tab-research").getAttribute("tabindex"), "-1", "roving tabindex");

    // viewer MIGRATED to molecular main — same element, no remount
    const viewer = log.find((e) => e.module === "molecule-viewer");
    const viewerEl = doc.getElementById("acp-panel-shared-viewer");
    assert(viewerEl.parentNode === dom.slots.molecular.main,  "viewer migrated to molecular main");
    assertEq(viewer.calls.filter((c) => c === "mount").length, 1, "migration did NOT remount");
    assert(viewer.calls.includes("mount"), "viewer mounted in C");
    assert(network.calls.includes("suspend"), "hidden-workspace panel suspended");

    // Alt+5 → execution: the same Viewer moves to the result inspector.
    doc.dispatchEvent({ type: "keydown", key: "5", altKey: true, ctrlKey: false, metaKey: false, preventDefault() {} });
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "execution");
    assert(viewerEl.parentNode === dom.slots.execution.side, "viewer moved to F side");
    assertEq(viewer.calls.filter((c) => c === "mount").length, 1, "still one mount");
    assert(viewer.calls.includes("suspend"), "viewer suspended while moving C → F");
    assert(viewer.calls.includes("resume"), "viewer resumed after moving C → F");
    assert(doc.getElementById("acp-panel-exec-results"), "F results panel rendered");

    // Alt+6 → files: its own workspace panel is mounted, no viewer duplicate.
    doc.dispatchEvent({ type: "keydown", key: "6", altKey: true, ctrlKey: false, metaKey: false, preventDefault() {} });
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "files");
    assert(doc.getElementById("acp-panel-files-workspace"), "files workspace rendered");
    assertEq(viewer.calls.filter((c) => c === "mount").length, 1, "files view does not mount a second viewer");

    // Alt+1 → back to research
    doc.dispatchEvent({ type: "keydown", key: "1", altKey: true, ctrlKey: false, metaKey: false, preventDefault() {} });
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "research");
    assert(viewerEl.parentNode === dom.slots.execution.side, "hidden F keeps the single viewer instance");
    assertEq(viewer.calls.filter((c) => c === "mount").length, 1, "single mount across views");
    assert(network.calls.includes("resume"), "research panel resumed on return");

    // Alt+0 is not a workspace shortcut — no crash, no switch
    doc.dispatchEvent({ type: "keydown", key: "0", altKey: true, ctrlKey: false, metaKey: false, preventDefault() {} });
    assertEq(A.layout.getCurrentWorkspace(), "research");
  });

  // ── 2. panel-local error boundary + retry ──

  await test("acp2_shell_bootstrap: mount failure is panel-local; retry re-mounts via loader", async () => {
    const { doc, A } = setup();
    const log = [];
    let failNext = { value: true };
    assertEq(A.registry.register({ id: "results-workspace", interfaceVersion: 1, version: "0.1.0", provides: ["energy.profile"], binds: ["taskRef"] }, function () {
      const entry = { module: "results-workspace", calls: [] }; log.push(entry);
      return { mount() { entry.calls.push("mount"); if (failNext.value) { failNext.value = false; throw new Error("energy forced mount failure"); } }, updateContext() {}, dispose() { entry.calls.push("dispose"); } };
    }).ok, true);
    registerStub(A, "molecule-viewer", log, { provides: ["viewer"], binds: ["structureRef"] });
    A.bootstrap.boot(); A.layout.applyRecipe("execution"); await flush();
    const energyEl = doc.getElementById("acp-panel-exec-results");
    const viewerEl = doc.getElementById("acp-panel-shared-viewer");
    assertEq(energyEl.getAttribute("data-acp-state"), "error");
    assert(energyEl.querySelector(".acp-state-msg").textContent.includes("forced mount failure"));
    assertEq(viewerEl.getAttribute("data-acp-state"), "ready", "sibling unaffected");
    const gen1 = energyEl.getAttribute("data-acp-generation");
    const retryBtn = energyEl.querySelectorAll("button").find((b) => b.getAttribute("data-action") === "retry");
    retryBtn.dispatch("click", {}); await flush();
    assertEq(energyEl.getAttribute("data-acp-state"), "ready");
    assert(Number(energyEl.getAttribute("data-acp-generation")) > Number(gen1));
    assertEq(log.filter((e) => e.module === "results-workspace").length, 2);
  });

  // ── 3. demo recipe + commands routing ──

  await test("acp2_shell_bootstrap: ?demo=1 boots mocks; commands route into viewer instance", async () => {
    const { sandbox, doc, dom, A } = setup({ search: "?demo=1", withMocks: true });

    const applied = A.bootstrap.boot();
    assertEq(applied.ok, true, JSON.stringify(applied));
    assertEq(applied.recipeId, "demo");
    await flush();

    const mockLog = sandbox.window.__acpMockLog;
    assert(mockLog.some((e) => e.module === "mock-alpha" && e.call === "factory"), "mock-alpha collected from ACPNextModules");
    assert(mockLog.some((e) => e.module === "mock-beta" && e.call === "mount"), "mock-beta mounted");

    const alphaEl = doc.getElementById("acp-panel-demo-alpha");
    const betaEl = doc.getElementById("acp-panel-demo-beta");
    assert(alphaEl.parentNode === dom.slots.research.main);
    assert(betaEl.parentNode === dom.slots.research.side);
    assertEq(alphaEl.getAttribute("data-acp-state"), "ready");
    assertEq(betaEl.getAttribute("data-acp-state"), "ready");

    // commands.openStructure → resolves the viewer panel (provides viewer),
    // writes context, calls instance.loadStructure — READ-ONLY navigation.
    const res = A.commands.openStructure({ name: "caffeine_conf1", source: "test:agent-card" });
    assertEq(res.ok, true, JSON.stringify(res));
    assertEq(res.via, "instance");
    assertEq(res.targetInstanceId, "demo-beta");
    assert(A.context.getSnapshot().structureRef.name === "caffeine_conf1", "context focused");
    assert(mockLog.some((e) => e.module === "mock-beta" && e.call === "loadStructure"), "viewer instance method called");

    // commands.openTask → mock-alpha binds taskRef and implements openTask,
    // so delivery reaches the instance method; context is still written.
    const taskRes = A.commands.openTask({ jobId: "job-7", attempt: 1, source: "test" });
    assertEq(taskRes.ok, true, JSON.stringify(taskRes));
    assertEq(taskRes.via, "instance");
    assertEq(taskRes.targetInstanceId, "demo-alpha");
    assert(mockLog.some((e) => e.module === "mock-alpha" && e.call === "openTask"), "explorer instance method called");
    assertEq(A.context.getSnapshot().taskRef.jobId, "job-7");

    // Bad payload → structured rejection, no throw.
    assertEq(A.commands.openTask({}).ok, false);
    assertEq(A.commands.openStructure(null).ok, false);
  });

  // ── 3b. P0-2: real capability tags + binds route cross-workspace ──

  await test("acp2_shell_bootstrap: openTask from Research routes to F results and focuses its title", async () => {
    const { doc, A } = setup(); const log = [];
    registerStub(A, "reaction-network", log, { provides: ["network.read"], binds: ["networkNodeId"] });
    registerStub(A, "molecule-viewer", log, { provides: ["structure.render"], binds: ["structureRef"] });
    registerStub(A, "results-workspace", log, { provides: ["energy.profile"], binds: ["taskRef"], stamp: true });
    A.bootstrap.boot(); await flush();
    assertEq(A.layout.getCurrentWorkspace(), "research");
    const res = A.commands.openTask({ jobId: "job-p0-2", attempt: 1, source: "test:agent-card" });
    await flush();
    const energyEl = doc.getElementById("acp-panel-exec-results");
    assertEq(res.ok, true, JSON.stringify(res));
    assertEq(res.targetInstanceId, null, "context is delivered while F mounts");
    assertEq(res.workspace, null);
    assertEq(A.layout.getCurrentWorkspace(), "execution");
    assert(doc.activeElement === energyEl.querySelector(".acp-panel-title"));
    assertEq(A.context.getSnapshot().taskRef.jobId, "job-p0-2");
  });

  // ── 4. whole-page fatal boundary ──

  await test("acp2_shell_bootstrap: recipe failure renders the fatal layer, not a blank page", async () => {
    const { sandbox, doc, dom, A } = setup();
    // Break recipes.get so boot cannot find the default recipe.
    A.recipes.get = function () { return null; };
    const res = A.bootstrap.boot();
    assertEq(res.ok, false);
    assertEq(res.fatal, true);
    assertEq(dom.fatal.hidden, false, "fatal layer visible");
    assertEq(dom.main.hidden, true, "main hidden");
    const card = dom.fatal.querySelector(".acp-shell-fatal-card");
    assert(card, "fatal card rendered");
    const backLink = card.querySelectorAll("a").find((a) => a.getAttribute("href") === "/");
    assert(backLink, "classic entry link present");
    assertEq(dom.shell.getAttribute("data-acp-ready"), "false", "shell never reported ready");
  });

  await test("acp2_shell_bootstrap: project scope reaches A/C/E/F and clears task/structure refs", async () => {
    const { A } = setup();
    const log = [];
    registerStub(A, "reaction-network", log, { provides: ["network.read"], binds: ["networkNodeId"] });
    registerStub(A, "molecule-viewer", log, { provides: ["structure.render"], binds: ["structureRef"] });
    registerStub(A, "config-workspace", log, { provides: ["task.wizard"], binds: [] });
    registerStub(A, "results-workspace", log, { provides: ["energy.profile"], binds: ["taskRef"] });
    A.bootstrap.boot();
    await flush();
    A.context.setFocus({ taskRef: { jobId: "old-job", attempt: 1 }, structureRef: { kind: "inline-xyz", text: "1\nH\nH 0 0 0" } }, "test");
    A.context.setFocus({ projectId: "project-real" }, "project-switcher");
    await flush();
    const snap = A.context.getSnapshot();
    assertEq(snap.projectId, "project-real");
    assertEq(snap.taskRef, null, "project switch clears the old task");
    assertEq(snap.structureRef, null, "project switch clears the old structure");
    for (const ws of ["molecular", "config", "execution"]) {
      A.layout.switchWorkspace(ws);
      await flush();
      const moduleId = ws === "molecular" ? "molecule-viewer" : ws === "config" ? "config-workspace" : "results-workspace";
      const entry = log.find((e) => e.module === moduleId);
      assertEq(entry.snapshots[entry.snapshots.length - 1].projectId, "project-real", moduleId + " received the project scope");
    }
    const network = log.find((e) => e.module === "reaction-network");
    assertEq(network.snapshots[network.snapshots.length - 1].projectId, "project-real", "A received the project scope");
  });

  // ── 5. i18n: missing keys never clobber mounted module copy (P0-1 现象 B) ──

  await test("acp2_shell_bootstrap: applyI18n leaves missing-key nodes untouched across toggle", async () => {
    const { doc, dom, A } = setup();

    // Simulate mounted-module chrome: inline copy + a key NOT in the dict.
    const missing = doc.createElement("span");
    missing.setAttribute("data-i18n", "not.in.the.dictionary");
    missing.textContent = "模块内联中文";
    dom.slots.research.main.appendChild(missing);

    const missingAria = doc.createElement("button");
    missingAria.setAttribute("data-i18n-aria", "also.missing.from.dict");
    missingAria.setAttribute("aria-label", "模块内联 aria 文案");
    dom.slots.research.main.appendChild(missingAria);

    // A known key on the same tree proves the scan did run.
    const known = doc.createElement("span");
    known.setAttribute("data-i18n", "shell.panel.reload");
    known.textContent = "旧文案";
    dom.slots.research.main.appendChild(known);

    A.i18n.applyI18n(doc);
    assertEq(missing.textContent, "模块内联中文", "missing-key node keeps module copy (zh pass)");
    assertEq(missingAria.getAttribute("aria-label"), "模块内联 aria 文案", "missing-key aria untouched (zh pass)");
    assertEq(known.textContent, "重新加载", "known key translated in the same pass");

    // Language toggle rescans the document — this is where bare keys used
    // to replace mounted copy.
    A.i18n.toggle();
    assertEq(A.i18n.getLang(), "en-US", "toggle flipped to en-US");
    assertEq(missing.textContent, "模块内联中文", "missing-key node NOT clobbered with the key after toggle");
    assertEq(missingAria.getAttribute("aria-label"), "模块内联 aria 文案", "missing-key aria NOT clobbered after toggle");
    assertEq(known.textContent, "Reload", "known key follows the language");

    A.i18n.toggle();
    assertEq(A.i18n.getLang(), "zh-CN", "toggle back to zh-CN");
    assertEq(missing.textContent, "模块内联中文", "missing-key node still intact after second toggle");
    assertEq(known.textContent, "重新加载", "known key back to zh");
  });
};
