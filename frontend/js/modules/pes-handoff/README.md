# pes-handoff — ACP 3.0 路径 → Batch 衔接（A-51 / P17）

- 文件：
  - `frontend/js/modules/pes-handoff/module.js`（classic script，无构建、无 ESM）
  - `frontend/css/modules/pes-handoff.css`（仅消费 `frontend/css/workbench/base.css` / `shell/shell.css` 的变量；无全局选择器、无新 token）
  - `frontend/js/module_tests/acp2_pes_handoff.test.js`（Node vm + fake DOM + 注入 request/commands，网络零直连）
- 注册名：`window.ACPNextModules["pes-handoff"]`（唯一新增全局）
- 对应计划：[ACP 3.0 前端计划](../../../../docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md)
  Track A **A-51**（`P17 与 PES / Batch 的衔接`），前置 **A-49**；
  验收要点：**选点→Batch 有据可查**——候选来源、服务端 rank/能量/溯源可见，预览/确认/回执只走既有路由，
  不在客户端重算科学排名、不伪造提交成功。

---

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["pes-handoff"] = {
  descriptor: {
    id: "pes-handoff",
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["batch.handoff"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["batch.handoff"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.handoff.errorTitle", messageKey: "next.handoff.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ },
  _internals: { /* 纯函数测试缝，见 §10 */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise<void>`、`updateContext(snapshot)`、`suspend()`、`resume()`、`dispose()`。
`mount` 只接受 DOM host；重复 mount 被拒绝（先 dispose）；dispose 后可重新 mount。

`deps`（全部可注入、可 mock）：

| 字段 | 内容 |
|---|---|
| `doc` | DOM document（缺省 `window.document`） |
| `request` | 现有客户端单点：`(path, opts) → Promise<parsedBody \| text>`（缺省回落 `deps.client.transport.request` → `window.ACPClient.transport.request`） |
| `context` | Shell 工作区上下文；只消费 `taskRef.jobId` |
| `commands` | 可选；只用 `commands.openTask({source:"pes-handoff", jobId, attempt})` 导航到回执任务 |
| `initialJobId` | 可选；挂载后直接载入该任务 |

全部缺失时载入/预览/提交都给出明确错误且**不发任何请求**；模块从不调用 `window.fetch` / `XMLHttpRequest` /
`sendBeacon`，也不读写 `localStorage`。

---

## 2. 端点（全部既有；本任务没有新增后端）

| 用途 | 请求 | 消费字段 |
|---|---|---|
| 任务身份 | `GET /api/v1/jobs/{jobId}` | `spec.workflow`、`status`、`attempt`、`spec.input.charge/multiplicity`、`molecule_name` |
| PES 已确认选点（`workflow === "PESsearch"`） | `GET /api/v1/jobs/{jobId}/pes/review` | `status`、`review.selected[]`（`candidate_id/role/frame_index/name/structure_path/frame_converged`，可选 `rank`）、`review.revision/confirmed_at`、`review.source.*` |
| 扫描帧候选（其他工作流） | `GET /api/v1/jobs/{jobId}/frame-candidates` | `revision`、`candidates[]`（`candidate_id/view_type/frame_index/role/name/structure_path/saved_at/role_index/display_label/item_id`，可选 `rank`） |
| 能量 | `GET /api/v1/jobs/{jobId}/energy-graph` | `view_type`、`default_series`、`series[].label/unit`、`nodes[].frame_index/energy/status`、`annotations[].candidate_id/y` |
| 候选结构文本 | `GET /api/v1/jobs/{jobId}/files/RESULT/structures/{candidateId}.xyz` | 原始 XYZ 文本（PES review 与 frame-candidates 两个写入方的既有产物路径） |
| profile 目录 | `GET /api/v1/method-catalog` | `method_schemas.batch_optimize.profiles[].profile_id/label/label_zh` |
| 配置预览 | `POST /api/v1/batch-optimize/config-preview` | `{method:{profile}}` → `schema/config_key/roles.int.effective/roles.ts.effective/orca_summary` |
| 提交预检 | `POST /api/v1/preflight` | 同一个 BatchOptimize body → `feasible/reasons/advisories/plan_digest` |
| 提交 | `POST /api/v1/jobs` + `Idempotency-Key` | body = 冻结 body + `preflight_fingerprint` → `V1JobCreatedResponse`（`job_id/status/attempt/plan_digest/idempotent_replay`） |

**刻意不调用**：`batch_structures` 多 item 提交、`/s2/*`、`/mechanism-studies/*`、`/drafts`、v2 路由、
任何“由前端拼出的新路由”。源码 pin 测试断言 POST 只有上表 3 条，GET 只有上表 6 条。

---

## 3. 为什么是“每个候选一个 BatchOptimize 任务”（服务端布局限制，非前端发明）

源码核对（2026-10-31）：

- `src/acp/api/submission_contract.py`：`BatchOptimize` 的 `batch_structures_only=True`，`input.items` 长度 > 1
  直接 422 `layout_limit`（“BatchOptimize runs one structure per task (single_flat layout)”）。
- `src/acp/calculations/batch/engine.py`：`single_flat` + `len(items) != 1` → 抛错。
- `src/acp/scheduler/runner.py`：所有 scheduler 的 BatchOptimize dispatch 都追加 `--layout-mode single_flat`。

因此 A-51 的 handoff 是 **N 个独立 BatchOptimize 任务**（N = 选中的候选数），每个任务恰好一个结构。
这与经典 Workbench 的 Batch 扇出（`submitJobBatch` / per-structure loop）一致，也保证单个候选的
逐项回执/job 归属可查。模块在 UI 上显式说明该限制。

> **已核对的既有链路缺口（不据此发明路由）**：指导文本推荐的 `input.from_artifact + selected_ids`
  在 `create_job` 的 `BatchOptimize` 分支**没有**经过 `_resolve_stage_artifact_ref`（只有 PESsearch / IRC 分支
  调用它）；`input.from_artifact` 会以相对路径原样传给子进程（runner cwd = 新任务目录），因此当前 HTTP 提交
  不能把上游 PES 任务目录解析到新任务。本模块因此使用**已经可工作的** `source_type:"xyz_text"`（单结构）+
  `selected_ids` + `source_job_id`，与经典 Workbench 的 Batch 提交形状相同；不在前端伪造
  `from_artifact` 解析，也不新增后端路由。若后端以后为 BatchOptimize 增加 `source_job_id+from_artifact`
  解析，应作为显式契约扩展替换本段实现（候选选择/确认/回执不变）。

---

## 4. 候选、排序与溯源（只显示服务端事实）

- **候选来源**：PES 任务只读 `pes/review.selected`（未确认/pending → `empty`，不显示算法推荐点、不伪造候选）；
  其他任务只读 `frame-candidates.candidates`。
- **角色**：`TS → ts`、`INT/intermediate/endpoint → intermediate`（词形归一，不做化学推断）。
- **能量**：按 `frame_index` 连接 `energy-graph` 节点；取不到时再按 `candidate_id` 连接 annotation `y`；
  单位/序列名来自默认序列（如 `相对能量 kcal/mol`、`扫描能量 Eh`）。取不到显示 `—（能量图未提供）`，绝不补 0。
- **排名**：只显示服务端字段 `candidate.rank`（若提供）；缺失显示 `—（服务端未提供）`。
  模块**不排序、不归一化、不计算**任何科学排名；`role_index/display_label` 只作为“服务端序”单独展示，
  不当作排名。行顺序严格保持服务端返回顺序。
- **溯源**：每行显示来源（PES 人工确认 / 帧候选）、`confirmed_at` 或 `saved_at`、`view_type`、
  `item_id`、`selection_source`、`frame_converged`（若提供）；顶部概览显示任务/workflow/status、
  候选来源、review revision + frame_source、确认时间、候选数、已选数。
- **选择**：默认全选服务端候选；可逐项勾选/全选/清空。改变选择或 profile 会使已完成的预览失效（`stale`），
  必须重新预览后才能确认。

---

## 5. 预览 / 确认 / 提交 / 回执

### 5.1 预览（不创建任务、不发 `POST /jobs`）

1. 选中 ≥1 个候选、profile 就绪（来自 `/method-catalog`）；
2. 逐个读取候选结构文本（`RESULT/structures/<candidateId>.xyz`），校验：
   - ≥1 个原子、行数足够（有效 XYZ）；
   - 第二行注释包含 `candidate_id=<candidateId>`（否则 `--select` 无法绑定，预览直接失败，不提交）；
3. 调 `POST /batch-optimize/config-preview`，body `{method:{profile}}`；显示服务端 `config_key` +
   INT/TS 生效配置；
4. 对每个候选调用 `POST /preflight`，body 与后续 submit body **完全相同**（不含 fingerprint）；
   - 任一 `feasible:false` 或缺 `plan_digest` → 预览失败并显示 server reasons（不提交）；
   - 端点 404/405 → 显式“预检端点不可用（404）…；未提交任何任务”；
5. 成功：`data-acp-preview-state="previewed"`，逐候选显示 `plan_digest`/feasible/advisories；
   **此时仍未创建任何任务**。

### 5.2 显式确认

- “确认并提交…”按钮只在预览有效且未提交时可用；点击仅打开确认框（`data-acp-confirm-open=true`），
  **不发任何请求**；
- 确认框列出数量、候选 ID 串、profile，并再次声明服务端 single_flat 限制；
- 只有“确认提交”按钮会触发 `POST /jobs`；取消只关闭确认框。

### 5.3 提交 body（每个候选一个）

```json
{
  "schema_version": 1,
  "workflow": "BatchOptimize",
  "input": {
    "source_type": "xyz_text",
    "source": "<候选 XYZ 原文（含 TAG/candidate_id 注释）>",
    "selected_ids": ["pes_ts_frame_005"],
    "source_job_id": "20261031_001_PESsearch",
    "charge": 0,
    "multiplicity": 1
  },
  "method": { "profile": "opt_freq" },
  "preflight_fingerprint": "sha256:…"
}
```

- 仅使用 `submission_contract.WORKFLOW_CONTRACTS["BatchOptimize"].input_keys` 内的键；
- `selected_ids` 由 runner 转成 `--select <candidate_id>`（`scheduler/jobs.py::batchoptimize_method_flags`），
  因此结构注释必须携带同一 `candidate_id`（预览已强校验）；
- `source_job_id` 让服务端继承来源任务的 molecule identity / 显式执行节点
  （`v1_routes.py::_resolve_job_molecule_name`、`_inherit_stage_execution_fields`），把“选点→任务”的来源
  留在 job spec 中；
- `charge/multiplicity` 仅在源任务 `spec.input` 提供整数时继承；缺失则不下发，交给服务端默认，不猜；
- 请求头 `Content-Type: application/json` + `Idempotency-Key: pes-handoff:<jobId>:<candidateId>:<plan_digest>`。
  同一个冻结 body/摘要重试会命中服务端幂等账本（replay 同一任务），不会产生第二个任务；
- 每个候选一个请求；任一失败立即停止后续提交（已成功的回执保留），不会“部分提交 + 全绿”伪装。

### 5.4 回执

`V1JobCreatedResponse` 只接受真实 `job_id`：缺 `job_id` → 非法回执（不显示成功）。每行回执显示：
候选 ID → 任务 ID、状态、attempt、`plan_digest`、是否幂等重放；注入 `commands.openTask` 时提供“打开任务”。
`data-acp-receipt-count` 与 `data-acp-handoff-state ∈ idle|submitting|submitted|error` 同步。

---

## 6. 四态、命令状态与守卫

根 `data-acp-module-state ∈ empty | loading | ready | error`（数据态）：

| 状态 | 触发 |
|---|---|
| `empty` | 未载入；或已载入但无候选（PES pending 给专属提示） |
| `loading` | 任务 + 候选/能量/方法目录读取中 |
| `ready` | 有候选（方法目录失败仍 ready，但配置态 error 且预览被阻止） |
| `error` | 任务读取失败，或候选端点失败（404/405 显式“候选端点不可用”） |

命令态：`data-acp-busy ∈ none|load|preview|submit`；`data-acp-preview-state ∈ idle|previewing|previewed|stale|error`；
`data-acp-handoff-state`；`data-acp-config-state ∈ idle|loading|ready|error`；`data-acp-confirm-open`；另有
`data-acp-candidate-count`、`data-acp-selection-count`、`data-acp-receipt-count`、`data-acp-warning-count`、`data-acp-profile`。

守卫：

- `state.generation`：mount/dispose 递增；迟到结果不写 DOM；
- `state.token`：每次载入递增；任务切换后旧候选/能量结果作废；
- `state.previewSeq`（不随载入重置）：每次预览/提交递增；旧预览结果不能覆盖新预览，dispose 后不能继续
  config-preview/preflight/submit 链；
- 提交中的重复点击被 busy 忽略；已提交的 preview 不能再次提交；
- 提交进行中忽略外部任务焦点变化（保留当前回执视图；warning 显式说明），避免丢回执后误重复提交；
- 预览成功但后改选择/profile → `stale` + 确认禁用；预览被失效时释放 busy 并回到可重试状态；
- dispose 幂等：移除 DOM、context 订阅、`acp:langchange` 监听；dispose 后可 remount。

---

## 7. 错误映射（不伪造成功）

| 情况 | 表现 |
|---|---|
| 方法目录/候选端点 404/405 | 显式端点不可用（含 HTTP 状态）+ 不提交 |
| config-preview / preflight 404/405 | 显式“批处理预览端点/预检端点不可用（404）…；未提交任何任务” |
| 候选结构 404/无效 XYZ/缺 `candidate_id` | 预览错误（含候选 ID 与原因），不进入 config/preflight |
| preflight `feasible:false` | 预览错误 + server reasons（field/rule/message），不提交 |
| 提交 400 `preflight_stale` | “预览已过期，请重新预览”；preview 置 `stale` |
| 提交 401/403 | “无提交权限（HTTP …）” |
| 提交 409 | 冲突（含 idempotency 冲突/进行中），不重放 |
| 提交 404 | 提交端点不可用（含 server detail） |
| 提交 5xx/422 等 | “提交请求被拒绝（HTTP …）：<detail>”，保留已成功回执 |
| 传输失败（无状态） | “提交结果未确认（…）：请先在任务队列核对，勿盲目重复提交。”绝不当作成功 |
| 提交中预览/选择被改变 | “提交过程中预览/选择已变化；已完成的任务回执保留，其余未提交。” |
| 提交中外部任务焦点变化 | 忽略焦点变化（不丢回执）；warning “已忽略任务焦点变化；请等待回执完成后再切换。” |

---

## 8. i18n（zh 内联兜底 + `data-i18n`，共 92 条）

需要在 `frontend/js/shell/i18n.js` 补的 key（zh + en，共 **92** 条）：

```
next.handoff.title
next.handoff.subtitle
next.handoff.source_title
next.handoff.source_hint
next.handoff.job_id
next.handoff.job_id_ph
next.handoff.load
next.handoff.reload
next.handoff.summary_title
next.handoff.summary_job
next.handoff.summary_workflow
next.handoff.summary_status
next.handoff.summary_source
next.handoff.summary_review
next.handoff.summary_confirmed
next.handoff.summary_count
next.handoff.summary_selected
next.handoff.candidates_hint
next.handoff.candidates_empty
next.handoff.candidates_pending
next.handoff.rank_none
next.handoff.rank_value
next.handoff.label_value
next.handoff.energy_none
next.handoff.select_all
next.handoff.select_none
next.handoff.selection_summary
next.handoff.profile_hint
next.handoff.profile_label
next.handoff.preview_title
next.handoff.preview
next.handoff.preview_hint
next.handoff.previewing
next.handoff.preview_idle
next.handoff.preview_ok
next.handoff.preview_error
next.handoff.preview_config
next.handoff.preview_roles
next.handoff.preview_item
next.handoff.preview_reasons
next.handoff.preview_advisories
next.handoff.preview_stale
next.handoff.handoff_title
next.handoff.confirm_hint
next.handoff.request_confirm
next.handoff.confirm_title
next.handoff.confirm_body
next.handoff.confirm_submit
next.handoff.confirm_cancel
next.handoff.submitting
next.handoff.receipt_title
next.handoff.receipt_empty
next.handoff.receipt_item
next.handoff.receipt_replay
next.handoff.receipt_error
next.handoff.receipt_invalid
next.handoff.open_task
next.handoff.submitted
next.handoff.submit_partial
next.handoff.empty_title
next.handoff.empty_hint
next.handoff.loading_title
next.handoff.error_title
next.handoff.errorTitle
next.handoff.errorMessage
next.handoff.retry
next.handoff.partial
next.handoff.partial_detail
next.handoff.err.client_missing
next.handoff.err.candidates_endpoint
next.handoff.err.no_selection
next.handoff.err.no_profile
next.handoff.err.catalog_endpoint
next.handoff.err.preview_endpoint
next.handoff.err.preflight_endpoint
next.handoff.err.submit_endpoint
next.handoff.err.xyz_read
next.handoff.err.xyz_invalid
next.handoff.err.xyz_unbound
next.handoff.err.preflight_rejected
next.handoff.err.submit_unconfirmed
next.handoff.err.submit_aborted
next.handoff.err.focus_locked
next.handoff.err.submit_conflict
next.handoff.err.submit_stale
next.handoff.err.submit_forbidden
next.handoff.err.submit_invalid
next.handoff.source_pes_review
next.handoff.source_frame_candidate
next.handoff.note_layout
next.handoff.feasible_yes
next.handoff.feasible_no
```

---

## 9. 测试与实测证据

命令（任务指定的 node.exe）：

```bash
"C:\Users\彭扬超\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" \
  frontend/js/module_tests/run_all.js acp2_pes_handoff
```

结果（2026-10-31 实测）：**37 passed / 0 failed，exit 0**。

覆盖：

1. 注册面：唯一 `ACPNextModules` key、`ACPShell.registry.validateDescriptor` 通过、无 legacy 全局；
2. `empty` 初始态、零请求、预览/确认禁用、服务端 single_flat 限制与“不计算排名”提示可见；
3. 手动载入：`loading` → `ready`，精确 GET 顺序（job → method-catalog → pes/review → energy-graph），
   候选行（role/id/frame/energy/source）、概览、profile 选项（4 个服务端 profile）与默认 `opt_freq`；
4. 排名/排序：服务端 `rank` 显示、缺失显示“服务端未提供”，行序不排序；帧候选来源（scan）与能量连接；
5. PES pending：empty + “尚未确认选点”，零假候选、预览禁用；
6. 预览：精确的逐候选 XYZ GET、`POST /batch-optimize/config-preview` body、逐候选 `POST /preflight`
   body（== handoff body）、config_key/生效配置/plan_digest/feasible 展示；预览零 `POST /jobs`；
7. 预览守卫：零选择本地报错零 POST；改选择后 preview → `stale`、确认禁用、重新预览成功（preflight 计数正确）；
8. 确认门：打开确认框零 `POST /jobs`，确认框文案含全部候选 ID/profile，取消不提交；
9. 提交：每个候选一个 `POST /jobs`（single_flat），body 含 `selected_ids/source_job_id/charge/multiplicity`、
   `preflight_fingerprint`，`Idempotency-Key` 确定性；2 个回执、状态 `submitted`、已提交后不能再次提交；
10. 回执导航：注入 `commands.openTask` 时点击回执可打开任务（job/attempt/source 正确）；
11. 部分失败：第 2 个任务 500 → 停止、保留第 1 个回执、`error` 文案含“部分候选未提交”；
12. 传输失败：无状态 → “提交结果未确认…勿盲目重复提交”，零回执、绝无“已提交”；
13. 400 `preflight_stale` → preview 置 `stale`、确认禁用；
14. 缺失端点：method-catalog 404（配置态 error，预览显式报错零提交）、config-preview 404、preflight 404、
    候选端点 404（加载 error）；
15. 几何失败：候选结构 404、注释缺 `candidate_id`、preflight infeasible（含 server reason）均阻断提交；
16. 迟到结果：dispose 后任务响应不画/不触发次级读；dispose 后预览不进入 config/preflight/submit；
    预览中切换任务 → 旧预览丢弃；预览被失效 → busy 释放、状态 `stale`、零 POST；
17. 提交中焦点变化：忽略切换并显示 warning，回执完整保留；提交完成后可再切换；
18. context：自动载入/清空零请求/切换任务；dispose 幂等 + 退订 + 移除语言监听 + remount 成功；
19. 无 client seam：显式错误、零请求；
20. 纯函数：路径编码、9 个 route 常量、job/method-catalog/energy/PES/frame 归一化、能量连接、
    XYZ 校验、handoff body 只含契约键、submit body fingerprint、preflight/receipt 归一化、
    submit 错误映射、幂等键、selection key；
21. i18n：源码每个 `next.handoff.*` 字面量都在本地字典，`data-i18n` 全为模块自有键；
22. 源码 pin：无 fetch/XHR/sendBeacon/localStorage/innerHTML、无 `$3Dmol`/scene/geometry store、
    无 `/api/v2`、无 `.sort(`；POST 恰好 3 条（config-preview/preflight/jobs）；候选产物路径来自
    既有 `RESULT/structures/<candidateId>.xyz`；`openTask` 只经注入 `deps.commands`。

### 后端契约核对（静态源码 + 请求/响应形状，不是真实 HTTP）

- `V1JobCreateRequest` / `V1PreflightRequest`：`schema_version`、`workflow`、`input`、`method`、
  `resources`、`preflight_fingerprint` 均声明；`input` 词表由
  `submission_contract.WORKFLOW_CONTRACTS["BatchOptimize"]` 校验（`source_type/source/selected_ids/
  source_job_id/charge/multiplicity` 在允许集内；单 item 限制如上）；
- `POST /preflight` 的 `plan_digest` 由 `_plan_digest`（`normalize_preflight` + `canonical_digest`）计算，
  只覆盖 workflow/input/method/resources 的科学字段；submit 的 `preflight_fingerprint` 由同一 `_plan_digest`
  比对（`v1_routes.py` create_job），因此预览 body 与 submit body 的 science 字段必须一致——本模块用同一
  冻结对象生成两者；
- `POST /jobs` 的 201 响应字段来自 `V1JobCreatedResponse`（`job_id/status/attempt/plan_digest/
  idempotent_replay/idempotency_key`）；
- `GET /method-catalog` 返回 `method_schemas`（`acp/catalog.py::get_method_catalog`），其中
  `batch_optimize.profiles` 提供 `profile_id/label/label_zh`（本模块据此建 selector，不硬编码枚举）；
- 候选结构路径与两个写入方的既有约定一致：`src/acp/calculations/pes/review.py`
  （`RESULT/structures/<candidate_id>.xyz`）与 `src/acp/results/frame_candidates.py`；
- `--select` 的匹配对象是 `item_id` 或 `candidate_id`（`acp/workflows/batch_optimize.py::_selected_items`），
  scheduler 从 `input.select` / `input.selected_ids` 生成（`scheduler/jobs.py::batchoptimize_method_flags`）。

---

## 10. 接入下一步（本次文件边界外，未改动）

1. `frontend/next/index.html`：加 `<link rel="stylesheet" href="/css/modules/pes-handoff.css">` 与
   `<script src="/js/modules/pes-handoff/module.js"></script>`；`ACPShell.bootstrap.boot()` 的
   `collectFrom(window.ACPNextModules)` 自动收编。
2. `frontend/js/shell/recipes.js`：为 Research（或 Execution 的 PES 作业）工作区选择槽位；当前**未接默认 recipe**。
3. `frontend/js/shell/i18n.js`：补 §8 的 90 条 key（zh + en）。
4. `commands.openTask` 注入：模块仅在 `deps.commands.openTask` 存在时显示“打开任务”；Shell 的
   `layout-manager.hostApi()` 是否透传 `commands` 需集成时核对（缺失时回执仍完整展示，不报错）。
5. 浏览器用例：`acp2_next_entry.browser.js` 增加“PES ready → 勾选候选 → 预览 → 确认 → 回执”场景
   （本次未执行浏览器套件，不计 PASS）。
6. parity 记录：A-51 的「模块注册 + 四态 + 测试 + 键组覆盖」中键组依赖第 3 步；其余三项已完成（代码/契约层）。

---

## 11. 纯函数测试缝（`_internals`）

`normalizeJob`、`normalizeMethodCatalog`、`defaultProfileFor`、`normalizeEnergyGraph`、`energyForFrame`、
`normalizePesCandidates`、`normalizeFrameCandidates`、`selectedCandidates`、`selectionKey`、`validateXyz`、
`buildHandoffBodies`、`buildSubmitBody`、`normalizePreflight`、`normalizeReceipt`、`mapSubmitError`、
`idempotencyKeyFor`、`roleToken`、`jobPath`、`pesReviewPath`、`frameCandidatesPath`、`energyPath`、
`structureFilePath`；常量 `ROUTES` / `DEFAULT_PROFILE("opt_freq")` / `i18n`（只读 zh 字典）。

测试通过这些函数钉住请求体/校验/错误映射/幂等键；DOM 测试再钉住四态、确认门、逐项回执与迟到结果。

---

## 12. 边界 pin（源码级，测试守护）

- 只写 `window.ACPNextModules` 一个全局；无 `ACPWorkbench`/`ACPView` 写入；
- 无 `fetch(` / `window.fetch` / `XMLHttpRequest` / `sendBeacon` / `localStorage` / `sessionStorage`；
- 无 `innerHTML` / `insertAdjacentHTML` / `document.write`；无 `.sort(`（不客户端排序）；
- 无 `$3Dmol` / `scene_instance` / `createViewer` / `canvas` / `ACPGeometryStore` / `structure_editor` /
  `geometry_store`（A-45/A-46 面）；
- POST 恰好 3 类：`batch-optimize/config-preview`、`preflight`、`jobs`；无其他写路由；
- 候选几何只经既有 `RESULT/structures/<candidateId>.xyz` 文件端点；不拼 run_root/工作目录。

---

## 13. 已知缺口 / 未验证

- **未在真实浏览器 / 真实 HTTP 服务执行**：全部经注入 seam 验证；真实 `/preflight`、`/jobs` 的 403/项目隔离、
  幂等 replay、并发提交与真实 ORCA 执行均 **NOT_VERIFIED**。
- **远端候选结构**：`GET /jobs/{id}/files/...` 读本地工作目录；远端 PES 任务的确认结构可能需要
  prepare/remote-files 路径。本模块对读取失败显式报错、不提交；远端交接需在集成时确认（未写第二套取数）。
- **`from_artifact + selected_ids` 链路未采用**：原因见 §3（BatchOptimize 提交分支不解析 `source_job_id`
  + `from_artifact`）。这是后端缺口，不在前端伪造；改用既有 `xyz_text` 单结构路径。
- **无 profile 之外的 BatchOptimize 方法编辑**：本模块只提交 `{profile}`（profile 来自服务端目录），
  不提供 batch_roles 逐字段编辑（P01/A-07 面）；需要自定义方法时由 `task-wizard`/`task-config` 负责。
- **无自动提交 / 无自动排名 / 无自动授权**：显式确认是本模块的硬门；A-51 的“全自动首版”属于
  MCP/Agent 计划，不在本前端任务内，也没有用 `manual` 快捷路径绕过确认。
- **无预算/审批/资源编辑**：不发送 `resources`/`budget`；服务端默认与既有提交策略生效。
- **回执只覆盖本页提交的任务**：刷新后不持久化历史（没有第二任务库/缓存）；可用 `commands.openTask`
  或任务队列找回。
- **zh-only**：Shell 字典尚无 `next.handoff.*`；en 显示 zh（A-61）。
- **未改共享文件**：`frontend/next/index.html`、`frontend/js/shell/*`、`recipes.js`、既有模块/测试均未修改；
  没有 git commit。
