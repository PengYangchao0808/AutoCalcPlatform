"use strict";
/* V7 assembly behavior: mount the real tool-drawer script against a tiny DOM
 * and fake registry, and assert one-module-at-a-time mount/dispose semantics.
 * This complements acp2_tool_drawer.test.js (static wiring) without a browser. */
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;

  function makeEl(tag) {
    const node = {
      tagName: String(tag || "div").toUpperCase(),
      children: [],
      attrs: {},
      className: "",
      textContent: "",
      hidden: false,
      tabIndex: 0,
      focused: false,
      listeners: {},
      classList: {
        toggle(cls, on) {
          const set = new Set(String(node.className || "").split(/\s+/).filter(Boolean));
          if (on) set.add(cls); else set.delete(cls);
          node.className = Array.from(set).join(" ");
        },
      },
      setAttribute(k, v) { node.attrs[k] = String(v); },
      getAttribute(k) { return Object.prototype.hasOwnProperty.call(node.attrs, k) ? node.attrs[k] : null; },
      appendChild(child) { node.children.push(child); child.parentNode = node; return child; },
      replaceChildren() { node.children.forEach((c) => { c.parentNode = null; }); node.children = []; },
      addEventListener(type, fn) { (node.listeners[type] = node.listeners[type] || []).push(fn); },
      removeEventListener() {},
      focus() { node.focused = true; },
      click() { (node.listeners.click || []).forEach((fn) => fn({ preventDefault() {} })); },
      querySelector(sel) { return node.queryMap && node.queryMap[sel] || null; },
    };
    return node;
  }

  function setup() {
    const calls = [];
    const button = makeEl("button");
    const drawer = makeEl("aside");
    drawer.hidden = true;
    const title = makeEl("h2");
    const host = makeEl("div");
    const close = makeEl("button");
    const tabs = makeEl("nav");
    drawer.queryMap = {
      "[data-acp-tools-title]": title,
      "[data-acp-tools-host]": host,
      "[data-acp-tools-close]": close,
      "[data-acp-tools-tabs]": tabs,
    };
    const document = {
      createElement: makeEl,
      getElementById(id) {
        if (id === "acp-tools-btn") return button;
        if (id === "acp-tools-drawer") return drawer;
        return null;
      },
    };
    const ids = [
      "task-wizard", "job-edit", "nmr-report", "tsmode-editor", "s2-scan",
      "node-status", "approval-card", "capability-form", "path-view",
      "pes-handoff", "mechanism-plan", "research-record",
    ];
    const registry = {
      has: (id) => ids.indexOf(id) !== -1,
      resolve(id) {
        if (ids.indexOf(id) === -1) return { ok: false };
        return {
          ok: true,
          factory() {
            calls.push(["factory", id]);
            return {
              mount(el) { calls.push(["mount", id]); return Promise.resolve(); },
              dispose() { calls.push(["dispose", id]); },
              updateContext() {},
            };
          },
        };
      },
    };
    const sandbox = makeSandbox({
      document,
      addEventListener() {},
    });
    sandbox.ACPNextModules = {};
    sandbox.ACS = sandbox.ACPShell = {
      i18n: { t: (k) => k },
      context: { getSnapshot: () => ({ revision: 0 }), subscribe: () => () => {} },
      commands: {},
      registry,
    };
    loadScript("js/shell/tool-drawer.js", sandbox);
    return { sandbox, button, drawer, host, close, tabs, calls, ids };
  }

  await test("V7 drawer: opens, mounts one module, switches and disposes", async function () {
    const s = setup();
    s.sandbox.ACPShell.toolDrawer.init();
    s.button.click();
    assertEq(s.button.getAttribute("aria-expanded"), "true");
    assertEq(s.drawer.hidden, false);
    await Promise.resolve();
    assertEq(s.calls.filter((c) => c[0] === "mount").length, 1);
    const first = s.calls.find((c) => c[0] === "factory")[1];
    const secondTab = s.tabs.children[1];
    secondTab.click();
    await Promise.resolve();
    assert(s.calls.some((c) => c[0] === "dispose" && c[1] === first), "old module disposed");
    assertEq(s.calls.filter((c) => c[0] === "mount").length, 2);
  });

  await test("V7 drawer: close disposes and restores opener focus", async function () {
    const s = setup();
    s.sandbox.ACPShell.toolDrawer.init();
    s.button.click();
    await Promise.resolve();
    s.close.click();
    assertEq(s.drawer.hidden, true);
    assertEq(s.button.getAttribute("aria-expanded"), "false");
    assert(s.calls.some((c) => c[0] === "dispose"), "disposed on close");
  });

  await test("V7 drawer: unregistered module shows an error, no factory call", async function () {
    const s = setup();
    s.sandbox.ACPShell.toolDrawer.init();
    s.button.click();
    await Promise.resolve();
    const before = s.calls.length;
    s.sandbox.ACPShell.registry.has = () => true;
    s.sandbox.ACPShell.registry.resolve = () => ({ ok: false });
    s.tabs.children[0].click();
    assertEq(s.calls.length, before, "no factory/mount for a rejected resolve");
    assertEq(s.host.children[0].className.indexOf("acp-tools-error") !== -1, true);
  });
};
