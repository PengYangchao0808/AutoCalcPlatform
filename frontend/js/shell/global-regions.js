/* Persistent task region: one module instance, three dock modes, a draggable
 * separator and accessible collapse. The instance is never remounted when the
 * mode or width changes; only CSS geometry changes. */
window.ACPShell = window.ACPShell || {};
ACPShell.globalRegions = (function () {
  "use strict";
  var deps = {}, records = {}, initialized = false, unsubscribe = null, current = {};
  var dock = { mode: "right", width: 320, height: 280, collapsed: false, drawerOpen: false };
  var filesActive = false, listeners = [], resizeObserver = null;
  function doc() { return ACPShell.env ? ACPShell.env.doc() : document; }
  function el(tag, cls, text) { var n = doc().createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function shellEl() { return doc().getElementById("acp-shell"); }
  function bodyEl() { return doc().getElementById("acp-next-body"); }
  function railEl() { return doc().getElementById("acp-next-task-rail"); }
  function sashEl() { return doc().getElementById("acp-next-task-sash"); }
  function openBtn() { return doc().getElementById("acp-next-task-open"); }
  function closeBtn() { return doc().getElementById("acp-next-task-close"); }
  function modeSelect() { return doc().getElementById("acp-next-task-mode"); }
  function mainEl() { return doc().getElementById("acp-shell-main"); }
  function panelApi(host) { return { setState: function (s) { host.setAttribute("data-acp-state", s || "ready"); }, reportError: function (e) { host.setAttribute("data-acp-state", "error"); var p = el("p", "acp-next-error", (e && e.message) || String(e)); p.setAttribute("role", "alert"); host.replaceChildren(p); }, el: host, bodyEl: host, statusEl: null }; }
  function hostDeps(host) { return { context: deps.context, commands: ACPShell.commands, i18n: ACPShell.i18n, request: deps.request || null, client: window.ACPClient || null, panel: panelApi(host) }; }
  function mount(key, moduleId, host) {
    if (records[key]) return;
    var rec = records[key] = { instance: null, disposed: false };
    deps.loader.instantiate(moduleId, "global-" + key, hostDeps(host)).then(function (result) {
      if (rec.disposed) { if (result.instance && result.instance.dispose) result.instance.dispose(); return; }
      rec.instance = result.instance;
      return Promise.resolve(rec.instance.mount(host, hostDeps(host))).then(function () {
        if (rec.instance.updateContext) rec.instance.updateContext(deps.context.getSnapshot());
        host.setAttribute("data-acp-state", "ready");
      });
    }).catch(function (e) { if (!rec.disposed) panelApi(host).reportError(e); });
  }
  function listen(node, type, fn) { if (!node || !node.addEventListener) return; node.addEventListener(type, fn); listeners.push({ node: node, type: type, fn: fn }); }
  function unlisten() { listeners.forEach(function (l) { try { l.node.removeEventListener(l.type, l.fn); } catch (e) { /* removed */ } }); listeners = []; }
  function storageGet() { try { return ACPShell.env ? ACPShell.env.storage.get("acp-next-task-dock-v3") : null; } catch (e) { return null; } }
  function storageSet() { try { if (ACPShell.env) ACPShell.env.storage.set("acp-next-task-dock-v3", JSON.stringify({ version: 3, mode: dock.mode, width: dock.width, height: dock.height, collapsed: dock.collapsed, drawerOpen: dock.drawerOpen })); } catch (e) { /* preference only */ } }
  function restoreDock() {
    try {
      var saved = JSON.parse(storageGet() || "null");
      if (!saved || saved.version !== 3) return;
      if (saved.mode === "right" || saved.mode === "bottom" || saved.mode === "drawer") dock.mode = saved.mode;
      if (isFinite(Number(saved.width))) dock.width = Number(saved.width);
      if (isFinite(Number(saved.height))) dock.height = Number(saved.height);
      dock.collapsed = saved.collapsed === true;
      dock.drawerOpen = saved.drawerOpen === true;
    } catch (e) { /* defaults */ }
  }
  function shellWidth() { var s = shellEl(); return s && s.clientWidth ? s.clientWidth : ((typeof window !== "undefined" && window.innerWidth) || 1302); }
  function mobile() { return shellWidth() <= 849; }
  function effectiveMode() { return mobile() ? "drawer" : dock.mode; }
  function maxWidth() { return Math.max(260, Math.floor(shellWidth() * 0.5)); }
  function clampWidth(value) { return Math.max(260, Math.min(maxWidth(), Math.round(Number(value) || 320))); }
  function clampHeight(value) { return Math.max(180, Math.min(480, Math.round(Number(value) || 280))); }
  function visible() {
    if (filesActive) return false;
    return effectiveMode() === "drawer" ? dock.drawerOpen : !dock.collapsed;
  }
  function applyDock() {
    var shell = shellEl(), body = bodyEl(), rail = railEl(), sash = sashEl(), open = openBtn(), select = modeSelect();
    if (!shell || !body || !rail) return;
    var mode = effectiveMode(), shown = visible(), max = maxWidth();
    dock.width = clampWidth(dock.width);
    dock.height = clampHeight(dock.height);
    shell.setAttribute("data-task-mode", mode);
    shell.classList.toggle("is-task-hidden", !shown);
    body.classList.toggle("is-task-open", mode === "drawer" && shown);
    body.classList.toggle("is-task-collapsed", !shown && mode !== "drawer");
    body.style.setProperty("--next-task-w", dock.width + "px");
    body.style.setProperty("--next-task-h", dock.height + "px");
    rail.classList.toggle("is-files-hidden", filesActive);
    if (shown) rail.removeAttribute("inert"); else rail.setAttribute("inert", "");
    if (shown) rail.removeAttribute("aria-hidden"); else rail.setAttribute("aria-hidden", "true");
    if (shown && mode === "drawer") { rail.setAttribute("role", "dialog"); rail.setAttribute("aria-modal", "true"); }
    else { rail.removeAttribute("role"); rail.removeAttribute("aria-modal"); }
    if (mainEl()) {
      if (shown && mode === "drawer") mainEl().setAttribute("inert", "");
      else mainEl().removeAttribute("inert");
    }
    if (open) { open.hidden = filesActive ? true : shown; open.setAttribute("aria-expanded", shown ? "true" : "false"); }
    if (select) { select.value = mode; select.disabled = mobile(); }
    if (sash) {
      sash.hidden = !shown || mode === "drawer";
      sash.setAttribute("aria-orientation", mode === "bottom" ? "horizontal" : "vertical");
      sash.setAttribute("aria-valuemin", mode === "bottom" ? "180" : "260");
      sash.setAttribute("aria-valuemax", mode === "bottom" ? "480" : String(max));
      sash.setAttribute("aria-valuenow", mode === "bottom" ? String(dock.height) : String(dock.width));
    }
  }
  function setMode(mode) {
    if (mode !== "right" && mode !== "bottom" && mode !== "drawer") return;
    dock.mode = mode;
    dock.collapsed = false;
    if (mode === "drawer") dock.drawerOpen = true;
    storageSet(); applyDock();
  }
  function closeTasks() {
    if (effectiveMode() === "drawer") dock.drawerOpen = false;
    else dock.collapsed = true;
    storageSet(); applyDock();
    var open = openBtn(); if (open && open.focus) open.focus();
  }
  function openTasks() {
    if (effectiveMode() === "drawer") dock.drawerOpen = true;
    else dock.collapsed = false;
    storageSet(); applyDock();
    var rail = railEl();
    var target = closeBtn() || (rail && rail.querySelector ? rail.querySelector("button,select,[tabindex='0']") : null);
    if (target && target.focus) target.focus();
  }
  function setTasks(open) { if (open) openTasks(); else closeTasks(); }
  function onSashPointerDown(event) {
    if (!event || mobile() || effectiveMode() === "drawer" || !visible()) return;
    event.preventDefault();
    var sash = sashEl(), startX = Number(event.clientX) || 0, startY = Number(event.clientY) || 0;
    var startWidth = dock.width, startHeight = dock.height, mode = effectiveMode(), dragging = true;
    if (sash && sash.classList) sash.classList.add("is-dragging");
    if (sash && sash.setPointerCapture && event.pointerId != null) sash.setPointerCapture(event.pointerId);
    function move(ev) {
      if (!dragging) return;
      if (mode === "bottom") {
        var nextH = startHeight + (startY - Number(ev.clientY || 0));
        if (nextH < 140) { dock.collapsed = true; applyDock(); return; }
        dock.collapsed = false; dock.height = clampHeight(nextH); applyDock();
      } else {
        var nextW = startWidth + (startX - Number(ev.clientX || 0));
        if (nextW < 220) { dock.collapsed = true; applyDock(); return; }
        dock.collapsed = false; dock.width = clampWidth(nextW); applyDock();
      }
    }
    function up() {
      dragging = false;
      if (sash && sash.classList) sash.classList.remove("is-dragging");
      doc().removeEventListener("pointermove", move);
      doc().removeEventListener("pointerup", up);
      storageSet();
    }
    doc().addEventListener("pointermove", move);
    doc().addEventListener("pointerup", up);
  }
  function onSashKey(event) {
    if (!event || !visible()) return;
    var mode = effectiveMode();
    if (mode === "drawer") return;
    var next = null;
    if (mode === "bottom") {
      if (event.key === "ArrowUp") next = dock.height + 16;
      else if (event.key === "ArrowDown") next = dock.height - 16;
      else if (event.key === "Home") next = 180;
      else if (event.key === "End") next = 480;
    } else {
      if (event.key === "ArrowLeft") next = dock.width + 16;
      else if (event.key === "ArrowRight") next = dock.width - 16;
      else if (event.key === "Home") next = 260;
      else if (event.key === "End") next = maxWidth();
    }
    if (next == null) return;
    event.preventDefault();
    if (mode === "bottom") dock.height = clampHeight(next); else dock.width = clampWidth(next);
    storageSet(); applyDock();
  }
  function syncResponsive() { applyDock(); }
  function context(snapshot) {
    current = snapshot || {};
    Object.keys(records).forEach(function (key) { var r = records[key]; if (r.instance && r.instance.updateContext) r.instance.updateContext(snapshot); });
    var status = doc().getElementById("acp-next-status"), ref = snapshot && snapshot.taskRef, edit = doc().getElementById("acp-next-job-edit");
    var label = ref && ref.jobId ? ref.jobId + (ref.attempt != null ? " · attempt " + ref.attempt : "") : "未选择任务";
    if (status) status.textContent = label;
    if (edit) edit.hidden = !(ref && ref.jobId);
  }
  function setFilesView(active) { filesActive = !!active; applyDock(); }
  function init(options) {
    if (initialized) return;
    deps = options || {}; initialized = true;
    var taskHost = doc().getElementById("acp-next-task-host");
    if (!taskHost || !deps.loader) return;
    restoreDock();
    if (modeSelect()) modeSelect().value = dock.mode;
    mount("tasks", "task-explorer", taskHost);
    listen(openBtn(), "click", openTasks);
    listen(closeBtn(), "click", closeTasks);
    listen(modeSelect(), "change", function () { setMode(modeSelect().value); });
    listen(sashEl(), "pointerdown", onSashPointerDown);
    listen(sashEl(), "keydown", onSashKey);
    listen(sashEl(), "dblclick", function () { dock.width = 320; dock.height = 280; dock.collapsed = false; storageSet(); applyDock(); });
    listen(doc(), "keydown", function (e) {
      if (!visible() || effectiveMode() !== "drawer") return;
      if (e.key === "Escape") { e.preventDefault(); closeTasks(); return; }
      if (e.key !== "Tab") return;
      var rail = railEl();
      if (!rail || !rail.querySelectorAll) return;
      var items = Array.prototype.slice.call(rail.querySelectorAll("button:not([disabled]), select:not([disabled]), input:not([disabled]), a[href], [tabindex='0']"));
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1], active = doc().activeElement;
      if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    });
    if (typeof window.ResizeObserver !== "undefined") {
      try { resizeObserver = new ResizeObserver(syncResponsive); resizeObserver.observe(shellEl()); } catch (e) { resizeObserver = null; }
    }
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") window.addEventListener("resize", syncResponsive);
    unsubscribe = deps.context.subscribe(context);
    context(deps.context.getSnapshot());
    applyDock();
  }
  function dispose() {
    unlisten();
    if (resizeObserver) { try { resizeObserver.disconnect(); } catch (e) { /* gone */ } resizeObserver = null; }
    if (typeof window !== "undefined" && typeof window.removeEventListener === "function") window.removeEventListener("resize", syncResponsive);
    Object.keys(records).forEach(function (k) { var r = records[k]; r.disposed = true; if (r.instance && r.instance.dispose) r.instance.dispose(); });
    records = {}; if (unsubscribe) unsubscribe(); initialized = false;
  }
  function setActive(active) {
    Object.keys(records).forEach(function (key) {
      var instance = records[key].instance, method = active ? "resume" : "suspend";
      if (instance && typeof instance[method] === "function") instance[method]();
      if (active && instance && typeof instance.resize === "function") instance.resize();
    });
  }
  return { init: init, dispose: dispose, setTasks: setTasks, setActive: setActive, setFilesView: setFilesView, mode: function () { return effectiveMode(); }, width: function () { return dock.width; } };
})();
