/*
 * ACP 2.0 next-entry module — task-explorer (UI-02b + A-17…A-24).
 *
 * Owner: frontend/js/modules/task-explorer/** (OpenCode, UI-02b).
 * Register contract: window.ACPNextModules["task-explorer"] = { descriptor,
 * factory } (UI-00 module recipe contract draft §1/§2). Classic script, no
 * build, no ESM, no top-level globals besides ACPNextModules.
 *
 * Scope (ACP 3.0 Track A / V5):
 *   - A-17 queue list/filter/status: the list keeps coming from the frozen
 *     task-view contract through the owned task-list controller; the toolbar
 *     adds a VISIBLE status-chip filter plus translated status badges. The
 *     chip vocabulary is derived at render time from
 *     ACPWorkbench.contracts.taskView.ACTIVE_STATUSES/TERMINAL_STATUSES (the
 *     frontend mirror of the services JobStatus enum) and extended with any
 *     status the server reports in body.counts that the frozen contract does
 *     not know yet — never a second hardcoded list. Toggling a chip rewrites
 *     prefs.filters.status and refreshes through the controller, so the
 *     controller's query-key change → offset reset → store query rules apply
 *     (ONE cache: client.taskView/ACPClient.store).
 *   - A-18 pause/unpause/continue: state-aware row actions backed ONLY by the
 *     injected client.jobCommands (pause/unpause/continueJob). PAUSED is an
 *     active, non-terminal status: pause is only shown for RUNNING, unpause
 *     only for PAUSED, and PAUSED never receives terminal-only actions.
 *   - A-19 rerun/cancel: terminal-aware rerun (completed/failed/cancelled)
 *     and active-aware cancel (every non-terminal status, PAUSED included).
 *     Every action enters a busy state while its promise is in flight and
 *     shows an inline error on rejection; success refreshes the list through
 *     the existing task-view/store path. No direct fetch, no second command
 *     client.
 *   - A-20 purge: per-row purge and multi-select batch purge. Both are behind
 *     an EXPLICIT second confirmation (an in-module modal dialog — a single
 *     click never deletes). The only transport is the injected
 *     client.jobCommands.purge({jobIds, deleteData}); the module never calls
 *     DELETE, never builds a purge endpoint and never touches the DB/disk
 *     directly — the server's purge_cascade semantics stay authoritative.
 *     Terminal/allowed semantics are NOT re-implemented here: the server's
 *     per-job report (ok/action/error) is rendered verbatim, so an active
 *     job's refusal ("job is active (status=…)…") is shown inline.
 *   - A-21 clone/move/rename: the injected client.jobCommands.clone/.move/
 *     .rename only. Rename reuses the task-view row's name_revision as
 *     expected_name_revision (optimistic lock); a 409 conflict is shown and
 *     the next attempt reuses the revision the server returned. Move/clone
 *     pick a target from the project list already loaded through the existing
 *     GET /projects path (no path construction, no second project client).
 *     Every lifecycle action is busy/error-safe and refreshes through the
 *     owned list controller.
 *   - A-22 project CRUD: list/create/rename/delete through the EXISTING
 *     project endpoints only (GET/POST /projects, PATCH/DELETE
 *     /projects/{id}). No project file path is ever constructed here: the
 *     server owns project ids/directories; the module only re-reads the same
 *     project list the toolbar already loads and refreshes the task list
 *     through the owned list controller. The delete confirmation asks the
 *     server for the project's dependency count (GET /projects/{id}/jobs,
 *     an existing endpoint) and renders it before the second confirmation;
 *     a server refusal (active jobs / default project) is shown verbatim.
 *   - A-23 tag management: add/remove job tags through the EXISTING
 *     client.jobCommands.batchOp({taskIds:[id], op:"add_tags"|"remove_tags",
 *     payload:{tags}}) → POST /api/v2/tasks/batch-ops (the only job-tag write
 *     endpoint; project tag rename/merge/delete is a project-level registry
 *     and stays out of scope). Tags are labels for grouping/filtering and
 *     NEVER path components: the module sends tag strings only and never
 *     derives a directory/file name from them. Server per-task errors are
 *     shown inline; success refreshes through the same task-view/store path.
 *   - A-24 recycle bin (trash view) + trash batch operations: a toolbar
 *     toggle switches the SAME owned list controller to the recoverable
 *     (soft-deleted) scope by setting prefs.archived = "only", which is part
 *     of ACPClient.taskView.queryKeyOf → the controller resets the offset and
 *     re-queries the single store (GET /api/v2/task-view?archived=only). No
 *     second cache, no direct fetch, no new endpoint.
 *     BACKEND REALITY (inspected 2026-10-31): there is NO /jobs/trash or
 *     /trash endpoint and no tombstone table for purged jobs.
 *     store.purge_cascade deletes the jobs/tasks/stage_tasks/artifacts/
 *     mechanism_studies rows, so a purged task cannot be listed or restored by
 *     any API. The ONLY recoverable job state the platform exposes is the
 *     existing `tasks.archived` flag (GET /api/v2/task-view?archived=only;
 *     POST /api/v2/tasks/batch-ops op=archive|unarchive). The trash view is
 *     therefore the archived (soft-deleted) set:
 *       restore ("恢复")  → client.jobCommands.batchOp({taskIds, op:"unarchive"})
 *                           → POST /api/v2/tasks/batch-ops (existing op),
 *       permanent delete  → client.jobCommands.purge({jobIds, deleteData})
 *                           → POST /api/v1/jobs/purge → purge_cascade (NEVER a
 *                           raw DELETE, ANTI #13), behind an explicit second
 *                           confirmation dialog.
 *     Permanent delete is irreversible by design: the dialog says so and the
 *     trash note repeats that purged tasks are not recoverable. The normal
 *     queue view is untouched (default scope archived:"exclude"; its A-20
 *     batch purge stays exactly as landed); batch selection in the trash is
 *     cleared on enter/leave and only ever targets rendered trash rows.
 *   - If client.jobCommands is absent, all write actions are hidden; if only
 *     one method is missing, only that button renders disabled. No action
 *     runs twice for one row (one in-flight command per job). A client
 *     without jobCommands keeps the trash view readable but actionless.
 *   - A-25/A-26 (detail stage/log) and A-22/A-23 project/tag changes are
 *     explicitly out of this task's scope; their landed behaviour is left
 *     untouched.
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
 * the task-view contract pure fns), deps.commands.openTask (detail button),
 * client.jobCommands (A-18/A-19 row commands + A-20/A-21 lifecycle commands;
 * absent → actions hidden, partial → only the missing buttons disabled).
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
    // A-17: status filter + status vocabulary (mirrors JobStatus zh labels).
    "next.tasks.filter_status": "状态筛选",
    "next.tasks.filter_all": "全部",
    "next.tasks.status.queued": "排队中",
    "next.tasks.status.starting": "启动中",
    "next.tasks.status.pending": "待处理",
    "next.tasks.status.running": "运行中",
    "next.tasks.status.paused": "已暂停",
    "next.tasks.status.cancelling": "取消中",
    "next.tasks.status.waiting_review": "等待审核",
    "next.tasks.status.completed": "已完成",
    "next.tasks.status.failed": "失败",
    "next.tasks.status.cancelled": "已取消",
    // A-18/A-19: row command surface.
    "next.tasks.actions_label": "任务操作",
    "next.tasks.action.pause": "暂停",
    "next.tasks.action.unpause": "恢复",
    "next.tasks.action.continue": "续算",
    "next.tasks.action.rerun": "重跑",
    "next.tasks.action.cancel": "取消",
    "next.tasks.action_busy": "{action}中…",
    "next.tasks.action_failed": "{action}失败：{message}",
    "next.tasks.action_unavailable": "当前客户端不支持该操作",
    "next.tasks.action_unknown_error": "未知错误",
    "next.tasks.action_in_flight": "该任务已有命令在执行，请稍候",
    // A-21: lifecycle actions (clone / move / rename).
    "next.tasks.action.clone": "复制",
    "next.tasks.action.move": "移动",
    "next.tasks.action.rename": "重命名",
    // A-20: purge (per-job + batch) — always behind a second confirmation.
    "next.tasks.action.purge": "清除",
    "next.tasks.action.purge_title": "清除任务（需二次确认）",
    "next.tasks.select_job": "选择任务",
    "next.tasks.batch.selected": "已选 {n} 项",
    "next.tasks.batch.select_all": "全选",
    "next.tasks.batch.clear": "清空选择",
    "next.tasks.batch.purge": "批量清除",
    "next.tasks.purge_report": "已清除 {ok} 个任务，{fail} 个失败",
    "next.tasks.purge_report_ok": "已清除 {count} 个任务",
    "next.tasks.purge_error": "清除失败：{message}",
    "next.tasks.dialog.purge_title": "确认清除任务",
    "next.tasks.dialog.purge_single": "将清除任务「{name}」的记录，此操作不可撤销。",
    "next.tasks.dialog.purge_batch": "将清除 {count} 个任务的记录，此操作不可撤销。",
    "next.tasks.dialog.delete_data": "同时删除磁盘数据",
    "next.tasks.dialog.purge_confirm": "确认清除",
    "next.tasks.dialog.purge_prompt": "请再次确认：清除后不可恢复。",
    "next.tasks.dialog.cancel": "取消",
    "next.tasks.dialog.rename_title": "重命名任务",
    "next.tasks.dialog.rename_label": "任务名称",
    "next.tasks.dialog.rename_placeholder": "留空恢复默认名称",
    "next.tasks.dialog.rename_save": "保存",
    "next.tasks.dialog.rename_invalid": "名称不能超过 200 个字符，且不能包含控制字符",
    "next.tasks.dialog.rename_unavailable": "缺少名称版本（name_revision），无法重命名",
    "next.tasks.dialog.rename_conflict": "名称已被修改，已刷新列表，请重试",
    "next.tasks.dialog.clone_title": "复制任务到项目",
    "next.tasks.dialog.move_title": "移动任务到项目",
    "next.tasks.dialog.project_label": "目标项目",
    "next.tasks.dialog.clone_confirm": "复制",
    "next.tasks.dialog.move_confirm": "移动",
    "next.tasks.dialog.no_projects": "暂无可用项目",
    "next.tasks.dialog.close": "关闭",
    // A-22: project CRUD (list/create/rename/delete through the v1 project
    // endpoints; delete asks the server for the dependency count first).
    "next.tasks.projects.manage": "项目",
    "next.tasks.projects.title": "项目管理",
    "next.tasks.projects.new_placeholder": "新项目名称",
    "next.tasks.projects.create": "创建",
    "next.tasks.projects.name_label": "项目名称",
    "next.tasks.projects.rename": "重命名",
    "next.tasks.projects.rename_title": "重命名项目",
    "next.tasks.projects.save": "保存",
    "next.tasks.projects.delete": "删除",
    "next.tasks.projects.delete_title": "删除项目",
    "next.tasks.projects.delete_confirm": "确认删除",
    "next.tasks.projects.delete_warning": "删除项目不会删除任务数据：该项目的任务会移动到默认项目；进行中的任务会阻止删除（由服务端判定）。",
    "next.tasks.projects.deps_loading": "正在检查项目依赖…",
    "next.tasks.projects.deps": "该项目包含 {n} 个任务。",
    "next.tasks.projects.deps_active": "该项目包含 {n} 个任务，其中 {active} 个进行中。",
    "next.tasks.projects.deps_more": "该项目至少包含 {n} 个任务。",
    "next.tasks.projects.deps_active_more": "该项目至少包含 {n} 个任务，其中 {active} 个进行中。",
    "next.tasks.projects.deps_unknown": "无法获取项目依赖数量（仍可尝试删除，服务端会校验）。",
    "next.tasks.projects.deps_error": "无法获取项目依赖数量：{message}（仍可尝试删除，服务端会校验）",
    "next.tasks.projects.name_invalid": "项目名称不能为空",
    "next.tasks.projects.create_failed": "创建项目失败：{message}",
    "next.tasks.projects.rename_failed": "重命名项目失败：{message}",
    "next.tasks.projects.delete_failed": "删除项目失败：{message}",
    "next.tasks.projects.created": "已创建项目「{name}」",
    "next.tasks.projects.renamed": "已重命名项目「{name}」",
    "next.tasks.projects.deleted": "已删除项目「{name}」",
    "next.tasks.projects.empty": "暂无项目",
    "next.tasks.projects.unavailable": "当前客户端不支持项目管理",
    // A-23: per-job tag management (add/remove through the batch-ops tag ops).
    "next.tasks.tags.manage": "标签",
    "next.tasks.tags.title": "管理标签",
    "next.tasks.tags.empty": "暂无标签",
    "next.tasks.tags.placeholder": "输入标签后添加",
    "next.tasks.tags.add": "添加",
    "next.tasks.tags.remove": "移除标签 {tag}",
    "next.tasks.tags.unavailable": "当前客户端不支持标签管理",
    "next.tasks.action.tags": "更新标签",
    // A-24: recycle bin (trash view) over the recoverable archived scope.
    "next.tasks.trash.open": "回收站",
    "next.tasks.trash.close": "返回任务列表",
    "next.tasks.trash.title": "回收站（已归档任务）",
    "next.tasks.trash.toggle_label": "切换回收站视图",
    "next.tasks.trash.note": "回收站列出的是已归档（软删除）的任务，可以恢复。已被「清除」的任务记录由服务端级联删除（purge_cascade），无法在这里找回。",
    "next.tasks.trash.readonly": "当前客户端不支持回收站操作，回收站为只读。",
    "next.tasks.trash.empty": "回收站为空",
    "next.tasks.trash.restore": "恢复",
    "next.tasks.trash.delete": "彻底删除",
    "next.tasks.trash.unavailable": "当前客户端不支持该回收站操作",
    "next.tasks.trash.report_ok": "已恢复 {count} 个任务",
    "next.tasks.trash.report": "已恢复 {ok} 个任务，{fail} 个失败",
    "next.tasks.trash.restore_failed": "恢复失败：{message}",
    "next.tasks.trash.delete_report_ok": "已彻底删除 {count} 个任务",
    "next.tasks.trash.delete_report": "已彻底删除 {ok} 个任务，{fail} 个失败",
    "next.tasks.trash.delete_failed": "彻底删除失败：{message}",
    "next.tasks.batch.restore": "批量恢复",
    "next.tasks.batch.trash_delete": "批量彻底删除",
    "next.tasks.dialog.trash_delete_title": "确认彻底删除",
    "next.tasks.dialog.trash_delete_single": "将彻底删除回收站中的任务「{name}」，服务端会级联删除任务记录（purge_cascade），此操作不可恢复。",
    "next.tasks.dialog.trash_delete_batch": "将彻底删除回收站中的 {count} 个任务，服务端会级联删除任务记录（purge_cascade），此操作不可恢复。",
    "next.tasks.dialog.trash_delete_prompt": "请再次确认：彻底删除后，任务不会再出现在回收站，也无法恢复。",
    "next.tasks.dialog.trash_delete_confirm": "彻底删除",
  };

  /* Action key -> injected client.jobCommands method. The command client is
   * the ONLY write path (never fetch, never a second commands object). */
  var ACTION_METHOD = {
    pause: "pause",
    unpause: "unpause",
    "continue": "continueJob",
    rerun: "rerun",
    cancel: "cancel",
    purge: "purge",
    clone: "clone",
    move: "move",
    rename: "rename",
  };

  var ACTION_LABEL_KEYS = {
    pause: "next.tasks.action.pause",
    unpause: "next.tasks.action.unpause",
    "continue": "next.tasks.action.continue",
    rerun: "next.tasks.action.rerun",
    cancel: "next.tasks.action.cancel",
    purge: "next.tasks.action.purge",
    clone: "next.tasks.action.clone",
    move: "next.tasks.action.move",
    rename: "next.tasks.action.rename",
    tags: "next.tasks.action.tags",
    // A-24: local busy/error labels for the trash commands (they are NOT
    // entries in ACTION_METHOD — restore rides batchOp, permanent delete
    // reuses jobCommands.purge behind its own confirmation dialog).
    restore: "next.tasks.trash.restore",
    "trash-delete": "next.tasks.trash.delete",
  };

  /* A-20/A-21 lifecycle actions rendered on EVERY row of a client that has
   * jobCommands at all. They deliberately carry data-acp-lifecycle instead of
   * data-acp-action so the A-18/A-19 state-aware matrix (status-driven) stays
   * a separate concern: purge/clone/move/rename are not status commands, and
   * the server decides whether one is allowed (its error text is surfaced). */
  var LIFECYCLE_ACTIONS = ["rename", "clone", "move", "purge"];

  var LIFECYCLE_TITLE_KEYS = {
    purge: "next.tasks.action.purge_title",
    clone: "next.tasks.dialog.clone_title",
    move: "next.tasks.dialog.move_title",
    rename: "next.tasks.dialog.rename_title",
  };

  var DIALOG_TITLE_KEYS = {
    purge: "next.tasks.dialog.purge_title",
    clone: "next.tasks.dialog.clone_title",
    move: "next.tasks.dialog.move_title",
    rename: "next.tasks.dialog.rename_title",
    // A-22: the project manager list and its two mutation dialogs.
    projects: "next.tasks.projects.title",
    "project-rename": "next.tasks.projects.rename_title",
    "project-delete": "next.tasks.projects.delete_title",
    // A-23: per-job tag editor.
    tags: "next.tasks.tags.title",
    // A-24: permanent delete of a trashed (archived) task.
    "trash-delete": "next.tasks.dialog.trash_delete_title",
  };

  var DIALOG_CONFIRM_KEYS = {
    purge: "next.tasks.dialog.purge_confirm",
    clone: "next.tasks.dialog.clone_confirm",
    move: "next.tasks.dialog.move_confirm",
    rename: "next.tasks.dialog.rename_save",
    "project-rename": "next.tasks.projects.save",
    "project-delete": "next.tasks.projects.delete_confirm",
    "trash-delete": "next.tasks.dialog.trash_delete_confirm",
  };

  /* Dialogs whose body owns the actions (create/rename/delete rows, tag
   * add/remove): they only get a close button, no generic confirm. */
  var CLOSE_ONLY_DIALOGS = { projects: true, tags: true };

  /* A-22: the project dependency probe is bounded by the server's documented
   * per-project job list cap (v1 GET /projects/{id}/jobs limit ≤ 1000). A
   * full page means "at least this many", never an exact count. */
  var PROJECT_DEPS_LIMIT = 1000;

  function hasOwn(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  function statusKey(status) {
    var key = "next.tasks.status." + String(status || "").toLowerCase();
    return hasOwn(ZH, key) ? key : "";
  }

  /** Translated status for badges/chips/counts; unknown values stay verbatim
   * (a server status the frozen contract has not mirrored yet must never be
   * silently relabeled or dropped). */
  function statusLabel(status) {
    var key = statusKey(status);
    return key ? ZH[key] : String(status == null ? "" : status);
  }

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
      // A-17: filters.status[] rides the existing task-view query key, so a
      // filter change resets pagination inside the owned list controller.
      // A-24: prefs.archived is the SAME query-key field the legacy queue
      // uses ("exclude" = queue, "only" = recoverable trash scope).
      prefs: {
        groupBy: "molecule",
        sort: "created_desc",
        filters: { status: [] },
        archived: "exclude",
      },
      body: null,         // last task-view body rendered
      rows: [],           // flattened rows (module-local; never a page global)
      selectedJobId: null,
      detail: null,       // last job summary for the selection
      taskView: null,
      contract: null,
      list: null,         // owned createTaskListController() instance
      session: null,      // owned createTaskSessionController() instance
      jobCommands: null,  // injected client.jobCommands ONLY (write path)
      actions: {          // per-job command UI state (survives list re-renders)
        busy: Object.create(null),   // jobId -> { action, status }
        errors: Object.create(null), // jobId -> { action, status, message }
      },
      // A-20: batch purge selection (module-local; ids only, never job data).
      selection: Object.create(null), // jobId -> true
      batchMsg: null,     // { key, vars, tone } summary of the last purge report
      purgeInFlight: false,
      // A-24: recycle bin mode. `trash` selects the archived scope in prefs;
      // `trashInFlight` serialises restore / permanent-delete batches (the
      // per-row guard stays beginBusy, exactly like A-20/A-21).
      trash: false,
      trashInFlight: false,
      // A-20/A-21: the ONE open modal dialog descriptor (null = closed).
      dialog: null,       // { kind, jobId, jobIds, value, projectId, deleteData, ... }
      rowUi: Object.create(null), // jobId -> { row, buttons[], msg } rebuilt per render
      filterEls: null,    // { all, chips: {status: {chip, count}} }
      filterSignature: "",
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
      // A-22: project CRUD entry point. Disabled (with an explicit title)
      // when the injected client exposes no request transport at all.
      var manage = textEl(doc, "button", "acp-mod-tasks-projects-btn", "next.tasks.projects.manage");
      manage.setAttribute("type", "button");
      manage.setAttribute("data-acp-projects-manage", "");
      manage.setAttribute("title", t("next.tasks.projects.title"));
      manage.setAttribute("data-i18n-title", "next.tasks.projects.title");
      var refreshBtn = textEl(doc, "button", "acp-mod-tasks-refresh", "next.tasks.refresh");
      refreshBtn.setAttribute("type", "button");
      // A-24: recycle-bin toggle. It switches the owned list controller's
      // query scope (prefs.archived) — never a second list, never a fetch.
      var trashBtn = textEl(doc, "button", "acp-mod-tasks-trash-btn", "next.tasks.trash.open");
      trashBtn.setAttribute("type", "button");
      trashBtn.setAttribute("data-acp-trash-toggle", "");
      trashBtn.setAttribute("aria-pressed", "false");
      trashBtn.setAttribute("title", t("next.tasks.trash.title"));
      trashBtn.setAttribute("data-i18n-title", "next.tasks.trash.title");
      toolbar.appendChild(project);
      toolbar.appendChild(manage);
      toolbar.appendChild(trashBtn);
      toolbar.appendChild(refreshBtn);

      var counts = doc.createElement("div");
      counts.className = "acp-mod-tasks-counts";

      // A-17: visible status filter — chips are (re)built from the task-view
      // contract vocabulary, never from a local hardcoded status list.
      var filters = doc.createElement("div");
      filters.className = "acp-mod-tasks-filters";
      filters.setAttribute("data-acp-filters", "status");
      filters.setAttribute("role", "group");
      filters.setAttribute("aria-label", t("next.tasks.filter_status"));
      filters.setAttribute("data-i18n-aria", "next.tasks.filter_status");

      // A-20: batch selection bar (hidden until at least one row is checked).
      var batch = doc.createElement("div");
      batch.className = "acp-mod-tasks-batch";
      batch.setAttribute("data-acp-batch-bar", "purge");
      batch.setAttribute("role", "group");
      batch.setAttribute("aria-label", t("next.tasks.batch.purge"));
      batch.setAttribute("data-i18n-aria", "next.tasks.batch.purge");
      batch.hidden = true;

      // A-24: the recycle bin is the archived (soft-deleted) scope. The note
      // states the scope and the irreversible part OUT LOUD — purged tasks
      // are not listable/restorable (see the module header).
      var trashNote = doc.createElement("div");
      trashNote.className = "acp-mod-tasks-trash-note";
      trashNote.setAttribute("data-acp-trash-note", "");
      trashNote.setAttribute("role", "note");
      trashNote.hidden = true;
      var trashScope = textEl(doc, "span", "acp-mod-tasks-trash-scope", "next.tasks.trash.note");
      var trashReadonly = textEl(doc, "span", "acp-mod-tasks-trash-readonly",
        "next.tasks.trash.readonly");
      trashReadonly.hidden = true;
      trashNote.appendChild(trashScope);
      trashNote.appendChild(trashReadonly);

      var batchMsg = doc.createElement("div");
      batchMsg.className = "acp-mod-tasks-batch-msg";
      batchMsg.setAttribute("role", "status");
      batchMsg.setAttribute("data-acp-batch-msg", "");
      batchMsg.hidden = true;

      var bodyEl = doc.createElement("div");
      bodyEl.className = "acp-mod-tasks-body";

      var pagination = doc.createElement("div");
      pagination.className = "acp-mod-tasks-pagination";

      var stale = textEl(doc, "div", "acp-mod-tasks-stale", "next.tasks.stale");
      stale.setAttribute("data-i18n", "next.tasks.stale");
      stale.hidden = true;

      var detail = doc.createElement("div");
      detail.className = "acp-mod-tasks-detail";

      // A-20/A-21: single modal host. Confirmation/rename/project dialogs are
      // rebuilt inside it; a closed dialog leaves an empty hidden host.
      var modal = doc.createElement("div");
      modal.className = "acp-mod-tasks-modal";
      modal.hidden = true;
      modal.setAttribute("data-acp-dialog-state", "closed");

      root.appendChild(toolbar);
      root.appendChild(filters);
      root.appendChild(trashNote);
      root.appendChild(batch);
      root.appendChild(batchMsg);
      root.appendChild(counts);
      root.appendChild(stale);
      root.appendChild(bodyEl);
      root.appendChild(pagination);
      root.appendChild(detail);
      root.appendChild(modal);
      state.host.replaceChildren(root);

      state.el = {
        root: root, toolbar: toolbar, project: project, manage: manage,
        refreshBtn: refreshBtn, trashBtn: trashBtn,
        filters: filters, trashNote: trashNote, trashScope: trashScope,
        trashReadonly: trashReadonly,
        batch: batch, batchMsg: batchMsg, counts: counts,
        body: bodyEl, pagination: pagination, stale: stale, detail: detail,
        modal: modal,
      };

      track(refreshBtn, "click", function () { refresh(); });
      track(manage, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        openProjectsDialog("");
      });
      track(trashBtn, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        setTrashMode(!state.trash);
      });
      track(project, "change", function () { onProjectChange(project.value); });
      renderFilters();
      syncTrashChrome();
      renderBatchBar();
      renderBatchMessage();
      syncProjectManagementAvailability();
    }

    // ------------------------------------------- recycle bin (A-24) --
    //
    // Backend reality (inspected 2026-10-31): no /jobs/trash or /trash route
    // exists and purge_cascade removes the job rows outright, so a purged task
    // is not listable or restorable. The recoverable scope the platform DOES
    // expose is tasks.archived (the legacy queue's own "归档/取消归档"), which
    // this view reads through the SAME task-view query key and writes back with
    // the SAME batch-ops / purge commands A-20/A-23 already use.

    /** Trash mode switches prefs.archived ("exclude" queue ↔ "only" trash) and
     * lets the owned controller re-run the query: archived is part of
     * ACPClient.taskView.queryKeyOf, so the offset resets and the single store
     * is re-queried (no second cache, no direct fetch). */
    function setTrashMode(on) {
      if (state.disposed) return;
      var next = !!on;
      if (next === state.trash) return;
      state.trash = next;
      state.prefs = Object.assign({}, state.prefs, { archived: next ? "only" : "exclude" });
      // Every piece of UI state belongs to the previous scope: bump the
      // generation so in-flight list/detail/command receipts go inert, then
      // drop the mode-local state (same rule as a project switch).
      state.generation++;
      state.selection = Object.create(null);
      state.actions = { busy: Object.create(null), errors: Object.create(null) };
      state.rowUi = Object.create(null);
      state.batchMsg = null;
      state.purgeInFlight = false;
      state.trashInFlight = false;
      state.body = null;
      state.rows = [];
      state.rowEls = [];
      state.retry = null;
      closeDialog();
      syncTrashChrome();
      renderBatchBar();
      renderBatchMessage();
      // The previous scope's rows must not linger behind the new query.
      renderLoading();
      var pending = null;
      try {
        // Offset 0 + one refresh through the OWNED controller (the task-view
        // query key already carries `archived`, so this is also the pagination
        // reset the switch implies).
        pending = state.list && typeof state.list.setOffset === "function"
          ? state.list.setOffset(0)
          : refresh();
      } catch (err) {
        pending = null; // controller already disposed: nothing left to query
      }
      if (pending && typeof pending.then === "function") {
        // The controller rethrows after views.onError rendered the error
        // state; never leave that rejection unhandled.
        Promise.resolve(pending).catch(function () { return undefined; });
      }
    }

    /** Toolbar/note/batch chrome for the current mode. */
    function syncTrashChrome() {
      var btn = state.el.trashBtn;
      var root = state.el.root;
      var note = state.el.trashNote;
      var labelKey = state.trash ? "next.tasks.trash.close" : "next.tasks.trash.open";
      if (btn) {
        btn.textContent = t(labelKey);
        btn.setAttribute("data-i18n", labelKey);
        btn.setAttribute("aria-pressed", state.trash ? "true" : "false");
        btn.className = "acp-mod-tasks-trash-btn" + (state.trash ? " is-active" : "");
        btn.setAttribute("aria-label", t("next.tasks.trash.toggle_label"));
        btn.setAttribute("data-i18n-aria", "next.tasks.trash.toggle_label");
      }
      if (root) root.setAttribute("data-acp-trash", state.trash ? "on" : "off");
      if (note) {
        note.hidden = !state.trash;
        note.setAttribute("data-acp-trash-note-state", state.trash ? "on" : "off");
      }
      if (state.el.trashScope) state.el.trashScope.textContent = t("next.tasks.trash.note");
      if (state.el.trashReadonly) {
        state.el.trashReadonly.hidden = !state.trash || canOperateTrash();
        state.el.trashReadonly.textContent = t("next.tasks.trash.readonly");
      }
      var batch = state.el.batch;
      if (batch) {
        batch.setAttribute("data-acp-batch-bar", state.trash ? "trash" : "purge");
        var ariaKey = state.trash ? "next.tasks.batch.trash_delete" : "next.tasks.batch.purge";
        batch.setAttribute("aria-label", t(ariaKey));
        batch.setAttribute("data-i18n-aria", ariaKey);
      }
      if (state.el.body) {
        state.el.body.setAttribute("data-acp-trash-scope", state.trash ? "archived" : "queue");
      }
    }

    /** A trash row may be restored when the batch-ops client exists; it may be
     * permanently deleted when the purge command exists (purge_cascade). */
    function canRestore() {
      return !!batchOpMethod();
    }

    function canPurgeTrash() {
      return !!commandMethod("purge");
    }

    function canOperateTrash() {
      return canRestore() || canPurgeTrash();
    }

    /** Row selection surface: the queue keeps A-20's purge-only rule; the
     * trash needs at least one trash command. */
    function canSelectRows() {
      return state.trash ? canOperateTrash() : canBatchPurge();
    }

    /** A row is a trash row when it belongs to the archived scope this view
     * requested. A row explicitly reporting archived:false is never treated as
     * trash (never targeted by restore / permanent delete). */
    function isTrashJob(jobId) {
      if (!state.trash) return false;
      var job = findJob(jobId);
      if (!job) return false;
      return job.archived !== false;
    }

    function selectedTrashIds() {
      return selectedIds().filter(isTrashJob);
    }

    // --------------------------------------------- status filter (A-17) --

    function statusFilter() {
      return (state.prefs && state.prefs.filters && state.prefs.filters.status) || [];
    }

    /** Contract-first status vocabulary (ACTIVE then TERMINAL, the same split
     * the frozen task-view contract exposes). A status present in the server
     * body.counts but not yet mirrored by the contract is appended (count > 0)
     * so a backend vocabulary addition is never silently unfilterable. */
    function statusVocabulary() {
      var c = state.contract || {};
      var list = [];
      (c.ACTIVE_STATUSES || []).concat(c.TERMINAL_STATUSES || []).forEach(function (s) {
        var k = String(s || "").toLowerCase();
        if (k && list.indexOf(k) === -1) list.push(k);
      });
      var counts = (state.body && state.body.counts) || {};
      Object.keys(counts).forEach(function (s) {
        var k = String(s || "").toLowerCase();
        if (k && list.indexOf(k) === -1 && Number(counts[s]) > 0) list.push(k);
      });
      // An ACTIVE filter value must never lose its chip (otherwise the list
      // stays filtered by a status the user can no longer see/toggle off).
      statusFilter().forEach(function (s) {
        var k = String(s || "").toLowerCase();
        if (k && list.indexOf(k) === -1) list.push(k);
      });
      return list;
    }

    function renderFilters() {
      var host = state.el.filters;
      if (!host || state.disposed) return;
      var vocab = statusVocabulary();
      var signature = vocab.join(",");
      if (signature !== state.filterSignature) {
        state.filterSignature = signature;
        var doc = state.doc;
        var label = textEl(doc, "span", "acp-mod-tasks-filters-label", "next.tasks.filter_status");
        var all = doc.createElement("button");
        all.setAttribute("type", "button");
        all.className = "acp-mod-tasks-filter-chip acp-mod-tasks-filter-chip--all";
        all.setAttribute("data-acp-filter", "all");
        var allLabel = textEl(doc, "span", "acp-mod-tasks-filter-chip-label", "next.tasks.filter_all");
        all.appendChild(allLabel);
        track(all, "click", function (ev) { onFilterAll(ev); });
        var nodes = [label, all];
        var chips = Object.create(null);
        vocab.forEach(function (status) {
          var chip = doc.createElement("button");
          chip.setAttribute("type", "button");
          chip.className = "acp-mod-tasks-filter-chip acp-mod-tasks-filter-chip--" + status;
          chip.setAttribute("data-acp-status", status);
          var key = statusKey(status);
          var chipLabelEl = doc.createElement("span");
          chipLabelEl.className = "acp-mod-tasks-filter-chip-label";
          chipLabelEl.textContent = statusLabel(status);
          if (key) chipLabelEl.setAttribute("data-i18n", key);
          chip.appendChild(chipLabelEl);
          var count = doc.createElement("span");
          count.className = "acp-mod-tasks-filter-chip-count";
          chip.appendChild(count);
          track(chip, "click", (function (s) {
            return function (ev) { onToggleStatus(s, ev); };
          })(status));
          chips[status] = { chip: chip, count: count };
          nodes.push(chip);
        });
        host.replaceChildren.apply(host, nodes);
        state.filterEls = { all: all, chips: chips };
      }
      syncFilterChips();
    }

    /** Chips reflect prefs + whole-scope body.counts (task_views contract:
     * counts are unaffected by filters/truncation). */
    function syncFilterChips() {
      if (!state.filterEls) return;
      var active = statusFilter();
      var allOn = active.length === 0;
      state.filterEls.all.className =
        "acp-mod-tasks-filter-chip acp-mod-tasks-filter-chip--all" + (allOn ? " is-active" : "");
      state.filterEls.all.setAttribute("aria-pressed", allOn ? "true" : "false");
      var counts = (state.body && state.body.counts) || {};
      Object.keys(state.filterEls.chips).forEach(function (status) {
        var ui = state.filterEls.chips[status];
        var on = active.indexOf(status) !== -1;
        ui.chip.className = "acp-mod-tasks-filter-chip acp-mod-tasks-filter-chip--" + status
          + (on ? " is-active" : "");
        ui.chip.setAttribute("aria-pressed", on ? "true" : "false");
        var count = counts[status];
        ui.count.textContent = (typeof count === "number" && count > 0) ? String(count) : "";
      });
    }

    /** Filter change → new prefs object → controller refresh. The controller
     * sees a new query key (filters.status is part of ACPClient.taskView
     * queryKeyOf), resets offset to 0 and re-queries the single store. */
    function setStatusFilter(next) {
      if (state.disposed) return;
      var desired = (next || []).slice();
      var current = statusFilter();
      var same = desired.slice().sort().join(",") === current.slice().sort().join(",");
      state.prefs = Object.assign({}, state.prefs, {
        filters: { status: desired },
      });
      renderFilters();
      if (!same) refresh(); // no redundant request when the set did not change
    }

    function onToggleStatus(status, ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (state.disposed) return;
      var active = statusFilter().slice();
      var idx = active.indexOf(status);
      if (idx >= 0) active.splice(idx, 1);
      else active.push(status);
      setStatusFilter(active);
    }

    function onFilterAll(ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (state.disposed) return;
      setStatusFilter([]);
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
      renderStateMessage(state.trash ? "next.tasks.trash.empty" : "next.tasks.empty",
        "acp-mod-tasks-state--empty");
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

    /** Status badge — class + raw status stay data-driven (CSS palette per
     * value); the visible text goes through the shared status vocabulary and
     * carries data-i18n for the shell retranslation pass. */
    function makeStatusBadge(doc, status) {
      var raw = String(status || "").toLowerCase();
      var badge = doc.createElement("span");
      badge.className = "acp-mod-tasks-badge acp-mod-tasks-badge--" + (raw || "pending");
      badge.setAttribute("data-acp-status", raw);
      if (!raw) return badge;
      var key = statusKey(raw);
      badge.textContent = statusLabel(raw);
      if (key) badge.setAttribute("data-i18n", key);
      return badge;
    }

    function renderRows() {
      var doc = state.doc;
      var bodyEl = state.el.body;
      state.rowEls = [];
      state.rowUi = Object.create(null); // row elements die with this render
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
          var rawStatus = String(job.status || "").toLowerCase();
          var row = doc.createElement("div");
          row.className = "acp-mod-tasks-row";
          row.setAttribute("data-job-id", jobId);
          row.setAttribute("data-acp-status", rawStatus);
          row.setAttribute("data-acp-selected", state.selection[jobId] ? "true" : "false");
          // A-24: the row belongs to the recoverable (archived) scope when the
          // trash view is active; the raw server flag stays visible for tests
          // and for the restore/delete guards.
          if (state.trash) row.setAttribute("data-acp-trash-row", "true");
          row.setAttribute("data-acp-archived", job.archived === true ? "true" : "false");
          row.setAttribute("role", "button");
          row.setAttribute("tabindex", "0");

          appendRowSelect(row, job, jobId);

          var nameEl = doc.createElement("span");
          nameEl.className = "acp-mod-tasks-row-name";
          nameEl.textContent = String(job.display_name || job.resolved_name || job.default_name || jobId);
          row.appendChild(nameEl);

          row.appendChild(makeStatusBadge(doc, rawStatus));

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

          // A-23: tags are labels only (never path components) — the row
          // renders the task-view tags verbatim and the editor manages them.
          var tagWrap = rowTagsEl(job, jobId);
          if (tagWrap) row.appendChild(tagWrap);

          appendRowActions(row, job, jobId, rawStatus);

          track(row, "click", function () { onRowClick(jobId); });
          track(row, "keydown", function (ev) {
            if (!ev || (ev.key !== "Enter" && ev.key !== " ")) return;
            // Keyboard activation of a nested row action must not also
            // select the row (the button has its own click handler).
            if (ev.target && ev.target !== row) return;
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
        if (counts[k]) parts.push(statusLabel(k) + " " + counts[k]);
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
      head.appendChild(makeStatusBadge(doc, summary.status));
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

    // ------------------------------------- row commands (A-18 / A-19) --

    /** Command method from the INJECTED client.jobCommands only. No fallback
     * to a page global, no direct transport, no second command client. */
    function commandMethod(action) {
      var jc = state.jobCommands;
      if (!jc) return null;
      var name = ACTION_METHOD[action];
      return name && typeof jc[name] === "function" ? jc[name] : null;
    }

    /** State-aware action matrix (mirrors the backend command contracts):
     *   pause    → RUNNING only              (manager.pause_job)
     *   unpause  → PAUSED only               (manager.unpause_job)
     *   continue → FAILED/CANCELLED only     (manager.continue_job, checkpoint;
     *                                          unsupported workflow → 409 shown
     *                                          inline, no client pre-check)
     *   rerun    → any terminal status       (manager.rerun_job, in-place)
     *   cancel   → any non-terminal status   (manager.cancel; PAUSED is active)
     * PAUSED is never terminal, so it keeps cancel and never gets rerun. */
    function actionsForStatus(status) {
      var s = String(status || "").toLowerCase();
      var out = [];
      if (s === "running") out.push("pause");
      if (s === "paused") out.push("unpause");
      if (s === "failed" || s === "cancelled") out.push("continue");
      if (isTerminal(s)) out.push("rerun");
      if (!isTerminal(s)) out.push("cancel");
      return out;
    }

    function actionLabel(action) {
      var key = ACTION_LABEL_KEYS[action];
      return key ? t(key) : String(action || "");
    }

    // ------------------------------------------- batch selection (A-20) --

    /** Batch purge exists only when the injected client exposes purge: the
     * selection checkbox is part of that command surface, not of the list. */
    function canBatchPurge() {
      return !!commandMethod("purge");
    }

    function selectedIds() {
      return Object.keys(state.selection).filter(function (id) {
        return state.selection[id] === true;
      });
    }

    /** One checkbox per rendered row; checked state comes from the module
     * selection map so a list re-render can never drop the selection. The
     * checkbox exists only when the CURRENT scope has a batch command (queue:
     * purge; trash: restore / permanent delete). */
    function appendRowSelect(row, job, jobId) {
      if (!canSelectRows()) return;
      var doc = state.doc;
      var wrap = doc.createElement("label");
      wrap.className = "acp-mod-tasks-select";
      var box = doc.createElement("input");
      box.setAttribute("type", "checkbox");
      box.setAttribute("data-acp-select", jobId);
      box.setAttribute("aria-label", t("next.tasks.select_job"));
      box.setAttribute("data-i18n-aria", "next.tasks.select_job");
      box.checked = state.selection[jobId] === true;
      wrap.appendChild(box);
      // A checkbox click must not also select/open the row.
      track(wrap, "click", function (ev) { if (ev && ev.stopPropagation) ev.stopPropagation(); });
      track(box, "click", function (ev) { if (ev && ev.stopPropagation) ev.stopPropagation(); });
      track(box, "change", function (ev) {
        var checked = !!(ev && ev.target ? ev.target.checked : box.checked);
        onToggleSelection(jobId, checked);
      });
      row.appendChild(wrap);
    }

    function onToggleSelection(jobId, checked) {
      if (state.disposed) return;
      if (checked) state.selection[jobId] = true;
      else delete state.selection[jobId];
      if (state.rowEls) {
        state.rowEls.forEach(function (el) {
          if (el.getAttribute("data-job-id") === String(jobId)) {
            el.setAttribute("data-acp-selected", checked ? "true" : "false");
          }
        });
      }
      renderBatchBar();
    }

    /** Batch bar mirrors the module selection map (count + select-all/clear +
     * the mode's batch commands). Queue mode keeps A-20's batch purge; trash
     * mode offers restore (batch-ops unarchive) and permanent delete (purge
     * behind the trash-delete confirmation dialog). Selection in the trash is
     * restricted to rendered trash rows — it never reaches the queue surface. */
    function renderBatchBar() {
      var host = state.el.batch;
      if (!host || state.disposed) return;
      var ids = state.trash ? selectedTrashIds() : selectedIds();
      if (!ids.length) {
        host.hidden = true;
        host.replaceChildren();
        return;
      }
      var doc = state.doc;
      host.hidden = false;
      host.setAttribute("data-acp-batch-bar", state.trash ? "trash" : "purge");
      var count = textEl(doc, "span", "acp-mod-tasks-batch-count", "next.tasks.batch.selected",
        { n: ids.length });
      var all = textEl(doc, "button", "acp-mod-tasks-batch-btn", "next.tasks.batch.select_all");
      all.setAttribute("type", "button");
      all.setAttribute("data-acp-batch", "select-all");
      track(all, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        selectAllRows();
      });
      var clear = textEl(doc, "button", "acp-mod-tasks-batch-btn", "next.tasks.batch.clear");
      clear.setAttribute("type", "button");
      clear.setAttribute("data-acp-batch", "clear");
      track(clear, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        clearSelection();
      });
      if (state.trash) {
        var restore = textEl(doc, "button", "acp-mod-tasks-batch-btn", "next.tasks.batch.restore");
        restore.setAttribute("type", "button");
        restore.setAttribute("data-acp-batch", "restore");
        restore.disabled = !canRestore() || state.trashInFlight || state.purgeInFlight;
        track(restore, "click", function (ev) {
          if (ev && ev.preventDefault) ev.preventDefault();
          runTrashRestore(selectedTrashIds());
        });
        var trashDelete = textEl(doc, "button",
          "acp-mod-tasks-batch-btn acp-mod-tasks-batch-btn--danger",
          "next.tasks.batch.trash_delete");
        trashDelete.setAttribute("type", "button");
        trashDelete.setAttribute("data-acp-batch", "trash-delete");
        trashDelete.disabled = !canPurgeTrash() || state.trashInFlight || state.purgeInFlight;
        track(trashDelete, "click", function (ev) {
          if (ev && ev.preventDefault) ev.preventDefault();
          openTrashDeleteDialog(selectedTrashIds());
        });
        host.replaceChildren(count, all, clear, restore, trashDelete);
        return;
      }
      var purge = textEl(doc, "button",
        "acp-mod-tasks-batch-btn acp-mod-tasks-batch-btn--danger", "next.tasks.batch.purge");
      purge.setAttribute("type", "button");
      purge.setAttribute("data-acp-batch", "purge");
      purge.disabled = !canBatchPurge() || state.purgeInFlight;
      track(purge, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        openPurgeDialog(selectedIds());
      });
      host.replaceChildren(count, all, clear, purge);
    }

    function selectAllRows() {
      if (state.disposed) return;
      var ids = (state.rows || []).map(function (r) {
        return String((r && (r.id || r.job_id)) || "");
      }).filter(Boolean);
      ids.forEach(function (id) { state.selection[id] = true; });
      syncSelectionBoxes();
      renderBatchBar();
    }

    function clearSelection() {
      state.selection = Object.create(null);
      syncSelectionBoxes();
      renderBatchBar();
    }

    function syncSelectionBoxes() {
      (state.rowEls || []).forEach(function (el) {
        var id = String(el.getAttribute("data-job-id") || "");
        var on = state.selection[id] === true;
        el.setAttribute("data-acp-selected", on ? "true" : "false");
        // The checkbox is the row's first child when batch purge exists.
        var box = (el.children || []).filter(function (c) {
          return String(c.className || "").indexOf("acp-mod-tasks-select") !== -1;
        })[0];
        if (!box) return;
        var input = (box.children || [])[0];
        if (input) input.checked = on;
      });
    }

    function renderBatchMessage() {
      var el = state.el.batchMsg;
      if (!el || state.disposed) return;
      if (!state.batchMsg) {
        el.hidden = true;
        el.textContent = "";
        el.setAttribute("data-acp-batch-tone", "");
        return;
      }
      el.hidden = false;
      el.textContent = t(state.batchMsg.key, state.batchMsg.vars || {});
      el.setAttribute("data-acp-batch-tone", state.batchMsg.tone || "ok");
      el.setAttribute("role", state.batchMsg.tone === "error" ? "alert" : "status");
    }

    // ------------------------------------- row commands (A-18 / A-19) --

    /** Row action cell: one button per state-aware action + the A-20/A-21
     * lifecycle buttons + a live message slot for busy/error text. Without
     * client.jobCommands the cell is rendered as an explicit "unavailable"
     * marker with NO write controls. */
    function appendRowActions(row, job, jobId, rawStatus) {
      var doc = state.doc;
      var actions = doc.createElement("span");
      actions.className = "acp-mod-tasks-row-actions";
      actions.setAttribute("role", "group");
      actions.setAttribute("aria-label", t("next.tasks.actions_label"));
      actions.setAttribute("data-i18n-aria", "next.tasks.actions_label");
      var ui = { row: row, buttons: [], msg: null, group: actions };
      // A job may appear in more than one group (e.g. tag grouping): keep one
      // ui entry per rendered row so busy/error painting covers every clone.
      if (!state.rowUi[jobId]) state.rowUi[jobId] = [];
      state.rowUi[jobId].push(ui);

      if (!state.jobCommands) {
        row.setAttribute("data-acp-actions", "unavailable");
        actions.setAttribute("data-acp-actions", "unavailable");
        row.appendChild(actions);
        return;
      }

      row.setAttribute("data-acp-actions", "available");
      // A-24: a trash row exposes ONLY restore / permanent delete. The status
      // matrix (A-18/A-19), lifecycle commands (A-20/A-21) and the tag editor
      // (A-23) stay out of the recycle bin: an archived task is terminal and
      // its task-scoped commands are not a trash operation.
      if (state.trash) {
        appendTrashRowActions(doc, actions, ui, jobId);
        appendRowMessage(row, actions, ui);
        syncRowActions(jobId);
        return;
      }

      var list = actionsForStatus(rawStatus);
      list.forEach(function (action) {
        var btn = doc.createElement("button");
        btn.setAttribute("type", "button");
        btn.className = "acp-mod-tasks-row-action acp-mod-tasks-row-action--" + action;
        btn.setAttribute("data-acp-action", action);
        var key = ACTION_LABEL_KEYS[action];
        btn.textContent = t(key);
        btn.setAttribute("data-i18n", key);
        btn.setAttribute("title", t(key));
        if (!commandMethod(action)) {
          btn.disabled = true;
          btn.setAttribute("data-acp-action-unavailable", "true");
          btn.setAttribute("title", t("next.tasks.action_unavailable"));
        }
        track(btn, "click", function (ev) { onActionClick(jobId, action, ev); });
        ui.buttons.push(btn);
        actions.appendChild(btn);
      });

      // A-23: per-job tag editor. Only the missing batchOp disables this
      // button (a partial command client keeps everything else usable); the
      // button lives outside data-acp-action/data-acp-lifecycle so the
      // status matrix and lifecycle set stay untouched.
      var tagsBtn = doc.createElement("button");
      tagsBtn.setAttribute("type", "button");
      tagsBtn.className = "acp-mod-tasks-row-action acp-mod-tasks-row-action--tags";
      tagsBtn.setAttribute("data-acp-tags", jobId);
      tagsBtn.textContent = t("next.tasks.tags.manage");
      tagsBtn.setAttribute("data-i18n", "next.tasks.tags.manage");
      tagsBtn.setAttribute("title", t("next.tasks.tags.title"));
      if (!canManageTags()) {
        tagsBtn.disabled = true;
        tagsBtn.setAttribute("data-acp-action-unavailable", "true");
        tagsBtn.setAttribute("title", t("next.tasks.tags.unavailable"));
      }
      track(tagsBtn, "click", function (ev) { onTagsClick(jobId, ev); });
      ui.buttons.push(tagsBtn);
      actions.appendChild(tagsBtn);

      LIFECYCLE_ACTIONS.forEach(function (action) {
        var btn = doc.createElement("button");
        btn.setAttribute("type", "button");
        btn.className = "acp-mod-tasks-row-action acp-mod-tasks-row-action--lifecycle"
          + " acp-mod-tasks-row-action--" + action;
        // Deliberately NOT data-acp-action: the status matrix and the
        // lifecycle commands are separate surfaces (see LIFECYCLE_ACTIONS).
        btn.setAttribute("data-acp-lifecycle", action);
        var key = ACTION_LABEL_KEYS[action];
        btn.textContent = t(key);
        btn.setAttribute("data-i18n", key);
        btn.setAttribute("title", t(LIFECYCLE_TITLE_KEYS[action] || key));
        var unavailable = lifecycleUnavailable(action, job);
        if (unavailable) {
          btn.disabled = true;
          btn.setAttribute("data-acp-action-unavailable", "true");
          btn.setAttribute("title", t(unavailable === "revision"
            ? "next.tasks.dialog.rename_unavailable" : "next.tasks.action_unavailable"));
        }
        track(btn, "click", (function (a) {
          return function (ev) { onLifecycleClick(a, jobId, ev); };
        })(action));
        ui.buttons.push(btn);
        actions.appendChild(btn);
      });

      appendRowMessage(row, actions, ui);
      syncRowActions(jobId);
    }

    /** Live busy/error slot shared by the queue and trash row surfaces. */
    function appendRowMessage(row, actions, ui) {
      var msg = state.doc.createElement("span");
      msg.className = "acp-mod-tasks-row-msg";
      msg.hidden = true;
      msg.setAttribute("role", "status");
      msg.setAttribute("data-acp-action-state", "idle");
      ui.msg = msg;
      row.appendChild(actions);
      row.appendChild(msg);
    }

    /** A-24 trash row commands: restore (batch-ops unarchive) and permanent
     * delete (purge behind the trash-delete confirmation). Each button is
     * disabled when its injected command is missing — never a hidden global.
     * A row the server reports as NOT archived is never operable from the
     * trash (defensive: the view's query is archived-only, so this only fires
     * on server drift). */
    function appendTrashRowActions(doc, actions, ui, jobId) {
      var trashRow = isTrashJob(jobId);
      var restore = doc.createElement("button");
      restore.setAttribute("type", "button");
      restore.className = "acp-mod-tasks-row-action acp-mod-tasks-row-action--restore";
      restore.setAttribute("data-acp-trash-action", "restore");
      restore.textContent = t("next.tasks.trash.restore");
      restore.setAttribute("data-i18n", "next.tasks.trash.restore");
      restore.setAttribute("title", t("next.tasks.trash.restore"));
      if (!canRestore() || !trashRow) {
        restore.disabled = true;
        restore.setAttribute("data-acp-action-unavailable", "true");
        restore.setAttribute("title", t("next.tasks.trash.unavailable"));
      }
      track(restore, "click", function (ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        if (ev && ev.preventDefault) ev.preventDefault();
        runTrashRestore([jobId]);
      });
      ui.buttons.push(restore);
      actions.appendChild(restore);

      var del = doc.createElement("button");
      del.setAttribute("type", "button");
      del.className = "acp-mod-tasks-row-action acp-mod-tasks-row-action--trash-delete";
      del.setAttribute("data-acp-trash-action", "delete");
      del.textContent = t("next.tasks.trash.delete");
      del.setAttribute("data-i18n", "next.tasks.trash.delete");
      del.setAttribute("title", t("next.tasks.dialog.trash_delete_title"));
      del.setAttribute("data-i18n-title", "next.tasks.dialog.trash_delete_title");
      if (!canPurgeTrash() || !trashRow) {
        del.disabled = true;
        del.setAttribute("data-acp-action-unavailable", "true");
        del.setAttribute("title", t("next.tasks.trash.unavailable"));
      }
      track(del, "click", function (ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        if (ev && ev.preventDefault) ev.preventDefault();
        openTrashDeleteDialog([jobId]);
      });
      ui.buttons.push(del);
      actions.appendChild(del);
    }

    /** Lifecycle availability: only the MISSING method (or the missing name
     * revision for rename) disables one button — never the whole surface. */
    function lifecycleUnavailable(action, job) {
      if (!commandMethod(action)) return "method";
      if (action === "rename" && !hasNameRevision(job)) return "revision";
      return "";
    }

    function hasNameRevision(job) {
      return !!job && typeof job.name_revision === "number"
        && isFinite(job.name_revision) && job.name_revision >= 0;
    }

    // -------------------------------- command busy state (A-18…A-21) --

    function rowStatusOf(jobId) {
      var uis = state.rowUi[jobId];
      return uis && uis[0] && uis[0].row
        ? String(uis[0].row.getAttribute("data-acp-status") || "") : "";
    }

    /** Claim the ONE in-flight command slot of a job. Returns the status the
     * row had when the command started ("" is a valid status) or null when a
     * command is already running for that job. */
    function beginBusy(jobId, action) {
      if (state.disposed || !jobId) return null;
      if (state.actions.busy[jobId]) return null;
      var statusAtStart = rowStatusOf(jobId);
      state.actions.busy[jobId] = { action: action, status: statusAtStart };
      delete state.actions.errors[jobId];
      syncRowActions(jobId);
      return statusAtStart;
    }

    function endBusy(jobId) {
      delete state.actions.busy[jobId];
    }

    function failBusy(jobId, action, statusAtStart, message) {
      delete state.actions.busy[jobId];
      state.actions.errors[jobId] = {
        action: action,
        status: statusAtStart,
        message: message,
      };
      syncRowActions(jobId);
    }

    // ------------------------- lifecycle commands (A-20 / A-21) --

    function jobDisplayName(job) {
      return String((job && (job.display_name || job.resolved_name || job.default_name))
        || (job && (job.id || job.job_id)) || "");
    }

    /** Live row lookup: the task-view body is the single source for a job row
     * (so a dialog always reads the CURRENT name_revision, never a stale one). */
    function findJob(jobId) {
      var groups = (state.body && state.body.groups) || [];
      for (var i = 0; i < groups.length; i++) {
        var jobs = (groups[i] && groups[i].jobs) || [];
        for (var j = 0; j < jobs.length; j++) {
          if (String(jobs[j].id || jobs[j].job_id || "") === String(jobId)) return jobs[j];
        }
      }
      return null;
    }

    function findProject(projectId) {
      var id = String(projectId == null ? "" : projectId);
      if (!id) return null;
      var projects = state.projects || [];
      for (var i = 0; i < projects.length; i++) {
        if (String(projects[i].project_id) === id) return projects[i];
      }
      return null;
    }

    function walkEl(el, out) {
      out = out || [];
      ((el && el.children) || []).forEach(function (c) { out.push(c); walkEl(c, out); });
      return out;
    }

    function findByAttr(root, name, value) {
      var all = walkEl(root);
      for (var i = 0; i < all.length; i++) {
        if (all[i].getAttribute && all[i].getAttribute(name) === value) return all[i];
      }
      return null;
    }

    function onLifecycleClick(action, jobId, ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (ev && ev.preventDefault) ev.preventDefault();
      if (state.disposed || !commandMethod(action)) return;
      if (state.actions.busy[jobId]) return; // one command per row at a time
      if (action === "purge") openPurgeDialog([jobId]);
      else if (action === "rename") openRenameDialog(jobId);
      else openProjectDialog(action, jobId);
    }

    // ------------------------------------ lifecycle dialogs (A-20/A-21) --

    function openDialog(cfg) {
      if (state.disposed) return;
      state.dialog = Object.assign({
        busy: false,
        error: "",
        value: "",
        projectId: "",
        deleteData: false,
      }, cfg || {});
      renderDialog();
    }

    function closeDialog() {
      state.dialog = null;
      var host = state.el.modal;
      if (!host) return;
      host.hidden = true;
      host.setAttribute("data-acp-dialog", "");
      host.setAttribute("data-acp-dialog-state", "closed");
      host.replaceChildren();
    }

    /** A-20: per-job / batch purge is ALWAYS confirmed in this dialog — the
     * row button (or the batch bar button) only opens it. */
    function openPurgeDialog(jobIds) {
      if (state.disposed || !commandMethod("purge")) return;
      if (state.purgeInFlight) return;
      var ids = (jobIds || []).map(String).filter(Boolean);
      if (!ids.length) return;
      // Never open a second confirmation on a row whose command is in flight.
      for (var i = 0; i < ids.length; i++) {
        if (state.actions.busy[ids[i]]) return;
      }
      // A new confirmation supersedes the previous report summary.
      state.batchMsg = null;
      renderBatchMessage();
      var name = "";
      if (ids.length === 1) {
        var job = findJob(ids[0]);
        name = job ? jobDisplayName(job) : ids[0];
      }
      openDialog({
        kind: "purge",
        jobIds: ids,
        jobId: ids.length === 1 ? ids[0] : null,
        name: name,
        deleteData: false,
      });
    }

    function openRenameDialog(jobId) {
      if (state.disposed || !commandMethod("rename")) return;
      var job = findJob(jobId);
      var current = job && typeof job.custom_name === "string" ? job.custom_name : "";
      openDialog({
        kind: "rename",
        jobId: String(jobId),
        revision: job && hasNameRevision(job) ? job.name_revision : null,
        currentCustomName: current,
        jobName: job ? jobDisplayName(job) : String(jobId),
        value: current,
      });
    }

    function openProjectDialog(action, jobId) {
      if (state.disposed || !commandMethod(action)) return;
      var job = findJob(jobId);
      var projects = state.projects || [];
      openDialog({
        kind: action,
        jobId: String(jobId),
        jobName: job ? jobDisplayName(job) : String(jobId),
        projectId: projects.length ? String(projects[0].project_id) : "",
      });
    }

    function dialogTitleKey(kind) {
      return DIALOG_TITLE_KEYS[kind] || "";
    }

    function normalizeName(raw) {
      return String(raw == null ? "" : raw).trim();
    }

    function renameValueInvalid(value) {
      if (value.length > 200) return true;
      return /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\n\r]/.test(value);
    }

    /** Rename conflicts arrive as 409 {detail:{error,name_revision_conflict,
     * current}} (v2 PATCH). Typed, so the dialog can adopt the server
     * revision and retry without a second local revision store. */
    function renameConflict(err) {
      var detail = err && err.body && err.body.detail;
      return !!(detail && typeof detail === "object"
        && (detail.error === "name_revision_conflict" || detail.current));
    }

    function dialogCanConfirm(d) {
      if (!d) return false;
      if (d.kind === "rename") {
        var value = normalizeName(d.value);
        if (renameValueInvalid(value)) return false;
        return value !== String(d.currentCustomName || "");
      }
      if (d.kind === "project-rename") {
        var projectName = normalizeName(d.value);
        // Non-empty and actually changed: an unchanged name is not saveable
        // (the server would accept it as a no-op anyway).
        return projectName !== "" && projectName !== String(d.currentName || "");
      }
      if (d.kind === "project-delete") return true;
      if (d.kind === "move" || d.kind === "clone") return !!findProject(d.projectId);
      if (d.kind === "purge") return (d.jobIds || []).length > 0;
      // A-24: the permanent-delete confirmation exists only inside the trash
      // scope and needs at least one trash row.
      if (d.kind === "trash-delete") return state.trash && (d.jobIds || []).length > 0;
      return true;
    }

    function renderDialog() {
      var host = state.el.modal;
      if (!host || state.disposed) return;
      var d = state.dialog;
      if (!d) { closeDialog(); return; }
      var doc = state.doc;
      var busy = !!d.busy;
      var closeOnly = !!CLOSE_ONLY_DIALOGS[d.kind];
      var refs = { confirm: null, hint: null };
      host.hidden = false;
      host.setAttribute("data-acp-dialog", d.kind);
      host.setAttribute("data-acp-dialog-state", busy ? "busy" : (d.error ? "error" : "open"));

      var card = doc.createElement("div");
      card.className = "acp-mod-tasks-modal-card";
      card.setAttribute("role", "dialog");
      card.setAttribute("aria-modal", "true");
      card.setAttribute("aria-label", t(dialogTitleKey(d.kind)));
      card.setAttribute("tabindex", "-1");
      card.setAttribute("data-acp-dialog-card", d.kind);
      card.appendChild(textEl(doc, "div", "acp-mod-tasks-modal-title", dialogTitleKey(d.kind)));

      var body = doc.createElement("div");
      body.className = "acp-mod-tasks-modal-body";
      if (d.kind === "purge") appendPurgeBody(doc, body, d);
      else if (d.kind === "rename") appendRenameBody(doc, body, d, refs);
      else if (d.kind === "projects") appendProjectsBody(doc, body, d);
      else if (d.kind === "project-rename") appendProjectRenameBody(doc, body, d, refs);
      else if (d.kind === "project-delete") appendProjectDeleteBody(doc, body, d);
      else if (d.kind === "tags") appendTagsBody(doc, body, d);
      else if (d.kind === "trash-delete") appendTrashDeleteBody(doc, body, d);
      else appendProjectBody(doc, body, d);
      card.appendChild(body);

      if (d.error) {
        var errorEl = doc.createElement("div");
        errorEl.className = "acp-mod-tasks-modal-error";
        errorEl.setAttribute("role", "alert");
        errorEl.setAttribute("data-acp-dialog-error", "");
        errorEl.textContent = String(d.error);
        card.appendChild(errorEl);
      }

      // Success line for project CRUD (create/rename/delete return the user
      // to the manager list). role=status, never an alert.
      if (d.notice) {
        var noticeEl = doc.createElement("div");
        noticeEl.className = "acp-mod-tasks-modal-notice";
        noticeEl.setAttribute("role", "status");
        noticeEl.setAttribute("data-acp-dialog-notice", "");
        noticeEl.textContent = String(d.notice);
        card.appendChild(noticeEl);
      }

      var actions = doc.createElement("div");
      actions.className = "acp-mod-tasks-modal-actions";
      var cancel = textEl(doc, "button", "acp-mod-tasks-modal-cancel",
        closeOnly ? "next.tasks.dialog.close" : "next.tasks.dialog.cancel");
      cancel.setAttribute("type", "button");
      cancel.setAttribute("data-acp-dialog-cancel", d.kind);
      track(cancel, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        closeDialog();
      });
      actions.appendChild(cancel);
      if (!closeOnly) {
        var confirm = textEl(doc, "button", "acp-mod-tasks-modal-confirm", DIALOG_CONFIRM_KEYS[d.kind]);
        confirm.setAttribute("type", "button");
        confirm.setAttribute("data-acp-dialog-confirm", d.kind);
        if (d.kind === "purge" || d.kind === "project-delete" || d.kind === "trash-delete") {
          confirm.className = "acp-mod-tasks-modal-confirm is-danger";
        }
        confirm.disabled = busy || !dialogCanConfirm(d);
        track(confirm, "click", function (ev) {
          if (ev && ev.preventDefault) ev.preventDefault();
          onDialogConfirm();
        });
        refs.confirm = confirm;
        actions.appendChild(confirm);
      }
      card.appendChild(actions);

      track(card, "keydown", function (ev) {
        if (!ev || ev.key !== "Escape") return;
        if (ev.preventDefault) ev.preventDefault();
        closeDialog();
      });

      host.replaceChildren(card);
      if (d.kind === "rename" && refs.hint) {
        // Validation hint starts hidden unless the prefilled value is invalid.
        if (renameValueInvalid(normalizeName(d.value))) {
          refs.hint.hidden = false;
          refs.hint.textContent = t("next.tasks.dialog.rename_invalid");
        }
      }
      if (!busy && typeof card.focus === "function") card.focus();
      if (!busy && d.kind === "rename") {
        var input = findByAttr(card, "data-acp-dialog-input", "");
        if (input && typeof input.focus === "function") input.focus();
        if (input && typeof input.select === "function") input.select();
      }
      if (!busy && d.kind === "project-rename") {
        var pInput = findByAttr(card, "data-acp-project-input", "");
        if (pInput && typeof pInput.focus === "function") pInput.focus();
        if (pInput && typeof pInput.select === "function") pInput.select();
      }
      if (!busy && d.kind === "projects") {
        var newInput = findByAttr(card, "data-acp-project-new", "");
        if (newInput && typeof newInput.focus === "function") newInput.focus();
      }
      if (!busy && d.kind === "tags") {
        var tagInput = findByAttr(card, "data-acp-tag-input", "");
        if (tagInput && typeof tagInput.focus === "function") tagInput.focus();
      }
    }

    function appendPurgeBody(doc, body, d) {
      var ids = d.jobIds || [];
      var line = doc.createElement("div");
      line.className = "acp-mod-tasks-modal-text";
      if (ids.length === 1) {
        line.textContent = t("next.tasks.dialog.purge_single", { name: d.name || ids[0] });
        line.setAttribute("data-i18n", "next.tasks.dialog.purge_single");
      } else {
        line.textContent = t("next.tasks.dialog.purge_batch", { count: ids.length });
        line.setAttribute("data-i18n", "next.tasks.dialog.purge_batch");
      }
      body.appendChild(line);
      body.appendChild(
        textEl(doc, "div", "acp-mod-tasks-modal-warning", "next.tasks.dialog.purge_prompt"));

      var label = doc.createElement("label");
      label.className = "acp-mod-tasks-modal-check";
      var box = doc.createElement("input");
      box.setAttribute("type", "checkbox");
      box.setAttribute("data-acp-dialog-delete-data", "");
      box.checked = d.deleteData === true;
      track(box, "change", function (ev) {
        if (!state.dialog) return;
        state.dialog.deleteData = !!(ev && ev.target ? ev.target.checked : box.checked);
      });
      label.appendChild(box);
      label.appendChild(textEl(doc, "span", "", "next.tasks.dialog.delete_data"));
      body.appendChild(label);
    }

    /** A-24 permanent delete of trashed (archived) tasks: the second
     * confirmation names the purge_cascade semantics and repeats that the
     * server cannot restore the records afterwards. delete_data stays opt-in. */
    function appendTrashDeleteBody(doc, body, d) {
      var ids = d.jobIds || [];
      var line = doc.createElement("div");
      line.className = "acp-mod-tasks-modal-text";
      if (ids.length === 1) {
        line.textContent = t("next.tasks.dialog.trash_delete_single", { name: d.name || ids[0] });
        line.setAttribute("data-i18n", "next.tasks.dialog.trash_delete_single");
      } else {
        line.textContent = t("next.tasks.dialog.trash_delete_batch", { count: ids.length });
        line.setAttribute("data-i18n", "next.tasks.dialog.trash_delete_batch");
      }
      body.appendChild(line);
      body.appendChild(
        textEl(doc, "div", "acp-mod-tasks-modal-warning",
          "next.tasks.dialog.trash_delete_prompt"));

      var label = doc.createElement("label");
      label.className = "acp-mod-tasks-modal-check";
      var box = doc.createElement("input");
      box.setAttribute("type", "checkbox");
      box.setAttribute("data-acp-dialog-delete-data", "");
      box.checked = d.deleteData === true;
      track(box, "change", function (ev) {
        if (!state.dialog) return;
        state.dialog.deleteData = !!(ev && ev.target ? ev.target.checked : box.checked);
      });
      label.appendChild(box);
      label.appendChild(textEl(doc, "span", "", "next.tasks.dialog.delete_data"));
      body.appendChild(label);
    }

    function appendRenameBody(doc, body, d, refs) {
      body.appendChild(
        textEl(doc, "label", "acp-mod-tasks-modal-label", "next.tasks.dialog.rename_label"));
      var input = doc.createElement("input");
      input.className = "acp-mod-tasks-modal-input";
      input.setAttribute("type", "text");
      input.setAttribute("maxlength", "200");
      input.setAttribute("data-acp-dialog-input", "");
      input.setAttribute("placeholder", t("next.tasks.dialog.rename_placeholder"));
      input.setAttribute("data-i18n-placeholder", "next.tasks.dialog.rename_placeholder");
      input.value = String(d.value == null ? "" : d.value);
      var hint = doc.createElement("div");
      hint.className = "acp-mod-tasks-modal-hint";
      hint.hidden = true;
      refs.hint = hint;
      track(input, "input", function (ev) {
        if (!state.dialog) return;
        state.dialog.value = ev && ev.target ? String(ev.target.value) : String(input.value);
        syncRenameHint(refs);
      });
      body.appendChild(input);
      body.appendChild(hint);
    }

    function syncRenameHint(refs) {
      var d = state.dialog;
      if (!d || d.kind !== "rename" || !refs) return;
      var invalid = renameValueInvalid(normalizeName(d.value));
      if (refs.hint) {
        refs.hint.hidden = !invalid;
        refs.hint.textContent = invalid ? t("next.tasks.dialog.rename_invalid") : "";
      }
      if (refs.confirm) refs.confirm.disabled = !!d.busy || !dialogCanConfirm(d);
    }

    function appendProjectBody(doc, body, d) {
      var text = doc.createElement("div");
      text.className = "acp-mod-tasks-modal-text";
      text.textContent = String(d.jobName || d.jobId || "");
      body.appendChild(text);
      body.appendChild(
        textEl(doc, "label", "acp-mod-tasks-modal-label", "next.tasks.dialog.project_label"));
      var projects = state.projects || [];
      var select = doc.createElement("select");
      select.className = "acp-mod-tasks-modal-select";
      select.setAttribute("data-acp-dialog-project", "");
      if (!projects.length) {
        var empty = textEl(doc, "option", "", "next.tasks.dialog.no_projects");
        empty.setAttribute("value", "");
        select.appendChild(empty);
        select.disabled = true;
      } else {
        projects.forEach(function (p) {
          var option = doc.createElement("option");
          option.setAttribute("value", String(p.project_id));
          option.textContent = String(p.name || p.project_id);
          select.appendChild(option);
        });
        if (!d.projectId) d.projectId = String(projects[0].project_id);
        select.value = String(d.projectId);
      }
      track(select, "change", function (ev) {
        if (!state.dialog) return;
        state.dialog.projectId = ev && ev.target
          ? String(ev.target.value) : String(select.value);
        var confirm = state.el.modal
          ? findByAttr(state.el.modal, "data-acp-dialog-confirm", state.dialog.kind) : null;
        if (confirm) confirm.disabled = !!state.dialog.busy || !dialogCanConfirm(state.dialog);
      });
      body.appendChild(select);
    }

    // ------------------------------ project dialogs (A-22) --

    /** Manager list: every project from the SAME GET /projects source the
     * toolbar select uses, plus a create row and per-project rename/delete
     * entries. Rows/actions are disabled while a mutation is in flight. */
    function appendProjectsBody(doc, body, d) {
      var projects = state.projects || [];
      var createRow = doc.createElement("div");
      createRow.className = "acp-mod-tasks-modal-createrow";
      var input = doc.createElement("input");
      input.className = "acp-mod-tasks-modal-input";
      input.setAttribute("type", "text");
      input.setAttribute("maxlength", "200");
      input.setAttribute("data-acp-project-new", "");
      input.setAttribute("placeholder", t("next.tasks.projects.new_placeholder"));
      input.setAttribute("data-i18n-placeholder", "next.tasks.projects.new_placeholder");
      input.setAttribute("aria-label", t("next.tasks.projects.new_placeholder"));
      input.value = String(d.value == null ? "" : d.value);
      input.disabled = !!d.busy;
      var create = textEl(doc, "button", "acp-mod-tasks-modal-confirm",
        "next.tasks.projects.create");
      create.setAttribute("type", "button");
      create.setAttribute("data-acp-project-create", "");
      create.disabled = !!d.busy || !normalizeName(d.value);
      track(input, "input", function (ev) {
        if (!state.dialog || state.dialog.kind !== "projects") return;
        state.dialog.value = ev && ev.target ? String(ev.target.value) : String(input.value);
        create.disabled = !!state.dialog.busy || !normalizeName(state.dialog.value);
      });
      track(create, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        submitProjectCreate();
      });
      createRow.appendChild(input);
      createRow.appendChild(create);
      body.appendChild(createRow);

      if (!projects.length) {
        body.appendChild(
          textEl(doc, "div", "acp-mod-tasks-modal-hint", "next.tasks.projects.empty"));
        return;
      }
      var list = doc.createElement("div");
      list.className = "acp-mod-tasks-project-list";
      projects.forEach(function (project) {
        var pid = String(project.project_id);
        var row = doc.createElement("div");
        row.className = "acp-mod-tasks-project-row";
        row.setAttribute("data-acp-project-row", pid);
        var name = doc.createElement("span");
        name.className = "acp-mod-tasks-project-name";
        name.textContent = projectLabel(project);
        row.appendChild(name);
        var rename = textEl(doc, "button", "acp-mod-tasks-project-btn",
          "next.tasks.projects.rename");
        rename.setAttribute("type", "button");
        rename.setAttribute("data-acp-project-rename", pid);
        rename.disabled = !!d.busy;
        track(rename, "click", function (ev) {
          if (ev && ev.preventDefault) ev.preventDefault();
          openProjectRenameDialog(pid);
        });
        var del = textEl(doc,
          "button", "acp-mod-tasks-project-btn acp-mod-tasks-project-btn--danger",
          "next.tasks.projects.delete");
        del.setAttribute("type", "button");
        del.setAttribute("data-acp-project-delete", pid);
        del.disabled = !!d.busy;
        track(del, "click", function (ev) {
          if (ev && ev.preventDefault) ev.preventDefault();
          openProjectDeleteDialog(pid);
        });
        row.appendChild(rename);
        row.appendChild(del);
        list.appendChild(row);
      });
      body.appendChild(list);
    }

    function appendProjectRenameBody(doc, body, d, refs) {
      var text = doc.createElement("div");
      text.className = "acp-mod-tasks-modal-text";
      text.textContent = String(d.currentName || d.projectId || "");
      body.appendChild(text);
      body.appendChild(
        textEl(doc, "label", "acp-mod-tasks-modal-label", "next.tasks.projects.name_label"));
      var input = doc.createElement("input");
      input.className = "acp-mod-tasks-modal-input";
      input.setAttribute("type", "text");
      input.setAttribute("maxlength", "200");
      input.setAttribute("data-acp-project-input", "");
      input.value = String(d.value == null ? "" : d.value);
      var hint = doc.createElement("div");
      hint.className = "acp-mod-tasks-modal-hint";
      hint.hidden = true;
      hint.setAttribute("data-acp-project-hint", "");
      refs.hint = hint;
      track(input, "input", function (ev) {
        if (!state.dialog || state.dialog.kind !== "project-rename") return;
        state.dialog.value = ev && ev.target ? String(ev.target.value) : String(input.value);
        syncProjectRenameHint(refs);
      });
      body.appendChild(input);
      body.appendChild(hint);
    }

    function syncProjectRenameHint(refs) {
      var d = state.dialog;
      if (!d || d.kind !== "project-rename" || !refs) return;
      var invalid = normalizeName(d.value) === "";
      if (refs.hint) {
        refs.hint.hidden = !invalid;
        refs.hint.textContent = invalid ? t("next.tasks.projects.name_invalid") : "";
      }
      if (refs.confirm) refs.confirm.disabled = !!d.busy || !dialogCanConfirm(d);
    }

    /** A-22 dependency warning: the server-side count for the project is
     * rendered before the second confirmation; a probe failure degrades to
     * "server will validate" instead of blocking the delete. */
    function appendProjectDeleteBody(doc, body, d) {
      var text = doc.createElement("div");
      text.className = "acp-mod-tasks-modal-text";
      text.textContent = String(d.name || d.projectId || "");
      body.appendChild(text);
      var info = d.deps || { state: "loading" };
      var deps = doc.createElement("div");
      deps.className = "acp-mod-tasks-modal-deps";
      deps.setAttribute("data-acp-project-deps", info.state || "loading");
      deps.setAttribute("role", "status");
      if (info.state === "ready") {
        var total = Number(info.total) || 0;
        var active = Number(info.active) || 0;
        if (active > 0) {
          var activeKey = info.truncated
            ? "next.tasks.projects.deps_active_more" : "next.tasks.projects.deps_active";
          deps.textContent = t(activeKey, { n: total, active: active });
          deps.setAttribute("data-i18n", activeKey);
        } else if (info.truncated) {
          deps.textContent = t("next.tasks.projects.deps_more", { n: total });
          deps.setAttribute("data-i18n", "next.tasks.projects.deps_more");
        } else {
          deps.textContent = t("next.tasks.projects.deps", { n: total });
          deps.setAttribute("data-i18n", "next.tasks.projects.deps");
        }
      } else if (info.state === "error") {
        deps.textContent = t("next.tasks.projects.deps_error", { message: info.message });
        deps.setAttribute("data-i18n", "next.tasks.projects.deps_error");
      } else if (info.state === "unknown") {
        deps.textContent = t("next.tasks.projects.deps_unknown");
        deps.setAttribute("data-i18n", "next.tasks.projects.deps_unknown");
      } else if (info.state === "loading") {
        deps.textContent = t("next.tasks.projects.deps_loading");
        deps.setAttribute("data-i18n", "next.tasks.projects.deps_loading");
      }
      body.appendChild(deps);
      body.appendChild(
        textEl(doc, "div", "acp-mod-tasks-modal-warning",
          "next.tasks.projects.delete_warning"));
    }

    // -------------------------------- tag editor dialog (A-23) --

    function appendTagsBody(doc, body, d) {
      var tags = (d.tags || []).slice();
      var list = doc.createElement("div");
      list.className = "acp-mod-tasks-tag-list";
      list.setAttribute("data-acp-tag-list", "");
      if (!tags.length) {
        list.appendChild(textEl(doc, "div", "acp-mod-tasks-modal-hint", "next.tasks.tags.empty"));
      } else {
        tags.forEach(function (tag) {
          var chip = doc.createElement("span");
          chip.className = "acp-mod-tasks-tag";
          chip.setAttribute("data-acp-tag-chip", tag);
          var label = doc.createElement("span");
          label.className = "acp-mod-tasks-tag-label";
          label.textContent = tag;
          var remove = doc.createElement("button");
          remove.setAttribute("type", "button");
          remove.className = "acp-mod-tasks-tag-remove";
          remove.setAttribute("data-acp-tag-remove", tag);
          remove.setAttribute("aria-label", t("next.tasks.tags.remove", { tag: tag }));
          remove.setAttribute("data-i18n-aria", "next.tasks.tags.remove");
          remove.textContent = "×";
          remove.disabled = !!d.busy;
          track(remove, "click", function (ev) {
            if (ev && ev.preventDefault) ev.preventDefault();
            submitTagChange("remove_tags", tag);
          });
          chip.appendChild(label);
          chip.appendChild(remove);
          list.appendChild(chip);
        });
      }
      body.appendChild(list);

      var addRow = doc.createElement("div");
      addRow.className = "acp-mod-tasks-modal-createrow";
      var input = doc.createElement("input");
      input.className = "acp-mod-tasks-modal-input";
      input.setAttribute("type", "text");
      // Server rule: each tag is ≤ 32 chars (POST /tasks/batch-ops).
      input.setAttribute("maxlength", "32");
      input.setAttribute("data-acp-tag-input", "");
      input.setAttribute("placeholder", t("next.tasks.tags.placeholder"));
      input.setAttribute("data-i18n-placeholder", "next.tasks.tags.placeholder");
      input.setAttribute("aria-label", t("next.tasks.tags.placeholder"));
      input.value = String(d.value == null ? "" : d.value);
      input.disabled = !!d.busy;
      var add = textEl(doc, "button", "acp-mod-tasks-modal-confirm", "next.tasks.tags.add");
      add.setAttribute("type", "button");
      add.setAttribute("data-acp-tag-add", "");
      add.disabled = !!d.busy || !normalizeName(d.value);
      track(input, "input", function (ev) {
        if (!state.dialog || state.dialog.kind !== "tags") return;
        state.dialog.value = ev && ev.target ? String(ev.target.value) : String(input.value);
        add.disabled = !!state.dialog.busy || !normalizeName(state.dialog.value);
      });
      track(input, "keydown", function (ev) {
        if (!ev || ev.key !== "Enter") return;
        if (ev.preventDefault) ev.preventDefault();
        var text = state.dialog && state.dialog.kind === "tags" ? state.dialog.value : "";
        submitTagChange("add_tags", normalizeName(text));
      });
      track(add, "click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        var text = state.dialog && state.dialog.kind === "tags" ? state.dialog.value : "";
        submitTagChange("add_tags", normalizeName(text));
      });
      addRow.appendChild(input);
      addRow.appendChild(add);
      body.appendChild(addRow);
    }

    function onDialogConfirm() {
      var d = state.dialog;
      if (!d || d.busy || state.disposed) return;
      if (d.kind === "purge") submitPurge();
      else if (d.kind === "trash-delete") submitTrashDelete();
      else if (d.kind === "rename") submitRename();
      else if (d.kind === "project-rename") submitProjectRename();
      else if (d.kind === "project-delete") submitProjectDelete();
      else if (d.kind === "move" || d.kind === "clone") submitProjectAction();
    }

    /** A-20 submit: ONE injected client.jobCommands.purge({jobIds, deleteData})
     * call for the confirmed selection. The server's per-job report decides
     * success/failure (purge_cascade semantics stay server-side). */
    function submitPurge() {
      var d = state.dialog;
      if (!d || d.kind !== "purge" || d.busy || state.disposed) return;
      if (state.purgeInFlight) return;
      var purge = commandMethod("purge");
      if (!purge) return;
      var ids = (d.jobIds || []).map(String).filter(Boolean);
      if (!ids.length) return;
      for (var i = 0; i < ids.length; i++) {
        if (state.actions.busy[ids[i]]) return; // never a second command per row
      }
      var gen = state.generation;
      var deleteData = d.deleteData === true;
      d.error = "";
      d.busy = true;
      state.purgeInFlight = true;
      ids.forEach(function (id) { beginBusy(id, "purge"); });
      renderDialog();
      renderBatchBar();
      var promise;
      try {
        promise = purge.call(state.jobCommands, { jobIds: ids, deleteData: deleteData });
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function (resp) {
        if (gen !== state.generation || state.disposed) return;
        state.purgeInFlight = false;
        ids.forEach(endBusy);
        closeDialog();
        applyPurgeResult(ids, resp);
        renderBatchBar();
        refresh();
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        state.purgeInFlight = false;
        var message = actionErrorText(err);
        ids.forEach(function (id) {
          failBusy(id, "purge", rowStatusOf(id), message);
        });
        if (state.dialog && state.dialog.kind === "purge") {
          state.dialog.busy = false;
          state.dialog.error = t("next.tasks.purge_error", { message: message });
          renderDialog();
        }
        renderBatchBar();
        // A failed purge (HTTP/transport) never refreshes the list — the same
        // rule as A-18/A-19; the dialog stays open for an explicit retry.
      });
    }

    /** Render the server's per-job purge report: the error TEXT is the
     * authority (e.g. "job is active (status=running); use force_cancel…"),
     * never a duplicated local terminal/active matrix. */
    function applyPurgeResult(ids, resp) {
      var entries = Array.isArray(resp) ? resp
        : (resp && Array.isArray(resp.results) ? resp.results : null);
      var okIds = [];
      var failed = [];
      if (entries) {
        entries.forEach(function (entry) {
          if (!entry || typeof entry !== "object") return;
          var id = String(entry.job_id || entry.id || "");
          if (!id || ids.indexOf(id) === -1) return;
          if (entry.ok === true) { okIds.push(id); return; }
          failed.push({
            jobId: id,
            message: String(entry.error || entry.message
              || t("next.tasks.action_unknown_error")),
          });
        });
        if (!entries.length) okIds = ids.slice();
      } else {
        // Unknown response shape: the command itself succeeded (tolerant
        // parser, same rule as the legacy purge modal).
        okIds = ids.slice();
      }
      okIds.forEach(function (id) {
        delete state.selection[id];
        delete state.actions.errors[id];
        syncRowActions(id);
      });
      failed.forEach(function (f) {
        delete state.selection[f.jobId];
        state.actions.errors[f.jobId] = {
          action: "purge",
          status: rowStatusOf(f.jobId),
          message: f.message,
        };
        syncRowActions(f.jobId);
      });
      var isBatch = ids.length > 1;
      if (okIds.length) {
        state.batchMsg = {
          key: failed.length ? "next.tasks.purge_report" : "next.tasks.purge_report_ok",
          vars: failed.length
            ? { ok: okIds.length, fail: failed.length }
            : { count: okIds.length },
          tone: failed.length ? "error" : "ok",
        };
      } else if (failed.length && isBatch) {
        state.batchMsg = {
          key: "next.tasks.purge_report",
          vars: { ok: 0, fail: failed.length },
          tone: "error",
        };
      }
      if (okIds.length) {
        var selected = state.selectedJobId ? String(state.selectedJobId) : "";
        if (selected && okIds.indexOf(selected) !== -1) {
          // The selected task no longer exists: drop local selection only.
          state.selectedJobId = null;
          state.detail = null;
          if (state.session && typeof state.session.clear === "function") state.session.clear();
          renderDetailNone();
          markSelectedRows();
        }
      }
      renderBatchMessage();
    }

    // --------------------------------------- recycle bin ops (A-24) --
    //
    // Restore = the EXISTING batch-ops "unarchive" op (the inverse of the
    // legacy queue's archive) through the injected client.jobCommands.
    // Permanent delete = the EXISTING purge command (purge_cascade) behind the
    // trash-delete confirmation dialog. Neither path builds a URL, calls
    // DELETE, or touches a second cache.

    /** batch-ops report → { okIds, failed:[{jobId, message}] }. A missing or
     * unknown report shape is tolerated as success (same tolerant parser rule
     * as A-23's single-task batchOpOutcome / the legacy _batchOp). */
    function batchOpReport(resp, ids) {
      var wanted = (ids || []).map(String);
      var results = resp && Array.isArray(resp.results) ? resp.results : null;
      if (!results) return { okIds: wanted.slice(), failed: [] };
      var okIds = [];
      var failed = [];
      results.forEach(function (entry) {
        if (!entry || typeof entry !== "object") return;
        var tid = String(entry.task_id || entry.id || "");
        if (!tid || wanted.indexOf(tid) === -1) return;
        if (entry.ok === false || (entry.ok !== true && entry.error)) {
          failed.push({
            jobId: tid,
            message: String(entry.error || entry.message
              || t("next.tasks.action_unknown_error")),
          });
        } else {
          okIds.push(tid);
        }
      });
      if (!results.length) return { okIds: wanted.slice(), failed: [] };
      return { okIds: okIds, failed: failed };
    }

    /** Restore one/several trash rows. Targets are filtered through isTrashJob
     * (only rendered archived rows) and the call is refused outright if any
     * requested id is not a trash row — the trash surface never touches the
     * queue's tasks. */
    function runTrashRestore(jobIds) {
      if (state.disposed || !state.trash) return;
      var batchOp = batchOpMethod();
      if (!batchOp) return;
      if (state.trashInFlight || state.purgeInFlight) return;
      var requested = (jobIds || []).map(String).filter(Boolean);
      var ids = requested.filter(isTrashJob);
      if (!ids.length || ids.length !== requested.length) return;
      for (var i = 0; i < ids.length; i++) {
        if (state.actions.busy[ids[i]]) return; // one command per row
      }
      var gen = state.generation;
      var statuses = Object.create(null);
      state.trashInFlight = true;
      var claimed = [];
      for (var j = 0; j < ids.length; j++) {
        var statusAtStart = beginBusy(ids[j], "restore");
        if (statusAtStart === null) {
          claimed.forEach(endBusy);
          state.trashInFlight = false;
          return;
        }
        statuses[ids[j]] = statusAtStart;
        claimed.push(ids[j]);
      }
      state.batchMsg = null;
      renderBatchMessage();
      renderBatchBar();
      var promise;
      try {
        promise = batchOp.call(state.jobCommands, {
          taskIds: ids,
          op: "unarchive",
          payload: {},
        });
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function (resp) {
        if (gen !== state.generation || state.disposed) return;
        state.trashInFlight = false;
        ids.forEach(endBusy);
        var report = batchOpReport(resp, ids);
        report.okIds.forEach(function (id) {
          delete state.selection[id];
          delete state.actions.errors[id];
          syncRowActions(id);
        });
        report.failed.forEach(function (f) {
          delete state.selection[f.jobId];
          failBusy(f.jobId, "restore", statuses[f.jobId] || "", f.message);
        });
        if (report.okIds.length) {
          state.batchMsg = {
            key: report.failed.length ? "next.tasks.trash.report" : "next.tasks.trash.report_ok",
            vars: report.failed.length
              ? { ok: report.okIds.length, fail: report.failed.length }
              : { count: report.okIds.length },
            tone: report.failed.length ? "error" : "ok",
          };
        } else if (report.failed.length && ids.length > 1) {
          state.batchMsg = {
            key: "next.tasks.trash.report",
            vars: { ok: 0, fail: report.failed.length },
            tone: "error",
          };
        }
        renderBatchMessage();
        renderBatchBar();
        refresh(); // same task-view/store path as every other write
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        state.trashInFlight = false;
        var message = actionErrorText(err);
        ids.forEach(function (id) {
          failBusy(id, "restore", statuses[id] || "", message);
        });
        state.batchMsg = {
          key: "next.tasks.trash.restore_failed",
          vars: { message: message },
          tone: "error",
        };
        renderBatchMessage();
        renderBatchBar();
        // A transport failure never refreshes the list (same rule as A-18/A-19).
      });
    }

    /** A-24 permanent delete: the trash row / batch button only OPENS this
     * confirmation; the second click sends the purge cascade command. */
    function openTrashDeleteDialog(jobIds) {
      if (state.disposed || !state.trash) return;
      if (!canPurgeTrash()) return;
      if (state.trashInFlight || state.purgeInFlight) return;
      var requested = (jobIds || []).map(String).filter(Boolean);
      var ids = requested.filter(isTrashJob);
      if (!ids.length || ids.length !== requested.length) return;
      for (var i = 0; i < ids.length; i++) {
        if (state.actions.busy[ids[i]]) return; // never a second command per row
      }
      state.batchMsg = null;
      renderBatchMessage();
      var name = "";
      if (ids.length === 1) {
        var job = findJob(ids[0]);
        name = job ? jobDisplayName(job) : ids[0];
      }
      openDialog({
        kind: "trash-delete",
        jobIds: ids,
        jobId: ids.length === 1 ? ids[0] : null,
        name: name,
        deleteData: false,
      });
    }

    /** Permanent delete of trashed tasks — the SAME injected purge command
     * A-20 uses ({jobIds, deleteData} → POST /jobs/purge → purge_cascade).
     * The UI only adds the trash confirmation/word choices; no raw DELETE and
     * no client-side cascade are ever issued. */
    function submitTrashDelete() {
      var d = state.dialog;
      if (!d || d.kind !== "trash-delete" || d.busy || state.disposed) return;
      if (!state.trash) { closeDialog(); return; }
      if (state.trashInFlight || state.purgeInFlight) return;
      var purge = commandMethod("purge");
      if (!purge) return;
      var ids = (d.jobIds || []).map(String).filter(Boolean);
      if (!ids.length) return;
      for (var i = 0; i < ids.length; i++) {
        if (state.actions.busy[ids[i]]) return;
      }
      var gen = state.generation;
      var deleteData = d.deleteData === true;
      d.error = "";
      d.busy = true;
      state.trashInFlight = true;
      ids.forEach(function (id) { beginBusy(id, "trash-delete"); });
      renderDialog();
      renderBatchBar();
      var promise;
      try {
        promise = purge.call(state.jobCommands, { jobIds: ids, deleteData: deleteData });
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function (resp) {
        if (gen !== state.generation || state.disposed) return;
        state.trashInFlight = false;
        ids.forEach(endBusy);
        closeDialog();
        applyTrashDeleteResult(ids, resp);
        renderBatchBar();
        refresh();
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        state.trashInFlight = false;
        var message = actionErrorText(err);
        ids.forEach(function (id) {
          failBusy(id, "trash-delete", rowStatusOf(id), message);
        });
        if (state.dialog && state.dialog.kind === "trash-delete") {
          state.dialog.busy = false;
          state.dialog.error = t("next.tasks.trash.delete_failed", { message: message });
          renderDialog();
        }
        renderBatchBar();
        // A failed delete keeps the dialog open and never refreshes the list.
      });
    }

    /** Render the server's per-job purge report for the trash permanent
     * delete. The server's error TEXT stays authoritative (purge_cascade
     * refusals, e.g. an active job), never a duplicated local matrix. */
    function applyTrashDeleteResult(ids, resp) {
      var entries = Array.isArray(resp) ? resp
        : (resp && Array.isArray(resp.results) ? resp.results : null);
      var okIds = [];
      var failed = [];
      if (entries) {
        entries.forEach(function (entry) {
          if (!entry || typeof entry !== "object") return;
          var id = String(entry.job_id || entry.id || "");
          if (!id || ids.indexOf(id) === -1) return;
          if (entry.ok === true) { okIds.push(id); return; }
          failed.push({
            jobId: id,
            message: String(entry.error || entry.message
              || t("next.tasks.action_unknown_error")),
          });
        });
        if (!entries.length) okIds = ids.slice();
      } else {
        okIds = ids.slice(); // tolerant parser, same rule as A-20
      }
      okIds.forEach(function (id) {
        delete state.selection[id];
        delete state.actions.errors[id];
        syncRowActions(id);
      });
      failed.forEach(function (f) {
        delete state.selection[f.jobId];
        state.actions.errors[f.jobId] = {
          action: "trash-delete",
          status: rowStatusOf(f.jobId),
          message: f.message,
        };
        syncRowActions(f.jobId);
      });
      var isBatch = ids.length > 1;
      if (okIds.length) {
        state.batchMsg = {
          key: failed.length
            ? "next.tasks.trash.delete_report" : "next.tasks.trash.delete_report_ok",
          vars: failed.length
            ? { ok: okIds.length, fail: failed.length }
            : { count: okIds.length },
          tone: failed.length ? "error" : "ok",
        };
      } else if (failed.length && isBatch) {
        state.batchMsg = {
          key: "next.tasks.trash.delete_report",
          vars: { ok: 0, fail: failed.length },
          tone: "error",
        };
      }
      if (okIds.length) {
        var selected = state.selectedJobId ? String(state.selectedJobId) : "";
        if (selected && okIds.indexOf(selected) !== -1) {
          // Permanently deleted: the task no longer exists anywhere.
          state.selectedJobId = null;
          state.detail = null;
          if (state.session && typeof state.session.clear === "function") state.session.clear();
          renderDetailNone();
          markSelectedRows();
        }
      }
      renderBatchMessage();
    }

    /** A-21 rename: the row's name_revision is the optimistic lock
     * (expected_name_revision); a 409 conflict adopts the server revision so
     * the retry in the still-open dialog is meaningful. */
    function submitRename() {
      var d = state.dialog;
      if (!d || d.kind !== "rename" || d.busy || state.disposed) return;
      var rename = commandMethod("rename");
      if (!rename) return;
      var jobId = String(d.jobId || "");
      if (!jobId) return;
      var job = findJob(jobId);
      var revision = job && hasNameRevision(job) ? job.name_revision : d.revision;
      if (typeof revision !== "number" || !isFinite(revision)) {
        d.error = t("next.tasks.dialog.rename_unavailable");
        renderDialog();
        return;
      }
      var value = normalizeName(d.value);
      if (renameValueInvalid(value)) {
        d.error = t("next.tasks.dialog.rename_invalid");
        renderDialog();
        return;
      }
      var gen = state.generation;
      var statusAtStart = beginBusy(jobId, "rename");
      if (statusAtStart === null) {
        d.error = t("next.tasks.action_in_flight");
        renderDialog();
        return;
      }
      d.error = "";
      d.busy = true;
      renderDialog();
      var promise;
      try {
        promise = rename.call(state.jobCommands, jobId, value === "" ? null : value, revision);
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function () {
        if (gen !== state.generation || state.disposed) return;
        endBusy(jobId);
        delete state.actions.errors[jobId];
        syncRowActions(jobId);
        closeDialog();
        afterActionSuccess(jobId);
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        var conflict = renameConflict(err);
        var message = conflict
          ? t("next.tasks.dialog.rename_conflict")
          : actionErrorText(err);
        failBusy(jobId, "rename", statusAtStart, message);
        if (conflict) {
          var current = err && err.body && err.body.detail && err.body.detail.current;
          if (state.dialog && current && typeof current.name_revision === "number") {
            state.dialog.revision = current.name_revision;
          }
          // The server did change the name: re-read the list through the
          // owned controller (a failure would otherwise leave a stale row).
          refresh();
        }
        if (state.dialog && state.dialog.kind === "rename") {
          state.dialog.busy = false;
          state.dialog.error = t("next.tasks.action_failed", {
            action: actionLabel("rename"),
            message: message,
          });
          renderDialog();
        }
      });
    }

    /** A-21 move/clone: target chosen from the project list already loaded
     * through the existing GET /projects path (never a constructed path). */
    function submitProjectAction() {
      var d = state.dialog;
      if (!d || (d.kind !== "move" && d.kind !== "clone") || d.busy || state.disposed) return;
      var action = d.kind;
      var method = commandMethod(action);
      if (!method) return;
      var jobId = String(d.jobId || "");
      var project = findProject(d.projectId);
      if (!jobId || !project) {
        d.error = t("next.tasks.dialog.no_projects");
        renderDialog();
        return;
      }
      var projectId = String(project.project_id);
      var gen = state.generation;
      var statusAtStart = beginBusy(jobId, action);
      if (statusAtStart === null) {
        d.error = t("next.tasks.action_in_flight");
        renderDialog();
        return;
      }
      d.error = "";
      d.busy = true;
      renderDialog();
      var promise;
      try {
        promise = method.call(state.jobCommands, jobId, projectId);
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function () {
        if (gen !== state.generation || state.disposed) return;
        endBusy(jobId);
        delete state.actions.errors[jobId];
        syncRowActions(jobId);
        closeDialog();
        afterActionSuccess(jobId);
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        var message = actionErrorText(err);
        failBusy(jobId, action, statusAtStart, message);
        if (state.dialog && (state.dialog.kind === "move" || state.dialog.kind === "clone")) {
          state.dialog.busy = false;
          state.dialog.error = t("next.tasks.action_failed", {
            action: actionLabel(action),
            message: message,
          });
          renderDialog();
        }
      });
    }

    // ------------------------------------------- project CRUD (A-22) --

    /** Project surface rides the SAME injected request seam the toolbar
     * already used for GET /projects (client.request in the mock/legacy
     * clients, client.transport.request in the Shell). No fetch, no second
     * project client, no project path construction. */
    function projectRequest(path, opts) {
      if (client && typeof client.request === "function") return client.request(path, opts);
      if (client && client.transport && typeof client.transport.request === "function") {
        return client.transport.request("/api/v1" + path, opts);
      }
      return Promise.reject(new Error("task-explorer: no request transport available"));
    }

    function canManageProjects() {
      return !!(client && (typeof client.request === "function"
        || (client.transport && typeof client.transport.request === "function")));
    }

    function jsonRequestOptions(method, body) {
      return {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      };
    }

    function projectLabel(project) {
      return String((project && (project.name || project.project_id)) || "");
    }

    /** Toolbar entry state: a client with no request transport cannot list
     * or mutate projects, so the button is disabled with an explicit title
     * (never a silent dead button). */
    function syncProjectManagementAvailability() {
      var btn = state.el.manage;
      if (!btn) return;
      var ok = canManageProjects();
      btn.disabled = !ok;
      btn.setAttribute("data-acp-project-unavailable", ok ? "false" : "true");
      btn.setAttribute("title", t(ok
        ? "next.tasks.projects.title" : "next.tasks.projects.unavailable"));
    }

    function openProjectsDialog(notice) {
      if (state.disposed || !canManageProjects()) return;
      state.dialog = {
        kind: "projects",
        busy: false,
        error: "",
        notice: notice || "",
        value: "",
        projectId: "",
      };
      renderDialog();
    }

    function openProjectRenameDialog(projectId) {
      if (state.disposed || !canManageProjects()) return;
      var project = findProject(projectId);
      if (!project) return;
      var name = projectLabel(project);
      state.dialog = {
        kind: "project-rename",
        projectId: String(projectId),
        currentName: name,
        value: name,
        busy: false,
        error: "",
        notice: "",
      };
      renderDialog();
    }

    /** A-22 delete confirmation: ask the server for the project's dependency
     * count (existing GET /projects/{id}/jobs) BEFORE the second confirmation
     * click. The count is advisory; the DELETE response stays authoritative. */
    function openProjectDeleteDialog(projectId) {
      if (state.disposed || !canManageProjects()) return;
      var project = findProject(projectId);
      if (!project) return;
      var d = {
        kind: "project-delete",
        projectId: String(projectId),
        name: projectLabel(project),
        deps: { state: "loading", total: 0, active: 0, truncated: false, message: "" },
        busy: false,
        error: "",
        notice: "",
      };
      state.dialog = d;
      renderDialog();
      loadProjectDependencies(String(projectId), state.generation);
    }

    function loadProjectDependencies(projectId, gen) {
      var path = "/projects/" + encodeURIComponent(projectId)
        + "/jobs?limit=" + PROJECT_DEPS_LIMIT;
      var promise;
      try {
        promise = projectRequest(path, { method: "GET" });
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function (body) {
        if (gen !== state.generation || state.disposed) return;
        var d = state.dialog;
        if (!d || d.kind !== "project-delete" || String(d.projectId) !== projectId) return;
        var jobs = body && Array.isArray(body.jobs) ? body.jobs : null;
        if (!jobs) {
          d.deps = { state: "unknown", total: 0, active: 0, truncated: false, message: "" };
        } else {
          var active = 0;
          jobs.forEach(function (job) {
            if (job && !isTerminal(job.status)) active += 1;
          });
          d.deps = {
            state: "ready",
            total: jobs.length,
            active: active,
            truncated: jobs.length >= PROJECT_DEPS_LIMIT,
            message: "",
          };
        }
        renderDialog();
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        var d2 = state.dialog;
        if (!d2 || d2.kind !== "project-delete" || String(d2.projectId) !== projectId) return;
        d2.deps = {
          state: "error",
          total: 0,
          active: 0,
          truncated: false,
          message: actionErrorText(err),
        };
        renderDialog();
      });
    }

    /** Create: POST /projects (the legacy create endpoint/body). The server
     * returns the new project; the module then re-reads the project list and
     * switches scope to it through the SAME path the toolbar select uses, so
     * the selector and the task list refresh via the owned list controller. */
    function submitProjectCreate() {
      var d = state.dialog;
      if (!d || d.kind !== "projects" || d.busy || state.disposed) return;
      var name = normalizeName(d.value);
      if (!name) {
        d.error = t("next.tasks.projects.name_invalid");
        renderDialog();
        return;
      }
      var gen = state.generation;
      d.error = "";
      d.busy = true;
      renderDialog();
      var promise;
      try {
        promise = projectRequest("/projects", jsonRequestOptions("POST", { name: name }));
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function (created) {
        if (gen !== state.generation || state.disposed) return;
        // A dialog closed while the request was in flight must not resurrect;
        // the selector is still re-read below (the server did create it).
        var dialogOpen = state.dialog === d;
        d.busy = false;
        var newId = created && created.project_id != null ? String(created.project_id) : "";
        return loadProjects().then(function () {
          if (gen !== state.generation || state.disposed) return;
          if (!dialogOpen) return;
          if (!newId) {
            d.notice = t("next.tasks.projects.created", { name: name });
            d.value = "";
            renderDialog();
            return;
          }
          setFocus({ projectId: newId }, MODULE_ID);
          applyProjectId(newId);
        });
      }, function (err) {
        if (gen !== state.generation || state.disposed || state.dialog !== d) return;
        d.busy = false;
        d.error = t("next.tasks.projects.create_failed", { message: actionErrorText(err) });
        renderDialog();
      });
    }

    /** Rename: PATCH /projects/{id} {name}. The delete/rename dialogs are
     * opened from the manager list; success returns to that list with the
     * re-read project data (same GET /projects source). */
    function submitProjectRename() {
      var d = state.dialog;
      if (!d || d.kind !== "project-rename" || d.busy || state.disposed) return;
      if (!dialogCanConfirm(d)) return;
      var name = normalizeName(d.value);
      var gen = state.generation;
      d.error = "";
      d.busy = true;
      renderDialog();
      var promise;
      try {
        promise = projectRequest("/projects/" + encodeURIComponent(d.projectId),
          jsonRequestOptions("PATCH", { name: name }));
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function () {
        if (gen !== state.generation || state.disposed) return;
        var dialogOpen = state.dialog === d;
        d.busy = false;
        return loadProjects().then(function () {
          if (gen !== state.generation || state.disposed) return;
          if (dialogOpen) openProjectsDialog(t("next.tasks.projects.renamed", { name: name }));
        });
      }, function (err) {
        if (gen !== state.generation || state.disposed || state.dialog !== d) return;
        d.busy = false;
        d.error = t("next.tasks.projects.rename_failed", { message: actionErrorText(err) });
        renderDialog();
      });
    }

    /** Delete: DELETE /projects/{id} (server default delete_data=false → the
     * project's tasks move to the default project; no data destruction here).
     * A server refusal (active jobs / default project) is rendered verbatim
     * and the dialog stays open for an explicit retry. */
    function submitProjectDelete() {
      var d = state.dialog;
      if (!d || d.kind !== "project-delete" || d.busy || state.disposed) return;
      var projectId = String(d.projectId || "");
      var name = String(d.name || projectId);
      if (!projectId) return;
      var gen = state.generation;
      d.error = "";
      d.busy = true;
      renderDialog();
      var promise;
      try {
        promise = projectRequest("/projects/" + encodeURIComponent(projectId),
          { method: "DELETE" });
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function () {
        if (gen !== state.generation || state.disposed) return;
        var dialogOpen = state.dialog === d;
        d.busy = false;
        var wasCurrent = String(state.projectId || "") === projectId;
        return loadProjects().then(function () {
          if (gen !== state.generation || state.disposed) return;
          if (wasCurrent) {
            // The deleted project was the active scope: fall back to "all
            // projects" through the same switch path (focus + generation bump
            // + refresh through the owned list controller).
            setFocus({ projectId: null }, MODULE_ID);
            applyProjectId("");
          } else {
            refresh();
          }
          if (dialogOpen) openProjectsDialog(t("next.tasks.projects.deleted", { name: name }));
        });
      }, function (err) {
        if (gen !== state.generation || state.disposed || state.dialog !== d) return;
        d.busy = false;
        d.error = t("next.tasks.projects.delete_failed", { message: actionErrorText(err) });
        renderDialog();
      });
    }

    // ------------------------------------- tag management (A-23) --

    /** The ONLY job-tag write surface: the injected client.jobCommands
     * .batchOp({taskIds, op, payload}) → POST /api/v2/tasks/batch-ops with the
     * documented add_tags / remove_tags ops. No fetch, no constructed tag
     * endpoint, and tags never become path components. */
    function batchOpMethod() {
      var jc = state.jobCommands;
      return jc && typeof jc.batchOp === "function" ? jc.batchOp : null;
    }

    function canManageTags() {
      return !!batchOpMethod();
    }

    function jobTags(job) {
      var raw = job && job.tags;
      if (Array.isArray(raw)) return raw.map(String);
      if (typeof raw === "string" && raw) {
        try {
          var parsed = JSON.parse(raw);
          if (Array.isArray(parsed)) return parsed.map(String);
        } catch (err) { /* malformed display data: treat as untagged */ }
      }
      return [];
    }

    function rowTagsEl(job, jobId) {
      var tags = jobTags(job);
      if (!tags.length) return null;
      var doc = state.doc;
      var wrap = doc.createElement("span");
      wrap.className = "acp-mod-tasks-row-tags";
      wrap.setAttribute("data-acp-row-tags", jobId);
      tags.forEach(function (tag) {
        var chip = doc.createElement("span");
        chip.className = "acp-mod-tasks-tag-chip";
        chip.setAttribute("data-acp-tag", tag);
        chip.textContent = tag;
        wrap.appendChild(chip);
      });
      return wrap;
    }

    function openTagsDialog(jobId) {
      if (state.disposed || !canManageTags()) return;
      if (state.actions.busy[jobId]) return; // one command per row at a time
      var job = findJob(jobId);
      state.dialog = {
        kind: "tags",
        jobId: String(jobId),
        jobName: job ? jobDisplayName(job) : String(jobId),
        tags: jobTags(job),
        value: "",
        busy: false,
        error: "",
        notice: "",
      };
      renderDialog();
    }

    function onTagsClick(jobId, ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (ev && ev.preventDefault) ev.preventDefault();
      if (state.disposed || !canManageTags()) return;
      openTagsDialog(jobId);
    }

    function nextTagList(tags, op, tag) {
      var out = (tags || []).slice();
      if (op === "add_tags") {
        if (out.indexOf(tag) === -1) out.push(tag);
      } else {
        out = out.filter(function (t) { return t !== tag; });
      }
      return out;
    }

    /** batch-ops returns 200 with a per-task report; a per-task ok:false is a
     * REPORT failure (server text verbatim), not a transport failure. An
     * unknown/missing report shape is tolerated as success (same rule as the
     * legacy _batchOp parser). */
    function batchOpOutcome(resp, jobId) {
      var results = resp && Array.isArray(resp.results) ? resp.results : null;
      if (!results) return { ok: true, message: "" };
      for (var i = 0; i < results.length; i++) {
        var entry = results[i];
        if (!entry || typeof entry !== "object") continue;
        var tid = String(entry.task_id || entry.id || "");
        if (tid && tid !== String(jobId)) continue;
        if (entry.ok === false || (entry.ok !== true && entry.error)) {
          return {
            ok: false,
            message: String(entry.error || entry.message
              || t("next.tasks.action_unknown_error")),
          };
        }
        return { ok: true, message: "" };
      }
      return { ok: true, message: "" };
    }

    function submitTagChange(op, rawTag) {
      var d = state.dialog;
      if (!d || d.kind !== "tags" || d.busy || state.disposed) return;
      if (op !== "add_tags" && op !== "remove_tags") return;
      var batchOp = batchOpMethod();
      if (!batchOp) return;
      var jobId = String(d.jobId || "");
      if (!jobId) return;
      var tag = normalizeName(rawTag);
      if (op === "add_tags" && !tag) return; // empty input is a no-op
      if (op === "remove_tags" && !tag) return;
      var gen = state.generation;
      var statusAtStart = beginBusy(jobId, "tags");
      if (statusAtStart === null) {
        d.error = t("next.tasks.action_in_flight");
        renderDialog();
        return;
      }
      d.error = "";
      d.busy = true;
      renderDialog();
      var promise;
      try {
        promise = batchOp.call(state.jobCommands, {
          taskIds: [jobId],
          op: op,
          payload: { tags: [tag] },
        });
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function (resp) {
        if (gen !== state.generation || state.disposed) return;
        var outcome = batchOpOutcome(resp, jobId);
        var dd = state.dialog;
        if (!outcome.ok) {
          // A report-level refusal is not a transport failure: no refresh,
          // the row keeps its previous data and the server text is shown.
          failBusy(jobId, "tags", statusAtStart, outcome.message);
          if (dd && dd.kind === "tags" && String(dd.jobId) === jobId) {
            dd.busy = false;
            dd.error = t("next.tasks.action_failed", {
              action: actionLabel("tags"),
              message: outcome.message,
            });
            renderDialog();
          }
          return;
        }
        endBusy(jobId);
        delete state.actions.errors[jobId];
        syncRowActions(jobId);
        if (dd && dd.kind === "tags" && String(dd.jobId) === jobId) {
          dd.busy = false;
          dd.value = "";
          dd.tags = nextTagList(dd.tags, op, tag);
          renderDialog();
        }
        // Same task-view/store refresh path as every other write: the row
        // chips/rows are re-read through the owned list controller.
        refresh();
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        var message = actionErrorText(err);
        failBusy(jobId, "tags", statusAtStart, message);
        var d3 = state.dialog;
        if (d3 && d3.kind === "tags" && String(d3.jobId) === jobId) {
          d3.busy = false;
          d3.error = t("next.tasks.action_failed", {
            action: actionLabel("tags"),
            message: message,
          });
          renderDialog();
        }
      });
    }

    /** Paint the per-job busy/error state into the live row(s). Called on
     * state transitions and whenever a render rebuilds a row, so the state is
     * data-driven (an external list refresh cannot lose it). */
    function syncRowActions(jobId) {
      var uis = state.rowUi[jobId];
      if (!uis) return;
      var busy = state.actions.busy[jobId] || null;
      var err = state.actions.errors[jobId] || null;
      uis.forEach(function (ui) {
        if (ui.row) {
          ui.row.setAttribute("data-acp-action-state",
            busy ? "busy" : (err ? "error" : "idle"));
        }
        ui.buttons.forEach(function (btn) {
          var unavailable = btn.getAttribute("data-acp-action-unavailable") === "true";
          btn.disabled = !!busy || unavailable;
          btn.setAttribute("aria-busy", busy ? "true" : "false");
        });
        if (!ui.msg) return;
        if (busy) {
          ui.msg.hidden = false;
          ui.msg.textContent = t("next.tasks.action_busy", { action: actionLabel(busy.action) });
          ui.msg.setAttribute("role", "status");
          ui.msg.setAttribute("data-acp-action-state", "busy");
          ui.msg.setAttribute("data-acp-action", busy.action);
          ui.msg.setAttribute("data-acp-action-error", "");
        } else if (err) {
          ui.msg.hidden = false;
          ui.msg.textContent = t("next.tasks.action_failed", {
            action: actionLabel(err.action),
            message: err.message,
          });
          ui.msg.setAttribute("role", "alert");
          ui.msg.setAttribute("data-acp-action-state", "error");
          ui.msg.setAttribute("data-acp-action", err.action);
          ui.msg.setAttribute("data-acp-action-error", err.message);
        } else {
          ui.msg.hidden = true;
          ui.msg.textContent = "";
          ui.msg.setAttribute("role", "status");
          ui.msg.setAttribute("data-acp-action-state", "idle");
          ui.msg.setAttribute("data-acp-action", "");
          ui.msg.setAttribute("data-acp-action-error", "");
        }
      });
    }

    function onActionClick(jobId, action, ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (ev && ev.preventDefault) ev.preventDefault();
      runAction(jobId, action);
    }

    /** Fire ONE injected command for ONE job; busy → error/success transitions
     * are generation-guarded exactly like the read paths (a project switch or
     * dispose kills the late write). Success re-runs the owned controller's
     * refresh (single cache) and, when that job is selected, re-fetches its
     * summary through client.taskView. */
    function runAction(jobId, action) {
      if (state.disposed || !jobId) return;
      var method = commandMethod(action);
      if (!method) return;
      var statusAtStart = beginBusy(jobId, action);
      if (statusAtStart === null) return; // one command per job at a time
      var gen = state.generation;
      var promise;
      try {
        promise = method.call(state.jobCommands, jobId);
      } catch (err) {
        promise = Promise.reject(err);
      }
      Promise.resolve(promise).then(function () {
        if (gen !== state.generation || state.disposed) return;
        endBusy(jobId);
        delete state.actions.errors[jobId];
        syncRowActions(jobId);
        afterActionSuccess(jobId);
      }, function (err) {
        if (gen !== state.generation || state.disposed) return;
        failBusy(jobId, action, statusAtStart, actionErrorText(err));
      });
    }

    function actionErrorText(err) {
      if (!err) return t("next.tasks.action_unknown_error");
      var body = err.body;
      var detail = body && (body.detail !== undefined ? body.detail : body.message);
      var msg = err.message || detail || (typeof err === "string" ? err : "");
      if (msg && typeof msg === "object") {
        // FastAPI detail can be an object (e.g. name_revision_conflict).
        msg = msg.message || msg.error || JSON.stringify(msg);
      }
      return msg ? String(msg) : t("next.tasks.action_unknown_error");
    }

    function afterActionSuccess(jobId) {
      // Same task-view path as every read: the owned list controller re-runs
      // the current query (filters/project/offset included) against the store.
      refresh();
      if (String(state.selectedJobId || "") === String(jobId)) {
        refreshDetailAfterAction(jobId);
      }
    }

    /** Guarded summary refresh (generation + stable selection + project), no
     * focus re-publication: the command changed server state, not selection. */
    function refreshDetailAfterAction(jobId) {
      if (!state.taskView || typeof state.taskView.fetchSummary !== "function") return;
      var gen = state.generation;
      var summaryPromise;
      try {
        summaryPromise = state.taskView.fetchSummary(jobId);
      } catch (e) {
        return; // detail refresh is best-effort; the row error path owns failures
      }
      Promise.resolve(summaryPromise).then(function (summary) {
        if (gen !== state.generation || state.disposed) return;
        if (String(state.selectedJobId || "") !== String(jobId)) return;
        var sess = state.session && typeof state.session.current === "function"
          ? state.session.current() : null;
        if (sess && sess.jobId && String(sess.jobId) !== String(jobId)) return;
        if (summary && summary.project_id != null && state.projectId
          && String(summary.project_id) !== String(state.projectId)) return;
        state.detail = summary || null;
        renderDetail(state.detail);
      }, function () { /* best-effort: keep the last known detail */ });
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
      state.generation++; // kills in-flight list + detail + command writes
      state.projectId = pid;
      if (state.session) state.session.clear();
      state.selectedJobId = null;
      state.detail = null;
      state.body = null;
      state.rows = [];
      // Command UI state belongs to the previous project's rows; never paint it
      // onto the new project (late command receipts are generation-guarded).
      state.actions = { busy: Object.create(null), errors: Object.create(null) };
      state.rowUi = Object.create(null);
      // A-20/A-21 local UI state is scoped to the previous project too: no
      // stale selection, batch summary or dialog may survive the switch.
      state.selection = Object.create(null);
      state.batchMsg = null;
      state.purgeInFlight = false;
      state.trashInFlight = false; // A-24: the trash batch freezes with the scope
      closeDialog();
      renderBatchBar();
      renderBatchMessage();
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
      pruneActionErrors(body);
      pruneSelection(body);
      renderFilters(); // chip counts always mirror the whole-scope body.counts
      if (state.contract && typeof state.contract.flattenRows === "function") {
        state.rows = state.contract.flattenRows(body);
      }
      renderBatchBar();
      if (!body || !body.groups || !body.groups.length) {
        renderEmpty();
        return;
      }
      renderRows();
    }

    /** Drop stale per-row command errors whose job left the current result
     * set (filtered out / deleted): the row they explained is gone. */
    function pruneActionErrors(body) {
      var seen = Object.create(null);
      ((body && body.groups) || []).forEach(function (group) {
        ((group && group.jobs) || []).forEach(function (job) {
          seen[String(job.id || job.job_id || "")] = true;
        });
      });
      Object.keys(state.actions.errors).forEach(function (jobId) {
        if (!seen[jobId]) delete state.actions.errors[jobId];
      });
    }

    /** Batch selection only ever references rendered rows: a selection whose
     * row left the result set (filter/page/project change) is dropped, so a
     * later batch purge can never target an invisible task. */
    function pruneSelection(body) {
      if (!Object.keys(state.selection).length) return;
      var seen = Object.create(null);
      ((body && body.groups) || []).forEach(function (group) {
        ((group && group.jobs) || []).forEach(function (job) {
          seen[String(job.id || job.job_id || "")] = true;
        });
      });
      Object.keys(state.selection).forEach(function (jobId) {
        if (!seen[jobId]) delete state.selection[jobId];
      });
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
      return projectRequest("/projects", { method: "GET" });
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
      // A-18/A-19 write path: the INJECTED client's jobCommands only. A mock /
      // minimal client that lacks it keeps the panel read-only (actions hidden).
      state.jobCommands = (client && client.jobCommands) || null;
      state.actions = { busy: Object.create(null), errors: Object.create(null) };
      state.rowUi = Object.create(null);
      // Fresh lifecycle UI state (a remount never inherits the previous life).
      state.selection = Object.create(null);
      state.batchMsg = null;
      state.purgeInFlight = false;
      // A-24: every mount starts in the normal queue scope; a disposed
      // instance that is mounted again never reopens the recycle bin.
      state.trash = false;
      state.trashInFlight = false;
      state.prefs = Object.assign({}, state.prefs, { archived: "exclude" });
      state.dialog = null;
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
      state.jobCommands = null; // no command client survives dispose
      state.actions = { busy: Object.create(null), errors: Object.create(null) };
      state.rowUi = Object.create(null);
      state.selection = Object.create(null);
      state.batchMsg = null;
      state.purgeInFlight = false;
      // A-24: a disposed instance leaves the recycle bin scope entirely.
      state.trash = false;
      state.trashInFlight = false;
      state.dialog = null;
      state.filterEls = null;
      state.filterSignature = "";
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
