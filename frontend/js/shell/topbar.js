/* Topbar: one current-view menu and one settings menu; no duplicated module instances. */
window.ACPShell = window.ACPShell || {};
ACPShell.topbar = (function () {
  "use strict";
  var VIEWS = {
    research: { icon: "acp-i-view-network", key: "shell.workspace.research", shortKey: "shell.workspace.short.network", short: "网络" },
    drawing: { icon: "acp-i-view-import", key: "shell.workspace.drawing", shortKey: "shell.workspace.short.drawing", short: "导入" },
    molecular: { icon: "acp-i-view-structure", key: "shell.workspace.molecular", shortKey: "shell.workspace.short.molecular", short: "3D" },
    config: { icon: "acp-i-view-config", key: "shell.workspace.config", shortKey: "shell.workspace.short.config", short: "配置" },
    execution: { icon: "acp-i-view-results", key: "shell.workspace.execution", shortKey: "shell.workspace.short.execution", short: "结果" },
    files: { icon: "acp-i-view-files", key: "shell.workspace.files", shortKey: "shell.workspace.short.files", short: "文件" }
  };
  var initialized = false, i18n = null, viewButton = null, viewMenu = null, settingsButton = null, settingsMenu = null, currentView = "research";
  function doc() { return ACPShell.env ? ACPShell.env.doc() : document; }
  function t(key) { try { return i18n ? i18n.t(key) : key; } catch (e) { return key; } }
  function setOpen(menu, button, open) {
    if (!menu || !button) return;
    menu.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }
  function isOpen(menu) { return !!(menu && !menu.hidden); }
  function inside(node, root) {
    var cur = node;
    while (cur) { if (cur === root) return true; cur = cur.parentNode; }
    return false;
  }
  function focusables(root) {
    if (!root || !root.querySelectorAll) return [];
    return Array.prototype.slice.call(root.querySelectorAll("button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']"));
  }
  function closeMenus(restore) {
    var viewWas = isOpen(viewMenu), settingsWas = isOpen(settingsMenu);
    setOpen(viewMenu, viewButton, false);
    setOpen(settingsMenu, settingsButton, false);
    if (restore && (viewWas || settingsWas)) {
      var target = viewWas ? viewButton : settingsButton;
      if (target && target.focus) target.focus();
    }
  }
  function toggle(menu, button, otherMenu, otherButton) {
    var open = !isOpen(menu);
    setOpen(otherMenu, otherButton, false);
    setOpen(menu, button, open);
    if (open) {
      if (ACPShell.projectSwitcher && ACPShell.projectSwitcher.close) ACPShell.projectSwitcher.close(false);
      var list = focusables(menu);
      var selected = menu.querySelector && menu.querySelector("[aria-current='page']");
      if (selected && selected.focus) selected.focus();
      else if (list[0] && list[0].focus) list[0].focus();
    }
  }
  function menuKeys(event, menu, button, deferArrows) {
    if (!event) return;
    if (event.key === "Escape") { event.preventDefault(); setOpen(menu, button, false); if (button.focus) button.focus(); return; }
    if (deferArrows && event.key !== "Tab") return;
    var items = Array.prototype.slice.call(menu.querySelectorAll("[role='menuitem'], [role='menuitemradio']"));
    if (!items.length) return;
    var active = doc().activeElement, index = items.indexOf(active);
    var next = null;
    if (event.key === "ArrowDown") next = index < 0 ? 0 : (index + 1) % items.length;
    else if (event.key === "ArrowUp") next = index <= 0 ? items.length - 1 : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else if (event.key === "Tab") {
      // Menu is a popover: keep keyboard focus inside until Escape or selection.
      var focus = focusables(menu), first = focus[0], last = focus[focus.length - 1];
      if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
      return;
    }
    if (next == null) return;
    event.preventDefault();
    items[next].focus();
  }
  function syncView(wsId) {
    var view = VIEWS[wsId] || VIEWS.research;
    currentView = wsId in VIEWS ? wsId : "research";
    var name = t(view.key), short = t(view.shortKey) || view.short;
    var nameEl = doc().getElementById("acp-current-view-name"), shortEl = doc().getElementById("acp-current-view-short"), icon = doc().getElementById("acp-current-view-icon");
    if (nameEl) nameEl.textContent = name;
    if (shortEl) shortEl.textContent = short;
    if (icon && icon.setAttribute) icon.setAttribute("href", "#" + view.icon);
    if (viewButton) viewButton.setAttribute("aria-label", "当前视图：" + name);
    if (viewMenu && viewMenu.querySelectorAll) {
      Array.prototype.forEach.call(viewMenu.querySelectorAll("[data-workspace-id]"), function (item) {
        var on = item.getAttribute("data-workspace-id") === currentView;
        item.className = "acp-view-item" + (on ? " is-active" : "");
        item.setAttribute("aria-checked", on ? "true" : "false");
        if (on) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
        item.tabIndex = on ? 0 : -1;
      });
    }
    return { id: currentView, name: name, short: short };
  }
  function init(options) {
    if (initialized) return; initialized = true;
    options = options || {}; i18n = options.i18n || ACPShell.i18n;
    viewButton = doc().getElementById("acp-view-menu-btn");
    viewMenu = doc().getElementById("acp-view-menu");
    settingsButton = doc().getElementById("acp-settings-btn");
    settingsMenu = doc().getElementById("acp-settings-menu");
    if (!viewButton || !viewMenu || !settingsButton || !settingsMenu) return;
    setOpen(viewMenu, viewButton, false);
    setOpen(settingsMenu, settingsButton, false);
    viewButton.addEventListener("click", function (e) { e.stopPropagation(); toggle(viewMenu, viewButton, settingsMenu, settingsButton); });
    settingsButton.addEventListener("click", function (e) { e.stopPropagation(); toggle(settingsMenu, settingsButton, viewMenu, viewButton); });
    viewMenu.addEventListener("keydown", function (e) { menuKeys(e, viewMenu, viewButton, true); });
    settingsMenu.addEventListener("keydown", function (e) { menuKeys(e, settingsMenu, settingsButton); });
    viewMenu.addEventListener("click", function (e) {
      var item = e.target;
      while (item && item !== viewMenu && !(item.getAttribute && item.getAttribute("data-workspace-id"))) item = item.parentNode;
      if (!item || item === viewMenu) return;
      syncView(item.getAttribute("data-workspace-id"));
      closeMenus(false);
      if (viewButton.focus) viewButton.focus();
    });
    // Approval/system open the real platform module and then close this popover.
    ["acp-approvals-btn", "acp-system-btn"].forEach(function (id) {
      var b = doc().getElementById(id);
      if (b) b.addEventListener("click", function () { closeMenus(false); });
    });
    doc().addEventListener("click", function (e) {
      var target = e.target;
      if (inside(target, viewMenu) || inside(target, viewButton) || inside(target, settingsMenu) || inside(target, settingsButton)) return;
      closeMenus(false);
    });
    doc().addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || (!isOpen(viewMenu) && !isOpen(settingsMenu))) return;
      e.preventDefault(); closeMenus(true);
    });
    doc().addEventListener("acp:langchange", onLanguageChange);
    syncView(options.layout && options.layout.getCurrentWorkspace ? options.layout.getCurrentWorkspace() : currentView);
  }
  function onLanguageChange() { syncView(currentView); }
  return { init: init, syncView: syncView, closeMenus: closeMenus, isOpen: function () { return isOpen(viewMenu) || isOpen(settingsMenu); }, _views: VIEWS };
})();
