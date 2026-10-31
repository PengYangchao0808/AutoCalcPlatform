/* Right-column panel layout: collapse keys + scroll affordance wiring.
 *
 * Loads the REAL js/workbench/compat/shared-state.js in the vm harness and
 * drives its applyLayout() path with a stub DOM.
 *
 * Pinned contracts:
 *   1. LAYOUT_DEFAULTS.panels carries every collapsible panel key, including
 *      scientific_evidence and research_approval (dead − buttons regression);
 *   2. a collapse state change really re-lays out: panel classes and the −/+
 *      glyph flip through applyLayout();
 *   3. the right column is the scroll container with a visible affordance
 *      (overflow-y auto + scroll cue wiring);
 *   4. bootstrap wires the .panel-collapse-btn clicks to layoutState.panels.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FRONTEND = path.resolve(__dirname, "..", "..");
const SHARED_STATE = "js/workbench/compat/shared-state.js";

function makeEl(id) {
  const classes = new Set();
  return {
    id,
    textContent: "",
    innerHTML: "",
    style: {
      props: {},
      setProperty(name, value) { this.props[name] = value; },
      removeProperty(name) { delete this.props[name]; },
    },
    classList: {
      toggle(name, force) {
        if (force === undefined) force = !classes.has(name);
        if (force) classes.add(name);
        else classes.delete(name);
        return force;
      },
      contains(name) { return classes.has(name); },
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
    },
    attrs: {},
    listeners: {},
    children: [],
    scrollHeight: 2000,
    clientHeight: 400,
    scrollTop: 0,
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) {
      return this.attrs[name] === undefined ? null : this.attrs[name];
    },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    appendChild(child) { this.children.push(child); return child; },
  };
}

function makeDom() {
  const elements = new Map();
  const panels = [];
  function el(id) {
    if (!elements.has(id)) {
      const node = makeEl(id);
      node.appendChild = (child) => {
        node.children.push(child);
        if (child && child.id) elements.set(child.id, child);
        return child;
      };
      elements.set(id, node);
    }
    return elements.get(id);
  }
  return {
    el,
    panels,
    documentElement: makeEl("html"),
    getElementById(id) {
      return elements.get(id) || null;
    },
    querySelector(selector) {
      if (selector === ".workbench") return el("workbench");
      if (selector === ".right-col") return el("right-col-zone");
      if (selector === ".left-col") return el("left-col-zone");
      return null;
    },
    querySelectorAll(selector) {
      if (selector === "[data-panel-key]") return panels.slice();
      return [];
    },
    createElement(tag) { return makeEl(tag); },
    register(node) { if (node && node.id) elements.set(node.id, node); },
    addEventListener() {},
  };
}

function panelStub(key) {
  const node = makeEl(key);
  node.setAttribute("data-panel-key", key);
  return node;
}

function loadSharedState(h, options) {
  options = options || {};
  const dom = makeDom();
  const storage = {};
  const sandbox = h.makeSandbox({
    document: dom,
    Intl,
    innerWidth: 1440,
    matchMedia: (query) => ({
      matches: false,
      media: String(query),
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }),
    localStorage: {
      getItem: (k) => (k in storage ? storage[k] : null),
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    },
    requestAnimationFrame: (fn) => { fn(); return 1; },
    console: { log() {}, warn() {}, error() {} },
  });
  sandbox.window = sandbox;
  h.loadScript(SHARED_STATE, sandbox);
  return { sandbox, dom, storage };
}

function probe(sandbox, expression) {
  return vm.runInContext(expression, sandbox);
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  await test("LAYOUT_DEFAULTS wires every collapsible panel key", () => {
    const { sandbox } = loadSharedState(h);
    const panels = probe(sandbox, "LAYOUT_DEFAULTS.panels");
    ["file_tree", "log", "queue", "info", "science_question",
      "scientific_evidence", "research_approval"].forEach((key) => {
      assert(panels[key] && panels[key].collapsed === false,
        "default collapse state for " + key);
    });
    const live = probe(sandbox, "layoutState.panels");
    assert(live.scientific_evidence && live.research_approval,
      "live layout state carries the evidence/approval keys");
  });

  await test("applyLayout really re-lays-out on collapse (classes + −/+ glyph)", () => {
    const { sandbox, dom } = loadSharedState(h);
    ["scientific_evidence", "research_approval", "science_question"].forEach((key) => {
      dom.panels.push(panelStub(key));
    });
    const btn = panelStub("scientific_evidence");
    btn.classList.add("panel-collapse-btn");
    btn.textContent = "−";
    dom.panels.push(btn);

    probe(sandbox, "applyLayout({skipViewer: true})");
    const section = dom.panels[0];
    assertEq(section.classList.contains("collapsed"), false, "starts expanded");
    assertEq(btn.textContent, "−", "expanded glyph");

    probe(sandbox,
      "layoutState.panels.scientific_evidence.collapsed = true; applyLayout({skipViewer: true})");
    assertEq(section.classList.contains("collapsed"), true, "collapse class applied");
    assertEq(btn.textContent, "+", "collapsed glyph applied");

    probe(sandbox,
      "layoutState.panels.research_approval.collapsed = true; applyLayout({skipViewer: true})");
    assertEq(dom.panels[1].classList.contains("collapsed"), true,
      "approval panel collapse applied");

    probe(sandbox,
      "layoutState.panels.scientific_evidence.collapsed = false; applyLayout({skipViewer: true})");
    assertEq(section.classList.contains("collapsed"), false, "re-expand applied");
    assertEq(btn.textContent, "−", "glyph restored");
  });

  await test("right column is the scroll container with a visible scroll affordance", () => {
    const { sandbox, dom } = loadSharedState(h);
    probe(sandbox, "applyLayout({skipViewer: true})");
    const col = dom.el("right-col-zone");
    assertEq(col.children.length >= 1, true, "scroll cue attached to the right column");
    const cue = col.children[col.children.length - 1];
    assertEq(cue.className, "right-col-scroll-cue", "cue element");
    assertEq(cue.hidden, false, "cue visible while more content is below");
    col.scrollTop = 2000;
    col.listeners.scroll.forEach((fn) => fn());
    const css = fs.readFileSync(
      path.join(FRONTEND, "css", "workbench", "layout.css"), "utf8");
    assert(/\.right-col\s*\{[^}]*overflow-y:\s*auto/s.test(css),
      "right column scrolls vertically");
    assert(/\.right-col\s*\{[^}]*overflow-x:\s*hidden/s.test(css),
      "no horizontal scroll");
    assert(css.indexOf(".right-col::-webkit-scrollbar-thumb") !== -1,
      "styled visible scrollbar");
    assert(/\.right-col-scroll-cue/.test(css), "cue styled");
  });

  await test("bootstrap wires panel-collapse-btn clicks to the layout state", () => {
    const source = fs.readFileSync(
      path.join(FRONTEND, "js", "workbench", "bootstrap.js"), "utf8");
    assert(source.indexOf('document.querySelectorAll(".panel-collapse-btn")') !== -1,
      "collapse buttons are wired");
    assert(/st\.collapsed = !st\.collapsed/.test(source), "click toggles the state");
    assert(/saveLayoutState\(\)/.test(source), "state is persisted");
  });

  await test("research panels contain overflow and queue cannot shrink behind them", () => {
    const css = fs.readFileSync(
      path.join(FRONTEND, "css", "workbench", "features.css"), "utf8");
    ["science-question-panel", "scientific-evidence-panel", "research-approval-panel"]
      .forEach((cls) => {
        const rule = new RegExp("\\." + cls + "\\s*\\{[^}]*\\}", "s").exec(css);
        assert(rule, "panel rule exists for " + cls);
        const body = new RegExp("\\." + cls + " \\.panel-body\\s*\\{[^}]*\\}", "s").exec(css);
        assert(body, "panel body rule exists for " + cls);
        assert(body[0].indexOf("overflow: visible") !== -1,
          cls + " body scrolls as part of the panel");
      });
    const queueCss = fs.readFileSync(
      path.join(FRONTEND, "css", "workbench", "queue-detail.css"), "utf8");
    assert(/\.right-col > \.queue-panel\s*\{[^}]*flex:\s*0 0 auto/s.test(queueCss),
      "queue retains space when research content grows");
    assert(/\.right-col > \.panel-section\s*\{[^}]*max-height:[^}]*overflow:\s*auto/s.test(queueCss),
      "research panels have a bounded, reachable scroll area");
    const row = /\.ra-row\s*\{[^}]*\}/s.exec(css);
    assert(row && row[0].indexOf("flex-wrap: wrap") !== -1, "ra-row wraps label/value");
    assert(/\.ra-row dt[^}]*word-break:\s*keep-all/s.test(css), "labels keep CJK words intact");
    assert(/\.ra-digest[^}]*overflow-wrap:\s*anywhere/s.test(css),
      "only the digest value may break anywhere");
    assert(css.indexOf("word-break: break-all") === -1,
      "no remaining break-all glyph stacks in the feature stylesheet");
    assert(/prefers-reduced-motion/.test(fs.readFileSync(
      path.join(FRONTEND, "css", "workbench", "base.css"), "utf8")),
      "reduced motion is respected");
  });
};
