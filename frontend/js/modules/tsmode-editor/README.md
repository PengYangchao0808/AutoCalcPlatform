# tsmode-editor — ACP 3.0 TS 模式优化编辑器（A-16 / P05）

- 文件：
  - `frontend/js/modules/tsmode-editor/module.js`（classic script，无构建、无 ESM）
  - `frontend/css/modules/tsmode-editor.css`（仅消费 `frontend/css/workbench/base.css` / `shell/shell.css` 的变量）
  - `frontend/js/module_tests/acp2_tsmode_editor.test.js`（vm + fake DOM + 注入 request，网络零直连）
- 注册名：`window.ACPNextModules["tsmode-editor"]`（唯一新增全局；不写页面全局、不建第二 viewer、不建第二缓存）
- 对应计划：[ACP 3.0 前端计划](../../../../docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md)
  §5.V4 / Track A **A-16**（`P05 TS Mode 编辑器`，计划里的目标名是 `tsmode-panel`，本模块 id 按任务要求取 `tsmode-editor`）；
  前置 **A-06**（提交与回执）；验收要点：**模式序号非位置序**。
- 旧实现对照：`frontend/js/tsmode_editor.js`（`window.ACPTsmodeEditor`，三面板 modal：来源 / 模式 / 设置）。
  本模块**不定义**该全局、不复制它的 `fetch` 直连，也不复用它的 `t()` 全局依赖。

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["tsmode-editor"] = {
  descriptor: {
    id: "tsmode-editor",
    interfaceVersion: 1,
    version: "0.1.0",
    dependencies: [],
    provides: ["tsmode.editor"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["tsmode.editor"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.tsmode.errorTitle", messageKey: "next.tsmode.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ },
  _internals: { /* 纯函数测试缝，见 §10 */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise<void>`、`updateContext(snapshot)`、`suspend()`、`resume()`、`dispose()`。

`deps`（生产由 `layout-manager.hostApi()` 直接传入，测试可全部注入）：

| 字段 | 内容 |
|---|---|
| `doc` | DOM document（缺省 `window.document`；测试注入 fake DOM） |
| `request` | 现有客户端单点：`(path, opts) → Promise<parsedBody>`（缺省回落 `deps.client.transport.request` → `window.ACPClient.transport.request`） |
| `context` | Shell 工作区上下文；只用 `taskRef.jobId` 与 `projectId`（`subscribe` 变更） |
| `commands` | 可选；仅回执后的 `commands.openTask({jobId, attempt, source})` 定位任务（导航，不是变更） |
| `client` | 可选；`{ transport: { request } }` 形式的客户端对象 |

解析优先级：**注入 `deps.request` → `deps.client.transport.request` → `window.ACPClient.transport.request` → 无**。
全部缺失时加载 `frequency-sources` 直接进入 `error`（`next.tsmode.err.client_missing`），**不发任何请求**；
模块源码从不出现 `fetch(` / `XMLHttpRequest` / `sendBeacon`（测试 pin 住）。

## 2. 端点与契约（全部为既有端点；没有为 A-16 新增后端）

| 用途 | 请求 | 说明 |
|---|---|---|
| 频率来源（候选） | `GET /api/v1/jobs/{job_id}/frequency-sources` | `v1_routes.py::get_frequency_sources`（L4312–4341）→ `{job_id, sources[], warnings[]}`；`sources[]` 含 `entry_id / output_path / hess_path / hessian_available / imaginary_count / atom_count / mode_count` |
| 模式列表（虚频） | `GET /api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/vibrations` | `v1_routes.py::get_structure_viewer_vibrations`（L4667–…）→ `{available, reason, modes[], atom_count, source, imaginary_count}`；`modes[]` 含 `mode_index / frequency_cm1 / imaginary / ir_intensity / vectors` |
| 预览 / 校验 | `POST /api/v1/preflight` | 既有零副作用 wizard 预检（L9404）。内部执行与提交同源的字段策略/工作流语义校验 + `_preflight_source_facts`（L9053）的 tsmode 来源可行性；返回 `feasible/reasons/advisories/plan_digest` |
| 提交 + 回执 | `POST /api/v1/jobs`（201） | 既有任务提交端点（L2663）；**tsmode 分支**（L2772–2782）调用 `_prepare_tsmode_input`（L2251–2405）重新校验来源/Hessian/原生模式/launch gate，并把 input 重写为 `source_type:"tsmode_bundle"` |

### 2.1 为什么这是"即有的 tsmode 提交入口"（记录判断依据）

- `src/acp/api/v1_routes.py` 的 104 条路由中**不存在** `POST /tsmode/...` / `tsmode/submit` / `tsmode/preview`；
  tsmode 的唯一提交路径是通用的 `POST /api/v1/jobs`，其 `workflow == "tsmode"` 分支执行 tsmode 专属准备
  （`_prepare_tsmode_input`：源任务 → 频率条目 → Hessian → `resolve_target_mode` → `enforce_launch_gate` → 冻结 bundle），
  旧 `frontend/js/tsmode_editor.js::submit()` 走的也正是这条路径。
- 本模块因此**不发明路由**：提交只在预检通过后走 `POST /jobs`；预检失败/本地校验失败时 `/jobs` 不会被调用。
  若后续出现专用 `tsmode/*` 预览或提交路由，应替换本模块的端点常量，而不是再写第二套提交逻辑。

### 2.2 模式序号语义（A-16 验收要点）

- `mode_index` 是 **ORCA 输出的原生模式编号**：`acp/results/frequencies.py::build_normal_modes_product`
  明确 "using ORCA's native indices (never reindexed)"（L25–30、L48、L88），`acp/results/orca_parser.py`
  也按打印编号解析（`parse_ts_frequency_map` / NORMAL MODES 列头）。
- 服务端按同一编号解析目标：`cccp/analysis/tsmode/mode_mapping.py::resolve_target_mode(bundle, source_mode_index)`
  用 `bundle.mode_by_index(source_mode_index)` 查找并拒绝非虚频/不存在/向量不完整的模式（L130–157）。
- 因此本模块：
  - 行身份用 payload 的 `mode_index`（`data-mode-index`、`aria-label`、详情行都显示 `#6` 这类原生编号）；
  - 选中态存 `selectedModeIndex = Number(mode.mode_index)`；
  - 组装 `input.source_mode_index` 时只用这个值，**任何数组下标/行号都不参与**；
  - 列表按原生编号升序展示（`normalizeModes` 排序），不重编号、不压缩、不补造缺失编号；
  - 只把 `imaginary === true` 的模式作为可选目标（与服务端 `resolve_target_mode` 的规则一致）。
- 测试用非连续编号 `6 / 9 / 13`（另有实频 `14`）钉住：选中第 3 行必须提交 `13`（不是 `2`），选中第 1 行提交 `6`（不是 `0`）。

### 2.3 预检请求体（模块实际发送）

```json
{
  "workflow": "tsmode",
  "name": "TSMode_alpha_mode9",
  "input": {
    "source_job_id": "freq-001",
    "entry_id": "alpha",
    "source_mode_index": 9,
    "allow_unverified_mapping": false,
    "final_frequency": true
  }
}
```

- `input` 只用 `submission_contract.py::_TSMODE_INPUT_KEYS`（L241–260）声明过的字段；`source_mode_index` 是原生编号。
- **不发送 `method`**：`WorkflowContract("tsmode")` 的 `method_keys=frozenset()`（L566–571），任何 method 键都会 422。
- **不发送** `resources` / `tags` / `project_id` / `level_overrides` / `bundle`：本面板不拥有资源/项目/级别覆盖；
  项目由服务端从请求上下文解析。`bundle` 只由 API 在提交时生成。
- 可选调参只有 `max_iterations`（严格整数 ≥1；留空 = 服务端默认）。`recalc_hess` / `trust_radius` / `retry_limit`
  经 HTTP 预检链存在后端契约矛盾（见 §9），模块**不提供**这些控件，也不发送它们。
- `allow_unverified_mapping` 始终显式发送（默认 `false`），`final_frequency` 始终显式（默认 `true`）。
- `name` 只影响显示名，不进入计划摘要：`_plan_digest` 只序列化 `workflow/input/method/resources`
  （`v1_routes.py::_plan_digest` L8636–8660 + `canonical_digest.frozen_plan_definition`），因此预检体与提交体同名同摘要。

### 2.4 提交请求与回执

提交体 = 预检体 + `preflight_fingerprint: <plan_digest>`；headers：

```
Content-Type: application/json
Idempotency-Key: next-tsmode-<uuid 或 时间戳随机>
```

- 键在同一 plan 内复用（`keyPlan === preview.planKey`），plan 改变后重新生成 → 重试不产生第二任务；
- 201 回执消费 `job_id / status / attempt / plan_digest / idempotent_replay`，渲染
  `任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试` 与计划摘要；`idempotent_replay` 时显示幂等重放提示；
- 同一 plan 已成功后又点提交 → 客户端直接拒绝（`receiptKey === planKey`），不发第二个 `POST /jobs`；
- 提交错误映射：
  - `400 + detail.code === "preflight_stale"` → 清空预检并要求重新预检（不自动重放）；
  - `422 + detail.error === "mode_mapping_unsupported"` → 显示"服务端拒绝：模式映射未验证…勾选允许未验证映射后重新预检"；
  - `mode_mapping_ambiguous` / `target_mode_invalid` / `frequency_source_incomplete` → 各自可读文案 + 服务端 detail；
  - `403` / 其它 → 服务端 detail（`describeError` 同时兼容 `{error,detail}`、`{code,message,params}`、字符串 detail）；
- 提交失败**不**显示成功回执、不自动重试、不导航；成功回执的"在任务面板打开"按钮才调用 `commands.openTask`（仅导航）。

## 3. 四态、子状态与守卫

模块根 `data-acp-module-state` ∈ `empty | loading | ready | error`（频率来源这一层的数据状态）：

| 状态 | 触发条件 |
|---|---|
| `empty` | 未绑定任务（上下文无 `taskRef.jobId`），或来源加载完成但 `sources: []` |
| `loading` | `frequency-sources` 读取中（初始 idle 也按 loading 呈现） |
| `ready` | `frequency-sources` 返回至少一个来源（即使全部缺 Hessian：列表可见但不可选） |
| `error` | 来源读取失败 / 客户端未注入 / 任务 404（overlay + 原因 + 重试） |

独立子状态：

- `data-acp-modes-state` ∈ `idle | loading | ready | unavailable | error`（模式列表这一层）；
  `unavailable` 表示服务端 `available:false`，`reason` 原样展示（`pending_fetch` / `no_normal_modes` 有中文兜底）；
- `data-acp-busy` ∈ `none | preview | submit`（命令忙碌态；预检/提交期间不禁用表单，但两个按钮禁用、重复点击被忽略）；
- `data-acp-selected-entry` / `data-acp-selected-mode` / `data-acp-allow-unverified` / `data-acp-valid` / `data-acp-task`。

守卫：`state.generation`（mount / dispose）+ 每类请求的 token（`sources.token`、`modes.token`、`preview.token`、`submit.token`）。
任何迟到结果在写 DOM 前被丢弃；`dispose()` 幂等，移除自有 DOM、context 订阅、`acp:langchange` 监听，
并清空任务绑定（同一实例可再次 `mount`，会重新读取来源）。

## 4. 频率来源规则

| 规则 | 行为 | 依据 |
|---|---|---|
| 只读发现 | 只展示 `frequency-sources` 返回的条目；模块不扫盘、不解析 `.out` | 端点契约 |
| Hessian 门 | `hessian_available !== true` 的行 `disabled` + "缺少 Hessian" 徽标；点击不选中、不发 vibrations | 提交侧 `_prepare_tsmode_input` 对无 Hessian 来源报 `hessian_missing`（L2316–2323） |
| 单一来源自动选中 | 只有**恰好一个** `hessian_available` 来源时自动选中并加载其模式 | 与旧编辑器一致；不是科学推断，不预检、不提交 |
| 多来源不自动选 | 必须由用户显式点击，避免猜来源 | 「no guessed modes」边界 |
| 切换来源 | 丢弃旧模式、旧目标、旧预检；重新请求新来源的 vibrations | 模式编号属于来源，跨来源复用即错配 |
| 警告 | `pending_fetch` 等 `warnings[]` 原样列出 | 远程任务未取回时明确提示 |

## 5. 设置与 launch gate（诚实边界）

| 字段 | 默认 | 说明 |
|---|---|---|
| `allow_unverified_mapping` | **false（未勾选）** | 显式 opt-in。服务端 `enforce_launch_gate(require_verified=not allow_unverified_mapping)`（`_prepare_tsmode_input` L2357–2364）在映射未验证时默认拒绝；`TS_MODE_MAPPING_VERIFIED_VERSIONS` 当前为**空集**（`cccp/analysis/tsmode/mode_mapping.py` L71），所以不勾选时提交会被 422 `mode_mapping_unsupported`。模块**不代替用户勾选**，并在勾选前常驻警告；勾选后隐藏警告并允许重试。 |
| `final_frequency` | true | 默认做最终频率验证；取消勾选才发 `false`（runner → bundle → `TsmodeRequest.final_frequency`，engine L414 生效） |
| `max_iterations` | 空 | 空 = 服务端默认；文本严格整数且 ≥1（int 可通过 canonical digest，engine 映射到 `geom_maxiter`） |

方法 / 基组 / 电荷 / 多重度 / 级别继承自频率源（catalog `tsmode` profile 语义）；本面板不覆盖，也不发 `level_overrides`。
`TsmodeOptimizationSettings` 默认 `retry_limit=2`、`require_verified_mapping=True`、`final_frequency=True`
（`src/acp/calculations/tsmode/contracts.py` L93–102），与上表一致。

**为什么没有 `recalc_hess` / `trust_radius` / `retry_limit` 控件**（§9 详述）：这三个键在"预检 + 指纹提交"链路上
无法既通过 `canonical_digest` 又被 workflow 适配器真正采用——`trust_radius` 的 JSON 浮点会让计划摘要
直接 `CanonicalDigestError`，`recalc_hess` 的布尔形式被 `_optional_int` 丢弃，`retry_limit: 0` 被 `or 2` 改写。
本模块只暴露可端到端生效的设置，避免"看起来能调、实际被忽略"的假控件。

## 6. 校验矩阵（客户端，提交前拦截；服务端仍为权威）

| 代码 | 规则 | 结果 |
|---|---|---|
| `task_missing` | 无 `taskRef.jobId` | 错误，阻断 |
| `client_missing` | 无 request 注入 | 错误，阻断 |
| `source_missing` | 未选来源 | 错误，阻断 |
| `source_unknown` | 选中来源不在当前列表 | 错误，阻断 |
| `hessian_missing` | 来源 `hessian_available !== true` | 错误，阻断 |
| `mode_missing` | 未选目标模式 | 错误，阻断 |
| `mode_index_invalid` | 目标不是非负整数 | 错误，阻断（正常路径不会出现） |
| `mode_unknown` | 目标不在当前模式列表 | 错误，阻断（列表可能已变化） |
| `mode_not_imaginary` | 目标不是虚频 | 错误，阻断 |
| `max_iterations_invalid` | 非空且非整数或 <1 | 错误，阻断 |
| `atom_count_mismatch` | 来源原子数 ≠ 模式表原子数 | **警告**（不误拦；提示核对来源） |
| `mapping_unverified` | 未允许未验证映射 | **警告**（服务端 launch gate 可能拒绝） |

校验失败时 `/preflight` 与 `/jobs` 都**不会**被调用（测试钉住），并显示可读 zh 文案。
非虚频/实频模式不会出现在可选列表（只列 `imaginary === true`）。

## 7. i18n（zh 内联 + data-i18n）

模块自带 `ZH` 兜底字典（Shell 字典缺 key 时 `t()` 回退本地 zh 文本；`ACPShell.i18n.t` 只回显 key）。
静态文案带 `data-i18n`；动态文案（插值/错误）随 `acp:langchange` 重绘。共 **100** 个 `next.tsmode.*` 键：

```
next.tsmode.title
next.tsmode.subtitle
next.tsmode.task_line
next.tsmode.task_unknown
next.tsmode.empty
next.tsmode.empty_no_task
next.tsmode.empty_no_sources
next.tsmode.loading
next.tsmode.error_title
next.tsmode.errorTitle
next.tsmode.errorMessage
next.tsmode.retry
next.tsmode.source_title
next.tsmode.source_hint
next.tsmode.source_empty
next.tsmode.source_loading
next.tsmode.source_error
next.tsmode.source_row_meta
next.tsmode.source_hess_available
next.tsmode.source_hess_missing
next.tsmode.source_entry
next.tsmode.source_selected
next.tsmode.source_select
next.tsmode.source_pending_fetch
next.tsmode.mode_title
next.tsmode.mode_hint_native
next.tsmode.mode_hint_select_source
next.tsmode.mode_loading
next.tsmode.mode_error
next.tsmode.mode_unavailable
next.tsmode.mode_empty
next.tsmode.mode_count
next.tsmode.mode_row_index
next.tsmode.mode_row_frequency
next.tsmode.mode_row_ir
next.tsmode.mode_select
next.tsmode.mode_selected
next.tsmode.mode_detail_title
next.tsmode.mode_detail_index
next.tsmode.mode_detail_frequency
next.tsmode.mode_detail_atoms
next.tsmode.mode_detail_source
next.tsmode.mode_detail_vectors
next.tsmode.mode_detail_ir
next.tsmode.mode_detail_entry
next.tsmode.mode_source_product
next.tsmode.mode_source_historical
next.tsmode.mode_source_unknown
next.tsmode.settings_title
next.tsmode.settings_hint
next.tsmode.allow_unverified
next.tsmode.allow_unverified_warn
next.tsmode.final_frequency
next.tsmode.max_iterations
next.tsmode.settings_default
next.tsmode.review_title
next.tsmode.summary
next.tsmode.summary_lines
next.tsmode.summary_no_mode
next.tsmode.preview
next.tsmode.preflighting
next.tsmode.preview_idle
next.tsmode.preview_ok
next.tsmode.preview_failed
next.tsmode.preview_error
next.tsmode.preview_blocked
next.tsmode.preview_mapping_note
next.tsmode.advisories
next.tsmode.submit
next.tsmode.submitting
next.tsmode.submit_blocked
next.tsmode.submit_already
next.tsmode.submit_error
next.tsmode.receipt
next.tsmode.receipt_body
next.tsmode.receipt_digest
next.tsmode.receipt_replay
next.tsmode.open_task
next.tsmode.note
next.tsmode.err.task_missing
next.tsmode.err.client_missing
next.tsmode.err.source_missing
next.tsmode.err.source_unknown
next.tsmode.err.hessian_missing
next.tsmode.err.mode_missing
next.tsmode.err.mode_unknown
next.tsmode.err.mode_not_imaginary
next.tsmode.err.mode_index_invalid
next.tsmode.err.max_iterations_invalid
next.tsmode.err.modes_entry_mismatch
next.tsmode.err.job_not_found
next.tsmode.err.atom_count_mismatch
next.tsmode.err.mapping_unverified
next.tsmode.err.mode_mapping_unsupported
next.tsmode.err.mode_mapping_ambiguous
next.tsmode.err.target_mode_invalid
next.tsmode.err.frequency_source_incomplete
next.tsmode.err.preflight_stale
next.tsmode.mode_reason.pending_fetch
next.tsmode.mode_reason.no_normal_modes
```

`A-61` 批次需要在 `frontend/js/shell/i18n.js` 的 `zh-CN` 与 `en-US` 两套字典补齐上述键（本次未改 shell 文件；
`acp2_i18n_new_modules.test.js` 的模块清单也不含本模块，因此不会被静态覆盖测试反向判红）。

## 8. 测试与实测证据

命令（任务指定的 node）：

```bash
"C:\Users\彭扬超\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" \
  frontend/js/module_tests/run_all.js acp2_tsmode_editor
```

结果（2026-10-31 实测）：**21 passed / 0 failed，exit 0**。

覆盖：

1. 注册面：唯一 `ACPNextModules` key、`ACPShell.registry.validateDescriptor` 通过、无 `ACPTsmodeEditor` 旧全局；
2. `empty`（无任务）零请求、`loading → ready`、Hessian 缺失行禁用且不可点选；
3. 单一 eligible 来源自动选中并按其 `entry_id` 请求 vibrations；
4. **原生模式序号**：`6/9/13` 非连续编号，选中第 3 行 → `source_mode_index: 13`，第 1 行 → `6`（位置/0 基不会泄漏）；
5. 模式详情：原生序号、虚频、原子数、来源（product / 历史投影）、条目、向量个数、IR；
6. 来源切换：重新请求新模式、丢弃旧目标与旧预检、摘要跟随新条目；
7. 校验矩阵：无来源/无目标/坏 `max_iterations` 全部本地拦截（零 `/preflight`、零 `/jobs`）；
8. 空来源 `empty`；全是实频 → 不提供目标（"没有虚频模式"）且不可提交；
9. 错误：来源 404 → error + 重试恢复；vibrations 404 → `modes-state=error` + 0 行 + 不提交；
10. 预检：精确 body（无 `method`、无资源、无指纹）、`Content-Type`、digest/ advisories 渲染、编辑后预检失效；
11. 预检不可行：列出全部 `reasons`、提交禁用、零 `/jobs`；
12. 显式未验证映射 opt-in + 可选 `max_iterations` 进入 wire body；
13. 提交：预检后才行；`preflight_fingerprint` + `Idempotency-Key`；回执字段；同 plan 二次提交被拒；`openTask` 导航；
14. 提交错误映射：`mode_mapping_unsupported` / `403` / `preflight_stale`（清空预检后可恢复）；
15. 忙碌：双击不重复请求、按钮禁用、`预检中…/提交中…`；
16. generation/dispose：迟到的 sources / modes / preview 不重建 DOM、不提交、不导航；dispose 幂等；可重新 mount 且真的重载；
17. context：任务焦点变化重载来源并丢弃旧目标；同一 job 不重复请求；
18. i18n：静态 `data-i18n` 键齐全、zh 兜底、`acp:langchange` 不抛错；
19. 不伪造模式：`available:false` / 空列表 / 非法/重复 `mode_index` 一律丢弃，不补造；
20. 纯函数：错误码矩阵、原子数不一致警告（非阻断）、请求构造；
21. 边界 pin：源码无 `$3Dmol` / `ACPVisualization` / `scene_instance` / `createViewer` / `canvas` / `innerHTML` /
    `fetch(` / `XMLHttpRequest` / `sendBeacon` / `jobCommands` / `ACPTsmodeEditor`；运行时只请求
    `frequency-sources` / `vibrations` / `preflight`（及显式提交时的 `/jobs`），无几何读取、无任务变更命令、无自造 tsmode 路由。

全量回归（同一 node，2026-10-31 最终一次）：`node frontend/js/module_tests/run_all.js` →
**906 passed / 0 failed / exit 0**（含本模块 21 项）。本次工作区并行批次仍在演进：中间一次全量运行曾出现
`acp2_task_wizard.test.js` 的 2 项失败（task-wizard 自己的 `nav-hint` 文案与"源码不得出现 `/jobs`"pin），
并行批次修复后最终全量已全绿；本模块只新增 `modules/tsmode-editor/`、`css/modules/tsmode-editor.css`、
`module_tests/acp2_tsmode_editor.test.js`，未改任何既有模块，也未放宽任何断言。

后端契约核对（一次性 Python 进程；不是服务端运行，也不等于真实 HTTP/ORCA）：

- 用 `acp_services.entrypoints.application_params.normalize_preflight` + `canonical_digest.frozen_plan_definition/plan_digest`
  复算 **预检体** 与 **提交体（去掉 `preflight_fingerprint`）**：两者 digest 完全一致
  （`sha256:72f6dcaed241af3111c7619862363ddbe8eaa737d812d5302e6882a60f72441f`），证明 `preflight_fingerprint` 绑定可用；
- `validate_request_fields(body, request_kind="preflight"|"submit", entry="http")` 均通过（顶层字段集合法）；
- 含 `max_iterations: 40` 的 body 也可计算 digest（`sha256:50d4a555…`），整数调参不破坏 canonical digest；
- 反例（实测/读码）：`trust_radius: 0.2` 触发 `CanonicalDigestError: non_canonical_number`；
  `recalc_hess: true` 被 `acp/workflows/tsmode.py::_optional_int` 丢弃；`retry_limit: 0` 被 `or 2` 改写。
  这三个键因此未进入模块 UI（§9-8）。

## 9. 已知缺口 / 未验证（诚实记录）

1. **模式列表的 entry-id 不保证匹配（后端契约缺口）**：
   `_discover_frequency_sources` 的 `entry_id` 是频率 `.out` 的文件 stem（或 manifest product 的 `entry_id`，通常缺失），
   而 `get_structure_viewer_vibrations` 要求 `entry_id` 等于 structure-viewer 目录条目 id
   （`simple_frequency` / `batch_<item>` / `tsmode_source`，见 `acp/results/structure_viewer.py::simple_entry_id` / `batch_entry_id`）。
   两者不一致时 vibrations 返回 **404**，本模块显示 `next.tsmode.err.modes_entry_mismatch`（"不会猜测其它条目 id"）
   并保持目标为空——**不猜映射、不伪造模式**。建议后端二选一：frequency-sources 返回 structure-viewer 条目 id，
   或 vibrations 接受频率源 stem。这是 A-16 后续要闭环的真实集成缺口。
2. **预检不覆盖模式映射/launch gate**：`POST /preflight` 只跑字段/工作流语义 + 来源可行性（`_preflight_source_facts`），
   不执行 `resolve_target_mode` / `enforce_launch_gate`；因此"预检通过"仍可能在 `POST /jobs` 被 422 拒绝。
   模块在 UI 明示该边界（`next.tsmode.preview_mapping_note`），并在提交错误里给出可读原因；**不**把预检当提交批准。
3. **没有方法/基组/电荷/多重度/级别覆盖**：tsmode 契约 `method_keys` 为空，级别/电荷/多重度从频率源继承
   （这也是 A-16 与 P01 方法配置面的分界）。
4. **没有资源/队列/项目选择**：不发 `resources`、`project_id`，由服务端默认/上下文解析。
5. **没有草稿持久化**：刷新后状态丢失（P02 草稿面不在 A-16）。
6. **没有 3D/结构预览**：A-16 只要求模式选择/频率来源/提交；为满足"无第二 viewer"，本模块完全不读几何。
   代价：无法在 3D 里看模式箭头（那是 A-42/A-43 `vibration-panel` 的职责）。
7. **自由输入原生序号未提供**：目标只能从已加载的虚频列表中点选（每个目标都带已核实的频率/原子数/来源），
   避免提交一个"列表里不存在"的猜测编号；服务端仍会做最终校验。
8. **三个设置键经 HTTP 预检链不可用（后端契约矛盾，已实测）**：
   - `trust_radius`：JSON 浮点会让 `_plan_digest` 抛 `non_canonical_number`（`canonical_digest.py` L235–238），
     而 workflow 适配器 `_optional_float` 只接受数字、会静默丢弃十进制字符串（`acp/workflows/tsmode.py` L176–180）；
     因此"能预检的格式"与"能生效的格式"互相排斥。
   - `recalc_hess`：契约声明 boolean（`workflow_contracts.py` L879），但 `_optional_int` 对 bool 直接返回 `None`
     （L169–173），`true` 会被静默丢弃（发整数 `1` 才能生效，但语义变成"每步重算"）。
   - `retry_limit`：`_optional_int(...) or 2`（L74）把合法的 `0` 改写成默认 `2`。
   本模块据此**不提供**这三个控件，只暴露 `allow_unverified_mapping` / `final_frequency` / `max_iterations`。
   建议后端统一数字形态（canonical decimal string 贯穿 digest 与适配器），再恢复控件。
9. **未做 Shell 接入**：按文件边界未改 `frontend/next/index.html`、`frontend/js/shell/*`、`recipes.js`；
   当前不会被默认布局加载（§10 给出接入步骤）。
10. **只跑 Node vm 套件**：未在真实浏览器执行，未对真实 HTTP/ORCA 运行；四态与提交只由注入 client 验证。
11. **zh-only**：Shell 字典尚无 `next.tsmode.*`，en 下显示 zh（A-61）。
12. **服务端映射版本状态动态**：`TS_MODE_MAPPING_VERIFIED_VERSIONS` 为空是当前 checkout 的事实；
    模块不硬编码该集合（只看输入布尔与提交错误），因此后端一旦验证通过，行为自动放宽。

## 10. 接入下一步（本次文件边界外，未改动）

1. `frontend/next/index.html`：`<head>` 加 `<link rel="stylesheet" href="/css/modules/tsmode-editor.css">`；
   脚本区加 `<script src="/js/modules/tsmode-editor/module.js"></script>`（放在 `module-registry.js` 之后、
   `ACPShell.bootstrap.js` 之前或模块脚本区）。`ACPShell.bootstrap.boot()` 的 `collectFrom(window.ACPNextModules)`
   会自动收编注册。
2. `frontend/js/shell/recipes.js`：为 Execution（V4/V5 装配）选择槽位/默认可见性；未接默认 recipe 前可由布局编辑器手动选用
   （面板标题键 `shell.module.tsmode_editor` 需在 A-61 批补）。
3. `frontend/js/shell/i18n.js`：补 §7 的 100 条 `next.tsmode.*`（zh + en；占位符保持一致）。
4. 浏览器用例：`frontend/js/module_tests/acp2_next_entry.browser.js` 增加 tsmode-editor 场景
   （选中频率任务 → 选来源 → 选原生模式 → 预检 → 提交回执），本次未执行浏览器套件，不计 PASS。
5. parity 记录：A-16 的「模块注册 + 四态 + 测试 + 键组」四项中，键组覆盖依赖第 3 步；其余三项已完成（代码/契约层）。

## 11. 纯函数测试缝（`_internals`）

`normalizeSources(payload)`、`normalizeModes(payload)`、`imaginaryModes(modes)`、`validateSelection(input)`、
`buildInput(input)`、`buildRequest(input)`、`defaultJobName(entryId, modeIndex)`、`planKeyOf(request)`、
`formatFrequency(value)`、`describeError(error)`、`settingsSummary(settings)`，
以及常量 `API("/api/v1")` / `WORKFLOW("tsmode")` / `MODULE_ID("tsmode-editor")`。

`normalizeModes` 是"不伪造模式"的唯一入口：`mode_index` 非整数/负数/重复的一律丢弃，
`available:false` 返回空列表；`validateSelection` 与 `buildRequest` 只消费规范化后的对象。
