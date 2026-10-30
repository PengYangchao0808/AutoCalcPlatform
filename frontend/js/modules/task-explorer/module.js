/*
 * ACP 2.0 next-entry module — task-explorer (P0 read-only).
 *
 * Owner: frontend/js/modules/task-explorer/** (OpenCode, UI-02b).
 * Register contract: window.ACPNextModules["task-explorer"] = { descriptor,
 * factory } (UI-00 module recipe contract draft §1/§2). Classic script, no
 * build, no ESM, no top-level globals besides ACPNextModules.
 *
 * P0 scope: task list + selected-task detail summary + navigation only.
 * NO write operations (pause/cancel/rerun/rename …) — those stay behind the
 * legacy page / later phases.
 *
 * Data flow (single cache, no polling):
 *   - list/detail data goes through the injected `client` (default
 *     window.ACPClient) → ACPClient.taskView (fetchView/fetchSummary/
 *     snapshotView/subscribeView) → ACPClient.store. This module builds NO
 *     second task cache and starts NO timer: refresh is explicit (toolbar
 *     button) + store-subscription driven; dispose cancels every
 *     subscription.
 *   - list use case is a module-OWNED instance of
 *     ACPWorkbench.controllers.createTaskListController() with self-rendered
 *     injected views (renders into the module host only — never touches
 *     #queue-list / jobsCache / taskViewCache globals).
 *   - selection uses a module-OWNED instance of
 *     ACPWorkbench.controllers.createTaskSessionController() (monotonic
 *     per-instance session tokens: A→B→A, same-job rerun and project switch
 *     all invalidate late responses).
 *
 * Context (WorkspaceContext contract draft §3): the module reads
 * context.getSnapshot(), subscribes via context.subscribe(), and publishes
 * focus through context.setFocus(patch, source) when writable; when the host
 * context is read-only it falls back to deps.onFocus?.(patch). Row click →
 * session.select(jobId) → isCurrent-guarded setFocus({taskRef:{jobId,
 * attempt}, source:"task-explorer"}).
 *
 * Hard deps (mount rejects when missing): ACPWorkbench.controllers.
 * createTaskListController / createTaskSessionController,
 * ACPWorkbench.contracts.taskView, client.taskView. Soft deps:
 * ACPWorkbench.controllers.jobDetail (terminal/active pure fns; falls back to
 * the task-view contract pure fns), deps.commands.openTask (detail button).
 */
(function () {
  "use strict";

  var MODULE_ID = "task-explorer";

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    dependencies: [],
    // Top-level provides/binds are what the Shell findPanelFor reads
    // (commands.js resolves "tasks.list" / binds taskRef).
    provides: ["tasks.list"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["tasks.list"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.tasks.errorTitle", messageKey: "next.tasks.errorMessage" },
  };

  /* zh defaults; static chrome also carries data-i18n so the shell i18n pass
   * can retranslate without a module reload. */
  var ZH = {
    "next.tasks.title": "任务列表",
    "next.tasks.refresh": "刷新",
    "next.tasks.project_all": "全部项目",
    "next.tasks.loading": "加载中…",
    "next.tasks.empty": "暂无任务",
    "next.tasks.error": "任务列表加载失败",
    "next.tasks.forbidden": "无权限查看任务列表",
    "next.tasks.retry": "重试",
    "next.tasks.stale": "缓存已失效，请点击刷新",
    "next.tasks.detail_none": "选择任务查看详情",
    "next.tasks.detail_loading": "正在加载任务详情…",
    "next.tasks.detail_failed": "详情加载失败",
    "next.tasks.failure_reason": "失败原因",
    "next.tasks.attempt_title": "第 {n} 次尝试",
    "next.tasks.open_task": "打开任务",
    "next.tasks.prev": "上一页",
    "next.tasks.next": "下一页",
    "next.tasks.page_of": "第 {from}–{to} 项 / 共 {total} 个任务",
    "next.tasks.errorTitle": "任务列表不可用",
    "next.tasks.errorMessage": "task-explorer 模块加载失败，请查看控制台。",
  };

  function t(key, vars) {
    var s = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        s = s.split("{" + k + "}").join(String(vars[k]));
      });
    }
    return s;
  }

  function textEl(doc, tag, cls, key, vars) {
    var el = doc.createElement(tag);
    if (cls) el.className = cls;
    if (key) {
      el.textContent = t(key, vars);
      el.setAttribute("data-i18n", key);
    }
    return el;
  }

  function createTaskExplorer(deps) {
    deps = deps || {};
    var context = deps.context || null;
    var commands = deps.commands || null;
    var onFocus = typeof deps.onFocus === "function" ? deps.onFocus : null;
    var client = deps.client || (typeof window !== "undefined" ? window.ACPClient : null);

    var state = {
      generation: 0,      // bumps on project switch / dispose: late async writes die
      mounted: false,
      suspended: false,
      disposed: false,
      host: null,
      doc: null,
      snapshot: null,     // last ContextSnapshot
      projectId: "",      // "" = all projects
      prefs: { groupBy: "molecule", sort: "created_desc" }, // P0: no filter UI yet
      body: null,         // last task-view body rendered
      rows: [],           // flattened rows (module-local; never a page global)
      selectedJobId: null,
      detail: null,       // last job summary for the selection
      taskView: null,
      contract: null,
      list: null,         // owned createTaskListController() instance
      session: null,      // owned createTaskSessionController() instance
      viewUnsub: null,    // store subscription for the current query key
      contextUnsub: null,
      listeners: [],      // { el, type, fn } — removed on dispose
      rowEls: [],         // row elements for selection marking
      retry: null,        // last error retry callback from views.onError
      projects: [],
      el: {},
    };

    // ---------------------------------------------------------------- DOM --

    function track(el, type, fn) {
      el.addEventListener(type, fn);
      state.listeners.push({ el: el, type: type, fn: fn });
    }

    function untrackAll() {
      state.listeners.forEach(function (l) {
        try { l.el.removeEventListener(l.type, l.fn); } catch (e) { /* already gone */ }
      });
      state.listeners = [];
    }

    function buildDom() {
      var doc = state.doc;
      var root = doc.createElement("div");
      root.className = "acp-mod-tasks";
      root.setAttribute("data-acp-module-state", "loading");

      var toolbar = doc.createElement("div");
      toolbar.className = "acp-mod-tasks-toolbar";
      var project = doc.createElement("select");
      project.className = "acp-mod-tasks-project";
      project.setAttribute("data-i18n-aria", "next.tasks.title");
      var refreshBtn = textEl(doc, "button", "acp-mod-tasks-refresh", "next.tasks.refresh");
      refreshBtn.setAttribute("type", "button");
      toolbar.appendChild(project);
      toolbar.appendChild(refreshBtn);

      var counts = doc.createElement("div");
      counts.className = "acp-mod-tasks-counts";

      var bodyEl = doc.createElement("div");
      bodyEl.className = "acp-mod-tasks-body";

      var pagination = doc.createElement("div");
      pagination.className = "acp-mod-tasks-pagination";

      var stale = textEl(doc, "div", "acp-mod-tasks-stale", "next.tasks.stale");
      stale.setAttribute("data-i18n", "next.tasks.stale");
      stale.hidden = true;

      var detail = doc.createElement("div");
      detail.className = "acp-mod-tasks-detail";

      root.appendChild(toolbar);
      root.appendChild(counts);
      root.appendChild(stale);
      root.appendChild(bodyEl);
      root.appendChild(pagination);
      root.appendChild(detail);
      state.host.replaceChildren(root);

      state.el = {
        root: root, toolbar: toolbar, project: project, refreshBtn: refreshBtn,
        counts: counts, body: bodyEl, pagination: pagination, stale: stale, detail: detail,
      };

      track(refreshBtn, "click", function () { refresh(); });
      track(project, "change", function () { onProjectChange(project.value); });
    }

    // ------------------------------------------------------------ rendering --

    function setState(name) {
      if (state.el.root) state.el.root.setAttribute("data-acp-module-state", name);
    }

    function renderLoading() {
      var doc = state.doc;
      var bodyEl = state.el.body;
      bodyEl.replaceChildren();
      var skel = doc.createElement("div");
      skel.className = "acp-mod-tasks-skel";
      skel.setAttribute("data-i18n", "next.tasks.loading");
      for (var i = 0; i < 6; i++) {
        var line = doc.createElement("div");
        line.className = "acp-mod-tasks-skel-line";
        skel.appendChild(line);
      }
      var label = textEl(doc, "div", "acp-mod-tasks-state-label", "next.tasks.loading");
      skel.appendChild(label);
      bodyEl.appendChild(skel);
      setState("loading");
    }

    function renderStateMessage(key, cls) {
      var bodyEl = state.el.body;
      var wrap = state.doc.createElement("div");
      wrap.className = "acp-mod-tasks-state " + (cls || "");
      // Four-state ARIA parity with the Shell matrix (2026-10-30 修订):
      // error → role=alert; empty/forbidden → role=status.
      if (cls === "acp-mod-tasks-state--error") wrap.setAttribute("role", "alert");
      else wrap.setAttribute("role", "status");
      wrap.appendChild(textEl(state.doc, "div", "acp-mod-tasks-state-label", key));
      bodyEl.replaceChildren(wrap);
      setState(cls === "acp-mod-tasks-state--error" ? "error"
        : cls === "acp-mod-tasks-state--forbidden" ? "forbidden" : "empty");
    }

    function renderEmpty() {
      state.el.counts.textContent = "";
      state.el.pagination.replaceChildren();
      renderStateMessage("next.tasks.empty", "acp-mod-tasks-state--empty");
    }

    function renderError(err, forbidden) {
      var doc = state.doc;
      var bodyEl = state.el.body;
      var wrap = doc.createElement("div");
      wrap.className = "acp-mod-tasks-state acp-mod-tasks-state--error";
      // error → role=alert; forbidden is a STATE not a fault → role=status
      // (Shell four-state matrix, 2026-10-30 修订).
      wrap.setAttribute("role", forbidden ? "status" : "alert");
      wrap.appendChild(textEl(doc, "div", "acp-mod-tasks-state-label",
        forbidden ? "next.tasks.forbidden" : "next.tasks.error"));
      if (err && err.message) {
        var msg = doc.createElement("div");
        msg.className = "acp-mod-tasks-state-detail";
        msg.textContent = String(err.message);
        wrap.appendChild(msg);
      }
      if (state.retry) {
        var retryBtn = textEl(doc, "button", "acp-mod-tasks-retry", "next.tasks.retry");
        retryBtn.setAttribute("type", "button");
        var retry = state.retry;
        track(retryBtn, "click", function () { retry(); });
        wrap.appendChild(retryBtn);
      }
      bodyEl.replaceChildren(wrap);
      setState(forbidden ? "forbidden" : "error");
    }

    function isForbiddenError(err) {
      return !!err && (err.status === 401 || err.status === 403);
    }

    function markSelectedRows() {
      state.rowEls.forEach(function (row) {
        var on = state.selectedJobId && row.getAttribute("data-job-id") === state.selectedJobId;
        row.className = "acp-mod-tasks-row" + (on ? " is-selected" : "");
      });
    }

    function renderRows() {
      var doc = state.doc;
      var bodyEl = state.el.body;
      state.rowEls = [];
      var frag = doc.createElement("div");
      frag.className = "acp-mod-tasks-groups";
      var groups = (state.body && state.body.groups) || [];
      groups.forEach(function (group) {
        var gEl = doc.createElement("div");
        gEl.className = "acp-mod-tasks-group";
        var head = doc.createElement("div");
        head.className = "acp-mod-tasks-group-head";
        var name = doc.createElement("span");
        name.className = "acp-mod-tasks-group-name";
        name.textContent = String(group.display_name || group.key || "");
        head.appendChild(name);
        var cnt = doc.createElement("span");
        cnt.className = "acp-mod-tasks-group-count";
        cnt.textContent = String(group.count != null ? group.count : ((group.jobs || []).length));
        head.appendChild(cnt);
        gEl.appendChild(head);
        (group.jobs || []).forEach(function (job) {
          var jobId = String(job.id || job.job_id || "");
          var row = doc.createElement("div");
          row.className = "acp-mod-tasks-row";
          row.setAttribute("data-job-id", jobId);
          row.setAttribute("role", "button");
          row.setAttribute("tabindex", "0");

          var nameEl = doc.createElement("span");
          nameEl.className = "acp-mod-tasks-row-name";
          nameEl.textContent = String(job.display_name || job.resolved_name || job.default_name || jobId);
          row.appendChild(nameEl);

          var badge = doc.createElement("span");
          badge.className = "acp-mod-tasks-badge acp-mod-tasks-badge--"
            + String(job.status || "pending").toLowerCase();
          badge.textContent = String(job.status || "");
          row.appendChild(badge);

          if (typeof job.attempt === "number" && job.attempt > 1) {
            var attempt = doc.createElement("span");
            attempt.className = "acp-mod-tasks-attempt";
            attempt.textContent = "#" + job.attempt;
            attempt.setAttribute("title", t("next.tasks.attempt_title", { n: job.attempt }));
            attempt.setAttribute("data-i18n-title", "next.tasks.attempt_title");
            row.appendChild(attempt);
          }

          if (job.workflow) {
            var wf = doc.createElement("span");
            wf.className = "acp-mod-tasks-row-workflow";
            wf.textContent = String(job.workflow);
            row.appendChild(wf);
          }

          track(row, "click", function () { onRowClick(jobId); });
          track(row, "keydown", function (ev) {
            if (!ev || (ev.key !== "Enter" && ev.key !== " ")) return;
            if (ev.preventDefault) ev.preventDefault();
            onRowClick(jobId);
          });
          state.rowEls.push(row);
          gEl.appendChild(row);
        });
        frag.appendChild(gEl);
      });
      bodyEl.replaceChildren(frag);
      setState("ready");
      markSelectedRows();
    }

    function renderCounts(counts) {
      counts = counts || {};
      var parts = [];
      Object.keys(counts).forEach(function (k) {
        if (counts[k]) parts.push(k + " " + counts[k]);
      });
      state.el.counts.textContent = parts.join(" · ");
    }

    function renderPagination(body) {
      var doc = state.doc;
      var host = state.el.pagination;
      host.replaceChildren();
      if (!body) return;
      var total = Number(body.total) || 0;
      var offset = Number(body.offset) || 0;
      var limit = Number(body.limit) || 200;
      if (total <= limit && offset <= 0) return;
      var from = total ? offset + 1 : 0;
      var to = Math.min(offset + limit, total);
      var info = textEl(doc, "span", "acp-mod-tasks-page-info", "next.tasks.page_of",
        { from: from, to: to, total: total });
      host.appendChild(info);
      var prev = textEl(doc, "button", "acp-mod-tasks-page-btn", "next.tasks.prev");
      prev.setAttribute("type", "button");
      prev.disabled = offset <= 0;
      track(prev, "click", function () { state.list.setOffset(Math.max(0, offset - limit)); });
      var next = textEl(doc, "button", "acp-mod-tasks-page-btn", "next.tasks.next");
      next.setAttribute("type", "button");
      next.disabled = to >= total;
      track(next, "click", function () { state.list.setOffset(offset + limit); });
      host.appendChild(prev);
      host.appendChild(next);
    }

    function renderDetailNone() {
      state.el.detail.replaceChildren(
        textEl(state.doc, "div", "acp-mod-tasks-state-label", "next.tasks.detail_none"));
    }

    function renderDetailLoading() {
      state.el.detail.replaceChildren(
        textEl(state.doc, "div", "acp-mod-tasks-state-label", "next.tasks.detail_loading"));
    }

    function renderDetailError(err) {
      var doc = state.doc;
      var wrap = doc.createElement("div");
      wrap.className = "acp-mod-tasks-state acp-mod-tasks-state--error";
      wrap.appendChild(textEl(doc, "div", "acp-mod-tasks-state-label", "next.tasks.detail_failed"));
      if (err && err.message) {
        var msg = doc.createElement("div");
        msg.className = "acp-mod-tasks-state-detail";
        msg.textContent = String(err.message);
        wrap.appendChild(msg);
      }
      state.el.detail.replaceChildren(wrap);
    }

    function renderDetail(summary) {
      var doc = state.doc;
      if (!summary) { renderDetailNone(); return; }
      var wrap = doc.createElement("div");
      wrap.className = "acp-mod-tasks-detail-card";

      var head = doc.createElement("div");
      head.className = "acp-mod-tasks-detail-head";
      var name = doc.createElement("span");
      name.className = "acp-mod-tasks-detail-name";
      name.textContent = String(summary.display_name || summary.resolved_name
        || summary.default_name || summary.id || "");
      head.appendChild(name);
      var badge = doc.createElement("span");
      badge.className = "acp-mod-tasks-badge acp-mod-tasks-badge--"
        + String(summary.status || "pending").toLowerCase();
      badge.textContent = String(summary.status || "");
      head.appendChild(badge);
      if (typeof summary.attempt === "number" && summary.attempt > 1) {
        var attempt = doc.createElement("span");
        attempt.className = "acp-mod-tasks-attempt";
        attempt.textContent = "#" + summary.attempt;
        attempt.setAttribute("title", t("next.tasks.attempt_title", { n: summary.attempt }));
        head.appendChild(attempt);
      }
      wrap.appendChild(head);

      // Failure reason comes straight from the taskView summary view model —
      // no status machine is reimplemented here.
      if (summary.error && isTerminal(summary.status)) {
        var fail = doc.createElement("div");
        fail.className = "acp-mod-tasks-detail-failure";
        var failLabel = textEl(doc, "span", "acp-mod-tasks-detail-failure-label",
          "next.tasks.failure_reason");
        fail.appendChild(failLabel);
        var failText = doc.createElement("span");
        failText.className = "acp-mod-tasks-detail-failure-text";
        failText.textContent = String(summary.error);
        fail.appendChild(failText);
        wrap.appendChild(fail);
      }

      if (commands && typeof commands.openTask === "function") {
        var openBtn = textEl(doc, "button", "acp-mod-tasks-open", "next.tasks.open_task");
        openBtn.setAttribute("type", "button");
        track(openBtn, "click", function () {
          commands.openTask({
            jobId: String(summary.id || state.selectedJobId || ""),
            attempt: typeof summary.attempt === "number" ? summary.attempt : undefined,
          }, MODULE_ID);
        });
        wrap.appendChild(openBtn);
      }

      state.el.detail.replaceChildren(wrap);
    }

    // ------------------------------------------------- status pure helpers --

    function jobDetailCtl() {
      var w = typeof window !== "undefined" ? window : null;
      return (w && w.ACPWorkbench && w.ACPWorkbench.controllers
        && w.ACPWorkbench.controllers.jobDetail) || null;
    }

    function isTerminal(status) {
      var jd = jobDetailCtl();
      if (jd && typeof jd.isTerminalJobStatus === "function") return jd.isTerminalJobStatus(status);
      return state.contract && typeof state.contract.isTerminalStatus === "function"
        ? state.contract.isTerminalStatus(status) : false;
    }

    // --------------------------------------------------------------- focus --

    /** Publish a focus patch: context.setFocus(patch, source) per the
     * WorkspaceContext contract; read-only contexts fall back to
     * deps.onFocus(patch). Returns false when neither sink exists. */
    function setFocus(patch, source) {
      if (context && typeof context.setFocus === "function") {
        context.setFocus(patch, source);
        return true;
      }
      if (onFocus) {
        onFocus(Object.assign({ source: source }, patch));
        return true;
      }
      return false;
    }

    // -------------------------------------------------------------- context --

    function applySnapshot(next) {
      if (!next || state.disposed) return;
      state.snapshot = next;
      var pid = next.projectId ? String(next.projectId) : "";
      if (pid !== state.projectId) applyProjectId(pid);
      // External focus from another module: read-only row highlight, no fetch
      // (contract: unrelated focus → silent; relevant taskRef → just mark it).
      var extJob = next.taskRef && next.taskRef.jobId ? String(next.taskRef.jobId) : null;
      if (extJob !== state.selectedJobId) {
        state.selectedJobId = extJob;
        markSelectedRows();
      }
    }

    function applyProjectId(pid) {
      if (pid === state.projectId) return;
      state.generation++; // kills in-flight list + detail writes for the old project
      state.projectId = pid;
      if (state.session) state.session.clear();
      state.selectedJobId = null;
      state.detail = null;
      state.body = null;
      state.rows = [];
      if (state.el.project) state.el.project.value = pid;
      renderDetailNone();
      refresh();
    }

    function onProjectChange(pid) {
      pid = pid ? String(pid) : "";
      if (pid === state.projectId) return;
      // Publish first (WorkspaceContext contract: setFocus(patch, source)); a
      // host echo arrives through the subscription. Apply locally in every
      // case too — hosts are not required to echo, and applyProjectId is a
      // no-op when the echo already landed.
      setFocus({ projectId: pid || null }, MODULE_ID);
      applyProjectId(pid);
    }

    // ----------------------------------------------------------- data paths --

    function applyBody(body) {
      state.body = body;
      if (state.contract && typeof state.contract.flattenRows === "function") {
        state.rows = state.contract.flattenRows(body);
      }
      if (!body || !body.groups || !body.groups.length) {
        renderEmpty();
        return;
      }
      renderRows();
    }

    function currentOffset() {
      return (state.list && state.list.getState && state.list.getState().offset) || 0;
    }

    /** Store subscription for the CURRENT query key. Own fetches notify
     * before this binds (onRows already rendered them), so a notify here is
     * an external invalidation/refetch → re-render cached data, never fetch. */
    function subscribeView() {
      if (state.viewUnsub) { state.viewUnsub(); state.viewUnsub = null; }
      if (!state.taskView || typeof state.taskView.subscribeView !== "function") return;
      var gen = state.generation;
      state.viewUnsub = state.taskView.subscribeView(
        state.prefs, state.projectId || null, currentOffset(), function (snap) {
          if (gen !== state.generation || state.disposed || state.suspended) return;
          if (!snap) return;
          if (snap.status === "fresh" && snap.data) {
            if (snap.data !== state.body) applyBody(snap.data);
            if (state.el.stale) state.el.stale.hidden = true;
          } else if (snap.status === undefined) {
            // Dropped elsewhere (write invalidation): hint only — P0 starts
            // no background refetch loop.
            if (state.el.stale) state.el.stale.hidden = false;
          }
        });
    }

    function refresh() {
      if (!state.list) return Promise.resolve(undefined);
      var gen = state.generation;
      if (!state.body) renderLoading();
      return Promise.resolve(state.list.refresh()).then(function (body) {
        if (gen !== state.generation || state.disposed) return undefined;
        return body;
      }, function () {
        // Error already surfaced via views.onError (with retry). Superseded
        // / aborted refreshes are silent by the controller contract.
        return undefined;
      });
    }

    function onRowClick(jobId) {
      if (!state.session || state.disposed) return;
      state.session.select(jobId).catch(function (err) {
        if (state.list && state.list.isSilentCancel && state.list.isSilentCancel(err)) return;
        if (state.disposed) return;
        renderDetailError(err);
      });
    }

    function initControllers() {
      state.list.init({
        client: state.taskView,
        contract: state.contract,
        getPrefs: function () { return state.prefs; },
        getSelectedProjectId: function () { return state.projectId || null; },
        getLanguage: function () { return "zh-CN"; },
        detailCacheRevision: function () { return 0; },
        renderKeyOf: function (parts) { return JSON.stringify(parts); },
        views: {
          sortGroups: function () { /* P0: server order is authoritative */ },
          onRows: function (rows, body) {
            if (state.disposed) return;
            state.rows = rows;
            applyBody(body);
          },
          renderQueue: function (counts) { if (!state.disposed) renderCounts(counts); },
          renderPagination: function (body) { if (!state.disposed) renderPagination(body); },
          afterRender: function () { if (!state.disposed) subscribeView(); },
          onError: function (err, retry) {
            if (state.disposed) return;
            state.retry = typeof retry === "function" ? retry : null;
            if (!err) { if (state.el.stale) state.el.stale.hidden = true; return; }
            renderError(err, isForbiddenError(err));
          },
          onEmptyError: function () { /* counts failure: error state already rendered */ },
        },
      });

      state.session.init({ hooks: {
        resetForSelection: function (jobId) {
          if (state.disposed) return;
          state.selectedJobId = jobId || null;
          markSelectedRows();
          renderDetailLoading();
        },
        loadDetail: function (jobId) {
          return state.taskView.fetchSummary(jobId);
        },
        applyDetail: function (summary, sess) {
          if (state.disposed || !state.session.isCurrent(sess)) return null;
          // Project double-check: a summary from another project never renders.
          if (summary && summary.project_id != null && state.projectId
            && String(summary.project_id) !== String(state.projectId)) return null;
          state.detail = summary || null;
          renderDetail(state.detail);
          return summary || null;
        },
        afterSelection: function (sess) {
          if (state.disposed || !state.session.isCurrent(sess)) return;
          var attempt = (state.detail && typeof state.detail.attempt === "number")
            ? state.detail.attempt : 1;
          setFocus({ taskRef: { jobId: sess.jobId, attempt: attempt } }, MODULE_ID);
        },
        onSelectionError: function () { /* surfaced through select().catch */ },
      } });
    }

    // ------------------------------------------------------------- projects --

    function requestProjects() {
      if (client && typeof client.request === "function") return client.request("/projects");
      if (client && client.transport && typeof client.transport.request === "function") {
        return client.transport.request("/api/v1/projects");
      }
      return Promise.reject(new Error("task-explorer: no request transport available"));
    }

    function loadProjects() {
      var gen = state.generation;
      return Promise.resolve(requestProjects()).then(function (body) {
        if (gen !== state.generation || state.disposed || !state.el.project) return [];
        state.projects = (body && Array.isArray(body.projects)) ? body.projects : [];
        renderProjects();
        return state.projects;
      }, function () {
        // Non-fatal: keep the "all projects" option only.
        return [];
      });
    }

    function renderProjects() {
      var doc = state.doc;
      var select = state.el.project;
      var all = textEl(doc, "option", "", "next.tasks.project_all");
      all.setAttribute("value", "");
      var opts = [all];
      state.projects.forEach(function (p) {
        var o = doc.createElement("option");
        o.setAttribute("value", String(p.project_id));
        o.textContent = String(p.name || p.project_id);
        opts.push(o);
      });
      if (typeof select.replaceChildren === "function") select.replaceChildren.apply(select, opts);
      else opts.forEach(function (o) { select.appendChild(o); });
      select.value = state.projectId || "";
    }

    // ----------------------------------------------------------- lifecycle --

    function cleanup() {
      state.generation++;
      if (state.viewUnsub) { try { state.viewUnsub(); } catch (e) { /* already gone */ } state.viewUnsub = null; }
      if (state.contextUnsub) { try { state.contextUnsub(); } catch (e) { /* already gone */ } state.contextUnsub = null; }
      untrackAll();
      if (state.list) { try { state.list.dispose(); } catch (e) { /* idempotent */ } }
      if (state.session) { try { state.session.dispose(); } catch (e) { /* idempotent */ } }
    }

    function resolveHardDeps() {
      var w = typeof window !== "undefined" ? window : null;
      var controllers = (w && w.ACPWorkbench && w.ACPWorkbench.controllers) || {};
      var contracts = (w && w.ACPWorkbench && w.ACPWorkbench.contracts) || {};
      if (typeof controllers.createTaskListController !== "function") {
        throw new Error("task-explorer: ACPWorkbench.controllers.createTaskListController unavailable"
          + " (load js/workbench/controllers/task_list_controller.js)");
      }
      if (typeof controllers.createTaskSessionController !== "function") {
        throw new Error("task-explorer: ACPWorkbench.controllers.createTaskSessionController unavailable"
          + " (load js/workbench/controllers/task_session_controller.js)");
      }
      if (!contracts.taskView) {
        throw new Error("task-explorer: ACPWorkbench.contracts.taskView unavailable"
          + " (load js/workbench/contracts/task_view_contract.js)");
      }
      if (!state.taskView) {
        throw new Error("task-explorer: client.taskView unavailable"
          + " (load js/client/resource-store.js + js/client/task-view-query.js)");
      }
      return { controllers: controllers, contract: contracts.taskView };
    }

    async function mount(host, initial) {
      if (state.mounted) throw new Error("task-explorer: already mounted (dispose() first)");
      if (!host) throw new Error("task-explorer: mount(host) requires a host element");
      state.host = host;
      state.doc = host.ownerDocument
        || (typeof document !== "undefined" ? document : null);
      if (!state.doc) throw new Error("task-explorer: no document available for rendering");
      state.disposed = false;
      state.generation++;
      state.taskView = (client && client.taskView) || null;
      var resolved;
      try {
        resolved = resolveHardDeps();
        state.contract = resolved.contract;
        state.list = resolved.controllers.createTaskListController();
        state.session = resolved.controllers.createTaskSessionController();
        state.snapshot = initial || null;
        state.projectId = initial && initial.projectId ? String(initial.projectId) : "";
        buildDom();
        initControllers();
        renderDetailNone();
        renderLoading();
        if (context && typeof context.subscribe === "function") {
          state.contextUnsub = context.subscribe(function (next) {
            if (state.disposed) return;
            applySnapshot(next);
          });
        }
        state.mounted = true;
        loadProjects();
        if (state.projectId && state.el.project) state.el.project.value = state.projectId;
        refresh(); // mount ≠ data ready: fire and forget, errors → error state
        if (initial && initial.taskRef && initial.taskRef.jobId) {
          state.selectedJobId = String(initial.taskRef.jobId);
          markSelectedRows();
        }
      } catch (err) {
        cleanup(); // mount failure self-cleans; never a half-mounted residue
        state.mounted = false;
        throw err;
      }
    }

    function updateContext(next) {
      if (!state.mounted || state.disposed) { state.snapshot = next || null; return; }
      applySnapshot(next);
    }

    function resize() { if (state.mounted && !state.disposed && state.body) renderRows(); }

    function suspend() { state.suspended = true; }

    function resume() { state.suspended = false; }

    /** Idempotent teardown: no subscription, listener, controller or DOM
     * residue; late responses are killed by the generation guard. */
    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      cleanup();
      state.mounted = false;
      state.body = null;
      state.rows = [];
      state.rowEls = [];
      state.detail = null;
      state.retry = null;
      state.projects = [];
      if (state.host) {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
        state.host = null;
      }
      state.el = {};
    }

    return {
      mount: mount,
      updateContext: updateContext,
      resize: resize,
      suspend: suspend,
      resume: resume,
      dispose: dispose,
    };
  }

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createTaskExplorer,
  };
})();
