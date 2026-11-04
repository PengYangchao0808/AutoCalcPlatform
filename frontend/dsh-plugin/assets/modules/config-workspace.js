/* file: js/modules/config-workspace/module.js */
/* E config host: one visible submit surface; tsmode/scan use their dedicated real editors. */
(function () {
  "use strict";
  var SPECIAL = { tsmode: "tsmode-editor", scan: "s2-scan", edit: "job-edit" };
  var LABEL = { tsmode: "TS Mode 专属配置", scan: "S2 键长扫描配置", edit: "修改参数后重算" };
  function create(deps) {
    deps = deps || {};
    var doc = deps.doc || document, host = null, root = null, generalHost = null, specialHost = null, route = "general", workflow = "", general = null, special = null, snapshot = {}, disposed = false, generation = 0, note = null, back = null, lastEditKey = "";
    var modules = deps.modules || window.ACPNextModules || {};
    function el(tag, cls, text) { var n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
    function api(h) { return { setState: function (s) { h.setAttribute("data-acp-module-state", s || "ready"); }, reportError: function (e) { h.setAttribute("data-acp-module-state", "error"); var p = el("p", "acp-next-error", (e && e.message) || String(e)); p.setAttribute("role", "alert"); h.replaceChildren(p); }, el: h, bodyEl: h, statusEl: null }; }
    function moduleDeps(h, extra) { return Object.assign({ doc: doc, context: deps.context, commands: deps.commands, i18n: deps.i18n, client: deps.client || window.ACPClient, panel: api(h) }, extra || {}); }
    function disposeSpecial() { if (special && special.dispose) special.dispose(); special = null; if (specialHost) specialHost.replaceChildren(); }
    function routeGeneral(focus) {
      route = "general";
      if (specialHost) specialHost.hidden = true;
      if (generalHost) generalHost.hidden = false;
      back.hidden = true;
      note.textContent = "";
      disposeSpecial();
      if (general && general.resume) general.resume();
      if (focus && generalHost) generalHost.focus();
    }
    function routeSpecial(id) {
      route = id;
      if (!SPECIAL[id]) { routeGeneral(); return; }
      var moduleId = SPECIAL[id], entry = modules[moduleId];
      if (general && general.suspend) general.suspend();
      generalHost.hidden = true; specialHost.hidden = false; specialHost.replaceChildren(); back.hidden = false;
      note.textContent = id === "edit" ? "从当前任务加载冻结配置；修改后走既有编辑重算链。" : "当前工作流使用专用配置；通用向导内容保留，返回后可继续。";
      disposeSpecial();
      if (!entry || typeof entry.factory !== "function") { api(specialHost).reportError(new Error("专用配置模块未注册：" + moduleId)); return; }
      var gen = ++generation;
      try {
        special = entry.factory(moduleDeps(specialHost));
        Promise.resolve(special.mount(specialHost, snapshot)).then(function () { if (disposed || gen !== generation) return; if (special.updateContext) special.updateContext(snapshot); }).catch(function (e) { if (gen === generation) api(specialHost).reportError(e); });
      } catch (e) { api(specialHost).reportError(e); }
    }
    function onChange(id) {
      if (disposed) return;
      workflow = String(id || "");
      if (SPECIAL[workflow]) routeSpecial(workflow); else routeGeneral();
    }
    function mount(h) {
      host = h; root = el("div", "acp-config-workspace");
      var bar = el("div", "acp-config-routebar"); note = el("span", "acp-config-route-note", ""); back = el("button", "acp-config-back", "返回通用配置"); back.type = "button"; back.hidden = true;
      back.addEventListener("click", function () { routeGeneral(true); });
      bar.appendChild(note); bar.appendChild(back);
      generalHost = el("div", "acp-config-general"); specialHost = el("div", "acp-config-special"); specialHost.hidden = true;
      generalHost.setAttribute("data-acp-config-route", "general"); specialHost.setAttribute("data-acp-config-route", "special");
      root.appendChild(bar); root.appendChild(generalHost); root.appendChild(specialHost); host.appendChild(root);
      var entry = modules["task-wizard"];
      if (!entry || typeof entry.factory !== "function") { api(generalHost).reportError(new Error("任务向导模块未注册")); return; }
      try {
        general = entry.factory(moduleDeps(generalHost, { onWorkflowChange: onChange }));
        Promise.resolve(general.mount(generalHost, snapshot)).then(function () { if (!disposed && general.updateContext) general.updateContext(snapshot); }).catch(function (e) { if (!disposed) api(generalHost).reportError(e); });
      } catch (e) { api(generalHost).reportError(e); }
    }
    return { mount: mount, loadStructure: function (ref) { routeGeneral(); return general && general.loadStructure ? general.loadStructure(ref) : { ok: false }; }, openJobEdit: function () { if (route !== "edit") routeSpecial("edit"); }, updateContext: function (next) { snapshot = next || {}; var editKey = snapshot.editRef && snapshot.editRef.jobId ? String(snapshot.editRef.jobId) + ":" + String(snapshot.editRef.attempt == null ? "" : snapshot.editRef.attempt) : ""; if (editKey && editKey !== lastEditKey) { lastEditKey = editKey; routeSpecial("edit"); } if (!editKey) lastEditKey = ""; if (general && general.updateContext) general.updateContext(snapshot); if (special && special.updateContext) special.updateContext(snapshot); }, suspend: function () { if (route === "general" && general && general.suspend) general.suspend(); else if (special && special.suspend) special.suspend(); }, resume: function () { if (route === "general" && general && general.resume) general.resume(); else if (special && special.resume) special.resume(); }, dispose: function () { disposed = true; generation += 1; if (general && general.dispose) general.dispose(); disposeSpecial(); if (host) host.replaceChildren(); }, _internals: { SPECIAL: SPECIAL } };
  }
  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules["config-workspace"] = { descriptor: { id: "config-workspace", interfaceVersion: 1, version: "0.1.0", provides: ["task.wizard"], binds: [], hosts: ["acp-web"], supportedInstancing: "single" }, factory: create, _internals: { SPECIAL: SPECIAL } };
})();

