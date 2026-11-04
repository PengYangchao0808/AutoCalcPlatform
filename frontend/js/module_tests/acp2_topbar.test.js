"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  const fs = require("fs"), path = require("path");
  const root = path.resolve(__dirname, "..", "..");
  function node(tag, attrs) {
    const n = { tagName: String(tag || "div").toUpperCase(), children: [], attrs: Object.assign({}, attrs || {}), parentNode: null, hidden: false, className: "", textContent: "", listeners: {},
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
      appendChild(c) { c.parentNode = this; this.children.push(c); return c; }, replaceChildren(...cs) { this.children = []; cs.forEach(c => this.appendChild(c)); },
      addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
      focus() { this.focused = true; },
      querySelectorAll(sel) {
        const out = []; (function walk(x) { x.children.forEach(c => { if (matches(c, sel)) out.push(c); walk(c); }); })(this); return out;
      },
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
      click() { (this.listeners.click || []).forEach(fn => fn({ target: this, stopPropagation() {}, preventDefault() {} })); },
    };
    return n;
  }
  function matches(n, sel) {
    if (sel.indexOf("[role='menuitem']") !== -1 || sel.indexOf("[role='menuitemradio']") !== -1) {
      return n.getAttribute("role") === "menuitem" || n.getAttribute("role") === "menuitemradio";
    }
    if (sel.indexOf("[data-workspace-id]") !== -1) return n.getAttribute("data-workspace-id") != null;
    if (sel.indexOf("[aria-current='page']") !== -1) return n.getAttribute("aria-current") === "page";
    if (sel.indexOf("button:not([disabled])") !== -1) return n.tagName === "BUTTON" && n.getAttribute("disabled") == null;
    return false;
  }
  await test("topbar has one project selector, one current-view menu and one settings menu; no letter tabs", function () {
    const html = fs.readFileSync(path.join(root, "next/index.html"), "utf8");
    const header = /<header class="acp-shell-topbar">([\s\S]*?)<\/header>/.exec(html)[1];
    assertEq((header.match(/id="acp-project-btn"/g) || []).length, 1);
    assertEq((header.match(/id="acp-view-menu-btn"/g) || []).length, 1);
    assertEq((header.match(/id="acp-settings-btn"/g) || []).length, 1);
    assert(header.indexOf("acp-project-btn") < header.indexOf("acp-view-menu-btn"));
    assert(header.indexOf("acp-view-menu-btn") < header.indexOf("acp-settings-btn"));
    assert(!header.includes("acp-shell-tab") && !header.includes("acp-next-view-letter"), "no old letter tab controls");
    assert(!header.includes("acp-shell-brand-copy") && !header.includes("shell.app.badge"), "no long brand title block");
    assert(header.includes('id="acp-dsh-session-slot"'), "DSH session slot lives in the single topbar");
    assert(header.indexOf('id="acp-dsh-session-slot"') > header.indexOf('id="acp-view-menu-btn"'));
    assert(header.includes('id="acp-conn-status"') && header.includes('id="acp-conn-pill"'), "connection indicator is independent");
    assert(header.indexOf('id="acp-conn-status"') < header.indexOf('id="acp-settings-btn"'));
    assert(header.includes('id="acp-theme-toggle"') && /id="acp-theme-toggle"[^>]*disabled/.test(header), "theme control is explicitly disabled");
    ["acp-approvals-btn", "acp-system-btn", "acp-layout-edit", "acp-lang-toggle", "acp-conn-menu-dot"].forEach(function (id) {
      assert(header.includes('id="' + id + '"'), id + " lives in the settings popover");
      assert(header.indexOf('id="' + id + '"') > header.indexOf('id="acp-settings-btn"'), id + " is not flat in the topbar");
    });
    assertEq((header.match(/acp-shell-link-classic/g) || []).length, 1, "classic workbench not duplicated");
    const projectName = /id="acp-project-name"[^>]*>([^<]+)/.exec(header);
    assertEq(projectName[1], "全部项目", "no fabricated current project before the real API answers");
    assert(!/class="acp-topbar-kicker"/.test(header) && !/acp-project-copy/.test(header), "project/view are single-line ghosts");
    const menu = /id="acp-view-menu"[\s\S]*?<\/div>/.exec(header);
    assert(menu, "view menu exists");
    assertEq((menu[0].match(/role="menuitemradio"/g) || []).length, 6);
    assertEq((menu[0].match(/aria-keyshortcuts="Alt\+[1-6]"/g) || []).length, 6);
    assertEq(new Set((menu[0].match(/data-icon="([^"]+)"/g) || []).map(x => x.split('"')[1])).size, 6, "six distinct view icons");
    assertEq(new Set((menu[0].match(/<small[^>]*>[^<]+/g) || []).map(x => x.replace(/<[^>]+>/g, ""))).size, 6, "six descriptions");
    assert(!html.includes(">A<") && !html.includes(">D<") && !html.includes(">G<") && !html.includes("acp-next-view-letter"), "no development letters in shell chrome");
    const css = fs.readFileSync(path.join(root, "css/shell/next-layout.css"), "utf8");
    assert(/--shell-topbar-h:\s*48px/.test(css), "topbar is 48px");
    assert(/\.acp-shell-topbar\s*\{[^}]*border-bottom:\s*1px solid #1f242c/.test(css), "topbar has one bottom hairline");
    const controlRule = /\.acp-project-btn, \.acp-view-btn, \.acp-settings-btn\s*\{([^}]*)\}/.exec(css)[1];
    ["height: 32px", "padding: 0 10px", "gap: 6px", "border-radius: 5px", "font-size: 13px", "line-height: 18px", "font-weight: 500", "background: transparent"].forEach(function (part) {
      assert(controlRule.indexOf(part) !== -1, "project/view/settings share " + part);
    });
    assert(header.includes('<span data-i18n="shell.settings.title">设置</span>'), "settings shows icon + visible text");
    assert(!/\.acp-settings-btn\s*\{[^}]*width:\s*28px/.test(css), "settings is no longer a 28px icon-only button");
    assert(/\.acp-settings-btn\s*>\s*span\s*\{[^}]*color:\s*#e6edf3/.test(css), "settings label uses the primary text color");
    assert(/\.acp-theme-btn\s*\{[^}]*width:\s*28px[^}]*height:\s*28px/.test(css), "theme remains an independent 28px icon button");
    assert(/--next-task-w:\s*320px/.test(css), "task dock default is 320px");
    assert(/\.acp-next-task-sash\s*\{[^}]*width:\s*6px[^}]*cursor:\s*col-resize/.test(css), "task sash is a 6px resize target");
    assert(/data-workspace="files"[\s\S]*?\.acp-next-task-rail[\s\S]*?display:\s*none/.test(css), "D is hidden in files mode");
    assert(/@container \(max-width: 640px\)[\s\S]*?\.acp-view-btn strong#acp-current-view-name\s*\{\s*display:\s*none[\s\S]*?\.acp-current-view-short\s*\{\s*display:\s*block/.test(css), "narrow view uses its short label");
  });
  await test("current view menu updates icon, full and short labels, aria-current and Escape focus", async function () {
    const ids = ["acp-view-menu-btn", "acp-view-menu", "acp-settings-btn", "acp-settings-menu", "acp-current-view-name", "acp-current-view-short", "acp-current-view-icon", "acp-shell"];
    const nodes = {}; ids.forEach(id => nodes[id] = node("button")); nodes["acp-settings-menu"].hidden = nodes["acp-view-menu"].hidden = true;
    const items = {};
    ["research", "drawing", "molecular", "config", "execution", "files"].forEach(function (id, i) {
      const b = node("button", { role: "menuitemradio", "data-workspace-id": id }); b.tabIndex = i === 0 ? 0 : -1; items[id] = b; nodes["acp-view-menu"].appendChild(b);
    });
    const doc = { getElementById: id => nodes[id] || null, createElement: tag => node(tag), addEventListener(t, fn) { this[t] = fn; }, activeElement: null, body: node("body") };
    const s = makeSandbox({ document: doc });
    s.ACPShell = { i18n: { t: k => ({ "shell.workspace.research": "化学网络", "shell.workspace.drawing": "结构导入", "shell.workspace.molecular": "三维结构", "shell.workspace.config": "任务配置", "shell.workspace.execution": "状态与结果", "shell.workspace.files": "文件", "shell.workspace.short.network": "网络", "shell.workspace.short.drawing": "导入", "shell.workspace.short.molecular": "3D", "shell.workspace.short.config": "配置", "shell.workspace.short.execution": "结果", "shell.workspace.short.files": "文件" }[k] || k) } };
    loadScript("js/shell/topbar.js", s);
    s.ACPShell.topbar.init({ layout: { getCurrentWorkspace: () => "research" }, i18n: s.ACPShell.i18n });
    assertEq(nodes["acp-view-menu-btn"].getAttribute("aria-expanded"), "false");
    nodes["acp-view-menu-btn"].click();
    assertEq(nodes["acp-view-menu-btn"].getAttribute("aria-expanded"), "true");
    assertEq(nodes["acp-view-menu"].hidden, false);
    s.ACPShell.topbar.syncView("drawing");
    assertEq(nodes["acp-current-view-name"].textContent, "结构导入");
    assertEq(nodes["acp-current-view-short"].textContent, "导入");
    assertEq(items.drawing.getAttribute("aria-current"), "page");
    assertEq(items.research.getAttribute("aria-checked"), "false");
    assertEq(nodes["acp-view-menu-btn"].getAttribute("aria-label"), "当前视图：结构导入");
    nodes["acp-view-menu"].listeners.click[0]({ target: items.config, stopPropagation() {}, preventDefault() {} });
    assertEq(nodes["acp-view-menu"].hidden, true);
    assertEq(nodes["acp-settings-btn"].getAttribute("aria-expanded"), "false");
    assertEq(nodes["acp-current-view-name"].textContent, "任务配置");
    s.ACPShell.topbar.syncView("files");
    assertEq(nodes["acp-current-view-name"].textContent, "文件");
    assertEq(nodes["acp-current-view-short"].textContent, "文件");
    assertEq(items.files.getAttribute("aria-current"), "page");
    doc.keydown({ key: "Escape", preventDefault() {} });
  });
  await test("settings menu keeps platform/layout/language entries, traps focus and Escape restores trigger", function () {
    const ids = ["acp-view-menu-btn", "acp-view-menu", "acp-settings-btn", "acp-settings-menu"];
    const nodes = {}; ids.forEach(id => nodes[id] = node("button")); nodes["acp-view-menu"].hidden = nodes["acp-settings-menu"].hidden = true;
    const settings = [];
    ["approval", "system", "layout", "language", "classic"].forEach(id => { const b = node("button", { role: id === "classic" ? "menuitem" : "menuitem", id: id }); settings.push(b); nodes["acp-settings-menu"].appendChild(b); });
    const doc = { getElementById: id => nodes[id] || null, createElement: tag => node(tag), addEventListener(t, fn) { this[t] = fn; }, activeElement: null, body: node("body") };
    const s = makeSandbox({ document: doc }); s.ACPShell = { i18n: { t: k => k } };
    loadScript("js/shell/topbar.js", s);
    s.ACPShell.topbar.init({ layout: { getCurrentWorkspace: () => "research" }, i18n: s.ACPShell.i18n });
    nodes["acp-settings-btn"].click();
    assertEq(nodes["acp-settings-btn"].getAttribute("aria-expanded"), "true");
    assertEq(nodes["acp-settings-menu"].hidden, false);
    const focusFirst = { key: "ArrowDown", preventDefault() {} };
    nodes["acp-settings-menu"].listeners.keydown[0](focusFirst);
    assert(settings[0].focused, "ArrowDown focuses the first settings entry");
    const esc = { key: "Escape", preventDefault() {} };
    nodes["acp-settings-menu"].listeners.keydown[0](esc);
    assertEq(nodes["acp-settings-menu"].hidden, true);
    assert(nodes["acp-settings-btn"].focused, "Escape restores focus to Settings");
  });
};
