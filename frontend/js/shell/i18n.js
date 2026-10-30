/*
 * ACP Next Shell — independent i18n instance (UI-01).
 * Authority: docs/reference/next-change/ACP_Frontend_UI01_Shell_Design_20261030.md §6
 *
 * Same contract as the legacy js/workbench/i18n.js but a separate instance:
 * the shell never loads the old dictionary and never occupies the legacy
 * globals `I18N` / `t`. Storage key `acp-lang` is deliberately SHARED with the
 * classic page (switch language in one entry, both follow).
 *
 * Fallback chain: current lang → zh-CN → the key itself.
 * Interpolation: `{var}` split/join exactly like the legacy t().
 * Attribute scan: data-i18n / data-i18n-title / data-i18n-ph / data-i18n-opt
 * + the NEW data-i18n-aria (aria-label) which lives only here.
 *
 * agent.* / next.tasks.* / next.viewer.* module dictionaries landed with
 * UI-03A / UI-02b / UI-02a in this same file (namespaced, no conflicts);
 * zh values mirror the modules' inline literals verbatim.
 */
window.ACPShell = window.ACPShell || {};
ACPShell.i18n = (function () {
  "use strict";

  var STORAGE_KEY = "acp-lang";
  var LANGS = ["zh-CN", "en-US"];
  var FALLBACK_LANG = "zh-CN";

  var DICT = {
    "zh-CN": {
      "layout.title": "配置布局",
      "layout.hint": "选择每个区域的面板并调整比例。交换已有面板会保留内容；布局按工作区保存在当前浏览器。",
      "layout.preview": "布局预览",
      "layout.main": "主区域",
      "layout.side": "侧边区域",
      "layout.bottom": "底部区域",
      "layout.none": "隐藏此区域",
      "layout.sideWidth": "侧边宽度",
      "layout.bottomHeight": "底部高度",
      "layout.reset": "恢复默认",
      "layout.cancel": "取消",
      "layout.apply": "应用布局",
      "layout.invalid": "布局未应用，请检查面板选择。",
      "layout.mainRequired": "请先为主区域选择其他面板，再移动当前主面板。",
      "layout.sessionOnly": "已应用；浏览器不允许保存，刷新后将恢复原布局。",
      // shell — page frame / workspaces
      "shell.app.title": "ACP 分子计算工作台 · Next",
      "shell.app.badge": "实验入口",
      "shell.lang.toggle": "中文 | EN",
      "shell.link.classic": "经典工作台",
      "shell.workspace.switch_label": "工作区",
      "shell.workspace.shortcut_hint": "Alt+1/2/3 切换工作区",
      "shell.workspace.research": "研究",
      "shell.workspace.molecular": "分子",
      "shell.workspace.execution": "执行",
      "shell.focus.empty": "未选择结构或任务",
      "shell.focus.attempt": "第 {n} 次尝试",
      "shell.conn.unknown": "连接状态未知",
      "shell.conn.online": "在线",
      "shell.conn.offline": "离线",
      // shell — panels / slots
      "shell.panel.reload": "重新加载",
      "shell.panel.close": "关闭面板",
      "shell.panel.follow_focus": "跟随全局焦点",
      "shell.panel.pinned": "已固定引用",
      "shell.panel.collapse": "收起此栏",
      "shell.panel.expand": "展开此栏",
      "shell.module.agent_workspace": "Agent 工作区",
      "shell.module.molecule_viewer": "结构查看器",
      "shell.module.task_explorer": "任务浏览",
      "shell.module.task_detail": "任务详情与证据",
      "shell.module.task_config": "任务配置",
      "shell.slot.pending_module": "待接入",
      "shell.slot.pending_hint": "该面板将在 {phase} 阶段接入",
      "shell.slot.unknown_module": "模块未接入",
      // shell — states / fatal / a11y
      "shell.state.loading": "加载中…",
      "shell.state.error_title": "面板加载失败",
      "shell.state.error_retry": "重试",
      "shell.state.error_copy": "复制错误详情",
      "shell.state.denied_title": "无权限访问",
      "shell.state.denied_body": "缺少能力：{capability}",
      "shell.state.denied_hint": "切换项目或联系管理员后重试",
      "shell.fatal.title": "实验入口初始化失败",
      "shell.fatal.retry": "重试初始化",
      "shell.fatal.back_classic": "返回经典工作台",
      "shell.a11y.skip": "跳到工作区",
      // agent workspace (UI-03A)
      "agent.action.open_artifact": "查看工件",
      "agent.action.open_structure": "打开结构",
      "agent.action.open_task": "打开任务",
      "agent.action.retry": "重试",
      "agent.connect.no_model": "只读 · 未连接模型",
      "agent.empty.no_record": "暂无研究记录",
      "agent.empty.no_record_hint": "在执行工作区选择任务后，此处显示关联研究",
      "agent.error.load_failed": "加载失败",
      "agent.evidence.claims": "验证结论",
      "agent.evidence.open_report": "查看报告",
      "agent.fixture.badge": "演示数据（fixture）",
      "agent.note.model": "模型解释",
      "agent.plan.empty": "暂无计划步骤",
      "agent.plan.empty_task": "未选择任务",
      "agent.plan.section.candidate": "候选计划",
      "agent.plan.section.execution": "实际执行",
      "agent.plan.tools.pending": "工具调用时间线将在 DSH 接入后提供",
      "agent.summary.none": "无",
      // task explorer (UI-02b)
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
      // molecule viewer (UI-02a)
      "next.viewer.empty": "等待结构加载",
      "next.viewer.loading": "结构加载中…",
      "next.viewer.ready": "",
      "next.viewer.errorTitle": "结构加载失败",
      "next.viewer.errorMessage": "molecule-viewer 模块加载失败，请查看控制台。",
      "next.viewer.retry": "重试",
      // agent workspace module-load fallback (descriptor error keys)
      "agent.workspace.errorTitle": "Agent 工作区不可用",
      "agent.workspace.errorMessage": "agent-workspace 模块加载失败，请查看控制台。"
    },
    "en-US": {
      "layout.title": "Configure layout",
      "layout.hint": "Choose panels and proportions. Moving panels preserves their content. Each workspace layout is saved in this browser.",
      "layout.preview": "Layout preview",
      "layout.main": "Main area",
      "layout.side": "Side area",
      "layout.bottom": "Bottom area",
      "layout.none": "Hide this area",
      "layout.sideWidth": "Side width",
      "layout.bottomHeight": "Bottom height",
      "layout.reset": "Restore defaults",
      "layout.cancel": "Cancel",
      "layout.apply": "Apply layout",
      "layout.invalid": "Layout was not applied. Check the panel selection.",
      "layout.mainRequired": "Choose another main panel before moving it to an empty area.",
      "layout.sessionOnly": "Applied for this session. Browser storage is unavailable; reloading will restore the previous layout.",
      "shell.app.title": "ACP Molecular Workbench · Next",
      "shell.app.badge": "Experimental",
      "shell.lang.toggle": "中文 | EN",
      "shell.link.classic": "Classic workbench",
      "shell.workspace.switch_label": "Workspaces",
      "shell.workspace.shortcut_hint": "Alt+1/2/3 to switch workspace",
      "shell.workspace.research": "Research",
      "shell.workspace.molecular": "Molecular",
      "shell.workspace.execution": "Execution",
      "shell.focus.empty": "No structure or task selected",
      "shell.focus.attempt": "attempt {n}",
      "shell.conn.unknown": "Connection unknown",
      "shell.conn.online": "Online",
      "shell.conn.offline": "Offline",
      "shell.panel.reload": "Reload",
      "shell.panel.close": "Close panel",
      "shell.panel.follow_focus": "Follows global focus",
      "shell.panel.pinned": "Pinned reference",
      "shell.panel.collapse": "Collapse",
      "shell.panel.expand": "Expand",
      "shell.module.agent_workspace": "Agent Workspace",
      "shell.module.molecule_viewer": "Structure Viewer",
      "shell.module.task_explorer": "Task Explorer",
      "shell.module.task_detail": "Task Detail & Evidence",
      "shell.module.task_config": "Task Config",
      "shell.slot.pending_module": "Coming in later phase",
      "shell.slot.pending_hint": "This panel lands in phase {phase}",
      "shell.slot.unknown_module": "Module not wired",
      "shell.state.loading": "Loading…",
      "shell.state.error_title": "Panel failed to load",
      "shell.state.error_retry": "Retry",
      "shell.state.error_copy": "Copy error details",
      "shell.state.denied_title": "Access denied",
      "shell.state.denied_body": "Missing capability: {capability}",
      "shell.state.denied_hint": "Switch project or contact your admin, then retry",
      "shell.fatal.title": "Failed to start the experimental workspace",
      "shell.fatal.retry": "Retry startup",
      "shell.fatal.back_classic": "Back to classic workbench",
      "shell.a11y.skip": "Skip to workspace",
      // agent workspace (UI-03A)
      "agent.action.open_artifact": "View artifact",
      "agent.action.open_structure": "Open structure",
      "agent.action.open_task": "Open task",
      "agent.action.retry": "Retry",
      "agent.connect.no_model": "Read-only · no model connected",
      "agent.empty.no_record": "No research record",
      "agent.empty.no_record_hint": "Select a task in the Execution workspace to see linked research",
      "agent.error.load_failed": "Failed to load",
      "agent.evidence.claims": "Validation findings",
      "agent.evidence.open_report": "View report",
      "agent.fixture.badge": "Demo data (fixture)",
      "agent.note.model": "Model explanation",
      "agent.plan.empty": "No plan steps yet",
      "agent.plan.empty_task": "No task selected",
      "agent.plan.section.candidate": "Candidate plan",
      "agent.plan.section.execution": "Actual execution",
      "agent.plan.tools.pending": "Tool-call timeline available after DSH integration",
      "agent.summary.none": "None",
      // task explorer (UI-02b)
      "next.tasks.title": "Task list",
      "next.tasks.refresh": "Refresh",
      "next.tasks.project_all": "All projects",
      "next.tasks.loading": "Loading…",
      "next.tasks.empty": "No jobs",
      "next.tasks.error": "Task list failed to load",
      "next.tasks.forbidden": "Not authorized to view the task list",
      "next.tasks.retry": "Retry",
      "next.tasks.stale": "Cache expired — click Refresh",
      "next.tasks.detail_none": "Select a task to see details",
      "next.tasks.detail_loading": "Loading task details…",
      "next.tasks.detail_failed": "Details failed to load",
      "next.tasks.failure_reason": "Failure reason",
      "next.tasks.attempt_title": "attempt {n}",
      "next.tasks.open_task": "Open task",
      "next.tasks.prev": "Previous",
      "next.tasks.next": "Next",
      "next.tasks.page_of": "Items {from}–{to} of {total} tasks",
      "next.tasks.errorTitle": "Task list unavailable",
      "next.tasks.errorMessage": "task-explorer module failed to load — see the console.",
      // molecule viewer (UI-02a)
      "next.viewer.empty": "Waiting for structure",
      "next.viewer.loading": "Loading structure…",
      "next.viewer.ready": "",
      "next.viewer.errorTitle": "Structure failed to load",
      "next.viewer.errorMessage": "molecule-viewer module failed to load — see the console.",
      "next.viewer.retry": "Retry",
      // agent workspace module-load fallback (descriptor error keys)
      "agent.workspace.errorTitle": "Agent workspace unavailable",
      "agent.workspace.errorMessage": "agent-workspace module failed to load — see the console."
    }
  };

  var currentLang = FALLBACK_LANG;

  function readStoredLang() {
    try {
      var stored = window.localStorage && window.localStorage.getItem(STORAGE_KEY);
      if (LANGS.indexOf(stored) >= 0) return stored;
    } catch (e) { /* storage unavailable (sandbox/privacy mode) → default */ }
    return null;
  }
  currentLang = readStoredLang() || FALLBACK_LANG;

  function getLang() { return currentLang; }

  function t(key, vars) {
    var dict = DICT[currentLang] || DICT[FALLBACK_LANG];
    var value = dict[key] || DICT[FALLBACK_LANG][key] || key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return value;
  }

  /** True only when a REAL translation exists (never on key-echo fallback). */
  function has(key) {
    if (!key) return false;
    var dict = DICT[currentLang] || DICT[FALLBACK_LANG];
    return Boolean((dict && dict[key]) || (DICT[FALLBACK_LANG] && DICT[FALLBACK_LANG][key]));
  }

  /**
   * Scan root (default document) for the five data-i18n* attributes.
   * A node is only rewritten when its key has a real translation — missing
   * keys fall back to the key itself in t(), and clobbering mounted module
   * copy with a bare key on language toggle is a bug, not a translation.
   */
  function applyI18n(root) {
    var scope = root || (typeof document !== "undefined" ? document : null);
    if (!scope || !scope.querySelectorAll) return;
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.lang = currentLang;
    }
    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (has(key)) el.textContent = t(key);
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-title");
      if (has(key)) el.setAttribute("title", t(key));
    });
    scope.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-ph");
      if (has(key)) el.setAttribute("placeholder", t(key));
    });
    scope.querySelectorAll("[data-i18n-opt]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-opt");
      if (has(key)) el.textContent = t(key);
    });
    scope.querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-aria");
      if (has(key)) el.setAttribute("aria-label", t(key));
    });
  }

  function setLang(lang) {
    if (LANGS.indexOf(lang) < 0) return currentLang;
    currentLang = lang;
    try {
      window.localStorage && window.localStorage.setItem(STORAGE_KEY, lang);
    } catch (e) { /* storage unavailable — keep in-memory language */ }
    if (typeof document !== "undefined") {
      if (document.documentElement) document.documentElement.lang = lang;
      if (typeof CustomEvent === "function" && document.dispatchEvent) {
        document.dispatchEvent(new CustomEvent("acp:langchange", { detail: { lang: lang } }));
      }
    }
    return currentLang;
  }

  /** Legacy-parity toggle: flip lang, persist, rescan static attributes. */
  function toggle() {
    setLang(currentLang === "zh-CN" ? "en-US" : "zh-CN");
    applyI18n(typeof document !== "undefined" ? document : null);
    return currentLang;
  }

  return {
    DICT: DICT,
    t: t,
    applyI18n: applyI18n,
    setLang: setLang,
    toggle: toggle,
    getLang: getLang
  };
})();
