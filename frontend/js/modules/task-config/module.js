(function () {
  "use strict";
  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules["task-config"] = {
    descriptor: { id: "task-config", interfaceVersion: 1, dependencies: [], provides: ["task.config"], binds: [], capabilities: { requires: [], provides: ["task.config"] }, hosts: ["acp-web"], supportedInstancing: "single", configSchemaVersion: 0 },
    factory: function () {
      var host, alive = false, generation = 0;
      function node(tag, cls, text) {
        var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e;
      }
      function load() {
        var token = ++generation;
        host.textContent = "";
        var intro = node("p", "acp-detail-intro", "选择计算类型，查看服务提供的工作流。完整参数、结构输入和提交检查在经典配置向导中完成。");
        var link = node("a", "acp-detail-action", "打开完整配置向导");
        link.href = "/#new-calculation";
        host.appendChild(intro); host.appendChild(link);
        var status = node("p", "acp-detail-note", "正在读取工作流目录…"); host.appendChild(status);
        return window.ACPClient.transport.request("/api/v1/workflow-catalog").then(function (data) {
          if (!alive || token !== generation) return;
          if (!data || !Array.isArray(data.workflows)) throw new Error("工作流目录格式无效");
          var workflows = data.workflows.filter(function (w) { return w.status === "active" && w.visible !== false; });
          status.textContent = workflows.length ? "可用计算类型" : "当前没有可显示的工作流";
          var list = node("div", "acp-workflow-list");
          workflows.forEach(function (w) {
            var card = node("div", "acp-workflow-card");
            card.appendChild(node("strong", "", w.label_zh || w.label || w.id));
            card.appendChild(node("span", "acp-detail-note", w.id + (w.default_backend ? " · " + w.default_backend : "")));
            if (w.description) card.appendChild(node("span", "acp-detail-note", w.description));
            list.appendChild(card);
          });
          host.appendChild(list);
        }).catch(function (error) {
          if (!alive || token !== generation) return;
          status.textContent = "工作流目录加载失败：" + (error.message || String(error));
          status.setAttribute("role", "alert");
          var retry = node("button", "acp-detail-retry", "重试"); retry.type = "button"; retry.addEventListener("click", load); host.appendChild(retry);
        });
      }
      return { mount: function (element) { host = element; alive = true; return load(); }, updateContext: function () {}, dispose: function () { alive = false; generation++; if (host) host.textContent = ""; host = null; } };
    }
  };
})();
