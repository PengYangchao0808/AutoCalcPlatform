# ACP 前端架构 — OpenCode 模块交付与 Antigravity 接线交接包

日期：2026-10-16。写者：OpenCode（frontend/js/{client,visualization,workbench/{controllers,contracts}}/**）。
状态：**模块已验证（三类状态之一，见 §7）**；原页面接入与功能片验收归 Antigravity，按 §2—§5 逐片执行。
上位文档：主方案 v1.12 §38.6—38.10、§40；任务书 `ACP_Frontend_Architecture_OpenCode_20261016.md` OC-0—OC-5。

## 0. 共同基线与并行事实

- 本轮开工基线：HEAD `779a29a` + 用户未提交工作（AR-0 冻结记录与 F0 哈希见
  `.omo/evidence/frontend-arch-20261016/AR0_baseline.md`）。
- **并行期间共享工作区已由 Antigravity 完成两步落地**：
  ① H2 整段外置 `frontend/js/workbench/compat/workbench-inline.js`；
  ② 随即拆分为 `compat/api.js`（api* 全家）、`compat/shared-state.js`（jobsCache/
  selectedJobId 等共享态）、`features/{molecule-view,energy,queue,job-detail,files-logs,
  reaction-builder,nmr,wizard}.js`、`i18n.js`、`bootstrap.js`；主 HTML 1518 行按新序装载。
  OpenCode 模块与其零冲突（新目录未被页面引用，已复核）。
- **当前共享工作区已知失败**：`tests/test_frontend_sync.py` 228 项失败源于 H2 外置
  后测试仍按旧内联结构断言（该测试为 Antigravity 写权范围）；其余基线面绿：
  `test_acp_energy_graph.py`+`test_acp_job_edit.py` 121 passed、
  `test_acp_api_structure_viewer.py` 118 passed。OpenCode 交付前基线同机为 655 passed
  （4 文件），对照证据见 AR0 文档。

## 1. 交付文件与公开 API（全部已验证，加载零副作用）

所有模块为浏览器 IIFE + window 命名空间，ES5 风格；**加载只注册公开能力，不联网、
不轮询、不挂 viewer、不动 DOM**；副作用仅在显式 init/create 调用后发生。
S0 静态可服务性已逐文件验证（200 + text/javascript，见 `s0-static-servability.log`）。

| 文件 | 命名空间 | 公开 API | 版本 |
|---|---|---|---|
| `js/client/transport.js` | `ACPClient.transport` | `init({defaultTimeoutMs,remoteTimeoutMs,translate})`、`request(url,opts)`、`remote(url,opts)` | v20261016.1 |
| `js/client/resource-store.js` | `ACPClient.store` | `subscribe(key,fn)`→退订、`snapshot(key)`、`query(key,{loader,signal,group,isEqual})`、`invalidate(key|"prefix*"|fn)`、`reset()` | v20261016.1 |
| `js/client/task-view-query.js` | `ACPClient.taskView` | `init({v1,v2})`、`buildParams`、`queryKeyOf`、`fetchView(prefs,project,offset,{signal})`、`fetchSummary(jobId,{signal})`、`invalidateAll()`、`invalidateSummary(jobId?)`、`snapshotView/subscribeView` | v20261016.1 |
| `js/visualization/framing.js` | `ACPVisualization.framing` | `frame3DViewer`、`waitForContainerStable`、`scheduleViewerFraming`、`disposeViewerFraming`（四函数与 compat 内实现逐字相同） | v20261016.1 |
| `js/visualization/mol3d_adapter.js` | `ACPVisualization.mol3d` | `isAvailable()`、`create(container,opts)`→viewer\|null、`destroy(viewer)`（幂等）、`frame(viewer,opts)` | v20261016.1 |
| `js/visualization/energy_chart_renderer.js` | `ACPVisualization.energyChart` | `init(deps)` + 22 个 energy 渲染函数（原名导出，见 §4 列表） | v20261016.1 |
| `js/workbench/contracts/task_view_contract.js` | `ACPWorkbench.contracts.taskView` | `validateBody`、`flattenRows`、`rowVersion/sameVersion`、`isActiveStatus/isTerminalStatus`、`capabilities` | v20261016.1 |
| `js/workbench/contracts/fixtures/task_view_samples.js` | `ACPWorkbench.contracts.taskViewSamples` | `row()`、`normal/empty/partialNoFacets/truncatedPage` 样例（合成数据） | v20261016.1 |
| `js/workbench/controllers/task_list_controller.js` | `ACPWorkbench.controllers.taskList` | `init(deps)`、`refresh()`、`setOffset(n)`、`invalidateMetadata()`、`getState()` | v20261016.1 |

专属测试（node，零依赖）：`node frontend/js/module_tests/run_all.js` → **42/42 passed**
（transport 10、store/query 11、framing/mol3d 8、energy renderer 6、controller 7）。
再生成器 `module_tests/generate_energy_renderer.js` 从 compat 源做逐字抽取并输出符号/依赖/外部调用点报告。该生成器已于 2026-10-16 归档至 `tests/baseline/refactor-evidence/generate_energy_renderer.js`：其输入 `compat/workbench-inline.js` 已在后续拆分中移除，**不可再运行**（归档说明见 `tests/baseline/refactor-evidence/frontend-generator-archive.md`）。

## 2. 接线片 A：transport（首片，就绪即接）

**脚本顺序**（在 `js/workbench/compat/api.js` 之前加载；沿用 /js 挂载，无需新路由）：

```html
<script src="js/client/transport.js?v=20261016.1"></script>
```

**init 与窄委托**（在 `js/workbench/compat/api.js` 内，替换原 `apiRequest` 函数体为委托；
`api/apiRemote/apiV2/apiV2Remote` 及 `API_TIMEOUT_MS/API_REMOTE_TIMEOUT_MS` 原样保留。
已复核 compat/api.js 中 apiRequest 实现与 transport 端口逐行为同一实现）：

```js
ACPClient.transport.init({
  defaultTimeoutMs: API_TIMEOUT_MS,        // 8000
  remoteTimeoutMs: API_REMOTE_TIMEOUT_MS,  // 60000
  translate: t,
});
async function apiRequest(url, opts) {     // 同签名窄 shim
  return ACPClient.transport.request(url, opts);
}
```

**必须保持不变的语义（已由 10 项模块测试锁定）**：POST /jobs 编辑拦截位于 `api()` 内
（不经过 transport，保持不动）；写后失效 `invalidateTaskViewMetadata()` 在 `api/apiV2`
包装层（不动）；FormData 正文原样透传；JSON/文本按 content-type 分流；自有超时转为
本地化错误（绝不外泄 AbortError）；调用方 signal 中止保留 AbortError 名（静默取消过滤
继续生效）；8s/60s 场景预算不变；422 数组 detail 展平为 `loc: msg; ...`。

**删除清单（接线时）**：compat 内 `apiRequest` 原实现体（约 10136–10196 行段）。
**回退**：恢复该函数体 + 移除 script 标签；无数据/状态影响。

## 3. 接线片 B：任务列表资源（store/query/refresh 协调）

**脚本顺序**（transport 之后）：`resource-store.js`、`task-view-query.js`、
`contracts/task_view_contract.js`（如 controller 同批：再加 controller）。

**init（compat 内，transport init 之后）**：

```js
ACPClient.taskView.init({
  v1: (url, opts) => api(url, opts),          // v1 前缀与写后失效保持原 api()
  v2: (path, opts) => apiV2(path, opts),      // v2 前缀保持原 apiV2()
});
```

**refreshJobs 切换**（`js/workbench/features/queue.js` 内的 `refreshJobs` 改为
`function refreshJobs(){ return ACPWorkbench.controllers.taskList.refresh(); }` 同签名 shim，
jobsCache 写入经注入回调仍落在 `compat/shared-state.js` 声明的变量上），init 注入：

```js
ACPWorkbench.controllers.taskList.init({
  client: ACPClient.taskView,
  contract: ACPWorkbench.contracts.taskView,
  getPrefs: () => taskViewPrefs || DEFAULT_TASK_VIEW,
  getSelectedProjectId: () => selectedProjectId,
  getLanguage: () => currentLang,
  detailCacheRevision: () => jobDetailCacheRevision,
  renderKeyOf: (parts) => JSON.stringify(parts),
  views: {
    sortGroups: (body, prefs) => { /* 原 refreshJobs 内排序逻辑原样搬入或引用 */ },
    onRows: (flat) => { jobsCache = flat; },        // 页面写者仍是这一处
    pruneDetailsAgainstList: pruneJobDetailCacheAgainstList,
    renderQueue: (counts) => renderQueue(counts),
    renderPagination: renderTaskPagination,
    afterRender: (body, stillCurrent) => { /* 高亮选中行、移动栏、_applyTaskViewPrefsToControls、renderChips、合并建议（用 stillCurrent 代替原 serial 判断） */ },
    onError: (err, retry) => setWorkbenchLoadError("Tasks", err, err && retry),
    onEmptyError: () => { /* 原 queue-counts 失败文案两处 */ },
  },
});
```

**行为对照（已锁定）**：queryKey 变化重置 offset；offset 越界钳制末页并重取一次；
facet 30s 缓存（include_facets=false 复用并在 body 上回填）；jobsCache=分组序扁平行；
渲染键门控（未变化不重渲）；AbortError **与 StaleDiscard** 均静默（后者等价原 serial
守卫丢弃）；`loadTaskViewPrefs` 仍在页侧 getPrefs 前调用。
**旧 owner 删除**：`features/queue.js` 内 refreshJobs 自带的 fetch/serial/abort/facet 逻辑与
`taskViewController/taskViewRequestSerial/taskViewQueryKey`（controller+store 接管）；
`features/molecule-view.js` 内 `invalidateTaskViewMetadata()` 增加
`ACPClient.taskView.invalidateAll()` 调用（merge 建议时间戳逻辑保留页侧）。
**S0 已实测**：合并请求（同 key 单飞）、真实 45 组/72 任务契约校验、invalidateAll 重取
（见 `s0-client-check.log`）。注意 `/jobs/{id}/summary` 对已清理任务 404——原页静默容忍，
controller 将错误交给 views.onError 之外的调用方 catch，行为等同。
**回退**：恢复原 refreshJobs 函数体与状态变量；模块不加载即无副作用。

## 4. 接线片 C：energy 渲染器 + framing + 3Dmol adapter

**脚本顺序**（在 `js/workbench/features/*.js` 之前）：`visualization/framing.js`、
`visualization/mol3d_adapter.js`、`visualization/energy_chart_renderer.js`。

**framing**：`js/workbench/features/molecule-view.js` 中的 `frame3DViewer/waitForContainerStable/
scheduleViewerFraming/disposeViewerFraming` 定义体删除，改四条同名委托：
`function frame3DViewer(v,o){ return ACPVisualization.framing.frame3DViewer(v,o); }`（其余同理）。
四处调用点遍布 molecule-view/energy 等 feature 文件，委托后无需逐点改动。
（逐字校验：四函数与现源内容一致——energy 族 22/22 逐字节一致，framing 四函数仅
IIFE 嵌套缩进差异，空白归一后一致。）

**mol3d adapter**：新代码一律 `ACPVisualization.mol3d.create(container, opts)`（返回 null
时保留原 empty-state 分支）。首片建议只切主结构 viewer 一处（`features/molecule-view.js`
内 `initViewer` 的 `viewer = $3Dmol.createViewer(container, {...})`），其余 `$3Dmol.createViewer`
直调点逐片再切。销毁走 `mol3d.destroy(viewer)`（幂等，内部 `clear()` + WeakSet 防重）。

**energy 渲染器**：先在 energy feature 装配处加 `ACPVisualization.energyChart.init({...})`
（十个 deps，见模块头注释：`t/energyGraphState/s2scanState/currentLang/translateStatus/escapeHtml/
energyGenCandidateName/energyGraphFrameCandidateFor/energyInspectorMetadataRows/
renderTrajectoryAnnotations`——对象按引用共享，页面只改不重赋），再对
`js/workbench/features/energy.js` 内 22 个符号逐个以同名委托替换定义体。外部调用点计数
（替换后需全数走委托）：
energyGraphValue ×30、energyGraphFormatNumber ×26、energyGraphNodeX ×11、
energyGraphChartGeometry ×10、energyGraphSeries ×6、energyChartNiceTicks ×6、
energyGraphMeasureText ×6、energyGraphInspectorMarkup ×6（**未迁出，保持原位**）、
energyGraphSelectedNode ×11、其余 1–4 处（全表见 `energy-renderer-symbols.json`；该表由一次性生成器产出，生成器已归档至 `tests/baseline/refactor-evidence/`，证据描述仍有效）。
**未迁出及原因**：`energyCandidateCardMarkup`、`energyGraphInspectorMarkup` 直接读
`lastSelectedJob`/`s2scanState.candidates`（可变全局任务态，Visualization 禁读）——留在
energy feature，待后续 workbench 候选/检查器 adapter 片再收。`_egMeasureCanvas/_egMeasureCtx/
_egFontFamily` 已随 `energyGraphMeasureText` 私有化入模块；energy feature 原三行在测量函数
委托化后一并删除（避免双实现）。
**验证对照**：同数据前后 SVG 输出一致（帧/节点 id、单位、failed 标记、annotation 传递、
空态文案）；模块测试 6 项含"无网络/无 $3Dmol/无全局任务态"静态断言。
**回退**：删除委托恢复原函数体 + 移除 script 标签。

## 5. 加载顺序总表（全部接入后）

```text
js/vendor/3Dmol-2.5.5.min.js        (async，原样)
js/client/transport.js              ← 片 A
js/client/resource-store.js         ← 片 B
js/client/task-view-query.js        ← 片 B
js/workbench/contracts/task_view_contract.js        ← 片 B
js/workbench/controllers/task_list_controller.js    ← 片 B
js/visualization/framing.js         ← 片 C
js/visualization/mol3d_adapter.js   ← 片 C
js/visualization/energy_chart_renderer.js           ← 片 C
js/geometry_store.js … js/candidate_details.js      (原序不动)
js/workbench/compat/shared-state.js → i18n.js → compat/api.js → features/*.js → bootstrap.js
  (Antigravity 现行装载序原样保留；片 A 委托在 compat/api.js 内)
```

无新构建步骤、无新静态路由；缓存版本用现有 `?v=` 惯例，发布遵循主方案 §38.9
（先备齐新资源再切入口；旧资源保留至兼容窗口结束）。`module_tests/` 与
`contracts/fixtures/` 可被 S0 服务但不被页面引用，无加载副作用。

## 6. S0 载荷与能力缺口（勿自行发明）

- task-view 契约字段与合成 fixtures 见 `contracts/task_view_contract.js` 头注释与
  `task_view_samples.js`；真实 S0 形状已核对（2026-10-16 抓取，仅键名）。
- `capabilities`：S0 有行 revision/name_revision/attempt 与 facets；**无**事件 cursor、
  全局事件流、prepare 可用性（AR-4 后续）。缺这些能力时保持轮询/按任务 SSE，不伪造。
- 未知可选字段忽略；缺必填→对应资源显式报错（validateBody 报 problems，不得空成功）。

## 7. 验证状态矩阵（PASS / FAIL / NOT_VERIFIED）

| 项 | 状态 | 证据 |
|---|---|---|
| 模块行为测试（node，42 项） | PASS | `module-tests-run1.log`/`module-tests-final.log`（transport/store/framing/mol3d/renderer/controller） |
| 逐字抽取校验（对 Antigravity 拆分后新源） | PASS | energy 族 22/22 逐字节一致；framing 4/4 空白归一一致（缩进差） |
| S0 真实 API：task-view 全链（合并/快照/失效/契约） | PASS | `s0-client-check.log`（live 127.0.0.1:8765，只读 GET） |
| S0 静态服务：9 个新模块文件 | PASS | `s0-static-servability.log` |
| 页面级回归：energy_graph/job_edit/api_structure_viewer | PASS（121+118） | 本文档 §0 |
| 页面级回归：test_frontend_sync.py | **FAIL（Antigravity H2 片内因，非本交付）** | §0；接线方修复其断言 |
| 浏览器端到端（接入后 F1+S0 门） | NOT_VERIFIED（待 Antigravity 接入后联合执行） | — |
| F0+S1 / F1+S1 | NOT_VERIFIED（S1 未发布） | — |
| 真实科学软件/LSF | NOT_VERIFIED（本轮不触） | — |

**三类完成状态**：本交付=「模块已验证」。「原页面已接入」与「此功能片已验收」在
Antigravity 按上述清单接线、双验证证后由双方记录；不得以本文件冒充页面已完成。

## 8. 回退要点（每片独立）

每片回退=恢复 compat 内被替换的同名函数体 + 移除对应 script 标签；无 DB/任务/数据
回退；已发命令不因回退重发。资源集与哈希见 AR0 文档；回退后必须复跑该片对应测试面。
