# s2-scan — ACP 3.0 S2 键长扫描弹窗（A-48 / P16）

- 文件：
  - `frontend/js/modules/s2-scan/module.js`（classic script，无构建、无 ESM）
  - `frontend/css/modules/s2-scan.css`（仅消费 `frontend/css/workbench/base.css` / `shell/shell.css` 的变量）
  - `frontend/js/module_tests/acp2_s2_scan.test.js`（vm + fake DOM + 注入 client，网络零直连）
- 注册名：`window.ACPNextModules["s2-scan"]`（唯一新增全局；不写页面全局、不建第二 viewer、不建第二缓存）
- 对应计划：[ACP 3.0 前端计划](../../../../docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md)
  §5.V5 / Track A **A-48**（`P16 S2 键长扫描弹窗`）；前置 **A-06**（提交与回执）；
  验收要点：**默认不启 ScanTS**。

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["s2-scan"] = {
  descriptor: {
    id: "s2-scan",
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["scan.s2"],
    binds: ["taskRef", "structureRef"],
    capabilities: { requires: [], provides: ["scan.s2"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.s2scan.errorTitle", messageKey: "next.s2scan.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ },
  _internals: { /* 纯函数测试缝，见 §8 */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise<void>`、`updateContext(snapshot)`、`suspend()`、`resume()`、`dispose()`。

`deps`（全部可注入、可 mock；Shell 的 `hostApi` 自动提供 `context` / `commands`）：

| 字段 | 内容 |
|---|---|
| `doc` | DOM document（缺省 `window.document`；测试注入 fake DOM） |
| `request` | 现有客户端单点：`(path, opts) → Promise<parsedBody>`（缺省回落 `deps.client.transport.request` → `window.ACPClient.transport.request`） |
| `context` | Shell 工作区上下文（读取 `structureRef/taskRef/projectId`；`subscribe` 变更） |
| `commands` | 可选；仅收据后 `commands.openTask({jobId, attempt, source})` 定位任务（导航，不是变更） |
| `loadStructure(ref)` | 可选注入；`Promise<xyzText | {xyz} | {content}>`。缺省用 `ACPView.source.buildStructureUrl` + `request` |
| `readFile(file)` | 可选注入；上传文本的本地读取（缺省 `file.text()` / `FileReader`） |
| `parseDebounceMs` | 内联 XYZ 解析防抖，缺省 300 ms（测试传 0） |

解析优先级：**注入 `deps.request` → `deps.client.transport.request` → `window.ACPClient.transport.request` → 无**。
全部缺失时预检/提交给出明确错误且**不发任何请求**（测试 `client_missing` 路径钉住）；模块从不调用
`window.fetch` / `XMLHttpRequest`。

## 2. 端点与契约（全部为既有端点；没有为 A-48 新增后端）

| 用途 | 请求 | 说明 |
|---|---|---|
| 预览 / 校验 | `POST /api/v1/preflight` | **本模块的 preview 选择**。零副作用（不建任务/attempt/reserve），内部执行与提交同源的 `validate_workflow_submission` + `_validate_scan_submission`；返回 `feasible/reasons/advisories/plan_digest` |
| 提交 + 回执 | `POST /api/v1/jobs`（201） | 既有 wizard 提交路径；`Content-Type: application/json` + `Idempotency-Key`；body = 预检 body + `preflight_fingerprint` |
| 任务结构几何 | `GET /api/v1/jobs/{id}/structure-viewer/entries/{entry_id}/geometry` | 只经 `ACPView.source.buildStructureUrl(ref, "/api/v1")` 生成的规范 URL；返回 `text/plain` XYZ |

**为什么不是 scan 专用 preview**（精确记录选择）：
- 仓库中**不存在** `scan/preview` 或 `scan/validate` 路由（`src/acp/api/v1_routes.py` 的 104 条路由逐条核对）；
- 旧 S2 专用 `POST /api/v1/s2/structure-preview` 已退役，handler 固定返回 **410**（`该机制研究端点已退役，历史只读`），本模块**不调用**它；
- `POST /api/v1/batch-optimize/config-preview` 只服务 BatchOptimize，不能校验 scan；
- `GET …/s2/profile|candidates|frame/{i}` 是**已存在任务**的只读结果面，不预览新扫描；
- 因此 preview 走**既有 wizard 预检/提交链** `POST /preflight → POST /jobs`（与四态 `task-config` 的 A-05/A-06 完全一致），
  客户端本地校验只做提交前拦截，服务端 `/preflight` 是可行性与指纹的权威。

### 2.1 预检请求体（模块实际发送的内容）

```json
{
  "workflow": "scan",
  "project_id": "proj-a",
  "input": {
    "source_type": "xyz_text",
    "source": "3\nwater\nO 0.000 0.000 0.000\nH 0.758 0.586 0.000\nH -0.758 0.586 0.000\n",
    "xyz_text": "3\nwater\nO 0.000 0.000 0.000\nH 0.758 0.586 0.000\nH -0.758 0.586 0.000\n",
    "charge": 0,
    "multiplicity": 1,
    "scan_coordinates": ["0,1,1.0,3.0"]
  },
  "method": {
    "schema_id": "dft_scan",
    "scan_points": 21,
    "scan_use_scants": false
  }
}
```

- `scan_coordinates` 字符串形如 `<atomA0>,<atomB0>,<start>,<end>`，原子索引 **0 基**；UI 的 1 基编号只在
  `scanCoordinateString()` 一处转换（`scan-index-base-convert`）。range 文本原样转发（保留用户小数位）。
- `source` 与 `xyz_text` 同为用户提交的几何原文（不改写、不重排）；`source_type: "xyz_text"`。
- `project_id` 仅在上下文有项目时出现（不伪造）。
- **没有** `resources`、`method.levels`、`method.method`、`idempotency_key`、`preflight_fingerprint`
  （预检体不带指纹；幂等键只在提交 header）。这些字段集被 `_SCAN_METHOD_KEYS` / `_SCAN_INPUT_KEYS` 接受，
  但本模块不发明多余字段（测试 `Object.keys(method)` 钉住）。

### 2.2 预检响应（消费字段）

`{ schema_version, feasible, reasons[{code,message,field,rule,params}], advisories[], plan_digest, plan_summary, estimate, admission, would_create_task:false, would_reserve:false }`

- `feasible:true` 且 `plan_digest` 非空 → 记录 `fingerprint/planKey/configRevision`，启用提交；
- `feasible:false` → 列出全部 `reasons`（`data-preflight-reason`），提交保持禁用；
- 非 2xx → `next.s2scan.preflight_error`（含 FastAPI `detail`），提交保持禁用；
- `advisories` 单独列出（例如单坐标键长扫描的 `scan_single_coordinate_geometry`）。

### 2.3 提交请求与回执

提交体 = 预检体 + `preflight_fingerprint: <plan_digest>`；headers：

```
Content-Type: application/json
Idempotency-Key: next-s2-<uuid 或 时间戳随机>
```

- 键在同一 plan 内复用（planKey = 提交体 JSON），成功后清空；plan 改变后重新生成，因此重试不会产生第二任务；
- 201 回执消费 `job_id/status/attempt/plan_digest/idempotent_replay`，渲染
  `任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试` 与计划摘要；
- 同一 plan 已成功后又点提交 → 客户端直接拒绝（`receiptKey === planKey`），不发第二个 `POST /jobs`；
- 提交错误映射：`400 + detail.code === "preflight_stale"` → 清空预检并要求重新预检；`401/403` → 显示服务端 detail；
  其余显示 detail/message。失败不重放、不自动重试、不显示成功。

## 3. 四态、忙碌态与守卫

模块根 `data-acp-module-state` ∈ `empty | loading | ready | error`（结构数据状态）：

| 状态 | 触发条件 |
|---|---|
| `empty` | 尚未载入结构（初始 / 内联清空 / 切换来源 / **任务焦点变化后主动失效**） |
| `loading` | 任务结构或上传文本读取进行中（overlay 可见） |
| `ready` | XYZ 解析成功（含原子数、当前键长、可提交表单） |
| `error` | 结构读取失败或 XYZ 解析失败（overlay + 原因 + 重试；重试按来源分派） |

命令忙碌态独立：`data-acp-busy = none | preflight | submit`（预检/提交期间不禁用表单，但两个按钮禁用、
重复点击被忽略）；另有 `data-acp-scants = off|on`、`data-acp-valid = true|false`、`data-acp-source = inline|task|upload`、
`data-acp-receipt-state = submitted|error`。

守卫：`state.generation`（mount/dispose/结构更换）+ 每类请求的 token（`source.token`、`preflight.token`、
`submit.token`）。任何迟到结果都在写 DOM 前被丢弃；`dispose()` 幂等，移除自有 DOM、context 订阅、
`acp:langchange` 监听、内联解析定时器；dispose 后可重新 `mount`（默认值复位）。

## 4. 校验矩阵（客户端，提交前拦截；服务端仍为权威）

| 代码 | 规则 | 备注 / 权威 |
|---|---|---|
| `structure_missing` | 未载入结构 | 无几何不能验证原子索引 |
| `atom_invalid` | 原子编号必须是整数 | 1 基，严格整数（`1.0` 非法） |
| `atom_out_of_range` | 原子编号 ≥1 且 ≤ 原子数 | 服务端同规则（0 基越界拒绝） |
| `atoms_equal` | 两个扫描原子必须不同 | 服务端同规则 |
| `start_invalid` | 起点必须是有限数值 | 空串/非数字 |
| `start_not_positive` | **start > 0** | A-48 明确要求；后端 `distance` 也只接受 > 0 |
| `end_invalid` | 终点必须是有限数值 |  |
| `end_not_greater` | **end > start** | A-48 要求（比后端 `start != end` 更严格，只会少放行 reverse 扫描） |
| `points_invalid` | 点数必须是整数 |  |
| `points_min` | **点数 ≥ 2** | `scheduler/jobs.py::validate_scan_submission`（"integer scan_points >= 2"） |
| `points_max` | **点数 ≤ 100** | `catalog.py::FIELD_DEFINITIONS["scan_points"]["max"]`（UI 字段上限） |
| `charge_invalid` | 电荷必须是整数 |  |
| `multiplicity_invalid` | 多重度必须是 ≥1 的整数 |  |
| `multiplicity_parity` | 电子数奇偶 ↔ 多重度奇偶 | 电子数 = ΣZ − charge；偶电子→奇多重度，奇电子→偶多重度 |

- 上限说明：`scan_points` 的 catalog 声明为 `min:5, max:100`，但提交边界（真正决定 201/422 的校验）允许 `≥2`；
  A-48 要求 `points>=2`，故常量取 **2..100**，并在模块注释里分别标注两个来源，避免"猜一个统一阈值"。
- 未知元素：结构照常解析，但奇偶检查标记为 **unverifiable**（`parityChecked:false`、`electrons:null`），
  **不误报**、也不伪造原子序数。
- 校验失败时：`/preflight` 与 `/jobs` 都**不会**被调用（测试钉住），并显示可读 zh 文案。

## 5. ScanTS 规则（A-48 验收要点）

- 目录默认：`catalog.py::FIELD_DEFINITIONS["scan_use_scants"]["default"] = {"*": False}`，help 明确
  "面向过渡态的扫描才启用；普通键长、键角和二面角扫描通常关闭"。
- 模块状态默认 `scants:false`，复选框初始 `checked=false`，`data-acp-scants="off"`；
  请求体**始终**带 `method.scan_use_scants`（默认 `false`，显式为真才为真）。
- 唯一开启路径：用户点击复选框（`change` 事件）→ 状态 `true` → 徽标"ScanTS 已启用（TS 导向扫描）"。
  模块内**没有任何**自动开启/记忆/默认勾选逻辑（测试用源码 pin 钉住 `scants.checked = true` 不存在）。
- 服务端链路核对：`scheduler/jobs.py::scan_method_flags` 对默认体输出
  `['--coordinate','0,1,1.0,3.0','--scan-points','21']`，显式开启才追加 `'--scants'`（本次实测，见 §8）。
- 普通键长扫描不会隐式触发内部 TS 优化。

## 6. 结构来源

| 来源 | 行为 | 边界 |
|---|---|---|
| 内联 XYZ | 文本域粘贴；300 ms 防抖解析（`parseDebounceMs` 可注入）；首行原子数可选、注释行可选 | 只取**第一帧**；文本原文提交，不重排 |
| 任务结构 | 读取 Shell 上下文的规范 `structureRef`（`job-file` / `job-remote-file` / `inline-xyz` / `structure-entry`）；点击"从当前任务载入"才取数 | **不在焦点变化时自动取数**；URL 只由 `ACPView.source.buildStructureUrl` 生成，模块内不拼路径；没有第二个 viewer |
| 上传文本 | `<input type="file" accept=".xyz,.txt">`；经 `deps.readFile`/`file.text()`/`FileReader` 本地读取并填入文本域 | 只读本地文件，**不上传**、不发明 upload 端点；内容随后按内联 XYZ 解析 |

- 任务结构加载优先级：注入 `deps.loadStructure(ref)` → `ACPView.source.buildStructureUrl` + `requestFn`（`GET`）。
  两者都没有时进入 `error` 态并明确提示 `view_source.js` 未加载，**不会**伪造 URL 或退回 `window.fetch`。
- 远程任务的几何未取回（409 `pending_fetch`）表现为该来源的 error + 重试；本弹窗不发起 prepare/写回（边界外）。

## 7. i18n（zh 内联 + data-i18n）

模块自带 `ZH` 兜底字典（Shell 字典缺 key 时 `t()` 回退本地 zh 文本；`ACPShell.i18n.t` 只回显 key）。
静态文案带 `data-i18n` / `data-i18n-ph`；动态文案（插值/错误）保留 zh 并随 `acp:langchange` 重绘。

需要在 `frontend/js/shell/i18n.js` 补的 key（zh + en，共 **89** 条；zh 值与模块内联文本逐字一致）：

```
next.s2scan.title
next.s2scan.subtitle
next.s2scan.source_title
next.s2scan.source_inline
next.s2scan.source_task
next.s2scan.source_upload
next.s2scan.source_inline_hint
next.s2scan.source_task_hint
next.s2scan.source_upload_hint
next.s2scan.xyz_placeholder
next.s2scan.parse
next.s2scan.load_task
next.s2scan.reload_task
next.s2scan.choose_file
next.s2scan.task_ref
next.s2scan.task_no_ref
next.s2scan.task_changed
next.s2scan.scan_title
next.s2scan.bond_length
next.s2scan.bond_length_unknown
next.s2scan.atom_a
next.s2scan.atom_b
next.s2scan.start
next.s2scan.end
next.s2scan.points
next.s2scan.scants
next.s2scan.scants_hint
next.s2scan.scants_on
next.s2scan.scants_off
next.s2scan.state_title
next.s2scan.charge
next.s2scan.multiplicity
next.s2scan.parity_ok
next.s2scan.parity_unverifiable
next.s2scan.review_title
next.s2scan.summary
next.s2scan.summary_lines
next.s2scan.scants_on_short
next.s2scan.scants_off_short
next.s2scan.source_inline_short
next.s2scan.source_task_short
next.s2scan.source_upload_short
next.s2scan.preflight
next.s2scan.preflighting
next.s2scan.preflight_idle
next.s2scan.preflight_ok
next.s2scan.preflight_failed
next.s2scan.preflight_error
next.s2scan.preflight_blocked
next.s2scan.preflight_edited
next.s2scan.advisories
next.s2scan.submit
next.s2scan.submitting
next.s2scan.submit_blocked
next.s2scan.receipt
next.s2scan.receipt_body
next.s2scan.receipt_digest
next.s2scan.receipt_replay
next.s2scan.open_task
next.s2scan.loading
next.s2scan.empty
next.s2scan.empty_hint
next.s2scan.error_title
next.s2scan.errorTitle
next.s2scan.errorMessage
next.s2scan.retry
next.s2scan.source_status_ready
next.s2scan.source_status_empty
next.s2scan.method_note
next.s2scan.err.structure_missing
next.s2scan.err.atom_invalid
next.s2scan.err.atom_out_of_range
next.s2scan.err.atoms_equal
next.s2scan.err.start_invalid
next.s2scan.err.start_not_positive
next.s2scan.err.end_invalid
next.s2scan.err.end_not_greater
next.s2scan.err.points_invalid
next.s2scan.err.points_min
next.s2scan.err.points_max
next.s2scan.err.charge_invalid
next.s2scan.err.multiplicity_invalid
next.s2scan.err.multiplicity_parity
next.s2scan.err.structure_adapter_missing
next.s2scan.err.client_missing
next.s2scan.err.structure_empty
next.s2scan.err.structure_read_failed
next.s2scan.err.file_unsupported
next.s2scan.err.parse_failed
```

## 8. 测试与实测证据

命令（本机 Node v24.21.0，任务指定的 node.exe）：

```bash
node frontend/js/module_tests/run_all.js acp2_s2_scan
```

结果：**20 passed / 0 failed，exit 0**（2026-10-31 实测）。覆盖：

1. 注册面：唯一 `ACPNextModules` key、`ACPShell.registry.validateDescriptor` 通过、无 legacy 全局；
2. `empty` 初始态、ScanTS 默认关闭、提交禁用、mount 零请求；
3. 内联 XYZ：`ready`、原子数、当前键长（水 O–H 0.958 Å）、冻结请求体（schema/坐标/点数/无多余字段）；
4. 校验矩阵：start>0、end>start、points 2/100/101、原子范围/整数/重复、charge/多重度整数、奇偶（含阴离子与未知元素）；
5. UI 级：非法参数不触发 `/preflight`、不触发 `/jobs`；
6. ScanTS：默认 false、唯一显式 opt-in、源码 pin（无自动开启）；
7. 预检：精确 body、digest + advisories、可提交；infeasible 列出全部 reasons 且提交禁用、无 `/jobs`；
8. 提交：`preflight_fingerprint`、`Idempotency-Key`、回执字段、`commands.openTask`、同 plan 二次提交被拒；
9. 预检前不可提交；编辑后预检失效；重新预检使用新 plan；
10. 忙碌：双击不重复请求、按钮禁用、`预检中…/提交中…`；
11. 错误：预检 500、提交 403、`preflight_stale` 清空预检后可恢复；
12. 任务结构：注入 loader 的 `loading→ready`、失败 `error→retry`；无 `ACPView.source` 时明确 error 且零请求；
13. 任务结构规范 URL：`ACPView.source.buildStructureUrl` 结果逐字比对；
14. 上传文本：本地读取、零网络、无 upload 端点、文本进入编辑器/预检体；
15. generation/dispose：迟到的结构/预检结果不重建 DOM、不触发 openTask、不提交；dispose 幂等；可重新 mount；
16. `updateContext`：焦点结构变化会丢弃旧结构并提示，且**不自动取数**；
17. `project_id` 进入预检与提交同一 plan；
18. i18n：静态 key 齐全、zh 兜底、`acp:langchange` 不抛错；
19. 边界 pin：源码无 `$3Dmol` / `ACPVisualization` / `scene_instance` / `createViewer` / `canvas` /
    `fetch(` / `XMLHttpRequest` / `sendBeacon` / `jobCommands` / `s2scanState`；预览只打一个 `/preflight`；无任何任务变更命令。

全量回归（同一 node）：`node frontend/js/module_tests/run_all.js` → **798 passed / 0 failed，exit 0**。

后端契约核对（一次性 Python 进程，非服务端运行；Windows 无 POSIX `fcntl`，脚本内以 `sys.modules["fcntl"]` 桩
仅为可导入，不改仓库代码）：

- 上述精确 body 通过 `validate_request_fields(request_kind="preflight")`、`normalize_preflight`、
  `canonical_submit_request`、`validate_workflow_submission("scan")`；
- `validate_scan_submission("scan", method, input)` 通过；对 `start=0`、`end==start`、`points=1`、
  同原子、原子越界分别给出 `start and end distances must be greater than 0` /
  `start and end distances must differ` / `scan_points must be an integer >= 2` /
  `scan coordinate atoms must be different atoms` / `atom index 9 is out of range for 3 atoms`；
- `scan_method_flags(method, input)`：默认 → `['--coordinate','0,1,1.0,3.0','--scan-points','21']`；
  `scan_use_scants:true` → 追加 `'--scants'`；
- `catalog.get_workflow_by_id("scan")["method_schema_id"] == "dft_scan"`，且 `dft_scan` 的 `scan` level
  声明 `scan_use_scants`。
- 这证明**请求形状/边界规则/ScanTS 旗标**与后端契约一致；不等于真实 HTTP/ORCA 执行。

## 9. 接入下一步（本次文件边界外，未改动）

1. `frontend/next/index.html`：`<head>` 加
   `<link rel="stylesheet" href="/css/modules/s2-scan.css">`；脚本区（放在 `/js/visualization/view_source.js`
   之后、`ACPShell.bootstrap.js` 之前或模块脚本区）加 `<script src="/js/modules/s2-scan/module.js"></script>`。
   `ACPShell.bootstrap.boot()` 的 `collectFrom(window.ACPNextModules)` 会自动收编注册。
2. `frontend/js/shell/recipes.js`：为 Execution 工作区（V5 装配）选择槽位/默认可见性。当前**未接默认 recipe**，
   因此默认布局不会自动显示该面板；可由布局编辑器手动选用（`shell.module.*` 标题键需在 A-61 批补）。
3. `frontend/js/shell/i18n.js`：补 §7 的 89 条 key（A-61 全键批次；en 缺失前静态元素在 en 下显示 zh）。
4. 浏览器用例：`frontend/js/module_tests/acp2_next_entry.browser.js` 增加 s2-scan 场景
   （加载 → 内联 XYZ → 校验 → 预检 → 提交回执；本次未执行浏览器套件，不计 PASS）。
5. parity 记录：A-48 的「模块注册 + 四态 + 测试 + 键组」四项中，键组覆盖依赖第 3 步；其余三项已完成（代码/契约层）。

## 10. 已知缺口 / 未验证

- **未在真实浏览器执行**：只跑 Node vm 套件；`next/index.html` 未加载本模块（文件边界限制），
  真实页面挂载/点击/布局 interaction 仍为 **NOT_VERIFIED**。
- **未对真实 HTTP 服务执行**：预检/提交只经注入 client 验证；真实 `/preflight`、`POST /jobs`、
  幂等键落库、回执字段未在本机运行（需要可运行实例）。
- **方法/基组/方法档位不在本弹窗**：A-48 只负责坐标与 ScanTS；`method` 仅带 `schema_id: dft_scan` +
  `scan_points` + `scan_use_scants`，执行级别由服务端 `dft_scan` 默认解析。旧 S2 弹窗里的
  `scanMethod`（GFN2-xTB）/单点开关/`maxIter` 等字段**未迁移**（属 P01/A-07 的方法配置面）。
- **无 3D 原子点选**：为避免第二 viewer，原子编号用 1 基数字输入；与经典页"在结构预览里点选原子"的交互不对等
  （需要 `commands` 侧的选点桥，未在本次边界内实现）。
- **多帧结构只取第一帧**：未提供帧选择器；多帧 XYZ/远程轨迹请先用 `molecule-viewer` 选帧，再引用该结构。
- **远程任务结构的 prepare 流程**：`pending_fetch` 只显示错误 + 重试，不发起 `POST /jobs/{id}/prepare` 或
  `?fetch=1` 取回（写操作不在本弹窗范围）。
- **无资源块**：不发送 `resources`（nproc/mem），由服务端默认；任务名/备注/项目选择也不在弹窗内（项目只读自上下文）。
- **无草稿持久化**：刷新后弹窗状态丢失（A-02/P02 的 draft 面不在本任务）。
- **超时预算**：预检/提交沿用页面传输默认 8 s（与 `task-config` 一致）；未按 A-28 之外的方式私设超时。
- **zh-only**：Shell 字典尚无 `next.s2scan.*`；en 显示 zh（A-61）。
- **未改共享文件**：`frontend/next/index.html`、`frontend/js/shell/*`、`frontend/js/shell/recipes.js`、
  既有模块/测试均未修改；没有 git commit。

## 11. 纯函数测试缝（`_internals`）

`parseXyz(text)`、`validateConfig(input)`、`buildRequest(input)`、`scanCoordinateString(...)`、
`atomDistance(a,b)`、`electronCount(atoms, charge)`、`atomicNumber(symbol)`、`formatStructureRef(ref)`、
`structureRefKey(ref)`、`planKeyOf(request)`，以及常量 `MIN_POINTS(2)` / `MAX_POINTS(100)` /
`WORKFLOW("scan")` / `METHOD_SCHEMA_ID("dft_scan")`。测试通过这些函数钉住校验矩阵与冻结请求体，
UI 测试再钉住事件/状态/请求次数，两者互补。
