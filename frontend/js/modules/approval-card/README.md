# approval-card（A-57 / P22 审批卡）

ACP Next 研究审批卡模块：展示一条**待审批（pending）研究审批**的服务端投影，并提供
批准 / 拒绝 / 撤销三个决定动作。审批是**意图记录**，不是执行事实；本模块不写任务状态、
不提交任务，也不在本地计算预算或摘要。

- 模块 id：`approval-card`；注册到 `window.ACPNextModules["approval-card"]`
- Shell 布局簿（`shell/layout-preferences.js` 的 `EXTRA_MODULES`）已登记该 id；
  默认 recipe 是否放置由 V7 装配决定
- 经典浏览器脚本：无构建、无 ESM、除 `ACPNextModules` 外不新增全局
- 状态机对应服务端 `acp_services.runtime.approvals`：`pending → approved | rejected`，
  `approved → revoked`；`expired` 由服务端过期结算，本模块只显示

## 文件

| 文件 | 职责 |
|---|---|
| `module.js` | descriptor + factory；四态渲染、待审批列表、决定命令、generation/token 守卫、dispose |
| `README.md` | 本文件：契约、API 形状、i18n key、接入步骤、已知缺口 |
| `../../css/modules/approval-card.css` | 仅 `.acp-ac*`；颜色/圆角/控件高度全部取 `base.css` / `shell.css` 变量 |
| `../../module_tests/acp2_approval_card.test.js` | 零依赖 vm 测试（fake DOM / fake transport / fake context），`fetch` 未定义 |

## 模块契约

```js
ACPNextModules["approval-card"] = {
  descriptor: {
    id: "approval-card",
    interfaceVersion: 1,
    version: "1.0.0",
    provides: ["research.approval"],
    binds: ["researchRef"],
    capabilities: { requires: [], provides: ["research.approval"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.approval.errorTitle", messageKey: "next.approval.errorMessage" },
  },
  factory: function (deps) { /* ModuleInstance */ },
};
```

`deps`：

| 键 | 形状 | 说明 |
|---|---|---|
| `doc` | `Document` | 缺省 `document`；测试注入 fake DOM |
| `fetch` | `(path, opts) => Promise<any>` | **注入的传输函数**，签名与 `ACPClient.transport.request` 相同（不是浏览器全局 `fetch`） |
| `client` | `{ transport: { request(path, opts) } }` | `deps.fetch` 缺失时使用；再缺省 `window.ACPClient` |
| `context` | `{ getSnapshot(), subscribe(fn) }` | 缺省由 `mount(host, api)` 的 `api.context` 提供 |
| `approvalRef` | `string` | 显式聚焦一条审批（跳过待审批列表） |
| `projectId` | `string` | context 无 `projectId` 时的查询范围兜底 |
| `now` | `() => number` | 仅用于「已过期」显示判断的时钟接缝 |

`ModuleInstance`：`mount(hostEl, api): Promise<void>` · `updateContext(snapshot)` ·
`suspend()` · `resume()` · `dispose()`。

**选择来源**（优先级从高到低）：

1. `deps.approvalRef`；
2. context `snapshot.approvalRef`（如宿主扩展）；
3. context `snapshot.researchRef.approvalRef`；
4. context `snapshot.researchRef.proposalId` → 作为 `proposal_ref` 过滤待审批列表；
5. 都没有 → 查询全部待审批（带 `project_id` 时按项目过滤）。

## 使用的 API（服务端既有，全部只读或审批决策）

| 方法/路径 | 用途 | 响应形状（实际依赖字段） |
|---|---|---|
| `GET /api/v2/research/approvals?status=pending[&project_id][&proposal_ref]` | 待审批列表 | `{ approvals: Approval[], snapshot_revision? }` |
| `GET /api/v2/research/approvals/{approval_ref}` | 单条审批（刷新 revision） | `Approval` |
| `POST /api/v2/research/approvals/{approval_ref}/approve` | 批准 | `Approval`（更新后的服务端记录） |
| `POST /api/v2/research/approvals/{approval_ref}/reject` | 拒绝 | `Approval` |
| `POST /api/v2/research/approvals/{approval_ref}/revoke` | 撤销（仅 `approved`） | `Approval` |

`Approval`（`src/acp_services/runtime/approvals.py::Approval.to_dict` + `v2_approvals` 投影）中
本模块显示/使用的字段：`approval_ref`、`proposal_ref`、`proposal_digest`、`plan_digest`、
`science_digest`、`science_projection`、`binding_status`、`plan_definition`、`project_id`、
`requester_ref`、`approver_ref`、`budget`、`budget_digest`、`allowed_change_range`、
`status`/`state`、`revision`、`requested_at`/`created_at`、`decided_at`、`reason`、
`task_ref`（仅作为证据显示，不用于提交）。响应若被包一层 `{ approval: ... }` 也能兼容。

### 决定请求体（安全契约）

```json
{ "expected_revision": 3, "reason": "可选" }
```

- `reason` 为空/仅空白时**整个字段被省略**；从不发送 `reason: null`。
- **绝不发送审批人身份**：不存在 `approver_ref` / `approver_principal` / `principal_id` /
  `actor` / `user_id` / 角色 / 租户等字段；审批人由 Controller 认证通道解析。
- `expected_revision` 取当前显示记录的 `revision`（整数 ≥ 1），否则拒绝发请求。
- 409 / 401 / 403 / 404 → 显示冲突/无权/不可见文案，并**重新读取该记录（revision）与待审批
  列表**；不会重放决定请求。409 文案会带刷新后的 revision，要求人工复核后再决定。

## 四态与生命周期

| 状态（`data-acp-module-state`） | 触发 |
|---|---|
| `empty` | 待审批列表为空；文案「暂无待审批的研究审批。」 |
| `loading` | 初始读取 / 重试 / 手动刷新进行中 |
| `ready` | 已有服务端记录；渲染卡片 |
| `error` | 传输缺失、读取失败、聚焦记录 404 等；显示原因 + 重试 |

- `generation` 在 `mount`/`dispose` 递增；读取用 `(generation, readToken)`、决定用
  `(generation, actionToken)` 双重守卫：切换 `researchRef`/`proposalId`、重新挂载或 dispose
  后，迟到的响应不写 DOM、不改状态。
- `dispose`：置 disposed、作废在途 token、退订 context、移除所有监听（含 `acp:langchange`）、
  清空 host、清空 ui/record/pending；幂等。
- 决定进行中（`busy`）四个按钮全部 `disabled`，动作容器带 `data-busy="true"`，原因输入禁用；
  完成后按新状态的可用性恢复。
- 语言切换：重渲染当前状态（静态 chrome 均带 `data-i18n`；zh 字面量为默认值）。

## 显示规则（服务端值，不本地计算）

- **预算**：逐项渲染 `record.budget`（键排序），值为服务端原值；`science_projection.budget`
  单独作为「科学计划预算」渲染。两者都为空时显示「服务端未提供预算。」。
- **执行计划摘要**：逐项渲染 `record.plan_definition`（workflow/input/method/resources 等）；
  嵌套对象以截断 JSON 展示。
- **科学计划摘要**：`science_projection.recipe_digest` + `science.method / mapping / charge /
  multiplicity / capability_steps / initial_inputs / allowed_substitutions`；步骤/输入只显示
  数组长度（展示计数，不做预算/摘要运算）。无科学摘要时明确显示「仅绑定执行计划」。
- **摘要**：`plan_digest` 必显；`science_digest`、`proposal_digest`、`budget_digest` 存在即显示；
  全部为服务端字符串。模块内没有 hash/摘要实现（静态测试断言无 `crypto`、无 `sha256:`）。
- **状态/版本**：徽标 + 明细行；徽标 `data-status` 为服务端原始状态。
- **申请人/审批人/到期/申请/决定时间**：`requester_ref`、`approver_ref`、`expires_at` 等；
  「已过期」只是按浏览器时钟对服务端时间戳的**显示**后缀，不改变记录、不拦截按钮（服务端
  仍会过期结算/拒绝）。
- **待审批切换**：列表多于 1 条时显示可点击的 `approval_ref` 条带（单实例只显示一张卡）。

## 禁止项（与根 AGENTS / ANTI 对齐）

- 不调用 `/jobs*`、不调用提交/重算接口、不写任务状态（审批 ≠ 执行事实）。
- 不本地计算预算、plan/proposal/science digest、绑定状态或审批人身份。
- 不新增全局（仅 `window.ACPNextModules`）、无构建/ESM、无裸 `fetch`。

## i18n keys（`next.approval.*`）

本模块内联 zh 默认值；Shell 侧字典（`frontend/js/shell/i18n.js`）由 Shell 车道的所有者补
zh-CN/en-US 两份（本模块不改该文件）。key 清单：

```
next.approval.title
next.approval.empty  empty_hint  loading  error  retry  refresh
next.approval.approve  reject  revoke  reason_label  reason_placeholder  pending_count
next.approval.notice.approved  rejected  revoked  conflict  conflict_unknown
next.approval.notice.access  not_found  no_transport  action_failed  invalid_record
next.approval.status.draft  pending  approved  rejected  expired  revoked
next.approval.binding.full  execution-only  execution_only
next.approval.label.approval_ref  proposal_ref  project_id  status  revision
next.approval.label.requester  approver  expires  requested  decided  binding
next.approval.label.plan_digest  science_digest  proposal_digest  budget_digest  reason
next.approval.label.budget  plan  science  recipe_digest  change_range  task_ref
next.approval.label.science_budget
next.approval.budget_empty  plan_empty  science_empty  expired
next.approval.errorTitle  errorMessage
next.approval.budget.{model_requests,input_tokens,output_tokens,task_refs,recipe_steps,
  qc_invocations,wall_seconds,queue_seconds,unit_basis,notes,unit,count,timeout_s}
next.approval.plan.{workflow,input,method,resources,steps}
next.approval.science.{method,mapping,charge,multiplicity,capability_steps,initial_inputs,
  allowed_substitutions}
```

## 接入步骤（需要 Shell / 装配车道配合，本任务文件范围外）

1. **`frontend/next/index.html`**：在模块脚本区加
   `<script src="/js/modules/approval-card/module.js"></script>`（与其它 `js/modules/*` 同批），
   并在 `<head>` 加 `<link rel="stylesheet" href="/css/modules/approval-card.css">`。
2. **注册即可被布局簿发现**：`shell/layout-preferences.js` 已把 `approval-card` 列为
   `EXTRA_MODULES`，脚本加载后 `ACPShell.registry.has("approval-card")` 为真，布局编辑器即可选。
3. **默认放置（可选，V7 装配）**：在 `shell/recipes.js` 的 research / execution recipe 中加
   `{ instanceId: "approval-card", moduleId: "approval-card", slot: "...", titleKey:
   "shell.module.approval_card", loading: "card" }`；不加也能通过布局编辑器手动加入。
4. **Shell i18n（可选但推荐）**：把上面 key 组按 zh-CN/en-US 写入 `shell/i18n.js`；
   未写入时模块仍显示内联 zh 默认文案（英文为中文回退）。
5. **宿主动作接线**：审批条目来自平台审批记录；若要"从提案跳转到审批"，由上层把
   `researchRef.proposalId`（提案过滤）或显式 `deps.approvalRef` 传给模块。不要在本模块加
   提交按钮——提交属于 A-58/提交侧契约。

## 测试

```bash
node frontend/js/module_tests/run_all.js acp2_approval_card
```

覆盖：注册/descriptor、empty、ready（预算/计划/双摘要/状态/版本/申请人/到期）、loading、
error+重试、传输缺失、`deps.client.transport.request` 回退、approve 请求体（`expected_revision`
+ reason、无身份字段）、reject 省略空 reason、revoke 路径、busy 禁用、409 刷新 revision+列表且
不重放、403 保留服务端记录、选择切换 generation 守卫、dispose、源码静态门（无裸 fetch /
无 `/jobs`、`/submit` / 无 crypto·`sha256:`）。

## 已知缺口 / 不确定项

- 未改 `next/index.html` / `recipes.js` / `i18n.js`（文件所有权限制），接入需上面步骤；
  在此之前模块不会被页面加载，也无法在真实浏览器验收。
- `GET /research/approvals/{ref}` 路由不接收 `project_id` 查询参数，因此聚焦读取按
  `approval_ref` 全局读取，由服务端做项目/身份授权；跨身份不可见按 404 处理。
- 待审批列表使用服务端默认 `limit=50`，未做分页；多于一页时仅显示首批（按服务端顺序）。
- 列表模式默认选中第一条待审批；未实现按提案/项目分组或排序（服务端顺序即展示顺序）。
- `reason` 未做客户端长度/字符校验，交由服务端 422/400 呈现；失败文案复用服务端 message。
- 过期标记依赖浏览器时钟，仅显示用途；是否允许决定由服务端过期结算决定。
- 未做真实浏览器 / 真实 Controller 联调（本任务仅 vm 模块测试）；后端审批路由语义以
  `src/acp/api/v2_approvals.py` 为准。
