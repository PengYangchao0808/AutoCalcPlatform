# ACP 前端基线地图与接管表（2026-10-18）

- 生成：并行计划一路 A（Frontend 收口）todo T6（`.omo/plans/acp-parallel-plan-1-frontend.md`，Closes GAP-1/GAP-4/GAP-7）。
- 基线：branch `feat/frontend-plan1-closure` @ `f410ac9dae035eb424c40c599692500fdea457cc`（= 冻结 F0 修订）。
- 数据来源：T1–T5 证据（`.omo/evidence/acp-parallel-plan-1-frontend/task-{1..5}-acp-parallel-plan-1-frontend.md`）；本文件为**接管表的权威副本**（`.omo` 仅日志，评审 X5）。
- 三态纪律：证据词汇只用 PASS / FAIL / NOT_VERIFIED；范围状态词汇用 IN_SCOPE / DEFERRED / NOT_APPLICABLE；延期≠通过。
- 阅读约定：行号均为基线 `f410ac9d` 现盘实测（T2 逐条 grep 核实，计划提示行号漂移见 §1.8）。

---

## 1. 装载/DOM/全局/网络/缓存/SSE/viewer/调用方地图（T2 浓缩版）

完整逐条地图（含全部原始 grep 输出）见 T2 证据 `task-2-acp-parallel-plan-1-frontend.md` §①–§⑨。本节为可导航浓缩版，**保留全部未迁移符号**（接管表见 §5）。

### 1.1 装载清单（T2 §①）

`frontend/ACP_Workbench_v2.html`（1252 行）实际装载：

| 类别 | 计数 | 说明 |
|---|---|---|
| `<script src>` | **41** | head 1 个 vendor（`:7`，`async id="viewer-library"`）+ body 40 个经典同步脚本（`:1211–1250`，按文档序执行）；无 defer/module |
| `<link rel="stylesheet">` | **10** | `:8–17`；2 个带 `?v=` cache-bust 查询串 |
| inline script / style | 0 / 0 | 无 |
| 引用但磁盘缺失 | 0 | 无 |

目录分布（41 脚本）：`js/*.js` 10 · `js/workbench/contracts` 2 · `js/client` 8 · `js/workbench/controllers` 4 · `js/visualization` 3 · `js/workbench/compat` 2 · `js/workbench`（i18n+bootstrap）2 · `js/workbench/features` 9 · `js/vendor` 1。

磁盘存在但**默认页不装载**的 JS 共 21 个（T2 §① 原文）：`module_tests/*.test.js` ×19、`module_tests/run_all.js`、`module_tests/page_integration.browser.js` 归测试；生产代码仅 2 个未装载——

- **`js/visualization/scene_instance.js`（F-①-1，未迁移 → T7 扩展 + T8 装载）**：存在且有模块测试（9/9），但 HTML 无 `<script src>`，运行页零引用。
- `js/workbench/contracts/fixtures/task_view_samples.js`：契约 fixture，供测试，保持不装载。

F-①-2：`memory_input.js` 仅 IIFE 内导出 `window.ACPMemoryInput`（`global.ACPMemoryInput@69`），无顶层声明——扫描口径需注意，非缺陷。

### 1.2 DOM hooks（T2 §②）

403 个 id（全部唯一，无重复）；`data-*` 结构属性 15 种（`data-panel-key/data-mobile-panel/data-mobile-body/data-create-step/data-input-mode/data-tab/data-view-mode/data-source-group/data-selection-kind/data-scan-kind`）；i18n 展示属性 253 处（非行为 hook）。分区计数：

| 区段（HTML 行） | id 数 |
|---|---|
| HEAD/TOPBAR（1–45） | 10 |
| 左栏 文件树/NMR/日志（46–79） | 7 |
| 中央 3D viewer + 测量 + 帧控制（80–240） | 51 |
| 队列面板（241–338） | 38 |
| 信息 + 移动面板（339–386） | 9 |
| 机理区（387–410） | 3 |
| 任务模态/向导（411–930） | 190 |
| 其他模态/抽屉（931–1210） | 95 |

抽查 5 个 hook↔消费者全部 PASS（`queue-list`→queue.js、`viewer-3d`→molecule-view.js、`structure-preview-3d`→wizard.js、`detail-drawer`→job-detail.js+api.js、`viewer-energy`→energy.js；T2 §② 有原始 grep）。

### 1.3 全局符号（T2 §③，按文件家族）

**已迁移（命名空间单实现，页面已接入）**：`js/client/**` 8 文件（`ACPClient.{transport,store,taskView,jobDetail,events,polling,jobCommands,wizardEndpoints}`）；`js/workbench/contracts/**` 2（`ACPWorkbench.contracts`）；`js/workbench/controllers/**` 4（`ACPWorkbench.controllers.{taskList,jobDetail,taskSession,candidateReview}`）；`js/visualization/{framing,mol3d_adapter}.js`（`ACPVisualization.{framing,mol3d}`）；`js/visualization/energy_chart_renderer.js`（`ACPVisualization.energyChart` + 25 个 `_eg*`/`energyGraph*` 纯函数，无副作用面）。

**未迁移全局（页根遗留 10 文件，IIFE 导出单命名空间，功能面保持）**：`ACPGeometryStore`（geometry_store.js:396）、`ACPStructureViewer`（structure_viewer.js:3933）、`ACPStructureEditor`（structure_editor.js:1920）、`ACPVibrationViewer`（vibration_viewer.js:1945）、`ACPSourcePicker`（structure_source_picker.js:2388）、`ACPTsmodeEditor`（tsmode_editor.js:910）、`ACPMemoryInput`（memory_input.js:69，IIFE 内）、`ACPJobEditor`/`ACPJobEditorInternals`（job_editor.js:2797/2828）、`ACPTaskInputWorkspace`（task_input_workspace.js:238）、`ACPCandidateDetails`（candidate_details.js:603）。

**未迁移全局（compat 层，接管核心区）**：

- `js/workbench/compat/shared-state.js`（408 行）顶层声明约 100 个全局（T2 §③ 完整清单）：选中/会话 `selectedJobId@23 selectedJobIsRemote@24 lastRemoteNode@25 selectedFileKey@26 activeSSE@27`；viewer 持有 `viewer@38 mainViewerFraming@39 currentModel@40 currentModelAtoms@41 viewerModelLoaded@42`；缓存 `jobsCache@31 taskViewCache@32 projectsCache@37 jobDetailCache@375 nodesCache@387 backendsCache@388 softwareDiscoveryCache@389 folderCache@281`；轮询遗留 `jobsPollTimer@28 jobsPollGeneration@29 jobsPollInFlight@30`；布局 `LAYOUT_DEFAULTS@60 layoutState@67 V_SPLITTER_DEFS@214`；文件树 `folderState@278 logPollToken@288 logCursor@289 logEntries@294 MAX_LOG_LINES@295`；机理 `mechanismStudiesByJob@308 …mechanismViewerState@311`；能量 `energyGraphState@317 samplingState@361`；其他 `queueCollapsedGroups@55 _taskCollator@57 lastStatus@279 lastFileListing@280 lastPinnedItems@305 lastSelectedJob@306 activeViewerTab@307 bgIndex@367 labelMode@368 framePlayTimer@369 initialView@370 targetProjectAction@371 targetProjectJobId@372 deleteJobId@373 jobDetailEndpointMissing@376 jobDetailGenerations@383 jobDetailCacheRevision@384 detailActionMessage@385 queueSelectedJobs@386 BG_COLORS@390 molDoc@391 STYLE_PRESETS@401` 等。
- `js/workbench/compat/api.js`（231 行）17 个顶层函数：`API_TIMEOUT_MS@5 API_REMOTE_TIMEOUT_MS@6 api@8 apiRequest@50 apiRemote@54 apiV2@64 apiV2Remote@75(死代码 F-④-2) formatDateTime@81 getStatusClass@90 createStatusBadge@102 normalizedProgress@109 isProgressKnown@117 isProgressIndeterminate@121 isActiveJobStatus@128 startPolling@135 loadDiskUsage@202 formatBytes@224`。

**未迁移全局（features 9 文件巨型遗留，逐符号权威=T2 §③+源文件）**：`molecule-view.js`（1388 行，约 110 顶层符号，入口 `initViewer@89 frame3DViewer@76 scheduleViewerFraming@82` 等）、`energy.js`（4586 行，含 `energyGraphState` 操作族与 `ACPStructureViewer._canvasViewerImpl@138` 注入点）、`queue.js`（2151 行，含 `refreshJobs@1314 loadProjects@1243 taskListControllerReady@1260` 与全部 `_batch*/_autoTag*/_lineage*` 队列域函数）、`reaction-builder.js`（2742 行，含 `selectJob@2193 openSSE@2126 loadNodes@1319 loadBackends@1848` 与 `previewViewer@2367 previewViewerFraming@2369 reactionViewer@2389 reactionViewerFraming@2390 workflowCatalogCache@2365 methodCatalogCache@2366` 声明）、`nmr.js`（827 行，`nmrPanelState@332 nmrEnsureViewer@721` 等 40+）、`wizard.js`（8304 行，`wizardState@362 wizardDraft@374 initPreviewViewer@1685 renderPreviewStructure3D@1694 handleModalSubmit@7688 submitJobModal@7732` 等）、`files-logs.js`（639 行，`filesSessionCapture@38 filesSessionAccepts@46 pollLogTail@579` 等）、`job-detail.js`（1195 行，`fetchJobDetail@128 refreshSelectedJobDetail@162 openDetailDrawer@796` 等）、`scan-s2.js`（116 行全 6 个全局：`s2scanState@2 s2scanCloseReport@43 s2scanResetState@47 s2scanDist@89 s2scanAngle@94 s2scanDihedral@102`）。

**保持类**：`i18n.js`（3125 行，`I18N@1 t@3003 …applyI18n@3086` 共 11 个）、`bootstrap.js`（710 行，`setupEventListeners@1` + 启动编排）、`vendor/3Dmol-2.5.5.min.js`（仅经 adapter 执行）。

### 1.4 网络入口/查询键/缓存写者（T2 §④）

网络入口单源表（已迁移）与调用计数：`api(` 93 · `apiRemote(` 19 · `apiV2(` 25 · `apiV2Remote(` **0（死入口，F-④-2 → T25 候选清理）** · `apiRequest(` 4。绕过 `apiV2` 直连 `"/api/v2"` 的 3 处：`structure_source_picker.js:96`、`wizard.js:7708`、`compat/api.js:65`（定义体）。

查询键（`task-view-query.js` 单源）：`task-view:<queryKey>:<offset>`、`job-summary:<jobId>`、`job-detail:<jobId>`、`job-files:<jobId>:local|remote`；facet 缓存 30s TTL。

缓存写者唯一性（owner 表，T2 §④）：`ACPClient.store`（resource-store.js:72 单写路径）✅；`jobsCache/taskViewCache`→queue.js:1291-1292 ✅；`jobDetailCache`→job-detail.js:107 ✅；`projectsCache`→queue.js 双写点（同模块）✅；`nodesCache/backendsCache/softwareDiscoveryCache`→reaction-builder.js ✅（T22 归位 facade）；**`workflowCatalogCache/methodCatalogCache` 声明在 reaction-builder.js:2365-2366、写者在 wizard.js:4194/4198/4217/4226（F-④-1 跨文件 owner 撕裂 → T22 裁定终 owner）**；`folderCache/folderState`→reaction-builder reset + files-logs 填充（保持）；localStorage 5 处（保持）。失效 chokepoints：`invalidateTaskViewMetadata()`（molecule-view.js:335）、非 GET `api()/apiV2()`、命令回执 `job-commands.js:36-44`、详情裁剪 `job-detail.js:44-46`。

### 1.5 SSE / timers（T2 §⑤）

SSE：全树唯一 `EventSource` 构造点 `event-stream.js:144`；页面 opener `reaction-builder.js openSSE@2126`（订阅 17 事件类型 `:2142-2157`）；selectJob step15 先关旧流再开新流 ✅。轮询 4 lane 单 owner `polling-strategy.js`（jobs 4s/15s/60s · summary 2.5s/30s · log 5s · slow 30s）；页面启动 `bootstrap.js:653`。其他 `setInterval` 4 处均有主：job_editor badgeTimer:2696、reaction-builder framePlayTimer:2045、structure_viewer IRC `_ircSetInterval`:1252、scene_instance:139（**页面未装载，F-①-1**）。`setTimeout` 注册表 12 文件 30+ 处均为一次性 debounce（T2 §⑤ 全表）。

### 1.6 viewer 创建/持有/消费者（T2 §⑥）

合法创建点恰 6 处（`ACPVisualization.mol3d.create`）：molecule-view.js:100（主结构）、energy.js:131（能量预览）、nmr.js:726（NMR 候选）、reaction-builder.js:537（反应预览）、wizard.js:1691（向导预览）；销毁点对应（energy.js:2878、nmr.js:358 等）。原生 `$3Dmol` 可执行引用仅 `mol3d_adapter.js`（5 处）+ vendor——**可执行面单驻点 PASS**；注释级出现 2 处（GAP-2-a `energy_chart_renderer.js:11`、GAP-2-b `candidate_review_controller.js:19`，非可执行 → T15/T27 裁定最终断言口径：代码级或容忍注释）。

持有者→消费者（接管核心，全表见 T2 §⑥）：

| 持有者 | 持有位置 | 消费者 |
|---|---|---|
| `viewer`（主结构，原生） | shared-state.js:38 | molecule-view（framing/守卫）、structure_viewer `_canvasViewer("main")`:898-915、vibration_viewer `_getMainViewer`:455-463、structure_editor 桥 `_svLoadXyzToViewer`:1530-1537、energy `_canvasViewerImpl`:138（energy-mini） |
| `currentModel/currentModelAtoms`（原生 model） | shared-state.js:40-41 | molecule-view.js:181/204-205/1063-1071（clickable/highlight/addStyle） |
| `energyGraphState.viewer` | shared-state.js:335 | energy.js 126-154/247-254/2877-2879 |
| `previewViewer`（向导预览，原生） | **声明于 reaction-builder.js:2367**（跨文件） | wizard.js 917/1147/1518-1533/1694-1745/2009-2046/2135-2178、job_editor.js:1912-1914、bootstrap.js:101-115 |
| `reactionViewer`（反应预览，原生） | reaction-builder.js:2389 | reaction-builder 303-324/540-545/632-642/690-860、wizard.js:704-713（dispose） |
| `nmrPanelState.viewers[cand]` | nmr.js:332-341 | nmr.js 342-361/721-735（上限 8） |
| `s2scanState.viewer/resultViewer` | scan-s2.js:16-17 | **无创建点，仅清除（F-⑥-1 死字段 → T25）** |
| 注入 seam `_canvasViewerImpl/_mainViewerImpl/_viewerImpl` | energy/structure_viewer/vibration_viewer | 测试与消费者覆盖点（T8-T14 处理） |
| framing 句柄 4 个 | shared-state.js:39/345、reaction-builder.js:2369/2390 | molecule-view/energy/wizard/reaction-builder |

### 1.7 selectedJobId / selectJob（T2 §⑦）

`selectedJobId`（shared-state.js:23）权威写 1 处（reaction-builder.js:2211，selectJob step1）+ 3 处显式清空（job-detail.js:254 清过期、molecule-view.js:656 删除后、bootstrap.js:32 启动复位）✅。`selectJob`（reaction-builder.js:2193）14 个调用方（bootstrap×2、queue×3、job-detail、wizard×6、tsmode_editor）；内部经 hooks 委托 `taskSession.select`（:2348），每 hook 后 `isCurrent` 会话守卫 ✅。

### 1.8 计划提示行号漂移（T2 §⑧）

DRIFT-1 `wizard.js:3850` 非 viewer 行（实际 `updateInputModeVisibility` 区）；DRIFT-2 `energy.js:2053` 非 viewer 行；计数 41+10 无漂移。其余 15 条提示行号全部 PASS。

---

## 2. 校正与复验结果（T3，计数时点/范围）

时点：**2026-10-18 10:00:20–10:01:21 UTC**（本机 +0800 18:00–18:01），基线 `f410ac9d`，lane venv Python 3.11.13 / pytest 9.1.1，node v20.19.0；复验前后 `git status --porcelain -- frontend/ src/ tests/` 均为空（只读复验）。

| 套件 | 命令 | 计数 | 范围 | 基线对比 | 结论 |
|---|---|---|---|---|---|
| Node 模块 | `node frontend/js/module_tests/run_all.js` | **183 passed, 0 failed**（exit 0） | 17 个 `.test.js`（子套件逐文件计数见 T3 §1） | 183（delta 0） | PASS |
| 前端 pytest 四文件 | `PYTHONPATH=src … -m pytest tests/test_frontend_{integration,sync,structure_picker,candidate_workspace}.py -q` | **508 passed, 1 warning**（16.68s） | 4 文件 = 421+78+6+3 | 508（delta 0） | PASS |
| 扩展回归 | `… -m pytest tests/test_acp_{energy_graph,api_structure_viewer,job_edit}.py tests/test_scan_workflow.py -q` | **303 passed, 1 warning**（64.61s） | 4 文件 = 39+118+82+64 | 303（delta 0） | PASS |

逐模块「模块已验证/页面已接入」复验（T3 §4）：R1/R1b/R2/R3a/R3b/R4a/files-logs 会话纪律/mol3d_adapter **PASS**（HTML 装载 + 委托存在 + 单 init owner，10 个 `.init({` 全树清点无竞争）；**R5 `scene_instance.js` = FAIL（页面已接入判据）**——文件存在、模块测试 9/9 通过，但默认 HTML 未装载；与 closure §3 / index §3.2 一致，属**已知未收口余项（非本次引入回归），路由到 T8 装载、T7 扩展**。

历史 §43/"42.7" 校正事实（T3 §5，逐条 grep 复核；补丁格式见本文 §7）：

| # | 校正 | 判定 |
|---|---|---|
| C1 | 会话实现是 `task_session_controller.js`（HTML:1233），`job_session_controller.js` 全树不存在 | 成立 |
| C2 | 无 `createInstance()`；`mol3d_adapter.js::create()` 直接返回 `$3Dmol.createViewer` 结果；公开 API 仅 `{isAvailable,create,destroy,frame}` | 成立 |
| C3 | `scene_instance.js` 未被默认 HTML 装载（grep 无输出） | 成立 |
| C4 | 历史 Node 计数 45/87/183 与 pytest 508/808 为**不同范围/时点、不可相加**；当前可复跑口径 = 183（17 文件）/ 508（4 文件）/ 303（4 文件） | 成立 |
| C5 | "11 个测试子套件" 陈旧 → 当前 17 个 `.test.js` | 漂移 |
| C6 | "87 passed" 陈旧（更早快照）→ 当前 183 | 漂移 |
| C7 | §42.7 pytest 分解 421/78/6/3=508 与实测一致 | 无漂移 |

---

## 3. F0/F1+S0（含 dirty-state 快照）与资源回退集指针（T1/T4）

### 3.1 F0 / S0 冻结

| 冻结项 | 值 |
|---|---|
| F0 = `frontend/**` 全集 | **77 文件**；聚合摘要（sha256 of 排序 sha256sum listing）= `fe9da69fc7fd59d6d80776d6bc85958b7fe1a1c9e65b6f6463ce11a46f0f185d`（T1 §6；T4 §2 复算一致） |
| 默认页引用 | **51 个**（41 script + 10 css；磁盘缺失 0；无 inline/module/favicon/preload；引用 CSS 无外部 `url()` → 51 即完整拉取集） |
| S0 | `src/**`、`pyproject.toml`、`config/**` 零未提交改动（T1 §6） |
| F1 页面 | `frontend/ACP_Workbench_v2.html`（82008 bytes，sha256 `67f50c9abc929f950b6033f44fc528038912706076970e185cec0a86c60575a8`，served==disk 字节一致） |

### 3.2 dirty-state 快照（T1 §2，恰 9 条 docs-only 未提交项）

```
 M docs/ACP_Architecture_Adjustment_Plan_20261016.md          （主方案 +14 行；架构规划工作，本路不改）
 ?? docs/reference/next-change/ACP_Architecture_Progress_and_Parallel_Index_20261018.md
 ?? docs/reference/next-change/ACP_Parallel_Plan_1_Frontend_20261018.md
 ?? docs/reference/next-change/ACP_Parallel_Plan_2_Services_20261018.md
 ?? docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md
 ?? docs/reference/next-change/ACP_Parallel_Plans_Review_20261018.md
 ?? docs/reference/next-change/ACP_Parallel_Plans_Review_R2_20261018.md
 ?? docs/reference/next-change/ACP_Parallel_Plans_Review_R3_20261018.md
 ?? docs/reference/next-change/ACP_Parallel_Plans_Review_R4_20261018.md
```

全部为文档（1 modified + 8 untracked），归属架构规划/并行协调/评审工作；`frontend/`、`src/`、`tests/` 全程洁净 → F0/S0 以代码洁净为准，不阻塞。

### 3.3 资源回退集指针与回退步骤（T4 §3）

- 机器可查清单（51 行，`kind→path→sha256→bytes→served_via`）：`.omo/evidence/acp-parallel-plan-1-frontend/tmp/t4_resource_listing.txt`；51 条 sha256 全部包含于 T1 的 77 文件 listing。
- TestClient 校验（T4 §4）：`GET /` 200 字节一致；51/51 资源 200、字节一致、`Cache-Control: no-cache`；4/4 代表资源 `If-None-Match` → 304；负控制 404 正常；failures: 0。
- 回退步骤（摘要；完整命令见 T4 §3）：`git checkout f410ac9dae035eb424c40c599692500fdea457cc -- frontend/` → 复跑 `tmp/t4_testclient_check.py` → 复算 F0 聚合摘要 → `systemctl restart acp`；无 git 环境用发布包 frontend tarball 覆盖。**不触碰**：DB/attempt/`WORK/**`/`RESULT/**`、不重发任何命令（pause/purge 等）、远端缓存不动。
- **F-T4-1（LOW，非 T33 阻塞）**：HTML `GET /` 无显式 `Cache-Control`/ETag/Last-Modified；资源级 no-cache 成立，HTML 依赖每次导航拉取。

---

## 4. 测量/未测（T5）

工具：Playwright 1.64.0（chromium headless）驱动**未改动产品代码的** `acp run serve`（隔离 run_root `~/.local/share/acp-frontend-plan1/runs`，端口 8899）；样本 = 3 个合成 COMPLETED 任务（plan1-sample-A/B/C）；网络抓取 = `page.on("request"/"response"/"requestfailed")`。原始工件：`tmp/logs/network-requests.jsonl`（128 请求全量）等（登记见 §9.5）。

### 已实测

| # | 指标 | 结果摘要 |
|---|---|---|
| M1 列表加载 | boot `/api/v2/task-view` ×2（含 facets 首取+30s 缓存命中）；COMPLETED-only 空闲 15s 节奏（6s 窗口 0 次）；每次选中切换 ×1 |
| M2 详情 | 每次选中 `/detail` ×2（taskSession loadDetail + 队列行预取 queue.js:1107）；空闲/隐藏 0 |
| M3 摘要+计数 | `/summary` 2.5s 节奏；drawer 打开 5.5s 内 0（`isDetailDrawerOpen` 门控）；`/api/v1/status` 每 jobs-lane tick ×1 |
| M4 结构 | 选中切换 `/structure-viewer` ×2（step18 + 摘要 lane 结构 tick）；此后 5s ×1 |
| M5 日志 | 选中切换 `/logs?lines=300` ×1 + 5s 游标增量 ×1（stdout_offset/stderr_offset 续读） |
| M6 同 key 并发合并 | 3× 并发 `refreshJobs()` → task-view **3→1**；3× 并发 `fetchJobDetail(A)` → detail **3→1**（ResourceStore in-flight 合并 resource-store.js:72-77） |
| M7 A→B→A 残留 | 每次切换 11 请求结构固定；B 选中期 0 个 A-scoped 请求；0 abort/失败；4 lane 计时器全程 armed；SSE 每选中恰 1 条新连接（4 选中=4 连接，无 after_seq 重连），终态后按契约关闭 |
| M8 隐藏/显示 | hidden 7.5s 仅 1 次 jobs-lane 突发（status+task-view），summary/logs/structure/slow 全静默；真实 `visibilitychange` → 330ms 内全 lane 重启突发 7 请求、generation +2、游标不重复 |
| M9 资源代理 | 峰值 67 请求/500ms（boot=52 静态+15 API）；API 并发上限 6；pageerror ×0 |

四条轮询通道静态交叉校验（源码表，T5 §7）与实测一致。

### 未测（NOT_VERIFIED，显式行）

| # | 指标 | 原因 |
|---|---|---|
| N1 | OS 级进程 CPU/RSS/句柄（服务端 & 浏览器） | 本轮采集口径为请求/定时器/连接面，未接 OS 采样（无 cgroup//proc 定时快照）；未编造数字 |
| N2 | 真实浏览器 UI 切 tab（非 getter 覆盖）的渲染/计时差异 | headless 用 `document.hidden` 覆盖模拟；覆盖语义与页面代码路径一致，但未用真实浏览器切 tab |

文件拆分加速不在本轮承诺范围（未承诺、未测量）。清理回执：服务 PID 221454 SIGTERM 后验证消失、端口 8899 释放（`/sbin/ss -ltn` grep count 0 + netstat/lsof/curl 三重交叉）；生产 8765 未触碰。

---

## 5. 接管表（权威副本；每个未迁移符号→目标 owner）

现状态词汇：**未迁移**（仍为遗留形态）/ **已迁移·单实现**（单源+页面已接入）/ **未接入**（模块就绪但页面未装载）。目标 owner 为并行计划一 todo 编号或「保持」（= 收口后仍是唯一实现或不属本轮迁移范围）。

### 5.1 viewer/实例边界（GAP-2，Wave 1 G1）

| 符号/文件 | 现状 | 目标 owner | 备注 |
|---|---|---|---|
| `js/visualization/scene_instance.js`（受限实例 API） | **已迁移·单实现**（T7 模块 v2 → T8 HTML:1237 装载；T15 生命周期矩阵） | 保持（T15 pin） | R5 页面接入收口点已闭合 |
| `viewer`/`mainViewerFraming`/`currentModel`/`currentModelAtoms`/`viewerModelLoaded`（shared-state.js:38-42） | **已迁移·单实现**（受限实例只读投影，非原生句柄） | 保持（T15） | T8 完成；`currentModel` = opaque token |
| molecule-view.js 主结构调用点（initViewer/clickable/highlight/addStyle 等） | **已迁移·单实现** | 保持（T15） | T8 完成；消费者兼容表（评审 A3）已落实 |
| `energyGraphState.viewer` + `_canvasViewerImpl` 注入（energy.js:131/138/2878） | **已迁移·单实现** | 保持（T15） | T9 完成；dispose 幂等、PNG 导出与 framing 保持 |
| `previewViewer`（唯一声明 reaction-builder.js:2367，消费 wizard/job_editor/bootstrap） | **已迁移·单实现** | 保持（T15） | T10 向导侧迁移；T11 核对：唯一声明在 reaction-builder，wizard 只赋值（非重复声明） |
| `reactionViewer`/`reactionViewerFraming`（reaction-builder.js:2389-2390） | **已迁移·单实现** | 保持（T15） | T11 完成；hover shape/标签回调 + 失效守卫 |
| `nmrPanelState.viewers`（nmr.js:332-341，上限 8） | **已迁移·单实现** | 保持（T15） | T12 完成；per-candidate dispose；`selectedAtoms` 等效 |
| vibration_viewer 动画帧/箭头 + `_viewerImpl` seam | **已迁移·单实现** | 保持（T15） | T13 完成；单 interval 断言；播放/隐藏走实例 |
| structure_viewer `_canvasViewer`/`_mainViewerImpl`/IRC 播放/overlay + structure_editor 桥 | **已迁移·单实现** | 保持（T15） | T14 完成；overlay 经 token；编辑推送不回归 |
| `s2scanState.viewer/resultViewer`（scan-s2.js:16-17/65-66） | **已清理（T25 `6376137` REMOVED-1，2026-10-19 复核 0 命中）** | 关闭 | 死字段删除；pin 改 post-cleanup 极性（字段/守卫清场不得复现）；活体 `s2scanDist/Angle/Dihedral/s2scanResetState` 保留 |
| `$3Dmol` 注释级出现（energy_chart_renderer.js:11 / candidate_review_controller.js:19，GAP-2-a/b） | **已迁移·单实现（可执行面单驻点，T27 已复核）** | 关闭 | T15 pin `test_dollar3dmol_single_station_pin` 落地（容许注释级）；T27 验证 PASS |
| 生命周期矩阵（复挂载/A→B→A/隐藏/销毁迟到回调/多实例/PNG/降级）+ pin | **已建（T15 `4d7d371`）** | 关闭 | `scene_lifecycle_matrix.test.js`（8 行）+ 场景扩展；pin：`$3Dmol` 单驻点、`addModel(` 受限 token；red-ability 已证 |
| FE-09 六创建入口单 adapter | **已迁移·单实现（T27 复核 PASS：features/*.js 创建点 = 0；全部经 `scene_instance.js`→adapter；唯一 raw 驻点 mol3d_adapter.js:34）** | 关闭 | T8/T10/T11/T12 收敛完成；不新建 adapter |
| `framePlayTimer`（reaction-builder.js:2045/2369 区） | **已迁移**（动画归实例 interval） | 关闭 | T8/T11 收敛；`stopFramePlayback` 调用点已适配 |
| structure_viewer IRC `_ircSetInterval`:1252 | **已迁移**（经实例 `play/pausePlayback`） | 关闭 | T13/T14 收敛；清理点 9 处（T2 §⑤） |

### 5.2 向导四片与通用用例（GAP-3，Wave 1 G2）— 终态（T16–T23 交付，T34 关闭）

| 符号/文件 | 现状（终态） | 交付 | 备注 |
|---|---|---|---|
| `js/workbench/features/wizard.js`（原 8304 行整体） | **已迁移·单实现**（四片外置 + 保留壳，HTML:1255 装载） | T16 冻结（零提交）+ T17–T20 提取 + T21 跨片验证 | 串行链 T16→T17→T18→T10→T19→T20 已走完 |
| 上传/解析片 → `wizard/upload-parse.js`（HTML:1251） | **已迁移·单实现** | **T17 `e564c30`** | FormData/`timeoutMs:0`/错误文本保持（T21 pin） |
| 预览/校验片 → `wizard/preview-validate.js`（HTML:1252） | **已迁移·单实现** | **T18 `ec3779b`** | `js/task_input_workspace.js:115,200` 消费 pin 保持 |
| 草稿/表单片 → `wizard/draft-form.js`（HTML:1253） | **已迁移·单实现** | **T19 `7ce58a6`** | `batch_role_defaults.js` 消费保持；显式 0/false/null（T21 pin）；catalog cache 终 owner（下行） |
| 提交/编辑重算片 → `wizard/submit-edit.js`（HTML:1254） | **已迁移·单实现** | **T20 `a35ccfd`** | 编辑 POST `/jobs` 拦截（compat/api.js:14-18）、409/422 保持（T21 pin + T32 A6/A8 真实测） |
| `js/job_editor.js`（编辑重算链） | **已迁移·单实现**（链接口归片，文件保留页根） | **T20**（T10 迁 1912-1914 用法） | ACPJobEditor IIFE 保持 |
| `loadNodes/loadBackends/backendsCache/softwareDiscoveryCache` 及 nodes 匹配 | **已迁移·单实现**（`js/client/system-endpoints.js` + `controllers/system_resources_controller.js`，HTML:1231/1236 装载；reaction-builder :1345/:1813 仅薄委托） | **T22 `3f2b031`** | bootstrap.js:225-226、compat/api.js、job-detail.js 调用者同迁；`/nodes` 503 错误保持（T32-B1 + T30） |
| `workflowCatalogCache/methodCatalogCache`（F-④-1：原声明 reaction-builder:2365-2366、写入 wizard） | **已裁定终 owner：`wizard/draft-form.js:59-60` 唯一声明 + 唯一写者**（`Object.defineProperty` live global view 供 job_editor/reaction-builder/bootstrap 只读） | **T22 `3f2b031`** | 2026-10-19 审计 grep 复核：无第二写者 |
| taskSession 会话核对（files/logs/候选/结果身份、revision、dirty/锁定、迟到） | **缺口已补**（`task_session_identity.test.js` 4 项 + `files_session` +2） | **T23 `c3375ba`** | 只补缺口，未重写第二份 controller |
| `js/candidate_details.js`/`candidate_review_controller.js` 候选面 | 已迁移·单实现 | 保持（T23 复核） | IIFE 导出，无 init |
| `js/structure_source_picker.js`（含 :96 直连 `/api/v2`） | 未迁移（页根遗留） | 保持（迁移范围外；直连点登记） | ACPSourcePicker 唯一入口 |

### 5.3 视图与清理（GAP-5，Wave 1 G3）— 终态（T24–T28 交付/核验，T34 关闭）

| 符号/文件 | 现状（终态） | 交付 | 备注 |
|---|---|---|---|
| `js/visualization/energy_chart_renderer.js` 22 窄委托 + 其他二维视图 | **已复核·纯度 PASS**（renderer 不联网/不读全局任务；修复 `ee0d849`） | **T24 `ee0d849`** | NMR 2D 谱图 N/A→REGISTERED、FE-08 inspector markup 留页为设计内例外（登记 T24 §1.8） |
| `apiV2Remote`（compat/api.js:80，0 产品调用点，F-④-2） | **未删除——登记开放项**（T25 范围为 FE-04/05 五项，未含此候选；2026-10-19 审计 grep：仅 module_tests 注释提及，无产品调用者） | **开放**（后续清理切片） | 删除前零调用者 grep 面=frontend/**+HTML+module_tests+tests+/legacy/；见验收记录 §8-OPEN-3 |
| FE-04 scan-s2 残留闭包 + S2 样式 | **已关闭**（死 viewer 字段 REMOVED-1；旧 S2 模态/闭包零残留，2026-10-19 审计 0 命中） | **T25 `6376137`** | `s2scanDist/Angle/Dihedral` 现用调用者保留；pin 改 post-cleanup 极性 |
| FE-05 退役机理写交互/空钩子（reaction-builder/energy） | **已关闭**（5 项 REMOVED：updateSrPickButtons/allowZeroChanges/buildManualBondChangesPayload/mech-* 提交链等；历史机理只读与 410 保持） | **T25 `6376137`** | 删除前逐项零调用者 grep 取证（task-25 §1） |
| FE-11 CSS 候选（6 个 workbench CSS） | **已归并**（安全重复收敛；computed style + 1280 截图等价，评审 A6） | **T26 `233b096`** | 非选择器文本比较 |
| FE-10 二面角双实现差异（scan-s2.js:89-102 vs geometry_store.js:326） | **已核验·黄金样例保持**（fe10_dihedral_golden 9 项 T30 不变；差异说明落档） | **T28**（验证型，零提交） | 统一需 C02/C07 确认科学约定；不当等价去重 |
| `js/geometry_store.js` | 页根遗留（单命名空间） | 保持（FE-10 黄金样例锚点） | 差异方 |
| `js/tsmode_editor.js` | 页根遗留 | 保持 | 无迁移 todo；tsmode 创建后 selectJob 调用点已登记 |
| `js/memory_input.js` | 页根遗留（IIFE 导出） | 保持 | F-①-2 仅扫描口径记录 |

### 5.4 网络/缓存/SSE/选择（已迁移面保持 + 登记点）

| 符号/文件 | 现状 | 目标 owner | 备注 |
|---|---|---|---|
| `ACPClient.*` 8 模块（client/**） | 已迁移·单实现 | 保持 | init owner：transport/events/taskView/jobDetail/polling=compat/api.js；jobCommands=queue.js:12；wizardEndpoints=wizard.js:355 |
| `ACPWorkbench.controllers.*` 4（controllers/**） | 已迁移·单实现 | 保持 | taskSession init=reaction-builder.js:2206；taskList=queue.js:1263；candidateReview=IIFE |
| `js/workbench/compat/api.js` 入口族（api/apiRemote/apiV2/apiRequest） | 已迁移·单实现 | 保持（apiV2Remote 除外→开放项，见 §5.3） | 编辑 POST `/jobs` 拦截 :14-19；非 GET 失效 :21/:66 |
| 轮询 4 lane / SSE EventSource | 已迁移·单实现（polling-strategy / event-stream） | 保持 | 唯一 EventSource=event-stream.js:144 |
| `selectedJobId`（shared-state.js:23） | 已收敛（权威写 1+清空 3） | 保持（T23 复核） | selectJob→taskSession.select 委托 |
| `jobsCache/taskViewCache/jobDetailCache/projectsCache` | 已迁移·单写者 | 保持 | owner 表见 §1.4 |
| `folderCache/folderState`（reset reaction-builder / fill files-logs） | 未迁移（双文件但职责清晰） | 保持（T23 核对会话规则） | 文件树投影 |
| localStorage 5 处（shared-state/bootstrap/reaction-builder/structure_source_picker） | 未迁移（per-site） | 保持 | T2 §④/§⑦ 登记点 |
| `js/workbench/i18n.js` / `js/workbench/bootstrap.js` / `js/workbench/features/{queue,job-detail,files-logs}.js` / `js/workbench/features/nmr.js`（非 viewer 面） | 已迁移·单实现（各自职责） | 保持（bootstrap 225-226 随 T22 迁调用者） | features 文件保留，不拆 |
| `js/vendor/3Dmol-2.5.5.min.js` | vendor | 保持（仅经 mol3d_adapter 执行） | 无原生 `getModelCount`（T7 注意） |

> 「无 owner」符号：**0**（上表逐行有 owner 或「保持」；保持=收口后仍为唯一实现/范围外，非悬空）。

### 5.5 T15 生命周期矩阵 + 既有发现裁定（2026-10-18）

**矩阵落地**：新增 `frontend/js/module_tests/scene_lifecycle_matrix.test.js`（8 行：反复挂载 / A→B→A / 隐藏-显示 / 暂停-停止 / 销毁+迟到回调 / 多实例相机+选择独立 / 缺 3Dmol 降级 / PNG 导出），并扩展 `energy_viewer.test.js`（复挂载+PNG）、`vibration_playback.test.js`（冷启动箭头）、`nmr_viewer.test.js`（成功路径 textContent）。既有 per-scenario harness（reaction/wizard_preview/nmr/vibration/energy）覆盖各自场景矩阵项。红可证：`/tmp` 移除 `scene_instance.js` 迟到回调守卫后，matrix 5 报 `expected 0 got 3`（matrix 1/2 同红）——断言有牙齿（见证据 task-15 §QA）。

**pin 落地**（`tests/test_frontend_sync.py`）：`test_dollar3dmol_single_station_pin`（可执行面仅 `mol3d_adapter.js`，容许 GAP-2-a/b 注释级）、`test_add_model_result_restricted_methods_pin`（`= ...addModel(` 结果只可用 token 白名单方法）、`test_scan_s2_dead_viewer_fields_pin`（T25 守护）。

**发现裁定（无静默通过）**：

| 发现 | 来源 | 裁定 | owner |
|---|---|---|---|
| (a) `_arrowsVisible()` 鸡生蛋：`arrowState.enabled` 仅在绘制成功后置 true，冷启动静态「箭头」模式画不出箭头（`vibration_viewer.js:539-570`，自 `5a430fc` 既有） | T13 §6 | **recorded-finding（T15 范围外，未修复——T15 禁改产品 JS）**；以 `vibration_playback.test.js::RECORDED FINDING` 显式锁定当前行为（修复后该断言会红并提示更新） | T27/T30（可视化收口复核） |
| (b) `nmr.js:837` 成功路径 `container.textContent = ""`（DOM 语义删除子节点）摘除 3Dmol canvas → 画布不可见 + 复用检查必失败（自 T40 既有） | T12 §3a | **recorded-finding（T15 范围外，未修复）**；以 `nmr_viewer.test.js::RECORDED FINDING` 显式锁定；复用路径的 replace+dispose 语义已由 T12 测试守护 | T27/T30 |

两项均为 **既有缺陷、非迁移引入**；T15 不改产品 JS（共享树协议 + MUST NOT），故只登记 + 加可见测试，不静默通过。

---

## 6. A04/A06 入场门与交接契约摘要（Gated conditional tasks GT-1…GT-5）

阶段规则（评审 P1-2）：A00–A03/A05 实施期间不启用新能力；条件任务在**对应版本交付物通过入场检查后**纳入范围（激活），未激活不得开工。范围状态词汇 `IN_SCOPE / DEFERRED / NOT_APPLICABLE`；证据词汇仍只用 `PASS / FAIL / NOT_VERIFIED`；延期≠通过。新代码须验证**能力缺失时的 S0 回退**；组合记录区分 **F1-base**（本轮 S0 收口前端）与 **F1-integrated**（接入新服务后），不混用 S0 证据。

| 编号 | 条件任务（Plan_1 节） | 入场条件（等待交付物，含提供方） | 文件 owner | 消费测试 | B/C 对接项 | 退出门 | 当前状态（2026-10-18，Wave 0） |
|---|---|---|---|---|---|---|---|
| GT-1 | prepare/operation 客户端（A04） | **B02** DataResourcePort/prepare 契约 + 正常/空/部分/失败/旧版本 fixture **冻结版**（B 路） | `frontend/**`（A） | 契约样例 + 能力缺失 S0 回退 | B02（提供方）；接收/联动项由 B 计划登记 | 契约样例通过 + S0 默认可运行保持 | **DEFERRED**（B02 未交付，版本号待记入本文档） |
| GT-2 | 快照/事件刷新（cursor/去重/重连/快照重置） | **B03/B04** task-view 快照与持久事件协议版本（B 路） | `frontend/**`（A） | 只停被事件覆盖的轮询 + 降级路径 | B03/B04（提供方） | 降级路径可验证 | **DEFERRED**（GT-1 同批冻结后） |
| GT-3 | 科学产品 adapter 接入（A04 消费侧） | **C02/C06** 产品 schema（C 路）+ B 的 frames/VIEW_REGISTRY 投影 adapter 版本 | `frontend/**`（A，消费侧） | 有限展示、不重算权威判据、缺失原因可见 | C02/C06（提供方）+ B 投影 adapter（P1-9 新增任务/子项） | 单位/身份/缺失原因逐项一致 | **DEFERRED** |
| GT-4 | 四组合浏览器测试（F0+S0 / F1-base+S0 / F0+S1 / F1-integrated+S1） | B 集成环境 + S1 交付版本（组合环境就绪） | A 维护测试并提供 S0 证据；B 执行黑盒测试（其 T75）、T69 消费收口 | 每组合证据绑定提交/契约版本/fixture 摘要 | B 侧 T75（执行）/T69（收口）（P1-4） | 每组合有唯一执行任务；发布前门不消费发布后证据 | **DEFERRED**（S1/集成环境未就绪） |
| GT-5 | 账户/配额/项目包 UI（A06） | **B01/B08/B09/B10/B11** 与 M2/M3/M4 范围（B 路） | `frontend/**`（A） | 权限/错误/回退验证 | B01/B08/B09/B10/B11（提供方） | 仅真实接口就绪后启用，保持旧功能可用 | **DEFERRED**（未启用；若本轮未启用，T34 记录「未接入」且不关闭相关产品/UI 目标，P1-2） |

交接契约摘要：等待方标识符集合 = **B02/B03/B04（B 路 prepare/快照/事件）+ C02/C06（C 路 schema/产品）+ B01/B08/B09/B10/B11（B 路 账户/配额/包）**；每一激活在本文档登记版本号与入场检查结论。四组合验收责任（T34 收口）：A 维护测试并提供 S0 证据；B 对选定集成提交执行黑盒测试（T75）；C 在同一集成候选判科学等价；组合以 F0+S0 / F1-base+S0 / F0+S1 / F1-integrated+S1 区分（X3/P1-4/P1-2）。

---

## 7. §43/"42.7" 校正补丁（可应用格式；**已交付 DELIVERED——交付≠应用**）

> **T34 状态记录（2026-10-19）**：本补丁 = **DELIVERED**（随 T6 `db0fcc8` 入库）；主方案文档**仍未编辑**（架构规划工作，本路禁改）。注意 C3 前提已随后续切片变化：`scene_instance.js` 已于 T8 装载默认页（HTML:1239），主方案维护者应用时须将「尚未装载默认页」表述更新为当前事实。交付事实与证据链见验收记录 §5。

**对象**：`docs/ACP_Architecture_Adjustment_Plan_20261016.md` §43（行 1717 起）与 §42.7（行 1721 起）；§44（行 1756 起）已含正确核查结论，本补丁不重复改写 §44。**本节为交付文本，主方案维护者自行应用；本轮不编辑主方案文档**（该文件有未提交改动，属架构规划工作）。

```diff
 --- docs/ACP_Architecture_Adjustment_Plan_20261016.md（基线 f410ac9d 工作区版本）
 +++ 建议修订（依据 T3 证据 §5 C1–C7，2026-10-18 复核）
@@ §43，行 1719 @@
-本节为状态记录，不改 §42 方案本身。R0—R4 与 R5（仅模块）已完成并在默认页面接入，R6 完成首轮清理；
+本节为状态记录，不改 §42 方案本身。R0—R4 已完成并在默认页面接入；R5 仅模块级实现（`scene_instance.js`
+未被默认页装载，见 2026-10-18 索引 §3.2 与 T3 复验）；R6 完成首轮清理；
@@ §42.7，行 1731 @@
-  - `frontend/js/workbench/controllers/job_session_controller.js`：任务选择显式会话令牌自增；针对 A→B→A 快速切换与慢响应迟到提供不可变令牌保护，彻底规避旧请求覆盖新会话。
+  - `frontend/js/workbench/controllers/task_session_controller.js`：任务选择显式会话令牌自增；针对 A→B→A 快速切换与慢响应迟到提供不可变令牌保护，规避旧请求覆盖新会话。（更正 2026-10-18：实际实现文件为 task_session_controller.js，HTML:1233 装载；`job_session_controller.js` 在当前树不存在，全树 grep 0 命中。）
@@ §42.7，行 1741 @@
-  - `frontend/js/visualization/mol3d_adapter.js`：新增 `createInstance(container, options)` 封装模式，返回受限的 `ViewerInstance` 接口（隐藏原生 `$3Dmol` 句柄，提供受限的 `render`、`frame`、`clear`、`exportPng`、`destroy` 操作）。
+  - `frontend/js/visualization/mol3d_adapter.js`：`create(container, options)` 返回 `$3Dmol.createViewer` 原生句柄；公开 API 为 `{isAvailable, create, destroy, frame}`，无 `createInstance()`（更正 2026-10-18：`createInstance` 全库 0 命中，仅 mol3d_adapter.js:57 注释提及"no createInstance/getRawViewer escape hatch"；受限实例 API 单源为 `scene_instance.js`，尚未装载默认页，页面仍持原生 viewer/model——收口由并行计划一 T7/T8 执行）。
@@ §42.7，行 1750 @@
-| Node 模块测试套件 | **87 passed, 0 failed** | 覆盖 client_store、transport、taskView、events_polling、job_session、job_commands_candidate、viz_framing、fe10 等全量 11 个测试子套件 |
+| Node 模块测试套件 | **183 passed, 0 failed**（2026-10-18T10:00:52Z 复验） | 覆盖全量 **17** 个 `.test.js` 子套件（更正 2026-10-18："87 passed/11 子套件"为 2026-10-17 更早快照，已陈旧；历史 45/87/183 与 pytest 508/808 为不同范围/时点计数，互不可相加，当前可复跑口径=Node 183（17 文件）/前端 pytest 508（4 文件）/扩展 303（4 文件）） |
@@ §42.7，行 1752 @@
-| 任务前端兼容重构状态 | **COMPLETED** | R0—R6 全要素落地，网络与共享缓存收拢至 Client，用户会话与交互决策归 Workbench controller，图形边界归 Visualization |
+| 任务前端兼容重构状态 | **COMPLETED（2026-10-17 口径；R5 页面接入为余项）** | 网络与共享缓存收拢至 Client，用户会话与交互决策归 Workbench controller；图形边界仅模块级落地——R5 `scene_instance.js` 未装载默认页、业务仍持原生 viewer/model，wizard 四片为 R6 余项（与 §44 当前结论对齐） |
```

补丁要点（与 T3 §5 对应）：C1 文件名（1731）、C2 无 createInstance（1741）、C3 未装载（1719/1741/1752）、C4 计数不可相加（1750）、C5/C6 子套件数与计数陈旧（1750）、C7 pytest 分解无漂移（不改 1751）。§42.7 行 1751（pytest 508 分解 421/78/6/3）经复核与实测一致，无需修改。

---

## 8. Plan_1 checkbox→todo 覆盖映射（23 条）— 终版（T34，2026-10-19）

来源：`.omo/plans/acp-parallel-plan-1-frontend.md` 尾部映射表（Plan_1 = `docs/reference/next-change/ACP_Parallel_Plan_1_Frontend_20261018.md`）。终版逐条证据见验收记录 `docs/reference/current/ACP_Frontend_Plan1_Acceptance_20261019.md` §7。

| Plan_1 条目 | 覆盖 todo | 终态（2026-10-19） |
| --- | --- | --- |
| A00-1 地图建立 | T2 | ✅ 已交付（T2 证据 + 本文档 §1） |
| A00-2 §43/42.7 校正 | T3、T6 | ✅ 已交付（T3 §5 + 本文档 §7 补丁 **DELIVERED**） |
| A00-3 H/R1–R4 复验 + F0/S0 固定 | T1、T3、T4 | ✅ 已交付（本文档 §2/§3） |
| A00-4 请求/并发/占用测量 | T5 | ✅ 已交付（本文档 §4；N1/N2 NOT_VERIFIED 显式） |
| A01-1 scene_instance 审计扩展 | T7 | ✅ 已交付（`24ffca5`；HTML:1239 T8 装载） |
| A01-2 opts.viewer/addModel 边界 | T7 | ✅ 已交付（`24ffca5`；TEST-ONLY seam + token 白名单 pin） |
| A01-3 逐场景迁移 | T8–T14 | ✅ 已交付（7 个切片提交；§5.1 终态） |
| A01-4 身份/framing/多实例/PNG/降级 | T8–T15 | ✅ 已交付（T15 矩阵 + pin；T31 S7/S8/S9 浏览器复证） |
| A01-5 生命周期验证 | T15 | ✅ 已交付（`4d7d371`；red-ability 已证） |
| A02-1 四片提取 | T16–T20 | ✅ 已交付（T16 冻结 + 4 提交 + T21 pin） |
| A02-2 defaults/校验/保留行为 | T19、T21 | ✅ 已交付（`7ce58a6` + `ea5dd26`） |
| A02-3 loadNodes/loadBackends 归位 | T22 | ✅ 已交付（`3f2b031`；F-④-1 终 owner） |
| A02-4 taskSession 核对 | T23 | ✅ 已交付（`c3375ba`） |
| A03-1 能量 22 委托复核 | T24 | ✅ 已交付（`ee0d849`；纯度 PASS） |
| A03-2 FE-04/05/11 闭包 | T25、T26 | ✅ 已交付（`6376137` 5 项 REMOVED / `233b096` 归并等价） |
| A03-3 FE-09 | T27 | ✅ 已验证（零提交验证型 PASS） |
| A03-4 FE-10 | T28 | ✅ 已验证（黄金样例保持，零提交验证型） |
| A03-5 测试拆分 | T29 | ✅ 已交付（`899c7c0`；525 passed 等效） |
| A05-1 Node/pytest 回归 | T30 | ✅ 已交付（306/525/303/202/80 全绿） |
| A05-2 走查矩阵 | T31 | ✅ 已交付（13+2 全 PASS + 独立复验） |
| A05-3 故障/真实标记 | T32 | ✅ 已交付（表 A/B/C；表 C 全 NOT_VERIFIED） |
| A05-4 发布包 | T33 | ✅ 已交付（`3cfd439` + 发布包文档） |
| A05-5 状态分列/收尾关闭 | T34 | ✅ 已交付（验收记录 2026-10-19 + 本接管表终态 + Plan_1 勾选） |

计数核对：A00×4 + A01×5 + A02×4 + A03×5 + A05×5 = **23** 行，无缺漏。A04（6 条）/A06（4 条）= **DEFERRED / 未接入**（评审 P1-2，不关闭；入场门见 §6）。**A05 退出门保持 OPEN**（T31 §8；终验波 F1–F4 待执行）。

---

## 9. 交接登记（评审 X1/X5）

### 9.1 B-T3/C-T5 共同登记表（引用槽）

X1 要求 B-T3 与 C-T5 共同产出一份**受版本控制**登记表，A-T1/T6 引用同一版本。截至 2026-10-18（本文件成文时点，`ls docs/reference/next-change/` + grep 核实）：**B/C 两路尚未产出该登记表** → 记录为 **pending from B/C lanes (B-T3/C-T5), reference slot reserved**。登记表必备列（X1 原文）：`owner / 可修改范围 / provider / consumer / base_commit / prerequisites / acceptance / applied_commit`。该表交付后，本节升级为对同一版本的逐行引用；三分支对同一登记版本确认前，跨路补丁不得记为已集成。

### 9.2 前端侧逐文件登记（本路 A，base_commit `f410ac9d`）

owner = 并行计划一路 A（frontend/** 写权；B/C 以契约样例或修改请求方式交付，X1 表）：

| 文件族 | owner | 可修改范围 | provider/consumer | 备注 |
|---|---|---|---|---|
| `frontend/ACP_Workbench_v2.html` | A | 装载顺序/装载集 | provider=A（T8/T22 改动）/ consumer=全部前端 | 顺序 pin 在 tests/test_frontend_integration.py:16-40 |
| `frontend/js/client/**`（8） | A | 全部 | provider=A → consumer=features/controllers | 已迁移单实现 |
| `frontend/js/workbench/{contracts,controllers}/**`（6） | A | 全部 | 同上 | 已迁移单实现 |
| `frontend/js/visualization/**`（4：framing/mol3d_adapter/energy_chart_renderer/scene_instance） | A | 全部 | provider=A → consumer=T8–T14 场景 | T7 扩展 scene_instance |
| `frontend/js/workbench/compat/**`（2） | A | 全部 | shared-state 全局面=消费者投影 | apiV2Remote→T25 |
| `frontend/js/workbench/{i18n,bootstrap}.js`（2） | A | 全部 | bootstrap 编排 | T22 迁 225-226 调用者 |
| `frontend/js/workbench/features/**`（9） | A | 全部 | wizard 串行链 T16→T20；energy=T9→T23→T24→T25；reaction-builder=T11→T22→T23→T25；scan-s2=T25 | 文件级单写者 |
| `frontend/js/*.js` 页根 10 文件 | A | 全部 | structure_viewer/vibration_viewer=viewer 消费者（T13/T14）；job_editor=T20；其余保持 | F0 77 文件全集 |
| `frontend/js/module_tests/**` | A | 全部 | provider=A；T7/T15/T21 扩展 | node v20.19.0 |
| `frontend/css/**` | A | 全部 | T26 归并 | computed style 等价（A6） |
| 前端专属 pytest（test_frontend_{integration,sync,structure_picker,candidate_workspace}.py） | A | 全部 | guard 文件 pin 随所属切片提交 | T29 拆分（见 9.3） |

### 9.3 计划新增/拆分测试文件 owner 裁定（开工前，X1 要求）

| 文件 | 裁定 owner | 依据 |
|---|---|---|
| T29 拆分产生的新测试文件（`tests/test_frontend_*` 拆分片） | **A（本路）**，且逐文件在拆分提交中登记 | 拆分自 A 拥有的前端专属 pytest；X1 明示「前端测试拆分后的新增文件…开工前逐文件裁定」，此处预裁定=A，T29 提交时逐文件落名 |
| `tests/conftest.py` | **B 路**；A 仅可提交有来源和基线的补丁 | X1 表：共享测试 fixture → B（`pyproject.toml`、节点依赖、CI、全局架构门、根 AGENTS、共享测试 fixture = B） |
| 打包测试（packaging/wheel 相关） | **B 路**；A 不取得写权 | X1 表：`pyproject.toml`/打包属 B；「禁止仅凭『对应测试』取得写权」；本轮 A 仅有 T26 消费 B 打包补丁的验证行为（见 plan T26），不写打包测试文件 |

### 9.4 跨路补丁状态词汇（X1/X5）

- **已交付（delivered）**：补丁/契约样例已生成并登记（provider 基线 + 摘要）。
- **已集成（integrated）**：接收方**实际应用**该补丁**并通过其测试**后方可标记；未完成应用的跨路补丁不算该切片已集成。
- 本轮（Wave 0，T6 时点）：A 路无对外跨路补丁；对 B/C 的等待交付物见 §6（GT-1…GT-5）。

### 9.5 大日志登记表（X5：URI/摘要/产生提交/环境/命令/退出码）

产生提交：全部为**基线 `f410ac9d` + 工作区（uncommitted 本地产物）**——`.omo/` 不入 git（仓忽略），摘要即完整性凭据。环境：lane venv `/home/redacted-user/.venvs/acp-frontend`（Python 3.11.13）与/或 node `v20.19.0`，按行注明。sha256/bytes 为 2026-10-18 本文件成文时现盘实测。

| URI（相对仓库根） | sha256 | bytes | 产生提交 | 环境 | 命令（产出该工件） | 退出码 |
|---|---|---|---|---|---|---|
| `.omo/evidence/acp-parallel-plan-1-frontend/task-1-acp-parallel-plan-1-frontend.md` | `23235990e401892f4ec9023bc223897fd9d35410d98eb1a3432d9e9c2ec1ac7a` | 15113 | f410ac9d + worktree | lane venv（记录型） | git rev-parse/show/status；stat -f；pip/playwright 安装（见下两行） | 0（全部子命令） |
| `.omo/evidence/acp-parallel-plan-1-frontend/task-2-acp-parallel-plan-1-frontend.md` | `cb9ee952c01f9aca30c000ae0f7ec976291b4f68e2a4eeb6af537df8971ad471` | 84999 | f410ac9d + worktree | node v20.19.0（grep/python 清点） | `python3 -c` 计数 script/link；`grep -rn '\$3Dmol'\|getElementById(...)…` frontend/js | 0 |
| `.omo/evidence/acp-parallel-plan-1-frontend/task-3-acp-parallel-plan-1-frontend.md` | `c091bf845e1f048826a298f95d7ed02afc9dfbb2b393678d71116466dba94f5d` | 17627 | f410ac9d + worktree | lane venv + node v20.19.0 | `node frontend/js/module_tests/run_all.js`；`PYTHONPATH=src /opt/acp2/venv/python -m pytest <四文件> -q`；扩展回归同上；逐条 grep 校正复核 | 0（三套） |
| `.omo/evidence/acp-parallel-plan-1-frontend/task-4-acp-parallel-plan-1-frontend.md` | `5148ee8bac7bff053770f5cab89f822382575368c16b16edaff191d4b55bcf29` | 22576 | f410ac9d + worktree | lane venv | `PYTHONPATH=src /opt/acp2/venv/python tmp/t4_testclient_check.py`；sha256sum 枚举 | 0 |
| `.omo/evidence/acp-parallel-plan-1-frontend/task-5-acp-parallel-plan-1-frontend.md` | `419fc6f2e89086bd5c99b4ae53933cc69adaa1b598f722ba59d3b70a418d04ff` | 20406 | f410ac9d + worktree | lane venv + Playwright | `ACP_RUN_ROOT=<隔离> /opt/acp2/venv/acp run serve --port 8899` + `/opt/acp2/venv/python tmp/capture_requests.py` | 0 |
| `.omo/evidence/.../tmp/pip-install.log` | `887cedf7bad7a504167dd2edef9e25bce45bc79a4d8190758a1049b8c1e03ae9` | 13502 | f410ac9d + worktree | lane venv（pip 22.3.1） | `/opt/acp2/venv/bin/pip install -e '.[dev,api,remote,browser]'` | 0 |
| `.omo/evidence/.../tmp/playwright-install.log` | `bceca26fc40ccad96c5602df6bf1322aa9919f5a12c1b2ea6627d2b249e9d43a` | 7113 | f410ac9d + worktree | lane venv | `/opt/acp2/venv/bin/python -m playwright install chromium` | 0 |
| `.omo/evidence/.../tmp/logs/network-requests.jsonl` | `3fe7f431e0c1f87848000d37da1b82f3bf6164f84d846e17ce9ec4a84881d0ba` | 52239 | f410ac9d + worktree | lane venv + Playwright chromium 156.0.8078.4 | `page.on("request"/"response"/"requestfailed")` 抓取（capture_requests.py） | 0 |
| `.omo/evidence/.../tmp/logs/network-summary.json` | `efad3653d465155f343141bd8b698584b4f165e857a4b6e1d2012822c8d2d026` | 14162 | f410ac9d + worktree | 同上 | 同上（汇总器输出） | 0 |
| `.omo/evidence/.../tmp/logs/derived-metrics.json` | `183a7c550dce429f3ce291c1080f475f92c115da73e8c079a3350c99633385db` | 1627 | f410ac9d + worktree | 同上 | 同上（派生指标：突发/并发/SSE 计数） | 0 |
| `.omo/evidence/.../tmp/logs/service-8899.log` | `04cd2958b92f33216ed846ae66cb5e1875196e5c352ce718b7e03a35f3236e8d` | 38392 | f410ac9d + worktree | lane venv（uvicorn） | `ACP_RUN_ROOT=<隔离> /opt/acp2/venv/acp run serve --port 8899`（后台，任务后 SIGTERM 收） | 143（SIGTERM 终止，预期） |
| `.omo/evidence/.../tmp/logs/cleanup-receipt.txt` | `e4a9d9de53c41167d312ac2aa74095b74a91cbf585e0d0967d6df9fbdf14afee` | 4735 | f410ac9d + worktree | lane venv（shell 检查） | `kill 221454`；`/sbin/ss -ltn`；netstat/lsof/curl 交叉 | 0 |
| `.omo/evidence/.../tmp/t4_testclient_output.txt` | `041aa78ac9ae8e866e59fbed268696e1b12ee63ac32782513eef1518ef8622d3` | 7659 | f410ac9d + worktree | lane venv（httpx 0.28.1 TestClient） | `PYTHONPATH=src /opt/acp2/venv/python tmp/t4_testclient_check.py`（stdout 重定向） | 0 |
| `.omo/evidence/.../tmp/t4_resource_listing.txt` | `41d88e91902cdbd7153032a82d237bb68860bbd46bbfef86284364b10079db34` | 6070 | f410ac9d + worktree | lane venv | 资源枚举脚本（HTML 解析 + sha256sum，T4 §2） | 0 |

登记纪律（X5）：不以「证据路径存在」为唯一验收——上表摘要为完整性凭据；契约/写权/样例/交接状态/验收摘要受版本控制（本文档 §5/§6/§9.1–§9.4 即 tracked 面）；大日志保留在 `.omo/` 稳定工件位置。

---

## 附：本文档维护约定

- 本文档为**接管表权威副本**；T15/T34 更新模块状态，T34 关闭接管表并出五维分列终版（✅ 已完成 2026-10-19：`docs/reference/current/ACP_Frontend_Plan1_Acceptance_20261019.md`，本表 §5.1–§5.4/§7/§8 已同步终态）。
- 「被替代实现已删除」审计（2026-10-19）：重复 viewer 创建 0 命中、旧 owner（job_session_controller）不存在、loadNodes/loadBackends 仅薄委托、catalog cache 单 owner、旧 S2 模态零残留、s2scan 死字段零命中 —— 全 PASS；唯一遗留 `apiV2Remote` 死入口（§5.3 开放项）。审计命令与原始输出见验收记录 §3.2 + T34 证据。
- GT-1…GT-5 激活时在 §6 表更新状态列（DEFERRED→IN_SCOPE）并登记交付物版本号；证据词汇仍只用 PASS/FAIL/NOT_VERIFIED。
- `.omo/evidence/acp-parallel-plan-1-frontend/**` 为日志层（不提交）；本文档引用处均已给出可复核路径与摘要。
