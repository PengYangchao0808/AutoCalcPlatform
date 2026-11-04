"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  function el(attrs) {
    return {
      attrs: Object.assign({}, attrs || {}), textContent: "", title: "", listeners: {},
      setAttribute(k, v) { this.attrs[k] = String(v); },
      getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; },
      addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
      click() { (this.listeners.click || []).forEach(fn => fn({})); },
    };
  }
  function setup(request) {
    const dots = [el(), el()];
    const button = el();
    const label = el();
    const menuText = el();
    const taskRefresh = el();
    const refresh = [];
    const doc = {
      getElementById(id) {
        if (id === "acp-conn-pill") return dots[0];
        if (id === "acp-conn-menu-dot") return dots[1];
        if (id === "acp-conn-status") return button;
        if (id === "acp-conn-label") return label;
        if (id === "acp-conn-text") return menuText;
        return null;
      },
      querySelector(sel) { return sel.indexOf("acp-mod-tasks-refresh") !== -1 ? taskRefresh : null; },
      addEventListener() {},
    };
    const s = makeSandbox({ document: doc });
    s.ACPShell = {
      env: { doc: () => doc },
      i18n: { t: k => k },
      layout: { reloadCurrentWorkspace() { refresh.push("layout"); return true; } },
    };
    loadScript("js/shell/connection.js", s);
    s.ACPShell.connection.init({ request, probe: false });
    taskRefresh.click = () => refresh.push("tasks");
    return { s, dots, button, label, menuText, taskRefresh, refresh };
  }
  const flush = () => Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve());
  await test("connection lamp uses real success, 503 and other-error evidence", async function () {
    let mode = "ok";
    const c = setup(path => {
      assertEq(path, "/status");
      if (mode === "ok") return Promise.resolve({ status: "ok" });
      const err = new Error("unavailable"); err.status = mode === "503" ? 503 : 500; return Promise.reject(err);
    });
    assertEq(c.s.ACPShell.connection.state(), "unknown");
    assertEq(c.dots[0].getAttribute("data-conn"), "unknown");
    await c.s.ACPShell.connection.retry();
    assertEq(c.s.ACPShell.connection.state(), "online");
    assertEq(c.dots[0].getAttribute("data-conn"), "online");
    assertEq(c.label.textContent, "已连接");
    assertEq(c.menuText.textContent, "已连接");
    assert(c.button.getAttribute("aria-label").indexOf("已连接") !== -1);
    c.s.ACPShell.connection.noteFailure({ status: 503 });
    assertEq(c.s.ACPShell.connection.state(), "offline");
    assertEq(c.dots[1].getAttribute("data-conn"), "offline");
    assertEq(c.label.textContent, "异常");
    c.s.ACPShell.connection.noteFailure({ status: 500 });
    assertEq(c.s.ACPShell.connection.state(), "offline", "other HTTP errors do not clear a known outage");
    c.s.ACPShell.connection.noteSuccess();
    assertEq(c.s.ACPShell.connection.state(), "online", "a real successful response recovers the lamp");
  });
  await test("connection retry is explicit, refreshes current data, and never fabricates success", async function () {
    const c = setup(() => { const e = new Error("down"); e.status = 503; return Promise.reject(e); });
    await flush();
    c.button.click();
    await flush();
    assertEq(c.s.ACPShell.connection.state(), "offline");
    assertEq(c.refresh, ["layout", "tasks"], "retry asks the current view and task list to refresh through their existing clients");
    assertEq(c.dots[0].getAttribute("data-conn"), "offline");
  });
};
