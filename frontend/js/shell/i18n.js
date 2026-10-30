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
      "shell.module.reaction_network": "反应网络",
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
      // task config (UI-03)
      "next.config.title": "任务配置",
      "next.config.structure": "1 · 结构",
      "next.config.structure_placeholder": "粘贴 SMILES、XYZ 或 molfile 文本，或上传文件",
      "next.config.parse": "解析结构",
      "next.config.upload": "上传文件",
      "next.config.parsing": "正在解析结构…",
      "next.config.structure_hint": "尚未载入结构。支持 SMILES / XYZ / SDF / MOL / GJF / ORCA 输入。",
      "next.config.structure_summary": "{formula} · {atoms} 个原子 · 电荷 {charge} · 自旋 {multiplicity}",
      "next.config.structure_error": "结构解析失败：{message}",
      "next.config.workflow": "2 · 计算类型与方法",
      "next.config.workflow_label": "计算类型",
      "next.config.workflow_unsupported": "（需经典向导）",
      "next.config.profile_label": "方法预设",
      "next.config.profile_fields": "字段默认值",
      "next.config.advanced": "高级参数",
      "next.config.state": "3 · 体系与资源",
      "next.config.charge": "电荷",
      "next.config.multiplicity": "自旋多重度",
      "next.config.molecule_name": "分子名",
      "next.config.project": "项目",
      "next.config.nproc": "核数",
      "next.config.mem": "内存",
      "next.config.review": "4 · 预检与提交",
      "next.config.summary": "配置摘要",
      "next.config.summary_empty": "载入结构并选择计算类型后显示摘要。",
      "next.config.validate": "预检",
      "next.config.preflighting": "预检中…",
      "next.config.preflight_ok": "预检通过：{digest}",
      "next.config.preflight_failed": "预检未通过",
      "next.config.preflight_retry": "重试预检",
      "next.config.submit": "提交任务",
      "next.config.submitting": "提交中…",
      "next.config.submit_blocked": "预检通过后才能提交",
      "next.config.receipt": "已提交",
      "next.config.receipt_body": "任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试",
      "next.config.receipt_digest": "计划摘要 {digest}",
      "next.config.open_task": "在任务面板打开",
      "next.config.open_classic": "在经典工作台打开",
      "next.config.open_classic_hint": "此计算类型需要额外输入（源任务、坐标或专用文件），请用经典向导完成配置。",
      "next.config.validate_running": "校验方法参数…",
      "next.config.validate_ok": "方法参数有效",
      "next.config.validate_invalid": "方法参数有误",
      "next.config.validate_error": "方法校验请求失败：{message}",
      "next.config.draft_found": "发现保存的草稿（{when}）",
      "next.config.draft_restore": "恢复",
      "next.config.draft_discard": "丢弃",
      "next.config.draft_saving": "正在保存草稿…",
      "next.config.draft_saved": "草稿已保存",
      "next.config.draft_conflict": "草稿已在其他地方更新",
      "next.config.draft_reload": "载入最新",
      "next.config.draft_fork": "另存为新草稿",
      "next.config.draft_error": "草稿保存失败：{message}",
      "next.config.errorTitle": "任务配置不可用",
      "next.config.errorMessage": "task-config 模块加载失败，请查看控制台。",
      "next.config.load_error": "目录加载失败：{message}",
      "next.config.retry": "重试",
      "next.config.note.composite_ri": "复合方法使用内置 RI 近似，RI/辅助基组由方法决定。",
      "next.config.note.builtin_dispersion": "该方法已内置 {dispersion} 色散校正，色散项固定为 none。",
      "next.config.error.structure_required": "请先载入结构。",
      "next.config.error.workflow_required": "请选择计算类型。",
      "next.config.error.workflow_unsupported": "该计算类型需要经典向导的专用输入。",
      "next.config.error.schema_missing": "目录中缺少该方法 schema。",
      "next.config.error.schema_empty": "该方法 schema 没有可配置层级。",
      "next.config.error.level_engine": "层级 {level} 缺少 engine。",
      "next.config.error.charge_integer": "电荷必须是整数。",
      "next.config.error.multiplicity_integer": "自旋多重度必须是 ≥1 的整数。",
      "next.config.error.multiplicity_parity": "电子数为 {electrons}，自旋多重度奇偶不匹配。",
      "next.config.error.nproc": "核数必须是 1–256 的整数。",
      "next.config.error.mem": "请填写内存（例如 2000MB）。",
      "next.config.warning.structure": "结构提示：{message}",
      "next.config.submit_failed": "提交失败：{message}",
      "next.config.plan_stale": "预检之后配置发生变化，请重新预检。",
      "next.config.forbidden": "当前身份无权提交任务。",
      // task evidence panel (UI-03)
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
      "next.detail.download": "下载",
      "next.detail.open_task": "在任务浏览中打开",
      "next.detail.open_classic": "经典工作台",
      "next.detail.wrong_project": "该任务不属于当前项目。",
      "next.detail.attempt_changed": "任务尝试次数已变化，请刷新。",
      "next.detail.failure": "失败原因",
      "next.detail.errorTitle": "任务详情不可用",
      "next.detail.errorMessage": "exec-detail 模块加载失败，请查看控制台。",
      // reaction network (UI-04, legacy mechanism studies only)
      "next.network.title": "反应网络",
      "next.network.empty": "选择任务后查看反应网络。",
      "next.network.empty_hint": "反应网络来自历史 mechanism 研究；现役工作流不产出该数据。",
      "next.network.loading": "正在读取反应网络…",
      "next.network.no_study": "该任务没有关联的 mechanism 研究。",
      "next.network.no_network": "研究报告未包含 reaction_network。",
      "next.network.error": "反应网络加载失败：{message}",
      "next.network.retry": "重试",
      "next.network.node": "节点",
      "next.network.role": "角色",
      "next.network.energy": "能量",
      "next.network.step": "步骤",
      "next.network.barrier": "正向能垒",
      "next.network.fidelity": "保真度",
      "next.network.status": "状态",
      "next.network.hint": "点击节点或箭头查看冻结字段。",
      "next.network.errorTitle": "反应网络不可用",
      "next.network.errorMessage": "reaction-network 模块加载失败，请查看控制台。",
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
      "shell.module.reaction_network": "Reaction network",
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
      // task config (UI-03)
      "next.config.title": "Task config",
      "next.config.structure": "1 · Structure",
      "next.config.structure_placeholder": "Paste SMILES, XYZ or molfile text, or upload a file",
      "next.config.parse": "Parse structure",
      "next.config.upload": "Upload file",
      "next.config.parsing": "Parsing structure…",
      "next.config.structure_hint": "No structure yet. SMILES / XYZ / SDF / MOL / GJF / ORCA input supported.",
      "next.config.structure_summary": "{formula} · {atoms} atoms · charge {charge} · multiplicity {multiplicity}",
      "next.config.structure_error": "Structure parsing failed: {message}",
      "next.config.workflow": "2 · Workflow & method",
      "next.config.workflow_label": "Workflow",
      "next.config.workflow_unsupported": "(classic wizard)",
      "next.config.profile_label": "Method profile",
      "next.config.profile_fields": "Field defaults",
      "next.config.advanced": "Advanced parameters",
      "next.config.state": "3 · System & resources",
      "next.config.charge": "Charge",
      "next.config.multiplicity": "Multiplicity",
      "next.config.molecule_name": "Molecule name",
      "next.config.project": "Project",
      "next.config.nproc": "Cores",
      "next.config.mem": "Memory",
      "next.config.review": "4 · Preflight & submit",
      "next.config.summary": "Configuration summary",
      "next.config.summary_empty": "Load a structure and pick a workflow to see the summary.",
      "next.config.validate": "Preflight",
      "next.config.preflighting": "Preflighting…",
      "next.config.preflight_ok": "Preflight passed: {digest}",
      "next.config.preflight_failed": "Preflight failed",
      "next.config.preflight_retry": "Retry preflight",
      "next.config.submit": "Submit task",
      "next.config.submitting": "Submitting…",
      "next.config.submit_blocked": "Preflight first",
      "next.config.receipt": "Submitted",
      "next.config.receipt_body": "Task {jobId} · status {status} · attempt {attempt}",
      "next.config.receipt_digest": "Plan digest {digest}",
      "next.config.open_task": "Open in task panel",
      "next.config.open_classic": "Open classic workbench",
      "next.config.open_classic_hint": "This workflow needs extra input (source task, coordinates or a dedicated file). Finish it in the classic wizard.",
      "next.config.validate_running": "Validating method…",
      "next.config.validate_ok": "Method parameters valid",
      "next.config.validate_invalid": "Method parameters rejected",
      "next.config.validate_error": "Method validation failed: {message}",
      "next.config.draft_found": "Saved draft found ({when})",
      "next.config.draft_restore": "Restore",
      "next.config.draft_discard": "Discard",
      "next.config.draft_saving": "Saving draft…",
      "next.config.draft_saved": "Draft saved",
      "next.config.draft_conflict": "Draft updated elsewhere",
      "next.config.draft_reload": "Load latest",
      "next.config.draft_fork": "Save as new draft",
      "next.config.draft_error": "Draft save failed: {message}",
      "next.config.errorTitle": "Task config unavailable",
      "next.config.errorMessage": "task-config module failed to load — see the console.",
      "next.config.load_error": "Catalog load failed: {message}",
      "next.config.retry": "Retry",
      "next.config.note.composite_ri": "Composite method: RI approximation and auxiliary bases are fixed by the method.",
      "next.config.note.builtin_dispersion": "This method carries a built-in {dispersion} correction; dispersion is fixed to none.",
      "next.config.error.structure_required": "Load a structure first.",
      "next.config.error.workflow_required": "Choose a workflow.",
      "next.config.error.workflow_unsupported": "This workflow needs dedicated input from the classic wizard.",
      "next.config.error.schema_missing": "The catalog has no method schema for this workflow.",
      "next.config.error.schema_empty": "This method schema has no configurable level.",
      "next.config.error.level_engine": "Level {level} has no engine.",
      "next.config.error.charge_integer": "Charge must be an integer.",
      "next.config.error.multiplicity_integer": "Multiplicity must be an integer ≥ 1.",
      "next.config.error.multiplicity_parity": "{electrons} electrons — multiplicity parity does not match.",
      "next.config.error.nproc": "Cores must be an integer between 1 and 256.",
      "next.config.error.mem": "Enter memory (e.g. 2000MB).",
      "next.config.warning.structure": "Structure note: {message}",
      "next.config.submit_failed": "Submit failed: {message}",
      "next.config.plan_stale": "The configuration changed after preflight — preflight again.",
      "next.config.forbidden": "This identity may not submit tasks.",
      // task evidence panel (UI-03)
      "next.detail.empty": "Select a task to inspect its state and evidence.",
      "next.detail.empty_hint": "Evidence comes from the task detail and artifact list; read-only.",
      "next.detail.loading": "Loading task detail…",
      "next.detail.error": "Task detail failed to load: {message}",
      "next.detail.retry": "Retry",
      "next.detail.job": "Task",
      "next.detail.status": "Status",
      "next.detail.workflow": "Workflow",
      "next.detail.attempt": "attempt",
      "next.detail.plan_digest": "Plan digest",
      "next.detail.submission": "Submission",
      "next.detail.release": "Release",
      "next.detail.created": "Created",
      "next.detail.completed": "Completed",
      "next.detail.artifacts": "Evidence artifacts",
      "next.detail.artifacts_empty": "No artifact records yet.",
      "next.detail.download": "Download",
      "next.detail.open_task": "Open in task panel",
      "next.detail.open_classic": "Classic workbench",
      "next.detail.wrong_project": "This task does not belong to the current project.",
      "next.detail.attempt_changed": "The task attempt changed — refresh the task list.",
      "next.detail.failure": "Failure reason",
      "next.detail.errorTitle": "Task detail unavailable",
      "next.detail.errorMessage": "exec-detail module failed to load — see the console.",
      // reaction network (UI-04, legacy mechanism studies only)
      "next.network.title": "Reaction network",
      "next.network.empty": "Select a task to inspect its reaction network.",
      "next.network.empty_hint": "Reaction networks come from legacy mechanism studies; active workflows do not produce them.",
      "next.network.loading": "Loading reaction network…",
      "next.network.no_study": "This task has no linked mechanism study.",
      "next.network.no_network": "The study report carries no reaction_network.",
      "next.network.error": "Reaction network failed to load: {message}",
      "next.network.retry": "Retry",
      "next.network.node": "Node",
      "next.network.role": "Role",
      "next.network.energy": "Energy",
      "next.network.step": "Step",
      "next.network.barrier": "Forward barrier",
      "next.network.fidelity": "Fidelity",
      "next.network.status": "Status",
      "next.network.hint": "Click a node or an arrow to inspect the frozen fields.",
      "next.network.errorTitle": "Reaction network unavailable",
      "next.network.errorMessage": "reaction-network module failed to load — see the console.",
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
