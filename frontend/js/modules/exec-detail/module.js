/*
 * ACP Next — exec-detail / evidence panel (UI-03 evidence slice).
 *
 * Read-only projection of ONE task's frozen identity and evidence:
 *   - identity: job id, workflow, status, attempt, plan_digest, submission_id,
 *     release_id, timestamps (the same fields the submit receipt carries);
 *   - evidence: the artifact list (type / relative path / size / checksum);
 *   - failure: error message + error_detail when the server has one;
 *   - handoff: open the task in the explorer or fall back to the classic page.
 *
 * Rules:
 *   - every value comes from GET /jobs/{id}/detail and GET /jobs/{id}/artifacts;
 *     nothing is inferred, and a missing field renders as "—";
 *   - one task at a time, guarded by (projectId, jobId, attempt): a late
 *     response for the previous selection can never paint over the current one;
 *   - no polling, no write route, no second cache — `jobDetail` owns the fetch.
 */
(function () {
  "use strict";

  var MODULE_ID = "exec-detail";

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.2.0",
    provides: ["task.detail", "task.evidence"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["task.detail", "task.evidence"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.detail.errorTitle", messageKey: "next.detail.errorMessage" },
  };

  var ZH = {
    "next.detail.empty": "选择任务后查看状态与证据。",
    "next.detail.empty_hint": "证据来自任务详情与工件清单，只读展示。",
    "next.detail.loading": "正在读取任务详情…",
    "next.detail.error": "任务详情加载失败：{message}",
    "next.detail.retry": "重试",
    "next.detail.job": "任务",
    "next.detail.status": "状态",
    "next.detail.workflow": "工作流",
    "next.detail.attempt": "尝试",
    "next.detail.plan_digest": "计划摘要",
    "next.detail.submission": "提交记录",
    "next.detail.release": "执行版本",
    "next.detail.created": "创建",
    "next.detail.completed": "完成",
    "next.detail.artifacts": "证据工件",
    "next.detail.artifacts_empty": "暂无工件记录。",
    "next.detail.artifacts_error": "工件清单读取失败：{message}",
    "next.detail.download": "下载",
    "next.detail.open_task": "在任务浏览中打开",
    "next.detail.open_classic": "经典工作台",
    "next.detail.wrong_project": "该任务不属于当前项目。",
    "next.detail.attempt_changed": "任务尝试次数已变化，请刷新。",
    "next.detail.failure": "失败原因",
    "next.detail.no_digest": "—",
    "next.detail.errorTitle": "任务详情不可用",
    "next.detail.errorMessage": "exec-detail 模块加载失败，请查看控制台。",
  };

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    var value = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return value;
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function shortDigest(value) {
    var text = String(value || "");
    if (!text) return "";
    var hex = text.indexOf("sha256:") === 0 ? text.slice(7) : text;
    return hex.length > 16 ? hex.slice(0, 12) + "…" + hex.slice(-4) : hex;
  }

  function formatSize(bytes) {
    var n = Number(bytes);
    if (!isFinite(n) || n < 0) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function formatTime(value) {
    if (!value) return "—";
    try {
      var d = new Date(value);
      if (isNaN(d.getTime())) return String(value);
      return d.toLocaleString();
    } catch (e) {
      return String(value);
    }
  }

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object" && detail.message) {
        return (detail.code ? detail.code + ": " : "") + detail.message;
      }
      if (typeof detail === "string") return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function statusKind(status) {
    var text = String(status || "").toLowerCase();
    if (text === "completed") return "ok";
    if (text === "failed" || text === "cancelled") return "bad";
    if (text === "running" || text === "queued" || text === "paused" || text === "waiting_review") return "run";
    return "idle";
  }

  function createExecDetail(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var jobDetail = deps.jobDetail ||
      (typeof window !== "undefined" && window.ACPClient ? window.ACPClient.jobDetail : null);
    var requestFn = deps.request || (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport
      ? function (path, opts) { return window.ACPClient.transport.request(path, opts); }
      : null);
    var commands = deps.commands || null;

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      snapshot: null,
      projectId: "",
      ref: null,
      currentKey: "",
      ui: {},
      token: 0,
    };

    var cleanup = [];

    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      cleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    function buildDom() {
      var root = el(doc, "div", "acp-ev");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-module", MODULE_ID);

      var head = el(doc, "header", "acp-ev-head");
      head.appendChild(el(doc, "h3", "acp-ev-title", t("next.detail.empty")));
      var badge = el(doc, "span", "acp-ev-status", "");
      badge.hidden = true;
      head.appendChild(badge);
      head.appendChild(el(doc, "p", "acp-ev-sub", ""));
      root.appendChild(head);

      var body = el(doc, "div", "acp-ev-body");
      root.appendChild(body);

      var grid = el(doc, "dl", "acp-ev-grid");
      grid.hidden = true;
      body.appendChild(grid);

      var failure = el(doc, "p", "acp-ev-failure");
      failure.hidden = true;
      failure.setAttribute("role", "alert");
      body.appendChild(failure);

      var artifactSection = el(doc, "section", "acp-ev-artifacts");
      artifactSection.hidden = true;
      artifactSection.appendChild(el(doc, "h4", "acp-ev-section-title", t("next.detail.artifacts")));
      var artifactNote = el(doc, "p", "acp-ev-note", "");
      artifactSection.appendChild(artifactNote);
      var artifactList = el(doc, "ul", "acp-ev-list");
      artifactSection.appendChild(artifactList);
      body.appendChild(artifactSection);

      var stateNode = el(doc, "p", "acp-ev-note", t("next.detail.loading"));
      stateNode.hidden = true;
      stateNode.setAttribute("aria-live", "polite");
      body.appendChild(stateNode);

      var actions = el(doc, "div", "acp-ev-actions");
      var open = el(doc, "button", "acp-btn acp-btn--ghost", t("next.detail.open_task"));
      open.type = "button";
      open.setAttribute("data-action", "open-task");
      open.hidden = true;
      var classic = el(doc, "a", "acp-btn acp-btn--ghost", t("next.detail.open_classic"));
      classic.setAttribute("href", "/");
      actions.appendChild(open);
      actions.appendChild(classic);
      root.appendChild(actions);

      state.host.replaceChildren(root);
      state.ui = {
        root: root,
        title: head.children[0],
        badge: badge,
        sub: head.children[2],
        grid: grid,
        failure: failure,
        artifactSection: artifactSection,
        artifactNote: artifactNote,
        artifactList: artifactList,
        stateNode: stateNode,
        open: open,
        emptyHint: null,
      };
      on(open, "click", function () { openTask(); });
    }

    function setState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function reset() {
      state.ui.grid.hidden = true;
      state.ui.grid.replaceChildren();
      state.ui.failure.hidden = true;
      state.ui.failure.textContent = "";
      state.ui.artifactSection.hidden = true;
      state.ui.artifactList.replaceChildren();
      state.ui.artifactNote.textContent = "";
      state.ui.stateNode.hidden = true;
      state.ui.open.hidden = true;
      state.ui.badge.hidden = true;
      state.ui.badge.textContent = "";
      state.ui.title.textContent = t("next.detail.empty");
      state.ui.sub.textContent = t("next.detail.empty_hint");
      setState("empty");
    }

    function renderEmpty() {
      reset();
    }

    function renderLoading() {
      reset();
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.detail.loading");
      setState("loading");
    }

    function renderError(error) {
      reset();
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.detail.error", { message: describeError(error) });
      var retry = el(doc, "button", "acp-btn acp-btn--ghost", t("next.detail.retry"));
      retry.type = "button";
      retry.setAttribute("data-action", "retry-detail");
      on(retry, "click", function () { refresh(); });
      state.ui.stateNode.appendChild(retry);
      setState("error");
    }

    function row(label, value, mono) {
      var dt = el(doc, "dt", "", label);
      var dd = el(doc, "dd", mono ? "acp-ev-mono" : "", value == null || value === "" ? "—" : String(value));
      if (mono && value) dd.setAttribute("title", String(value));
      state.ui.grid.appendChild(dt);
      state.ui.grid.appendChild(dd);
    }

    function renderDetail(detail, artifacts) {
      reset();
      var job = (detail && detail.job) || {};
      var ref = state.currentRef || {};
      if (state.projectId && job.project_id && String(state.projectId) !== String(job.project_id)) {
        state.ui.title.textContent = t("next.detail.wrong_project");
        setState("error");
        return;
      }
      var attempt = job.attempt != null ? job.attempt : ref.attempt;
      if (ref.attempt != null && attempt != null && Number(ref.attempt) !== Number(attempt)) {
        state.ui.title.textContent = t("next.detail.attempt_changed");
        setState("error");
        return;
      }
      var status = String(job.status || "unknown");
      var spec = job.spec || {};
      var workflow = spec.workflow || job.workflow || "";
      var displayName = job.custom_name || job.resolved_name || spec.name || job.name || job.id || ref.jobId;
      state.ui.title.textContent = String(displayName);
      state.ui.badge.hidden = false;
      state.ui.badge.textContent = status;
      state.ui.badge.setAttribute("data-status", statusKind(status));
      state.ui.sub.textContent = [workflow || "—", t("next.detail.attempt") + " " + String(attempt || 1)]
        .join(" · ");
      state.ui.grid.hidden = false;
      row(t("next.detail.job"), job.id || ref.jobId, true);
      row(t("next.detail.workflow"), workflow);
      row(t("next.detail.plan_digest"), job.plan_digest ? shortDigest(job.plan_digest) : "", true);
      row(t("next.detail.submission"), job.submission_id || "", true);
      row(t("next.detail.release"), job.release_id || "", true);
      row(t("next.detail.created"), formatTime(job.created_at));
      row(t("next.detail.completed"), formatTime(job.completed_at));

      var errorText = job.error || (detail && detail.error_detail && (detail.error_detail.message || detail.error_detail.summary));
      if (errorText) {
        state.ui.failure.hidden = false;
        state.ui.failure.textContent = t("next.detail.failure") + "：" + String(errorText);
      }

      renderArtifacts(artifacts);
      state.ui.open.hidden = !state.currentRef;
      setState("ready");
    }

    function renderArtifacts(artifacts) {
      var list = artifacts && Array.isArray(artifacts.artifacts) ? artifacts.artifacts : [];
      state.ui.artifactSection.hidden = false;
      if (!list.length) {
        state.ui.artifactNote.textContent = t("next.detail.artifacts_empty");
        return;
      }
      state.ui.artifactNote.textContent = "";
      list.slice(0, 40).forEach(function (artifact) {
        var item = el(doc, "li", "acp-ev-artifact");
        item.appendChild(el(doc, "span", "acp-ev-artifact-type", String(artifact.artifact_type || "file")));
        var path = el(doc, "span", "acp-ev-artifact-path", String(artifact.file_path || artifact.artifact_id || ""));
        item.appendChild(path);
        item.appendChild(el(doc, "span", "acp-ev-artifact-size", formatSize(artifact.size_bytes)));
        if (artifact.artifact_id) {
          var link = el(doc, "a", "acp-ev-artifact-link", t("next.detail.download"));
          link.setAttribute("href", "/api/v1/artifacts/" + encodeURIComponent(artifact.artifact_id) + "/download");
          item.appendChild(link);
        }
        state.ui.artifactList.appendChild(item);
      });
    }

    function openTask() {
      if (!commands || typeof commands.openTask !== "function" || !state.currentRef) return;
      try {
        commands.openTask({
          jobId: String(state.currentRef.jobId),
          attempt: state.currentRef.attempt != null ? state.currentRef.attempt : null,
          source: MODULE_ID,
        });
      } catch (e) {
        if (typeof console !== "undefined" && console.error) console.error("openTask failed", e);
      }
    }

    function refresh() {
      var ref = state.currentRef;
      if (!ref || !ref.jobId) return;
      var key = String(state.projectId || "") + ":" + String(ref.jobId) + ":" + String(ref.attempt == null ? "" : ref.attempt);
      state.currentKey = key;
      var token = state.token + 1;
      state.token = token;
      var gen = state.generation;
      renderLoading();
      var detailPromise = jobDetail && typeof jobDetail.fetchDetail === "function"
        ? jobDetail.fetchDetail(String(ref.jobId))
        : Promise.reject(new Error("jobDetail.fetchDetail unavailable"));
      var artifactPromise = requestFn
        ? requestFn("/api/v1/jobs/" + encodeURIComponent(String(ref.jobId)) + "/artifacts", { method: "GET" })
            .catch(function (error) {
              if (error && (error.status === 404 || error.status === 405)) return { artifacts: [] };
              throw error;
            })
        : Promise.resolve({ artifacts: [] });
      Promise.all([detailPromise, artifactPromise]).then(function (results) {
        if (state.disposed || !state.mounted || state.generation !== gen || state.token !== token) return;
        if (state.currentKey !== key) return;
        renderDetail(results[0], results[1]);
      }).catch(function (error) {
        if (state.disposed || !state.mounted || state.generation !== gen || state.token !== token) return;
        if (state.currentKey !== key) return;
        renderError(error);
      });
    }

    function applySnapshot(snapshot) {
      state.snapshot = snapshot || null;
      state.projectId = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      var ref = snapshot && snapshot.taskRef ? snapshot.taskRef : null;
      if (!ref || !ref.jobId) {
        state.currentRef = null;
        state.currentKey = "";
        state.token += 1;
        if (state.mounted) renderEmpty();
        return;
      }
      var next = { jobId: String(ref.jobId), attempt: ref.attempt == null ? null : Number(ref.attempt), taskId: ref.taskId };
      var key = state.projectId + ":" + next.jobId + ":" + String(next.attempt == null ? "" : next.attempt);
      state.currentRef = next;
      if (key === state.currentKey) return;
      state.currentKey = key;
      refresh();
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("exec-detail: mount host missing");
      if (!doc) throw new Error("exec-detail: document missing");
      state.host = hostEl;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      var context = api.context || deps.context || null;
      buildDom();
      renderEmpty();
      if (context && typeof context.getSnapshot === "function") applySnapshot(context.getSnapshot());
      if (context && typeof context.subscribe === "function") {
        var unsubscribe = context.subscribe(function (snapshot) {
          if (state.disposed) return;
          applySnapshot(snapshot);
        });
        cleanup.push(unsubscribe);
      }
      var langHandler = function () {
        if (state.disposed) return;
        if (state.currentRef) refresh();
        else renderEmpty();
      };
      if (doc.addEventListener) {
        doc.addEventListener("acp:langchange", langHandler);
        cleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      return Promise.resolve();
    }

    function updateContext(snapshot) {
      if (state.disposed || !state.mounted) return;
      applySnapshot(snapshot);
    }

    function suspend() { state.suspended = true; }

    function resume() { state.suspended = false; }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.token += 1;
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.currentRef = null;
    }

    return {
      mount: mount,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose,
    };
  }

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createExecDetail,
  };
})();
