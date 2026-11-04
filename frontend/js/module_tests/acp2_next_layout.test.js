"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  const fs = require("fs"), path = require("path");
  const root = path.resolve(__dirname, "..", "..");
  await test("NEXT production entry keeps six views and only the persistent D anchor", function () {
    const html = fs.readFileSync(path.join(root, "next/index.html"), "utf8");
    ["research", "drawing", "molecular", "config", "execution", "files"].forEach(id => {
      assert(html.includes('id="acp-tab-' + id + '"')); assert(html.includes('id="acp-ws-' + id + '"'));
    });
    ["acp-next-task-host", "acp-next-task-rail", "acp-next-status", "acp-ws-files", "acp-slot-files-main"].forEach(id => assert(html.includes('id="' + id + '"')));
    assert(!html.includes("acp-next-dock") && !html.includes("acp-next-view-letter"), "global G dock and dev letters are gone");
    assert(html.includes('id="acp-next-root"') && html.includes("ACP Next"));
    assert(!html.includes("m1") && !html.includes("演示预检"));
    assert(html.includes("/js/shell/global-regions.js") && html.includes("/js/modules/structure-import/module.js"));
    assert(html.includes("/js/modules/files-workspace/module.js") && html.includes("/js/client/project-files.js"));
  });
  await test("NEXT recipes use full wizard, one C/F Viewer and the files workspace", function () {
    const s = makeSandbox(); loadScript("js/shell/recipes.js", s);
    assertEq(s.ACPShell.recipes.list(), ["research", "drawing", "molecular", "config", "execution", "files"]);
    assertEq(s.ACPShell.recipes.get("config").panels[0].moduleId, "config-workspace");
    const viewers = [].concat(s.ACPShell.recipes.get("molecular").panels, s.ACPShell.recipes.get("execution").panels).filter(p => p.moduleId === "molecule-viewer");
    assertEq(viewers.map(p => p.instanceId), ["shared-viewer", "shared-viewer"]);
    assertEq(s.ACPShell.recipes.get("execution").panels[0].moduleId, "results-workspace");
    assertEq(s.ACPShell.recipes.get("files").panels[0].moduleId, "files-workspace");
  });
  function node(id) {
    return { id, children: [], hidden: false, textContent: "", attrs: {}, parentNode: null, style: { setProperty(k, v) { this[k] = v; } },
      classList: { values: new Set(), add(v) { this.values.add(v); }, remove(v) { this.values.delete(v); }, toggle(v, on) { if (on) this.values.add(v); else this.values.delete(v); }, contains(v) { return this.values.has(v); } },
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
      appendChild(n) { n.parentNode = this; this.children.push(n); return n; }, replaceChildren(...ns) { this.children = []; ns.forEach(n => this.appendChild(n)); },
      addEventListener(type, fn) { (this.listeners || (this.listeners = {}))[type] = fn; }, click() { if (this.listeners && this.listeners.click) this.listeners.click(); }, focus() {},
    };
  }
  await test("global D mounts once, drags/persists its dock and files mode hides it", async function () {
    const ids = ["acp-shell", "acp-next-body", "acp-next-task-host", "acp-next-task-sash", "acp-next-task-mode", "acp-next-task-open", "acp-next-task-close", "acp-next-task-rail", "acp-next-status", "acp-shell-main", "acp-next-job-edit"];
    const nodes = {}; ids.forEach(id => nodes[id] = node(id));
    nodes["acp-shell"].clientWidth = 1302;
    const listeners = {}, updates = [], store = {};
    const doc = { getElementById: id => nodes[id] || null, createElement: () => node(""), addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); }, removeEventListener() {} };
    const s = makeSandbox({ document: doc });
    s.ACPShell = { env: { doc: () => doc, storage: { get: k => store[k], set: (k, v) => { store[k] = v; } } }, commands: {}, i18n: {} };
    const context = { getSnapshot: () => ({ taskRef: { jobId: "real-job", attempt: 2 } }), subscribe: fn => { s.notify = fn; return () => {}; } };
    const mounts = [];
    const loader = { instantiate: (moduleId, instanceId) => { mounts.push(instanceId); return Promise.resolve({ instance: { mount() {}, updateContext(s) { updates.push([moduleId, s.taskRef.jobId]); }, suspend() {}, resume() {}, dispose() {} } }); } };
    s.ACPShell.globalRegions = undefined; loadScript("js/shell/global-regions.js", s);
    s.ACPShell.globalRegions.init({ context, loader }); s.ACPShell.globalRegions.init({ context, loader });
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    assertEq(mounts, ["global-tasks"]); assertEq(updates.map(x => x[1]), ["real-job"]);
    assertEq(nodes["acp-shell"].getAttribute("data-task-mode"), "right");
    assertEq(nodes["acp-next-body"].style["--next-task-w"], "320px");
    assertEq(nodes["acp-next-task-sash"].getAttribute("aria-valuenow"), "320");
    nodes["acp-next-task-close"].listeners.click();
    assertEq(nodes["acp-next-task-rail"].getAttribute("inert"), "");
    assertEq(nodes["acp-next-task-open"].hidden, false, "collapsed dock exposes its opener");
    assertEq(nodes["acp-next-task-open"].getAttribute("aria-expanded"), "false");
    nodes["acp-next-task-open"].listeners.click();
    assertEq(nodes["acp-next-task-rail"].getAttribute("inert"), null);
    nodes["acp-next-task-sash"].listeners.keydown({ key: "End", preventDefault() {} });
    assertEq(nodes["acp-next-task-sash"].getAttribute("aria-valuenow"), "651", "max width is 50vw");
    nodes["acp-next-task-sash"].listeners.keydown({ key: "Home", preventDefault() {} });
    assertEq(JSON.parse(store["acp-next-task-dock-v3"]).width, 260, "min width is 260");
    nodes["acp-next-task-mode"].value = "bottom";
    nodes["acp-next-task-mode"].listeners.change();
    assertEq(nodes["acp-shell"].getAttribute("data-task-mode"), "bottom");
    assertEq(nodes["acp-next-task-sash"].getAttribute("aria-orientation"), "horizontal");
    assertEq(JSON.parse(store["acp-next-task-dock-v3"]).mode, "bottom");
    s.ACPShell.globalRegions.setFilesView(true);
    assertEq(nodes["acp-next-task-rail"].classList.contains("is-files-hidden"), true);
    assertEq(nodes["acp-next-task-rail"].getAttribute("inert"), "");
    assertEq(nodes["acp-next-task-rail"].getAttribute("aria-hidden"), "true");
    assertEq(nodes["acp-shell-main"].getAttribute("inert"), null, "files content is not inert");
    s.ACPShell.globalRegions.setFilesView(false);
    assertEq(nodes["acp-next-task-rail"].classList.contains("is-files-hidden"), false);
    assertEq(mounts.length, 1, "D's instance stays alive across dock/files changes");
    s.ACPShell.globalRegions.dispose();
  });
  await test("NEXT typography and task-card layout follow the 4px information hierarchy", function () {
    const css = fs.readFileSync(path.join(root, "css/modules/task-explorer.css"), "utf8");
    assert(/\.acp-mod-tasks-row\s*\{[^}]*display:\s*grid/.test(css), "task row is a grid, not a wrapping flex row");
    assert(/grid-template-areas:[\s\S]*?"select name status more"/.test(css), "status/name/more use explicit areas");
    assert(/\.acp-mod-tasks-row-name\s*\{[^}]*font-size:\s*13px[^}]*line-height:\s*18px/.test(css), "task name is 13/18");
    assert(/\.acp-mod-tasks-row-meta\s*\{[^}]*font-size:\s*11px[^}]*line-height:\s*15px/.test(css), "meta line is 11/15");
    assert(/min-height:\s*56px/.test(css) && /min-height:\s*68px/.test(css), "task cards target 56/68px");
    assert(/\.acp-mod-tasks-more\s*\{[^}]*grid-area:\s*more/.test(css), "all actions use the more disclosure");
    const layout = fs.readFileSync(path.join(root, "css/shell/next-layout.css"), "utf8");
    assert(/\.acp-panel-title\s*\{[^}]*font-size:\s*14px[^}]*line-height:\s*20px/.test(layout), "panel titles are 14/20");
    assert(/\.acp-panel-body\s*\{\s*font-size:\s*13px;\s*line-height:\s*19px/.test(layout), "panel body is 13/19");
  });
  await test("B import uses the real parse seam and contains no drawing/submit facade", function () {
    const source = fs.readFileSync(path.join(root, "js/modules/structure-import/module.js"), "utf8");
    assert(source.includes('/structures/parse')); assert(source.includes("ACPClient.transport.request"));
    assert(!source.includes("fetch(") && !source.includes("/jobs") && !source.includes("<svg"));
    const s = makeSandbox(); loadScript("js/modules/structure-import/module.js", s);
    assertEq(s.ACPNextModules["structure-import"].descriptor.id, "structure-import");
  });
};
