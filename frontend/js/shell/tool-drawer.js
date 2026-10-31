/*
 * ACP Next Shell — tool drawer (V7 assembly).
 *
 * A default entry point for registered modules that are not pinned to one of
 * the three core recipes. It mounts a single module at a time into a transient
 * host using the same deps shape as the layout host, then disposes it on close
 * or switch. No module is duplicated and no second data cache is created.
 */
(function () {
  "use strict";

  var MODULES = [
    { id: "task-wizard", titleKey: "shell.module.task_wizard" },
    { id: "job-edit", titleKey: "shell.module.job_edit" },
    { id: "nmr-report", titleKey: "shell.module.nmr_report" },
    { id: "tsmode-editor", titleKey: "shell.module.tsmode_editor" },
    { id: "s2-scan", titleKey: "shell.module.s2_scan" },
    { id: "node-status", titleKey: "shell.module.node_status" },
    { id: "approval-card", titleKey: "shell.module.approval_card" },
    { id: "capability-form", titleKey: "shell.module.capability_form" },
    { id: "path-view", titleKey: "shell.module.path_view" },
    { id: "pes-handoff", titleKey: "shell.module.pes_handoff" },
    { id: "mechanism-plan", titleKey: "shell.module.mechanism_plan" },
    { id: "research-record", titleKey: "shell.module.research_record" }
  ];

  var state = {
    initialized: false,
    open: false,
    activeId: null,
    instance: null,
    opener: null,
    unsubscribe: null
  };
  var els = {};

  function t(key) {
    try { return window.ACPShell.i18n.t(key); } catch (e) { return key; }
  }

  function el(tag, cls, text) {
    var node = window.document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function snapshot() {
    try {
      return window.ACPShell.context.getSnapshot();
    } catch (e) {
      return { revision: 0, source: "tool-drawer" };
    }
  }

  function panelApi(host) {
    return {
      setState: function (next) {
        host.setAttribute("data-acp-module-state", next || "ready");
      },
      reportError: function (error) {
        host.setAttribute("data-acp-module-state", "error");
        var box = host.querySelector && host.querySelector(".acp-tools-error");
        if (box) box.textContent = (error && error.message) || String(error);
      },
      el: host,
      bodyEl: host,
      statusEl: null
    };
  }

  function deps(host) {
    return {
      context: window.ACPShell.context,
      commands: window.ACPShell.commands,
      i18n: window.ACPShell.i18n,
      client: window.ACPClient || null,
      panel: panelApi(host)
    };
  }

  function disposeInstance() {
    if (!state.instance) return;
    try {
      if (typeof state.instance.dispose === "function") state.instance.dispose();
    } catch (e) { /* module-local cleanup is best effort */ }
    state.instance = null;
  }

  function select(id) {
    var item = null;
    for (var i = 0; i < MODULES.length; i += 1) {
      if (MODULES[i].id === id) { item = MODULES[i]; break; }
    }
    if (!item) return;
    var entry = window.ACPShell.registry && window.ACPShell.registry.resolve
      ? window.ACPShell.registry.resolve(id) : null;
    if (!entry || entry.ok === false || typeof entry.factory !== "function") {
      showError("未注册模块：" + id);
      return;
    }
    disposeInstance();
    state.activeId = id;
    els.host.replaceChildren();
    var host = el("div", "acp-tools-module");
    host.setAttribute("data-acp-tools-module", id);
    host.setAttribute("data-acp-module-state", "loading");
    els.host.appendChild(host);
    els.title.textContent = t(item.titleKey);
    els.tabs.forEach(function (tab) {
      var active = tab.getAttribute("data-acp-tools-id") === id;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.tabIndex = active ? 0 : -1;
    });
    try {
      var instance = entry.factory(deps(host));
      state.instance = instance;
      Promise.resolve(instance.mount(host, snapshot())).then(function () {
        if (state.activeId !== id || !state.open) return;
        host.setAttribute("data-acp-module-state", host.getAttribute("data-acp-module-state") || "ready");
      }).catch(function (error) {
        showError((error && error.message) || String(error));
      });
    } catch (error) {
      showError((error && error.message) || String(error));
    }
  }

  function showError(message) {
    if (!els.host) return;
    els.host.replaceChildren();
    var box = el("p", "acp-tools-error", message || "模块加载失败");
    box.setAttribute("role", "alert");
    els.host.appendChild(box);
  }

  function open(opener) {
    if (!state.initialized) return;
    state.open = true;
    state.opener = opener || null;
    els.drawer.hidden = false;
    els.button.setAttribute("aria-expanded", "true");
    if (!state.activeId) select(MODULES[0].id);
    else if (els.host.childNodes.length === 0) select(state.activeId);
    if (els.firstTab) els.firstTab.focus();
  }

  function close() {
    state.open = false;
    disposeInstance();
    els.drawer.hidden = true;
    els.button.setAttribute("aria-expanded", "false");
    if (state.opener && typeof state.opener.focus === "function") {
      try { state.opener.focus(); } catch (e) { /* no-op */ }
    }
  }

  function build() {
    var doc = window.document;
    var button = doc.getElementById("acp-tools-btn");
    var drawer = doc.getElementById("acp-tools-drawer");
    if (!button || !drawer) return false;
    els.button = button;
    els.drawer = drawer;
    els.title = drawer.querySelector("[data-acp-tools-title]");
    els.host = drawer.querySelector("[data-acp-tools-host]");
    els.close = drawer.querySelector("[data-acp-tools-close]");
    var tablist = drawer.querySelector("[data-acp-tools-tabs]");
    els.tabs = [];
    MODULES.forEach(function (item) {
      if (!window.ACPShell.registry || !window.ACPShell.registry.has(item.id)) return;
      var tab = el("button", "acp-tools-tab", t(item.titleKey));
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("data-acp-tools-id", item.id);
      tab.setAttribute("aria-selected", "false");
      tab.tabIndex = -1;
      tab.addEventListener("click", function () { select(item.id); });
      tab.addEventListener("keydown", function (event) {
        var current = els.tabs.indexOf(tab);
        var next = null;
        if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (current + 1) % els.tabs.length;
        if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (current - 1 + els.tabs.length) % els.tabs.length;
        if (next == null) return;
        event.preventDefault();
        els.tabs[next].focus();
        select(els.tabs[next].getAttribute("data-acp-tools-id"));
      });
      tablist.appendChild(tab);
      els.tabs.push(tab);
    });
    if (!els.tabs.length) return false;
    els.firstTab = els.tabs[0];
    button.addEventListener("click", function () {
      if (state.open) close(); else open(button);
    });
    els.close.addEventListener("click", close);
    drawer.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });
    button.setAttribute("aria-controls", "acp-tools-drawer");
    button.setAttribute("aria-expanded", "false");
    return true;
  }

  function init() {
    if (state.initialized) return;
    if (!build()) return;
    state.initialized = true;
    try {
      state.unsubscribe = window.ACPShell.context.subscribe(function (snap) {
        if (state.instance && typeof state.instance.updateContext === "function") {
          state.instance.updateContext(snap);
        }
      });
    } catch (e) { /* context is optional for some modules */ }
    window.addEventListener("beforeunload", disposeInstance);
  }

  window.ACPShell = window.ACPShell || {};
  window.ACPShell.toolDrawer = { init: init, open: open, close: close };
})();
