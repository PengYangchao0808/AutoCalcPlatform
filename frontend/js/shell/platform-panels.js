/* Two platform entry points only: system status and research approvals; plus A capability plan. */
window.ACPShell = window.ACPShell || {};
ACPShell.platformPanels = (function () {
  "use strict";
  var TYPES = {
    system: { title: "系统状态", moduleId: "node-status" },
    approvals: { title: "待审批研究", moduleId: "approval-card" },
    capability: { title: "科学能力计划", moduleId: "capability-form" }
  };
  var initialized = false, openType = null, instance = null, opener = null, panel = null, title = null, host = null;
  function doc() { return ACPShell.env ? ACPShell.env.doc() : document; }
  function api(h) { return { setState: function (s) { h.setAttribute("data-acp-module-state", s || "ready"); }, reportError: function (e) { h.setAttribute("data-acp-module-state", "error"); var p = doc().createElement("p"); p.className = "acp-next-error"; p.setAttribute("role", "alert"); p.textContent = (e && e.message) || String(e); h.replaceChildren(p); }, el: h, bodyEl: h, statusEl: null }; }
  function deps(h, type) {
    var base = { context: ACPShell.context, commands: ACPShell.commands, i18n: ACPShell.i18n, client: window.ACPClient || null, panel: api(h) };
    if (type === "capability") {
      base.science = window.ACPClient && window.ACPClient.science ? window.ACPClient.science : null;
      base.commands = window.ACPClient && window.ACPClient.researchCommands ? window.ACPClient.researchCommands : null;
    }
    return base;
  }
  function dispose() { if (instance && instance.dispose) { try { instance.dispose(); } catch (e) {} } instance = null; if (host) host.replaceChildren(); }
  function visible(node) {
    var cur = node;
    while (cur && cur !== doc().body) {
      if (cur.hidden) return false;
      cur = cur.parentNode;
    }
    return !!(node && node.focus);
  }
  function close() {
    if (!openType) return;
    dispose(); panel.hidden = true; openType = null; doc().getElementById("acp-shell-main").removeAttribute("inert");
    var buttons = ["acp-approvals-btn", "acp-system-btn", "acp-capability-btn"];
    buttons.forEach(function (id) { var b = doc().getElementById(id); if (b) b.setAttribute("aria-expanded", "false"); });
    var target = visible(opener) ? opener : doc().getElementById("acp-settings-btn");
    opener = null; if (target && target.focus) target.focus();
  }
  function open(type, trigger) {
    if (!TYPES[type]) return;
    if (openType) close();
    if (ACPShell.topbar && ACPShell.topbar.closeMenus) ACPShell.topbar.closeMenus(false);
    var entry = ACPShell.registry && ACPShell.registry.resolve ? ACPShell.registry.resolve(TYPES[type].moduleId) : null;
    opener = trigger || null; openType = type; panel.hidden = false; title.textContent = TYPES[type].title;
    doc().getElementById("acp-shell-main").setAttribute("inert", "");
    var b = doc().getElementById(type === "system" ? "acp-system-btn" : type === "approvals" ? "acp-approvals-btn" : "acp-capability-btn"); if (b) b.setAttribute("aria-expanded", "true");
    var h = doc().createElement("div"); h.className = "acp-platform-module"; h.setAttribute("data-acp-platform-module", type); host.replaceChildren(h);
    if (!entry || entry.ok === false || typeof entry.factory !== "function") { api(h).reportError(new Error("平台模块不可用：" + TYPES[type].moduleId)); return; }
    try { instance = entry.factory(deps(h, type)); Promise.resolve(instance.mount(h, ACPShell.context.getSnapshot())).then(function () { if (instance && instance.updateContext) instance.updateContext(ACPShell.context.getSnapshot()); }).catch(function (e) { api(h).reportError(e); }); } catch (e) { api(h).reportError(e); }
    var closeBtn = doc().getElementById("acp-platform-close"); if (closeBtn) closeBtn.focus();
  }
  function init() {
    if (initialized) return; initialized = true;
    panel = doc().getElementById("acp-platform-panel"); title = doc().getElementById("acp-platform-title"); host = doc().getElementById("acp-platform-host");
    if (!panel || !title || !host) return;
    [["approvals", "acp-approvals-btn"], ["system", "acp-system-btn"], ["capability", "acp-capability-btn"]].forEach(function (pair) {
      var b = doc().getElementById(pair[1]); if (b) b.addEventListener("click", function () { open(pair[0], b); });
    });
    var closeBtn = doc().getElementById("acp-platform-close"); if (closeBtn) closeBtn.addEventListener("click", close);
    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key !== "Tab" || !panel.querySelectorAll) return;
      var items = Array.prototype.slice.call(panel.querySelectorAll("button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']"));
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1], active = doc().activeElement;
      if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    });
  }
  return { init: init, open: open, close: close, _types: TYPES };
})();
