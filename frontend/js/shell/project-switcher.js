/* Global project scope. Selected project is WorkspaceContext.projectId; this module only owns the fetched option list.
 * Contract: existing GET /api/v1/projects -> { projects: [{ project_id, name, ... }] }, already
 * filtered by the verified server identity. There is no polling and no second project state. */
window.ACPShell = window.ACPShell || {};
ACPShell.projectSwitcher = (function () {
  "use strict";
  var initialized = false, context = null, request = null, i18n = null;
  var button = null, popover = null, nameEl = null, search = null, statusEl = null, listEl = null, retry = null, refreshBtn = null;
  var projects = [], state = "idle", error = null, loading = null, opener = null, filter = "";
  function doc() { return ACPShell.env ? ACPShell.env.doc() : document; }
  function t(key) { try { return i18n ? i18n.t(key) : key; } catch (e) { return key; } }
  function tx(key, fallback, vars) { var v = t(key); if (!v || v === key) v = fallback; if (vars) Object.keys(vars).forEach(function (k) { v = v.split("{" + k + "}").join(String(vars[k])); }); return v; }
  function currentId() { try { var s = context.getSnapshot(); return s && s.projectId ? String(s.projectId) : ""; } catch (e) { return ""; } }
  function currentLabel() {
    var id = currentId();
    if (!id) return tx("shell.project.all", "全部项目");
    if (state === "loading") return id;
    for (var i = 0; i < projects.length; i += 1) if (String(projects[i].project_id) === id) return String(projects[i].name || projects[i].project_id);
    return id + " （不可见）";
  }
  function syncButton() {
    if (!nameEl) return;
    nameEl.textContent = currentLabel();
    if (button) {
      button.title = currentLabel();
      button.setAttribute("aria-label", "当前项目：" + currentLabel());
    }
  }
  function el(tag, cls, text) { var n = doc().createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function message(kind, text) {
    if (!statusEl) return;
    statusEl.hidden = !text;
    statusEl.className = "acp-project-status is-" + kind;
    statusEl.textContent = text || "";
  }
  function option(id, label, description) {
    var b = el("button", "acp-project-option", "");
    b.type = "button"; b.setAttribute("role", "option"); b.setAttribute("data-project-id", id || "");
    var text = el("span", "acp-project-option-copy");
    text.appendChild(el("strong", "", label));
    if (description) text.appendChild(el("small", "", description));
    b.appendChild(text);
    var on = (id || "") === currentId();
    b.setAttribute("aria-selected", on ? "true" : "false");
    if (on) b.setAttribute("aria-current", "true");
    b.addEventListener("click", function () { select(id); });
    b.addEventListener("keydown", function (e) {
      var options = Array.prototype.slice.call(listEl.querySelectorAll("[role='option']")), index = options.indexOf(b), next = null;
      if (e.key === "ArrowDown") next = (index + 1) % options.length;
      else if (e.key === "ArrowUp") next = (index - 1 + options.length) % options.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = options.length - 1;
      else if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (next == null) return;
      e.preventDefault(); options[next].focus();
    });
    return b;
  }
  function renderList() {
    if (!listEl) return;
    listEl.replaceChildren();
    var q = filter.trim().toLowerCase();
    var allLabel = tx("shell.project.all", "全部项目");
    var allVisible = !q || allLabel.toLowerCase().indexOf(q) !== -1;
    if (allVisible) listEl.appendChild(option("", allLabel, tx("shell.project.visible_all", "当前身份可见的全部项目")));
    projects.forEach(function (project) {
      var id = String(project.project_id || "");
      var name = String(project.name || id);
      if (!id || (q && name.toLowerCase().indexOf(q) === -1 && id.toLowerCase().indexOf(q) === -1)) return;
      listEl.appendChild(option(id, name, String(project.project_id || "")));
    });
    if (!listEl.children.length) {
      message("empty", q ? "没有匹配的项目" : tx("shell.project.empty", "当前身份没有可用项目。"));
    } else if (!projects.length) {
      message("empty", tx("shell.project.empty", "当前身份没有可用项目。"));
    } else if (state === "ready") message("", "");
  }
  function refresh() {
    if (!request) { state = "error"; error = new Error("项目接口不可用"); render(); return Promise.resolve([]); }
    if (loading) return loading;
    state = "loading"; error = null; message("loading", tx("shell.project.loading", "正在读取项目…"));
    if (retry) retry.hidden = true;
    loading = Promise.resolve(request("/projects", { method: "GET" })).then(function (body) {
      if (!body || !Array.isArray(body.projects)) throw new Error("项目列表响应缺少 projects[]");
      projects = body.projects.filter(function (p) { return p && p.project_id != null && String(p.project_id); });
      state = "ready"; loading = null; render(); return projects;
    }).catch(function (e) {
      loading = null; state = "error"; error = e || new Error("项目列表读取失败"); render(); return projects;
    });
    return loading;
  }
  function render() {
    syncButton();
    if (refreshBtn) refreshBtn.disabled = state === "loading";
    if (!listEl) return;
    if (state === "loading") { listEl.replaceChildren(); message("loading", tx("shell.project.loading", "正在读取项目…")); if (retry) retry.hidden = true; return; }
    if (state === "error") {
      listEl.replaceChildren();
      var code = error && error.status;
      var text = code === 401 || code === 403 ? tx("shell.project.forbidden", "无权查看项目列表。") : tx("shell.project.load_failed", "项目列表加载失败：{message}", { message: (error && error.message) || "未知错误" });
      message("error", text);
      if (retry) retry.hidden = false;
      return;
    }
    if (state === "ready") { if (!projects.length) message("empty", tx("shell.project.empty", "当前身份没有可用项目。")); else message("", ""); renderList(); }
  }
  function select(id) {
    var next = id ? String(id) : null;
    if (next !== (currentId() || null)) {
      context.setFocus({ projectId: next }, "project-switcher");
    }
    close(true);
  }
  function open(trigger) {
    if (!popover || !button) return;
    setOpen(true); opener = trigger || button;
    if (filter) { filter = ""; if (search) search.value = ""; }
    render();
    if (state === "idle") refresh();
    if (search && search.focus) search.focus();
  }
  function setOpen(open) {
    if (!popover || !button) return;
    popover.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }
  function close(restore) {
    if (!popover || popover.hidden) return;
    setOpen(false);
    if (restore !== false && opener && opener.focus) opener.focus();
    opener = null;
  }
  function onContext() { syncButton(); if (!popover || popover.hidden) return; renderList(); }
  function init(options) {
    if (initialized) return; initialized = true;
    options = options || {}; context = options.context; request = options.request; i18n = options.i18n || ACPShell.i18n;
    button = doc().getElementById("acp-project-btn"); popover = doc().getElementById("acp-project-popover");
    nameEl = doc().getElementById("acp-project-name"); search = doc().getElementById("acp-project-search");
    statusEl = doc().getElementById("acp-project-status"); listEl = doc().getElementById("acp-project-list"); retry = doc().getElementById("acp-project-retry"); refreshBtn = doc().getElementById("acp-project-refresh");
    if (!button || !popover || !listEl || !context) return;
    button.addEventListener("click", function (e) {
      e.stopPropagation();
      if (!popover.hidden) close(); else {
        if (ACPShell.topbar && ACPShell.topbar.closeMenus) ACPShell.topbar.closeMenus(false);
        open(button);
      }
    });
    if (search) search.addEventListener("input", function () { filter = String(search.value || ""); renderList(); });
    if (search) search.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { var first = listEl.querySelector("[role='option']"); if (first) { e.preventDefault(); first.focus(); } }
    });
    if (retry) retry.addEventListener("click", function () { refresh(); });
    if (refreshBtn) refreshBtn.addEventListener("click", function () { refresh(); });
    popover.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key !== "Tab") return;
      var items = Array.prototype.slice.call(popover.querySelectorAll("button:not([disabled]):not([hidden]), input:not([disabled]), [tabindex='0']"));
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1], active = doc().activeElement;
      if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    });
    doc().addEventListener("click", function (e) {
      var target = e.target, cur = target, inside = false;
      while (cur) { if (cur === popover || cur === button) { inside = true; break; } cur = cur.parentNode; }
      if (!inside) close(false);
    });
    syncButton();
    try { context.subscribe(onContext); } catch (e) { /* host context is mandatory; callback unavailable is non-fatal */ }
    doc().addEventListener("acp:langchange", function () { syncButton(); render(); });
    refresh();
  }
  return { init: init, refresh: refresh, open: open, close: close, _projects: function () { return projects.slice(); }, _state: function () { return state; } };
})();
