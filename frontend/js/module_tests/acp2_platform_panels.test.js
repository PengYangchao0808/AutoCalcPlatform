"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  const fs = require("fs"), path = require("path"), root = path.resolve(__dirname, "..", "..");
  function node() { return { children: [], attrs: {}, hidden: false, textContent: "", setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; }, appendChild(n) { this.children.push(n); return n; }, replaceChildren(...ns) { this.children = []; ns.forEach(n => this.appendChild(n)); }, addEventListener(t, fn) { (this.listeners || (this.listeners = {}))[t] = fn; }, focus() { this.focused = true; } }; }
  await test("platform topbar has only System, Approvals and A capability entry; no tool drawer", function () {
    const html = fs.readFileSync(path.join(root, "next/index.html"), "utf8");
    ["acp-system-btn", "acp-approvals-btn", "acp-capability-btn", "acp-platform-panel"].forEach(id => assert(html.includes('id="' + id + '"')));
    assert(!html.includes("acp-tools-btn") && !html.includes("acp-tools-drawer"));
    assert(html.indexOf("acp-capability-btn") > html.indexOf('id="acp-ws-research"'), "capability starts from A");
  });
  function setup() {
    const ids = {}; ["acp-platform-panel", "acp-platform-title", "acp-platform-host", "acp-platform-close", "acp-shell-main", "acp-approvals-btn", "acp-system-btn", "acp-capability-btn"].forEach(id => ids[id] = node());
    ids["acp-platform-panel"].hidden = true;
    const doc = { createElement: () => node(), getElementById: id => ids[id] || null };
    const mounted = [], disposed = [];
    const registry = { resolve: id => ({ ok: true, factory: () => ({ mount() { mounted.push(id); }, updateContext() {}, dispose() { disposed.push(id); } }) }) };
    const s = makeSandbox({ document: doc });
    s.ACPShell = { env: { doc: () => doc }, context: { getSnapshot: () => ({}) }, commands: {}, i18n: {}, registry };
    loadScript("js/shell/platform-panels.js", s);
    return { s, ids, mounted, disposed, doc };
  }
  await test("System status and approvals mount their real modules, one at a time", async function () {
    const c = setup(); c.s.ACPShell.platformPanels.init();
    c.ids["acp-system-btn"].listeners.click(); await Promise.resolve(); await Promise.resolve();
    assertEq(c.mounted, ["node-status"]); assertEq(c.ids["acp-platform-panel"].hidden, false);
    c.ids["acp-approvals-btn"].listeners.click(); await Promise.resolve(); await Promise.resolve();
    assertEq(c.mounted, ["node-status", "approval-card"]); assertEq(c.disposed, ["node-status"]);
    c.ids["acp-platform-close"].listeners.click(); assert(c.disposed.indexOf("approval-card") >= 0); assertEq(c.ids["acp-platform-panel"].hidden, true);
  });
  await test("A capability entry mounts the real capability form in its project context", async function () {
    const c = setup(); c.s.ACPShell.platformPanels.init(); c.ids["acp-capability-btn"].listeners.click(); await Promise.resolve(); await Promise.resolve();
    assertEq(c.mounted, ["capability-form"]);
  });
};
