# mechanism-plan — ACP 3.0 机理计划装配（A-49 / P17）

- 文件：
  - `frontend/js/modules/mechanism-plan/module.js`（classic script，无构建、无 ESM）
  - `frontend/css/modules/mechanism-plan.css`（仅消费 `frontend/css/workbench/base.css` / `shell/shell.css` 的变量）
  - `frontend/js/module_tests/acp2_mechanism_plan.test.js`（Node vm + fake DOM + 注入 client，网络零直连）
- 注册名：`window.ACPNextModules["mechanism-plan"]`（唯一新增全局；不写页面全局、不建第二 viewer、不建第二缓存）
- 对应计划：[ACP 3.0 前端计划](../../../../docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md)
  §5.V6 / Track A **A-49**（`P17 机理构建（反应坐标计划装配）`），前置 **A-06**（提交/回执）与 A-05/A-06 的
  四态模块形态；验收要点：**与 Batch 契约一致**（计划体只使用既有的 reaction-coordinate plan 形状，
  不另造一套步骤执行协议）。
- 后端事实（2026-10-31 源码核对）：`POST /mechanism-studies/{id}/mechanism/plan` 当前 handler 固定返回
  **410**（`该机制研究端点已退役，历史只读`）。本模块仍调用这条**唯一既有**确认路由，并把 410 显示为
  显式错误；**不伪造 plan_confirmed 成功**，也不改走任务提交路由。详见 §6 / §10。

---

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["mechanism-plan"] = {
  descriptor: {
    id: "mechanism-plan",
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["mechanism.plan"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["mechanism.plan"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.mechplan.errorTitle", messageKey: "next.mechplan.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ },
  _internals: { /* 纯函数测试缝，见 §11 */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise<void>`、`updateContext(snapshot)`、`suspend()`、`resume()`、`dispose()`。
`mount` 只接受 DOM host；重复 mount 被拒绝（先 dispose）；dispose 后可重新 mount（状态复位）。

`deps`（全部可注入、可 mock；Shell 的 `hostApi` 自动提供 `context`）：

| 字段 | 内容 |
|---|---|
| `doc` | DOM document（缺省 `window.document`；测试注入 fake DOM） |
| `request` | 现有客户端单点：`(path, opts) → Promise<parsedBody>`（缺省回落 `deps.client.transport.request` → `window.ACPClient.transport.request`） |
| `context` | Shell 工作区上下文；只消费 `taskRef.jobId`（用于按任务列研究）与 `projectId`（仅记录，不发请求） |
| `client` | 可选；`client.transport.request` 作为 `request` 的二级回落 |
| `initialStudyId` | 可选；指定初始研究 ID（跳过任务列表自动载入） |

解析优先级：**注入 `deps.request` → `deps.client.transport.request` → `window.ACPClient.transport.request` → 无**。
全部缺失时载入动作给出明确错误且**不发任何请求**（测试 `client_missing` 路径钉住）；模块从不调用
`window.fetch` / `XMLHttpRequest` / `sendBeacon`。

---

## 2. 端点与请求体（全部为既有端点；没有为 A-49 新增后端）

| 用途 | 请求 | 说明 |
|---|---|---|
| 研究列表 | `GET /api/v1/mechanism-studies?limit=50` 或 `?job_id=<jobId>&limit=50` | `job_id` 来自 `context.taskRef`；无任务上下文时 UI 可“浏览全部研究”（`?limit=50`）。响应是 `MechanismStudySummary[]`（字段 `id`，不是 `study_id`） |
| 研究详情 | `GET /api/v1/mechanism-studies/{id}` | `MechanismStudyDetail`：`id/job_id/status/unified_status/created_at/updated_at/study_json/decisions` |
| 研究报告 | `GET /api/v1/mechanism-studies/{id}/report` | `reaction_network / mechanism_profile / stationary_points / quality_gates / provenance`；本模块消费 network、stationary points、provenance（profile/quality gates 保留在响应里，不在本面板展开） |
| 反应定义（只读） | `GET /api/v1/mechanism-studies/{id}/reaction` | `ReactionGetResponse`：`{reaction, status}`；`reaction` 可为 null。用于展示 reactants/products/mapping/config_hash/confirmed_by |
| 计划确认 | `POST /api/v1/mechanism-studies/{id}/mechanism/plan` | **本模块唯一的写操作**。body 固定 `{plan, strategy, fidelity}`；响应 `MechanismPlanResponse` `{status, plan_hash}` |

**刻意不调用**（已核对为退役/不适用）：

- `POST /api/v1/mechanism-studies`（create）→ 410；
- `POST …/{id}/reaction/preview`、`POST …/{id}/reaction/confirm` → 410；
- `POST …/{id}/decisions/{decision_id}`、`…/reviews/{review_id}/decision`、`…/resume`、`…/promote` → 410；
- **不存在** plan preview / plan fingerprint 端点；本模块用本地 plan 预览 + 本地指纹（§6）；
- 不调用 `/jobs`、`/preflight`、`/s2/*`、`/pes/*`、Batch 端点；计划确认**不提交任务**。

### 2.1 计划确认请求体（模块实际发送的内容）

`MechanismPlanRequest`（`src/acp/api/v1_schemas.py`）只声明 `plan: dict`、`strategy: str`、`fidelity: str`；
历史 handler 的 `_validate_plan_request` 对非 `direct-ts` 要求 `plan.coordinates` 非空，且每个坐标至少有
`id/kind/atoms/role`。本模块发送的是既有的 **reaction-coordinate plan 形状**（`ReactionCoordinatePlan.to_dict()`
消费的形状），没有新增字段：

```json
{
  "strategy": "guided-scan",
  "fidelity": "s3",
  "plan": {
    "coordinates": [
      {
        "id": "step-1/rc1",
        "kind": "distance",
        "atoms": [0, 1],
        "role": "drive",
        "start": 1.2,
        "end": 2.4
      },
      {
        "id": "step-1/rc2",
        "kind": "angle",
        "atoms": [0, 1, 2],
        "role": "monitor",
        "start": 110.0,
        "end": null
      }
    ],
    "points": 21,
    "coupling": "synchronous",
    "start_from": "reactant"
  }
}
```

- `strategy ∈ {guided-scan, rph-reverse, direct-ts}`；`fidelity ∈ {s3, s4}`（与 `MechanismPlanRequest` /
  `MechanismRouteIn` 的枚举一致）。
- `plan.coordinates[].atoms` 是 **0 基**（`CoordinateSpec` 文档明确 0-based；ORCA `%geom` 也是 0-based）。
  界面按 **1 基**显示（`formatAtoms`），wire 原样保留 0 基。模块内没有第二套基址转换。
- 坐标 `id` 以 `步骤/路线 id + "/" + 源坐标 id` 命名（如 `step-1/rc1`），把计划**来源**保留在既有
  `id` 字段里；**没有**往 `plan` 增加 `steps` 等非契约字段。
- `start` / `end` 对 `monitor` 可为 null；`drive` 必须两者都是有限数值，`freeze` 必须有 `start`
  （来自 `CoordinateSpec.__post_init__` 的既有约束）。
- `points` 默认取所选步骤/路线 `coordinate_plan.points` 的最大值（≥2），允许用户修改；`coupling` 固定
  `synchronous`（`ReactionCoordinatePlan` 只支持它）；`start_from` 默认 `rph-reverse → product`，其余
  `reactant`，可显式改成 `custom`。
- 源码 pin 测试断言 **没有** `/jobs` / `/preflight` / `/s2/` / `/pes/` / `/batch` 路径，且唯一 POST 是
  `…/mechanism/plan`。

### 2.2 计划确认响应

`{status: string, plan_hash: string}`。成功时模块显示：

- 计划状态 `status`（通常 `plan_confirmed`）；
- **服务端 `plan_hash`**——这是权威确认指纹；
- 本地预览指纹（`fnv1a:`）另行标注为**非权威**，仅用于装配预览。

当前后端返回 410，因此真实服务上无法取得 `plan_hash`；该路径按错误态显示（§6）。

---

## 3. 四态、忙碌态与守卫

模块根 `data-acp-module-state ∈ empty | loading | ready | error`（研究数据状态）：

| 状态 | 触发条件 |
|---|---|
| `empty` | 尚未载入研究（初始 / 任务焦点清空 / 列表为空） |
| `loading` | 列表或详情 + report + reaction 读取中（overlay 可见） |
| `ready` | 详情已载入（report/reaction 失败仍为 ready + 局部提示） |
| `error` | 列表/详情读取失败（overlay + 原因 + 重试；重试按失败面分派） |

命令忙碌态独立：`data-acp-busy ∈ none | load | confirm`；计划命令态 `data-acp-plan-state ∈ idle | blocked | confirming | confirmed | error`；
研究计划状态 `data-acp-plan-status ∈ pending | reaction_locked | confirmed`；另有 `data-acp-study=<id>`。
忙碌期间输入/步骤选择/角色编辑禁用；重复确认点击被忽略（测试钉住只发一次 POST）。

守卫：

- `state.generation`：mount/dispose 递增，任何迟到结果在写 DOM 前被丢弃；
- `state.list.token` / `state.study.token` / `state.plan.token`：列表、详情、确认各自 token；旧请求结果
  永远不能覆盖新研究或新确认；
- 任务焦点变化会清空旧研究、取消进行中的确认显示并重新列列表；
- `dispose()` 幂等：移除自有 DOM、context 订阅、`acp:langchange` 监听，拒绝迟到详情/确认结果；
- dispose 后可重新 mount（默认值复位）。

---

## 4. 研究视图模型（显示什么、从哪来）

### 4.1 概览

| 行 | 来源 |
|---|---|
| 研究 ID / 任务 / 研究状态 / 统一状态 / 更新时间 / 决策点数 | `GET …/{id}` 的 `id/job_id/status/unified_status/updated_at/decisions` |
| 计划状态 | `detail.status/unified_status`，或本次确认回执（见 §4.5） |
| 角色计数 chips | 见 §4.2 |

### 4.2 角色（reactants / products / intermediates / TS）

按**显示角色**分桶：`reactant / product / intermediate / ts / unknown`。来源优先级：

1. `report.reaction_network.nodes[].role`（并带 `canonical_energy_hartree`）；
2. `study_json.stable_states[].role`（`stable_states` 的 `role` 是角色权威：`reactant/product/intermediate`）；
3. `report.stationary_points.stationary_points[]`（`kind === "ts"` 归入 TS，其余按 `role`）；
4. `study_json.stationary_points[]`（同上）。

`classifyRole` 只做词形归一（`transition_state`/`ts` → TS，`react*`/`start*` → reactant，`prod*`/`end*` → product，
`inter*` → intermediate），**不推断化学**；无法识别的角色进入 `unknown`，不猜。每个条目显示 `id · role`
（有能量时加 `x.xxxx Ha`，有电荷/多重度时加 `q=` / `2S+1=`）。

### 4.3 步骤

`collectSteps(detail, report)`：

- 首选 `report.reaction_network.edges[]`（发布投影的紧凑字段：`step_id/source/sink/ts_id/barrier_forward/
  barrier_reverse/fidelity/status/path_strategy`）；
- 用 `study_json.elementary_steps[]`（或 `study_json.network.edges[]`）按 `step_id` 补齐报告投影丢掉的
  `coordinate_plan` 以及缺失的 source/sink/path_strategy；
- 报告不可用时整表回退到 `study_json.elementary_steps[]` / `network.edges[]`。

表格列：步骤、起点状态、终点状态、TS、正向能垒、逆向能垒、保真度、路径策略、状态。能垒按报告原值
显示（不换算、不补单位）；缺失值显示 `—`。

### 4.4 反应定义（只读）

`GET …/{id}/reaction` 返回的 `reaction.reactants[] / products[]` 显示 `smiles · 电荷 q · 多重度 2S+1`；
另显示 `mapping` 对数、`config_hash`（反应锁定指纹）、`confirmed_by`、`reaction.status`。`reaction` 为
null 时显示明确的空状态，不伪造物种。

### 4.5 计划状态与溯源

- 计划状态：本次确认回执优先；否则 `detail.status === "plan_confirmed"` 或 `unified_status ===
  "PLAN_CONFIRMED"` → 已确认；`status === "reaction_confirmed"` 或 `unified_status === "RESUMABLE"` →
  反应已锁定、计划待确认；其余 → 未确认（含空状态）。
- 溯源：`report.provenance` 的 `study_input_signature`、`count`、`records[]`（最多展示 20 条，超出明确标注
  `shown/total`）与 `notes[]`；原样 JSON 展示，不汇总成“已验证”结论（模型叙述不作为证据）。

### 4.6 局部降级（不把部分失败伪装成 ready 成功）

| 情况 | 结果 |
|---|---|
| `report` 读取失败 | 模块仍 `ready`（详情在），显示“研究报告不可用：…”提示；步骤回退 study_json；溯源为空态 |
| `reaction` 读取失败 | 模块仍 `ready`，显示“反应定义读取失败：…”提示；反应区空态 |
| `report`/`reaction` 返回 null/空对象 | 对应区空态，不补造 |
| `study_json` 为空 | `ready` + 空步骤/角色/计划候选，计划校验显示 `coordinates_missing`，确认禁用 |

模块**从不读取/渲染 `study_json.study_dir`**，也不拼接任何文件系统路径（测试 pin）。

---

## 5. 计划装配（A-49 的“步骤 / 角色”）

1. **候选来源**（只使用研究自己持久化的坐标计划，按层级取**一层**，避免重复）：
   - `study_json.elementary_steps[]` 的 `coordinate_plan`；
   - 没有时用 `study_json.network.edges[]` 的 `coordinate_plan`；
   - 再没有时用 `study_json.routes[]` 的 `coordinate_plan`。
   节点/状态本身**不会**被用来编造坐标。
2. **默认选择全部候选**；用户可逐个勾选/取消。取消后其坐标从计划中移除。
3. **坐标角色**：`drive / freeze / monitor`；默认沿用源坐标 role，UI 可改。改角色/起点/终点会**失效**上一次
   确认回执（同 plan 不能二次确认）。
4. **坐标值**：`start/end` 默认沿用源计划；可编辑（空字符串 = 未提供）。改 `monitor → drive` 但未补
   `start/end` 时，校验报 `drive_start_end`，确认按钮禁用——**不自动填数**。
5. **点数**：默认取所选候选 `points` 的最大值（≥2），可改；`coupling` 固定 `synchronous`。
6. **direct-ts**：允许不选任何步骤（`plan.coordinates: []`），因为历史校验对 `direct-ts` 提前返回；
   UI 仍保留已选坐标（若用户选了）。非 `direct-ts` 至少需要一个坐标。
7. **预览**：实时渲染 `{plan, strategy, fidelity}` JSON + 本地指纹 + 校验错误；**没有 plan preview 端点**，
   预览完全在客户端完成。

### 5.1 本地装配校验矩阵（提交前拦截；服务端仍为权威）

| 代码 | 规则 |
|---|---|
| `plan_invalid` | `plan` 必须是对象 |
| `strategy_invalid` | `strategy ∈ {guided-scan, rph-reverse, direct-ts}` |
| `fidelity_invalid` | `fidelity ∈ {s3, s4}` |
| `points_invalid` | `points` 必须是 ≥2 的整数 |
| `coupling_invalid` | `coupling === "synchronous"` |
| `start_from_invalid` | `start_from ∈ {reactant, product, custom}` |
| `coordinates_invalid` | `plan.coordinates` 必须是数组 |
| `coordinates_missing` | 非 `direct-ts` 至少一个坐标 |
| `coordinate_invalid` | 坐标项必须是对象 |
| `coordinate_id_missing` | 坐标必须有非空 `id` |
| `coordinate_kind_invalid` | `kind ∈ {distance, angle, dihedral}` |
| `coordinate_atoms_invalid` | 原子数 = 2/3/4 且都是 ≥0 整数（0 基） |
| `coordinate_role_invalid` | `role ∈ {drive, freeze, monitor}` |
| `drive_start_end` | `drive` 坐标的 `start/end` 都是有限数值 |
| `freeze_start` | `freeze` 坐标的 `start` 是有限数值 |
| `drive_missing` | 至少一个 `drive` 坐标（非 `direct-ts`） |

校验失败时**不会发送确认请求**；点击确认把计划命令态置为 `blocked` 并显示全部错误。

---

## 6. 确认与指纹（单一既有写操作）

- 按钮仅在：研究已载入（ready）、busy=none、计划校验通过、同一计划尚未确认 时可用。
- POST body = 预览里的同一个 `{plan, strategy, fidelity}`（点击时重新构建，避免旧预览）；header
  `Content-Type: application/json`；**没有** Idempotency-Key（`MechanismPlanRequest` 不接收该字段）。
- 成功（2xx 且含 string `status`）：`data-acp-plan-state="confirmed"`，回执显示 `status` + **服务端
  `plan_hash`**；摘要的计划状态变为“已确认”；同一 plan 的确认按钮禁用，编辑任一计划字段后恢复。
- 失败映射：
  - **410** → `next.mechplan.confirm_retired`（“该端点已退役（HTTP 410）：<server detail>”）；
  - **409** → `next.mechplan.confirm_not_locked`（“研究反应未锁定（HTTP 409）：<detail>”）；
  - 其他 HTTP/transport → 服务端 detail/message；
  - 2xx 但缺 `status` → `next.mechplan.invalid_response`；
  - 所有失败都 **busy 清除、计划命令态 error、回执 error 区显示原因**，不重试、不重放、不显示成功。
- **指纹分层**：
  - **本地指纹** `fnv1a:xxxxxxxx`：对紧凑 JSON 做 FNV-1a 32 位，仅作装配预览/失效辅助，UI 明确标注
    “非权威”；
  - **服务端 `plan_hash`**：确认成功后才是权威指纹；当前 410 后端不会返回它。
- 反应定义有独立的 `config_hash`（§4.4），只展示，不当作 plan 指纹。
- 没有任何 plan preview 端点可调用；没有“先 preview 再 confirm”的双阶段接口，本模块不发明。

---

## 7. i18n（zh 内联 + data-i18n，共 139 条）

模块自带 `ZH` 兜底字典（Shell 字典缺 key 时 `t()` 回退本地 zh；`ACPShell.i18n.t` 只回显 key）。
静态文案带 `data-i18n`；动态文案（插值/错误）保留 zh 并随 `acp:langchange` 重绘。

需要在 `frontend/js/shell/i18n.js` 补的 key（zh + en，共 **139** 条；zh 值与模块内联文本逐字一致）：

```
next.mechplan.title
next.mechplan.subtitle
next.mechplan.study_title
next.mechplan.study_hint
next.mechplan.study_id
next.mechplan.study_id_ph
next.mechplan.load_study
next.mechplan.list_studies
next.mechplan.study_select
next.mechplan.study_select_ph
next.mechplan.list_loading
next.mechplan.list_empty
next.mechplan.list_ready
next.mechplan.list_scope_job
next.mechplan.list_error
next.mechplan.summary_title
next.mechplan.summary_id
next.mechplan.summary_job
next.mechplan.summary_status
next.mechplan.summary_unified
next.mechplan.summary_plan
next.mechplan.summary_updated
next.mechplan.summary_decisions
next.mechplan.steps_title
next.mechplan.steps_hint
next.mechplan.steps_empty
next.mechplan.step_col
next.mechplan.step_source
next.mechplan.step_sink
next.mechplan.step_ts
next.mechplan.step_barrier_f
next.mechplan.step_barrier_r
next.mechplan.step_fidelity
next.mechplan.step_strategy
next.mechplan.step_status
next.mechplan.roles_title
next.mechplan.role_reactant
next.mechplan.role_product
next.mechplan.role_intermediate
next.mechplan.role_ts
next.mechplan.role_unknown
next.mechplan.role_empty
next.mechplan.role_item
next.mechplan.role_energy
next.mechplan.reaction_title
next.mechplan.reaction_hint
next.mechplan.reaction_unavailable
next.mechplan.reaction_reactants
next.mechplan.reaction_products
next.mechplan.reaction_item
next.mechplan.reaction_mapping
next.mechplan.reaction_hash
next.mechplan.reaction_confirmed_by
next.mechplan.reaction_status
next.mechplan.provenance_title
next.mechplan.provenance_hint
next.mechplan.provenance_empty
next.mechplan.provenance_summary
next.mechplan.provenance_more
next.mechplan.plan_title
next.mechplan.plan_hint
next.mechplan.plan_strategy
next.mechplan.plan_fidelity
next.mechplan.plan_points
next.mechplan.plan_start_from
next.mechplan.start_from_reactant
next.mechplan.start_from_product
next.mechplan.start_from_custom
next.mechplan.strategy_guided
next.mechplan.strategy_rph
next.mechplan.strategy_direct
next.mechplan.candidates_title
next.mechplan.candidates_empty
next.mechplan.candidates_empty_direct
next.mechplan.candidate_step
next.mechplan.candidate_route
next.mechplan.coords_title
next.mechplan.coords_col_id
next.mechplan.coords_col_kind
next.mechplan.coords_col_atoms
next.mechplan.coords_col_start
next.mechplan.coords_col_end
next.mechplan.coords_col_role
next.mechplan.coords_empty
next.mechplan.kind_distance
next.mechplan.kind_angle
next.mechplan.kind_dihedral
next.mechplan.role_drive
next.mechplan.role_freeze
next.mechplan.role_monitor
next.mechplan.preview_title
next.mechplan.preview_fingerprint
next.mechplan.preview_note
next.mechplan.confirm
next.mechplan.confirming
next.mechplan.confirm_disabled
next.mechplan.confirm_idle
next.mechplan.confirm_blocked
next.mechplan.confirm_ok
next.mechplan.confirm_error
next.mechplan.confirm_retired
next.mechplan.confirm_not_locked
next.mechplan.invalid_response
next.mechplan.receipt_title
next.mechplan.receipt_status
next.mechplan.receipt_hash
next.mechplan.receipt_error
next.mechplan.plan_status_confirmed
next.mechplan.plan_status_reaction_locked
next.mechplan.plan_status_pending
next.mechplan.plan_status_unknown
next.mechplan.empty_title
next.mechplan.empty_hint
next.mechplan.loading_title
next.mechplan.error_title
next.mechplan.errorTitle
next.mechplan.errorMessage
next.mechplan.retry
next.mechplan.notices
next.mechplan.report_unavailable
next.mechplan.reaction_unavailable_detail
next.mechplan.err.client_missing
next.mechplan.err.study_id_required
next.mechplan.err.plan_invalid
next.mechplan.err.strategy_invalid
next.mechplan.err.fidelity_invalid
next.mechplan.err.points_invalid
next.mechplan.err.coupling_invalid
next.mechplan.err.start_from_invalid
next.mechplan.err.coordinates_invalid
next.mechplan.err.coordinates_missing
next.mechplan.err.coordinate_invalid
next.mechplan.err.coordinate_id_missing
next.mechplan.err.coordinate_kind_invalid
next.mechplan.err.coordinate_atoms_invalid
next.mechplan.err.coordinate_role_invalid
next.mechplan.err.drive_start_end
next.mechplan.err.freeze_start
next.mechplan.err.drive_missing
```

---

## 8. 测试与实测证据

命令（任务指定的 node.exe）：

```bash
"C:\Users\彭扬超\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" \
  frontend/js/module_tests/run_all.js acp2_mechanism_plan
```

结果（2026-10-31 实测）：**29 passed / 0 failed，exit 0**。

覆盖：

1. 注册面：唯一 `ACPNextModules` key、`ACPShell.registry.validateDescriptor` 通过、无 legacy 全局；
2. `empty` 初始态、零请求、确认禁用、zh 标题；
3. 手动载入：`loading`/`busy=load` overlay → `ready`；精确 GET detail/report/reaction 路径与顺序；
4. 任务上下文：`?job_id=…&limit=50` 列表、select 选项、自动载入第一个研究、切换 select 重新读详情；
5. 列表空态、列表错误 + 重试（重发同一查询）、详情错误 + 重试；
6. ready 视图：步骤行（source/sink/ts/barriers/fidelity/strategy/status）、五类角色计数与列表、
   reactants/products/mapping/config_hash/confirmed_by/reaction status、provenance（记录/签名/JSON/备注）、
   计划状态“反应已锁定”、`study_dir` 不渲染；
7. report 失败 → ready + 提示 + study_json 步骤回退；reaction 失败 → ready + 提示 + 反应空态；
8. 计划默认值：候选来自 elementary_steps、坐标行、0 基 wire / 1 基显示、preview JSON、本地指纹、确认可用；
9. 角色编辑：`monitor → drive` 缺值 → `drive_start_end`、确认禁用、不发 POST；补 start/end 后恢复并进入 body；
10. 步骤勾选：取消 → 坐标移除、`coordinates_missing`、确认 blocked、零 POST；重新勾选恢复；
11. `direct-ts`：无坐标可确认，POST body `coordinates: []`；
12. 确认：精确 body/headers、busy 双击只发一次、`plan_hash` 回执、同一 plan 二次确认禁用；
13. 410：显示退役 detail、计划命令态 error、无假成功、摘要计划状态不被翻转；
14. 409/422/500、2xx 缺 `status`：错误映射、busy 清除、错误回执；
15. generation/dispose：迟到详情/确认不写 DOM、dispose 幂等、移除 language 监听、可 remount；
16. 任务焦点变化：清旧研究、按新 job 列表、重置计划；清空焦点不再发请求；
17. 研究 ID 编码（空格/斜杠）进入既有路由；
18. 空研究：ready + 空步骤/角色/反应/溯源 + 坐标缺失 + 确认禁用；
19. `plan_confirmed` 研究：摘要显示已确认；
20. 无 client seam：明确错误、零请求；
21. 纯函数：列表/详情归一化、角色分类、步骤回退、候选（step/route）、计划体、校验矩阵、`planStatusInfo`、
    `formatAtoms`、`studyPath`/`listQuery` 编码、指纹确定性；
22. i18n：源码中每个 `next.mechplan.*` 字面量都存在本地字典，挂载后 `data-i18n` 全部可解析，langchange 不破坏；
23. 路由白名单：只出现 5 类既有路由；方法仅 GET + 一次 POST（`…/mechanism/plan`）；无 v2/jobs/preflight/s2/pes/batch；
24. 源码 pin：无 fetch/XHR/sendBeacon、无 `study_dir`/`mechanism_study/`/`path.join`、无 3D/几何编辑、
    无任务命令、只写 `ACPNextModules` 一个全局、无 `innerHTML`/`require`/`import()`。

全量回归（同一 node，2026-10-31，共享工作区有并行模块在落盘）：两次运行分别得到
**968 passed / 2 failed**（失败均在未归属本任务的 `acp2_job_edit`）与 **981 passed / 19 failed**
（失败均在未归属本任务的 `acp2_research_record`）；两次运行中 `acp2_mechanism_plan` 都全绿。
本模块未引用/修改这些模块，全量数字不作为本任务的通过证据。

后端契约核对（静态源码 + 请求体形状，非真实 HTTP）：

- `MechanismPlanRequest {plan: dict, strategy: str, fidelity: str}`（`src/acp/api/v1_schemas.py`）；
- 历史 `_validate_plan_request`：非 `direct-ts` 要求 `plan.coordinates` 非空；每个坐标至少
  `id/kind/atoms/role`；`direct-ts` 提前返回；
- `CoordinateSpec`：0-based atoms；distance/angle/dihedral = 2/3/4 atoms；drive 需要 start+end，
  freeze 需要 start；`role ∈ {drive, freeze, monitor}`；
- `_compute_unified_status` 的行状态映射（draft → DRAFT、reaction_confirmed/plan_confirmed → RESUMABLE）；
- 当前 handler 固定 410（本模块错误路径按此显示）。

这证明**请求形状/枚举/0 基约定**与后端契约一致；不等于真实 HTTP 确认成功。

---

## 9. 接入下一步（本次文件边界外，未改动）

1. `frontend/next/index.html`：`<head>` 加
   `<link rel="stylesheet" href="/css/modules/mechanism-plan.css">`；脚本区（模块脚本区或
   `ACPShell.bootstrap.js` 之前）加 `<script src="/js/modules/mechanism-plan/module.js"></script>`。
   `ACPShell.bootstrap.boot()` 的 `collectFrom(window.ACPNextModules)` 会自动收编注册。
2. `frontend/js/shell/recipes.js`：为 Research/Execution 工作区选择槽位/默认可见性。当前**未接默认 recipe**，
   默认布局不会自动显示；可由布局编辑器手动选用（`shell.module.*` 标题键需在 i18n 批补）。
3. `frontend/js/shell/i18n.js`：补 §7 的 139 条 key（zh + en；en 缺失前静态元素在 en 下显示 zh）。
4. 浏览器用例：`frontend/js/module_tests/acp2_next_entry.browser.js` 增加 mechanism-plan 场景
   （无上下文空态 → 手动载入 → ready → 切换直接 TS → 确认 410 错误显示；本次未执行浏览器套件，不计 PASS）。
5. parity 记录：A-49 的「模块注册 + 四态 + 测试 + 键组」四项中，键组覆盖依赖第 3 步；其余三项已完成
   （代码/契约层）。

---

## 10. 已知缺口 / 未验证

- **后端计划确认端点已退役（410）**：真实服务上无法产生 `plan_confirmed` 回执/`plan_hash`。模块按
  “显式错误”交付，测试覆盖 410 路径；**不能把本模块的 410 错误测试记作“确认功能已验收”**。需要后端
  恢复/替代该端点后才能做真实确认闭环；本次不改后端、不发明路由。
- **没有 plan preview / plan fingerprint 端点**：本地 `fnv1a` 只是装配预览，不是服务端摘要；不要把它当
  幂等键或审计指纹。
- **没有读取已存 plan_json 的读路由**：详情只暴露研究行状态；本模块只能显示“已确认/未确认”和本次回执，
  无法回读历史 plan 坐标（若后端以后加读路由，应作为显式契约扩展）。
- **计划只能复用研究自带坐标**：不提供“从 3D 测量新建坐标”（A-45/A-46 属于 molecule-viewer，且 A-50 的
  路径结构视图/编辑明确不在本次范围）；没有坐标的研究只能走 `direct-ts` 或等待数据。
- **高级 `ReactionCoordinatePlan` 字段未转发**：计划体只发送 `coordinates/points/coupling/start_from`；
  源计划里的 `values[]`、`lambda_values[]`、`reference_geometries[]`、`fixed_endpoints` 不会进入确认体
  （历史 `_validate_plan_request` 只校验 coordinates，且这些字段与可编辑的 points 存在一致性约束）。
  需要显式坐标值/固定端点的场景应作为后续契约扩展，不在本模块里猜测。
- **未在真实浏览器执行**：只跑 Node vm 套件；`next/index.html` 未加载本模块（文件边界限制），真实页面
  挂载/点击/布局 interaction 为 **NOT_VERIFIED**。
- **未对真实 HTTP 服务执行**：列表/详情/report/reaction/确认都只经注入 client 验证；真实 410 响应体、
  权限/项目隔离未在本机运行。
- **zh-only**：Shell 字典尚无 `next.mechplan.*`；en 显示 zh（A-61）。
- **A-50 / A-51 未实现**（按要求）：无路径结构视图/编辑、无 PES 选点发布、无 Batch 衔接、无任务提交；
  本模块唯一 POST 是既有 plan 确认。
- **不处理 reaction preview/confirm**：两者已 410，模块不调用；反应定义只读展示。
- **无资源/方法/预算/审批块**：与 A-49 无关，未添加。
- **无草稿持久化**：刷新后弹窗状态丢失（P02/A-02 不在本任务）。
- **未改共享文件**：`frontend/next/index.html`、`frontend/js/shell/*`、`recipes.js`、既有模块/测试均未修改；
  没有 git commit。

---

## 11. 纯函数测试缝（`_internals`）

`normalizeStudyList(payload)`、`normalizeDetail(payload)`、`normalizeReport(payload)`、
`normalizeReaction(payload)`、`classifyRole(role)`、`collectRoleBuckets(detail, report)`、
`collectSteps(detail, report)`、`buildCandidates(detail)`、`buildPlanBody(candidates, planState)`、
`buildPlanCoordinates(candidates, planState)`、`validatePlanBody(body)`、`planKeyOf(body)`、
`localFingerprint(text)`、`planStatusInfo(detail, receipt)`、`formatAtoms(atoms)`、`studyPath(id)`、
`listQuery(jobId)`；常量 `STRATEGIES` / `FIDELITIES` / `COORDINATE_ROLES` / `COORDINATE_KINDS` /
`MIN_POINTS(2)` / `DEFAULT_POINTS(21)` / `LIST_LIMIT(50)` / `STUDIES_PATH` / `i18n`（只读 zh 字典）。

测试通过这些函数钉住归一化、角色分类、候选/计划体、校验矩阵与路径编码；DOM 测试再钉住状态、事件、
请求次数与错误呈现，两者互补。

---

## 12. 边界 pin（源码级，测试守护）

- 只写 `window.ACPNextModules` 一个全局；无 `ACPWorkbench`/`ACPView`/`ACPClient` 写入；
- 无 `fetch(` / `window.fetch` / `XMLHttpRequest` / `sendBeacon` / `navigator.`；
- 无 `study_dir` / `mechanism_study/` / `path.join` / 任何文件系统路径构造；
- 无 `$3Dmol` / `ACPVisualization` / `scene_instance` / `createViewer` / `canvas` /
  `ACPGeometryStore` / `structure_editor` / `geometry_store`（A-50 面）；
- 无 `/jobs` / `/preflight` / `/s2/` / `/pes/` / `/batch` / `jobCommands`（任务提交面）；
- 无 `innerHTML` / `document.write` / `require(` / `import(`；
- 请求面只允许 §2 的 5 类路径，方法只允许 GET + 一次 `POST …/mechanism/plan`。
