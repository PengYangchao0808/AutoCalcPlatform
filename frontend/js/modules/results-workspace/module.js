/* F results router: applicability from real workflow; product states stay in existing modules. */
(function () {
  "use strict";
  var DEFS = [
    { id: "energy", moduleId: "energy-profile", label: "能量与轨迹", workflows: { pessearch: 1, scan: 1, irc: 1, confsearch: 1, batchoptimize: 1, optimize: 1, xtb_optimize: 1, xtbpathsearch: 1, xtbconstrainedopt: 1, orcagradient: 1, tsmode: 1 } },
    { id: "path", moduleId: "path-view", label: "路径与结构", workflows: { pessearch: 1, scan: 1, irc: 1, xtbpathsearch: 1 } },
    { id: "pes", moduleId: "pes-handoff", label: "候选与 Batch", workflows: { pessearch: 1 } },
    { id: "nmr", moduleId: "nmr-report", label: "NMR", workflows: { nmr: 1 } },
    { id: "mechanism", moduleId: "mechanism-plan", label: "历史机理（只读）", workflows: { mechanism: 1 }, historyOnly: true },
    { id: "evidence", moduleId: "research-record", label: "研究证据", all: true }
  ];
  function applicable(workflow) { var name = String(workflow || "").toLowerCase(); return DEFS.filter(function (d) { return d.all || d.workflows[name]; }); }
  function create(deps) {
    deps = deps || {}; var doc = deps.doc || document, host = null, snapshot = {}, generation = 0, active = "energy", available = [], current = null, currentHost = null, suspended = false, disposed = false, root = null, tablist = null, content = null, note = null;
    var modules = deps.modules || window.ACPNextModules || {};
    function el(tag, cls, text) { var n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
    function clearCurrent() { if (current && current.instance && current.instance.dispose) current.instance.dispose(); current = null; if (content) content.replaceChildren(); }
    function panelApi(h) { return { setState: function (s) { h.setAttribute("data-acp-state", s || "ready"); }, reportError: function (e) { h.setAttribute("data-acp-state", "error"); var p = el("p", "acp-next-error", (e && e.message) || String(e)); p.setAttribute("role", "alert"); h.replaceChildren(p); }, el: h, bodyEl: h, statusEl: null }; }
    function moduleDeps(h, def) { return { doc: doc, context: deps.context, commands: deps.commands, i18n: deps.i18n, client: deps.client || window.ACPClient, panel: panelApi(h), historyOnly: !!(def && def.historyOnly) }; }
    function select(id) {
      if (available.indexOf(id) < 0) return;
      active = id; clearCurrent();
      Array.prototype.forEach.call(tablist.children, function (b) { var on = b.getAttribute("data-result-tab") === id; b.setAttribute("aria-selected", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
      var def = DEFS.filter(function (d) { return d.id === id; })[0], entry = modules[def.moduleId];
      currentHost = el("div", "acp-result-module"); currentHost.setAttribute("data-result-module", def.moduleId); content.appendChild(currentHost);
      if (!entry || typeof entry.factory !== "function") { panelApi(currentHost).reportError(new Error("结果模块未注册：" + def.moduleId)); return; }
      var gen = generation;
      try { var instance = entry.factory(moduleDeps(currentHost, def)); current = { id: id, instance: instance }; Promise.resolve(instance.mount(currentHost, snapshot)).then(function () { if (gen !== generation || disposed) return; if (instance.updateContext) instance.updateContext(snapshot); if (suspended && instance.suspend) instance.suspend(); }).catch(function (e) { if (gen === generation) panelApi(currentHost).reportError(e); }); } catch (e) { panelApi(currentHost).reportError(e); }
    }
    function renderTabs(workflow) {
      clearCurrent(); available = applicable(workflow).map(function (d) { return d.id; }); tablist.replaceChildren(); note.hidden = available.length > 0;
      if (!available.length) { note.textContent = workflow ? "该工作流没有已装配的结果模块。" : "未选择任务。"; return; }
      available.forEach(function (id) { var def = DEFS.filter(function (d) { return d.id === id; })[0], b = el("button", "acp-result-tab", def.label); b.type = "button"; b.id = "acp-result-tab-" + id; b.setAttribute("role", "tab"); b.setAttribute("data-result-tab", id); b.setAttribute("aria-controls", "acp-result-panel"); b.addEventListener("click", function () { select(id); }); b.addEventListener("keydown", function (e) { var i = available.indexOf(id), next = e.key === "ArrowRight" ? (i + 1) % available.length : e.key === "ArrowLeft" ? (i - 1 + available.length) % available.length : -1; if (next >= 0) { e.preventDefault(); select(available[next]); tablist.children[next].focus(); } }); tablist.appendChild(b); });
      select(available.indexOf(active) >= 0 ? active : available[0]);
    }
    function resolveWorkflow(ref) {
      if (ref && ref.workflow) return Promise.resolve(String(ref.workflow));
      var client = deps.client || window.ACPClient;
      if (!ref || !ref.jobId || !client || !client.jobDetail || typeof client.jobDetail.fetchDetail !== "function") return Promise.resolve("");
      return client.jobDetail.fetchDetail(ref.jobId).then(function (detail) { return detail && detail.job && detail.job.workflow ? String(detail.job.workflow) : ""; }, function () { return ""; });
    }
    function update(next) {
      snapshot = next || {}; var ref = snapshot.taskRef, gen = ++generation;
      if (!ref || !ref.jobId) { renderTabs(""); return; }
      note.hidden = false; note.textContent = "正在确认任务与适用产物…";
      resolveWorkflow(ref).then(function (workflow) { if (disposed || gen !== generation) return; renderTabs(workflow); });
    }
    function mount(h) { host = h; root = el("div", "acp-results-workspace"); tablist = el("div", "acp-result-tabs"); tablist.setAttribute("role", "tablist"); tablist.setAttribute("aria-label", "结果视图"); note = el("p", "acp-result-note", "未选择任务"); note.setAttribute("role", "status"); content = el("div", "acp-result-content"); content.id = "acp-result-panel"; content.setAttribute("role", "tabpanel"); root.appendChild(tablist); root.appendChild(note); root.appendChild(content); host.appendChild(root); update(deps.context && deps.context.getSnapshot ? deps.context.getSnapshot() : {}); }
    return { mount: mount, updateContext: update, suspend: function () { suspended = true; if (current && current.instance.suspend) current.instance.suspend(); }, resume: function () { suspended = false; if (current && current.instance.resume) current.instance.resume(); }, dispose: function () { disposed = true; generation += 1; clearCurrent(); if (host) host.replaceChildren(); } };
  }
  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules["results-workspace"] = { descriptor: { id: "results-workspace", interfaceVersion: 1, version: "0.1.0", provides: ["energy.profile"], binds: ["taskRef"], hosts: ["acp-web"], supportedInstancing: "single" }, factory: create, _internals: { applicable: applicable } };
})();
