/* ACP 3.0 — A-63 keyboard accessibility + ARIA for the Shell chrome.
 *
 * Authority: docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md
 *   §5 / A-63 “键盘可达与 a11y — 焦点顺序可走完”.
 *
 * Scope: SHELL CHROME ONLY (skip link, workspace tabs, panel wrappers,
 * layout-editor dialog, fatal layer). Module internals are out of scope and the
 * shell never edits module nodes.
 *
 * This file has two halves:
 *
 *  1. STATIC assertions over frontend/next/index.html, shell.css and the shell
 *     sources — they pin the shipped markup/CSS contract (skip link first,
 *     landmark + tab ARIA, visible focus ring, no new script/global).
 *
 *  2. DOM assertions: the REAL shell scripts (i18n → context → registry →
 *     loader → commands → layout-manager → recipes → preferences →
 *     layout-editor → bootstrap) run inside the vm harness against a
 *     hand-rolled DOM stub. They walk the actual focus order:
 *       skip link → workspace tabs → panel titles → panel content,
 *     exercise Enter/Space/Arrow/Escape, and verify the layout-editor dialog
 *     focus trap, initial focus and focus restore.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const FRONTEND = path.resolve(__dirname, "..", "..");
const INDEX_HTML = path.join(FRONTEND, "next", "index.html");
const SHELL_CSS = path.join(FRONTEND, "css", "shell", "shell.css");
const SHELL_DIR = path.join(FRONTEND, "js", "shell");

const WORKSPACES = ["research", "molecular", "execution"];

// ── DOM stub (no jsdom, no network) ─────────────────────────────────────

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
  const s = String(selector).trim();
  let m = /^([A-Za-z0-9_-]+)$/.exec(s);
  if (m) return node.tagName === m[1].toUpperCase();
  m = /^\.([A-Za-z0-9_-]+)$/.exec(s);
  if (m) return node.classList.contains(m[1]);
  m = /^\[([A-Za-z0-9_-]+)\]$/.exec(s);
  if (m) return node.getAttribute(m[1]) !== null;
  m = /^\[([A-Za-z0-9_-]+)="([^"]*)"\]$/.exec(s);
  if (m) return node.getAttribute(m[1]) === m[2];
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
    open: undefined,
    value: "",
    type: "",
    style: {
      setProperty(name, value) { this[name] = value; },
      removeProperty(name) { delete this[name]; },
    },
    listeners: {},
    classList: null,
    focus() { doc.activeElement = node; },
    blur() { if (doc.activeElement === node) doc.activeElement = null; },
    showModal() { node.open = true; },
    close() { node.open = false; },
    setAttribute(name, value) {
      node.attrs[name] = String(value);
      if (name === "class") {
        node.classList._set.clear();
        String(value).split(/\s+/).filter(Boolean).forEach((c) => node.classList._set.add(c));
      }
      if (name === "hidden") node.hidden = true;
      if (name === "id") doc._byId.set(String(value), node);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(node.attrs, name) ? node.attrs[name] : null;
    },
    hasAttribute(name) { return node.getAttribute(name) !== null; },
    removeAttribute(name) {
      const had = name in node.attrs;
      delete node.attrs[name];
      if (name === "hidden") node.hidden = false;
      return had;
    },
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
    get isConnected() { return true; },
  };
  node.classList = makeClassList(node);
  Object.defineProperty(node, "id", {
    get() { return node.attrs.id || ""; },
    set(value) {
      node.attrs.id = String(value);
      doc._byId.set(String(value), node);
    },
  });
  Object.defineProperty(node, "className", {
    get() { return node.attrs.class || ""; },
    set(value) {
      node.attrs.class = String(value);
      node.classList._set.clear();
      String(value).split(/\s+/).filter(Boolean).forEach((name) => node.classList._set.add(name));
    },
  });
  return node;
}

function makeDocument() {
  const byId = new Map();
  const docListeners = {};
  const doc = {
    _all: [],
    _byId: byId,
    documentElement: { lang: "zh-CN" },
    title: "",
    activeElement: null,
    body: null,
    createElement(tag) { return makeElement(doc, tag); },
    createElementNS(namespace, tag) { return doc.createElement(tag); },
    getElementById(id) { return byId.get(String(id)) || null; },
    querySelectorAll(selector) { return doc._all.filter((n) => matchesSelector(n, selector)); },
    querySelector(selector) { return doc._all.find((n) => matchesSelector(n, selector)) || null; },
    addEventListener(type, handler) { (docListeners[type] = docListeners[type] || []).push(handler); },
    removeEventListener(type, handler) {
      const list = docListeners[type] || [];
      const idx = list.indexOf(handler);
      if (idx >= 0) list.splice(idx, 1);
    },
    dispatchEvent(event) {
      (docListeners[event && event.type] || []).slice().forEach((h) => h(event));
      return true;
    },
    createRange() { return { selectNodeContents() {} }; },
    _docListeners: docListeners,
  };
  doc.body = doc.createElement("body");
  return doc;
}

function buildShellDom(doc) {
  // Mirrors frontend/next/index.html (landmarks + ARIA attributes included).
  const shell = doc.createElement("div");
  shell.setAttribute("class", "acp-shell");
  shell.setAttribute("id", "acp-shell");
  shell.setAttribute("data-workspace", "research");
  shell.setAttribute("data-acp-ready", "false");

  const skip = doc.createElement("a");
  skip.setAttribute("class", "acp-skip-link");
  skip.setAttribute("id", "acp-skip-link");
  skip.setAttribute("href", "#acp-ws-research");
  skip.setAttribute("data-i18n", "shell.a11y.skip");
  skip.setAttribute("data-acp-focus-kind", "skip");
  skip.setAttribute("data-acp-skip-target", "acp-ws-research");
  skip.textContent = "跳到工作区";
  shell.appendChild(skip);

  const topbar = doc.createElement("header");
  topbar.setAttribute("class", "acp-shell-topbar");
  shell.appendChild(topbar);

  const nav = doc.createElement("nav");
  nav.setAttribute("class", "acp-shell-switch");
  nav.setAttribute("role", "tablist");
  nav.setAttribute("aria-label", "工作区");
  nav.setAttribute("aria-orientation", "horizontal");
  nav.setAttribute("data-i18n-aria", "shell.workspace.switch_label");
  nav.setAttribute("data-acp-focus-kind", "tablist");
  topbar.appendChild(nav);

  const tabs = {};
  WORKSPACES.forEach((ws, index) => {
    const tab = doc.createElement("button");
    tab.setAttribute("id", "acp-tab-" + ws);
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", index === 0 ? "true" : "false");
    tab.setAttribute("aria-controls", "acp-ws-" + ws);
    tab.setAttribute("aria-keyshortcuts", "Alt+" + (index + 1));
    tab.setAttribute("data-workspace-id", ws);
    tab.setAttribute("data-acp-focus-kind", "tab");
    tab.setAttribute("tabindex", index === 0 ? "0" : "-1");
    tab.setAttribute("type", "button");
    if (index === 0) tab.classList.add("is-active");
    const label = doc.createElement("span");
    label.setAttribute("data-i18n", "shell.workspace." + ws);
    label.textContent = ws;
    tab.appendChild(label);
    nav.appendChild(tab);
    tabs[ws] = tab;
  });

  const chip = doc.createElement("div");
  chip.setAttribute("id", "acp-focus-chip");
  chip.setAttribute("role", "status");
  chip.setAttribute("aria-live", "polite");
  chip.setAttribute("data-empty", "true");
  topbar.appendChild(chip);

  const actions = doc.createElement("div");
  actions.setAttribute("class", "acp-shell-actions");
  topbar.appendChild(actions);

  const layoutBtn = doc.createElement("button");
  layoutBtn.setAttribute("class", "acp-shell-lang");
  layoutBtn.setAttribute("id", "acp-layout-edit");
  layoutBtn.setAttribute("type", "button");
  layoutBtn.textContent = "配置布局";
  actions.appendChild(layoutBtn);

  const langBtn = doc.createElement("button");
  langBtn.setAttribute("class", "acp-shell-lang");
  langBtn.setAttribute("id", "acp-lang-toggle");
  langBtn.setAttribute("type", "button");
  langBtn.textContent = "中文 | EN";
  actions.appendChild(langBtn);

  const main = doc.createElement("main");
  main.setAttribute("class", "acp-shell-main");
  main.setAttribute("id", "acp-shell-main");
  main.setAttribute("aria-label", "工作区");
  main.setAttribute("data-i18n-aria", "shell.workspace.switch_label");
  main.setAttribute("data-acp-landmark", "workspace-area");
  shell.appendChild(main);

  const sections = {};
  const slots = {};
  WORKSPACES.forEach((ws, index) => {
    const section = doc.createElement("section");
    section.setAttribute("class", "acp-workspace");
    section.setAttribute("id", "acp-ws-" + ws);
    section.setAttribute("role", "tabpanel");
    section.setAttribute("aria-labelledby", "acp-tab-" + ws);
    section.setAttribute("data-workspace-id", ws);
    section.setAttribute("data-acp-landmark", "workspace");
    section.setAttribute("tabindex", "0");
    if (index > 0) section.hidden = true;
    main.appendChild(section);
    sections[ws] = section;
    slots[ws] = {};
    ["main", "side", "bottom"].forEach((slotName) => {
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

  return { shell, skip, nav, tabs, chip, actions, layoutBtn, langBtn, main, sections, slots, fatal };
}

// ── harness helpers ────────────────────────────────────────────────────

function evt(key, extra) {
  const event = {
    type: "keydown",
    key,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  };
  Object.assign(event, extra || {});
  return event;
}

async function flush(rounds) {
  for (let i = 0; i < (rounds || 6); i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

const SHELL_SCRIPTS = [
  "js/shell/i18n.js",
  "js/shell/workspace-context.js",
  "js/shell/module-registry.js",
  "js/shell/module-loader.js",
  "js/shell/commands.js",
  "js/shell/layout-manager.js",
  "js/shell/recipes.js",
  "js/shell/layout-preferences.js",
  "js/shell/layout-editor.js",
  "js/shell/bootstrap.js",
];

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup(options) {
    options = options || {};
    const doc = makeDocument();
    const dom = buildShellDom(doc);
    const storage = {};
    const sandbox = makeSandbox({
      document: doc,
      localStorage: {
        getItem(k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
        setItem(k, v) {
          if (options.readonlyStorage) throw new Error("storage blocked");
          storage[k] = String(v);
        },
        removeItem(k) { delete storage[k]; },
      },
      location: { search: options.search || "" },
      CustomEvent: function CustomEvent(type, init) {
        this.type = type;
        this.detail = (init && init.detail) || {};
      },
      navigator: { clipboard: null },
    });
    SHELL_SCRIPTS.forEach((rel) => loadScript(rel, sandbox));
    if (typeof options.register === "function") options.register(sandbox.window.ACPShell);
    const booted = sandbox.window.ACPShell.bootstrap.boot();
    return { sandbox, doc, dom, A: sandbox.window.ACPShell, booted };
  }

  function panelIn(doc, instanceId) {
    return doc.getElementById("acp-panel-" + instanceId);
  }

  // ── 1. shipped markup / CSS / source contract ────────────────────────

  await test("A-63: index.html ships the skip link, landmark and tab ARIA (no new scripts)", function () {
    const html = fs.readFileSync(INDEX_HTML, "utf8");

    // Skip link: first focusable element, real id, real target, translated.
    const skipMatch = /<a[^>]*class="acp-skip-link"[^>]*>/.exec(html);
    assert(skipMatch, "skip link anchor exists");
    const skipTag = skipMatch[0];
    assert(skipTag.indexOf('id="acp-skip-link"') !== -1, "skip link has a stable id");
    assert(/href="#acp-ws-/.test(skipTag), "skip link targets a workspace landmark");
    assert(skipTag.indexOf('data-i18n="shell.a11y.skip"') !== -1, "skip link reuses the existing i18n key");
    assert(html.indexOf('id="acp-shell-main"') > html.indexOf('id="acp-skip-link"'),
      "skip link precedes the main landmark");
    assert(html.indexOf('role="tablist"') > html.indexOf('id="acp-skip-link"'),
      "skip link precedes the workspace tablist");

    // Main + workspace landmarks are named.
    const mainMatch = /<main[^>]*id="acp-shell-main"[^>]*>/.exec(html);
    assert(mainMatch, "main landmark present");
    assert(mainMatch[0].indexOf("aria-label=") !== -1, "main landmark has an accessible name");
    assert(mainMatch[0].indexOf('data-i18n-aria="shell.workspace.switch_label"') !== -1,
      "main landmark name follows the language toggle");
    assert(mainMatch[0].indexOf('data-acp-landmark="workspace-area"') !== -1,
      "main landmark carries focus-order metadata");

    const tablist = /<nav[^>]*role="tablist"[^>]*>/.exec(html);
    assert(tablist, "tablist present");
    assert(tablist[0].indexOf('aria-label="') !== -1, "tablist has an aria-label");
    assert(tablist[0].indexOf('aria-orientation="horizontal"') !== -1, "tablist orientation declared");

    WORKSPACES.forEach((ws, index) => {
      const tab = new RegExp("<button[^>]*id=\"acp-tab-" + ws + "\"[\\s\\S]*?>", "m").exec(html);
      assert(tab, "tab exists: " + ws);
      const tag = tab[0];
      assert(tag.indexOf('role="tab"') !== -1, ws + " tab role");
      assert(tag.indexOf('aria-controls="acp-ws-' + ws + '"') !== -1, ws + " tab aria-controls");
      assert(tag.indexOf('aria-keyshortcuts="Alt+' + (index + 1) + '"') !== -1, ws + " tab shortcut");
      assert(/aria-selected="(true|false)"/.test(tag), ws + " tab aria-selected");

      const panel = new RegExp("<section[^>]*id=\"acp-ws-" + ws + "\"[\\s\\S]*?>", "m").exec(html);
      assert(panel, "tabpanel exists: " + ws);
      assert(panel[0].indexOf('role="tabpanel"') !== -1, ws + " tabpanel role");
      assert(panel[0].indexOf('aria-labelledby="acp-tab-' + ws + '"') !== -1, ws + " tabpanel label");
      assert(panel[0].indexOf('tabindex="0"') !== -1, ws + " tabpanel is focusable");
      assert(panel[0].indexOf('data-acp-landmark="workspace"') !== -1, ws + " tabpanel landmark metadata");
    });

    // Exactly one tab is in the roving tab order and it is the active one.
    const selected = WORKSPACES.filter((ws) => {
      const tag = new RegExp("<button[^>]*id=\"acp-tab-" + ws + "\"[\\s\\S]*?>", "m").exec(html)[0];
      return tag.indexOf('aria-selected="true"') !== -1;
    });
    assertEq(selected, ["research"], "single selected tab in the shipped markup");

    // No new scripts / inline handlers: a11y lives in the shell JS files.
    const inlineScripts = html.match(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g) || [];
    assertEq(inlineScripts.length, 2, "only the file:// guard and the boot call are inline scripts");
    inlineScripts.forEach((block) => {
      assert(!/focusTrap|focus-trap|skip-link|aria-selected/i.test(block),
        "no a11y behaviour smuggled into an inline script");
    });
    assert(!/\son[a-z]+="/i.test(html), "no inline event handlers");
  });

  await test("A-63: shell.css provides one visible focus ring for shell chrome and the dialog", function () {
    const css = fs.readFileSync(SHELL_CSS, "utf8");
    assert(css.indexOf(".acp-visually-hidden") !== -1, "visually-hidden utility exists for the live region");
    assert(/\.acp-skip-link:focus\s*\{[^}]*top:\s*8px/.test(css), "skip link becomes visible on focus");
    assert(/\.acp-skip-link:focus,?[\s\S]{0,600}?outline:\s*2px solid var\(--accent\)/.test(css),
      "skip link has a visible focus ring");
    assert(/\.acp-shell :focus-visible[^{]*\{[^}]*outline:\s*2px solid var\(--accent\)/.test(css),
      "shell-wide :focus-visible ring");
    assert(/\.acp-layout-dialog :focus-visible/.test(css), "dialog controls get the focus ring");
    assert(/\.acp-panel-body:focus-visible[^{]*\{[^}]*outline-offset:\s*-2px/.test(css),
      "panel body uses an inset ring (not clipped by the panel)");
    assert(/\.acp-panel-title:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\)/.test(css),
      "focusable panel titles keep a visible ring");
  });

  await test("A-63: a11y code lives in shell JS only; no new global is introduced", function () {
    const files = ["layout-manager.js", "layout-editor.js", "bootstrap.js"];
    files.forEach((file) => {
      const source = fs.readFileSync(path.join(SHELL_DIR, file), "utf8");
      // Assignments only (`===` comparisons are reads of existing globals).
      const globals = source.match(/window\.[A-Za-z_$][\w$]*\s*=(?!=)/g) || [];
      globals.forEach((assignment) => {
        assert(assignment.indexOf("window.ACPShell") === 0, file + " introduces no global: " + assignment);
      });
    });

    const manager = fs.readFileSync(path.join(SHELL_DIR, "layout-manager.js"), "utf8");
    ["data-acp-focus-step", "data-acp-focus-kind", "data-acp-focus-sequence", "focusOrder: focusOrder",
      "focusPanelContent", "acp-skip-link", "aria-live", "ArrowDown", "Escape", "aria-keyshortcuts"]
      .forEach((needle) => assert(manager.indexOf(needle) !== -1, "layout-manager implements " + needle));

    const editor = fs.readFileSync(path.join(SHELL_DIR, "layout-editor.js"), "utf8");
    ['"role", "dialog"', "aria-modal", "aria-labelledby", "aria-describedby", "data-acp-dialog-open",
      "onDialogKeydown", "Escape", "focusables"].forEach((needle) => {
      assert(editor.indexOf(needle) !== -1, "layout-editor implements " + needle);
    });

    const bootstrap = fs.readFileSync(path.join(SHELL_DIR, "bootstrap.js"), "utf8");
    ["alertdialog", "acp-shell-fatal-title", "acp-shell-fatal-msg"].forEach((needle) => {
      assert(bootstrap.indexOf(needle) !== -1, "fatal layer implements " + needle);
    });
  });

  // ── 2. skip link + live region ───────────────────────────────────────

  await test("A-63: skip link lands on the active workspace landmark and retargets on switch", async function () {
    const { doc, dom, A, booted } = setup();
    assertEq(booted.ok, true, "shell booted");
    await flush();

    assertEq(dom.skip.getAttribute("href"), "#acp-ws-research", "skip href targets the active workspace");
    assertEq(dom.skip.getAttribute("data-acp-skip-target"), "acp-ws-research");

    // Skip link activation (Enter on an anchor is a click) moves focus to the
    // tabpanel, not just the URL hash.
    dom.skip.focus();
    dom.skip.dispatch("click", { type: "click", preventDefault() {}, });
    assert(doc.activeElement === dom.sections.research, "skip link focuses the research tabpanel");

    // Switch workspace via the tab, then the skip link must follow.
    dom.tabs.molecular.focus();
    dom.tabs.molecular.dispatch("keydown", evt("Enter"));
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "molecular");
    assertEq(dom.skip.getAttribute("href"), "#acp-ws-molecular", "skip href retargeted");
    dom.skip.focus();
    dom.skip.dispatch("click", { type: "click", preventDefault() {} });
    assert(doc.activeElement === dom.sections.molecular, "skip link focuses the molecular tabpanel");

    // The live region announces the active workspace.
    const live = doc.getElementById("acp-shell-live");
    assert(live, "polite live region is created by the shell");
    assertEq(live.getAttribute("role"), "status");
    assertEq(live.getAttribute("aria-live"), "polite");
    assertEq(live.textContent, "分子", "live region announced the new workspace");
  });

  // ── 3. tab ARIA + keyboard activation ────────────────────────────────

  await test("A-63: tabs expose roles/labels and Arrow/Home/End/Enter/Space activation", async function () {
    const { doc, dom, A } = setup();
    await flush();

    // Static role wiring survives boot.
    WORKSPACES.forEach((ws, index) => {
      const tab = dom.tabs[ws];
      assertEq(tab.getAttribute("role"), "tab", ws + " role");
      assertEq(tab.getAttribute("aria-controls"), "acp-ws-" + ws, ws + " aria-controls");
      assertEq(tab.getAttribute("aria-keyshortcuts"), "Alt+" + (index + 1), ws + " shortcut");
      assertEq(tab.getAttribute("data-acp-focus-step"), String(index), ws + " focus step");
      assertEq(doc.getElementById("acp-ws-" + ws).getAttribute("aria-labelledby"), "acp-tab-" + ws);
    });
    assertEq(dom.nav.getAttribute("aria-label"), "工作区", "tablist aria-label applied");

    // ArrowRight moves selection + focus + roving tabindex.
    dom.tabs.research.focus();
    dom.tabs.research.dispatch("keydown", evt("ArrowRight"));
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "molecular");
    assertEq(dom.tabs.molecular.getAttribute("aria-selected"), "true");
    assertEq(dom.tabs.research.getAttribute("aria-selected"), "false");
    assertEq(dom.tabs.molecular.getAttribute("tabindex"), "0", "active tab is tabbable");
    assertEq(dom.tabs.research.getAttribute("tabindex"), "-1", "inactive tabs are skipped");
    assert(doc.activeElement === dom.tabs.molecular, "focus follows the arrow key");
    assertEq(dom.sections.molecular.hidden, false, "molecular workspace visible");
    assertEq(dom.sections.research.hidden, true, "research workspace hidden");

    // Home/End jump to the first/last workspace.
    dom.tabs.molecular.dispatch("keydown", evt("End"));
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "execution");
    assert(doc.activeElement === dom.tabs.execution, "End focuses the last tab");
    dom.tabs.execution.dispatch("keydown", evt("Home"));
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "research");
    assert(doc.activeElement === dom.tabs.research, "Home focuses the first tab");

    // Enter and Space activate the focused tab without stealing focus back
    // into the workspace (tab activation keeps the tab stop).
    dom.tabs.execution.focus();
    dom.tabs.execution.dispatch("keydown", evt(" "));
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "execution", "Space activates the tab");
    assert(doc.activeElement === dom.tabs.execution, "focus stays on the tab after Space");

    dom.tabs.research.focus();
    dom.tabs.research.dispatch("keydown", evt("Enter"));
    await flush();
    assertEq(A.layout.getCurrentWorkspace(), "research", "Enter activates the tab");
    assert(doc.activeElement === dom.tabs.research, "focus stays on the tab after Enter");

    // Down enters the A-63 walk at the first panel title.
    dom.tabs.research.dispatch("keydown", evt("ArrowDown"));
    assert(doc.activeElement === panelIn(doc, "research-agent").querySelector(".acp-panel-title"),
      "ArrowDown enters the panel-title stop");
  });

  // ── 4. layout-editor dialog: ARIA, trap, initial focus, Escape/restore ─

  await test("A-63: layout-editor dialog is a labelled modal with a Tab trap and focus restore", async function () {
    const { doc, dom } = setup();
    await flush();

    // The opener is a native <button>, so Enter/Space activate it.
    assertEq(dom.layoutBtn.tagName, "BUTTON", "layout trigger is a native button");
    assertEq(dom.layoutBtn.getAttribute("type"), "button");

    dom.layoutBtn.focus();
    dom.layoutBtn.dispatch("click", { type: "click" });
    const dialog = doc.getElementById("acp-layout-dialog");
    assert(dialog, "dialog created");
    assertEq(dialog.getAttribute("role"), "dialog", "dialog role");
    assertEq(dialog.getAttribute("aria-modal"), "true", "modal dialog");
    assertEq(dialog.getAttribute("aria-labelledby"), "acp-layout-title", "dialog is named by its title");
    assertEq(dialog.getAttribute("aria-describedby"), "acp-layout-hint", "dialog is described by its hint");
    assertEq(dialog.getAttribute("data-acp-dialog"), "layout");
    assertEq(dialog.getAttribute("data-acp-dialog-open"), "true");
    assert(doc.getElementById("acp-layout-title"), "labelled element exists");
    assert(doc.getElementById("acp-layout-hint"), "described element exists");
    assert(dialog.parentNode === dom.shell, "dialog lives inside the shell root");

    const focused = (function walk(root) {
      const out = [];
      (function collect(node) {
        node.children.forEach((child) => {
          const tag = child.tagName;
          const explicit = child.getAttribute("tabindex");
          const tabbable = explicit != null ? Number(explicit) >= 0
            : (tag === "BUTTON" || tag === "SELECT" || tag === "INPUT");
          if (tabbable) out.push(child);
          collect(child);
        });
      })(root);
      return out;
    })(dialog);
    assert(focused.length >= 6, "dialog exposes its interactive controls");
    const first = focused[0];
    const last = focused[focused.length - 1];

    // Initial focus lands on the first control, not on the page behind.
    assert(doc.activeElement === first, "initial focus on the first dialog control");

    // Tab from the last control wraps to the first.
    last.focus();
    const wrap = evt("Tab");
    dialog.dispatch("keydown", wrap);
    assert(wrap.defaultPrevented, "trap consumed the wrapping Tab");
    assert(doc.activeElement === first, "Tab wraps to the first control");

    // Shift+Tab from the first control wraps to the last.
    first.focus();
    const back = evt("Tab", { shiftKey: true });
    dialog.dispatch("keydown", back);
    assert(back.defaultPrevented, "trap consumed the reverse Tab");
    assert(doc.activeElement === last, "Shift+Tab wraps to the last control");

    // A non-edge Tab is left to the browser (no preventDefault).
    focused[1].focus();
    const middle = evt("Tab");
    dialog.dispatch("keydown", middle);
    assertEq(middle.defaultPrevented, false, "middle Tab is not hijacked");

    // Focus that escaped the dialog is pulled back in.
    dom.tabs.research.focus();
    const escapeTrap = evt("Tab");
    dialog.dispatch("keydown", escapeTrap);
    assert(doc.activeElement === first, "focus outside the dialog is trapped back in");

    // Escape closes the dialog and restores focus to the opener.
    const escape = evt("Escape");
    dialog.dispatch("keydown", escape);
    assert(escape.defaultPrevented, "Escape is consumed by the dialog");
    assertEq(dialog.getAttribute("data-acp-dialog-open"), "false", "dialog reported closed");
    assertEq(dialog.open, false, "dialog.close() ran");
    assert(doc.activeElement === dom.layoutBtn, "focus restored to the opener");

    // Re-opening restores the same contract.
    dom.layoutBtn.focus();
    dom.layoutBtn.dispatch("click", { type: "click" });
    const reopened = doc.getElementById("acp-layout-dialog");
    assertEq(reopened.getAttribute("data-acp-dialog-open"), "true");
    assert(doc.activeElement.tagName === "SELECT", "reopened dialog focuses its first select");
    const closeEscape = evt("Escape");
    reopened.dispatch("keydown", closeEscape);
    assert(doc.activeElement === dom.layoutBtn, "focus restored again");

    // Cancel button also restores focus (same close path).
    dom.layoutBtn.focus();
    dom.layoutBtn.dispatch("click", { type: "click" });
    const actions = doc.getElementById("acp-layout-dialog").querySelector(".acp-layout-actions");
    const cancel = actions.children.find((child) => child.textContent === "取消");
    assert(cancel, "cancel button exists");
    cancel.dispatch("click", { type: "click" });
    assert(doc.activeElement === dom.layoutBtn, "cancel restores focus");
  });

  await test("A-63: apply keeps the modal focus contract (persisted closes, session-only stays inside)", async function () {
    const { doc, dom } = setup();
    await flush();

    dom.layoutBtn.focus();
    dom.layoutBtn.dispatch("click", { type: "click" });
    let dialog = doc.getElementById("acp-layout-dialog");
    const actions = dialog.querySelector(".acp-layout-actions");
    const apply = actions.children.find((child) => child.textContent === "应用布局");
    assert(apply, "apply button exists");
    apply.dispatch("click", { type: "click" });
    assertEq(dialog.getAttribute("data-acp-dialog-open"), "false", "persisted apply closes the dialog");
    assert(doc.activeElement === dom.layoutBtn, "persisted apply restores focus to the opener");

    // Storage blocked → the dialog stays open with focus still inside it.
    const blocked = setup({ readonlyStorage: true });
    await flush();
    blocked.dom.layoutBtn.focus();
    blocked.dom.layoutBtn.dispatch("click", { type: "click" });
    dialog = blocked.doc.getElementById("acp-layout-dialog");
    const blockedActions = dialog.querySelector(".acp-layout-actions");
    const blockedApply = blockedActions.children.find((child) => child.textContent === "应用布局");
    blockedApply.focus();
    blockedApply.dispatch("click", { type: "click" });
    assertEq(dialog.getAttribute("data-acp-dialog-open"), "true", "session-only apply keeps the dialog open");
    assert(blocked.doc.activeElement === blockedApply, "focus is not stolen out of the open dialog");
    assert(dialog.querySelector('[role="status"]').textContent.length > 0,
      "the session-only reason is visible to the user");
  });

  // ── 5. panel landmarks / accessible names via shell wrappers ─────────

  await test("A-63: every shell panel is a named region and its title/body carry focus metadata", async function () {
    const { doc, dom } = setup();
    await flush();

    const panels = doc.querySelectorAll(".acp-panel");
    assert(panels.length >= 3, "the research recipe rendered its panels");
    const ids = new Set();
    panels.forEach((panel) => {
      const instanceId = panel.getAttribute("data-instance-id");
      assert(instanceId, "panel carries its instance id");
      ids.add(panel.getAttribute("id"));
      assertEq(panel.getAttribute("role"), "region", instanceId + " is a region landmark");
      assertEq(panel.getAttribute("data-acp-landmark"), "panel");
      const labelledby = panel.getAttribute("aria-labelledby");
      assert(labelledby, instanceId + " has an accessible name");
      const title = doc.getElementById(labelledby);
      assert(title, instanceId + " aria-labelledby resolves: " + labelledby);
      assertEq(title.getAttribute("class"), "acp-panel-title");
      assert(String(title.textContent || "").length > 0, instanceId + " title is not empty");
      assertEq(title.getAttribute("tabindex"), "0", instanceId + " title is a focus stop");
      assertEq(title.getAttribute("data-acp-focus-kind"), "panel-title");
      const body = panel.querySelector(".acp-panel-body");
      assert(body, instanceId + " has a body");
      assertEq(body.getAttribute("tabindex"), "-1", instanceId + " body is a programmatic content anchor");
      assertEq(body.getAttribute("data-acp-focus-kind"), "panel-content");
      // The panel must live inside a named tabpanel.
      const section = panel.parentNode.parentNode;
      assertEq(section.getAttribute("role"), "tabpanel");
      assertEq(section.getAttribute("aria-labelledby"), "acp-tab-research");
    });
    assertEq(ids.size, panels.length, "panel ids are unique");

    // Placeholder panels (unregistered modules) still get an accessible name.
    const placeholder = panelIn(doc, "research-agent");
    assertEq(placeholder.getAttribute("role"), "region");
    assert(doc.getElementById(placeholder.getAttribute("aria-labelledby")));

    // The workspace landmark itself is named and focusable.
    assertEq(dom.sections.research.getAttribute("role"), "tabpanel");
    assertEq(dom.sections.research.getAttribute("tabindex"), "0");
    assertEq(dom.main.getAttribute("aria-label"), "工作区");
  });

  // ── 6. focus-order metadata + the navigable walk ─────────────────────

  await test("A-63: focus order metadata walks tabs → panel titles → panel content", async function () {
    const { doc, dom, A } = setup();
    await flush();

    assertEq(A.layout.focusOrder("research").order, [
      "tab:research", "tab:molecular", "tab:execution",
      "panel-title:research-agent", "panel-content:research-agent",
      "panel-title:shared-viewer", "panel-content:shared-viewer",
      "panel-title:research-tasks", "panel-content:research-tasks",
    ], "focusOrder() documents the A-63 walk");

    // The same order is readable from the DOM metadata.
    WORKSPACES.forEach((ws, index) => {
      assertEq(dom.tabs[ws].getAttribute("data-acp-focus-step"), String(index));
      assertEq(dom.tabs[ws].getAttribute("data-acp-focus-kind"), "tab");
    });
    assertEq(panelIn(doc, "research-agent").querySelector(".acp-panel-title").getAttribute("data-acp-focus-step"), "3");
    assertEq(panelIn(doc, "research-agent").querySelector(".acp-panel-body").getAttribute("data-acp-focus-step"), "4");
    assertEq(panelIn(doc, "shared-viewer").querySelector(".acp-panel-title").getAttribute("data-acp-focus-step"), "5");
    assertEq(panelIn(doc, "research-tasks").querySelector(".acp-panel-title").getAttribute("data-acp-focus-step"), "7");
    assertEq(dom.sections.research.getAttribute("data-acp-focus-sequence"),
      "main:research-agent,side:shared-viewer,bottom:research-tasks",
      "workspace metadata lists the panel walk in slot order");

    // Execution gets its own sequence after the switch.
    dom.tabs.execution.focus();
    dom.tabs.execution.dispatch("keydown", evt("Enter"));
    await flush();
    assertEq(A.layout.focusOrder("execution").order, [
      "tab:research", "tab:molecular", "tab:execution",
      "panel-title:exec-tasks", "panel-content:exec-tasks",
      "panel-title:exec-detail", "panel-content:exec-detail",
      "panel-title:exec-network", "panel-content:exec-network",
    ]);
    assertEq(dom.sections.execution.getAttribute("data-acp-focus-sequence"),
      "main:exec-tasks,side:exec-detail,bottom:exec-network");
  });

  await test("A-63: keyboard walk reaches a real module control and Escape climbs back out", async function () {
    let mounted = null;
    const { doc, dom, A } = setup({
      register: (shell) => {
        const result = shell.registry.register(
          {
            id: "agent-workspace",
            interfaceVersion: 1,
            version: "0.1.0",
            provides: ["research.summary"],
            binds: ["researchRef"],
          },
          function () {
            return {
              mount(hostEl) {
                const button = hostEl.ownerDocument.createElement("button");
                button.setAttribute("id", "agent-cta");
                button.setAttribute("type", "button");
                button.textContent = "打开研究记录";
                hostEl.appendChild(button);
                mounted = button;
              },
              updateContext() {},
              dispose() {},
            };
          }
        );
        assertEq(result.ok, true, "stub module registered");
      },
    });
    await flush();
    assert(mounted, "module mounted a real control into the panel body");

    // tabs → panel title → panel content
    dom.tabs.research.focus();
    dom.tabs.research.dispatch("keydown", evt("ArrowDown"));
    const title = panelIn(doc, "research-agent").querySelector(".acp-panel-title");
    assert(doc.activeElement === title, "Down from the tab reaches the panel title");

    const enter = evt("Enter");
    title.dispatch("keydown", enter);
    assert(enter.defaultPrevented, "Enter on the title is consumed");
    assert(doc.activeElement === mounted, "Enter on the title focuses the first module control");

    // Escape ladder: body → title → workspace tab.
    const body = panelIn(doc, "research-agent").querySelector(".acp-panel-body");
    body.focus();
    const bodyEscape = evt("Escape");
    body.dispatch("keydown", bodyEscape);
    assert(doc.activeElement === title, "Escape on the body returns to the panel title");

    const titleEscape = evt("Escape");
    title.dispatch("keydown", titleEscape);
    assert(doc.activeElement === dom.tabs.research, "Escape on the title returns to the workspace tab");

    // Escape from the workspace landmark itself returns to the tablist.
    dom.sections.research.focus();
    const sectionEscape = evt("Escape");
    dom.sections.research.dispatch("keydown", sectionEscape);
    assert(doc.activeElement === dom.tabs.research, "Escape on the landmark returns to the tablist");

    // Keys bubbling from a MODULE control are never hijacked by the shell.
    const moduleKey = evt("ArrowDown", { target: mounted });
    dom.sections.research.dispatch("keydown", moduleKey);
    assertEq(moduleKey.defaultPrevented, false, "workspace landmark does not hijack module keys");
    const moduleEscape = evt("Escape", { target: mounted });
    dom.sections.research.dispatch("keydown", moduleEscape);
    assertEq(moduleEscape.defaultPrevented, false, "module Escape stays with the module");

    // Up from a title steps back to the previous title (or the tab).
    const viewerTitle = panelIn(doc, "shared-viewer").querySelector(".acp-panel-title");
    viewerTitle.focus();
    viewerTitle.dispatch("keydown", evt("ArrowUp"));
    assert(doc.activeElement === title, "Up steps back to the previous panel title");

    // focusPanelContent is a public, read-only focus helper.
    assertEq(A.layout.focusPanelContent("research-agent"), true);
    assert(doc.activeElement === mounted, "focusPanelContent lands on module content");
    assertEq(A.layout.focusPanelContent("does-not-exist"), false);
  });
};
