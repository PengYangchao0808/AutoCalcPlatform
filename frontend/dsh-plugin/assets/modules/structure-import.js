/* file: js/modules/structure-import/module.js */
/* B view: real structure import only. No drawing surface or implied Ketcher capability. */
(function () {
  "use strict";
  function create(deps) {
    var doc = document, disposed = false, generation = 0, parsed = null;
    function el(tag, cls, text) { var n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
    function request(path, options) {
      var base = window.ACPShell.env ? window.ACPShell.env.apiBase() : "/api/v1";
      return window.ACPClient.transport.request(base + path, options);
    }
    function mount(host) {
      var page = el("div", "acp-import-page");
      var notice = el("div", "acp-import-notice"); notice.innerHTML = "<strong>二维绘制器尚未集成</strong><span>当前可使用真实 SMILES / XYZ / SDF / MOL / GJF / ORCA 导入。导入不会提交计算。</span>"; page.appendChild(notice);
      var work = el("div", "acp-import-work");
      var tools = el("aside", "acp-import-tools"); tools.setAttribute("aria-label", "绘制工具可用性");
      ["选择", "单键 / 双键", "环与元素", "立体化学"].forEach(function (label) { var b = el("button", "", label + " · 待集成"); b.type = "button"; b.disabled = true; tools.appendChild(b); }); work.appendChild(tools);
      var paper = el("div", "acp-import-paper"); var title = el("h2", "", "导入结构"); var hint = el("p", "", "粘贴结构文本或选择文件；服务端解析成功后可进入三维结构或任务配置。");
      var label = el("label", "", "结构内容"); var textarea = doc.createElement("textarea"); textarea.id = "acp-structure-import-text"; textarea.setAttribute("aria-label", "结构内容"); textarea.placeholder = "例如：CCO，或粘贴 XYZ 坐标…"; label.appendChild(textarea);
      var file = doc.createElement("input"); file.type = "file"; file.setAttribute("aria-label", "选择结构文件"); file.accept = ".xyz,.sdf,.mol,.gjf,.com,.inp,.txt";
      var actions = el("div", "acp-import-actions"); var parse = el("button", "acp-btn acp-btn--primary", "解析结构"); parse.type = "button"; var view = el("button", "acp-btn", "进入三维结构"); view.type = "button"; view.disabled = true; var configure = el("button", "acp-btn", "为此结构配置任务"); configure.type = "button"; configure.disabled = true; actions.appendChild(parse); actions.appendChild(view); actions.appendChild(configure);
      var status = el("p", "acp-import-status", "尚未导入结构"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
      paper.appendChild(title); paper.appendChild(hint); paper.appendChild(label); paper.appendChild(file); paper.appendChild(actions); paper.appendChild(status); work.appendChild(paper); page.appendChild(work); host.appendChild(page);
      function setReady(asset) { parsed = asset; view.disabled = !(asset && asset.xyz); configure.disabled = !(asset && asset.xyz); var bits = []; if (asset.formula) bits.push(asset.formula); if (asset.atom_count != null) bits.push(asset.atom_count + " 个原子"); if (asset.charge != null) bits.push("电荷 " + asset.charge); if (asset.multiplicity != null) bits.push("自旋 " + asset.multiplicity); status.textContent = "解析成功" + (bits.length ? " · " + bits.join(" · ") : ""); }
      function run() {
        var content = textarea.value.trim(); if (!content) { status.textContent = "请先粘贴结构文本或选择文件。"; textarea.focus(); return; }
        var gen = ++generation; parsed = null; view.disabled = configure.disabled = true; parse.disabled = true; parse.textContent = "解析中…"; status.textContent = "正在由服务端解析结构…";
        request("/structures/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: content, format: "auto", filename: "" }), timeoutMs: 0 }).then(function (body) {
          if (disposed || gen !== generation) return;
          if (!body || body.ok === false || !Array.isArray(body.structures) || !body.structures.length || !body.structures[0].xyz) throw new Error(body && body.errors && body.errors[0] || "未解析到可显示的三维结构");
          setReady(body.structures[0]);
        }).catch(function (e) { if (!disposed && gen === generation) status.textContent = "结构解析失败：" + ((e && e.message) || String(e)); }).then(function () { if (!disposed && gen === generation) { parse.disabled = false; parse.textContent = "解析结构"; } });
      }
      function open(ref, config) { if (!parsed || !parsed.xyz) return; var payload = { kind: "inline-xyz", text: parsed.xyz, frameIndex: 0, source: "structure-import" }; if (config) { deps.commands.openConfiguration(payload); } else deps.commands.openStructure(payload); }
      parse.addEventListener("click", run); view.addEventListener("click", function () { open(parsed, false); }); configure.addEventListener("click", function () { open(parsed, true); });
      file.addEventListener("change", function () { var f = file.files && file.files[0]; if (!f) return; f.text().then(function (text) { if (!disposed) { textarea.value = text; run(); } }).catch(function (e) { status.textContent = "文件读取失败：" + e.message; }); });
    }
    return { mount: mount, updateContext: function () {}, dispose: function () { disposed = true; generation += 1; } };
  }
  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules["structure-import"] = { descriptor: { id: "structure-import", interfaceVersion: 1, version: "0.1.0", provides: ["structure.import"], binds: [], hosts: ["acp-web"], supportedInstancing: "single" }, factory: create };
})();

