# job-edit — ACP 3.0 编辑重算模块（A-10 / A-11 / A-12 · P03）

- 文件：
  - `frontend/js/modules/job-edit/module.js`（classic script，无构建、无 ESM）
  - `frontend/css/modules/job-edit.css`（仅消费 workbench base / shell 的 CSS 变量）
  - `frontend/js/module_tests/acp2_job_edit.test.js`（vm + fake DOM + 注入 request seam）
- 注册名：`window.ACPNextModules["job-edit"]`（唯一新增全局；不写页面全局、不建第二任务缓存、不建第二请求栈）
- 对应计划：[ACP 3.0 前端计划](../../../../docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md)
  §5.V4 / Track A **A-10 / A-11 / A-12**（`P03 修改参数后重算`）；前置 A-06。
  验收要点：A-10「字段与任务真实参数一致」、A-11「变更清单与实际执行一致」、
  A-12「同目录原地重跑（ANTI #15）」。

本模块只覆盖编辑重算三件事：**草稿加载与回填（读取）→ 预览（零副作用）→ 执行（attempt）**。
它既不是创建任务向导（P01/A-01…A-08），也不是结构来源选择器（P04/A-13…A-15）。

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["job-edit"] = {
  descriptor: {
    id: "job-edit",
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["job.edit"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["job.edit"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.jobedit.errorTitle", messageKey: "next.jobedit.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ },
  _internals: { /* 纯函数测试缝，见 §12 */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise<void>`、`updateContext(snapshot)`、`suspend()`、`resume()`、`dispose()`。

`deps`（全部可注入、可 mock）：

| 字段 | 内容 |
|---|---|
| `doc` | DOM document（缺省 `window.document`；测试注入 fake DOM） |
| `request` | 现有客户端单点：`(path, opts) → Promise<parsedBody>`（缺省回落 `deps.client.transport.request` → `window.ACPClient.transport.request`） |
| `context` | Shell 工作区上下文（读取 `taskRef.jobId`；`subscribe` 变更） |
| `commands` | 可选；仅回执后 `commands.openTask({jobId, attempt, source})` 定位任务（导航，不是变更） |
| `jobId` | 可选显式任务 id（无 Context 时使用，测试/宿主嵌入） |

解析优先级：**注入 `deps.request` → `deps.client.transport.request` → `window.ACPClient.transport.request` → 无**。
全部缺失时草稿/预览/执行给出明确错误（`next.jobedit.err.client_missing`）且**不发任何请求**；
模块从不调用 `window.fetch` / `XMLHttpRequest` / `sendBeacon`。

## 2. 端点（逐条核对 `src/acp/api/v1_routes.py`，未新增后端）

| 用途 | 方法 & 路径 | 路由位置 | 说明 |
|---|---|---|---|
| 草稿回填 | `GET /api/v1/jobs/{job_id}/edit-draft` | `v1_routes.py:6188` | 200 → `V1EditDraftResponse`；404 未知任务；远程任务走 `_job_read_root` 并附 `previous_outputs` / `reaction_path_sources` |
| 预览 | `POST /api/v1/jobs/{job_id}/edit-recalculate/preview` | `v1_routes.py:6291` | 只校验 + diff，不写库、不建 attempt；404 / 409 / 422 / 400 |
| 执行 | `POST /api/v1/jobs/{job_id}/edit-recalculate` | `v1_routes.py:6353` | 原地重算或新任务；404 / 409 / 422 / 400 |

- 路由对象本身无 prefix（`APIRouter()`）；`src/acp/api/server.py:554` 以 `prefix="/api/v1"` 挂载，
  因此前端使用 `/api/v1` + 上述相对路径，**只在 `ROUTES` 一处**组合并 `encodeURIComponent(job_id)`。
- `ROUTES` 暴露在 `_internals`，测试逐字钉住三条 URL。
- 模块**不调用**任何其他端点：不 `POST /jobs`、不 rerun/cancel/purge、不 prepare、不读文件、
  不构造 `work_dir` / 任务目录 / run_root 路径（源码 pin：`work_dir` / `run_root` / `task_dir` / `Path(` 均不出现）。

### 2.1 草稿响应（本模块消费的字段）

`job_id / workflow / workflow_status / job_status / attempt / source_revision`、
`editable_spec{workflow,input,method,resources,molecule_name,task_name,remark,tags,project_id,execution_mode,target_node,config_path}`、
`method_projection`、`input_refs{original{kind,charge,multiplicity,items_count,source_job_id,from_artifact,materialized_input_xyz},
structure_items[],previous_outputs[],outputs_availability}`、
`effective_config{status,source,config}`、`capabilities{can_edit,can_in_place,can_new_job,disabled_reasons}`、
`preserved_fields / missing_fields / migration_hint / notes`。

### 2.2 预览请求体（模块实际发送的精确形状）

只发送 **变化过的 section** + 绑定字段：

```json
{
  "mode": "in_place",
  "workflow": "singlepoint",
  "expected_source_revision": "sr_…",
  "method": { "schema_id": "dft_sp", "levels": { "sp": { "functional": "r2SCAN-3c" } } }
}
```

- 未修改的 draft 只发 `mode / workflow / expected_source_revision` 三个键（测试 `Object.keys(...)` 钉住）。
- 不发送 `request_id` / `preview_fingerprint`（只有执行才带）、`molecule_name`、`config_path`、`output_dir`、`name`。
- `workflow` 恒等于 draft 的工作流：**本模块不支持换工作流**（`new_job` 也不换，见 §11）。
- 草稿「空方法/空资源」编辑器显示 `{}`，与原始 `{}` 比较相等，因此不会凭空生成 `method` / `resources` 段。

### 2.3 预览响应（本模块消费的字段）

`{ ok, mode, workflow, job_id, diff[{path,kind,old,new}], warnings[], blocking_reasons[], preview_fingerprint, source_revision }`

- `ok:true` 且无 `blocking_reasons` → `data-acp-preview-state="ok"`，执行启用；
- `ok:false` 或非空 `blocking_reasons`（如任务非终态）→ `blocked`，渲染全部阻塞原因，执行保持禁用；
- `diff` 原样渲染（path/kind/old→new，值截断 120 字符），空 diff 显示「无变更（服务端 diff 为空）」；
- `preview_fingerprint` 全文显示在 `data-role="preview-digest"`；
- 响应缺 `preview_fingerprint` / `source_revision` → 显式错误（`err.preview_payload`），不放行执行。

### 2.4 执行请求体

= 预览体 + 两个路由要求字段：

```json
{ "mode": "in_place", "workflow": "singlepoint", "expected_source_revision": "sr_…",
  "method": { … }, "request_id": "jobedit-…", "preview_fingerprint": "pf_…" }
```

- `request_id`：必填（后端空白 → 422）。每个「工作流 + 变更 patch + preview_fingerprint」组合生成一次；
  **失败重试复用同一 id**（网络错误/5xx 不清空），服务端 `_edit_request_payload` hash 排除
  `request_id / expected_source_revision / preview_fingerprint`，因此同一意图重试是幂等的；
  收到非 fingerprint/revision 的 409 时清空 id，下一次用新 id。
- `preview_fingerprint`：当前预览的指纹，必须与「提交时的配置」一致，否则 409 `preview_fingerprint_conflict`。
- `expected_source_revision`：draft 的 `source_revision` 原值，过期即 409 `source_revision_conflict`。
- 执行前再次用同一纯函数重建 body 并与预览时的 body 规范化比较；不一致直接本地失效预览，不发请求。

### 2.5 执行响应与 attempt 语义

`{ job_id, attempt, operation, status, replayed, diff_summary[] }`

- `operation:"in_place"`：后端 `JobManager._edit_in_place`（`manager.py:1906`）复用同一任务目录、`attempt+1`
  （CAS 单事务）、旧 attempt 回执归档；回执显示「原第 N 次 → 第 N+1 次（attempt+1）」与「同目录原地重跑」。
- `operation:"new_job"`：`_edit_new_job`（`manager.py:1957`）新建任务，`attempt = 1`，原任务不变。
- `attempt` 与服务端 draft 计算的期望值（in_place = draft.attempt+1，new_job = 1）**不一致**时显示醒目警告，
  不静默接受（测试 7 vs 2 的错位用例）。
- `replayed:true` → 显示「幂等重放」说明；不显示为第二次执行。

## 3. A-10：草稿加载与回填（无猜测默认值）

`mount` 时若 Context 已有 `taskRef.jobId`（或注入 `deps.jobId`）→ 自动 `GET …/edit-draft`；
焦点身份 = `(jobId, taskRef.attempt)`（与 `exec-detail` 一致）：任务变化**或同一任务 attempt 变化**
→ 丢弃旧草稿/预览/回执并载入新草稿；焦点清空 → 回到 `empty`。

| 面板字段 | 来源（逐字） | 缺失时 |
|---|---|---|
| 工作流 / 工作流状态 / 任务状态 / attempt / 源版本 | `workflow / workflow_status / job_status / attempt / source_revision` | （响应缺失即整体报错） |
| 分子名 / 项目 / 执行模式 / 目标节点 / 配置路径 / 标签 | `editable_spec.*` | 显示「未设置」 |
| 任务名 / 备注 | `editable_spec.task_name / remark` | 空字符串（与 draft 一致） |
| 方法 JSON | `editable_spec.method`（`JSON.stringify(…, null, 2)`） | `{}`（空对象，不是默认方法） |
| 资源 JSON | `editable_spec.resources` | `{}`（不补 nproc/mem 默认值） |
| 电荷 / 自旋多重度 | `editable_spec.input.charge / multiplicity` | 空（不写 0/1） |
| 输入类型 / 条目数 / 来源任务 / 来源产物 / 是否已物化 | `input_refs.original.*` | 「未设置」 |
| 可复用结构列表 | `input_refs.structure_items`（最多 20 行 + 剩余计数） | 空列表 + 「可复用结构 0 个」 |
| 可复用结构产物 | `input_refs.previous_outputs.length`（不渲染 xyz 正文） | 0 |
| 生效配置 | `effective_config.status/source(+config JSON)` | 显示「不可用（草稿仅按 spec 回填）」 |
| 方法投影（只读） | `method_projection` | `{}` |
| 编辑覆盖 | `workflow_status + capabilities + migration_hint + preserved_fields + missing_fields + notes` | — |

服务端权威（`job_edit.py:758 build_edit_draft` + `editable_spec_from_record`）明确：
`editable_spec` 是 `JobRecord.spec` 的投影，运行时字段（status/pid/remote_job_id/work_dir/result）不在其中，
前端也不写；`effective_config` 仅展示/参照，不参与编辑。

## 4. 编辑面与 patch 语义

可编辑：`mode`、`method`（JSON）、`resources`（JSON）、`input.charge`、`input.multiplicity`、
`task_name`、`remark`、`tags`；「放弃修改并回填」恢复 draft 原值。

- **只发送变化段**：后端 `_build_edited_spec`（`v1_routes.py:5904`）按 `model_fields_set` 合并：
  `merge_edit_section` 深合并、`merge_edit_input` 合并输入并在几何来源被替换时清理旧来源描述。
  本模块从不替换几何，因此只会出现 `input.charge/multiplicity` 的标量补丁。
- `method` 修改走 `apply_edited_parameters`（`edit_parameters.py:262`）：当 `req.method != spec.method` 时，
  `edited_levels = req.method.levels`，变更的 level 字段投影到执行别名（例如
  `levels.sp.functional`），预览 diff 即为 `method.levels.sp.functional`。
- 若方法/资源/名称与 draft 完全一致 → patch 为空，预览体只有绑定字段（服务端 diff `[]`）。
- 任务名清空：后端 `task_name = str(req.task_name or "").strip() or record.spec.task_name`，
  **无法通过编辑清空**；本面板显式提示该限制（`form_notice_task_name`），不伪造成功。
- 类型保真：资源/方法以原始 JSON 编辑，`nproc: 8` 仍是数字、`mem: "16GB"` 仍是字符串；
  不做 `parseInt` 猜测，也不丢弃草稿里未知的键。

## 5. A-11：预览（零副作用）

`POST …/edit-recalculate/preview` 只做校验 + diff + 指纹，不建任务/attempt、不 reserve、不写盘
（后端路径 `preview_job_edit_recalculate`：`compute_source_revision` → `_build_edited_spec`
（内含 `merge_edit_*` / `apply_edited_parameters` / `_validate_edited_execution`）→ `_edited_spec_diff`
→ `compute_preview_fingerprint`；无 store 写入、无 attempt）。

面板渲染：

- 变更清单行：`path · kind：old → new`，各自带 `data-diff-path` / `data-diff-kind`；
- `warnings[]` 原文（如「资源已修改（resources.nproc）」「输入已修改…」）；
- `blocking_reasons[]`（`data-preview-blocking`）→ 执行禁用；
- `preview_fingerprint` 摘要；
- **无执行请求**（测试断言 preview 后 `executeCalls = 0`）。

## 6. A-12：执行、回执与覆盖提示

- 执行门槛：草稿 `ready` 且未 stale、`mode` 可用、预览 `ok`、表单自预览后未变、
  重建 body 与预览 body 完全一致、未完成过同一 key；任一不满足 → 按钮禁用 + title 说明。
- 回执展示：`任务 {jobId} · 第 {attempt} 次尝试 · 状态 {status} · 模式 {原地重算|创建新任务}`、
  attempt+1/同目录行、**编辑覆盖行**（工作流/状态/保留方法字段数/缺字段数）、本次变更数、幂等重放行。
- 执行成功后 `in_place` 会改变 `source_revision`（attempt 进入 revision），模块立即把草稿标记为 stale：
  禁止再预览/执行，提示重新载入草稿（避免用旧 revision 继续写）。
- 回执后可选 `commands.openTask({jobId, attempt, source:"job-edit"})`（导航，不写任务）。

错误映射（预览与执行共用 `mapError`）：

| HTTP / 条件 | 呈现 | 附带状态 |
|---|---|---|
| 404 | 「任务不存在（可能已被清除）」 | 草稿标记 stale，必须重载 |
| 401 / 403 | 「没有权限执行该操作」 | 保持草稿，可重试 |
| 409 `source_revision_conflict` | 「源任务配置已变化，请重新载入编辑草稿」 | 草稿标记 stale |
| 409 `preview_fingerprint_conflict` | 「提交配置与预览不一致，请重新预览」 | 清空预览，要求重新预览 |
| 其他 409（request_id 冲突 / manager EditConflictError） | 「冲突：{detail}」 | 清空 request_id，下次用新 id |
| 422（EditValidationError / invalid mode / request_id 缺失等） | 「参数未通过服务端校验：{detail}」 | 保持草稿 |
| 400（执行目标校验，detail.code） | 「请求被拒绝：{detail}」 | 保持草稿 |
| 5xx | 「服务端错误：{detail}」 | 保持草稿 |
| 无 status（网络/超时） | 「网络错误：{detail}」 | 保持草稿；request_id 复用 |

草稿载入错误：404 → 任务不存在；其他 → 「编辑草稿载入失败：{detail}」；响应结构不全 →
「缺少必需字段（job_id / workflow / editable_spec / source_revision / attempt / capabilities）」；
overlay 提供重试（`data-action="retry"`）。

## 7. 四态、忙碌与守卫

模块根 `data-acp-module-state` ∈ `empty | loading | ready | error`（**草稿生命周期**）：

| 状态 | 触发 |
|---|---|
| `empty` | 无任务焦点 / 尚未载入（初始、焦点清空、dispose 后） |
| `loading` | 草稿请求进行中（overlay 可见，重试隐藏） |
| `ready` | 草稿载入成功（含不可编辑工作流，此时 coverage=blocked） |
| `error` | 草稿请求失败或响应结构不全（overlay + 原因 + 重试） |

命令状态独立：`data-acp-busy` = `none | draft | preview | execute`；另有
`data-acp-mode`、`data-acp-editable`、`data-acp-coverage`（editable/blocked）、
`data-acp-preview-state`（idle/invalid/running/ok/blocked/error）、
`data-acp-receipt-state`（idle/running/submitted/error）、`data-acp-draft-stale`、`data-acp-dirty`。

守卫：

- `state.generation`（mount/dispose/焦点任务或 attempt 更换）+ 每类请求 token（draft/preview/execute）；
  任何迟到结果在写 DOM 前被丢弃（dispose、切任务、重新预览都不会串台）。
- 忙碌期间按钮禁用，重复点击被忽略（`busy !== "none"` 提前返回），不会重复 POST。
- `suspend()` 只暂停绘制（结果仍落在 state），`resume()` 补绘；不取消已发出的请求。
- `dispose()` 幂等：移除自有 DOM、context 订阅、`acp:langchange` 监听与全部事件监听；
  dispose 后可重新 `mount`（不复用旧草稿/预览/回执，重新读取）。

## 8. i18n（zh 内联 + data-i18n）

模块自带 `ZH` 兜底字典（Shell 字典缺 key 时 `t()` 回退本地 zh 文本；`ACPShell.i18n.t` 只回显 key）。
静态文案带 `data-i18n`；动态文案随 `acp:langchange` 重绘。

需要在 `frontend/js/shell/i18n.js` 补的 key（zh + en，共 **135** 条；zh 值与模块内联文本逐字一致）：

```
next.jobedit.title
next.jobedit.subtitle
next.jobedit.errorTitle
next.jobedit.errorMessage
next.jobedit.source_title
next.jobedit.job_ref
next.jobedit.job_no_ref
next.jobedit.load
next.jobedit.reload
next.jobedit.loading
next.jobedit.empty
next.jobedit.empty_hint
next.jobedit.draft_ready
next.jobedit.draft_stale
next.jobedit.draft_error
next.jobedit.retry
next.jobedit.value_unset
next.jobedit.yes
next.jobedit.no
next.jobedit.coverage_title
next.jobedit.coverage_editable
next.jobedit.coverage_blocked
next.jobedit.coverage_reason
next.jobedit.coverage_hint
next.jobedit.coverage_missing
next.jobedit.coverage_preserved
next.jobedit.notes_title
next.jobedit.original_title
next.jobedit.field_workflow
next.jobedit.field_workflow_status
next.jobedit.field_job_status
next.jobedit.field_attempt
next.jobedit.field_source_revision
next.jobedit.field_molecule_name
next.jobedit.field_project_id
next.jobedit.field_execution_mode
next.jobedit.field_target_node
next.jobedit.field_config_path
next.jobedit.field_tags
next.jobedit.input_title
next.jobedit.input_kind
next.jobedit.input_charge
next.jobedit.input_multiplicity
next.jobedit.input_items
next.jobedit.input_source_job
next.jobedit.input_artifact
next.jobedit.input_materialized
next.jobedit.input_structures
next.jobedit.input_structure_item
next.jobedit.input_more
next.jobedit.input_available
next.jobedit.input_missing
next.jobedit.input_outputs
next.jobedit.input_outputs_state
next.jobedit.effective_title
next.jobedit.effective_state
next.jobedit.effective_unavailable
next.jobedit.edit_title
next.jobedit.mode
next.jobedit.mode_in_place
next.jobedit.mode_new_job
next.jobedit.mode_hint_in_place
next.jobedit.mode_hint_new_job
next.jobedit.method
next.jobedit.method_hint
next.jobedit.resources
next.jobedit.resources_hint
next.jobedit.charge
next.jobedit.multiplicity
next.jobedit.task_name
next.jobedit.remark
next.jobedit.tags
next.jobedit.projection
next.jobedit.reset_form
next.jobedit.form_notice_no_changes
next.jobedit.form_notice_task_name
next.jobedit.form_notice_stale
next.jobedit.err.draft_missing
next.jobedit.err.workflow_blocked
next.jobedit.err.mode_invalid
next.jobedit.err.mode_unavailable
next.jobedit.err.method_json
next.jobedit.err.method_object
next.jobedit.err.resources_json
next.jobedit.err.resources_object
next.jobedit.err.charge_integer
next.jobedit.err.multiplicity_integer
next.jobedit.err.multiplicity_positive
next.jobedit.err.unknown
next.jobedit.preview_title
next.jobedit.preview
next.jobedit.previewing
next.jobedit.preview_idle
next.jobedit.preview_ok
next.jobedit.preview_blocked
next.jobedit.preview_error
next.jobedit.preview_need_valid
next.jobedit.preview_need_draft
next.jobedit.preview_digest
next.jobedit.diff_title
next.jobedit.diff_row
next.jobedit.diff_empty
next.jobedit.warnings_title
next.jobedit.blocking_title
next.jobedit.execute_title
next.jobedit.execute
next.jobedit.executing
next.jobedit.execute_idle
next.jobedit.execute_error
next.jobedit.receipt
next.jobedit.receipt_body
next.jobedit.operation_in_place
next.jobedit.operation_new_job
next.jobedit.receipt_attempt_ok
next.jobedit.receipt_attempt_new
next.jobedit.receipt_attempt_mismatch
next.jobedit.receipt_coverage
next.jobedit.receipt_diff
next.jobedit.receipt_replay
next.jobedit.open_task
next.jobedit.execute_need_preview
next.jobedit.err.load
next.jobedit.err.draft_payload
next.jobedit.err.preview_payload
next.jobedit.err.receipt_payload
next.jobedit.err.not_found
next.jobedit.err.forbidden
next.jobedit.err.source_revision_conflict
next.jobedit.err.preview_fingerprint_conflict
next.jobedit.err.conflict
next.jobedit.err.invalid_spec
next.jobedit.err.bad_request
next.jobedit.err.server
next.jobedit.err.network
next.jobedit.err.client_missing
```

动态族：`t("next.jobedit." + FORM_ERROR_KEYS[code])`（`err.*`）与
`t("next.jobedit.operation_" + operation)`，具体 key 全部在上面列表中。

## 9. 测试与实测证据

命令（本机 Node，任务指定的 node.exe）：

```bash
"C:\Users\彭扬超\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" \
  frontend/js/module_tests/run_all.js acp2_job_edit
```

结果（2026-10-31 实测）：**29 passed / 0 failed，exit 0**。

覆盖：

1. 注册面：唯一 `ACPNextModules` key、`ACPShell.registry.validateDescriptor` 通过、无 legacy 全局、三条路由精确 URL；
2. `empty`：无任务焦点、动作禁用、mount 零请求、zh 回退；
3. A-10 回填：只读字段/方法投影/输入摘要/结构条目/生效配置/覆盖/缺失字段逐项等于 payload；
   方法/资源 JSON 逐字、电荷/多重度/名称/备注/标签原值；
4. A-10 无默认值：缺省 method/resources → `{}`、缺省 charge/multiplicity → 空、
   「未设置」而非猜测；未修改表单的预览体**只有** `mode/workflow/expected_source_revision`；
5. 草稿错误：404 / 结构不全 / 网络失败 → error overlay + 重试恢复；
6. A-11：只发变化段、diff 行（path/kind/old→new）、warnings、digest、零执行请求；
7. A-11 空 diff：无变更说明；服务端 blocking → `blocked` 且执行禁用；
8. A-11 元数据与电荷/多重度：只补丁对应顶层字段，回退后不再发送；
9. A-11 422 与 409 source-revision：可读文案 + 草稿 stale + 重载恢复；
10. A-12：执行体 = 预览体 + `request_id` + `preview_fingerprint` + `expected_source_revision`；
    回执 attempt 2、同目录 attempt+1、覆盖行、openTask、成功后 stale；
11. A-12 门槛：无预览/预览后编辑 → 零执行请求；
12. A-12 幂等：失败重试复用同一 `request_id`；`replayed:true` 显示重放说明；
13. A-12 409 fingerprint：清空预览、要求重新预览、不导航；422/403/409/500/404 映射；
14. attempt 错位（7 vs 预期 2）→ 醒目警告；
15. 模式：in_place 被服务端禁用 → 默认 new_job + 覆盖原因 + 新任务回执行；
16. 完全禁用工作流（retired）：coverage blocked、迁移建议、零预览/执行；
17. 忙碌：双击不重复请求、预览中不可执行、执行中按钮禁用；
18. 本地校验：坏 JSON / 非整数 / 多重度 <1 → `invalid`，零请求；
19. dispose：移除 DOM、幂等、迟到 draft/preview 不重建 DOM/不 openTask；remount 重新回填且不残留编辑；
20. 焦点切换：旧任务迟到预览不串台，新任务草稿立即回填；清空焦点 → empty；
21. 焦点 attempt 变化：重载草稿、丢弃旧编辑、清空预览/回执；
22. suspend/resume：暂停绘制、恢复后补绘；
23. 无 client seam：显式报错、零网络；
24. 边界 pin：无 `fetch(` / `XMLHttpRequest` / `localStorage` / `jobCommands` / 3D / 文件路径词汇；
    只调用三条 edit 路由；
25. i18n 静态 key、zh 回退、语言切换重绘；
26. CSS 契约：模块作用域、错误回执样式、窄屏 fallback、无新 token / 无全局选择器。

全量回归（同一 node，2026-10-31 22:06）：`node frontend/js/module_tests/run_all.js` →
**1001 passed / 1 failed，exit 1**；唯一失败项是工作区并发开发中的
`research-record: a late submit after a selection switch never paints`（另一个 agent 的模块，
不属于本任务文件面），本模块 **29/29 全绿**。并发模块仍在变动，全量数字会继续漂移；
本模块验收以 `acp2_job_edit` 过滤结果为准。

后端契约核对（一次性 Python 进程；**非 HTTP 运行**）。Windows 无 POSIX `fcntl`，脚本内仅在
`sys.modules` 放 import 桩，不改仓库代码；`acp` 可从 `src` 导入：

- 模块预览体经 `V1EditPreviewRequest` 解析后 `model_fields_set == {mode, workflow, expected_source_revision, method}`；
  执行体再加 `{request_id, preview_fingerprint}`，与路由要求一致；
- `merge_edit_section(method_orig, patch)` 保留未提交字段；`merge_edit_input(input, {charge:-1})`
  只改电荷、几何原文不变；
- `diff_editable_specs(原 spec, 原 spec)` → `[]`（未修改回填不产生 diff）；
  改 `levels.sp.functional` → `[('method.levels.sp.functional', 'method')]`；
  改 charge/multiplicity → `[('input.charge','input'), ('input.multiplicity','input')]`；
  改名称/备注/标签 → `[('remark','meta'), ('tags','meta'), ('task_name','meta')]`；
- `compute_preview_fingerprint` 对相同输入稳定（本次 `pf_4de5c2c5a6b14d14e8432e2474fd8082`），
  格式 `pf_` + 32 hex；
- `v1_router.routes` 中存在且仅存在所需方法：`GET /jobs/{job_id}/edit-draft`、
  `POST /jobs/{job_id}/edit-recalculate/preview`、`POST /jobs/{job_id}/edit-recalculate`
  （router 无 prefix；`server.py:554` 以 `/api/v1` 挂载）。

这些证明**请求形状/diff 语义/指纹/路由存在性**与后端契约一致；不等于真实 HTTP、真实 attempt 或浏览器行为已验收。

## 10. 接入下一步（本次文件边界外，未改动）

1. `frontend/next/index.html`：`<head>` 增加
   `<link rel="stylesheet" href="/css/modules/job-edit.css">`；
   在 `<script src="/js/shell/bootstrap.js"></script>` 之后的「real modules」块
   （现有 `/js/modules/*/module.js` 列表，约 236–250 行）增加
   `<script src="/js/modules/job-edit/module.js"></script>`，且必须位于内联
   `ACPShell.bootstrap.boot()` 之前：loader 的 P0 `dynamicResolver` 为 null，
   只从静态 `window.ACPNextModules` 取模块，脚本不加载就不会注册。
2. `frontend/js/shell/recipes.js`：在 Execution 工作区选择槽位/默认可见性，面板需要
   `titleKey: "shell.module.job_edit"`；当前**未接默认 recipe**，默认布局不会自动出现该面板
   （可由布局编辑器手动选用）。
3. `frontend/js/shell/i18n.js`：补 §8 的 135 条 `next.jobedit.*` key，以及面板标题
   `shell.module.job_edit`（zh「编辑重算」/ en「Edit & Recalculate」），共 136 条字典项
   （A-61 批次；en 缺失前静态元素在 en 下显示 zh）。
4. `frontend/js/module_tests/acp2_i18n_new_modules.test.js`（A-61 覆盖测试）：把 `"job-edit"` 加入
   `MODULES`、把 `"next.jobedit."` 加入 `OWNED_PREFIXES`；否则该测试不会扫描本模块的新 key。
5. 浏览器用例：`frontend/js/module_tests/acp2_next_entry.browser.js` 增加
   「选中任务 → 回填 → 改参数 → 预览 → 执行回执」场景（本次未执行浏览器套件，不计 PASS）。
6. parity 记录：A-10/A-11/A-12 的「模块注册 + 四态 + 测试 + 键组」四项中，键组依赖第 3 步；
   其余三项已完成（代码/契约层）。

## 11. 已知缺口 / 未验证

- **未在真实浏览器执行**：只跑 Node vm 套件；`next/index.html` 未加载本模块（文件边界限制），
  真实挂载/点击/布局/焦点联动仍为 **NOT_VERIFIED**。
- **未对真实 HTTP 服务执行**：草稿/预览/执行只经注入 client 验证；真实 200/409/422、SQLite 幂等记录、
  attempt 归档目录与远端 `_job_read_root` 行为未在本机运行（需要可运行实例）。
- **结构来源不可换**：A-10/A-12 只在原输入上改参数；换结构/引用 last_structure/previous_outputs/path_frame
  属 P04（A-13…A-15），本模块仅只读展示（`structure_items` / `previous_outputs` 计数）。
  服务端对 in_place 改结构会 422（「已更换计算输入结构…请选择创建新任务」），本模块不触发、也不吞掉该错误。
- **不支持换工作流**：`new_job` 模式保留原工作流；换工作流（如 PESsearch→scan）需要目标工作流的
  完整 input/method 契约，属向导 P01/A-07 面。模块在 README/提示中显式说明，不静默改 `workflow`。
- **方法编辑是 JSON 文本面**，不是按 catalog 字段生成的可读表单：方法/基组/档位的友好投影属
  P01/A-07（`task-wizard`）；本模块保证「真实冻结参数逐字回填 + 只发改动字段」。
  编辑 `levels.*` 时依赖后端 `apply_edited_parameters` 的别名投影（已核对）。
- **项目 / 目标节点 / 执行模式 / 配置路径 / molecule_name 只读**：从 draft 原样展示，不提交这些字段
  （服务端保持原值）；如需改项目/节点属 P06/P07 运维面。
- **BatchOptimize 条目级编辑未覆盖**：`input.items`/`batch_roles` 不在此面板编辑（方法与资源可改）；
  条目级批量编辑属 A-07/P03 其余工作。
- **任务名无法清空**（后端 `or record.spec.task_name` 兜底）：面板提示该限制，不假装清空成功。
- **无草稿持久化 / 无自动轮询**：刷新后需重新载入（A-09 草稿面、P02 属其他任务）；
  执行成功后不自动重载草稿，而是标记 stale 要求手动重载（避免旧 revision 继续写）。
- **charge/multiplicity 的化学合法性由服务端预览权威裁决**（本模块只做整数/≥1 的本地拦截，
  不做电子奇偶推断，避免伪造化学规则）。
- **suspend 不取消请求**：只暂停绘制；真正的请求取消/超时沿用页面传输默认预算（8s，与既有模块一致）。
- **zh-only**：Shell 字典尚无 `next.jobedit.*`；en 显示 zh（A-61）。
- **未改共享文件**：`frontend/next/index.html`、`frontend/js/shell/*`、`frontend/css/workbench/*`、
  既有模块/测试均未修改；没有 git commit。

## 12. 纯函数测试缝（`_internals`）

`ROUTES`、`MODES`、`readBackfill(draft)`、`defaultMode(view)`、`formFromDraft(draft)`、
`formSnapshot(form)`、`validateForm(view, form)`、`buildPatch(view, form, check)`、
`buildPreviewBody(view, form)`、`buildExecuteBody(view, form, requestId, fingerprint)`、
`expectedAttempt(view, operation)`、`canonicalJson(value)`、`parseTags(text)`、
`integerOrNull(value)`、`formatValue(value)`、`isDraftPayload(payload)`、
`isPreviewPayload(payload)`、`isReceiptPayload(payload)`、`newRequestId()`、
常量 `MAX_STRUCTURE_ROWS(20)`。

测试用这些函数钉住「回填零默认值 → 只发改动段 → 指纹/attempt 计算」，UI 用例再钉住
事件/状态/请求次数/守卫，两者互补。
