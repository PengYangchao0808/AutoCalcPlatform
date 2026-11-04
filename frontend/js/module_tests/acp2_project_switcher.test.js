"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  function node() {
    return { tagName: "DIV", children: [], attrs: {}, parentNode: null, hidden: false, textContent: "", className: "", style: {}, disabled: false,
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
      appendChild(c) { c.parentNode = this; this.children.push(c); return c; }, replaceChildren(...cs) { this.children = []; cs.forEach(c => this.appendChild(c)); },
      addEventListener(t, fn) { (this.listeners = this.listeners || {})[t] = (this.listeners[t] || []).concat(fn); },
      querySelectorAll(sel) { const out = []; (function walk(x) { x.children.forEach(c => { if (sel.indexOf("[role='option']") !== -1 ? c.getAttribute("role") === "option" : c.tagName === "BUTTON") out.push(c); walk(c); }); })(this); return out; },
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }, focus() { this.focused = true; },
    };
  }
  function setup(responder) {
    const ids = ["acp-project-btn", "acp-project-popover", "acp-project-name", "acp-project-search", "acp-project-status", "acp-project-list", "acp-project-retry", "acp-project-refresh"];
    const nodes = {}; ids.forEach(id => nodes[id] = node()); nodes["acp-project-popover"].hidden = true;
    const doc = { getElementById: id => nodes[id] || null, createElement: () => node(), addEventListener(t, fn) { (doc.listeners = doc.listeners || {})[t] = fn; }, activeElement: null, body: node() };
    const listeners = [];
    let snap = { projectId: null };
    const context = { getSnapshot: () => snap, subscribe(fn) { listeners.push(fn); return () => {}; }, setFocus(patch, source) { snap = Object.assign({}, snap, patch); listeners.forEach(fn => fn(snap)); this.calls = this.calls || []; this.calls.push({ patch, source }); } };
    const s = makeSandbox({ document: doc }); s.ACPShell = { env: { doc: () => doc }, context };
    loadScript("js/shell/project-switcher.js", s);
    s.ACPShell.projectSwitcher.init({ context, request: (p, o) => { assertEq(p, "/projects"); assertEq(o.method, "GET"); return responder(); } });
    return { s, context, nodes, doc };
  }
  const flush = () => Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve());
  await test("project selector uses GET /projects names, search and WorkspaceContext only", async function () {
    const c = setup(() => Promise.resolve({ projects: [{ project_id: "p1", name: "催化项目 Alpha" }, { project_id: "p2", name: "溶剂筛选 Beta" }] }));
    await flush();
    assertEq(c.nodes["acp-project-name"].textContent, "全部项目");
    c.nodes["acp-project-btn"].listeners.click[0]({ stopPropagation() {} });
    assertEq(c.nodes["acp-project-popover"].hidden, false);
    assertEq(c.nodes["acp-project-list"].children.length, 3, "all scope + two real projects");
    assertEq(c.nodes["acp-project-list"].children[2].children[0].children[0].textContent, "溶剂筛选 Beta");
    c.nodes["acp-project-search"].focus();
    c.nodes["acp-project-popover"].listeners.keydown[0]({ key: "Escape", preventDefault() {} });
    assertEq(c.nodes["acp-project-popover"].hidden, true);
    assert(c.nodes["acp-project-btn"].focused, "Escape restores focus to the project selector");
    c.nodes["acp-project-btn"].listeners.click[0]({ stopPropagation() {} });
    c.nodes["acp-project-list"].children[2].listeners.click[0]({});
    assertEq(c.context.getSnapshot().projectId, "p2");
    assertEq(c.nodes["acp-project-name"].textContent, "溶剂筛选 Beta");
    assertEq(c.nodes["acp-project-popover"].hidden, true);
    c.nodes["acp-project-btn"].listeners.click[0]({ stopPropagation() {} });
    c.nodes["acp-project-search"].value = "Alpha";
    c.nodes["acp-project-search"].listeners.input[0]({});
    assertEq(c.nodes["acp-project-list"].children.length, 1, "search filters on real names");
    assertEq(c.context.calls, [{ patch: { projectId: "p2" }, source: "project-switcher" }], "switching has one context write and no second project store");
  });
  await test("project selector reports permission error and never invents fallback projects", async function () {
    const c = setup(() => Promise.reject(Object.assign(new Error("forbidden"), { status: 403 })));
    await flush();
    c.nodes["acp-project-btn"].listeners.click[0]({ stopPropagation() {} });
    assert(c.nodes["acp-project-status"].textContent.includes("无权查看项目列表"));
    assertEq(c.nodes["acp-project-list"].children.length, 0);
    assertEq(c.nodes["acp-project-retry"].hidden, false);
  });
  await test("project selector has an explicit empty identity state", async function () {
    const c = setup(() => Promise.resolve({ projects: [] }));
    await flush();
    c.nodes["acp-project-btn"].listeners.click[0]({ stopPropagation() {} });
    assert(c.nodes["acp-project-status"].textContent.includes("当前身份没有可用项目"));
    assertEq(c.nodes["acp-project-list"].children.length, 1, "only the all-accessible scope; no fabricated project");
    assertEq(c.nodes["acp-project-list"].children[0].getAttribute("data-project-id"), "");
  });
  await test("malformed project data is a recoverable panel error", async function () {
    const c = setup(() => Promise.resolve({ items: [] }));
    await flush();
    c.nodes["acp-project-btn"].listeners.click[0]({ stopPropagation() {} });
    assert(c.nodes["acp-project-status"].textContent.includes("projects[]"));
    assertEq(c.nodes["acp-project-retry"].hidden, false);
    assertEq(c.nodes["acp-project-list"].children.length, 0);
  });
};
