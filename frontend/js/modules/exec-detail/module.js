(function () {
  "use strict";
  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules["exec-detail"] = {
    descriptor: { id: "exec-detail", interfaceVersion: 1, dependencies: [], provides: ["task.detail"], binds: ["taskRef"], capabilities: { requires: [], provides: ["task.detail"] }, hosts: ["acp-web"], supportedInstancing: "single", configSchemaVersion: 0 },
    factory: function () {
      var host, alive = false, generation = 0, current = "";
      function line(label, value) {
        if (value == null || value === "") return;
        var row = document.createElement("div"); row.className = "acp-detail-row";
        var name = document.createElement("span"); name.textContent = label;
        var text = document.createElement("strong"); text.textContent = String(value);
        row.appendChild(name); row.appendChild(text); host.appendChild(row);
      }
      function update(snapshot) {
        var ref = snapshot && snapshot.taskRef, key = ref && ref.jobId ? String(snapshot.projectId || "") + ":" + ref.jobId + ":" + (ref.attempt || 1) : "";
        if (key === current) return;
        current = key; var token = ++generation; host.textContent = "";
        if (!key) { host.textContent = "选择任务后查看状态和执行信息。"; return; }
        host.textContent = "正在读取任务详情…";
        window.ACPClient.jobDetail.fetchDetail(ref.jobId).then(function (detail) {
          if (!alive || token !== generation) return;
          var job = detail && detail.job || {};
          host.textContent = "";
          if (snapshot.projectId && job.project_id && String(snapshot.projectId) !== String(job.project_id)) { host.textContent = "任务不属于当前项目。"; return; }
          if (job.attempt != null && ref.attempt != null && Number(job.attempt) !== Number(ref.attempt)) { host.textContent = "任务尝试次数已变化，请刷新任务列表。"; return; }
          line("任务", job.custom_name || job.name || ref.jobId);
          line("状态", job.status); line("工作流", job.workflow); line("尝试", job.attempt != null ? job.attempt : ref.attempt);
          line("错误", job.error || (detail.error_detail && (detail.error_detail.message || detail.error_detail.summary)));
          var note = document.createElement("p"); note.className = "acp-detail-note"; note.textContent = "文件、日志和任务操作可在经典工作台中查看。"; host.appendChild(note);
          var link = document.createElement("a"); link.className = "acp-detail-action"; link.href = "/"; link.textContent = "打开经典工作台"; host.appendChild(link);
        }).catch(function (error) { if (alive && token === generation) { host.textContent = "任务详情加载失败：" + (error.message || String(error)); host.setAttribute("role", "alert"); } });
      }
      return { mount: function (element) { host = element; alive = true; host.textContent = "选择任务后查看状态和执行信息。"; }, updateContext: update, dispose: function () { alive = false; generation++; current = ""; if (host) host.textContent = ""; host = null; } };
    }
  };
})();
