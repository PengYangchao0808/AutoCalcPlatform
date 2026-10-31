# path-view — ACP 3.0 路径结构视图（A-50 / P17）

- 文件：
  - `frontend/js/modules/path-view/module.js`（classic script，无构建、无 ESM）
  - `frontend/css/modules/path-view.css`（仅消费 `frontend/css/workbench/base.css` / `shell/shell.css` 的变量；无全局选择器、无新 token）
  - `frontend/js/module_tests/acp2_path_view.test.js`（Node vm + fake DOM + 注入 request/commands，网络零直连）
- 注册名：`window.ACPNextModules["path-view"]`（唯一新增全局；不写页面全局、不建第二 viewer、不建第二缓存）
- 对应计划：[ACP 3.0 前端计划](../../../../docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md)
  Track A **A-50**（`P17 路径结构视图与编辑`），前置 **A-49**（`mechanism-plan`）与 **A-33**（结构目录）；
  验收要点：**复用 A-45/46 的测量与编辑**——本模块只做“路径读取 + 选定结构”，测量（A-45）、
  内坐标编辑事务（A-46）、资产保存（A-47）全部留在 `molecule-viewer`，不复制控件、不复制 3D 场景。

---

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["path-view"] = {
  descriptor: {
    id: "path-view",
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["path.structures"],
    binds: ["taskRef", "structureRef"],
    capabilities: { requires: [], provides: ["path.structures"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.pathview.errorTitle", messageKey: "next.pathview.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ },
  _internals: { /* 纯函数测试缝，见 §9 */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise<void>`、`updateContext(snapshot)`、`suspend()`、`resume()`、`dispose()`。
`mount` 只接受 DOM host；重复 mount 被拒绝（先 dispose）；dispose 后可重新 mount（状态复位）。
`mount(host, { context })` 的 `context` 与 `deps.context` 等价（Shell panel API 会传）。

`deps`（全部可注入、可 mock）：

| 字段 | 内容 |
|---|---|
| `doc` | DOM document（缺省 `window.document`；测试注入 fake DOM） |
| `request` | 现有客户端单点：`(path, opts) → Promise<parsedBody \| text>`（缺省回落 `deps.client.transport.request` → `window.ACPClient.transport.request`） |
| `context` | Shell 工作区上下文；只消费 `taskRef.jobId`（路径来源）与 `structureRef`（高亮匹配条目） |
| `commands` | Shell 导航命令；只使用 `commands.openStructure(ref)` |
| `initialJobId` | 可选；挂载后直接载入该任务（跳过 context 空态） |

解析优先级：**注入 `deps.request` → `deps.client.transport.request` → `window.ACPClient.transport.request` → 无**。
全部缺失时载入动作给出明确错误且**不发任何请求**（测试 `client_missing` 路径钉住）；模块从不调用
`window.fetch` / `XMLHttpRequest` / `sendBeacon`，也不读写 `localStorage`。

---

## 2. 端点（全部为既有只读端点；本任务没有新增后端）

| 用途 | 请求 | 消费字段 |
|---|---|---|
| 路径身份 | `GET /api/v1/jobs/{jobId}` | `spec.workflow`、`status`、`attempt`、`molecule_name` |
| 驻点结构目录 | `GET /api/v1/jobs/{jobId}/structure-viewer` | `availability`、`revision`、`groups[]`、`entries[]`（`role/status/energy/source.frame_index/source.geometry_ref/source.kind/source.confirmed`）、`warnings[]` |
| 能量图（帧 + 能量 + 选点标注） | `GET /api/v1/jobs/{jobId}/energy-graph` | `view_type`、`default_series`、`series[].unit/label/values`、`nodes[].frame_index/energy/status/x/geometry_ref`、`annotations[].candidate_id/frame_index/y/selection_source/saved/confidence/reason`、`provenance` |
| PES 选点（仅 `workflow === "PESsearch"`） | `GET /api/v1/jobs/{jobId}/pes/review` | `status`、`review.selected[]`（`candidate_id/role/frame_index/name/structure_path/frame_converged`）、`review.revision/confirmed_at/note/profile_sha256`、`review.source.{task_status,frame_source,scan_complete,data_source}`、`backups[]` |
| 机理研究列表（仅 `workflow === "mechanism"`） | `GET /api/v1/mechanism-studies?job_id={jobId}&limit=5` | `id`、`status`、`unified_status` |
| 机理研究报告 | `GET /api/v1/mechanism-studies/{studyId}/report` | `reaction_network.nodes[]/edges[]`、`stationary_points`、`provenance` |

**刻意不调用**：`/jobs`（GET 之外的方法）、`/preflight`、`/batch-optimize/*`、`/frame-candidate*`、`/s2/*`、
`/mechanism-studies/{id}/mechanism/plan`、任何 v2 路由。源码 pin 测试断言只出现上表 6 类路径且请求方法只有 GET。

---

## 3. 四态、守卫与生命周期

根节点 `data-acp-module-state ∈ empty | loading | ready | error`：

| 状态 | 触发条件 |
|---|---|
| `empty` | 无任务上下文/未载入；或载入成功但服务端没有结构/帧（PES 未确认选点时给专属提示） |
| `loading` | 任务读取或次级读取（目录/能量/选点/研究）进行中；overlay 可见 |
| `ready` | 至少有一行驻点结构或帧/步骤；部分数据失败只降级为 warnings |
| `error` | 任务读取失败，或结构目录端点失败/响应不可识别（overlay + 原因 + 重试） |

命令/选择状态独立：`data-acp-busy ∈ none | load`；`data-acp-selection-state ∈ none | sent | unavailable | error`；
`data-acp-path-kind ∈ pes | scan | mechanism | job`；另有 `data-acp-structure-count`、`data-acp-step-count`、
`data-acp-warning-count`、`data-acp-selected-ref`、`data-acp-focus`。

守卫：

- `state.generation`：mount/dispose 递增；任何迟到结果在写 DOM 前丢弃；
- `state.token`：每次载入递增；旧任务/旧载入结果不能覆盖新路径；
- 任务焦点变化：清空旧路径并按新 `taskRef.jobId` 重新载入；焦点清空时不发请求；
- dispose 幂等：移除自有 DOM、context 订阅、`acp:langchange` 监听；dispose 后可 remount；
- `suspend()`/`resume()`：只影响渲染时机，不发起任何请求。

---

## 4. 路径视图模型（显示什么、从哪来）

| 区块 | 行/列 | 来源 |
|---|---|---|
| 概览 | 路径类型 / 任务 / 工作流 / 状态 / 尝试 / 目录 availability / 目录 revision / PES 选点状态 / 能量序列 | job + catalog + review + energy |
| 驻点结构 | 角色、名称、帧、能量、来源、操作 | 目录条目（服务端顺序）+ 机理报告节点；能量按 `frame_index` 与能量图节点/标注连接 |
| 帧 / 步骤 | 任务路径：帧号、能量、状态、几何引用；机理路径：source/sink/TS、正/逆能垒、状态 | 能量图 `nodes[]` 或研究报告 `reaction_network.edges[]` |
| 溯源 | 任务/工作流/状态/尝试、目录 availability+revision、能量图 view+默认序列、`pes_review.source`、研究 ID、报告 `provenance` JSON（截断 320 字符） | 六个端点的原始字段 |

规则：

- **不排序、不排名**：结构保持目录/报告的服务端顺序；帧/步骤保持能量图节点顺序或报告边顺序。
- **不推断化学**：`classifyRole` 只做词形归一（`transition_state`/`ts`→TS、`react*`/`start*`→反应物、
  `prod*`/`end*`→产物、`int`/`inter*`/`endpoint`/`minimum`→中间体/最低点），无法识别进 `unknown`。
- **缺失即声明**：能量缺失显示 `—`，不补 0；结构引用缺失时按钮禁用并给出 `next.pathview.ref_missing_title`。
- **不写任务**：全部是 GET；本面板没有任何提交/命令面（除 `commands.openStructure` 导航）。

### 4.1 结构选择与 canonical structureRef（交给现有 viewer）

选中一行 → `deps.commands.openStructure({ source: "path-view", ...ref })`，ref 为冻结形状之一：

| 结构来源 | 发出的 ref |
|---|---|
| 结构目录条目 | `{kind:"structure-entry", jobId, entryId:<entry.id>, frameIndex:<source.frame_index \|\| 0>}` |
| 机理报告节点 `structure_ref` | 逐字段校验后的 `structure-entry` / `job-file` / `job-remote-file` / `inline-xyz`（未知 kind 拒绝） |
| 机理报告节点 `entry_id` | `{kind:"structure-entry", jobId:<path job>, entryId:<entry_id>, frameIndex:0}` |
| 两者都没有 | 不发送；按钮禁用 + 显式原因 |

- 不在本模块构造几何 URL、不按 `geometry_ref` 猜条目、不创建第二个 3D 栈；`structure-entry` 由
  `molecule-viewer`（A-33）用既有 geometry 端点解析。
- 未注入 `commands.openStructure` 时选择状态为 `unavailable`，提示“未注入 …；未发送任何查看请求”，绝不回退到自建 viewer。
- 命令返回值只用于回执说明（`via` / `targetInstanceId`）；本模块**不写** context focus（命令内部已写，
  避免双向回写反馈）。
- 面板常驻一句“本模块不提供测量 / 编辑控件（A-45/A-46 专属）；也不创建第二个 3D 查看器”。

### 4.2 PES 路径

- `workflow === "PESsearch"` 时额外读 `pes/review`；`status === "pending"` 且目录无条目 → `empty` +
  `next.pathview.empty_pes_pending`（明确“先确认选点”，不显示推荐点、不伪造候选）。
- 目录条目的 `role`（`ts` / `endpoint`）与 `pes_review.selected[].role`（TS/INT）按 `frame_index` 连接：
  已确认条目显示 candidate_id 与 `selection_source=manual`；能量优先目录 `energy.value`，否则能量图帧能量，
  否则标注 `y`，单位随默认序列（如 `kcal/mol` / `Eh`）。
- 能量图默认序列取值顺序：`default_series` → 第一个可绘制序列 → 第一个序列；显示 `label [unit]`。

### 4.3 机理路径

- `workflow === "mechanism"` 时先 `GET /mechanism-studies?job_id=`（limit 5），取第一条研究再读 `report`；
  报告不可用 → 仍 `ready`（若目录有结构）并写 warning。
- 节点合并策略：`reaction_network.nodes[]` 优先，`stationary_points.stationary_points[]` 按 id 补缺；
  能量字段依次取 `canonical_energy_hartree` / `energy_hartree` / `energy`。
- 步骤取 `reaction_network.edges[]`；没有边时回退到能量图帧序列。
- 研究报告可能是历史（retired mechanism 工作流）数据；**只有报告显式携带 `structure_ref` 或 `entry_id` 的节点可打开**，
  其余显示“服务端未提供结构引用”（不猜 `study_dir`、不拼文件路径）。

---

## 5. 局部降级（不把部分失败伪装成 ready 成功）

| 情况 | 结果 |
|---|---|
| `job` 读取失败 | `error` + 原因 + 重试 |
| `structure-viewer` 失败 / 响应不可识别 | `error`，文案“结构目录端点不可用（status）：…；未使用任何替代端点” |
| `energy-graph` 失败 | `ready`（目录行仍在）+ warning；能量列显示 `—` |
| `pes/review` 失败（PES） | `ready` + warning；不伪造 confirmed |
| 研究列表/报告失败 | `ready`（目录行仍在）+ warning；机理节点缺失 |
| 目录 `availability=pending_fetch` | 目录行按服务端返回（通常为空）；warning 原样保留 |
| 空任务/空目录/空能量图 | `empty` + 对应提示（PES pending / 无结构 / 研究无数据） |

---

## 6. i18n（zh 内联兜底 + `data-i18n`，共 81 条）

模块自带 `ZH` 字典（Shell 字典缺 key 时 `t()` 回退本地 zh；`ACPShell.i18n.t` 只回显 key）。
静态文案带 `data-i18n`；动态文案（插值/错误）保留 zh 并随 `acp:langchange` 重绘。

需要在 `frontend/js/shell/i18n.js` 补的 key（zh + en，共 **81** 条；zh 值与模块内联文本逐字一致）：

```
next.pathview.title
next.pathview.subtitle
next.pathview.source_title
next.pathview.source_hint
next.pathview.job_id
next.pathview.job_id_ph
next.pathview.load
next.pathview.reload
next.pathview.summary_title
next.pathview.summary_kind
next.pathview.summary_job
next.pathview.summary_workflow
next.pathview.summary_status
next.pathview.summary_attempt
next.pathview.summary_catalog
next.pathview.summary_catalog_revision
next.pathview.summary_review
next.pathview.summary_series
next.pathview.structures_title
next.pathview.structures_hint
next.pathview.structures_empty
next.pathview.steps_title
next.pathview.steps_hint
next.pathview.steps_empty
next.pathview.provenance_title
next.pathview.provenance_hint
next.pathview.provenance_empty
next.pathview.selection_title
next.pathview.selection_none
next.pathview.selection_sent
next.pathview.selection_unavailable
next.pathview.selection_error
next.pathview.measure_note
next.pathview.open_structure
next.pathview.ref_missing
next.pathview.ref_missing_title
next.pathview.no_commands
next.pathview.empty_title
next.pathview.empty_hint
next.pathview.empty_no_structures
next.pathview.empty_pes_pending
next.pathview.empty_no_study
next.pathview.loading_title
next.pathview.error_title
next.pathview.errorTitle
next.pathview.errorMessage
next.pathview.retry
next.pathview.partial
next.pathview.partial_detail
next.pathview.err.client_missing
next.pathview.err.catalog_endpoint
next.pathview.err.invalid_catalog
next.pathview.kind_pes
next.pathview.kind_scan
next.pathview.kind_mechanism
next.pathview.kind_job
next.pathview.kind_unknown
next.pathview.role.ts
next.pathview.role.reactant
next.pathview.role.product
next.pathview.role.intermediate
next.pathview.role.minimum
next.pathview.role.unknown
next.pathview.none
next.pathview.no_energy
next.pathview.unit_hartree
next.pathview.unit_kcal
next.pathview.source_manual_review
next.pathview.source_formal_result
next.pathview.source_mechanism
next.pathview.source_other
next.pathview.prov_job
next.pathview.prov_workflow
next.pathview.prov_status
next.pathview.prov_attempt
next.pathview.prov_catalog
next.pathview.prov_energy
next.pathview.prov_review
next.pathview.prov_review_source
next.pathview.prov_study
next.pathview.prov_report
```

---

## 7. 测试与实测证据

命令（任务指定的 node.exe）：

```bash
"C:\Users\彭扬超\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" \
  frontend/js/module_tests/run_all.js acp2_path_view
```

结果（2026-10-31 实测）：**25 passed / 0 failed，exit 0**。

覆盖：

1. 注册面：唯一 `ACPNextModules` key、`ACPShell.registry.validateDescriptor` 通过、无 legacy 全局；
2. `empty` 初始态、零请求、zh 标题、**无测量/编辑控件、无 canvas**；
3. 手动载入：`loading`/`busy=load` overlay → `ready`；精确 GET 路径与顺序（job → catalog → energy → pes/review）；
4. ready 视图：概览字段、驻点行（role/id/frame/energy/source/candidate_id）、帧步骤、溯源（review revision /
   frame_source / scan_complete / 能量序列）；
5. PES pending：empty + 明确“先确认选点”，零假候选；
6. 选择：`commands.openStructure` 精确收到 canonical `structure-entry` ref（job/entry/frameIndex/source），
   `data-acp-selected-ref` 可回读；回执注明 A-45/A-46 归属；模块不写 context；
7. 未注入命令：`unavailable` + 明确原因 + 零请求；
8. 机理路径：研究列表 + 报告、五类角色、两条基元步骤、溯源 JSON；缺 `structure_ref` 的节点禁用，
   `structure_ref` / `entry_id` 各自生成 canonical ref；
9. 扫描路径：目录能量 + 帧顺序（无客户端排序）；
10. 空任务路径、错误 + 重试恢复、**目录端点 404**（显式错误 + 无替代端点）、能量/选点局部失败（warning）；
11. 迟到结果：dispose 后不重挂/不画；A→B 切换后旧任务响应被丢弃；
12. dispose 幂等、context 退订、语言监听移除、可 remount；context 自动载入/清空零请求/切换任务；
13. `structureRef` 焦点高亮；无 client seam 的错误路径；
14. 纯函数：路径编码、六类路由常量、各 normalize 防御性、`buildPathModel` 连接/顺序/ref 缺失、
    `canonicalRef` 白名单、`refKey`；
15. i18n：源码每个 `next.pathview.*` 字面量都在本地字典，`data-i18n` 全为模块自有键；
16. 源码 pin：无 fetch/XHR/sendBeacon/localStorage/innerHTML、无 `$3Dmol`/scene/geometry store、
    无 `/api/v2`/`/preflight`/`/frame-candidate`/`/batch-optimize`、无 POST；唯一导航是
    `commands.openStructure`。

### 后端契约核对（静态源码 + 请求/响应形状，不是真实 HTTP）

- 六个端点的路径与响应字段来自 `src/acp/api/v1_routes.py`（`structure-viewer` 目录 + geometry、
  `energy-graph`、`pes/review`、`mechanism-studies`、`job`）；
- `structure-entry` 冻结形状与 `molecule-viewer` README §2 一致（jobId/entryId/frameIndex）；
- `pes_review_v1` 字段来自 `src/acp/api/v1_schemas.py::PesReviewStateResponse` 与
  `src/acp/calculations/pes/review.py`；`pes/review` 对非 PES 任务返回 400、对历史 mechanism 返回 410，
  模块只在 `workflow === "PESsearch"` 时请求它；
- 机理报告是 `network.json` / `stationary_points.json` / `provenance.json` 的只读投影
  （`v1_routes.py::_build_mechanism_report`），历史研究可能没有结构引用——模块按缺失显式降级。

---

## 8. 接入下一步（本次文件边界外，未改动）

1. `frontend/next/index.html`：`<head>` 加 `<link rel="stylesheet" href="/css/modules/path-view.css">`；
   脚本区加 `<script src="/js/modules/path-view/module.js"></script>`。`ACPShell.bootstrap.boot()` 的
   `collectFrom(window.ACPNextModules)` 自动收编注册。
2. `frontend/js/shell/recipes.js`：为 Research 工作区选择槽位（建议与 `mechanism-plan` / `reaction-network` 同列），
   当前**未接默认 recipe**，默认布局不会自动显示。
3. `frontend/js/shell/i18n.js`：补 §6 的 81 条 key（zh + en；en 缺失前静态元素在 en 下显示 zh）。
4. 浏览器用例：`acp2_next_entry.browser.js` 增加“PES ready → 选中 TS → viewer 定位 → 测量抽屉可编辑”
   场景（本次未执行浏览器套件，不计 PASS）。
5. parity 记录：A-50 的「模块注册 + 四态 + 测试 + 键组覆盖」四项中键组依赖第 3 步；其余三项已完成（代码/契约层）。
6. 若 Shell 的 panel API 提供 per-module deps，把 `commands` 注入本模块（当前 `layout-manager.hostApi()`
   是否透传 `commands` 需集成时核对；缺失时模块给出 `no_commands` 显式降级，不会崩溃）。

---

## 9. 纯函数测试缝（`_internals`）

`classifyRole`、`normalizeJob`、`normalizeCatalog`、`normalizeEnergyGraph`、`normalizePesReview`、
`normalizeStudyList`、`normalizeMechanismReport`、`canonicalRef`、`structureRefForEntry`、
`structureRefForNode`、`refKey`、`buildPathModel`、`jobPath`、`catalogPath`、`energyPath`、
`pesReviewPath`、`studiesQuery`、`studyReportPath`、`roleLabelKey`；常量 `STRUCTURE_KINDS` /
`STUDY_LIMIT(5)` / `ROUTES` / `i18n`（只读 zh 字典）。

测试通过这些函数钉住服务端数据 → 面板模型（连接、顺序、ref 生成/拒绝），DOM 测试再钉住状态、事件、
请求次数与错误呈现，两者互补。

---

## 10. 边界 pin（源码级，测试守护）

- 只写 `window.ACPNextModules` 一个全局；无 `ACPWorkbench`/`ACPView`/`ACPClient` 写入；
- 无 `fetch(` / `window.fetch` / `XMLHttpRequest` / `sendBeacon` / `localStorage` / `sessionStorage`；
- 无文件系统路径构造（不读 `work_dir`、不拼 `RESULT/...`、不读 `study_dir`）；
- 无 `$3Dmol` / `ACPVisualization` / `scene_instance` / `createViewer` / `canvas` / `ACPGeometryStore` /
  `structure_editor` / `geometry_store`（A-45/A-46/A-47 面）；
- 无 `innerHTML` / `insertAdjacentHTML` / `document.write`；
- 请求只允许 §2 的 6 类 GET 路径；无 POST。

---

## 11. 已知缺口 / 未验证

- **未在真实浏览器执行**：只跑 Node vm 套件；`next/index.html` 未加载本模块（文件边界限制），真实页面
  挂载/点击/布局 interaction 为 **NOT_VERIFIED**。
- **未对真实 HTTP 服务执行**：所有端点响应来自注入 seam；真实服务上的 403/项目隔离、远端 `pending_fetch`、
  大体量 `energy-graph` 未验证。
- **机理报告可用结构有限**：历史 mechanism 报告不保证带 `structure_ref` / `entry_id`；没有引用的节点只能
  展示角色/能量，不能打开查看器（显式文案，不猜 geometry 端点）。
- **PES 推荐点不可见**：`/pes/review` 只暴露已确认选点；未确认时本面板显示 empty，而不是把
  `pes_recommendations.json` 的审计推荐当成候选（A-51 也遵循同一约束）。
- **无分页/虚拟化**：很多帧时一次性渲染全部行（A-50 范围内不做 1k 帧虚拟化）。
- **无编辑/测量**：按 A-50 验收说明复用 A-45/46，本模块刻意不提供；若后续需要“在路径视图内测量”，
  必须复用 viewer 的测量状态，而不是在此复制数学实现。
- **zh-only**：Shell 字典尚无 `next.pathview.*`；en 显示 zh（A-61）。
- **未改共享文件**：`frontend/next/index.html`、`frontend/js/shell/*`、`recipes.js`、既有模块/测试均未修改；
  没有 git commit。
